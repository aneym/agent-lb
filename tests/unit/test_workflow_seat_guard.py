from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / 'config/coding-agents'
HOOK = POLICY / 'hooks/workflow-seat-guard.py'


def invoke(script=None, *, script_path=None, table=None, raw=None, env=None, args=None):
    payload = (
        {'tool_input': {'script': script}} if script_path is None
        else {'tool_input': {'scriptPath': str(script_path)}}
    )
    if args is not None:
        payload['tool_input']['args'] = args
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=raw if raw is not None else json.dumps(payload), text=True, capture_output=True,
        env={**os.environ, 'ROUTE_TABLE': str(table or POLICY / 'routing-table.json'), **(env or {})},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)['hookSpecificOutput']


@pytest.mark.parametrize('script,reason', [
    ("agent('x', {label:'y'})", 'agent() without agentType: pass agentType (models.md, Workflows)'),
    ("agent('...', {label:'y'})", 'without agentType'),
    ("agent(`...`, {label:'y'})", 'without agentType'),
    ("agent(p, {'...': opts, label:'y'})", 'without agentType'),
    ("agent('x')", 'agent() without agentType: pass agentType (models.md, Workflows)'),
    ('await agent(p)', 'agent() without agentType: pass agentType (models.md, Workflows)'),
    ("agent('x',)", 'agent() without agentType: pass agentType (models.md, Workflows)'),
    ("agent(p, {agentType:'opus-seat', model:'claude-sonnet-5'})", 'retired model'),
    ("const s = `r: ${await agent('x', {label:'y'})}`;", 'without agentType'),
    ("const s = `${await agent('x', {agentType:'opus-seat', model:'claude-sonnet-5'})}`;", 'retired model'),
    ("const s = `outer ${`nested ${await agent('x', {label:'y'})}`}`;", 'without agentType'),
    ("agent(p, {'agentType': 'opus-seat', 'model': 'gpt-5.6-sol'})", 'retired model'),
    ("agent(foo(a, b), {label: nested({agentType:'x'})})", 'without agentType'),
    ("agent(p, {agentType: seat}); agent(q, {label: 'y'})", 'without agentType'),
    ("/* agent(p, opts) */ agent(p, {label: ') , }'})", 'without agentType'),
    ("const text = `agent(p, {label:'y'})`; agent(p, {label:'y'})", 'without agentType'),
])
def test_static_violations_are_denied(script, reason):
    output = invoke(script)
    assert output['permissionDecision'] == 'deny'
    assert reason in output['permissionDecisionReason']


@pytest.mark.parametrize('script', [
    'agent(p, {...opts, agentType: seat})',
    'agent(p, opts)',
    'agent(p, options())',
    'agent(...args)',
    'agent(p, {agentType: seat, ...opts})',
])
def test_dynamic_options_warn_without_blocking(script):
    output = invoke(script)
    assert 'permissionDecision' not in output
    assert 'dynamic agent() options' in output['additionalContext']


@pytest.mark.parametrize('script', [
    "agent(p, {agentType: 'opus-seat', model:'claude-sonnet-5-5'})",
    'agent(p, {agentType: seat})',
    "const text = `agent(p, {label:'y'})`;",
    "const text = `outer ${'agent(p, {label: x})'} ${`nested agent(p, {label: x})`}`;",
    "// agent(p, {label:'y'})\n/* agent(p, {model:'claude-sonnet-5'}) */",
    "const s = 'agent(p, {label: x})'; const r = /agent\\( [)]/;",
])
def test_safe_scripts_and_non_code_text_allow(script):
    assert invoke(script) == {'hookEventName': 'PreToolUse'}


def test_real_fold_script_allows_inline_and_by_path():
    fixture = ROOT / 'tests/fixtures/fold-pipeline-v2.js'
    for output in [invoke(fixture.read_text()), invoke(script_path=fixture)]:
        assert output['hookEventName'] == 'PreToolUse'
        assert 'permissionDecision' not in output
        assert 'could not be parsed' not in output.get('additionalContext', '')


