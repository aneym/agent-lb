## 1. Measurement Foundation

- [x] 1.1 Add tests for monotonic startup phase recording, structured summaries, bounded Prometheus labels, and no-Prometheus fallback
- [x] 1.2 Implement the service startup recorder and instrument the readiness-critical lifespan phases
- [x] 1.3 Add a repeatable command/service benchmark tool with JSONL history, median/p95 aggregation, useful-output markers, and regression comparison
- [x] 1.4 Add focused benchmark-tool tests and a discoverable Makefile target/help entry

## 2. Service Startup Optimization

- [x] 2.1 Add tests proving migration-head validation remains fail closed while full startup schema drift is explicit
- [x] 2.2 Add the startup drift setting, default to the fast migration-head path, and preserve comprehensive `agent-lb-db check`
- [x] 2.3 Record before/after service startup/startup-probe/readiness samples against isolated data and the live LaunchAgent

## 3. Launcher Startup Optimization

- [x] 3.1 Add tests proving interactive readiness selection, headless session claiming, ccdex capability plus routable-pool validation, and remote-first/local-fallback ordering
- [x] 3.2 Defer interactive account selection to the first proxied message, collapse redundant headless probes, reject empty ccdex candidates, retain the measured-faster portable proxy subprocess, and preserve connectable-before-exec proof
- 3.3 (pending rollout) Exercise `cc`, `ccdex`, and `co` dry-run or real paths and record before/after useful-output/proxy-ready samples

## 4. Installer Restart Optimization

- [x] 4.1 Add installer contract tests for readiness probing, removal of unconditional cooldown, bounded retry cadence, and timing output
- [x] 4.2 Replace fixed cooldowns with state-driven port/bootstrap polling, require `/health/ready`, and print phase timings
- [x] 4.3 Reinstall/restart the live service and verify the measured readiness result at `http://127.0.0.1:2455`

## 5. Verification and Rollout

- [x] 5.1 Run focused tests, launcher byte-compilation, Ruff, strict OpenSpec validation, and the benchmark regression gate
- 5.2 (pending rollout) Sync the delta specs and stable startup-performance operational context, verify the change, and archive it
- 5.3 (pending rollout) Commit and push validated changes to `main`, fast-forward the MacBook checkout, and rerun remote health plus `co`
- 5.4 (pending closeout) Report exact baseline/improved timings, artifacts, unmeasured Docker/Helm scope, and any remaining remote Claude login requirement
