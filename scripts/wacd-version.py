#!/usr/bin/env python3
"""Compute the release version for wac-cd from conventional commits and tags.

Run inside the app checkout, with full history and tags fetched. The CLI prints
mode=, version= and tag= lines for $GITHUB_OUTPUT. Any problem exits 1 with a
one-line message, except a refused replay (below), which exits 3 so the caller
can tell it from every other failure.

Modes: release (tag and deploy), publish (tag and push images, no deploy),
resume (HEAD is already tagged, so finish that release) and none.

--published says whether the deploy chart for HEAD's tagged version is in the
registry. A tagged HEAD whose chart is absent (`false`) is built and published
again, which is how a version is recovered after a registry loss and how a HEAD
tagged by an older pipeline gets its first chart. `true` and `unknown` resume.

A deploying run fails when origin/main carries a tag with the prefix that HEAD
does not contain, or when HEAD's highest tag is lower than the highest tag with
the prefix on origin/main: what is running changes by a rollback or a new
release, never by replaying an old build.

`--mode settings --commit C --running V` plans a settings-only release: the
running version V's images, built from C, released again under a new version
with the copy's current settings. The new version is the next patch above the
highest tag with the prefix merged into origin/main, never above V itself, so
after a rollback it still goes above every published version; its tag goes on
C, which must be on main. It prints mode=settings, version=, tag= and
claimed=: claimed names main's newest version when that tag already sits on C
above V (an earlier settings release of this code that never went live), so
wac-cd can take it again rather than spend a second version on one revision.
above= lists every version on main newer than V, newest first, comma-separated,
so wac-cd can find one that already shipped the dispatched revision.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

NUM = r"(0|[1-9][0-9]*)"
SEMVER = re.compile(rf"{NUM}\.{NUM}\.{NUM}")
SHA40 = re.compile(r"[0-9a-f]{40}")
BREAKING_HEADER = re.compile(r"\w+(\([^)]*\))?!:")
FEAT_HEADER = re.compile(r"feat(\([^)]*\))?:")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
PUBLISHED = ("true", "false", "unknown")
MAIN_REF = "origin/main"
REPLAY_REFUSED_EXIT = 3


class VersionError(Exception):
    pass


class ReplayRefused(VersionError):
    pass


def git(repo, *args):
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as err:
        detail = " ".join((err.stderr or "").split())
        raise VersionError(f"git {args[0]} failed: {detail}") from err
    except FileNotFoundError as err:
        raise VersionError("git is not installed") from err
    return result.stdout


def parse_tag(tag, prefix):
    match = re.fullmatch(re.escape(prefix) + rf"{NUM}\.{NUM}\.{NUM}", tag)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def newest_tag(tags, prefix):
    found = [(parsed, tag) for tag in tags if (parsed := parse_tag(tag, prefix))]
    if not found:
        return None
    return max(found)


def latest_tag(tags, prefix):
    newest = newest_tag(tags, prefix)
    if newest is None:
        return None
    return ".".join(str(part) for part in newest[0])


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
    return commit_of_ref(repo, f"refs/tags/{tag}")


def commit_of_ref(repo, ref):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "-q", "--verify", f"{ref}^{{commit}}"],
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        ).stdout
    except subprocess.CalledProcessError:
        return None
    return out.strip()


def tag_lines(repo, *args):
    return [line for line in git(repo, "tag", *args).splitlines() if line]


def older_than(tag):
    return ReplayRefused(
        f"This commit is older than {tag}, which is already published and contains "
        "or replaces it. Roll back or release anew; an old build is never replayed."
    )


def published_tags(repo, prefix, main_ref):
    if commit_of_ref(repo, main_ref) is None:
        raise VersionError(
            f"{main_ref} is not in the checkout, so the newest published version cannot be judged; fetch with full depth"
        )
    on_main = tag_lines(repo, "--merged", main_ref)
    in_head = set(tag_lines(repo, "--merged", "HEAD"))
    newest = newest_tag(on_main, prefix)
    unmerged = newest_tag([tag for tag in on_main if tag not in in_head], prefix)
    return newest, unmerged


def plan(
    repo, prefix, source, caller_version, deploy=True, published="unknown", main_ref=MAIN_REF
):
    if any(ch in prefix for ch in "\r\n\0"):
        raise VersionError("tag prefix contains a line break")
    if source not in ("commits", "caller"):
        raise VersionError(f"unknown version source {source!r}")
    if published not in PUBLISHED:
        raise VersionError(f"published must be one of {', '.join(PUBLISHED)}, not {published!r}")
    repo = Path(repo)
    if git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise VersionError(
            "the checkout is shallow, so tags and history are incomplete; fetch with full depth"
        )
    head = git(repo, "rev-parse", "HEAD").strip()

    at_head = [
        (parsed, tag)
        for tag in tag_lines(repo, "--points-at", "HEAD")
        if (parsed := parse_tag(tag, prefix))
    ]
    if at_head:
        parsed, tag = max(at_head)
        if deploy:
            newest, unmerged = published_tags(repo, prefix, main_ref)
            if newest is not None and parsed < newest[0]:
                raise ReplayRefused(
                    f"This run is for {tag}, but {newest[1]} is already published. "
                    "Roll back or release anew; an old build is never replayed."
                )
            if unmerged is not None:
                raise older_than(unmerged[1])
        result = {
            "mode": "release" if published == "false" else "resume",
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

    if deploy:
        _, unmerged = published_tags(repo, prefix, main_ref)
        if unmerged is not None:
            raise older_than(unmerged[1])

    tag = f"{prefix}{new}"
    existing = commit_of(repo, tag)
    if existing is not None and existing != head:
        raise VersionError(f"tag {tag} already exists on {existing}, not on HEAD {head}")
    return finish({"mode": "release", "version": new, "tag": tag}, deploy)


def plan_settings(repo, prefix, commit, running, main_ref=MAIN_REF):
    """The version and tag of a settings release of running (built from commit)."""
    if any(ch in prefix for ch in "\r\n\0"):
        raise VersionError("tag prefix contains a line break")
    if not SHA40.fullmatch(commit or ""):
        raise VersionError("--commit must be 40 lowercase hex characters")
    match = SEMVER.fullmatch(running or "")
    if not match:
        raise VersionError(
            f"the running version {str(running)[:40]!r} is not a released X.Y.Z, so there is no "
            "running code to release settings on"
        )
    current = tuple(int(part) for part in match.groups())
    repo = Path(repo)
    if git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise VersionError(
            "the checkout is shallow, so tags and history are incomplete; fetch with full depth"
        )
    if commit_of_ref(repo, main_ref) is None:
        raise VersionError(
            f"{main_ref} is not in the checkout, so the newest published version cannot be judged; fetch with full depth"
        )
    if commit_of_ref(repo, commit) != commit:
        raise VersionError(f"{commit} is not a commit in this checkout")
    ancestor = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, main_ref],
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise VersionError(f"{running} was built from {commit}, which is not on main")
    newest = newest_tag(tag_lines(repo, "--merged", main_ref), prefix)
    if newest is None:
        raise VersionError(f"main carries no {prefix}X.Y.Z tag, so there is no release to go above")
    (major, minor, patch), newest_name = newest
    merged = sorted(
        ((parsed, tag) for tag in tag_lines(repo, "--merged", main_ref) if (parsed := parse_tag(tag, prefix))),
        reverse=True,
    )
    above = ",".join(".".join(str(part) for part in parsed) for parsed, _ in merged if parsed > current)
    claimed = ""
    if commit_of(repo, newest_name) == commit and newest[0] > current:
        claimed = f"{major}.{minor}.{patch}"
    new = f"{major}.{minor}.{patch + 1}"
    tag = f"{prefix}{new}"
    existing = commit_of(repo, tag)
    if existing is not None and existing != commit:
        raise VersionError(f"tag {tag} already exists on {existing}, not on {commit}")
    return {"mode": "settings", "version": new, "tag": tag, "claimed": claimed, "above": above}


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
    parser.add_argument("--published", choices=PUBLISHED, default="unknown")
    parser.add_argument("--main-ref", default=MAIN_REF)
    parser.add_argument("--mode", choices=("code", "settings"), default="code")
    parser.add_argument("--commit", default="")
    parser.add_argument("--running", default="")
    args = parser.parse_args(argv[1:])
    if args.mode == "settings":
        try:
            result = plan_settings(Path.cwd(), args.prefix, args.commit, args.running, args.main_ref)
        except VersionError as err:
            print(f"wacd version: {' '.join(str(err).split())}", file=sys.stderr)
            return 1
        for key in ("mode", "version", "tag", "claimed", "above"):
            print(f"{key}={result[key]}")
        return 0
    try:
        result = plan(
            Path.cwd(),
            args.prefix,
            args.version_source,
            args.version,
            args.deploy == "true",
            args.published,
            args.main_ref,
        )
    except VersionError as err:
        message = " ".join(str(err).split())
        print(f"wacd version: {message}", file=sys.stderr)
        return REPLAY_REFUSED_EXIT if isinstance(err, ReplayRefused) else 1
    for key in ("mode", "version", "tag"):
        print(f"{key}={result[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
