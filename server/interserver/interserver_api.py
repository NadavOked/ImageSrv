"""Storage Nodes — כניסת ה-enrollment הבין-שרתי על המשני (#740, tracer 2.1).

זה הצד ש**מקבל** את הראשי: ‏pair-begin / pair-complete / ping מעל mTLS 1.3
עם channel-binding (RFC 9266) ו-SPKI-pin. שכבת ה-TLS עצמה מסתיימת ב-
terminator של pyOpenSSL (ראו ``interserver_auth`` — stdlib ssl אינו יכול
לקלוט תעודת-לקוח לא-מוכרת ואין לו exporter); כאן יושבת הלוגיקה שמעליו.

**האמון אינו מ-uvicorn ואינו מכותרות HTTP.** זהות ה-peer — גרסת ה-TLS,
תעודת-הלקוח וה-exporter — מגיעה מ-``tls_peer_provider`` שה-terminator
מזין, ולעולם לא מ-header שניתן לזייף. כל שומר נאכף **גם** בשכבת ה-service
(עיקרון 5): קריאה ישירה לפונקציה עם peer שגוי נכשלת בדיוק כמו דרך ה-route.

⚠️ **בלי ``from __future__ import annotations``** — האנוטציה ``Request``
ב-dependencies חייבת להיות אובייקט אמיתי ל-FastAPI, אחרת ה-endpoint מחזיר
422 (אותו באג של ``auth.py``/``boot/http.py``, ‏CLAUDE.md).
"""

import logging
import re
import secrets
import select
import shutil
import socket
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from server.console import auth
from server.storage import disk_reserve
from server.interserver import interserver_auth, storage_nodes
from server.db import journal, monitor_rekeyed, now_iso

log = logging.getLogger("imagesrv.interserver")

#: תוקף ה-handle של pair-begin עד pair-complete — קצר, אותה session.
HANDLE_TTL_SECONDS = 60
#: ‏#1122: כמה זמן מותר ל-handshake של ה-TLS להימשך. חיבור TCP שלא שולח
#: בייט (סורק, FW שחתך באמצע) שחרר את התהליכון שלו אחרי זה, לא לעולם.
HANDSHAKE_TIMEOUT_SECONDS = 10.0
#: ‏#1122: קוד תשובה בשם כשהמפתח של האב השתנה — הקונסולה בראשי מציגה אותו.
PEER_CERT_CHANGED = "peer_cert_changed"
PEER_CERT_CHANGED_DETAIL = "התעודה של הראשי השתנתה — יש לבצע רישום מחדש"


@dataclass
class TlsPeer:
    """זהות ה-peer כפי שה-terminator של ה-TLS ראה אותה — לא HTTP.

    ``tls_version`` כמו ``"TLSv1.3"``; ``cert_der`` תעודת-הלקוח הגולמית
    (או ``None`` אם לא הוצגה); ``exporter`` ערך ה-tls-exporter של החיבור."""
    tls_version: str | None
    cert_der: bytes | None
    exporter: bytes | None


