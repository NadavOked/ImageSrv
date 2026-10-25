"""‏TLS לקונסולה (‏#703, tracer 5).

הקונסולה (8081, כרטיס הניהול) הגישה HTTP וסיסמת admin עברה גלויה —
במכללה שמלמדת סייבר. מ-tracer 5 המאזין של הקונסולה הוא **HTTPS בלבד**:
תעודה חתומה-עצמית שנוצרת בהפעלה הראשונה ל-``<data_dir>/console-tls/``
(‏``console.crt`` + ‏``console.key`` ב-0600) ונשארת **אותה תעודה** בכל
הפעלה — הדפדפן של המפעיל מאשר אותה פעם אחת. ‏HTTP על 8081 אינו מוגש
(fail-closed): אין הפניה שקטה ואין מאזין-מפנה, כי מאזין HTTP על אותו
פורט הוא בדיוק מה שהדפדפן (או תלמיד) היה נופל אליו קודם.

**למה תעודה נפרדת מזהות ה-interserver (`storage_nodes.ensure_identity`)
ולא אותה זהות:** ה-SPKI של זהות ה-interserver **מוצמד** על ידי הצד
השני (SPKI-pin, #740), והיא נוצרת בלי SAN של כרטיס הניהול (על ראשי —
בלי SAN כלל, כתעודת-לקוח). תעודת הקונסולה צריכה SAN של ‎--console-host‏
כדי שהדפדפן יתלונן על "לא מוכרת" בלבד ולא גם על "שם לא תואם", ואסור
שחידושה ישבור pin. המחולל **משותף** (`interserver_auth.generate_self_signed`)
— אין כאן מחולל שני, רק זהות שנייה.

**למה stdlib ssl ולא pyOpenSSL:** ‏uvicorn מקבל ``ssl_certfile``/
``ssl_keyfile`` ובונה ``ssl.SSLContext`` בעצמו; לקונסולה אין תעודת-לקוח
ואין exporter, ולכן אין צורך ב-terminator של pyOpenSSL כמו ב-8443.

**‏EKU ותוקף (#1815):** התעודה העצמית נושאת EKU ‏``serverAuth`` בלבד — היא
מוגשת לדפדפן ואינה משמשת כתעודת-לקוח. **התוקף נשאר 3650 יום, וזו מגבלה
ידועה:** ‏Apple ‏(support.apple.com/103769, iOS 13 / macOS 10.15 ואילך) דורשת
מ**כל** תעודת שרת TLS תוקף של 825 יום לכל היותר, בלי הבחנה בין שורש מובנה
לאמון שהמשתמש הוסיף. כלומר ה-EKU לבדו כנראה **אינו** מספיק ל-Safari. קיצור
התוקף מחייב חידוש אוטומטי (ושינוי טביעת האצבע שהמפעיל אישר) — הכרעה פתוחה.
תעודה שנוצרה לפני #1815 אינה מוחלפת אוטומטית; "הנפקת תעודה חדשה" מוסיפה לה EKU.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import shutil
import socket
import stat as stat_module
from dataclasses import dataclass, field
from pathlib import Path

from .atomicfile import write_atomic
from .interserver_auth import (generate_self_signed, is_loopback,
                               write_credential_0600)

#: תת-התיקייה של ``data_dir`` שבה חיה זהות הקונסולה.
CONSOLE_TLS_DIRNAME = "console-tls"
CERT_NAME = "console.crt"
KEY_NAME = "console.key"
#: ה-CN של התעודה; מה שהדפדפן מציג ב"הונפק ל".
COMMON_NAME = "imagesrv-console"
GENERATED_MARKER = "generated.sha256"
#: ‏#2095: תת-התיקייה עם העותק ש-uvicorn טוען בפועל — נכתב בכל עליית שרת
#: מהבייטים שטביעת האצבע חושבה מהם, ולכן ``/me`` = מה שמוגש, גם אם מישהו
#: כותב את התעודה הקנונית אחרי העלייה.
SERVING_DIRNAME = "serving"


class ConsoleTLSError(RuntimeError):
    """תצורת TLS של הקונסולה שאינה מתקבלת — נאמרת בשמה, לא מתוקנת בשקט."""


@dataclass(frozen=True)
class ConsoleTLS:
    """זהות הקונסולה כפי שנטענה/נוצרה: הנתיבים ל-uvicorn וטביעת האצבע למפעיל."""

    cert_path: Path
    key_path: Path
    #: ‏SHA-256 של התעודה (DER), ``AB:CD:…`` — הצורה שהדפדפן מציג
    #: ב"פרטי תעודה", כדי שנדב ישווה ביד.
    fingerprint_sha256: str
    #: ה-SANs שבתעודה, כפי שנקראו ממנה (לא כפי שביקשנו).
    sans: tuple[str, ...]
    #: ‏#153: המנפיק הוא הנושא — התעודה שנוצרה כאן, או עצמית שהועלתה.
    #: ‏False = תעודה שהועלתה וחתומה בידי CA (של המכללה או ציבורי).
    self_signed: bool = True
    regenerated: bool = False
    previous_fingerprint_sha256: str | None = None
    #: ‏#2095: הבייטים שטביעת האצבע חושבה מהם — מה ש-``uvicorn_kwargs`` מגיש.
    cert_pem: bytes = field(default=b"", repr=False)
    key_pem: bytes = field(default=b"", repr=False)

    def public_info(self) -> dict:
        """מה שהקונסולה מקבלת ב-``/me`` לשורת הסטטוס."""
        return {"mode": "self-signed" if self.self_signed else "ca-signed",
                "fingerprint_sha256": self.fingerprint_sha256}

    def uvicorn_kwargs(self) -> dict:
        """הארגומנטים ל-``uvicorn.Config`` של מאזין הקונסולה.

        ‏#2095: uvicorn קורא את הקבצים כשהמאזין נבנה — ומאזין של כתובת שהגיעה
        אחרי העלייה (#1088) נבנה דקות אחר כך. התעודה הקנונית הוחלפה בינתיים
        על הדיסק (ה-DCUI הנפיק אותה מחדש), ו-``/me`` דיווח טביעה של תעודה
        שאינה זו שמוגשת. לכן המאזין טוען **עותק** של הבייטים שבזיכרון, ב-
        ``serving/``, ולא את הקובץ הקנוני."""
        directory = self.cert_path.parent / SERVING_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        cert_path, key_path = directory / CERT_NAME, directory / KEY_NAME
        write_credential_0600(key_path, self.key_pem.decode("utf-8"))
        cert_path.write_bytes(self.cert_pem)
        return {"ssl_certfile": str(cert_path), "ssl_keyfile": str(key_path)}


def check_tls_flag(mode: str, console_host: str) -> None:
    """‏``--console-tls off`` מותר **רק** על loopback (הרצת פיתוח/e2e).

    על כל כתובת אחרת הסיסמה הייתה עוברת ברשת גלויה — וזה בדיוק מה
    ש-tracer 5 סוגר. הסירוב נוקב בשני הדגלים, לא נופל ל-TLS בשקט."""
    if mode == "off" and not is_loopback(console_host):
        raise ConsoleTLSError(
            f"--console-tls off מותר רק עם --console-host על loopback "
            f"(127.0.0.1); התקבל --console-host {console_host!r}. הקונסולה "
            f"על כרטיס ניהול מוגשת ב-HTTPS בלבד (#703)")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def fingerprint_sha256(cert_pem: bytes) -> str:
    """‏SHA-256 של התעודה (DER), ‏``AB:CD:…`` — כמו בדפדפן."""
    from cryptography import x509
    der = x509.load_pem_x509_certificate(cert_pem).public_bytes(_encoding_der())
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def _encoding_der():
    from cryptography.hazmat.primitives.serialization import Encoding
    return Encoding.DER


def cert_sans(cert_pem: bytes) -> tuple[str, ...]:
    """ה-SANs שבתעודה — IP ו-DNS — כמחרוזות, כפי שנכתבו בה."""
    from cryptography import x509
    cert = x509.load_pem_x509_certificate(cert_pem)
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return ()
    values = [str(v) for v in ext.value.get_values_for_type(x509.IPAddress)]
    values += ext.value.get_values_for_type(x509.DNSName)
    return tuple(values)


def ensure_console_cert(data_dir, console_host: str,
                        hostname: str | None = None) -> ConsoleTLS:
    """מחזיר את זהות הקונסולה — יוצר אותה **פעם אחת** אם אין.

    ‏SAN = ‏``console_host`` (IP או DNS) + שם המארח. רק תעודה שהשרת יצר
    וסימן בטביעת האצבע שלה מתחדשת כשכתובת הקונסולה השתנתה. תעודה שהמפעיל
    העלה לעולם אינה מוחלפת אוטומטית, גם אם היא חתומה-עצמית.
    חצי זהות (תעודה בלי מפתח או להפך) היא כשל בשמו, לא יצירה מחדש
    שקטה שהייתה מחליפה תעודה שדפדפנים כבר מכירים (עיקרון 5).
    """
    directory = Path(data_dir) / CONSOLE_TLS_DIRNAME
    cert_path, key_path = directory / CERT_NAME, directory / KEY_NAME
    have_cert, have_key = cert_path.exists(), key_path.exists()
    if have_cert != have_key:
        raise ConsoleTLSError(
            f"זהות הקונסולה חלקית ב-{directory}: "
            f"{'תעודה בלי מפתח' if have_cert else 'מפתח בלי תעודה'} — "
            f"הסר את שני הקבצים כדי שתיווצר זהות חדשה")
    regenerated = False
    previous_fingerprint = None
    if have_cert:
        existing_pem = cert_path.read_bytes()
        existing_sans = cert_sans(existing_pem)
        generated = directory / GENERATED_MARKER
        generated_fingerprint = generated.read_text(encoding="ascii").strip() \
            if generated.is_file() else ""
        # ‏#2095: loopback אינו כתובת חדשה — זה מה שהשרת מעביר כשה-lease עוד
        # לא הגיע. תעודה שנוצרה מחדש עליו התחלפה בכל אתחול שבו השרת עלה
        # לפני ה-DHCP (ESXi, 24/10), והדפדפן נדרש לאשר שוב.
        if (console_host not in existing_sans and not is_loopback(console_host)
                and _self_signed(existing_pem)
                and generated_fingerprint == fingerprint_sha256(existing_pem)):
            previous_fingerprint = fingerprint_sha256(existing_pem)
            shutil.copy2(cert_path, cert_path.with_name(CERT_NAME + PREV_SUFFIX))
            write_credential_0600(key_path.with_name(KEY_NAME + PREV_SUFFIX),
                                  key_path.read_text(encoding="utf-8"))
            have_cert = have_key = False
            regenerated = True
    if not have_cert:
        from cryptography.x509.oid import ExtendedKeyUsageOID

        directory.mkdir(parents=True, exist_ok=True)
        hostname = hostname or socket.gethostname()
        ip_sans = [console_host] if _is_ip(console_host) else []
        dns_sans = [] if _is_ip(console_host) else [console_host]
        if hostname and hostname not in dns_sans:
            dns_sans.append(hostname)
        cert_pem, key_pem = generate_self_signed(
            COMMON_NAME, ip_sans=ip_sans, dns_sans=dns_sans,
            eku=(ExtendedKeyUsageOID.SERVER_AUTH,))
        write_credential_0600(key_path, key_pem.decode())
        write_atomic(cert_path, cert_pem)
        write_atomic(directory / GENERATED_MARKER,
                     fingerprint_sha256(cert_pem) + "\n", encoding="ascii")
    elif os.name == "posix":
        # המפתח הוא שלנו — מצב רחב מדי (umask, העתקה ביד) מוחזר ל-0600.
        mode = stat_module.S_IMODE(os.stat(key_path).st_mode)
        if mode != 0o600:
            os.chmod(key_path, 0o600)
    cert_pem = cert_path.read_bytes()
    return ConsoleTLS(cert_path=cert_path, key_path=key_path,
                      fingerprint_sha256=fingerprint_sha256(cert_pem),
                      sans=cert_sans(cert_pem), self_signed=_self_signed(cert_pem),
                      regenerated=regenerated,
                      previous_fingerprint_sha256=previous_fingerprint,
                      cert_pem=cert_pem, key_pem=key_path.read_bytes())


def presented_console_cert(address: tuple[str, int] = ("127.0.0.1", 8081),
                           timeout: float = 1.0) -> bytes:
    """‏#2148: התעודה שמאזין הקונסולה **מציג עכשיו** (PEM) — מה שהדפדפן יראה.

    ‏#2095 קרא את ``serving/`` מהדיסק, וזה לא היה עדכני: אחרי restart העותק
    שם נשאר של התהליך הקודם עד שהחדש בנה את המאזין, וה-DCUI הציג טביעה
    שאף מאזין כבר לא הגיש (ESXi, 24/10). כאן אין עותק לסמוך עליו — לחיצת
    יד TLS על loopback (הקונסולה מאזינה עליו תמיד, #904), בלי אימות: אנחנו
    לא סומכים על התעודה, רק קוראים אותה. מאזין שלא עונה = ``OSError``, ולא
    טביעה ישנה (עיקרון 5)."""
    import ssl
    return ssl.get_server_certificate(address, timeout=timeout).encode("ascii")


# --- ‏#153: תעודה שהמפעיל מעלה מלשונית ההגדרות ---------------------------------

#: גבול גודל לכל אחד מהשניים — תעודה עם שרשרת ארוכה היא כמה KB.
MAX_PEM_BYTES = 64 * 1024
#: העותק של הזוג הקודם, ליד הנוכחי — החזרה ידנית במסוף השרת אם הדפדפן נכשל.
PREV_SUFFIX = ".prev"


class UploadRefused(ValueError):
    """תעודה/מפתח שהועלו ואינם מתקבלים — ההודעה בעברית ונוקבת בסיבה."""


def _first_cert(cert_pem: bytes):
    from cryptography import x509
    return x509.load_pem_x509_certificates(cert_pem)[0]


def _self_signed(cert_pem: bytes) -> bool:
    cert = _first_cert(cert_pem)
    return cert.issuer == cert.subject


def _cn(name) -> str:
    from cryptography.x509.oid import NameOID
    values = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(values[0].value) if values else ""


def cert_details(cert_pem: bytes) -> dict:
    """מה שהלשונית מציגה על תעודה: נושא, מנפיק, SANs, תוקף, טביעת אצבע."""
    cert = _first_cert(cert_pem)
    return {
        "subject": _cn(cert.subject),
        "issuer": _cn(cert.issuer),
        "self_signed": cert.issuer == cert.subject,
        "sans": list(cert_sans(cert_pem)),
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": cert.not_valid_after_utc.isoformat(),
        "fingerprint_sha256": fingerprint_sha256(cert_pem),
    }


def _ssl_loads(cert_pem: bytes, key_pem: bytes, directory: Path) -> str | None:
    """הטעינה **שה-uvicorn עצמו יעשה** (``SSLContext.load_cert_chain``) על
    קבצים זמניים — ראיה חיובית שהשרת יעלה עם הזוג, לא ניחוש מהפענוח."""
    import ssl
    import tempfile
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        cert_file, key_file = Path(tmp) / "c.pem", Path(tmp) / "k.pem"
        cert_file.write_bytes(cert_pem)
        write_credential_0600(key_file, key_pem.decode())
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(
                str(cert_file), str(key_file))
        except (ssl.SSLError, OSError) as exc:
            return str(exc)
    return None


def validate_upload(cert_pem: bytes, key_pem: bytes, now=None) -> dict:
    """בודק זוג שהועלה: PEM, מפתח לא מוצפן, תואם, ובתוקף **עכשיו**.

    מחזיר את ``cert_details`` של התעודה; כל כשל הוא ``UploadRefused``
    בשמו. ‏SAN שאינו כולל את שם המארח **אינו** סיבת דחייה — תעודה של
    ‏``imagectl.college.ac.il`` היא בדיוק מה שמעלים כדי לגשת בשם — אלא
    אזהרה שהלשונית מציגה (``covers_hostname``)."""
    import datetime
    from cryptography.hazmat.primitives import serialization
    if len(cert_pem) > MAX_PEM_BYTES or len(key_pem) > MAX_PEM_BYTES:
        raise UploadRefused("קובץ גדול מ-64KB — זו אינה תעודה או מפתח PEM")
    try:
        cert = _first_cert(cert_pem)
    except ValueError:
        raise UploadRefused("קובץ התעודה אינו PEM תקין "
                            "(מצופה -----BEGIN CERTIFICATE-----)") from None
    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except TypeError:
        raise UploadRefused("המפתח מוצפן בסיסמה — השרת עולה בלי אדם שיקליד "
                            "אותה, ולכן נדרש מפתח לא מוצפן") from None
    except Exception:                                    # noqa: BLE001 — כל כשל פענוח
        raise UploadRefused("קובץ המפתח אינו PEM תקין "
                            "(מצופה -----BEGIN PRIVATE KEY-----)") from None
    spki = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    if cert.public_key().public_bytes(*spki) != key.public_key().public_bytes(*spki):
        raise UploadRefused("המפתח אינו תואם את התעודה — "
                            "המפתח הציבורי שבתעודה שונה מזה של המפתח")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now < cert.not_valid_before_utc:
        raise UploadRefused(f"התעודה עוד לא בתוקף — תקפה מ-"
                            f"{cert.not_valid_before_utc:%Y-%m-%d %H:%M} UTC")
    if now > cert.not_valid_after_utc:
        raise UploadRefused(f"תוקף התעודה פג ב-"
                            f"{cert.not_valid_after_utc:%Y-%m-%d %H:%M} UTC")
    return cert_details(cert_pem)


def install_upload(data_dir, cert_pem: bytes, key_pem: bytes) -> dict:
    """מאמת, טוען ב-``ssl`` כמו uvicorn, ורק אז מחליף — הזוג הקודם נשמר
    כ-``.prev``. ‏``UploadRefused`` = שום קובץ לא השתנה."""
    info = validate_upload(cert_pem, key_pem)
    directory = Path(data_dir) / CONSOLE_TLS_DIRNAME
    error = _ssl_loads(cert_pem, key_pem, directory)
    if error:
        raise UploadRefused(f"השרת לא הצליח לטעון את הזוג ל-TLS: {error}")
    cert_path, key_path = directory / CERT_NAME, directory / KEY_NAME
    if cert_path.exists() and key_path.exists():
        # העתקה ולא העברה: עד הרגע האחרון יש על הדיסק זוג שלם שהשרת יעלה איתו.
        shutil.copy2(cert_path, cert_path.with_name(CERT_NAME + PREV_SUFFIX))
        write_credential_0600(key_path.with_name(KEY_NAME + PREV_SUFFIX),
                              key_path.read_text(encoding="utf-8"))
    # כל קובץ מוחלף אטומית; בין שתי ההחלפות הזוג אינו תואם, וזה החלון היחיד.
    write_credential_0600(key_path, key_pem.decode())
    write_atomic(cert_path, cert_pem)
    (directory / GENERATED_MARKER).unlink(missing_ok=True)
    return info


def reissue_self_signed(data_dir, hostname: str) -> dict:
    """הנפקה מחדש של תעודה **עצמית** (הכפתור "הנפקת תעודה חדשה" בהגדרות).

    ה-SAN נשמר כפי שהיה בתעודה על הדיסק (כתובת הקונסולה והשמות שכבר הוזנו) +
    ``hostname`` — כך שהכתובת שהמפעיל משתמש בה ממשיכה להיות מכוסה. הזוג הקודם נשמר
    כ-``.prev`` כמו בהעלאה; התעודה החדשה נטענת רק באתחול השרת. תעודה חתומה (שהועלתה)
    אינה מוחלפת כאן — ``UploadRefused``, שום קובץ לא השתנה."""
    from cryptography.x509.oid import ExtendedKeyUsageOID

    directory = Path(data_dir) / CONSOLE_TLS_DIRNAME
    cert_path, key_path = directory / CERT_NAME, directory / KEY_NAME
    if not (cert_path.exists() and key_path.exists()):
        raise UploadRefused("אין תעודה על הדיסק להחליף")
    old_pem = cert_path.read_bytes()
    if not _self_signed(old_pem):
        raise UploadRefused("התעודה הנוכחית חתומה בידי גורם חיצוני — מחליפים אותה ב\"העלאת תעודה\", "
                            "לא מנפיקים במקומה תעודה עצמית")
    sans = list(cert_sans(old_pem))
    if hostname and hostname not in sans:
        sans.append(hostname)
    cert_pem, key_pem = generate_self_signed(
        COMMON_NAME, ip_sans=[h for h in sans if _is_ip(h)],
        dns_sans=[h for h in sans if not _is_ip(h)],
        eku=(ExtendedKeyUsageOID.SERVER_AUTH,))
    error = _ssl_loads(cert_pem, key_pem, directory)
    if error:
        raise UploadRefused(f"השרת לא הצליח לטעון את הזוג ל-TLS: {error}")
    shutil.copy2(cert_path, cert_path.with_name(CERT_NAME + PREV_SUFFIX))
    write_credential_0600(key_path.with_name(KEY_NAME + PREV_SUFFIX),
                          key_path.read_text(encoding="utf-8"))
    write_credential_0600(key_path, key_pem.decode())
    write_atomic(cert_path, cert_pem)
    write_atomic(directory / GENERATED_MARKER, fingerprint_sha256(cert_pem) + "\n", encoding="ascii")
    return cert_details(cert_pem)
