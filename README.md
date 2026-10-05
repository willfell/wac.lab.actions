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
| `require_hook` | Retired since `v1.10.9`; accepted for compatibility and ignored, because migration proof is the app's schema-aware health route | `""` |
| `kubectl_version` / `kustomize_version` / `crane_version` | Tool releases for the deploy and pin steps | `v1.35.0` / `v5.7.1` / `v0.20.6` |

Output: `bump_sha`, the `main`-branch commit carrying the image pin.

### What the sync wait asserts, and what it does not

The sync step runs `scripts/argo-await-sync.sh`. It asks one question: is the
Application `Synced` at the pinned revision? Each poll reads
`.status.sync.status`, `.status.sync.revision` and `.operation.sync.revision`
in a single `kubectl get`, retrying a failed read up to three times. If the
Application is already `Synced` at the pin, the step is done. Otherwise, when
the operation slot is free, it requests a sync of the pin. That request is a
JSON Patch `add` on `/operation`, stamped `initiatedBy.username: ci`, and it
replaces the whole object instead of merging into one an automated sync has
just claimed. When the slot is occupied, it waits. It gives up after
`wait_timeout` seconds.

The steps after it finish the deploy. They wait for the Application to reach
the pin, `Synced` and `Healthy`, then for the Deployment rollout. When
`health_url` is set, they also check that the health route is serving the
source sha and, unless `health_expect_db_ok` is `"false"`, that it reports
`db == ok`. The verify step treats any non-2xx response as a failure.

The step does **not** try to prove that a migration hook ran. Up to `v1.10.8`
it did, by reading the operation's state out of
`.status.operationState`. That proof was abandoned because one week of
production deploys showed the record misreporting what happened in three
ways:

- A requested full sync was recorded as an automated, selective selfHeal
  operation.
- Hook phases stayed frozen at `Running` in the `syncResult` of an operation
  that had already finished.
- Completed hook Jobs were deleted before they could be inspected.

The underlying hazard is unchanged. With `syncPolicy.automated.selfHeal`, Argo
can start its own selective sync between the pin landing on `main` and this
action's request. That sync reconciles only the drifted resources and runs
**no hooks**, so a `PreSync` migration Job never executes. It still leaves the
Application at the pinned revision, `Synced` and `Healthy`, which is exactly
what this step waits for. A sync this action requested can also skip its
hooks. On flights, on 2026-10-02, under Argo CD v3.5.2, an operation that had
to wait for an earlier hook Job to be deleted resumed with its hooks skipped.

`require_hook` is still accepted so that existing callers keep working, but it
is ignored. The script logs a notice when it is set. Callers can drop it.

### Where migration proof lives

The proof has moved into the app. The app's health route compares the
migration journal shipped in the image against the database. When the
database is behind, the route returns a 503 with `db=behind`. The `travel#35`
change is the pattern to follow.

What CI shows for a skipped migration depends on where the readiness probe
points.

**Readiness on the schema-aware route (the recommended setup).** The new pod
never goes Ready, so the Deployment stays `Progressing`. The step "Wait for
Argo to reach that revision, synced and healthy" times out on
`health.status=Healthy` after `wait_timeout`. The rollout and verify steps
after it do not run, so `db=behind` never appears in the CI log. The CI
symptom is only that Healthy-wait timeout. To find the cause, look in these
places:

- `kubectl -n <ns> describe pod <new pod>` shows the pod `NotReady`, with
  readiness probe failures returning 503.
- The app's own log has the line where it reports the database behind its
  journal.
- A request to the health route on the new pod's IP returns 503 `db=behind`.

Do not expect the verify step to report it, even if you run it by hand.
`health_url` is normally a Service. With `maxUnavailable: 0`, that Service
routes only to the old Ready pod, so the request reaches the old sha with
`db == ok` and fails on the sha mismatch instead.

**Readiness not on that route.** The new pod goes Ready and the rollout
finishes. The verify step then reaches the new build, and its `db == ok`
assertion is what catches the behind schema: the route answers 503 and the
step fails on the non-2xx response. This is the case `health_expect_db_ok`
exists for.

### Recovering from a skipped migration

Recover with a full sync of the Application, meaning a sync operation with no
`resources` list. A full sync runs the hooks; a selective sync, which is all
selfHeal ever does, never runs them. The request is the same JSON Patch the
script sends, issued when `.operation` is empty:

```sh
kubectl -n argocd patch application <app> --type json -p \
  '[{"op":"add","path":"/operation","value":{"initiatedBy":{"username":"<you>"},"sync":{"revision":"<pinned sha>"}}}]'
```

Then check the Application's events and make sure the operation was not
partial. A selective sync is reported as `Partial sync operation to <rev>
succeeded`, while a full one says `Sync operation to <rev> succeeded`:

```sh
kubectl -n argocd get events --field-selector involvedObject.name=<app> \
  --sort-by=.lastTimestamp
```

Also confirm that the migration Job ran and that the health route reports
`db == ok`.

