import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { get, put } from "@/lib/api-client";

const maybeNumber = z.number().nullable().optional();
const maybeDate = z.string().nullable().optional();
const QuotaSourceSchema = z.enum(["vendor", "vendor_observed", "estimate", "unavailable"]);
const WindowKindSchema = z.enum(["session", "daily", "weekly", "monthly"]);
export const PoolWindowSchema = z.object({
  id: z.string(),
  kind: WindowKindSchema,
  label: z.string(),
  remainingPercent: z.number().nullable(),
  usedPercent: z.number().nullable(),
  resetsAt: z.string().nullable(),
  measuredAccounts: z.number(),
  source: z.string().nullable(),
});
export type PoolWindow = z.infer<typeof PoolWindowSchema>;
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
  percentUsed: maybeNumber,
  percentSource: z.string().nullable().optional(),
  cycleResetAt: maybeDate,
  refills: z
    .array(z.object({ at: z.string(), accounts: z.number(), remainingPercent: z.number() }))
    .nullable()
    .optional(),
  usableAccounts: maybeNumber,
  totalAccounts: maybeNumber,
  windows: z.array(PoolWindowSchema).nullable().optional(),
  quotaSource: z.string().nullable().optional(),
  unavailableReason: z.string().nullable().optional(),
});
export const PoolsSchema = z.object({ generatedAt: z.string(), pools: z.array(PoolSchema) });
export type Pool = z.infer<typeof PoolSchema>;
export const SeatQuotaWindowSchema = z.object({
  id: z.string(),
  kind: WindowKindSchema,
  label: z.string(),
  durationMins: z.number().nullable(),
  usedPercent: z.number().nullable(),
  remainingPercent: z.number().nullable(),
  resetsAt: z.string().nullable(),
  source: QuotaSourceSchema.exclude(["unavailable"]),
});
export type SeatQuotaWindow = z.infer<typeof SeatQuotaWindowSchema>;
export const SeatQuotaSchema = z.object({
  source: QuotaSourceSchema,
  observedAt: z.string().nullable(),
  stale: z.boolean(),
  unavailableReason: z
    .enum(["not_reported", "auth_unsupported", "credential_unavailable", "vendor_error", "vendor_parse", "not_collected"])
    .nullable(),
  primaryWindowId: z.string().nullable(),
  windows: z.array(SeatQuotaWindowSchema),
});
export type SeatQuota = z.infer<typeof SeatQuotaSchema>;
const SeatTodaySchema = z.object({
  runs: z.number(),
  ok: z.number(),
  tokensIn: z.number().nullable(),
  tokensOut: z.number().nullable(),
  tokensKnown: z.boolean(),
  okKnown: z.boolean().default(true),
  valueUsd: z.number().nullable(),
  valueSource: z.literal("estimate").nullable(),
  models: z.array(
    z.object({
      model: z.string(),
      runs: z.number(),
      tokensIn: z.number().nullable(),
      tokensOut: z.number().nullable(),
    }),
  ),
});
export type SeatToday = z.infer<typeof SeatTodaySchema>;
const SeatRunSchema = z.object({
  ts: z.string(),
  model: z.string().nullable(),
  tokensIn: z.number().nullable().optional(),
  tokensOut: z.number().nullable().optional(),
  cacheRead: z.number().nullable().optional(),
  ok: z.boolean().nullable().optional(),
  wallS: z.number().nullable().optional(),
});
export const SeatAccountSchema = z.object({
  id: z.string(),
  vendor: z.string(),
  auth: z.string().nullable().optional(),
  tier: z.string().nullable().optional(),
  plan: z.string().nullable().optional(),
  enabled: z.boolean(),
  ready: z.boolean(),
  authOk: z.boolean().nullable(),
  authCheckedAt: maybeDate,
  cooldownUntil: maybeDate,
  cooldowns: z.record(z.string(), z.string()).optional(),
  lastErrorKind: z.string().nullable().optional(),
  lastErrorAt: maybeDate,
  lastRunAt: maybeDate,
  recentRuns: z.array(SeatRunSchema).optional(),
  email: z.string().nullable().optional(),
  planLabel: z.string().nullable().optional(),
  status: z.enum(["active", "rate_limited", "paused", "reauth_required"]).optional(),
  statusResetAt: maybeDate,
  quota: SeatQuotaSchema.optional(),
  today: SeatTodaySchema.optional(),
});
export const SeatAccountsSchema = z.object({
  accounts: z.array(SeatAccountSchema),
  stateUpdatedAt: z.string().nullable().optional(),
  source: z.string().optional(),
  quotaRefreshedAt: maybeDate,
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
export const SeatActivitySchema = z.object({
  periodHours: z.number(),
  runs: z.number(),
  ok: z.number(),
  tokensIn: z.number().nullable(),
  tokensOut: z.number().nullable(),
  tokensKnown: z.boolean(),
  okKnown: z.boolean().default(true),
  valueUsd: z.number().nullable(),
  valueSource: z.literal("estimate").nullable(),
  models: z.array(z.object({ model: z.string(), runs: z.number() })),
});
export type SeatActivity = z.infer<typeof SeatActivitySchema>;
export const UsageSummarySchema = z
  .object({
    provider: z.string().nullable().optional(),
    seatActivity: SeatActivitySchema.nullable().optional(),
  })
  .passthrough();
const SummarySchema = z.object({
  provider: z.string().nullable().optional(),
  seatActivity: SeatActivitySchema.nullable().optional(),
  totals: z.object({ requests: z.number(), costUsd: z.number(), errors: z.number() }),
  groups: z.array(
    z.object({
      key: z.string().nullable(),
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
export const useSeatActivity = (provider: string) =>
  useLbQuery(
    "usage-summary",
    `/api/usage/summary${provider ? `?provider=${encodeURIComponent(provider)}` : ""}`,
    UsageSummarySchema,
  );
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
