# wac-cd

The merge half of wacd: on a push to `main` it versions the app, builds and
pushes its images, publishes the deploy chart and git tag, releases through
Lab's gateway, proves the served build, and records a GitHub Release. The
profile it reads is in [The wacd profile](wacd-profile.md).

```yaml
name: cd
on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      version:
        description: Published version to roll back to, X.Y.Z
        type: string
        required: true
permissions:
  contents: read
jobs:
  wac-cd:
    uses: willfell/wac.lab.actions/.github/workflows/wac-cd.yml@vX.Y.Z
    permissions:
      contents: write
      id-token: write
      packages: read
    with:
      build_runner: ${{ vars.CI_RUNNER_STANDARD }}
      rollback_version: ${{ inputs.version }}
```

**The caller file must be `.github/workflows/cd.yml`.** The gateway admits a
release only from that caller file on `refs/heads/main`, for a `push` or
`workflow_dispatch` event, whose job workflow ref is exactly
`willfell/wac.lab.actions/.github/workflows/wac-cd.yml@refs/tags/vX.Y.Z`. That
is why the gateway call is a job in `wac-cd.yml` itself rather than a step in
the caller's own workflow: such a step would carry the caller's identity, and
the gateway used
to deny every reusable-workflow identity before it learned to admit exactly
this one. It also means this repository cannot be transferred out of
`willfell` or renamed until the gateway's policy names the new ref first;
otherwise every app's next release is denied.

The calling job grants `contents: write` (the tag and the GitHub Release),
`id-token: write` (the gateway's OIDC token) and `packages: read` (private npm
packages during the build), and declares no `environment`: the gateway rejects
a token that carries that claim. The trigger is a push to `main` plus a
`workflow_dispatch` whose own `version` input is passed as `rollback_version`;
on a push that input is empty.

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label for every job but `build` when `build_runner` is set, in the caller's repo | empty: the caller's `CI_RUNNER_SMALL` |
| `build_runner` | `runs-on` label for `build`, the one job that runs `docker build` | empty: `runner` |
| `version_source` | `commits` computes the version from conventional commits; `caller` takes it from `version` | `commits` |
| `version` | the caller-computed bare `X.Y.Z` with `version_source: caller`; empty means no release | empty |
| `rollback_version` | a published version to release again; only on `workflow_dispatch` | empty |
| `deploy` | `false` publishes the images, chart and tag without releasing them | `true` |
| `build_args` | newline-separated `KEY=VALUE` build args passed to every image build | empty |
| `wait_timeout` | seconds to wait for the gateway's release proof | `2100` |

| Secret | Meaning |
| --- | --- |
| `npm_token` | optional token for private npm packages; empty uses the job's GitHub token |

| Output | Meaning |
| --- | --- |
| `mode` | `release`, `publish`, `resume`, `rollback` or `none` |
| `version` | the released or published version, bare `X.Y.Z` |
| `tag` | the git tag for that version |
| `images` | JSON map of the built images with digests; empty on resume and rollback |

The jobs are `plan`, `build`, `publish`, `deploy`, `github-release` and the
observation-only `shadow` (below),
serialized per repository by a concurrency group that never cancels a run in
progress. A cancelled release is worse than a queued one. The group does not
queue every run, though: GitHub keeps at most one pending run per group, so a
newer pending run replaces an older one that has not started. For pushes that
is harmless, because the later push's version covers the earlier commits. A
replaced rollback dispatch never runs, so check that a rollback started before
walking away from it.

Only `build` runs `docker build`, so only `build` needs a dind runner; the
others push a chart with helm, call the gateway or the GitHub API, and run on
`CI_RUNNER_SMALL` when the caller passes `build_runner` and no `runner`. A
caller that passes only `runner` keeps every job on it. `publish`, `deploy` and `shadow` reach the in-cluster registry,
the gateway and hemi, so a caller whose `CI_RUNNER_SMALL` is a hosted label
keeps passing `runner`. `plan` through
`github-release` run one after another, so on a three-pod dind pool each of
them used to queue again for a slot behind every other repo's jobs.

