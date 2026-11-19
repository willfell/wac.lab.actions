"""wac-ci's probe of the private npm packages, against a stub npm on PATH.

The probe reads each lockfile's `@willfell` entries, refuses one it cannot
name a version for before npm runs at all, and asks the registry for each
locked version through the `.npmrc` beside that lockfile, failing on the
first the registry does not confirm.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "wacd-npm-probe.py"
TOKEN = "npm-token-that-must-never-be-printed"
NPMRC = "@willfell:registry=https://npm.pkg.github.com\n//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}\n"

STUB_NPM = """#!/bin/sh
printf '%s|%s|%s\\n' "$PWD" "$*" "${NODE_AUTH_TOKEN:-}" >> "$NPM_LOG"
case " $NPM_PUBLISHED " in
  *" $2 "*) [ -n "$NPM_SILENT" ] || printf '%s\\n' "${2##*@}"; exit 0 ;;
esac
if [ -n "$NPM_SILENT" ]; then exit 0; fi
echo "npm error 404 No match found for version ${2##*@}" >&2
exit 1
"""


def lock(packages):
    return {"name": "app", "lockfileVersion": 3, "requires": True, "packages": {"": {"name": "app"}, **packages}}


def registry(version):
    return {"version": version, "resolved": f"https://npm.pkg.github.com/download/x/{version}/abc"}


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.repo = tmp / "repo"
        self.repo.mkdir()
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        npm = bin_dir / "npm"
        npm.write_text(STUB_NPM)
        npm.chmod(0o755)
        self.log = tmp / "npm.log"
        self.env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "NPM_LOG": str(self.log),
            "NODE_AUTH_TOKEN": TOKEN,
            "NPM_PUBLISHED": "",
        }

    def write(self, relative, content):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))

    def probe(self, *lockfiles, published=(), silent=False):
        env = {**self.env, "NPM_PUBLISHED": " ".join(published)}
        if silent:
            env["NPM_SILENT"] = "1"
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), str(self.repo), *lockfiles],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        return result

    def calls(self):
        if not self.log.exists():
            return []
        rows = []
        for line in self.log.read_text().splitlines():
            cwd, args, token = line.split("|")
            rows.append((Path(cwd).resolve().relative_to(self.repo.resolve()).as_posix(), args, token == TOKEN))
        return rows

    def test_probes_every_locked_private_package_from_its_lockfiles_directory(self):
        self.write("package-lock.json", lock({
            "node_modules/@willfell/wac-bridge": registry("0.4.2"),
            "node_modules/next": registry("16.0.0"),
            "node_modules/next/node_modules/@willfell/wac-bridge": registry("0.4.1"),
        }))
        self.write(".npmrc", NPMRC)
        self.write("app/package-lock.json", lock({
            "node_modules/@willfell/wac-ui": registry("0.24.0"),
            "node_modules/@willfell/wac-bridge": registry("0.5.0"),
            "node_modules/@willfell/wac-bridge/node_modules/undici": registry("7.29.0"),
            "node_modules/@willfell/kit": {"resolved": "packages/kit", "link": True},
        }))
        self.write("app/.npmrc", NPMRC)
        self.write("sync-agent/package-lock.json", lock({"node_modules/exceljs": registry("4.4.0")}))
        result = self.probe(
            "app/package-lock.json",
            "package-lock.json",
            "sync-agent/package-lock.json",
            published=("@willfell/wac-bridge@0.4.1", "@willfell/wac-bridge@0.4.2",
                       "@willfell/wac-bridge@0.5.0", "@willfell/wac-ui@0.24.0"),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        tail = "version --prefer-online --userconfig .npmrc"
        self.assertEqual(self.calls(), [
            ("app", f"view @willfell/wac-bridge@0.5.0 {tail}", True),
            ("app", f"view @willfell/wac-ui@0.24.0 {tail}", True),
            (".", f"view @willfell/wac-bridge@0.4.1 {tail}", True),
            (".", f"view @willfell/wac-bridge@0.4.2 {tail}", True),
        ])
        self.assertIn("@willfell/kit", result.stdout)
        self.assertIn("packages/kit", result.stdout)

    def test_refuses_a_lockfile_it_cannot_read_before_npm_runs(self):
        cases = {
            "no version": (lock({"node_modules/@willfell/wac-ui": {"resolved": "x"}}), True,
                           "node_modules/@willfell/wac-ui"),
            "an empty version": (lock({"node_modules/@willfell/wac-ui": registry("")}), True,
                                 "node_modules/@willfell/wac-ui"),
            "a range for a version": (lock({"node_modules/@willfell/wac-ui": registry("^0.24.0")}), True,
                                      "node_modules/@willfell/wac-ui"),
            "an unusable package name": (lock({"node_modules/@willfell/Wac UI": registry("1.0.0")}), True,
                                         "@willfell/Wac UI"),
            "no .npmrc beside it": (lock({"node_modules/@willfell/wac-ui": registry("0.24.0")}), False,
                                    "bad/.npmrc"),
            "no packages map": ({"lockfileVersion": 1, "dependencies": {}}, True, "packages"),
            "text that is not JSON": ("{", True, "JSON"),
        }
        for label, (content, npmrc, named) in cases.items():
            with self.subTest(case=label):
                for path in self.repo.rglob("*"):
                    if path.is_file():
                        path.unlink()
                self.log.unlink(missing_ok=True)
                self.write("good/package-lock.json", lock({"node_modules/@willfell/wac-ui": registry("0.24.0")}))
                self.write("good/.npmrc", NPMRC)
                self.write("bad/package-lock.json", content)
                if npmrc:
                    self.write("bad/.npmrc", NPMRC)
                result = self.probe("good/package-lock.json", "bad/package-lock.json",
                                    published=("@willfell/wac-ui@0.24.0", "@willfell/wac-ui@"))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("::error::", result.stdout)
                self.assertIn("bad/package-lock.json", result.stdout)
                self.assertIn(named, result.stdout)
                self.assertEqual(self.calls(), [])

    def test_fails_on_the_first_version_the_registry_does_not_confirm(self):
        self.write("package-lock.json", lock({
            "node_modules/@willfell/wac-bridge": registry("0.9.9"),
            "node_modules/@willfell/wac-ui": registry("0.24.0"),
        }))
        self.write(".npmrc", NPMRC)
        for label, silent in (("npm reports no match", False), ("npm exits 0 and prints nothing", True)):
            with self.subTest(case=label):
                self.log.unlink(missing_ok=True)
                result = self.probe("package-lock.json", published=("@willfell/wac-ui@0.24.0",), silent=silent)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("::error::", result.stdout)
                self.assertIn("@willfell/wac-bridge@0.9.9", result.stdout)
                self.assertEqual([args for _, args, _ in self.calls()],
                                 ["view @willfell/wac-bridge@0.9.9 version --prefer-online --userconfig .npmrc"])

    def test_says_in_one_line_when_there_is_nothing_to_probe(self):
        self.write("package-lock.json", lock({"node_modules/next": registry("16.0.0")}))
        for label, lockfiles in (("no lockfile", ()), ("no private package", ("package-lock.json",))):
            with self.subTest(case=label):
                result = self.probe(*lockfiles)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout)
                self.assertIn("nothing to probe", result.stdout)
                self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
