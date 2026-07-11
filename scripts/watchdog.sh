#!/usr/bin/env bash
set -uo pipefail

# agent-lb launchd watchdog. Canonical source for ~/.agent-lb/bin/watchdog.sh
# (run by com.aneyman.agent-lb-watchdog every 30s).
#
# Recovery contract:
# - Healthy (/health -> 200): reset counters, exit.
# - Unhealthy but bootstrapped: kickstart after THRESHOLD consecutive failures,
#   unless the service process is younger than BOOT_GRACE_SECONDS — cold boot
#   takes 60-80+s under host load, and kicking a booting instance restarts the
#   clock and doubles the outage (observed 2026-07-11T20:24:50Z).
# - Not bootstrapped at all: re-bootstrap from the plist after
#   MISSING_THRESHOLD consecutive ticks. Intentional downtime must be signaled
#   by touching the pause file — an unloaded job alone is treated as a failed
#   deploy, not an operator decision (a bootout-without-bootstrap took the
#   service down for 10 minutes on 2026-07-11).
# - Service logs over SERVICE_LOG_MAX_MB rotate copy-then-truncate: launchd
#   holds an append-mode fd, so a rename would leave it writing to the old
#   inode until the next restart.

URL="${AGENT_LB_HEALTH_URL:-http://127.0.0.1:2455/health}"
LABEL="${AGENT_LB_LABEL:-com.aneyman.agent-lb}"
PLIST_FILE="${AGENT_LB_PLIST:-$HOME/Library/LaunchAgents/com.aneyman.agent-lb.plist}"
STATE_FILE="${AGENT_LB_WATCHDOG_STATE:-$HOME/.agent-lb/watchdog.state}"
PAUSE_FILE="${AGENT_LB_WATCHDOG_PAUSE:-$HOME/.agent-lb/watchdog.pause}"
LOG_FILE="${AGENT_LB_WATCHDOG_LOG:-$HOME/.agent-lb/watchdog.log}"
THRESHOLD="${AGENT_LB_WATCHDOG_THRESHOLD:-3}"
MISSING_THRESHOLD="${AGENT_LB_WATCHDOG_MISSING_THRESHOLD:-2}"
TIMEOUT="${AGENT_LB_WATCHDOG_TIMEOUT:-5}"
KICK_GRACE_SECONDS="${AGENT_LB_WATCHDOG_KICK_GRACE:-240}"
BOOT_GRACE_SECONDS="${AGENT_LB_WATCHDOG_BOOT_GRACE:-240}"
SERVICE_LOG_MAX_MB="${AGENT_LB_WATCHDOG_SERVICE_LOG_MAX_MB:-256}"
SERVICE_LOG_FILES="${AGENT_LB_WATCHDOG_SERVICE_LOG_FILES:-$HOME/.agent-lb/agent-lb.err.log $HOME/.agent-lb/agent-lb.out.log}"
FORENSICS_DIR="${AGENT_LB_FORENSICS_DIR:-$HOME/.agent-lb/forensics}"
FORENSICS_MAX_FILES="${AGENT_LB_FORENSICS_MAX_FILES:-50}"
FORENSICS_SAMPLE_SECONDS="${AGENT_LB_FORENSICS_SAMPLE_SECONDS:-2}"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(ts)" "$*" >> "$LOG_FILE"; }

# Touch this file to silence the watchdog without unloading the launchd job
# (planned downtime, debugging, deploys that bootout/bootstrap).
if [[ -f "$PAUSE_FILE" ]]; then
  exit 0
fi

rotate_service_logs() {
  local f size max_bytes
  max_bytes=$((SERVICE_LOG_MAX_MB * 1024 * 1024))
  for f in $SERVICE_LOG_FILES; do
    [[ -f "$f" ]] || continue
    size=$(stat -f %z "$f" 2>/dev/null || stat -c %s "$f" 2>/dev/null || echo 0)
    ((size > max_bytes)) || continue
    if cp -f "$f" "$f.1" 2>/dev/null; then
      : > "$f"
      rm -f "$f.1.gz"
      gzip -f "$f.1" 2>/dev/null || true
      log "rotated $f ($((size / 1024 / 1024))MB > ${SERVICE_LOG_MAX_MB}MB)"
    else
      log "rotation copy failed for $f — leaving in place"
    fi
  done
}
rotate_service_logs

# Pre-kick stall forensics: dump Python thread stacks (SIGUSR2) and an OS
# sample of the app process before it gets killed and restarted, so the
# evidence for what froze it survives the kick. Bounded to a few seconds
# (sample duration is fixed) and every failure is swallowed — this must
# never delay or block the kickstart.
capture_forensics() {
  local pid="$1" reason="$2"
  mkdir -p "$FORENSICS_DIR" 2>/dev/null || return 0
  printf '%s kick pid=%s reason=%s\n' "$(ts)" "${pid:-none}" "$reason" >> "$FORENSICS_DIR/events.log" 2>/dev/null

  if [[ -n "$pid" ]]; then
    kill -USR2 "$pid" 2>/dev/null
    sample "$pid" "$FORENSICS_SAMPLE_SECONDS" \
      -file "$FORENSICS_DIR/sample-$(date -u +%Y%m%dT%H%M%SZ).txt" >/dev/null 2>&1
  fi

  local total
  total=$(ls -1 "$FORENSICS_DIR" 2>/dev/null | wc -l | tr -d ' ')
  if [[ -n "$total" ]] && (( total > FORENSICS_MAX_FILES )); then
    ls -1t "$FORENSICS_DIR" 2>/dev/null | tail -n +"$((FORENSICS_MAX_FILES + 1))" | while IFS= read -r f; do
      rm -f "$FORENSICS_DIR/$f" 2>/dev/null
    done
  fi
  return 0
}

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

service_pid() {
  launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | \
    awk '/^[[:space:]]*pid = /{print $3; exit}'
}

# [[dd-]hh:]mm:ss from `ps -o etime=` -> seconds; empty on parse failure.
process_age_seconds() {
  local etime
  etime=$(ps -p "$1" -o etime= 2>/dev/null | tr -d ' ')
  [[ -n "$etime" ]] || return 0
  awk -F'[-:]' '{
    if (NF == 4) print $1*86400 + $2*3600 + $3*60 + $4;
    else if (NF == 3) print $1*3600 + $2*60 + $3;
    else if (NF == 2) print $1*60 + $2;
  }' <<<"$etime"
}

if (( count >= THRESHOLD )); then
  pid=$(service_pid)
  if [[ -n "$pid" ]]; then
    age=$(process_age_seconds "$pid")
    if [[ -n "$age" ]] && (( age < BOOT_GRACE_SECONDS )); then
      log "unhealthy (http=$http_code) count=$count but pid=$pid age=${age}s < boot grace ${BOOT_GRACE_SECONDS}s — waiting for boot"
      save_state
      exit 0
    fi
  fi
  capture_forensics "$pid" "unhealthy http=$http_code count=$count" || true
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
