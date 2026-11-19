# wac-ci

The pull-request half of wacd: it runs the profile's checks, renders and
validates the deploy chart, and runs the fleet gates. It builds no image and
pushes nothing, so it takes no `build_secrets` (`wac-cd`'s build-time secret
mounts) and a pull request's `ci.commands` run without them. The profile it
reads is in [The wacd profile](wacd-profile.md).

```yaml
name: ci
on:
  pull_request:
permissions:
  contents: read
jobs:
  wacd-ci:
    uses: willfell/wac.lab.actions/.github/workflows/wac-ci.yml@vX.Y.Z
    permissions:
      contents: read
      packages: read
      id-token: write
```

The calling job grants `id-token: write` from `v1.35.0` on. A reusable
workflow cannot hold a permission its caller did not grant, and `contract`
asks for it, so a caller that bumps without the grant gets a run GitHub
refuses to start.

Callers' obligations from `v1.35.0`:

- The calling job grants `id-token: write`, as above.
- The calling job's id is `wacd-ci`, with no `name:`. It is load-bearing:
  the jobs show as `wacd-ci / contract`, `wacd-ci / checks` and the rest, and
  hemi and the organization ruleset read the required check
  `wacd-ci / contract` by that name. A renamed job leaves the required check
  unreported, and every merge waits for it.
- `contract` runs on `runner`, or the caller's `CI_RUNNER_SMALL`, which must
  provide `python3` 3.12 or later with the standard modules the scripts
  import; its first step checks that.
- A cut-over repository's `contract` must run on a Lab pool: it reaches hemi
  at its in-cluster address. A caller whose `CI_RUNNER_SMALL` is a hosted
  label (mcp.argo) passes a Lab pool as `runner` before it cuts over.

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label for every job but `checks` when `build_runner` is set, in the caller's repo; it must provide `python3` 3.12 or later for `contract` | empty: the caller's `CI_RUNNER_SMALL` |
| `build_runner` | `runs-on` label for `checks`, the job that installs, tests and builds | empty: `runner` |
| `parity_kustomize_dir` | a kustomize directory whose render must match the Helm render | empty: no parity check |

| Secret | Meaning |
| --- | --- |
| `npm_token` | optional token for private npm packages; empty uses the job's GitHub token |

