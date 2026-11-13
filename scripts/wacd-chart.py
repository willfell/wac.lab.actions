#!/usr/bin/env python3
"""Package the per-app deploy chart for wac-cd and read registry state back.

The deploy chart is the app's values nested under the vendored wac-app chart.
It is pushed to registry repo deploy/<app> at tag <version>, and the release
gateway reads it back from the Helm config blob (the Chart.yaml as JSON), so the
revision and image annotations written here are the contract.

Every CLI prints single-line key=value output for $GITHUB_OUTPUT. Any problem
exits 1 with a one-line "wacd chart: ..." message.
"""

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEPLOY_REPO = "deploy"
CHARTS_REPO = "charts"
PUSH_REGISTRY = "registry.network.svc.cluster.local:5000"
PULL_REGISTRY = "localhost:30500"
REVISION_ANNOTATION = "org.opencontainers.image.revision"
IMAGES_ANNOTATION = "wacwini.com/images"
HELM_CONFIG_TYPE = "application/vnd.cncf.helm.config.v1+json"
MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    )
)
MAX_BODY = 4 * 1024 * 1024
TIMEOUT = 30.0
OWNED_KEYS = ("app", "images", "global")

APP = re.compile(r"[a-z][a-z0-9-]{0,39}")
IMAGE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?")
SHA40 = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REGISTRY = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?")
REF = re.compile(
    re.escape(PULL_REGISTRY + "/") + r"(?P<name>[a-z0-9][a-z0-9._-]{0,63}):"
    r"(?P<tag>[A-Za-z0-9_][A-Za-z0-9._-]{0,127})@(?P<digest>sha256:[0-9a-f]{64})"
)
OWNED_TOP_LEVEL = re.compile(
    r"""^(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s#'"\-\[\]{},&*!|>%@`?:][^:#]*?))\s*:(\s|$)"""
)


class ChartError(Exception):
    """A packaging or registry problem; the message is one line."""


def check(value, pattern, what):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ChartError(f"{what} is invalid: {str(value)[:80]!r}")
    return value


def deploy_values(app_values, app, images):
    """The values.yaml document: the app's values nested under the wac-app subchart."""
    if not isinstance(app_values, dict):
        raise ChartError("the values file must be a mapping")
    for key in OWNED_KEYS:
        if key in app_values:
            raise ChartError(f"the values file may not set {key!r}; wac-cd owns it")
    return {"wac-app": {**app_values, "app": app, "images": images}}


def image_refs(images):
    """Space-separated repository:tag@digest refs, sorted by image name."""
    refs = []
    for name in sorted(images):
        image = images[name]
        digest = image.get("digest")
        if not digest:
            raise ChartError(f"image {name!r} has no digest")
        refs.append(f"{image['repository']}:{image['tag']}@{digest}")
    return " ".join(refs)


def parse_image_refs(text):
    """Inverse of image_refs; every ref must be localhost:30500/<name>:<tag>@sha256:<hex>."""
    images = {}
    for ref in text.split():
        match = REF.fullmatch(ref)
        if not match:
            raise ChartError(f"image ref {ref[:100]!r} is not {PULL_REGISTRY}/<name>:<tag>@sha256:<hex>")
        name = match["name"]
        images[name] = {
            "repository": f"{PULL_REGISTRY}/{name}",
            "tag": match["tag"],
            "digest": match["digest"],
        }
    return images


def chart_metadata(app, version, chart_version, source_sha, images):
    """Chart.yaml content; images is None only for the wac-ci render."""
    annotations = {REVISION_ANNOTATION: source_sha}
    if images is not None:
        annotations[IMAGES_ANNOTATION] = image_refs(images)
    return {
        "apiVersion": "v2",
        "name": app,
        "version": version,
        "appVersion": version,
        "type": "application",
        "annotations": annotations,
        "dependencies": [
            {
                "name": "wac-app",
                "version": chart_version,
                "repository": f"oci://{PUSH_REGISTRY}/{CHARTS_REPO}",
            }
        ],
    }


