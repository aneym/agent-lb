## ADDED Requirements

### Requirement: Versioned terms contracts
The CLI MUST accept JSON terms objects with version equal to 1, string glossary and source fields, a retired array and an allowlist array. Each retired entry MUST contain only old, forms, new, status and keep; old and new MUST be nonempty strings, forms MUST be a nonempty array of nonempty strings, keep MUST be an array of nonempty glob strings, and status MUST be settled or proposed. Allowlist entries MUST contain only string path, word and reason fields. Unknown fields MUST be rejected except the optional enforce field, which MUST NOT activate enforcement. Repeated --terms options MUST merge contracts. Without --terms, existing glossary/terms.json and glossary/rails-terms.json under the target repository MUST be loaded.

#### Scenario: Invalid terms file
- **WHEN** a user runs `of words check --terms bad.json` with an invalid contract
- **THEN** the command reports the invalid file and exits 2

#### Scenario: Default contracts
- **GIVEN** both default terms files exist in the repository
- **WHEN** a user runs `of words check`
- **THEN** terms from both files are checked

### Requirement: Warn-only token-aware checks
`of words check [paths...]` MUST scan tracked text for whole tokens, splitting snake case, kebab case and camel case while preserving digit suffixes. It MUST match fold_pipeline, foldPipeline, FoldResult, fold-pr and FOLD_X for fold, but not folder, unfold or scaffold. It MUST support multi-word forms and p6/it2 tokens. It MUST omit external hits identified by keep globs, allowlists, URLs or blockquotes. It MUST report path, line and replacement as text or structured hits with --json. Hits MUST NOT cause a nonzero exit; valid checks MUST exit 0. With --diff BASE it MUST inspect only added lines in BASE...HEAD.

#### Scenario: Hits warn without blocking
- **WHEN** tracked code contains a retired word and the user runs `of words check --json`
- **THEN** the report includes the hit and exits 0 without editing files

#### Scenario: Only added lines
- **GIVEN** an unchanged retired word and an added retired word
- **WHEN** the user checks a diff from the earlier commit
- **THEN** only the added-line hit is reported

### Requirement: Read-only rename plans and status
`of words rename OLD NEW --plan` MUST report hits grouped by external, file_name, db, api, cli, config, ui, prose and code in first-match precedence. It MUST include file-name hits, support repeated --repo and a JSON --repos-file list, report missing repositories and leave all repositories unchanged. Rename without --plan MUST exit 2. `of words status` MUST report per-term class counts and totals excluding external hits. Both commands MUST support --json.

#### Scenario: Multiple repositories and one missing path
- **WHEN** a rename plan includes two existing clean repositories and one missing path
- **THEN** the plan reports hits and the missing path, exits 0 and leaves git status --porcelain empty in both repositories

#### Scenario: Status counts
- **WHEN** a user runs `of words status --json`
- **THEN** each retired term has counts by class and a total excluding external hits

#### Scenario: No implicit rename
- **WHEN** a user runs `of words rename fold job` without --plan
- **THEN** the command exits 2 without modifying files

### Requirement: Complete glossary entries
`of glossary check` MUST read CONTEXT.md by default or --file PATH. It MUST accept JSON terms fences, labeled Markdown sections and Rails bold-term entries. Every entry MUST have a definition of 1 to 25 words, an example, not, related terms and a code name. A complete glossary MUST exit 0; missing entries, incomplete entries, invalid input or unreadable files MUST exit 1 with diagnostics.

#### Scenario: Complete entries
- **WHEN** all glossary entries have the required fields and definitions within the word limit
- **THEN** the command reports the number of complete entries and exits 0

#### Scenario: Incomplete entry
- **WHEN** an entry lacks not or has a definition exceeding 25 words
- **THEN** the command reports those failures and exits 1
