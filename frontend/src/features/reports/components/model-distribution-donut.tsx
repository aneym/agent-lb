import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import type { ModelCostEntry } from "../schemas";
import { ChartTooltip } from "./chart-tooltip";

/** Grayscale data ramp; segments cycle the 5 chart tokens (DESIGN.md). */
const SEGMENT_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

function segmentColor(index: number): string {
  return SEGMENT_COLORS[index % SEGMENT_COLORS.length];
}

export type ModelDistributionDonutProps = {
  data: ModelCostEntry[];
};

export function ModelDistributionDonut({ data }: ModelDistributionDonutProps) {
  return (
    <div className="rounded-xl border bg-card p-5">
      <div className="text-sm font-semibold text-foreground">
        Distribution by Model
      </div>
      <div className="mt-4 flex items-center gap-4">
        <div className="h-[140px] w-[140px] shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="costUsd"
                nameKey="model"
                cx="50%"
                cy="50%"
                innerRadius={45}
                outerRadius={65}
                stroke="var(--card)"
                strokeWidth={2}
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={segmentColor(i)} />
                ))}
              </Pie>
              <Tooltip
                content={
                  <ChartTooltip
                    names={{ costUsd: "Cost" }}
                    formatValue={(v) => `$${v.toFixed(2)}`}
                  />
                }
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex-1 space-y-1.5 text-xs">
          {data.map((entry, i) => (
            <div
              key={entry.model}
              className="flex items-center justify-between gap-3 rounded-md px-2 py-1 hover:bg-muted/50"
            >
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                  style={{ background: segmentColor(i) }}
                />
                <span
                  className="truncate whitespace-nowrap text-foreground"
                  title={entry.model}
                >
                  {entry.model}
                </span>
              </div>
              <div className="flex shrink-0 items-center gap-3 text-right font-mono tabular-nums">
                <span className="text-muted-foreground">
                  {entry.percentage}%
                </span>
                <span className="font-medium text-foreground">
                  ${entry.costUsd.toFixed(2)}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
