# The OpenTofu actions

Three composites: `lab-tofu-plan` and `lab-tofu-apply` are one pipeline split
across two triggers, and `lab-tofu-validate` is a credential-free check.

All three set `tofu_wrapper: false` on `setup-opentofu`. The wrapper exists only
to expose a `tofu` call's stdout, stderr and exit code as step outputs, which
nothing here reads, and it is a Node script, so on a runner image without node
every `tofu` invocation dies with `/usr/bin/env: 'node': No such file or
directory`. Disabling it removes the dependency rather than installing one.

## Plan on the pull request, apply on the merge

Plan runs on the pull request and comments what would change. Apply runs on
the merge: it writes its own plan file, runs the guard against that plan, and
applies **exactly that file** with `tofu apply tfplan`. That is the reason the
pair exists: `tofu apply -auto-approve` re-plans at apply time and applies
whatever it found, so a drift or a racing change lands without anything having
vetted it.

```yaml
- uses: willfell/wac.lab.actions/lab-tofu-plan@vX.Y.Z
  with:
    working_directory: infra/cloudflare
    role_arn: arn:aws:iam::634560051830:role/lab-cloudflare-plan
    github_token: ${{ secrets.GITHUB_TOKEN }}
```

## `lab-tofu-plan`

| Input | Meaning | Default |
| --- | --- | --- |
| `working_directory` | Stack directory to plan, relative to the repository root | required |
| `role_arn` | IAM role assumed by OIDC; scope it to this stack's state object | required |
| `github_token` | Token that posts the plan comment and reads pull request labels | required |
| `aws_region` | AWS region for the role assumption and provider | `us-west-1` |
| `tofu_version` | OpenTofu release to install; empty installs the latest | `""` |
| `guard_command` | Shell command vetting the plan; see below. Empty runs no guard | `""` |
| `override_label` | Pull request label that sets `GUARD_OVERRIDE`; empty leaves it unset | `""` |
| `comment_on_pr` | Post the plan as a pull request comment; the step summary is always written | `"true"` |
| `workspace` | OpenTofu workspace, passed as `TF_WORKSPACE`; empty uses the default workspace | `""` |

| Output | Meaning |
| --- | --- |
| `has_changes` | `true` when the plan proposes at least one resource change |
| `plan_json` | Path to the rendered plan JSON |
| `guard_outcome` | `success`, `failure` or `skipped` |

## `lab-tofu-apply`

| Input | Meaning | Default |
| --- | --- | --- |
| `working_directory` | Stack directory to apply, relative to the repository root | required |
| `role_arn` | IAM role assumed by OIDC; scope it to this stack's state object | required |
| `aws_region` | AWS region for the role assumption and provider | `us-west-1` |
| `tofu_version` | OpenTofu release to install; empty installs the latest | `""` |
| `guard_command` | Shell command vetting the plan before it is applied; empty runs no guard | `""` |
| `override_label` | Label on the merged pull request that sets `GUARD_OVERRIDE`; empty leaves it unset | `""` |
| `github_token` | Token that resolves the merged pull request's labels | `""` |
| `workspace` | OpenTofu workspace, passed as `TF_WORKSPACE`; must match the plan caller's | `""` |

| Output | Meaning |
| --- | --- |
| `has_changes` | `true` when the plan proposed at least one resource change |

## Selecting a workspace

Both actions pass `workspace` as `TF_WORKSPACE` into every step that invokes
`tofu`: init, plan, and on apply the apply itself. Empty, the default, means
the default workspace. A repo that selects a workspace per environment must
set the same `workspace` on both its plan and apply callers: a workspace
honored at plan time and dropped at apply time would apply against the wrong
state.

## Guards stay in the calling repo

Neither action ships a policy check. They take a `guard_command` instead, run
from the repository root with two variables set:

| Variable | Meaning |
| --- | --- |
| `TOFU_PLAN_JSON` | Path to the rendered plan JSON |
| `GUARD_OVERRIDE` | Non-empty when `override_label` was on the pull request |

```yaml
    guard_command: uv run lab tf guard --plan "$TOFU_PLAN_JSON" ${GUARD_OVERRIDE:+--allow-destroy}
    override_label: allow-access-destroy
```

A guard is the one piece of the pipeline whose failure mode is an outage rather
than a red check, so it lives in the consumer, as a function under that repo's
own linters and tests. Moving it here would trade it for an untested shell
fragment. These actions only decide *when* to call it and *whether* an override
applies.

`override_label` is resolved through the API on both sides: from the pull
request on plan, from the merge commit's pull request on apply. Reading it from
the push event instead would let a guard that a reviewer had to override be
bypassed simply by merging.

A failing guard on plan still posts its comment before failing the job. On apply
there is nothing to read, so it fails immediately.

## Requirements of the calling job

```yaml
permissions:
  id-token: write
  contents: read
  pull-requests: write
```

Check out the repository and install whatever `guard_command` needs before
calling either action; neither does its own checkout or language setup.

The override-label step shells out to `gh`, which GitHub-hosted images carry and
`ghcr.io/actions/actions-runner` does not. The step installs the matching gh
release itself when the binary is absent, so `override_label` works on a
self-hosted pool. Nothing is downloaded unless `override_label` is set and gh is
missing.

## `lab-tofu-validate`

Checks OpenTofu formatting (`tofu fmt -check -recursive`) and validates every
root under a directory, without assuming any cloud role or reading any state.
It installs OpenTofu itself, so a caller needs no setup step.

```yaml
- uses: willfell/wac.lab.actions/lab-tofu-validate@vX.Y.Z
  with:
    working_directory: infra
```

| Input | Meaning | Default |
| --- | --- | --- |
| `working_directory` | Directory searched for OpenTofu roots | `infra` |
| `tofu_version` | OpenTofu release to install | `1.11.2` |

A root is any directory whose `*.tf` files declare an anchored `backend "`
block. Discovery walks `working_directory` for that pattern rather than
requiring a caller to list roots, so a `backend.tf` layout and a `versions.tf`
layout are found the same way. Discovering zero roots is a loud failure, not a
vacuous pass: a directory that quietly stopped matching the pattern would
otherwise let the check pass while validating nothing.

Each root is initialized with `-backend=false`, so the check needs no cloud
credentials and can gate any pull request cheaply.
