#!/usr/bin/env python3
"""Which changed paths are documentation: the one matcher wac-ci and wac-cd use.

A profile's `ci.docs_paths` (hemi's `delivery.docsPaths`) lists repo-relative
patterns. Absent means DEFAULT; `[]` means no change ever counts as
documentation. wacd-profile.py validates the list through check_docs_paths(),
wac-ci's `scope` job (`pr-scope`) decides from it whether a pull request
changed only documentation, and wac-cd's `plan` (`release-scope`) whether a
push did. Each loads this file by its own path, so a `python3 -I` caller
finds it without the scripts' directory on sys.path.

The grammar is not fnmatch. A pattern and a path are split on `/`. `*`
matches any characters within one segment, none included, and never `/`.
`**` as a whole segment matches zero or more segments; `**` inside a segment
is refused. Every other character is literal, and only letters, digits and
`. _ - / *` are allowed, so `?`, `[` and `{` are refused rather than read as
fnmatch would. Matching is case-sensitive, on the exact characters.

A pattern is accepted only when every path it can match is documentation by
name: its first segment is literally `docs`, `doc` or `documentation`; or it
is an exact path (no `*`) that is `mkdocs.yml` or ends in one of
DOC_EXTENSIONS; or its last segment is `*.<ext>` with ext one of
DOC_EXTENSIONS and every earlier segment literal or `**`. It is also refused
when it matches a path the checks or the release read (protected_paths()).

`match <pattern> <path>` checks the pattern's grammar and prints `match=true`
or `match=false`; the case table's `docs-path-match` cases run through it.

`pr-scope` reads the pull request from the runner's own GITHUB_EVENT_NAME and
GITHUB_EVENT_PATH (the event's base ref and sha, head sha and the
repository's default branch, at most MAX_EVENT bytes), the contract job's
DOCS_PATHS and CONTRACT_RESULT, and GITHUB_API_URL, GITHUB_REPOSITORY and
GITHUB_TOKEN, and the changed files from `GET /repos/{repo}/compare/{base}...{head}`, under
COMPARE_TIMEOUT and MAX_COMPARE bytes, with redirects refused so the token
never goes to another host. It prints `heavy=false` only for a pull_request
into the default branch whose comparison is complete (a `files` list of fewer
than MAX_COMPARE_FILES entries, as many commits listed as it counts) and
lists at least one file, none under `.github/`, and every `filename` and
`previous_filename` a match. Anything else, a failure of this script
included, is `heavy=true` with a `reason=` phrase, and the job summary says
which, every time. It always exits 0.

`release-scope --prefix P` runs in the app checkout on a push. The base is
the commit of the newest `P`X.Y.Z tag merged into HEAD that has a GitHub
Release, read newest first with wacd-release.py's release_exists() (rule B's
reader) for at most MAX_RELEASE_READS tags. It prints `docs_only=true` and
`since=<tag>` only when no tag with the prefix is on HEAD and every path of
`git diff --name-status -M <base> HEAD` (both of a rename's) matches DOCS_PATHS
and none is under `.github/`. No Release, a Release that cannot be read, a
diff that fails, lists nothing or more than MAX_DIFF_PATHS paths, and
patterns that cannot be read or are refused all print `docs_only=false` with
a `reason=`, so the release goes ahead as it would have, and so does a
decision not reached within RELEASE_SCOPE_SECONDS: each git call is bounded
by the time left, and a Release is read only while a whole read fits in it.
It always exits 0.

Paths printed in a reason or a summary are escaped as workflow-command data.
"""

import argparse
import http.client
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT = ("docs/**", "mkdocs.yml", "*.md")
DOC_DIRS = ("docs", "doc", "documentation")
DOC_EXTENSIONS = ("md", "mdx", "rst", "txt", "adoc")
ALLOWED = re.compile(r"[A-Za-z0-9._*/-]+")
PACKAGE_FILES = ("package.json", "package-lock.json", "go.mod", "go.sum")
PROFILE_FILE = ".github/wacd.json"
GITHUB_DIR = ".github/"
TOKEN_SPLIT = re.compile(r"[\s;|&()<>]+")
SHA40 = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
MAX_EVENT = 8 * 1024 * 1024
COMPARE_TIMEOUT = 20
MAX_COMPARE = 32 * 1024 * 1024
MAX_COMPARE_FILES = 300
MAX_RELEASE_READS = 10
MAX_DIFF_PATHS = 3000
GIT_SECONDS = 30
RELEASE_SCOPE_SECONDS = 150
SHOWN = 3

