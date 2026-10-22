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


def shell_commands(text, heredoc=False):
    words, bodies, pending = [], [], []
    word, quote, started = "", '"' if heredoc else None, False
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and quote != "'":
            if i + 1 == len(text):
                raise ValueError("unfinished escape")
            following = text[i + 1]
            if following != "\n":
                word += following if quote is None or following in '$`"\\' else "\\" + following
                started = True
            i += 2
            continue
        if quote == "'":
            if char == "'":
                quote = None
            else:
                word += char
            i += 1
            continue
        if char == "`" or text.startswith("$(", i):
            backtick = char == "`"
            start = i + (1 if backtick else 2)
            j, level, inner_quote = start, 1, None
            while j < len(text):
                current = text[j]
                if current == "\\" and inner_quote != "'":
                    j += 2
                    continue
                if backtick and current == "`":
                    break
                if inner_quote:
                    if current == inner_quote:
                        inner_quote = None
                elif current in "\"'":
                    inner_quote = current
                elif not backtick:
                    if current == "(":
                        level += 1
                    elif current == ")":
                        level -= 1
                        if not level:
                            break
                j += 1
            if j == len(text):
                raise ValueError("unfinished substitution")
            bodies.append(text[start:j])
            word += "__substitution__"
            started = True
            i = j + 1
            continue
        if quote:
            if char == quote and not heredoc:
                quote = None
            else:
                word += char
            i += 1
            continue
        if char in "\"'":
            quote = char
            started = True
            i += 1
            continue
        if char == "#" and not started:
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        if text.startswith("<<", i) and not text.startswith("<<<", i):
            match = re.match(r"<<(-?)[ \t]*((?:[^\s;&|<>()'\"\\]|\\.|'[^']*'|\"[^\"]*\")+)", text[i:])
            if not match:
                raise ValueError("missing heredoc delimiter")
            tabs, spelling = match.groups()
            pending.append(("".join(shlex.split(spelling)), bool(tabs), any(c in spelling for c in "\\\"'")))
            if started and not word.isdigit():
                words.append(word)
            word, started = "", False
            i += match.end()
            continue
        if char in "<>":
            if started and not word.isdigit():
                words.append(word)
            word, started = "", False
            match = re.match(r"[<>]+&?", text[i:])
            if match is None:
                raise ValueError("invalid redirection")
            words.append((match[0],))
            i += match.end()
            continue
        if char in " \t\r\n;&|()":
            if started:
                words.append(word)
                word, started = "", False
            i += 1
            if char == "\n":
                for delimiter, tabs, quoted in pending:
                    body = []
                    while i < len(text):
                        end = text.find("\n", i)
                        end = len(text) if end < 0 else end + 1
                        line, i = text[i:end], end
                        if tabs:
                            line = line.lstrip("\t")
                        if line.rstrip("\r\n") == delimiter:
                            bodies.append(("".join(body), quoted))
                            break
                        body.append(line)
                    else:
                        raise ValueError("unfinished heredoc")
                pending = []
            if char in "\n;&|()":
                yield words, bodies
                words, bodies = [], []
            continue
        word += char
        started = True
        i += 1
    if quote and not heredoc or pending:
        raise ValueError("unfinished shell input")
    if started:
        words.append(word)
    yield words, bodies


def check(command, environ=None, depth=0):
    if depth > 8:
        raise ValueError("nested shell limit")
    for words, bodies in shell_commands(command):
        env = dict(os.environ if environ is None else environ)
        args = []
        skip = False
        for word in words:
            if isinstance(word, tuple):
                skip = True
            elif skip:
                skip = False
            else:
                args.append(word)
        while args:
            assignment = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)=(.*)", args[0], re.S)
            if assignment:
                env[assignment[1]] = assignment[2]
                args.pop(0)
            elif os.path.basename(args[0]) in {"env", "command", "exec", "builtin"}:
                args.pop(0)
                while args and args[0].startswith("-"):
                    option = args.pop(0)
                    if option in {"-u", "--unset"} and args:
                        env.pop(args.pop(0), None)
            else:
                break
        name = os.path.basename(args[0]) if args else ""
        shell = name in {"sh", "bash", "zsh", "dash", "ksh"}
        for body in bodies:
            if isinstance(body, tuple):
                source, quoted = body
                if shell:
                    check(source, env, depth + 1)
                elif not quoted:
                    for _, expansions in shell_commands(source, heredoc=True):
                        for expansion in expansions:
                            check(expansion, env, depth + 1)
            else:
                check(body, env, depth + 1)
        if shell:
            for i, arg in enumerate(args[1:], 1):
                if arg.startswith("-") and "c" in arg[1:] and i + 1 < len(args):
                    check(args[i + 1], env, depth + 1)
                    break
        if name != "gh":
            continue
        executable, args = args[0], args[1:]
        while args and (
            args[0] in {"-R", "--repo", "--hostname"} or args[0].startswith(("--repo=", "-R", "--hostname="))
        ):
            args = args[2:] if args[0] in {"-R", "--repo", "--hostname"} else args[1:]
        token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or ""
        path = shutil.which(executable, path=env.get("PATH")) or executable
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
