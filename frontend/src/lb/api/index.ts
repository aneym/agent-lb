import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { get, put } from "@/lib/api-client";

const maybeNumber = z.number().nullable().optional();
const maybeDate = z.string().nullable().optional();
export const PoolSchema = z.object({
  id: z.string(),
  provider: z.string(),
  kind: z.string(),
  accounts: z.number(),
  eligibleAccounts: z.number(),
  status: z.string(),
  headroomPercent: maybeNumber,
  aggregateRemainingPercent: maybeNumber,
  fiveHourRemainingPercent: maybeNumber,
  fiveHourResetAt: maybeDate,
  weeklyRemainingPercent: maybeNumber,
  weeklyResetAt: maybeDate,
  weeklyPacePercent: maybeNumber,
  windowLabel: z.string().nullable().optional(),
  resetAt: maybeDate,
});
const PoolsSchema = z.object({ generatedAt: z.string(), pools: z.array(PoolSchema) });
export type Pool = z.infer<typeof PoolSchema>;
const SeatAccountSchema = z.object({
  id: z.string(),
  vendor: z.string(),
  tier: z.string().nullable().optional(),
  enabled: z.boolean(),
  ready: z.boolean(),
  authOk: z.boolean(),
  cooldownUntil: maybeDate,
  lastRunAt: maybeDate,
});
const SeatAccountsSchema = z.object({
  accounts: z.array(SeatAccountSchema),
  stateUpdatedAt: z.string().nullable().optional(),
});
export type SeatAccount = z.infer<typeof SeatAccountSchema>;
const ReceiptSchema = z.object({
  id: z.union([z.string(), z.number()]),
  requestedAt: z.string(),
  sessionId: z.string().nullable(),
  accountId: z.string().nullable(),
  provider: z.string().nullable(),
  pool: z.string().nullable(),
  model: z.string().nullable(),
  costUsd: z.number().nullable(),
  status: z.string(),
  inputTokens: z.number(),
  outputTokens: z.number(),
});
const ReceiptsSchema = z.object({
  receipts: z.array(ReceiptSchema),
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
});
const SummarySchema = z.object({
  totals: z.object({ requests: z.number(), costUsd: z.number(), errors: z.number() }),
  groups: z.array(
    z.object({
      key: z.string(),
      provider: z.string().nullable(),
      requests: z.number(),
      costUsd: z.number(),
      errors: z.number(),
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
const StickySchema = z.object({
  total: z.number(),
  entries: z.array(
    z.object({
      key: z.string(),
      displayName: z.string().nullable().optional(),
      kind: z.string().optional(),
    }),
  ),
});
const VersionSchema = z.object({ currentVersion: z.string() });
const AddressSchema = z.object({ connectAddress: z.string() });
const PolicySchema = z.object({
  activeVersion: z.number().nullable().optional(),
  versions: z.array(z.object({ version: z.number(), state: z.string() })),
});
const SchedulesSchema = z.object({
  schedules: z.array(z.object({ accountId: z.string(), resumeAt: z.string().nullable() })),
});
const useLbQuery = <T>(key: string, url: string, schema: z.ZodType<T>) =>
  useQuery({
    queryKey: ["lb", key, url],
    queryFn: () => get(url, schema),
    refetchInterval: 30_000,
    retry: 1,
  });
export const usePools = () => useLbQuery("pools", "/api/pools", PoolsSchema);
export const useSeatAccounts = () =>
  useLbQuery("seat-accounts", "/api/pools/seat-accounts", SeatAccountsSchema);
export const useReceipts = (params = "") =>
  useLbQuery("receipts", `/api/receipts${params ? `?${params}` : ""}`, ReceiptsSchema);
export const useReceiptSummary = (params = "") =>
  useLbQuery("summary", `/api/receipts/summary${params ? `?${params}` : ""}`, SummarySchema);
export const useStickySessions = () => useLbQuery("sticky", "/api/sticky-sessions", StickySchema);
export const useRuntimeVersion = () => useLbQuery("version", "/api/runtime/version", VersionSchema);
export const useConnectAddress = () =>
  useLbQuery("address", "/api/settings/runtime/connect-address", AddressSchema);
export const usePolicyVersions = () =>
  useLbQuery("policies", "/api/routing-policy/versions", PolicySchema);
export const useResumeSchedules = () =>
  useLbQuery("resume-schedules", "/api/account-resume-schedules", SchedulesSchema);
export const setResumeAt = (accountId: string, resumeAt: string | null) =>
  put(
    `/api/accounts/${encodeURIComponent(accountId)}/resume-at`,
    z.object({ accountId: z.string(), resumeAt: maybeDate }),
    { body: { resumeAt } },
  );
