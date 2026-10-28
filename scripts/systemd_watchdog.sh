#!/bin/bash
set -uo pipefail

STATE_DIR="${AGENT_LB_WATCHDOG_DIR:-$HOME/.agent-lb-primary}"
PAUSE_FILE="$STATE_DIR/watchdog.pause"
LOG_FILE="$STATE_DIR/watchdog.log"
THRESHOLD="${AGENT_LB_WATCHDOG_THRESHOLD:-3}"
TIMEOUT="${AGENT_LB_WATCHDOG_TIMEOUT:-5}"
KICK_GRACE="${AGENT_LB_WATCHDOG_KICK_GRACE:-240}"
BOOT_GRACE="${AGENT_LB_WATCHDOG_BOOT_GRACE:-240}"
DRAIN_HOLD="${AGENT_LB_WATCHDOG_DRAIN_HOLD:-900}"
DRAIN_STATUS_URL="${AGENT_LB_WATCHDOG_DRAIN_STATUS_URL:-http://127.0.0.1:2477/internal/drain/status}"

[[ -f "$PAUSE_FILE" ]] && exit 0
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG_FILE"; }

draining() {
  curl -sS -m "$TIMEOUT" "$DRAIN_STATUS_URL" 2>/dev/null | grep -q '"draining":"true"' || return 1
  (( drain_since > 0 )) || drain_since=$now
  (( now - drain_since < DRAIN_HOLD ))
}

check() {
  local unit="$1" url="$2" state="$STATE_DIR/watchdog-$1.state"
  local count=0 last_kick=0 drain_since=0 code now pid age
  # shellcheck source=/dev/null
  [[ -f "$state" ]] && source "$state"
  code=$(curl -sS -o /dev/null -m "$TIMEOUT" -w '%{http_code}' "$url" 2>/dev/null) || true
  code="${code:-000}"
  now=$(date +%s)
  if [[ "$code" == "200" ]]; then
    (( count > 0 )) && log "$unit recovered after count=$count"
    count=0
  elif draining; then
    log "$unit http=$code while the primary drains ($(( now - drain_since ))s of ${DRAIN_HOLD}s); not counting"
    count=0
  elif ! systemctl --user is-active -q "$unit"; then
    log "$unit not active (http=$code); systemd Restart= owns it"
    count=0
  else
    count=$((count + 1))
    age=$(( now - $(date -d "$(systemctl --user show -p ActiveEnterTimestamp --value "$unit")" +%s 2>/dev/null || echo "$now") ))
    if (( now - last_kick < KICK_GRACE )); then
      log "$unit unhealthy http=$code count=$count within kick grace"
    elif (( age < BOOT_GRACE )); then
      log "$unit unhealthy http=$code count=$count but up ${age}s < boot grace"
    elif (( count >= THRESHOLD )); then
      if [[ "$unit" == agent-lb ]] && ! pg_isready -q -h 127.0.0.1 -p 5432 -t 3; then
        log "$unit unhealthy http=$code count=$count but postgres not ready; not restarting"
      else
        pid=$(systemctl --user show -p MainPID --value "$unit")
        log "$unit unhealthy http=$code count=$count >= $THRESHOLD; restarting (pid=$pid)"
        systemctl --user restart --no-block "$unit" >> "$LOG_FILE" 2>&1 || log "restart $unit failed rc=$?"
        count=0
        last_kick=$now
      fi
    else
      log "$unit unhealthy http=$code count=$count below threshold"
    fi
  fi
  [[ "$code" == "200" ]] && drain_since=0
  printf 'count=%d\nlast_kick=%d\ndrain_since=%d\n' "$count" "$last_kick" "$drain_since" > "$state"
}

check agent-lb http://127.0.0.1:2477/health
check agent-lb-front http://127.0.0.1:2455/health