It runs six jobs. `contract` runs first and decides where `checks` and
`render` read their settings (below); those two need it, while `lint`,
`techdocs` and `agents-md` read no settings and start at once. `checks` sets up node or go for `ci.stack` and runs
`ci.commands` in order, each in its own log group. For `stack: node` it runs
`npm ci` first, but only when the repo has a root `package.json`; a repo
without one, such as finance, lists `npm ci --prefix <dir>` in `ci.commands`
itself. The npm cache is keyed on every `package-lock.json` in the repo. Before
the root `npm ci` and `ci.commands`, it probes the private `@willfell`
packages every lockfile pins (below).
Nothing implicit runs for `go`; setup-go takes `ci.go_version`, or reads the version from `ci.go_version_file` when the profile names one (from a `go.mod`, its `toolchain` line before its `go` line). setup-go sets `GOTOOLCHAIN=local` for every later step, so the commands run on the Go it installed and never download another toolchain. A `ci.go_version` (hemi's `build.goVersion`) below the `go` line of the repo's `go.mod` therefore fails `checks`; before `v1.36.0` Go downloaded the newer toolchain and the run passed. The durable setting is `go_version_file: go.mod` (hemi's `build.goVersionFile`) with `go_version` dropped, since the profile refuses the two together, so the gate always runs the Go the module asks for. When the profile lists `ci.apt_packages`,
`checks` first runs `sudo apt-get update` and
`sudo apt-get install -y --no-install-recommends` with those packages, before
setup-node, the root `npm ci` and `ci.commands`; a profile without the field
installs nothing. This is for dependencies that compile natively on install:
finance lists `["python3", "make", "g++"]` because `better-sqlite3` builds
with node-gyp, which stops at `not found: make` on a runner image without
them. The list reaches the shell only as the validator's space-separated
`apt_packages` output, never as interpolated profile JSON, and the name
pattern admits no whitespace, quote or shell metacharacter. `wac-cd` installs
nothing: it builds images with `docker build`, so a native toolchain belongs
in the Dockerfile. It does validate the same profile, so a repo that sets
`ci.apt_packages` pins both workflows at a tag that knows the field. `render` pulls `wac-app` at `chart_version`,
packages the deploy chart with placeholder `ci` images, runs `helm lint` and
`helm template`, and validates the render with strict kubeconform against the
core and Datree CRD schemas. `lint` (`actionlint` with `runner_policy: fleet`),
`techdocs` and `agents-md` are the existing gates, called from the same commit.
It builds no image and pushes nothing.

`checks` is the only job that needs more than a small runner: it installs the
app's dependencies, runs its tests and builds it. `render`, `lint`, `techdocs`
and `agents-md` peak under 400Mi and run for seconds to a minute. A caller
that passes `build_runner: ${{ vars.CI_RUNNER_STANDARD }}` and no `runner`
sends `checks` there and leaves the other five jobs on its `CI_RUNNER_SMALL`;
`contract`, the sixth, reads two shallow trees and makes one request.
Passing only `runner` still puts every job on that runner, as before
`build_runner` existed. `render` pulls `wac-app` from the in-cluster registry, so this
split fits only a caller whose `CI_RUNNER_SMALL` is a Lab pool. A repository
whose small size is a hosted label (mcp.argo, in wac.lab.iac's `github` runner
mode) keeps passing `runner`. `contract` also reaches hemi at its
in-cluster address, so on a hosted runner it gets no answer: a repository on
its files runs on them as before (with the one exception below), and a
cut-over repository needs a Lab pool.
Six jobs per pull request on a three-pod dind pool is
how wac.hemi waited up to 17 minutes for a slot on 2026-11-18 while each job
ran for under four.

`parity_kustomize_dir` exists for cutover only. When set, `render` also builds
that kustomize tree and `scripts/wacd-parity.py` compares the two renders
object by object, failing on any difference its normalization does not cover
(namespaces, object labels, image tags and digests, and port names are the
covered ones). The normalization is ported from lab's `tests/wac_app/render.py`
and is the one copy outside lab. The caller removes the input at cutover step
3, when `deploy/k8s` is deleted.

## The private package probe

From `v1.36.0`, for `stack: node`, `checks` probes the fleet's private
`@willfell` packages after setup-node and before the root `npm ci` and
`ci.commands`, so a locked version missing from the registry fails with one
plain sentence instead of an install error midway through `npm ci`.
`scripts/wacd-npm-probe.py` reads every `package-lock.json` the node-layout
step found (finance's `app/package-lock.json` as much as hemi's root one) and
takes each `node_modules/@willfell/<name>` entry, nested ones included. For
each locked version it runs, in that lockfile's directory, with the
`NODE_AUTH_TOKEN` `npm ci` gets (`npm_token`, or the job's GitHub token):

```sh
npm view "<name>@<version>" version --prefer-online --userconfig .npmrc
```

That is the probe hemi carries in its own `checks` gate, with the version
taken from the lockfile and checked before npm runs, since
`npm view <name>@` resolves the latest version and exits 0. Every lockfile is
read first, and the step fails before any request when a lockfile cannot be
read or is not JSON, has no `packages` map (lockfileVersion 1), has a
`@willfell` entry whose name npm would not publish, whose entry is not an
object or that has no exact version, or locks `@willfell` packages with no
`.npmrc` beside it; a lockfile path that is not a repo-relative
`package-lock.json` is refused too, though the node-layout step never passes
one. The probes then run in
order and stop at the first the registry does not confirm: npm exits
non-zero, or prints anything but the locked version. A workspace link
(`"link": true`, as the wac repo's own `@willfell/wac-ui` is) is installed
from the repository and is named and skipped. A repo with no lockfile, or no
`@willfell` entry, gets one line saying there is nothing to probe. The token
reaches npm through the environment and is never printed; lockfile paths
reach the script as arguments, never through `${{ }}` in the shell; and the
script runs as `python3 -I` from the shared checkout and imports nothing from
the repository.

A repository that pins `v1.36.0` or later can drop its own probe commands,
the `npm view "@willfell/...` lines (pinned or read from the lockfile), since
`checks` now runs the same probe for every lockfile before them. On its files
they are entries of `ci.commands` in `.github/wacd.json`; cut over, they are
commands of hemi's `checks` gate, changed in hemi like any other setting. The
list must keep at least one command, and a repository whose own tests require
the probe commands (wac.hemi's `deploy-contract.test.ts` does) changes those
tests in the same pull request. Nothing breaks if the commands stay: each
then runs twice.

## The contract job

`contract` is built for `pull_request` runs. Hemi admits `wac-ci`'s token
only for a `pull_request` run on the merge ref, so on any other event a
cut-over repository fails `contract` with a sentence naming the event, and a
repository on its files runs on them as before.

A repository on its files is never failed because of hemi, except a pull request that deletes or moves `.github/wacd.json` or the values file while hemi does not answer, which fails until hemi answers, because it cannot be told from hemi's clean-up pull request or from a mistake. That holds
whatever hemi's answer was (no answer, a 401, 403 or 404, an unreadable 200)
and on any runner, a hosted one included; the table below has the row.

A repository that has been cut over to hemi has no `.github/wacd.json` and no
values file: its settings live in hemi. `contract` asks hemi for them on every
pull request, so a cut-over repository's checks run on hemi's copy, and it
refuses a pull request that would put the files back.

It is the only job in `wac-ci` that holds `id-token: write`, and it runs no
repository code. It follows the rules `wac-cd`'s token jobs follow: its
first step checks the runner's own `python3` (3.12 or later, with the modules
the scripts import), it runs no `actions/setup-python`, every action is pinned
by commit and every step is bounded. It sets `PYTHONSAFEPATH=1` for every
step and runs its inline Python as `python3 -I -c`, so no interpreter imports
from the working directory. It checks out the default branch's head
(`refs/heads/` plus `github.event.repository.default_branch`) and the pull
request's merge commit, each at depth 1 with `filter: blob:none` and a sparse
`.github/wacd.json`, without persisted credentials. `scripts/wacd-contract.py
pr` reads both trees with `git ls-tree` and main's profile with
`git cat-file`, as data, and asks hemi with `GET /api/v1/contract` at
`http://hemi-app.hemi.svc.cluster.local:3000`, the constant the shadow job
uses, with a token of audience `wacwini:hemi` minted for each attempt. No
answer, a 5xx and an address that does not resolve are asked three times, 5 and 10 seconds apart, each attempt
bounded at 15 seconds. When the source is `hemi` it uploads `.wacd-contract/`
(`contract.json`, `profile.json` and `values.json`, as `wac-cd`'s `plan`
writes them) as the artifact `wac-ci-contract`, kept for one day. A re-run of
failed jobs more than a day after the run finds it gone: `checks` or `render`
then fails at its download, and the next step says, in an `::error::`
annotation and the run summary, `The settings copy this run's earlier job uploaded could not be downloaded. It is kept for one day, so a re-run more than a day after the run started must re-run all jobs, not only the failed ones.`

`checks` and `render` need `contract` and run under
`if: ${{ !cancelled() }}`. A plain dependent of a failed job reports
`skipped`, which a ruleset counts as passing, so each first runs a step that
fails unless `contract` succeeded. `lint`, `techdocs` and `agents-md` read no
settings and do not wait for `contract`: they start with it and run their own
gates whatever it concludes. The required check `wacd-ci / contract` is what
blocks a merge while `contract` fails.
With the source `hemi`, `checks` and `render` remove any `.wacd-contract/`
the pull request carries, download the copy, and read the profile with
`wacd-profile.py --contract`: `checks` runs the copy's gates and runtime
setup, and `render` packages the chart from `.wacd-contract/profile.json`
with `--values-json .wacd-contract/values.json`. With the source `files`
every command is the one `wac-ci` ran before `v1.35.0`.

Whether a repository is cut over is read from main, never from the pull
request: otherwise a pull request could put the files back with gates of its
own, silence hemi (by dropping `id-token` from its own `ci.yml`, say) and run
its own gates green. "Files on main" means `.github/wacd.json` in main's
tree, the profile that holds the gates. The paths checked in the merge commit
are that file, the values path main's profile names and the copy's
`delivery.valuesFile`. Nothing looks at the base branch, so a pull request
into another pull request's branch is judged by main like any other.

`contract` writes `### wac-ci settings` to the run summary: a table with the
columns `source` and `revision` and one row (`hemi` and the revision, `files`
and the revision or `-`, or `-` and `-` when the job fails), then the sentences
the `result` column below quotes. Every result from
hemi's copy opens with `Settings come from hemi's copy, revision <n>.`, then
adds a sentence for each file still on main. A passing result's sentences
are in the run summary only, not the job log. A failing result's sentence is
also the script's stderr line `wacd contract: <sentence>`, which the step
turns into the `::error::` annotation `wac-ci: wacd contract: <sentence>`, so
it shows in the job log and on the run's page.

| hemi's answer | files on main | files in the pull request | result |
| --- | --- | --- | --- |
| 200, source `hemi` | absent | present | fails: `<path> was added back; <app>'s settings live in hemi.`, or, when the merge commit's first parent is not main's head, `This run's merge commit is older than <app>'s clean-up; update the branch and re-run.` |
| 200, source `hemi` | present | present, the same blob | hemi's settings; after `Settings come from hemi's copy, revision <n>.` the summary says, for each such file, `<path> is still on main until hemi's clean-up pull request merges; hemi's settings were used.` |
| 200, source `hemi` | present | absent: this pull request deletes the files | hemi's settings; the summary says only `Settings come from hemi's copy, revision <n>.`. This is hemi's clean-up pull request itself, and it passes |
| 200, source `hemi` | present | present, another blob | fails: `<path> was changed; <app>'s settings live in hemi, so change them there.` |
| 200, source `hemi` | absent | absent | hemi's settings; the summary says only `Settings come from hemi's copy, revision <n>.` |
| 200, source `files` | absent | present | the pull request's own files: it is the restore pull request of a return to files (`hemi's copy (revision <n>) says <app> is returning to its files and this pull request puts them back, so its own files were used.`), or a new app's first pull request (below) |
| 200, source `files` | absent | absent | fails: wait for hemi's restore pull request and re-run after it merges; for an answer with no revision, `hemi holds no settings for <app> yet, and neither main nor this pull request has .github/wacd.json, so there is nothing to check.` |
| 200, source `files` | present | any | the pull request's own files, as before; the summary says `hemi's copy (revision <n>) says <app>'s settings live in its files, so the pull request's own files were used.`, or, for an answer with no revision, `hemi holds no settings for <app> yet, so the pull request's own files were used.` |
| anything but a readable 200 | present | present | the pull request's own files, as before; the summary names only hemi's status, such as `hemi answered 401; using the repository's files.` |
| anything but a readable 200 | present | removes either file | fails, naming the cause, then `and this pull request removes <repository>'s files`: `hemi did not answer, ...; re-run when hemi answers.` for silence or a 5xx (hemi's clean-up pull request during a hemi outage), `hemi refused this pull request's token (HTTP <status>), ...; if <repository> is new, register it with lab first and re-run.` for a 401 or 403 (outside a `pull_request` run, `hemi refused the job token of this <event> run (HTTP <status>): it admits wac-ci only for pull_request runs, and this pull request removes <repository>'s files.`), `hemi answered HTTP <status>, ...; re-run once hemi serves this repository.` for another status, `hemi's answer could not be read, ...; re-run once hemi serves this repository.` for an unreadable answer, the Lab-runner sentence for an address that does not resolve, and likewise for a token GitHub would not mint or no `id-token` grant. |
| 403 with hemi's body `{"error":"not registered"}` (an app hemi does not know, or one with no active copy) | absent | any | fails: `hemi does not know <repository>: it is not registered with lab, or hemi holds no active copy of its settings. If it is new, register it (lab:lab-onboard-app), wait for hemi to list it, then re-run the checks.` |
| any other 401 or 403 | absent | any | fails: `hemi refused this pull request's token (HTTP <status>): if <repository> is new, register it with lab first and re-run; if it is cut over, its settings live in hemi and the checks cannot run until hemi admits this run.` (outside a `pull_request` run: `hemi refused the job token of this <event> run (HTTP <status>): it admits wac-ci only for pull_request runs; ` and the same clauses). Only a 403's body is read, at most 4096 bytes, and only to find that code; a longer body, or anything else, reads as this row |
| no answer (a connection closed or reset after the request, or an answer shorter than the `Content-Length` it declares), a timeout or a 5xx | absent | any | fails: `hemi did not answer; re-run when it does.` |
| hemi's address does not resolve, on all three attempts | absent | any | fails: ``the contract job must run on a Lab runner: <runner> cannot reach hemi's in-cluster address. Pass a Lab pool as `runner`.`` |
| GitHub mints no job token | absent | any | fails: `GitHub would not mint a job token for hemi; re-run the checks.` |
| 404, 422 or any other status | absent | any | fails, naming the status |

**A new app's first pull request.** This is the onboarding case, and the
order is Lab's registration first, then the app's first pull request, the one
that adds `.github/wacd.json`, its values file and the `wacd-ci` job. For a
registered repository that has never been cut over, hemi answers 200 with
exactly
`{"version": 1, "identity": {"repositoryId": "<id>", "repository": "<owner/name>", "app": "<app>"}, "flags": {"source": "files"}}`:
no revision and no settings. Main has neither file and the pull request adds
them, so it runs on its own files and passes, with the summary line
`hemi holds no settings for <app> yet, so the pull request's own files were used.`
Before the registration hemi answers 403 (`not registered`), and the pull
request fails with the 401-or-403 sentence above, which says to register
first.

A `files` answer is read for its source and its repository: `contract`
requires `flags.source` and an `identity.repositoryId` equal to the job
token's, which hemi sends in every `files` answer, the new app's included. A
`hemi` answer must carry the full copy. A 200 that cannot be
read, names another repository than the token's, or does not derive a profile
counts as "anything but a readable 200". So do an
address that does not resolve, a token GitHub would not mint, and a job with
no `id-token` grant to mint with; for a repository on its files the summary
then says which. An address that does not resolve is asked again, like
silence, since a lookup can fail once on a Lab runner; one that never
resolves means a runner outside the cluster, such as a hosted one, and the
sentence names the runner (`runner.name`, passed to the step as
`JOB_RUNNER_NAME`), when the name is letters, digits, `_`, `.`, `-` and inner
spaces (a hosted runner's `GitHub Actions <n>` included), up to 100
characters; any other name, one that could carry a workflow command or a shell
character, is shown as `this runner`.

Files on both main and the merge commit are compared by blob id
(`git ls-tree`), never by content read from the checkout: the same blob is the
clean-up window below, and another blob is an edit of settings that live in
hemi. A file the merge commit has and main lacks is added back, unless the
merge commit was made on an older main: GitHub's merge commit has the base
branch's head as its first parent, so when that parent is not main's head and
main no longer has the files, the run is a re-run of a merge commit made
before the clean-up merged, and the sentence says to update the branch. A
stacked pull request's merge commit has its base branch as first parent, so a
stacked pull request that carries the files gets that sentence too; it fails
either way.
A connection refused inside the cluster is hemi restarting, and counts as no
answer. The restore pull request is
not looked up: `contract` holds `contents: read` only, so its sentence says
to wait for that pull request rather than naming it.

Between a repository's cutover and the merge of hemi's clean-up pull request,
the one that deletes the files from main, main still has the files. In that
window a hemi outage makes pull requests run on the files rather than fail.
The release still ships hemi's copy, and the window lasts only as long as the
clean-up pull request is open.

A pull request that rewrites `ci.yml` wholesale can still skip every gate, as
it could before `v1.35.0`. Closing that is the rulesets project's work, not
this job's.
