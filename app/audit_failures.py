from __future__ import annotations

from collections import Counter, defaultdict


def failure_counts(rows):
    pools = defaultdict(Counter)
    classes = defaultdict(Counter)
    for row in rows:
        if row.get("status") == "success":
            continue
        pool = row.get("provider") or "unknown"
        status = row.get("upstream_status_code")
        pools[pool][str(status) if status is not None else "unknown"] += 1
        classes[pool][row.get("upstream_error_code") or row.get("error_code") or "unknown"] += 1
    return {pool: {"status_counts": dict(pools[pool]), "error_classes": dict(classes[pool])} for pool in sorted(pools)}
