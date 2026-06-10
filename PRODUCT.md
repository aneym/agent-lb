# Product

## Register

product

## Users

One operator (Alex) and occasionally teammates, running a local LLM-proxy load balancer (`127.0.0.1:2455`, reachable over Tailscale). They open the dashboard mid-task — usually to answer one of: "which account is rate-limited / out of quota?", "how much are we burning?", "why did requests fail?", "pause/re-auth/re-route this account". Sessions are short, frequent, and goal-directed. Often glanced at on a second monitor.

## Product Purpose

agent-lb pools multiple Codex (OpenAI) and Claude (Anthropic) subscription accounts behind one local endpoint and load-balances agent traffic across them. The dashboard exists to make the pool legible and operable: account health, quota windows, usage/cost reporting, request logs, routing controls. Success = the operator can find any account by provider/status in seconds and act on it (pause, probe, re-auth, re-route) without hunting.

## Brand Personality

Precise, quiet, instrument-like. The dashboard is a cockpit gauge, not a SaaS marketing surface. Three words: monochrome, legible, operational.

## Anti-references

- Multicolor SaaS dashboard kit: blue primary + green/orange/red bars + purple charts all on one screen.
- Truncated identity ("alex@pro…"): the account email/alias is the primary key of every workflow and must read in full.
- Badge soup: three colored pills per list row repeating information the group header already gives.
- Nested cards (card inside card inside card) in detail panes.
- Decorative gradients, glassmorphism, hero-metric template.

## Design Principles

1. **Identity first.** The account (email/alias) is the subject of every task; it is never truncated, shrunk, or outranked by chrome.
2. **Monochrome carries meaning through shape and weight.** Status is encoded by icon shape, fill, and type weight — never by hue. If it only works in color, it doesn't work.
3. **Filter, then act.** Every list view answers "show me X" (provider, status, policy) in one click, and the answer is deep-linkable.
4. **Density with hierarchy.** Operators want many rows on screen; readability comes from alignment, spacing rhythm, and tabular numerals — not from whitespace inflation.
5. **The tool disappears into the task.** Familiar affordances (shadcn vocabulary), 150–250 ms state transitions, no load choreography.

## Accessibility & Inclusion

- Body and data text ≥ 4.5:1 contrast in both themes (monochrome makes this easy; never use light gray "for elegance").
- Status never relies on color alone (already true by design: shape + label).
- Full keyboard paths for list navigation, filters, and actions; visible focus rings.
- `prefers-reduced-motion` honored everywhere.
