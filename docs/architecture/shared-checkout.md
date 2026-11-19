# How the reusable workflows run this repo's scripts

A composite action finds its scripts beside itself, at
`${{ github.action_path }}/../scripts/`, so it always runs the scripts from the
tag the caller pinned. A reusable workflow has no such path. Five of them
(`actionlint`, `techdocs`, `agents-md`, `wac-ci` and `wac-cd`) need this repo's
scripts, so each job that does checks this repo out a second time, at the
exact commit the caller pinned.

## The checkout

```yaml
- env:
    SHARED_WORKFLOW_SHA: ${{ job.workflow_sha }}
  run: '[[ "$SHARED_WORKFLOW_SHA" =~ ^[0-9a-f]{40}$ ]]'
- name: Check out the shared scripts (actions/checkout v7.0.1)
  uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
  with:
    repository: willfell/wac.lab.actions
    ref: ${{ job.workflow_sha }}
    persist-credentials: false
```

- `job.workflow_sha` is the commit of the workflow file that defines the job,
  which for a called workflow is the commit behind the caller's pinned tag.
  `github.workflow_sha` is the caller's own commit, and `main` would let a push
  here change every consumer's gate at once. Tags `v1.13.0` to `v1.15.0` read
  `github.job_workflow_sha`, a context that does not exist, and so silently
  checked out `main`; `v1.15.1` fixed it.
- The validation step runs first, so an empty or missing identity fails the job
  instead of letting `actions/checkout` fall back to the default branch.
  GitHub Enterprise Server does not expose this context and is not supported.
- The checkout needs no token only because this repository is public.
- `.github/actionlint.yaml` ignores `property "workflow_sha" is not defined` for
  exactly these five files: older actionlint schemas do not know the property.

The gates check out to a dot-directory (`.techdocs-gate`, `.agents-md-gate`).
`wac-ci` and `wac-cd` check out to `.wacd` and move it to `$RUNNER_TEMP/wacd`
in the next step, before anything else runs, so an app's `ci.commands` and its
`docker build` context never see it.

## What enforces it

`.github/scripts/check_actions.py` fails on a shared checkout of this
repository whose `ref` is anything but `${{ job.workflow_sha }}`, that persists
credentials, or that has no validation step directly before it, and on a gate
that checks out more than one shared source.
`.github/scripts/test_workflow_identity.py` and
`.github/scripts/test_reusable_workflows.py` prove those rules reject the
broken forms.
