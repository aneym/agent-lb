from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import cli

pytestmark = pytest.mark.unit


class _Handler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, object]] = {}
    requests: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        type(self).requests.append(self.path)
        code, body = type(self).routes.get(self.path, (404, {"token": "must-not-print"}))
        encoded = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args) -> None:
        pass


@pytest.fixture
def service():
    _Handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _account(**overrides):
    account = {
        "accountId": "acc-safe-id",
        "provider": "anthropic",
        "email": "secret@example.test",
        "displayName": "Secret Operator",
        "status": "active",
        "subscription": {"status": "active"},
        "usage": {"primaryRemainingPercent": 53, "secondaryRemainingPercent": 37},
        "additionalQuotas": [],
    }
    account.update(overrides)
    return account


def _set_routes(accounts: list[dict]) -> None:
    _Handler.routes = {
        "/health/ready": (200, {"status": "ready"}),
        "/api/accounts": (200, {"accounts": accounts}),
        "/api/request-logs/anthropic-cache-summary": (
            200,
            {
                "window_minutes": 60,
                "request_count": 0,
                "incomplete_request_count": 0,
                "input_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
            },
        ),
    }


def test_status_uses_only_read_only_endpoints_and_redacts_account_identity(service, capsys):
    _set_routes([_account()])

    cli.main(["status", "--json", "--base-url", service])

    payload = json.loads(capsys.readouterr().out)
    assert _Handler.requests == ["/health/ready", "/api/accounts", "/api/request-logs/anthropic-cache-summary"]

    assert payload["schema_version"] == 1
    assert payload["accounts"][0]["account_id"] == "acc-safe-id"
    assert "secret@example.test" not in json.dumps(payload)
    assert "Secret Operator" not in json.dumps(payload)


def test_reset_credits_are_account_specific_nullable_and_use_existing_request(service, capsys):
    _set_routes(
        [
            _account(accountId="positive", resetCreditsAvailable=3),
            _account(accountId="zero", resetCreditsAvailable=0),
            _account(accountId="unknown", resetCreditsAvailable=None),
            _account(accountId="missing"),
            _account(accountId="invalid", resetCreditsAvailable=True),
        ]
    )

    cli.main(["status", "--json", "--base-url", service])

    accounts = json.loads(capsys.readouterr().out)["accounts"]
    assert {item["account_id"]: item["reset_credits_available"] for item in accounts} == {
        "positive": 3,
        "zero": 0,
        "unknown": None,
        "missing": None,
        "invalid": None,
    }
    assert _Handler.requests == ["/health/ready", "/api/accounts", "/api/request-logs/anthropic-cache-summary"]

    cli.main(["status", "--base-url", service])
    output = capsys.readouterr().out
    assert "positive [active/usable]: primary 53%; weekly 37%; banked resets 3" in output
    assert "zero [active/usable]: primary 53%; weekly 37%; banked resets 0" in output
    assert "unknown [active/usable]: primary 53%; weekly 37%; banked resets unknown" in output


def test_status_reports_last_confirmed_anthropic_prime(service, capsys):
    primed_at = "2026-09-23T20:00:12Z"
    _set_routes([_account(lastPrimedAt=primed_at)])

    cli.main(["status", "--json", "--base-url", service])
    payload = json.loads(capsys.readouterr().out)
    assert payload["accounts"][0]["last_primed_at"] == primed_at

    cli.main(["status", "--base-url", service])
    assert f"last primed {primed_at}" in capsys.readouterr().out

@pytest.mark.parametrize(("read", "state"), [(49, "alert"), (50, "ok"), (75, "ok")])
def test_anthropic_cache_ratio_threshold(service, capsys, read, state):
    _set_routes([_account()])
    _Handler.routes["/api/request-logs/anthropic-cache-summary"] = (
        200,
        {
            "window_minutes": 60,
            "request_count": 2,
            "incomplete_request_count": 0,
            "input_tokens": 100 - read,
            "cache_creation_tokens": 0,
            "cache_read_tokens": read,
        },
    )

    cli.main(["status", "--json", "--base-url", service])
    assert json.loads(capsys.readouterr().out)["anthropic_cache"] == {
        "state": state,
        "ratio_percent": float(read),
        "window_minutes": 60,
        "request_count": 2,
    }
    cli.main(["status", "--base-url", service])
    output = capsys.readouterr().out
    assert ("ALERT Anthropic cache-read ratio" in output) is (state == "alert")


