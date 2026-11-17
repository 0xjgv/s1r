"""Verify minisign signatures with the standard library.

Ed25519 verification follows the reference implementation in RFC 8032, section 6.
It only checks public data, so it does not need to run in constant time.
"""

from __future__ import annotations

import base64
import hashlib

p = 2**255 - 19
q = 2**252 + 27742317777372353535851937790883648493
d = -121665 * pow(121666, p - 2, p) % p
SQRT_M1 = pow(2, (p - 1) // 4, p)
TRUSTED = "trusted comment: "


def point_add(P: tuple, Q: tuple) -> tuple:
    A, B = (P[1] - P[0]) * (Q[1] - Q[0]) % p, (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C, D = 2 * P[3] * Q[3] * d % p, 2 * P[2] * Q[2] % p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % p, G * H % p, F * G % p, E * H % p)


def point_mul(s: int, P: tuple) -> tuple:
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = point_add(Q, P)
        P = point_add(P, P)
        s >>= 1
    return Q


def point_equal(P: tuple, Q: tuple) -> bool:
    return (P[0] * Q[2] - Q[0] * P[2]) % p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % p == 0


def recover_x(y: int, sign: int) -> int | None:
    if y >= p:
        return None
    x2 = (y * y - 1) * pow(d * y * y + 1, p - 2, p)
    if x2 % p == 0:
        return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * SQRT_M1 % p
    if (x * x - x2) % p != 0:
        return None
    return p - x if (x & 1) != sign else x


def point_decompress(s: bytes) -> tuple | None:
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % p)


def sha512_modq(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % q


G_Y = 4 * pow(5, p - 2, p) % p
G_X = recover_x(G_Y, 0)
G = (G_X, G_Y, 1, G_X * G_Y % p)


def ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A, R = point_decompress(public), point_decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if A is None or R is None or s >= q:
        return False
    h = sha512_modq(signature[:32] + public + message)
    return point_equal(point_mul(s, G), point_add(R, point_mul(h, A)))


def verify(public_key: str, message: bytes, signature: str) -> str:
    """Return the trusted comment of a valid minisign signature, or raise ValueError."""
    key = base64.b64decode(public_key, validate=True)
    if len(key) != 42 or key[:2] != b"Ed":
        raise ValueError("invalid minisign public key")
    lines = signature.splitlines()
    if len(lines) != 4 or not lines[2].startswith(TRUSTED):
        raise ValueError("malformed minisign signature")
    blob = base64.b64decode(lines[1], validate=True)
    global_signature = base64.b64decode(lines[3], validate=True)
    if len(blob) != 74 or len(global_signature) != 64:
        raise ValueError("malformed minisign signature")
    algorithm, key_id, file_signature = blob[:2], blob[2:10], blob[10:]
    if key_id != key[2:10]:
        raise ValueError("signature was made with a different key")
    if algorithm == b"ED":
        message = hashlib.blake2b(message).digest()
    elif algorithm != b"Ed":
        raise ValueError("unsupported minisign signature algorithm")
    trusted = lines[2][len(TRUSTED):]
    if not (ed25519_verify(key[10:], message, file_signature)
            and ed25519_verify(key[10:], file_signature + trusted.encode(), global_signature)):
        raise ValueError("signature verification failed")
    return trusted
