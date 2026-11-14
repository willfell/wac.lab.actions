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

    def test_wacd_shared_checkout_leaves_the_app_workspace(self):
        """The shared checkout must not stay where the app's commands and docker context see it."""
        import yaml

        workflows = Path(__file__).resolve().parents[1] / "workflows"
        for name in ("wac-ci.yml", "wac-cd.yml"):
            doc = yaml.safe_load((workflows / name).read_text())
            for job_name, job in doc["jobs"].items():
                steps = (job or {}).get("steps") or []
                shared = [
                    index for index, step in enumerate(steps)
                    if str((step.get("with") or {}).get("repository") or "").lower()
                    == module.SHARED_REPOSITORY
                ]
                for index in shared:
                    with self.subTest(workflow=name, job=job_name):
                        self.assertEqual((steps[index]["with"]).get("path"), ".wacd")
                        self.assertLess(index + 1, len(steps))
                        self.assertIn('mv .wacd "$RUNNER_TEMP/wacd"', steps[index + 1].get("run", ""))
                for index, step in enumerate(steps):
                    if index - 1 in shared:
                        continue
                    text = str(step.get("run", "")) + str(step.get("with", "")) + str(step.get("env", ""))
                    with self.subTest(workflow=name, job=job_name, step=index):
                        self.assertNotIn(".wacd/", text)

    def test_wac_ci_app_checkouts_do_not_persist_credentials(self):
        import yaml

        doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "workflows" / "wac-ci.yml").read_text())
        for job_name in ("checks", "render"):
            app = doc["jobs"][job_name]["steps"][0]
            with self.subTest(job=job_name):
                self.assertTrue(app["uses"].startswith("actions/checkout@"))
                self.assertNotIn("repository", app.get("with") or {})
                self.assertIs((app.get("with") or {}).get("persist-credentials"), False)

    def test_wac_ci_render_fails_when_the_chart_renders_nothing(self):
        import os
        import subprocess
        import yaml

        doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "workflows" / "wac-ci.yml").read_text())
        step = next(s for s in doc["jobs"]["render"]["steps"] if s.get("name") == "Render the deploy chart")
        for label, output, expected in (
            ("empty", "", 1),
            ("only separators", "---\n# Source: x\n---\n", 1),
            ("one object", "---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n", 0),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                bin_dir = Path(tmp) / "bin"
                bin_dir.mkdir()
                helm = bin_dir / "helm"
                helm.write_text("#!/bin/sh\nprintf '%s' \"$FAKE_RENDER\"\n")
                helm.chmod(0o755)
                env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNNER_TEMP": tmp,
                       "APP": "travel", "CHART_DIR": tmp, "FAKE_RENDER": output}
                result = subprocess.run(["bash", "-e", "-c", step["run"]], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                if expected:
                    self.assertIn("::error::", result.stdout)

    def test_wac_ci_apt_packages_come_from_the_validator_and_precede_the_installs(self):
        import yaml

        doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "workflows" / "wac-ci.yml").read_text())
        steps = doc["jobs"]["checks"]["steps"]
        names = [s.get("name") or s.get("uses") for s in steps]
        apt = names.index("Install the profile's apt packages")
        step = steps[apt]
        self.assertEqual(step["if"], "steps.profile.outputs.apt_packages != ''")
        self.assertEqual(step["env"], {"APT_PACKAGES": "${{ steps.profile.outputs.apt_packages }}"})
        self.assertNotIn("${{", step["run"])
        self.assertLess(names.index("Read the wacd profile"), apt)
        for later in ("actions/setup-node@v4", "Install root node dependencies", "Run the profile's CI commands"):
            with self.subTest(step=later):
                self.assertLess(apt, names.index(later))

    def test_wac_ci_apt_step_passes_each_package_as_one_word(self):
        import os
        import subprocess
        import yaml

        doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "workflows" / "wac-ci.yml").read_text())
        step = next(s for s in doc["jobs"]["checks"]["steps"] if s.get("name") == "Install the profile's apt packages")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            log = Path(tmp) / "sudo.log"
            sudo = bin_dir / "sudo"
            sudo.write_text('#!/usr/bin/env bash\nprintf "%s|" "$@" >> "$SUDO_LOG"\necho >> "$SUDO_LOG"\n')
            sudo.chmod(0o755)
            env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "SUDO_LOG": str(log),
                   "APT_PACKAGES": "python3 make g++"}
            result = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                log.read_text().splitlines(),
                [
                    "apt-get|update|",
                    "env|DEBIAN_FRONTEND=noninteractive|apt-get|install|-y|--no-install-recommends|python3|make|g++|",
                ],
            )

    def test_wac_cd_rollback_version_must_be_strict_semver(self):
        import os
        import subprocess
        import yaml

        doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "workflows" / "wac-cd.yml").read_text())
        step = next(s for s in doc["jobs"]["plan"]["steps"] if s.get("name") == "Check the trigger")
        for value, expected in (
            ("1.2.3", 0), ("0.0.0", 0), ("10.20.30", 0),
            ("01.2.3", 1), ("1.02.3", 1), ("1.2.03", 1), ("1.2", 1), ("v1.2.3", 1), ("1.2.3-rc.1", 1), ("", 1),
        ):
            with self.subTest(value=value):
                env = {**os.environ, "GITHUB_REF": "refs/heads/main", "EVENT_NAME": "workflow_dispatch",
                       "ROLLBACK_VERSION": value, "DEPLOY": "true"}
                result = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_repository_workflows_pass(self):
        self.assertEqual(module.main(), 0)


if __name__ == "__main__":
    unittest.main()
