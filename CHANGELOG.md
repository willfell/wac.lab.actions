# Changelog

Every tag, and **which components it changed**. That is the only question this
file exists to answer: all components share one repo-wide tag, so most releases
move a consumer's pin to byte-identical code, and without this table there is no
way to tell a real bump from a no-op one except by diffing tags by hand.

Reading the table:

- **Components** names the actions and reusable workflows whose behaviour
  changed. A consumer pinning only components *not* listed can bump safely
  without re-testing.
- `scripts/` belongs to **`lab-gitops-deploy`** — it is the only component that
  runs anything from there (`lab-gitops-deploy/action.yml` shells out to
  `scripts/argo-await-sync.sh`). A tag that touches only `scripts/` still
  changes `lab-gitops-deploy`, which is easy to miss from a file list alone and
  is why the column says components rather than paths.
- README-only and CI-only changes are not releases and are not listed.

| Tag | Date | Components changed | What |
|---|---|---|---|
| `v1.12.0` | 2026-09-13 | `techdocs` | new reusable workflow running the pinned strict MkDocs build and the fleet nav-category check |
| `v1.11.0` | 2026-09-12 | `lab-gitops-deploy` | new optional `extra_images` input pins additional images inside the commit-back retry loop |
| `v1.10.8` | 2026-09-07 | `lab-gitops-deploy` | `argo-await-sync` proves a frozen hook by the Job itself |
| `v1.10.7` | 2026-09-07 | `lab-gitops-deploy` | `argo-await-sync` trusts the hook, not the operation's initiator |
| `v1.10.6` | 2026-09-05 | `lab-gitops-deploy` | `argo-await-sync` rejects torn reads and Running hooks it cannot prove |
| `v1.10.5` | 2026-09-04 | `actionlint` | fails the caller when a job asks for a GitHub-hosted runner |
| `v1.10.4` | 2026-09-04 | `lab-tofu-apply`, `lab-tofu-plan` | install `gh` where the override label needs it |
| `v1.10.3` | 2026-09-04 | `lab-tofu-apply`, `lab-tofu-plan`, `lab-tofu-validate` | drop the node wrapper, so `tofu` runs on a runner without node |
| `v1.10.2` | 2026-09-04 | `actionlint` | provide pipx and shellcheck, not just node |
| `v1.10.1` | 2026-09-04 | `actionlint` | provision node, so the workflow runs on a runner without one |
| `v1.10.0` | 2026-09-04 | `actionlint`, `nextjs-site-check`, `nextjs-site-deploy` | let callers choose the runner for every reusable workflow |
| `v1.9.4` | 2026-09-03 | `lab-gitops-deploy` | test the sync hook by presence, not by phase |
| `v1.9.3` | 2026-09-03 | `lab-gitops-deploy` | judge the operation by its hook, not its initiator |
| `v1.9.2` | 2026-09-03 | `lab-gitops-deploy` | replace the operation instead of merging into it |
| `v1.9.1` | 2026-09-03 | `lab-gitops-deploy` | never patch an occupied operation slot |
| `v1.9.0` | 2026-09-03 | `lab-gitops-deploy` | read the operation atomically and require its hook |
| `v1.8.0` | 2026-09-03 | `lab-gitops-deploy` | — |
| `v1.7.1` | | `nextjs-site-check`, `nextjs-site-deploy` | — |
| `v1.7.0` | | `lab-tofu-apply`, `lab-tofu-plan`, `lab-tofu-validate`, `nextjs-site-deploy` | — |
| `v1.6.0` | | `nextjs-site-check`, `nextjs-site-deploy` | — |

Entries at and below `v1.8.0` were reconstructed from tag diffs on 2026-09-06;
the components are derived from the diffs and are reliable, the prose is not
recorded and is left blank rather than guessed.

## Adding an entry

Add the row **in the same PR as the change**, not at tag time. The tag is
created by hand afterwards (`git tag vX.Y.Z && git push origin vX.Y.Z`), and a
changelog written at tag time is a changelog written from memory.

Then walk `CONSUMERS.md` for every repo pinning a changed component. A consumer
whose components are untouched does not need a PR.
