# wac-cd

The merge half of wacd: on a push to `main` it versions the app, builds and
pushes its images, publishes the deploy chart and git tag, releases through
Lab's gateway, proves the served build, and records a GitHub Release. The
profile it reads is in [The wacd profile](wacd-profile.md); once an app is cut
over, it reads the same settings from the copy hemi stores instead (see
**Settings** below).

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
`id-token: write` (the gateway's OIDC token, which `plan`, `shadow` and
`deploy` ask for) and `packages: read` (private npm packages during the build), and declares no `environment`: the gateway rejects
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
| `wait_timeout` | seconds `plan` waits out an unavailable gateway for the settings, and seconds to wait for the gateway's release proof | `2100` |

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

**Settings.** Before it reads a profile, `plan` asks Lab's gateway once for
the app's settings copy: `scripts/wacd-contract.py fetch --out .wacd-contract`
posts `{"operation": "contract"}` to the same gateway address as `release`,
with a fresh OIDC token (audience `wacwini:ci-deploy`) for every attempt. The
gateway answers with hemi's copy, `active.json` in wac.fleet, and the wac.fleet
commit it read it at, or with `{"copy": null, "fleet_commit": null}` for a
repository never cut over. Then:

**Pin this only after Lab's gateway serves `contract`** (wacwini/wac.lab#570
deployed). A gateway older than that admits only `release`, `status` and
`migrate`, and answers `contract` with the same uncoded 403 as a caller it
does not verify, so every run of every repository, cut over or not, fails at
`plan` with the sentence for an uncoded denial below. `plan` does not guess
that such a gateway means "no copy": it fails closed.

| The gateway answers | Settings come from | The chart also carries |
| --- | --- | --- |
| a copy whose `flags.source` is `hemi` | the copy: its profile is derived by `wacd-profile.py --contract`, its `runtime` is the values | `wacwini.com/revision`, `wacwini.com/fleet-commit` |
| a copy whose `flags.source` is `files` | `.github/wacd.json` and its values file, as before | `wacwini.com/revision`, `wacwini.com/fleet-commit` |
| no copy, and the repository has `.github/wacd.json` | `.github/wacd.json` and its values file, exactly as before | nothing new |
| anything else | nothing: `plan` fails before anything is built | |

Anything else is a copy that cannot be read, a copy whose
`identity.repositoryId` is not the job token's `repository_id`, a copy whose
source is `files` (or no copy) in a repository with no `.github/wacd.json`, a
denial, any other status, or a gateway still answering 503 after the bound.
A cut-over repository whose copy is missing is refused by the gateway itself
(`fleet_copy_missing`), never answered with null, so it cannot fall back to
stale files.

