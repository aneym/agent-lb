from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROUTE = Path(__file__).resolve().parents[2] / 'clients/route'


@pytest.fixture
def policy(tmp_path):
    table = {
        'policy_rev': 'routing-v2-test',
        'standards': {
            'implement': {'arm_id': 'sol', 'seat': 'gpt-implementer', 'model': 'sol-latest', 'effort': 'medium'},
            'plan': {'arm_id': 'opus', 'seat': 'planner', 'model': 'opus-latest', 'effort': 'medium'},
        },
        'explore': {
            'min_n_per_arm': 40, 'stop_after_nonaccepts': 3,
            'arms': {
                'implement': [
                    {'arm_id': 'muse', 'seat': 'muse-seat', 'model': 'muse-latest'},
                    {'arm_id': 'grok', 'seat': 'cursor-seat', 'model': 'grok-latest'},
                ],
                'plan': [{'arm_id': 'sonnet', 'seat': 'planner', 'model': 'sonnet-latest'}],
            },
        },
        'overrides': [],
    }
    path = tmp_path / 'managed/routing-table.json'
    path.parent.mkdir()
    path.write_text(json.dumps(table))
    outcomes = tmp_path / 'outcomes.jsonl'
    now = datetime.now(timezone.utc)
    rows = []
    for arm, count, cost, propensity in [('sol', 40, .64, .8), ('muse', 40, .40, .1), ('grok', 5, .2, .1)]:
        for i in range(count):
            rows.append({
                'job_id': f'{arm}-{i}', 'class': 'implement', 'arm_id': arm,
                'ts': (now - timedelta(hours=2, seconds=count-i)).isoformat(),
                'outcome': 'accepted' if i < 38 else 'rejected',
                'cost_usd': cost * 38 / 40, 'wall_s': 20,
                'propensity': propensity, 'eligible_arms': ['sol', 'muse', 'grok'],
                'attribution_complete': True, 'policy_rev': 'routing-v2-test',
            })
    rows.extend([
        {**rows[0], 'job_id': 'infra', 'arm_id': 'muse', 'outcome': 'infra_failure'},
        {**rows[0], 'job_id': 'pending', 'arm_id': 'muse', 'outcome': 'pending'},
        {**rows[0], 'job_id': 'unattributed', 'arm_id': 'muse', 'attribution_complete': False},
        {**rows[0], 'job_id': 'ineligible', 'arm_id': 'muse', 'eligible_arms': ['muse']},
    ])
    outcomes.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    env = {key: value for key, value in os.environ.items() if not key.startswith(('ROUTE_', 'AGENT_LB_'))}
    env.update(HOME=str(tmp_path), ROUTE_TABLE=str(path), ROUTE_OUTCOMES=str(outcomes))
    return path, outcomes, env


