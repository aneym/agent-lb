#!/usr/bin/env bash
# Shared boot/watchdog parser. Never emit a URL or parser diagnostics.
db_endpoint() {
  local endpoint host port
  if ! endpoint=$(python3 -c 'import sys,urllib.parse as u; p=u.urlsplit(sys.argv[1]); print((p.hostname or "") + " " + str(p.port or ""))' "$1" 2>/dev/null); then
    printf '%s\n' unparseable
    return 1
  fi
  read -r host port <<<"$endpoint"
  # Reject whitespace/control characters before endpoint data can reach logs.
  if [[ -z "$host" || "$host" == *[!a-zA-Z0-9_.:%-]* ]]; then
    printf '%s\n' unparseable
    return 1
  fi
  printf '%s\n' "$endpoint"
}
