#!/usr/bin/env bash
set -uo pipefail

PLIST="$HOME/Library/LaunchAgents/sh.brew.postgresql@17.plist"
PGDATA="/opt/homebrew/var/postgresql@17"
PID=""
LOG="$HOME/.agent-lb/pg-qos-guard.log"
CHECK=0

while (($#)); do
  case "$1" in
    --plist|--pgdata|--pid|--log)
      if (($# < 2)); then
        printf 'missing value for %s\n' "$1" >&2
        exit 2
      fi
      case "$1" in
        --plist) PLIST="$2" ;;
        --pgdata) PGDATA="$2" ;;
        --pid) PID="$2" ;;
        --log) LOG="$2" ;;
      esac
      shift 2
      ;;
    --check) CHECK=1; shift ;;
    *) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

log() {
  mkdir -p "$(dirname "$LOG")" &&
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"
}

DRIFT=0
if [[ -f "$PLIST" ]] && [[ "$(plutil -extract ProcessType raw "$PLIST" 2>/dev/null)" != Interactive ]]; then
  DRIFT=1
  if (( ! CHECK )); then
    TEMP="$(mktemp "$PLIST.XXXXXX")" || exit 1
    trap 'rm -f "$TEMP"' EXIT
    cp -p "$PLIST" "$TEMP" &&
      plutil -replace ProcessType -string Interactive "$TEMP" &&
      plutil -lint "$TEMP" >/dev/null &&
      mv -f "$TEMP" "$PLIST" || exit 1
    trap - EXIT
    log 'plist ProcessType set Interactive; effective at next postgres bootstrap' || exit 1
  fi
fi

if [[ -z "$PID" && -f "$PGDATA/postmaster.pid" ]]; then
  IFS= read -r PID < "$PGDATA/postmaster.pid"
fi
if [[ ! "$PID" =~ ^[1-9][0-9]*$ ]]; then
  if (( CHECK )); then exit "$DRIFT"; fi
  exit 0
fi
COMM="$(ps -o comm= -p "$PID" 2>/dev/null)"
COMM="${COMM#"${COMM%%[![:space:]]*}"}"
if [[ "${COMM##*/}" != postgres ]]; then
  if (( CHECK )); then exit "$DRIFT"; fi
  exit 0
fi

PROCESSES="$(ps -ax -o pid=,ppid=,pri=)" || exit 1
SELECTED="$(awk -v parent="$PID" '($1 == parent || $2 == parent) && $3 <= 4 {print $1}' <<< "$PROCESSES")"
if (( CHECK )); then
  [[ -n "$SELECTED" ]] && DRIFT=1
  exit "$DRIFT"
fi

COUNT=0
POSTMASTER=no
FAILED=0
while IFS= read -r TARGET; do
  [[ -n "$TARGET" ]] || continue
  if taskpolicy -B -p "$TARGET"; then
    COUNT=$((COUNT + 1))
    [[ "$TARGET" == "$PID" ]] && POSTMASTER=yes
  else
    FAILED=1
  fi
done <<< "$SELECTED"
if (( COUNT )); then
  log "cleared background QoS for $COUNT processes; postmaster=$POSTMASTER" || exit 1
fi
exit "$FAILED"