class PairError(Exception):
    """כשל בכניסה הבין-שרתית, עם קוד HTTP מפורש (route מתרגם אותו כפי שהוא)."""

    def __init__(self, status: int, detail: str, *, code: str | None = None,
                 headers: dict | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code
        #: ‏#1456: כותרות שהתשובה חייבת לשאת (‏416 → ``Content-Range``).
        self.headers = headers or {}

    def payload(self) -> dict:
        """גוף התשובה: ``detail`` תמיד, ו-``code`` רק כשיש שם למצב (#1122)."""
        body = {"detail": self.detail}
        if self.code:
            body["code"] = self.code
        return body


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- שומרי ה-peer, נאכפים בשכבת ה-service -----------------------------------

def _require_secure_peer(peer: TlsPeer) -> None:
    """‏TLS 1.3 בדיוק **וגם** תעודת-לקוח נוכחת. ראיה חיובית: גרסה לא-מדווחת
    או ``None`` נדחית, לא "כנראה בסדר" (עיקרון 5, downgrade)."""
    if peer.tls_version != "TLSv1.3":
        raise PairError(421, "נדרש TLS 1.3 בדיוק")
    if not peer.cert_der:
        raise PairError(421, "נדרשת תעודת-לקוח (mTLS)")


def _require_protocol(version: str) -> None:
    if version != interserver_auth.PROTOCOL_VERSION:
        raise PairError(426, f"גרסת פרוטוקול לא נתמכת: {version!r}")


def _require_secondary(conn) -> None:
    if storage_nodes.role(conn) != storage_nodes.ROLE_SECONDARY:
        raise PairError(409, "כניסה בין-שרתית מותרת רק על שרת משנה (secondary)")


# --- pair-begin -------------------------------------------------------------

def pair_begin(conn, handles: dict, peer: TlsPeer, body: dict, *,
               data_dir=None, now: datetime | None = None) -> dict:
    """מתחיל pairing: מאמת mTLS 1.3, הצמדת SPKI הדדית, ומחזיר handle כרוך
    ל-session. **לפני** כל קוד — כדי שהצמדה שגויה תיכשל בלי לחשוף אותו."""
    now = now or _utcnow()
    _require_secure_peer(peer)
    _require_protocol(str(body.get("protocol_version")))
    _require_secondary(conn)
    if peer.exporter is None:
        raise PairError(421, "אין channel-binding (tls-exporter) לחיבור")

    ident = storage_nodes.identity(conn, data_dir=data_dir)
    expected = str(body.get("expected_secondary_spki") or "")
    # הצמדה הדדית: הראשי מצהיר איזה SPKI הוא מצפה שלמשני יש. אם אינו
    # תואם את שלנו — הראשי מדבר עם המכונה הלא נכונה. נדחה לפני הקוד.
    if not interserver_auth.verify_pinned_spki_value(ident["server_spki"], expected):
        raise PairError(400, "expected_secondary_spki אינו תואם את זהות שרת המשנה")

    primary_spki = interserver_auth.spki_sha256(peer.cert_der)
    primary_ref = interserver_auth.certificate_ref(peer.cert_der)
    handle = secrets.token_hex(16)
    server_nonce = secrets.token_hex(16)
    _sweep_handles(handles, now)
    handles[handle] = {
        "exporter": peer.exporter,
        "primary_ref": primary_ref,
        "primary_spki": primary_spki,
        "primary_id": str(body.get("primary_id") or ""),
        "client_nonce": str(body.get("client_nonce") or ""),
        "server_nonce": server_nonce,
        "protocol": interserver_auth.PROTOCOL_VERSION,
        "expires_at": now + timedelta(seconds=HANDLE_TTL_SECONDS),
    }
    return {
        "handle": handle,
        "secondary_id": ident["node_id"],
        "secondary_spki": ident["server_spki"],
        "server_nonce": server_nonce,
        "protocol_version": interserver_auth.PROTOCOL_VERSION,
    }


def _sweep_handles(handles: dict, now: datetime) -> None:
    for hid in [h for h, v in handles.items() if v["expires_at"] <= now]:
        del handles[hid]


# --- pair-complete ----------------------------------------------------------

def pair_complete(conn, handles: dict, peer: TlsPeer, body: dict, *,
                  now: datetime | None = None) -> dict:
    """משלים pairing על **אותה** session: מאמת את ה-handle, את הכריכה
    לערוץ (exporter) ולתעודת-הלקוח, את הקוד החד-פעמי ואת אב-יחיד; מנפיק
    את הטוקן פעם אחת. טרנזאקציה אחת עם אכיפת אב-יחיד ברמת ה-DB."""
    now = now or _utcnow()
    _require_secure_peer(peer)
    _require_protocol(str(body.get("protocol_version")))
    _require_secondary(conn)

    _sweep_handles(handles, now)
    handle = handles.get(str(body.get("handle") or ""))
    if handle is None:
        raise PairError(400, "handle לא קיים או פג")

    # channel-binding: complete חייב לרוץ על אותה session TLS כמו begin.
    # ‏begin על A, complete על B → exporters שונים → נדחה (RFC 9266).
    if not _exporter_matches(peer.exporter, handle["exporter"]):
        raise PairError(400, "אי-התאמת channel-binding — begin ו-complete על session שונה")
    # אותה תעודת-לקוח בדיוק שפתחה את ה-handle.
    if not _cert_binding_ok(interserver_auth.certificate_ref(peer.cert_der),
                            handle["primary_ref"]):
        raise PairError(400, "תעודת-הלקוח שונה מזו של pair-begin")

    code = str(body.get("code") or "")
    if not interserver_auth.verify_and_consume_code(conn, code, now=now):
        raise PairError(401, "קוד pairing שגוי, פג, או שאין חלון פתוח")

    token = interserver_auth.generate_token()
    try:
        storage_nodes.record_parent(
            conn,
            parent_id=handle["primary_id"] or handle["primary_spki"][:16],
            token_hash=interserver_auth.hash_token(token),
            bound_cert_ref=handle["primary_ref"],
            pinned_parent_spki=handle["primary_spki"],
            protocol_version=interserver_auth.PROTOCOL_VERSION,
        )
    except storage_nodes.ParentAlreadyEnrolledError:
        raise PairError(409, "כבר קיים אב רשום — נתקו אותו תחילה (revoke-first)")
    del handles[str(body["handle"])]
    return {
        "token": token,
        "parent_id": handle["primary_id"] or handle["primary_spki"][:16],
        "protocol_version": interserver_auth.PROTOCOL_VERSION,
    }


def _exporter_matches(a: bytes | None, b: bytes | None) -> bool:
    if a is None or b is None:
        return False
    return secrets.compare_digest(a, b)


def _cert_binding_ok(peer_ref: str, bound_ref: str) -> bool:
    """השוואת הפניית תעודת-הלקוח (RFC 8705, thumbprint מלא)."""
    return secrets.compare_digest(peer_ref, bound_ref)


def _peer_bound(peer_cert_der: bytes, cred) -> bool:
    """‏**נקודת הבקרה השלילית המכריעה של #740** (RFC 8705 sender-constraint).

    שני תנאים: ה-SPKI של הפונה תואם את ה-pin (המפתח הנכון), **וגם**
    ה-thumbprint של התעודה תואם את ``bound_cert_ref`` (אותה תעודה בדיוק).
    אם מנטרלים את הבדיקה הזו, טוקן גנוב עם תעודת-לקוח אחרת עובר — וזו
    ההוכחה שהכריכה היא שמגינה, לא הטוקן לבדו (ראו הטסט המכריע)."""
    if not interserver_auth.verify_pinned_spki(peer_cert_der, cred["pinned_parent_spki"]):
        return False
    return _cert_binding_ok(interserver_auth.certificate_ref(peer_cert_der),
                            cred["bound_cert_ref"])


# --- אימות שלאחר-ה-pairing (ping, אימג'ים, מכונות, מוניטור) -----------------

def authenticate_parent(conn, peer: TlsPeer, *, token: str, protocol_version: str,
                        has_console_cookie: bool = False):
    """השומר המשותף לכל קריאה מאומתת של האב: ‏TLS 1.3, תעודת-לקוח כרוכה
    לטוקן, SPKI תואם ה-pin, בלי console cookie, ותפקיד secondary.
    אי-התאמת SPKI/תעודה = 401 קשה, בלי TOFU. מחזיר את רשומת האב.
    עיקרון 5: נאכף בשכבת ה-service, לא רק ב-route."""
    if has_console_cookie:
        raise PairError(401, "עוגיית קונסולה אינה תקפה בערוץ הבין-שרתי")
    _require_secure_peer(peer)
    _require_protocol(protocol_version)
    _require_secondary(conn)

    cred = conn.execute(
        "SELECT parent_id, token_hash, bound_cert_ref, pinned_parent_spki,"
        " protocol_version FROM parent_credentials WHERE singleton = 1"
    ).fetchone()
    if cred is None:
        raise PairError(401, "אין אב רשום")

    # הצמדת SPKI+תעודה שלאחר-ה-pairing (RFC 8705). **מפתח** אחר = כשל קשה
    # 401 בשם (``peer_cert_changed``), בלי TOFU — בלי הכריכה, טוקן גנוב היה
    # מספיק. תעודה **מחודשת על אותו מפתח** (#1122) היא מצב אחר: ה-handshake
    # של TLS 1.3 כבר הוכיח (CertificateVerify) שהפונה מחזיק את המפתח הפרטי
    # שה-SPKI מוצמד אליו, ולכן הכריכה לתעודה עצמה אינה מוסיפה הגנה — היא
    # מאומצת, אבל **רק אחרי** שהטוקן אומת, ועם שורת יומן.
    bound = _peer_bound(peer.cert_der, cred)
    if not bound and not interserver_auth.verify_pinned_spki(
            peer.cert_der, cred["pinned_parent_spki"]):
        raise PairError(401, PEER_CERT_CHANGED_DETAIL, code=PEER_CERT_CHANGED)
    if not interserver_auth.verify_token(token or "", cred["token_hash"]):
        raise PairError(401, "טוקן שגוי")
    if not bound:
        new_ref = interserver_auth.certificate_ref(peer.cert_der)
        storage_nodes.rebind_parent_cert(conn, bound_cert_ref=new_ref)
        journal(conn, "storage_parent_cert_renewed",
                f"{cred['parent_id']}: {cred['bound_cert_ref'][:16]} → {new_ref[:16]}", "")
        log.info("interserver: parent %s renewed its certificate (same key) — adopted",
                 cred["parent_id"])
        cred = dict(cred)
        cred["bound_cert_ref"] = new_ref
    # #1564: every parent request converges here after TLS, protocol, role,
    # SPKI/certificate binding and token authentication have all succeeded.
    storage_nodes.record_parent_contact(conn)
    return cred


def ping(conn, peer: TlsPeer, *, token: str, protocol_version: str,
         has_console_cookie: bool = False) -> dict:
    """‏ping מאומת — ראו :func:`authenticate_parent`."""
    cred = authenticate_parent(conn, peer, token=token,
                               protocol_version=protocol_version,
                               has_console_cookie=has_console_cookie)
    return {
        "protocol_version": interserver_auth.PROTOCOL_VERSION,
        "node_id": storage_nodes.identity(conn)["node_id"],
        "role": storage_nodes.ROLE_SECONDARY,
        "parent_id": cred["parent_id"],
        "server_version": _own_version(),   # #1300: הראשי מציג אותו בכרטיס הסניפים
        "ok": True,
    }


def _own_version() -> str | None:
    """גרסת התוכנה של השרת הזה (תג git), כפי ש-/me מדווח. ‏None = אין תג —
    הראשי מציג "לא ידועה", לא מספר שאין לו מקור."""
    from server.core.agent_version import server_version
    return server_version()


# --- קליטת אימג' מהאב (#655 v1, העברה ראשי→משני) -----------------------------
#
# הגוף הוא ה-tar של תיקיית האימג' (``archive.tar_stream``), והכניסה לספרייה
# היא ``archive.import_tar_stream`` — אותם שומרים של העלאה מהדפדפן: חילוץ
# לאזור ביניים, ‏sha256 של כל מחיצה מול המניפסט, ורק אז ``rename`` לספרייה
# (עיקרון 6). ‏#1457: החילוץ וה-sha256 נעשים **תוך כדי** קבלת הזרם — בלי קובץ
# ``.transfer``, בלי העתקה ובלי קריאה שנייה — ולכן אחרי הבייט האחרון נשארת
# רק הכניסה לספרייה, והראשי לא מחכה לאימות של דקות.
# קטוע/לא-תואם — נמחק ומדווח בגלוי; לעולם לא תיקייה חלקית בשם אימג'.
#
# שני שלבים, כי הגוף הוא עשרות ג'יגה: ``receive_image_precheck`` רץ **לפני**
# שבייט אחד של גוף נקרא (אימות, תפקיד, מזהה, "כבר קיים", מקום פנוי) —
# הראשי שולח ``Expect: 100-continue`` ומחכה ל-``100`` — ואז
# ``receive_image`` מחלץ את הזרם לאזור הביניים ומכניס לספרייה.

#: כמה מקום פנוי דורשים לפני קליטה: החילוץ (פעם אחת — אין קובץ זמני, #1457)
#: + שוליים. ה-tar ארוך מסכום הקבצים שבו (כותרות וריפוד), ולכן ``length``
#: לבדו כבר מכסה את מה שייכתב.
FREE_SPACE_FACTOR = 1
FREE_SPACE_MARGIN = 256 * 1024 * 1024
#: תקרה לגוף JSON בערוץ הבין-שרתי (pair/ping/מכונות): לא זרם, לא ג'יגה.
MAX_JSON_BODY = 1024 * 1024


@dataclass
class ReceivePlan:
    """מה שנקבע ב-precheck ומועבר לקליטה עצמה."""
    image_id: str
    content_length: int
    root: Path


@dataclass
class FilePlan:
    """קובץ מחיצה להגשה לאב, אחרי אימות ורק אם השם במניפסט."""
    path: Path
    status: int
    start: int
    end: int
    total: int

    @property
    def length(self) -> int:
        if self.total <= 0:
            return 0
        return self.end - self.start + 1


def _library(ctx):
    library = getattr(ctx, "library", None)
    if library is None:
        raise PairError(503, "אין ספריית אימג'ים בשרת הזה")
    return library


def image_present(ctx, peer: TlsPeer, *, token: str, protocol_version: str,
                  image_id: str, has_console_cookie: bool = False) -> dict:
    """האם האימג' כבר בספריית המשני — מהדיסק (עיקרון 3), לא מטבלה."""
    from server.library.images import valid_image_id
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    if not valid_image_id(image_id):
        raise PairError(400, f"מזהה אימג' לא תקין: {image_id!r}")
    manifest = _library(ctx).get(image_id)
    return {"id": image_id, "present": manifest is not None,
            "name": manifest["name"] if manifest else None}


def _public_manifest(manifest: dict) -> dict:
    """מניפסט בלי שדות פנימיים (`_dir` וכו') — מה שעובר בערוץ."""
    return {k: v for k, v in manifest.items() if not str(k).startswith("_")}


def _decoded_filename(name: str) -> str | None:
    """שם קובץ בטוח להגשה, או None — לא נתיב, לא `..`, לא ריק."""
    from urllib.parse import unquote
    decoded = unquote(name or "")
    if not decoded or decoded in {".", ".."}:
        return None
    if "/" in decoded or "\\" in decoded or "\0" in decoded:
        return None
    return decoded


def list_images(ctx, peer: TlsPeer, *, token: str, protocol_version: str,
                has_console_cookie: bool = False) -> list:
    """רשימת האימג'ים בספריית המשני — מהדיסק (עיקרון 3), לא מטבלה."""
    from server.library.images import guarded_image_path, image_os, streamed_partitions
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    rows = []
    for manifest in _library(ctx).scan().values():
        if not manifest.get("_available", True) or not manifest.get("_dir"):
            continue
        directory = Path(manifest["_dir"])
        sha = {}
        size: int | None = 0
        for part in streamed_partitions(manifest):
            name = part["file"]
            sha[name] = part.get("sha256") or ""
            # ‏#2160: ``size_bytes`` הוא סכום של **כל** הקבצים או ``None`` — קובץ
            # שלא נמדד (נעלם, סימלינק, stat נכשל) לא נספר כאפס. סכום חלקי נראה
            # כגודל שנמדד, והראשי היה בודק מקום פנוי מול מספר קטן מדי; ``None``
            # גורם לו להיכשל בשם (`_check_pull_space`, "לא דיווח את הגודל").
            try:
                path = guarded_image_path(directory, name)
                measured = path.stat().st_size if path is not None else None
            except OSError:
                measured = None
            size = size + measured if size is not None and measured is not None else None
        rows.append({
            "id": manifest["id"],
            "name": manifest["name"],
            "size_bytes": size,
            "sha256": sha,
            "created_at": manifest.get("created") or "",
            "manifest_summary": {
                "family": manifest["family"],
                "os": image_os(manifest),
                "partitions": len(manifest.get("partitions") or []),
                "folder": manifest.get("folder") or "",
            },
        })
    rows.sort(key=lambda r: (r["name"], r["id"]))
    return rows


def image_manifest(ctx, peer: TlsPeer, *, token: str, protocol_version: str,
                   image_id: str, has_console_cookie: bool = False) -> dict:
    """המניפסט המלא של אימג' במשני — מהדיסק. חסר → 404."""
    from server.library.images import valid_image_id
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    if not valid_image_id(image_id):
        raise PairError(400, f"מזהה אימג' לא תקין: {image_id!r}")
    manifest = _library(ctx).get(image_id)
    if manifest is None:
        raise PairError(404, "אימג' לא קיים")
    return _public_manifest(manifest)


def parse_byte_range(header: str | None, size: int) -> tuple[int, int, int]:
    """מפרק ``Range: bytes=`` ל-``(status, start, end_inclusive)``.

    קובץ ריק: ``end == -1``. טווח לא ניתן לסיפוק → 416. כותרת חסרה או
    לא-bytes → הקובץ כולו (200). רק הטווח הראשון נספר.
    """
    if size < 0:
        size = 0
    if not header or not str(header).strip():
        return 200, 0, size - 1
    raw = str(header).strip()
    if not raw.lower().startswith("bytes="):
        return 200, 0, size - 1
    spec = raw.split("=", 1)[1].split(",")[0].strip()
    if "-" not in spec:
        return 200, 0, size - 1
    start_s, end_s = spec.split("-", 1)
    try:
        if start_s == "":
            n = int(end_s) if end_s else 0
            if n <= 0 or size == 0:
                return 200, 0, size - 1
            start = max(size - n, 0)
            return (206 if start > 0 else 200), start, size - 1
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    except ValueError:
        return 200, 0, size - 1
    if start < 0 or start >= size:
        # ‏#1456: הגודל בתשובה (RFC 9110) — הראיה של הראשי שהקובץ שלו שלם.
        raise PairError(416, f"טווח לא ניתן לסיפוק: {header}",
                        headers={"Content-Range": f"bytes */{size}"})
    end = min(max(end, start), size - 1)
    if start == 0 and end == size - 1:
        return 200, 0, end
    return 206, start, end


def image_file_plan(ctx, peer: TlsPeer, *, token: str, protocol_version: str,
                    image_id: str, filename: str, range_header: str | None = None,
                    has_console_cookie: bool = False) -> FilePlan:
    """קובץ מחיצה להגשה — רק שם שמופיע במניפסט (לא נתיב חופשי)."""
    from server.library.images import inside, valid_image_id
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    if not valid_image_id(image_id):
        raise PairError(400, f"מזהה אימג' לא תקין: {image_id!r}")
    decoded = _decoded_filename(filename)
    if decoded is None:
        raise PairError(404, "קובץ לא קיים")
    library = _library(ctx)
    path = library.file_path(image_id, decoded)
    if path is None:
        raise PairError(404, "קובץ לא קיים")
    manifest = library.get(image_id)
    if manifest is None or not manifest.get("_dir"):
        raise PairError(404, "קובץ לא קיים")
    if inside(path, Path(manifest["_dir"])) is None:
        raise PairError(404, "קובץ לא קיים")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PairError(404, f"לא ניתן לקרוא את הקובץ: {exc}")
    status, start, end = parse_byte_range(range_header, size)
    return FilePlan(path=path, status=status, start=start, end=end, total=size)


def receive_image_precheck(ctx, peer: TlsPeer, *, token: str, protocol_version: str,
                           image_id: str, content_length: object,
                           location_id: str = "loc_local",
                           has_console_cookie: bool = False) -> ReceivePlan:
    """כל מה שאפשר לדחות **לפני** קריאת הגוף."""
    from server.library.images import valid_image_id
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    library = _library(ctx)
    if not valid_image_id(image_id):
        raise PairError(400, f"מזהה אימג' לא תקין: {image_id!r}")
    if library.get(image_id) is not None:
        raise PairError(409, f"האימג' {image_id} כבר קיים בספריית המשני")
    try:
        length = int(content_length)
    except (TypeError, ValueError):
        raise PairError(411, "נדרש Content-Length מספרי")
    if length <= 0:
        raise PairError(411, "נדרש Content-Length חיובי")
    from server.storage import storage_locations
    location = storage_locations.get(ctx.conn, location_id)
    if location is None and location_id == storage_locations.LOCAL_ID:
        # תאימות ל-contexts ישנים/מבודדים; בשרת מלא ensure_local יצר שורה.
        root = Path(library.root)
    elif location is None:
        raise PairError(404, f"מיקום האחסון {location_id!r} לא קיים במשני")
    elif not storage_locations.location_writable(ctx.conn, location_id):
        raise PairError(409, f"מיקום האחסון {location_id!r} אינו זמין לקליטה")
    else:
        if location_id != storage_locations.LOCAL_ID \
                and not Path(location["mount_point"]).is_dir():
            raise PairError(409, f"מיקום האחסון {location_id!r} אינו מעוגן")
        root = storage_locations.images_dir(location)
    root.mkdir(parents=True, exist_ok=True)
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        # לא הצלחנו לבדוק ≠ יש מקום (עיקרון 5).
        raise PairError(507, f"לא ניתן לקרוא את המקום הפנוי בספרייה: {exc}")
    # ‏#1737: ועוד הרזרבה למערכת — האימג' לא ייכנס אם ישאיר פחות ממנה.
    keep = disk_reserve.reserve()
    need = length * FREE_SPACE_FACTOR + FREE_SPACE_MARGIN + keep
    if free < need:
        raise PairError(507, _space_refusal(ctx, library, location_id,
                                            need=need, keep=keep, free=free))
    return ReceivePlan(image_id=image_id, content_length=length, root=root)


def _space_refusal(ctx, library, location_id: str, *, need: int, keep: int,
                   free: int) -> str:
    """‏#1697: ה-507 אומר כמה נדרש, כמה פנוי **בכל** מיקום אחסון במשני, ולאן כן
    אפשר להעביר. ההודעה עוברת כמו שהיא לשורת ההעברה בקונסולת הראשי — שם
    המפעיל בוחר את המיקום, ובלי הרשימה הוא רואה "אין מקום" בזמן שבדף האחסון
    של המשני יש דיסק פנוי. אין בחירה אוטומטית: היעד נשאר מה שהמפעיל בחר.
    מיקום שלא נמדד נקוב כ"לא נמדד" ואינו מוצע (עיקרון 5)."""
    from server.storage import storage_locations as sl
    rows = sl.list_rows(ctx.conn) if ctx.conn is not None else []
    if not any(r["id"] == sl.LOCAL_ID for r in rows):
        # אותו מקרה כמו ב-precheck: אין שורה ל-loc_local — הוא ``--images``.
        rows.insert(0, {"id": sl.LOCAL_ID, "name": sl.LOCAL_NAME, "type": "local",
                        "mount_point": str(library.root), "state": "connected"})
    gb = disk_reserve.gb
    parts, fits, selected = [], [], location_id
    for row in rows:
        label = f'"{row["name"]}" ({row["id"]})'
        if row["id"] == location_id:
            selected = label
            room: int | str = free
        elif row["state"] != "connected":
            room = f"לא מחובר ({sl.STATE_LABELS.get(row['state'], row['state'])})"
        elif row["type"] != "local" and not Path(row["mount_point"]).is_dir():
            room = "לא מעוגן"
        else:
            target = sl.images_dir(row)
            try:
                room = shutil.disk_usage(target if target.is_dir()
                                         else row["mount_point"]).free
            except OSError as exc:
                room = f"לא נמדד: {exc}"
        if isinstance(room, str):
            parts.append(f"{label} — {room}")
        elif room >= need:
            parts.append(f"{label} — פנויים {gb(room)}, מספיק")
            fits.append(label)
        else:
            parts.append(f"{label} — פנויים {gb(room)}, חסרים {gb(need - room)}")
    advice = (f"אפשר להעביר אל: {', '.join(fits)} — פתחו העברה חדשה ובחרו אותו."
              if fits else "אין בשרת המשנה מיקום שהאימג' נכנס אליו — יש לפנות מקום או "
                           "לחבר לשרת המשנה מיקום אחסון גדול יותר.")
    return (f"אין די מקום פנוי בשרת המשנה במיקום {selected}: נדרשים {need} בייטים "
            f"({gb(need)}, מתוכם {gb(keep)} נשמרים למערכת), פנויים {free} בייטים "
            f"({gb(free)}). פנוי בכל מיקום בשרת המשנה: {' · '.join(parts)}. {advice}")


class _BodyTooLong(Exception):
    """הגוף עבר את ה-Content-Length המוצהר."""


class _BodyReader:
    """‏``read(n)`` מעל מנות הגוף, עם ספירה מול Content-Length (‏#1457):
    ‏``tarfile`` במצב זרם קורא ממנו, ו-``received`` הוא הראיה לכמה הגיע."""

    def __init__(self, chunks: Iterable[bytes], limit: int):
        self._chunks = iter(chunks)
        self._buf = b""
        self.limit = limit
        self.received = 0

    def read(self, size: int = -1) -> bytes:
        while not self._buf:
            chunk = next(self._chunks, None)
            if chunk is None:
                return b""
            self.received += len(chunk)
            if self.received > self.limit:
                raise _BodyTooLong()
            self._buf = bytes(chunk)
        if size is None or size < 0 or size >= len(self._buf):
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:size], self._buf[size:]
        return out

    def drain(self) -> None:
        """קורא ומשליך את שארית הגוף — כדי שהספירה תהיה שלמה, וכדי שסיבת
        דחייה תגיע לראשי כתשובה ולא כ"נותק" באמצע השליחה."""
        while self.read(1024 * 1024):
            pass


