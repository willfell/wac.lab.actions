# Changelog

Every tag, and **which components it changed**. That is the only question this
file exists to answer: all components share one repo-wide tag, so most releases
move a consumer's pin to byte-identical code, and without this table there is no
way to tell a real bump from a no-op one except by diffing tags by hand.

Reading the table:

- **Components** names the actions and reusable workflows whose behaviour
  changed. A consumer pinning only components *not* listed can bump safely
  without re-testing.
- `scripts/` is shared, and each script belongs to the components that run it.
  A tag that touches only `scripts/` still changes those components, which is
  easy to miss from a file list alone and is why the column says components
  rather than paths:

  | Path | Components |
  |---|---|
  | `scripts/install-tools.sh` | `lab-build`, `lab-deploy`, `lab-gitops-deploy`, `lab-kubeconform`, `lab-tools` |
  | `scripts/argo-await-sync.sh` | `lab-gitops-deploy` |
  | `scripts/check-docs-nav.sh` | `techdocs` |
  | `scripts/agents-md.py`, `agents-md/` | `agents-md` |
  | `scripts/tests/` | none; CI only |

- A tag that changes `agents-md/fleet-rules.md` fails every `agents-md`
  consumer's gate at its bump until that repo re-renders its AGENTS.md tail.
  That is intended, and the row says so.
- README-only and CI-only changes are not releases and are not listed.

| Tag | Date | Components changed | What |
|---|---|---|---|
| `v1.15.1` | 2026-10-03 | `agents-md`, `techdocs` | fix defining-workflow source checkout to documented `job.workflow_sha` and reject an absent commit before checkout; the previous undefined context silently selected main. Fleet rules remain byte-identical to v1.15.0 |
| `v1.15.0` | 2026-10-03 | `agents-md` | native knowledge-search and cited-context guidance alongside catalog consumer checks; removes the obsolete catalog-only endpoint claim. Every consumer must re-render its AGENTS.md tail at the bump or its gate fails; unavailable tools require direct authoritative-source inspection |
| `v1.14.0` | 2026-10-03 | `agents-md` | fleet rules direct agents to the local docs MCP for owners and incoming dependencies before shared-resource, API or cross-repo changes; every consumer must re-render its AGENTS.md tail when bumping or its gate fails |
| `v1.13.0` | 2026-10-02 | `agents-md` | new reusable workflow gating each repo's AGENTS.md: no tracked CLAUDE.md, the section contract, a tail rendered from `catalog-info.yaml` and the fleet rules, and resolvable commands |
| `v1.12.2` | 2026-09-19 | `nextjs-site-deploy` | the bucket check runs from the workspace root, so it no longer dies before checkout on a fresh runner; installs the aws CLI the S3 sync and CloudFront invalidation need when the image lacks it |
| `v1.12.1` | 2026-09-19 | `nextjs-site-check`, `nextjs-site-deploy` | provision node, npm and yarn when absent, so setup-node's yarn cache probe survives a minimal runner image |
| `v1.12.0` | 2026-09-13 | `techdocs` | new reusable workflow running the pinned strict MkDocs build and the fleet nav-category check |
| `v1.11.0` | 2026-09-12 | `lab-gitops-deploy` | new optional `extra_images` input pins additional images inside the commit-back retry loop |
| `v1.10.9` | 2026-09-07 | `lab-gitops-deploy` | `argo-await-sync` stops reading Argo's operation record for hook proof and waits for Synced at the pinned revision; `require_hook` is retired, accepted and ignored, and migration proof moves to the app's schema-aware health route |
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
| `v1.5.0` | 2026-09-01 | `lab-gitops-deploy`, `lab-kubeconform` | new composites: the build-to-served GitOps deploy and the kubeconform manifest check |
| `v1.4.0` | 2026-09-01 | `actionlint`, `lab-build`, `lab-deploy`, `lab-tools` | new `lab-tools` installer and `actionlint` reusable workflow; `lab-build` and `lab-deploy` install crane and kubectl through `scripts/install-tools.sh` |
| `v1.3.0` | 2026-08-31 | `lab-tofu-apply`, `lab-tofu-plan` | new composites extracting the OpenTofu plan and apply pipeline |
| `v1.2.0` | 2026-08-12 | `lab-deploy` | the caller can pin the Application's chart version |
| `v1.1.0` | 2026-08-11 | `lab-build`, `lab-deploy` | first tag: `lab-build` versions, builds, pushes, promotes and releases; `lab-deploy` points an Argo Application at a new image |

Entries `v1.6.0` to `v1.8.0` were reconstructed from tag diffs on 2026-09-06;
the components are derived from the diffs and are reliable, the prose is not
recorded and is left blank rather than guessed.

Entries `v1.1.0` to `v1.5.0` were added on 2026-10-02 from tag diffs, with
the prose taken from the commit subjects. `v1.1.1` is not listed: it only
removed comments from `lab-build` and `lab-deploy`, so no behaviour changed.

## Adding an entry

Add the row **in the same PR as the change**, not at tag time. The tag is
created by hand afterwards (`git tag vX.Y.Z && git push origin vX.Y.Z`), and a
changelog written at tag time is a changelog written from memory.

Then walk `CONSUMERS.md` for every repo pinning a changed component. A consumer
whose components are untouched does not need a PR.
