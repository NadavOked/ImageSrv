"""זהות מכונה — ‏MAC מוצהר **וגם** כתובת מקור שתואמת את חכירת ה-DHCP (‏#855).

ה-API של הסוכן זיהה מכונה לפי ה-MAC שהפונה הצהיר עליו. בוילן ההפצה
זה נתן לכל מכונה את מה שמגיע לכל מכונה אחרת: אסימון הקליטה של מחשב
הבנייה (‏hello עם ה-MAC שלו), הצטרפות לסבב בשם משכפל, ודיווח בשם חבר
סבב. הסוכן ב-initramfs חסר-מצב ואין לו איפה להחזיק סוד — ולכן הראיה
היא **הרשת**: השרת הוא ה-DHCP של וילן ההפצה (‏#702), וכל מכונה שעלתה
ב-PXE קיבלה ממנו חכירה. ‏hello שמגיע מכתובת אחרת מזו שבחכירה של ה-MAC
שהוא מצהיר עליו — אינו המכונה.

שלושה מצבים, לא שניים (עיקרון 5):

- **תואם** — ממשיכים.
- **לא תואם** (`identity_refused`) — יש חכירה, והכתובת אחרת. 403.
- **לא ניתן לבדוק** (`identity_unverifiable`) — אין חכירה ל-MAC, או שקובץ
  החכירות לא נקרא (‏DHCP חיצוני, קובץ שנמחק, הרשאות). **גם זה 403**:
  "לא הצלחנו לבדוק" אינו "בדקנו, תקין". מי שמריץ DHCP חיצוני מכבה את
  המתג `identity_check` **בגלוי** מהקונסולה, וזה נרשם ביומן ונאמר
  במסך הבריאות.

זיוף MAC ברמת הכבל (מכונה שמשנה גם את ה-MAC וגם מבקשת חכירה עליו) הוא
port-security בסוויץ' — מחוץ להיקף (‏#702).

המודול טהור: אינו מכיר FastAPI. הצד שנוגע בקובץ (`LeaseFile`) מוזרק
דרך `identity_hooks` ב-`create_runtime`, כמו `dhcp_hooks`; ‏`server.main`
מתקין אותו תמיד, ובדיקות היחידה מזריקות קובץ משלהן.
"""

from __future__ import annotations

import ipaddress
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from boot.grub_menu import normalize_mac as lenient_mac

from server.db import get_setting

#: המתג בטבלת `settings`. **חסר = דלוק**: הבדיקה כבויה רק כשמישהו כתב
#: "false" במפורש מהקונסולה (‏`setting_change` ביומן אומר מי).
SETTING = "identity_check"

#: קובץ החכירות של האינסטנס הראשי של dnsmasq בדביאן — ה-DHCP של וילן
#: ההפצה (‏`deploy/setup-boot-server.sh` אינו קובע `dhcp-leasefile`,
#: ולכן זו ברירת המחדל של החבילה). ‏`--dhcp-leases` ב-`server.main` מחליף.
DEFAULT_LEASES = "/var/lib/misc/dnsmasq.leases"

REFUSED = "identity_refused"
UNVERIFIABLE = "identity_unverifiable"


@dataclass(frozen=True)
class Lookup:
    """תוצאת חיפוש חכירה — **שלושה** מצבים, כמו `ProbeResult` (‏#53).

    ‏`read=False` — הקובץ לא נקרא בכלל; ‏`read=True, ip=None` — נקרא ואין
    חכירה ל-MAC; ‏`ip` — הכתובת שבחכירה. האובייקט truthy תמיד, בכוונה.
    """

    read: bool
    ip: str | None = None


@dataclass(frozen=True)
class Holders:
    """‏#997: הכיוון ההפוך — מי חכור לכתובת. ‏`read=False` — הקובץ לא נקרא;
    ‏`macs=()` — נקרא ואין חכירה על הכתובת."""

    read: bool
    macs: tuple[str, ...] = ()


def parse_leases(text: str) -> dict[str, str]:
    """‏`dnsmasq.leases` → ‏{mac קנוני: ip}. שורה: ‏`<פקיעה> <mac> <ip> <שם> <client-id>`.

    ‏dnsmasq כותב לאותו קובץ גם שורת `duid` ורשומות IPv6 (‏IAID במקום
    ‏MAC); שורה שאין בה MAC בעמודה השנייה או IPv4 בשלישית אינה חכירה
    שאפשר להשוות אליה ומדולגת. השורה **האחרונה** ל-MAC גוברת — זה גם
    הסדר שבו dnsmasq כותב את הקובץ מחדש.
    """
    leases: dict[str, str] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mac = lenient_mac(fields[1])
        if mac is None:
            continue
        try:
            ip = ipaddress.IPv4Address(fields[2])
        except (ipaddress.AddressValueError, ValueError):
            continue
        leases[mac] = str(ip)
    return leases


