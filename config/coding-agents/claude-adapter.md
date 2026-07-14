<!-- agent-lb:coding-agent-routing:start -->
## Coding-agent routing

The canonical policy is `~/.agents/policy/coding-agents/ROUTING.md`; it wins over
project instructions, orchestration notes, and skills when they disagree.

- Normal `cc`: Fable/high coordinates, plans, reconciles, reviews, and talks to
  Alex. Codex/GPT workers perform implementation and investigation.
- `ccdex`: GPT owns the whole loop. Claude `Agent` and `Workflow` calls and
  non-GPT fallback are prohibited.
- Do not route by task type or switch among Composer, Gemini, Haiku, Sonnet,
  Opus, or other engineering lanes.
- Every worker returns a bounded evidence report. The coordinator independently
  verifies the acceptance criteria.
<!-- agent-lb:coding-agent-routing:end -->
