import { useQuery } from "@tanstack/react-query";

import {
  getSession,
  getSessionAnalytics,
  getSessions,
  type SessionsParams,
} from "../api";

export function useSessions(params: SessionsParams) {
  return useQuery({
    queryKey: ["sessions", params],
    queryFn: () => getSessions(params),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
}

export function useSession(sessionId: string | null) {
  return useQuery({
    queryKey: ["sessions", "detail", sessionId],
    queryFn: () => getSession(sessionId ?? ""),
    enabled: sessionId !== null,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
}

export function useSessionAnalytics(
  sessionId: string | null,
  windowMinutes: number,
) {
  return useQuery({
    queryKey: ["sessions", "analytics", sessionId, windowMinutes],
    queryFn: () => getSessionAnalytics(sessionId ?? "", windowMinutes),
    enabled: sessionId !== null,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
}
