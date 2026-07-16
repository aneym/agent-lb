"""Stall forensics: SIGUSR2-triggered stack dumps for post-mortem diagnosis.

Timestamps are intentionally not embedded per-dump for SIGUSR2 dumps:
faulthandler writes raw tracebacks with no signal-safe way to prefix a
wall-clock line, so dump freshness is correlated via the watchdog's
``events.log`` entry, which is written immediately before the signal is sent.
Watchdog-thread dumps (``dump_all_thread_stacks``) run outside signal context
and do embed their own timestamped header.
"""

from __future__ import annotations

import faulthandler
import logging
import signal
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

DEFAULT_FORENSICS_DIR = Path.home() / ".agent-lb" / "forensics"
STACK_DUMP_FILENAME = "py-stacks.log"

_stack_dump_file: IO[str] | None = None
_stack_dump_write_lock = threading.Lock()


def register_stack_dump_signal(forensics_dir: Path = DEFAULT_FORENSICS_DIR) -> None:
    """Register SIGUSR2 to append an all-threads stack dump to the forensics log.

    Idempotent: a second call is a no-op once registration has succeeded. The
    opened file object is kept alive for the process lifetime because
    ``faulthandler.register`` requires a live file descriptor.
    """
    global _stack_dump_file
    if not hasattr(signal, "SIGUSR2"):
        logger.info("SIGUSR2 unavailable on this platform; stack-dump forensics disabled")
        return
    if _stack_dump_file is not None:
        return
    forensics_dir.mkdir(parents=True, exist_ok=True)
    stack_dump_file = (forensics_dir / STACK_DUMP_FILENAME).open("a")
    faulthandler.register(signal.SIGUSR2, file=stack_dump_file, all_threads=True, chain=False)
    _stack_dump_file = stack_dump_file


def dump_all_thread_stacks(*, header: str, forensics_dir: Path = DEFAULT_FORENSICS_DIR) -> None:
    """Append a timestamped all-threads stack dump to the forensics log.

    Runs from ordinary (non-signal) context — e.g. the event-loop stall
    watchdog thread — so it may write its own header line. Best-effort: a
    full disk or unwritable log must never take the watchdog down.
    """
    global _stack_dump_file
    with _stack_dump_write_lock:
        try:
            if _stack_dump_file is None:
                forensics_dir.mkdir(parents=True, exist_ok=True)
                _stack_dump_file = (forensics_dir / STACK_DUMP_FILENAME).open("a")
            timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            _stack_dump_file.write(f"\n=== {timestamp} {header} ===\n")
            _stack_dump_file.flush()
            faulthandler.dump_traceback(file=_stack_dump_file, all_threads=True)
            _stack_dump_file.flush()
        except OSError:
            logger.warning("forensics stack dump write failed", exc_info=True)


__all__ = [
    "DEFAULT_FORENSICS_DIR",
    "STACK_DUMP_FILENAME",
    "dump_all_thread_stacks",
    "register_stack_dump_signal",
]
