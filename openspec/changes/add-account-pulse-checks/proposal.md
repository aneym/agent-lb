## Why

Account state only changed when an operator acted or live traffic failed. Because the
balancer excludes unusable accounts from selection, a broken account never sees live
traffic again, so its stored state silently diverges from upstream reality:

- A subscription lapse (upstream 403 "OAuth authentication is currently not allowed
  for this organization") left the account `active` in `/api/accounts` until an
  operator manually probed it.
- A recovered subscription stayed ledger-`canceled` until an operator manually ran
  `POST /api/accounts/{id}/subscription/check`.
- A revoked credential on an idle account was only discovered on use, and the live
  403 path conflates "unsubscribed" with "auth broken" (`invalid_api_key`).

The operator asked for automatic "pulse checks" so the dashboard and menubar always
distinguish three states end-to-end: active (auth'd + subscribed), auth'd but
unsubscribed, and disconnected (needs reauth).

## What Changes

- New `AccountPulseScheduler` (`app/modules/accounts/pulse.py`), modeled on the Auth
  Guardian scheduler (leader election, jitter, per-account failure backoff, bounded
  concurrency). Default-enabled, interval 6h, both env-tunable
  (`ACCOUNT_PULSE_ENABLED`, `ACCOUNT_PULSE_INTERVAL_SECONDS`,
  `ACCOUNT_PULSE_CONCURRENCY`, `ACCOUNT_PULSE_JITTER_SECONDS`,
  `ACCOUNT_PULSE_FAILURE_BACKOFF_{BASE,MAX}_SECONDS`).
- Each cycle probes every non-paused account with the provider-appropriate minimal
  request (Anthropic/GLM: `/v1/messages` with `max_tokens=4`; OpenAI:
  `codex/responses` stream that is never consumed). Probe senders are extracted to
  `app/modules/accounts/probes.py` and shared with the existing manual probe and
  subscription-check endpoints.
- Conservative classification (`classify_probe_result`): 2xx → healthy; 401 →
  disconnected; 403 with a known subscription-refusal marker → unsubscribed;
  everything else (network failures, 400 contract drift, unmarked 403, 429, 5xx) →
  inconclusive, no state change, exponential backoff.
- State transitions (all audited via `account_pulse_*` audit actions, all
  invalidating the account selection cache):
  - healthy + ledger `canceled` → ledger `active` (automatic recovery detection).
  - healthy + status `deactivated`/`reauth_required` → status `active` (automatic
    reactivation after the operator fixes the account upstream).
  - unsubscribed + ledger not `canceled` → ledger `canceled` with the upstream
    message in the notes; account `status` is NOT touched (credentials are intact).
  - disconnected + status `active` → status `reauth_required` with a pulse reason.
  - permanent token-refresh failure + status `active` → status `reauth_required`.
- Paused accounts are never probed (operator intent).

## Impact

- The dashboard (`/api/accounts`, overview) and menubar now converge on upstream
  reality within one pulse interval with zero operator action, including recovery.
- Cost: one minimal request per account per interval (4 output tokens on Anthropic,
  an unconsumed stream open on OpenAI) — negligible against any subscription quota.
- Multi-replica deployments are safe: the scheduler requires leader election when
  more than one replica is configured, mirroring the Auth Guardian gating.
- No DB schema changes: transitions reuse the existing account status and
  subscription-ledger columns.
