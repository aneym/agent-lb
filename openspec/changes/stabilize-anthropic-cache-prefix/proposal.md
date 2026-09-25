# Stabilize Anthropic cache prefixes

Claude Code 2.1.280 sends a per-request billing block (`x-anthropic-billing-header: ...; cch=...`) as the first system block. Anthropic recognizes that block only in first position and keeps it out of the cached prefix; a direct Claude Code session caches normally with it.

The proxy prepended its Claude Code identity whenever the first block was not the identity line, which pushed the billing block to index 1, where upstream treats it as prompt text. Its `cch` changes on every request, so every request re-wrote its whole context. Two interim fixes (move the block after the system breakpoints, then pin `cch`/`cc_prompt_id` per session) repaired main-thread turns only: payloads without `cc_prompt_id` (in-process teammates, notification-driven turns) still missed the cache on about 99% of calls through 2026-09-25.

Correct fix: treat a payload whose first system block is the billing block as a Claude Code payload and forward it unchanged. Remove the move and pin transforms.
