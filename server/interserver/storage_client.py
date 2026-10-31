"""Storage Nodes — לקוח ה-enrollment היוצא של הראשי (#740, tracer 2.1).

הראשי מציג את זהות ה-TLS שלו כתעודת-לקוח, ומצמיד את ה-SPKI של המשני
(hard-fail, בלי TOFU). לעולם לא עוקב אחר redirect ולעולם לא נופל ל-HTTP
או ל-TLS 1.2 — כתובת שאינה ``https://host:port`` מפורש נדחית עוד לפני
החיבור (עיקרון 5: downgrade הוא כשל, לא "קרוב מספיק").

ה-transport הוא HTTP/1.1 מינימלי מעל ``OpenSSL.SSL`` — ‏pair-begin
ו-pair-complete רצים על **אותו** חיבור (keep-alive), ולכן על אותה session
TLS, וזה מה שקושר אותם ב-channel-binding בצד המשני.
"""

from __future__ import annotations

import json
import select
import socket
import time

from server.interserver import interserver_auth
from server.interserver.interserver_auth import parse_interserver_url   # re-export לנוחות

__all__ = ["parse_interserver_url", "PinnedMTLSClient", "fetch_server_spki",
           "pair_secondary", "ping_secondary", "InterserverClientError",
           "InterserverIdentityMismatch", "InterserverTunnelRefused", "open_tunnel",
           "InterserverTimeout"]

#: ‏#1286: תקרה **כוללת** לבקשה רגילה — ping, ‏JSON, ו-relay של הקונסולה.
#: משני שקיבל את הבקשה ולא ענה בתוכה = "לא נקרא" (502), לא תהליכון תקוע.
REQUEST_TIMEOUT = 15.0
#: ‏#1286: בזרם (העברת אימג') — כמה זמן מותר **בלי בייט אחד שזז**. זו תקרה
#: למנה ולא לכל ההעברה: זרם איטי שמתקדם אינו נחתך, זרם שעמד — נכשל.
STREAM_IDLE_TIMEOUT = 60.0
#: ‏#1286: אחרי שכל גוף ה-PUT יצא, המשני מחלץ את ה-tar ומאמת sha256 לפני
#: שהוא עונה — דקות של שקט לגיטימי. התקרה לשקט הזה יחסית לגודל: קצב רצפה
#: שנבחר (לא נמדד) כחמישית מ-106MB/s של סבב המעבדה (17/09). ‏#1457: משני
#: מעודכן מחלץ ומאמת **תוך כדי** הזרם, ואחרי הבייט האחרון נשארת לו רק הכניסה
#: לספרייה; התקרה נשארת בשביל משני בגרסה קודמת, שעדיין עושה את העבודה אחרי.
VERIFY_MIN_RATE = 20 * 1024 * 1024


class InterserverClientError(RuntimeError):
    """כשל בשיחה הבין-שרתית מצד הראשי (חיבור, SPKI, או תשובת שגיאה)."""


class InterserverTimeout(InterserverClientError):
    """‏#1286: המשני לא התקדם בתוך התקרה — כשל בשם, לא המתנה בלי סוף."""


class RequestNotSent(InterserverClientError):
    """‏#1179: הבקשה לא נכתבה במלואה לחיבור (‏ECONNRESET/EPIPE בתוך ה-sendall).
    המשני לא קיבל בקשה שלמה ולכן לא הריץ אותה — כותרות חלקיות או גוף קצר
    מהמוצהר אינם מגיעים ל-dispatch (‏#1122). כשל **אחרי** כתיבה מלאה אינו
    זה: שם לא ידוע אם הפעולה בוצעה."""


class InterserverIdentityMismatch(InterserverClientError):
    """‏#883: המשני הצהיר ב-JSON על מזהה שאינו נגזר מהמפתח המוצמד.

    ההצהרה (``secondary_id`` ב-pair-begin) היא טקסט חופשי מהצד השני;
    הזהות היא ``node_id_from_spki(expected_spki)`` — מה שה-handshake אימת.
    סתירה ביניהן נכשלת **בקול ולפני שליחת הקוד** — לא מתוקנת בשקט."""


