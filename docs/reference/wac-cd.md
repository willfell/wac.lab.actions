# wac-cd

The merge half of wacd: on a push to `main` it versions the app, builds and
pushes its images, publishes the deploy chart and git tag, releases through
Lab's gateway, proves the served build, and records a GitHub Release. The
profile it reads is in [The wacd profile](wacd-profile.md); once an app is cut
over, it reads the same settings from the copy hemi stores instead (see
**Settings** below).

```yaml
name: cd
run-name: ${{ inputs.revision && format('settings revision {0}', inputs.revision) || inputs.version && format('rollback to {0}', inputs.version) || github.event.head_commit.message }}
on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      version:
        description: Published version to roll back to, X.Y.Z
        type: string
        required: false
      revision:
        description: hemi's settings revision to release on the running code
        type: string
        required: false
permissions:
  contents: read
jobs:
  cd:
    uses: willfell/wac.lab.actions/.github/workflows/wac-cd.yml@vX.Y.Z
    permissions:
      contents: write
      id-token: write
      packages: read
    with:
      build_runner: ${{ vars.CI_RUNNER_STANDARD }}
      rollback_version: ${{ inputs.version }}
      settings_revision: ${{ inputs.revision }}
```

Callers must:

- set a `run-name` that contains
  `format('settings revision {0}', inputs.revision)`, so a settings run is
  named exactly `settings revision <n>`: hemi finds the run it dispatched by
  that display name;
- call `wac-cd.yml` from a job whose id is `cd` and which has no `name:`, since
  hemi reads the jobs as `cd / <job>` (`cd / plan`, `cd / retag`,
  `cd / deploy`);
- declare both `workflow_dispatch` inputs, `version` and `revision`, with
  `required: false`: hemi dispatches one of them alone, and GitHub refuses a
  dispatch that omits a required input.

hemi refuses to cut an app over until its `cd.yml` meets the first two.

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
`workflow_dispatch` whose own `version` input is passed as `rollback_version`
and whose `revision` input is passed as `settings_revision`; on a push both are
empty, and a dispatch sets exactly one. hemi dispatches `revision` and reads
the run by its name, so the `run-name` above is part of the contract: a
settings run is named exactly `settings revision <n>`. `wac-cd` does not set
the name; the caller does. So are the job names: the calling job's id is `cd`
and it has no `name:`, and no `wac-cd` job has one, so the jobs API shows
`cd / plan`, `cd / retag`, `cd / publish`, `cd / deploy` and the rest, which
is what hemi reads.

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label for every job but `build` when `build_runner` is set, in the caller's repo; it must provide `python3` 3.12 or later | empty: the caller's `CI_RUNNER_SMALL` |
| `build_runner` | `runs-on` label for `build`, the one job that runs `docker build` | empty: `runner` |
| `version_source` | `commits` computes the version from conventional commits; `caller` takes it from `version` | `commits` |
| `version` | the caller-computed bare `X.Y.Z` with `version_source: caller`; empty means no release | empty |
| `rollback_version` | a published version to release again; only on `workflow_dispatch` | empty |
| `settings_revision` | hemi's settings revision to release on the running code, with no build (**Settings-only releases** below); only on `workflow_dispatch`, never with `rollback_version` or `deploy: false` | empty |
| `deploy` | `false` publishes the images, chart and tag without releasing them | `true` |
| `build_args` | newline-separated `KEY=VALUE` build args passed to every image build | empty |
| `wait_timeout` | seconds `plan` waits out an unavailable gateway for the settings, and seconds to wait for the gateway's release proof | `2100` |

| Secret | Meaning |
| --- | --- |
| `npm_token` | optional token for private npm packages; empty uses the job's GitHub token |
| `build_secrets` | optional newline-separated `KEY=VALUE` build-time values, mounted into every image build as BuildKit secrets with id the lower-cased `KEY` (**Build secrets** below); empty mounts none |

| Output | Meaning |
| --- | --- |
| `mode` | `release`, `publish`, `resume`, `rollback`, `settings` or `none` |
| `version` | the released or published version, bare `X.Y.Z` |
| `tag` | the git tag for that version |
| `images` | JSON map of the built images with digests, or in `settings` mode the retagged ones; empty on resume and rollback |

The jobs are `plan`, `build` (or `retag` in `settings` mode), `publish`,
`deploy`, `github-release` and the observation-only `shadow` (below),
serialized per repository by a concurrency group that never cancels a run in
progress. A cancelled release is worse than a queued one. The group does not
queue every run, though: GitHub keeps at most one pending run per group, so a
newer pending run replaces an older one that has not started. For pushes that
is harmless, because the later push's version covers the earlier commits. A
replaced rollback dispatch never runs, so check that a rollback started before
walking away from it. A dispatch can also replace a pending push run: a
rollback or settings dispatch that arrives while a push waits behind a running
release takes the push's place, and that push's commits stay unreleased until
the next push. hemi's guard is to dispatch only when no `cd.yml` run is queued
or running.

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

**Register a new app with Lab before its first `wac-cd` run.** The gateway
answers `contract` only for a repository it holds as a release target with a
chart, so from `v1.35.0` on the order is: Lab's registration first, then the
app's first pull request (the one that adds `.github/wacd.json`, `cd.yml` and
`ci.yml`). With the registration in place the gateway answers "no copy", and
`plan` carries on from the files, `deploy: false` included. Before it, every
run of the app fails at `plan` with the uncoded-denial sentence, which ends
`if this app is new, register it with lab first.` Under `v1.34.0` a
`deploy: false` run never asked the gateway, so the app's first chart could be
published before the registration; that order no longer works.

| The gateway answers | Settings come from | The chart also carries |
| --- | --- | --- |
| a copy whose `flags.source` is `hemi` | the copy: its profile is derived by `wacd-profile.py --contract`, its `runtime` is the values | `wacwini.com/revision`, `wacwini.com/fleet-commit` |
| a copy whose `flags.source` is `files` | `.github/wacd.json` and its values file, as before | `wacwini.com/revision`, `wacwini.com/fleet-commit` |
| no copy, and the repository has `.github/wacd.json` | `.github/wacd.json` and its values file, exactly as before | nothing new |
| anything else | nothing: `plan` fails before anything is built | |

