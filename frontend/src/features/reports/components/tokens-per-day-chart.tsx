import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { DailyReportRow } from "../schemas";
import { ChartTooltip } from "./chart-tooltip";

export type TokensPerDayChartProps = {
  data: DailyReportRow[];
};

function formatTokens(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return String(v);
}

export function TokensPerDayChart({ data }: TokensPerDayChartProps) {
  const chartData = data.map((d) => ({
    date: d.date.slice(5),
    input: d.inputTokens,
    output: d.outputTokens,
  }));

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="text-sm font-semibold text-foreground">Tokens by Day</div>
      <div className="mt-4 h-[200px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={chartData}
            margin={{ top: 5, right: 10, left: 0, bottom: 0 }}
          >
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="var(--border)"
              vertical={false}
            />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
              tickFormatter={formatTokens}
            />
            <Tooltip
              cursor={{
                stroke: "var(--foreground)",
                strokeOpacity: 0.2,
                strokeWidth: 1,
              }}
              content={
                <ChartTooltip
                  names={{ input: "Input", output: "Output" }}
                  formatValue={(v) => formatTokens(v)}
                />
              }
            />
            {/* Two series: differentiated by grayscale ramp step AND dash pattern (DESIGN.md). */}
            <Area
              type="monotone"
              dataKey="input"
              stroke="var(--chart-1)"
              strokeWidth={2}
              fill="var(--chart-1)"
              fillOpacity={0.08}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 1.5, fill: "var(--card)" }}
            />
            <Area
              type="monotone"
              dataKey="output"
              stroke="var(--chart-2)"
              strokeWidth={2}
              strokeDasharray="6 3"
              fill="var(--chart-2)"
              fillOpacity={0.06}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 1.5, fill: "var(--card)" }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-3 flex items-center gap-4 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <svg width="16" height="4" aria-hidden="true">
            <line
              x1="0"
              y1="2"
              x2="16"
              y2="2"
              stroke="var(--chart-1)"
              strokeWidth="2"
            />
          </svg>
          Input
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="16" height="4" aria-hidden="true">
            <line
              x1="0"
              y1="2"
              x2="16"
              y2="2"
              stroke="var(--chart-2)"
              strokeWidth="2"
              strokeDasharray="4 2"
            />
          </svg>
          Output
        </span>
      </div>
    </div>
  );
}
