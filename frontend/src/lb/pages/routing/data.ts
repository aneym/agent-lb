import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { get, put } from "@/lib/api-client";
import type { Pool } from "@/lb/api";
import { paceDetail, tierText, type PaceTier } from "@/lb/format";
import { vendorName } from "@/lb/kit/provider-mark-helpers";

const nullableString = z.string().nullable();
const nullableNumber = z.number().nullable();
const Candidate = z.object({
  seat: z.string(),
  model: nullableString,
  score: nullableNumber,
  excludedReason: nullableString,
});
export const Decision = z.object({
  id: z.string(),
  ts: z.string(),
  sessionId: nullableString,
  taskClass: nullableString,
  kind: z.enum(["dispatch", "of_decision"]),
  pick: z
    .object({ seat: nullableString, model: nullableString, subagentType: nullableString })
    .nullable(),
  candidates: z.array(Candidate),
  abstained: z.boolean(),
  recheck: nullableString,
  fallback: nullableString,
  deciderMs: nullableNumber,
  deciderCostUsd: nullableNumber,
  policyVersion: nullableNumber,
  outcome: z.object({
    state: z.enum(["ok", "failed", "open"]),
    durationS: nullableNumber,
    tokensIn: nullableNumber,
    tokensOut: nullableNumber,
    error: nullableString,
    match: z.enum(["exact", "ambiguous", "none"]),
  }),
});
export type RouteDecision = z.infer<typeof Decision>;
const Decisions = z.object({
  decisions: z.array(Decision),
  counts: z.object({
    total: z.number(),
    fallbacks: z.number(),
    noPick: z.number(),
    failed: z.number(),
  }),
  truncated: z.boolean(),
});
const Seat = z.object({
  alias: z.string().nullable().optional(),
  model: z.string(),
  seat: z.string(),
  pool: z.string().optional(),
});
const Exclusion = z.object({
  alias: z.string().nullable().optional(),
  seat: z.string(),
  reason: z.string(),
});
const Menu = z.object({
  classes: z.record(
    z.string(),
    z.object({
      driver: z.boolean().optional(),
      seats: z.array(Seat),
      excluded: z.array(Exclusion),
      paced: z.array(z.unknown()).optional(),
    }),
  ),
  pools: z.record(
    z.string(),
    z.object({
      pace: z
        .object({
          tier: nullableString.optional(),
          rho: nullableNumber.optional(),
          b_star: nullableNumber.optional(),
        })
        .nullable()
        .optional(),
      status: z.string().optional(),
    }),
  ),
});
const PolicyVersions = z.object({
  activeVersion: z.number().nullable(),
  versions: z.array(z.object({ version: z.number() })),
});
const Summary = z.object({
  totals: z.object({
    requests: z.number(),
    errors: z.number(),
    errorRate: z.number(),
    p50LatencyMs: nullableNumber,
  }),
  groups: z.array(z.object({ key: z.string(), provider: nullableString, requests: z.number() })),
});
const PlannerSettings = z.object({ mode: z.string() }).passthrough();
const PlannerDecision = z.object({
  createdAt: z.string(),
  action: z.string(),
  scheduledAt: nullableString.optional(),
  status: z.string(),
});
const Forecast = z.object({ peakSlotStart: nullableString.optional() });
const useRoutingQuery = <T>(key: string, url: string, schema: z.ZodType<T>) =>
  useQuery({
    queryKey: ["routing", key, url],
    queryFn: () => get(url, schema),
    retry: 1,
    refetchInterval: 30_000,
  });
export const localMidnight = () => {
  const date = new Date();
  date.setHours(0, 0, 0, 0);
  return date.toISOString();
};
export const useRouteDecisions = (since: string, taskClass = "", outcome = "") => {
  const params = new URLSearchParams({ since, limit: "500" });
  if (taskClass) params.set("class", taskClass);
  if (outcome) params.set("outcome", outcome);
  return useRoutingQuery("decisions", `/api/route-decisions?${params}`, Decisions);
};
export const useMenu = () => useRoutingQuery("menu", "/api/menu", Menu);
export const useRoutingPolicy = () =>
  useRoutingQuery("policy", "/api/routing-policy/versions", PolicyVersions);
export const useTodaySummary = () =>
  useRoutingQuery(
    "summary",
    `/api/receipts/summary?since=${encodeURIComponent(localMidnight())}&group_by=provider`,
    Summary,
  );
export const usePlannerSettings = () =>
  useRoutingQuery("planner-settings", "/api/quota-planner/settings", PlannerSettings);
export const usePlannerDecisions = () =>
  useRoutingQuery("planner-decisions", "/api/quota-planner/decisions", z.array(PlannerDecision));
export const usePlannerForecast = () =>
  useRoutingQuery("planner-forecast", "/api/quota-planner/forecast", Forecast);
export const updatePlannerMode = (settings: z.infer<typeof PlannerSettings>, mode: string) =>
  put("/api/quota-planner/settings", PlannerSettings, { body: { ...settings, mode } });

const KIND_TEXT: Record<string, string> = {
  session: "5-hour",
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly cap",
};
const SEAT_PROVIDERS = ["cursor", "devin", "muse"];
export function poolPaceText(
  name: string,
  shadow: PaceTier | null | undefined,
  pool?: Pool,
): string {
  if (shadow?.tier) return `${tierText(shadow)} · ${paceDetail(shadow)} (shadow)`;
  const provider = pool?.provider ?? name.split("-")[0];
  const measured = (pool?.windows ?? []).filter((w) => w.remainingPercent != null);
  if (measured.length) {
    const tight = measured.reduce((low, w) =>
      (w.remainingPercent as number) < (low.remainingPercent as number) ? w : low,
    );
    const est = tight.source === "estimate" ? " (est.)" : "";
    return `${KIND_TEXT[tight.kind] ?? tight.label} · ${Math.round(tight.remainingPercent as number)}% left${est}`;
  }
  if (pool?.quotaSource === "unavailable" || (pool?.windows && SEAT_PROVIDERS.includes(provider)))
    return `No reading from ${vendorName(provider)}`;
  if (["glm", "kimi"].includes(provider)) return "No cap";
  if (pool?.windowLabel === "month") {
    const left = pool.percentUsed != null ? Math.max(0, 100 - pool.percentUsed) : null;
    const est = pool.percentSource === "estimate" ? " (est.)" : "";
    return left == null ? "Monthly cap" : `Monthly cap · ${Math.round(left)}% left${est}`;
  }
  if (SEAT_PROVIDERS.includes(provider)) return `No reading from ${vendorName(provider)}`;
  return "No weekly cap";
}
