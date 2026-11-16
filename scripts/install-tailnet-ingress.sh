#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT=agent-lb-tailnet-ingress.service
CADDY="$HOME/.local/bin/caddy"
CONFIG="$HOME/.config/agent-lb"
ENV_FILE="$CONFIG/tailnet-ingress.env"
CADDYFILE="$CONFIG/tailnet-ingress/Caddyfile"
UNIT_FILE="$HOME/.config/systemd/user/$UNIT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

[[ "$(uname -s)" == Linux ]] || { echo "install-tailnet-ingress: Linux boxes only" >&2; exit 2; }
[[ -x "$CADDY" ]] || { echo "install-tailnet-ingress: caddy missing at $CADDY" >&2; exit 2; }
IP="$(tailscale ip -4 | head -1)"
if ! python3 -c 'import ipaddress,sys; sys.exit(0 if ipaddress.ip_address(sys.argv[1]) in ipaddress.ip_network("100.64.0.0/10") else 1)' "$IP"; then
  echo "install-tailnet-ingress: refusing $IP; only a tailnet address (100.64.0.0/10) may be bound" >&2
  exit 2
fi

put() {
  local src="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [[ -e "$dest" ]] && ! cmp -s "$src" "$dest"; then
    cp -p "$dest" "$dest.bak-$STAMP"
  fi
  cp "$src" "$dest"
}

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
printf 'AGENT_LB_TAILNET_IP=%s\nAGENT_LB_TAILNET_PORT=2455\nAGENT_LB_FRONT_PORT=2455\n' "$IP" > "$TMP"
put "$TMP" "$ENV_FILE"
put "$ROOT/deploy/tailnet-ingress/Caddyfile" "$CADDYFILE"
put "$ROOT/deploy/systemd/$UNIT" "$UNIT_FILE"
"$CADDY" validate --envfile "$ENV_FILE" --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1
systemctl --user daemon-reload
systemctl --user enable "$UNIT" >/dev/null
systemctl --user restart "$UNIT"
sleep 2
systemctl --user is-active --quiet "$UNIT"
curl -fsS -o /dev/null -m 5 "http://$IP:2455/health"
echo "$UNIT active on $IP:2455 -> 127.0.0.1:2455"
