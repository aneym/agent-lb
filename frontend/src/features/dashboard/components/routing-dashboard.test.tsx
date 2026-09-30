import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { poolRows, type AccountPlan, type DashboardPool } from "../pool-rows";
import { RoutingDashboard } from "./routing-dashboard";

const now = new Date("2026-09-30T14:31:00Z");
const timeZone = "America/New_York";

const pools: DashboardPool[] = [
  {
    id: "anthropic-general",
    provider: "anthropic",
    accounts: 8,
    eligibleAccounts: 3,
    status: "ok",
    weeklyRemainingPercent: 28.4,
    usableAccounts: 3,
    totalAccounts: 8,
    weeklyResetAt: "2026-10-07T12:00:00Z",
    refills: [{ at: "2026-09-30T16:59:00Z", accounts: 1, remainingPercent: 40 }],
  },
  {
    id: "anthropic-fable",
    provider: "anthropic",
    accounts: 1,
    eligibleAccounts: 1,
    status: "ok",
    weeklyRemainingPercent: 4,
    usableAccounts: 1,
    totalAccounts: 1,
    weeklyResetAt: "2026-10-07T12:00:00Z",
  },
  {
    id: "openai-codex",
    provider: "openai",
    accounts: 5,
    eligibleAccounts: 4,
    status: "ok",
    aggregateRemainingPercent: 16.6,
    usableAccounts: 4,
    totalAccounts: 5,
    resetAt: "2026-10-06T22:05:00Z",
    refills: [{ at: "2026-10-03T16:58:00Z", accounts: 3, remainingPercent: 50 }],
  },
  {
    id: "cursor-models",
    provider: "cursor",
    accounts: 1,
    eligibleAccounts: 1,
    status: "ok",
    windowLabel: "month",
    percentUsed: 2,
    percentSource: "estimate",
    cycleResetAt: "2026-10-30T04:00:00Z",
  },
  {
    id: "cursor-other",
    provider: "cursor",
    accounts: 1,
    eligibleAccounts: 0,
    status: "exhausted",
    windowLabel: "month",
    percentUsed: 100,
    percentSource: "vendor",
    cycleResetAt: "2026-10-30T04:00:00Z",
  },
  {
    id: "devin",
    provider: "devin",
    accounts: 2,
    eligibleAccounts: 1,
    status: "ok",
    weeklyRemainingPercent: 50,
    usableAccounts: 1,
    totalAccounts: 2,
  },
];

const plan: AccountPlan = {
  generatedAt: "2026-09-30T14:31:00Z",
  recommended: "balanced",
  levels: {
    budget: {
      title: "Budget",
      why: "Spend least while keeping review quality.",
      accounts: [
        { pool: "anthropic", label: "Claude", count: 8, change: "keep", delta: 0, reason: "Keep. Claude reviews and decides." },
        { pool: "openai", label: "OpenAI", count: 5, change: "drop", delta: -2, reason: "Drop 2. Sol keeps reviews and reading." },
      ],
      ladders: {
        implement: [{ id: "grok", model: "Grok 4.7 low", harness: "Cursor CLI" }],
        mechanical: [{ id: "composer", model: "Composer 2.5", harness: "Cursor CLI" }],
        explore: [{ id: "sol", model: "GPT-6.1 Sol low", harness: "Codex CLI" }],
        review: [
          { id: "sonnet", model: "Claude Sonnet 5.5 for GPT and Grok work", harness: "Claude Code" },
          { id: "sol-review", model: "GPT-6.1 Sol for Claude work", harness: "Codex CLI" },
        ],
      },
      risk: "When Cursor refuses, code falls to Claude Sonnet.",
    },
    balanced: {
      title: "Balanced",
      why: "Same accounts as today.",
      accounts: [
        { pool: "cursor", label: "Cursor Ultra", count: 1, change: "keep", delta: 0, reason: "Flag a second plan if Cursor models passes 60% before day 20." },
      ],
      ladders: {
        implement: [{ id: "grok", model: "Grok 4.7 low", harness: "Cursor CLI" }],
        mechanical: [{ id: "composer", model: "Composer 2.5", harness: "Cursor CLI" }],
        explore: [{ id: "sol", model: "GPT-6.1 Sol low", harness: "Codex CLI" }],
        review: [
          { id: "sonnet", model: "Claude Sonnet 5.5 for GPT and Grok work", harness: "Claude Code" },
          { id: "sol-review", model: "GPT-6.1 Sol for Claude work", harness: "Codex CLI" },
        ],
      },
      risk: "Grok 4.7 low first rests on six units.",
    },
    unlimited: {
      title: "Unlimited",
      why: "Buy Claude until it never runs dry.",
      accounts: [
        { pool: "anthropic", label: "Claude", count: 8, change: "add", delta: 5, reason: "Add until no week runs dry." },
      ],
      ladders: {
        implement: [{ id: "grok", model: "Grok 4.7 low", harness: "Cursor CLI" }],
        mechanical: [{ id: "composer", model: "Composer 2.5", harness: "Cursor CLI" }],
        explore: [{ id: "sol", model: "GPT-6.1 Sol low", harness: "Codex CLI" }],
        review: [
          { id: "opus", model: "Claude Opus on every piece", harness: "Claude Code" },
          { id: "sol-review", model: "GPT-6.1 Sol for Claude work", harness: "Codex CLI" },
        ],
      },
      risk: "Opus is for judgment, not every line of code.",
    },
  },
};

