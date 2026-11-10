# Thread pins across routers

A thread's prompt cache belongs to one account. Each router (the primary on ax42 and every box edge) keeps its own `sticky_sessions` table, so when Caddy sent a thread from a box edge to the primary during an edge deploy, the primary had no pin and picked another account; every warm thread then paid a full cache write. The 2026-11-10 audit counted 25 Fable and 94 Opus 5.5 rewrites from these hops in 4.5 hours.

## Design: pins ride the existing federation link

Each edge already talks to the primary over federation (mirror pull, usage report, request-log forward). It now also runs `StickyPinSync` (`app/modules/federation/sticky_pin_sync.py`) every 10 s:

1. Push: the edge sends the pins it bound since its last exchange to `POST /api/federation/sticky-pins` (mirror token).
2. Pull: in the same exchange it asks for the primary's pins bound since the last answer and applies them.

Rules (`app/modules/federation/sticky_pin_store.py`):

- A pin's `updated_at` is its bind time; Anthropic pins are not touched on reuse, so the newest bind is the newest routing decision. The newer bind wins; a received pin keeps its original bind time, so the two sides never ping-pong.
- Times cross the wire in UTC. The primary's Postgres session runs in Europe/Berlin, so the store converts on read and write.
- A pin to an account the receiver does not have is skipped.
- On an edge's first exchange (no `federation-sticky-pin-seed.json` in its data dir) it sends every pin with `seed`, and the primary takes the edge's account for each key. Edge pins describe where box threads live now; older primary pins come from earlier hops.
- The edge exchanges once before it starts serving (5 s cap) and once while shutting down (3 s cap), so a pin bound just before a deploy reaches the primary and one bound by the primary during the deploy is on the edge when it comes back. Both cursors live in `federation-sticky-pin-seed.json`, so a restart trades only what changed while it was down (never more than 6 h).
- Only `codex_session` and `sticky_thread` pins sync; `prompt_cache` pins are age-limited and stay local.

The give-up rules are unchanged: the router that serves a request runs `_select_with_stickiness` on the shared pin, so an ineligible, rate-limited, exhausted or briefly unselectable account still moves the thread, and that rebind reaches the other side on the next exchange.

Chosen over the alternatives: asking the peer for a pin on a miss fails exactly when it is needed (the edge is down during its own deploy); one shared store would make every edge depend on the primary's database for every request. The exchange needs no new service, table or setting.

## The 503 window on edge deploys

`agent-lb-edge-follow` drains the edge (`/health/ready` turns 503 so new requests go to the primary while streams finish), restarts it, and waits for health. Removing that window means running the new process beside the old one on a second port and teaching Caddy to swap, which is not cheap. With pins shared, a request served by the primary during the window stays on its account, so the window is left as it is.

## Cache lifetime on request rows

`cache_creation_tier` now records the cache lifetime every Anthropic request asked for (`5m`, `1h`, or `mx` when breakpoints mix), not only the helper requests agent-lb upgrades to 1 h, and edges forward it with their request rows. `cost_usd` prices `1h` writes at the 1-hour rate; `mx` still prices at the 5-minute rate.
