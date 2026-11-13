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
        self.root = Path(self._tmp.name)
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
        self.assertRejected(self.profile(values_file="deploy/missing.yaml"), "values_file")
        self.assertRejected(self.profile(values_file="../x.yaml"), "values_file")
        self.assertRejected(self.profile(values_file="/etc/x"), "values_file")

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


if __name__ == "__main__":
    unittest.main()
