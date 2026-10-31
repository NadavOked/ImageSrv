#!/usr/bin/env python3
"""verify-release-tag.py — ‏#1277: האם תג גרסה חתום במפתח שהשרת סומך עליו.

שימוש: verify-release-tag.py <repo|git-dir> <tag> <allowed-signers>

שלושה מצבים, שלושה קודי יציאה — "לא בדקנו" אינו "בדקנו ואינו חתום",
ושניהם אינם "חתום" (עיקרון 5):

  0  חתום — שורה אחת ב-stdout: ``signed: <principal> SHA256:<טביעה>``
  1  נבדק ואינו חתום במפתח מוכר (תג קל, בלי חתימה, מפתח זר, חתימה שאינה
     תקפה, שם תג שאינו תואם) — הסיבה ב-stderr, בתחילית ``unsigned:``
  2  לא נבדק — קובץ המפתחות חסר/ריק/פגום, git נכשל, התג לא נמצא — הסיבה
     ב-stderr, בתחילית ``unchecked:``

המקבלים: ``tools/server-upgrade.sh`` (לפני ה-checkout — 1 ו-2 שניהם
סירוב), ‏``server/update.py`` (בדיקת עדכון בקונסולה: חתום / לא חתום /
לא נבדק), ו-``tools/git/release-sign.sh`` (התג שנחתם בפרסום עובר את
אותו מאמת שהשרתים יריצו, לפני שהוא נדחף).

**למה לא ``git verify-tag``:** הוא מפעיל את ``ssh-keygen``, וזה מחייב את
חבילת לקוח ה-SSH על השרת — ו-#1266 הוציא SSH מהמוצר. הפורמט של חתימת
git ב-SSH (‏SSHSIG, ‏PROTOCOL.sshsig) קטן ומוגדר, ו-``cryptography`` כבר
מותקן בשרת (‏#740). התאימות נבדקת מול חתימות אמיתיות של ``git tag -s``
ב-``tests/test_release_signing_1277.py``.

מה נאכף, ולמה:
* האובייקט הוא תג מוער (לא תג קל), ושורת ה-``tag`` שבתוכו היא **בדיוק**
  התג המבוקש — אחרת אפשר להציג חתימה תקפה של גרסה ישנה תחת שם חדש.
* החתימה היא החתימה האחרונה בתג (כמו ש-git קורא אותה), מסוג SSH בלבד,
  ב-namespace ‏``git``, וה-hash ‏sha512/sha256.
* המפתח שבחתימה זהה בבייטים למפתח שבקובץ — ורק אז האימות הקריפטוגרפי.
* ‏ed25519, או RSA עם ‏rsa-sha2-512/256 (לא ‏SHA-1).
"""

from __future__ import annotations

import base64
import hashlib
import subprocess
import sys

KEY_TYPES = ("ssh-ed25519", "ssh-rsa")
#: האפשרות היחידה שמותרת בשורת מפתח — כל אחרת (valid-after, cert-authority)
#: הייתה מקבלת כאן משמעות שונה ממה ש-ssh-keygen נותן לה, ולכן היא "לא נבדק".
ALLOWED_OPTION = 'namespaces="git"'
NAMESPACE = b"git"
HASHES = {b"sha512": hashlib.sha512, b"sha256": hashlib.sha256}
RSA_SIGS = {b"rsa-sha2-512": "SHA512", b"rsa-sha2-256": "SHA256"}
#: הסמנים שבהם git מזהה תחילת חתימה (gpg-interface.c) — ולוקח את **האחרון**.
SIG_MARKERS = (b"-----BEGIN PGP SIGNATURE-----", b"-----BEGIN PGP MESSAGE-----",
               b"-----BEGIN SIGNED MESSAGE-----", b"-----BEGIN SSH SIGNATURE-----")
SSH_BEGIN, SSH_END = b"-----BEGIN SSH SIGNATURE-----", b"-----END SSH SIGNATURE-----"


class Unsigned(Exception):
    """נבדק: אינו חתום במפתח מוכר."""


class Unchecked(Exception):
    """לא נבדק: הבדיקה עצמה לא יכלה לרוץ."""


def fingerprint(key_blob: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key_blob).digest()).decode().rstrip("=")


