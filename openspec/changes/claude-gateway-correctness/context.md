# Operation and evidence

`clients/claude-desktop-gateway --print --route /path/to/clients/route` resolves the canonical workflow parent and implementation helper plus explicit `kimi-latest`. It fails startup if any resolution is absent. The Desktop IDs are compatibility selectors only; catalog labels and health routes expose actual upstream models. Native children may request those actual IDs directly.

The existing `scripts/install-claude-clients.sh` bundles the gateway. Run `claude-desktop-gateway --upstream https://studio.tailf266ac.ts.net:2455` behind the existing operator-owned loopback service/transport. Default port is 2466. This command does not modify Desktop settings or take over a listener. `--port` supports candidate validation. `AGENT_LB_API_KEY` is read only from the environment when required. Keep remote access authenticated outside the loopback gateway.

Parent uses the canonical workflow orchestrator stage, not quota-swapped `route resolve opus-latest`. The implementation helper follows canonical workflow routing. Kimi is an explicit choice, never an automatic replacement for Opus authority. Its current `k3.1-pro` pin is owned by routing-table.json, not the gateway.

The bridge corrects only a complete generated identity-reminder text block. It does not change client branding, rewrite arbitrary user strings, alter tools/history, or assert a knowledge cutoff. An authoritative identity statement is added to Responses instructions or as a final noncached system block for non-Claude Anthropic-compatible providers only; existing system blocks retain their order and cache metadata. Claude-native requests are returned unchanged. Older local gateway payload rewriting should be removed at cutover.

Desktop native functionality is client/provider dependent: native Claude Code tools can call non-Claude inference, but Claude artifact publication requires claude.ai authentication and the Anthropic provider; a gateway token cannot enable it. Do not advertise gateway artifact publication as verified.


## Candidate receipt (October 23, 2026)

Actual HTTP calls to live baseline 2455 and candidate 2482 demonstrate ToolSearch nonstream input was `{}` before and `{ "max_results": 1, "query": "screenshot" }` after; candidate streaming arguments parse to the same object. Repo-owned transport 2483 also delivered native Skill input `{ "skill": "screenshot" }` through the candidate. Identity HTTP reports actual `gpt-6.1-sol`, distinguishes the client, and preserves the literal user string `claude-opus-4-6`.

Raw proof: `/tmp/claude-gateway-http-proof.json`, `/tmp/claude-gateway-toolargs-2482-True.txt`, `/tmp/claude-gateway-transport-skill.json`, `/tmp/claude-gateway-transport-catalog.json`, `/tmp/claude-gateway-identity-candidate.json`. `./check` passed with 17 pre-existing lint findings (`/tmp/claude-gateway-check.txt`); strict OpenSpec validation passed (`/tmp/claude-gateway-openspec.txt`). Installer `--print` passed (`/tmp/claude-gateway-install-preview.txt`). Structural block invariants were checked in-process only, not asserted as HTTP acceptance (`/tmp/claude-gateway-block-invariants.json`).

The candidate used an isolated SQLite store with encrypted OpenAI account rows copied read-only from the actual local PostgreSQL store; stale home SQLite was not used as authoritative live state. Background refresh/pulse/redemption were disabled. Candidate listeners 2482/2483 were stopped after proof. Native Anthropic cache eval and Kimi inference were not run; after deployment the lead must run the documented cache eval and verify Kimi and native Opus. No commit, push, deployment, or main-checkout changes were performed by this seat. The completed-item-without-deltas branch was source-reviewed, not independently triggered by the real provider.
