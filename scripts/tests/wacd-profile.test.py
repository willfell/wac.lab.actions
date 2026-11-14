"""The wacd profile validator names the failing field and rejects anything unknown."""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "wacd-profile.py"
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
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / ".github").mkdir()
        (self.root / "deploy").mkdir()
        (self.root / "deploy" / "values.yaml").write_text("replicas: 1\n")
        self.path = self.root / ".github" / "wacd.json"

    def write(self, data):
        self.path.write_text(json.dumps(data))
        return self.path

    def profile(self, **changes):
        data = copy.deepcopy(TRAVEL)
        data.update(changes)
        return data

    def assertRejected(self, data, field):
        self.write(data)
        with self.assertRaises(profile.ProfileError) as caught:
            profile.load(self.path)
        self.assertIn(field, str(caught.exception))

    def cli(self, data):
        self.write(data)
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.path)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_spec_example_is_valid(self):
        self.write(TRAVEL)
        self.assertEqual(profile.load(self.path), TRAVEL)

    def test_unknown_top_level_field(self):
        self.assertRejected(self.profile(colour="red"), "colour")

    def test_unknown_nested_fields(self):
        data = self.profile()
        data["images"][0]["tag"] = "x"
        self.assertRejected(data, "images[0].tag")
        data = self.profile()
        data["ci"]["cache"] = True
        self.assertRejected(data, "ci.cache")

    def test_schema_version_must_be_one(self):
        self.assertRejected(self.profile(schema_version=2), "schema_version")
        self.assertRejected(self.profile(schema_version=True), "schema_version")

    def test_values_file_must_exist_and_stay_inside(self):
        self.assertRejected(self.profile(values_file="deploy/missing.yaml"), "does not exist")
        (self.base / "x.yaml").write_text("escaped: true\n")
        self.assertRejected(self.profile(values_file="../x.yaml"), "'..' segment")
        outside = self.base / "abs.yaml"
        outside.write_text("absolute: true\n")
        self.assertRejected(self.profile(values_file=str(outside)), "relative path inside the repo")

    def test_relative_paths_reject_line_breaks(self):
        (self.root / "deploy" / "values.yaml\nextra=1").write_text("x: 1\n")
        self.assertRejected(
            self.profile(values_file="deploy/values.yaml\nextra=1"),
            "relative path inside the repo",
        )
        data = self.profile()
        data["images"][0]["dockerfile"] = "Dockerfile\r"
        self.assertRejected(data, "images[0].dockerfile")

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

    def test_duplicate_image_names(self):
        data = self.profile()
        data["images"].append(
            {"name": "travel-app", "dockerfile": "Dockerfile", "context": "."}
        )
        self.assertRejected(data, "images[1].name")

    def test_stack_versions_match_the_stack(self):
        data = self.profile()
        del data["ci"]["node_version"]
        self.assertRejected(data, "ci.node_version")
        data = self.profile(ci={"stack": "go", "node_version": "22", "go_version": "1.24", "commands": ["go test ./..."]})
        self.assertRejected(data, "ci.node_version")
        data = self.profile(ci={"stack": "go", "commands": ["go test ./..."]})
        self.assertRejected(data, "ci.go_version")

    def go_ci(self, **fields):
        return self.profile(ci={"stack": "go", "commands": ["go test ./..."], **fields})

    def test_go_version_file_is_accepted_and_reaches_the_ci_output(self):
        (self.root / "go.mod").write_text("module example.com/x\n\ngo 1.24.2\n")
        (self.root / "svc").mkdir()
        (self.root / "svc" / ".go-version").write_text("1.24.2\n")
        for path in ("go.mod", "svc/.go-version"):
            with self.subTest(path=path):
                data = self.go_ci(go_version_file=path)
                result = self.cli(data)
                self.assertEqual(result.returncode, 0, result.stderr)
                ci_line = next(l for l in result.stdout.splitlines() if l.startswith("ci="))
                self.assertEqual(json.loads(ci_line[3:])["go_version_file"], path)
                self.assertNotIn("go_version", json.loads(ci_line[3:]))

    def test_go_version_file_and_go_version_are_exclusive(self):
        (self.root / "go.mod").write_text("module example.com/x\n")
        self.assertRejected(
            self.go_ci(go_version="1.24", go_version_file="go.mod"), "ci.go_version_file: not allowed together"
        )

    def test_go_stack_needs_one_version_source(self):
        self.assertRejected(self.go_ci(), "ci.go_version: required when stack is 'go', unless go_version_file")

    def test_go_version_file_must_exist_and_stay_inside(self):
        self.assertRejected(self.go_ci(go_version_file="go.mod"), "ci.go_version_file: go.mod does not exist")
        (self.root / "dir").mkdir()
        self.assertRejected(self.go_ci(go_version_file="dir"), "ci.go_version_file: dir does not exist")
        self.assertRejected(self.go_ci(go_version_file="../go.mod"), "'..' segment")
        self.assertRejected(self.go_ci(go_version_file="/etc/hostname"), "relative path inside the repo")
        self.assertRejected(self.go_ci(go_version_file=""), "relative path inside the repo")
        self.assertRejected(self.go_ci(go_version_file=["go.mod"]), "must be a string")

    def test_go_version_file_is_refused_for_node(self):
        (self.root / "go.mod").write_text("module example.com/x\n")
        data = self.profile()
        data["ci"]["go_version_file"] = "go.mod"
        self.assertRejected(data, "ci.go_version_file: not allowed when stack is 'node'")

    def test_empty_commands_and_multiline_command(self):
        data = self.profile()
        data["ci"]["commands"] = []
        self.assertRejected(data, "ci.commands")
        data["ci"]["commands"] = ["a\nb"]
        self.assertRejected(data, "ci.commands[0]")

    def test_health_url_must_be_in_cluster_http(self):
        self.assertRejected(self.profile(health_url="https://wacwini.com/x"), "health_url")

    def test_health_expect_db_ok_is_an_optional_boolean(self):
        result = self.cli(TRAVEL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("health_expect_db_ok=true", result.stdout.splitlines())
        result = self.cli(self.profile(health_expect_db_ok=False))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("health_expect_db_ok=false", result.stdout.splitlines())
        self.assertRejected(self.profile(health_expect_db_ok="false"), "health_expect_db_ok")
        self.assertRejected(self.profile(health_expect_db_ok=0), "health_expect_db_ok")

    def test_prefixes(self):
        for good in ("", "v", "app-v"):
            self.write(self.profile(tag_prefix=good))
            profile.load(self.path)
        for bad in ("V", "-v", "v/"):
            self.assertRejected(self.profile(tag_prefix=bad), "tag_prefix")

    def with_apt(self, packages):
        data = self.profile()
        data["ci"]["apt_packages"] = packages
        return data

    def test_apt_packages_are_accepted_and_printed_space_separated(self):
        data = self.with_apt(["python3", "make", "g++", "libssl3t64", "lib.x-1"])
        self.write(data)
        self.assertEqual(profile.load(self.path), data)
        result = self.cli(data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "apt_packages=python3 make g++ libssl3t64 lib.x-1", result.stdout.splitlines()
        )

    def test_apt_packages_are_optional_and_print_empty_when_absent(self):
        result = self.cli(TRAVEL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("apt_packages=", result.stdout.splitlines())

    def test_apt_packages_apply_to_go_too(self):
        data = self.profile(
            ci={"stack": "go", "go_version": "1.24", "commands": ["go test ./..."], "apt_packages": ["make"]}
        )
        self.write(data)
        self.assertEqual(profile.load(self.path), data)

    def test_apt_packages_reject_bad_names(self):
        for bad in ("Make", "-o", "", "make g++", "make;rm", "$(id)", "make\n", "a/b", "make:arm64", "é"):
            with self.subTest(name=bad):
                self.assertRejected(self.with_apt(["python3", bad]), "ci.apt_packages[1]")
        self.assertRejected(self.with_apt([1]), "ci.apt_packages[0]")
        self.assertRejected(self.with_apt([None]), "ci.apt_packages[0]")

    def test_apt_packages_reject_duplicates(self):
        self.assertRejected(self.with_apt(["make", "g++", "make"]), "ci.apt_packages[2]: duplicate")

    def test_apt_packages_reject_an_empty_list(self):
        self.assertRejected(self.with_apt([]), "ci.apt_packages: must be a non-empty list")

    def test_apt_packages_must_be_a_list(self):
        for bad in ("make", {"make": True}, None, True):
            with self.subTest(value=bad):
                self.assertRejected(self.with_apt(bad), "ci.apt_packages: must be a non-empty list")

    def test_cli_prints_outputs_and_fails_cleanly(self):
        result = self.cli(TRAVEL)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn("app=travel", lines)
        self.assertTrue(any(line.startswith("images=[") for line in lines))
        self.assertTrue(any(line.startswith("ci={") for line in lines))
        result = self.cli(self.profile(colour="red"))
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("wacd profile:"))


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