def run(policy, *args, rc=0):
    result = subprocess.run([sys.executable, str(ROUTE), *args], env=policy[2],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == rc, result.stderr + result.stdout
    return result


def append_rows(policy, rows):
    with policy[1].open('a') as stream:
        for row in rows:
            stream.write(json.dumps(row) + '\n')


def test_advisory_promotion_and_revert_roundtrip(policy):
    before = policy[0].read_bytes()
    report = json.loads(run(policy, 'learn', '--json').stdout)
    assert [(p['class'], p['arm_id']) for p in report['proposals']] == [('implement', 'muse')]
    assert policy[0].read_bytes() == before
    arms = {a['arm_id']: a for a in report['classes']['implement']['arms']}
    assert arms['muse']['n'] == 40
    assert arms['muse']['accepted'] == 38
    assert arms['muse']['attributed_share'] == pytest.approx(40 / 44)
    assert arms['grok']['n'] == 5
    assert arms['sol']['cost_per_accepted'] == pytest.approx(.64)
    assert arms['muse']['cost_per_accepted'] == pytest.approx(.40)
    run(policy, 'learn', '--promote', 'implement', 'grok', '--reason', 'too small', rc=2)
    assert policy[0].read_bytes() == before
    promoted = json.loads(run(policy, 'learn', '--promote', 'implement', 'muse',
                              '--reason', 'lower attributed cost', '--json').stdout)['entry']
    table = json.loads(policy[0].read_text())
    assert table['standards']['implement']['model'] == 'muse-latest'
    assert table['overrides'] == [promoted]
    assert {'n', 'accepted', 'lower_bound', 'cost_per_accepted', 'window', 'policy_rev'} <= promoted['evidence'].keys()
    assert promoted['reason'] == 'lower attributed cost'
    assert datetime.fromisoformat(promoted['ts']).tzinfo is not None
    run(policy, 'learn', '--revert', promoted['id'])
    table = json.loads(policy[0].read_text())
    assert table['standards']['implement']['model'] == 'sol-latest'
    assert table['overrides'][0]['reverted_at']


def test_claude_plan_promotion_waits_for_alex_and_logs_override(policy):
    run(policy, 'learn', '--promote', 'plan', 'sonnet', '--reason', 'explicit experiment',
        '--override-evidence')
    table = json.loads(policy[0].read_text())
    assert table['standards']['plan']['model'] == 'opus-latest'
    entry = table['overrides'][0]
    assert entry['pending_alex'] is True
    assert entry['override_evidence'] is True
    run(policy, 'learn', '--revert', entry['id'])
    assert json.loads(policy[0].read_text())['standards']['plan']['model'] == 'opus-latest'


def test_pauses_are_durable_idempotent_and_never_auto_lift(policy):
    seed = json.loads(policy[1].read_text().splitlines()[40])
    rows = [{**seed, 'job_id': f'failed-{i}', 'outcome': 'rejected',
             'ts': (datetime.now(timezone.utc) - timedelta(minutes=5-i)).isoformat()} for i in range(3)]
    append_rows(policy, rows)
    run(policy, 'learn', '--apply-pauses')
    run(policy, 'learn', '--apply-pauses')
    table = json.loads(policy[0].read_text())
    assert len(table['overrides']) == 1
    assert table['overrides'][0]['action'] == 'pause'
    assert table['standards']['implement']['arm_id'] == 'sol'
    append_rows(policy, [{**seed, 'job_id': 'later-accept', 'ts': datetime.now(timezone.utc).isoformat()}])
    run(policy, 'learn', '--apply-pauses')
    report = json.loads(run(policy, 'standings', '--json').stdout)
    muse = next(a for a in report['classes']['implement']['arms'] if a['arm_id'] == 'muse')
    assert muse['paused'] is True
    assert report['proposals'] == []


def test_legacy_purge_preserves_v2_entries_and_requires_apply(policy):
    run(policy, 'learn', '--promote', 'plan', 'sonnet', '--reason', 'explicit', '--override-evidence')
    table = json.loads(policy[0].read_text())
    retained = table['overrides'][0]
    table['overrides'].extend({'class': 'implement', 'demote': {'seat': f'old-{i}'}} for i in range(187))
    policy[0].write_text(json.dumps(table))
    run(policy, 'learn', '--purge-legacy', rc=2)
    report = json.loads(run(policy, 'learn', '--purge-legacy', '--apply', '--json').stdout)
    assert report['purged'] == 187
    assert json.loads(policy[0].read_text())['overrides'] == [retained]
    run(policy, 'learn', '--apply', rc=2)


def test_standings_lists_costs_and_exports_class_tables(policy, tmp_path):
    mdx = tmp_path / 'standings.mdx'
    report = json.loads(run(policy, 'standings', '--class', 'implement', '--json', '--mdx', str(mdx)).stdout)
    assert list(report['classes']) == ['implement']
    arms = {a['arm_id']: a for a in report['classes']['implement']['arms']}
    assert set(arms) == {'sol', 'muse', 'grok'}
    assert arms['muse']['cost_per_accepted'] == pytest.approx(.4)
    assert arms['sol']['cost_per_accepted'] == pytest.approx(.64)
    assert arms['muse']['median_wall_s'] == 20
    assert 'Routing standings' in mdx.read_text()
    assert 'Propose promoting muse for implement.' in mdx.read_text()


@pytest.mark.parametrize('mutation', ['cost_unknown', 'ineligible', 'bad_propensity', 'latest_pending', 'too_old'])
def test_invalid_or_incomplete_evidence_cannot_promote(policy, mutation):
    rows = [json.loads(line) for line in policy[1].read_text().splitlines()]
    for row in rows:
        if row['arm_id'] != 'muse':
            continue
        if mutation == 'cost_unknown':
            row['cost_usd'] = None
        elif mutation == 'ineligible':
            row['eligible_arms'] = ['muse']
        elif mutation == 'bad_propensity':
            row['propensity'] = 0
        elif mutation == 'too_old':
            row['ts'] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    if mutation == 'latest_pending':
        rows.extend({**row, 'ts': datetime.now(timezone.utc).isoformat(), 'outcome': 'pending'}
                    for row in list(rows) if row['arm_id'] == 'muse')
    policy[1].write_text(''.join(json.dumps(row) + '\n' for row in rows))
    before = policy[0].read_bytes()
    assert json.loads(run(policy, 'learn', '--json').stdout)['proposals'] == []
    run(policy, 'learn', '--promote', 'implement', 'muse', '--reason', 'insufficient', rc=2)
    assert policy[0].read_bytes() == before


@pytest.mark.parametrize('mutation', ['low_acceptance', 'no_savings', 'under_80_percent_cost'])
def test_quality_and_cost_thresholds_are_required(policy, mutation):
    rows = [json.loads(line) for line in policy[1].read_text().splitlines()]
    muse = [row for row in rows if row['arm_id'] == 'muse']
    if mutation == 'low_acceptance':
        for row in muse[:10]:
            row['outcome'] = 'rejected'
    elif mutation == 'no_savings':
        for row in muse:
            row['cost_usd'] = 1
    else:
        for row in muse[:9]:
            row['cost_usd'] = None
    policy[1].write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert json.loads(run(policy, 'learn', '--json').stdout)['proposals'] == []


def test_pause_records_a_past_stop_even_after_a_later_accept(policy):
    seed = json.loads(policy[1].read_text().splitlines()[40])
    now = datetime.now(timezone.utc)
    append_rows(policy, [{**seed, 'job_id': f'stop-{i}',
                          'ts': (now - timedelta(minutes=5-i)).isoformat(),
                          'outcome': 'rejected' if i < 3 else 'accepted'} for i in range(4)])
    before = policy[0].read_bytes()
    run(policy, 'learn')
    assert policy[0].read_bytes() == before
    run(policy, 'learn', '--apply-pauses')
    assert json.loads(policy[0].read_text())['overrides'][0]['action'] == 'pause'
