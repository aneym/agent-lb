from __future__ import annotations

import os
import plistlib
import stat
import tempfile
from pathlib import Path

from app.core.shutdown_budget import EXCHANGE_SETTLE_S as EXCHANGE_SETTLE_S
from app.core.shutdown_budget import minimum_exit_timeout as minimum_exit_timeout


def regenerate_plist(plist: Path) -> int:
    existing = plistlib.loads(plist.read_bytes())
    if not isinstance(existing, dict):
        raise RuntimeError("existing plist has no usable ProgramArguments")
    arguments = existing.get("ProgramArguments")
    if not isinstance(arguments, list) or not arguments or not all(isinstance(arg, str) for arg in arguments):
        raise RuntimeError("existing plist has no usable ProgramArguments")
    raw_env = existing.get("EnvironmentVariables", {})
    if not isinstance(raw_env, dict):
        raise RuntimeError("existing plist has unusable EnvironmentVariables")
    env = {str(k): str(v) for k, v in raw_env.items()}
    required = minimum_exit_timeout(env)
    updated = dict(existing)
    updated["ExitTimeOut"] = max(int(existing.get("ExitTimeOut", 0)), required)
    if updated["ProgramArguments"] != arguments:
        raise RuntimeError("plist repair changed ProgramArguments")
    if {k: v for k, v in updated.items() if k != "ExitTimeOut"} != {
        k: v for k, v in existing.items() if k != "ExitTimeOut"
    }:
        raise RuntimeError("plist repair changed unrelated fields")
    if updated["ExitTimeOut"] < required:
        raise RuntimeError("plist repair has an insufficient ExitTimeOut")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=plist.parent, prefix=plist.name + ".", suffix=".tmp", delete=False) as f:
            temp = Path(f.name)
            f.write(plistlib.dumps(updated, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, stat.S_IMODE(plist.stat().st_mode))
        os.replace(temp, plist)
    except Exception:
        if temp is not None:
            temp.unlink(missing_ok=True)
        raise
    if plistlib.loads(plist.read_bytes()).get("ProgramArguments") != arguments:
        raise RuntimeError("plist repair read-back changed ProgramArguments")
    return updated["ExitTimeOut"]


def parse_reaper_args(argv: list[str]) -> tuple[int, float, Path | None]:
    if len(argv) not in (3, 5) or argv[0] != "--reap":
        raise ValueError(f"reaper takes --reap PID SECONDS [--sandbox CONFIG], got {len(argv)} word(s)")
    if not argv[1].isascii() or not argv[1].isdigit() or int(argv[1]) <= 1:
        raise ValueError("reaper pid must be a positive integer above 1")
    try:
        deadline = float(argv[2])
    except ValueError as exc:
        raise ValueError("reaper seconds must be a number") from exc
    if not 0 <= deadline <= 86400:
        raise ValueError("reaper seconds out of range")
    config = None
    if len(argv) == 5:
        if argv[3] != "--sandbox" or not argv[4]:
            raise ValueError(f"reaper option {argv[3]!r} is not --sandbox CONFIG")
        config = Path(argv[4])
    return int(argv[1]), deadline, config
