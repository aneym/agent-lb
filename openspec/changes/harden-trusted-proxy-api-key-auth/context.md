# Tailscale Funnel public proxy runbook

## Purpose

Expose agent-lb through Tailscale Funnel while keeping proxy traffic protected
behind `sk-clb-*` API-key authentication.

## How Funnel reaches agent-lb

Tailscale Funnel terminates HTTPS at the Tailscale edge and forwards plain HTTP
to the local agent-lb port. The socket peer that agent-lb sees is the local
forwarder, commonly `127.0.0.1`, while the real public client IP is carried in
`X-Forwarded-For`.

The important constraint is that `proxy_unauthenticated_client_cidrs` must be
evaluated against the trusted-proxy-aware resolved client IP. Otherwise, adding
`127.0.0.1/32` to that allowlist would make every public Funnel request look
local and unauthenticated.

## Required agent-lb settings

Enable API-key authentication before exposing Funnel. Set this in the dashboard
or via the settings API; there is no environment variable for this switch.

Use tight trusted-proxy settings:

```bash
AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS=true
AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS=127.0.0.1/32
```

Leave the unauthenticated proxy CIDR list empty unless you intentionally want a
specific resolved public or tailnet client range to bypass API keys:

```bash
# Do not set this to 127.0.0.1/32 for Funnel.
# AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS=
```

## Tailscale commands

Configure Tailscale Serve first, then enable Funnel:

```bash
tailscale serve --bg https / http://127.0.0.1:2455
tailscale funnel --bg https 443 on
tailscale serve status
tailscale funnel status
```

## Verification

First confirm local health:

```bash
curl -s http://127.0.0.1:2455/health
```

Then confirm proxy API-key enforcement through the public Funnel hostname:

```bash
curl -s https://MY_HOST.tailXXXX.ts.net/v1/models
# Expected: 401 with error.code = invalid_api_key

curl -s https://MY_HOST.tailXXXX.ts.net/v1/models \
  -H "Authorization: Bearer $AGENT_LB_API_KEY"
# Expected: 200 with a models list
```

Confirm spoofed forwarded headers do not bypass auth:

```bash
curl -s https://MY_HOST.tailXXXX.ts.net/v1/models \
  -H "X-Forwarded-For: 127.0.0.1"
# Expected: 401 with error.code = invalid_api_key
```

## Rollback

Disable public exposure immediately:

```bash
tailscale funnel off
tailscale serve reset
```

For local-only operation, turn proxy header trust back off and restart
agent-lb:

```bash
AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS=false
```

## Failure modes

- If API-key auth is disabled and no specific resolved client CIDR is
  allowlisted, non-local proxy requests fail closed with 401.
- If `firewall_trust_proxy_headers=false`, forwarded headers are ignored and
  direct-client CIDR behavior remains socket-peer based.
- If `firewall_trusted_proxy_cidrs` is too broad, untrusted peers may be able to
  influence resolved client IPs. Keep the list as narrow as possible.
