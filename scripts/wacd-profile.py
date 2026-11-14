#!/usr/bin/env python3
"""Validate a repo's .github/wacd.json profile, the single input to wac-ci and wac-cd.

The CLI prints key=value lines for $GITHUB_OUTPUT. Any problem exits 1 with a
message that names the dotted field path of the first offence.

`wacd-profile.py node-layout <repo>` instead reports how a node repo installs:
`root_package=true|false` (whether wac-ci's implicit root `npm ci` applies)
and a `lockfiles` multi-line block of every package-lock.json for the npm
cache, empty when there are none.
"""

import json
import os
import re
import sys
import uuid
from pathlib import Path

APP = re.compile(r"[a-z][a-z0-9-]{0,39}")
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
TAG_PREFIX = re.compile(r"([a-z][a-z0-9-]*)?")
HEALTH_URL = re.compile(
    r"http://[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.svc\.cluster\.local(:[0-9]{1,5})?/\S*"
)
IMAGE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
BUILD_ARG = re.compile(r"[A-Z_][A-Z0-9_]*")
VERSION = re.compile(r"[0-9]+(\.[0-9]+){0,2}")

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
CI_OPTIONAL = ("node_version", "go_version")


class ProfileError(Exception):
    """A profile problem; the message starts with the dotted field path."""


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


def check_ci(ci, path):
    if not isinstance(ci, dict):
        fail(path, "must be an object")
    check_keys(ci, path, CI_REQUIRED, CI_OPTIONAL)
    stack = ci["stack"]
    if stack not in ("node", "go"):
        fail(f"{path}.stack", "must be 'node' or 'go'")
    own, other = (
        ("node_version", "go_version") if stack == "node" else ("go_version", "node_version")
    )
    if own not in ci:
        fail(f"{path}.{own}", f"required when stack is {stack!r}")
    if other in ci:
        fail(f"{path}.{other}", f"not allowed when stack is {stack!r}")
    check_pattern(ci[own], f"{path}.{own}", VERSION, "a version such as 22 or 1.24")
    commands = ci["commands"]
    if not isinstance(commands, list) or not commands:
        fail(f"{path}.commands", "must be a non-empty list")
    for i, command in enumerate(commands):
        here = f"{path}.commands[{i}]"
        if not isinstance(command, str) or not command.strip():
            fail(here, "must be a non-empty string")
        if "\n" in command or "\r" in command:
            fail(here, "must be a single line")


def validate(data, repo_root):
    if not isinstance(data, dict):
        fail("profile", "must be a JSON object")
    check_keys(data, "", TOP_REQUIRED, TOP_OPTIONAL)
    version = data["schema_version"]
    if isinstance(version, bool) or version != 1 or not isinstance(version, int):
        fail("schema_version", "must be the integer 1")
    check_pattern(data["app"], "app", APP, "a lowercase app name of at most 40 characters")
    check_pattern(data["chart_version"], "chart_version", SEMVER, "X.Y.Z")
    check_relative(data["values_file"], "values_file")
    if not (repo_root / data["values_file"]).is_file():
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
    check_ci(data["ci"], "ci")


def load(path):
    """Return the validated profile at path; the repo root is the parent of .github/."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except OSError as err:
        fail("profile", f"cannot read {path}: {err.strerror}")
    except UnicodeDecodeError:
        fail("profile", f"{path} is not valid UTF-8")
    except json.JSONDecodeError as err:
        fail("profile", f"{path} is not valid JSON: {err}")
    validate(data, path.parent.parent)
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
    if len(argv) != 2:
        print(
            "usage: wacd-profile.py <path to .github/wacd.json> | node-layout <repo>",
            file=sys.stderr,
        )
        return 2
    try:
        data = load(argv[1])
    except ProfileError as err:
        print(f"wacd profile: {err}", file=sys.stderr)
        return 1
    print("\n".join(outputs(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
