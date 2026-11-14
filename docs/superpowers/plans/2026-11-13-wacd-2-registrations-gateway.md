# wacd Plan 2: registrations, gateway `release`, registry retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship, with nothing live changing at merge, the mechanism that lets a registered app run a published deploy chart: an ApplicationSet over `k8s/registrations/apps.yaml`, a gateway `release` operation that points the app's Application at a chart version, and registry retention that keeps deploy charts and the images they name.

**Architecture:** `apps.yaml` ships with `apps: []`. An ApplicationSet (matrix of a git-files generator and a list generator over `.apps`) renders one sync-less OCI Application per entry, ignoring `targetRevision` and `syncPolicy` so the gateway owns both. The gateway gains a per-target `chart`; a chart target admits only `cd.yml` calling `wac-cd.yml` at a release tag, and only `release` and pin-less `status`. A lab test ties every registration to its policy target, RBAC, AppProject and credential, so a registration cannot land half-wired. The registry GC orders `deploy/*` tags by semver, never reclaims an unregistered deploy chart, and treats every image a retained chart names as live.

**Tech Stack:** Python 3.12, httpx, PyJWT, sqlite3, pytest via `uv`, PyYAML, POSIX sh (busybox in the GC pod), Argo CD v3.5.5 ApplicationSet.

**Spec:** `/Users/willfell/Documents/GitHub/wac.lab.actions/docs/superpowers/specs/2026-11-13-wacd-design.md` (sections Registrations, Gateway changes, Registry, Known costs and risks). The contract this plan implements is already written: `docs/ci-deploy-contract.md`, section "Chart releases (wac-cd)", commit `01f212ad`. It is the authority for every request, response, identity rule and denial reason below; read it before Task 1.

**Repo and branch:** `wac.lab`, worktree `/Users/willfell/Documents/GitHub/wac.lab-worktrees/wacd-registrations`, branch `feat/wacd-registrations` from `feat/wac-app-chart`. All paths are relative to it. Never commit to `feat/wac-app-chart`; never push.

## Global Constraints

