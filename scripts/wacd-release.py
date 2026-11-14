#!/usr/bin/env python3
"""Release a published deploy chart through Lab's gateway and prove it is served.

wac-cd publishes the deploy chart, then asks the release gateway to release
that chart version and polls its status until Argo CD has synced the chart's
manifest digest, every Deployment has rolled out, and the Deployments run this
release's images. The release CLI first asks for status once and refuses to
release over a newer running X.Y.Z unless the mode is rollback. The transport is scripts/ci-release.py's, which mints a fresh
GitHub OIDC token for every request. That file belongs to other components and
is loaded, never changed.

The gateway contract (Plan 2, "Chart releases (wac-cd)") is isolated in
RELEASE_BODY, STATUS_BODY and ready(). After the proof, github-release creates
the GitHub Release for the tag with the job's GITHUB_TOKEN when none exists
yet, and never edits one that does. Any problem exits 1 with one stable
"wacd release: ..." stderr line, and no token is ever printed.
"""

import argparse
import http.client
import importlib.util
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "wacd_release_ci_release", Path(__file__).with_name("ci-release.py")
)
ci = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci)

ReleaseError = ci.ReleaseError

PULL_REGISTRY = "localhost:30500"
VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
POLL_SECONDS = 5
MAX_BODY = 1024 * 1024
MAX_WAIT = 3600
GITHUB_TIMEOUT = 30
TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
RELEASED_MODES = ("release", "resume", "rollback")


def RELEASE_BODY(version):
    return {"operation": "release", "version": version}


def STATUS_BODY():
    return {"operation": "status"}


def trust_context():
    try:
        return ssl.create_default_context(cafile=ci.CA_FILE)
    except (OSError, ssl.SSLError):
        raise ReleaseError(
            "runner TLS trust is missing or invalid; no insecure fallback"
        ) from None


def transport(body, trust, timeout):
    return ci.request(body, trust, timeout)


def released_digest(value, version):
    record = value.get("released")
    if not isinstance(record, dict) or record.get("version") != version:
        raise ReleaseError("release response is for another version")
    digest = record.get("chart_digest")
    if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseError("release response has no valid chart digest")
    return digest


def image_repository(ref):
    name = ref.split("@", 1)[0]
    head, _, last = name.rpartition("/")
    if ":" in last:
        last = last.split(":", 1)[0]
    return f"{head}/{last}" if head else last


def expected_refs(version, images):
    return {
        spec["repository"]: f"{spec['repository']}:{version}@{spec['digest']}"
        for spec in images.values()
    }


def rolled_out(deployment):
    if not isinstance(deployment, dict):
        raise ReleaseError("deployment evidence is malformed")
    status = deployment.get("status")
    replicas = deployment.get("replicas")
    generation = deployment.get("generation")
    if (
        not isinstance(status, dict)
        or type(replicas) is not int
        or type(generation) is not int
    ):
        return False
    observed = status.get("observedGeneration", 0)
    return (
        type(observed) is int
        and observed >= generation
        and all(
            status.get(key, 0) == replicas
            for key in ("updatedReplicas", "readyReplicas", "availableReplicas")
        )
        and status.get("unavailableReplicas", 0) == 0
    )


def ready(value, version, chart_digest, images):
    sync = value.get("sync")
    health = value.get("health")
    if (
        value.get("version") != version
        or not isinstance(sync, dict)
        or sync.get("status") != "Synced"
        or sync.get("revision") != chart_digest
        or not isinstance(health, dict)
        or health.get("status") != "Healthy"
        or value.get("automated") is not True
    ):
        return False
    deployments = value.get("deployments")
    if not isinstance(deployments, list) or not deployments:
        return False
    wanted = expected_refs(version, images)
    running = False
    for deployment in deployments:
        if not rolled_out(deployment):
            return False
        refs = deployment.get("images", [])
        if not isinstance(refs, list):
            raise ReleaseError("deployment image evidence is malformed")
        for ref in refs:
            if not isinstance(ref, str):
                raise ReleaseError("deployment image evidence is malformed")
            expected = wanted.get(image_repository(ref))
            if expected is None:
                continue
            if ref != expected:
                return False
            running = True
    return running


def release(version, images, wait_timeout, request=None):
    return proved(version, images, wait_timeout, request)[0]


def semver(text):
    if not isinstance(text, str):
        return None
    match = VERSION_PATTERN.fullmatch(text)
    return tuple(int(part) for part in match.groups()) if match else None


