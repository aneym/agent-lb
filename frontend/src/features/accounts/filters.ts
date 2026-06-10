import type {
  AccountProvider,
  AccountSummary,
} from "@/features/accounts/schemas";
import {
  DEFAULT_ACCOUNT_SORT_MODE,
  type AccountSortMode,
} from "@/features/accounts/sorting";

export type AccountProviderFilter = "all" | AccountProvider;

export const ACCOUNT_STATUS_FILTER_VALUES = [
  "active",
  "paused",
  "rate_limited",
  "quota_exceeded",
  "reauth_required",
  "deactivated",
] as const;

export type AccountStatusFilter =
  | "all"
  | (typeof ACCOUNT_STATUS_FILTER_VALUES)[number];

export type AccountGroupMode = "provider" | "status" | "none";

export type AccountFilterState = {
  provider: AccountProviderFilter;
  status: AccountStatusFilter;
  query: string;
  sort: AccountSortMode;
  group: AccountGroupMode;
};

export const DEFAULT_ACCOUNT_FILTERS: AccountFilterState = {
  provider: "all",
  status: "all",
  query: "",
  sort: DEFAULT_ACCOUNT_SORT_MODE,
  group: "provider",
};

const SORT_MODES: readonly AccountSortMode[] = [
  "reset_soonest",
  "reset_latest",
  "subscription_soonest",
  "name_asc",
  "name_desc",
];

function parseProvider(value: string | null): AccountProviderFilter {
  return value === "openai" || value === "anthropic" ? value : "all";
}

function parseStatus(value: string | null): AccountStatusFilter {
  return value &&
    (ACCOUNT_STATUS_FILTER_VALUES as readonly string[]).includes(value)
    ? (value as AccountStatusFilter)
    : "all";
}

function parseSort(value: string | null): AccountSortMode {
  return value && (SORT_MODES as readonly string[]).includes(value)
    ? (value as AccountSortMode)
    : DEFAULT_ACCOUNT_SORT_MODE;
}

function parseGroup(value: string | null): AccountGroupMode {
  return value === "status" || value === "none" ? value : "provider";
}

/** Read filter state from URL search params; unknown values fall back to defaults. */
export function readAccountFilters(
  params: URLSearchParams,
): AccountFilterState {
  return {
    provider: parseProvider(params.get("provider")),
    status: parseStatus(params.get("status")),
    query: params.get("q") ?? "",
    sort: parseSort(params.get("sort")),
    group: parseGroup(params.get("group")),
  };
}

/**
 * Write filter state into a copy of the given search params.
 * Defaults are omitted to keep URLs minimal; unrelated params
 * (e.g. `selected`) are preserved.
 */
export function writeAccountFilters(
  params: URLSearchParams,
  filters: AccountFilterState,
): URLSearchParams {
  const next = new URLSearchParams(params);
  const entries: ReadonlyArray<[string, string, string]> = [
    ["provider", filters.provider, DEFAULT_ACCOUNT_FILTERS.provider],
    ["status", filters.status, DEFAULT_ACCOUNT_FILTERS.status],
    ["q", filters.query, DEFAULT_ACCOUNT_FILTERS.query],
    ["sort", filters.sort, DEFAULT_ACCOUNT_FILTERS.sort],
    ["group", filters.group, DEFAULT_ACCOUNT_FILTERS.group],
  ];
  for (const [key, value, defaultValue] of entries) {
    if (value === defaultValue) {
      next.delete(key);
    } else {
      next.set(key, value);
    }
  }
  return next;
}

export function hasActiveAccountFilters(filters: AccountFilterState): boolean {
  return (
    filters.provider !== "all" ||
    filters.status !== "all" ||
    filters.query.trim() !== ""
  );
}

export function accountMatchesSearch(
  account: AccountSummary,
  query: string,
): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) {
    return true;
  }
  return (
    account.email.toLowerCase().includes(needle) ||
    (account.alias?.toLowerCase().includes(needle) ?? false) ||
    account.displayName.toLowerCase().includes(needle) ||
    account.accountId.toLowerCase().includes(needle) ||
    account.planType.toLowerCase().includes(needle) ||
    (account.provider ?? "openai").toLowerCase().includes(needle)
  );
}

/** Provider AND status AND search. */
export function filterAccounts(
  accounts: AccountSummary[],
  filters: AccountFilterState,
): AccountSummary[] {
  return accounts.filter((account) => {
    if (
      filters.provider !== "all" &&
      (account.provider ?? "openai") !== filters.provider
    ) {
      return false;
    }
    if (filters.status !== "all" && account.status !== filters.status) {
      return false;
    }
    return accountMatchesSearch(account, filters.query);
  });
}

export const PROVIDER_GROUP_ORDER: readonly AccountProvider[] = [
  "openai",
  "anthropic",
];

/** Problems first when grouping by status. */
export const STATUS_GROUP_ORDER: readonly string[] = [
  "reauth_required",
  "quota_exceeded",
  "rate_limited",
  "paused",
  "active",
  "deactivated",
];

export type AccountGroup = {
  /** Stable key: provider id, status id, or "all". */
  id: string;
  kind: AccountGroupMode;
  accounts: AccountSummary[];
};

/** Group an already filtered + sorted list; preserves in-group order. */
export function groupAccounts(
  accounts: AccountSummary[],
  group: AccountGroupMode,
): AccountGroup[] {
  if (group === "none") {
    return accounts.length > 0 ? [{ id: "all", kind: "none", accounts }] : [];
  }

  if (group === "provider") {
    return PROVIDER_GROUP_ORDER.map((provider) => ({
      id: provider,
      kind: "provider" as const,
      accounts: accounts.filter(
        (account) => (account.provider ?? "openai") === provider,
      ),
    })).filter((entry) => entry.accounts.length > 0);
  }

  const knownOrder = STATUS_GROUP_ORDER.filter((status) =>
    accounts.some((account) => account.status === status),
  );
  const unknown = Array.from(
    new Set(
      accounts
        .map((account) => account.status)
        .filter((status) => !STATUS_GROUP_ORDER.includes(status)),
    ),
  );
  return [...knownOrder, ...unknown].map((status) => ({
    id: status,
    kind: "status" as const,
    accounts: accounts.filter((account) => account.status === status),
  }));
}

/**
 * Median weekly-window remaining percent for a group (monthly window as
 * fallback for monthly-only plans). Null when no account reports one.
 */
export function medianWeeklyRemaining(
  accounts: AccountSummary[],
): number | null {
  const values = accounts
    .map(
      (account) =>
        account.usage?.secondaryRemainingPercent ??
        account.usage?.monthlyRemainingPercent ??
        null,
    )
    .filter((value): value is number => value !== null)
    .sort((left, right) => left - right);
  if (values.length === 0) {
    return null;
  }
  const middle = Math.floor(values.length / 2);
  return values.length % 2 === 1
    ? values[middle]
    : (values[middle - 1] + values[middle]) / 2;
}
