#!/usr/bin/env bash

db_endpoint() {
  local endpoint
  if ! endpoint=$(python3 -c '
import re
import sys
import urllib.parse as u

p = u.urlsplit(sys.argv[1])
host = p.hostname or ""
port = p.port
if not re.fullmatch(r"[a-zA-Z0-9_.:%-]+", host):
    sys.exit(1)
print(host + " " + str(port or ""))
' "$1" 2>/dev/null); then
    printf '%s\n' unparseable
    return 1
  fi
  printf '%s\n' "$endpoint"
}
