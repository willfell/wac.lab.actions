# Consumers

Every repo pinning this library, by component and file. Bumping a tag means
walking this table and opening one PR per repo whose pinned components changed —
`CHANGELOG.md` says which those are.

**Reconciled against the live default branch of every repo on 2026-09-11**, after
the rename of eight repos to the `wac.*` scheme. Seven entries in the Repo column
named repos that no longer answer under that name: `lab`, `finance`,
`flight-checker`, `headspace`, `travel`, `terraform-global` and
`remote-process-orchestration`. Every pin was re-read rather than carried over,
and none had changed.

The regeneration also found `wac.docs` missing from the table entirely, pinning
`actionlint` and `lab-tools` at `v1.10.5`. That is the same failure the
2026-09-07 pass was written up for — a stale table is worse than no table,
because the walk it drives silently skips whatever it forgot.

| Component | Repo | File | Pin |
|---|---|---|---|
| `actionlint` | ero-copilot-iac | `.github/workflows/lint.yml` | `v1.7.1` |
| `actionlint` | fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.6.0` |
| `nextjs-site-check` | fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.6.0` |
| `nextjs-site-deploy` | fellhoelter-consulting | `.github/workflows/deploy.yml` | `v1.6.0` |
| `actionlint` | homebrew-sauce | `.github/workflows/ci.yml` | `v1.7.1` |
| `nextjs-site-deploy` | jack-creek-patch | `.github/workflows/deploy.yml` | `v1.7.0` |
| `actionlint` | mac-config | `.github/workflows/ci.yml` | `v1.7.1` |
| `actionlint` | sauce | `.github/workflows/ci.yml` | `v1.10.5` |
| `actionlint` | wac | `.github/workflows/ci.yml` | `v1.10.8` |
| `lab-gitops-deploy` | wac | `.github/workflows/ci.yml` | `v1.10.8` |
| `lab-kubeconform` | wac | `.github/workflows/ci.yml` | `v1.10.8` |
| `actionlint` | wac.app.claw | `.github/workflows/ci.yml` | `v1.10.5` |
| `actionlint` | wac.app.finance | `.github/workflows/ci.yml` | `v1.10.8` |
| `lab-gitops-deploy` | wac.app.finance | `.github/workflows/ci.yml` | `v1.10.8` |
| `lab-kubeconform` | wac.app.finance | `.github/workflows/ci.yml` | `v1.10.8` |
| `actionlint` | wac.app.flights | `.github/workflows/ci.yml` | `v1.13.0` |
| `lab-gitops-deploy` | wac.app.flights | `.github/workflows/ci.yml` | `v1.13.0` |
| `lab-kubeconform` | wac.app.flights | `.github/workflows/ci.yml` | `v1.13.0` |
| `techdocs` | wac.app.flights | `.github/workflows/ci.yml` | `v1.13.0` |
| `actionlint` | wac.app.travel | `.github/workflows/ci.yml` | `v1.10.9` |
| `lab-gitops-deploy` | wac.app.travel | `.github/workflows/ci.yml` | `v1.10.9` |
| `lab-kubeconform` | wac.app.travel | `.github/workflows/ci.yml` | `v1.10.9` |
| `actionlint` | wac.docs | `.github/workflows/ci.yml` | `v1.10.5` |
| `lab-tools` | wac.docs | `.github/workflows/ci.yml` | `v1.10.5` |
| `actionlint` | wac.lab | `.github/workflows/ci.yml` | `v1.10.5` |
| `lab-tofu-apply` | wac.lab | `.github/workflows/tofu-apply.yml` | `v1.10.5` |
| `lab-tofu-plan` | wac.lab | `.github/workflows/tofu-drift.yml` | `v1.10.5` |
| `lab-tofu-plan` | wac.lab | `.github/workflows/tofu-plan.yml` | `v1.10.5` |
| `actionlint` | wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.10.5` |
| `lab-tofu-apply` | wac.lab.iac | `.github/workflows/tofu-apply.yml` | `v1.10.5` |
| `lab-tofu-plan` | wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.10.5` |
| `lab-tofu-validate` | wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.10.5` |
| `actionlint` | wac.lab.remote | `.github/workflows/ci.yml` | `v1.10.5` |
| `actionlint` | wac.plugins | `.github/workflows/ci.yml` | `v1.10.5` |
| `actionlint` | will-fell | `.github/workflows/pr.yml` | `v1.6.0` |
| `nextjs-site-check` | will-fell | `.github/workflows/pr.yml` | `v1.6.0` |
| `nextjs-site-deploy` | will-fell | `.github/workflows/deploy.yml` | `v1.6.0` |

## Consumers that are not maintained here

| Component | Repo | File | Pin | Why it is listed separately |
|---|---|---|---|---|
| `actionlint` | wac.vaults | `.github/workflows/ci.yml` | `v1.5.0` | repo is being archived; do not bump |
| `lab-tools` | wac.vaults | `.github/workflows/ci.yml` | `v1.5.0` | same |
| `lab-build` | egnyte-mcp | `.github/workflows/ci.yml` | `v1.7.0` | retired project; do not bump |
| `lab-tofu-validate` | egnyte-mcp | `.github/workflows/ci.yml` | `v1.7.0` | same |
| `lab-deploy` | egnyte-mcp | `.github/workflows/promote.yml` | `v1.7.0` | same |

These still pin real tags and would still run if triggered, so they are recorded
rather than deleted. They are simply out of scope for a bump walk.

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
