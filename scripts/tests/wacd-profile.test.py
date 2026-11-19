"""The wacd profile validator names the failing field and rejects anything unknown."""

import ast
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "wacd-profile.py"
DOCS = Path(__file__).parents[1] / "wacd-docs.py"
SPEC = importlib.util.spec_from_file_location("wacd_profile", SCRIPT)
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)

TRAVEL = {
    "schema_version": 1,
    "app": "travel",
    "chart_version": "1.0.0",
    "values_file": "deploy/values.yaml",
    "tag_prefix": "v",
    "health_url": "http://travel-app.travel.svc.cluster.local:3000/api/health",
    "images": [
        {
            "name": "travel-app",
            "dockerfile": "Dockerfile",
            "context": ".",
            "sha_build_arg": "TRAVEL_BUILD_SHA",
        }
    ],
    "ci": {
        "stack": "node",
        "node_version": "22",
        "commands": ["npm run lint", "npm run typecheck", "npm test", "npm run build"],
    },
}


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        (self.root / ".github").mkdir()
        (self.root / "deploy").mkdir()
        (self.root / "deploy" / "values.yaml").write_text("replicas: 1\n")
        self.path = self.root / ".github" / "wacd.json"

    def write(self, data):
        self.path.write_text(json.dumps(data))
        return self.path

    def test_the_spec_example_is_valid(self):
        self.write(TRAVEL)
        self.assertEqual(profile.load(self.path), TRAVEL)

    def test_profile_must_be_utf8_json(self):
        self.path.write_bytes(b"\xff\xfe{")
        with self.assertRaises(profile.ProfileError) as caught:
            profile.load(self.path)
        self.assertIn("UTF-8", str(caught.exception))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("wacd profile:"))
        self.assertNotIn("Traceback", result.stderr)


CASES = Path(__file__).parents[1] / "fixtures" / "wacd-profile-cases.json"
CASE_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
CASE_INPUT = {"profile": ".github/wacd.json", "contract": ".wacd-contract/contract.json"}
MATCH = "docs-path-match"
PREFIX = "wacd profile: "
MATCH_PREFIX = "wacd docs: "

# Functions whose refusals no case can express, and why. The test fails if one
# of them stops refusing, so the list cannot outlive the code it excuses.
NOT_A_CASE = {
    "read_json": "a case's input is a JSON value the test writes, so an unreadable, "
    "non-UTF-8 or malformed file is not expressible, and the sentence names the file's own path",
    "node_layout": "node-layout reads a checkout, not a profile or a copy",
}
# Refusals only a copy reaches; every other refusal is reachable from a profile
# file and needs a profile case of its own.
CONTRACT_ONLY = {"from_contract", "contract_object", "contract_field"}
# wacd-docs.py's refusals that only its match command and the scope steps make,
# with the field path `pattern` or `docs_paths`; every other one is a profile's.
MATCH_ONLY = {"check_grammar"}
SCOPE_ONLY = {"read_patterns"}


