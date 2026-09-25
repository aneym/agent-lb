import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { z } from "zod";
import { post } from "@/lib/api-client";
import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { useOauth } from "@/features/accounts/hooks/use-oauth";
import { OauthDialog } from "@/features/accounts/components/oauth-dialog";
import { Crumbs, LimitMeter, PageHead } from "../../kit/primitives";
import { ProviderMark } from "../../kit/provider-mark";
import "./add-account.css";

type Provider =
  "anthropic" | "openai" | "cursor" | "devin" | "kimi" | "glm" | "openrouter" | "gemini" | "ollama";
type Tile = { id: Provider; label: string; detail: string; disabled?: boolean };
const groups: { name: string; tiles: Tile[] }[] = [
  {
    name: "Subscriptions",
    tiles: [
      { id: "anthropic", label: "Claude", detail: "Sign in · Pro or Max" },
      { id: "openai", label: "ChatGPT · Codex", detail: "Sign in · Plus or Pro" },
    ],
  },
  {
    name: "Coding agents",
    tiles: [
      { id: "cursor", label: "Cursor", detail: "Import CLI login" },
      { id: "devin", label: "Devin", detail: "Import CLI login" },
    ],
  },
  {
    name: "API keys",
    tiles: [
      { id: "kimi", label: "Kimi", detail: "Paste a key" },
      { id: "glm", label: "GLM · Z.ai", detail: "Paste a key" },
      { id: "openrouter", label: "OpenRouter", detail: "Paste a key" },
      { id: "gemini", label: "Gemini", detail: "Not available yet", disabled: true },
    ],
  },
  {
    name: "On this machine",
    tiles: [{ id: "ollama", label: "Ollama", detail: "Not available yet", disabled: true }],
  },
];
const labels: Record<Provider, string> = Object.fromEntries(
  groups.flatMap((group) => group.tiles.map((tile) => [tile.id, tile.label])),
) as Record<Provider, string>;
const Imported = z.object({ accountId: z.string(), email: z.string(), planType: z.string() });
const isKeyProvider = (value: Provider) => ["kimi", "glm", "openrouter"].includes(value);

