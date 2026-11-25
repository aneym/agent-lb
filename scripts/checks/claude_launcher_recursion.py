import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

LAUNCHER = Path(__file__).resolve().parents[2] / "clients/claude-lb-launch"


def main():
    with TemporaryDirectory() as temp:
        home = Path(temp)
        native_bin = home / ".local/bin"
        native_bin.mkdir(parents=True)
        native = native_bin / "claude"
        native.symlink_to("/bin/echo")
        shadow_bin = home / "review-bin"
        shadow_bin.mkdir()
        shadow = shadow_bin / "claude"
        shadow.write_text(
            "#!/bin/sh\n"
            '[ "${CHECK_WRAPPER_DEPTH:-0}" -lt 3 ] || exit 97\n'
            "export CHECK_WRAPPER_DEPTH=$((${CHECK_WRAPPER_DEPTH:-0} + 1))\n"
            'exec "$CHECK_LAUNCHER" "$@"\n'
        )
        shadow.chmod(0o700)
        env = {
            "HOME": temp,
            "PATH": os.pathsep.join((str(shadow_bin), str(native_bin), os.environ["PATH"])),
            "CLAUDE_LB_DISABLE": "1",
            "CHECK_LAUNCHER": str(LAUNCHER),
        }
        command = [str(shadow), "-p", "--model", "claude-fable-5-2"]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10, check=True)
        assert "-p --model claude-fable-5-2" in result.stdout, result.stdout

        result = subprocess.run(
            [str(LAUNCHER), "-p"],
            env={**env, "FACTORY_CLAUDE_BIN": str(LAUNCHER)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode and "refusing to start itself" in result.stderr, result

        native.unlink()
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode and "recursion limit reached" in result.stderr, result
    print("Claude launcher selected the native binary and rejected recursive launches")


if __name__ == "__main__":
    main()
