#!/usr/bin/env python3
"""Release a published deploy chart through Lab's gateway and prove it is served.

wac-cd publishes the deploy chart, then asks the release gateway to release
that chart version and polls its status until Argo CD has synced the chart's
manifest digest, every Deployment has rolled out, and the Deployments run this
release's images. The release CLI first asks for status once and refuses to
release over a newer running X.Y.Z unless the mode is rollback. Every request
mints a fresh GitHub OIDC token through scripts/ci-release.py's oidc_token()
and goes to its URL with its CA file and NoRedirect; that file belongs to other
components and is loaded, never changed. The POST itself is post() here rather
than ci-release's request(), because ci-release's fetch() drops the body of
every answer but a 200, and a 403 from the gateway carries a refusal code
(reason). Only that code is kept, mapped through REFUSALS to a sentence (a code
not in the list gets UNKNOWN_REFUSAL, a 403 without one UNVERIFIED), and the
sentence is the error line and a job summary line. The body is never printed.
wacd-contract.py uses the same post() and map for the gateway's contract
operation.

`running --app A --wait-timeout N` is plan's gateway read in `settings` mode:
the version the app runs now, which is what a settings release re-releases, a
rolled-back version included. The status's `version` is the version the
gateway last pointed the Application at, not proof that it runs, so the read
is taken only from a steady status (steady() below): Synced at its
chart_digest and Healthy, every Deployment rolled out, and every running image
from the fleet's registry (localhost:30500/) one of that version's chart
images by repository and digest, with at least one; images from any other
registry are ignored. That chart is read from the push address (wacd-chart.py's constant), and
chart_digest must be its manifest digest. An unsteady status is read again
until N seconds pass, then the read exits ROLLING_OUT_EXIT (3) so plan can
report the rollout from its own step. A version that is not a bare X.Y.Z, a
chart_digest that is not the registry's, or a chart the registry lacks is a
fault and exits 1 at once. A 503 (the gateway cannot read Kubernetes) is
retried within N; a 403 without a reason is a refused status read, which an
app the gateway does not know and a token it could not check both give. It
prints running=, commit= (the chart's source), images= (its images),
copy_revision= (the copy revision it ships) and fleet_commit= (the wac.fleet
commit that copy was read at, or none).

`earlier-release --app A --prefix P --revision N --running V --versions L
--claimed X` is the settings plan's rule B over L, the versions on main above
V, newest first. A version counts as released only with evidence that it was proved running:
its GitHub Release, which wac-cd's github-release job creates only after
deploy proved the served build. A rollback creates no Release for the
version it leaves, so the evidence survives it. One that carries N and has a
Release exits ALREADY_RELEASED_EXIT (4) with ALREADY_RELEASED; one that
carries N and has none was never proved running, and is taken again when it is X (the claim
on the running code), as is an X with no chart yet. Copy revisions only grow
with versions, so the scan stops at the first chart with an older revision or
none; at most MAX_EARLIER charts are read, and running out of them fails
with a sentence. A Release that cannot be read (anything but 200 or 404)
fails, never counting as absent. It prints reuse=<X or empty>. Only the
candidates' Releases are read, never V's.

`unfinished-release --app A --prefix P --running V --versions L --codes K`
refuses the settings release behind an unfinished code release, before any
version is chosen. L is the versions on main above V, newest first, and K
those of them a code release claimed (wacd-version.py's above= and codes=). It
reads deploy/<app>:<version> at the push address for each, newest first. The
first present chart ends the scan: that version published, and a code release
below it can no longer be re-run anyway (a replay). An absent chart that K
names exits UNFINISHED_EXIT (5) with UNFINISHED: a settings version above it
would overtake that claim, and re-running the code release would then be
refused. An absent chart K does not name is a settings release's claim, which
the settings plan takes again, and is passed over. At most MAX_EARLIER charts
are read, and running out of them fails with a sentence. A chart that cannot
be read fails, never counting as absent. It prints unfinished=none.

All three settings reads take `--deadline <epoch seconds>`, the plan step's one
wall-clock deadline. The status wait ends DEADLINE_MARGIN seconds before it,
starts no request with under MIN_READ_SECONDS of that wait left, and bounds
each read by the time left, so an app that never settles reaches the rollout
refusal (exit 3) with its sentence, unless GitHub's token endpoint is slow on
the last status pass (ci.oidc_token() has its own fixed 20 s): then the
backstop ends the read and the run fails as a fault with the deadline
sentence, never with a wrong decision. A hard backstop (SIGALRM) at the
deadline itself stops a read that trickles past every socket timeout; each
command disarms it and restores the previous handler in a finally, so it
cannot fire once the command has decided.

The gateway contract (Plan 2, "Chart releases (wac-cd)") is isolated in
RELEASE_BODY, STATUS_BODY and ready(). After the proof, github-release creates
the GitHub Release for the tag with the job's GITHUB_TOKEN when none exists
yet, and never edits one that does. Any problem exits 1 with one stable
"wacd release: ..." stderr line, and no token is ever printed.
"""

