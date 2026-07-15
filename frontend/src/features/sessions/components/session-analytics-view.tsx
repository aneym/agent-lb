import { Activity, ArrowLeft } from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { AlertMessage } from "@/components/alert-message";
import { DonutChart } from "@/components/donut-chart";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { SpinnerBlock } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { RequestLog } from "@/features/dashboard/schemas";
import { ChartTooltip } from "@/features/reports/components/chart-tooltip";
import {
  useSession,
  useSessionAnalytics,
} from "@/features/sessions/hooks/use-sessions";
import {
  buildSessionTimeline,
  getSeatKey,
  getSeatLabel,
} from "@/features/sessions/session-analytics";
import type { SessionAnalyticsSeat } from "@/features/sessions/schemas";
import { getErrorMessageOrNull } from "@/utils/errors";
import {
  formatCompactNumber,
  formatCurrency,
  formatDateTimeInline,
  formatModelLabel,
  formatTokensWithCached,
} from "@/utils/formatters";

const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
] as const;

export type SessionAnalyticsViewProps = {
  sessionId: string;
  windowMinutes: number;
  onClose: () => void;
};

export function SessionAnalyticsView({
  sessionId,
  windowMinutes,
  onClose,
}: SessionAnalyticsViewProps) {
  const analyticsQuery = useSessionAnalytics(sessionId, windowMinutes);
  const detailQuery = useSession(sessionId);
  const analyticsError = getErrorMessageOrNull(analyticsQuery.error);
  const detailError = getErrorMessageOrNull(detailQuery.error);
  const analytics = analyticsQuery.data;

  if (analyticsQuery.isLoading) {
    return <SpinnerBlock />;
  }

  if (analyticsError || !analytics) {
    return (
      <div className="space-y-4">
        <BackButton onClick={onClose} />
        <AlertMessage variant="error">
          Failed to load session analytics:{" "}
          {analyticsError ?? "Unknown session"}
        </AlertMessage>
      </div>
    );
  }

  const timeline = buildSessionTimeline(analytics);
  const totalTokens =
    analytics.session.inputTokens + analytics.session.outputTokens;
  const cachedShare =
    totalTokens > 0
      ? (analytics.session.cachedInputTokens / totalTokens) * 100
      : 0;
  const durationMilliseconds = Math.max(
    0,
    new Date(analytics.session.lastSeen).getTime() -
      new Date(analytics.session.firstSeen).getTime(),
  );

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <BackButton onClick={onClose} />
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">
            Session Analytics
          </h1>
          <p className="break-all font-mono text-xs text-muted-foreground">
            {sessionId}
          </p>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        <StatTile
          label="Duration"
          value={formatDuration(durationMilliseconds)}
        />
        <StatTile
          label="Requests"
          value={formatCompactNumber(analytics.session.requests)}
        />
        <StatTile
          label="Tokens"
          value={formatTokensWithCached(
            totalTokens,
            analytics.session.cachedInputTokens,
          )}
          detail={`${cachedShare.toFixed(0)}% cached share`}
        />
        <StatTile
          label="Cost"
          value={formatCurrency(analytics.session.costUsd)}
        />
        <StatTile
          label="Errors"
          value={formatCompactNumber(analytics.session.errors)}
        />
        <StatTile
          label="Seats"
          value={formatCompactNumber(analytics.seats.length)}
        />
      </div>

      <TimelineChart data={timeline.data} models={timeline.models} />

      <div className="grid gap-4 xl:grid-cols-[minmax(320px,0.8fr)_minmax(0,1.7fr)]">
        <DonutChart
          title="Cost by Seat"
          subtitle="Model and reasoning-effort seats"
          items={analytics.seats.map((seat) => ({
            id: getSeatKey(seat),
            label: getSeatLabel(seat.model, seat.reasoningEffort),
            value: seat.costUsd,
          }))}
          total={analytics.seats.reduce(
            (total, seat) => total + seat.costUsd,
            0,
          )}
          centerValue={analytics.session.costUsd}
        />
        <SeatTable seats={analytics.seats} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <HistogramChart
          title="Latency Distribution"
          data={analytics.latencyHistogram}
          testId="latency-histogram-bars"
        />
        <HistogramChart
          title="Tokens per Request"
          data={analytics.tokensPerRequestHistogram}
          testId="tokens-histogram-bars"
        />
      </div>

      <RecentRequestsSection
        isLoading={detailQuery.isLoading}
        errorMessage={detailError}
        requests={detailQuery.data?.recentRequests ?? []}
      />
    </div>
  );
}

function BackButton({ onClick }: { onClick: () => void }) {
  return (
    <Button
      type="button"
      variant="outline"
      size="icon"
      aria-label="Back to sessions"
      onClick={onClick}
    >
      <ArrowLeft className="h-4 w-4" />
    </Button>
  );
}

function StatTile({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="text-[13px] font-medium text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 font-mono text-xl font-semibold tabular-nums tracking-tight">
        {value}
      </div>
      {detail ? (
        <div className="mt-0.5 text-xs text-muted-foreground">{detail}</div>
      ) : null}
    </div>
  );
}

