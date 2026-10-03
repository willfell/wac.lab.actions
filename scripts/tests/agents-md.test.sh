#!/usr/bin/env bash
# Scenario tests for agents-md.py, the fleet's AGENTS.md gate.
#
# Each case copies a fixture repo into a temp dir, makes it a git repo (the
# gate finds tracked CLAUDE.md files through `git ls-files`), and runs the
# script there through `uv run`, the same way agents-md.yml does.
#
# Fixtures carry only the hand-written prose and catalog-info.yaml. The
# generated tail depends on agents-md/fleet-rules.md, which is meant to
# change, so a fixture with a frozen tail would go stale on every rules edit.
# Instead each case calls `render` to produce a valid tail and then breaks
# exactly one thing.
#
# The prose is stored as AGENTS.prose.md and renamed in the temp dir, and no
# fixture carries a CLAUDE.md; the cases that need one create it. A tracked
# CLAUDE.md anywhere in this repo would fail this repo's own gate, and a
# nested AGENTS.md would be loaded by Claude Code as instructions for anyone
# reading the fixtures.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBJECT="$HERE/../agents-md.py"
FIXTURES="$HERE/fixtures/agents-md"
RULES="$HERE/../../agents-md/fleet-rules.md"

failures=0
workdir=""
repo=""

setup() {
  workdir="$(mktemp -d)"
  repo="$workdir/repo"
  cp -R "$FIXTURES/$1" "$repo"
  mv "$repo/AGENTS.prose.md" "$repo/AGENTS.md"
  git -C "$repo" init -q
  git -C "$repo" add -A
}

teardown() {
  [ -n "$workdir" ] && rm -rf "$workdir"
  workdir=""
}

# gate SUBCOMMAND: run the script from the fixture repo's root.
gate() {
  (cd "$repo" && uv run --quiet "$SUBJECT" "$@") >"$workdir/out" 2>&1
}

# rewrite AWK_PROGRAM [ARGS...]: apply an awk program to AGENTS.md in place.
rewrite() {
  local program="$1"
  shift
  awk "$@" "$program" "$repo/AGENTS.md" >"$workdir/agents.tmp"
  mv "$workdir/agents.tmp" "$repo/AGENTS.md"
}

# add_command LINE: insert LINE as the first line of the Commands block.
add_command() {
  rewrite '{ print } !done && $0 == "```sh" { print cmd; done = 1 }' -v cmd="$1"
}

# drop_line LINE: delete every line exactly equal to LINE.
drop_line() {
  rewrite '$0 != line { print }' -v line="$1"
}

check() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  ok: $label"
  else
    echo "  FAIL: $label -- expected $expected, got $actual"
    sed 's/^/      /' "$workdir/out"
    failures=$((failures + 1))
  fi
}

contains() {
  local label="$1" needle="$2"
  if grep -qF -- "$needle" "$workdir/out"; then
    echo "  ok: $label"
  else
    echo "  FAIL: $label -- output lacks '$needle'"
    sed 's/^/      /' "$workdir/out"
    failures=$((failures + 1))
  fi
}

lacks() {
  local label="$1" needle="$2"
  if grep -qF -- "$needle" "$workdir/out"; then
    echo "  FAIL: $label -- output has '$needle'"
    sed 's/^/      /' "$workdir/out"
    failures=$((failures + 1))
  else
    echo "  ok: $label"
  fi
}

same() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  ok: $label"
  else
    echo "  FAIL: $label"
    diff <(printf '%s\n' "$expected") <(printf '%s\n' "$actual") | sed 's/^/      /' || true
    failures=$((failures + 1))
  fi
}

echo "a rendered repo passes"
setup basic
gate render
status=0
gate check || status=$?
check "exits 0" 0 "$status"
contains "reports ok" "agents-md check: ok"
same "the fleet rules are copied verbatim" \
  "$(cat "$RULES")" "$(sed -n '/^## Fleet rules$/,$p' "$repo/AGENTS.md" | sed '1,2d')"
teardown

echo "render is idempotent"
setup basic
gate render
cp "$repo/AGENTS.md" "$workdir/first"
status=0
gate render || status=$?
check "exits 0" 0 "$status"
contains "says nothing changed" "already current"
if cmp -s "$workdir/first" "$repo/AGENTS.md"; then
  echo "  ok: second render is byte-identical"
else
  echo "  FAIL: second render changed the file"
  diff "$workdir/first" "$repo/AGENTS.md" | sed 's/^/      /' || true
  failures=$((failures + 1))
fi
teardown

