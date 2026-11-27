import { useEffect, useRef, useState } from "react";
import { z } from "zod";

import { getOauthStatus } from "@/features/accounts/api";
import { ApiError, del, post } from "@/lib/api-client";

const BROWSER_API = "/api/reauth/browser";
const RFB_MODULE = `${BROWSER_API}/novnc/core/rfb.js`;
const BrowserSession = z.object({
  sessionId: z.string().regex(/^[\w-]+$/),
  flowId: z.string(),
  websocketPath: z.string(),
});
type BrowserSession = z.infer<typeof BrowserSession>;
type Rfb = EventTarget & { scaleViewport: boolean; disconnect: () => void };
type RfbModule = { default: new (element: HTMLElement, url: string, options: { wsProtocols: string[] }) => Rfb };
type BrowserStatus = Awaited<ReturnType<typeof getOauthStatus>>;

export function ReauthBrowser({ accountId, onResult }: {
  accountId: string;
  onResult: (result: BrowserStatus) => void;
}) {
  const screen = useRef<HTMLDivElement>(null);
  const attempt = useRef<AbortController | null>(null);
  const [session, setSession] = useState<BrowserSession | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => () => attempt.current?.abort(), []);

  async function open() {
    if (attempt.current) return;
    const controller = new AbortController();
    attempt.current = controller;
    setNotice(null);
    setLoading(true);
    try {
      const result = await post(`${BROWSER_API}/sessions`, BrowserSession, { body: { accountId }, signal: controller.signal });
      if (!controller.signal.aborted) setSession(result);
    } catch (error) {
      if (controller.signal.aborted) return;
      setNotice(error instanceof ApiError && [401, 403].includes(error.status)
        ? "This dashboard cannot open the server browser. Use the private sign-in below."
        : "The server browser is unavailable. Try again or use the private sign-in below.");
    } finally {
      attempt.current = null;
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  useEffect(() => {
    if (!session || !screen.current) return;
    const element = screen.current;
    const { flowId, sessionId, websocketPath } = session;
    let disposed = false;
    let checking = false;
    let rfb: Rfb | undefined;
    let disconnectedAt = 0;
    const wsPath = `${BROWSER_API}/sessions/${sessionId}/ws`;
    const wsUrl = new URL(wsPath, window.location.origin);
    wsUrl.protocol = "wss:";

    function fail() {
      if (disposed) return;
      setSession(null);
      setNotice("The server browser closed. Open it again or use private sign-in.");
    }

    async function poll() {
      if (disposed || checking) return;
      checking = true;
      try {
        const result = await getOauthStatus(flowId);
        if (disposed) return;
        if (result.status === "success" || result.status === "error") {
          setSession(null);
          onResult(result);
          if (result.status === "error") setNotice(result.errorMessage ?? "Claude did not complete the sign-in.");
        } else if (disconnectedAt && Date.now() - disconnectedAt > 5000) {
          fail();
        }
      } catch {
        if (disconnectedAt && Date.now() - disconnectedAt > 5000) fail();
      } finally {
        checking = false;
      }
    }

    if (websocketPath !== wsPath || window.location.protocol !== "https:") {
      fail();
      return;
    }
    void import(RFB_MODULE).then((module: RfbModule) => {
      if (disposed) return;
      rfb = new module.default(element, wsUrl.href, { wsProtocols: ["binary"] });
      rfb.scaleViewport = true;
      rfb.addEventListener("disconnect", () => { disconnectedAt = Date.now(); void poll(); });
    }).catch(fail);
    const timer = window.setInterval(() => void poll(), 2000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      rfb?.disconnect();
      void del(`${BROWSER_API}/sessions/${sessionId}`).catch(() => {});
    };
  }, [onResult, session]);

  return (
    <section className="reauth-browser" aria-label="Isolated server browser">
      {notice && <p role="status">{notice}</p>}
      <div className="toolbar">
        {session
          ? <button className="btn sm" type="button" onClick={() => setSession(null)}>Close server browser</button>
          : <button className="btn sm primary" disabled={loading} type="button" onClick={() => void open()}>
              {loading ? "Opening…" : "Open isolated server browser"}
            </button>}
      </div>
      {session && <div className="reauth-screen" ref={screen} aria-label="Claude sign-in display" />}
    </section>
  );
}
