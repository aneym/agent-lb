from __future__ import annotations

import ast
from pathlib import Path

FOLLOWER = "scripts/runtime_follow.py"
LOCAL_ROOTS = ("app", "scripts")


def _module_file(root: Path, dotted: str) -> str | None:
    parts = dotted.split(".")
    for rel in ("/".join(parts) + ".py", "/".join(parts) + "/__init__.py"):
        if (root / rel).is_file():
            return rel
    return None


def _packages(root: Path, dotted: str) -> list[str]:
    parts = dotted.split(".")
    found = []
    for depth in range(1, len(parts)):
        rel = "/".join(parts[:depth]) + "/__init__.py"
        if (root / rel).is_file():
            found.append(rel)
    return found


def _module_level(tree: ast.AST) -> list[ast.AST]:
    found: list[ast.AST] = []
    todo = list(ast.iter_child_nodes(tree))
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        found.append(node)
        todo.extend(ast.iter_child_nodes(node))
    return found


def _imported(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in _module_level(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return [name for name in names if name.split(".", 1)[0] in LOCAL_ROOTS]


def closure(root: Path, entry: str = FOLLOWER) -> set[str]:
    seen: set[str] = set()
    todo = [entry]
    while todo:
        rel = todo.pop()
        if rel in seen or not (root / rel).is_file():
            continue
        seen.add(rel)
        try:
            tree = ast.parse((root / rel).read_text(), filename=rel)
        except (OSError, SyntaxError, ValueError):
            continue
        for dotted in _imported(tree):
            todo.extend(_packages(root, dotted))
            found = _module_file(root, dotted)
            if found:
                todo.append(found)
    return seen
