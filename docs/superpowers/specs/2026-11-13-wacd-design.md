# wacd: one chart, one CI, one CD for the fleet

Date: 2026-11-13. Status: draft for approval.

## Intent

Every cluster app carries its own hand-written `deploy/k8s` kustomize tree and
its own copy of the build, pin and deploy wiring. A fix to probes, migrations
or the deploy sequence is made once per app. The fleet moves to the shape Hemi
uses at Accuris (`accuristech/actions`, branch `feat/hemi-naming-cohesion`): app
repos hold values files and no manifests, one shared chart renders every app,
one CI workflow checks pull requests, and one CD workflow versions, builds,
publishes and deploys on a merge to `main`. A `wacd` plugin onboards a repo.

"Decided" marks what the owner said. "Call" marks a choice made while writing
this, open to correction.

## Goals

- Decided: app repos contain no chart and no manifests, only values files.
- Decided: one shared chart, versioned.
- Decided: no git pinning. No pin commit, deploy key or `[skip ci]` commit.
- Decided: CI never pushes. Only a merge to `main` builds, pushes and deploys,
  and that is CD's job.
- Decided: releases are versioned.
- Decided: deploy the way Hemi does: an immutable deploy chart per release, and
  Argo CD told which chart version to run.
- Decided: names are `wac-ci`, `wac-cd`, and a plugin named `wacd`.
- Decided: no Snyk and no approval gates. The fleet has none today, so this
  means Hemi's scans, SBOM, attestation, build approval and environment
  approvals are not ported.
- Onboard every live cluster app: travel, finance, wac, wealth, mcp.argo, baton.

## Non-goals

- `wac.docs` and `wac.ai.ag` (wrappers of upstream charts), `wac.lab.remote`
  (host launchd), lab's own platform images, the static sites, and the retired
  apps (flights, handbook, claw) stay as they are.
- Multiple environments and promotion. There is one cluster and one
  environment. The profile leaves room for a second, nothing builds it.
- Deleting `lab-gitops-deploy`, `lab-release`, `lab-build`, `lab-deploy`. They
  stay published; removal is a later change once no consumer pins them.
- Hemi's evidence chain: release-asset deployment records, New Relic markers,
  readiness assets, six environment slots.

## Anti-goals

- A runner holding a Kubernetes or Argo CD token.
- A cutover that recreates a PersistentVolumeClaim or renames a live object.
- A chart so generic that a values file is a manifest in disguise. Values name
  features (`migrate.mode: presync`), not Kubernetes structure.
- Two deploy mechanisms left running for the same app after its cutover.

## Constraints found in the repos

- Runners are tokenless Linux ARM64. Deploy authority is the release gateway
  in `wac.lab` (`src/lab/cideploy/`, contract in `docs/ci-deploy-contract.md`),
  reached with a GitHub OIDC token. It fixes each repository ID to one
  Application and Deployment, and today trusts only the caller's own workflow
  file: tokens carrying a different reusable-workflow identity are denied, and
  so is any `environment` claim.
- Images push to `registry.network.svc.cluster.local:5000` over plain HTTP and
  pull from `localhost:30500`. The two addresses are never collapsed.
- Jobs route only through `vars.CI_RUNNER_SMALL`, `CI_RUNNER_STANDARD` and
  `CI_RUNNER_LARGE`. Reusable workflows declare `runner` with default `''`
  (Invariant 4 here).
- `wac.lab` registers apps as one Application file each under
  `k8s/argocd-apps/`, watched by a root app with prune and self-heal. A lab
  test forbids inline Helm values on an Application.
- Each app proves a deploy by fetching its in-cluster health URL and matching
  the served build SHA to the source SHA.
- Tags already in use: `wac` has `ui-v*`, `knowledge-v*`, `rhdh-theme-v*`;
  baton has `v1.37.x`; travel and mcp.argo have none.
- Fleet rule 3 says a merge to `main` reconciled by Argo CD is the only deploy.
  The rule text is rendered into every consumer's AGENTS.md.

## Approaches

1. **Hemi-faithful (picked by the owner).** CD publishes a deploy chart per
   release and the gateway sets the Application's chart version. No git write
   on deploy. Costs: a new gateway operation, a rewritten fleet rule 3, and the
   running version living in Argo CD and the registry instead of git.
