"""‏#2442 (א): שרת DHCP או proxyDHCP **זר** ברשת ההפצה, בזמן ריצה.

מחשב ברשת ההפצה יכול להרים DHCP ו-TFTP משלו, ותחנה תאתחל ממנו. ההגנה
האמיתית היא DHCP snooping בסוויץ' (משימה של נדב, לא כאן); כאן רק **מתריעים**:
השרת מקשיב על כרטיס ההפצה, ו-DHCPOFFER (או proxyDHCP — תשובה עם אפשרויות PXE)
שמגיע ממי שאינו השרת עצמו הוא אירוע ביומן ושורה אדומה במסך הבריאות.

**האזנה פסיבית, אותה שכבה כמו השומר של #2417** — ‏AF_PACKET עם מסנן BPF בקרנל
(‏`dhcp_host._open_tap`), כך שחומת האש של השרת אינה מסתירה את ה-OFFER, והמסנן
מסנן בקרנל. בלי polling: הקריאה חוסמת עד שמסגרת מגיעה או עד סוף הפרוסה
(‏`SLICE_SECONDS`); בפרוסה נבדקים מחדש השער וזהות הכרטיס, בלי לסגור את הסוקט.

**מי "אני".** מה שה-dnsmasq שלנו באמת שלח: מסגרת שיצאה מהמארח
(‏PACKET_OUTGOING — הקרנל אומר זאת, לא החבילה). ה-MAC וה-IP שבמסגרת הם מה
שהשולח כתב, ומחשב ברשת כותב שם את שלנו בלי מאמץ — ולכן מסגרת **נכנסת** עם
ה-MAC או ה-IP של הכרטיס היא התחזות לשרת (‏`as=server`), לא "אני". החריג היחיד:
עותק של תשובה שיצאה מכאן בשניות האחרונות (‏`ECHO_SECONDS`, לפי גוף ה-BOOTP) —
לולאה בסוויץ' שהחזירה אותה אלינו.

**הצפה (#2442, ‏05/11).** כל MAC+IP אקראי הוא מקור חדש, ובלי תקרה ‏20,000
OFFERs מזויפים היו 20,000 שורות יומן ורשומות בזיכרון. נשמרים עד
`MAX_SOURCES` מקורות; הראשונים נשארים (תוקף אינו דוחק שרת זר אמיתי מהמסך),
ומה שמעבר נספר בלבד — שורת סיכום ‏`foreign_dhcp_more` לכל היותר פעם
ב-`SUMMARY_EVERY`, והשורה במסך **נשארת אדומה** ואומרת כמה עוד (עיקרון 5).

**מה אינו נראה, בכוונה מוצהר.** האזנה פסיבית רואה רק תשובות שמגיעות לכרטיס:
‏OFFER משודר (דגל broadcast, כמו אצל לקוחות PXE ברובם) ו-proxyDHCP. תשובת
unicast ללקוח אחר בסוויץ' לא מגיעה אלינו. ולכן "לא נראה שרת זר" אינו "אין
שרת זר", והטקסט במסך אומר זאת; זו עוד סיבה שההגנה היא ה-snooping.

**עיקרון 5.** ההאזנה שנכשלה (אין הרשאה, הכרטיס לא קיים) היא "לא הצלחנו
להאזין", לא "אין שרת זר". השומר פעיל רק כש-DHCP שלנו דלוק בכרטיס ההפצה
(‏`gate`) — בלי זה שרת אחר הוא אולי בדיוק מה שמתוכנן.
"""

from __future__ import annotations

import hashlib
import logging
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from server.db import journal
from server.net import dhcp_host

log = logging.getLogger("imagesrv.rogue_dhcp")

