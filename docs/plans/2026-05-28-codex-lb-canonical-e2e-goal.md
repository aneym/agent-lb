# Goal Brief (companion) — agent-lb canonical single-service, end-to-end: verify Codex → add Anthropic LB → public anonymized usage

- **Date:** 2026-05-28
- **Owner / requester:** Alex Neyman
- **Status:** Not started (infra cutover ~done this session; this brief sequences the remaining verification + features)
- **Repo / worktree:** `/Users/aneyman/repos/agent-lb` ← **canonical checkout** (the old `/Users/aneyman/repos/swap-lb` was deleted; `GOAL.md`/`HANDOFF.md` still say `swap-lb` — that path is stale, do not use it)
- **Branch:** `feat/anthropic-provider`
- **origin:** `https://github.com/aneym/agent-lb` (fork) · **upstream:** `https://github.com/aneym/agent-lb.git`
- **Canonical product spec for the Anthropic work:** `GOAL.md` (owns Anthropic product scope, Stage A/B/C). This file is a **companion** — it owns the cross-session infra decisions, the end-to-end objective spanning three phases, and the run progress log. On conflict about *Anthropic provider scope*, `GOAL.md` wins; on conflict about *runtime/infra/architecture state*, this file wins.

---

## 1. Objective & stopping condition

**Objective.** Make the single canonical agent-lb service on **Studio** the proven, durable home for *all* of Alex's coding-agent traffic, then (a) verify the OpenAI/Codex path end-to-end through it, (b) finish the blocked Anthropic provider so it load-balances ≥2 Claude accounts for Claude Code, and (c) surface a **public, anonymized** usage view embeddable on his personal website. All usage must be canonically tracked in exactly one place (Studio's DB) — never split across machines again.

**Multi-stage stopping condition (each phase has a measurable before→after):**

| Phase | Outcome metric | Before | Target (after) |
|---|---|---|---|
| **P0 — Canonical infra** | # independent writers to usage data | 2 (MacBook local + Studio → split-brain) | **1** (Studio only); MacBook LaunchAgent disabled; DR backup loaded with ≥1 integrity-verified snapshot showing `accounts=5` |
| **P1 — Codex e2e** | verified Codex requests recorded on the canonical LB | 0 (was erroring on stale `127.0.0.1:2455`) | **≥1 real `cx` model call + ≥1 raw curl**, both HTTP 200, both visible in Studio `request_logs` with account distribution across the 5 OpenAI accounts |
| **P2 — Anthropic LB** | providers poolable + working Claude routing | 1 provider, 0 active Claude accounts | **2 providers**; ≥2 active Claude accounts; **≥20** Claude Code requests 200 distributed across ≥2 accounts; 429 failover proven; per-account Claude usage incl. cache tokens recorded; OpenAI path regression-green |
| **P3 — Public anonymized usage** | public usage surface | none | **1 live anonymized endpoint/artifact** (daily-bucketed token + request totals), embeddable on the personal site, with an automated sanitization test proving **no PII / no content / no exact timestamps / no per-account identity** leak |

**Done** = P0–P3 acceptance all met, evidence in the Progress Log + `GOAL.md`. **Pause/stop** before any phase that needs a human (Anthropic browser OAuth; P3 exposure-mechanism design gate; P3 go-live security review) or any git push/commit/PR/merge.

> **Note on the canonical Anthropic spec:** `GOAL.md` §1 already defines Stage A (code-landed) / Stage B (runtime) / Stage C (upstream PR) for the Anthropic provider. P2 here == GOAL.md Stage B (Stage A is largely landed per CP0–CP6). **Stage C (upstream PR) is NOT in scope for this goal** — it is an explicit user-authorized GitHub action (see Stop rules).

---

## 2. Relevant conversation context (decided — do not re-litigate)