2. **Shared chart, git pin kept.** The values file carries the image tag and CI
   commits it. The gateway barely changes, but pin commits, deploy keys and
   `[skip ci]` all stay. Rejected: the owner wants pinning gone.
3. **Shared chart, image set by Argo CD Image Updater** (baton's route today).
   No pin commit either, but the deployed version is an override nobody
   requested explicitly, releases are not immutable, and rollback is awkward.
   Rejected: not how Hemi does it.

## Design

### The pieces and where they live

| Piece | Repo | Path |
| --- | --- | --- |
| Shared chart `wac-app` | `wac.lab` | `charts/wac-app/` |
| App registrations and the ApplicationSet that reads them | `wac.lab` | `k8s/registrations/` |
| Gateway `release` operation and policy | `wac.lab` | `src/lab/cideploy/` |
| Reusable workflows `wac-ci.yml`, `wac-cd.yml` and their scripts | `wac.lab.actions` | `.github/workflows/`, `scripts/` |
| Plugin `wacd` | `wac.plugins` | `plugins/wacd/` |
| Profile, values file, two caller workflows | each app repo | `.github/wacd.json`, `deploy/values.yaml`, `.github/workflows/ci.yml`, `cd.yml` |

Call: the chart lives in `wac.lab` because lab owns the cluster conventions it
encodes (Traefik, the suite token audience, the secrets registry), and Hemi
keeps its chart beside its registrations for the same reason.

### What an app repo holds

- `.github/wacd.json`, the profile. Unknown fields fail. Call: one file for
  both CI and CD, where Hemi has two.

  ```json
  {
    "schema_version": 1,
    "app": "travel",
    "chart_version": "1.0.0",
    "values_file": "deploy/values.yaml",
    "tag_prefix": "v",
    "health_url": "http://travel-app.travel.svc.cluster.local:3000/api/health",
    "images": [
      {"name": "travel-app", "dockerfile": "Dockerfile", "context": ".", "sha_build_arg": "TRAVEL_BUILD_SHA"}
    ],
    "ci": {"stack": "node", "node_version": "22", "commands": ["npm run lint", "npm run typecheck", "npm test", "npm run build"]}
  }
  ```

- `deploy/values.yaml`, the app's values for `wac-app`. It never sets the
  app name, namespace, image tag or image digest; CD and the registration
  supply those.
- `.github/workflows/ci.yml` calling `wac-ci.yml`, and `cd.yml` calling
  `wac-cd.yml`, both at an exact `wac.lab.actions` tag.
- A Dockerfile per image. No `deploy/k8s`, no `Chart.yaml`, no templates.

### The shared chart `wac-app`

One chart renders everything the six apps deploy today. Top-level values:

- `service`: port, extra ports.
- `probes`: readiness path, optional separate liveness path, optional startup.
- `replicas`, `strategy` (`RollingUpdate` or `Recreate`), `resources`,
  `priorityClassName`, `securityContext`, `lifecycle.preStop`, `args`.
- `env`: plain values and `secretKeyRef` entries against registry-synced Secrets.
- `serviceAccount`: create, sync-wave, projected token with an audience.
- `route`: `ingress` (Traefik; path prefix with StripPrefix Middleware, or root
  with none; extra hostnames) or `httpRoute` (parent Gateway, cross-namespace).
- `migrate`: `mode` of `none`, `init`, `presync` or `both`, and the command.
- `cronJobs`: schedule, command, which of the release's images, volume mounts.
- `persistence`: named claims with size, `Prune=false` and health annotations.
- `volumes`: Secret volumes.
- `serviceMonitor`.

Rendered object names match what is live today (`<app>-app` for most, `baton`
for baton), set through an explicit name value, so a cutover changes no names.

Versioning. Call: `Chart.yaml` `version` is the source of truth. Lab CI lints,
unit-tests and renders the chart on every pull request, fails if chart content
changed without a version bump, and on merge publishes
`oci://registry.network.svc.cluster.local:5000/charts/wac-app` at that version
if it is not there already. A published version is never overwritten.

### Registrations

`wac.lab/k8s/registrations/apps.yaml` holds one entry per app:

```yaml
apps:
  - name: travel
    project: travel
    namespace: travel
    chart: deploy/travel
    bootstrapRevision: 0.1.0
```

An ApplicationSet renders one Application per entry: single-source Helm OCI
chart `deploy/<app>` from the in-cluster registry, automated sync with prune
and self-heal, no inline values. It ignores differences on
`/spec/source/targetRevision`, so the version the gateway sets survives
re-renders (Hemi's `ciOwnsRevision`). `bootstrapRevision` is used only when the
Application is first created and is never updated afterwards.

Call: the template carries no automated sync, and the ApplicationSet also
ignores differences on `/spec/syncPolicy`. The gateway's `release` turns
automated sync (prune and self-heal) on in the same patch that sets the
version. An Application that is ever recreated, by a rename or a cluster
rebuild, therefore sits idle at the bootstrap version until the next release
instead of rolling out a stale chart and its old migration.

The app's existing file under `k8s/argocd-apps/` is deleted in the same change
that registers it; those Applications carry no finalizer, so nothing live is
deleted. Each AppProject gains the OCI chart as an allowed source. Namespace,
quota and secrets entries stay where they are.

### Versioning a release

Call: semver per app. `wac-cd` finds the latest tag matching
`<tag_prefix>X.Y.Z` and bumps it from the conventional commits since: breaking
is major, `feat` is minor, anything else is patch. With no prior tag the first
release is `0.1.0`. Baton continues from `v1.37.2`. `wac` uses
`tag_prefix: "app-v"` because its other tag families are taken. The version is
the image tag, the deploy chart version, the git tag and the GitHub Release
name. Every merge to `main` releases.

Call: the git tag is pushed in the same step that publishes the deploy chart,
so a tag means "this version was published", and a failed rollout never leaves
a version that the next merge would compute again. The GitHub Release is
created only after the deploy is proved, so a Release means "this version ran".

Call (the owner was asked twice and did not object): baton keeps its own
version rules (`baton/docs/versioning.md`, `scripts/release-version.mjs`): only
commits touching shipped paths release, and the version and per-piece floors
are stamped into the image. `wac-cd` therefore accepts an optional
caller-computed version, a "no release" outcome that ends the run green, and
extra build args. The other five apps use the default.

### Registry

Decided: no Harbor for now. The registry stays `registry:2.8.3` at its two
addresses. Two changes keep rollback and immutability honest without it:

- The cleanup job in `wac.lab/k8s/platform/registry/` keeps every image that a
  retained deploy chart references, and keeps the last several deploy charts
  per app.
- The gateway records the digest of each chart version it releases and refuses
  to release that version again under a different digest.

### wac-ci (pull requests)

A reusable workflow. It sets up the toolchain for `ci.stack` (`node` or `go`),
handles npm registry auth, runs `ci.commands` in order, renders the deploy
chart from the app's values file against the pinned `chart_version` and
validates the output with kubeconform, and runs the three existing gates
(actionlint with `runner_policy: fleet`, techdocs, agents-md). It builds no
image and pushes nothing. Baton's heavy suites and mcp.argo's Argo CD e2e
matrix stay in their own workflows beside it.

### wac-cd (merge to main)

A reusable workflow, one job chain, serialized per app:

1. Compute the version. Stop if the tag already exists on another commit.
2. Build each image in `images` once and push it to the push registry as
   `<name>:<version>`, with the source SHA as a build arg and an OCI label.
   Record each digest.
3. Package the deploy chart `deploy/<app>` at the version: `wac-app` at
   `chart_version` vendored in as a dependency, the app's values nested under
   it, and each image's pull address, tag and digest written in. Push it. A
   version that already exists is an error, never an overwrite.
4. Ask the gateway to `release` the version, then poll `status` until the
   Application is Synced and Healthy at that chart version and the Deployment
   is fully rolled out on the expected digest.
5. Fetch `health_url` and require the served SHA to equal the source SHA.
6. Create the GitHub Release. The tag was pushed with the chart in step 3.

Rollback is a `workflow_dispatch` run of the same workflow with a `version`
input: it skips steps 1 to 3 and 6 and releases the chart that already exists.

Call: rollback is supported only back to the last release that added a
database migration. There are no down migrations, and an older image meeting a
newer schema is the app's to refuse. Nothing in `wac-cd` or the gateway
enforces the limit; the `wacd:release` skill states it before rolling back.

Permissions: `contents: write` (tag and Release) and `id-token: write`. No
deploy key, no secrets beyond the npm read token builds already use. No GitHub
environment is used anywhere; the gateway rejects that claim.

### Gateway changes

- New operation `release` with body `{"version": "X.Y.Z"}`. The gateway
  resolves the fixed chart for the calling repository, confirms that version
  exists in the registry and that its source SHA label is the token's SHA and
  an ancestor of current `main`, then patches the fixed Application's
  `targetRevision` under a resource-version precondition and requests a sync.
  For a rollback dispatch the chart's SHA only has to be an ancestor of `main`.
- `status` reports the chart version in place of a pin SHA.
- Policy admits, per repository ID, the caller workflow `cd.yml` on a `main`
  push or dispatch whose job-workflow identity is `wac-cd.yml` in
  `wac.lab.actions` at a release tag. This replaces the rule that denies every
  reusable-workflow identity with a rule that admits exactly one.
- RBAC: patch on each registered Application, get on its Deployment. Baton's
  observe-only grant becomes the same as the others.
- `sync` with a pin SHA is retired per app at that app's cutover.

### The wacd plugin

A new plugin in `wac.plugins`, listed in the marketplace, with two skills:

- `wacd:onboard <repo>`: staged like Hemi's. Discover the repo's current
  manifests and workflow; write the profile and values file; render the chart
  and diff it against the current kustomize output until only expected
  differences remain; open the app and lab pull requests in cutover order;
  validate the first release. It opens pull requests and does not merge them
  unless told to.
- `wacd:release`: show what version each app runs, verify a release, roll back.

The four `wac` skills that describe the old route (`deploy-verify`,
`lab-onboard-app`, `suite-add-app`, `fleet-ci-routing`) are rewritten to match.

### Cutover, per app

1. App pull request: add the profile, values file and `cd.yml` with deploys
   switched off, beside the existing deploy job. CI proves render parity with
   `deploy/k8s`. On merge the old route still deploys and `wac-cd` publishes
   the first deploy chart without releasing it.
2. Lab pull request: register the app at that version, admit it in gateway
   policy, delete its old Application file. Argo CD switches source; with
   parity this rolls nothing.
3. App pull request: switch deploys on; delete `deploy/k8s`, the old deploy
   job and the deploy key secret; move `ci.yml` to `wac-ci`.

Order: travel (the baseline app) as pilot, then finance, wac, wealth,
mcp.argo, and baton last. Baton also drops its Image Updater configuration and
kustomize digest pin, and its AppProject whitelist gains what the chart renders.

### Rules and records that change

- Fleet rule 3 becomes: a deploy is a merge to `main` reconciled by Argo CD, or
  a `wac-cd` release through the gateway; never a change by hand. Every
  consumer's agents-md gate fails at its next bump until it re-renders, and the
  CHANGELOG row says so.
- `wac.lab.actions` ships `wac-ci` and `wac-cd` as new components in a minor
  release, with CHANGELOG rows, README sections, and CONSUMERS.md updated from
  each repo as it cuts over. mcp.argo is added to CONSUMERS.md.

## Known costs and risks

- **The running version is not in git.** After a cluster rebuild each
  Application comes back at `bootstrapRevision`, and the registry may be empty.
  Recovery is re-running `wac-cd` for each app's latest tag. Git tags and
  Releases are the record of what was released.
- **Argo CD pulling a Helm OCI chart from a plain-HTTP registry** is unproven
  here. It is the first thing to prove, before anything else is built.
- **PVC continuity** for finance and wealth depends on exact name parity.
- **Chart drift.** Six apps on one chart means a chart bug reaches all six,
  one `chart_version` bump at a time. The chart's unit tests carry a fixture
  per app.

## Left to the builder

- The exact values schema and template layout of `wac-app`.
- How the ApplicationSet reads the registrations file (generator choice).
- The semver and chart-packaging scripts and their tests.
- Whether policy targets stay hard-coded in `policy.py` or are read from the
  registrations file.
- Whether Image Updater has users other than baton and can be removed.