EVENT = "foreign_dhcp_seen"
#: שורת הסיכום של תשובות ממקורות שמעבר לתקרה (#2442, הצפה).
EVENT_MORE = "foreign_dhcp_more"
#: מקורות שנשמרים בזיכרון (ולכן גם נרשמים ביומן אחד-אחד). מעבר לזה — נספרים.
MAX_SOURCES = 32
#: שורת סיכום לכל היותר פעם בזמן הזה (בהצפה מתמשכת: ‏6 בשעה).
SUMMARY_EVERY = 600.0
#: כמה זמן תשובה שיצאה מכאן נחשבת "שלנו" כשהיא חוזרת (לולאה בסוויץ').
ECHO_SECONDS = 5.0
#: כמה תשובות יוצאות זוכרים לצורך זה — תקרה גם כאן.
ECHO_KEEP = 256
#: השורה האדומה נשארת עד שהשרת הזר לא נראה כל כך הרבה זמן. שרת זר נראה רק
#: כשמחשב מאתחל ברשת, ולכן שעה — לא דקות.
HOLD_SECONDS = 3600.0
#: אירוע ביומן: פעם אחת לכל מקור (MAC+IP) בכל שעה.
JOURNAL_EVERY = 3600.0
#: אורך פרוסת ההאזנה: קריאה חוסמת אחת, ובסופה בודקים מחדש שער וזהות.
SLICE_SECONDS = 60.0
GATE_RECHECK = 30.0
RETRY_SECONDS = 10.0
#: פרוסה שלא הסתיימה מזה כל כך הרבה — התהליכון תקוע או מת.
STALE_SECONDS = 4 * SLICE_SECONDS

KIND_HE = {"offer": "מציע כתובות (DHCPOFFER)", "proxy": "מציע אתחול רשת (proxyDHCP)"}
_COOKIE = b"\x63\x82\x53\x63"
_SOURCE = re.compile(r"^mac=(\S+) ip=(\S+) kind=(offer|proxy) nic=(\S+)( as=server)?$")
_MORE = re.compile(r"^count=(\d+) last_mac=\S+ last_ip=\S+ nic=(\S+)$")


@dataclass(frozen=True)
class Reply:
    mac: str
    ip: str
    kind: str        # "offer" | "proxy"
    #: גוף ה-BOOTP — לזיהוי עותק של תשובה שיצאה מכאן; אינו חלק מזהות התשובה.
    body: bytes = field(default=b"", compare=False, repr=False)


@dataclass
class Source:
    kind: str
    last_seen: float
    journaled: float
    as_server: bool = False


