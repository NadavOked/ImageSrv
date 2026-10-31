"""השרת כ-IGMP querier ברשת ההפצה (‏#1972, הכרעת נדב 22/10).

סוויץ' עם IGMP snooping (ה-Cisco של המכללה) לומד מי רוצה את זרם
המולטיקאסט מתשובות המחשבים ל**שאילתות** IGMP. בלי querier ברשת אין
שאילתות, הטבלה של הסוויץ' מתיישנת, והזרם מוצף לכל הפורטים או נחסם —
תלוי בדגם. השרת שולח לכן שאילתה כללית (IGMPv2, ‏224.0.0.1, ‏TTL 1, ‏Router
Alert) כל `QUERY_INTERVAL` שניות על כרטיס ההפצה.

**למה לא `multicast_querier` ולא igmpproxy.** ‏`multicast_querier` קיים רק
על גשר לינוקס (‏/sys/class/net/<br>/bridge/) — בייצור כרטיס ההפצה הוא
כרטיס פיזי, ורק במעבדה הוא br0. ‏igmpproxy הוא פרוקסי בין upstream
ל-downstream; לרשת ההפצה אין upstream. סוקט raw אחד עובד על שניהם.

**querier אחר ברשת** (נתב, הסוויץ' עצמו) מזוהה לפי שאילתות **נכנסות**. לפי
RFC 2236 הכתובת הנמוכה מנצחת: querier אחר עם כתובת נמוכה משלנו שנשמע
בתוך `OTHER_PRESENT` שניות — השרת אינו שולח. כדי לשמוע אותו, חומת האש
מקבלת IGMP על כרטיס ההפצה (‏deploy/nftables-rules.sh); בלי זה הקרנל זורק
את השאילתות לפני הסוקט, ו"לא נשמע querier אחר" היה "לא יכולנו לשמוע".

**עיקרון 5.** "פעיל" רק אחרי `sendto` שהחזיר את אורך החבילה; שאילתה
שלא נשלחה מזה פי שניים מהמרווח — אדום, לא ירוק ישן.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import struct
import threading
import time
from typing import Callable

from server.db import get_setting

log = logging.getLogger("imagesrv.igmp")

#: ‏"false" בלבד מכבה; חסר = דלוק (הכרעת נדב: השרת **הוא** ה-querier).
SETTING = "igmp_querier"
QUERY_INTERVAL = 125.0                       # RFC 2236 Query Interval
MAX_RESPONSE_DS = 100                        # 10.0 שניות, ביחידות של 1/10
#: ‏Other Querier Present Interval = 2 × 125 + 10/2.
OTHER_PRESENT = 2 * QUERY_INTERVAL + MAX_RESPONSE_DS / 20
ALL_HOSTS = "224.0.0.1"
ROUTER_ALERT = b"\x94\x04\x00\x00"           # RFC 2113
MEMBERSHIP_QUERY = 0x11
#: כמה זמן מחזור אחד מחזיק את הסוקט לפני שהוא קורא שוב את כתובת הכרטיס.
CYCLE_SECONDS = 60.0
RETRY_SECONDS = 10.0


def enabled(conn) -> bool:
    return get_setting(conn, SETTING) != "false"


def parse_setting(value) -> str | None:
    """‏true/false בלבד (גם bool של JSON); כל ערך אחר — None (סירוב)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value if value in ("true", "false") else None


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def query_packet() -> bytes:
    """שאילתה כללית, IGMPv2: סוג, זמן תגובה מרבי, checksum, קבוצה 0.0.0.0."""
    body = struct.pack("!BBH4s", MEMBERSHIP_QUERY, MAX_RESPONSE_DS, 0, bytes(4))
    return body[:2] + struct.pack("!H", checksum(body)) + body[4:]


def query_source(datagram: bytes) -> str | None:
    """כתובת המקור אם החבילה (IP + IGMP, כפי שסוקט raw מקבל) היא שאילתה."""
    if len(datagram) < 20 or datagram[0] >> 4 != 4:
        return None
    header = (datagram[0] & 0x0F) * 4
    if datagram[9] != socket.IPPROTO_IGMP or len(datagram) < header + 8:
        return None
    if datagram[header] != MEMBERSHIP_QUERY:
        return None
    return socket.inet_ntoa(datagram[12:16])


def firewall_hears(ruleset: str | None, interface: str) -> bool | None:
    """האם חומת האש **הטעונה** מעבירה IGMP נכנס בכרטיס. ‏None = לא נקרא.

    בלי טבלת imagesrv אין policy drop שלנו — הכול עובר. שרת ששודרג וחומת
    האש שלו נכתבה לפני #1972 זורק IGMP עד שהיא נכתבת מחדש (שמירת שירותי
    כרטיס או הגדרת רשת ההפצה) — ואז "לא נשמע querier אחר" אינו ראיה."""
    if ruleset is None:
        return None
    if "table inet imagesrv" not in ruleset:
        return True
    return f'iifname "{interface}" ip protocol igmp accept' in ruleset


