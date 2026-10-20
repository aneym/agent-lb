from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

from app.core.shutdown_budget import EXCHANGE_SETTLE_S as EXCHANGE_SETTLE_S
from app.core.shutdown_budget import minimum_exit_timeout as minimum_exit_timeout


def regenerate_plist(runtime: Path, plist: Path) -> None:
    result = subprocess.run(
        ["bash", str(runtime / "scripts/install-service.sh"), "--print"],
        capture_output=True,
        check=True,
        timeout=30,
    )
    generated = plistlib.loads(result.stdout)
    if int(generated.get("ExitTimeOut", 0)) < minimum_exit_timeout(generated["EnvironmentVariables"]):
        raise RuntimeError("generated plist still has an insufficient ExitTimeOut")
    temp = plist.with_suffix(".plist.tmp")
    temp.write_bytes(result.stdout)
    temp.replace(plist)


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
