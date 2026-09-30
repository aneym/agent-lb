import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { ApiError, get } from "@/lib/api-client";

import type { AccountPlan } from "./pool-rows";

const RungSchema = z.object({
  id: z.string(),
  model: z.string(),
  harness: z.string(),
});
const AccountSchema = z.object({
  pool: z.string(),
  label: z.string(),
  count: z.number(),
  change: z.enum(["add", "drop", "keep"]),
  delta: z.number(),
  reason: z.string(),
});
const LevelSchema = z.object({
  title: z.string(),
  why: z.string(),
  accounts: z.array(AccountSchema),
  ladders: z.object({
    implement: z.array(RungSchema).optional(),
    mechanical: z.array(RungSchema).optional(),
    explore: z.array(RungSchema).optional(),
    review: z.array(RungSchema).optional(),
  }),
  risk: z.string(),
});
const PlanSchema = z.object({
  generatedAt: z.string(),
  recommended: z.enum(["budget", "balanced", "unlimited"]),
  levels: z.object({
    budget: LevelSchema,
    balanced: LevelSchema,
    unlimited: LevelSchema,
  }),
});

export function useAccountPlan() {
  return useQuery({
    queryKey: ["lb", "pools-plan"],
    queryFn: async (): Promise<AccountPlan | null> => {
      try {
        return await get("/api/pools/plan", PlanSchema);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    retry: false,
    refetchInterval: 30_000,
  });
}
