import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { get } from "@/lib/api-client";

const PoolBurnSchema = z.object({
  poolSharePct: z.number(),
  accountWeekPct: z.number(),
  paceMultiple: z.number().nullable().optional(),
  requests: z.number(),
  inputTokens: z.number(),
  outputTokens: z.number(),
  cacheReadTokens: z.number(),
});
export type ThreadPoolBurn = z.infer<typeof PoolBurnSchema>;

const NativeSessionSchema = z.object({ nativeId: z.string(), driver: z.string() });

const ThreadBaseSchema = z.object({
  host: z.string(),
  threadId: z.string(),
  rootThreadId: z.string(),
  title: z.string().nullable(),
  projectTitle: z.string().nullable(),
  createdAt: z.string().nullable(),
  lastRequestAt: z.string().nullable(),
  archived: z.boolean(),
  deleted: z.boolean(),
  subagentCount: z.number(),
  nativeSessions: z.array(NativeSessionSchema),
  pools: z.record(z.string(), PoolBurnSchema),
  models: z.record(z.string(), z.number()),
});
export type ThreadBurn = z.infer<typeof ThreadBaseSchema> & { children: ThreadBurn[] };
const ThreadSchema: z.ZodType<ThreadBurn> = ThreadBaseSchema.extend({
  children: z.lazy(() => z.array(ThreadSchema)),
});

const ShareSchema = z.object({ poolSharePct: z.number(), accountWeekPct: z.number() });
const PoolTotalsSchema = z.object({
  t3Threads: ShareSchema,
  t3Unregistered: ShareSchema,
  outsideT3: ShareSchema,
  unexplained: ShareSchema,
  matchedRequestPct: z.number().nullable().optional(),
});
export type PoolTotals = z.infer<typeof PoolTotalsSchema>;

const BurnSchema = z.object({
  generatedAt: z.string(),
  windowHours: z.number(),
  host: z.string().nullable().optional(),
  pools: z.record(z.string(), PoolTotalsSchema),
  threads: z.array(ThreadSchema),
});
export type ThreadsBurn = z.infer<typeof BurnSchema>;

export function useThreadsBurn(range: string, host: string) {
  const query = new URLSearchParams({ range });
  if (host) query.set("host", host);
  const url = `/api/t3/threads/burn?${query}`;
  return useQuery({
    queryKey: ["threads", "burn", url],
    queryFn: () => get(url, BurnSchema),
    refetchInterval: 60_000,
  });
}
