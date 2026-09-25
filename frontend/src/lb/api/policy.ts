import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { del, get, patch, post } from "@/lib/api-client";

const jsonObject = z.record(z.string(), z.unknown());
const VersionSchema = z.object({
  version: z.number(),
  state: z.enum(["active", "retired", "draft", "rejected"]),
  source: z.string(),
  summary: z.string(),
  createdAt: z.string(),
  createdBy: z.string().nullable(),
  approvedBy: z.string().nullable(),
  approvedAt: z.string().nullable(),
  counts: z.object({
    classes: z.number(),
    options: z.number(),
    aliases: z.number(),
    retired: z.number(),
  }),
});
const VersionsSchema = z.object({
  activeVersion: z.number().nullable(),
  versions: z.array(VersionSchema),
});
const DetailSchema = VersionSchema.extend({
  routingTable: jsonObject,
  decider: jsonObject,
  diff: z.array(z.object({ path: z.string(), before: z.unknown(), after: z.unknown() })),
});

export type PolicyVersion = z.infer<typeof VersionSchema>;
export type PolicyDetail = z.infer<typeof DetailSchema>;
export type PolicyTable = Record<string, unknown>;
export const policyKeys = {
  versions: ["lb", "policy", "versions"],
  detail: (version: number) => ["lb", "policy", "version", version],
};
export function useRoutingPolicyVersions() {
  return useQuery({
    queryKey: policyKeys.versions,
    queryFn: () => get("/api/routing-policy/versions", VersionsSchema),
    retry: 1,
  });
}
export function useRoutingPolicyDetail(version: number | null) {
  return useQuery({
    queryKey: policyKeys.detail(version ?? 0),
    queryFn: () => get(`/api/routing-policy/versions/${version}`, DetailSchema),
    enabled: version != null,
    retry: 1,
  });
}
export function createPolicyDraft(body: {
  summary: string;
  routingTable?: PolicyTable;
  decider?: PolicyTable;
}) {
  return post("/api/routing-policy/drafts", DetailSchema, { body });
}
export function updatePolicyDraft(
  version: number,
  body: { summary?: string; routingTable?: PolicyTable; decider?: PolicyTable },
) {
  return patch(`/api/routing-policy/drafts/${version}`, DetailSchema, { body });
}
export function discardPolicyDraft(version: number) {
  return del(`/api/routing-policy/drafts/${version}`);
}
export function getPolicyDetail(version: number) {
  return get(`/api/routing-policy/versions/${version}`, DetailSchema);
}
