#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml==6.0.2"]
# ///
"""Render and check a fleet repo's AGENTS.md.

AGENTS.md is the one repo context file every fleet repo carries: hand-written
prose on top, a generated tail underneath. The tail -- everything from
`## Dependencies` to the end of the file -- is rendered from the repo's
`catalog-info.yaml` and from `agents-md/fleet-rules.md` in this repo, at the
tag the repo's CI pins. A stale file is worse than none, because an agent
trusts it; so `check` fails the moment the file and the repo disagree.

    uv run scripts/agents-md.py render    # rewrite the generated tail in place
    uv run scripts/agents-md.py check     # fail on any drift; GitHub annotations

Both operate on the current directory as the target repo root, or on
`--root PATH`. `check` reads the working tree and asks git which paths are
tracked or ignored; it never touches the network.

Command paths are resolved against what git tracks, not against the local
disk, so a check that passes on a developer's machine passes on a fresh CI
checkout too. A path git ignores is treated as a build output and left
unchecked.

The contract itself is the design doc's, in wac.docs:
docs/superpowers/specs/2026-10-02-agents-md-knowledge-layer-design.md.
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import json
import posixpath
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FLEET_RULES = Path(__file__).resolve().parent.parent / "agents-md" / "fleet-rules.md"

AGENTS_MD = "AGENTS.md"
CATALOG = "catalog-info.yaml"

#: Context files that would make Claude Code skip AGENTS.md entirely: it reads
#: AGENTS.md only when no CLAUDE.md or CLAUDE.local.md exists in the working
#: directory or above it. Matched on basename, so `.claude/CLAUDE.md` counts.
FORBIDDEN_CONTEXT_FILES = ("CLAUDE.md", "CLAUDE.local.md")

#: The seven H2 sections every root AGENTS.md carries, in this relative order.
#: A repo may add its own H2s anywhere before `## Dependencies`.
REQUIRED_H2 = (
    "The one thing to understand",
    "Commands",
    "Layout",
    "Invariants",
    "Boundaries",
    "Dependencies",
    "Fleet rules",
)
TAIL_H2 = "Dependencies"
LAST_H2 = "Fleet rules"
COMMANDS_H2 = "Commands"

CATALOG_URL = "https://docs.wacwini.com/catalog/default/{kind}/{name}"

TAIL_PREAMBLE = (
    "Generated from `catalog-info.yaml` by wac.lab.actions "
    "`scripts/agents-md.py render`. Change the catalog and re-render; do not "
    "edit this section or the next by hand."
)
INCOMING_NOTE = (
    "Incoming edges (what depends on this repo) are not listed here. They live "
    "in the catalog at docs.wacwini.com."
)

#: Backstage's default kind for a bare ref, per relation field.
REF_FIELDS = (
    ("Depends on", "dependsOn", "component"),
    ("Provides APIs", "providesApis", "api"),
    ("Consumes APIs", "consumesApis", "api"),
)
COMPONENT_FACTS = ("system", "type", "lifecycle")

BOM = "﻿"


class GateError(Exception):
    """A problem that stops `render` outright, or that `check` reports as findings."""

    def __init__(self, *messages: str) -> None:
        super().__init__("; ".join(messages))
        self.messages = list(messages)


# --------------------------------------------------------------------------
# Markdown scanning
# --------------------------------------------------------------------------

#: An ATX heading. The `#` run must be followed by whitespace or end of line,
#: so `#hashtag` is a paragraph, as CommonMark has it.
ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
#: A setext underline, which turns the paragraph above it into an H1 (`=`) or
#: an H2 (`-`). GitHub renders these as headings, so the contract reads them too.
SETEXT_UNDERLINE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
THEMATIC_BREAK = re.compile(r"^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")
#: Lines that open some other block -- list items, block quotes, HTML, table
#: rows -- and so end a paragraph rather than becoming part of a heading.
BLOCK_START = re.compile(r"^ {0,3}(?:[-*+](?:[ \t]|$)|\d{1,9}[.)](?:[ \t]|$)|>|<|\|)")
#: Indented code: cannot start a paragraph, but continues one.
INDENTED = re.compile(r"^(?: {4}|\t)")
#: A fence opener. Indentation is not limited to three spaces, so a fence
#: nested in a list item is still seen as a fence.
FENCE_OPEN = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")
FENCE_CLOSE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*$")


@dataclass
class Heading:
    level: int
    text: str
    line: int

    def markup(self) -> str:
        return f"{'#' * self.level} {self.text}"


@dataclass
class Fence:
    start: int
    info: str
    body: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class Markdown:
    headings: list[Heading]
    fences: list[Fence]
    #: Line of a fence opener that is never closed, if any.
    unclosed: int | None


def scan_markdown(lines: list[str]) -> Markdown:
    """Headings outside fenced code blocks, and every fenced block's body.

    Line numbers are 1-based. A `# note` inside a fence is code, not an H1;
    that is the whole reason this is a scanner rather than a regex over the file.
    """
    headings: list[Heading] = []
    fences: list[Fence] = []
    open_fence: tuple[str, int, Fence] | None = None
    paragraph: tuple[int, list[str]] | None = None
    for number, line in enumerate(lines, 1):
        if open_fence is not None:
            char, length, fence = open_fence
            closing = FENCE_CLOSE.match(line)
            if closing and closing.group(1)[0] == char and len(closing.group(1)) >= length:
                fences.append(fence)
                open_fence = None
            else:
                fence.body.append((number, line))
            continue

        opening = FENCE_OPEN.match(line)
        # A backtick fence's info string may not itself hold a backtick;
        # without this, inline code like ```foo``` would open a block.
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            info = opening.group(2).strip()
            open_fence = (opening.group(1)[0], len(opening.group(1)), Fence(number, info))
            paragraph = None
            continue

        if not line.strip():
            paragraph = None
            continue

        atx = ATX_HEADING.match(line)
        if atx:
            text = re.sub(r"(?:^|[ \t]+)#+$", "", atx.group(2) or "").strip()
            headings.append(Heading(len(atx.group(1)), text, number))
            paragraph = None
            continue

        underline = SETEXT_UNDERLINE.match(line)
        if underline and paragraph is not None:
            level = 1 if underline.group(1)[0] == "=" else 2
            headings.append(Heading(level, " ".join(paragraph[1]).strip(), paragraph[0]))
            paragraph = None
            continue

        if THEMATIC_BREAK.match(line):
            paragraph = None
            continue

        if BLOCK_START.match(line):
            # A list item or quote ends any paragraph above it, and a setext
            # underline below it is a thematic break, not a heading.
            paragraph = None
        elif paragraph is None:
            if not INDENTED.match(line):
                paragraph = (number, [line.strip()])
        else:
            paragraph[1].append(line.strip())

    unclosed = None
    if open_fence is not None:
        unclosed = open_fence[2].start
        fences.append(open_fence[2])
    return Markdown(headings, fences, unclosed)


def first_tail_heading(md: Markdown) -> Heading | None:
    """The first `## Dependencies` outside a fence: where the generated tail starts."""
    return next((h for h in md.headings if h.level == 2 and h.text == TAIL_H2), None)


def tail_intruders(md: Markdown) -> list[Heading]:
    """H1 and H2 headings below `## Dependencies` that `render` does not write.

    Everything from `## Dependencies` on is rewritten by `render`, so a section
    found there is hand-written prose that a render would destroy. The one
    `## Fleet rules` is the only H2 the generated tail itself holds.
    """
    tail = first_tail_heading(md)
    if tail is None:
        return []
    intruders = []
    fleet_rules_seen = False
    for heading in md.headings:
        if heading.line <= tail.line or heading.level > 2:
            continue
        if heading.level == 2 and heading.text == LAST_H2 and not fleet_rules_seen:
            fleet_rules_seen = True
            continue
        intruders.append(heading)
    return intruders


def normalize(text: str) -> str:
    """Drop a UTF-8 byte-order mark and convert CRLF line endings to LF."""
    if text.startswith(BOM):
        text = text[len(BOM) :]
    return text.replace("\r\n", "\n")


# --------------------------------------------------------------------------
# The catalog
# --------------------------------------------------------------------------


def _split_ref(ref: str, default_kind: str) -> tuple[str, str]:
    text = ref.strip()
    if ":" in text:
        kind, rest = text.split(":", 1)
        kind = kind.strip().lower()
    else:
        kind, rest = default_kind, text
    rest = rest.strip()
    if "/" in rest:
        namespace, name = rest.split("/", 1)
        if namespace == "default":
            rest = name
    return kind, rest


def normalize_ref(ref: str, default_kind: str) -> str:
    """`kind:name` with the kind lowercased and a `default/` namespace dropped.

    A bare name gets the field's default kind, as Backstage resolves it. Names
    keep their case: Backstage compares them case-insensitively, but the
    catalog shows them as written, and so does this file.
    """
    kind, name = _split_ref(ref, default_kind)
    return f"{kind}:{name}"


def _is_component(doc: dict) -> bool:
    return doc["kind"].lower() == "component"


def _entity_ref(doc: dict) -> str:
    metadata = doc["metadata"]
    namespace = metadata.get("namespace")
    name = metadata["name"]
    if namespace and namespace != "default":
        name = f"{namespace}/{name}"
    return f"{doc['kind'].lower()}:{name}"


def _validate_entity(index: int, doc: object) -> list[str]:
    """Every shape `render` relies on, so a malformed catalog is a finding, not a traceback."""
    where = f"document {index}"
    if not isinstance(doc, dict):
        return [f"{where} is not a mapping"]
    kind = doc.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        return [f"{where} has no string `kind`"]
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return [f"{where} (`{kind}`): `metadata` is missing or not a mapping"]
    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip():
        return [f"{where} (`{kind}`): `metadata.name` is missing or not a string"]

    where = f"`{kind.lower()}:{name}`"
    problems = []
    for key in ("description", "namespace"):
        value = metadata.get(key)
        if value is not None and not isinstance(value, str):
            problems.append(f"{where}: `metadata.{key}` is not a string")
    spec = doc.get("spec")
    if spec is None:
        return problems
    if not isinstance(spec, dict):
        problems.append(f"{where}: `spec` is not a mapping")
        return problems
    for key in COMPONENT_FACTS:
        value = spec.get(key)
        if value is not None and not isinstance(value, str):
            problems.append(f"{where}: `spec.{key}` is not a string")
    for _, key, default_kind in REF_FIELDS:
        values = spec.get(key)
        if values is None:
            continue
        if not isinstance(values, list):
            problems.append(f"{where}: `spec.{key}` is not a list")
            continue
        for position, value in enumerate(values, 1):
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{where}: `spec.{key}` item {position} is not a string ref")
                continue
            ref_kind, ref_name = _split_ref(value, default_kind)
            if not ref_kind or not ref_name:
                problems.append(f"{where}: `spec.{key}` item {position} (`{value}`) is not a `kind:name` ref")
    return problems


def load_catalog(root: Path) -> list[dict]:
    """The catalog's entities, validated. Raises GateError naming every problem."""
    path = root / CATALOG
    if not path.is_file():
        raise GateError(f"no `{CATALOG}` at the repo root")
    try:
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise GateError(f"`{CATALOG}` does not parse: {exc}") from exc
    problems = []
    entities = []
    for index, doc in enumerate(docs, 1):
        if doc is None:
            # An empty document, e.g. from a trailing `---`.
            continue
        found = _validate_entity(index, doc)
        if found:
            problems += found
        else:
            entities.append(doc)
    if problems:
        raise GateError(*problems)
    if not any(_is_component(doc) for doc in entities):
        raise GateError(
            f"`{CATALOG}` declares no Component, so there is nothing to render a "
            "Dependencies section from; every fleet repo is at least one Component"
        )
    return entities


