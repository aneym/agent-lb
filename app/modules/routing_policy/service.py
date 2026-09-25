from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.db.models import RoutingPolicyVersion
from app.modules.routing_policy.repository import RoutingPolicyRepository
from app.modules.routing_policy.schemas import PolicyCounts, PolicyDetail, PolicyDiff, PolicySummary

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _live_paths() -> tuple[Path, Path]:
    managed = Path.home() / ".agent-lb/managed/coding-agents/routing-table.json"
    table = (
        Path(os.environ["ROUTE_TABLE"]).expanduser()
        if os.environ.get("ROUTE_TABLE")
        else (managed if managed.is_file() else _REPO_ROOT / "config/coding-agents/routing-table.json")
    )
    if os.environ.get("OF_DECIDER_JSON"):
        return table, Path(os.environ["OF_DECIDER_JSON"]).expanduser()
    managed_decider = managed.with_name("decider.json")
    if _readable(managed_decider):
        return table, managed_decider
    # The runtime copy has no clients/open-factory; follow the installed launcher to the checkout it runs from.
    launcher = Path.home() / ".local/bin/open-factory"
    installed = launcher.resolve().parents[1] / "open_factory/decider.json" if launcher.exists() else None
    decider = (
        installed
        if installed and _readable(installed)
        else _REPO_ROOT / "clients/open-factory/open_factory/decider.json"
    )
    return table, decider


def _readable(path: Path) -> bool:
    # launchd services may be denied /Volumes; treat that like a missing file.
    try:
        return path.is_file()
    except OSError:
        return False


def _serialize(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(table: str, decider: str) -> str:
    return hashlib.sha256(json.dumps([json.loads(table), json.loads(decider)], sort_keys=True).encode()).hexdigest()


def _counts(table: dict[str, Any]) -> PolicyCounts:
    classes = table.get("classes", {})
    return PolicyCounts(
        classes=len(classes),
        options=sum(len(item.get("chain", [])) for item in classes.values()),
        aliases=len(table.get("aliases", {})),
        retired=len(table.get("retired", [])),
    )


def _diff(before: Any, after: Any, path: str = "") -> list[PolicyDiff]:
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            part
            for key in sorted(before.keys() | after.keys())
            for part in _diff(
                before.get(key), after.get(key), path + "/" + str(key).replace("~", "~0").replace("/", "~1")
            )
            if key not in before or key not in after or before[key] != after[key]
        ]
    if isinstance(before, list) and isinstance(after, list):
        return [
            part
            for index in range(max(len(before), len(after)))
            for part in _diff(
                before[index] if index < len(before) else None,
                after[index] if index < len(after) else None,
                path + f"/{index}",
            )
        ]
    return [] if before == after else [PolicyDiff(path=path or "/", before=before, after=after)]


def summary(row: RoutingPolicyVersion) -> PolicySummary:
    return PolicySummary(
        version=row.version,
        state=row.state,
        source=row.source,
        summary=row.summary,
        created_at=row.created_at,
        created_by=row.created_by,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        counts=_counts(json.loads(row.routing_table_json)),
    )


def detail(row: RoutingPolicyVersion, active: RoutingPolicyVersion | None) -> PolicyDetail:
    table = json.loads(row.routing_table_json)
    decider = json.loads(row.decider_json)
    before_table = json.loads(active.routing_table_json) if active else {}
    before_decider = json.loads(active.decider_json) if active else {}
    return PolicyDetail(
        **summary(row).model_dump(),
        routing_table=table,
        decider=decider,
        diff=[]
        if row.state == "active"
        else (_diff(before_table, table, "/routingTable") + _diff(before_decider, decider, "/decider")),
    )


async def observe(repo: RoutingPolicyRepository) -> RoutingPolicyVersion:
    table_path, decider_path = _live_paths()
    table = _serialize(json.loads(table_path.read_text()))
    decider = _serialize(json.loads(decider_path.read_text() if _readable(decider_path) else "{}"))
    digest = _digest(table, decider)
    active = await repo.active()
    if active is not None and active.content_sha256 == digest:
        return active
    if active is not None:
        active.state = "retired"
    row = RoutingPolicyVersion(
        version=await repo.next_version(),
        state="active",
        source="file",
        summary="installed from file",
        routing_table_json=table,
        decider_json=decider,
        content_sha256=digest,
        created_by="file",
    )
    repo.session.add(row)
    await repo.session.commit()
    await repo.session.refresh(row)
    return row


def require_draft(row: RoutingPolicyVersion | None) -> RoutingPolicyVersion:
    if row is None:
        raise HTTPException(status_code=404, detail="Policy version not found")
    if row.state != "draft":
        raise HTTPException(status_code=409, detail="Only draft versions can be changed")
    return row
