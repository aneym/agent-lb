
import fcntl
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


DISPATCH_LINE = re.compile(r'(?im)^[\s>*`-]*Dispatch:\s*`?(dcn_[0-9A-HJKMNP-TV-Z]{26})\b')


def decision_id(agent):
    value = agent.get('decision_id')
    if isinstance(value, str) and value.startswith('dcn_'):
        return value
    for key in ('prompt', 'result'):
        found = DISPATCH_LINE.search(agent.get(key) or '') if isinstance(agent.get(key), str) else None
        if found:
            return found.group(1)
    return None


def agent_rows(response):
    agents = response.get('agents') or []
    rows = []
    for agent in agents if isinstance(agents, list) else []:
        if not isinstance(agent, dict):
            continue
        rows.append({'label': agent.get('label') if isinstance(agent.get('label'), str) else None,
                     'agent_type': agent.get('agentType'),
                     'decision_id': decision_id(agent)})
    return rows


def main():
    payload = json.load(sys.stdin)
    if payload.get('tool_name') != 'Workflow':
        return
    response = payload.get('tool_response') or {}
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            response = {}
    response = response if isinstance(response, dict) else {}
    usage = response.get('usage') or {}
    usage = usage if isinstance(usage, dict) else {}
    state = Path(os.environ.get('FACTORY_HOME', str(Path.home() / '.open-factory/state')))
    capture = Path(os.environ.get('FACTORY_UNIFIED_UTILIZATION', str(state / 'unified-utilization.json')))
    workflow_id = response.get('workflow_id') or response.get('id')
    quota = number(response.get('quota_percent', usage.get('quota_percent')))
    if quota is None and capture.is_file():
        try:
            captured = json.loads(capture.read_text())
        except (OSError, ValueError):
            captured = {}
        if workflow_id and captured.get('workflow_id') == workflow_id:
            quota = number(captured.get('quota_percent'))
    if quota is not None and quota > 100:
        quota = None
    record = {'at': datetime.now(timezone.utc).isoformat(),
              'workflow_id': workflow_id,
              'quota_percent': quota,
              'tokens_in': number(usage.get('input_tokens', usage.get('tokens_in'))),
              'tokens_out': number(usage.get('output_tokens', usage.get('tokens_out'))),
              'usage_source': 'unified-utilization' if quota is not None else 'tokens',
              'agents': agent_rows(response)}
    if quota is None and record['tokens_in'] is None and record['tokens_out'] is None:
        record['usage_source'] = 'unavailable'
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'workflow-readback.jsonl').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(record) + '\n')


if __name__ == '__main__':
    main()
