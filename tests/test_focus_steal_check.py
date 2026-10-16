import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / 'config/coding-agents/hooks'
CHECK = HOOKS / 'focus-steal-check.py'
LAUNCHER = HOOKS / 'codex-local-launch.py'
GUARD = shutil.which('desktop-guard-focus') or str(Path.home() / '.local/bin/desktop-guard-focus')

pytestmark = pytest.mark.skipif(sys.platform != 'darwin' or not Path(GUARD).exists(),
                                reason='needs the Studio desktop-guard-focus tool')


@pytest.fixture
def env(tmp_path):
    return dict(os.environ, DESKTOP_GUARD_FOCUS=GUARD, DESKTOP_GUARD_FOCUS_STATE=str(tmp_path / 'focus'),
                HERDR_PANE_ID='w9:p9')


def note(env, pane='w9:p9'):
    done = subprocess.run([GUARD, 'note', '--tool', 'aside-tab', '--from', 'Granola', '--to', 'Aside'],
                          env=dict(env, HERDR_PANE_ID=pane), capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr


def since(env):
    return subprocess.run([sys.executable, str(CHECK), 'now'], env=env, capture_output=True, text=True).stdout.strip()


def test_a_steal_from_this_pane_fails_and_another_panes_does_not(env):
    start = since(env)
    note(env, pane='w1:p1')
    assert subprocess.run([sys.executable, str(CHECK), '--since', start, 'x'], env=env).returncode == 0
    note(env)
    done = subprocess.run([sys.executable, str(CHECK), '--since', start, 'opus-seat-local'], env=env,
                          capture_output=True, text=True)
    assert done.returncode == 6 and 'FOCUS-STEAL opus-seat-local' in done.stderr


def stop(env, tmp_path, agent_type, active=False):
    transcript = tmp_path / 'agent.jsonl'
    transcript.write_text(json.dumps({'timestamp': '2000-01-01T00:00:00.000Z'}) + '\n')
    payload = dict(agent_type=agent_type, agent_transcript_path=str(transcript), stop_hook_active=active)
    return subprocess.run([sys.executable, str(CHECK), 'subagent-stop'], input=json.dumps(payload), env=env,
                          capture_output=True, text=True, timeout=30)


def test_a_local_seat_that_stole_focus_is_blocked_from_reporting_success(env, tmp_path):
    assert stop(env, tmp_path, 'opus-seat-local').stdout == ''
    note(env)
    out = json.loads(stop(env, tmp_path, 'opus-seat-local').stdout)
    assert out['decision'] == 'block' and 'FOCUS-STEAL opus-seat-local' in out['reason']
    assert stop(env, tmp_path, 'opus-seat').stdout == ''
    assert stop(env, tmp_path, 'opus-seat-local', active=True).stdout == ''


def test_the_codex_local_launcher_fails_a_run_that_stole_focus(env):
    child = f'import subprocess, time; time.sleep(10.5); subprocess.run([{GUARD!r}, "note", "--tool", "aside-tab"])'
    done = subprocess.run([sys.executable, str(LAUNCHER), sys.executable, '-c', child], env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 6 and 'FOCUS-STEAL codex-local' in done.stderr, done.stderr
