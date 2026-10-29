from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hook_retired_invocation import retired_invocation  # type: ignore[missing-import]
from policy_layout import POLICY_DIR, RETIRED_HOOKS  # type: ignore[missing-import]

SHIPPED_SHA256 = {
    "claude-bash-hands-nudge.py": frozenset(
        {
            "90125c043fca6583aad5ea3f783b335587c66192843cbf0ec8bb6ed99d619a31",
        }
    ),
    "claude-grind-pulse.py": frozenset(
        {
            "068ff863ee92a3d7fddd9a2adcb2d94802c53f00a575cf3e15b90125b8649f9d",
            "316e96efcf63610db5b5b1ac2596112e0cfd24c7df97d762a7765b388fed7821",
            "7ec07162aceef71dd046fd4e95d371368b8f61d8cf7cd07d71aff9ef8e3a9785",
        }
    ),
    "poll-loop-warn.py": frozenset(
        {
            "ebd76d0fce81c42a7e6173285aadc5cc966a5c065012adaa1355de7a109d51ee",
        }
    ),
    "workflow-relay-guard.py": frozenset(
        {
            "07125e6204ff1997dbad632ee8e9ada42fa73fdb6713b7b30b72b72a170f2d9b",
            "9a972e6290365f04d2c82cf3f1a7b414e3f046e3cd4a6f62c3a568604a012b38",
            "b7138cedf0724c479c7f5afbab019925c089e9f4c2e8549c37a32e134e44ddc7",
            "bc8fcc3b503152658a7dd72f123de12cad376fca2961abe91a0ce82585cb5c76",
            "ccb81e1b29f379344811a455899ea35e5969ccf85dc708d3b092f2245dbb6d33",
        }
    ),
    "seat_guard_advisory.py": frozenset(
        {
            "e648b12d91c2ed8a4cc9fb40066fbe0c76ffe310147547415b3a5b719bd9adef",
        }
    ),
    "seat_hands.py": frozenset(
        {
            "249363dfff8438b70a2823c841675fc8b1211a7cb94587f75cfcf3674f168933",
            "660a6d080c23058c139264fc507324065ef34ceeef8df87fd9f76ee43a61f31b",
            "71c0bd698855401c583aaaec059b279dce9352d7696dac12ca792e07eb3fd658",
            "7fb29f5941a4c5ee93b7f04329819540859ee8720ce43992e9212136b06bbc92",
            "a106a3e170926f318fc2d39a46176c359352cf7338304f69cd87067f4d7900a6",
            "ad40ce7d56e1cac5128fabdadab46a5e024cec0e53546bf8c107733cacaceba7",
            "c1ecb0149125b031446de16d32b01ba3718c78c13fdaefea95a4e3f00a3ea2ce",
            "ff11d111c2baf3c341fad47ff1d624d5bdecfce2dccbb0c0154374a479ea872a",
        }
    ),
    "workflow-seat-guard.py": frozenset(
        {
            "38eb4d89ff3328aa4dfe355a107de41f22ffde9cbe83066651972b8471eae5da",
            "3e157cf983df1ce538fc65da2100efadf5648fa32a9bdcf4964e20b72bf268c5",
            "4c15ce9d85e84faa5b3b56d38ae66308554a30debff6997ef3b66884739ba393",
            "584a3a1c736a8e7856d17e7d779012f88b2d2be1dd1c50ac9314bf7b2f5384f7",
            "5b2dc6833cc0443d476e71bf96a38c5eba0af8bb51b4741a48a205fa984e5386",
            "6189459d9401c5d35e5912001b5a244feca55f3413a94b9c88e09463e55cf6b8",
            "745b090018ba4fcf985b41bf62c3182953170a280fb4c0a4897182b83013dbc5",
            "807da68afef150a264a01e2ff15debff1da3d0031bc34bac2898397e291189d0",
            "842f90ebe38b26a0b327964bc5320559102a139782957ae2bbf917b7f7df75ec",
            "90887882d1797d26c8300395cba858bcf2006e4eace095b8666fbb77f276686a",
            "94f7d2162c933c02a3e1360658025d95269d80bad265f72d76832a49c2ed8c78",
            "abea57af998508332ec6abf8ca41474d34e61f02f888a8afeab6f70f19db504d",
            "c1891735497afab0a928968e70ef1871dd3625eb72dae17cd352c0ef8c889161",
            "f1a76f802a1c78d015974c8c0fdb3f05b04d03668ef3715b3bbbca9d6d9f785d",
            "f8a03be06e8bbdd8ded0b84a1cb622793fee1b8a16e2f5c2a8ac161e3516e7b7",
        }
    ),
    "seat-budget.py": frozenset(
        {
            "2717c2fc0e7b2968d3da373ee79146fae217fdcb12c587899b610e4213fb64c6",
            "aef98594851947c4c72d09a55b6aad0f64926f493c3502b01e6840e9c95dabed",
        }
    ),
    "herdr-send-guard.py": frozenset(
        {
            "1aaba7c236109a75f58b117c9833cde8d27788c1caa4cc33d159b22ed9b4cde9",
        }
    ),
}


def retirable(path: Path, name: str) -> bool:
    try:
        content = path.read_bytes()
    except OSError:
        return False
    marker = f"agent-lb:{Path(name).stem}:v1".encode()
    return marker in content or hashlib.sha256(content).hexdigest() in SHIPPED_SHA256.get(name, ())


def kept_retired_hooks(home: Path) -> dict[Path, str]:
    kept = {}
    for name in dict.fromkeys(RETIRED_HOOKS):
        for path in (home / ".claude/hooks" / name, home / POLICY_DIR / "hooks" / name):
            if (path.is_file() or path.is_symlink()) and not retirable(path, name):
                kept[path] = name
    return kept


def kept_warning(path: Path) -> str:
    try:
        path.read_bytes()
    except OSError as exc:
        return f"WARNING kept {path}: retired hook name, but ownership cannot be checked ({type(exc).__name__}); not ours to delete"
    return f"WARNING kept {path}: retired hook name, but no agent-lb marker and no shipped hash; not ours to delete"


def without_retired_hooks(value, names, refused, path):
    if isinstance(value, list):
        out = []
        for child in value:
            command = child.get("command") if isinstance(child, dict) else None
            if isinstance(command, str) and any(name in command for name in names):
                if retired_invocation(command, names):
                    continue
                refused.append(f"{path}: not a single retired invocation, left untouched: {command}")
            out.append(without_retired_hooks(child, names, refused, path))
        return out
    if isinstance(value, dict):
        return {key: without_retired_hooks(child, names, refused, path) for key, child in value.items()}
    return value


def retire_hook_registries(home: Path, changes: dict[Path, str | None], kept: dict[Path, str]) -> None:
    names = frozenset(RETIRED_HOOKS) - frozenset(kept.values())
    folder = home / ".claude/hooks/dispatch"
    paths = set(folder.glob("registry*.json"))
    paths.update(path for path in changes if path.parent == folder and path.name.startswith("registry"))
    refused: list[str] = []
    for path in paths:
        if path in changes and changes[path] is None:
            continue
        pending = changes.get(path)
        if pending is not None:
            registry = json.loads(pending)
        elif path.is_file():
            registry = json.loads(path.read_text())
        else:
            continue
        if isinstance(registry, dict) and registry.get("rev"):
            continue
        cleaned = without_retired_hooks(registry, names, refused, path)
        if cleaned != registry:
            changes[path] = json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n"
    for line in refused:
        print(f"refused retiring {line}")
