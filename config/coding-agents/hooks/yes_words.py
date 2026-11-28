import re

COVER = (
    "Cite his turn only when his words cover this act. If they don't, ask him one plain question; his plain yes "
    "settles it. Never ask him to repeat his words."
)
REFUSE = re.compile(
    r"\b(?:no|nope|nah|don['’]?t|do\s+not|stop|wait|hold\s+on|not|never|cancel|later)\b|\?",
    re.IGNORECASE,
)
HEDGE = re.compile(r"\b(?:but|only|nothing|none|except|keep|leave)\b", re.IGNORECASE)
PRESSED = re.compile(r"^\W*(?:yes|allow)\b", re.IGNORECASE)


def refused(reply):
    return not (reply or "").strip() or REFUSE.search(reply) is not None


def pressed_clear(option):
    return not refused(option) and HEDGE.search(option or "") is None


def pressed_yes(option):
    return PRESSED.match(option or "") is not None and pressed_clear(option)
