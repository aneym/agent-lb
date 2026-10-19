import { z } from "zod";
import { ApiKeySchema } from "@/features/api-keys/schemas";

export const KeyWithOwnerSchema = ApiKeySchema.extend({
  memberId: z.string().nullable().optional(),
  memberName: z.string().nullable().optional(),
});
export type KeyWithOwner = z.infer<typeof KeyWithOwnerSchema>;