Anything else is a copy that cannot be read (a `hemi` copy whose `runtime`
sets `app`, `images` or `global`, which `wacd-chart.py package` owns and would
refuse in `publish`, after the tag, included), a copy whose
`identity.repositoryId` is not the job token's `repository_id`, a copy whose
source is `files` (or no copy) in a repository with no `.github/wacd.json`, a
denial, any other status, or a gateway still answering 503 after the bound.
A cut-over repository whose copy is missing is refused by the gateway itself
(`fleet_copy_missing`), never answered with null, so it cannot fall back to
stale files.

When the copy is acted on, `.wacd-contract/` holds `contract.json` (the copy as
received), `profile.json` (the profile it stands for, in `.github/wacd.json`'s
shape, its `values_file` pointing at `.wacd-contract/values.json`) and
`values.json` (the copy's `runtime`). When the source is `hemi`, `plan` uploads
the directory as the artifact `wac-cd-contract`; a `files` copy uploads
nothing, since only `publish` reads the artifact and only for `hemi`, and the
chart takes that copy's revision and fleet commit from `plan`'s outputs. `plan` reads its profile
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
within a day of the run, or its `publish` finds no copy and fails, with the
step after the download saying so in an `::error::` annotation and the run
summary: `The settings copy this run's earlier job uploaded could not be downloaded. It is kept for one day, so a re-run more than a day after the run started must re-run all jobs, not only the failed ones.` "Re-run all
jobs" runs `plan` again, which reads the copy afresh and replaces the artifact.

**What runs beside the token.** `plan`, `shadow` and `deploy` hold
`id-token: write`, and a GitHub OIDC token does not say which step minted it, so
none of them runs anything fetched at run time. Each uses the runner image's own
`python3`, which its first step requires to be 3.12 or later and to import the standard modules the scripts need, failing with the
runner's name, OS and architecture and the `python3` path and version it found
(`shadow` records its clock stamp first, then checks), and each uses only actions
pinned by full commit SHA, with the tag in each step's name. `deploy` and
`shadow` used to run `actions/setup-python` on tag-pinned actions, which
downloaded a Python build at run time on the fleet's runners. A caller's
`runner`, or `CI_RUNNER_SMALL` when `runner` is empty, must therefore provide
`python3` 3.12 or later with the standard library intact: the fleet's runner
image has 3.12.3, and a runner on an image with an older or stripped-down Python
fails these jobs at that step, before any token is requested. `build_runner`
routes only `build`, which holds no `id-token` and still uses `setup-python`,
so the requirement does not fall on it. The step also imports every standard
module the scripts these jobs run import (`ssl`, `json`, `http.client`,
`urllib.request` and the rest, held to the scripts' imports by the workflow
test), and names the first one it cannot import, with the exception's type,
together with the runner, the `python3` path and the version, instead of
failing later inside the gateway call; an interpreter that dies importing
them (a signal, a crash) is named by its exit status.
`.github/scripts/test_reusable_workflows.py` holds every job of every reusable
workflow here, and every job anywhere that holds `id-token: write`, to an
allowlist of actions pinned by full commit SHA, one commit per action, each
recorded with the release tag it was resolved from and named with that tag in
its step; a job that calls a reusable workflow names a local file or another
repository's workflow at a full commit SHA. A job of `wac-cd` or `wac-ci` that holds `id-token: write` may use
only the checkout and artifact actions on that list, exempts none, and is refused a step that runs
`install-tools.sh`, `pip`, `npm`, `npx`, `uvx`, `curl` piped to a shell, or
`get-helm`. `install-helm.sh` is allowed: it checks a committed digest before
it runs anything.

Nor does anything in those jobs run code from the calling repository. In
`plan` and `shadow` the working directory is the app's checkout, and a
`python3 -c` puts the working directory first on the import path, so a
`json.py` at the root of an app's `main` would be imported beside the token.
So the jobs set `PYTHONSAFEPATH=1` for every step, and every inline
`python3 -c` runs as `python3 -I -c`. A script is started by its path in the
shared checkout, outside the workspace, and the scripts load each other by
path, so `PYTHONSAFEPATH` costs them nothing. git reads the checkout as data:
the hooks, filters and diff drivers it could run are configured only in git
config, which `actions/checkout` and the runner write and a repository's tree
cannot, and `.gitattributes` can only name a driver that config defines.
Helm, in `shadow`, gets a scratch copy of the values, the probe chart from the
shared checkout and empty Helm directories. No step sources a file, and none
runs `npm`, `node` or `make`. `.github/scripts/test_reusable_workflows.py` runs
every step of these jobs, and of `wac-ci`'s `contract`, in a checkout that
plants `json.py`, `ssl.py` and `sitecustomize.py`, each of which leaves a
marker when imported, and fails on any marker. It runs each step twice, once
with the job's env and once without `PYTHONSAFEPATH`, and checks that each
job's env alone keeps a bare `python3 -c` from the plant, so removing either
protection from any of these jobs fails it.

A 503 (`release dependency unavailable`: GitHub slow, rate-limited or down
behind the gateway) is retried with a fresh token within `wait_timeout`
seconds (default `2100`), the bound `deploy` gives a `release`, passed to the
script as `--wait-timeout` and parsed by the same function `deploy` uses, so
both jobs accept exactly the same values. `plan`'s wait is capped at 2100
seconds (35 minutes) whatever the input, while `deploy` honours the full
`wait_timeout`: `plan` has `timeout-minutes: 68` (GitHub expressions have no
arithmetic, so it cannot be sized from the input), and the cap makes a long
outage end in `plan`'s own summary line rather than the job timeout. The 68 is
a strict sum: 35 minutes for the capped wait, one for its last request (a token
mint and a connection), and 32 for every other step's own kill switch (one to
six minutes each; `Plan the settings release`'s six hold the status read's 120
seconds), so even a run in which every step uses its whole bound ends at a
step's kill switch, never at the job's.
`.github/scripts/test_reusable_workflows.py` holds the sum. A push release is
unaffected unless a step hangs. The interval starts at 5 seconds and
doubles up to 30, so a long outage costs about two requests a minute. Each
request may wait the whole time left for its answer, as `deploy`'s requests
do, because the gateway checks the caller against `main` with several GitHub
calls before it reads wac.fleet and can be slow. A certificate that fails
verification fails at once, and so does any transport failure not listed
below (a TLS protocol error from a wrong address, say) and a request still
unanswered at the deadline; all read `could not be reached`.

