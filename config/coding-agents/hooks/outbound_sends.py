import base64
import binascii
import os
import re

from outbound_shell import SHELLS, base, parse, reads_stdin, shell_string, ssh_remote, unwrap  # type: ignore[missing-import]

APP_MESSAGES = re.compile(
    r"(?:application|app|process)\s*\(?\s*[\"'](?:messages|imessage|ichat|com\.apple\.(?:mobilesms|ichat|messages))[\"']"
    r"|com\.apple\.(?:mobilesms|ichat)\b|application\s+id\s+[\"']com\.apple\.(?:mobilesms|ichat)"
)
APP_MAIL = re.compile(
    r"(?:application|app|process)\s*\(?\s*[\"'](?:mail|com\.apple\.mail)[\"']|com\.apple\.mail\b"
    r"|application\s+id\s+[\"']com\.apple\.mail[\"']|outgoing\s*message"
)
SCRIPTING = re.compile(
    r"osascript|osacompile|nsapplescript|applescript|\bjxa\b|-l\s*javascript|tell\s+(?:application|app)\b"
    r"|application\s*\(|scriptingbridge|sbapplication|appscript|run\s+script|nsappleeventdescriptor"
)
SEND = re.compile(r"\bsend\b|\.send\s*\(")
UI_SEND = re.compile(r"keystroke|key\s+code|click\s+(?:button|menu\s+item)|perform\s+action")
UI_TARGET = re.compile(r"messages|imessage|\bmail\b|\bsms:|imessage:|mailto:|mobilesms")
SHORTCUT_TEXT = re.compile(r"\bshortcuts\s+run\b|shortcuts\s+events|run\s+shortcut\b")
TOUCH = re.compile(r"messages|imessage|imsg|mobilesms|ichat|\bmail\b|com\.apple\.mail|\bsms\b|sendmail")
HTTP_SEND = re.compile(
    r"api\.twilio\.com/[^\s\"']*/messages|(?:messaging|conversations)\.twilio\.com/[^\s\"']*messages"
    r"|api\.postmarkapp\.com/email|api\.sendgrid\.com/v3/mail/send|api\.resend\.com/emails"
    r"|api\.(?:eu\.)?mailgun\.net/v\d/[^\s\"']*/messages|gmail\.googleapis\.com/[^\s\"']*/(?:messages|drafts)/send"
    r"|googleapis\.com/(?:upload/)?gmail/[^\s\"']*/(?:messages|drafts)/send|graph\.microsoft\.com/[^\s\"']*/sendmail"
    r"|api\.(?:brevo|sendinblue)\.com/v3/smtp/email|api\.mailjet\.com/v3(?:\.1)?/send|api\.telnyx\.com/v2/messages"
    r"|rest\.nexmo\.com/sms|api\.(?:nexmo|vonage)\.com/[^\s\"']*messages|api\.plivo\.com/[^\s\"']*/message"
    r"|api\.sendblue\.co/api/send|loopmessage\.com/api/[^\s\"']*send|/api/v1/message/(?:text|attachment)"
    r"|resend\.emails\.send|sgmail\.send|postmark[^\s]*\.send(?:email|mail)|messages\.create\s*\("
)
SMTP = re.compile(r"smtplib|\.send_message\s*\(|\.sendmail\s*\(|nodemailer|sendmail\s*\(|--mail-rcpt|smtps?://")
DATA_FLAG = re.compile(
    r"(?:^|\s)(?:-d|--data[\w-]*|-F|--form[\w-]*|--json|-T|--upload-file)(?=[\s=]|$)|-X\s*(?:POST|PUT)"
    r"|--request\s*(?:POST|PUT)|\.post\s*\(|method\s*[:=]\s*[\"']post|requests\.request\s*\(\s*[\"']post"
)
QUOTED_CLI = re.compile(r"[\"'](?:/[\w./-]*/)?(sendmail|msmtp|swaks|mailx|ssmtp|imsg|imessage|osascript)[\"']")
SPAWN = re.compile(
    r"subprocess|popen|os\.system|os\.exec|spawn|execfile|execsync|child_process|process\.run|open3|system\s*\("
)
SENDERS = {"sendmail", "msmtp", "swaks", "ssmtp", "esmtp", "mutt", "neomutt", "mpack", "bsd-mailx"}
MAILERS = {"mail", "mailx", "nail", "s-nail", "heirloom-mailx"}
IMESSAGE_CLIS = {
    "imsg",
    "imessage",
    "imessage-cli",
    "imessage-send",
    "imessages",
    "bluebubbles",
    "sendblue",
    "send-sms",
    "sms",
}
READ_VERBS = {"chats", "history", "list", "read", "search", "watch", "contacts", "export", "help", "ls", "show"}
HELP = {"--help", "-h", "--version", "-V", "help"}
INTERPRETERS = re.compile(r"^(?:python[\d.]*|node|deno|bun|tsx|ruby|perl|swift|php|osascript)$")
SCRIPT_SUFFIX = (".scpt", ".scptd", ".applescript", ".jxa")
SHELL_SUFFIX = (".sh", ".bash", ".zsh", ".command")
TEXT_FLAGS = {"-m", "--message", "--text", "--body", "-b", "--content", "--msg", "-t"}
TEXT_FIELDS = re.compile(
    r"(?:^|[&\"'{,\s])(?:body|textbody|htmlbody|text|html|content|message|msg)[\"']?\s*[=:]\s*", re.I
)
B64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
HEXRUN = re.compile(r"\b[0-9a-fA-F]{32,}\b")
ESCAPES = re.compile(r"\\x[0-9a-fA-F]{2}|\\[0-7]{3}|\\u[0-9a-fA-F]{4}")
JOINS = re.compile(r"[\"']\s*(?:&|\+|\.\.)\s*[\"']")
MAX_FILE = 1 << 20
RAILS_SERVERS = {"rails", "rails_one", "rails_lane", "railsone"}
MCP_VERB = re.compile(r"send|reply|forward|deliver|post_message|create_message|compose")
MCP_NOUN = re.compile(r"imessage|imsg|message|sms|mms|mail|whatsapp|(?:^|_)texts?(?:_|$)")
MCP_TEXT_KEY = re.compile(r"body|text|message|content|html|plain|msg|raw", re.I)