export function AddAccountPage() {
  const [params] = useSearchParams();
  const initial = params.get("provider");
  const [selected, setSelected] = useState<Provider>(
    groups.flatMap((g) => g.tiles).some((t) => t.id === initial && !t.disabled)
      ? (initial as Provider)
      : "anthropic",
  );
  const [oauthOpen, setOauthOpen] = useState(false);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reading, setReading] = useState(false);
  const oauth = useOauth();
  const accounts = useAccounts();
  const client = useQueryClient();
  const navigate = useNavigate();
  const added = accounts.accountsQuery.data?.find((a) => a.accountId === createdId);

  async function afterAdd(id: string) {
    setCreatedId(id);
    await accounts.accountsQuery.refetch();
    setReading(true);
    try {
      await accounts.probeMutation.mutateAsync({ accountId: id });
      await accounts.accountsQuery.refetch();
    } catch {
      /* Probe can be retried; never hide the newly added account. */
    } finally {
      setReading(false);
    }
  }
  async function addKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isKeyProvider(selected) || !key.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await post("/api/accounts/import/api-key", Imported, {
        body: { provider: selected, apiKey: key.trim() },
      });
      setKey("");
      await client.invalidateQueries({ queryKey: ["accounts", "list"] });
      await afterAdd(result.accountId);
      toast.success("Account added");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not add account");
    } finally {
      setBusy(false);
    }
  }
  async function saveName() {
    if (!createdId) return;
    setBusy(true);
    setError("");
    try {
      await accounts.setAliasMutation.mutateAsync({
        accountId: createdId,
        alias: name.trim() || null,
      });
      await accounts.accountsQuery.refetch();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not name account");
    } finally {
      setBusy(false);
    }
  }
  async function oauthComplete() {
    const before = new Set(accounts.accountsQuery.data?.map((a) => a.accountId));
    await oauth.complete();
    const refreshed = await accounts.accountsQuery.refetch();
    const latest = refreshed.data?.find((a) => !before.has(a.accountId) && a.provider === selected);
    if (latest) await afterAdd(latest.accountId);
    else setError("Sign-in completed. If this was an existing account, open it from Providers.");
    setOauthOpen(false);
  }
  const choose = (id: Provider) => {
    setSelected(id);
    setError("");
    setCreatedId(null);
    setKey("");
    setName("");
  };
  const command =
    selected === "cursor"
      ? "seat add my-cursor --vendor cursor --api-key-prompt"
      : "seat add my-devin --vendor devin --data-dir /path/to/login";
  return (
    <div className="add-account-page">
      <Crumbs items={[{ label: "Providers", to: "/" }, { label: "Add account" }]} />
      <PageHead title="Add an account">
        Pick a provider. Subscriptions sign in through the provider's own page; agent-lb never sees
        your password.
      </PageHead>
      <div className="addsplit">
        <div>
          {groups.map((group) => (
            <section key={group.name}>
              <h2 className="grouph">{group.name}</h2>
              <div className="ptiles">
                {group.tiles.map((tile) => (
                  <button
                    type="button"
                    key={tile.id}
                    className={`pt ${selected === tile.id ? "sel" : ""} ${tile.disabled ? "soon" : ""}`}
                    disabled={tile.disabled}
                    aria-pressed={selected === tile.id}
                    onClick={() => choose(tile.id)}
                  >
                    <ProviderMark id={tile.id === "anthropic" ? "claude" : tile.id} size={28} />
                    <span className="pn">{tile.label}</span>
                    <span className="pm">{tile.detail}</span>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
        <aside className="steps" aria-label={`Connect ${labels[selected]}`}>
          <div className="add-account-title">
            <ProviderMark id={selected === "anthropic" ? "claude" : selected} size={28} />
            <h2>Connect a {labels[selected]} account</h2>
          </div>
          {createdId ? (
            <>
              <div className="step done">
                <span className="sn">✓</span>
                <b>{isKeyProvider(selected) ? "Key saved" : "Signed in"}</b>
                <div className="sb">
                  <span className="muted privacy-blur">{added?.email || "Account connected"}</span>
                </div>
              </div>
              <div className="step now">
                <span className="sn">2</span>
                <b>Name it</b>
                <div className="sb">
                  <input
                    aria-label="Account name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder={added?.alias || `${added?.planType || labels[selected]} · account`}
                  />
                  <span className="muted">This name shows everywhere instead of the email.</span>
                  <button className="btn sm" disabled={busy} onClick={() => void saveName()}>
                    Save name
                  </button>
                </div>
              </div>
              <div className="step">
                <span className="sn">3</span>
                <b>First reading</b>
                <div className="sb">
                  {reading ? (
                    <span className="wait">
                      <span className="spin" /> Checking limits…
                    </span>
                  ) : added ? (
                    <div className="lrows">
                      {added.windowMinutesSecondary && (
                        <div className="lrow">
                          <span className="k">Week</span>
                          <LimitMeter
                            remaining={added.usage?.secondaryRemainingPercent}
                            resetAt={added.resetAtSecondary}
                          />
                        </div>
                      )}
                      {added.windowMinutesPrimary === 300 && selected !== "openai" && (
                        <div className="lrow">
                          <span className="k">5-hour</span>
                          <LimitMeter
                            remaining={added.usage?.primaryRemainingPercent}
                            resetAt={added.resetAtPrimary}
                            unit="5h"
                          />
                        </div>
                      )}
                      {!added.windowMinutesSecondary && (
                        <span className="muted">No limit reading yet.</span>
                      )}
                    </div>
                  ) : (
                    <span className="muted">Reading not available yet.</span>
                  )}
                </div>
              </div>
              <div className="step done">
                <span className="sn">✓</span>
                <b>Join a pool</b>
                <div className="sb">The account is ready to route when eligible.</div>
              </div>
              <div className="add-actions">
                <button className="btn primary" onClick={() => navigate("/")}>
                  Done
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="step now">
                <span className="sn">1</span>
                <b>
                  {isKeyProvider(selected)
                    ? "Paste an API key"
                    : selected === "cursor" || selected === "devin"
                      ? "Import local CLI login"
                      : `Sign in to ${labels[selected]}`}
                </b>
                <div className="sb">
                  {isKeyProvider(selected) ? (
                    <form className="add-key-form" onSubmit={(event) => void addKey(event)}>
                      <label htmlFor="provider-api-key">API key</label>
                      <input
                        id="provider-api-key"
                        type="password"
                        autoComplete="off"
                        required
                        value={key}
                        onChange={(event) => setKey(event.target.value)}
                        placeholder="Paste your API key"
                      />
                      <button className="btn primary" disabled={busy || !key.trim()}>
                        Add account
                      </button>
                    </form>
                  ) : selected === "cursor" || selected === "devin" ? (
                    <>
                      <span>Run this on the machine with your CLI login:</span>
                      <code className="add-command">{command}</code>
                      <span>Then return to Providers to see the seat.</span>
                    </>
                  ) : (
                    <>
                      <span>Sign in through the provider's own page.</span>
                      <button className="btn primary" onClick={() => setOauthOpen(true)}>
                        Sign in with {labels[selected]}
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="step later">
                <span className="sn">2</span>
                <b>Name it</b>
                <div className="sb">This name shows everywhere instead of the email.</div>
              </div>
              <div className="step later">
                <span className="sn">3</span>
                <b>First reading</b>
                <div className="sb">We'll check the limits after connecting.</div>
              </div>
              <div className="step later">
                <span className="sn">4</span>
                <b>Join a pool</b>
                <div className="sb">Starts taking requests when eligible.</div>
              </div>
              <div className="add-actions">
                <Link className="btn" to="/">
                  Cancel
                </Link>
              </div>
            </>
          )}
          {error && (
            <p className="add-error" role="alert">
              {error}
            </p>
          )}
        </aside>
      </div>
      <OauthDialog
        key={selected}
        open={oauthOpen}
        state={oauth.state}
        initialProvider={selected === "anthropic" ? "anthropic" : "openai"}
        onOpenChange={setOauthOpen}
        onStart={async (method, provider) => {
          await oauth.start(method, provider);
        }}
        onComplete={oauthComplete}
        onManualCallback={async (callback) => {
          await oauth.manualCallback(callback);
        }}
        onReset={oauth.reset}
      />
    </div>
  );
}
