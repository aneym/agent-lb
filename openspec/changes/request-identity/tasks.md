# Tasks — request-identity

- [x] U0. OpenSpec proposal, request-log/usage and dashboard spec deltas,
      and this unit checklist. Check: `openspec validate request-identity --strict`.
- [ ] U1. Add four nullable columns to the model and an idempotent batch
      migration chained after the current single head; do not backfill or add
      an unproven index. Check upgrade/downgrade on disposable SQLite and
      PostgreSQL, old rows null, and `alembic heads` with one head.
- [ ] U2. Implement shared-client-IP machine/person resolver, flat env config,
      request context middleware and explicit identity on `add_log`; cover
      proxy, streaming and Anthropic writers. Check table-driven tests for
      member, owner machine, keyless non-owner, forged/multiple XFF, IPv6,
      funnel, remote, valid/invalid claim, and HTTP/Anthropic logging.
- [ ] U3. Capture WebSocket identity per turn, propagate bridge identity only
      over authenticated forward context, and keep no-request warmups internal,
      including proxy/_service/warmup.py (and tasks spawned inside requests).
      Check two-turn WebSocket, bridge forward/reuse/cancellation/plain-client
      rejection, and quota/limit warmup tests.
- [ ] U4. Refresh tailnet node handles on a background timer with timeout,
      preserving the last cache on failure and excluding User/login data.
      Check fixture parsing, miss/failure handling, warning rate limit and
      absence of subprocess work on the request path.
- [ ] U5. Expose four fields in request-log API/dashboard; add a bounded
      user/machine summary on the existing authenticated usage API with existing
      exclusions and soft-delete rules. Check API/auth, legacy nulls, totals,
      dashboard contract and real-stack keyless/Serve-shaped/member requests.
- [ ] U6. After verified rollout, exercise local and tailnet clients and a
      validated member key; confirm tagged devices supply a client IP and
      persisted user/machine/source values. No live DB operations in earlier
      units.

For each unit: three-lens xhigh review (correctness, public-repo privacy
scan, request-path latency/failure safety); any failed lens blocks rollout.
Federated usage and factory job records are separate follow-ups.
