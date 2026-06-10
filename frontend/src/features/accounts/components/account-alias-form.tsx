import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { AccountSummary } from "@/features/accounts/schemas";

export type AccountAliasFormProps = {
  account: AccountSummary;
  busy: boolean;
  onSetAlias: (accountId: string, alias: string | null) => Promise<unknown>;
};

/**
 * Inline alias editor for the detail header. Aliases exist to distinguish
 * accounts that share the same email.
 */
export function AccountAliasForm({
  account,
  busy,
  onSetAlias,
}: AccountAliasFormProps) {
  const [alias, setAlias] = useState(account.alias ?? "");

  const normalized = alias.trim();
  const storedAlias = account.alias ?? "";
  const dirty = normalized !== storedAlias;
  const canClear = storedAlias.length > 0;

  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        void onSetAlias(
          account.accountId,
          normalized === "" ? null : normalized,
        );
      }}
    >
      <Label htmlFor="account-alias" className="text-xs text-muted-foreground">
        Alias
      </Label>
      <Input
        id="account-alias"
        maxLength={255}
        placeholder="Personal Plus"
        title="Local label to distinguish accounts that share the same email"
        value={alias}
        onChange={(event) => setAlias(event.target.value)}
        disabled={busy}
        className="h-8 w-48 min-w-0 flex-1 text-xs sm:flex-none"
      />
      <Button
        type="submit"
        size="sm"
        className="h-8 shrink-0 text-xs"
        disabled={busy || !dirty}
      >
        Save alias
      </Button>
      {canClear ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 shrink-0 text-xs"
          disabled={busy}
          onClick={() => {
            setAlias("");
            void onSetAlias(account.accountId, null);
          }}
        >
          Clear alias
        </Button>
      ) : null}
    </form>
  );
}
