import subprocess
import sys
import unittest
from pathlib import Path


LAUNCHER = Path(__file__).resolve().parents[1] / 'config/coding-agents/hooks/codex-local-launch.py'


class LaunchTests(unittest.TestCase):
    def test_refusal_is_streamed_and_fails(self):
        result = subprocess.run(
            [sys.executable, str(LAUNCHER), sys.executable, '-c',
             'import sys; print("companion: repo write refused", file=sys.stderr); sys.exit(2)'],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 2
        assert 'companion: repo write refused' in result.stderr
        assert 'not running' in result.stderr


    def test_early_success_is_not_reported_as_running(self):
        result = subprocess.run(
            [sys.executable, str(LAUNCHER), sys.executable, '-c', 'print("finished")'],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 1
        assert result.stdout == 'finished\n'
        assert 'exited within 10 seconds (status 0)' in result.stderr

if __name__ == "__main__":
    unittest.main()
