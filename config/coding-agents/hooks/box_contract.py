import json
import os
import re
import subprocess
from pathlib import Path

SCRATCH = re.compile(r"(?<![\w.~$/-])(?:/private)?/tmp/claude-\d+(?:/[^\s'\"`<>()\[\]{},;]+)+")
TRAILER = re.compile(r"Co-Authored-By:[^\n<]*<[^>\n]*>", re.I)
DOMAINS = {
    "openai": "openai.com",
    "anthropic": "anthropic.com",
    "xai": "x.ai",
    "moonshot": "moonshot.ai",
    "cursor": "cursor.com",
    "devin": "cognition.ai",
    "google": "google.com",
}


def seat_of(command):
    if "--seat" in command[:-1]:
        return command[command.index("--seat") + 1]
    return ""


def served(seat):
    dispatch = {}
    try:
        dispatch = json.loads(os.environ.get("AGENT_LB_DISPATCH") or "{}")
    except ValueError:
        pass
    if dispatch.get("seat") != seat:
        dispatch = {}
    argv = [str(Path.home() / ".agent-lb/bin/route"), "seat", seat, "--json"]
    if dispatch.get("class"):
        argv[3:3] = ["--class", dispatch["class"]]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
        info = json.loads(out.stdout) if out.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        info = {}
    model = dispatch.get("model") or info.get("model") or ""
    vendor = info.get("vendor") or ""
    return model, vendor


def scrub_scratch(brief):
    outputs = []

    def place(match):
        raw = match.group(0)
        path = raw.rstrip(".:/")
        if os.path.isfile(path):
            return raw
        canonical = "/private" + path if path.startswith("/tmp/") else path
        if canonical not in outputs:
            outputs.append(canonical)
        return canonical + raw[len(path) :]

    brief = SCRATCH.sub(place, brief)
    outputs = [p for p in outputs if not any(p.startswith(q + "/") for q in outputs)]
    if outputs:
        brief += (
            "\nThe caller's scratch paths above are output slots, not places on your machine: "
            "write only new result files there; seat-run maps each to your own output directory "
            "and copies them back.\n" + "".join(f"writes: {path}\n" for path in outputs)
        )
    return brief


def attribute(brief, seat):
    model, vendor = served(seat)
    if not model:
        return TRAILER.sub("Co-Authored-By: <the model id you run as>", brief) + (
            "\nCommit trailer: name the model you actually run as; never Claude unless you are Claude.\n"
        )
    domain = DOMAINS.get(vendor) or (vendor + ".com" if vendor else "noreply.invalid")
    trailer = f"Co-Authored-By: {model} <noreply@{domain}>"
    return TRAILER.sub(trailer, brief) + (
        f"\nCommit trailer for any commit you make: {trailer}. It names the model that wrote the change, "
        "so the reviewing seat routes by the right vendor.\n"
    )


def prepare(brief, command):
    seat = seat_of(command)
    brief = scrub_scratch(brief)
    if seat:
        brief = attribute(brief, seat)
    return brief
