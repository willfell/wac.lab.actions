#!/usr/bin/env bash
# Installs the one AWS CLI v2 that nextjs-site-deploy uses on a runner that has
# no aws, from AWS's zip, checked against a sha256 committed here and AWS's
# signature by the key committed beside this script, before anything in it runs.
# Usage: install-awscli.sh   (needs RUNNER_TEMP and GITHUB_PATH; curl, sha256sum, gpgv, base64 and unzip)
# Installs under $RUNNER_TEMP/aws-cli with the aws command in $RUNNER_TEMP/bin,
# which goes on GITHUB_PATH for the later steps; no sudo.
set -euo pipefail

AWSCLI_VERSION="2.39.4"
AWSCLI_SHA256_X86_64="5153610ddd5dff13d43b2965c92d58f16e9f3f54164eea76d3abb29f38787597"
AWSCLI_SHA256_AARCH64="76e4ac0f82b5b1ec68dfc747705fa3298c05e22648e5503f1869f0d035ad57bb"
AWSCLI_KEY_FINGERPRINT="FB5DB77FD5C118B80511ADA8A6310ACC4672475C"

key="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/awscli-public-key.asc"
: "${RUNNER_TEMP:?}" "${GITHUB_PATH:?}"

case "$(uname -m)" in
  x86_64)
    arch=x86_64
    expected="$AWSCLI_SHA256_X86_64"
    ;;
  aarch64 | arm64)
    arch=aarch64
    expected="$AWSCLI_SHA256_AARCH64"
    ;;
  *)
    echo "install-awscli: unsupported architecture: $(uname -m)" >&2
    exit 1
    ;;
esac
for tool in curl sha256sum gpgv base64 unzip; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "install-awscli: the runner has no aws and no $tool to install the pinned AWS CLI ${AWSCLI_VERSION} with" >&2
    exit 1
  fi
done
if [ ! -s "$key" ]; then
  echo "install-awscli: the committed AWS CLI key is missing at $key" >&2
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
url="https://awscli.amazonaws.com/awscli-exe-linux-${arch}-${AWSCLI_VERSION}.zip"
what="the AWS CLI ${AWSCLI_VERSION} ${arch} zip"
for target in zip sig; do
  source_url="$url"
  if [ "$target" = sig ]; then source_url="$url.sig"; fi
  if ! curl -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 3 --retry-delay 5 --max-time 300 \
    -o "$work/awscli.$target" "$source_url"; then
    echo "install-awscli: could not download $source_url; nothing was installed" >&2
    exit 1
  fi
done

if ! (cd "$work" && echo "${expected}  awscli.zip" | sha256sum -c - >/dev/null 2>&1); then
  echo "install-awscli: $what does not match its committed sha256; nothing was unzipped or run" >&2
  exit 1
fi

gnupg="$work/gnupg"
mkdir -m 700 "$gnupg"
sed -e '1,/^$/d' -e '/^=/d' -e '/^-----END/d' "$key" | base64 -d >"$gnupg/aws-cli.gpg"
if ! status="$(gpgv --homedir "$gnupg" --keyring "$gnupg/aws-cli.gpg" --status-fd 1 \
  "$work/awscli.sig" "$work/awscli.zip" 2>"$work/gpgv.log")" ||
  ! grep -Eq "^\[GNUPG:\] VALIDSIG .* ${AWSCLI_KEY_FINGERPRINT}\$" <<<"$status"; then
  cat "$work/gpgv.log" >&2
  echo "install-awscli: $what is not signed by the AWS CLI key ${AWSCLI_KEY_FINGERPRINT}; nothing was unzipped or run" >&2
  exit 1
fi

unzip -q "$work/awscli.zip" -d "$work/unzipped"
"$work/unzipped/aws/install" --install-dir "$RUNNER_TEMP/aws-cli" --bin-dir "$RUNNER_TEMP/bin"
echo "$RUNNER_TEMP/bin" >>"$GITHUB_PATH"
"$RUNNER_TEMP/bin/aws" --version
