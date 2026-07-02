## Purpose

Three changes shipped together because they land in the same UI cycle and
share one motivating direction from the owner: filter selection should scope
everything, not just some things.

## Scoped stats — rationale

§9.2 already scoped pool windows, the accounts list, and Recent when a
provider was selected. §11 (value multiple) and the status icon deliberately
stayed pool-global, reasoning that the numerator (`totalUsd7d`) was an
all-providers server figure and scoping only the denominator would
misrepresent the ratio. The owner's direction for this cycle overrides that:
when a user picks "Claude" in the scope control, they expect *every* number
on screen — including the value multiple and the little icon in the menu
bar — to answer "what is my Claude usage/value," not a mix of Claude
denominator against pool-global numerator. This is a deliberate, explicit
supersession of the §11 decision, not a bug fix; §11 stays in the design doc
as a record of what shipped and why it changed (see §13).

Making this correct requires the server to do the scoping, not the client:
the client only sees the accounts list and un-scoped request logs, and has
no way to attribute `totalUsd7d`/error counts/token counts to a provider on
its own (unlike the §9.2 window math, which sums fields already present
per-account). Hence `GET /api/usage/summary?provider=<name>` — the summary
endpoint gains a filter, computed server-side against subscription-usable
accounts for that provider, with unattributed logs (`account_id IS NULL`,
e.g. pre-attribution or key-only traffic) dropped from a scoped result
rather than guessed at.

### The stale-fetch race

Switching scope now triggers a new network fetch (previously it triggered
only a synchronous recompute over already-loaded accounts). Two fetches can
be in flight if a user flips scope quickly (All → Claude → Codex within one
round trip). Without a guard, the All-scope response landing *after* the
user has already selected Codex would silently overwrite the Codex-scoped
UI with All-scope numbers — invisible to the user, wrong data on screen. The
guard tags each fetch with the scope it was issued under and drops the
response if the current scope no longer matches at completion time. This is
the same "last write should not always win" shape as any typeahead/search
race; the fix is standard (a monotonic request/generation token or
scope-equality check at apply time), scoped narrowly to the summary fetch
path.

### Privacy pseudonyms stay full-list-keyed

§12 already builds pseudonyms once from the full account list specifically
so they don't reshuffle when scope changes. This cycle doesn't touch that —
it's called out here only to make explicit that "scope now affects
everything" has one deliberate, pre-existing exception, so implementers
don't over-apply the new scoping principle to privacy mode.

### Worked example

Provider scope = Claude, pool has 5 Claude accounts (3 headline-countable)
and 4 Codex accounts. `GET /api/usage/summary?provider=anthropic` returns
`cost.totalUsd7d` computed only over the 3 Claude accounts' attributed
request logs (Codex logs and any `account_id IS NULL` logs excluded). The
§13 value-multiple denominator sums monthly plan price over those same 3
Claude accounts only. If all 3 are Claude Max at $200/mo: weekly plan cost
`≈ $137.99`; if the scoped `totalUsd7d` is `$2,000`, the multiple is
`≈14.5×` — a materially different number than the pool-global multiple
these accounts would have contributed to before this change, which is the
intended, disclosed behavior change.

## Two-ring status icon — rationale

The menu-bar icon has only ever encoded the 5-hour (primary) window; a user
whose 5-hour usage looks fine can still be about to hit a weekly cap with no
menu-bar signal until they open the panel. A second concentric ring —
inner, smaller radius — adds the weekly (or monthly-fallback) percent
without adding icon states or changing the icon's footprint.

### Risk-state inner-ring tradeoff

The risk state already draws an exclamation glyph over the ring to flag an
urgent condition. Two thin concentric rings plus an exclamation glyph at
menu-bar pixel dimensions (18-22pt) was tested to be illegible — the inner
ring and the glyph's stroke collide. The decision: risk state keeps the
outer ring (the window that triggered risk) and the exclamation, and simply
omits the inner ring. This trades one degree of information (weekly %
during a risk state) for legibility of the higher-priority risk signal —
consistent with the rest of the icon design's bias toward glanceability over
completeness (see design.md §2 icon principles).

### Unknown state

Unknown draws both rings as track-only (no fill arc) — same as the existing
single-ring unknown treatment, just duplicated per ring — so "no data yet"
never looks like "0% remaining."

## Icon-chip removal — rationale

The design language elsewhere (see §2.3, §8) uses flat monochrome SF
Symbols with no background chip; the header eye/refresh/overflow buttons and
the footer power button were the last holdouts still drawing a circular
glass chip behind the glyph, which reads visually heavier and inconsistent
next to the segmented scope control and metrics strip. Removing the chip is
purely a background/material change — hit target (22×22, matching Apple HIG
minimum touch target guidance) and accessibility label are unchanged.
Buttons that carry visible text (not icon-only) keep their chips; this
principle applies only to icon-only affordances, where the glyph alone now
carries the full visual weight of the control.

## Failure modes handled

- Scoped `/api/usage/summary?provider=doesnotexist` returns an empty-but-
  valid summary (zeroed windows/cost/metrics), never a 4xx/5xx — the client
  scope control only ever offers known providers, but the endpoint itself
  must not assume that.
- A scope switch during an in-flight fetch never lets the older-scope
  response apply after a newer scope has been selected.
- A provider scope with zero subscription-usable accounts (e.g. all Claude
  accounts paused) still returns a well-formed, zeroed summary — the value
  multiple line is simply absent per the existing §11/§13 "unpriceable ⇒
  line absent" rule, not an error state.
