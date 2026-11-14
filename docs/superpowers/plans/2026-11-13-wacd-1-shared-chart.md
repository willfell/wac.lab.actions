# wacd Plan 1: the shared chart `wac-app` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, test and publish one Helm chart in `wac.lab` that renders all six live apps exactly as they deploy today, and prove Argo CD can run it from the in-cluster registry.

**Architecture:** `charts/wac-app` is a plain Helm chart with a closed values schema. Its tests are pytest, like the rest of `wac.lab`: each app has a values fixture and the kustomize render it must reproduce. A lab workflow publishes each chart version once to the in-cluster OCI registry. A sync-less proof Application shows Argo CD can pull and render it.

**Tech Stack:** Helm 3.16.3, pytest via `uv`, PyYAML, GitHub Actions on fleet runners, Argo CD, `registry:2.8.3`.

**Spec:** `wac.lab.actions/docs/superpowers/specs/2026-11-13-wacd-design.md`

## Where this plan sits

The spec covers five subsystems. Each gets its own plan, written when the one before it has landed:

1. **This plan**: shared chart, its publication, the Argo CD proof. Repo: `wac.lab`.
2. Registrations ApplicationSet, gateway `release` operation, registry retention. Repo: `wac.lab`.
3. `wac-ci.yml`, `wac-cd.yml`, their scripts, fleet rule 3. Repo: `wac.lab.actions`.
4. Cutovers: travel, finance, wac, wealth, mcp.argo, baton.
5. The `wacd` plugin and the four rewritten skills. Repo: `wac.plugins`.

All work in this plan happens in `/Users/willfell/Documents/GitHub/wac.lab`, in a worktree branched from `origin/main` (the local checkout sits on a stale branch). Every path below is relative to that repo.

## Global Constraints

- No comments in any YAML, JSON or Helm template, including `{{/* */}}`. Reasoning goes in tests and commit messages.
- No emoji or glyph icons anywhere.
- Never change cluster state by hand. Cluster changes are merges to `wac.lab` `main`.
- Images pull from `localhost:30500`; charts and images push to `registry.network.svc.cluster.local:5000` over plain HTTP. Never collapse the two.
- Workflow jobs use `runs-on: ${{ vars.CI_RUNNER_STANDARD }}` or `CI_RUNNER_SMALL`, never a literal label.
- A published chart version is never overwritten.
- Pod selectors and pod template labels are exactly `app: <name>`. Selectors are immutable, so anything else breaks cutover.
- PersistentVolumeClaim names render exactly as they exist today: `finance-data`, `wealth-garmin-tokens`.
- No Application carries inline Helm values (`test_no_application_carries_inline_helm_values`).
- The chart renders no `metadata.namespace`; the Application's destination sets it.

## Review Focus

1. A values file with a misspelt or unknown key: the render fails naming the key, it does not silently ignore it. (Task 1)
2. `image` or a cron job's `image` naming a key missing from `images`: the render fails with the missing key. (Task 1, Task 5)
3. An image entry with a digest: the reference renders `repository:tag@digest`; with a digest and no tag, `repository@digest`; with neither, the render fails. (Task 1)
4. A route with `path: /` and `stripPrefix: true`: the render fails; stripping the root is never meant. (Task 1)
5. `migrate.mode: none` with a `migrate.command` still present: renders no migration at all. (Task 4)

---

## The values contract