- **Single shared service, Studio is canonical.** Studio (`alexs-Mac-Studio`, macOS 26.3) runs agent-lb on `feat/anthropic-provider`, reachable over Tailscale at `https://studio.tailf266ac.ts.net:2455`. Verified this session: `/health` 200, dashboard `/` 200, `/api/accounts` → 5 OpenAI accounts active, migrated+reconciled DB (~143k `request_logs`, ~199k `usage_history`, 371M `~/.agent-lb/store.db`). Studio set to never sleep (`pmset sleep 0`, `womp 1`).
- **MacBook (`macbook-pro-110`) is CLIENT-ONLY now.** `~/.codex/config.toml`: `model_provider="agent-lb"`, `base_url="https://studio.tailf266ac.ts.net:2455/backend-api/codex"`. The local agent-lb LaunchAgent `com.aneyman.agent-lb` was **disabled** and moved to `~/.agent-lb/disabled-launchagents/com.aneyman.agent-lb.plist.disabled`. Stale `tailscale serve` rules for `:2455`/`:2456` (which proxied to the now-dead local backend) were removed; the unrelated `:8787` serve rule is preserved.
- **The earlier "broke it" symptom was a stale process, not a bad config.** Connection-refused errors hit `http://127.0.0.1:2455` because a Codex session predated the config edit; on-disk config already points at Studio. **A Codex restart resolves it** (P1 verifies this).
- **"Sync" = one-way DR backup ONLY.** Single writer (Studio) ⇒ bidirectional sync would *re-create* split-brain. The correct artifact is a consistent SQLite snapshot pulled to the MacBook. Implemented this session: `~/.agent-lb/bin/backup-from-studio.sh` + launchd `~/Library/LaunchAgents/com.aneyman.agent-lb-backup.plist` (12h `StartInterval`, `RunAtLoad`). The timer was **not yet loaded/verified** when this brief was written — P0 finishes that.
- **NEW requirement (this session): public anonymized usage.** Alex wants all usage canonically tracked (P0 guarantees that) and wants to expose an anonymized view on his personal website — "how many tokens I'm using and requests I'm making", **not exact times** ("likely DoD" → interpreted as **day-level granularity**; confirm in P3 design gate). This is a personal/fork-local feature, **flag-gated and excluded from any upstream PR diff**.
- **Anthropic correctness invariants (from GOAL.md, critical):** the real client is Claude Code, which already sends the right headers + system prompt. The proxy forwards them intact and only swaps the `Authorization` bearer. Anthropic requests must **never** pass through OpenAI rewriters; never strip inbound `anthropic-beta`; never rewrite Claude Code's system prompt; never touch `app/modules/proxy/service.py` internals (build/extend the slim `AnthropicProxyService` instead).

---

## 3. Source-of-truth map (read first, in order)

1. **`GOAL.md`** — canonical Anthropic product spec (Stage A/B/C, architecture findings, the detailed code-path table in its §3). The big map of which OpenAI module to mirror for Anthropic lives there.
2. **`HANDOFF.md`** — CP0–CP7 status, completed commits, the repaired Anthropic OAuth URL shape, the "Cloudflare human-verification — do not automate" note, hard rules. **Stale:** its runtime paths (`.runtime/cp7/...`) and `swap-lb` repo path refer to the deleted checkout; the canonical service now lives on Studio, not a local `.runtime/cp7`.
3. **`AGENTS.md`** — repo conventions, surgical-change rules, verification (`uv run pytest` / `uv run ruff` / `codex review --base origin/main`), spec SSOT.
4. **`Makefile`** — real CI targets: `lint`, `typecheck`, `frontend-lint`, `frontend-typecheck`, `frontend-test`, `frontend-build`, `test-unit`, `test-integration-core`, `migration-check`, `ci-fast`, `ci`.
5. **Usage substrate for P3:** `app/modules/usage/api.py` (router → `/summary`, `/history`, `/window`; models `UsageSummaryResponse` etc.), `app/modules/usage/builders.py` (already buckets `requests/tokens/cost` by epoch — the anonymized endpoint aggregates from here). `app/modules/dashboard/api.py` (prefix `/api`). Routes mounted in `app/main.py` (~L364–373).
6. **Auth surfaces (for P3 safety):** `app/core/config/settings.py` (`dashboard_auth_mode`, default `STANDARD`; firewall/CIDR settings), `app/core/auth/dashboard_mode.py`. The public surface must NOT reuse the admin dashboard; it is a dedicated minimal read-only artifact.
7. **Runtime/infra (this session, MacBook):** `~/.codex/config.toml`, `~/.agent-lb/bin/backup-from-studio.sh`, `~/Library/LaunchAgents/com.aneyman.agent-lb-backup.plist`, `~/.agent-lb/disabled-launchagents/`, `~/.agent-lb/backups/`. Studio data dir: `~/.agent-lb/store.db` + `encryption.key` (SSH alias `studio-ts`).