**A gateway restart is ridden out.** The gateway's Deployment is `Recreate`,
so a rollout leaves about 35 seconds with no endpoint, and from `v1.35.0`
every release's first step calls it; the gateway also sheds load by closing a
socket before the TLS handshake. In the `contract` call, a connection
nothing answered on is retried with a fresh token and the same back-off for
at most 90 seconds from the first such failure in a row, and never past the
wait above; after that it reads `could not be reached`. That is a connection
refused or reset, one closed during the TLS handshake (`ssl.SSLEOFError`) or
before the status line, a connect timeout, and no route to the host or
network (`EHOSTUNREACH`, `ENETUNREACH`); `wacd-release.py`'s
`no_connection()` names them.

From `v1.36.0` `deploy` rides out a restart too, telling apart a request that
never left the runner from one the gateway may have read
(`wacd-release.py`'s `never_sent()`). A connection refused, reset or closed
while connecting or in the TLS handshake, a connect timeout, or no route is
nothing sent: the release call, the downgrade pre-check and every status read
of the proof are asked again with a fresh token, backing off from 5 seconds
doubling to 30, for at most 90 seconds from the first such failure in a row
and never past `wait_timeout`, and then `Release through the gateway` fails
with `The gateway could not be reached for <n> s, longer than a restart is
waited out or than the time left; ...`. A status read changes nothing, so
one whose connection was reset or closed after it was sent, or whose answer
was cut off, is asked again the same way. A `release` lost after it was sent
may have been acted on, so it is never re-sent blind: `deploy` reads `status`
first. When the status's `version` is the version being released, the proof
goes on from that status as if the release had answered, and the receipt's
`released` record is `{"version", "chart_digest", "from_status": true}`,
taken from it. When it is not, the release is sent once more with a fresh
token, which the gateway admits as an idempotent repeat: each token's `jti`
is consumed once, a version already in its ledger under the same chart digest
skips the fleet checks and is not recorded again, and the Application is
patched only when it is not already synced at that digest. A second loss
fails with `The gateway dropped the release of <version> after it was sent,
twice, and its status does not show <version> targeted, so the release
stopped; run the deploy again.` A certificate that fails verification and
every other transport failure still fail at once with `release transport
failed; check TLS trust and service readiness`, a coded refusal is still
final, and `wait_timeout` still bounds it all. When the release waited out a
restart, the run summary gains one line after the `### wac-cd deploy` table:
`The gateway was restarting; the release waited <n> s for it.`

Every other `plan` step has its own `timeout-minutes` (one to six minutes), so
only the gateway wait can use most of the job's 68 minutes: a stalled checkout,
upload or tag push fails within minutes instead. `wacd-tag.py` bounds each
call that reaches the remote (`ls-remote`, `push`) at 60 seconds and kills git
and its transport helpers when it passes, and lets go of git's output 5 seconds
later even when a helper that left git's process group (a daemonizing
credential helper) still holds it; a timed-out push fails the plan and
is never read as superseded. `Check the trigger` runs before the gateway is
asked, so a malformed dispatch fails at once, even during an outage.

While `plan` waits out an outage it holds the repository's `cd` concurrency
group for up to the capped wait, about 35 to 40 minutes, and since a group
keeps one pending run, a newer push then replaces an older pending one. That
is the wait `deploy` already had before
`v1.35.0`, moved earlier, not a new delay. A coded 403 is not retried. An
uncoded 403 is asked twice more, 10 seconds apart and each time with a fresh
token, before it fails: the gateway also answers that way when it could not
check a token (its fetch of GitHub's signing keys failed, or its replay store
was busy), which a moment later it can. A
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
| `fleet_repository_unreachable` | The gateway's GitHub App can no longer read wac.fleet; the owner must restore its access, so re-running will not help. |
| `fleet_repository_mismatch` | The gateway's pinned wac.fleet no longer names wacwini/wac.fleet; the gateway needs a change, so re-running will not help. |