# --------------------------------------------------------------------------
# Rendering the tail
# --------------------------------------------------------------------------


def _one_line(text: str) -> str:
    # A folded or literal YAML description can span lines; the tail never wraps.
    return " ".join(text.split())


def _component_sentence(doc: dict) -> str:
    spec = doc.get("spec") or {}
    parts = []
    description = _one_line(doc["metadata"].get("description") or "")
    if description:
        parts.append(description if description[-1] in ".!?" else description + ".")
    facts = [f"{key} `{_one_line(spec[key])}`" for key in COMPONENT_FACTS if (spec.get(key) or "").strip()]
    if facts:
        joined = ", ".join(facts)
        parts.append(joined[0].upper() + joined[1:] + ".")
    return " ".join(parts)


def _ref_list(values: list[str] | None, default_kind: str) -> str:
    refs = sorted({normalize_ref(value, default_kind) for value in values or []})
    if not refs:
        return "(none)"
    return ", ".join(f"`{ref}`" for ref in refs)


def read_fleet_rules() -> str:
    try:
        text = FLEET_RULES.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise GateError(f"cannot read the fleet rules at {FLEET_RULES}: {exc}") from exc
    text = text.replace("\r\n", "\n").strip("\n").rstrip()
    if not text:
        raise GateError(f"the fleet rules at {FLEET_RULES} are empty")
    return text


