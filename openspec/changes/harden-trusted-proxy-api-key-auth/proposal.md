# Change: Harden trusted-proxy API-key auth

## Why

Public exposure through a local reverse proxy, such as Tailscale Funnel, makes
the raw socket peer look like loopback while the real client IP arrives in
`X-Forwarded-For`. If operators put `127.0.0.1/32` in
`proxy_unauthenticated_client_cidrs`, the API-key bypass must not treat every
Funnel request as local.

## What Changes

- Evaluate `proxy_unauthenticated_client_cidrs` against the trusted-proxy-aware
  resolved client IP instead of the raw socket peer.
- Trust forwarded client headers only when `firewall_trust_proxy_headers=true`
  and the socket peer is in `firewall_trusted_proxy_cidrs`.
- Preserve existing direct-client CIDR behavior when proxy header trust is off.
- Add a Tailscale Funnel operating runbook as OpenSpec context rather than
  feature documentation under `docs/`.

## Impact

Funnel and reverse-proxy deployments can be exposed publicly while requiring
`sk-clb-*` API keys for proxy traffic. Operators who intentionally allow a
known forwarded client CIDR can still do so by allowlisting the resolved public
or tailnet client range.