def receive_image(ctx, plan: ReceivePlan, chunks: Iterable[bytes]) -> dict:
    """מחלץ את הזרם ישר לאזור ביניים **בתוך** הספרייה (אותה מערכת קבצים),
    עם sha256 תוך כדי כתיבה (``import_tar_stream``, ‏#1457), ומכניס רק אחרי
    שכל מחיצה תאמה למניפסט. מחזיר את המניפסט שנקלט."""
    from server.library.archive import ArchiveError, import_tar_stream, unreadable_message
    from server.library.images import validate_display_name
    library = _library(ctx)
    body = _BodyReader(chunks, plan.content_length)
    failure: PairError | None = None
    manifest = None
    try:
        try:
            manifest = import_tar_stream(body, plan.root, set(library.scan()),
                                         max_bytes=plan.content_length)
        except ArchiveError as exc:
            failure = PairError(400, f"האימג' נדחה בקליטה: {exc}")
        except _BodyTooLong:
            raise
        except Exception as exc:                       # noqa: BLE001 — tar פגום
            failure = PairError(400, unreadable_message(exc))
        body.drain()        # ריפוד אחרי סוף הארכיון, או שארית אחרי דחייה
    except _BodyTooLong:
        failure = PairError(400, "הגוף ארוך מ-Content-Length המוצהר")
    if body.received < plan.content_length:
        # קטוע = כשל בשם, עם המספרים (עיקרון 4: אין זריקת בייטים בשקט). קודם
        # לכל סיבה אחרת: tar שנקטע באמצע נראה גם כ"ארכיון פגום".
        failure = PairError(400, f"ההעברה נקטעה: התקבלו {body.received} מתוך "
                                 f"{plan.content_length} בייטים")
    if failure is not None:
        if manifest is not None:
            library.delete(manifest["id"])
        raise failure
    # המניפסט נקרא מהזרם; מזהה שאינו זה שבנתיב הוא אימג' אחר שנכנס בשם
    # מטעה — מוסר מהספרייה ונדחה בגלוי. אותה בדיקת-שם כמו העלאה (#138).
    try:
        if manifest["id"] != plan.image_id:
            raise ValueError(f"המניפסט מצהיר על {manifest['id']!r} ולא על "
                             f"{plan.image_id!r}")
        validate_display_name(manifest["name"], "שם האימג'")
        if manifest.get("folder"):
            validate_display_name(manifest["folder"], "שם התיקייה")
    except ValueError as exc:
        library.delete(manifest["id"])
        raise PairError(400, str(exc))
    journal(ctx.conn, "storage_image_received",
            f'{manifest["id"]} "{manifest["name"]}" {plan.content_length}',
            "")
    return {"ok": True, "id": manifest["id"], "name": manifest["name"],
            "bytes": plan.content_length}