def ci_images(profile):
    """The images map wac-ci renders with: no digests, tag ci."""
    return {
        image["name"]: {"repository": f"{PULL_REGISTRY}/{image['name']}", "tag": "ci"}
        for image in profile["images"]
    }


def nest_values_text(text, app, images):
    """Nest a block-YAML values file under wac-app without a YAML parser.

    Every line moves two spaces right, then app and images are appended at the
    new top level. Owned keys are refused by a column-0 scan.
    """
    lines = text.splitlines()
    if lines and lines[0].rstrip() == "---":
        lines = lines[1:]
    first = None
    for line in lines:
        if line.strip() and not line.lstrip().startswith("#"):
            first = line
            break
    if first is not None:
        if first[0] in " \t":
            raise ChartError("the values file must be a mapping starting at column 0")
        if first.startswith(("- ", "[")) or first.rstrip() == "-":
            raise ChartError("the values file must be a mapping")
        if first.startswith("{"):
            raise ChartError("the values file must be block YAML or a JSON object")
    for line in lines:
        if line.rstrip() in ("---", "...") or line.startswith(("--- ", "%")):
            raise ChartError("the values file must be a single YAML document")
        found = OWNED_TOP_LEVEL.match(line)
        if found:
            key = found["dq"] or found["sq"] or found["bare"]
            if key.strip() in OWNED_KEYS:
                raise ChartError(f"the values file may not set {key.strip()!r}; wac-cd owns it")
    nested = ["wac-app:"] + [f"  {line}" if line.strip() else "" for line in lines]
    nested.append(f"  app: {json.dumps(app)}")
    nested.append(f"  images: {json.dumps(images, sort_keys=True)}")
    return "\n".join(nested) + "\n"


def render_values(text, app, images):
    try:
        parsed = json.loads(text)
    except ValueError:
        return nest_values_text(text, app, images)
    return json.dumps(deploy_values(parsed, app, images), indent=2) + "\n"


def write_chart(out, profile, values_path, version, source_sha, images, wac_app_tgz, ci=False):
    """Write out/<app>/{Chart.yaml,values.yaml,charts/wac-app-<chart_version>.tgz}."""
    app = check(profile["app"], APP, "app")
    chart_version = check(profile["chart_version"], VERSION, "chart_version")
    check(version, VERSION, "version")
    check(source_sha, SHA40, "source sha")
    wanted = {image["name"] for image in profile["images"]}
    if set(images) != wanted:
        raise ChartError(
            f"images {sorted(images)} do not match the profile images {sorted(wanted)}"
        )
    if not ci:
        for name, image in images.items():
            if image.get("tag") != version:
                raise ChartError(f"image {name!r} is tagged {image.get('tag')!r}, not {version}")
            check(image.get("digest"), DIGEST, f"image {name!r} digest")
    tgz = Path(wac_app_tgz)
    if not tgz.is_file():
        raise ChartError(f"wac-app package {tgz} does not exist")
    try:
        text = Path(values_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        raise ChartError(f"cannot read the values file {values_path}: {err}") from err
    values = render_values(text, app, images)
    meta = chart_metadata(app, version, chart_version, source_sha, None if ci else images)
    chart_dir = Path(out) / app
    try:
        if chart_dir.exists():
            shutil.rmtree(chart_dir)
        (chart_dir / "charts").mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text(json.dumps(meta, indent=2) + "\n")
        (chart_dir / "values.yaml").write_text(values)
        shutil.copyfile(tgz, chart_dir / "charts" / f"wac-app-{chart_version}.tgz")
    except OSError as err:
        raise ChartError(f"cannot write the chart under {chart_dir}: {err.strerror}") from err
    return chart_dir


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url, accept, timeout):
    request = urllib.request.Request(url, headers={"Accept": accept})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            body = response.read(MAX_BODY + 1)
    except urllib.error.HTTPError as err:
        err.close()
        return err.code, b""
    except (urllib.error.URLError, OSError, ValueError) as err:
        raise ChartError(f"cannot reach the registry at {url}: {getattr(err, 'reason', err)}") from err
    if len(body) > MAX_BODY:
        raise ChartError(f"the registry response for {url} exceeds {MAX_BODY} bytes")
    return 200, body