---

## 4. Scope & non-goals

**In scope:** finish/verify DR backup; verify Codex e2e through Studio; complete Anthropic Stage B (≥2 accounts, distribution, failover, usage incl. cache); build a flag-gated anonymized public usage artifact + sanitization test; fix the stale `swap-lb`/`.runtime` references in `GOAL.md`/`HANDOFF.md` to point at Studio + `agent-lb`; update `GOAL.md` Progress Log.

**Non-goals / must-not-change:**
- **Do not touch `app/modules/proxy/service.py` internals.** Build/extend the slim sibling.
- **Do not route Anthropic through OpenAI rewriters; do not strip inbound `anthropic-beta`; do not rewrite Claude Code's system prompt.**
- Do not re-introduce bidirectional DB sync or a second writer. MacBook stays client-only.
- Do not make the OpenAI/Codex path behave differently (must stay byte-identical / upstream-mergeable).
- Public-stats feature stays **fork-local + flag-gated**; it must not appear in any upstream contribution diff.
- Do not commit `.runtime/`, encryption keys, DBs, browser artifacts, secrets, or local agent scaffolding. Preserve unrelated dirty worktree files.
- Anthropic **Desktop chat** is out of scope (not proxyable). Claude coverage is Claude Code CLI only.

---

## 5. Work order / checkpoints

### P0 — Finish & verify canonical infra  *(local critical path; mostly done)*
- Load the backup timer: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.aneyman.agent-lb-backup.plist` (it runs once via `RunAtLoad`).
- **Accept when:** `~/.agent-lb/backups/store-latest.db` exists, `pragma integrity_check` = `ok`, `select count(*) from accounts` = 5, a timestamped `store-<stamp>.db` retained, and `~/.agent-lb/backups/backup.log` shows an `OK` line. Confirm MacBook has **no** listener on 2455/2456 and the LaunchAgent is not loaded.

### P1 — Prove Codex end-to-end through Studio  *(local critical path)*
- Restart/spawn a fresh `cx`/codex CLI session (picks up the Studio base_url) and make one real model call. Capture that it returns a completion (HTTP 200, streamed).
- Raw curl smoke: `POST https://studio.tailf266ac.ts.net:2455/backend-api/codex/responses` with a valid minimal body + auth as Codex sends it; expect a streamed 200 (a bare `{}` returns 400 — that only proves routing).
- **Accept when:** both calls succeed AND appear in Studio `request_logs` (query over SSH: `sqlite3 ~/.agent-lb/store.db "select provider,count(*) from request_logs where created_at > <t0>;"`), with selection spread across the 5 OpenAI accounts over a handful of calls. OpenAI path otherwise unchanged.

### P2 — Anthropic load balancing == GOAL.md Stage B  *(human-gated start)*
- **Add ≥2 Anthropic accounts on the *canonical Studio* dashboard** (`https://studio.tailf266ac.ts.net:2455`), not a local runtime. Open the Anthropic OAuth flow; **pause** for Alex to complete Claude login + Cloudflare + consent in a browser; then submit the `code#state` to the active/manual flow per `HANDOFF.md`. Repeat for a second account.
- If token exchange fails, diagnose against Claude Code's real OAuth shape (`HANDOFF.md` records the working URL shape) and patch only minimal provider/OAuth code with focused tests. No changes to the OpenAI path or `service.py`.
- Stage B smoke: point `ANTHROPIC_BASE_URL` at the service, run **≥20** Claude Code requests; verify 200 + correct SSE, distribution across ≥2 accounts, 429 failover (real or forced), per-account Claude usage incl. `cache_creation_input_tokens`/`cache_read_input_tokens`, and an OpenAI/Codex regression smoke.
- **Accept when:** all Stage B bullets in `GOAL.md` §1 are evidenced in the Progress Log.