# --- צפייה מהאב: רשימת המכונות של המשני (#655 v1) -----------------------------

def list_machines(ctx, peer: TlsPeer, *, token: str, protocol_version: str,
                  has_console_cookie: bool = False) -> dict:
    """מה שדף המוניטור של המשני מראה — ‏``monitor.machine_rows`` — לאב
    המאומת. קריאה בלבד; ``node_id`` מצורף כדי שהאב ידע ממי קיבל."""
    from server.console.monitor import machine_rows
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    return {"node_id": storage_nodes.identity(ctx.conn)["node_id"],
            "server_version": _own_version(),   # #1300
            "machines": machine_rows(ctx.conn)}


# --- הקונסולה של המשני מהאב: GET מרשימה מותרת (#1179) -------------------------
#
# הראשי מצייר את העמודים של המשני **באותן פונקציות** שמציירות את שלו — ולכן
# צריך את אותן תשובות JSON של ``/api/console/…``. המשני מריץ את ה-GET בתוך
# התהליך מול אפליקציית הקונסולה שלו, בזהות ``primary:<user>``/admin שנקבעת
# ב-scope (``auth.PEER_SCOPE_KEY``) **אחרי** ``authenticate_parent``, ומחזיר
# מעטפת ``{status, body, headers}``. רק GET, ורק נתיב שמתאים לשורה ב-
# ``CONSOLE_GETS``; לכל שורה הטיפוס העליון שהנתיב מחזיר — הראשי בודק אותו.

#: ‏(ביטוי לנתיב המלא אחרי ``/api/console``, הטיפוס העליון של תשובה 200).
#: בלי ``/me``, הורדות/CSV/גיבוי, ``…/probe`` (שולח DHCP לרשת) ו-``…/disk``.
CONSOLE_GETS: tuple[tuple[str, type], ...] = (
    (r"/overview", dict), (r"/machines", list), (r"/groups", list),
    (r"/disk-failures", list), (r"/shrink-records", list),
    (r"/net", list), (r"/net/config", dict), (r"/net/deploy", dict),
    (r"/net/interfaces", list), (r"/net/interfaces/[^/]+/leases", dict),
    (r"/room", dict), (r"/room/history", list), (r"/room/driver-models", list),
    (r"/images", list), (r"/images/[^/]+", dict), (r"/folders", list), (r"/tasks", list),
    (r"/health", list), (r"/ports", list),
    (r"/storage-locations", dict), (r"/storage-locations/[^/]+", dict),
    (r"/drivers", list), (r"/monitor/machines", list), (r"/monitor/settings", dict),
    (r"/users", list), (r"/journal", list), (r"/journal/events", list),
    (r"/settings", dict), (r"/update", dict), (r"/update/status", dict),
    # מה-gate (11/10): כרטיסי TLS ושם המארח בהגדרות, ומי מחובר לקונסולה (דף הרשת).
    (r"/tls", dict), (r"/hostname", dict), (r"/sessions", list),
    # ‏#1932: שורת "אזור זמן" בהגדרות של המשני (קריאה; הכתיבה ב-``CONSOLE_WRITES``).
    (r"/timezone", dict),
    # המשני עונה 409 משלו (אינו מנהל ילדים) — העמוד מקבל את תשובת המשני.
    (r"/storage-nodes", list), (r"/storage-node-groups", list), (r"/storage-transfers", list),
)
#: ‏#1179 שלב 2: כתיבה מהראשי — רשימה נפרדת, ‏(שיטה, ביטוי לנתיב), לפי הסדר שנקבע:
#: משתמשים → הגדרות → רשת. **מחוץ לרשימה במכוון:** ‏``/users/{n}/reset-password``
#: (הסיסמה החדשה חוזרת בתשובה, ו-``defang`` היה משבש בה תווים כמו ``&``), ‏``/ports/…``
#: (כיבוי 8443 במשני מנתק אותו מהראשי — ואין דרך להדליק בחזרה מכאן), מחיקת אימג'/
#: מכונה וכל העלאה.
CONSOLE_WRITES: tuple[tuple[str, str], ...] = (
    # שליטת הראשי בסבב של המשני (הכרעת נדב 17/10): פתיחה ועצירה בלבד.
    # start/wake/answer/poweroff אינם נפתחים אגב כך — הרשימה היא (שיטה, נתיב).
    ("POST", r"/room"), ("POST", r"/room/close"),
    ("POST", r"/users"), ("PUT", r"/users/[^/]+"), ("DELETE", r"/users/[^/]+"),
    ("POST", r"/settings"),
    # ‏#1932: אזור הזמן של המשני — הגדרה כמו ``/settings`` (‏timedatectl + ההגדרה במשני עצמו).
    ("POST", r"/timezone"),
    # רשת: כרטיסים ו-DHCP (‏/net/interfaces) וכתובות (‏/net/config). שינוי כתובת שמנתק
    # את המשני מהראשי חוזר לבד: אישור ההחזרה (‏/net/config/confirm) לא יגיע, וה-rollback
    # של המשני (#1263) מחזיר. ‏…/probe (שולח DHCP לרשת) אינו כאן — הוא GET שאינו ברשימה.
    ("POST", r"/net/interfaces"), ("PUT", r"/net/interfaces/[^/]+"),
    ("PUT", r"/net/interfaces/[^/]+/description"), ("DELETE", r"/net/interfaces/[^/]+"),
    ("POST", r"/net/config/[^/]+/preview"), ("PUT", r"/net/config/[^/]+"),
    ("PUT", r"/net/config/[^/]+/deploy"), ("PUT", r"/net/config/[^/]+/second"),
    ("POST", r"/net/config/confirm"),
    # ‏#1776 (נדב 19/10: "אני מתל אביב רוצה לעדכן את חיפה"): לשונית העדכון של המשני בעץ
    # של הראשי — בדיקה והחלה, במסלול החתום של המשני עצמו (server-upgrade.sh מסרב לתג
    # שאינו חתום, והסיבה חוזרת ב-GET /update/status). ‏/update/revert אינו כאן: הוא דורש
    # את שם השרת **של המשני**, והקונסולה של הראשי שולחת את שם הראשי.
    ("POST", r"/update/check"), ("POST", r"/update/apply"),
)
#: ‏#1776: תקרת בקשה ארוכה מ-``REQUEST_TIMEOUT`` (15 ש') לכתיבות שהמשני עונה עליהן
#: לאט מטבען. ‏``/update/check`` מריץ אצלו ls-remote (20 ש') + הורדת התג ובדיקת החתימה
#: (60+60 ש'); ‏``/update/apply`` ממתין ל-systemd-run (15 ש') ולקריאת הגרסה. בלי זה
#: הראשי היה מכריז "לא ידוע אם בוצעה" על בדיקה תקינה שפשוט לוקחת דקה.
#: ‏``/timezone`` (#1932): ‏timedatectl set-timezone (עד 20 ש') ואז קריאה חוזרת (עד 10 ש').
CONSOLE_WRITE_TIMEOUTS: dict[str, float] = {"/update/check": 180.0, "/update/apply": 60.0,
                                            "/timezone": 45.0}
