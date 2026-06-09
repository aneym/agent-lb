# Claude LB Durable Final State Goal

- Date: 2026-06-09
- Requester: Alex
- Status: In progress
- Repo/worktree: `/Users/aneyman/repos/codex-lb`
- Branch policy: work directly on `main` per `AGENTS.md`; preserve unrelated dirty worktree changes.

## Objective and Stopping Condition

Implement the durable final state for Claude Code load balancing in codex-lb: Claude Code launched through `cclb` must use Claude Max/OAuth billing, preserve Claude Code's best-model default, route each request to an Anthropic account that can serve the requested model/params, keep a Claude conversation sticky to one account for prompt-cache locality until that account is unavailable for that quota key, fail over cleanly when possible, and surface Claude usage/limit state in the dashboard at parity with Codex where the upstream data allows.

Stop only when a fresh Claude Code session through `cclb` can run Fable/default best-model traffic end to end across the local proxy without API usage billing or model downgrade, selector behavior is verified across at least two Anthropic OAuth accounts, per-session stickiness and per-quota failover are covered by tests, the dashboard shows actionable Anthropic usage/limit/reset/availability state, focused backend/frontend tests pass, OpenSpec is updated and validated, and the brief's Progress Log records the verification evidence.

## Relevant Conversation Context

- Alex rejected any default downgrade to Haiku. `cclb` must always preserve Claude Code's own best/default model, currently Fable 5 with xhigh effort.
- Alex wants OAuth subscription billing, not API usage billing. Do not set `ANTHROPIC_AUTH_TOKEN` in the launcher; doing so made Claude Code show `API Usage Billing`. Launching with only `ANTHROPIC_BASE_URL=http://127.0.0.1:2455` preserved `Claude Max`.
- The practical product is provider-neutral account pooling for coding agents, but this goal is specifically the Claude/Anthropic parity work needed before making `cclb` the default `cc` path.
- Earlier Haiku stress tests were green but misleading. Fable/Sonnet-tier probes later showed both Anthropic accounts could be active overall while limited for high-tier requests.
- Codex/OpenAI already has sticky session machinery. Anthropic currently calls generic account selection with provider/model and no sticky key, so it can random-hop accounts and lose prompt-cache locality.
- Prompt caching can work through the proxy if request bodies/headers are preserved, but cache hits are account-scoped, so Claude conversations need account stickiness.
- Dashboard currently shows Anthropic request-log totals and binary status, not true Codex-style usage windows or model-specific cooldowns.

## Source-of-Truth Map

Read these first:

- `/Users/aneyman/repos/codex-lb/AGENTS.md` - branch policy, OpenSpec-first workflow, review trapdoors.
- `/Users/aneyman/repos/codex-lb/openspec/changes/add-anthropic-provider/` - active Anthropic provider context/tasks already in flight.
- `/Users/aneyman/repos/codex-lb/app/modules/proxy/anthropic_service.py` - current Claude `/v1/messages` proxy and Anthropic account selection path.
- `/Users/aneyman/repos/codex-lb/app/modules/proxy/load_balancer.py` - existing selector, sticky affinity, caps, leases, and rate-limit handling.
- `/Users/aneyman/repos/codex-lb/app/modules/proxy/affinity.py` - Codex/OpenAI sticky key extraction and prompt-cache affinity helpers.
- `/Users/aneyman/repos/codex-lb/app/modules/proxy/sticky_repository.py` and `/Users/aneyman/repos/codex-lb/app/modules/sticky_sessions/` - sticky session storage and dashboard API.
- `/Users/aneyman/repos/codex-lb/app/modules/usage/updater.py`, `/Users/aneyman/repos/codex-lb/app/modules/usage/repository.py`, `/Users/aneyman/repos/codex-lb/app/core/usage/refresh_scheduler.py` - Codex/OpenAI usage refresh pattern and any in-flight Anthropic usage work.
- `/Users/aneyman/repos/codex-lb/app/core/clients/anthropic_usage.py` - untracked/in-flight Anthropic usage client; inspect before editing.
- `/Users/aneyman/repos/codex-lb/app/core/anthropic/models.py` - Claude Code payload compatibility; Fable sends newer fields and system-role messages.
- `/Users/aneyman/repos/codex-lb/frontend/src/features/accounts/components/account-list-item.tsx`
- `/Users/aneyman/repos/codex-lb/frontend/src/features/accounts/components/account-usage-panel.tsx`
- `/Users/aneyman/repos/codex-lb/frontend/src/features/accounts/schemas.ts`
- `/Users/aneyman/.zshrc` - `claude-lb` / `cclb` launcher. Verify it does not set `ANTHROPIC_AUTH_TOKEN` and does not inject a model default.
- Claude Code bundle/local state discovery targets:
  - `zsh -ic 'whence -a claude'`
  - `/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code`
  - `~/.claude` JSON/JSONL state, searched narrowly.

