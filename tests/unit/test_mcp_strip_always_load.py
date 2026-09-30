"""Transport contract for the deferred-tool MCP proxy."""

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

PROXY = Path(__file__).resolve().parents[2] / "config/coding-agents/bin/mcp-strip-always-load"
strip_always_load = runpy.run_path(str(PROXY))["strip_always_load"]


@pytest.mark.parametrize(
    "line",
    [
        b' { "jsonrpc": "2.0", "result": {"ok": true} }\r\n',
        b'not JSON\xff\n',
        b'[1, 2]\n',
        b'{"result": null}\n',
        b'{"result": {"tools": {}}}\n',
    ],
)
def test_other_lines_pass_through_byte_for_byte(line):
    assert strip_always_load(line) == line


def test_tools_metadata_is_selectively_removed():
    response = {
        "result": {
            "tools": [
                {"name": "café", "_meta": {"anthropic/alwaysLoad": True, "anthropic/searchHint": "x"}},
                {"name": "second", "_meta": {"anthropic/alwaysLoad": False}},
                {"name": "third", "_meta": {"other": True}},
                {"name": "fourth"},
                {"name": "fifth", "_meta": None},
            ]
        }
    }
    output = strip_always_load(json.dumps(response).encode() + b"\n")
    assert json.loads(output)["result"]["tools"] == [
        {"name": "café", "_meta": {"anthropic/searchHint": "x"}},
        {"name": "second"},
        {"name": "third", "_meta": {"other": True}},
        {"name": "fourth"},
        {"name": "fifth", "_meta": None},
    ]
    assert b"caf\xc3\xa9" in output
    assert output.endswith(b"\n")
    assert b'": ' not in output


def test_stdio_proxy_relays_child_and_returns_its_exit_code():
    child = r'''
import json
import sys
request = sys.stdin.buffer.readline()
assert json.loads(request)["method"] == "tools/list"
sys.stdout.buffer.write(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": [
    {"name": "first", "_meta": {"anthropic/alwaysLoad": True, "anthropic/searchHint": "x"}},
    {"name": "second", "_meta": {"anthropic/alwaysLoad": True}}
]}}).encode() + b"\n")
sys.stdout.buffer.flush()
# Echo a non-tools response verbatim to prove input and output byte preservation.
other = sys.stdin.buffer.readline()
sys.stdout.buffer.write(other)
sys.stdout.buffer.write(b"not JSON\xff\r\n")
sys.stdout.buffer.flush()
sys.stderr.buffer.write(b"child stderr\n")
assert sys.stdin.buffer.read() == b""
sys.exit(7)
'''
    request = b' { "jsonrpc": "2.0", "id": 1, "method": "tools/list" }\n'
    other = b' { "id": 2, "result": { "ok": true } }\r\n'
    result = subprocess.run(
        [str(PROXY), "--", sys.executable, "-c", child],
        input=request + other,
        capture_output=True,
        timeout=10,
    )
    tools_line, remainder = result.stdout.split(b"\n", 1)
    assert json.loads(tools_line)["result"]["tools"] == [
        {"name": "first", "_meta": {"anthropic/searchHint": "x"}},
        {"name": "second"},
    ]
    assert remainder == other + b"not JSON\xff\r\n"
    assert result.stderr == b"child stderr\n"
    assert result.returncode == 7
