import { TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

export type MonoMeterProps = {
  /** Percent remaining/complete, 0–100. */
  percent: number;
  label?: string;
  sublabel?: string;
  /** Below this percent the readout turns semibold and gains a warning glyph. */
  warnBelow?: number;
  className?: string;
};

/**
 * Monochrome meter (DESIGN.md §Status vocabulary).
 * 4px track in --muted, fill in ink. Below `warnBelow` the percentage turns
 * semibold and gains a TriangleAlert; the bar itself stays ink — never hue.
 */
export function MonoMeter({
  percent,
  label,
  sublabel,
  warnBelow = 20,
  className,
}: MonoMeterProps) {
  const clamped = Math.min(100, Math.max(0, percent));
  const warning = percent < warnBelow;

  return (
    <div className={cn("min-w-0", className)}>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        {label ? (
          <span className="truncate text-[13px] font-medium leading-none">
            {label}
          </span>
        ) : (
          <span aria-hidden="true" />
        )}
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1 text-right font-mono text-xs leading-none tabular-nums",
            warning && "font-semibold",
          )}
        >
          {warning && (
            <TriangleAlert size={12} aria-hidden="true" className="shrink-0" />
          )}
          {Math.round(clamped)}%
        </span>
      </div>
      <div
        role="progressbar"
        aria-valuenow={Math.round(clamped)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label ?? "Meter"}
        className="h-1 w-full overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full rounded-full bg-foreground transition-[width] duration-200 ease-out motion-reduce:transition-none"
          style={{ width: `${clamped}%` }}
        />
      </div>
      {sublabel ? (
        <p className="mt-1 truncate text-xs leading-none text-muted-foreground">
          {sublabel}
        </p>
      ) : null}
    </div>
  );
}
