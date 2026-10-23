# Configuration and verification

Set `AGENT_LB_MODEL_ALIASES='{"work":"claude-sonnet-4-5","fast":"gpt-5.4"}'` and `AGENT_LB_EXCLUDED_MODEL_PATTERNS='["claude-opus-*"]'` before starting the service. Settings use JSON environment values and apply on restart. Resolution uses one lookup; targets name actual models rather than other aliases. Exclusions apply to both the requested name and target. API key permissions continue to apply to the target.

The dashboard settings API persists a separate database settings model; adding fields there requires a second configuration authority and is deferred. No dashboard UI changes are included.

Run `.venv/bin/python scripts/model_aliases_probe.py` from the worktree. It starts the real application on a free loopback port, uses `.local/alias-proof/store.db`, seeds disposable accounts using the benchmark harness, and restricts child-process egress to its fake upstream. It never contacts the live port. The probe replaces only its own database on each run and stops its child process on exit.

Evidence: `.local/alias-proof/evidence.json` contains discovery, rejection, provider-routing and SQLite log results. `.local/alias-proof/server.log` contains server diagnostics. The probe prints the same JSON to stdout. Four completed inference requests reached the target provider/model; nine excluded requests returned HTTP 400 with no upstream calls. Aliased rows retained `work` and `fast`; direct rows retained null aliases. Model detail lookups succeeded for aliases and ordinary models, while excluded lookups returned 404.
