"""מצב מערכי ה-RAID בעמוד הבריאות (#1989).

המתקין יוצר מערך mdadm (‏installer/lib/raid.sh) ורושם את העובדה ב-
``/etc/imagesrv/raid`` (‏0644 — השרת אינו רץ כ-root). הראיה למצב היא
``/proc/mdstat``, לא ההצהרה: לכל מערך שורה — תקין / פגוע / בבנייה מחדש
(עם אחוז). "התראה כשכונן נופל" = נורה אדומה כאן; ספירת הכשלים בקונסולה
נשענת על אותה רשימה. מייל עדיין אין (#1751).

עיקרון 5: מערך שהוצהר ואינו ב-mdstat אדום, ו-mdstat שלא נקרא אינו "אין
מערכים". בלי הצהרה ובלי מערכים — אין שורה בכלל.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MDSTAT_PATH = Path("/proc/mdstat")
RAID_FACT_PATH = Path("/etc/imagesrv/raid")

_HEAD = re.compile(r"^(md[\w/]+)\s*:\s*(active|inactive)\s*(?:\(\w[\w-]*\)\s*)?(\S+)?\s*(.*)$")
_MEMBER = re.compile(r"(\S+?)\[\d+\](\([FSRW]\))?")
_COUNT = re.compile(r"\[(\d+)/(\d+)\]\s*\[([U_]+)\]")
_PROGRESS = re.compile(r"\b(recovery|resync|reshape|check|repair)\s*=\s*([\d.]+)%")
_DELAYED = re.compile(r"\b(resync|recovery)\s*=\s*(DELAYED|PENDING)")


@dataclass
class Array:
    name: str
    active: bool
    level: str = ""
    members: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    spares: list[str] = field(default_factory=list)
    want: int | None = None
    have: int | None = None
    map: str = ""
    action: str = ""
    percent: float | None = None


def read_optional(path: Path) -> str | None:
    """None = הקובץ אינו קיים (אין md / אין הצהרה). שגיאה אחרת נזרקת —
    "לא נקרא" אינו "אין" (עיקרון 5)."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None


def parse_mdstat(text: str) -> list[Array]:
    arrays: list[Array] = []
    current: Array | None = None
    for raw in text.splitlines():
        line = raw.strip()
        head = _HEAD.match(line)
        if head:
            name, state, level, rest = head.groups()
            current = Array(name=name, active=state == "active")
            if level and level.startswith("raid") or level in ("linear", "multipath"):
                current.level = level
            else:   # inactive arrays list members right away
                rest = f"{level or ''} {rest}"
            for member, flag in _MEMBER.findall(rest):
                current.members.append(member)
                if flag == "(F)":
                    current.failed.append(member)
                elif flag == "(S)":
                    current.spares.append(member)
            arrays.append(current)
            continue
        if current is None or not line:
            continue
        count = _COUNT.search(line)
        if count:
            current.want, current.have, current.map = int(count[1]), int(count[2]), count[3]
        progress = _PROGRESS.search(line)
        if progress:
            current.action, current.percent = progress[1], float(progress[2])
        elif _DELAYED.search(line) and not current.action:
            current.action = _DELAYED.search(line)[1]
    return arrays


def parse_fact(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


LEVEL_LABELS = {"raid1": "RAID1", "raid5": "RAID5", "raid6": "RAID6", "raid10": "RAID10"}
ACTION_LABELS = {"recovery": "בבנייה מחדש", "resync": "בסנכרון", "reshape": "בשינוי מבנה",
                 "repair": "בתיקון"}


def array_check(array: Array) -> tuple[str, str]:
    """(state, detail) — ok / bad / warn, בעברית."""
    level = LEVEL_LABELS.get(array.level, array.level or "רמה לא ידועה")
    if not array.active:
        return "bad", f"{level} · המערך אינו פעיל (inactive) — הנתונים אינם נגישים"
    bits = [level]
    if array.want is not None:
        bits.append(f"{array.have}/{array.want} כוננים")
    missing = (array.want is not None and array.have is not None and array.have < array.want) \
        or "_" in array.map
    if array.failed:
        bits.append("נפל: " + ", ".join(array.failed) + " — להחליף")
    if array.action and array.action != "check":
        label = ACTION_LABELS.get(array.action, array.action)
        pct = f" {array.percent:g}%" if array.percent is not None else " (ממתין)"
        bits.append(label + pct)
        return "warn", " · ".join(bits)
    if missing or array.failed:
        if missing and not array.failed:
            bits.append("פגוע — חסר כונן במערך")
        return "bad", " · ".join(bits)
    if array.want is None:
        return "unknown", " · ".join(bits + ["לא נקרא מצב הכוננים"])
    if array.action == "check" and array.percent is not None:
        bits.append(f"בדיקה תקופתית {array.percent:g}%")
    bits.append("תקין")
    if array.spares:
        bits.append("רזרבי: " + ", ".join(array.spares))
    return "ok", " · ".join(bits)


def health_checks(read_mdstat, read_declared) -> list[dict]:
    """שורה לכל מערך. ‏`read_*` מחזירים טקסט, ‏None (אין קובץ) או זורקים.
    hook שלא הוזרק (None) = אין שורה — כמו `vm_host` ב-health.py."""
    from server.system.health import check
    if read_mdstat is None or read_declared is None:
        return []
    try:
        declared_text = read_declared()
    except OSError as exc:
        return [check("raid", "מערך RAID", "unknown", f"לא ניתן לקרוא {RAID_FACT_PATH}: {exc}")]
    declared = parse_fact(declared_text) if declared_text else {}
    want_level = declared.get("level", "none")
    want_array = (declared.get("array") or "/dev/md0").removeprefix("/dev/")
    expected = want_level not in ("", "none")
    try:
        mdstat = read_mdstat()
    except OSError as exc:
        if not expected:
            return [check("raid", "מערך RAID", "unknown", f"לא ניתן לקרוא /proc/mdstat: {exc}")]
        return [check("raid", "מערך RAID", "bad",
                      f"המתקין יצר {LEVEL_LABELS.get(want_level, want_level)}, אבל /proc/mdstat לא נקרא: {exc}")]
    arrays = parse_mdstat(mdstat) if mdstat else []
    rows = [check(f"raid_{a.name}", f"מערך RAID {a.name}", *array_check(a)) for a in arrays]
    if expected and not any(a.name == want_array for a in arrays):
        rows.insert(0, check("raid", "מערך RAID", "bad",
                             f"המתקין יצר {LEVEL_LABELS.get(want_level, want_level)} ({want_array}), "
                             "אבל המערך אינו מופיע ב-/proc/mdstat"))
    return rows
