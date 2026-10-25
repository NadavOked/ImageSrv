"""‏DHCP — כל מה שנוגע במכונה עצמה (אפיון סעיף 24).

‏dhcp.py הוא הלוגיקה הטהורה: מקבל הגדרות, מחזיר טקסט. כאן יושב הצד
המלוכלך — לקרוא אילו כרטיסים קיימים, לשאול אם מישהו כבר מחלק כתובות,
לכתוב את קבצי ה-conf ולהפעיל שירותים. הפרדה זו גם מה שמאפשר לבדיקות
להזריק `dhcp_hooks` ולעולם לא לגעת במכונה.

שני יעדים, בכוונה (‏#36):

- `DEFAULT_CONF` — בתוך `/etc/dnsmasq.d`, נטען לאינסטנס הראשי של
  dnsmasq (ה-DHCP של וילן ההפצה).
- `PROXY_CONF` — **מחוץ** ל-`/etc/dnsmasq.d`, כדי שהאינסטנס הראשי לא
  יטען אותו. הוא נטען ביחידה `imagectl-proxy` בלבד. ‏dnsmasq 2.91 קופא
  על בקשת PXE לפורט 4011 במצב proxy, וכל עוד זה כך — הקפיאה חייבת
  להישאר בתהליך שאפשר לאבד.
"""

from __future__ import annotations

import logging
import re
import secrets
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .atomicfile import write_atomic

log = logging.getLogger(__name__)

DEFAULT_CONF = "/etc/dnsmasq.d/imagectl-dhcp.conf"
#: קובץ המתקין (‏#1013) — נקרא כאן כדי לדעת אם `enable-tftp` עדיין יושב בו.
INSTALLER_CONF = "/etc/dnsmasq.d/imagectl.conf"
PROXY_CONF = "/etc/imagectl/dnsmasq-proxy.conf"
PROXY_UNIT = "imagectl-proxy"

#: ‏#141: רשימת המכונות הרשומות, שממנה dhcp-boot (render() הראשי) מדליק
#: tag:known. **מחוץ** ל-/etc/dnsmasq.d בכוונה — הוא נטען כ-dhcp-hostsfile
#: מפורש מתוך imagectl.conf (setup-boot-server.sh), לא דרך הסריקה
#: האוטומטית של dnsmasq לתיקייה, כדי ששינוי בו לא ידרוש לגעת בקובץ
#: הממשקים שהקונסולה כותבת. כבר ב-ReadWritePaths של imagectl-server
#: (‏-/etc/imagectl, אופציונלי) — אין שינוי ביחידה.
KNOWN_MACS_CONF = "/etc/imagectl/known-macs"


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


# --- מי כבר עונה ל-DHCP כאן? ------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """תוצאת ה-DHCPDISCOVER על ממשק — **שלושה** מצבים, לא שניים.

    - ‏`checked=False` — הבדיקה לא רצה בכלל. זה *לא* "נקי": שום דבר לא
      נשלל, ואסור לתת לזה לפתוח את ההדלקה.
    - ‏`checked=True` עם `servers` ריק — שאלנו, ואיש לא ענה.
    - ‏`servers` — מי ענה.

    האובייקט אמיתי (truthy) תמיד, בכוונה: קודם הוחזרו `None`/`[]`/רשימה,
    ו-`if found:` קיפל את שני הראשונים לאחד — ככה אפשר היה להדליק DHCP
    על ה-trunk של המכללה כשהבדיקה רק *נכשלה* ‏(#53). עכשיו `if` מקרי
    נופל לצד החוסם, לא לצד המדליק.
    """

    checked: bool
    servers: tuple[str, ...] = ()


def probe_existing_dhcp(iface: str, timeout: float = 2.0) -> ProbeResult:
    """שולח DHCPDISCOVER על הממשק ומחזיר מי ענה — ואם בכלל הצלחנו לשאול.

    בלי הרשאות או כשפורט 68 תפוס (לקוח DHCP על הכרטיס) מוחזר
    `ProbeResult(checked=False)`. זה מה שמונע את התקלה הגרועה ביותר:
    להדליק DHCP שני על רשת שכבר יש בה.
    """
    xid = secrets.token_bytes(4)
    try:
        mac = bytes.fromhex(
            (Path("/sys/class/net") / iface / "address").read_text().strip().replace(":", "")
        )
    except (OSError, ValueError):
        mac = b"\x02" + secrets.token_bytes(5)          # locally administered, random
    packet = (
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
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, 25, iface.encode() + b"\0")  # SO_BINDTODEVICE
        sock.bind(("", 68))
        sock.settimeout(timeout)
        sock.sendto(packet, ("255.255.255.255", 67))
    except OSError:
        return ProbeResult(checked=False)
    seen: list[str] = []
    try:
        while True:
            data, addr = sock.recvfrom(1024)
            if len(data) > 8 and data[4:8] == xid and addr[0] not in seen:
                seen.append(addr[0])
    except socket.timeout:
        pass
    except OSError:
        # שגיאה באמצע ההאזנה: מי שכבר ענה נספר, אבל שקט כאן אינו ראיה.
        return ProbeResult(checked=bool(seen), servers=tuple(seen))
    finally:
        sock.close()
    return ProbeResult(checked=True, servers=tuple(seen))


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
        return (head + " ה-proxy רץ באינסטנס נפרד (imagectl-proxy), ולכן "
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
