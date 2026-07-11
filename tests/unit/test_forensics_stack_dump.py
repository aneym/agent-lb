from __future__ import annotations

import os
import signal
import time
from pathlib import Path

import pytest

import app.core.forensics as forensics

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each test registers against its own tmp path; the module guards against
    # double-registration, so the global must be cleared between tests.
    monkeypatch.setattr(forensics, "_stack_dump_file", None)
    yield
    if forensics._stack_dump_file is not None:
        faulthandler_unregister()


def faulthandler_unregister() -> None:
    import faulthandler

    if hasattr(signal, "SIGUSR2"):
        faulthandler.unregister(signal.SIGUSR2)


def test_register_stack_dump_signal_creates_forensics_dir_and_file(tmp_path: Path) -> None:
    forensics_dir = tmp_path / "forensics"

    forensics.register_stack_dump_signal(forensics_dir=forensics_dir)

    assert forensics_dir.is_dir()
    assert (forensics_dir / forensics.STACK_DUMP_FILENAME).exists()


def test_register_stack_dump_signal_is_idempotent(tmp_path: Path) -> None:
    forensics_dir = tmp_path / "forensics"
    other_dir = tmp_path / "other"

    forensics.register_stack_dump_signal(forensics_dir=forensics_dir)
    forensics.register_stack_dump_signal(forensics_dir=other_dir)

    assert not other_dir.exists()


@pytest.mark.skipif(not hasattr(signal, "SIGUSR2"), reason="SIGUSR2 unavailable on this platform")
def test_sigusr2_dumps_thread_stacks_to_forensics_log(tmp_path: Path) -> None:
    forensics_dir = tmp_path / "forensics"
    forensics.register_stack_dump_signal(forensics_dir=forensics_dir)

    dump_path = forensics_dir / forensics.STACK_DUMP_FILENAME
    size_before = dump_path.stat().st_size

    os.kill(os.getpid(), signal.SIGUSR2)
    for _ in range(50):
        if dump_path.stat().st_size > size_before:
            break
        time.sleep(0.05)

    assert dump_path.stat().st_size > size_before
    assert "most recent call first" in dump_path.read_text()