@pytest.mark.parametrize("count,incomplete,tokens", [(0, 0, 0), (1, 0, 0), (1, 1, 100)])
def test_anthropic_cache_unknown_does_not_alert(service, capsys, count, incomplete, tokens):
    _set_routes([_account()])
    _Handler.routes["/api/request-logs/anthropic-cache-summary"] = (
        200,
        {
            "window_minutes": 60,
            "request_count": count,
            "incomplete_request_count": incomplete,
            "input_tokens": tokens,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        },
    )
    cli.main(["status", "--base-url", service])
    output = capsys.readouterr().out
    assert "Anthropic cache-read ratio (last hour): unknown" in output
    assert "ALERT" not in output


def test_cache_summary_failure_does_not_fail_status(service, capsys):
    _set_routes([_account()])
    _Handler.routes["/api/request-logs/anthropic-cache-summary"] = (503, {"secret": "must-not-print"})
    cli.main(["status", "--json", "--base-url", service])
    payload = json.loads(capsys.readouterr().out)
    assert payload["health"]["state"] == "ready"
    assert payload["anthropic_cache"]["state"] == "unknown"
    assert "must-not-print" not in json.dumps(payload)


def test_exhausted_quota_is_a_successful_snapshot(service, capsys):
    reset = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _set_routes(
        [
            _account(
                additionalQuotas=[
                    {"quotaKey": "anthropic_top", "primaryWindow": {"usedPercent": 100, "resetAt": reset}}
                ]
            )
        ]
    )

    cli.main(["status", "--json", "--base-url", service])

    payload = json.loads(capsys.readouterr().out)
    assert payload["accounts"][0]["usable"] == "usable"
    assert payload["accounts"][0]["quota_cooldowns"] == [{"quota_key": "anthropic_top", "reset_at": reset}]


def test_service_error_is_safe_json_and_exit_two(service, capsys):
    _Handler.routes = {"/health/ready": (503, {"cookie": "must-not-print"})}

    with pytest.raises(SystemExit) as result:
        cli.main(["status", "--json", "--base-url", service])

    payload = json.loads(capsys.readouterr().out)
    assert result.value.code == 2
    assert payload["health"]["state"] == "error"
    assert "must-not-print" not in json.dumps(payload)


def test_model_fable_reports_observed_exhaustion_and_policy_as_distinct(service, capsys):
    reset = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _set_routes(
        [
            _account(
                fableEligible=False,
                fableScopedWeekly={"usedPercent": 100, "resetAt": reset, "fresh": True},
            )
        ]
    )

    cli.main(["status", "--json", "--model", "claude-fable-5", "--base-url", service])

    payload = json.loads(capsys.readouterr().out)
    assert payload["model"]["status"] == "blocked"
    assert "observed Fable scoped weekly quota is exhausted until its reset" in payload["model"]["reasons"]
    assert payload["accounts"][0]["fable"]["routing_policy_eligible"] is False


def test_fable_telemetry_is_labeled_only_for_fable_human_assessment(service, capsys):
    _set_routes([_account(fableScopedWeekly={"usedPercent": 50, "fresh": True})])

    cli.main(["status", "--model", "claude-opus-5-5", "--thinking", "--base-url", service])
    assert "Fable scoped weekly" not in capsys.readouterr().out

    cli.main(["status", "--base-url", service])
    assert "Fable scoped weekly" not in capsys.readouterr().out

    cli.main(["status", "--model", "claude-fable-5", "--base-url", service])
    assert "Fable scoped weekly 50%" in capsys.readouterr().out

    cli.main(["status", "--json", "--model", "claude-opus-5-5", "--base-url", service])
    assert "fable" in json.loads(capsys.readouterr().out)["accounts"][0]


@pytest.mark.parametrize("alias", ["opus", "sonnet"])
def test_anthropic_model_aliases_are_recognized(service, capsys, alias):
    _set_routes([_account(additionalQuotas=[{"quotaKey": "anthropic_top", "primaryWindow": {"usedPercent": 10}}])])

    cli.main(["status", "--json", "--model", alias, "--base-url", service])

    assert json.loads(capsys.readouterr().out)["model"]["status"] == "usable"


