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
        kw.setdefault("main_ref", "main")
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

    def test_a_claim_overtaken_by_a_settings_release_on_older_code_says_the_commit_was_never_released(self):
        """c2's run claims v1.0.1 and fails in build; a settings release then tags v1.0.2 on c1, the running code.

        Re-running c2's run is not a replay: v1.0.2 does not contain c2, which has never been released.
        """
        c1 = self.commit("feat: running")
        self.tag("v1.0.0")
        self.commit("fix: claimed, build failed")
        self.tag("v1.0.1")
        self.tag("v1.0.2", c1)
        self.assertEqual(
            self.refusal(),
            "v1.0.1 was taken by a later release (v1.0.2) that does not contain this commit, which has not "
            "been released; push a new commit to release it.",
        )
        with self.assertRaises(version.ReplayRefused):
            self.plan()
        self.commit("fix: next")
        self.assertEqual(self.plan(), {"mode": "release", "version": "1.0.3", "tag": "v1.0.3"})

    def test_a_higher_tag_off_main_does_not_make_main_s_newest_old(self):
        self.two_releases()
        git(self.repo, "checkout", "-q", "-b", "stray", "HEAD~1")
        self.commit("feat: stray")
        self.tag("v0.9.0")
        git(self.repo, "checkout", "-q", "main")
        self.assertEqual(
            self.plan(), {"mode": "resume", "version": "0.2.0", "tag": "v0.2.0"}
        )

    def test_a_higher_tag_on_main_makes_an_older_head_old(self):
        first, _ = self.two_releases()
        self.commit("feat: third")
        self.tag("v0.3.0")
        git(self.repo, "checkout", "-q", first)
        self.assertIn(
            "This run is for v0.1.0, but v0.3.0 is already published", self.refusal()
        )

    def old_untagged_commit(self):
        self.commit("feat: first")
        self.tag("v1.4.0")
        old = self.commit("fix: failed before it was tagged")
        self.commit("feat: next")
        self.tag("v1.5.0")
        git(self.repo, "checkout", "-q", old)

    def test_an_old_untagged_commit_behind_a_newer_tag_is_refused(self):
        self.old_untagged_commit()
        sentence = (
            "This commit is older than v1.5.0, which is already published and "
            "contains or replaces it. "
            "Roll back or release anew; an old build is never replayed."
        )
        self.assertEqual(self.refusal(), sentence)
        self.assertEqual(self.refusal(source="caller", caller="9.0.0"), sentence)
        self.assertEqual(self.plan(source="caller", caller="")["mode"], "none")

    def test_an_untagged_head_that_is_mains_tip_releases_above_the_newest(self):
        self.old_untagged_commit()
        git(self.repo, "checkout", "-q", "main")
        self.commit("fix: after 1.5.0")
        self.assertEqual(
            self.plan(), {"mode": "release", "version": "1.5.1", "tag": "v1.5.1"}
        )
        self.assertEqual(
            self.plan(source="caller", caller="2.0.0")["version"], "2.0.0"
        )

    def test_a_tag_on_main_that_a_tagged_head_does_not_contain_refuses_it(self):
        self.commit("feat: base")
        base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "-b", "side")
        self.commit("feat: side")
        self.tag("v0.1.5")
        git(self.repo, "checkout", "-q", "main")
        self.commit("feat: head")
        self.tag("v0.3.0")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        git(self.repo, "checkout", "-q", "main~1")
        self.assertEqual(
            self.refusal(),
            "This commit is older than v0.1.5, which is already published and "
            "contains or replaces it. "
            "Roll back or release anew; an old build is never replayed.",
        )

    def test_publish_only_runs_are_not_judged_for_a_new_version(self):
        self.old_untagged_commit()
        self.assertEqual(self.plan(deploy=False)["mode"], "publish")

    def test_a_missing_main_ref_fails_instead_of_judging_by_every_tag(self):
        self.two_releases()
        message = self.refusal(main_ref="origin/main")
        self.assertIn("origin/main is not in the checkout", message)
        self.assertEqual(
            self.plan(main_ref="origin/main", deploy=False)["mode"], "none"
        )

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


