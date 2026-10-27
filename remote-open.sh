#!/bin/sh
# bifrost remote opener
# `bifrost bind` installs this on a machine as ~/.local/share/bifrost/bin/xdg-open
# and names it in $BROWSER. While bifrost on a computer watches the machine, its
# watch.sh refreshes a heartbeat file every second. An http(s) URL or a file
# then goes to that computer and opens in its browser. Other schemes, a path
# that is not a file, or no watcher fall through to the real xdg-open.
spool="$HOME/.cache/bifrost"
heartbeat_max_age_s=3

open_request=
if [ $# -eq 1 ]; then
  case $1 in
    http://*|https://*|file://*) open_request=$1 ;;  # bifrost decodes file:// itself; dash cannot
    *) [ -f "$1" ] && open_request=$(readlink -f "$1") ;;
  esac
fi

is_watched() {
  now_s=$(date +%s)
  for heartbeat in "$spool"/watching.*; do
    [ -f "$heartbeat" ] || continue
    heartbeat_age_s=$(( now_s - $(stat -c %Y "$heartbeat") ))
    [ "$heartbeat_age_s" -le "$heartbeat_max_age_s" ] && return 0
  done
  return 1
}

if [ -n "$open_request" ] && is_watched; then
  umask 077
  request="$spool/open.$$.$(date +%s%N)"
  printf '%s\n' "$open_request" > "$request.partial" && mv "$request.partial" "$request" && exit 0
fi

self=$(readlink -f "$0")
IFS=:
for dir in $PATH; do
  candidate="$dir/xdg-open"
  if [ -x "$candidate" ] && [ "$(readlink -f "$candidate")" != "$self" ]; then
    # The real xdg-open honours $BROWSER, which may name this shim: unset it so
    # the fallback cannot loop back here.
    unset BROWSER
    exec "$candidate" "$@"
  fi
done
echo "xdg-open: no system xdg-open to fall back to" >&2
exit 3
