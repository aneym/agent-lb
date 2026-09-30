from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.errors import dashboard_error
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.pools.cli_seats import SeatAccountsResponse, read_seat_accounts
from app.modules.pools.plan import build_plan, load_plan_inputs
from app.modules.pools.schemas import PoolsResponse
from app.modules.pools.service import PoolsService
from app.modules.proxy.stand_in import snapshot

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


@router.get("/plan")
async def account_plan(context: AccountsContext = Depends(get_accounts_context)):
    try:
        pools = await PoolsService(context.service).get_pools()
        table, costs = load_plan_inputs()
        return build_plan(pools, table, costs)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return JSONResponse(
            status_code=503,
            content=dashboard_error("account_plan_unavailable", "Account plan inputs are unavailable or invalid"),
        )


@router.get("/seat-accounts", response_model=SeatAccountsResponse)
async def list_seat_accounts() -> SeatAccountsResponse:
    """Cursor and Devin accounts as the `seat` CLI last saw them: auth, cooldowns, observed usage."""
    return read_seat_accounts()


@router.get("/stand-ins")
async def list_stand_ins() -> dict:
    return snapshot()
