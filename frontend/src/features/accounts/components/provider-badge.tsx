import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { AccountProvider } from "@/features/accounts/schemas";
import { ProviderIcon } from "@/features/accounts/components/provider-icon";
import { providerLabel } from "@/features/accounts/components/provider-label";

/** Monochrome provider badge: outline style, ink text (DESIGN.md §Components). */
export function ProviderBadge({
  provider,
  className,
}: {
  provider?: AccountProvider;
  className?: string;
}) {
  const normalizedProvider = provider ?? "openai";
  return (
    <Badge variant="outline" className={cn("text-foreground", className)}>
      <ProviderIcon provider={normalizedProvider} className="size-3" />
      {providerLabel(normalizedProvider)}
    </Badge>
  );
}
