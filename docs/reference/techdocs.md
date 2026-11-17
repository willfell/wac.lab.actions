# techdocs

Runs the fleet TechDocs gate against the calling repo: the pinned strict
MkDocs build, and the nav-category check that keeps every repo's sidebar the
same shape on docs.wacwini.com.

```yaml
jobs:
  techdocs:
    uses: willfell/wac.lab.actions/.github/workflows/techdocs.yml@vX.Y.Z
    with:
      runner: ${{ vars.CI_RUNNER_SMALL }}
```

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label the jobs are sent to, in the caller's repo | empty: the caller's `CI_RUNNER_SMALL` |

The gate is two steps, and both have to pass:

```
uvx --from mkdocs-techdocs-core==1.6.1 --with mkdocs==1.6.0 \
  mkdocs build --strict --site-dir "$RUNNER_TEMP/site"
scripts/check-docs-nav.sh mkdocs.yml
```

Both pins are exact. `mkdocs-techdocs-core` and `mkdocs` move independently and
a floating pair has silently changed what `--strict` rejects before, so a docs
build that is green here has to stay green on the portal's own renderer.

`--strict` alone is not the gate. In MkDocs 1.6 a page in neither the nav nor
`exclude_docs` is reported at INFO, and `--strict` only escalates WARNINGs, so
an unfiled page builds green. The `validation:` block in each repo's
`mkdocs.yml` is what raises it to a warning; `check-docs-nav.sh` is what proves
the block is still there, along with the seven fixed category names, their
order, the flat `Overview: index.md` mapping, and the standing `exclude_docs`
list.

`scripts/check-docs-nav.sh` is vendored here rather than fetched from
`wac.plugins`, which is private: reaching into it at run time would need a token
in every consumer. The `gate` job checks this repo out a second time at
`job.workflow_sha`, so the script it runs is the one that shipped in the tag the
caller pinned; see
[How the reusable workflows run this repo's scripts](../architecture/shared-checkout.md).

A repo with no `mkdocs.yml` is not a failure. The `detect` job probes for the
file and `gate` is skipped, so the workflow can be added to a repo's `ci.yml`
before its docs exist.