`fleet_commit_not_on_default_branch`, `fleet_commit_older_than_last_release`,
`revision_annotation_missing` and `revision_mismatch` come only from the
gateway's release check, which answers a `release` in `deploy`; that check can
also return the three `fleet_copy_*` codes, for the copy at the chart's own
fleet commit, while the `contract` request returns only those three and the
two `fleet_repository_*` codes. Those two (wacwini/wac.lab#575) can come from
either and name a fault on the gateway's side that a re-run cannot clear;
like every coded refusal, they are never asked again. `scripts/wacd-release.py` holds the map, and both
scripts use it: `deploy` fails with
`release request refused (HTTP 403): <sentence>` and adds
`The gateway refused the release: <sentence>` to the job summary. Its POST
keeps only a 403's `reason` and drops the rest of every body but a 200's.

A code not in that list gets
`The gateway refused this request for a reason this wac-cd does not know (code <code>).`
(without the parenthesis when the code is not a plain lowercase word), so a
code added to the gateway later cannot break a release summary. A denial
without a code reads
`The gateway did not accept this run as a release of a registered app.` From
`plan`, after its three attempts, it reads
`The gateway refused this run's token on each of 3 attempts (HTTP 403 with no reason), so nothing was built; a gateway that does not yet serve the contract operation (wacwini/wac.lab#570 not deployed) looks like this too; if this app is new, register it with lab first.`
The revision is a JSON number in the copy and a decimal string in the
annotation, of at most ten digits as the gateway reads it; `wacd-chart.py`
converts between the two in one place.

**What `plan` writes to the run summary.** Two sections, in this order. The
settings step writes `### wac-cd settings`: a table with the columns
`source`, `revision` and `fleet commit`, one row, then one sentence. The
fleet commit column holds the full 40-hex commit; a `-` stands for a value the
run does not have.

| The gateway answered | Row (source, revision, fleet commit) | Sentence |
| --- | --- | --- |
| a copy whose source is `hemi` | `hemi`, `<n>`, the 40-hex commit | `Settings come from hemi's copy, revision <n>, read at wac.fleet commit <12 hex>.` |
| a copy whose source is `files` | `files`, `<n>`, the 40-hex commit | `hemi's copy (revision <n>) says this app's settings live in its files, so they come from the files and the chart names revision <n>.` |
| no copy | `files`, `-`, `-` | `The gateway holds no copy of this app's settings, so they come from the repository's files, as before.` |
| a refusal, or anything else that stops the run | `-`, `-`, `-` | the sentence that says why, from the list above or from the cases under **Settings** |

The `<12 hex>` is the first twelve characters of the commit in the table. The
sentence is written to the run summary only; the job log carries the
`Set output` lines but not the sentence, so read it on the run's page.
`Plan the version` then writes `### wac-cd plan`: a table with the columns
`mode`, `version`, `tag` and `expected sha` and one row, such as `release`, the
new version, its tag and the commit, followed by the sentences of the run (a
resume, a superseded run, `mode: settings`) when it has any. Every run that
reaches `Plan the version` has both sections; the first differs by what the
gateway answered, as in the table above.

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
| `settings` | `workflow_dispatch` with `settings_revision` | retag, publish, deploy, github-release |
| `none` | `workflow_dispatch` with `settings_revision` when hemi's copy already holds a newer revision | nothing |
| `none` | `version_source: caller` with no version, or a tagged HEAD whose chart is published, with `deploy: false` | nothing |
| `none` | a `release` or `publish` run whose tag push was refused after `main` moved on: superseded | nothing |

Every run lists all of `plan`, `build`, `retag`, `shadow`, `publish`,
`deploy` and `github-release`, and a job a mode does not use is listed with the
conclusion `skipped`, not left out. A `release` of a code merge reads
differently by where its settings came from:

| Settings came from | `plan` | `build` | `shadow` | `retag` | `publish`, `deploy`, `github-release` |
| --- | --- | --- | --- | --- | --- |
| the files (a copy whose source is `files`, or no copy) | `success` | `success` | `success` | `skipped` | `success` |
| hemi's copy | `success` | `success` | `skipped` | `skipped` | `success` |

`shadow` runs only when the settings came from the files, since a copy has no
files to compare it with, and `retag` runs only in `settings` mode, so a code
release from hemi's copy skips both, and the run is green with those two
`skipped`. `deploy` ends with `Verified served build: <the merge commit>`.

`deploy: false` is publish-only, used for cutover step 1: the old route still
deploys while `wac-cd` publishes the first deploy chart. From `v1.35.0` Lab's
registration of the app comes first, because `plan` asks the gateway for the
settings in every mode, `deploy: false` included, and the gateway answers only
for a registered app (**Register a new app with Lab before its first `wac-cd`
run** above). `rollback_version` with `deploy: false` fails.

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
re-run it. A chart recorded from another SHA fails the plan. The resumed chart
ships the settings it was published with, not the copy as it is now, so the
`wac-cd plan` summary adds `Resuming <V>, whose chart was published with
settings revision <n>.` (or `from the repository's files.`), read from the
chart's `wacwini.com/revision` annotation; a read that fails says so and does
not fail the run.

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
`deploy` asks the gateway for `status` once (retrying 503 and a gateway
restart, within `wait_timeout`), and when the running `version` is a strict `X.Y.Z` newer than
the version being released, it fails naming both and pointing at a rollback
dispatch. A rollback skips that refusal; an equal, older or non-`X.Y.Z`
running version (the bootstrap tag before the first release) proceeds.

**No replay of an old version.** A `release` or `resume` run is refused when
`origin/main` carries a `<tag_prefix>X.Y.Z` tag that HEAD does not contain, tagged
HEAD or not (a re-run of a run whose first attempt failed before it tagged, for
instance), or when HEAD's highest tag is lower than the highest tag on
`origin/main`. The next version is picked from the tags merged into HEAD;
this rule counts the tags merged into `origin/main`, in the same version
order. A tagged HEAD gets `This run is for <tag>, but <newest> is
already published. Roll back or release anew; an old build is never replayed.`
when the newer tag's commit contains HEAD, which is a real replay. When it does
not, HEAD's version was overtaken rather than replayed, and the run gets
`<tag> was taken by a later release (<newest>) that does not contain this
commit, which has not been released; push a new commit to release it.` That is
this sequence: commit c2's run claims `v1.0.1` and fails in build while `v1.0.0`
(c1) keeps running; a settings release then tags `v1.0.2` on c1, the running
code; "Re-run all jobs" on c2 is refused with that sentence, and only a new
commit releases c2's changes, as `v1.0.3`. From `v1.36.0` that settings
release is refused instead, by `Refuse a settings release behind an unfinished
code release` (**Settings-only releases**, rule 5), while c2's chart is
unpublished and no version above the running one has a GitHub Release, so
"Re-run all jobs" on c2 stays the way out. The sentence is left for a c2 whose
chart was published before its run failed, for a c2 a settings release passed
after a rollback (a version above the running one has a GitHub Release; rule
5), and for tags a settings release placed under an older pin; it reaches the run as `Refuse to replay an old version`. Git ancestry alone tells the two
apart, so when c2 had been released and then rolled back before the settings
release, the sentence still says it was never released.
An untagged HEAD, or a tagged one that lacks a tag on main, gets `This commit is
older than <tag>, which is already published and contains or replaces it. Roll
back or release anew; an old build is never replayed.` The sentence goes to the
run summary and stderr, and `plan` fails before the chart is checked, before
`claim-tag` pushes a tag and before anything is built, so no tag or chart is
left behind. Only tags on `main` count, so a stray tag on another branch cannot
block a run; the full-depth checkout `plan` already makes carries `origin/main`,
and a checkout without it fails the plan rather than judging by every tag. The
tip of `main`, and a re-run of the newest version (every tag on `main` is then
contained in HEAD), proceed as before; a commit carrying several tags counts by
its highest. What is running changes by a rollback or a new release, never by
replaying an old build. A rollback is not refused: it names its version and does
not consult this rule. A `deploy: false` run is not refused either, since it
never deploys.

**The refusal is its own step.** `Plan the version` records a refused replay
as the step output `replay_refused` and succeeds, setting no mode, version, tag
or expected SHA; the next step, named exactly `Refuse to replay an old version`,
runs only when that output is set, writes the sentence to stderr, to an
`::error::wac-cd:` annotation and to the run summary, and exits 1. It is the
last step of `plan`, so `plan` fails, every job that needs it is skipped, and
nothing was tagged or published. Hemi reads a refused replay from the Actions
jobs API as the job `cd / plan`, step `Refuse to replay an old version`,
conclusion `failure`; any other failure of `plan`, including a shallow checkout,
a tag collision or a missing `origin/main`, fails `Plan the version` itself and
leaves this step skipped. Do not rename the step. `wacd-version.py` tells the
two apart by exit code: 3 for a refused replay, 1 for any other failure.

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
compare it with, and the job is listed with the conclusion `skipped` (**Modes**, above). It mints a GitHub
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
| deadline after the stamp, shared by both checkouts, the Python check, the install step and the script | 95 |
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

## Settings-only releases

A settings change saved in hemi goes live with no pull request and no build:
hemi dispatches `cd.yml` with `revision`, and `wac-cd` releases the running
version's images again under a new version, with the chart rendered from
hemi's copy. The pipeline and the gateway require each image's tag to equal
the chart version, and the gateway's ledger never publishes a version twice,
so a settings-only release is a new version whose images are the running
ones, retagged.

`plan` checks the trigger (exactly one of `rollback_version` and
`settings_revision` on a dispatch, a revision of one to ten digits, not with
`deploy: false`; a dispatch with neither fails as it always did), reads the
copy as for any run, and then, in `Plan the settings release`:

1. Requires the copy's `source` to be `hemi`; a copy whose settings live in
   the files fails. When the copy's `identity.revision` is newer than
   `settings_revision`, the run ends as mode `none`, green, with the summary
   line `mode: none (a newer revision is live; its own release follows)`: that
   save dispatched its own run. A copy older than the dispatched revision
   fails, since the save has not reached the fleet copy yet.
2. Reads the running version V from a steady gateway `status`
   (`wacd-release.py running`). The step sets one wall-clock deadline, 120
   seconds after it starts, and passes it as `--deadline` to each of its
   three network reads: this one, rule 5's and rule 6's. The status wait ends 15 seconds
   before the deadline, starts no request with under 2 seconds of the wait
   left, and bounds each read by the time left, its token mint included (the
   mint is given the time left in the wait, at most its usual 20 seconds), so
   an app that never settles reaches the rollout refusal with its sentence,
   naming the seconds it waited: a mint that has not succeeded when the wait
   ends, after the app was seen rolling out, ends the wait like any other
   read. At the deadline
   itself a `SIGALRM` backstop stops a read that trickles past every socket
   timeout and fails the step with a sentence; each read disarms it and
   restores the previous handler as soon as it decides, so it cannot fire
   after a decision. The step's other work is local git. The status's `version` is
   the version the gateway last pointed the Application at, which mid-sync or
   right after a rollback dispatch is already the new target, so it counts
   only when the same answer is Synced at its `chart_digest` and Healthy, every
   Deployment is rolled out (observed generation, and updated, ready and
   available replicas all equal to replicas; a Deployment listed by name only
   is not), and every image the Deployments run from the fleet's registry
   (`localhost:30500/`) is one of V's `wacwini.com/images` references by
   repository and digest, with at least one such image. Images from any
   other registry, such as a third-party sidecar, are ignored. The status
   lists only the Deployments' containers, so this is containment, not
   equality. V's chart is read at the push address, and
   `chart_digest` must be its manifest digest. An unsteady answer is read
   again; one that never settles is refused (below). A `version` that is not
   a bare `X.Y.Z` (an unknown digest or a lost ledger), a `chart_digest` the
   registry does not hold, or a 403 without a reason (the gateway refused the
   status read: an app it does not know, or a token it could not check) fails
   `plan` at once; a 503 (the gateway cannot read Kubernetes)
   is retried within the bound. After a rollback V is the rolled-back version,
   never the newest tag. C, V's source commit, and V's digests come from the
   same chart.
3. Fails when the copy names other images than V runs (that needs a build:
   release code first).
4. Takes V' as the next patch above the highest `<tag_prefix>X.Y.Z` tag merged
   into `origin/main` (`wacd-version.py --mode settings`), with the tag on C,
   which must be on `main`. The tag goes on C, not on `main`'s head, because a
   tagged head counts as released: a settings tag there would make the next
   code release resume onto old images. On C, the next merge versions above V'.
5. Unless V's chart already carries `settings_revision` (rule 6's re-prove
   of V, which claims no tag and so cannot overtake anything), refuses the
   settings release behind an unfinished code release, before a new version
   is chosen and before anything is tagged or retagged.
   `wacd-version.py --mode settings` lists the versions on `main` above V
   (`above=`) and those of them a code release claimed (`codes=`): a code
   release tags a commit that no `<tag_prefix>X.Y.Z` tag names yet, while a
   settings release tags code an earlier version already ran, so a version is
   a code release's when no lower tag with the prefix sits on its commit.
   `wacd-release.py unfinished-release` then reads `deploy/<app>:<version>` at
   the push address for each, newest first, within the step's deadline, each
   read bounded by the time left and the same `SIGALRM` backstop behind it.
   An absent chart of a settings claim is passed over, since rule 6 takes
   that claim again.
   - When the first chart present comes before any absent chart of a code
     release, there is no unfinished code release (one below a published
     version could only be replayed): the scan ends there, reads no Release,
     and the run proceeds with nothing added to the summary. With no version
     above V nothing is read.
   - Otherwise the first absent chart of a code release is the newest
     unfinished code release, the one a re-run could still release. The scan
     goes on through the charts below it, and for each one present reads its
     GitHub Release with the job's `GITHUB_TOKEN`, through rule 6's reader
     (`GET /repos/{repo}/releases/tags/{tag}`, 404 for none). A Release is the
     only evidence of a rollback: `github-release` creates one only after
     `deploy` proved the served build, and an older build runs below a proved
     newer one only by a rollback. V is never taken as rolled back merely
     because it is older or below a published chart, and no run list is read.
     A published chart with no Release (a code release whose deploy failed or
     whose proof timed out, a `deploy: false` publish, a settings release
     whose deploy failed) proves nothing and is passed over.
   - After a rollback (a version above the running one has a GitHub Release),
     the settings release proceeds past the unfinished code release, which
     can now ship only as a new commit, and the run summary gains exactly one
     line after the `mode: settings` summary: `<tag> was left unfinished and
     can now ship only as a new commit.` V' goes above that claim as usual. A
     later "Re-run all jobs" on the code release is refused at `Refuse to
     replay an old version`, whose sentence ends `push a new commit to release
     it.`
   - With no Release above V (#83's c2 shape: V runs, and the next code
     release failed in `build`, with or without a never-deployed chart between
     them), the claim records the refusal for `Refuse a settings release
     behind an unfinished code release` (below): a settings version above it
     would overtake it, and re-running the code release, the way out, would
     then be refused.

   A Release that cannot be read (any answer but 200 or 404, or none)
   concludes nothing, neither a rollback nor a refusal: `Plan the settings
   release` fails with `the GitHub Releases of <app> could not be read, so
   this settings release cannot tell a rollback from an unfinished release;
   run it again.` A chart that cannot be read fails `plan` with a sentence and
   never counts as absent, and the scan reads at most 25 charts, failing with
   a sentence when it runs out of them.
