"""wac-ci's scope decision: the checks are skipped only for a complete, documentation-only pull request.

`wacd-docs.py pr-scope` runs as the scope job runs it, `python3 -I` with the
job's environment, against a stand-in for GitHub's compare API. The matcher's
grammar and the profile's refusals are the case table's
(`wacd-profile.test.py`); wac-cd's push decision is run through its workflow
step in `.github/scripts/test_reusable_workflows.py`.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "wacd-docs.py"
BASE, HEAD = "b" * 40, "c" * 40
DEFAULT = '["docs/**","mkdocs.yml","*.md"]'
SKIPPED = "Only documentation changed, so the checks that build and test the app did not run."


def answer(*files, total=None):
    """A compare answer listing these files, each a filename or (filename, previous_filename)."""
    entries = []
    for entry in files:
        if isinstance(entry, tuple):
            entries.append({"filename": entry[1], "previous_filename": entry[0], "status": "renamed"})
        else:
            entries.append({"filename": entry, "status": "modified"})
    commits = [{"sha": "d" * 40}]
    return {"status": "ahead", "ahead_by": 1, "total_commits": len(commits) if total is None else total,
            "commits": commits, "files": entries}


class Compare:
    """GET /repos/<repository>/compare/<base>...<head>, answering what the test set."""

    def __init__(self, response):
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append((self.path, self.headers.get("Authorization")))
                status, body, *extra = outer.response
                if status == "hang":
                    threading.Event().wait(body)
                    return
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for name, value in (extra[0] if extra else {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.response = response
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ScopeTest(unittest.TestCase):
    def scope(self, response=(200, answer("docs/guide.md")), **overrides):
        compare = Compare(response)
        self.addCleanup(compare.close)
        self.compare = compare
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(tmp)], check=True))
        summary, event = tmp / "summary.md", tmp / "event.json"
        payload = {"pull_request": {"base": {"ref": overrides.pop("base_ref", "main"), "sha": BASE},
                                    "head": {"ref": "docs/guide", "sha": HEAD}},
                   "repository": {"default_branch": "main"}}
        event.write_text(overrides.pop("event", json.dumps(payload)))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONSAFEPATH": "1",
               "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_API_URL": compare.url,
               "GITHUB_REPOSITORY": "wacwini/wac.app.travel", "GITHUB_TOKEN": "job-token",
               "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event),
               "DOCS_PATHS": DEFAULT, "CONTRACT_RESULT": "success"}
        env.update(overrides)
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), "pr-scope"], env=env, cwd=tmp,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("job-token", result.stdout + result.stderr)
        outputs = dict(line.split("=", 1) for line in result.stdout.splitlines())
        self.assertEqual(set(outputs), {"heavy", "reason"})
        written = summary.read_text()
        self.assertTrue(written.startswith("### wac-ci scope\n\n"), written)
        return outputs["heavy"], outputs["reason"], written

    def assertRuns(self, reason, **kwargs):
        heavy, said, summary = self.scope(**kwargs)
        self.assertEqual((heavy, said), ("true", reason))
        self.assertIn(f"The checks ran because: {reason}\n", summary)
        self.assertNotIn(SKIPPED, summary)

    def test_a_complete_documentation_only_pull_request_skips_the_checks(self):
        heavy, reason, summary = self.scope((200, answer("docs/guide.md", "README.md", "mkdocs.yml",
                                                         ("docs/old.md", "docs/new.md"))))
        self.assertEqual((heavy, reason), ("false", ""))
        self.assertIn(SKIPPED + "\n", summary)
        self.assertEqual(self.compare.seen, [(f"/repos/wacwini/wac.app.travel/compare/{BASE}...{HEAD}",
                                               "Bearer job-token")])

    def test_a_rename_from_code_into_the_docs_runs_the_checks(self):
        self.assertRuns("a file outside the documentation paths changed: src/guide.md",
                        response=(200, answer(("src/guide.md", "docs/guide.md"))))

    def test_a_change_outside_the_documentation_paths_names_the_first_three(self):
        self.assertRuns("a file outside the documentation paths changed: src/a.ts, src/b.ts, package.json, and 1 more",
                        response=(200, answer("docs/a.md", "src/a.ts", "src/b.ts", "package.json", "src/c.ts")))

    def test_a_github_path_runs_the_checks_even_when_a_pattern_matches_it(self):
        self.assertRuns("a file under .github/ changed: .github/PULL_REQUEST_TEMPLATE.md",
                        response=(200, answer("docs/a.md", ".github/PULL_REQUEST_TEMPLATE.md")),
                        DOCS_PATHS='["docs/**","**/*.md"]')

    def test_a_path_is_escaped_before_it_reaches_the_output_or_the_summary(self):
        heavy, reason, summary = self.scope((200, answer("src/a%0A.ts\n::warning::x")))
        self.assertEqual(heavy, "true")
        self.assertEqual(reason, "a file outside the documentation paths changed: src/a%250A.ts%0A::warning::x")
        self.assertNotIn("\n::warning::", summary)

    def test_a_comparison_that_may_be_truncated_runs_the_checks(self):
        many = [f"docs/{n}.md" for n in range(300)]
        for label, response in (
            ("300 files", (200, answer(*many))),
            ("more commits than it lists", (200, answer("docs/a.md", total=251))),
            ("no files list", (200, {"total_commits": 1, "commits": [{}]})),
        ):
            with self.subTest(comparison=label):
                self.assertRuns("the list of changed files may be incomplete", response=response)
        self.assertEqual(self.scope((200, answer(*many[:299])))[0], "false", "299 files are a complete list")

    def test_an_unreadable_comparison_runs_the_checks(self):
        for label, response in (
            ("a 404", (404, {"message": "Not Found"})),
            ("a 500", (500, {})),
            ("a redirect", (302, b"", {"Location": "http://127.0.0.1:9/elsewhere"})),
            ("not JSON", (200, b"{oops")),
            ("a file with no name", (200, {"total_commits": 1, "commits": [{}], "files": [{"status": "added"}]})),
        ):
            with self.subTest(answer=label):
                self.assertRuns("the changed files could not be read", response=response)

    def test_an_oversized_comparison_runs_the_checks(self):
        self.assertRuns("the changed files could not be read", response=(200, b"x" * (32 * 1024 * 1024 + 1)))

    def test_anything_but_a_pull_request_into_the_default_branch_runs_the_checks(self):
        for label, overrides in (
            ("merge_group", {"GITHUB_EVENT_NAME": "merge_group",
                             "event": json.dumps({"merge_group": {"base_sha": BASE, "head_sha": HEAD}})}),
            ("push", {"GITHUB_EVENT_NAME": "push"}),
            ("pull_request_target", {"GITHUB_EVENT_NAME": "pull_request_target"}),
            ("a stacked pull request", {"base_ref": "feat/base"}),
            ("an unreadable event", {"event": "{"}),
            ("an event with no shas", {"event": json.dumps({"pull_request": {"base": {"ref": "main"}},
                                                             "repository": {"default_branch": "main"}})}),
        ):
            with self.subTest(event=label):
                self.assertRuns("this is not a pull request into the default branch", **overrides)
                self.assertEqual(self.compare.seen, [], "nothing is read for a run that cannot skip")

    def test_without_readable_patterns_the_checks_run(self):
        for label, overrides, reason in (
            ("contract failed", {"CONTRACT_RESULT": "failure"}, "the documentation paths could not be read"),
            ("no output", {"DOCS_PATHS": ""}, "the documentation paths could not be read"),
            ("refused", {"DOCS_PATHS": '["src/**"]'}, "a documentation path was refused"),
            ("not JSON", {"DOCS_PATHS": "docs/**"}, "a documentation path was refused"),
            ("never", {"DOCS_PATHS": "[]"}, "this repository sets no documentation paths"),
        ):
            with self.subTest(patterns=label):
                self.assertRuns(reason, **overrides)

    def test_an_empty_comparison_runs_the_checks(self):
        self.assertRuns("the comparison lists no changed file", response=(200, answer()))


if __name__ == "__main__":
    unittest.main()
