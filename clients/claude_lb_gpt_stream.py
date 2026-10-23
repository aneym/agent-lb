from __future__ import annotations

import json
import signal
import subprocess
import sys


def run_gpt_stream(command: list[str]) -> int:
    child = subprocess.Popen(command, stdout=subprocess.PIPE)
    previous = {}

    def forward(signum: int, frame: object) -> None:
        if child.poll() is None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, forward)
        assert child.stdout is not None
        for line in child.stdout:
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                event = None
            if isinstance(event, dict) and event.get("type") == "rate_limit_event":
                continue
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        result = child.wait()
        return 128 - result if result < 0 else result
    finally:
        if child.stdout is not None:
            child.stdout.close()
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
