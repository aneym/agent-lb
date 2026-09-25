import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { get } from "@/lib/api-client";

const ReceiptSchema = z.object({
  id: z.union([z.number(), z.string()]),
  requestedAt: z.string(),
  sessionId: z.string().nullable(),
  clientSessionId: z.string().nullable(),
  apiKeyId: z.string().nullable(),
  apiKeyName: z.string().nullable(),
  apiKeyPrefix: z.string().nullable(),
  accountId: z.string().nullable(),
  provider: z.string().nullable(),
  pool: z.string().nullable(),
  model: z.string().nullable(),
  reasoningEffort: z.string().nullable(),
  inputTokens: z.number(),
  outputTokens: z.number(),
  cacheReadTokens: z.number(),
  cacheWriteTokens: z.number(),
  cacheReadRatio: z.number().nullable(),
  latencyMs: z.number().nullable(),
  latencyFirstTokenMs: z.number().nullable(),
  status: z.enum(["ok", "error"]),
  httpStatus: z.number().nullable(),
  errorCode: z.string().nullable(),
  costUsd: z.number().nullable(),
});
export type Receipt = z.infer<typeof ReceiptSchema>;
const ReceiptsSchema = z.object({
  receipts: z.array(ReceiptSchema),
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
});
const SummarySchema = z.object({
  totals: z.object({
    requests: z.number(),
    errors: z.number(),
    errorRate: z.number(),
    inputTokens: z.number(),
    outputTokens: z.number(),
    cacheReadTokens: z.number(),
    cacheWriteTokens: z.number(),
    tokens: z.number(),
    cacheReadRatio: z.number().nullable(),
    costUsd: z.number(),
    p50LatencyMs: z.number().nullable(),
    p95LatencyMs: z.number().nullable(),
    topErrorCode: z.string().nullable(),
  }),
  groups: z.array(
    z.object({
      key: z.string().nullable(),
      provider: z.string().nullable(),
      requests: z.number(),
      errors: z.number(),
      errorRate: z.number(),
      tokens: z.number(),
      cacheReadRatio: z.number().nullable(),
      costUsd: z.number(),
    }),
  ),
  series: z.array(
    z.object({
      start: z.string(),
      requests: z.number(),
      byProvider: z.record(z.string(), z.number()),
    }),
  ),
});
export type ReceiptSummary = z.infer<typeof SummarySchema>;
export function useUsageSummary(filters: string, groupBy: string, bucket: string, enabled = true) {
  const query = new URLSearchParams(filters);
  query.set("group_by", groupBy);
  query.set("bucket", bucket);
  const url = `/api/receipts/summary?${query}`;
  return useQuery({
    queryKey: ["usage", "summary", url],
    queryFn: () => get(url, SummarySchema),
    enabled,
    refetchInterval: 30_000,
  });
}
export function useUsageReceipts(filters: string, limit: number) {
  const query = new URLSearchParams(filters);
  query.set("limit", String(limit));
  const url = `/api/receipts?${query}`;
  return useQuery({
    queryKey: ["usage", "receipts", url],
    queryFn: () => get(url, ReceiptsSchema),
    refetchInterval: 30_000,
  });
}
export const fetchUsageReceipts = (params: URLSearchParams) =>
  get(`/api/receipts?${params}`, ReceiptsSchema);
