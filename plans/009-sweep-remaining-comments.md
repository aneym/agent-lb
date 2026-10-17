# Plan 009: The remaining ~2,200 comment lines are gone and new ones are blocked at push

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report; do not improvise. When done, update the status row for this plan
> in `plans/README.md`, unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: none needed; this plan re-measures from the
> live tree in Step 1. Run it last, after plans 001-008, so it does not sweep
> files those plans delete.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (mechanical edits across live launchers and restart tooling)
- **Depends on**: plans/001, 002, 003, 004, 005, 006, 007, 008
- **Category**: tech-debt
- **Planned at**: commit `63b62f18`, 2026-10-17

## Why this matters

House rule since 2026-10-14 (owner: "ban and delete all comments in the
facotry"): no code comments. Keep only shebangs, license headers,
compiler/lint/type directives, generated-file markers, and doc comments a
public API toolchain requires; docstrings count as comments. A first sweep
(commit `e4ce6356`, 2026-10-14) removed about 13,000 comment lines from
`app/` and tests but skipped extensionless scripts. At `63b62f18`,
`bin/no-new-comments` still finds 2,259 comment lines. The repo also has no
pre-push hook installed (`.git/hooks` has only samples, `core.hooksPath` unset),
so new comments land unchecked.

## Current state

The checker is `bin/no-new-comments` (172 lines). It lists comment lines added
between two commits; against the empty tree it lists every comment line:

```sh
python3 bin/no-new-comments --repo . 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD 2> /tmp/comments.txt
```

Its `KEEP` regex (line 13) is the definition of an allowed comment: shebangs,
SPDX/copyright/license text, `@generated`/`DO NOT EDIT`, `eslint-disable`,
`@ts-expect-error`, `@ts-ignore`, `type: ignore`, `noqa`, `pragma`,
`swiftlint:`, `prettier-ignore`, `biome-ignore`, `swift-tools-version:`,
`go:build`, `shellcheck`, `/// <reference`, `-*- coding -*-`.

Largest files at `63b62f18` (lines flagged): `scripts/lb-sandbox` 687,
`clients/claude-lb-launch` 315, `clients/route` 252, `scripts/lb-restart` 231,
`clients/seat` 111, `scripts/lb-sandbox-check` 101,
`config/coding-agents/verify-routing` 37, `clients/agent-lb-federation` 31,
`clients/gaming-mode-watch` 26, `clients/fable` 20,
`scripts/lb-spend-cap-check` 19, `config/coding-agents/hooks/railway-vars-guard.sh` 19,
`clients/gaming-adaptive-cap` 19, `clients/muse-lb-launch` 17,
`clients/agent-defs-doctor` 16, `clients/lb-cache` 12. All but the `.sh`
hook are Python scripts without an extension (`#!/usr/bin/env python3`).

Do not sweep (the first sweep left these on purpose; scenarios and fixtures
are protected test data):
- `tests/fixtures/**`
- any `tests/**/test_*_scenarios.py`
- Markdown, `.txt`, JSON, `openspec/**`, `CHANGELOG.md`

Code that reads its own docstring (removing the docstring would break `--help`
or crash on `None.splitlines()`):

```
app/audit_forwarded.py:24,110                      help=__doc__ / description=__doc__
clients/agent-defs-doctor:526                      description=__doc__
clients/lb-cache:422                               description=__doc__
clients/open-factory/evals/of-work/build_tasks.py:189  description=__doc__.splitlines()[0]
clients/open-factory/evals/of-work/prebuild.py:41      (same)
clients/open-factory/evals/of-work/run_arms.py:20      (same)
clients/open-factory/evals/of-work/summarize.py:91     (same)
```

For these, replace `__doc__` with a string literal holding the first line of
the docstring, then remove the docstring. Re-run `git grep -n "__doc__"` in
Step 1; the live list wins.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Worktree | `git -C /Volumes/StudioExt/repos/agent-lb worktree add /Volumes/StudioExt/repos/agent-lb-wt/comment-sweep -b cull/comments origin/main` | exit 0 |
| Env | `sh scripts/worktree-setup.sh` | exit 0 |
| Count | `python3 bin/no-new-comments --repo . 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD 2>&1 >/dev/null \| grep -v -e "^tests/fixtures/" -e "_scenarios.py:" \| wc -l` | `0` at the end |
| Python compile | `for f in $(git diff --name-only origin/main); do head -1 "$f" \| grep -q python && python3 -m py_compile "$f"; done; echo done` | `done`, no errors |
| AST equivalence | see Step 3 | `ok` |
| Shell syntax | `for f in $(git diff --name-only origin/main -- '*.sh'); do bash -n "$f" \|\| echo "BAD $f"; done` | no `BAD` |
| Lint | `uv run --no-sync ruff check . && uv run --no-sync ruff format --check .` | exit 0 |
| Launcher | `CLAUDE_LB_DRY_RUN=1 python3 clients/claude-lb-launch -p hi` | exit 0 |
| Routing | `python3 config/coding-agents/verify-routing --source-only` | exit 0 |
| Restart tool | `python3 scripts/lb-restart --help && python3 scripts/lb-sandbox --help` | both exit 0 |
| Floor and integration | `uv run --no-sync pytest -q -n 4 --timeout=180 --timeout-method=thread tests/floor tests/integration tests/e2e` | same failure set as baseline |
| No new comments | `python3 bin/no-new-comments --repo . origin/main HEAD` | exit 0 |

## Scope

**In scope**: every tracked code file the Count command lists, except the
do-not-sweep set; the `__doc__` call sites above; `.git/hooks/pre-push` in the
main checkout's common git dir (a symlink, not a tracked file).

**Out of scope**: string contents (a `#` inside a string or heredoc that the
program writes out, e.g. a generated plist, shell snippet or Python script
body, is data, not a comment); `tests/fixtures/**`; scenario tests; docs.

## Git workflow

- Branch `cull/comments`; one commit per top-level directory (`clients`, `scripts`, `config`, `app`, other), message `Remove remaining code comments per no-comments rule (<dir>)`.
- Each body has a `check:` line with AST equivalence, Python compile and the smoke results, and a `Seat:` trailer.
- Land: `git push origin HEAD:main`. Then `git -C /Volumes/StudioExt/repos/agent-lb pull --ff-only`, `~/.agent-lb/bin/sync-runtime.sh`, `~/.agent-lb/bin/lb-restart --reason "plan 009: comment sweep"`.

## Steps

### Step 1: Measure

Run Count without `wc -l` and save the list to `/tmp/009-before.txt`. Run
`git grep -n "__doc__"`. Run Floor and integration; save failures to `/tmp/009-fail-before.txt`.

**Verify**: the list exists; note its length.

### Step 2: Remove Python comments and docstrings

For each Python file (by extension or `python` shebang) in the list, remove
`#` comment tokens whose text does not match `KEEP`, and remove docstrings
(the first statement of a module, class or function when it is a string
constant). A body left empty gets `pass`. Do it with a script that uses
`tokenize` (comments) and `ast` (docstring line ranges) and edits by line
ranges; never regex-edit Python. Remove trailing whitespace left behind and
collapse runs of 3+ blank lines to 2 at top level and 1 inside blocks.
Handle the `__doc__` sites first.

**Verify**: Python compile passes.

### Step 3: Prove Python behavior unchanged

For every changed Python file, compare ASTs with docstrings stripped:

```sh
python3 - <<'EOF'
import ast, subprocess
def strip(src):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.dump(tree, include_attributes=False)
files = subprocess.check_output(["git", "diff", "--name-only", "origin/main"], text=True).split()
bad = []
for f in files:
    try:
        new = open(f).read()
    except FileNotFoundError:
        continue
    if not (f.endswith(".py") or new.startswith("#!") and "python" in new.splitlines()[0]):
        continue
    old = subprocess.check_output(["git", "show", f"origin/main:{f}"], text=True)
    if strip(old) != strip(new):
        bad.append(f)
print("ok" if not bad else bad)
EOF
```

Files where you replaced `__doc__` with a literal will differ; check those by
eye and list them in the commit body.

**Verify**: prints `ok` (or only the `__doc__` files).

### Step 4: Remove shell, YAML, TOML, JS/TS, Swift, CSS, SQL comments

For `.sh` and shell-shebang files: delete whole-line `#` comments outside
heredocs that do not match `KEEP`; delete trailing ` # ...` only when the `#`
is outside quotes and outside a heredoc. Skip any line between a `<<WORD` or
`<<'WORD'` opener and its terminator. For the other languages use the same
rules with their comment markers. Keep `shellcheck` directives.

**Verify**: Shell syntax prints no `BAD`; `make frontend-lint frontend-typecheck` exits 0 if any frontend file changed.

### Step 5: Smoke everything that runs live

**Verify**: Lint, Launcher, Routing, Restart tool all pass; Floor and
integration shows no new failures versus `/tmp/009-fail-before.txt`; Count
prints `0`; No new comments exits 0.

### Step 6: Block new comments at push

In the main checkout's common git dir, install the pre-push hook if absent:

```sh
common=$(git -C /Volumes/StudioExt/repos/agent-lb rev-parse --git-common-dir)
test -e "$common/hooks/pre-push" || ln -s ../../bin/no-new-comments-pre-push "$common/hooks/pre-push"
```

`bin/no-new-comments-pre-push` resolves the checker from the pushing worktree's
top level, so one link covers every agent-lb worktree on Studio.

**Verify**: `ls -l "$common/hooks/pre-push"` shows the link. Then, without
pushing anything: on a throwaway local branch commit a scratch file
`probe.py` containing `# prose`, and run
`echo "refs/heads/probe $(git rev-parse HEAD) refs/heads/probe $(git rev-parse origin/main)" | "$common/hooks/pre-push" origin unused`
→ non-zero exit naming `probe.py:1`. Delete the branch and commit afterwards.

### Step 7: Land and restart

**Verify**: push succeeds through the new hook; `lb-restart` completes;
`curl -fsS http://127.0.0.1:2455/health/ready` returns 200.

## Test plan

No new tests. AST equivalence proves Python behavior is unchanged; the smokes
prove the live scripts still start.

## Done criteria

- [ ] Count prints `0`
- [ ] AST equivalence `ok` (except listed `__doc__` files)
- [ ] All smokes pass; no new floor/integration failures
- [ ] pre-push hook link exists in the common git dir
- [ ] `plans/README.md` row 009 updated

## STOP conditions

- AST equivalence fails for a file you did not change on purpose.
- A `#` you are about to delete sits inside a heredoc or string the program emits (for example lines in `scripts/lb-sandbox` that build a Seatbelt profile or a shell script). Leave it and list it.
- A pre-push hook already exists in the common dir and is not `no-new-comments-pre-push`; do not replace it, report it.
- `lb-restart` rolls back.

## Maintenance notes

- Box clones (`pc-wsl`, `ax42`, `forge`) do not get the hook from this step; the factory's `factory-ff` installs it for factory clones only. Seats that push agent-lb from a box should get it the same way (follow-up).
- If `bin/no-new-comments` is ever deleted, keep a copy: the factory repo has the same file at `bin/no-new-comments`.

LOC removed (estimate): ~2,200 comment lines.
