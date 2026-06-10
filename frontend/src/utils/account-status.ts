export type DashboardAccountStatus =
  | "active"
  | "paused"
  | "limited"
  | "exceeded"
  | "reauth"
  | "deactivated";

export function normalizeStatus(status: string): DashboardAccountStatus {
  if (status === "paused") {
    return "paused";
  }
  if (status === "rate_limited") {
    return "limited";
  }
  if (status === "quota_exceeded") {
    return "exceeded";
  }
  if (status === "reauth_required") {
    return "reauth";
  }
  if (status === "deactivated") {
    return "deactivated";
  }
  return "active";
}

function isHardBlockedStatus(status: string): boolean {
  const normalized = normalizeStatus(status);
  return (
    normalized === "paused" ||
    normalized === "reauth" ||
    normalized === "deactivated"
  );
}

export function isAccountAssignmentSelectable(status: string): boolean {
  return !isHardBlockedStatus(status);
}

export function isSingleAccountRoutingSelectable(status: string): boolean {
  return !isHardBlockedStatus(status);
}
