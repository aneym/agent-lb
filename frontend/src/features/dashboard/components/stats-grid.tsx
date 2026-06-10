import { SparklineChart } from "@/components/sparkline-chart";
import type { DashboardStat } from "@/features/dashboard/utils";
import { cn } from "@/lib/utils";

export type StatsGridProps = {
  stats: DashboardStat[];
};

export function StatsGrid({ stats }: StatsGridProps) {
  const columnsClass = stats.length >= 5 ? "xl:grid-cols-5" : "xl:grid-cols-4";

  return (
    <div className={cn("grid gap-3 sm:grid-cols-2", columnsClass)}>
      {stats.map((stat, index) => {
        const Icon = stat.icon;
        return (
          <div
            key={stat.label}
            className="animate-fade-in-up card-hover rounded-xl border bg-card p-4"
            style={{ animationDelay: `${index * 75}ms` }}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[13px] font-medium text-muted-foreground">
                {stat.label}
              </span>
              <Icon
                className="h-4 w-4 shrink-0 text-muted-foreground"
                aria-hidden="true"
              />
            </div>
            <div className="mt-1">
              <p className="font-mono text-[1.625rem] font-semibold tabular-nums tracking-[-0.02em]">
                {stat.value}
              </p>
              {stat.meta ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  {stat.meta}
                </p>
              ) : null}
            </div>
            {stat.trend.length > 0 ? (
              <div className="mt-1">
                <SparklineChart
                  data={stat.trend}
                  color={stat.trendColor}
                  index={index}
                />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
