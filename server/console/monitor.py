"""Admin-only browser-to-machine RFB proxy and monitor boot gate."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect

from server.console import auth
from server.agent import registry
from server.agent.api import ServerContext
from server.db import get_setting, journal, monitor_rekeyed, set_setting

MONITOR_PORT = 5900
ONLINE_SECONDS = 90
STATION_KEY = "monitor:stations"
CMDLINE_PARAM = "imagesrv.monitor=1"
CMDLINE_PREFIX = "imagesrv.monitor"

#: מה שמקלידים כדי להדליק את המוניטור בכל התחנות. ‏ASCII, וזהה למה
#: שנכנס לשורת הקרנל — מי שמקליד יודע בדיוק מה הוא מדליק.
MONITOR_CONFIRM = "imagesrv.monitor"

#: קוד סגירת ה-WebSocket כשאין cookie תקף (טרם התחברות) וכשמחובר אך
#: אינו admin. הסגירה קורית **לפני** פתיחת ה-TCP למכונה, ולכן היעדר
#: הרשאה לעולם אינו נוגע בשירות ה-RFB — הבקרה השלילית מוכיחה בדיוק זאת.
WS_UNAUTHENTICATED = 4401
WS_FORBIDDEN = 4403

#: ‏#839: הפרוקסי לא הצליח להזדהות מול המוניטור **במכונה** — המכונה לא
#: דיווחה סוד (סוכן ישן), המוניטור לא הציע את סוג-האבטחה שלנו (מוניטור
#: ישן, או מי שמתחזה לו), או שדחה את הסוד. מובחן מ-4403 (המשתמש בדפדפן
#: אינו admin) ומ-4502 (אין מי שיענה על 5900).
WS_MACHINE_AUTH = 4512

#: The authenticated machine-side TCP stream ended while the browser was still
#: watching.  This is deliberately different from a browser/network close
#: (normally 1006) so the last framebuffer cannot masquerade as a live screen.
WS_MACHINE_GONE = 4513
MACHINE_GONE_REASON = "החיבור למחשב נותק — ייתכן שהמחשב כבה"

#: ‏#1129: הגשר סוגר את שני הצדדים אחרי IDLE_SECONDS בלי בייט אחד לאף כיוון
#: — ‏WS/TCP חצי-פתוח היה משאיר task ו-`active` תפוס לנצח.
WS_IDLE = 4408
IDLE_SECONDS = 600

# The first hello makes a booting machine visible before monitor_start() has a
# framebuffer to serve and binds 5900.  Keep the accepted WebSocket open (the
# browser continues to show "מתחבר…") while that short startup window closes.
MONITOR_CONNECT_WAIT_SECONDS = 15.0
MONITOR_CONNECT_ATTEMPT_SECONDS = 3.0
MONITOR_CONNECT_BACKOFF = (0.25, 0.5, 1.0, 2.0, 3.0)

#: ‏#1129: ‏Origin זר (דף באתר אחר שפתח WebSocket עם ה-cookie של המנהל —
#: ‏SameSite=lax אינו מגן על WS בכל הדפדפנים) נסגר **לפני** accept:
#: תוקף לא מקבל 101 ולא הסבר.
ORIGIN_REFUSED = "Origin אינו תואם לשרת"

#: ‏RFC 6455 §5.5.1: מסגרת close נושאת עד 125 בייט — 2 לקוד ו-123 לסיבה.
CLOSE_REASON_MAX_BYTES = 123

#: ‏#1129: הודעות לקוח→שרת של RFB 3.8 שהדפדפן רשאי לשלוח, ואורכן הקבוע.
#: ‏SetEncodings (2), ‏ClientCutText (6), ‏ClientFence (248) ו-QEMU (255) משתני-
#: אורך ונפתרים ב-`client_message_length`. ‏#1392: ‏150 (EnableContinuousUpdates),
#: ‏248 ו-255 הם מה ש-noVNC 1.6 (מסך ה-VM) מוסיף על monitor.js.
RFB_CLIENT_CUT_TEXT = 6
RFB_CLIENT_FIXED_LENGTHS = {0: 20, 3: 10, 4: 8, 5: 6, 150: 10}
RFB_CLIENT_FENCE = 248
RFB_QEMU = 255
#: ‏#1392: תקרה להודעת לקוח אחת. הגדולה הלגיטימית היא SetEncodings מלא
#: (‏4+4·65535). אורך שמבטיח יותר — נסגר בשם, ולא ממתינים לו ובולעים כל מה
#: שאחריו (כך -8 של Extended Clipboard נקרא 4GB והמסך קפא בלי סגירה).
RFB_CLIENT_MESSAGE_MAX = 1 << 20
POWER_PREFIX = "imagesrv-power:"
POWER_ACTIONS = ("reboot", "poweroff")
#: ‏#1852 (הכרעת נדב 19/10): מכונה אחת — צופה אחד. מנהל שני רואה מי מחזיק
#: ו**מבקש** שליטה, כמו ב-VM (‏#1334): המחזיק מאשר; 60 ש' בלי תשובה = סירוב;
#: מחזיק שלא נראה 2 דק' (הדף שלו לא שאל על `…/control`) — אישור אוטומטי.
CONTROL_REQUEST_SECONDS = 60
HOLDER_GONE_SECONDS = 120
#: אחרי העברה: המכונה שמורה למקבל עד שיתחבר — אחר לא "חוטף" אותה באמצע.
GRANT_HOLD_SECONDS = 30
#: אותו מנהל פתח את המכונה במקום אחר ("פתח בחלון נפרד", רענון): הצפייה עוברת.
WS_MOVED = 4410
#: השליטה הועברה למנהל אחר (אישור, או אוטומטי כשהמחזיק לא נראה).
WS_HANDED_OVER = 4411
POWER_NAME_STALE = "שם המחשב השתנה מאז שהמוניטור נפתח — סגרו ופתחו אותו מחדש"
POWER_OVER_WS_REFUSED = "פקודת כוח אינה עוברת ב-WebSocket — רק POST …/power"


def origin_allowed(headers) -> bool:
    """‏#1129: ‏`Origin` חייב להתאים ל-`Host` של הבקשה, או להיעדר (לקוח שאינו
    דפדפן — ‏wsmon.py, בדיקות). דפדפן תמיד שולח Origin ב-WebSocket, ולכן
    היעדרו אינו מסלול עוקף לדף זר; ‏`null` (sandbox/file://) נדחה."""
    origin = headers.get("origin")
    if origin is None:
        return True
    host = headers.get("host", "")
    return bool(host) and urlsplit(origin).netloc.lower() == host.lower()


def client_message_length(buf: bytes) -> int | None:
    """אורך ההודעה הראשונה ב-buf, או None כשחסרים בייטים כדי לקבוע.
    סוג לא מוכר → ValueError."""
    kind = buf[0]
    if kind in RFB_CLIENT_FIXED_LENGTHS:
        return RFB_CLIENT_FIXED_LENGTHS[kind]
    if kind == 2:                                 # SetEncodings: pad, u16 count
        if len(buf) < 4:
            return None
        return 4 + 4 * struct.unpack(">H", buf[2:4])[0]
    if kind == RFB_CLIENT_CUT_TEXT:               # pad(3), s32 length
        if len(buf) < 8:
            return None
        # ‏Extended Clipboard: אורך שלילי = |אורך| בייטים מורחבים (‏#1392).
        return 8 + abs(struct.unpack(">i", buf[4:8])[0])
    if kind == RFB_CLIENT_FENCE:                  # pad(3), u32 flags, u8 len
        if len(buf) < 9:
            return None
        return 9 + buf[8]
    if kind == RFB_QEMU:                          # u8 sub-type
        if len(buf) < 2:
            return None
        if buf[1] == 0:                           # Extended Key Event
            return 12
    raise ValueError(kind)


def cut_text_extended(buf: bytes) -> bool:
    """‏ClientCutText (שכבר ידוע שיש בו 8 בייטים) באורך שלילי — Extended
    Clipboard. noVNC 1.6 שולח כזה מעצמו, כתשובה ל-caps של QEMU 10."""
    return struct.unpack(">i", buf[4:8])[0] < 0


def power_frame(action: str) -> bytes:
    """‏ClientCutText (סוג 6) עם אסימון הכוח ש-monitor.c מזהה (#781)."""
    body = (POWER_PREFIX + action).encode("ascii")
    return b"\x06\x00\x00\x00" + struct.pack(">I", len(body)) + body


def machine_name(conn, canonical: str) -> str | None:
    """השם הקנוני של המכונה (`suffix`) — מה שמקלידים לאישור כוח."""
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?",
                       (canonical,)).fetchone()
    return None if row is None else row["suffix"]


