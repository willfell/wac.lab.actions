# lab-actions

Composite GitHub Actions and reusable workflows shared across the fleet. This
repo has no runtime: it ships nothing to the cluster, builds no image, and runs
no service. It is a library of CI building blocks, and its only consumers are
the other repos' workflows, which pin it at exact tags.

It is public on purpose: every credential arrives as an input or through
GitHub's native OIDC job environment, so there is nothing secret here, and the
reusable workflows can check this repo out at the caller's pinned commit
without a token.

## Start here

- Calling a component from another repo: [Consuming a component](guides/consuming.md).
- Changing a component here, or cutting a tag: [Changing and releasing a component](guides/releasing.md).
- How the reusable workflows reach this repo's scripts: [the shared checkout](architecture/shared-checkout.md).

## The components

| Component | Kind | Purpose |
| --- | --- | --- |
| [`lab-tools`](reference/lab-tools.md) | composite | Install the fleet's k8s and registry tooling, arch-aware, onto `PATH` |
| [`lab-kubeconform`](reference/lab-kubeconform.md) | composite | Validate a kustomize overlay by piping its build through kubeconform |
| [`lab-tofu-plan`](reference/opentofu.md) | composite | Plan an OpenTofu stack on a pull request, guard it, and comment the result |
| [`lab-tofu-apply`](reference/opentofu.md) | composite | Guard and apply that stack's plan file on merge |
| [`lab-tofu-validate`](reference/opentofu.md) | composite | Check formatting and validate every OpenTofu root under a directory, no credentials needed |
| [`lab-route`](reference/lab-route.md) | composite | Send heavy jobs to a Lab runner pool when it has room, and to an overflow label otherwise |
| [`lab-release`](reference/lab-release.md) | composite | Apply Lab's warehouse migrations through Lab's TLS release service and verify both receipts |
| [`actionlint`](reference/actionlint.md) | reusable workflow | Lint the caller's workflows and enforce the fleet runner policy |
| [`techdocs`](reference/techdocs.md) | reusable workflow | The fleet TechDocs gate: pinned strict MkDocs build and nav-category check |
| [`agents-md`](reference/agents-md.md) | reusable workflow | The fleet AGENTS.md gate |
| [`nextjs-site-check`](reference/nextjs-site.md) | reusable workflow | Prove a static-exported Next.js site still builds |
| [`nextjs-site-deploy`](reference/nextjs-site.md) | reusable workflow | Build a static-exported Next.js site and ship it to S3 and CloudFront |
| [`wac-ci`](reference/wac-ci.md) | reusable workflow | An app's pull-request checks and deploy-chart render, from its [wacd profile](reference/wacd-profile.md) |
| [`wac-cd`](reference/wac-cd.md) | reusable workflow | Version, build, publish and release an app through Lab's gateway on a merge to `main` |

Every tag and the components it changed are in
[CHANGELOG.md](https://github.com/willfell/wac.lab.actions/blob/main/CHANGELOG.md);
every repo pinning a component is in
[CONSUMERS.md](https://github.com/willfell/wac.lab.actions/blob/main/CONSUMERS.md).

## Why this entity has only two annotations

`github.com/project-slug` and `backstage.io/techdocs-ref`, and nothing else.
There is no Argo CD `Application`, no namespace, and no workload, so there is
nothing for the CD or Kubernetes annotations to point at, and no metrics for a
Prometheus one. The absences are the shape of a library, not an oversight.
