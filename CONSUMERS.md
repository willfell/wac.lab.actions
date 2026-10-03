# Consumers

Every repo pinning this library, by component and file. Bumping a tag means
walking this table and opening one PR per repo whose pinned components changed —
`CHANGELOG.md` says which those are.

**Reconciled against the live default branch of every repo on 2026-10-03.**
Every pin was re-read rather than carried over, by listing `.github/workflows/`
on each repo's default branch and reading every file in it, because code search
can lag a fresh merge. Against the 2026-09-11 table, 38 of the 64 rows below are
new or changed:

- `agents-md`, from the AGENTS.md rollout: ten repos pin it at `v1.13.0`.
  `wac` and `wac.app.travel` do not pin it on their default branches.
- `techdocs` had one row, for `wac.app.flights`, while twelve repos pin it. The
  other eleven pin `v1.12.0`.
- `wac.app.handbook` and `wac.app.wealth` were missing from the table entirely.
- `wac.lab` pins `lab-tools` in `warehouse-image.yml`, which had no row.
- Eleven pins had moved to tags cut after 2026-09-11 without the table
  following: `fellhoelter-consulting`, `jack-creek-patch` and `will-fell` are at
  `v1.12.2`; `homebrew-sauce`, `mac-config` and both `wac.docs` pins are at
  `v1.12.0`.
- `wac.vaults` no longer answers, so its two rows are gone from the table of
  consumers not maintained here.

That is the failure the 2026-09-07 and 2026-09-11 passes were written up for: a
stale table is worse than no table, because the walk it drives silently skips
whatever it forgot.