import argparse
import errno
import http.client
import importlib.util
import json
import os
import re
import signal
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

_CHART_SPEC = importlib.util.spec_from_file_location(
    "wacd_release_chart", Path(__file__).with_name("wacd-chart.py")
)
chart = importlib.util.module_from_spec(_CHART_SPEC)
_CHART_SPEC.loader.exec_module(chart)

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
RELEASED_MODES = ("release", "resume", "rollback", "settings")
REASON_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
ROLLING_OUT_EXIT = 3
ALREADY_RELEASED_EXIT = 4
UNFINISHED_EXIT = 5
MAX_EARLIER = 25
DEADLINE_MARGIN = 15
MIN_READ_SECONDS = 2
ALREADY_RELEASED = (
    "Revision {revision} was released as {earlier}, and {running} runs now after a rollback. To put those "
    "settings on the running code, make them live again in hemi; they are released as a new revision."
)
UNFINISHED = (
    "{tag} was claimed by a code release that never published; re-run that release, or release a new commit, "
    "before releasing settings."
)
STATUS_REFUSED = (
    "The gateway refused the status read (HTTP 403 with no reason), so the running version is unknown "
    "and no settings were released."
)

# The gateway's closed list of refusal codes, each with the sentence a run
# summary shows. The contract operation can return the three fleet_copy_*
# codes and the two fleet_repository_* codes; the release check can return all
# nine (the fleet_copy_* ones for a copy missing, unreadable or naming another
# repository at the chart's own fleet commit). The two fleet_repository_* codes
# (wacwini/wac.lab#575) are faults on the gateway's side that a re-run cannot
# clear. One map serves wacd-contract.py and the release below, so every
# sentence holds for each place its code can come from; a coded refusal is
# never asked again.
REFUSALS = {
    "fleet_copy_missing": (
        "The gateway found no copy of this app's settings where one is required, so the "
        "run stopped."
    ),
    "fleet_copy_unreadable": (
        "The gateway could not read hemi's copy of this app's settings, so the run stopped."
    ),
    "fleet_copy_mismatch": (
        "hemi's copy of this app's settings names another app, so the run stopped."
    ),
    "fleet_commit_not_on_default_branch": (
        "The settings this release was planned with are no longer on the fleet copy's main line."
    ),
    "fleet_commit_older_than_last_release": (
        "The settings this release was planned with are older than the settings already released."
    ),
    "revision_annotation_missing": (
        "This chart does not carry both of the settings annotations, and the gateway requires "
        "them for this app."
    ),
    "revision_mismatch": (
        "This chart's settings revision is not the one the fleet copy holds now; plan again to "
        "release the current settings."
    ),
    "fleet_repository_unreachable": (
        "The gateway's GitHub App can no longer read wac.fleet; the owner must restore its access, "
        "so re-running will not help."
    ),
    "fleet_repository_mismatch": (
        "The gateway's pinned wac.fleet no longer names wacwini/wac.fleet; the gateway needs a change, "
        "so re-running will not help."
    ),
}
UNKNOWN_REFUSAL = "The gateway refused this request for a reason this wac-cd does not know"
UNVERIFIED = "The gateway did not accept this run as a release of a registered app."


