"""#3064, K3 (שער הקבלה), קבוצה ד': שערים לפני פתיחת סבב. **בצל בלבד**: נרשם, אינו חוסם ואינו משנה דבר.

**מה זה.** כשסבב חדר נפתח (`room.open_round`), המוח שואל עשרה שערים על דברים שהשרת או הסוכן כבר מודדים והמוח עוד לא
קרא. כל שער מחזיר ``Reading``: אחד מארבעה פסקים, סיבה בעברית, וראיות. הרישום ב-``brain_decisions``: ``rule`` = מזהה
השער בקטלוג (``B49``...), ``action`` = ``ROUND_GATE``, ``point`` = שם השער.

**ארבעת הפסקים (ההגדרה אחת לכל השערים):**

- ``OPEN`` -- נבדק, ויש **ראיה חיובית** שהכול תקין (קוד תשובה, מונה, ערך שנקרא בחזרה). לא "לא מצאנו כשל".
- ``WARN`` -- יש בעיה, ופתיחת הסבב **עדיין סבירה**: המפעיל צריך לדעת. מערך פגוע, מקום שמתמלא, משכפל שחשוד.
- ``BLOCK`` -- פתיחת הסבב **חסרת תוחלת או מסוכנת**: היעד לא יעלה, או שהכתיבה תיכשל. משמר רק מה שהוכח, ולא "נראה רע".
- ``UNDECIDED`` -- **לא הצלחנו לבדוק**, או שאין מדידה (עיקרון 5). זה אינו ``OPEN`` ואינו ``WARN``: מצב בפני עצמו.

**הסיבה בעברית היא של הקוד הקיים.** שורת הבריאות או האזהרה שהמפעיל כבר רואה (`raid_status.array_check`,
`health.db_space_check`...) היא הסיבה, מילה במילה. לא נבנה כאן מסך, והמוח אינו מנסח מחדש.

**צל.** אף שער אינו חוסם, גם כש-``brain_veto_mode=enforce``. ביצוע (חסימה בפועל) לא נבנה: אם יידרש, הוא ייכנס
מאחורי מפתח משלו, וברירת המחדל שלו תהיה ``off``. ``off`` ב-``brain_veto_mode`` = לא נקרא ולא נרשם.

**איך כותבים שער.** פונקציה ``read(conn, macs, round_id) -> list[Reading]`` בקובץ ``gate_*.py``, ורישום ב-`GATES` למטה:

- שער של השרת או של הסבב כולו: ``Reading(mac=None)``, והישות ב-``brain_decisions`` היא הסבב.
- שער של משכפל: ``Reading(mac=...)`` לכל משכפל ב-``macs``, והישות היא המשכפל.
- **שער שלא החזיר כלום, או שזרק, או שהחזיר פסק שאינו אחד מהארבעה = ``UNDECIDED``** (הרצה מתעדת למה). שער אינו מוותר בשקט.
- ``OPEN`` של משכפלים מקופל לשורה אחת לשער ("נבדקו N משכפלים"): שורה לכל משכפל תקין היא רעש. כל פסק אחר נרשם בשם המשכפל.

**ברקע.** `room.open_round` קורא ל-`start`: השערים רצים בתהליכון אחרי שהסבב כבר נשמר ו-`open_round` חזר, כי חלק מהם איטי
(`collect` של `/health`). לכל השערים יחד תקרת זמן (`TIMEOUT_S`); שער שלא ענה בזמן = `UNDECIDED`.

**הכול כאן אינו זורק** אל פתיחת הסבב: מוח שנפל הוא שורת לוג, והסבב נפתח כמו בלעדיו.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from server.brain import gate_host, gate_machines, gate_round, memory, opengate, veto
from server.brain.gate_core import BLOCK, OPEN, ROUND, UNDECIDED, VERDICTS, WARN, Reading, name_of, undecided  # noqa: F401 -- API
from server.db import now_iso, SqlConnection

log = logging.getLogger("imagesrv.brain")

ACTION = "ROUND_GATE"
SHADOW_HE = "צל: רק נרשם, הסבב נפתח כמו קודם"
#: תקרת הזמן לכל השערים יחד, בשניות. **ניחוש מנומק, לא מדידה:** ה-`collect` של `/health` (B51) מריץ ss/systemctl ובדיקות HTTP לשרת
#: עצמו, ובטסטים נמדדו 13 ש'; 20 ש' משאירות לו מקום, ושער שלא ענה בזמן הוא `UNDECIDED` ולא עיכוב של השאר.
TIMEOUT_S = 20.0


@dataclass(frozen=True)
class Gate:
    id: str                 # מזהה הקטלוג = ``rule`` ב-brain_decisions
    name: str               # ``point``
    title_he: str
    read: Callable[[SqlConnection, tuple[str, ...], str], list[Reading]]
    #: #3662 (B70): השער קורא רק מה שקיים **לפני** שהסבב נפתח (לא שורת `room_rounds`), ולכן הוא נכנס לפסק של `opengate.gate`.
    pre_open: bool = False


#: הסדר הוא סדר הרישום. הוספת שער = שורה כאן, פונקציה בקובץ שלו, ופריט בקטלוג.
GATES: tuple[Gate, ...] = (
    Gate("B49", "raid", "RAID של השרת", gate_host.raid, pre_open=True),
    Gate("B60", "library_space", "מקום פנוי בספרייה", gate_host.library_space, pre_open=True),
    Gate("B51", "health", "בריאות המערכת", gate_host.system_health, pre_open=True),
    Gate("B23", "ahci", "מצב בקר ה-SATA והדרייבר", gate_machines.ahci, pre_open=True),
    Gate("B52", "boot", "אתחולים שנכשלו ולולאות", gate_machines.boot),
    Gate("B53", "pxe", "אירועי PXE ו-DHCP", gate_machines.pxe, pre_open=True),
    Gate("B54", "nic", "כרטיס הרשת, כבל ו-LLDP", gate_machines.nic, pre_open=True),
    Gate("B55", "questions", "שאלות פתוחות לאדם ותור פס הייצור", gate_round.questions),
    Gate("B56", "firmware", "סטיית קושחה והמלצת חיווט", gate_machines.firmware),
    Gate("B57", "held", "משכפל מוחזק, probe ו-F1", gate_round.held, pre_open=True),
)


def _read(gate: Gate, conn: SqlConnection, macs: tuple[str, ...], round_id: str) -> list[Reading]:
    """קריאות השער, בלי שום מצב שאינו אחד מארבעת הפסקים. שער שלא ענה = ``UNDECIDED``, בשמו."""
    try:
        got = gate.read(conn, macs, round_id)
    except Exception as exc:                                                # noqa: BLE001 -- שער שנפל אינו מפיל סבב
        log.exception("brain: the round gate %s (%s) failed", gate.id, gate.name)
        return [undecided(f"לא הצלחנו לבדוק ({gate.title_he}): {type(exc).__name__}", (f"gate={gate.id}", f"error={type(exc).__name__}"))]
    if not got:
        return [undecided(f"לא הצלחנו לבדוק ({gate.title_he}): השער לא החזיר קריאה", (f"gate={gate.id}", "empty"))]
    out = []
    for r in got:
        if not isinstance(r, Reading) or r.verdict not in VERDICTS or not str(r.reason).strip():
            out.append(undecided(f"לא הצלחנו לבדוק ({gate.title_he}): הקריאה אינה תקינה", (f"gate={gate.id}", f"got={r!r}"[:120])))
        else:
            out.append(r)
    return out


def _rows(conn: SqlConnection, round_id: str, gate: Gate, readings: list[Reading]) -> list[tuple[Reading, int | None]]:
    """רושם: כל קריאה בשם הישות שלה, ו-``OPEN`` של משכפלים מקופל לשורה אחת לשער."""
    done, folded = [], [r for r in readings if r.mac is not None and r.verdict == OPEN]
    for r in readings:
        if r in folded:
            continue
        entity = ("machine", r.mac) if r.mac is not None else ("round", round_id)
        done.append((r, memory.record(conn, mode="shadow", rule=gate.id, action=ACTION, point=gate.name, round_id=round_id,
                                      entity=entity, verdict=r.verdict, reason=r.reason, evidence=r.evidence, outcome=SHADOW_HE)))
    if folded:
        macs = sorted(r.mac for r in folded if r.mac is not None)
        one = Reading(OPEN, f"{gate.title_he}: נבדקו {len(macs)} משכפלים, ואין מה להזהיר", tuple(f"mac={m}" for m in macs))
        done.append((one, memory.record(conn, mode="shadow", rule=gate.id, action=ACTION, point=gate.name, round_id=round_id,
                                        entity=("round", round_id), verdict=OPEN, reason=one.reason, evidence=one.evidence, outcome=SHADOW_HE)))
    return done


def _read_within(gate: Gate, conn: SqlConnection, macs: tuple[str, ...], round_id: str, deadline: float) -> list[Reading]:
    """קריאת שער אחד בתהליכון משלו, עד ``deadline`` (זמן ``monotonic``). שער שלא ענה בזמן, או שלא נשאר לו זמן להתחיל, = ``UNDECIDED``.
    התהליכון של שער שנתקע ממשיך לרוץ ותוצאתו נזרקת; החיבור שלו ל-DB הוא של התהליכון (`db.Database`), לא של הרצה."""
    left = deadline - time.monotonic()
    if left <= 0:
        return [undecided(f"לא הצלחנו לבדוק ({gate.title_he}): נגמר הזמן של כל השערים לפני שהשער התחיל", (f"gate={gate.id}", "not_started"))]
    box: list[list[Reading]] = []
    worker = threading.Thread(target=lambda: box.append(_read(gate, conn, macs, round_id)), daemon=True, name=f"brain-gate-{gate.id}")
    worker.start()
    worker.join(left)
    if worker.is_alive() or not box:
        return [undecided(f"לא הצלחנו לבדוק ({gate.title_he}): השער לא ענה בזמן", (f"gate={gate.id}", "timeout"))]
    return box[0]


def run(conn: SqlConnection, round_id: str, macs, timeout_s: float | None = None) -> list[tuple[Gate, Reading]]:
    """כל השערים, לפי הסדר, עד ``timeout_s`` (ברירת מחדל `TIMEOUT_S`) לכולם יחד: קורא, רושם בצל ומחזיר מה נרשם (לטסטים).
    ``off`` = ריק, בלי לקרוא דבר. אינו זורק. **חוסם עד שנגמר**: פתיחת סבב קוראת ל-`start`, לא לכאן."""
    out: list[tuple[Gate, Reading]] = []
    try:
        if veto.mode(conn) == veto.OFF:
            return out
        deadline = time.monotonic() + (TIMEOUT_S if timeout_s is None else timeout_s)
        roster = tuple(sorted({m for m in macs if isinstance(m, str) and m}))
        for gate in GATES:
            for reading, row_id in _rows(conn, round_id, gate, _read_within(gate, conn, roster, round_id, deadline)):
                out.append((gate, reading))
                (log.warning if reading.verdict in (WARN, BLOCK) else log.info)(
                    "brain %s", json.dumps({"at": now_iso(), "mode": "shadow", "rule": gate.id, "action": ACTION, "point": gate.name,
                                            "round": round_id, "target": reading.mac or round_id, "verdict": reading.verdict,
                                            "reason": reading.reason, "enforced": False, "memory": row_id}, ensure_ascii=False))
    except Exception:                                                       # noqa: BLE001 -- הסבב נפתח גם כשהמוח נכשל
        log.exception("brain: the round gates of %s were not recorded", round_id)
    return out


def read_pre_open(conn: SqlConnection, macs, timeout_s: float | None = None) -> list[tuple[Gate, Reading]]:
    """#3662 (B70): קריאת השערים של ``pre_open`` בלבד, **לפני** שהסבב קיים (``round_id`` ריק), בלי לרשום דבר: מי שקורא (`opengate.gate`)
    מחליט ורושם פסק אחד. אותה תקרת זמן כמו `run`, ושער שלא ענה = ``UNDECIDED``. אינו בודק את `off` (זה של הקורא)."""
    deadline = time.monotonic() + (TIMEOUT_S if timeout_s is None else timeout_s)
    roster = tuple(sorted({m for m in macs if isinstance(m, str) and m}))
    return [(gate, reading) for gate in GATES if gate.pre_open for reading in _read_within(gate, conn, roster, "", deadline)]


def _job(conn: SqlConnection, round_id: str, macs: tuple[str, ...], timeout_s: float | None) -> None:
    """הגוף של תהליכון הרקע: `run`, ואחריו הפסק האחד (`opengate.record_after_open`). ``off`` ל-`run` = ריק = לא נרשם פסק."""
    opengate.record_after_open(conn, round_id, run(conn, round_id, macs, timeout_s))


_running: list[threading.Thread] = []
_running_lock = threading.Lock()


def start(conn: SqlConnection, round_id: str, macs, timeout_s: float | None = None) -> threading.Thread | None:
    """מריץ את `run` **ברקע**, בתהליכון משלו, ומחזיר מיד: פתיחת הסבב אינה מחכה לשערים (ה-`collect` של B51 איטי). ה-`conn` הוא
    `db.Database`, וכל תהליכון מקבל ממנו חיבור משלו. אינו זורק; ``None`` = התהליכון לא יצא לדרך (נרשם בלוג)."""
    try:
        worker = threading.Thread(target=_job, args=(conn, round_id, tuple(macs), timeout_s), daemon=True, name=f"brain-gates-{round_id}")
        with _running_lock:
            _running[:] = [t for t in _running if t.is_alive()]
            _running.append(worker)
        worker.start()
        return worker
    except Exception:                                                       # noqa: BLE001 -- הסבב נפתח גם כשהמוח נכשל
        log.exception("brain: the round gates of %s did not start", round_id)
        return None


def wait_idle(timeout_s: float) -> bool:
    """מחכה לתהליכוני הרקע של `start` (לטסטים). ``True`` = כולם נגמרו; ``False`` = נשאר חי אחרי ``timeout_s`` (לא "נגמר")."""
    end = time.monotonic() + timeout_s
    with _running_lock:
        pending = list(_running)
    for t in pending:
        t.join(max(end - time.monotonic(), 0))
    return not any(t.is_alive() for t in pending)
