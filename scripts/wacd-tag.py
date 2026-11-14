#!/usr/bin/env python3
"""Push wac-cd's release tag, accepting one that already names the same commit.

Run inside the app checkout, whose remote carries credentials that may push
tags. `push-tag --tag T --sha S`:

- T absent on the remote: create a lightweight tag T at S (or reuse a local T
  already at S) and push it;
- T on the remote at S, lightweight or annotated: accept it, change nothing;
- T on the remote, or locally, at another commit: fail, naming both commits.

`claim-tag --tag T --sha S [--branch main]` is what wac-cd's plan job runs
before it builds anything. It does what push-tag does, and when the remote
refuses the push outright (GitHub refuses a GITHUB_TOKEN tag push at a commit
whose workflow files differ from the default branch's), it reads the branch tip
on the remote:

- tip is no longer S: the run is superseded by a newer commit's run, which
  releases these commits with its own; print claim=superseded and exit 0;
- tip is still S: fail, as push-tag does.

claim-tag prints one human line, then claim=pushed, claim=exists or
claim=superseded.

A published tag is never moved, deleted or forced. Any problem exits 1 with one
"wacd tag: ..." stderr line.
"""

import argparse
import re
import subprocess
import sys

SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
REMOTE_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


class TagError(Exception):
    pass


class PushRefused(TagError):
    pass


def git(*args, check=True):
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        raise TagError("git is not installed") from None
    if check and result.returncode != 0:
        detail = " ".join((result.stderr or "").split())[:200]
        raise TagError(f"git {args[0]} failed: {detail}")
    return result


def remote_commit(remote, tag):
    """The commit tag names on the remote, peeling an annotated tag; None when absent."""
    ref = f"refs/tags/{tag}"
    out = git("ls-remote", "--tags", remote, ref, ref + "^{}").stdout
    found = {}
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        found[name.strip()] = sha.strip()
    return found.get(ref + "^{}") or found.get(ref)


def local_commit(tag):
    result = git("rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}", check=False)
    return result.stdout.strip() or None


def validate(remote, tag, sha):
    if not SHA_PATTERN.fullmatch(sha):
        raise TagError("--sha must be 40 lowercase hex characters")
    if not REMOTE_PATTERN.fullmatch(remote):
        raise TagError("--remote is not a valid remote name")
    if not tag or tag.startswith("-") or any(c.isspace() for c in tag):
        raise TagError("--tag is not a valid tag name")
    if git("check-ref-format", f"refs/tags/{tag}", check=False).returncode != 0:
        raise TagError("--tag is not a valid tag name")
    if git("cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode != 0:
        raise TagError(f"{sha} is not a commit in this checkout")


def push_tag(remote, tag, sha):
    return push_tag_outcome(remote, tag, sha)[1]


def push_tag_outcome(remote, tag, sha):
    """Push tag at sha; return ("pushed" or "exists", message)."""
    validate(remote, tag, sha)
    existing = remote_commit(remote, tag)
    if existing == sha:
        return "exists", f"Tag {tag} already exists at {sha}"
    if existing is not None:
        raise TagError(f"tag {tag} already exists on {existing}, not {sha}")
    local = local_commit(tag)
    if local is not None and local != sha:
        raise TagError(f"local tag {tag} is on {local}, not {sha}")
    if local is None:
        git("tag", tag, sha)
    pushed = git("push", remote, f"refs/tags/{tag}", check=False)
    if pushed.returncode != 0:
        # A concurrent run may have pushed the same tag first; that is fine
        # only when it names this commit.
        raced = remote_commit(remote, tag)
        if raced == sha:
            return "exists", f"Tag {tag} already exists at {sha}"
        detail = " ".join((pushed.stderr or "").split())[:200]
        if raced is not None:
            raise TagError(f"tag {tag} already exists on {raced}, not {sha}")
        raise PushRefused(f"git push failed: {detail}")
    return "pushed", f"Pushed tag {tag} at {sha}"


def branch_tip(remote, branch):
    ref = f"refs/heads/{branch}"
    out = git("ls-remote", "--heads", remote, ref).stdout
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name.strip() == ref:
            return sha.strip()
    return None


def claim_tag(remote, tag, sha, branch):
    """Return the lines claim-tag prints: one human line, then claim=<outcome>."""
    if branch.startswith("-") or git("check-ref-format", "--branch", branch, check=False).returncode != 0:
        raise TagError("--branch is not a valid branch name")
    try:
        outcome, message = push_tag_outcome(remote, tag, sha)
    except PushRefused as refused:
        tip = branch_tip(remote, branch)
        if tip is None:
            raise TagError(f"{refused}; and {branch} is absent on {remote}") from None
        if tip == sha:
            raise TagError(f"{refused}; {sha} is still the tip of {branch}") from None
        message = (
            f"Tag {tag} push was refused and {branch} has moved from {sha} to {tip}; "
            f"this run is superseded and the run for {tip} releases these commits"
        )
        return [message, "claim=superseded"]
    return [message, f"claim={outcome}"]


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise TagError("invalid arguments: " + message)


def build_parser():
    parser = Parser(prog="wacd-tag.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    push = commands.add_parser("push-tag")
    push.add_argument("--tag", required=True)
    push.add_argument("--sha", required=True)
    push.add_argument("--remote", default="origin")
    claim = commands.add_parser("claim-tag")
    claim.add_argument("--tag", required=True)
    claim.add_argument("--sha", required=True)
    claim.add_argument("--branch", default="main")
    claim.add_argument("--remote", default="origin")
    return parser


def main(argv):
    try:
        args = build_parser().parse_args(argv[1:])
        if args.command == "claim-tag":
            lines = claim_tag(args.remote, args.tag, args.sha, args.branch)
        else:
            lines = [push_tag(args.remote, args.tag, args.sha)]
    except TagError as err:
        print("wacd tag: " + " ".join(str(err).split()), file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
