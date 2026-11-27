from __future__ import annotations

import json
import math
import os
import sys
from urllib.parse import quote

from app.core.browser_profiles import BrowserProfile, add_profile, load_profiles, profile_for, user_data_dir
from app.core.profile_launch import LaunchError, chrome_argv, launch_profile, registered_profile_dir
from app.core.profile_lock import profile_held
from app.core.profile_theme import apply_theme, display_name, pick_color, theme_color
from app.lb_service import (
    ServiceError,
    account_for,
    retrying,
)
from app.status_cli import DEFAULT_BASE_URL, StatusObservationError, validated_base_url

PROVIDERS = ("anthropic", "openai")


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
    reauth = subparsers.add_parser("reauth", help="Print the account's server reauthentication page.")
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
    try:
        data = registered_profile_dir(profile, profile.providers[0] if profile.providers else "")
        chrome_argv(profile, "about:blank")
        apply_theme(data, color, name)
    except LaunchError:
        pass
    print(f"registered {profile.email} as {name!r} (profile {profile.dir}, CDP {profile.cdp_port})")


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
        if provider not in PROVIDERS:
            raise ServiceError("choose --provider anthropic or openai; dry-run cannot query account providers")
        if args.dry_run:
            print(json.dumps({"reauthPath": "/reauth/<accountId>", "provider": provider, "localBrowser": False}))
            return
        account = retrying(lambda: account_for(base, profile.email, provider), "account lookup")
        account_id = account.get("accountId") if account else None
        if not account_id:
            raise ServiceError("could not find an existing account for the requested provider and profile")
        dashboard = os.getenv("AGENT_LB_DASHBOARD_URL", "https://ax42.tailf266ac.ts.net:8455").rstrip("/")
        url = f"{dashboard}/reauth/{quote(account_id, safe='')}"
        print(json.dumps({"reauthUrl": url}) if args.json else f"Open {url} to sign in on ax42.")
    except (ServiceError, StatusObservationError, LaunchError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        print("reauth failed; check Chrome and the service response", file=sys.stderr)
        raise SystemExit(1) from None
