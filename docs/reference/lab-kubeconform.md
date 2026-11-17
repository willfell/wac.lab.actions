# lab-kubeconform

Renders a kustomize overlay and validates the result with kubeconform. It
replaced three hand-rolled copies of this two-command pipeline that had drifted
onto different tool versions and, on the arm64 consumers, hard-coded one
architecture's release-asset URLs.

```yaml
- uses: willfell/wac.lab.actions/lab-kubeconform@vX.Y.Z
  with:
    kustomize_dir: deploy/k8s
```

| Input | Meaning | Default |
| --- | --- | --- |
| `kustomize_dir` | Directory holding the kustomization to validate | required |
| `flags` | Flags passed to kubeconform | `-strict -summary` |
| `kustomize_version` | kustomize release to install | `v5.7.1` |
| `kubeconform_version` | kubeconform release to install | `v0.7.0` |
| `github_token` | Token for the upstream public schema API rate limit | `${{ github.token }}` |

The tools come from `scripts/install-tools.sh` (see [lab-tools](lab-tools.md));
the validation is `scripts/validate-kustomize.sh`, with
`scripts/schema-api-proxy.py` as its fallback transport.

## The schema transport fallback

The overlay is rendered once, and the same bytes are validated on every
attempt. There are at most two attempts.

- When kubeconform cannot fetch its default Kubernetes schemas from
  `raw.githubusercontent.com`, the action retries through a temporary adapter
  bound only to loopback. The adapter fetches the same repository, ref and
  schema path through GitHub's public contents API over verified TLS.
- Only the default upstream location and an explicitly configured
  `datreeio/CRDs-catalog/main` location are redirected, in their original search
  order. Other custom schema hosts, strictness, output format and cache flags
  are unchanged.
- An upstream 404 stays a 404, so a caller's `-ignore-missing-schemas` policy
  for custom resources still holds. Other API errors and non-schema responses
  stay errors. Invalid manifests and unresolved schema errors still fail the
  gate.
- The job token goes through the optional `github_token` input so the shared
  anonymous API rate limit is not exhausted; an empty input opts into anonymous
  requests. Credentials go only to the fixed GitHub API host, and redirects are
  rejected.
- Python 3.12 is provisioned by the action and the adapter uses only the
  standard library, so callers need no setup. The adapter is stopped and its
  temporary files removed on exit.

`scripts/tests/validate-kustomize.test.sh` and
`scripts/tests/schema-api-proxy.test.py` cover recovery and strict rejection;
the smoke job also validates a real core and CRD fixture through the composite.