function renderDashboard(nextPlan: AccountPlan | null) {
  return render(
    <div className="lb">
      <RoutingDashboard pools={pools} plan={nextPlan} now={now} timeZone={timeZone} />
    </div>,
  );
}

describe("routing dashboard", () => {
  it("renders pool rows and the recommended plan", async () => {
    const user = userEvent.setup();
    renderDashboard(plan);
    expect(screen.getByRole("heading", { name: "Account plan" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Balanced/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/recommended/)).toBeInTheDocument();
    expect(screen.getByText("Same accounts as today.")).toBeInTheDocument();
    expect(screen.getByText("keep")).toBeInTheDocument();
    expect(screen.getByText("Grok 4.7 low first rests on six units.")).toBeInTheDocument();
    expect(screen.getByText("Claude Sonnet 5.5 for GPT and Grok work")).toBeInTheDocument();
    expect(screen.queryByText("→", { exact: false })).not.toBeNull();

    expect(screen.getByText("Claude")).toBeInTheDocument();
    expect(screen.getByText("28% · 3/8")).toBeInTheDocument();
    expect(screen.getByText("+1 at 12:59 PM")).toBeInTheDocument();
    expect(screen.getByText("17% · 4/5")).toBeInTheDocument();
    expect(screen.getByText("98% · 1/1")).toBeInTheDocument();
    expect(screen.getByText("estimate · resets Oct 30")).toBeInTheDocument();
    expect(screen.getByText("out")).toBeInTheDocument();
    expect(screen.getByText("50% · 1/2")).toBeInTheDocument();
    expect(screen.getByText("Code → Grok 4.7 low · Edits → Composer 2.5")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Budget" }));
    expect(screen.getByText("drop")).toBeInTheDocument();
    expect(screen.getByText("Spend least while keeping review quality.")).toBeInTheDocument();
  });

  it("keeps anthropic-general on the Claude row in either order", () => {
    const general = pools[0];
    const fable = pools[1];
    expect(general.id).toBe("anthropic-general");
    expect(fable.id).toBe("anthropic-fable");
    const forward = poolRows(pools, now, timeZone);
    const reversed = poolRows([fable, general, ...pools.slice(2)], now, timeZone);
    expect(forward[0]).toMatchObject({ name: "Claude", value: "28% · 3/8" });
    expect(reversed[0]).toMatchObject({ name: "Claude", value: "28% · 3/8" });
  });

  it("hides the account plan when the plan route is missing", () => {
    renderDashboard(null);
    expect(screen.queryByRole("heading", { name: "Account plan" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Pools" })).toBeInTheDocument();
    expect(screen.queryByText(/Code →/)).not.toBeInTheDocument();
  });
});
