from __future__ import annotations

import logging
from typing import Protocol

from app.core.metrics.prometheus import MULTIPROCESS_MODE

logger = logging.getLogger(__name__)


class _MetricsServer(Protocol):
    should_exit: bool

    async def serve(self) -> None: ...


def _is_benign_metrics_bind_failure(exc: BaseException) -> bool:
    if not MULTIPROCESS_MODE:
        return False
    if isinstance(exc, SystemExit):
        return exc.code == 1
    if isinstance(exc, OSError):
        import errno as _errno

        return exc.errno in (_errno.EADDRINUSE, _errno.EADDRNOTAVAIL)
    return False


async def serve_metrics(srv: _MetricsServer, port: int) -> None:
    try:
        await srv.serve()
    except SystemExit as exc:
        if _is_benign_metrics_bind_failure(exc):
            logger.info(
                "Metrics port %d unavailable (another worker likely serves metrics)",
                port,
            )
        else:
            raise
    except OSError as exc:
        if _is_benign_metrics_bind_failure(exc):
            logger.info(
                "Metrics port %d already bound (another worker serves metrics)",
                port,
            )
        else:
            raise
