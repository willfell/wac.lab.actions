#!/usr/bin/env python3
"""Ask for an app's settings copy and decide where wac-cd and wac-ci read their settings.

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
   (its runtime setting a key wacd-chart.py's package owns included, which
   publish would refuse only after the tag) or names another repository than
   the job token's, no copy and no files, a refusal, or a gateway that stays
   unavailable.

On success it prints `source=hemi|files`, `revision=<n>|`,
`fleet_commit=<sha>|` and `reason=<sentence>` for $GITHUB_OUTPUT. On failure
it prints one `wacd contract: <sentence>` line to stderr and exits 1, and
nothing is left under `<out>` for a later job to act on. Either way the job
summary names the source, revision and fleet commit with the sentence.

A 503 is the gateway's "release dependency unavailable" (GitHub slow,
rate-limited or down behind it) and is retried within `--wait-timeout`
seconds, the workflow's `wait_timeout` (parsed by wacd-release's own
wait_bound, so both jobs accept the same values), capped at PLAN_WAIT_CAP, the
same bound `deploy` gives a `release`: before the contract operation existed, a release rode out such a
window in `deploy`, and moving the first gateway call into `plan` must not
make it fail sooner. Each attempt waits the whole time left for its answer,
as `deploy`'s requests do: the gateway first checks the caller's revision
against main with several GitHub calls and can be slow under a slow GitHub,
and a slow answer is still an answer. A connection nothing answered on
(wacd-release.py's no_connection(): refused, reset, closed during the TLS
handshake or before the status line, a connect timeout, no route to the host
or network) is retried with a fresh token and the same back-off for at most
RESTART_WINDOW seconds from the first in a row, and then fails as
unreachable: the gateway's Recreate rollout leaves about 35 s with nothing
listening, and it sheds load by closing a socket before the handshake. A
certificate that fails verification fails at once, as does any other
transport failure and a read still unanswered at the deadline. The interval
between 503s starts at FIRST_POLL_SECONDS and doubles up to
MAX_POLL_SECONDS, so a long outage costs about two requests a minute. A 403
is a denial, told apart from a 503 by its status, never its body: a coded one
carries one of REFUSALS' codes and is final, an uncoded one none, and since
the gateway also answers a token it could not check that way, it is asked
UNCODED_ATTEMPTS times in all, UNCODED_PAUSE seconds apart, before it fails
saying the gateway refused this run's token. The response body is never
printed; only a sentence is. The request is wacd-release.py's post(), and the
code-to-sentence map is wacd-release.py's REFUSALS with its two defaults, so a
refused contract and a refused release read alike. The gateway's address is
ci-release.py's constant, never an input, and no token is ever printed.

wac-ci's `contract` job runs `pr --out .wacd-contract --main <checkout>
--head <checkout>` once per pull request. `--main` is the default branch's
head and `--head` the pull request's merge commit, each checked out by
actions/checkout at depth 1 with no blobs but `.github/wacd.json`'s. Their
trees are read with `git ls-tree` and main's profile with `git cat-file`, as
data: nothing in either checkout is run or followed. It asks hemi (wacd-shadow.py's
address and audience, the same constants the shadow job uses) with
`GET /api/v1/contract` and a token minted for each attempt. Hemi answers a
pull request's job with its repository's active copy as it stands. Whether a
repository is cut over is read from main, never from the pull request, which
could otherwise put the files back with gates of its own and silence hemi by
dropping `id-token` from its own ci.yml:

| hemi | files on main | files in the merge commit | result |
| --- | --- | --- | --- |
| 200 `hemi` | absent | present | fails: `<path> was added back`, or, when the merge commit's first parent is not main's head, the merge commit predates the clean-up |
| 200 `hemi` | present | present | hemi's settings, and a line per file still on main; fails when the pull request changes either file's blob |
| 200 `hemi` | absent | absent | hemi's settings |
| 200 `files` | absent | present | the files: the restore pull request, or a new app's first one |
| 200 `files` | absent | absent | fails: wait for the restore pull request (or, for a new app, nothing to check) |
| 200 `files` | present | any | the files, as before |
| anything else | present | present | the files, as before; the summary names hemi's status |
| anything else | present | removes one | fails: hemi did not answer and the pull request removes the files |
| 401 or 403 | absent | any | fails: hemi refused the token (or the event, outside `pull_request`); register a new app first |
| no answer, a timeout or 5xx | absent | any | fails: `hemi did not answer; re-run when it does.` |
| an address that does not resolve, three times | absent | any | fails: the job must run on a Lab runner |
| no token minted | absent | any | fails: GitHub would not mint a job token |
| any other status | absent | any | fails, naming the status |

"Files on main" is `.github/wacd.json` on main, the profile that holds the
gates; the paths checked in the merge commit are that file, the values path
main's profile names and the copy's `delivery.valuesFile`. A `files` answer is
read for its source alone: hemi answers a registered app that was never cut
over with `version`, `identity.repositoryId`, `identity.repository`,
`identity.app` and `flags.source` and nothing else, so only `flags.source` is
required, and `identity.repositoryId`, when present, must be the token's. A
`hemi` answer is read in full. A 200 that cannot be read, names another
repository than the token's or does not derive a profile is treated as an
answer but a 200, and so is a job with no `id-token` grant.
No answer, a 5xx and an address that does not resolve are asked again, PR_ATTEMPTS times in all, PR_BACKOFF
apart; each attempt, token mint included, is bounded at PR_ATTEMPT_SECONDS by
a worker thread, so a reply that trickles in counts as silence. On success it prints
`source=hemi|files` and `revision=<n>|`, and with source `hemi` leaves
`contract.json`, `profile.json` and `values.json` under `<out>` as `fetch`
does; otherwise `<out>` is removed. A failure prints one `wacd contract:`
line to stderr and exits 1. Either way the job summary says why, naming hemi's
status and never its body.
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
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = _load("wacd_contract_release", "wacd-release.py")
ci = release.ci
profile = _load("wacd_contract_profile", "wacd-profile.py")
chart = _load("wacd_contract_chart", "wacd-chart.py")
shadow = _load("wacd_contract_shadow", "wacd-shadow.py")

CONTRACT_BODY = {"operation": "contract"}
FIRST_POLL_SECONDS = 5
MAX_POLL_SECONDS = 30
MAX_WAIT = release.MAX_WAIT
# plan's job timeout (40 minutes) is sized for this wait plus its other steps,
# and a workflow test ties the two. A larger wait_timeout still bounds deploy,
# but plan stops here so it fails with its summary line, not the job timeout.
PLAN_WAIT_CAP = 2100
# The gateway's Deployment is Recreate: a rollout leaves about 35 s with no
# endpoint. A refused or reset connection is retried for at most this long
# from the first refusal in a row, and never past the overall wait.
RESTART_WINDOW = 90
# The gateway answers a token it could not check (a failed signing-key fetch,
# a busy replay store) with the same uncoded 403 as a caller it refuses, so an
# uncoded 403 is asked this many times in all, UNCODED_PAUSE seconds apart.
UNCODED_ATTEMPTS = 3
UNCODED_PAUSE = 10
REGISTER_FIRST = "if this app is new, register it with lab first."
FILES_PROFILE = Path(".github") / "wacd.json"
SOURCES = ("files", "hemi")
SHA40 = re.compile(r"[0-9a-f]{40}")
REPOSITORY_ID = re.compile(r"[0-9]{1,20}")

REFUSALS = release.REFUSALS

CONTRACT_ROUTE = "/api/v1/contract"
PR_ATTEMPTS = 3
PR_BACKOFF = (5, 10)
PR_ATTEMPT_SECONDS = 15
GIT_SECONDS = 30
MAX_ANSWER = 1048576
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
RUNNER_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")
EVENT_NAME = re.compile(r"[a-z_]{1,40}")
GRANT_ENV = ("ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN")
CUT_OVER_CLAUSE = "{app}'s settings live in hemi, so the checks cannot run"
CUT_OVER = CUT_OVER_CLAUSE + "."
UNREACHABLE = (
    "the contract job must run on a Lab runner: {runner} cannot reach hemi's in-cluster address. "
    "Pass a Lab pool as `runner`."
)

PR_SENTENCES = {
    "added_back": "{path} was added back; {app}'s settings live in hemi.",
    "changed": "{path} was changed; {app}'s settings live in hemi, so change them there.",
    "stale": "This run's merge commit is older than {app}'s clean-up; update the branch and re-run.",
    "removed": "hemi did not answer, and this pull request removes {app}'s files; re-run when hemi answers.",
    "still_on_main": (
        "{path} is still on main until hemi's clean-up pull request merges; hemi's settings were used."
    ),
    "hemi": "Settings come from hemi's copy, revision {revision}.",
    "files": (
        "hemi's copy (revision {revision}) says {app}'s settings live in its files, so the pull "
        "request's own files were used."
    ),
    "restore": (
        "hemi's copy (revision {revision}) says {app} is returning to its files and this pull "
        "request puts them back, so its own files were used."
    ),
    "new": "hemi holds no settings for {app} yet, so the pull request's own files were used.",
    "new_nothing": (
        "hemi holds no settings for {app} yet, and neither main nor this pull request has "
        ".github/wacd.json, so there is nothing to check."
    ),
    "returning": (
        "hemi says {app} is returning to its files, and neither main nor this pull request has "
        ".github/wacd.json; wait for hemi's restore pull request and re-run after it merges."
    ),
    "status_files": "hemi answered {status}; using the repository's files.",
    "silent_files": "hemi did not answer; using the repository's files.",
    "no_grant_files": "no job token for hemi; using the repository's files.",
    "no_mint_files": "GitHub would not mint a job token for hemi; using the repository's files.",
    "unreachable_files": UNREACHABLE,
    "unreadable_files": "hemi's answer could not be read; using the repository's files.",
    "refused": "hemi refused this pull request's token (HTTP {status}); " + CUT_OVER_CLAUSE + "; " + REGISTER_FIRST,
    "refused_event": (
        "hemi refused the job token of this {event} run (HTTP {status}): it admits wac-ci only for "
        "pull_request runs, and " + CUT_OVER_CLAUSE + "; " + REGISTER_FIRST
    ),
    "silent": "hemi did not answer; re-run when it does.",
    "no_mint": "GitHub would not mint a job token for hemi; re-run the checks.",
    "unreachable": UNREACHABLE,
    "status": "hemi answered HTTP {status}; " + CUT_OVER,
    "no_grant": (
        "GitHub would not mint a job token for hemi: the caller's ci.yml must grant "
        "id-token: write, and " + CUT_OVER
    ),
    "unreadable": "hemi's answer could not be read; " + CUT_OVER,
    "tree": "The {which} could not be read, so the settings' source cannot be decided.",
    "unexpected": "Reading the settings stopped unexpectedly, so the checks did not run.",
}

SENTENCES = {
    "unverified": (
        f"The gateway refused this run's token on each of {UNCODED_ATTEMPTS} attempts (HTTP 403 with no "
        "reason), so nothing was built; a gateway that does not yet serve the contract operation "
        "(wacwini/wac.lab#570 not deployed) looks like this too; " + REGISTER_FIRST
    ),
    "unknown_refusal": release.UNKNOWN_REFUSAL,
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
        "The gateway could not read the fleet copy within {wait} seconds; re-run "
        "when GitHub is answering."
    ),
    "wait": f"--wait-timeout must be a whole number of seconds from 1 to {MAX_WAIT}, as wait_timeout is for deploy.",
    "status": "The gateway answered HTTP {status} when asked for this app's settings.",
    "unreachable": (
        "The gateway could not be reached for this app's settings, or did not answer within "
        "the wait; check the runner's TLS trust and the gateway's readiness."
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


def post(trust, timeout):
    """One contract request with a token minted for it: (status, parsed body or None, token).

    A connection nothing answered on passes through as release.Unanswered
    for ask() to retry; any other transport failure is final.
    """
    try:
        return release.post(CONTRACT_BODY, trust, timeout)
    except release.Unanswered:
        raise
    except release.Unreachable:
        raise Refusal(SENTENCES["unreachable"]) from None
    except ci.ReleaseError:
        raise Refusal(SENTENCES["no_token"]) from None


def ask(trust, wait):
    """The gateway's 200 answer and the token that got it.

    A 503 is retried within wait seconds; a connection nothing answered on
    (release.no_connection) within RESTART_WINDOW seconds of the first in a
    row, and never past wait. Each retry mints a fresh token and backs off as
    a 503 does. An uncoded 403 is asked UNCODED_ATTEMPTS times in all,
    UNCODED_PAUSE seconds apart, within wait; a coded one is final.
    """
    deadline = time.monotonic() + wait
    interval = FIRST_POLL_SECONDS
    refused_since = None
    uncoded = 0
    last_uncoded = False
    while time.monotonic() < deadline:
        try:
            status, value, token = post(trust, max(1, deadline - time.monotonic()))
        except release.Unanswered:
            now = time.monotonic()
            refused_since = now if refused_since is None else refused_since
            if now - refused_since >= RESTART_WINDOW:
                raise Refusal(SENTENCES["unreachable"]) from None
            time.sleep(min(interval, RESTART_WINDOW - (now - refused_since), max(0, deadline - now)))
            interval = min(interval * 2, MAX_POLL_SECONDS)
            continue
        refused_since = None
        last_uncoded = False
        if status == 200:
            return value, token
        if status == 403:
            sentence = release.refusal_sentence(value)
            if sentence != release.UNVERIFIED:
                raise Refusal(sentence)
            uncoded += 1
            last_uncoded = True
            if uncoded >= UNCODED_ATTEMPTS:
                raise Refusal(SENTENCES["unverified"])
            time.sleep(min(UNCODED_PAUSE, max(0, deadline - time.monotonic())))
            continue
        if status != 503:
            raise Refusal(SENTENCES["status"].format(status=status))
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        interval = min(interval * 2, MAX_POLL_SECONDS)
    if last_uncoded:
        raise Refusal(SENTENCES["unverified"])
    if refused_since is not None:
        raise Refusal(SENTENCES["unreachable"])
    raise Refusal(SENTENCES["unavailable"].format(wait=wait))


def wait_seconds(text):
    """--wait-timeout as deploy reads wait_timeout, capped at PLAN_WAIT_CAP."""
    try:
        seconds = release.wait_bound(text)
    except release.ReleaseError:
        raise Refusal(SENTENCES["wait"]) from None
    return min(seconds, PLAN_WAIT_CAP)


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
        or not 1 <= identity["revision"] <= chart.MAX_REVISION
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


def fetch(out, wait_text):
    """Ask once (retrying 503) and leave the copy under out; returns the step outputs."""
    wait = wait_seconds(wait_text)
    clear(out)
    value, token = ask(trust_context(), wait)
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
    try:
        chart.deploy_values(copy["runtime"], derived["app"], {})
    except chart.ChartError as err:
        raise unreadable(f"its runtime is the values file, and {err}") from None
    write(out, {"contract.json": copy, "profile.json": derived, "values.json": copy["runtime"]})
    reason = (
        f"Settings come from hemi's copy, revision {revision}, read at wac.fleet "
        f"commit {commit[:12]}."
    )
    return "hemi", revision, commit, reason


def tree_entries(checkout, paths, which):
    """{path: blob id} for the paths among these that are files in the tree at checkout's HEAD.

    Only trees are read, so a checkout fetched with no blobs answers; the
    names are compared exactly, never as patterns, and two files are the same
    exactly when their blob ids are.
    """
    wanted = sorted(set(paths))
    output = git(checkout, ["ls-tree", "-z", "HEAD", "--", *wanted], which)
    found = {}
    for entry in output.split(b"\0"):
        meta, tab, name = entry.partition(b"\t")
        fields = meta.split(b" ")
        if tab and len(fields) == 3 and fields[1] == b"blob":
            found[name.decode("utf-8", "replace")] = fields[2].decode("ascii", "replace")
    return {path: found[path] for path in wanted if path in found}


def first_parent(checkout, which):
    """The first parent named in the raw commit at checkout's HEAD, or None.

    The raw object is read, so a depth-1 checkout, whose parents are cut off,
    still names it. A pull request's merge commit has the base branch's head
    there, as it was when GitHub made it.
    """
    raw = git(checkout, ["cat-file", "commit", "HEAD"], which)
    for line in raw.split(b"\n"):
        if not line:
            break
        if line.startswith(b"parent "):
            return line[len(b"parent "):].decode("ascii", "replace")
    return None


def git(checkout, args, which):
    """git's stdout in checkout; no prompt, no lazy fetch, bounded."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"}
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args], capture_output=True, env=env, timeout=GIT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Refusal(PR_SENTENCES["tree"].format(which=which)) from None
    if result.returncode != 0:
        raise Refusal(PR_SENTENCES["tree"].format(which=which))
    return result.stdout


