#!/usr/bin/env python3
"""Integration fixture: exercise the real dispatcher subprocess and JSON boundary.

Synthetic command hooks are protocol fixtures, not mocks of our own guards.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dispatcher', type=Path, default=Path(__file__).with_name('hook-dispatch.py'))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='seat-run-wake-parity-') as directory:
        root = Path(directory)
        registry = root / 'registry.json'
        cases = 0
        for reply in ({}, {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                       'updatedInput': {'command': 'seat-run --bg rewritten -- true', 'timeout': 5000}}},
                      {'hookSpecificOutput': {'permissionDecision': 'deny', 'permissionDecisionReason': 'fixture'}}):
            hook = root / 'reply.sh'
            hook.write_text("#!/bin/sh\nprintf '%s\\n' '" + json.dumps(reply) + "'\n")
            registry.write_text(json.dumps({'entries': {'PreToolUse': {'Bash': [
                {'type': 'command', 'command': 'sh ' + str(hook)}]}}}))
            for child, command in ((False, 'seat-run --bg fixture -- true'),
                                   (True, 'seat-run --bg fixture -- true'),
                                   (True, 'seat-run --owner X --bg fixture -- true'),
                                   (True, 'export FACTORY_OWNER_PANE=none; seat-run --bg fixture -- true')):
                payload = {'tool_name': 'Bash', 'tool_input': {'command': command, 'timeout': 1000}}
                if child:
                    payload['agent_id'] = 'test-owned-agent'
                env = dict(os.environ, HOOK_DISPATCH_REGISTRY=str(registry))
                proc = subprocess.run(['python3', str(args.dispatcher), 'PreToolUse', 'Bash'],
                                      input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=15)
                assert proc.returncode == 0, proc.stderr
                answer = json.loads(proc.stdout)
                specific = answer.get('hookSpecificOutput', {})
                denied = reply.get('hookSpecificOutput', {}).get('permissionDecision') == 'deny'
                if not child or denied:
                    assert answer == reply, (payload, answer, reply)
                else:
                    expected = reply.get('hookSpecificOutput', {}).get('updatedInput', payload['tool_input'])
                    prefix = 'export FACTORY_OWNER_PANE=none;'
                    expected = dict(expected, command=(expected['command'] if expected['command'].startswith(prefix)
                                                       else prefix + ' ' + expected['command']))
                    assert specific['updatedInput'] == expected, (payload, answer, expected)
                cases += 1
        print(f'PASS: {cases} dispatcher subprocess cases; main loop, subagent, explicit owner, idempotence, rewrite composition, denial')
    print('PASS: test-owned fixture directory cleaned up')


if __name__ == '__main__':
    main()