6. Never spends a second version on one revision, and never undoes a
   rollback:
   - when V's chart already carries `settings_revision`, V' is V (a re-run
     after the release went live; releasing it again changes nothing);
   - otherwise `wacd-release.py earlier-release` judges the versions on
     `main` above V, newest first. A version counts as released only with
     evidence that it was proved running: its GitHub Release, which `github-release` creates
     only after `deploy` proved the served build, read with the job's
     `GITHUB_TOKEN` (`GET /repos/{repo}/releases/tags/{tag}`; `plan`'s
     existing `contents: write` covers it). A rollback creates no Release for
     the version it leaves (it creates the Release of its own target when
     that is missing), so the evidence survives it; V's own Release is never
     read. When a version above V carries `settings_revision` and has a
     Release, `plan` fails with `Revision <n> was released as <V'old>, and <V>
     runs now after a rollback. To put those settings on the running code,
     make them live again in hemi; they are released as a new revision.`
     Nothing is tagged or retagged: a re-run nobody dispatched must not put
     those settings back over a deliberate rollback.
   - a version above V that carries `settings_revision` and has no Release
     was never proved running (a code release whose gateway release was
     refused or whose proof timed out, a `deploy: false` publish, a deploy
     that failed after `Release through the gateway`, or a run cancelled after
     it), so it does not refuse. Such a version may still have reached the
     cluster; a GitHub Release is the only proof wac-cd keeps that it ran. When it is `main`'s newest tag and sits on C (the
     claim on the running code), V' is that version, as it is when that claim
     has no chart yet; otherwise V' is the next patch.
   - a 404 for the Release means not released; any other answer, or none,
     fails `plan` with a sentence and never counts as absent.
   - Copy revisions only grow with versions, so the scan stops at the first
     chart with an older revision, or with none (charts from before any
     copy); a version with no chart is skipped. It reads at most 25 charts,
     and running out of them fails `plan` with a sentence.
