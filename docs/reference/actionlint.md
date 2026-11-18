# actionlint

Runs [`raven-actions/actionlint`](https://github.com/raven-actions/actionlint)
against the caller's own workflows. It provisions the action's own dependencies
first, because a self-hosted runner image does not have to carry them and
`ghcr.io/actions/actions-runner` carries none of them: `node`/`npm` for the
tool-download step, `pipx` for pyflakes, and `shellcheck` for linting `run:`
blocks. Each was a separate red job on the first ARC pool to call this workflow.
The apt step is skipped when both tools are already present, so a
GitHub-hosted caller pays nothing.

```yaml
jobs:
  lint:
    uses: willfell/wac.lab.actions/.github/workflows/actionlint.yml@vX.Y.Z
    permissions:
      contents: read
```

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label the job is sent to, in the caller's repo | empty: the caller's `CI_RUNNER_SMALL` |
| `runner_policy` | `legacy`, or `fleet` for explicit fleet routing validation | `legacy` |
| `transitional_labels` | Temporary repository labels, spaces or commas | empty |
| `allowed_host_labels` | Declared host labels, spaces or commas | empty |

The runner policy itself is `scripts/runner-policy.py`, tested by
`scripts/tests/runner-policy.test.py`.

`runner_policy: legacy` remains the default: a non-`ubuntu-latest` runner
arms the original direct-label scanner, preserving existing callers.

`runner_policy: fleet` validates direct `runs-on`, matrices and reusable
`with.runner` and `with.build_runner` choices independently of where lint
runs. Inside `wacwini`, `runs-on`, `with.runner` and `with.build_runner`
accept only `${{ vars.CI_RUNNER_SMALL }}`,
`${{ vars.CI_RUNNER_STANDARD }}`, `${{ vars.CI_RUNNER_LARGE }}`,
`${{ inputs.runner || vars.CI_RUNNER_<SIZE> }}`,
`${{ inputs.build_runner || inputs.runner || vars.CI_RUNNER_<SIZE> }}`,
bare `${{ inputs.runner }}` in a `workflow_call` workflow, matrices of those,
and `allowed_host_labels`. There are three sizes: small, standard, and large;
large is for heavy test suites. Literal `vars.*` text and empty `runs-on` values
(`[]`, or a dict without `labels`) fail. Outside the organization the literal
`ubuntu-24.04-arm` and `transitional_labels` are also accepted. `CI_HOSTED_LABEL`
and owner switches are gone.

The fleet policy also accepts
`runs-on: ${{ fromJSON(needs.<route>.outputs.runners).<key> }}` and the bracket
form `['<key>']` when `<route>` is a needed job in the same workflow whose
`outputs.runners` comes from a `lab-route` step with size-variable `lab` and
`overflow` and `<key>` in its `jobs`; see [lab-route](lab-route.md). Every other
`needs.*` or `fromJSON` runner stays rejected.

Helpers bill at most two minutes per private-repository job. Tests, builds,
Docker and deployment jobs stay on our runners. Public-repository jobs are free.
Every helper has a ten-minute timeout; timeouts bound failures, not eligibility.
The library's public smoke matrix alone may request `ubuntu-latest`, explicitly
through `allowed_host_labels`; another repository cannot use that exception.
