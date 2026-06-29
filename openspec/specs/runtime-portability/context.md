# Runtime Portability Context

See `openspec/specs/runtime-portability/spec.md` for normative requirements.

## Codex Session Retagging

`codex resume` filters sessions by `model_provider`. Sessions created before
switching to agent-lb may still be tagged as `openai`, so they will not appear
until the stored provider tag is updated.

Use the built-in command instead of editing Codex files by hand:

```bash
# Preview what will change first.
agent-lb codex-sessions retag --from openai --to agent-lb --dry-run

# Then close Codex/Codex CLI and apply the retag.
agent-lb codex-sessions retag --from openai --to agent-lb --yes
```

The command updates both Codex storage formats when they exist: JSONL session
files under `~/.codex/sessions` and `state_*.sqlite` thread rows created by
newer Codex CLI versions. It uses Python's built-in SQLite support, creates a
backup under `~/.codex/backups/provider-retag/`, and refuses non-interactive
writes unless `--yes` is provided.

On native Windows, macOS, Linux, and WSL, use `--codex-home PATH` if your Codex
data directory is not detected. In WSL, autodetect only considers the current
Windows `USERPROFILE`; pass `--codex-home /mnt/c/Users/<name>/.codex` to retag
another Windows profile explicitly.

To switch back, reverse the providers:

```bash
agent-lb codex-sessions retag --from agent-lb --to openai --dry-run
agent-lb codex-sessions retag --from agent-lb --to openai --yes
```

For Docker, mount your Codex data directory only for this one-off command:

```bash
docker run --rm \
  -v ~/.codex:/codex-home \
  ghcr.io/aneym/agent-lb:1.20.0-beta.3 \
  agent-lb codex-sessions retag --from openai --to agent-lb \
    --codex-home /codex-home --dry-run

docker run --rm \
  -v ~/.codex:/codex-home \
  ghcr.io/aneym/agent-lb:1.20.0-beta.3 \
  agent-lb codex-sessions retag --from openai --to agent-lb \
    --codex-home /codex-home --yes
```
