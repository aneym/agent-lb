import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Copy, Play, Plus, UserRound } from "lucide-react";
import { toast } from "sonner";
import { z } from "zod";

import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ApiKeyCreateDialog } from "@/features/api-keys/components/api-key-create-dialog";
import { ApiKeyCreatedDialog } from "@/features/api-keys/components/api-key-created-dialog";
import { ApiKeyEditDialog } from "@/features/api-keys/components/api-key-edit-dialog";
import { useApiKeys } from "@/features/api-keys/hooks/use-api-keys";
import {
  ApiKeySchema,
  type ApiKey,
  type ApiKeyCreateRequest,
  type ApiKeyUpdateRequest,
} from "@/features/api-keys/schemas";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { TeamMemberDrawer } from "@/features/team/components/team-member-drawer";
import { TeamOnboardingDialog } from "@/features/team/components/team-onboarding-dialog";
import { useTeamMembers } from "@/features/team/hooks/use-team";
import type {
  TeamMember,
  TeamMemberCreateRequest,
  TeamMemberUpdateRequest,
} from "@/features/team/schemas";
import { useConnectAddress } from "@/lb/api";
import { relative, duration } from "@/lb/format";
import { PageHead, RowMenu, Section, Seg } from "@/lb/kit/primitives";
import { ProviderMark } from "@/lb/kit/provider-mark";
import { providerMark } from "@/lb/kit/provider-mark-helpers";
import { get } from "@/lib/api-client";
import { getErrorMessageOrNull } from "@/utils/errors";
import "./keys.css";

const KeyWithOwnerSchema = ApiKeySchema.extend({
  memberId: z.string().nullable().optional(),
  memberName: z.string().nullable().optional(),
});
type KeyWithOwner = z.infer<typeof KeyWithOwnerSchema>;
const keysQueryKey = ["lb", "keys"];
const placeholder = "<a key from the list below>";
const dateLabel = (date: string) =>
  new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" }).format(new Date(date));
const money = (amount: number) =>
  `$${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(amount)}`;
const runKey = (key: KeyWithOwner) =>
  /^of-/i.test(key.name) ||
  (key.expiresAt !== null &&
    new Date(key.expiresAt).getTime() - new Date(key.createdAt).getTime() <= 7 * 86_400_000);

function Meter({ value, max }: { value: number; max: number }) {
  return (
    <span className="bar" role="meter" aria-valuenow={value} aria-valuemin={0} aria-valuemax={max}>
      <i style={{ width: `${Math.min(100, Math.max(0, (value / max) * 100))}%` }} />
    </span>
  );
}

function ConnectClient() {
  const [client, setClient] = useState("claude");
  const address = useConnectAddress();
  const base = address.data?.connectAddress;
  const snippet =
    client === "claude"
      ? `# from any machine on your private network\nexport ANTHROPIC_BASE_URL=${base ?? "<connect address unavailable>"}\nexport ANTHROPIC_API_KEY=${placeholder}`
      : client === "codex"
        ? `# from any machine on your private network\nexport OPENAI_BASE_URL=${base ?? "<connect address unavailable>"}/v1\nexport OPENAI_API_KEY=${placeholder}`
        : `# OpenAI SDK\nexport OPENAI_BASE_URL=${base ?? "<connect address unavailable>"}/v1\nexport OPENAI_API_KEY=${placeholder}`;
  return (
    <section className="connect keys-connect">
      <div className="top2">
        <h2>Connect a client</h2>
        <Seg
          value={client}
          onChange={setClient}
          options={[
            { label: "Claude Code", value: "claude" },
            { label: "Codex", value: "codex" },
            { label: "OpenAI SDK", value: "sdk" },
          ]}
        />
      </div>
      <div className="keys-snippet">
        <pre className="code">{snippet}</pre>
        <button
          type="button"
          className="btn ghost sm icon"
          aria-label="Copy connection snippet"
          disabled={!base}
          onClick={() =>
            void navigator.clipboard.writeText(snippet).then(
              () => toast.success("Snippet copied"),
              () => toast.error("Could not copy snippet"),
            )
          }
        >
          <Copy size={16} />
        </button>
      </div>
      {address.isError && <span role="alert">Connect address unavailable. Try again later.</span>}
    </section>
  );
}

