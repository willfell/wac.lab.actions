#!/usr/bin/env python3
"""Validate a repo's .github/wacd.json profile, the single input to wac-ci and wac-cd.

The CLI prints key=value lines for $GITHUB_OUTPUT. Any problem exits 1 with a
message that names the dotted field path of the first offence.

`wacd-profile.py --contract <path>` reads hemi's copy of the settings (the
`contract.json` wac-cd's plan wrote under `.wacd-contract/`) instead, derives the
profile it stands for and prints exactly the lines the profile file it came
from prints, `values_file` included: the path the repository's values file
had, which the copy carries as `delivery.valuesFile`. The values themselves
are the copy's `runtime`, so the repository need not hold that file.

`wacd-profile.py node-layout <repo>` instead reports how a node repo installs:
`root_package=true|false` (whether wac-ci's implicit root `npm ci` applies)
and a `lockfiles` multi-line block of every package-lock.json for the npm
cache, empty when there are none.

`ci.docs_paths` (hemi's `delivery.docsPaths`) is checked by wacd-docs.py's
check_docs_paths(), the one matcher wac-ci and wac-cd share, and its refusals
are this script's: ProfileError is that module's class. The `docs_paths` line
is the effective list, wacd-docs.py's DEFAULT when the key is absent, while
`ci` keeps the key as written, so an absent list and `[]` stay apart.
"""

import importlib.util
import json
import os
import re
import sys
import uuid
from pathlib import Path