Every task implements part of this. It is the interface apps code against in Plans 3 and 4.

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string, required | Workload name: Deployment, Service, container, `app` label. `travel-app`, `baton`, `argo-mcp`. |
| `app` | string, required | Short name: prefix for Middleware (`<app>-stripprefix`) and migrate Job (`<app>-migrate`). `travel`. |
| `images` | map of `{repository, tag, digest}` | Written by `wac-cd`. Keyed by image name. |
| `image` | string, required | Key into `images` for the main container and migrations. |
| `args` | list | Container args. |
| `replicas` | int, default 1 | |
| `minReadySeconds` | int or null, default 5 | Null omits the field. The default lives in the Deployment template, not `values.yaml`, because on Helm 3.16 a parent's `null` does not override a subchart default. |
| `strategy` | `RollingUpdate` (surge 1, unavailable 0) or `Recreate` | |
| `ports` | list of `{name, port}` | First is the Service's main port. All are named on the container and Service. |
| `probes.readiness`, `.liveness`, `.startup` | `{path, port, initialDelaySeconds, periodSeconds, timeoutSeconds, failureThreshold}` | `port` is a port name. Unset probes are omitted; unset fields are omitted. |
| `resources` | Kubernetes resources block | |
| `env` | list of `{name, value}` or `{name, secret: {name, key, optional}}` | |
| `serviceAccount` | `{create, name, automountToken}` | `create: true` renders it at sync-wave `-1`. `name` without `create` references one lab owns. |
| `suiteToken` | bool | Projected token, audience `wac-suite-internal`, 3600s, at `/var/run/secrets/wac-suite`. |
| `podSecurityContext`, `securityContext` | Kubernetes blocks | |
| `priorityClassName`, `terminationGracePeriodSeconds`, `preStopSleepSeconds` | scalar | |
| `routes` | list | `{kind: ingress, name, hosts, path, stripPrefix}` (`name` optional, defaults to `<name>-<index>`; every cutover sets the live Ingress name) or `{kind: httpRoute, parent: {name, namespace}, path, rewriteTo}`. |
| `migrate` | `{mode, command, env, resources, activeDeadlineSeconds}` | `mode`: `none`, `init`, `presync`, `both`. |
| `cronJobs` | list of `{name, image, schedule, timeZone, env, resources, volumeMounts, activeDeadlineSeconds, backoffLimit, startingDeadlineSeconds}` | `concurrencyPolicy: Forbid`, history 1 success and 3 failed, fixed. |
| `persistence` | list of `{name, size, ignoreHealthcheck}` | Each claim renders with `Prune=false`. |
| `volumes` | list of `{name, claim}` or `{name, emptyDir: {sizeLimit}}` or `{name, secret: {secretName, items}}` | |
| `volumeMounts` | list | Main container mounts; `migrate.mode: init` shares claim mounts. |
| `serviceMonitor` | `{port, path, interval, scrapeTimeout}` | Rendered with label `release: kube-prometheus-stack` when set. |

## Parity, defined once

A fixture passes when the chart's render and the app's kustomize render contain the same set of `(kind, name)` objects and every pair is equal after this normalization, and nothing else:

- drop `metadata.namespace`;
- drop `metadata.labels` on the object itself (pod template labels and selectors still compare);
- replace every container `image` with its repository path, dropping tag and digest;
- resolve named ports to numbers in probes, Service `targetPort` and Ingress backends, and drop container port names;
- drop the migrate Job's container name and pod template labels;
- compare the `argocd.argoproj.io/hook-delete-policy` annotation as a set of comma-separated policies (baton writes them in the opposite order to travel and wealth; Argo CD ignores the order).

Any other difference is a chart bug or needs a line added to this list in the same commit, with the reason in the commit message.

---

### Task 1: Chart skeleton, schema, parity harness, travel

**Files:**
- Create: `charts/wac-app/Chart.yaml` (`name: wac-app`, `version: 0.1.0`, `type: application`)
- Create: `charts/wac-app/values.yaml`, `charts/wac-app/values.schema.json`
- Create: `charts/wac-app/templates/_helpers.tpl`, `deployment.yaml`, `service.yaml`, `serviceaccount.yaml`, `ingress.yaml`, `middleware.yaml`, `migrate-job.yaml`
- Create: `tests/wac_app/__init__.py`, `tests/wac_app/render.py`, `tests/wac_app/test_parity.py`, `tests/wac_app/test_schema.py`
- Create: `tests/fixtures/wac_app/travel/values.yaml`, `tests/fixtures/wac_app/travel/expected.yaml`

**Interfaces:**
- Produces, in `tests/wac_app/render.py`:
  - `render(values_path: Path, extra_set: dict[str, str] | None = None) -> list[dict]` runs `helm template app charts/wac-app -f <values>` and returns the parsed documents; raises `RenderError(stderr)` on a non-zero exit.
  - `normalize(docs: list[dict]) -> dict[tuple, dict]` applies the parity rules above.
  - `FIXTURES: Path` pointing at `tests/fixtures/wac_app`.
- Produces, in `_helpers.tpl`: `wac-app.imageRef` taking `(dict "root" . "key" <image key>)` and returning the pull reference, failing when the key is absent or has neither tag nor digest.