function KeyRow({
  apiKey,
  accounts,
  onEdit,
  onRegenerate,
  onRevoke,
}: {
  apiKey: KeyWithOwner;
  accounts: { accountId: string; provider?: string }[];
  onEdit: (key: ApiKey) => void;
  onRegenerate: (key: KeyWithOwner) => void;
  onRevoke: (key: KeyWithOwner) => void;
}) {
  const providers = [
    ...new Set(
      apiKey.assignedAccountIds
        .map((id) => accounts.find((account) => account.accountId === id)?.provider)
        .filter((provider): provider is string => Boolean(provider)),
    ),
  ];
  const cap = apiKey.limits.find(
    (limit) => limit.limitType === "cost_usd" && ["weekly", "7d"].includes(limit.limitWindow),
  );
  const spent = apiKey.usageSummary?.totalCostUsd;
  const [now] = useState(() => Date.now());
  const expires = apiKey.expiresAt ? new Date(apiKey.expiresAt).getTime() - now : null;
  return (
    <div className={`row link ${apiKey.isActive ? "" : "dim"}`}>
      <div className="c-who who">
        <div className="t">
          <Link to={`/usage?key=${encodeURIComponent(apiKey.id)}`} className="n">
            {apiKey.name} <span className="mono muted">••{apiKey.keyPrefix.slice(-4)}</span>
          </Link>
          <span className="s">
            created {dateLabel(apiKey.createdAt)}
            {!apiKey.isActive ? " · revoked" : ""}
          </span>
        </div>
      </div>
      <div className="c-scope scope">
        {apiKey.assignedAccountIds.length === 0 ? (
          <span className="muted">All accounts</span>
        ) : providers.length > 0 ? (
          providers.map((provider) => (
            <ProviderMark key={provider} id={providerMark(provider)} size={16} />
          ))
        ) : (
          <span className="muted">{apiKey.assignedAccountIds.length} accounts</span>
        )}
      </div>
      <div className="c-spend">
        <div className="spend">
          <div className="t">
            <span className="num">{spent == null ? "—" : money(spent)}</span>
            <span className="muted">
              {cap ? `of ${money(cap.maxValue / 1_000_000)} cap` : "no cap"}
            </span>
          </div>
          {cap && <Meter value={cap.currentValue} max={cap.maxValue} />}
        </div>
      </div>
      <span className="c-used muted">
        {apiKey.lastUsedAt ? relative(apiKey.lastUsedAt) : "never"}
      </span>
      <span className="c-exp muted">
        {expires === null ? "never" : expires <= 0 ? "expired" : `in ${duration(expires)}`}
      </span>
      <div className="c-more">
        <RowMenu
          label={`Actions for ${apiKey.name}`}
          items={[
            { label: "Edit limits", onSelect: () => onEdit(apiKey) },
            { label: "Regenerate", onSelect: () => onRegenerate(apiKey) },
            { label: "Revoke", onSelect: () => onRevoke(apiKey), hidden: !apiKey.isActive },
          ]}
        />
      </div>
    </div>
  );
}

