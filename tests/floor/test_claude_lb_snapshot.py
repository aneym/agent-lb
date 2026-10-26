from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLIENTS = Path(__file__).resolve().parents[2] / "clients"


class ProcessSnapshotLifecycleTests(unittest.TestCase):
    def test_waiting_shims_survive_directory_removal(self):
        script = r"""
import fcntl
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from claude_lb_snapshot import bounded_process_snapshots

with bounded_process_snapshots():
    root = Path(os.environ["CLAUDE_LB_PS_CACHE"])
    lock = (root / "snapshot.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    children = [subprocess.Popen([str(root / "ps"), "-A", "-o", "pid=", "-o", "ppid="],
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    time.sleep(0.25)
    shutil.rmtree(root)
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    for child in children:
        output, error = child.communicate(timeout=5)
        assert child.returncode == 0, (child.returncode, error)
        assert "Traceback" not in error, error
"""
        self.run_script(script)

    def test_cleanup_preserves_exit_status_during_overlapping_shims(self):
        script = r'''
import os
import subprocess
import sys
import time
from pathlib import Path
from claude_lb_snapshot import bounded_process_snapshots

worker = r"""
import os
import subprocess
import sys
import time
from pathlib import Path
root, shim = map(Path, sys.argv[1:])
deadline = time.monotonic() + 4
while (root / 'ps').exists() and time.monotonic() < deadline:
    time.sleep(0.001)
for _ in range(12):
    result = subprocess.run([str(shim), '-A', '-o', 'pid=', '-o', 'ppid='],
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
"""
children = []
try:
    with bounded_process_snapshots():
        root = Path(os.environ['CLAUDE_LB_PS_CACHE'])
        for index in range(6000):
            (root / f'padding-{index}').touch()
        children = [subprocess.Popen([sys.executable, '-c', worker, str(root), sys.argv[1]],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
        time.sleep(0.25)
        print('child result preserved', flush=True)
        raise SystemExit(23)
finally:
    for child in children:
        output, error = child.communicate(timeout=12)
        assert child.returncode == 0, (child.returncode, error)
'''
        with tempfile.TemporaryDirectory() as directory:
            shim = Path(directory) / "ps"
            shim.symlink_to(CLIENTS / "claude_lb_snapshot.py")
            self.run_script(script, [str(shim)], expected=23, output="child result preserved\n")

    def run_script(self, script, args=(), expected=0, output=""):
        result = subprocess.run(
            [sys.executable, "-c", script, *args],
            env={**os.environ, "PYTHONPATH": str(CLIENTS)},
            capture_output=True,
            text=True,
            timeout=25,
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        self.assertEqual(result.stdout, output)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
