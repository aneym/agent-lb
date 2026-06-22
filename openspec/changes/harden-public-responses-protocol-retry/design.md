## Context

Public OpenAI SDK-compatible Responses streams must begin with `response.created`, but that lifecycle event is not useful output by itself. Today the streaming retry layer marks any yielded event as downstream-visible, so an upstream attempt that emits only lifecycle events and then ends with `stream_incomplete` cannot be hidden or replayed. Hermes then receives a syntactically valid but semantically empty failed stream.

## Goals / Non-Goals

**Goals:**

- Retry transient public Responses stream attempts that fail before semantic output reaches the downstream client.
- Preserve streaming behavior once assistant text, tool-call data, or any non-lifecycle Responses event is emitted.
- Keep existing Codex backend routes and post-output no-replay invariants intact.

**Non-Goals:**

- Do not change account selection, stored-response ownership, or file-pinned routing semantics.
- Do not retry after user-visible output has been forwarded.
- Do not broaden this behavior to every internal stream surface.

## Decisions

- Treat lifecycle-only SSE events as bufferable for public OpenAI SDK streams. The public route can hold `response.created`, `response.in_progress`, and `response.queued` until either semantic output arrives, a successful terminal event arrives, or a retryable terminal failure proves the attempt was empty.
- Reuse the existing transient retry budget and account failover path. Protocol-only `stream_incomplete`, `server_error`, and request-timeout terminal failures should count like other transient stream errors, including same-account retries before account failover.
- Gate the behavior behind an explicit stream option passed only from OpenAI-contract public routes. Internal Codex routes continue to surface mid-stream failures after the first yielded event.
- Treat HTTP bridge first-event timeouts as replayable only when the request has emitted zero upstream/downstream events and is not bound to previous-response or preferred-account ownership. The bridge quarantines/excludes the silent account, retries once on a fresh eligible account, and only forwards `bridge_first_event_timeout` if that safe internal failover cannot recover.

## Risks / Trade-offs

- [Risk] Public streams may delay `response.created` until the first semantic event or terminal success. -> Mitigation: the delay only applies before semantic output and is bounded by existing stream attempt budgets and keepalives.
- [Risk] Retry could replay a request that upstream technically accepted but never produced useful output. -> Mitigation: replay is allowed only before any non-lifecycle event is forwarded downstream, matching the client-visible contract.
- [Risk] HTTP bridge active sessions may have separate replay mechanics. -> Mitigation: wire the same option through the bridge fallback path used for oversized payloads; keep bridge-native recovery behavior unchanged unless the bridge path is inactive.
- [Risk] First-event failover could cross account-bound state. -> Mitigation: failover is limited to first-turn requests with no preferred owner; previous-response and file-pinned requests keep the existing terminal timeout behavior.