- No comments in YAML, JSON, Helm templates or Dockerfiles, including new shell comments inside YAML block scalars. Leave the existing comments in `registry-gc-cronjob.yaml` untouched. Reasoning goes in test docstrings and commit messages.
- No emoji or glyph icons.
- Never run `docker`, `kubectl`, `argocd`, `helm push`, or anything that changes cluster state. Read-only Argo CD MCP reads are fine.
- Merging this branch must change nothing live: `apps.yaml` holds `apps: []`, no `Target` gets a `chart`, no `k8s/argocd-apps/<app>.yaml` is deleted, no RBAC grant is removed.
- Push registry `registry.network.svc.cluster.local:5000`; pull address `localhost:30500`. Never collapse them.
- Deploy chart URL: `oci://registry.network.svc.cluster.local:5000/deploy/<name>`, `path: "."`, no `chart` key (`test_oci_helm_chart_sources_carry_the_chart_in_the_url_and_path_dot`).
- No Application carries inline Helm values.
- The admitted reusable identity is exactly `willfell/wac.lab.actions/.github/workflows/wac-cd.yml@refs/tags/vX.Y.Z`.
- Version pattern everywhere: `^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`.
- Existing pin-mode `sync` and `status` behave byte-for-byte as before for every target without a `chart`.
- The gate: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest -q`.

## Review Focus

1. A token from `wac-cd.yml` at a branch or SHA ref, from a fork owner's `wac.lab.actions`, or with `job_workflow_sha` missing: denied, even though every other claim is right. (Task 1)
2. A pin-mode app sending exactly today's requests keeps working, and a chart-mode app sending `sync` or `pin_sha` is denied. (Task 1)
3. A version re-pushed under a different digest after its first release: denied on every later `release`, and `status` keeps reporting the first digest. (Task 2)
4. A `release` while Argo CD's operation slot is busy: the revision and automated sync are patched, no `/operation` is added, and the call succeeds with `sync_requested: false`. (Task 2)
5. A deploy chart published before its app is registered (no Application names `deploy/<app>` yet): GC reclaims none of its tags and none of the images it names. (Task 4)

---

### Task 1: Policy admits chart targets through `wac-cd.yml` only

**Files:**
- Modify: `src/lab/cideploy/policy.py`
- Modify: `tests/test_cideploy_policy.py`
- Modify: `tests/test_cideploy_manifests.py` (`test_transferred_application_sources_match_gateway_identity_and_credentials` skips targets with a `chart`)

**Interfaces:**
- Produces in `policy.py`:
  - `Target.chart: str = ""` (last field). Non-empty means release mode, e.g. `"deploy/travel"`.
  - `VERSION: re.Pattern` = the version pattern above; `REUSABLE_CD: re.Pattern` = `willfell/wac\.lab\.actions/\.github/workflows/wac-cd\.yml@refs/tags/v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)`, both used with `fullmatch`.
  - `Authorization.operation: Literal["sync", "status", "migrate", "release"]`; new trailing fields `version: str = ""`, `event: str = "push"` (the verified `event_name`).
- `TARGETS` is unchanged: no entry gets a `chart` in this plan.

- [ ] **Step 1: Write the failing tests.** In `tests/test_cideploy_policy.py`, add a fixture `chart_target` that monkeypatches `lab.cideploy.policy.TARGETS` to replace travel's entry with `Target("1354136417", "wac.app.travel", "travel", "travel", ("travel-app",), ("cd.yml",), chart="deploy/travel")`, and a fixture `cd_claims` derived from `claims` for travel: `workflow_ref` `wacwini/wac.app.travel/.github/workflows/cd.yml@refs/heads/main`, `job_workflow_ref` `willfell/wac.lab.actions/.github/workflows/wac-cd.yml@refs/tags/v1.24.0`, `job_workflow_sha` `"d" * 40`, matching `sub`, `repository`, `repository_id`. Tests:

```python
@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_chart_target_releases_through_the_reusable_cd_workflow(...):
    auth = policy.authorize(encode(cd_claims | {"event_name": event}, key), {"operation": "release", "version": "1.4.0"})
    assert (auth.operation, auth.version, auth.event, auth.pin_sha) == ("release", "1.4.0", event, "")
    assert auth.target.chart == "deploy/travel"

def test_chart_target_status_takes_no_pin(...):
    # {"operation": "status"} admitted; {"operation": "status", "pin_sha": "b"*40} denied

@pytest.mark.parametrize("job_ref", [
    "willfell/wac.lab.actions/.github/workflows/wac-cd.yml@refs/heads/main",
    "willfell/wac.lab.actions/.github/workflows/wac-cd.yml@" + "d" * 40,
    "willfell/wac.lab.actions/.github/workflows/wac-cd.yml@refs/tags/v1.24.0-rc.1",
    "willfell/wac.lab.actions/.github/workflows/wac-cd.yml@refs/tags/v01.2.3",
    "attacker/wac.lab.actions/.github/workflows/wac-cd.yml@refs/tags/v1.24.0",
    "willfell/wac.lab.actions/.github/workflows/wac-ci.yml@refs/tags/v1.24.0",
    "wacwini/wac.app.travel/.github/workflows/cd.yml@refs/heads/main",
])
def test_chart_target_denies_any_other_job_workflow(...):   # Denied, message "release requires the wac-cd reusable workflow"

def test_chart_target_denies_missing_job_identity(...)      # drop job_workflow_ref; then drop job_workflow_sha; then sha "D"*40: each Denied
def test_chart_target_denies_an_environment_claim(...)      # environment "production": Denied
def test_chart_target_denies_another_caller_file(...)       # workflow_ref ci.yml: Denied "workflow claim mismatch: workflow_ref"
def test_chart_target_denies_pull_requests_and_other_events(...)  # event_name pull_request, schedule: Denied

