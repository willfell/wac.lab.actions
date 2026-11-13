"""Render parity ignores exactly lab's normalized differences and reports every other one."""

import copy
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPT = Path(__file__).parents[1] / "wacd-parity.py"
SPEC = importlib.util.spec_from_file_location("wacd_parity", SCRIPT)
parity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(parity)

FIXTURE = Path(__file__).parent / "fixtures" / "wacd-parity" / "travel-kustomize.yaml"
HOOK = "argocd.argoproj.io/hook-delete-policy"
DIGEST = "sha256:" + "a" * 64


def load():
    return [doc for doc in yaml.safe_load_all(FIXTURE.read_text()) if doc is not None]


def find(docs, kind, name):
    for doc in docs:
        if doc["kind"] == kind and doc["metadata"]["name"] == name:
            return doc
    raise AssertionError(f"fixture has no {kind} {name}")


def deployment_container(docs):
    return find(docs, "Deployment", "travel-app")["spec"]["template"]["spec"]["containers"][0]


class ParityTest(unittest.TestCase):
    def test_identical_renders_match(self):
        docs = load()
        self.assertEqual(parity.compare(docs, docs), [])

    def test_normalized_differences_are_ignored(self):
        expected = load()
        actual = copy.deepcopy(expected)
        for doc in actual:
            doc["metadata"]["namespace"] = "travel"
            doc["metadata"]["labels"] = {"app.kubernetes.io/managed-by": "Helm"}
        container = deployment_container(actual)
        container["image"] = f"localhost:30500/travel-app:ci@{DIGEST}"
        container["ports"][0]["name"] = "http"
        container["readinessProbe"]["httpGet"]["port"] = "http"
        container["livenessProbe"]["httpGet"]["port"] = "http"
        job = find(actual, "Job", "travel-migrate")
        job["spec"]["template"]["spec"]["containers"][0]["image"] = "localhost:30500/travel-app:1.2.3"
        job["metadata"]["annotations"][HOOK] = "BeforeHookCreation,HookSucceeded"
        self.assertNotEqual(expected, actual)
        self.assertEqual(parity.compare(expected, actual), [])

    def test_real_differences_are_reported(self):
        expected = load()

        replicas = copy.deepcopy(expected)
        find(replicas, "Deployment", "travel-app")["spec"]["replicas"] = 2
        lines = parity.compare(expected, replicas)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("('Deployment', 'travel-app')", lines[0])
        self.assertIn("spec.replicas", lines[0])

        extra = copy.deepcopy(expected)
        extra.append({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "travel-extra"}})
        lines = parity.compare(expected, extra)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("extra", lines[0])
        self.assertIn("('ConfigMap', 'travel-extra')", lines[0])

        dropped = [doc for doc in copy.deepcopy(expected) if doc["kind"] != "Service"]
        lines = parity.compare(expected, dropped)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("missing", lines[0])
        self.assertIn("('Service', 'travel-app')", lines[0])

    def test_renamed_ingress_is_a_difference(self):
        expected = load()
        renamed = copy.deepcopy(expected)
        find(renamed, "Ingress", "travel-app-path")["metadata"]["name"] = "travel-app"
        lines = parity.compare(expected, renamed)
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(any("missing" in line and "travel-app-path" in line for line in lines))
        self.assertTrue(any("extra" in line for line in lines))

    def test_cli_exit_codes(self):
        expected = load()
        changed = copy.deepcopy(expected)
        find(changed, "Deployment", "travel-app")["spec"]["replicas"] = 3
        with tempfile.TemporaryDirectory() as tmp:
            changed_path = Path(tmp) / "helm.yaml"
            changed_path.write_text(yaml.safe_dump_all(changed))

            same = subprocess.run(
                [sys.executable, str(SCRIPT), str(FIXTURE), str(FIXTURE)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(same.returncode, 0, same.stderr)
            self.assertEqual(same.stdout.strip(), f"parity: {len(expected)} objects match")

            differ = subprocess.run(
                [sys.executable, str(SCRIPT), str(FIXTURE), str(changed_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(differ.returncode, 1)
            self.assertIn("spec.replicas", differ.stdout)
            self.assertNotIn("parity: ", differ.stdout)


if __name__ == "__main__":
    unittest.main()
