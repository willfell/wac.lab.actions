# wacd program: shared context for every session

Owner: Will. Coordinator session: `wacd-init`. Date: 2026-11-13.

This file is the shared brief for the sessions that execute the wacd program.
It is read-only for them: if something here is wrong, message `wacd-init`
rather than editing it.

## What the program is

Move every live homelab app off its hand-written `deploy/k8s` kustomize tree and
per-app deploy wiring, onto the shape Hemi uses at Accuris:

- one shared Helm chart, `wac-app`, that renders every app;
- app repos hold only values files, a profile, a Dockerfile and two caller
  workflows, never a chart or manifests;
- `wac-ci` checks pull requests and never pushes;
- `wac-cd`, on a merge to `main`, computes the version, builds and pushes the
  image once, publishes a per-app deploy chart at that version, and has the
  release gateway point the app's Argo CD Application at that chart version;
- no git pinning: no pin commit, no deploy key, no `[skip ci]` commit;
- no Snyk and no approval gates;
- a `wacd` Claude Code plugin onboards a repo.

The apps in scope: travel, finance, wac, wealth, mcp.argo, baton.

## The authorities, in order

1. **The spec** (binding):
   `/Users/willfell/Documents/GitHub/wac.lab.actions/docs/superpowers/specs/2026-11-13-wacd-design.md`.
   Read it in full before planning. Never edit it. If your work needs an
   interface in it to change, stop and message `wacd-init`.
2. **Plan 1, the shared chart** (done; under final fix-wave review):
   `/Users/willfell/Documents/GitHub/wac.lab.actions/docs/superpowers/plans/2026-11-13-wacd-1-shared-chart.md`.
   Its values contract and parity rules are the chart's interface.
3. Each repo's `AGENTS.md`. Fleet rule 1 (no comments in config files) and the
   user's global CLAUDE.md apply everywhere.

## State of the shared chart

- Branch `feat/wac-app-chart` in `wac.lab`, worktree
  `/Users/willfell/Documents/GitHub/wac.lab-worktrees/wac-app-chart`.
  **Not pushed, not merged.** The owner merges it after the fix wave passes.
  Read from it; never commit to it.
- Chart: `charts/wac-app/` at `1.0.0`, closed `values.schema.json`, reproducing
  all six apps' live renders byte-for-byte after the documented normalization.
- Publication: `.github/workflows/wac-app-chart.yml` publishes on merge to
  `oci://registry.network.svc.cluster.local:5000/charts/wac-app`, once per
  version, never overwriting, failing if a version exists with other content.
- Version lock: `charts/wac-app.lock.json`, tool `uv run python -m lab.wac_app_lock write|check|digest <dir>|not-found <ref>`
  (in `src/lab/wac_app_lock.py`).
- Values contract summary (full table in Plan 1): `name`, `app`, `image`,
  `images` (map of `{repository, tag, digest}`, written by `wac-cd`),
  `args`, `replicas`, `minReadySeconds`, `strategy`, `ports`,
  `probes.{readiness,liveness,startup}`, `resources`, `env`
  (`{name, value}` or `{name, secret: {name, key, optional}}`),
  `serviceAccount`, `suiteToken`, `podSecurityContext`, `securityContext`,
  `priorityClassName`, `terminationGracePeriodSeconds`, `preStopSleepSeconds`,
  `routes` (`ingress` with optional `name`, or `httpRoute`), `migrate`
  (`none|init|presync|both`), `cronJobs`, `persistence`, `volumes`,
  `volumeMounts`, `serviceMonitor`. Image `repository` must match
  `^localhost:30500/`; `digest` matches `^sha256:[0-9a-f]{64}$`.
- As a dependency, values nest under the `wac-app:` key, and Helm's injected
  `global` key is allowed (fixed in the fix wave).

## Facts established while building Plan 1

- The cluster runs Argo CD v3.5.5 (chart 10.10.3, `src/lab/k8s.py`), which
  ships Helm 4.
- Argo CD pulling a Helm chart from the plain-HTTP in-cluster registry: this
  cluster already did it in August for ero-mcp (`wac.lab` commits `e5372d23`,
  `7b74b5a4`): Application `repoURL: oci://registry.network.svc.cluster.local:5000/charts/<chart>`,
  `path: "."`, and a repository Secret of `type: oci` with
  `insecureOCIForceHttp: "true"` and no `insecure`. The repo test
  `test_oci_helm_chart_sources_carry_the_chart_in_the_url_and_path_dot`
  enforces that form. The proof Application `wac-app-proof` uses it. Whether a
  repository Secret's URL must match the Application exactly (so a
  `deploy/` repository needs its own Secret or a prefix form) is not yet
  proven.
