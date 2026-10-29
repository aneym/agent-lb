from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import StringConstraints

from app.modules.shared.schemas import DashboardModel

Repo = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}(/[A-Za-z0-9_.-]{1,63})?$")]
Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{7,64}$")]
ContractHash = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9:_-]{1,128}$")]
Seat = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9@._:-]{0,63}$")]
JobId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9@._:-]{0,159}$")]


class ReceiptIn(DashboardModel):
    repo: Repo
    head_sha: Sha
    contract_hash: ContractHash
    verdict: Literal["PASS", "FAIL"]
    seat: Seat | None = None
    job_id: JobId | None = None


class ReceiptOut(DashboardModel):
    id: int
    repo: str
    head_sha: str
    contract_hash: str
    verdict: str
    seat: str | None
    job_id: str | None
    recorded_at: datetime


class JobUsage(DashboardModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class LastReceiptResponse(DashboardModel):
    receipt: ReceiptOut | None = None
    job_usage: JobUsage | None = None