class Unreachable(ReleaseError):
    """The gateway could not be reached or dropped the answer."""


class Unanswered(Unreachable):
    """Nothing answered the connection before any response (no_connection()).

    The release path treats it as any Unreachable (same message, fails at
    once). Only wacd-contract.py's plan call tells it apart, to ride out the
    gateway's Recreate rollout, which leaves a short gap with no endpoint.
    """


NO_ROUTE = (errno.EHOSTUNREACH, errno.ENETUNREACH)


def no_connection(error):
    """Whether a transport error came before any response: nothing answered.

    That is a refused or reset connection, one closed during the TLS handshake
    (ssl.SSLEOFError) or before the status line (http.client's
    RemoteDisconnected, a ConnectionResetError), a connect timeout (urllib
    wraps only the connect in URLError, so a read timeout is not one), and no
    route to the host or network. A certificate that fails verification is an
    answer of a kind and is never counted, and nor is anything else.
    """
    wrapped = isinstance(error, urllib.error.URLError) and not isinstance(error, urllib.error.HTTPError)
    if wrapped:
        error = error.reason
    if isinstance(error, ssl.SSLCertVerificationError):
        return False
    if wrapped and isinstance(error, TimeoutError):
        return True
    if isinstance(error, (ConnectionRefusedError, ConnectionResetError, ssl.SSLEOFError)):
        return True
    return isinstance(error, OSError) and error.errno in NO_ROUTE


class RollingOut(ReleaseError):
    """The status never settled within the wait: the app is mid-rollout."""


class AlreadyReleased(ReleaseError):
    """Rule B: an earlier version above the running one ran with this revision."""


class Unfinished(ReleaseError):
    """A code release above the running version claimed its tag and never published its chart."""


class Deadline(ReleaseError):
    """The settings plan's wall-clock deadline passed during a read."""


class Refused(ReleaseError):
    """The gateway answered 403; the message ends with the refusal's sentence."""

    def __init__(self, what, sentence):
        super().__init__(f"{what} refused (HTTP 403): {sentence}")
        self.sentence = sentence


def refusal_sentence(value):
    """The sentence for a 403 whose body carried value; value never reaches output."""
    reason = value.get("reason") if isinstance(value, dict) else None
    if reason is None:
        return UNVERIFIED
    if isinstance(reason, str) and reason in REFUSALS:
        return REFUSALS[reason]
    if isinstance(reason, str) and REASON_PATTERN.fullmatch(reason):
        return f"{UNKNOWN_REFUSAL} (code {reason})."
    return UNKNOWN_REFUSAL + "."


def rejected(what, code, value):
    if code == 403:
        return Refused(what, refusal_sentence(value))
    return ReleaseError(f"{what} rejected (HTTP {code})")


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


