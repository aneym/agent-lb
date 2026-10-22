from __future__ import annotations

import base64
import binascii
import re


def secret_patterns(secret: str) -> list[bytes]:

    raw = secret.encode()
    patterns = {raw}
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        for shift, skip in ((0, 0), (1, 2), (2, 3)):
            encoded = encode(b"\0" * shift + raw).rstrip(b"=")

            whole = ((shift + len(raw)) * 8) // 6

            if encoded[skip:whole]:
                patterns.add(encoded[skip:whole])
    return sorted(p for p in patterns if p)


FERNET_FRAGMENT = re.compile(rb"(?=(gAAAAA[A-Za-z0-9_-]{50}))")


FERNET_FRAGMENT_CHARS = 56


B64_RUN = re.compile(rb"[A-Za-z0-9+/_\-\r\n]{75,}")


B64_FRAGMENT_SPAN = 96


B64_STANDARD = bytes.maketrans(b"-_", b"+/")


B64_CHAR = rb"[A-Za-z0-9+/=_-]"


WRAP_BREAK = re.compile(rb"(?<=" + B64_CHAR + rb")[ \t]*+\r?\n[ \t]*+(?=" + B64_CHAR + rb")")


WRAP_MIN_LINE = 16


WRAP_MAX_SEPARATOR = 8


def wrapped_span(length: int) -> int:

    return length + (length // WRAP_MIN_LINE + 1) * WRAP_MAX_SEPARATOR


def base64_decodings(buf: bytes):

    for match in B64_RUN.finditer(buf):
        text = match.group(0).translate(B64_STANDARD, b"\r\n")
        for offset in range(4):
            piece = text[offset:]
            piece = piece[: len(piece) - len(piece) % 4]
            if len(piece) >= 4:
                yield binascii.a2b_base64(piece)


def plausible_plaintext(block: bytes) -> bool:

    pad = block[-1]
    body = block[:-pad] if 1 <= pad <= 16 and block.endswith(bytes([pad]) * pad) else block
    return bool(body) and all(0x20 <= b < 0x7F for b in body)


class Detector:
    def __init__(self, secrets: list[str], keys: list[str] | None = None) -> None:
        self.patterns = sorted({p for s in secrets if s for p in secret_patterns(s)})
        self.decryptors = []
        for key in keys or []:
            if not key:
                continue
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            raw = base64.urlsafe_b64decode(key.encode() if isinstance(key, str) else key)
            self.decryptors.append(Cipher(algorithms.AES(raw[16:]), modes.ECB()).decryptor())

        lengths = [wrapped_span(len(p)) for p in self.patterns]
        if self.decryptors:
            lengths.append(wrapped_span(B64_FRAGMENT_SPAN))
        self.overlap = max(lengths, default=1) - 1

    def __bool__(self) -> bool:
        return bool(self.patterns)

    def opens(self, fragment: bytes) -> bool:
        raw = base64.urlsafe_b64decode(fragment[:FERNET_FRAGMENT_CHARS])
        iv, first = raw[9:25], raw[25:41]
        return any(
            plausible_plaintext(bytes(a ^ b for a, b in zip(dec.update(first), iv, strict=True)))
            for dec in self.decryptors
        )

    def keyed(self, buf: bytes) -> bool:
        return b"gAAAAA" in buf and any(self.opens(m.group(1)) for m in FERNET_FRAGMENT.finditer(buf))

    @staticmethod
    def unwrapped(buf: bytes) -> bytes | None:

        if b"\n" not in buf:
            return None
        joined = WRAP_BREAK.sub(b"", buf)
        return joined if len(joined) != len(buf) else None

    def hit(self, buf: bytes) -> bool:
        if any(p in buf for p in self.patterns):
            return True
        joined = self.unwrapped(buf)

        if joined is not None and any(p in joined for p in self.patterns):
            return True
        if not self.decryptors:
            return False

        for view in (buf,) if joined is None else (buf, joined):
            if self.keyed(view) or any(self.keyed(decoded) for decoded in base64_decodings(view)):
                return True
        return False


def scan_stream(read, detector: Detector, write=None, chunk: int = 4 << 20) -> tuple[bool, int]:

    tail, total = b"", 0
    while True:
        block = read(chunk)
        if not block:
            return False, total
        total += len(block)
        buf = tail + block
        if detector.hit(buf):
            return True, total
        if write is not None:
            write(block)
        tail = buf[-detector.overlap :] if detector.overlap > 0 else b""
