#!/usr/bin/env bash
# Scenario tests for agents-md.py, the fleet's AGENTS.md gate.
#
# Each case copies a fixture repo into a temp dir, makes it a git repo (the
# gate resolves paths and finds CLAUDE.md files through what git tracks and
# ignores), and runs the script there through `uv run`, the same way
# agents-md.yml does.
#
# Fixtures carry only the hand-written prose and catalog-info.yaml. The
# generated tail depends on agents-md/fleet-rules.md, which is meant to
# change, so a fixture with a frozen tail would go stale on every rules edit.
# Instead each case calls `render` to produce a valid tail and then breaks
# exactly one thing.
#
# The prose is stored as AGENTS.prose.md and renamed in the temp dir, and no
# fixture carries a CLAUDE.md or a .gitignore; the cases that need one create
# it. A tracked CLAUDE.md anywhere in this repo would fail this repo's own
# gate, a nested AGENTS.md would be loaded by Claude Code as instructions for
# anyone reading the fixtures, and a fixture .gitignore would apply to this
# repo too.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBJECT="$HERE/../agents-md.py"
FIXTURES="$HERE/fixtures/agents-md"
RULES_DIR="$HERE/../../agents-md"
RULES="$RULES_DIR/fleet-rules.md"

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

# gate SUBCOMMAND: run the script from the fixture repo's root. GATE_SCRIPT
# points it at another copy of the script, with its own fleet rules.
gate() {
  (cd "$repo" && uv run --quiet "${GATE_SCRIPT:-$SUBJECT}" "$@") >"$workdir/out" 2>&1
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

# insert_before HEADING LINE...: insert lines just above a heading line. The
# block goes through the environment, since awk -v rejects a newline.
insert_before() {
  local heading="$1"
  shift
  local block
  block="$(printf '%s\n' "$@"; printf '.')"
  HEADING="$heading" BLOCK="${block%.}" \
    awk '$0 == ENVIRON["HEADING"] { printf "%s", ENVIRON["BLOCK"] } { print }' \
    "$repo/AGENTS.md" >"$workdir/agents.tmp"
  mv "$workdir/agents.tmp" "$repo/AGENTS.md"
}

# drop_line LINE: delete every line exactly equal to LINE.
drop_line() {
  rewrite '$0 != line { print }' -v line="$1"
}

# rewrite_catalog_description TEXT: set the fixture Component's description.
rewrite_catalog_description() {
  awk -v d="$1" '/^  description:/ { print "  description: \"" d "\""; next } { print }' \
    "$repo/catalog-info.yaml" >"$workdir/catalog.tmp"
  mv "$workdir/catalog.tmp" "$repo/catalog-info.yaml"
}

snapshot() {
  cp "$repo/AGENTS.md" "$workdir/before"
}

unchanged() {
  local label="$1" file="${2:-$repo/AGENTS.md}"
  if cmp -s "$workdir/before" "$file"; then
    echo "  ok: $label"
  else
    echo "  FAIL: $label -- the file changed"
    diff "$workdir/before" "$file" | sed 's/^/      /' || true
    failures=$((failures + 1))
  fi
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

# rendered: render, as most cases do to get a valid tail before breaking one
# thing. A render that fails here is reported, not left to abort the run.
rendered() {
  local status=0
  gate render || status=$?
  if [ "$status" -ne 0 ]; then
    echo "  FAIL: render exited $status"
    sed 's/^/      /' "$workdir/out"
    failures=$((failures + 1))
  fi
}

# passes / fails: run check and assert its exit code.
passes() {
  local status=0
  gate check || status=$?
  check "${1:-check exits 0}" 0 "$status"
}

fails() {
  local status=0
  gate check || status=$?
  check "${1:-check exits 1}" 1 "$status"
}

# render_refuses: render exits 2, says it refused, and leaves AGENTS.md as it
# was. Takes no arguments.
render_refuses() {
  local status=0
  snapshot
  gate render || status=$?
  check "render exits 2" 2 "$status"
  contains "render says it refused" "refusing to write AGENTS.md"
  unchanged "render wrote nothing" "$repo/AGENTS.md"
}

# --------------------------------------------------------------------------
echo "a rendered repo passes"
setup basic
rendered
passes
contains "reports ok" "agents-md check: ok"
same "the fleet rules are copied verbatim" \
  "$(cat "$RULES")" "$(sed -n '/^## Fleet rules$/,$p' "$repo/AGENTS.md" | sed '1,2d')"
teardown

echo "render is idempotent"
setup basic
rendered
snapshot
status=0
gate render || status=$?
check "exits 0" 0 "$status"
contains "says nothing changed" "already current"
unchanged "second render is byte-identical"
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
rendered
printf '    - resource:ecosystem-postgres\n' >>"$repo/catalog-info.yaml"
rendered
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
rendered
passes
same "the Dependencies section matches expected-dependencies.md" \
  "$(cat "$FIXTURES/multi/expected-dependencies.md")" \
  "$(sed -n '/^## Dependencies$/,/^## Fleet rules$/p' "$repo/AGENTS.md" | sed '$d')"
teardown

# --------------------------------------------------------------------------
echo "no AGENTS.md fails"
setup basic
rm "$repo/AGENTS.md"
fails
contains "names the missing file" "no AGENTS.md at the repo root"
teardown

echo "a symlinked AGENTS.md fails, and render will not write through it"
setup basic
rendered
mv "$repo/AGENTS.md" "$repo/real.md"
ln -s real.md "$repo/AGENTS.md"
fails
contains "names the symlink" "::error file=AGENTS.md::AGENTS.md is a symlink"
cp "$repo/real.md" "$workdir/before"
status=0
gate render || status=$?
check "render exits 2" 2 "$status"
contains "render names the symlink" "AGENTS.md is a symlink"
unchanged "the link target is untouched" "$repo/real.md"
teardown

echo "no catalog-info.yaml fails"
setup basic
rendered
rm "$repo/catalog-info.yaml"
fails
contains "names the missing catalog" "::error file=catalog-info.yaml::no \`catalog-info.yaml\`"
teardown

for shape in empty separators resource-only; do
  echo "a catalog with no Component fails, and render refuses it: $shape"
  setup basic
  rendered
  case "$shape" in
    empty) : >"$repo/catalog-info.yaml" ;;
    separators) printf -- '---\n---\n' >"$repo/catalog-info.yaml" ;;
    resource-only)
      cat >"$repo/catalog-info.yaml" <<'EOF'
