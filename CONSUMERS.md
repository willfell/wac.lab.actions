# Consumers

Every repo pinning this library, by component and file. Bumping a tag means
walking this table and opening one PR per repo whose pinned components changed;
`CHANGELOG.md` says which those are.

**Reconciled against fetched default branches of the migration repositories on 2026-10-10 UTC.**
Rows outside the migration scope retain their previous audit evidence. Both
willfell and wacwini shared-action callers were searched; scoped rows were read
from fetched default branches. Every scoped `.github/workflows/` file was read;
code-search results and planned PR pins were not used as evidence.

The local catalog MCP guidance was released in `v1.14.0`; conditional native
knowledge-search guidance followed in `v1.15.0`. The `v1.15.1` patch pins the
shared AGENTS and TechDocs checkout to the defining workflow commit and fails
closed when that commit is unavailable. The table records merged default-branch
pins; an open bump PR does not change a row. Baton rows were added from its
actual default-branch workflow on 2026-10-05 UTC.

Baton also checks out `v1.15.1` to run `scripts/install-tools.sh` directly for
its release job; that checkout pin must move with a shared installer bump.

| Component | Repo | File | Pin |
|---|---|---|---|
| `actionlint` | baton | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | ero-copilot-iac | `.github/workflows/lint.yml` | `v1.7.1` |
| `actionlint` | sauce | `.github/workflows/ci.yml` | `v1.10.5` |
| `actionlint` | wacwini/wac.lab | `.github/workflows/ci.yml` | `v1.17.0` |
| `actionlint` | willfell/fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.12.2` |
| `actionlint` | willfell/homebrew-sauce | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | willfell/mac-config | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | willfell/wac | `.github/workflows/ci.yml` | `v1.16.0` |
| `actionlint` | willfell/wac.app.claw | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | willfell/wac.app.finance | `.github/workflows/ci.yml` | `v1.16.0` |
| `actionlint` | willfell/wac.app.flights | `.github/workflows/ci.yml` | `v1.16.0` |
| `actionlint` | willfell/wac.app.handbook | `.github/workflows/ci.yml` | `v1.16.0` |
| `actionlint` | willfell/wac.app.travel | `.github/workflows/ci.yml` | `v1.16.0` |
| `actionlint` | willfell/wac.app.wealth | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | willfell/wac.docs | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | willfell/wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.15.1` |
| `actionlint` | willfell/wac.lab.remote | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | willfell/wac.plugins | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | willfell/will-fell | `.github/workflows/pr.yml` | `v1.12.2` |
| `agents-md` | baton | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wacwini/wac.lab | `.github/workflows/ci.yml` | `v1.17.0` |
| `agents-md` | willfell/wac | `.github/workflows/ci.yml` | `v1.16.0` |
| `agents-md` | willfell/wac.app.claw | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | willfell/wac.app.finance | `.github/workflows/ci.yml` | `v1.16.0` |
| `agents-md` | willfell/wac.app.flights | `.github/workflows/ci.yml` | `v1.16.0` |
| `agents-md` | willfell/wac.app.handbook | `.github/workflows/ci.yml` | `v1.16.0` |
| `agents-md` | willfell/wac.app.travel | `.github/workflows/ci.yml` | `v1.16.0` |
| `agents-md` | willfell/wac.app.wealth | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | willfell/wac.docs | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | willfell/wac.lab.iac | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | willfell/wac.lab.remote | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | willfell/wac.plugins | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-gitops-deploy` | willfell/wac | `.github/workflows/ci.yml` | `v1.16.0` |
| `lab-gitops-deploy` | willfell/wac.app.finance | `.github/workflows/ci.yml` | `v1.16.0` |
| `lab-gitops-deploy` | willfell/wac.app.flights | `.github/workflows/ci.yml` | `v1.16.0` |
| `lab-gitops-deploy` | willfell/wac.app.handbook | `.github/workflows/ci.yml` | `v1.16.0` |
| `lab-gitops-deploy` | willfell/wac.app.travel | `.github/workflows/ci.yml` | `v1.16.0` |
| `lab-gitops-deploy` | willfell/wac.app.wealth | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-kubeconform` | baton | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-kubeconform` | willfell/wac | `.github/workflows/ci.yml` | `v1.16.0` |
| `lab-kubeconform` | willfell/wac.app.finance | `.github/workflows/ci.yml` | `v1.16.0` |
| `lab-kubeconform` | willfell/wac.app.flights | `.github/workflows/ci.yml` | `v1.16.0` |
| `lab-kubeconform` | willfell/wac.app.handbook | `.github/workflows/ci.yml` | `v1.16.0` |
| `lab-kubeconform` | willfell/wac.app.travel | `.github/workflows/ci.yml` | `v1.16.0` |
| `lab-release` | wacwini/wac.lab | `.github/workflows/warehouse-image.yml` | `v1.17.0` |
| `lab-tofu-apply` | wacwini/wac.lab | `.github/workflows/tofu-apply.yml` | `v1.17.0` |
| `lab-tofu-apply` | willfell/wac.lab.iac | `.github/workflows/tofu-apply.yml` | `v1.15.1` |
| `lab-tofu-plan` | wacwini/wac.lab | `.github/workflows/tofu-drift.yml` | `v1.17.0` |
| `lab-tofu-plan` | wacwini/wac.lab | `.github/workflows/tofu-plan.yml` | `v1.17.0` |
| `lab-tofu-plan` | willfell/wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.15.1` |
| `lab-tofu-validate` | willfell/wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.15.1` |
| `lab-tools` | willfell/wac.docs | `.github/workflows/ci.yml` | `v1.15.1` |
| `nextjs-site-check` | willfell/fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.12.2` |
| `nextjs-site-check` | willfell/will-fell | `.github/workflows/pr.yml` | `v1.12.2` |
| `nextjs-site-deploy` | willfell/fellhoelter-consulting | `.github/workflows/deploy.yml` | `v1.12.2` |
| `nextjs-site-deploy` | willfell/jack-creek-patch | `.github/workflows/deploy.yml` | `v1.12.2` |
| `nextjs-site-deploy` | willfell/will-fell | `.github/workflows/deploy.yml` | `v1.12.2` |
| `techdocs` | baton | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | wacwini/wac.lab | `.github/workflows/ci.yml` | `v1.17.0` |
| `techdocs` | willfell/wac | `.github/workflows/ci.yml` | `v1.16.0` |
| `techdocs` | willfell/wac.app.claw | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | willfell/wac.app.finance | `.github/workflows/ci.yml` | `v1.16.0` |
| `techdocs` | willfell/wac.app.flights | `.github/workflows/ci.yml` | `v1.16.0` |
| `techdocs` | willfell/wac.app.handbook | `.github/workflows/ci.yml` | `v1.16.0` |
| `techdocs` | willfell/wac.app.travel | `.github/workflows/ci.yml` | `v1.16.0` |
| `techdocs` | willfell/wac.app.wealth | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | willfell/wac.docs | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | willfell/wac.lab.iac | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | willfell/wac.lab.remote | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | willfell/wac.plugins | `.github/workflows/ci.yml` | `v1.15.1` |

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
gh search code 'willfell/wac.lab.actions' --owner willfell --limit 100 \
  --json repository,path
```

then, for each hit, read the file and extract every
`willfell/wac.lab.actions/<component>@<tag>`. Reusable workflows appear as
`willfell/wac.lab.actions/.github/workflows/<name>.yml@<tag>`; their component name
is `<name>`. The search also matches prose in docs and plans — only
`.github/workflows/` files carry real pins.
