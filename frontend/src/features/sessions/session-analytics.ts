import type { SessionAnalyticsResponse, SessionAnalyticsSeat } from "./schemas";

const MAX_MODEL_SERIES = 4;

export function getSeatLabel(model: string, reasoningEffort: string | null) {
  if (model === "gpt-5.6-sol" && reasoningEffort === "medium") {
    return "Implementer";
  }
  if (model === "gpt-5.6-sol" && reasoningEffort === "xhigh") {
    return "Verifier";
  }
  if (model === "claude-sonnet-5") return "Explore";
  if (model === "claude-fable-5") return "Driver";
  return model;
}

export function getSeatKey(seat: SessionAnalyticsSeat) {
  return `${seat.model}:${seat.reasoningEffort ?? "default"}`;
}

export type TimelineDatum = Record<string, number | string> & {
  bucketStart: string;
};

export type SessionTimeline = {
  data: TimelineDatum[];
  models: string[];
};

export function buildSessionTimeline(
  analytics: SessionAnalyticsResponse,
): SessionTimeline {
  if (analytics.series.length === 0) return { data: [], models: [] };

  const totals = new Map<string, number>();
  for (const bucket of analytics.series) {
    for (const entry of bucket.byModel) {
      totals.set(
        entry.model,
        (totals.get(entry.model) ?? 0) + entry.outputTokens,
      );
    }
  }

  const rankedModels = [...totals.entries()]
    .sort((left, right) => right[1] - left[1])
    .map(([model]) => model);
  const topModels = rankedModels.slice(0, MAX_MODEL_SERIES);
  const hasOther = rankedModels.length > MAX_MODEL_SERIES;
  const models = hasOther ? [...topModels, "other"] : topModels;
  const firstBucket = new Date(analytics.series[0].bucketStart).getTime();
  const lastBucket = new Date(
    analytics.series[analytics.series.length - 1].bucketStart,
  ).getTime();
  const bucketMilliseconds = analytics.bucketSeconds * 1_000;
  const entriesByTime = new Map(
    analytics.series.map((bucket) => [
      new Date(bucket.bucketStart).getTime(),
      bucket.byModel,
    ]),
  );
  const data: TimelineDatum[] = [];

  for (
    let bucketTime = firstBucket;
    bucketTime <= lastBucket;
    bucketTime += bucketMilliseconds
  ) {
    const datum: TimelineDatum = {
      bucketStart: new Date(bucketTime).toISOString(),
    };
    for (const model of models) datum[model] = 0;

    for (const entry of entriesByTime.get(bucketTime) ?? []) {
      const key = topModels.includes(entry.model) ? entry.model : "other";
      if (key === "other" && !hasOther) continue;
      datum[key] = Number(datum[key] ?? 0) + entry.outputTokens;
    }
    data.push(datum);
  }

  return { data, models };
}