def main_values_path(checkout):
    """The values path main's .github/wacd.json names, or None when it names none readable."""
    raw = git(checkout, ["cat-file", "blob", f"HEAD:{FILES_PROFILE.as_posix()}"], "default branch's tree")
    try:
        value = json.loads(raw).get("values_file")
        profile.check_relative(value, "values_file")
    except (ValueError, AttributeError, profile.ProfileError):
        return None
    return value


def granted():
    return all(os.environ.get(name) for name in GRANT_ENV)


def hemi_attempt():
    """(status, body, token) for one GET with a token minted for it.

    Without an answer the status says why instead: "no_mint" when GitHub
    minted no token, "unreachable" when hemi's address does not resolve (a
    runner outside the cluster), and None for silence.
    """
    try:
        token = shadow.oidc_token()
    except shadow.Unanswered:
        return "no_mint", None, None
    request = urllib.request.Request(
        shadow.URL + CONTRACT_ROUTE, headers={"Authorization": "Bearer " + token}, method="GET"
    )
    opener = urllib.request.build_opener(shadow.NoRedirect(), urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=PR_ATTEMPT_SECONDS)
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, None, token
    except urllib.error.URLError as error:
        return ("unreachable" if isinstance(error.reason, socket.gaierror) else None), None, token
    except (OSError, ValueError, http.client.HTTPException):
        return None, None, token
    try:
        with response:
            raw = response.read(MAX_ANSWER + 1)
    except (OSError, ValueError, http.client.HTTPException):
        return None, None, token
    return response.status, raw, token


