# Codex companion broker patch

## Base and ownership

Patch base: `openai/codex-plugin-cc` commit
`db52e28f4d9ded852ab3942cea316258ae4ef346` (installed version inspected
2026-09-22). Only agent-lb owns these patch artifacts; no upstream commit,
PR, issue, or installation-repository git history is changed.

Repository-wide searches for `codex-plugin-cc`, `claude plugin install`, and
`claude plugin update` found no agent-lb-owned installer/updater. Claude's
marketplace/update mechanism is external to agent-lb. This script is an
explicit post-update step, not an automatically executed hook.

## Apply after every update or reinstall

From the agent-lb checkout:

```sh
scripts/apply-codex-plugin-cc-patch.sh
```

The default target is `$HOME/.agent-lb/plugins/codex-plugin-cc`. To apply to a
test installation:

```sh
CODEX_PLUGIN_CC_DIR=/path/to/plugin scripts/apply-codex-plugin-cc-patch.sh
```

It first checks the entire patch, applies without `--reject` or `--3way`,
and reports already-applied state. An incompatible update fails nonzero with
git's file/hunk diagnostics; inspect and regenerate the patch rather than
forcing it. Do not run application concurrently with a plugin update.

## Runtime

`CODEX_COMPANION_BROKER_IDLE_TIMEOUT_MS` defaults to `600000` (ten minutes).
It accepts positive integral milliseconds up to `2147483647`. For example,
`CODEX_COMPANION_BROKER_IDLE_TIMEOUT_MS=3000` gives a three-second idle window.
The environment setting is read by the broker; existing CLI arguments and
line-delimited JSON-RPC, including `broker/shutdown`, are unchanged.

The timer starts when the broker listens and when the final socket closes.
Every connection cancels it. Shutdown stops new connections, flushes/closes
existing sockets, reaps the app-server's process tree, and removes the
socket/pid file. Before signalling, the broker walks the app-server's
descendants with `ps -axo pid=,ppid=,pgid=,stat=,lstart=` until a pass finds
nothing new, recording each process as (pid, start time, pgid). Before every
signal (the TERM, each liveness poll, the final KILL) it re-reads ps and acts
only on processes whose pid, start time and pgid all still match the record,
so a reused pid or group id is skipped. A group is signalled only when every
current member matches a recorded process; otherwise matching members are
signalled one by one and unrecorded members of that group are left alone.
KILL follows after three seconds, including for members re-parented to init.
If ps is unavailable, only the app-server child is signalled through Node's
child handle, which the kernel keeps bound to that process until Node reaps
it; no group is signalled. Ordinary direct app-server clients and Windows
tree cleanup are unchanged.

Codex 0.155.1 starts each MCP server in its own process group (observed
2026-09-22), so signalling the app-server group alone left MCP descendants
running. The real-Codex test asserts an MCP fixture sits outside the
app-server group and that every recorded descendant is gone after exit. A
descendant that forks after the walk and leaves every recorded group is not
reaped, and neither is an unrecorded process that shares a recorded group.
Real pid or group reuse cannot be forced in a test; case I simulates it with
a ps stub that reports a new start time for a recorded pid.

Applying the patch affects newly launched brokers only. It cannot repair
already running Node processes. Do not kill existing brokers as part of this
procedure; their lifecycle belongs to their sessions.

## Verification commands

```sh
# Test A: real broker/Codex, no client; Test B: real socket/initialize,
# connected for >2 idle windows, another client, disconnect/reconnect,
# real Codex thread/start with an isolated local MCP and child.
# Also tests real stand-in trees: resistant leader, exited leader,
# explicit shutdown RPC, and SIGTERM. No inference requests or credentials.
node tests/integration/codex_broker_idle_reap.mjs

# Explicit fallback if Codex is unavailable: real processes and sockets,
# protocol stand-in in place of Codex. NOT full Codex/MCP proof.
node tests/integration/codex_broker_idle_reap.mjs --standin-only

# File application, atomic drift failure, idempotence, patched JS syntax,
# invalid timer validation. Does NOT exercise broker runtime shutdown.
node tests/integration/codex_broker_idle_reap.mjs --delivery-only

sh -n scripts/apply-codex-plugin-cc-patch.sh
```

The integration test archives the installed plugin's committed HEAD into a
fresh `/private/tmp/broker-reap-*` directory; it never patches the installed
copy. `CODEX_TEST_BINARY` may select a real standalone Codex binary. The
default is `~/.codex/packages/standalone/current/bin/codex`. It uses an empty
HOME/CODEX_HOME and a locally implemented MCP protocol server, not the user's
MCP configuration. It inventories PIDs before spawning, signals only its own
new groups, and prints `ps` rows before/after plus the evidence directory.
The test needs permission to run `ps`, create Unix sockets, and signal its
own child processes. It fails before spawning if inventory is prohibited.

## 2026-09-22 execution evidence and blockers

- Delivery-only test: PASS (exit 0), including syntax checks of both patched
  modules, idempotence, atomic incompatible-hunk rejection, and six invalid
  idle settings. Log: `/private/tmp/codex-broker-delivery-tests.log`.
- Full test command: PASS on ac862487, all nine runtime cases (A real
  zero-client, B real connected, C-I stand-in trees, identity and pid-reuse
  checks) plus the delivery checks, with real PID evidence. An earlier sandboxed
  run was blocked at `spawnSync ps EPERM`; the unsandboxed run superseded it.
  Evidence: `checks/idle-reap-integration-identity-2026-09-22.txt`.
- Live apply script: BLOCKED (exit 1): git reported `Operation not permitted`
  unlinking/writing the installed plugin files. Subsequent git status/diff
  confirmed the installed checkout remains clean, unpatched at the base SHA.
  No pre-existing broker/app-server/MCP process was signalled.
- OpenSpec change strict validation: PASS. The change is intentionally left
  active, not archived, because runtime verification and live application
  remain incomplete.
- Merge condition met: Tests A/B pass with real PID evidence. The one open
  reviewer finding (the ps-to-kill gap) is accepted; see Residual risk in
  design.md.
- Global strict spec validation: FAIL (36 passed, 2 failed). Untouched
  `anthropic-messages-compat` and `oauth-refresh-safety` specs have requirements
  missing scenarios. Full output: `/private/tmp/codex-broker-spec-validation.log`.
