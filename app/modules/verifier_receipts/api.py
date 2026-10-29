from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.utils.time import utcnow
from app.db.verifier_models import VerifierReceipt
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.verifier_receipts.repository import VerifierReceiptsRepository
from app.modules.verifier_receipts.schemas import (
    ContractHash,
    LastReceiptResponse,
    ReceiptIn,
    ReceiptOut,
    Repo,
    Sha,
)

router = APIRouter(
    prefix="/api/verifier/receipts",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


def _out(row: VerifierReceipt) -> ReceiptOut:
    return ReceiptOut(
        id=row.id,
        repo=row.repo,
        head_sha=row.head_sha,
        contract_hash=row.contract_hash,
        verdict=row.verdict,
        seat=row.seat,
        job_id=row.job_id,
        recorded_at=row.recorded_at,
    )


@router.post("", response_model=ReceiptOut)
async def record_receipt(
    payload: ReceiptIn,
    context: AccountsContext = Depends(get_accounts_context),
) -> ReceiptOut:
    return _out(await VerifierReceiptsRepository(context.session).add(payload, utcnow()))


@router.get("/last", response_model=LastReceiptResponse)
async def last_receipt(
    repo: Repo = Query(),
    head_sha: Sha = Query(alias="headSha"),
    contract_hash: ContractHash = Query(alias="contractHash"),
    context: AccountsContext = Depends(get_accounts_context),
) -> LastReceiptResponse:
    repository = VerifierReceiptsRepository(context.session)
    row = await repository.last(repo, head_sha, contract_hash)
    if row is None:
        return LastReceiptResponse()
    usage = await repository.job_usage(row.job_id) if row.job_id else None
    return LastReceiptResponse(receipt=_out(row), job_usage=usage)
