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

export type SessionAggregate = z.infer<typeof SessionAggregateSchema>;
export type SessionModelBreakdown = z.infer<typeof SessionModelBreakdownSchema>;
export type SessionDetailResponse = z.infer<typeof SessionDetailResponseSchema>;
