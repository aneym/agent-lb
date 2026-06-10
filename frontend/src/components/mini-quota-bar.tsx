type MiniQuotaBarProps = {
  percent: number | null;
  testId: string;
  "aria-label"?: string;
};

/**
 * Monochrome mini meter (DESIGN.md §Status vocabulary): track in --muted,
 * fill in ink. Urgency is carried by the surrounding readout, never hue.
 */
export function MiniQuotaBar({
  percent,
  testId,
  "aria-label": ariaLabel,
}: MiniQuotaBarProps) {
  if (percent === null) {
    return (
      <div
        aria-hidden="true"
        data-testid={testId}
        className="h-1 flex-1 overflow-hidden rounded-full bg-muted"
      />
    );
  }
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div
      role="progressbar"
      aria-label={ariaLabel}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      data-testid={testId}
      className="h-1 flex-1 overflow-hidden rounded-full bg-muted"
    >
      <div
        data-testid={`${testId}-fill`}
        className="h-full rounded-full bg-foreground"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
