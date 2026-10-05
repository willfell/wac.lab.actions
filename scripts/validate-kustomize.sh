#!/usr/bin/env bash
set -euo pipefail
KUSTOMIZE_DIR="${KUSTOMIZE_DIR:?}"
FLAGS="${FLAGS:--strict -summary}"
workdir="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/kubeconform.XXXXXX")"
proxy_pid=""
cleanup() {
  if [ -n "$proxy_pid" ]; then
    kill "$proxy_pid" 2>/dev/null || true
    wait "$proxy_pid" 2>/dev/null || true
  fi
  rm -rf "$workdir"
}
trap cleanup EXIT
read -ra flag_args <<<"$FLAGS"
kustomize build "$KUSTOMIZE_DIR" >"$workdir/manifests.yaml"
status=0
kubeconform "${flag_args[@]}" - <"$workdir/manifests.yaml" >"$workdir/result" 2>&1 || status=$?
if [ "$status" -eq 0 ] || ! grep -Eq '(failed downloading schema at|error while downloading schema at|failed parsing schema from) https://raw\.githubusercontent\.com/(yannh/kubernetes-json-schema/master|datreeio/CRDs-catalog/main)/' "$workdir/result"; then
  cat "$workdir/result"
  exit "$status"
fi
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$here/schema-api-proxy.py" "$workdir/port" &
proxy_pid=$!
for _ in {1..50}; do
  if [ -s "$workdir/port" ]; then break; fi
  if ! kill -0 "$proxy_pid" 2>/dev/null; then
    cat "$workdir/result"
    exit "$status"
  fi
  sleep 0.1
done
port="$(cat "$workdir/port")"
[[ "$port" =~ ^[0-9]+$ ]]
prefix='https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/'
crd_prefix='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/'
local_prefix="http://127.0.0.1:$port/"
default_template='{{.NormalizedKubernetesVersion}}-standalone{{.StrictSuffix}}/{{.ResourceKind}}{{.KindSuffix}}.json'
has_location=false
for ((i = 0; i < ${#flag_args[@]}; i++)); do
  case "${flag_args[$i]}" in
    -schema-location|--schema-location)
      has_location=true
      value="${flag_args[$((i + 1))]:?schema-location requires a value}"
      if [ "$value" = default ]; then
        flag_args[$((i + 1))]="$local_prefix$default_template"
      elif [[ "$value" == "$prefix"* ]]; then
        flag_args[$((i + 1))]="$local_prefix${value#"$prefix"}"
      elif [[ "$value" == "$crd_prefix"* ]]; then
        flag_args[$((i + 1))]="${local_prefix}crds/${value#"$crd_prefix"}"
      fi
      ;;
    -schema-location=*|--schema-location=*)
      has_location=true
      value="${flag_args[$i]#*=}"
      if [ "$value" = default ]; then
        flag_args[$i]="-schema-location=$local_prefix$default_template"
      elif [[ "$value" == "$prefix"* ]]; then
        flag_args[$i]="-schema-location=$local_prefix${value#"$prefix"}"
      elif [[ "$value" == "$crd_prefix"* ]]; then
        flag_args[$i]="-schema-location=${local_prefix}crds/${value#"$crd_prefix"}"
      fi
      ;;
  esac
done
if ! "$has_location"; then
  flag_args+=(-schema-location "$local_prefix$default_template")
fi
echo 'Retrying the same manifests through the upstream GitHub schema API' >&2
NO_PROXY="127.0.0.1${NO_PROXY:+,$NO_PROXY}" kubeconform "${flag_args[@]}" - <"$workdir/manifests.yaml"
