# The Next.js site workflows

Two reusable workflows for the static-exported Next.js sites: `nextjs-site-check`
proves a pull request still builds, and `nextjs-site-deploy` builds and ships
the export to S3 behind CloudFront.

## nextjs-site-deploy

Builds a static-exported Next.js site and ships it: install (with the yarn
dependency cache restored via `setup-node`), restore cached optimized images
from S3, validate and re-optimize images, build, run an optional postbuild
step, verify the `out` export exists, sync images with an immutable cache
header, sync the rest of the app files, and invalidate CloudFront.
Canonicalizes the deploy.yml the site fleet's repos had copy-pasted and
drifted -- one step and one invalidation path apart.

```yaml
jobs:
  deploy:
    uses: willfell/wac.lab.actions/.github/workflows/nextjs-site-deploy.yml@vX.Y.Z
    permissions:
      id-token: write
      contents: read
    with:
      role_arn: arn:aws:iam::111111111111:role/site-deploy
      aws_region: us-east-1
    secrets:
      s3_bucket: ${{ secrets.S3_BUCKET_NAME }}
      cloudfront_distribution_id: ${{ secrets.CLOUDFRONT_DISTRIBUTION_ID }}
```

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label the job is sent to, in the caller's repo | empty: the caller's `CI_RUNNER_SMALL` |
| `app_dir` | Directory holding the Next.js app | `app` |
| `node_version` | Node release installed before `yarn install` | `22` |
| `role_arn` | OIDC role assumed for the AWS credentials used to deploy | required |
| `aws_region` | AWS region passed to `configure-aws-credentials` | required |
| `postbuild_command` | Shell command run after `yarn build`, skipped when empty | `""` |
| `build_env` | Newline-separated `KEY=value` pairs exported into the build step | `""` |
| `invalidation_paths` | Space-separated CloudFront invalidation path patterns | `/*.html /index.html /_next/* /sitemap*.xml` |
| `install_command` | Shell command that installs dependencies | `yarn install` |
| `app_cache_control` | `Cache-Control` header applied to the app-files S3 sync, skipped when empty | `""` |
| `bucket` | S3 bucket the export is synced to, as a plain string; takes precedence over the `s3_bucket` secret | `""` |

| Secret | Meaning |
| --- | --- |
| `s3_bucket` | S3 bucket the export is synced to; optional, but one of `bucket` or `s3_bucket` must be set |
| `cloudfront_distribution_id` | CloudFront distribution invalidated after sync |

Values passed through `secrets:` are masked in logs, which turns a bucket
name into `***` in `aws s3 sync` output even when the name is not sensitive.
Callers whose bucket name is not a secret should pass it via the `bucket`
input instead so deploy failures stay legible; `bucket`, when set, takes
precedence over `s3_bucket`. The workflow's first step fails immediately if
neither is supplied.

`node_version` defaults to `22`, retiring the fleet's node-18 debt; a caller
whose build breaks on 22 can override it to `"20"` and then `"18"` while it
migrates, rather than being blocked on the bump.

`postbuild_command` and `install_command` are commands by contract, the same
as any `run:` step -- both are deliberately interpolated into a shell step,
not passed through `env:`. Every other input above is data and rides `env:`
inside the workflow; treat `postbuild_command` and `install_command` as
untrusted-caller-writable code, not as data values.

## nextjs-site-check

Runs the same install (with the yarn dependency cache restored via
`setup-node`), image validation, and build a pull request needs to prove a
Next.js site still builds, without any of the deploy steps.

```yaml
jobs:
  check:
    uses: willfell/wac.lab.actions/.github/workflows/nextjs-site-check.yml@vX.Y.Z
```

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label the job is sent to, in the caller's repo | empty: the caller's `CI_RUNNER_SMALL` |
| `app_dir` | Directory holding the Next.js app | `app` |
| `node_version` | Node release installed before `yarn install` | `22` |

`node_version` defaults to `22` with the same fallback ladder (`20`, then
`18`) as `nextjs-site-deploy` -- a caller's green check on the default is the
acceptance test for the node-22 bump.
