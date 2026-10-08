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


def invoke(command, shell=None, **tokens):
    env = {key: value for key, value in os.environ.items()
           if key not in {'RAILWAY_TOKEN', 'RAILWAY_API_TOKEN'}}
    env.update(tokens)
    return subprocess.run([shell or os.environ.get('RAILWAY_TEST_SHELL', '/bin/bash'), str(HOOK)], input=json.dumps({
        'tool_name': 'Bash', 'tool_input': {'command': command},
    }), text=True, capture_output=True, env=env)


@pytest.mark.parametrize('command', [
    'railway up', 'railway deploy', 'railway redeploy',
    'railway environment new staging',
    'railway service delete api', 'env railway up', 'sudo railway up',
    'npx railway up', 'bunx railway up', 'sh -c "railway up"',
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
    'railway status', 'railway logs', 'railway environment', 'echo ordinary',
    # Live overblock after 7a71805e: paths and prose that merely name the guard.
    'cmp config/coding-agents/hooks/railway-vars-guard.sh ~/.claude/hooks/railway-vars-guard.sh',
    'python3 -m pytest -q tests/test_railway_write_guard.py',
    'env PYTHONPATH=. python3 tests/test_railway_write_guard.py',
    'nohup cat ~/.claude/hooks/railway-vars-guard.sh',
    'git commit -m "fix railway guard overblock"',
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



# hook-dispatcher-4 review (2026-10-08): the custody rewrite (7a71805e) dropped the earlier guard's refusal of
# reads that print every secret value; `railway variables --kv` was allowed. These print values whatever token is
# in custody.
@pytest.mark.parametrize('command,denial', [
    ('railway variables --kv', 'railway variables read prints values'),
    ('RAILWAY_TOKEN=x railway variables --kv', 'railway variables read prints values'),
    ('RAILWAY_TOKEN=x railway variables -s api -k', 'railway variables read prints values'),
    ('echo "$(railway variables --kv)"', 'railway variables read prints values'),
    ('railway run printenv', 'railway run printenv/env prints every secret'),
    ('RAILWAY_TOKEN=x railway run -s api -- env', 'railway run printenv/env prints every secret'),
])
def test_reads_that_print_secret_values_are_refused_even_with_custody(command, denial):
    result = invoke(command)
    assert result.returncode == 2, result.stderr
    assert denial in result.stderr
    assert "value output is refused" in result.stderr

# Review F1-F7: subprocess JSON boundary covers missed executable routes and
# overblocked data without executing the CLI or adding a production seam.
@pytest.mark.parametrize('command,tokens,expected', [
    *[(f'{runner} @railway/cli up', {}, 2) for runner in
      ['npx', 'bunx', 'pnpm dlx', 'yarn dlx', 'npm exec', 'npm exec --']],
    ('eval "railway up"', {}, 2),
    ('env -S "railway up"', {}, 2),
    ('RAILWAY_TOKEN=x npx @railway/cli variables --set API_TOKEN=synthetic', {}, 2),
    ('RAILWAY_TOKEN=x eval "railway variables --set API_TOKEN=synthetic"', {}, 2),
    ('RAILWAY_TOKEN=x env -S "railway variables --set API_TOKEN=synthetic"', {}, 2),
    ('echo "$(railway up --service \'api(staging)\')"', {}, 2),
    ('echo "`railway up --service \'api(staging)\'`"', {}, 2),
    ('railway variables < /dev/null --set PORT=8080', {}, 0),
    ('RAILWAY_TOKEN=x railway variables --set < /dev/null API_TOKEN=synthetic', {}, 2),
    ('RAILWAY_TOKEN="$MISSING" railway up', {}, 2),
    ('RAILWAY_TOKEN="" railway up', {}, 2),
    ('RAILWAY_TOKEN="$CUSTODY" railway up', {'CUSTODY': 'synthetic'}, 0),
    ('env -uRAILWAY_TOKEN railway up', {'RAILWAY_TOKEN': 'synthetic'}, 2),
    ('env -u RAILWAY_TOKEN railway up', {'RAILWAY_TOKEN': 'synthetic'}, 2),
    ("nohup echo okay\ncat <<'EOF'\nwe use railway for deployment\nEOF\n", {}, 0),
    ('cat <<"EOF"\n$(railway up)\nEOF\n', {}, 0),
    ('cat <<EOF\nrailway up\nEOF\n', {}, 0),
    ('cat <<EOF\n$(railway up)\nEOF\n', {}, 2),
    ("printf '%s\\n' railway", {}, 0),
    ('cat /tmp/railway', {}, 0),
    ('timeout 10 railway status', {}, 0),
    ('timeout 10 railway up', {}, 2),
    ('timeout 10 railway up', {'RAILWAY_TOKEN': 'synthetic'}, 0),
    *[(f'{wrapper} railway up', {}, 2) for wrapper in
      ['nice', 'nice -n 5', 'time', 'exec', 'command', 'xargs']],
    ('npx @railway/cli up', {'RAILWAY_TOKEN': 'synthetic'}, 0),
    ('eval "railway up"', {'RAILWAY_TOKEN': 'synthetic'}, 0),
    ('env -S "railway up"', {'RAILWAY_TOKEN': 'synthetic'}, 0),
])
def test_review_counterexamples(command, tokens, expected):
    result = invoke(command, **tokens)
    assert result.returncode == expected, result.stderr
    assert 'synthetic' not in result.stderr


# R3 floor regressions: real hook decisions for numeric argv, unknown custody,
# sudo options and executable heredoc expansions; no CLI or test-only seam.
@pytest.mark.parametrize('command,tokens,expected', [
    ('RAILWAY_TOKEN=x railway variables --set API_TOKEN 1234 > /dev/null', {}, 2),
    ('RAILWAY_TOKEN=x railway variables --set API_TOKEN 1234 2 > /dev/null', {}, 2),
    ('railway status 2>/dev/null', {}, 0),
    ('RAILWAY_TOKEN=$() railway up', {}, 2),
    ('RAILWAY_TOKEN="$(printf \'\')" railway up', {}, 2),
    ('RAILWAY_API_TOKEN="$(unknown)" railway up', {}, 2),
    ('RAILWAY_TOKEN="$(unknown)" railway up', {'RAILWAY_TOKEN': 'synthetic'}, 0),
    *[(f'RAILWAY_TOKEN=x sudo {options} railway variables --set API_TOKEN=synthetic', {}, 2)
      for options in ['-n', '-u USER', '-E', '--', '-n -u USER -E --', '--user USER']],
    ('sudo -n railway up', {}, 2),
    ('sudo -n railway status', {}, 0),
    ('# docs: cat <<EOF\nrailway up\n', {}, 2),
    ("echo '<<EOF'\nrailway up\n", {}, 2),
    ('echo "<<EOF"\nrailway up\n', {}, 2),
    ("cat <<EOF\n'$(railway up)'\nEOF\n", {}, 2),
    ("cat <<EOF\n'`railway up`'\nEOF\n", {}, 2),
    ("cat <<EOF\n'$(RAILWAY_TOKEN=x railway variables --set API_TOKEN=synthetic)'\nEOF\n", {}, 2),
    ("cat <<EOF\n'`RAILWAY_TOKEN=x railway variables --set API_TOKEN=synthetic`'\nEOF\n", {}, 2),
    ("cat <<'EOF'\n'$(railway up)'\nEOF\n", {}, 0),
])
def test_r3_counterexamples(command, tokens, expected):
    result = invoke(command, **tokens)
    assert result.returncode == expected, result.stderr
    assert 'synthetic' not in result.stderr


# R4 floor regressions: comments must preserve command boundaries, here-strings
# must not swallow later commands, and sudo bundles must expose executable argv.
@pytest.mark.parametrize('command,tokens,expected', [
    ('echo hi # note\nrailway up', {}, 2),
    ("echo hi # owner's note\nrailway status", {}, 0),
    ("echo hi # owner's note\nrailway up", {}, 2),
    ('echo hi # $(railway up)\nrailway status', {}, 0),
    ("echo '# literal'\nrailway up", {}, 2),
    ('cat <<< hello\nrailway up', {}, 2),
    ('cat <<< hello\nrailway status', {}, 0),
    ('cat <<< hello\nrailway up', {'RAILWAY_TOKEN': 'synthetic'}, 0),
    *[(f'sudo {options} railway up', {}, 2) for options in
      ['-nu USER', '-Eu USER', '-nuUSER', '-EuUSER', '-nEu USER', '-ng GROUP']],
    *[(f'sudo {options} railway status', {}, 0) for options in
      ['-nu USER', '-Eu USER']],
    *[(f'RAILWAY_TOKEN=x sudo {options} railway variables --set API_TOKEN=synthetic', {}, 2)
      for options in ['-nu USER', '-Eu USER']],
    ('sudo -nu USER railway up', {'RAILWAY_TOKEN': 'synthetic'}, 0),
])
def test_r4_counterexamples(command, tokens, expected):
    result = invoke(command, **tokens)
    assert result.returncode == expected, result.stderr
    assert 'synthetic' not in result.stderr


@pytest.mark.xfail(strict=True, reason='Accepted deliberate shell obfuscation residual')
@pytest.mark.parametrize('command,tokens', [
    ("rail''way up", {}),
    ('rail\\way up', {}),
    ("bash -lc 'unset RAILWAY_TOKEN; railway up'", {'RAILWAY_TOKEN': 'synthetic'}),
])
def test_deliberate_obfuscation_residuals(command, tokens):
    assert invoke(command, **tokens).returncode == 2


# Pre-GA policy at the real hook JSON/subprocess boundary. These cases protect
# session-based non-prod writes, production custody, and the retained output
# floor; wrappers and short flags would otherwise bypass the new branch.
@pytest.mark.parametrize('command,expected', [
    ('railway variables --set FOO=bar', 0),
    ('railway variables -s FOO=bar', 0),
    ('railway variables -sFOO=bar', 0),
    ('railway variables -sAPI_TOKEN=synthetic', 2),
    ('railway variable set FOO bar --environment test', 0),
    ('railway variables delete FOO -e dev', 0),
    ('env sh -c "railway variables --set FOO=bar"', 0),
    ('eval "railway variables --set FOO=bar"', 0),
    ('env -S "railway variables --set FOO=bar"', 0),
    ('railway variables --set FOO=bar --environment production', 2),
    ('railway variables --set FOO=bar -e prod', 2),
    ('railway variables delete FOO --environment=PRODUCTION', 2),
    ('railway variables --set FOO=bar -eprod', 2),
    ('railway -e production variables --set FOO=bar', 2),
    ('RAILWAY_TOKEN=x railway variables --set FOO=bar -e prod', 0),
    ('railway variables --set API_TOKEN=synthetic -e dev', 2),
    ('railway variables -s API_TOKEN synthetic', 2),
    ('eval "railway variables --set API_TOKEN=synthetic"', 2),
    ('env -S "railway variables --set API_TOKEN=synthetic"', 2),
    ('railway variables', 2),
    ('railway variables --json', 2),
    ('railway variables get FOO', 2),
    ('RAILWAY_TOKEN=x railway variables get FOO', 2),
    ('railway variables --set FOO=bar --json', 2),
    ('railway up --environment dev', 2),
])
def test_pre_ga_variable_policy(command, expected):
    result = invoke(command)
    assert result.returncode == expected, result.stderr
    assert 'synthetic' not in result.stderr
    assert 'bar' not in result.stderr


def test_unknown_link_write_is_logged_without_values():
    result = invoke('railway variables --set FOO=bar')
    assert result.returncode == 0, result.stderr
    assert 'unknown linked environment' in result.stderr
    assert 'FOO' not in result.stderr
    assert 'bar' not in result.stderr
