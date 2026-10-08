"""Integration coverage of the custody/argv floor at the actual hook boundary.

The prior login-only guard allowed writes and wrapper bypasses. No existing
Railway coverage exercises hook JSON; subprocesses need no production seam.
Only synthetic credentials are used; Railway itself is never executed.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest

HOOK = Path(__file__).resolve().parents[1] / 'config/coding-agents/hooks/railway-vars-guard.sh'


def invoke(command, **tokens):
    env = {key: value for key, value in os.environ.items()
           if key not in {'RAILWAY_TOKEN', 'RAILWAY_API_TOKEN'}}
    env.update(tokens)
    return subprocess.run(['bash', str(HOOK)], input=json.dumps({
        'tool_name': 'Bash', 'tool_input': {'command': command},
    }), text=True, capture_output=True, env=env)


@pytest.mark.parametrize('command', [
    'railway up', 'railway deploy', 'railway redeploy',
    'railway variables --set PORT=8080', 'railway variables set PORT 8080',
    'railway variables delete PORT', 'railway environment new staging',
    'railway service delete api', 'env railway up', 'sudo railway up',
    'npx railway up', 'bunx railway up', 'sh -c "railway up"',
    'env sh -c "railway variables --set PORT=8080"',
    'echo okay; railway up', 'echo okay\nrailway up',
    'echo "$(railway up)"', 'RAILWAY_TOKEN=x echo okay; railway up',
    'RAILWAY_TOKEN=x env -u RAILWAY_TOKEN railway up',
    'RAILWAY_TOKEN=x env -i railway up',
])
def test_login_only_writes_are_refused(command):
    result = invoke(command)
    assert result.returncode == 2, result.stderr
    assert 'custody' in result.stderr
    assert 'stdin or an env file' in result.stderr


@pytest.mark.parametrize('command', [
    'RAILWAY_TOKEN=x railway up', 'RAILWAY_API_TOKEN=x railway deploy',
    'env RAILWAY_TOKEN=x railway redeploy',
    'RAILWAY_TOKEN=x sh -c "railway up"',
    'RAILWAY_TOKEN=x railway variables --set PORT=8080',
    'railway status', 'railway logs', 'railway variables',
    'railway variables --json', 'railway environment', 'echo ordinary',
])
def test_custody_writes_and_reads_are_allowed(command):
    result = invoke(command)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('token', ['RAILWAY_TOKEN', 'RAILWAY_API_TOKEN'])
def test_exported_custody_token_allows_write(token):
    result = invoke('railway up', **{token: 'synthetic'})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('command', [
    'RAILWAY_TOKEN=x railway variables --set API_TOKEN=synthetic',
    'RAILWAY_TOKEN=x railway variables --set=API_KEY=synthetic',
    'RAILWAY_TOKEN=x railway variables set API_SECRET=synthetic',
    'RAILWAY_TOKEN=x railway variables set API_SECRET synthetic',
    'RAILWAY_TOKEN=x railway variables --set API_KEY synthetic',
    'RAILWAY_TOKEN=x railway variables --set VALUE=sk-synthetic',
    'RAILWAY_TOKEN=x railway variables --set VALUE=abcdefghijklmnopqrstuvwxyz',
    'RAILWAY_TOKEN=x railway up --header "Bearer synthetic"',
    'env RAILWAY_TOKEN=x sh -c "railway variables --set API_TOKEN=synthetic"',
])
def test_secret_shaped_argv_is_refused_even_with_custody(command):
    result = invoke(command)
    assert result.returncode == 2
    assert 'synthetic' not in result.stderr
