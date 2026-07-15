import { describe, expect, it } from "vitest";

import {
  SessionAnalyticsResponseSchema,
  SessionDetailResponseSchema,
  SessionsResponseSchema,
} from "@/features/sessions/schemas";

const aggregate = {
  sessionId: "session-123",
  provider: "anthropic",
  useragentGroup: "claude-code",
  models: [{ model: "claude-fable-5", requests: 4 }],
  requests: 4,
  inputTokens: 1_000,
  outputTokens: 200,
  cachedInputTokens: 600,
  costUsd: 0.42,
  errors: 1,
  firstSeen: "2026-07-15T10:00:00Z",
  lastSeen: "2026-07-15T11:00:00Z",
};

describe("SessionsResponseSchema", () => {
  it("parses a sessions rollup response", () => {
    const parsed = SessionsResponseSchema.parse({
      sessions: [aggregate],
      total: 1,
    });

    expect(parsed.sessions[0]?.models[0]).toEqual({
      model: "claude-fable-5",
      requests: 4,
    });
    expect(parsed.sessions[0]?.costUsd).toBe(0.42);
    expect(parsed.sessions[0]?.sparkline).toBeUndefined();
  });

  it("parses an optional sparkline", () => {
    const parsed = SessionsResponseSchema.parse({
      sessions: [{ ...aggregate, sparkline: [0, 2, 1] }],
      total: 1,
    });

    expect(parsed.sessions[0]?.sparkline).toEqual([0, 2, 1]);
  });

  it("rejects invalid aggregate counts", () => {
    expect(() =>
      SessionsResponseSchema.parse({
        sessions: [{ ...aggregate, requests: -1 }],
        total: 1,
      }),
    ).toThrow();
  });
});

describe("SessionAnalyticsResponseSchema", () => {
  it("parses analytics series, seats, and histograms", () => {
    const parsed = SessionAnalyticsResponseSchema.parse({
      session: aggregate,
      bucketSeconds: 300,
      series: [
        {
          bucketStart: "2026-07-15T10:00:00Z",
          byModel: [
            {
              model: "claude-fable-5",
              reasoningEffort: null,
              requests: 2,
              outputTokens: 200,
              cachedInputTokens: 100,
              costUsd: 0.2,
            },
          ],
        },
      ],
      seats: [
        {
          model: "claude-fable-5",
          reasoningEffort: null,
          requests: 2,
          inputTokens: 500,
          outputTokens: 200,
          cachedInputTokens: 100,
          costUsd: 0.2,
          errors: 0,
        },
      ],
      latencyHistogram: [{ label: "0-1s", count: 2 }],
      tokensPerRequestHistogram: [{ label: "100-500", count: 2 }],
    });

    expect(parsed.bucketSeconds).toBe(300);
    expect(parsed.seats[0]?.model).toBe("claude-fable-5");
    expect(parsed.latencyHistogram[0]).toEqual({ label: "0-1s", count: 2 });
  });
});

describe("SessionDetailResponseSchema", () => {
  it("parses model breakdown and request-log rows", () => {
    const parsed = SessionDetailResponseSchema.parse({
      session: aggregate,
      byModel: [
        {
          model: "claude-fable-5",
          requests: 4,
          inputTokens: 1_000,
          outputTokens: 200,
          cachedInputTokens: 600,
          costUsd: 0.42,
        },
      ],
      recentRequests: [
        {
          requestedAt: "2026-07-15T11:00:00Z",
          accountId: null,
          sessionId: "session-123",
          requestId: "request-1",
          model: "claude-fable-5",
          status: "ok",
          errorCode: null,
          errorMessage: null,
          tokens: 1_200,
          cachedInputTokens: 600,
          reasoningEffort: null,
          costUsd: 0.42,
          latencyMs: 120,
        },
      ],
    });

    expect(parsed.byModel[0]?.inputTokens).toBe(1_000);
    expect(parsed.recentRequests[0]?.sessionId).toBe("session-123");
    expect(parsed.recentRequests[0]?.requestId).toBe("request-1");
  });
});
