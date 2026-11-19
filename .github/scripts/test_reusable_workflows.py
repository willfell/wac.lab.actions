import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("check_actions", Path(__file__).with_name("check_actions.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

GUARD_RUN = '[[ "$SHARED_WORKFLOW_SHA" =~ ^[0-9a-f]{40}$ ]]'

# A GitHub OIDC token does not say which step minted it, so any code that runs
# in a job holding id-token: write can mint a gateway token. Such a job may use
# only these actions, pinned by commit (the tag goes in the step's name), and
# may not run a fetched installer. install-helm.sh is allowed: it checks a
# committed digest before it runs anything.
PINNED_ACTIONS = {
    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262": "v4",
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02": "v4",
    "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093": "v4",
}
FETCHED_INSTALLERS = (
    ("install-tools.sh", re.compile(r"install-tools\.sh")),
    ("pip", re.compile(r"\bpip3?\b")),
    ("npm", re.compile(r"\bnpm\b")),
    ("npx", re.compile(r"\bnpx\b")),
    ("uvx", re.compile(r"\buvx\b")),
    ("curl piped to a shell", re.compile(r"\bcurl\b[^\n]*\|\s*(sudo\s+)?(ba|z|da)?sh\b")),
    ("get-helm", re.compile(r"get-helm")),
)
# Known exceptions, owned by the coordinator and raised with the owner: these
# jobs held id-token: write with tag-pinned actions and setup-python before the
# rule existed. This list may only shrink; a job fixed here must leave it.
KNOWN_ID_TOKEN_EXPOSURES_TO_REMOVE = (("wac-cd.yml", "deploy"), ("wac-cd.yml", "shadow"))
SHA = "${{ job.workflow_sha }}"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def holds_id_token(doc, job):
    permissions = job.get("permissions") if "permissions" in (job or {}) else doc.get("permissions")
    if permissions == "write-all":
        return True
    return isinstance(permissions, dict) and permissions.get("id-token") == "write"


def id_token_problems(doc, job_name, job):
    """What lets code other than wac-cd's own run in a job that can mint a gateway token."""
    problems = []
    if "uses" in job:
        problems.append(f"{job_name}: calls {job['uses']} while holding id-token: write")
    for index, step in enumerate(job.get("steps") or []):
        where = f"{job_name} steps[{index}]"
        uses = step.get("uses")
        if uses is not None:
            tag = PINNED_ACTIONS.get(uses)
            if tag is None:
                problems.append(f"{where}: {uses} is not a commit-pinned action on the allowlist")
            elif f"{uses.split('@')[0]} {tag}" not in str(step.get("name", "")):
                problems.append(f"{where}: the step name does not say {uses.split('@')[0]} {tag}")
        for label, pattern in FETCHED_INSTALLERS:
            if pattern.search(str(step.get("run", ""))):
                problems.append(f"{where}: runs {label}")
    return problems


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

    def test_build_job_may_take_the_build_runner_when_declared(self):
        declared = workflow({"checks": {"runs-on": module.BUILD_RUNS_ON_EXPRESSION, "steps": []}})
        declared["on"]["workflow_call"]["inputs"]["build_runner"] = {"type": "string", "default": ""}
        self.assertEqual(module._check_runner_is_a_caller_choice(Path("wac-ci.yml"), declared), [])

    def test_build_runner_expression_needs_the_input_with_an_empty_default(self):
        undeclared = workflow({"checks": {"runs-on": module.BUILD_RUNS_ON_EXPRESSION, "steps": []}})
        self.assertTrue(module._check_runner_is_a_caller_choice(Path("wac-ci.yml"), undeclared))
        defaulted = workflow({"checks": {"runs-on": module.BUILD_RUNS_ON_EXPRESSION, "steps": []}})
        defaulted["on"]["workflow_call"]["inputs"]["build_runner"] = {"type": "string", "default": "ci-standard"}
        self.assertTrue(module._check_runner_is_a_caller_choice(Path("wac-ci.yml"), defaulted))

    def test_build_runner_without_the_runner_fallback_fails(self):
        for runs_on in ("${{ inputs.build_runner || vars.CI_RUNNER_SMALL }}", "${{ inputs.build_runner }}"):
            with self.subTest(runs_on=runs_on):
                doc = workflow({"checks": {"runs-on": runs_on, "steps": []}})
                doc["on"]["workflow_call"]["inputs"]["build_runner"] = {"type": "string", "default": ""}
                self.assertTrue(module._check_runner_is_a_caller_choice(Path("wac-ci.yml"), doc))

    def test_wac_ci_and_wac_cd_route_only_their_build_job_to_the_build_runner(self):
        import yaml

        root = Path(__file__).parents[1] / "workflows"
        for name, heavy in (("wac-ci.yml", "checks"), ("wac-cd.yml", "build")):
            with self.subTest(workflow=name):
                doc = yaml.safe_load((root / name).read_text())
                for job_name, job in doc["jobs"].items():
                    expected = module.BUILD_RUNS_ON_EXPRESSION if job_name == heavy else module.RUNS_ON_EXPRESSION
                    if "uses" in job:
                        self.assertEqual(job["with"]["runner"], module.RUNNER_PASSTHROUGH, job_name)
                        continue
                    self.assertEqual(job["runs-on"], expected, job_name)

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

    def test_wac_cd_plan_claims_the_tag_before_build_and_steps_aside_when_superseded(self):
        import os
        import subprocess
        import tempfile
        import yaml

        doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "workflows" / "wac-cd.yml").read_text())
        plan = doc["jobs"]["plan"]
        self.assertEqual(plan["permissions"], {"contents": "write", "id-token": "write"})
        app = next(s for s in plan["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")
                   and "repository" not in (s.get("with") or {}))
        self.assertNotIn("persist-credentials", app.get("with") or {}, "plan pushes the release tag")
        step = next(s for s in plan["steps"] if s.get("name") == "Plan the version")
        stubs = {
            "wacd-version.py": "import os\nprint('mode=' + os.environ['STUB_MODE'])\nprint('version=0.2.0')\nprint('tag=v0.2.0')\n",
            "wacd-chart.py": "print('state=present')\n",
            "wacd-tag.py": (
                "import os, sys\n"
                "open(os.environ['STUB_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                "claim = os.environ['STUB_CLAIM']\n"
                "if claim == 'fail':\n"
                "    sys.exit(print('wacd tag: git push failed: refused', file=sys.stderr) or 1)\n"
                "print('Tag v0.2.0 line')\n"
                "print('claim=' + claim)\n"
            ),
        }
        sha = "a" * 40
        for mode, claim, code, expected_mode, claimed in (
            ("release", "pushed", 0, "release", True),
            ("publish", "exists", 0, "publish", True),
            ("release", "superseded", 0, "none", True),
            ("release", "fail", 1, None, True),
            ("resume", "pushed", 0, "resume", False),
        ):
            with self.subTest(mode=mode, claim=claim), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for name, body in stubs.items():
                    (root / name).write_text(body)
                out, summary, log = root / "out", root / "summary", root / "log"
                log.write_text("")
                env = {**os.environ, "WACD_SCRIPTS": str(root), "RUNNER_TEMP": str(root),
                       "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_SHA": sha,
                       "ROLLBACK_VERSION": "", "VERSION_SOURCE": "commits", "CALLER_VERSION": "",
                       "DEPLOY": "true", "APP": "app", "TAG_PREFIX": "v",
                       "STUB_MODE": mode, "STUB_CLAIM": claim, "STUB_LOG": str(log)}
                result = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                calls = log.read_text().splitlines()
                self.assertEqual(calls, [f"claim-tag --tag v0.2.0 --sha {sha} --branch main"] if claimed else [])
                if code:
                    self.assertIn("::error::wac-cd: wacd tag: git push failed", result.stdout)
                    continue
                outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
                self.assertEqual(outputs["mode"], expected_mode)
                if claim == "superseded" and claimed:
                    self.assertEqual((outputs["version"], outputs["tag"]), ("", ""))
                    self.assertIn("newer run releases these commits cumulatively", summary.read_text())
                else:
                    self.assertEqual((outputs["version"], outputs["tag"]), ("0.2.0", "v0.2.0"))
                    self.assertNotIn("Superseded", summary.read_text())

    def test_wac_cd_shadow_observes_and_never_holds_a_release(self):
        """A hemi outage must not fail or delay a release: the shadow job is off the release path."""
        import yaml

        root = Path(__file__).resolve().parents[2]
        doc = yaml.safe_load((root / ".github" / "workflows" / "wac-cd.yml").read_text())
        jobs = doc["jobs"]
        job = jobs["shadow"]
        self.assertEqual(job["permissions"], {"contents": "read", "id-token": "write"})
        self.assertIs(job["continue-on-error"], True)
        self.assertEqual(job["timeout-minutes"], 2, "the next queued release waits for the run to end")
        self.assertEqual(job["needs"], "plan")
        self.assertEqual(
            job["if"],
            "needs.plan.outputs.mode != 'none' && needs.plan.outputs.source == 'files'",
            "a copy hemi releases from is the truth; there are no files to compare it with",
        )
        for name, other in jobs.items():
            needs = other.get("needs") or []
            with self.subTest(job=name):
                self.assertNotIn("shadow", [needs] if isinstance(needs, str) else needs)
        app = job["steps"][1]
        self.assertTrue(app["uses"].startswith("actions/checkout@"))
        self.assertNotIn("repository", app.get("with") or {})
        self.assertIs((app.get("with") or {}).get("persist-credentials"), False)
        runs = [s["run"] for s in job["steps"] if "wacd-shadow.py" in s.get("run", "")]
        self.assertEqual(runs, ['python3 "$WACD_SCRIPTS"/wacd-shadow.py .github/wacd.json'])
        self.assertTrue((root / "scripts" / "wacd-shadow.py").is_file())
        steps = job["steps"]
        shadow_install = 'bash "$WACD_SCRIPTS"/install-helm.sh --max-time 25 --attempts 1'
        publish_install = 'bash "$WACD_SCRIPTS"/install-helm.sh --max-time 120 --attempts 3'
        installs = [i for i, st in enumerate(steps) if st.get("run") == shadow_install]
        compare = [i for i, st in enumerate(steps) if "wacd-shadow.py" in st.get("run", "")]
        self.assertEqual(len(installs), 1)
        install = steps[installs[0]]
        self.assertLess(installs[0], compare[0], "helm is on PATH before the comparison reads it")
        self.assertNotIn("env", install, "the version and digests live only in install-helm.sh")
        self.assertEqual(
            [st for st in jobs["publish"]["steps"] if "install-helm.sh" in st.get("run", "")],
            [{"name": "Install helm", "shell": "bash", "run": publish_install}],
            "publish keeps a generous download bound, retries, and no tighter step timeout than before",
        )
        for name, other in jobs.items():
            for st in other["steps"]:
                if "install-tools.sh" in st.get("run", ""):
                    with self.subTest(job=name, step=st.get("name")):
                        self.assertNotIn("helm", st["run"], "wac-cd installs Helm only with install-helm.sh")
        shadow_py = load_module("wacd_shadow_for_workflow_test", root / "scripts" / "wacd-shadow.py")
        installer = (root / "scripts" / "install-helm.sh").read_text()
        self.assertEqual(re.findall(r'^HELM_VERSION="(.+)"$', installer, re.M), ["v3.16.3"])
        self.assertEqual(shadow_py.HELM_VERSION, "v3.16.3", "the script accepts exactly the version installed")
        self.assertEqual(install.get("timeout-minutes"), 1, "the smallest bound GitHub accepts")
        self.assertIs(install.get("continue-on-error"), True, "a failed download must not skip the comparison")
        stamp = [i for i, st in enumerate(steps) if shadow_py.STAMP_ENV in st.get("run", "")]
        self.assertEqual(stamp, [0], "the clock starts in the first step, before any checkout can retry")
        self.assertEqual(
            steps[0]["run"], f'echo "{shadow_py.STAMP_ENV}=$(date +%s)" >> "$GITHUB_ENV"',
            "the first step only records the time",
        )
        self.assertEqual(
            shadow_py.BEFORE_STAMP + shadow_py.DEADLINE_SECONDS + shadow_py.SUMMARY_RESERVE,
            job["timeout-minutes"] * 60,
            "job set-up before the stamp, the deadline and the reserve for writing the summary add up to the job's bound "
            "(budget in docs/reference/wac-cd.md)",
        )
        self.assertGreaterEqual(shadow_py.SUMMARY_RESERVE, 15)
        self.assertEqual(
            sorted((root / "scripts" / "fixtures" / "values-probe").rglob("*")),
            [
                root / "scripts" / "fixtures" / "values-probe" / "Chart.yaml",
                root / "scripts" / "fixtures" / "values-probe" / "templates",
                root / "scripts" / "fixtures" / "values-probe" / "templates" / "values.json",
            ],
            "the probe chart ships no values.yaml: chart defaults would remove a user's explicit null",
        )

    def wac_cd(self):
        import yaml

        return yaml.safe_load((Path(__file__).resolve().parents[1] / "workflows" / "wac-cd.yml").read_text())

    def test_wac_cd_plan_asks_the_gateway_once_before_reading_the_profile(self):
        import os
        import subprocess

        plan = self.wac_cd()["jobs"]["plan"]
        steps = plan["steps"]
        names = [s.get("name") or s.get("uses") for s in steps]
        asks = [s for s in steps if "wacd-contract.py" in s.get("run", "")]
        self.assertEqual(len(asks), 1, "plan asks the gateway exactly once")
        ask = asks[0]
        self.assertEqual(ask["id"], "contract")
        self.assertIn('fetch --out .wacd-contract --wait-timeout "$WAIT_TIMEOUT"', ask["run"])
        self.assertEqual(ask["env"], {"WAIT_TIMEOUT": "${{ inputs.wait_timeout }}"})
        self.assertLess(names.index(ask["name"]), names.index("Read the wacd profile"))
        upload = next(s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
        self.assertEqual(upload["if"], "steps.contract.outputs.revision != ''")
        self.assertEqual(upload["with"]["name"], "wac-cd-contract")
        self.assertEqual(upload["with"]["path"], ".wacd-contract/")
        self.assertIs(upload["with"]["include-hidden-files"], True, "upload-artifact skips dot directories")
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertEqual(upload["with"]["retention-days"], 1, "settings and Secret names outlive the run otherwise")
        self.assertLess(names.index(ask["name"]), names.index(upload["name"]))
        for key in ("source", "revision", "fleet_commit"):
            self.assertEqual(plan["outputs"][key], f"${{{{ steps.contract.outputs.{key} }}}}")
        profile = next(s for s in steps if s.get("name") == "Read the wacd profile")
        self.assertEqual(profile["env"], {"SOURCE": "${{ steps.contract.outputs.source }}"})
        for source, expected in (
            ("hemi", "--contract .wacd-contract/contract.json"),
            ("files", ".github/wacd.json"),
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "wacd-profile.py").write_text(
                    "import sys\nprint('argv=' + ' '.join(sys.argv[1:]))\n"
                )
                out = root / "out"
                env = {**os.environ, "WACD_SCRIPTS": str(root), "GITHUB_OUTPUT": str(out), "SOURCE": source}
                result = subprocess.run(["bash", "-e", "-c", profile["run"]], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(out.read_text(), f"argv={expected}\n")

    def test_jobs_holding_id_token_run_only_pinned_actions_and_no_fetched_installer(self):
        import yaml

        root = Path(__file__).resolve().parents[1] / "workflows"
        checked = []
        for name in ("wac-cd.yml", "wac-ci.yml"):
            doc = yaml.safe_load((root / name).read_text())
            for job_name, job in doc["jobs"].items():
                if not holds_id_token(doc, job) or (name, job_name) in KNOWN_ID_TOKEN_EXPOSURES_TO_REMOVE:
                    continue
                checked.append((name, job_name))
                with self.subTest(workflow=name, job=job_name):
                    self.assertEqual(id_token_problems(doc, job_name, job), [])
        self.assertIn(("wac-cd.yml", "plan"), checked)

    def test_the_known_id_token_exposures_only_shrink(self):
        import yaml

        self.assertLessEqual(
            set(KNOWN_ID_TOKEN_EXPOSURES_TO_REMOVE), {("wac-cd.yml", "deploy"), ("wac-cd.yml", "shadow")},
            "a job holding id-token: write may never be added as an exception",
        )
        root = Path(__file__).resolve().parents[1] / "workflows"
        for name, job_name in KNOWN_ID_TOKEN_EXPOSURES_TO_REMOVE:
            doc = yaml.safe_load((root / name).read_text())
            job = doc["jobs"][job_name]
            with self.subTest(workflow=name, job=job_name):
                self.assertTrue(holds_id_token(doc, job), "no longer holds id-token: remove it from the list")
                self.assertTrue(id_token_problems(doc, job_name, job), "now passes the rule: remove it from the list")

    def test_the_id_token_rule_names_each_way_in(self):
        pinned = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        good = {"name": "Check out (actions/checkout v4)", "uses": pinned}
        cases = (
            ("tag-pinned action", {"name": "x (actions/checkout v4)", "uses": "actions/checkout@v4"}, "allowlist"),
            ("setup-python", {"name": "x", "uses": "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"},
             "allowlist"),
            ("unnamed pin", {"uses": pinned}, "step name"),
            ("install-tools", {"run": 'bash "$WACD_SCRIPTS"/install-tools.sh helm'}, "install-tools.sh"),
            ("pip", {"run": "pip install requests"}, "pip"),
            ("pip3", {"run": "python3 -m pip3 install x"}, "pip"),
            ("npm", {"run": "npm ci"}, "npm"),
            ("npx", {"run": "npx something"}, "npx"),
            ("uvx", {"run": "uvx mkdocs build"}, "uvx"),
            ("curl to sh", {"run": "curl -fsSL https://example.invalid/i | sh"}, "curl"),
            ("curl to sudo bash", {"run": "curl -fsSL https://example.invalid/i | sudo bash -s"}, "curl"),
            ("get-helm", {"run": "./get-helm-3"}, "get-helm"),
        )
        for label, step, expected in cases:
            with self.subTest(case=label):
                problems = id_token_problems({}, "job", {"steps": [good, step]})
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(expected, problems[0])
        allowed = [good, {"run": 'set -euo pipefail\nbash "$WACD_SCRIPTS"/install-helm.sh'},
                   {"run": "curl -fsS -o out https://example.invalid/x"}]
        self.assertEqual(id_token_problems({}, "job", {"steps": allowed}), [])
        self.assertTrue(id_token_problems({}, "job", {"uses": "./.github/workflows/actionlint.yml"}))
        inherited = {"permissions": {"id-token": "write"}}
        self.assertTrue(holds_id_token(inherited, {"steps": []}))
        self.assertFalse(holds_id_token(inherited, {"permissions": {"contents": "read"}}))
        self.assertTrue(holds_id_token({}, {"permissions": "write-all"}))

    def test_wac_cd_plan_first_requires_the_runners_python3_and_names_the_runner(self):
        import os
        import subprocess

        plan = self.wac_cd()["jobs"]["plan"]
        self.assertFalse([s for s in plan["steps"] if "setup-python" in str(s.get("uses", ""))],
                         "setup-python fetches and runs a Python build in a job that can mint a gateway token")
        step = plan["steps"][0]
        self.assertEqual(step["env"], {"JOB_RUNNER_NAME": "${{ runner.name }}",
                                       "JOB_RUNNER_OS": "${{ runner.os }}", "JOB_RUNNER_ARCH": "${{ runner.arch }}"})
        fake = (
            "#!/bin/bash\n"
            'case "$2" in\n'
            '  *sys.exit*) exit "$STUB_OLD" ;;\n'
            '  *print*) echo "Python $STUB_VERSION" ;;\n'
            "esac\n"
        )
        for label, old, version, code in (("3.12", "0", "3.12.3", 0), ("3.10", "1", "3.10.12", 1), ("absent", None, None, 1)):
            with self.subTest(python=label), tempfile.TemporaryDirectory() as tmp:
                bin_dir = Path(tmp)
                if old is not None:
                    (bin_dir / "python3").write_text(fake)
                    (bin_dir / "python3").chmod(0o755)
                env = {"PATH": str(bin_dir), "STUB_OLD": old or "", "STUB_VERSION": version or "",
                       "JOB_RUNNER_NAME": "ci-light-abc12", "JOB_RUNNER_OS": "Linux", "JOB_RUNNER_ARCH": "ARM64"}
                result = subprocess.run(["/bin/bash", "-c", step["run"]], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                if label == "3.10":
                    self.assertEqual(
                        result.stdout,
                        "::error::wac-cd: plan needs python3 3.12 or later on the runner; found Python 3.10.12 "
                        f"at {bin_dir}/python3 on ci-light-abc12 (Linux, ARM64).\n",
                    )
                if label == "absent":
                    self.assertIn("found no python3 on ci-light-abc12 (Linux, ARM64).", result.stdout)

    def test_wac_cd_plan_fails_with_the_refusal_sentence(self):
        import os
        import subprocess

        ask = next(s for s in self.wac_cd()["jobs"]["plan"]["steps"] if "wacd-contract.py" in s.get("run", ""))
        for code, stdout, stderr in ((0, "source=files\nrevision=\n", ""), (1, "", "wacd contract: refused here\n")):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "wacd-contract.py").write_text(
                    "import sys\n"
                    f"sys.stdout.write({stdout!r})\n"
                    f"sys.stderr.write({stderr!r})\n"
                    f"sys.exit({code})\n"
                )
                out = root / "out"
                out.write_text("")
                env = {**os.environ, "WACD_SCRIPTS": str(root), "GITHUB_OUTPUT": str(out), "RUNNER_TEMP": tmp,
                       "WAIT_TIMEOUT": "2100"}
                result = subprocess.run(["bash", "-c", ask["run"]], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertEqual(out.read_text(), stdout)
                if code:
                    self.assertIn("::error::wac-cd: wacd contract: refused here", result.stdout)

    def test_wac_cd_publish_renders_from_the_copy_plan_read(self):
        """A save made live during a build must not change what the release ships."""
        import os
        import subprocess

        doc = self.wac_cd()
        jobs = doc["jobs"]
        publish = jobs["publish"]
        self.assertNotIn("id-token", publish["permissions"], "publish can ask neither the gateway nor hemi")
        for name, job in jobs.items():
            text = json.dumps(job)
            with self.subTest(job=name):
                self.assertEqual("wacd-contract.py" in text, name == "plan")
        steps = publish["steps"]
        names = [s.get("name") or s.get("uses") for s in steps]
        clear = names.index("Clear any copy in the checkout")
        self.assertEqual(steps[clear]["run"], "rm -rf .wacd-contract")
        download = next(i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/download-artifact@"))
        self.assertEqual(steps[download]["if"], "needs.plan.outputs.source == 'hemi'")
        self.assertEqual(steps[download]["with"], {"name": "wac-cd-contract", "path": ".wacd-contract"})
        upload = next(s for s in jobs["plan"]["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
        self.assertEqual(steps[download]["with"]["name"], upload["with"]["name"])
        step_index = names.index("Publish the deploy chart and the tag")
        self.assertLess(clear, download)
        self.assertLess(download, step_index)
        step = steps[step_index]
        self.assertEqual(step["env"]["SOURCE"], "${{ needs.plan.outputs.source }}")
        self.assertEqual(step["env"]["REVISION"], "${{ needs.plan.outputs.revision }}")
        self.assertEqual(step["env"]["FLEET_COMMIT"], "${{ needs.plan.outputs.fleet_commit }}")
        sha, commit = "a" * 40, "c" * 40
        annotations = f"--annotation wacwini.com/revision=7 --annotation wacwini.com/fleet-commit={commit}"
        for source, revision, profile, extra in (
            ("hemi", "7", ".wacd-contract/profile.json", f" --values-json .wacd-contract/values.json {annotations}"),
            ("files", "7", ".github/wacd.json", f" {annotations}"),
            ("files", "", ".github/wacd.json", ""),
        ):
            with self.subTest(source=source, revision=revision), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                log = root / "log"
                (root / "wacd-chart.py").write_text(
                    "import os, sys\n"
                    "args = sys.argv[1:]\n"
                    "if args[0] == 'chart-state':\n"
                    "    seen = os.path.join(os.environ['RUNNER_TEMP'], 'seen')\n"
                    "    print('state=present' if os.path.exists(seen) else 'state=absent')\n"
                    "    open(seen, 'w').close()\n"
                    "else:\n"
                    "    open(os.environ['STUB_LOG'], 'a').write(' '.join(args) + '\\n')\n"
                    "    os.makedirs(os.path.join(os.environ['RUNNER_TEMP'], 'packaged'), exist_ok=True)\n"
                    "    open(os.path.join(os.environ['RUNNER_TEMP'], 'packaged', 'app-0.2.0.tgz'), 'w').close()\n"
                    "    print('chart_dir=' + os.environ['RUNNER_TEMP'])\n"
                )
                (root / "wacd-tag.py").write_text("print('tag pushed')\n")
                bin_dir = root / "bin"
                bin_dir.mkdir()
                (bin_dir / "helm").write_text("#!/bin/sh\nexit 0\n")
                (bin_dir / "helm").chmod(0o755)
                env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WACD_SCRIPTS": str(root),
                       "RUNNER_TEMP": str(root), "STUB_LOG": str(log), "GITHUB_SHA": sha,
                       "APP": "app", "VERSION": "0.2.0", "TAG": "v0.2.0", "IMAGES": "{}", "WAC_APP_TGZ": "w.tgz",
                       "SOURCE": source, "REVISION": revision, "FLEET_COMMIT": commit if revision else ""}
                result = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(
                    log.read_text(),
                    f"package --profile {profile} --version 0.2.0 --source-sha {sha} --images {{}} "
                    f"--wac-app-tgz w.tgz --out {root}/deploy-chart{extra}\n",
                )

    def test_wac_cd_build_and_deploy_take_their_settings_only_from_plan(self):
        jobs = self.wac_cd()["jobs"]
        for name in ("build", "deploy", "github-release"):
            text = json.dumps(jobs[name])
            with self.subTest(job=name):
                self.assertNotIn(".github/wacd.json", text)
                self.assertNotIn("values_file", text)
                self.assertNotIn(".wacd-contract", text)

    def test_wac_cd_only_shadow_may_read_the_repositorys_values_path(self):
        """In hemi mode values_file names a repository path; reading it would fall back to the files."""
        jobs = self.wac_cd()["jobs"]
        readers = [name for name, job in jobs.items() if name != "plan" and "values_file" in json.dumps(job)]
        self.assertLessEqual(set(readers), {"shadow"})
        publish = json.dumps(jobs["publish"])
        self.assertNotIn("values_file", publish)
        self.assertNotIn("deploy/values", publish)
        package = next(s for s in jobs["publish"]["steps"] if s.get("name") == "Publish the deploy chart and the tag")
        self.assertIn("--values-json .wacd-contract/values.json", package["run"])

    def test_wac_cd_plan_has_time_to_wait_out_the_gateway_for_wait_timeout(self):
        import math

        doc = self.wac_cd()
        trigger = doc.get("on", doc.get(True))
        default = int(trigger["workflow_call"]["inputs"]["wait_timeout"]["default"])
        plan = doc["jobs"]["plan"]
        self.assertIsInstance(plan["timeout-minutes"], int, "GitHub expressions have no arithmetic to size it from the input")
        self.assertGreaterEqual(plan["timeout-minutes"], math.ceil(default / 60) + 5)
        spec = importlib.util.spec_from_file_location(
            "wacd_contract_cap", Path(__file__).resolve().parents[2] / "scripts" / "wacd-contract.py"
        )
        contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contract)
        self.assertEqual(contract.PLAN_WAIT_CAP, default, "plan waits the default and no longer")
        self.assertGreaterEqual(plan["timeout-minutes"], math.ceil(contract.PLAN_WAIT_CAP / 60) + 5,
                                "a capped wait still ends inside the job, with its summary line")

    def test_wac_cd_plan_bounds_every_step_but_the_gateway_wait(self):
        """With 40 minutes for the job, a stalled checkout or git push must not use them all."""
        steps = self.wac_cd()["jobs"]["plan"]["steps"]
        for step in steps:
            name = step.get("name") or step.get("uses")
            with self.subTest(step=name):
                if step.get("id") == "contract":
                    self.assertNotIn("timeout-minutes", step, "the wait is bounded by --wait-timeout")
                    continue
                self.assertIsInstance(step.get("timeout-minutes"), int)
                self.assertLessEqual(step["timeout-minutes"], 6)

    def test_wac_cd_plan_checks_the_trigger_before_asking_the_gateway(self):
        """A malformed dispatch must not wait out a gateway outage before saying so."""
        steps = self.wac_cd()["jobs"]["plan"]["steps"]
        names = [s.get("name") or s.get("uses") for s in steps]
        contract_index = next(i for i, s in enumerate(steps) if s.get("id") == "contract")
        self.assertLess(names.index("Check the trigger"), contract_index)

    def test_wac_ci_setup_go_takes_the_version_or_the_version_file_from_the_profile(self):
        import yaml

        doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "workflows" / "wac-ci.yml").read_text())
        steps = [s for s in doc["jobs"]["checks"]["steps"] if str(s.get("uses", "")).startswith("actions/setup-go@")]
        self.assertEqual(len(steps), 1)
        self.assertEqual(
            steps[0]["with"],
            {
                "go-version": "${{ fromJSON(steps.profile.outputs.ci).go_version }}",
                "go-version-file": "${{ fromJSON(steps.profile.outputs.ci).go_version_file }}",
            },
        )

    def test_repository_workflows_pass(self):
        self.assertEqual(module.main(), 0)


if __name__ == "__main__":
    unittest.main()
