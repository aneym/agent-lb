export type PoolRefill = {
  at: string;
  accounts: number;
  remainingPercent: number;
};

export type DashboardPoolWindow = {
  id: string;
  kind: string;
  label: string;
  remainingPercent: number | null;
  usedPercent?: number | null;
  resetsAt: string | null;
  measuredAccounts?: number;
  source?: string | null;
};

export type DashboardPool = {
  id: string;
  provider: string;
  kind?: string;
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
  windows?: DashboardPoolWindow[] | null;
  quotaSource?: string | null;
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

const ORDER = ["anthropic-general", "openai-codex", "cursor-models", "cursor-other", "devin", "muse"];

const NAMES: Record<string, string> = {
  "anthropic-general": "Claude",
  "openai-codex": "OpenAI",
  "cursor-models": "Cursor models",
  "cursor-other": "Cursor other",
  devin: "Devin",
  muse: "Muse",
};

const SEAT_KINDS = ["cli_seat", "cli_seat_budget"];
const SEAT_PROVIDERS = ["cursor", "devin", "muse"];

function isSeat(pool: DashboardPool): boolean {
  return SEAT_KINDS.includes(pool.kind ?? "") || SEAT_PROVIDERS.includes(pool.provider);
}

function canonicalId(pool: DashboardPool): string | null {
  if (ORDER.includes(pool.id)) return pool.id;
  if (pool.provider === "anthropic") return "anthropic-general";
  if (pool.provider === "openai") return "openai-codex";
  if (isSeat(pool)) return pool.id;
  return null;
}

function poolName(id: string): string {
  return NAMES[id] ?? id.charAt(0).toUpperCase() + id.slice(1).replaceAll("-", " ");
}

function tightestWindow(pool: DashboardPool): DashboardPoolWindow | null {
  const measured = (pool.windows ?? []).filter((w) => w.remainingPercent != null);
  if (!measured.length) return null;
  return measured.reduce((low, w) => ((w.remainingPercent as number) < (low.remainingPercent as number) ? w : low));
}

function remainingPercent(pool: DashboardPool): number | null {
  const window = tightestWindow(pool);
  if (window) return window.remainingPercent;
  if (pool.windows && pool.quotaSource === "unavailable") return null;
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

const KIND_WORD: Record<string, string> = {
  session: "5-hour",
  daily: "daily",
  weekly: "weekly",
  monthly: "monthly",
};

function vendorName(provider: string): string {
  return provider.charAt(0).toUpperCase() + provider.slice(1);
}

function windowSubline(pool: DashboardPool, out: boolean, timeZone: string): string | null {
  if (pool.windows && !tightestWindow(pool) && pool.quotaSource === "unavailable") {
    return `not reported by ${vendorName(pool.provider)}`;
  }
  const window = tightestWindow(pool);
  if (!window) return null;
  const reset = window.resetsAt;
  const word = KIND_WORD[window.kind] ?? window.kind;
  if (window.source === "estimate") return reset ? `estimate · resets ${monthDay(reset, timeZone)}` : "estimate";
  if (out) return reset ? `back ${monthDay(reset, timeZone)}` : null;
  if (!reset) return word;
  return window.kind === "session" || window.kind === "daily"
    ? `${word} · resets ${clock(reset, timeZone)}`
    : `${word} · resets ${monthDay(reset, timeZone)}`;
}

function subline(pool: DashboardPool, out: boolean, now: Date, timeZone: string): string | null {
  if (pool.windows && isSeat(pool)) return windowSubline(pool, out, timeZone);
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
  const byId = new Map<string, DashboardPool>();
  const shadowed = (pool: DashboardPool) =>
    pool.id === pool.provider &&
    isSeat(pool) &&
    pools.some((other) => other.provider === pool.provider && other.id !== pool.id);
  for (const pool of pools) {
    if (shadowed(pool)) continue;
    const id = canonicalId(pool);
    if (!id) continue;
    const existing = byId.get(id);
    if (!existing || (pool.id === id && existing.id !== id)) byId.set(id, pool);
  }
  const ids = [
    ...ORDER.filter((id) => byId.has(id)),
    ...[...byId.keys()].filter((id) => !ORDER.includes(id)),
  ];
  return ids.flatMap((id) => {
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
        name: poolName(id),
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

const MODEL_LABELS: Record<string, string> = {
  "grok-4.7-low": "Grok 4.7 low",
  "grok-4.7-medium": "Grok 4.7 medium",
  "gpt-6.1-sol": "GPT-6.1 Sol",
  "composer-2.5": "Composer 2.5",
  "swe-2": "SWE-2",
  "claude-sonnet-5-5": "Sonnet 5.5",
  "claude-opus-5-5": "Opus 5.5",
};

export function modelLabel(id: string): string {
  return MODEL_LABELS[id] ?? id;
}

export function planFooter(plan: AccountPlan | null | undefined): string | null {
  if (!plan) return null;
  const level = plan.levels[plan.recommended];
  const code = level?.ladders.implement?.[0]?.model;
  const edits = level?.ladders.mechanical?.[0]?.model;
  if (!code || !edits) return null;
  return `Code → ${modelLabel(code)} · Edits → ${modelLabel(edits)}`;
}
