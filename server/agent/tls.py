"""Per-server CA and renewable agent HTTPS identity (#3580)."""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import os
import re
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path

from server.interserver.auth import write_credential_0600

AGENT_TLS_DIRNAME = "agent-tls"
CA_DIRNAME = "ca"
CA_CERT_NAME = "ca.crt"
CA_KEY_NAME = "ca.key"
LEAF_CERT_NAME = "agent.crt"
LEAF_KEY_NAME = "agent.key"
#: השם בעלה: ``imagesrv-agent-<16 hex>`` (``server_name``). תווית DNS אחת (בלי
#: נקודות, בלי קו תחתון) — מה ש-curl, OpenSSL ו-Schannel מקבלים כשם מארח.
#: הסוכן קורא את שם השרת שלו מ-/etc/imagesrv/agent-name.
NAME_PREFIX = "imagesrv-agent"
NAME_RE = re.compile(r"imagesrv-agent-[a-z0-9][a-z0-9-]{0,46}")
CA_COMMON_NAME = "ImageSrv agent CA"
#: הכרעת נדב 31/10 (1): שעון שמאחור (סוללה מתה) אינו עוצר. agent/lib/core/tls.sh
#: משווה את השעון של המכונה לאותו רגע (``TLS_NOT_BEFORE_EPOCH``).
NOT_BEFORE = _dt.datetime(2000, 1, 1, tzinfo=_dt.timezone.utc)
#: הכרעת נדב 01/11: ה-CA לתוקף ארוך, העלה לתוקף רגיל ומתחדש מקומית.
CA_DAYS = 3650
LEAF_DAYS = 90
#: חידוש כשנשאר פחות משליש מחיי העלה — 30 יום להפעלה מחדש ברגע שקט.
RENEW_BEFORE = _dt.timedelta(days=LEAF_DAYS / 3)


class AgentTLSError(RuntimeError):
    """זהות TLS של הסוכן שאינה מתקבלת — נאמרת בשמה, לא מתוקנת בשקט."""


@dataclass(frozen=True)
class AgentTLS:
    """העלה שהשרת מגיש, ותעודת ה-CA (של השרת הזה) שחתמה עליו."""

    cert_path: Path
    key_path: Path
    ca_path: Path
    #: השם שבעלה — מה שמכונות של השרת הזה מאמתות.
    name: str

    def uvicorn_kwargs(self) -> dict:
        return {"ssl_certfile": str(self.cert_path), "ssl_keyfile": str(self.key_path)}


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _load_cert(pem: bytes):
    from cryptography import x509
    return x509.load_pem_x509_certificate(pem)


def _pem(cert) -> bytes:
    from cryptography.hazmat.primitives.serialization import Encoding
    return cert.public_bytes(Encoding.PEM)


def _new_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1())


