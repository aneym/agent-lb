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


def test_ccdex_build_command_locks_model_and_effort() -> None:
    launcher = load_launcher_module()
    launcher.CCDEX_MODE = True

    command = launcher.build_command(["--model", "claude-opus-4-6", "--effort=max", "-p", "hello"])

    assert command == ["claude", "--model", "gpt-5.6-sol", "--effort", "high", "-p", "hello"]


def test_ccdex_proxy_rewrite_is_scoped_to_messages_paths() -> None:
    launcher = load_launcher_module()

    assert launcher._ccdex_upstream_path("/v1/messages") == "/v1/ccdex/messages"
    assert launcher._ccdex_upstream_path("/v1/messages/count_tokens") == "/v1/ccdex/messages/count_tokens"
    assert launcher._ccdex_upstream_path("/api/organizations") == "/api/organizations"


def test_ccdex_capability_probe_requires_native_token_count(monkeypatch) -> None:
    launcher = load_launcher_module()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"input_tokens":12}'

    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda request, timeout: Response())
    monkeypatch.setattr(
        launcher,
        "lb_json",
        lambda path, **kwargs: {"accounts": [{"provider": "openai", "status": "active"}]},
    )

    assert launcher._probe_ccdex_at("http://127.0.0.1:2455", timeout=1) == (True, "")


def test_ccdex_capability_probe_rejects_endpoint_without_openai_pool(monkeypatch) -> None:
    launcher = load_launcher_module()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"input_tokens":12}'

    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda request, timeout: Response())
    monkeypatch.setattr(launcher, "lb_json", lambda path, **kwargs: {"accounts": []})

    assert launcher._probe_ccdex_at("http://127.0.0.1:2455", timeout=1) == (
        False,
        "no active OpenAI accounts",
    )


def test_ccdex_endpoint_uses_capability_probe_without_health_probe(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://127.0.0.1:2455")])
    monkeypatch.setattr(
        launcher,
        "_probe_health_at",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("redundant health probe")),
    )
    monkeypatch.setattr(launcher, "_probe_ccdex_at", lambda url, timeout: (True, ""))

    assert launcher.prepare_ccdex_endpoint() is True


def test_remote_preference_retains_local_fallback(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("CLAUDE_LB_LOCAL_URL", "http://127.0.0.1:2455")
    monkeypatch.setenv("CLAUDE_LB_BASE_URL", "https://studio.example:2455")
    monkeypatch.setenv("CLAUDE_LB_PREFER_REMOTE", "1")
    monkeypatch.delenv("CLAUDE_LB_LOCAL_PREFER", raising=False)

    assert launcher._lb_candidates() == [
        ("remote", "https://studio.example:2455"),
        ("local", "http://127.0.0.1:2455"),
    ]


def test_normal_launcher_claims_without_health_probe(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://127.0.0.1:2455")])
    monkeypatch.setattr(
        launcher,
        "_probe_health_at",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("redundant health probe")),
    )
    monkeypatch.setattr(
        launcher,
        "_claim_at_endpoint",
        lambda url, session_id, model, quota_key, deadline, request_timeout=None: (
            {"accountId": "account-1", "alias": "Local"},
            "",
        ),
    )
    monkeypatch.setattr(launcher, "lb_json", lambda *args, **kwargs: {"accounts": []})

    assert launcher.print_lb_banner([], "session-1") is True


def test_interactive_launcher_uses_ready_probe_without_eager_claim(monkeypatch) -> None:
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://127.0.0.1:2455")])
    monkeypatch.setattr(
        launcher,
        "_probe_health_at",
        lambda url, retries, timeout, gap: (True, ""),
    )
    monkeypatch.setattr(
        launcher,
        "_claim_at_endpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("interactive startup must not eagerly claim")),
    )

    assert launcher.prepare_interactive_endpoint() is True
    assert launcher.AGENT_LB_BASE_URL == "http://127.0.0.1:2455"


def test_ready_probe_targets_readiness_endpoint(monkeypatch) -> None:
    launcher = load_launcher_module()
    requested_paths: list[str] = []
    monkeypatch.setattr(
        launcher,
        "lb_json",
        lambda path, **kwargs: requested_paths.append(path) or {"status": "ready"},
    )

    assert launcher._probe_health_at("http://127.0.0.1:2455", retries=1, timeout=1.0, gap=0.0) == (True, "")
    assert requested_paths == ["/health/ready"]


def test_proxy_spawn_uses_portable_subprocess(monkeypatch, tmp_path) -> None:
    launcher = load_launcher_module()
    calls: list[list[str]] = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda command, **kwargs: calls.append(command))

    launcher._spawn_lb_proxy(tmp_path / "ready", "session", 456, "http://127.0.0.1:2455")

    assert calls and calls[0][1].endswith("claude-lb-launch")


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
            },
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
            },
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


def test_shim_connect_retry_budget_outlasts_watchdog_recovery() -> None:
    launcher = load_launcher_module()

    budget = sum(
        min(launcher.SHIM_CONNECT_BACKOFF_DEFAULT * (2**attempt), launcher.SHIM_CONNECT_BACKOFF_CAP_DEFAULT)
        for attempt in range(launcher.SHIM_CONNECT_RETRIES_DEFAULT)
    )

    # Watchdog revival of an unloaded LB takes up to ~65s (2 x 30s ticks +
    # startup); the shim must keep retrying well past that instead of
    # surfacing a 502 broken pipe to the agent.
    assert budget >= 100
