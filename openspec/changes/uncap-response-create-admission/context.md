## Context

The prior stream-cap fix made active streams an opt-in cap while preserving them
as routing pressure. The remaining default response-create cap of 4 was intended
to protect the short upstream creation phase, but live burst testing showed it
is now the limiting local admission gate for bursty desktop agent workloads.

Response-create leases should still be acquired and released with Prometheus
lease counters, an active lease gauge, and pressure snapshots. Disabling the
default cap only removes the rejection threshold; it must not remove analytics,
routing pressure, stale lease recovery, or the explicit
`account_response_create_cap` path when an operator configures a nonzero cap.

Concrete example: with three usable OpenAI accounts and the old cap of 4, a
32-call burst could locally reject once roughly 12 response-create leases were
held. With the default cap set to `0`, that burst should continue through the
normal process-wide admission path while response-create lease counts remain
visible as account pressure.

The live macOS LaunchAgent has operator-specific runtime settings that are not
safe to replace with a generic template: Postgres is the source of truth,
dashboard auth is intentionally disabled for trusted local use, Tailnet client
CIDRs are allowlisted for remote proxy access, and the plist carries explicit
`--host 127.0.0.1 --port 2455` plus file descriptor limits. When the installer
adds metrics defaults, it must preserve those existing values and only fill
missing keys. Otherwise a routine restart can make the dashboard look empty,
make accounts appear logged out, and make Tailnet proxy clients fail with
`Proxy authentication must be configured before remote access is allowed` even
though the real account rows still exist in Postgres.

Breakage reports also need the running instance to say what posture it actually
booted with. A safe startup fingerprint should make the common "agent-lb broke
because of X" loop answerable from logs: database backend and data directory,
dashboard auth mode, trusted-proxy/unauthenticated CIDR counts, whether Tailnet
proxy access is configured, metrics availability and bind target, account-local
cap values, and HTTP bridge/schema posture. It must not log credentials, raw
database URLs, prompts, account tokens, API keys, request payloads, or raw
affinity keys.
