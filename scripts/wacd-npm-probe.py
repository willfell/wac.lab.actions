#!/usr/bin/env python3
"""Ask the registry for every private `@willfell` package a lockfile installs.

`wacd-npm-probe.py <repo> [<lockfile> ...]` takes the repo checkout and the
repo-relative lockfiles `wacd-profile.py node-layout` found. wac-ci's `checks`
runs it before any `npm ci`, with the `NODE_AUTH_TOKEN` that `npm ci` gets.

Every `node_modules/@willfell/<name>` entry of each lockfile, nested ones
included, is probed once per locked version with

    npm view "<name>@<version>" version --prefer-online --userconfig .npmrc

run in the lockfile's own directory, so the `.npmrc` that `npm ci` there reads
is both the project and the user config. This is the probe the apps carried in
their own `ci.commands`: the version comes from the lockfile and is checked
before npm runs, because `npm view <name>@` resolves the latest version and
exits 0. Every lockfile is read and checked first, so one with an entry that
has no exact version, a name npm would not publish, no `.npmrc` beside it, or
no `packages` map fails the step before any request is made. The probes then
run in order and stop at the first the registry does not confirm with the
locked version. A workspace link (`"link": true`) is installed from the
repository, not the registry, and is named and skipped.

Exit 0 when every probe passed or there was nothing to probe, 1 on a refusal
or a failed probe, 2 on a usage error. The token is passed to npm through the
environment and never printed.
"""

import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

PREFIX = "wacd npm probe"
SCOPE = "@willfell"
ENTRY = re.compile(r"(?:^|/)node_modules/(@willfell/[^/]+)$")
NAME = re.compile(r"@willfell/[a-z0-9][a-z0-9._-]{0,213}")
EXACT_VERSION = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?"
)


class Refusal(Exception):
    """A lockfile or probe problem, already worded for the run log."""


def escape(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def plan_lockfile(repo, relative):
    """Return (directory, [(name, version)], [(name, link target)]) for one lockfile."""
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or path.name != "package-lock.json":
        raise Refusal(f"{relative!r} is not a repo-relative package-lock.json")
    lockfile = repo / relative
    try:
        data = json.loads(lockfile.read_text())
    except OSError as err:
        raise Refusal(f"{relative}: cannot be read ({err.strerror})") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise Refusal(f"{relative}: is not JSON ({err})") from None
    packages = data.get("packages") if isinstance(data, dict) else None
    if not isinstance(packages, dict):
        raise Refusal(
            f"{relative}: has no packages map (lockfileVersion 1 or older); "
            "regenerate it with npm 7 or later so the probe can read its private packages"
        )
    probes, links = set(), []
    for key, entry in packages.items():
        match = ENTRY.search(key)
        if not match:
            continue
        name = match.group(1)
        if not NAME.fullmatch(name):
            raise Refusal(f"{relative}: {key!r} is not a package name npm publishes")
        if not isinstance(entry, dict):
            raise Refusal(f"{relative}: {key} is not an object")
        if entry.get("link") is True:
            links.append((name, str(entry.get("resolved", ""))))
            continue
        version = entry.get("version")
        if not isinstance(version, str) or not EXACT_VERSION.fullmatch(version):
            raise Refusal(
                f"{relative}: {key} has no exact version ({version!r}); "
                f"`npm view {name}@` would resolve the latest and pass, so it is refused"
            )
        probes.add((name, version))
    directory = path.parent
    if probes and not (repo / directory / ".npmrc").is_file():
        raise Refusal(
            f"{relative}: locks {SCOPE} packages but {(directory / '.npmrc').as_posix()} does not exist, "
            "so `npm ci` there has no registry or token for them"
        )
    return directory, sorted(probes), sorted(links)


def probe(repo, directory, name, version):
    shown = f"npm view \"{name}@{version}\" version --prefer-online --userconfig .npmrc"
    print(f"{PREFIX}: in {directory.as_posix()}: {shown}", flush=True)
    result = subprocess.run(
        ["npm", "view", f"{name}@{version}", "version", "--prefer-online", "--userconfig", ".npmrc"],
        cwd=repo / directory,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    printed = result.stdout.strip()
    if printed:
        print(printed, flush=True)
    if result.returncode != 0:
        raise Refusal(f"the registry did not confirm {name}@{version} (npm exited {result.returncode}, "
                      f"its error is above); {directory.as_posix()}/package-lock.json locks it, "
                      "so `npm ci` there would fail")
    if printed != version:
        raise Refusal(f"{name}@{version}: npm exited 0 but printed {printed!r}, not the locked version")


def main(argv):
    if len(argv) < 2:
        print("usage: wacd-npm-probe.py <repo> [<lockfile> ...]", file=sys.stderr)
        return 2
    repo = Path(argv[1])
    lockfiles = argv[2:]
    try:
        plans = [(relative, *plan_lockfile(repo, relative)) for relative in lockfiles]
        total = sum(len(probes) for _, _, probes, _ in plans)
        if total == 0:
            where = "no package-lock.json in the repository" if not lockfiles else f"no {SCOPE} package locked from the registry"
            print(f"{PREFIX}: {where}; nothing to probe")
            return 0
        for relative, directory, probes, links in plans:
            for name, target in links:
                print(f"{PREFIX}: {relative}: {name} links to {target!r} in the repository; not probed")
            for name, version in probes:
                probe(repo, directory, name, version)
    except Refusal as err:
        print(f"::error::{PREFIX}: {escape(str(err))}", flush=True)
        return 1
    print(f"{PREFIX}: {total} locked {SCOPE} version(s) are in the registry")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
