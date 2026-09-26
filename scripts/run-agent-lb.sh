#!/bin/bash
# Canonical source for ~/.agent-lb/bin/run-agent-lb.sh, the program the main
# launchd job runs. Start the approved internal runtime. Deployment is a
# separate coordinated step: run the configured sync-runtime.sh interactively,
# verify candidate hashes, then use the normal launchctl kickstart during an
# approved service window. AGENT_LB_RUNTIME_DIR overrides the runtime checkout.
set -e
cd "${AGENT_LB_RUNTIME_DIR:-$HOME/.agent-lb/runtime/agent-lb}"
exec .venv/bin/agent-lb "$@"
