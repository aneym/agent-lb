## Purpose

Two independent menubar features shipped together because they touch the
same pool metrics strip and header chrome: a "value multiple" headline that
makes the pool's flat-rate-vs-metered value tangible, and a privacy mode that
makes the panel safe to screenshot for that same audience.

## Value multiple — rationale

The pool already tracks `summary.cost.totalUsd7d`: the API-equivalent retail
dollar value of tokens burned in the trailing 7 days, priced server-side at
list rates. That number alone is not meaningful without a denominator. The
denominator is what the pool's underlying flat-rate subscriptions cost for
the same 7 days — a number the menubar client has to derive itself, because
the server tracks credits/usage, not subscription billing.

### Decisions

- **Operator amount wins.** `subscription.amount` is what the operator was
  actually billed; it beats any guess. It only counts when denominated in
  USD — mixing currencies into one ratio would misrepresent the multiple.
- **List-price fallback, flagged.** Accounts without an operator-entered
  amount fall back to a client-side table of published list prices. Because
  these are estimates, not verified bills, any pool that used the fallback
  for even one counted account gets the `≈` prefix on the whole multiple —
  the estimate contaminates the aggregate, so it is disclosed at the
  aggregate level rather than tracked per-account in the UI.
- **Pool-global, not scope-filtered.** `totalUsd7d` is an all-providers
  server figure; scoping the denominator to one provider while the numerator
  stayed global would silently change what the ratio means. The line always
  reads the whole pool, matching the existing §9.2 honesty rule that hides
  (rather than mis-scopes) projections when a computation can't scope
  cleanly.
- **Denominator uses `isHeadlineCountable`, not `isRoutable`.** A paused
  account's subscription is still being paid for even though it's excluded
  from routing capacity — see `menubar-usable-account-counts`.
- **Layout-deterministic.** Per the `PanelLayout` design invariant (§2, §9),
  panel height must be a pure function of state, never a measured view. The
  value line is a fixed additional `metricsLines` count, present or absent
  as a whole unit — never a variable-height wrapped line.

### Plan price table (client-side fallback, USD/month)

| Provider  | planType          | Price |
| --------- | ------------------ | ----- |
| anthropic | `max` / `claude`   | $200  |
| anthropic | `max5` / `max_5x`  | $100  |
| anthropic | `pro`               | $20   |
| anthropic | `free`               | $0    |
| openai    | `pro`                | $200  |
| openai    | `plus`               | $20   |
| openai    | `free`               | $0    |

Per-seat / team / business / enterprise / unrecognized plan types resolve to
`nil` and are excluded from the denominator rather than guessed at.

### Worked example

3 headline-countable accounts: 2× Claude Max priced via the table (no
operator amount) at $200/mo each, 1× Codex Pro with an operator-entered
`subscription.amount` of $200 (USD). `summary.cost.totalUsd7d = $6,200`.

- Monthly plan cost: `200 + 200 + 200 = $600/mo`
- Weekly plan cost: `600 * 7 / 30.4375 ≈ $137.99/wk`
- Multiple: `6200 / 137.99 ≈ 44.9`
- Rendered line: `≈45× value · $6.2k vs $138/wk` — `≈` present because 2 of
  the 3 counted accounts used the fallback table.

### Failure modes handled

- Zero or negative `totalUsd7d` → line absent (no false "0×").
- No account resolves a price → line absent (avoids a `NaN`/`Inf` multiple).
- Mixed-currency `subscription.amount` → that account is treated as unpriced
  by the operator amount and falls through to the list-price table (or is
  excluded if the plan type isn't in the table).

### COPY GUARDRAIL (deliberate, not stylistic)

User-visible strings must say "value" / "× value on plan cost" and must
NEVER contain "arbitrage", "pooling", "reselling", or "circumvent". Heavy
personal use of one's own accounts is a permitted-but-rate-limited ToS
posture; pool/resell/circumvent language maps onto separately-prohibited,
termination-grade clauses. Internal type/comment names may say arbitrage
(e.g. `ArbitrageStats`); on-screen words must not.

## Privacy mode — rationale

The panel is genuinely interesting to share (the value multiple is the
headline reason to), but every account row currently shows a real email
address, and the header shows the operator's real remote hostname. Privacy
mode is a pure display-layer redaction: it never touches what data is
fetched or stored, only what identity-shaped strings render as.

### Decisions

- **Redact identity, never aggregates.** The whole point of sharing is the
  numbers (usage %, cost, tokens, the value multiple). Redacting those would
  defeat the feature; only *who* is hidden.
- **Pseudonyms keyed on accountId, not screen position.** Row order changes
  with sort mode and refresh-driven reordering; if pseudonyms were assigned
  by row index they would reshuffle on every re-sort, making "Claude 2" refer
  to a different account moment to moment. Keying on a fixed sort
  (`accountId` ascending) of the full account list — built once per account
  list snapshot in `RootView`, not per visible/scoped subset — keeps a given
  account's label stable for as long as it exists, independent of scope or
  sort.
- **Built from the full list, not the scoped/visible list.** If pseudonyms
  were built only from the currently-scoped accounts, switching scope would
  change the numbering (e.g. "Claude 1" in All-scope might not be "Claude 1"
  in Claude-only scope). Building from `appState.accounts` once and injecting
  via `EnvironmentValues.privacyMask` avoids that.
- **Layout-neutral by construction.** Redacted text (pseudonyms, `"remote"`)
  renders inside the same fixed-height row/line frames as the real text; it
  is not a `PanelLayout.Inputs` field, so toggling privacy mode never changes
  panel height — consistent with the same determinism invariant as the value
  multiple.
- **Two affordances, one source of truth.** The header eye/eye.slash button
  and the overflow "Hide Sensitive Info" toggle both bind
  `@AppStorage("privacyMode")` directly rather than each other, so either
  one flips both.

### Failure modes handled

- An account somehow absent from the pseudonym map (e.g. a race between list
  update and render) falls back to a generic provider label ("Claude" /
  "Codex" / "Account") rather than crashing or leaking the real name.
- Tooltip code paths that only carry an `accountId` (not a full `Account`)
  use the `name(forId:provider:real:)` lookup, so redaction covers
  string-built tooltips as well as SwiftUI rows.
