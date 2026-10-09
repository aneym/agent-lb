#!/usr/bin/env python3
"""Jev alert refresh, adopted from the hand-installed hooks on 2026-10-08.

Fix ledger: stale ALERT ignored a newer CLI ok; curl inherited sandbox proxies and
reported transport status 000 as an outage. Cache first, isolated forty-second
probe, two failures at least a minute apart, and detached prompt refresh keep network off the hot path.
"""
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HOME = Path.home() / '.jev'
STATUS = HOME / 'status.json'
STATE = HOME / 'alert-state.json'
ALERT = HOME / 'ALERT'
LOCK = HOME / 'alert-refresh.lock'
# Two failures inside one blip (a launchd run and a prompt refresh seconds apart) are one outage signal,
# not two: the second counts only this long after the first (2026-10-09).
MIN_FAILURE_SPACING_SECONDS = 60


def read(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def fresh(status):
    try:
        age = time.time() - datetime.datetime.fromisoformat(status['checkedAt'].replace('Z', '+00:00')).timestamp()
        return 0 <= age < 600
    except (KeyError, TypeError, ValueError):
        return False


def write(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


def clear():
    ALERT.unlink(missing_ok=True)
    write(STATE, {'failures': 0})


def probe():
    # Credentials stay in memory and curl stdin, never command arguments or output.
    config = {}
    try:
        for line in (HOME / '.env').read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep:
                config[key] = value.strip().strip('"\'')
    except OSError:
        return False
    config.update({k: v for k, v in os.environ.items() if k.startswith('TYPESAFE_')})
    key = config.get('TYPESAFE_API_KEY', '')
    if not key or any(c in key for c in '\r\n"\\'):
        return False
    body = json.dumps({'model': config.get('TYPESAFE_MODEL', 'jev-latest'),
                       'state': 'connectivity healthcheck',
                       'questions': {'alive': {'type': 'noul', 'instructions': 'Respond true to confirm the model is reachable'}}})
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith('_proxy')}
    try:
        result = subprocess.run(['curl', '-q', '--config', '-', '-sS', '--max-time', '40',
                                 '-w', '\n%{http_code}', '-X', 'POST',
                                 config.get('TYPESAFE_BASE_URL', 'https://api.typesafe.ai').rstrip('/') + '/v1/systemone', '-H', 'Content-Type: application/json',
                                 '--data', body], input=f'header = "Authorization: Bearer {key}"\n',
                                text=True, capture_output=True, env=env, timeout=41)
        response, _, code = result.stdout.rstrip('\n').rpartition('\n')
        data = json.loads(response)
        return result.returncode == 0 and code == '200' and isinstance(data, dict) and bool(data.get('answers'))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def refresh():
    HOME.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return
    try:
        status = read(STATUS)
        if fresh(status) and status.get('status') == 'ok':
            clear()
            return
        ok = probe()
        if ok:
            clear()
            write(STATUS, {'status': 'ok', 'checkedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'message': 'ok'})
        else:
            state = read(STATE)
            failures = state.get('failures', 0)
            failures = failures if isinstance(failures, int) and failures >= 0 else 0
            first = state.get('firstFailureAt')
            now = time.time()
            if failures == 0 or not isinstance(first, (int, float)):
                # A streak with no recorded start (state from before spacing) starts over here.
                write(STATE, {'failures': 1, 'firstFailureAt': now})
                ALERT.unlink(missing_ok=True)
            elif now - first >= MIN_FAILURE_SPACING_SECONDS:
                write(STATE, {'failures': 2, 'firstFailureAt': first})
                ALERT.write_text('down: two consecutive health probes failed\n')
            else:
                write(STATE, {'failures': failures, 'firstFailureAt': first})
    finally:
        os.close(fd)


def hook():
    status = read(STATUS)
    if fresh(status) and status.get('status') == 'ok':
        # A newer CLI success breaks the failure streak as well as suppressing
        # the old alert. Refresh takes the lock and returns without network.
        refresh()
        return
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'refresh'],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    if read(STATE).get('failures', 0) >= 2 and ALERT.exists():
        print('[jev] UNAVAILABLE: two consecutive health probes failed. Fall back to your own model for decisions.')


if __name__ == '__main__':
    refresh() if sys.argv[1:] == ['refresh'] else hook()
