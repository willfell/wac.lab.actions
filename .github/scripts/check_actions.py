"""Structural validation for every composite action in this repo.

There is no test suite here and nothing else runs before a consumer does. A
malformed `action.yml` is not caught at author time or at review time -- it is
caught when someone else's deploy job fails to start, in their repo, on their
merge. These checks are the cheapest possible stand-in for that: they run in
seconds and catch the class of mistake that breaks every consumer at once.

Shell bodies are extracted here rather than shellchecked in place because
`action.yml` is YAML: shellcheck cannot see a `run:` block until something
pulls it out and gives it a shebang.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Refs a consuming workflow must never be told to use. `@main` lets an
#: unrelated push here change how someone else builds and deploys; a floating
#: major is the same hole with a slower fuse, which is exactly how `v1` went
#: stale carrying a `gh` dependency the runner image does not have.
FLOATING_REFS = ("@main", "@master")

#: The input every reusable workflow here exposes so a caller can send the job
#: to its own runners, and the default that keeps pre-input callers unchanged.
RUNNER_INPUT = "runner"
DEFAULT_RUNNER = "ubuntu-latest"
RUNS_ON_EXPRESSION = "${{ inputs.runner }}"


def _action_files() -> list[Path]:
    return sorted(REPO_ROOT.glob("*/action.yml"))


def _check_schema(path: Path, doc: dict) -> list[str]:
    problems = []
    for key in ("name", "description", "runs"):
        if not doc.get(key):
            problems.append(f"{path}: missing top-level `{key}`")

    runs = doc.get("runs") or {}
    if runs.get("using") != "composite":
        problems.append(f"{path}: runs.using is {runs.get('using')!r}, expected 'composite'")

    for index, step in enumerate(runs.get("steps") or []):
        where = f"{path}: runs.steps[{index}]"
        if "run" in step and not step.get("shell"):
            # A composite step with `run:` and no `shell:` is rejected by the
            # Actions runner at load time, before any step executes.
            problems.append(f"{where}: has `run` but no `shell`")
        if "run" in step and "uses" in step:
            problems.append(f"{where}: has both `run` and `uses`")

    for name, spec in (doc.get("inputs") or {}).items():
        if not (spec or {}).get("description"):
            problems.append(f"{path}: input `{name}` has no description")
        if (spec or {}).get("required") and "default" in (spec or {}):
            problems.append(f"{path}: input `{name}` is required but also has a default")

    return problems


def _check_no_floating_refs(path: Path) -> list[str]:
    text = path.read_text()
    return [
        f"{path}: pins a floating ref ({ref}) -- consumers must be given an exact tag"
        for ref in FLOATING_REFS
        if ref in text
    ]


def _workflow_files() -> list[Path]:
    return sorted((REPO_ROOT / ".github/workflows").glob("*.yml"))


def _is_reusable(doc: dict) -> bool:
    # PyYAML resolves an unquoted `on:` key to True, so triggers live under
    # doc[True] rather than doc["on"].
    triggers = doc.get("on") or doc.get(True) or {}
    return isinstance(triggers, dict) and "workflow_call" in triggers


def _check_reusable_workflow(path: Path, doc: dict) -> list[str]:
    problems = []
    if not doc.get("name"):
        problems.append(f"{path}: reusable workflow missing top-level `name`")
    if not doc.get("jobs"):
        problems.append(f"{path}: reusable workflow has no `jobs`")
    problems.extend(_check_no_floating_refs(path))
    problems.extend(_check_runner_is_a_caller_choice(path, doc))
    problems.extend(_check_gate_identity(path, doc))
    return problems


def _check_gate_identity(path: Path, doc: dict) -> list[str]:
    if path.name not in ("agents-md.yml", "techdocs.yml"):
        return []
    steps = (doc.get("jobs", {}).get("gate") or {}).get("steps", [])
    shared = [(index, step) for index, step in enumerate(steps)
              if step.get("with", {}).get("repository") == "willfell/wac.lab.actions"]
    if len(shared) != 1:
        return [f"{path}: gate must check out exactly one shared source"]
    index, step = shared[0]
    problems = []
    if step["with"].get("ref") != "${{ job.workflow_sha }}":
        problems.append(f"{path}: shared gate source must use the defining job.workflow_sha")
    if not any(previous.get("env", {}).get("SHARED_WORKFLOW_SHA") == "${{ job.workflow_sha }}"
               and previous.get("run") == '[[ "$SHARED_WORKFLOW_SHA" =~ ^[0-9a-f]{40}$ ]]'
               for previous in steps[:index]):
        problems.append(f"{path}: missing SHA validation before shared checkout could select main")
    return problems


def _check_runner_is_a_caller_choice(path: Path, doc: dict) -> list[str]:
    """A reusable workflow must let the caller pick the runner.

    A reusable-workflow job runs on the CALLER's runners, billed to the caller
    and queued against the caller's pools -- but the label is written here. So
    one hardcoded `runs-on: ubuntu-latest` in this repo silently put a
    GitHub-hosted job into every consumer's pipeline, including the repos that
    had otherwise moved entirely onto self-hosted ARC pools. That is why this
    is a check rather than a note: the mistake is invisible from the caller's
    side, where the only visible symptom is a bill and a runner name nobody
    reads.

    The default keeps `ubuntu-latest`, so a caller that passes nothing is
    byte-identical to the behaviour before the input existed.
    """
    triggers = doc.get("on") or doc.get(True) or {}
    call = triggers.get("workflow_call") or {}
    declared = (call.get("inputs") or {}) if isinstance(call, dict) else {}

    problems = []
    if RUNNER_INPUT not in declared:
        problems.append(
            f"{path}: reusable workflow declares no `{RUNNER_INPUT}` input -- its jobs "
            "run on the caller's runners, so the caller has to be able to choose them"
        )
    elif declared[RUNNER_INPUT].get("default") != DEFAULT_RUNNER:
        problems.append(
            f"{path}: `{RUNNER_INPUT}` defaults to "
            f"{declared[RUNNER_INPUT].get('default')!r}, not {DEFAULT_RUNNER!r} -- "
            "changing the default changes every existing caller's runner silently"
        )

    for name, job in (doc.get("jobs") or {}).items():
        runs_on = job.get("runs-on")
        if runs_on != RUNS_ON_EXPRESSION:
            problems.append(
                f"{path}: job {name!r} has `runs-on: {runs_on}` instead of "
                f"{RUNS_ON_EXPRESSION} -- a hardcoded label here lands in every "
                "consumer's pipeline and cannot be overridden from the caller"
            )
    return problems


def _shellcheck(path: Path, doc: dict) -> list[str]:
    problems = []
    steps = (doc.get("runs") or {}).get("steps") or []
    with tempfile.TemporaryDirectory() as tmp:
        for index, step in enumerate(steps):
            body = step.get("run")
            if not body or step.get("shell") != "bash":
                continue
            # GitHub expands ${{ ... }} before bash ever sees it. Left in place
            # it is a syntax error, so substitute a harmless placeholder and
            # check the shape of the script around it.
            scrubbed = _strip_expressions(body)
            script = Path(tmp) / f"step_{index}.sh"
            script.write_text("#!/usr/bin/env bash\n" + scrubbed)
            result = subprocess.run(
                ["shellcheck", "--severity=warning", "--shell=bash", str(script)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                label = step.get("name") or f"steps[{index}]"
                problems.append(f"{path}: shellcheck failed for {label!r}\n{result.stdout}")
    return problems


def _strip_expressions(body: str) -> str:
    out = []
    rest = body
    while "${{" in rest:
        head, _, tail = rest.partition("${{")
        _, _, tail = tail.partition("}}")
        out.append(head)
        out.append("GHA_EXPR")
        rest = tail
    out.append(rest)
    return "".join(out)


def main() -> int:
    actions = _action_files()
    if not actions:
        print("::error::no */action.yml found -- this check would pass vacuously")
        return 1

    problems: list[str] = []
    for path in actions:
        try:
            doc = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            problems.append(f"{path}: does not parse as YAML: {exc}")
            continue
        problems.extend(_check_schema(path, doc))
        problems.extend(_check_no_floating_refs(path))
        problems.extend(_shellcheck(path, doc))

    reusable = []
    for path in _workflow_files():
        try:
            doc = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            problems.append(f"{path}: does not parse as YAML: {exc}")
            continue
        if _is_reusable(doc):
            reusable.append(path)
            problems.extend(_check_reusable_workflow(path, doc))
    if not reusable:
        problems.append(
            ".github/workflows: no reusable workflow found -- the reusable-workflow "
            "checks would pass vacuously"
        )

    for problem in problems:
        print(f"::error::{problem}")
    print(
        f"checked {len(actions)} composite action(s) and {len(reusable)} reusable "
        f"workflow(s); {len(problems)} problem(s)"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