def _read_http_headers(conn) -> tuple[int, dict, bytes]:
    """קורא כותרות HTTP/1.1 מחיבור ה-TLS. מחזיר ``(status, headers, rest)``
    — ‏``rest`` הם בייטי גוף שכבר הגיעו עם הכותרות."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            raise InterserverClientError("החיבור נסגר לפני סוף הכותרות")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = int(lines[0].split(b" ")[1])
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            k, _, v = line.partition(b":")
            headers[k.strip().lower().decode()] = v.strip().decode()
    return status, headers, rest


def _read_http_response(conn) -> tuple[int, dict, bytes]:
    """קורא תשובת HTTP/1.1 אחת (status, headers, body) מחיבור ה-TLS.

    קורא עד סוף הכותרות, ואז בדיוק ``Content-Length`` בייטים. אין chunked —
    השרת הבין-שרתי מחזיר תמיד אורך מפורש (תשובות זעירות)."""
    status, headers, rest = _read_http_headers(conn)
    length = int(headers.get("content-length", "0") or 0)
    body = rest
    while len(body) < length:
        chunk = conn.recv(4096)
        if not chunk:
            raise InterserverClientError("החיבור נסגר לפני סוף הגוף")
        body += chunk
    return status, headers, body[:length]


def _parse_json(raw: bytes) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"detail": raw.decode("utf-8", "replace")}


class _TimedConn:
    """‏#1286: חיבור pyOpenSSL על socket לא-חוסם, עם ``select`` לכל המתנה.

    ‏socket עם timeout הוא non-blocking ל-pyOpenSSL (‏WantRead/WantWrite) —
    ולכן ה-timeout הוסר פעם אחרי ה-connect, והחיבור חסם בלי תקרה. כאן כל
    ``recv``/``sendall``/``do_handshake`` ממתין לכל היותר ``idle`` שניות
    להתקדמות, ולא אחרי ``deadline`` (אם נקבע). כל השאר עובר לחיבור עצמו."""

    def __init__(self, conn, raw):
        self._ssl = conn
        self._raw = raw
        self.idle = REQUEST_TIMEOUT
        self.deadline: float | None = None
        self.waiting_for = "תשובה"

    def limit(self, idle: float, *, total: bool, waiting_for: str) -> None:
        self.idle = idle
        self.deadline = time.monotonic() + idle if total else None
        self.waiting_for = waiting_for

    def _wait(self, rlist, wlist) -> None:
        wait = self.idle
        if self.deadline is not None:
            wait = min(wait, self.deadline - time.monotonic())
        if wait <= 0 or not any(select.select(rlist, wlist, [], wait)):
            raise InterserverTimeout(
                f"המשני לא התקדם {self.idle:g} שניות ({self.waiting_for}) — נכשל, לא ממתינים")

    def _io(self, op):
        from OpenSSL import SSL
        while True:
            try:
                return op()
            except SSL.WantReadError:
                self._wait([self._raw], [])
            except SSL.WantWriteError:
                self._wait([], [self._raw])

    def do_handshake(self) -> None:
        self._io(self._ssl.do_handshake)

    def recv(self, n: int) -> bytes:
        return self._io(lambda: self._ssl.recv(n))

    def sendall(self, data: bytes) -> None:
        # ‏send חוזר על **אותו** אובייקט אחרי Want*: ‏OpenSSL דורש אותם ארגומנטים.
        view = memoryview(data)
        while view:
            current = view
            view = view[self._io(lambda: self._ssl.send(current)):]

    def __getattr__(self, name):
        return getattr(self._ssl, name)


class PinnedMTLSClient:
    """חיבור mTLS 1.3 יחיד למשני, עם SPKI מוצמד ותעודת-לקוח של הראשי."""

    def __init__(self, host: str, port: int, *, expected_secondary_spki: str,
                 cert_pem: bytes, key_pem: bytes, path_prefix: str = "/api/interserver/v1",
                 timeout: float = 10.0, request_timeout: float | None = None):
        self.host = host
        self.port = port
        self.expected_spki = expected_secondary_spki
        self._cert_pem = cert_pem
        self._key_pem = key_pem
        self.path_prefix = path_prefix.rstrip("/")
        self.timeout = timeout
        #: ‏#1564: הדופק מקצר את התקרה לבקשה; כל השאר נשארים על `REQUEST_TIMEOUT`.
        #: ‏None נקרא כאן ולא כברירת מחדל בחתימה — ברירת מחדל נקבעת פעם אחת
        #: בייבוא, והקבוע של המודול הפסיק להיות הכפתור (#1286).
        self.request_timeout = REQUEST_TIMEOUT if request_timeout is None else request_timeout
        self._conn = None

    def __enter__(self) -> "PinnedMTLSClient":
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def open(self) -> None:
        from OpenSSL import SSL
        ctx = interserver_auth.build_pinned_client_context(
            self.expected_spki, self._cert_pem, self._key_pem)
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        # ‏#1286: לא-חוסם, וכל המתנה דרך ``_TimedConn`` (select) — לא
        # ``settimeout(None)``, שבו משני שקיבל TCP ונתקע חסם לנצח.
        raw.setblocking(False)
        # codeql[py/insecure-protocol] false positive: build_pinned_client_context קובע min=max=TLS 1.3, ונבדק שוב אחרי ה-handshake (#291)
        ssl_conn = SSL.Connection(ctx, raw)
        ssl_conn.set_connect_state()
        conn = _TimedConn(ssl_conn, raw)
        conn.limit(self.timeout, total=True, waiting_for="handshake")
        try:
            conn.do_handshake()
        except InterserverTimeout:
            raw.close()
            raise
        except SSL.Error as exc:
            raw.close()
            # אי-התאמת SPKI מפילה את ה-handshake כאן — hard fail, לא המשך.
            raise InterserverClientError(
                f"‏handshake mTLS נכשל (SPKI לא תואם או TLS<1.3?): {exc}") from exc
        if conn.get_protocol_version_name() != "TLSv1.3":
            conn.close(); raw.close()
            raise InterserverClientError("השרת ירד מ-TLS 1.3 — נדחה")
        self._raw = raw
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.shutdown()
            except Exception:                                # noqa: BLE001
                pass
            self._conn.close()
            self._raw.close()
            self._conn = None

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 headers: dict | None = None) -> tuple[int, dict, dict]:
        if self._conn is None:
            raise InterserverClientError("החיבור אינו פתוח")
        payload = json.dumps(body).encode() if body is not None else b""
        lines = [
            f"{method} {self.path_prefix}{path} HTTP/1.1",
            f"Host: {self.host}",
            "Connection: keep-alive",
            f"ImageSrv-Protocol-Version: {interserver_auth.PROTOCOL_VERSION}",
        ]
        for k, v in (headers or {}).items():
            lines.append(f"{k}: {v}")
        if body is not None:
            lines.append("Content-Type: application/json")
            lines.append(f"Content-Length: {len(payload)}")
        else:
            lines.append("Content-Length: 0")
        request = ("\r\n".join(lines) + "\r\n\r\n").encode() + payload
        self._conn.limit(self.request_timeout, total=True, waiting_for=f"{method} {path}")
        try:
            self._conn.sendall(request)
        except Exception as exc:                             # noqa: BLE001
            raise RequestNotSent(f"הבקשה לא נשלחה במלואה: {exc}") from exc
        status, _resp_headers, raw_body = _read_http_response(self._conn)
        try:
            parsed = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            parsed = {"detail": raw_body.decode("utf-8", "replace")}
        return status, _resp_headers, parsed

    def pair(self, *, code: str, primary_id: str, client_nonce: str = "") -> dict:
        """‏pair-begin ואז pair-complete על אותו חיבור. מחזיר את תשובת ה-complete
        (הטוקן וזהות האב) או מעלה חריגה עם ה-detail של השרת."""
        import secrets
        begin_status, _, begin = self._request("POST", "/pair-begin", body={
            "primary_id": primary_id,
            "expected_secondary_spki": self.expected_spki,
            "protocol_version": interserver_auth.PROTOCOL_VERSION,
            "client_nonce": client_nonce or secrets.token_hex(16),
        })
        if begin_status != 200:
            raise InterserverClientError(
                f"pair-begin נכשל ({begin_status}): {begin.get('detail')}")
        # ‏#883: הזהות נגזרת מה-SPKI שה-handshake אימת. ההצהרה ב-JSON חייבת
        # להיות זהה לה — אחרת המשני מצהיר על זהות שאינה שלו, והקוד
        # החד-פעמי לא נשלח (המשני לא נקשר לאב על סמך ההצהרה).
        derived = interserver_auth.node_id_from_spki(self.expected_spki)
        declared = str(begin.get("secondary_id") or "")
        if declared != derived:
            raise InterserverIdentityMismatch(
                f"המשני הצהיר על מזהה {declared!r} שאינו נגזר מהמפתח המוצמד "
                f"({derived}) — הקוד לא נשלח")
        comp_status, _, comp = self._request("POST", "/pair-complete", body={
            "handle": begin["handle"],
            "code": code,
            "protocol_version": interserver_auth.PROTOCOL_VERSION,
        })
        if comp_status != 200:
            raise InterserverClientError(
                f"pair-complete נכשל ({comp_status}): {comp.get('detail')}")
        comp["secondary_id"] = derived
        comp["secondary_spki"] = begin.get("secondary_spki")
        return comp

    def ping(self, token: str) -> dict:
        status, _, body = self._request("GET", "/ping", headers={
            "Authorization": f"Bearer {token}",
        })
        if status != 200:
            raise InterserverClientError(f"ping נכשל ({status}): {body.get('detail')}")
        return body

    def get_json(self, path: str, token: str) -> dict:
        """‏GET מאומת שמחזיר JSON; תשובה שאינה 200 היא חריגה עם ה-detail."""
        status, _, body = self._request("GET", path, headers={
            "Authorization": f"Bearer {token}",
        })
        if status != 200:
            raise InterserverClientError(
                f"{path} נכשל ({status}): {body.get('detail')}")
        return body

    def get_answer(self, path: str, token: str, headers: dict | None = None) -> tuple[int, dict]:
        """‏#1179: GET מאומת → ``(status, body)`` בלי לזרוק על status — הקורא
        מבחין בין "המשני דחה" לבין כשל חיבור (שכן זורק)."""
        status, _, body = self._request("GET", path, headers={
            "Authorization": f"Bearer {token}", **(headers or {})})
        return status, body

    def send_answer(self, method: str, path: str, token: str, body: object,
                    headers: dict | None = None) -> tuple[int, dict]:
        """‏#1179 שלב 2: בקשת כתיבה מאומתת → ``(status, body)`` בלי לזרוק על status.
        זורק רק על כשל בערוץ: ``RequestNotSent`` — הבקשה לא נכתבה במלואה; כל
        חריגה אחרת — הבקשה נכתבה והתשובה לא הגיעה (לא ידוע אם בוצעה)."""
        status, _, answer = self._request(method, path, body=body, headers={
            "Authorization": f"Bearer {token}", **(headers or {})})
        return status, answer

    def get_stream(self, path: str, token: str, dest, *, on_progress=None) -> int:
        """‏GET של גוף בינארי לקובץ, עם ``Range`` אם היעד כבר חלקי.

        ``dest`` הוא ``Path``. אם הקובץ קיים וגודלו > 0 נשלח
        ``Range: bytes={size}-``: ‏206 ממשיך בסוף, ‏200 דורס (השרת התעלם
        מהטווח), ‏416 = היעד כבר שלם **רק** אם ``Content-Range: bytes */N``
        שווה לגודלו (‏#1456; אחרת כשל), ואחריו חיבור חדש — המשני סוגר
        את החיבור על 416. בלי ``Content-Length`` — כשל, לא
        קריאה עד סגירת החיבור (עיקרון 5, keep-alive). מחזיר את גודל היעד.
        """
        from pathlib import Path
        if self._conn is None:
            raise InterserverClientError("החיבור אינו פתוח")
        dest = Path(dest)
        resume_from = dest.stat().st_size if dest.is_file() else 0
        lines = [
            f"GET {self.path_prefix}{path} HTTP/1.1",
            f"Host: {self.host}",
            "Connection: keep-alive",
            f"ImageSrv-Protocol-Version: {interserver_auth.PROTOCOL_VERSION}",
            f"Authorization: Bearer {token}",
            "Content-Length: 0",
        ]
        if resume_from > 0:
            lines.append(f"Range: bytes={resume_from}-")
        self._conn.limit(STREAM_IDLE_TIMEOUT, total=False, waiting_for=f"זרם {path}")
        self._conn.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        status, headers, rest = _read_http_headers(self._conn)
        if status == 416 and resume_from > 0:
            # ‏#1456: המשני סוגר את החיבור אחרי 416. חיבור חדש לבקשה הבאה —
            # אחרת הקובץ הבא נשלח על חיבור סגור ונכשל בכל ניסיון חוזר.
            self.close()
            # 416 לבדו אינו "שלם" (עיקרון 5): שלם = הגודל שהמשני מדווח
            # (‏``Content-Range: bytes */N``) שווה לגודל היעד. ה-sha256 נבדק
            # אחר כך מול המניפסט, כמו לכל קובץ.
            reported = headers.get("content-range", "")
            total = reported.rpartition("/")[2] if reported.startswith("bytes */") else ""
            if not total.isdigit():
                raise InterserverClientError(
                    f"{path}: 416 בלי Content-Range — המשני לא דיווח את גודל הקובץ,"
                    f" ולא ידוע אם {resume_from} הבייטים שכבר הורדו הם הקובץ השלם")
            if int(total) != resume_from:
                raise InterserverClientError(
                    f"{path}: 416 — ביעד {resume_from} בייטים, ובמשני הקובץ"
                    f" {int(total)} בייטים; הקובץ החלקי אינו הקובץ שבמשני")
            self.open()
            return resume_from
        if status not in (200, 206):
            raw = rest
            length = int(headers.get("content-length", "0") or 0)
            while len(raw) < length:
                chunk = self._conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
            body = _parse_json(raw[:length] if length else raw)
            raise InterserverClientError(
                f"{path} נכשל ({status}): {body.get('detail')}")
        if "content-length" not in headers:
            raise InterserverClientError(f"{path}: חסר Content-Length")
        length = int(headers["content-length"] or 0)
        if length < 0:
            raise InterserverClientError(f"{path}: Content-Length שלילי")
        if status == 200:
            mode = "wb"
            already = 0
        else:
            mode = "ab"
            already = resume_from
        dest.parent.mkdir(parents=True, exist_ok=True)
        got = 0
        try:
            with dest.open(mode) as handle:
                if rest:
                    piece = rest[:length]
                    handle.write(piece)
                    got += len(piece)
                    rest = rest[len(piece):]
                while got < length:
                    chunk = self._conn.recv(min(1024 * 1024, length - got))
                    if not chunk:
                        raise InterserverClientError(
                            f"החיבור נסגר אחרי {already + got} בייטים")
                    handle.write(chunk)
                    got += len(chunk)
                    if on_progress is not None:
                        on_progress(already + got)
        except InterserverTimeout as exc:
            raise InterserverTimeout(f"{exc} — אחרי {already + got} בייטים") from exc
        except InterserverClientError:
            raise
        except Exception as exc:                                 # noqa: BLE001
            raise InterserverClientError(
                f"החיבור למשני נותק אחרי {already + got} בייטים: {exc}") from exc
        if got != length:
            raise InterserverClientError(
                f"{path}: התקבלו {got} מתוך {length} בייטים")
        if on_progress is not None:
            on_progress(already + got)
        return already + got

    def put_stream(self, path: str, token: str, chunks, total: int, *,
                   on_progress=None, headers: dict | None = None) -> dict:
        """‏PUT של גוף זורם באורך ידוע (``total``), עם ``Expect: 100-continue``.

        המשני מריץ את בדיקותיו (אימות, "כבר קיים", מקום פנוי) **לפני**
        שהגוף נשלח, ועונה ``100`` — או תשובה סופית שנקראת כאן כשגיאה בלי
        לשלוח בייט אחד. ``on_progress(sent)`` נקרא אחרי כל מנה; ניתוק באמצע
        מעלה חריגה עם כמות הבייטים שנשלחו (כשל בשם, לא "נתקע")."""
        if self._conn is None:
            raise InterserverClientError("החיבור אינו פתוח")
        lines = [
            f"PUT {self.path_prefix}{path} HTTP/1.1",
            f"Host: {self.host}",
            "Connection: keep-alive",
            f"ImageSrv-Protocol-Version: {interserver_auth.PROTOCOL_VERSION}",
            f"Authorization: Bearer {token}",
            "Content-Type: application/x-tar",
            f"Content-Length: {total}",
            "Expect: 100-continue",
        ]
        lines.extend(f"{key}: {value}" for key, value in (headers or {}).items())
        self._conn.limit(STREAM_IDLE_TIMEOUT, total=False, waiting_for=f"זרם {path}")
        self._conn.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        status, _headers, raw = _read_http_response(self._conn)
        if status != 100:
            body = _parse_json(raw)
            raise InterserverClientError(
                f"{path} נדחה לפני שליחה ({status}): {body.get('detail')}")
        sent = 0
        try:
            for chunk in chunks:
                self._conn.sendall(chunk)
                sent += len(chunk)
                if on_progress is not None:
                    on_progress(sent)
        except Exception as exc:                                 # noqa: BLE001
            raise InterserverClientError(
                f"החיבור למשני נותק אחרי {sent} מתוך {total} בייטים: {exc}") from exc
        if sent != total:
            raise InterserverClientError(
                f"המקור הניב {sent} בייטים במקום {total} — ההעברה לא הושלמה")
        # המשני מאמת עכשיו בלי לשלוח בייט — תקרה יחסית לגודל, לא תקרת המנה.
        self._conn.limit(max(STREAM_IDLE_TIMEOUT, total / VERIFY_MIN_RATE), total=True,
                         waiting_for=f"אימות {total} בייטים במשני")
        status, _headers, raw = _read_http_response(self._conn)
        body = _parse_json(raw)
        if status != 200:
            raise InterserverClientError(
                f"{path} נכשל ({status}): {body.get('detail')}")
        return body


class InterserverTunnelRefused(InterserverClientError):
    """המשני ענה על בקשת מנהרה בתשובה סופית (לא 200) — הקוד וה-detail שלו."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"מנהרה נדחתה ({status}): {detail}")
        self.status = status
        self.detail = detail


async def open_tunnel(host: str, port: int, *, expected_secondary_spki: str,
                      cert_path: str, key_path: str, path: str, token: str,
                      path_prefix: str = "/api/interserver/v1"):
    """פותח מנהרת מוניטור למשני מתוך asyncio (‏#655 v1): ‏TLS 1.3 של ספריית
    התקן עם תעודת-לקוח, ‏SPKI של המשני מאומת **אחרי** ה-handshake ולפני
    שבייט אפליקטיבי יוצא (סטנדרט ssl אינו מציע callback לפני; התעודה
    שהוצגה אינה סוד). מחזיר ``(reader, writer)`` אחרי ``200`` מהמשני; תשובה
    אחרת → ``InterserverTunnelRefused`` עם הקוד וה-detail.

    בניגוד ל-``PinnedMTLSClient`` (pyOpenSSL, חוסם — ל-pairing עם exporter
    ולהעברות ברקע), כאן אין צורך ב-exporter: הטוקן כבר כרוך לתעודה (#740),
    ומה שנדרש הוא זרם asyncio שאפשר לגשר ל-WebSocket."""
    import asyncio
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE                 # ההצמדה היא על SPKI, למטה
    ctx.load_cert_chain(cert_path, key_path)
    reader, writer = await asyncio.open_connection(host, port, ssl=ctx,
                                                   server_hostname=host)
    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        der = ssl_obj.getpeercert(binary_form=True) if ssl_obj else None
        if ssl_obj is None or ssl_obj.version() != "TLSv1.3":
            raise InterserverClientError("השרת ירד מ-TLS 1.3 — נדחה")
        if not der or not interserver_auth.verify_pinned_spki(der, expected_secondary_spki):
            raise InterserverClientError("‏SPKI של שרת המשנה אינו תואם את ההצמדה — נדחה")
        request = (
            f"GET {path_prefix.rstrip('/')}{path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Connection: close\r\n"
            f"ImageSrv-Protocol-Version: {interserver_auth.PROTOCOL_VERSION}\r\n"
            f"Authorization: Bearer {token}\r\n\r\n"
        ).encode()
        writer.write(request)
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        status = int(lines[0].split(b" ")[1])
        headers = {}
        for line in lines[1:]:
            if b":" in line:
                k, _, v = line.partition(b":")
                headers[k.strip().lower().decode()] = v.strip().decode()
        if status != 200:
            length = int(headers.get("content-length", "0"))
            body = await reader.readexactly(length) if length else b""
            raise InterserverTunnelRefused(status, str(_parse_json(body).get("detail")))
        return reader, writer
    except BaseException:
        writer.close()
        raise


def fetch_server_spki(host: str, port: int, *, timeout: float = 10.0) -> str:
    """קורא את תעודת השרת (בלי הצמדה ובלי תעודת-לקוח) ומחזיר את ה-SPKI שלה,
    כדי שהמנהל יַשווה אותו מול מה שהמשני מציג לפני שהוא שולח את הקוד."""
    from OpenSSL import SSL
    ctx = SSL.Context(SSL.TLS_METHOD)
    ctx.set_min_proto_version(SSL.TLS1_3_VERSION)
    ctx.set_max_proto_version(SSL.TLS1_3_VERSION)
    ctx.set_verify(SSL.VERIFY_NONE, lambda *a: True)
    raw = socket.create_connection((host, port), timeout=timeout)
    # ‏#902: socket עם timeout הוא non-blocking ל-pyOpenSSL, ו-``do_handshake``
    # זרק ``WantReadError`` כשה-ServerHello התעכב. התיקון אז היה
    # ``settimeout(None)`` — וזה בדיוק המוקש של #1286: משני שמקבל TCP ונתקע
    # תקע את ה-preview לנצח (#1292). כאן, כמו ב-``open``: לא-חוסם, וכל
    # המתנה דרך ``_TimedConn`` (select) עם תקרה כוללת ל-handshake.
    raw.setblocking(False)
    # codeql[py/insecure-protocol] false positive: set_min/max_proto_version נועלים על TLS 1.3 למעלה (#291)
    ssl_conn = SSL.Connection(ctx, raw)
    ssl_conn.set_connect_state()
    conn = _TimedConn(ssl_conn, raw)
    conn.limit(timeout, total=True, waiting_for="handshake")
    try:
        conn.do_handshake()
        return interserver_auth.spki_sha256(conn.get_peer_certificate())
    finally:
        try:
            conn.close()
        finally:
            raw.close()


def pair_secondary(url: str, *, code: str, expected_secondary_spki: str,
                   cert_pem: bytes, key_pem: bytes, primary_id: str) -> dict:
    """נוחות: פותח כתובת בין-שרתית מפורשת, מבצע pairing, ומחזיר את הטוקן."""
    host, port, _path = parse_interserver_url(url)
    with PinnedMTLSClient(host, port, expected_secondary_spki=expected_secondary_spki,
                          cert_pem=cert_pem, key_pem=key_pem) as client:
        return client.pair(code=code, primary_id=primary_id)


def ping_secondary(url: str, *, token: str, expected_secondary_spki: str,
                   cert_pem: bytes, key_pem: bytes) -> dict:
    host, port, _path = parse_interserver_url(url)
    with PinnedMTLSClient(host, port, expected_secondary_spki=expected_secondary_spki,
                          cert_pem=cert_pem, key_pem=key_pem) as client:
        return client.ping(token)