echo "render appends a tail when there is none, and keeps the prose"
setup basic
status=0
gate render || status=$?
check "exits 0" 0 "$status"
prose="$(cat "$FIXTURES/basic/AGENTS.prose.md")"
rendered="$(cat "$repo/AGENTS.md")"
if [[ "$rendered" == "$prose"* ]]; then
  echo "  ok: the prose head is unchanged"
else
  echo "  FAIL: the prose head changed"
  failures=$((failures + 1))
fi
cp "$repo/AGENTS.md" "$workdir/out"
contains "appends Dependencies" "## Dependencies"
contains "renders the component" "### Component \`fixture-basic\`"
contains "renders the catalog facts" "A fixture repo for the agents-md gate. System \`platform\`, type \`service\`, lifecycle \`experimental\`."
contains "renders the catalog link" "https://docs.wacwini.com/catalog/default/component/fixture-basic"
contains "renders empty lists as (none)" "- Provides APIs: (none)"
contains "appends Fleet rules" "## Fleet rules"
teardown

echo "render replaces only the tail"
setup basic
prose="$(cat "$repo/AGENTS.md")"
gate render
printf '    - resource:ecosystem-postgres\n' >>"$repo/catalog-info.yaml"
gate render
cp "$repo/AGENTS.md" "$workdir/out"
contains "picks up the catalog change" "- Depends on: \`resource:ecosystem-postgres\`, \`resource:traefik\`"
rendered="$(cat "$repo/AGENTS.md")"
if [[ "$rendered" == "$prose"* ]] && [ "$(grep -c '^## Dependencies$' "$repo/AGENTS.md")" = 1 ]; then
  echo "  ok: one tail, prose intact"
else
  echo "  FAIL: re-render duplicated the tail or touched the prose"
  failures=$((failures + 1))
fi
teardown

echo "a multi-Component catalog renders one block per Component, in file order"
setup multi
gate render
status=0
gate check || status=$?
check "exits 0" 0 "$status"
same "the Dependencies section matches expected-dependencies.md" \
  "$(cat "$FIXTURES/multi/expected-dependencies.md")" \
  "$(sed -n '/^## Dependencies$/,/^## Fleet rules$/p' "$repo/AGENTS.md" | sed '$d')"
teardown

echo "no AGENTS.md fails"
setup basic
rm "$repo/AGENTS.md"
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "names the missing file" "no AGENTS.md at the repo root"
teardown

echo "no catalog-info.yaml fails"
setup basic
gate render
rm "$repo/catalog-info.yaml"
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "names the missing catalog" "::error file=catalog-info.yaml::no \`catalog-info.yaml\`"
teardown

echo "a tracked root CLAUDE.md fails"
setup basic
gate render
echo "# old context" >"$repo/CLAUDE.md"
git -C "$repo" add CLAUDE.md
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "annotates the file" "::error file=CLAUDE.md::tracked \`CLAUDE.md\`"
teardown

echo "a tracked nested CLAUDE.md fails"
setup basic
gate render
mkdir -p "$repo/app/.claude"
echo "# old context" >"$repo/app/CLAUDE.md"
echo "# old context" >"$repo/app/.claude/CLAUDE.md"
git -C "$repo" add app
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "annotates app/CLAUDE.md" "::error file=app/CLAUDE.md::"
contains "annotates app/.claude/CLAUDE.md" "::error file=app/.claude/CLAUDE.md::"
teardown

echo "an untracked CLAUDE.md is ignored"
setup basic
gate render
mkdir -p "$repo/.worktrees/old-branch"
echo "# old context" >"$repo/.worktrees/old-branch/CLAUDE.md"
echo "# local only" >"$repo/CLAUDE.md"
status=0
gate check || status=$?
check "exits 0" 0 "$status"
lacks "says nothing about CLAUDE.md" "file=CLAUDE.md"
teardown

echo "a tracked CLAUDE.local.md fails"
setup basic
gate render
echo "# local context" >"$repo/CLAUDE.local.md"
git -C "$repo" add CLAUDE.local.md
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "annotates the file" "::error file=CLAUDE.local.md::"
teardown

echo "a missing required section fails"
setup basic
gate render
drop_line "## Boundaries"
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "names the section" "missing required section \`## Boundaries\`"
teardown

echo "required sections out of order fail"
setup basic
gate render
rewrite '$0 == "## Layout" { print "## Invariants"; next } $0 == "## Invariants" { print "## Layout"; next } { print }'
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "says out of order" "\`## Layout\` is out of order"
teardown

