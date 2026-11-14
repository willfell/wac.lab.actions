"""Compare an app's kustomize render with its wac-app Helm render.

Usage: wacd-parity.py <kustomize.yaml> <helm.yaml>

Exit 0 prints `parity: N objects match`. Exit 1 prints one line per missing,
extra or differing `(kind, name)` object; a differing line names the first
differing dotted path. Unreadable or malformed input also exits 1, with one
`wacd parity:` line on stderr.

`normalize` and its helpers are ported verbatim from `wac.lab`
`tests/wac_app/render.py` @ `8612f031` (branch `feat/wac-app-chart`), minus
`render`, `_helm` and pytest; that file is byte-identical at wac.lab main
`a8214238`. Those are the parity rules of the wacd plan
("Parity, defined once"): namespaces, object labels, image tags and digests,
container port names and probe/targetPort names are dropped or resolved; the
Argo CD hook-delete-policy annotation compares as a set. Ingresses keep their
names and compare whole, keyed like every other object, because a cutover must
rename nothing. Nothing else is normalized, so any other difference fails.
If lab changes its normalize, port the change here; this is the one copy
outside lab.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import yaml

Doc = dict[str, Any]
Key = tuple[str, ...]


def _pod_spec(doc: Doc) -> Doc | None:
    if doc["kind"] in ("Deployment", "Job"):
        spec: Doc = doc["spec"]["template"]["spec"]
        return spec
    if doc["kind"] == "CronJob":
        spec = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        return spec
    return None


def _containers(pod: Doc) -> list[Doc]:
    return [*pod.get("containers", []), *pod.get("initContainers", [])]


def _port_names(containers: list[Doc]) -> dict[str, int]:
    return {
        port["name"]: port["containerPort"]
        for container in containers
        for port in container.get("ports", [])
        if "name" in port
    }


def _repository(image: str) -> str:
    """`host:port/repo:tag@sha256:...` -> `host:port/repo`."""
    without_digest = image.split("@", 1)[0]
    head, _, last = without_digest.rpartition("/")
    return f"{head}/{last.split(':', 1)[0]}" if head else last.split(":", 1)[0]


def _resolve_probe_ports(container: Doc) -> None:
    ports = _port_names([container])
    for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
        for handler in ("httpGet", "tcpSocket"):
            target = container.get(probe, {}).get(handler)
            if target is not None and isinstance(target.get("port"), str):
                target["port"] = ports[target["port"]]


def _workload_port_names(docs: list[Doc], selector: dict[str, str]) -> dict[str, int]:
    for doc in docs:
        if doc["kind"] != "Deployment":
            continue
        template = doc["spec"]["template"]
        if selector.items() <= template["metadata"].get("labels", {}).items():
            return _port_names(_containers(template["spec"]))
    return {}


def _service_port_names(docs: list[Doc]) -> dict[str, dict[str, int]]:
    return {
        doc["metadata"]["name"]: {
            port["name"]: port["port"] for port in doc["spec"]["ports"] if "name" in port
        }
        for doc in docs
        if doc["kind"] == "Service"
    }


HOOK_DELETE_POLICY = "argocd.argoproj.io/hook-delete-policy"


def _sort_hook_delete_policy(metadata: Doc) -> None:
    """Argo CD splits the policy on commas into a set, so order carries nothing:
    travel's and wealth's Jobs write `HookSucceeded,BeforeHookCreation`, baton's
    `BeforeHookCreation,HookSucceeded`, and one chart template renders both."""
    annotations = metadata.get("annotations", {})
    if HOOK_DELETE_POLICY in annotations:
        policies = annotations[HOOK_DELETE_POLICY].split(",")
        annotations[HOOK_DELETE_POLICY] = ",".join(sorted(p.strip() for p in policies))


def _add(out: dict[Key, Doc], key: Key, value: Doc) -> None:
    if key in out:
        raise ValueError(f"two objects normalize to the same key {key}")
    out[key] = value


def normalize(docs: list[Doc]) -> dict[Key, Doc]:
    """Key every object by `(kind, name)` after applying exactly the plan's
    parity rules. Ingresses compare whole and by name, as every other object
    does: a cutover renames nothing."""
    # Port lookups read the documents before container port names are dropped.
    service_ports = _service_port_names(docs)
    out: dict[Key, Doc] = {}
    for original in docs:
        doc = copy.deepcopy(original)
        metadata = doc["metadata"]
        metadata.pop("namespace", None)
        metadata.pop("labels", None)
        _sort_hook_delete_policy(metadata)

        pod = _pod_spec(doc)
        if pod is not None:
            for container in _containers(pod):
                container["image"] = _repository(container["image"])
                _resolve_probe_ports(container)
                for port in container.get("ports", []):
                    port.pop("name", None)
            if doc["kind"] == "Job":
                template = doc["spec"]["template"]
                template.get("metadata", {}).pop("labels", None)
                if template.get("metadata") == {}:
                    del template["metadata"]
                for container in pod["containers"]:
                    container.pop("name", None)

        if doc["kind"] == "Service":
            names = _workload_port_names(docs, doc["spec"].get("selector", {}))
            for port in doc["spec"]["ports"]:
                if isinstance(port.get("targetPort"), str):
                    port["targetPort"] = names[port["targetPort"]]

        if doc["kind"] == "Ingress":
            for rule in doc["spec"]["rules"]:
                for path in rule["http"]["paths"]:
                    service = path["backend"]["service"]
                    if "name" in service["port"]:
                        port_name = service["port"]["name"]
                        service["port"] = {"number": service_ports[service["name"]][port_name]}

        _add(out, (doc["kind"], metadata["name"]), doc)
    return out


# Everything below is this repo's, not lab's: the comparison and the CLI.

_ABSENT = object()


def _first_difference(expected: Any, actual: Any, path: str = "") -> str | None:
    """The dotted path of the first place the two values differ, or None.

    Mapping keys are walked in sorted order and list items by index, so the
    path is deterministic; a key present on one side only differs at that key.
    """
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual), key=str):
            child = f"{path}.{key}" if path else str(key)
            found = _first_difference(expected.get(key, _ABSENT), actual.get(key, _ABSENT), child)
            if found is not None:
                return found
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        for index in range(max(len(expected), len(actual))):
            child = f"{path}.{index}" if path else str(index)
            left = expected[index] if index < len(expected) else _ABSENT
            right = actual[index] if index < len(actual) else _ABSENT
            found = _first_difference(left, right, child)
            if found is not None:
                return found
        return None
    if expected is _ABSENT or actual is _ABSENT or type(expected) is not type(actual):
        return path or "."
    return None if expected == actual else (path or ".")


def compare(expected: list[Doc], actual: list[Doc]) -> list[str]:
    """One line per `(kind, name)` missing from, extra in, or differing in
    `actual` (the Helm render) against `expected` (the kustomize render)."""
    want = normalize(expected)
    got = normalize(actual)
    lines: list[str] = []
    for key in sorted(set(want) | set(got)):
        if key not in got:
            lines.append(f"missing {key}: in the kustomize render, not the helm render")
        elif key not in want:
            lines.append(f"extra {key}: in the helm render, not the kustomize render")
        else:
            path = _first_difference(want[key], got[key])
            if path is not None:
                lines.append(f"differs {key}: first at {path}")
    return lines


def _load(path: str) -> list[Doc]:
    docs = [doc for doc in yaml.safe_load_all(Path(path).read_text()) if doc is not None]
    for doc in docs:
        if not isinstance(doc, dict) or "kind" not in doc or "name" not in doc.get("metadata", {}):
            raise ValueError(f"{path}: every document needs kind and metadata.name")
    return docs


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: wacd-parity.py <kustomize.yaml> <helm.yaml>", file=sys.stderr)
        return 1
    try:
        expected = _load(argv[0])
        actual = _load(argv[1])
        if not expected:
            # A parity gate over nothing passes vacuously; refuse it instead.
            raise ValueError(f"{argv[0]}: the kustomize render has no objects")
        lines = compare(expected, actual)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as err:
        print("wacd parity: " + " ".join(str(err).split()), file=sys.stderr)
        return 1
    if lines:
        for line in lines:
            print(line)
        return 1
    print(f"parity: {len(expected)} objects match")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
