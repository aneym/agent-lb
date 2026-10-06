"""E3-r6 scenario: a Codex reviewer cannot launch a second companion from inside its own run.

Written by the spec author (specs/E3-r6.md). Copy verbatim to agent-lb
tests/integration/test_e3_r6_nested_companion.py; the implementer may not edit it.
2026-10-05, model-traffic-exempt-fix2: the read-only reviewer read the codex-verifier seat file, ran
codex-companion.mjs task itself, hit EROFS on /tmp/codex-companion and returned no verdict.
The plugin is a copy of the installed one with the repo's patches applied by the repo's own script.
Only `codex` is a fake on PATH: it answers the companion's probes and records every call with
CODEX_COMPANION_DEPTH.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLED = Path(os.environ.get('CODEX_PLUGIN_CC_DIR') or Path.home() / '.agent-lb/plugins/codex-plugin-cc')

FAKE_CODEX = '''#!/usr/bin/env python3
import json, os, sys
open(os.environ["FAKE_CODEX_CALLS"], "a").write(json.dumps(
    {"argv": sys.argv[1:], "depth": os.environ.get("CODEX_COMPANION_DEPTH")}) + "\\n")
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.159.3"); sys.exit(0)
if "--help" in sys.argv:
    sys.exit(0)
sys.exit(1)
'''


@pytest.fixture
def world():
    if not (INSTALLED / 'plugins/codex/scripts/codex-companion.mjs').exists() or not shutil.which('node'):
        pytest.fail(f'needs node and the installed plugin at {INSTALLED}')
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        plugin = tmp / 'plugin'
        shutil.copytree(INSTALLED, plugin, ignore=shutil.ignore_patterns('node_modules', '.git'), symlinks=True)
        applied = subprocess.run(['sh', str(REPO / 'scripts/apply-codex-plugin-cc-patch.sh')],
                                 env=dict(os.environ, CODEX_PLUGIN_CC_DIR=str(plugin)),
                                 capture_output=True, text=True, timeout=60)
        assert applied.returncode == 0, applied.stdout + applied.stderr
        again = subprocess.run(['sh', str(REPO / 'scripts/apply-codex-plugin-cc-patch.sh')],
                               env=dict(os.environ, CODEX_PLUGIN_CC_DIR=str(plugin)),
                               capture_output=True, text=True, timeout=60)
        assert again.returncode == 0, 'applying twice is a no-op: ' + again.stdout + again.stderr
        bin_dir, scratch, ws = tmp / 'bin', tmp / 'tmp', tmp / 'ws'
        for d in (bin_dir, scratch, ws):
            d.mkdir()
        (bin_dir / 'codex').write_text(FAKE_CODEX)
        (bin_dir / 'codex').chmod(0o755)
        subprocess.run(['git', 'init', '-q'], cwd=ws, check=True)
        prompt = tmp / 'contract.txt'
        prompt.write_text('Review the diff and end with VERDICT.\n')
        calls = tmp / 'calls.jsonl'

        def companion(**env):
            e = {k: v for k, v in os.environ.items()
                 if k not in ('CODEX_COMPANION_DEPTH', 'CODEX_COMPANION_ALLOW_NESTED', 'CLAUDE_PLUGIN_DATA')}
            e.update(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", TMPDIR=str(scratch),
                     FAKE_CODEX_CALLS=str(calls), **env)
            return subprocess.run(['node', str(plugin / 'plugins/codex/scripts/codex-companion.mjs'), 'task',
                                   '--prompt-file', str(prompt)], cwd=ws, env=e, capture_output=True, text=True,
                                  timeout=90)

        def recorded():
            return [json.loads(l) for l in calls.read_text().splitlines()] if calls.exists() else []
        yield companion, recorded, scratch


def test_nested_task_is_refused_before_any_state_or_codex_call(world):
    companion, recorded, scratch = world
    run = companion(CODEX_COMPANION_DEPTH='1')
    assert run.returncode == 3, run.stdout + run.stderr
    assert 'nested' in run.stderr and 'review the diff yourself' in run.stderr, run.stderr
    assert recorded() == [], 'no codex process may start'
    assert not (scratch / 'codex-companion').exists(), 'no job state may be written'


def test_top_level_task_marks_its_codex_children(world):
    companion, recorded, _ = world
    companion()
    servers = [c for c in recorded() if c['argv'] == ['app-server']]
    assert servers, recorded()
    assert all(c['depth'] == '1' for c in servers), servers


def test_explicit_allow_runs_nested(world):
    companion, recorded, _ = world
    companion(CODEX_COMPANION_DEPTH='1', CODEX_COMPANION_ALLOW_NESTED='1')
    servers = [c for c in recorded() if c['argv'] == ['app-server']]
    assert servers and all(c['depth'] == '2' for c in servers), recorded()
