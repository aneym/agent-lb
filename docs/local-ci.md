# Local CI

All merge CI runs on the coordinator's machine, in an isolated worktree.
See the normative [local CI spec](../openspec/specs/local-ci/spec.md). Run
the complete tier for a pull request, commit SHA, or `main`:

```bash
python3 scripts/local_ci.py run <PR|sha|main>
```

This is a full suite, not a changed-file selector. It includes frontend lint,
types, and tests; Python lint/types; SQLite unit, integration, and end-to-end
tests; disposable PostgreSQL parity and migration checks; package validation;
Docker vulnerability gating; Helm/kind validation; and native Swift tests and
release build. Missing or failed tools make the run red; they are never skipped.

The runner writes a new same-machine receipt per attempt under
`~/agent-lb-ci/receipts/<sha>/<runid>/receipt.json`, with hashed command logs
under `~/agent-lb-ci/runs/<sha>/<runid>/logs`. Before merging, verify the exact
40-character candidate SHA:

```bash
python3 scripts/local_ci.py status <40sha>
python3 scripts/local_ci.py show <40sha>
python3 scripts/local_ci.py doctor
```

`status` succeeds only when the newest receipt for that exact SHA is complete,
has matching log hashes, and every required tier passed. A receipt from another
machine is not merge authority.

Windows native startup smoke is a separate Windows tier because macOS cannot
prove it. Run it on Windows when that surface changes:

```powershell
uv sync --dev --frozen
uv run pytest tests/unit/test_memory_monitor.py
uv run python -c "from app.main import create_app; assert create_app() is not None"
```

## Local tools and evidence

Install Python 3.13 (through uv), Bun, Xcode/Swift, Docker, Helm, kind,
kubectl, kubeconform and Trivy. `doctor` reports which are missing. Studio's
verified standalone Helm/kind/kubeconform/Trivy binaries can live under
`~/agent-lb-ci/tools`; the runner adds that directory to its PATH.

`make ci REF=HEAD` and the manual pre-commit `local-ci` hook run committed
HEAD, not staged or uncommitted edits. Commit the candidate first. The runner
never stages, stashes or resets the caller's work. Individual Make targets are
useful during editing but do not issue a merge receipt.

After merging, run `python3 scripts/local_ci.py run main` and check the exact
merged SHA. A red baseline is a red receipt: do not re-label it green because
the failing files were unchanged. SQLite, PostgreSQL, native macOS fixture,
Windows-native, and live-provider evidence are separate claims.

GitHub remains the source/PR/release host. Release, publication, attribution
labels and stale-issue automation are not local CI and are not removed. No
local CI command publishes a package, changes branch protection or merges a PR.
