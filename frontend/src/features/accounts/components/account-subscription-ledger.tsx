import { CalendarClock, Save } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AccountSubscriptionLedger, AccountSummary } from "@/features/accounts/schemas";
import { formatCurrency, formatDateTimeInline, formatSlug, parseDate } from "@/utils/formatters";

const UNTRACKED_STATUS = "untracked";

type LedgerStatus = NonNullable<AccountSubscriptionLedger["status"]>;

const LEDGER_STATUSES = [
  "active",
  "cancel_pending",
  "pause_pending",
  "paused",
  "canceled",
] as const satisfies readonly LedgerStatus[];

const STATUS_OPTIONS = [
  UNTRACKED_STATUS,
  ...LEDGER_STATUSES,
] as const;

type SubscriptionForm = {
  status: (typeof STATUS_OPTIONS)[number];
  nextChargeAt: string;
  currentPeriodEndAt: string;
  amount: string;
  currency: string;
  lastVerifiedAt: string;
  notes: string;
};

export type AccountSubscriptionLedgerProps = {
  account: AccountSummary;
  busy: boolean;
  onSave: (accountId: string, payload: AccountSubscriptionLedger) => Promise<unknown>;
};

export function AccountSubscriptionLedgerPanel({
  account,
  busy,
  onSave,
}: AccountSubscriptionLedgerProps) {
  const initialForm = useMemo(() => formFromSubscription(account.subscription), [account.subscription]);
  const [form, setForm] = useState<SubscriptionForm>(initialForm);

  useEffect(() => {
    setForm(initialForm);
  }, [account.accountId, initialForm]);

  const subscription = account.subscription ?? null;
  const status = subscription?.status ? formatSlug(subscription.status) : "Untracked";
  const primaryDate = subscription?.currentPeriodEndAt ?? subscription?.nextChargeAt ?? null;
  const primaryDateLabel =
    subscription?.status === "cancel_pending" || subscription?.status === "canceled"
      ? "Active until"
      : "Next charge";
  const currencyLabel = subscription?.currency ?? form.currency;
  const amountLabel =
    subscription?.amount != null ? formatAmount(subscription.amount, currencyLabel) : `-- ${currencyLabel}`;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSave(account.accountId, {
      status: form.status === UNTRACKED_STATUS ? null : form.status,
      nextChargeAt: isoFromInput(form.nextChargeAt),
      currentPeriodEndAt: isoFromInput(form.currentPeriodEndAt),
      amount: form.amount.trim() ? Number(form.amount) : null,
      currency: form.currency.trim() ? form.currency.trim().toUpperCase() : null,
      lastVerifiedAt: isoFromInput(form.lastVerifiedAt),
      notes: form.notes.trim() ? form.notes.trim() : null,
    });
  }

  return (
    <form className="space-y-3 rounded-md border p-3" onSubmit={handleSubmit}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <CalendarClock className="h-4 w-4 text-muted-foreground" />
            Subscription
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>{status}</span>
            <span>
              {primaryDateLabel}: {formatDateTimeInline(primaryDate)}
            </span>
            <span>{amountLabel}</span>
          </div>
        </div>
        <Button type="submit" size="sm" className="h-8 gap-1.5 text-xs" disabled={busy}>
          <Save className="h-3.5 w-3.5" />
          Save
        </Button>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Status">
          <Select
            value={form.status}
            onValueChange={(status) =>
              setForm((current) => ({
                ...current,
                status: status as SubscriptionForm["status"],
              }))
            }
            disabled={busy}
          >
            <SelectTrigger size="sm" aria-label="Subscription status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((option) => (
                <SelectItem key={option} value={option}>
                  {option === UNTRACKED_STATUS ? "Untracked" : formatSlug(option)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label="Amount">
          <div className="grid grid-cols-[minmax(0,1fr)_4.75rem] gap-2">
            <Input
              type="number"
              min="0"
              step="0.01"
              value={form.amount}
              onChange={(event) => setForm((current) => ({ ...current, amount: event.target.value }))}
              disabled={busy}
            />
            <Input
              value={form.currency}
              maxLength={3}
              onChange={(event) => setForm((current) => ({ ...current, currency: event.target.value.toUpperCase() }))}
              disabled={busy}
            />
          </div>
        </Field>
        <Field label="Next charge">
          <Input
            type="datetime-local"
            value={form.nextChargeAt}
            onChange={(event) => setForm((current) => ({ ...current, nextChargeAt: event.target.value }))}
            disabled={busy}
          />
        </Field>
        <Field label="Active until">
          <Input
            type="datetime-local"
            value={form.currentPeriodEndAt}
            onChange={(event) => setForm((current) => ({ ...current, currentPeriodEndAt: event.target.value }))}
            disabled={busy}
          />
        </Field>
        <Field label="Verified">
          <Input
            type="datetime-local"
            value={form.lastVerifiedAt}
            onChange={(event) => setForm((current) => ({ ...current, lastVerifiedAt: event.target.value }))}
            disabled={busy}
          />
        </Field>
        <Field label="Notes">
          <Input
            value={form.notes}
            onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))}
            disabled={busy}
          />
        </Field>
      </div>
    </form>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}

function formFromSubscription(subscription: AccountSubscriptionLedger | null | undefined): SubscriptionForm {
  return {
    status: subscription?.status ?? UNTRACKED_STATUS,
    nextChargeAt: inputFromIso(subscription?.nextChargeAt),
    currentPeriodEndAt: inputFromIso(subscription?.currentPeriodEndAt),
    amount: subscription?.amount != null ? String(subscription.amount) : "",
    currency: subscription?.currency ?? "USD",
    lastVerifiedAt: inputFromIso(subscription?.lastVerifiedAt),
    notes: subscription?.notes ?? "",
  };
}

function formatAmount(amount: number, currency: string | null | undefined): string {
  if (!currency || currency === "USD") {
    return formatCurrency(amount);
  }
  return `${amount.toFixed(2)} ${currency}`;
}

function inputFromIso(iso: string | null | undefined): string {
  const date = parseDate(iso);
  if (!date) {
    return "";
  }
  const localDate = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return localDate.toISOString().slice(0, 16);
}

function isoFromInput(value: string): string | null {
  if (!value.trim()) {
    return null;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}
