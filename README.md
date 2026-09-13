# lab.actions

Composite GitHub Actions shared across the homelab's projects.

Public on purpose: composite actions are YAML wrappers and every credential is
passed in as an input, so there is nothing secret here — and a public repo
avoids the private-repo Access setting that otherwise has to be right in every
consuming repository.

| Action | Purpose |
| --- | --- |
| `lab-build` | Compute semver, build, push an immutable tag, move a mutable environment pointer, cut a release |
| `lab-deploy` | Point an Argo CD Application at a new release and wait for it to converge |
| `lab-tofu-plan` | Plan an OpenTofu stack on a pull request, guard it, and comment the result |
| `lab-tofu-apply` | Guard and apply the reviewed plan file on merge |
| `lab-tofu-validate` | Check formatting and validate every OpenTofu root under a directory, no credentials needed |
| `lab-tools` | Install the fleet's k8s and registry tooling, arch-aware, onto `PATH` |
| `lab-gitops-deploy` | Build, push, pin via a kustomize commit-back, sync Argo, verify the served build |
| `lab-kubeconform` | Validate a kustomize overlay by piping its build through kubeconform |

Consume at an **exact patch-level tag** — never at `main`, and never at a floating
major:

```yaml
- uses: willfell/wac.lab.actions/lab-build@v1.2.0
```

`@main` lets an unrelated push here change how a consuming repo builds and deploys.
A floating `@v1` is the same hole with a slower fuse: it moves without review, and
this repo's `v1` already went stale carrying a bootstrap that shells out to `gh`,
which is absent from the self-hosted runner image. Repointing a major tag depends on
a human remembering to, which is not a guarantee.

The cost of exact pins is that bumping a consumer is a deliberate edit. That is the
point: an unreviewed change to a composite cannot reach anyone's CI, and cannot
silently fail to reach it either.

All actions share one tag. A release touching only `lab-deploy` still moves
`lab-build`'s pin to the same ref, pointing at byte-identical code. Reusable
workflows (below) ride the same repo-wide exact tags.

`CONSUMERS.md` tracks every pinned component, repo, and file, and `CHANGELOG.md`
records which components each tag actually changed. Bumping a tag means reading
the changelog for the components that moved, then walking the consumers table for
the repos pinning those. A consumer whose components are untouched needs no PR;
one whose components moved and gets skipped runs old code indefinitely, with
nothing to say so.

## Releasing

Tags are created by hand. Nothing computes them, and merging a PR publishes nothing.

```sh
git tag vX.Y.Z && git push origin vX.Y.Z
```

Until the tag exists, a consumer pinned to it fails at job start-up with "unable to
resolve action" — before running a single step.

**Which number to move.** All components share one tag, so the version describes
the repo, not any one action:

- **patch** — a fix inside a component that does not change its inputs, outputs,
  or what a caller must pass.
- **minor** — a new component, a new optional input, or a behaviour change a
  caller could notice. A new *required* input is a minor here too: consumers pin
  exact tags, so nobody is broken until they choose to bump, and the changelog
  row is what warns them.
- **major** — reserved. Nothing has needed one; `v1` is deliberately not
  maintained as a floating pointer (see above), so a major bump would buy only
  the label.

Add the `CHANGELOG.md` row **in the PR that makes the change**, not at tag time.
A changelog written at tag time is written from memory, and the components column
is the part consumers rely on.

## The OpenTofu actions

All three set `tofu_wrapper: false` on `setup-opentofu`. The wrapper exists only
to expose a `tofu` call's stdout, stderr and exit code as step outputs, which
nothing here reads -- and it is a Node script, so on a runner image without node
every `tofu` invocation dies with `/usr/bin/env: 'node': No such file or
directory`. Disabling it removes the dependency rather than installing one.


`lab-tofu-plan` and `lab-tofu-apply` are one pipeline split across two triggers.
Plan runs on the pull request and comments what would change; apply runs on the
merge and applies **the plan file it just wrote**, not a fresh one. That
distinction is the whole reason the pair exists: `tofu apply -auto-approve`
re-plans at apply time, so a drift or a racing change lands without anyone having
reviewed it.

