# Startup evidence and limits (10/06/2026)

## Config causes found
- External ~/.agent-rails/workflows/codex-lab-home.py:24 wrote network_access=false; lines 27-35 copied provider websocket settings verbatim. Source inspected; no real config/authentication file contents read.
- External ~/.local/bin/cx-bg:44 linked ~/.codex/sessions after home generation, undoing an isolated layout.
- Engine handoff ~/.agent-rails/scoping/rails-rooms/evidence/engine-audit-2026-10-06/HANDOFF-to-p138-factory-fixes.md:11 reports MachPortRendezvousServer bootstrap_check_in 1100 / Chromium SIGTRAP. That sandbox denies macOS bootstrap services; allowing TCP network alone does not grant Mach access. Capture in an authorized parent/outside-sandbox browser process and hand the artifact back; do not remove the default write seat filesystem boundary.
- ENGINE-LEDGER-2026-10-06.md:209-216 quotes WebSocket-to-HTTPS fallback and workspace routing discovery timed out; HTTPS alone is explicitly not proven sufficient. Original referenced run logs were not present at the named local paths, so this is source-reported evidence, not our reproduction.
- The same ledger:277-282 reports 28 minutes under load 240, then sessions-link removal and output after relaunch. No latency isolation or causal timing experiment was supplied. We fix the observed archive link; we do not claim a measured 28-minute improvement.

## Repair
Canonical codex-seat-home.py is installed to ~/.agent-rails/workflows/codex-lab-home.py. It writes workspace-write, network_access=true, provider supports_websockets=false and legacy responses_websockets{,_v2}=false. A current Codex features list in an empty temporary home labels those feature flags removed; provider support is the active transport control, and legacy flags protect older launchers. No automatic apps, MCP, plugins or hooks are copied. Authentication is linked without reading or printing contents. A missing source config has an explicit loopback Agent LB provider, never a direct OpenAI default.

Only an old sessions symlink is removed; the archive is not traversed or deleted. Existing job-local rollouts remain for resume. Global archive aggregators must explicitly collect job homes; no implicit shared sessions archive. Apply cx-bg.patch so that launcher does not undo the layout. Full-home opt-outs remain opt-outs; we do not rewrite desktop/global Codex configuration or the routing guard.

Test authoring gate: a real generator subprocess, fixture source config, real filesystem, TOML assertions, archive integrity and local-resume preservation. A real isolated installer installs the generator; a redacted actual cx-bg fragment is patched and run against a scratch HOME, verifying it does not re-link sessions. No credentials, remote Codex turn or test-only production seam.

Before: dry-launch failed because network_access was false (/tmp/seat-p4-home-before.txt). After: dry generator/installed-launcher and installer checks pass in /tmp/seat-p4-final-integration.txt. py_compile passes. Chromium capture and live transport/stall behavior remain unverified. No live files changed; no service restarted. Cross-vendor review not run (agent messaging forbidden).

## Deployment after merge (not executed)
From the merged main checkout on each seat-launching machine:

```sh
python3 config/coding-agents/install-policy.py
patch --fuzz=0 --forward "$HOME/.local/bin/cx-bg" < patches/codex-seat/cx-bg.patch
```

Installer copies clients/route to ~/.agent-lb/bin/route; converges routing-table.json and seat definitions; copies codex-seat-home.py to ~/.agent-rails/workflows/codex-lab-home.py. cx-bg.patch targets the observed launcher; check it before applying and do not force a drifted hunk. No app runtime file changes: lb-restart is NOT needed. New/relaunched seats use the new definitions/home; already running seats retain their settings. Calls using the managed home writer inherit the fix; other independently managed launchers remain outside scope.
