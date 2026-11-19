#!/usr/bin/env bash
# Refuses a downloaded site export that holds anything but plain files and
# directories, before nextjs-site-deploy assumes its AWS role and syncs it.
# Usage: check-site-export.sh DIR
# The export is data the build job made from the site's own code, so it is
# walked and never run. `aws s3 sync` follows symbolic links, so a link in it
# could publish a file from anywhere on the runner; every link is refused, and
# one whose target resolves outside the export is named as such. Anything that
# is neither a regular file nor a directory is refused too. Every offending
# entry is reported, as a workflow error, before the step fails.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "check-site-export: usage: check-site-export.sh DIR" >&2
  exit 2
fi
if [ -L "$1" ] || [ ! -d "$1" ]; then
  echo "::error::nextjs-site-deploy: the site export $1 is not a directory of its own; nothing was synced."
  exit 1
fi
root="$(cd "$1" && pwd -P)"

inside() {
  case "$1" in
    "$root" | "$root"/*) return 0 ;;
    *) return 1 ;;
  esac
}

refused=0
while IFS= read -r -d '' entry; do
  relative="${entry#"$root"/}"
  if [ -L "$entry" ]; then
    target="$(readlink "$entry")"
    resolved="$(realpath -- "$entry" 2>/dev/null || true)"
    if [ -n "$resolved" ] && ! inside "$resolved"; then
      echo "::error::nextjs-site-deploy: $relative is a symbolic link to $target, which resolves outside the export."
    else
      echo "::error::nextjs-site-deploy: $relative is a symbolic link to $target."
    fi
    refused=$((refused + 1))
  elif [ ! -f "$entry" ] && [ ! -d "$entry" ]; then
    echo "::error::nextjs-site-deploy: $relative is neither a regular file nor a directory."
    refused=$((refused + 1))
  elif ! inside "$(realpath -- "$entry")"; then
    echo "::error::nextjs-site-deploy: $relative resolves outside the export."
    refused=$((refused + 1))
  fi
done < <(find "$root" -mindepth 1 -print0)

if [ "$refused" -gt 0 ]; then
  if [ "$refused" -eq 1 ]; then
    held="1 entry that is not a plain file or directory"
  else
    held="$refused entries that are not plain files or directories"
  fi
  echo "::error::nextjs-site-deploy: the site export holds $held; deploy syncs only plain files and directories, so nothing was synced."
  exit 1
fi