def text_pattern(node, what):
    """A regex for a refusal argument: literal text, with each f-string field as a wildcard."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return re.escape(node.value)
    if isinstance(node, ast.JoinedStr):
        return "".join(
            re.escape(part.value) if isinstance(part, ast.Constant) else ".*" for part in node.values
        )
    if what == "path":
        return ".*"
    raise AssertionError(f"line {node.lineno}: a refusal's message must be literal text or an f-string")


def refusal_templates(source):
    """Every refusal sentence the script can print, as (function, line, regex).

    A `fail(path, message)` call is one template, `<path>: <message>`. The
    pattern check's own `fail` is replaced by its callers, each of which names
    what the value must be.
    """
    tree = ast.parse(source)
    templates, excused = [], set()
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "ProfileError" and function.name != "fail":
                    raise AssertionError(f"line {node.lineno}: refuse through fail(), so the refusal is a template")
                if node.func.id == "fail" and function.name == "check_pattern":
                    continue
                if node.func.id == "fail":
                    path, message = node.args
                elif node.func.id == "check_pattern":
                    _, path, _, what = node.args
                    message = ast.JoinedStr([ast.Constant("must be "), what])
                    if not (isinstance(what, ast.Constant) and isinstance(what.value, str)):
                        raise AssertionError(f"line {node.lineno}: check_pattern's description must be literal")
                else:
                    continue
                if function.name in NOT_A_CASE:
                    excused.add(function.name)
                    continue
                regex = text_pattern(path, "path") + re.escape(": ") + text_pattern(message, "message")
                templates.append((function.name, node.lineno, re.compile(regex)))
    return templates, excused


class CaseTableTest(unittest.TestCase):
    """`scripts/fixtures/wacd-profile-cases.json` is the contract hemi's check mirrors.

    hemi loads the table at the tag it pins and keys on each case's name and
    outcome, so every case must hold against the real CLI, and every refusal
    the script can print must have a case.
    """

    @classmethod
    def setUpClass(cls):
        cls.table = json.loads(CASES.read_text())
        cls.cases = cls.table["cases"]

    def test_the_table_has_the_published_shape(self):
        self.assertEqual(set(self.table), {"version", "cases"})
        self.assertEqual(self.table["version"], 1)
        self.assertIsInstance(self.cases, list)
        self.assertTrue(self.cases)
        names = [case.get("name") for case in self.cases]
        self.assertEqual(len(names), len(set(names)), "case names are unique")
        for case in self.cases:
            with self.subTest(case=case.get("name")):
                self.assertIsInstance(case["name"], str)
                self.assertTrue(CASE_NAME.fullmatch(case["name"]), "kebab-case name")
                self.assertTrue({"name", "kind", "input", "expect"} <= set(case) <= {"name", "kind", "input", "files", "expect"})
                self.assertIn(case["kind"], (*CASE_INPUT, MATCH))
                expect = case["expect"]
                if case["kind"] == MATCH:
                    self.assertNotIn("files", case)
                    self.assertEqual(set(case["input"]), {"pattern", "path"})
                    self.assertTrue(all(isinstance(value, str) for value in case["input"].values()))
                    self.assertIn(set(expect), ({"match"}, {"refused"}))
                    self.assertIsInstance(expect.get("match", expect.get("refused")), (bool, str))
                    continue
                if "refused" in expect:
                    self.assertEqual(set(expect), {"refused"})
                    self.assertIsInstance(expect["refused"], str)
                else:
                    self.assertIn(set(expect), ({"ok"}, {"ok", "outputs"}))
                    self.assertIs(expect["ok"], True)
                    for key, value in expect.get("outputs", {}).items():
                        self.assertIsInstance(key, str)
                        self.assertIsInstance(value, str)
                for path, content in case.get("files", {}).items():
                    self.assertIsInstance(content, str)
                    self.assertFalse(path.startswith("/") or ".." in path.split("/"), path)
                    self.assertNotIn(path, CASE_INPUT.values())

    def run_case(self, case):
        if case["kind"] == MATCH:
            return subprocess.run(
                [sys.executable, "-I", str(DOCS), "match", case["input"]["pattern"], case["input"]["path"]],
                capture_output=True, text=True, check=False,
            )
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            for relative, content in case.get("files", {}).items():
                (repo / relative).parent.mkdir(parents=True, exist_ok=True)
                (repo / relative).write_text(content)
            target = repo / CASE_INPUT[case["kind"]]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(case["input"]))
            args = [str(target)] if case["kind"] == "profile" else ["--contract", str(target)]
            return subprocess.run(
                [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
            )

    def test_every_case_holds_against_the_cli(self):
        for case in self.cases:
            with self.subTest(case=case["name"]):
                result = self.run_case(case)
                expect = case["expect"]
                prefix = MATCH_PREFIX if case["kind"] == MATCH else PREFIX
                if "refused" in expect:
                    self.assertEqual((result.returncode, result.stdout), (1, ""))
                    self.assertEqual(result.stderr, f"{prefix}{expect['refused']}\n")
                elif case["kind"] == MATCH:
                    self.assertEqual((result.returncode, result.stderr), (0, ""))
                    self.assertEqual(result.stdout, f"match={'true' if expect['match'] else 'false'}\n")
                else:
                    self.assertEqual((result.returncode, result.stderr), (0, ""))
                    printed = parse_github_output(result.stdout)
                    for key, value in expect.get("outputs", {}).items():
                        self.assertEqual(printed.get(key), value, key)

    def test_every_refusal_the_script_can_print_has_a_case(self):
        templates, excused = refusal_templates(SCRIPT.read_text())
        self.assertTrue(templates, "found no refusals in the script")
        self.assertEqual(excused, set(NOT_A_CASE), "every excused function still refuses")
        docs_templates, docs_excused = refusal_templates(DOCS.read_text())
        self.assertTrue(docs_templates, "found no refusals in wacd-docs.py")
        self.assertEqual(docs_excused, set())
        refused = {kind: [c["expect"]["refused"] for c in self.cases if c["kind"] == kind and "refused" in c["expect"]]
                   for kind in (*CASE_INPUT, MATCH)}
        missing = []
        for function, line, regex in templates:
            kind = "contract" if function in CONTRACT_ONLY else "profile"
            if not any(regex.fullmatch(sentence) for sentence in refused[kind]):
                missing.append(f"line {line} in {function}: no {kind} case matches {regex.pattern}")
        for function, line, regex in docs_templates:
            if function in SCOPE_ONLY:
                continue
            kinds = (MATCH, "profile", "contract") if function in MATCH_ONLY else ("profile", "contract")
            for kind in kinds:
                sentences = refused[kind]
                if kind == MATCH:
                    sentences = [s.removeprefix("pattern: ") for s in sentences]
                else:
                    sentences = [s.split(": ", 1)[1] for s in sentences if s.startswith("ci.docs_paths")]
                message = regex.pattern.split(re.escape(": "), 1)[1]
                if not any(re.fullmatch(message, sentence) for sentence in sentences):
                    missing.append(f"line {line} in wacd-docs.py {function}: no {kind} case matches {message}")
        self.assertEqual(missing, [])


def parse_github_output(text):
    """Read key=value and key<<DELIM blocks the way the runner does."""
    values = {}
    lines = iter(text.splitlines())
    for line in lines:
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            key, delimiter = line.split("<<", 1)
            body = []
            for inner in lines:
                if inner == delimiter:
                    break
                body.append(inner)
            else:
                raise AssertionError(f"block {key} never closed")
            values[key] = "\n".join(body)
        else:
            key, value = line.split("=", 1)
            values[key] = value
    return values


FIXTURES = Path(__file__).parent / "fixtures" / "wacd-contract"


class ContractTest(unittest.TestCase):
    """`--contract` reads hemi's copy and emits the outputs the profile it came from emits.

    The fixture contract is the shape hemi's resolveContract builds from the
    fixture profile and values file, so the two runs must agree line for line.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        (self.root / ".github").mkdir(parents=True)
        (self.root / "deploy").mkdir()
        (self.root / ".wacd-contract").mkdir()
        (self.root / "deploy" / "values.yaml").write_text((FIXTURES / "values.yaml").read_text())
        self.profile_path = self.root / ".github" / "wacd.json"
        self.contract_path = self.root / ".wacd-contract" / "contract.json"
        self.write(json.loads((FIXTURES / "wacd.json").read_text()), json.loads((FIXTURES / "contract.json").read_text()))

    def write(self, data, contract):
        self.profile_path.write_text(json.dumps(data))
        self.contract_path.write_text(json.dumps(contract))

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
        )

    def assertSameOutputs(self):
        files = self.run_cli(str(self.profile_path))
        copy_ = self.run_cli("--contract", str(self.contract_path))
        self.assertEqual(files.returncode, 0, files.stderr)
        self.assertEqual(copy_.returncode, 0, copy_.stderr)
        self.assertEqual(copy_.stdout, files.stdout)
        return copy_.stdout.splitlines()

    def test_a_contract_emits_the_outputs_of_the_profile_it_came_from(self):
        lines = self.assertSameOutputs()
        self.assertIn("values_file=deploy/values.yaml", lines)
        self.assertIn("health_expect_db_ok=false", lines)
        self.assertIn("apt_packages=python3 g++", lines)

    def test_defaults_and_the_go_stack_derive_the_same_outputs(self):
        data = json.loads((FIXTURES / "wacd.json").read_text())
        contract = json.loads((FIXTURES / "contract.json").read_text())
        del data["health_expect_db_ok"]
        contract["delivery"]["healthExpectDbOk"] = True
        data["ci"] = {"stack": "go", "go_version_file": "go.mod", "commands": ["go vet ./...", "go test ./..."]}
        contract["build"] = {
            "images": contract["build"]["images"],
            "stack": "go",
            "goVersionFile": "go.mod",
            "aptPackages": [],
        }
        contract["gates"] = [{"name": "checks", "commands": ["go vet ./...", "go test ./..."], "required": True}]
        (self.root / "go.mod").write_text("module travel\n\ngo 1.24\n")
        self.write(data, contract)
        lines = self.assertSameOutputs()
        self.assertIn("apt_packages=", lines)


