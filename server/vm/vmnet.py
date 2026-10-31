"""הרשת של ה-VM לעדכון (‏#1334, ‏#1468) — זהות משלו על כרטיס שהמפעיל בוחר.

**הכרעת נדב 16/10 (‏#1468):** ל-VM יש **כתובת משלו** ברשת שהמפעיל בוחר
("כרטיס לעדכוני VM"), כדי שחומת האש של המכללה תראה אותו בנפרד מהשרת ותנטר
ותשלוט בו לפי כתובת. האורח רשאי להגיע למארחים אחרים בוילן הזה (למשל שיתוף
קבצים) — **חומת האש של המכללה** שולטת בזה, לא אנחנו. כרטיס ההפצה (וכל כרטיס
שמתחתיו — חבר ב-bond, הורה של VLAN) מסורב; כרטיס הניהול **מותר** (רשת
הניהול יכולה להיות גם רשת הכיתות).

**החיבור:** ‏macvtap במצב `bridge` על הכרטיס שנבחר (`TAP_NAME`), עם MAC
יציב שהשרת ייצר פעם אחת (`new_mac` — unicast, locally-administered; שמור
ב-DB). ‏QEMU מקבל את ה-fd של ‏`/dev/tapN` (‏`-netdev tap,fd=`) ושום דבר
אחר. האורח עושה DHCP בעצמו מול שרת ה-DHCP של הוילן — **אין** `ip_forward`,
**אין** DHCP על המארח, ואין NAT.

**האיום:** ‏Windows שמתעדכן מהאינטרנט הוא אורח לא-מהימן. מה שאסור לו: להגיע
**לשרת עצמו** (הקונסולה, ה-API של הסוכן, הערוץ הבין-שרתי) ולרשת ההפצה.

* **מארח ↔ אורח בשכבה 2 — סגור מעצם macvtap.** בין הכרטיס לבין ילד macvlan
  אין מיתוג פנימי: מסגרת מהאורח לכתובת של השרת על אותו כרטיס יוצאת לחוט,
  והסוויץ' אינו מחזיר מסגרת לפורט שממנו היא באה. זה לא כלל שלנו שאפשר לשכוח
  לטעון — זה המבנה, ולכן לא מנסים "לתקן" אותו (נדב: כך במכוון).
* **בכל זאת, במארח** (`ruleset`, ‏`table inet imagesrv_vm`): ‏`input` זורק כל
  מסגרת שה-MAC של האורח הוא המקור שלה — ‏IPv4 **ו-IPv6** (משפחת `inet`;
  סוויץ' עם hairpin/VEPA). ‏`output` זורק כל מה שמשתמש המערכת `imagesrv-vm`
  שולח: ‏QEMU עם macvtap אינו צריך socket רשת אחד (‏VNC/QMP הם unix
  sockets), וגם כלי השמירה שרצים תחתיו.
* **אין כלל לפי כתובת — במכוון (סקירת אבטחה 16/10).** כלל `ip saddr` לפי
  הכתובת מה-DHCP היה נשען על תשובה שכל אחד בוילן יכול לזייף (ואז השרת זורק
  כתובת שבחר התוקף — השער, מנהל בקונסולה), היה מכסה IPv4 בלבד, ונשאר על
  הכתובת הישנה אחרי חידוש. כלל לפי MAC אינו תלוי בשום ראיה מה-DHCP.
* **מה שהמארח אינו מכסה, ולמה זה בסדר לפי ההכרעה:** הדרך **המנותבת** — מהאורח
  לשער של הוילן ומשם לכתובת של השרת (גם לכתובת על אותו כרטיס, אם השער מחזיר).
  שם המסגרת מגיעה מה-MAC של השער, לא של האורח. זו בדיוק חומת האש של המכללה,
  והסיבה שה-VM קיבל זהות משלו: היא רואה אותו בכתובת שלו וחוסמת אותו מהשרת.
  נדב מאמת זאת מתוך האורח, ב-IPv4 וב-IPv6, בכל פלטפורמה.

**ראיה ל-lease** (`watch_lease`): ‏socket ‏`AF_PACKET` על `TAP_NAME` עצמו —
רואה רק את מה שהאורח שולח ומה שנמסר לו. **‏DHCPACK** שה-`chaddr` שלו הוא
ה-MAC של ה-VM = שרת ה-DHCP של הוילן נתן לו כתובת. זו ראיה חיובית מהרשת, לא
מהאורח: ‏ARP של האורח היה "מוכיח" גם כתובת APIPA שהוא המציא לעצמו, ו-guest
agent אינו מותקן באימג'ים של המכללה. בלי חבילה חדשה (stdlib), קריאה בלבד.
ACK נספר רק כש**נכנס** מהחוט (לא `PACKET_OUTGOING`, ‏MAC המקור אינו של ה-VM —
אחרת האורח "עונה לעצמו"), כשה-`xid` שלו הוא של בקשה שהאורח שלח, וכשהכתובת
שימושית (לא loopback/link-local/multicast/broadcast/0.0.0.0/שמורה). ההאזנה
נמשכת כל זמן שה-VM רץ, וחידוש עם כתובת אחרת מעדכן את המסך. הכתובת **מוצגת**
בלבד — שום כלל במארח אינו נבנה ממנה.
‏DHCP שנשלח ולא נענה ≠ ‏DHCP שלא נשלח — שני סירובים שונים (`no_lease_message`).

**כשל = סירוב** (עיקרון 5): ‏macvtap שלא נוצר או לא נקרא בחזרה, טבלה שלא
חזרה, ‏socket שלא נפתח, ואין ACK תוך `LEASE_TIMEOUT_S` — כל אחד בשמו, עם הרמז
לפלטפורמה (`platform_hint`, לפי `systemd-detect-virt`).
"""

