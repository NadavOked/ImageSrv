"""#3277: מהירות כרטיס ההפצה **כפי שהמשכפל קורא אותה עכשיו**, מה-hello האחרון -- לא מה-probe במטמון.

**למה.** עד #3277 מהירות הכרטיס (`nic_speed.reported`) באה מ-`probe.nic[].speed_mbps`, וה-probe נשמר אצל הסוכן
`PROBE_TTL` (600 ש') ואצל השרת בלי גיל: `probe.latest` מחזיר את הדגימה האחרונה **מכל אתחול**. קישור שירד ל-100Mb/s אחרי
ה-hello (כבל שהתנתק והתחבר מחדש) נשאר "1000 reported", ולא הייתה אזהרה ולא K3 (#3277, ארגוס 1ד, 17/11: צפוי 425 ש', בפועל 745).

**מה.** הסוכן שולח בכל hello שדה אח אחד, `nic_link` = `{"iface": "eth0", "speed_mbps": 100}` -- קריאת sysfs אחת של
`/sys/class/net/$IFACE/speed`, מחוץ למטמון. כאן הוא נשמר **בזיכרון** (לא בטבלה: hello הוא ה-poll, ושורה לכל hello היא
בדיוק מה ש-`probe.record` נמנע ממנו), עם הזמן שבו נקלט ועם מזהה האתחול.

- **`max_mbps`** (אופציונלי, `ethtool` "Supported link modes" אצל הסוכן): המהירות הגבוהה שהכרטיס תומך בה. עם `speed_mbps` הוא
  נותן את "הקישור נמוך" (`is_low`): **100Mb/s ומטה הוא תמיד צוואר בקבוק** (בכל רשת), וקישור מתחת ליכולת הכרטיס הוא נמוך גם הוא.
  `max_mbps` חסר = "לא ידוע", ואז רק הרצפה של 100.
- **שדה חסר** (סוכן ישן) או `speed_mbps: null` (הקישור למטה / ה-sysfs לא נקרא): אין כאן ערך. זה "לא ידוע" ולא 1000
  (עיקרון 5); `nic_speed` יחליט אם הדגימה של ה-probe עדיין ראויה.
- **ערך שעבר זמנו** (`LIVE_MAX_AGE_S`): אינו נחשב. מכונה ששתקה אינה "מדווחת".
- **אתחול חדש**: כשמזהה האתחול של hello משתנה (מכונה שעלתה מחדש), `boot_since` נרשם, ו-`nic_speed` אינו מקבל דגימת probe
  שנלקחה לפניו. אתחול ראשון שנראה אינו מסמן `boot_since`: השרת לא יודע מתי המכונה עלתה, ולכן לא פוסל על סמך השערה.

**לעולם אינו זורק** אל מסלול ה-hello.
"""

from __future__ import annotations

import threading
import time

from server.rounds.ledger import clean_boot_id

#: ערך חי ישן מזה אינו "מדווח". ה-hello חוזר כל כמה שניות, ולכן 120 ש' הן עשרות דופקים שהוחמצו.
LIVE_MAX_AGE_S = 120
#: #3277 (נדב 17/11): קישור של 100Mb/s ומטה הוא צוואר בקבוק בכל רשת, גם כשהכרטיס אינו מדווח את היכולת שלו.
LOW_FLOOR_MBPS = 100
#: תקרה לזיכרון: hello של MAC לא מוכר אינו מאומת, והמרשם לא ייגדל בלי גבול.
MAX_MACHINES = 1024

_lock = threading.Lock()
_machines: dict[str, dict] = {}


