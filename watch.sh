# Runs on the saved machine as `sh -c <this script> bifrost-watch MIN_PORT WATCHER_ID`.
# The bifrost daemon writes "tick SELECTED" on stdin once a second. Each tick
# rescans the listeners and the open queue, so this loop stops as soon as the
# daemon's connection does. Output lines:
#   ports PORT/FAMILY/PROCESS_NAME ...   the listener set changed
#   open REQUEST                         a URL or file path remote-open.sh queued
#   (empty line)                         nothing changed; proves the loop is alive
#   fatal REASON                         this machine cannot be watched
min_port=$1
watcher_id=$2
[ -r /proc/net/tcp ] || { echo "fatal needs Linux with /proc/net/tcp"; exit 1; }
umask 077
spool="$HOME/.cache/bifrost"
mkdir -p "$spool" && chmod 700 "$spool"
uid=$(id -u)
listener_tables=/proc/net/tcp
[ -r /proc/net/tcp6 ] && listener_tables="$listener_tables /proc/net/tcp6"

# One awk program for both tables. A heredoc would not do: dash hands it over a
# pipe, which only the first awk could read.
# Only this user's sockets on loopback or a wildcard address count, so system
# services never get offered.
# shellcheck disable=SC2016 # awk expands these, not the shell
listeners_program='
function hex(s,  i, n) { for (i = 1; i <= length(s); i++) n = n * 16 + index("0123456789ABCDEF", substr(s, i, 1)) - 1; return n }
FNR > 1 && $4 == "0A" && $8 == uid {
  family = (FILENAME ~ /tcp6$/) ? 6 : 4
  split($2, address, ":")
  ip = address[1]
  if (ip ~ /^0+$/ || (family == 4 && ip ~ /7F$/) || ip == "00000000000000000000000001000000") {
    port = hex(address[2])
    if (port >= min) print port "/" family
  }
}'

previous="unset"
while read -r command selected; do
  [ "$command" = tick ] || continue
  : > "$spool/watching.$watcher_id"
  now_s=$(date +%s)
  for request in "$spool"/open.*; do
    [ -f "$request" ] || continue
    case $request in *.partial) continue ;; esac
    if [ "$selected" != 1 ]; then
      # Give the computer whose herdr shows this machine two seconds to claim it.
      request_age_s=$(( now_s - $(stat -c %Y "$request" 2>/dev/null || echo "$now_s") ))
      [ "$request_age_s" -ge 2 ] || continue
    fi
    claimed="$spool/claimed.$watcher_id"
    # mv claims the request, so a second watching computer skips it.
    mv "$request" "$claimed" 2>/dev/null || continue
    # First line only, minus control characters, so a crafted request cannot
    # add protocol lines.
    open_request=$(head -n 1 "$claimed" | tr -d '\000-\037\177')
    rm -f "$claimed"
    [ -n "$open_request" ] && printf 'open %s\n' "$open_request"
  done
  # Family 4 wins over 6 when a port listens on both, because the forward then
  # targets 127.0.0.1.
  # shellcheck disable=SC2086 # one or two table paths, split on purpose
  listeners=$(awk -v uid="$uid" -v min="$min_port" "$listeners_program" $listener_tables 2>/dev/null \
    | sort -t/ -k1,1n -k2,2n | awk -F/ '!seen[$1]++')
  if [ "$listeners" = "$previous" ]; then
    echo
  else
    line=ports
    for listener in $listeners; do
      port=${listener%/*}
      process_name=$(ss -Hltnp "sport = :$port" 2>/dev/null | sed -n 's/.*users:(("\([^"]*\)".*/\1/p' | head -n 1 \
        | tr -c 'A-Za-z0-9._:\n-' '_' | cut -c1-32)
      line="$line $listener/${process_name:-unknown}"
    done
    printf '%s\n' "$line"
    previous=$listeners
  fi
done
rm -f "$spool/watching.$watcher_id"
