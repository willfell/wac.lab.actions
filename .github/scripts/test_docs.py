"""The docs must describe the components as they are.

Each component (a composite action, or a reusable workflow other repos call)
has exactly one reference heading under docs/reference/, and the Input, Secret
and Output tables in that heading's section name exactly what the component
declares. Every script a component runs is in CHANGELOG.md's owner table, and
the README, docs/index.md and AGENTS.md each name every component. A change
that adds, renames or removes any of these without the docs fails here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs" / "reference"
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
FIRST_CELL_NAME = re.compile(r"^\|\s*\[?`([^`]+)`")


def components() -> dict[str, dict[str, dict]]:
    found: dict[str, dict[str, dict]] = {}
    for action in sorted(ROOT.glob("*/action.yml")):
        doc = yaml.safe_load(action.read_text())
        found[action.parent.name] = {
            "Input": doc.get("inputs") or {},
            "Secret": {},
            "Output": doc.get("outputs") or {},
        }
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        doc = yaml.safe_load(workflow.read_text())
        on = doc.get(True, doc.get("on"))
        call = on.get("workflow_call") if isinstance(on, dict) else None
        if call is None:
            continue
        call = call or {}
        found[workflow.stem] = {
            "Input": call.get("inputs") or {},
            "Secret": call.get("secrets") or {},
            "Output": call.get("outputs") or {},
        }
    return found


def outside_fences(text: str) -> list[str]:
    lines, fence = [], False
    for line in text.split("\n"):
        if line.startswith("```"):
            fence = not fence
            lines.append("")
            continue
        lines.append("" if fence else line)
    return lines


def sections(name: str) -> list[tuple[Path, list[str]]]:
    hits = []
    for page in sorted(REFERENCE.glob("*.md")):
        lines = outside_fences(page.read_text())
        for index, line in enumerate(lines):
            match = HEADING.match(line)
            if not match or match.group(2).replace("`", "") != name:
                continue
            level = len(match.group(1))
            end = len(lines)
            for later in range(index + 1, len(lines)):
                other = HEADING.match(lines[later])
                if other and len(other.group(1)) <= level:
                    end = later
                    break
            hits.append((page, lines[index + 1 : end]))
    return hits


def tables(lines: list[str]) -> list[list[list[str]]]:
    found, current = [], []
    for line in lines + [""]:
        if line.startswith("|"):
            current.append([cell.strip() for cell in line.strip().strip("|").split("|")])
        elif current:
            found.append(current)
            current = []
    return found


def component_rows(path: Path) -> set[str]:
    names = set()
    for table in tables(outside_fences(path.read_text())):
        if table[0][0] == "Component":
            for row in table[2:]:
                match = FIRST_CELL_NAME.match("| " + row[0])
                if match:
                    names.add(match.group(1))
    return names


class DocsTests(unittest.TestCase):
    def setUp(self):
        self.components = components()
        self.assertTrue(self.components, "no components found")

    def test_each_component_has_one_reference_heading(self):
        for name in self.components:
            with self.subTest(component=name):
                hits = sections(name)
                self.assertEqual(
                    len(hits),
                    1,
                    f"{name}: expected one heading named {name} under docs/reference/, "
                    f"found {[str(p.relative_to(ROOT)) for p, _ in hits]}",
                )

    def test_reference_tables_match_the_component(self):
        for name, declared in self.components.items():
            hits = sections(name)
            if len(hits) != 1:
                continue
            page, body = hits[0]
            documented: dict[str, dict[str, list[str]]] = {}
            for table in tables(body):
                kind = table[0][0]
                if kind not in declared:
                    continue
                rows = documented.setdefault(kind, {})
                for row in table[2:]:
                    match = FIRST_CELL_NAME.match("| " + row[0])
                    if match:
                        rows[match.group(1)] = row
            for kind, items in declared.items():
                with self.subTest(component=name, kind=kind):
                    self.assertEqual(
                        sorted(documented.get(kind, {})),
                        sorted(items),
                        f"{page.relative_to(ROOT)}: the {kind} table for {name} "
                        "must list exactly what the component declares",
                    )
            header = next(
                (t[0] for t in tables(body) if t[0][0] == "Input"), []
            )
            if "Default" not in header:
                continue
            column = header.index("Default")
            for input_name, row in documented.get("Input", {}).items():
                spec = declared["Input"].get(input_name)
                if spec is None or column >= len(row):
                    continue
                with self.subTest(component=name, input=input_name):
                    self.assertEqual(
                        row[column] == "required",
                        bool(spec.get("required")),
                        f"{page.relative_to(ROOT)}: {name} input {input_name} "
                        "says required exactly when the component requires it",
                    )

    def test_every_script_is_in_the_changelog_owner_table(self):
        changelog = (ROOT / "CHANGELOG.md").read_text()
        table = changelog[: changelog.index("| Tag |")]
        listed = set(re.findall(r"`(scripts/[^`]+|agents-md/)`", table))
        scripts = {
            f"scripts/{p.name}"
            for p in (ROOT / "scripts").iterdir()
            if p.is_file() and p.suffix in {".py", ".sh"}
        }
        self.assertEqual(
            sorted(scripts - listed),
            [],
            "every script must name its components in CHANGELOG.md's owner table",
        )
        for path in listed:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).exists(), f"{path} is listed but absent")

    def test_overviews_name_every_component(self):
        names = set(self.components)
        for path in [ROOT / "README.md", ROOT / "docs" / "index.md"]:
            with self.subTest(path=path.name):
                self.assertEqual(
                    sorted(component_rows(path)),
                    sorted(names),
                    f"{path.relative_to(ROOT)}: the Component table must list every component",
                )
        agents = (ROOT / "AGENTS.md").read_text()
        for name in names:
            with self.subTest(agents=name):
                self.assertIn(f"`{name}`", agents, f"AGENTS.md does not route {name}")


if __name__ == "__main__":
    unittest.main()