def render_tail(entities: list[dict], fleet_rules: str) -> str:
    """Everything from `## Dependencies` to end of file. Byte-stable."""
    out = ["## Dependencies", "", TAIL_PREAMBLE, ""]

    for doc in (d for d in entities if _is_component(d)):
        name = doc["metadata"]["name"]
        out += [f"### Component `{name}`", ""]
        sentence = _component_sentence(doc)
        if sentence:
            out.append(sentence)
        out.append(CATALOG_URL.format(kind="component", name=name))
        out.append("")
        spec = doc.get("spec") or {}
        for label, key, default_kind in REF_FIELDS:
            out.append(f"- {label}: {_ref_list(spec.get(key), default_kind)}")
        out.append("")

    others = [d for d in entities if not _is_component(d)]
    if others:
        out += ["### Also declared here", ""]
        for doc in others:
            description = _one_line(doc["metadata"].get("description") or "")
            ref = _entity_ref(doc)
            out.append(f"- `{ref}`: {description}" if description else f"- `{ref}`")
        out.append("")

    out += [INCOMING_NOTE, "", f"## {LAST_H2}", "", fleet_rules]
    return "\n".join(out) + "\n"


def compose(text: str, tail: str) -> str:
    """The prose head of a normalized file, with `tail` in place of its old tail.

    The head is kept as written, except that its trailing whitespace becomes
    exactly one blank line before `## Dependencies`.
    """
    lines = text.split("\n")
    tail_heading = first_tail_heading(scan_markdown(lines))
    head_lines = lines if tail_heading is None else lines[: tail_heading.line - 1]
    head = "\n".join(head_lines).rstrip()
    return f"{head}\n\n{tail}" if head else tail


def _intruder_list(intruders: list[Heading]) -> str:
    return ", ".join(f"line {h.line} `{h.markup()}`" for h in intruders)


def render_text(text: str, entities: list[dict], fleet_rules: str) -> str:
    """What `render` writes for `text`. Raises GateError rather than lose prose.

    The output is re-read before it is returned: if a second render would not
    reproduce it byte for byte, nothing is written.
    """
    norm = normalize(text)
    md = scan_markdown(norm.split("\n"))
    if md.unclosed is not None:
        raise GateError(
            f"unclosed code fence opened at line {md.unclosed}; close it first, or "
            "render cannot tell prose from the generated tail"
        )
    intruders = tail_intruders(md)
    if intruders:
        raise GateError(
            "these headings sit below `## Dependencies`, where render rewrites "
            f"everything; move them above `## Dependencies` first: {_intruder_list(intruders)}"
        )
    tail = render_tail(entities, fleet_rules)
    out = compose(norm, tail)
    again = scan_markdown(out.split("\n"))
    if again.unclosed is not None or tail_intruders(again) or compose(out, tail) != out:
        raise GateError(
            "the rendered tail would not survive a second render: a catalog "
            "description or the fleet rules hold a line that reads as a heading "
            "or a code fence"
        )
    return out


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


@dataclass
class Finding:
    file: str
    line: int | None
    message: str
    detail: str | None = None

    def annotation(self) -> str:
        props = f"file={_escape_property(self.file)}"
        if self.line is not None:
            props += f",line={self.line}"
        return f"::error {props}::{_escape_data(self.message)}"

    def human(self) -> str:
        where = self.file if self.line is None else f"{self.file}:{self.line}"
        return f"{where}: {self.message}"


def _escape_data(text: str) -> str:
    # GitHub workflow-command escaping: an unescaped newline ends the command.
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


# --------------------------------------------------------------------------
# What git tracks
# --------------------------------------------------------------------------

FILE, DIR, ANY, NODE = "file", "dir", "any", "node"
NODE_SUFFIXES = (".js", ".mjs", ".cjs", ".json")


@dataclass
class Lookup:
    """A path resolved against the tracked set.

    `problem` set: a finding. `path` set: resolved to that repo-relative path.
    Neither: unchecked (outside the repo, gitignored, or not knowable statically).
    """

    path: str | None = None
    problem: str | None = None


