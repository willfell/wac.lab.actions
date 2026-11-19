#!/usr/bin/env bash
# install-awscli.sh against a fake curl, with throwaway signing keys standing in
# for AWS's: nothing from a download runs unless both its committed sha256 and
# its signature by the pinned key hold.
set -euo pipefail
scripts="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workdir="$(mktemp -d)"
gnupg="$(mktemp -d /tmp/awscli-test.XXXXXX)"
cleanup() {
  gpgconf --homedir "$gnupg" --kill all >/dev/null 2>&1 || true
  rm -rf "$workdir" "$gnupg"
}
trap cleanup EXIT
chmod 700 "$gnupg"
mkdir -p "$workdir/tools" "$workdir/copy" "$workdir/payload/aws" "$workdir/other/aws"

gpg_() { gpg --homedir "$gnupg" --batch --quiet --pinentry-mode loopback --passphrase '' "$@"; }
gpg_ --quick-gen-key 'AWS stand-in <stand-in@example.invalid>' ed25519 sign never
gpg_ --quick-gen-key 'Impostor <impostor@example.invalid>' ed25519 sign never
fingerprint() { gpg_ --with-colons --list-keys "$1" | awk -F: '$1 == "fpr" { print $10; exit }'; }
stand_in="$(fingerprint stand-in@example.invalid)"
impostor="$(fingerprint impostor@example.invalid)"
test -n "$stand_in" && test -n "$impostor" && test "$stand_in" != "$impostor"

cat >"$workdir/payload/aws/install" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
touch "$TEST_STATE/installer-ran"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir) install_dir="$2"; shift ;;
    --bin-dir) bin_dir="$2"; shift ;;
  esac
  shift
done
mkdir -p "$install_dir" "$bin_dir"
printf '#!/bin/sh\necho "aws-cli/2.39.4 stand-in"\n' >"$bin_dir/aws"
chmod +x "$bin_dir/aws"
FAKE
chmod +x "$workdir/payload/aws/install"
printf '#!/bin/sh\ntouch "$TEST_STATE/tampered-ran"\n' >"$workdir/other/aws/install"
chmod +x "$workdir/other/aws/install"
(cd "$workdir/payload" && python3 -I -m zipfile -c "$workdir/good.zip" aws)
(cd "$workdir/other" && python3 -I -m zipfile -c "$workdir/tampered.zip" aws)
gpg_ --local-user "$stand_in" --output "$workdir/good.sig" --detach-sign "$workdir/good.zip"
gpg_ --local-user "$impostor" --output "$workdir/impostor.sig" --detach-sign "$workdir/good.zip"
gpg_ --local-user "$stand_in" --output "$workdir/other.sig" --detach-sign "$workdir/tampered.zip"
good_sha="$(sha256sum "$workdir/good.zip" | cut -d' ' -f1)"

copy() {
  local key_fingerprint="$1"
  sed -e "s/^AWSCLI_SHA256_X86_64=.*/AWSCLI_SHA256_X86_64=\"$good_sha\"/" \
    -e "s/^AWSCLI_SHA256_AARCH64=.*/AWSCLI_SHA256_AARCH64=\"$good_sha\"/" \
    -e "s/^AWSCLI_KEY_FINGERPRINT=.*/AWSCLI_KEY_FINGERPRINT=\"$key_fingerprint\"/" \
    "$scripts/install-awscli.sh" >"$workdir/copy/install-awscli.sh"
  grep -q "\"$good_sha\"" "$workdir/copy/install-awscli.sh"
  grep -q "^AWSCLI_KEY_FINGERPRINT=\"$key_fingerprint\"$" "$workdir/copy/install-awscli.sh"
}
gpg_ --armor --export "$stand_in" >"$workdir/copy/awscli-public-key.asc"

cat >"$workdir/tools/curl" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
output=""
url=""
all=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output="$2"; shift ;;
    https://*) url="$1" ;;
  esac
  all="$all $1"
  shift
done
echo "$url$all" >>"$TEST_STATE/requests"
if [ "$TEST_MODE" = unavailable ]; then exit 22; fi
case "$url" in
  *.zip.sig) cp "$TEST_STATE/$TEST_SIG" "$output" ;;
  *.zip) cp "$TEST_STATE/$TEST_ZIP" "$output" ;;
  *) exit 22 ;;