function TimelineChart({
  data,
  models,
}: {
  data: ReturnType<typeof buildSessionTimeline>["data"];
  models: string[];
}) {
  return (
    <section className="rounded-xl border bg-card p-5">
      <h2 className="text-sm font-semibold">Output Tokens over Time</h2>
      <div className="mt-4 h-[260px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={data}
            margin={{ top: 5, right: 10, left: 0, bottom: 0 }}
          >
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="var(--border)"
              vertical={false}
            />
            <XAxis
              dataKey="bucketStart"
              tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(value: string) => formatDateTimeInline(value)}
              minTickGap={48}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
              tickFormatter={formatCompactNumber}
            />
            <Tooltip
              cursor={{
                stroke: "var(--foreground)",
                strokeOpacity: 0.2,
                strokeWidth: 1,
              }}
              content={<ChartTooltip formatValue={formatCompactNumber} />}
            />
            {models.map((model, index) => (
              <Area
                key={model}
                type="monotone"
                dataKey={model}
                stackId="output"
                stroke={CHART_COLORS[index]}
                strokeWidth={2}
                strokeDasharray={index === 0 ? undefined : "6 3"}
                fill={CHART_COLORS[index]}
                fillOpacity={0.12}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 1.5, fill: "var(--card)" }}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {models.length > 1 ? (
        <div className="mt-3 flex flex-wrap gap-4 text-[11px] text-muted-foreground">
          {models.map((model, index) => (
            <span key={model} className="flex items-center gap-1.5">
              <svg width="16" height="4" aria-hidden="true">
                <line
                  x1="0"
                  y1="2"
                  x2="16"
                  y2="2"
                  stroke={CHART_COLORS[index]}
                  strokeWidth="2"
                  strokeDasharray={index === 0 ? undefined : "6 3"}
                />
              </svg>
              {model === "other" ? "Other" : formatModelLabel(model, null)}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function SeatTable({ seats }: { seats: SessionAnalyticsSeat[] }) {
  return (
    <section className="overflow-hidden rounded-xl border bg-card">
      <div className="p-5 pb-3">
        <h2 className="text-sm font-semibold">Seat Usage</h2>
      </div>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Seat</TableHead>
              <TableHead>Model</TableHead>
              <TableHead>Effort</TableHead>
              <TableHead className="text-right">Requests</TableHead>
              <TableHead className="text-right">Tokens</TableHead>
              <TableHead className="text-right">Cost</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {seats.map((seat) => (
              <TableRow key={getSeatKey(seat)}>
                <TableCell className="font-medium">
                  {getSeatLabel(seat.model, seat.reasoningEffort)}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {seat.model}
                </TableCell>
                <TableCell className="text-xs">
                  {seat.reasoningEffort ?? "--"}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">
                  {formatCompactNumber(seat.requests)}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">
                  {formatTokensWithCached(
                    seat.inputTokens + seat.outputTokens,
                    seat.cachedInputTokens,
                  )}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">
                  {formatCurrency(seat.costUsd)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

function HistogramChart({
  title,
  data,
  testId,
}: {
  title: string;
  data: { label: string; count: number }[];
  testId: string;
}) {
  return (
    <section className="rounded-xl border bg-card p-5">
      <h2 className="text-sm font-semibold">{title}</h2>
      <div className="mt-4 h-[220px]" data-testid={testId}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            margin={{ top: 5, right: 10, left: 0, bottom: 0 }}
          >
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="var(--border)"
              vertical={false}
            />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              allowDecimals={false}
              tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip content={<ChartTooltip names={{ count: "Requests" }} />} />
            <Bar dataKey="count" fill="var(--chart-1)" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function RecentRequestsSection({
  isLoading,
  errorMessage,
  requests,
}: {
  isLoading: boolean;
  errorMessage: string | null;
  requests: RequestLog[];
}) {
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold">Recent Requests</h2>
      {isLoading ? (
        <SpinnerBlock />
      ) : errorMessage ? (
        <AlertMessage variant="error">
          Failed to load recent requests: {errorMessage}
        </AlertMessage>
      ) : requests.length === 0 ? (
        <EmptyState
          icon={Activity}
          title="No recent requests"
          description="No request-log rows are available for this session."
        />
      ) : (
        <div className="overflow-x-auto rounded-xl border bg-card">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Time</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Tokens</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {requests.map((request) => (
                <TableRow key={request.requestId}>
                  <TableCell className="whitespace-nowrap text-xs">
                    {formatDateTimeInline(request.requestedAt)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatModelLabel(
                      request.model,
                      request.reasoningEffort,
                      request.actualServiceTier ?? request.serviceTier,
                    )}
                  </TableCell>
                  <TableCell className="text-xs">{request.status}</TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {formatTokensWithCached(
                      request.tokens,
                      request.cachedInputTokens,
                    )}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {formatCurrency(request.costUsd)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}

function formatDuration(milliseconds: number) {
  const totalMinutes = Math.floor(milliseconds / 60_000);
  if (totalMinutes < 60) return `${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours < 24) return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const remainingHours = hours % 24;
  return remainingHours > 0 ? `${days}d ${remainingHours}h` : `${days}d`;
}
