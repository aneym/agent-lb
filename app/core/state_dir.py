from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    return Path(os.getenv("AGENT_LB_STATE_DIR", str(Path.home() / ".agent-lb/state")))