class Unreadable(Exception):
    pass


class Scan:
    def __init__(self, cwd):
        self.cwd = cwd
        self.readings = []
        self.texts = []
        self.kinds = []
        self.unreadable = []
        self.files = set()
        self.shells_seen = 0

    def add(self, text):
        if text and text not in self.readings and len(self.readings) < 400:
            self.readings.append(text)

    def kind(self, name):
        if name not in self.kinds:
            self.kinds.append(name)


def printable(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    shown = sum(ch.isprintable() or ch in "\n\t\r" for ch in text)
    return text if text and shown >= 0.9 * len(text) else None


def decoded(text):
    found = []
    for token in B64.findall(text):
        for decode in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                value = printable(decode(token + "=" * (-len(token) % 4)))
            except (binascii.Error, ValueError):
                value = None
            if value and re.search(r"[A-Za-z]{3}", value):
                found.append(value)
                break
    for token in HEXRUN.findall(text):
        try:
            value = printable(bytes.fromhex(token if len(token) % 2 == 0 else token[:-1]))
        except ValueError:
            value = None
        if value:
            found.append(value)
    if ESCAPES.search(text):
        try:
            found.append(text.encode("latin-1", "backslashreplace").decode("unicode_escape"))
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    if JOINS.search(text):
        found.append(JOINS.sub("", text))
    return found


def read_file(scan, path):
    full = os.path.join(scan.cwd, os.path.expanduser(path))
    real = os.path.realpath(full)
    if real in scan.files or len(scan.files) > 24:
        return True
    try:
        if not os.path.isfile(real):
            return False
        with open(real, "rb") as handle:
            data = handle.read(MAX_FILE)
    except OSError:
        return False
    scan.files.add(real)
    text = printable(data)
    if text is None:
        text = data.decode("latin-1") + "\n" + data.replace(b"\x00", b"").decode("latin-1")
    scan.add(text)
    if path.endswith(SHELL_SUFFIX) or text.startswith(
        ("#!/bin/sh", "#!/bin/bash", "#!/usr/bin/env bash", "#!/bin/zsh")
    ):
        shell(scan, text, 1)
    return True


def flag_values(words):
    values = []
    for n, word in enumerate(words):
        text = word.text
        if text in TEXT_FLAGS and n + 1 < len(words):
            values.append(words[n + 1].text)
        elif "=" in text and text.split("=", 1)[0] in TEXT_FLAGS:
            values.append(text.split("=", 1)[1])
    return values


def cli(scan, name, args, command, depth):
    texts = [a.text for a in args]
    if name in SENDERS and not set(texts) & HELP:
        scan.kind("cli:" + name)
    elif name in MAILERS and any(not t.startswith("-") or t in ("-s", "-a", "-c", "-b", "-r") for t in texts):
        scan.kind("cli:" + name)
    elif name in IMESSAGE_CLIS and texts and not (texts[0] in READ_VERBS or set(texts) & HELP):
        scan.kind("cli:" + name)
    elif name == "shortcuts" and texts[:1] == ["run"]:
        scan.kind("shortcut")
    elif name == "twilio" and any(re.search(r"messages?:create|message", t) for t in texts):
        scan.kind("cli:twilio")
    elif name == "osascript":
        osascript(scan, args, command)
    if name in SENDERS | MAILERS | IMESSAGE_CLIS:
        scan.texts += flag_values(args) + list(command.stdin)


def osascript(scan, args, command):
    scripts, n = [], 0
    while n < len(args):
        text = args[n].text
        if text in ("-e", "-l", "-s") and n + 1 < len(args):
            if text == "-e":
                if args[n + 1].dynamic:
                    scan.unreadable.append("osascript -e body")
                scripts.append(args[n + 1].text)
            n += 2
            continue
        if not text.startswith("-"):
            if not read_file(scan, text):
                scan.unreadable.append("osascript script file")
            break
        n += 1
    for body in scripts + list(command.stdin):
        scan.add(body)


def look(scan, command, before, depth):
    for word in command.words:
        scan.add(word.text)
        for sub in word.inner:
            walk(scan, sub, depth + 1)
    for sub in command.inner:
        walk(scan, sub, depth + 1)
    for body in command.stdin:
        scan.add(body)
        scan.texts.append(body)
    words, _ = unwrap(command.words)
    if not words:
        return
    name, args = base(words[0]), words[1:]
    if words[0].text.startswith(("./", "/", "~/")) or words[0].text.endswith(SHELL_SUFFIX + SCRIPT_SUFFIX):
        read_file(scan, words[0].text)
    if INTERPRETERS.match(name):
        for arg in args:
            if not arg.text.startswith("-") and ("/" in arg.text or "." in arg.text):
                read_file(scan, arg.text)
                break
    for arg in args:
        if arg.text.endswith(SCRIPT_SUFFIX):
            read_file(scan, arg.text)
    cli(scan, name, args, command, depth)
    if name == "osascript" and not command.stdin and before and not any(a.text == "-e" for a in args):
        feeder, _ = unwrap(before[-1].words)
        if feeder and base(feeder[0]) == "cat":
            for arg in feeder[1:]:
                if not arg.text.startswith("-") and not read_file(scan, arg.text):
                    scan.unreadable.append("osascript stdin file")
        elif not before[-1].stdin:
            scan.unreadable.append("osascript stdin")
    if name in SHELLS:
        script = shell_string(args)
        if script is not None:
            shell(scan, script, depth + 1)
        elif reads_stdin(args):
            for body in list(command.stdin) + [b for c in before for b in c.stdin]:
                shell(scan, body, depth + 1)
        else:
            for arg in args:
                if not arg.text.startswith("-"):
                    read_file(scan, arg.text)
                    break
    elif name == "eval":
        shell(scan, " ".join(w.text for w in args), depth + 1)
    elif name == "ssh" or (args[:1] and args[0].text == "ssh"):
        remote = ssh_remote(args if name == "ssh" else args[1:])
        if remote is not None:
            shell(scan, remote, depth + 1)
        for body in command.stdin:
            shell(scan, body, depth + 1)


def walk(scan, pipelines, depth):
    if depth > 10:
        raise Unreadable("the command nests too deeply to read")
    for pipeline in pipelines:
        for n, command in enumerate(pipeline):
            look(scan, command, pipeline[:n], depth)


def shell(scan, text, depth):
    scan.shells_seen += 1
    if scan.shells_seen > 60:
        raise Unreadable("the command expands into too many shells to read")
    scan.add(text)
    walk(scan, parse(text), depth)


def classify(scan):
    queue, seen = list(scan.readings), set()
    while queue and len(seen) < 600:
        text = queue.pop()
        if text in seen:
            continue
        seen.add(text)
        for value in decoded(text):
            if value not in seen:
                scan.add(value)
                queue.append(value)
                if re.search(r"(?:^|[;&|\n])\s*\w", value) and not value.strip().startswith(("tell", "{")):
                    try:
                        shell(scan, value, 1)
                    except (Unreadable, RecursionError, IndexError):
                        pass
                    queue.extend(r for r in scan.readings if r not in seen)
    corpus = "\n".join(scan.readings).lower()
    scripted = SCRIPTING.search(corpus)
    if scripted and SEND.search(corpus) and APP_MESSAGES.search(corpus):
        scan.kind("applescript:messages")
    if scripted and SEND.search(corpus) and APP_MAIL.search(corpus):
        scan.kind("applescript:mail")
    if "system events" in corpus and UI_SEND.search(corpus) and UI_TARGET.search(corpus):
        scan.kind("applescript:ui-send")
    if SHORTCUT_TEXT.search(corpus):
        scan.kind("shortcut")
    match = HTTP_SEND.search(corpus)
    if match and (
        DATA_FLAG.search(corpus) or not re.search(r"\bcurl\b|\bwget\b|\bhttps?\b", corpus) or "send" in match.group()
    ):
        host = re.match(r"(?:api\.|rest\.|server\.)?([\w-]*)", match.group())
        scan.kind("http:" + (host.group(1) if host and host.group(1) else "sdk"))
    if SMTP.search(corpus) and re.search(r"sendmail|send_message|sendmail\s*\(|nodemailer|mail-rcpt|smtp", corpus):
        if re.search(r"\.send_message\s*\(|\.sendmail\s*\(|sendmail\s*\(|--mail-rcpt|createtransport", corpus):
            scan.kind("smtp")
    if SPAWN.search(corpus):
        for cli_name in QUOTED_CLI.findall(corpus):
            if cli_name != "osascript":
                scan.kind("cli:" + cli_name)
    if scan.unreadable and (TOUCH.search(corpus) or SEND.search(corpus)):
        scan.kind("unreadable:" + scan.unreadable[0].replace(" ", "-"))
    if scan.kinds:
        scan.texts += sent_texts(scan.readings)
    return scan.kinds


def sent_texts(readings):
    found = []
    for text in readings:
        for match in re.finditer(r"\bsend\s*\(?\s*([\"'])((?:\\.|(?!\1).)*)\1", text, re.S):
            found.append(match.group(2))
        for match in re.finditer(r"\bcontent\s*:\s*([\"'])((?:\\.|(?!\1).)*)\1", text, re.S):
            found.append(match.group(2))
        for match in TEXT_FIELDS.finditer(text):
            rest = text[match.end() :]
            quoted = re.match(r"([\"'])((?:\\.|(?!\1).)*)\1", rest, re.S)
            found.append(quoted.group(2) if quoted else re.split(r"[&\s]", rest, 1)[0])
    return [t for t in found if t]


def mcp_send(tool_name, tool_input):
    parts = tool_name.split("__")
    if len(parts) < 3:
        return None, []
    server, tool = parts[1].lower().replace("-", "_"), "__".join(parts[2:]).lower().replace("-", "_")
    if server in RAILS_SERVERS:
        return None, []
    if not (MCP_VERB.search(tool) and MCP_NOUN.search(server + "__" + tool)):
        return None, []
    values = tool_input if isinstance(tool_input, dict) else {}
    texts = [v for k, v in values.items() if isinstance(v, str) and MCP_TEXT_KEY.search(k)]
    texts = texts or [v for v in values.values() if isinstance(v, str)]
    return "mcp:%s:%s" % (server, tool), texts


def bash_scan(command, cwd):
    scan = Scan(cwd or os.getcwd())
    scan.add(command)
    try:
        shell(scan, command, 0)
    except (Unreadable, RecursionError, IndexError) as exc:
        scan.unreadable.append(str(exc) or "the command does not parse")
    classify(scan)
    return scan
