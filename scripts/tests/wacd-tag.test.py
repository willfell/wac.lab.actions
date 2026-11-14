"""wacd-tag pushes the release tag once and refuses a tag on another commit.

Every scenario runs in a fresh temp directory: an app repo whose only remote,
"origin", is a local bare repo in the same temp directory. No test touches a
network remote or the repository this file lives in.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "wacd-tag.py"
ISOLATED = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_TERMINAL_PROMPT": "0",
}


class Repos:
    """A temp app repo plus a local bare origin."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.origin = self.root / "origin.git"
        self.app = self.root / "app"
        self.env = {**os.environ, **ISOLATED}
        self.env.pop("GIT_DIR", None)
        self.env.pop("GIT_WORK_TREE", None)
        self.git_in(self.root, "init", "-q", "--bare", str(self.origin))
        self.git_in(self.root, "init", "-q", "-b", "main", str(self.app))
        self.git("remote", "add", "origin", str(self.origin))
        self.first = self.commit("one")
        self.second = self.commit("two")
        self.git("push", "-q", "origin", "main")

    def git_in(self, where, *args):
        result = subprocess.run(
            ["git", *args], cwd=where, env=self.env, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise AssertionError(f"git {args}: {result.stderr}")
        return result.stdout.strip()

    def git(self, *args):
        self.assert_contained()
        return self.git_in(self.app, *args)

    def assert_contained(self):
        top = Path(self.git_in(self.app, "rev-parse", "--show-toplevel")).resolve()
        if top != self.app:
            raise AssertionError(f"refusing: repo is {top}, not the temp app")
        remotes = self.git_in(self.app, "remote", "-v")
        for line in remotes.splitlines():
            url = Path(line.split()[1]).resolve()
            if url != self.origin:
                raise AssertionError(f"refusing: remote {url} is not the temp bare origin")

    def commit(self, message):
        self.git_in(self.app, "commit", "-q", "--allow-empty", "-m", message)
        return self.git_in(self.app, "rev-parse", "HEAD")

    def origin_tags(self):
        out = self.git_in(self.origin, "show-ref", "--tags", "-d") if self.has_origin_tags() else ""
        return {name: sha for sha, name in (line.split() for line in out.splitlines())}

    def has_origin_tags(self):
        return bool(self.git_in(self.origin, "tag", "-l"))

    def push_tag(self, *args):
        return self.run_script("push-tag", *args)

    def claim_tag(self, *args):
        return self.run_script("claim-tag", *args)

    def refuse_tag_pushes(self):
        """Install a pre-receive hook on origin that rejects every tag ref, as
        GitHub rejects a GITHUB_TOKEN tag push at a workflow-changing commit."""
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            "while read old new ref; do\n"
            "  case \"$ref\" in refs/tags/*)\n"
            "    echo \"refusing to allow a GitHub App to create or update workflow without workflows permission\" >&2\n"
            "    exit 1;;\n"
            "  esac\n"
            "done\n"
        )
        hook.chmod(0o755)

    def advance_origin_main(self, message):
        """Land a newer commit on origin's main, as a later merge would."""
        self.git("checkout", "-q", "-b", "newer")
        newer = self.commit(message)
        self.git("push", "-q", "origin", "newer:main")
        self.git("checkout", "-q", "main")
        return newer

    def run_script(self, command, *args):
        self.assert_contained()
        return subprocess.run(
            [sys.executable, str(SCRIPT), command, *args],
            cwd=self.app,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )


class TagCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repos = Repos(tmp.name)

    def assert_fails(self, result, *needles):
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(result.stderr.startswith("wacd tag: "), result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)
        for needle in needles:
            self.assertIn(needle, result.stderr)