echo "a second H1 fails"
setup basic
gate render
rewrite '$0 == "## Layout" { print "# Another title"; print "" } { print }'
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "names the extra H1" "extra H1 \`# Another title\`"
teardown

echo "a section after Fleet rules fails"
setup basic
gate render
printf '\n## Notes\n\nHand-written, after the generated tail.\n' >>"$repo/AGENTS.md"
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "names the section" "\`## Notes\` comes after \`## Fleet rules\`"
teardown

echo "stale dependencies fail until re-rendered"
setup basic
gate render
printf '    - resource:ecosystem-postgres\n' >>"$repo/catalog-info.yaml"
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "says the file is not what render writes" "AGENTS.md is not what \`render\` writes"
contains "diffs the new dependency in" "+- Depends on: \`resource:ecosystem-postgres\`, \`resource:traefik\`"
gate render
status=0
gate check || status=$?
check "re-render makes it pass" 0 "$status"
teardown

echo "hand-edited fleet rules fail"
setup basic
gate render
printf '7. A hand-written rule.\n' >>"$repo/AGENTS.md"
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "says the file is not what render writes" "AGENTS.md is not what \`render\` writes"
contains "diffs the hand edit out" "-7. A hand-written rule."
teardown

echo "an unresolved npm run fails"
setup basic
gate render
add_command "npm run nope"
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "names the script" "\`package.json\` has no \`nope\` script"
teardown

echo "cd app && npm test resolves inside app/, not at the root"
setup basic
gate render
add_command "cd app && npm test && npm start"
status=0
gate check || status=$?
check "exits 0" 0 "$status"
teardown
setup basic
gate render
add_command "npm test"
status=0
gate check || status=$?
check "the same script at the root exits 1" 1 "$status"
contains "names the root package.json" "\`package.json\` has no \`test\` script"
teardown

echo "cd into a missing directory fails"
setup basic
gate render
add_command "cd missing && npm test"
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "names the directory" "there is no directory \`missing\` in the repo"
teardown

echo "a line with a placeholder is skipped"
setup basic
gate render
add_command "npm run <script-name>"
add_command "cd <stack> && make <target>"
status=0
gate check || status=$?
check "exits 0" 0 "$status"
teardown

echo "an unknown command passes"
setup basic
gate render
add_command "kubectl get pods -A"
add_command "tofu init && tofu plan"
add_command "npm ci"
status=0
gate check || status=$?
check "exits 0" 0 "$status"
teardown

echo "make resolves targets present in the Makefile and fails on absent ones"
setup basic
gate render
add_command "make build test"
status=0
gate check || status=$?
check "present targets exit 0" 0 "$status"
teardown
setup basic
gate render
add_command "make deploy"
status=0
gate check || status=$?
check "an absent target exits 1" 1 "$status"
contains "names the target" "\`Makefile\` has no rule for \`deploy\`"
teardown

echo "every recognised form resolves when its file exists"
setup basic
gate render
add_command "./scripts/hello.sh"
add_command "scripts/hello.sh --verbose"
add_command "python3 scripts/hello.py"
add_command "uv run scripts/hello.py"
add_command "uv run --with pyyaml python scripts/hello.py"
add_command "node app/server.js"
add_command "npm --prefix app test"
add_command "python3 -m http.server"
add_command "bash -c 'echo inline'"
status=0
gate check || status=$?
check "exits 0" 0 "$status"
teardown

echo "every recognised form fails closed when its file is missing"
setup basic
gate render
add_command "bash scripts/missing.sh"
add_command "./scripts/missing.sh"
add_command "uv run scripts/missing.py"
add_command "uv run --with pyyaml python scripts/gone.py"
add_command "node app/missing.js"
add_command "npm --prefix app run nope"
status=0
gate check || status=$?
check "exits 1" 1 "$status"
contains "bash path" "\`bash scripts/missing.sh\`: there is no \`scripts/missing.sh\`"
contains "bare ./ path" "\`./scripts/missing.sh\`: there is no \`scripts/missing.sh\`"
contains "uv run path" "\`uv run scripts/missing.py\`: there is no \`scripts/missing.py\`"
contains "uv run interpreter path" "there is no \`scripts/gone.py\`"
contains "node path" "there is no \`app/missing.js\`"
contains "npm --prefix" "\`app/package.json\` has no \`nope\` script"
teardown

echo "a # line inside the Commands fence is not an H1"
setup basic
gate render
add_command "# a note about the commands, not a heading"
status=0
gate check || status=$?
check "exits 0" 0 "$status"
lacks "reports no extra H1" "extra H1"
teardown

if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed"
  exit 1
fi
echo "all scenarios passed"
