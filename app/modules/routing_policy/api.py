from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.db.models import RoutingPolicyVersion
from app.db.session import SessionLocal
from app.modules.routing_policy.repository import RoutingPolicyRepository
from app.modules.routing_policy.schemas import DraftCreate, DraftPatch, PolicyDetail, PolicyVersions
from app.modules.routing_policy.service import _digest, _serialize, detail, observe, require_draft, summary

router = APIRouter(
    prefix="/api/routing-policy",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("/versions", response_model=PolicyVersions)
async def versions() -> PolicyVersions:
    async with SessionLocal() as session:
        repo = RoutingPolicyRepository(session)
        active = await observe(repo)
        return PolicyVersions(active_version=active.version, versions=[summary(row) for row in await repo.all()])


@router.get("/versions/{version}", response_model=PolicyDetail)
async def version_detail(version: int) -> PolicyDetail:
    async with SessionLocal() as session:
        repo = RoutingPolicyRepository(session)
        active = await observe(repo)
        row = await repo.get(version)
        if row is None:
            raise HTTPException(status_code=404, detail="Policy version not found")
        return detail(row, active)


@router.post("/drafts", response_model=PolicyDetail)
async def create_draft(body: DraftCreate) -> PolicyDetail:
    async with SessionLocal() as session:
        repo = RoutingPolicyRepository(session)
        active = await observe(repo)
        table = _serialize(
            body.routing_table if body.routing_table is not None else json.loads(active.routing_table_json)
        )
        decider = _serialize(body.decider if body.decider is not None else json.loads(active.decider_json))
        row = RoutingPolicyVersion(
            version=await repo.next_version(),
            state="draft",
            source="draft",
            summary=body.summary,
            routing_table_json=table,
            decider_json=decider,
            content_sha256=_digest(table, decider),
            created_by="dashboard",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return detail(row, active)


@router.patch("/drafts/{version}", response_model=PolicyDetail)
async def patch_draft(version: int, body: DraftPatch) -> PolicyDetail:
    async with SessionLocal() as session:
        repo = RoutingPolicyRepository(session)
        active = await observe(repo)
        row = require_draft(await repo.get(version))
        if body.summary is not None:
            row.summary = body.summary
        if body.routing_table is not None:
            row.routing_table_json = _serialize(body.routing_table)
        if body.decider is not None:
            row.decider_json = _serialize(body.decider)
        row.content_sha256 = _digest(row.routing_table_json, row.decider_json)
        await session.commit()
        return detail(row, active)


@router.delete("/drafts/{version}", status_code=204)
async def delete_draft(version: int) -> Response:
    async with SessionLocal() as session:
        repo = RoutingPolicyRepository(session)
        await observe(repo)
        row = require_draft(await repo.get(version))
        await session.delete(row)
        await session.commit()
        return Response(status_code=204)


@router.post("/drafts/{version}/approve", status_code=409)
async def approve_draft(version: int) -> Response:
    async with SessionLocal() as session:
        repo = RoutingPolicyRepository(session)
        await observe(repo)
        require_draft(await repo.get(version))
    return Response(
        content=json.dumps(
            {
                "error": {
                    "code": "replay_required",
                    "message": "No replay runner exists yet; activate through install-policy.",
                }
            }
        ),
        status_code=409,
        media_type="application/json",
    )
