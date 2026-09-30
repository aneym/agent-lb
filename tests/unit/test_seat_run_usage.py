from __future__ import annotations

import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize("value", ["not-a-number", {}, [], float("inf")])
def test_seat_record_run_preserves_run_with_malformed_tokens(value: object) -> None:
    seat = runpy.run_path(str(Path(__file__).resolve().parents[2] / "clients" / "seat"))
    record = {}
    seat["record_run"](record, {
        "ts": "2026-09-30T04:00:00Z", "model": "grok-4.7-low", "ok": True,
        "tokens_in": value, "tokens_out": "12", "cache_read": value,
    })
    assert len(record["runs"]) == 1
    assert record["runs"][0]["ok"] is True
    assert record["runs"][0]["tokens_in"] == 0
    assert record["daily"]["2026-09-30"]["grok-4.7-low"] == {
        "runs": 1, "tokens_in": 0, "tokens_out": 12, "cache_read": 0,
    }
