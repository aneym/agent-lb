import hashlib

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    k = (y1 - x1) * (y2 - x2) % P
    m = (y1 + x1) * (y2 + x2) % P
    c = 2 * t1 * t2 * D % P
    d = 2 * z1 * z2 % P
    e, f, g, h = m - k, d - c, d + c, m + k
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(scalar, point):
    out = (0, 1, 1, 0)
    while scalar:
        if scalar & 1:
            out = _add(out, point)
        point = _add(point, point)
        scalar >>= 1
    return out


def _x_of(y, sign):
    xx = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P:
        x = x * SQRT_M1 % P
    if (x * x - xx) % P:
        return None
    if x == 0 and sign:
        return None
    return P - x if x & 1 != sign else x


def _decode(data):
    if len(data) != 32:
        return None
    value = int.from_bytes(data, "little")
    y, sign = value & ((1 << 255) - 1), value >> 255
    if y >= P:
        return None
    x = _x_of(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


def _encode(point):
    x, y, z, _ = point
    zi = pow(z, P - 2, P)
    x, y = x * zi % P, y * zi % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


_BY = 4 * pow(5, P - 2, P) % P
_BX = _x_of(_BY, 0) or 0
BASE = (_BX, _BY, 1, _BX * _BY % P)


def verify(public, message, signature):
    if len(signature) != 64:
        return False
    a, r = _decode(public), _decode(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % L
    return _encode(_mul(s, BASE)) == _encode(_add(r, _mul(h, a)))
