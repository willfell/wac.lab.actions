import copy
import importlib.util
from pathlib import Path
import unittest
import yaml

spec = importlib.util.spec_from_file_location("check_actions", Path(__file__).with_name("check_actions.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class IdentityTests(unittest.TestCase):
    def test_gates_reject_floating_caller_and_undefined_identity(self):
        for name in ("agents-md", "techdocs"):
            path = module.REPO_ROOT / f".github/workflows/{name}.yml"
            original = yaml.safe_load(path.read_text())
            self.assertEqual(module._check_gate_identity(path, original), [])
            for ref in ("", "main", "${{ github.workflow_sha }}", "${{ github.job_workflow_sha }}"):
                with self.subTest(gate=name, ref=ref):
                    changed = copy.deepcopy(original)
                    shared = next(step for step in changed["jobs"]["gate"]["steps"] if step.get("with", {}).get("repository"))
                    shared["with"]["ref"] = ref
                    self.assertTrue(module._check_gate_identity(path, changed))
            changed = copy.deepcopy(original)
            changed["jobs"]["gate"]["steps"] = [step for step in changed["jobs"]["gate"]["steps"] if "SHARED_WORKFLOW_SHA" not in step.get("env", {})]
            self.assertTrue(module._check_gate_identity(path, changed))

if __name__ == "__main__":
    unittest.main()