apiVersion: backstage.io/v1alpha1
kind: Resource
metadata:
  name: traefik
spec:
  type: ingress
  owner: group:default/owners
EOF
      ;;
  esac
  fails
  contains "says there is no Component" "::error file=catalog-info.yaml::\`catalog-info.yaml\` declares no Component"
  render_refuses
  teardown
done

# malformed LABEL NEEDLE <<CATALOG: a catalog shape that must be a finding in
# check and a refusal in render, never a traceback from either.
malformed() {
  local label="$1" needle="$2"
  echo "a malformed catalog is a finding, not a traceback: $label"
  setup basic
  rendered
  cat >"$repo/catalog-info.yaml"
  fails
  contains "annotates the catalog" "::error file=catalog-info.yaml::"
  contains "names the problem" "$needle"
  lacks "check raised no traceback" "Traceback"
  render_refuses
  lacks "render raised no traceback" "Traceback"
  teardown
}

malformed "a document that is not a mapping" "document 1 is not a mapping" <<'EOF'
- just
- a list
EOF

malformed "metadata that is not a mapping" "\`metadata\` is missing or not a mapping" <<'EOF'
kind: Component
metadata: [fixture-basic]
spec:
  type: service
EOF

malformed "no metadata.name" "\`metadata.name\` is missing or not a string" <<'EOF'
kind: Component
metadata:
  description: nameless
