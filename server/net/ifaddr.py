"""כרטיס וילן השרתים כלקוח DHCP — הקונסולה נקשרת לפי **שם כרטיס** (‏#1088).

הכרעת נדב (18/09): השרת הראשי מתחבר לרשת השרתים של המכללה ומקבל כתובת
מה-DHCP שלה. עד כאן ‎--console-host‏ היה כתובת בלבד — כתובת שמשתנה עם
lease חדש השאירה את הקונסולה קשורה לכתובת שכבר אינה על הכרטיס. מעכשיו
הדגל מקבל גם שם כרטיס (`eth0`/`ens18`); השרת פותר את הכתובת בעלייה
ו**מאזין מחדש** כשהיא משתנה.

שלושה חלקים, כולם טהורים או עם hooks:

* ‏`ipv4_of` / `read_ipv4` — שם כרטיס → הכתובת שלו, מ-`ip -json -4 addr`.
  שלושה מצבים, לא שניים (עיקרון 5): כתובת · אין כתובת (`None`) · הבדיקה
  עצמה נכשלה (`AddressLookupError`). "אין כתובת" הוא מצב לגיטימי — lease
  שעוד לא הגיע — ואינו כשל; `ip` שנפל הוא כשל ואינו "אין כתובת".
* ‏`AddressWatcher` — תהליכון שדוגם כל `interval` שניות ומדווח **שינוי**
  בלבד (‏`on_change(old, new)`). ההשוואה היא לערך שנקרא בפעם הקודמת, ולכן
  ‏None→כתובת (lease ראשון) הוא שינוי כמו כתובת→כתובת.
* ‏`dhclient_lease` / `servers_nic_status` — מה שמסך הבריאות מציג: הכתובת,
  ‏DHCP או סטטי, ומתי החכירה פגה — מקובץ החכירות של dhclient (ifupdown),
  כי `ip addr` אינו נושא תוקף לכתובת ש-dhclient קבע.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from server.net.dhcp import validate_name

#: כל כמה שניות הכתובת נדגמת. ‏lease של DHCP נמדד בשעות; 10 שניות זה
#: הזמן שלוקח למפעיל להבחין ש"הקונסולה לא עונה" ולנסות שוב.
POLL_SECONDS = 10.0

#: איפה ifupdown/dhclient שומרים את החכירה של כל כרטיס (דביאן).
DHCLIENT_LEASES_DIR = "/var/lib/dhcp"


class AddressLookupError(RuntimeError):
    """‏`ip` קיים ונכשל, או שהפלט שלו אינו JSON — הבדיקה עצמה נשברה.
    אינו "אין כתובת": מי שמקפל את השניים יקשור את הקונסולה ל-loopback
    ויקרא לזה "עוד לא הגיע lease"."""


def is_interface_name(value: str | None) -> bool:
    """האם הערך הוא שם כרטיס (ולא כתובת IP). אותה רשימת-היתר כמו שם
    כרטיס בקובץ dnsmasq (‏`dhcp.validate_name`) — ולא "כל מה שאינו IP":
    ‏`0.0.0.0`, ‏`::1` ו-`localhost` אינם שמות כרטיס."""
    if not value or value == "localhost":
        return False
    if _looks_like_ip(value):
        return False
    try:
        validate_name(value)
    except ValueError:
        return False
    return True


def _looks_like_ip(value: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


# --- שם כרטיס → כתובת ---------------------------------------------------------


def ipv4_of(name: str, nics: list) -> str | None:
    """הכתובת ה-IPv4 של הכרטיס מתוך פלט `ip -json addr` שכבר נקרא.

    ‏#1121 ס' 5: בזמן חידוש lease יש לרגע **שתי** כתובות, והישנה מסומנת
    `deprecated`; ‏169.254.x היא `scope link`. לכן: כתובת `scope global`
    שאינה deprecated קודמת; deprecated מדולגת תמיד; בלי global — הראשונה
    שאינה deprecated. ‏None = הכרטיס קיים בלי IPv4 **או** אינו ברשימה —
    הקורא שרוצה להבדיל ביניהם בודק `ifname` בעצמו."""
    fallback: str | None = None
    for nic in nics or []:
        if nic.get("ifname") != name:
            continue
        for addr in nic.get("addr_info") or []:
            if not (addr.get("family") == "inet" or (addr.get("local") or "").count(".") == 3):
                continue
            if addr.get("deprecated"):
                continue
            if addr.get("scope", "global") == "global":
                return addr.get("local")
            fallback = fallback or addr.get("local")
    return fallback


def _run_ip(args: list[str]) -> str:
    try:
        return subprocess.run(
            ["ip", "-json", *args], capture_output=True, text=True,
            check=True, timeout=5, stdin=subprocess.DEVNULL,
        ).stdout
    except FileNotFoundError as exc:
        raise AddressLookupError("אין `ip` במכונה הזו") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise AddressLookupError(f"'ip -json addr' נכשל: {exc}") from exc


def read_ipv4(name: str, run: Callable[[list[str]], str] = _run_ip) -> str | None:
    """הכתובת של הכרטיס **עכשיו**, מהמכונה. ‏`run` מוזרק בבדיקות.

    ‏`ip -json -4 addr show dev X` על כרטיס שאינו קיים יוצא 1 — וזה
    ‏`AddressLookupError` ("הכרטיס לא נמצא" הוא כשל תצורה, לא lease
    שמתעכב); על כרטיס בלי כתובת הוא יוצא 0 עם `addr_info: []` — ‏None.
    """
    out = run(["-4", "addr", "show", "dev", name])
    try:
        nics = json.loads(out or "[]")
    except json.JSONDecodeError as exc:
        raise AddressLookupError(f"פלט 'ip -json addr' אינו JSON: {exc}") from exc
    return ipv4_of(name, nics)


# --- הדוגם ---------------------------------------------------------------------


class AddressWatcher:
    """דוגם את כתובת הכרטיס ומדווח שינוי — בתהליכון daemon, או ידנית
    (‏`check_once`) בבדיקות.

    ‏`on_change(old, new)` נקרא **רק** כשהכתובת שנקראה שונה מהקודמת; כשל
    קריאה (`AddressLookupError`) אינו "אין כתובת" — הוא נמסר ל-`on_error`
    ואינו משנה את הערך הידוע, כדי שנפילה חד-פעמית של `ip` לא תסגור את
    מאזין הקונסולה.
    """

    def __init__(self, name: str, *, current: str | None,
                 on_change: Callable[[str | None, str | None], None],
                 lookup: Callable[[str], str | None] = read_ipv4,
                 on_error: Callable[[str], None] | None = None,
                 interval: float = POLL_SECONDS) -> None:
        self.name = name
        self.current = current
        self._on_change = on_change
        self._on_error = on_error or (lambda msg: None)
        self._lookup = lookup
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check_once(self) -> bool:
        """דגימה אחת. מחזיר True אם הכתובת השתנתה (ו-`on_change` נקרא)."""
        try:
            now = self._lookup(self.name)
        except AddressLookupError as exc:
            self._on_error(str(exc))
            return False
        if now == self.current:
            return False
        old, self.current = self.current, now
        try:
            handled = self._on_change(old, now)
        except Exception:
            # ‏#1121 ס' 6: חריגה = הטיפול לא קרה; הערך הידוע חוזר לישן כדי
            # שהדגימה הבאה תדווח שוב (אחרת השינוי "נזכר" בלי מאזין).
            self.current = old
            raise
        if handled is False:
            # הטיפול נכשל (למשל לולאת האירועים עוד לא עלתה) — הערך הידוע
            # נשאר הישן, כדי שהדגימה הבאה תנסה שוב ולא "תזכור" כתובת
            # שאף מאזין לא נקשר אליה.
            self.current = old
            return False
        return True

    def start(self) -> "AddressWatcher":
        self._thread = threading.Thread(
            target=self._loop, name=f"ifaddr-{self.name}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # ‏#1121 ס' 6: חריגה ב-`on_change`/`lookup` שאינה AddressLookupError
        # הייתה הורגת את התהליכון לצמיתות — והקונסולה הייתה נשארת על
        # כתובת ישנה בלי שאיש יראה. נרשמת ב-`on_error`, והדגימה הבאה רצה.
        while not self._stop.wait(self.interval):
            try:
                self.check_once()
            except Exception as exc:                                    # noqa: BLE001
                self._on_error(f"הדוגם של {self.name} נפל ({exc!r}) — ממשיך לדגום")


# --- חכירת DHCP של הכרטיס (למסך הבריאות) ----------------------------------------


@dataclass(frozen=True)
class Lease:
    """חכירה אחת מקובץ dhclient: הכתובת, מי נתן אותה, ומתי היא פגה."""

    address: str
    expires: datetime | None
    server: str = ""
    hostname: str = ""

    def expires_text(self) -> str:
        if self.expires is None:
            return "ללא תוקף"
        return self.expires.astimezone().strftime("%d/%m %H:%M")


_LEASE_BLOCK = re.compile(r"lease\s*\{(.*?)\}", re.DOTALL)
_FIELD = re.compile(r"^\s*(fixed-address|expire|option dhcp-server-identifier|"
                    r"option host-name)\s+(.+?);\s*$", re.MULTILINE)


def parse_dhclient_leases(text: str) -> Lease | None:
    """החכירה **האחרונה** בקובץ — dhclient מוסיף בסוף ואינו מוחק.

    ‏`expire 4 2026/09/18 12:00:00;` הוא UTC (dhclient כותב בזמן אוניברסלי
    כברירת מחדל). ‏`expire never;` = ללא תוקף. ‏None = אין אף חכירה בקובץ.
    """
    latest: Lease | None = None
    for block in _LEASE_BLOCK.finditer(text or ""):
        fields = {m.group(1): m.group(2).strip().strip('"')
                  for m in _FIELD.finditer(block.group(1))}
        address = fields.get("fixed-address")
        if not address:
            continue
        expires = _parse_expire(fields.get("expire", ""))
        latest = Lease(address=address, expires=expires,
                       server=fields.get("option dhcp-server-identifier", ""),
                       hostname=fields.get("option host-name", ""))
    return latest


def _parse_expire(raw: str) -> datetime | None:
    if not raw or raw == "never":
        return None
    # ‏"4 2026/09/18 12:00:00" — יום בשבוע, תאריך, שעה, ב-UTC.
    parts = raw.split()
    if len(parts) == 3:
        parts = parts[1:]
    try:
        return datetime.strptime(" ".join(parts), "%Y/%m/%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def dhclient_lease(name: str, leases_dir: str | Path = DHCLIENT_LEASES_DIR) -> Lease | None:
    """החכירה של הכרטיס מ-`/var/lib/dhcp/dhclient.<if>.leases`. ‏None =
    אין קובץ (כרטיס סטטי, או לקוח DHCP אחר) או שאין בו חכירה."""
    path = Path(leases_dir) / f"dhclient.{name}.leases"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_dhclient_leases(text)


def servers_nic_status(name: str, address: str | None, lease: Lease | None,
                       *, lookup_failed: str | None = None,
                       hostname: str = "") -> tuple[str, str]:
    """שורת "כתובת השרתים" במסך הבריאות — ‏(state, detail), שלושה מצבים:

    * הבדיקה נכשלה (`lookup_failed`) → `unknown`. לא "אין כתובת".
    * אין כתובת → `warn` — הקונסולה על loopback בלבד עד שיגיע lease.
    * יש כתובת → `ok`, עם "DHCP, פג ב-…" כשיש חכירה תואמת, "סטטי" כשאין
      קובץ חכירות, ו"DHCP — החכירה שנקראה היא לכתובת אחרת" כשהן חלוקות.
    """
    label = f"כתובת השרתים ({name})"
    if lookup_failed:
        return "unknown", f"{label}: לא נקראה — {lookup_failed}"
    if not address:
        return "warn", (f"{label}: אין כתובת על הכרטיס — הקונסולה נגישה מ-127.0.0.1 "
                        "בלבד עד שיגיע lease")
    name_part = f"; שם: {hostname}" if hostname else ""
    if lease is None:
        return "ok", f"{label}: {address} (סטטי או ללא קובץ חכירות{name_part})"
    if lease.address != address:
        return "warn", (f"{label}: {address} (DHCP — החכירה שנקראה היא ל-{lease.address}"
                        f"{name_part})")
    return "ok", f"{label}: {address} (DHCP, פג ב-{lease.expires_text()}{name_part})"