class PushTagTests(TagCase):
    def test_absent_tag_is_created_lightweight_and_pushed(self):
        r = self.repos
        result = r.push_tag("--tag", "v0.1.0", "--sha", r.second)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Pushed tag v0.1.0", result.stdout)
        self.assertEqual(r.origin_tags(), {"refs/tags/v0.1.0": r.second})
        self.assertEqual(r.git_in(r.origin, "cat-file", "-t", "refs/tags/v0.1.0"), "commit")

    def test_tag_already_at_the_sha_is_accepted_unchanged(self):
        r = self.repos
        self.assertEqual(r.push_tag("--tag", "v0.1.0", "--sha", r.second).returncode, 0)
        again = r.push_tag("--tag", "v0.1.0", "--sha", r.second)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("already exists", again.stdout)
        self.assertEqual(r.origin_tags(), {"refs/tags/v0.1.0": r.second})

    def test_annotated_tag_at_the_sha_is_accepted(self):
        r = self.repos
        r.git("tag", "-a", "-m", "release", "v0.2.0", r.second)
        r.git("push", "-q", "origin", "refs/tags/v0.2.0")
        before = r.origin_tags()
        result = r.push_tag("--tag", "v0.2.0", "--sha", r.second)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(r.origin_tags(), before)

    def test_remote_tag_elsewhere_fails_naming_both_commits(self):
        r = self.repos
        for kind in ("lightweight", "annotated"):
            with self.subTest(kind=kind):
                tag = f"v0.0.9-{kind}"
                if kind == "annotated":
                    r.git("tag", "-a", "-m", "old", tag, r.first)
                else:
                    r.git("tag", tag, r.first)
                r.git("push", "-q", "origin", f"refs/tags/{tag}")
                r.git("tag", "-d", tag)
                before = r.origin_tags()
                result = r.push_tag("--tag", tag, "--sha", r.second)
                self.assert_fails(result, r.first, r.second)
                self.assertEqual(r.origin_tags(), before)

    def test_local_tag_elsewhere_fails_and_pushes_nothing(self):
        r = self.repos
        r.git("tag", "v0.3.0", r.first)
        result = r.push_tag("--tag", "v0.3.0", "--sha", r.second)
        self.assert_fails(result, r.first, r.second)
        self.assertEqual(r.origin_tags(), {})

    def test_local_tag_at_the_sha_is_pushed(self):
        r = self.repos
        r.git("tag", "v0.4.0", r.second)
        result = r.push_tag("--tag", "v0.4.0", "--sha", r.second)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(r.origin_tags(), {"refs/tags/v0.4.0": r.second})

    def test_bad_arguments_push_nothing(self):
        r = self.repos
        for label, args in (
            ("short sha", ("--tag", "v1.0.0", "--sha", "abc")),
            ("unknown commit", ("--tag", "v1.0.0", "--sha", "f" * 40)),
            ("bad tag", ("--tag", "v1..0", "--sha", r.second)),
            ("option-like tag", ("--tag=-v1", "--sha", r.second)),
            ("bad remote", ("--tag", "v1.0.0", "--sha", r.second, "--remote", "-x")),
        ):
            with self.subTest(case=label):
                self.assert_fails(r.push_tag(*args))
        self.assertEqual(r.origin_tags(), {})

    def test_unreachable_remote_fails_cleanly(self):
        r = self.repos
        r.origin.rename(r.root / "moved.git")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "push-tag", "--tag", "v1.0.0", "--sha", r.second],
            cwd=r.app,
            env=r.env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assert_fails(result)


class ClaimTagTests(TagCase):
    def outcome(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 2, result.stdout)
        self.assertTrue(lines[1].startswith("claim="), result.stdout)
        return lines[1].removeprefix("claim=")

    def test_absent_tag_is_pushed(self):
        r = self.repos
        self.assertEqual(self.outcome(r.claim_tag("--tag", "v0.1.0", "--sha", r.second)), "pushed")
        self.assertEqual(r.origin_tags(), {"refs/tags/v0.1.0": r.second})

    def test_tag_already_at_the_sha_is_accepted(self):
        r = self.repos
        self.assertEqual(self.outcome(r.claim_tag("--tag", "v0.1.0", "--sha", r.second)), "pushed")
        self.assertEqual(self.outcome(r.claim_tag("--tag", "v0.1.0", "--sha", r.second)), "exists")
        self.assertEqual(r.push_tag("--tag", "v0.1.0", "--sha", r.second).returncode, 0)
        self.assertEqual(r.origin_tags(), {"refs/tags/v0.1.0": r.second})

    def test_refused_push_after_main_moved_is_superseded(self):
        r = self.repos
        r.refuse_tag_pushes()
        newer = r.advance_origin_main("ci: bump the shared workflows")
        result = r.claim_tag("--tag", "v0.2.0", "--sha", r.second)
        self.assertEqual(self.outcome(result), "superseded")
        self.assertIn(newer, result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(r.origin_tags(), {})

    def test_refused_push_at_the_tip_fails(self):
        r = self.repos
        r.refuse_tag_pushes()
        result = r.claim_tag("--tag", "v0.2.0", "--sha", r.second)
        self.assert_fails(result, "git push failed", "still the tip of main")
        self.assertIn("workflows permission", result.stderr)
        self.assertEqual(r.origin_tags(), {})

    def test_refused_push_with_the_branch_absent_fails(self):
        r = self.repos
        r.refuse_tag_pushes()
        newer = r.advance_origin_main("feat: elsewhere")
        self.assertTrue(newer)
        result = r.claim_tag("--tag", "v0.2.0", "--sha", r.second, "--branch", "release")
        self.assert_fails(result, "release is absent")
        self.assertEqual(r.origin_tags(), {})

    def test_tag_on_another_commit_fails_even_when_main_moved(self):
        r = self.repos
        r.git("tag", "v0.5.0", r.first)
        r.git("push", "-q", "origin", "refs/tags/v0.5.0")
        r.git("tag", "-d", "v0.5.0")
        r.advance_origin_main("feat: later")
        result = r.claim_tag("--tag", "v0.5.0", "--sha", r.second)
        self.assert_fails(result, r.first, r.second)
        self.assertEqual(r.origin_tags(), {"refs/tags/v0.5.0": r.first})

    def test_bad_branch_pushes_nothing(self):
        r = self.repos
        for branch in ("-x", "a..b", ""):
            with self.subTest(branch=branch):
                self.assert_fails(r.claim_tag("--tag", "v1.0.0", "--sha", r.second, f"--branch={branch}"))
        self.assertEqual(r.origin_tags(), {})


if __name__ == "__main__":
    unittest.main()
