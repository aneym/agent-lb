# Open Factory on Harbor

This directory holds Harbor agents and local images for Open Factory evals. Every model call goes
through agent-lb on the host, so it spends the pooled subscriptions. Background, measurements and the auth
model are in `~/.agent-lb/of/harbor-feasibility.md`.

## Agents (`agents.py`)

`PrebakedClaudeCode` and `PrebakedCodex` subclass Harbor's built-in `ClaudeCode` and `Codex`. They change
four things:

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

- **Egress pinned to agent-lb.** On a task with `network_mode = "allowlist"`, the agent phase starts by
  narrowing the egress sidecar's allowlist to agent-lb's `host:port`. See [Egress](#egress-no-way-to-the-public-repo).
  Set `OF_HARBOR_REQUIRE_ALLOWLIST=1` to fail any trial whose task still has open egress.
- **No web tools.** Claude Code runs with `--disallowedTools WebSearch,WebFetch`, and Codex runs with
  `web_search=disabled`. Their server-side search would otherwise reach the internet around the allowlist.
  `--ak disallowed_tools=...` or `--ak web_search=...` overrides this.

Pass full model ids. With a custom base URL, Harbor passes the model name through unchanged.

## Images (`build-images.sh`)

| Image | Contents |
|---|---|
| `of-work/agents:cc2.1.282-codex0.157.0` (`:latest`) | Ubuntu 24.04, Node 22.23.3, Claude Code 2.1.282 (native binary, checksum-checked against the release manifest), Codex 0.157.0 (npm), git, ripgrep, jq, procps, and a non-root `agent` user |
| `of-work/agent-lb-base:<sha12>` (`:latest`) | `of-work/agents` plus uv 0.12.19, CPython 3.14.5 (uv-managed), and agent-lb at `AGENT_LB_REF` in `/app` with `uv sync --frozen --dev` |
| `of-work/agent-rails-base:<sha12>` (`:latest`) | The same for agent-rails (shallow, 300 commits deep): `uv sync --frozen --extra dev`, `.ci/requirements.txt`, `npm ci --ignore-scripts`, and `PYTHONPATH=/app` (4.7 GB) |

```bash
./build-images.sh             # agents + agent-lb at origin/main
./build-images.sh agent-rails # needs GITHUB_TOKEN (private repo, shallow clone of main)
# For a dataset, build at the newest task base_sha of each repo (see the rule below):
AGENT_LB_REF=<newest agent-lb base_sha> ./build-images.sh agent-lb
AGENT_RAILS_BASE=<newest agent-rails base_sha> ./build-images.sh agent-rails
```

The versions are pinned through env vars. The defaults are the versions Harbor's installers picked on
2026-09-25. The script reads the agent-lb checkout and never modifies it. It does not fetch, so run
`git -C /Volumes/StudioExt/repos/agent-lb fetch origin` first when you need a current `origin/main`.

## Per-task images

```dockerfile
FROM of-work/agent-lb-base:<sha12>
RUN of-checkout <task-base-sha>
```

```toml
# task.toml
[environment]
network_mode = "allowlist"
allowed_hosts = ["host.docker.internal"]   # the agents narrow this to host.docker.internal:2455
```

`of-checkout` does the following, in order:

1. Checks out the task's base commit and cleans the tree. It keeps `.venv` and `node_modules`.
2. Deletes every ref, remote and reflog.
3. Names the pinned commit `main`.
4. Repacks and prunes.
5. Verifies that every commit object left in the repo is the base commit or one of its ancestors.
6. Re-syncs dependencies with the image's `of-sync`.

It fails the build if any other commit survives. A per-task build takes about 5 s for agent-lb and 10-20 s
for agent-rails. agent-rails's `of-sync` reruns `npm ci` only when `package-lock.json` differs from the one
installed in the base; a full reinstall takes about 11 min and adds 2.6 GB to the layer.

### Running the repo's tests inside

Run the suites with the repo's CI timeouts and a few workers, for example
`uv run --frozen pytest -q -n 4 --timeout=180 --timeout-method=thread tests/unit`. agent-lb's unit suite
takes about 4 min this way. Run serially without `--timeout`, it hangs on some async tests. Neither image
matches CI exactly: there is no `gh`, no system `python3` on PATH, and no agent-lb frontend build (bun).
So some tests fail in the image whatever the commit. Grade tasks on their own fail-to-pass and
pass-to-pass lists, measured in the task image, and not on the whole suite being green.

### Why the fix cannot leak through git

- **The source history ends at the base commit.** The base image is built from a scratch bare repo that
  only holds history up to the base commit. It is bind-mounted into the build and never stored in a layer.
  Local branches, stashes and unpushed work never enter the image.
- **Newer commits are deleted per task.** The per-task layer drops all refs and reflogs, then prunes
  unreachable objects. Inside the container, `git log --all`, `git reflog` and `git cat-file <later-sha>`
  can only reach history up to the task's base commit. The base layer's older pack files are hidden by
  overlay whiteouts, and nothing in the container can read image layers.
- **The base must contain every task commit.** Build it at or after the newest task base commit, since
  `of-checkout` can only check out history the base holds. Newer objects stay in the base's lower layers,
  where only the host can see them (for example, through `docker save`). Always run `of-checkout`, even
  when the base commit is the image's own.
- **Remaining channels are outside git.** The uv cache holds third-party wheels, not project source.
  `docker history` shows the `of-checkout <sha>` build step, but only on the host. aneym/agent-lb and
  Soju06/codex-lb are public on GitHub, so the network is the real channel. The next section covers it.

## Egress: no way to the public repo

Harbor 0.23's docker environment has a native egress allowlist. When a task sets
`network_mode = "allowlist"`, Harbor starts a sidecar, `harbor-docker-egress-control-sidecar` (a gost
transparent proxy plus nftables), and runs the task container in the sidecar's network namespace. The
agent has no `NET_ADMIN` there, so it cannot change the rules. All TCP goes through gost, which checks
the destination against the allowlist by IP and by sniffed SNI/Host. All other traffic is rejected,
except DNS to the configured resolver.

Harbor's `allowed_hosts` accepts hostnames only, not ports. `host.docker.internal` alone would expose
every service listening on the host: dozens of ports on the Studio, including the Aside browser daemon on
1455. The sidecar itself accepts `host:port`, so `pin_egress_to_agent_lb` in `agents.py` runs
`network-policy allow host.docker.internal:2455` in the sidecar when the agent phase starts.

The proof is the `egress-probe` task in `~/.agent-lb/of/harbor-smoke/` (2026-09-25):

| Probe | Open egress | Allowlist (oracle) | Allowlist + pin (CC and Codex agents) |
|---|---|---|---|
| agent-lb `host.docker.internal:2455/health` | 200 | 200 | 200 |
| github.com/aneym/agent-lb, Soju06/codex-lb, raw.githubusercontent, codeload, api.github | 200 | blocked | blocked |
| `git ls-remote https://github.com/aneym/agent-lb` | open | blocked | blocked |
| pypi.org, example.com, a GitHub IP directly | 200/301 | blocked | blocked |
| Spoofing an allowlisted name (Host header, or SNI pointed at a GitHub IP) | 200/301 | blocked | blocked |
| Another host port (`host.docker.internal:2457`) | 200 | 200 | blocked |

Recipe for a task:
1. Put the `[environment]` block from [Per-task images](#per-task-images) in its `task.toml`.
2. Run it with `-a agents:PrebakedClaudeCode` or `-a agents:PrebakedCodex`.

You need no `--allow-agent-host` flag. That flag cannot carry a port, and the agents apply the pin
themselves. `~/.agent-lb/of/harbor-smoke/hello-file-egress` is the minimal proof. Its verifier checks,
inside the trial's own container, that `curl -m5 https://github.com` and
`git ls-remote https://github.com/aneym/agent-lb` both fail (TLS reset by the sidecar) while
agent-lb's `/health` returns 200. With both agents it scored reward 1.0, and every request was keyed
`of-harbor`.

Model traffic still works under the pin. Both agent trials scored reward 1.0, and every request showed
up with `key_name=of-harbor`.

Channels that remain:
- **DNS queries to the resolver.** A covert channel, not a way to read the repo.
- **agent-lb itself.** It serves model traffic only, and the web tools are off by default.
- **The verifier phase.** It usually inherits the pinned policy, so test commands must work offline.
  The images pre-sync all dependencies.

`scan_trials.py` is the second line of defense. It reads each trial's `agent/trajectory.json` and flags
agent tool calls that name the upstream repos or GitHub content hosts, that run
`git fetch/clone/pull/ls-remote/remote add` or `gh repo|api|pr|search`, or that use a web tool. It exits 1
when any trial is flagged:

```bash
python3 scan_trials.py jobs/<job> [--json]
```
