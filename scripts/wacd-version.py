#!/usr/bin/env python3
"""Compute the release version for wac-cd from conventional commits and tags.

Run inside the app checkout, with full history and tags fetched. The CLI prints
mode=, version= and tag= lines for $GITHUB_OUTPUT. Any problem exits 1 with a
one-line message.

Modes: release (tag and deploy), publish (tag and push images, no deploy),
resume (HEAD is already tagged, so finish that release) and none.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)", re.ASCII)
BREAKING_HEADER = re.compile(r"\w+(\([^)]*\))?!:")
FEAT_HEADER = re.compile(r"feat(\([^)]*\))?:")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)


class VersionError(Exception):
    pass


def git(repo, *args):
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as err:
        detail = " ".join((err.stderr or "").split())
        raise VersionError(f"git {args[0]} failed: {detail}") from err
    except FileNotFoundError as err:
        raise VersionError("git is not installed") from err
    return result.stdout


def parse_tag(tag, prefix):
    match = re.fullmatch(re.escape(prefix) + r"(\d+)\.(\d+)\.(\d+)", tag, re.ASCII)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def latest_tag(tags, prefix):
    found = [(parsed, tag) for tag in tags if (parsed := parse_tag(tag, prefix))]
    if not found:
        return None
    return ".".join(str(part) for part in max(found)[0])


def is_breaking(message):
    first = message.split("\n", 1)[0]
    return bool(BREAKING_HEADER.match(first)) or bool(BREAKING_FOOTER.search(message))


def bump(latest, messages):
    if latest is None:
        return "0.1.0"
    match = SEMVER.fullmatch(latest)
    if not match:
        raise VersionError(f"latest version {latest!r} is not X.Y.Z")
    major, minor, patch = (int(part) for part in match.groups())
    if any(is_breaking(message) for message in messages):
        return f"{major + 1}.0.0"
    if any(FEAT_HEADER.match(message.split("\n", 1)[0]) for message in messages):
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def commit_of(repo, tag):
    ref = f"refs/tags/{tag}^{{commit}}"
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "-q", "--verify", ref],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError:
        return None
    return out.strip()


def tag_lines(repo, *args):
    return [line for line in git(repo, "tag", *args).splitlines() if line]


def plan(repo, prefix, source, caller_version, deploy=True):
    if any(ch in prefix for ch in "\r\n\0"):
        raise VersionError("tag prefix contains a line break")
    if source not in ("commits", "caller"):
        raise VersionError(f"unknown version source {source!r}")
    repo = Path(repo)
    head = git(repo, "rev-parse", "HEAD").strip()

    at_head = [
        (parsed, tag)
        for tag in tag_lines(repo, "--points-at", "HEAD")
        if (parsed := parse_tag(tag, prefix))
    ]
    if at_head:
        parsed, tag = max(at_head)
        result = {
            "mode": "resume",
            "version": ".".join(str(part) for part in parsed),
            "tag": tag,
        }
        return finish(result, deploy)

    if source == "caller":
        if not caller_version:
            return {"mode": "none", "version": "", "tag": ""}
        if not SEMVER.fullmatch(caller_version):
            raise VersionError(
                f"version {caller_version!r} must be X.Y.Z with no prefix"
            )
        new = caller_version
    else:
        latest = latest_tag(tag_lines(repo, "--merged", "HEAD"), prefix)
        if latest is None:
            log = git(repo, "log", "--format=%B%x00", "HEAD")
        else:
            log = git(repo, "log", "--format=%B%x00", f"refs/tags/{prefix}{latest}..HEAD")
        messages = [m.strip("\n") for m in log.split("\0") if m.strip()]
        new = bump(latest, messages)

    tag = f"{prefix}{new}"
    existing = commit_of(repo, tag)
    if existing is not None and existing != head:
        raise VersionError(f"tag {tag} already exists on {existing}, not on HEAD {head}")
    return finish({"mode": "release", "version": new, "tag": tag}, deploy)


def finish(result, deploy):
    if deploy:
        return result
    mapped = {"release": "publish", "resume": "none", "none": "none"}
    return {**result, "mode": mapped[result["mode"]]}


def main(argv):
    parser = argparse.ArgumentParser(prog="wacd-version.py")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--version-source", choices=("commits", "caller"), default="commits")
    parser.add_argument("--version", default="")
    parser.add_argument("--deploy", choices=("true", "false"), default="true")
    args = parser.parse_args(argv[1:])
    try:
        result = plan(
            Path.cwd(),
            args.prefix,
            args.version_source,
            args.version,
            args.deploy == "true",
        )
    except VersionError as err:
        message = " ".join(str(err).split())
        print(f"wacd version: {message}", file=sys.stderr)
        return 1
    for key in ("mode", "version", "tag"):
        print(f"{key}={result[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
