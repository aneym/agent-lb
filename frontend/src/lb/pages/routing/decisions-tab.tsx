import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { clock, compact } from "@/lb/format";
import { FilterSelect, ModelChip, Seg, SidePanel } from "@/lb/kit/primitives";
import { useMenu, useRouteDecisions, type RouteDecision } from "./data";

function elapsed(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}
function Pick({ decision }: { decision: RouteDecision }) {
  if (!decision.pick) return <span className="muted">No pick</span>;
  return (
    <div className="pickc">
      <ModelChip
        model={decision.pick.model || decision.pick.seat || "unknown"}
        seat={decision.pick.seat || undefined}
      />
      {decision.fallback && <span className="muted">after {decision.fallback}</span>}
    </div>
  );
}
function RouteTrace({ decision }: { decision: RouteDecision }) {
  const outcome = decision.outcome;
  return (
    <div className="route-trace">
      <div className="meta">
        {decision.taskClass || "Task"} · {new Date(decision.ts).toLocaleString()}{" "}
        {decision.sessionId && (
          <>
            · session <span className="mono">{decision.sessionId}</span>
          </>
        )}
      </div>
      <div className="stg">
        <div className="sh">
          <span className="no">step 2</span>
          <h3>{decision.kind === "of_decision" ? "Open Factory decision" : "Direct dispatch"}</h3>
        </div>
        {decision.kind === "dispatch" ? (
          <p>
            Dispatched directly, no decider ran.{" "}
            {decision.pick && (
              <>
                Seat: {decision.pick.seat || "unknown"} · model:{" "}
                {decision.pick.model || "not recorded"}.
              </>
            )}
          </p>
        ) : (
          <>
            {decision.candidates.map((candidate, index) => (
              <div
                className={`cand ${candidate.excludedReason ? "x" : decision.pick?.seat === candidate.seat ? "won" : ""}`}
                key={`${candidate.seat}-${index}`}
              >
                <span aria-hidden="true">
                  {candidate.excludedReason
                    ? "×"
                    : decision.pick?.seat === candidate.seat
                      ? "✓"
                      : "—"}
                </span>
                <ModelChip model={candidate.model || candidate.seat} seat={candidate.seat} />
                <span className="num">
                  {candidate.score == null ? "" : candidate.score.toFixed(2)}
                </span>
                {candidate.excludedReason && <span className="rs">{candidate.excludedReason}</span>}
              </div>
            ))}
            {decision.candidates.length === 0 && <p>Candidate scores were not recorded.</p>}
            <div className="kv">
              {decision.deciderMs != null && (
                <span>
                  decider <b>{decision.deciderMs} ms</b>
                </span>
              )}
              {decision.policyVersion != null && (
                <span>
                  policy <b>v{decision.policyVersion}</b>
                </span>
              )}
              {decision.recheck && (
                <span>
                  second check <b>{decision.recheck}</b>
                </span>
              )}
            </div>
          </>
        )}
      </div>
      <div className="stg">
        <div className="sh">
          <span className="no">step 3</span>
          <h3>Account selection</h3>
        </div>
        <p>Account choice is not recorded in this decision.</p>
        {decision.sessionId && (
          <Link className="btn sm" to={`/usage?session=${encodeURIComponent(decision.sessionId)}`}>
            Receipts for this session →
          </Link>
        )}
      </div>
      <div className="stg">
        <div className="sh">
          <span className="no">step 4</span>
          <h3>Outcome</h3>
        </div>
        <span className="st">
          {outcome.state === "ok" ? "✓ ok" : outcome.state === "failed" ? "× failed" : "○ open"}
          {outcome.durationS != null && ` · ${elapsed(outcome.durationS)}`}
        </span>
        {outcome.error && <p>{outcome.error}</p>}
        <div className="kv">
          {outcome.tokensIn != null && (
            <span>
              <b>{compact(outcome.tokensIn)}</b> tokens in
            </span>
          )}
          {outcome.tokensOut != null && (
            <span>
              <b>{compact(outcome.tokensOut)}</b> tokens out
            </span>
          )}
          {outcome.match === "exact" && <span>matched by prompt hash</span>}
          {outcome.match === "ambiguous" && (
            <span>ambiguous: more than one dispatch could own this closeout</span>
          )}
        </div>
      </div>
    </div>
  );
}

