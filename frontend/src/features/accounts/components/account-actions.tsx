import {
  Activity,
  Download,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Trash2,
  Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  AccountRoutingPolicy,
  AccountSummary,
} from "@/features/accounts/schemas";

export type AccountActionsProps = {
  account: AccountSummary;
  busy: boolean;
  onPause: (accountId: string) => void;
  onResume: (accountId: string) => void;
  onProbe: (accountId: string) => void;
  onRedeemResetCredit?: (accountId: string) => void;
  onDelete: (accountId: string) => void;
  onReauth: () => void;
  onExportAuth: (accountId: string) => void;
  onLimitWarmupChange: (accountId: string, enabled: boolean) => void;
  onRoutingPolicyChange: (
    accountId: string,
    routingPolicy: AccountRoutingPolicy,
  ) => void;
};

/**
 * Header actions toolbar: primary operations live next to the identity
 * block so the operator never has to scroll to act.
 */
export function AccountActions({
  account,
  busy,
  onPause,
  onResume,
  onProbe,
  onRedeemResetCredit,
  onDelete,
  onReauth,
  onExportAuth,
  onLimitWarmupChange,
  onRoutingPolicyChange,
}: AccountActionsProps) {
  const showOperatorRecoveryAction =
    account.status === "reauth_required" || account.status === "deactivated";
  const probeDisabled =
    busy || account.status === "paused" || showOperatorRecoveryAction;
  // Banked reset credits are an OpenAI/Codex feature; the count comes from
  // the accounts payload (null until the service has listed it upstream).
  const bankedResetCredits = account.resetCreditsAvailable ?? 0;
  const showResetLimits =
    !!onRedeemResetCredit &&
    account.provider === "openai" &&
    !showOperatorRecoveryAction &&
    account.status !== "paused";
  const resetLimitsDisabled = busy || bankedResetCredits < 1;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {account.status === "paused" ? (
        <Button
          type="button"
          size="sm"
          className="h-8 gap-1.5 text-xs"
          onClick={() => onResume(account.accountId)}
          disabled={busy}
        >
          <Play className="h-3.5 w-3.5" />
          Resume
        </Button>
      ) : showOperatorRecoveryAction ? null : (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 text-xs"
          onClick={() => onPause(account.accountId)}
          disabled={busy}
        >
          <Pause className="h-3.5 w-3.5" />
          Pause
        </Button>
      )}

      {showOperatorRecoveryAction ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 text-xs"
          onClick={onReauth}
          disabled={busy}
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Re-authenticate
        </Button>
      ) : null}

      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-8 gap-1.5 text-xs"
        onClick={() => onProbe(account.accountId)}
        disabled={probeDisabled}
      >
        <Activity className="h-3.5 w-3.5" />
        Force probe
      </Button>

      {showResetLimits ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 text-xs"
          onClick={() => onRedeemResetCredit?.(account.accountId)}
          disabled={resetLimitsDisabled}
          title={
            bankedResetCredits < 1
              ? "No banked reset credits on this account"
              : "Spend one banked credit to reset this account's rate limits now"
          }
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Reset limits ({bankedResetCredits})
        </Button>
      ) : null}

      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-8 gap-1.5 text-xs"
        onClick={() =>
          onLimitWarmupChange(account.accountId, !account.limitWarmupEnabled)
        }
        disabled={busy}
      >
        <Zap className="h-3.5 w-3.5" />
        {account.limitWarmupEnabled ? "Disable warm-up" : "Enable warm-up"}
      </Button>

      {!showOperatorRecoveryAction ? (
        <Select
          value={account.routingPolicy ?? "normal"}
          onValueChange={(value) =>
            onRoutingPolicyChange(
              account.accountId,
              value as AccountRoutingPolicy,
            )
          }
          disabled={busy}
        >
          <SelectTrigger
            aria-label="Routing policy"
            size="sm"
            className="h-8 w-32 text-xs"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="burn_first">Burn first</SelectItem>
            <SelectItem value="normal">Normal</SelectItem>
            <SelectItem value="preserve">Preserve</SelectItem>
          </SelectContent>
        </Select>
      ) : null}

      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-8 gap-1.5 text-xs"
        onClick={() => onExportAuth(account.accountId)}
        disabled={busy}
      >
        <Download className="h-3.5 w-3.5" />
        Export
      </Button>

      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-8 gap-1.5 text-xs"
        onClick={() => onDelete(account.accountId)}
        disabled={busy}
      >
        <Trash2 className="h-3.5 w-3.5" />
        Delete
      </Button>
    </div>
  );
}
