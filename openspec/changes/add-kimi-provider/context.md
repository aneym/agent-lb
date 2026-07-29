# Context: add-kimi-provider

Research digest from the 2026-07-17 four-seat fan-out (video analysis, K3
facts, agent-lb architecture map, CLIProxyAPI study). Confidence tags matter:
several auth details are reverse-engineered from third-party projects and MUST
be verified against a live capture before hardening.

## Upstreams (both Anthropic-Messages-native)

| Path | Base URL | Auth | Cost model |
| --- | --- | --- | --- |
| A: platform API | `https://api.moonshot.ai/anthropic` (`/v1/messages`) | API key | pay-per-token: $3/Mtok in ($0.30 cached), $15/Mtok out |
| B: subscription | `https://api.kimi.com/coding` (`/v1/messages`, protocol selectable) | OAuth bearer + `X-Msh-*` headers | membership tiers, rolling 5h window + 7-day cap |

Membership tiers (verify current): Moderato $19/mo (256K ctx), Allegretto
$39/mo (1M ctx, Agent Swarm), Allegro $99, Vivace $199. `kimi-k3` needs 1M ctx
for full value → Allegretto floor.

## Subscription OAuth mechanics (Phase B)

- RFC 8628 device-code grant against `https://auth.kimi.com`
  (`/api/oauth/device_authorization`, `/api/oauth/token`). Public client id
  `17e5f671-d194-4dfb-9706-5516cb48c098` (same one kimi-cli and Kimi Code
  use; no registration). Poll interval min 5s; device-code TTL ~15m. Kimi
  returns HTTP 200 for pending states; discriminate on the `error` field
  (`authorization_pending`/`slow_down` continue; `expired_token`/
  `access_denied` abort).
- Token lifetimes (reverse-engineered, medium confidence): access ~15 min,
  refresh ~30 days. Refresh with a lead window (~5 min before expiry), not
  reactively on 401.
- CRITICAL — coding-agent gate: the subscription endpoint rejects generic
  proxies with `access_terminated_error` ("only available for Coding Agents
  such as Kimi CLI, Claude Code, Roo Code..."). Requests MUST carry the
  `X-Msh-Platform`, `X-Msh-Version`, `X-Msh-Device-Name`, `X-Msh-Device-Model`,
  `X-Msh-Os-Version`, `X-Msh-Device-Id` headers with a stable device id per
  account. Exact header values: capture from a live kimi-cli session before
  hardening (CLIProxyAPI reference: `kimi_executor.go:619-724`).
- Known upstream bug: kimi-cli OAuth subagent requests 401 (MoonshotAI/kimi-cli
  issue #1983). Multi-agent fan-out through one subscription token may be
  unreliable; Phase A sidesteps this.

## Prior art (CLIProxyAPI, `router-for-me/CLIProxyAPI`, Go)

Theo's confirmed reference ("CLIProxyAPI stays undefeated",
x.com/theo/status/2077883902635725018). Patterns adopted into the spec:

1. Single-flight token refresh keyed on refresh token (their
   `internal/auth/kimi/kimi.go:44,356`) → prevents concurrent refreshes
   invalidating each other. Python analogue: per-account asyncio lock map.
   Note agent-lb already has an `oauth-refresh-safety` capability; extend it,
   do not build a parallel mechanism.
2. Status-specific cooldowns (their `conductor.go:4338-4459`): 401→30m,
   402/403→30m, 404→12h, 429→exponential (base 1s, cap 30m) honoring
   `Retry-After`; transient 5xx short/configurable.
3. Burst suppression: failures inside an open quota window reuse the window
   instead of re-escalating backoff (`conductor.go:4431-4441`).
4. Fill-first selection to stagger rolling-window caps — agent-lb already has
   `add-fill-first-routing-strategy`; Moonshot accounts should be eligible for
   it rather than growing a new selector.
5. Kimi request-shape quirks their issues surfaced (mirror in our forwarding
   path, with regression coverage):
   - assistant tool-call history missing `reasoning_content` is rejected when
     thinking is on (#3719);
   - empty-string text content parts are rejected (#3891);
   - `tool_call_id` chains must stay consistent (#3184).
6. Model churn: stale hardcoded catalogs repeatedly broke keys (#3589, #2807,
   #3040, #4378). Hence prefix routing without an exhaustive catalog.

## agent-lb integration map (from repo scout, 2026-07-17)

- Template: `GlmProvider` (`app/core/providers/glm.py`) — clone as
  `MoonshotProvider`, register in `app/core/providers/registry.py`.
- Settings: add `moonshot_anthropic_upstream_base_url` (and a subscription
  base URL for Phase B) beside `glm_anthropic_upstream_base_url`
  (`app/core/config/settings.py:197`).
- Dispatch: ~12 provider-keyed helpers in
  `app/modules/proxy/anthropic_service.py` need a third branch (or a small
  metadata-driven refactor): `_provider_name_for_model:1258` (`kimi-` →
  moonshot), `_upstream_base_url:1268`, quota keys `:1262,1277,1285`, sticky
  prefix `:1333`, labels/errors `:1337-1349`.
- Import guard: `import_api_key_account` rejects non-GLM providers at
  `app/modules/accounts/service.py:446` — relax to accept `moonshot`.
- Probe: add a Moonshot probe model beside `DEFAULT_GLM_PROBE_MODEL`
  (`app/modules/accounts/probes.py:20`).
- Load balancer: confirm Moonshot lands in the non-Anthropic credits path
  (`app/modules/proxy/load_balancer.py:2045`).
- No usage polling needed: GLM precedent is cooldown-on-429 without
  `_USAGE_REFRESH_PROVIDERS` membership (`app/modules/usage/updater.py:155`).
  A future `/v1/usages` probe for precise subscription resets is optional
  (CLIProxyAPI PR #4373 is the pattern; still open upstream).
- Frontend accounts enum is `openai|anthropic` only; GLM ships backend-only.
  Moonshot follows the GLM precedent: backend/API/CLI-only at first.

## Routing decision context (not part of this change)

K3 evidence: #1 Arena Frontend Code (1679 Elo, independent), #3 AA Intelligence
Index, behind Fable 5 on FrontierSWE/GDPval/AA-Briefcase; ~2x output-token
verbosity, always-max reasoning, ~28 tok/s, hallucination regression vs K2.6.
Planned use: a benchmark-gated `frontend-implementer` seat pinned `kimi-k3`
building from the Fable frontend-designer's frozen specs. Driver and verifier
seats unchanged. Seat-table edits ship separately per ROUTING.md discipline.

## Verify-before-hardening list

1. Live-capture `X-Msh-*` header names/values and device-id derivation from a
   real kimi-cli login on this machine.
2. Confirm token lifetimes and refresh semantics from the same capture.
3. Confirm `kimi-k3` (and `k3[1m]` alias) are accepted model ids on both
   upstreams; do not bake aliases in until observed.
4. Per-tier quota numbers (5h call counts, weekly caps) vary by source.
