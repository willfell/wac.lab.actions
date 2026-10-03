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
`--root PATH`. `check` reads only the working tree and `git ls-files`; it never
touches the network.

The contract itself is the design doc's, in wac.docs:
docs/superpowers/specs/2026-10-02-agents-md-knowledge-layer-design.md.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
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


class GateError(Exception):
    """A problem that stops `render` outright, or that `check` reports as one finding."""


# --------------------------------------------------------------------------
# Markdown scanning
# --------------------------------------------------------------------------

#: An ATX heading. The `#` run must be followed by whitespace or end of line,
#: so `#hashtag` is a paragraph, as CommonMark has it.
ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
#: A fence opener. Indentation is not limited to three spaces, so a fence
#: nested in a list item is still seen as a fence.
FENCE_OPEN = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")
FENCE_CLOSE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*$")


@dataclass
class Heading:
    level: int
    text: str
    line: int


@dataclass
class Fence:
    start: int
    body: list[tuple[int, str]] = field(default_factory=list)


def scan_markdown(lines: list[str]) -> tuple[list[Heading], list[Fence]]:
    """Headings outside fenced code blocks, and every fenced block's body.

    Line numbers are 1-based. A `# note` inside a fence is code, not an H1;
    that is the whole reason this is a scanner rather than a regex over the file.
    """
    headings: list[Heading] = []
    fences: list[Fence] = []
    open_fence: tuple[str, int, Fence] | None = None
    for number, raw in enumerate(lines, 1):
        line = raw.rstrip("\r")
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
            open_fence = (opening.group(1)[0], len(opening.group(1)), Fence(start=number))
            continue
        heading = ATX_HEADING.match(line)
        if heading:
            text = re.sub(r"(?:^|[ \t]+)#+$", "", heading.group(2) or "").strip()
            headings.append(Heading(len(heading.group(1)), text, number))
    if open_fence is not None:
        # An unclosed fence runs to the end of the document.
        fences.append(open_fence[2])
    return headings, fences


def tail_index(lines: list[str]) -> int | None:
    """0-based index of the first `## Dependencies` line outside a fence."""
    headings, _ = scan_markdown(lines)
    for heading in headings:
        if heading.level == 2 and heading.text == TAIL_H2:
            return heading.line - 1
    return None


# --------------------------------------------------------------------------
# Rendering the tail
# --------------------------------------------------------------------------


def load_catalog(root: Path) -> list[dict]:
    path = root / CATALOG
    if not path.is_file():
        raise GateError(f"no `{CATALOG}` at the repo root")
    try:
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise GateError(f"`{CATALOG}` does not parse: {exc}") from exc
    entities = []
    for index, doc in enumerate(docs):
        if doc is None:
            # An empty document, e.g. from a trailing `---`.
            continue
        if not isinstance(doc, dict):
            raise GateError(f"`{CATALOG}` document {index + 1} is not a mapping")
        kind = doc.get("kind")
        name = (doc.get("metadata") or {}).get("name")
        if not kind or not name:
            raise GateError(f"`{CATALOG}` document {index + 1} has no `kind` or `metadata.name`")
        entities.append(doc)
    return entities


def normalize_ref(ref: object, default_kind: str) -> str:
    """`kind:name` with the kind lowercased and a `default/` namespace dropped.

    A bare name gets the field's default kind, as Backstage resolves it. Names
    keep their case: Backstage compares them case-insensitively, but the
    catalog shows them as written, and so does this file.
    """
    text = str(ref).strip()
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
    return f"{kind}:{rest}"


def _entity_ref(doc: dict) -> str:
    metadata = doc.get("metadata") or {}
    namespace = metadata.get("namespace")
    name = str(metadata["name"])
    if namespace and namespace != "default":
        name = f"{namespace}/{name}"
    return f"{str(doc['kind']).lower()}:{name}"


def _one_line(text: object) -> str:
    # A folded or literal YAML description can span lines; the tail never wraps.
    return " ".join(str(text).split())


def _component_sentence(doc: dict) -> str:
    metadata = doc.get("metadata") or {}
    spec = doc.get("spec") or {}
    parts = []
    description = _one_line(metadata.get("description") or "")
    if description:
        parts.append(description if description[-1] in ".!?" else description + ".")
    facts = [
        f"{label} `{_one_line(spec[key])}`"
        for label, key in (("system", "system"), ("type", "type"), ("lifecycle", "lifecycle"))
        if spec.get(key) not in (None, "")
    ]
    if facts:
        joined = ", ".join(facts)
        parts.append(joined[0].upper() + joined[1:] + ".")
    return " ".join(parts)