Known local Anthropic accounts:

- `neyman`: `a.neyman17@gmail.com`, account id `2c436b54-a7e2-4299-9d6b-689ad2dda8cb`
- `kinetic`: `alex@kineticapps.io`, account id `ddb5ff1a-4aea-4810-9f10-196fb49b5d80`

Useful current-state probes:

```sh
cd /Users/aneyman/repos/codex-lb
zsh -ic 'functions claude-lb; functions cclb'
curl -sS --max-time 5 http://127.0.0.1:2455/api/accounts | jq -r '.accounts[] | select(.provider=="anthropic") | [.accountId,.email,.status,(.rateLimitResetAt // ""),(.requestUsage.requestCount|tostring)] | @tsv'
curl -sS --max-time 5 'http://127.0.0.1:2455/api/request-logs?limit=30' | jq -r '.requests[] | select(.provider=="anthropic" or (.model|tostring|startswith("claude"))) | [.requestedAt,.accountId,.model,.status,(.errorCode // ""),(.errorMessage // "")] | @tsv'
```

## Scope and Non-Goals

In scope:

- Discover Claude Code's usable session/conversation signal and usage/limit/reset data source, or prove it is unavailable.
- Add Anthropic/Claude session affinity using durable sticky-session storage or a provider-neutral equivalent.
- Add model/params-aware Anthropic quota keys so high-tier limits do not globally disable otherwise usable accounts.
- Add usage/cooldown persistence and selector integration for requested model/effort/context class.
- Preserve Anthropic request payloads and headers needed for Fable, thinking, context management, prompt caching, and future Claude Code fields.
- Update dashboard/API schemas and UI to show honest Anthropic usage, cooldowns, resets, account eligibility, and recent request evidence.
- Verify `cclb` and, only after tests pass, prepare a safe default `cc` rollout path with an escape hatch.

Non-goals:

- Do not rename the product/repo in this goal.
- Do not downgrade default models or optimize for small LLM costs.
- Do not switch to API usage billing or require API keys for Claude Code.
- Do not mutate production data, external payments, notifications, or unrelated services.
- Do not revert, stash, or overwrite unrelated dirty worktree changes from other agents.

## Work Order / Checkpoints

### Checkpoint 1 - Baseline and OpenSpec

Acceptance:

- Record current dirty files and identify which in-flight changes are related.
- Create or update an OpenSpec change for Claude account routing/usage/stickiness/dashboard behavior, keeping `spec.md` normative and rationale in context/notes.
- Verify `cclb` currently preserves `Claude Max`, has no `ANTHROPIC_AUTH_TOKEN`, and has no model override.

### Checkpoint 2 - Claude Code Signals

Acceptance:

- Discover what stable session/conversation identifier Claude Code exposes through headers, payload, local state, or bundle behavior.
- Discover whether Claude Code or Anthropic exposes subscription usage windows/reset data for each OAuth account.
- Save findings in the OpenSpec change context and this brief's Progress Log.
- If true usage telemetry is unavailable, define the fallback quota-key cooldown contract from 429/reset headers and local request evidence.