#: תקרה לגוף בקשת כתיבה מהראשי — טופס, לא קובץ.
MAX_CONSOLE_WRITE_BODY = 64 * 1024
#: כותרות תשובה שהעמודים קוראים (זמן מדידה, "החיפוש נקטע") — עוברות בשמן.
CONSOLE_HEADERS = ("x-health-checked-at", "x-journal-search-truncated", "x-journal-total")
_ACTING_USER = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
#: נתיב שעובר כפי שהוא, בלי פענוח אחר-כך: בלי ``%`` (אין ``%2e%2e``/``%2f``/קידוד
#: כפול שהופכים לנתיב אחר אחרי unquote), בלי ``\``, בלי רווח — רק תווים שאינם
#: משתנים בפענוח. הבדיקה והבקשה רצות על **אותה** מחרוזת.
_SAFE_PATH = re.compile(r"(?:/[A-Za-z0-9._~-]+)+")
#: תקרה לגוף שהקונסולה של המשני מחזירה, לפני שהוא נאסף לזיכרון.
MAX_CONSOLE_BODY = 16 * 1024 * 1024
_QUERY = re.compile(r"^[A-Za-z0-9._~%&=+-]{0,512}$")


def console_get_type(path: str) -> type | None:
    """הטיפוס העליון של ``path`` אם הוא ברשימה המותרת, אחרת ``None``. נתיב עם
    ``%``, לוכסן הפוך או מקטע ``.``/``..`` נדחה **לפני** ההתאמה — אין דרך לפענח אותו לאחר."""
    if not _SAFE_PATH.fullmatch(path or "") or any(
            seg in (".", "..") for seg in path.split("/")):
        return None
    for pattern, kind in CONSOLE_GETS:
        if re.fullmatch(pattern, path):
            return kind
    return None


def console_write_allowed(method: str, path: str) -> bool:
    """‏``(method, path)`` ברשימת הכתיבה — אחרי אותה בדיקת נתיב של ה-GET (בלי ``%``/``..``)."""
    if not _SAFE_PATH.fullmatch(path or "") or any(seg in (".", "..") for seg in path.split("/")):
        return False
    return any(method == m and re.fullmatch(p, path) for m, p in CONSOLE_WRITES)


def run_console_get(app, path: str, query: str, user: str) -> tuple[int, dict, object]:
    """GET אחד מול אפליקציית הקונסולה, בתוך התהליך → ``(status, headers, body)``."""
    if console_get_type(path) is None:          # גם כאן — שום קורא אינו עוקף את הבדיקה
        return 404, {}, {"detail": "הנתיב אינו מורשה בערוץ הבין-שרתי"}
    return _run_console(app, "GET", path, query, user, b"")


def run_console_write(app, method: str, path: str, query: str, user: str,
                      body: bytes) -> tuple[int, dict, object]:
    """‏#1179 שלב 2: כתיבה אחת מהרשימה, בתוך התהליך — אותה זהות, אותו מסלול."""
    if not console_write_allowed(method, path):
        return 404, {}, {"detail": "הפעולה אינה מורשית בערוץ הבין-שרתי"}
    if len(body) > MAX_CONSOLE_WRITE_BODY:
        return 413, {}, {"detail": "גוף הבקשה גדול מהתקרה"}
    return _run_console(app, method, path, query, user, body)


def _run_console(app, method: str, path: str, query: str, user: str,
                 body: bytes) -> tuple[int, dict, object]:
    """בקשה אחת מול אפליקציית הקונסולה, בתוך התהליך → ``(status, headers, body)``.

    דרך ``app`` המלאה (ה-handlers וה-middleware של FastAPI, כמו בבקשה רגילה);
    הזהות היא ``(user, "admin")`` ב-scope. גוף שאינו JSON → ``{"detail": …}``."""
    import asyncio
    import json
    request_headers = [(b"host", b"interserver")]
    if body:
        request_headers += [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii"))]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "https",
             "path": "/api/console" + path,
             "raw_path": ("/api/console" + path).encode("ascii"),
             "root_path": "", "query_string": query.encode("ascii"),
             "headers": request_headers,
             "client": ("127.0.0.1", 0), "server": ("interserver", 0),
             auth.PEER_SCOPE_KEY: (user, "admin")}
    sent: list[dict] = []
    size = [0]

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body":
            size[0] += len(message.get("body", b""))
            if size[0] > MAX_CONSOLE_BODY:
                raise OverflowError("גוף התשובה גדול מהתקרה")
        sent.append(message)

    try:
        asyncio.run(app(scope, receive, send))
    except Exception as exc:                                 # noqa: BLE001
        # ‏ServerErrorMiddleware שולח 500 ואז זורק שוב — התשובה כבר אצלנו.
        log.warning("interserver: console %s %s raised — %s", method, path, exc)
    if size[0] > MAX_CONSOLE_BODY:
        return 500, {}, {"detail": f"תשובת הקונסולה גדולה מ-{MAX_CONSOLE_BODY} בייטים"}
    start = next((m for m in sent if m["type"] == "http.response.start"), None)
    if start is None:
        return 500, {}, {"detail": "הקונסולה של שרת המשנה לא החזירה תשובה"}
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    headers = {k.decode("latin-1").lower(): v.decode("latin-1")
               for k, v in start.get("headers", [])}
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = {"detail": raw[:200].decode("utf-8", "replace")}
    return start["status"], {k: headers[k] for k in CONSOLE_HEADERS if k in headers}, body


def console_get(ctx, app, peer: TlsPeer, *, path: str, query: str, acting_user: str,
                token: str, protocol_version: str, has_console_cookie: bool = False) -> dict:
    """GET מהרשימה המותרת בשם ``primary:<acting_user>``. האימות **לפני** הנתיב."""
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    if app is None:
        raise PairError(503, "הקונסולה של שרת המשנה אינה מחוברת לערוץ הבין-שרתי")
    if console_get_type(path) is None:
        raise PairError(404, "הנתיב אינו מורשה בערוץ הבין-שרתי")
    if not _QUERY.fullmatch(query or ""):
        raise PairError(400, "פרמטרים לא תקינים")
    if not _ACTING_USER.fullmatch(acting_user or ""):
        raise PairError(400, "שם המשתמש בראשי חסר או לא תקין")
    status, headers, body = run_console_get(app, path, query or "", f"primary:{acting_user}")
    return {"node_id": storage_nodes.identity(ctx.conn)["node_id"],
            "status": status, "headers": headers, "body": body}


def console_write(ctx, app, peer: TlsPeer, *, method: str, path: str, query: str,
                  acting_user: str, body: bytes, token: str, protocol_version: str,
                  has_console_cookie: bool = False) -> dict:
    """‏#1179 שלב 2: כתיבה מהרשימה בשם ``primary:<acting_user>``. האימות **לפני**
    הנתיב, והתקרה **לפני** ההרצה. רשומת יומן ``peer_write`` במשני — מי (בראשי), מה,
    ומה יצא — גם כשהנתיב עצמו אינו כותב יומן."""
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    if app is None:
        raise PairError(503, "הקונסולה של שרת המשנה אינה מחוברת לערוץ הבין-שרתי")
    if not console_write_allowed(method, path):
        raise PairError(404, "הפעולה אינה מורשית בערוץ הבין-שרתי")
    if len(body or b"") > MAX_CONSOLE_WRITE_BODY:
        raise PairError(413, "גוף הבקשה גדול מהתקרה")
    if not _QUERY.fullmatch(query or ""):
        raise PairError(400, "פרמטרים לא תקינים")
    if not _ACTING_USER.fullmatch(acting_user or ""):
        raise PairError(400, "שם המשתמש בראשי חסר או לא תקין")
    who = f"primary:{acting_user}"
    status, headers, answer = run_console_write(app, method, path, query or "", who, body or b"")
    journal(ctx.conn, "peer_write", f"{method} {path} → {status}", who)
    return {"node_id": storage_nodes.identity(ctx.conn)["node_id"],
            "status": status, "headers": headers, "body": answer}


# --- מנהרת מוניטור מהאב (#655 v1): פרוקסי-של-פרוקסי ----------------------------
#
# הראשי לעולם אינו רואה סוד של מכונה. הוא מבקש ``GET /monitor/{mac}`` בערוץ
# המאומת; **המשני** פותח TCP למכונה, מבצע את לחיצת-היד של #839 בסוד
# שהמכונה דיווחה **לו**, ורק אחרי SecurityResult OK עונה ``200`` — ומכאן
# אותה session TLS הופכת למנהרת בייטים גולמית (כמו CONNECT): כל מה שהאב
# שולח נכנס למכונה (ClientInit ואילך), וכל מה שהמכונה מחזירה חוזר לאב.
# הראשי מגשר את זה לדפדפן ב-``monitor.bridge_browser`` — הדפדפן רואה
# בדיוק מוניטור מקומי.
#
# הממסר הוא **תהליכון אחד עם select** על שני ה-sockets: אובייקט SSL של
# OpenSSL אינו בטוח לקריאה וכתיבה מקבילות משני תהליכונים, ולכן לא
# "תהליכון לכל כיוון".

#: כמה זמן בלי בייט באף כיוון עד שהמנהרה נסגרת.
TUNNEL_IDLE_SECONDS = 600
#: מכונה שכבר יש אליה מנהרה מהאב — השנייה נדחית (409), כמו במוניטור המקומי.
_tunnels: set[str] = set()
_tunnels_lock = threading.Lock()


class MachineAuthError(Exception):
    """לחיצת-היד מול המוניטור במכונה נכשלה — הסיבה בטקסט."""


