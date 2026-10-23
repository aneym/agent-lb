import { HttpResponse, http } from "msw";

const emptyPlanLevel = {
  title: "Fixture",
  why: "Synthetic test data",
  accounts: [],
  ladders: {},
  risk: "None",
};
const receiptTotals = {
  requests: 0,
  errors: 0,
  errorRate: 0,
  inputTokens: 0,
  outputTokens: 0,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
  tokens: 0,
  cacheReadRatio: null,
  costUsd: 0,
  p50LatencyMs: null,
  p95LatencyMs: null,
  topErrorCode: null,
};

export const cockpitHandlers = [
  http.get("/api/pools", () =>
    HttpResponse.json({ generatedAt: "2026-10-22T00:00:00Z", pools: [] }),
  ),
  http.get("/api/pools/seat-accounts", () => HttpResponse.json({ accounts: [] })),
  http.get("/api/pools/plan", () =>
    HttpResponse.json({
      generatedAt: "2026-10-22T00:00:00Z",
      recommended: "balanced",
      levels: { budget: emptyPlanLevel, balanced: emptyPlanLevel, unlimited: emptyPlanLevel },
    }),
  ),
  http.get("/api/receipts/summary", () =>
    HttpResponse.json({ totals: receiptTotals, groups: [], series: [] }),
  ),
  http.get("/api/receipts", () =>
    HttpResponse.json({ receipts: [], total: 0, limit: 50, offset: 0 }),
  ),
  http.get("/api/settings/runtime/connect-address", () =>
    HttpResponse.json({ connectAddress: "http://127.0.0.1:2455" }),
  ),
  http.get("/api/team/members", () => HttpResponse.json([])),
];
