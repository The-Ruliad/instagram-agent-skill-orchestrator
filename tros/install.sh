#!/usr/bin/env bash
# Install this pack into TR_OS, the way TR_OS installs every reviewed skill:
#   1. stage each skill with LICENSE and SOURCE.txt, and check every file
#      against tros/MANIFEST.sha256 (no unlisted files, no changed bytes)
#   2. add an "instagram" set to ~/TR_OS/skills/sets.json, linked into the
#      website repo and every --project, and gate `igapi.py ... --confirm`
#      as instagram_publish in ~/.tr_os/permission_policy.json
#   3. import into ~/TR_OS/skills/library and link with tros-skill-sync
#
#   bash tros/install.sh                          first install
#   bash tros/install.sh --update                 replace library copies with this checkout
#   bash tros/install.sh --project "/path/to/client project"   (repeatable)
#
# Run by the operator: sessions cannot write ~/TR_OS, ~/.claude/skills or ~/.tr_os.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tros="$HOME/TR_OS"
library="$tros/skills/library"
website="$HOME/THE RULIAD/TR_WEBSITE/theruliad_website"
update=0
projects=("$website")
while [ $# -gt 0 ]; do
  case "$1" in
    --update) update=1 ;;
    --project) shift; projects+=("${1:?--project needs a path}") ;;
    *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
[ -f "$tros/scripts/tros-skill-sync" ] || { echo "install.sh: no TR_OS at $tros" >&2; exit 1; }

stage="$(mktemp -d "${TMPDIR:-/tmp}/ig-stage.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
bash "$repo/tros/stage.sh" "$stage"
cp "$repo/tros/MANIFEST.sha256" "$stage/claude-user/MANIFEST.sha256"

echo "1. checking the staged files against tros/MANIFEST.sha256"
listed="$(sed -E 's/^[0-9a-f]{64}  //' "$repo/tros/MANIFEST.sha256" | LC_ALL=C sort)"
present="$(cd "$stage/claude-user" && find . -type f ! -name MANIFEST.sha256 | sed 's|^\./||' | LC_ALL=C sort)"
if [ "$listed" != "$present" ]; then
  echo "install.sh: the checkout's files differ from the reviewed list:" >&2
  diff <(echo "$listed") <(echo "$present") >&2 || true
  echo "Review the change, then run tros/make-manifest.sh and commit the manifest." >&2
  exit 1
fi
(cd "$stage/claude-user" && shasum -a 256 -c MANIFEST.sha256 --quiet) || {
  echo "install.sh: a file changed since review; see the lines above" >&2; exit 1; }
echo "   ok: $(echo "$present" | wc -l | tr -d ' ') files"

skills=()
for dir in "$stage"/claude-user/*/; do skills+=("$(basename "$dir")"); done

if [ "$update" = 1 ]; then
  for skill in "${skills[@]}"; do
    target="$library/$skill"
    [ -d "$target" ] || continue
    if grep -q "instagram-agent-skill-orchestrator" "$target/SOURCE.txt" 2>/dev/null; then
      rm -rf "$target"
    else
      echo "install.sh: $target exists and did not come from this pack; left alone" >&2
      exit 1
    fi
  done
fi

echo "2. configuring TR_OS"
config=(--sets "$tros/skills/sets.json" --policy "$HOME/.tr_os/permission_policy.json"
        --skills "$(IFS=,; echo "${skills[*]}")")
for project in "${projects[@]}"; do config+=(--project "$project"); done
python3 "$repo/tros/configure.py" "${config[@]}"

echo "3. importing and linking"
python3 "$tros/scripts/tros-skill-sync" --import "$stage"

echo
echo "Done. New Claude sessions in these projects list /ig-campaign and /seo-geo:"
printf '  %s\n' "${projects[@]}"
echo "A confirmed post (igapi.py ... publish ... --confirm) now waits on the instagram_publish gate."
