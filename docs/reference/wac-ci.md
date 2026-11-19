# wac-ci

The pull-request half of wacd: it runs the profile's checks, renders and
validates the deploy chart, and runs the fleet gates. It builds no image and
pushes nothing. The profile it reads is in [The wacd profile](wacd-profile.md).

```yaml
name: ci
on:
  pull_request:
permissions:
  contents: read
jobs:
  wac-ci:
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

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label for every job but `checks` when `build_runner` is set, in the caller's repo | empty: the caller's `CI_RUNNER_SMALL` |
| `build_runner` | `runs-on` label for `checks`, the job that installs, tests and builds | empty: `runner` |
| `parity_kustomize_dir` | a kustomize directory whose render must match the Helm render | empty: no parity check |

| Secret | Meaning |
| --- | --- |
| `npm_token` | optional token for private npm packages; empty uses the job's GitHub token |

It runs six jobs. `contract` runs first and decides where the others read
their settings (below); every other job needs it. `checks` sets up node or go for `ci.stack` and runs
`ci.commands` in order, each in its own log group. For `stack: node` it runs
`npm ci` first, but only when the repo has a root `package.json`; a repo
without one, such as finance, lists `npm ci --prefix <dir>` in `ci.commands`
itself. The npm cache is keyed on every `package-lock.json` in the repo.
Nothing implicit runs for `go`; setup-go takes `ci.go_version`, or reads the version from `ci.go_version_file` when the profile names one. When the profile lists `ci.apt_packages`,
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
its files runs on them as before, and a cut-over repository needs a Lab pool.
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

## The contract job

A repository that has been cut over to hemi has no `.github/wacd.json` and no
values file: its settings live in hemi. `contract` asks hemi for them on every
pull request, so a cut-over repository's checks run on hemi's copy, and it
refuses a pull request that would put the files back.

It is the only job in `wac-ci` that holds `id-token: write`, and it runs no
repository code. It follows the rules `wac-cd`'s token jobs follow: its
first step checks the runner's own `python3` (3.12 or later, with the modules
the scripts import), it runs no `actions/setup-python`, every action is pinned
by commit and every step is bounded. It checks out the default branch's head
(`refs/heads/` plus `github.event.repository.default_branch`) and the pull
request's merge commit, each at depth 1 with `filter: blob:none` and a sparse
`.github/wacd.json`, without persisted credentials. `scripts/wacd-contract.py
pr` reads both trees with `git ls-tree` and main's profile with
`git cat-file`, as data, and asks hemi with `GET /api/v1/contract` at
`http://hemi-app.hemi.svc.cluster.local:3000`, the constant the shadow job
uses, with a token of audience `wacwini:hemi` minted for each attempt. No
answer and a 5xx are asked three times, 5 and 10 seconds apart, each attempt
bounded at 15 seconds. When the source is `hemi` it uploads `.wacd-contract/`
(`contract.json`, `profile.json` and `values.json`, as `wac-cd`'s `plan`
writes them) as the artifact `wac-ci-contract`, kept for one day.

Every other job needs `contract` and runs under `if: ${{ !cancelled() }}`.
A plain dependent of a failed job reports `skipped`, which a ruleset counts
as passing, so `checks` and `render` first run a step that fails unless
`contract` succeeded. `lint`, `techdocs` and `agents-md` call nested
workflows, which take no step of ours: they wait for `contract` and then run
their own gates whatever it concluded, since none of them reads the settings.
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

| hemi's answer | files on main | files in the pull request | result |
| --- | --- | --- | --- |
| 200, source `hemi` | absent | present | fails: `<path> was added back; <app>'s settings live in hemi.` |
| 200, source `hemi` | present | present | hemi's settings; the summary says `<path> is still on main until hemi's clean-up pull request merges; hemi's settings were used.` |
| 200, source `hemi` | absent | absent | hemi's settings |
| 200, source `files` | absent | present | the pull request's own files: it is the restore pull request of a return to files |
| 200, source `files` | absent | absent | fails: wait for hemi's restore pull request and re-run after it merges |
| 200, source `files` | present | any | the pull request's own files, as before |
| anything but a readable 200 | present | any | the pull request's own files, as before; the summary names only hemi's status, such as `hemi answered 401; using the repository's files.` |
| 401 or 403 | absent | any | fails: `hemi refused this pull request's token (HTTP <status>); <repository>'s settings live in hemi, so the checks cannot run.` |
| no answer, a timeout or a 5xx | absent | any | fails: `hemi did not answer; re-run when it does.` |
| 404, 422 or any other status | absent | any | fails, naming the status |

A 200 that cannot be read, names another repository than the token's, or
does not derive a profile counts as "anything but a readable 200", and so does
a job that has no `id-token` grant to mint with. The restore pull request is
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