Give migration hooks
`argocd.argoproj.io/hook-delete-policy: BeforeHookCreation,HookSucceeded`.
`BeforeHookCreation` deletes the previous run's Job when the next sync creates
its own. That avoids the case where an operation has to wait for an earlier
hook Job to be deleted, which is the case that resumed with hooks skipped.

### Apps whose migrations are not hooks

Some apps migrate in-process. Finance, for example, runs drizzle's `migrate()`
inside `getDb()` on the first request. There is no Job for a hookless sync to
skip: if the pod is serving, it has already migrated, because migrating is the
precondition of serving. What can still go wrong is a migration that fails or
is never reached. The schema-aware health route catches that the same way.
For a manual check, compare the journal itself:

```sh
kubectl -n <ns> exec deploy/<db> -- psql -U postgres -d <database> \
  -tAc "select count(*) from <schema>.__drizzle_migrations"
```

against the number of migration files the deployed commit carries.

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
| `github_token` | Token for the upstream public schema API rate limit | `${{ github.token }}` |

The overlay is rendered once and the same bytes are validated on every attempt.
If kubeconform cannot retrieve its default Kubernetes schemas from
`raw.githubusercontent.com`, the action retries through a temporary adapter
bound only to loopback. The adapter retrieves the same repository, ref and schema
path through GitHub's public contents API over verified TLS. An upstream 404
remains a 404, preserving caller `-ignore-missing-schemas` policy for custom
resources; other API errors and non-schema responses remain errors.
There are at most two validation attempts. Strictness, output format, cache
flags and custom schema locations are preserved; only the default upstream
location and the explicitly configured `datreeio/CRDs-catalog/main` location are
redirected, in their original search order. Other custom schema hosts are
unchanged. Python 3.12 is provisioned by the action, uses only its
standard library, and requires no caller setup. The existing job token is passed
through the optional `github_token` input to avoid exhausting the shared anonymous
API rate limit. No new credential grant is required; an empty input opts into
anonymous requests. Credentials go only to the fixed GitHub API host, and
redirects are rejected. The adapter is stopped
and its temporary files removed on exit. Invalid manifests and unresolved schema
errors still fail the gate.

The shared Helm installer has the same transport fallback for the upstream
`helm/helm` `main` script. It requires a successful download and a shell-script
header before execution; a failed process substitution can no longer silently
run an empty installer.

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
`job.workflow_sha`, so the script it runs is the one that shipped in the
tag the caller pinned -- not whatever is on `main`. That works without a token
only because this repo is public.

A repo with no `mkdocs.yml` is not a failure. The `detect` job probes for the
file and `gate` is skipped, so the workflow can be added to a repo's `ci.yml`
before its docs exist.

`.github/actionlint.yaml` carries a narrow ignore for
`job.workflow_sha`: it is the documented defining-workflow context property that older actionlint
schemas do not yet know. Both gates validate its full commit SHA before checkout,
so a missing identity fails instead of selecting the default branch. GitHub
Enterprise Server does not expose this context and is not supported by these
gates. The same file has to enumerate this repo's
self-hosted labels, because actionlint only checks labels once a config file
exists.

### agents-md

Gates the calling repo's `AGENTS.md`, the one repo context file every fleet repo
carries for Claude Code and Codex. The file is hand-written prose on top and a
generated tail underneath, and the gate fails the moment the file and the repo
disagree: a stale context file is worse than none, because an agent trusts it.

```yaml
jobs:
  agents-md:
    uses: willfell/wac.lab.actions/.github/workflows/agents-md.yml@v1.13.0
    with:
      runner: lab
    permissions:
      contents: read
```

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label the job is sent to, in the caller's repo | `ubuntu-latest` |

The job checks this repo out a second time at `job.workflow_sha`, into
`.agents-md-gate`, exactly as `techdocs` does, and runs
`uv run .agents-md-gate/scripts/agents-md.py check`. The script declares its
own Python and PyYAML pin inline (PEP 723), so `uv run` builds its environment
from that block and ignores any project in the calling repo. `check` reads the
working tree and asks git which paths are tracked and which are ignored; it
makes no network calls of its own.

It fails, with a `::error file=...,line=...::` annotation for each finding, on:

1. A tracked `CLAUDE.md` or `CLAUDE.local.md` at any depth. Claude Code reads
   `AGENTS.md` only when no such file exists in the working directory or above
   it, so one tracked CLAUDE.md silently turns the whole file off. Untracked
   files, such as old branches under `.worktrees/`, are ignored.
2. A root `AGENTS.md` that is missing, a symlink, or not UTF-8; one that
   starts with a byte-order mark or has CRLF line endings (each named as such);
   or a `catalog-info.yaml` that is missing, does not parse, has a malformed
   entity (`metadata` or `spec` not a mapping, no `metadata.name`, a relation
   that is not a list of string refs), or declares no Component at all.