def close_reason(text: str) -> str:
    """סיבת סגירה שנכנסת במסגרת close. עברית היא 2 בייט לתו, ו-`websockets`
    זורק ProtocolError על סיבה ארוכה מ-123 בייט — מה שלפני #904 לא קרה
    מעולם, כי הסיבה נזרקה לפח יחד עם ה-handshake."""
    data = text.encode("utf-8")
    if len(data) <= CLOSE_REASON_MAX_BYTES:
        return text
    return data[:CLOSE_REASON_MAX_BYTES].decode("utf-8", "ignore")


async def accept_browser(websocket: WebSocket) -> None:
    """מקבל את ה-WebSocket **לפני** כל בדיקה, כדי שדחייה תגיע לדפדפן כקוד
    וסיבה (‏#904 סעיף 4). לפי מפרט ASGI, ‏`websocket.close` לפני accept הוא
    דחיית ה-handshake — uvicorn מגיש אותה כ-HTTP 403 בשני המימושים
    (‏wsproto/websockets), והדפדפן רואה 1006 בלי קוד ובלי סיבה: 4401,
    ‏4403, ‏4404, ‏4409, ‏4502 ו-4512 כולם נראו "החיבור נסגר". ה-accept אינו
    שולח בייט אחד של RFB ואינו נוגע במכונה — השער (cookie/admin) עדיין
    סוגר לפני TCP, וזו הבקרה השלילית של #859."""
    offered = websocket.headers.get("sec-websocket-protocol", "")
    protocol = "binary" if "binary" in {
        item.strip() for item in offered.split(",")
    } else None
    await websocket.accept(subprotocol=protocol)

#: ‏RFB security type 2 — מסגור VNC Authentication (challenge של 16
#: בייטים, תשובה של 16 בייטים). התשובה היא HMAC-SHA256(סוד, challenge)
#: קטום ל-16 בייטים (#1077); הסוד עצמו אינו עובר על 5900.
RFB_SECURITY_SECRET = 2
RFB_VERSION = b"RFB 003.008\n"
MONITOR_AUTH_HMAC = "hmac"
HMAC_RESPONSE_LEN = 16


def flag_json(enabled: bool) -> str:
    """ייצוג ההגדרה: ‏`{"enabled": bool}`, כמו מתגי `port:<id>`."""
    return json.dumps({"enabled": bool(enabled)})

OpenConnection = Callable[
    [str, int],
    Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]],
]


