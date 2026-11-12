from __future__ import annotations

import json
import subprocess
import sys
import time

from websockets.exceptions import WebSocketException

from app.core.browser_profiles import profile_for
from app.core.cdp_page import CdpError, CdpPage, cdp_up
from app.core.claude_billing import BILLING_URL, SIGNED_IN_OR_LOGIN, claude_tab
from app.profiles_cli import launch_profile

PLACE = {"left": 80, "top": 60, "width": 1280, "height": 1000}

_PLACE_CHECK = """ObjC.import("AppKit"); ObjC.import("CoreGraphics");
const pid = %d;
$.NSRunningApplication.runningApplicationWithProcessIdentifier(pid).activateWithOptions(3);
delay(0.8);
const primary = ObjC.deepUnwrap($.NSScreen.screens.objectAtIndex(0).frame).size;
const all = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly, 0)));
const windows = all.filter(w => w.kCGWindowOwnerPID === pid && w.kCGWindowLayer === 0).map(w => w.kCGWindowBounds);
const onMain = windows.some(b => b.X >= 0 && b.Y >= 0 && b.X + b.Width <= primary.width && b.Y + b.Height <= primary.height);
JSON.stringify({frontmost: $.NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier === pid, onScreen: onMain,
  windows: windows, display: primary})"""


def _launch(email: str) -> int | None:
    profile = profile_for(email)
    if profile is None:
        return None
    if not cdp_up(profile.cdp_port):
        launch_profile(profile, BILLING_URL)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not (cdp_up(profile.cdp_port) and claude_tab(profile.cdp_port)):
            time.sleep(0.5)
    return profile.cdp_port


def _placement(page: CdpPage) -> dict:
    if sys.platform != "darwin":
        return {"frontmost": None, "onScreen": None}
    try:
        processes = page.call("SystemInfo.getProcessInfo").get("processInfo", [])
        pid = next(int(row["id"]) for row in processes if row.get("type") == "browser")
        script = _PLACE_CHECK % pid
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script], capture_output=True, text=True, timeout=15
        )
        return json.loads(result.stdout)
    except (CdpError, StopIteration, KeyError, ValueError, OSError, subprocess.TimeoutExpired):
        return {"frontmost": None, "onScreen": None}


def _placed(bounds: dict) -> bool:
    return bounds.get("X") == PLACE["left"] and bounds.get("Y") == PLACE["top"]


def open_billing_window(email: str) -> dict:
    started = time.monotonic()
    try:
        port = _launch(email)
        if port is None:
            return {"open": False, "detail": f"no browser profile for {email}"}
        tab = claude_tab(port)
        page = CdpPage(port, target_id=tab["id"]) if tab else CdpPage(port, BILLING_URL, new_window=True)
    except (CdpError, OSError, WebSocketException) as exc:
        return {"open": False, "detail": str(exc) or type(exc).__name__}
    report: dict = {"open": True, "reused": bool(tab), "port": port}
    try:
        window = page.call("Browser.getWindowForTarget", {"targetId": page.target_id})["windowId"]
        page.call("Browser.setWindowBounds", {"windowId": window, "bounds": {"windowState": "normal"}})
        page.call("Browser.setWindowBounds", {"windowId": window, "bounds": PLACE})
        page.call("Page.bringToFront", session=page.session)
        page.wait_for("document.readyState === 'complete'", seconds=20)
        for _ in range(4):
            page.call("Browser.setWindowBounds", {"windowId": window, "bounds": PLACE})
            report.update(_placement(page))
            if report.get("onScreen") is None or any(_placed(bounds) for bounds in report.get("windows", [])):
                break
            time.sleep(0.5)
        signed = page.wait_for(SIGNED_IN_OR_LOGIN, seconds=8)
        report["signedIn"] = signed if signed and signed != "-" else None
        report["page"] = page.evaluate("location.origin + location.pathname")
    except (CdpError, OSError, WebSocketException, KeyError, TypeError) as exc:
        report["detail"] = str(exc) or type(exc).__name__
    finally:
        page.detach()
    report["seconds"] = round(time.monotonic() - started, 1)
    return report
