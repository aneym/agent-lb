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
PARSE_REFUSAL = "BLOCKED: gh-write-guard could not parse this command ({}), so it fails closed. No gh write was found. Split it into simpler commands, or use the Edit and Write tools."


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


def shell_words(text, heredoc=False):
    i, word, quote, active = 0, "", None, False
    while i < len(text):
        char = text[i]
        if char == "\\" and quote != "'":
            if i + 1 < len(text):
                if text[i + 1] != "\n":
                    word += text[i + 1]
                    active = True
                i += 2
                continue
        if quote != "'" and (char == "`" or text.startswith("$(", i)):
            backtick = char == "`"
            start = i + (1 if backtick else 2)
            j, level, inner = start, 1, None
            while j < len(text):
                c = text[j]
                if c == "\\" and inner != "'":
                    j += 2
                    continue
                if backtick and c == "`":
                    break
                if inner:
                    if c == inner:
                        inner = None
                elif c in "\"'":
                    inner = c
                elif not backtick:
                    if c == "(":
                        level += 1
                    elif c == ")":
                        level -= 1
                        if not level:
                            break
                j += 1
            if j == len(text):
                raise ValueError("unfinished substitution")
            yield "substitution", text[start:j]
            word += "__substitution__"
            active = True
            i = j + 1
            continue
        if heredoc:
            i += 1
            continue
        if quote:
            if char == quote:
                quote = None
            else:
                word += char
        elif char in "\"'":
            quote, active = char, True
        elif char == "#" and not active:
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif char in " \t\r\n;&|()<>":
            if active:
                if not (char in "<>" and word.isdigit()):
                    yield "word", word
                word, active = "", False
            if char not in " \t\r":
                end = i + 1
                if char in ";&|<>":
                    while end < len(text) and text[end] == char:
                        end += 1
                yield "operator", text[i:end]
                i = end
                continue
        else:
            word += char
            active = True
        i += 1
    if quote:
        raise ValueError("unfinished quote")
    if active and not heredoc:
        yield "word", word


def shell_source(text):
    pending, kept, live = [], [], []
    marker = re.compile(r"<<(?P<tabs>-)?[ \t]*(?P<spelling>'[^'\n]+'|\"[^\"\n]+\"|[\w-]+)")
    quote = None
    for line in text.splitlines(keepends=True):
        if pending:
            delimiter, tabs, quoted, executable, body = pending[0]
            if (line.lstrip("\t") if tabs else line).rstrip("\r\n") == delimiter:
                pending.pop(0)
                if executable:
                    live.append("".join(body))
                elif not quoted:
                    live.extend(
                        value for kind, value in shell_words("".join(body), heredoc=True) if kind == "substitution"
                    )
            else:
                body.append(line.lstrip("\t") if tabs else line)
            continue
        index, start = 0, 0
        while index < len(line):
            char = line[index]
            if char == "\\" and quote != "'":
                index += 2
                continue
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "#" and (index == 0 or line[index - 1].isspace()):
                break
            elif line.startswith("<<<", index):
                index += 3
                continue
            elif line.startswith("<<", index):
                match = marker.match(line, index)
                if not match:
                    raise ValueError("unknown heredoc delimiter")
                spelling = match.group("spelling")
                prefix = list(shell_words(line[:index]))
                command_words = []
                for kind, value in prefix:
                    if kind == "operator" and value[0] in ";&|()\n":
                        command_words = []
                    elif kind == "word":
                        command_words.append(value)
                executable = bool(command_words) and os.path.basename(command_words[0]) in {"sh", "bash", "zsh"}
                pending.append(
                    (shlex.split(spelling)[0], bool(match.group("tabs")), spelling[0] in "\"'", executable, [])
                )
                kept.append(line[start:index])
                index = start = match.end()
                continue
            index += 1
        kept.append(line[start:])
    if pending:
        raise ValueError("unfinished heredoc")
    return "".join(kept), live


def check(command, environ=None, depth=0):
    base = dict(os.environ if environ is None else environ)
    if depth > 8:
        raise ValueError("nested shell limit")
    source, live = shell_source(command)
    for body in live:
        check(body, base, depth + 1)
    tokens = list(shell_words(source))
    words, segments, redirect = [], [], False
    for kind, value in tokens:
        if kind == "substitution":
            check(value, base, depth + 1)
        elif kind == "operator" and value[0] in "<>":
            redirect = True
        elif kind == "operator":
            if words:
                segments.append(words)
            words, redirect = [], False
        elif redirect:
            redirect = False
        else:
            words.append(value)
    if words:
        segments.append(words)
    for words in segments:
        env, i = dict(base), 0
        while i < len(words):
            word = words[i]
            assignment = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)=(.*)", word, re.S)
            if assignment:
                env[assignment[1]] = assignment[2]
                i += 1
                continue
            name = os.path.basename(word)
            if name in {"env", "command", "exec", "nohup", "sudo"} or name in {
                "if",
                "then",
                "elif",
                "while",
                "do",
                "!",
            }:
                i += 1
                while i < len(words) and words[i].startswith("-"):
                    option = words[i]
                    i += 1
                    if name == "env" and option in {"-u", "--unset"} and i < len(words):
                        env.pop(words[i], None)
                        i += 1
                continue
            break
        if i >= len(words):
            continue
        word, args = words[i], words[i + 1 :]
        if os.path.basename(word) in {"sh", "bash", "zsh"}:
            for j, arg in enumerate(args):
                if arg.startswith("-") and "c" in arg and j + 1 < len(args):
                    check(args[j + 1], env, depth + 1)
                    break
        if os.path.basename(word) != "gh":
            continue
        while args and (
            args[0] in {"-R", "--repo", "--hostname"} or args[0].startswith(("--repo=", "-R", "--hostname="))
        ):
            args = args[2:] if args[0] in {"-R", "--repo", "--hostname"} else args[1:]
        token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or ""
        path = shutil.which(word, path=env.get("PATH")) or word
        app_shim = False
        try:
            with open(os.path.realpath(path), encoding="utf-8") as source:
                app_shim = any(line.strip() == "import gh_guard" for line in source)
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
        if data.get("tool_name", "Bash") != "Bash":
            return 0
        command = data.get("tool_input", {}).get("command", "")
        if not isinstance(command, str):
            raise ValueError("command is not text")
        check(command)
    except PermissionError:
        print(REFUSAL, file=sys.stderr)
        return 2
    except (ValueError, TypeError, AttributeError) as exc:
        print(PARSE_REFUSAL.format(exc), file=sys.stderr)
        return 2
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print("floor-ok gh-write-guard.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
