#!/usr/bin/env bash
set -euo pipefail
subject="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/install-tools.sh"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
mkdir -p "$workdir/tools"
export TEST_STATE="$workdir"
cat >"$workdir/tools/curl" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
output=""
url=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output="$2"; shift ;;
    https://*) url="$1" ;;
  esac
  shift
done
echo "$url" >>"$TEST_STATE/requests"
if [[ "$url" == https://raw.githubusercontent.com/* ]] && [ "$TEST_MODE" != raw ]; then exit 35; fi
if [ "$TEST_MODE" = unavailable ]; then exit 22; fi
if [ "$TEST_MODE" = metadata ]; then
  echo '{"content":"this is not an executable installer"}' >"$output"
else
  cat >"$output" <<'INSTALLER'
#!/usr/bin/env bash
set -euo pipefail
test "$USE_SUDO" = false
test "$DESIRED_VERSION" = v3.17.0
command -v helm >/dev/null
echo installed >>"$TEST_STATE/executions"
INSTALLER
fi
FAKE
printf '#!/usr/bin/env bash\nexit 0\n' >"$workdir/tools/helm"
chmod +x "$workdir/tools/"*

for mode in raw api unavailable metadata; do
  mkdir -p "$workdir/$mode/bin"
  cp "$workdir/tools/helm" "$workdir/$mode/bin/helm"
  : >"$workdir/requests"
  : >"$workdir/executions"
  status=0
  TEST_MODE="$mode" PATH="$workdir/tools:$PATH" RUNNER_TEMP="$workdir/$mode" \
    GITHUB_PATH="$workdir/github-path" HELM_VERSION=v3.17.0 \
    bash "$subject" helm >"$workdir/result" 2>&1 || status=$?
  case "$mode" in
    raw|api)
      test "$status" -eq 0
      test "$(wc -l <"$workdir/executions" | tr -d ' ')" = 1
      ;;
    *)
      test "$status" -ne 0
      test ! -s "$workdir/executions"
      ;;
  esac
  if [ "$mode" = raw ]; then
    test "$(wc -l <"$workdir/requests" | tr -d ' ')" = 1
  else
    grep -qx 'https://api.github.com/repos/helm/helm/contents/scripts/get-helm-3?ref=main' "$workdir/requests"
  fi
  echo "PASS: Helm installer $mode"
done
