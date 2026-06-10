import { useEffect, useMemo, useRef, useState } from "react";
import {
  Cell,
  Pie,
  PieChart,
  Sector,
  type PieSectorShapeProps,
} from "recharts";

import { formatCurrency } from "@/utils/formatters";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import type { ApiKeyAccountCost } from "@/features/apis/schemas";

export type AccountCostDonutProps = {
  accountCosts: ApiKeyAccountCost[];
  totalCostUsd: number;
};

const CHART_SIZE = 152;
const CHART_MARGIN = 4;
const PIE_CX = 72;
const PIE_CY = 72;
const INNER_R = 53;
const OUTER_R = 68;
const ACTIVE_RADIUS_OFFSET = 4;
const LEGEND_VISIBLE_COUNT = 5;
const LEGEND_ROW_HEIGHT_REM = 1.75;
const LEGEND_ROW_GAP_REM = 0;

type DonutDatum = {
  id: string;
  name: string;
  value: number;
  fill: string;
};

/** Grayscale data ramp; segments cycle the 5 chart tokens (DESIGN.md). */
const SEGMENT_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

/** Quiet fill for the consumed/remaining (non-data) slice. */
const CONSUMED_COLOR = "var(--muted)";

export function AccountCostDonut({
  accountCosts,
  totalCostUsd,
}: AccountCostDonutProps) {
  const blurred = usePrivacyStore((s) => s.blurred);
  const reducedMotion = useReducedMotion();
  const [activeLegendId, setActiveLegendId] = useState<string | null>(null);
  const legendRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const { chartData, legendItems } = useMemo(() => {
    const visibleCosts = accountCosts.filter((ac) => ac.costUsd > 0);

    const items = visibleCosts.map((ac, i) => {
      const isDeleted = ac.isDeleted;
      return {
        id: isDeleted ? "__deleted__" : (ac.accountId ?? `__unknown_${i}__`),
        label: isDeleted ? "Deleted Account" : (ac.email ?? "Unknown Account"),
        isDeleted,
        value: ac.costUsd,
        color: isDeleted
          ? CONSUMED_COLOR
          : SEGMENT_COLORS[i % SEGMENT_COLORS.length],
      };
    });

    const totalValue = items.reduce((sum, item) => sum + item.value, 0);
    const remaining = Math.max(0, totalCostUsd - totalValue);

    const data: DonutDatum[] = [
      ...items.map((item) => ({
        id: item.id,
        name: item.label,
        value: item.value,
        fill: item.color,
      })),
      ...(remaining > 0
        ? [
            {
              id: "__remaining__",
              name: "__remaining__",
              value: remaining,
              fill: CONSUMED_COLOR,
            },
          ]
        : []),
    ];

    if (!data.some((d) => d.value > 0)) {
      data.length = 0;
      data.push({
        id: "__empty__",
        name: "__empty__",
        value: 1,
        fill: CONSUMED_COLOR,
      });
    }

    return { chartData: data, legendItems: items };
  }, [accountCosts, totalCostUsd]);

  useEffect(() => {
    if (!activeLegendId) {
      return;
    }

    const legendNode = legendRefs.current[activeLegendId];
    if (typeof legendNode?.scrollIntoView === "function") {
      legendNode.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }, [activeLegendId]);

  const renderDonutShape = (props: PieSectorShapeProps) => {
    const isHighlighted =
      props.isActive ||
      (props.payload as DonutDatum | undefined)?.id === activeLegendId;
    return (
      <Sector
        {...props}
        outerRadius={
          typeof props.outerRadius === "number"
            ? props.outerRadius + (isHighlighted ? ACTIVE_RADIUS_OFFSET : 0)
            : OUTER_R + (isHighlighted ? ACTIVE_RADIUS_OFFSET : 0)
        }
        stroke="var(--card)"
        strokeWidth={2}
      />
    );
  };

  return (
    <div data-testid="account-cost-panel">
      <div className="mb-5">
        <div>
          <h3 className="text-sm font-semibold">7-Day Cost by Account</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Breakdown of usage cost
          </p>
        </div>
      </div>

      <div className="flex flex-col items-center gap-4">
        <div className="flex shrink-0 flex-col items-center">
          <div className="relative h-[152px] w-[152px] overflow-visible">
            <PieChart
              width={CHART_SIZE}
              height={CHART_SIZE}
              margin={{
                top: CHART_MARGIN,
                right: CHART_MARGIN,
                bottom: CHART_MARGIN,
                left: CHART_MARGIN,
              }}
            >
              <Pie
                data={chartData}
                cx={PIE_CX}
                cy={PIE_CY}
                innerRadius={INNER_R}
                outerRadius={OUTER_R}
                startAngle={90}
                endAngle={-270}
                dataKey="value"
                stroke="var(--card)"
                strokeWidth={2}
                shape={renderDonutShape}
                isAnimationActive={!reducedMotion}
                animationDuration={600}
                animationEasing="ease-out"
                onMouseEnter={(data) => {
                  const datum = data.payload as DonutDatum | undefined;
                  if (typeof datum?.id === "string") {
                    setActiveLegendId(datum.id);
                  }
                }}
                onMouseLeave={() => setActiveLegendId(null)}
                onMouseOut={() => setActiveLegendId(null)}
              >
                {chartData.map((entry) => (
                  <Cell key={entry.id} fill={entry.fill} />
                ))}
              </Pie>
            </PieChart>
            <div className="absolute inset-[22px] flex items-center justify-center rounded-full text-center pointer-events-none">
              <div>
                <p className="text-[11px] font-medium text-muted-foreground">
                  7-Day Cost
                </p>
                <p className="font-mono text-base font-semibold tabular-nums">
                  {formatCurrency(totalCostUsd)}
                </p>
              </div>
            </div>
          </div>
        </div>
        {legendItems.length > 0 && (
          <div
            className="w-full overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
            data-testid="account-cost-legend-list"
            style={{
              maxHeight: `calc(${LEGEND_VISIBLE_COUNT} * ${LEGEND_ROW_HEIGHT_REM}rem + ${(LEGEND_VISIBLE_COUNT - 1) * LEGEND_ROW_GAP_REM}rem)`,
            }}
          >
            {legendItems.map((item, i) => {
              const isActive = activeLegendId === item.id;

              return (
                <button
                  ref={(node) => {
                    legendRefs.current[item.id] = node;
                  }}
                  type="button"
                  key={item.id}
                  className="animate-fade-in-up flex h-7 w-full items-center justify-between px-1.5 gap-3 rounded-lg border bg-transparent text-xs transition-all"
                  style={{
                    animationDelay: `${i * 75}ms`,
                    borderColor: isActive ? item.color : "transparent",
                  }}
                  onMouseEnter={() => setActiveLegendId(item.id)}
                  onMouseLeave={() => setActiveLegendId(null)}
                  onFocus={() => setActiveLegendId(item.id)}
                  onBlur={() => setActiveLegendId(null)}
                  data-active={isActive ? "true" : "false"}
                  data-testid={`account-cost-legend-${i}`}
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      aria-hidden
                      className="h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: item.color }}
                    />
                    <span className="truncate font-medium">
                      {item.isDeleted ? (
                        item.label
                      ) : blurred ? (
                        <span className="privacy-blur">{item.label}</span>
                      ) : (
                        item.label
                      )}
                    </span>
                  </div>
                  <span className="shrink-0 font-mono tabular-nums text-muted-foreground">
                    {formatCurrency(item.value)}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
