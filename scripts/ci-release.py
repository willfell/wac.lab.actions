#!/usr/bin/env python3
"""TLS-verified client for Lab's repository-scoped GitHub OIDC release service."""

from __future__ import annotations

import argparse
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
from typing import Any

URL = "https://ci-deploy.ci-deploy.svc.cluster.local:8443/v1/release"
AUDIENCE = "wacwini:ci-deploy"
CA_FILE = "/etc/ci-deploy/ca.crt"


class ReleaseError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def fetch(
    request: urllib.request.Request, context: ssl.SSLContext, timeout: float
) -> tuple[int, dict[str, Any]]:
    opener = urllib.request.build_opener(
        NoRedirect(),
        urllib.request.HTTPSHandler(context=context),
        urllib.request.ProxyHandler({}),
    )
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except (OSError, urllib.error.URLError):
        raise ReleaseError(
            "release transport failed; check TLS trust and service readiness"
        ) from None
    with response:
        raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ReleaseError("response exceeds bound")
        if response.status != 200:
            return response.status, {}
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ReleaseError("invalid release response") from None
    if not isinstance(value, dict):
        raise ReleaseError("invalid release response")
    return response.status, value


def oidc_token() -> str:
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    bearer = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".actions.githubusercontent.com")
        or parsed.username
        or parsed.password
        or not bearer
    ):
        raise ReleaseError(
            "GitHub OIDC unavailable; the direct release job needs id-token: write"
        )
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k != "audience"]
    query.append(("audience", AUDIENCE))
    url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    status, value = fetch(
        urllib.request.Request(url, headers={"Authorization": "Bearer " + bearer}),
        ssl.create_default_context(),
        20,
    )
    token = value.get("value")
    if (
        status != 200
        or not isinstance(token, str)
        or not token.isascii()
        or not 1 <= len(token) <= 16384
    ):
        raise ReleaseError("GitHub did not return a usable OIDC token")
    return token


def request(
    body: dict[str, Any], trust: ssl.SSLContext, timeout: float
) -> tuple[int, dict[str, Any]]:
    token = oidc_token()
    return fetch(
        urllib.request.Request(
            URL,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
            method="POST",
        ),
        trust,
        timeout,
    )


def ready(
    value: dict[str, Any], pin: str, source: str, image: str, minimum: int
) -> bool:
    if value.get("pin_sha") != pin or value.get("source_sha") != source:
        raise ReleaseError("release response does not match this source and pin")
    if (
        value.get("sync", {}).get("revision") != pin
        or value.get("sync", {}).get("status") != "Synced"
        or value.get("health", {}).get("status") != "Healthy"
    ):
        return False
    deployments = value.get("deployments")
    if not isinstance(deployments, list) or len(deployments) != 1:
        raise ReleaseError("fixed deployment evidence is missing")
    deployment = deployments[0]
    status = deployment.get("status", {})
    replicas = deployment.get("replicas")
    generation = deployment.get("generation")
    if type(replicas) is not int or replicas < minimum or type(generation) is not int:
        return False
    return (
        status.get("observedGeneration", 0) >= generation
        and status.get("replicas") == replicas
        and status.get("updatedReplicas") == replicas
        and status.get("readyReplicas") == replicas
        and status.get("availableReplicas") == replicas
        and status.get("unavailableReplicas", 0) == 0
        and image in deployment.get("images", [])
    )


def migrated(value: dict[str, Any], source: str, tag: str, digest: str) -> None:
    expected = {
        "source_sha": source,
        "image": f"localhost:30500/warehouse:{tag}@{digest}",
        "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
    }
    if any(value.get(k) != v for k, v in expected.items()):
        raise ReleaseError("migration receipt belongs to another release attempt")
    jobs = value.get("jobs", [])
    if (
        value.get("succeeded") is not True
        or [j.get("target") for j in jobs] != ["warehouse", "ecosystem"]
        or any(j.get("succeeded") is not True or not j.get("uid") for j in jobs)
    ):
        raise ReleaseError("both fixed migrations must succeed before pin publication")


def run(args: argparse.Namespace) -> dict[str, Any]:
    source = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch("[0-9a-f]{40}", source):
        raise ReleaseError("source SHA is missing")
    if args.operation == "migrate":
        if not re.fullmatch(
            r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", args.image_tag
        ) or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_digest):
            raise ReleaseError("invalid migration image")
        body = {
            "operation": "migrate",
            "image_tag": args.image_tag,
            "image_digest": args.image_digest,
        }
    else:
        if not re.fullmatch("[0-9a-f]{40}", args.pin_sha) or not args.expected_image:
            raise ReleaseError("exact pin and expected image are required")
        body = {"operation": args.operation, "pin_sha": args.pin_sha}
    try:
        trust = ssl.create_default_context(cafile=CA_FILE)
    except (OSError, ssl.SSLError):
        raise ReleaseError(
            "runner TLS trust is missing or invalid; no insecure fallback"
        ) from None
    deadline = time.monotonic() + args.wait_timeout
    while time.monotonic() < deadline:
        code, value = request(body, trust, max(1, deadline - time.monotonic()))
        if code == 200:
            if args.operation == "migrate":
                migrated(value, source, args.image_tag, args.image_digest)
                return value
            if ready(
                value, args.pin_sha, source, args.expected_image, args.min_replicas
            ):
                return value
        elif code not in (409, 503):
            raise ReleaseError(f"release request rejected (HTTP {code})")
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    raise ReleaseError("release evidence deadline exceeded")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operation", choices=["status", "sync", "migrate"], required=True
    )
    parser.add_argument("--pin-sha", default="")
    parser.add_argument("--expected-image", default="")
    parser.add_argument("--image-tag", default="")
    parser.add_argument("--image-digest", default="")
    parser.add_argument("--min-replicas", type=int, default=1)
    parser.add_argument("--wait-timeout", type=int, default=2100)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if not 1 <= args.min_replicas <= 10 or not 1 <= args.wait_timeout <= 3600:
        parser.error("replica or wait bound exceeded")
    try:
        result = run(args)
        Path(args.receipt).write_text(json.dumps(result, indent=2) + "\n")
    except (ReleaseError, OSError, KeyError, TypeError, ValueError):
        print(
            "Release verification failed; check workflow identity, service readiness and fixed release evidence. No pin may be published after failed migration verification.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print("Verified release receipt: " + args.receipt)


if __name__ == "__main__":
    main()
