# Issues log — add-session-analytics

Continues the log from `add-claude-session-map/notes.md`. Timestamps UTC.

1. **2026-07-15 ~16:50 — Sol seat traffic invisible in the session map.**
   Owner screenshot review exposed it: the session detail showed only
   `claude-fable-5` despite heavy implementer/verifier seat usage. Root
   cause: Sol-alias `/v1/messages` requests route through the HTTP bridge,
   and `streaming.py:493` (plus 4 retry sites) overwrites
   `request_state.session_id` with the synthesized
   `http_turn_<hex>` from `ensure_http_downstream_turn_state()` — which
   checks codex turn-state headers only, never the Claude session headers or
   metadata. The v1 rollup's synthetic-id exclusion then (correctly) filters
   those rows out. Verified live: post-restart sol smoke logged
   `session='http_turn_…'` while carrying `claude-cli` UA.
   Design lesson recorded: in-process subagents share the parent's session
   identity end to end — the LB-side loss happened in the bridge, not the
   client.

2. **2026-07-15 — no per-agent identity exists LB-side.** Model + reasoning
   effort is the only seat dimension the proxy can see (confirmed
   `reasoningEffort` populated on all sol rows). True subagent-instance
   counts require client-side transcript enrichment — backlogged; the
   fleet-monitor analyzer already derives dispatch counts from transcripts
   and is the prior art.