def parse_json(body, what):
    try:
        parsed = json.loads(body)
    except ValueError as err:
        raise ChartError(f"{what} is not valid JSON") from err
    if not isinstance(parsed, dict):
        raise ChartError(f"{what} is not a JSON object")
    return parsed


def manifest_state(registry, repo, ref, timeout=TIMEOUT):
    """The parsed manifest, or None when the registry has no such reference."""
    check(registry, REGISTRY, "registry")
    url = f"http://{registry}/v2/{repo}/manifests/{ref}"
    status, body = http_get(url, MANIFEST_ACCEPT, timeout)
    if status == 404:
        return None
    if status != 200:
        raise ChartError(f"the registry answered HTTP {status} for {repo}:{ref}")
    return parse_json(body, f"the manifest of {repo}:{ref}")


def blob(registry, repo, digest, timeout=TIMEOUT):
    check(digest, DIGEST, "blob digest")
    status, body = http_get(
        f"http://{registry}/v2/{repo}/blobs/{digest}", "application/octet-stream", timeout
    )
    if status != 200:
        raise ChartError(f"the registry answered HTTP {status} for blob {digest[:19]} of {repo}")
    if "sha256:" + hashlib.sha256(body).hexdigest() != digest:
        raise ChartError(f"blob {digest[:19]} of {repo} does not match its digest")
    return body


def config_digest(manifest, repo, ref):
    config = manifest.get("config")
    if not isinstance(config, dict) or not isinstance(config.get("digest"), str):
        raise ChartError(f"the manifest of {repo}:{ref} has no config")
    return config


def chart_config(registry, app, version, timeout=TIMEOUT):
    """The Helm config blob (Chart.yaml as JSON) of deploy/<app>:<version>, or None."""
    check(app, APP, "app")
    check(version, VERSION, "version")
    repo = f"{DEPLOY_REPO}/{app}"
    manifest = manifest_state(registry, repo, version, timeout)
    if manifest is None:
        return None
    config = config_digest(manifest, repo, version)
    if config.get("mediaType") != HELM_CONFIG_TYPE:
        raise ChartError(
            f"{repo}:{version} config media type is {config.get('mediaType')!r}, not {HELM_CONFIG_TYPE}"
        )
    return parse_json(blob(registry, repo, config["digest"], timeout), f"the chart config of {repo}:{version}")


def chart_annotations(config):
    annotations = config.get("annotations")
    return annotations if isinstance(annotations, dict) else {}


def chart_revision(registry, app, version):
    config = chart_config(registry, app, version)
    if config is None:
        return None
    revision = chart_annotations(config).get(REVISION_ANNOTATION)
    if not isinstance(revision, str) or not SHA40.fullmatch(revision):
        raise ChartError(
            f"{DEPLOY_REPO}/{app}:{version} has no 40-hex {REVISION_ANNOTATION} annotation"
        )
    return revision


def chart_images(registry, app, version):
    config = chart_config(registry, app, version)
    if config is None:
        return None
    text = chart_annotations(config).get(IMAGES_ANNOTATION)
    if not isinstance(text, str):
        raise ChartError(f"{DEPLOY_REPO}/{app}:{version} has no {IMAGES_ANNOTATION} annotation")
    return parse_image_refs(text)


def runnable_manifest(registry, name, manifest):
    """Follow an image index to its first real platform manifest."""
    for entry in manifest.get("manifests") or []:
        platform = entry.get("platform") or {}
        if platform.get("os") == "unknown" or platform.get("architecture") == "unknown":
            continue
        digest = check(entry.get("digest"), DIGEST, "index entry digest")
        inner = manifest_state(registry, name, digest)
        if inner is None:
            raise ChartError(f"{name} index entry {digest[:19]} is missing from the registry")
        return inner
    raise ChartError(f"{name} index has no runnable manifest")