def post(body, trust, timeout):
    """One POST to the gateway with a token minted for it: (status, JSON or None, token).

    ci.oidc_token() raises ReleaseError when no token can be had; a transport
    failure raises Unreachable. Whatever the status, the parsed body is
    returned to the caller, which keeps only what it needs from it.
    """
    token = ci.oidc_token()
    request = urllib.request.Request(
        ci.URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(
        ci.NoRedirect(),
        urllib.request.HTTPSHandler(context=trust),
        urllib.request.ProxyHandler({}),
    )
    unreachable = "release transport failed; check TLS trust and service readiness"
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as error:
        raise (Unanswered if no_connection(error) else Unreachable)(unreachable) from None
    try:
        with response:
            status = response.code if isinstance(response, urllib.error.HTTPError) else response.status
            raw = response.read(MAX_BODY + 1)
    except (OSError, http.client.HTTPException, ValueError):
        raise Unreachable(unreachable) from None
    value = None
    if len(raw) <= MAX_BODY:
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            value = None
    return status, value, token


def transport(body, trust, timeout):
    """(status, value) as ci-release's request() returns it, but a 403 keeps its reason."""
    status, value, _ = post(body, trust, timeout)
    if status == 200:
        if not isinstance(value, dict):
            raise ReleaseError("invalid release response")
        return status, value
    if status == 403 and isinstance(value, dict) and "reason" in value:
        return status, {"reason": value["reason"]}
    return status, {}


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
            raise rejected("status pre-check", code, value)

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
            raise rejected("release request", code, value)
    while digest is not None and time.monotonic() < deadline:
        code, value = send(STATUS_BODY())
        if code == 200:
            if ready(value, version, digest, images):
                return value, record
        elif code != 503:
            raise rejected("release request", code, value)
        wait()
    raise ReleaseError("release evidence deadline exceeded")


def read_release(app, version, timeout=chart.TIMEOUT):
    """deploy/<app>:<version> as the registry holds it, at the push address."""
    try:
        return chart.chart_release(chart.PUSH_REGISTRY, app, version, timeout)
    except chart.ChartError as error:
        raise ReleaseError(str(error)) from None


def read_copy(app, version):
    """The copy revision deploy/<app>:<version> ships: "absent", "none" or the decimal."""
    try:
        return chart.chart_copy_revision(chart.PUSH_REGISTRY, app, version)
    except chart.ChartError as error:
        raise ReleaseError(str(error)) from None


def github_context(environ):
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
    return api, repository, token


def release_exists(tag):
    """Whether tag has a GitHub Release: 200 yes, 404 no, anything else a failure."""
    if not TAG_PATTERN.fullmatch(tag):
        raise ReleaseError("the tag is not a valid tag name")
    api, repository, token = github_context(os.environ)
    url = f"{api}/repos/{repository}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    code = github_call("GET", url, token)
    if code == 200:
        return True
    if code == 404:
        return False
    raise ReleaseError(
        f"GitHub answered HTTP {code} for the Release of {tag}, so whether it ran is unknown"
    )


def earlier_release(revision, running, versions, claimed, prefix, app):
    """Rule B over versions (newest first); returns the version to take again, or ""."""
    wanted = int(revision)
    reuse = ""
    for checked, earlier in enumerate(versions):
        if checked == MAX_EARLIER:
            raise ReleaseError(
                f"{MAX_EARLIER} versions above {running} were read and none settled whether revision "
                f"{revision} already ran, so the settings plan stops here and nothing was tagged"
            )
        copy = read_copy(app, earlier)
        if copy == "absent":
            reuse = earlier if earlier == claimed else reuse
            continue
        if copy == "none" or int(copy) < wanted:
            break
        if int(copy) != wanted:
            continue
        if release_exists(prefix + earlier):
            raise AlreadyReleased(ALREADY_RELEASED.format(revision=revision, earlier=earlier, running=running))
        reuse = earlier if earlier == claimed else reuse
    return reuse


def chart_published(app, version, timeout):
    """Whether deploy/<app>:<version> is in the registry, read at the push address."""
    try:
        return chart.chart_config(chart.PUSH_REGISTRY, app, version, timeout) is not None
    except chart.ChartError as error:
        raise ReleaseError(str(error)) from None


def unfinished_release(running, versions, codes, prefix, app, deadline):
    """Refuse while the newest code release above running (versions newest first) never published."""
    for checked, version in enumerate(versions):
        if checked == MAX_EARLIER:
            raise ReleaseError(
                f"{MAX_EARLIER} versions above {running} were read and none was published, so whether a code "
                "release is unfinished is unknown; the settings plan stops here and nothing was tagged"
            )
        if chart_published(app, version, max(1, min(chart.TIMEOUT, deadline - time.time()))):
            return
        if version in codes:
            raise Unfinished(UNFINISHED.format(tag=prefix + version))


def expire(signum, frame):
    raise Deadline("a read did not finish within the settings plan's deadline, so nothing was tagged")


def arm_deadline(deadline):
    """Arm the hard backstop at the wall-clock deadline: (seconds left, previous handler).

    It is the last resort for a read that trickles past every socket timeout.
    The caller decides before it (the status wait ends DEADLINE_MARGIN seconds
    earlier) and always calls disarm() in a finally, so the alarm cannot fire
    once a command has decided.
    """
    left = deadline - time.time()
    if left <= 0:
        raise Deadline("the settings plan's deadline had passed before this read")
    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, left)
    return left, previous


