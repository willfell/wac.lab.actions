# The wacd profile

`wac-ci` and `wac-cd` are two reusable workflows that replace an app's own
deploy pipeline. [`wac-ci`](wac-ci.md) runs on pull requests;
[`wac-cd`](wac-cd.md) runs on a merge to `main` and releases through Lab's
gateway. Both are driven by one profile. Until an app is cut over to hemi the
profile is a file in the app repo, `.github/wacd.json`, so the app holds its
profile, a values file for the shared `wac-app` chart, a Dockerfile per image,
and two short caller workflows. It holds no `deploy/k8s`, no `Chart.yaml` and
no templates. Once the app is cut over, hemi's clean-up pull request deletes
`.github/wacd.json` and the values file from `main`: the app holds the
Dockerfiles and the two caller workflows, and both workflows derive the same
profile from hemi's copy (**The copy hemi stores**, below).

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
runs, in both workflows, for an app on its files; for a cut-over app it
validates the profile derived from hemi's copy in the same way. Every field is required unless marked optional, and an
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
| `ci.docs_paths` | optional, from `v1.36.0`; a list of repo-relative patterns naming the documentation, which a pull request may change without `checks` running and a push may change without a release (**Documentation paths**, below). Absent is `["docs/**", "mkdocs.yml", "*.md"]`; `[]` means no change ever skips |

The values file sets `name`, `image` and everything else the `wac-app` chart
takes. It never sets `app`, `images` or `global`: `wac-cd` writes `app` from the
profile and `images` from the build, and the packager refuses a values file
that tries, because a value it could override is a value two places would
disagree about.

## The copy hemi stores

Once an app is cut over, its settings live in hemi, and `wac-cd`'s `plan`
reads them from the gateway's copy rather than from the repository
([wac-cd](wac-cd.md), **Settings**). The copy is hemi's contract: the same
settings under other names. `scripts/wacd-profile.py --contract <path>`
derives the profile it stands for and prints exactly the lines the profile
file it came from prints, so every later step reads one set of outputs
whichever the source:

| Profile field | Copy field |
| --- | --- |
| `app` | `identity.app` |
| `chart_version`, `tag_prefix`, `health_url`, `health_expect_db_ok` | `delivery.chartVersion`, `delivery.tagPrefix`, `delivery.healthUrl`, `delivery.healthExpectDbOk` |
| `values_file` | `delivery.valuesFile`, the path the repository's values file had; the file need not exist |
| `images[].name`, `dockerfile`, `context`, `sha_build_arg` | `build.images[].name`, `dockerfile`, `context`, `shaBuildArg` |
| `ci.stack`, `node_version`, `go_version`, `go_version_file`, `apt_packages` | `build.stack`, `nodeVersion`, `goVersion`, `goVersionFile`, `aptPackages` (left out when it is `[]` or absent; any other value that is not a non-empty list of names, `0`, `null` and `""` included, is refused as `ci.apt_packages: must be a non-empty list`) |
| `ci.commands` | every gate's `commands`, in order |
| `ci.docs_paths` | `delivery.docsPaths`, as it is: absent stays absent, `[]` stays `[]`, and any other value (`null` included) goes on to the check |
| the values file | `runtime` |

The derived profile is validated exactly like the file. An unknown field
inside `build` or one of its images fails, naming its dotted path under
`contract.`, rather than being dropped, so a setting the copy carries never
silently does nothing. Line equality holds for a profile whose `ci` keys are
written in the order the table above lists them; the JSON outputs are read
with `fromJSON`, so key order does not change what a job sees.

## Documentation paths

