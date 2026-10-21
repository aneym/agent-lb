import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { get } from "@/lib/api-client";

const TracePartsSchema = z.object({
  system: z.number(),
  tools: z.number(),
  history: z.number(),
  toolResults: z.number(),
});
export type TraceParts = z.infer<typeof TracePartsSchema>;
export const PARTS: { key: keyof TraceParts; label: string; fill: string }[] = [
  { key: "system", label: "System", fill: "var(--ink)" },
  { key: "tools", label: "Tools", fill: "var(--g2)" },
  { key: "history", label: "History", fill: "var(--g3)" },
  { key: "toolResults", label: "Tool results", fill: "var(--track)" },
];
const TraceRequestSchema = z.object({
  logId: z.number(),
  requestId: z.string(),
  requestedAt: z.string(),
  provider: z.string(),
  model: z.string(),
  status: z.string(),
  latencyMs: z.number().nullable(),
  latencyFirstTokenMs: z.number().nullable(),
  promptTokens: z.number(),
  uncachedTokens: z.number(),
  cacheReadTokens: z.number(),
  cacheWriteTokens: z.number(),
  outputTokens: z.number(),
  cacheReadRatio: z.number().nullable(),
  cacheKind: z.string().nullable(),
  bodySchema: z.string().nullable(),
  bodyBytes: z.number().nullable(),
  estParts: TracePartsSchema.nullable(),
  partChars: TracePartsSchema.nullable(),
  estimateRatio: z.number().nullable(),
});
export type TraceRequest = z.infer<typeof TraceRequestSchema>;
const SessionTraceSchema = z.object({
  sessionId: z.string(),
  requests: z.array(TraceRequestSchema),
  totals: z.object({
    requests: z.number(),
    measured: z.number(),
    coverage: z.number(),
    promptTokens: z.number(),
    cacheReadTokens: z.number(),
    latencyMs: z.number(),
    estParts: TracePartsSchema,
  }),
});
export type SessionTrace = z.infer<typeof SessionTraceSchema>;
export function useSessionTrace(sessionId: string) {
  return useQuery({
    queryKey: ["sessions", "trace", sessionId],
    queryFn: () =>
      get(`/api/sessions/${encodeURIComponent(sessionId)}/trace?limit=500`, SessionTraceSchema),
    refetchInterval: 15_000,
  });
}
