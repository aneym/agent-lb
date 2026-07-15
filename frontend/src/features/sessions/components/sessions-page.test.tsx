import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionsPage } from "@/features/sessions/components/sessions-page";
import {
  useSession,
  useSessionAnalytics,
  useSessions,
} from "@/features/sessions/hooks/use-sessions";
import { formatDateTimeInline } from "@/utils/formatters";

vi.mock("@/features/sessions/hooks/use-sessions", () => ({
  useSessions: vi.fn(),
  useSession: vi.fn(),
  useSessionAnalytics: vi.fn(),
}));

const useSessionsMock = useSessions as unknown as ReturnType<typeof vi.fn>;
const useSessionMock = useSession as unknown as ReturnType<typeof vi.fn>;
const useSessionAnalyticsMock = useSessionAnalytics as unknown as ReturnType<
  typeof vi.fn
>;

function renderPage(initialEntry = "/sessions") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <SessionsPage />
    </MemoryRouter>,
  );
}

const session = {
  sessionId: "019-session-very-long-identifier",
  provider: "anthropic",
  useragentGroup: "claude-code",
  models: [
    { model: "claude-fable-5", requests: 3 },
    { model: "gpt-5.6-sol", requests: 2 },
  ],
  requests: 5,
  inputTokens: 1_000,
  outputTokens: 250,
  cachedInputTokens: 600,
  costUsd: 0.42,
  errors: 1,
  firstSeen: "2026-07-15T10:00:00Z",
  lastSeen: "2026-07-15T11:00:00Z",
  sparkline: [0, 1, 3, 1],
};

const analytics = {
  session,
  bucketSeconds: 300,
  series: [
    {
      bucketStart: "2026-07-15T10:00:00Z",
      byModel: [
        {
          model: "claude-fable-5",
          reasoningEffort: null,
          requests: 3,
          outputTokens: 150,
          cachedInputTokens: 400,
          costUsd: 0.3,
        },
      ],
    },
  ],
  seats: [
    {
      model: "claude-fable-5",
      reasoningEffort: null,
      requests: 3,
      inputTokens: 700,
      outputTokens: 150,
      cachedInputTokens: 400,
      costUsd: 0.3,
      errors: 0,
    },
    {
      model: "gpt-5.6-sol",
      reasoningEffort: "medium",
      requests: 2,
      inputTokens: 300,
      outputTokens: 100,
      cachedInputTokens: 200,
      costUsd: 0.12,
      errors: 1,
    },
  ],
  latencyHistogram: [
    { label: "0-1s", count: 3 },
    { label: "1-2s", count: 2 },
  ],
  tokensPerRequestHistogram: [
    { label: "<100", count: 1 },
    { label: "100-500", count: 4 },
  ],
};

describe("SessionsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSessionsMock.mockReturnValue({
      data: { sessions: [session], total: 1 },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    useSessionMock.mockReturnValue({
      data: { session, byModel: [], recentRequests: [] },
      isLoading: false,
      error: null,
    });
    useSessionAnalyticsMock.mockReturnValue({
      data: analytics,
      isLoading: false,
      error: null,
    });
  });

  it("renders session usage, sparkline, and the default window", () => {
    renderPage();

    expect(
      screen.getByRole("heading", { name: "Sessions" }),
    ).toBeInTheDocument();
    expect(screen.getByText("claude-code")).toBeInTheDocument();
    expect(screen.getByText("1.25K (600 Cached)")).toBeInTheDocument();
    expect(
      screen.getByTestId(`session-sparkline-${session.sessionId}`),
    ).not.toBeEmptyDOMElement();
    const sessionRow = screen.getByRole("row", {
      name: `View session ${session.sessionId}`,
    });
    expect(sessionRow).toHaveTextContent(
      `First ${formatDateTimeInline(session.firstSeen)}`,
    );
    expect(sessionRow).toHaveTextContent(
      `Last ${formatDateTimeInline(session.lastSeen)}`,
    );
    expect(screen.getByRole("button", { name: "3d" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(useSessionsMock).toHaveBeenCalledWith({
      windowMinutes: 4_320,
      limit: 25,
      offset: 0,
    });
  });

  it("hides the sparkline gracefully when the backend omits it", () => {
    useSessionsMock.mockReturnValue({
      data: { sessions: [{ ...session, sparkline: undefined }], total: 1 },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderPage();

    expect(
      screen.getByTestId(`session-sparkline-${session.sessionId}`),
    ).toBeEmptyDOMElement();
  });

  it("opens a full-width analytics view with tiles, seats, and histograms", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(
      screen.getByRole("row", { name: `View session ${session.sessionId}` }),
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Session Analytics" }),
    ).toBeInTheDocument();
    expect(screen.getByText("1h")).toBeInTheDocument();
    expect(screen.getAllByText("Driver")).toHaveLength(2);
    expect(screen.getAllByText("Implementer")).toHaveLength(2);
    expect(screen.getByTestId("latency-histogram-bars")).toBeInTheDocument();
    expect(screen.getByTestId("tokens-histogram-bars")).toBeInTheDocument();
    expect(useSessionAnalyticsMock).toHaveBeenLastCalledWith(
      session.sessionId,
      4_320,
    );
    expect(useSessionMock).toHaveBeenLastCalledWith(session.sessionId);
  });

  it("loads an unknown query-param id directly and preserves its error semantics", () => {
    const unknownSessionId = "session-not-in-current-window";
    useSessionAnalyticsMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Not found"),
    });

    renderPage(
      `/sessions?tab=active&session=${encodeURIComponent(unknownSessionId)}`,
    );

    expect(
      screen.getByText(/Failed to load session analytics: Not found/),
    ).toBeInTheDocument();
    expect(useSessionAnalyticsMock).toHaveBeenLastCalledWith(
      unknownSessionId,
      4_320,
    );
  });

  it("surfaces list failures instead of an empty state", () => {
    useSessionsMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("upstream unavailable"),
      refetch: vi.fn(),
    });

    renderPage();

    expect(
      screen.getByText(/Failed to load sessions: upstream unavailable/),
    ).toBeInTheDocument();
    expect(screen.queryByText("No sessions")).not.toBeInTheDocument();
  });
});
