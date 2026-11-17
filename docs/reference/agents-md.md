# agents-md

Gates the calling repo's `AGENTS.md`, the one repo context file every fleet repo
carries for Claude Code and Codex. The file is hand-written prose on top and a
generated tail underneath, and the gate fails the moment the file and the repo
disagree: a stale context file is worse than none, because an agent trusts it.

```yaml
jobs:
  agents-md:
    uses: willfell/wac.lab.actions/.github/workflows/agents-md.yml@vX.Y.Z
    with:
      runner: ${{ vars.CI_RUNNER_SMALL }}
    permissions:
      contents: read
```

| Input | Meaning | Default |
| --- | --- | --- |
| `runner` | `runs-on` label the job is sent to, in the caller's repo | empty: the caller's `CI_RUNNER_SMALL` |

The job checks this repo out a second time at `job.workflow_sha`, into
`.agents-md-gate`, exactly as `techdocs` does (see [the shared checkout](../architecture/shared-checkout.md)), and runs
`uv run .agents-md-gate/scripts/agents-md.py check`. The script declares its
own Python and PyYAML pin inline (PEP 723), so `uv run` builds its environment
from that block and ignores any project in the calling repo. `check` reads the
working tree and asks git which paths are tracked and which are ignored; it
makes no network calls of its own.

It fails, with a `::error file=...,line=...::` annotation for each finding, on:

1. A tracked `CLAUDE.md` or `CLAUDE.local.md` at any depth. Claude Code reads
   `AGENTS.md` only when no such file exists in the working directory or above
   it, so one tracked CLAUDE.md silently turns the whole file off. Untracked
   files, such as old branches under `.worktrees/`, are ignored.
2. A root `AGENTS.md` that is missing, a symlink, or not UTF-8; one that
   starts with a byte-order mark or has CRLF line endings (each named as such);
   or a `catalog-info.yaml` that is missing, does not parse, has a malformed
   entity (`metadata` or `spec` not a mapping, no `metadata.name`, a relation
   that is not a list of string refs), or declares no Component at all.
3. A broken section contract: the first non-blank line must be the only H1,
   then the H2s `The one thing to understand`, `Commands`, `Layout`,
   `Invariants`, `Boundaries`, `Dependencies` and `Fleet rules` must all be
   present, in that relative order. A repo may add its own H2s anywhere before
   `## Dependencies`; no H1 or H2 other than the generated `## Fleet rules` may
   follow it. Headings are read outside fenced code blocks only, so a `# note`
   line in a command block is not an H1, and a code fence left unclosed is a
   finding of its own. Setext headings (a line underlined with `===` or
   `---`) count, since GitHub renders them as headings.
4. A file that differs from what `render` would write, with a unified diff.
   Everything from `## Dependencies` to the end of the file is generated from
   the repo's `catalog-info.yaml` and from `agents-md/fleet-rules.md` here; a
   hand edit there fails.
5. A `## Commands` section with no fenced shell block holding a command, or a
   command there that does not resolve. Only fences tagged `sh`, `bash`,
   `shell`, `zsh` or `console`, or not tagged at all, are read; in a `console`
   block only `$ ` prompt lines are commands. Each line is read as a command run
   from the repo root: a trailing `# note` is dropped, a line holding a
   `<placeholder>` is skipped, segments split on `&&`, `||` and `;`, and `cd`,
   `pushd` and `popd` are followed. Then:
   - `npm run <x>`, `npm test|start|stop|restart` and `npm --prefix <dir> ...`
     must name a script in that directory's `package.json` (`npm start` also
     passes on a `server.js`, npm's own fallback);
   - `make <target>` (and `make -C <dir> <target>`) must name a rule in that
     directory's Makefile;
   - `bash|sh|node|python|python3 <path>`, `node --test <path>...`,
     `uv run <path>` and a bare `./<path>` or `scripts/<path>` must name a path
     that exists.

   "Exists" means **tracked by git**, not present on the local disk, so a file
   that exists only on one machine cannot make the check pass there. A path
   git does not track but does ignore (`dist/`, `node_modules/`) is a build
   output and is left unchecked, as is a path that leaves the repo, an
   absolute path, or one built from a shell variable. A path is matched
   literally first, so a tracked `app/[id]/route.test.mjs` resolves as written;
   only a path that misses and holds `*`, `?` or `[` is read as a glob, which
   must then match at least one tracked file. Anything else (`tofu`, `npx`,
   `kubectl`, `npm install`) is not checked: recognised forms fail closed,
   unrecognised ones pass.

   Ignore rules come from the repo's `.gitignore` files and from the clone's
   own `.git/info/exclude`. The user's global excludes file
   (`core.excludesFile`) is switched off, but `.git/info/exclude` is not, so a
   path ignored only there passes locally and fails on a fresh CI checkout.
   Put build outputs in a committed `.gitignore`.

There is no detect step. Unlike `techdocs`, a fleet repo without the file is a
failure rather than a skip, so add the job in the same PR as the `AGENTS.md`.

**Render from the tag you pin.** The fleet rules are part of the tail, and they
change between tags. A tail rendered from `main`, or from whatever a local
checkout happens to be on, is rejected by a gate pinned elsewhere. From the
repo being rendered:

```sh
git -C <wac.lab.actions-checkout> worktree add --detach <gate-dir> <pinned-tag>
uv run <gate-dir>/scripts/agents-md.py render
uv run <gate-dir>/scripts/agents-md.py check
```

`render` rewrites everything from the first `## Dependencies` to the end of the
file, or appends the tail if there is none yet. The prose above it is kept as
written, with three normalisations: a UTF-8 byte-order mark is dropped, CRLF
line endings become LF, and trailing blank lines become exactly one before
`## Dependencies`. Running it twice changes nothing.

What `render` overwrites, exactly: a tail it generated, which it recognises by
the generated preamble line directly under `## Dependencies`. Everything in
that tail is replaced, including any hand edit made inside it; `check` fails on
such an edit, and that failure is the only warning before a render discards
it. A `## Dependencies` with anything else under it was written by hand, and
render will not touch it.

So `render` writes nothing and exits non-zero when:

- `## Dependencies` exists but the line under it is not the generated
  preamble, and anything other than a bare `## Fleet rules` heading follows it
  (bare `## Dependencies` and `## Fleet rules` headings, an outline waiting for
  its first render, are filled in);
- an H1 or H2 other than the generated `## Fleet rules` sits below
  `## Dependencies` (it names each one to move above it);
- a code fence is left unclosed;
- `AGENTS.md` is a symlink, or the catalog has any problem listed above;
- its own output would not survive a second render (a catalog description
  that reads as a heading or a code fence).

`catalog-info.yaml` is read as YAML 1.2 reads booleans, the way Backstage
reads it: only `true` and `false` are booleans, so `name: on` is the
Component `on`, not `True`. A name YAML reads as a number (`name: 123`) is a
finding that says to quote it. Both subcommands take `--root PATH` in place of
the current directory.

The consequence is deliberate: bumping a repo's pin to a tag that changed
`agents-md/fleet-rules.md` fails its gate until the bump PR re-renders, so the
new rules ride the bump and cannot go stale silently. A `CHANGELOG.md` row for
such a tag says so.
