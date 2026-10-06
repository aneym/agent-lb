"""G3: a write-mode Codex seat in a linked git worktree gets that worktree's git dirs as writable roots,
on top of the writable roots the user already configured.

2026-10-06, PR 5053: codex-sol ran `git fetch origin` in a worktree with sandbox workspace-write and hit
"FETCH_HEAD: Operation not permitted"; FETCH_HEAD, refs and objects live under <main>/.git, outside the
workspace. The first fix sent the git dirs as a `config` override, and Codex replaces arrays on override,
so a user's own `sandbox_workspace_write.writable_roots` were dropped (codex-verifier FAIL).

The plugin is a copy of the installed one with the repo's patches applied by the repo's own script. `codex`
is the real Codex app-server with a temp CODEX_HOME whose config.toml carries the user's roots; a recorder
tees its JSON-RPC both ways, and a local stub answers model calls, so nothing leaves the host. The assertions
read the sandbox the app-server itself reports for thread/start and thread/resume.
Sandbox enforcement (fetch, commit and a write in the configured root) is the scenario in results/G3-report.md.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLED = Path(os.environ.get('CODEX_PLUGIN_CC_DIR') or Path.home() / '.agent-lb/plugins/codex-plugin-cc')

RECORDER = '''#!/bin/sh
if [ "$*" = app-server ]; then
  tee -a "$RPC_IN" | "$REAL_CODEX" "$@" | tee -a "$RPC_OUT"
else
  exec "$REAL_CODEX" "$@"
fi
'''


class ModelStub(BaseHTTPRequestHandler):
    """Answers every Responses call with one finished assistant message."""

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        usage = {'input_tokens': 1, 'input_tokens_details': None, 'output_tokens': 1,
                 'output_tokens_details': None, 'total_tokens': 2}
        events = [
            {'type': 'response.created', 'response': {'id': 'r1'}},
            {'type': 'response.output_item.done', 'item': {
                'type': 'message', 'role': 'assistant', 'id': 'm1',
                'content': [{'type': 'output_text', 'text': 'done'}]}},
            {'type': 'response.completed', 'response': {'id': 'r1', 'usage': usage}},
        ]
        body = ''.join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


def git(cwd, *args):
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def world():
    real_codex = shutil.which('codex')
    if not (INSTALLED / 'plugins/codex/scripts/codex-companion.mjs').exists() or not shutil.which('node') \
            or not real_codex:
        pytest.fail(f'needs node, codex and the installed plugin at {INSTALLED}')
    stub = ThreadingHTTPServer(('127.0.0.1', 0), ModelStub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t).resolve()
        plugin = tmp / 'plugin'
        shutil.copytree(INSTALLED, plugin, ignore=shutil.ignore_patterns('node_modules', '.git'), symlinks=True)
        # The installed plugin may already carry this repo's patches. Strip them so the repo's patch files
        # alone must produce the behavior; a deployed G3 change left behind with no patch file fails here.
        for patch in sorted((REPO / 'patches/codex-plugin-cc').glob('*.patch'), reverse=True):
            if subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)], cwd=plugin,
                              capture_output=True, timeout=30).returncode == 0:
                subprocess.run(['git', 'apply', '--reverse', str(patch)], cwd=plugin, check=True,
                               capture_output=True, timeout=30)
        assert 'worktreeWritableRoots' not in (plugin / 'plugins/codex/scripts/lib/codex.mjs').read_text(), \
            'the installed plugin carries the worktree-roots change but no repo patch removes it'
        applied = subprocess.run(['sh', str(REPO / 'scripts/apply-codex-plugin-cc-patch.sh')],
                                 env=dict(os.environ, CODEX_PLUGIN_CC_DIR=str(plugin)),
                                 capture_output=True, text=True, timeout=60)
        assert applied.returncode == 0, applied.stdout + applied.stderr
        bin_dir = tmp / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'codex').write_text(RECORDER)
        (bin_dir / 'codex').chmod(0o755)
        codex_home = tmp / 'codex-home'
        codex_home.mkdir()
        main = tmp / 'main'
        main.mkdir()
        git(main, 'init', '-q', '-b', 'main')
        git(main, 'commit', '-q', '--allow-empty', '-m', 'init')
        git(main, 'worktree', 'add', '-q', str(tmp / 'wt'), '-b', 'topic')
        (tmp / 'plain').mkdir()
        (tmp / 'build-cache').mkdir()
        rpc_in, rpc_out = tmp / 'rpc-in.jsonl', tmp / 'rpc-out.jsonl'
        env = {k: v for k, v in os.environ.items() if not k.startswith('CODEX_COMPANION')}
        env.update(PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}', REAL_CODEX=real_codex, RPC_IN=str(rpc_in),
                   RPC_OUT=str(rpc_out), CODEX_HOME=str(codex_home), CLAUDE_PLUGIN_DATA=str(tmp / 'data'))

        def configure(writable_roots):
            config = (f'model = "stub"\nmodel_provider = "stub"\n'
                      f'[model_providers.stub]\nname = "stub"\n'
                      f'base_url = "http://127.0.0.1:{stub.server_address[1]}/v1"\n'
                      f'wire_api = "responses"\nsupports_websockets = false\n'
                      f'request_max_retries = 0\nstream_max_retries = 0\n')
            if writable_roots is not None:
                config += f'[sandbox_workspace_write]\nwritable_roots = {json.dumps(writable_roots)}\n'
            (codex_home / 'config.toml').write_text(config)

        def task(cwd, *flags):
            """Run one companion task; return {method: sandbox the app-server reported} for start/resume."""
            rpc_in.write_text('')
            rpc_out.write_text('')
            run = subprocess.run(['node', str(plugin / 'plugins/codex/scripts/codex-companion.mjs'), 'task', *flags,
                                  'run git fetch origin'], cwd=cwd, env=env, capture_output=True, text=True,
                                 timeout=60)
            assert run.returncode == 0, run.stdout + run.stderr
            asked = {m['id']: m['method'] for m in map(json.loads, rpc_in.read_text().splitlines())
                     if m.get('method') in ('thread/start', 'thread/resume')}
            return {asked[m['id']]: m['result']['sandbox'] for m in map(json.loads, rpc_out.read_text().splitlines())
                    if m.get('id') in asked and 'result' in m}

        configure(None)
        yield {'task': task, 'configure': configure, 'main': main, 'wt': tmp / 'wt', 'plain': tmp / 'plain',
               'cache': tmp / 'build-cache'}
    stub.shutdown()


@pytest.mark.parametrize('configured', [False, True], ids=['no-configured-roots', 'configured-roots'])
def test_write_task_in_worktree_adds_git_dirs_to_configured_roots(world, configured):
    user_roots = [str(world['cache'])] if configured else []
    world['configure'](user_roots if configured else None)
    common = (world['main'] / '.git').resolve()
    want = [*user_roots, str(common / 'worktrees/wt'), str(common)]
    started = world['task'](world['wt'], '--write')['thread/start']
    assert started['type'] == 'workspaceWrite'
    assert started['writableRoots'] == want
    resumed = world['task'](world['wt'], '--write', '--resume-last')['thread/resume']
    assert resumed['writableRoots'] == want


@pytest.mark.parametrize('where', ['main', 'plain'], ids=['normal-checkout', 'not-a-repo'])
def test_write_task_outside_a_worktree_keeps_configured_roots_only(world, where):
    world['configure']([str(world['cache'])])
    started = world['task'](world[where], '--write')['thread/start']
    assert started['writableRoots'] == [str(world['cache'])]


def test_read_only_task_in_worktree_stays_read_only(world):
    world['configure']([str(world['cache'])])
    assert world['task'](world['wt'])['thread/start']['type'] == 'readOnly'
