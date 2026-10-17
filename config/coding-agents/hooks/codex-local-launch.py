#!/usr/bin/env python3
import os
import selectors
import subprocess
import sys
import time

started = time.monotonic()
focus_check = os.path.join(os.path.dirname(os.path.abspath(__file__)), "focus-steal-check.py")
focus_since = subprocess.run([sys.executable, focus_check, "now"], capture_output=True, text=True).stdout.strip()
child = subprocess.Popen(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
selector = selectors.DefaultSelector()
selector.register(child.stdout, selectors.EVENT_READ, sys.stdout.buffer)
selector.register(child.stderr, selectors.EVENT_READ, sys.stderr.buffer)
while selector.get_map():
    for key, _ in selector.select():
        data = os.read(key.fd, 65536)
        if data:
            key.data.write(data)
            key.data.flush()
        else:
            selector.unregister(key.fileobj)
rc = child.wait()
if time.monotonic() - started < 10:
    print(f"codex-local: launch exited within 10 seconds (status {rc}); not running", file=sys.stderr)
    sys.exit(rc if rc > 0 else 1)
rc = rc if rc >= 0 else 128 - rc
stolen = subprocess.run([sys.executable, focus_check, "--since", focus_since, "codex-local"]).returncode
sys.exit(rc or (stolen if stolen == 6 else 0))
