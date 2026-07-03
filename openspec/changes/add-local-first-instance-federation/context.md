# Context — rollout & cutover runbook

All code for this change is merged and validated (unit matrix + localhost
two-instance exercise, `scripts/two-instance-exercise.sh`, 21/21). What
remains is operational rollout — task 5.2. **Do not perform the studio steps
over a degraded network path (relay-routed Tailscale)**: a failed service
restart with no physical access loses routing until someone can reach the box.

## Cutover steps (stable network required)

1. **Studio** (owner instance): restart `com.aneyman.agent-lb` (kickstart -k,
   per ops memory) so the running process picks up the federation code and
   applies migrations `20260702_000000` + `20260703_000000`. Verify `/health`
   and that `/api/federation/mirror` returns 403 unauthenticated. Behavior is
   otherwise unchanged: every account has `owner_instance = NULL` (owned).
2. **Shared secret**: generate one token; set `AGENT_LB_FEDERATION_TOKEN` in
   studio's service environment and the laptop's.
3. **Laptop** (this MacBook): stand up a local instance per GETTING-STARTED
   (launchd service on 127.0.0.1:2455) with
   `AGENT_LB_LOCAL_INSTANCE_ID=<name>`, `AGENT_LB_FEDERATION_TOKEN=<token>`,
   `AGENT_LB_FEDERATION_PEER_URL=https://studio.tailf266ac.ts.net:2455`.
   Within ~5 min the mirror pull materializes studio's accounts as mirrors
   (dashboard shows "mirror: studio" badges); the launcher automatically
   prefers the local instance for new sessions (`CLAUDE_LB_LOCAL_URL` default)
   and falls through to studio when the local claim can't be served.
4. **Live checkout validation** (one LOW-VALUE account first):
   `clients/agent-lb-federation checkout <account-id>` on the laptop; verify
   the laptop refreshes it (wait past token expiry or force via dashboard),
   then `checkin`, then verify studio's next refresh of that account succeeds
   (no `refresh_token_reused`). That closes task 5.2.
5. **Pre-flight ritual** (before losing good connectivity): checkout the
   accounts you want; on return, checkin. Ambiguous states never double-
   refresh — worst case an account is temporarily excluded everywhere and a
   retry or operator force resolves it.

## Existing-session safety

Cutover interrupts nothing: running sessions keep their claimed endpoint and
route; the local-first preference applies only to newly launched sessions.
The studio restart itself briefly drops in-flight streams (the resilient
launcher absorbs it) — prefer a quiet moment.
