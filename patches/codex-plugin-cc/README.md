# codex-plugin-cc patches

`scripts/apply-codex-plugin-cc-patch.sh` applies every `*.patch` here to the installed plugin after each plugin update.

`worktree-roots.patch` (2026-10-06): a write-mode task (`--write`, sandbox `workspace-write`) whose cwd is a linked git worktree adds the worktree's git dir (`<main>/.git/worktrees/<name>`) and the common dir (`<main>/.git`) to `config.sandbox_workspace_write.writable_roots` on `thread/start` and `thread/resume`. Without them, `git fetch` fails on FETCH_HEAD and `git commit` on the index lock, because both dirs sit outside the workspace ("Operation not permitted" on macOS, "Read-only file system" on Linux). A worktree is detected when `git rev-parse --git-dir` differs from `--git-common-dir`; normal checkouts, non-repos and read-only runs send nothing extra.

Codex replaces an array named in a `config` override rather than extending it, so the patch first asks the app-server for the effective roots (`config/read` with the task's cwd, which covers `~/.codex/config.toml` and project layers; the plugin starts `codex app-server` with no profile or `-c` flags) and sends those roots followed by the two git dirs, deduped. If `config/read` fails it sends nothing, which keeps the user's roots and leaves git writes blocked.

The grant is the main checkout's whole `.git`, so a write seat in a worktree can also write the main checkout's refs, config and hooks. Narrowing it to `objects`, `refs` and `logs` was tried and dropped: on a cloned repo, `git fetch --prune` must rewrite `<main>/.git/packed-refs` through `packed-refs.lock` in the common dir itself and fails with "Read-only file system". Network access is unchanged, so fetching from a remote URL still depends on the sandbox's network setting.
