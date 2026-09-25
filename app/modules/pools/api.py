from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.pools.cli_seats import SeatAccountsResponse, read_seat_accounts
from app.modules.pools.schemas import PoolsResponse
from app.modules.pools.service import PoolsService

router = APIRouter(
    prefix="/api/pools",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=PoolsResponse)
async def list_pools(
    context: AccountsContext = Depends(get_accounts_context),
) -> PoolsResponse:
    return await PoolsService(context.service).get_pools()


@router.get("/seat-accounts", response_model=SeatAccountsResponse)
async def list_seat_accounts() -> SeatAccountsResponse:
    """Cursor and Devin accounts as the `seat` CLI last saw them: auth, cooldowns, observed usage."""
    return read_seat_accounts()
