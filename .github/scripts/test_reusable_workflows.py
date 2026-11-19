import ast
import importlib.util
import json
import platform
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("check_actions", Path(__file__).with_name("check_actions.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

GUARD_RUN = '[[ "$SHARED_WORKFLOW_SHA" =~ ^[0-9a-f]{40}$ ]]'

# Every action a reusable workflow here uses, pinned by commit with the release tag the
# commit was resolved from; the step that uses one says "<action> <tag>" in its
# name. A tag can be moved, so a job holding a write token (or able to mint one)
# would otherwise run whatever the tag points at today. One commit per action,
# so every job of a run, upload and download alike, runs the same release.
PINNED_ACTIONS = {
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1": "v7.0.1",
    "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9": "v7.0.2",
    "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333": "v8.0.2",
    "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0": "v7.1.0",
    "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1": "v7.1.0",
    "actions/setup-go@d8cbb8045d7f43706b9177cceb36c1f91a6644d5": "v7.1.0",
    "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2": "v10.3.3",
    "raven-actions/actionlint@3d39aea434753780c3b3d4a1a31c854b4dbf49d7": "v2.2.0",
    "aws-actions/configure-aws-credentials@e1253824e5c10ff9df46874f81ed3ec929e19cfd": "v6.3.0",
}
# A GitHub OIDC token does not say which step minted it, so any code that runs
# in a job holding id-token: write can mint a gateway token. Such a job may use
# only these of the pinned actions, and may not run a fetched installer.
# install-helm.sh is allowed: it checks a committed digest before it runs
# anything.
CALLED_WORKFLOW = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml@[0-9a-f]{40}")
ID_TOKEN_ACTIONS = ("actions/checkout", "actions/upload-artifact", "actions/download-artifact")
FETCHED_INSTALLERS = (
    ("install-tools.sh", re.compile(r"install-tools\.sh")),
    ("pip", re.compile(r"\bpip3?\b")),
    ("npm", re.compile(r"\bnpm\b")),
    ("npx", re.compile(r"\bnpx\b")),
    ("uvx", re.compile(r"\buvx\b")),
    ("curl piped to a shell", re.compile(r"\bcurl\b[^\n]*\|\s*(sudo\s+)?(ba|z|da)?sh\b")),
    ("get-helm", re.compile(r"get-helm")),
)
KNOWN_ID_TOKEN_EXPOSURES_TO_REMOVE = ()
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


def holds_write(doc, job):
    """Whether a job holds any write permission, its own or else its workflow's."""
    job = job or {}
    permissions = job["permissions"] if "permissions" in job else doc.get("permissions")
    if permissions == "write-all":
        return True
    return isinstance(permissions, dict) and "write" in permissions.values()


def pinned_action_problems(job_name, job, allowed=None):
    """Each step's action must be on the allowlist (and in `allowed`, when given) and named with its tag.

    A job that calls a reusable workflow must name a local file, itself held to
    this rule, or another repository's workflow at a full commit SHA.
    """
    problems = []
    called = job.get("uses")
    if called is not None and not (str(called).startswith("./.github/workflows/")
                                   or CALLED_WORKFLOW.fullmatch(str(called))):
        problems.append(f"{job_name}: calls {called}, which is neither a local workflow nor pinned by commit")
    for index, step in enumerate(job.get("steps") or []):
        uses = step.get("uses")
        if uses is None:
            continue
        where = f"{job_name} steps[{index}]"
        action = uses.split("@")[0]
        tag = PINNED_ACTIONS.get(uses)
        if tag is None or (allowed is not None and action not in allowed):
            problems.append(f"{where}: {uses} is not a commit-pinned action on the allowlist")
        elif f"{action} {tag}" not in str(step.get("name", "")):
            problems.append(f"{where}: the step name does not say {action} {tag}")
    return problems


def id_token_problems(doc, job_name, job):
    """What lets code other than wac-cd's own run in a job that can mint a gateway token."""
    problems = []
    if "uses" in job:
        problems.append(f"{job_name}: calls {job['uses']} while holding id-token: write")
    problems.extend(pinned_action_problems(job_name, job, ID_TOKEN_ACTIONS))
    for index, step in enumerate(job.get("steps") or []):
        for label, pattern in FETCHED_INSTALLERS:
            if pattern.search(str(step.get("run", ""))):
                problems.append(f"{job_name} steps[{index}]: runs {label}")
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
            app = doc["jobs"][job_name]["steps"][1]
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
        setup_node = next(s["name"] for s in steps if str(s.get("uses", "")).startswith("actions/setup-node@"))
        for later in (setup_node, "Install root node dependencies", "Run the profile's CI commands"):
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

    def test_wac_cd_plan_refuses_to_replay_an_old_version_but_not_a_rollback_or_a_new_release(self):
        import os
        import shutil
        import subprocess
        import tempfile
        import yaml

        root = Path(__file__).resolve().parents[2]
        doc = yaml.safe_load((root / ".github" / "workflows" / "wac-cd.yml").read_text())
        steps = doc["jobs"]["plan"]["steps"]
        names = [s.get("name") for s in steps]
        step = steps[names.index("Plan the version")]
        refusal = steps[names.index("Plan the version") + 1]
        self.assertEqual(refusal["name"], "Refuse to replay an old version")
        self.assertEqual(refusal["if"], "${{ steps.version.outputs.replay_refused != '' }}")
        self.assertEqual(refusal["env"], {"REASON": "${{ steps.version.outputs.replay_refused }}"})
        self.assertIsInstance(refusal["timeout-minutes"], int)
        self.assertEqual(names[-1], refusal["name"], "nothing runs between the planning step and the refusal")

        def git(repo, *args):
            return subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                 "-c", "tag.gpgsign=false", *args],
                cwd=repo, check=True, capture_output=True, text=True,
            ).stdout.strip()

        def run(case_head, rollback="", extra_commit=False, origin=True, untagged_old=False):
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                repo, scripts = tmp / "repo", tmp / "scripts"
                repo.mkdir()
                scripts.mkdir()
                shutil.copy(root / "scripts" / "wacd-version.py", scripts / "wacd-version.py")
                git(repo, "-c", "init.defaultBranch=main", "init", "-q")
                git(repo, "commit", "--allow-empty", "-q", "-m", "feat: first")
                first = git(repo, "rev-parse", "HEAD")
                git(repo, "tag", "v0.1.0")
                git(repo, "commit", "--allow-empty", "-q", "-m", "fix: failed before it was tagged")
                old = git(repo, "rev-parse", "HEAD")
                git(repo, "commit", "--allow-empty", "-q", "-m", "feat: second")
                git(repo, "tag", "v0.2.0")
                if origin:
                    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
                if case_head == "first":
                    git(repo, "checkout", "-q", first)
                if untagged_old:
                    git(repo, "checkout", "-q", old)
                if extra_commit:
                    git(repo, "commit", "--allow-empty", "-q", "-m", "fix: next")
                head = git(repo, "rev-parse", "HEAD")
                (scripts / "wacd-chart.py").write_text(
                    "import sys\n"
                    f"print('revision={first}' if sys.argv[1] == 'chart-revision' else 'state=present')\n"
                )
                (scripts / "wacd-tag.py").write_text("print('claim=pushed')\n")
                out, summary = tmp / "out", tmp / "summary"
                env = {**os.environ, "WACD_SCRIPTS": str(scripts), "RUNNER_TEMP": str(tmp),
                       "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_SHA": head,
                       "ROLLBACK_VERSION": rollback, "VERSION_SOURCE": "commits", "CALLER_VERSION": "",
                       "DEPLOY": "true", "APP": "app", "TAG_PREFIX": "v"}
                result = subprocess.run(["bash", "-c", step["run"]], env=env, cwd=repo,
                                        capture_output=True, text=True)
                outputs = dict(line.split("=", 1) for line in out.read_text().splitlines()) if out.exists() else {}
                reason = outputs.pop("replay_refused", "")
                if reason:
                    self.assertEqual(result.returncode, 0, "the planning step records a refused replay and succeeds")
                    self.assertEqual(outputs, {}, "no mode, version or tag is left for a later job")
                    self.assertFalse(summary.exists(), "the refusal step writes the summary")
                    result = subprocess.run(["bash", "-c", refusal["run"]], env={**env, "REASON": reason},
                                            capture_output=True, text=True)
                return result, outputs, summary.read_text() if summary.exists() else ""

        sentence = ("This run is for v0.1.0, but v0.2.0 is already published. "
                    "Roll back or release anew; an old build is never replayed.")
        result, outputs, summary = run("first")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(outputs, {})
        self.assertIn(sentence, result.stderr)
        self.assertIn(sentence, summary)
        self.assertIn("::error::wac-cd: ", result.stdout)

        result, outputs, _ = run("main")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((outputs["mode"], outputs["version"]), ("resume", "0.2.0"))

        result, outputs, summary = run("main", origin=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(outputs, {})
        self.assertIn("origin/main is not in the checkout", summary)
        self.assertIn("origin/main is not in the checkout", result.stderr)

        result, outputs, _ = run("main", extra_commit=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((outputs["mode"], outputs["version"]), ("release", "0.2.1"))

        result, outputs, summary = run("main", untagged_old=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(outputs, {})
        old_sentence = ("This commit is older than v0.2.0, which is already published and contains or replaces it. "
                        "Roll back or release anew; an old build is never replayed.")
        self.assertIn(old_sentence, result.stderr)
        self.assertIn(old_sentence, summary)

        result, outputs, _ = run("main", rollback="0.1.0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((outputs["mode"], outputs["version"], outputs["tag"]), ("rollback", "0.1.0", "v0.1.0"))

        jobs = doc["jobs"]
        for name in ("shadow", "build", "publish", "deploy", "github-release"):
            with self.subTest(job=name):
                condition = str(jobs[name].get("if", ""))
                self.assertIn("plan", jobs[name]["needs"])
                self.assertNotIn("always()", condition)
                if "cancelled()" in condition:
                    self.assertIn("!failure()", condition)

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
        app = next(st for st in job["steps"] if str(st.get("name", "")).startswith("Check out the app ("))
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
        self.assertEqual(upload["if"], "steps.contract.outputs.source == 'hemi'",
                         "only publish reads the copy, and only when the source is hemi; a files copy uploads nothing")
        download = next(s for s in self.wac_cd()["jobs"]["publish"]["steps"]
                        if str(s.get("uses", "")).startswith("actions/download-artifact@"))
        self.assertEqual(download["if"], "needs.plan.outputs.source == 'hemi'")
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
        self.assertIn(("wac-ci.yml", "contract"), checked)

    def test_no_job_holding_id_token_is_exempt_from_the_rule(self):
        self.assertEqual(KNOWN_ID_TOKEN_EXPOSURES_TO_REMOVE, (), "a job holding id-token: write may never be an exception")
        import yaml

        doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "workflows" / "wac-cd.yml").read_text())
        holders = sorted(name for name, job in doc["jobs"].items() if holds_id_token(doc, job))
        self.assertEqual(holders, ["deploy", "plan", "shadow"])
        for name in holders:
            with self.subTest(job=name):
                self.assertEqual(id_token_problems(doc, name, doc["jobs"][name]), [])

    def test_the_id_token_rule_names_each_way_in(self):
        pinned = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        good = {"name": "Check out (actions/checkout v7.0.1)", "uses": pinned}
        cases = (
            ("tag-pinned action", {"name": "x (actions/checkout v4)", "uses": "actions/checkout@v4"}, "allowlist"),
            ("setup-python", {"name": "x", "uses": "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"},
             "allowlist"),
            ("setup-python pinned on the allowlist",
             {"name": "x (actions/setup-python v7.1.0)",
              "uses": "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0"}, "allowlist"),
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

    def test_jobs_run_only_commit_pinned_allowlisted_actions(self):
        """Every job of every reusable workflow, and every job anywhere that holds id-token: write."""
        import yaml

        root = Path(__file__).resolve().parents[1] / "workflows"
        reusable, writers, id_token = [], [], []
        for path in sorted(root.glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            is_reusable = module._is_reusable(doc)
            if is_reusable:
                reusable.append(path.name)
            for job_name, job in doc["jobs"].items():
                if holds_write(doc, job):
                    writers.append((path.name, job_name))
                if holds_id_token(doc, job):
                    id_token.append((path.name, job_name))
                if not is_reusable and not holds_id_token(doc, job):
                    continue
                with self.subTest(workflow=path.name, job=job_name):
                    self.assertEqual(pinned_action_problems(job_name, job), [])
        for name in ("wac-cd.yml", "wac-ci.yml", "actionlint.yml", "techdocs.yml", "agents-md.yml",
                     "nextjs-site-check.yml", "nextjs-site-deploy.yml"):
            self.assertIn(name, reusable)
        for expected in (("wac-cd.yml", "publish"), ("wac-cd.yml", "github-release"), ("wac-cd.yml", "plan"),
                         ("wac-ci.yml", "contract"), ("nextjs-site-deploy.yml", "build-and-deploy")):
            self.assertIn(expected, writers)
        self.assertIn(("nextjs-site-deploy.yml", "build-and-deploy"), id_token)

    def test_reusable_workflows_are_found_whichever_form_on_takes(self):
        jobs = {"gate": {"runs-on": "${{ inputs.runner || vars.CI_RUNNER_SMALL }}", "steps": [{"run": "true"}]}}
        for label, on, reusable in (
            ("a mapping", {"workflow_call": {"inputs": {"runner": {"type": "string", "default": ""}}}}, True),
            ("a mapping with no configuration", {"workflow_call": None}, True),
            ("a string", "workflow_call", True),
            ("a list", ["push", "workflow_call"], True),
            ("unquoted on", None, True),
            ("another event as a string", "push", False),
            ("another event in a list", ["push", "pull_request"], False),
        ):
            doc = {"name": "wf", "jobs": jobs}
            if label == "unquoted on":
                doc[True] = "workflow_call"
            else:
                doc["on"] = on
            with self.subTest(on=label):
                self.assertIs(module._is_reusable(doc), reusable)
        problems = module._check_runner_is_a_caller_choice(Path("wf.yml"), {"name": "wf", "on": "workflow_call", "jobs": jobs})
        self.assertTrue(any("declares no `runner` input" in p for p in problems), problems)

    def test_the_pinned_action_rule_names_each_way_in(self):
        self.assertEqual(len({uses.split("@")[0] for uses in PINNED_ACTIONS}), len(PINNED_ACTIONS),
                         "one commit per action")
        for uses, tag in PINNED_ACTIONS.items():
            with self.subTest(uses=uses):
                self.assertRegex(uses, r"^[a-z0-9-]+/[a-z0-9-]+@[0-9a-f]{40}$")
                self.assertRegex(tag, r"^v\d+\.\d+\.\d+$")
        node = "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1"
        good = {"name": "Set up Node (actions/setup-node v7.1.0)", "uses": node}
        cases = (
            ("tag-pinned", {"name": "x (actions/setup-node v4)", "uses": "actions/setup-node@v4"}, "allowlist"),
            ("a commit off the allowlist",
             {"name": "x (actions/setup-node v4.4.0)", "uses": "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020"},
             "allowlist"),
            ("unnamed", {"uses": node}, "step name"),
            ("named with another tag", {"name": "x (actions/setup-node v4)", "uses": node}, "step name"),
        )
        for label, step, expected in cases:
            with self.subTest(case=label):
                problems = pinned_action_problems("job", {"steps": [good, {"run": "true"}, step]})
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(expected, problems[0])
        self.assertEqual(pinned_action_problems("job", {"steps": [good, {"run": "true"}]}), [])
        sha = "a" * 40
        for label, called, expected in (
            ("a local workflow", "./.github/workflows/actionlint.yml", None),
            ("another repository's workflow at a commit", f"someone/else/.github/workflows/x.yml@{sha}", None),
            ("another repository's workflow at a branch", "someone/else/.github/workflows/x.yml@main", "pinned by commit"),
            ("another repository's workflow at a tag", "someone/else/.github/workflows/x.yml@v1", "pinned by commit"),
            ("a short SHA", "someone/else/.github/workflows/x.yml@aaaaaaa", "pinned by commit"),
            ("a local path outside the workflows", "./lab-tools", "pinned by commit"),
        ):
            with self.subTest(call=label):
                problems = pinned_action_problems("job", {"uses": called})
                if expected is None:
                    self.assertEqual(problems, [])
                else:
                    self.assertEqual(len(problems), 1, problems)
                    self.assertIn(expected, problems[0])
        for label, doc, job, expected in (
            ("contents write", {}, {"permissions": {"contents": "write"}}, True),
            ("packages write", {}, {"permissions": {"contents": "read", "packages": "write"}}, True),
            ("id-token write", {}, {"permissions": {"id-token": "write"}}, True),
            ("write-all", {}, {"permissions": "write-all"}, True),
            ("read only", {}, {"permissions": {"contents": "read", "packages": "read"}}, False),
            ("the workflow's", {"permissions": {"contents": "write"}}, {"steps": []}, True),
            ("the job's over the workflow's", {"permissions": {"contents": "write"}},
             {"permissions": {"contents": "read"}}, False),
        ):
            with self.subTest(permissions=label):
                self.assertIs(holds_write(doc, job), expected)

    def token_step_env(self, head):
        """The env each run step of a token job gets in the planted-repository run, keyed by job and step."""
        sha = "a" * 40
        images = json.dumps({"web": {"repository": "localhost:30500/web", "tag": "1.4.2", "digest": "sha256:" + "e" * 64}},
                            separators=(",", ":"))
        runner = {"JOB_RUNNER_NAME": "ci-small-abc12", "JOB_RUNNER_OS": "Linux", "JOB_RUNNER_ARCH": "ARM64"}
        guard = {"SHARED_WORKFLOW_SHA": sha}
        code = {"ROLLBACK_VERSION": "", "VERSION_SOURCE": "commits", "CALLER_VERSION": "", "DEPLOY": "true",
                "APP": "app", "TAG_PREFIX": "v", "SETTINGS_REVISION": "", "SETTINGS_MODE": "", "SETTINGS_VERSION": "",
                "SETTINGS_TAG": "", "SETTINGS_COMMIT": "", "SETTINGS_SUMMARY": ""}
        settings = {**code, "SETTINGS_REVISION": "7", "SETTINGS_MODE": "settings", "SETTINGS_VERSION": "1.4.3",
                    "SETTINGS_TAG": "v1.4.3", "SETTINGS_COMMIT": head, "SETTINGS_SUMMARY": "settings revision 7"}
        refusal = ({"REASON": "a refusal"}, 1)
        return {
            ("plan", "Require the runner's python3 3.12 or later"): [(runner, 0)],
            ("plan", "Check the trigger"): [({"EVENT_NAME": "push", "ROLLBACK_VERSION": "", "SETTINGS_REVISION": "",
                                              "DEPLOY": "true"}, 0)],
            ("plan", "Require the defining workflow commit"): [(guard, 0)],
            ("plan", "Move the shared checkout out of the workspace"): [({}, 0)],
            ("plan", "Read the settings from the gateway"): [({"WAIT_TIMEOUT": "2100"}, 0)],
            ("plan", "Read the wacd profile"): [({"SOURCE": "hemi"}, 0), ({"SOURCE": "files"}, 0)],
            ("plan", "Plan the settings release"): [({
                "SETTINGS_REVISION": "7", "SOURCE": "hemi", "COPY_REVISION": "7", "APP": "app", "TAG_PREFIX": "v",
                "PROFILE_IMAGES": json.dumps([{"name": "web", "dockerfile": "Dockerfile", "context": "."}]),
                "GITHUB_TOKEN": "job-token"}, 0)],
            ("plan", "Refuse a settings release while the app is rolling out"): [refusal],
            ("plan", "Refuse a settings release behind a workflow change"): [refusal],
            ("plan", "Plan the version"): [(code, 0), ({**code, "ROLLBACK_VERSION": "1.4.2"}, 0), (settings, 0)],
            ("plan", "Refuse to replay an old version"): [refusal],
            ("shadow", "Start the clock"): [({}, 0)],
            ("shadow", "Require the runner's python3 3.12 or later"): [(runner, 0)],
            ("shadow", "Require the defining workflow commit"): [(guard, 0)],
            ("shadow", "Move the shared checkout out of the workspace"): [({}, 0)],
            ("shadow", "Install helm"): [({}, 0)],
            ("shadow", "Compare hemi's copy with the files"): [({}, 0)],
            ("deploy", "Require the runner's python3 3.12 or later"): [(runner, 0)],
            ("deploy", "Require the defining workflow commit"): [(guard, 0)],
            ("deploy", "Move the shared checkout out of the workspace"): [({}, 0)],
            ("deploy", "Read the published images"): [({"APP": "app", "VERSION": "1.4.3"}, 0)],
            ("deploy", "Summarize the release"): [({"MODE": "release", "VERSION": "1.4.3", "TAG": "v1.4.3",
                                                    "EXPECTED_SHA": head}, 0)],
            ("deploy", "Release through the gateway"): [({"MODE": "release", "VERSION": "1.4.3", "TAG": "v1.4.3",
                                                          "EXPECTED_SHA": head, "IMAGES": images,
                                                          "WAIT_TIMEOUT": "2100"}, 0)],
            ("deploy", "Prove the served build"): [({"HEALTH_URL": "http://app.app.svc.cluster.local/api/health",
                                                     "EXPECTED_SHA": head, "EXPECT_DB": "false"}, 0)],
            ("deploy", "Record the proof"): [({}, 0)],
            ("contract", "Require the runner's python3 3.12 or later"): [(runner, 0)],
            ("contract", "Require the defining workflow commit"): [(guard, 0)],
            ("contract", "Move the shared checkout out of the workspace"): [({}, 0)],
            ("contract", "Ask hemi for the settings"): [({"JOB_RUNNER_NAME": "ci-small-abc12"}, 0)],
        }

    def test_jobs_holding_id_token_import_nothing_from_the_calling_repository(self):
        """Every run step of a token job, run in a checkout that plants json.py, ssl.py and sitecustomize.py.

        Each planted module writes a marker when it is imported. The scripts are
        stand-ins that import the same standard modules, every python3 the steps
        start is the real interpreter (logged by a PATH wrapper), and git, the
        version check and the inline Python run for real.
        """
        import os
        import shutil
        import subprocess

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        repo, scripts, bin_dir = tmp / "repo", tmp / "scripts", tmp / "bin"
        for directory in (repo, scripts, bin_dir):
            directory.mkdir()

        def git(*args):
            return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                                   *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

        git("-c", "init.defaultBranch=main", "init", "-q")
        (repo / ".github" / "workflows").mkdir(parents=True)
        (repo / ".github" / "workflows" / "cd.yml").write_text("on: push\n")
        (repo / ".github" / "wacd.json").write_text("{}\n")
        planted = ("json", "ssl", "sitecustomize")
        for name in planted:
            (repo / f"{name}.py").write_text("open(__file__ + '.ran', 'w').close()\n")
        git("add", ".")
        git("commit", "-q", "-m", "feat: planted")
        head = git("rev-parse", "HEAD")
        git("update-ref", "refs/remotes/origin/main", "HEAD")

        running = {"web": {"repository": "localhost:30500/web", "tag": "1.4.2", "digest": "sha256:" + "e" * 64}}
        answers = {
            "wacd-contract.py fetch": "source=hemi\nrevision=7\nfleet_commit=" + "f" * 40 + "\nreason=r",
            "wacd-contract.py pr": "source=hemi\nrevision=7",
            "wacd-profile.py": "app=app\ntag_prefix=v",
            "wacd-release.py running": (f"running=1.4.2\ncommit={head}\nimages={json.dumps(running)}\n"
                                        "copy_revision=6\nfleet_commit=" + "f" * 40),
            "wacd-release.py earlier-release": "reuse=",
            "wacd-release.py": "Verified",
            "wacd-version.py --mode settings": "mode=settings\nversion=1.4.3\ntag=v1.4.3\nclaimed=\nabove=",
            "wacd-version.py": "mode=release\nversion=1.4.3\ntag=v1.4.3",
            "wacd-chart.py chart-revision": f"revision={head}",
            "wacd-chart.py chart-images": "images=" + json.dumps(running),
            "wacd-chart.py chart-copy-revision": "copy_revision=6",
            "wacd-chart.py": "state=absent",
            "wacd-tag.py": "claim=pushed",
            "wacd-shadow.py": "",
        }
        stub = (
            "import json, os, socket, ssl, sys\n"
            "line = os.path.basename(sys.argv[0]) + ' ' + ' '.join(sys.argv[1:])\n"
            "open(os.environ['STUB_LOG'], 'a').write(line + '\\n')\n"
            "answers = json.loads(os.environ['STUB_ANSWERS'])\n"
            "print(answers[max((key for key in answers if line.startswith(key)), key=len)])\n"
        )
        for name in ("wacd-contract.py", "wacd-profile.py", "wacd-release.py", "wacd-version.py", "wacd-chart.py",
                     "wacd-tag.py", "wacd-shadow.py"):
            (scripts / name).write_text(stub)
        (scripts / "install-helm.sh").write_text("exit 0\n")
        python_log, stub_log = tmp / "python.log", tmp / "stub.log"
        (bin_dir / "python3").write_text(f'#!/bin/bash\necho "$*" >> "{python_log}"\nexec "{sys.executable}" "$@"\n')
        (bin_dir / "python3").chmod(0o755)
        base = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
        base.update({"PATH": f"{bin_dir}:{base['PATH']}", "WACD_SCRIPTS": str(scripts), "RUNNER_TEMP": str(tmp),
                     "GITHUB_OUTPUT": str(tmp / "out"), "GITHUB_STEP_SUMMARY": str(tmp / "summary"),
                     "GITHUB_ENV": str(tmp / "env"), "GITHUB_SHA": head, "GITHUB_REF": "refs/heads/main",
                     "STUB_LOG": str(stub_log), "STUB_ANSWERS": json.dumps(answers)})

        def marks():
            found = sorted(path.name for path in repo.glob("*.ran"))
            for name in found:
                (repo / name).unlink()
            return found

        def run(script, env):
            (repo / ".wacd").mkdir(exist_ok=True)
            return subprocess.run(["bash", "-c", script], env=env, cwd=repo, capture_output=True, text=True)

        scenarios = self.token_step_env(head)
        covered = set()
        jobs = [("wac-cd", name, job) for name, job in self.wac_cd()["jobs"].items()]
        jobs += [("wac-ci", name, job) for name, job in self.wac_ci()["jobs"].items()]
        token_jobs = [(workflow, name, job) for workflow, name, job in jobs if holds_id_token({}, job)]
        self.assertEqual(sorted(name for _, name, _ in token_jobs), ["contract", "deploy", "plan", "shadow"])
        for workflow, job_name, job in token_jobs:
            job_env = job.get("env") or {}
            for key, value in job_env.items():
                self.assertNotIn("${{", str(value), f"{job_name} env {key} must be a constant")
            for step in job["steps"]:
                if "run" not in step:
                    continue
                key = (job_name, step["name"])
                self.assertIn(key, scenarios, f"{workflow} {job_name} step {step['name']!r} has no planted-repository run")
                covered.add(key)
                for step_env, expected in scenarios[key]:
                    with self.subTest(job=job_name, step=step["name"], env=step_env):
                        self.assertEqual(set(step_env), set(step.get("env") or {}))
                        python_log.write_text("")
                        result = run(step["run"], {**base, **{k: str(v) for k, v in job_env.items()}, **step_env})
                        self.assertEqual(marks(), [], f"{step['name']} imported a module the checkout planted")
                        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                        if "python3" in step["run"]:
                            self.assertNotEqual(python_log.read_text(), "", "the step started no interpreter")
        self.assertEqual(covered, set(scenarios), "a planted-repository run for a step that no longer exists")

        probe = "python3 -c 'import json, ssl'"
        plan_env = {k: str(v) for k, v in (self.wac_cd()["jobs"]["plan"].get("env") or {}).items()}
        self.assertEqual(run(probe, {**base, **plan_env}).returncode, 0)
        self.assertEqual(marks(), [], "the job's own env keeps a step that forgets -I from importing the checkout")
        self.assertEqual(run(probe, base).returncode, 0)
        self.assertEqual(marks(), ["json.py.ran", "ssl.py.ran"], "without it the plant is imported: the probe can see it")

    def test_wac_cd_jobs_holding_id_token_require_the_runners_python3_before_anything_else(self):
        jobs = self.wac_cd()["jobs"]
        for job_name, position in (("plan", 0), ("deploy", 0), ("shadow", 1)):
            with self.subTest(job=job_name):
                self.python_check_holds(jobs[job_name], job_name, position)
        self.assertEqual(
            jobs["shadow"]["steps"][0]["run"], 'echo "WACD_SHADOW_STARTED=$(date +%s)" >> "$GITHUB_ENV"',
            "only the clock stamp precedes the check in shadow: its budget starts there",
        )

    def python_check_holds(self, job, job_name, position, workflow="wac-cd"):
        import subprocess

        self.assertFalse([s for s in job["steps"] if "setup-python" in str(s.get("uses", ""))],
                         "setup-python fetches and runs a Python build in a job that can mint a gateway token")
        step = job["steps"][position]
        self.assertEqual(step["name"], "Require the runner's python3 3.12 or later")
        self.assertEqual(step["env"], {"JOB_RUNNER_NAME": "${{ runner.name }}",
                                       "JOB_RUNNER_OS": "${{ runner.os }}", "JOB_RUNNER_ARCH": "${{ runner.arch }}"})
        fake = (
            "#!/bin/bash\n"
            'case "$*" in\n'
            "  *importlib*) exit 0 ;;\n"
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
                        f"::error::{workflow}: {job_name} needs python3 3.12 or later on the runner; found Python 3.10.12 "
                        f"at {bin_dir}/python3 on ci-light-abc12 (Linux, ARM64).\n",
                    )
                if label == "absent":
                    self.assertIn(f"{job_name} needs python3 3.12 or later on the runner; found no python3 on ci-light-abc12 (Linux, ARM64).", result.stdout)
        self.assertTrue(sys.version_info >= (3, 12), "the real-interpreter cases below need 3.12 or later")
        runner_env = {"JOB_RUNNER_NAME": "ci-light-abc12", "JOB_RUNNER_OS": "Linux", "JOB_RUNNER_ARCH": "ARM64"}
        for broken in (None, "ssl", "http.client", "uuid", "json"):
            with self.subTest(broken_module=broken), tempfile.TemporaryDirectory() as tmp:
                venv = Path(tmp) / "venv"
                subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
                bin_dir = venv / "bin"
                if broken is not None:
                    site = subprocess.run(
                        [str(bin_dir / "python3"), "-I", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                        check=True, capture_output=True, text=True,
                    ).stdout.strip()
                    (Path(site) / "stripped.pth").write_text(f"import sys; sys.modules[{broken!r}] = None\n")
                env = {"PATH": str(bin_dir), **runner_env}
                result = subprocess.run(["/bin/bash", "-c", step["run"]], env=env, capture_output=True, text=True)
                if broken is None:
                    self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
                    continue
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(
                    result.stdout,
                    f"::error::{workflow}: {job_name} needs python3 3.12 or later on the runner; found "
                    f"Python {platform.python_version()} at {bin_dir}/python3, which cannot import {broken} "
                    "on ci-light-abc12 (Linux, ARM64).\n",
                )

    def test_wac_cd_python_check_lists_every_standard_module_the_scripts_import(self):
        steps = self.wac_cd()["jobs"]["plan"]["steps"]
        listed = re.search(r"^modules=\((.*?)\)$", steps[0]["run"], re.M).group(1).split()
        scripts = Path(__file__).resolve().parents[2] / "scripts"
        imported = set()
        for name in ("wacd-contract", "wacd-release", "ci-release", "wacd-chart", "wacd-shadow", "wacd-profile",
                     "wacd-tag", "wacd-version"):
            for node in ast.walk(ast.parse((scripts / f"{name}.py").read_text())):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    imported.add(node.module)
        imported.discard("__future__")
        self.assertEqual(sorted(listed), sorted(imported), "the check proves exactly the modules the scripts import")
        for module_name in listed:
            self.assertIn(module_name.split(".")[0], sys.stdlib_module_names)
        for job_name in ("deploy", "shadow"):
            check = self.wac_cd()["jobs"][job_name]["steps"][0 if job_name == "deploy" else 1]["run"]
            with self.subTest(job=job_name):
                self.assertEqual(re.search(r"^modules=\((.*?)\)$", check, re.M).group(1).split(), listed)
        with self.subTest(job="wac-ci contract"):
            check = self.wac_ci()["jobs"]["contract"]["steps"][0]["run"]
            self.assertEqual(re.search(r"^modules=\((.*?)\)$", check, re.M).group(1).split(), listed)

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

    def test_wac_cd_dispatch_takes_exactly_one_of_a_rollback_and_a_settings_revision(self):
        import os
        import subprocess

        step = next(s for s in self.wac_cd()["jobs"]["plan"]["steps"] if s.get("name") == "Check the trigger")
        self.assertEqual(step["env"]["SETTINGS_REVISION"], "${{ inputs.settings_revision }}")
        for event, rollback, revision, deploy, code, needle in (
            ("workflow_dispatch", "", "7", "true", 0, ""),
            ("workflow_dispatch", "", "9999999999", "true", 0, ""),
            ("workflow_dispatch", "1.2.3", "", "true", 0, ""),
            ("workflow_dispatch", "1.2.3", "7", "true", 1, "not both"),
            ("workflow_dispatch", "", "", "true", 1, "exactly one of rollback_version"),
            ("workflow_dispatch", "", "07", "true", 1, "settings_revision must be"),
            ("workflow_dispatch", "", "0", "true", 1, "settings_revision must be"),
            ("workflow_dispatch", "", "10000000000", "true", 1, "settings_revision must be"),
            ("workflow_dispatch", "", "7", "false", 1, "deploy: false"),
            ("push", "", "7", "true", 1, "only on workflow_dispatch"),
            ("push", "", "", "true", 0, ""),
        ):
            with self.subTest(event=event, rollback=rollback, revision=revision, deploy=deploy):
                env = {**os.environ, "GITHUB_REF": "refs/heads/main", "EVENT_NAME": event,
                       "ROLLBACK_VERSION": rollback, "SETTINGS_REVISION": revision, "DEPLOY": deploy}
                result = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertIn(needle, result.stdout)

    def test_wac_cd_settings_steps_sit_between_the_profile_and_the_claim(self):
        plan = self.wac_cd()["jobs"]["plan"]
        steps = plan["steps"]
        names = [s.get("name") or s.get("uses") for s in steps]
        settings = steps[names.index("Plan the settings release")]
        refusal = steps[names.index("Refuse a settings release behind a workflow change")]
        rollout = steps[names.index("Refuse a settings release while the app is rolling out")]
        self.assertEqual(settings["id"], "settings")
        self.assertEqual(settings["if"], "inputs.settings_revision != ''")
        self.assertEqual(refusal["if"], "${{ steps.settings.outputs.workflow_refused != '' }}")
        self.assertEqual(refusal["env"], {"REASON": "${{ steps.settings.outputs.workflow_refused }}"})
        self.assertEqual(rollout["if"], "${{ steps.settings.outputs.rollout_refused != '' }}")
        self.assertEqual(rollout["env"], {"REASON": "${{ steps.settings.outputs.rollout_refused }}"})
        self.assertEqual(
            names.index("Read the wacd profile") + 1, names.index("Plan the settings release"),
            "the settings plan reads the copy's profile",
        )
        self.assertEqual(
            names.index("Plan the settings release") + 1, names.index("Refuse a settings release while the app is rolling out")
        )
        self.assertEqual(
            names.index("Refuse a settings release while the app is rolling out") + 1,
            names.index("Refuse a settings release behind a workflow change"),
        )
        self.assertEqual(
            names.index("Refuse a settings release behind a workflow change") + 1, names.index("Plan the version"),
            "the refusal fails plan before Plan the version claims a tag",
        )
        version = steps[names.index("Plan the version")]
        for key, output in (("SETTINGS_MODE", "mode"), ("SETTINGS_VERSION", "version"), ("SETTINGS_TAG", "tag"),
                            ("SETTINGS_COMMIT", "commit"), ("SETTINGS_SUMMARY", "summary")):
            self.assertEqual(version["env"][key], f"${{{{ steps.settings.outputs.{output} }}}}")
        self.assertEqual(plan["outputs"]["source_images"], "${{ steps.settings.outputs.images }}")
        self.assertEqual(settings["env"]["GITHUB_TOKEN"], "${{ github.token }}", "the GitHub Release read")
        self.assertIn("deadline=$(( $(date +%s) + 120 ))", settings["run"])
        self.assertEqual(settings["run"].count('--deadline "$deadline"'), 2, "both network reads share the deadline")
        self.assertNotIn("wacd-chart.py", settings["run"], "every registry read happens inside the bounded reads")
        self.assertEqual(plan["permissions"], {"contents": "write", "id-token": "write"}, "no permission was added")

    def settings_plan(self, *, running="1.4.2", copy_revision="7", revision="7", source="hemi", charts=None,
                      workflow_change=False, rollback=False, extra_tags=(), claim="pushed", profile_images=None,
                      running_copy="6", status_exit=0, earlier="reuse=", running_fleet="f" * 40, status_err=None):
        """Run plan's settings steps against a temp repo, real wacd-version.py and stub gateway and registry reads."""
        import os
        import shutil
        import subprocess

        root = Path(__file__).resolve().parents[2]
        steps = self.wac_cd()["jobs"]["plan"]["steps"]
        by_name = {s.get("name"): s for s in steps}
        settings_step = by_name["Plan the settings release"]
        refusal_step = by_name["Refuse a settings release behind a workflow change"]
        rollout_step = by_name["Refuse a settings release while the app is rolling out"]
        version_step = by_name["Plan the version"]

        def git(repo, *args):
            return subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                 "-c", "tag.gpgsign=false", *args],
                cwd=repo, check=True, capture_output=True, text=True,
            ).stdout.strip()

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        repo, scripts = tmp / "repo", tmp / "scripts"
        repo.mkdir()
        scripts.mkdir()
        shutil.copy(root / "scripts" / "wacd-version.py", scripts / "wacd-version.py")
        git(repo, "-c", "init.defaultBranch=main", "init", "-q")
        (repo / ".github" / "workflows").mkdir(parents=True)
        (repo / ".github" / "workflows" / "cd.yml").write_text("on: push\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "feat: first")
        old = git(repo, "rev-parse", "HEAD")
        git(repo, "tag", "v1.3.9")
        git(repo, "commit", "--allow-empty", "-q", "-m", "fix: second")
        code = git(repo, "rev-parse", "HEAD")
        git(repo, "tag", "v1.4.2")
        if workflow_change:
            (repo / ".github" / "workflows" / "cd.yml").write_text("on: [push, workflow_dispatch]\n")
            git(repo, "commit", "-q", "-am", "ci: take the revision input")
        else:
            git(repo, "commit", "--allow-empty", "-q", "-m", "docs: later")
        for tag, where in extra_tags:
            git(repo, "tag", tag, {"code": code, "old": old}[where])
        head = git(repo, "rev-parse", "HEAD")
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        source_commit = old if rollback else code
        images = {"web": {"repository": "localhost:30500/web", "tag": running,
                          "digest": "sha256:" + ("d" if rollback else "e") * 64}}
        charts = {**(charts or {}), running: {"revision": source_commit, "images": images, "copy": running_copy,
                                              "fleet": running_fleet}}
        log = tmp / "log"
        log.write_text("")
        (scripts / "wacd-release.py").write_text(
            "import json, os, sys\n"
            "open(os.environ['STUB_LOG'], 'a').write('wacd-release.py ' + ' '.join(sys.argv[1:]) + '\\n')\n"
            "code = int(os.environ['STUB_STATUS_EXIT'])\n"
            "if code == 3 and 'STUB_STATUS_ERR' in os.environ:\n"
            "    sys.exit(print(os.environ['STUB_STATUS_ERR'], end='', file=sys.stderr) or 3)\n"
            "if code == 3:\n"
            "    sys.exit(print('wacd release: app was rolling out 1.4.2 for the whole 120 seconds', file=sys.stderr) or 3)\n"
            "if code:\n"
            "    sys.exit(print('wacd release: the gateway reports an unknown version', file=sys.stderr) or 1)\n"
            "if sys.argv[1] == 'earlier-release':\n"
            "    outcome = os.environ['STUB_EARLIER']\n"
            "    if outcome == 'refuse':\n"
            "        sys.exit(print('wacd release: Revision 7 was released as 1.4.3, and 1.3.9 runs now after a '\n"
            "                       'rollback. To put those settings on the running code, make them live again in '\n"
            "                       'hemi; they are released as a new revision.', file=sys.stderr) or 4)\n"
            "    if outcome == 'fail':\n"
            "        sys.exit(print('wacd release: GitHub answered HTTP 502 for the Release of v1.4.3', file=sys.stderr) or 1)\n"
            "    sys.exit(print(outcome))\n"
            "running = os.environ['STUB_RUNNING']\n"
            "chart = json.loads(os.environ['STUB_CHARTS'])[running]\n"
            "print('running=' + running)\n"
            "print('commit=' + chart['revision'])\n"
            "print('images=' + json.dumps(chart['images'], separators=(',', ':'), sort_keys=True))\n"
            "print('copy_revision=' + chart['copy'])\n"
            "print('fleet_commit=' + chart['fleet'])\n"
        )
        (scripts / "wacd-chart.py").write_text(
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "open(os.environ['STUB_LOG'], 'a').write('wacd-chart.py ' + ' '.join(args) + '\\n')\n"
            "assert args[1:3] == ['--registry', 'registry.network.svc.cluster.local:5000'], args\n"
            "chart = json.loads(os.environ['STUB_CHARTS']).get(args[args.index('--version') + 1])\n"
            "if args[0] == 'chart-copy-revision':\n"
            "    print('copy_revision=' + ('absent' if chart is None else chart['copy']))\n"
            "elif chart is None:\n"
            "    sys.exit(print('wacd chart: deploy/app is not in the registry', file=sys.stderr) or 1)\n"
            "elif args[0] == 'chart-revision':\n"
            "    print('revision=' + chart['revision'])\n"
            "elif args[0] == 'chart-images':\n"
            "    print('images=' + json.dumps(chart['images'], separators=(',', ':'), sort_keys=True))\n"
        )
        (scripts / "wacd-tag.py").write_text(
            "import os, sys\n"
            "open(os.environ['STUB_LOG'], 'a').write('wacd-tag.py ' + ' '.join(sys.argv[1:]) + '\\n')\n"
            "claim = os.environ['STUB_CLAIM']\n"
            "if claim == 'fail':\n"
            "    sys.exit(print('wacd tag: git push failed: refused', file=sys.stderr) or 1)\n"
            "print('Tag line')\n"
            "print('claim=' + claim)\n"
        )
        out, summary = tmp / "out", tmp / "summary"
        profile = profile_images if profile_images is not None else [{"name": "web", "dockerfile": "Dockerfile", "context": "."}]
        env = {**os.environ, "WACD_SCRIPTS": str(scripts), "RUNNER_TEMP": str(tmp), "GITHUB_OUTPUT": str(out),
               "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_SHA": head, "SETTINGS_REVISION": revision,
               "SOURCE": source, "COPY_REVISION": copy_revision, "APP": "app", "TAG_PREFIX": "v",
               "PROFILE_IMAGES": json.dumps(profile), "STUB_LOG": str(log), "STUB_RUNNING": running,
               "STUB_CHARTS": json.dumps(charts), "STUB_CLAIM": claim, "STUB_STATUS_EXIT": str(status_exit),
               "STUB_EARLIER": earlier, "GITHUB_TOKEN": "job-token", "FLEET_COMMIT": "f" * 40}
        if status_err is not None:
            env["STUB_STATUS_ERR"] = status_err
        self.assertEqual(set(settings_step["env"]), {"SETTINGS_REVISION", "SOURCE", "COPY_REVISION", "APP",
                                                     "TAG_PREFIX", "PROFILE_IMAGES", "GITHUB_TOKEN"})

        def outputs():
            return dict(line.split("=", 1) for line in out.read_text().splitlines()) if out.exists() else {}

        started = time.time()
        result = subprocess.run(["bash", "-c", settings_step["run"]], env=env, cwd=repo, capture_output=True, text=True)
        plan = {"settings": result, "settings_outputs": outputs(), "code": code, "old": old, "head": head,
                "started": started,
                "log": log, "summary": summary}
        if result.returncode != 0:
            return plan
        settings_outputs = outputs()
        if settings_outputs.get("rollout_refused"):
            plan["refusal"] = subprocess.run(
                ["bash", "-c", rollout_step["run"]], env={**env, "REASON": settings_outputs["rollout_refused"]},
                capture_output=True, text=True,
            )
            return plan
        if settings_outputs.get("workflow_refused"):
            plan["refusal"] = subprocess.run(
                ["bash", "-c", refusal_step["run"]], env={**env, "REASON": settings_outputs["workflow_refused"]},
                capture_output=True, text=True,
            )
            return plan
        out.write_text("")
        version_env = {**env, "ROLLBACK_VERSION": "", "VERSION_SOURCE": "commits", "CALLER_VERSION": "",
                       "DEPLOY": "true",
                       "SETTINGS_MODE": settings_outputs.get("mode", ""), "SETTINGS_VERSION": settings_outputs.get("version", ""),
                       "SETTINGS_TAG": settings_outputs.get("tag", ""), "SETTINGS_COMMIT": settings_outputs.get("commit", ""),
                       "SETTINGS_SUMMARY": settings_outputs.get("summary", "")}
        plan["version"] = subprocess.run(["bash", "-c", version_step["run"]], env=version_env, cwd=repo,
                                         capture_output=True, text=True)
        plan["version_outputs"] = outputs()
        return plan

    def test_wac_cd_settings_release_retags_the_running_version_above_main_s_newest(self):
        plan = self.settings_plan()
        self.assertEqual(plan["settings"].returncode, 0, plan["settings"].stdout + plan["settings"].stderr)
        self.assertEqual(plan["version"].returncode, 0, plan["version"].stdout + plan["version"].stderr)
        code = plan["code"]
        self.assertEqual(
            {k: plan["version_outputs"][k] for k in ("mode", "version", "tag", "expected_sha")},
            {"mode": "settings", "version": "1.4.3", "tag": "v1.4.3", "expected_sha": code},
            "the running code is what the health check must serve",
        )
        self.assertEqual(json.loads(plan["settings_outputs"]["images"])["web"]["digest"], "sha256:" + "e" * 64)
        calls = plan["log"].read_text().splitlines()
        self.assertRegex(calls[0], r"^wacd-release\.py running --app app --wait-timeout 120 --deadline (\d+)$",
                         "the running version comes from a steady gateway status")
        started = int(re.search(r"--deadline (\d+)$", calls[0]).group(1)) - 120
        self.assertLessEqual(abs(started - int(plan["started"])), 5, "the deadline is 120 seconds after the step began")
        self.assertIn(f"wacd-tag.py claim-tag --tag v1.4.3 --commit {code}", calls)
        summary = plan["summary"].read_text()
        self.assertIn("mode: settings", summary)
        self.assertIn(f"settings revision 7 on 1.4.2's code ({code[:12]}), released as 1.4.3", summary)

    def test_wac_cd_settings_release_after_a_rollback_ships_the_rolled_back_code(self):
        plan = self.settings_plan(running="1.3.9", rollback=True)
        self.assertEqual(plan["version"].returncode, 0, plan["version"].stdout + plan["version"].stderr)
        old = plan["old"]
        outputs = plan["version_outputs"]
        self.assertEqual((outputs["mode"], outputs["version"], outputs["expected_sha"]), ("settings", "1.4.3", old))
        self.assertIn(f"wacd-tag.py claim-tag --tag v1.4.3 --commit {old}", plan["log"].read_text())
        self.assertEqual(json.loads(plan["settings_outputs"]["images"])["web"]["digest"], "sha256:" + "d" * 64,
                         "the rolled-back version's digests, not the newest tag's")
        self.assertIn(f"settings revision 7 on 1.3.9's code ({old[:12]}), released as 1.4.3", plan["summary"].read_text())

    def test_wac_cd_settings_release_ends_as_none_when_a_newer_revision_is_live(self):
        plan = self.settings_plan(copy_revision="8", revision="7")
        self.assertEqual(plan["settings"].returncode, 0, plan["settings"].stderr)
        self.assertEqual(plan["version"].returncode, 0, plan["version"].stderr)
        self.assertEqual(plan["version_outputs"]["mode"], "none")
        self.assertEqual((plan["version_outputs"]["version"], plan["version_outputs"]["tag"]), ("", ""))
        self.assertIn("mode: none (a newer revision is live; its own release follows)", plan["summary"].read_text())
        self.assertEqual(plan["log"].read_text(), "", "no gateway status, registry read or tag push")

    def test_wac_cd_settings_release_refuses_a_copy_it_cannot_release_on_its_own(self):
        for kw, needle in (
            ({"source": "files"}, "come from its files"),
            ({"copy_revision": "6", "revision": "7"}, "older than the dispatched revision 7"),
            ({"profile_images": [{"name": "api", "dockerfile": "Dockerfile", "context": "."}]}, "release code first"),
        ):
            with self.subTest(case=kw):
                plan = self.settings_plan(**kw)
                self.assertEqual(plan["settings"].returncode, 1, plan["settings"].stdout)
                self.assertIn(f"::error::wac-cd: ", plan["settings"].stdout)
                self.assertIn(needle, plan["summary"].read_text())
                self.assertNotIn("wacd-tag.py", plan["log"].read_text())

    def test_wac_cd_settings_release_behind_a_workflow_change_is_refused_by_its_own_step(self):
        plan = self.settings_plan(workflow_change=True)
        self.assertEqual(plan["settings"].returncode, 0, "the planning step records the refusal and succeeds")
        self.assertEqual(set(plan["settings_outputs"]), {"workflow_refused"}, "no mode, version or tag is left")
        refusal = plan["refusal"]
        sentence = (f"Release code first: app's workflow files changed since 1.4.2, so its settings cannot be "
                    f"released on {plan['code'][:12]}.")
        self.assertEqual(refusal.returncode, 1)
        self.assertIn(sentence, refusal.stderr)
        self.assertIn(f"::error::wac-cd: {sentence}", refusal.stdout)
        self.assertIn(sentence, plan["summary"].read_text())
        self.assertNotIn("wacd-tag.py", plan["log"].read_text())

    def test_wac_cd_settings_rerun_never_spends_a_second_version_on_one_revision(self):
        live = self.settings_plan(running="1.4.3", running_copy="7", extra_tags=(("v1.4.3", "code"),))
        self.assertEqual(live["settings"].returncode, 0, live["settings"].stdout + live["settings"].stderr)
        self.assertEqual(live["version"].returncode, 0, live["version"].stdout + live["version"].stderr)
        self.assertEqual(live["version_outputs"]["version"], "1.4.3", "already live: the same version again")
        self.assertNotIn("earlier-release", live["log"].read_text(), "nothing above the live version to judge")
        self.assertIn(f"settings revision 7 on 1.4.3's code ({live['code'][:12]}), released as 1.4.3",
                      live["summary"].read_text())
        for earlier, expected in (("reuse=1.4.3", "1.4.3"), ("reuse=", "1.4.4")):
            with self.subTest(earlier=earlier):
                plan = self.settings_plan(extra_tags=(("v1.4.3", "code"),), earlier=earlier)
                self.assertEqual(plan["version"].returncode, 0, plan["version"].stdout + plan["version"].stderr)
                self.assertEqual(plan["version_outputs"]["version"], expected)
                self.assertEqual(plan["version_outputs"]["expected_sha"], plan["code"])
                call = next(line for line in plan["log"].read_text().splitlines() if "earlier-release" in line)
                self.assertRegex(call, r"^wacd-release\.py earlier-release --app app --prefix v --revision 7 "
                                       r"--running 1\.4\.2 --versions 1\.4\.3 --claimed 1\.4\.3 --deadline \d+$")

    def test_wac_cd_settings_rerun_of_the_live_revision_after_another_apps_save_re_proves_it(self):
        """The copy's fleet commit is wac.fleet's head, which moves with every app's save, so it decides nothing."""
        plan = self.settings_plan(running="1.4.3", running_copy="7", extra_tags=(("v1.4.3", "code"),),
                                  running_fleet="a" * 40)
        self.assertEqual(plan["settings"].returncode, 0, plan["settings"].stdout + plan["settings"].stderr)
        self.assertEqual(plan["version"].returncode, 0, plan["version"].stdout + plan["version"].stderr)
        self.assertEqual((plan["version_outputs"]["mode"], plan["version_outputs"]["version"]), ("settings", "1.4.3"),
                         "V again, never a refusal and never a new version")

    def test_wac_cd_settings_rerun_after_a_rollback_refuses_instead_of_undoing_it(self):
        sentence = ("Revision 7 was released as 1.4.3, and 1.3.9 runs now after a rollback. To put those settings "
                    "on the running code, make them live again in hemi; they are released as a new revision.")
        plan = self.settings_plan(running="1.3.9", rollback=True, extra_tags=(("v1.4.3", "code"),), earlier="refuse")
        self.assertEqual(plan["settings"].returncode, 1, plan["settings"].stdout)
        self.assertIn(f"::error::wac-cd: {sentence}\n", plan["settings"].stdout)
        self.assertIn(sentence, plan["summary"].read_text())
        self.assertNotIn("wacd-tag.py", plan["log"].read_text(), "nothing tagged or retagged")
        call = next(line for line in plan["log"].read_text().splitlines() if "earlier-release" in line)
        self.assertIn("--running 1.3.9 --versions 1.4.3,1.4.2 --claimed  --deadline", call,
                      "every version above the rolled-back one is judged, newest first")
        fault = self.settings_plan(running="1.3.9", rollback=True, extra_tags=(("v1.4.3", "code"),), earlier="fail")
        self.assertEqual(fault["settings"].returncode, 1, "an unreadable Release fails, never reads as absent")
        self.assertIn("::error::wac-cd: wacd release: GitHub answered HTTP 502", fault["settings"].stdout)
        self.assertEqual(fault["settings_outputs"], {})

    def test_wac_cd_rerun_of_a_superseded_settings_run_after_a_rollback_is_none_before_rule_b(self):
        """R (revision 7) patched 1.4.3 and was never proved; a rollback runs 1.3.9; the copy is now 9; R is re-run."""
        plan = self.settings_plan(
            revision="7", copy_revision="9", running="1.3.9", rollback=True,
            extra_tags=(("v1.4.3", "code"),), charts={"1.4.3": {"revision": None, "images": None, "copy": "7"}},
            earlier="refuse",
        )
        self.assertEqual(plan["settings"].returncode, 0, plan["settings"].stdout + plan["settings"].stderr)
        self.assertEqual(plan["version"].returncode, 0, plan["version"].stderr)
        self.assertEqual(plan["version_outputs"]["mode"], "none")
        self.assertEqual((plan["version_outputs"]["version"], plan["version_outputs"]["tag"]), ("", ""))
        self.assertIn("mode: none (a newer revision is live; its own release follows)", plan["summary"].read_text())
        self.assertEqual(plan["log"].read_text(), "",
                         "no status read, no rule B Release read, no registry read and no tag claim")
        jobs = self.wac_cd()["jobs"]
        self.assertFalse(self.runs(jobs["retag"], "none"), "nothing is retagged")
        self.assertFalse(self.runs(jobs["publish"], "none"))
        steps = [s.get("name") for s in jobs["plan"]["steps"]]
        settings = jobs["plan"]["steps"][steps.index("Plan the settings release")]["run"]
        self.assertLess(settings.index("(( COPY_REVISION > SETTINGS_REVISION ))"), settings.index("earlier-release"),
                        "the superseded check comes before rule B")

    def test_wac_cd_settings_release_while_the_app_rolls_out_is_refused_by_its_own_step(self):
        plan = self.settings_plan(status_exit=3)
        self.assertEqual(plan["settings"].returncode, 0, "the planning step records the refusal and succeeds")
        self.assertEqual(set(plan["settings_outputs"]), {"rollout_refused"}, "no mode, version or tag is left")
        refusal = plan["refusal"]
        self.assertEqual(refusal.returncode, 1)
        self.assertIn("::error::wac-cd: app was rolling out 1.4.2 for the whole 120 seconds", refusal.stdout)
        self.assertIn("app was rolling out 1.4.2", plan["summary"].read_text())
        self.assertNotIn("wacd-tag.py", plan["log"].read_text())
        fault = self.settings_plan(status_exit=1)
        self.assertEqual(fault["settings"].returncode, 1, "an unknown running version is a fault, not a rollout")
        self.assertIn("::error::wac-cd: wacd release: the gateway reports an unknown version", fault["settings"].stdout)
        self.assertEqual(fault["settings_outputs"], {})

    def test_wac_cd_a_rollout_refusal_with_no_sentence_fails_the_planning_step(self):
        """An empty sentence would skip the refusal step and leave a green run that released nothing."""
        for err in ("", "wacd release: \n"):
            with self.subTest(stderr=err):
                plan = self.settings_plan(status_exit=3, status_err=err)
                self.assertEqual(plan["settings"].returncode, 1, plan["settings"].stdout + plan["settings"].stderr)
                self.assertIn("::error::wac-cd: The settings release was refused while the app rolls out, and the "
                              "refusal gave no reason.", plan["settings"].stdout)
                self.assertEqual(plan["settings_outputs"], {}, "no refusal output: the planning step itself failed")

    def plan_version(self, version_out="", version_code=0, version_err="", copy_revision="copy_revision=5",
                     copy_code=0):
        """Run Plan the version for a push with stand-in wacd-version.py, wacd-chart.py and wacd-tag.py."""
        import os
        import shutil
        import subprocess

        step = next(s for s in self.wac_cd()["jobs"]["plan"]["steps"] if s.get("name") == "Plan the version")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / "wacd-version.py").write_text(
            f"import sys\nsys.stdout.write({version_out!r})\nsys.stderr.write({version_err!r})\nsys.exit({version_code})\n")
        (tmp / "wacd-chart.py").write_text(
            "import sys\n"
            "if sys.argv[1] == 'chart-copy-revision':\n"
            f"    print({copy_revision!r})\n"
            f"    sys.exit({copy_code})\n"
            "print('state=present')\n")
        (tmp / "wacd-tag.py").write_text("print('claim=pushed')\n")
        out, summary = tmp / "out", tmp / "summary"
        env = {**os.environ, "WACD_SCRIPTS": str(tmp), "RUNNER_TEMP": str(tmp), "GITHUB_OUTPUT": str(out),
               "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_SHA": "a" * 40, "ROLLBACK_VERSION": "",
               "VERSION_SOURCE": "commits", "CALLER_VERSION": "", "DEPLOY": "true", "APP": "app", "TAG_PREFIX": "v",
               "SETTINGS_REVISION": ""}
        result = subprocess.run(["bash", "-c", step["run"]], env=env, cwd=tmp, capture_output=True, text=True)
        outputs = dict(line.split("=", 1) for line in out.read_text().splitlines()) if out.exists() else {}
        return result, outputs, summary.read_text() if summary.exists() else ""

    def test_wac_cd_a_replay_refusal_with_no_sentence_fails_the_planning_step(self):
        """An empty sentence would skip Refuse to replay an old version and leave a green run that released nothing."""
        for err in ("", "wacd version: \n"):
            with self.subTest(stderr=err):
                result, outputs, summary = self.plan_version(version_code=3, version_err=err)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                sentence = "This run was refused as a replay of an old version, and the refusal gave no reason."
                self.assertIn(f"::error::wac-cd: {sentence}", result.stdout)
                self.assertIn(sentence, summary)
                self.assertEqual(outputs, {}, "no replay_refused output: the planning step itself failed")

    def test_wac_cd_a_resume_names_the_settings_revision_its_chart_was_published_with(self):
        """plan's settings section names the copy as it is now; a resumed chart ships the one it was published with."""
        resume = "mode=resume\nversion=1.4.2\ntag=v1.4.2\n"
        for label, answer, code, line in (
            ("a revision", "copy_revision=5", 0, "Resuming 1.4.2, whose chart was published with settings revision 5."),
            ("the files", "copy_revision=none", 0, "Resuming 1.4.2, whose chart was published from the repository's files."),
            ("unreadable", "", 1, "Resuming 1.4.2; the settings revision its chart was published with could not be read."),
        ):
            with self.subTest(chart=label):
                result, outputs, summary = self.plan_version(version_out=resume, copy_revision=answer, copy_code=code)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(outputs["mode"], "resume")
                self.assertIn(line, summary)
        result, outputs, summary = self.plan_version(version_out="mode=release\nversion=1.4.3\ntag=v1.4.3\n")
        self.assertEqual(outputs["mode"], "release")
        self.assertNotIn("Resuming", summary)

    def test_wac_cd_settings_rerun_of_the_live_version_needs_no_workflow_check(self):
        plan = self.settings_plan(running="1.4.3", running_copy="7", extra_tags=(("v1.4.3", "code"),),
                                  workflow_change=True)
        self.assertEqual(plan["settings"].returncode, 0, plan["settings"].stderr)
        self.assertNotIn("workflow_refused", plan["settings_outputs"])
        self.assertEqual(plan["version"].returncode, 0, plan["version"].stderr)
        self.assertEqual(plan["version_outputs"]["version"], "1.4.3")

    def test_wac_cd_settings_release_fails_on_any_tag_claim_but_pushed_or_exists(self):
        plan = self.settings_plan(claim="exists")
        self.assertEqual(plan["version"].returncode, 0, plan["version"].stderr)
        self.assertEqual(plan["version_outputs"]["mode"], "settings")
        for claim in ("superseded", "fail"):
            with self.subTest(claim=claim):
                plan = self.settings_plan(claim=claim)
                self.assertEqual(plan["version"].returncode, 1, plan["version"].stdout)
                self.assertIn("::error::wac-cd: ", plan["version"].stdout)
                self.assertNotIn("mode", plan["version_outputs"], "never mode none: a refused settings tag fails")

    def test_wac_cd_retag_replaces_build_only_in_settings_mode(self):
        jobs = self.wac_cd()["jobs"]
        retag = jobs["retag"]
        self.assertEqual(retag["needs"], "plan")
        self.assertEqual(retag["if"], "needs.plan.outputs.mode == 'settings'")
        self.assertEqual(retag["permissions"], {"contents": "read"}, "the registry takes no token")
        self.assertEqual(jobs["build"]["if"], "needs.plan.outputs.mode == 'release' || needs.plan.outputs.mode == 'publish'",
                         "build never runs in settings mode")
        runs = [s for s in retag["steps"] if "wacd-retag.py" in s.get("run", "")]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["env"], {"SOURCE_IMAGES": "${{ needs.plan.outputs.source_images }}",
                                          "VERSION": "${{ needs.plan.outputs.version }}"})
        self.assertEqual(retag["outputs"], {"images": "${{ steps.retag.outputs.images }}"})
        self.assertEqual(jobs["deploy"]["needs"], ["plan", "build", "retag", "publish"])
        publish = jobs["publish"]
        self.assertEqual(publish["needs"], ["plan", "build", "retag"])
        self.assertEqual(
            " ".join(publish["if"].split()),
            "!cancelled() && !failure() && ( needs.plan.outputs.mode == 'release' || "
            "needs.plan.outputs.mode == 'publish' || needs.plan.outputs.mode == 'settings')",
        )
        step = next(s for s in publish["steps"] if s.get("name") == "Publish the deploy chart and the tag")
        self.assertEqual(step["env"]["IMAGES"], "${{ needs.build.outputs.images || needs.retag.outputs.images }}")
        for name in ("deploy", "github-release"):
            with self.subTest(job=name):
                self.assertIn("needs.plan.outputs.mode == 'settings'", jobs[name]["if"])
        for name, job in jobs.items():
            with self.subTest(job=name):
                self.assertEqual("wacd-retag.py" in json.dumps(job), name == "retag")

    def test_wac_cd_publish_renders_a_settings_release_from_the_running_code(self):
        import os
        import subprocess

        step = next(s for s in self.wac_cd()["jobs"]["publish"]["steps"]
                    if s.get("name") == "Publish the deploy chart and the tag")
        self.assertEqual(step["env"]["MODE"], "${{ needs.plan.outputs.mode }}")
        self.assertEqual(step["env"]["EXPECTED_SHA"], "${{ needs.plan.outputs.expected_sha }}")
        head, code, fleet = "a" * 40, "c" * 40, "f" * 40
        for mode, source_sha in (("settings", code), ("release", head)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                log = root / "log"
                (root / "wacd-chart.py").write_text(
                    "import os, sys\n"
                    "open(os.environ['STUB_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                    "if sys.argv[1] == 'chart-state':\n"
                    "    seen = os.path.join(os.environ['RUNNER_TEMP'], 'seen')\n"
                    "    print('state=present' if os.path.exists(seen) else 'state=absent')\n"
                    "    open(seen, 'w').close()\n"
                    "else:\n"
                    "    os.makedirs(os.path.join(os.environ['RUNNER_TEMP'], 'packaged'), exist_ok=True)\n"
                    "    open(os.path.join(os.environ['RUNNER_TEMP'], 'packaged', 'app-1.4.3.tgz'), 'w').close()\n"
                    "    print('chart_dir=' + os.environ['RUNNER_TEMP'])\n"
                )
                (root / "wacd-tag.py").write_text(
                    "import os, sys\nopen(os.environ['STUB_LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                )
                bin_dir = root / "bin"
                bin_dir.mkdir()
                (bin_dir / "helm").write_text("#!/bin/sh\nexit 0\n")
                (bin_dir / "helm").chmod(0o755)
                env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WACD_SCRIPTS": str(root),
                       "RUNNER_TEMP": str(root), "STUB_LOG": str(log), "GITHUB_SHA": head, "MODE": mode,
                       "EXPECTED_SHA": source_sha, "APP": "app", "VERSION": "1.4.3", "TAG": "v1.4.3",
                       "IMAGES": "{}", "WAC_APP_TGZ": "w.tgz", "SOURCE": "hemi", "REVISION": "7",
                       "FLEET_COMMIT": fleet}
                result = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = log.read_text().splitlines()
                self.assertEqual(
                    calls,
                    [
                        f"chart-state --registry registry.network.svc.cluster.local:5000 --app app --version 1.4.3 --source-sha {source_sha}",
                        f"package --profile .wacd-contract/profile.json --version 1.4.3 --source-sha {source_sha} --images {{}} "
                        f"--wac-app-tgz w.tgz --out {root}/deploy-chart --values-json .wacd-contract/values.json "
                        f"--annotation wacwini.com/revision=7 --annotation wacwini.com/fleet-commit={fleet}",
                        f"chart-state --registry registry.network.svc.cluster.local:5000 --app app --version 1.4.3 --source-sha {source_sha}",
                        f"push-tag --tag v1.4.3 --sha {source_sha}",
                    ],
                )

    def runs(self, job, mode, source="hemi", deploy_result="success"):
        """Whether a job's if: lets it run in a plan of this mode, with every earlier job succeeded."""
        condition = str(job.get("if", "true"))
        python = (
            condition.replace("${{", "").replace("}}", "")
            .replace("!cancelled()", "True").replace("!failure()", "True")
            .replace("needs.plan.outputs.mode", repr(mode)).replace("needs.plan.outputs.source", repr(source))
            .replace("needs.deploy.result", repr(deploy_result))
            .replace("&&", " and ").replace("||", " or ")
        )
        self.assertNotRegex(python, r"needs\.|steps\.|inputs\.|\(\)", f"the test cannot read {condition!r}")
        return eval(python)

    def test_wac_cd_jobs_that_run_in_each_mode(self):
        """The outcome markers hemi reads: which jobs a settings run, a superseded run and a release run."""
        jobs = self.wac_cd()["jobs"]
        names = ("shadow", "build", "retag", "publish", "deploy", "github-release")
        expected = {
            ("settings", "hemi"): {"retag", "publish", "deploy", "github-release"},
            ("none", "hemi"): set(),
            ("release", "files"): {"shadow", "build", "publish", "deploy", "github-release"},
            ("release", "hemi"): {"build", "publish", "deploy", "github-release"},
            ("publish", "files"): {"shadow", "build", "publish"},
            ("resume", "files"): {"shadow", "deploy", "github-release"},
            ("rollback", "files"): {"shadow", "deploy", "github-release"},
        }
        for (mode, source), running in expected.items():
            with self.subTest(mode=mode, source=source):
                self.assertEqual({n for n in names if self.runs(jobs[n], mode, source)}, running)
        self.assertFalse(self.runs(jobs["github-release"], "settings", deploy_result="failure"))

    def test_wac_cd_names_hemi_reads_are_the_job_ids_and_these_steps(self):
        """Hemi reads cd / plan, cd / retag, cd / publish, cd / deploy and two deploy steps by display name."""
        jobs = self.wac_cd()["jobs"]
        for name in ("plan", "shadow", "retag", "publish", "deploy", "github-release"):
            with self.subTest(job=name):
                self.assertNotIn("name", jobs[name], "a name: key would change the display name hemi reads")
        steps = [s.get("name") for s in jobs["deploy"]["steps"]]
        release, prove = steps.index("Release through the gateway"), steps.index("Prove the served build")
        self.assertLess(release, prove)
        self.assertIn("wacd-release.py release", jobs["deploy"]["steps"][release]["run"])
        self.assertIn("wacd-release.py health", jobs["deploy"]["steps"][prove]["run"])
        for step in jobs["deploy"]["steps"][release + 1:]:
            with self.subTest(after=step.get("name")):
                self.assertNotIn("wacd-release.py release", step.get("run", ""), "only one step patches the cluster")
        plan = [s.get("name") for s in jobs["plan"]["steps"]]
        for name in ("Refuse a settings release behind a workflow change",
                     "Refuse a settings release while the app is rolling out", "Refuse to replay an old version"):
            self.assertIn(name, plan)

    def test_wac_cd_the_status_read_comes_out_of_plan_s_budget(self):
        """The contract's capped wait, the status read's wait and the other steps' allowance fit plan's bound."""
        doc = self.wac_cd()
        plan = doc["jobs"]["plan"]
        settings = next(s for s in plan["steps"] if s.get("name") == "Plan the settings release")
        status = int(re.search(r"wacd-release\.py running --app \"\$APP\" --wait-timeout (\d+)", settings["run"]).group(1))
        spec = importlib.util.spec_from_file_location(
            "wacd_contract_budget", Path(__file__).resolve().parents[2] / "scripts" / "wacd-contract.py"
        )
        contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contract)
        other_steps = 180
        self.assertEqual(contract.PLAN_WAIT_CAP + status + other_steps, plan["timeout-minutes"] * 60,
                         "docs/reference/wac-cd.md states this budget")
        deadline = int(re.search(r"deadline=\$\(\( \$\(date \+%s\) \+ (\d+) \)\)", settings["run"]).group(1))
        self.assertEqual(deadline, status, "one wall-clock deadline bounds every read in the step")
        self.assertLess(status + 60, settings["timeout-minutes"] * 60, "the step's kill switch is never what stops it")

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

    def wac_ci(self):
        import yaml

        return yaml.safe_load((Path(__file__).resolve().parents[1] / "workflows" / "wac-ci.yml").read_text())

    def test_wac_ci_only_the_contract_job_holds_a_job_token_and_only_the_settings_readers_wait_for_it(self):
        """A dependent of a failed job reports skipped, which a ruleset counts as passing.

        checks and render read the settings, so they wait for contract and fail
        unless it succeeded. lint, techdocs and agents-md read none, and the
        required check wacd-ci / contract is what blocks a merge, so they start
        at once.
        """
        doc = self.wac_ci()
        jobs = doc["jobs"]
        self.assertEqual(sorted(name for name, job in jobs.items() if holds_id_token(doc, job)), ["contract"])
        self.assertEqual(jobs["contract"]["permissions"], {"contents": "read", "id-token": "write"})
        self.assertNotIn("needs", jobs["contract"])
        self.assertEqual(jobs["contract"]["outputs"], {"source": "${{ steps.contract.outputs.source }}"})
        others = [name for name in jobs if name != "contract"]
        self.assertEqual(sorted(others), ["agents-md", "checks", "lint", "render", "techdocs"])
        for name in ("lint", "techdocs", "agents-md"):
            with self.subTest(job=name):
                self.assertNotIn("needs", jobs[name])
                self.assertNotIn("if", jobs[name])
                self.assertNotIn("needs.contract", json.dumps(jobs[name]))
        for name in ("checks", "render"):
            job = jobs[name]
            with self.subTest(job=name):
                needs = job.get("needs")
                self.assertIn("contract", [needs] if isinstance(needs, str) else needs or [])
                self.assertEqual(job.get("if"), "${{ !cancelled() }}")
                first = job["steps"][0]
                self.assertEqual(first["name"], "Require the contract job's success")
                self.assertEqual(first["env"], {"CONTRACT_RESULT": "${{ needs.contract.result }}"})

    def test_wac_ci_contract_job_runs_on_the_callers_runner(self):
        def runs_on(expression, inputs, variables):
            body = re.fullmatch(r"\$\{\{ (.*) \}\}", expression).group(1)
            for term in body.split(" || "):
                scope, name = term.split(".")
                value = {"inputs": inputs, "vars": variables}[scope].get(name, "")
                if value:
                    return value
            return ""

        expression = self.wac_ci()["jobs"]["contract"]["runs-on"]
        variables = {"CI_RUNNER_SMALL": "ci-light", "CI_RUNNER_STANDARD": "ci-standard"}
        for inputs, expected in (({"runner": "ci-standard"}, "ci-standard"), ({"runner": ""}, "ci-light"),
                                 ({"runner": "", "build_runner": "ci-large"}, "ci-light"),
                                 ({"runner": "ci-standard", "build_runner": "ci-large"}, "ci-standard")):
            with self.subTest(inputs=inputs):
                self.assertEqual(runs_on(expression, inputs, variables), expected)

    def test_wac_ci_contract_job_is_the_check_the_rulesets_require(self):
        """The organisation ruleset requires `wacd-ci / contract`: the caller's job id, then this job's id."""
        jobs = self.wac_ci()["jobs"]
        self.assertIn("contract", jobs)
        self.assertNotIn("name", jobs["contract"], "a name would replace `contract` in the check's name")
        self.assertNotIn("if", jobs["contract"], "a skipped contract job would count as passing")

    def test_wac_ci_a_job_after_a_failed_contract_job_fails_rather_than_passing(self):
        import subprocess

        step = self.wac_ci()["jobs"]["render"]["steps"][0]
        for result, code in (("success", 0), ("failure", 1), ("skipped", 1), ("cancelled", 1), ("", 1)):
            with self.subTest(result=result):
                run = subprocess.run(["bash", "-e", "-c", step["run"]], env={"CONTRACT_RESULT": result},
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, code, run.stdout + run.stderr)
                if code:
                    self.assertIn("::error::wac-ci: ", run.stdout)
        jobs = self.wac_ci()["jobs"]
        self.assertEqual(jobs["checks"]["steps"][0], step, "every job checks the same way")

    def test_wac_ci_contract_job_requires_the_runners_python3_before_anything_else(self):
        self.python_check_holds(self.wac_ci()["jobs"]["contract"], "contract", 0, workflow="wac-ci")

    def test_wac_ci_contract_job_bounds_every_step(self):
        for index, step in enumerate(self.wac_ci()["jobs"]["contract"]["steps"]):
            with self.subTest(step=step.get("name", index)):
                self.assertIsInstance(step.get("timeout-minutes"), int)

    def test_wac_ci_contract_reads_main_from_the_default_branch_and_never_the_base(self):
        """Whether a repository is cut over is main's to say: stacked pull requests are judged the same way."""
        steps = self.wac_ci()["jobs"]["contract"]["steps"]
        reads = {s["with"]["path"]: s for s in steps
                 if str(s.get("uses", "")).startswith("actions/checkout@") and "repository" not in s["with"]}
        self.assertEqual(sorted(reads), [".wacd-head", ".wacd-main"])
        sparse = {"fetch-depth": 1, "filter": "blob:none", "sparse-checkout": ".github/wacd.json",
                  "sparse-checkout-cone-mode": False, "persist-credentials": False}
        self.assertEqual(reads[".wacd-main"]["with"],
                         {"ref": "refs/heads/${{ github.event.repository.default_branch }}",
                          "path": ".wacd-main", **sparse})
        self.assertEqual(reads[".wacd-head"]["with"], {"path": ".wacd-head", **sparse})
        text = (Path(__file__).resolve().parents[1] / "workflows" / "wac-ci.yml").read_text()
        for word in ("base_ref", "pull_request.base", "github.head_ref", "pull_request.head"):
            with self.subTest(word=word):
                self.assertNotIn(word, text)

    def test_wac_ci_contract_step_asks_once_and_fails_with_the_scripts_sentence(self):
        import os
        import subprocess

        steps = self.wac_ci()["jobs"]["contract"]["steps"]
        asks = [s for s in steps if "wacd-contract.py" in s.get("run", "")]
        self.assertEqual(len(asks), 1)
        ask = asks[0]
        self.assertEqual(ask["id"], "contract")
        self.assertEqual(ask["env"], {"JOB_RUNNER_NAME": "${{ runner.name }}"},
                         "nothing the caller passes reaches the script")
        for code, expected in ((0, 0), (1, 1)):
            with self.subTest(script_exit=code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "wacd-contract.py").write_text(
                    "import sys\n"
                    "print('source=hemi\\nrevision=7\\nargv=' + ' '.join(sys.argv[1:]))\n"
                    "print('wacd contract: hemi did not answer;\\nre-run when it does.', file=sys.stderr)\n"
                    f"sys.exit({code})\n"
                )
                out = root / "out"
                env = {**os.environ, "WACD_SCRIPTS": str(root), "GITHUB_OUTPUT": str(out), "RUNNER_TEMP": tmp}
                result = subprocess.run(["bash", "-e", "-c", ask["run"]], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                if code:
                    self.assertIn("::error::wac-ci: wacd contract: hemi did not answer; re-run when it does.",
                                  result.stdout)
                    self.assertFalse(out.exists(), "a failure sets no output")
                else:
                    self.assertEqual(
                        out.read_text(),
                        "source=hemi\nrevision=7\nargv=pr --out .wacd-contract --main .wacd-main --head .wacd-head\n",
                    )

    def test_wac_ci_hands_hemis_copy_to_the_other_jobs_only_when_the_source_is_hemi(self):
        jobs = self.wac_ci()["jobs"]
        names = [s.get("name") for s in jobs["contract"]["steps"]]
        upload = next(s for s in jobs["contract"]["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
        self.assertLess(names.index("Ask hemi for the settings"), names.index(upload["name"]))
        self.assertEqual(upload["if"], "steps.contract.outputs.source == 'hemi'")
        self.assertEqual(upload["with"], {"name": "wac-ci-contract", "path": ".wacd-contract/",
                                          "include-hidden-files": True, "if-no-files-found": "error",
                                          "overwrite": True, "retention-days": 1})
        for name in ("checks", "render"):
            steps = jobs[name]["steps"]
            names = [s.get("name") or s.get("uses") for s in steps]
            with self.subTest(job=name):
                clear = steps[names.index("Clear any copy in the checkout")]
                download = next(s for s in steps if str(s.get("uses", "")).startswith("actions/download-artifact@"))
                self.assertEqual(clear["if"], "needs.contract.outputs.source == 'hemi'")
                self.assertEqual(clear["run"], "rm -rf .wacd-contract")
                self.assertEqual(download["if"], "needs.contract.outputs.source == 'hemi'")
                self.assertEqual(download["with"], {"name": "wac-ci-contract", "path": ".wacd-contract"})
                app = next(i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/checkout@")
                           and "repository" not in s["with"])
                self.assertLess(app, names.index(clear["name"]))
                self.assertLess(names.index(clear["name"]), names.index(download["name"]))
                self.assertLess(names.index(download["name"]), names.index("Read the wacd profile"))

    def test_wac_ci_reads_the_profile_from_the_copy_only_when_the_source_is_hemi(self):
        import os
        import subprocess

        jobs = self.wac_ci()["jobs"]
        for name in ("checks", "render"):
            step = next(s for s in jobs[name]["steps"] if s.get("name") == "Read the wacd profile")
            self.assertEqual(step["env"], {"SOURCE": "${{ needs.contract.outputs.source }}"})
            for source, expected in (("hemi", "--contract .wacd-contract/contract.json"),
                                     ("files", ".github/wacd.json"), ("", ".github/wacd.json")):
                with self.subTest(job=name, source=source), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    (root / "wacd-profile.py").write_text("import sys\nprint('argv=' + ' '.join(sys.argv[1:]))\n")
                    out = root / "out"
                    env = {**os.environ, "WACD_SCRIPTS": str(root), "GITHUB_OUTPUT": str(out), "SOURCE": source}
                    result = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(out.read_text(), f"argv={expected}\n")

    def test_wac_ci_render_packages_the_copys_runtime_only_when_the_source_is_hemi(self):
        import os
        import subprocess

        step = next(s for s in self.wac_ci()["jobs"]["render"]["steps"]
                    if s.get("name") == "Package the deploy chart with CI images")
        self.assertEqual(step["env"], {"WAC_APP_TGZ": "${{ steps.pull.outputs.tgz }}",
                                       "SOURCE": "${{ needs.contract.outputs.source }}"})
        today = [
            "ci-images --profile .github/wacd.json",
            "package --profile .github/wacd.json --version 0.0.0 --source-sha " + "a" * 40
            + " --ci --images {\"x\":1} --wac-app-tgz /t/wac-app.tgz --out {tmp}/deploy-chart",
        ]
        copy = [
            "ci-images --profile .wacd-contract/profile.json",
            "package --profile .wacd-contract/profile.json --version 0.0.0 --source-sha " + "a" * 40
            + " --ci --images {\"x\":1} --wac-app-tgz /t/wac-app.tgz --out {tmp}/deploy-chart"
            " --values-json .wacd-contract/values.json",
        ]
        for source, expected in (("files", today), ("", today), ("hemi", copy)):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                log = root / "argv.log"
                (root / "wacd-chart.py").write_text(
                    "import sys\n"
                    f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                    "print('images={\"x\":1}' if sys.argv[1] == 'ci-images' else 'chart_dir=/c')\n"
                )
                out = root / "out"
                env = {**os.environ, "WACD_SCRIPTS": str(root), "GITHUB_OUTPUT": str(out), "SOURCE": source,
                       "WAC_APP_TGZ": "/t/wac-app.tgz", "GITHUB_SHA": "a" * 40, "RUNNER_TEMP": tmp}
                result = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(log.read_text().splitlines(), [line.replace("{tmp}", tmp) for line in expected])
                self.assertEqual(out.read_text(), "chart_dir=/c\n")

    def test_repository_workflows_pass(self):
        self.assertEqual(module.main(), 0)


if __name__ == "__main__":
    unittest.main()