**Versions.** `plan` finds the latest tag `<tag_prefix>X.Y.Z` reachable from
HEAD and reads the commits since it: any breaking change (`type!:` or a
`BREAKING CHANGE:` footer) is a major, any `feat` is a minor, and anything else
is a patch. A repo with no such tag releases `0.1.0`. Every merge to `main`
releases. The one version is the image tag, the deploy chart version, the git
tag suffix and the GitHub Release name. Baton keeps its own version rules, so
it passes `version_source: caller` and its computed `version`; an empty
`version` there is a `none` run that ends green. A computed tag that already
exists on another commit fails the plan.

**Modes.**

| Mode | When | Runs |
| --- | --- | --- |
| `release` | a new version on a push | build, publish, deploy, github-release |
| `publish` | a new version with `deploy: false` | build, publish |
| `release` | HEAD already carries a `<tag_prefix>X.Y.Z` tag, but its deploy chart is not in the registry | build, publish, deploy, github-release |
| `publish` | the same, with `deploy: false` | build, publish |
| `resume` | HEAD already carries a `<tag_prefix>X.Y.Z` tag and its deploy chart is published from this SHA | deploy, github-release |
| `rollback` | `workflow_dispatch` with `rollback_version` | deploy, github-release |
| `none` | `version_source: caller` with no version, or a tagged HEAD whose chart is published, with `deploy: false` | nothing |
| `none` | a `release` or `publish` run whose tag push was refused after `main` moved on: superseded | nothing |

`deploy: false` is publish-only, used for cutover step 1: the old route still
deploys while `wac-cd` publishes the first deploy chart so Lab can register the
app at that version. `rollback_version` with `deploy: false` fails.

**A tag means "claimed"; a GitHub Release means "ran".** In `release` and
`publish` modes, `plan` pushes the git tag (`wacd-tag.py claim-tag`) as soon as
the version is decided, before anything is built, and `publish` pushes the
deploy chart and then accepts that same tag again, unchanged. So a version
whose build, publish or rollout fails is still tagged, and the next merge
computes the next version rather than this one again. `github-release` runs after any successful
deploy, in every mode that deploys, and creates the Release for the tag if it
is missing, with generated notes. It never edits one that exists, so a resume
or rollback leaves the original Release as it was.

**Resume.** A run whose HEAD already carries a release tag checks the
registry for that version's deploy chart. When the chart is there, recorded
from this push's SHA, the run skips build and publish and releases that
version. That is how a run whose rollout failed after the tag is finished:
re-run it. A chart recorded from another SHA fails the plan.

When the chart is absent, the run builds and publishes that same version
(`release`, or `publish` with `deploy: false`), with this push's SHA as the
expected SHA: the image and chart state checks accept an empty registry, and
the tag step accepts a tag already on this SHA. That is the recovery after a
cluster rebuild empties the registry: re-run `wac-cd` for the commit carrying
the latest tag. It also covers cutover: a HEAD tagged by the app's old
pipeline, which never published a deploy chart, publishes one on its first
`wac-cd` run instead of failing.

**No downgrade by re-run.** "Re-run failed jobs" on an older run skips `plan`
and reuses its mode, so a run that tagged V and failed at deploy, re-run after
a later merge released V+1, would put V back. Before its `release` request,
`deploy` asks the gateway for `status` once (retrying only 503, within
`wait_timeout`), and when the running `version` is a strict `X.Y.Z` newer than
the version being released, it fails naming both and pointing at a rollback
dispatch. A rollback skips that refusal; an equal, older or non-`X.Y.Z`
running version (the bootstrap tag before the first release) proceeds.

