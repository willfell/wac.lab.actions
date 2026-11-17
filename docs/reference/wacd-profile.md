# The wacd profile

`wac-ci` and `wac-cd` are two reusable workflows that replace an app's own
deploy pipeline. [`wac-ci`](wac-ci.md) runs on pull requests;
[`wac-cd`](wac-cd.md) runs on a merge to `main` and releases through Lab's
gateway. Both are driven by one file in the app repo,
`.github/wacd.json`, so the app holds its profile, a values file for the
shared `wac-app` chart, a Dockerfile per image, and two short caller
workflows. It holds no `deploy/k8s`, no `Chart.yaml` and no templates.

Both workflows reach their scripts by checking this repo out at
`job.workflow_sha`, the commit the caller pinned, exactly as the `techdocs`
and `agents-md` gates do. `actions/checkout` only writes under the workspace,
so every job checks it out to `.wacd` and its next step moves it to
`$RUNNER_TEMP/wacd`, before anything else runs. Left in place it would be
linted, tested and globbed by the app's `ci.commands`, and sent in a `.`
docker build context, where `COPY . .` would bake it into the image. Both
default `runner` to the caller's `CI_RUNNER_SMALL`. Both pull from the
in-cluster registry, so they run on Lab runners only.

## The profile

`scripts/wacd-profile.py` validates `.github/wacd.json` before anything else
runs, in both workflows. Every field is required unless marked optional, and an
unknown field fails at every level, so a typo is an error naming its dotted
path (`images[1].sha_build_arg`) rather than a setting that silently does
nothing.

```json
{
  "schema_version": 1,
  "app": "travel",
  "chart_version": "1.0.0",
  "values_file": "deploy/values.yaml",
  "tag_prefix": "v",
  "health_url": "http://travel-app.travel.svc.cluster.local:3000/api/health",
  "images": [
    {"name": "travel-app", "dockerfile": "Dockerfile", "context": ".", "sha_build_arg": "TRAVEL_BUILD_SHA"}
  ],
  "ci": {"stack": "node", "node_version": "22", "commands": ["npm run lint", "npm run typecheck", "npm test", "npm run build"]}
}
```

| Field | Rule |
| --- | --- |
| `schema_version` | the integer `1` exactly; `true` is not `1` |
| `app` | `^[a-z][a-z0-9-]{0,39}$`; the deploy chart's name, which must equal the Application name Lab registered |
| `chart_version` | `X.Y.Z` of the `wac-app` chart to vendor |
| `values_file` | a repo-relative path with no `..` segment and no leading `/`; the file must exist |
| `tag_prefix` | empty, or a lowercase prefix such as `v` or `app-v` |
| `health_url` | an in-cluster `http://<host>.svc.cluster.local[:port]/<path>` URL |
| `health_expect_db_ok` | optional JSON boolean, default `true`; `false` checks only the served `sha` |
| `images` | a non-empty list of `{name, dockerfile, context, sha_build_arg?}`; `name` matches `^[a-z0-9][a-z0-9._-]{0,63}$` and is unique; `dockerfile` and `context` are repo-relative paths as above; optional `sha_build_arg` is an upper-case build arg name that receives the source SHA |
| `ci` | `{stack, node_version?, go_version?, go_version_file?, commands, apt_packages?}`; `stack` is `node` or `go`; the matching version (`22`, `1.24`) is required and the other stack's is refused; `commands` is a non-empty list of single-line commands |
| `ci.go_version_file` | optional, `go` only, in place of `go_version`: a repo-relative file such as `go.mod`, `go.work`, `.go-version` or `.tool-versions` that setup-go reads the Go version from, so the profile does not repeat a version the repo already pins; it must exist, and setting it together with `go_version` fails |
| `ci.apt_packages` | optional; a non-empty list of unique Debian package names, each matching `^[a-z0-9][a-z0-9.+-]*$`, that `wac-ci`'s `checks` job installs before anything else; omit it rather than writing `[]` |

The values file sets `name`, `image` and everything else the `wac-app` chart
takes. It never sets `app`, `images` or `global`: `wac-cd` writes `app` from the
profile and `images` from the build, and the packager refuses a values file
that tries, because a value it could override is a value two places would
disagree about.