def refuse_downgrade(running, version, mode):
    """Refuse to release over a newer running version unless this is a rollback.

    A re-run of an older run's failed deploy job reuses its plan, so without
    this it would put that older version back over a newer release. A running
    version that is not strict X.Y.Z (the bootstrap tag, or none) never blocks.
    """
    current = semver(running)
    if mode == "rollback" or current is None or current <= semver(version):
        return
    raise ReleaseError(
        f"{running} is running, which is newer than {version}; "
        "dispatch a rollback to go back on purpose"
    )


def proved(version, images, wait_timeout, request=None, mode=None):
    if request is None:
        request = transport
        trust = trust_context()
    else:
        trust = None
    deadline = time.monotonic() + wait_timeout

    def send(body):
        return request(body, trust, max(1, deadline - time.monotonic()))

    def wait():
        time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))

    checked = mode is None
    while not checked and time.monotonic() < deadline:
        code, value = send(STATUS_BODY())
        if code == 200:
            refuse_downgrade(value.get("version"), version, mode)
            checked = True
        elif code == 503:
            wait()
        else:
            raise ReleaseError(f"status pre-check rejected (HTTP {code})")

    digest = None
    record = None
    while checked and digest is None and time.monotonic() < deadline:
        code, value = send(RELEASE_BODY(version))
        if code == 200:
            digest = released_digest(value, version)
            record = value["released"]
            if ready(value, version, digest, images):
                return value, record
        elif code == 503:
            wait()
        else:
            raise ReleaseError(f"release request rejected (HTTP {code})")
    while digest is not None and time.monotonic() < deadline:
        code, value = send(STATUS_BODY())
        if code == 200:
            if ready(value, version, digest, images):
                return value, record
        elif code != 503:
            raise ReleaseError(f"release request rejected (HTTP {code})")
        wait()
    raise ReleaseError("release evidence deadline exceeded")


def probe(url, timeout):
    opener = urllib.request.build_opener(
        ci.NoRedirect(), urllib.request.ProxyHandler({})
    )
    try:
        with opener.open(url, timeout=timeout) as response:
            raw = response.read(MAX_BODY + 1)
            if response.status != 200 or len(raw) > MAX_BODY:
                return None
    except urllib.error.HTTPError as err:
        err.close()
        return None
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
        return None
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def served(url, sha, expect_db, timeout):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ReleaseError("health url must be http or https")
    deadline = time.monotonic() + timeout
    seen = None
    while True:
        value = probe(url, max(1, min(10, deadline - time.monotonic())))
        if value is not None:
            got = value.get("sha")
            seen = got[:80] if isinstance(got, str) else "(not a string)"
            if got == sha and (not expect_db or value.get("db") == "ok"):
                return value
            if got == sha:
                seen = f"{sha} with db {str(value.get('db'))[:80]!r}"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_SECONDS, remaining))
    if seen is None:
        raise ReleaseError(f"health check never answered 200 with a JSON object; expected sha {sha}")
    raise ReleaseError(f"health check served sha {seen}, expected {sha}")