function OwnerGroup({
  name,
  detail,
  keys,
  member,
  accounts,
  onEdit,
  onRegenerate,
  onRevoke,
  onRevokeAll,
  onEditMember,
  onSuspend,
  onRemove,
  onOnboarding,
  onIssueKey,
}: {
  name: string;
  detail: string;
  keys: KeyWithOwner[];
  member?: TeamMember;
  accounts: { accountId: string; provider?: string }[];
  onEdit: (key: ApiKey) => void;
  onRegenerate: (key: KeyWithOwner) => void;
  onRevoke: (key: KeyWithOwner) => void;
  onRevokeAll?: () => void;
  onEditMember: (member: TeamMember) => void;
  onSuspend: (member: TeamMember) => void;
  onRemove: (member: TeamMember) => void;
  onOnboarding: (member: TeamMember) => void;
  onIssueKey: (member: TeamMember) => void;
}) {
  const cap =
    member &&
    (member.costCapWeekUsd != null
      ? { limit: member.costCapWeekUsd, value: member.usage.week.costUsd, reset: "Mon" }
      : member.costCapDayUsd != null
        ? { limit: member.costCapDayUsd, value: member.usage.day.costUsd, reset: "tomorrow" }
        : member.costCapMonthUsd != null
          ? {
              limit: member.costCapMonthUsd,
              value: member.usage.month.costUsd,
              reset: "next month",
            }
          : null);
  return (
    <>
      <div className="grp">
        <span aria-hidden="true">{onRevokeAll ? <Play size={16} /> : <UserRound size={16} />}</span>
        <span className="gn">{name}</span>
        <span className="gs">{detail}</span>
        {cap && (
          <span className="cap">
            <span className="num">
              {money(cap.value)} of {money(cap.limit)}
            </span>
            <Meter value={cap.value} max={cap.limit} />
            <span>resets {cap.reset}</span>
          </span>
        )}
        {member && (
          <span className="ga">
            <RowMenu
              label={`Manage ${member.name}`}
              items={[
                { label: "Issue key", onSelect: () => onIssueKey(member) },
                { label: "Edit caps and details", onSelect: () => onEditMember(member) },
                {
                  label: member.status === "active" ? "Suspend" : "Reactivate",
                  onSelect: () => onSuspend(member),
                },
                { label: "Onboarding", onSelect: () => onOnboarding(member) },
                { label: "Remove", onSelect: () => onRemove(member) },
              ]}
            />
          </span>
        )}
        {onRevokeAll && (
          <span className="ga">
            <button type="button" className="btn ghost sm" onClick={onRevokeAll}>
              Revoke all
            </button>
          </span>
        )}
      </div>
      {keys.map((key) => (
        <KeyRow
          key={key.id}
          apiKey={key}
          accounts={accounts}
          onEdit={onEdit}
          onRegenerate={onRegenerate}
          onRevoke={onRevoke}
        />
      ))}
    </>
  );
}

