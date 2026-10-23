import { useState } from "react";
import { ResetCreditConfirmDialog } from "@/features/accounts/components/reset-credit-confirm-dialog";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import type { AccountSummary } from "@/features/accounts/schemas";
import { formatDateTimeInline } from "@/utils/formatters";

export function AccountResetCredits({ account }: { account: AccountSummary }) {
  const [open, setOpen] = useState(false);
  const canWrite = useAuthStore((state) => state.canWrite);
  if (account.provider !== "openai") return null;
  const count = account.availableResetCredits ?? 0;
  return (
    <section className="card">
      <h2>Reset credits</h2>
      <div className="account-setting">
        <span>Available</span>
        <span>{count}</span>
      </div>
      {account.resetCreditNearestExpiresAt && (
        <div className="account-setting">
          <span>Next expiry</span>
          <span>{formatDateTimeInline(account.resetCreditNearestExpiresAt)}</span>
        </div>
      )}
      <button className="btn sm" disabled={!canWrite} onClick={() => setOpen(true)}>
        Review and redeem
      </button>
      <ResetCreditConfirmDialog
        open={open}
        onOpenChange={setOpen}
        accountId={account.accountId}
        summaryAvailableCount={count}
      />
    </section>
  );
}
