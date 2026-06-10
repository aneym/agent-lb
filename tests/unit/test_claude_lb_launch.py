from __future__ import annotations

import importlib.machinery
import importlib.util
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
        'claude-fable-5/anthropic_top_thinking; statuses: active=1, rate_limited=2. '
        'OpenAI accounts are not eligible for Claude routing.",'
        '"type":"server_error","code":"no_available_anthropic_accounts"}}'
    )

    assert launcher.format_lb_pick_error(body) == (
        "Claude accounts are all at limit or unavailable "
        "(3 accounts; active=1, rate_limited=2). OpenAI accounts cannot serve Claude."
    )


def test_format_lb_pick_error_handles_all_rate_limited_accounts() -> None:
    launcher = load_launcher_module()
    body = (
        '{"error":{"message":"3 Anthropic accounts exist, but none are selectable for '
        'claude-fable-5/anthropic_top_thinking; statuses: rate_limited=3. '
        'OpenAI accounts are not eligible for Claude routing.",'
        '"type":"server_error","code":"no_available_anthropic_accounts"}}'
    )

    assert launcher.format_lb_pick_error(body) == (
        "all Claude accounts are rate-limited (3 accounts). OpenAI accounts cannot serve Claude."
    )


def test_format_lb_pick_error_preserves_plain_text_body() -> None:
    launcher = load_launcher_module()

    assert launcher.format_lb_pick_error("upstream unavailable") == "upstream unavailable"
