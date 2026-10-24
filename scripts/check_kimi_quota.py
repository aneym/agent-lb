#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from urllib.request import urlopen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:2455")
    parser.add_argument("--remaining", type=float, required=True)
    parser.add_argument("--reset", required=True)
    args = parser.parse_args()
    with urlopen(f"{args.base_url}/api/accounts", timeout=15) as response:
        accounts = [account for account in json.load(response)["accounts"] if account["provider"] == "kimi"]
    with urlopen(f"{args.base_url}/api/pools", timeout=15) as response:
        pool = next(pool for pool in json.load(response)["pools"] if pool["id"] == "kimi")
    assert len(accounts) == 1, f"Expected one Kimi account, got {len(accounts)}"
    account = accounts[0]
    observed = {
        "remaining": account["usage"]["primaryRemainingPercent"],
        "reset": account["resetAtPrimary"],
        "poolRemaining": pool["headroomPercent"],
        "poolStatus": pool["status"],
        "usableAccounts": pool["usableAccounts"],
    }
    print(json.dumps(observed, indent=2))
    assert observed["remaining"] == args.remaining, "Account quota differs from measured upstream quota"
    assert observed["reset"] == args.reset, "Account reset differs from measured upstream reset"
    assert observed["poolRemaining"] == args.remaining, "Pool quota differs from account quota"
    if args.remaining == 0:
        assert observed["poolStatus"] == "exhausted", "Exhausted quota still reported usable"
        assert observed["usableAccounts"] == 0, "Exhausted account still admitted by pool"


if __name__ == "__main__":
    main()
