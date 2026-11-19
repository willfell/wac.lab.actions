#!/usr/bin/env python3
"""Ask Lab's gateway for an app's settings copy and decide where wac-cd reads its settings.

wac-cd's `plan` job runs `fetch --out .wacd-contract` once. It posts
`{"operation": "contract"}` to the release gateway with a fresh GitHub OIDC
job token (ci-release.py's audience, minted anew for every attempt, since the
gateway burns each token's jti) and gets back hemi's copy of the app's
settings, `active.json` in wac.fleet, with the wac.fleet commit it was read at.
Then, as the design numbers the rules:

1. The copy says `source: hemi`: the run acts on it. `<out>/contract.json` is
   the copy as received, `<out>/profile.json` the wacd profile it stands for
   (in `.github/wacd.json`'s shape, its `values_file` pointing at
   `<out>/values.json`), and `<out>/values.json` the copy's `runtime`.
2. The copy says `source: files`: the repository's files, as before, and the
   chart still names the copy (`<out>/contract.json` alone is written).
3. The gateway holds no copy and the repository has its files: the files,
   exactly as before.
4. Anything else fails before anything is built: a copy that cannot be read
   or names another repository than the job token's, no copy and no files, a
   refusal, or a gateway that stays unavailable.

On success it prints `source=hemi|files`, `revision=<n>|`,
`fleet_commit=<sha>|` and `reason=<sentence>` for $GITHUB_OUTPUT. On failure
it prints one `wacd contract: <sentence>` line to stderr and exits 1, and
nothing is left under `<out>` for a later job to act on. Either way the job
summary names the source, revision and fleet commit with the sentence.

A 503 is the gateway's "release dependency unavailable" (GitHub slow,
rate-limited or down behind it) and is retried every POLL_SECONDS within
CONTRACT_WAIT seconds, as a `release` retries it. A 403 is a denial, told
apart from a 503 by its status, never its body: a coded one carries one of
REFUSALS' codes, an unverified caller none. The response body is never
printed; only a sentence chosen here is. The gateway's address is
ci-release.py's constant, never an input, and no token is ever printed.
"""

import argparse
import base64
import binascii
import http.client
import importlib.util
import json
import os
import re
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci = _load("wacd_contract_ci_release", "ci-release.py")
profile = _load("wacd_contract_profile", "wacd-profile.py")
chart = _load("wacd_contract_chart", "wacd-chart.py")

CONTRACT_BODY = {"operation": "contract"}
CONTRACT_WAIT = 120
POLL_SECONDS = 5
REQUEST_TIMEOUT = 30
MAX_BODY = 1024 * 1024
FILES_PROFILE = Path(".github") / "wacd.json"
SOURCES = ("files", "hemi")
SHA40 = re.compile(r"[0-9a-f]{40}")
CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
REPOSITORY_ID = re.compile(r"[0-9]{1,20}")

# The gateway's closed list of refusal codes, each with the sentence the run
# summary shows. The release-check codes come back from `release`, not from
# `contract`; they are listed so one map serves both.
REFUSALS = {
    "fleet_copy_missing": (
        "This app has released from hemi's settings before, and the gateway found no copy "
        "of them, so nothing was built."
    ),
    "fleet_copy_unreadable": (
        "The gateway could not read hemi's copy of this app's settings, so nothing was built."
    ),
    "fleet_copy_mismatch": (
        "hemi's copy of this app's settings names another app, so nothing was built."
    ),
    "fleet_commit_not_on_default_branch": (
        "The settings this release was planned with are no longer on the fleet copy's main line."
    ),
    "fleet_commit_older_than_last_release": (
        "The settings this release was planned with are older than the settings already released."
    ),
    "revision_annotation_missing": (
        "This app releases from hemi's settings, and this chart does not say which revision "
        "of them it was built from."
    ),
    "revision_mismatch": (
        "This chart names a different revision of the settings from the copy it points to."
    ),
}