def open_socket(interface: str, address: str):
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IGMP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode())
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_OPTIONS, ROUTER_ALERT)
    except OSError:
        sock.close()
        raise
    return sock


class Querier:
    """מצב ה-querier בכרטיס אחד. ‏`run_forever` בתהליכון daemon (‏main בלבד)."""

    def __init__(self, conn, interface: str, address: Callable[[], str | None], *,
                 gate: Callable[[str], str | None] | None = None,
                 hears: Callable[[], bool | None] | None = None,
                 opener=open_socket, clock=time.monotonic) -> None:
        self.conn, self.interface, self.address = conn, interface, address
        self.gate, self.hears, self.opener, self.clock = gate, hears, opener, clock
        self.own: str | None = None
        self.last_sent: float | None = None
        self.error: str | None = None
        self.other: str | None = None
        self.other_seen: float | None = None

    # --- ההכרעה -------------------------------------------------------------

    def other_present(self, now: float) -> str | None:
        if self.other_seen is None or now - self.other_seen > OTHER_PRESENT:
            return None
        return self.other

    def other_wins(self, now: float) -> bool:
        other = self.other_present(now)
        if other is None or self.own is None:
            return False
        return ipaddress.ip_address(other) < ipaddress.ip_address(self.own)

    def refused(self) -> str | None:
        if not enabled(self.conn):
            return "כבוי בהגדרות"
        return self.gate(self.interface) if self.gate else None

    def should_send(self, now: float) -> bool:
        if self.refused() is not None or self.other_wins(now):
            return False
        return self.last_sent is None or now - self.last_sent >= QUERY_INTERVAL

    def observe(self, datagram: bytes, now: float) -> None:
        source = query_source(datagram)
        if source is None or source == self.own:
            return
        self.other, self.other_seen = source, now

    # --- הלולאה -------------------------------------------------------------

    def cycle(self, stop: threading.Event, seconds: float = CYCLE_SECONDS) -> None:
        """מחזור אחד: כתובת, סוקט, ובתוכו שליחה בזמן והאזנה לשאילתות זרות."""
        own = self.address()
        if not own:
            raise OSError(f"לכרטיס {self.interface} אין כתובת IPv4")
        self.own = own
        sock = self.opener(self.interface, own)
        self.error = None
        try:
            end = self.clock() + seconds
            while not stop.is_set() and self.clock() < end:
                now = self.clock()
                if self.should_send(now):
                    packet = query_packet()
                    if sock.sendto(packet, (ALL_HOSTS, 0)) != len(packet):
                        raise OSError("sendto לא שלח את כל השאילתה")
                    self.last_sent, self.error = now, None
                sock.settimeout(1.0)
                try:
                    datagram, _peer = sock.recvfrom(2048)
                except (socket.timeout, TimeoutError):
                    continue
                self.observe(datagram, self.clock())
        finally:
            sock.close()

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.cycle(stop)
            except Exception as exc:  # noqa: BLE001 — תהליכון רקע: נאמר, לא נבלע
                if str(exc) != self.error:
                    log.warning("igmp querier on %s: %s", self.interface, exc)
                self.error = str(exc)
                stop.wait(RETRY_SECONDS)

    # --- מסך הבריאות ----------------------------------------------------------

    def health(self) -> tuple[str, str]:
        now = self.clock()
        other = self.other_present(now)
        refused = self.refused()
        if refused is not None:
            seen = (f" · querier אחר ברשת: {other}" if other else
                    " · לא נשמע querier אחר — סוויץ' עם IGMP snooping עלול להציף או לחסום את השידור")
            return "off", refused + seen
        if self.error is not None:
            return "bad", f"לא פועל על {self.interface}: {self.error}"
        if self.other_wins(now):
            return "ok", f"יש querier אחר ברשת ({other}) — השרת אינו שולח שאילתות"
        if self.last_sent is None:
            return "unknown", f"טרם נשלחה שאילתה על {self.interface}"
        age = int(now - self.last_sent)
        if age > 2 * QUERY_INTERVAL:
            return "bad", f"לא נשלחה שאילתה על {self.interface} מזה {age} שניות"
        text = (f"פעיל על {self.interface} — שאילתה כל {int(QUERY_INTERVAL)} שניות, "
                f"האחרונה לפני {age} שניות")
        if other:
            return "ok", text + f" · querier נוסף ({other}) — השרת קודם לו"
        hears = self.hears() if self.hears else True
        if hears is False:
            return "warn", (text + f" · חומת האש אינה מקבלת IGMP על {self.interface}, ולכן "
                            "querier אחר לא יישמע — שמירת שירותי הכרטיס כותבת אותה מחדש")
        if hears is None:
            return "warn", text + " · לא נבדק אם חומת האש מקבלת IGMP — querier אחר אולי לא נשמע"
        return "ok", text