class SettingsTest(unittest.TestCase):
    """A settings release is the next patch above main's newest tag, tagged on the running code."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        git(self.repo, "-c", "init.defaultBranch=main", "init", "-q")
        self.old = self.commit("feat: first")
        git(self.repo, "tag", "v1.3.9")
        self.code = self.commit("fix: second")
        git(self.repo, "tag", "v1.4.2")

    def commit(self, message):
        git(self.repo, "commit", "--allow-empty", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def settings(self, commit, running, prefix="v"):
        return version.plan_settings(self.repo, prefix, commit, running, main_ref="main")

    def refusal(self, *args, **kw):
        with self.assertRaises(version.VersionError) as ctx:
            self.settings(*args, **kw)
        return str(ctx.exception)

    def test_the_next_patch_above_the_highest_tag_merged_into_main(self):
        git(self.repo, "checkout", "-q", "-b", "stray")
        self.commit("feat: never merged")
        git(self.repo, "tag", "v9.0.0")
        git(self.repo, "checkout", "-q", "main")
        self.commit("feat: merged, not released yet")
        self.assertEqual(
            self.settings(self.code, "1.4.2"),
            {"mode": "settings", "version": "1.4.3", "tag": "v1.4.3", "claimed": "", "above": "", "codes": ""},
        )

    def test_after_a_rollback_it_still_releases_above_the_newest(self):
        self.assertEqual(
            self.settings(self.old, "1.3.9"),
            {"mode": "settings", "version": "1.4.3", "tag": "v1.4.3", "claimed": "", "above": "1.4.2", "codes": "1.4.2"},
        )

    def test_above_names_every_version_on_main_newer_than_the_running_one_newest_first(self):
        git(self.repo, "tag", "v1.4.10", self.code)
        git(self.repo, "tag", "v1.4.3", self.code)
        git(self.repo, "tag", "ui-v9.0.0", self.code)
        git(self.repo, "checkout", "-q", "-b", "stray")
        self.commit("feat: never merged")
        git(self.repo, "tag", "v9.0.0")
        git(self.repo, "checkout", "-q", "main")
        self.assertEqual(self.settings(self.old, "1.3.9")["above"], "1.4.10,1.4.3,1.4.2")
        self.assertEqual(self.settings(self.code, "1.4.10")["above"], "")

    def test_a_version_already_claimed_on_the_running_code_is_named(self):
        git(self.repo, "tag", "v1.4.3", self.code)
        self.assertEqual(
            self.settings(self.code, "1.4.2"),
            {"mode": "settings", "version": "1.4.4", "tag": "v1.4.4", "claimed": "1.4.3", "above": "1.4.3", "codes": ""},
        )
        self.assertEqual(self.settings(self.code, "1.4.3")["claimed"], "", "a running version is not a pending claim")
        git(self.repo, "tag", "v1.4.0", self.old)
        self.assertEqual(self.settings(self.old, "1.3.9")["claimed"], "", "only main's newest tag can be a claim")

    def test_codes_names_the_versions_above_that_a_code_release_claimed(self):
        """A code release tags a commit no lower tag names; a settings release tags code an earlier version ran."""
        git(self.repo, "tag", "v1.4.3", self.code)
        git(self.repo, "tag", "-a", "-m", "settings", "v1.4.4", self.old)
        self.assertEqual(self.settings(self.code, "1.4.2")["codes"], "", "settings tags, lightweight or annotated")
        claimed = self.commit("fix: claimed, build failed")
        git(self.repo, "tag", "v1.4.5", claimed)
        git(self.repo, "tag", "ui-v0.1.0", claimed)
        planned = self.settings(self.code, "1.4.2")
        self.assertEqual((planned["above"], planned["codes"]), ("1.4.5,1.4.4,1.4.3", "1.4.5"))
        self.assertEqual(self.settings(self.old, "1.3.9")["codes"], "1.4.5,1.4.2",
                         "after a rollback the newer code releases are named too")
        self.assertEqual(self.settings(claimed, "1.4.5")["codes"], "", "only versions above the running one")

    def test_other_tag_families_do_not_count(self):
        git(self.repo, "tag", "ui-v5.0.0")
        self.assertEqual(self.settings(self.code, "1.4.2")["version"], "1.4.3")
        git(self.repo, "tag", "app-v0.2.0", self.code)
        self.assertEqual(self.settings(self.code, "0.2.0", prefix="app-v")["tag"], "app-v0.2.1")

    def test_refusals(self):
        git(self.repo, "checkout", "-q", "-b", "side", self.old)
        side = self.commit("fix: off main")
        git(self.repo, "checkout", "-q", "main")
        self.assertIn("is not on main", self.refusal(side, "1.4.2"))
        self.assertIn("is not a commit in this checkout", self.refusal("f" * 40, "1.4.2"))
        self.assertIn("40 lowercase hex", self.refusal("abc", "1.4.2"))
        for running in ("v1.4.2", "1.4", "bootstrap", ""):
            with self.subTest(running=running):
                self.assertIn("not a released X.Y.Z", self.refusal(self.code, running))
        self.assertIn("no zz-v", self.refusal(self.code, "1.4.2", prefix="zz-v"))
        git(self.repo, "tag", "v1.4.3", side)
        self.assertEqual(
            self.refusal(self.code, "1.4.2"), f"tag v1.4.3 already exists on {side}, not on {self.code}"
        )
        message = self.refusal(self.code, "1.4.2", prefix="v\n")
        self.assertIn("line break", message)

    def test_a_missing_main_ref_or_a_shallow_checkout_fails(self):
        with self.assertRaises(version.VersionError) as ctx:
            version.plan_settings(self.repo, "v", self.code, "1.4.2", main_ref="origin/main")
        self.assertIn("origin/main is not in the checkout", str(ctx.exception))
        clone = Path(self._tmp.name) / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{self.repo}", str(clone)], check=True)
        with self.assertRaises(version.VersionError) as ctx:
            version.plan_settings(clone, "v", self.code, "1.4.2", main_ref="origin/main")
        self.assertIn("shallow", str(ctx.exception))

    def test_a_settings_tag_on_the_head_leaves_it_with_two_tags_that_resume_the_newer(self):
        git(self.repo, "tag", self.settings(self.code, "1.4.2")["tag"], self.code)
        self.assertEqual(
            version.plan(self.repo, "v", "commits", None, main_ref="main"),
            {"mode": "resume", "version": "1.4.3", "tag": "v1.4.3"},
            "a re-run of the code release on that commit re-releases the current settings",
        )
        self.commit("fix: later")
        self.assertEqual(
            version.plan(self.repo, "v", "commits", None, main_ref="main"),
            {"mode": "release", "version": "1.4.4", "tag": "v1.4.4"},
            "the next merge releases above the settings version",
        )
        git(self.repo, "tag", "v1.4.4")
        git(self.repo, "checkout", "-q", self.code)
        with self.assertRaises(version.ReplayRefused) as ctx:
            version.plan(self.repo, "v", "commits", None, main_ref="main")
        self.assertIn("This run is for v1.4.3, but v1.4.4 is already published.", str(ctx.exception))

    def test_a_rerun_after_a_rollback_to_other_code_sees_the_earlier_release_above_it(self):
        git(self.repo, "tag", self.settings(self.code, "1.4.2")["tag"], self.code)
        rerun = self.settings(self.old, "1.3.9")
        self.assertEqual(rerun["above"], "1.4.3,1.4.2", "plan reads these charts for the revision before taking 1.4.4")
        self.assertEqual((rerun["version"], rerun["claimed"]), ("1.4.4", ""))

    def test_a_settings_tag_after_a_rollback_sits_on_old_code_and_the_next_merge_goes_above_it(self):
        git(self.repo, "tag", self.settings(self.old, "1.3.9")["tag"], self.old)
        self.commit("fix: next")
        self.assertEqual(
            version.plan(self.repo, "v", "commits", None, main_ref="main"),
            {"mode": "release", "version": "1.4.4", "tag": "v1.4.4"},
        )
        git(self.repo, "checkout", "-q", self.old)
        with self.assertRaises(version.ReplayRefused):
            version.plan(self.repo, "v", "commits", None, main_ref="main")


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        git(self.repo, "-c", "init.defaultBranch=main", "init", "-q")
        git(self.repo, "commit", "--allow-empty", "-q", "-m", "feat: first")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")

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

    def test_cli_exits_3_for_a_refused_replay_and_1_for_any_other_failure(self):
        git(self.repo, "tag", "v0.1.0")
        git(self.repo, "commit", "--allow-empty", "-q", "-m", "feat: second")
        git(self.repo, "tag", "v0.2.0")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.repo, "checkout", "-q", "v0.1.0")
        result = self.cli("--prefix", "v")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("This run is for v0.1.0, but v0.2.0 is already published.", result.stderr)
        git(self.repo, "checkout", "-q", "-")
        git(self.repo, "checkout", "-q", "--detach", "HEAD~1")
        git(self.repo, "commit", "--allow-empty", "-q", "-m", "fix: untagged")
        result = self.cli("--prefix", "v")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("This commit is older than v0.2.0", result.stderr)
        for args in (
            ["--main-ref", "refs/remotes/origin/absent"],
            ["--version-source", "caller", "--version", "v1.0.0"],
        ):
            with self.subTest(args=args):
                result = self.cli("--prefix", "v", *args)
                self.assertEqual(result.returncode, 1, result.stderr)

    def test_cli_settings_mode_prints_the_version_and_any_claim(self):
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "tag", "v1.3.9")
        git(self.repo, "tag", "v1.4.2")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        result = self.cli("--mode", "settings", "--prefix", "v", "--commit", head, "--running", "1.4.2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(), ["mode=settings", "version=1.4.3", "tag=v1.4.3", "claimed=", "above=", "codes="]
        )
        for args in (
            ["--commit", head, "--running", "v1.4.2"],
            ["--commit", head],
            ["--running", "1.4.2"],
        ):
            with self.subTest(args=args):
                result = self.cli("--mode", "settings", "--prefix", "v", *args)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.startswith("wacd version:"), result.stderr)
                self.assertEqual(len(result.stderr.strip().splitlines()), 1)

    def test_cli_rejects_bad_deploy_value(self):
        result = self.cli("--prefix", "v", "--deploy", "yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