def monitor_target(ctx, peer: TlsPeer, *, token: str, protocol_version: str,
                   mac: str, has_console_cookie: bool = False) -> tuple[str, str, str]:
    """‏``(mac קנוני, ip, secret)`` של מכונה שמותר לפתוח אליה מוניטור מהאב —
    אותם תנאים של המוניטור המקומי (``monitor._target``: רשומה, build/cloner,
    נראתה ב-90 השניות האחרונות). בלי סוד או בלי ``monitor_auth: hmac`` =
    512 (סוכן ישן), לפני TCP."""
    from fastapi import HTTPException
    from server.console import monitor as monitor_module
    from server.agent import registry
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    try:
        ip, _role, secret, machine_auth = monitor_module._target(ctx, mac)
    except HTTPException as exc:
        raise PairError(exc.status_code, str(exc.detail))
    if secret is None:
        raise PairError(512, "המכונה לא דיווחה סוד מוניטור — סוכן ישן?")
    if machine_auth != monitor_module.MONITOR_AUTH_HMAC:
        raise PairError(512, "המכונה אינה תומכת באתגר-תגובה (HMAC) — סוכן ישן? אתחל מחדש")
    return registry.normalize_mac(mac), ip, secret


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        piece = sock.recv(n - len(buf))
        if not piece:
            raise MachineAuthError(f"המוניטור סגר את החיבור אחרי {len(buf)}/{n} בייטים")
        buf += piece
    return bytes(buf)


def _reason_sync(sock: socket.socket) -> str:
    length = struct.unpack(">I", _recv_exact(sock, 4))[0]
    if length > 200:
        return "(סיבה ארוכה מדי)"
    return _recv_exact(sock, length).decode("utf-8", "replace")[:40]


def authenticate_machine_sync(sock: socket.socket, secret: str) -> str:
    """‏#839/#1077 על socket חוסם — מראה של ``monitor.authenticate_machine``
    (שהוא asyncio): גרסה, סוג 2 בלבד (None לעולם לא), HMAC על ה-challenge,
    ‏SecurityResult. אחרי ההצלחה המכונה ממתינה ל-ClientInit — מהאב.
    ‏#1837: מחזיר את הסוד הבא, כמו המקור האסינכרוני."""
    from server.console.monitor import (MACHINE_REJECTED_SECRET, RFB_SECURITY_SECRET, RFB_VERSION,
                            hmac_response, next_secret)
    banner = _recv_exact(sock, 12)
    if not banner.startswith(b"RFB 003."):
        raise MachineAuthError("על 5900 עונה משהו שאינו RFB")
    sock.sendall(RFB_VERSION)
    count = _recv_exact(sock, 1)[0]
    if count == 0:
        raise MachineAuthError(f"המוניטור דחה את החיבור: {_reason_sync(sock)}")
    offered = _recv_exact(sock, count)
    if RFB_SECURITY_SECRET not in offered:
        raise MachineAuthError(
            "המוניטור במכונה אינו דורש סוד — סוכן ישן? (סוגי אבטחה "
            f"{sorted(offered)})")
    sock.sendall(bytes([RFB_SECURITY_SECRET]))
    challenge = _recv_exact(sock, 16)
    sock.sendall(hmac_response(secret, challenge))
    result = struct.unpack(">I", _recv_exact(sock, 4))[0]
    if result != 0:
        raise MachineAuthError(MACHINE_REJECTED_SECRET)
    return next_secret(secret, challenge)


def _close_reason(side: str, exc: BaseException) -> str:
    """‏#1122: סיבת סגירה בשם — הצד + סוג החריגה + הפרטים. ‏SysCallError
    ו-ZeroReturnError של OpenSSL הם שלושת-רבעי הסגירות בפועל, וכולם נבלעו."""
    return f"{side}: {type(exc).__name__}: {exc}"


def relay_tunnel(tls_conn, raw_sock: socket.socket, machine: socket.socket, *,
                 initial: bytes = b"", idle_seconds: float = TUNNEL_IDLE_SECONDS) -> str:
    """ממסר דו-כיווני בתהליכון אחד: ‏TLS(האב) ↔ TCP(המכונה), עד שצד נסגר
    או עד ``idle_seconds`` בלי תנועה. ‏non-blocking + select, ו-Want*Error
    של OpenSSL מטופלים כ"עוד לא", לא ככשל.

    מחזיר את **סיבת הסגירה** (#1122) — הקורא רושם אותה ליומן. מנהרה
    שנקטעת בלי סיבה נראית כמו באג בדפדפן, והיא בדרך כלל FW או המכונה."""
    from OpenSSL import SSL
    raw_sock.setblocking(False)
    machine.setblocking(False)
    to_machine = bytearray(initial)
    to_tls = bytearray()
    while True:
        rlist = [raw_sock, machine]
        wlist = []
        if to_machine:
            wlist.append(machine)
        if to_tls:
            wlist.append(raw_sock)
        if tls_conn.pending():
            readable, writable = [raw_sock], []
            if wlist:
                _, writable, _ = select.select([], wlist, [], 0)
        else:
            readable, writable, _ = select.select(rlist, wlist, [], idle_seconds)
            if not readable and not writable:
                return f"idle {idle_seconds:g}s"
        if raw_sock in readable:
            try:
                data = tls_conn.recv(65536)
            except SSL.WantReadError:
                data = None
            except (SSL.ZeroReturnError, SSL.SysCallError, SSL.Error, OSError) as exc:
                return _close_reason("primary", exc)
            if data == b"":
                return "primary closed"
            if data:
                to_machine += data
        if machine in readable:
            try:
                data = machine.recv(65536)
            except BlockingIOError:
                data = None
            except OSError as exc:
                return _close_reason("machine", exc)
            if data == b"":
                return "machine closed"
            if data:
                to_tls += data
        if to_machine and machine in writable:
            try:
                sent = machine.send(bytes(to_machine))
                del to_machine[:sent]
            except BlockingIOError:
                pass
            except OSError as exc:
                return _close_reason("machine send", exc)
        if to_tls and raw_sock in writable:
            try:
                sent = tls_conn.send(bytes(to_tls[:16384]))
                del to_tls[:sent]
            except (SSL.WantWriteError, SSL.WantReadError):
                pass
            except (SSL.ZeroReturnError, SSL.SysCallError, SSL.Error, OSError) as exc:
                return _close_reason("primary send", exc)


# --- אפליקציית ה-FastAPI המבודדת --------------------------------------------

def _bearer(headers) -> str:
    auth = headers.get("authorization", "")
    return auth[7:] if auth[:7].lower() == "bearer " else ""


def create_interserver_app(ctx, tls_peer_provider):
    """אפליקציה **מבודדת** לכניסה הבין-שרתית — לא הקונסולה ולא הסוכן.

    בלי ‏/console, ‏/boot או עוגיות. ``tls_peer_provider`` הוא
    callable(request)->TlsPeer שה-terminator מזין; הוא **מקור זהות ה-TLS
    היחיד** (עיקרון 5: לא header, לא request.client). המאזין בייצור הוא
    ``InterserverTLSServer`` למטה; האפליקציה הזאת היא אותן פונקציות
    service מאחורי routes של FastAPI, לבדיקות ולתיעוד הצורה."""
    from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

    app = FastAPI(title="ImageSrv interserver", docs_url=None, redoc_url=None)
    app.state.ctx = ctx
    app.state.handles = {}
    router = APIRouter(prefix="/api/interserver/v1")

    def _peer(request: Request) -> TlsPeer:
        return tls_peer_provider(request)

    def _http(exc: PairError) -> HTTPException:
        return HTTPException(exc.status, exc.detail, headers=exc.headers or None)

    def _creds(request: Request) -> dict:
        # דחיית עוגיית קונסולה: הערוץ הבין-שרתי אינו מכיר אימות-קונסולה.
        return {"token": _bearer(request.headers),
                "protocol_version": request.headers.get("imagesrv-protocol-version", ""),
                "has_console_cookie": bool(request.cookies)}

    @router.post("/pair-begin")
    async def pair_begin_route(request: Request, peer: TlsPeer = Depends(_peer)):
        body = await request.json()
        try:
            return pair_begin(ctx.conn, app.state.handles, peer, body,
                              data_dir=getattr(ctx, "data_dir", None))
        except PairError as exc:
            raise _http(exc)

    @router.post("/pair-complete")
    async def pair_complete_route(request: Request, peer: TlsPeer = Depends(_peer)):
        body = await request.json()
        try:
            return pair_complete(ctx.conn, app.state.handles, peer, body)
        except PairError as exc:
            raise _http(exc)

    @router.get("/ping")
    def ping_route(request: Request, peer: TlsPeer = Depends(_peer)):
        try:
            return ping(ctx.conn, peer, **_creds(request))
        except PairError as exc:
            raise _http(exc)

    @router.get("/machines")
    def machines_route(request: Request, peer: TlsPeer = Depends(_peer)):
        try:
            return list_machines(ctx, peer, **_creds(request))
        except PairError as exc:
            raise _http(exc)

    @router.get("/images")
    def list_images_route(request: Request, peer: TlsPeer = Depends(_peer)):
        try:
            return list_images(ctx, peer, **_creds(request))
        except PairError as exc:
            raise _http(exc)

    @router.get("/images/{image_id}")
    def image_present_route(image_id: str, request: Request,
                            peer: TlsPeer = Depends(_peer)):
        try:
            return image_present(ctx, peer, image_id=image_id, **_creds(request))
        except PairError as exc:
            raise _http(exc)

    @router.get("/images/{image_id}/manifest")
    def image_manifest_route(image_id: str, request: Request,
                             peer: TlsPeer = Depends(_peer)):
        try:
            return image_manifest(ctx, peer, image_id=image_id, **_creds(request))
        except PairError as exc:
            raise _http(exc)

    @router.get("/images/{image_id}/files/{name}")
    def image_file_route(image_id: str, name: str, request: Request,
                         peer: TlsPeer = Depends(_peer)):
        from fastapi.responses import StreamingResponse
        try:
            plan = image_file_plan(
                ctx, peer, image_id=image_id, filename=name,
                range_header=request.headers.get("range"), **_creds(request))
        except PairError as exc:
            raise _http(exc)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(plan.length),
        }
        if plan.status == 206:
            headers["Content-Range"] = (
                f"bytes {plan.start}-{plan.end}/{plan.total}")
        return StreamingResponse(
            _iter_file_range(plan.path, plan.start, plan.length),
            status_code=plan.status,
            media_type="application/octet-stream",
            headers=headers)

    @router.put("/images/{image_id}")
    async def receive_image_route(image_id: str, request: Request,
                                  peer: TlsPeer = Depends(_peer)):
        try:
            plan = receive_image_precheck(
                ctx, peer, image_id=image_id,
                content_length=request.headers.get("content-length"),
                location_id=request.headers.get("imagesrv-location-id") or "loc_local",
                **_creds(request))
        except PairError as exc:
            raise _http(exc)
        chunks = []
        async for chunk in request.stream():
            chunks.append(chunk)
        try:
            return receive_image(ctx, plan, chunks)
        except PairError as exc:
            raise _http(exc)

    app.include_router(router)
    return app


