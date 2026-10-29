from __future__ import annotations

import calendar
import time

QUIET_S = 600
MIN_GAP_S = 1800
CEILING_S = 7200
LIVE_PREFIXES = (
    "docs/",
    "openspec/",
    "scripts/",
    "bin/",
    "clients/",
    "features/",
    "artifact-manifests/",
    "frontend/",
    "app/static/",
    "config/coding-agents/",
    "config/audit/",
    ".agents/",
    ".claude/",
    ".github/",
    ".githooks/",
    "deploy/",
)
LIVE_TOP = (
    "*.md",
    "LICENSE",
    "Makefile",
    "check",
    "atoms.toml",
    ".atoms-unowned.json",
    ".filecap.json",
    ".factory-lint.toml",
    ".gitignore",
    ".dockerignore",
    ".env.example",
    "renovate.json",
    "Dockerfile",
    "docker-compose.yml",
)
RESTART_EXCEPT = ("scripts/run-agent-lb.sh",)
PENDING_KEYS = (
    "restart_pending_since",
    "restart_paths",
    "restart_count",
    "next_restart_at",
    "restart_wait_reason",
    "restart_held",
)


def epoch(stamp: str | None) -> float:
    return float(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))) if stamp else 0.0


def rel_of(line: str) -> str:
    return line.split(None, 1)[1].strip().split(" -> ", 1)[0]


def split(changes: list[str], own: set[str], held: list[str]) -> tuple[list[str], list[str], list[str]]:
    live, need = [], []
    for line in changes:
        rel = rel_of(line)
        top = "/" not in rel and (rel in LIVE_TOP or rel.endswith(".md"))
        is_live = rel not in RESTART_EXCEPT and (top or rel.startswith(LIVE_PREFIXES))
        if is_live or (rel in own and not line.startswith("*deleting")):
            live.append(rel)
        if not is_live:
            need.append(rel)
    held = sorted({*held, *(rel for rel in live if rel in need)})
    need.extend(rel for rel in held if rel not in need)
    return live, need, held


def restart_due(state: dict, now: float) -> tuple[bool, float, str]:
    quiet = epoch(state.get("main_seen_at")) + QUIET_S
    gap = epoch(state.get("restarted_at")) + MIN_GAP_S
    eligible = max(quiet, gap)
    ceiling = epoch(state.get("restart_pending_since")) + CEILING_S
    due = min(eligible, ceiling)
    reason = "ceiling" if ceiling <= eligible else "gap" if gap > quiet else "quiet"
    return now >= due, due, reason


def status_suffix(state: dict, now: float) -> str:
    text = ""
    if state.get("restart_pending_since"):
        due, when, reason = restart_due(state, now)
        paths = ", ".join(state.get("restart_paths", []))
        if state.get("restart_count", 0) > len(state.get("restart_paths", [])):
            paths += ", …"
        since = time.strftime("%H:%MZ", time.gmtime(epoch(state["restart_pending_since"])))
        next_at = time.strftime("%H:%MZ", time.gmtime(when))
        text = f"; restart pending for {state.get('restart_count', 0)} path(s) ({paths}) since {since}; next eligible {next_at} ({'due now' if due else reason})"
    if state.get("restarted_at"):
        text += "; last restart " + time.strftime("%H:%MZ", time.gmtime(epoch(state["restarted_at"])))
    return text
