# Consumers

Every repo pinning this library, by component and file. Bumping a tag means
walking this table and opening one PR per repo whose pinned components changed;
`CHANGELOG.md` says which those are.

**Reconciled against the default branches of every non-archived wacwini
repository on 2026-10-16 UTC; the willfell rows are as of 2026-10-11 UTC.** Every `.github/` YAML file of each
repository was read from its default branch; code-search results and open PRs
were not used as evidence. The table records merged default-branch pins; an open
bump PR does not change a row.

The fleet's application, docs and platform repositories now live under the
`wacwini` organization and pin `v1.22.0`. jack-creek-patch's default branch is
`dev`, which is what this table records; its `main` moves to the same pin when
wacwini/jack-creek-patch#63 merges. The personal
`willfell` repositories pinned at older tags (`sauce`, `ero-copilot-iac`,
`mac-config`) are listed as they are and were not part of the `v1.22.0` walk.

Three workflows also check out this repository at a tag to run its scripts
directly, through `actions/checkout` with `repository: willfell/wac.lab.actions`
and a `ref:`. Those pins move with the shared component pins in the same file:
wacwini/baton `.github/workflows/ci.yml`, wacwini/wac.lab
`.github/workflows/warehouse-image.yml` and
`.github/workflows/ci-deploy-denial.yml`, all at `v1.22.0`.