def _mbps(value: object) -> int | None:
    """מספר חיובי שלם, או None -- `-1` (קישור למטה), 0, bool, מחרוזת ומספר שבור אינם מהירות."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 400_000 else None


def is_low(mbps: int | None, max_mbps: int | None) -> bool:
    """#3277: הקישור נמוך -- 100Mb/s ומטה, או מתחת ליכולת הכרטיס. מהירות שלא נקראה אינה נמוכה (ואינה "תקינה": `None`
    אצל הקורא)."""
    return mbps is not None and (mbps <= LOW_FLOOR_MBPS or (max_mbps is not None and mbps < max_mbps))


def clean_bus(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    kind = value.get("bus")
    if kind == "pci":
        return {"bus": kind, **{key: value[key] if type(value.get(key)) is bool else None
                                for key in ("capable_66mhz", "bridge_capable_66mhz")}}
    if kind == "pcie":
        result = {"bus": kind}
        for key in ("current_link_speed", "max_link_speed", "current_link_width", "max_link_width"):
            n = value.get(key)
            valid = type(n) is int or (key.endswith("speed") and type(n) is float)
            result[key] = n if valid and n is not None and 0 < n <= 1024 else None
        return result
    return {"bus": "unknown"}


def note(mac: str, nic_link: object, boot_id: object, now: float | None = None, *,
         nic_bus: object = None) -> tuple[str, int, int | None] | None:
    """נקרא מכל hello. ``nic_link`` = השדה כפי שהגיע (כל צורה אחרת מ-dict: לא נשמר), ``boot_id`` = של ה-hello.

    מחזיר ``("low" | "back", mbps, max_mbps)`` **רק כשהמצב התהפך** (הקישור ירד, או חזר), אחרת ``None`` -- הקורא (ה-hello)
    רושם ביומן שורה אחת לשינוי, לא שורה לכל hello. אתחול ראשון שנראה אינו "חזר": אין מצב קודם להתהפך ממנו."""
    try:
        at = time.time() if now is None else now
        boot = clean_boot_id(boot_id)
        with _lock:
            row = _machines.get(mac)
            if row is None:
                if len(_machines) >= MAX_MACHINES:
                    _machines.pop(min(_machines, key=lambda m: _machines[m]["at"]), None)
                row = _machines[mac] = {"mbps": None, "max": None, "iface": None, "at": None, "boot": None,
                                        "boot_since": None, "low": None}
            if boot is not None and row["boot"] is not None and boot != row["boot"]:
                row["boot_since"] = at                     # אתחול חדש: מה שנלקח לפני כן אינו שלו
            if boot is not None:
                row["boot"] = boot
            row["at"] = at
            row["bus"] = clean_bus(nic_bus)
            if isinstance(nic_link, dict):
                iface = nic_link.get("iface")
                row["mbps"] = _mbps(nic_link.get("speed_mbps"))
                row["max"] = _mbps(nic_link.get("max_mbps"))
                row["iface"] = iface if isinstance(iface, str) and 0 < len(iface) <= 32 else None
            else:
                row["mbps"] = row["max"] = row["iface"] = None   # שדה חסר או פגום: "לא ידוע", ולא הערך הקודם
            if row["mbps"] is None:
                return None                                       # לא נקרא עכשיו: לא "נמוך" ולא "חזר" -- המצב הקודם נשמר
            low, before = is_low(row["mbps"], row["max"]), row["low"]
            row["low"] = low
            if before is None or before == low:
                return ("low", row["mbps"], row["max"]) if low and before is None else None
            return ("low" if low else "back", row["mbps"], row["max"])
    except Exception:    # noqa: BLE001 -- לעולם לא עוצר hello
        return None


def live(mac: str, now: float | None = None) -> int | None:
    """המהירות החיה של המכונה ב-Mb/s, או None: לא נקראה, לא נשלחה, או שעבר זמנה."""
    at = time.time() if now is None else now
    with _lock:
        row = _machines.get(mac)
        if row is None or row["mbps"] is None or row["at"] is None or at - row["at"] > LIVE_MAX_AGE_S:
            return None
        return row["mbps"]


def bus(mac: str, now: float | None = None) -> dict | None:
    at = time.time() if now is None else now
    with _lock:
        row = _machines.get(mac)
        if row and at - row["at"] <= LIVE_MAX_AGE_S:
            return row["bus"]
    return None


def link(mac: str, now: float | None = None) -> tuple[int, int | None] | None:
    """``(mbps, max_mbps)`` החיים של המכונה, או None (כמו `live`)."""
    mbps = live(mac, now)
    if mbps is None:
        return None
    with _lock:
        return mbps, _machines[mac]["max"]


def boot_since(mac: str) -> float | None:
    """מתי השרת ראה את האתחול הנוכחי מתחיל (שעון epoch), או None -- לא ידוע."""
    with _lock:
        row = _machines.get(mac)
        return row["boot_since"] if row is not None else None


def forget(mac: str | None = None) -> None:
    """לבדיקות: ריקון (מכונה אחת, או הכול)."""
    with _lock:
        _machines.pop(mac, None) if mac else _machines.clear()