When the copy is acted on, `.wacd-contract/` holds `contract.json` (the copy as
received), `profile.json` (the profile it stands for, in `.github/wacd.json`'s
shape, its `values_file` pointing at `.wacd-contract/values.json`) and
`values.json` (the copy's `runtime`). Whenever a copy exists, `plan` uploads
the directory as the artifact `wac-cd-contract`. `plan` reads its profile
outputs from the copy; `build`, `deploy` and `github-release` take every
setting from those outputs and read no settings file; `publish` downloads the
artifact and packages the chart from `profile.json` with
`--values-json .wacd-contract/values.json`. So a release ships the revision
`plan` read, even when a newer one is saved while it builds; nothing after
`plan` asks the gateway or hemi for settings. A `.wacd-contract/` committed to
the repository is deleted before `plan` writes and before `publish` downloads,
so it is never acted on.

**Every mode waits on that answer.** `plan` asks before it knows the mode, so
while the gateway refuses, or still answers 503 after `wait_timeout` (below),
`plan` fails in every mode, `publish` and `none` included, and nothing is
built, published or tagged. Re-run the run once the gateway answers.

**A re-run of failed jobs reuses the copy.** "Re-run failed jobs" skips a
`plan` that succeeded, so a re-run `publish` downloads the `wac-cd-contract`
artifact the earlier attempt's `plan` uploaded in the same run, and never
reads the copy again. The artifact holds settings and Secret names, so it is
kept for one day (`retention-days: 1`): a re-run of failed jobs must start
within a day of the run, or its `publish` finds no copy and fails. "Re-run all
jobs" runs `plan` again, which reads the copy afresh and replaces the artifact.

**What runs beside the token.** `plan` holds `id-token: write`, and a GitHub
OIDC token does not say which step minted it, so `plan` runs nothing fetched
at run time. It uses the runner image's own `python3`, which its first step
requires to be 3.12 or later, failing with the runner's name, OS and
architecture and the `python3` path and version it found, and it uses only
actions pinned by full commit SHA, with the tag in each step's name.
`.github/scripts/test_reusable_workflows.py` holds every job of `wac-cd` and
`wac-ci` that holds `id-token: write` to an allowlist of commit-pinned actions
and refuses a step that runs `install-tools.sh`, `pip`, `npm`, `npx`, `uvx`,
`curl` piped to a shell, or `get-helm`. `install-helm.sh` is allowed: it checks
a committed digest before it runs anything. `deploy` and `shadow` are the
known exceptions, still on tag-pinned actions and `setup-python`; that list
only shrinks.

A 503 (`release dependency unavailable`: GitHub slow, rate-limited or down
behind the gateway) is retried with a fresh token within `wait_timeout`
seconds (default `2100`), the bound `deploy` gives a `release`, passed to the
script as `--wait-timeout` and parsed by the same function `deploy` uses, so
both jobs accept exactly the same values. `plan`'s wait is capped at 2100
seconds (35 minutes) whatever the input, while `deploy` honours the full
`wait_timeout`: `plan` has `timeout-minutes: 40`, the cap plus five minutes
for its other steps (GitHub expressions have no arithmetic, so it cannot be
sized from the input), and the cap makes a long outage end in `plan`'s own
summary line rather than the job timeout. The interval starts at 5 seconds and
doubles up to 30, so a long outage costs about two requests a minute. Each
request may wait the whole time left for its answer, as `deploy`'s requests
do, because the gateway checks the caller against `main` with several GitHub
calls before it reads wac.fleet and can be slow. A refused or reset connection
or a TLS failure fails at once, as for a `release`, and so does a request
still unanswered at the deadline; both read `could not be reached`.

Every other `plan` step has its own `timeout-minutes` (one to six minutes), so
only the gateway wait can use the job's 40 minutes: a stalled checkout,
upload or tag push fails within minutes instead. `wacd-tag.py` bounds each
call that reaches the remote (`ls-remote`, `push`) at 60 seconds and kills git
and its transport helpers when it passes; a timed-out push fails the plan and
is never read as superseded. `Check the trigger` runs before the gateway is
asked, so a malformed dispatch fails at once, even during an outage.

While `plan` waits out an outage it holds the repository's `cd` concurrency
group for up to the capped wait, about 35 to 40 minutes, and since a group
keeps one pending run, a newer push then replaces an older pending one. That
is the wait `deploy` already had before
`v1.35.0`, moved earlier, not a new delay. A 403 is not retried. A
denial is told apart from a 503 by its status, never by its body, and the body
is never printed: a 403 without a
`reason` means the gateway did not verify the caller, and a 403 with one of the
gateway's codes gets this sentence in the run summary:

| Code | Sentence |
| --- | --- |
| `fleet_copy_missing` | The gateway found no copy of this app's settings where one is required, so the run stopped. |
| `fleet_copy_unreadable` | The gateway could not read hemi's copy of this app's settings, so the run stopped. |
| `fleet_copy_mismatch` | hemi's copy of this app's settings names another app, so the run stopped. |
| `fleet_commit_not_on_default_branch` | The settings this release was planned with are no longer on the fleet copy's main line. |
| `fleet_commit_older_than_last_release` | The settings this release was planned with are older than the settings already released. |
| `revision_annotation_missing` | This chart does not carry both of the settings annotations, and the gateway requires them for this app. |
| `revision_mismatch` | This chart's settings revision is not the one the fleet copy holds now; plan again to release the current settings. |

`fleet_commit_not_on_default_branch`, `fleet_commit_older_than_last_release`,
`revision_annotation_missing` and `revision_mismatch` come only from the
gateway's release check, which answers a `release` in `deploy`; that check can
also return the three `fleet_copy_*` codes, for the copy at the chart's own
fleet commit, while the `contract` request returns only those three. `scripts/wacd-release.py` holds the map, and both
scripts use it: `deploy` fails with
`release request refused (HTTP 403): <sentence>` and adds
`The gateway refused the release: <sentence>` to the job summary. Its POST
keeps only a 403's `reason` and drops the rest of every body but a 200's.

A code not in that list gets
`The gateway refused this request for a reason this wac-cd does not know (code <code>).`
(without the parenthesis when the code is not a plain lowercase word), so a
code added to the gateway later cannot break a release summary. A denial
without a code reads
`The gateway did not accept this run as a release of a registered app.` From `plan` it adds
`A gateway that does not yet serve the contract operation (wacwini/wac.lab#570 not deployed) answers this way too.`
The run summary's
`wac-cd settings` section names the source, the revision and the fleet commit,
with one sentence saying which rule applied or why the run stopped. The
revision is a JSON number in the copy and a decimal string in the annotation,
of at most ten digits as the gateway reads it; `wacd-chart.py` converts between
the two in one place.

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

**Installing Helm.** `publish` and `shadow` both run `scripts/install-helm.sh`,
the one way wac-cd installs Helm (`install-tools.sh` still installs `crane` for
`build`). It downloads exactly
`https://get.helm.sh/helm-v3.16.3-linux-<arch>.tar.gz` for amd64 or arm64 with
`curl --proto '=https' --proto-redir '=https' --tlsv1.2`, checks it with `sha256sum -c` against the two
digests committed in the script beside the version, extracts only
`linux-<arch>/helm` into `$RUNNER_TEMP/bin` and appends that directory to
`GITHUB_PATH`. A failed or partial download is deleted, and every attempt must
pass the digest check. Two arguments set the bound: `--max-time <seconds>` for
one attempt (default 120) and `--attempts <n>` (default 3, five seconds apart).
`publish` passes `--max-time 120 --attempts 3`, so a slow fetch of the roughly
16 MB tarball cannot fail a release that a retry would save; `shadow` passes
`--max-time 25 --attempts 1`. A digest mismatch, no good download or an unknown
architecture exits non-zero and installs nothing. It never runs a fetched
script: the upstream `get-helm-3` installer that `install-tools.sh` uses runs
code from `helm/helm` `main` before any checksum is checked, and `shadow` holds
`id-token: write`, so the GitHub token request URL and bearer are in the
environment of every step it runs. The install stays in that job, with the
tarball pinned, because a separate job without the permission that passed the
binary on as an artifact would cost a fleet runner per release.

**Shadow.** In every mode but `none`, when the settings came from the files,
the `shadow` job asks hemi whether its stored copy of the app's configuration
matches the files. When they came from hemi's copy there are no files to
compare it with, and the job is skipped. It mints a GitHub
OIDC job token with audience `wacwini:hemi` for each request, reads
`GET http://hemi-app.hemi.svc.cluster.local:3000/api/v1/contract` for the copy
number (`identity.revision`), then posts `{"revision", "profile", "values"}` and,
when Helm could read the file, `"helmValues"` to `/api/v1/shadow`: the copy
number, `.github/wacd.json` parsed as JSON, the text of the values file the
profile names, and `helmValues`, Helm's own reading of that file. YAML 1.1,
which Helm uses, reads `yes` and `on` as true, `010` as 8 and a key written `y`
as the string `true`, and applies `<<` merges; hemi reads the same text as
YAML 1.2, so with `helmValues` hemi compares JSON with JSON.

The script reads only regular files inside the repository checkout (the working
directory). A `.github/wacd.json` or values file that is a symbolic link, sits
behind one, lies outside the checkout (`..`, an absolute path) or is not a
regular file is refused, and so is a values path containing a comma (Helm
splits `-f` on commas). So is a file over 256 KiB, and a profile and values
that together would make a request body over 256 KiB, which is the limit of
hemi's shadow route. A refusal skips the whole comparison, mints no token,
posts nothing and writes its own summary line, for example `the values file is
a symbolic link or sits behind one; nothing was compared.`, `the values file
lies outside the repository checkout; nothing was compared.`, `the values file
path contains a comma; nothing was compared.` or `the values file is larger
than 256 KiB; nothing was compared.`

The script uses Helm only when `helm version --template '{{.Version}}'` prints
exactly `v3.16.3` (any other Helm, such as one the runner image ships, is
treated as absent). Helm is never given a path the repository chose: the bytes
the script read and checked are written to `values.yaml` in a scratch directory
and only that file is passed, as `helm template probe
scripts/fixtures/values-probe -f <scratch>/values.yaml --show-only
templates/values.json`. Helm runs with no standard input, no cluster
(`KUBECONFIG=/dev/null`), none of the job's token variables, and empty throwaway
`HELM_PLUGINS`, `HELM_CACHE_HOME`, `HELM_CONFIG_HOME` and `HELM_DATA_HOME`
directories, and its output is read to at most 256 KiB. The probe chart's only
template is `{{ toJson .Values }}` and it ships no `values.yaml`, because with
chart defaults Helm removes a key the user set to null and without them it keeps
every null where it was written. If Helm is absent, is another version, fails,
times out, is skipped for lack of time, or prints more than 256 KiB or anything
but a JSON object, or if its reading would push the request body over 256 KiB,
the body carries no `helmValues` and the summary ends with `Helm could not read
the values; hemi compared its own reading.`

Each socket operation of a request has a 5-second timeout, there is no proxy and there are no redirects. The job summary
gets one line, `hemi's copy matched the files (copy <n>).` or `hemi's copy
differs from the files in <k> places (copy <n>).` (`in 1 place` for a single
difference) followed by one line per difference, or `hemi did not answer;
nothing was compared.` for any other status, a timeout, a dropped connection or
a missing token. It is observation only: the files stay the truth and nothing
acts on the answer. The job is `continue-on-error`, no other job lists it in
`needs`, and `scripts/wacd-shadow.py` exits 0 on every path, so a hemi outage
never fails or delays a release. It asks for `contents: read` and
`id-token: write`, which the calling job already grants. Neither token is ever
printed: every token it minted is replaced with `[token]` in any line it
writes, even one hemi echoed back.

The job is bounded at two minutes, because the run holds its concurrency group
until every job ends and the next queued release waits for it. The summary has
to be written inside that bound, and a socket timeout cannot promise that: it
applies to each operation, not to a DNS lookup, and not to a reply that trickles
in. So the exchange has one deadline. The job's first step records
`WACD_SHADOW_STARTED`, the time in epoch seconds, before any checkout (which
sleeps 10 to 20 seconds before a retry), and the script ends the exchange 95
seconds after it (after the script's own start if the stamp is absent, not a
number, in the future or older than the job). In seconds:

| Part | Seconds |
| --- | --- |
| "Set up job" before the stamp, assumed at most | 10 |
| deadline after the stamp, shared by both checkouts, `setup-python`, the install step and the script | 95 |
| reserve for writing the summary | 15 |
| total | 120 |

The install step (`--max-time 25 --attempts 1`, `timeout-minutes: 1` as a kill
switch) only uses up part of the 95. Helm gets what is left minus 10 seconds
kept for the exchange, each call limited to its own 5 or 10 seconds, and is
skipped as unread when under 5 seconds are left. The token mints and the
exchange with hemi run in a worker thread that is joined against what is left;
when the deadline passes the summary says `hemi did not answer; nothing was
compared.` and the script exits 0. `.github/scripts/test_reusable_workflows.py`
checks that the three parts add up to the job's bound.

## Where releases live

The deploy chart is pushed to
`oci://registry.network.svc.cluster.local:5000/deploy/<app>:<version>`: the
`wac-app` chart at `chart_version` vendored as a dependency, the app's values
nested under it, and `appVersion` equal to the version. Its `Chart.yaml`
carries two annotations the gateway reads back from the Helm config blob:
`org.opencontainers.image.revision` (the source SHA) and `wacwini.com/images`
(the space-separated image references, sorted by name). A chart published
while the gateway holds a copy of the app's settings also carries
`wacwini.com/revision` (the copy's revision as a decimal string, such as `"7"`)
and `wacwini.com/fleet-commit` (the 40-hex wac.fleet commit it was read at),
and the gateway holds the release to that copy. A published chart version is
never overwritten.

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
