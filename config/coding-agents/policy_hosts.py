import json
import os
from pathlib import Path
from typing import Any


def policy_box_hosts(home: Path) -> list[str]:
    registry = Path(os.environ.get("FACTORY_HOME") or home / ".agent-rails/factory").expanduser()

    def load(name: str, override: str | None = None) -> Any:
        path = Path(override).expanduser() if override else registry / name
        if not path.exists() and not override:
            return []
        return json.loads(path.read_text())

    machines = load("machines.json", os.environ.get("FACTORY_MACHINES"))
    boxes = load("boxes.json", os.environ.get("FLEET_BOXES_FILE"))
    boxes = boxes.get("boxes", []) if isinstance(boxes, dict) else boxes
    disabled = {
        row.get("name") for row in [*machines, *boxes] if row.get("enabled") is False or row.get("kind") == "slot"
    }
    game_state = Path(os.environ.get("GAME_MODE_STATE") or registry / "state/game-mode.json").expanduser()
    try:
        gaming = json.loads(game_state.read_text()).get("mode") not in ("off", "normal")
    except FileNotFoundError:
        gaming = False
    except (OSError, ValueError, AttributeError):
        gaming = True
    if gaming:
        disabled.update(("pc-wsl", "pc", "pc-windows"))
    known = {name for row in machines for name in [row["name"], *row.get("aliases", [])]}
    hosts = [
        row["name"]
        for row in machines
        if row.get("job_host") is True
        and not str(row.get("os", "")).lower().startswith("macos")
        and row["name"] not in disabled
        and row.get("ssh", row["name"]) not in disabled
    ]
    hosts.extend(
        row["name"]
        for row in boxes
        if row.get("kind") == "plain"
        and row.get("enabled") is True
        and row["name"] not in known
        and row["name"] not in disabled
    )
    return list(dict.fromkeys(hosts))
