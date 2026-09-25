# Open Factory on Harbor

This directory holds Harbor agents and local images for Open Factory evals. Every model call goes
through agent-lb on the host, so it spends the pooled subscriptions. Background, measurements and the auth
model are in `~/.agent-lb/of/harbor-feasibility.md`.

## Agents (`agents.py`)

`PrebakedClaudeCode` and `PrebakedCodex` subclass Harbor's built-in `ClaudeCode` and `Codex`. They change
two things:

- **No install.** If the CLI is already in the image, the install step returns at once (about 1 s for
  claude and 4 s for codex, against 3-6 min for Harbor's installer). If it is missing, the trial fails fast.
  Set `OF_HARBOR_ALLOW_INSTALL=1` to fall back to Harbor's installer, for example on registry datasets such as
  terminal-bench, whose images do not carry the CLIs.
- **agent-lb by default.** Claude Code gets `ANTHROPIC_BASE_URL=http://host.docker.internal:2455`. Codex
  gets `OPENAI_BASE_URL=…/v1`. The key sent is `OF_HARBOR_KEY`. When that is unset, it is read from
  `OF_HARBOR_KEY_FILE` (default `~/.agent-lb/of/of-harbor.key`), so requests show `key_name=of-harbor`
  in `request_log_query.py`. With neither, a placeholder is sent and requests are unattributed.
  `OF_AGENT_LB_URL` overrides the URL, and an explicit `--ae` always wins.

```bash
export OF_HARBOR_KEY="$(cat ~/.agent-lb/of/of-harbor.key)"   # optional: the file is the default anyway
export PYTHONPATH=/Volumes/StudioExt/repos/agent-lb-worktrees/open-factory/clients/open-factory/harbor
harbor run -p <task-or-dataset> -a agents:PrebakedClaudeCode -m claude-sonnet-5
harbor run -p <task-or-dataset> -a agents:PrebakedCodex -m gpt-6-luna
```

Pass full model ids. With a custom base URL, Harbor passes the model name through unchanged.

## Images (`build-images.sh`)

| Image | Contents |
|---|---|
| `of-work/agents:cc2.1.282-codex0.157.0` (`:latest`) | Ubuntu 24.04, Node 22.23.3, Claude Code 2.1.282 (native binary, checksum-checked against the release manifest), Codex 0.157.0 (npm), git, ripgrep, jq, procps, and a non-root `agent` user |
| `of-work/agent-lb-base:<sha12>` (`:latest`) | `of-work/agents` plus uv 0.12.19, CPython 3.14.5 (uv-managed), and agent-lb at `origin/main` in `/app` with `uv sync --frozen --dev` |
| `of-work/agent-rails-base:<sha12>` (`:latest`) | The same for agent-rails: `uv sync --frozen --extra dev`, `.ci/requirements.txt`, `npm ci --ignore-scripts`, and `PYTHONPATH=/app` |

```bash
./build-images.sh             # agents + agent-lb
./build-images.sh agent-rails # needs GITHUB_TOKEN (private repo, shallow clone)
```

The versions are pinned through env vars. The defaults are the versions Harbor's installers picked on
2026-09-25. The script reads the agent-lb checkout and never modifies it. It does not fetch, so run
`git -C /Volumes/StudioExt/repos/agent-lb fetch origin` first when you need a current `origin/main`.

## Per-task images

```dockerfile
FROM of-work/agent-lb-base:<sha12>
RUN of-checkout <task-base-sha>
```

`of-checkout` does the following, in order:

1. Checks out the task's base commit and cleans the tree. It keeps `.venv` and `node_modules`.
2. Deletes every ref, remote and reflog.
3. Names the pinned commit `main`.
4. Repacks and prunes.
5. Verifies that every commit object left in the repo is the base commit or one of its ancestors.
6. Re-syncs dependencies with the image's `of-sync`.

It fails the build if any other commit survives. A per-task build takes about 5 s on top of the base.

### Why the fix cannot leak through git

- **The source history ends at the base commit.** The base image is built from a scratch bare repo that
  only holds history up to the base commit. It is bind-mounted into the build and never stored in a layer.
  Local branches, stashes and unpushed work never enter the image.
- **Newer commits are deleted per task.** The per-task layer drops all refs and reflogs, then prunes
  unreachable objects. Inside the container, `git log --all`, `git reflog` and `git cat-file <later-sha>`
  can only reach history up to the task's base commit. The base layer's older pack files are hidden by
  overlay whiteouts, and nothing in the container can read image layers.
- **Base images must not be newer than their tasks.** Build the base at or before the oldest task base
  commit you care about, or rebuild it per batch. Always run `of-checkout`, even when the base commit is
  the image's own.
- **Remaining channels are outside git.** The uv cache holds third-party wheels, not project source.
  `docker history` shows the `of-checkout <sha>` build step, but only on the host. Network access is still
  possible: a task on `network_mode = "public"` can clone the repo from GitHub if it has credentials. agent-rails is private and no
  credentials are passed into tasks.
