"""First-match classification for read-only word reports."""

import fnmatch
import re
from pathlib import Path

CLASSES = ("external", "file_name", "db", "api", "cli", "config", "ui", "prose", "code")


def classify(path, text, col, term, allowlist, filename=False):
    if any(fnmatch.fnmatch(path, glob) for glob in term["keep"]):
        return "external"
    if any(
        fnmatch.fnmatch(path, e["path"])
        and e["word"].lower() in {term["old"].lower(), *(f.lower() for f in term["forms"])}
        for e in allowlist
    ):
        return "external"
    if text.lstrip().startswith("> ") or any(m.start() <= col < m.end() for m in re.finditer(r"https?://\S+", text)):
        return "external"
    if filename:
        return "file_name"
    suffix = Path(path).suffix.lower()
    if (
        "migrations" in Path(path).parts
        or suffix == ".sql"
        or re.search(r"CREATE TABLE|ALTER TABLE|ADD COLUMN|REFERENCES", text, re.I)
    ):
        return "db"
    strings = list(re.finditer(r"""(["'`])([^"'`]*?)\1""", text))
    literal = next((m.group(2) for m in strings if m.start() <= col < m.end()), None)
    if (literal and literal.startswith("/")) or re.search(
        r"@app\.(get|post|put|patch|delete)|router\.|APIRouter|app\.route|fetch\(", text
    ):
        return "api"
    if re.search(r"add_parser\(|add_argument\(|argparse|click\.|@app.command", text) or any(
        m.start() <= col < m.end() for m in re.finditer(r"--[\w-]+", text)
    ):
        return "cli"
    if suffix in (".json", ".toml", ".yaml", ".yml", ".ini", ".env"):
        key = re.match(r"""\s*(?:["']([^"']+)["']|([^:=]+))\s*[:=]""", text)
        if key and col < key.end() - 1:
            return "config"
    if suffix in (".tsx", ".jsx", ".html", ".vue", ".svelte", ".mjs") and (
        literal is not None or any(m.start() < col < m.end() for m in re.finditer(r">[^<]*<", text))
    ):
        return "ui"
    if suffix in (".md", ".txt", ".rst"):
        return "prose"
    marker = "#" if suffix in (".py", ".sh", ".toml", ".yaml", ".yml") else "//"
    comment = text.find(marker)
    if (comment >= 0 and col >= comment) or ("/*" in text[: col + 1] and "*/" not in text[text.find("/*") : col]):
        return "prose"
    return "code"
