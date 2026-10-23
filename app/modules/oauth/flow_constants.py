from __future__ import annotations

_TERMINAL_OAUTH_STATUSES = {"error", "success"}
_PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS = 15 * 60
_REAUTH_SEAT_MISMATCH_MESSAGE = "The account you signed in as is not the one being re-authenticated. No changes were made. Sign out of ChatGPT (or use a private window), then re-run reauthentication and log in as the exact account that needs repair."
