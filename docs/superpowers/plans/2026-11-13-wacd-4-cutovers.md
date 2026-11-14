# wacd Plan 4: App Cutovers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move travel, finance, wac, wealth, mcp.argo (`argo-mcp`) and baton off `deploy/k8s` and the pin-commit deploy onto `wac-app` 1.0.0, `wac-ci` and `wac-cd` at `wac.lab.actions` v1.24.0, three PRs per app, travel first as the pilot.

**Architecture:** Every app runs the spec's three steps in order: App PR 1 (publish only), Lab PR (register), App PR 2 (deploy on, old route gone). The `wacd:onboard` skill (read from `/Users/willfell/Documents/GitHub/wac.plugins-worktrees/wacd-plugin/plugins/wacd/skills/onboard/SKILL.md`) is the procedure for each step; this plan fixes the order, the per-app values, the pilot questions and the gates between apps. The Lab PR and App PR 2 land back to back.

**Tech Stack:** GitHub Actions reusable workflows, Helm 4 OCI charts, Argo CD v3.5.5 ApplicationSet, kustomize, `uv`, `gh`.

**Spec:** `/Users/willfell/Documents/GitHub/wac.lab.actions/docs/superpowers/specs/2026-11-13-wacd-design.md`, section "Cutover, per app". Brief: `docs/superpowers/handoffs/2026-11-13-wacd-program.md`, "Notes for Plan 4".

## Global Constraints

- `wac.lab.actions` tag: `v1.24.0` (`6f5c1974`); every `willfell/wac.lab.actions` pin in an app repo moves to it in App PR 1, with the AGENTS.md tail re-rendered (fleet rule 3 text changed at v1.24.0).
- Chart: `wac-app` `1.0.0`, `--chart` from a `wac.lab` checkout at `origin/main`.
- `app` names: `travel`, `finance`, `wac`, `wealth`, `argo-mcp`, `baton`. `tag_prefix`: `v` (travel, finance, wealth, argo-mcp, baton continues `v1.37.x`), `app-v` (wac).
- Values never set `app`, `images`, `global`, a namespace, tag, digest or registry; `routes[].name` is each live Ingress name.
- `runner: ${{ vars.CI_RUNNER_STANDARD }}` in both callers. `cd.yml` permissions `contents: write, id-token: write, packages: read`; no `environment`.
- No comments in YAML/JSON/Dockerfiles. No emoji. Never docker, `kubectl` writes, `argocd`, `helm push`, Argo CD MCP write tools. Never force-push, never move a tag, never merge.
- Branches: app `wacd/cutover-1`, `wacd/cutover-2` in `../<repo>-worktrees/wacd-cutover`; lab `wacd/register-<app>` in `../wac.lab-worktrees/wacd-<app>`; each fresh from `origin/main`.
- Every PR: fresh-subagent review of the diff before the merge ask; message `wacd-init` the URL once checks are green; `wacd-init` merges.
- Before travel's Lab PR merges: message `wacd-init` "need secrets sync" and wait for "synced".

## Review Focus

