import { z } from "zod";

import { RequestLogSchema } from "@/features/dashboard/schemas";

export const SessionModelSummarySchema = z.object({
  model: z.string(),
  requests: z.number().int().nonnegative(),
});

export const SessionAggregateSchema = z.object({
  sessionId: z.string(),
  provider: z.string(),
  useragentGroup: z.string().nullable(),
  models: z.array(SessionModelSummarySchema),
  requests: z.number().int().nonnegative(),
  inputTokens: z.number().nonnegative(),
  outputTokens: z.number().nonnegative(),
  cachedInputTokens: z.number().nonnegative(),
  costUsd: z.number(),
  errors: z.number().int().nonnegative(),
  firstSeen: z.iso.datetime({ offset: true }),
  lastSeen: z.iso.datetime({ offset: true }),
  sparkline: z.array(z.number().nonnegative()).optional(),
});

export const SessionModelBreakdownSchema = z.object({
  model: z.string(),
  requests: z.number().int().nonnegative(),
  inputTokens: z.number().nonnegative(),
  outputTokens: z.number().nonnegative(),
  cachedInputTokens: z.number().nonnegative(),
  costUsd: z.number().nullable(),
});

export const SessionsResponseSchema = z.object({
  sessions: z.array(SessionAggregateSchema),
  total: z.number().int().nonnegative(),
});

export const SessionDetailResponseSchema = z.object({
  session: SessionAggregateSchema,
  byModel: z.array(SessionModelBreakdownSchema),
  recentRequests: z.array(RequestLogSchema),
});

export const SessionAnalyticsSeriesEntrySchema = z.object({
  model: z.string(),
  reasoningEffort: z.string().nullable(),
  requests: z.number().int().nonnegative(),
  outputTokens: z.number().nonnegative(),
  cachedInputTokens: z.number().nonnegative(),
  costUsd: z.number(),
});

export const SessionAnalyticsSeatSchema = z.object({
  model: z.string(),
  reasoningEffort: z.string().nullable(),
  requests: z.number().int().nonnegative(),
  inputTokens: z.number().nonnegative(),
  outputTokens: z.number().nonnegative(),
  cachedInputTokens: z.number().nonnegative(),
  costUsd: z.number(),
  errors: z.number().int().nonnegative(),
});

export const SessionAnalyticsHistogramEntrySchema = z.object({
  label: z.string(),
  count: z.number().int().nonnegative(),
});

export const SessionAnalyticsResponseSchema = z.object({
  session: SessionAggregateSchema,
  bucketSeconds: z.number().positive(),
  series: z.array(
    z.object({
      bucketStart: z.iso.datetime({ offset: true }),
      byModel: z.array(SessionAnalyticsSeriesEntrySchema),
    }),
  ),
  seats: z.array(SessionAnalyticsSeatSchema),
  latencyHistogram: z.array(SessionAnalyticsHistogramEntrySchema),
  tokensPerRequestHistogram: z.array(SessionAnalyticsHistogramEntrySchema),
});

export type SessionAggregate = z.infer<typeof SessionAggregateSchema>;
export type SessionModelBreakdown = z.infer<typeof SessionModelBreakdownSchema>;
export type SessionDetailResponse = z.infer<typeof SessionDetailResponseSchema>;
export type SessionAnalyticsResponse = z.infer<
  typeof SessionAnalyticsResponseSchema
>;
export type SessionAnalyticsSeat = z.infer<typeof SessionAnalyticsSeatSchema>;