- [ ] **Step 1: Capture the travel fixture**

```bash
git -C ../wac.app.travel archive origin/main deploy/k8s | tar -x -C "$TMP"
kustomize build "$TMP/deploy/k8s" > tests/fixtures/wac_app/travel/expected.yaml
```

Write `tests/fixtures/wac_app/travel/values.yaml` by hand from that render:

```yaml
name: travel-app
app: travel
image: travel-app
images:
  travel-app:
    repository: localhost:30500/travel-app
    tag: fixture
ports:
  - name: http
    port: 3000
probes:
  readiness:
    path: /api/health
    port: http
    initialDelaySeconds: 5
    periodSeconds: 5
    timeoutSeconds: 3
    failureThreshold: 12
  liveness:
    path: /api/health
    port: http
    initialDelaySeconds: 60
    periodSeconds: 15
    timeoutSeconds: 5
    failureThreshold: 8
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi
env:
  - name: DATABASE_URL
    secret:
      name: travel-db
      key: url
  - name: CF_ACCESS_TEAM_DOMAIN
    secret:
      name: travel-cf-access
      key: teamDomain
  - name: CF_ACCESS_AUD
    secret:
      name: travel-cf-access
      key: aud
  - name: CALENDAR_FEED_TOKEN
    secret:
      name: travel-calendar-feed
      key: token
serviceAccount:
  create: true
suiteToken: true
routes:
  - kind: ingress
    hosts:
      - wacwini.com
    path: /travel
    stripPrefix: true
migrate:
  mode: presync
  command:
    - node
    - dist/worker/migrate.js
  env:
    - name: DATABASE_URL
      secret:
        name: travel-db
        key: url
```

- [ ] **Step 2: Write the failing tests**

`tests/wac_app/test_parity.py`:

```python
@pytest.mark.parametrize("app", sorted(p.name for p in FIXTURES.iterdir() if p.is_dir()))
def test_chart_reproduces_the_live_render(app):
    expected = normalize(list(yaml.safe_load_all((FIXTURES / app / "expected.yaml").read_text())))
    actual = normalize(render(FIXTURES / app / "values.yaml"))
    assert actual.keys() == expected.keys()
    for key in expected:
        assert actual[key] == expected[key], key

def test_pod_labels_and_selector_are_exactly_app_name():
    deployment = next(d for d in render(FIXTURES / "travel" / "values.yaml") if d["kind"] == "Deployment")
    assert deployment["spec"]["selector"]["matchLabels"] == {"app": "travel-app"}
    assert deployment["spec"]["template"]["metadata"]["labels"] == {"app": "travel-app"}

def test_no_object_carries_a_namespace():
    assert all("namespace" not in d["metadata"] for d in render(FIXTURES / "travel" / "values.yaml"))
```

`tests/wac_app/test_schema.py`, each asserting `pytest.raises(RenderError)` and a substring of the message:

```python
def test_unknown_top_level_key_is_rejected()        # set "replicsa=2", message contains "replicsa"
def test_image_key_missing_from_images_is_rejected() # set "image=nope", message contains "nope"
def test_image_with_no_tag_or_digest_is_rejected()   # blank the tag, message contains "tag or digest"
def test_strip_prefix_on_root_path_is_rejected()     # path "/" with stripPrefix true, message contains "stripPrefix"

def test_image_reference_forms():
    # tag only            -> localhost:30500/travel-app:1.2.3
    # tag and digest      -> localhost:30500/travel-app:1.2.3@sha256:<64 hex>
    # digest only         -> localhost:30500/travel-app@sha256:<64 hex>
```

- [ ] **Step 3: Run them and see them fail**

Run: `uv run pytest tests/wac_app -q`
Expected: FAIL, chart directory does not exist.

- [ ] **Step 4: Write the chart**

`values.schema.json` sets `"additionalProperties": false` at the top level and on every object in the contract table, and requires `name`, `app`, `image`. The two cross-field failures (`stripPrefix` on `/`, missing image key) are `fail` calls in the templates. The PreSync Job carries `argocd.argoproj.io/hook: PreSync` and `argocd.argoproj.io/hook-delete-policy: HookSucceeded,BeforeHookCreation`, not Helm hook annotations, with `backoffLimit: 2`. The Ingress uses `ingressClassName: traefik` and, when `stripPrefix` is set, the annotation `traefik.ingress.kubernetes.io/router.middlewares: <namespace>-<app>-stripprefix@kubernetescrd` using `.Release.Namespace`.

