export type PoolRefill = {
  at: string;
  accounts: number;
  remainingPercent: number;
};

export type DashboardPool = {
  id: string;
  provider: string;
  accounts: number;
  eligibleAccounts: number;
  status: string;
  windowLabel?: string | null;
  percentUsed?: number | null;
  percentSource?: string | null;
  cycleResetAt?: string | null;
  weeklyRemainingPercent?: number | null;
  aggregateRemainingPercent?: number | null;
  weeklyResetAt?: string | null;
  resetAt?: string | null;
  refills?: PoolRefill[] | null;
  usableAccounts?: number | null;
  totalAccounts?: number | null;
};

export type PoolTone = "success" | "warning" | "danger";

export type PoolRow = {
  name: string;
  value: string;
  fraction: number;
  tone: PoolTone;
  ticks: number[];
  subline: string | null;
};

const ORDER = ["anthropic-general", "openai-codex", "cursor-models", "cursor-other", "devin"] as const;

const NAMES: Record<(typeof ORDER)[number], string> = {
  "anthropic-general": "Claude",
  "openai-codex": "OpenAI",
  "cursor-models": "Cursor models",
  "cursor-other": "Cursor other",
  devin: "Devin",
};

function canonicalId(pool: DashboardPool): (typeof ORDER)[number] | null {
  if ((ORDER as readonly string[]).includes(pool.id)) return pool.id as (typeof ORDER)[number];
  if (pool.provider === "anthropic") return "anthropic-general";
  if (pool.provider === "openai") return "openai-codex";
  if (pool.provider === "devin") return "devin";
  return null;
}

function remainingPercent(pool: DashboardPool): number | null {
  if (pool.provider === "cursor" && pool.percentUsed != null) return Math.max(0, 100 - pool.percentUsed);
  return pool.weeklyRemainingPercent ?? pool.aggregateRemainingPercent ?? null;
}

function isOut(pool: DashboardPool, remaining: number | null): boolean {
  if (pool.status === "exhausted" || pool.status === "out") return true;
  if (pool.percentUsed != null && pool.percentUsed >= 100) return true;
  if (remaining != null && remaining <= 0) return true;
  return false;
}

function tone(remaining: number | null, out: boolean): PoolTone {
  if (out || remaining == null) return "danger";
  if (remaining >= 40) return "success";
  if (remaining >= 10) return "warning";
  return "danger";
}

function ticks(pool: DashboardPool): number[] {
  const endIso = pool.weeklyResetAt || pool.resetAt || pool.cycleResetAt;
  if (!endIso || !pool.refills?.length) return [];
  const end = Date.parse(endIso);
  const days = pool.windowLabel === "month" ? 30 : 7;
  const start = end - days * 86_400_000;
  const span = end - start;
  if (span <= 0) return [];
  return pool.refills.map((refill) => Math.min(1, Math.max(0, (Date.parse(refill.at) - start) / span)));
}

function monthDay(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone }).format(new Date(iso));
}

function clock(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  }).format(new Date(iso));
}

function sameDay(a: string, b: Date, timeZone: string): boolean {
  const fmt = new Intl.DateTimeFormat("en-US", { year: "numeric", month: "2-digit", day: "2-digit", timeZone });
  return fmt.format(new Date(a)) === fmt.format(b);
}

function subline(pool: DashboardPool, out: boolean, now: Date, timeZone: string): string | null {
  if (pool.percentSource === "estimate") {
    const reset = pool.cycleResetAt || pool.weeklyResetAt || pool.resetAt;
    return reset ? `estimate · resets ${monthDay(reset, timeZone)}` : "estimate";
  }
  if (out && pool.provider === "cursor") {
    const reset = pool.cycleResetAt || pool.resetAt;
    return reset ? `back ${monthDay(reset, timeZone)}` : null;
  }
  const next = (pool.refills ?? [])
    .filter((refill) => Date.parse(refill.at) > now.getTime())
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at))[0];
  if (!next) return null;
  const time = clock(next.at, timeZone);
  if (sameDay(next.at, now, timeZone)) return `+${next.accounts} at ${time}`;
  return `+${next.accounts} ${monthDay(next.at, timeZone)}, ${time}`;
}

export function poolRows(pools: DashboardPool[], now: Date, timeZone: string): PoolRow[] {
  const byId = new Map<(typeof ORDER)[number], DashboardPool>();
  for (const pool of pools) {
    const id = canonicalId(pool);
    if (!id) continue;
    const existing = byId.get(id);
    if (!existing || (pool.id === id && existing.id !== id)) byId.set(id, pool);
  }
  return ORDER.flatMap((id) => {
    const pool = byId.get(id);
    if (!pool) return [];
    const remaining = remainingPercent(pool);
    const out = isOut(pool, remaining);
    const usable = pool.usableAccounts ?? pool.eligibleAccounts;
    const total = pool.totalAccounts ?? pool.accounts;
    const value = out
      ? "out"
      : remaining == null
        ? `— · ${usable}/${total}`
        : `${Math.round(remaining)}% · ${usable}/${total}`;
    return [
      {
        name: NAMES[id],
        value,
        fraction: out || remaining == null ? 0 : Math.min(1, Math.max(0, remaining / 100)),
        tone: tone(remaining, out),
        ticks: ticks(pool),
        subline: subline(pool, out, now, timeZone),
      },
    ];
  });
}

export type PlanRung = { id: string; model: string; harness: string };
export type PlanAccount = {
  pool: string;
  label: string;
  count: number;
  change: "add" | "drop" | "keep";
  delta: number;
  reason: string;
};
export type PlanLevel = {
  title: string;
  why: string;
  accounts: PlanAccount[];
  ladders: {
    implement?: PlanRung[];
    mechanical?: PlanRung[];
    explore?: PlanRung[];
    review?: PlanRung[];
  };
  risk: string;
};
export type AccountPlan = {
  generatedAt: string;
  recommended: "budget" | "balanced" | "unlimited";
  levels: { budget: PlanLevel; balanced: PlanLevel; unlimited: PlanLevel };
};

export function planFooter(plan: AccountPlan | null | undefined): string | null {
  if (!plan) return null;
  const level = plan.levels[plan.recommended];
  const code = level?.ladders.implement?.[0]?.model;
  const edits = level?.ladders.mechanical?.[0]?.model;
  if (!code || !edits) return null;
  return `Code → ${code} · Edits → ${edits}`;
}
