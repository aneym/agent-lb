from __future__ import annotations

import fcntl
import hashlib
import random
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from typing import BinaryIO

import aiohttp

from app.core import upload_admission
from app.core.account_warmup import warmup_active
from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings
from app.db.models import Account


class WarmupBusy(Exception):
    pass


def claim_slot(account: Account) -> BinaryIO | None:
    directory = get_settings().data_dir / "warmup-leases" / hashlib.sha256(account.id.encode()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(account.warmup_max_concurrent or 2):
        slot = (directory / str(index)).open("a+b")
        try:
            fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return slot
        except BlockingIOError:
            slot.close()
    return None


def candidates(accounts: Iterable[Account], sticky_key: str | None) -> Iterator[Account]:
    for account in accounts:
        if not warmup_active(account):
            yield account
            continue
        if account.warmup_owner_instance != get_settings().local_instance_id:
            continue
        fraction = (account.warmup_capacity_percent or 15.0) / 100.0
        draw = (
            int.from_bytes(hashlib.sha256(f"{account.id}:{sticky_key}".encode()).digest()[:8], "big") / 2**64
            if sticky_key
            else random.random()
        )
        if draw >= fraction:
            continue
        slot = claim_slot(account)
        if slot is not None:
            slot.close()
            yield account


@contextmanager
def admission(account: Account) -> Iterator[None]:
    if not warmup_active(account):
        yield
        return
    if account.warmup_owner_instance != get_settings().local_instance_id:
        raise WarmupBusy
    slot = claim_slot(account)
    if slot is None:
        raise WarmupBusy
    with slot:
        yield


@asynccontextmanager
async def admitted_http_session(
    account: Account, session_key: str | None, nbytes: int, upload_class: str
) -> AsyncIterator[tuple[aiohttp.ClientSession, AsyncExitStack]]:
    with admission(account):
        async with AsyncExitStack() as admission_stack:
            await admission_stack.enter_async_context(
                upload_admission.admission().admit(session_key, nbytes, upload_class)
            )
            async with lease_http_session() as session:
                yield session, admission_stack