from __future__ import annotations

import ipaddress
import os
import re
import secrets
import socket
import struct
import subprocess
import time
from pathlib import Path
from typing import Callable

TABLE = "imagesrv_vm"
VM_USER = "imagesrv-vm"
#: ה-macvtap של ה-VM. ‏15 תווים לכל היותר (IFNAMSIZ).
TAP_NAME = "imagesrv-vm0"
#: כמה זמן מהדלקת QEMU עד שחייב להגיע DHCPACK. ‏Windows על KVM מבקש כתובת
#: בדקה הראשונה; חמש דקות מכסות זיהוי חומרה בעלייה ראשונה של אימג'.
LEASE_TIMEOUT_S = 300
_IFNAME = re.compile(r"[A-Za-z0-9_.:-]{1,15}")
_MAC = re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}")


# --- ה-MAC --------------------------------------------------------------------------


def new_mac(rand: Callable[[int], bytes] = secrets.token_bytes) -> str:
    """MAC חדש: ביט 0 של הבית הראשון כבוי (unicast), ביט 1 דלוק (מנוהל מקומית —
    אינו מתנגש באף יצרן). נוצר **פעם אחת** ונשמר (`vmupdate.ensure_mac`)."""
    raw = bytearray(rand(6))
    raw[0] = (raw[0] & 0xFC) | 0x02
    return ":".join(f"{b:02x}" for b in raw)


def valid_mac(mac: object) -> bool:
    """unicast ומנוהל מקומית, באותיות קטנות — כל דבר אחר אינו ה-MAC שלנו."""
    if not isinstance(mac, str) or not _MAC.fullmatch(mac):
        return False
    first = int(mac[:2], 16)
    return first & 0x01 == 0 and first & 0x02 == 0x02


def _mac(mac: str) -> str:
    if not valid_mac(mac):
        raise ValueError(f"MAC לא תקין: {mac!r}")
    return mac


def valid_ifname(name: object) -> bool:
    return isinstance(name, str) and bool(_IFNAME.fullmatch(name))


# --- חומת האש במארח -------------------------------------------------------------------