async def connect_machine(
    open_connection: OpenConnection,
    ip: str,
    *,
    total_seconds: float | None = None,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to a monitor that may still be starting, within one deadline.

    A hello is deliberately sent before ``monitor_start`` knows the machine's
    role, and the monitor then waits for the GUI framebuffer mirror.  Refused
    connections in that interval are transient; timeouts and all other
    ``OSError`` failures remain bounded by the same deadline.
    """
    if total_seconds is None:
        total_seconds = MONITOR_CONNECT_WAIT_SECONDS
    loop = asyncio.get_running_loop()
    deadline = loop.time() + total_seconds
    attempt = 0
    last_error: OSError | asyncio.TimeoutError | None = None
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            assert last_error is not None
            raise last_error
        try:
            return await asyncio.wait_for(
                open_connection(ip, MONITOR_PORT),
                timeout=min(MONITOR_CONNECT_ATTEMPT_SECONDS, remaining),
            )
        except (OSError, asyncio.TimeoutError) as exc:
            last_error = exc
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise last_error
        delay = MONITOR_CONNECT_BACKOFF[
            min(attempt, len(MONITOR_CONNECT_BACKOFF) - 1)
        ]
        attempt += 1
        await asyncio.sleep(min(delay, remaining))


#: ‏#1850: מכונה שנעלמה בלי FIN (‏`reboot -f`, ‏`poweroff -f`, כיבוי idle, חשמל,
#: כבל) — בלי keepalive הגשר חיכה לה עד `IDLE_SECONDS` (10 דק') עם מסך קפוא
#: "מחובר". 10 ש' שקט, ואז שלוש בדיקות כל 5 ש': מכונה מתה מזוהה תוך ~25 ש',
#: ומכונה שאותחלה (קרנל חדש עונה RST) מיד. כמה בייטים בדקה, רק בזמן צפייה.
KEEPALIVE_IDLE_SECONDS = 10
KEEPALIVE_INTERVAL_SECONDS = 5
KEEPALIVE_PROBES = 3


def keep_alive(sock) -> None:
    """‏TCP keepalive על ה-socket אל המכונה (#1850) — במוניטור המקומי ובמנהרה
    מהמשני. כשל ה-keepalive מגיע לקורא כ-`TimeoutError`/`ConnectionError`, והגשר
    נסגר `machine` (4513) — וה-monitor.js מתחבר מחדש לבד."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for name, value in (("TCP_KEEPIDLE", KEEPALIVE_IDLE_SECONDS),
                        ("TCP_KEEPINTVL", KEEPALIVE_INTERVAL_SECONDS),
                        ("TCP_KEEPCNT", KEEPALIVE_PROBES)):
        option = getattr(socket, name, None)
        if option is not None:          # Linux (השרת) — כולן; ווינדוס ישן — חלק
            sock.setsockopt(socket.IPPROTO_TCP, option, value)


def connect_error_reason(exc: OSError | asyncio.TimeoutError) -> str:
    """A short Hebrew reason suitable for the WebSocket close frame."""
    if isinstance(exc, ConnectionRefusedError):
        return "המכונה סירבה לחיבור"
    if isinstance(exc, asyncio.TimeoutError):
        return "ניסיון החיבור לא הסתיים"
    if isinstance(exc, OSError) and exc.errno is not None:
        return f"שגיאת רשת {exc.errno}"
    return "שגיאת רשת"


def stations_enabled(conn) -> bool:
    try:
        raw = get_setting(conn, STATION_KEY)
        return bool(raw and json.loads(raw).get("enabled") is True)
    except (ValueError, AttributeError):
        return False


def station_cmdline(extra: tuple[str, ...], enabled: bool) -> tuple[str, ...]:
    """Make the monitor gate single-source: the console switch only."""
    kept = tuple(item for item in extra
                 if not item.startswith(CMDLINE_PREFIX))
    return kept + ((CMDLINE_PARAM,) if enabled else ())


def seen_within(last_seen: str | None,
                now: datetime | None = None) -> bool:
    """‏"מחובר" לרשימת המוניטור: אותו חלון (‏ONLINE_SECONDS) שבו
    ‏``_target`` מסכים לפתוח חיבור. חותמת חסרה, פגומה או נאיבית = לא
    מחובר — לא ידוע אינו "כן" (עיקרון 5)."""
    if not last_seen:
        return False
    try:
        seen = datetime.fromisoformat(last_seen)
    except (TypeError, ValueError):
        return False
    if seen.tzinfo is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current - seen <= timedelta(seconds=ONLINE_SECONDS)


def machine_rows(conn, now: datetime | None = None) -> list[dict]:
    """רשימת המכונות של דף המוניטור (#822): רק build/cloner, ‏``online``
    נקבע **בשרת** באותו חלון-נוכחות של ``_target``. משמש גם את הראשי
    שצופה במשני (#655 v1) — אותה רשימה, אותו חישוב."""
    rows = conn.execute(
        "SELECT m.mac, m.suffix, g.role, d.ip, d.last_seen, d.progress_seen, d.prompt "
        "FROM machines m "
        "JOIN groups g ON g.id = m.group_id "
        "LEFT JOIN net_devices d ON d.mac = m.mac "
        "WHERE g.role IN ('build', 'cloner') "
        "ORDER BY g.role, m.suffix"
    ).fetchall()
    return [
        {
            "mac": r["mac"],
            "name": r["suffix"],
            "role": r["role"],
            "ip": r["ip"],
            # ‏#1839: hello **או** progress — משכפל שכותב אינו שולח hello.
            "online": bool(r["ip"]) and (seen_within(r["last_seen"], now)
                                         or seen_within(r["progress_seen"], now)),
            "prompt": r["prompt"],   # #906: השאלה שממתינה לאדם, או None
        }
        for r in rows
    ]


def hmac_response(secret: str, challenge: bytes) -> bytes:
    """HMAC-SHA256(secret, challenge) truncated to the 16-byte RFB response."""
    return hmac.new(bytes.fromhex(secret), challenge,
                    hashlib.sha256).digest()[:HMAC_RESPONSE_LEN]


#: ‏#1837: אותה תווית כמו `REKEY_LABEL` ב-`agent/monitor.c` — בייט-בייט.
REKEY_LABEL = b"imagesrv-monitor-rekey"

#: ‏#1837: SecurityResult שאינו 0 — המכונה דחתה את התשובה של השרת. בעברית
#: ובלי הסיבה של LibVNCServer (‏"password check failed!" באנגלית, באמצע
#: פס RTL). הסיבה הנפוצה: המכונה אותחלה וה-hello שלה עוד לא הגיע.
MACHINE_REJECTED_SECRET = "המכונה דחתה את אימות השרת — ממתינים לדיווח הבא שלה"


def next_secret(secret: str, challenge: bytes) -> str:
    """‏#1837: הסוד הבא, אחרי אימות שהצליח — מה ש-`rekey_secret` במכונה
    גוזר מאותו סוד ומאותו challenge: HMAC-SHA256(סוד, תווית‖challenge)
    הקטום ל-16 בייטים, כ-32 hex קטנות (הצורה של `monitor_secret`).

    שני הצדדים גוזרים אותו בלי לשלוח אותו, ולכן החיבור הבא מצליח מיד —
    בלי לחכות ל-hello. תשובה שנלכדה קודם נשענת על סוד שכבר אינו בתוקף."""
    return hmac.new(bytes.fromhex(secret), REKEY_LABEL + challenge,
                    hashlib.sha256).digest()[:HMAC_RESPONSE_LEN].hex()


def _target(ctx: ServerContext, mac: str,
            now: datetime | None = None
            ) -> tuple[str, str, str | None, str | None]:
    """‏(ip, role, monitor_secret, monitor_auth) של מכונה שמותר לפתוח אליה
    מוניטור. הסוד (#839) חוזר כמו שהוא — ‏None כשהמכונה מעולם לא דיווחה
    אחד. ‏monitor_auth הוא ``hmac`` אחרי #1077, או None לסוכן ישן."""
    canonical = registry.normalize_mac(mac)
    if canonical is None:
        raise HTTPException(404, "מכונה לא מוכרת")

    row = ctx.conn.execute(
        "SELECT d.ip, d.last_seen, d.progress_seen, d.monitor_secret, d.monitor_auth, g.role "
        "FROM machines m "
        "JOIN groups g ON g.id = m.group_id "
        "LEFT JOIN net_devices d ON d.mac = m.mac "
        "WHERE m.mac = ?",
        (canonical,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "מכונה לא מוכרת")
    if row["role"] not in {"build", "cloner"}:
        raise HTTPException(409, "מוניטור עדיין אינו זמין למחשב כיתה")
    if not row["ip"] or not row["last_seen"]:
        raise HTTPException(409, "המכונה אינה מחוברת")

    try:
        seen = datetime.fromisoformat(row["last_seen"])
    except (TypeError, ValueError):
        raise HTTPException(409, "זמן הנוכחות של המכונה אינו תקין") from None
    if seen.tzinfo is None:
        raise HTTPException(409, "זמן הנוכחות של המכונה אינו תקין")

    current = now or datetime.now(timezone.utc)
    # ‏#1839: דיווח progress שהתקבל בחלון הוא ראיה שהמכונה חיה — בזמן כתיבה
    # הוא הקשר היחיד שלה, ובלעדיו המוניטור נדחה בדיוק כשהכי רוצים לצפות.
    if (current - seen > timedelta(seconds=ONLINE_SECONDS)
            and not seen_within(row["progress_seen"], current)):
        raise HTTPException(409, "המכונה אינה מחוברת")
    return row["ip"], row["role"], row["monitor_secret"], row["monitor_auth"]


class MachineAuthError(Exception):
    """המוניטור במכונה לא קיבל את הפרוקסי — הסיבה בטקסט, לדפדפן."""


async def _read_exact(reader, n: int) -> bytes:
    """‏n בייטים בדיוק; ‏EOF מוקדם הוא כשל בשם, לא bytes קצר (עיקרון 5)."""
    chunks = bytearray()
    while len(chunks) < n:
        piece = await reader.read(n - len(chunks))
        if not piece:
            raise MachineAuthError(
                f"המוניטור סגר את החיבור אחרי {len(chunks)}/{n} בייטים")
        chunks += piece
    return bytes(chunks)


async def _reason(reader) -> str:
    """מחרוזת הסיבה של RFB 3.8 (u32 אורך + טקסט). האורך נחתך: מכונה
    עוינת לא תגרום לפרוקסי להקצות 4GB, וסיבת סגירת WebSocket מוגבלת
    ממילא ל-123 בייטים."""
    length = struct.unpack(">I", await _read_exact(reader, 4))[0]
    if length > 200:
        return "(סיבה ארוכה מדי)"
    return (await _read_exact(reader, length)).decode("utf-8", "replace")[:40]


async def authenticate_machine(reader, writer, secret: str) -> str:
    """‏#839/#1077: לחיצת-יד RFB 3.8 מול imagesrv-monitor, עד SecurityResult.

    הפרוקסי — ולא הדפדפן — הוא הצד שמזדהה: הוא בוחר את סוג 2 בלבד, עונה
    ל-challenge ב-HMAC-SHA256(סוד, challenge) הקטום ל-16 בייטים, וקורא
    את התוצאה. הסוד עצמו אינו נשלח. ‏None (סוג 1) **לעולם אינו נבחר** גם
    אם הוצע: מוניטור שמציע אותו הוא סוכן שלא שודרג או מישהו שמתחזה
    למכונה, ושניהם אינם מסלול. אחרי ההצלחה המכונה ממתינה ל-ClientInit
    — והוא מגיע מהדפדפן דרך הממסר הרגיל.

    ‏#1837: מחזיר את הסוד הבא (`next_secret`) — המכונה כבר עברה אליו, והקורא
    שומר אותו (`db.monitor_rekeyed`) לפני שהחיבור הבא נפתח.
    """
    banner = await _read_exact(reader, 12)
    if not banner.startswith(b"RFB 003."):
        raise MachineAuthError("על 5900 עונה משהו שאינו RFB")
    writer.write(RFB_VERSION)
    await writer.drain()
    count = (await _read_exact(reader, 1))[0]
    if count == 0:
        raise MachineAuthError(f"המוניטור דחה את החיבור: {await _reason(reader)}")
    offered = await _read_exact(reader, count)
    if RFB_SECURITY_SECRET not in offered:
        raise MachineAuthError(
            "המוניטור במכונה אינו דורש סוד — סוכן ישן? (סוגי אבטחה "
            f"{sorted(offered)})")
    writer.write(bytes([RFB_SECURITY_SECRET]))
    await writer.drain()
    challenge = await _read_exact(reader, 16)
    writer.write(hmac_response(secret, challenge))
    await writer.drain()
    result = struct.unpack(">I", await _read_exact(reader, 4))[0]
    if result != 0:
        raise MachineAuthError(MACHINE_REJECTED_SECRET)
    return next_secret(secret, challenge)


#: ‏#1334: הודעות קלט של RFB — מקלדת (4) ועכבר (5). צופה בלבד אינו שולח אותן.
#: ‏#1392: וגם מקש QEMU (255) — noVNC שולח כך כל מקש אחרי ש-QEMU אישר -258.
RFB_INPUT_TYPES = (4, 5, RFB_QEMU)


async def bridge_browser(websocket: WebSocket, reader, writer, *,
                         idle_seconds: float = IDLE_SECONDS,
                         allow_input: Callable[[], bool] | None = None,
                         evicted: asyncio.Future | None = None) -> str:
    """מגשר WebSocket שכבר התקבל (‏`accept_browser`) לזרם RFB שכבר עבר
    אימות מול המכונה.

    הדפדפן רואה בדיוק את מה שראה תמיד (‏#839): גרסה, סוג-אבטחה None יחיד,
    ‏SecurityResult OK — ואז הבייטים עוברים לשני הכיוונים. משמש
    את המוניטור המקומי **וגם** את המוניטור דרך המשני (#655 v1), ששם
    ``reader``/``writer`` הם המנהרה מהמשני ולא TCP למכונה. חוזר עם סיבת
    הסיום — ``browser``/``machine``/``idle``/``refused`` — ליומן;
    ביטול/ניתוק מטופלים אצל הקורא.

    ‏#1129: הכיוון דפדפן→מכונה עובר **בגבולות הודעה** (RFB 3.8, סוגים
    0/2/3/4/5; ‏#1392: וגם 150/248/255 של noVNC 1.6), ו-ClientCutText (6)
    נדחה בשם (המורחב — תשובת caps של noVNC — נזרק) — עד כאן פקודת הכוח
    ‏`imagesrv-power:` נסעה בו, והאישור בהקלדת שם היה UI בלבד; עכשיו היא
    עוברת רק ב-`POST …/power` שמאמת את השם **בשרת**. סוג לא מוכר נסגר
    1002. הכיוון מכונה→דפדפן נשאר גולמי — הפרסור שלו בדפדפן, עם תקרות.
    ‏idle_seconds בלי בייט לאף כיוון → שני הצדדים נסגרים (4408)."""
    pending = bytearray()
    loop = asyncio.get_running_loop()
    last_activity = loop.time()
    outcome = "browser"

    async def from_browser() -> bytes | None:
        """מסגרת בינארית אחת; ‏None = הדפדפן התנתק."""
        nonlocal last_activity
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return None
        payload = message.get("bytes")
        if payload is None:
            # RFB is binary. Reject accidental text frames.
            await websocket.close(code=1003)
            return None
        last_activity = loop.time()
        return payload

    async def browser_exact(n: int) -> bytes | None:
        while len(pending) < n:
            payload = await from_browser()
            if payload is None:
                return None
            pending.extend(payload)
        taken = bytes(pending[:n])
        del pending[:n]
        return taken

    # ‏#839: לחיצת-היד מול הדפדפן היא של הפרוקסי, לא של המכונה —
    # המכונה כבר עברה אימות למעלה. הדפדפן רואה בדיוק את מה שראה
    # לפני: גרסה, סוג-אבטחה None יחיד, ‏SecurityResult OK. מכאן
    # והלאה הבייטים עוברים כמו שהם (ClientInit → ServerInit …).
    await websocket.send_bytes(RFB_VERSION)
    if await browser_exact(12) is None:
        return outcome
    await websocket.send_bytes(b"\x01\x01")
    choice = await browser_exact(1)
    if choice is None:
        return outcome
    if choice != b"\x01":
        await websocket.close(code=1002, reason="סוג אבטחה לא צפוי")
        return "refused"
    await websocket.send_bytes(b"\x00\x00\x00\x00")
    # ‏ClientInit (בייט shared יחיד) הוא ההודעה היחידה בלי סוג — עובר כמו
    # שהוא; מכאן כל הודעה מהדפדפן נושאת סוג.
    client_init = await browser_exact(1)
    if client_init is None:
        return outcome
    writer.write(client_init)
    await writer.drain()

    async def browser_to_machine() -> None:
        nonlocal outcome
        buf = bytearray(pending)
        pending.clear()
        while True:
            while buf:
                try:
                    length = client_message_length(buf)
                except ValueError as exc:
                    await websocket.close(code=1002,
                                          reason=f"הודעת RFB לא מוכרת: {exc}")
                    outcome = "refused"
                    return
                if length is None:
                    break
                # ‏ClientCutText רגיל נסגר כבר על הכותרת — לא ממתינים לגוף.
                # מורחב (Extended Clipboard, ‏#1392) נזרק במכוון: noVNC 1.6 שולח
                # אותו מעצמו בכל חיבור ל-QEMU, וגם הוא אינו מגיע למכונה לעולם.
                if (buf[0] == RFB_CLIENT_CUT_TEXT
                        and not cut_text_extended(buf)):
                    await websocket.close(code=WS_FORBIDDEN,
                                          reason=POWER_OVER_WS_REFUSED)
                    outcome = "refused"
                    return
                if length > RFB_CLIENT_MESSAGE_MAX:
                    await websocket.close(code=1009, reason="הודעת RFB גדולה מדי")
                    outcome = "refused"
                    return
                if len(buf) < length:
                    break
                if buf[0] == RFB_CLIENT_CUT_TEXT:
                    del buf[:length]
                    continue
                # ‏#1334: צופה בלבד — מקלדת ועכבר נזרקים כאן, בשרת; שאר
                # ההודעות (בקשות עדכון מסך, קידודים) עוברות כרגיל.
                if (allow_input is not None and buf[0] in RFB_INPUT_TYPES
                        and not allow_input()):
                    del buf[:length]
                    continue
                writer.write(bytes(buf[:length]))
                del buf[:length]
            await writer.drain()
            payload = await from_browser()
            if payload is None:
                return
            buf.extend(payload)

    async def machine_to_browser() -> None:
        nonlocal last_activity, outcome
        while True:
            try:
                payload = await reader.read(65536)
            except (ConnectionError, OSError):
                outcome = "machine"
                return
            if not payload:
                outcome = "machine"
                return
            last_activity = loop.time()
            await websocket.send_bytes(payload)

    async def idle_watch() -> None:
        nonlocal outcome
        while True:
            remaining = idle_seconds - (loop.time() - last_activity)
            if remaining <= 0:
                outcome = "idle"
                return
            await asyncio.sleep(remaining)

    upstream = asyncio.create_task(browser_to_machine())
    downstream = asyncio.create_task(machine_to_browser())
    watchdog = asyncio.create_task(idle_watch())
    tasks = (upstream, downstream, watchdog)
    # ‏#1852: הצפייה עברה — לחלון אחר של אותו מנהל, או למנהל אחר שקיבל שליטה.
    # ‏`evicted` מתמלא ב-(קוד, סיבה) מבחוץ (`call_soon_threadsafe`).
    waits = tasks + ((evicted,) if evicted is not None else ())
    try:
        done, not_done = await asyncio.wait(
            waits, return_when=asyncio.FIRST_COMPLETED)
        for task in not_done:
            if task is not evicted:
                task.cancel()
        await asyncio.gather(*(t for t in (*done, *not_done) if t is not evicted),
                             return_exceptions=True)
        if evicted is not None and evicted.done():
            code, reason = evicted.result()
            outcome = "moved"
            await websocket.close(code=code, reason=close_reason(reason))
        elif watchdog in done:
            await websocket.close(
                code=WS_IDLE,
                reason=f"המוניטור נסגר — {int(idle_seconds // 60)} דק' בלי תעבורה")
        elif downstream in done and outcome == "machine":
            await websocket.close(code=WS_MACHINE_GONE,
                                  reason=MACHINE_GONE_REASON)
    finally:
        # ביטול של ה-endpoint עצמו (‏#725) לא משאיר משימת ממסר יתומה.
        for task in tasks:
            if not task.done():
                task.cancel()
    return outcome


class _Holder:
    """‏#1852: מי מחזיק מכונה — ומאיזה event loop אפשר לסגור את הגשר שלו."""

    def __init__(self, user: str, now: float, loop: asyncio.AbstractEventLoop):
        self.user = user
        self.seen = now
        self.request: tuple[str, float] | None = None
        self.loop = loop
        self.evicted: asyncio.Future = loop.create_future()

    def evict(self, code: int, reason: str) -> None:
        def settle() -> None:
            if not self.evicted.done():
                self.evicted.set_result((code, reason))
        try:
            self.loop.call_soon_threadsafe(settle)
        except RuntimeError:            # ה-loop שלו כבר נסגר — הגשר כבר איננו
            pass


def create_monitor_router(
    ctx: ServerContext,
    *,
    open_connection: OpenConnection = asyncio.open_connection,
    now_fn: Callable[[], datetime] | None = None,
    clock: Callable[[], float] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    _, admin_only = auth.dependencies(ctx.conn)
    #: ‏#1852: מי מחזיק כל מכונה. ‏`threading.Lock` ולא asyncio: הבקשות של
    #: ‏`…/control` רצות בתהליכוני ה-threadpool, וכל WebSocket ב-TestClient
    #: רץ ב-event loop משלו. אין `await` בתוך הנעילה.
    active: dict[str, _Holder] = {}
    grants: dict[str, tuple[str, float]] = {}
    outcomes: dict[str, dict] = {}
    #: ‏#1129: הכותב אל המכונה של כל גשר פתוח — הנתיב היחיד של פקודת כוח.
    bridges: dict[str, asyncio.StreamWriter] = {}
    lock = threading.Lock()
    clock = clock or time.monotonic

    @router.get("/monitor/settings")
    def read_settings(user=Depends(admin_only)):
        """‏admin בלבד. גם הקריאה ניהולית — משתמש deploy מקבל 403:
        רשימת מי שאפשר להציץ אליו היא מידע ניהולי."""
        del user
        return {"port": MONITOR_PORT, "enabled": stations_enabled(ctx.conn)}

    @router.get("/monitor/machines")
    def list_machines(user=Depends(admin_only)):
        """‏#822: דף המוניטור בקונסולה. רק build/cloner — אותו JOIN
        ואותו חלון-נוכחות של ``_target``, כדי ש"מחובר" בדף יהיה מה
        שהשרת יסכים לחבר אליו, ולא חישוב בדפדפן."""
        del user
        return machine_rows(ctx.conn, now_fn() if now_fn else None)

    @router.put("/monitor/settings")
    def set_settings(body: dict, user=Depends(admin_only)):
        """הדלקה דורשת הקלדה מפורשת (עיקרון 7): מוניטור מעמיד שירות
        ‏RFB בכל תחנת בנייה/שיכפול, ולחיצה מקרית אינה נראית עד שמישהו
        מתחבר. כיבוי הוא הכיוון אל ברירת המחדל ואינו דורש אישור."""
        want = bool(body.get("enabled", False))
        if want and body.get("confirm") != MONITOR_CONFIRM:
            raise HTTPException(
                409, f"הדלקת מוניטור בכל התחנות — יש להקליד בדיוק: "
                     f"{MONITOR_CONFIRM}")
        set_setting(ctx.conn, STATION_KEY, flag_json(want))
        journal(ctx.conn, "monitor_stations",
                "on" if want else "off", user[0])
        return {"enabled": stations_enabled(ctx.conn)}

    def _expire(mac: str, now: float) -> None:
        """בקשה שלא נענתה 60 ש' = סירוב; שמירה למקבל שלא הגיע — פגה."""
        holder = active.get(mac)
        if holder is not None and holder.request is not None:
            by, at = holder.request
            if now - at >= CONTROL_REQUEST_SECONDS:
                holder.request = None
                outcomes[mac] = {"by": by, "outcome": "timeout"}
                journal(ctx.conn, "monitor_control_timeout", f"{mac} {by} \u2190 {holder.user}", by)
        grant = grants.get(mac)
        if grant is not None and now >= grant[1]:
            grants.pop(mac, None)

    def _hand_over(mac: str, holder: _Holder, to: str, outcome: str) -> None:
        """המכונה עוברת ל-`to`: הגשר של המחזיק נסגר 4411, והמכונה שמורה למקבל."""
        active.pop(mac, None)
        grants[mac] = (to, clock() + GRANT_HOLD_SECONDS)
        outcomes[mac] = {"by": to, "outcome": outcome}
        holder.evict(WS_HANDED_OVER, f"השליטה במכונה הועברה ל-{to}")
        journal(ctx.conn, "monitor_control_auto" if outcome == "auto" else "monitor_control_approved",
                f"{mac} {holder.user} \u2192 {to}", to)

    def reserve(mac: str, user: str) -> tuple[_Holder | None, str]:
        """‏#1852: (המחזיק החדש, "") — או (None, הסיבה ל-4409, עם שם המחזיק)."""
        loop = asyncio.get_running_loop()
        with lock:
            now = clock()
            _expire(mac, now)
            grant = grants.get(mac)
            current = active.get(mac)
            if current is not None and current.user != user:
                return None, f"המכונה בצפייה של {current.user}"
            if current is None and grant is not None and grant[0] != user:
                return None, f"השליטה במכונה הועברה ל-{grant[0]} — ממתינה שיתחבר"
            grants.pop(mac, None)
            holder = _Holder(user, now, loop)
            active[mac] = holder
        if current is not None:
            # אותו מנהל פתח את המכונה במקום אחר — הצפייה עוברת לשם.
            current.evict(WS_MOVED, "המוניטור נפתח בחלון אחר")
        return holder, ""

    def release(mac: str, holder: _Holder, writer) -> None:
        with lock:
            if active.get(mac) is holder:
                del active[mac]
            if writer is not None and bridges.get(mac) is writer:
                del bridges[mac]

    def _control_view(mac: str, user: str) -> dict:
        now = clock()
        _expire(mac, now)
        holder = active.get(mac)
        if holder is not None and holder.user == user:
            holder.seen = now
        grant = grants.get(mac)
        request = None
        if holder is not None and holder.request is not None:
            by, at = holder.request
            request = {"by": by, "seconds_left": max(0, int(at + CONTROL_REQUEST_SECONDS - now))}
        return {
            "holder": holder.user if holder else None,
            "you_hold": bool(holder and holder.user == user),
            "request": request,
            "granted_to_you": bool(grant and grant[0] == user),
            "last": outcomes.get(mac),
        }

    def _canonical_or_404(mac: str) -> str:
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            raise HTTPException(404, "מכונה לא מוכרת")
        return canonical

    @router.get("/monitor/{mac}/control")
    def control_state(mac: str, user=Depends(admin_only)):
        """‏#1852: מי מחזיק את המכונה, ובקשת שליטה פתוחה. הדף של המחזיק שואל
        כל כמה שניות — וזו גם ה"נוכחות" שלו (2 דק' בלי = לא נראה)."""
        canonical = _canonical_or_404(mac)
        with lock:
            return _control_view(canonical, user[0])

    @router.post("/monitor/{mac}/control/request")
    def control_request(mac: str, user=Depends(admin_only)):
        """‏#1852: בקשה, לא השתלטות. מחזיק שלא נראה 2 דק' — אישור אוטומטי."""
        canonical = _canonical_or_404(mac)
        who = user[0]
        with lock:
            now = clock()
            _expire(canonical, now)
            holder = active.get(canonical)
            if holder is None:
                return {"outcome": "free"}
            if holder.user == who:
                raise HTTPException(409, "המכונה כבר בצפייה שלך")
            if holder.request is not None:
                raise HTTPException(409, f"{holder.request[0]} כבר ביקש שליטה")
            if now - holder.seen >= HOLDER_GONE_SECONDS:
                _hand_over(canonical, holder, who, "auto")
                return {"outcome": "auto"}
            holder.request = (who, now)
            journal(ctx.conn, "monitor_control_request", f"{canonical} {who} \u2192 {holder.user}", who)
            return {"outcome": "pending", "seconds_left": CONTROL_REQUEST_SECONDS}

    @router.post("/monitor/{mac}/control/answer")
    def control_answer(mac: str, body: dict, user=Depends(admin_only)):
        """‏#1852: רק המחזיק עונה. אישור = הגשר שלו נסגר והמכונה שמורה למבקש."""
        canonical = _canonical_or_404(mac)
        who = user[0]
        with lock:
            now = clock()
            _expire(canonical, now)
            holder = active.get(canonical)
            if holder is None or holder.user != who:
                raise HTTPException(403, "רק מי שצופה במכונה עונה על בקשת שליטה")
            holder.seen = now
            if holder.request is None:
                raise HTTPException(409, "אין בקשת שליטה ממתינה")
            by = holder.request[0]
            holder.request = None
            if body.get("approve") is True:
                _hand_over(canonical, holder, by, "approved")
                return {"outcome": "approved"}
            outcomes[canonical] = {"by": by, "outcome": "denied"}
            journal(ctx.conn, "monitor_control_denied", f"{canonical} {who} \u2192 {by}", who)
            return {"outcome": "denied"}

    @router.post("/monitor/{mac}/power")
    async def power(mac: str, body: dict, user=Depends(admin_only)):
        """‏#1129: הפעלה-מחדש/כיבוי של המכונה המנוטרת — האישור **בשרת**.
        עד כאן הדפדפן שלח `imagesrv-power:` ב-ClientCutText על ה-WS אחרי
        בדיקת שם ב-JS בלבד, והשם הגיע מ-`?name=` בכתובת (R48 ממצא 10):
        כל מי שיש לו WS של admin כיבה מכונה בלי המודאל. עכשיו: ‏`confirm`
        מושווה ל-`suffix` **מהטבלה**, הגשר מסרב ל-ClientCutText מהדפדפן,
        והפקודה נכתבת אל המכונה מכאן. דורש מוניטור פתוח (409): הערוץ
        למכונה הוא ה-RFB המאומת של הגשר, ואין ערוץ אחר."""
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            raise HTTPException(404, "מכונה לא מוכרת")
        action = body.get("action")
        if action not in POWER_ACTIONS:
            raise HTTPException(400, "פעולה לא מוכרת — reboot או poweroff")
        name = machine_name(ctx.conn, canonical)
        if name is None:
            raise HTTPException(404, "מכונה לא מוכרת")
        if body.get("confirm") != name:
            # ‏#1850: מאז #1270 איש אינו מקליד — הדף שולח את שמו. אי-התאמה = השם
            # השתנה אחרי שהדף נפתח.
            raise HTTPException(403, POWER_NAME_STALE)
        writer = bridges.get(canonical)
        if writer is None:
            raise HTTPException(
                409, "אין מוניטור פתוח למכונה — הפקודה נוסעת דרך חיבור המוניטור")
        with lock:
            holder = active.get(canonical)
        if holder is None or holder.user != user[0]:
            # ‏#1852: הפקודה נוסעת בגשר של מי שמחזיק את המכונה — רק הוא שולח אותה.
            raise HTTPException(409, f"המכונה בצפייה של {holder.user if holder else 'אחר'}"
                                     " — בקשו שליטה כדי לשלוח פקודה")
        writer.write(power_frame(action))
        await writer.drain()
        journal(ctx.conn, "monitor_power", f"{canonical} {action}", user[0])
        return {"ok": True, "action": action}

    @router.websocket("/monitor/{mac}")
    async def monitor(
        websocket: WebSocket,
        mac: str,
    ):
        # ‏#1129: ‏Origin זר נסגר לפני accept — דף באתר אחר שמנצל את ה-cookie
        # אינו מקבל 101. הלקוח הלגיטימי
        # (monitor.js) שולח Origin של השרת עצמו.
        if not origin_allowed(websocket.headers):
            await websocket.close(code=WS_FORBIDDEN, reason=ORIGIN_REFUSED)
            return
        # ‏#904: accept לפני השער, אחרת כל סגירה למטה מגיעה לדפדפן כ-403
        # אילם (ראו `accept_browser`). ה-accept אינו פותח דבר מול המכונה.
        await accept_browser(websocket)
        # ‏השער נבדק כאן ידנית, ולא דרך `Depends(admin_only)`. ‏Depends
        # על websocket מפיל את החיבור בקוד גנרי, וה-RFB proxy הוא נתיב
        # שאסור שאי-הרשאה תיגע בו בכלל: הסגירה חייבת לקרות **לפני**
        # ‏`open_connection`, עם קוד מובחן (4401/4403) שהלקוח קורא. הבקרה
        # השלילית מסירה את בדיקת ה-admin ומראה ש-deploy מגיע ל-connector
        # במקום להיסגר 4403 — כלומר השורה הזאת היא מה שסוגר את הדלת.
        found = auth.check(ctx.conn, websocket.cookies.get(auth.COOKIE_NAME))
        if found is None:
            await websocket.close(code=WS_UNAUTHENTICATED, reason="נדרשת התחברות")
            return
        if found[1] != "admin":
            await websocket.close(code=WS_FORBIDDEN, reason="פעולה למנהל בלבד")
            return
        # ‏#705: המוניטור עובר בפורט הקונסולה — אין לו מאזין לסגור, ולכן "כבוי
        # בכרטיס" נאכף כאן, לפי הכתובת המקומית של החיבור, **לפני** כל TCP למכונה.
        gate = getattr(ctx, "nic_services", None)
        if gate is not None:
            local = (websocket.scope.get("server") or ("", 0))[0]
            why = await asyncio.to_thread(gate.refused, "monitor", local)
            if why:
                await websocket.close(code=WS_FORBIDDEN, reason=close_reason(why))
                return

        canonical = registry.normalize_mac(mac)
        if canonical is None:
            await websocket.close(code=4404, reason="מכונה לא מוכרת")
            return

        # ‏אחרי השער: אי-זמינות המכונה נסגרת גם היא כ-WebSocket, ולא
        # כ-HTTPException — חריגה בנתיב websocket אחרי השער היא כשל
        # לא מטופל, ולכן קוד הסטטוס מתורגם לקוד סגירה (4000+status).
        try:
            ip, _role, secret, machine_auth = _target(ctx, canonical,
                                              now_fn() if now_fn else None)
        except HTTPException as exc:
            await websocket.close(code=4000 + exc.status_code,
                                  reason=close_reason(str(exc.detail)))
            return
        if secret is None:
            # ‏#839: בלי סוד אין עם מה להזדהות — ולכן גם אין TCP. מוניטור
            # שהיה נפתח כאן "כמו פעם" הוא בדיוק המסלול הלא-מאומת שנסגר.
            await websocket.close(
                code=WS_MACHINE_AUTH,
                reason="המכונה לא דיווחה סוד מוניטור — סוכן ישן?")
            return
        if machine_auth != MONITOR_AUTH_HMAC:
            # ‏#1077: סוכן ישן ששולח סוד גולמי. לא נופלים לגולמי בשקט —
            # "לא הצלחנו לאמת" אינו "אימתנו" (עיקרון 5). אתחול מספיק.
            await websocket.close(
                code=WS_MACHINE_AUTH,
                reason="המכונה אינה תומכת באתגר-תגובה (HMAC) — סוכן ישן? אתחל מחדש")
            return
        holder, refused = reserve(canonical, found[0])
        if holder is None:
            # ‏#1852: מי מחזיק — וב-`…/control` אפשר לבקש ממנו שליטה.
            await websocket.close(code=4409, reason=close_reason(refused))
            return

        writer: asyncio.StreamWriter | None = None
        try:
            try:
                reader, writer = await connect_machine(open_connection, ip)
            except (OSError, asyncio.TimeoutError) as exc:
                # ‏`finally` משחרר את ההזמנה; כאן רק סוגרים את החיבור.
                await websocket.close(
                    code=4502, reason=close_reason(
                        "שירות המוניטור במכונה אינו זמין לאחר 15 שניות: "
                        f"{connect_error_reason(exc)}"))
                return
            # ‏#1850: מכונה שנעלמה בלי FIN נסגרת 4513 תוך ~25 ש', לא idle אחרי 10 דק'.
            # (מכונה מזויפת בבדיקות אינה StreamWriter ואין לה socket.)
            machine_socket = (writer.get_extra_info("socket")
                              if hasattr(writer, "get_extra_info") else None)
            if machine_socket is not None:
                keep_alive(machine_socket)

            try:
                rekeyed = await asyncio.wait_for(
                    authenticate_machine(reader, writer, secret), timeout=5.0)
            except MachineAuthError as exc:
                await websocket.close(code=WS_MACHINE_AUTH,
                                      reason=close_reason(str(exc)))
                return
            except asyncio.TimeoutError:
                await websocket.close(
                    code=WS_MACHINE_AUTH,
                    reason="המוניטור לא השלים את לחיצת-היד תוך 5 שניות")
                return
            # ‏#1837: המכונה כבר עברה לסוד הבא — שומרים אותו לפני כל דבר
            # אחר, כדי שהחיבור הבא יצליח בלי לחכות ל-hello.
            monitor_rekeyed(ctx.conn, canonical, secret, rekeyed)

            bridges[canonical] = writer
            journal(ctx.conn, "monitor_connected", canonical, found[0])
            # ניתוק/ביטול תוך כדי הגשר (ה-runner מבטל את ה-endpoint כשהדפדפן
            # הלך — ראו למטה) = "הדפדפן סגר"; חריגה אחרת = "כשל".
            outcome = "error"
            try:
                outcome = await bridge_browser(websocket, reader, writer,
                                               idle_seconds=IDLE_SECONDS,
                                               evicted=holder.evicted)
            except (WebSocketDisconnect, asyncio.CancelledError):
                outcome = "browser"
                raise
            finally:
                journal(ctx.conn, "monitor_closed", f"{canonical} {outcome}",
                        found[0])
        except (WebSocketDisconnect, asyncio.CancelledError):
            # ‏WebSocketDisconnect: הלקוח סגר בצורה מסודרת. ‏CancelledError:
            # ה-ASGI runner מבטל את משימת ה-endpoint בזמן פירוק החיבור —
            # ‏Starlette TestClient מבטל מיד אחרי websocket.disconnect, ו-uvicorn
            # נוהג כך בכיבוי. ‏`gather(return_exceptions=True)` בולע רק חריגות
            # של המשימות-הבנות; ביטול של המשימה שמריצה את ה-gather עצמה חוצה
            # אותה החוצה (#725). מגיעים לכאן רק אחרי שאחת ממשימות הממסר כבר
            # הסתיימה — סגירה רגילה, לא כשל — ואסור שהביטול יצא מה-endpoint:
            # יציאה כזו מסמנת את ה-future של ה-runner כמבוטל ו-result() זורק.
            pass
        finally:
            # ‏#1860: ביטול שנופל בתוך `wait_closed` (ה-runner מבטל בפירוק,
            # #725) — או `wait_closed` שזורק, בדיוק כשהמכונה נעלמה (#1850) —
            # דילג על `release`, והמכונה נשארה "כבר פתוח" (4409) עד אתחול.
            # השחרור יושב ב-`finally` משלו; הביטול ממשיך הלאה. סגירה שנכשלה
            # של socket למכונה שכבר איננה אינה כשל של הגשר: הוא כבר הסתיים.
            try:
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
            finally:
                release(canonical, holder, writer)

    return router
