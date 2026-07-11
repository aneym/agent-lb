#!/usr/bin/env bash
set -uo pipefail

# agent-lb launchd watchdog. Canonical source for ~/.agent-lb/bin/watchdog.sh
# (run by com.aneyman.agent-lb-watchdog every 30s).
#
# Recovery contract:
# - Healthy (/health -> 200): reset counters, exit.
# - Unhealthy but bootstrapped: kickstart after THRESHOLD consecutive failures.
# - Not bootstrapped at all: re-bootstrap from the plist after
#   MISSING_THRESHOLD consecutive ticks. Intentional downtime must be signaled
#   by touching the pause file — an unloaded job alone is treated as a failed
#   deploy, not an operator decision (a bootout-without-bootstrap took the
#   service down for 10 minutes on 2026-07-11).

URL="${AGENT_LB_HEALTH_URL:-http://127.0.0.1:2455/health}"
LABEL="${AGENT_LB_LABEL:-com.aneyman.agent-lb}"
PLIST_FILE="${AGENT_LB_PLIST:-$HOME/Library/LaunchAgents/com.aneyman.agent-lb.plist}"
STATE_FILE="${AGENT_LB_WATCHDOG_STATE:-$HOME/.agent-lb/watchdog.state}"
PAUSE_FILE="${AGENT_LB_WATCHDOG_PAUSE:-$HOME/.agent-lb/watchdog.pause}"
LOG_FILE="${AGENT_LB_WATCHDOG_LOG:-$HOME/.agent-lb/watchdog.log}"
THRESHOLD="${AGENT_LB_WATCHDOG_THRESHOLD:-3}"
MISSING_THRESHOLD="${AGENT_LB_WATCHDOG_MISSING_THRESHOLD:-2}"
TIMEOUT="${AGENT_LB_WATCHDOG_TIMEOUT:-5}"
KICK_GRACE_SECONDS="${AGENT_LB_WATCHDOG_KICK_GRACE:-60}"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(ts)" "$*" >> "$LOG_FILE"; }

# Touch this file to silence the watchdog without unloading the launchd job
# (planned downtime, debugging, deploys that bootout/bootstrap).
if [[ -f "$PAUSE_FILE" ]]; then
  exit 0
fi

count=0
last_kick=0
missing=0
if [[ -f "$STATE_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$STATE_FILE" 2>/dev/null || true
fi
count="${count:-0}"
last_kick="${last_kick:-0}"
missing="${missing:-0}"

save_state() {
  printf 'count=%d\nlast_kick=%d\nmissing=%d\n' "$count" "$last_kick" "$missing" > "$STATE_FILE"
}

# Job unloaded from launchd: revive it after MISSING_THRESHOLD consecutive
# ticks (grace window for a deploy that bootstraps right back).
if ! launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  missing=$((missing + 1))
  if (( missing >= MISSING_THRESHOLD )); then
    if [[ -f "$PLIST_FILE" ]]; then
      log "service not bootstrapped (missing=$missing >= $MISSING_THRESHOLD) and no pause file — bootstrapping $PLIST_FILE"
      launchctl bootstrap "gui/$(id -u)" "$PLIST_FILE" >> "$LOG_FILE" 2>&1 || \
        log "bootstrap failed with exit $?"
      missing=0
      last_kick=$(date +%s)
    else
      log "service not bootstrapped and plist missing at $PLIST_FILE — cannot revive"
    fi
  else
    log "service not bootstrapped (missing=$missing) below threshold=$MISSING_THRESHOLD — waiting"
  fi
  count=0
  save_state
  exit 0
fi
missing=0

http_code=$(curl -sS -o /dev/null -m "$TIMEOUT" -w '%{http_code}' "$URL" 2>/dev/null) || true
http_code="${http_code:-000}"

if [[ "$http_code" == "200" ]]; then
  if (( count > 0 )); then
    log "recovered (http=200) after count=$count"
  fi
  count=0
  save_state
  exit 0
fi

count=$((count + 1))
now=$(date +%s)

if (( now - last_kick < KICK_GRACE_SECONDS )); then
  save_state
  log "unhealthy (http=$http_code) count=$count within kick grace (last_kick=$last_kick) — skipping"
  exit 0
fi

if (( count >= THRESHOLD )); then
  log "unhealthy (http=$http_code) count=$count >= threshold=$THRESHOLD — kickstarting $LABEL"
  launchctl kickstart -k "gui/$(id -u)/$LABEL" >> "$LOG_FILE" 2>&1 || \
    log "kickstart failed with exit $?"
  count=0
  last_kick=$now
  save_state
else
  log "unhealthy (http=$http_code) count=$count below threshold=$THRESHOLD"
  save_state
fi
