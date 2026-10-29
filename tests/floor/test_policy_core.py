from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config/coding-agents"
BUDGET = 7168


def installer():
    if str(SOURCE) not in sys.path:
        sys.path.insert(0, str(SOURCE))
    spec = importlib.util.spec_from_file_location("install_policy", SOURCE / "policy_layout.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def without_model_lines(text: str) -> str:
    return re.sub(r"(?m)^(model|effort): .*\n", "", text)


def core_text() -> str:
    return (SOURCE / installer().CORE_SOURCE).read_text()


@pytest.fixture
def installed(tmp_path: Path, policy_checkout):
    source = tmp_path / "policy-repo" / "config/coding-agents"
    shutil.copytree(SOURCE, source)
    clients = source.parent.parent / "clients"
    clients.mkdir()
    for path in [ROOT / "clients/route", *sorted((ROOT / "clients").glob("route_*.py"))]:
        shutil.copy2(path, clients / path.name)
    policy_checkout(tmp_path / "policy-repo")
    home = tmp_path / "home"
    settings = home / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    other = {"theme": "dark", "permissions": {"allow": ["Bash(ls:*)"]}, "customKey": [1, 2, {"x": "y"}]}
    settings.write_text(json.dumps(other, indent=2) + "\n")
    env = os.environ | {
        "HOME": str(home),
        "AGENT_LB_URL": "http://127.0.0.1:1",
        "ROUTE_MODELS_CACHE": str(tmp_path / "models-cache.json"),
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin")),
    }
    for key in ("ROUTE_FIXTURE_DIR", "ROUTE_TABLE"):
        env.pop(key, None)

    def install(*options: str) -> str:
        return subprocess.run(
            [sys.executable, str(source / "install-policy.py"), "--home", str(home), *options],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout

    with pytest.raises(subprocess.CalledProcessError) as missing:
        install("--print")
    assert str(home / ".claude/rules/floors.md") in missing.value.stderr
    floors = home / ".claude/rules/floors.md"
    floors.parent.mkdir(parents=True, exist_ok=True)
    floors.write_text("# Install fixture\n\n## Nested section\n\nFixture body.\n")
    Path(env["ROUTE_MODELS_CACHE"]).write_text(
        json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "models": ["gpt-6.1-sol"]})
    )
    install()
    return SimpleNamespace(home=home, env=env, settings=settings, other=other, install=install)


def test_installed_core_links_resolve_to_installed_sections(installed) -> None:
    models = (installed.home / ".claude/rules/models.md").read_text()
    linked = sorted(set(re.findall(r"`~/(\.claude/docs/models/[^`/]+\.md)`", models)))
    assert linked
    for relative in linked:
        target = installed.home / relative
        assert target.read_bytes() == (SOURCE / "routing" / target.name).read_bytes(), relative


def test_install_renders_core_within_budget_and_is_idempotent(installed) -> None:
    home, env, settings, other, install = (
        installed.home,
        installed.env,
        installed.settings,
        installed.other,
        installed.install,
    )

    def snapshot() -> dict[str, bytes]:
        return {str(path.relative_to(home)): path.read_bytes() for path in home.rglob("*") if path.is_file()}

    seat = (home / ".claude/agents/codex-verifier-local.md").read_text()
    assert re.search(r"(?m)^model: \S+$", seat) and re.search(r"(?m)^effort: \S+$", seat)
    assert without_model_lines(seat) == without_model_lines((SOURCE / "agents/codex-verifier-local.md").read_text())
    result = subprocess.run(
        [sys.executable, str(home / ".agent-lb/bin/route"), "policy", "snapshot", "--json"],
        env=env | {"ROUTE_TABLE": str(home / ".agent-lb/managed/coding-agents/routing-table.json")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["policy_rev"]
    models = home / ".claude/rules/models.md"
    full = home / ".claude/docs/models-full.md"
    rendered = models.read_text()
    codex = (home / ".codex/AGENTS.md").read_text()
    assert "## Install fixture\n\n### Nested section\n\nFixture body." in codex
    assert "{{inline:" not in codex
    for name in ("muse-seat", "muse-seat-local", "pi-durable-seat"):
        assert not (home / ".claude/agents" / f"{name}.md").exists()
        assert (home / ".agents/policy/coding-agents/agents" / f"{name}.md").is_file()
    assert len(models.read_bytes()) <= BUDGET, len(models.read_bytes())
    assert rendered.startswith(installer().MODELS_MARKER)
    assert rendered.split("\n\n", 1)[1] == core_text()
    assert not full.exists()
    assert "`~/.claude/docs/models/README.md`" in (home / ".agents/policy/coding-agents/ROUTING.md").read_text()
    installed_settings = json.loads(settings.read_text())
    for key, value in other.items():
        if key != "permissions":
            assert installed_settings[key] == value
    assert set(other["permissions"]["allow"]) <= set(installed_settings["permissions"]["allow"])
    first = snapshot()
    assert "already converged" in install()
    assert snapshot() == first
    install("--uninstall")
    assert not models.exists()
