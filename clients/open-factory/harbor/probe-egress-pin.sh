#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
url="${OF_AGENT_LB_URL:-https://studio.tailf266ac.ts.net:2455}"
other_port="${PROBE_OTHER_PORT:-8950}"
python="${HARBOR_PYTHON:-$HOME/.local/share/uv/tools/harbor/bin/python}"
image="${EGRESS_SIDECAR_IMAGE:-$(docker images --format '{{.Repository}}:{{.Tag}}' | grep 'harbor-docker-egress-control-sidecar' | head -1)}"
[ -n "$image" ] || { echo "no harbor egress sidecar image on this host; run one allowlist trial first" >&2; exit 2; }

pin="$(cd "$here" && OF_AGENT_LB_URL="$url" "$python" -c 'import agents; print(agents.egress_pin_command())')"
host="$(OF_AGENT_LB_URL="$url" "$python" -c 'import os, urllib.parse; print(urllib.parse.urlsplit(os.environ["OF_AGENT_LB_URL"]).hostname)')"
scheme="${url%%://*}"
name="of-egress-probe-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT

docker run -d --name "$name" --cap-add NET_ADMIN --cap-add NET_RAW --init \
  --entrypoint /opt/egress-sidecar/entrypoint.sh \
  -e EGRESS_CONTROL_INITIAL_NETWORK_MODE=allowlist \
  -e "EGRESS_CONTROL_INITIAL_ALLOWED_HOSTS=host.docker.internal $host" \
  "$image" >/dev/null
for _ in $(seq 30); do
  docker exec "$name" test -f /tmp/harbor-docker-egress-control-sidecar.ready 2>/dev/null && break
  sleep 1
done

code() { docker run --rm --network "container:$name" curlimages/curl -sS -m 8 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || true; }
fail=0
expect() {
  local label="$1" target="$2" want="$3" got
  got="$(code "$target")"
  if { [ "$want" = open ] && [ "$got" != 000 ]; } || { [ "$want" = blocked ] && [ "$got" = 000 ]; }; then
    echo "ok   $label $target -> $got"
  else
    echo "FAIL $label $target -> $got (want $want)"
    fail=1
  fi
}

docker exec "$name" sh -c "$pin" >/dev/null
expect pinned "$url/health" open
expect pinned "$scheme://$host:$other_port/" blocked
expect pinned https://example.com/ blocked
expect pinned https://github.com/ blocked
docker exec "$name" sh -c "$pin" >/dev/null
rules="$(docker exec "$name" nft list chain inet gost_egress of_agent_lb_port | grep -c reject)"
[ "$rules" = 1 ] && echo "ok   repin keeps one port rule" || { echo "FAIL repin left $rules port rules"; fail=1; }
expect repinned "$url/health" open
exit "$fail"
