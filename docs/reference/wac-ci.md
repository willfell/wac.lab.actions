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
```

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label for every job, in the caller's repo | empty: the caller's `CI_RUNNER_SMALL` |
| `parity_kustomize_dir` | a kustomize directory whose render must match the Helm render | empty: no parity check |

| Secret | Meaning |
| --- | --- |
| `npm_token` | optional token for private npm packages; empty uses the job's GitHub token |

It runs five jobs. `checks` sets up node or go for `ci.stack` and runs
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

`parity_kustomize_dir` exists for cutover only. When set, `render` also builds
that kustomize tree and `scripts/wacd-parity.py` compares the two renders
object by object, failing on any difference its normalization does not cover
(namespaces, object labels, image tags and digests, and port names are the
covered ones). The normalization is ported from lab's `tests/wac_app/render.py`
and is the one copy outside lab. The caller removes the input at cutover step
3, when `deploy/k8s` is deleted.