def _key_pem(key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _load_key(pem: bytes):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    return load_pem_private_key(pem, password=None)


def _spki(public_key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return public_key.public_bytes(serialization.Encoding.DER,
                                   serialization.PublicFormat.SubjectPublicKeyInfo)


def _tighten(key_path: Path) -> None:
    if os.name == "posix" and stat_module.S_IMODE(os.stat(key_path).st_mode) != 0o600:
        os.chmod(key_path, 0o600)


def _write_public(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _dir(data_dir) -> Path:
    return Path(data_dir) / AGENT_TLS_DIRNAME


def _ca_path(data_dir) -> Path:
    return _dir(data_dir) / CA_DIRNAME / CA_CERT_NAME


def _window_problems(cert, label: str, now: _dt.datetime) -> list[str]:
    """תוקף שמתחיל אחרי 2000-01-01 (הכרעה 1 — שעון שמאחור היה נדחה), או
    שכבר פג."""
    found = []
    if cert.not_valid_before_utc > NOT_BEFORE:
        found.append(f"{label} מתחיל ב-{cert.not_valid_before_utc:%Y-%m-%d}, לא ב-2000-01-01")
    if cert.not_valid_after_utc <= now:
        found.append(f"{label} פג ב-{cert.not_valid_after_utc:%Y-%m-%d}")
    return found


# --- ה-CA של השרת הזה -----------------------------------------------------------

def ensure_ca(data_dir, *, now=None) -> tuple[bytes, bytes]:
    """(ca_cert_pem, ca_key_pem) — יוצר **פעם אחת**, בכל תפקיד. חצי CA = כשל
    בשם: CA חדש בשקט היה מבטל את האמון של כל initrd שנאפה מול הקודם."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID

    directory = _dir(data_dir) / CA_DIRNAME
    cert_path, key_path = directory / CA_CERT_NAME, directory / CA_KEY_NAME
    have_cert, have_key = cert_path.exists(), key_path.exists()
    if have_cert != have_key:
        raise AgentTLSError(
            f"ה-CA של ערוץ הסוכן חלקי ב-{directory}: "
            f"{'תעודה בלי מפתח' if have_cert else 'מפתח בלי תעודה'}. CA חדש מבטל את "
            "כל ה-initrd של השרת הזה — הסר את שני הקבצים רק אם זו הכוונה")
    if not have_cert:
        directory.mkdir(parents=True, exist_ok=True)
        key = _new_key()
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME)])
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(NOT_BEFORE)
                .not_valid_after((now or _now()) + _dt.timedelta(days=CA_DAYS))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(
                    digital_signature=False, content_commitment=False,
                    key_encipherment=False, data_encipherment=False, key_agreement=False,
                    key_cert_sign=True, crl_sign=True, encipher_only=False,
                    decipher_only=False), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                               critical=False)
                .sign(key, hashes.SHA256()))
        write_credential_0600(key_path, _key_pem(key).decode())
        _write_public(cert_path, _pem(cert))
    _tighten(key_path)
    return cert_path.read_bytes(), key_path.read_bytes()


def server_name(ca_pem: bytes) -> str:
    """שם העלה של השרת: נגזר מהמפתח הציבורי של ה-CA שלו — שונה בכל שרת,
    וקבוע כל עוד ה-CA קבוע (חידוש עלה, צימוד או ניתוק אינם משנים אותו)."""
    digest = hashlib.sha256(_spki(_load_cert(ca_pem).public_key())).hexdigest()
    return f"{NAME_PREFIX}-{digest[:16]}"


# --- העלה -----------------------------------------------------------------------

def leaf_names(leaf) -> list[str]:
    from cryptography import x509
    try:
        return leaf.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        return []


def ensure_agent_key(data_dir) -> bytes:
    """מפתח העלה של **השרת הזה** — נוצר פעם אחת, ואינו עוזב אותו."""
    directory = _dir(data_dir)
    key_path = directory / LEAF_KEY_NAME
    if not key_path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        write_credential_0600(key_path, _key_pem(_new_key()).decode())
    _tighten(key_path)
    return key_path.read_bytes()


def make_csr(key_pem: bytes) -> bytes:
    """CSR בלי שם: השם נקבע בחתימה (``sign_leaf(name=…)``)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.x509.oid import NameOID
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NAME_PREFIX)]))
           .sign(_load_key(key_pem), hashes.SHA256()))
    return csr.public_bytes(Encoding.PEM)


