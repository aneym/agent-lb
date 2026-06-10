import {
  Circle,
  CircleOff,
  CirclePause,
  Clock,
  KeyRound,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Monochrome status vocabulary (DESIGN.md §Status vocabulary).
 * Status = icon shape + text label, both ink. Never color-coded.
 */
export type AccountStatus =
  | "active"
  | "paused"
  | "rate_limited"
  | "quota_exceeded"
  | "reauth_required"
  | "deactivated"
  | (string & {});

type StatusConfig = {
  icon: LucideIcon;
  label: string;
  /** Filled glyph (fill="currentColor") instead of outline. */
  filled?: boolean;
  /** Rendered in muted-foreground ink instead of full ink. */
  muted?: boolean;
  /** Label rendered semibold to carry urgency without hue. */
  semibold?: boolean;
};

const STATUS_CONFIG: Record<string, StatusConfig> = {
  active: { icon: Circle, label: "Active", filled: true },
  paused: { icon: CirclePause, label: "Paused", muted: true },
  rate_limited: { icon: Clock, label: "Rate limited" },
  quota_exceeded: {
    icon: TriangleAlert,
    label: "Quota exceeded",
    semibold: true,
  },
  reauth_required: {
    icon: KeyRound,
    label: "Re-auth required",
    semibold: true,
  },
  deactivated: { icon: CircleOff, label: "Deactivated", muted: true },
};

function fallbackLabel(status: string): string {
  const words = status.replace(/[_-]+/g, " ").trim();
  if (!words) return "Unknown";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function statusLabel(status: AccountStatus): string {
  return STATUS_CONFIG[status]?.label ?? fallbackLabel(status);
}

export type StatusGlyphProps = {
  status: AccountStatus;
  showLabel?: boolean;
  className?: string;
};

export function StatusGlyph({
  status,
  showLabel = true,
  className,
}: StatusGlyphProps) {
  const config: StatusConfig = STATUS_CONFIG[status] ?? {
    icon: Circle,
    label: fallbackLabel(status),
    muted: true,
  };
  const Icon = config.icon;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5",
        config.muted && "text-muted-foreground",
        className,
      )}
      title={showLabel ? undefined : config.label}
    >
      <Icon
        size={14}
        aria-hidden="true"
        className="shrink-0"
        {...(config.filled ? { fill: "currentColor" } : {})}
      />
      {showLabel ? (
        <span
          className={cn(
            "text-xs leading-none",
            config.semibold && "font-semibold",
          )}
        >
          {config.label}
        </span>
      ) : (
        <span className="sr-only">{config.label}</span>
      )}
    </span>
  );
}