@pytest.mark.parametrize("body", [
    {"operation": "sync", "pin_sha": "b" * 40},
    {"operation": "status", "pin_sha": "b" * 40},
    {"operation": "release"},
    {"operation": "release", "version": "1.4"},
    {"operation": "release", "version": "v1.4.0"},
    {"operation": "release", "version": "01.4.0"},
    {"operation": "release", "version": 140},
    {"operation": "release", "version": "1.4.0", "application": "wac"},
    {"operation": "migrate", "image_tag": "x", "image_digest": "sha256:" + "c" * 64},
])
def test_chart_target_admits_only_release_and_bare_status(...)  # Denied

def test_pin_target_cannot_release(...)                      # finance, ci.yml, {"operation":"release","version":"1.0.0"}: Denied
def test_pin_target_still_denies_the_reusable_cd_identity(...)  # finance ci.yml token with the wac-cd job identity: WorkflowContextDenied
```

The existing parametrized tests over `TARGETS` must keep passing unchanged; that is Review Focus 2's other half.

- [ ] **Step 2: Run and see them fail.** `uv run pytest tests/test_cideploy_policy.py -q`. Expected: FAIL (`Target` has no `chart`).

- [ ] **Step 3: Implement in `policy.py`.** In `authorize`, branch on `target.chart` after the shared claim checks:
  - chart target: events `{"push", "workflow_dispatch"}`; still require `workflow_ref` in the target's trusted set and `workflow_sha == sha`; deny `environment` with `WorkflowContextDenied`; require `REUSABLE_CD.fullmatch(job_workflow_ref)` and `SHA.fullmatch(job_workflow_sha)` or raise `Denied("release requires the wac-cd reusable workflow")`; body exactly `{"operation", "version"}` with `operation == "release"` and `VERSION.fullmatch(version)`, or exactly `{"operation": "status"}`, else `Denied("request must contain only operation and version")`.
  - otherwise: the current code path, unchanged, plus `release` is not an admitted operation.
  Keep the replay `consume` as the last check before returning.

- [ ] **Step 4: Update the manifests test** so `active` is `[t for t in TARGETS if t.application not in ("", "baton") and not t.chart]`; leave its `len(active) == 5` assertion (still true).

- [ ] **Step 5: Run.** `uv run pytest tests/test_cideploy_policy.py tests/test_cideploy_manifests.py -q`, `uv run mypy`, `uv run ruff check .`. Expected: PASS.

- [ ] **Step 6: Commit.** `git commit -m "feat(ci-deploy): admit chart targets through wac-cd.yml at a release tag"` (body: the identity table from the contract, and that no target has a chart yet).

### Task 2: Backend `release` and chart `status`, with the digest ledger

**Files:**
- Modify: `src/lab/cideploy/backend.py`, `src/lab/cideploy/server.py`
- Modify: `tests/test_cideploy_backend.py`, `tests/test_cideploy_transport.py`

**Interfaces:**
- Consumes: Task 1's `Target.chart`, `Authorization.version`, `Authorization.event`, `VERSION`.
- Produces in `backend.py`:
  - `@dataclass(frozen=True) class ChartRelease: version: str; digest: str; source_sha: str`
  - `verify_chart(auth: Authorization, registry: httpx.Client) -> ChartRelease`: `GET /v2/<chart>/manifests/<version>` with `Accept: application/vnd.oci.image.manifest.v1+json`, body bounded at 1 MiB; 404 is `Denied("chart version is not published")`; digest is `sha256:` + sha256 of the body and must equal any `Docker-Content-Digest` header; config media type must be `application/vnd.cncf.helm.config.v1+json`; the config blob is fetched by digest and verified like `verify_image` does; config `name` must be the chart's last path segment, `version` the requested version, and `annotations["org.opencontainers.image.revision"]` a 40-hex SHA, else `Denied("chart metadata does not describe the requested release")`. On `push`, the revision must equal `auth.source_sha`, else `Denied("chart source does not match the releasing commit")`.
  - `verify_revision_on_main(auth: Authorization, revision: str) -> None`: same GitHub client and `compare/<revision>...<head>` rule as `verify_release`, raising `Denied("release revision is not on the admitted main history")`.
  - `class ReleaseLedger: __init__(self, path: Path)`; `record(self, chart: str, release: ChartRelease) -> None` inserts the first digest for `(chart, version)` and raises `Denied("chart version was released under a different digest")` if one exists with another digest; `digest_for(self, chart: str, version: str) -> str` returns `""` when unrecorded. Table `released_charts(chart TEXT, version TEXT, digest TEXT NOT NULL, source_sha TEXT NOT NULL, PRIMARY KEY (chart, version))`. sqlite errors are not caught: they propagate and the server answers 503.
  - `chart_status(auth: Authorization, kube: Kubernetes, ledger: ReleaseLedger) -> dict[str, Any]` and `chart_release(auth: Authorization, kube: Kubernetes, release: ChartRelease, ledger: ReleaseLedger) -> dict[str, Any]`, returning exactly the contract's shapes. Both require the Application's `spec.source` to be `repoURL == f"oci://registry.network.svc.cluster.local:5000/{auth.target.chart}"` and `path == "."`, else `Denied("application source does not match the registered chart")`; a missing Application is `Denied("application is not onboarded")`.
- `app_status` raises `Denied("operation not admitted")` if handed a chart target, so the pin path can never patch an OCI Application.
- Produces in `server.py`: `Gateway.__init__(self, policy, kube, migrations, registry, ledger: ReleaseLedger)`; `request` routes `release` to `verify_release` -> `verify_chart` -> `verify_revision_on_main` -> `ledger.record` -> `chart_release`, chart-target `status` to `verify_release` -> `chart_status`, everything else as today. `main()` builds `ReleaseLedger(directory / "releases.sqlite3")`. The seven new reasons from the contract join `DENIAL_REASONS`.

The patch `chart_release` sends, in this order: `{"op": "test", "path": "/metadata/resourceVersion", "value": <rv>}`, `{"op": "replace", "path": "/spec/source/targetRevision", "value": <version>}`, `{"op": "add", "path": "/spec/syncPolicy/automated", "value": {"prune": True, "selfHeal": True}}` (or `add /spec/syncPolicy` with `{"automated": ...}` when the Application has no `syncPolicy`), and, only when `application.get("operation")` is falsy, `{"op": "add", "path": "/operation", "value": {"initiatedBy": {"username": f"github-{repository_id}-{run_id}"}, "sync": {"revision": <version>}}}`. No patch at all when the target revision, the automated block, `sync.status == "Synced"` and `sync.revision == release.digest` already hold. The response's status part reflects the Application as read before the patch, with `version` set to the released version.

- [ ] **Step 1: Write the failing tests** in `tests/test_cideploy_backend.py`, with a fake registry (`httpx.MockTransport` serving one manifest and config blob built in the test, Helm media types) and a fake Kubernetes like `test_app_operation_cannot_escape_repository_target`'s:

```python
def test_release_patches_revision_and_automated_sync_under_the_resource_version()
    # one PATCH; ops == [test rv "7", replace targetRevision "1.4.0", add automated {prune, selfHeal}, add operation sync "1.4.0"]
    # result["released"] == {"version": "1.4.0", "chart_digest": <digest>, "chart_source_sha": "a"*40, "previous_version": "0.1.0", "sync_requested": True}
