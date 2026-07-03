import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Shown only for accounts this instance does not own (federation mirrors).
 * Locally-owned accounts (the default, single-instance case) render nothing.
 * Monochrome outline badge, matching ProviderBadge (DESIGN.md §Components).
 */
export function OwnerInstanceBadge({
  ownerInstance,
  isLocallyOwned,
  className,
}: {
  ownerInstance?: string | null;
  isLocallyOwned?: boolean;
  className?: string;
}) {
  if (isLocallyOwned !== false) return null;
  return (
    <Badge
      variant="outline"
      className={cn("text-muted-foreground", className)}
      title={
        ownerInstance
          ? `OAuth refresh owned by instance "${ownerInstance}"`
          : "OAuth refresh owned by another instance"
      }
    >
      {ownerInstance ? `mirror: ${ownerInstance}` : "mirror"}
    </Badge>
  );
}