def bounded_attempt():
    """hemi_attempt, or an unanswered attempt once PR_ATTEMPT_SECONDS have passed."""
    result = []
    worker = threading.Thread(target=lambda: result.append(hemi_attempt()), daemon=True)
    worker.start()
    worker.join(PR_ATTEMPT_SECONDS + 1)
    return result[0] if result else (None, None, None)


def ask_hemi():
    """("ok", copy), ("status", code) or (silent | no_mint | unreachable | no_grant | unreadable, None).

    Silence, a 5xx, a token GitHub would not mint and an address that does
    not resolve are asked again: a lookup can fail once on a Lab runner, and
    only on a runner outside the cluster does it keep failing.
    """
    if not granted():
        return "no_grant", None
    last = "silent"
    for attempt in range(PR_ATTEMPTS):
        if attempt:
            time.sleep(PR_BACKOFF[min(attempt, len(PR_BACKOFF)) - 1])
        status, raw, token = bounded_attempt()
        if status == "unreachable":
            last = "unreachable"
            continue
        if status == "no_mint":
            last = "no_mint"
            continue
        if status is None or 500 <= status <= 599:
            last = "silent"
            continue
        if status != 200:
            return "status", status
        copy = read_pr_copy(raw, token)
        return ("ok", copy) if copy is not None else ("unreadable", None)
    return last, None