spec:
  type: service
EOF

malformed "spec that is not a mapping" "\`component:fixture-basic\`: \`spec\` is not a mapping" <<'EOF'
kind: Component
metadata:
  name: fixture-basic
spec: just-a-string
EOF

malformed "a relation that is not a list" "\`spec.dependsOn\` is not a list" <<'EOF'
kind: Component
metadata:
  name: fixture-basic
spec:
  type: service
  dependsOn: resource:traefik
EOF

malformed "a ref that is not a string" "\`spec.dependsOn\` item 1 is not a string ref" <<'EOF'
kind: Component
metadata:
  name: fixture-basic
spec:
  type: service
  dependsOn:
    - {a: b}
EOF

malformed "a description that is not a string" "\`metadata.description\` is not a string" <<'EOF'
kind: Component
metadata:
  name: fixture-basic
  description: [not, prose]
spec:
  type: service
EOF

malformed "YAML that does not parse" "does not parse" <<'EOF'
kind: Component
metadata: {name: [unclosed
EOF

malformed "a name YAML reads as a number" "\`metadata.name\` is 123, which YAML reads as int, not a string; quote it" <<'EOF'
kind: Component
metadata:
  name: 123
spec:
  type: service
EOF

echo "YAML 1.1 boolean words stay strings, as Backstage's YAML 1.2 parser reads them"
setup basic
cat >"$repo/catalog-info.yaml" <<'EOF'
kind: Component
metadata:
  name: on
spec:
  type: service
  dependsOn:
    - no
    - yes
EOF
rendered
passes
cp "$repo/AGENTS.md" "$workdir/out"
contains "renders the name as written" "### Component \`on\`"
contains "renders bare refs as written" "- Depends on: \`component:no\`, \`component:yes\`"
contains "links the name as written" "https://docs.wacwini.com/catalog/default/component/on"
teardown

echo "a catalog description that would read as a heading is refused, not written"
setup basic
rendered
rewrite_catalog_description "# Not a heading"
render_refuses
contains "says the tail would not survive a second render" "would not survive a second render"
fails
contains "check reports the same" "would not survive a second render"
teardown

# --------------------------------------------------------------------------
echo "a tracked root CLAUDE.md fails"
setup basic
rendered
echo "# old context" >"$repo/CLAUDE.md"
git -C "$repo" add CLAUDE.md
fails
contains "annotates the file" "::error file=CLAUDE.md::tracked \`CLAUDE.md\`"
teardown

echo "a tracked nested CLAUDE.md fails"
setup basic
rendered
mkdir -p "$repo/app/.claude"
echo "# old context" >"$repo/app/CLAUDE.md"
echo "# old context" >"$repo/app/.claude/CLAUDE.md"
git -C "$repo" add app
fails
contains "annotates app/CLAUDE.md" "::error file=app/CLAUDE.md::"
contains "annotates app/.claude/CLAUDE.md" "::error file=app/.claude/CLAUDE.md::"
teardown

echo "an untracked CLAUDE.md is ignored"
setup basic
rendered
mkdir -p "$repo/.worktrees/old-branch"
echo "# old context" >"$repo/.worktrees/old-branch/CLAUDE.md"
echo "# local only" >"$repo/CLAUDE.md"
passes
lacks "says nothing about CLAUDE.md" "file=CLAUDE.md"
teardown

echo "a tracked CLAUDE.local.md fails"
setup basic
rendered
echo "# local context" >"$repo/CLAUDE.local.md"
git -C "$repo" add CLAUDE.local.md
fails
contains "annotates the file" "::error file=CLAUDE.local.md::"
teardown

# --------------------------------------------------------------------------
echo "a missing H1 fails"
setup basic
rendered
drop_line "# fixture-basic"
fails
contains "says the file must open with its H1" "must open with its one H1"
teardown

echo "a second H1 fails"
setup basic
rendered
insert_before "## Layout" "# Another title" ""
fails
contains "names the extra H1" "extra H1 \`# Another title\`"
teardown

echo "a missing required section fails"
setup basic
rendered
drop_line "## Boundaries"
fails
contains "names the section" "missing required section \`## Boundaries\`"
teardown

echo "required sections out of order fail"
setup basic
rendered
rewrite '$0 == "## Layout" { print "## Invariants"; next } $0 == "## Invariants" { print "## Layout"; next } { print }'
fails
contains "says out of order" "\`## Layout\` is out of order"
teardown

echo "a section after Fleet rules fails, and render will not overwrite it"
setup basic
rendered
printf '\n## Notes\n\nHand-written, after the generated tail.\n' >>"$repo/AGENTS.md"
fails
contains "names the section" "\`## Notes\` comes after \`## Fleet rules\`"
render_refuses
contains "render says where the section must go" "move them above \`## Dependencies\` first"
contains "render names the section" "\`## Notes\`"
teardown

echo "a section between Dependencies and Fleet rules fails, and render will not overwrite it"
setup basic
rendered
insert_before "## Fleet rules" "## Notes" "" "Hand-written, inside the generated tail." ""
fails
contains "names the section" "\`## Notes\` comes after \`## Dependencies\`"
render_refuses
contains "render names the section" "\`## Notes\`"
teardown

echo "a setext H2 after the tail is a section too"
setup basic
rendered
printf '\nMy notes\n--------\n\nHand-written.\n' >>"$repo/AGENTS.md"
fails
contains "names the section" "\`## My notes\` comes after \`## Fleet rules\`"
render_refuses
teardown

echo "a hand-written Dependencies section is never overwritten"
setup basic
printf '\n## Dependencies\n\n### Upstream\n\nHand-written notes on what this repo leans on.\n' >>"$repo/AGENTS.md"
render_refuses
contains "render says the section is hand-written" "was not written by render"
fails
contains "check reports the same" "was not written by render"
lacks "check does not also ask for a re-render" "is not what \`render\` writes"
teardown

echo "bare Dependencies and Fleet rules headings are an outline, and render fills them"
setup basic
printf '\n## Dependencies\n\n## Fleet rules\n' >>"$repo/AGENTS.md"
status=0
gate render || status=$?
check "render exits 0" 0 "$status"
passes "check passes after render"
teardown

echo "hand edits inside a generated tail are overwritten by render, after check has failed on them"
setup basic
rendered
insert_before "Incoming edges (what depends on this repo) are not listed here. They live in the catalog at docs.wacwini.com." \
  "### Hand-added" "" "A paragraph render did not write." ""
fails
contains "check diffs the hand edit out" "-### Hand-added"
status=0
gate render || status=$?
check "render exits 0" 0 "$status"
if grep -qF "Hand-added" "$repo/AGENTS.md"; then
  echo "  FAIL: render kept the hand edit"
  failures=$((failures + 1))
else
  echo "  ok: render replaced the generated tail"
fi
passes "check passes after render"
teardown

echo "an unclosed code fence fails, and render will not guess"
setup basic
rendered
line="$(grep -n '^## Layout$' "$repo/AGENTS.md" | cut -d: -f1)"
rewrite '{ print } $0 == "## Layout" { print ""; print "```sh"; print "npm run build" }'
fails
contains "names the opening line" "unclosed code fence opened at line $((line + 2))"
render_refuses
teardown

echo "a paragraph line underlined with --- or === is a heading"
setup basic
rendered
rewrite '$0 == "## Boundaries" { print "Boundaries"; print "----------"; next } { print }'
passes "a setext Boundaries satisfies the contract"
teardown
setup basic
rendered
insert_before "## Layout" "Another title" "=============" ""
fails
contains "a setext H1 is an extra H1" "extra H1 \`# Another title\`"
teardown

echo "a --- under a list item and its continuation line is a rule, not a heading"
setup basic
rendered
rewrite '{ print } $0 == "## Commands" { print ""; print "- run these from the repo root,"; print "  and nowhere else"; print "---" }'
passes
lacks "no false Commands finding" "has no fenced shell block"
teardown

echo "a --- under a blank line is a rule, not a heading"
setup basic
rendered
rewrite '{ print } $0 == "## Commands" { print ""; print "Run these from the repo root."; print ""; print "---" }'
passes
teardown

echo "a # line inside the Commands fence is not an H1"
setup basic
rendered
add_command "# a note about the commands, not a heading"
passes
lacks "reports no extra H1" "extra H1"
teardown

# --------------------------------------------------------------------------
echo "a UTF-8 BOM fails with its own finding, and render strips it"
setup basic
rendered
{ printf '\357\273\277'; cat "$repo/AGENTS.md"; } >"$workdir/bom"
mv "$workdir/bom" "$repo/AGENTS.md"
fails
contains "names the BOM" "starts with a UTF-8 BOM"
lacks "the BOM does not hide the H1" "must open with its one H1"
rendered
if [ "$(head -c 3 "$repo/AGENTS.md" | od -An -tx1 | tr -d ' ')" = "efbbbf" ]; then
  echo "  FAIL: render kept the BOM"
  failures=$((failures + 1))
else
  echo "  ok: render stripped the BOM"
fi
passes "check passes after render"
teardown

echo "CRLF line endings fail with their own finding, and render converts them"
setup basic
rendered
awk '{ printf "%s\r\n", $0 }' "$repo/AGENTS.md" >"$workdir/crlf"
mv "$workdir/crlf" "$repo/AGENTS.md"
fails
contains "names CRLF" "AGENTS.md has CRLF line endings"
lacks "line endings alone produce no diff finding" "is not what \`render\` writes"
rendered
check "render leaves no CR" 0 "$(tr -cd '\r' <"$repo/AGENTS.md" | wc -c | tr -d ' ')"
passes "check passes after render"
teardown

# --------------------------------------------------------------------------
echo "stale dependencies fail until re-rendered"
setup basic
rendered
printf '    - resource:ecosystem-postgres\n' >>"$repo/catalog-info.yaml"
fails
contains "says the file is not what render writes" "AGENTS.md is not what \`render\` writes"
contains "diffs the new dependency in" "+- Depends on: \`resource:ecosystem-postgres\`, \`resource:traefik\`"
rendered
passes "re-render makes it pass"
teardown

echo "stale fleet rules fail until re-rendered from the new rules"
setup basic
rendered
mkdir -p "$workdir/gate/scripts"
cp "$SUBJECT" "$workdir/gate/scripts/agents-md.py"
cp -R "$RULES_DIR" "$workdir/gate/agents-md"
printf '\n7. A rule added upstream.\n' >>"$workdir/gate/agents-md/fleet-rules.md"
status=0
GATE_SCRIPT="$workdir/gate/scripts/agents-md.py" gate check || status=$?
check "the newer gate fails the old tail" 1 "$status"
contains "says the file is not what render writes" "AGENTS.md is not what \`render\` writes"
contains "diffs the new rule in" "+7. A rule added upstream."
GATE_SCRIPT="$workdir/gate/scripts/agents-md.py" rendered
status=0
GATE_SCRIPT="$workdir/gate/scripts/agents-md.py" gate check || status=$?
check "re-rendering from the newer gate passes it" 0 "$status"
fails "the older gate now fails the newer tail"
contains "diffs the rule back out" "-7. A rule added upstream."
teardown

echo "a hand-edited tail fails"
setup basic
rendered
printf '7. A hand-written rule.\n' >>"$repo/AGENTS.md"
fails
contains "says the file is not what render writes" "AGENTS.md is not what \`render\` writes"
contains "diffs the hand edit out" "-7. A hand-written rule."
teardown

# --------------------------------------------------------------------------
echo "Commands with no fenced shell block fails"
setup basic
rendered
rewrite '$0 == "```sh" { skip = 1; next } skip && $0 == "```" { skip = 0; next } !skip { print }'
fails
contains "names the section" "\`## Commands\` has no fenced shell block"
teardown

echo "Commands whose only fence is text fails"
setup basic
rendered
rewrite '$0 == "```sh" { print "```text"; next } { print }'
fails
contains "names the section" "\`## Commands\` has no fenced shell block"
teardown

echo "a non-shell fence in Commands is not checked"
setup basic
rendered
insert_before "## Layout" '```yaml' "npm run nope" '```' ""
passes
teardown

echo "a console fence checks its prompt lines and skips their output"
setup basic
rendered
insert_before "## Layout" '```console' '$ npm run nope' "npm run also-nope" '```' ""
fails
contains "checks the prompt line" "\`npm run nope\`: \`package.json\` has no \`nope\` script"
lacks "skips the output line" "also-nope"
teardown

echo "an unresolved npm run fails"
setup basic
rendered
add_command "npm run nope"
fails
contains "names the script" "\`package.json\` has no \`nope\` script"
teardown

echo "cd app && npm test resolves inside app/, not at the root"
setup basic
rendered
add_command "cd app && npm test && npm start"
passes
teardown
setup basic
rendered
add_command "npm test"
fails "the same script at the root exits 1"
contains "names the root package.json" "\`package.json\` has no \`test\` script"
teardown

echo "cd into a missing directory fails"
setup basic
rendered
add_command "cd missing && npm test"
fails
contains "names the directory" "\`cd missing\`: \`missing\` is not a directory holding any tracked file"
teardown

echo "pushd moves into a directory and popd moves back"
setup basic
rendered
add_command "pushd app && npm test && popd && npm run lint"
passes
teardown
setup basic
rendered
add_command "pushd app && popd && npm test"
fails "a script only app/ has fails after popd"
contains "resolves at the root after popd" "\`package.json\` has no \`test\` script"
teardown

echo "a line with a placeholder is skipped"
setup basic
rendered
add_command "npm run <script-name>"
add_command "cd <stack> && make <target>"
passes
teardown

echo "an unknown command passes"
setup basic
rendered
add_command "kubectl get pods -A"
add_command "tofu init && tofu plan"
add_command "npm ci"
passes
teardown

echo "make resolves targets present in the Makefile and fails on absent ones"
setup basic
rendered
add_command "make build test"
passes "present targets exit 0"
teardown
setup basic
rendered
add_command "make deploy"
fails "an absent target exits 1"
contains "names the target" "\`Makefile\` has no rule for \`deploy\`"
teardown

echo "every recognised form resolves when its path is tracked"
setup basic
rendered
add_command "./scripts/hello.sh"
add_command "scripts/hello.sh --verbose"
add_command "python3 scripts/hello.py"
add_command "uv run scripts/hello.py"
add_command "uv run --with pyyaml python scripts/hello.py"
add_command "node app/server.js"
add_command "node app/server"
add_command "npm --prefix app test"
add_command "python3 -m http.server"
add_command "bash -c 'echo inline'"
add_command "bash scripts/hello.sh > out.log 2>&1"
passes
teardown

echo "every recognised form fails closed when its path is not tracked"
setup basic
rendered
add_command "bash scripts/missing.sh"
add_command "./scripts/missing.sh"
add_command "uv run scripts/missing.py"
add_command "uv run --with pyyaml python scripts/gone.py"
add_command "node app/missing.js"
add_command "npm --prefix app run nope"
add_command "npm --prefix nowhere test"
add_command "make -C app build"
fails
contains "bash path" "\`bash scripts/missing.sh\`: \`scripts/missing.sh\` is not a tracked file"
contains "bare ./ path" "\`./scripts/missing.sh\`: \`scripts/missing.sh\` is not a tracked file"
contains "uv run path" "\`uv run scripts/missing.py\`: \`scripts/missing.py\` is not a tracked file"
contains "uv run interpreter path" "\`scripts/gone.py\` is not a tracked file"
contains "node path" "\`app/missing.js\` is not a tracked file or directory"
contains "npm --prefix script" "\`app/package.json\` has no \`nope\` script"
contains "npm --prefix directory" "\`--prefix nowhere\`: \`nowhere\` is not a directory holding any tracked file"
contains "make -C" "there is no tracked Makefile in \`app\`"
teardown

echo "bash -euo pipefail checks the script, not the option value"
setup basic
rendered
add_command "bash -euo pipefail scripts/hello.sh"
passes
teardown
setup basic
rendered
add_command "bash -euo pipefail scripts/nope.sh"
fails
contains "names the script" "\`scripts/nope.sh\` is not a tracked file"
lacks "does not take pipefail for the script" "\`pipefail\` is not"
teardown

echo "node --test checks every path it is given"
setup basic
rendered
add_command "node --test app/math.test.mjs --test-reporter spec"
passes
teardown
setup basic
rendered
add_command "node --test app/math.test.mjs app/missing.test.mjs"
fails
contains "names the missing test file" "\`app/missing.test.mjs\` is not a tracked file or directory"
lacks "passes the tracked one" "\`app/math.test.mjs\` is not"
teardown

# --------------------------------------------------------------------------
echo "a gitignored build output is left unchecked (claw's pulse-doctor shape)"
setup basic
printf 'dist/\n' >"$repo/app/.gitignore"
git -C "$repo" add app/.gitignore
rendered
add_command "cd app && npm run build && node dist/bin/doctor.js"
add_command "node app/dist/*.js"
add_command "cd app/dist && node doctor.js"
if [ -e "$repo/app/dist" ]; then
  echo "  FAIL: the build output exists, so this case proves nothing"
  failures=$((failures + 1))
fi
passes
teardown

echo "a file on disk that git does not track fails"
setup basic
rendered
printf '#!/usr/bin/env bash\necho local\n' >"$repo/scripts/local.sh"
mkdir -p "$repo/scratch"
: >"$repo/scratch/notes.txt"
add_command "bash scripts/local.sh"
add_command "cd scratch && ls"
fails
contains "names the untracked file" "\`scripts/local.sh\` is not a tracked file, and is not gitignored as a build output"
contains "names the untracked directory" "\`cd scratch\`: \`scratch\` is not a directory holding any tracked file"
teardown

echo "a tracked file passes even where the local disk has lost it"
setup basic
rendered
rm "$repo/scripts/hello.sh"
add_command "bash scripts/hello.sh"
passes
teardown

echo "paths that leave the repo are unchecked"
setup basic
rendered
add_command "bash ../outside.sh"
add_command "cd .. && make nope"
add_command "node /opt/tool/index.js"
passes
teardown

echo "a tracked path with brackets resolves literally, quoted or not"
setup basic
rendered
add_command 'node --test "app/trips/[id]/route.test.mjs"'
add_command "node --test app/trips/[id]/route.test.mjs"
add_command "bash 'scripts/[slug]/run.sh'"
add_command "cd 'app/trips/[id]' && node --test route.test.mjs"
passes
teardown
setup basic
rendered
add_command 'node --test "app/trips/[id]/missing.test.mjs"'
fails
contains "names the missing bracketed path" "\`app/trips/[id]/missing.test.mjs\` matches no tracked file"
teardown

echo "a glob passes when it matches a tracked file and fails when it matches none"
setup basic
rendered
add_command "bash scripts/*.sh"
add_command "node --test app/*.test.mjs"
add_command "node --test 'app/**/*.test.mjs'"
passes
teardown
setup basic
rendered
add_command "node --test app/*.spec.mjs"
fails
contains "names the glob" "\`app/*.spec.mjs\` matches no tracked file"
teardown

if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed"
  exit 1
fi
echo "all scenarios passed"
