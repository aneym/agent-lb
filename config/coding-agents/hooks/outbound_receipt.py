import base64
import hashlib
import json
import os
import re
import time
import urllib.request

from ed25519_verify import verify  # type: ignore[missing-import]

ENV = "AGENT_OUTBOUND_APPROVAL"
CITED = re.compile(r"(?:--approval(?:=|\s+)|\b%s=)([\"']?)([^\s\"';|&]+)\1" % ENV)
TOKEN = re.compile(r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{40,}")
KEYS_URL = "https://one.rails.so/.well-known/rails-holds-key"
PINNED = {"AnxoembdPqh0mjX_": "X-rNJDKYfpGM5lR7bpyDPYPRjS7DHWmlglv4TQZJ9w4"}
USED = os.path.join(os.path.expanduser("~"), ".claude", "hooks", "state", "outbound-receipts-used.jsonl")


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def cited_path(command, tool_input):
    found = CITED.search(command or "")
    if found:
        return found.group(2)
    if isinstance(tool_input, dict) and isinstance(tool_input.get("approval"), str):
        return tool_input["approval"]
    return os.environ.get(ENV) or None


def live_keys():
    try:
        with urllib.request.urlopen(
            urllib.request.Request(KEYS_URL, headers={"User-Agent": "outbound-send-guard"}), timeout=6
        ) as reply:
            keys = json.loads(reply.read(65536)).get("keys") or []
    except (OSError, ValueError, AttributeError):
        return {}
    return {
        k["kid"]: k["x"]
        for k in keys
        if isinstance(k, dict) and k.get("crv") == "Ed25519" and k.get("kid") and k.get("x")
    }


def receipt_claims(token, keys):
    head, body, sig = token.split(".")
    try:
        header, claims = json.loads(unb64(head)), json.loads(unb64(body))
        signature = unb64(sig)
    except (ValueError, TypeError):
        return None
    if not isinstance(header, dict) or header.get("alg") != "EdDSA" or header.get("typ") != "rails-hold+jws":
        return None
    public = keys.get(header.get("kid"))
    if not public or not verify(unb64(public), (head + "." + body).encode("ascii"), signature):
        return None
    return claims if isinstance(claims, dict) else None


def digests(text):
    forms = {text, text.rstrip("\n"), text + "\n", text.strip()}
    return {hashlib.sha256(form.encode("utf-8")).hexdigest() for form in forms if form}


def used(hold_id):
    marker = hashlib.sha256(str(hold_id).encode()).hexdigest()
    try:
        with open(USED) as handle:
            return any(marker in line for line in handle)
    except OSError:
        return False


def mark_used(hold_id):
    try:
        os.makedirs(os.path.dirname(USED), exist_ok=True)
        with open(USED, "a") as handle:
            handle.write(
                json.dumps({"at": int(time.time()), "hold": hashlib.sha256(str(hold_id).encode()).hexdigest()}) + "\n"
            )
    except OSError:
        pass


def check(cited, texts, cwd, keys=None):
    if not cited:
        return "no Rails hold receipt is cited", None
    path = os.path.join(cwd or os.getcwd(), os.path.expanduser(cited))
    try:
        with open(path) as handle:
            content = handle.read(65536)
    except (OSError, UnicodeDecodeError):
        return "the cited receipt file does not exist or cannot be read", None
    tokens = TOKEN.findall(content)
    if not tokens:
        return (
            "the cited file holds no Rails hold receipt (a local approval file or agent grant is not Alex's yes)",
            None,
        )
    trusted = dict(PINNED) if keys is None else dict(keys)
    claims = None
    for token in tokens:
        claims = receipt_claims(token, trusted)
        if claims is None and keys is None:
            trusted.update(live_keys())
            claims = receipt_claims(token, trusted)
        if claims:
            break
    if not claims:
        return "the cited receipt is not signed by a key Rails publishes", None
    if not float(claims.get("exp") or 0) > time.time():
        return "the cited Rails hold receipt has expired", None
    if claims.get("media"):
        return (
            "the cited hold carries files, which this guard cannot match; send it through the Rails tool that raised it",
            None,
        )
    if used(claims.get("hold_id")):
        return "the cited Rails hold receipt was already used for a send from this host", None
    sent = [t for t in texts if isinstance(t, str) and t]
    if not sent:
        return "the guard could not find the exact text being sent to match against the receipt", None
    if not any(str(claims.get("text_sha256", "")).lower() in digests(t) for t in sent):
        return "the cited Rails hold receipt was allowed for a different text (its sha256 does not match)", None
    return None, claims.get("hold_id")
