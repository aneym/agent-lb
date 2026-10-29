import hashlib
import json
import re


def capped(view):
    remaining, out = 1024, {}
    for key in ("context", "shown"):
        value = view.get(key, "")
        raw = value.encode("utf-8")
        if len(raw) > 1024:
            marker = "\n[hook-context:" + hashlib.sha256(raw).hexdigest()[:12] + "]"
            value = raw[: 1024 - len(marker.encode())].decode("utf-8", "ignore") + marker
        out[key] = value.encode()[:remaining].decode("utf-8", "ignore")
        remaining -= len(out[key].encode())
    return out


def expected_presentation(view, segments, old_root, new_root):

    def moved(value):
        return json.loads(json.dumps(value).replace(old_root, new_root))

    view = moved(view)
    parts = [capped(moved(segment)) if folded else moved(segment) for segment, folded in segments]
    for key in ("context", "shown"):
        view[key] = "\n".join(part[key] for part in parts if part.get(key))
    return view


FLOOR_REFUSAL = re.compile(r"^(\[[^\n]*\]: )hook-dispatch: floor guard refusal$")


def same_effect(old_view, new_view):

    def strip(view):
        return {key: value for key, value in view.items() if key not in ("block", "block_parts", "block_raw")}

    if strip(old_view) != strip(new_view):
        return False
    if old_view["block"] == new_view["block"]:
        return True
    pattern = "\n".join(
        re.escape(match.group(1) + "hook-dispatch: floor guard ") + ".*?" if match else re.escape(part)
        for part, match in ((part, FLOOR_REFUSAL.match(part)) for part in old_view["block_parts"])
    )
    return bool(pattern) and re.fullmatch(pattern, new_view["block_raw"], re.S) is not None
