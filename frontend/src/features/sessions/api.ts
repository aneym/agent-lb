import { get } from "@/lib/api-client";
import { SessionDetailResponseSchema, SessionsResponseSchema } from "./schemas";

export type SessionsParams = {
  windowMinutes: number;
  limit: number;
  offset: number;
};

export function getSessions(params: SessionsParams) {
  const query = new URLSearchParams({
    windowMinutes: String(params.windowMinutes),
    limit: String(params.limit),
    offset: String(params.offset),
  });
  return get(`/api/sessions?${query.toString()}`, SessionsResponseSchema);
}

export function getSession(sessionId: string) {
  return get(
    `/api/sessions/${encodeURIComponent(sessionId)}`,
    SessionDetailResponseSchema,
  );
}