def ruleset(uid: int, mac: str) -> str:
    """הטבלה — טקסט ל-`nft -f -`. ‏delete+create בטרנזאקציה אחת. אין בה שום כתובת
    שבאה מהרשת: ‏`ether saddr` במשפחת `inet` תופס את האורח ב-IPv4 וב-IPv6."""
    if type(uid) is not int or uid <= 0:
        raise ValueError(f"uid לא תקין: {uid!r}")
    mac = _mac(mac)
    lines = [
        f"add table inet {TABLE}",
        f"delete table inet {TABLE}",
        f"table inet {TABLE} {{",
        # #1405: positive match only -- an orphaned socket has no owner and must
        # stay on the hook chain's accept policy.
        "  chain out {",
        "    type filter hook output priority 0; policy accept;",
        f"    meta skuid {uid} drop",
        "  }",
        "  chain in {",
        "    type filter hook input priority 0; policy accept;",
        f"    ether saddr {mac} drop",
        "  }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def apply(uid: int, mac: str, *, run=subprocess.run) -> str | None:
    """טוען וקורא בחזרה. ‏None = הטבלה במקומה; אחרת הסיבה."""
    text = ruleset(uid, mac)
    try:
        loaded = run(["nft", "-f", "-"], input=text, capture_output=True, text=True, timeout=15)
        if loaded.returncode != 0:
            return f"nft -f נכשל (rc={loaded.returncode}): {loaded.stderr.strip()}"
        back = run(["nft", "list", "table", "inet", TABLE], capture_output=True, text=True,
                   timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"nft לא רץ: {exc}"
    if back.returncode != 0:
        return f"הטבלה לא נקראה בחזרה (rc={back.returncode}): {back.stderr.strip()}"
    for needle in (f"skuid {uid} drop", f"ether saddr {mac} drop"):
        if needle not in back.stdout:
            return f"הטבלה נקראה בחזרה בלי '{needle}' — לא ידוע מה נטען"
    return None


def remove(*, run=subprocess.run) -> str | None:
    """מוחק את הטבלה כשה-VM כבה — אין סיבה לחסום MAC שאיש אינו משתמש בו.
    ‏None = אין טבלה (נמחקה עכשיו או לא הייתה)."""
    try:
        run(["nft", "delete", "table", "inet", TABLE], capture_output=True, text=True,
            timeout=15, stdin=subprocess.DEVNULL)
        back = run(["nft", "list", "table", "inet", TABLE], capture_output=True, text=True,
                   timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"nft לא רץ: {exc}"
    return None if back.returncode != 0 else "הטבלה עדיין קיימת אחרי delete"


# --- macvtap ---------------------------------------------------------------------------


def macvtap_up(lower: str, mac: str, *, run=subprocess.run,
               sysfs: Path = Path("/sys/class/net"), dev: Path = Path("/dev"),
               wait_s: float = 5.0, sleep=time.sleep) -> tuple[Path | None, str | None]:
    """יוצר את `TAP_NAME` על `lower` ומחזיר (‏/dev/tapN, None) — או (None, הסיבה).
    נקרא בחזרה מ-`ip -d link`: סוג, מצב bridge, ה-MAC והכרטיס שמתחתיו."""
    if not valid_ifname(lower) or lower == TAP_NAME:
        return None, f"שם כרטיס לא תקין: {lower!r}"
    mac = _mac(mac)
    try:
        run(["ip", "link", "del", TAP_NAME], capture_output=True, text=True, timeout=15,
            stdin=subprocess.DEVNULL)   # שארית מ-VM קודם/שרת שנפל; אין — rc≠0, בסדר
        for argv in (["ip", "link", "add", "link", lower, "name", TAP_NAME, "address", mac,
                      "type", "macvtap", "mode", "bridge"],
                     ["ip", "link", "set", TAP_NAME, "up"]):
            proc = run(argv, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
            if proc.returncode != 0:
                return None, f"{' '.join(argv[:3])} נכשל (rc={proc.returncode}): {proc.stderr.strip()}"
        back = run(["ip", "-d", "-o", "link", "show", "dev", TAP_NAME], capture_output=True,
                   text=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"ip לא רץ: {exc}"
    shown = back.stdout.lower() if back.returncode == 0 else ""
    for needle in (f"{TAP_NAME}@{lower.lower()}", f"link/ether {mac}", "macvtap", "mode bridge"):
        if needle not in shown:
            return None, f"{TAP_NAME} נקרא בחזרה בלי '{needle}' — לא ידוע מה נוצר"
    try:
        index = int((sysfs / TAP_NAME / "ifindex").read_text().strip())
    except (OSError, ValueError) as exc:
        return None, f"ה-ifindex של {TAP_NAME} לא נקרא: {exc}"
    path = dev / f"tap{index}"
    deadline = time.monotonic() + wait_s
    while not path.exists():   # udev יוצר את ההתקן מעט אחרי ה-link
        if time.monotonic() >= deadline:
            return None, f"{path} לא נוצר תוך {wait_s:g} ש' (udev)"
        sleep(0.1)
    return path, None


def macvtap_down(*, run=subprocess.run) -> str | None:
    try:
        run(["ip", "link", "del", TAP_NAME], capture_output=True, text=True, timeout=15,
            stdin=subprocess.DEVNULL)
        back = run(["ip", "-o", "link", "show", "dev", TAP_NAME], capture_output=True,
                   text=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"ip לא רץ: {exc}"
    return None if back.returncode != 0 else f"{TAP_NAME} עדיין קיים אחרי ip link del"


# --- הראיה ל-lease ----------------------------------------------------------------------

_DHCP_MAGIC = b"\x63\x82\x53\x63"
_DISCOVER, _REQUEST, _ACK = 1, 3, 5
#: ‏`sll_pkttype` של מסגרת שהממשק **שלח** (linux/if_packet.h).
PACKET_OUTGOING = 4


def usable_lease(address: str) -> bool:
    """כתובת שאפשר להציג כ"ה-VM קיבל": לא loopback, ‏link-local, ‏multicast,
    ‏broadcast, ‏0.0.0.0 או טווח שמור."""
    ip = ipaddress.IPv4Address(address)
    return not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                or ip.is_reserved or ip == ipaddress.IPv4Address("255.255.255.255"))


def dhcp_from(frame: bytes, mac: str) -> tuple[str, str | None, str | None, bytes] | None:
    """מסגרת Ethernet → ‏("request", None, None, xid) כשהאורח ביקש כתובת,
    ‏("ack", הכתובת, שרת ה-DHCP, xid) כשנענה — רק כשה-`chaddr` הוא `mac` והכתובת
    שימושית (`usable_lease`). כל דבר אחר — None. **מי** שלח — בודק הקורא."""
    try:
        if len(frame) < 14 + 20 + 8 + 240 or frame[12:14] != b"\x08\x00":
            return None
        ihl = (frame[14] & 0x0F) * 4
        if frame[14] >> 4 != 4 or ihl < 20 or frame[23] != 17:
            return None
        udp = 14 + ihl
        sport, dport = struct.unpack("!HH", frame[udp:udp + 4])
        boot = udp + 8
        op, htype, hlen = frame[boot], frame[boot + 1], frame[boot + 2]
        if htype != 1 or hlen != 6 or frame[boot + 236:boot + 240] != _DHCP_MAGIC:
            return None
        if frame[boot + 28:boot + 34] != bytes.fromhex(mac.replace(":", "")):
            return None
        xid = bytes(frame[boot + 4:boot + 8])
        kind, server, i = None, None, boot + 240
        while i < len(frame) and frame[i] != 255:
            if frame[i] == 0:
                i += 1
                continue
            code, size = frame[i], frame[i + 1]
            value = frame[i + 2:i + 2 + size]
            if code == 53 and size == 1:
                kind = value[0]
            elif code == 54 and size == 4:
                server = str(ipaddress.IPv4Address(value))
            i += 2 + size
        if op == 1 and (sport, dport) == (68, 67) and kind in (_DISCOVER, _REQUEST):
            return "request", None, None, xid
        if op == 2 and (sport, dport) == (67, 68) and kind == _ACK:
            yiaddr = str(ipaddress.IPv4Address(frame[boot + 16:boot + 20]))
            return ("ack", yiaddr, server, xid) if usable_lease(yiaddr) else None
    except (IndexError, ValueError, struct.error):
        return None
    return None


def _packet_socket(ifname: str):   # pragma: no cover — לינוקס בלבד, root
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
    sock.bind((ifname, 0))
    return sock


def watch_lease(ifname: str, mac: str, timeout: float, stop: Callable[[], bool],
                on_ack: Callable[[str, str | None], None] | None = None, *,
                open_socket=_packet_socket, clock=time.monotonic) -> dict:
    """מאזין ל-DHCP של האורח **כל זמן שה-VM רץ** (`stop`). ‏ACK תקף (נכנס מהחוט,
    ‏xid של בקשה שהאורח שלח, כתובת שימושית) → ‏`on_ack(כתובת, שרת)` — גם בחידוש.
    בלי ACK תקף תוך `timeout` — חוזר מיד עם ‏address=None. התוצאה:
    ‏`{"address", "server", "requests", "ignored", "error"}` — ‏`ignored` = תשובות
    שנדחו (יוצאות, מה-MAC של ה-VM, או בלי בקשה תואמת); ‏`error` = לא ניתן היה
    להאזין בכלל (≠ "לא נענה")."""
    mac = _mac(mac)
    own = bytes.fromhex(mac.replace(":", ""))
    out = {"address": None, "server": None, "requests": 0, "ignored": 0, "error": None}
    try:
        sock = open_socket(ifname)
    except OSError as exc:
        return {**out, "error": f"לא ניתן להאזין ל-DHCP על {ifname}: {exc}"}
    xids: set[bytes] = set()
    deadline = clock() + timeout
    try:
        sock.settimeout(1.0)
        while not stop() and (out["address"] or clock() < deadline):
            try:
                frame, where = sock.recvfrom(4096)
            except socket.timeout:
                continue
            seen = dhcp_from(frame, mac)
            if seen is None:
                continue
            kind, address, server, xid = seen
            outgoing = len(where) > 2 and where[2] == PACKET_OUTGOING
            from_vm = frame[6:12] == own
            if kind == "request":
                if outgoing and from_vm:
                    out["requests"] += 1
                    xids.add(xid)
                continue
            if outgoing or from_vm or xid not in xids:
                out["ignored"] += 1
                continue
            out.update(address=address, server=server)
            if on_ack is not None:
                on_ack(address, server)
    except OSError as exc:
        if out["address"] is None:
            out["error"] = f"ההאזנה ל-DHCP על {ifname} נקטעה: {exc}"
    finally:
        sock.close()
    return out


# --- הפלטפורמה והרמז --------------------------------------------------------------------

HINTS = {
    "vmware": ("השרת רץ ב-ESXi: ב-port group של הכרטיס הזה צריך Promiscuous mode, "
               "MAC address changes ו-Forged transmits = Accept (או MAC learning ב-vDS)"),
    "microsoft": ("השרת רץ ב-Hyper-V: בכרטיס הרשת של ה-VM של השרת צריך "
                  "Advanced Features › Enable MAC address spoofing"),
    "none": ("השרת על ברזל: בשרת אין מה לשנות — בדקו שיש DHCP בוילן של הכרטיס, "
             "שנוצרה כתובת שמורה ל-MAC, ושהסוויץ' אינו מגביל MAC לפורט (port security)"),
}


def detect_virt(*, run=subprocess.run) -> str | None:
    """‏`systemd-detect-virt`: ‏"vmware"/"microsoft"/"kvm"/… או "none" (ברזל).
    ‏None = לא ניתן לדעת (אין הכלי / לא רץ)."""
    try:
        proc = run(["systemd-detect-virt"], capture_output=True, text=True, timeout=5,
                   stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = proc.stdout.strip()
    # rc≠0 עם "none" = ברזל (כך הכלי מדווח); ‏rc≠0 בלי פלט = הכלי נכשל
    return out if out and (proc.returncode == 0 or out == "none") else None


def platform_hint(virt: str | None) -> str:
    if virt in HINTS:
        return HINTS[virt]
    where = f"השרת רץ ב-{virt}" if virt else "לא ידוע על מה השרת רץ"
    return (f"{where} — לפי הפלטפורמה: ESXi — {HINTS['vmware'].split(': ', 1)[1]}; "
            f"Hyper-V — {HINTS['microsoft'].split(': ', 1)[1]}; ברזל — אין מה לשנות בשרת")


def no_lease_message(result: dict, nic: str, mac: str, timeout: float, virt: str | None) -> str:
    if result.get("error"):
        head = f"לא ניתן לבדוק אם ה-VM קיבל כתובת: {result['error']}"
    elif result.get("requests"):
        head = (f"ה-VM ביקש כתובת ב-DHCP ({result['requests']} בקשות) ולא נענה תוך "
                f"{int(timeout)} ש' בכרטיס {nic} (MAC {mac})")
        if result.get("ignored"):
            head += (f" · {result['ignored']} תשובות נדחו (לא תאמו בקשה של ה-VM, "
                     "או שבאו ממנו עצמו)")
    else:
        head = (f"ה-VM לא ביקש כתובת ב-DHCP תוך {int(timeout)} ש' בכרטיס {nic} (MAC {mac}) — "
                "Windows לא עלה, או שהוגדרה בו כתובת קבועה")
    return f"{head}. {platform_hint(virt)}"


def stacked(name: str, sysfs: Path = Path("/sys/class/net")) -> set[str] | None:
    """כל הכרטיסים שמעל `name` ומתחתיו, טרנזיטיבית (‏`upper_*`/`lower_*` ב-sysfs):
    ‏bond שהוא חבר בו, החברים של bond, ‏VLAN עליו וההורה של VLAN. ‏None = התיקייה
    של `name` עצמו לא נקראה — "לא נבדק", לא "אין קשר"."""
    def links(nic: str) -> set[str]:
        return {p.name.split("_", 1)[1] for p in (sysfs / nic).iterdir()
                if p.name.startswith(("upper_", "lower_"))}
    try:
        todo = links(name)
    except OSError:
        return None
    seen: set[str] = set()
    while todo:
        nic = todo.pop()
        if nic in seen or nic == name:
            continue
        seen.add(nic)
        try:
            todo |= links(nic)
        except OSError:
            continue   # הקישור עצמו כבר נספר; רק הענף שמעבר לו לא נקרא
    return seen


def nic_exists(name: str, sysfs: Path = Path("/sys/class/net")) -> bool:
    return valid_ifname(name) and os.path.exists(sysfs / name)
