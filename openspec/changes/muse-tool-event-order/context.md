# Parallel-tool incident

Real Muse SSE emits added events A/B/C then their done events A/B/C, unlike the sequential assumption in the shared translator. Captured raw SSE replay produced five calls before normalization and three afterward. Same-call-ID repeats in real lane Claude Code receipts confirmed actual duplicate side effects, not independent model decisions.

Normalize only Muse events by suppressing partial function events and emitting each done call once. No full call-argument buffering is needed; only current SSE frame and completed IDs are retained. Upstream errors pass through, incomplete calls never execute. GPT and native Muse transports remain unchanged.

Real Claude Code parallel session 8590086b-feaf-4dec-b709-1e30880eb68d called Bash three times with append commands. Each output file contained exactly one character A/B/C and the final turn returned PARALLEL_OK. Evidence: /Users/aneyman/.agent-rails/lanes/muse-claude-code/evidence/parallel-cc.jsonl and multi-raw.sse.
