import type { AccountSummary } from "@/features/accounts/schemas";
import {
  formatAccessTokenLabel,
  formatIdTokenLabel,
  formatRefreshTokenLabel,
} from "@/utils/formatters";

export type AccountTokenInfoProps = {
  account: AccountSummary;
};

/** Compact token-status rows for the Connection section. */
export function AccountTokenInfo({ account }: AccountTokenInfoProps) {
  return (
    <dl className="space-y-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <dt className="text-muted-foreground">Access token</dt>
        <dd className="font-mono font-medium tabular-nums">
          {formatAccessTokenLabel(account.auth)}
        </dd>
      </div>
      <div className="flex items-center justify-between gap-2">
        <dt className="text-muted-foreground">Refresh token</dt>
        <dd className="font-mono font-medium tabular-nums">
          {formatRefreshTokenLabel(account.auth)}
        </dd>
      </div>
      <div className="flex items-center justify-between gap-2">
        <dt className="text-muted-foreground">ID token</dt>
        <dd className="font-mono font-medium tabular-nums">
          {formatIdTokenLabel(account.auth)}
        </dd>
      </div>
    </dl>
  );
}
