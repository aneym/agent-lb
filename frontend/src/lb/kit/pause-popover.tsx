import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { pauseAccount, reactivateAccount } from "@/features/accounts/api";
import { useStickySessions, setResumeAt } from "../api";
import { dayTime } from "../format";

export function PausePopover({
  accountId,
  pool,
  fiveHourResetAt,
  weeklyResetAt,
  onClose,
}: {
  accountId: string;
  pool: string;
  fiveHourResetAt?: string | null;
  weeklyResetAt?: string | null;
  onClose: () => void;
}) {
  const [choice, setChoice] = useState<string | null>(fiveHourResetAt || weeklyResetAt || null);
  const [busy, setBusy] = useState(false);
  const sessions = useStickySessions().data;
  // Sticky entries do not expose an account ID in all server versions; never guess a per-account count.
  const count = sessions?.entries.filter((entry) => entry.key === accountId).length;
  const qc = useQueryClient();
  async function apply() {
    setBusy(true);
    try {
      await pauseAccount(accountId);
      try {
        await setResumeAt(accountId, choice);
      } catch (error) {
        await reactivateAccount(accountId);
        throw error;
      }
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["accounts"] }),
        qc.invalidateQueries({ queryKey: ["lb", "pools"] }),
      ]);
      toast("Account paused", {
        duration: 5000,
        action: {
          label: "Undo",
          onClick: () => {
            void reactivateAccount(accountId)
              .then(() => {
                void setResumeAt(accountId, null);
                void qc.invalidateQueries({ queryKey: ["accounts"] });
                void qc.invalidateQueries({ queryKey: ["lb", "pools"] });
              })
              .catch(() => toast.error("Could not resume account"));
          },
        },
      });
      onClose();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not pause account");
    } finally {
      setBusy(false);
    }
  }
  const choices = [
    { reset: fiveHourResetAt, title: "Until the 5-hour reset" },
    { reset: weeklyResetAt, title: "Until the weekly reset" },
    { reset: null, title: "Until I resume" },
  ].filter((option, i) => i === 2 || !!option.reset);
  return (
    <div className="pop" role="dialog" aria-label="Pause account">
      <h3>Pause account</h3>
      {choices.map(({ reset, title }) => (
        <div
          key={title}
          className={`opt ${choice === reset ? "on" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => setChoice(reset ?? null)}
          onKeyDown={(e) => {
            if (e.key === "Enter") setChoice(reset ?? null);
          }}
        >
          <span className="rd" />
          <span>
            {title}
            {reset && <div className="os">{dayTime(reset)}</div>}
          </span>
        </div>
      ))}
      <p className="note">
        {count ? `${count} sticky sessions move` : "Sticky sessions move"} to another account in{" "}
        {pool} on their next request.
      </p>
      <div className="toolbar">
        <button className="btn primary" disabled={busy} onClick={() => void apply()}>
          Pause account
        </button>
        <button className="btn ghost" onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}
