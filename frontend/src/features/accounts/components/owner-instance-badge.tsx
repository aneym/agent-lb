type OwnerInstanceBadgeProps = {
  ownerInstance?: string | null;
  isLocallyOwned?: boolean;
};

export function OwnerInstanceBadge({
  ownerInstance,
  isLocallyOwned,
}: OwnerInstanceBadgeProps) {
  const label = isLocallyOwned ? "Local" : (ownerInstance ?? "Local");

  return (
    <span
      className="inline-flex min-w-0 max-w-full rounded border px-1.5 py-0.5 text-xs text-muted-foreground"
      title={`Owner instance: ${label}`}
    >
      <span className="truncate">Owner: {label}</span>
    </span>
  );
}
