# Local-First Instance Federation with Account Checkout

## Why

Today all clients route through the single agent-lb on studio over Tailscale.
On constrained networks (plane wifi blocking direct paths), every request rides
a DERP relay (~2.5s RTT observed 2026-07-02) — sessions work only with widened
launcher timeouts and are slow; if the relay also fails, routing is lost
entirely and the launcher falls back to plain claude.

Requests should run directly from the machine the session is on to Anthropic,
with the account pool shared across a small personal fleet (studio, laptop).

The gating constraint, confirmed in code: **Anthropic rotates refresh tokens on
every refresh** (`app/core/anthropic/oauth.py` requires and stores a new
`refresh_token` per refresh; `refresh_token_reused`/`refresh_token_invalidated`
are permanent failures forcing re-login). Refreshes run proactively every ~90
minutes. Two instances independently refreshing the same account will corrupt
it within about an hour. Refresh serialization is currently in-process only
(asyncio singleflight); SQLite leader election short-circuits to always-leader.
Naive DB copying or DB sharing across instances is therefore unsafe.

Access-token *use* is safe from any number of machines; only *refresh* must be
exclusive. Usage windows, cooldowns, and quota markers are derived from
Anthropic response headers/endpoints, so they self-heal per instance.

## What Changes

- **Ownership model.** Each account gains a single `owner_instance` (default:
  studio). Only the owner refreshes the account's tokens. Non-owner instances
  MUST NOT refresh; they hold mirrored tokens for direct use while fresh.
- **Token mirroring.** A non-owner instance periodically pulls current access
  tokens (not refresh authority) for accounts it mirrors from the owner over
  the existing authenticated HTTP API, while the owner is reachable.
- **Checkout / checkin.** An operator command transfers refresh authority for
  selected accounts to another instance (e.g. laptop before a flight). The
  previous owner atomically stops refreshing before the new owner starts.
  Checkin returns authority and syncs the rotated refresh token back. Stale
  double-owner states MUST be impossible by construction (single handshake,
  owner disables before transfer completes).
- **Launcher preference order.** The launcher prefers a healthy local instance
  (`127.0.0.1`), then the configured remote LB, then plain claude. Existing
  sessions keep their claimed route; preference applies to new sessions only.
- **Degraded mode.** A non-owner with unmirrored/expired tokens and an
  unreachable owner excludes those accounts from selection (they are not
  routable, not corrupted).

## Impact

- Affected specs: new `instance-federation` capability; `account-routing`
  (launcher preference order).
- Affected code: `app/modules/accounts/**` (ownership + mirror/transfer
  endpoints), scheduler refresh gating, `clients/claude-lb-launch`
  (preference order), Alembic migration (`owner_instance` column), CLI/skill
  for checkout/checkin.
- Rollout is additive and interruption-free: single-instance deployments have
  every account owned by the sole instance and behave exactly as today.

## Non-Goals

- Multi-writer shared databases across instances (unsafe on SQLite; not needed).
- Automatic ownership failover (explicit checkout only — rotation semantics
  make optimistic auto-transfer dangerous).
- OpenAI/GLM providers in the first iteration (Anthropic first; same model
  generalizes).
