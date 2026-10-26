#!/usr/bin/env python3
import json
import os
import re
import selectors
import subprocess
import sys
import time
from pathlib import Path

started = time.monotonic()
review = os.environ.get("CODEX_LOCAL_REVIEW_CONTRACT")
focus_check = os.path.join(os.path.dirname(os.path.abspath(__file__)), "focus-steal-check.py")
focus_since = subprocess.run([sys.executable, focus_check, "now"], capture_output=True, text=True).stdout.strip()
child = subprocess.Popen(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
selector = selectors.DefaultSelector()
selector.register(child.stdout, selectors.EVENT_READ, sys.stdout.buffer)
selector.register(child.stderr, selectors.EVENT_READ, sys.stderr.buffer)
output = bytearray()
while selector.get_map():
    for key, _ in selector.select():
        data = os.read(key.fd, 65536)
        if data:
            if review and key.fileobj is child.stdout:
                output.extend(data)
            else:
                key.data.write(data)
                key.data.flush()
        else:
            selector.unregister(key.fileobj)
rc = child.wait()
rc = rc if rc >= 0 else 128 - rc
if review and not rc:
    try:
        payload = json.loads(output)
        text = payload["rawOutput"].strip()
        thread = payload["threadId"]
        if payload["status"] != 0 or not isinstance(thread, str) or not thread:
            raise ValueError("missing successful Codex thread")
        lines = text.splitlines()
        if len(lines) < 2 or not lines[-2].startswith("REVIEW-JSON "):
            raise ValueError("missing REVIEW-JSON")
        verdict = json.loads(lines[-2].removeprefix("REVIEW-JSON "))
        match = re.fullmatch(r"VERDICT: (PASS|FAIL)(?: lens=\S+)? diff=(\S+)", lines[-1])
        if not match or verdict.get("verdict", "").upper() != match[1] or verdict.get("diff") != match[2]:
            raise ValueError("inconsistent review verdict")
        records = list((Path(review) / "companion" / "state").glob("*/jobs/task-*.json"))
        jobs = [json.loads(path.read_text()) for path in records]
        jobs = [job for job in jobs if job.get("threadId") == thread and job.get("result") == payload]
        if len(jobs) != 1:
            raise ValueError("no unique matching companion receipt")
        job = jobs[0]
        if job.get("status") != "completed" or not re.fullmatch(r"task-[a-z0-9]+-[a-z0-9]+", job.get("id", "")):
            raise ValueError("invalid companion job id or state")
        print(f"job={job['id']} thread={thread}")
        print(text, flush=True)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"codex-local: review result rejected: {exc}", file=sys.stderr)
        rc = 1
elif review:
    print("codex-local: review failed; no verified verdict", file=sys.stderr)
if not review and time.monotonic() - started < 10:
    print(f"codex-local: launch exited within 10 seconds (status {rc}); not running", file=sys.stderr)
    sys.exit(rc or 1)
stolen = subprocess.run([sys.executable, focus_check, "--since", focus_since, "codex-local"]).returncode
sys.exit(rc or (stolen if stolen == 6 else 0))