class Repo:
    """The target repo as git sees it: its tracked files, and what it ignores.

    Paths are resolved against this rather than the disk, so a file that only
    exists on one machine (built, or never committed) cannot make the check
    pass there and fail on a fresh checkout.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: set[str] = set()
        self.dirs: set[str] = {"."}
        self.error: str | None = None
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-z", "--stage"],
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            self.error = f"cannot run git to list tracked files: {exc}"
            return
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            self.error = f"`git ls-files` failed, so tracked files cannot be checked: {stderr}"
            return
        for entry in result.stdout.split(b"\0"):
            if not entry:
                continue
            meta, _, raw = entry.partition(b"\t")
            path = raw.decode("utf-8", "replace")
            if meta.startswith(b"160000 "):
                # A submodule is a directory, not a file.
                self.dirs.add(path)
            else:
                self.files.add(path)
            parent = posixpath.dirname(path)
            while parent:
                self.dirs.add(parent)
                parent = posixpath.dirname(parent)

    def is_ignored(self, path: str, *, is_dir: bool = False) -> bool:
        """Whether git ignores `path` or one of its parent directories.

        The user's global excludes file is switched off, so the answer is the
        repo's own .gitignore files, the same on every machine.
        """
        candidates = [path + "/" if is_dir else path]
        parent = posixpath.dirname(path)
        while parent:
            candidates.append(parent + "/")
            parent = posixpath.dirname(parent)
        try:
            result = subprocess.run(
                ["git", "-c", "core.excludesFile=/dev/null", "-C", str(self.root), "check-ignore", "-q", "--stdin", "-z"],
                input=b"\0".join(c.encode() for c in candidates) + b"\0",
                capture_output=True,
                check=False,
            )
        except OSError:
            return False
        return result.returncode == 0

    def lookup(self, cwd: str, token: str, kind: str) -> Lookup:
        if not token or token.startswith(("/", "~")) or "$" in token or "`" in token:
            # Absolute, home-relative, or built by the shell at run time.
            return Lookup()
        if "{" in token and "," in token:
            # Brace expansion; the shell makes several paths of it.
            return Lookup()
        path = posixpath.normpath(posixpath.join(cwd, token))
        if path == ".." or path.startswith("../"):
            # Outside the repo, so outside what this check can know.
            return Lookup()
        if any(c in token for c in "*?["):
            return self._glob(path, token, kind)

        if kind in (FILE, ANY, NODE) and path in self.files:
            return Lookup(path=path)
        if kind in (DIR, ANY, NODE) and path in self.dirs:
            return Lookup(path=path)
        if kind == NODE and any(path + suffix in self.files for suffix in NODE_SUFFIXES):
            return Lookup(path=path)
        if self.is_ignored(path, is_dir=(kind == DIR)):
            return Lookup()
        what = {
            FILE: "a tracked file",
            DIR: "a directory holding any tracked file",
        }.get(kind, "a tracked file or directory")
        return Lookup(problem=f"`{path}` is not {what}, and is not gitignored as a build output")

    def _glob(self, pattern: str, token: str, kind: str) -> Lookup:
        """A glob passes when it matches at least one tracked path."""
        parts = pattern.split("/")
        pool = self.dirs if kind == DIR else self.files
        if kind in (ANY, NODE):
            pool = self.files | self.dirs
        if any(_glob_match(parts, candidate.split("/")) for candidate in pool):
            return Lookup()
        literal = []
        for part in parts:
            if any(c in part for c in "*?["):
                break
            literal.append(part)
        prefix = "/".join(literal)
        if prefix and self.is_ignored(prefix, is_dir=True):
            return Lookup()
        return Lookup(problem=f"`{token}` matches no tracked file, and is not under a gitignored directory")


def _glob_match(pattern: list[str], path: list[str]) -> bool:
    """Shell-style: `*` stays inside one path segment, `**` spans any number."""
    if not pattern:
        return not path
    head = pattern[0]
    if head == "**":
        return any(_glob_match(pattern[1:], path[i:]) for i in range(len(path) + 1))
    if not path:
        return False
    return fnmatch.fnmatchcase(path[0], head) and _glob_match(pattern[1:], path[1:])


# --------------------------------------------------------------------------
# Command resolution
# --------------------------------------------------------------------------

#: `<like-this>`. A line holding one is an example, not a command, and is
#: skipped whole. The class excludes spaces, so `cmd < in > out` is not one.
PLACEHOLDER = re.compile(r"<[A-Za-z][\w.:/-]*>")
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
#: A redirection token: `>`, `2>&1`, `<<EOF`, `&>log`. Group 1 is any target
#: written in the same token; when empty, the target is the next token.
REDIRECT = re.compile(r"^(?:\d*|&)(?:>>?|<<?<?|>&|<&|>\|)(.*)$")

#: Fence info strings whose blocks hold shell commands. Any other fence
#: (text, yaml, json, ...) in `## Commands` is not checked and does not count.
SHELL_FENCES = {"", "sh", "bash", "shell", "zsh", "console"}

INTERPRETERS = ("bash", "sh", "node", "python", "python3")
#: Short flags after which the interpreter runs inline code or stdin, not a file.
INLINE_SHORT = {"bash": "cs", "sh": "cs", "node": "ep", "python": "cm", "python3": "cm"}
INLINE_LONG = {"bash": set(), "sh": set(), "node": {"--eval", "--print"}, "python": set(), "python3": set()}
#: Short flags whose value is the next token when they end a cluster
#: (`bash -euo pipefail x.sh`), or the rest of the cluster otherwise.
VALUE_SHORT = {"bash": "oO", "sh": "o", "node": "rC", "python": "WX", "python3": "WX"}
VALUE_LONG = {
    "bash": {"--rcfile", "--init-file"},
    "sh": set(),
    "node": {
        "--require", "--import", "--loader", "--experimental-loader", "--conditions",
        "--input-type", "--env-file", "--test-reporter", "--test-reporter-destination",
        "--test-name-pattern", "--test-skip-pattern", "--watch-path", "--inspect-port",
        "--title", "--test-concurrency", "--test-timeout", "--test-shard",
    },
    "python": {"--check-hash-based-pycs"},
    "python3": {"--check-hash-based-pycs"},
}

#: `uv` flags whose value is the next token. `--from` is uvx's rather than
#: `uv run`'s, but a command that passes it should still parse.
UV_VALUE_FLAGS = {
    "--with", "--from", "--python", "-p", "--project", "--directory",
    "--with-editable", "--with-requirements", "--extra", "--group", "--only-group",
    "--no-group", "--package", "--env-file", "--index", "--default-index",
    "--index-url", "-i", "--extra-index-url", "--find-links", "-f", "--cache-dir",
    "--config-file", "--color", "--exclude-newer", "--resolution", "--prerelease",
    "--link-mode", "-C", "--config-setting", "--upgrade-package", "-P",
    "--reinstall-package", "--refresh-package", "--index-strategy",
    "--keyring-provider", "--allow-insecure-host", "--python-platform",
}
UV_PATH_SUFFIXES = (".py", ".sh", ".js", ".mjs", ".cjs", ".ts")

NPM_RUN = ("run", "run-script")
NPM_LIFECYCLE = ("test", "start", "stop", "restart")
#: npm flags whose value is the next token.
NPM_VALUE_FLAGS = {"--prefix", "-C", "--loglevel", "--userconfig", "--cache", "--registry", "--script-shell"}
#: Forms npm resolves somewhere this check cannot follow, or tolerates missing.
NPM_UNCHECKED_FLAGS = {"-w", "--workspace", "--workspaces", "-ws", "--if-present"}

#: GNU make's own search order.
MAKEFILE_NAMES = ("GNUmakefile", "makefile", "Makefile")
MAKE_RULE = re.compile(r"^(?P<targets>[^\s:=#][^:=#]*?)[ \t]*(?P<rest>:.*)$")


def _scan_shell(code: str):
    """Yield (index, char, quoted) over a shell line, tracking quotes and escapes."""
    quote = None
    index = 0
    while index < len(code):
        char = code[index]
        if quote is None and char == "\\" and index + 1 < len(code):
            yield index, char, True
            yield index + 1, code[index + 1], True
            index += 2
            continue
        if quote is not None:
            if char == quote:
                quote = None
            yield index, char, True
            index += 1
            continue
        if char in "'\"":
            quote = char
            yield index, char, True
            index += 1
            continue
        yield index, char, False
        index += 1


def strip_comment(code: str) -> str:
    """Drop a shell comment: an unquoted `#` that starts a word."""
    for index, char, quoted in _scan_shell(code):
        if char == "#" and not quoted and (index == 0 or code[index - 1] in " \t;&|("):
            return code[:index]
    return code


def split_segments(code: str) -> list[str]:
    """Split on unquoted `&&`, `||` and `;`. Pipes stay inside a segment."""
    segments = []
    start = 0
    skip_next = False
    for index, char, quoted in _scan_shell(code):
        if skip_next:
            skip_next = False
            continue
        if quoted:
            continue
        if char == ";":
            segments.append(code[start:index])
            start = index + 1
        elif char in "&|" and code[index + 1 : index + 2] == char:
            segments.append(code[start:index])
            start = index + 2
            skip_next = True
    segments.append(code[start:])
    return [segment.strip() for segment in segments if segment.strip()]


def _tokens(segment: str) -> list[str]:
    """The words of the segment's first command: no env prefix, pipe or redirect."""
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    while words and ENV_ASSIGNMENT.match(words[0]):
        words.pop(0)
    tokens = []
    index = 0
    while index < len(words):
        word = words[index]
        if word in ("|", "|&", "&"):
            break
        redirect = REDIRECT.match(word)
        if redirect:
            index += 1 if redirect.group(1) else 2
            continue
        tokens.append(word)
        index += 1
    return tokens


