"""‏DHCP — כל מה שנוגע במכונה עצמה (אפיון סעיף 24).

‏dhcp.py הוא הלוגיקה הטהורה: מקבל הגדרות, מחזיר טקסט. כאן יושב הצד
המלוכלך — לקרוא אילו כרטיסים קיימים, לשאול אם מישהו כבר מחלק כתובות,
לכתוב את קבצי ה-conf ולהפעיל שירותים. הפרדה זו גם מה שמאפשר לבדיקות
להזריק `dhcp_hooks` ולעולם לא לגעת במכונה.

שני יעדים, בכוונה (‏#36):

- `DEFAULT_CONF` — בתוך `/etc/dnsmasq.d`, נטען לאינסטנס הראשי של
  dnsmasq (ה-DHCP של וילן ההפצה).
- `PROXY_CONF` — **מחוץ** ל-`/etc/dnsmasq.d`, כדי שהאינסטנס הראשי לא
  יטען אותו. הוא נטען ביחידה `imagesrv-proxy` בלבד. ‏dnsmasq 2.91 קופא
  על בקשת PXE לפורט 4011 במצב proxy, וכל עוד זה כך — הקפיאה חייבת
  להישאר בתהליך שאפשר לאבד.
"""

from __future__ import annotations

import ctypes
import errno
import logging
import re
import secrets
import socket
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from server.core.atomicfile import write_atomic

log = logging.getLogger(__name__)

DEFAULT_CONF = "/etc/dnsmasq.d/imagesrv-dhcp.conf"
#: קובץ המתקין (‏#1013) — נקרא כאן כדי לדעת אם `enable-tftp` עדיין יושב בו.
INSTALLER_CONF = "/etc/dnsmasq.d/imagesrv.conf"
PROXY_CONF = "/etc/imagesrv/dnsmasq-proxy.conf"
PROXY_UNIT = "imagesrv-proxy"

#: ‏#141: רשימת המכונות הרשומות, שממנה dhcp-boot (render() הראשי) מדליק
#: tag:known. **מחוץ** ל-/etc/dnsmasq.d בכוונה — הוא נטען כ-dhcp-hostsfile
#: מפורש מתוך imagesrv.conf (setup-boot-server.sh), לא דרך הסריקה
#: האוטומטית של dnsmasq לתיקייה, כדי ששינוי בו לא ידרוש לגעת בקובץ
#: הממשקים שהקונסולה כותבת. כבר ב-ReadWritePaths של imagesrv-server
#: (‏-/etc/imagesrv, אופציונלי) — אין שינוי ביחידה.
KNOWN_MACS_CONF = "/etc/imagesrv/known-macs"


# --- מה יש במכונה -----------------------------------------------------------


def list_interfaces(sys_root: str | Path = "/sys/class/net") -> list[dict]:
    """כרטיסי הרשת הפיזיים והווירטואליים, בלי loopback. נקרא מ-/sys כדי
    שלא יהיה צורך ב-`ip` בזמן הבדיקות; הכתובות נמשכות בנפרד."""
    root = Path(sys_root)
    found = []
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        if entry.name == "lo":
            continue
        try:
            state = (entry / "operstate").read_text().strip()
        except OSError:
            state = "unknown"
        try:
            mac = (entry / "address").read_text().strip()
        except OSError:
            mac = ""
        try:
            flags = int((entry / "flags").read_text().strip(), 16)
            admin_up = bool(flags & 1)  # IFF_UP
        except (OSError, ValueError):
            admin_up = None
        try:
            link_up = (entry / "carrier").read_text().strip() == "1"
        except OSError:
            link_up = None
        found.append({"name": entry.name, "state": state, "mac": mac,
                      "addresses": _addresses(entry.name),
                      "admin_up": admin_up, "link_up": link_up})
    return found


def _addresses(name: str) -> list[str]:
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", name],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[3] for line in out.splitlines() if len(line.split()) > 3]


