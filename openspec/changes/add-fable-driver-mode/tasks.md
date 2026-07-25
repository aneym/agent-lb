# add-fable-driver-mode — tasks

## 1. Fable driver client
- [x] 1.1 Add `clients/fable`: exec `claude-lb-launch` with `ANTHROPIC_MODEL`,
      `ANTHROPIC_DEFAULT_OPUS_MODEL`, and `ANTHROPIC_DEFAULT_FABLE_MODEL` set
      to `claude-fable-5` (override via `AGENT_LB_FABLE_MODEL`); seat slots
      left unwritten and named in the module so the intent is auditable.
- [x] 1.2 Generalize `scripts/install-claude-clients.sh` to a `CLIENT_NAMES`
      loop (`cc`, `fable`) across print, install, and uninstall, keeping the
      backup and ownership-gated removal behavior per client.

## 2. Plan reviewer seat
- [x] 2.1 Add `config/coding-agents/agents/plan-reviewer.md`: `claude-planner`
      alias, `effort: high`, `tools: [Read, Grep, Glob, Bash]`,
      `disallowedTools: [Write, Edit]`, ROUTING.md as first action, and a
      procedure that fact-checks repo claims before judging the plan.
- [x] 2.2 Register the definition in `install-policy.py` `MANAGED_AGENTS`
      with the `agent-lb:plan-reviewer:v1` ownership marker.

## 3. Canon
- [x] 3.1 Add the Plan reviewer row to the canonical seat table and the
      rationale paragraph for the second sanctioned Fable seat.
- [x] 3.2 Add the "Fable driver mode" section (driver-swap shape, why the seat
      slots stay unset, hands-vs-brain still binds inside the mode).
- [x] 3.3 Record both clients under Runtime enforcement.

## 4. Verification
- [x] 4.1 Extend `verify-routing`: plan-reviewer source/alias/effort/no-write,
      installed match, ownership marker, seat-table row; fable client
      executable, target model, driver slots swapped, seat slots untouched,
      canonical launcher, installer wiring, and the ROUTING.md section.
- [x] 4.2 Run the policy installer and `verify-routing` on this machine.
- [x] 4.3 Exercise `fable` through `CLAUDE_LB_DRY_RUN=1` and confirm the
      launched command and environment.
- [x] 4.4 `ruff check app clients` and `openspec validate --specs`.
