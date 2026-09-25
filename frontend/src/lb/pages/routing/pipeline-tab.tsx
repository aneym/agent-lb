import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useSettings } from "@/features/settings/hooks/use-settings";
import { StickySessionsSection } from "@/features/sticky-sessions/components/sticky-sessions-section";
import { useStickySessions } from "@/lb/api";
import { clock, pace } from "@/lb/format";
import { ModelChip, Panel, Section, Seg, SidePanel } from "@/lb/kit/primitives";
import { ProviderMark } from "@/lb/kit/provider-mark";
import { providerMark } from "@/lb/kit/provider-mark-helpers";
import {
  localMidnight,
  updatePlannerMode,
  useMenu,
  usePlannerDecisions,
  usePlannerForecast,
  usePlannerSettings,
  useRouteDecisions,
  useRoutingPolicy,
  useTodaySummary,
} from "./data";

function PipelineStages() {
  const decisions = useRouteDecisions(localMidnight());
  const policy = useRoutingPolicy();
  const receipts = useTodaySummary();
  const rows = decisions.data?.decisions ?? [];
  const deciderTimes = rows
    .filter((row) => row.kind === "of_decision" && row.deciderMs != null)
    .map((row) => row.deciderMs as number)
    .sort((a, b) => a - b);
  const middle = Math.floor(deciderTimes.length / 2);
  const median = deciderTimes.length
    ? Math.round(
        (deciderTimes[middle] + deciderTimes[Math.floor((deciderTimes.length - 1) / 2)]) / 2,
      )
    : null;
  const providers =
    receipts.data?.groups
      .filter((group) => group.requests > 0 && group.provider)
      .map((group) => group.provider as string) ?? [];
  const stage = (
    no: string,
    title: string,
    description: string | null,
    live: React.ReactNode,
    key = false,
  ) => (
    <div className={`stage${key ? " key" : ""}`}>
      <span className="no">{no}</span>
      <span className="tt">{title}</span>
      {description && <span className="dd">{description}</span>}
      {live}
    </div>
  );
  const arrow = (
    <div className="arr" aria-hidden="true">
      →
    </div>
  );
  return (
    <div className="flow">
      {stage(
        "1 · task",
        "A task comes in",
        "Each request carries its kind of task, a session and a key.",
        <div className="live">
          <span>
            <b>{(decisions.data?.counts.total ?? rows.length).toLocaleString()}</b> tasks today
            {decisions.data?.truncated && " (latest 500)"}
          </span>
        </div>,
      )}
      {arrow}
      {stage(
        "2 · Open Factory",
        "Picks a model and agent",
        "Goes down the task's list of options, skips any that are over pace or unhealthy, and asks a small, fast decider model to choose.",
        <div className="live">
          <span>
            policy{" "}
            <b>
              {policy.data?.activeVersion != null ? `v${policy.data.activeVersion}` : "unavailable"}
            </b>
          </span>
          <span>
            <b>
              {decisions.data?.counts.noPick ??
                rows.filter((row) => row.abstained || !row.pick).length}
            </b>{" "}
            with no pick
          </span>
          <span>
            decider median <b>{median == null ? "no decider runs yet" : `${median} ms`}</b>
          </span>
        </div>,
        true,
      )}
      {arrow}
      {stage(
        "3 · agent-lb",
        "Picks an account in the pool",
        "Keeps a session on the account it started on, else weighs accounts by remaining capacity.",
        <div className="live">
          <span>
            <b>{receipts.data?.totals.requests.toLocaleString() ?? "—"}</b> requests today
          </span>
          <span>
            <b>{decisions.data?.counts.fallbacks ?? rows.filter((row) => row.fallback).length}</b>{" "}
            fallbacks
          </span>
        </div>,
        true,
      )}
      {arrow}
      {stage(
        "4 · upstream",
        "Serves it and writes a receipt",
        null,
        <>
          <div className="logos">
            {[...new Set(providers)].map((provider) => (
              <ProviderMark
                key={provider}
                id={provider === "anthropic" ? "claude" : providerMark(provider)}
                size={16}
              />
            ))}
          </div>
          <div className="live">
            <span>
              median{" "}
              <b>
                {receipts.data?.totals.p50LatencyMs != null
                  ? `${(receipts.data.totals.p50LatencyMs / 1000).toFixed(1)} s`
                  : "—"}
              </b>
            </span>
            <span>
              <b>{receipts.data ? `${(receipts.data.totals.errorRate * 100).toFixed(1)}%` : "—"}</b>{" "}
              errors
            </span>
          </div>
        </>,
      )}
    </div>
  );
}