def sign_leaf(csr_pem: bytes, ca_cert_pem: bytes, ca_key_pem: bytes, *, name: str,
              now=None) -> bytes:
    """חותם עלה מ-CSR. **השם נקבע כאן, לא ב-CSR**: רק המפתח הציבורי נכנס
    (אחרי שחתימת ה-CSR אומתה). התוקף: 2000-01-01 עד ``LEAF_DAYS`` מעכשיו,
    ולא אחרי ה-CA."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    if not NAME_RE.fullmatch(name or ""):
        raise AgentTLSError(f"שם עלה לא תקין: {name!r}")
    try:
        csr = x509.load_pem_x509_csr(csr_pem)
    except ValueError as exc:
        raise AgentTLSError(f"CSR לא תקין: {exc}") from exc
    if not csr.is_signature_valid:
        raise AgentTLSError("חתימת ה-CSR אינה תקפה — המבקש אינו מחזיק את המפתח")
    ca = _load_cert(ca_cert_pem)
    ca_key = _load_key(ca_key_pem)
    if _spki(ca_key.public_key()) != _spki(ca.public_key()):
        raise AgentTLSError("מפתח ה-CA אינו תואם לתעודת ה-CA")
    not_after = min((now or _now()) + _dt.timedelta(days=LEAF_DAYS), ca.not_valid_after_utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca.subject)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(NOT_BEFORE)
            .not_valid_after(not_after)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    return _pem(cert)


def leaf_problems(leaf_pem: bytes, ca_pem: bytes, *, key_pem: bytes | None = None,
                  name: str | None = None, now=None) -> list[str]:
    """מה פסול בעלה מול ה-CA — ריק = תקין. אותן שאלות שהסוכן שואל: חתום
    בידי ה-CA, **שם אחד בלבד** בצורה הנכונה (ו-``name`` כשניתן), התוקף
    (מ-2000, ולא פג), ו-(כשניתן) המפתח הפרטי שלנו."""
    from cryptography import x509
    now = now or _now()
    try:
        leaf, ca = _load_cert(leaf_pem), _load_cert(ca_pem)
    except ValueError as exc:
        return [f"תעודה לא קריאה: {exc}"]
    found = []
    try:
        if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            found.append("תעודת ה-CA אינה CA")
    except x509.ExtensionNotFound:
        found.append("תעודת ה-CA אינה CA")
    try:
        leaf.verify_directly_issued_by(ca)
    except Exception:                                         # noqa: BLE001
        found.append("העלה אינו חתום בידי ה-CA")
    names = leaf_names(leaf)
    if len(names) != 1 or not NAME_RE.fullmatch(names[0]):
        found.append(f"ב-SAN של העלה צריך שם אחד בצורה {NAME_PREFIX}-…, יש: {names}")
    elif name is not None and names[0] != name:
        found.append(f"העלה הוא של {names[0]}, לא של {name}")
    found += _window_problems(leaf, "העלה", now)
    found += _window_problems(ca, "ה-CA", now)
    if key_pem is not None and _spki(_load_key(key_pem).public_key()) != _spki(leaf.public_key()):
        found.append("העלה אינו של המפתח של השרת הזה")
    return found


def renewal_due(leaf_pem: bytes, ca_pem: bytes, now=None) -> bool:
    """נשאר לעלה פחות משליש מחייו — ועלה חדש יחזיק יותר ממנו (בחודש האחרון
    של ה-CA עלה חדש אינו ארוך יותר, וחידוש בכל שעה היה הפעלה מחדש בכל שעה)."""
    leaf_end = _load_cert(leaf_pem).not_valid_after_utc
    return (leaf_end - (now or _now()) < RENEW_BEFORE
            and leaf_end < _load_cert(ca_pem).not_valid_after_utc)


def _identity(data_dir) -> AgentTLS:
    d = _dir(data_dir)
    leaf = _load_cert((d / LEAF_CERT_NAME).read_bytes())
    return AgentTLS(cert_path=d / LEAF_CERT_NAME, key_path=d / LEAF_KEY_NAME,
                    ca_path=_ca_path(data_dir), name=leaf_names(leaf)[0])


def ensure_identity(data_dir, *, now=None) -> tuple[AgentTLS, bool]:
    # The server and payload builder may initialize or renew the same site concurrently.
    directory = _dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "identity.lock").open("ab") as lock:
        if os.name == "posix":
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        return _ensure_identity(data_dir, now=now)


def _ensure_identity(data_dir, *, now=None) -> tuple[AgentTLS, bool]:
    """ה-CA, מפתח העלה ועלה חתום — בכל שרת, בלי קשר לתפקיד ובלי שרת אחר.
    מנפיק עלה חדש כשהוא חסר, פסול או שהגיע זמן חידושו. (הזהות, האם הונפק
    עלה חדש). ה-CA אינו מוחלף כאן לעולם, ולכן initrd קיים ממשיך לעבוד."""
    now = now or _now()
    ca_pem, ca_key_pem = ensure_ca(data_dir, now=now)
    problems = _window_problems(_load_cert(ca_pem), "CA", now)
    if problems:
        raise AgentTLSError(", ".join(problems))
    key_pem = ensure_agent_key(data_dir)
    name = server_name(ca_pem)
    leaf_path = _dir(data_dir) / LEAF_CERT_NAME
    fresh = (leaf_path.exists()
             and not leaf_problems(leaf_path.read_bytes(), ca_pem, key_pem=key_pem,
                                   name=name, now=now)
             and not renewal_due(leaf_path.read_bytes(), ca_pem, now))
    if not fresh:
        _write_public(leaf_path, sign_leaf(make_csr(key_pem), ca_pem, ca_key_pem,
                                           name=name, now=now))
    return _identity(data_dir), not fresh


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["ca"])
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    identity, _ = ensure_identity(args.data_dir)
    print(identity.ca_path)
    print(identity.name)


if __name__ == "__main__":
    main()