def _npm(repo: Repo, cwd: str, args: list[str]) -> list[str]:
    prefix = None
    positional: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            break
        if arg in NPM_UNCHECKED_FLAGS or arg.startswith(("--workspace=", "-w=")):
            return []
        if arg.startswith("--prefix="):
            prefix = arg.split("=", 1)[1]
        elif arg in NPM_VALUE_FLAGS:
            if arg in ("--prefix", "-C") and index + 1 < len(args):
                prefix = args[index + 1]
            index += 2
            continue
        elif not arg.startswith("-"):
            positional.append(arg)
        index += 1

    if not positional:
        return []
    if positional[0] in NPM_RUN:
        if len(positional) < 2:
            # `npm run` alone lists the scripts.
            return []
        script = positional[1]
    elif positional[0] in NPM_LIFECYCLE:
        script = positional[0]
    else:
        # `npm ci`, `npm install`, `npm exec` and the rest are not scripts.
        return []

    directory = cwd
    if prefix is not None:
        found = repo.lookup(cwd, prefix, DIR)
        if found.problem:
            return [f"`--prefix {prefix}`: {found.problem}"]
        if found.path is None:
            return []
        directory = found.path

    manifest = posixpath.normpath(posixpath.join(directory, "package.json"))
    if manifest not in repo.files:
        if repo.is_ignored(manifest):
            return []
        return [f"`{manifest}` is not a tracked file, and is not gitignored as a build output"]
    try:
        data = json.loads((repo.root / manifest).read_text(encoding="utf-8-sig"))
    except OSError as exc:
        return [f"cannot read `{manifest}`: {exc}"]
    except (ValueError, UnicodeDecodeError) as exc:
        return [f"`{manifest}` does not parse: {exc}"]
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if scripts is None:
        scripts = {}
    if not isinstance(scripts, dict):
        return [f"`{manifest}` has a `scripts` field that is not an object"]
    if script in scripts:
        return []
    if script == "start" and posixpath.normpath(posixpath.join(directory, "server.js")) in repo.files:
        # npm's built-in default: with no start script, `npm start` runs `node server.js`.
        return []
    return [f"`{manifest}` has no `{script}` script"]


def _make_targets(text: str) -> tuple[set[str], list[re.Pattern]]:
    names: set[str] = set()
    patterns: list[re.Pattern] = []
    for line in text.splitlines():
        if line.startswith("\t"):
            continue
        rule = MAKE_RULE.match(line)
        if not rule:
            continue
        rest = rule.group("rest")
        if rest.startswith((":=", "::=", ":::=")):
            # A variable assignment, not a rule.
            continue
        for target in rule.group("targets").split():
            if "$" in target:
                continue
            if "%" in target:
                stem = re.escape(target).replace("%", ".+", 1)
                patterns.append(re.compile(f"^{stem}$"))
            else:
                names.add(target)
    return names, patterns


