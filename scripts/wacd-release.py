#!/usr/bin/env python3
"""Release a published deploy chart through Lab's gateway and prove it is served.

wac-cd publishes the deploy chart, then asks the release gateway to release
that chart version and polls its status until Argo CD has synced the chart's
manifest digest, every Deployment has rolled out, and the Deployments run this
release's images. The transport is scripts/ci-release.py's, which mints a fresh
GitHub OIDC token for every request. That file belongs to other components and
is loaded, never changed.

The gateway contract (Plan 2, "Chart releases (wac-cd)") is isolated in
RELEASE_BODY, STATUS_BODY and ready(). Any problem exits 1 with one stable
"wacd release: ..." stderr line, and no token is ever printed.
"""

import argparse
import importlib.util
import json
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
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
POLL_SECONDS = 5
MAX_BODY = 1024 * 1024
MAX_WAIT = 3600


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


def expected_refs(images):
    return {
        spec["repository"]: f"{spec['repository']}:{spec['tag']}@{spec['digest']}"
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
    deployments = value.get("deployments")
    if not isinstance(deployments, list) or not deployments:
        raise ReleaseError("deployment evidence is missing")
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
    wanted = expected_refs(images)
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

    digest = None
    while digest is None and time.monotonic() < deadline:
        code, value = send(RELEASE_BODY(version))
        if code == 200:
            digest = released_digest(value, version)
            if ready(value, version, digest, images):
                return value
        elif code == 503:
            wait()
        else:
            raise ReleaseError(f"release request rejected (HTTP {code})")
    while digest is not None and time.monotonic() < deadline:
        code, value = send(STATUS_BODY())
        if code == 200:
            if ready(value, version, digest, images):
                return value
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
    except (OSError, urllib.error.URLError, ValueError):
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
            seen = got if isinstance(got, str) else "(not a string)"
            if got == sha and (not expect_db or value.get("db") == "ok"):
                return value
            if got == sha:
                seen = f"{sha} with db {value.get('db')!r}"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_SECONDS, remaining))
    if seen is None:
        raise ReleaseError(f"health check never answered 200 with a JSON object; expected sha {sha}")
    raise ReleaseError(f"health check served sha {seen}, expected {sha}")


def parse_images(text):
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
            or not isinstance(spec.get("tag"), str)
            or not spec["tag"]
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
    health = commands.add_parser("health")
    health.add_argument("--url", required=True)
    health.add_argument("--sha", required=True)
    health.add_argument("--expect-db", required=True, choices=["true", "false"])
    health.add_argument("--wait-timeout", required=True)
    return parser


def run(args):
    timeout = wait_bound(args.wait_timeout)
    if args.command == "health":
        value = served(args.url, args.sha, args.expect_db == "true", timeout)
        return f"Verified served build: {value['sha']}"
    if not VERSION_PATTERN.fullmatch(args.version):
        raise ReleaseError("--version must be X.Y.Z")
    images = parse_images(args.images)
    result = release(args.version, images, timeout)
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
