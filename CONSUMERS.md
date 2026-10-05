# Consumers

Every repo pinning this library, by component and file. Bumping a tag means
walking this table and opening one PR per repo whose pinned components changed;
`CHANGELOG.md` says which those are.

**Reconciled against the live default branch of every listed repo on 2026-10-04 UTC.**
Every `.github/workflows/` file was read directly from its default branch;
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
| `agents-md` | baton | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | baton | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-kubeconform` | baton | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | ero-copilot-iac | `.github/workflows/lint.yml` | `v1.7.1` |
| `actionlint` | fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.12.2` |
| `nextjs-site-check` | fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.12.2` |
| `nextjs-site-deploy` | fellhoelter-consulting | `.github/workflows/deploy.yml` | `v1.12.2` |
| `actionlint` | homebrew-sauce | `.github/workflows/ci.yml` | `v1.12.0` |
| `nextjs-site-deploy` | jack-creek-patch | `.github/workflows/deploy.yml` | `v1.12.2` |
| `actionlint` | mac-config | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | sauce | `.github/workflows/ci.yml` | `v1.10.5` |
| `actionlint` | wac | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wac | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-gitops-deploy` | wac | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-kubeconform` | wac | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | wac | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | wac.app.claw | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wac.app.claw | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | wac.app.claw | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | wac.app.finance | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wac.app.finance | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-gitops-deploy` | wac.app.finance | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-kubeconform` | wac.app.finance | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | wac.app.finance | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | wac.app.flights | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wac.app.flights | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-gitops-deploy` | wac.app.flights | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-kubeconform` | wac.app.flights | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | wac.app.flights | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | wac.app.handbook | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wac.app.handbook | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-gitops-deploy` | wac.app.handbook | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-kubeconform` | wac.app.handbook | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | wac.app.handbook | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | wac.app.travel | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wac.app.travel | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-gitops-deploy` | wac.app.travel | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-kubeconform` | wac.app.travel | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | wac.app.travel | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | wac.app.wealth | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wac.app.wealth | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-gitops-deploy` | wac.app.wealth | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | wac.app.wealth | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | wac.docs | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wac.docs | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-tools` | wac.docs | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | wac.docs | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | wac.lab | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wac.lab | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-tofu-apply` | wac.lab | `.github/workflows/tofu-apply.yml` | `v1.15.1` |
| `lab-tofu-plan` | wac.lab | `.github/workflows/tofu-drift.yml` | `v1.15.1` |
| `lab-tofu-plan` | wac.lab | `.github/workflows/tofu-plan.yml` | `v1.15.1` |
| `lab-tools` | wac.lab | `.github/workflows/warehouse-image.yml` | `v1.15.1` |
| `techdocs` | wac.lab | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.15.1` |
| `agents-md` | wac.lab.iac | `.github/workflows/ci.yml` | `v1.15.1` |
| `lab-tofu-apply` | wac.lab.iac | `.github/workflows/tofu-apply.yml` | `v1.15.1` |
| `lab-tofu-plan` | wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.15.1` |
| `lab-tofu-validate` | wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.15.1` |
| `techdocs` | wac.lab.iac | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | wac.lab.remote | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wac.lab.remote | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | wac.lab.remote | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | wac.plugins | `.github/workflows/ci.yml` | `v1.15.1` |
| `agents-md` | wac.plugins | `.github/workflows/ci.yml` | `v1.15.1` |
| `techdocs` | wac.plugins | `.github/workflows/ci.yml` | `v1.15.1` |
| `actionlint` | will-fell | `.github/workflows/pr.yml` | `v1.12.2` |
| `nextjs-site-check` | will-fell | `.github/workflows/pr.yml` | `v1.12.2` |
| `nextjs-site-deploy` | will-fell | `.github/workflows/deploy.yml` | `v1.12.2` |

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