export function KeysPage() {
  const queryClient = useQueryClient();
  const { apiKeysQuery, createMutation, updateMutation, regenerateMutation, deleteMutation } =
    useApiKeys();
  const {
    membersQuery,
    createMutation: addMember,
    updateMutation: updateMember,
    deleteMutation: removeMember,
    issueKeyMutation,
  } = useTeamMembers();
  const { accountsQuery } = useAccounts();
  const ownerKeys = useQuery({
    queryKey: keysQueryKey,
    queryFn: () => get("/api/api-keys/", z.array(KeyWithOwnerSchema)),
  });
  const [owner, setOwner] = useState("");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [editing, setEditing] = useState<ApiKey | null>(null);
  const [revoke, setRevoke] = useState<KeyWithOwner | null>(null);
  const [revokeRuns, setRevokeRuns] = useState(false);
  const [memberDrawer, setMemberDrawer] = useState<TeamMember | "new" | null>(null);
  const [remove, setRemove] = useState<TeamMember | null>(null);
  const [suspend, setSuspend] = useState<TeamMember | null>(null);
  const [onboarding, setOnboarding] = useState<TeamMember | null>(null);
  const members = membersQuery.data ?? [];
  const { personal, byMember, runs } = useMemo(() => {
    const keys: KeyWithOwner[] = ownerKeys.data ?? apiKeysQuery.data ?? [];
    const personal: KeyWithOwner[] = [];
    const byMember = new Map<string, KeyWithOwner[]>();
    const runs: KeyWithOwner[] = [];
    for (const key of keys) {
      if (runKey(key)) runs.push(key);
      else if (key.memberId)
        byMember.set(key.memberId, [...(byMember.get(key.memberId) ?? []), key]);
      else personal.push(key);
    }
    return { personal, byMember, runs };
  }, [ownerKeys.data, apiKeysQuery.data]);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: keysQueryKey });
  const createKey = async (payload: ApiKeyCreateRequest) => {
    if (owner) {
      const result = await issueKeyMutation.mutateAsync({
        memberId: owner,
        payload: { name: payload.name, expiresAt: payload.expiresAt },
      });
      setCreated(result.key);
      // Team issuance accepts only a name and expiry. Apply the remaining fields
      // through the existing key update endpoint without ever logging the secret.
      const restrictions: ApiKeyUpdateRequest = { ...payload };
      delete restrictions.name;
      delete restrictions.expiresAt;
      delete restrictions.weeklyTokenLimit;
      if (
        Object.values(restrictions).some(
          (value) =>
            value !== undefined && value !== null && value !== false && value !== "foreground",
        )
      ) {
        await updateMutation.mutateAsync({ keyId: result.id, payload: restrictions });
      }
    } else {
      const result = await createMutation.mutateAsync(payload);
      setCreated(result.key);
    }
    refresh();
  };
  const regenerateKey = async (key: KeyWithOwner) => {
    if (!window.confirm(`Regenerate ${key.name}? The old secret will stop working immediately.`))
      return;
    try {
      const result = await regenerateMutation.mutateAsync(key.id);
      setCreated(result.key);
      refresh();
    } catch {
      /* mutation hook reports failure */
    }
  };
  const confirmRevoke = async () => {
    const targets = revokeRuns ? runs.filter((key) => key.isActive) : revoke ? [revoke] : [];
    try {
      for (const key of targets) await deleteMutation.mutateAsync(key.id);
      setRevoke(null);
      setRevokeRuns(false);
      refresh();
    } catch {
      /* keep confirmation open if a revoke failed */
    }
  };
  const submitMember = async (payload: TeamMemberCreateRequest | TeamMemberUpdateRequest) => {
    if (memberDrawer && memberDrawer !== "new")
      await updateMember.mutateAsync({ memberId: memberDrawer.id, payload });
    else await addMember.mutateAsync(payload as TeamMemberCreateRequest);
    setMemberDrawer(null);
  };
  const issueKey = async (member: TeamMember) => {
    try {
      const result = await issueKeyMutation.mutateAsync({ memberId: member.id });
      setCreated(result.key);
      refresh();
    } catch {
      /* hook reports failure */
    }
  };
  const error = [
    ownerKeys.error,
    apiKeysQuery.error,
    membersQuery.error,
    accountsQuery.error,
    createMutation.error,
    issueKeyMutation.error,
    updateMutation.error,
    deleteMutation.error,
    regenerateMutation.error,
    addMember.error,
    updateMember.error,
    removeMember.error,
  ]
    .map((item) => getErrorMessageOrNull(item))
    .find(Boolean);
  const actions = {
    accounts: accountsQuery.data ?? [],
    onEdit: setEditing,
    onRegenerate: (key: KeyWithOwner) => void regenerateKey(key),
    onRevoke: setRevoke,
    onEditMember: setMemberDrawer,
    onSuspend: setSuspend,
    onRemove: setRemove,
    onOnboarding: setOnboarding,
    onIssueKey: (member: TeamMember) => void issueKey(member),
  };
  return (
    <div className="keys-page">
      <PageHead
        title="Keys"
        action={
          <div className="keys-create">
            <label htmlFor="key-owner">Owner</label>
            <select id="key-owner" value={owner} onChange={(event) => setOwner(event.target.value)}>
              <option value="">You</option>
              {members.map((member) => (
                <option key={member.id} value={member.id}>
                  {member.name}
                </option>
              ))}
            </select>
            <button type="button" className="btn primary" onClick={() => setCreating(true)}>
              <Plus size={16} />
              New key
            </button>
          </div>
        }
      >
        Every client that talks to agent-lb uses a key. Spend caps, account limits and usage attach
        to the key, so any load can be traced to whoever sent it.
      </PageHead>
      <ConnectClient />
      {error && <AlertMessage variant="error">{error}</AlertMessage>}
      <Section
        title="All keys"
        aside="A key's secret is shown once, when it is created"
        className="keys-section"
      >
        <div className="rows keys">
          <div className="row hd">
            <span>Key</span>
            <span>Scope</span>
            <span>This week</span>
            <span>Last used</span>
            <span>Expires</span>
            <span />
          </div>
          <OwnerGroup name="You" detail={`${personal.length} keys`} keys={personal} {...actions} />
          {members.map((member) => (
            <OwnerGroup
              key={member.id}
              name={member.name}
              detail={`team member · ${byMember.get(member.id)?.length ?? 0} keys${member.status === "suspended" ? " · suspended" : ""}`}
              keys={byMember.get(member.id) ?? []}
              member={member}
              {...actions}
            />
          ))}
          <OwnerGroup
            name="Run keys"
            detail={`expiry within 7 days or of- name · ${runs.filter((key) => key.isActive).length} live`}
            keys={runs}
            onRevokeAll={() => setRevokeRuns(true)}
            {...actions}
          />
        </div>
        <button
          type="button"
          className="btn ghost sm keys-add-member"
          onClick={() => setMemberDrawer("new")}
        >
          <Plus size={15} />
          Add team member
        </button>
        <div className="qline">
          <span>Run keys from a script:</span>
          <code>
            POST {typeof window === "undefined" ? "" : window.location.origin}/api/api-keys{" "}
            {JSON.stringify({
              name: "of-eval · arm-d",
              expires_at: "2026-09-26T14:00Z",
              limits: [{ limit_type: "cost_usd", limit_window: "weekly", max_value: 40 }],
            })}
          </code>
        </div>
      </Section>
      <ApiKeyCreateDialog
        open={creating}
        busy={createMutation.isPending || issueKeyMutation.isPending}
        onOpenChange={setCreating}
        onSubmit={createKey}
      />
      <ApiKeyCreatedDialog
        open={created !== null}
        apiKey={created}
        onOpenChange={(open) => {
          if (!open) setCreated(null);
        }}
      />
      <ApiKeyEditDialog
        open={editing !== null}
        apiKey={editing}
        busy={updateMutation.isPending}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
        onSubmit={async (payload: ApiKeyUpdateRequest) => {
          if (!editing) return;
          await updateMutation.mutateAsync({ keyId: editing.id, payload });
          refresh();
        }}
      />
      <ConfirmDialog
        open={revoke !== null || revokeRuns}
        title={revokeRuns ? "Revoke all run keys?" : `Revoke ${revoke?.name ?? "key"}?`}
        description={
          revokeRuns
            ? `This will stop ${runs.filter((key) => key.isActive).length} run keys immediately.`
            : `The key ${revoke?.name ?? ""} will stop working immediately.`
        }
        confirmLabel="Revoke"
        onOpenChange={(open) => {
          if (!open) {
            setRevoke(null);
            setRevokeRuns(false);
          }
        }}
        onConfirm={() => void confirmRevoke()}
      />
      <TeamMemberDrawer
        open={memberDrawer !== null}
        member={memberDrawer === "new" ? null : memberDrawer}
        busy={addMember.isPending || updateMember.isPending}
        onOpenChange={(open) => {
          if (!open) setMemberDrawer(null);
        }}
        onSubmit={submitMember}
      />
      <TeamOnboardingDialog
        open={onboarding !== null}
        member={onboarding}
        onOpenChange={(open) => {
          if (!open) setOnboarding(null);
        }}
      />
      <ConfirmDialog
        open={remove !== null}
        title={`Remove ${remove?.name ?? "team member"}?`}
        description="Their keys keep working but stop counting against this member's caps."
        confirmLabel="Remove"
        onOpenChange={(open) => {
          if (!open) setRemove(null);
        }}
        onConfirm={() => {
          if (remove)
            void removeMember.mutateAsync(remove.id).then(() => {
              setRemove(null);
              refresh();
            });
        }}
      />
      <ConfirmDialog
        open={suspend !== null}
        title={`${suspend?.status === "active" ? "Suspend" : "Reactivate"} ${suspend?.name ?? "team member"}?`}
        description="This changes whether their keys can send requests."
        onOpenChange={(open) => {
          if (!open) setSuspend(null);
        }}
        onConfirm={() => {
          if (suspend)
            void updateMember
              .mutateAsync({
                memberId: suspend.id,
                payload: { status: suspend.status === "active" ? "suspended" : "active" },
              })
              .then(() => setSuspend(null));
        }}
      />
    </div>
  );
}
