import os
import re

SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "fish"}
KEYWORDS = {"if", "then", "do", "while", "until", "else", "elif", "!", "{", "}", "fi", "done", "builtin", "noglob"}
WRAPPERS = {
    "sudo": "ugpChDrTtU",
    "doas": "uC",
    "env": "uCS",
    "nice": "n",
    "nohup": "",
    "time": "fo",
    "command": "",
    "exec": "a",
    "timeout": "sk",
    "gtimeout": "sk",
    "stdbuf": "ioe",
    "caffeinate": "tw",
    "ionice": "cnp",
    "taskpolicy": "cgtldp",
    "setsid": "",
    "chronic": "",
    "unbuffer": "",
    "flock": "wEc",
    "xargs": "InPLdsEa",
}
SSH_VALUED = set("BbcDEeFIiJLlmOoPpQRSWw")
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def base(word):
    return os.path.basename(word.text)


def unwrap(words):
    fed = False
    while words:
        name = base(words[0])
        if name in KEYWORDS or ASSIGN.match(words[0].text):
            words = words[1:]
            continue
        if name not in WRAPPERS:
            break
        valued, args = WRAPPERS[name], words[1:]
        if name == "command" and any(a.text in ("-v", "-V") for a in args[:2]):
            return [], False
        while args and args[0].text.startswith("-") and args[0].text != "-":
            flag = args[0].text
            args = args[1:]
            if flag == "--":
                break
            if not flag.startswith("--") and len(flag) == 2 and flag[1] in valued and args:
                args = args[1:]
        while name == "env" and args and ASSIGN.match(args[0].text):
            args = args[1:]
        if name in ("timeout", "gtimeout", "flock") and args:
            args = args[1:]
        fed = fed or name == "xargs"
        words = args
    return words, fed


def shell_options(args):
    skip = False
    for n, arg in enumerate(args):
        if skip:
            skip = False
            continue
        text = arg.text
        if text == "--" or not text.startswith(("-", "+")):
            return n, None
        if text in ("-o", "+o", "-O", "+O"):
            skip = True
        elif not text.startswith("--") and "c" in text[1:]:
            return n, "c"
        elif not text.startswith("--") and "s" in text[1:]:
            return n, "s"
    return len(args), None


def shell_string(args):
    n, mode = shell_options(args)
    if mode != "c":
        return None
    rest = [a for a in args[n + 1 :] if not a.text.startswith(("-", "+"))]
    return rest[0].text if rest else None


def reads_stdin(args):
    n, mode = shell_options(args)
    return mode == "s" or (mode is None and n == len(args))


def ssh_remote(args):
    n = 0
    while n < len(args):
        text = args[n].text
        if text == "--":
            n += 1
            break
        if not text.startswith("-") or text == "-":
            break
        for k, letter in enumerate(text[1:]):
            if letter in SSH_VALUED:
                if k == len(text) - 2:
                    n += 1
                break
        n += 1
    rest = args[n + 1 :]
    return " ".join(w.text for w in rest) if rest else None


def invoked(command, pattern):
    body = re.sub(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", "\n", command, flags=re.S)
    body = re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", "''", body)
    prefix = r"(?:^|[;&|(\n]|\$\()\s*(?:(?:env|nohup|exec|timeout\s+\S+|[A-Za-z_]\w*=\S*)\s+)*(?:\S*/)?"
    return re.search(prefix + pattern, body) is not None
