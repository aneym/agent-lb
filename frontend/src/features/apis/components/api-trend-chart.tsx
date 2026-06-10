import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useReducedMotion } from "@/hooks/use-reduced-motion";
import type { ApiKeyTrendPoint } from "@/features/apis/schemas";
import {
  formatChartDateTime,
  formatCompactNumber,
  formatCurrency,
} from "@/utils/formatters";

type MergedPoint = {
  t: string;
  cost: number;
  tokens: number;
};

function mergePoints(
  cost: ApiKeyTrendPoint[],
  tokens: ApiKeyTrendPoint[],
): MergedPoint[] {
  const costMap = new Map(cost.map((p) => [p.t, p.v]));
  const tokensMap = new Map(tokens.map((p) => [p.t, p.v]));

  const allTimes = new Set([...costMap.keys(), ...tokensMap.keys()]);
  if (allTimes.size === 0) return [];

  return Array.from(allTimes)
    .sort()
    .map((t) => ({
      t,
      cost: costMap.get(t) ?? 0,
      tokens: tokensMap.get(t) ?? 0,
    }));
}

function formatXTick(isoStr: string): string {
  const d = new Date(isoStr);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatCostTick(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return "<$0.01";
  return `$${value.toFixed(2)}`;
}

function formatTokenTick(value: number): string {
  if (value === 0) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(0)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}K`;
  return String(value);
}

const SERIES_META: Record<
  string,
  { label: string; formatter: (v: number) => string }
> = {
  cost: { label: "Cost", formatter: formatCurrency },
  tokens: { label: "Tokens", formatter: (v) => formatCompactNumber(v) },
};

type ChartTooltipPayloadEntry = {
  dataKey?: string | number;
  value?: number;
  color?: string;
};

type ChartTooltipProps = {
  active?: boolean;
  payload?: ChartTooltipPayloadEntry[];
  label?: string;
};

function CustomTooltip({ active, payload, label }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const heading = formatChartDateTime(label as string);
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-popover-foreground shadow-md">
      <p className="mb-1 text-[11px] text-muted-foreground">{heading}</p>
      {payload.map((entry: ChartTooltipPayloadEntry) => {
        const meta = SERIES_META[entry.dataKey as string];
        return (
          <div key={entry.dataKey} className="flex items-center gap-2 text-xs">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: entry.color }}
            />
            <span className="text-muted-foreground">{meta?.label}</span>
            <span className="ml-auto font-mono font-medium tabular-nums">
              {meta?.formatter(entry.value ?? 0)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

const CHART_MARGIN = { top: 4, right: 8, bottom: 0, left: 0 } as const;

export type ApiTrendChartProps = {
  cost: ApiKeyTrendPoint[];
  tokens: ApiKeyTrendPoint[];
};

export function ApiTrendChart({ cost, tokens }: ApiTrendChartProps) {
  const reducedMotion = useReducedMotion();
  // Grayscale data ramp tokens (DESIGN.md): series separate by gray step + dash.
  const c1 = "var(--chart-1)";
  const c2 = "var(--chart-3)";
  const data = useMemo(() => mergePoints(cost, tokens), [cost, tokens]);

  const maxTokens = useMemo(
    () => Math.max(...data.map((d) => d.tokens), 1),
    [data],
  );
  const maxCost = useMemo(
    () => Math.max(...data.map((d) => d.cost), 0.01),
    [data],
  );

  if (data.length === 0) {
    return (
      <div className="flex h-[280px] items-center justify-center text-xs text-muted-foreground">
        No trend data available
      </div>
    );
  }

  const tokenTicks = [0, maxTokens * 0.5, maxTokens];
  const costTicks = [0, maxCost * 0.5, maxCost];

  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart data={data} margin={CHART_MARGIN}>
        <CartesianGrid
          strokeDasharray="3 3"
          vertical={false}
          stroke="var(--border)"
        />
        <XAxis
          dataKey="t"
          tickFormatter={formatXTick}
          tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          minTickGap={50}
          dy={4}
        />
        <YAxis
          yAxisId="tokens"
          orientation="left"
          domain={[0, maxTokens * 1.1]}
          ticks={tokenTicks}
          tickFormatter={formatTokenTick}
          tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          width={48}
        />
        <YAxis
          yAxisId="cost"
          orientation="right"
          domain={[0, maxCost * 1.1]}
          ticks={costTicks}
          tickFormatter={formatCostTick}
          tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          width={48}
        />
        <Tooltip
          content={<CustomTooltip />}
          cursor={{
            stroke: "var(--foreground)",
            strokeOpacity: 0.2,
            strokeWidth: 1,
          }}
        />
        {/* Two series: grayscale ramp steps + dash pattern on the second (DESIGN.md). */}
        <Area
          yAxisId="tokens"
          type="monotone"
          dataKey="tokens"
          stroke={c2}
          strokeWidth={1.5}
          strokeDasharray="6 3"
          fill={c2}
          fillOpacity={0.06}
          dot={false}
          activeDot={{ r: 3, strokeWidth: 1.5, fill: "var(--card)" }}
          isAnimationActive={!reducedMotion}
          animationDuration={500}
        />
        <Area
          yAxisId="cost"
          type="monotone"
          dataKey="cost"
          stroke={c1}
          strokeWidth={1.5}
          fill={c1}
          fillOpacity={0.08}
          dot={false}
          activeDot={{ r: 3, strokeWidth: 1.5, fill: "var(--card)" }}
          isAnimationActive={!reducedMotion}
          animationDuration={500}
          animationBegin={100}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
