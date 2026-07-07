from __future__ import annotations

import importlib.machinery
import importlib.util
import time
from pathlib import Path


def load_launcher_module():
    path = Path(__file__).resolve().parents[2] / "clients" / "claude-lb-launch"
    loader = importlib.machinery.SourceFileLoader("claude_lb_launch_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_format_lb_pick_error_prettifies_anthropic_selection_failure() -> None:
    launcher = load_launcher_module()
    body = (
        '{"error":{"message":"3 Anthropic accounts exist, but none are selectable for '
        "claude-fable-5/anthropic_top_thinking; statuses: active=1, rate_limited=2. "
        'OpenAI accounts are not eligible for Claude routing.",'
        '"type":"server_error","code":"no_available_anthropic_accounts"}}'
    )

    assert launcher.format_lb_pick_error(body) == (
        "Claude accounts are all at limit or unavailable "
        "(3 accounts; active=1, rate_limited=2). OpenAI accounts cannot serve Claude."
    )


def test_format_lb_pick_error_preserves_anthropic_quota_detail() -> None:
    launcher = load_launcher_module()
    body = (
        '{"error":{"message":"2 Anthropic accounts exist, but none are selectable for '
        "claude-fable-5/anthropic_top; statuses: active=1, quota_exceeded=1. "
        "Model quota: anthropic_top cooldown excluded 1 account until 2026-06-12T09:20:24; "
        "1 account remained after the anthropic_top prefilter; "
        "selector reason: no_available_anthropic_accounts. "
        'OpenAI accounts are not eligible for Claude routing. Limits reset at 2026-06-12T09:20:24.",'
        '"type":"server_error","code":"no_available_anthropic_accounts"}}'
    )

    assert launcher.format_lb_pick_error(body) == (
        "Claude accounts are all at limit or unavailable (2 accounts; active=1, quota_exceeded=1). "
        "Model quota: anthropic_top cooldown excluded 1 account until 2026-06-12T09:20:24; "
        "1 account remained after the anthropic_top prefilter; "
        "selector reason: no_available_anthropic_accounts. OpenAI accounts cannot serve Claude."
    )


def test_format_lb_pick_error_handles_all_rate_limited_accounts() -> None:
    launcher = load_launcher_module()
    body = (
        '{"error":{"message":"3 Anthropic accounts exist, but none are selectable for '
        "claude-fable-5/anthropic_top_thinking; statuses: rate_limited=3. "
        'OpenAI accounts are not eligible for Claude routing.",'
        '"type":"server_error","code":"no_available_anthropic_accounts"}}'
    )

    assert launcher.format_lb_pick_error(body) == (
        "all Claude accounts are rate-limited (3 accounts). OpenAI accounts cannot serve Claude."
    )


def test_format_lb_pick_error_preserves_plain_text_body() -> None:
    launcher = load_launcher_module()

    assert launcher.format_lb_pick_error("upstream unavailable") == "upstream unavailable"


def test_retry_at_from_error_body_prefers_retry_at_iso() -> None:
    launcher = load_launcher_module()
    body = (
        '{"error":{"message":"cooling down","code":"anthropic_quota_cooldown",'
        '"retryAt":"2026-06-10T18:20:01Z","retryAfterSeconds":900}}'
    )

    assert launcher.retry_at_from_error_body(body) == 1781115601


def test_retry_at_from_error_body_falls_back_to_retry_after_seconds() -> None:
    launcher = load_launcher_module()
    body = '{"error":{"message":"cooling down","retryAfterSeconds":120}}'

    retry_at = launcher.retry_at_from_error_body(body)
    assert retry_at is not None
    assert abs(retry_at - (time.time() + 120)) < 5


def test_retry_at_from_error_body_returns_none_without_metadata() -> None:
    launcher = load_launcher_module()

    assert launcher.retry_at_from_error_body('{"error":{"message":"nope"}}') is None
    assert launcher.retry_at_from_error_body("not json") is None


def test_account_pressure_includes_fable_available_state() -> None:
    launcher = load_launcher_module()

    account = {
        "alias": "Claude A",
        "fableEligible": True,
        "usage": {"primaryRemainingPercent": 100, "secondaryRemainingPercent": 35},
        "additionalQuotas": [
            {
                "quotaKey": "anthropic_fable_scoped_weekly",
                "primaryWindow": {"usedPercent": 84, "resetAt": None, "windowMinutes": 10080},
            },
            {
                "quotaKey": "anthropic_top_thinking",
                "primaryWindow": {"usedPercent": 0, "resetAt": 0},
            }
        ],
    }

    *_, reason = launcher.account_pressure(account, "anthropic_top_thinking")

    assert reason == "left: top-thinking 100% · 5h 100% · weekly 35% · fable 16% left"


def test_account_pressure_includes_fable_out_state() -> None:
    launcher = load_launcher_module()

    account = {
        "alias": "Claude B",
        "fableEligible": False,
        "usage": {"primaryRemainingPercent": 100, "secondaryRemainingPercent": 18},
        "additionalQuotas": [
            {
                "quotaKey": "anthropic_fable_scoped_weekly",
                "primaryWindow": {"usedPercent": 100, "resetAt": None, "windowMinutes": 10080},
            },
            {
                "quotaKey": "anthropic_top_thinking",
                "primaryWindow": {"usedPercent": 0, "resetAt": 0},
            }
        ],
    }

    *_, reason = launcher.account_pressure(account, "anthropic_top_thinking")

    assert reason == "left: top-thinking 100% · 5h 100% · weekly 18% · fable out (0% left)"


def test_account_pressure_falls_back_to_fable_availability_without_scoped_usage() -> None:
    launcher = load_launcher_module()

    account = {
        "alias": "Claude B",
        "fableEligible": False,
        "usage": {"primaryRemainingPercent": 100, "secondaryRemainingPercent": 18},
        "additionalQuotas": [
            {
                "quotaKey": "anthropic_top_thinking",
                "primaryWindow": {"usedPercent": 0, "resetAt": 0},
            }
        ],
    }

    *_, reason = launcher.account_pressure(account, "anthropic_top_thinking")

    assert reason == "left: top-thinking 100% · 5h 100% · weekly 18% · fable out"


def test_account_pressure_omits_unknown_fable_state() -> None:
    launcher = load_launcher_module()

    account = {
        "alias": "Claude C",
        "usage": {"primaryRemainingPercent": 100, "secondaryRemainingPercent": 41},
        "additionalQuotas": [
            {
                "quotaKey": "anthropic_top_thinking",
                "primaryWindow": {"usedPercent": 0, "resetAt": 0},
            }
        ],
    }

    *_, reason = launcher.account_pressure(account, "anthropic_top_thinking")

    assert reason == "left: top-thinking 100% · 5h 100% · weekly 41%"


def test_should_wait_for_reset_honors_mode_and_deadline(monkeypatch) -> None:
    launcher = load_launcher_module()
    now = time.time()

    monkeypatch.delenv("CLAUDE_LB_WAIT_FOR_LIMIT", raising=False)
    assert launcher.should_wait_for_reset(int(now + 60), now + 3600) is True
    assert launcher.should_wait_for_reset(int(now + 7200), now + 3600) is False
    assert launcher.should_wait_for_reset(None, now + 3600) is False

    monkeypatch.setenv("CLAUDE_LB_WAIT_FOR_LIMIT", "never")
    assert launcher.should_wait_for_reset(int(now + 60), now + 3600) is False


def test_build_resume_command_preserves_model_and_output_format(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.delenv("CC_EFFORT_LEVEL", raising=False)
    monkeypatch.delenv("CC_PERMISSION_MODE", raising=False)

    command = launcher.build_resume_command(
        ["-p", "do the thing", "--model", "claude-fable-5", "--output-format", "json"],
        "11111111-2222-3333-4444-555555555555",
    )

    assert command[:1] == ["claude"]
    assert command[command.index("--model") + 1] == "claude-fable-5"
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--resume") + 1] == "11111111-2222-3333-4444-555555555555"
    assert "-p" in command
    assert "do the thing" not in command


def test_headless_invocation_detects_print_flags() -> None:
    launcher = load_launcher_module()

    assert launcher.headless_invocation(["-p", "hi"]) is True
    assert launcher.headless_invocation(["--print", "hi"]) is True
    assert launcher.headless_invocation(["chat"]) is False


def test_shim_connect_error_is_retryable_only_for_connection_failures() -> None:
    import socket
    import urllib.error

    launcher = load_launcher_module()
    classify = launcher._is_retryable_shim_connect_error

    # Connection refused/reset (LB restarting) — safe to retry, request never landed.
    assert classify(ConnectionRefusedError()) is True
    assert classify(ConnectionResetError()) is True
    assert classify(urllib.error.URLError(ConnectionRefusedError())) is True

    # A real HTTP response — never retried; its status passes straight through.
    assert classify(urllib.error.HTTPError("http://lb", 503, "Service Unavailable", {}, None)) is False

    # Read timeout — request may be in flight, so do not re-send (no double-process).
    assert classify(urllib.error.URLError(socket.timeout())) is False
    assert classify(RuntimeError("boom")) is False
