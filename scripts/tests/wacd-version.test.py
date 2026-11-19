"""wacd-version computes the release version from conventional commits and tags."""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "wacd-version.py"
SPEC = importlib.util.spec_from_file_location("wacd_version", SCRIPT)
version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(version)


def git(repo, *args):
    result = subprocess.run(
        [
            "git",
            "-c", "user.name=t",
            "-c", "user.email=t@t",
            "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class BumpTest(unittest.TestCase):
    def test_first_release_is_0_1_0(self):
        self.assertEqual(version.bump(None, ["feat!: x"]), "0.1.0")

    def test_bump_levels(self):
        self.assertEqual(version.bump("1.2.3", ["fix: a"]), "1.2.4")
        self.assertEqual(version.bump("1.2.3", ["feat(ui): a"]), "1.3.0")
        self.assertEqual(version.bump("1.2.3", ["refactor!: a"]), "2.0.0")
        self.assertEqual(version.bump("1.2.3", ["fix(api)!: a"]), "2.0.0")
        self.assertEqual(
            version.bump("1.2.3", ["chore: a\n\nBREAKING CHANGE: b"]), "2.0.0"
        )
        self.assertEqual(
            version.bump("1.2.3", ["chore: a\n\nBREAKING-CHANGE: b"]), "2.0.0"
        )
        self.assertEqual(version.bump("1.2.3", []), "1.2.4")

    def test_breaking_beats_feat_anywhere(self):
        self.assertEqual(
            version.bump("1.2.3", ["feat: a", "fix: b", "chore!: c"]), "2.0.0"
        )
        self.assertEqual(version.bump("1.2.3", ["fix: b", "feat: a"]), "1.3.0")

    def test_feature_words_are_not_feat(self):
        self.assertEqual(version.bump("1.2.3", ["feature: a"]), "1.2.4")
        self.assertEqual(version.bump("1.2.3", ["fix: a\n\nfeat: b"]), "1.2.4")

    def test_breaking_marker_must_open_a_line(self):
        self.assertEqual(
            version.bump("1.2.3", ["fix: a\n\nsee BREAKING CHANGE: b"]), "1.2.4"
        )


class LatestTagTest(unittest.TestCase):
    TAGS = ["ui-v9.9.9", "app-v1.0.0", "v1.2", "v1.2.3-rc.1", "1.9.0", "v1.10.0", "v1.9.9"]

    def test_latest_tag_ignores_other_families(self):
        self.assertEqual(version.latest_tag(self.TAGS, "v"), "1.10.0")
        self.assertEqual(version.latest_tag(self.TAGS, "app-v"), "1.0.0")
        self.assertEqual(version.latest_tag(self.TAGS, ""), "1.9.0")

    def test_leading_zeros_are_ignored(self):
        tags = ["v01.0.0", "v1.02.3", "v1.2.03", "v0.0.0", "v0.1.0"]
        self.assertEqual(version.latest_tag(tags, "v"), "0.1.0")
        self.assertIsNone(version.latest_tag(["v01.0.0"], "v"))

    def test_no_match_is_none(self):
        self.assertIsNone(version.latest_tag(self.TAGS, "zzz-v"))
        self.assertIsNone(version.latest_tag([], "v"))


class RepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        git(self.repo, "-c", "init.defaultBranch=main", "init", "-q")

    def commit(self, message):
        git(self.repo, "commit", "--allow-empty", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def tag(self, name, ref="HEAD"):
        git(self.repo, "tag", name, ref)

    def plan(self, prefix="v", source="commits", caller=None, **kw):
        return version.plan(self.repo, prefix, source, caller, **kw)

    def test_first_release_without_tags(self):
        self.commit("feat: first")
        self.assertEqual(
            self.plan(), {"mode": "release", "version": "0.1.0", "tag": "v0.1.0"}
        )

    def test_baton_continues_from_its_tag(self):
        self.commit("feat: old")
        self.tag("v1.37.2")
        self.commit("fix: x")
        self.assertEqual(
            self.plan(), {"mode": "release", "version": "1.37.3", "tag": "v1.37.3"}
        )

    def test_only_commits_after_the_tag_count(self):
        self.commit("feat!: old")
        self.tag("v1.0.0")
        self.commit("chore: later")
        self.assertEqual(self.plan()["version"], "1.0.1")

    def test_commit_bodies_are_read(self):
        self.commit("feat: old")
        self.tag("v1.0.0")
        self.commit("chore: later\n\nBREAKING CHANGE: gone")
        self.assertEqual(self.plan()["version"], "2.0.0")

    def test_other_tag_families_do_not_move_the_version(self):
        self.commit("feat: old")
        self.tag("app-v3.0.0")
        self.commit("fix: x")
        self.assertEqual(self.plan(prefix="v")["version"], "0.1.0")
        self.assertEqual(self.plan(prefix="app-v")["version"], "3.0.1")

    def test_resume_when_head_is_already_tagged(self):
        self.commit("feat: first")
        self.tag("v0.1.0")
        self.assertEqual(
            self.plan(), {"mode": "resume", "version": "0.1.0", "tag": "v0.1.0"}
        )

    def test_resume_takes_the_highest_head_tag(self):
        self.commit("feat: first")
        self.tag("v0.1.0")
        self.tag("v0.10.0")
        self.tag("v0.9.0")
        self.assertEqual(self.plan()["version"], "0.10.0")

    def two_releases(self):
        first = self.commit("feat: first")
        self.tag("v0.1.0")
        second = self.commit("feat: second")
        self.tag("v0.2.0")
        return first, second

    def refusal(self, **kw):
        with self.assertRaises(version.VersionError) as ctx:
            self.plan(**kw)
        return str(ctx.exception)

    def test_rerun_of_a_version_that_is_no_longer_the_newest_is_refused(self):
        first, second = self.two_releases()
        self.assertEqual(
            self.plan(), {"mode": "resume", "version": "0.2.0", "tag": "v0.2.0"}
        )
        git(self.repo, "checkout", "-q", first)
        sentence = (
            "This run is for v0.1.0, but v0.2.0 is already published. "
            "Roll back or release anew; an old build is never replayed."
        )
        for published in ("true", "unknown", "false"):
            with self.subTest(published=published):
                self.assertEqual(self.refusal(published=published), sentence)

    def test_the_newest_version_is_judged_across_every_branch(self):
        self.commit("feat: first")
        self.tag("v0.1.0")
        base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "-b", "elsewhere")
        self.commit("feat: elsewhere")
        self.tag("v0.3.0")
        git(self.repo, "checkout", "-q", base)
        self.assertIn("but v0.3.0 is already published", self.refusal())

    def test_other_tag_families_do_not_make_a_version_old(self):
        first, _ = self.two_releases()
        self.tag("ui-v9.0.0")
        self.tag("v0.3.0-rc.1")
        git(self.repo, "checkout", "-q", "main")
        self.assertEqual(self.plan()["version"], "0.2.0")
        self.assertEqual(self.plan(prefix="ui-v")["version"], "9.0.0")

    def test_a_head_with_two_tags_resumes_when_the_higher_is_the_newest(self):
        first, second = self.two_releases()
        self.tag("v0.2.1")
        self.assertEqual(
            self.plan(), {"mode": "resume", "version": "0.2.1", "tag": "v0.2.1"}
        )
        self.assertEqual(self.plan(published="false")["mode"], "release")

    def test_a_head_with_two_tags_is_refused_when_neither_is_the_newest(self):
        first, second = self.two_releases()
        self.tag("v0.1.1", first)
        self.tag("v0.1.5", first)
        git(self.repo, "checkout", "-q", first)
        self.assertEqual(
            self.refusal(),
            "This run is for v0.1.5, but v0.2.0 is already published. "
            "Roll back or release anew; an old build is never replayed.",
        )

    def test_an_untagged_head_releases_whatever_is_published(self):
        self.two_releases()
        self.commit("fix: next")
        self.assertEqual(
            self.plan(), {"mode": "release", "version": "0.2.1", "tag": "v0.2.1"}
        )
        self.assertEqual(
            self.plan(source="caller", caller="3.0.0")["version"], "3.0.0"
        )

    def test_publish_only_runs_keep_their_modes_on_an_old_head(self):
        first, _ = self.two_releases()
        git(self.repo, "checkout", "-q", first)
        self.assertEqual(
            self.plan(deploy=False),
            {"mode": "none", "version": "0.1.0", "tag": "v0.1.0"},
        )
        self.assertEqual(
            self.plan(deploy=False, published="false"),
            {"mode": "publish", "version": "0.1.0", "tag": "v0.1.0"},
        )

    def test_resume_ignores_what_the_commits_say(self):
        self.commit("feat!: first")
        self.tag("v4.0.0")
        self.assertEqual(self.plan(source="caller", caller="")["mode"], "resume")
        self.assertEqual(self.plan(source="caller", caller="9.9.9")["version"], "4.0.0")

    def test_existing_tag_on_another_commit_fails(self):
        self.commit("feat: base")
        self.tag("v0.1.0")
        git(self.repo, "checkout", "-q", "-b", "other")
        other = self.commit("feat: elsewhere")
        self.tag("v0.2.0")
        git(self.repo, "checkout", "-q", "main")
        self.commit("feat: next")
        with self.assertRaises(version.VersionError) as ctx:
            self.plan()
        self.assertIn("v0.2.0", str(ctx.exception))
        self.assertIn(other, str(ctx.exception))

    def test_caller_version_and_no_release(self):
        self.commit("fix: x")
        self.assertEqual(
            self.plan(source="caller", caller="2.0.0"),
            {"mode": "release", "version": "2.0.0", "tag": "v2.0.0"},
        )
        self.assertEqual(
            self.plan(source="caller", caller=""),
            {"mode": "none", "version": "", "tag": ""},
        )
        self.assertEqual(
            self.plan(source="caller", caller=None),
            {"mode": "none", "version": "", "tag": ""},
        )
        for bad in ("v2.0.0", "2.0", "2.0.0\n", "2.0.0-rc.1", " 2.0.0", "01.2.3", "1.02.3", "1.2.03"):
            with self.assertRaises(version.VersionError, msg=repr(bad)):
                self.plan(source="caller", caller=bad)

    def test_caller_version_collides_with_another_commit(self):
        first = self.commit("fix: x")
        self.tag("v2.0.0")
        self.commit("fix: y")
        with self.assertRaises(version.VersionError) as ctx:
            self.plan(source="caller", caller="2.0.0")
        self.assertIn("v2.0.0", str(ctx.exception))
        self.assertIn(first, str(ctx.exception))

    def test_unknown_source_fails(self):
        self.commit("fix: x")
        with self.assertRaises(version.VersionError):
            self.plan(source="magic")

    def test_publish_only_modes(self):
        self.commit("fix: x")
        self.assertEqual(
            self.plan(deploy=False),
            {"mode": "publish", "version": "0.1.0", "tag": "v0.1.0"},
        )
        self.tag("v0.1.0")
        self.assertEqual(
            self.plan(deploy=False),
            {"mode": "none", "version": "0.1.0", "tag": "v0.1.0"},
        )
        self.commit("fix: y")
        self.assertEqual(
            self.plan(source="caller", caller="", deploy=False),
            {"mode": "none", "version": "", "tag": ""},
        )

    def test_tagged_head_without_a_published_chart_releases_again(self):
        self.commit("feat: first")
        self.tag("v0.1.0")
        self.assertEqual(
            self.plan(published="false"),
            {"mode": "release", "version": "0.1.0", "tag": "v0.1.0"},
        )
        self.assertEqual(
            self.plan(published="false", deploy=False),
            {"mode": "publish", "version": "0.1.0", "tag": "v0.1.0"},
        )

    def test_tagged_head_with_a_published_chart_resumes(self):
        self.commit("feat: first")
        self.tag("v0.1.0")
        for published in ("true", "unknown"):
            with self.subTest(published=published):
                self.assertEqual(
                    self.plan(published=published),
                    {"mode": "resume", "version": "0.1.0", "tag": "v0.1.0"},
                )
                self.assertEqual(
                    self.plan(published=published, deploy=False),
                    {"mode": "none", "version": "0.1.0", "tag": "v0.1.0"},
                )

    def test_published_only_matters_for_a_tagged_head(self):
        self.commit("feat: first")
        self.tag("v0.1.0")
        self.commit("fix: next")
        self.assertEqual(
            self.plan(published="true"),
            {"mode": "release", "version": "0.1.1", "tag": "v0.1.1"},
        )

    def test_unknown_published_value_fails(self):
        self.commit("feat: first")
        with self.assertRaises(version.VersionError):
            self.plan(published="maybe")

    def test_annotated_tags_count(self):
        self.commit("feat: old")
        git(self.repo, "tag", "-a", "v1.4.0", "-m", "release")
        self.commit("fix: x")
        self.assertEqual(self.plan()["version"], "1.4.1")
        git(self.repo, "tag", "-a", "v1.4.1", "-m", "release")
        self.assertEqual(
            self.plan(), {"mode": "resume", "version": "1.4.1", "tag": "v1.4.1"}
        )

    def test_leading_zero_head_tag_does_not_resume(self):
        self.commit("feat: first")
        self.tag("v01.0.0")
        self.assertEqual(self.plan()["mode"], "release")

    def test_shallow_clone_fails(self):
        self.commit("feat: one")
        self.tag("v1.0.0")
        self.commit("fix: two")
        with tempfile.TemporaryDirectory() as dest:
            clone = Path(dest) / "clone"
            git(Path(dest), "clone", "-q", "--depth", "1", f"file://{self.repo}", str(clone))
            with self.assertRaises(version.VersionError) as ctx:
                version.plan(clone, "v", "commits", None)
            self.assertIn("shallow", str(ctx.exception))

    def test_non_utf8_commit_message_is_read(self):
        self.commit("feat: old")
        self.tag("v1.0.0")
        message = self.repo / "msg.txt"
        message.write_bytes(b"feat: caf\xe9\n")
        git(self.repo, "commit", "--allow-empty", "-q", "-F", str(message))
        self.assertEqual(self.plan()["version"], "1.1.0")

    def test_not_a_repo_fails(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(version.VersionError):
                version.plan(Path(empty), "v", "commits", None)


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        git(self.repo, "-c", "init.defaultBranch=main", "init", "-q")
        git(self.repo, "commit", "--allow-empty", "-q", "-m", "feat: first")

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

    def test_cli_prints_outputs(self):
        result = self.cli("--prefix", "app-v")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ["mode=release", "version=0.1.0", "tag=app-v0.1.0"],
        )

    def test_cli_deploy_false_publishes(self):
        result = self.cli("--prefix", "v", "--deploy", "false")
        self.assertEqual(result.stdout.splitlines()[0], "mode=publish")

    def test_cli_caller_source(self):
        result = self.cli(
            "--prefix", "v", "--version-source", "caller", "--version", "3.1.4"
        )
        self.assertEqual(
            result.stdout.splitlines(),
            ["mode=release", "version=3.1.4", "tag=v3.1.4"],
        )
        result = self.cli("--prefix", "v", "--version-source", "caller", "--version", "")
        self.assertEqual(
            result.stdout.splitlines(), ["mode=none", "version=", "tag="]
        )

    def test_cli_errors_exit_1_with_one_line(self):
        result = self.cli(
            "--prefix", "v", "--version-source", "caller", "--version", "v1.0.0"
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("wacd version:"))
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)

    def test_cli_published_false_on_a_tagged_head_releases(self):
        git(self.repo, "tag", "v0.1.0")
        result = self.cli("--prefix", "v", "--published", "false")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ["mode=release", "version=0.1.0", "tag=v0.1.0"],
        )
        result = self.cli("--prefix", "v", "--published", "true", "--deploy", "false")
        self.assertEqual(result.stdout.splitlines()[0], "mode=none")
        result = self.cli("--prefix", "v", "--published", "maybe")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_cli_rejects_bad_deploy_value(self):
        result = self.cli("--prefix", "v", "--deploy", "yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
