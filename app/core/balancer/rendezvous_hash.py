from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256


def select_node(key: str, nodes: Sequence[str]) -> str | None:

    if not nodes:
        return None
    if len(nodes) == 1:
        return nodes[0]

    def _score(node: str) -> bytes:
        return sha256(f"{key}:{node}".encode()).digest()

    return max(nodes, key=_score)
