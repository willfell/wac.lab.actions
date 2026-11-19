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

### Three jobs, and the one that runs the site's code holds no token

A GitHub OIDC token does not say which step asked for it, so any code that runs
in a job holding `id-token: write` can mint the deploy role's credentials. The
site's own code (its `package.json` scripts, every package `yarn install`
fetches, `install_command`, `postbuild_command`) therefore runs only in `build`,
which holds `contents: read` and nothing else. The two jobs that assume the role
run nothing from the site.

| Job | Needs | Holds | Runs | Hands on |
| --- | --- | --- | --- | --- |
| `image-cache` | | `contents: read`, `id-token: write`, the role | this repository's scripts at the defining commit (`job.workflow_sha`), and `aws s3 sync` of `s3://<bucket>/images` (`*.webp`, `*.avif`, `.image-manifest.json`) | artifact `nextjs-image-cache`, kept one day, only when the sync found a file |
| `build` | `image-cache` | `contents: read` | the caller's checkout (`persist-credentials: false`, so no token is left on disk where the site's code runs), the node, npm and yarn fallbacks, setup-node, `install_command`, the image cache unpacked into `<app_dir>/public/images`, `yarn images:validate`, `yarn images:optimize`, `yarn build` with `build_env`, `postbuild_command`, the `out` check | artifact `nextjs-site-out` (the contents of `<app_dir>/out`), kept one day |
| `deploy` | `build` | `contents: read`, `id-token: write`, the role | this repository's scripts at the defining commit, then the image sync (skipped when the export has no `images/`), the app-file sync and the CloudFront invalidation | the site, in S3 |

The hand-offs:

- **`nextjs-image-cache`** is read-only input to `build`. An empty or missing
  S3 prefix, or a sync that fails, is no cache, as before: `image-cache`
  passes, uploads nothing, and `build` skips the download. A cache artifact
  that cannot be downloaded (one older than a day, on a re-run of `build`
  alone) is also no cache; `images:optimize` then re-creates every image.
- **`nextjs-site-out`** is data to `deploy`. It is downloaded to
  `$RUNNER_TEMP/site-out`, never executed, and named to `aws s3 sync` only
  as an argument. Before the role is assumed, `scripts/check-site-export.sh`
  walks it and fails the job, naming every entry, on any symbolic link (one
  whose target resolves outside the export is named as such), on any entry
  that is neither a regular file nor a directory, and on any entry whose real
  path lies outside it; `aws s3 sync` follows links, so a link could
  otherwise publish a file from anywhere on the runner. Both syncs also pass
  `--no-follow-symlinks`. The names and link targets in those annotations
  are escaped as workflow-command data (`%`, CR and LF), so a name the build
  chose cannot end its annotation and start a command of its own.
- An artifact keeps no empty directory, so a site whose `out/images` is
  empty, or absent, reaches `deploy` with no `images/`; the image sync is then
  skipped with a line saying so, where a sync of the missing directory would
  fail the deploy.
- Both artifacts include hidden files, so `images/.image-manifest.json`
  travels with the images it describes, as the old single job's sync did.

`image-cache` and `deploy` check out no caller code, run no `node`, `yarn`,
`npm` or Python, set `PYTHONSAFEPATH: "1"` for the job, and use only the
commit-pinned checkout, artifact and `configure-aws-credentials` actions.
`.github/scripts/test_reusable_workflows.py` fails when any of that, or the
job graph and permissions above, changes.

Each token job runs `scripts/install-awscli.sh` before it assumes the role.
It uses the runner's own `aws` only when the `aws` on `PATH` is
`/usr/bin/aws` or `/usr/local/bin/aws` and that directory, the file it
resolves to and that file's directory are all owned by root (uid 0) and
neither group- nor world-writable, so the job's user could not have put it
there. Any other `aws` is never run: the script says it is not used and
installs AWS CLI 2.39.4 under `$RUNNER_TEMP` (no sudo), first on `PATH` for
the later steps: it downloads AWS's per-architecture zip and its `.sig`, and runs
nothing from it until the zip matches the sha256 committed in the script and
`gpgv` finds a valid signature by the AWS CLI key, fingerprint
`FB5D B77F D5C1 18B8 0511 ADA8 A631 0ACC 4672 475C`, committed as
`scripts/awscli-public-key.asc` from AWS's install guide. It needs `curl`,
`sha256sum`, `gpgv`, `base64` and `unzip` on the runner, and says which is
missing. Moving to another CLI version means re-resolving the version, both
digests and the key from AWS and updating `scripts/tests/install-awscli.test.sh`,
which fails on any other values.

The split assumes each job starts on a fresh runner, as the fleet's
ephemeral pools and GitHub's hosted runners give. On a runner reused between
jobs, `build`, which runs the site's code, could leave files for a later
token job to run; the `aws` rule above closes that path for the one tool the
token jobs would otherwise take from the runner as they found it, and nothing
else does, so never pass a persistent runner as `runner`.

The three jobs each take a runner from `runner` (or the caller's
`CI_RUNNER_SMALL`), so a deploy now holds three runner slots in turn rather
than one. Their names, `image-cache`, `build` and `deploy`, are stable: a
caller's required check or concurrency rule may name them.

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
