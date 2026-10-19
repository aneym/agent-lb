import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Plus } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { z } from "zod";

import { AlertMessage } from "@/components/alert-message";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ApiKeyCreateDialog } from "@/features/api-keys/components/api-key-create-dialog";
import { ApiKeyCreatedDialog } from "@/features/api-keys/components/api-key-created-dialog";
import { ApiKeyEditDialog } from "@/features/api-keys/components/api-key-edit-dialog";
import { useApiKeys } from "@/features/api-keys/hooks/use-api-keys";
import {
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
import { PageHead, Section, Seg } from "@/lb/kit/primitives";
import { get } from "@/lib/api-client";
import { getErrorMessageOrNull } from "@/utils/errors";
import { KeyWithOwnerSchema, type KeyWithOwner } from "./keys-model";
import { OwnerGroup } from "./owner-group";
import "./keys.css";

const keysQueryKey = ["lb", "keys"];
const placeholder = "<a key from the list below>";
const keyCount = (count: number) => `${count} ${count === 1 ? "key" : "keys"}`;
const runKey = (key: KeyWithOwner) =>
  /^of-/i.test(key.name) ||
  (key.expiresAt !== null &&
    new Date(key.expiresAt).getTime() - new Date(key.createdAt).getTime() <= 7 * 86_400_000);

function ConnectClient() {
  const [client, setClient] = useState("claude");
  const address = useConnectAddress();
  const configuredAddress = address.data?.connectAddress;
  const base =
    configuredAddress && !configuredAddress.includes("<agent-lb-ip-or-dns>")
      ? configuredAddress.replace(/\/$/, "")
      : window.location.origin;
  const snippet =
    client === "claude"
      ? `# from any machine on your private network\nexport ANTHROPIC_BASE_URL=${base}\nexport ANTHROPIC_API_KEY=${placeholder}`
      : client === "codex"
        ? `# from any machine on your private network\nexport OPENAI_BASE_URL=${base}/v1\nexport OPENAI_API_KEY=${placeholder}`
        : `# OpenAI SDK\nexport OPENAI_BASE_URL=${base}/v1\nexport OPENAI_API_KEY=${placeholder}`;
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
      {(!configuredAddress || configuredAddress.includes("<agent-lb-ip-or-dns>")) && (
        <span className="muted">
          Using this page's address; use your private-network address from other machines.
        </span>
      )}
    </section>
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
  const [choosingOwner, setChoosingOwner] = useState(false);
  const [creating, setCreating] = useState(false);
  const [showRevoked, setShowRevoked] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [editing, setEditing] = useState<ApiKey | null>(null);
  const [revoke, setRevoke] = useState<KeyWithOwner | null>(null);
  const [revokeRuns, setRevokeRuns] = useState(false);
  const [memberDrawer, setMemberDrawer] = useState<TeamMember | "new" | null>(null);
  const [remove, setRemove] = useState<TeamMember | null>(null);
  const [suspend, setSuspend] = useState<TeamMember | null>(null);
  const [onboarding, setOnboarding] = useState<TeamMember | null>(null);
  const members = membersQuery.data ?? [];
  const revoked = (ownerKeys.data ?? apiKeysQuery.data ?? []).filter((key) => !key.isActive);
  const { personal, byMember, runs } = useMemo(() => {
    const keys: KeyWithOwner[] = (ownerKeys.data ?? apiKeysQuery.data ?? []).filter(
      (key) => key.isActive,
    );
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
    if (owner === "run") {
      payload = {
        ...payload,
        name: /^of-/i.test(payload.name) ? payload.name : `of-${payload.name}`,
        expiresAt: payload.expiresAt ?? new Date(Date.now() + 7 * 86_400_000).toISOString(),
      };
    }
    if (owner && owner !== "run") {
      const result = await issueKeyMutation.mutateAsync({
        memberId: owner,
        payload: { name: payload.name, expiresAt: payload.expiresAt },
      });
      setCreated(result.key);


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
        await updateMutation.mutateAsync({
          keyId: result.id,
          payload: restrictions,
        });
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
      const result = await issueKeyMutation.mutateAsync({
        memberId: member.id,
      });
      setCreated(result.key);
      refresh();
    } catch {

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
          <button type="button" className="btn primary" onClick={() => setChoosingOwner(true)}>
            <Plus size={16} />
            New key
          </button>
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
          <OwnerGroup name="You" detail={keyCount(personal.length)} keys={personal} {...actions} />
          {members.map((member) => (
            <OwnerGroup
              key={member.id}
              name={member.name}
              detail={`team member · ${keyCount(byMember.get(member.id)?.length ?? 0)}${member.status === "suspended" ? " · suspended" : ""}`}
              keys={byMember.get(member.id) ?? []}
              member={member}
              {...actions}
            />
          ))}
          <OwnerGroup
            name="Run keys"
            detail={`expiry within 7 days or of- name · ${keyCount(runs.length)} live`}
            keys={runs}
            onRevokeAll={runs.length > 0 ? () => setRevokeRuns(true) : undefined}
            {...actions}
          />
          {revoked.length > 0 && (
            <>
              <button
                type="button"
                className="keys-revoked-toggle"
                aria-expanded={showRevoked}
                onClick={() => setShowRevoked((shown) => !shown)}
              >
                {showRevoked ? "Hide" : "Show"} {keyCount(revoked.length)} revoked
              </button>
              {showRevoked && (
                <OwnerGroup
                  name="Revoked keys"
                  detail={keyCount(revoked.length)}
                  keys={revoked}
                  {...actions}
                />
              )}
            </>
          )}
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
            POST {typeof window === "undefined" ? "" : window.location.origin}
            /api/api-keys{" "}
            {JSON.stringify({
              name: "of-eval · arm-d",
              expires_at: "2026-09-26T14:00Z",
              limits: [
                {
                  limit_type: "cost_usd",
                  limit_window: "weekly",
                  max_value: 40,
                },
              ],
            })}
          </code>
        </div>
      </Section>
      <Dialog open={choosingOwner} onOpenChange={setChoosingOwner}>
        <DialogContent className="keys-owner-dialog">
          <DialogHeader>
            <DialogTitle>Who is this key for?</DialogTitle>
            <DialogDescription>
              Choose an owner before setting the key's restrictions.
            </DialogDescription>
          </DialogHeader>
          <div className="keys-owner-options">
            {[
              { id: "", label: "You", detail: "A personal key" },
              ...members.map((member) => ({
                id: member.id,
                label: member.name,
                detail: "Team member",
              })),
              {
                id: "run",
                label: "Run key",
                detail: "A short-lived key for scripts",
              },
            ].map((choice) => (
              <button
                key={choice.id}
                type="button"
                className="keys-owner-option"
                onClick={() => {
                  setOwner(choice.id);
                  setChoosingOwner(false);
                  setCreating(true);
                }}
              >
                <strong>{choice.label}</strong>
                <span>{choice.detail}</span>
              </button>
            ))}
          </div>
        </DialogContent>
      </Dialog>
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
                payload: {
                  status: suspend.status === "active" ? "suspended" : "active",
                },
              })
              .then(() => setSuspend(null));
        }}
      />
    </div>
  );
}
