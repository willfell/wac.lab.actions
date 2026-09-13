#!/usr/bin/env bash
# Validate a repo's mkdocs.yml against the fleet TechDocs nav standard.
#
# Usage: check-docs-nav.sh <mkdocs.yml> [<mkdocs.yml> ...]
#
# The standard lives in the wac:docs-onboard-repo skill. This script is the
# executable half of it: the seven category names, their order, the Overview
# rule, and the standing exclude_docs block. It does NOT check that a page is
# filed under the RIGHT category - only a reader can judge that. It checks the
# things that are mechanical, so drift cannot ship unnoticed.
#
# Placement itself is enforced elsewhere and more strongly: under
# `mkdocs build --strict`, a page that is neither in the nav nor excluded
# fails the build.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: $(basename "$0") <mkdocs.yml> [<mkdocs.yml> ...]" >&2
  exit 2
fi

python3 - "$@" <<'PY'
import re
import sys

CATEGORIES = [
    "Overview",
    "Guides",
    "Runbooks",
    "Reference",
    "Architecture",
    "Decisions",
    "Records",
]
EXCLUDES = [
    "superpowers/",
    "handoffs/",
    "proposals/",
    "audits/",
    "design/",
    "plans/",
]
VALIDATION = [
    ("nav", "omitted_files", "warn"),
    ("nav", "not_found", "warn"),
    ("links", "unrecognized_links", "warn"),
]

def check(path):
    bad = []

    def fail(msg):
        bad.append(msg)
        print(f"{path}: {msg}", file=sys.stderr)

    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError as exc:
        fail(f"cannot read: {exc}")
        return False

    if not any(re.match(r"^plugins:", ln) for ln in lines):
        fail("no `plugins:` block; the portal expects techdocs-core to be loaded")
    elif not any(ln.strip() == "- techdocs-core" for ln in lines):
        fail("plugins block does not load techdocs-core")

    body = "\n".join(lines)
    if "exclude_docs:" not in body:
        fail("no `exclude_docs:` block; the standing block is mandatory")
    else:
        for tree in EXCLUDES:
            if not re.search(rf"^\s+{re.escape(tree)}\s*$", body, re.M):
                fail(f"exclude_docs is missing the standing entry `{tree}`")

    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith("#"):
            fail(f"line {i} is a comment; this file carries config and nothing else")

    if "validation:" not in body:
        fail(
            "no `validation:` block; without it --strict does NOT fail on a page "
            "missing from the nav, and the nav is decorative rather than enforced"
        )
    else:
        for section, key, level in VALIDATION:
            if not re.search(rf"^  {section}:$", body, re.M) or not re.search(
                rf"^    {key}: {level}$", body, re.M
            ):
                fail(f"validation block is missing `{section}.{key}: {level}`")

    try:
        start = next(i for i, ln in enumerate(lines) if re.match(r"^nav:\s*$", ln))
    except StopIteration:
        fail("no `nav:` block; an auto-generated nav is not acceptable")
        return False

    seen = []
    pages = {}
    current = None
    for ln in lines[start + 1:]:
        if ln.strip() == "":
            continue
        if not ln.startswith(" "):
            break
        child = re.match(r"^ {6}- ([^:]+):(.*)$", ln)
        if child:
            title, target = child.group(1).strip(), child.group(2).strip()
            if not target:
                fail(
                    f"`{title}` under `{current}` is a sub-group; categories are one "
                    "level deep, and per-repo sub-grouping is the divergence the "
                    "fixed categories exist to stop"
                )
            else:
                pages.setdefault(target, []).append(current)
            continue
        if re.match(r"^ {7,}", ln):
            fail(f"nav is nested deeper than one level: {ln!r}")
            continue

        m = re.match(r"^  - ([^:]+):(.*)$", ln)
        if not m:
            if re.match(r"^ {3,}", ln):
                continue
            fail(f"nav entry is not in the standard shape: {ln!r}")
            continue
        name, rest = m.group(1).strip(), m.group(2).strip()
        if name not in CATEGORIES:
            fail(
                f"`{name}` is not one of the seven categories "
                f"({', '.join(CATEGORIES)}); categories are fixed, not per-repo",
            )
            continue
        if name in seen:
            fail(f"`{name}` appears more than once")
            continue
        if name == "Overview" and rest != "index.md":
            got = rest if rest else "a nested group"
            fail(
                f"Overview must be the flat mapping `- Overview: index.md`, got {got}"
            )
        if name != "Overview" and rest:
            fail(f"`{name}` must be a group with pages nested under it, not a single page")
        seen.append(name)
        current = name
        if name == "Overview" and rest:
            pages.setdefault(rest, []).append(name)

    for target, cats in pages.items():
        if len(cats) > 1:
            fail(
                f"`{target}` is filed under {' and '.join(cats)}; every page sits "
                "in exactly one category"
            )

    if not seen:
        fail("nav block has no valid category entries")
        return False
    if seen[0] != "Overview":
        fail(f"first nav entry must be Overview, got `{seen[0]}`")
    order = [CATEGORIES.index(n) for n in seen]
    if order != sorted(order):
        fail(
            "categories are out of order; the fixed order is "
            + " -> ".join(CATEGORIES),
        )

    if not bad:
        print(f"{path}: ok ({', '.join(seen)})")
    return not bad


ok = True
for arg in sys.argv[1:]:
    ok = check(arg) and ok

sys.exit(0 if ok else 1)
PY