class NodeLayoutTest(unittest.TestCase):
    """wac-ci installs at the root only when there is a root package, and
    caches npm over every lockfile, or not at all when there is none."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()

    def touch(self, relative):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")

    def layout(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "node-layout", str(self.repo)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return parse_github_output(result.stdout)

    def test_prefixed_packages_without_a_root_package(self):
        self.touch("app/package.json")
        self.touch("app/package-lock.json")
        self.touch("sync-agent/package.json")
        self.touch("sync-agent/package-lock.json")
        self.touch(".claude/skills/llc-month/ledger/package-lock.json")
        self.touch("app/node_modules/dep/package-lock.json")
        self.touch(".wacd/scripts/package-lock.json")
        (self.repo / ".wacd" / ".git").mkdir()
        out = self.layout()
        self.assertEqual(out["root_package"], "false")
        self.assertEqual(
            out["lockfiles"].splitlines(),
            [
                ".claude/skills/llc-month/ledger/package-lock.json",
                "app/package-lock.json",
                "sync-agent/package-lock.json",
            ],
        )

    def test_root_package_with_its_lockfile(self):
        self.touch("package.json")
        self.touch("package-lock.json")
        out = self.layout()
        self.assertEqual(out["root_package"], "true")
        self.assertEqual(out["lockfiles"], "package-lock.json")

    def test_no_lockfiles_at_all(self):
        self.touch("package.json")
        out = self.layout()
        self.assertEqual(out["root_package"], "true")
        self.assertEqual(out["lockfiles"], "")

    def test_missing_repo_fails_cleanly(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "node-layout", str(self.repo / "absent")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("wacd profile:"))


if __name__ == "__main__":
    unittest.main()