```yaml
- uses: willfell/wac.lab.actions/lab-tofu-plan@v1.3.0
  with:
    working_directory: infra/cloudflare
    role_arn: arn:aws:iam::634560051830:role/lab-cloudflare-plan
    github_token: ${{ secrets.GITHUB_TOKEN }}
```

### Selecting a workspace

Both actions take an optional `workspace` input, plumbed as `TF_WORKSPACE` into
every step that invokes `tofu` -- init, plan, and (on apply) apply itself. Empty,
the default, means the default workspace, so existing callers are unaffected. A
repo that selects a workspace per environment must set the same `workspace`
value on both its plan and apply callers: a workspace honored at plan time and
dropped at apply time would apply against the wrong state.

### Guards stay in the calling repo

Neither action ships a policy check. They take a `guard_command` instead, run
from the repository root with two variables set:

| Variable | Meaning |
| --- | --- |
| `TOFU_PLAN_JSON` | Path to the rendered plan JSON |
| `GUARD_OVERRIDE` | Non-empty when `override_label` was on the pull request |

```yaml
    guard_command: uv run lab tf guard --plan "$TOFU_PLAN_JSON" ${GUARD_OVERRIDE:+--allow-destroy}
    override_label: allow-access-destroy
```

This repo has no test suite by design, and a guard is the one piece of the
pipeline whose failure mode is an outage rather than a red check. Moving it here
would trade a tested function for an unverified shell fragment. So the guard
lives in the consumer, under that repo's own linters and tests, and these actions
only decide *when* to call it and *whether* an override applies.

`override_label` is resolved through the API on both sides -- from the pull
request on plan, from the merge commit's pull request on apply. Reading it from
the push event instead would mean a guard that a reviewer had to override could
be bypassed simply by merging.

A failing guard on plan still posts its comment before failing the job. On apply
there is nothing to read, so it fails immediately.

### Requirements of the calling job

```yaml
permissions:
  id-token: write
  contents: read
  pull-requests: write
```

Check out the repository and install whatever `guard_command` needs before
calling either action; neither does its own checkout or language setup.

The override-label step shells out to `gh`, which GitHub-hosted images carry and
`ghcr.io/actions/actions-runner` does not. Since v1.10.4 that step installs the
matching gh release itself when the binary is absent, so `override_label` works
on a self-hosted pool. Nothing is downloaded unless you pass `override_label`
AND gh is missing: the step is already gated on the input, and the install is
guarded on `command -v gh`.

## lab-tofu-validate

Checks OpenTofu formatting and validates every root under a directory, without
assuming any cloud role or reading any state. It installs OpenTofu itself, so a
caller drops its own setup step.

```yaml
- uses: willfell/wac.lab.actions/lab-tofu-validate@v1.7.0
  with:
    working_directory: infra
```

| Input | Meaning | Default |
| --- | --- | --- |
| `working_directory` | Directory searched for OpenTofu roots | `infra` |
| `tofu_version` | OpenTofu release to install | `1.11.2` |

### Root discovery

A root is any directory whose `*.tf` files declare an anchored `backend "`
block. Discovery walks `working_directory` for that pattern rather than
requiring a caller to list roots explicitly, so `terraform-global`'s
`backend.tf` layout and `egnyte-mcp`'s `versions.tf` layout are both found the
same way. Discovering zero roots is a loud failure, not a vacuous pass -- a
directory that quietly stopped matching the pattern would otherwise let this
check pass while validating nothing.

Each discovered root is initialized with `-backend=false`, so the check needs
no cloud credentials and can gate any pull request cheaply.

## lab-tools

Installs the fleet's k8s and registry tooling -- `kubectl`, `kustomize`, `crane`,
`kubeconform`, `helm` -- to `$RUNNER_TEMP/bin` and appends it to `GITHUB_PATH`, so
every subsequent step in the job finds them on `PATH`. It maps `uname -m` to each
tool's release-asset arch naming, so the same call works unmodified on
GitHub-hosted amd64 runners and this homelab's arm64 self-hosted runners.

```yaml
- uses: willfell/wac.lab.actions/lab-tools@v1.4.0
  with:
    tools: kubectl,kustomize,crane,kubeconform
```

