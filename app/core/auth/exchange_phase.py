from __future__ import annotations

from enum import StrEnum

import aiohttp


class ExchangePhase(StrEnum):
    PRE_SEND = "pre_send"
    ANSWERED = "answered"
    AMBIGUOUS = "ambiguous"


def aiohttp_exchange_phase(exc: BaseException) -> ExchangePhase:
    if isinstance(exc, (aiohttp.ClientConnectorError, aiohttp.InvalidURL)):
        return ExchangePhase.PRE_SEND
    return ExchangePhase.AMBIGUOUS
