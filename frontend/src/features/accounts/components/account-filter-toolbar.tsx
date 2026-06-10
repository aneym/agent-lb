import { Search } from "lucide-react";

import { ProviderFilter } from "@/components/provider-filter";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { statusLabel } from "@/components/ui/status-glyph";
import {
  ACCOUNT_STATUS_FILTER_VALUES,
  type AccountFilterState,
  type AccountGroupMode,
  type AccountProviderFilter,
} from "@/features/accounts/filters";
import {
  ACCOUNT_SORT_OPTIONS,
  type AccountSortMode,
} from "@/features/accounts/sorting";

const GROUP_OPTIONS: ReadonlyArray<{ value: AccountGroupMode; label: string }> =
  [
    { value: "provider", label: "Group: Provider" },
    { value: "status", label: "Group: Status" },
    { value: "none", label: "Group: None" },
  ];

export type AccountFilterToolbarProps = {
  filters: AccountFilterState;
  /** Live counts per provider, with the other filters applied. */
  providerCounts: Record<AccountProviderFilter, number>;
  onFiltersChange: (patch: Partial<AccountFilterState>) => void;
};

export function AccountFilterToolbar({
  filters,
  providerCounts,
  onFiltersChange,
}: AccountFilterToolbarProps) {
  return (
    <div className="space-y-2">
      <div className="relative min-w-0">
        <Search
          className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          type="search"
          placeholder="Search accounts..."
          aria-label="Search accounts"
          value={filters.query}
          onChange={(event) => onFiltersChange({ query: event.target.value })}
          className="h-8 pl-8"
        />
      </div>

      <ProviderFilter
        value={filters.provider}
        counts={providerCounts}
        onChange={(provider) => onFiltersChange({ provider })}
        aria-label="Filter accounts by provider"
      />

      <div className="grid grid-cols-2 gap-2">
        <Select
          value={filters.status}
          onValueChange={(status) =>
            onFiltersChange({ status: status as AccountFilterState["status"] })
          }
        >
          <SelectTrigger
            size="sm"
            className="w-full min-w-0"
            aria-label="Filter accounts by status"
          >
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {ACCOUNT_STATUS_FILTER_VALUES.map((status) => (
              <SelectItem key={status} value={status}>
                {statusLabel(status)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={filters.group}
          onValueChange={(group) =>
            onFiltersChange({ group: group as AccountGroupMode })
          }
        >
          <SelectTrigger
            size="sm"
            className="w-full min-w-0"
            aria-label="Group accounts"
          >
            <SelectValue placeholder="Group" />
          </SelectTrigger>
          <SelectContent>
            {GROUP_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Select
        value={filters.sort}
        onValueChange={(sort) =>
          onFiltersChange({ sort: sort as AccountSortMode })
        }
      >
        <SelectTrigger
          size="sm"
          className="w-full min-w-0"
          aria-label="Sort accounts"
        >
          <SelectValue placeholder="Sort" />
        </SelectTrigger>
        <SelectContent>
          {ACCOUNT_SORT_OPTIONS.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
