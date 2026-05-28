import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { AccountProvider } from "@/features/accounts/schemas";
import { providerLabel } from "@/features/accounts/components/provider-label";

const PROVIDER_BADGE_CLASSES: Record<AccountProvider, string> = {
  openai: "border-sky-500/20 bg-sky-500/10 text-sky-700 dark:text-sky-400",
  anthropic: "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
};

export function ProviderBadge({
  provider,
  className,
}: {
  provider?: AccountProvider;
  className?: string;
}) {
  const normalizedProvider = provider ?? "openai";
  return (
    <Badge
      variant="outline"
      className={cn(PROVIDER_BADGE_CLASSES[normalizedProvider], className)}
    >
      {providerLabel(normalizedProvider)}
    </Badge>
  );
}