7. Unless V' is V, compares `.github/workflows` at C with `origin/main`
   (below). Re-proving the live version claims no new tag, so it needs no
   check.

`Plan the version` then claims V' on C with `wacd-tag.py claim-tag --commit`.
A refused tag push in `settings` mode fails `plan`; it is never `superseded`
and never mode `none`. The summary says `mode: settings` and
`settings revision <n> on <V>'s code (<C short>), released as <V'>`, also
when V' is V.

`build` never runs in this mode. `retag` runs in its place, and only in this
mode: `scripts/wacd-retag.py` first reads the tag V' itself: when it already
serves V's digest (a re-run) nothing is written, and when it serves another
digest the retag fails, since a version is never moved. Otherwise it reads each
of V's image manifests by digest from
`registry.network.svc.cluster.local:5000`, accepting the OCI and Docker
manifest and index media types, checks the bytes against the digest, and PUTs
the same bytes with the same `Content-Type` under the tag V'. No layer is
copied. `retag` holds no `id-token`: the registry takes no credential.
`publish` renders the chart from the copy `plan` read, as for any release from
hemi's copy, with source commit C, both `wacwini.com/` annotations and image
references `localhost:30500/<name>:<V'>@<digest>`, and accepts the tag on C.
`deploy` (which needs `retag`) and `github-release` run as for `release`, and
the health check expects C.

**The refusals are their own steps.** Each is the C3 pattern: `Plan the
settings release` records the refusal as a step output and succeeds, setting
no mode, version or tag, and the named step writes the sentence to stderr, an
`::error::wac-cd:` annotation and the run summary, and exits 1, before
anything is tagged or retagged. All three run only in `settings` mode and
only then, and are skipped in every other run: a push, a rollback and a
`deploy: false` run skip `Plan the settings release`, so none of them reads
the charts above V or records a settings refusal. They sit beside `Refuse to
replay an old version`; hemi reads all four by job `cd / plan`, step name and
conclusion. Do not rename any of them. A refusal whose sentence comes out empty
(the script printed none) never records an empty output, which would skip its
step and leave a green run that released nothing: the planning step fails
itself with a fixed sentence instead, such as `The settings release was refused
while the app rolls out, and the refusal gave no reason.`, `The settings
release was refused behind an unfinished code release, and the refusal gave no
reason.` or `This run was refused as a replay of an old version, and the
refusal gave no reason.`

- `Refuse a settings release while the app is rolling out`: the status never
  became steady within the status wait, which ends 15 seconds before the
  step's 120-second deadline. The sentence names V and says no settings
  were released; dispatch again once the app settles.