esac
FAKE
cat >"$workdir/tools/uname" <<'FAKE'
#!/usr/bin/env bash
echo "$TEST_UNAME"
FAKE
chmod +x "$workdir/tools/"*
export TEST_STATE="$workdir"

# The runs see the fake curl and uname first, then a case's own directory
# (first_path), then only the tools the installer and its stand-ins run,
# linked one by one from this machine. No system directory is on their PATH,
# so none can shadow a fake, none drops bash for holding an aws, and no aws of
# this machine's is found unless a case puts its directory in first_path.
mkdir -p "$workdir/sysbin"
for tool in bash sh env dirname find readlink mktemp rm mkdir cp chmod cat touch sed grep sha256sum gpgv base64 unzip; do
  found="$(command -v "$tool")" || { echo "FAIL: this machine has no $tool, which the installer runs" >&2; exit 1; }
  ln -s "$found" "$workdir/sysbin/$tool"
done
first_path=""

run() {
  local name="$1" zip="$2" sig="$3" machine="$4" mode="${5:-ok}"
  rm -rf "${workdir:?}/${name:?}" "$workdir/installer-ran" "$workdir/tampered-ran"
  mkdir -p "$workdir/$name"
  : >"$workdir/requests"
  status=0
  TEST_MODE="$mode" TEST_ZIP="$zip" TEST_SIG="$sig" TEST_UNAME="$machine" \
    PATH="$workdir/tools:${first_path:+$first_path:}$workdir/sysbin" RUNNER_TEMP="$workdir/$name" GITHUB_PATH="$workdir/$name/github-path" \
    bash "$workdir/copy/install-awscli.sh" >"$workdir/$name/result" 2>&1 || status=$?
}
nothing_ran() {
  test ! -e "$workdir/installer-ran" && test ! -e "$workdir/tampered-ran" &&
    test ! -e "$workdir/$1/bin" && test ! -e "$workdir/$1/github-path"
}

copy "$stand_in"
for pair in "x86_64 x86_64" "aarch64 aarch64" "aarch64 arm64"; do
  read -r arch machine <<<"$pair"
  run "ok-$machine" good.zip good.sig "$machine"
  test "$status" -eq 0 || { cat "$workdir/ok-$machine/result"; exit 1; }
  grep -q "^https://awscli.amazonaws.com/awscli-exe-linux-$arch-2.39.4.zip .*--proto =https " "$workdir/requests"
  grep -q "^https://awscli.amazonaws.com/awscli-exe-linux-$arch-2.39.4.zip.sig " "$workdir/requests"
  test "$(wc -l <"$workdir/requests" | tr -d ' ')" = 2
  test -e "$workdir/installer-ran"
  test "$("$workdir/ok-$machine/bin/aws")" = "aws-cli/2.39.4 stand-in"
  test "$(cat "$workdir/ok-$machine/github-path")" = "$workdir/ok-$machine/bin"
  grep -q 'aws-cli/2.39.4 stand-in' "$workdir/ok-$machine/result"
  echo "PASS: the pinned AWS CLI installs from a zip whose sha256 and signature hold on $machine"
done

run tampered tampered.zip other.sig x86_64
test "$status" -ne 0
grep -q 'install-awscli: the AWS CLI 2.39.4 x86_64 zip does not match its committed sha256; nothing was unzipped or run' "$workdir/tampered/result"
nothing_ran tampered
echo "PASS: a zip with the wrong sha256 is refused, though its signature is good"

run impostor good.zip impostor.sig x86_64
test "$status" -ne 0
grep -q "install-awscli: the AWS CLI 2.39.4 x86_64 zip is not signed by the AWS CLI key $stand_in; nothing was unzipped or run" "$workdir/impostor/result"
nothing_ran impostor
echo "PASS: a zip with the right sha256 signed by another key is refused"

run wrongsig good.zip other.sig x86_64
test "$status" -ne 0
grep -q "zip is not signed by the AWS CLI key $stand_in; nothing was unzipped or run" "$workdir/wrongsig/result"
nothing_ran wrongsig
echo "PASS: a signature by the right key over other content is refused"

copy "$impostor"
run fingerprint good.zip good.sig x86_64
test "$status" -ne 0
grep -q "zip is not signed by the AWS CLI key $impostor; nothing was unzipped or run" "$workdir/fingerprint/result"
nothing_ran fingerprint
echo "PASS: a good signature by a key other than the pinned fingerprint is refused"
copy "$stand_in"

