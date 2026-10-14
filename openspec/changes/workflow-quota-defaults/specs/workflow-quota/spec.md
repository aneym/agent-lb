## ADDED Requirements
### Requirement: Workflow quota defaults
Workflow helper options SHALL default to Sol and emit an agent cap of 6 in tight mode or 16 in normal mode. Every top-level tab and workflow orchestrator SHALL run opus-latest at medium effort. Opus judge and synthesis stages SHALL also run at medium effort. Top-level defaults SHALL never use xhigh, max or Fable. Completion hooks SHALL append quota percentage when captured, otherwise token usage.

#### Scenario: Tight mode workflow
- **WHEN** route workflow-args reads tight quota mode
- **THEN** it emits Sol stage options and an agent cap of 6

### Requirement: Compact hook context
Context hooks SHALL emit at most 1024 UTF-8 bytes with commands that retrieve complete context.

#### Scenario: Resume a lane
- **WHEN** the lane resumes with state and learnings
- **THEN** the hook emits identity and short pointers within 1024 bytes

#### Scenario: Workflow orchestrator
- **WHEN** route workflow-args emits workflow options
- **THEN** it emits an opus-latest medium orchestrator and opus-latest medium judge and synthesis stages
