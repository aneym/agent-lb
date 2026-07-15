import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import { getSession, getSessions } from "@/features/sessions/api";
import {
  useSession,
  useSessions,
} from "@/features/sessions/hooks/use-sessions";

vi.mock("@/features/sessions/api", () => ({
  getSessions: vi.fn(),
  getSession: vi.fn(),
}));

const getSessionsMock = vi.mocked(getSessions);
const getSessionMock = vi.mocked(getSession);

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: PropsWithChildren) {
    return createElement(
      QueryClientProvider,
      { client: queryClient },
      children,
    );
  };
}

describe("session hooks", () => {
  it("loads sessions with the selected window and pagination", async () => {
    getSessionsMock.mockResolvedValue({ sessions: [], total: 0 });

    const params = { windowMinutes: 4_320, limit: 25, offset: 50 };
    const { result } = renderHook(() => useSessions(params), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getSessionsMock).toHaveBeenCalledWith(params);
  });

  it("does not load detail until a session is selected", async () => {
    getSessionMock.mockResolvedValue({
      session: {
        sessionId: "session-1",
        provider: "anthropic",
        useragentGroup: null,
        models: [],
        requests: 0,
        inputTokens: 0,
        outputTokens: 0,
        cachedInputTokens: 0,
        costUsd: 0,
        errors: 0,
        firstSeen: "2026-07-15T10:00:00Z",
        lastSeen: "2026-07-15T10:00:00Z",
      },
      byModel: [],
      recentRequests: [],
    });

    const initialProps: { sessionId: string | null } = { sessionId: null };
    const { result, rerender } = renderHook(
      ({ sessionId }: { sessionId: string | null }) => useSession(sessionId),
      { initialProps, wrapper: createWrapper() },
    );

    expect(result.current.fetchStatus).toBe("idle");
    expect(getSessionMock).not.toHaveBeenCalled();

    rerender({ sessionId: "session-1" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getSessionMock).toHaveBeenCalledWith("session-1");
  });
});