| Input | Meaning | Default |
| --- | --- | --- |
| `tools` | Comma-separated subset of `kubectl,kustomize,crane,kubeconform,helm` | required |
| `kubectl_version` | kubectl release installed when `kubectl` is requested | `v1.35.0` |
| `kustomize_version` | kustomize release installed when `kustomize` is requested | `v5.7.1` |
| `crane_version` | go-containerregistry release installed when `crane` is requested | `v0.20.6` |
| `kubeconform_version` | kubeconform release installed when `kubeconform` is requested | `v0.7.0` |
| `helm_version` | helm release installed when `helm` is requested; empty installs the latest | `""` |

This repo's CI runs the smoke matrix on `ubuntu-latest` (amd64) and
`ubuntu-24.04-arm` (arm64) on every push and pull request, installing all five
tools and executing each one, so the arch mapping above is enforced by a real
job rather than trusted.

## lab-gitops-deploy

Runs the whole build-to-served pipeline as one composite: build the image,
push it to the in-cluster registry, pin it by committing a kustomize edit
back to `main` over a deploy key, force an Argo CD sync onto that commit, and
verify the endpoint now serving traffic is running the source sha that was
just built. This canonicalizes the ~150-line version of this job that
finance, flight-checker, and wac each carried and drifted independently.

```yaml
- uses: willfell/wac.lab.actions/lab-gitops-deploy@v1.5.0
  with:
    image: finance-app
    argo_app: finance
    namespace: finance
    deploy_ssh_key: ${{ secrets.DEPLOY_SSH_KEY }}
    sha_build_arg: FINANCE_BUILD_SHA
    build_secret: ${{ secrets.GITHUB_TOKEN }}
    health_url: http://finance-app.finance.svc.cluster.local:3000/api/health
```

| Input | Meaning | Default |
| --- | --- | --- |
| `image` | Image name, also the default deployment name | required |
| `extra_images` | Space-separated additional image names pinned to the same sha inside the same deploy commit; empty disables | `""` |
| `argo_app` | Argo CD Application to sync and wait on | required |
| `namespace` | Namespace holding the deployment | required |
| `deploy_ssh_key` | Write-scoped deploy key authorizing the commit-back push | required |
| `deployment` | Deployment name when it differs from `image` | `""` |
| `dockerfile` | Dockerfile path | `deploy/Dockerfile` |
| `build_context` | Docker build context | `./` |
| `sha_build_arg` | Build-arg name that receives the source sha; empty disables | `""` |
| `build_secret` | Value exposed to the build as the `node_auth_token` docker secret; empty disables | `""` |
| `kustomize_dir` | Directory holding the kustomization the pin edits | `deploy/k8s` |
| `push_registry` | In-cluster registry address runners push to | `registry.network.svc.cluster.local:5000` |
| `pull_registry` | Registry address the node's docker daemon pulls from | `localhost:30500` |
| `health_url` | In-cluster health endpoint returning `{sha,...}`; empty skips verification | `""` |
| `health_expect_db_ok` | Also assert the health payload reports `db == ok` | `"true"` |
| `wait_timeout` | Seconds to wait on each Argo condition | `"300"` |
| `require_hook` | Hook type (e.g. `PreSync`) that must have run inside the sync operation; empty skips the check | `""` |
| `kubectl_version` / `kustomize_version` / `crane_version` | Tool releases for the deploy and pin steps | `v1.35.0` / `v5.7.1` / `v0.20.6` |

Output: `bump_sha`, the `main`-branch commit carrying the image pin.

### Why the sync wait asserts the operation, not the end state

An Argo `Application` with `syncPolicy.automated.selfHeal` can start its own
sync between the pin landing on `main` and this action's patch taking effect.
That automated sync reconciles only the drifted `Deployment` and runs **no
hooks**, so a `PreSync` migration Job never executes -- yet it still leaves the
Application at the pinned revision, `Synced`, and `Healthy`. A wait built on
those three flags passes, the rollout succeeds, and a `/api/health` probe that
only does `select 1` confirms the new image is serving. The result is a fully
green deploy against an unmigrated schema. This happened three times in
production before the wait was written to catch it.