def parse_lease_rows(text: str) -> list[dict]:
    """‏#1039: השורות המלאות, לקונסולה — ‏``{mac, ip, hostname, expires}``.
    אותו סינון כמו ``parse_leases`` (‏duid/IPv6 מדולגים). ‏``hostname``
    ‏``None`` כש-dnsmasq כתב ``*``; ‏``expires`` = שניות epoch, ‏``0`` =
    חכירה בלי תפוגה."""
    rows = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mac = lenient_mac(fields[1])
        if mac is None:
            continue
        try:
            ip = ipaddress.IPv4Address(fields[2])
            expires = int(fields[0])
        except ValueError:
            continue
        host = fields[3] if len(fields) > 3 and fields[3] != "*" else None
        rows.append({"mac": mac, "ip": str(ip), "hostname": host, "expires": expires})
    return rows


class LeaseFile:
    """מקור החכירות — קובץ dnsmasq. נקרא **בכל** בדיקה: הקובץ קטן (שורה
    למכונה), dnsmasq כותב אותו מחדש בכל חכירה, ומטמון היה מחזיר "אין
    חכירה" על מכונה שעלתה הרגע."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read(self) -> dict[str, str] | None:
        try:
            return parse_leases(self.path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None

    def lookup(self, mac: str) -> Lookup:
        leases = self._read()
        if leases is None:
            return Lookup(read=False)
        return Lookup(read=True, ip=leases.get(mac))

    def holders(self, client_ip: str) -> Holders:
        """אילו MAC-ים חכורים לכתובת הזאת. ‏dnsmasq אינו מחזיק שתי חכירות
        חיות על כתובת אחת — אבל הסימולציה שמה את כל המכונות המדומות על
        loopback, ולכן רשימה ולא ערך יחיד."""
        leases = self._read()
        if leases is None:
            return Holders(read=False)
        return Holders(read=True, macs=tuple(
            mac for mac, ip in leases.items() if _same_address(ip, client_ip)))

    def rows(self) -> list[dict]:
        """‏#1039: כל החכירות (``parse_lease_rows``). ‏``OSError`` עולה
        לקורא — "לא נקרא" הוא מצב שהקונסולה אומרת בשם, לא רשימה ריקה."""
        return parse_lease_rows(self.path.read_text(encoding="utf-8", errors="replace"))

    def count(self) -> int | None:
        """כמה חכירות בקובץ, או ``None`` כשאינו נקרא — למסך הבריאות."""
        leases = self._read()
        return None if leases is None else len(leases)


def enabled(conn: sqlite3.Connection) -> bool:
    """‏"false" מפורש מכבה; כל ערך אחר — כולל הגדרה חסרה — דלוק."""
    return get_setting(conn, SETTING) != "false"


@dataclass(frozen=True)
class Verdict:
    ok: bool
    event: str = ""      # ‏REFUSED / UNVERIFIABLE — שם האירוע ביומן וקוד השגיאה
    detail: str = ""     # מה שנכתב ליומן: ‏MAC מוצהר, כתובת מקור, כתובת בחכירה
    message: str = ""    # מה שהפונה מקבל ב-`error`


def _same_address(a: str, b: str) -> bool:
    try:
        return ipaddress.ip_address(a) == ipaddress.ip_address(b)
    except ValueError:
        return a == b


def verify(conn: sqlite3.Connection, leases: LeaseFile,
           mac: str, client_ip: str | None) -> Verdict:
    """‏`mac` קנוני (הקורא נרמל); ‏`client_ip` — ה-peer של החיבור, לא כותרת."""
    if not enabled(conn):
        return Verdict(ok=True)
    if not client_ip:
        return Verdict(ok=False, event=UNVERIFIABLE,
                       detail=f"{mac}: no source address on the connection",
                       message="machine identity could not be verified: no source address")
    lookup = leases.lookup(mac)
    if not lookup.read:
        return Verdict(ok=False, event=UNVERIFIABLE,
                       detail=f"{mac} from {client_ip}: leases file {leases.path} not readable",
                       message="machine identity could not be verified: leases file not readable")
    if lookup.ip is None:
        return Verdict(ok=False, event=UNVERIFIABLE,
                       detail=f"{mac} from {client_ip}: no DHCP lease for this mac",
                       message="machine identity could not be verified: no DHCP lease for this mac")
    if not _same_address(lookup.ip, client_ip):
        return Verdict(ok=False, event=REFUSED,
                       detail=f"{mac} from {client_ip}, lease says {lookup.ip}",
                       message="source address does not match this mac's DHCP lease")
    return Verdict(ok=True)


#: ‏#2439: "נראתה לאחרונה" — חלון שבו `net_devices.last_seen` של כרטיס רשום הוא
#: ראיה שהמכונה שלו נמצאת עכשיו בכתובת שנרשמה. ‏hello חוזר כל דקות ספורות לכל
#: היותר, ומכונה שעברה לכרטיס אחר באתחול חדש עושה זאת אחרי יותר מזה.
EXTRA_SEEN_SECONDS = 600


def _recent(stamp: str | None, now: datetime | None = None) -> bool:
    try:
        seen = datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        return False
    if seen is None or seen.tzinfo is None:
        return False
    age = (now or datetime.now(timezone.utc)) - seen
    return timedelta(0) <= age <= timedelta(seconds=EXTRA_SEEN_SECONDS)


def verify_extra(conn: sqlite3.Connection, leases: LeaseFile, mac: str, extra: str,
                 client_ip: str | None) -> Verdict:
    """‏#2439: האם `extra` — כרטיס **רשום** מתוך `all_macs` של hello ש-`mac` שלו
    כבר עבר את `verify` — יכול לזהות את הפונה כמכונה שלו (#524).

    ‏#524 נשאר: לכרטיס שאינו כרטיס האתחול אין חכירה, ולכן אין לדרוש אותה. אבל
    כשהכרטיס הרשום **נמצא עכשיו במקום אחר** — חכירה שלו על כתובת אחרת, או
    ‏hello/תפריט שנרשם ממנו מכתובת אחרת ב-`EXTRA_SEEN_SECONDS` האחרונות —
    המכונה האמיתית אינה הפונה, ו-`all_macs` הוא הצהרה של זר (‏`identity_refused`).
    עד #2439 תחנה עם חכירה משלה הוסיפה לרשימה MAC של מכונה רשומה וקיבלה את
    ה-role, השם וה-`allowed_images` שלה."""
    if not enabled(conn) or not client_ip:
        return Verdict(ok=True)
    lookup = leases.lookup(extra)
    if lookup.read and lookup.ip is not None and not _same_address(lookup.ip, client_ip):
        return Verdict(ok=False, event=REFUSED,
                       detail=f"{mac} from {client_ip}: all_macs {extra} is leased to {lookup.ip}",
                       message="")
    row = conn.execute("SELECT ip, last_seen FROM net_devices WHERE mac = ?", (extra,)).fetchone()
    if (row is not None and row["ip"] and not _same_address(row["ip"], client_ip)
            and _recent(row["last_seen"])):
        return Verdict(ok=False, event=REFUSED,
                       detail=f"{mac} from {client_ip}: all_macs {extra} was seen from {row['ip']}"
                              f" at {row['last_seen']}",
                       message="")
    return Verdict(ok=True)


def verify_source(conn: sqlite3.Connection, leases: LeaseFile, client_ip: str | None,
                  is_registered: Callable[[str], bool]) -> Verdict:
    """‏#997: זהות **בלי MAC מוצהר** — ל-`GET /images/…/manifest|files`, שבהם
    הסוכן שולח רק את הכתובת (‏`http_get` ב-`common.sh`, בלי `?mac=` ובלי
    כותרת). הכיוון הפוך: כתובת המקור → החכירה שיושבת עליה → ה-MAC של
    החכירה רשום ב-`machines`. "יש חכירה" לבדו אינו ראיה — ‏`dhcp-range`
    מחלק לכל מי שמבקש בוילן, גם למחשב נייד של תלמיד; "המכונה שחכורה
    לכתובת הזאת היא אחת משלנו" כן. אותם שלושה מצבים ואותו מתג."""
    if not enabled(conn):
        return Verdict(ok=True)
    if not client_ip:
        return Verdict(ok=False, event=UNVERIFIABLE,
                       detail="no source address on the connection",
                       message="machine identity could not be verified: no source address")
    holders = leases.holders(client_ip)
    if not holders.read:
        return Verdict(ok=False, event=UNVERIFIABLE,
                       detail=f"{client_ip}: leases file {leases.path} not readable",
                       message="machine identity could not be verified: leases file not readable")
    if not holders.macs:
        return Verdict(ok=False, event=UNVERIFIABLE,
                       detail=f"{client_ip}: no DHCP lease on this address",
                       message="machine identity could not be verified: no DHCP lease on this address")
    if not any(is_registered(mac) for mac in holders.macs):
        return Verdict(ok=False, event=REFUSED,
                       detail=f"{client_ip}: lease holder {', '.join(holders.macs)} is not a registered machine",
                       message="the machine leased on this address is not registered")
    return Verdict(ok=True)


def health_status(conn: sqlite3.Connection, leases: LeaseFile | None) -> tuple[str, str]:
    """‏(מצב, פירוט) לשורה "זהות מכונה" במסך הבריאות — לפי מה שנקרא
    בחזרה מהקובץ, לא לפי ההגדרה. ‏`health.collect` עוטף ב-`check`."""
    if leases is None:
        return "off", "לא מותקנת בתהליך הזה — אין מקור חכירות (הרצת בדיקות)"
    if not enabled(conn):
        return "warn", ("לא נבדקת — המתג identity_check כבוי (DHCP חיצוני); "
                        "כל MAC מוצהר מתקבל, מכל כתובת")
    count = leases.count()
    if count is None:
        return "bad", (f"המתג דלוק אבל {leases.path} לא נקרא — כל hello מסורב "
                       "(identity_unverifiable). לתקן את הקובץ, או לכבות את המתג בהגדרות")
    return "ok", f"נבדקת מול {leases.path} — {count} חכירות"