def disarm(previous):
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, previous)


def image_digest(ref):
    return ref.split("@", 1)[1] if "@" in ref else None


def steady(value, release):
    """Whether a status proves release runs: synced, healthy, rolled out, and only its fleet images.

    Only images from the fleet's own registry (localhost:30500/) are held to the
    release's references; another registry's image, such as a third-party
    sidecar, is not the release's to prove and is ignored.
    """
    sync = value.get("sync")
    health = value.get("health")
    if (
        not isinstance(sync, dict)
        or sync.get("status") != "Synced"
        or sync.get("revision") != value.get("chart_digest")
        or not isinstance(health, dict)
        or health.get("status") != "Healthy"
    ):
        return False
    deployments = value.get("deployments")
    if not isinstance(deployments, list) or not deployments:
        return False
    wanted = {(spec["repository"], spec["digest"]) for spec in release["images"].values()}
    seen = 0
    for deployment in deployments:
        if not isinstance(deployment, dict) or not rolled_out(deployment):
            return False
        refs = deployment.get("images")
        if not isinstance(refs, list):
            return False
        for ref in refs:
            if not isinstance(ref, str):
                return False
            if not ref.startswith(PULL_REGISTRY + "/"):
                continue
            if (image_repository(ref), image_digest(ref)) not in wanted:
                return False
            seen += 1
    return seen > 0


def running_release(app, wait_timeout, request=None):
    """(version, release) from the first steady status within wait_timeout seconds."""
    if request is None:
        request = transport
        trust = trust_context()
    else:
        trust = None
    deadline = time.monotonic() + wait_timeout
    seen = None
    while deadline - time.monotonic() >= MIN_READ_SECONDS:
        code, value = request(STATUS_BODY(), trust, deadline - time.monotonic())
        if code == 200:
            version = value.get("version")
            if semver(version) is None:
                shown = version[:40] if isinstance(version, str) else type(version).__name__
                raise ReleaseError(
                    f"the gateway reports {shown!r} running, which is not a released X.Y.Z (an unknown "
                    "chart digest or a lost ledger), so there is no running code to release settings on"
                )
            release = read_release(app, version, max(1, min(chart.TIMEOUT, deadline - time.monotonic())))
            if release is None:
                raise ReleaseError(f"deploy/{app}:{version}, which the gateway reports running, is not in the registry")
            if value.get("chart_digest") != release["digest"]:
                raise ReleaseError(
                    f"the gateway's chart for {version} is not the chart the registry holds as "
                    f"deploy/{app}:{version}"
                )
            if steady(value, release):
                return version, release
            seen = version
        elif code == 403 and value.get("reason") is None:
            raise ReleaseError(STATUS_REFUSED)
        elif code != 503:
            raise rejected("status request", code, value)
        time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
    if seen is not None:
        raise RollingOut(
            f"{app} was rolling out {seen} for the whole {int(wait_timeout)} seconds: the gateway never "
            "reported it synced, healthy and fully rolled out with only its own images, so no settings "
            "were released; dispatch again once it settles"
        )
    raise ReleaseError("status deadline exceeded; the gateway could not read the cluster")


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
    api, repository, token = github_context(environ)
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
    running = commands.add_parser("running")
    running.add_argument("--app", required=True)
    running.add_argument("--wait-timeout", required=True)
    running.add_argument("--deadline", type=float)
    earlier = commands.add_parser("earlier-release")
    for name in ("--app", "--prefix", "--revision", "--running", "--versions", "--claimed"):
        earlier.add_argument(name, required=True)
    earlier.add_argument("--deadline", type=float)
    unfinished = commands.add_parser("unfinished-release")
    for name in ("--app", "--prefix", "--running", "--versions", "--codes"):
        unfinished.add_argument(name, required=True)
    unfinished.add_argument("--deadline", type=float, required=True)
    gh = commands.add_parser("github-release")
    gh.add_argument("--tag", required=True)
    gh.add_argument("--sha", required=True)
    return parser