- Repository Secrets are entries in `wac.lab/k8s/secrets/registry.yaml` and
  reach the cluster only when the owner runs `lab k8s secrets sync`.
- The registry GC CronJob (`wac.lab/k8s/platform/registry/registry-gc-cronjob.yaml`)
  deletes chart tags no Application references in the `oci://` form. The fix
  wave exempts `charts/wac-app` permanently. Deploy charts and the images
  they reference need protection too (Plan 2).
- `wac-app-proof` is a temporary no-sync Application; `COMPARE_ONLY_APPLICATIONS`
  in `src/lab/k8s.py` makes `lab k8s doctor` and the VM restart wait skip it.
  Plan 2 deletes both once registrations prove the same path.
- The root app ignores `/spec/source/targetRevision` on Applications
  (`k8s/bootstrap/root-app.yaml`, `RespectIgnoreDifferences=true`).
- The registry is unauthenticated inside the cluster.
- Any change under `wac.lab/src/lab/**` fires `warehouse-image.yml` on merge
  (warehouse build plus migrations; ledger-idempotent; accepted).
- mcp.argo PR #24 (merged 2026-11-13) moved its HTTPRoute to `wac.ai.ag`;
  mcp.argo's live render has no route now.
- baton keeps its own version rules (`baton/docs/versioning.md`,
  `scripts/release-version.mjs`); `wac-cd` must accept a caller-computed
  version, a "no release" outcome, and extra build args.
- Tag prefixes: travel and mcp.argo have no tags; baton has `v1.37.x`; wac's
  `v*`-like families are taken (`ui-v*`, `knowledge-v*`, `rhdh-theme-v*`), so
  wac uses `app-v`.
- Gateway today: `wac.lab/src/lab/cideploy/` (policy, backend, server),
  contract `wac.lab/docs/ci-deploy-contract.md`. It hard-codes repository ID to
  Application, namespace and Deployment, trusts only the caller's own
  `ci.yml`/`deploy.yml`, and denies any reusable-workflow identity and any
  `environment` claim.
- Push registry `registry.network.svc.cluster.local:5000` (plain HTTP); pull
  address `localhost:30500`. Never collapse them.
- Runners: route only via `vars.CI_RUNNER_SMALL|STANDARD|LARGE`; reusable
  workflows declare `runner` with default `''`.

## Interfaces pinned by the sessions

Plan 2 (`wacd-app-registrations`), 2026-11-13. Authority:
`/Users/willfell/Documents/GitHub/wac.lab-worktrees/wacd-registrations/docs/ci-deploy-contract.md`,
section "Chart releases (wac-cd)", commit `01f212ad` on `feat/wacd-registrations`.

- Bodies on the existing `/v1/release` endpoint:
  `{"operation":"release","version":"X.Y.Z"}` and `{"operation":"status"}`.
- Deploy chart: registry repository `deploy/<Application name>` (travel,
  finance, wac, wealth, argo-mcp, baton), tag = version, `wac-app` vendored.
  `Chart.yaml` annotations: `org.opencontainers.image.revision` (source SHA,
  checked by the gateway) and `wacwini.com/images` (pull refs with digests,
  kept by registry GC).
- Trusted reusable identity:
  `willfell/wac.lab.actions/.github/workflows/wac-cd.yml@refs/tags/vX.Y.Z`
  (tag format checked only). Transferring `wac.lab.actions` needs a gateway
  change.
- Argo CD v3.5.5 reports an OCI source's `sync.revision` as the manifest
  digest; release proof compares it with the digest the gateway recorded, and
  the response returns that digest.
- Errors keep the existing opaque shapes (400/403/404/503); 503 includes a
  lost resource-version race, retried with a fresh token.
- One Argo CD `repo-creds` credential template for the prefix
  `oci://registry.network.svc.cluster.local:5000/deploy` (`type: oci`,
  `insecureOCIForceHttp`), not proven on the cluster yet.

Plan 3 (`wacd-version-scripts`), 2026-11-13. Authority:
`/Users/willfell/Documents/GitHub/wac.lab.actions/docs/superpowers/plans/2026-11-13-wacd-3-ci-cd.md`,
branch `feat/wac-ci-cd` (worktree `../wac.lab.actions-worktrees/wac-ci-cd`).