**Rollback** is a `workflow_dispatch` of `cd.yml` with `version`. `plan` reads
the chart's recorded revision and requires it to be an ancestor of `main`;
build and publish are skipped and the chart that already exists is released
again, expecting its own revision at the health check. A version not in the
registry fails the plan before any gateway call. Rollback is supported only
back to the last release that added a database migration: there are no down
migrations, and an older image meeting a newer schema is the app's to refuse.
Nothing in `wac-cd` or the gateway enforces that limit; the `wacd:release`
skill states it before rolling back.

**Proof.** `deploy` reads the image map back from the published chart, so
release, resume and rollback take one path, and asks the gateway to `release`
the version. The gateway pins the Application to the chart's manifest digest
and requests a sync; `wac-cd` then polls `status` until the Application is
Synced at that digest and Healthy with automated sync on, every Deployment has
observed its generation with every replica updated, ready and available, and
every Deployment image naming one of this release's repositories is exactly
`localhost:30500/<name>:<version>@<digest>`, with at least one such image. A
cron-only image never appears in a Deployment, which that rule allows. The
gateway receipt is uploaded as
the artifact `wac-cd-release-<version>`. Last, `health_url` must serve the
expected source SHA (and `db: ok`, unless `health_expect_db_ok` is `false`)
within a fixed 300 seconds.

**Shadow.** In every mode but `none`, the `shadow` job asks hemi whether its
stored copy of the app's configuration matches the files. It mints a GitHub
OIDC job token with audience `wacwini:hemi` for each request, reads
`GET http://hemi-app.hemi.svc.cluster.local:3000/api/v1/contract` for the copy
number (`identity.revision`), then posts `{"revision", "profile", "values"}` and,
when Helm could read the file, `"helmValues"` to `/api/v1/shadow`: the copy number, `.github/wacd.json` parsed as JSON, the text of the values
file the profile names, and `helmValues`, Helm's own reading of that file. YAML 1.1,
which Helm uses, reads `yes` and `on` as true, `010` as 8 and a key written `y` as
the string `true`, and applies `<<` merges; hemi reads the same text as YAML 1.2, so
hemi compares JSON with JSON. The job installs Helm `v3.16.3` (the version `publish`
installs) in a step that may fail and is bounded at one minute, and the script, once `helm version --template '{{.Version}}'` prints exactly `v3.16.3` (any other Helm, such as one the runner image ships, is treated as absent), runs `helm template probe
scripts/fixtures/values-probe -f <values file> --show-only templates/values.json`
with no cluster (`KUBECONFIG=/dev/null`), none of the job's token variables and a
30-second timeout. The probe chart's only template is `{{ toJson .Values }}` and it
ships no `values.yaml`, because with chart defaults Helm removes a key the user set to
null and without them it keeps every null where it was written. If Helm is absent,
fails, times out or prints anything but a JSON object, the body carries no
`helmValues` and the summary ends with `Helm could not read the values; hemi
compared its own reading.` Each request has a 10-second timeout, no proxy and no
redirects. The job summary gets one line, `hemi's copy matched the files
(copy <n>).` or `hemi's copy differs from the files in <k> places (copy <n>).`
(`in 1 place` for a single difference) followed by one line per difference, or `hemi did not answer; nothing was
compared.` for any other status, a timeout, a dropped connection or a missing
token. It is observation only: the files stay the truth and nothing acts on the
answer. The job is `continue-on-error`, no other job lists it in `needs`, and
`scripts/wacd-shadow.py` exits 0 on every path, so a hemi outage never fails or
delays a release. The job is bounded at two minutes, because the run holds its
concurrency group until every job ends and the next queued release waits for
it. It asks for `contents: read` and `id-token: write`, which the calling job
already grants. Neither token is ever printed: every token it minted is
replaced with `[token]` in any line it writes, even one hemi echoed back.

## Where releases live

