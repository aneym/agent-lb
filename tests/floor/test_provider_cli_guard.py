import json
import os
from pathlib import Path
import subprocess

import pytest

GUARD = Path(__file__).resolve().parents[2] / "config/coding-agents/hooks/dangerous-command-guard.sh"
KEYS = ("RAILWAY_TOKEN", "RAILWAY_API_TOKEN", "VERCEL_TOKEN", "FLY_API_TOKEN", "NEON_API_KEY")


def run(command, home, **environment):
    env = {key: value for key, value in os.environ.items() if key not in KEYS}
    env.update(HOME=str(home), **environment)
    return subprocess.run(
        ["bash", str(GUARD)],
        input=json.dumps({"tool_input": {"command": command}}),
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )


@pytest.mark.parametrize(
    "command,blocked",
    [
        ("railway variable set X=1", True),
        ('RAILWAY_TOKEN="$(agent-secret get railway-token)" railway variable set X=1', False),
        ('echo "railway ssh"', False),
        ("ssh ax42 'railway ssh'", False),
        ("vercel deploy", True),
        ("gh run list", False),
        ('echo "$(railway status)"', True),
        ("echo '$(railway status)'", False),
        ("echo `railway status`", True),
        ("env railway status", True),
        ("env -i railway status", True),
        ("env RAILWAY_TOKEN=fixture railway status", False),
        ("command -- railway status", True),
        ("fly deploy", True),
        ("flyctl deploy", True),
        ("neonctl projects list", True),
        ("FLY_API_TOKEN=fixture flyctl deploy", False),
        ("NEON_API_KEY=fixture neonctl projects list", False),
        ("VERCEL_TOKEN=fixture vercel deploy", False),
        ("RAILWAY_API_TOKEN=fixture /usr/local/bin/railway status", False),
        ("export RAILWAY_TOKEN=fixture; railway status", False),
        ('RAILWAY_TOKEN="" railway status', True),
        ("echo railway; railway status", True),
        ("cat <<'EOF'\nrailway ssh\nEOF\n", False),
        ('bash -c "railway status"', True),
    ],
)
def test_provider_argv(command, blocked, tmp_path):
    result = run(command, tmp_path)
    assert result.returncode == (2 if blocked else 0), result.stderr
    if blocked:
        assert "kept floor: Alex's own" in result.stderr
    assert "fixture" not in result.stdout + result.stderr


def test_exported_hook_environment(tmp_path):
    result = run("railway ssh", tmp_path, RAILWAY_TOKEN="not-a-real-token")
    assert result.returncode == 0, result.stderr
    assert "not-a-real-token" not in result.stdout + result.stderr


def test_exact_cos_approval(tmp_path):
    root = tmp_path / ".open-factory/approvals"
    root.mkdir(parents=True)
    approval = root / "alex-provider.md"
    approval.write_text("Alex approved at 22:44Z\n`railway ssh`\n")
    assert run(f"railway ssh # alex-approval: {approval}", tmp_path).returncode == 0
    assert run(f"railway variable set X=1 # alex-approval: {approval}", tmp_path).returncode == 2


def test_existing_destructive_sql_floor(tmp_path):
    result = run('psql -c "DROP TABLE app"', tmp_path)
    assert result.returncode == 2
    assert "floor: destructive data" in result.stderr


def test_unparsable_non_floor_warns_and_allows(tmp_path):
    result = run('echo "unterminated railway', tmp_path)
    assert result.returncode == 0
    assert "WARNING:" in result.stderr


def test_installer_revision_refuses_stale_and_passes_descendant(tmp_path):

    import runpy

    installer = GUARD.parent.parent / "install-policy.py"
    verify = runpy.run_path(str(installer))["verify_install_revision"]
    remote = tmp_path / "remote.git"
    repo = tmp_path / "work"

    def git(*args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)

    git("init", "--bare", str(remote))
    git("clone", str(remote), str(repo))
    git("config", "user.email", "fixture@example.invalid", cwd=repo)
    git("config", "user.name", "Fixture", cwd=repo)
    git("checkout", "-b", "main", cwd=repo)
    (repo / "fixture").write_text("first")
    git("add", ".", cwd=repo)
    git("commit", "-m", "first", cwd=repo)
    first = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    git("push", "origin", "main", cwd=repo)
    source = repo / "config/coding-agents"
    source.mkdir(parents=True)
    verify(source)
    (repo / "fixture").write_text("second")
    git("commit", "-am", "second", cwd=repo)
    git("push", "origin", "main", cwd=repo)
    git("checkout", "--detach", first, cwd=repo)
    with pytest.raises(SystemExit, match="rebase onto origin/main"):
        verify(source)
    with pytest.raises(SystemExit):
        verify(source, True, None)
    verify(source, True, "fixture exercises logged override")
    git("checkout", "main", cwd=repo)
    (repo / "fixture").write_text("third")
    git("commit", "-am", "third", cwd=repo)
    verify(source)
