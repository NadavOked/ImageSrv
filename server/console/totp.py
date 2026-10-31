"""RFC 6238 TOTP — HMAC-SHA1, 30 שניות, 6 ספרות. בלי pyotp.

זה מה שאפליקציות Authenticator מצפות: אותו אלגוריתם, אותו חלון.
`valid_window=1` = הצעד הנוכחי ± אחד (כ-30 שניות לכל צד).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1


def new_secret() -> str:
    """סוד base32 של 160 ביט, בלי ריפוד — כמו Google Authenticator."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    pad = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(secret.upper() + pad, casefold=True)


def totp_code(secret: str, when: float | None = None) -> str:
    t = int(time.time() if when is None else when)
    counter = t // STEP_SECONDS
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{code % (10 ** DIGITS):0{DIGITS}d}"


def verify_totp(secret: str, code: str, *, window: int = WINDOW,
                when: float | None = None) -> int | None:
    """מחזיר את ה-timestep שאומת, או None. לא בולע קוד שאינו 6 ספרות."""
    code = (code or "").strip()
    if len(code) != DIGITS or not code.isdigit() or not secret:
        return None
    t = int(time.time() if when is None else when)
    step = t // STEP_SECONDS
    for delta in range(-window, window + 1):
        candidate_when = (step + delta) * STEP_SECONDS
        if hmac.compare_digest(totp_code(secret, when=candidate_when), code):
            return step + delta
    return None


def otpauth_url(username: str, secret: str, issuer: str = "ImageSrv") -> str:
    from urllib.parse import quote
    label = f"{quote(issuer)}:{quote(username)}"
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
