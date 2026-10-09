"""Subprocess integration at the hook/file boundary; only the curl API edge is fake."""
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(tmp_path):
    home = tmp_path / 'home'
    hooks = home / '.jev/hooks'
    hooks.mkdir(parents=True)
    source = Path(os.environ.get('JEV_HOOK_ROOT', ROOT / 'config/coding-agents/hooks'))
    for dest, name in [('route.sh', 'jev-route.sh'), ('health-check.sh', 'jev-health-check.sh'), ('jev-health.py', 'jev-health.py')]:
        if (source / name).exists():
            shutil.copyfile(source / name, hooks / dest)
    (home / '.jev/.env').write_text('TYPESAFE_API_KEY=fixture-only\n')
    binary = tmp_path / 'bin'
    binary.mkdir()
    curl = binary / 'curl'
    curl.write_text('''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
Path(os.environ['CALLS']).write_text(json.dumps({'env': [k for k in os.environ if k.lower().endswith('_proxy')], 'args': sys.argv[1:]}))
time.sleep(float(os.environ.get('DELAY', '0')))
if os.environ.get('FAIL') == '1':
    print('000')
    sys.exit(7)
print('{"answers":{"alive":true},"model":"fixture"}\\n200')
''')
    curl.chmod(0o755)
    env = {**os.environ, 'HOME': str(home), 'PATH': str(binary) + ':' + os.environ['PATH'], 'CALLS': str(tmp_path / 'calls')}
    return home, hooks, env


def run(setup, name, **extra):
    _, hooks, env = setup
    return subprocess.run(['bash', str(hooks / name)], env={**env, **extra}, text=True, capture_output=True, timeout=12)


def test_cached_ok_suppresses_stale_alert(setup):
    home, _, env = setup
    (home / '.jev/ALERT').write_text('down: unexpected status 000')
    (home / '.jev/status.json').write_text(json.dumps({'status': 'ok', 'checkedAt': (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=6)).isoformat()}))
    result = run(setup, 'route.sh')
    assert result.returncode == 0
    assert result.stdout == ''
    assert not Path(env['CALLS']).exists()


def test_proxy_isolation_and_timeout(setup):
    result = run(setup, 'health-check.sh', HTTPS_PROXY='http://invalid:1', https_proxy='http://invalid:1', ALL_PROXY='http://invalid:1')
    assert result.returncode == 0
    call = json.loads(Path(setup[2]['CALLS']).read_text())
    assert call['env'] == []
    assert call['args'][call['args'].index('--max-time') + 1] == '40'
    assert 'fixture-only' not in json.dumps(call)


def age_first_failure(home, seconds):
    """Fake the clock at the state-file boundary: the recorded first failure happened `seconds` ago."""
    state_path = home / '.jev/alert-state.json'
    state = json.loads(state_path.read_text())
    state['firstFailureAt'] -= seconds
    state_path.write_text(json.dumps(state))


def test_failures_inside_a_minute_are_quiet(setup):
    """2026-10-08: two failures seconds apart (one blip seen by launchd and a prompt refresh) alerted."""
    for _ in range(3):
        assert run(setup, 'health-check.sh', FAIL='1').returncode == 0
        assert not (setup[0] / '.jev/ALERT').exists()
    assert run(setup, 'route.sh', FAIL='1').stdout == ''


def test_two_failures_a_minute_apart_alert(setup):
    assert run(setup, 'health-check.sh', FAIL='1').returncode == 0
    age_first_failure(setup[0], 61)
    assert run(setup, 'health-check.sh', FAIL='1').returncode == 0
    result = run(setup, 'route.sh', FAIL='1')
    assert result.stdout == '[jev] UNAVAILABLE: two consecutive health probes failed. Fall back to your own model for decisions.\n'


