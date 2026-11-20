"""#3064, K3, קבוצה ד' (#3475): שערי הסבב והמפעיל לפני פתיחת סבב -- שאלות פתוחות ותור פס הייצור (B55), מוחזק, probe ו-F1 (B57).

הכללים (פסקים, צל בלבד, סיבה מהקוד הקיים) ב-`server/brain/gates.py`. כל פונקציה: ``(conn, macs, round_id) -> list[Reading]``.
הפסק החמור והקיפול לקריאה אחת הם של `gate_host.fold`. ייבואים בתוך הפונקציות: `gates` נטען מ-`room`, ו-`interrupt` מייבא אותו חזרה.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from server.brain import gate_core
from server.brain.gate_core import OPEN, UNDECIDED, WARN, Reading, undecided
from server.brain.gate_host import fold

log = logging.getLogger("imagesrv.brain")


def _count(conn: sqlite3.Connection, sql: str, *args) -> int:
    return conn.execute(sql, args).fetchone()[0]


def questions(conn: sqlite3.Connection, macs: tuple[str, ...], round_id: str) -> list[Reading]:
    """B55: שאלה פתוחה לאדם על הסבב (`round_interrupts`, `interrupt.view`), ותור פס הייצור (`prodline`).

    **מה אפשר לראות ברגע הזה.** השער רץ מיד אחרי שהסבב נוצר: שאלה נולדת רק מ-hello מצטרף של משכפל שכבר בתוך הזרם
    (`interrupt.on_rejoin`), ולכן מבנית אין עוד שאלה על הסבב הזה (אלא ב-race בין פתיחת הגל ל-commit, ואז היא ``WARN`` עם
    הטקסט שהמסך מציג). שורות של סבב **אחר** נשארות בטבלה עד ה-hello הראשון (`ledger.opened` מנקה סבב שאינו פעיל), אינן
    של הסבב הזה, ונרשמות רק בראיות. פס ייצור פתוח עם תור הוא המצב הצפוי שלו (`target_drives` > המגירות), לא אזהרה.
    לכן הפסק כאן הוא ``OPEN`` על ראיה חיובית (נקראו 0 שאלות ושורת הסבב עצמה), והערך האמיתי של השער הוא בנקודה מאוחרת יותר,
    אחרי שהגל נפתח -- שאינה בנויה (#3475).
    """
    from server.rounds import interrupt, prodline        # noqa: PLC0415

    round_row = conn.execute("SELECT * FROM room_rounds WHERE id = ?", (round_id,)).fetchone()
    if round_row is None:
        return [undecided(f"הסבב {round_id} לא נמצא -- השאלות הפתוחות לא נבדקו", ("room_rounds=0",))]
    asked = interrupt.asks(conn, round_id)
    others = _count(conn, "SELECT COUNT(*) FROM round_interrupts WHERE round_id != ?", round_id)
    line = prodline.line_of(conn, round_id)
    queued = _count(conn, "SELECT COUNT(*) FROM prodline_disks WHERE round_id = ? AND state = ?", round_id, prodline.QUEUED)
    seen = (f"asks={len(asked)}", f"stale_asks_of_other_rounds={others}", f"line={'yes' if line else 'no'}", f"queued={queued}")
    if asked:
        view = interrupt.view(conn, round_row)
        if view is None:
            return [undecided("יש שאלה על הסבב, אבל המסך לא הצליח להציג אותה", seen)]
        return [Reading(WARN, view["text_he"], seen)]
    reason = "אין שאלה פתוחה לאדם על הסבב"
    if line is not None:
        reason += f" · פס ייצור: {queued} בתור, יעד {line['target']} דיסקים"
    return [Reading(OPEN, reason, seen)]


def _held_part(conn: sqlite3.Connection, mac: str, name: str) -> tuple[str, str, str]:
    from server.agent import held as agent_held          # noqa: PLC0415

    row = conn.execute("SELECT held_at, held_json FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    if row is None:
        return UNDECIDED, "מצב ההחזקה לא נקרא: אין למכונה שורה ברשימת המכונות שדיווחו", "held=unread"
    view = agent_held.view(row["held_at"], row["held_json"])
    if view is None:
        return OPEN, "לא מוחזק", "held=no"
    # הנוסח של `room_precheck.missing` ("held"), עם השם כמו שהמפעיל קורא אותו
    problems = [f"{p['disk']}:{p['level']}:{p['reason']}" for p in view["problems"]]
    return WARN, f"{name} דלוק ומחכה ללחיצה על 'כבה' — יש בו דיסק עם בעיה מהסבב הקודם", "held=yes " + ";".join(problems)


def _probe_part(conn: sqlite3.Connection, mac: str, name: str) -> list[tuple[str, str, str]]:
    from server.agent import probe                       # noqa: PLC0415

    got = probe.latest(conn, mac)
    if got is None:
        return [(UNDECIDED, f"{name}: ה-probe לא נקרא -- המשכפל מעולם לא דיווח", "probe=none")]
    found = probe.verdicts(got["probe"])
    if found:
        return [(WARN, f"{name}: {v['text_he']}", f"probe:{v['key']}={v['level']}") for v in found]
    # רשימה ריקה אומרת "לא נמצאה בעיה במה שנמדד" (`probe.verdicts`): ראיה רק אם נמדד משהו ממה שהשערים קוראים
    p = got["probe"]
    measured = any(isinstance(p.get(k), dict) and "error" not in p[k] for k in ("power", "rtc", "pstore", "netprobe")) \
        or any(isinstance(d, dict) and isinstance(d.get("nvme_smart"), dict) and "error" not in d["nvme_smart"]
               for d in p.get("disks") or []) \
        or any(isinstance(n, dict) and (isinstance(n.get("stats"), dict) or isinstance(n.get("ip_conflict"), dict))
               for n in p.get("nic") or [])
    if not measured:
        return [(UNDECIDED, f"{name}: ה-probe נקרא, אבל לא נמדד בו דבר שהשער בודק", "probe=nothing_measured")]
    return [(OPEN, "ה-probe נקרא ולא נמצאה בו בעיה במה שנמדד", f"probe=ok sampled_at={got['sampled_at']}")]


def _f1_part(conn: sqlite3.Connection, mac: str, name: str, marks: dict | None) -> list[tuple[str, str, str]]:
    if marks is None:
        return [(UNDECIDED, f"{name}: סימוני ה-F1 של הדיסקים לא נקראו", "f1=unread")]
    row = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    disks = json.loads(row["disks_json"]) if row is not None and row["disks_json"] else None
    if not isinstance(disks, list):
        return [(UNDECIDED, f"{name}: הדיסקים שלו לא נקראו, ולכן אי אפשר לבדוק סימוני F1", "f1=no_disks_read")]
    hits = [d for d in disks if isinstance(d, dict) and d.get("serial") in marks]
    if not hits:
        return [(OPEN, "אין סימון F1 על דיסק שלו", f"f1=none disks={len(disks)}")]
    return [(WARN, f"{name}: נצפה F1 בהדלקה בדיסק {d.get('model') or '?'} ({d['serial']})"
                   f" -- סומן {marks[d['serial']]['date'] or '?'}", f"f1:{d['serial']}={marks[d['serial']]['source']}")
            for d in hits]


def held(conn: sqlite3.Connection, macs: tuple[str, ...], round_id: str) -> list[Reading]:
    """B57: לכל משכפל: מוחזק (`held`), ה-probe שלו (`probe.verdicts`), וסימוני F1 על הדיסקים שלו (`disk_f1_marks`).

    - מוחזק (דלוק ומחכה ל"כבה", דיסק עם בעיה), פריט ב-``probe.verdicts`` (``warn`` או ``err``), או דיסק שלו שסומן "נצפה F1"
      = ``WARN``. ``BLOCK`` אין: אף אחד מהם אינו מוכיח שהסבב חסר תוחלת (משכפל אחד אינו כל הסבב).
    - אין שורת מכונה, אין probe, ה-probe נקרא ולא נמדד בו דבר שנקרא כאן, הדיסקים או סימוני ה-F1 לא נקראו, או שאחד המקורות זרק =
      ``UNDECIDED`` **למשכפל הזה** (וכל מקור נקרא לחוד, כך ש-``WARN`` של אחד אינו נבלע בכשל של אחר). רשימת ``verdicts`` ריקה אינה "תקין": היא "לא נמצאה בעיה במה שנמדד".
    - ``OPEN`` רק כשהשלושה נקראו ותקינים (`gates` מקפל אותם לשורה אחת).
    """
    from server.disks import f1_marks                    # noqa: PLC0415

    marks = f1_marks.all_marks(conn)
    out = []
    for mac in macs:
        name = gate_core.name_of(conn, mac)
        parts = []
        # כל מקור לחוד: שכשל באחד לא יסתיר את מה שהשני מצא (`WARN` של מוחזק לא נבלע בגלל JSON פגום של הדיסקים)
        for key, label, read in (("held", "מצב ההחזקה", lambda: [_held_part(conn, mac, name)]),
                                 ("probe", "ה-probe", lambda: _probe_part(conn, mac, name)),
                                 ("f1", "סימוני ה-F1", lambda: _f1_part(conn, mac, name, marks))):
            try:
                parts += read()
            except Exception as exc:                                            # noqa: BLE001 -- נרשם כ-UNDECIDED למשכפל הזה
                log.exception("brain: the %s of %s could not be read for the held gate", key, mac)
                parts.append((UNDECIDED, f"{name}: {label} לא נקרא ({type(exc).__name__})", f"{key}=error"))
        got = fold(parts, mac)
        out.append(got if got.verdict != OPEN else Reading(OPEN, f"{name}: לא מוחזק, ה-probe וסימוני ה-F1 נקראו ותקינים",
                                                           got.evidence, mac))
    return out
