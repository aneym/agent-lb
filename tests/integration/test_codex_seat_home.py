"""Dry seat startup through the actual home-writer process, using fixture config
and no credentials. Protects sandbox/network/transport and rollout layout at the
file boundary. Old global archive indexing is not duplicated in a mock.
"""
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / "config/coding-agents/codex-seat-home.py"


def test_dry_seat_home_transport_network_and_local_sessions(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "sessions"
    archive.mkdir()
    old = archive / "old-rollout.jsonl"
    old.write_text("old rollout stays untouched\n")
    (source / "config.toml").write_text('''model_provider = "agent-lb"
[mcp_servers.not-for-seats]
command = "never-run"
[features]
responses_websockets = true
responses_websockets_v2 = true
[model_providers.agent-lb]
name = "Agent LB"
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = true
[model_providers.unrelated]
base_url = "https://example.invalid"
''')
    job = tmp_path / "job"
    job.mkdir()
    (job / "sessions").symlink_to(archive, target_is_directory=True)
    env = os.environ | {"HOME": str(tmp_path / "home"), "CODEX_SOURCE_HOME": str(source)}

    def launch():
        result = subprocess.run([sys.executable, str(WRITER), str(job)], env=env,
                                capture_output=True, text=True, timeout=10, check=True)
        assert result.stdout.strip() == str(job)
        return tomllib.loads((job / "config.toml").read_text())

    cfg = launch()
    assert cfg["sandbox_mode"] == "workspace-write"
    assert cfg["sandbox_workspace_write"]["network_access"] is True
    assert cfg["approval_policy"] == "never"
    assert cfg["model_provider"] == "agent-lb"
    assert cfg["model_providers"]["agent-lb"]["supports_websockets"] is False
    assert cfg["model_providers"]["agent-lb"]["requires_openai_auth"] is True
    assert set(cfg["model_providers"]) == {"agent-lb"}
    assert cfg["features"]["responses_websockets"] is False
    assert cfg["features"]["responses_websockets_v2"] is False
    assert cfg["features"]["apps"] is False
    assert cfg["features"]["plugins"] is False
    assert "mcp_servers" not in cfg
    assert (job / "sessions").is_dir() and not (job / "sessions").is_symlink()
    assert old.read_text() == "old rollout stays untouched\n"
    assert not (job / "sessions/old-rollout.jsonl").exists()
    local = job / "sessions/local-rollout.jsonl"
    local.write_text("resume me\n")
    assert launch() == cfg
    assert local.read_text() == "resume me\n"


def test_dry_installed_writer_and_launcher_patch(tmp_path: Path):
    # Exercise the installed generator and apply the real external launcher patch.
    import shutil

    source = tmp_path / "policy"
    shutil.copytree(ROOT / "config/coding-agents", source)
    home = tmp_path / "home"
    result = subprocess.run([sys.executable, str(source / "install-policy.py"), "--home", str(home)],
                            env=os.environ | {"HOME": str(home), "AGENT_LB_URL": "http://127.0.0.1:1"},
                            capture_output=True, text=True, check=True, timeout=30)
    assert result.returncode == 0
    installed = home / ".agent-rails/workflows/codex-lab-home.py"
    job = tmp_path / "job"
    subprocess.run([sys.executable, str(installed), str(job)],
                   env=os.environ | {"HOME": str(home)}, check=True, capture_output=True, timeout=10)
    cfg = tomllib.loads((job / "config.toml").read_text())
    assert cfg["sandbox_workspace_write"]["network_access"] is True

    launcher = tmp_path / "cx-bg"
    launcher.write_bytes((ROOT / "tests/fixtures/codex-seat/cx-bg").read_bytes())
    with (ROOT / "patches/codex-seat/cx-bg.patch").open() as patch:
        subprocess.run(["patch", "--fuzz=0", "--forward", str(launcher)], stdin=patch,
                       capture_output=True, check=True, timeout=10)
    archive = home / ".codex/sessions"
    archive.mkdir(parents=True)
    (archive / "untouched").write_text("global archive")
    # The patched actual launcher fragment must not undo the writer's local layout.
    subprocess.run(["zsh", str(launcher)], env=os.environ | {"HOME": str(home), "N": "dry"},
                   check=True, capture_output=True, timeout=10)
    local = home / ".agent-rails/jobs/codex-homes/dry/sessions"
    assert local.is_dir() and not local.is_symlink()
    assert not (local / "untouched").exists()
    assert (archive / "untouched").read_text() == "global archive"