| Component | Repo | File | Pin |
|---|---|---|---|
| `actionlint` | ero-copilot-iac | `.github/workflows/lint.yml` | `v1.7.1` |
| `actionlint` | fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.12.2` |
| `nextjs-site-check` | fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.12.2` |
| `nextjs-site-deploy` | fellhoelter-consulting | `.github/workflows/deploy.yml` | `v1.12.2` |
| `actionlint` | homebrew-sauce | `.github/workflows/ci.yml` | `v1.12.0` |
| `nextjs-site-deploy` | jack-creek-patch | `.github/workflows/deploy.yml` | `v1.12.2` |
| `actionlint` | mac-config | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | sauce | `.github/workflows/ci.yml` | `v1.10.5` |
| `actionlint` | wac | `.github/workflows/ci.yml` | `v1.10.8` |
| `lab-gitops-deploy` | wac | `.github/workflows/ci.yml` | `v1.10.8` |
| `lab-kubeconform` | wac | `.github/workflows/ci.yml` | `v1.10.8` |
| `techdocs` | wac | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | wac.app.claw | `.github/workflows/ci.yml` | `v1.10.5` |
| `agents-md` | wac.app.claw | `.github/workflows/ci.yml` | `v1.13.0` |
| `techdocs` | wac.app.claw | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | wac.app.finance | `.github/workflows/ci.yml` | `v1.10.8` |
| `agents-md` | wac.app.finance | `.github/workflows/ci.yml` | `v1.13.0` |
| `lab-gitops-deploy` | wac.app.finance | `.github/workflows/ci.yml` | `v1.10.8` |
| `lab-kubeconform` | wac.app.finance | `.github/workflows/ci.yml` | `v1.10.8` |
| `techdocs` | wac.app.finance | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | wac.app.flights | `.github/workflows/ci.yml` | `v1.13.0` |
| `agents-md` | wac.app.flights | `.github/workflows/ci.yml` | `v1.13.0` |
| `lab-gitops-deploy` | wac.app.flights | `.github/workflows/ci.yml` | `v1.13.0` |
| `lab-kubeconform` | wac.app.flights | `.github/workflows/ci.yml` | `v1.13.0` |
| `techdocs` | wac.app.flights | `.github/workflows/ci.yml` | `v1.13.0` |
| `actionlint` | wac.app.handbook | `.github/workflows/ci.yml` | `v1.10.9` |
| `agents-md` | wac.app.handbook | `.github/workflows/ci.yml` | `v1.13.0` |
| `lab-gitops-deploy` | wac.app.handbook | `.github/workflows/ci.yml` | `v1.10.9` |
| `lab-kubeconform` | wac.app.handbook | `.github/workflows/ci.yml` | `v1.10.9` |
| `techdocs` | wac.app.handbook | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | wac.app.travel | `.github/workflows/ci.yml` | `v1.10.9` |
| `lab-gitops-deploy` | wac.app.travel | `.github/workflows/ci.yml` | `v1.10.9` |
| `lab-kubeconform` | wac.app.travel | `.github/workflows/ci.yml` | `v1.10.9` |
| `techdocs` | wac.app.travel | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | wac.app.wealth | `.github/workflows/ci.yml` | `v1.11.0` |
| `agents-md` | wac.app.wealth | `.github/workflows/ci.yml` | `v1.13.0` |
| `lab-gitops-deploy` | wac.app.wealth | `.github/workflows/ci.yml` | `v1.11.0` |
| `techdocs` | wac.app.wealth | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | wac.docs | `.github/workflows/ci.yml` | `v1.12.0` |
| `agents-md` | wac.docs | `.github/workflows/ci.yml` | `v1.13.0` |
| `lab-tools` | wac.docs | `.github/workflows/ci.yml` | `v1.12.0` |
| `techdocs` | wac.docs | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | wac.lab | `.github/workflows/ci.yml` | `v1.10.5` |
| `agents-md` | wac.lab | `.github/workflows/ci.yml` | `v1.13.0` |
| `lab-tofu-apply` | wac.lab | `.github/workflows/tofu-apply.yml` | `v1.10.5` |
| `lab-tofu-plan` | wac.lab | `.github/workflows/tofu-drift.yml` | `v1.10.5` |
| `lab-tofu-plan` | wac.lab | `.github/workflows/tofu-plan.yml` | `v1.10.5` |
| `lab-tools` | wac.lab | `.github/workflows/warehouse-image.yml` | `v1.10.5` |
| `techdocs` | wac.lab | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.10.5` |
| `agents-md` | wac.lab.iac | `.github/workflows/ci.yml` | `v1.13.0` |
| `lab-tofu-apply` | wac.lab.iac | `.github/workflows/tofu-apply.yml` | `v1.10.5` |
| `lab-tofu-plan` | wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.10.5` |
| `lab-tofu-validate` | wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.10.5` |
| `techdocs` | wac.lab.iac | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | wac.lab.remote | `.github/workflows/ci.yml` | `v1.10.5` |
| `agents-md` | wac.lab.remote | `.github/workflows/ci.yml` | `v1.13.0` |
| `techdocs` | wac.lab.remote | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | wac.plugins | `.github/workflows/ci.yml` | `v1.10.5` |
| `agents-md` | wac.plugins | `.github/workflows/ci.yml` | `v1.13.0` |
| `techdocs` | wac.plugins | `.github/workflows/ci.yml` | `v1.12.0` |
| `actionlint` | will-fell | `.github/workflows/pr.yml` | `v1.12.2` |
| `nextjs-site-check` | will-fell | `.github/workflows/pr.yml` | `v1.12.2` |
| `nextjs-site-deploy` | will-fell | `.github/workflows/deploy.yml` | `v1.12.2` |

## Consumers that are not maintained here

| Component | Repo | File | Pin | Why it is listed separately |
|---|---|---|---|---|
| `lab-build` | egnyte-mcp | `.github/workflows/ci.yml` | `v1.7.0` | retired project; do not bump |
| `lab-tofu-validate` | egnyte-mcp | `.github/workflows/ci.yml` | `v1.7.0` | same |
| `lab-deploy` | egnyte-mcp | `.github/workflows/promote.yml` | `v1.7.0` | same |

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