def test_numeric_quota_reset_and_runtime_fable_telemetry_are_projected(service, capsys):
    reset_epoch = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    _set_routes(
        [
            _account(
                fableScopedWeekly={"usedPercent": 90, "resetAt": reset_epoch, "fresh": True},
                additionalQuotas=[
                    {
                        "quotaKey": "anthropic_top",
                        "primaryWindow": {"usedPercent": 100, "resetAt": reset_epoch},
                    }
                ],
            )
        ]
    )

    cli.main(["status", "--json", "--base-url", service])

    account = json.loads(capsys.readouterr().out)["accounts"][0]
    assert account["fable"]["scoped_weekly"]["remaining_percent"] == 10
    assert account["fable"]["freshness"] == "fresh"
    assert account["quota_cooldowns"][0]["reset_at"] == str(reset_epoch)


def test_model_fable_with_missing_scoped_freshness_stays_unknown(service, capsys):
    _set_routes([_account(fableEligible=True)])

    cli.main(["status", "--json", "--model", "claude-fable-5", "--base-url", service])

    assert json.loads(capsys.readouterr().out)["model"]["status"] == "unknown"


def test_stale_fable_exhaustion_is_unknown_not_blocked(service, capsys):
    reset = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _set_routes([_account(fableScopedWeekly={"usedPercent": 100, "resetAt": reset, "fresh": False})])

    cli.main(["status", "--json", "--model", "claude-fable-5", "--base-url", service])

    payload = json.loads(capsys.readouterr().out)
    assert payload["model"]["status"] == "unknown"
    assert "Fable scoped telemetry is stale" in payload["model"]["reasons"]


def test_model_scoped_cooldown_does_not_block_general_account(service, capsys):
    reset = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _set_routes(
        [
            _account(
                additionalQuotas=[
                    {
                        "quotaKey": "anthropic_top_thinking",
                        "primaryWindow": {"usedPercent": 100, "resetAt": reset},
                    }
                ]
            )
        ]
    )

    cli.main(["status", "--json", "--base-url", service])

    assert json.loads(capsys.readouterr().out)["accounts"][0]["usable"] == "usable"


def test_thinking_flag_selects_actual_fable_anthropic_quota_key(service, capsys):
    reset = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _set_routes(
        [
            _account(
                fableScopedWeekly={"usedPercent": 50, "fresh": True},
                additionalQuotas=[
                    {"quotaKey": "anthropic_top", "primaryWindow": {"usedPercent": 0}},
                    {
                        "quotaKey": "anthropic_top_thinking",
                        "primaryWindow": {"usedPercent": 100, "resetAt": reset},
                    },
                ],
            )
        ]
    )

    cli.main(["status", "--json", "--model", "claude-fable-5", "--thinking", "--base-url", service])

    assert json.loads(capsys.readouterr().out)["model"]["status"] == "blocked"


def test_unknown_account_and_model_are_not_assumed_usable(service, capsys):
    _set_routes([_account(status="mystery")])

    cli.main(["status", "--json", "--model", "future-model", "--base-url", service])

    payload = json.loads(capsys.readouterr().out)
    assert payload["accounts"][0]["usable"] == "unknown"
    assert payload["model"]["status"] == "unknown"


def test_openai_weekly_only_usage_and_malformed_quota_are_safe(service, capsys):
    _set_routes(
        [
            _account(
                provider="openai",
                # Shape /api/accounts returns for a Pro account: the missing
                # five-hour window is null, not absent.
                usage={"primaryRemainingPercent": None, "secondaryRemainingPercent": 25},
                additionalQuotas=[{"quotaKey": "codex", "modelIds": None, "primaryWindow": "bad"}],
            )
        ]
    )

    cli.main(["status", "--json", "--base-url", service])

    account = json.loads(capsys.readouterr().out)["accounts"][0]
    assert account["usable"] == "usable"
    assert account["primary"]["remaining_percent"] is None
    assert account["quota_windows"][0]["model_ids"] == []


def test_invalid_inputs_fail_safely_and_human_output_has_account_windows(service, capsys):
    _set_routes([_account(usage={"primaryRemainingPercent": float("nan"), "secondaryRemainingPercent": 25})])

    cli.main(["status", "--base-url", service])

    output = capsys.readouterr().out
    assert "acc-safe-id" in output
    assert "primary unknown" in output
    assert "weekly 25%" in output

    with pytest.raises(SystemExit, match="finite"):
        cli.main(["status", "--timeout", "nan", "--base-url", service])
    with pytest.raises(SystemExit) as result:
        cli.main(["status", "--json", "--base-url", "http://127.0.0.1:bad"])
    assert result.value.code == 2
