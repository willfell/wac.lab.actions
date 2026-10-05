#!/usr/bin/env bash
# Shared arch-aware installer for the fleet's k8s and registry tooling.
# Usage: install-tools.sh TOOL[,TOOL...]  with versions supplied via env
# (KUBECTL_VERSION, KUSTOMIZE_VERSION, CRANE_VERSION, KUBECONFORM_VERSION,
# HELM_VERSION — HELM_VERSION empty or unset means latest).
set -euo pipefail

TOOLS_CSV="${1:?usage: install-tools.sh kubectl,kustomize,crane,kubeconform,helm}"
BIN_DIR="${RUNNER_TEMP:?}/bin"
mkdir -p "$BIN_DIR"

case "$(uname -m)" in
  x86_64) ARCH=amd64 CRANE_ARCH=x86_64 ;;
  aarch64 | arm64) ARCH=arm64 CRANE_ARCH=arm64 ;;
  *)
    echo "unsupported architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

IFS=',' read -ra TOOLS <<<"$TOOLS_CSV"
for tool in "${TOOLS[@]}"; do
  case "$tool" in
    kubectl)
      curl -fsSLo "$BIN_DIR/kubectl" \
        "https://dl.k8s.io/release/${KUBECTL_VERSION:?}/bin/linux/${ARCH}/kubectl"
      chmod +x "$BIN_DIR/kubectl"
      ;;
    kustomize)
      curl -fsSL \
        "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2F${KUSTOMIZE_VERSION:?}/kustomize_${KUSTOMIZE_VERSION:?}_linux_${ARCH}.tar.gz" |
        tar -xz -C "$BIN_DIR" kustomize
      ;;
    crane)
      curl -fsSL \
        "https://github.com/google/go-containerregistry/releases/download/${CRANE_VERSION:?}/go-containerregistry_Linux_${CRANE_ARCH}.tar.gz" |
        tar -xz -C "$BIN_DIR" crane
      ;;
    kubeconform)
      curl -fsSL \
        "https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION:?}/kubeconform-linux-${ARCH}.tar.gz" |
        tar -xz -C "$BIN_DIR" kubeconform
      ;;
    helm)
      # get-helm-3 verifies its own work with `command -v helm`, and $BIN_DIR
      # does not reach PATH until the GITHUB_PATH append below, which only
      # affects later steps. On a runner image that already ships helm the
      # check passed against that copy; on one that does not, the installer
      # writes the binary and then declares it missing.
      (
        installer="$(mktemp)"
        trap 'rm -f "$installer"' EXIT
        if ! curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 -o "$installer"; then
          curl -fsSL -H 'Accept: application/vnd.github.raw+json' \
            'https://api.github.com/repos/helm/helm/contents/scripts/get-helm-3?ref=main' -o "$installer"
        fi
        head -n 1 "$installer" | grep -qx '#!/usr/bin/env bash'
        DESIRED_VERSION="${HELM_VERSION:-}" HELM_INSTALL_DIR="$BIN_DIR" USE_SUDO=false \
          PATH="$BIN_DIR:$PATH" bash "$installer"
      )
      ;;
    *)
      echo "unknown tool: ${tool}" >&2
      exit 1
      ;;
  esac
done

echo "$BIN_DIR" >>"$GITHUB_PATH"