def _load_docs():
    spec = importlib.util.spec_from_file_location("wacd_profile_docs", Path(__file__).with_name("wacd-docs.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


docs = _load_docs()

APP = re.compile(r"[a-z][a-z0-9-]{0,39}")
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
TAG_PREFIX = re.compile(r"([a-z][a-z0-9-]*)?")
HEALTH_URL = re.compile(
    r"http://[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.svc\.cluster\.local(:[0-9]{1,5})?/\S*"
)
IMAGE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
BUILD_ARG = re.compile(r"[A-Z_][A-Z0-9_]*")
VERSION = re.compile(r"[0-9]+(\.[0-9]+){0,2}")
APT_PACKAGE = re.compile(r"[a-z0-9][a-z0-9.+-]*")

TOP_REQUIRED = (
    "schema_version",
    "app",
    "chart_version",
    "values_file",
    "tag_prefix",
    "health_url",
    "images",
    "ci",
)
TOP_OPTIONAL = ("health_expect_db_ok",)
IMAGE_REQUIRED = ("name", "dockerfile", "context")
IMAGE_OPTIONAL = ("sha_build_arg",)
CI_REQUIRED = ("stack", "commands")
CI_OPTIONAL = ("node_version", "go_version", "go_version_file", "apt_packages", "docs_paths")

CONTRACT_DIR = ".wacd-contract"
DERIVED_VALUES = f"{CONTRACT_DIR}/values.json"
CONTRACT_DELIVERY = ("chartVersion", "valuesFile", "tagPrefix", "healthUrl", "healthExpectDbOk")
CONTRACT_BUILD_REQUIRED = ("images", "stack")
CONTRACT_BUILD_OPTIONAL = ("nodeVersion", "goVersion", "goVersionFile", "aptPackages")
CONTRACT_IMAGE_REQUIRED = ("name", "dockerfile", "context")
CONTRACT_IMAGE_OPTIONAL = ("shaBuildArg",)


ProfileError = docs.ProfileError


def fail(path, message):
    raise ProfileError(f"{path}: {message}")


def check_keys(obj, path, required, optional):
    """Reject unknown keys and report the first missing required one."""
    prefix = f"{path}." if path else ""
    for key in obj:
        if key not in required and key not in optional:
            fail(f"{prefix}{key}", "unknown field")
    for key in required:
        if key not in obj:
            fail(f"{prefix}{key}", "required")


def check_str(value, path):
    if not isinstance(value, str):
        fail(path, "must be a string")
    return value


def check_pattern(value, path, pattern, what):
    if not pattern.fullmatch(check_str(value, path)):
        fail(path, f"must be {what}")


def check_relative(value, path):
    """A repo-relative path that cannot climb out of the repo."""
    check_str(value, path)
    if not value or value.startswith("/") or any(c in value for c in "\\\n\r"):
        fail(path, "must be a relative path inside the repo")
    if ".." in value.split("/"):
        fail(path, "must not contain a '..' segment")


def check_images(images, path):
    if not isinstance(images, list) or not images:
        fail(path, "must be a non-empty list")
    seen = set()
    for i, image in enumerate(images):
        here = f"{path}[{i}]"
        if not isinstance(image, dict):
            fail(here, "must be an object")
        check_keys(image, here, IMAGE_REQUIRED, IMAGE_OPTIONAL)
        check_pattern(image["name"], f"{here}.name", IMAGE_NAME, "a lowercase image name")
        if image["name"] in seen:
            fail(f"{here}.name", f"duplicate image name {image['name']!r}")
        seen.add(image["name"])
        check_relative(image["dockerfile"], f"{here}.dockerfile")
        check_relative(image["context"], f"{here}.context")
        if "sha_build_arg" in image:
            check_pattern(
                image["sha_build_arg"],
                f"{here}.sha_build_arg",
                BUILD_ARG,
                "an upper-case build arg name",
            )


def check_ci(ci, path, repo_root):
    if not isinstance(ci, dict):
        fail(path, "must be an object")
    check_keys(ci, path, CI_REQUIRED, CI_OPTIONAL)
    stack = ci["stack"]
    if stack not in ("node", "go"):
        fail(f"{path}.stack", "must be 'node' or 'go'")
    if stack == "node":
        for other in ("go_version", "go_version_file"):
            if other in ci:
                fail(f"{path}.{other}", "not allowed when stack is 'node'")
        if "node_version" not in ci:
            fail(f"{path}.node_version", "required when stack is 'node'")
        check_pattern(ci["node_version"], f"{path}.node_version", VERSION, "a version such as 22 or 1.24")
    else:
        if "node_version" in ci:
            fail(f"{path}.node_version", "not allowed when stack is 'go'")
        if "go_version" in ci and "go_version_file" in ci:
            fail(f"{path}.go_version_file", "not allowed together with go_version; set one")
        if "go_version_file" in ci:
            check_version_file(ci["go_version_file"], f"{path}.go_version_file", repo_root)
        elif "go_version" in ci:
            check_pattern(ci["go_version"], f"{path}.go_version", VERSION, "a version such as 22 or 1.24")
        else:
            fail(f"{path}.go_version", "required when stack is 'go', unless go_version_file is set")
    commands = ci["commands"]
    if not isinstance(commands, list) or not commands:
        fail(f"{path}.commands", "must be a non-empty list")
    for i, command in enumerate(commands):
        here = f"{path}.commands[{i}]"
        if not isinstance(command, str) or not command.strip():
            fail(here, "must be a non-empty string")
        if "\n" in command or "\r" in command:
            fail(here, "must be a single line")
    if "apt_packages" in ci:
        check_apt_packages(ci["apt_packages"], f"{path}.apt_packages")


def check_version_file(value, path, repo_root):
    """A repo-relative file setup-go reads the Go version from, such as go.mod."""
    check_relative(value, path)
    if not (repo_root / value).is_file():
        fail(path, f"{value} does not exist in the repo")


def check_apt_packages(packages, path):
    """Debian package names only, so wac-ci can pass them to apt-get as words."""
    if not isinstance(packages, list) or not packages:
        fail(path, "must be a non-empty list")
    seen = set()
    for i, package in enumerate(packages):
        here = f"{path}[{i}]"
        check_pattern(package, here, APT_PACKAGE, "a Debian package name such as g++")
        if package in seen:
            fail(here, f"duplicate package {package!r}")
        seen.add(package)


def validate(data, repo_root, values_in_repo=True):
    """Validate a profile; values_in_repo=False when the values come from hemi's copy."""
    if not isinstance(data, dict):
        fail("profile", "must be a JSON object")
    check_keys(data, "", TOP_REQUIRED, TOP_OPTIONAL)
    version = data["schema_version"]
    if isinstance(version, bool) or version != 1 or not isinstance(version, int):
        fail("schema_version", "must be the integer 1")
    check_pattern(data["app"], "app", APP, "a lowercase app name of at most 40 characters")
    check_pattern(data["chart_version"], "chart_version", SEMVER, "X.Y.Z")
    check_relative(data["values_file"], "values_file")
    if values_in_repo and not (repo_root / data["values_file"]).is_file():
        fail("values_file", f"{data['values_file']} does not exist in the repo")
    check_pattern(data["tag_prefix"], "tag_prefix", TAG_PREFIX, "empty or a lowercase prefix such as v")
    check_pattern(
        data["health_url"],
        "health_url",
        HEALTH_URL,
        "an in-cluster http://<host>.svc.cluster.local URL",
    )
    if "health_expect_db_ok" in data and not isinstance(data["health_expect_db_ok"], bool):
        fail("health_expect_db_ok", "must be a JSON boolean")
    check_images(data["images"], "images")
    check_ci(data["ci"], "ci", repo_root)
    docs.check_docs_paths(data["ci"], "ci", data)


def load(path):
    """Return the validated profile at path; the repo root is the parent of .github/."""
    path = Path(path)
    data = read_json(path, "profile")
    validate(data, path.parent.parent)
    return data


def contract_object(value, path):
    if not isinstance(value, dict):
        fail(path, "must be an object")
    return value


def contract_field(obj, key, path):
    if key not in obj:
        fail(f"{path}.{key}", "required")
    return obj[key]


def from_contract(contract, values_file):
    """The profile hemi's copy stands for, with values_file set by the caller.

    Field names map one to one, the inverse of hemi's readProfile: delivery's
    settings to the top level, build to images and ci, and the gates' commands,
    in order, to ci.commands. Unknown fields inside build and its images are
    refused rather than dropped, so a setting the copy carries is never
    silently ignored. An aptPackages of [] is left out, as the profile writes
    it; any other value goes on to ci.apt_packages and its check, so 0, null
    and "" are refused there rather than dropped. delivery.docsPaths, when
    present, goes on to ci.docs_paths as it is, `[]` and null included.
    """
    contract_object(contract, "contract")
    version = contract_field(contract, "version", "contract")
    if type(version) is not int or version != 1:
        fail("contract.version", "must be the integer 1")
    identity = contract_object(contract_field(contract, "identity", "contract"), "contract.identity")
    delivery = contract_object(contract_field(contract, "delivery", "contract"), "contract.delivery")
    build = contract_object(contract_field(contract, "build", "contract"), "contract.build")
    gates = contract_field(contract, "gates", "contract")
    for key in CONTRACT_DELIVERY:
        contract_field(delivery, key, "contract.delivery")
    check_keys(build, "contract.build", CONTRACT_BUILD_REQUIRED, CONTRACT_BUILD_OPTIONAL)
    images = build["images"]
    if not isinstance(images, list):
        fail("contract.build.images", "must be a list")
    derived_images = []
    for i, image in enumerate(images):
        here = f"contract.build.images[{i}]"
        contract_object(image, here)
        check_keys(image, here, CONTRACT_IMAGE_REQUIRED, CONTRACT_IMAGE_OPTIONAL)
        derived = {key: image[key] for key in CONTRACT_IMAGE_REQUIRED}
        if "shaBuildArg" in image:
            derived["sha_build_arg"] = image["shaBuildArg"]
        derived_images.append(derived)
    if not isinstance(gates, list) or not gates:
        fail("contract.gates", "must be a non-empty list")
    commands = []
    for i, gate in enumerate(gates):
        here = f"contract.gates[{i}]"
        gate_commands = contract_field(contract_object(gate, here), "commands", here)
        if not isinstance(gate_commands, list):
            fail(f"{here}.commands", "must be a list")
        commands.extend(gate_commands)
    ci = {"stack": build["stack"]}
    for source, target in (
        ("nodeVersion", "node_version"),
        ("goVersion", "go_version"),
        ("goVersionFile", "go_version_file"),
    ):
        if source in build:
            ci[target] = build[source]
    ci["commands"] = commands
    if "docsPaths" in delivery:
        ci["docs_paths"] = delivery["docsPaths"]
    if "aptPackages" in build and build["aptPackages"] != []:
        ci["apt_packages"] = build["aptPackages"]
    return {
        "schema_version": 1,
        "app": contract_field(identity, "app", "contract.identity"),
        "chart_version": delivery["chartVersion"],
        "values_file": values_file,
        "tag_prefix": delivery["tagPrefix"],
        "health_url": delivery["healthUrl"],
        "health_expect_db_ok": delivery["healthExpectDbOk"],
        "images": derived_images,
        "ci": ci,
    }


def read_json(path, what):
    path = Path(path)
    try:
        return json.loads(path.read_text())
    except OSError as err:
        fail(what, f"cannot read {path}: {err.strerror}")
    except UnicodeDecodeError:
        fail(what, f"{path} is not valid UTF-8")
    except json.JSONDecodeError as err:
        fail(what, f"{path} is not valid JSON: {err}")


def load_contract(path):
    """The validated profile hemi's copy at path stands for.

    Its values_file is the copy's delivery.valuesFile, and that file need not
    exist; the repository root (for go_version_file) is the parent of
    .wacd-contract/, as it is the parent of .github/ for a profile file.
    """
    path = Path(path)
    contract = read_json(path, "contract")
    delivery = contract.get("delivery") if isinstance(contract, dict) else None
    values_file = delivery.get("valuesFile") if isinstance(delivery, dict) else None
    data = from_contract(contract, values_file)
    validate(data, path.parent.parent, values_in_repo=False)
    return data


def outputs(data):
    expect_db = data.get("health_expect_db_ok", True)
    compact = {"separators": (",", ":")}
    return [
        f"app={data['app']}",
        f"chart_version={data['chart_version']}",
        f"values_file={data['values_file']}",
        f"tag_prefix={data['tag_prefix']}",
        f"health_url={data['health_url']}",
        f"health_expect_db_ok={'true' if expect_db else 'false'}",
        f"images={json.dumps(data['images'], **compact)}",
        f"ci={json.dumps(data['ci'], **compact)}",
        f"apt_packages={' '.join(data['ci'].get('apt_packages', []))}",
        f"docs_paths={json.dumps(data['ci'].get('docs_paths', list(docs.DEFAULT)), **compact)}",
    ]


def node_layout(repo):
    """Return (root_package, lockfiles) for the repo checked out at `repo`.

    Lockfiles are repo-relative POSIX paths, sorted. The walk skips
    `node_modules` and any nested checkout (a directory holding `.git`, such
    as a submodule), so only the repo's own lockfiles reach the cache key.
    wac-ci moves its shared checkout out of the workspace before this runs. Hidden directories are walked: finance keeps a
    package under `.claude/`.
    """
    root = Path(repo)
    if not root.is_dir():
        fail("node-layout", f"{repo} is not a directory")
    lockfiles = []
    for current, dirs, files in os.walk(root):
        here = Path(current)
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in ("node_modules", ".git") and not (here / name / ".git").exists()
        )
        if "package-lock.json" in files:
            relative = (here / "package-lock.json").relative_to(root).as_posix()
            if "\n" in relative or "\r" in relative:
                fail("node-layout", f"lockfile path {relative!r} contains a line break")
            lockfiles.append(relative)
    return (root / "package.json").is_file(), sorted(lockfiles)


def node_layout_outputs(root_package, lockfiles):
    # A random delimiter cannot collide with a path, which has no line breaks.
    delimiter = f"WACD_LOCKFILES_{uuid.uuid4().hex}"
    return [
        f"root_package={'true' if root_package else 'false'}",
        f"lockfiles<<{delimiter}",
        *lockfiles,
        delimiter,
    ]


def main(argv):
    if len(argv) == 3 and argv[1] == "node-layout":
        try:
            lines = node_layout_outputs(*node_layout(argv[2]))
        except ProfileError as err:
            print(f"wacd profile: {err}", file=sys.stderr)
            return 1
        print("\n".join(lines))
        return 0
    contract = len(argv) == 3 and argv[1] == "--contract"
    if len(argv) != 2 and not contract:
        print(
            "usage: wacd-profile.py <path to .github/wacd.json> | --contract <path> | node-layout <repo>",
            file=sys.stderr,
        )
        return 2
    try:
        data = load_contract(argv[2]) if contract else load(argv[1])
    except ProfileError as err:
        print(f"wacd profile: {err}", file=sys.stderr)
        return 1
    print("\n".join(outputs(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
