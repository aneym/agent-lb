from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from app.core.state_dir import state_dir
from app.lb_service import ServiceError, ServiceUnavailable, request, retrying
from app.status_cli import DEFAULT_BASE_URL, StatusObservationError, validated_base_url

PROVIDER = "gemini"
DEFAULT_LIFETIME = 900
KEEP_EXPIRED = 86400
START_AGAIN = "run `agent-lb add-account gemini` again for a fresh link"


class SignInError(Exception):
    pass


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "add-account", help="Add or refresh a Gemini (Neon) account: one link, one pasted URL."
    )
    parser.add_argument(
        "provider",
        choices=(PROVIDER,),
        help="gemini only; anthropic and openai accounts are added with `agent-lb profiles add` "
        "plus `agent-lb reauth`.",
    )
    parser.add_argument(
        "--callback-file",
        metavar="PATH",
        help="Finish sign-in with the pasted address-bar URL read from PATH, or - for stdin.",
    )
    parser.add_argument("--flow", metavar="ID", help="Finish this flow instead of the newest remembered one.")
    parser.add_argument("--base-url", help="Service URL (default: AGENT_LB_BASE_URL or localhost).")
    parser.add_argument(
        "--timeout", type=float, default=60.0, metavar="SECONDS", help="How long to retry an unreachable service."
    )
    parser.add_argument("--json", action="store_true")


def flow_path() -> Path:
    return state_dir() / "add-account-gemini.json"