SENTENCES = {
    "unverified": (
        "The gateway did not accept this run as a release of a registered app, so it handed "
        "over no settings."
    ),
    "unknown_refusal": (
        "The gateway refused to hand over this app's settings for a reason this wac-cd does not know"
    ),
    "another_repository": (
        "The settings copy the gateway handed over belongs to another repository, so nothing "
        "was built."
    ),
    "token_unknown": (
        "The job token does not say which repository it belongs to, so the settings copy "
        "cannot be checked and nothing was built."
    ),
    "unreadable": "The settings copy the gateway handed over cannot be read",
    "no_files": (
        "The gateway holds no copy of this app's settings and the repository has no "
        ".github/wacd.json, so there is nothing to release from."
    ),
    "files_copy_no_files": (
        "hemi's copy says this app's settings live in its files, and the repository has no "
        ".github/wacd.json, so there is nothing to release from."
    ),
    "unavailable": (
        f"The gateway could not read the fleet copy within {CONTRACT_WAIT} seconds; re-run "
        "when GitHub is answering."
    ),
    "status": "The gateway answered HTTP {status} when asked for this app's settings.",
    "unreachable": (
        "The gateway could not be reached for this app's settings; check the runner's TLS "
        "trust and the gateway's readiness."
    ),
    "no_trust": (
        "The runner's TLS trust for the gateway is missing or invalid, so the settings were "
        "not asked for."
    ),
    "no_token": (
        "GitHub would not mint a job token for the gateway; the plan job needs id-token: write."
    ),
    "unwritable": "The settings copy could not be written for the later jobs.",
    "unexpected": "Reading the settings stopped unexpectedly, so nothing was built.",
}


class Refusal(Exception):
    """The run stops here; the message is a sentence safe to print."""


def trust_context():
    try:
        return ssl.create_default_context(cafile=ci.CA_FILE)
    except (OSError, ssl.SSLError):
        raise Refusal(SENTENCES["no_trust"]) from None


def denial(value):
    """The sentence for a 403, chosen by the reason code it carries."""
    reason = value.get("reason") if isinstance(value, dict) else None
    if reason is None:
        return SENTENCES["unverified"]
    if isinstance(reason, str) and reason in REFUSALS:
        return REFUSALS[reason]
    if isinstance(reason, str) and CODE.fullmatch(reason):
        return f"{SENTENCES['unknown_refusal']} (code {reason})."
    return SENTENCES["unknown_refusal"] + "."


def post(trust, timeout):
    """One contract request with a token minted for it: (status, parsed body or None, token)."""
    try:
        token = ci.oidc_token()
    except ci.ReleaseError:
        raise Refusal(SENTENCES["no_token"]) from None
    request = urllib.request.Request(
        ci.URL,
        data=json.dumps(CONTRACT_BODY).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(
        ci.NoRedirect(),
        urllib.request.HTTPSHandler(context=trust),
        urllib.request.ProxyHandler({}),
    )
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
        raise Refusal(SENTENCES["unreachable"]) from None
    try:
        with response:
            status = response.code if isinstance(response, urllib.error.HTTPError) else response.status
            raw = response.read(MAX_BODY + 1)
    except (OSError, http.client.HTTPException, ValueError):
        raise Refusal(SENTENCES["unreachable"]) from None
    value = None
    if len(raw) <= MAX_BODY:
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            value = None
    return status, value, token


def ask(trust):
    """The gateway's 200 answer and the token that got it; 503 is retried within CONTRACT_WAIT."""
    deadline = time.monotonic() + CONTRACT_WAIT
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        status, value, token = post(trust, max(1, min(REQUEST_TIMEOUT, remaining)))
        if status == 200:
            return value, token
        if status == 403:
            raise Refusal(denial(value))
        if status != 503:
            raise Refusal(SENTENCES["status"].format(status=status))
        time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
    raise Refusal(SENTENCES["unavailable"])


def token_repository_id(token):
    """The repository_id claim of the job token the copy was handed to, or None.

    The token is read, not verified: the gateway verified it, and this only
    checks that the copy it answered with names the same repository.
    """
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError, binascii.Error, UnicodeDecodeError):
        return None
    found = claims.get("repository_id") if isinstance(claims, dict) else None
    return found if isinstance(found, str) and REPOSITORY_ID.fullmatch(found) else None