mkdir -p "$workdir/planted-bin"
printf '#!/bin/sh\ntouch "$TEST_STATE/planted-ran"\necho "aws-cli/9.9.9 planted"\n' >"$workdir/planted-bin/aws"
chmod 755 "$workdir/planted-bin/aws"
first_path="$workdir/planted-bin"
run planted good.zip good.sig x86_64
first_path=""
test "$status" -eq 0 || { cat "$workdir/planted/result"; exit 1; }
test ! -e "$workdir/planted-ran"
test -e "$workdir/installer-ran"
test "$(wc -l <"$workdir/requests" | tr -d ' ')" = 2
test "$(cat "$workdir/planted/github-path")" = "$workdir/planted/bin"
grep -q 'install-awscli: the aws on PATH is not a root-owned /usr/bin/aws or /usr/local/bin/aws, so it is not used' "$workdir/planted/result"
grep -q 'aws-cli/2.39.4 stand-in' "$workdir/planted/result"
echo "PASS: an aws the job's user owns is never run; the pinned AWS CLI is installed and put first on PATH"

# A runner image's own aws (GitHub's hosted images keep one in /usr/local/bin) is used only when root owns it,
# its directory, the file it resolves to and that file's directory, and none is group- or world-writable.
owned_by_root() { ls -ldn "$1" | awk '{ exit !($3 == 0 && substr($1, 6, 1) != "w" && substr($1, 9, 1) != "w") }'; }
for system in /usr/local/bin/aws /usr/bin/aws; do
  [ -x "$system" ] || continue
  real="$(readlink -f "$system")"
  first_path="$(dirname "$system")"
  run "system-$(basename "$first_path")" good.zip good.sig x86_64
  first_path=""
  test "$status" -eq 0 || { cat "$workdir/system-$(basename "$(dirname "$system")")/result"; exit 1; }
  result="$workdir/system-$(basename "$(dirname "$system")")"
  if owned_by_root "$(dirname "$system")" && owned_by_root "$real" && owned_by_root "$(dirname "$real")"; then
    test ! -s "$workdir/requests" && test ! -e "$workdir/installer-ran" && test ! -e "$result/github-path"
    grep -q '^aws-cli/' "$result/result"
    echo "PASS: the root-owned $system is used and nothing is downloaded"
  else
    test -e "$workdir/installer-ran"
    grep -q 'is not a root-owned /usr/bin/aws or /usr/local/bin/aws, so it is not used' "$result/result"
    echo "PASS: $system, which the job's user or its group could replace, is not used"
  fi
  break
done

run unavailable good.zip good.sig x86_64 unavailable
test "$status" -ne 0
nothing_ran unavailable
echo "PASS: a failed download installs nothing"

run riscv good.zip good.sig riscv64
test "$status" -ne 0
grep -q 'unsupported architecture: riscv64' "$workdir/riscv/result"
test ! -s "$workdir/requests"
nothing_ran riscv
echo "PASS: an unknown architecture fails before downloading"

if ! grep -q '^AWSCLI_VERSION="2.39.4"$' "$scripts/install-awscli.sh" ||
  ! grep -q '^AWSCLI_SHA256_X86_64="5153610ddd5dff13d43b2965c92d58f16e9f3f54164eea76d3abb29f38787597"$' "$scripts/install-awscli.sh" ||
  ! grep -q '^AWSCLI_SHA256_AARCH64="76e4ac0f82b5b1ec68dfc747705fa3298c05e22648e5503f1869f0d035ad57bb"$' "$scripts/install-awscli.sh" ||
  ! grep -q '^AWSCLI_KEY_FINGERPRINT="FB5DB77FD5C118B80511ADA8A6310ACC4672475C"$' "$scripts/install-awscli.sh"; then
  echo "FAIL: the committed version, digests or fingerprint changed; re-verify them against awscli.amazonaws.com and AWS's install guide before updating this test" >&2
  exit 1
fi
committed="$(gpg --homedir "$gnupg" --batch --with-colons --show-keys "$scripts/awscli-public-key.asc" | awk -F: '$1 == "fpr" { print $10; exit }')"
if [ "$committed" != FB5DB77FD5C118B80511ADA8A6310ACC4672475C ]; then
  echo "FAIL: the committed key is $committed, not the AWS CLI key AWS's install guide names" >&2
  exit 1
fi
echo "PASS: the committed version, digests and key are the verified ones"