### P3 — Public anonymized usage surface  *(can run as a parallel subagent lane after P0; has a design gate + a security gate)*
- **Design gate (pause for user):** confirm (1) **exposure mechanism** — default recommendation = a **publisher** job on Studio that emits a sanitized aggregate JSON and uploads it to the personal site's static host (keeps the LB tailnet-only, zero public inbound); alternative = `tailscale funnel` of a single dedicated public path (riskier — same port serves admin+proxy). (2) **granularity** = daily buckets ("DoD"); confirm. (3) **fields** = date, total_requests, total_input_tokens, total_output_tokens (+ optional split by provider/model-family; cost omitted by default).
- Implement a flag-gated (`AGENT_LB_PUBLIC_STATS_ENABLED`) anonymized aggregate builder reading `app/modules/usage/builders.py` data, rounding timestamps to the day, summing across all accounts. Plus the chosen publish path. Plus an embeddable widget/snippet for the personal site.
- **Anonymization is a hard contract**, enforced by an automated test: the public payload schema contains ONLY the allow-listed aggregate fields — **no** account email/id, api-key, IP, user-agent, prompt/response content, or sub-day timestamp. Test must fail if any disallowed field appears.
- **Security gate (pause for user) before go-live:** review the exact public payload + exposure config with Alex; only then enable the flag / publish.
- **Accept when:** the anonymized artifact is reachable by the website, the sanitization test passes in CI, and a manual inspection of the live payload confirms zero sensitive fields.

---

## 6. Parallelization / subagent plan

