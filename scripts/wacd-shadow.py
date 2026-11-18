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
request bearer nor a minted token is ever printed.

ci-release.py mints its token the same way. It is not imported because wac.lab
loads that file by path and its names are pinned; this script keeps its own.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

URL = "http://hemi-app.hemi.svc.cluster.local:3000"
AUDIENCE = "wacwini:hemi"
TOKEN_SCHEME = "https"
TOKEN_HOST_SUFFIX = ".actions.githubusercontent.com"
TIMEOUT = 10
MAX_RESPONSE = 1048576
SILENT = "hemi did not answer; nothing was compared."


class Unanswered(Exception):
    """Nothing was compared; the message is a reason safe to print."""


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


def one_line(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def compare(profile_path: str) -> list[str]:
    path = Path(profile_path)
    text = path.read_text()
    profile = json.loads(text)
    values = (path.parent.parent / profile["values_file"]).read_text()
    identity = call("GET", "/api/v1/contract").get("identity")
    revision = identity.get("revision") if isinstance(identity, dict) else None
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise Unanswered("hemi's contract carries no copy number")
    answer = call(
        "POST", "/api/v1/shadow", {"revision": revision, "profile": profile, "values": values}
    )
    equal, differences = answer.get("equal"), answer.get("differences")
    if (
        not isinstance(equal, bool)
        or not isinstance(differences, list)
        or equal != (not differences)
        or not all(isinstance(d, dict) and isinstance(d.get("path"), str) for d in differences)
    ):
        raise Unanswered("hemi's comparison is not in the expected shape")
    if equal:
        return [f"hemi's copy matched the files (copy {revision})."]
    lines = [
        f"hemi's copy differs from the files in {len(differences)} places (copy {revision})."
    ]
    for difference in differences:
        where = " ".join(difference["path"].split())
        lines.append(
            f"- {where}: hemi's copy has {one_line(difference.get('contract'))}, "
            f"the files have {one_line(difference.get('files'))}"
        )
    return lines


def main(argv: list[str]) -> int:
    try:
        if len(argv) != 2:
            raise Unanswered("usage: wacd-shadow.py <path to .github/wacd.json>")
        lines = compare(argv[1])
    except Unanswered as reason:
        print(f"wacd shadow: {reason}")
        lines = [SILENT]
    except Exception as error:
        print(f"wacd shadow: stopped on {type(error).__name__}")
        lines = [SILENT]
    try:
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
