#!/usr/bin/env python3
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

WRITES = {
    "workflow": {"run", "enable", "disable"},
    "run": {"cancel", "rerun", "delete"},
    "pr": {"create", "merge", "close", "comment", "review", "edit"},
    "issue": {"create", "close", "comment", "edit"},
    "release": {"create", "delete", "edit", "upload"},
    "repo": {"create", "delete", "edit"},
    "secret": {"set", "delete"},
    "variable": {"set", "delete"},
}
REFUSAL = "BLOCKED: kept floor \"Alex's credentials\": gh writes may not use Alex's account. Use the factory gh App shim with GH_APP=on GH_APP_FALLBACK=off, or an explicit GitHub App installation token (GH_TOKEN/GITHUB_TOKEN)."


def token_login(token, env):
    directory = Path(env.get("GH_SHIM_DIR", "~/.cache/gh-shim")).expanduser()
    cache = directory / "token-logins.json"
    key = hashlib.sha256(token.encode()).hexdigest()[:12]
    try:
        logins = json.loads(cache.read_text())
    except (OSError, ValueError):
        logins = {}
    if not isinstance(logins, dict):
        logins = {}
    if key in logins:
        return logins[key]
    real = env.get("GH_SHIM_REAL") or next(
        (path for path in ("/opt/homebrew/bin/gh", "/usr/bin/gh", "/usr/local/bin/gh") if Path(path).is_file()), None
    )
    if not real:
        return None
    try:
        result = subprocess.run(
            [real, "api", "user", "--jq", ".login"], env=env, capture_output=True, text=True, timeout=10, check=False
        )
        login = result.stdout.strip() if result.returncode == 0 else None
        if login and re.fullmatch(r"[A-Za-z0-9-]+", login):
            directory.mkdir(parents=True, exist_ok=True)
            logins[key] = login
            temporary = cache.with_name(f"token-logins.{os.getpid()}.tmp")
            temporary.write_text(json.dumps(logins) + "\n")
            os.replace(temporary, cache)
            return login
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def write(args):
    if not args:
        return False
    if args[0] != "api":
        return len(args) > 1 and args[1] in WRITES.get(args[0], set())
    method, endpoint = "", ""
    fields, queries = [], []
    input_body = False
    i = 1
    while i < len(args):
        arg = args[i]
        value = ""
        if arg in {
            "-X",
            "--method",
            "-f",
            "-F",
            "--field",
            "--raw-field",
            "--input",
            "-H",
            "--header",
            "--hostname",
            "--jq",
            "-q",
            "--template",
            "-t",
            "--cache",
        }:
            i += 1
            value = args[i] if i < len(args) else ""
        elif arg.startswith(("--method=", "--field=", "--raw-field=", "--input=")):
            arg, value = arg.split("=", 1)
        elif arg.startswith(("-X", "-f", "-F")):
            arg, value = arg[:2], arg[2:]
        elif not arg.startswith("-") and not endpoint:
            endpoint = arg.strip("/")
        if arg in {"-X", "--method"}:
            method = value.upper()
        elif arg in {"-f", "-F", "--field", "--raw-field"}:
            fields.append(value)
            if value.startswith("query="):
                queries.append(value[6:])
        elif arg == "--input":
            input_body = True
        i += 1
    if method in {"POST", "PATCH", "PUT", "DELETE"}:
        return True
    if endpoint == "graphql":
        return (
            input_body
            or not queries
            or any(
                not query.lstrip().startswith(("query", "{")) or re.search(r"\bmutation\b", query, re.IGNORECASE)
                for query in queries
            )
        )
    return input_body or bool(fields) and method != "GET"


def check(command, environ=None, depth=0):
    env = dict(os.environ if environ is None else environ)
    if depth > 8:
        raise ValueError("nested shell limit")
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace_split = True
    lexer.commenters = ""
    words = list(lexer)
    i = 0
    while i < len(words):
        word = words[i]
        assignment = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)=(.*)", word, re.S)
        if assignment:
            env[assignment[1]] = assignment[2]
        if word and set(word) <= set(";&|()\n"):
            env = dict(os.environ if environ is None else environ)
        if os.path.basename(word) in {"sh", "bash", "zsh"} and i + 2 < len(words) and "c" in words[i + 1].lstrip("-"):
            check(words[i + 2], env, depth + 1)
        if os.path.basename(word) == "gh":
            end = next(
                (j for j in range(i + 1, len(words)) if words[j] and set(words[j]) <= set(";&|()\n")), len(words)
            )
            args = words[i + 1 : end]
            while args and (
                args[0] in {"-R", "--repo", "--hostname"} or args[0].startswith(("--repo=", "-R", "--hostname="))
            ):
                args = args[2:] if args[0] in {"-R", "--repo", "--hostname"} else args[1:]
            token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or ""
            path = shutil.which(word, path=env.get("PATH")) or word
            app_shim = False
            try:
                with open(os.path.realpath(path), encoding="utf-8") as source:
                    app_shim = "import gh_guard" in source.read(20000)
            except (OSError, UnicodeError):
                pass
            app = token.startswith("ghs_") or (
                not token and env.get("GH_APP") == "on" and env.get("GH_APP_FALLBACK") == "off" and app_shim
            )
            if write(args) and (not app or token and token_login(token, env) in {"aneym", "alexneyman"}):
                raise PermissionError(REFUSAL)
        i += 1


def main():
    if sys.argv[1:] == ["--session-env"]:
        target = os.environ.get("CLAUDE_ENV_FILE")
        if target:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write("\nunset GH_TOKEN GITHUB_TOKEN\n")
        return 0
    try:
        data = json.load(sys.stdin)
        command = data.get("tool_input", {}).get("command", "")
        if not isinstance(command, str):
            raise ValueError("command is not text")
        check(command)
    except (ValueError, TypeError, AttributeError, PermissionError):
        print(REFUSAL, file=sys.stderr)
        return 2
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print("floor-ok gh-write-guard.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