SKIPPED = "Only documentation changed, so the checks that build and test the app did not run."
RAN = "The checks ran because: {reason}"
REASONS = {
    "not_pr": "this is not a pull request into the default branch",
    "no_paths": "the documentation paths could not be read",
    "refused_paths": "a documentation path was refused",
    "never": "this repository sets no documentation paths",
    "unreadable": "the changed files could not be read",
    "incomplete": "the list of changed files may be incomplete",
    "nothing": "the comparison lists no changed file",
    "github": "a file under .github/ changed: {paths}",
    "outside": "a file outside the documentation paths changed: {paths}",
}
RELEASE_REASONS = {
    "tagged": "HEAD already carries a {prefix}X.Y.Z tag",
    "no_paths": "the documentation paths could not be read",
    "never": "this repository sets no documentation paths",
    "no_release": "no version of this app has a GitHub Release yet",
    "unread_release": "the GitHub Releases could not be read",
    "too_many_releases": "none of the newest {count} versions has a GitHub Release",
    "diff": "the changes since {tag} could not be read",
    "nothing": "nothing changed since {tag}",
    "too_many": "more than {count} paths changed since {tag}",
    "github": "a file under .github/ changed since {tag}",
    "outside": "a file outside the documentation paths changed since {tag}",
    "late": "the changes since the last release could not be read in time",
}


class ProfileError(Exception):
    """A profile or pattern problem; the message starts with the dotted field path.

    wacd-profile.py raises this class too (its ProfileError is this one), so a
    refused pattern reads and is caught like any other profile refusal.
    """


def fail(path, message):
    raise ProfileError(f"{path}: {message}")


