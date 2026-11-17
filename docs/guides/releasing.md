# Changing and releasing a component

A merge to `main` changes nobody's CI. A change is shipped only when it has
merged, a human has cut the tag, and each affected consumer has taken a bump PR.
This page is the first two; the bump walk is its own task, driven by
`CHANGELOG.md` and `CONSUMERS.md` (see [Consuming a component](consuming.md)).

## What a change carries

A pull request that changes a component's behaviour carries, in the same PR:

- the code, under `lab-*/action.yml`, `.github/workflows/` or `scripts/`;
- a test that fails without it: the scenario tests under `scripts/tests/`, or
  `.github/scripts/` for the structural checks;
- the component's reference page under `docs/reference/`, kept in step with its
  inputs, secrets and outputs (`.github/scripts/test_docs.py` fails when a table
  and the component disagree);
- a `CHANGELOG.md` row naming the **components** it changed, never paths. A
  change under `scripts/` changes every component that runs that script; the
  table at the top of `CHANGELOG.md` says which, and the docs test fails when a
  script is missing from it.

A new component also gets a reference page filed in `mkdocs.yml`'s nav, a row
in `README.md`'s component table, and a row in `docs/index.md`'s. README-only,
docs-only and CI-only changes are not releases and get no row.

Write the row in the PR, not at tag time. A changelog written at tag time is
written from memory, and the components column is the part consumers rely on.

## Which number to move

All components share one tag, so the version describes the repo, not any one
component:

- **patch**: a fix inside a component that does not change its inputs, outputs,
  or what a caller must pass.
- **minor**: a new component, a new optional input, a removed component or
  input, or a behaviour change a caller could notice. A new *required* input is a
  minor here too: consumers pin exact tags, so nobody is broken until they
  choose to bump, and the changelog row is what warns them.
- **major**: reserved. Nothing has needed one; `v1` is deliberately not
  maintained as a floating pointer, so a major bump would buy only the label.

## Cutting the tag

Tags are created by hand, after the PR has merged, and only by or at the
request of the repo's owner. Nothing computes them, and merging publishes
nothing.

```sh
git tag vX.Y.Z && git push origin vX.Y.Z
```

Never move, delete or re-point a published tag: consumers pin them, and a bad
release is fixed by a new tag.

## A fleet-rules change

`agents-md/fleet-rules.md` is rendered verbatim into every fleet repo's
AGENTS.md tail. A tag that changes it fails every `agents-md` consumer's gate
at its bump until that consumer re-renders, which is intended: the new rules
ride the bump PR and cannot go stale silently. The tag's CHANGELOG row must say
so. Re-render this repo's own AGENTS.md in the same PR with
`uv run scripts/agents-md.py render`.