`ci.docs_paths` (hemi's `delivery.docsPaths`) names the paths that are
documentation and nothing else: `wac-ci`'s `scope` job skips `checks` for a
pull request that changes only those ([wac-ci](wac-ci.md), **The scope job**),
and `wac-cd`'s `plan` releases nothing for a push that changes only those
([wac-cd](wac-cd.md), **Documentation-only merges**). One module,
`scripts/wacd-docs.py`, holds the grammar and the rules for `wacd-profile.py`,
the scope step and `plan`.

Absent means `["docs/**", "mkdocs.yml", "*.md"]`. `[]` means never skip. The
two stay apart everywhere: the `ci` output keeps the key as written (absent,
or `[]`), and a separate `docs_paths` line prints the effective list as
compact JSON, the default when the key is absent.

**The grammar is not fnmatch.** A pattern and a path are split on `/`. `*`
matches any characters within one segment, none included, and never `/`.
`**` as a whole segment matches zero or more segments, so `docs/**` matches
`docs` itself and `**/*.md` matches `a.md`; `**` inside a segment is refused.
No other character is special, and only letters, digits and `. _ - / *` are
allowed, so `?`, `[`, `{` and `!` are refused rather than read as fnmatch or
a gitignore would. Matching is case-sensitive: `docs/**` does not match
`Docs/a.md`, nor `*.md` `README.MD`.

**A pattern is accepted only when every path it can match is documentation
by name**, one of:

- its first segment is literally `docs`, `doc` or `documentation`, whatever
  follows: `docs/**`, `doc/api/*`;
- an exact path, with no `*`, that is `mkdocs.yml` at the root or ends in
  `.md`, `.mdx`, `.rst` or `.adoc`: `README.md`, `guides/intro.rst`;
- a last segment `*.md`, `*.mdx`, `*.rst` or `*.adoc` below segments
  that are literal or `**`: `*.md`, `**/*.md`, `guides/*.adoc`.

Everything else is refused, `**`, `*`, `src/**`, `*.json`, `*.MD` and
`guides*/*.md` included. `.txt` is not a documentation extension, so
`*.txt`, `**/*.txt` and `requirements.txt` are refused too: a `.txt` file is
as often a dependency list (`requirements.txt`), a build file
(`CMakeLists.txt`) or content the app serves (`public/robots.txt`). It is also refused when it matches a path the checks
or the release read, in this order: `.github/wacd.json`; the values file;
each image's `dockerfile`; `package.json`, `package-lock.json`, `go.mod` and
`go.sum` at the root and in each image's `context`; and every path-like token
of `ci.commands` (hemi's gates' commands): a word, split at whitespace and
`; | & ( ) < >`, that holds `/` or `.` once its quotes are gone and a leading
`./` or trailing `/` is dropped, since a gate that names a path reads it. The
default is checked the same way, so a profile whose gates name `README.md`,
`mkdocs.yml` or a file under `docs/` sets `ci.docs_paths` itself.

A pattern may still match a file that something reads without naming it in
a gate: wealth's tests read `docs/` and `mkdocs.yml` by a path built in code.
Such a repository sets `ci.docs_paths` to the paths nothing reads, or `[]`;
`CONSUMERS.md` lists what each consumer needs.

Each refusal is one sentence, `<field>: <message>`, where the field is
`ci.docs_paths` or `ci.docs_paths[<i>]` for a profile file and a copy alike
(a copy's `delivery.docsPaths` is checked as the `ci.docs_paths` it derives):

| Refused | Sentence |
| --- | --- |
| `null`, a string, an object, any non-list | `ci.docs_paths: must be a list of patterns, or [] for none` |
| an entry that is not a string, or empty | `ci.docs_paths[<i>]: must be a non-empty string` |
| a leading `/` or `./`, a `..` or empty segment, a trailing `/`, a backslash, or any character but letters, digits and `. _ - / *` | `ci.docs_paths[<i>]: '<pattern>' must be a path relative to the repository root, using only letters, digits and . _ - / *, with no empty, . or .. segment` |
| `**` inside a segment (`docs/**.md`, `***`) | `ci.docs_paths[<i>]: '<pattern>' may use ** only as a whole segment` |
| a pattern that could match code | `ci.docs_paths[<i>]: '<pattern>' could match a file that is not documentation; a pattern must start with docs/, doc/ or documentation/, name one .md, .mdx, .rst or .adoc file or mkdocs.yml, or end in *.md, *.mdx, *.rst or *.adoc below literal or ** segments` |
| a pattern that matches a protected path | `ci.docs_paths[<i>]: '<pattern>' matches <path>, which the checks or the release read, so it is not documentation` |
| a default pattern that matches a protected path | `ci.docs_paths: the default pattern '<pattern>' matches <path>, which the checks or the release read; set ci.docs_paths without it` |

The pattern is quoted as Python's `repr` writes it, so a line feed in one
reads `\n` and the sentence stays one line. Each entry is checked in turn
against the rows down to the one for code, then every entry against the
protected paths, so the first offence is the one named.

## The case table

`scripts/fixtures/wacd-profile-cases.json` holds the rules above as cases:
profiles and copies, each with the outcome `wacd-profile.py` gives it. It is
the contract hemi's own check mirrors. hemi's tests load it at the tag hemi
pins, from
`https://raw.githubusercontent.com/willfell/wac.lab.actions/<tag>/scripts/fixtures/wacd-profile-cases.json`
or a git fetch at that tag, and match each case by its `name` and outcome, so
hemi refuses what the pipeline refuses, with the same sentence.

```json
{"version": 1, "cases": [
  {"name": "profile-go-version-file-missing", "kind": "profile",
   "input": {"schema_version": 1, "...": "..."},
   "files": {"deploy/values.yaml": "..."},
   "expect": {"refused": "ci.go_version_file: go.mod does not exist in the repo"}}
]}
```

| Field | Meaning |
| --- | --- |
| `name` | unique kebab-case, and stable: hemi keys on it, so a case is never renamed; a new one is added instead |
| `kind` | `profile`, run as `wacd-profile.py .github/wacd.json`; `contract`, run as `wacd-profile.py --contract .wacd-contract/contract.json`; or `docs-path-match` (from `v1.36.0`), run as `wacd-docs.py match <pattern> <path>` |
| `input` | the profile, or the copy, as written to that path; for `docs-path-match`, `{"pattern": "<pattern>", "path": "<path>"}` |
| `files` | optional; the repository files the check reads, by repo-relative path: the values file for a profile, a `go_version_file` for either |
| `expect` | `{"ok": true}`, optionally with `"outputs": {"<key>": "<value>"}` for some of the lines printed, or `{"refused": "<sentence>"}`: exactly what the script prints after its `wacd profile: ` prefix. For `docs-path-match`, `{"match": true}` or `{"match": false}`, or `{"refused": "pattern: <message>"}` for a pattern outside the grammar, exactly what `wacd-docs.py` prints after its `wacd docs: ` prefix; `match` checks the grammar only, so the pattern rules above are `profile` and `contract` cases |

`version` changes only when the format does in a way a reader would break
on. A new case, or a new optional field a reader can ignore, is not one.

`scripts/tests/wacd-profile.test.py` runs every case through the real script
in a fresh temporary repository and fails on any difference. It also reads the
script's refusal calls and fails when one of them has no case whose sentence
it can print: a refusal only a copy reaches needs a `contract` case, every
other a `profile` case. It reads `wacd-docs.py`'s too: each documentation-path
refusal needs a `profile` and a `contract` case, and each grammar refusal a
`docs-path-match` case as well. So a new refusal lands with its case, in the same tag
hemi then pins. Two kinds of refusal are not in the table: a file that cannot
be read, is not UTF-8 or is not JSON (a case's input is a JSON value, and the
sentence names the file's path), and `node-layout`'s, which reads a checkout
rather than settings. The new app's first answer (a `files` answer with no
settings, [wac-ci](wac-ci.md)) is not a case either: `--contract` only ever
reads a copy whose source is `hemi`, and the script does not read `flags`, so
a full copy whose source is `files` is accepted as one whose source is `hemi`.
The table fixes which check refuses first only where a case breaks two rules.
