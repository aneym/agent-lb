import base64
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/codex-pool-identity"
CONFIG = """model_provider = "agent-lb"

[model_providers.agent-lb]
name = "OpenAI"
base_url = "http://127.0.0.1:2456/backend-api/codex"
requires_openai_auth = false
"""


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--codex-home", str(home), *args], capture_output=True, text=True, timeout=10
    )


def test_codex_home_gets_pool_identity_once_and_real_login_is_kept(tmp_path: Path):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text(CONFIG)
    assert _run(home, "--check").returncode == 1

    assert _run(home).returncode == 0
    config = tomllib.loads((home / "config.toml").read_text())
    assert config["chatgpt_base_url"] == "http://127.0.0.1:2456"
    assert config["model_providers"]["agent-lb"]["requires_openai_auth"] is False
    auth = json.loads((home / "auth.json").read_text())
    assert auth["auth_mode"] == "chatgpt"
    assert auth["tokens"]["account_id"] == "agent-lb-pool"
    claims = json.loads(base64.urlsafe_b64decode(auth["tokens"]["id_token"].split(".")[1] + "=="))
    assert claims["exp"] > 4_000_000_000
    assert (home / "auth.json").stat().st_mode & 0o777 == 0o600

    first = ((home / "config.toml").read_text(), (home / "auth.json").read_text())
    assert _run(home).returncode == 0
    assert _run(home, "--check").returncode == 0
    assert (home / "config.toml").read_text() == first[0]
    assert (home / "auth.json").read_text() == first[1]

    real = tmp_path / "real"
    real.mkdir()
    (real / "config.toml").write_text(CONFIG)
    (real / "auth.json").write_text('{"tokens": {"account_id": "acct-real"}}')
    assert _run(real).returncode == 0
    assert (real / "auth.json").read_text() == '{"tokens": {"account_id": "acct-real"}}'
    assert "chatgpt_base_url" not in tomllib.loads((real / "config.toml").read_text())
