"""#3064 (N6, N8): סיווג מסגרת Ethernet שהגיעה לפורט ההפצה -- **אותו סיווג כמו `agent/netwatch.c`** (`classify`), כדי
שהמספרים של השרת והמספרים של המשכפל ייקראו באותה שפה. `tests/test_netwatch_c_3064.py` מריץ את שניהם על אותן מסגרות
ומשווה.

המסגרות שמגיעות לכאן כבר **אינן אלינו ואינן מאיתנו** (מסנן BPF בקרנל, `server/net/netwatch.py`), כך שהשידור שלנו
עצמו לעולם אינו נספר כ"תעבורה אחרת".

הקבוצות:

- **צפוי ברשת ההפצה:** ``arp``, ``dhcp``, ``wol`` (ה-WoL של השרת), ``igmp``, ``lldp``, ``l2ctl`` (STP/CDP, WoL
  ב-ethertype משלו, pause).
- **כל השאר:** ``bcast_ip`` (שידור IPv4 שאינו DHCP או WoL: NetBIOS, SSDP...), ``mcast_ip`` (מולטיקאסט IPv4: גם הזרם
  של המנוע וגם mDNS), ``unicast_other`` (יוניקאסט למי שאינו אנחנו: הצפה של הסוויץ'), ``other`` (כל ethertype אחר).
- ``ipv6`` נספר בנפרד: ND ו-MLD של לינוקס עצמו (גם שלנו) הם רעש רקע של הפרוטוקול, לא "מי שאינו שלנו".
"""

from __future__ import annotations

import socket
import struct

CLASSES = ("arp", "dhcp", "wol", "igmp", "lldp", "l2ctl", "bcast_ip", "mcast_ip", "ipv6", "unicast_other", "other")
#: מה שנחשב "תעבורה אחרת" (N8). ``mcast_ip`` נכנס אחרי הפחתת הזרם שלנו, ו-``ipv6`` אינו נכנס.
FOREIGN = ("bcast_ip", "unicast_other", "other")

_BROADCAST = b"\xff" * 6


def classify(frame: bytes) -> tuple[str, str | None]:
    """מסגרת Ethernet → ``(קבוצה, כתובת קבוצת המולטיקאסט)``. הכתובת רק ב-``mcast_ip``, אחרת ``None``."""
    if len(frame) < 14:
        return "other", None
    ethertype = struct.unpack("!H", frame[12:14])[0]
    payload = frame[14:]
    if ethertype == 0x8100 and len(frame) >= 18:
        ethertype = struct.unpack("!H", frame[16:18])[0]
        payload = frame[18:]
    broadcast = frame[:6] == _BROADCAST
    if ethertype < 0x600 or ethertype in (0x0842, 0x8808):
        return "l2ctl", None
    if ethertype == 0x0806:
        return "arp", None
    if ethertype == 0x88CC:
        return "lldp", None
    if ethertype == 0x86DD:
        return "ipv6", None
    if ethertype != 0x0800 or len(payload) < 20 or payload[0] >> 4 != 4:
        return "other", None
    header = (payload[0] & 0x0F) * 4
    if header < 20:
        return "other", None
    protocol = payload[9]
    if protocol == 2:
        return "igmp", None
    sport = dport = 0
    if protocol == 17 and len(payload) >= header + 4:
        sport, dport = struct.unpack("!HH", payload[header:header + 4])
    if protocol == 17 and sport in (67, 68) and dport in (67, 68):
        return "dhcp", None
    if 224 <= payload[16] <= 239:
        return "mcast_ip", socket.inet_ntoa(payload[16:20])
    if protocol == 17 and dport == 9 and broadcast:
        return "wol", None
    if broadcast:
        return "bcast_ip", None
    if not frame[0] & 1:
        return "unicast_other", None
    return "other", None


def source_mac(frame: bytes) -> str | None:
    return frame[6:12].hex(":") if len(frame) >= 12 else None
