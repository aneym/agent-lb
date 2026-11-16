from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from app.core.browser_profiles import BrowserProfile, add_profile, load_profiles, profile_for, user_data_dir
from app.core.cdp_page import close_browser
from app.core.profile_theme import apply_theme, display_name, pick_color, theme_color
from app.status_cli import DEFAULT_BASE_URL, StatusObservationError, _NoRedirect, _validated_base_url

PROVIDERS = ("anthropic", "openai")
SKIPPED_PROVIDERS = ("openai",)
SERVICE_TIMEOUT = 30
RETRY_WINDOW = 300


class ProfilesError(Exception):
    pass


class ServiceUnavailable(ProfilesError):
    pass


def add_parser(subparsers) -> None:
    profiles = subparsers.add_parser("profiles", help="Manage per-account Chrome profiles.")
    commands = profiles.add_subparsers(dest="profiles_command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    adding = commands.add_parser("add")
    adding.add_argument("email")
    adding.add_argument("--dir")
    adding.add_argument("--provider", choices=PROVIDERS, nargs="+", action="extend", default=[])
    adding.add_argument("--label")
    opening = commands.add_parser("open")
    opening.add_argument("email")
    reauth = subparsers.add_parser("reauth", help="Reauthenticate in an account's Chrome profile.")
    reauth.add_argument("email")
    reauth.add_argument("--provider", choices=PROVIDERS)
    reauth.add_argument("--timeout", type=float, default=600)
    reauth.add_argument("--base-url")
    reauth.add_argument("--dry-run", action="store_true")
    reauth.add_argument("--json", action="store_true")
    reauth.add_argument("--warmup-hours", type=float)
    reauth.add_argument("--warmup-capacity-percent", type=float, default=15.0)
    reauth.add_argument("--warmup-max-concurrent", type=int, default=2)


def chrome_argv(profile: BrowserProfile, url: str) -> list[str]:
    return [
        os.getenv("AGENT_LB_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        f"--user-data-dir={user_data_dir(profile)}",
        f"--remote-debugging-port={profile.cdp_port}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
    ]


def _profile(email: str) -> BrowserProfile:
    profile = profile_for(email)
    if profile is None:
        print(f"no browser profile for {email}; run: agent-lb profiles add {email} --dir <dir>", file=sys.stderr)
        raise SystemExit(2)
    return profile


def launch_profile(profile: BrowserProfile, url: str) -> None:
    subprocess.Popen(
        chrome_argv(profile, url), start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def run_profiles(args) -> None:
    try:
        if args.profiles_command == "list":
            rows = [
                {
                    "email": profile.email,
                    "dir": profile.dir,
                    "providers": profile.providers,
                    "cdpPort": profile.cdp_port,
                    "exists": user_data_dir(profile).is_dir(),
                }
                for profile in load_profiles().values()
            ]
            if args.json:
                print(json.dumps(rows))
            else:
                for row in rows:
                    print(
                        f"{row['email']}\t{row['dir']}\t{','.join(row['providers'])}\t{row['cdpPort']}\t{row['exists']}"
                    )
        elif args.profiles_command == "add":
            _add(args)
        else:
            launch_profile(_profile(args.email), "about:blank")
    except (OSError, ValueError) as exc:
        print(f"profiles: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


def _add(args) -> None:
    email = args.email.strip().lower()
    others = [profile for key, profile in load_profiles().items() if key != email]
    previous = profile_for(email)
    current = (previous.color if previous else None) or (theme_color(user_data_dir(previous)) if previous else None)
    used = [profile.color or theme_color(user_data_dir(profile)) for profile in others]
    color = pick_color(current, used)
    profile = add_profile(email, dir=args.dir, providers=args.provider, label=args.label, color=color)
    name = display_name(color, profile.label or profile.email, profile.providers)
    data = user_data_dir(profile)
    if _cdp_ready(f"http://127.0.0.1:{profile.cdp_port}"):
        print(f"warning: {profile.email} Chrome is running; close it and rerun to apply {name!r}", file=sys.stderr)
    else:
        apply_theme(data, color, name)
    print(f"registered {profile.email} as {name!r} (profile {profile.dir}, CDP {profile.cdp_port})")


def _request(base: str, path: str, *, body: dict | None = None, method: str | None = None, cdp: bool = False) -> Any:
    headers = {"Accept": "application/json"}
    if not cdp and (cookie := os.getenv("AGENT_LB_STATUS_COOKIE")):
        headers["Cookie"] = cookie
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method or ("POST" if body is not None else "GET"),
    )
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(
            request, timeout=2 if cdp else SERVICE_TIMEOUT
        ) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 409 and not cdp:
            try:
                error = json.load(exc).get("error", {})
                if error.get("code") == "oauth_owner_required":
                    raise ProfilesError(error.get("message", "OAuth must run on the owner instance")) from None
            except (ValueError, AttributeError):
                pass
        if exc.code in (429, 502, 503, 504):
            raise ServiceUnavailable(f"service returned HTTP {exc.code}") from None
        raise ProfilesError(f"service returned HTTP {exc.code}") from None
    except (URLError, OSError, ValueError):
        raise ServiceUnavailable("service unavailable or returned invalid JSON") from None


def _retrying(call, what: str):
    deadline = time.monotonic() + RETRY_WINDOW
    delay = 1.0
    while True:
        try:
            return call()
        except ServiceUnavailable as exc:
            if time.monotonic() + delay > deadline:
                raise
            print(f"{what}: {exc}; retrying", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 15)


def _cdp_ready(base: str) -> bool:
    try:
        return isinstance(_request(base, "/json/version", cdp=True), dict)
    except ProfilesError:
        return False


def _open_browser(profile: BrowserProfile, url: str, cdp: str) -> tuple[bool, bool]:
    if _cdp_ready(cdp):
        _request(cdp, "/json/new?" + quote(url, safe=""), method="PUT", cdp=True)
        return True, False
    launch_profile(profile, url)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _cdp_ready(cdp):
            return True, True
        time.sleep(0.25)
    print(f"close the {profile.email} Chrome window and rerun", file=sys.stderr)
    return False, True


def _account(base: str, email: str, provider: str | None = None) -> dict | None:
    response = _request(base, "/api/accounts")
    if not isinstance(response, dict) or not isinstance(response.get("accounts"), list):
        raise ProfilesError("service returned a malformed accounts response")
    return next(
        (
            row
            for row in response["accounts"]
            if row.get("email", "").lower() == email and (provider is None or row.get("provider") == provider)
        ),
        None,
    )


def _oauth_state(url: str) -> str | None:
    return (parse_qs(urlsplit(url).query).get("state") or [None])[0]


def _callback_pages(cdp: str, prefix: str) -> list[dict]:
    try:
        pages = _request(cdp, "/json/list", cdp=True)
    except ProfilesError:
        return []
    if not isinstance(pages, list):
        return []
    return [
        page
        for page in pages
        if isinstance(page, dict) and page.get("type") == "page" and str(page.get("url", "")).startswith(prefix)
    ]


def _close_page(cdp: str, page: dict) -> None:
    try:
        _request(cdp, "/json/close/" + quote(str(page.get("id", "")), safe=""), cdp=True)
    except ProfilesError:
        pass


def _close_stale_callbacks(cdp: str, prefix: str) -> None:
    for page in _callback_pages(cdp, prefix):
        _close_page(cdp, page)


def _send_callback(base: str, flow: dict, cdp: str, handled: set[str]) -> None:
    expected = _oauth_state(flow["authorizationUrl"])
    for page in _callback_pages(cdp, flow["callbackUrl"]):
        url = page["url"]
        query = parse_qs(urlsplit(url).query)
        if url in handled or not query.get("code"):
            continue
        if expected is None or (query.get("state") or [None])[0] != expected:
            handled.add(url)
            _close_page(cdp, page)
            continue
        try:
            reply = _request(base, "/api/oauth/manual-callback", body={"flowId": flow["flowId"], "callbackUrl": url})
        except ServiceUnavailable:
            return
        except ProfilesError as exc:
            reply = {"status": "error", "errorMessage": str(exc)}
        if isinstance(reply, dict) and reply.get("status") == "error":
            print(f"callback not accepted ({reply.get('errorMessage')}); watching for a new one", file=sys.stderr)
        handled.add(url)


def _wait(args, profile: BrowserProfile, provider: str, base: str, flow: dict, cdp: str, debug: bool) -> None:
    started = time.monotonic()
    handled: set[str] = set()
    prompted = False
    down = False
    while time.monotonic() - started < args.timeout:
        try:
            if debug and provider == "anthropic" and flow.get("callbackUrl"):
                _send_callback(base, flow, cdp, handled)
            status = _request(base, "/api/oauth/status?" + urlencode({"flowId": flow["flowId"]}))
            if status.get("status") == "success":
                account = _retrying(lambda: _account(base, profile.email, provider), "account lookup")
                if account is None:
                    raise ProfilesError(f"signed-in account did not match {profile.email}")
                result = {"email": profile.email, "status": account.get("status")}
                print(json.dumps(result) if args.json else f"{profile.email}: {result['status']}")
                if result["status"] != "active":
                    raise SystemExit(1)
                return
            if status.get("status") == "error":
                raise ProfilesError(status.get("errorMessage") or "OAuth failed")
            if down:
                print("agent-lb reachable again", file=sys.stderr)
                down = False
        except ServiceUnavailable as exc:
            if not down:
                print(f"agent-lb unreachable ({exc}); retrying until the timeout", file=sys.stderr)
                down = True
        if not prompted and time.monotonic() - started >= 15:
            print(f"sign-in needed in the {profile.email} window; waiting up to {args.timeout:g}s", file=sys.stderr)
            prompted = True
        time.sleep(min(2, max(0, args.timeout - (time.monotonic() - started))))
    print(f"still pending; finish sign-in and rerun agent-lb reauth {profile.email}", file=sys.stderr)
    raise SystemExit(3)


def run_reauth(args) -> None:
    profile = _profile(args.email)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print("--timeout must be finite and greater than zero", file=sys.stderr)
        raise SystemExit(2)
    try:
        provider = args.provider or (profile.providers[0] if len(profile.providers) == 1 else None)
        base = ""
        if not args.dry_run:
            base = _validated_base_url(args.base_url or os.getenv("AGENT_LB_BASE_URL", DEFAULT_BASE_URL))
            if provider is None:
                account = _retrying(lambda: _account(base, profile.email), "account lookup")
                provider = account.get("provider") if account else None
        if provider in SKIPPED_PROVIDERS:
            result = {"email": profile.email, "status": "skipped", "reason": f"{provider} accounts are not reauthed"}
            print(json.dumps(result) if args.json else f"{profile.email}: skipped ({result['reason']})")
            return
        if provider not in PROVIDERS:
            raise ProfilesError("choose --provider anthropic or openai; dry-run cannot query account providers")
        body = {"provider": provider, "forceMethod": "browser"}
        if args.warmup_hours is not None:
            body.update(
                {
                    "warmupHours": args.warmup_hours,
                    "warmupCapacityPercent": args.warmup_capacity_percent,
                    "warmupMaxConcurrent": args.warmup_max_concurrent,
                }
            )
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "start": {"method": "POST", "path": "/api/oauth/start", "body": body},
                        "chrome_argv": chrome_argv(profile, "<authorizationUrl>"),
                        "cdp": f"127.0.0.1:{profile.cdp_port}",
                        "complete": "manual-callback via CDP" if provider == "anthropic" else "status poll",
                    }
                )
            )
            return
        flow = _retrying(lambda: _request(base, "/api/oauth/start", body=body), "oauth start")
        if not isinstance(flow, dict) or not flow.get("flowId") or not flow.get("authorizationUrl"):
            raise ProfilesError("service returned an incomplete browser OAuth flow")
        cdp = f"http://127.0.0.1:{profile.cdp_port}"
        if flow.get("callbackUrl"):
            _close_stale_callbacks(cdp, flow["callbackUrl"])
        debug, launched = _open_browser(profile, flow["authorizationUrl"], cdp)
        _wait(args, profile, provider, base, flow, cdp, debug)
        if launched:
            close_browser(profile.cdp_port)
    except (ProfilesError, StatusObservationError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        print("reauth failed; check Chrome and the service response", file=sys.stderr)
        raise SystemExit(1) from None
