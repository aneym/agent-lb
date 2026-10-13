"""Subprocess integration: real Bash hook inputs guard account sessions without executing provider CLIs."""
import json
import os
from pathlib import Path
import subprocess

import pytest

GUARD = Path(__file__).resolve().parents[1] / 'config/coding-agents/hooks/dangerous-command-guard.sh'
KEYS = ('RAILWAY_TOKEN', 'RAILWAY_API_TOKEN', 'VERCEL_TOKEN', 'FLY_API_TOKEN', 'NEON_API_KEY')


def run(command, home, **environment):
    env = {key: value for key, value in os.environ.items() if key not in KEYS}
    env.update(HOME=str(home), **environment)
    return subprocess.run(['bash', str(GUARD)], input=json.dumps({'tool_input': {'command': command}}),
                          text=True, capture_output=True, env=env, timeout=10)


@pytest.mark.parametrize('command,blocked', [
    ('railway variable set X=1', True),
    ('RAILWAY_TOKEN="$(agent-secret get railway-token)" railway variable set X=1', False),
    ('echo "railway ssh"', False),
    ("ssh ax42 'railway ssh'", False),
    ('vercel deploy', True),
    ('gh run list', False),
    ('echo "$(railway status)"', True),
    ("echo '$(railway status)'", False),
    ('echo `railway status`', True),
    ('env railway status', True),
    ('env -i railway status', True),
    ('env RAILWAY_TOKEN=fixture railway status', False),
    ('command -- railway status', True),
    ('fly deploy', True),
    ('flyctl deploy', True),
    ('neonctl projects list', True),
    ('FLY_API_TOKEN=fixture flyctl deploy', False),
    ('NEON_API_KEY=fixture neonctl projects list', False),
    ('VERCEL_TOKEN=fixture vercel deploy', False),
    ('RAILWAY_API_TOKEN=fixture /usr/local/bin/railway status', False),
    ('export RAILWAY_TOKEN=fixture; railway status', False),
    ('RAILWAY_TOKEN="" railway status', True),
    ('echo railway; railway status', True),
    ('cat <<\'EOF\'\nrailway ssh\nEOF\n', False),
    ('bash -c "railway status"', True),
])
def test_provider_argv(command, blocked, tmp_path):
    result = run(command, tmp_path)
    assert result.returncode == (2 if blocked else 0), result.stderr
    if blocked:
        assert "kept floor: Alex's own" in result.stderr
    assert 'fixture' not in result.stdout + result.stderr


def test_exported_hook_environment(tmp_path):
    result = run('railway ssh', tmp_path, RAILWAY_TOKEN='not-a-real-token')
    assert result.returncode == 0, result.stderr
    assert 'not-a-real-token' not in result.stdout + result.stderr


def test_exact_cos_approval(tmp_path):
    root = tmp_path / '.open-factory/approvals'
    root.mkdir(parents=True)
    approval = root / 'alex-provider.md'
    approval.write_text('Alex approved at 22:44Z\n`railway ssh`\n')
    assert run(f'railway ssh # alex-approval: {approval}', tmp_path).returncode == 0
    assert run(f'railway variable set X=1 # alex-approval: {approval}', tmp_path).returncode == 2