| Component | Repo | File | Pin |
|---|---|---|---|
| `actionlint` | wacwini/baton | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.22.0` |
| `actionlint` | wacwini/jack-creek-patch | `.github/workflows/actionlint.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac.app.claw | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac.app.finance | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac.app.flights | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac.app.handbook | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac.app.travel | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac.app.wealth | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac.docs | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac.lab | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac.lab.remote | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/wac.plugins | `.github/workflows/ci.yml` | `v1.22.0` |
| `actionlint` | wacwini/will-fell | `.github/workflows/pr.yml` | `v1.22.0` |
| `actionlint` | willfell/ero-copilot-iac | `.github/workflows/lint.yml` | `v1.7.1` |
| `actionlint` | willfell/homebrew-sauce | `.github/workflows/ci.yml` | `v1.19.0` |
| `actionlint` | willfell/mac-config | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | willfell/sauce | `.github/workflows/ci.yml` | `v1.10.5` |
| `agents-md` | wacwini/baton | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac.app.claw | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac.app.finance | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac.app.flights | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac.app.handbook | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac.app.travel | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac.app.wealth | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac.docs | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac.lab | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac.lab.iac | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac.lab.remote | `.github/workflows/ci.yml` | `v1.22.0` |
| `agents-md` | wacwini/wac.plugins | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-gitops-deploy` | wacwini/wac | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-gitops-deploy` | wacwini/wac.app.finance | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-gitops-deploy` | wacwini/wac.app.flights | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-gitops-deploy` | wacwini/wac.app.handbook | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-gitops-deploy` | wacwini/wac.app.travel | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-gitops-deploy` | wacwini/wac.app.wealth | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-kubeconform` | wacwini/baton | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-kubeconform` | wacwini/wac | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-kubeconform` | wacwini/wac.app.finance | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-kubeconform` | wacwini/wac.app.flights | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-kubeconform` | wacwini/wac.app.handbook | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-kubeconform` | wacwini/wac.app.travel | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-release` | wacwini/baton | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-release` | wacwini/wac.lab | `.github/workflows/warehouse-image.yml` | `v1.22.0` |
| `lab-route` | wacwini/baton | `.github/workflows/ci.yml` | `v1.22.0` |
| `lab-tofu-apply` | wacwini/wac.lab | `.github/workflows/tofu-apply.yml` | `v1.22.0` |
| `lab-tofu-apply` | wacwini/wac.lab.iac | `.github/workflows/tofu-apply.yml` | `v1.22.0` |
| `lab-tofu-plan` | wacwini/wac.lab | `.github/workflows/tofu-drift.yml` | `v1.22.0` |
| `lab-tofu-plan` | wacwini/wac.lab | `.github/workflows/tofu-plan.yml` | `v1.22.0` |
| `lab-tofu-plan` | wacwini/wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.22.0` |
| `lab-tofu-validate` | wacwini/wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.22.0` |
| `lab-tools` | wacwini/wac.docs | `.github/workflows/ci.yml` | `v1.22.0` |
| `nextjs-site-check` | wacwini/fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.22.0` |
| `nextjs-site-check` | wacwini/will-fell | `.github/workflows/pr.yml` | `v1.22.0` |
| `nextjs-site-deploy` | wacwini/fellhoelter-consulting | `.github/workflows/deploy.yml` | `v1.22.0` |
| `nextjs-site-deploy` | wacwini/jack-creek-patch | `.github/workflows/deploy.yml` | `v1.22.0` |
| `nextjs-site-deploy` | wacwini/will-fell | `.github/workflows/deploy.yml` | `v1.22.0` |
| `techdocs` | wacwini/baton | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac.app.claw | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac.app.finance | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac.app.flights | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac.app.handbook | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac.app.travel | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac.app.wealth | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac.docs | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac.lab | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac.lab.iac | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac.lab.remote | `.github/workflows/ci.yml` | `v1.22.0` |
| `techdocs` | wacwini/wac.plugins | `.github/workflows/ci.yml` | `v1.22.0` |

## Consumers that are not maintained here

| Component | Repo | File | Pin | Why it is listed separately |
|---|---|---|---|---|
| `lab-build` | egnyte-mcp | `.github/workflows/ci.yml` | `v1.7.0` | retired project; do not bump |
| `lab-deploy` | egnyte-mcp | `.github/workflows/promote.yml` | `v1.7.0` | retired project; do not bump |
| `lab-tofu-validate` | egnyte-mcp | `.github/workflows/ci.yml` | `v1.7.0` | retired project; do not bump |

These still pin real tags, so they are recorded rather than deleted. They are
simply out of scope for a bump walk.

`egnyte-mcp` is now archived, and its files name this library by its
pre-rename path, `willfell/lab.actions`. Neither the search below nor the
`willfell/wac.lab.actions/` pattern finds them, so read them directly.

## Closed gap, 2026-09-07

`lab-gitops-deploy` and `lab-kubeconform` sat at `v1.9.4` in all four GitOps
consumers while `actionlint` in the same files was at `v1.10.5` -- those repos
had been bumped for the actionlint fixes and for nothing else. The consequence
was that the `argo-await-sync` fixes were deployed nowhere.

Closed by finance#100, flight-checker#95 and wac#70; travel was already ahead.
All four now pin `v1.10.8`, and all three components in each file carry the same
tag, which is free: `git diff --name-only v1.10.5 v1.10.8` is `scripts/` and
docs, so `actionlint` and `lab-kubeconform` are byte-identical across that move.

The gap hid for a reason worth keeping in view. `v1.9.1` through `v1.10.8` touch
only `scripts/`, which reads as "no component changed" from a file list --
`scripts/` is owned by `lab-gitops-deploy` and nothing else says so except the
components column in `CHANGELOG.md`. A bump walk driven by file lists rather
than by that column will miss this class of release every time.

## How to regenerate this table

Do not hand-edit rows. Read the pins from the repos:

```sh
for owner in willfell wacwini; do
  gh search code 'willfell/wac.lab.actions' --owner "$owner" --limit 100 \
    --json repository,path
done
```

then, for each hit, read the file and extract every
`willfell/wac.lab.actions/<component>@<tag>`. Reusable workflows appear as
`willfell/wac.lab.actions/.github/workflows/<name>.yml@<tag>`; their component name
is `<name>`. Also record any `actions/checkout` of `willfell/wac.lab.actions`
with a `ref:`. The search also matches prose in docs and plans — only
`.github/` workflow and action files carry real pins. Code search can lag a
push or skip a repository, so confirm the result against each repository's
default branch, listed with `gh repo list <owner> --no-archived`.