def _options(data: bytes) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    i = 0
    while i < len(data):
        code = data[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        if i + 1 >= len(data):
            break
        size = data[i + 1]
        out.setdefault(code, data[i + 2:i + 2 + size])
        i += 2 + size
    return out


def parse_reply(frame: bytes) -> Reply | None:
    """מסגרת Ethernet → ‏`Reply` אם היא DHCPOFFER (שרת 67 → לקוח 68), אחרת None.

    ‏proxyDHCP: ‏yiaddr ריק ואפשרויות PXE (60 = PXEClient או 43). כל מסגרת קצרה,
    מקוטעת או פגומה היא None."""
    if len(frame) < 14 + 20 + 8 + 240 or frame[12:14] != b"\x08\x00":
        return None
    ip = frame[14:]
    ihl = (ip[0] & 0x0F) * 4
    if ip[0] >> 4 != 4 or ihl < 20 or ip[9] != 17 or len(ip) < ihl + 8:
        return None
    if struct.unpack("!H", ip[6:8])[0] & 0x1FFF:
        return None
    sport, dport, length = struct.unpack("!HHH", ip[ihl:ihl + 6])
    if (sport, dport) != (67, 68) or length < 8 + 240:
        return None
    bootp = ip[ihl + 8:ihl + length]
    if bootp[0:1] != b"\x02" or bootp[236:240] != _COOKIE:
        return None
    options = _options(bootp[240:])
    if options.get(53) != b"\x02":
        return None
    pxe = options.get(60, b"").startswith(b"PXEClient") or 43 in options
    kind = "proxy" if pxe and bootp[16:20] == bytes(4) else "offer"
    return Reply(frame[6:12].hex(":"), socket.inet_ntoa(ip[12:16]), kind, bytes(bootp))


def _digest(reply: Reply) -> bytes:
    """גוף ה-BOOTP בלבד: סכום הבדיקה של UDP במסגרת יוצאת עוד לא חושב
    (checksum offload), ולכן מסגרת שלמה אינה משתווה לעותק שחזר."""
    return hashlib.blake2b(reply.body, digest_size=16).digest()


def own_identity(interface: str, sys_root: str | Path = "/sys/class/net"
                 ) -> tuple[frozenset[str], frozenset[str]]:
    """‏(ה-MAC, כתובות ה-IPv4) של הכרטיס. כרטיס שאינו קיים — OSError, כלומר
    "לא הצלחנו להאזין", לא "נקי"."""
    mac = Path(sys_root, interface, "address").read_text().strip().lower()
    ips = {a.split("/")[0] for a in dhcp_host._addresses(interface)}
    return frozenset({mac}), frozenset(ips)


def _ago(seconds: float) -> str:
    return "פחות מדקה" if seconds < 60 else f"{int(seconds // 60)} דק'"


class Watch:
    """מצב הזיהוי בכרטיס אחד. ‏`run_forever` בתהליכון daemon (‏main בלבד).

    ‏`gate()` — None כש-DHCP שלנו דלוק בכרטיס, אחרת הסיבה בעברית.
    ‏`own()` — ‏(MACs, IPs) של הכרטיס. ‏`opener(interface)` — הסוקט המסונן."""

    def __init__(self, conn, interface: str, *, gate: Callable[[], str | None],
                 own: Callable[[], tuple[frozenset[str], frozenset[str]]] | None = None,
                 opener=dhcp_host._open_tap, clock=time.monotonic, wall=time.time) -> None:
        self.conn, self.interface, self.gate = conn, interface, gate
        self.own = own or (lambda: own_identity(interface))
        self.opener, self.clock = opener, clock
        self.sources: dict[tuple[str, str], Source] = {}
        self.refused: str | None = None
        self.error: str | None = None
        self.slice_done: float | None = None
        self.drops: int | None = 0
        self._tap = None
        self.sent: dict[bytes, float] = {}
        #: תשובות ממקורות מעבר ל-`MAX_SOURCES`: כמה מאז שורת הסיכום האחרונה
        #: (‏unlogged), כמה בחלון ההחזקה (‏overflow), מתי האחרונה ומי.
        self.unlogged = 0
        self.overflow = 0
        self.overflow_seen: float | None = None
        self.overflow_last = ("", "")
        self.summarized: float | None = None
        self._seed(wall())

    # --- אחרי אתחול: מי שנראה לפני ההפעלה מחדש עדיין חי ----------------------

    def _seed(self, wall_now: float) -> None:
        """שורה אדומה שנעלמת באתחול השרת היא ירוק שקרי: ‏`foreign_dhcp_seen`
        שביומן, בתוך `HOLD_SECONDS`, חוזר כמי שנראה."""
        try:
            rows = self.conn.execute(
                "SELECT ts, detail FROM journal WHERE event = ? ORDER BY id DESC LIMIT 100",
                (EVENT,)).fetchall()
        except Exception as exc:  # noqa: BLE001 — בלי היסטוריה מתחילים ריקים, בגלוי
            log.warning("rogue dhcp: could not read the journal: %s", exc)
            return
        now = self.clock()
        for row in rows:
            match = _SOURCE.match(row["detail"] or "")
            try:
                age = wall_now - datetime.fromisoformat(row["ts"]).astimezone(timezone.utc).timestamp()
            except ValueError:
                continue
            if match is None or match[4] != self.interface or not 0 <= age < HOLD_SECONDS:
                continue
            if len(self.sources) < MAX_SOURCES:
                self.sources.setdefault((match[1], match[2]),
                                        Source(match[3], now - age, now - age, bool(match[5])))
        self._seed_overflow(wall_now, now)

    def _seed_overflow(self, wall_now: float, now: float) -> None:
        """הצפה שנרשמה בשורות סיכום בשעה האחרונה נשארת אדומה גם אחרי אתחול."""
        try:
            rows = self.conn.execute(
                "SELECT ts, detail FROM journal WHERE event = ? ORDER BY id DESC LIMIT 100",
                (EVENT_MORE,)).fetchall()
        except Exception as exc:  # noqa: BLE001 — בלי היסטוריה מתחילים ריקים, בגלוי
            log.warning("rogue dhcp: could not read the journal: %s", exc)
            return
        for row in rows:
            match = _MORE.match(row["detail"] or "")
            try:
                age = wall_now - datetime.fromisoformat(row["ts"]).astimezone(timezone.utc).timestamp()
            except ValueError:
                continue
            if match is None or match[2] != self.interface or not 0 <= age < HOLD_SECONDS:
                continue
            self.overflow += int(match[1])
            if self.overflow_seen is None:
                self.overflow_seen = self.summarized = now - age

    # --- מה נראה --------------------------------------------------------------

    def observe(self, frame: bytes, packet_type: int, now: float,
                macs: frozenset[str], ips: frozenset[str]) -> None:
        reply = parse_reply(frame)
        if reply is None:
            return
        if packet_type == dhcp_host.PACKET_OUTGOING:
            self._remember_sent(reply, now)
            return
        as_server = reply.mac in macs or reply.ip in ips
        if as_server and now - self.sent.get(_digest(reply), -1e18) < ECHO_SECONDS:
            return                                     # עותק של מה שיצא מכאן
        key = (reply.mac, reply.ip)
        known = self.sources.get(key)
        if known is not None and now - known.journaled < JOURNAL_EVERY:
            known.kind, known.last_seen = reply.kind, now
            return
        if known is None and len(self.sources) >= MAX_SOURCES:
            self.active(now)                           # מקומות שהתפנו בפקיעת ההחזקה
            if len(self.sources) >= MAX_SOURCES:
                self._overflowed(reply, now)
                return
        self.sources[key] = Source(reply.kind, now, now, as_server)
        tag = " as=server" if as_server else ""
        self._journal(EVENT, f"mac={reply.mac} ip={reply.ip} kind={reply.kind} "
                             f"nic={self.interface}{tag}")

    def _remember_sent(self, reply: Reply, now: float) -> None:
        self.sent[_digest(reply)] = now
        if len(self.sent) > ECHO_KEEP:
            for digest in [d for d, at in self.sent.items() if now - at >= ECHO_SECONDS]:
                del self.sent[digest]
            while len(self.sent) > ECHO_KEEP:
                del self.sent[next(iter(self.sent))]

    def _overflowed(self, reply: Reply, now: float) -> None:
        if self.overflow_seen is None or now - self.overflow_seen >= HOLD_SECONDS:
            self.overflow = 0
        self.overflow += 1
        self.unlogged += 1
        self.overflow_seen, self.overflow_last = now, (reply.mac, reply.ip)
        self.summarize(now)

    def summarize(self, now: float) -> None:
        """שורת סיכום על מה שלא נרשם אחד-אחד — לכל היותר פעם ב-`SUMMARY_EVERY`."""
        if not self.unlogged or (self.summarized is not None
                                 and now - self.summarized < SUMMARY_EVERY):
            return
        mac, ip = self.overflow_last
        self._journal(EVENT_MORE, f"count={self.unlogged} last_mac={mac} last_ip={ip} "
                                  f"nic={self.interface}")
        self.unlogged, self.summarized = 0, now

    def _journal(self, event: str, detail: str) -> None:
        try:
            journal(self.conn, event, detail)
        except Exception as exc:  # noqa: BLE001 — היומן אינו מפיל את הזיהוי
            log.warning("rogue dhcp: journal failed: %s", exc)

    def active(self, now: float) -> list[tuple[tuple[str, str], Source]]:
        for key in [k for k, s in self.sources.items() if now - s.last_seen >= HOLD_SECONDS]:
            del self.sources[key]
        return sorted(self.sources.items(), key=lambda item: -item[1].last_seen)

    # --- הלולאה ---------------------------------------------------------------

    def step(self, stop: threading.Event) -> None:
        """פרוסה אחת: שער, זהות, והאזנה חוסמת אחת. הסוקט נשאר פתוח בין פרוסות."""
        self.refused = self.gate()
        if self.refused is not None:
            self.close()
            self.error = None
            stop.wait(GATE_RECHECK)
            return
        macs, ips = self.own()
        if self._tap is None:
            self._tap = self.opener(self.interface)
        self.error = None
        end = self.clock() + SLICE_SECONDS
        while not stop.is_set() and self.clock() < end:
            self._tap.settimeout(max(end - self.clock(), 0.001))
            try:
                frame, addr = self._tap.recvfrom(65535)
            except (socket.timeout, TimeoutError):
                break
            self.observe(frame, addr[2], self.clock(), macs, ips)
        self.drops = self._read_drops()
        self.slice_done = self.clock()
        self.summarize(self.slice_done)

    def _read_drops(self) -> int | None:
        try:
            return struct.unpack("II", self._tap.getsockopt(
                dhcp_host.SOL_PACKET, dhcp_host.PACKET_STATISTICS, 8))[1]
        except (OSError, struct.error):
            return None

    def close(self) -> None:
        tap, self._tap = self._tap, None
        if tap is not None:
            tap.close()

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.step(stop)
            except Exception as exc:  # noqa: BLE001 — תהליכון רקע: נאמר, לא נבלע
                self.close()
                if str(exc) != self.error:
                    log.warning("rogue dhcp watch on %s: %s", self.interface, exc)
                self.error = str(exc) or type(exc).__name__
                stop.wait(RETRY_SECONDS)
        self.close()

    # --- מסך הבריאות ------------------------------------------------------------

    def health(self) -> tuple[str, str]:
        now = self.clock()
        if self.refused is not None:
            return "off", f"כבוי: {self.refused}"
        found = self.active(now)
        flood = self.overflow if (self.overflow_seen is not None
                                  and now - self.overflow_seen < HOLD_SECONDS) else 0
        failed = (f" · ההאזנה כרגע נכשלה: {self.error}" if self.error is not None else "")
        if found or flood:
            parts = [f"{mac} ({ip}) — {KIND_HE[s.kind]}"
                     f"{', מתחזה לשרת (הכתובת שלו במסגרת)' if s.as_server else ''}, "
                     f"נראה לפני {_ago(now - s.last_seen)}"
                     for (mac, ip), s in found[:3]]
            if len(found) > 3:
                parts.append(f"ועוד {len(found) - 3}")
            if flood:
                parts.append(f"ועוד {flood} תשובות ממקורות נוספים שלא נשמרו "
                             f"(יותר מ-{MAX_SOURCES} מקורות — אולי הצפה)")
            return "bad", (f"שרת DHCP זר ברשת ההפצה {self.interface}: {' · '.join(parts)}{failed} — "
                           "לאתר ולנתק; ההגנה הקבועה היא DHCP snooping בסוויץ'")
        if self.error is not None:
            return "warn", (f"לא הצלחנו להאזין ל-{self.interface}: {self.error} — "
                            "לא נבדק אם יש שרת DHCP זר")
        if self.slice_done is None:
            return "unknown", f"טרם התחילה ההאזנה על {self.interface}"
        if now - self.slice_done > STALE_SECONDS:
            return "warn", (f"ההאזנה על {self.interface} לא דיווחה מזה "
                            f"{_ago(now - self.slice_done)} — לא נבדק אם יש שרת DHCP זר")
        if self.drops is None or self.drops:
            why = ("לא ניתן לקרוא את מוני ההאזנה" if self.drops is None
                   else f"הקרנל השליך {self.drops} מסגרות DHCP")
            return "warn", f"מאזין על {self.interface}, אבל {why} — שרת זר יכול היה להיות ביניהן"
        return "ok", (f"מאזין על {self.interface} — לא נראה שרת DHCP זר "
                      "(נראות רק תשובות משודרות; ההגנה היא DHCP snooping בסוויץ')")