def read_pr_copy(raw, token):
    """hemi's answer to a pull request's job, or None when it cannot be used.

    A `files` answer is read for flags.source alone, and for
    identity.repositoryId when present, which must be the token's: hemi answers
    a registered app that was never cut over with no revision and no settings
    document, and the pull request runs on its own files whatever else the
    answer holds. A `hemi` answer is checked as fetch checks the gateway's copy.
    """
    if raw is None or len(raw) > MAX_ANSWER:
        return None
    try:
        copy = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(copy, dict):
        return None
    identity, flags, delivery = copy.get("identity"), copy.get("flags"), copy.get("delivery")
    if not isinstance(flags, dict) or flags.get("source") not in SOURCES:
        return None
    if flags["source"] == "files":
        if isinstance(identity, dict) and "repositoryId" in identity:
            owner = token_repository_id(token) if token else None
            if owner is None or identity["repositoryId"] != owner:
                return None
        return copy
    if (
        type(copy.get("version")) is not int
        or copy["version"] != 1
        or not isinstance(identity, dict)
        or not isinstance(identity.get("repositoryId"), str)
        or not isinstance(identity.get("app"), str)
        or not profile.APP.fullmatch(identity["app"])
        or type(identity.get("revision")) is not int
        or not 1 <= identity["revision"] <= chart.MAX_REVISION
        or not isinstance(flags, dict)
        or flags.get("source") not in SOURCES
        or not isinstance(copy.get("runtime"), dict)
        or not isinstance(delivery, dict)
    ):
        return None
    owner = token_repository_id(token) if token else None
    if owner is None or identity["repositoryId"] != owner:
        return None
    try:
        profile.check_relative(delivery.get("valuesFile"), "contract.delivery.valuesFile")
        profile.from_contract(copy, profile.DERIVED_VALUES)
    except profile.ProfileError:
        return None
    return copy


