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



def warned(output):
    """Guard trim (Alex, 2026-10-08 09:18 ET): the workflow seat guard warns in one line and never denies."""
    return 'permissionDecision' not in output and output.get('additionalContext', '').startswith(
        'workflow-seat-guard (warn only, not blocked): ')

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
        env={**os.environ, 'ROUTE_TABLE': str(table or POLICY / 'routing-table.json'),
             'ROUTE_LEDGER': os.devnull, 'SEAT_GUARD_AGENTS_DIR': str(POLICY / 'agents'), **(env or {})},
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
    ("agent(p, {agentType:'opus-seat', model:'gpt-5.4-mini'})", 'no longer served'),
    ("const s = `r: ${await agent('x', {label:'y'})}`;", 'without agentType'),
    ("const s = `${await agent('x', {agentType:'opus-seat', model:'claude-planner'})}`;", 'no longer served'),
    ("const s = `outer ${`nested ${await agent('x', {label:'y'})}`}`;", 'without agentType'),
    ("agent(foo(a, b), {label: nested({agentType:'x'})})", 'without agentType'),
    ("agent(p, {agentType: seat}); agent(q, {label: 'y'})", 'without agentType'),
    ("/* agent(p, opts) */ agent(p, {label: ') , }'})", 'without agentType'),
    ("const text = `agent(p, {label:'y'})`; agent(p, {label:'y'})", 'without agentType'),
])
def test_static_violations_are_warned_never_denied(script, reason):
    output = invoke(script)
    assert warned(output)
    assert reason in output['additionalContext']


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
    # Generated args are a default, not a request: Astra's bridge alias runs only on its readmitted seat.
    ({'route': {'seats': {'plan': {'opts': {'agentType': 'sol-consult', 'model': 'astra-latest-high'}}}}}, True),
    ({'route': {'seats': {'plan': {'opts': {'agentType': 'astra-consult', 'model': 'astra-latest-high'}}}}}, False),
])
def test_seat_options_in_workflow_args_are_checked(args, denied):
    """Options a script reads from args are invisible to the static scan; `route workflow-args` output lands here."""
    output = invoke(ROUTED, args=args)
    assert warned(output) is denied
    if denied:
        assert 'Workflow args pin retired model' in output['additionalContext']


def test_retired_args_are_warned_even_when_script_cannot_be_parsed():
    """The hook must enforce args retirement before its fail-open script parser."""
    output = invoke(
        'if (false) /[)]/.test(")"); await agent("x", args.opts);',
        args={'opts': {'agentType': 'opus-seat', 'model': 'claude-sonnet-5'}},
    )
    assert warned(output)
    assert 'Workflow args pin retired model' in output['additionalContext']


@pytest.mark.parametrize('script', [
    "agent(p, {agentType:'opus-seat', model:'claude-sonnet-5'})",
    "const s = `${await agent('x', {agentType:'opus-seat', model:'claude-fable-5-1'})}`;",
    "agent(p, {'agentType': 'opus-seat', 'model': 'gpt-5.6-sol'})",
])
def test_a_literal_retired_model_is_an_explicit_request_and_runs(script):
    # Alex, 2026-10-05: allow a model off the ladder when it is asked for; only blocked ids are refused.
    output = invoke(script)
    assert 'permissionDecision' not in output
    assert 'explicitly requests a model off the default ladder' in output['additionalContext']


@pytest.mark.parametrize('alias', ['sonnet', 'haiku'])
def test_alias_is_checked_against_resolved_retirement(tmp_path, alias):
    table = tmp_path / 'routing.json'
    table.write_text(json.dumps(
        {'retired': ['claude-old-*'], 'aliases': {alias + '-latest': {'pinned': 'claude-old-1'}}}
    ))
    script = f"agent(p, {{agentType: seat, model: '{alias}'}})"
    assert warned(invoke(
        script, table=table, env={'ANTHROPIC_DEFAULT_' + alias.upper() + '_MODEL': ''}
    ))
    assert 'permissionDecision' not in invoke(
        script, table=table, env={'ANTHROPIC_DEFAULT_' + alias.upper() + '_MODEL': 'claude-current'}
    )


@pytest.mark.parametrize('script', ["agent(p, {label: 'y'}", "agent(p, {label: 'unterminated})", '/* unclosed'])
def test_parse_errors_warn(script):
    """S44 (hook-dispatcher-5, 2026-10-08): a floor guard denies when its scanner fails; it used to allow."""
    output = invoke(script)
    assert warned(output)
    assert 'read or parsed' in output['additionalContext']


def test_missing_files_and_invalid_hook_json_warn(tmp_path):
    """S44: input, script or routing table the guard cannot read denies the launch; it used to allow."""
    for output in [
        invoke(script_path=tmp_path / 'missing'),
        invoke('agent(p, {label: x})', table=tmp_path / 'missing'),
        invoke(raw='{'),
    ]:
        assert warned(output)
        assert 'read or parsed' in output['additionalContext']


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
    assert registered[0]['command'] == '/usr/bin/python3 "$HOME/.claude/hooks/workflow-seat-guard.py"'
    removed = module.reconcile_settings(installed, True)
    assert removed['hooks'] == original['hooks']
    assert module.reconcile_settings(removed, True) == removed
    assert module.reconcile_settings({}, False)['hooks']['PreToolUse'][-1]['matcher'] == 'Workflow'


def test_a_seat_defined_off_the_ladder_needs_a_request_and_the_request_is_logged(tmp_path):
    agents = tmp_path / 'agents'
    agents.mkdir()
    (agents / 'legacy-fable.md').write_text('---\nname: legacy-fable\nmodel: claude-fable-5-1\n---\nbody\n')
    ledger = tmp_path / 'dispatch.jsonl'
    env = {'SEAT_GUARD_AGENTS_DIR': str(agents), 'ROUTE_LEDGER': str(ledger)}
    silent = invoke("agent(p, {agentType: 'legacy-fable', label: 'x'})", env=env)
    assert warned(silent)
    assert "defined on 'claude-fable-5-1', and nothing asked for it" in silent['additionalContext']
    undefined = invoke("agent(p, {agentType: 'legacy-fable', model: undefined})", env=env)
    assert warned(undefined)
    from_args = invoke('agent(p, args.opts)', args={'opts': {'agentType': 'legacy-fable'}}, env=env)
    assert warned(from_args)
    assert "defined on 'claude-fable-5-1', and nothing asked" in from_args['additionalContext']
    assert not ledger.exists()
    asked = invoke("agent(p, {agentType: 'legacy-fable', model: 'claude-fable-5-1'});"
                   "agent(q, {agentType: 'opus-seat', model: 'astra-latest-high'})", env=env)
    assert 'permissionDecision' not in asked
    [record] = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert (record['event'], record['explicit_models']) == ('workflow_dispatch', [
        {'model': 'astra-latest-high', 'source': 'workflow'}, {'model': 'claude-fable-5-1', 'source': 'workflow'}])
    # Readmitted seats run their own family without asking.
    assert 'permissionDecision' not in invoke("agent(p, {agentType: 'fable-orchestrator'})")
    assert 'permissionDecision' not in invoke("agent(p, {agentType: 'astra-consult'})")
