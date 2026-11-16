#!/usr/bin/env bash
# Regenerate tros/MANIFEST.sha256 after reviewing a change to any skill.
# The manifest is the review: install.sh refuses files that are not in it or
# that differ from it. Read every changed file before you run this.
#
#   bash tros/make-manifest.sh
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/ig-manifest.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
bash "$repo/tros/stage.sh" "$tmp"
(
  cd "$tmp/claude-user"
  find . -type f | sed 's|^\./||' | LC_ALL=C sort | while IFS= read -r file; do
    shasum -a 256 "$file"
  done
) > "$repo/tros/MANIFEST.sha256"
echo "tros/MANIFEST.sha256: $(wc -l < "$repo/tros/MANIFEST.sha256" | tr -d ' ') files"