- `Refuse a settings release behind an unfinished code release`: a code
  release claimed a version above V and its chart was never published (rule
  5), for instance a run that failed in `build`. The step writes `<tag> was
  claimed by a code release that never published; re-run that release, or
  release a new commit, before releasing settings.`, naming the tag, such as
  `v1.0.1`. Re-running that code release releases it; dispatch the settings
  release again afterwards. It is never shown after a rollback (a version
  above the running one has a GitHub Release): the settings release then
  proceeds past the claim with the summary line above.
- `Refuse a settings release behind a workflow change`: GitHub refuses a
  `GITHUB_TOKEN` tag push at a commit whose `.github/workflows` differ from the
  default branch's (**Why the tag comes first** below), and C is usually
  behind `main`. When `git diff --quiet <C> origin/main -- .github/workflows`
  reports a difference, the step writes `Release code first: <app>'s workflow
  files changed since <V>, so its settings cannot be released on <C short>.`

Every other failure in planning (a fault in the status, a copy that cannot be
released on its own, a chart above V that cannot be read, an earlier release of
the revision, a refused tag claim)
fails `Plan the settings release` or `Plan the version` itself.

**How hemi reads the outcome.** A settings run released if and only if its
`cd / retag` and `cd / deploy` jobs concluded `success`. The run summary says
`mode: settings` or `mode: none (<reason>)`. A green run that released
nothing means exactly one thing, superseded. `wac-cd` never undoes the
gateway's patch: after `Release through the gateway` succeeds the Application
points at V' whatever happens next.

| Outcome | Run conclusion | Decided by | Tagged | Retagged | Cluster patched |
| --- | --- | --- | --- | --- | --- |
| Superseded | `success` | `cd / plan` `success`, and `cd / shadow`, `cd / retag`, `cd / publish`, `cd / deploy` and `cd / github-release` all `skipped`; summary `mode: none (a newer revision is live; its own release follows)` | no | no | no |
| Behind a workflow change | `failure` | `cd / plan`, step `Refuse a settings release behind a workflow change` `failure` | no | no | no |
| Behind an unfinished code release | `failure` | `cd / plan`, step `Refuse a settings release behind an unfinished code release` `failure`, sentence `<tag> was claimed by a code release that never published; re-run that release, or release a new commit, before releasing settings.` | no | no | no |
| Proceeds past an unfinished code release after a rollback (a version above the running one has a GitHub Release) | `success` | `cd / retag` `success` and `cd / deploy` `success`, as Released; the summary also carries `<tag> was left unfinished and can now ship only as a new commit.` | yes | yes | yes |
| Mid-rollout | `failure` | `cd / plan`, step `Refuse a settings release while the app is rolling out` `failure` | no | no | no |
| Already released, then rolled back | `failure` | `cd / plan`, step `Plan the settings release` `failure`, sentence `Revision <n> was released as <V'old>, and <V> runs now after a rollback. To put those settings on the running code, make them live again in hemi; they are released as a new revision.` | no | no | no |
| Failed before deploying | `failure` | `cd / plan`, `cd / retag` or `cd / publish` `failure` with `cd / deploy` `skipped` | if `cd / plan` succeeded | if `cd / retag` succeeded | no |
| Failed at the release | `failure` | `cd / deploy` failed at `Release through the gateway`: the release may or may not have been accepted. The app's target tells them apart: if it is V' (its chart carries the settings revision), the patch landed and this is failed after deploying; if it is not, nothing was patched and this is failed before deploying; if hemi could not read the target since the run ended, it concludes nothing yet. | yes | yes | by the app's target (see that cell) |
| Failed after deploying | `failure` | `cd / deploy` step `Release through the gateway` `success`, then step `Prove the served build` `failure` | yes | yes | yes, and it stays |
| Failed at another deploy step | `failure` | `cd / deploy` `failure` at a step other than `Release through the gateway` and `Prove the served build`, keyed on where it sits: before `Release through the gateway` (the python3 check, the shared checkout, `Read the published images`, `Summarize the release`), so `Release through the gateway` is `skipped`, is failed before deploying; `Upload the release receipt`, after `Release through the gateway` `success` and with `Prove the served build` `skipped`, is failed after deploying, patched and not proved; `Record the proof`, after `Prove the served build` `success`, is patched and proved, with `cd / github-release` `skipped` | yes | yes | no before `Release through the gateway`; yes after it, and it stays |
| Cancelled | `cancelled` | the run's conclusion; `cd / deploy` `success` or its step `Release through the gateway` `success` says the patch landed | if `cd / plan` succeeded | if `cd / retag` succeeded | only if `Release through the gateway` succeeded |
| Released | `success` | `cd / retag` `success` and `cd / deploy` `success` | yes | yes | yes |

`Release through the gateway` asks for the release and then waits, within
`wait_timeout`, for the gateway's proof that it rolled out. A refused release
(403), a refused downgrade or a gateway unavailable for the whole wait patches
nothing; a release the gateway accepted whose proof did not arrive in time
leaves the Application pointed at V'. Both fail the step with no difference
in its conclusion (a timed-out proof and a gateway that never answered print
the same `release evidence deadline exceeded`), which is why the "Failed at
the release" row reads the app's target instead.

**Re-runs and rollbacks.** "Re-run failed jobs" reuses `plan`, so it releases
the same V'; the retag finds V' already on the digests and writes nothing, and `publish` finds its chart.
"Re-run all jobs" plans again and lands on the same V' by rule 6, or refuses
by it after a rollback. A settings release after a rollback ships the
rolled-back version's digests on its code, above every published version. A
re-run of C's own code-release run then sees V' on its HEAD: while V' is the
newest it resumes V', a harmless re-release of the current settings; once
anything newer exists, **No replay of an old version** refuses it. When C was
`main`'s head, HEAD carries both V and V', and counts by V'.