- Profile `.github/wacd.json`, schema_version 1, unknown keys fail at every
  level. `app` must equal the Application and deploy-chart name (travel,
  finance, wac, wealth, argo-mcp, baton). Fields: `chart_version`,
  `values_file`, `tag_prefix`, `health_url`, `images[]`
  (`name`, `dockerfile`, `context`, optional `sha_build_arg`), `ci`
  (`stack` node|go, `node_version` or `go_version`, `commands`), plus an
  optional health-db flag for wac (name to be confirmed by Plan 3).
- The values file may set `name` and `image`; it must not set `app`,
  `images` or `global` (wac-cd writes `app` and `images`).
- Caller files: `.github/workflows/ci.yml` calls `wac-ci.yml`;
  `.github/workflows/cd.yml` (exact path; the gateway checks it) calls
  `wac-cd.yml` on push to main and on `workflow_dispatch`, whose `version`
  input maps to `rollback_version`. Caller job permissions `contents: write`,
  `id-token: write`, `packages: read`; no `environment`.
- `wac-cd` inputs: `runner`, `version_source` (`commits`|`caller`),
  `version`, `rollback_version`, `build_args`, `wait_timeout`; secret
  `npm_token`. Outputs `mode` (release|resume|rollback|none), `version`,
  `tag`, `images`.
- Fleet rule 3 keeps the bootstrap exception clause (wacd-init ruling).
- A publish-only switch for cutover step 1 (chart push and tag, no gateway
  release, no GitHub Release) was requested from Plan 3 by wacd-init; name
  to be confirmed.

Plan 5 (`wacd-plugin`), 2026-11-13. Authority:
`/Users/willfell/Documents/GitHub/wac.lab.actions/docs/superpowers/plans/2026-11-13-wacd-5-plugin.md`,
branch `feat/wacd-plugin` (worktree `/Users/willfell/Documents/GitHub/wac.plugins-worktrees/wacd-plugin`).

- Parity tool `plugins/wacd/skills/onboard/onboard.py`: exit 0 parity, 1
  differences, 2 error; normalization vendored from `wac.lab`
  `tests/wac_app/render.py` at `8612f031`.

Plan 2 additions: plan at
`/Users/willfell/Documents/GitHub/wac.lab.actions/docs/superpowers/plans/2026-11-13-wacd-2-registrations-gateway.md`.
`k8s/registrations/apps.yaml` ships with `apps: []`; the ApplicationSet is
delivered by `k8s/argocd-apps/registrations.yaml`; `src/lab/registrations.py`
`problems()` ties each registration to its policy target, RBAC, AppProject,
namespace and credential and requires no leftover `k8s/argocd-apps/<app>.yaml`;
the registry GC keeps 10 deploy charts per app and every image they list.

Plan 2 fix-wave changes (2026-11-13): `release` pins the Application's
`targetRevision` to the deploy chart's manifest digest, not the tag (a tag in
the unauthenticated registry can be overwritten in-cluster); `status` maps the
digest back to the version through the ledger. Unproven on the cluster until
the travel pilot. The GC keeps images of pinned charts beyond the newest ten.

## Notes for Plan 4 (app cutovers)

- Proven 2026-11-13 23:59Z: Argo CD v3.5.5 renders `wac-app` 1.0.0 from
  `oci://registry.network.svc.cluster.local:5000/charts/wac-app` (`path: "."`)
  with a repository Secret `type: oci`, `insecureOCIForceHttp: "true"`.
- A chart gateway Target needs `workflows=("cd.yml",)` as well as `chart`; the
  gateway Role needs get and patch on each registered Application (baton has
  no patch today). The first Lab PR retires any test asserting `apps: []`.
- wac-cd creates the GitHub Release after any successful deploy (release,
  resume or rollback) when none exists for the tag (wacd-init ruling).
- wac-ci runs the implicit `npm ci` only when a root `package.json` exists;
  finance (packages in `app/`, `sync-agent/`, `.claude/skills/llc-month/ledger/`)
  lists `npm ci --prefix <dir>` in `ci.commands` (wacd-init ruling). App PR 1
  must also update repo-local CI tests that pin the old jobs (finance's
  `app/tests/node/ci-routing.test.ts`).
- Each app's registration PR sets its gateway Target's `chart="deploy/<name>"`
  and `workflows=("cd.yml",)`, adds get+patch on its Application
  (argoproj.io), get on its Deployment (apps) and the RoleBindings to
  `ci-deploy/ci-deploy`; `test_every_registration_is_fully_wired` enforces it.
  baton's registration PR adds its missing patch grant and deletes
  `k8s/platform/image-updater/baton.yaml`.