export function DecisionsTab() {
  const [params] = useSearchParams();
  const session = params.get("session");
  const [period, setPeriod] = useState("24h");
  const [referenceTime] = useState(() => Date.now());
  const [taskClass, setTaskClass] = useState("");
  const [outcome, setOutcome] = useState("");
  const [segment, setSegment] = useState("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [visibleCount, setVisibleCount] = useState(30);
  const [mobileOpen, setMobileOpen] = useState(false);
  const menu = useMenu();
  const since =
    period === "24h"
      ? new Date(referenceTime - 86_400_000).toISOString()
      : new Date(referenceTime - 7 * 86_400_000).toISOString();
  const decisions = useRouteDecisions(since, taskClass, outcome);
  const base = (decisions.data?.decisions ?? []).filter(
    (row) => !session || row.sessionId === session,
  );
  const shown = base.filter(
    (row) =>
      segment === "all" || (segment === "fallbacks" ? !!row.fallback : row.abstained || !row.pick),
  );
  const selected = shown.find((row) => row.id === selectedId) ?? shown[0];
  const fallbackCount = base.filter((row) => row.fallback).length;
  const noPickCount = base.filter((row) => row.abstained || !row.pick).length;
  return (
    <>
      <div className="toolbar filters">
        <FilterSelect
          label="Time"
          value={period}
          options={[
            { label: "Last 24 hours", value: "24h" },
            { label: "Last 7 days", value: "7d" },
          ]}
          onChange={setPeriod}
        />
        <FilterSelect
          label="Class"
          value={taskClass}
          options={[
            { label: "All", value: "" },
            ...Object.keys(menu.data?.classes ?? {}).map((value) => ({ label: value, value })),
          ]}
          onChange={setTaskClass}
        />
        <FilterSelect
          label="Outcome"
          value={outcome}
          options={[
            { label: "All", value: "" },
            ...["ok", "failed", "no_pick", "open"].map((value) => ({
              label: value.replace("_", " "),
              value,
            })),
          ]}
          onChange={setOutcome}
        />
        <Seg
          value={segment}
          onChange={setSegment}
          options={[
            {
              label: "All",
              value: "all",
              count: session ? base.length : (decisions.data?.counts.total ?? base.length),
            },
            {
              label: "Fallbacks",
              value: "fallbacks",
              count: session ? fallbackCount : (decisions.data?.counts.fallbacks ?? fallbackCount),
            },
            {
              label: "No pick",
              value: "no_pick",
              count: session ? noPickCount : (decisions.data?.counts.noPick ?? noPickCount),
            },
          ]}
        />
      </div>
      {session && <p className="muted">Showing session {session}</p>}
      <div className="dsplit">
        <div className="rows dec">
          <div className="row hd">
            <span>Time</span>
            <span>Class</span>
            <span>Pick</span>
            <span>Decider</span>
            <span className="r">Outcome</span>
          </div>
          {shown.slice(0, visibleCount).map((row) => (
            <button
              type="button"
              key={row.id}
              className={`row link ${selected?.id === row.id ? "sel" : ""}`}
              onClick={() => {
                setSelectedId(row.id);
                setMobileOpen(true);
              }}
            >
              <span className="c-t num">{clock(row.ts)}</span>
              <span className="c-cls">{row.taskClass || "—"}</span>
              <span className="c-pick">
                <Pick decision={row} />
              </span>
              <span className="c-d muted">
                {row.deciderMs != null ? `${row.deciderMs} ms` : "first in list"}
              </span>
              <span className="c-out r">
                {row.outcome.state === "ok"
                  ? "✓ ok"
                  : row.outcome.state === "failed"
                    ? "× failed"
                    : "○ open"}
                {row.outcome.durationS != null && ` · ${elapsed(row.outcome.durationS)}`}
                {row.outcome.state === "failed" && row.outcome.error && ` · ${row.outcome.error}`}
              </span>
            </button>
          ))}
          {shown.length > visibleCount && (
            <button className="routing-more" onClick={() => setVisibleCount(visibleCount + 30)}>
              Show more decisions
            </button>
          )}
          {decisions.isError && <div className="row">Decisions unavailable right now.</div>}
          {!decisions.isLoading && !shown.length && (
            <div className="row">No decisions match these filters.</div>
          )}
        </div>
        {selected && (
          <aside className="routing-trace-desktop">
            <h2>Route trace</h2>
            <RouteTrace decision={selected} />
          </aside>
        )}
      </div>
      <SidePanel open={mobileOpen} onOpenChange={setMobileOpen} title="Route trace">
        {selected && <RouteTrace decision={selected} />}
      </SidePanel>
      {decisions.data?.truncated && (
        <p className="muted">
          Showing the latest {decisions.data.decisions.length} decisions; earlier ledger entries
          were truncated.
        </p>
      )}
    </>
  );
}
