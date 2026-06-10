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

export type CostPerDayChartProps = {
  data: DailyReportRow[];
};

export function CostPerDayChart({ data }: CostPerDayChartProps) {
  const chartData = data.map((d) => ({
    date: d.date.slice(5),
    cost: d.costUsd,
  }));

  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="text-sm font-semibold text-foreground">Cost by Day</div>
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
              tickFormatter={(v: number) => `$${v}`}
            />
            <Tooltip
              cursor={{
                stroke: "var(--foreground)",
                strokeOpacity: 0.2,
                strokeWidth: 1,
              }}
              content={
                <ChartTooltip
                  names={{ cost: "Cost" }}
                  formatValue={(v) => `$${v.toFixed(2)}`}
                />
              }
            />
            <Area
              type="monotone"
              dataKey="cost"
              stroke="var(--chart-1)"
              strokeWidth={2}
              fill="var(--chart-1)"
              fillOpacity={0.08}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 1.5, fill: "var(--card)" }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
