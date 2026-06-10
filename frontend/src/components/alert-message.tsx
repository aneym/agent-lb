import { Check, TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

export type AlertMessageProps = {
  variant: "error" | "success" | "warning";
  className?: string;
  children: React.ReactNode;
};

/**
 * Monochrome alert (DESIGN.md): severity is carried by icon + weight, never hue.
 * All variants share the same ink-on-muted surface.
 */
const variantStyles: Record<AlertMessageProps["variant"], string> = {
  error: "border bg-muted text-foreground font-semibold",
  success: "border bg-muted text-foreground",
  warning: "border bg-muted text-foreground",
};

const variantIcons: Record<AlertMessageProps["variant"], React.ElementType> = {
  error: TriangleAlert,
  success: Check,
  warning: TriangleAlert,
};

export function AlertMessage({
  variant,
  className,
  children,
}: AlertMessageProps) {
  const Icon = variantIcons[variant];
  return (
    <div
      className={cn(
        "flex items-start gap-2.5 rounded-lg px-3 py-2 text-xs font-medium",
        variantStyles[variant],
        className,
      )}
    >
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{children}</span>
    </div>
  );
}
