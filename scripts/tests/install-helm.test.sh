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
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output="$2"; shift ;;
    https://*) url="$1" ;;
  esac
  all="${all:-} $1"
  shift
done
echo "$url$all" >>"$TEST_STATE/requests"
count="$(wc -l <"$TEST_STATE/requests" | tr -d ' ')"
case "$TEST_MODE" in
  unavailable) exit 22 ;;
  tampered) cp "$TEST_STATE/tampered.tgz" "$output" ;;
  flaky)
    if [ "$count" -le "$TEST_FAIL_FIRST" ]; then exit 28; fi
    cp "$TEST_STATE/$TEST_ARCH.tgz" "$output"
    ;;
  partial)
    if [ -e "$output" ]; then exit 99; fi
    if [ "$count" -eq 1 ]; then head -c 100 "$TEST_STATE/amd64.tgz" >"$output"; exit 28; fi
    cp "$TEST_STATE/$TEST_ARCH.tgz" "$output"
    ;;
  *) cp "$TEST_STATE/$TEST_ARCH.tgz" "$output" ;;
esac
FAKE
cat >"$workdir/tools/sleep" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >>"$TEST_STATE/sleeps"
FAKE
cat >"$workdir/tools/uname" <<'FAKE'
#!/usr/bin/env bash
echo "$TEST_UNAME"
FAKE
chmod +x "$workdir/tools/"*
export TEST_STATE="$workdir"

run() {
  local name="$1" mode="$2" arch="$3" machine="$4"
  shift 4
  rm -rf "${workdir:?}/${name:?}"
  mkdir -p "$workdir/$name"
  : >"$workdir/requests"
  : >"$workdir/sleeps"
  status=0
  TEST_MODE="$mode" TEST_ARCH="$arch" TEST_UNAME="$machine" TEST_FAIL_FIRST="${TEST_FAIL_FIRST:-0}" \
    PATH="$workdir/tools:$PATH" RUNNER_TEMP="$workdir/$name" GITHUB_PATH="$workdir/$name/github-path" \
    bash "$workdir/install-helm.sh" "$@" >"$workdir/$name/result" 2>&1 || status=$?
}

requests() { wc -l <"$workdir/requests" | tr -d ' '; }
installed() { test -x "$workdir/$1/bin/helm"; }
nothing_installed() { test ! -e "$workdir/$1/bin" && test ! -e "$workdir/$1/github-path"; }

for pair in "amd64 x86_64" "arm64 aarch64" "arm64 arm64"; do
  read -r arch machine <<<"$pair"
  run "ok-$machine" ok "$arch" "$machine"
  test "$status" -eq 0
  grep -q "^https://get.helm.sh/helm-v3.16.3-linux-$arch.tar.gz .*--max-time 120 " "$workdir/requests"
  grep -q -- " --proto =https " "$workdir/requests"
  grep -q -- " --proto-redir =https " "$workdir/requests"
  grep -q -- " --tlsv1.2 " "$workdir/requests"
  test "$(requests)" = 1
  test -x "$workdir/ok-$machine/bin/helm"
  test "$("$workdir/ok-$machine/bin/helm")" = "$([ "$arch" = amd64 ] && echo genuine || echo genuine-arm)"
  test "$(ls "$workdir/ok-$machine/bin")" = helm
  test "$(cat "$workdir/ok-$machine/github-path")" = "$workdir/ok-$machine/bin"
  echo "PASS: Helm installs from a matching tarball on $machine"
done

run tampered tampered amd64 x86_64
test "$status" -ne 0
test "$(requests)" = 3
test "$(wc -l <"$workdir/sleeps" | tr -d ' ')" = 2
grep -q 'no download matched the pinned sha256; nothing was installed' "$workdir/tampered/result"
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

run short ok amd64 x86_64 --max-time 25 --attempts 1
test "$status" -eq 0
grep -q -- "--max-time 25 " "$workdir/requests"
installed short
echo "PASS: the download bound is a parameter"

run onetry unavailable amd64 x86_64 --max-time 25 --attempts 1
test "$status" -ne 0
test "$(requests)" = 1
test ! -s "$workdir/sleeps"
nothing_installed onetry
echo "PASS: one attempt means one download and no pause"

TEST_FAIL_FIRST=2 run flaky flaky amd64 x86_64
test "$status" -eq 0
test "$(requests)" = 3
test "$(wc -l <"$workdir/sleeps" | tr -d ' ')" = 2
installed flaky
echo "PASS: a download that fails twice is retried and installs"

run partial partial amd64 x86_64
test "$status" -eq 0
test "$(requests)" = 2
installed partial
echo "PASS: a partial file from a failed attempt is deleted before the next"

run bad1 ok amd64 x86_64 --attempts 9
test "$status" -eq 2
run bad2 ok amd64 x86_64 --max-time soon
test "$status" -eq 2
run bad3 ok amd64 x86_64 --max-time
test "$status" -eq 2
run bad4 ok amd64 x86_64 --whatever
test "$status" -eq 2
test ! -s "$workdir/requests"
echo "PASS: bad arguments fail before downloading"

if ! grep -q '^HELM_SHA256_AMD64="f5355c79190951eed23c5432a3b920e071f4c00a64f75e077de0dd4cb7b294ea"$' "$scripts/install-helm.sh" ||
  ! grep -q '^HELM_SHA256_ARM64="5bd34ed774df6914b323ff84a0a156ea6ff2ba1eaf0113962fa773f3f9def798"$' "$scripts/install-helm.sh"; then
  echo "FAIL: committed digests changed; re-verify them against get.helm.sh before updating this test" >&2
  exit 1
fi
echo "PASS: the committed digests are the verified ones"
