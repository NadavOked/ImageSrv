"""#3064, שלב 3 (K7): החושים של תקציב הרשת שאין להם בית אחר -- מונה הכרטיס של השרת (V9), הלחץ על הדיסק של השרת
(V10) והזריקות של מקלטי הגל (S44).

**כל חוש מחזיר ערך שנקרא, או ``None`` = "לא נקרא".** ``None`` אינו אפס: מונה שלא נקרא אינו "הכרטיס לא זרק" (עיקרון 5).
כל הקריאות לעולם אינן זורקות: חוש שנפל הוא ``None``, לא חריגה שמפילה את מחזור התקציב.

המונים מצטברים (בייטים, זריקות, מיקרו-שניות של לחץ), והתקציב (`budget.py`) לוקח הפרשים בין שתי קריאות.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("imagesrv.brain")

SYS_NET = Path("/sys/class/net")
PROC = Path("/proc")


def _count(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return None
    return int(text) if text.isascii() and text.isdigit() else None


def nic_counters(interface: str | None, sys_net: Path = SYS_NET) -> dict[str, int | None]:
    """V9: `tx_bytes` ו-`tx_dropped` של כרטיס ההפצה מ-`/sys/class/net/<if>/statistics/`. ללא כרטיס, או כרטיס שאינו
    בספרייה (ווינדוס, בדיקות), שני השדות ``None``."""
    out: dict[str, int | None] = {"tx_bytes": None, "tx_dropped": None}
    if not interface or "/" in interface or interface in (".", ".."):
        return out
    stats = Path(sys_net) / interface / "statistics"
    return {key: _count(stats / key) for key in out}


def psi_io_full_us(proc: Path = PROC) -> int | None:
    """V10: הזמן המצטבר (מיקרו-שניות) שבו **כל** המשימות חיכו ל-I/O -- שורת `full` של `/proc/pressure/io`.
    אותו קובץ ש-`server_io` קורא בתחילת גל ובסופו. חסר = ``None``."""
    try:
        for line in (Path(proc) / "pressure" / "io").read_text(encoding="ascii").splitlines():
            kind, *fields = line.split()
            if kind == "full":
                total = next((f.split("=", 1)[1] for f in fields if f.startswith("total=")), None)
                return int(total) if total is not None else None
    except (OSError, ValueError):
        return None
    return None


def receivers_rcvbuf(conn) -> int | None:
    """S44: סכום `RcvbufErrors` שדיווחו מחשבי השיכפול של הגלים שרצים עכשיו (`session_members.udp_drops_json`).

    **מכונה אחת שלא דיווחה, או דיווחה "לא נקרא", היא "לא נקרא" לכולם** -- סכום חלקי היה נקרא כ"אפס זריקות"
    (אותו כלל של `drive_rates._rcvbuf`). בגלי חתיכות הסוכן מדווח את השדה תוך כדי המעבר (#3064 שלב 4, G6,
    `agent/lib/restore/chunkloss.sh`); סוכן ישן שאינו מדווח = ``None``, והתקציב אומר "אין מדידה" ולא "המקלטים
    נקיים". אין גל רץ = ``None`` (אין על מי לדווח)."""
    return _receivers(conn, "rcvbuf")


def receivers_abandoned(conn) -> int | None:
    """S41 (G7): סכום החתיכות שמקלטי החתיכות נטשו באמצע (`chunkcast`: `evicted`), אותו מקור ואותו כלל של
    `receivers_rcvbuf`. סוכן שלא מדד אינו שולח שדה כזה, ולכן התשובה שם ``None``: "אין מדידה", לא "אפס נטישות"."""
    return _receivers(conn, "abandoned")


def receivers_card(conn, field: str) -> int | None:
    """S50 (#3597): סכום ``rx_missed`` או ``rx_no_buffer`` של **כרטיס הרשת של המקלטים** בגלים שרצים עכשיו (`wave_senses.nic`,
    מצטבר מתחילת הגל): אותו מקור ש-B59 (`rxlimit`) ו-K8 (`capability`) קוראים, ושונה מ-`receivers_rcvbuf` (הקרנל, אחרי הכרטיס).

    אותו כלל של `_receivers`: **מכונה אחת בלי שורת חושים, או עם השדה ``None`` (סוכן ישן, בלי ethtool: `rx_no_buffer`), היא
    "לא נקרא" לכולם** -- סכום חלקי היה נקרא כ"הכרטיס לא זרק". אין גל רץ = ``None``."""
    try:
        rows = conn.execute("SELECT w.senses_json FROM session_members m JOIN sessions s ON s.id = m.session_id"
                            " LEFT JOIN wave_senses w ON w.session_id = m.session_id AND w.mac = m.mac"
                            " WHERE s.state = 'running'").fetchall()
    except Exception:                           # noqa: BLE001 — חוש שנפל הוא "לא נקרא"
        log.exception("brain: the receivers' card counter %s could not be read", field)
        return None
    if not rows:
        return None
    total = 0
    for row in rows:
        try:
            got = json.loads(row[0]) if row[0] else None
        except (TypeError, ValueError):
            return None
        nic = got.get("nic") if isinstance(got, dict) else None
        value = nic.get(field) if isinstance(nic, dict) else None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        total += value
    return total


def _receivers(conn, field: str) -> int | None:
    try:
        rows = conn.execute("SELECT m.udp_drops_json FROM session_members m JOIN sessions s ON s.id = m.session_id"
                            " WHERE s.state = 'running'").fetchall()
    except Exception:                           # noqa: BLE001 — חוש שנפל הוא "לא נקרא"
        log.exception("brain: the receivers' %s counters could not be read", field)
        return None
    if not rows:
        return None
    total = 0
    for row in rows:
        try:
            got = json.loads(row[0]) if row[0] else None
        except (TypeError, ValueError):
            return None
        value = got.get(field) if isinstance(got, dict) else None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        total += value
    return total
