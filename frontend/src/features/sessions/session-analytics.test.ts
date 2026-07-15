import { describe, expect, it } from "vitest";

import {
  buildSessionTimeline,
  getSeatLabel,
} from "@/features/sessions/session-analytics";
import type { SessionAnalyticsResponse } from "@/features/sessions/schemas";

const session = {
  sessionId: "session-1",
  provider: "anthropic",
  useragentGroup: "claude-code",
  models: [],
  requests: 1,
  inputTokens: 10,
  outputTokens: 10,
  cachedInputTokens: 0,
  costUsd: 0.1,
  errors: 0,
  firstSeen: "2026-07-15T10:00:00Z",
  lastSeen: "2026-07-15T10:10:00Z",
};

describe("getSeatLabel", () => {
  it.each([
    ["gpt-5.6-sol", "medium", "Implementer"],
    ["gpt-5.6-sol", "xhigh", "Verifier"],
    ["claude-sonnet-5", null, "Explore"],
    ["claude-fable-5", null, "Driver"],
    ["custom-model", "high", "custom-model"],
  ])("maps %s and %s to %s", (model, effort, expected) => {
    expect(getSeatLabel(model, effort)).toBe(expected);
  });
});

describe("buildSessionTimeline", () => {
  it("fills gaps and folds models after the top four into other", () => {
    const analytics: SessionAnalyticsResponse = {
      session,
      bucketSeconds: 300,
      series: [
        {
          bucketStart: "2026-07-15T10:00:00Z",
          byModel: [
            ...[100, 80, 60, 40, 20].map((outputTokens, index) => ({
              model: `model-${index + 1}`,
              reasoningEffort: null,
              requests: 1,
              outputTokens,
              cachedInputTokens: 0,
              costUsd: 0,
            })),
          ],
        },
        {
          bucketStart: "2026-07-15T10:10:00Z",
          byModel: [],
        },
      ],
      seats: [],
      latencyHistogram: [],
      tokensPerRequestHistogram: [],
    };

    const timeline = buildSessionTimeline(analytics);

    expect(timeline.models).toEqual([
      "model-1",
      "model-2",
      "model-3",
      "model-4",
      "other",
    ]);
    expect(timeline.data).toHaveLength(3);
    expect(timeline.data[0]?.other).toBe(20);
    expect(timeline.data[1]).toMatchObject({
      "model-1": 0,
      other: 0,
      bucketStart: "2026-07-15T10:05:00.000Z",
    });
  });
});