3. A broken section contract: the first non-blank line must be the only H1,
   then the H2s `The one thing to understand`, `Commands`, `Layout`,
   `Invariants`, `Boundaries`, `Dependencies` and `Fleet rules` must all be
   present, in that relative order. A repo may add its own H2s anywhere before
   `## Dependencies`; no H1 or H2 other than the generated `## Fleet rules` may
   follow it. Headings are read outside fenced code blocks only, so a `# note`
   line in a command block is not an H1, and a code fence left unclosed is a
   finding of its own. Setext headings (a line underlined with `===` or
   `---`) count, since GitHub renders them as headings.
4. A file that differs from what `render` would write, with a unified diff.
   Everything from `## Dependencies` to the end of the file is generated from
   the repo's `catalog-info.yaml` and from `agents-md/fleet-rules.md` here; a
   hand edit there fails.
5. A `## Commands` section with no fenced shell block holding a command, or a
   command there that does not resolve. Only fences tagged `sh`, `bash`,
   `shell`, `zsh` or `console`, or not tagged at all, are read; in a `console`
   block only `$ ` prompt lines are commands. Each line is read as a command run
   from the repo root: a trailing `# note` is dropped, a line holding a
   `<placeholder>` is skipped, segments split on `&&`, `||` and `;`, and `cd`,
   `pushd` and `popd` are followed. Then:
   - `npm run <x>`, `npm test|start|stop|restart` and `npm --prefix <dir> ...`
     must name a script in that directory's `package.json` (`npm start` also
     passes on a `server.js`, npm's own fallback);
   - `make <target>` (and `make -C <dir> <target>`) must name a rule in that
     directory's Makefile;
   - `bash|sh|node|python|python3 <path>`, `node --test <path>...`,
     `uv run <path>` and a bare `./<path>` or `scripts/<path>` must name a path
     that exists.

   "Exists" means **tracked by git**, not present on the local disk, so a file
   that exists only on one machine cannot make the check pass there. A path
   git does not track but does ignore (`dist/`, `node_modules/`) is a build
   output and is left unchecked, as is a path that leaves the repo, an
   absolute path, or one built from a shell variable. A path is matched
   literally first, so a tracked `app/[id]/route.test.mjs` resolves as written;
   only a path that misses and holds `*`, `?` or `[` is read as a glob, which
   must then match at least one tracked file. Anything else (`tofu`, `npx`,
   `kubectl`, `npm install`) is not checked: recognised forms fail closed,
   unrecognised ones pass.

   Ignore rules come from the repo's `.gitignore` files and from the clone's
   own `.git/info/exclude`. The user's global excludes file
   (`core.excludesFile`) is switched off, but `.git/info/exclude` is not, so a
   path ignored only there passes locally and fails on a fresh CI checkout.
   Put build outputs in a committed `.gitignore`.

There is no detect step. Unlike `techdocs`, a fleet repo without the file is a
failure rather than a skip, so add the job in the same PR as the `AGENTS.md`.

**Render from the tag you pin.** The fleet rules are part of the tail, and they
change between tags. A tail rendered from `main`, or from whatever a local
checkout happens to be on, is rejected by a gate pinned elsewhere. From the
repo being rendered:

```sh
git -C <wac.lab.actions-checkout> worktree add --detach <gate-dir> v1.13.0
uv run <gate-dir>/scripts/agents-md.py render
uv run <gate-dir>/scripts/agents-md.py check
```

`render` rewrites everything from the first `## Dependencies` to the end of the
file, or appends the tail if there is none yet. The prose above it is kept as
written, with three normalisations: a UTF-8 byte-order mark is dropped, CRLF
line endings become LF, and trailing blank lines become exactly one before
`## Dependencies`. Running it twice changes nothing.

What `render` overwrites, exactly: a tail it generated, which it recognises by
the generated preamble line directly under `## Dependencies`. Everything in
that tail is replaced, including any hand edit made inside it; `check` fails on
such an edit, and that failure is the only warning before a render discards
it. A `## Dependencies` with anything else under it was written by hand, and
render will not touch it.

So `render` writes nothing and exits non-zero when:

- `## Dependencies` exists but the line under it is not the generated
  preamble, and anything other than a bare `## Fleet rules` heading follows it
  (bare `## Dependencies` and `## Fleet rules` headings, an outline waiting for
  its first render, are filled in);
- an H1 or H2 other than the generated `## Fleet rules` sits below
  `## Dependencies` (it names each one to move above it);
- a code fence is left unclosed;
- `AGENTS.md` is a symlink, or the catalog has any problem listed above;
- its own output would not survive a second render (a catalog description
  that reads as a heading or a code fence).

`catalog-info.yaml` is read as YAML 1.2 reads booleans, the way Backstage
reads it: only `true` and `false` are booleans, so `name: on` is the
Component `on`, not `True`. A name YAML reads as a number (`name: 123`) is a
finding that says to quote it. Both subcommands take `--root PATH` in place of
the current directory.

The consequence is deliberate: bumping a repo's pin to a tag that changed
`agents-md/fleet-rules.md` fails its gate until the bump PR re-renders, so the
new rules ride the bump and cannot go stale silently. A `CHANGELOG.md` row for
such a tag says so.