So the wait does not ask "is the app synced and healthy at my revision". It
asks "did the operation *I* requested run to completion", by polling
`.status.operationState` until it shows a `Succeeded` operation whose
`initiatedBy.username` is `ci` and whose `sync.revision` is the pin. If the
operation slot is free and the recorded operation is not ours -- an automated
sync displaced the request -- it re-requests rather than waiting on an
operation that will never be ours.

Two details make that assertion trustworthy:

**The read is atomic.** Every field the decision depends on comes out of one
`kubectl get`. Reading them with separate calls lets a `Succeeded` phase left
over from the *previous* operation pair with the revision of the one just
requested -- a combination that never existed in any single state of the
resource. That torn read is not a rare race: it reproduced on four consecutive
deploys, each passing the wait ~5 seconds after the patch, before the migration
Job could possibly have run.

**A stale success cannot satisfy it.** The wait records the operation identity
before patching and requires the observed one to differ, so the previous
deploy's `Succeeded` is never mistaken for this one's.

**The request replaces the operation, it does not merge into one.** The sync is
requested with a JSON Patch `add` on `/operation`, which replaces the whole
object, and only when the slot looks free, so a sync that is genuinely
mid-flight is not displaced.

**The hook, not the initiator, is what makes the result trustworthy.** Argo's
own auto-sync can stamp `automated: true` onto the very operation carrying this
action's username, producing `initiatedBy: {automated: true, username: ci}`. No
client-side patch prevents that -- the stamping happens on Argo's side, after
the write. Fused operations usually do run hooks, so rejecting them outright
only makes deploys unachievable on an app whose sync slot is contended. What
the wait requires instead is that the completed operation carries the pinned
revision, is not the one observed before patching, and -- when `require_hook`
is set -- ran that hook to `Succeeded`. All three production incidents were
syncs with no hook at all, which that catches regardless of who is recorded as
having started them.

Set `require_hook: PreSync` on any app whose manifests carry a migration Job.
It makes the deploy fail when an operation converges without running the hook,
which is the difference between a schema that migrated and one that did not.

The test is that the hook appears in the completed operation's `syncResult`,
not that it reached `Succeeded` there. The syncs this wait exists to reject
carry no entry for the hook at all -- their `syncResult` is the drifted
workloads and nothing else. An operation can also finish while the hook it
created is still `Running`, and that is a sync that did include the migration,
so only an outright hook failure is treated as fatal.

### Apps whose migrations are not hooks

Leaving `require_hook` empty is the right setting for an app with no Argo hook,
but it does not mean there is nothing to verify -- only that this action cannot
be the thing that verifies it. The wait asserts that a hook Argo was supposed
to run actually ran. Where the migration is not an Argo-visible resource, that
assertion has no referent.

An app that migrates in-process -- finance runs drizzle's `migrate()` inside
`getDb()` on the first request -- has no Job for a hookless sync to skip: if the
pod is serving, it has already migrated, because migrating is the precondition
of serving. What can still go wrong there is a migration that fails or is never
reached, and the check for that is the journal itself, not the sync operation:

```sh
kubectl -n <ns> exec deploy/<db> -- psql -U postgres -d <database> \
  -tAc "select count(*) from <schema>.__drizzle_migrations"
```

compared against the number of migration files the deployed commit carries.
Travel and flight-checker take the hook-shaped check because their migrations
are `PreSync` Jobs; finance takes the journal-shaped one. Picking the wrong
shape for an app reads as a passing check while proving nothing.

### The registry split

`registry.network` collides with a real public TLD (`.network`), so the
node's Docker daemon resolves it as a public FQDN rather than the in-cluster
Service -- pulls were silently going out to a stranger's server while
pushes from in-cluster runners kept working, because pods resolve the name
fine through the kubelet's DNS search path and the daemon doesn't. The fix
is to stop pretending it's one address: `push_registry` is the explicit,
fully-qualified in-cluster DNS name a push can never mistake for anything
public, and `pull_registry` is `localhost:30500`, the registry Service's
NodePort as the node's daemon sees it -- Docker treats `localhost`
registries as insecure/HTTP automatically, and `localhost` can't collide
with a public domain. Same registry, same blobs, two routes into it. Do
not collapse them back into one variable; that collapse is the outage this
section is describing.