def escape(text):
    """Workflow-command data, escaped as the runner unescapes it, so a path cannot end a line."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def check_grammar(pattern, path):
    """A relative pattern in the grammar: allowed characters, no empty, . or .. segment, ** whole."""
    if not ALLOWED.fullmatch(pattern) or any(part in ("", ".", "..") for part in pattern.split("/")):
        fail(path, f"{pattern!r} must be a path relative to the repository root, using only letters, "
             "digits and . _ - / *, with no empty, . or .. segment")
    if any("**" in part and part != "**" for part in pattern.split("/")):
        fail(path, f"{pattern!r} may use ** only as a whole segment")


def is_documentation(pattern):
    """Whether every path the (grammatical) pattern can match is documentation by name."""
    parts = pattern.split("/")
    if parts[0] in DOC_DIRS:
        return True
    if "*" not in pattern:
        return pattern == "mkdocs.yml" or parts[-1].endswith(tuple("." + ext for ext in DOC_EXTENSIONS))
    return parts[-1] in tuple("*." + ext for ext in DOC_EXTENSIONS) and all(
        part == "**" or "*" not in part for part in parts[:-1]
    )


def check_documentation(pattern, path):
    if not is_documentation(pattern):
        fail(path, f"{pattern!r} could match a file that is not documentation; a pattern must start with "
             "docs/, doc/ or documentation/, name one .md, .mdx, .rst, .txt or .adoc file or mkdocs.yml, "
             "or end in *.md, *.mdx, *.rst, *.txt or *.adoc below literal or ** segments")


def segment_matches(pattern, segment):
    regex = "".join("[^/]*" if char == "*" else re.escape(char) for char in pattern)
    return re.fullmatch(regex, segment, re.DOTALL) is not None


def matches(pattern, path):
    """Whether path matches pattern, which is in the grammar."""
    want, have = pattern.split("/"), path.split("/")
    reach = {0}
    for part in want:
        if part == "**":
            reach = set(range(min(reach), len(have) + 1)) if reach else set()
        else:
            reach = {i + 1 for i in reach if i < len(have) and segment_matches(part, have[i])}
        if not reach:
            return False
    return len(have) in reach


def normalized(path):
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def command_paths(command):
    """Each path-like token of a gate command: it holds / or . once its quotes are gone."""
    found = []
    for token in TOKEN_SPLIT.split(command):
        token = normalized(token.replace("'", "").replace('"', "").replace("`", ""))
        if token and ("/" in token or "." in token):
            found.append(token)
    return found


def protected_paths(profile):
    """The paths the checks or the release read, in a fixed order, from a profile-shaped dict."""
    paths = [PROFILE_FILE]
    if isinstance(profile.get("values_file"), str):
        paths.append(normalized(profile["values_file"]))
    contexts = [""]
    for image in profile.get("images") or []:
        if isinstance(image, dict):
            if isinstance(image.get("dockerfile"), str):
                paths.append(normalized(image["dockerfile"]))
            if isinstance(image.get("context"), str):
                context = normalized(image["context"])
                contexts.append("" if context == "." else context)
    for context in dict.fromkeys(contexts):
        paths.extend(f"{context}/{name}" if context else name for name in PACKAGE_FILES)
    ci = profile.get("ci") if isinstance(profile.get("ci"), dict) else {}
    for command in ci.get("commands") or []:
        if isinstance(command, str):
            paths.extend(command_paths(command))
    return list(dict.fromkeys(paths))


def check_docs_paths(ci, path, profile):
    """The effective documentation patterns of a profile's ci, refusing any that could match code."""
    here = f"{path}.docs_paths"
    if "docs_paths" not in ci:
        patterns, explicit = list(DEFAULT), False
    else:
        patterns, explicit = ci["docs_paths"], True
        if not isinstance(patterns, list):
            fail(here, "must be a list of patterns, or [] for none")
    for i, pattern in enumerate(patterns):
        if not isinstance(pattern, str) or not pattern:
            fail(f"{here}[{i}]", "must be a non-empty string")
        check_grammar(pattern, f"{here}[{i}]")
        check_documentation(pattern, f"{here}[{i}]")
    protected = protected_paths(profile)
    for i, pattern in enumerate(patterns):
        hit = next((target for target in protected if matches(pattern, target)), None)
        if hit is None:
            continue
        if explicit:
            fail(f"{here}[{i}]", f"{pattern!r} matches {hit}, which the checks or the release read, "
                 "so it is not documentation")
        fail(here, f"the default pattern {pattern!r} matches {hit}, which the checks or the release read; "
             "set ci.docs_paths without it")
    return list(patterns)


def read_patterns(text):
    """The patterns a profile already validated, as the scope steps receive them; refuses anything else."""
    try:
        patterns = json.loads(text)
    except ValueError:
        fail("docs_paths", "is not JSON")
    if not isinstance(patterns, list):
        fail("docs_paths", "must be a list of patterns, or [] for none")
    for i, pattern in enumerate(patterns):
        if not isinstance(pattern, str) or not pattern:
            fail(f"docs_paths[{i}]", "must be a non-empty string")
        check_grammar(pattern, f"docs_paths[{i}]")
        check_documentation(pattern, f"docs_paths[{i}]")
    return patterns


def listed(paths):
    shown = ", ".join(escape(path) for path in paths[:SHOWN])
    return shown if len(paths) <= SHOWN else f"{shown}, and {len(paths) - SHOWN} more"


def judge(paths, patterns):
    """None when every path is documentation, else (key, offending paths)."""
    github = [path for path in paths if path.startswith(GITHUB_DIR)]
    if github:
        return "github", github
    outside = [path for path in paths if not any(matches(pattern, path) for pattern in patterns)]
    if outside:
        return "outside", outside
    return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Unread(Exception):
    """The comparison could not be read; the key names why."""


