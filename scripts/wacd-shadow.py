#!/usr/bin/env python3
"""Ask hemi whether its stored copy of an app's configuration matches the files.

wac-cd's `shadow` job runs this with the path to `.github/wacd.json`. It reads
the profile and the values file the profile names, mints a GitHub OIDC job
token (audience wacwini:hemi) for each request, reads hemi's contract to learn
the copy number, and posts the files for hemi to compare. The result is one
line in the job summary, followed by one line per difference.

This is observation only. The files remain the truth and nothing acts on the
answer, so every path exits 0: a hemi outage, a refused token, a malformed
answer or a bug here never fails or delays a release. Neither the runner's
request bearer nor a minted token is ever printed: every token minted is kept,
and any occurrence of one in an output line, including one hemi echoed back in
a difference, is replaced with [token] before the line is written.

Files compared as text differ from Helm's reading of them (YAML 1.1 reads
`yes` as true, `010` as 8 and a key written `y` as "true"), and Helm is what
the chart actually sees. So when Helm is installed the script also renders
a fixture chart whose only template is `{{ toJson .Values }}` against the values
file and posts the result as `helmValues`. That chart ships no values.yaml: with
chart defaults a user's explicit null removes the key, without them Helm keeps
every null where it was written. If Helm is absent, fails, hangs or prints
anything but a JSON object, the files are posted without `helmValues` and the
summary says so.

Only files inside the repository checkout are read: a path that is, or passes
through, a symbolic link, that is not a regular file, or that leaves the working
directory is refused, and so is a file over MAX_RESPONSE bytes. A refusal skips
the whole comparison with its own summary line and posts nothing.

Time budget. The job is bounded at 120 seconds and the summary must be written
inside it, so the worst case is added up and kept under it:

    checkouts and setup-python (assumed)             30
    install step, its timeout-minutes: 1             60
    helm version (HELM_VERSION_TIMEOUT)               5
    helm template (HELM_TIMEOUT)                     10
    four requests at TIMEOUT 2 (two token mints,
      the contract read and the comparison)           8
    total                                           113

That leaves 7 seconds. install-helm.sh bounds its one download at 25 seconds,
so the 60 for the install step is a backstop, not the usual worst case. The
sum is checked against the workflow in .github/scripts/test_reusable_workflows.py.

ci-release.py mints its token the same way. It is not imported because wac.lab
loads that file by path and its names are pinned; this script keeps its own.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

URL = "http://hemi-app.hemi.svc.cluster.local:3000"
AUDIENCE = "wacwini:hemi"
TOKEN_SCHEME = "https"
TOKEN_HOST_SUFFIX = ".actions.githubusercontent.com"
TIMEOUT = 2
HELM_TIMEOUT = 10
HELM_VERSION_TIMEOUT = 5
HELM_VERSION = "v3.16.3"
PROBE_CHART = Path(__file__).resolve().parent / "fixtures" / "values-probe"
MAX_RESPONSE = 1048576  # 1 MiB: also the cap on each file read and on Helm's output
SILENT = "hemi did not answer; nothing was compared."
HELM_UNREAD = "Helm could not read the values; hemi compared its own reading."
REDACTED = "[token]"
MINTED: list[str] = []


class Unanswered(Exception):
    """Nothing was compared; the message is a reason safe to print."""


class Skipped(Exception):
    """A file was refused; the message is the whole summary line, safe to print."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def fetch(request: urllib.request.Request, what: str) -> dict[str, Any]:
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:
        error.close()
        raise Unanswered(f"{what} answered HTTP {error.code}") from None
    except (OSError, ValueError, http.client.HTTPException):
        raise Unanswered(f"{what} could not be reached") from None
    try:
        with response:
            raw = response.read(MAX_RESPONSE + 1)
    except (OSError, ValueError, http.client.HTTPException):
        raise Unanswered(f"{what} dropped the connection") from None
    if response.status != 200:
        raise Unanswered(f"{what} answered HTTP {response.status}")
    if len(raw) > MAX_RESPONSE:
        raise Unanswered(f"{what} answered more than {MAX_RESPONSE} bytes")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise Unanswered(f"{what} did not answer JSON") from None
    if not isinstance(value, dict):
        raise Unanswered(f"{what} did not answer a JSON object")
    return value


def oidc_token() -> str:
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    bearer = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != TOKEN_SCHEME
        or not parsed.hostname
        or not parsed.hostname.endswith(TOKEN_HOST_SUFFIX)
        or parsed.username
        or parsed.password
        or not bearer
    ):
        raise Unanswered("GitHub OIDC is unavailable; the job needs id-token: write")
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k != "audience"]
    query.append(("audience", AUDIENCE))
    url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    value = fetch(
        urllib.request.Request(url, headers={"Authorization": "Bearer " + bearer}),
        "the GitHub token endpoint",
    )
    token = value.get("value")
    if not isinstance(token, str) or not token.isascii() or not 1 <= len(token) <= 16384:
        raise Unanswered("GitHub did not return a usable OIDC token")
    MINTED.append(token)
    return token


def call(method: str, route: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Authorization": "Bearer " + oidc_token()}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    return fetch(
        urllib.request.Request(URL + route, data=data, headers=headers, method=method),
        f"hemi's {route}",
    )


def redact(text: str) -> str:
    for token in MINTED:
        for form in (token, json.dumps(token)[1:-1]):
            text = text.replace(form, REDACTED)
    return text