- [ ] **Step 5: Run the tests and see them pass**

Run: `uv run pytest tests/wac_app -q` and `helm lint charts/wac-app -f tests/fixtures/wac_app/travel/values.yaml`
Expected: all pass, lint reports 0 failed.

- [ ] **Step 6: Commit**

```bash
git add charts/wac-app tests/wac_app tests/fixtures/wac_app/travel
git commit -m "feat(wac-app): shared chart skeleton reproducing travel"
```

### Task 2: Version lock and publication

**Files:**
- Create: `scripts/wac_app_lock.py`, `charts/wac-app.lock.json`
- Create: `tests/wac_app/test_lock.py`
- Create: `.github/workflows/wac-app-chart.yml`
- Modify: `AGENTS.md` (Commands and Layout entries for the chart)

**Interfaces:**
- Produces: `chart_digest(chart_dir: Path) -> str`, a sha256 over the sorted relative paths and bytes of every file under the chart; `check(chart_dir, lock_path) -> None` raising `LockError`; CLI `uv run python scripts/wac_app_lock.py write|check`.
- Produces: published artifact `oci://registry.network.svc.cluster.local:5000/charts/wac-app:<version>`.

- [ ] **Step 1: Write the failing tests** in `tests/wac_app/test_lock.py`

```python
def test_lock_matches_the_chart_in_the_tree()            # check() passes on the repo as committed
def test_changed_content_with_same_version_is_refused(tmp_path)
    # copy chart, edit a template, keep version: check() raises, message contains "bump"
def test_write_refuses_new_content_under_a_locked_version(tmp_path)
    # write() raises when digest differs from the lock and version equals the lock's
def test_write_records_version_and_digest_after_a_bump(tmp_path)
    # bump Chart.yaml version, write(): lock == {"version": <new>, "sha256": <digest>}
```

- [ ] **Step 2: Run and see them fail.** Run: `uv run pytest tests/wac_app/test_lock.py -q`. Expected: FAIL, module missing.

- [ ] **Step 3: Implement `scripts/wac_app_lock.py`** and write the first lock with `uv run python scripts/wac_app_lock.py write`.

- [ ] **Step 4: Write `.github/workflows/wac-app-chart.yml`**

Triggers: `push` to `main` on `charts/wac-app/**`, `charts/wac-app.lock.json`, the workflow file; and `workflow_dispatch`. One job on `${{ vars.CI_RUNNER_STANDARD }}`, `contents: read`, concurrency group `wac-app-chart` with `cancel-in-progress: false`. Steps: checkout; `azure/setup-helm@1a275c3b69536ee54be43f2070a358922e12c8d4` at `v3.16.3`; `astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86`; `uv run pytest tests/wac_app -q`; `helm package charts/wac-app`; then:

```bash
ref="oci://registry.network.svc.cluster.local:5000/charts/wac-app"
if helm show chart "$ref" --version "$VERSION" --plain-http >/dev/null 2>&1; then
  echo "wac-app $VERSION is already published; leaving it untouched" >> "$GITHUB_STEP_SUMMARY"
  exit 0
fi
helm push "wac-app-$VERSION.tgz" "oci://registry.network.svc.cluster.local:5000/charts" --plain-http
helm show chart "$ref" --version "$VERSION" --plain-http
```

- [ ] **Step 5: Verify.** Run: `uv run pytest tests/wac_app -q`, `actionlint .github/workflows/wac-app-chart.yml`, and the repo's full `uv run pytest -q`. Expected: all pass.

- [ ] **Step 6: Commit**, then open the pull request for Tasks 1 and 2. After it merges, confirm the workflow run's summary shows the pushed version.

```bash
git commit -m "feat(wac-app): lock chart versions and publish to the in-cluster registry"
```

### Task 3: Prove Argo CD runs the chart from the plain-HTTP registry

This is the gate for the whole program. If it cannot pass, stop and report; Plans 2 to 5 depend on it.