def test_release_while_an_operation_runs_requests_no_sync()            # Review Focus 4: no /operation op, sync_requested False
def test_release_already_running_at_the_recorded_digest_sends_no_patch()
def test_release_creates_sync_policy_when_the_application_has_none()
def test_release_refuses_an_application_on_another_source()             # git repoURL, or deploy/wac for travel, or path "chart": Denied, no PATCH
def test_missing_chart_version_is_denied()                              # registry 404
def test_chart_metadata_must_describe_the_release()                     # wrong name, wrong version, missing/short revision, wrong media type, digest header mismatch, oversized body
def test_push_release_requires_the_chart_from_the_token_commit()        # revision "e"*40 on push: Denied; same chart on workflow_dispatch: verify_chart passes
def test_ledger_refuses_a_version_re_pushed_under_another_digest(tmp_path)  # Review Focus 3: record d1, record d2 -> Denied; digest_for still d1; survives a new ReleaseLedger on the same path
def test_ledger_rerecording_the_same_digest_is_idempotent(tmp_path)
def test_chart_status_reports_version_digest_and_automation()           # contract shape exactly: keys, chart_digest "" for an unrecorded bootstrap version, operation_phase from status.operationState.phase, no env values leaked
def test_pin_status_refuses_a_chart_target()                            # app_status(chart auth): Denied, no request made
def test_rollback_revision_must_be_on_main(monkeypatch)                 # compare status "diverged" or "behind" -> Denied; "ahead" or "identical" -> passes; reuse the GitHub fake from test_pin_and_source_must_belong_to_current_main
```

In `tests/test_cideploy_transport.py`:

```python
def test_contract_denial_reasons_are_logged_by_name()
    # every backtick-quoted reason in the contract's "The new denial reasons logged are" sentence is in DENIAL_REASONS
