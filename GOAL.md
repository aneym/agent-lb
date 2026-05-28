# Goal Brief — codex-lb → add Anthropic Claude as a second provider

- **Date:** 2026-05-28
- **Owner / requester:** Alex Neyman
- **Status:** CP4 complete — Anthropic OAuth landed; CP5/CP6 integration in progress
- **Fork target:** https://github.com/Soju06/codex-lb (Python / FastAPI / SQLAlchemy)
- **Repo:** `/Users/aneyman/repos/swap-lb` (already cloned; full history; remote `upstream` → Soju06/codex-lb)
- **Branch:** `feat/anthropic-provider` (already created)
- **This brief:** `/Users/aneyman/repos/swap-lb/GOAL.md`

---

## 1. Objective & stopping condition

**Objective.** Fork `Soju06/codex-lb` and add **Anthropic Claude** as a _second provider_ so the same single instance load-balances multiple Claude Pro/Max OAuth accounts for **Claude Code (CLI)** via `ANTHROPIC_BASE_URL`, with the existing dashboard unified to show **both** providers' accounts, usage, and cost — while the OpenAI/ChatGPT path stays byte-identical and upstream-mergeable.

**Outcome metric:** number of providers the instance can pool + correct Claude request handling.

- **Before:** 1 provider (OpenAI/ChatGPT only). 0 Claude accounts poolable. Claude Code cannot route through the LB (no Anthropic provider exists; literally zero `anthropic`/`claude` strings in `app/`).
- **After — two-stage acceptance:**

  **Stage A — Code-landed acceptance (sufficient to call the build done; runtime needs the user's real accounts):**
  - `provider` migration applies on both a fresh and an existing DB; existing rows default to `openai`.
  - All Anthropic modules exist and are wired: `core/anthropic/` (Messages models, SSE parser, pricing, model registry), Anthropic OAuth (PKCE), `/v1/messages` route, slim `AnthropicProxyService`.
  - Unit/integration tests pass against **mocked upstream**: Anthropic SSE stream parses into correct events + usage (incl. cache tokens); OAuth PKCE URL builds + token exchange/refresh parse correctly (no `id_token`); provider-filtered account selection never picks an OpenAI account for a Claude request; `/v1/messages` happy-path streams SSE through intact.
  - Existing OpenAI test suite still green; OpenAI request path unchanged.

  **Stage B — Runtime-measured acceptance (final; requires user's ≥2 real Claude accounts):**
  - ≥2 Claude Pro/Max accounts added via the dashboard OAuth flow.
  - With `ANTHROPIC_BASE_URL` pointed at the proxy, **≥20 consecutive Claude Code requests** return HTTP 200 with correctly streamed SSE.
  - Requests are **distributed across ≥2 accounts** (visible in the dashboard request log).
  - A 429 (real or forced) on one account **fails over** to another; the client request still succeeds.
  - Dashboard shows **per-account Claude token usage + cost** (input/output + cache-creation/cache-read) alongside Codex.
  - **Regression:** Codex CLI/desktop/IDE path through the same instance is unaffected.

  **Stage C — Upstream contribution (the long-term goal: don't maintain a fork):**
  - The Anthropic provider is contributed back to `Soju06/codex-lb` as a pull request (or short series — see §5 CP8), structured for maintainer acceptance: additive, well-tested, matching repo conventions, OpenAI path byte-identical.
  - **Deliverable = an open upstream PR with green CI and a clean additive diff.** **Merge is the maintainer's decision and is explicitly NOT a goal gate** — we cannot block on someone else's review. If upstream merges, codex-lb becomes natively dual-provider and there is nothing to maintain. If the maintainer declines or stalls, the same branch runs as a private fork (Stage A/B already make it fully usable).

---

## 2. Relevant conversation context (distilled — do not re-litigate)

- **Why fork, not CLIProxyAPI, not greenfield.** User loves codex-lb's _integrated single-app dashboard_ + _per-key rate limits_ + operational simplicity. CLIProxyAPI is more actively maintained but its dashboard is a churny multi-service bolt-on and loses per-key limits. Both viable paths require building; the fork keeps the beloved dashboard **and** lets the OpenAI half keep merging from upstream. Decision is final.
- **Scope is CLI / coding-agent only.** Claude **Desktop chat** is a sealed Electron web client (no base-URL knob, pinned TLS) → **not proxyable, out of scope, accepted.** Codex already covers all its surfaces (CLI + desktop + IDE) via its shared app-server reading `chatgpt_base_url`; Claude is covered only through **Claude Code CLI** via `ANTHROPIC_BASE_URL`. The embedded Claude-Code-in-Desktop agent is a possible _later_ follow-up, not this goal.
- **ToS / impersonation reality (critical for correctness).** Since Jan 2026 Anthropic only accepts subscription OAuth tokens that look like they came from Claude Code (requires `anthropic-beta: oauth-2025-04-20` and the Claude-Code system-prompt prefix). **The real client here IS Claude Code**, so it already sends the right headers + system prompt. The proxy's job is to **forward them intact** and only swap the `Authorization` bearer to the selected account's token — never strip, rewrite, or run OpenAI payload transforms on Anthropic requests. Personal accounts, local only; **do not** add multi-user/reselling features.
- **Architecture finding: codex-lb has NO provider abstraction.** OpenAI is baked into the data model, OAuth, refresh, proxy, SSE parser, pricing, balancer, and frontend — but module seams are clean. This is **fork-and-extend with a slim parallel Anthropic stack**, additive and upstream-mergeable.
- **The 14,904-line `app/modules/proxy/service.py` is OpenAI-Responses-specific. DO NOT TOUCH IT.** The Anthropic Messages protocol is simpler (one-shot HTTP+SSE, no `previous_response_id`, no websocket, no file pinning) — build a **slim parallel `AnthropicProxyService` (~500–1500 LOC)**. Anthropic requests must **never** pass through the OpenAI rewriters.
- **End goal is upstream contribution, not a permanent fork.** The whole reason every change stays additive and the OpenAI path stays byte-identical is so the Anthropic provider can be submitted back to `Soju06/codex-lb`. If merged, fork maintenance drops to zero. The earlier "keep it private for ToS reasons" worry is largely moot for _upstream_: codex-lb is **already** a public tool that pools ChatGPT subscription accounts — adding Claude as a second provider is symmetric and doesn't change the project's nature. A private fork is the fallback only if the maintainer passes.

---

## 3. Source-of-truth map (read first)

**This brief**, then the fork. Key repo-relative files and what they tell you:

| Area                                  | File(s)                                                                                                                                             | Note                                                                                                                                                                                                                                                                                        |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Data model                            | `app/db/models.py`                                                                                                                                  | `Account` (~L51) has `chatgpt_account_id`, `plan_type` free string, assumes `id_token_encrypted`; `RequestLog` (~L135) has single `cached_input_tokens`; `UsageHistory` shaped on ChatGPT 5h/weekly windows                                                                                 |
| Accounts/refresh                      | `app/modules/accounts/auth_manager.py` (`update_tokens`, `_chatgpt_account_id_from_id_token` ~L271), `repository.py`                                | `update_tokens` takes `id_token_encrypted` + `chatgpt_account_id` positionally — make polymorphic                                                                                                                                                                                           |
| OAuth client                          | `app/core/clients/oauth.py`                                                                                                                         | Already parameterized on `base_url`/`client_id`/`scope` (good) but injects ChatGPT-only params (`id_token_add_organizations`, `codex_cli_simplified_flow`, `originator` ~L78-81). **Copy-and-strip** for Anthropic                                                                          |
| Refresh                               | `app/core/auth/refresh.py` (~L111-127), `app/core/auth/models.py`                                                                                   | `OAuthTokenPayload` requires `id_token`; Anthropic returns none → per-provider refresh path                                                                                                                                                                                                 |
| OAuth flow                            | `app/modules/oauth/service.py`                                                                                                                      | Templates + state store are flow-aware; add a parallel Anthropic flow                                                                                                                                                                                                                       |
| Proxy client                          | `app/core/clients/proxy.py`                                                                                                                         | `IGNORE_INBOUND_HEADERS` (~L68-76) drops `authorization`/`chatgpt-account-id` but forwards rest (beta header passes ✔); `stream_responses` (~L1890), upstream URL hardcoded `…/codex/responses` (~L1926); `parse_sse_event` → `OpenAIEvent` (~L86-96). Build a parallel `stream_messages()` |
| Proxy routers                         | `app/modules/proxy/api.py` (9 `APIRouter` ~L153-194)                                                                                                | Add a `messages_router` for `/v1/messages`                                                                                                                                                                                                                                                  |
| **Monster (DO NOT TOUCH)**            | `app/modules/proxy/service.py`                                                                                                                      | 14,904 lines, OpenAI-Responses-specific. Build a slim sibling instead                                                                                                                                                                                                                       |
| Balancer/routing                      | `app/modules/proxy/load_balancer.py` (imports `app.core.openai.model_registry` ~L28), `app/core/balancer/logic.py` (`PERMANENT_FAILURE_CODES` ~L14) | Selection math is generic; add a **provider filter** so Anthropic requests only pick Anthropic accounts                                                                                                                                                                                     |
| OpenAI core (no Anthropic logic here) | `app/core/openai/{models,parsing,model_registry,requests,v1_requests,chat_responses}.py`                                                            | Mirror its structure under `app/core/anthropic/`                                                                                                                                                                                                                                            |
| Pricing/usage                         | `app/core/usage/pricing.py`, `app/core/usage/__init__.py` (`UNKNOWN_PLAN_FALLBACK="free"` ~L18/L29), `app/core/plan_types.py`                       | Make costing dispatch by provider                                                                                                                                                                                                                                                           |
| API keys / rate limits                | `app/modules/api_keys/`                                                                                                                             | **Provider-agnostic — reuse as-is.** Per-key limits trigger on model+tokens+cost                                                                                                                                                                                                            |
| Config                                | `app/core/config/settings.py` (~L132, L149-156)                                                                                                     | `upstream_base_url`, `auth_base_url`, `oauth_client_id`, `oauth_originator`, `oauth_scope` → make per-provider config blocks                                                                                                                                                                |
| Frontend                              | `frontend/src/features/accounts/schemas.ts` (`chatgptAccountId` ~L116), `components/opencode-auth-export-dialog.tsx` (~L83)                         | Vite/React/Zod; list-driven; add a `provider` discriminator + badge                                                                                                                                                                                                                         |

**Anthropic protocol facts to implement:**

- **OAuth (PKCE):** Claude Code's public client id; scopes `org:create_api_key user:profile user:inference`; authorize on claude.ai; token endpoint returns **no `id_token`**; refresh is shape-compatible minus `id_token`.
- **Request header:** preserve inbound `anthropic-beta: oauth-2025-04-20`; add `anthropic-version`; swap `Authorization: Bearer <selected account token>`.
- **Messages API `/v1/messages` SSE events:** `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`, plus `tool_use` content blocks.
- **Usage shape:** `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`.
- **Model catalog:** Anthropic `GET /v1/models` for model-registry sync.

---

## 4. Scope & non-goals

**Must change:** data model (provider dimension), auth/refresh dispatch, new `core/anthropic/` + Anthropic OAuth + `/v1/messages` route + slim service, dashboard provider support, per-provider config blocks.

**Must NOT change:** internals of `app/modules/proxy/service.py`; do not add Anthropic logic into `app/core/openai/*`. Keep the OpenAI request path byte-identical so upstream merges stay clean.

**In scope (added):** an upstream pull request to `Soju06/codex-lb` is the final deliverable (see §1 Stage C, §5 CP8).

**Non-goals:** Claude Desktop chat capture; TLS-MITM; embedded-desktop-agent integration; multi-user / token reselling; any non-CLI surface. Getting the PR _merged_ is **not** a goal gate (maintainer's call).

**Boundaries:** Local build is unrestricted. **Per-checkpoint `git commit` on `feat/anthropic-provider` is allowed** — a clean commit history is required for the PR. The account-visible steps — `gh repo fork` of Soju06/codex-lb, `git push`, and opening the PR — are the goal's final deliverable but **PAUSE for explicit user authorization** before running. No deploy/publish.

---

## 5. Work order / checkpoints

> Critical path CP0→CP2 is serial (schema + dispatch seam is the keystone). After CP2, Lane A (backend protocol) and Lane B (frontend) run in parallel. See §6.

- **CP0 — Baseline (clone/branch already done).** Repo is cloned at `/Users/aneyman/repos/swap-lb` on branch `feat/anthropic-provider` (remote `upstream` → Soju06/codex-lb), brief at repo root. Remaining: run codex-lb locally per its README; confirm existing dashboard loads + test suite green. **Accept:** app boots, baseline tests pass, dashboard renders the OpenAI accounts UI.
- **CP1 — Schema migration.** Add `provider` column (enum/string, default `'openai'`) to `accounts`, `request_logs`, `usage_history`; make `id_token_encrypted` + `chatgpt_account_id` nullable; add `cache_creation_tokens` + `cache_read_tokens` to `request_logs`. **Accept:** migration applies on fresh + existing DB; existing rows default `openai`; tests pass.
- **CP2 — Provider dispatch seam.** Introduce a thin `Provider` protocol (model_registry + pricing + sse_parser + request_normalizer + oauth_config). Route refresh + account-creation through provider dispatch as **thin wrappers over the existing OpenAI impl** (keeps upstream mergeable). **Accept:** OpenAI path unchanged + green; dispatch unit-tested.
- **CP3 — `core/anthropic/` (Lane A).** Messages request/response models; SSE event parser (6 event types + `tool_use`); pricing catalog with cache tiers; model registry syncing from Anthropic `GET /v1/models`. **Accept:** unit tests parse a recorded Anthropic SSE stream into correct events + usage; pricing computes cache-aware cost.
- **CP4 — Anthropic OAuth (Lane A).** Copy-and-strip `oauth.py` → Anthropic PKCE module (client_id/scopes/authorize/token, no `id_token`); per-provider refresh path; derive account id from token metadata. **Accept:** PKCE URL builds; token exchange + refresh unit-tested against recorded responses; account persists with `provider='anthropic'`.
- **CP5 — `/v1/messages` route + slim `AnthropicProxyService` (Lane A).** New router in `api.py`; slim service forwards to `api.anthropic.com/v1/messages`, injects `anthropic-version`, **preserves** inbound `anthropic-beta`, swaps `Authorization` to the selected account's token, streams SSE through untouched; reuses balancer (**provider-filtered**), api-keys, rate-limit, audit, request logging. **HARD RULE:** never routes through `app/modules/proxy/service.py`. **Accept:** integration test — a Claude-Code-shaped request against mocked upstream returns streamed SSE intact; provider filter verified; usage logged with cache tokens.
- **CP6 — Frontend (Lane B).** Provider discriminator in account schemas; Provider selector on add-account/login; provider badge in accounts grid; Anthropic cache-token columns in the cost view. **Accept:** dashboard lists both providers; add-Claude-account OAuth flow reachable in UI; cost view renders Anthropic accounts.
- **CP7 — Runtime acceptance (pause for user).** User adds ≥2 real Claude accounts; point Claude Code via `ANTHROPIC_BASE_URL`; run ≥20 requests; observe distribution + 429 failover + usage/cost; confirm Codex path unaffected. **Accept:** Stage B metric (§1) met.
- **CP8 — Upstream PR (final deliverable; PAUSES for user go before any GitHub action).**
  - **Issue-first (recommended, can run in parallel from CP1):** open an upstream issue/discussion proposing dual-provider support to gauge the maintainer's appetite. It doesn't block the build — if they're cold on it, you still have a working private fork; if warm, you tailor the PR to their preferences.
  - **Split for reviewability — not one mega-diff:** **PR 1** = the provider-abstraction seam (CP2) as a pure no-behavior-change refactor with all tests green (easy for a maintainer to accept). **PR 2** = the additive Anthropic provider (CP3–CP6) on top of PR 1.
  - **Submit:** `gh repo fork` Soju06/codex-lb, push the branch(es), open the PR(s) with green CI and a clean diff that leaves the OpenAI path byte-identical; include a short rationale + test plan in the PR body.
  - **Accept:** PR(s) open upstream, CI green, additive diff. **Merge is the maintainer's decision — NOT a goal gate.**

---

## 6. Parallelization / subagent plan

- **Local critical path (serial):** CP0 → CP1 → CP2. The schema + dispatch seam is the keystone; everything else builds on it.
- **After CP2, two disjoint lanes run in parallel (Codex should use subagents):**
  - **Lane A — backend protocol (CP3→CP4→CP5).** Owns: `app/core/anthropic/*`, the new Anthropic OAuth module (`app/modules/oauth_anthropic/*` or equivalent), the new slim service file, the `messages_router` addition in `app/modules/proxy/api.py`, the provider split in `app/core/auth/refresh.py`.
  - **Lane B — frontend (CP6).** Owns: `frontend/src/features/accounts/*`.
- **Integration (main agent):** after Lane A lands, wire route registration in `app/main.py` and the provider filter in `app/modules/proxy/load_balancer.py`; run the full end-to-end check before CP7.
- Lanes never edit the same file. Subagents cannot spawn subagents — keep briefs flat.

---

## 7. Verification loop (scoped to THIS fork repo, not PayMe)

- **Tests:** run the repo's `pytest` suite after every checkpoint; add the per-CP tests named in §5. Repo-wide health gates outside this fork are irrelevant.
- **Fixtures (build in CP3/CP4):** record a sample Anthropic `/v1/messages` SSE stream and a sample OAuth token+refresh JSON so CP3–CP5 are fully testable against a **mocked upstream** without live accounts.
- **Lint/type:** use whatever the repo already configures (ruff/mypy/etc.); do not add new tooling.
- **Manual (CP6):** `docker run` or `uvx` the fork, open the dashboard, confirm both-provider UI renders.
- **Runtime (CP7):** Open the local Anthropic OAuth login flow for the user, let them add real Claude accounts, then run `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>/<anthropic-path> claude` prompts; inspect the dashboard request log for account distribution and a forced-429 failover.

---

## 8. Progress Log

**Current checkpoint:** CP5/CP6 — Anthropic proxy and dashboard integration
**Outcome metric:** providers poolable, before = 1 (OpenAI only) / 0 Claude accounts; target = 2 providers, ≥2 Claude accounts balancing + failing over with unified dashboard usage/cost. **Final deliverable:** open upstream PR(s) (Stage C / CP8).
**Current value:** CP4 complete: schema/provider seam remain OpenAI-compatible, Anthropic protocol core exists, and dashboard OAuth can persist Anthropic accounts with no `id_token`; Claude requests still need CP5 proxy routing before accounts are poolable by Claude Code.
**Last verified:** 2026-05-28 17:52 EDT — `uv run ruff check app/core/anthropic/oauth.py app/core/providers app/core/clients/oauth.py app/core/auth/refresh.py app/modules/oauth tests/unit/test_anthropic_oauth.py tests/unit/test_provider_registry.py tests/integration/test_oauth_flow.py` passed; `uv run pytest -q tests/unit/test_auth_manager.py tests/unit/test_auth_refresh.py tests/unit/test_oauth_client.py tests/unit/test_provider_registry.py tests/unit/test_anthropic_oauth.py tests/integration/test_oauth_flow.py` passed (50 passed); `git diff -- app/modules/proxy/service.py` empty.
**Remaining:** CP5–CP8
**Blocked:** No

| Time | Checkpoint | Change | Outcome delta (before → after) | Next |
| ---- | ---------- | ------ | ------------------------------ | ---- |
| 2026-05-28 17:28 EDT | CP0 — Baseline | Installed frozen backend/frontend deps, ran migration/unit gates, booted local app with dashboard auth disabled on temp SQLite DB, rendered Accounts UI via Playwright. User updated CP7 intent: open real Anthropic login flows once implemented so real accounts can be used for CLI runtime checks. | Baseline unknown → OpenAI-only baseline verified; providers poolable remains 1 → 1, Claude accounts 0 → 0. | CP1 schema migration: provider columns/defaults, nullable OpenAI-only fields, cache token columns, fresh/existing DB verification. |
| 2026-05-28 17:35 EDT | CP1 — Schema migration | Added `provider` to `accounts`, `request_logs`, and `usage_history`; made `accounts.id_token_encrypted` nullable; added `cache_creation_tokens` and `cache_read_tokens` to request logs; added OpenSpec change artifacts. | Provider persistence 1-provider implicit → provider-discriminated schema with legacy rows backfilled as `openai`; Claude accounts still 0 until OAuth/proxy land. | CP2 provider dispatch seam over existing OpenAI impl, with OpenAI behavior unchanged. |
| 2026-05-28 17:36 EDT | CP2 — Provider dispatch seam | Added `app/core/providers/` with OpenAI as the default provider, provider OAuth config, provider metadata extraction, refresh dispatch, and account-creation routing through the seam. | Provider implementation count 1 hardcoded → 1 registered default provider with Anthropic-ready extension points; Claude accounts still 0 until Anthropic core/OAuth/proxy land. | CP3 backend protocol core and CP6 dashboard provider UI lanes. |
| 2026-05-28 17:44 EDT | CP3 — Anthropic protocol core | Added `app/core/anthropic/` with Messages request/response/event models, SSE parsing and usage extraction, cache-aware pricing, and model-list registry parsing/sync helpers. | Anthropic implementation count 0 modules → protocol core ready for OAuth/proxy wiring; providers poolable remains 1 and Claude accounts remain 0 until CP4/CP5/CP7. | CP4 Anthropic OAuth, CP5 `/v1/messages` proxy, and CP6 dashboard integration review. |
| 2026-05-28 17:52 EDT | CP4 — Anthropic OAuth | Added Anthropic provider registration, Claude-Code-shaped PKCE authorize/token/refresh helpers, provider-aware OAuth state/exchange/persistence, nullable `id_token` refresh handling, and mocked OAuth tests. | Anthropic accounts 0 persistable → dashboard OAuth can create `provider='anthropic'` accounts without `id_token`; Claude Code routing still 0 until CP5 `/v1/messages` lands. | CP5 slim `/v1/messages` proxy and CP6 dashboard integration review. |

---

## 9. Stop / pause rules

- **Default to continuous progress on code checkpoints (CP0–CP6)** — self-verify via §7 tests. At CP7, open the implemented Anthropic login flow for the user and proceed with real-account CLI verification after the user completes login. Pause only if account login/consent needs user action, if an Anthropic-protocol decision is ambiguous, or before CP8 GitHub actions.
- **No-movement guard:** if two consecutive checkpoints log no movement on the outcome metric, pause and name the mechanism that will move it, or mark blocked.
- **Commits allowed; account-visible actions gated.** Per-checkpoint `git commit` on `feat/anthropic-provider` is allowed (the PR needs clean history). **Never without explicit user request:** `git push`, `gh repo fork` / creating any GitHub remote, opening the PR, deploy, publish. Prepare everything and PAUSE at CP8 for the user's go before touching GitHub.
- **Never** touch `app/modules/proxy/service.py` internals; **never** put Anthropic logic in `app/core/openai/*`; **never** run OpenAI payload rewriters on Anthropic requests; **never** strip the inbound `anthropic-beta` header or the client system prompt.
- **ToS:** keep it local and single-user; do not add reselling/multi-tenant features.

---

## 10. Handoff prompt

See the `/goal` launcher returned alongside this brief (also paste it here once finalized).