def unreadable(detail=""):
    suffix = f" ({' '.join(detail.split())[:200]})" if detail else ""
    return Refusal(f"{SENTENCES['unreadable']}{suffix}, so nothing was built.")


def read_copy(value, token):
    """(copy, fleet commit) from the gateway's answer, or (None, None) when it holds none."""
    if not isinstance(value, dict):
        raise unreadable()
    copy, commit = value.get("copy"), value.get("fleet_commit")
    if copy is None and commit is None:
        return None, None
    if not isinstance(copy, dict) or not isinstance(commit, str) or not SHA40.fullmatch(commit):
        raise unreadable()
    identity, flags = copy.get("identity"), copy.get("flags")
    if (
        type(copy.get("version")) is not int
        or copy["version"] != 1
        or not isinstance(identity, dict)
        or not isinstance(identity.get("repositoryId"), str)
        or type(identity.get("revision")) is not int
        or identity["revision"] < 1
        or not isinstance(flags, dict)
        or flags.get("source") not in SOURCES
        or not isinstance(copy.get("runtime"), dict)
    ):
        raise unreadable()
    owner = token_repository_id(token)
    if owner is None:
        raise Refusal(SENTENCES["token_unknown"])
    if identity["repositoryId"] != owner:
        raise Refusal(SENTENCES["another_repository"])
    return copy, commit


def clear(out):
    if out.is_symlink() or out.is_file():
        out.unlink()
    elif out.exists():
        shutil.rmtree(out)


def write(out, documents):
    try:
        out.mkdir(parents=True)
        for name, document in documents.items():
            (out / name).write_text(json.dumps(document, indent=2) + "\n")
    except OSError:
        raise Refusal(SENTENCES["unwritable"]) from None


def fetch(out):
    """Ask once (retrying 503) and leave the copy under out; returns the step outputs."""
    clear(out)
    value, token = ask(trust_context())
    copy, commit = read_copy(value, token)
    files = FILES_PROFILE.is_file()
    if copy is None:
        if not files:
            raise Refusal(SENTENCES["no_files"])
        reason = (
            "The gateway holds no copy of this app's settings, so they come from the "
            "repository's files, as before."
        )
        return "files", "", "", reason
    revision = chart.revision_annotation(copy["identity"]["revision"])
    if copy["flags"]["source"] == "files":
        if not files:
            raise Refusal(SENTENCES["files_copy_no_files"])
        write(out, {"contract.json": copy})
        reason = (
            f"hemi's copy (revision {revision}) says this app's settings live in its files, "
            f"so they come from the files and the chart names revision {revision}."
        )
        return "files", revision, commit, reason
    try:
        derived = profile.from_contract(copy, profile.DERIVED_VALUES)
        profile.validate(derived, Path.cwd(), values_in_repo=False)
    except profile.ProfileError as err:
        raise unreadable(str(err)) from None
    write(out, {"contract.json": copy, "profile.json": derived, "values.json": copy["runtime"]})
    reason = (
        f"Settings come from hemi's copy, revision {revision}, read at wac.fleet "
        f"commit {commit[:12]}."
    )
    return "hemi", revision, commit, reason


def summarize(source, revision, commit, sentence):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "### wac-cd settings",
        "",
        "| source | revision | fleet commit |",
        "| --- | --- | --- |",
        f"| {source or '-'} | {revision or '-'} | {commit or '-'} |",
        "",
        sentence,
    ]
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass


def build_parser():
    parser = argparse.ArgumentParser(prog="wacd-contract.py", description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_parser = commands.add_parser("fetch", help="ask the gateway for the copy (wac-cd plan)")
    fetch_parser.add_argument("--out", required=True)
    return parser


def main(argv):
    args = build_parser().parse_args(argv[1:])
    out = Path(args.out)
    try:
        source, revision, commit, reason = fetch(out)
    except Exception as error:
        sentence = str(error) if isinstance(error, Refusal) else SENTENCES["unexpected"]
        try:
            clear(out)
        except OSError:
            pass
        summarize(None, None, None, sentence)
        print("wacd contract: " + sentence, file=sys.stderr)
        return 1
    print(f"source={source}\nrevision={revision}\nfleet_commit={commit}\nreason={reason}")
    summarize(source, revision, commit, reason)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
