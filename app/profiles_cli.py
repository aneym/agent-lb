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
from app.status_cli import DEFAULT_BASE_URL, StatusObservationError, _NoRedirect, _validated_base_url

PROVIDERS = ("anthropic", "openai")


class ProfilesError(Exception):
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
    opening = commands.add_parser("open")
    opening.add_argument("email")
    reauth = subparsers.add_parser("reauth", help="Reauthenticate in an account's Chrome profile.")
    reauth.add_argument("email")
    reauth.add_argument("--provider", choices=PROVIDERS)
    reauth.add_argument("--timeout", type=float, default=600)
    reauth.add_argument("--base-url")
    reauth.add_argument("--dry-run", action="store_true")
    reauth.add_argument("--json", action="store_true")


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


def _launch(profile: BrowserProfile, url: str) -> None:
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
            profile = add_profile(args.email, dir=args.dir, providers=args.provider)
            if not user_data_dir(profile).is_dir():
                print(f"warning: profile directory does not exist: {user_data_dir(profile)}", file=sys.stderr)
            print(f"registered {profile.email} (profile {profile.dir}, CDP {profile.cdp_port})")
        else:
            _launch(_profile(args.email), "about:blank")
    except (OSError, ValueError) as exc:
        print(f"profiles: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


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
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=2 if cdp else 10) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 409 and not cdp:
            try:
                error = json.load(exc).get("error", {})
                if error.get("code") == "oauth_owner_required":
                    raise ProfilesError(error.get("message", "OAuth must run on the owner instance")) from None
            except (ValueError, AttributeError):
                pass
        raise ProfilesError(f"service returned HTTP {exc.code}") from None
    except (URLError, OSError, ValueError):
        raise ProfilesError("service unavailable or returned invalid JSON") from None


def _cdp_ready(base: str) -> bool:
    try:
        return isinstance(_request(base, "/json/version", cdp=True), dict)
    except ProfilesError:
        return False


def _open_browser(profile: BrowserProfile, url: str, cdp: str) -> bool:
    if _cdp_ready(cdp):
        _request(cdp, "/json/new?" + quote(url, safe=""), method="PUT", cdp=True)
        return True
    _launch(profile, url)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _cdp_ready(cdp):
            return True
        time.sleep(0.25)
    print(f"close the {profile.email} Chrome window and rerun", file=sys.stderr)
    return False


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


def _wait(args, profile: BrowserProfile, provider: str, base: str, flow: dict, cdp: str, debug: bool) -> None:
    started = time.monotonic()
    sent = False
    prompted = False
    while time.monotonic() - started < args.timeout:
        if debug and provider == "anthropic" and not sent and flow.get("callbackUrl"):
            try:
                pages = _request(cdp, "/json/list", cdp=True)
            except ProfilesError:
                pages = []
            for page in pages:
                url = page.get("url", "")
                if (
                    page.get("type") == "page"
                    and url.startswith(flow["callbackUrl"])
                    and parse_qs(urlsplit(url).query).get("code")
                ):
                    sent = True
                    _request(base, "/api/oauth/manual-callback", body={"flowId": flow["flowId"], "callbackUrl": url})
                    break
        status = _request(base, "/api/oauth/status?" + urlencode({"flowId": flow["flowId"]}))
        if status.get("status") == "success":
            account = _account(base, profile.email, provider)
            if account is None:
                raise ProfilesError(f"signed-in account did not match {profile.email}")
            result = {"email": profile.email, "status": account.get("status")}
            print(json.dumps(result) if args.json else f"{profile.email}: {result['status']}")
            if result["status"] != "active":
                raise SystemExit(1)
            return
        if status.get("status") == "error":
            raise ProfilesError(status.get("errorMessage") or "OAuth failed")
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
                account = _account(base, profile.email)
                provider = account.get("provider") if account else None
        if provider not in PROVIDERS:
            raise ProfilesError("choose --provider anthropic or openai; dry-run cannot query account providers")
        body = {"provider": provider, "forceMethod": "browser"}
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
        flow = _request(base, "/api/oauth/start", body=body)
        if not isinstance(flow, dict) or not flow.get("flowId") or not flow.get("authorizationUrl"):
            raise ProfilesError("service returned an incomplete browser OAuth flow")
        cdp = f"http://127.0.0.1:{profile.cdp_port}"
        debug = _open_browser(profile, flow["authorizationUrl"], cdp)
        _wait(args, profile, provider, base, flow, cdp, debug)
    except (ProfilesError, StatusObservationError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        print("reauth failed; check Chrome and the service response", file=sys.stderr)
        raise SystemExit(1) from None
