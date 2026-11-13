import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("check_actions", Path(__file__).with_name("check_actions.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

GUARD_RUN = '[[ "$SHARED_WORKFLOW_SHA" =~ ^[0-9a-f]{40}$ ]]'
SHA = "${{ job.workflow_sha }}"


def guard_step():
    return {"env": {"SHARED_WORKFLOW_SHA": SHA}, "run": GUARD_RUN}


def shared_checkout(**overrides):
    with_ = {"repository": "willfell/wac.lab.actions", "ref": SHA, "persist-credentials": False}
    with_.update(overrides)
    return {"uses": "actions/checkout@v4", "with": with_}


def workflow(jobs):
    return {
        "name": "wf",
        "on": {"workflow_call": {"inputs": {"runner": {"type": "string", "default": ""}}}},
        "jobs": jobs,
    }


def nested_call(**overrides):
    job = {"uses": "./.github/workflows/actionlint.yml", "with": {"runner": "${{ inputs.runner }}"}}
    job.update(overrides)
    return job


def runner_problems(jobs, name="wac-ci.yml"):
    path = Path(name)
    return module._check_runner_is_a_caller_choice(path, workflow(jobs))


class ReusableWorkflowTests(unittest.TestCase):
    def test_nested_call_job_passes_the_runner_through(self):
        self.assertEqual(runner_problems({"lint": nested_call()}), [])

    def test_nested_call_job_without_runner_fails(self):
        for label, job in (
            ("empty with", nested_call(**{"with": {}})),
            ("no with", {"uses": "./.github/workflows/actionlint.yml"}),
            ("other runner", nested_call(**{"with": {"runner": "ubuntu-latest"}})),
            ("runs-on added", nested_call(**{"runs-on": "${{ inputs.runner || vars.CI_RUNNER_SMALL }}"})),
        ):
            with self.subTest(case=label):
                problems = runner_problems({"lint": job})
                self.assertTrue(problems)
                self.assertIn("'lint'", problems[0])

    def test_step_jobs_keep_the_exact_runs_on_rule(self):
        good = {"runs-on": module.RUNS_ON_EXPRESSION, "steps": []}
        self.assertEqual(runner_problems({"gate": good}), [])
        self.assertTrue(runner_problems({"gate": {"runs-on": "ubuntu-latest", "steps": []}}))

    def test_shared_checkout_needs_workflow_sha_and_guard(self):
        path = Path("wac-ci.yml")

        def check(steps):
            return module._check_shared_checkouts(path, workflow({"gate": {"steps": steps}}))

        self.assertEqual(check([guard_step(), shared_checkout()]), [])
        self.assertEqual(check([{"uses": "actions/checkout@v4"}]), [])
        self.assertTrue(check([guard_step(), shared_checkout(ref="main")]))
        self.assertTrue(check([guard_step(), shared_checkout(ref="")]))
        self.assertTrue(check([shared_checkout()]))
        self.assertTrue(check([shared_checkout(), guard_step()]))
        self.assertTrue(check([guard_step(), shared_checkout(**{"persist-credentials": True})]))
        no_flag = shared_checkout()
        del no_flag["with"]["persist-credentials"]
        self.assertTrue(check([guard_step(), no_flag]))
        weak_guard = guard_step()
        weak_guard["run"] = "true"
        self.assertTrue(check([weak_guard, shared_checkout()]))
        self.assertIn("'gate'", check([shared_checkout()])[0])

    def test_shared_checkout_is_checked_in_every_job(self):
        path = Path("wac-cd.yml")
        doc = workflow({
            "ok": {"steps": [guard_step(), shared_checkout()]},
            "bad": {"steps": [shared_checkout()]},
        })
        problems = module._check_shared_checkouts(path, doc)
        self.assertEqual(len(problems), 1)
        self.assertIn("'bad'", problems[0])

    def test_jobs_may_not_declare_an_environment(self):
        for name in ("wac-ci.yml", "wac-cd.yml"):
            with self.subTest(workflow=name):
                doc = workflow({"release": {"runs-on": module.RUNS_ON_EXPRESSION, "environment": "production", "steps": []}})
                problems = module._check_no_environment(Path(name), doc)
                self.assertTrue(any("environment" in p and "'release'" in p for p in problems))

    def test_environment_is_refused_in_every_reusable_workflow(self):
        doc = workflow({"release": {"runs-on": module.RUNS_ON_EXPRESSION, "environment": {"name": "x"}, "steps": []}})
        problems = module._check_no_environment(Path("other.yml"), doc)
        self.assertTrue(any("environment" in p for p in problems))

    def test_reusable_workflow_check_runs_the_shared_checkout_and_environment_rules(self):
        cases = (
            ("shared checkout at main", {"gate": {
                "runs-on": module.RUNS_ON_EXPRESSION,
                "steps": [guard_step(), shared_checkout(ref="main")],
            }}, "shared checkout must use ref"),
            ("job environment", {"release": {
                "runs-on": module.RUNS_ON_EXPRESSION,
                "environment": "production",
                "steps": [],
            }}, "declares `environment`"),
        )
        for label, jobs, expected in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "wac-ci.yml"
                doc = workflow(jobs)
                path.write_text("name: wf\n")
                problems = module._check_reusable_workflow(path, doc)
                self.assertTrue(any(expected in p for p in problems), problems)

    def test_shared_repository_match_ignores_case(self):
        doc = workflow({"gate": {"steps": [guard_step(), shared_checkout(repository="WillFell/Wac.Lab.Actions", ref="main")]}})
        self.assertTrue(module._check_shared_checkouts(Path("wac-ci.yml"), doc))

    def test_null_nested_job_is_a_problem_not_a_crash(self):
        self.assertTrue(runner_problems({"lint": None}))

    def test_repository_workflows_pass(self):
        self.assertEqual(module.main(), 0)


if __name__ == "__main__":
    unittest.main()
