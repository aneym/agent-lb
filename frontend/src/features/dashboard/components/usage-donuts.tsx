import { useMemo } from "react";

import { DonutChart } from "@/components/donut-chart";
import type { RemainingItem, SafeLineView } from "@/features/dashboard/utils";

export type UsageDonutsProps = {
	primaryItems: RemainingItem[];
	secondaryItems: RemainingItem[];
	primaryTotal: number;
	secondaryTotal: number;
	primaryCenterValue?: number;
	secondaryCenterValue?: number;
	safeLinePrimary?: SafeLineView | null;
	safeLineSecondary?: SafeLineView | null;
	/** Codex has no 5h window (OpenAI removed it) — hide the primary donut. */
	showPrimary?: boolean;
};

export function UsageDonuts({
	primaryItems,
	secondaryItems,
	primaryTotal,
	secondaryTotal,
	primaryCenterValue,
	secondaryCenterValue,
	safeLinePrimary,
	safeLineSecondary,
	showPrimary = true,
}: UsageDonutsProps) {
	const primaryChartItems = useMemo(
		() =>
			primaryItems.map((item) => ({
				id: item.accountId,
				label: item.label,
				labelSuffix: item.labelSuffix,
				isEmail: item.isEmail,
				value: item.value,
				color: item.color,
			})),
		[primaryItems],
	);
	const secondaryChartItems = useMemo(
		() =>
			secondaryItems.map((item) => ({
				id: item.accountId,
				label: item.label,
				labelSuffix: item.labelSuffix,
				isEmail: item.isEmail,
				value: item.value,
				color: item.color,
			})),
		[secondaryItems],
	);

	return (
		<div
			className={
				showPrimary ? "grid gap-4 lg:grid-cols-2" : "grid gap-4 grid-cols-1"
			}
		>
			{showPrimary ? (
				<DonutChart
					title="5-Hour Credits"
					items={primaryChartItems}
					total={primaryTotal}
					centerValue={primaryCenterValue}
					safeLine={safeLinePrimary}
					centerLayout="credits"
				/>
			) : null}
			<DonutChart
				title="Weekly Credits"
				items={secondaryChartItems}
				total={secondaryTotal}
				centerValue={secondaryCenterValue}
				safeLine={safeLineSecondary}
				centerLayout="credits"
			/>
		</div>
	);
}
