#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GATE = "/Volumes/StudioExt/repos/agent-rails/tools/gate"

HASH_NAMES = {"Dockerfile", "Containerfile", "Makefile", "GNUmakefile", "Caddyfile"}
ALIASES = {".bash": ".sh", ".zsh": ".sh", ".mk": ".sh", ".cjs": ".js", ".pyi": ".py"}

DIRECTIVE = re.compile(
    r"""^(?:
      noqa(?::\s*[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)?
    | type:\s*ignore(?:\[[\w\-, ]+\])?
    | (?:pyrefly|ty):\s*ignore\s*(?:\[[\w\-, ]+\])?
    | pyright:\s*[\w\-=, \[\]]+
    | mypy:\s*[\w\-=, ]+
    | pragma:\s*no\s+(?:cover|branch)
    | fmt:\s*(?:skip|off|on)
    | isort:\s*(?:skip|skip_file|off|on)
    | shellcheck(?:\s+(?:disable|enable|source|shell|external-sources)=\S+)+
    | (?:eslint|oxlint)-(?:disable|enable)(?:-next-line|-line)?(?:\s+[\w@/\-]+(?:\s*,\s*[\w@/\-]+)*)?
    | stylelint-(?:disable|enable)(?:-next-line|-line)?(?:\s+[\w@/\-]+(?:\s*,\s*[\w@/\-]+)*)?
    | biome-ignore(?:-all|-start|-end)?\s+lint/\S+:\s*\S.*
    | @ts-(?:ignore|expect-error|nocheck|check)
    | @(?:vitest|jest)-environment\s+[\w\-]+
    | (?:istanbul|c8|v8)\s+ignore(?:\s+(?:next|else|if|start|stop|file))?
    | prettier-ignore
    | swiftlint:(?:disable|enable)(?::next|:this|:previous)?(?:\s+[\w]+)+
    | swift-tools-version:\s*[\d.]+
    | /\s*<reference\s+(?:types|path|lib)="[^"]+"\s*/>
    | (?:syntax|escape|check)=\S+
    | -\*-\s*coding[:=]\s*[\w\-]+\s*-\*-
    | SPDX-License-Identifier:\s*\S.*
    | Copyright\s.*
    | @generated\b.*
    | .*\b(?:auto-?)?generated\b.*\bdo\s+not\s+edit\b.*
    )$""",
    re.X | re.I,
)
GENERATED_HEADER = re.compile(r"@generated\b|\b(?:auto-?)?generated\b.*\bdo\s+not\s+edit\b", re.I)
GENERATED_BY = re.compile(r"\bgenerated\s+by\s+[`\"']?([\w./\-]+)", re.I)
FIX = "delete it, and fix the hack it excuses or encode its constraint as a type, runtime check or lint rule"


def load_tokenizer(gate: Path) -> ModuleType:
    source = gate / "no-new-comments"
    sys.path.insert(0, str(gate))
    loader = importlib.machinery.SourceFileLoader("no_new_comments", str(source))
    spec = importlib.util.spec_from_loader("no_new_comments", loader)
    if spec is None:
        raise SystemExit(
            f"check_no_comments: cannot load {source}\nnext: set AGENT_RAILS_GATE_DIR to agent-rails tools/gate"
        )
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def tracked(staged: bool) -> dict[str, bytes]:
    if not staged:
        names = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
        return {name: (ROOT / name).read_bytes() for name in names if name and (ROOT / name).is_file()}
    entries = [entry.split("\t", 1) for entry in git("ls-files", "-s", "-z").decode().split("\0") if entry]
    blobs = {path: meta.split()[1] for meta, path in entries if meta.startswith("100")}
    batch = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "--batch"],
        input="".join(f"{sha}\n" for sha in blobs.values()).encode(),
        capture_output=True,
        check=True,
    ).stdout
    contents, cursor = {}, 0
    for path in blobs:
        header_end = batch.index(b"\n", cursor)
        size = int(batch[cursor:header_end].split()[2])
        contents[path] = batch[header_end + 1 : header_end + 1 + size]
        cursor = header_end + 1 + size + 1
    return contents


def pinned(files: dict[str, bytes], tokenizer: ModuleType) -> set[str]:
    exempt = set()
    for name, data in files.items():
        if PurePosixPath(name).name != tokenizer.UPSTREAM:
            continue
        try:
            listed = tokenizer.manifest(name, data.decode("utf-8", "replace"))
        except tokenizer.PolicyError:
            continue
        for path, digest in listed.items():
            if path in files and hashlib.sha256(files[path]).hexdigest() == digest:
                exempt.add(path)
    return exempt


def language(path: str, text: str, extensions: set[str]) -> str | None:
    pure = PurePosixPath(path)
    suffix = ALIASES.get(pure.suffix, pure.suffix)
    if suffix in extensions:
        return suffix
    if pure.name in HASH_NAMES or pure.name.startswith(("Dockerfile.", "Containerfile.")):
        return ".sh"
    if not pure.suffix and text.startswith("#!"):
        return ".py" if "python" in text.splitlines()[0] else ".sh"
    return None


def generated(text: str, files: dict[str, bytes]) -> bool:
    header = "\n".join(text.splitlines()[:3])
    if GENERATED_HEADER.search(header):
        return True
    match = GENERATED_BY.search(header)
    return bool(match) and match.group(1) in files


def allowed(body: str, line: int) -> bool:
    text = body.strip()
    if line == 1 and text.startswith("!"):
        return True
    parts = [part.strip(" \t*") for part in re.split(r"\s#\s*", text)]
    return all(DIRECTIVE.match(part) for part in parts if part)


def violations(files: dict[str, bytes], tokenizer: ModuleType):
    exempt = pinned(files, tokenizer)
    for path in sorted(files):
        if path in exempt or tokenizer.VENDORED.search(path):
            continue
        try:
            text = files[path].decode("utf-8")
        except UnicodeDecodeError:
            continue
        suffix = language(path, text, tokenizer.EXTENSIONS)
        if suffix is None or generated(text, files):
            continue
        rows = text.splitlines()
        for first, _last, body in tokenizer.comments(text, suffix):
            if allowed(body, first):
                continue
            docstring = suffix == ".py" and f"#{body}" not in rows[first - 1]
            kind = "docstring" if docstring else "comment"
            yield f"{path}:{first}: {kind} outside the keep list (shebang, license, lint/type directive, generated marker); fix: {FIX}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Refuse every code comment outside the keep list.")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--gate", default=os.environ.get("AGENT_RAILS_GATE_DIR", DEFAULT_GATE))
    args = parser.parse_args()
    tokenizer = load_tokenizer(Path(args.gate))
    found = list(violations(tracked(args.staged), tokenizer))
    for line in found:
        print(line, file=sys.stderr)
    if found:
        print(f"no-comments: {len(found)} comment(s) refused", file=sys.stderr)
        print(
            "next: delete each listed comment; keep only shebangs, licenses, lint/type directives and generated markers",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
