from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_snapshot_install_and_drift(tmp_path: Path) -> None:
    source = tmp_path / 'repo/config/coding-agents'
    shutil.copytree(ROOT / 'config/coding-agents', source)
    clients = source.parents[1] / 'clients'
    clients.mkdir()
    home = tmp_path / 'home'
    home.mkdir()
    subprocess.run(['git', 'init', str(source.parents[1])], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(source.parents[1]), 'remote', 'add', 'origin', str(ROOT)], check=True)
    subprocess.run(['git', '-C', str(source.parents[1]), 'fetch', 'origin', 'main'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(source.parents[1]), 'reset', '--soft', 'origin/main'], check=True)
    seats = {}
    relays = {
        'codex-sol', 'codex-sol-local', 'codex-verifier', 'codex-test-runner',
        'codex-test-runner-local', 'gpt-implementer', 'gpt-explorer', 'sonnet-implementer',
        'verifier', 'cursor-seat', 'devin-seat', 'muse-seat', 'muse-seat-local',
        'computer-use', 'computer-use-local', 'host-relay',
    }
    for path in (source / 'agents').glob('*.md'):
        seats[path.stem] = {'model': 'claude-sonnet-5-5' if path.stem in relays else 'claude-opus-5-5',
                            'effort': 'low' if path.stem in relays else 'medium'}
    seats['fable-orchestrator']['model'] = 'claude-fable-5-1'
    snapshot = {'policy_rev': 'routing-20261016', 'table_sha256': 'a' * 64,
                'seats': seats, 'background_model': 'sonnet-latest',
                'standards': {'relay': {'wrapper_model': 'sonnet-latest'}},
                'resolved_models': {'sonnet-latest': 'claude-sonnet-5-5', 'haiku-latest': 'claude-haiku-5-5'}}
    fixture = tmp_path / 'snapshot.json'
    fixture.write_text(json.dumps(snapshot))
    (clients / 'route').write_text(
        'import json, os, sys\nfrom pathlib import Path\n'
        'if sys.argv[1:4] == ["policy", "snapshot", "--json"]:\n'
        ' print(Path(os.environ["SNAPSHOT_FIXTURE"]).read_text())\n'
        'elif sys.argv[1:] == ["resolve", "sonnet-latest"]: print("claude-sonnet-5-5")\n'
        'else: sys.exit(2)\n'
    )
    env = os.environ | {'HOME': str(home), 'SNAPSHOT_FIXTURE': str(fixture)}

    def run(*args: str, rc: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(source / 'install-policy.py'), '--home', str(home), *args],
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == rc, result.stdout + result.stderr
        return result

    table_path = home / '.agent-lb/managed/coding-agents/routing-table.json'
    table_path.parent.mkdir(parents=True)
    table_path.write_text(json.dumps({'overrides': [{'class': 'removed-class'}, {'class': 'implement'}]}))
    run()
    assert json.loads(table_path.read_text())['overrides'] == [{'class': 'implement'}]
    run('--check')
    for path in (home / '.claude/agents').glob('*.md'):
        assert f'model: {seats[path.stem]["model"]}\n' in path.read_text()
        assert '--effort xhigh' not in path.read_text()
        if path.stem != 'fable-orchestrator':
            assert 'fable' not in seats[path.stem]['model'].lower()
    settings = json.loads((home / '.claude/settings.json').read_text())
    assert settings['effortLevel'] == 'medium'
    assert settings['env']['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'claude-sonnet-5-5'
    assert json.loads((home / '.agent-lb/managed/coding-agents/policy-snapshot.json').read_text()) == snapshot
    settings_path = home / '.claude/settings.json'
    settings['effortLevel'] = 'high'
    settings_path.write_text(json.dumps(settings))
    run('--check', rc=1)
    run()
    managed_snapshot = home / '.agent-lb/managed/coding-agents/policy-snapshot.json'
    managed_snapshot.write_text('{}')
    run('--check', rc=1)
    run()
    seat = home / '.claude/agents/codex-sol.md'
    seat.write_text(seat.read_text().replace('model: claude-sonnet-5-5', 'model: haiku'))
    before = seat.read_bytes()
    run('--check', rc=1)
    assert seat.read_bytes() == before
    snapshot['standards']['relay']['wrapper_model'] = 'haiku-latest'
    fixture.write_text(json.dumps(snapshot))
    run()
    assert 'model: claude-haiku-5-5\n' in seat.read_text()
    run('--check')
    run('--uninstall')
    assert not seat.exists()
    settings = json.loads((home / '.claude/settings.json').read_text())
    assert 'ANTHROPIC_DEFAULT_HAIKU_MODEL' not in settings.get('env', {})
