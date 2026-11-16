import os
import re

REDIRECT = re.compile(r"<<<|<<-|<<|<\(|>\(|<>|<&|>>|>&|>\||&>>|&>|<|>")
OPERATOR = re.compile(r";;&|;;|;&|&&|\|\||\|&|;|&|\|")
NAME = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|[0-9!?$#@*-])")
WORD_END = " \t\n;&|()<>"


class Word:
    __slots__ = ("text", "dynamic", "inner")

    def __init__(self):
        self.text = ""
        self.dynamic = False
        self.inner = []


class Command:
    __slots__ = ("words", "stdin", "inner")

    def __init__(self, words=None, stdin=None):
        self.words = words or []
        self.stdin = stdin or []
        self.inner = []


class Parser:
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.pending = []

    def script(self, stop=None):
        s = self.s
        pipelines, pipeline, command, depth = [], [], Command(), 0

        def end_command():
            nonlocal command
            if command.words or command.stdin or command.inner:
                pipeline.append(command)
            command = Command()

        def end_pipeline():
            nonlocal pipeline
            end_command()
            if pipeline:
                pipelines.append(pipeline)
            pipeline = []

        while self.i < len(s):
            c = s[self.i]
            if c in " \t":
                self.i += 1
            elif c == "\\" and s.startswith("\n", self.i + 1):
                self.i += 2
            elif c == "\n":
                self.i += 1
                end_pipeline()
                self.bodies()
            elif c == ")" and stop == ")" and depth == 0:
                self.i += 1
                break
            elif c == "#":
                end = s.find("\n", self.i)
                self.i = len(s) if end < 0 else end
            elif c == "&" and s.startswith(">", self.i + 1):
                self.redirect(command)
            elif c in ";&|":
                found = OPERATOR.match(s, self.i)
                op = found.group() if found else c
                self.i += len(op)
                if op in ("|", "|&"):
                    end_command()
                else:
                    end_pipeline()
            elif c in "()":
                self.i += 1
                depth = depth + 1 if c == "(" else max(depth - 1, 0)
                end_pipeline()
            elif c in "<>":
                self.redirect(command)
            else:
                word = self.word()
                if word.text.isdigit() and not word.dynamic and s[self.i : self.i + 1] in ("<", ">"):
                    continue
                command.words.append(word)
        end_pipeline()
        return pipelines

    def bodies(self):
        s = self.s
        for command, delimiter, strip in self.pending:
            lines = []
            while self.i < len(s):
                end = s.find("\n", self.i)
                end = len(s) if end < 0 else end
                line = s[self.i : end]
                self.i = end + 1
                if (line.lstrip("\t") if strip else line) == delimiter:
                    break
                lines.append(line)
            command.stdin.append("\n".join(lines))
        self.pending = []

    def redirect(self, command):
        found = REDIRECT.match(self.s, self.i)
        op = found.group() if found else self.s[self.i]
        self.i += len(op)
        if op in ("<(", ">("):
            command.inner.append(self.script(")"))
            return
        while self.s[self.i : self.i + 1] in (" ", "\t"):
            self.i += 1
        word = self.word()
        if op in ("<<", "<<-"):
            self.pending.append((command, word.text, op == "<<-"))
            return
        if op == "<<<":
            command.stdin.append(word.text)
        command.inner.extend(word.inner)

    def word(self):
        s, word = self.s, Word()
        while self.i < len(s):
            c = s[self.i]
            if c in WORD_END:
                break
            if c == "\\":
                if s[self.i + 1 : self.i + 2] != "\n":
                    word.text += s[self.i + 1 : self.i + 2]
                self.i += 2
            elif c == "'":
                end = s.find("'", self.i + 1)
                end = len(s) if end < 0 else end
                word.text += s[self.i + 1 : end]
                self.i = end + 1
            elif c == '"':
                self.i += 1
                self.double(word)
            elif c == "$":
                self.dollar(word)
            elif c == "`":
                self.backtick(word)
            else:
                word.text += c
                self.i += 1
        return word

    def double(self, word):
        s = self.s
        while self.i < len(s) and s[self.i] != '"':
            c = s[self.i]
            if c == "\\" and s[self.i + 1 : self.i + 2] in ("$", "`", '"', "\\", "\n"):
                if s[self.i + 1] != "\n":
                    word.text += s[self.i + 1]
                self.i += 2
            elif c == "$":
                self.dollar(word)
            elif c == "`":
                self.backtick(word)
            else:
                word.text += c
                self.i += 1
        self.i += 1

    def dollar(self, word):
        s, start = self.s, self.i
        nxt = s[start + 1 : start + 2]
        if s.startswith("$((", start):
            depth, end = 0, start + 1
            while end < len(s):
                depth += {"(": 1, ")": -1}.get(s[end], 0)
                if depth == 0:
                    break
                end += 1
            word.text += s[start : end + 1]
            word.dynamic = True
            self.i = end + 1
        elif nxt == "(":
            self.i += 2
            word.inner.append(self.script(")"))
            word.text += "$(...)"
            word.dynamic = True
        elif nxt == "{":
            end = s.find("}", start)
            end = len(s) - 1 if end < 0 else end
            word.text += s[start : end + 1]
            word.dynamic = True
            self.i = end + 1
        elif nxt == "'":
            end = start + 2
            while end < len(s) and s[end] != "'":
                end += 2 if s[end] == "\\" else 1
            raw = s[start + 2 : end]
            try:
                word.text += raw.encode("latin-1", "backslashreplace").decode("unicode_escape")
            except UnicodeDecodeError:
                word.text += raw
            self.i = end + 1
        else:
            match = NAME.match(s, start)
            if match:
                word.text += match.group()
                word.dynamic = True
                self.i = match.end()
            else:
                word.text += "$"
                self.i += 1

    def backtick(self, word):
        s, end, body = self.s, self.i + 1, ""
        while end < len(s) and s[end] != "`":
            if s[end] == "\\" and end + 1 < len(s):
                body += s[end + 1] if s[end + 1] in "`\\$" else s[end : end + 2]
                end += 2
            else:
                body += s[end]
                end += 1
        self.i = end + 1
        word.inner.append(parse(body))
        word.text += "`...`"
        word.dynamic = True


def parse(text):
    return Parser(text).script()


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