# --- terminator TLS של pyOpenSSL (המאזין הבין-שרתי בייצור ובבדיקות) ---------
#
# ‏uvinicorn/stdlib-ssl אינם יכולים לקלוט תעודת-לקוח לא-מוכרת ולחשוף
# exporter (ראו interserver_auth). לכן המאזין הבין-שרתי הוא terminator
# עצמאי: הוא מסיים את ה-mTLS, בונה ``TlsPeer`` מהחיבור החי, וקורא לאותן
# פונקציות service בדיוק שה-app ומבחני היחידה משתמשים בהן — מקור אמת אחד.
#
# גוף של אימג' (``PUT /images/{id}``) **זורם** לקובץ ואינו נאסף בזיכרון;
# כל שאר הגופים הם JSON קטן, ומוגבלים ב-``MAX_JSON_BODY``.

def _iter_file_range(path: Path, start: int, length: int, chunk_size: int = 1024 * 1024):
    """מזרים ``length`` בייטים מ-``start``. קובץ ריק = מחולל ריק."""
    if length <= 0:
        yield from ()
        return
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(chunk_size, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk


_PREFIX = "/api/interserver/v1"


def _read_http_head(conn):
    """קורא שורת-בקשה וכותרות של בקשת HTTP/1.1 אחת מחיבור pyOpenSSL.
    מחזיר ``(method, path, headers, rest)`` — ‏``rest`` הם בייטי הגוף שכבר
    הגיעו עם הכותרות — או ``None`` כשהחיבור נסגר."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    method, path, _ = (lines[0].decode() + "  ").split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            k, _, v = line.partition(b":")
            headers[k.strip().lower().decode()] = v.strip().decode()
    return method, path, headers, rest


def _body_chunks(conn, rest: bytes, length: int, chunk_size: int = 1024 * 1024):
    """מחולל את גוף הבקשה — בדיוק ``length`` בייטים, או פחות אם החיבור
    נסגר (הקורא סופר ומכשיל; כאן לא מסתירים קטיעה)."""
    got = 0
    if rest:
        piece = rest[:length]
        got += len(piece)
        yield piece
    while got < length:
        chunk = conn.recv(min(chunk_size, length - got))
        if not chunk:
            return
        got += len(chunk)
        yield chunk


def _http_response(status: int, payload: object, *, close: bool = False,
                   headers: dict | None = None) -> bytes:
    import json
    body = json.dumps(payload).encode()
    reason = {200: "OK", 100: "Continue", 206: "Partial Content"}.get(status, "ERROR")
    connection = "close" if close else "keep-alive"
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    return (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: {connection}\r\n{extra}\r\n"
            ).encode() + body


_CONTINUE = b"HTTP/1.1 100 Continue\r\n\r\n"

#: שם התהליכון לכל חיבור — כדי שאפשר יהיה לספור אותם (ולראות שהם משתחררים).
HANDLER_THREAD_NAME = "interserver-conn"


def _peer_addr(raw) -> str:
    try:
        host, port = raw.getpeername()[:2]
        return f"{host}:{port}"
    except OSError:
        return "?"


def _handshake_within(conn, raw, seconds: float) -> bool:
    """‏#1122: ‏``do_handshake`` עם תקרת זמן. מחזיר ``True`` כשה-handshake
    הושלם, ‏``False`` כשפג הזמן — והקורא סוגר.

    ‏socket עם timeout הוא non-blocking ל-pyOpenSSL (אותו מוקש כמו #902 בצד
    הלקוח): ‏``do_handshake`` זורק ``WantReadError``/``WantWriteError`` ברגע
    שהבייט הבא לא הגיע, ולכן ממתינים ב-``select`` עד ה-deadline ומנסים שוב.
    בסיום — חוסם רגיל (``settimeout(None)``): קליטת אימג' של עשרות ג'יגה
    אסור שתיפול על timeout של handshake."""
    from OpenSSL import SSL
    deadline = time.monotonic() + seconds
    raw.settimeout(seconds)
    try:
        while True:
            try:
                conn.do_handshake()
                return True
            except SSL.WantReadError:
                wait_r, wait_w = [raw], []
            except SSL.WantWriteError:
                wait_r, wait_w = [], [raw]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            ready = select.select(wait_r, wait_w, [], remaining)
            if not any(ready):
                return False
    finally:
        raw.settimeout(None)


class InterserverTLSServer:
    """מאזין mTLS 1.3 threaded לכניסה הבין-שרתית, מבוסס pyOpenSSL.

    ``ctx`` הוא ה-ServerContext (עם ``conn`` ו-``library``); ``data_dir``
    לזהות. מסיים את ה-TLS, בונה TlsPeer, ומנתב לפונקציות ה-service.
    """

    def __init__(self, ctx, host: str, port: int, cert_pem: bytes, key_pem: bytes,
                 *, data_dir=None, console_app=None):
        import socket
        import threading
        self.ctx = ctx
        self.data_dir = data_dir
        #: ‏#1179: אפליקציית הקונסולה של השרת הזה — ל-``GET /console/…`` בלבד.
        self.console_app = console_app
        self.handles: dict = {}
        self._cert_pem = cert_pem
        self._key_pem = key_pem
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(16)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> "InterserverTLSServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        import socket
        self._stop.set()
        # ‏close() לבדו אינו מעיר accept() חסום בלינוקס: המאזין המשיך לקבל חיבור
        # אחד, השלים TLS וסגר בלי לקרוא (ECONNRESET אצל השולח, שנראה "אחרי
        # השליחה"). ‏shutdown מעיר אותו; בווינדוס הוא נכשל על שקע מאזין
        # (ENOTCONN), ושם close לבדו כבר מעיר את ה-accept.
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve(self) -> None:
        from OpenSSL import SSL
        tls_ctx = interserver_auth.build_server_tls_context(self._cert_pem, self._key_pem)
        while not self._stop.is_set():
            try:
                raw, _addr = self._sock.accept()
            except OSError:
                break
            import threading
            threading.Thread(target=self._handle, args=(tls_ctx, raw),
                             name=HANDLER_THREAD_NAME, daemon=True).start()

    def _handle(self, tls_ctx, raw) -> None:
        from OpenSSL import SSL
        conn = SSL.Connection(tls_ctx, raw)
        conn.set_accept_state()
        try:
            if not _handshake_within(conn, raw, HANDSHAKE_TIMEOUT_SECONDS):
                log.info("interserver: TLS handshake from %s did not finish in %gs — closed",
                         _peer_addr(raw), HANDSHAKE_TIMEOUT_SECONDS)
                return
            peer_cert = conn.get_peer_certificate()
            peer = TlsPeer(
                tls_version=conn.get_protocol_version_name(),
                cert_der=(interserver_auth._to_der(peer_cert) if peer_cert else None),
                exporter=conn.export_keying_material(interserver_auth.EXPORTER_LABEL,
                                                     interserver_auth.EXPORTER_LENGTH),
            )
            while not self._stop.is_set():
                head = _read_http_head(conn)
                if head is None:
                    break
                method, path, headers, rest = head
                if method == "PUT" and _image_id_of(path) is not None:
                    keep = self._receive_stream(conn, peer, path, headers, rest)
                elif method == "GET" and _monitor_mac_of(path) is not None:
                    self._monitor_tunnel(conn, raw, peer, path, headers, rest)
                    keep = False
                elif method == "GET" and _image_file_of(path) is not None:
                    keep = self._send_file(conn, peer, path, headers)
                else:
                    keep = self._small_request(conn, peer, method, path, headers, rest)
                if not keep or headers.get("connection", "").lower() == "close":
                    break
        except SSL.Error:
            pass
        finally:
            try:
                conn.shutdown()
            except Exception:                                # noqa: BLE001
                pass
            conn.close()
            raw.close()

    def _small_request(self, conn, peer, method, path, headers, rest) -> bool:
        """בקשת JSON: קוראים את הגוף (עד התקרה) ומשיבים. מחזיר האם
        להמשיך ב-keep-alive."""
        length = int(headers.get("content-length", "0") or 0)
        if length > MAX_JSON_BODY:
            conn.sendall(_http_response(413, {"detail": "גוף גדול מדי"}, close=True))
            return False
        body = b"".join(_body_chunks(conn, rest, length))
        # ‏#1122 (Low): גוף שאינו באורך המוצהר אסור שישאיר את החיבור ב-
        # keep-alive — קצר = החיבור נסגר באמצע (400, לא dispatch של חצי
        # JSON); ארוך = בייטים מעבר ל-Content-Length שאיש לא יקרא (desync).
        if len(body) != length:
            conn.sendall(_http_response(
                400, {"detail": f"גוף קצר מהמוצהר: התקבלו {len(body)} מתוך {length}"},
                close=True))
            return False
        conn.sendall(self._dispatch(peer, method, path, headers, body))
        return len(rest) <= length

    def _receive_stream(self, conn, peer, path, headers, rest) -> bool:
        """‏``PUT /images/{id}``: precheck → ‏``100 Continue`` → זרם לקובץ →
        תשובה. דחייה ב-precheck נשלחת **בלי** לקרוא גוף, והחיבור נסגר —
        כדי שלקוח שלא חיכה ל-100 לא ישאיר עשרות ג'יגה בצנרת."""
        try:
            plan = receive_image_precheck(
                self.ctx, peer, image_id=_image_id_of(path),
                content_length=headers.get("content-length"),
                location_id=headers.get("imagesrv-location-id") or "loc_local",
                token=_bearer(headers),
                protocol_version=headers.get("imagesrv-protocol-version", ""),
                has_console_cookie="imagesrv_session" in headers.get("cookie", ""))
        except PairError as exc:
            conn.sendall(_http_response(exc.status, exc.payload(), close=True))
            return False
        if "100-continue" in headers.get("expect", "").lower():
            conn.sendall(_CONTINUE)
        try:
            result = receive_image(self.ctx, plan,
                                   _body_chunks(conn, rest, plan.content_length))
        except PairError as exc:
            conn.sendall(_http_response(exc.status, exc.payload(), close=True))
            return False
        conn.sendall(_http_response(200, result))
        return True

    def _send_file(self, conn, peer, path, headers) -> bool:
        """``GET /images/{id}/files/{name}``: אימות → Range → זרם. 404 על
        שם שאינו במניפסט, בלי לקרוא את הדיסק כנתיב חופשי."""
        image_id, name = _image_file_of(path)
        try:
            plan = image_file_plan(
                self.ctx, peer, image_id=image_id, filename=name,
                range_header=headers.get("range"),
                token=_bearer(headers),
                protocol_version=headers.get("imagesrv-protocol-version", ""),
                has_console_cookie="imagesrv_session" in headers.get("cookie", ""))
        except PairError as extra:
            conn.sendall(_http_response(extra.status, extra.payload(), close=True,
                                        headers=extra.headers))
            return False
        reason = "Partial Content" if plan.status == 206 else "OK"
        lines = [
            f"HTTP/1.1 {plan.status} {reason}",
            "Content-Type: application/octet-stream",
            f"Content-Length: {plan.length}",
            "Accept-Ranges: bytes",
            "Connection: keep-alive",
        ]
        if plan.status == 206:
            lines.append(f"Content-Range: bytes {plan.start}-{plan.end}/{plan.total}")
        conn.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        for chunk in _iter_file_range(plan.path, plan.start, plan.length):
            conn.sendall(chunk)
        return True

    def _monitor_tunnel(self, conn, raw, peer, path, headers, rest) -> None:
        """‏``GET /monitor/{mac}``: אימות → יעד → מנעול → TCP למכונה →
        לחיצת-יד בסוד של המשני → ``200`` → ממסר גולמי עד סגירה."""
        from server.console import monitor as monitor_module
        try:
            mac, ip, secret = monitor_target(
                self.ctx, peer, mac=_monitor_mac_of(path), token=_bearer(headers),
                protocol_version=headers.get("imagesrv-protocol-version", ""),
                has_console_cookie="imagesrv_session" in headers.get("cookie", ""))
        except PairError as exc:
            conn.sendall(_http_response(exc.status, exc.payload(), close=True))
            return
        with _tunnels_lock:
            if mac in _tunnels:
                conn.sendall(_http_response(
                    409, {"detail": "כבר פתוח מוניטור למכונה הזאת"}, close=True))
                return
            _tunnels.add(mac)
        machine = None
        try:
            try:
                machine = socket.create_connection((ip, monitor_module.MONITOR_PORT),
                                                   timeout=3.0)
            except OSError as exc:
                conn.sendall(_http_response(
                    502, {"detail": f"שירות המוניטור במכונה אינו זמין: {exc}"},
                    close=True))
                return
            monitor_module.keep_alive(machine)       # #1850: מכונה שנעלמה בלי FIN
            try:
                machine.settimeout(5.0)
                rekeyed = authenticate_machine_sync(machine, secret)
            except MachineAuthError as exc:
                conn.sendall(_http_response(512, {"detail": str(exc)}, close=True))
                return
            except (OSError, socket.timeout):
                conn.sendall(_http_response(
                    512, {"detail": "המוניטור לא השלים את לחיצת-היד תוך 5 שניות"},
                    close=True))
                return
            # ‏#1837: המכונה כבר עברה לסוד הבא — כמו במוניטור המקומי.
            monitor_rekeyed(self.ctx.conn, mac, secret, rekeyed)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream"
                         b"\r\nConnection: close\r\n\r\n")
            journal(self.ctx.conn, "storage_monitor_tunnel", mac, "")
            reason = relay_tunnel(conn, raw, machine, initial=rest)
            journal(self.ctx.conn, "storage_monitor_tunnel_closed", f"{mac}: {reason}", "")
            log.info("interserver: monitor tunnel to %s closed — %s", mac, reason)
        finally:
            if machine is not None:
                try:
                    machine.close()
                except OSError:
                    pass
            with _tunnels_lock:
                _tunnels.discard(mac)

    def _dispatch(self, peer, method, path, headers, body) -> bytes:
        import json
        bare, _, query = path.partition("?")
        console_path = _console_path_of(bare)
        if method in ("POST", "PUT", "DELETE") and console_path is not None:
            # ‏#1179 שלב 2: הגוף עובר לקונסולה כפי שהוא — לא מפוענח כאן.
            creds = {"token": _bearer(headers),
                     "protocol_version": headers.get("imagesrv-protocol-version", ""),
                     "has_console_cookie": "imagesrv_session" in headers.get("cookie", "")}
            try:
                return _http_response(200, console_write(
                    self.ctx, self.console_app, peer, method=method, path=console_path,
                    query=query, acting_user=headers.get("imagesrv-acting-user", ""),
                    body=body, **creds))
            except PairError as exc:
                return _http_response(exc.status, exc.payload())
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return _http_response(400, {"detail": "גוף JSON לא תקין"})
        creds = {
            "token": _bearer(headers),
            "protocol_version": headers.get("imagesrv-protocol-version", ""),
            "has_console_cookie": "imagesrv_session" in headers.get("cookie", ""),
        }
        image_id = _image_id_of(path)
        try:
            if method == "POST" and path == f"{_PREFIX}/pair-begin":
                return _http_response(200, pair_begin(
                    self.ctx.conn, self.handles, peer, payload, data_dir=self.data_dir))
            if method == "POST" and path == f"{_PREFIX}/pair-complete":
                return _http_response(200, pair_complete(
                    self.ctx.conn, self.handles, peer, payload))
            if method == "GET" and path == f"{_PREFIX}/ping":
                return _http_response(200, ping(self.ctx.conn, peer, **creds))
            if method == "GET" and path == f"{_PREFIX}/machines":
                return _http_response(200, list_machines(self.ctx, peer, **creds))
            if method == "GET" and path == f"{_PREFIX}/images":
                return _http_response(200, list_images(self.ctx, peer, **creds))
            console_path, _, query = path.partition("?")
            console_path = _console_path_of(console_path)
            if method == "GET" and console_path is not None:
                return _http_response(200, console_get(
                    self.ctx, self.console_app, peer, path=console_path, query=query,
                    acting_user=headers.get("imagesrv-acting-user", ""), **creds))
            manifest_id = _image_manifest_of(path)
            if method == "GET" and manifest_id is not None:
                return _http_response(200, image_manifest(
                    self.ctx, peer, image_id=manifest_id, **creds))
            if method == "GET" and image_id is not None:
                return _http_response(200, image_present(
                    self.ctx, peer, image_id=image_id, **creds))
            return _http_response(404, {"detail": "לא קיים"})
        except PairError as exc:
            return _http_response(exc.status, exc.payload())