function MenuSection() {
  const menu = useMenu();
  const decisions = useRouteDecisions(localMidnight());
  const rows = Object.entries(menu.data?.classes ?? {}).filter(([, value]) => !value.driver);
  return (
    <Section title="Menu" aside="What each kind of task can run on right now, in order">
      <div className="rows menu">
        <div className="row hd">
          <span>Task</span>
          <span>
            Options in order (the first available one wins unless the decider picks another)
          </span>
          <span className="r">Picks today</span>
        </div>
        {rows.map(([task, value]) => (
          <div className="row" key={task}>
            <div className="c-cls cls">
              <span className="n">{task}</span>
            </div>
            <div className="c-chain">
              <div className="chain">
                {value.seats.map((seat, index) => (
                  <span className="routing-option" key={`${seat.seat}-${index}`}>
                    {index > 0 && <span className="sep">›</span>}
                    <ModelChip
                      model={seat.model}
                      alias={seat.alias ?? undefined}
                      seat={seat.seat}
                    />
                  </span>
                ))}
              </div>
              {value.excluded.length > 0 && (
                <div className="routing-excluded">
                  {value.excluded.map((excluded, index) => (
                    <ModelChip
                      key={`${excluded.seat}-${index}`}
                      model={excluded.alias || excluded.seat}
                      seat={excluded.seat}
                      excluded
                    />
                  ))}
                </div>
              )}
              {value.excluded.length > 0 && (
                <div className="why">
                  {value.excluded.map((excluded, index) => (
                    <span key={index}>
                      × {excluded.alias || excluded.seat}: {excluded.reason}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <span className="c-p r num">
              {decisions.data?.decisions.filter((row) => row.taskClass === task && row.pick)
                .length ?? "—"}
            </span>
          </div>
        ))}
        {menu.isError && <div className="row">Menu unavailable right now.</div>}
      </div>
    </Section>
  );
}

function PaceLimits() {
  const menu = useMenu();
  const displayNames: Record<string, string> = {
    "anthropic-general": "Anthropic general",
    "openai-codex": "OpenAI Codex",
    cursor: "Cursor",
    devin: "Devin",
    glm: "GLM",
    kimi: "Kimi",
  };
  const classes = Object.entries(menu.data?.classes ?? {}).filter(([, task]) => !task.driver);
  const admittedClasses = (pool: string) =>
    classes
      .filter(([, task]) => task.seats.some((seat) => seat.pool === pool))
      .map(([name]) => name);
  const admission = (pool: string, status?: string) => {
    if (status === "exhausted" || status === "unavailable") return "No work until capacity returns";
    const excludedByPace = classes.some(([, task]) =>
      task.excluded.some((entry) => entry.reason.includes(`pool ${pool} behind pace`)),
    );
    if (!excludedByPace) return "All work";
    const admitted = admittedClasses(pool);
    return admitted.length ? `Admits: ${admitted.join(", ")}` : "No options available";
  };
  return (
    <section>
      <div className="sec-h">
        <h2>Pace limits now</h2>
      </div>
      <div className="rows adm">
        {Object.entries(menu.data?.pools ?? {})
          .filter(([name]) => name !== "anthropic-fable")
          .map(([name, pool]) => (
            <div className="row" key={name}>
              <span className="c-who who">
                <ProviderMark
                  id={
                    name === "anthropic-general"
                      ? "claude"
                      : name === "openai-codex"
                        ? "codex"
                        : providerMark(name)
                  }
                  size={20}
                />
                <span className="n">{displayNames[name] ?? name}</span>
              </span>
              <span className="c-st">
                {pool.weeklyPacePercent == null ? "No weekly cap" : pace(pool.weeklyPacePercent)}
              </span>
              <span className="c-gate muted">{admission(name, pool.status)}</span>
            </div>
          ))}
      </div>
    </section>
  );
}

function AccountSelection() {
  const { settingsQuery, updateSettingsMutation } = useSettings();
  const sticky = useStickySessions();
  const planner = usePlannerSettings();
  const plannerDecisions = usePlannerDecisions();
  const forecast = usePlannerForecast();
  const queryClient = useQueryClient();
  const [showSessions, setShowSessions] = useState(false);
  const [referenceTime] = useState(() => Date.now());
  const [pendingStrategy, setPendingStrategy] = useState<string | null>(null);
  const plannerMutation = useMutation({
    mutationFn: (mode: string) => updatePlannerMode(planner.data!, mode),
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: ["routing", "planner-settings"],
      }),
    onError: (error: Error) => toast.error(error.message),
  });
  const settings = settingsQuery.data;
  const strategies = [
    { label: "Capacity weighted", value: "capacity_weighted" },
    { label: "Fill first", value: "fill_first" },
    { label: "Round robin", value: "round_robin" },
  ];
  if (settings && !strategies.some((s) => s.value === settings.routingStrategy))
    strategies.push({
      label: settings.routingStrategy.replaceAll("_", " "),
      value: settings.routingStrategy,
    });
  const today =
    plannerDecisions.data?.filter(
      (d) =>
        new Date(d.createdAt).toDateString() === new Date().toDateString() && d.action !== "no_op",
    ).length ?? 0;
  const next =
    plannerDecisions.data
      ?.map((d) => d.scheduledAt)
      .filter((time): time is string => !!time && new Date(time).getTime() > referenceTime)
      .sort()[0] ?? forecast.data?.peakSlotStart;
  return (
    <section>
      <div className="sec-h">
        <h2>Account selection</h2>
        <span className="aside">Step 3 settings</span>
      </div>
      <Panel>
        <dl className="dl">
          <div>
            <dt>Strategy</dt>
            <dd>
              <Seg
                options={strategies}
                value={settings?.routingStrategy ?? ""}
                onChange={setPendingStrategy}
              />
              {pendingStrategy && (
                <div className="routing-confirm" role="dialog" aria-label="Confirm strategy">
                  <p>Applies to new sessions.</p>
                  <button className="btn sm" onClick={() => setPendingStrategy(null)}>
                    Cancel
                  </button>{" "}
                  <button
                    className="btn sm"
                    disabled={updateSettingsMutation.isPending}
                    onClick={() => {
                      updateSettingsMutation.mutate({
                        routingStrategy: pendingStrategy as NonNullable<
                          typeof settings
                        >["routingStrategy"],
                      });
                      setPendingStrategy(null);
                    }}
                  >
                    Apply
                  </button>
                </div>
              )}
            </dd>
          </div>
          <div>
            <dt>Sticky sessions</dt>
            <dd>
              <span className="muted">
                {stickyUntil(settings?.stickyReallocationPrimaryBudgetThresholdPct)}
              </span>
              <button
                className={`toggle ${settings?.stickyThreadsEnabled ? "on" : ""}`}
                role="switch"
                aria-label="Sticky sessions"
                aria-checked={!!settings?.stickyThreadsEnabled}
                disabled={!settings || updateSettingsMutation.isPending}
                onClick={() =>
                  updateSettingsMutation.mutate({
                    stickyThreadsEnabled: !settings?.stickyThreadsEnabled,
                  })
                }
              />
            </dd>
          </div>
          <div>
            <dt>Sessions held now</dt>
            <dd>
              <span className="num">{sticky.data?.total ?? "—"}</span>
              <button className="btn sm" onClick={() => setShowSessions(true)}>
                Review…
              </button>
            </dd>
          </div>
          <div>
            <dt>When an account hits its limit</dt>
            <dd>
              <span className="muted">
                Next account in the pool, then the next option in the list
              </span>
            </dd>
          </div>
          <div>
            <dt>Warm-up planner</dt>
            <dd>
              <span className="muted">
                {today} today{next && ` · next ${clock(next)}`}
              </span>
              <button
                className={`toggle ${planner.data?.mode !== "off" && planner.data ? "on" : ""}`}
                role="switch"
                aria-label="Warm-up planner"
                aria-checked={!!planner.data && planner.data.mode !== "off"}
                disabled={!planner.data || plannerMutation.isPending}
                onClick={() =>
                  plannerMutation.mutate(planner.data?.mode === "off" ? "suggest" : "off")
                }
              />
            </dd>
          </div>
        </dl>
      </Panel>
      <SidePanel open={showSessions} onOpenChange={setShowSessions} title="Sessions held now">
        <StickySessionsSection />
      </SidePanel>
    </section>
  );
}

export function PipelineTab() {
  return (
    <>
      <PipelineStages />
      <MenuSection />
      <div className="two sec">
        <PaceLimits />
        <AccountSelection />
      </div>
    </>
  );
}

function stickyUntil(usedThresholdPct: number | null | undefined): string {
  if (usedThresholdPct == null) return "Keep sessions on their current account";
  const left = Math.round(100 - usedThresholdPct);
  return left <= 0 ? "until a limit runs out" : `until a limit falls below ${left}%`;
}