def _make(repo: Repo, cwd: str, args: list[str]) -> list[str]:
    directory = cwd
    targets: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-f", "--file", "--makefile") or arg.startswith(("-f", "--file=", "--makefile=")):
            # A named makefile is not one this check goes looking for.
            return []
        change = None
        if arg in ("-C", "--directory"):
            if index + 1 >= len(args):
                return []
            change = args[index + 1]
            index += 1
        elif arg.startswith("--directory="):
            change = arg.split("=", 1)[1]
        elif arg.startswith("-C"):
            change = arg[2:]
        elif arg in ("-j", "--jobs", "-l", "--load-average") and index + 1 < len(args) and args[index + 1].isdigit():
            index += 1
        elif arg in ("-I", "--include-dir", "-o", "--old-file", "-W", "--what-if", "--assume-new"):
            index += 1
        elif not arg.startswith("-") and "=" not in arg:
            targets.append(arg)
        if change is not None:
            found = repo.lookup(directory, change, DIR)
            if found.problem:
                return [f"`-C {change}`: {found.problem}"]
            if found.path is None:
                return []
            directory = found.path
        index += 1

    if not targets:
        # The default goal; nothing named to resolve.
        return []
    makefile = next(
        (path for name in MAKEFILE_NAMES if (path := posixpath.normpath(posixpath.join(directory, name))) in repo.files),
        None,
    )
    if makefile is None:
        return [f"there is no tracked Makefile in `{directory}`"]
    try:
        text = (repo.root / makefile).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"cannot read `{makefile}`: {exc}"]
    names, patterns = _make_targets(text)
    missing = [t for t in targets if t not in names and not any(p.match(t) for p in patterns)]
    if missing:
        listed = ", ".join(f"`{t}`" for t in missing)
        return [f"`{makefile}` has no rule for {listed}"]
    return []


def _interpreter(repo: Repo, cwd: str, name: str, args: list[str]) -> list[str]:
    test_mode = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            break
        if arg in ("-", "+") or not arg.startswith(("-", "+")):
            break
        if arg.startswith("--"):
            flag = arg.split("=", 1)[0]
            if flag in INLINE_LONG[name]:
                return []
            if name == "node" and flag == "--test":
                test_mode = True
            index += 2 if ("=" not in arg and flag in VALUE_LONG[name]) else 1
            continue
        # A short flag or cluster, read the way getopt does: `-euo pipefail`
        # is -e, -u, then -o taking `pipefail`.
        takes_next = False
        for position, char in enumerate(arg[1:]):
            if char in INLINE_SHORT[name]:
                return []
            if char in VALUE_SHORT[name]:
                takes_next = position == len(arg) - 2
                break
        index += 2 if takes_next else 1

    rest = args[index:]
    if rest and rest[0] in ("-", "+"):
        # The script comes from stdin.
        return []
    if test_mode:
        # `node --test a b ...`: every argument is a test file, directory or glob.
        problems = []
        position = 0
        while position < len(rest):
            token = rest[position]
            if token.startswith("-"):
                flag = token.split("=", 1)[0]
                position += 2 if ("=" not in token and flag in VALUE_LONG[name]) else 1
                continue
            problems += _path(repo, cwd, token, ANY)
            position += 1
        return problems
    if not rest:
        # A REPL, or a runner like `node --test` that finds its own files.
        return []
    return _path(repo, cwd, rest[0], NODE if name == "node" else FILE)


def _path(repo: Repo, cwd: str, token: str, kind: str) -> list[str]:
    found = repo.lookup(cwd, token, kind)
    return [found.problem] if found.problem else []


def _uv(repo: Repo, cwd: str, args: list[str]) -> list[str]:
    base = cwd
    seen_run = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            break
        if arg in ("-m", "--module"):
            return []
        directory = None
        if arg.startswith("--directory="):
            directory = arg.split("=", 1)[1]
        elif arg in UV_VALUE_FLAGS:
            if arg == "--directory" and index + 1 < len(args):
                directory = args[index + 1]
            index += 1
        elif not arg.startswith("-"):
            if seen_run:
                break
            if arg != "run":
                # `uv sync`, `uv lock`, `uv tool ...`: not a path form.
                return []
            seen_run = True
        if directory is not None:
            found = repo.lookup(cwd, directory, DIR)
            if found.problem:
                return [f"`--directory {directory}`: {found.problem}"]
            if found.path is None:
                return []
            base = found.path
        index += 1
    if not seen_run or index >= len(args):
        return []
    target = args[index]
    if target in INTERPRETERS:
        return _interpreter(repo, base, target, args[index + 1 :])
    if "://" in target:
        return []
    if target.endswith(UV_PATH_SUFFIXES) or "/" in target:
        return _path(repo, base, target, FILE)
    # A console script from the environment (`uv run pytest`): unchecked.
    return []


def _resolve(repo: Repo, cwd: str, tokens: list[str]) -> list[str]:
    command = tokens[0]
    if command == "npm":
        return _npm(repo, cwd, tokens[1:])
    if command == "make":
        return _make(repo, cwd, tokens[1:])
    if command in INTERPRETERS:
        return _interpreter(repo, cwd, command, tokens[1:])
    if command == "uv":
        return _uv(repo, cwd, tokens[1:])
    if command.startswith(("./", "scripts/")):
        return _path(repo, cwd, command, FILE)
    # tofu, npx, kubectl, git, ...: not this check's to judge.
    return []


def check_command_line(repo: Repo, code: str) -> list[str]:
    """Problems with one logical command line, run from the repo root.

    `cd` and `pushd` move the directory later segments resolve against, and
    `popd` moves it back. Where the directory stops being knowable (`cd -`,
    `cd $X`, a gitignored or out-of-repo directory), the rest of the line is
    left unchecked rather than guessed at.
    """
    code = strip_comment(code).strip()
    if not code or PLACEHOLDER.search(code):
        return []
    problems: list[str] = []
    cwd = "."
    stack: list[str] = []
    for segment in split_segments(code):
        tokens = _tokens(segment)
        if not tokens:
            continue
        command = tokens[0]
        if command in ("cd", "pushd"):
            args = [a for a in tokens[1:] if a not in ("-P", "-L", "-e", "-@", "--")]
            if not args or args[0] == "-" or args[0].startswith(("+", "-")):
                break
            found = repo.lookup(cwd, args[0], DIR)
            if found.problem:
                problems.append(f"`{command} {args[0]}`: {found.problem}")
                break
            if found.path is None:
                break
            if command == "pushd":
                stack.append(cwd)
            cwd = found.path
            continue
        if command == "popd":
            if not stack:
                break
            cwd = stack.pop()
            continue
        problems += _resolve(repo, cwd, tokens)
    return problems