**Files:**
- Create: `charts/wac-app/examples/proof.yaml` (the travel fixture values with `name: wac-app-proof`, `app: wac-app-proof`, no `env`, `migrate.mode: none`, no `routes`); bump `Chart.yaml` to `0.1.1` and rewrite the lock
- Create: the Argo CD repository declaration for the registry, beside the existing ones (find them with `grep -rn "argocd.argoproj.io/secret-type" k8s/`)
- Create: `k8s/argocd-apps/wac-app-proof.yaml`
- Modify: `tests/test_manifests.py` only if an existing rule rejects a sync-less Application; add the narrowest named exemption

**Interfaces:**
- Produces: the repository declaration later plans reuse, and a recorded answer on which repository fields this cluster's Argo CD version needs for plain-HTTP OCI.

- [ ] **Step 1: Read the installed Argo CD version** from its Application or values in `k8s/`, and look up that version's repository Secret fields for OCI Helm over plain HTTP in the Argo CD docs. Write the version and the fields into the pull request description.

- [ ] **Step 2: Add the repository declaration**: a Secret labelled `argocd.argoproj.io/secret-type: repository` with `type: helm`, `enableOCI: "true"`, `url: registry.network.svc.cluster.local:5000/charts`, and the plain-HTTP field step 1 found. It holds no credential.

- [ ] **Step 3: Add the proof Application**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: wac-app-proof
  namespace: argocd
spec:
  project: default
  source:
    repoURL: registry.network.svc.cluster.local:5000/charts
    chart: wac-app
    targetRevision: 0.1.1
    helm:
      valueFiles:
        - examples/proof.yaml
  destination:
    server: https://kubernetes.default.svc
    namespace: default