**Known limit: a hand edit of wac.fleet.** A hand edit of wac.fleet that
keeps the revision number is not released: the running version is re-proved
unchanged. A hand edit must carry a new revision number (hemi's runbook).

**Known limit: a settings release over a published, unreleased code
release.** Rule 5 refuses a settings release only for a code release that
never published its chart. One whose chart was published without being proved
running counts as published: a code release whose run then failed (at
`deploy`, or with its proof timed out), and a deliberate `deploy: false`
publish, which leaves a chart and no Release by design. When it is the newest
code release above V, a settings release still plans V' above it, and "Re-run
all jobs" on that code release is then refused at `Refuse to replay an old
version` with `<tag> was taken by a later release (<newest>) that does not
contain this commit, which has not been released; push a new commit to release
it.`, so only a new commit releases that code. The same holds for a claim a
settings release overtook under a pin before `v1.36.0`.

**Known limit: a rollback leaves the unfinished code release behind.** After
a rollback (a version above the running one has a GitHub Release), a settings
release proceeds past an unfinished code release rather than block every
settings change until code ships over the rollback. That code release's own
re-run is then refused as above, and its changes ship only as a new commit.
The run summary names the tag: `<tag> was left unfinished and can now ship
only as a new commit.` A rollback from a version whose `github-release` job
itself failed leaves no Release to prove it, so there the settings release is
refused behind the unfinished code release instead.

**Known limit: tags placed outside wac-cd's rules.** Rule 5 tells a code
release's version from a settings release's by wac-cd's own tagging: a code
release claims only an untagged commit, and a settings release tags code that
V's tag already names. A tag placed by hand, deleted by hand, or left by an
older pipeline can be misread. A settings tag whose commit lost its lower tag
reads as a code release, so an unpublished one refuses its own re-run with a
way out ("re-run that release") that does not apply; release a new commit
instead. A second code tag on one commit reads as a settings claim and is
passed over, so a settings release can overtake it and leave its re-run to
`Refuse to replay an old version`.

**Known limit: a repository id that changes.** When a repository's id changes
(a delete and re-create under the same name), hemi starts its revisions at 1
again, while the tags and `deploy/<app>:<V>` charts persist under the app
name, and a chart annotation carries only the revision number, never the
repository id. After a rollback onto code from before the change, a new
revision can equal an old chart's number: on V itself it takes the V' = V
shortcut and ships none of the new settings, and on a version above V with a
Release it is refused by rule 6, and rule 6's scan can stop early and fail
open: the new lineage's charts read `none` or a number below the old one, so
the scan stops before an old chart above V that carries the number and has a
Release, and rule 6 may not refuse. `wac-cd` cannot tell the two lineages
apart; the gateway checks the copy's repository at release time, not the old
chart's. What remains is a re-run of a settings run while its revision is
still the active one after a rollback, which needs hemi to have neither
reverted nor reconciled; hemi's H14 (settings follow what runs) is the guard,
and is a precondition of the first cutover.

**The next code release may over-bump.** After a settings tag V' on a
rolled-back commit C1, the next merge counts its commits from V', the highest
tag it contains, so the range reaches back over commits that the newer
version already released. A `feat` already shipped there bumps the minor
version again. The invariant holds: the next merge always releases above V'.
Only the size of the step can be larger than the commits since the last
release call for.

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

## Build secrets

`build_secrets` carries values an image build needs that must not show in the
run's log, such as the key Next.js encrypts server actions with or a token a
build uses to upload source maps. It is a secret, never an input: GitHub masks
only values that arrive through `secrets:`, and `build_args` reaches the build
step through its `env:` block, which the log prints with its values. A caller
passes it in the `cd` job:

```yaml
    secrets:
      build_secrets: |
        NEXT_SERVER_ACTIONS_ENCRYPTION_KEY=${{ secrets.NEXT_SERVER_ACTIONS_ENCRYPTION_KEY }}
```

Each non-blank line is `KEY=VALUE`, with `KEY` matching `^[A-Z][A-Z0-9_]*$`
and the value everything after the first `=`, unchanged (it may be empty); a
trailing carriage return is dropped. Each line becomes
`--secret id=<key>,src=<file>` on every image build, after the npm token's
mount, where `<key>` is the `KEY` lower-cased:
`NEXT_SERVER_ACTIONS_ENCRYPTION_KEY` is mounted as
`next_server_actions_encryption_key`. A Dockerfile reads a mount only in the
`RUN` that names it, as an environment variable with Dockerfile syntax 1.10 or
later:

```dockerfile
# syntax=docker/dockerfile:1.10
RUN --mount=type=secret,id=next_server_actions_encryption_key,env=NEXT_SERVER_ACTIONS_ENCRYPTION_KEY npm run build
```

or, without `env=`, as the file `/run/secrets/<key>`.

The build step fails before any image is built on a line with no `=`, an empty
`KEY`, a `KEY` that does not match the pattern, `NODE_AUTH_TOKEN` (already
mounted as `node_auth_token` from `npm_token`), or a `KEY` given twice. Each
refusal names the line number, and the `KEY` once it is a valid name, and
never prints a value.

How the values are kept out of the log: the runner masks a multi-line secret
as a whole and line by line, so each `KEY=VALUE` line is masked, but a value
printed on its own would not be. `wacd-chart.py build-secrets` therefore reads
the lines from the step's environment, never from its arguments, and issues
an `::add-mask::` for each non-blank value before any image is built. It
writes each value to a file of mode 0600 in a fresh directory under
`$RUNNER_TEMP`. The step then removes `BUILD_SECRETS` from its environment, so
docker, crane and the helper scripts never inherit it, and deletes the
directory when it ends, whether or not the build succeeded; the runner also
empties `$RUNNER_TEMP` after every job. The values travel as files rather than
through `env=` so that a caller's `KEY` never becomes a variable of the build
step, where a name such as `PATH`, `VERSION` or `DOCKER_HOST` would change what
the step does. A value of only a character or two, once masked, hides every
occurrence of those characters in the rest of the log.

What a mount does not do: it keeps a value out of the log and out of the
image's layer history, and nothing more. When the build itself writes the
value into an output, the value is in the image. Next.js writes the server
actions encryption key into its server-reference manifest under
`.next/server`, so an image built with that key holds it whether the key came
through a mount or a build arg, and anyone who can pull the image can read it.

With `build_secrets` empty or not passed, the build is exactly as before: the
same `docker build` arguments, no directory and no masks.

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
