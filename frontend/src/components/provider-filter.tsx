import {
  PROVIDER_FILTER_OPTIONS,
  type ProviderFilterValue,
} from "@/components/provider-filter-options";
import { cn } from "@/lib/utils";

export type { ProviderFilterValue };

export type ProviderFilterProps = {
  value: ProviderFilterValue;
  /** Live counts per segment; segments render without counts when omitted. */
  counts?: Record<ProviderFilterValue, number>;
  onChange: (value: ProviderFilterValue) => void;
  className?: string;
  "aria-label"?: string;
};

/**
 * Monochrome segmented provider control (All / Codex / Claude) shared by the
 * accounts toolbar and the dashboard header. Active segment is an ink fill;
 * inactive segments are muted with an accent hover (DESIGN.md §Components).
 */
export function ProviderFilter({
  value,
  counts,
  onChange,
  className,
  "aria-label": ariaLabel = "Filter by provider",
}: ProviderFilterProps) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn(
        "grid grid-cols-3 gap-0.5 rounded-md border bg-background p-0.5",
        className,
      )}
    >
      {PROVIDER_FILTER_OPTIONS.map((option) => {
        const active = value === option.value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex h-7 min-w-0 items-center justify-center gap-1.5 rounded-sm px-2 text-xs transition-colors duration-150 ease-out outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none",
              active
                ? "bg-primary font-medium text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
          >
            <span className="truncate">{option.label}</span>
            {counts ? (
              <span className="font-mono tabular-nums">
                {counts[option.value]}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
