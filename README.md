# wac.lab.actions

Composite GitHub Actions and reusable workflows shared across the fleet's
repositories, consumed at exact patch tags. It has no runtime: every effect it
has happens inside a caller's CI.

Public on purpose: every credential arrives as an input or through GitHub's
native OIDC job environment, so there is nothing secret here, and a public repo
lets the reusable workflows check it out at the caller's pinned commit without
a token.

The full documentation is under [`docs/`](docs/index.md) and published on
[docs.wacwini.com](https://docs.wacwini.com/catalog/default/component/lab-actions).

## Components

| Component | Kind | Reference |
| --- | --- | --- |
| `lab-tools` | composite | [docs/reference/lab-tools.md](docs/reference/lab-tools.md) |
| `lab-kubeconform` | composite | [docs/reference/lab-kubeconform.md](docs/reference/lab-kubeconform.md) |
| `lab-tofu-plan` | composite | [docs/reference/opentofu.md](docs/reference/opentofu.md) |
| `lab-tofu-apply` | composite | [docs/reference/opentofu.md](docs/reference/opentofu.md) |
| `lab-tofu-validate` | composite | [docs/reference/opentofu.md](docs/reference/opentofu.md) |
| `lab-route` | composite | [docs/reference/lab-route.md](docs/reference/lab-route.md) |
| `lab-release` | composite | [docs/reference/lab-release.md](docs/reference/lab-release.md) |
| `actionlint` | reusable workflow | [docs/reference/actionlint.md](docs/reference/actionlint.md) |
| `techdocs` | reusable workflow | [docs/reference/techdocs.md](docs/reference/techdocs.md) |
| `agents-md` | reusable workflow | [docs/reference/agents-md.md](docs/reference/agents-md.md) |
| `nextjs-site-check` | reusable workflow | [docs/reference/nextjs-site.md](docs/reference/nextjs-site.md) |
| `nextjs-site-deploy` | reusable workflow | [docs/reference/nextjs-site.md](docs/reference/nextjs-site.md) |
| `wac-ci` | reusable workflow | [docs/reference/wac-ci.md](docs/reference/wac-ci.md), profile in [docs/reference/wacd-profile.md](docs/reference/wacd-profile.md) |
| `wac-cd` | reusable workflow | [docs/reference/wac-cd.md](docs/reference/wac-cd.md) |

## Pinning

Consume at an **exact patch-level tag**, never at `main` and never at a floating
major:

```yaml
- uses: willfell/wac.lab.actions/lab-tools@vX.Y.Z
```

All actions share one tag. A release touching only `lab-route` still moves
`lab-tools`'s pin to the same ref, pointing at byte-identical code. Reusable
workflows ride the same repo-wide exact tags.

[CHANGELOG.md](CHANGELOG.md) records which components each tag changed, and
[CONSUMERS.md](CONSUMERS.md) records every repo, file and pin. Why the rule
exists and how a bump walk uses the two files:
[docs/guides/consuming.md](docs/guides/consuming.md). Changing a component and
cutting a tag: [docs/guides/releasing.md](docs/guides/releasing.md).

`lab-build`, `lab-deploy` and `lab-gitops-deploy` were removed in `v1.30.0`.
Their documentation is in this file at any tag up to `v1.29.0`.
