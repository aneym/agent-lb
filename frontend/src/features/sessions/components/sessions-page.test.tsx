import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionsPage } from "@/features/sessions/components/sessions-page";
import {
  useSession,
  useSessions,
} from "@/features/sessions/hooks/use-sessions";
import { formatDateTimeInline } from "@/utils/formatters";

vi.mock("@/features/sessions/hooks/use-sessions", () => ({
  useSessions: vi.fn(),
  useSession: vi.fn(),
}));

const useSessionsMock = useSessions as unknown as ReturnType<typeof vi.fn>;
const useSessionMock = useSession as unknown as ReturnType<typeof vi.fn>;

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
    { model: "gpt-5.6-sol-medium", requests: 2 },
  ],
  requests: 5,
  inputTokens: 1_000,
  outputTokens: 250,
  cachedInputTokens: 600,
  costUsd: 0.42,
  errors: 1,
  firstSeen: "2026-07-15T10:00:00Z",
  lastSeen: "2026-07-15T11:00:00Z",
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
      data: {
        session,
        byModel: [
          {
            model: "claude-fable-5",
            requests: 3,
            inputTokens: 700,
            outputTokens: 150,
            cachedInputTokens: 400,
            costUsd: 0.3,
          },
        ],
        recentRequests: [],
      },
      isLoading: false,
      error: null,
    });
  });

  it("renders session usage and defaults to the three-day window", () => {
    renderPage();

    expect(
      screen.getByRole("heading", { name: "Sessions" }),
    ).toBeInTheDocument();
    expect(screen.getByText("claude-code")).toBeInTheDocument();
    expect(screen.getByText("claude-fable-5")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.6-sol-medium")).toBeInTheDocument();
    expect(screen.getByText("1.25K (600 Cached)")).toBeInTheDocument();
    expect(screen.getByText("$0.42")).toBeInTheDocument();
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

  it("changes the window and opens session detail from a row", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: "6h" }));
    expect(useSessionsMock).toHaveBeenLastCalledWith({
      windowMinutes: 360,
      limit: 25,
      offset: 0,
    });

    await user.click(
      screen.getByRole("row", {
        name: `View session ${session.sessionId}`,
      }),
    );
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(session.sessionId)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Usage by model" }),
    ).toBeInTheDocument();
  });

  it("opens the session detail from the session query parameter", () => {
    renderPage(`/sessions?session=${encodeURIComponent(session.sessionId)}`);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(session.sessionId)).toBeInTheDocument();
    expect(useSessionMock).toHaveBeenLastCalledWith(session.sessionId);
  });

  it("opens detail for an unknown session id not present in the list", () => {
    const unknownSessionId = "session-not-in-current-window";
    renderPage(`/sessions?session=${encodeURIComponent(unknownSessionId)}`);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(unknownSessionId)).toBeInTheDocument();
    expect(useSessionMock).toHaveBeenLastCalledWith(unknownSessionId);
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
