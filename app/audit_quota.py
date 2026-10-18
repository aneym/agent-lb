from __future__ import annotations

import tomllib
from bisect import bisect_left
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from app.audit_pricing import price_row, token_classes
from app.audit_transcripts import RULE_PATH


def quota_allocation(rows, snapshots, since, until):

    with (RULE_PATH.parent / "openai_quota_weights.toml").open("rb") as stream:
        openai_weights = tomllib.load(stream)

    weights_by_row = {}

    def row_weight(row):
        weight = weights_by_row.get(id(row))
        if weight is None:
            weight = weights_by_row[id(row)] = receipt_weight(row)
        return weight

    def receipt_weight(row):
        tokens = token_classes(row)
        if row["provider"] == "openai":
            return (
                tokens["fresh_input"]
                + openai_weights["c_cached"] * tokens["cache_read"]
                + openai_weights["c_out"] * tokens["output"]
            )
        usd = price_row(row)[0]
        return (
            usd
            if usd > 0
            else (tokens["fresh_input"] + tokens["cache_write"] + tokens["output"] + 0.1 * tokens["cache_read"])
        )

    def utc(stamp):
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)

    def bin_start(stamp, hours=1):
        stamp = utc(stamp)
        return stamp.replace(hour=stamp.hour // hours * hours, minute=0, second=0, microsecond=0)

    grouped = defaultdict(list)
    for snap in snapshots:
        minutes = snap.get("window_minutes")
        provider = snap["provider"]
        weekly = (provider == "anthropic" and snap["window"] == "secondary") or (
            provider == "openai" and minutes == 10080 and snap["window"] == "primary"
        )
        short = provider == "anthropic" and minutes == 300 and snap["window"] == "primary"
        if weekly or short:
            grouped[(provider, snap["account_id"], "weekly" if weekly else "5h")].append(snap)
    receipt_groups = defaultdict(list)
    for index, row in enumerate(rows):
        receipt_groups[(row["provider"], row["account_id"])].append((index, row))
    allocated = [dict(weekly=0.0, **{"5h": 0.0}) for _ in rows]
    missing, accounts, calibration = [], [], defaultdict(list)
    binned_calibration = defaultdict(list)
    quanta: dict[tuple[str, str], float] = {}
    for (provider, account, window_name), history in grouped.items():
        history.sort(key=lambda v: v["recorded_at"])
        history = [v for v in history if v["used_percent"] is not None and v["reset_at"] is not None]
        if not history:
            continue
        indexed = receipt_groups[(provider, account)]
        times = [utc(row["requested_at"]) for _, row in indexed]
        quota_bins = defaultdict(float)
        receipt_bins = defaultdict(list)
        for index, row in indexed:
            stamp = utc(row["requested_at"])
            if since <= stamp < until and row.get("status", "success") == "success":
                receipt_bins[bin_start(stamp)].append((index, row))
        maximum, previous, reset, previous_used = 0.0, None, None, None
        used, capacity = 0.0, 0.0
        periods = set()
        observed = False
        for snap in history:
            stamp = utc(snap["recorded_at"])
            end = datetime.fromtimestamp(snap["reset_at"], UTC)
            period_length = timedelta(minutes=10080 if window_name == "weekly" else 300)
            current_used = float(snap["used_percent"])
            credit_reset = previous_used is not None and (
                previous_used - current_used > 50 or (current_used <= 5 and previous_used >= 20)
            )
            if reset is None or abs((end - reset).total_seconds()) >= 3600 or credit_reset:
                maximum = 0.0
                reset = end
            delta = max(0.0, float(snap["used_percent"]) - maximum)
            maximum = max(maximum, float(snap["used_percent"]))
            if since <= stamp <= until:
                observed = True
                periods.add((reset - period_length, reset))
            if previous is not None and since < stamp <= until:
                if delta > 0:
                    key = (provider, window_name)
                    prior = quanta.get(key)
                    if prior is None or delta < prior:
                        quanta[key] = delta
                used += delta
                quota_bins[bin_start(stamp - timedelta(microseconds=1))] += delta
                left = bisect_left(times, max(previous, since))
                while left < len(times) and times[left] <= max(previous, since):
                    left += 1
                right = bisect_left(times, stamp)
                while right < len(times) and times[right] <= stamp:
                    right += 1
                candidates = [
                    (index, row)
                    for index, row in indexed[left:right]
                    if provider != "openai" or row.get("status", "success") == "success"
                ]
                weights = [row_weight(row) for _, row in candidates]
                total = sum(weights)
                if total:
                    if provider != "openai":
                        remainder = delta
                        for (index, _), weight in zip(candidates[:-1], weights[:-1], strict=True):
                            part = delta * weight / total
                            allocated[index][window_name] += part
                            remainder -= part
                        allocated[candidates[-1][0]][window_name] += remainder
                    calibration[(provider, window_name, "all")].append((total, delta))
                    models = defaultdict(float)
                    for (_, row), weight in zip(candidates, weights, strict=True):
                        models[row["model"] or "unknown"] += weight
                    for model, weight in models.items():
                        if not weight:
                            continue
                        calibration[(provider, window_name, model)].append((weight, delta * weight / total))
                elif delta and provider != "openai":
                    missing.append(
                        {"provider": provider, "account_id": account, "window": window_name, "points": delta}
                    )
            previous = stamp
            previous_used = current_used
        if provider == "openai":
            for stamp, delta in quota_bins.items():
                candidates = receipt_bins[stamp]
                weights = [row_weight(row) for _, row in candidates]
                total = sum(weights)
                if total:
                    remainder = delta
                    for (index, _), weight in zip(candidates[:-1], weights[:-1], strict=True):
                        part = delta * weight / total
                        allocated[index][window_name] += part
                        remainder -= part
                    allocated[candidates[-1][0]][window_name] += remainder
                elif delta:
                    missing.append(
                        {"provider": provider, "account_id": account, "window": window_name, "points": delta}
                    )
        bin_weights = {
            stamp: sum(row_weight(row) for _, row in candidates) for stamp, candidates in receipt_bins.items()
        }
        for hours in (1, 3, 6):
            bins = defaultdict(lambda: [0.0, 0.0])
            for stamp, weight in bin_weights.items():
                bins[bin_start(stamp, hours)][0] += weight
            for stamp, delta in quota_bins.items():
                bins[bin_start(stamp, hours)][1] += delta
            binned_calibration[(provider, window_name, hours)].extend(
                (stamp, weight, points) for stamp, (weight, points) in bins.items()
            )
        if observed:
            in_window = [
                snap["recorded_at"].replace(tzinfo=UTC)
                for snap in history
                if since <= snap["recorded_at"].replace(tzinfo=UTC) <= until
            ]
            presence = (max(in_window) - min(in_window)).total_seconds() if in_window else 0.0
            capacity = 100 * presence / (7 * 86400 if window_name == "weekly" else 5 * 3600)
            latest = history[-1]
            accounts.append(
                {
                    "provider": provider,
                    "account_id": account,
                    "window": window_name,
                    "quota_points": used,
                    "capacity_points": capacity,
                    "latest_used_percent": latest["used_percent"],
                    "headroom_now": max(0.0, 100 - latest["used_percent"])
                    if latest["reset_at"] > until.timestamp()
                    else 0.0,
                    "reset_at": latest["reset_at"],
                }
            )
    providers = {}
    for account in accounts:
        key = account["provider"] + ":" + account["window"]
        summary = providers.setdefault(
            key, {"accounts": 0, "quota_points": 0.0, "capacity_points": 0.0, "headroom_now": 0.0}
        )
        summary["accounts"] += 1
        summary["quota_points"] += account["quota_points"]
        summary["capacity_points"] += account["capacity_points"]
        summary["headroom_now"] += account["headroom_now"]
    for key, summary in providers.items():
        summary["headroom_points"] = summary["capacity_points"] - summary["quota_points"]
        summary["account_weeks" if key.endswith("weekly") else "5h_windows_used"] = summary["quota_points"] / 100
        summary["fleet_share"] = (
            summary["quota_points"] / summary["capacity_points"] if summary["capacity_points"] else 0
        )
        summary["sustainable_points_per_week"] = summary["accounts"] * 100 if key.endswith("weekly") else None
        sustainable = summary["accounts"] * 100 * (until - since).total_seconds() / (7 * 86400)
        summary["used_over_sustainable"] = summary["quota_points"] / sustainable if sustainable else 0
    fits = []
    for (provider, window_name, model), pairs in calibration.items():
        slope = sum(x * y for x, y in pairs) / sum(x * x for x, _ in pairs)
        mean = sum(y for _, y in pairs) / len(pairs)
        variance = sum((y - mean) ** 2 for _, y in pairs)
        ratios = [y / x for x, y in pairs]
        fits.append(
            {
                "provider": provider,
                "window": window_name,
                "model": model,
                "intervals": len(pairs),
                "points_per_weight": slope,
                "ratio_min": min(ratios),
                "ratio_max": max(ratios),
                "r_squared": 1 - sum((y - slope * x) ** 2 for x, y in pairs) / variance if variance > 1e-20 else None,
            }
        )
    for (provider, window_name, hours), bins in binned_calibration.items():
        training = [(x, y) for stamp, x, y in bins if stamp.day % 2 == 0]
        holdout = [(x, y) for stamp, x, y in bins if stamp.day % 2 == 1]
        denominator = sum(x * x for x, _ in training)
        slope = sum(x * y for x, y in training) / denominator if denominator else None
        mean = sum(y for _, y in holdout) / len(holdout) if holdout else 0.0
        variance = sum((y - mean) ** 2 for _, y in holdout)
        holdout_points = sum(y for _, y in holdout)
        holdout_predicted_points = slope * sum(x for x, _ in holdout) if slope is not None else None
        holdout_total_error = (
            (holdout_predicted_points - holdout_points) / holdout_points
            if slope is not None and holdout_points
            else None
        )
        holdout_points_per_bin = holdout_points / len(holdout) if holdout else None
        quantum_points = quanta.get((provider, window_name))
        if quantum_points is not None:
            quantum_points = round(quantum_points, 4)
        quantized = (
            quantum_points is not None
            and holdout_points_per_bin is not None
            and holdout_points_per_bin < 3 * quantum_points
        )
        fit = {
            "provider": provider,
            "window": window_name,
            "model": "all",
            "bin_hours": hours,
            "intervals": len(bins),
            "training_intervals": len(training),
            "holdout_intervals": len(holdout),
            "points_per_weight": slope,
            "holdout_r2": 1 - sum((y - slope * x) ** 2 for x, y in holdout) / variance
            if slope is not None and variance > 1e-20
            else None,
            "quantum_points": quantum_points,
            "holdout_points": holdout_points,
            "holdout_predicted_points": holdout_predicted_points,
            "holdout_total_error": holdout_total_error,
            "holdout_points_per_bin": holdout_points_per_bin,
            "quantized": quantized,
        }
        if quantized:
            fit["note"] = (
                "bins average under 3 quota steps; holdout_r2 is mostly rounding noise, read holdout_total_error"
            )
        fits.append(fit)
    return allocated, {"providers": providers, "accounts": accounts, "calibration": fits, "unattributed": missing}