def compare(base, head):
    """GitHub's comparison of base...head, as JSON, or Unread."""
    api = os.environ.get("GITHUB_API_URL", "").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    parsed = urllib.parse.urlsplit(api)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or not REPOSITORY.fullmatch(repository) \
            or not token:
        raise Unread("unreadable")
    request = urllib.request.Request(f"{api}/repos/{repository}/compare/{base}...{head}", method="GET")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=COMPARE_TIMEOUT) as response:
            body = response.read(MAX_COMPARE + 1)
            status = response.status
    except urllib.error.HTTPError as error:
        error.close()
        raise Unread("unreadable") from None
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
        raise Unread("unreadable") from None
    if status != 200 or len(body) > MAX_COMPARE:
        raise Unread("unreadable")
    try:
        return json.loads(body)
    except ValueError:
        raise Unread("unreadable") from None


def changed_files(answer):
    """Every filename and previous_filename of a complete comparison, or Unread."""
    if not isinstance(answer, dict):
        raise Unread("unreadable")
    files, commits, total = answer.get("files"), answer.get("commits"), answer.get("total_commits")
    if not isinstance(files, list) or not isinstance(commits, list) or type(total) is not int:
        raise Unread("incomplete")
    if len(files) >= MAX_COMPARE_FILES or total != len(commits):
        raise Unread("incomplete")
    paths = []
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str) or not entry["filename"]:
            raise Unread("unreadable")
        paths.append(entry["filename"])
        if "previous_filename" in entry:
            previous = entry["previous_filename"]
            if not isinstance(previous, str) or not previous:
                raise Unread("unreadable")
            paths.append(previous)
    return paths