def repository_name():
    name = os.environ.get("GITHUB_REPOSITORY", "")
    return name if REPOSITORY.fullmatch(name) else "this repository"


def runner_name():
    name = os.environ.get("JOB_RUNNER_NAME", "")
    return name if RUNNER_NAME.fullmatch(name) else "this runner"


def event_name():
    name = os.environ.get("GITHUB_EVENT_NAME", "")
    return name if EVENT_NAME.fullmatch(name) else "unnamed"


def files_answer(copy):
    """(app, revision or None) from a `files` answer, which need carry neither."""
    identity = copy.get("identity") if isinstance(copy.get("identity"), dict) else {}
    app = identity.get("app")
    app = app if isinstance(app, str) and profile.APP.fullmatch(app) else repository_name()
    revision = identity.get("revision")
    if type(revision) is not int or not 1 <= revision <= chart.MAX_REVISION:
        return app, None
    return app, chart.revision_annotation(revision)


def pull_request(out, main, head):
    """Decide a pull request's settings source; returns (source, revision, sentences)."""
    clear(out)
    profile_path = FILES_PROFILE.as_posix()
    on_main = tree_entries(main, [profile_path], "default branch's tree")
    paths = [profile_path]
    if profile_path in on_main:
        values = main_values_path(main)
        if values is not None:
            paths.append(values)
    outcome, answer = ask_hemi()
    files_on_main = profile_path in on_main
    if outcome != "ok":
        if files_on_main:
            if set(paths) - set(tree_entries(head, paths, "pull request's tree")):
                raise Refusal(PR_SENTENCES["removed"].format(app=repository_name()))
            return "files", "", [PR_SENTENCES[f"{outcome}_files"].format(status=answer, runner=runner_name())]
        app, event = repository_name(), event_name()
        if outcome == "status":
            key = "status"
            if answer in (401, 403):
                key = "refused" if event == "pull_request" else "refused_event"
            raise Refusal(PR_SENTENCES[key].format(status=answer, app=app, event=event))
        raise Refusal(PR_SENTENCES[outcome].format(app=app, runner=runner_name()))
    copy = answer
    if copy["flags"]["source"] == "files":
        app, revision = files_answer(copy)
        if files_on_main:
            key = "files" if revision else "new"
            return "files", revision or "", [PR_SENTENCES[key].format(revision=revision, app=app)]
        if profile_path in tree_entries(head, [profile_path], "pull request's tree"):
            key = "restore" if revision else "new"
            return "files", revision or "", [PR_SENTENCES[key].format(revision=revision, app=app)]
        raise Refusal(PR_SENTENCES["returning" if revision else "new_nothing"].format(app=app))
    app, revision = copy["identity"]["app"], chart.revision_annotation(copy["identity"]["revision"])
    paths.append(copy["delivery"]["valuesFile"])
    on_main = tree_entries(main, paths, "default branch's tree")
    in_head = tree_entries(head, paths, "pull request's tree")
    ordered = list(dict.fromkeys(paths))
    added = [path for path in ordered if path in in_head and path not in on_main]
    if added:
        main_head = git(main, ["rev-parse", "HEAD"], "default branch's tree").decode("ascii", "replace").strip()
        if not files_on_main and first_parent(head, "pull request's tree") != main_head:
            raise Refusal(PR_SENTENCES["stale"].format(app=app))
        raise Refusal(" ".join(PR_SENTENCES["added_back"].format(path=path, app=app) for path in added))
    kept = [path for path in ordered if path in on_main and path in in_head]
    changed = [path for path in kept if in_head[path] != on_main[path]]
    if changed:
        raise Refusal(" ".join(PR_SENTENCES["changed"].format(path=path, app=app) for path in changed))
    derived = profile.from_contract(copy, profile.DERIVED_VALUES)
    write(out, {"contract.json": copy, "profile.json": derived, "values.json": copy["runtime"]})
    notes = [PR_SENTENCES["still_on_main"].format(path=path) for path in kept]
    return "hemi", revision, [PR_SENTENCES["hemi"].format(revision=revision), *notes]


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


