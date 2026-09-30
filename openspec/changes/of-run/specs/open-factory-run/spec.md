## ADDED Requirements

### Requirement: One command executes a routed brief
`of run <class>` and `open-factory run <class>` SHALL accept a trailing brief or `--brief-file`, plus intended model, author vendor, cwd, timeout and JSON options. They SHALL use `route pick` and select the first matching menu model or alias when intended is supplied. An absent intended model SHALL be recorded with a null seat and an explanatory reason.

#### Scenario: Intended model is absent
- **WHEN** the intended model is not on the class menu
- **THEN** the route pick executes and the receipt records the absent intended model

### Requirement: Vendor adapters preserve execution boundaries
Anthropic pools SHALL use claude-lb-launch with the class intent environment; OpenAI pools SHALL use codex exec; Cursor and Devin pools SHALL use seat run. Explore, research, review, verify and plan SHALL receive read-only modes. Binary resolution SHALL honor `OF_BIN_<NAME>` before other locations. Each attempt SHALL save stdout and stderr under `~/.agent-lb/of/runs/<decision_id>/`.

#### Scenario: Read-only job
- **WHEN** an explore job runs through Codex
- **THEN** its sandbox is read-only and its working directory is the requested cwd

### Requirement: Stand-ins are bounded and receipted
A successful exit SHALL be ok; seat exit 3 or quota/limit output SHALL be limit; exit 75 or 124, timeout or OSError SHALL be infra; other exits SHALL be fail. Limit and infra SHALL advance through rung skip picks when available, otherwise initial fallbacks, for at most three attempts. Fail SHALL stop. Each run SHALL append one of_decision and one of_outcome per attempt, and JSON SHALL report intended, successful ran seat or null, standing_in, reason, attempts, final output path and exit. Exit SHALL be 0 on success, 1 on fail and 2 when unavailable or exhausted.

#### Scenario: Codex quota exhausted
- **WHEN** the first Codex attempt reports a usage limit and the Claude stand-in succeeds
- **THEN** both attempt outcomes are recorded and the run succeeds with standing_in true

### Requirement: Installation and diagnostics are simple
`install.sh [--prefix DIR]` SHALL symlink of and open-factory into the prefix, defaulting to ~/.local/bin, and suggest of doctor. Doctor SHALL check seat, codex and active stand-ins; a stand-ins 404 SHALL report not deployed without failing. The README SHALL cover installation, doctor, two run examples, start, receipts and ladder baseline opt-out in fewer than 60 lines.

#### Scenario: Symlink installation
- **WHEN** the installer is given a prefix
- **THEN** the installed of command reports open-factory 0.3.0
