#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

SUFFIXES = {".py", ".js", ".ts", ".mjs", ".tsx", ".jsx", ".cjs", ".mts", ".cts"}


def git(*args: str) -> bytes:
    result = subprocess.run(["git", *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def source(ref: str, path: str) -> str:
    try:
        return git("show", f"{ref}:{path}").decode("utf-8")
    except UnicodeError as error:
        raise ValueError(f"{path}: cannot classify invalid UTF-8") from error


def package_names(ref: str, files: set[str]) -> set[str]:
    paths = sorted(p for p in files if PurePosixPath(p).name == "package.json" and "\n" not in p)
    if not paths:
        return set()
    data = subprocess.run(
        ["git", "cat-file", "--batch"],
        input="".join(f"{ref}:{p}\n" for p in paths).encode(),
        capture_output=True,
        check=False,
    ).stdout
    names: set[str] = set()
    at = 0
    for _ in paths:
        end = data.find(b"\n", at)
        if end < 0:
            break
        header = data[at:end].split()
        at = end + 1
        if len(header) != 3:
            continue
        size = int(header[2])
        body, at = data[at : at + size], at + size + 1
        try:
            name = json.loads(body.decode("utf-8")).get("name")
        except (ValueError, AttributeError):
            continue
        if isinstance(name, str):
            names.add(name)
    return names


def test_file(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        re.fullmatch(r"test_.*\.py|.*_test\.py|.*\.(?:test|spec)\..+", p.name) is not None
        or p.suffix in SUFFIXES
        and bool({"tests", "__tests__"} & set(p.parts))
    )


def helper(path: str) -> bool:
    p = PurePosixPath(path)
    return bool({"tests", "__tests__", "floor"} & set(p.parts)) or p.name == "conftest.py"


def tracked(ref: str) -> set[str]:
    return {os.fsdecode(p) for p in git("ls-tree", "-rz", "--name-only", ref).split(b"\0") if p}


def python_imports(path: str, body: str, files: set[str]):
    tree = ast.parse(body, filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parent = list(PurePosixPath(path).parent.parts)
                count = node.level - 1
                target = parent[: len(parent) - count] if count <= len(parent) else []
                if node.module:
                    target += node.module.split(".")
                if not helper("/".join(target)):
                    yield f"line {node.lineno}: relative import of repo code"
                continue
            names = [node.module or ""]
        else:
            continue
        roots = [str(folder) + "/" for folder in PurePosixPath(path).parents if str(folder) != "."]
        prefixes = ["", "src/", *roots, *(root + "src/" for root in roots)]
        for name in names:
            top = name.split(".")[0]
            for prefix in prefixes:
                candidates = (
                    prefix + name.replace(".", "/") + ".py",
                    prefix + name.replace(".", "/") + "/__init__.py",
                )
                package = prefix + top + "/"
                if any(p in files and not helper(p) for p in candidates) or any(
                    p.startswith(package) and not helper(p) for p in files
                ):
                    yield f"line {node.lineno}: in-process import {name}"
                    break


def python_mocks(path: str, body: str):
    tree = ast.parse(body, filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [f"{node.module or ''}.{item.name}" for item in node.names]
        elif isinstance(node, ast.arg) and node.arg == "mocker":
            yield f"line {node.lineno}: mocker fixture"
            continue
        else:
            continue
        for name in names:
            if name.split(".")[0] in {"mock", "pytest_mock"} or name.startswith("unittest.mock"):
                yield f"line {node.lineno}: mock import {name}"


def js_mocks(body: str):
    pattern = r"""\b(?:jest|vi)\.(?:mock|fn|spyOn)\s*\(|\bmock\.module\s*\(|["']sinon["']"""
    for match in re.finditer(pattern, body):
        yield f"mock {match[0].strip()}"


def js_imports(path: str, body: str, packages: set[str]):
    pattern = r"""(?:\bfrom\s*|\bimport\s*(?:\(\s*)?|\brequire\s*\(\s*)["']([^"']+)["']"""
    for match in re.finditer(pattern, body):
        name = match[1]
        if name.startswith("."):
            target = os.path.normpath(str(PurePosixPath(path).parent / name))
            if not helper(target):
                yield f"in-process relative import {name}"
        elif any(name == package or name.startswith(package + "/") for package in packages):
            yield f"in-process workspace import {name}"


def merge_changes(ref: str, staged: bool) -> set[str] | None:
    if staged:
        merge_head = Path(git("rev-parse", "--git-path", "MERGE_HEAD").decode().strip())
        if not merge_head.exists():
            return None
        parents = ["HEAD", *merge_head.read_text().split()]
    else:
        parents = git("rev-list", "--parents", "-n", "1", ref).decode().split()[1:]
        if len(parents) < 2:
            return None
    changed: set[str] | None = None
    for parent in parents:
        paths = {
            os.fsdecode(path)
            for path in git("diff", "--name-only", "-z", "--no-renames", parent, ref, "--").split(b"\0")
            if path
        }
        changed = paths if changed is None else changed & paths
    return changed


def removed_fragments(diff: list[str]) -> dict[str | None, list[str]]:
    fragments: dict[str | None, list[str]] = {}
    path: str | None = None
    run: list[str] = []
    for line in git("diff", "--no-renames", "--unified=0", *diff, "--").decode(errors="replace").splitlines():
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else None
        if line.startswith("-") and not line.startswith("--- "):
            run.append(line[1:])
        elif run:
            fragments.setdefault(path, []).append("\n".join(run))
            run = []
    if run:
        fragments.setdefault(path, []).append("\n".join(run))
    return fragments


def moved_block(path: str, block: str, removed: dict[str | None, list[str]]) -> bool:
    return any(
        "\n" + block + "\n" in "\n" + fragment + "\n"
        for origin, fragments in removed.items()
        if origin and origin != path and test_file(origin)
        for fragment in fragments
    )


def python_move(path: str, body: str, removed: dict[str | None, list[str]]) -> bool:
    tree = ast.parse(body, filename=path)
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    ]
    if not functions:
        return False
    lines = body.splitlines()
    for node in functions:
        start = min([node.lineno, *[d.lineno for d in node.decorator_list]]) - 1
        if not moved_block(path, "\n".join(lines[start : node.end_lineno]), removed):
            return False
    return all(
        node in functions
        or isinstance(node, (ast.Import, ast.ImportFrom))
        or isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


def js_move_body(path: str, body: str, removed: dict[str | None, list[str]]) -> str:
    masked = re.sub(
        r"/\*[\s\S]*?\*/|//[^\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`",
        lambda match: "".join("\n" if c == "\n" else " " for c in match[0]),
        body,
    )
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"\b(?:test|it|describe|context|suite)(?:\.(?:only|skip|todo))?\s*\(", masked):
        if spans and match.start() < spans[-1][1]:
            continue
        depth = 1
        end = match.end()
        while end < len(masked) and depth:
            depth += (masked[end] == "(") - (masked[end] == ")")
            end += 1
        if depth:
            return body
        if end < len(body) and body[end] == ";":
            end += 1
        start = body.rfind("\n", 0, match.start()) + 1
        if body[start : match.start()].strip():
            return body
        if not moved_block(path, body[start:end], removed):
            return body
        spans.append((start, end))
    if not spans:
        return body
    remaining = body
    for start, end in reversed(spans):
        remaining = remaining[:start] + remaining[end:]
    imports = list(
        re.finditer(r"(?m)^[ \t]*import\s+(?:(?:[\s\S]*?\bfrom\s*)?[\"'][^\"'\n]+[\"'])[ \t]*;?", remaining)
    )
    residue = remaining
    for match in reversed(imports):
        residue = residue[: match.start()] + residue[match.end() :]
    if residue.strip():
        return body
    kept = [match[0] for match in imports if not moved_block(path, match[0], removed)]
    return "\n".join(kept)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on new in-process or mock tests.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--base")
    group.add_argument("--staged", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = git("rev-parse", "--show-toplevel").decode().strip()
        os.chdir(root)
        if args.staged:
            ref = git("write-tree").decode().strip()
            diff = ["--cached"]
        else:
            ref = "HEAD"
            base = git("merge-base", args.base, ref).decode().strip()
            diff = [base, ref]
        fields = git("diff", "--name-status", "-z", "--no-renames", *diff, "--").split(b"\0")
        removed = removed_fragments(diff)
        files = tracked(ref)
        authored = merge_changes(ref, args.staged)
        added: list[str] = []
        for i in range(0, len(fields) - 1, 2):
            status, path = fields[i].decode(), os.fsdecode(fields[i + 1])
            if authored is not None and path not in authored:
                continue
            if status == "A" and test_file(path):
                added.append(path)
        packages = package_names(ref, files) if any(not p.endswith(".py") for p in added) else set()
        violations: list[str] = []
        for path in added:
            body = source(ref, path)
            try:
                if path.endswith(".py"):
                    moved = python_move(path, body, removed)
                    imports = () if moved else python_imports(path, body, files)
                    mocks = () if moved else python_mocks(path, body)
                else:
                    kept = js_move_body(path, body, removed)
                    imports = js_imports(path, kept, packages)
                    mocks = js_mocks(kept)
                if list(imports):
                    violations.append(f"{path}: new in-process unit test; prove behavior through a real CLI or app")
                elif list(mocks):
                    violations.append(f"{path}: new mock test; prove behavior through a real CLI or app")
            except SyntaxError as error:
                violations.append(f"{path}: cannot classify invalid Python: {error}")
        for violation in violations:
            print(f"test-guard: {violation}", file=sys.stderr)
        print(f"test-guard: {len(violations)} violation(s)")
        if violations:
            print("fix: delete the new test and prove the behavior once on the running app, a real CLI or HTTP")
        return 1 if violations else 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"test-guard: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
