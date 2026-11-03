from shell_vocabulary import KEYWORDS, WRAPPERS  # type: ignore[missing-import]


def is_assignment(word):
    name, eq, _ = word[3].partition("=")
    return bool(eq) and name.rstrip("+").isidentifier()


def base(word):
    return word[0].rsplit("/", 1)[-1]


def split_string(words, k):
    w = words[k][0]
    if w.startswith("--split-string="):
        attached = w.split("=", 1)[1]
    elif w == "--split-string" or (w[:1] == "-" and w[1:2] != "-" and "S" in w):
        attached = w[w.find("S") + 1 :] if w[1:2] != "-" else ""
    else:
        return None
    rest = words[k + 1 :] if attached else words[k + 2 :]
    text = attached or (words[k + 1][0] if k + 1 < len(words) else "")
    return " ".join([text, *(word[3] for word in rest)])


def command_word(words, strings):
    k = 0
    while k < len(words) and (words[k][0] in KEYWORDS or is_assignment(words[k])):
        k += 1
    while k < len(words) and base(words[k]) in WRAPPERS:
        name = base(words[k])
        takes, duration, k = WRAPPERS[name], name in ("timeout", "gtimeout"), k + 1
        while k < len(words):
            w = words[k][0]
            split = split_string(words, k) if name == "env" else None
            if split is not None:
                strings.append(split)
                return None
            if w.startswith("-") and len(w) > 1:
                k += 2 if w in takes else 1
            elif is_assignment(words[k]):
                k += 1
            elif duration:
                duration, k = False, k + 1
            else:
                break
    return k if k < len(words) else None
