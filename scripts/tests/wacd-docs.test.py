"""wac-ci's scope decision: the checks are skipped only for a complete, documentation-only pull request.

`wacd-docs.py pr-scope` runs as the scope job runs it, `python3 -I` with the
job's environment, against a stand-in for GitHub's compare and commit APIs,
answering either what a test sets or what a real git repository holds. The
comparison is of the pull request's base with the merge commit `checks` tests
(GITHUB_SHA), never with the head. The matcher's
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
BASE, HEAD, MERGE = "b" * 40, "c" * 40, "e" * 40
DEFAULT = '["docs/**","mkdocs.yml","*.md"]'
SKIPPED = "Only documentation changed, so the checks that build and test the app did not run."


def answer(*files, total=None, merge_base=BASE):
    """A compare answer listing these files, each a filename or (filename, previous_filename)."""
    entries = []
    for entry in files:
        if isinstance(entry, tuple):
            entries.append({"filename": entry[1], "previous_filename": entry[0], "status": "renamed"})
        else:
            entries.append({"filename": entry, "status": "modified"})
    commits = [{"sha": "d" * 40}]
    value = {"status": "ahead", "ahead_by": 1, "total_commits": len(commits) if total is None else total,
             "commits": commits, "files": entries}
    if merge_base is not None:
        value["merge_base_commit"] = {"sha": merge_base}
    return value


MERGE_COMMIT = (200, {"sha": MERGE, "parents": [{"sha": BASE}, {"sha": HEAD}]})


class GitHub:
    """GET /repos/<repository>/compare/<base>...<head> and /commits/<sha>, answering what the test set."""

    def __init__(self, response, commit=MERGE_COMMIT):
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append((self.path, self.headers.get("Authorization")))
                status, body, *extra = outer.commit if "/commits/" in self.path else outer.response
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

        self.response, self.commit = response, commit
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def git(repo, *args, date=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    if date:
        env.update({"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})
    return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


class GitBacked(GitHub):
    """The two APIs answered from a real repository, as GitHub computes them.

    A comparison A...B is git's merge base of A and B against B: its files are
    `git diff --name-status -M <merge base> B`, its commits `<merge base>..B`.
    A commit's parents are git's, in order.
    """

    def __init__(self, repo):
        super().__init__(None)
        self.repo = repo
        outer = self

        def respond(path):
            if "/commits/" in path:
                sha = path.rsplit("/", 1)[1]
                parents = git(repo, "rev-list", "--parents", "-n", "1", sha).split()[1:]
                return 200, {"sha": sha, "parents": [{"sha": parent} for parent in parents]}
            base, head = path.rsplit("/", 1)[1].split("...")
            merge_base = git(repo, "merge-base", base, head)
            files = []
            for line in git(repo, "diff", "--name-status", "-M", merge_base, head).splitlines():
                status, *names = line.split("\t")
                entry = {"filename": names[-1], "status": status}
                if len(names) == 2:
                    entry["previous_filename"] = names[0]
                files.append(entry)
            commits = git(repo, "rev-list", "--reverse", f"{merge_base}..{head}").split()
            return 200, {"status": "ahead", "merge_base_commit": {"sha": merge_base}, "total_commits": len(commits),
                         "commits": [{"sha": sha} for sha in commits], "files": files}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append((self.path, self.headers.get("Authorization")))
                status, body = respond(self.path)
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server.shutdown()
        self.server.server_close()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()


class ScopeTest(unittest.TestCase):
    def scope(self, response=(200, answer("docs/guide.md")), commit=MERGE_COMMIT, github=None, base=BASE, head=HEAD,
              merge=MERGE, **overrides):
        compare = github or GitHub(response, commit)
        self.addCleanup(compare.close)
        self.compare = compare
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(tmp)], check=True))
        summary, event = tmp / "summary.md", tmp / "event.json"
        payload = {"pull_request": {"base": {"ref": overrides.pop("base_ref", "main"), "sha": base},
                                    "head": {"ref": "docs/guide", "sha": head}},
                   "repository": {"default_branch": "main"}}
        event.write_text(overrides.pop("event", json.dumps(payload)))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONSAFEPATH": "1",
               "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_API_URL": compare.url,
               "GITHUB_REPOSITORY": "wacwini/wac.app.travel", "GITHUB_TOKEN": "job-token",
               "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event), "GITHUB_SHA": merge,
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
        self.assertEqual(self.compare.seen, [
            (f"/repos/wacwini/wac.app.travel/commits/{MERGE}", "Bearer job-token"),
            (f"/repos/wacwini/wac.app.travel/compare/{BASE}...{MERGE}", "Bearer job-token"),
        ], "the merge commit checks tests is compared, never the pull request's head")

    def test_a_criss_cross_history_whose_merge_changes_code_runs_the_checks(self):
        """base...head shows only docs/x.md, but the merge GitHub builds and checks tests puts src/f back.

        main merges MB1 and MB2 (which changes src/f from a to b); the pull request merges the same two and
        resolves src/f back to a, then changes docs/x.md. The two branches have two merge bases, and git (as
        GitHub's comparison) takes MB1, against which the head changed only the doc. The merge commit, whose
        recursive merge resolves src/f to a, changes src/f against main.
        """
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(repo)], check=True))
        git(repo, "-c", "init.defaultBranch=main", "init", "-q")
        (repo / "src").mkdir()
        (repo / "docs").mkdir()
        (repo / "src" / "f").write_text("a\n")
        (repo / "docs" / "x.md").write_text("0\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "root", date="2026-01-01T00:00:00")
        root = git(repo, "rev-parse", "HEAD")
        (repo / "docs" / "other.md").write_text("one\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "mb1", date="2026-01-03T00:00:00")
        mb1 = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", root)
        (repo / "src" / "f").write_text("b\n")
        git(repo, "commit", "-qam", "mb2", date="2026-01-02T00:00:00")
        mb2 = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", mb1)
        git(repo, "merge", "-q", "--no-edit", mb2, date="2026-01-04T00:00:00")
        main = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", mb2)
        git(repo, "merge", "-q", "--no-edit", mb1, date="2026-01-04T00:00:00")
        (repo / "src" / "f").write_text("a\n")
        git(repo, "commit", "-q", "--amend", "-am", "merge, keeping a", date="2026-01-04T00:00:00")
        (repo / "docs" / "x.md").write_text("1\n")
        git(repo, "commit", "-qam", "docs only", date="2026-01-05T00:00:00")
        head = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", main)
        git(repo, "merge", "-q", "--no-edit", head, date="2026-01-06T00:00:00")
        merge = git(repo, "rev-parse", "HEAD")
        self.assertEqual(sorted(git(repo, "merge-base", "--all", main, head).split()), sorted([mb1, mb2]))
        self.assertEqual(git(repo, "merge-base", main, head), mb1, "the comparison's merge base hides src/f")
        self.assertEqual(git(repo, "diff", "--name-only", f"{main}...{head}").split(), ["docs/x.md"])
        self.assertEqual(git(repo, "diff", "--name-only", main, merge).split(), ["docs/x.md", "src/f"])
        self.assertRuns("a file outside the documentation paths changed: src/f",
                        github=GitBacked(repo), base=main, head=head, merge=merge)

    def test_a_merge_base_other_than_the_pull_requests_base_runs_the_checks(self):
        for label, merge_base in (("another commit", "f" * 40), ("none listed", None)):
            with self.subTest(merge_base=label):
                self.assertRuns("the comparison's merge base is not the pull request's base",
                                response=(200, answer("docs/guide.md", merge_base=merge_base)))

    def test_a_merge_commit_not_made_on_the_base_runs_the_checks(self):
        for label, commit, reason in (
            ("another first parent", (200, {"sha": MERGE, "parents": [{"sha": HEAD}, {"sha": BASE}]}),
             "the merge commit is not made on the pull request's base"),
            ("no parents", (200, {"sha": MERGE, "parents": []}),
             "the merge commit is not made on the pull request's base"),
            ("a 404", (404, {"message": "Not Found"}), "the merge commit could not be read"),
            ("not JSON", (200, b"{"), "the merge commit could not be read"),
            ("no parents list", (200, {"sha": MERGE}), "the merge commit could not be read"),
        ):
            with self.subTest(commit=label):
                self.assertRuns(reason, commit=commit)
        self.assertRuns("the changed files could not be read", merge="not-a-sha")

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
            ("a file with no name", (200, {"total_commits": 1, "commits": [{}], "merge_base_commit": {"sha": BASE},
                                           "files": [{"status": "added"}]})),
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
