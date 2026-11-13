## ADR-0002: Keep driver aliases separate from canonical model seats

- **Date:** 2026-09-13
- **Status:** Accepted
- **Scope:** Coding-agent launchers and doctor diagnostics

### Context

Provider aliases are not interchangeable labels. They select distinct budget
pools, while canonical model seats describe the role a model must fill. Writing
a driver alias into every model slot can silently move an Opus seat onto the
Fable budget and corrupt the intended lineup.

Claude Code creates and caches its session agent registry. If doctor does not
validate the configured aliases first, an invalid routing setup can be cached
for the session lifetime.

### Decision

- A driver launcher owns only its driver slots. The Fable launcher owns
  `ANTHROPIC_MODEL` and `ANTHROPIC_DEFAULT_FABLE_MODEL`.
- Canonical seats are resolved independently. The Fable launcher always writes
  `ANTHROPIC_DEFAULT_OPUS_MODEL` from a nonempty `AGENT_LB_OPUS_MODEL`, falling
  back to `claude-opus-5`.
- Sonnet, Haiku, and subagent seat slots remain outside the Fable driver swap.
- Doctor validates model aliases and canonical-seat assignments before Claude
  Code creates and caches its session agent registry.

### Consequences

- An inherited Fable value cannot poison the canonical Opus slot.
- Driver changes do not implicitly become lineup changes.
- Source inspection and unit tests establish configuration behavior only; they
  do not constitute a live routing or provider-budget claim.