def run(args) -> None:
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print("--timeout must be finite and greater than zero", file=sys.stderr)
        raise SystemExit(2)
    if args.flow and not args.callback_file:
        print("--flow finishes a sign-in and needs --callback-file", file=sys.stderr)
        raise SystemExit(2)
    try:
        base = validated_base_url(args.base_url or os.getenv("AGENT_LB_BASE_URL", DEFAULT_BASE_URL))
    except StatusObservationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    try:
        if args.callback_file:
            _finish(args, base)
        else:
            _start(args, base)
    except ServiceUnavailable as exc:
        print(f"agent-lb at {base} is unreachable ({exc}); start it or pass --base-url, then rerun", file=sys.stderr)
        raise SystemExit(1) from None
    except (SignInError, ServiceError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        print(f"add-account failed ({type(exc).__name__}); {START_AGAIN}", file=sys.stderr)
        raise SystemExit(1) from None


def _start(args, base: str) -> None:
    accounts = retrying(lambda: _gemini_accounts(base), "account lookup", args.timeout)
    flow = retrying(lambda: request(base, "/api/oauth/start", body={"provider": PROVIDER}), "oauth start", args.timeout)
    if not isinstance(flow, dict) or not flow.get("flowId") or not flow.get("authorizationUrl"):
        raise ServiceError("agent-lb returned an incomplete Gemini sign-in flow")
    expires_at = time.time() + float(flow.get("expiresInSeconds") or DEFAULT_LIFETIME)
    flows = _load()
    flows[flow["flowId"]] = {"expiresAt": expires_at, "accountIds": [row.get("accountId") for row in accounts]}
    _save(flows)
    finish = f"agent-lb add-account gemini --flow {flow['flowId']} --callback-file - < PASTED_URL_FILE"
    if args.json:
        expires = datetime.fromtimestamp(expires_at, UTC).isoformat().replace("+00:00", "Z")
        print(
            json.dumps(
                {
                    "authorizationUrl": flow["authorizationUrl"],
                    "flowId": flow["flowId"],
                    "expiresAt": expires,
                    "finish": finish,
                }
            )
        )
        return
    print("Gemini (Neon) sign-in link. Open it on your own computer and sign in with the Google account to add:")
    print(flow["authorizationUrl"])
    print(f"It expires at {_eastern_time(expires_at)} ({_minutes_left(expires_at)}).")
    print(
        "After sign-in, copy the full URL from the address bar of the page Google lands on, even if it does not load."
    )
    print("Save that pasted URL to a file (it carries a one-time code), then finish with:")
    print(finish)


def _finish(args, base: str) -> None:
    flows = _load()
    flow_id = args.flow or max(flows, key=lambda key: flows[key]["expiresAt"], default=None)
    if flow_id is None:
        raise SignInError(f"no Gemini sign-in is waiting on this machine; {START_AGAIN}")
    remembered = flows.get(flow_id)
    if remembered and remembered["expiresAt"] <= time.time():
        raise SignInError(f"the Gemini sign-in link expired at {_eastern_time(remembered['expiresAt'])}; {START_AGAIN}")
    pasted = _read_pasted(args.callback_file)
    reply = retrying(
        lambda: request(base, "/api/oauth/manual-callback", body={"flowId": flow_id, "callbackUrl": pasted}),
        "finish sign-in",
        args.timeout,
    )
    if not isinstance(reply, dict) or reply.get("status") != "success":
        message = reply.get("errorMessage") if isinstance(reply, dict) else None
        raise SignInError(f"agent-lb did not accept the pasted URL: {_refusal(message, pasted)}")
    rows = retrying(lambda: _gemini_accounts(base), "account lookup", args.timeout)
    baseline = set(remembered["accountIds"]) if remembered else None
    new = [row for row in rows if baseline is not None and row.get("accountId") not in baseline]
    row = new[0] if new else max(rows, key=lambda item: str(item.get("lastRefreshAt") or ""), default=None)
    flows.pop(flow_id, None)
    _save(flows)
    if row is None:
        raise SignInError(
            "sign-in finished but agent-lb lists no Gemini account; check `agent-lb status --provider gemini`"
        )
    added = bool(new) if baseline is not None else None
    result = {"email": row.get("email"), "status": row.get("status"), "added": added}
    if args.json:
        print(json.dumps(result))
        return
    active = sum(1 for item in rows if item.get("status") == "active")
    verb = {True: "added", False: "refreshed", None: "signed in"}[added]
    where = "to" if added else "in"
    print(
        f"{verb} {result['email']} {where} the gemini pool: {result['status']} ({len(rows)} accounts, {active} active)"
    )


def _read_pasted(path: str) -> str:
    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        raise SignInError("could not read the callback file; check its path and permissions") from None
    text = text.strip()
    if not text:
        raise SignInError("the callback file is empty; paste the full address-bar URL into it and rerun")
    return text


def _refusal(message: object, pasted: str) -> str:
    parts = urlsplit(pasted)
    values = {value for _, value in parse_qsl(f"{parts.query}&{parts.fragment}") if len(value) >= 4}
    if not isinstance(message, str) or not message or message.startswith("OAuth error:"):
        return f"Google did not complete the sign-in (denied, cancelled or cut short); {START_AGAIN}"
    if pasted in message or any(value in message for value in values):
        return f"the sign-in failed; {START_AGAIN}"
    if message.startswith("Invalid OAuth callback"):
        return (
            "it does not belong to this sign-in (an old or partial URL, or agent-lb restarted since the link was made); "
            f"copy the full URL again, or {START_AGAIN}"
        )
    return f"{message}; {START_AGAIN}"


def _gemini_accounts(base: str) -> list[dict]:
    response = request(base, "/api/accounts")
    if not isinstance(response, dict) or not isinstance(response.get("accounts"), list):
        raise ServiceError("agent-lb returned a malformed accounts response")
    return [row for row in response["accounts"] if isinstance(row, dict) and row.get("provider") == PROVIDER]


def _load() -> dict[str, dict]:
    try:
        data = json.loads(flow_path().read_text())
        flows = data["flows"] if data.get("version") == 1 else {}
        return {
            key: row
            for key, row in flows.items()
            if isinstance(row, dict)
            and isinstance(row.get("expiresAt"), int | float)
            and isinstance(row.get("accountIds"), list)
        }
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return {}


def _save(flows: dict[str, dict]) -> None:
    path = flow_path()
    kept = {key: row for key, row in flows.items() if row["expiresAt"] > time.time() - KEEP_EXPIRED}
    if not kept:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".add-account-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"version": 1, "flows": kept}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _eastern_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, ZoneInfo("America/New_York")).strftime("%-I:%M %p ET")


def _minutes_left(timestamp: float) -> str:
    minutes = max(0, round((timestamp - time.time()) / 60))
    return f"{minutes} minute{'s' if minutes != 1 else ''} left"
