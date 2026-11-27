import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { useAccounts } from "@/features/accounts/hooks/use-accounts";
import { getOauthStatus } from "@/features/accounts/api";
import { useOauth } from "@/features/accounts/hooks/use-oauth";
import type { AccountSummary } from "@/features/accounts/schemas";
import { Crumbs } from "@/lb/kit/primitives";
import { ProviderMark } from "@/lb/kit/provider-mark";
import { providerMark } from "@/lb/kit/provider-mark-helpers";
import { ReauthBrowser } from "./reauth-browser";
import { toast } from "sonner";
import "./reauth.css";

function accountPath(accountId: string) {
  return `/providers/${encodeURIComponent(accountId)}`;
}

function reauthPath(accountId: string) {
  return `/reauth/${encodeURIComponent(accountId)}`;
}

function safeDeviceUrl(rawUrl: string | null) {
  if (!rawUrl) return null;
  try {
    const url = new URL(rawUrl);
    return url.protocol === "https:" && url.hostname === "auth.openai.com" ? url.href : null;
  } catch {
    return null;
  }
}

async function copy(value: string, label: string) {
  try {
    await navigator.clipboard.writeText(value);
    toast.success(`${label} copied`);
  } catch {
    toast.error(`Could not copy ${label.toLowerCase()}`);
  }
}