def test_release_routes_through_chart_verification_in_order()
    # Gateway with stub collaborators records call order: verify_release, verify_chart, verify_revision_on_main, ledger.record, chart_release
```

- [ ] **Step 2: Run and see them fail.** `uv run pytest tests/test_cideploy_backend.py tests/test_cideploy_transport.py -q`.
- [ ] **Step 3: Implement** the interfaces above. Keep the 1 MiB response bound and `follow_redirects=False` on every registry and Kubernetes call.
- [ ] **Step 4: Run.** `uv run pytest tests/test_cideploy_*.py -q`, `uv run mypy`, `uv run ruff check .`, `uv run ruff format --check .`. Expected: PASS, and every pre-existing cideploy test unchanged.
- [ ] **Step 5: Commit.** `git commit -m "feat(ci-deploy): release a deploy chart version and report chart status"` (body: digest ledger rule, the no-patch idempotency condition, that Argo CD v3.5.5 reports an OCI sync revision as the manifest digest so proof compares digests; note that the merge rebuilds the gateway image through `ci-deploy-image.yml` and `warehouse-image.yml` fires on `src/lab/**`).

### Task 3: Registrations, the ApplicationSet, credentials and AppProjects

**Files:**
- Create: `k8s/registrations/apps.yaml` (exactly `apps: []`)
- Create: `k8s/registrations/applicationset.yaml`
- Create: `k8s/argocd-apps/registrations.yaml`
- Modify: `k8s/secrets/registry.yaml` (one entry `repo-creds-lab-deploy-charts`)
- Modify: the six AppProjects in `k8s/platform/core/` (`travel`, `finance`, `wac`, `wealth`, `argo-mcp`, `baton`): append `oci://registry.network.svc.cluster.local:5000/deploy/<name>` to `sourceRepos`
- Modify: `tests/test_manifests.py` (`NON_MANIFEST_PATHS` gains `Path("k8s") / "registrations" / "apps.yaml"`); grep `src/` and `tests/` for any other walker of `k8s/**/*.yaml` and exclude it there too
- Create: `src/lab/registrations.py`, `tests/test_registrations.py`, `tests/fixtures/registrations/apps.yaml` (entries for `travel` and `finance`, bootstrap `0.1.0`)

**Interfaces:**
- Consumes: Task 1's `TARGETS` and `Target.chart`.
- Produces in `src/lab/registrations.py`:
  - `REGISTRATIONS: Path` = `k8s/registrations/apps.yaml`; `DEPLOY_CHART_PREFIX = "oci://registry.network.svc.cluster.local:5000/deploy"`.
  - `@dataclass(frozen=True) class Registration: name: str; project: str; namespace: str; chart: str; bootstrapRevision: str`
  - `load(path: Path = REGISTRATIONS) -> list[Registration]`: raises `ValueError` naming the entry on unknown or missing keys, `chart != f"deploy/{name}"`, a `bootstrapRevision` not matching `VERSION`, or a duplicate name.
  - `render(registration: Registration) -> dict`: the Application the ApplicationSet template produces, by substituting `{{.field}}` in `applicationset.yaml`'s `spec.template`; raises `ValueError` on any other template expression.
  - `problems(registration: Registration, root: Path) -> list[str]`: every wiring gap, empty when fully wired. Checks: an AppProject named `project` exists, lists `f"{DEPLOY_CHART_PREFIX}/{name}"` in `sourceRepos` and `namespace` in `destinations`; a Namespace `namespace` exists; exactly one `TARGETS` entry has `application == name`, and it has `chart == registration.chart`, `namespace == registration.namespace`, `workflows == ("cd.yml",)`; the `ci-deploy-gateway` Role in `argocd` grants `get` and `patch` on Application `name`, and the one in `namespace` grants `get` on each of the target's deployments; no file under `k8s/argocd-apps/` defines an Application named `name`; the `repo-creds-lab-deploy-charts` registry entry exists.

`applicationset.yaml`: `ApplicationSet` `registrations` in `argocd`; `goTemplate: true`; `goTemplateOptions: ["missingkey=error"]`; generator `matrix` of `git` (`repoURL` the lab SSH URL `git@github.com:wacwini/wac.lab.git`, `revision: main`, `files: [{path: k8s/registrations/apps.yaml}]`) and `list` (`elementsYaml: "{{ .apps | toJson }}"`); `ignoreApplicationDifferences: [{jsonPointers: [/spec/source/targetRevision, /spec/syncPolicy]}]`; `syncPolicy: {preserveResourcesOnDeletion: true}`; template `metadata: {name: "{{.name}}", namespace: argocd}`, `spec.project: "{{.project}}"`, `spec.source: {repoURL: "oci://registry.network.svc.cluster.local:5000/{{.chart}}", path: ".", targetRevision: "{{.bootstrapRevision}}"}`, destination in-cluster `"{{.namespace}}"`, `syncPolicy: {syncOptions: [CreateNamespace=false]}`. No `automated`, no `helm`, no finalizers.

`k8s/argocd-apps/registrations.yaml`: Application `registrations`, project `default`, source the lab SSH URL at `main`, `path: k8s/registrations`, `directory: {include: applicationset.yaml}`, destination `argocd`, automated prune and self-heal.

Registry entry: name `repo-creds-lab-deploy-charts`, namespace `argocd`, label `argocd.argoproj.io/secret-type: repo-creds`, keys exactly `name: lab-deploy-charts`, `type: oci`, `url: oci://registry.network.svc.cluster.local:5000/deploy`, `insecureOCIForceHttp: "true"`, all literal values.

