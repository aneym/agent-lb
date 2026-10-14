"""Workflow completion writes measured usage, never derives quota from tokens.

The real hook and append-only file boundary protect missing completion records
and accidental prompt logging; no existing test owns Workflow completion usage.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_workflow_completion_uses_capture_then_tokens(tmp_path):
    hook = ROOT / 'config/coding-agents/hooks/workflow-readback.py'
    capture = tmp_path / 'unified-utilization.json'
    capture.write_text('{"workflow_id":"wf-capture","quota_percent": 2.5}')
    env = dict(os.environ, FACTORY_HOME=str(tmp_path))
    for ident, usage in [('wf-capture', {'input_tokens': 12, 'output_tokens': 7}),
                         ('wf-tokens', {'input_tokens': 30, 'output_tokens': 8})]:
        subprocess.run([sys.executable, str(hook)], input=json.dumps({
            'tool_name': 'Workflow', 'tool_input': {'script': 'PRIVATE PROMPT'},
            'tool_response': {'id': ident, 'usage': usage}}),
            env=env, text=True, capture_output=True, check=True, timeout=5)
        capture.write_text('{"workflow_id":"another-run","quota_percent": 99}')
    log = tmp_path / 'workflow-readback.jsonl'
    first, second = map(json.loads, log.read_text().splitlines())
    assert (first['workflow_id'], first['quota_percent'], first['usage_source']) == (
        'wf-capture', 2.5, 'unified-utilization')
    assert (second['quota_percent'], second['tokens_in'], second['tokens_out'], second['usage_source']) == (
        None, 30, 8, 'tokens')
    assert 'PRIVATE PROMPT' not in log.read_text()