def image_revision(registry, name, tag):
    """The revision label of <name>:<tag>, or None when the tag is absent."""
    check(name, IMAGE_NAME, "image name")
    check(tag, re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}"), "image tag")
    manifest = manifest_state(registry, name, tag)
    if manifest is None:
        return None
    if "manifests" in manifest:
        manifest = runnable_manifest(registry, name, manifest)
    config = config_digest(manifest, name, tag)
    parsed = parse_json(blob(registry, name, config["digest"]), f"the image config of {name}:{tag}")
    inner = parsed.get("config")
    labels = inner.get("Labels") if isinstance(inner, dict) else None
    revision = labels.get(REVISION_ANNOTATION) if isinstance(labels, dict) else None
    if not isinstance(revision, str) or not SHA40.fullmatch(revision):
        raise ChartError(f"{name}:{tag} has no 40-hex {REVISION_ANNOTATION} label")
    return revision


def load_profile(path):
    spec = importlib.util.spec_from_file_location(
        "wacd_profile", Path(__file__).resolve().with_name("wacd-profile.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.load(path)
    except module.ProfileError as err:
        raise ChartError(f"profile: {err}") from err


def state_line(found, wanted, what):
    if found is None:
        return "state=absent"
    if found != wanted:
        raise ChartError(f"{what} is at revision {found}, not this run's {wanted}")
    return "state=present"


def parse_images_arg(text):
    try:
        images = json.loads(text)
    except ValueError as err:
        raise ChartError("--images is not valid JSON") from err
    if not isinstance(images, dict):
        raise ChartError("--images must be a JSON object keyed by image name")
    return images


def build_parser():
    parser = argparse.ArgumentParser(prog="wacd-chart.py")
    sub = parser.add_subparsers(dest="command", required=True)
    package = sub.add_parser("package")
    package.add_argument("--profile", required=True)
    package.add_argument("--version", required=True)
    package.add_argument("--source-sha", required=True)
    package.add_argument("--images", required=True)
    package.add_argument("--wac-app-tgz", required=True)
    package.add_argument("--out", required=True)
    package.add_argument("--ci", action="store_true")
    for command in ("chart-revision", "chart-images", "chart-state"):
        one = sub.add_parser(command)
        one.add_argument("--registry", required=True)
        one.add_argument("--app", required=True)
        one.add_argument("--version", required=True)
        if command == "chart-state":
            one.add_argument("--source-sha", required=True)
    image = sub.add_parser("image-state")
    image.add_argument("--registry", required=True)
    image.add_argument("--name", required=True)
    image.add_argument("--tag", required=True)
    image.add_argument("--source-sha", required=True)
    ci = sub.add_parser("ci-images")
    ci.add_argument("--profile", required=True)
    return parser


def run(args):
    if args.command == "package":
        profile = load_profile(args.profile)
        values_path = Path(args.profile).parent.parent / profile["values_file"]
        chart_dir = write_chart(
            Path(args.out),
            profile,
            values_path,
            args.version,
            args.source_sha,
            parse_images_arg(args.images),
            Path(args.wac_app_tgz),
            ci=args.ci,
        )
        return f"chart_dir={chart_dir}"
    if args.command == "ci-images":
        compact = json.dumps(ci_images(load_profile(args.profile)), separators=(",", ":"))
        return f"images={compact}"
    if args.command == "chart-revision":
        revision = chart_revision(args.registry, args.app, args.version)
        if revision is None:
            raise ChartError(f"{DEPLOY_REPO}/{args.app}:{args.version} is not in the registry")
        return f"revision={revision}"
    if args.command == "chart-images":
        images = chart_images(args.registry, args.app, args.version)
        if images is None:
            raise ChartError(f"{DEPLOY_REPO}/{args.app}:{args.version} is not in the registry")
        return "images=" + json.dumps(images, separators=(",", ":"), sort_keys=True)
    check(args.source_sha, SHA40, "--source-sha")
    if args.command == "chart-state":
        found = chart_revision(args.registry, args.app, args.version)
        return state_line(found, args.source_sha, f"{DEPLOY_REPO}/{args.app}:{args.version}")
    found = image_revision(args.registry, args.name, args.tag)
    return state_line(found, args.source_sha, f"{args.name}:{args.tag}")


def main(argv):
    args = build_parser().parse_args(argv[1:])
    try:
        line = run(args)
    except ChartError as err:
        print("wacd chart: " + " ".join(str(err).split()), file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
