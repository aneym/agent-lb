# Handoff — Anthropic provider for agent-lb

Date: 2026-05-28
Repo: `/Users/aneyman/repos/swap-lb`
Branch: `feat/anthropic-provider`
Remote: `upstream -> https://github.com/Soju06/codex-lb.git`

## Goal

Add Anthropic Claude as a second provider to agent-lb so one instance can load-balance multiple Claude Pro/Max OAuth accounts for Claude Code via `ANTHROPIC_BASE_URL`, while keeping the OpenAI path byte-identical and preparing the work for upstream contribution.

## Current State

CP0 through CP6 are implemented and committed. CP7 is in progress and blocked on real Claude account login/consent. The local runtime is healthy enough for Claude Code to reach `/v1/messages`, but there are currently zero active Anthropic accounts:

- `a.neyman17@gmail.com`: `rate_limited`
- `alex@prove-it.io`: `paused`

The latest local CP7 server was started at:

```bash
http://127.0.0.1:2456
```

with repo-local state:

```bash
/Users/aneyman/repos/swap-lb/.runtime/cp7/store.db
/Users/aneyman/repos/swap-lb/.runtime/cp7/encryption.key
```

The repaired Anthropic OAuth flow id was:

```text
nUcpgXj-lAHQaDRl
```

The matching Claude authorize URL used Claude Code's browser shape:

```text
https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference+user%3Asessions%3Aclaude_code+user%3Amcp_servers+user%3Afile_upload&code_challenge=apTiR9pwCxNwuXZAZ6C_x2WcZlWEkSBWEjjNfkVqJAk&code_challenge_method=S256&state=ZU7P7fh5B-6Hz95A5R8-p4CMY-mj94FzOFTdZjy0k-I
```

Chrome/CDP was left on Claude's Cloudflare human verification page. Do not automate the Cloudflare checkbox. Ask the user to complete the verification and consent, then continue CP7.

## Completed Commits

```text
247ad0c fix(anthropic): match claude code oauth url shape
bfa9e23 fix(anthropic): use claude manual oauth callback
84c8175 fix(anthropic): align oauth authorize flow
b1d37f3 fix(anthropic): harden real account runtime
9804700 feat(dashboard): add provider-aware account UI
ceda3a8 feat(anthropic): add messages proxy
a4afb28 feat(anthropic): add oauth provider flow
513b9e3 feat(anthropic): add protocol core
7cca30a feat(providers): add provider dispatch seam
365deac feat(db): add provider schema dimension
126e375 chore(goal): record baseline checkpoint
```

## Important Hard Rules

- Do not touch `app/modules/proxy/service.py` internals.
- Do not route Anthropic requests through OpenAI request rewriters.
- Do not strip inbound `anthropic-beta`.
- Do not strip or rewrite Claude Code's client system prompt.
- Do not commit runtime DBs, encryption keys, browser artifacts, or local agent scaffolding.
- Do not create PRs or take further GitHub actions unless the user explicitly asks.

## Recent Fixes Worth Knowing

- Anthropic OAuth now uses `https://platform.claude.com/oauth/code/callback` and accepts manual `code#state` callbacks.
- Anthropic authorize URLs now match Claude Code's observed shape:
  - `code=true` first
  - 43-character state token from `secrets.token_urlsafe(32)`
  - Claude Code scope set including `org:create_api_key`
- Anthropic no-account failures return a 503 JSON response before streaming starts.
- Inbound dummy `x-api-key` is not forwarded upstream.
- OpenAI OAuth and proxy behavior should remain unchanged.

## Verification Already Run

Focused backend checks after the final URL-shape repair:

```bash
uv run pytest -q tests/unit/test_provider_registry.py tests/unit/test_anthropic_oauth.py tests/integration/test_oauth_flow.py
uv run ruff check app/core/anthropic/oauth.py app/modules/oauth/service.py tests/unit/test_provider_registry.py tests/integration/test_oauth_flow.py
git diff -- app/modules/proxy/service.py
git diff --check
```

Results:

- `30 passed`
- Ruff passed
- `app/modules/proxy/service.py` diff empty
- `git diff --check` clean

Broader CP7 rerun before the OAuth-shape fix:

```bash
uv run pytest -q tests/integration/test_anthropic_proxy.py tests/unit/test_auth_manager.py tests/unit/test_provider_registry.py tests/unit/test_anthropic_oauth.py tests/unit/test_model_refresh_scheduler.py tests/unit/test_usage_updater.py tests/unit/test_usage_refresh_scheduler_recovery.py tests/unit/test_proxy_load_balancer_refresh.py tests/integration/test_oauth_flow.py
```

Result:

- `162 passed, 3 skipped` on rerun

Frontend checks:

```bash
cd frontend
bun run test src/features/accounts/hooks/use-oauth.test.ts src/features/accounts/components/oauth-dialog.test.tsx
bun run build
```

Results:

- Focused frontend tests passed
- Frontend build passed

## Next Agent Instructions

1. Confirm the server is still up:

   ```bash
   curl -sS http://127.0.0.1:2456/health
   curl -sS http://127.0.0.1:2456/api/accounts | jq
   ```

2. If the user has completed Claude login and has a `code#state` value, submit it to the existing flow:

   ```bash
   curl -sS -X POST http://127.0.0.1:2456/api/oauth/manual-callback \
     -H 'Content-Type: application/json' \
     -d '{"flowId":"nUcpgXj-lAHQaDRl","callbackUrl":"<code#state>"}' | jq
   ```

3. If token exchange fails, inspect the error before changing code. A likely next real-runtime mismatch is the Anthropic token exchange body/header shape. Claude Code appears to post JSON to its token endpoint; this fork may still need to match that exactly.

4. Once one active Anthropic account exists, open a second Anthropic OAuth flow if fewer than two active Anthropic accounts are available.

5. Once at least two active Anthropic accounts exist, run CP7 Stage B:

   - Set `ANTHROPIC_BASE_URL=http://127.0.0.1:2456`
   - Run at least 20 Claude Code requests.
   - Verify HTTP 200 streaming responses.
   - Verify distribution across at least two Anthropic accounts in request logs/dashboard.
   - Force or observe a 429 on one account and verify failover.
   - Verify Anthropic usage/cost includes input, output, cache creation, and cache read tokens.
   - Re-run a small OpenAI/Codex smoke through the same instance to prove regression safety.

6. Update `GOAL.md` Progress Log after the next CP7 movement.

## Local Dirty Files to Ignore

At handoff time these untracked files were present and intentionally not staged:

```text
.agents/hooks.json
.agents/skills/source-command-opsx-*/
.runtime/
excalidraw.log
```

## GitHub / Upstream

The user explicitly asked on 2026-05-28 to commit, push, and write this handoff. That authorizes this push of the current feature branch. It does not authorize opening upstream PRs or creating additional GitHub artifacts unless they ask again.
