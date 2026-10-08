"""Exercise the shell hook through its real JSON stdin/stdout boundary."""
import json
from pathlib import Path
import subprocess

import pytest

HOOK = Path(__file__).resolve().parents[1] / 'config/coding-agents/hooks/plutil-guard.sh'


@pytest.mark.parametrize('wrapper', ['', 'sudo -n ', 'env -i ', 'nice -n 5 ', 'command -p ',
                                    'exec -a plist ', 'time -p ', 'nohup ',
                                    'env -i sudo -n nice -n 5 ', 'timeout 5 ',
                                    'timeout -k 2 5 ', 'xargs ', 'xargs -n 1 '])
def test_transform_without_output_denied(wrapper):
    result = subprocess.run(['bash', str(HOOK)], input=json.dumps({'tool_name': 'Bash',
        'tool_input': {'command': wrapper + 'plutil -extract key raw file.plist'}}),
        capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'


@pytest.mark.parametrize('shell', ['bash', 'sh', '/bin/bash', '/bin/sh', 'timeout 5 bash', 'xargs sh'])
def test_shell_transform_without_output_denied(shell):
    result = subprocess.run(['bash', str(HOOK)], input=json.dumps({'tool_name': 'Bash',
        'tool_input': {'command': shell + " -c 'plutil -extract key raw file.plist'"}}),
        capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'


@pytest.mark.parametrize('command', ['plutil -p file.plist', 'plutil -lint file.plist',
                                    'plutil -extract key raw -o - file.plist',
                                    'sudo -n plutil -extract key raw -o out.txt file.plist',
                                    'timeout 5 plutil -extract key raw -o - file.plist',
                                    'xargs plutil -extract key raw -o out.txt file.plist',
                                    "bash -c 'plutil -extract key raw -o - file.plist'",
                                    "sh -c 'plutil -lint file.plist'"])
def test_read_only_or_explicit_output_allowed(command):
    result = subprocess.run(['bash', str(HOOK)], input=json.dumps({'tool_name': 'Bash',
        'tool_input': {'command': command}}), capture_output=True, text=True, check=True)
    assert result.stdout == ''
