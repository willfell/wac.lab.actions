#!/usr/bin/env python3
"""Tag the running version's images with a settings release's version, copying no layer.

wac-cd's `retag` job runs this in `settings` mode, in place of `build`. A
settings-only release is a new version V' whose images are the ones the
running version V already runs: the pipeline and the gateway require every
image reference in the chart to carry the chart's version as its tag
(`localhost:30500/<name>:<V'>@<digest>`), and the gateway's ledger never
publishes a version twice, so the digests have to be reachable under V'.

`--images` is V's image map as `wacd-chart.py chart-images` prints it. For each
image, the manifest is read by digest from the push address with an Accept
header naming the OCI and Docker manifest and index media types, its bytes are
checked against the digest, and the same bytes are PUT under the tag V' with
the Content-Type the registry served. Layers and an index's child manifests
are already in the repository, so nothing else moves. A PUT of the same bytes
under the same tag changes nothing, so a re-run is harmless.

On success it prints `images=<json>` for $GITHUB_OUTPUT: the same map with
the tag V'. Any problem exits 1 with one "wacd retag: ..." line on stderr.

The registry is the push address, a constant (invariant 8): no argument or
environment variable moves it, and no proxy or redirect is followed. The
registry takes no credential, so no token is involved.
"""

import argparse
import hashlib
import http.client
import json
import re
import sys
import urllib.error
import urllib.request

PUSH_REGISTRY = "registry.network.svc.cluster.local:5000"
PULL_REGISTRY = "localhost:30500"
MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
MAX_BODY = 4 * 1024 * 1024
TIMEOUT = 30
IMAGE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
NUM = r"(0|[1-9][0-9]*)"
VERSION = re.compile(rf"{NUM}\.{NUM}\.{NUM}")


class RetagError(Exception):
    """The message is one line, safe to print."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))


def send(request):
    """(status, headers, body) for one request; a transport failure is a RetagError."""
    try:
        response = _OPENER.open(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:
        response = error
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as error:
        reason = getattr(error, "reason", error)
        raise RetagError(f"cannot reach the registry at {request.full_url}: {reason}") from None
    try:
        with response:
            status = response.code if isinstance(response, urllib.error.HTTPError) else response.status
            body = response.read(MAX_BODY + 1)
            headers = response.headers
    except (OSError, http.client.HTTPException, ValueError):
        raise RetagError(f"the registry dropped the answer for {request.full_url}") from None
    if len(body) > MAX_BODY:
        raise RetagError(f"the registry answer for {request.full_url} exceeds {MAX_BODY} bytes")
    return status, headers, body


def parse_images(text):
    try:
        images = json.loads(text)
    except ValueError:
        raise RetagError("--images is not valid JSON") from None
    if not isinstance(images, dict) or not images:
        raise RetagError("--images must be a non-empty JSON object keyed by image name")
    for name, spec in images.items():
        if (
            not IMAGE_NAME.fullmatch(name)
            or not isinstance(spec, dict)
            or spec.get("repository") != f"{PULL_REGISTRY}/{name}"
            or not isinstance(spec.get("digest"), str)
            or not DIGEST.fullmatch(spec["digest"])
        ):
            raise RetagError(f"--images entry {name[:64]!r} is not {PULL_REGISTRY}/<name> with a sha256 digest")
    return images


def retag_one(registry, name, digest, version):
    base = f"http://{registry}/v2/{name}/manifests"
    status, headers, body = send(
        urllib.request.Request(f"{base}/{digest}", headers={"Accept": ", ".join(MANIFEST_TYPES)})
    )
    if status != 200:
        raise RetagError(f"the registry answered HTTP {status} for {name}@{digest}")
    if "sha256:" + hashlib.sha256(body).hexdigest() != digest:
        raise RetagError(f"the manifest served for {name}@{digest} does not match its digest")
    content_type = headers.get("Content-Type", "")
    if content_type.split(";", 1)[0].strip() not in MANIFEST_TYPES:
        raise RetagError(f"{name}@{digest} is served as {content_type[:80]!r}, not an image manifest or index")
    status, headers, _ = send(
        urllib.request.Request(
            f"{base}/{version}", data=body, method="PUT", headers={"Content-Type": content_type}
        )
    )
    if status != 201:
        raise RetagError(f"the registry answered HTTP {status} to tagging {name}@{digest} as {version}")
    stored = headers.get("Docker-Content-Digest")
    if stored is not None and stored != digest:
        raise RetagError(f"the registry stored {name}:{version} as {stored[:80]}, not {digest}")


def retag(registry, images, version):
    """Tag every digest in images as version; returns the image map naming version."""
    if not VERSION.fullmatch(version):
        raise RetagError("--version must be X.Y.Z")
    out = {}
    for name in sorted(images):
        digest = images[name]["digest"]
        retag_one(registry, name, digest, version)
        out[name] = {"repository": f"{PULL_REGISTRY}/{name}", "tag": version, "digest": digest}
    return out


def build_parser():
    parser = argparse.ArgumentParser(prog="wacd-retag.py", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--images", required=True)
    parser.add_argument("--version", required=True)
    return parser


def main(argv):
    args = build_parser().parse_args(argv[1:])
    try:
        if not VERSION.fullmatch(args.version):
            raise RetagError("--version must be X.Y.Z")
        images = retag(PUSH_REGISTRY, parse_images(args.images), args.version)
    except RetagError as error:
        print("wacd retag: " + " ".join(str(error).split()), file=sys.stderr)
        return 1
    print("images=" + json.dumps(images, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
