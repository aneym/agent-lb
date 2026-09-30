"""Widening contracts at the launcher, HTTP handler and upstream-header boundary."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.responses import StreamingResponse

from app.modules.proxy import api as proxy_api
from app.modules.proxy.anthropic_service import AnthropicProxyError, AnthropicProxyService, AnthropicProxyStream

LAUNCHER = Path(__file__).resolve().parents[2] / "clients" / "claude-lb-launch"


def load_launcher():
    loader = importlib.machinery.SourceFileLoader("widening_launcher", str(LAUNCHER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def launcher_env(tmp_path, kind, stage="all", tab="w5H:tC8"):
    table = tmp_path / "routing.json"
    table.write_text(json.dumps({"policy": {"stand_in_rollout": {"stage": stage, "tabs": ["w5H:tC8"]}}}))
    registry = tmp_path / ".agent-rails" / "workflows" / "kinds" / f"{tab.replace(':', '_')}.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(kind))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    herdr = bin_dir / "herdr"
    herdr.write_text(f"#!{sys.executable}\nimport json, os\nfrom pathlib import Path\n"
                     "Path(os.environ['HOME'], 'herdr-called').touch()\n"
                     "print(json.dumps({'result': {'tab': {'label': os.environ.get('TEST_TAB_LABEL', '')}}}))\n")
    herdr.chmod(0o755)
    return {"HOME": str(tmp_path), "PATH": f"{bin_dir}{os.pathsep}{os.defpath}",
            "ROUTE_TABLE": str(table), "HERDR_TAB_ID": tab,
            "CLAUDE_LB_DRY_RUN": "1", "CLAUDE_LB_DISABLE": "1"}


@pytest.mark.parametrize("kind", [
    {"lane": "Review-build"}, {"lane": "verify-money"}, {"lane": "AUDIT"},
    {"kind": "review"}, {"title": "[Review] routing"}, {"lane": "audit — unicode title"},
    *({"lane": lane} for lane in ("code_review", "review_build", "pr_review", "review2", "audit3",
                                  "verification", "verified-build", "reviews", "auditing")),
])
@pytest.mark.parametrize("stage", ["all", "half"])
def test_stand_in_review_launcher_untagged(tmp_path, kind, stage):
    env = launcher_env(tmp_path, kind, stage)
    result = subprocess.run([sys.executable, str(LAUNCHER)], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert "intent=none lane=none reason=review lane" in result.stderr
    assert "agent-lb tags" not in result.stdout
    env.update(AGENT_LB_INTENT="lane-tab", AGENT_LB_LANE="explicit")
    explicit = subprocess.run([sys.executable, str(LAUNCHER)], env=env, capture_output=True, text=True, timeout=5)
    assert "intent=lane-tab lane=explicit reason=explicit intent" in explicit.stderr


@pytest.mark.parametrize("tab,tagged", [("w5H:tC8", True), ("w5H:tab0", False), ("w5H:tab1", True)])
def test_stand_in_half_selection_stable(tmp_path, tab, tagged):
    env = launcher_env(tmp_path, {"lane": "build"}, "half", tab)
    for _ in range(2):
        result = subprocess.run([sys.executable, str(LAUNCHER)], env=env, capture_output=True, text=True, timeout=5)
        assert result.returncode == 0
        assert ("intent=lane-tab" in result.stderr) is tagged
        assert (tmp_path / "herdr-called").exists() is tagged


@pytest.mark.parametrize("label,tagged", [("[review] x", False), ("preview-build", True)])
def test_stand_in_herdr_label(tmp_path, label, tagged):
    env = launcher_env(tmp_path, {"lane": "build"}, "half")
    env["TEST_TAB_LABEL"] = label
    result = subprocess.run([sys.executable, str(LAUNCHER)], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert ("intent=lane-tab" in result.stderr) is tagged


@pytest.mark.parametrize("ccgpt", [False, True])
def test_stand_in_banner_and_ccgpt_tags(tmp_path, monkeypatch, capsys, ccgpt):
    env = launcher_env(tmp_path, {"lane": "build"})
    for name in ("AGENT_LB_INTENT", "AGENT_LB_LANE", "CLAUDECODE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AGENT_LB_INTENT", "")
    monkeypatch.setenv("AGENT_LB_LANE", "")
    monkeypatch.setenv("CLAUDE_LB_DISABLE", "0")
    launcher = load_launcher()
    monkeypatch.setattr(launcher, "CCGPT_MODE", ccgpt)
    monkeypatch.setattr(sys, "argv", [str(LAUNCHER)])
    monkeypatch.setattr(launcher, "_lb_candidates", lambda: [("local", "http://example.invalid")])
    monkeypatch.setattr(launcher, "_probe_ccgpt_at", lambda *a, **k: (True, None))
    monkeypatch.setattr(launcher, "_probe_interactive_ready", lambda *a, **k: ("ready", None))
    captured = {}

    def proxy(session_id):
        captured.update(launcher.tag_headers(os.environ))
        return "http://example.invalid"

    monkeypatch.setattr(launcher, "start_lb_proxy", proxy)
    for name in ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy", "NODE_EXTRA_CA_CERTS",
                 "ANTHROPIC_BASE_URL", "ANTHROPIC_UNIX_SOCKET", "_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL",
                 "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "")
    launcher.main()
    output = capsys.readouterr()
    assert "claude" in output.out
    assert captured == {"x-agent-lb-intent": "lane-tab", "x-agent-lb-lane": "build"}
    assert "stand-in: lane-tab (build)" in output.err
    assert ("ccgpt:" in output.err) is ccgpt
    monkeypatch.delenv("AGENT_LB_INTENT", raising=False)
    assert launcher.stand_in_banner() == "stand-in: off"
    monkeypatch.setenv("AGENT_LB_INTENT", "explicit\n\x1b[31m")
    assert launcher.stand_in_banner() == "stand-in: explicit???31m (lane: none)"
    monkeypatch.setenv("AGENT_LB_LANE", "build\n\x1b")
    assert launcher.stand_in_banner() == "stand-in: explicit???31m (build??)"


def test_stand_in_internal_headers_not_forwarded():
    from app.core.clients.proxy import filter_inbound_headers
    from app.modules.proxy.anthropic_service import _build_anthropic_headers

    inbound = {"X-Agent-Lb-Intent": "lane-tab", "x-agent-lb-lane": "build", "anthropic-version": "2023-06-01"}
    for headers in (filter_inbound_headers(inbound), _build_anthropic_headers(inbound, "fixture-access")):
        assert not any(key.lower() in {"x-agent-lb-intent", "x-agent-lb-lane"} for key in headers)
        assert headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", [
    "build", "preview-build", "Review-build", "VERIFY", "audit-money", "reviewer",
    "code_review", "review_build", "pr_review", "review2", "audit3", "verification",
    "verified-build", "reviews", "auditing", "review\x1b[31m" + "x" * 100,
])
async def test_stand_in_forced_swap_and_recovery(async_client, monkeypatch, tmp_path, caplog, lane):
    table = tmp_path / "routing.json"
    table.write_text(json.dumps({"policy": {"stand_in": {"lane-tab": "sol-latest-high"}}}))
    monkeypatch.setenv("ROUTE_TABLE", str(table))
    monkeypatch.setenv("AGENT_LB_STAND_IN_FILE", str(tmp_path / "stand-ins.json"))
    mode = {"failed": True}
    bridge_calls = []

    async def no_limits(*args, **kwargs):
        return None

    async def anthropic(self, payload, headers, **kwargs):
        async def body():
            if mode["failed"]:
                raise AnthropicProxyError(429, "fake upstream exhausted", code="rate_limit_error")
            yield (b'event: message_start\ndata: {"type":"message_start",'
                   b'"message":{"id":"msg_claude","model":"claude-opus-5-5"}}\n\n')
            yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        return AnthropicProxyStream(body=body(), media_type="text/event-stream")

    async def responses(request, payload, context, api_key, **kwargs):
        bridge_calls.append((kwargs["locked_model"], kwargs["locked_reasoning_effort"]))

        async def body():
            yield 'data: {"type":"response.created","response":{"id":"resp_sol","model":"gpt-6.1-sol"}}\n\n'
            yield 'data: {"type":"response.output_text.delta","delta":"fake sol answer"}\n\n'
            yield 'data: {"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":1}}}\n\n'
        return StreamingResponse(body(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_enforce_request_limits", no_limits)
    monkeypatch.setattr(AnthropicProxyService, "stream_messages", anthropic)
    monkeypatch.setattr(proxy_api, "_stream_responses", responses)
    caplog.set_level("INFO", logger=proxy_api.__name__)
    body = {"model": "claude-opus-5-5", "max_tokens": 10, "stream": True,
            "messages": [{"role": "user", "content": "next"}]}
    headers = {"x-agent-lb-intent": "lane-tab", "x-agent-lb-lane": lane, "x-claude-session-id": "forced-swap"}
    mode["failed"] = False
    healthy = await async_client.post("/v1/messages", json=body, headers=headers)
    assert "msg_claude" in healthy.text
    assert "stand_in_refused_review_lane" not in caplog.text
    mode["failed"] = True
    response = await async_client.post("/v1/messages", json=body, headers=headers)
    if lane not in {"build", "preview-build"}:
        assert "x-agent-lb-standing-in" not in response.headers
        assert bridge_calls == []
        assert response.status_code == 429
        expected_lane = "review??31m" + "x" * 69 if "\x1b" in lane else lane
        assert f"stand_in_refused_review_lane lane={expected_lane}" in caplog.text
        assert "\x1b" not in caplog.text
        assert "rate_limit_error" in response.text
        return
    assert response.status_code == 200 and "fake sol answer" in response.text
    assert bridge_calls == [("gpt-6.1-sol", "high")]
    assert response.headers["x-agent-lb-standing-in"] == "gpt-6.1-sol for claude-opus-5-5"
    mode["failed"] = False
    recovered = await async_client.post("/v1/messages", json=body, headers=headers)
    assert recovered.status_code == 200 and "msg_claude" in recovered.text
    assert "x-agent-lb-standing-in" not in recovered.headers
    assert len(bridge_calls) == 1
