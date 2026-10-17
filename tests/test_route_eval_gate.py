from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / 'clients/route'
FIXTURES = ROOT / 'tests/fixtures/outcomes-replay/eval'


@pytest.fixture
def policy(tmp_path):
    table = {
        'policy_rev': 'routing-v2-test',
        'standards': {'implement': {'arm_id': 'sol', 'seat': 'gpt-implementer', 'model': 'sol-latest'}},
        'explore': {'min_n_per_arm': 40, 'stop_after_nonaccepts': 3,
                    'arms': {'implement': [{'arm_id': 'muse', 'seat': 'muse-seat', 'model': 'muse-latest'}]}},
        'overrides': [],
    }
    path = tmp_path / 'managed/routing-table.json'
    path.parent.mkdir()
    path.write_text(json.dumps(table))
    outcomes = tmp_path / 'outcomes.jsonl'
    now = datetime.now(timezone.utc)
    rows = [{
        'job_id': f'{arm}-{i}', 'class': 'implement', 'arm_id': arm,
        'ts': (now - timedelta(hours=2, seconds=40 - i)).isoformat(),
        'outcome': 'accepted' if i < 38 else 'rejected', 'cost_usd': cost, 'wall_s': 20,
        'propensity': propensity, 'eligible_arms': ['sol', 'muse'],
        'attribution_complete': True, 'policy_rev': 'routing-v2-test',
    } for arm, cost, propensity in [('sol', .6, .8), ('muse', .4, .1)] for i in range(40)]
    outcomes.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    env = {key: value for key, value in os.environ.items() if not key.startswith(('ROUTE_', 'AGENT_LB_'))}
    env.update(HOME=str(tmp_path), ROUTE_TABLE=str(path), ROUTE_OUTCOMES=str(outcomes),
               ROUTE_EVAL_STANDINGS=str(tmp_path / 'missing-eval-standings.json'))
    return path, outcomes, env


def run(env, *args, rc=0):
    result = subprocess.run([sys.executable, str(ROUTE), *args], env=env, text=True,
                            capture_output=True, timeout=20)
    assert result.returncode == rc, result.stderr + result.stdout
    return result


def with_eval(policy, name):
    return {**policy[2], 'ROUTE_EVAL_STANDINGS': str(FIXTURES / f'{name}.json')}


def muse(report):
    return next(arm for arm in report['classes']['implement']['arms'] if arm['arm_id'] == 'muse')


@pytest.mark.parametrize('name, veto, reason', [
    ('veto-per-trial', True, 'per-trial upper bound'),
    ('veto-solved-all3', True, 'solved_all3 5'),
    ('no-veto', False, 'eval-ok'),
    ('missing', False, 'no-eval-evidence'),
])
def test_eval_gate_vetoes_only_worse_candidates(policy, name, veto, reason):
    env = with_eval(policy, name)
    report = json.loads(run(env, 'learn', '--json').stdout)
    gate = muse(report)['eval_gate']
    assert gate['veto'] is veto
    assert reason in gate['reason']
    assert [p['arm_id'] for p in report['proposals']] == ([] if veto else ['muse'])
    assert [p['arm_id'] for p in report['vetoed']] == (['muse'] if veto else [])


def test_eval_gate_ignores_mismatched_task_sets(policy, tmp_path):
    doc = json.loads((FIXTURES / 'veto-per-trial.json').read_text())
    doc['muse-med']['task_version_set'][0] = 't00@2'
    path = tmp_path / 'mismatch.json'
    path.write_text(json.dumps(doc))
    report = json.loads(run({**policy[2], 'ROUTE_EVAL_STANDINGS': str(path)}, 'learn', '--json').stdout)
    assert muse(report)['eval_gate']['reason'] == 'no-eval-evidence'
    assert [p['arm_id'] for p in report['proposals']] == ['muse']


def test_eval_rows_leave_production_standings_unchanged(policy):
    before = json.loads(run(policy[2], 'standings', '--json').stdout)
    now = datetime.now(timezone.utc)
    with policy[1].open('a') as stream:
        for i in range(30):
            stream.write(json.dumps({
                'job_id': f'eval:t{i}@1#1:muse', 'class': 'implement', 'arm_id': 'muse', 'source': 'eval',
                'ts': (now - timedelta(minutes=30)).isoformat(), 'outcome': 'rejected', 'cost_usd': .1,
                'propensity': 1, 'eligible_arms': ['sol', 'muse'], 'attribution_complete': True,
            }) + '\n')
    after = json.loads(run(policy[2], 'standings', '--json').stdout)
    assert after['classes'] == before['classes']
    assert after['proposals'] == before['proposals']
    evals = json.loads(run(policy[2], 'standings', '--source', 'eval', '--json').stdout)
    assert muse(evals)['n'] == 30
    assert evals['proposals'] == []
    text = run(policy[2], 'standings', '--source', 'eval').stdout
    assert 'MDD ~30pp per-trial at 30 trials/arm (alpha .1 one-sided, power .8)' in text


def test_explicit_promote_under_veto_needs_override(policy):
    env = with_eval(policy, 'veto-per-trial')
    before = policy[0].read_bytes()
    refused = run(env, 'learn', '--promote', 'implement', 'muse', '--reason', 'try', '--json', rc=3)
    assert 'eval gate veto' in refused.stderr
    assert policy[0].read_bytes() == before
    report = json.loads(run(env, 'learn', '--promote', 'implement', 'muse', '--reason', 'eval disagrees',
                            '--override-evidence', '--json').stdout)
    assert report['entry']['override_evidence'] is True
    assert report['entry']['eval_gate']['veto'] is True
    assert json.loads(policy[0].read_text())['standards']['implement']['arm_id'] == 'muse'
