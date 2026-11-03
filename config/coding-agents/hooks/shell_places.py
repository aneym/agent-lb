import os

import data_delete  # type: ignore[missing-import]
from shell_vocabulary import COMPOUND  # type: ignore[missing-import]

MOVES = {"cd", "pushd", "popd"}
CONDITIONAL = {"&&", "||"}
PIPES = {"|", "|&"}


def links(notes, every):
    if len(notes["segs"]) != len(every):
        return [("", "", True)] * len(every)
    found = []
    for meta, seg in zip(notes["segs"], every):
        nested = meta["depth"] or meta["compound"] or meta["brace"] or notes["parents"][meta["group"]] is not None
        alone = PIPES.isdisjoint({meta["start"], meta["end"]}) and meta["end"] != "&" and seg[0][3] not in COMPOUND
        found.append((meta["start"], meta["end"], bool(nested) or not alone))
    return found


def moved(name, words, assignments, cwd):
    if name not in MOVES:
        return cwd
    after = words[words.index(name) + 1 :] if name in words else []
    if "-" in after:
        return None
    rest = [word for word in after if not word.startswith("-")]
    if name == "popd" or len(rest) != 1:
        return None if name == "popd" or rest else os.path.expanduser("~")
    return data_delete.located(rest[0], assignments, cwd)


def reached(state, start):
    cwds, fallback = state
    return state if start == "&&" else (cwds | fallback, set())


def places(name, words, assignments, state, link):
    cwds, fallback = state
    start, end, inner = link
    if name not in MOVES:
        return state
    new = {moved(name, words, assignments, cwd) for cwd in cwds}
    if inner or start == "||":
        cwds = cwds | new
    elif start == "&&" or end in CONDITIONAL:
        fallback, cwds = fallback | cwds, new
    else:
        cwds = new
    return (cwds, fallback) if len(cwds | fallback) <= 16 else ({None}, set())