def _ref_list(values: object, default_kind: str) -> str:
    if values is None:
        values = []
    if not isinstance(values, list):
        values = [values]
    refs = sorted({normalize_ref(value, default_kind) for value in values if str(value).strip()})
    if not refs:
        return "(none)"
    return ", ".join(f"`{ref}`" for ref in refs)


def read_fleet_rules() -> str:
    try:
        text = FLEET_RULES.read_text(encoding="utf-8")
    except OSError as exc:
        raise GateError(f"cannot read the fleet rules at {FLEET_RULES}: {exc}") from exc
    text = text.replace("\r\n", "\n").strip("\n").rstrip()
    if not text:
        raise GateError(f"the fleet rules at {FLEET_RULES} are empty")
    return text


def render_tail(entities: list[dict], fleet_rules: str) -> str:
    """Everything from `## Dependencies` to end of file. Byte-stable."""
    out = ["## Dependencies", "", TAIL_PREAMBLE, ""]

    components = [doc for doc in entities if str(doc["kind"]) == "Component"]
    others = [doc for doc in entities if str(doc["kind"]) != "Component"]

    for doc in components:
        name = str(doc["metadata"]["name"])
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

    if others:
        out += ["### Also declared here", ""]
        for doc in others:
            description = _one_line((doc.get("metadata") or {}).get("description") or "")
            ref = _entity_ref(doc)
            out.append(f"- `{ref}`: {description}" if description else f"- `{ref}`")
        out.append("")

    out += [INCOMING_NOTE, "", f"## {LAST_H2}", "", fleet_rules]
    return "\n".join(out) + "\n"


def compose(agents_text: str, tail: str) -> str:
    """The whole file `render` writes: the prose head kept, the tail replaced.

    The head is byte-identical except that its trailing whitespace is
    normalised to exactly one blank line before `## Dependencies`, and line
    endings are LF throughout.
    """
    lines = agents_text.replace("\r\n", "\n").split("\n")
    index = tail_index(lines)
    head = "\n".join(lines if index is None else lines[:index]).rstrip()
    return f"{head}\n\n{tail}" if head else tail


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