def set_link(name: str, up: bool) -> str | None:
    """‏`ip link set dev <name> up|down`, בלי כתובת (‏#2805). מחזיר הודעה או None.

    ‏None פירושו "הפקודה יצאה 0", לא "יש קישור" — את זה קובע `link_up`
    שנקרא בחזרה מ-`list_interfaces`."""
    try:
        done = subprocess.run(
            ["ip", "link", "set", "dev", name, "up" if up else "down"],
            capture_output=True, text=True, timeout=5, check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"ip link: {exc}"
    if done.returncode != 0:
        return f"ip link rc={done.returncode}: {(done.stderr or done.stdout).strip()[:200]}"
    return None


# --- מי כבר עונה ל-DHCP כאן? ------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """תוצאת ה-DHCPDISCOVER על ממשק — **שלושה** מצבים, לא שניים.

    - ‏`checked=False` — הבדיקה לא רצה בכלל. זה *לא* "נקי": שום דבר לא
      נשלל, ואסור לתת לזה לפתוח את ההדלקה. ‏`reason` אומר למה.
    - ‏`checked=True` עם `servers` ריק — שאלנו, ואיש לא ענה.
    - ‏`servers` — מי ענה.

    האובייקט אמיתי (truthy) תמיד, בכוונה: קודם הוחזרו `None`/`[]`/רשימה,
    ו-`if found:` קיפל את שני הראשונים לאחד — ככה אפשר היה להדליק DHCP
    על ה-trunk של המכללה כשהבדיקה רק *נכשלה* ‏(#53). עכשיו `if` מקרי
    נופל לצד החוסם, לא לצד המדליק.
    """

    checked: bool
    servers: tuple[str, ...] = ()
    # Socket failures use link_down / port_busy / permission / os_error.
    # Other incomplete probes retain their explanatory diagnostics.
    reason: str = ""


def _probe_os_error(exc: OSError) -> ProbeResult:
    if exc.errno in (errno.ENETUNREACH, errno.ENETDOWN):
        reason = "link_down"
    elif exc.errno == errno.EADDRINUSE:
        reason = "port_busy"
    elif exc.errno in (errno.EACCES, errno.EPERM):
        reason = "permission"
    else:
        reason = "os_error"
    return ProbeResult(False, reason=reason)


# ‏#2417: ההאזנה היא ברמת הקישור (AF_PACKET), לא על סוקט UDP. סוקט UDP
# מקבל את ה-OFFER רק אחרי ה-hook של nft input ואחרי rp_filter — ו-OFFER
# משודר אינו משויך ב-conntrack לבקשה שיצאה, ולכן חומת האש של השרת עצמו
# (policy drop, בלי כלל ל-dport 68) זרקה אותו, והבדיקה דיווחה "נבדק, נקי".
# אומת על Debian 13 במעבדה, 04/11. ‏AF_PACKET רואה את המסגרת לפני שניהם.
AF_PACKET = getattr(socket, "AF_PACKET", 17)
ETH_P_ALL = getattr(socket, "ETH_P_ALL", 3)
PACKET_OUTGOING = getattr(socket, "PACKET_OUTGOING", 4)
SOL_PACKET = getattr(socket, "SOL_PACKET", 263)
PACKET_STATISTICS = 6
SO_ATTACH_FILTER = getattr(socket, "SO_ATTACH_FILTER", 26)

#: מסנן BPF בקרנל: רק UDP/IPv4 לא-מקוטע מפורט 67 או אליו (ה-OFFER, וה-DISCOVER
#: שלנו כשהוא יוצא). בלעדיו כל תעבורת הכרטיס — גם מולטיקאסט של סבב שהשרת
#: עצמו משדר — הייתה ממלאת את החוצץ, ו-OFFER שנזרק שם היה נראה כמו שקט.
#: ‏(code, jt, jf, k); הקפיצות יחסיות לפקודה הבאה. ‏drop=11, ‏accept=12.
_BPF_DHCP = (
    (0x28, 0, 0, 12),          # 0  ldh [12]            ethertype
    (0x15, 0, 9, 0x0800),      # 1  jeq IPv4            : drop
    (0x30, 0, 0, 23),          # 2  ldb [23]            IP protocol
    (0x15, 0, 7, 17),          # 3  jeq UDP             : drop
    (0x28, 0, 0, 20),          # 4  ldh [20]            flags + fragment offset
    (0x45, 5, 0, 0x1FFF),      # 5  jset fragment       ? drop
    (0xB1, 0, 0, 14),          # 6  ldxb 4*([14]&0xf)   IP header length
    (0x48, 0, 0, 14),          # 7  ldh [x+14]          UDP source port
    (0x15, 3, 0, 67),          # 8  jeq 67              ? accept
    (0x48, 0, 0, 16),          # 9  ldh [x+16]          UDP destination port
    (0x15, 1, 0, 67),          # 10 jeq 67              ? accept : drop
    (0x06, 0, 0, 0),           # 11 drop
    (0x06, 0, 0, 0x40000),     # 12 accept
)


def _open_tap(iface: str) -> socket.socket:
    """סוקט AF_PACKET על `iface` בלבד, עם המסנן. ‏protocol=0 ביצירה = לא מקבל
    כלום עד ה-bind, כך שמסגרות מכרטיסים אחרים לא נכנסות לפני שהמסנן והכרטיס
    נקבעו. כשל = OSError, והקורא מחזיר "לא נבדק"."""
    tap = socket.socket(AF_PACKET, socket.SOCK_RAW, 0)
    try:
        program = ctypes.create_string_buffer(
            b"".join(struct.pack("HBBI", *op) for op in _BPF_DHCP))
        tap.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER,
                       struct.pack("HP", len(_BPF_DHCP), ctypes.addressof(program)))
        tap.bind((iface, ETH_P_ALL))
    except BaseException:
        tap.close()
        raise
    return tap


def _dhcp_frame(frame: bytes, xid: bytes) -> tuple[str, str] | None:
    """מסגרת Ethernet → ‏("request", כתובת המקור) ל-BOOTREQUEST עם ה-xid שלנו,
    ‏("reply", כתובת המקור) לתשובה אליו, או None לכל דבר אחר (כולל מסגרת
    קצרה/פגומה)."""
    if len(frame) < 14 + 20 or frame[12:14] != b"\x08\x00":
        return None
    ip = frame[14:]
    ihl = (ip[0] & 0x0F) * 4
    if ip[0] >> 4 != 4 or ihl < 20 or ip[9] != 17 or len(ip) < ihl + 8 + 8:
        return None
    sport, dport = struct.unpack("!HH", ip[ihl:ihl + 4])
    bootp = ip[ihl + 8:]
    if bootp[4:8] != xid:
        return None
    if bootp[0] == 1 and dport == 67:
        return ("request", socket.inet_ntoa(ip[12:16]))
    if bootp[0] == 2 and sport == 67:
        return ("reply", socket.inet_ntoa(ip[12:16]))
    return None


def _ip_checksum(header: bytes) -> int:
    total = sum(struct.unpack(f"!{len(header) // 2}H", header))
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def _discover_frame(mac: bytes, xid: bytes) -> bytes:
    """ה-DISCOVER כמסגרת שלמה, כמו שלקוח DHCP שולח (RFC 2131 4.1): מ-0.0.0.0:68
    אל 255.255.255.255:67, ל-ff:ff:ff:ff:ff:ff, ממקור ה-MAC של הכרטיס.

    ‏#2417, במעבדה 04/11: סוקט UDP על כרטיס בלי כתובת שלח עם כתובת **של כרטיס
    אחר** (192.168.68.61), ושרת עם rp_filter=2 — ברירת המחדל בדביאן — לא ענה.
    "נבדק, נקי" על שאלה שאיש לא יכול היה לענות עליה."""
    bootp = (
        b"\x01\x01\x06\x00"           # op=BOOTREQUEST, htype=ethernet, hlen=6, hops
        + xid
        + b"\x00\x00"                   # secs
        + b"\x80\x00"                   # flags: broadcast -- answer where we can hear it
        + b"\x00" * 16                   # ciaddr, yiaddr, siaddr, giaddr
        + mac.ljust(16, b"\x00")         # chaddr
        + b"\x00" * 64 + b"\x00" * 128  # sname, file
        + b"\x63\x82\x53\x63"           # magic cookie
        + b"\x35\x01\x01"               # option 53: DHCPDISCOVER
        + b"\xff"
    )
    udp = struct.pack("!HHHH", 68, 67, 8 + len(bootp), 0) + bootp   # checksum 0: מותר ב-IPv4
    header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
                         b"\x00" * 4, b"\xff" * 4)
    header = header[:10] + struct.pack("!H", _ip_checksum(header)) + header[12:]
    return b"\xff" * 6 + mac + b"\x08\x00" + header + udp


def probe_existing_dhcp(iface: str, timeout: float = 2.0) -> ProbeResult:
    """שולח DHCPDISCOVER על הממשק ומחזיר מי ענה — ואם בכלל הצלחנו לשאול.

    השליחה וההאזנה שתיהן ב-AF_PACKET על הכרטיס עצמו (‏#2417): ה-DISCOVER יוצא
    כמסגרת שנבנתה כאן, ממקור 0.0.0.0, מהכרטיס הזה — עם כתובת או בלעדיה; וההאזנה
    רואה את ה-OFFER גם כשחומת האש של השרת או rp_filter זורקים אותו לפני כל
    סוקט UDP. אין bind לפורט 68, ולכן לקוח DHCP על הכרטיס אינו חוסם את הבדיקה.

    ‏`checked=True` דורש **ראיה חיובית** שהשאלה נשאלה כמו שצריך ושההאזנה שמעה
    את הכרטיס: ה-DISCOVER שלנו (ה-xid שלנו, ממקור 0.0.0.0) נראה יוצא בה, והקרנל
    לא השליך מסגרות שלה (PACKET_STATISTICS). בלי אלה — "לא הצלחנו לבדוק", לא
    "נקי" (עיקרון 5). כך גם בלי root ובלי קישור. זה מה שמונע את התקלה הגרועה
    ביותר: להדליק DHCP שני על רשת שכבר יש בה.
    """
    xid = secrets.token_bytes(4)
    tap = sender = None
    try:
        try:
            tap = _open_tap(iface)
        except OSError as exc:
            return _probe_os_error(exc)
        try:
            # סוקט נפרד: ההאזנה אינה רואה מה שהיא עצמה שלחה. ‏protocol=0 — אינו מקבל.
            sender = socket.socket(AF_PACKET, socket.SOCK_RAW, 0)
            sender.bind((iface, 0))
            mac = sender.getsockname()[4]
            if len(mac) != 6:
                return ProbeResult(False, reason=(
                    f"ל-{iface} אין כתובת Ethernet (קיבלנו {mac.hex() or 'כלום'}) — "
                    "אי אפשר לשלוח עליו DHCPDISCOVER"))
            frame = _discover_frame(mac, xid)
            written = sender.sendto(frame, (iface, 0x0800))
        except OSError as exc:
            return _probe_os_error(exc)
        if written != len(frame):
            return ProbeResult(False, reason=(
                f"הקרנל קיבל {written} מתוך {len(frame)} בייט של ה-DHCPDISCOVER על {iface}"))
        return _listen(tap, iface, xid, timeout)
    finally:
        for s in (sender, tap):
            if s is not None:
                s.close()


def _listen(tap: socket.socket, iface: str, xid: bytes, timeout: float) -> ProbeResult:
    """עד `timeout` מרגע השליחה (מועד אחרון, לא לכל קריאה — DISCOVER של מכונות
    אחרות ברשת עמוסה לא מאריך את ההמתנה). ואז הראיות, לפי הסדר."""
    asked = False
    wrong_source = ""
    seen: list[str] = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            tap.settimeout(left)
            frame, addr = tap.recvfrom(65535)
            found = _dhcp_frame(frame, xid)
            if found is None:
                continue
            if found[0] == "request":
                if addr[2] != PACKET_OUTGOING:
                    continue
                if found[1] == "0.0.0.0":
                    asked = True
                else:
                    wrong_source = found[1]
            elif found[1] not in seen:
                seen.append(found[1])
    except socket.timeout:
        pass  # חלון ההאזנה נגמר — זה סיום הרגיל של הסריקה, לא כשל
    except OSError as exc:
        # שגיאה באמצע ההאזנה: מי שכבר ענה נספר, אבל שקט כאן אינו ראיה.
        return ProbeResult(bool(seen), tuple(seen), "" if seen else
                           f"ההאזנה על {iface} נקטעה: {exc}")
    if seen:
        return ProbeResult(True, tuple(seen))
    if not asked and wrong_source:
        return ProbeResult(False, reason=(
            f"ה-DHCPDISCOVER יצא מ-{iface} ממקור {wrong_source} ולא 0.0.0.0 — שרת "
            "שמסנן מקור (rp_filter, DHCP snooping) לא היה עונה לו, ולכן שקט אינו ראיה"))
    if not asked:
        return ProbeResult(False, reason=(
            f"ה-DHCPDISCOVER לא נראה יוצא מ-{iface} — אין ראיה שהבקשה יצאה ושההאזנה "
            "שומעת את הכרטיס (אין קישור? הכרטיס מושבת?)"))
    try:
        _packets, drops = struct.unpack(
            "II", tap.getsockopt(SOL_PACKET, PACKET_STATISTICS, 8))
    except (OSError, struct.error) as exc:
        return ProbeResult(False, reason=f"לא ניתן לקרוא את מוני ההאזנה על {iface}: {exc}")
    if drops:
        return ProbeResult(False, reason=(
            f"הקרנל השליך {drops} מסגרות DHCP בזמן ההאזנה על {iface} — תשובה יכלה "
            "להיות ביניהן"))
    return ProbeResult(True, ())


# --- האם מצב proxy בטוח בגרסת dnsmasq שמותקנת כאן? (#36) --------------------

#: הגרסה שבה הקפיאה שוחזרה במעבדה — ארבע פעמים, בארבעה וריאנטים של
#: התצורה, ולכן זו לא שגיאה בתחביר שאנחנו מייצרים.
PROXY_BROKEN = ("2.91",)

#: הגרסאות שבהן מצב proxy נבדק **בפועל** מול תחנת UEFI ועבד. ריקה
#: בכוונה: אף גרסה עוד לא עברה את הבדיקה הזו. כשאחת תעבור — מוסיפים
#: אותה כאן, וההגנה נפתחת מעצמה בלי לגעת בשום מקום אחר. אסור למלא
#: את הרשימה לפי CHANGELOG: היעדר אזכור של באג אינו ראיה לתיקון,
#: והרשימה הזו היא רשימת *ראיות חיוביות* (עיקרון 5).
PROXY_VERIFIED: tuple[str, ...] = ()

_VERSION_LINE = re.compile(r"version\s+(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)


@dataclass(frozen=True)
class ProxySupport:
    """האם מותר להדליק proxy בלי אישור מפורש — **שלושה** מצבים, לא שניים.

    בדיוק כמו `ProbeResult` (‏#53): ‏`read=False` הוא "לא הצלחנו לקרוא
    את הגרסה", ולא "הגרסה תקינה". רק `verified` פותח, והוא דורש ראיה
    חיובית — גרסה שנקראה ונמצאת ב-`PROXY_VERIFIED`. האובייקט truthy
    תמיד, בכוונה, כדי ש-`if support:` מקרי לא ייקרא כאישור.
    """

    read: bool
    version: str = ""

    @property
    def verified(self) -> bool:
        return self.read and self.version in PROXY_VERIFIED

    @property
    def broken(self) -> bool:
        return self.read and self.version in PROXY_BROKEN

    def reason(self) -> str:
        """למה חוסמים — האמת, לא "לא זמין". מפעיל שלא יודע *למה* יעקוף."""
        if self.broken:
            head = (f"‏dnsmasq {self.version} המותקן כאן נתקע על בקשת PXE "
                    "לפורט 4011 במצב proxy: ‏100% מעבד, מפסיק לענות לכל "
                    "הסוקטים, ולא מגיב ל-SIGTERM. באג upstream, שוחזר "
                    "במעבדה (‏#36).")
        elif self.read:
            head = (f"‏dnsmasq {self.version} המותקן כאן לא נבדק במצב proxy. "
                    "הקפיאה של #36 שוחזרה ב-2.91, ואין ראיה חיובית שהיא "
                    "תוקנה בגרסה הזו — \"לא נבדק\" אינו \"תקין\".")
        else:
            head = ("לא ניתן לקרוא את גרסת dnsmasq המותקנת — הבדיקה עצמה "
                    "לא רצה. זה \"לא יודעים\", ולא \"הגרסה תקינה\".")
        return (head + " ה-proxy רץ באינסטנס נפרד (imagesrv-proxy), ולכן "
                "קפיאה שלו לא מורידה את ה-DHCP של וילן ההפצה — אבל אתחול "
                "רשת דרך המצב הזה לא יעבוד. להדלקה בכל זאת, כבדיקה "
                "מכוונת: confirm_proxy_broken.")


def proxy_support(raw: str | None) -> ProxySupport:
    """מתרגם את פלט `dnsmasq --version` להחלטה. ‏None/זבל = לא נקרא."""
    match = _VERSION_LINE.search(raw or "")
    return ProxySupport(read=bool(match), version=match.group(1) if match else "")


def dnsmasq_version() -> str | None:
    """פלט `dnsmasq --version`, או None אם לא הצלחנו להריץ אותו.

    קריאה בלבד — לא נוגעת בשירות. ‏None אינו "אין בעיה": הקורא חייב
    להתייחס אליו כמו לגרסה שלא נבדקה.
    """
    try:
        result = subprocess.run(
            ["dnsmasq", "--version"], capture_output=True, text=True,
            timeout=5, check=False, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


# --- החלה -------------------------------------------------------------------


def apply(text: str, conf_path: str | Path = DEFAULT_CONF) -> str | None:
    """האינסטנס הראשי: כותב את הקובץ ומפעיל מחדש את dnsmasq.

    מחזיר הודעת שגיאה או None. כתיבה שנכשלת (הרשאות) לא מפילה את השרת:
    ההגדרה כבר נשמרה ב-DB, והודעה ברורה בקונסולה עדיפה על 500.

    ‏#1828: ‏`dnsmasq --test` על ההגדרה כולה **לפני** ה-restart, וכשל בבדיקה
    או ב-restart מחזיר את הקובץ הקודם — קובץ פגום אינו משאיר את dnsmasq
    למטה ואת הכיתה בלי DHCP/PXE.
    """
    path = Path(conf_path)
    return _apply_checked(text, path, _main_test_cmd(path), "restart", "dnsmasq")


def apply_proxy(text: str, active: bool,
                conf_path: str | Path = PROXY_CONF) -> str | None:
    """אינסטנס ה-proxy: כותב את הקובץ הנפרד ומפעיל/עוצר את היחידה שלו.

    כשאף ממשק אינו במצב proxy היחידה נעצרת — תהליך dnsmasq שני שרץ
    על קובץ ריק הוא רק עוד משהו שיכול להיתקע. הכתיבה קודמת ל-systemctl,
    כך שכשל בכתיבה חוזר בלי לגעת בשירותים.

    ‏#1828: כשהיחידה עולה — ‏`--test` על הקובץ (אותה שורה כמו ExecStartPre
    שלה) לפני ה-restart, והחזרת הקודם בכשל. עצירה אינה דורשת בדיקה.
    """
    path = Path(conf_path)
    if not active:
        return _write(text, path) or _systemctl("stop", PROXY_UNIT)
    return _apply_checked(text, path, ["dnsmasq", "--test", f"--conf-file={path}"],
                          "restart", PROXY_UNIT)


def apply_known_macs(text: str, conf_path: str | Path = KNOWN_MACS_CONF) -> str | None:
    """‏#141: כותב את קובץ ה-known-macs ומבקש מ-dnsmasq לקרוא אותו מחדש.

    ‏reload ולא restart, בכוונה: `--dhcp-hostsfile` נטען מחדש ב-SIGHUP
    בלי לאבד חכירות ובלי להפיל את הסוקטים (נבדק במדריך dnsmasq ובמעבדה,
    ‏#36 הוא התקדים למה restart על ההגדרה הזו אסור). מחזיר הודעת שגיאה
    או None, באותו דפוס כמו apply/apply_proxy.

    ‏#1828: בלי `--test` (קובץ hostsfile אינו קובץ הגדרה), אבל reload שנכשל
    מחזיר את הקובץ הקודם, כמו השניים שלמעלה.
    """
    return _apply_checked(text, Path(conf_path), None, "reload", "dnsmasq")


def _main_test_cmd(path: Path) -> list[str]:
    """‏`dnsmasq --test` כמו שהיחידה של דביאן בודקת לפני עלייה: ‏/etc/dnsmasq.conf
    ועוד התיקייה של הקובץ, בלי הסיומות שהחבילה מדלגת עליהן."""
    return ["dnsmasq", "--test", f"--conf-dir={path.parent},.dpkg-dist,.dpkg-old,.dpkg-new"]


def _dnsmasq_test(cmd: list[str]) -> str | None:
    """‏None = dnsmasq קיבל את ההגדרה. בדיקה שלא רצה היא כישלון (עיקרון 5)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("dhcp_host: %s could not run: %s", " ".join(cmd), exc)
        return "לא ניתן היה לבדוק את ההגדרה (dnsmasq --test) — הפרטים ביומן השרת"
    if result.returncode != 0:
        log.warning("dhcp_host: %s rc=%s: %s", " ".join(cmd), result.returncode,
                    (result.stderr or result.stdout).strip()[:300])
        return "dnsmasq דחה את ההגדרה החדשה (dnsmasq --test) — הפרטים ביומן השרת"
    return None


def _restore(path: Path, previous: str | None) -> str | None:
    """הקובץ הקודם בחזרה (או מחיקה, כשלא היה) — None או הודעה."""
    try:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            write_atomic(path, previous)
    except OSError as exc:
        log.warning("dhcp_host: restoring %s failed: %s", path, exc)
        return f"והחזרת {path} הקודם נכשלה גם היא — הפרטים ביומן השרת"
    return None


def _apply_checked(text: str, path: Path, test_cmd: list[str] | None,
                   action: str, unit: str) -> str | None:
    try:
        previous = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        previous = None
    except OSError as exc:
        # בלי הקודם אין לאן לחזור — לא כותבים (עיקרון 5).
        log.warning("dhcp_host: read %s failed: %s", path, exc)
        return f"לא ניתן לקרוא את {path} הקיים — לא נכתב דבר; הפרטים ביומן השרת"
    error = _write(text, path)
    if error:
        return error
    error = _dnsmasq_test(test_cmd) if test_cmd is not None else None
    if error:
        failed = _restore(path, previous)
        return f"{error}. {path} הקודם הוחזר" if failed is None else f"{error}, {failed}"
    error = _systemctl(action, unit)
    if error:
        failed = _restore(path, previous)
        if failed is not None:
            return f"{error}, {failed}"
        again = _systemctl(action, unit)
        return (f"{error}. {path} הקודם הוחזר ו-{unit} הופעל איתו" if again is None
                else f"{error}. {path} הקודם הוחזר, אך גם {action} שלו נכשל")
    return None


def _write(text: str, conf_path: str | Path) -> str | None:
    path = Path(conf_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, text)   # ‏#1908: לא קובץ חתוך שמפיל את dnsmasq
    except OSError as exc:
        # הטקסט הגולמי של מערכת ההפעלה (אנגלית, תלוי-שפה) הולך ללוג בלבד;
        # המשתמש מקבל משפט בעברית עם הנתיב (#1016).
        log.warning("dhcp_host: write %s failed: %s", path, exc)
        return f"לא ניתן לכתוב את {path} — בדקו הרשאות; הפרטים ביומן השרת"
    return None


def _systemctl(action: str, unit: str) -> str | None:
    try:
        result = subprocess.run(
            ["systemctl", action, unit],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("dhcp_host: systemctl %s %s failed: %s", action, unit, exc)
        return f"שירות הרשת {unit} לא עודכן ({action}) — הפרטים ביומן השרת"
    if result.returncode != 0:
        log.warning("dhcp_host: systemctl %s %s rc=%s: %s", action, unit,
                    result.returncode, (result.stderr or result.stdout).strip()[:300])
        return f"שירות הרשת {unit} לא הגיב ל-{action} — הפרטים ביומן השרת"
    return None


# --- אמת חיה ל-DHCP (‏#762) --------------------------------------------------
#
# מה שמוצג בקונסולה כ-"DHCP בפועל" חייב לבוא מכאן, לא מ-InterfaceConfig.enabled
# (שהוא כוונה שנשמרה, לא מציאות). שתי הפונקציות למטה הן הצד המלוכלך היחיד:
# קריאת קובץ וקריאת מצב שירות. כשל בכל אחת מהן = None, ולעולם לא "כבוי" —
# זו בדיוק ההרחבה 5א בעיקרון 5: "לא הצלחנו לבדוק" ו"בדקנו, זה כבוי" הם שני
# מצבים שונים.


def read_active_conf(conf_path: str | Path = DEFAULT_CONF) -> str | None:
    """תוכן קובץ ה-dnsmasq הפעיל, או None אם לא ניתן לקרוא אותו."""
    try:
        return Path(conf_path).read_text(encoding="utf-8")
    except OSError:
        return None


def read_installer_conf(conf_path: str | Path = INSTALLER_CONF) -> str | None:
    """קובץ ה-dnsmasq **של המתקין** (‏#1013), בשלושה מצבים — עיקרון 5:
    הטקסט כשנקרא; ‏`""` כשהקובץ **אינו קיים** (ראיה חיובית: אין קובץ
    שיכול לשאת `enable-tftp`); ‏`None` כשהוא קיים ולא ניתן לקרוא אותו —
    "לא הצלחנו לבדוק", שאסור לקפל ל"אין"."""
    try:
        return Path(conf_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError:
        return None


def service_active(unit: str = "dnsmasq") -> bool | None:
    """‏tri-state: `True`/`False` פעיל/לא, או `None` כשהבדיקה עצמה נכשלה.

    ‏`systemctl is-active` מחזיר קוד יציאה שאינו 0 גם על "inactive" —
    ‏`is-active` הוא בדיוק המשפחה שבה `1` הוא תשובה תקנית ולא כישלון של
    הפקודה, ולכן קוד היציאה לבדו אינו מספיק: קוראים את stdout בשם.
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (result.stdout or "").strip()
    if out == "active":
        return True
    if out in ("inactive", "failed", "unknown", "activating", "deactivating"):
        return False
    return None
