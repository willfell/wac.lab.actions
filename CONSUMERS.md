# Consumers

Every repo pinning this library, by component and file. Bumping a tag means
walking this table and opening one PR per repo whose pinned components changed;
`CHANGELOG.md` says which those are.

**Reconciled against the default branches of every non-archived wacwini
repository and the non-archived willfell repositories on 2026-11-17 UTC.** Every
file listed here was read from its repository's default branch; code-search
results and open PRs were not used as evidence. The table records merged
default-branch pins; an open bump PR does not change a row.

`wac.app.travel`, `wac.app.finance`, `wac`, `wac.app.wealth`, `wac.hemi`,
`mcp.argo` and `baton` deploy through the `wac-ci` and `wac-cd` reusable
workflows. On 2026-11-19 UTC the six other than `baton` were re-read at `v1.34.0`
after their pin bumps merged: the first five pass `build_runner` and no
`runner`, and `mcp.argo` keeps `runner`, because its `CI_RUNNER_SMALL` is a
hosted label. `baton`, shut down, stays at `v1.27.0`. Their own `lab-gitops-deploy` and `lab-build` usages are gone, and
baton's `deploy.yml` was deleted at its cutover, so none of them has such rows;
`baton` keeps `lab-kubeconform`, `actionlint`, `agents-md` and `techdocs` in
`ci.yml` at `v1.27.0`. The other wacwini repositories were bumped to `v1.28.0`
on 2026-11-17, and `wac.lab` then to `v1.29.0` (wacwini/wac.lab#549).
`wac.fantasy` took its bump (wacwini/wac.fantasy#2) after wacwini/wac.fantasy#3
fixed its nav for the `techdocs / gate`. jack-creek-patch's default branch is `dev`, which is
what this table records. The three `nextjs-site-deploy` callers (fellhoelter-consulting, will-fell and jack-creek-patch, read on 2026-11-19 UTC from `main`, `main` and `dev`) pass nothing the bump to `v1.36.0` changes: its inputs, secrets and the caller job's `id-token: write` and `contents: read` stay as they are. `sauce` lives at `wacwini/sauce` and still pins
`v1.10.5`; the personal `willfell` repositories (`ero-copilot-iac`,
`homebrew-sauce`, `mac-config`) pin older tags and are listed as they are.

Three workflows also check out this repository at a tag to run its scripts
directly, through `actions/checkout` with `repository: willfell/wac.lab.actions`
and a `ref:`. Those pins move with the shared component pins in the same file:
wacwini/baton `.github/workflows/runner-image.yml` at `v1.27.0`, and
wacwini/wac.lab `.github/workflows/warehouse-image.yml` and
`.github/workflows/ci-deploy-denial.yml`, both at `v1.29.0`.

| Component | Repo | File | Pin |
|---|---|---|---|
| `actionlint` | wacwini/baton | `.github/workflows/ci.yml` | `v1.27.0` |
| `actionlint` | wacwini/ero.egnyte.agents | `.github/workflows/ci.yml` | `v1.28.0` |
| `actionlint` | wacwini/fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.28.0` |
| `actionlint` | wacwini/jack-creek-patch | `.github/workflows/actionlint.yml` | `v1.28.0` |
| `actionlint` | wacwini/sauce | `.github/workflows/ci.yml` | `v1.10.5` |
| `actionlint` | wacwini/wac.ai.ag | `.github/workflows/ci.yml` | `v1.28.0` |
| `actionlint` | wacwini/wac.app.claw | `.github/workflows/ci.yml` | `v1.28.0` |
| `actionlint` | wacwini/wac.app.flights | `.github/workflows/ci.yml` | `v1.28.0` |
| `actionlint` | wacwini/wac.app.handbook | `.github/workflows/ci.yml` | `v1.28.0` |
| `actionlint` | wacwini/wac.docs | `.github/workflows/ci.yml` | `v1.28.0` |
| `actionlint` | wacwini/wac.fantasy | `.github/workflows/ci.yml` | `v1.28.0` |
| `actionlint` | wacwini/wac.lab | `.github/workflows/ci.yml` | `v1.29.0` |
| `actionlint` | wacwini/wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.28.0` |
| `actionlint` | wacwini/wac.lab.remote | `.github/workflows/ci.yml` | `v1.28.0` |
| `actionlint` | wacwini/wac.plugins | `.github/workflows/ci.yml` | `v1.28.0` |
| `actionlint` | wacwini/will-fell | `.github/workflows/pr.yml` | `v1.28.0` |
| `actionlint` | willfell/ero-copilot-iac | `.github/workflows/lint.yml` | `v1.7.1` |
| `actionlint` | willfell/homebrew-sauce | `.github/workflows/ci.yml` | `v1.19.0` |
| `actionlint` | willfell/mac-config | `.github/workflows/ci.yml` | `v1.12.0` |
| `agents-md` | wacwini/baton | `.github/workflows/ci.yml` | `v1.27.0` |
| `agents-md` | wacwini/ero.egnyte.agents | `.github/workflows/ci.yml` | `v1.28.0` |
| `agents-md` | wacwini/wac.ai.ag | `.github/workflows/ci.yml` | `v1.28.0` |
| `agents-md` | wacwini/wac.app.claw | `.github/workflows/ci.yml` | `v1.28.0` |
| `agents-md` | wacwini/wac.app.flights | `.github/workflows/ci.yml` | `v1.28.0` |
| `agents-md` | wacwini/wac.app.handbook | `.github/workflows/ci.yml` | `v1.28.0` |
| `agents-md` | wacwini/wac.docs | `.github/workflows/ci.yml` | `v1.28.0` |
| `agents-md` | wacwini/wac.fantasy | `.github/workflows/ci.yml` | `v1.28.0` |
| `agents-md` | wacwini/wac.lab | `.github/workflows/ci.yml` | `v1.29.0` |
| `agents-md` | wacwini/wac.lab.iac | `.github/workflows/ci.yml` | `v1.28.0` |
| `agents-md` | wacwini/wac.lab.remote | `.github/workflows/ci.yml` | `v1.28.0` |
| `agents-md` | wacwini/wac.plugins | `.github/workflows/ci.yml` | `v1.28.0` |
| `lab-kubeconform` | wacwini/baton | `.github/workflows/ci.yml` | `v1.27.0` |
| `lab-kubeconform` | wacwini/wac.app.flights | `.github/workflows/ci.yml` | `v1.28.0` |
| `lab-kubeconform` | wacwini/wac.app.handbook | `.github/workflows/ci.yml` | `v1.28.0` |
| `lab-release` | wacwini/wac.lab | `.github/workflows/warehouse-image.yml` | `v1.29.0` |
| `lab-tofu-apply` | wacwini/wac.lab | `.github/workflows/tofu-apply.yml` | `v1.29.0` |
| `lab-tofu-apply` | wacwini/wac.lab.iac | `.github/workflows/tofu-apply.yml` | `v1.28.0` |
| `lab-tofu-plan` | wacwini/wac.lab | `.github/workflows/tofu-drift.yml` | `v1.29.0` |
| `lab-tofu-plan` | wacwini/wac.lab | `.github/workflows/tofu-plan.yml` | `v1.29.0` |
| `lab-tofu-plan` | wacwini/wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.28.0` |
| `lab-tofu-validate` | wacwini/wac.lab.iac | `.github/workflows/tofu-plan.yml` | `v1.28.0` |
| `lab-tools` | wacwini/wac.ai.ag | `.github/workflows/ci.yml` | `v1.28.0` |
| `lab-tools` | wacwini/wac.docs | `.github/workflows/ci.yml` | `v1.28.0` |
| `nextjs-site-check` | wacwini/fellhoelter-consulting | `.github/workflows/pr.yml` | `v1.28.0` |
| `nextjs-site-check` | wacwini/will-fell | `.github/workflows/pr.yml` | `v1.28.0` |
| `nextjs-site-deploy` | wacwini/fellhoelter-consulting | `.github/workflows/deploy.yml` | `v1.28.0` |
| `nextjs-site-deploy` | wacwini/jack-creek-patch | `.github/workflows/deploy.yml` | `v1.28.0` |
| `nextjs-site-deploy` | wacwini/will-fell | `.github/workflows/deploy.yml` | `v1.28.0` |
| `techdocs` | wacwini/baton | `.github/workflows/ci.yml` | `v1.27.0` |
| `techdocs` | wacwini/ero.egnyte.agents | `.github/workflows/ci.yml` | `v1.28.0` |
| `techdocs` | wacwini/wac.ai.ag | `.github/workflows/ci.yml` | `v1.28.0` |
| `techdocs` | wacwini/wac.app.claw | `.github/workflows/ci.yml` | `v1.28.0` |
| `techdocs` | wacwini/wac.app.flights | `.github/workflows/ci.yml` | `v1.28.0` |
| `techdocs` | wacwini/wac.app.handbook | `.github/workflows/ci.yml` | `v1.28.0` |
| `techdocs` | wacwini/wac.docs | `.github/workflows/ci.yml` | `v1.28.0` |
| `techdocs` | wacwini/wac.fantasy | `.github/workflows/ci.yml` | `v1.28.0` |
| `techdocs` | wacwini/wac.lab | `.github/workflows/ci.yml` | `v1.29.0` |
| `techdocs` | wacwini/wac.lab.iac | `.github/workflows/ci.yml` | `v1.28.0` |
| `techdocs` | wacwini/wac.lab.remote | `.github/workflows/ci.yml` | `v1.28.0` |
| `techdocs` | wacwini/wac.plugins | `.github/workflows/ci.yml` | `v1.28.0` |
| `wac-cd` | wacwini/baton | `.github/workflows/cd.yml` | `v1.27.0` |
| `wac-cd` | wacwini/mcp.argo | `.github/workflows/cd.yml` | `v1.34.0` |
| `wac-cd` | wacwini/wac | `.github/workflows/cd.yml` | `v1.34.0` |
| `wac-cd` | wacwini/wac.app.finance | `.github/workflows/cd.yml` | `v1.34.0` |
| `wac-cd` | wacwini/wac.app.travel | `.github/workflows/cd.yml` | `v1.34.0` |
| `wac-cd` | wacwini/wac.app.wealth | `.github/workflows/cd.yml` | `v1.34.0` |
| `wac-cd` | wacwini/wac.hemi | `.github/workflows/cd.yml` | `v1.34.0` |
| `wac-ci` | wacwini/baton | `.github/workflows/ci.yml` | `v1.27.0` |
| `wac-ci` | wacwini/mcp.argo | `.github/workflows/ci.yml` | `v1.34.0` |
| `wac-ci` | wacwini/wac | `.github/workflows/ci.yml` | `v1.34.0` |
| `wac-ci` | wacwini/wac.app.finance | `.github/workflows/ci.yml` | `v1.34.0` |
| `wac-ci` | wacwini/wac.app.travel | `.github/workflows/ci.yml` | `v1.34.0` |
| `wac-ci` | wacwini/wac.app.wealth | `.github/workflows/ci.yml` | `v1.34.0` |
| `wac-ci` | wacwini/wac.hemi | `.github/workflows/ci.yml` | `v1.34.0` |

## Documentation paths before the `v1.36.0` bump

From `v1.36.0` a pull request that changes only documentation skips
`wac-ci`'s `checks`, and such a push releases nothing (`docs/reference/wacd-profile.md`,
**Documentation paths**). The default patterns are `docs/**`, `mkdocs.yml`
and `*.md`, so a repository whose gates read one of those paths must narrow
`ci.docs_paths` (hemi's `delivery.docsPaths` for a cut-over app), or set
`[]`, before its pin moves to `v1.36.0`; otherwise a documentation-only pull
request skips the checks that read it. A cut-over app sets it in hemi's copy,
which needs hemi to accept the key first.

Surveyed on 2026-11-19 UTC from each repository's default branch, read-only
through `gh api` (the tree, each tarball, and for the cut-over apps their
`contracts/<repository id>/active.json` in `wacwini/wac.fleet`): the gate
commands, every test and script they run, the lint and format configs, and
the Dockerfiles. Every live copy, and wac.hemi.test's profile, passes the new
rules with the default and with the value below.

| Repository | Settings | What reads a default path | Required `docs_paths` |
|---|---|---|---|
| wacwini/wac | hemi | `npm run check:emoji` (`scripts/check-no-emoji.mjs`) scans every `.md` from the root, `AGENTS.md`, `DISTRIBUTION.md` and `docs/` included; `scripts/sync-kit-docs.test.mjs` (in `npm test`) reads `docs/kit/*.md` against `packages/ui/README.md`, `packages/ui/CHANGELOG.md` and `DISTRIBUTION.md` | `[]` (nothing reads `mkdocs.yml`, so `["mkdocs.yml"]` is the most it could skip on) |
| wacwini/wac.hemi | hemi | nothing: its tests read `src/`, `.github/workflows/`, `deploy/`, `scripts/`, the package files, `.gitignore`, `.dockerignore` and `next.config.ts`; docs paths appear in `release-paths.test.ts`, `view.test.ts`, `cutover-preflight.test.ts` and `deploy-contract.test.ts` only as string fixtures; `check:emoji` scans `src`, `scripts` and `deploy` | the default (leave it absent) |
| wacwini/mcp.argo | hemi | nothing: `make lint` is `gofmt -l .` and `go vet`, `make test` reads `__toolsnaps__/` and `testdata/` | the default |
| wacwini/wac.app.finance | hemi | `node scripts/check-no-emoji.mjs` scans `docs/llc.md`; `app/tests/api/llc-snapshot-contract.test.ts` and the ledger's `doc`, `snapshot` and `paid` tests read `docs/llc.md`; `app/tests/ui/payday.test.tsx` and `tokens.test.ts` read `docs/ui.md`; `app/tests/db/schema.test.ts` reads `docs/data-and-api.md`; `app/tests/ui/payday-summary.test.tsx` reads `docs/superpowers/specs/2026-08-10-cash-on-hand-design.md` | `["mkdocs.yml", "*.md", "docs/guides/**", "docs/handoffs/**", "docs/llc-research-2026-09/**", "docs/proposals/**", "docs/superpowers/plans/**"]` |
| wacwini/wac.app.travel | hemi | nothing: `check:emoji` scans `src`, `scripts` and `deploy`, and no test reads `docs/` or a root `.md` | the default |
| wacwini/wac.app.wealth | hemi | `src/lib/__tests__/docs-invariants.test.ts` reads `mkdocs.yml` and every page under `docs/`; `body-docs.test.ts` reads `docs/` and `AGENTS.md`; `close-docs`, `data-docs`, `gratitude-coffee`, `today-redirects` and `week-redirect` tests read `docs/tasks.md`, `docs/design.md` and others | `[]` |
| wacwini/wac.hemi.test | files | nothing: `compileall src tests` and `unittest discover -s tests` | the default |

No image copies `docs/` or a root `.md` into what it serves: the Next.js
images ship only their standalone output, and the others copy named sources.

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