function ReauthFlow({ account, next }: { account: AccountSummary; next?: AccountSummary }) {
  const { accountId, email } = account;
  const device = account.provider === "openai";
  const { start, state, manualCallback } = useOauth();
  const started = useRef(false);
  const submitting = useRef(false);
  const [code, setCode] = useState("");
  const [callbackError, setCallbackError] = useState<string | null>(null);
  const [retryingCallback, setRetryingCallback] = useState(false);
  const [browserProbeStatus, setBrowserProbeStatus] = useState<number | null>(null);
  const browserResult = useCallback((result: Awaited<ReturnType<typeof getOauthStatus>>) => {
    if (result.status === "success") setBrowserProbeStatus(result.probeStatusCode ?? 0);
  }, []);
  const begin = () => {
    setCode("");
    setCallbackError(null);
    void start(device ? "device" : "browser", device ? "openai" : "anthropic", accountId).catch(() => {});
  };

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void start(device ? "device" : "browser", device ? "openai" : "anthropic", accountId).catch(() => {});
  }, [accountId, device, start]);

  async function submit(rawCode: string) {
    const callback = rawCode.trim();
    if (!state.flowId || submitting.current) return;
    if (!/^[^\s#]+#[^\s#]+$/.test(callback)) {
      setCallbackError("Paste the full code, including the part after #.");
      return;
    }
    submitting.current = true;
    setCallbackError(null);
    setRetryingCallback(true);
    try {
      await manualCallback(callback);
    } catch {
      setCallbackError("Could not submit the code. Try again with a fresh sign-in.");
    } finally {
      setCode("");
      setRetryingCallback(false);
      submitting.current = false;
    }
  }

  const deviceUrl = safeDeviceUrl(state.verificationUrl);
  const finished = state.status === "success" || browserProbeStatus !== null;
  const verified = (browserProbeStatus ?? state.probeStatusCode) === 200;
  const failed = state.status === "error";
  const loading = state.status === "idle" || state.status === "starting";
  const boundLink = state.authorizationUrl;

  return (
    <div className="account-page reauth-page">
      <Crumbs items={[{ label: "Providers", to: "/" }, { label: email, to: accountPath(accountId) }, { label: "Sign in" }]} />
      <div className="reauth-heading">
        <ProviderMark id={providerMark(device ? "openai" : "anthropic")} size={28} />
        <div><h1>Reconnect account</h1><p>{email}</p></div>
      </div>
      <section className="card reauth-steps" aria-live="polite">
        {loading && <p>Preparing a sign-in for this account…</p>}
        {finished && (
          <>
            <h2>{verified ? "Account reconnected" : "Sign-in saved"}</h2>
            <p>{verified
              ? "The provider verified the new sign-in. You can close this page or continue to the next account."
              : "The provider did not verify the new sign-in. The Needs You card stays open."}</p>
            <div className="toolbar">
              <Link className="btn sm" to={accountPath(accountId)}>Back to account</Link>
              {next && <Link className="btn sm primary" to={reauthPath(next.accountId)}>Next account →</Link>}
            </div>
          </>
        )}
        {failed && !finished && (
          <>
            <h2>Sign-in did not finish</h2>
            <p role="alert">{state.errorMessage || "Start a fresh sign-in to try again."}</p>
            <button className="btn sm primary" type="button" onClick={begin}>Start a new sign-in</button>
          </>
        )}
        {!loading && !finished && !failed && device && (
          <>
            <h2>Enter the device code at OpenAI</h2>
            <p>Sign in as {email}. This page checks for completion automatically.</p>
            {state.userCode && <output className="reauth-device-code" aria-label="Device code">{state.userCode}</output>}
            <div className="toolbar">
              {deviceUrl && <a className="btn sm primary" href={deviceUrl} target="_blank" rel="noreferrer">Open OpenAI</a>}
              {state.userCode && <button className="btn sm" type="button" onClick={() => void copy(state.userCode!, "Device code")}>Copy code</button>}
            </div>
          </>
        )}
        {!loading && !finished && !failed && !device && boundLink && (
          <>
            <h2>Sign in to Claude</h2>
            <ReauthBrowser accountId={accountId} onResult={browserResult} />
            <details className="reauth-fallback">
              <summary>Sign in privately if the server IP is blocked</summary>
              <p>Open in Private/Incognito — never your main Claude tab. Paste the code Claude shows after you authorize.</p>
              <div className="toolbar">
                <button className="btn sm" type="button" onClick={() => void copy(email, "Email")}>Copy Email</button>
                <button className="btn sm" type="button" onClick={() => void copy(boundLink, "Bound auth link")}>Copy Bound Auth Link</button>
              </div>
              <form onSubmit={(event: FormEvent<HTMLFormElement>) => { event.preventDefault(); void submit(code); }}>
                <label htmlFor="reauth-code">Claude authorization code</label>
                <div className="toolbar">
                  <input id="reauth-code" type="password" autoComplete="off" spellCheck={false} placeholder="code#state" value={code}
                    onChange={(event) => setCode(event.target.value)}
                    onPaste={(event) => { event.preventDefault(); void submit(event.clipboardData.getData("text")); }} />
                  <button className="btn sm" type="submit" disabled={retryingCallback || !code}>Submit code</button>
                </div>
                {callbackError && <p role="alert">{callbackError}</p>}
              </form>
            </details>
          </>
        )}
      </section>
    </div>
  );
}

export function ReauthPage() {
  const { accountId = "" } = useParams();
  const { accountsQuery } = useAccounts();
  const account = accountsQuery.data?.find((item) => item.accountId === accountId);
  const next = accountsQuery.data?.find((item) => item.accountId !== accountId && item.status === "reauth_required"
    && (item.provider === "anthropic" || item.provider === "openai") && item.isLocallyOwned !== false);

  if (accountsQuery.isPending) return <p role="status">Loading account…</p>;
  if (accountsQuery.isError) return <p role="alert">Could not load the account. Refresh to try again.</p>;
  if (!account || (account.provider !== "anthropic" && account.provider !== "openai")) {
    return <p role="alert">This account does not support this sign-in page. <Link to="/">Back to providers</Link></p>;
  }
  if (account.isLocallyOwned === false) {
    return <p role="alert">Reconnect this account on its owner instance. <Link to={accountPath(accountId)}>Back to account</Link></p>;
  }
  return <ReauthFlow key={accountId} account={account} next={next} />;
}
