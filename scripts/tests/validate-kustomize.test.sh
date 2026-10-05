#!/usr/bin/env bash
set -euo pipefail
subject="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/validate-kustomize.sh"
validator="$(command -v kubeconform)"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
mkdir -p "$workdir/bin"
export TEST_STATE="$workdir"
cat >"$workdir/bin/kustomize" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
echo build >>"$TEST_STATE/builds"
cat "$TEST_STATE/manifest"
FAKE
cat >"$workdir/bin/kubeconform" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$TEST_STATE/arguments"
HTTPS_PROXY=http://127.0.0.1:1 HTTP_PROXY=http://127.0.0.1:1 NO_PROXY=127.0.0.1 "$TEST_VALIDATOR" "$@"
FAKE
TEST_PYTHON="$(command -v python3)"
TEST_ADAPTER="$(dirname "$subject")/schema-api-proxy.py"
export TEST_PYTHON TEST_ADAPTER
cat >"$workdir/bin/python3" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
exec "$TEST_PYTHON" "$TEST_STATE/server.py" "$@"
FAKE
cat >"$workdir/server.py" <<'FAKE'
import importlib.util
import io
import os
import sys
from pathlib import Path
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location("adapter", os.environ["TEST_ADAPTER"])
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)

def urlopen(request, timeout):
    state = Path(os.environ["TEST_STATE"])
    with (state / "requests").open("a") as log:
        log.write(request.full_url + "\n")
    response = os.environ["TEST_RESPONSE"]
    if response == "unavailable":
        raise HTTPError(request.full_url, 503, "unavailable", None, None)
    if "custom-example-v1.json" in request.full_url:
        raise HTTPError(request.full_url, 404, "absent", None, None)
    if "datreeio/CRDs-catalog" in request.full_url:
        return io.BytesIO((state / "crd-schema").read_bytes())
    return io.BytesIO((state / response).read_bytes())

adapter.urlopen = urlopen
sys.argv = sys.argv[1:]
adapter.main()
FAKE
chmod +x "$workdir/bin/"*
cat >"$workdir/schema" <<'SCHEMA'
{"$schema":"http://json-schema.org/draft-04/schema#","type":"object","additionalProperties":false,"required":["apiVersion","kind","metadata","spec"],"properties":{"apiVersion":{"enum":["v1"]},"kind":{"enum":["Service"]},"metadata":{"type":"object"},"spec":{"type":"object","additionalProperties":false,"properties":{"ports":{"type":"array","items":{"type":"object","properties":{"port":{"type":"integer"}}}}}}}}
SCHEMA
printf '{"name":"service-v1.json","content":"metadata is not a schema"}\n' >"$workdir/metadata"
printf '{"$schema":"http://json-schema.org/draft-04/schema#",broken}\n' >"$workdir/malformed"
printf '{"type":"object","additionalProperties":false,"properties":{"apiVersion":{"type":"string"},"kind":{"enum":["Custom"]},"metadata":{"type":"object"}}}\n' >"$workdir/crd-schema"
export TEST_VALIDATOR="$validator"
export PATH="$workdir/bin:$PATH"

run_case() {
  local label="$1" expected="$2" response="$3" flags="$4" status=0
  : >"$workdir/builds"
  : >"$workdir/requests"
  : >"$workdir/arguments"
  TEST_RESPONSE="$response" KUSTOMIZE_DIR=fixture FLAGS="$flags" \
    bash "$subject" >"$workdir/result" 2>&1 || status=$?
  if [ "$expected" = pass ]; then
    test "$status" -eq 0 || { cat "$workdir/result"; exit 1; }
  else
    test "$status" -ne 0 || { echo "unexpected pass: $label"; exit 1; }
  fi
  test "$(wc -l <"$workdir/builds" | tr -d ' ')" = 1
  echo "PASS: $label"
}

printf 'apiVersion: v1\nkind: Service\nmetadata:\n  name: test\nspec:\n  ports:\n    - port: 80\n' >"$workdir/manifest"
run_case 'valid resource recovers through API' pass schema '-strict -summary -schema-location https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/{{.ResourceKind}}-{{.ResourceAPIVersion}}.json'
test -s "$workdir/requests"
grep -q 'Valid: 1' "$workdir/result"
printf 'unexpected: true\n' >>"$workdir/manifest"
run_case 'strict validation rejects an unknown field after recovery' fail schema '-strict -summary'
sed 's/port: 80/port: invalid/' "$workdir/manifest" >"$workdir/changed"
mv "$workdir/changed" "$workdir/manifest"
run_case 'invalid field type still fails' fail schema '-strict -summary'
run_case 'API failure stays failed' fail unavailable '-strict -summary'
run_case 'API metadata cannot become a permissive schema' fail metadata '-strict -summary'
run_case 'malformed schema stays failed even with caller skip policy' fail malformed '-strict -summary -ignore-missing-schemas'
printf 'apiVersion: example.invalid/v1\nkind: Custom\nmetadata:\n  name: test\n' >"$workdir/manifest"
run_case 'missing custom schema preserves caller skip policy' pass schema '-strict -summary -ignore-missing-schemas'
test -s "$workdir/requests"
run_case 'missing custom schema fails without caller skip policy' fail schema '-strict -summary'
run_case 'explicit CRD schema location keeps original search order' pass schema '-strict -summary -schema-location default -schema-location https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
grep -q 'datreeio/CRDs-catalog/contents/example.invalid/custom_v1.json?ref=main' "$workdir/requests"
printf 'unexpected: true\n' >>"$workdir/manifest"
run_case 'explicit CRD schema remains strict with equals-style flags' fail schema '-strict -summary -schema-location=default -schema-location=https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
printf 'apiVersion: v1\nkind: Service\nmetadata:\n  name: test\nspec: {}\n' >"$workdir/manifest"
mkdir -p "$workdir/preloaded"
key="$(printf '%s' 'https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/service-v1.json' | shasum -a 256 | cut -d ' ' -f 1)"
cp "$workdir/schema" "$workdir/preloaded/$key"
run_case 'existing caller cache and JSON output are preserved' pass unavailable "-strict -output json -cache $workdir/preloaded"
test ! -s "$workdir/requests"
grep -q '"resources"' "$workdir/result"
printf 'spec: [\n' >"$workdir/manifest"
run_case 'malformed manifest fails without fallback' fail unavailable '-strict -summary'
test ! -s "$workdir/requests"
