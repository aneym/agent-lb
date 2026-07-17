from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "clients" / "codex-routing-guard"


def _run(config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD), "--config", str(config), "--provider", "agent-lb", *args],
        cwd=ROOT,
        env={
            **os.environ,
            "CODEX_ROUTING_GUARD_SKIP_HEALTH": "1",
            "CODEX_ROUTING_GUARD_LOG": str(config.parent / "guard.log"),
        },
        capture_output=True,
        text=True,
    )


def test_guard_is_silent_byte_stable_noop(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = """# keep this comment
model = "gpt-5.6-sol"
model_provider = "agent-lb"
approval_policy = "never"

[model_providers.agent-lb]
name = "Local Agent LB"
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = true

[mcp_servers.keep]
command = "keep-me"
"""
    config.write_text(original)

    result = _run(config)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert config.read_text() == original


def test_guard_repairs_direct_openai_rewrite_without_touching_unrelated_bytes(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = """# user comment
model_provider = "openai" # ChatGPT rewrote this
approval_policy = "on-request"

[model_providers.agent-lb]
name = "keep this name"
base_url = "https://wrong.example/codex" # keep comment
wire_api = "chat"
supports_websockets = false
requires_openai_auth = false
extra = "untouched"

[profiles.personal]
model = "gpt-5.6-sol"
"""
    config.write_text(original)

    result = _run(config)

    assert result.returncode == 0, result.stderr
    repaired = config.read_text()
    assert 'model_provider = "agent-lb" # ChatGPT rewrote this' in repaired
    assert 'base_url = "http://127.0.0.1:2455/backend-api/codex" # keep comment' in repaired
    assert 'wire_api = "responses"' in repaired
    assert 'supports_websockets = true' in repaired
    assert 'requires_openai_auth = true' in repaired
    assert 'name = "keep this name"' in repaired
    assert 'extra = "untouched"' in repaired
    assert '[profiles.personal]\nmodel = "gpt-5.6-sol"' in repaired


def test_guard_adds_one_missing_provider_table_and_is_idempotent(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('model_provider = "openai"\n\n[projects."/tmp"]\ntrust_level = "trusted"\n')

    first = _run(config)
    assert first.returncode == 0, first.stderr
    first_bytes = config.read_bytes()
    assert first_bytes.count(b"[model_providers.agent-lb]") == 1
    assert b'model_provider = "agent-lb"' in first_bytes

    second = _run(config)

    assert second.returncode == 0, second.stderr
    assert config.read_bytes() == first_bytes


def test_guard_refuses_malformed_toml_without_clobbering(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = b'model_provider = "openai"\n[broken\nvalue = true\n'
    config.write_bytes(original)

    result = _run(config)

    assert result.returncode != 0
    assert "invalid TOML" in result.stderr
    assert config.read_bytes() == original
    assert not list(tmp_path.glob(".config.toml.routing-guard.*"))


def test_guard_failure_before_atomic_replace_leaves_original_and_mode(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = b'model_provider = "openai"\n'
    config.write_bytes(original)
    config.chmod(0o640)
    env = {
        **os.environ,
        "CODEX_ROUTING_GUARD_SKIP_HEALTH": "1",
        "CODEX_ROUTING_GUARD_FAIL_BEFORE_REPLACE": "1",
        "CODEX_ROUTING_GUARD_LOG": str(tmp_path / "guard.log"),
    }

    result = subprocess.run(
        [sys.executable, str(GUARD), "--config", str(config), "--provider", "agent-lb"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "injected failure" in result.stderr
    assert config.read_bytes() == original
    assert config.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob(".config.toml.routing-guard.*"))


def test_guard_atomic_repair_preserves_mode_and_preview_is_non_mutating(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = b'model_provider = "openai"\n'
    config.write_bytes(original)
    config.chmod(0o600)

    preview = _run(config, "--preview")
    assert preview.returncode == 0, preview.stderr
    assert "Would repair" in preview.stdout
    assert config.read_bytes() == original

    repair = _run(config)

    assert repair.returncode == 0, repair.stderr
    assert config.stat().st_mode & 0o777 == 0o600
    assert config.read_bytes() != original


def test_guard_requires_healthy_local_agent_lb_before_repair(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = b'model_provider = "openai"\n'
    config.write_bytes(original)
    env = {
        **os.environ,
        "CODEX_ROUTING_GUARD_HEALTH_URL": "http://127.0.0.1:1/health",
        "CODEX_ROUTING_GUARD_LOG": str(tmp_path / "guard.log"),
    }

    result = subprocess.run(
        [sys.executable, str(GUARD), "--config", str(config), "--provider", "agent-lb"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "local Agent LB is unavailable" in result.stderr
    assert config.read_bytes() == original
