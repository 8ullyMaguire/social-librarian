#!/bin/sh
# The loop the systemd unit runs: mirror often, sweep once a day.
set -eu
cd "$(dirname "$0")/.."
STATE="${SOCIAL_LIBRARIAN_STATE:-$HOME/.local/share/social-librarian}"
PY="${PYTHON:-python3}"
log() { echo "$(date -Is) $*"; }

last_sweep=""
while :; do
  $PY -m librarian mirror --apply --limit 25 >>"$STATE/mirror.log" 2>&1 || log "mirror failed"
  today=$(date +%F)
  hour=$(date +%H)
  if [ "$today" != "$last_sweep" ] && [ "$hour" -ge 4 ]; then
    # Sweep is dry-run until the operator flips SOCIAL_LIBRARIAN_SWEEP_APPLY=1.
    if [ "${SOCIAL_LIBRARIAN_SWEEP_APPLY:-0}" = "1" ]; then
      $PY -m librarian sweep --apply >>"$STATE/sweep.log" 2>&1 || log "sweep failed"
    else
      $PY -m librarian sweep >>"$STATE/sweep-dry.log" 2>&1 || log "sweep (dry) failed"
    fi
    last_sweep="$today"
    log "sweep done for $today"
  fi
  sleep 120
done