def load_signers(path: str) -> list[tuple[str, bytes]]:
    """``[(principal, key_blob)]``. קובץ בלי אף מפתח = "לא נבדק", לא "הכול מותר"."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise Unchecked(f"קובץ המפתחות {path} לא נקרא: {exc}") from exc
    keys = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        principal, rest = fields[0], fields[1:]
        if rest and rest[0] not in KEY_TYPES:
            if rest[0] != ALLOWED_OPTION:
                raise Unchecked(f"{path}:{n}: אפשרות לא נתמכת {rest[0]!r} (רק {ALLOWED_OPTION})")
            rest = rest[1:]
        if len(rest) < 2 or rest[0] not in KEY_TYPES:
            raise Unchecked(f"{path}:{n}: שורה אינה '<principal> [{ALLOWED_OPTION}] <type> <key>'")
        try:
            blob = base64.b64decode(rest[1], validate=True)
        except ValueError as exc:
            raise Unchecked(f"{path}:{n}: המפתח אינו base64 תקין") from exc
        keys.append((principal, blob))
    if not keys:
        raise Unchecked(f"אין אף מפתח ב-{path} — אין מול מה לבדוק חתימה")
    return keys


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unchecked(f"git לא רץ: {exc}") from exc


def read_tag(repo: str, tag: str) -> bytes:
    ref = f"refs/tags/{tag}"
    kind = _git(repo, "cat-file", "-t", ref)
    if kind.returncode != 0:
        raise Unchecked(f"התג {tag} לא נמצא ב-{repo}: "
                        f"{kind.stderr.decode(errors='replace').strip()}")
    if kind.stdout.strip() != b"tag":
        raise Unsigned(f"{tag} הוא תג קל ({kind.stdout.decode().strip()}) — אין אובייקט תג ואין חתימה")
    obj = _git(repo, "cat-file", "tag", ref)
    if obj.returncode != 0:
        raise Unchecked(f"git cat-file tag {ref} נכשל: {obj.stderr.decode(errors='replace').strip()}")
    return obj.stdout


def split_signature(raw: bytes) -> tuple[bytes, bytes]:
    """‏(payload, signature) — החתימה מתחילה בשורת הסמן **האחרונה**, כמו ב-git."""
    start, pos = None, 0
    for line in raw.splitlines(keepends=True):
        if line.startswith(SIG_MARKERS):
            start = pos
        pos += len(line)
    if start is None:
        raise Unsigned("התג אינו חתום (אין בו חתימה)")
    payload, sig = raw[:start], raw[start:]
    if not sig.startswith(SSH_BEGIN):
        raise Unsigned("החתימה אינה חתימת SSH — מתקבלת רק חתימה מול deploy/release-signers")
    lines = sig.split(b"\n")
    try:
        end = lines.index(SSH_END)
    except ValueError as exc:
        raise Unsigned("חתימה בלי שורת סיום") from exc
    if any(line.strip() for line in lines[end + 1:]):
        raise Unsigned("תוכן אחרי סוף החתימה")
    try:
        return payload, base64.b64decode(b"".join(lines[1:end]), validate=True)
    except ValueError as exc:
        raise Unsigned("גוף החתימה אינו base64 תקין") from exc


def _string(buf: bytes, pos: int) -> tuple[bytes, int]:
    if pos + 4 > len(buf):
        raise Unsigned("חתימה קטועה")
    size = int.from_bytes(buf[pos:pos + 4], "big")
    if pos + 4 + size > len(buf):
        raise Unsigned("חתימה קטועה")
    return buf[pos + 4:pos + 4 + size], pos + 4 + size


def _pack(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def verify(payload: bytes, blob: bytes, signers: list[tuple[str, bytes]]) -> str:
    """מחזיר ``<principal> <טביעה>``; כל דבר אחר — ``Unsigned``."""
    if blob[:6] != b"SSHSIG" or int.from_bytes(blob[6:10], "big") != 1:
        raise Unsigned("החתימה אינה SSHSIG גרסה 1")
    pubkey, pos = _string(blob, 10)
    namespace, pos = _string(blob, pos)
    reserved, pos = _string(blob, pos)
    hash_alg, pos = _string(blob, pos)
    signature, pos = _string(blob, pos)
    if pos != len(blob):
        raise Unsigned("בייטים מיותרים אחרי החתימה")
    if namespace != NAMESPACE:
        raise Unsigned(f"namespace של החתימה הוא {namespace!r}, לא git")
    if hash_alg not in HASHES:
        raise Unsigned(f"hash לא נתמך: {hash_alg!r}")
    principal = next((p for p, key in signers if key == pubkey), None)
    if principal is None:
        raise Unsigned(f"חתום במפתח שאינו ב-release-signers ({fingerprint(pubkey)})")
    sig_type, spos = _string(signature, 0)
    sig_bytes, spos = _string(signature, spos)
    signed = (b"SSHSIG" + _pack(namespace) + _pack(reserved) + _pack(hash_alg)
              + _pack(HASHES[hash_alg](payload).digest()))
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
    except ImportError as exc:
        raise Unchecked(f"python3-cryptography אינו מותקן: {exc}") from exc
    key_type, _ = _string(pubkey, 0)
    key = serialization.load_ssh_public_key(key_type + b" " + base64.b64encode(pubkey))
    try:
        if isinstance(key, ed25519.Ed25519PublicKey) and sig_type == b"ssh-ed25519":
            key.verify(sig_bytes, signed)
        elif isinstance(key, rsa.RSAPublicKey) and sig_type in RSA_SIGS:
            key.verify(sig_bytes, signed, padding.PKCS1v15(),
                       getattr(hashes, RSA_SIGS[sig_type])())
        else:
            raise Unsigned(f"סוג חתימה {sig_type!r} אינו תואם למפתח {key_type!r}")
    except InvalidSignature as exc:
        raise Unsigned("החתימה אינה תקפה — התג שונה אחרי החתימה, או שהחתימה זויפה") from exc
    return f"{principal} {fingerprint(pubkey)}"


def check(repo: str, tag: str, signers_path: str) -> str:
    signers = load_signers(signers_path)
    raw = read_tag(repo, tag)
    header = raw.split(b"\n\n", 1)[0].split(b"\n")
    names = [line[4:] for line in header if line.startswith(b"tag ")]
    if names != [tag.encode()]:
        raise Unsigned(f"refs/tags/{tag} מחזיק את אובייקט התג של {names!r} — שם שאינו תואם")
    payload, blob = split_signature(raw)
    return verify(payload, blob, signers)


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):              # הסיבות בעברית; קונסולת ווינדוס היא cp1252
        stream.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) != 3:
        print("usage: verify-release-tag.py <repo|git-dir> <tag> <allowed-signers>", file=sys.stderr)
        return 2
    try:
        print(f"signed: {check(*argv)}")
        return 0
    except Unsigned as exc:
        print(f"unsigned: {exc}", file=sys.stderr)
        return 1
    except Unchecked as exc:
        print(f"unchecked: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                            # noqa: BLE001 — באג במאמת ≠ "חתום"
        print(f"unchecked: המאמת נכשל: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
