# fix-claude-fast-mode-quota-routing

## Why
Claude Code fast mode sends Anthropic Messages requests with `speed: "fast"` and
uses a separate Anthropic fast-mode rate limit. The fast-mode beta header can
remain present after Claude Code falls back to standard speed for prompt-cache
continuity, so the proxy must key routing from the explicit request speed rather
than from the header alone.

agent-lb currently stores Anthropic 429 cooldowns under the model/effort quota
family, such as `anthropic_top_thinking`. When the fast-mode pool is exhausted,
that can locally block the standard-speed fallback that Claude Code expects to
use.

## What Changes
- Introduce a dedicated `anthropic_fast` additional quota family for Anthropic
  Messages requests with `speed: "fast"`.
- Keep Claude session stickiness scoped to the underlying model/effort quota
  family so fast and standard turns in the same session keep account locality.
- Ensure Anthropic OAuth requests carry the OAuth beta header, and ensure
  `speed: "fast"` requests carry the fast-mode beta header.
- Parse Anthropic fast-mode reset headers when recording cooldown metadata.
- Add regression coverage for fast cooldown isolation and standard fallback.

## Impact
- Fast-mode rate limits no longer poison normal Claude top-thinking routing.
- Claude Code fallback can continue through agent-lb after fast-mode exhaustion.
- Existing Anthropic standard/top/top-thinking quota behavior remains unchanged.
