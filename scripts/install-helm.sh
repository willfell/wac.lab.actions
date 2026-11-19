#!/usr/bin/env bash
# Installs the one Helm that wac-cd uses, from a tarball checked against a digest committed here.
# Usage: install-helm.sh   (needs RUNNER_TEMP and GITHUB_PATH; takes no arguments)
# Nothing fetched is ever executed: the tarball is hashed before it is opened,
# and only linux-<arch>/helm is taken out of it.
set -euo pipefail

HELM_VERSION="v3.16.3"
HELM_SHA256_AMD64="f5355c79190951eed23c5432a3b920e071f4c00a64f75e077de0dd4cb7b294ea"
HELM_SHA256_ARM64="5bd34ed774df6914b323ff84a0a156ea6ff2ba1eaf0113962fa773f3f9def798"

BIN_DIR="${RUNNER_TEMP:?}/bin"
: "${GITHUB_PATH:?}"

case "$(uname -m)" in
  x86_64)
    arch=amd64
    expected="$HELM_SHA256_AMD64"
    ;;
  aarch64 | arm64)
    arch=arm64
    expected="$HELM_SHA256_ARM64"
    ;;
  *)
    echo "install-helm: unsupported architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

curl -fsSL --max-time 25 -o "$work/helm.tar.gz" \
  "https://get.helm.sh/helm-${HELM_VERSION}-linux-${arch}.tar.gz"

if ! (cd "$work" && echo "${expected}  helm.tar.gz" | sha256sum -c - >/dev/null 2>&1); then
  echo "install-helm: the helm ${HELM_VERSION} ${arch} tarball does not match its pinned sha256; nothing was installed" >&2
  exit 1
fi

tar -xzf "$work/helm.tar.gz" -C "$work" "linux-${arch}/helm"
mkdir -p "$BIN_DIR"
install -m 0755 "$work/linux-${arch}/helm" "$BIN_DIR/helm"
echo "$BIN_DIR" >>"$GITHUB_PATH"
