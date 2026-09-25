# Hold OpenAI/Codex sessions on their account until it is exhausted

A Codex prompt cache belongs to the account that wrote it, as a Claude one does. The OpenAI side still moved pinned conversations under budget pressure (95% by default) and burn-first drain. It also moved a prompt-cache pin to a fallback without persisting it, so the conversation came back to the old account on recovery and rewrote its cache a second time.

Change: the Claude-side hold (`anthropic_sticky_hold_until_exhausted`) applies to OpenAI sticky sessions behind `openai_sticky_hold_until_exhausted` (default on). A pinned conversation stays on its account until the account is unselectable (a live 429 or usage-limit cooldown, or 100% usage). It then fails over once and the new pin persists.

Same change, bridge side: a bridged Claude turn whose account rejects it (401/403, or a token that cannot be refreshed) moves to another usable account instead of showing Claude Code "Please run /login". Bridged streams now report usage in Anthropic's shape, so transcripts no longer record 0/0.