ROUTED = "agent(p, {...R.seats.implement.opts, label: 'x'})"


@pytest.mark.parametrize('args,denied', [
    ({'route': {'seats': {'implement': {'opts': {'agentType': 'opus-seat', 'model': 'claude-sonnet-5'}}}}}, True),
    ({'pieces': [{'implementer': 'cursor-seat', 'model': 'gpt-5.6-sol'}]}, True),
    ({'route': {'seats': {'implement': {'opts': {'agentType': 'opus-seat', 'model': 'claude-opus-5-5'}}}}}, False),
    ({'rows': [{'model': 'claude-sonnet-5', 'requests': 3}]}, False),
    ('not an object', False),
])
def test_seat_options_in_workflow_args_are_checked(args, denied):
    """Options a script reads from args are invisible to the static scan; `route workflow-args` output lands here."""
    output = invoke(ROUTED, args=args)
    assert ('permissionDecision' in output) is denied
    if denied:
        assert 'Workflow args pin retired model' in output['permissionDecisionReason']


def test_retired_args_are_denied_even_when_script_cannot_be_parsed():
    """The hook must enforce args retirement before its fail-open script parser."""
    output = invoke(
        'if (false) /[)]/.test(")"); await agent("x", args.opts);',
        args={'opts': {'agentType': 'opus-seat', 'model': 'claude-sonnet-5'}},
    )
    assert output['permissionDecision'] == 'deny'
    assert 'Workflow args pin retired model' in output['permissionDecisionReason']


@pytest.mark.parametrize('alias', ['sonnet', 'haiku'])
def test_alias_is_checked_against_resolved_retirement(tmp_path, alias):
    table = tmp_path / 'routing.json'
    table.write_text(json.dumps(
        {'retired': ['claude-old-*'], 'aliases': {alias + '-latest': {'pinned': 'claude-old-1'}}}
    ))
    script = f"agent(p, {{agentType: seat, model: '{alias}'}})"
    assert invoke(
        script, table=table, env={'ANTHROPIC_DEFAULT_' + alias.upper() + '_MODEL': ''}
    )['permissionDecision'] == 'deny'
    assert 'permissionDecision' not in invoke(
        script, table=table, env={'ANTHROPIC_DEFAULT_' + alias.upper() + '_MODEL': 'claude-current'}
    )


@pytest.mark.parametrize('script', ["agent(p, {label: 'y'}", "agent(p, {label: 'unterminated})", '/* unclosed'])
def test_parse_errors_fail_open(script):
    output = invoke(script)
    assert 'permissionDecision' not in output
    assert 'not blocked' in output['additionalContext']


def test_missing_files_and_invalid_hook_json_fail_open(tmp_path):
    for output in [
        invoke(script_path=tmp_path / 'missing'),
        invoke('agent(p, {label: x})', table=tmp_path / 'missing'),
        invoke(raw='{'),
    ]:
        assert 'permissionDecision' not in output
        assert 'not blocked' in output['additionalContext']


def test_settings_install_uninstall_is_idempotent_and_preserves_user_hook():
    spec = importlib.util.spec_from_file_location('install_policy', POLICY / 'install-policy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    user_hook = {'type': 'command', 'command': 'user-workflow-hook'}
    original = {'hooks': {'PreToolUse': [{'matcher': 'Workflow', 'hooks': [user_hook]}]}}
    installed = module.reconcile_settings(original, False)
    assert module.reconcile_settings(installed, False) == installed
    workflow_groups = [g for g in installed['hooks']['PreToolUse'] if g['matcher'] == 'Workflow']
    assert len(workflow_groups) == 1
    registered = [h for h in workflow_groups[0]['hooks'] if h != user_hook]
    assert len(registered) == 1
    assert 'workflow-seat-guard.py' in registered[0]['command']
    assert '||' in registered[0]['command']
    removed = module.reconcile_settings(installed, True)
    assert removed['hooks'] == original['hooks']
    assert module.reconcile_settings(removed, True) == removed
    assert module.reconcile_settings({}, False)['hooks']['PreToolUse'][-1]['matcher'] == 'Workflow'
