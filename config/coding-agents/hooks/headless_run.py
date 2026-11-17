import os

CHILD_THREAD = "thread:delegated-task:"
NOBODY = "No person is watching this delegated run, so nothing in it can be approved and this call was refused instead."
ROUTE = (
    "Do it another way: write file contents with the Write or Edit tool instead of a shell heredoc, "
    "split the command into simpler ones, or report the blocker to your lead."
)


def headless(env=None):
    return (os.environ if env is None else env).get("T3_THREAD_ID", "").startswith(CHILD_THREAD)


def refusal(reason=""):
    said = reason.strip() if isinstance(reason, str) else ""
    if said and said[-1] not in ".!?":
        said += "."
    return " ".join(part for part in (NOBODY, f"The check said: {said}" if said else "", ROUTE) if part)


def settled(specific):
    if not isinstance(specific, dict) or specific.get("permissionDecision") != "ask" or not headless():
        return specific
    return {
        **specific,
        "permissionDecision": "deny",
        "permissionDecisionReason": refusal(specific.get("permissionDecisionReason") or ""),
    }