- Healed 2026-11-14 by mcp.argo #30's builds (argo-mcp:0.1.0 and 3a88295
  complete, 0 missing blobs). Original note: argo-mcp's registry images were broken: since the 19:15Z GC run on
  2026-11-13, tags `argo-mcp:1d2d64d…`, `25ba012c…`, `6da0a23…`, `e7bfe939…`
  reference blobs missing from the store; the running pod uses the node's
  cached copy, so a reschedule could fail to pull. The registry GC job exits
  non-zero at its pullability check until mcp.argo pushes a fresh image. Its
  cutover heals it: do not reuse any of those tags, and confirm the first
  wac-cd build pushes every layer. (wac.lab #521 fixed the separate
  `tags: null` crash.)
- baton's Argo CD Image Updater configuration must be removed at baton's
  cutover; `src/lab/registrations.py` `problems()` flags any ImageUpdater that
  matches a registered app.
- The travel pilot proves digest-pinned `targetRevision` for OCI charts on
  Argo CD v3.5.5. A ComparisonError there means the gateway goes back to tag
  pinning plus a drift alert (Plan 2's fallback).

- Plan 4 owns deleting `k8s/argocd-apps/wac-app-proof.yaml` and
  `COMPARE_ONLY_APPLICATIONS` (in `wac.lab/src/lab/k8s.py` and its tests),
  after travel's registration renders cleanly.
- wac-ci proves render parity (spec cutover step 1): App PR 1's `ci.yml` sets
  `parity_kustomize_dir: deploy/k8s`; App PR 2 removes it with `deploy/k8s`.
- `wac-cd` input `deploy: false` in App PR 1 (publish only: chart and tag);
  App PR 2 removes it. Output `mode` is `publish` for those runs.
- wac's profile sets `health_expect_db_ok: false` (its health returns only
  `{sha}`).
- Each app rolls exactly once during cutover: parity ignores image tags, and
  the first deploy chart carries a new `wac-cd` build (`<app>:<version>@sha256:…`)
  where the live pod runs `<app>:<sha>`. The PreSync migrate Job re-runs (must
  be a no-op); Recreate apps (finance) take a brief outage. The spec's "with
  parity this rolls nothing" is wrong on this point.
- When that rollout happens depends on how the ApplicationSet meets the old
  Application. If the root app prunes the old one and the ApplicationSet
  creates a fresh one, it has no automated sync and sits OutOfSync until App PR
  2's first `release` (the rollout is then at App PR 2). If the ApplicationSet
  adopts the existing Application, `ignoreApplicationDifferences` on
  `/spec/syncPolicy` may keep the old `automated` block, and it auto-syncs to
  `bootstrapRevision` at the Lab PR merge. Establish which on the travel pilot
  before scheduling finance and wealth.
- App PR 1 adds a separate `wacd-ci` job beside the existing ones; App PR 2
  cuts `ci.yml` down to the `wac-ci` call. Each repo's required status-check
  contexts change then: an owner settings step before App PR 2 merges.

- Once an app's gateway target gets a chart (cutover step 2), its old
  `ci.yml` sync/pin-status call is denied, so the old deploy job goes red
  until step 3 lands. Land steps 2 and 3 back to back, or make step 1's old
  job tolerate it.
- When an ApplicationSet takes over an Application of the same name that the
  root app created, the root's prune and the ApplicationSet's adoption can
  race on the tracking annotation. Check it on the travel pilot.
- Ingress names are preserved by the chart (Plan 1 fix wave); the cutover must
  set each app's live Ingress names in its values.

## Rules for every session

- **Never use docker on this Mac.** Its daemon is the lab cluster's Colima VM.
  Build tools as local processes if a test needs a registry
  (registry v2.8.3 built from source was used in Plan 1).
- Never run `kubectl`, `argocd`, `helm push`, or any command that changes
  cluster state. Read-only Argo CD MCP reads are fine.
- **Never push, open a PR, or merge without the owner.** Work on your own
  branch in your own worktree, commit locally, and stop before any push.
  Report what is ready.
- No comments in YAML, JSON, HCL, TOML, Helm templates or Dockerfiles. Reasoning
  goes in tests, commit messages, PR descriptions, or `docs/`.
- No emoji.
- Commit trailers: use the attribution lines your own session gives you.
- Process: invoke `superpowers:writing-plans` for your plan, save it as
  `/Users/willfell/Documents/GitHub/wac.lab.actions/docs/superpowers/plans/2026-11-13-wacd-<n>-<topic>.md`,
  then execute it with `superpowers:subagent-driven-development`
  (fresh implementer per task, task review after each, whole-branch review at
  the end). The owner chose subagent execution for this program.
- Do not open bump PRs in consumer repos as a side effect.

## Reporting back to wacd-init

Use `SendMessage` to `wacd-init`:

1. When your plan is written: the plan path, the task list in one line each,
   any interface you had to pin that the spec left open.
2. When you hit something only the owner can decide, or an interface conflict
   with another session: stop that thread and message at once.
3. When the plan is executed: branch, worktree, commit range, test evidence,
   the whole-branch review verdict, every `Ruling:` you made with its cost if
   wrong, and the exact owner steps needed (push, PR, merge order, any
   `lab k8s secrets sync`).

Peer sessions: `wacd-app-registrations` (Plan 2), `wacd-version-scripts`
(Plan 3), `wacd-plugin` (Plan 5), `wacd-app-cutovers` (Plan 4, not started
until `wacd-init` says go). You may message a peer directly to hand over an
interface (copy `wacd-init` on anything that changes a contract).

## Coordination with the tiered-plugins work (session pligins-init)

- `deploy-verify` and `lab-onboard-app` now live in `wac.lab` `plugin/lab/skills/`
  (`lab:` prefix); `suite-add-app` moves to `wac` `plugin/suite/skills/`
  (`suite:` prefix, after wac#109). `fleet-ci-routing` stays in `wac.plugins`.
- `plugins/wacd` stays in `wac.plugins` (team tier) and gains a dependency on
  the hosted `lab` plugin (pligins-init adds it in wac.plugins#70).
- Any edit to `plugins/wacd` in `wac.plugins`, such as onboard stage 7 after the
  travel pilot: message pligins-init before merging, and use the new skill names.
- wac.lab.actions v1.25.0 is `ci.apt_packages` (PR #62); the fleet-rules change
  is v1.26.0.
- All five skill moves are merged (wac.plugins main d9fcc28): `plugins/wacd`
  depends on `lab` and cites `lab:deploy-verify` and `lab:lab-onboard-app`.
  `lab:deploy-verify`'s route test invokes `wacd:onboard`'s discover stage
  rather than running another plugin's script.

## Coordinator rulings (wacd-init)

- Program Ruling: fleet rule 3 keeps "outside the bootstrap exceptions lab documents" (overturns Plan 3's ruling 9) — the spec meant to add the wac-cd route, not drop lab's bootstrap exceptions — cost if wrong: one clause
- Program Ruling: wac-cd gains a publish-only switch for cutover step 1 (requested from Plan 3) — the spec's step 1 publishes without releasing — cost if wrong: one input
- Program Ruling: profile gains an optional health-db flag for wac (requested from Plan 3) — wac passes health_expect_db_ok false today — cost if wrong: one optional field
- Program Ruling: no render-parity step in wac-ci; wacd:onboard's parity output goes in App PR 1 and is re-run before App PR 2 — avoids a third copy of the normalization — cost if wrong: parity not CI-enforced during the short cutover window
- Program Ruling (reverses the no-CI-parity ruling): wac-ci gets `parity_kustomize_dir` and scripts/wacd-parity.py, as the spec's cutover step 1 requires; normalization ported from feat/wac-app-chart >= 8612f031; wacd-plugin reuses it or drift-tests its copy — cost if wrong: a second normalization copy outside lab
- Program Ruling: wac-cd tags in the plan job before build, and a run whose tag push is refused after main moved past it is superseded (mode none, exit 0); no App token or new secret (v1.27.0, wacd-version-scripts). GitHub refuses a GITHUB_TOKEN ref push at a commit whose workflows differ from main, and travel run 43555058496 published deploy/travel:0.2.0 with no tag. Repaired by tagging v0.2.0 at 5e62767 with the owner's token. Cost if wrong: a superseded run's commits wait for the next run, and a failed build leaves a skipped version.
- Program Ruling: v1.27.0 is the cutover pin. v1.28.0 (wac-ci ci.go_version_file, mutually exclusive with ci.go_version) is tagged, but no consumer re-pins during the cutovers; the CONSUMERS.md walk runs after. Cost if wrong: one later bump PR per repo.
- Program Ruling: every non-feat merge publishes a patch (wac-cd's "anything else patch" rule) and is intended. Travel run 43562670671 (planned v0.2.1 for a ci: commit) was cancelled before build, leaving no artifact. Cost if wrong: a patch version per ci/docs merge.
- Program Ruling: baton's first deploy/baton chart waits for its next real product merge (baton's own versioning planned none for #1131, as designed); no no-op shipped-path change. Revisit if baton is the last cutover left without a chart. Cost if wrong: baton's cutover waits on product work.
- Repair log: wac app-v0.2.1 tagged at 26d243ed (deploy/wac:0.2.1 had no tag after #111 merged behind its run); travel v0.2.0 at 5e62767 likewise. Both were pushed with the owner's token, and neither prefix triggers a workflow.
- Follow-up (post-cutover): once baton registers, no Target uses the pin route; retire the gateway's pin `sync` path and its finance stand-in fixtures (wac.lab #527 moved them) in one wac.lab PR, or keep it with a named owner. Lab PRs staged as stacked drafts: finance #527 (0.1.1), wac #528 (0.2.1), wealth #529 (0.1.1), argo-mcp #530 (0.1.0).
- Pilot finding 1 (travel, wac.lab #522 a6c646cd): with root prune on, the old git Application is pruned and replaced by the ApplicationSet's (oci source, bootstrapRevision, auto-sync off); its resources are orphaned and taken over by tracking id with no rollout, no prune and no ComparisonError window. The repo-creds prefix for deploy/ resolves (digest pinned). The one rollout is App PR 2's first release. Stage 7 of wacd:onboard must say "replace", not "adopt".
- Pilot finding 2 (travel #104 4fa63bd4): release v0.3.0 is green end to end. claim=pushed, targetRevision pinned to the chart digest with automated sync on, PreSync migrate ran as a no-op, rolling update 1/1, served SHA verified, GitHub Release published. Post-cutover: DEPLOY_SSH_KEY and deploy key argocd-repo-travel deleted. The registry entries repo-travel/repo-travel-wacwini and the AppProject git sourceRepos are queued for removal in wac.lab. Travel is cleared for the plugin sessions. Stage 7 correction sent to wacd-plugin.
- Program Ruling: `lab k8s secrets sync` does not prune, so each cut-over app's argocd/repo-<app> and repo-<app>-wacwini Secrets stay live after their registry entries go. They hold a revoked key and are sourced by nothing. Deleting them is a by-hand cluster change, left to the owner and listed in the final report. Cost if wrong: two inert Secrets per app until the owner removes them.
- Stage 7 correction merged: wac.plugins #78 d6d5154c ("replace", not "adopt"; post-cutover cleanup row; floor v1.27.0; from v1.27.0 a tag means planned and a green run means published, which wacd:release also corrects).
- Pilot finding 3 (finance, wac.lab #527): the adopt-or-replace outcome is a race. Finance was adopted, keeping targetRevision main and auto-sync, so a ComparisonError ("deploy/finance:main: not found") blocks any sync; the live Deployment was unchanged and nothing was pruned. Auto-sync with prune is armed behind the error, so only the gateway release may change targetRevision, and App PR 2 follows promptly. Stage 7 amendment sent to wacd-plugin.
- Stage 7 amendment merged: wac.plugins #79 d4239ed9 (adopt or replace both accepted; prune-armed warning; ComparisonError expected when adopted).
- Pilot finding 4 (finance #191 0baf04ee): v0.2.0 is green end to end. The adopted Application kept its history; the gateway digest patch cleared the ComparisonError and synced. The Recreate outage was about 17s, with the init-container migrate and PVC kept. Post-cutover: DEPLOY_SSH_KEY plus both deploy keys deleted, and ruleset 20613204's DeployKey bypass removed (required checks are the six wacd-ci contexts). Cleared for the plugin sessions. Owner item: argocd/repo-finance{,-wacwini} stay live.
- Pilot finding 5 (wac #114 6b79a7f1): app-v0.3.0 is green end to end. The Application was adopted, as finance's was, and resolved at the digest patch; rolling update, no outage. Post-cutover: DEPLOY_SSH_KEY plus deploy keys wac-deploy and argocd-repo-wac deleted (the package release workflows use GITHUB_TOKEN only). Cleared for the plugin sessions. Owner item: argocd/repo-wac{,-wacwini} stay live. wac.lab #533 (finance git-cred cleanup) merged at 3ea500b5.
- Owner item: travel, wac and wealth have no rulesets. After the cutover, a main push runs wac-cd with no test gate, so a PR merged red would ship. Recommend a ruleset requiring the six wacd-ci contexts, as finance has. This is left to the owner because it changes how they merge. wac.lab #529 (wealth registration) merged at 6665dc05.
- Pilot finding 6 (wealth #79 cc51ed2c): v0.2.0 is green end to end. Replaced, like travel. Both images built (wealth-app, wealth-garmin); the CronJob moved to the new garmin image; PVC wealth-garmin-tokens kept on the same volume; PreSync plus init migrate ran; no outage. Post-cutover: DEPLOY_SSH_KEY and deploy key argocd-repo-wealth deleted, and wealth cleared. Owner item: argocd/repo-wealth{,-wacwini} stay live. wac.lab #534 (wac git-cred cleanup) merged at ccc8d840.
- Pilot finding 7 (argo-mcp #31 5abff2ff): v0.2.0 is green end to end. Replaced. initiated_by read "automated", because auto-sync raced the gateway's operation; same result, and wacd:release amended. Post-cutover: DEPLOY_SSH_KEY and both deploy keys deleted, and mcp.argo cleared. Owner item: argocd/repo-argo-mcp-wacwini stays live. wac.lab #535 (wealth git-cred cleanup) merged at ac4fec27. Wealth's garmin poll succeeded on the new image at 19:17Z.
- Program Ruling: baton's cutover waits for its next product merge; this program does not merge #1124 or #1125 (Claude-authored, unreviewed, not ours). Lab PR, App PR 2 and the ruleset swap are staged. Cost if wrong: baton stays on its old pin route until the owner merges a product PR.
- wacd:release Verify amended for an auto-sync-initiated release (wac.plugins #80 426456d6). CONSUMERS.md walk merged (wac.lab.actions #65). The cut-over six are at v1.27.0; the rest of the fleet is at v1.22.0 to v1.26.0, and no consumer is at v1.28.0 yet.
- Owner decision (2026-11-16): the owner authorized merging baton #1124 and #1125, which overrides the waits-for-product-merge ruling. Each was updated with main and merged only on a fresh green run, including the wacd-ci contexts: #1124 0bf99821 and #1125 5a24020a. Their cd runs published deploy/baton 1.37.3 and 1.37.4, tagged v1.37.3 and v1.37.4 at those commits, with no tag loss on v1.27.0.
- Pilot finding 8 (baton, wac.lab #537 15c5dce8): the Application was adopted. At adoption, auto-sync started one operation from the old git source (deploy/k8s at 5a24020a) without the image-updater override, so it rendered deploy/k8s's own pin, the 2026-10-16 image sha256:2df8f984. That image had been garbage-collected, so the PreSync migrate hook sat in ErrImagePull and the op could not reach the Deployment. An app whose old image still existed would have rolled back a month. Stage 7 should check, before the lab PR, that no auto-sync is pending on the old Application and that the deploy/k8s pin matches the live image.
- Program Ruling: no terminate-op on baton's stuck operation (a hand cluster write); let it fail out instead. The hook's activeDeadlineSeconds 600 and the op's retry limit 5 bounded it, and it ended Failed at 19:36:11Z with the image unchanged. The gateway sends its /operation sync only while the operation slot is empty, so App PR 2 waited for it. Cost if wrong: about an hour of delay, nothing more.
- Owner item applied: ruleset 24908590 (baton) now requires checks plus the six wacd-ci contexts. The DeployKey bypass was dropped after deleting key 165380700, the only deploy key and read-only. Baton has no Actions or environment secrets.
- Pilot finding 9 (baton #1134 efaaea75): the cutover merge itself planned none, because baton's caller versioning bumps only for shipped paths and #1134 changed workflows, deploy/ and docs. This is owner item (a) applying to the cutover. Program Ruling: the first release is a workflow_dispatch of cd.yml with version=1.37.4, the chart built from 5a24020a; main has no shipped-path change since, so it deploys the running code. Run 43942844670 went green: digest sha256:872aae70 pinned, automated, Synced and Healthy, initiated_by the run, migrate on the new image, rolling 2/2 with no outage, /api/health sha 5a24020a release 1.37.4. Cost if wrong: none; the served code is unchanged.
- Baton cleared (wac.lab #539 merged; "clear: baton" sent to pligins-init). Owner item: argocd/repo-baton{,-wacwini} stay live, which makes eleven inert repo Secrets fleet-wide.
- Owner decision (2026-11-16, "do it"): rulesets created on wac.app.travel (27420127), wac (27420129), wac.app.wealth (27420131) and mcp.argo (27420132). Each copies finance's 20613204: PR required, the six wacd-ci contexts, no bypass. Checked first: every repo's last merged PR reports all six contexts, wac-ci runs on pull_request with no path filter, and the only no-PR commits on main are pin commits from the retired deploy workflows, the newest dated 2026-11-14.
- Incident (2026-11-17, about 00:04Z): a `lab host vm restart` brought the VM up without its Vault data disk, and k3s started an empty datastore. The owner restored it, and the real cluster (node age 107 days) returned with all six chart apps Synced and Healthy at their digests. Jobs queued during the outage drained once the ARC listeners re-registered; nothing needed re-running except one lost-runner job (wac.plugins #82 codex-compat, re-run green). All merges waited for the restore.
- Gateway pin route retired: wac.lab #544 (222f8a4a). It removes pin status and sync, `pin_sha`, `app_status`, `verify_release` and the finance stand-in fixtures. A Target with neither a chart nor Lab's migration workflow is denied. The gateway rolled to ci-deploy 0.1.0-24. The next warehouse-image run's `migrate` succeeded, and its deliberate replay and caller-target denial probes were denied as designed. Follow-up, not done: wac.lab.actions still ships `lab-gitops-deploy`'s `oidc` route, `lab-release`'s `pin_sha` input and `ci-release.py`'s pin requests, which no consumer uses. Retiring them is a library change with its own CHANGELOG row.
- Image updater removed: prep wac.lab #545 (resources-finalizer, crds.keep false) merged and synced first, then #543 (4fd86972). Live evidence before the merge: no ImageUpdater CRs, no annotated Applications, the controller logged "No ImageUpdater CRs to process", and it had no Secrets. The cascade deleted the controller Deployment, ServiceAccount, ConfigMaps, Roles, RoleBindings, ClusterRole and ClusterRoleBinding. The CRD imageupdaters.argocd-image-updater.argoproj.io survived because it never carried an Argo tracking annotation. It holds no objects. Owner item: delete it by hand.
- Baton tidy: baton #1135 removes `release-version.mjs publish` and `verify-release-health.mjs`, neither of which had a caller. Owner item: baton's own board project config cannot be read without a Cloudflare Access sign-in. Read it with `baton board --json | jq .result.project` and, if it still names deploy.yml, repoint it with `baton project update BATON --deploy-job '.github/workflows/cd.yml#cd / deploy'` plus the required checks `checks` and the six wacd-ci contexts.
- CONSUMERS walk to v1.28.0: 13 repos merged, re-rendering AGENTS.md where v1.26.0's fleet-rules change required it. Deploys on merge, all green: fellhoelter-consulting, will-fell, jack-creek-patch (dev), and wac.lab.remote's update on the mini. wac.lab #542's warehouse, knowledge and Cloudflare apply ran on main. wac.fantasy #2 stays open, because its `techdocs / gate` (check-docs-nav.sh) fails on its default branch too, and the program merges only green PRs.
- Regression found: registry-gc in namespace network has failed every run since its last success at 2026-11-13T13:15Z, so oci-registry is Degraded and registry cleanup has been off since then. wac.lab #521 did not cure it. A diagnosis agent took it, with a safety constraint: no digest that an Application, a running pod or a pinned chart references may ever be deleted.
- Registry-gc root cause: argo-mcp's first push was untagged and was collected on 2026-11-13. registry:2.8.3's in-memory blob descriptor cache then kept answering HEAD 200 for the deleted layers, so later pushes skipped them and left 36 manifests pointing at missing blobs; the gc job's own integrity check then failed every run. Separately, a tag sharing a digest with a protected tag was not itself protected, which deleted baton's `main-<sha>` aliases.
- Registry-gc fixed: wac.lab #547 (d71a4c69) sets `REGISTRY_STORAGE_CACHE_BLOBDESCRIPTOR` empty, keeps every tag that shares a digest with a live tag or a pinned chart revision, and exits FATAL when a named tag's digest cannot be read. mcp.argo #32 (a622512a, the v1.28.0 bump) released argo-mcp v0.2.1, which re-pushed the missing layers. After it, a HEAD of every config and layer blob of every tag across the six chart apps and their deploy/ charts found 52 tags pullable and none missing.
- Lost rollback targets: baton's images main-c40858e0, main-05220f44, main-c208b4d7, main-65a24683 and main-0bf99821 were deleted by the alias hole, and three older ones are missing from before the logs read. The running baton (1.37.4) is unaffected; a rollback to those commits would need a rebuild.
- Rule breach (subagent): the registry-gc diagnosis agent ran throwaway containers on the Colima Docker daemon and mounted the registry volume read-only, against the rule never to use docker on this Mac. It wrote nothing to the cluster or the registry. Reported to the owner; future briefs that touch the registry name the rule explicitly.
- Program close-out (2026-11-17): all six chart apps release through wac-cd and the gateway, the pin route is gone, the image updater is gone, and every cut-over repo has a ruleset. Owner items left: the eleven inert argocd/repo-<app> Secrets; the orphaned CRD imageupdaters.argocd-image-updater.argoproj.io; baton's board project config; wac.fantasy #2, waiting on its default branch's techdocs gate. Open follow-up: retire the library's pin-route client (`lab-gitops-deploy` `oidc`, `lab-release` `pin_sha`, `ci-release.py` pin requests) with its own CHANGELOG row and tag.