def test_install_policy_installs_the_hooks_executable_and_restores_a_lost_mode(tmp_path):
    """install-policy owns ~/.jev/hooks: the installed bytes are the repo hooks, mode 0755, and a hook that lost
    its exec bit (launchd then fails to run health-check.sh) is repaired by the next install."""
    home = tmp_path / 'home'
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('ROUTE_', 'HOOK_DISPATCH_'))}
    env.update({'HOME': str(home), 'AGENT_LB_URL': 'http://127.0.0.1:1', 'ROUTE_MODELS_CACHE': str(tmp_path / 'models.json')})
    source = ROOT / 'config/coding-agents'

    def install():
        result = subprocess.run([sys.executable, str(source / 'install-policy.py'), '--home', str(home)],
                                env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr

    install()
    hooks = home / '.jev/hooks'
    for name, template in [('route.sh', 'jev-route.sh'), ('health-check.sh', 'jev-health-check.sh'), ('jev-health.py', 'jev-health.py')]:
        assert (hooks / name).read_bytes() == (source / 'hooks' / template).read_bytes()
    for name in ('route.sh', 'health-check.sh'):
        assert (hooks / name).stat().st_mode & 0o777 == 0o755
    (hooks / 'route.sh').chmod(0o644)
    install()
    assert (hooks / 'route.sh').stat().st_mode & 0o777 == 0o755


def test_six_second_success_no_alert(setup):
    started = time.monotonic()
    assert run(setup, 'health-check.sh', DELAY='6').returncode == 0
    assert time.monotonic() - started >= 6
    assert json.loads((setup[0] / '.jev/status.json').read_text())['status'] == 'ok'
    assert not (setup[0] / '.jev/ALERT').exists()
    assert run(setup, 'route.sh').stdout == ''


def test_stale_hook_does_not_wait_for_network(setup):
    started = time.monotonic()
    result = run(setup, 'route.sh', DELAY='6')
    assert result.returncode == 0 and result.stdout == ''
    assert time.monotonic() - started < 1
    # Wait for our detached child before the temporary HOME is removed.
    deadline = time.monotonic() + 10
    while not (setup[0] / '.jev/status.json').exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert json.loads((setup[0] / '.jev/status.json').read_text())['status'] == 'ok'


def test_concurrent_probes_count_one_failure(setup):
    """The subprocess/file boundary must serialize overlapping failed refreshes."""
    _, hooks, env = setup
    env = {**env, 'FAIL': '1', 'DELAY': '4'}
    children = [subprocess.Popen(['bash', str(hooks / 'health-check.sh')], env=env)]
    lock = setup[0] / '.jev/alert-refresh.lock'
    deadline = time.monotonic() + 5
    while not Path(env['CALLS']).exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert lock.exists()
    os.utime(lock, (time.time() - 60, time.time() - 60))
    children += [subprocess.Popen(['bash', str(hooks / 'health-check.sh')], env=env) for _ in range(7)]
    assert all(child.wait(timeout=12) == 0 for child in children)
    assert json.loads((setup[0] / '.jev/alert-state.json').read_text())['failures'] == 1
    assert not (setup[0] / '.jev/ALERT').exists()


def test_real_http_slow_success(setup):
    """Real curl must accept a healthy API taking longer than the old eight-second budget.

    The network edge is a local server; no production collaborator is mocked.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get('Authorization'),
                             json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            time.sleep(9)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"answers":{"alive":{"noul":true}},"model":"fixture"}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home, hooks, env = setup
    env = {**env, 'PATH': os.environ['PATH'],
           'TYPESAFE_BASE_URL': f'http://127.0.0.1:{server.server_port}'}
    try:
        result = subprocess.run(['bash', str(hooks / 'health-check.sh')], env=env,
                                capture_output=True, text=True, timeout=45)
        assert result.returncode == 0
        assert json.loads((home / '.jev/status.json').read_text())['status'] == 'ok'
        assert requests[0][0] == '/v1/systemone'
        assert requests[0][1] == 'Bearer fixture-only'
        assert requests[0][2]['questions']['alive']['type'] == 'noul'
        assert not (home / '.jev/ALERT').exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