- [ ] **Step 1: Write the failing tests** in `tests/test_registrations.py`:

```python
def test_the_registrations_ship_empty()                    # load() == []; set(t.application for t in TARGETS if t.chart) == set()
def test_chart_targets_are_exactly_the_registrations()     # set of chart-target applications == {r.name for r in load()}
def test_every_registration_is_fully_wired()               # for r in load(): problems(r, ROOT) == []
def test_problems_names_every_gap_for_an_unwired_entry()   # fixture travel: problems name the policy target without a chart and the existing k8s/argocd-apps/travel.yaml; a copy with project "nope": also names the missing AppProject
def test_load_rejects_malformed_entries(tmp_path)          # unknown key, chart "deploy/other", bootstrapRevision "v0.1.0", duplicate name: ValueError naming the entry
def test_the_template_renders_a_sync_less_oci_application()
    # render(fixture travel) == {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
    #   "metadata": {"name": "travel", "namespace": "argocd"},
    #   "spec": {"project": "travel",
    #            "source": {"repoURL": "oci://registry.network.svc.cluster.local:5000/deploy/travel", "path": ".", "targetRevision": "0.1.0"},
    #            "destination": {"server": "https://kubernetes.default.svc", "namespace": "travel"},
    #            "syncPolicy": {"syncOptions": ["CreateNamespace=false"]}}}
def test_the_gateway_owns_revision_and_sync_policy()       # ignoreApplicationDifferences pointers exactly the two; preserveResourcesOnDeletion; missingkey=error
def test_the_generator_reads_the_registrations_file_from_main()
def test_the_registrations_application_delivers_only_the_applicationset()
def test_one_credential_template_covers_every_deploy_chart()  # entry exactly as above, no refs; every render()'s repoURL starts with url + "/"
def test_every_app_project_admits_its_deploy_chart()       # the six projects
```