The deploy chart is pushed to
`oci://registry.network.svc.cluster.local:5000/deploy/<app>:<version>`: the
`wac-app` chart at `chart_version` vendored as a dependency, the app's values
nested under it, and `appVersion` equal to the version. Its `Chart.yaml`
carries two annotations the gateway reads back from the Helm config blob:
`org.opencontainers.image.revision` (the source SHA) and `wacwini.com/images`
(the space-separated image references, sorted by name). A published chart
version is never overwritten.

Images are built once per version and pushed through the push address, then
recorded and pulled as `localhost:30500/<name>:<version>@<digest>`. Each
image is labelled with `org.opencontainers.image.revision`, and the source SHA
is also passed as the image's `sha_build_arg` when it has one.

Image builds need BuildKit, because the npm token reaches the build as a
`--secret` (`id=node_auth_token`), never as a build arg that would be baked
into a layer.

## The registry split

Push and pull use two addresses for the same registry, and they are never
collapsed into one. `registry.network` collides with a real public TLD
(`.network`), so the node's Docker daemon resolved it as a public FQDN rather
than the in-cluster Service: pulls silently went out to a stranger's server
while pushes from runner pods kept working, because pods resolve the name
through the kubelet's DNS search path and the daemon does not. So the push
address is the fully qualified in-cluster name
`registry.network.svc.cluster.local:5000`, which a runner pod resolves and no
public name can shadow, and the pull address is `localhost:30500`, the
registry Service's NodePort as the node's daemon sees it. Docker treats a
`localhost` registry as plain HTTP automatically, and `localhost` cannot
collide with a public domain. Collapsing the two back into one variable is the
outage this section describes.

The images are saved as tarballs and pushed with `crane push --insecure`, not
`docker push`. dockerd refuses plain HTTP to any registry address it does not
consider `localhost`, and the push address deliberately is not one.
Reconfiguring the runner's dind sidecar to trust it would couple every caller
to that chart's internals; crane sidesteps dockerd's registry trust entirely.

## Why the tag comes first

GitHub refuses a `GITHUB_TOKEN` push of any ref at a commit whose
`.github/workflows` tree differs from the default branch's, even when that
commit is already on `main`, and that token can never be granted the
`workflows` permission. A run queued behind the concurrency group while a
workflow-changing merge (a pin bump of `cd.yml` or `ci.yml`, say) lands on
`main` therefore cannot tag its own commit any more. Travel's first `wac-cd`
release hit exactly that: it published `deploy/travel:0.2.0` and then failed
to push `v0.2.0`. Claiming the tag in `plan`, before build, shrinks that
window to the plan job alone and makes sure nothing is built or published for
a version that cannot be tagged.

**Superseded runs.** When the tag push is refused, `plan` reads `main` on the
remote. If `main` has moved past this run's SHA, a newer run is coming that
will release these commits together with its own, so this run is superseded:
it sets mode `none`, writes a summary line saying so, builds and publishes
nothing, and ends green. If the refused SHA is still `main`'s tip, nothing will
release it, and the plan fails as before. A tag that already exists on another
commit fails the plan in either case. The cost: a queue of workflow-only merges
can delay a release by one run, and their commits release with the next one.
No version is ever lost or tagged twice.

## When a run fails after the tag

If build or publish fails after `plan` claimed the tag, re-run the same commit.
Its HEAD is now tagged and the version's deploy chart is absent, so `plan`
takes the `release` (or `publish`) path described under **Resume** above: it rebuilds and
publishes that same version, the image and chart state checks accept anything
already pushed from this SHA, and the claim accepts the tag already on it. A
different commit that computes the same version fails at the image or chart
state check instead, naming both SHAs, because a version is never rebuilt from
other source.

If the failed run is never re-run, the next commit's run versions above the
tag, so the failed version is skipped: it has a tag and no deploy chart, and
nothing was ever deployed at it. That gap in the version sequence is accepted;
the skipped version can still be completed later by re-running its run, as
long as no later version has deployed, since `deploy` refuses to go below the
running version.
