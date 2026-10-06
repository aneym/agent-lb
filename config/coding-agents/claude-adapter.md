<!-- agent-lb:coding-agent-routing:start -->

Coding-agent routing: `~/.agents/policy/coding-agents/ROUTING.md`. Opus plans, specs and judges; `gpt-implementer` (Sol, medium) writes code to a spec with one check, and `sonnet-implementer` (Sonnet, high) only when Codex is really out; a fresh verifier from the other vendor reviews every slice; a FAIL gets one fix round, then a verdict on the final diff, and after that Opus overrides with a logged reason, splits the slice or drops it. Name models by family alias (`route resolve <alias>`); retired models (Fable, Astra, gpt-5.6 and older) never run by default but run when a dispatch or brief names them, logged; only blocked models are refused (Alex, 2026-10-05).

<!-- agent-lb:coding-agent-routing:end -->
