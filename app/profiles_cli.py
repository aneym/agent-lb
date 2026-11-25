from __future__ import annotations

import json
import math
import os
import sys
import time
from urllib.parse import quote, urlencode

from app.core.browser_profiles import BrowserProfile, add_profile, load_profiles, profile_for, user_data_dir
from app.core.cdp_page import close_browser
from app.core.profile_launch import LaunchError, chrome_argv, launch_profile
from app.core.profile_lock import profile_held
from app.core.profile_theme import apply_theme, display_name, pick_color, theme_color
from app.lb_service import (
    ServiceError,
    ServiceUnavailable,
    account_for,
    cdp_ready,
    close_stale_callbacks,
    request,
    retrying,
    send_code,
    shown_code,
)
from app.status_cli import DEFAULT_BASE_URL, StatusObservationError, validated_base_url

PROVIDERS = ("anthropic", "openai")
SKIPPED_PROVIDERS = ("openai",)


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
    adding.add_argument("--login", choices=("google", "email"), help="How the account signs in to claude.ai.")
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
    reauth.add_argument("--auto", action="store_true", help="Reuse the profile's session; never type or page.")


def _profile(email: str) -> BrowserProfile:
    profile = profile_for(email)
    if profile is None:
        print(f"no browser profile for {email}; run: agent-lb profiles add {email} --dir <dir>", file=sys.stderr)
        raise SystemExit(2)
    return profile


def run_profiles(args) -> None:
    try:
        if args.profiles_command == "list":
            rows = [
                {
                    "email": profile.email,
                    "dir": profile.dir,
                    "providers": profile.providers,
                    "cdpPort": profile.cdp_port,
                    "login": profile.login,
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
    except LaunchError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
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
    profile = add_profile(email, dir=args.dir, providers=args.provider, label=args.label, color=color, login=args.login)
    name = display_name(color, profile.label or profile.email, profile.providers)
    data = user_data_dir(profile)
    if cdp_ready(f"http://127.0.0.1:{profile.cdp_port}"):
        print(f"warning: {profile.email} Chrome is running; close it and rerun to apply {name!r}", file=sys.stderr)
    else:
        apply_theme(data, color, name)
    print(f"registered {profile.email} as {name!r} (profile {profile.dir}, CDP {profile.cdp_port})")


def _open_browser(profile: BrowserProfile, url: str, cdp: str) -> tuple[bool, bool]:
    if cdp_ready(cdp):
        request(cdp, "/json/new?" + quote(url, safe=""), method="PUT", cdp=True)
        return True, False
    launch_profile(profile, url)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if cdp_ready(cdp):
            return True, True
        time.sleep(0.25)
    print(f"close the {profile.email} Chrome window and rerun", file=sys.stderr)
    return False, True


def _wait(args, profile: BrowserProfile, provider: str, base: str, flow: dict, cdp: str, debug: bool) -> None:
    started = time.monotonic()
    sent = False
    prompted = False
    down = False
    while time.monotonic() - started < args.timeout:
        try:
            if not sent and debug and provider == "anthropic" and flow.get("callbackUrl"):
                shown = shown_code(profile.cdp_port, flow)
                sent = bool(shown) and send_code(base, flow, shown)
                shown = None
            status = request(base, "/api/oauth/status?" + urlencode({"flowId": flow["flowId"]}))
            if status.get("status") == "success":
                account = retrying(lambda: account_for(base, profile.email, provider), "account lookup")
                if account is None:
                    raise ServiceError(f"signed-in account did not match {profile.email}")
                result = {"email": profile.email, "status": account.get("status")}
                print(json.dumps(result) if args.json else f"{profile.email}: {result['status']}")
                if result["status"] != "active":
                    raise SystemExit(1)
                return
            if status.get("status") == "error":
                raise ServiceError(status.get("errorMessage") or "OAuth failed")
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
    with profile_held(profile.email) as free:
        if not free:
            print(
                f"another reauth, recard or auto run is using the {profile.email} profile; rerun shortly",
                file=sys.stderr,
            )
            raise SystemExit(4)
        _reauth(args, profile)


def _reauth(args, profile: BrowserProfile) -> None:
    try:
        provider = args.provider or (profile.providers[0] if len(profile.providers) == 1 else None)
        base = ""
        if not args.dry_run:
            base = validated_base_url(args.base_url or os.getenv("AGENT_LB_BASE_URL", DEFAULT_BASE_URL))
            if provider is None:
                account = retrying(lambda: account_for(base, profile.email), "account lookup")
                provider = account.get("provider") if account else None
        if provider in SKIPPED_PROVIDERS:
            result = {"email": profile.email, "status": "skipped", "reason": f"{provider} accounts are not reauthed"}
            print(json.dumps(result) if args.json else f"{profile.email}: skipped ({result['reason']})")
            return
        if provider not in PROVIDERS:
            raise ServiceError("choose --provider anthropic or openai; dry-run cannot query account providers")
        body = {"provider": provider, "forceMethod": "browser"}
        if not args.dry_run:
            account = retrying(lambda: account_for(base, profile.email, provider), "account lookup")
            if account and account.get("accountId"):
                body["expectedAccountId"] = account["accountId"]
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
        flow = retrying(lambda: request(base, "/api/oauth/start", body=body), "oauth start")
        if not isinstance(flow, dict) or not flow.get("flowId") or not flow.get("authorizationUrl"):
            raise ServiceError("service returned an incomplete browser OAuth flow")
        cdp = f"http://127.0.0.1:{profile.cdp_port}"
        if flow.get("callbackUrl"):
            close_stale_callbacks(cdp, flow["callbackUrl"])
        debug, launched = _open_browser(profile, flow["authorizationUrl"], cdp)
        _wait(args, profile, provider, base, flow, cdp, debug)
        if launched:
            close_browser(profile.cdp_port)
    except (ServiceError, StatusObservationError, LaunchError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        print("reauth failed; check Chrome and the service response", file=sys.stderr)
        raise SystemExit(1) from None