def _monitor_mac_of(path: str) -> str | None:
    """ה-MAC מ-``/api/interserver/v1/monitor/{mac}`` (רכיב יחיד), או ``None``."""
    prefix = f"{_PREFIX}/monitor/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):]
    if not rest or "/" in rest or "?" in rest:
        return None
    return rest


def _image_id_of(path: str) -> str | None:
    """המזהה מ-``/api/interserver/v1/images/{id}`` (רכיב יחיד), או ``None``."""
    prefix = f"{_PREFIX}/images/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):]
    if not rest or "/" in rest or "?" in rest:
        return None
    return rest


def _image_manifest_of(path: str) -> str | None:
    """המזהה מ-``/api/interserver/v1/images/{id}/manifest``, או ``None``."""
    prefix = f"{_PREFIX}/images/"
    suffix = "/manifest"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    mid = path[len(prefix):-len(suffix)]
    if not mid or "/" in mid or "?" in mid:
        return None
    return mid


def _image_file_of(path: str) -> tuple[str, str] | None:
    """``(id, name)`` מ-``/api/interserver/v1/images/{id}/files/{name}``."""
    from urllib.parse import unquote
    prefix = f"{_PREFIX}/images/"
    marker = "/files/"
    if not path.startswith(prefix) or marker not in path:
        return None
    rest = path[len(prefix):]
    image_id, found, name = rest.partition(marker)
    if found != marker or not image_id or "/" in image_id or not name:
        return None
    if "?" in image_id:
        return None
    decoded = unquote(name)
    if not decoded or "/" in decoded or "\\" in decoded:
        return None
    return image_id, decoded


def _console_path_of(path: str) -> str | None:
    """‏#1179: ‏``/…`` מ-``/api/interserver/v1/console/…`` (בלי query), או ``None``."""
    prefix = f"{_PREFIX}/console/"
    if not path.startswith(prefix) or len(path) == len(prefix):
        return None
    return "/" + path[len(prefix):]