def _logical_lines(fence: Fence) -> list[tuple[int, str]]:
    """Fence body lines with backslash continuations joined; numbered by first line."""
    joined: list[tuple[int, str]] = []
    pending: tuple[int, str] | None = None
    for number, line in fence.body:
        text = line.rstrip()
        continued = text.endswith("\\") and not text.endswith("\\\\")
        if continued:
            text = text[:-1]
        if pending is not None:
            pending = (pending[0], pending[1] + " " + text.strip())
        else:
            pending = (number, text)
        if not continued:
            joined.append(pending)
            pending = None
    if pending is not None:
        joined.append(pending)
    return joined


def _fence_language(fence: Fence) -> str:
    words = fence.info.split()
    return words[0].strip("{}.").lower() if words else ""


def command_lines(lines: list[str], md: Markdown) -> tuple[Heading | None, list[tuple[int, str]]]:
    """The `## Commands` heading and every command line in its shell fences."""
    start = next((h for h in md.headings if h.level == 2 and h.text == COMMANDS_H2), None)
    if start is None:
        return None, []
    end = next((h.line for h in md.headings if h.line > start.line and h.level <= 2), len(lines) + 1)
    found = []
    for fence in md.fences:
        if not start.line < fence.start < end:
            continue
        language = _fence_language(fence)
        if language not in SHELL_FENCES:
            continue
        for number, code in _logical_lines(fence):
            if language == "console":
                # Only prompt lines are commands; the rest is their output.
                stripped = code.lstrip()
                if not stripped.startswith("$ "):
                    continue
                code = stripped[2:]
            if strip_comment(code).strip():
                found.append((number, code))
    return start, found


def check_commands(repo: Repo, lines: list[str], md: Markdown) -> tuple[list[Finding], int]:
    """Finding 5: commands in `## Commands` that do not resolve, or none at all."""
    start, found = command_lines(lines, md)
    if start is None:
        # The missing section is the contract check's finding.
        return [], 0
    if not found:
        return [
            Finding(
                AGENTS_MD,
                start.line,
                "`## Commands` has no fenced shell block holding a command; write the "
                "commands in a ```sh block (text, yaml and other fences are not checked)",
            )
        ], 0
    findings = []
    for number, code in found:
        for problem in check_command_line(repo, code):
            findings.append(Finding(AGENTS_MD, number, f"`{code.strip()}`: {problem}"))
    return findings, len(found)


# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------


def check_context_files(repo: Repo) -> list[Finding]:
    """Finding 1: a tracked CLAUDE.md or CLAUDE.local.md at any depth.

    Tracked only: an untracked file (an old branch checked out under
    `.worktrees/`, say) is not the repo's content.
    """
    return [
        Finding(
            path,
            None,
            f"tracked `{path}`: AGENTS.md is the only repo context file, and Claude "
            "Code ignores AGENTS.md wherever a CLAUDE.md or CLAUDE.local.md is "
            "present; move what it says into AGENTS.md and delete it",
        )
        for path in sorted(repo.files)
        if posixpath.basename(path) in FORBIDDEN_CONTEXT_FILES
    ]


def check_contract(lines: list[str], md: Markdown) -> list[Finding]:
    """Finding 3: the section contract."""
    findings = []
    if md.unclosed is not None:
        findings.append(
            Finding(
                AGENTS_MD,
                md.unclosed,
                f"unclosed code fence opened at line {md.unclosed}; everything after it "
                "reads as code, so no heading below it counts",
            )
        )

    first = next((n for n, line in enumerate(lines, 1) if line.strip()), None)
    if first is None:
        return findings + [Finding(AGENTS_MD, None, "AGENTS.md is empty")]
    h1s = [h for h in md.headings if h.level == 1]
    if not h1s or h1s[0].line != first:
        findings.append(
            Finding(AGENTS_MD, first, "AGENTS.md must open with its one H1 (`# <repo>`) on the first non-blank line")
        )
    for heading in h1s:
        if heading.line != first:
            findings.append(
                Finding(AGENTS_MD, heading.line, f"extra H1 `# {heading.text}`; AGENTS.md has exactly one H1")
            )

    seen: dict[str, Heading] = {}
    for heading in (h for h in md.headings if h.level == 2 and h.text in REQUIRED_H2):
        if heading.text in seen:
            findings.append(
                Finding(
                    AGENTS_MD,
                    heading.line,
                    f"`## {heading.text}` appears more than once (first at line {seen[heading.text].line})",
                )
            )
        else:
            seen[heading.text] = heading

    for name in REQUIRED_H2:
        if name not in seen:
            findings.append(Finding(AGENTS_MD, None, f"missing required section `## {name}`"))

    order = " -> ".join(f"`{name}`" for name in REQUIRED_H2)
    highest = -1
    for heading in sorted(seen.values(), key=lambda h: h.line):
        rank = REQUIRED_H2.index(heading.text)
        if rank < highest:
            findings.append(
                Finding(
                    AGENTS_MD,
                    heading.line,
                    f"`## {heading.text}` is out of order; the required sections run {order}",
                )
            )
        highest = max(highest, rank)

    last = seen.get(LAST_H2)
    for heading in tail_intruders(md):
        if heading.level == 1:
            # Already reported as an extra H1.
            continue
        after = LAST_H2 if last is not None and heading.line > last.line else TAIL_H2
        findings.append(
            Finding(
                AGENTS_MD,
                heading.line,
                f"`{heading.markup()}` comes after `## {after}`; everything from "
                "`## Dependencies` on is generated, so move it above `## Dependencies` "
                "(render refuses to overwrite it)",
            )
        )
    return findings


