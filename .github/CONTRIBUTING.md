# Contributing to agent-lb

Thanks for thinking about contributing to agent-lb! This document covers
everything you need to get from "clone" to "merged PR" without re-discovering
the conventions yourself.

If anything below is unclear or out of date, please open a small `docs:` PR —
that's the easiest first contribution.

---

## Table of contents

1. [Code of conduct](#code-of-conduct)
2. [Ways to contribute](#ways-to-contribute)
3. [Development setup](#development-setup)
4. [Project layout](#project-layout)
5. [Workflow: OpenSpec-first](#workflow-openspec-first)
6. [Coding conventions](#coding-conventions)
7. [Commit & PR conventions](#commit--pr-conventions)
8. [Merge gates and collaborator rules](#merge-gates-and-collaborator-rules)
9. [Tests](#tests)
10. [Security issues](#security-issues)

---

## Code of conduct

Participation in this project is governed by the
[Contributor Covenant Code of Conduct](../CODE_OF_CONDUCT.md). By participating,
you agree to uphold it.

## Ways to contribute

You don't need to write code to help:

- **Triage**: reproduce open issues, ask for missing details, add labels.
- **Docs**: improve the README, fix typos, document a deployment recipe.
- **Discussions**: answer "how do I…?" questions in
  [GitHub Discussions](https://github.com/aneym/agent-lb/discussions).
- **Bug reports**: file a structured report using the Bug Report issue form.
- **Feature ideas**: propose them in Discussions first if open-ended; use the
  Feature Request form when the idea is concrete.
- **Code**: fix a bug, implement a feature, refactor a hot spot.

Look for issues labelled
[`good first issue`](https://github.com/aneym/agent-lb/issues?q=is%3Aopen+is%3Aissue+label%3A%22good+first+issue%22) or
[`help wanted`](https://github.com/aneym/agent-lb/issues?q=is%3Aopen+is%3Aissue+label%3A%22help+wanted%22)
if you're looking for a starter task.

## Development setup

agent-lb is a Python project managed with [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Fork & clone
git clone https://github.com/<your-user>/agent-lb.git
cd agent-lb

# 2. Install Python + deps via uv
uv sync --all-extras --dev

# 3. Activate the venv (optional — `uv run` works without activating)
source .venv/bin/activate

# 4. Install pre-commit hooks
uv run pre-commit install

# 5. Run the proxy locally
uv run agent-lb --help
```

Frontend (dashboard) lives under `frontend/`. Use the project's standard
package manager (see `frontend/package.json`).

## Project layout

```
.
├── app/                  # Python application (proxy, accounts, dashboard backend)
├── frontend/             # Dashboard SPA
├── tests/
│   ├── unit/             # Fast, isolated tests
│   └── integration/      # End-to-end / network-touching tests
├── openspec/             # SSOT — specs, change proposals, archived changes
│   ├── specs/<capability>/
│   └── changes/<change>/
└── .github/              # Issue forms, PR template, workflows
```

## Workflow: OpenSpec-first

agent-lb uses **OpenSpec as the source of truth** for change-driven development.
You don't have to author OpenSpec entries for trivial fixes, but for anything
that changes observable behavior, requirements, contracts, or schema, an
OpenSpec change is expected.

The typical flow:

1. Find the relevant spec in `openspec/specs/<capability>/spec.md`.
2. If your change alters behavior: create
   `openspec/changes/<kebab-case-change>/` with a proposal + tasks.
3. Implement the tasks; keep `spec.md` in sync.
4. Validate: `openspec validate --specs`.
5. Verify and archive when done.

If you're unsure whether your PR needs an OpenSpec change, open it as draft
and ask — a maintainer will tell you.

**Do not** hand-edit `CHANGELOG.md`.

## Coding conventions

- **Python**: 3.13+. Code is formatted with `ruff format`, linted with
  `ruff check`, and type-checked with `ty` (Astral's type checker).
- **Imports / style**: follow what you see in nearby files; the linter is
  authoritative — if `ruff` and your style guide disagree, ruff wins.
- **Type hints**: required on new public functions and new modules. Existing
  code is being typed incrementally.
- **Logging**: prefer the project's structured logger over `print`.
- **Secrets**: never commit API keys, tokens, account credentials, or `.env`
  files. The CI redaction is best-effort, not a safety net.

Run the complete local CI tier before asking to merge:

```bash
python3 scripts/local_ci.py run <PR|sha|main>
python3 scripts/local_ci.py status <40sha>
```

Use `python3 scripts/local_ci.py show <40sha>` to inspect the receipt and
`python3 scripts/local_ci.py doctor` to diagnose unavailable local tools.
Local CI always runs the entire suite; a selector is not a merge gate.

```bash
make lint
make test-floor
make package
```

## Commit & PR conventions


Format:

```
<type>(<scope>)?: <subject>

[optional body]

[optional footer(s)]
```

Common `type`s:

| Type        | When to use it                                            |
|-------------|-----------------------------------------------------------|
| `fix`       | Bug fix → patch version bump                              |
| `feat`      | New user-facing feature → minor version bump              |
| `refactor`  | Internal change, no behavior change                       |
| `perf`      | Performance improvement                                   |
| `docs`      | Documentation only                                        |
| `test`      | Test-only change                                          |
| `build`     | Build system / packaging                                  |
| `ci`        | CI configuration                                          |
| `chore`     | Tooling / maintenance with no user-visible effect         |

Common `scope`s used in this repo: `proxy`, `accounts`, `auth`, `oauth`,
`quota`, `chat`, `proxy-responses`, `dashboard-auth`, `ui`, `db`, `types`,
`openspec`, `main` (release).

**Breaking changes**: append `!` after the type/scope and/or add a
`BREAKING CHANGE:` footer.

Examples (from the actual repo history):

```
fix(proxy): handle model fetch timeouts
fix(accounts): recover quota status from usage refresh
feat(proxy): add OpenAI-compatible /v1/images API (gpt-image-2 via image_generation tool)
fix(dashboard-auth): reject passwords longer than bcrypt's 72-byte limit
```


### Pull request flow

1. Create a branch from `main`: `git checkout -b <type>/<short-name>`.
2. Make atomic commits with Conventional Commit titles.
3. Run local CI and retain its exact-SHA receipt (see above).
4. Open a PR using the template. Link the relevant issue.
5. An owner or coordinator reviews the current diff. Address findings with
   follow-up commits — no force-pushing during active review.
6. Once the exact-SHA local receipt is green and the diff is reviewed, a
   maintainer squash-merges with a clean
   Conventional Commits title.

## Merge gates and collaborator rules

These rules apply to **every PR**, regardless of author (external
contributor, project owner, or collaborator). They formalize the review
bar that has produced the current proxy / continuity-recovery / OpenSpec
quality so far.

### Merge gates

Before a PR is squash-merged into `main`:

1. **Local CI must be green for the exact merge candidate SHA.** Run
   `python3 scripts/local_ci.py run <PR|sha|main>` in its isolated worktree,
   then require `python3 scripts/local_ci.py status <40sha>` to pass. The
   latest same-machine receipt and its log hashes must match that SHA, and every
   tier must pass. The full tier includes frontend, lint/types, SQLite tests,
   disposable PostgreSQL parity/migrations, package, Docker vulnerability
   A different machine's receipt is not authoritative.
2. **Owner/coordinator diff review is required.** Review the current candidate
   diff and address findings. Hosted CI and `🤖 codex` labels are not required
   merge dependencies and are not a substitute for this review.
3. **`mergeable=MERGEABLE` and `mergeStateStatus=CLEAN`** in the GitHub API and no requested-changes
   review may remain outstanding. Hosted status checks are not a merge gate.
   After merging, run `python3 scripts/local_ci.py run main` and retain the
   exact merged-SHA receipt. A red result remains red; it is not waived by
   being pre-existing.
4. **OpenSpec change folder for behavior changes** (see
   [Workflow: OpenSpec-first](#workflow-openspec-first)). Pure
   refactors, docs-only edits, dev-tool changes, and test stabilization
   PRs are exempt; everything else needs an `openspec/changes/<slug>/`
   entry.
5. **`Fixes #N` / `Closes #N` in the PR body** for anything that
   resolves an issue, so the issue close stays automatic and the merge
   stays traceable. Use `Refs #N` / `Related to #N` for partial cover.
### Collaborator rules

Collaborators (write-access contributors) follow two additional rules on
top of the merge gates above:

1. **No self-merge by default.** A collaborator's own PR is merged by
   another maintainer (or, until the project has more collaborators,
   by the project owner). Review independence matters more than
   turnaround.
2. **Large PRs get split.** Roughly:
   - If a PR is a stack tip pulling in unrelated commits from sibling
     branches, split it so each merged PR is a single scoped change.
   - If a single PR is over **~800 net lines** and spans multiple
     concerns, split it into reviewable pieces. A single 1500-line
     change scoped to one capability is fine; a 400-line change that
     touches the proxy hot path *and* the dashboard *and* the OAuth
     flow is not.

### Bus factor escape hatch

To keep the project unblocked if the owner is unavailable, the following
self-merge escape hatch applies:

- If a collaborator's PR has been waiting on a maintainer merge for
  **more than 14 days** with **all merge gates met** (exact-SHA local CI green,
  owner/coordinator review, `mergeable=CLEAN`, no
  outstanding requested-changes review, no objection from any other
  active collaborator in the thread), the PR author may self-merge.
- Self-merge under this clause **must** include a comment on the PR
  explicitly invoking the clause and linking to the date the merge
  gates first went green. Audit trail must stay clean.
- The clause is a safety valve, not a default path. If you're tempted to
  invoke it on a PR you opened less than two weeks ago, the merge gates
  probably aren't actually all green yet.

### What this is not

These rules are intentionally lightweight. They don't require:

- A hosted bot review or hosted CI check. The local receipt and
  owner/coordinator diff review are the baseline.
- Squash-merge commit message rewriting beyond the Conventional Commits
  title. The PR description ends up in the body; that's enough.
- A formal escalation process for disagreements. If a P1 finding is
  disputed, work it out in-thread; if it can't be resolved, leave the
  PR open and ping the owner.

## Tests

- **Kept-floor guards** live under `tests/floor/`.
- **Integration tests** live under `tests/integration/` and may spin up the
  app, hit local sockets, or use fixtures that mimic upstream.
- New behavior and endpoints need integration or end-to-end coverage.
- Run a focused subset with:

```bash
uv run pytest tests/integration/test_request_logs_options_api.py -q
```

## Security issues

Please do **not** open public issues for security vulnerabilities. Report them
privately via
[GitHub Security Advisories](https://github.com/aneym/agent-lb/security/advisories/new).
See [SECURITY.md](./SECURITY.md) for full details.

---

Happy hacking. If you get stuck, open a draft PR or start a Discussion —
maintainers would rather see a half-finished idea than no idea at all.