1. A merge to the app's `main` between App PR 1 and the Lab PR publishes a newer version: `bootstrapRevision` is re-read from the highest `<prefix>X.Y.Z` tag immediately before each Lab PR merge ask.
2. A merge to the app's `main` between the Lab PR merge and App PR 2 is denied by the gateway: the freeze is stated in each Lab PR body and the two merges are asked for in one message.
3. The adoption race leaves the Application deleted, on git, or at `targetRevision: main`: read it after the Lab PR merges, before asking for App PR 2; stop and report on anything but the deploy chart owned by `registrations`.
4. A PreSync migration that is not a no-op on re-run, or a Recreate outage (finance), at the single rollout: schedule from the pilot's finding, and say when in the Lab PR body.
5. Required status-check contexts stranded by App PR 2: give `wacd-init` the old and new contexts (from `gh api repos/<o/r>/rulesets` and App PR 1's `gh pr checks`) before App PR 2's merge ask.

---

## Per-step procedure (used by every app task)

- **App PR 1:** onboard stages 1 to 5. Write `.github/wacd.json`, `deploy/values.yaml` (start from `wac.lab/tests/fixtures/wac_app/<app>/values.yaml`, remove `app` and `images`), `.github/workflows/cd.yml` with `deploy: false`, the `wacd-ci` job in `ci.yml` with `parity_kustomize_dir: deploy/k8s`, every pin to v1.24.0, the AGENTS.md tail re-rendered, repo-local CI tests updated. Verify: profile validator exit 0; `onboard.py parity` exit 0; repo's own tests; `agents-md.py check`. After merge: `cd` run green, `mode` `publish`, tag `<prefix><version>` exists.
- **Lab PR:** onboard stage 6. `k8s/registrations/apps.yaml` entry; `policy.py` Target `chart="deploy/<app>"`, `workflows=("cd.yml",)`; RBAC get+patch on the Application and get on the Deployment(s); AppProject source confirmed; delete `k8s/argocd-apps/<app>.yaml`; retire any test asserting `apps: []`. Verify: `uv run pytest -q` green.
- **App PR 2:** onboard stage 7. Parity re-run exit 0 at `main`; drop `deploy: false`, `parity_kustomize_dir`, `deploy/k8s`, the old deploy job and the jobs `wac-ci` replaces; update AGENTS.md, `deploy/README.md`, repo-local tests. Hand the contexts and the deploy-key removal commands to `wacd-init`.
- **Land:** Lab PR merge; read the Application (Argo CD read tools); App PR 2 merge; `wacd:release` Verify on the first release.

## Task 1: travel App PR 1

Repo `wacwini/wac.app.travel`. Image `travel-app`, `deploy/Dockerfile`, context `.`, `sha_build_arg: TRAVEL_BUILD_SHA`; health `http://travel-app.travel.svc.cluster.local:3000/api/health`; Ingress `travel-app-path`. `ci.commands`: `npm run check:emoji`, `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, `npm run build:worker`. The `npm view` pin check is not one line: open item in the PR body.

- [ ] Write files, run validator and parity (exit 0), commit, push, open PR, review, report URL.
- [ ] After merge: confirm `mode` `publish` and tag `v0.1.0` (or whatever the run created).

## Task 2: travel Lab PR

- [ ] Branch `wacd/register-travel`; procedure above with `bootstrapRevision` = travel's tag; `uv run pytest -q`; open PR; body carries the pilot questions, the freeze and the repo-creds note.

## Task 3: travel App PR 2 and landing

- [ ] Open App PR 2, green, parity re-run. Ask `wacd-init` for "need secrets sync"; wait for "synced". Re-read the highest tag. Ask for the Lab PR then App PR 2 merges in one message.
- [ ] Between the two merges, read Application `travel`: owner reference, source, `targetRevision`, `syncPolicy`, sync/health, root tracking annotation.

## Task 4: travel pilot findings

Report each to `wacd-init` with evidence as soon as known:
- [ ] Adoption or replacement; `targetRevision` and `syncPolicy` after the Lab PR merge; when the one rollout happened.
- [ ] The `deploy/` repo-creds template works (Application renders, no repository error), else propose a per-app repository Secret.
- [ ] Digest-pinned `targetRevision` renders and reads Synced (else stop; tell `wacd-init` before any lab change).
- [ ] PreSync `travel-migrate` re-run succeeded with no schema change; `wac-cd` served-SHA proof passed; `wacd:release` Verify items 1 to 4.

## Task 5: lab cleanup after the pilot

- [ ] `wac.lab` branch `wacd/retire-wac-app-proof`: delete `k8s/argocd-apps/wac-app-proof.yaml` and `COMPARE_ONLY_APPLICATIONS` in `src/lab/k8s.py` and its tests; `uv run pytest -q`; PR.

## Tasks 6 to 20: the other apps, three tasks each, in order

finance, wac, wealth, argo-mcp (repo `mcp.argo`), baton. Each app's values come from discovery at the time; known deltas:
- finance: no root `package.json`; `ci.commands` start with `npm ci --prefix app`, `npm ci --prefix sync-agent`, `npm ci --prefix .claude/skills/llc-month/ledger`; update `app/tests/node/ci-routing.test.ts` in both App PRs; Recreate strategy and PVCs, outage scheduled per Task 4's finding; no ruleset (no contexts to change).
- wac: `tag_prefix: app-v`, `health_expect_db_ok: false`.
- wealth: `health_expect_db_ok: false`; second image `wealth-garmin` (`garmin-poller/Dockerfile`, context `garmin-poller`); PVC continuity.
- argo-mcp: no live route (moved to `wac.ai.ag`); its e2e matrix stays in its own workflow.
- baton: `version_source: caller` with its computed version, extra `build_args`; Lab PR deletes `k8s/platform/image-updater/baton.yaml` and adds `baton` to the RBAC `patch` rule; App PR 2 drops the kustomize digest pin; heavy suites keep their workflows.

Finance and wealth are scheduled only after Task 4 says when the rollout happens.