def run(args):
    if args.command == "github-release":
        return github_release(args.tag, args.sha, os.environ)
    if args.command == "unfinished-release":
        versions = [v for v in args.versions.split(",") if v]
        codes = [v for v in args.codes.split(",") if v]
        if any(not VERSION_PATTERN.fullmatch(v) for v in versions + codes):
            raise ReleaseError("--versions and --codes must be X.Y.Z")
        if not set(codes) <= set(versions):
            raise ReleaseError("--codes must name only versions in --versions")
        _, previous = arm_deadline(args.deadline)
        try:
            unfinished_release(args.running, versions, set(codes), args.prefix, args.app, args.deadline)
        finally:
            disarm(previous)
        return "unfinished=none"
    if args.command == "earlier-release":
        if not chart.DECIMAL.fullmatch(args.revision):
            raise ReleaseError("--revision must be a decimal")
        versions = [v for v in args.versions.split(",") if v]
        if any(not VERSION_PATTERN.fullmatch(v) for v in versions + ([args.claimed] if args.claimed else [])):
            raise ReleaseError("--versions and --claimed must be X.Y.Z")
        if args.deadline is None:
            return "reuse=" + earlier_release(
                args.revision, args.running, versions, args.claimed, args.prefix, args.app
            )
        _, previous = arm_deadline(args.deadline)
        try:
            return "reuse=" + earlier_release(
                args.revision, args.running, versions, args.claimed, args.prefix, args.app
            )
        finally:
            disarm(previous)
    timeout = wait_bound(args.wait_timeout)
    if args.command == "running":
        previous = None
        if args.deadline is not None:
            left, previous = arm_deadline(args.deadline)
            timeout = min(timeout, left - DEADLINE_MARGIN)
        try:
            if timeout < MIN_READ_SECONDS:
                raise Deadline("too little of the settings plan's deadline was left to read the status")
            version, release = running_release(args.app, timeout)
        finally:
            if previous is not None:
                disarm(previous)
        images = json.dumps(release["images"], separators=(",", ":"), sort_keys=True)
        return (
            f"running={version}\ncommit={release['revision']}\nimages={images}\n"
            f"copy_revision={release['copy']}\nfleet_commit={release['fleet_commit']}"
        )
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


def summarize_refusal(sentence):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"\nThe gateway refused the release: {sentence}\n")
    except OSError:
        pass


def main(argv):
    try:
        line = run(build_parser().parse_args(argv[1:]))
    except ReleaseError as err:
        if isinstance(err, Refused):
            summarize_refusal(err.sentence)
        print("wacd release: " + " ".join(str(err).split()), file=sys.stderr)
        if isinstance(err, RollingOut):
            return ROLLING_OUT_EXIT
        if isinstance(err, Unfinished):
            return UNFINISHED_EXIT
        return ALREADY_RELEASED_EXIT if isinstance(err, AlreadyReleased) else 1
    except Exception:
        print("wacd release: unexpected failure", file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
