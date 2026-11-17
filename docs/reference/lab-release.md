# lab-release

Applies Lab's warehouse and ecosystem migrations through Lab's scoped release
service (the ci-deploy gateway), authenticated by the job's GitHub OIDC token
over verified TLS, and proves both receipts. Its one caller is wac.lab's
`warehouse-image.yml`, which runs it before publishing the warehouse image pin.

```yaml
- uses: willfell/wac.lab.actions/lab-release@vX.Y.Z
  with:
    operation: migrate
    wait_timeout: "2700"
    image_tag: ${{ steps.tag.outputs.tag }}
    image_digest: ${{ steps.build.outputs.digest }}
```

| Input | Meaning | Default |
| --- | --- | --- |
| `operation` | `migrate`, the only operation the service admits from this action | required |
| `image_tag` | Warehouse image tag to migrate with | `""` |
| `image_digest` | Warehouse image digest to migrate with, `sha256:<64 hex>` | `""` |
| `wait_timeout` | Overall evidence deadline in seconds, at most 3600 | `"2100"` |

| Output | Meaning |
| --- | --- |
| `receipt` | Path of a local JSON receipt, for the caller to upload with its release evidence |

## What it proves

The job needs `id-token: write` and the runner's public `/etc/ci-deploy/ca.crt`
trust mount. There is no Kubernetes token and no unverified-TLS fallback. The
service derives the target from the verified repository identity and the
trusted workflow; the caller names none.

A migration succeeds only when the receipt matches this run's source SHA, the
image `localhost:30500/warehouse:<tag>@<digest>`, and this run's ID and attempt,
and both the `warehouse` and the `ecosystem` jobs report success with a UID.
Each poll mints a fresh OIDC token, redirects are rejected, a denial fails at
once, and busy or dependency responses (409, 503) retry within `wait_timeout`.

The service's admission rules are Lab's
[ci-deploy contract](https://github.com/wacwini/wac.lab/blob/main/docs/ci-deploy-contract.md).
It admits `release` and `status` only from `wac-cd`, so this action carries
nothing else.

## The client is shared

`scripts/ci-release.py` is both this action's CLI and a module other code
loads. `wacd-release.py` imports its transport (`CA_FILE`, `NoRedirect`,
`ReleaseError`, `request`), and wac.lab's `.github/jobs/ci-deploy-proof.py`
loads it from a checkout of this repo at its pinned tag and uses `CA_FILE`,
`URL`, `fetch`, `oidc_token` and `migrated`. Renaming any of them breaks
`wac-cd` or wac.lab's denial proof at its next bump;
`test_loaders_find_the_names_they_depend_on` in
`scripts/tests/ci-release.test.py` fails first.
