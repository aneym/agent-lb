import os
import shutil
import subprocess
import sys

MINIMUM = (3, 11)
NAMES = ["python3.%d" % minor for minor in range(14, MINIMUM[1] - 1, -1)] + ["python3"]
FIXED = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
UV = ["~/.local/bin/uv", "/opt/homebrew/bin/uv", "~/.cargo/bin/uv", "/usr/local/bin/uv"]


def recent(path):
    try:
        probe = subprocess.run(
            [path, "-c", "import sys; sys.exit(sys.version_info < %r)" % (MINIMUM,)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def uv_python():
    for uv in [shutil.which("uv")] + [os.path.expanduser(path) for path in UV]:
        if not uv or not os.access(uv, os.X_OK):
            continue
        try:
            found = subprocess.run(
                [uv, "python", "find", "--no-project", "--system", ">=%d.%d" % MINIMUM],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        path = found.stdout.decode("utf-8", "replace").strip()
        if found.returncode == 0 and path:
            return path
    return None


def policy_python():
    if sys.version_info >= MINIMUM:
        return sys.executable
    for path in [uv_python()] + [shutil.which(name) for name in NAMES] + FIXED:
        if path and recent(path):
            return path
    return None


def missing_detail():
    have = "%d.%d" % tuple(sys.version_info[:2])
    need = "%d.%d" % MINIMUM
    return (
        f"install-policy needs Python {need} or newer; {sys.executable} is {have} and no newer python3 "
        "or uv-managed Python was found. Run `uv python install 3.12`."
    )
