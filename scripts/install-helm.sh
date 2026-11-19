#!/usr/bin/env bash
# Installs the one Helm that wac-cd uses, from a tarball checked against a digest committed here.
# Usage: install-helm.sh [--max-time SECONDS] [--attempts N]   (needs RUNNER_TEMP and GITHUB_PATH)
#   --max-time  longest one download attempt may take; default 120
#   --attempts  how many downloads to try, 5 seconds apart; default 3
# Nothing fetched is ever executed: the tarball is hashed before it is opened,
# and only linux-<arch>/helm is taken out of it.
set -euo pipefail

HELM_VERSION="v3.16.3"
HELM_SHA256_AMD64="f5355c79190951eed23c5432a3b920e071f4c00a64f75e077de0dd4cb7b294ea"
HELM_SHA256_ARM64="5bd34ed774df6914b323ff84a0a156ea6ff2ba1eaf0113962fa773f3f9def798"
RETRY_PAUSE=5

max_time=120
attempts=3
while [ "$#" -gt 0 ]; do
  case "$1" in
    --max-time | --attempts)
      if [ "$#" -lt 2 ]; then
        echo "install-helm: $1 needs a value" >&2
        exit 2
      fi
      if [ "$1" = --max-time ]; then max_time="$2"; else attempts="$2"; fi
      shift 2
      ;;
    *)
      echo "install-helm: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done
case "$max_time" in '' | *[!0-9]* | 0?*)
  echo "install-helm: --max-time wants a whole number of seconds" >&2
  exit 2
  ;;
esac
case "$attempts" in '' | *[!0-9]* | 0?*)
  echo "install-helm: --attempts wants a whole number" >&2
  exit 2
  ;;
esac
if [ "$max_time" -lt 1 ] || [ "$max_time" -gt 600 ] || [ "$attempts" -lt 1 ] || [ "$attempts" -gt 5 ]; then
  echo "install-helm: --max-time must be 1 to 600 and --attempts 1 to 5" >&2
  exit 2
fi

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
tarball="$work/helm.tar.gz"

verified=false
for ((attempt = 1; attempt <= attempts; attempt++)); do
  rm -f "$tarball"
  if curl -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 --max-time "$max_time" -o "$tarball" \
    "https://get.helm.sh/helm-${HELM_VERSION}-linux-${arch}.tar.gz" &&
    (cd "$work" && echo "${expected}  helm.tar.gz" | sha256sum -c - >/dev/null 2>&1); then
    verified=true
    break
  fi
  rm -f "$tarball"
  echo "install-helm: attempt ${attempt} of ${attempts} did not give the pinned helm ${HELM_VERSION} ${arch} tarball" >&2
  if [ "$attempt" -lt "$attempts" ]; then
    sleep "$RETRY_PAUSE"
  fi
done
if [ "$verified" != true ]; then
  echo "install-helm: no download matched the pinned sha256; nothing was installed" >&2
  exit 1
fi

tar -xzf "$tarball" -C "$work" "linux-${arch}/helm"
mkdir -p "$BIN_DIR"
install -m 0755 "$work/linux-${arch}/helm" "$BIN_DIR/helm"
echo "$BIN_DIR" >>"$GITHUB_PATH"