### Why crane, not `docker push`

dockerd refuses plain HTTP to any registry address it doesn't consider
`localhost`, and the push address above deliberately isn't one.
Reconfiguring ARC's injected dind sidecar to trust it would work, but it
couples this pipeline to that chart's internals for every consumer. Saving
the image and pushing the tarball with `crane push --insecure` sidesteps
dockerd's registry trust entirely.

### The deploy key

`main` is protected by a ruleset requiring a PR and a passing check. The
pin commit is `deploy: <sha> [skip ci]` by design, so it can never produce
a passing check to satisfy that rule, and GitHub Actions cannot be granted
a ruleset bypass on a user-owned repository (only an org-owned one). The
push is therefore made with a write-scoped deploy key, and the ruleset
grants that key's `DeployKey` principal the bypass instead. Deploy keys
are SSH-only, which is why this step rewrites the push remote to
`git@github.com` rather than reusing the checkout's HTTPS token.

### `[skip ci]`, not paths-ignore

`[skip ci]` stops the pin commit from retriggering this workflow. A
`paths-ignore` filter on the kustomize directory would do the same thing
for this commit, but it would also suppress the workflow for a hand-edited
manifest in that directory -- and a hand edit is exactly the kind of change
that SHOULD deploy. Argo watches git directly, not this workflow, so
either way the sync happens; only the `[skip ci]` approach keeps Actions
correctly silent on its own commit while staying alert to everyone else's.

### What the verify step checks

The final step asserts that the endpoint is serving the *source* sha --
the commit this job built from -- never the bump commit that carries the
pin. Checking the bump sha instead would make the assertion vacuous: it's
always true the moment the pin lands, whether or not the workload actually
picked it up. Re-running this composite against an already-current sha is
a no-op at the pin step (nothing to commit) and falls straight through to
this same verification, rather than treating "nothing changed" as a
failure.

### The onboarded guard

A repo whose Argo Application doesn't exist yet -- newly composited but
not yet wired into Argo CD -- stops cleanly after the image push instead
of failing the job. Every sync, wait, rollout, and verify step is
conditioned on that onboarded check, so bringing a new consumer onto this
composite doesn't require Argo to be ready on day one.

### The pin retry loop

Each of the five pin attempts re-derives from `origin/main`: fetch, branch
from the fresh tip, edit, commit, push. A concurrent pin from another job
landing between attempts is not a conflict to rebase through -- the next
attempt just starts over from wherever `main` now points, so it can never
push a commit based on a base that's gone stale underneath it.

## lab-kubeconform

Pipes a kustomize overlay's rendered manifests through kubeconform.
Replaces three hand-rolled versions of this same two-command pipeline that
had drifted onto different tool versions and, on the arm64 consumers, hard-
coded release-asset URLs for that one architecture.

```yaml
- uses: willfell/wac.lab.actions/lab-kubeconform@v1.5.0
  with:
    kustomize_dir: deploy/k8s
```

| Input | Meaning | Default |
| --- | --- | --- |
| `kustomize_dir` | Directory holding the kustomization to validate | required |
| `flags` | Flags passed to kubeconform | `-strict -summary` |
| `kustomize_version` | kustomize release to install | `v5.7.1` |
| `kubeconform_version` | kubeconform release to install | `v0.7.0` |

## Reusable workflows

Reusable workflows ride the same repo-wide exact tags as the composites above --
consume at an exact patch-level tag, never `@main` or a floating major.

**Every one of them takes a `runner` input, and every one of them defaults it to
`ubuntu-latest`.** A reusable workflow's jobs run on the *caller's* runners,
billed to the caller and queued against the caller's pools -- but the label is
written here, in this repo. So a hardcoded `runs-on` put a GitHub-hosted job into
every consumer's pipeline, including repos that had otherwise moved onto
self-hosted pools, and it was invisible from the caller's side. Pass `runner`
with your own pool's label to keep the job on your infrastructure:

```yaml
jobs:
  lint:
    uses: willfell/wac.lab.actions/.github/workflows/actionlint.yml@v1.10.2
    with:
      runner: lab
```

The default is deliberate and load-bearing: a caller that passes nothing behaves
exactly as it did before the input existed, so bumping the tag is not a runner
change. `runs-on` receives the value as a single label, so `runner` takes one
label (`ubuntu-latest`, `lab`, `wac`) rather than a list -- a multi-label
self-hosted set like `[self-hosted, macOS, ARM64]` cannot be expressed through
it. `.github/scripts/check_actions.py` fails CI if any reusable workflow here
hardcodes `runs-on`, omits the input, or changes its default.

### actionlint

Runs [`raven-actions/actionlint`](https://github.com/raven-actions/actionlint)
against the caller's own workflows. It provisions the action's own dependencies
first, because a self-hosted runner image does not have to carry them and
`ghcr.io/actions/actions-runner` carries none of them: `node`/`npm` for the
tool-download step, `pipx` for pyflakes, and `shellcheck` for linting `run:`
blocks. Each was a separate red job on the first ARC pool to call this workflow.
The apt step is skipped when both tools are already present, so a
GitHub-hosted caller pays nothing.

```yaml
jobs:
  lint:
    uses: willfell/wac.lab.actions/.github/workflows/actionlint.yml@v1.10.2
    permissions:
      contents: read
```

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label the job is sent to, in the caller's repo | `ubuntu-latest` |

Passing a `runner` other than `ubuntu-latest` also **enables the runner
policy**: a step that scans the caller's own `.github/workflows/` and fails on
any `runs-on` resolving to a GitHub-hosted label (`ubuntu-*`, `macos-*`,
`windows-*`), including through a `matrix` whose values are visible in the file.

Running your lint on a self-hosted pool is the sovereignty claim, so the claim
is what arms the check -- a repo that deliberately stays GitHub-hosted passes no
`runner`, gets the default, and is never scanned. That is deliberate: several
repos outside the homelab fleet call this workflow and would fail the scan.

Expressions carry no literal label, so a break-glass
`runs-on: ${{ inputs.runner || 'lab' }}` passes. Self-hosted qualifier labels
like `[self-hosted, macOS, ARM64]` also pass -- `macOS` is not `macos-*`.

### nextjs-site-deploy

Builds a static-exported Next.js site and ships it: install (with the yarn
dependency cache restored via `setup-node`), restore cached optimized images
from S3, validate and re-optimize images, build, run an optional postbuild
step, verify the `out` export exists, sync images with an immutable cache
header, sync the rest of the app files, and invalidate CloudFront.
Canonicalizes the deploy.yml the site fleet's repos had copy-pasted and
drifted -- one step and one invalidation path apart.

```yaml
jobs:
  deploy:
    uses: willfell/wac.lab.actions/.github/workflows/nextjs-site-deploy.yml@v1.10.0
    permissions:
      id-token: write
      contents: read
    with:
      role_arn: arn:aws:iam::111111111111:role/site-deploy
      aws_region: us-east-1
    secrets:
      s3_bucket: ${{ secrets.S3_BUCKET_NAME }}
      cloudfront_distribution_id: ${{ secrets.CLOUDFRONT_DISTRIBUTION_ID }}
```

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label the job is sent to, in the caller's repo | `ubuntu-latest` |
| `app_dir` | Directory holding the Next.js app | `app` |
| `node_version` | Node release installed before `yarn install` | `22` |
| `role_arn` | OIDC role assumed for the AWS credentials used to deploy | required |
| `aws_region` | AWS region passed to `configure-aws-credentials` | required |
| `postbuild_command` | Shell command run after `yarn build`, skipped when empty | `""` |
| `build_env` | Newline-separated `KEY=value` pairs exported into the build step | `""` |
| `invalidation_paths` | Space-separated CloudFront invalidation path patterns | `/*.html /index.html /_next/* /sitemap*.xml` |
| `install_command` | Shell command that installs dependencies | `yarn install` |
| `app_cache_control` | `Cache-Control` header applied to the app-files S3 sync, skipped when empty | `""` |
| `bucket` | S3 bucket the export is synced to, as a plain string; takes precedence over the `s3_bucket` secret | `""` |

| Secret | Meaning |
| --- | --- |
| `s3_bucket` | S3 bucket the export is synced to; optional, but one of `bucket` or `s3_bucket` must be set |
| `cloudfront_distribution_id` | CloudFront distribution invalidated after sync |

Values passed through `secrets:` are masked in logs, which turns a bucket
name into `***` in `aws s3 sync` output even when the name is not sensitive.
Callers whose bucket name is not a secret should pass it via the `bucket`
input instead so deploy failures stay legible; `bucket`, when set, takes
precedence over `s3_bucket`. The workflow's first step fails immediately if
neither is supplied.

`node_version` defaults to `22`, retiring the fleet's node-18 debt; a caller
whose build breaks on 22 can override it to `"20"` and then `"18"` while it
migrates, rather than being blocked on the bump.

`postbuild_command` and `install_command` are commands by contract, the same
as any `run:` step -- both are deliberately interpolated into a shell step,
not passed through `env:`. Every other input above is data and rides `env:`
inside the workflow; treat `postbuild_command` and `install_command` as
untrusted-caller-writable code, not as data values.

### nextjs-site-check

Runs the same install (with the yarn dependency cache restored via
`setup-node`), image validation, and build a pull request needs to prove a
Next.js site still builds, without any of the deploy steps.

```yaml
jobs:
  check:
    uses: willfell/wac.lab.actions/.github/workflows/nextjs-site-check.yml@v1.10.0
```

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label the job is sent to, in the caller's repo | `ubuntu-latest` |
| `app_dir` | Directory holding the Next.js app | `app` |
| `node_version` | Node release installed before `yarn install` | `22` |

`node_version` defaults to `22` with the same fallback ladder (`20`, then
`18`) as `nextjs-site-deploy` -- a caller's green check on the default is the
acceptance test for the node-22 bump.

### techdocs

Runs the fleet TechDocs gate against the calling repo: the pinned strict
MkDocs build, and the nav-category check that keeps every repo's sidebar the
same shape on docs.wacwini.com.

```yaml
jobs:
  techdocs:
    uses: willfell/wac.lab.actions/.github/workflows/techdocs.yml@v1.12.0
    with:
      runner: lab
```

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label the jobs are sent to, in the caller's repo | `ubuntu-latest` |

The gate is two steps, and both have to pass:

```
uvx --from mkdocs-techdocs-core==1.6.1 --with mkdocs==1.6.0 \
  mkdocs build --strict --site-dir "$RUNNER_TEMP/site"
scripts/check-docs-nav.sh mkdocs.yml
```

Both pins are exact. `mkdocs-techdocs-core` and `mkdocs` move independently and
a floating pair has silently changed what `--strict` rejects before, so a docs
build that is green here has to stay green on the portal's own renderer.

`--strict` alone is not the gate. In MkDocs 1.6 a page in neither the nav nor
`exclude_docs` is reported at INFO, and `--strict` only escalates WARNINGs, so
an unfiled page builds green. The `validation:` block in each repo's
`mkdocs.yml` is what raises it to a warning; `check-docs-nav.sh` is what proves
the block is still there, along with the seven fixed category names, their
order, the flat `Overview: index.md` mapping, and the standing `exclude_docs`
list.

`scripts/check-docs-nav.sh` is vendored here rather than fetched from
`wac.plugins`, which is private: reaching into it at run time would need a token
in all thirteen consumers. The `gate` job checks this repo out a second time at
`github.job_workflow_sha`, so the script it runs is the one that shipped in the
tag the caller pinned -- not whatever is on `main`. That works without a token
only because this repo is public.

A repo with no `mkdocs.yml` is not a failure. The `detect` job probes for the
file and `gate` is skipped, so the workflow can be added to a repo's `ci.yml`
before its docs exist.

`.github/actionlint.yaml` carries a narrow ignore for
`github.job_workflow_sha`: it is a real context property that actionlint 1.7's
schema does not yet know. The same file has to enumerate this repo's
self-hosted labels, because actionlint only checks labels once a config file
exists.
