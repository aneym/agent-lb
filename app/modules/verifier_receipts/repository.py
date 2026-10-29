from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.request_models import RequestLog
from app.db.verifier_models import VerifierReceipt
from app.modules.verifier_receipts.schemas import JobUsage, ReceiptIn


class VerifierReceiptsRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, payload: ReceiptIn, now: datetime) -> VerifierReceipt:
        row = VerifierReceipt(
            repo=payload.repo,
            head_sha=payload.head_sha,
            contract_hash=payload.contract_hash,
            verdict=payload.verdict,
            seat=payload.seat,
            job_id=payload.job_id,
            recorded_at=now,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def last(self, repo: str, head_sha: str, contract_hash: str) -> VerifierReceipt | None:
        statement = (
            select(VerifierReceipt)
            .where(
                VerifierReceipt.repo == repo,
                VerifierReceipt.head_sha == head_sha,
                VerifierReceipt.contract_hash == contract_hash,
            )
            .order_by(VerifierReceipt.id.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalars().first()

    async def job_usage(self, job_id: str) -> JobUsage:
        statement = select(
            func.count(),
            func.coalesce(func.sum(RequestLog.input_tokens), 0),
            func.coalesce(func.sum(RequestLog.output_tokens), 0),
        ).where(RequestLog.job_id == job_id)
        requests, input_tokens, output_tokens = (await self._session.execute(statement)).one()
        return JobUsage(requests=int(requests), input_tokens=int(input_tokens), output_tokens=int(output_tokens))
