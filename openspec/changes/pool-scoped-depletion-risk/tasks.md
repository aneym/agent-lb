# Tasks

- [x] Pool-mean aggregate + worst-account attribution in
      `compute_aggregate_depletion` (`app/modules/usage/depletion_service.py`);
      ETA gated on pool danger/critical.
- [x] `DepletionResponse` attribution fields + per-provider maps on
      `DashboardProjectionsResponse`; dashboard service computes both.
- [x] Unit tests (pool mean, one-hot-account, attribution) and route-level
      regression (`tests/integration/test_dashboard_overview.py`).
- [x] OpenSpec delta (`usage-projections`, new capability).
- [x] Validate: pytest, ruff, `openspec validate --specs`, restart service and
      confirm the live payload reconciles with the ~81%-remaining pool.
