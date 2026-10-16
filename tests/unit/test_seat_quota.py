from __future__ import annotations

import json
import runpy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest

SEAT = Path(__file__).resolve().parents[2] / "clients/seat"


@pytest.fixture
def seat(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAT_HOME", str(tmp_path))
    (tmp_path / "accounts.json").write_text(
        json.dumps({"accounts": [{"id": "fixture", "vendor": "muse", "auth": "login"}]})
    )
    return runpy.run_path(str(SEAT))


def test_vendor_quota_parser_golden_table(seat):
    cursor = seat["cursor_quota"](
        {"planUsage": {"autoPercentUsed": 110, "apiPercentUsed": 40}, "billingCycleEnd": "2026-10-30T00:00:00Z"}, {}
    )
    assert [(w["id"], w["usedPercent"], w["remainingPercent"], w["kind"]) for w in cursor["windows"]] == [
        ("cursor-models", 110, 0, "monthly"),
        ("cursor-other", 40, 60, "monthly"),
    ]
    assert {w["resetsAt"] for w in cursor["windows"]} == {"2026-10-30T00:00:00Z"}
    devin = seat["devin_quota"](
        {
            "userStatus": {
                "hideDailyQuota": True,
                "dailyQuotaRemainingPercent": 80,
                "weeklyQuotaRemainingPercent": 70,
                "weeklyResetAtUnix": 1792454400,
            }
        }
    )
    assert [(w["id"], w["usedPercent"]) for w in devin["windows"]] == [("weekly", 30)]
    muse = seat["muse_quota"](
        json.dumps(
            {
                "type": "usage/changed",
                "usage": {
                    "observedAtMs": 1792159200000,
                    "window": {"windowDurationMins": 300, "usedPercent": 12},
                    "weekly": {"usedPercent": 30},
                },
            }
        )
    )
    assert muse["source"] == "vendor_observed"
    assert muse["observedAt"] == "2026-10-16T14:00:00Z"
    assert [(w["id"], w["usedPercent"], w["durationMins"]) for w in muse["windows"]] == [
        ("session", 12, 300),
        ("weekly", 30, None),
    ]


def test_muse_quota_command_records_not_reported_without_network(seat, tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Muse quota must not call the network")

    monkeypatch.setitem(seat["quota_post"].__globals__, "urlopen", forbidden)
    assert seat["main"](["quota", "--vendor", "muse", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["accounts"]["fixture"] == {
        "source": "unavailable",
        "observedAt": None,
        "stale": False,
        "unavailableReason": "not_reported",
        "primaryWindowId": None,
        "windows": [],
    }
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["accounts"]["fixture"]["quota"] == body["accounts"]["fixture"]
    assert state["quota_refreshed_at"]


def test_vendor_error_keeps_last_reading_at_file_boundary(seat, tmp_path, monkeypatch):
    observed = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    previous = seat["quota_payload"]([seat["quota_window"]("weekly", "weekly", "Weekly", 30, None)], observed=observed)
    account = {"id": "fixture", "vendor": "cursor", "auth": "api-key", "key_file": str(tmp_path / "key")}
    (tmp_path / "key").write_text("fixture-not-a-real-key")
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": [account]}))
    (tmp_path / "state.json").write_text(json.dumps({"accounts": {"fixture": {"quota": previous}}}))

    def failed(request, **kwargs):
        raise HTTPError(request.full_url, 500, "fixture error", {}, None)

    monkeypatch.setitem(seat["quota_post"].__globals__, "urlopen", failed)
    quota = seat["refresh_quota"](account)
    assert quota["windows"] == previous["windows"]
    assert quota["observedAt"] == observed
    assert quota["stale"] is True
    assert quota["unavailableReason"] == "vendor_error"


def test_cursor_api_key_unsupported_attempts_bearer_once(seat, tmp_path, monkeypatch):
    account = {"id": "fixture", "vendor": "cursor", "auth": "api-key", "key_file": str(tmp_path / "key")}
    (tmp_path / "key").write_text("fixture-not-a-real-key")
    calls = []

    def rejected(request, **kwargs):
        calls.append(request)
        raise HTTPError(request.full_url, 401, "fixture", {}, None)

    monkeypatch.setitem(seat["quota_post"].__globals__, "urlopen", rejected)
    result = seat["refresh_quota"](account)
    assert len(calls) == 1
    assert result["unavailableReason"] == "auth_unsupported"


def test_cursor_and_devin_collection_use_vendor_request_boundaries(seat, tmp_path, monkeypatch):
    import io

    calls = []

    def response(request, **kwargs):
        calls.append(request)
        if request.full_url.endswith("GetCurrentPeriodUsage"):
            return io.StringIO(
                json.dumps(
                    {
                        "planUsage": {"autoPercentUsed": 12, "apiPercentUsed": 38},
                        "billingCycleEnd": "1793325807000",
                    }
                )
            )
        if request.full_url.endswith("GetPlanInfo"):
            return io.StringIO(json.dumps({"planInfo": {"includedUsagePeriod": "weekly", "planName": "Ultra"}}))
        body = json.loads(request.data)
        assert body["metadata"]["apiKey"] == "fixture-not-a-real-key"
        assert body["metadata"]["extensionVersion"] == "1.0.0"
        assert body["metadata"]["ideVersion"] == "1.0.0"
        return io.StringIO(
            json.dumps(
                {
                    "userStatus": {
                        "dailyQuotaRemainingPercent": 80,
                        "dailyQuotaResetAtUnix": "1792209600",
                        "weeklyQuotaResetAtUnix": "1792468800",
                        "weeklyQuotaRemainingPercent": 40,
                        "planStatus": {"planInfo": {"planName": "Pro"}},
                    }
                }
            )
        )

    monkeypatch.setitem(seat["quota_post"].__globals__, "urlopen", response)
    (tmp_path / "key").write_text("fixture-not-a-real-key")
    cursor = seat["collect_quota"](
        {"id": "cursor", "vendor": "cursor", "auth": "api-key", "key_file": str(tmp_path / "key")}
    )
    assert {window["kind"] for window in cursor["windows"]} == {"weekly"}
    assert cursor["planLabel"] == "Ultra"
    assert {window["resetsAt"] for window in cursor["windows"]} == {"2026-10-30T02:03:27Z"}
    directory = tmp_path / "devin"
    directory.mkdir()
    (directory / "credentials.toml").write_text('windsurf_api_key = "fixture-not-a-real-key"\n')
    devin = seat["collect_quota"]({"id": "devin", "vendor": "devin", "data_dir": str(tmp_path)})
    assert [(window["id"], window["usedPercent"]) for window in devin["windows"]] == [("daily", 20), ("weekly", 60)]
    assert devin["planLabel"] == "Pro"
    assert [window["resetsAt"] for window in devin["windows"]] == [
        "2026-10-17T04:00:00Z", "2026-10-20T04:00:00Z"
    ]
    assert len(calls) == 3