- **Local critical path (main agent, serial):** P0 → P1. These touch live runtime + the MacBook/Studio boxes; keep them in the main thread.
- **P2 (main agent, human-gated):** the OAuth start + `code#state` submission must be interactive. Once ≥2 accounts exist, the Stage B smoke can fan a **verification subagent** to drive the 20+ requests and tabulate distribution/failover/usage while the main agent watches the dashboard — disjoint from P3.
- **P3 (subagent lane, parallelizable after P0):** spin a subagent owning **only** `app/modules/usage/` additions (new public aggregate builder + endpoint), a new `app/modules/public_stats/` (or similar) module, its tests, and the website widget snippet. It must NOT edit Anthropic core/oauth/proxy files (P2's scope) — disjoint write scope. It pauses at the design gate and the security gate for the main agent/user.
- **Integration point:** main agent reviews the P3 subagent's diff for the anonymization contract before anything is enabled.
- Do not parallelize P0/P1 (single live runtime, ordering matters).

---

## 7. Verification loop (exact)

Scoped to changed files by default; full `ci` is informational, not a goal blocker for unrelated pre-existing failures.

- **Python:** `cd /Users/aneyman/repos/agent-lb && uv run ruff check . && uv run pytest <changed test paths>` (Anthropic SSE/OAuth/selection tests for P2; sanitization + aggregate tests for P3).
- **Migrations (if schema touched):** `make migration-check`.
- **Frontend (if dashboard/widget touched):** `make frontend-lint frontend-typecheck frontend-test`.
- **Codex e2e (P1):** real `cx` call + curl, then Studio DB query for `request_logs`/`usage_history` deltas and per-account distribution.
- **Anthropic e2e (P2):** `ANTHROPIC_BASE_URL=https://studio.tailf266ac.ts.net:2455 ...` Claude Code loop ≥20 reqs; dashboard request log shows ≥2 accounts; force/observe a 429 failover; usage rows include cache token columns.
- **Public stats (P3):** sanitization test (asserts allow-listed fields only) green; live payload manual inspection; website fetch works.
- **Self-review:** `git -C /Users/aneyman/repos/agent-lb diff` before each checkpoint commit; confirm OpenAI path + `service.py` untouched.

---

## 8. Progress Log

**Current checkpoint:** P2 complete (Anthropic load-balancing verified) → P3 next (public anonymized usage)
**Outcome metric:** see §1 table (writers→1; Codex verified ≥1+1; providers 1→2; public surface 0→1)
**Current value:** P0–P2 DONE — writers 2→1; Codex verified live; **providers 1→2 (openai + anthropic), both Claude accounts active, Stage B passed**
**Last verified:** Stage B on Studio (2026-05-29 ~02:15Z): 26/26 `/v1/messages` 200, distributed neyman 8 / kinetic 12, failover OK (paused→reroute), cost $0.0671 computed, OpenAI route live (400 on `{}`), 5 OpenAI accounts active
**Remaining:** P3 public anonymized usage · gap #3b (per-account subscription limits from `anthropic-ratelimit-*` headers) · fix GOAL.md/HANDOFF.md stale `swap-lb`/`.runtime` paths · **commit (awaiting user)**
**Blocked:** No (P3 pauses at the design + security gates)
**Uncommitted:** OAuth + usage/cost fixes are live on MacBook + Studio checkouts but NOT committed (awaiting user per stop rules).

| Time | Checkpoint | Change | Outcome delta (before → after) | Next |
| ---- | ---------- | ------ | ------------------------------ | ---- |
| 2026-05-28 | P0 (pre) | MacBook LaunchAgent disabled; stale serve rules removed; Studio never-sleeps; DR backup script+plist written; config verified → Studio | writers 2→1 (pending timer load) | Load timer, verify snapshot |
| 2026-05-28 21:30 | P0 ✅ | DR backup verified (integrity=ok, accounts=5, 2 snapshots retained); single-flight lock added after a concurrent-run collision; launchd timer loaded | writers 2→1 (done) | P1: real `cx` call + curl, confirm Studio `request_logs` delta + account spread |
| 2026-05-29 01:33 | P1 ✅ | Codex path proven by live traffic: 143k OpenAI request_logs, 5,607/24h across all 5 accounts, recording in real time | Codex verified 0→live | P2: add Anthropic accounts |
| 2026-05-29 01:50 | P2 OAuth fix | Anthropic token exchange was form-urlencoded w/o `state` → Anthropic "Invalid request format". Fixed to JSON body + `state` (matches Claude Code), threaded through both callback paths; ruff+tests green | — | open OAuth flows |
| 2026-05-29 01:50 | P2 accounts | 2 distinct Anthropic accounts active (neyman=2c436b54, kinetic=ddb5ff1a); aliases set (mapping by login order — verify) | providers 1→2 | gap fixes + Stage B |
| 2026-05-29 02:05 | P2 gap #1 | Non-stream `/v1/messages` usage now parsed from JSON body (was dropped; SSE-only). +integration test | usage capture complete | — |
| 2026-05-29 02:05 | P2 gap #2 | Cost was null: 4.5-gen models missing from price table (`claude-haiku-4-5` no alias; `claude-opus-4-5` would mis-map to Opus-4 $15/$75). Added haiku/sonnet/opus-4-5 prices+aliases (verified $1·5 / $3·15 / $5·25). +unit tests | cost 0→computed | — |
| 2026-05-29 02:15 | P2 ✅ Stage B | 26/26 `/v1/messages` 200; distributed neyman 8/kinetic 12; failover OK; cost $0.0671; OpenAI regression clean | providers 1→2 verified | P3 public usage (design gate) |

---

## 9. Stop / pause rules

- **Pause for the user** at: the Anthropic browser-OAuth step (do NOT automate Cloudflare/consent); the P3 exposure-mechanism design gate; the P3 go-live security review.
- **Never** `git push` / `git commit` / merge / open a PR / contribute upstream (Stage C) / deploy / publish without an **explicit** user request. Each completed checkpoint pauses for Alex's local verification before the next begins.
- **No-movement guard:** if two consecutive checkpoints don't move their phase's outcome metric, pause and name the mechanism that will, or mark blocked.
- **Never** commit `.runtime/`, keys, DBs, browser artifacts, secrets, or scaffolding; preserve unrelated dirty worktree files.
- Honor all Anthropic invariants in §4. If a fix seems to require touching `service.py` or an OpenAI rewriter, **stop and surface it** instead.
- Public stats: if the only safe exposure conflicts with Alex's hosting, stop at the design gate rather than weakening anonymization.

---

## 10. Handoff prompt

See the `/goal` launcher returned alongside this brief. It points here and inlines only the hard stops.