def github_call(method, url, token, body=None):
    """One GitHub REST call; returns the status code. Redirects are refused so
    the token is never replayed to another host."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(ci.NoRedirect())
    try:
        with opener.open(request, timeout=GITHUB_TIMEOUT) as response:
            response.read(MAX_BODY)
            return response.status
    except urllib.error.HTTPError as err:
        err.close()
        return err.code
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
        raise ReleaseError(f"GitHub API {method} failed to connect") from None


def github_release(tag, sha, environ):
    """Create the GitHub Release for tag at sha, accepting one that already exists."""
    if not TAG_PATTERN.fullmatch(tag):
        raise ReleaseError("--tag is not a valid tag name")
    if not SHA_PATTERN.fullmatch(sha):
        raise ReleaseError("--sha must be 40 lowercase hex characters")
    api = environ.get("GITHUB_API_URL", "").rstrip("/")
    repository = environ.get("GITHUB_REPOSITORY", "")
    token = environ.get("GITHUB_TOKEN", "")
    parsed = urllib.parse.urlsplit(api)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ReleaseError("GITHUB_API_URL must be an http or https URL")
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ReleaseError("GITHUB_REPOSITORY must be owner/name")
    if not token:
        raise ReleaseError("GITHUB_TOKEN is empty")
    releases = f"{api}/repos/{repository}/releases"
    lookup = f"{releases}/tags/{urllib.parse.quote(tag, safe='')}"
    # Look before writing: an existing release is left exactly as it is, so a
    # resume or rollback never duplicates or edits one.
    found = github_call("GET", lookup, token)
    if found == 200:
        return f"GitHub Release {tag} already exists; left unchanged"
    if found != 404:
        raise ReleaseError(f"GitHub release lookup for {tag} failed (HTTP {found})")
    body = {
        "tag_name": tag,
        "name": tag,
        "target_commitish": sha,
        "generate_release_notes": True,
    }
    code = github_call("POST", releases, token, body)
    if code == 201:
        return f"Created GitHub Release {tag}"
    if code == 422 and github_call("GET", lookup, token) == 200:
        # Another run created it between the lookup and the create.
        return f"GitHub Release {tag} already exists; left unchanged"
    raise ReleaseError(f"GitHub refused the release for {tag} (HTTP {code})")


def parse_images(text, version):
    try:
        images = json.loads(text)
    except ValueError:
        raise ReleaseError("--images is not valid JSON") from None
    if not isinstance(images, dict) or not images:
        raise ReleaseError("--images must be a non-empty JSON object")
    for name, spec in images.items():
        if (
            not isinstance(spec, dict)
            or spec.get("repository") != f"{PULL_REGISTRY}/{name}"
            or spec.get("tag") != version
            or not isinstance(spec.get("digest"), str)
            or not DIGEST_PATTERN.fullmatch(spec["digest"])
        ):
            raise ReleaseError(f"--images entry {name!r} is malformed")
    return images


def wait_bound(text):
    try:
        seconds = int(text)
    except ValueError:
        raise ReleaseError("--wait-timeout must be an integer") from None
    if not 1 <= seconds <= MAX_WAIT:
        raise ReleaseError(f"--wait-timeout must be 1..{MAX_WAIT}")
    return seconds


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ReleaseError("invalid arguments: " + message)


def build_parser():
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    rel = commands.add_parser("release")
    rel.add_argument("--version", required=True)
    rel.add_argument("--images", required=True)
    rel.add_argument("--wait-timeout", required=True)
    rel.add_argument("--receipt", required=True)
    rel.add_argument("--mode", required=True, choices=RELEASED_MODES)
    rel.add_argument("--tag", required=True)
    rel.add_argument("--expected-sha", required=True)
    health = commands.add_parser("health")
    health.add_argument("--url", required=True)
    health.add_argument("--sha", required=True)
    health.add_argument("--expect-db", required=True, choices=["true", "false"])
    health.add_argument("--wait-timeout", required=True)
    gh = commands.add_parser("github-release")
    gh.add_argument("--tag", required=True)
    gh.add_argument("--sha", required=True)
    return parser


def run(args):
    if args.command == "github-release":
        return github_release(args.tag, args.sha, os.environ)
    timeout = wait_bound(args.wait_timeout)
    if args.command == "health":
        if not SHA_PATTERN.fullmatch(args.sha):
            raise ReleaseError("--sha must be 40 lowercase hex characters")
        value = served(args.url, args.sha, args.expect_db == "true", timeout)
        return f"Verified served build: {value['sha']}"
    if not VERSION_PATTERN.fullmatch(args.version):
        raise ReleaseError("--version must be X.Y.Z")
    if not TAG_PATTERN.fullmatch(args.tag):
        raise ReleaseError("--tag is not a valid tag name")
    if not SHA_PATTERN.fullmatch(args.expected_sha):
        raise ReleaseError("--expected-sha must be 40 lowercase hex characters")
    images = parse_images(args.images, args.version)
    result, record = proved(args.version, images, timeout, mode=args.mode)
    plan = {
        "mode": args.mode,
        "version": args.version,
        "tag": args.tag,
        "expected_sha": args.expected_sha,
    }
    result = {**result, "released": record, "wac_cd": plan}
    Path(args.receipt).write_text(json.dumps(result, indent=2) + "\n")
    return "Verified release receipt: " + args.receipt


def main(argv):
    try:
        line = run(build_parser().parse_args(argv[1:]))
    except ReleaseError as err:
        print("wacd release: " + " ".join(str(err).split()), file=sys.stderr)
        return 1
    except Exception:
        print("wacd release: unexpected failure", file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
