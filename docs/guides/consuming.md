# Consuming a component

## Pin an exact patch tag

Consume every component at an **exact patch-level tag**, never at `main` and
never at a floating major:

```yaml
- uses: willfell/wac.lab.actions/lab-tools@vX.Y.Z
```

`vX.Y.Z` here and in every example on these pages stands for the tag you pin:
the newest row of [CHANGELOG.md](https://github.com/willfell/wac.lab.actions/blob/main/CHANGELOG.md)
unless you have a reason to stay behind.

`@main` lets an unrelated push here change how a consuming repo builds and
deploys. A floating `@v1` is the same hole with a slower fuse: it moves without
review, and this repo's `v1` already went stale once, carrying a bootstrap that
shelled out to `gh`, which the self-hosted runner image does not have.
Repointing a major tag depends on a human remembering to.

The cost of exact pins is that bumping a consumer is a deliberate edit. That is
the point: an unreviewed change to a component cannot reach anyone's CI, and
cannot silently fail to reach it either.

Tags are cut by hand after a change merges. Until the tag exists, a consumer
pinned to it fails at job start-up with "unable to resolve action", before
running a single step.

## One tag for the whole repo

All actions share one tag. A release touching only `lab-route` still moves
`lab-tools`'s pin to the same ref, pointing at byte-identical code. Reusable
workflows ride the same repo-wide exact tags. Keep every component a repo pins
on the same tag.

## Bumping: the two tables

- [CHANGELOG.md](https://github.com/willfell/wac.lab.actions/blob/main/CHANGELOG.md)
  names the **components** each tag changed. A consumer pinning only components
  a tag does not list can bump without re-testing. A tag that touches only
  `scripts/` still changes the components that run those scripts; the table at
  its top maps each script to its components.
- [CONSUMERS.md](https://github.com/willfell/wac.lab.actions/blob/main/CONSUMERS.md)
  lists every pinned component, repo and file, as merged on each default branch.

Bumping a tag means reading the changelog for the components that moved, then
walking the consumers table for the repos pinning those. A consumer whose
components are untouched needs no PR; one whose components moved and is
skipped runs old code indefinitely, with nothing to say so. A tag whose row says
the fleet rules changed fails every `agents-md` consumer's gate until that
consumer re-renders its AGENTS.md tail in the bump PR; see
[agents-md](../reference/agents-md.md).

## Reusable workflows take the caller's runner

**Every reusable workflow takes an optional `runner` input. When the caller
passes none, the job runs on the caller's `CI_RUNNER_SMALL` variable; a caller
passes `runner: ${{ vars.CI_RUNNER_STANDARD }}` for a build.** A reusable
workflow's jobs run on the *caller's* runners, billed to the caller and queued
against the caller's pools, so the label has to come from the caller. A caller
that sets neither the input nor `CI_RUNNER_SMALL` fails at once instead of
landing on a hosted runner.

```yaml
jobs:
  lint:
    uses: willfell/wac.lab.actions/.github/workflows/actionlint.yml@vX.Y.Z
    with:
      runner: ${{ vars.CI_RUNNER_STANDARD }}
```

`runs-on` receives the value as a single label, so `runner` takes one label
rather than a list. Inside `wacwini`, the fleet runner policy accepts only the
size variables; see [actionlint](../reference/actionlint.md).