def pull_request_event(environ):
    """(base ref, base sha, head sha, default branch) of a pull_request event, or None."""
    if environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return None
    try:
        with open(environ.get("GITHUB_EVENT_PATH", ""), "rb") as handle:
            raw = handle.read(MAX_EVENT + 1)
        event = json.loads(raw) if len(raw) <= MAX_EVENT else None
        pull, repository = event["pull_request"], event["repository"]
        values = (pull["base"]["ref"], pull["base"]["sha"], pull["head"]["sha"], repository["default_branch"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return values if all(isinstance(value, str) and value for value in values) else None


def pr_decision(environ):
    """(heavy, reason); heavy is False only when the pull request changed nothing but documentation."""
    event = pull_request_event(environ)
    if event is None:
        return True, REASONS["not_pr"]
    base_ref, base, head, default = event
    if base_ref != default:
        return True, REASONS["not_pr"]
    text = environ.get("DOCS_PATHS", "")
    if environ.get("CONTRACT_RESULT") != "success" or not text:
        return True, REASONS["no_paths"]
    try:
        patterns = read_patterns(text)
    except ProfileError:
        return True, REASONS["refused_paths"]
    if not patterns:
        return True, REASONS["never"]
    if not SHA40.fullmatch(base) or not SHA40.fullmatch(head):
        return True, REASONS["unreadable"]
    try:
        paths = changed_files(compare(base, head))
    except Unread as error:
        return True, REASONS[str(error)]
    if not paths:
        return True, REASONS["nothing"]
    verdict = judge(paths, patterns)
    if verdict is not None:
        key, offending = verdict
        return True, REASONS[key].format(paths=listed(offending))
    return False, ""


def summarize(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass


def main_pr_scope():
    try:
        heavy, reason = pr_decision(os.environ)
    except Exception:
        heavy, reason = True, REASONS["unreadable"]
    sentence = RAN.format(reason=reason) if heavy else SKIPPED
    summarize(["### wac-ci scope", "", sentence])
    print(f"heavy={'true' if heavy else 'false'}\nreason={reason}")
    if not heavy:
        print(f"::notice::wac-ci: {SKIPPED}", file=sys.stderr)
    return 0


DEADLINE = [None]


def left():
    return float("inf") if DEADLINE[0] is None else DEADLINE[0] - time.monotonic()


def git(*args):
    timeout = min(GIT_SECONDS, left())
    if timeout <= 0:
        raise Unread("late")
    try:
        result = subprocess.run(["git", *args], capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise Unread("late") from None
    if result.returncode != 0:
        raise Unread("diff")
    return result.stdout


def version_tags(prefix, ref):
    pattern = re.compile(re.escape(prefix) + r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
    found = []
    for tag in git("tag", "--merged", ref).decode("utf-8", "replace").splitlines():
        match = pattern.fullmatch(tag)
        if match:
            found.append((tuple(int(part) for part in match.groups()), tag))
    return [tag for _, tag in sorted(found, reverse=True)], pattern


def load_release():
    spec = importlib.util.spec_from_file_location("wacd_docs_release", Path(__file__).with_name("wacd-release.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def diff_paths(base):
    """Both names of each change between base and HEAD, from git's -z name-status output."""
    fields = git("diff", "--name-status", "-M", "-z", base, "HEAD").split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    paths, index = [], 0
    while index < len(fields):
        status = fields[index].decode("ascii", "replace")
        names = 2 if status[:1] in ("R", "C") else 1
        if not status or index + names >= len(fields):
            raise Unread("diff")
        for name in fields[index + 1:index + 1 + names]:
            paths.append(os.fsdecode(name))
        index += 1 + names
        if len(paths) > MAX_DIFF_PATHS:
            raise Unread("too_many")
    return paths


def release_decision(prefix, environ):
    """(since tag, "") when only documentation changed since the newest Released version, else ("", reason)."""
    tags, pattern = version_tags(prefix, "HEAD")
    on_head = git("tag", "--points-at", "HEAD").decode("utf-8", "replace").splitlines()
    if any(pattern.fullmatch(tag) for tag in on_head):
        return "", RELEASE_REASONS["tagged"].format(prefix=prefix)
    text = environ.get("DOCS_PATHS", "")
    if not text:
        return "", RELEASE_REASONS["no_paths"]
    try:
        patterns = read_patterns(text)
    except ProfileError:
        return "", RELEASE_REASONS["no_paths"]
    if not patterns:
        return "", RELEASE_REASONS["never"]
    if not tags:
        return "", RELEASE_REASONS["no_release"]
    release = load_release()
    base = None
    for tag in tags[:MAX_RELEASE_READS]:
        if left() < release.GITHUB_TIMEOUT:
            return "", RELEASE_REASONS["late"]
        try:
            if release.release_exists(tag):
                base = tag
                break
        except release.ReleaseError:
            return "", RELEASE_REASONS["unread_release"]
    if base is None:
        if len(tags) > MAX_RELEASE_READS:
            return "", RELEASE_REASONS["too_many_releases"].format(count=MAX_RELEASE_READS)
        return "", RELEASE_REASONS["no_release"]
    try:
        paths = diff_paths(f"refs/tags/{base}^{{commit}}")
    except Unread as error:
        return "", RELEASE_REASONS[str(error)].format(tag=base, count=MAX_DIFF_PATHS)
    if not paths:
        return "", RELEASE_REASONS["nothing"].format(tag=base)
    verdict = judge(paths, patterns)
    if verdict is not None:
        return "", RELEASE_REASONS[verdict[0]].format(tag=base)
    return base, ""


def main_release_scope(prefix):
    DEADLINE[0] = time.monotonic() + RELEASE_SCOPE_SECONDS
    try:
        since, reason = release_decision(prefix, os.environ)
    except Unread as error:
        since, reason = "", RELEASE_REASONS["late" if str(error) == "late" else "diff"].format(tag="the last release")
    except Exception:
        since, reason = "", "the changes since the last release could not be read"
    if since:
        print(f"docs_only=true\nsince={since}")
    else:
        print(f"docs_only=false\nreason={escape(reason)}")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="wacd-docs.py", description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    match_parser = commands.add_parser("match", help="whether a path matches a pattern")
    match_parser.add_argument("pattern")
    match_parser.add_argument("path")
    commands.add_parser("pr-scope", help="whether a pull request changed only documentation (wac-ci scope)")
    release_parser = commands.add_parser("release-scope", help="whether a push changed only documentation (wac-cd plan)")
    release_parser.add_argument("--prefix", required=True)
    args = parser.parse_args(argv[1:])
    if args.command == "pr-scope":
        return main_pr_scope()
    if args.command == "release-scope":
        return main_release_scope(args.prefix)
    try:
        check_grammar(args.pattern, "pattern")
    except ProfileError as error:
        print(f"wacd docs: {error}", file=sys.stderr)
        return 1
    print(f"match={'true' if matches(args.pattern, args.path) else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
