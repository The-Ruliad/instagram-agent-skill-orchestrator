#!/usr/bin/env bash
# Build the TR_OS staging layout from this checkout:
#   OUT/claude-user/<skill>/   each skill byte-for-byte, plus LICENSE and SOURCE.txt
# install.sh and make-manifest.sh both call this, so the manifest a reviewer
# commits describes exactly what the installer stages.
#
#   bash tros/stage.sh OUT
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${1:?usage: stage.sh OUT}"
mkdir -p "$out/claude-user"
for dir in "$repo"/skills/*/; do
  skill="$(basename "$dir")"
  mkdir -p "$out/claude-user/$skill"
  cp -R "$dir." "$out/claude-user/$skill/"
  cp "$repo/LICENSE" "$out/claude-user/$skill/LICENSE"
  sed "s/@SKILL@/$skill/" "$repo/tros/SOURCE.txt" > "$out/claude-user/$skill/SOURCE.txt"
done
find "$out/claude-user" \( -name '__pycache__' -o -name '.DS_Store' \) -prune -exec rm -rf {} +
