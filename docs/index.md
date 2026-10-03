# lab-actions

Composite GitHub Actions and reusable workflows shared across the fleet. This
repo has no runtime: it ships nothing to the cluster, builds no image, and runs
no service. It is a library of CI building blocks, and its only consumers are
the other repos' workflows.

Public on purpose — composite actions are YAML wrappers and every credential is
passed in as an input, so there is nothing secret here, and a public repo avoids
the private-repo Access setting that would otherwise have to be right in every
consuming repository.

## The components

| Action | Purpose |
| --- | --- |
| `lab-build` | Compute semver, build, push an immutable tag, move a mutable environment pointer, cut a release |
| `lab-deploy` | Point an Argo CD Application at a new release and wait for it to converge |
| `lab-gitops-deploy` | Build, push, pin via a kustomize commit-back, sync Argo, verify the served build |
| `lab-kubeconform` | Validate a kustomize overlay by piping its build through kubeconform |
| `lab-tofu-plan` | Plan an OpenTofu stack on a pull request, guard it, and comment the result |
| `lab-tofu-apply` | Guard and apply the reviewed plan file on merge |
| `lab-tofu-validate` | Check formatting and validate every OpenTofu root under a directory, no credentials needed |
| `lab-tools` | Install the fleet's k8s and registry tooling, arch-aware, onto `PATH` |

Five reusable workflows ride the same tags: `actionlint.yml` (the fleet's
shared lint and runner-policy gate), `nextjs-site-check.yml`,
`nextjs-site-deploy.yml`, `techdocs.yml` (the fleet TechDocs gate), and
`agents-md.yml` (the fleet AGENTS.md gate).

## The pinning rule

Consume at an **exact patch-level tag** — never at `main`, and never at a
floating major:

```yaml
- uses: willfell/wac.lab.actions/lab-build@v1.10.5
```

`@main` lets an unrelated push here change how a consuming repo builds and
deploys. A floating `@v1` is the same hole with a slower fuse: it moves without
review, and this repo's `v1` already went stale carrying a bootstrap that shells
out to `gh`, which is absent from the self-hosted runner image.

The cost is that bumping a consumer is a deliberate edit, which is the point —
an unreviewed change to a composite cannot reach anyone's CI.

Tags are created by hand; merging a PR publishes nothing. Until the tag exists,
a consumer pinned to it fails at job start-up with "unable to resolve action",
before running a single step.

## The two tables that make a bump safe

All components share one tag, so the version describes the repo rather than any
one action. Two files carry the rest:

- `CHANGELOG.md` — which components each tag actually changed. The row goes in
  the PR that makes the change, not at tag time; a changelog written at tag time
  is written from memory, and the components column is the part consumers rely
  on.
- `CONSUMERS.md` — every pinned component, repo, and file. Bumping a tag means
  reading the changelog for the components that moved, then walking the
  consumers table for the repos pinning those. A consumer whose components moved
  and gets skipped runs old code indefinitely, with nothing to say so.

A stale consumers table is worse than no table, because the walk it drives
silently skips whatever it forgot.

## Where the full reference lives

This page is the map. The per-action input tables, the guard contract the
OpenTofu pair expects, and the arch-mapping notes are in the repo's
[README](https://github.com/willfell/wac.lab.actions/blob/main/README.md),
alongside [CHANGELOG.md](https://github.com/willfell/wac.lab.actions/blob/main/CHANGELOG.md)
and [CONSUMERS.md](https://github.com/willfell/wac.lab.actions/blob/main/CONSUMERS.md).

## Why this entity has only two annotations

`github.com/project-slug` and `backstage.io/techdocs-ref`, and nothing else.
There is no Argo CD `Application`, no namespace, and no workload, so there is
nothing for the CD or Kubernetes annotations to point at, and no metrics for a
Prometheus one. The absences are the shape of a library, not an oversight.