def check_context_files(root: Path) -> list[Finding]:
    """Finding 1: a tracked CLAUDE.md or CLAUDE.local.md at any depth.

    Tracked only: an untracked file (an old branch checked out under
    `.worktrees/`, say) is not the repo's content.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return [Finding(".", None, f"cannot run git to list tracked files: {exc}")]
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        return [Finding(".", None, f"`git ls-files` failed, so tracked files cannot be checked: {stderr}")]
    findings = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8", "replace")
        if path.rsplit("/", 1)[-1] in FORBIDDEN_CONTEXT_FILES:
            findings.append(
                Finding(
                    path,
                    None,
                    f"tracked `{path}`: AGENTS.md is the only repo context file, and Claude "
                    "Code ignores AGENTS.md wherever a CLAUDE.md or CLAUDE.local.md is "
                    "present; move what it says into AGENTS.md and delete it",
                )
            )
    return findings


def check_contract(lines: list[str]) -> list[Finding]:
    """Finding 3: the section contract."""
    findings = []
    headings, _ = scan_markdown(lines)

    first = next((n for n, line in enumerate(lines, 1) if line.strip()), None)
    if first is None:
        return [Finding(AGENTS_MD, None, "AGENTS.md is empty")]
    h1s = [h for h in headings if h.level == 1]
    if not h1s or h1s[0].line != first:
        findings.append(
            Finding(AGENTS_MD, first, "AGENTS.md must open with its one H1 (`# <repo>`) on the first non-blank line")
        )
    for heading in h1s:
        if heading.line != first:
            findings.append(
                Finding(AGENTS_MD, heading.line, f"extra H1 `# {heading.text}`; AGENTS.md has exactly one H1")
            )

    h2s = [h for h in headings if h.level == 2]
    seen: dict[str, Heading] = {}
    for heading in h2s:
        if heading.text in REQUIRED_H2:
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
    present = sorted(seen.values(), key=lambda h: h.line)
    highest = -1
    for heading in present:
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

    tail = seen.get(TAIL_H2)
    last = seen.get(LAST_H2)
    for heading in h2s:
        if heading.text in (TAIL_H2, LAST_H2):
            continue
        if last is not None and heading.line > last.line:
            findings.append(
                Finding(
                    AGENTS_MD,
                    heading.line,
                    f"`## {heading.text}` comes after `## {LAST_H2}`; nothing may follow "
                    "the fleet rules, and a repo's own sections go before `## Dependencies`",
                )
            )
        elif tail is not None and heading.line > tail.line and heading.text not in REQUIRED_H2:
            findings.append(
                Finding(
                    AGENTS_MD,
                    heading.line,
                    f"`## {heading.text}` comes after `## {TAIL_H2}`; a repo's own sections "
                    "go before it, because everything from there on is generated",
                )
            )
    return findings


def check_render(text: str, entities: list[dict], fleet_rules: str) -> list[Finding]:
    """Finding 4: the file differs from what `render` would write.

    The whole file is compared, not just the tail, so a passing check means a
    `render` would be a no-op -- including the blank line before the tail and
    the single trailing newline.
    """
    expected = compose(text, render_tail(entities, fleet_rules))
    if expected == text:
        return []
    have = text.split("\n")
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
    if tail_index(have) is None:
        message = "AGENTS.md has no generated tail; run `scripts/agents-md.py render`"
    else:
        message = (
            "AGENTS.md is not what `render` writes; the tail from `## Dependencies` on is "
            "generated from catalog-info.yaml and the fleet rules, so change those and "
            "re-render with `scripts/agents-md.py render` from wac.lab.actions at the tag "
            "this repo's CI pins"
        )
    return [Finding(AGENTS_MD, first_diff, message, detail=diff.rstrip("\n"))]


# --------------------------------------------------------------------------
# Command resolution
# --------------------------------------------------------------------------

#: `<like-this>`. A line holding one is an example, not a command, and is
#: skipped whole. The class excludes spaces, so `cmd < in > out` is not one.
PLACEHOLDER = re.compile(r"<[A-Za-z][\w.:/-]*>")
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

INTERPRETERS = ("bash", "sh", "node", "python", "python3")
#: Flags after which the interpreter runs inline code or stdin, not a file.
INLINE_FLAGS = {
    "bash": {"-c", "-s"},
    "sh": {"-c", "-s"},
    "node": {"-e", "--eval", "-p", "--print"},
    "python": {"-c", "-m"},
    "python3": {"-c", "-m"},
}
#: Single letters that, inside a short-flag cluster like `-ec`, mean the same.
INLINE_SHORT = {"bash": "cs", "sh": "cs", "node": "ep", "python": "cm", "python3": "cm"}
#: Flags whose value is the next token, which is therefore not the script.
INTERPRETER_VALUE_FLAGS = {
    "bash": {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"},
    "sh": {"-o", "+o"},
    "node": {
        "-r", "--require", "--import", "--loader", "--experimental-loader", "-C",
        "--conditions", "--input-type", "--env-file", "--test-reporter",
        "--test-reporter-destination", "--test-name-pattern", "--test-skip-pattern",
        "--watch-path", "--inspect-port", "--title",
    },
    "python": {"-W", "-X", "--check-hash-based-pycs"},
    "python3": {"-W", "-X", "--check-hash-based-pycs"},
}
NODE_SUFFIXES = (".js", ".mjs", ".cjs", ".json")

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

MAKEFILE_NAMES = ("GNUmakefile", "makefile", "Makefile")
MAKE_RULE = re.compile(r"^(?P<targets>[^\s:=#][^:=#]*?)[ \t]*(?P<rest>:.*)$")


def _rel(root: Path, path: Path) -> str:
    rel = os.path.relpath(path, root)
    return "." if rel == "." else rel.replace(os.sep, "/")


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
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    while tokens and ENV_ASSIGNMENT.match(tokens[0]):
        tokens.pop(0)
    return tokens


def _cd(root: Path, cwd: Path, args: list[str]) -> tuple[Path | None, str | None]:
    """Follow `cd <dir>` from the current directory.

    Returns the new directory, or None when it cannot be known statically
    (`cd`, `cd -`, `cd ~`, `cd $X`, an absolute path); the rest of the line is
    then unchecked rather than guessed at.
    """
    args = [a for a in args if a not in ("-P", "-L", "-e", "-@", "--")]
    if not args:
        return None, None
    target = args[0]
    if target == "-" or target.startswith(("~", "$", "/")):
        return None, None
    resolved = Path(os.path.normpath(cwd / target))
    if resolved != root and root not in resolved.parents:
        return None, f"`cd {target}` leaves the repo; commands run from the repo root"
    if not resolved.is_dir():
        return None, f"`cd {target}`: there is no directory `{_rel(root, resolved)}` in the repo"
    return resolved, None


def _file_problem(root: Path, base: Path, token: str, *, node: bool = False) -> str | None:
    if token.startswith(("/", "~", "$")):
        # Outside the repo, or not knowable without running the shell.
        return None
    path = Path(os.path.normpath(base / token))
    if path.exists():
        return None
    if node and any(path.with_name(path.name + suffix).is_file() for suffix in NODE_SUFFIXES):
        return None
    return f"there is no `{_rel(root, path)}`"


def _npm(root: Path, cwd: Path, args: list[str]) -> str | None:
    prefix = None
    positional: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            break
        if arg in NPM_UNCHECKED_FLAGS or arg.startswith(("--workspace=", "-w=")):
            return None
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
        return None
    if positional[0] in NPM_RUN:
        if len(positional) < 2:
            # `npm run` alone lists the scripts.
            return None
        script = positional[1]
    elif positional[0] in NPM_LIFECYCLE:
        script = positional[0]
    else:
        # `npm ci`, `npm install`, `npm exec` and the rest are not scripts.
        return None

    directory = Path(os.path.normpath(cwd / prefix)) if prefix else cwd
    manifest = directory / "package.json"
    if not manifest.is_file():
        return f"there is no `{_rel(root, manifest)}`"
    try:
        scripts = (json.loads(manifest.read_text(encoding="utf-8")).get("scripts") or {})
    except (ValueError, AttributeError, UnicodeDecodeError) as exc:
        return f"`{_rel(root, manifest)}` does not parse: {exc}"
    if script in scripts:
        return None
    if script == "start" and (directory / "server.js").is_file():
        # npm's built-in default: with no start script, `npm start` runs `node server.js`.
        return None
    return f"`{_rel(root, manifest)}` has no `{script}` script"


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


def _make(root: Path, cwd: Path, args: list[str]) -> str | None:
    directory = cwd
    targets: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-f", "--file", "--makefile") or arg.startswith(("-f", "--file=", "--makefile=")):
            # A named makefile is not one this check goes looking for.
            return None
        if arg in ("-C", "--directory"):
            if index + 1 >= len(args):
                return None
            directory = Path(os.path.normpath(directory / args[index + 1]))
            index += 2
            continue
        if arg.startswith("--directory="):
            directory = Path(os.path.normpath(directory / arg.split("=", 1)[1]))
        elif arg.startswith("-C"):
            directory = Path(os.path.normpath(directory / arg[2:]))
        elif arg in ("-j", "--jobs", "-l", "--load-average") and index + 1 < len(args) and args[index + 1].isdigit():
            index += 2
            continue
        elif arg in ("-I", "--include-dir", "-o", "--old-file", "-W", "--what-if", "--assume-new"):
            index += 2
            continue
        elif not arg.startswith("-") and "=" not in arg:
            targets.append(arg)
        index += 1

    if not targets:
        # The default goal; nothing named to resolve.
        return None
    # Listed rather than probed: on a case-insensitive filesystem `makefile`
    # would "exist" whenever `Makefile` does, and name the wrong file.
    try:
        entries = set(os.listdir(directory))
    except OSError:
        return f"there is no directory `{_rel(root, directory)}` in the repo"
    makefile = next((directory / n for n in MAKEFILE_NAMES if n in entries and (directory / n).is_file()), None)
    if makefile is None:
        return f"there is no Makefile in `{_rel(root, directory)}`"
    names, patterns = _make_targets(makefile.read_text(encoding="utf-8", errors="replace"))
    missing = [t for t in targets if t not in names and not any(p.match(t) for p in patterns)]
    if missing:
        listed = ", ".join(f"`{t}`" for t in missing)
        return f"`{_rel(root, makefile)}` has no rule for {listed}"
    return None


def _interpreter(root: Path, cwd: Path, name: str, args: list[str]) -> str | None:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "-":
            return None
        if arg == "--":
            index += 1
            break
        if not arg.startswith(("-", "+")):
            break
        if arg in INLINE_FLAGS[name]:
            return None
        if name.startswith("python") and arg[:2] in ("-W", "-X"):
            index += 1 if len(arg) > 2 else 2
            continue
        if arg in INTERPRETER_VALUE_FLAGS[name]:
            index += 2
            continue
        if not arg.startswith("--") and arg.startswith("-") and any(c in arg[1:] for c in INLINE_SHORT[name]):
            # A short-flag cluster such as `bash -ec` or `python -um`.
            return None
        index += 1
    if index >= len(args):
        # A REPL, or a runner like `node --test` that finds its own files.
        return None
    return _file_problem(root, cwd, args[index], node=(name == "node"))


def _uv(root: Path, cwd: Path, args: list[str]) -> str | None:
    base = cwd
    seen_run = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            break
        if arg in ("-m", "--module"):
            return None
        if arg.startswith("--directory="):
            base = Path(os.path.normpath(cwd / arg.split("=", 1)[1]))
        elif arg in UV_VALUE_FLAGS:
            if arg == "--directory" and index + 1 < len(args):
                base = Path(os.path.normpath(cwd / args[index + 1]))
            index += 2
            continue
        elif not arg.startswith("-"):
            if seen_run:
                break
            if arg != "run":
                # `uv sync`, `uv lock`, `uv tool ...`: not a path form.
                return None
            seen_run = True
        index += 1
    if not seen_run or index >= len(args):
        return None
    target = args[index]
    if target in INTERPRETERS:
        return _interpreter(root, base, target, args[index + 1 :])
    if "://" in target:
        return None
    if target.endswith(UV_PATH_SUFFIXES) or "/" in target:
        return _file_problem(root, base, target)
    # A console script from the environment (`uv run pytest`): unchecked.
    return None


def _resolve(root: Path, cwd: Path, tokens: list[str]) -> str | None:
    command = tokens[0]
    if command == "npm":
        return _npm(root, cwd, tokens[1:])
    if command == "make":
        return _make(root, cwd, tokens[1:])
    if command in INTERPRETERS:
        return _interpreter(root, cwd, command, tokens[1:])
    if command == "uv":
        return _uv(root, cwd, tokens[1:])
    if command.startswith(("./", "scripts/")):
        path = Path(os.path.normpath(cwd / command))
        return None if path.is_file() else f"there is no `{_rel(root, path)}`"
    # tofu, npx, kubectl, git, ...: not this check's to judge.
    return None


def check_command_line(root: Path, code: str) -> list[str]:
    """Problems with one logical command line, run from the repo root."""
    code = strip_comment(code).strip()
    if not code or PLACEHOLDER.search(code):
        return []
    problems = []
    cwd: Path | None = root
    for segment in split_segments(code):
        tokens = _tokens(segment)
        if not tokens:
            continue
        if tokens[0] == "cd":
            cwd, problem = _cd(root, cwd, tokens[1:])
            if problem:
                problems.append(problem)
            if cwd is None:
                break
            continue
        problem = _resolve(root, cwd, tokens)
        if problem:
            problems.append(problem)
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


def check_commands(root: Path, lines: list[str]) -> tuple[list[Finding], int]:
    """Finding 5: commands in `## Commands` that do not resolve."""
    headings, fences = scan_markdown(lines)
    start = next((h for h in headings if h.level == 2 and h.text == "Commands"), None)
    if start is None:
        return [], 0
    end = next((h.line for h in headings if h.line > start.line and h.level <= 2), len(lines) + 1)
    findings = []
    checked = 0
    for fence in fences:
        if not start.line < fence.start < end:
            continue
        for number, code in _logical_lines(fence):
            if not strip_comment(code).strip():
                continue
            checked += 1
            for problem in check_command_line(root, code):
                findings.append(Finding(AGENTS_MD, number, f"`{code.strip()}`: {problem}"))
    return findings, checked


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------


def _read_agents(root: Path) -> str:
    try:
        return (root / AGENTS_MD).read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateError(f"`{AGENTS_MD}` is not UTF-8: {exc}") from exc


def cmd_render(root: Path) -> int:
    path = root / AGENTS_MD
    if not path.is_file():
        print(f"agents-md render: no {AGENTS_MD} in {root}; write the prose sections first", file=sys.stderr)
        return 2
    try:
        text = _read_agents(root)
        entities = load_catalog(root)
        rendered = compose(text, render_tail(entities, read_fleet_rules()))
    except GateError as exc:
        print(f"agents-md render: {exc}", file=sys.stderr)
        return 2
    if rendered == text:
        print(f"agents-md render: {AGENTS_MD} is already current")
        return 0
    path.write_bytes(rendered.encode("utf-8"))
    components = sum(1 for doc in entities if str(doc["kind"]) == "Component")
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
    findings += check_context_files(root)

    text = None
    if not (root / AGENTS_MD).is_file():
        findings.append(Finding(AGENTS_MD, None, f"no {AGENTS_MD} at the repo root; every fleet repo carries one"))
    else:
        try:
            text = _read_agents(root)
        except GateError as exc:
            findings.append(Finding(AGENTS_MD, None, str(exc)))

    entities = None
    try:
        entities = load_catalog(root)
    except GateError as exc:
        findings.append(Finding(CATALOG, None, str(exc)))

    checked = 0
    if text is not None:
        lines = text.split("\n")
        findings += check_contract(lines)
        if entities is not None:
            findings += check_render(text, entities, fleet_rules)
        command_findings, checked = check_commands(root, lines)
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