The docstring of `test_the_gateway_owns_revision_and_sync_policy` carries the spec's reason: a recreated Application sits idle at its bootstrap version instead of rolling out a stale chart and its old migration. The docstring of `test_one_credential_template_covers_every_deploy_chart` records that Argo CD v3.5.5 matches `repo-creds` by longest URL prefix and copies `type`, `enableOCI` and `insecureOCIForceHttp` into a repository it has no Secret for (`util/db/repository_secrets.go`, `CopyCredentialsFrom`), and that the cluster has not yet proved it.

- [ ] **Step 2: Run and see them fail.** `uv run pytest tests/test_registrations.py -q`.
- [ ] **Step 3: Implement** the files and `src/lab/registrations.py`.
- [ ] **Step 4: Run the whole gate.** `uv run pytest -q` (the manifest walker, the AppProject and Application tests must all pass with the new files), `uv run mypy`, `uv run ruff check .`, `uv run ruff format --check .`.
- [ ] **Step 5: Commit.** `git commit -m "feat(registrations): ApplicationSet over k8s/registrations/apps.yaml, shipped empty"` (body: the matrix generator choice, the two ignored pointers, the credential template and that the owner runs `uv run lab k8s secrets sync` after merge, that nothing is registered yet).

### Task 4: Registry retention keeps deploy charts and their images

**Files:**
- Modify: `k8s/platform/registry/registry-gc-cronjob.yaml` (the `untag` init container script only)
- Modify: `tests/test_registry_retention.py`

**Interfaces:**
- Consumes: the contract's `wacwini.com/images` annotation in each deploy chart's Helm config blob.
- Produces in the script (shell functions, defined before the `for repo in $repos` loop, no new comments):
  - `KEEP_DEPLOY_CHARTS=10`
  - `deploy_candidates` reading tags on stdin, one per line: prints the semver-shaped tags (`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`) other than the newest `KEEP_DEPLOY_CHARTS` by numeric semver order; never prints a non-semver tag.
  - `deploy_chart_images REPO TAG`: fetches the manifest with `Accept: application/vnd.oci.image.manifest.v1+json`, takes its config digest, fetches that blob, prints every `localhost:30500/<path>:<tag>@sha256:<64 hex>` it contains; returns non-zero if either fetch is not HTTP 200.
- Behavior: before the main loop, for every `deploy/*` repository and every tag not in `deploy_candidates`, the images printed by `deploy_chart_images` are added to `live_tags` and `live_digests` exactly as pod references are; a failure prints `FATAL` and exits 1. In the main loop a `deploy/*` repository is never treated as unreferenced: its candidates are `deploy_candidates`' output, and each still passes the existing per-tag guards (Application `chart_revs`, live tags, digests) before any DELETE.

- [ ] **Step 1: Write the failing tests** in `tests/test_registry_retention.py`, executing the real script slices as the existing tests do (extract the functions by their names, stub `curl`):

```python
def test_deploy_charts_keep_the_newest_ten_by_semver()      # tags 0.1.0..0.12.0 in shuffled order: candidates == {"0.1.0", "0.2.0"}; 0.10.0 sorts after 0.9.0
def test_non_semver_deploy_tags_are_never_candidates()      # "latest", "1.0", "v1.0.0" never printed
def test_images_named_by_a_retained_deploy_chart_are_printed()  # stub manifest+config with the annotation for two images: both refs printed
def test_unreadable_deploy_chart_metadata_fails_closed()    # stub 500 on the config blob: non-zero
def test_an_unregistered_deploy_chart_is_not_reclaimed()    # Review Focus 5: drive the repo loop for "deploy/travel" with chart_repos empty: no "every tag is a candidate" line, no DELETE for its five tags
def test_an_application_pinned_deploy_chart_older_than_ten_survives()  # tag 0.1.0 in chart_revs with 12 tags: no DELETE of 0.1.0
```

