type OwnerInstanceBadgeProps = {
  ownerInstance?: string | null;
};

export function OwnerInstanceBadge({ ownerInstance }: OwnerInstanceBadgeProps) {
  const label = ownerInstance ?? "Local";

  return (
    <span
      className="inline-flex min-w-0 max-w-full rounded border px-1.5 py-0.5 text-xs text-muted-foreground"
      title={`Owner instance: ${label}`}
    >
      <span className="truncate">Owner: {label}</span>
    </span>
  );
}
