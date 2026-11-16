#!/usr/bin/env bash
# Store the access token that is on the clipboard for the client of the project
# this runs from, then show which account it reaches. First, in the Meta
# dashboard: The Ruliad Campaigns → Use cases → Instagram API → API setup with
# Unified login → step 2 → Generate token next to the handle → Copy. Then, from
# the client's project:
#
#   bash <this folder>/connect.sh IG_ACCOUNT_ID     (the id the dashboard shows under the handle)
#
# The token goes from the clipboard straight into igapi.py and is never printed;
# the clipboard is cleared afterwards. The outcome is written to connect.log in
# the client folder (igapi.py never prints a token, so the log cannot hold one).
set -uo pipefail
id="${1:?usage: connect.sh IG_ACCOUNT_ID  (the id the dashboard shows under the handle)}"
igapi="$(cd "$(dirname "$0")" && pwd)/igapi.py"
home="$(python3 "$igapi" --json where | python3 -c 'import json, sys; print(json.load(sys.stdin)["home"])')"
mkdir -p "$home"
log="$home/connect.log"
: > "$log"
token="$(pbpaste)"
if [ -z "$token" ]; then
  echo "The clipboard is empty: click Copy in Meta's 'Token generated' box, then run this again." | tee "$log"
  exit 1
fi
case "$token" in
  EAA*) ;;
  *) echo "The clipboard does not hold a Meta access token (those start with EAA). Copy it again." | tee "$log"
     exit 1 ;;
esac
echo "clipboard: a ${#token}-character token, for client folder $home" >> "$log"
printf '%s' "$token" | python3 "$igapi" connect --token-stdin \
  --host graph.facebook.com --user-id "$id" --system-user 2>&1 | tee -a "$log"
status=${PIPESTATUS[1]}
token=""
printf '' | pbcopy
[ "$status" = 0 ] && python3 "$igapi" whoami 2>&1 | tee -a "$log"
exit "$status"