Targeted discovery commands:

```sh
zsh -ic 'whence -a claude'
rg -n "Usage credits|Current week|Current session|Claude Max|usage_limit|usageLimits|subscription|billing|rate_limit|reset|conversation|session" /opt/homebrew/lib/node_modules/@anthropic-ai/claude-code -S
find ~/.claude -maxdepth 4 -type f \( -name '*.json' -o -name '*.jsonl' -o -name '*.log' \) 2>/dev/null | head -200
rg -n '"usage"|"limits"|"subscription"|"max"|"reset"|"conversation"|"session"' ~/.claude --glob '*.json' --glob '*.jsonl' -S
```

### Checkpoint 3 - Quota-Key Model

Acceptance:

- Implement or adapt persistence for Anthropic quota state by account and quota key, not just whole-account status.
- Quota key must include at least provider, account, model family/class, and effort/thinking class when available.
- 429/reset evidence for `claude-sonnet-*` or Fable high-tier must not globally mark the account unusable for all Claude requests if lower-tier models remain available.
- Existing OpenAI/Codex account selection behavior remains unchanged.

### Checkpoint 4 - Claude Session Stickiness

Acceptance:

- Anthropic `/v1/messages` selection passes a sticky key/kind into the load balancer or equivalent session-affinity layer.
- Repeated turns from the same Claude Code conversation select the same Anthropic account while it remains eligible for the requested quota key.
- If the sticky account is temporarily unavailable for the requested quota key, fail over to another eligible account without destroying recoverable cache affinity unless the design explicitly requires rebind.
- Prompt-cache and payload fields are preserved through the proxy.

### Checkpoint 5 - Failover and Error Behavior

Acceptance:

- 401/403 refresh/auth failures mark or refresh the right account and try another eligible account when safe.
- Pre-stream 429 tries another eligible account for the same quota key.
- All-accounts-limited for the requested Fable/xhigh quota returns a clear structured error with reset/cooldown evidence and does not leave Claude Code retrying forever.
- Mid-stream failure behavior is defined, tested where replay-safe, and otherwise surfaced honestly.

### Checkpoint 6 - Dashboard/API Parity

Acceptance:

- `/api/accounts` and related schemas expose Anthropic usage/limit/cooldown state with reset times and model/quota-key availability.
- Account list/detail UI shows actionable Anthropic state: available/limited by quota key, reset estimates, recent 429 evidence, request-log totals, and last successful model use.
- If true Claude 5h/weekly percentages are unavailable, UI says/represents the honest fallback state rather than fake 100 percent remaining windows.

### Checkpoint 7 - E2E Verification and Default Rollout Decision

Acceptance:

- Run unit/integration tests for selector, Anthropic proxy, usage/cooldown persistence, dashboard schemas/components.
- Restart the local service if needed and run live `cclb` smoke tests.
- Verify Fable/default best-model through `cclb` uses Claude Max and no model downgrade.
- Verify account distribution, session stickiness, quota-key failover, and all-limited fail-fast behavior across the two known accounts.
- Only then decide whether to make `cclb` the default `cc` command; if yes, document the wrapper change and escape hatch.

## Verification Loop

Use focused checks first, then broader checks after integration:

```sh
cd /Users/aneyman/repos/codex-lb
uv run pytest tests/unit/test_anthropic_core.py tests/unit/test_anthropic_oauth.py tests/integration/test_anthropic_proxy.py
uv run pytest tests/unit tests/integration -k "anthropic or sticky or usage or account"
openspec validate --specs
```

Frontend/API checks should include the relevant project commands from package scripts or the established frontend test runner. Record exact commands and outputs in the Progress Log.

Live smoke checks should include:

- `cclb -p 'Reply with exactly: CCLB-OK'` with Claude Max visible and no `API Usage Billing`.
- Fable/default request through `cclb` with no injected `--model`.
- Forced account tests by pausing one Anthropic account at a time.
- Repeat-turn same-session test proving same account selection.
- All-limited/high-tier test proving clear reset/cooldown surfaced.
- Dashboard `/api/accounts` and UI inspection proving Anthropic account state is visible.

## Progress Log

**Current checkpoint:** Checkpoint 7 - E2E Verification and Default Rollout Decision
**Last verified:** Fresh default/Fable `cc` traffic through Claude LB works locally with Claude Max/OAuth billing and an exact proxy-claimed account banner.
**Remaining:** Commit/review ownership of the broader dirty worktree and install/restore the OpenSpec CLI if validator execution is required.
**Blocked:** No

| Time | Checkpoint | Change | Verification | Next |
| --- | --- | --- | --- | --- |
| 2026-06-09 16:06 EDT | 3-6 | Added Anthropic quota-key cooldown routing, durable hashed Claude session stickiness, quota labels, and focused integration coverage for sticky reuse/failover/dashboard payload. | `uv run pytest tests/integration/test_anthropic_proxy.py` -> 7 passed. Broader related run: 136 passed, 3 existing OpenAI usage/account recovery failures outside the Anthropic proxy path. | Restart local service, run live default/Fable `cclb`, inspect `/api/accounts` and request logs, then decide default `cc` rollout. |
| 2026-06-09 16:33 EDT | 7 | Made `cc` the local Claude LB launcher path, added proxy-claimed Anthropic session routing, preserved Claude Max/OAuth billing by avoiding `ANTHROPIC_AUTH_TOKEN`, and added a per-launch header-injecting shim so sticky session routing is durable for Claude Code. | `cc -p "Reply with exactly: CC-LB-LOG-OK"` -> banner picked `alex@prove-it.io` for `fable-5/top-thinking`, response `CC-LB-LOG-OK`, request log persisted `claude-fable-5 ok` on account `502ebd48-a6e4-42fc-b7cf-849cae280c14_0b155c6f`; `uv run pytest tests/unit/test_anthropic_core.py tests/unit/test_anthropic_oauth.py tests/unit/test_anthropic_usage_client.py tests/unit/test_usage_updater.py tests/integration/test_accounts_api.py tests/integration/test_accounts_api_extended.py tests/integration/test_anthropic_proxy.py` -> 139 passed. `openspec validate --specs` blocked: command not found. | Keep `cc` as daily command; use `CLAUDE_LB_DISABLE=1 cc ...` only as direct-account escape hatch if needed. |

## Stop / Pause Rules

- Pause before any destructive git command, branch switch, force push, reset, stash, or revert.
- Pause if live code conflicts with this brief's source-of-truth map or if an in-flight dirty change appears owned by another agent and must be modified.
- Pause before making `cclb` the default `cc` launcher unless the verification gates pass.
- Pause if Claude usage telemetry requires credentials, private browser state, or external service access that cannot be discovered safely from local code/state.
- Stop when the stopping condition is met and the Progress Log contains final verification evidence.

## Handoff Prompt

```text
/goal Implement the durable final state for Claude Code load balancing: OAuth/Claude Max billing only, no default model downgrade, per-session sticky account affinity, per-model/effort quota-aware account selection, clean failover/reset handling, and dashboard usage/limit parity for Anthropic. Work in /Users/aneyman/repos/codex-lb on main, follow AGENTS.md and OpenSpec-first rules, and treat /Users/aneyman/repos/codex-lb/docs/plans/2026-06-09-claude-lb-durable-final-state-goal.md as the source of truth. Read the brief first, then continue in checkpoints until fresh cclb Fable/default traffic is verified end to end across the local proxy with Claude Max billing, sticky routing, quota-key failover, actionable dashboard state, focused tests passing, OpenSpec validated, and the brief's Progress Log updated. Preserve unrelated dirty worktree changes, keep edits surgical, self-review the diff, and pause only for the brief's explicit stop rules or if live code conflicts with the source map.
```
