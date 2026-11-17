# lab-tools

Installs the fleet's k8s and registry tooling (`kubectl`, `kustomize`, `crane`,
`kubeconform`, `helm`) to `$RUNNER_TEMP/bin` and appends it to `GITHUB_PATH`, so
every later step in the job finds them on `PATH`. It maps `uname -m` to each
tool's release-asset arch naming, so the same call works unmodified on
GitHub-hosted amd64 runners and the Lab's arm64 runners.

```yaml
- uses: willfell/wac.lab.actions/lab-tools@vX.Y.Z
  with:
    tools: kubectl,kustomize,crane,kubeconform
```

| Input | Meaning | Default |
| --- | --- | --- |
| `tools` | Comma-separated subset of `kubectl,kustomize,crane,kubeconform,helm` | required |
| `kubectl_version` | kubectl release installed when `kubectl` is requested | `v1.35.0` |
| `kustomize_version` | kustomize release installed when `kustomize` is requested | `v5.7.1` |
| `crane_version` | go-containerregistry release installed when `crane` is requested | `v0.20.6` |
| `kubeconform_version` | kubeconform release installed when `kubeconform` is requested | `v0.7.0` |
| `helm_version` | helm release installed when `helm` is requested; empty installs the latest | `""` |

## The shared installer

The work is `scripts/install-tools.sh`, which `lab-kubeconform`, `wac-ci` and
`wac-cd` also run, so a change to it changes all four components.

Helm installs through the upstream `get-helm-3` script from `helm/helm` `main`.
When `raw.githubusercontent.com` fails, the installer fetches the same file
through GitHub's contents API. It requires a successful download and a bash
shebang before running it, so a failed fetch can never run an empty installer.
`scripts/tests/helm-install.test.sh` covers both paths against a fake `curl`.

This repo's CI runs a smoke matrix on `ubuntu-latest` (amd64) and
`ubuntu-24.04-arm` (arm64) on every push and pull request, installing all five
tools and executing each one, so the arch mapping is enforced by a real job
rather than trusted.