def one_line(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def repo_file(path: Path, what: str) -> tuple[Path, str]:
    """The absolute path and text of a regular file inside the working directory.

    Refuses a path that leaves the checkout, passes through a symbolic link or
    is not a regular file, and a file over MAX_RESPONSE bytes, so the checkout
    cannot make this script read /proc/self/environ or post a huge body.
    """
    root = Path.cwd().resolve()
    absolute = Path(os.path.abspath(path))
    try:
        relative = absolute.relative_to(root)
    except ValueError:
        raise Skipped(f"{what} lies outside the repository checkout; nothing was compared.") from None
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise Skipped(f"{what} is a symbolic link or sits behind one; nothing was compared.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(absolute, flags)
    except OSError:
        raise Unanswered(f"{what} could not be read") from None
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise Skipped(f"{what} is not a regular file; nothing was compared.")
        data = handle.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise Skipped(f"{what} is larger than {MAX_RESPONSE // 1048576} MiB; nothing was compared.")
    return absolute, data.decode("utf-8")


def run_bounded(argv: list[str], env: dict[str, str], timeout: float) -> bytes | None:
    """Standard output of a command that exits 0 in time and within MAX_RESPONSE, else None.

    The command gets no standard input and its error output is discarded. It is
    killed, with anything it started, on timeout or when it prints too much.
    """
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, start_new_session=True,
        )
    except OSError:
        return None
    box: list[bytes | None] = []

    def drain() -> None:
        try:
            box.append(process.stdout.read(MAX_RESPONSE + 1))
        except (OSError, ValueError):
            box.append(None)

    reader = threading.Thread(target=drain, daemon=True)
    deadline = time.monotonic() + timeout
    reader.start()
    try:
        reader.join(timeout)
        out = box[0] if box else None
        if reader.is_alive() or out is None or len(out) > MAX_RESPONSE:
            return None
        process.wait(timeout=max(deadline - time.monotonic(), 0.1))
        return out if process.returncode == 0 else None
    except subprocess.TimeoutExpired:
        return None
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.wait()
        reader.join(1)
        try:
            process.stdout.close()
        except (OSError, ValueError):
            pass


def helm_values(values_file: Path) -> dict[str, Any] | None:
    """Helm's own reading of the values file as JSON, or None when it cannot be had.

    values_file is the absolute path repo_file returned, so a repository-chosen
    name can neither be "-" (standard input) nor reach a getter or plugin as a URL.
    Only the pinned Helm counts: another version (a runner image's own, when the
    install step failed) reads YAML differently and would be posted as Helm's.
    Helm gets a bare environment: no cluster (KUBECONFIG=/dev/null), none of the
    job's token variables, and plugin, cache, config and data directories that
    are empty and thrown away.
    """
    helm = shutil.which("helm")
    if helm is None:
        return None
    try:
        scratch = tempfile.TemporaryDirectory()
    except OSError:
        return None
    with scratch:
        env = {"KUBECONFIG": os.devnull}
        for name in ("PATH", "HOME", "TMPDIR"):
            if name in os.environ:
                env[name] = os.environ[name]
        for name in ("PLUGINS", "CACHE_HOME", "CONFIG_HOME", "DATA_HOME"):
            directory = Path(scratch.name) / name.lower()
            directory.mkdir()
            env["HELM_" + name] = str(directory)
        version = run_bounded([helm, "version", "--template", "{{.Version}}"], env, HELM_VERSION_TIMEOUT)
        if version is None or version.decode("utf-8", "replace").strip() != HELM_VERSION:
            return None
        out = run_bounded(
            [helm, "template", "probe", str(PROBE_CHART), "-f", str(values_file),
             "--show-only", "templates/values.json"],
            env, HELM_TIMEOUT,
        )
    if out is None:
        return None
    try:
        text = out.decode("utf-8")
        value = json.loads(text[text.index("{"):])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def compare(profile_path: str) -> list[str]:
    path = Path(profile_path)
    _, text = repo_file(path, ".github/wacd.json")
    profile = json.loads(text)
    values_file, values = repo_file(path.parent.parent / profile["values_file"], "the values file")
    identity = call("GET", "/api/v1/contract").get("identity")
    revision = identity.get("revision") if isinstance(identity, dict) else None
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise Unanswered("hemi's contract carries no copy number")
    body: dict[str, Any] = {"revision": revision, "profile": profile, "values": values}
    helm = helm_values(values_file)
    if helm is not None:
        body["helmValues"] = helm
    answer = call("POST", "/api/v1/shadow", body)
    equal, differences = answer.get("equal"), answer.get("differences")
    if (
        not isinstance(equal, bool)
        or not isinstance(differences, list)
        or equal != (not differences)
        or not all(isinstance(d, dict) and isinstance(d.get("path"), str) for d in differences)
    ):
        raise Unanswered("hemi's comparison is not in the expected shape")
    unread = [] if helm is not None else [HELM_UNREAD]
    if equal:
        return [f"hemi's copy matched the files (copy {revision}).", *unread]
    places = "1 place" if len(differences) == 1 else f"{len(differences)} places"
    lines = [f"hemi's copy differs from the files in {places} (copy {revision})."]
    for difference in differences:
        where = " ".join(difference["path"].split())
        lines.append(
            f"- {where}: hemi's copy has {one_line(difference.get('contract'))}, "
            f"the files have {one_line(difference.get('files'))}"
        )
    return lines + unread


def main(argv: list[str]) -> int:
    MINTED.clear()
    try:
        if len(argv) != 2:
            raise Unanswered("usage: wacd-shadow.py <path to .github/wacd.json>")
        lines = compare(argv[1])
    except Skipped as reason:
        print(redact(f"wacd shadow: {reason}"))
        lines = [str(reason)]
    except Unanswered as reason:
        print(redact(f"wacd shadow: {reason}"))
        lines = [SILENT]
    except Exception as error:
        print(redact(f"wacd shadow: stopped on {type(error).__name__}"))
        lines = [SILENT]
    try:
        lines = [redact(line) for line in lines]
        print("\n".join(lines))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