def check_render(norm: str, entities: list[dict], fleet_rules: str) -> list[Finding]:
    """Finding 4: the file differs from what `render` would write.

    The whole normalized file is compared, not just the tail, so a passing
    check means a `render` would be a no-op -- including the blank line before
    the tail and the single trailing newline. A BOM or CRLF endings are
    reported on their own, so this diff stays readable.
    """
    try:
        expected = render_text(norm, entities, fleet_rules)
    except GateError as exc:
        return [Finding(AGENTS_MD, None, message) for message in exc.messages]
    if expected == norm:
        return []
    have = norm.split("\n")
    want = expected.split("\n")
    first_diff = next(
        (n for n, (a, b) in enumerate(zip(have, want), 1) if a != b),
        min(len(have), len(want)),
    )
    diff = "".join(
        difflib.unified_diff(
            [line + "\n" for line in have],
            [line + "\n" for line in want],
            fromfile=f"{AGENTS_MD} (committed)",
            tofile=f"{AGENTS_MD} (render)",
        )
    )
    if first_tail_heading(scan_markdown(have)) is None:
        message = "AGENTS.md has no generated tail; run `scripts/agents-md.py render`"
    else:
        message = (
            "AGENTS.md is not what `render` writes; the tail from `## Dependencies` on is "
            "generated from catalog-info.yaml and the fleet rules, so change those and "
            "re-render with `scripts/agents-md.py render` from wac.lab.actions at the tag "
            "this repo's CI pins"
        )
    return [Finding(AGENTS_MD, first_diff, message, detail=diff.rstrip("\n"))]


def check_encoding(text: str) -> list[Finding]:
    findings = []
    if text.startswith(BOM):
        findings.append(
            Finding(AGENTS_MD, 1, "AGENTS.md starts with a UTF-8 BOM; render strips it, so re-render or remove it")
        )
    crlf = text.find("\r\n")
    if crlf != -1:
        findings.append(
            Finding(
                AGENTS_MD,
                text.count("\n", 0, crlf) + 1,
                "AGENTS.md has CRLF line endings; render writes LF, so re-render or convert the file",
            )
        )
    return findings


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------


def read_agents(root: Path) -> str:
    """The root AGENTS.md as text. Raises GateError when it cannot be the contract file."""
    path = root / AGENTS_MD
    if path.is_symlink():
        raise GateError(
            "AGENTS.md is a symlink; it must be a regular file, so that what the "
            "gate checks is what an agent reads"
        )
    if not path.exists():
        raise GateError("no AGENTS.md at the repo root; every fleet repo carries one")
    if not path.is_file():
        raise GateError("AGENTS.md is not a regular file")
    try:
        return path.read_bytes().decode("utf-8")
    except OSError as exc:
        raise GateError(f"cannot read AGENTS.md: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise GateError(f"AGENTS.md is not UTF-8: {exc}") from exc


def cmd_render(root: Path) -> int:
    try:
        text = read_agents(root)
        entities = load_catalog(root)
        rendered = render_text(text, entities, read_fleet_rules())
    except GateError as exc:
        print("agents-md render: refusing to write AGENTS.md:", file=sys.stderr)
        for message in exc.messages:
            print(f"  - {message}", file=sys.stderr)
        return 2
    if rendered == text:
        print(f"agents-md render: {AGENTS_MD} is already current")
        return 0
    (root / AGENTS_MD).write_bytes(rendered.encode("utf-8"))
    components = sum(1 for doc in entities if _is_component(doc))
    print(
        f"agents-md render: wrote the tail of {AGENTS_MD} from {CATALOG} "
        f"({components} component(s), {len(entities) - components} other entit(ies)) "
        f"and {FLEET_RULES.parent.name}/{FLEET_RULES.name}"
    )
    return 0


def cmd_check(root: Path) -> int:
    try:
        fleet_rules = read_fleet_rules()
    except GateError as exc:
        print(f"::error::{_escape_data(str(exc))}")
        return 2

    findings: list[Finding] = []
    repo = Repo(root)
    if repo.error:
        findings.append(Finding(".", None, repo.error))
    else:
        findings += check_context_files(repo)

    text = None
    try:
        text = read_agents(root)
    except GateError as exc:
        findings += [Finding(AGENTS_MD, None, message) for message in exc.messages]

    entities = None
    try:
        entities = load_catalog(root)
    except GateError as exc:
        findings += [Finding(CATALOG, None, message) for message in exc.messages]

    checked = 0
    if text is not None:
        findings += check_encoding(text)
        norm = normalize(text)
        lines = norm.split("\n")
        md = scan_markdown(lines)
        findings += check_contract(lines, md)
        # A file render would refuse to touch gets the contract findings above,
        # not a diff that tells the reader to re-render.
        if entities is not None and md.unclosed is None and not tail_intruders(md):
            findings += check_render(norm, entities, fleet_rules)
        if repo.error is None:
            command_findings, checked = check_commands(repo, lines, md)
            findings += command_findings

    for finding in findings:
        print(finding.annotation())
        if finding.detail:
            print(finding.detail)

    if findings:
        print(f"\nagents-md check: {len(findings)} problem(s) in {root}")
        for finding in findings:
            print(f"  - {finding.human()}")
        return 1
    print(
        f"agents-md check: ok -- {AGENTS_MD} holds the section contract, its tail is "
        f"current, {checked} command line(s) checked, and no CLAUDE.md is tracked"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS, so `--root` may sit before or after the subcommand without the
    # subparser's default overwriting a value the main parser already took.
    common.add_argument(
        "--root",
        type=Path,
        default=argparse.SUPPRESS,
        help="target repo root (default: the current directory)",
    )
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("render", parents=[common], help="rewrite the generated tail of AGENTS.md")
    sub.add_parser("check", parents=[common], help="fail on any drift between AGENTS.md and the repo")
    args = parser.parse_args(argv)

    root = (getattr(args, "root", None) or Path.cwd()).resolve()
    if not root.is_dir():
        print(f"agents-md: {root} is not a directory", file=sys.stderr)
        return 2
    if args.command == "render":
        return cmd_render(root)
    return cmd_check(root)


if __name__ == "__main__":
    sys.exit(main())
