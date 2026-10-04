# Tasks

- [x] Add `POST /api/federation/request-logs` with mirror auth, a 500-row cap, and idempotent insert (`source = edge:<instance_id>`, null edge key ids, unknown accounts nulled).
- [x] Forward edge `request_logs` from the mirror loop in id order, cursor file written only after 2xx, 48-hour start, own error handling.
- [x] Exclude `edge:%` from `list_local_usage_rollups` and from routing, API-key limit, member-cap, and quota-planner readers.
- [x] Tests for ingest, forward cursor/5xx, and rollup exclusion.
