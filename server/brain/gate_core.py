"""#3064, K3 (שער הקבלה), קבוצה ד': הטיפוסים המשותפים של שערי פתיחת הסבב. עלה: אין כאן ייבוא של המוח.

הפסקים, ``Reading`` ו-``undecided`` נמצאים כאן ולא ב-`gates.py`, כי `gates.py` מייבא את קובצי השערים (`gate_host`,
`gate_machines`, `gate_round`), והם מייבאים מכאן. כל הכללים (מה זה כל פסק, צל בלבד, איך כותבים שער) ב-`gates.py`.
"""

from __future__ import annotations

import logging
from server.db import SqlConnection
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("imagesrv.brain")

OPEN, WARN, BLOCK, UNDECIDED = "OPEN", "WARN", "BLOCK", "UNDECIDED"
VERDICTS = (OPEN, WARN, BLOCK, UNDECIDED)
#: ``Reading.mac`` של שער שלא נוגע במשכפל מסוים.
ROUND = None


@dataclass(frozen=True)
class Reading:
    """מה שער אחד אומר: פסק, סיבה בעברית (מהקוד הקיים), וראיות. ``mac=None`` = השרת או הסבב כולו."""
    verdict: str
    reason: str
    evidence: tuple[str, ...] = ()
    mac: str | None = ROUND


def undecided(reason: str, evidence: tuple[str, ...] = (), mac: str | None = ROUND) -> Reading:
    """"לא הצלחנו לבדוק" -- לעולם לא ``OPEN``."""
    return Reading(UNDECIDED, reason, evidence, mac)


def name_of(conn: SqlConnection, mac: str) -> str:
    """שם המשכפל כפי שהמפעיל קורא אותו (`משכפל-N`), או ה-MAC כשהוא לא רשום."""
    from server.core import machine_names                                   # noqa: PLC0415 -- כמו `room._room_label`
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()
    return machine_names.label("cloner", row["suffix"]) if row else mac


def attach_health(conn: SqlConnection, collect: Callable[[], list[dict]] | None) -> None:
    """B51: מי שבנה את נתיב ה-`/health` (`health.create_health_router`) מוסר את ה-`collect` שלו, עם אותם hooks בדיוק (כולל SMART של
    דיסקי השרת, `server_disks`). נישא על החיבור, כמו `veto.attach`, כדי שהשער יקרא את אותן שורות שהמפעיל רואה."""
    try:
        setattr(conn, "brain_health", collect)   # רק חיבור שמקבל תכונות נושא אותו: SqlConnection גולמי = AttributeError
    except AttributeError:
        log.warning("brain: %s cannot carry the health source; the health gate is undecided", type(conn).__name__)


def health_rows(conn: SqlConnection) -> list[dict] | None:
    """כל שורות `/health` כפי שהמפעיל רואה אותן (`{"id", "label", "state", "detail"}`). ``None`` = המקור לא חובר: לא "אין שורות".
    זורק כשה-`collect` זורק: השער הופך אותו ל-`UNDECIDED`."""
    collect = getattr(conn, "brain_health", None)
    return None if collect is None else list(collect())