```

It has no `syncPolicy`, so Argo CD pulls and renders the chart and creates nothing.

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: pass. Commit, open the pull request, and after merge wait for the chart workflow to publish `0.1.1`.

- [ ] **Step 5: Verify against the cluster, read-only.** Read the `wac-app-proof` Application through the Argo CD MCP (`get_application`). Pass: no `ComparisonError` condition, sync status `OutOfSync`, and its resource list names Deployment `wac-app-proof`, Service `wac-app-proof` and a ServiceAccount. Record the output in the pull request.

- [ ] **Step 6: If it fails**, record the exact condition message and stop. Do not work around it by hand in the cluster.

### Task 4: finance and wac

**Files:**
- Create: `charts/wac-app/templates/pvc.yaml`
- Modify: `charts/wac-app/templates/deployment.yaml`, `ingress.yaml`, `values.schema.json`; `Chart.yaml` to `0.2.0`; lock
- Create: `tests/fixtures/wac_app/finance/{values,expected}.yaml`, `tests/fixtures/wac_app/wac/{values,expected}.yaml`

- [ ] **Step 1: Capture both fixtures** as in Task 1 Step 1 (`../wac.app.finance`, `../wac`) and write their values files. Finance exercises: `strategy: Recreate`, `minReadySeconds: null`, `migrate.mode: init` with command `node scripts/migrate.mjs` and its own resources, `persistence: [{name: finance-data, size: 1Gi}]` mounted at `/data`, plain `value` env, an `optional: true` secret env, a liveness path of `/api/live`, and two routes (`wacwini.com` `/finance` stripped; `finance-sync.wacwini.com` `/` unstripped). wac exercises: no ServiceAccount, no `suiteToken`, one route at `/` unstripped.

- [ ] **Step 2: Add the test for Review Focus 5** in `tests/wac_app/test_schema.py`:

```python
def test_migrate_mode_none_renders_no_migration_even_with_a_command():
    docs = render(FIXTURES / "travel" / "values.yaml", {"migrate.mode": "none"})
    assert not [d for d in docs if d["kind"] == "Job"]
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    assert "initContainers" not in deployment["spec"]["template"]["spec"]
```

- [ ] **Step 3: Run.** `uv run pytest tests/wac_app -q`. Expected: FAIL on `finance`, `wac` and the new test.

- [ ] **Step 4: Extend the chart.** The init container is named `migrate`. Each claim carries `argocd.argoproj.io/sync-options: Prune=false`, `accessModes: [ReadWriteOnce]`, and no `storageClassName`.

- [ ] **Step 5: Run.** `uv run pytest tests/wac_app -q`. Expected: PASS for travel, finance, wac.

- [ ] **Step 6: Commit.** `git commit -m "feat(wac-app): init migrations, persistence, Recreate and multi-route for finance and wac"`

### Task 5: wealth

**Files:**
- Create: `charts/wac-app/templates/cronjob.yaml`
- Modify: `deployment.yaml`, `pvc.yaml`, `values.schema.json`; `Chart.yaml` to `0.3.0`; lock
- Create: `tests/fixtures/wac_app/wealth/{values,expected}.yaml`

- [ ] **Step 1: Capture the fixture** from `../wac.app.wealth`. It exercises: `migrate.mode: both`; two entries in `images` (`wealth-app`, `wealth-garmin`); one cron job `wealth-garmin-poll` on image `wealth-garmin`, schedule `17 * * * *`, `timeZone: America/Denver`, `activeDeadlineSeconds: 900`, `backoffLimit: 1`, `startingDeadlineSeconds: 120`; a claim `wealth-garmin-tokens` of `100Mi` with `ignoreHealthcheck: true` (annotation `argocd.argoproj.io/ignore-healthcheck: "true"`); a Secret volume `garmin-seed` with `items`; mounts on the cron job only.

- [ ] **Step 2: Add the test:**

```python
def test_cron_job_image_key_missing_from_images_is_rejected()  # message contains the missing key
```

- [ ] **Step 3: Run and see wealth fail.** `uv run pytest tests/wac_app -q`.
- [ ] **Step 4: Extend the chart.** Volumes named by a cron job's `volumeMounts` are attached to that job's pod, not the Deployment's.
- [ ] **Step 5: Run and see all four fixtures pass.**
- [ ] **Step 6: Commit.** `git commit -m "feat(wac-app): cron jobs, second images and secret volumes for wealth"`

### Task 6: baton and mcp.argo

**Files:**
- Create: `charts/wac-app/templates/httproute.yaml`, `servicemonitor.yaml`
- Modify: `deployment.yaml`, `service.yaml`, `ingress.yaml`, `migrate-job.yaml`, `values.schema.json`; `Chart.yaml` to `1.0.0`; lock
- Create: `tests/fixtures/wac_app/baton/{values,expected}.yaml`, `tests/fixtures/wac_app/argo-mcp/{values,expected}.yaml`

- [ ] **Step 1: Capture both fixtures** (`../baton`, `../mcp.argo`). Baton exercises: `name: baton`, `app: baton`; a digest-only image; `replicas: 2`; ports `http` 3000 and `management` 3001 with all three probes on `management` (`/readyz`, `/livez`); `serviceAccount: {name: baton, automountToken: false}` with no create; pod and container security contexts; `priorityClassName: lab-baton`; `terminationGracePeriodSeconds: 45`; `preStopSleepSeconds: 10`; an `emptyDir` `tmp` of `64Mi` at `/tmp`; a PreSync migration with its own env, resources and `activeDeadlineSeconds: 600`, inheriting the pod security context, container security context, priority class and ServiceAccount; a `serviceMonitor` on `management` at `/metrics`, `30s` and `5s`; one route with hosts `wacwini.com` and `localhost` at `/baton`, stripped. mcp.argo exercises: `name: argo-mcp`, `args: [http]`, port 8080, `minReadySeconds: null`, a memory-only limit, and a route `{kind: httpRoute, parent: {name: mcp, namespace: mcp-gateway}, path: /argo, rewriteTo: /}`.

- [ ] **Step 2: Run and see both fail.** `uv run pytest tests/wac_app -q`.
- [ ] **Step 3: Extend the chart.**
- [ ] **Step 4: Run and see all six fixtures and the schema tests pass.** Also run `helm lint charts/wac-app -f tests/fixtures/wac_app/<app>/values.yaml` for each of the six.
- [ ] **Step 5: Commit**, open the pull request, and after merge confirm the workflow published `wac-app` `1.0.0`.

```bash
git commit -m "feat(wac-app): 1.0.0 with hardening, monitors and Gateway API routes for baton and mcp.argo"
```

## Done when

- `uv run pytest tests/wac_app -q` passes with six parity fixtures.
- `wac-app` `1.0.0` is in the in-cluster registry.
- The `wac-app-proof` Application shows the chart pulled and rendered by Argo CD, with the evidence in its pull request.