def summarize_pr(source, revision, sentences):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "### wac-ci settings",
        "",
        "| source | revision |",
        "| --- | --- |",
        f"| {source or '-'} | {revision or '-'} |",
        "",
        *sentences,
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
    fetch_parser.add_argument("--wait-timeout", required=True)
    pr_parser = commands.add_parser("pr", help="ask hemi for a pull request's settings (wac-ci contract)")
    pr_parser.add_argument("--out", required=True)
    pr_parser.add_argument("--main", required=True, help="checkout of the default branch's head")
    pr_parser.add_argument("--head", required=True, help="checkout of the pull request's merge commit")
    return parser


def main_pr(args):
    out = Path(args.out)
    try:
        source, revision, sentences = pull_request(out, Path(args.main), Path(args.head))
    except Exception as error:
        sentence = str(error) if isinstance(error, Refusal) else PR_SENTENCES["unexpected"]
        try:
            clear(out)
        except OSError:
            pass
        summarize_pr(None, None, [sentence])
        print("wacd contract: " + sentence, file=sys.stderr)
        return 1
    print(f"source={source}\nrevision={revision}")
    summarize_pr(source, revision, sentences)
    return 0


def main(argv):
    args = build_parser().parse_args(argv[1:])
    if args.command == "pr":
        return main_pr(args)
    out = Path(args.out)
    try:
        source, revision, commit, reason = fetch(out, args.wait_timeout)
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