Docstrings carry the reasons: semver, not manifest dates, because Helm's OCI manifests carry no created time; an unregistered chart is the normal state between cutover steps 1 and 2; images are protected through the chart because rollback must find them.

- [ ] **Step 2: Run and see them fail.** `uv run pytest tests/test_registry_retention.py -q`.
- [ ] **Step 3: Implement** in the script. The two existing tests (`charts/wac-app` exemption, unreferenced chart repository reclaimed) must still pass.
- [ ] **Step 4: Run.** `uv run pytest tests/test_registry_retention.py tests/test_manifests.py -q`; then `sh -n` on the extracted script. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "fix(registry): keep the last ten deploy charts per app and every image they name"`.

### Task 5: Records: AGENTS.md, the contract's RBAC paragraph, the hand-offs

**Files:**
- Modify: `AGENTS.md` (route table row for a registered app's release; Layout entry for `k8s/registrations/` and `src/lab/registrations.py`)
- Modify: `docs/ci-deploy-contract.md` (the "Deployment and credentials" paragraph: the gateway now also reads `deploy/*` charts from the registry and keeps `releases.sqlite3` on its state volume; patch and get grants follow `apps.yaml`, enforced by `tests/test_registrations.py`)

- [ ] **Step 1: Edit both files.** The AGENTS.md route row: "A registered app's release | `wac-cd` in the app's repo | the gateway's `release` points the app's ApplicationSet-made Application at a published `deploy/<app>` chart version and turns on automated sync; nothing is committed". Do not touch AGENTS.md from `## Dependencies` on.
- [ ] **Step 2: Run** `uv run pytest -q` (the suite reads every Markdown file) and the AGENTS.md gate command listed in AGENTS.md's Commands section, if any. Expected: PASS.
- [ ] **Step 3: Commit.** `git commit -m "docs: record the chart release route and its registrations"`.

`wac-app-proof` and `COMPARE_ONLY_APPLICATIONS` are not deleted here: with no registration, nothing yet proves the `deploy/` path live. Their deletion is handed to Plan 4, once travel's registered Application reads its first deploy chart without a `ComparisonError`.

## Rulings

Each is a call this plan made where the spec left room; the cost is what a wrong call costs to undo.

1. Bodies carry `operation` (`release`/`status`) on the existing endpoint. Cost: a client rename, before any client ships.
2. A deploy chart is named after its Application (`deploy/argo-mcp`, not `deploy/mcp.argo`). Cost: one registration and one Plan 3 profile field.
3. The source SHA and image list travel as `Chart.yaml` annotations, read from the Helm config blob. Cost: Plan 3 and the gateway change together.
4. One `repo-creds` template for the `deploy` prefix, proved from Argo CD source, not yet on the cluster. Cost if wrong: travel's pilot shows a `ComparisonError` and Plan 4 adds a per-app `repository` Secret.
5. Policy targets stay in `policy.py`; a test ties them to `apps.yaml`. Cost: none live; a later refactor.
6. The release tag of `wac-cd.yml` is checked by format, not resolved against GitHub. Cost: a tag in `willfell/wac.lab.actions` could be admitted the moment it is pushed; only the owner can push tags there.
7. Ten deploy charts kept per app. Cost: kilobytes per chart, plus their images, the real disk cost.
8. The ApplicationSet is delivered by an Application `registrations` in `k8s/argocd-apps/`, so `apps.yaml` is never applied as a manifest. Cost: one extra Application.
9. `wac-app-proof` deletion handed to Plan 4. Cost: one sync-less Application lives longer.
