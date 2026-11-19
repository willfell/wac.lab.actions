#!/usr/bin/env bash
set -euo pipefail
scripts="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
mkdir -p "$workdir/tools" "$workdir/good/linux-amd64" "$workdir/good/linux-arm64" "$workdir/bad/linux-amd64"

printf '#!/bin/sh\necho genuine\n' >"$workdir/good/linux-amd64/helm"
printf '#!/bin/sh\necho genuine-arm\n' >"$workdir/good/linux-arm64/helm"
printf '#!/bin/sh\necho extra\n' >"$workdir/good/linux-amd64/extra"
printf '#!/bin/sh\necho tampered\n' >"$workdir/bad/linux-amd64/helm"
(cd "$workdir/good" && tar -czf "$workdir/amd64.tgz" linux-amd64/helm linux-amd64/extra)
(cd "$workdir/good" && tar -czf "$workdir/arm64.tgz" linux-arm64/helm)
(cd "$workdir/bad" && tar -czf "$workdir/tampered.tgz" linux-amd64/helm)
amd64_sha="$(sha256sum "$workdir/amd64.tgz" | cut -d' ' -f1)"
arm64_sha="$(sha256sum "$workdir/arm64.tgz" | cut -d' ' -f1)"

sed -e "s/^HELM_SHA256_AMD64=.*/HELM_SHA256_AMD64=\"$amd64_sha\"/" \
  -e "s/^HELM_SHA256_ARM64=.*/HELM_SHA256_ARM64=\"$arm64_sha\"/" \
  "$scripts/install-helm.sh" >"$workdir/install-helm.sh"
grep -q "\"$amd64_sha\"" "$workdir/install-helm.sh"
grep -q "\"$arm64_sha\"" "$workdir/install-helm.sh"

cat >"$workdir/tools/curl" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
output=""
url=""
max_time=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output="$2"; shift ;;
    --max-time) max_time="$2"; shift ;;
    https://*) url="$1" ;;
  esac
  shift
done
echo "$max_time $url" >>"$TEST_STATE/requests"
case "$TEST_MODE" in
  unavailable) exit 22 ;;
  tampered) cp "$TEST_STATE/tampered.tgz" "$output" ;;
  *) cp "$TEST_STATE/$TEST_ARCH.tgz" "$output" ;;
esac
FAKE
cat >"$workdir/tools/uname" <<'FAKE'
#!/usr/bin/env bash
echo "$TEST_UNAME"
FAKE
chmod +x "$workdir/tools/"*
export TEST_STATE="$workdir"

run() {
  local name="$1" mode="$2" arch="$3" machine="$4"
  rm -rf "${workdir:?}/${name:?}"
  mkdir -p "$workdir/$name"
  : >"$workdir/requests"
  status=0
  TEST_MODE="$mode" TEST_ARCH="$arch" TEST_UNAME="$machine" PATH="$workdir/tools:$PATH" \
    RUNNER_TEMP="$workdir/$name" GITHUB_PATH="$workdir/$name/github-path" \
    bash "$workdir/install-helm.sh" >"$workdir/$name/result" 2>&1 || status=$?
}

for pair in "amd64 x86_64" "arm64 aarch64" "arm64 arm64"; do
  read -r arch machine <<<"$pair"
  run "ok-$machine" ok "$arch" "$machine"
  test "$status" -eq 0
  grep -qx "25 https://get.helm.sh/helm-v3.16.3-linux-$arch.tar.gz" "$workdir/requests"
  test "$(wc -l <"$workdir/requests" | tr -d ' ')" = 1
  test -x "$workdir/ok-$machine/bin/helm"
  test "$("$workdir/ok-$machine/bin/helm")" = "$([ "$arch" = amd64 ] && echo genuine || echo genuine-arm)"
  test "$(ls "$workdir/ok-$machine/bin")" = helm
  test "$(cat "$workdir/ok-$machine/github-path")" = "$workdir/ok-$machine/bin"
  echo "PASS: Helm installs from a matching tarball on $machine"
done

run tampered tampered amd64 x86_64
test "$status" -ne 0
grep -q 'does not match its pinned sha256; nothing was installed' "$workdir/tampered/result"
test ! -e "$workdir/tampered/bin"
test ! -e "$workdir/tampered/github-path"
echo "PASS: a tarball with the wrong digest installs nothing"

run unavailable unavailable amd64 x86_64
test "$status" -ne 0
test ! -e "$workdir/unavailable/bin"
test ! -e "$workdir/unavailable/github-path"
echo "PASS: a failed download installs nothing"

run riscv ok amd64 riscv64
test "$status" -ne 0
grep -q 'unsupported architecture: riscv64' "$workdir/riscv/result"
test ! -s "$workdir/requests"
test ! -e "$workdir/riscv/bin"
echo "PASS: an unknown architecture fails before downloading"

if ! grep -q '^HELM_SHA256_AMD64="f5355c79190951eed23c5432a3b920e071f4c00a64f75e077de0dd4cb7b294ea"$' "$scripts/install-helm.sh" ||
  ! grep -q '^HELM_SHA256_ARM64="5bd34ed774df6914b323ff84a0a156ea6ff2ba1eaf0113962fa773f3f9def798"$' "$scripts/install-helm.sh"; then
  echo "FAIL: committed digests changed; re-verify them against get.helm.sh before updating this test" >&2
  exit 1
fi
echo "PASS: the committed digests are the verified ones"
