## Why

When an Anthropic session's pinned account approaches its 5-hour (primary) window
mid-session, the balancer should migrate the session to an account with headroom
*before* the upstream 429 wall — while respecting prompt-cache locality (Anthropic
prompt caches are org-scoped, so every account switch forces one full cache
rebuild on the new account).

The machinery mostly exists, but has two gaps and one client-side inefficiency:

1. **Proactive rebind is burn-first-gated.** `_select_with_stickiness` rebinds a
   budget-pressured pin (primary used% above the sticky threshold) only when a
   selectable `burn_first` candidate exists. Fable-class traffic never stamps
   `burn_first` (the weekly filter narrows eligibility instead), so with no
   stored burn-first accounts a Fable session rides its pinned account to
   exhaustion, then pays a failed upstream call + reactive cooldown failover.
2. **The reactive path's cache-aware re-pin is load-bearing but untested.** On a
   429, the cooled-down pin drops out of eligibility, the mapping is deleted, and
   the fallback that serves the response is persisted as the new durable pin
   (one cache rebuild, no flapping). No regression test pins this contract.
3. **Headless launcher turns scatter.** `claude-lb-launch` mints a fresh session
   id (`cc-<pid>-<epoch>`) per invocation, so looped `claude -p` runs claim a new
   sticky route each turn and spread across accounts, discarding the prompt
   cache their shared prefixes just wrote.

## What Changes

- New setting `ANTHROPIC_STICKY_HEADROOM_REALLOCATION_ENABLED` (default true).
- `LoadBalancer.select_account`/`_select_with_stickiness` gain a
  `headroom_reallocate` flag (passed only by the Anthropic service, gated on the
  new setting). Under budget pressure, when no burn-first candidate exists, the
  pin rebinds to the best budget-safe eligible account instead of being kept.
  The existing anti-thrash guard is preserved: if the pool's best candidate is
  the pinned account itself or is also above threshold, the pin is kept.
  Rebinding persists the new mapping (one-time re-pin; no flap-back when the old
  account's window resets).
- Regression tests for the reactive contract: after an in-request 429 failover,
  the durable session mapping points at the account that served the successful
  response.
- Launcher: headless (`-p`/`--print`) invocations without `CLAUDE_LB_SESSION_ID`
  derive a stable session id from the working directory instead of pid+epoch, so
  consecutive loop turns share account affinity and prompt cache. Interactive
  invocations keep per-process ids.

## Impact

- Sessions migrate off exhausting accounts at the configured threshold (default
  95% primary) instead of at the 429 wall — no failed round-trip, no mid-session
  stall, all accounts get used as headroom shifts.
- Cache locality is preserved by construction: at most one rebuild per
  migration, sessions never bounce back, headless loops stop scattering.
- OpenAI/Codex selection is unchanged (flag is Anthropic-only).
- Affected code: `app/core/config/settings.py`, `app/modules/proxy/load_balancer.py`,
  `app/modules/proxy/anthropic_service.py`, `clients/claude-lb-launch`, tests.
