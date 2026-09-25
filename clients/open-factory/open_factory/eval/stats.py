"""Interval estimates for eval results. No single-number claims (PROGRAM.md D8).

Trials are clustered by task: attempts on one task are not independent, so every bootstrap
resamples tasks first and attempts within each drawn task second.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence

Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    index = q * (len(sorted_values) - 1)
    low, high = math.floor(index), math.ceil(index)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (index - low)


def _resample(by_task: Mapping[str, Sequence[float]], tasks: Sequence[str], rng: random.Random) -> list[float]:
    drawn: list[float] = []
    for _ in tasks:
        attempts = by_task[rng.choice(tasks)]
        drawn.extend(rng.choice(attempts) for _ in attempts)
    return drawn


def cluster_bootstrap(
    by_task: Mapping[str, Sequence[float]],
    statistic: Callable[[Sequence[float]], float] = lambda xs: sum(xs) / len(xs),
    *,
    reps: int = 10_000,
    seed: int = 0,
) -> dict[str, float]:
    """Point estimate and 95% interval of `statistic` over all attempts, clustered by task."""
    tasks = [task for task, attempts in by_task.items() if attempts]
    if not tasks:
        raise ValueError("no attempts")
    rng = random.Random(seed)
    point = statistic([value for task in tasks for value in by_task[task]])
    draws = sorted(statistic(_resample(by_task, tasks, rng)) for _ in range(reps))
    return {"estimate": point, "low": _percentile(draws, 0.025), "high": _percentile(draws, 0.975), "tasks": len(tasks)}


def paired_difference(
    arm_a: Mapping[str, Sequence[float]],
    arm_b: Mapping[str, Sequence[float]],
    *,
    reps: int = 10_000,
    seed: int = 0,
) -> dict[str, float]:
    """Mean(a) - mean(b) over the tasks both arms ran, resampling shared tasks as pairs."""
    tasks = sorted(task for task in arm_a if arm_a[task] and arm_b.get(task))
    if not tasks:
        raise ValueError("no shared tasks")

    def task_means(arm: Mapping[str, Sequence[float]], task: str, rng: random.Random | None) -> float:
        attempts = arm[task]
        values = attempts if rng is None else [rng.choice(attempts) for _ in attempts]
        return sum(values) / len(values)

    point = sum(task_means(arm_a, t, None) - task_means(arm_b, t, None) for t in tasks) / len(tasks)
    rng = random.Random(seed)
    draws = []
    for _ in range(reps):
        picked = [rng.choice(tasks) for _ in tasks]
        draws.append(sum(task_means(arm_a, t, rng) - task_means(arm_b, t, rng) for t in picked) / len(picked))
    draws.sort()
    low, high = _percentile(draws, 0.025), _percentile(draws, 0.975)
    return {"estimate": point, "low": low, "high": high, "tasks": len(tasks), "excludes_zero": low > 0 or high < 0}
