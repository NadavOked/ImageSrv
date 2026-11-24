"""#3064: שני כללים של המוח, **בצל בלבד** -- עליית מונה ה-CRC לכל דיסק (D29, LR11), ו-K4: למי שייך כשל באימות.

**CRC (`crc_delta`).** מונה 199 של SMART מצטבר לאורך כל חיי הדיסק, ולכן הערך המוחלט אינו אומר דבר על הסבב
(#2846). רק **עלייה** מאז הסבב הקודם של אותו דיסק, לפי סידורי, מזהירה. הבסיס (`previous_crc`) הוא הסבב האחרון
**לפני** הסבב הזה שבו המונה של הסידורי נקרא באמת -- אותו כלל "נקרא" כמו הבסיס של מסך החדר (LR11,
`room_precheck._CRC_READ_VERDICTS`), ולעולם לא הסבב הזה עצמו או סבב מאוחר ממנו. המצבים:

- ``WARN`` -- המונה עלה מאז הבסיס.
- ``NO_INCREASE`` -- נקרא, ושווה לבסיס.
- ``NO_BASELINE`` -- **מצב בשם משלו**: אין סידורי, או שאין סבב קודם שבו המונה שלו נקרא. לא תקין ולא אזהרה.
- ``UNREAD`` -- המונה של הסבב הזה לא נקרא. ``crc=0`` של שער SMART שלא קרא אינו מדידה (עיקרון 5).
- ``COUNTER_RESET`` -- המונה ירד: דיסק אחר עם אותו סידורי, או מונה שאופס. אי אפשר לדעת בכמה עלה.

**K4 (`integrity`)** -- לכל יעד שהסתיים בגל (או שהגל נסגר עליו), אחד מאלה, בסדר הזה:

- ``HALF_WRITTEN`` -- הכתיבה נעצרה באמצע: אחד ממשפטי העצירה של הסוכן (`disk_failures.interrupted`), או יעד
  שעוד היה ``writing`` כשהגל נסגר. נקרא בשמו: MAC, התקן וסידורי.
- ``SOURCE_FAILURE`` -- אותה חתיכה (מחיצה, אינדקס) נכשלה באימות בשני דיסקים שונים או יותר (`SOURCE_DISKS`, לפי
  סידורי) באותו גל, ולכן באותו אימג'. הכשל של **המקור**, לא של הדיסק.
- ``DISK_FAILURE`` -- אימות שנכשל **בפועל** (רשימת חתיכות, או משפט הכשל של הסוכן `READBACK_FAILED`), בלי דיסק
  שני שמאשר אותה חתיכה.
- ``VERIFIED_OK`` -- ``done`` עם ``readback: verified``: כל החתיכות תאמו.
- ``SAMPLED_OK`` -- ``done`` עם ``readback: sampled``: החתיכות שנבחרו תאמו. "נבדק במדגם", לעולם לא "אומת"
  (סעיף 4 ב-interfaces.md), ולכן מצב משלו.
- ``COULD_NOT_VERIFY`` -- **מצב בשם משלו** (עיקרון 5): כל השאר. ``unverified``, שדה חסר, ראיה פגומה, משפט
  "האימות לא רץ", יעד שנכשל בלי ראיה מהאימות. גם כשהסוכן אמר ``done``.

**החתיכה שנכשלה (S63)** מגיעה מהסוכן מ-#3287 (`agent/lib/restore/rbchunks.sh`). הקלט של ``SOURCE_FAILURE`` הוא
השדה האופציונלי ``readback_failed_chunks`` של יעד בדיווח (סעיף 4 ב-interfaces.md). סוכן ישן אינו שולח אותו, ואז כל
כשל אימות הוא ``DISK_FAILURE``, ואין קורלציה. האינדקס אינו נגזר מטקסט חופשי: במדגם כל סוכן בוחר חתיכות לפי זרע משלו, ושני
דיסקים שלא דגמו אותה חתיכה אינם ראיה לשום דבר.

**מצבים** (המתג של המוח, ``brain_veto_mode``): ``off`` -- דבר לא נקרא ולא נרשם. כל ערך אחר -- ההחלטה נרשמת
ב-``brain_decisions`` עם ``mode=shadow``, **גם ב-enforce**: אין כאן פעולה לאכוף. לא צבע, לא סימון, לא כשל.

**השעון:** המחזור של K7 (`budget.production`, KT1: כל 10 ש'). כל מחזור קורא את הגלים הפתוחים ואת אלה שנסגרו מאז
שהשרת עלה, ורושם רק החלטה שהשתנתה. גל שנסגר נקרא פעם אחת אחרי הסגירה, ולא שוב. גלים שנסגרו לפני העלייה אינם
נקראים (`record_round` קורא גל אחד ביד).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from server.brain import memory, veto
from server.db import now_iso
from server.disks.disk_failures import interrupted
from server.rounds.room_precheck import _CRC_READ_FAIL_REASONS, _CRC_READ_VERDICTS

log = logging.getLogger("imagesrv.brain")

CRC_RULE, CRC_ACTION = "D29", "CRC_DELTA"
K4_RULE, K4_ACTION = "K4", "INTEGRITY"
WARN, NO_INCREASE, NO_BASELINE, UNREAD, COUNTER_RESET = "WARN", "NO_INCREASE", "NO_BASELINE", "UNREAD", "COUNTER_RESET"
HALF_WRITTEN, SOURCE_FAILURE, DISK_FAILURE = "HALF_WRITTEN", "SOURCE_FAILURE", "DISK_FAILURE"
VERIFIED_OK, SAMPLED_OK, COULD_NOT_VERIFY = "VERIFIED_OK", "SAMPLED_OK", "COULD_NOT_VERIFY"

#: הכרעת נדב (#3064): אותה חתיכה שנכשלה בשני דיסקים שונים = כשל מקור. לא הגדרה למשתמש.
SOURCE_DISKS = 2
#: המשפט של הסוכן לאימות שרץ ומצא אי-התאמה (`READBACK_FAIL` ב-`agent/lib/restore/readback.sh`).
#: "האימות אחרי הכתיבה לא רץ" (`READBACK_NOT_RUN`) אינו כאן: הוא ``COULD_NOT_VERIFY``.
READBACK_FAILED = "האימות אחרי הכתיבה נכשל"
#: יעד שעדיין אין לו פסק דין בגל פתוח: לא נשפט עד שהוא מסתיים או שהגל נסגר.
FINAL = ("done", "failed", "cancelled")
SHADOW_HE = "צל: רק נרשם, שום פעולה לא בוצעה"


@dataclass(frozen=True)
class Decision:
    rule: str
    action: str
    target: str                     # mac/dev/serial
    verdict: str
    reason: str
    evidence: tuple[str, ...] = ()


def count(value: object) -> int | None:
    """מספר שלם לא שלילי, או ``None``. ``bool`` אינו מונה."""
    return value if type(value) is int and value >= 0 else None


# --- CRC (D29, LR11) ------------------------------------------------------------------------------------------------


def crc_delta(serial: str | None, current: int | None, previous: int | None, *, target: str = "",
              base: str | None = None) -> Decision:
    """פונקציה טהורה. ``current``/``previous`` = ``None`` כשלא נקראו (לא 0). ``base`` = הגל של הבסיס, לראיה."""
    current, previous = count(current), count(previous)
    name = target or serial or "?"
    evidence = (f"serial={serial or '?'}", f"crc={current}", f"previous_crc={previous}", f"previous_round={base}")
    if current is None:
        verdict, why = UNREAD, "מונה ה-CRC של הסבב הזה לא נקרא"
    elif not serial:
        verdict, why = NO_BASELINE, "אין סידורי: אין לאיזה דיסק להשוות"
    elif previous is None:
        verdict, why = NO_BASELINE, "אין סבב קודם שבו המונה של הסידורי הזה נקרא"
    elif current < previous:
        verdict, why = COUNTER_RESET, f"המונה ירד מ-{previous} ל-{current}: אי אפשר לדעת בכמה עלה"
    elif current > previous:
        verdict, why = WARN, f"מונה ה-CRC עלה ב-{current - previous} מאז הסבב הקודם"
    else:
        verdict, why = NO_INCREASE, "מונה ה-CRC לא עלה מאז הסבב הקודם"
    return Decision(CRC_RULE, CRC_ACTION, name, verdict, why, evidence)


def _read(row) -> bool:
    """האם המונה של ה-disk_event נקרא באמת. אותו כלל כמו `room_precheck.crc_baseline`."""
    return (row["verdict"] in _CRC_READ_VERDICTS
            or (row["verdict"] == "fail" and row["reason"] in _CRC_READ_FAIL_REASONS)
            or (row["verdict"] == "unchecked" and row["reason"] == "no_health"))


def previous_crc(conn, serial: str | None, session_id: str) -> tuple[int, str] | None:
    """(המונה, הגל) של הסבב האחרון **לפני** ``session_id`` שבו המונה של הסידורי נקרא, או ``None``."""
    if not serial:
        return None
    rows = conn.execute(
        "SELECT e.crc, e.verdict, e.reason, e.session_id FROM disk_events e"
        " JOIN sessions old ON old.id = e.session_id JOIN sessions cur ON cur.id = ?"
        " WHERE e.serial = ? AND old.created_at < cur.created_at"
        " ORDER BY old.created_at DESC, e.updated_at DESC", (session_id, serial))
    for row in rows:
        if _read(row) and count(row["crc"]) is not None:
            return row["crc"], row["session_id"]
    return None


# --- K4 ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    mac: str
    dev: str
    serial: str | None
    state: str
    readback: str | None = None
    #: ``None`` = לא דווח; ``()`` = דווח ריק -- גם הוא אינו "אומת" בלי ``readback``.
    failed_chunks: tuple[tuple[int, int], ...] | None = None
    interrupted: bool = False
    failed_readback: bool = False   # משפט הכשל של הסוכן: האימות רץ ומצא אי-התאמה
    error: str = ""

    @property
    def name(self) -> str:
        return f"{self.mac}/{self.dev}/{self.serial or '?'}"


def integrity(targets: tuple[Target, ...]) -> list[Decision]:
    """פונקציה טהורה, לגל אחד (אימג' אחד). דיסק נספר פעם אחת לחתיכה, לפי סידורי; יעד בלי סידורי אינו נספר."""
    failures: dict[tuple[int, int], set[str]] = {}
    for t in targets:
        if t.serial and not t.interrupted:
            for key in t.failed_chunks or ():
                failures.setdefault(key, set()).add(t.serial)
    shared = {key for key, disks in failures.items() if len(disks) >= SOURCE_DISKS}
    out = []
    for t in sorted(targets, key=lambda t: t.name):
        chunks = tuple(sorted(set(t.failed_chunks or ())))
        evidence: tuple[str, ...] = (f"state={t.state}", f"readback={t.readback}", f"failed_chunks={list(chunks)}")
        common = sorted(shared.intersection(chunks))
        if t.interrupted:
            verdict, why = HALF_WRITTEN, "הכתיבה נעצרה באמצע: הדיסק כתוב בחלקו"
        elif common:
            verdict, why = SOURCE_FAILURE, "אותה חתיכה נכשלה באימות בדיסקים שונים: כשל של המקור, לא של הדיסק"
            evidence += tuple(f"p{p}/chunk{k}: {','.join(sorted(failures[p, k]))}" for p, k in common)
        elif chunks or t.failed_readback:
            verdict, why = DISK_FAILURE, "האימות נכשל, ואין דיסק אחר שנכשל באותה חתיכה"
        elif t.state == "done" and t.readback == "verified":
            verdict, why = VERIFIED_OK, "נכתב, וכל החתיכות תאמו באימות"
        elif t.state == "done" and t.readback == "sampled":
            verdict, why = SAMPLED_OK, "נכתב, והחתיכות שנבחרו במדגם תאמו (לא אומת במלואו)"
        else:
            verdict, why = COULD_NOT_VERIFY, "לא ניתן לאמת: אין ראיה חיובית לכתיבה שהושלמה ולאימות שעבר"
        if t.error:
            evidence += (f"error={t.error}",)
        out.append(Decision(K4_RULE, K4_ACTION, t.name, verdict, f"{t.name}: {why}", evidence))
    return out


def target(mac: str, raw: dict, *, closed: bool = False) -> Target | None:
    """יעד אחד מ-``targets_json``, או ``None`` כשעוד אין מה לשפוט (הגל פתוח והיעד לא הסתיים)."""
    state = raw.get("state")
    if not closed and state not in FINAL:
        return None
    readback = raw.get("readback") if isinstance(raw.get("readback"), str) else None
    chunks = None
    if "readback_failed_chunks" in raw:
        value = raw["readback_failed_chunks"]
        if isinstance(value, list) and all(isinstance(c, dict) and count(c.get("partition")) is not None
                                           and count(c.get("chunk_index")) is not None for c in value):
            chunks = tuple(sorted({(c["partition"], c["chunk_index"]) for c in value}))
        else:
            readback = "evidence_unreadable"        # ראיה פגומה אינה "אין כשל": COULD_NOT_VERIFY
    raw_error = raw.get("error")
    error = raw_error if isinstance(raw_error, str) else ""
    serial = raw.get("serial") if isinstance(raw.get("serial"), str) and raw.get("serial") else None
    return Target(mac, str(raw.get("dev") or "?"), serial, str(state), readback, chunks,
                  interrupted=interrupted(None, error) or (closed and state == "writing"),
                  failed_readback=READBACK_FAILED in error, error=error)


# --- הקריאה והרישום -----------------------------------------------------------------------------------------------


def _targets(conn, session_id: str, closed: bool) -> list[Target]:
    out = []
    for row in conn.execute("SELECT mac, targets_json FROM session_members WHERE session_id = ? ORDER BY mac",
                            (session_id,)):
        try:
            raw = json.loads(row["targets_json"] or "[]")
            if not isinstance(raw, list) or not all(isinstance(t, dict) for t in raw):
                raise ValueError("targets_json is not a list of targets")
        except (TypeError, ValueError):
            # דיווח שלא נקרא אינו "אין יעדים": יעד אחד בשם המכונה, שלא ניתן לאמת
            raw = [{"dev": "?", "state": "failed", "error": "targets_json לא נקרא"}]
        if not raw and closed:
            raw = [{"dev": "?", "state": "failed", "error": "הגל נסגר בלי שום יעד בדיווח"}]
        out += [t for t in (target(row["mac"], item, closed=closed) for item in raw) if t is not None]
    return out


def record_round(conn, session_id: str) -> list[Decision]:
    """גל אחד: קורא, מחליט, ורושם כל החלטה **שהשתנתה** מהאחרונה של אותו יעד (`memory.record`). נקרא שוב כשדיסק
    נוסף מסתיים: כשל שנרשם כ-``DISK_FAILURE`` מקבל ``SOURCE_FAILURE`` כשדיסק שני מאשר אותה חתיכה, והקודם נשאר
    בהיסטוריה."""
    if veto.mode(conn) == veto.OFF:
        return []
    session = conn.execute("SELECT image_id, state FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None:
        return []
    decisions, entities = [], {}
    for row in conn.execute("SELECT * FROM disk_events WHERE session_id = ? ORDER BY mac, disk", (session_id,)):
        name = f"{row['mac']}/{row['disk']}/{row['serial'] or '?'}"
        base = previous_crc(conn, row["serial"], session_id)
        decisions.append(crc_delta(row["serial"], row["crc"] if _read(row) else None, base and base[0],
                                   target=name, base=base and base[1]))
        entities[name] = ("disk", row["serial"]) if row["serial"] else ("machine", row["mac"])
    targets = _targets(conn, session_id, session["state"] in ("closed", "failed"))
    entities.update({t.name: ("disk", t.serial) if t.serial else ("machine", t.mac) for t in targets})
    decisions += integrity(tuple(targets))
    latest: dict[tuple, dict] = {}
    for p in memory.decisions(conn, round_id=session_id, limit=100_000):        # החדשה ראשונה
        latest.setdefault((p["rule"], p["action"], p["point"]), p)
    for d in decisions:
        evidence = (*d.evidence, f"image_id={session['image_id']}")
        prior = latest.get((d.rule, d.action, d.target))
        if prior and (prior["verdict"], prior["reason"], prior["evidence"]) == (d.verdict, d.reason, list(evidence)):
            continue
        memory.record(conn, mode="shadow", rule=d.rule, action=d.action, point=d.target, round_id=session_id,
                      entity=entities[d.target], verdict=d.verdict, reason=d.reason, evidence=evidence,
                      outcome=SHADOW_HE)
        (log.warning if d.verdict in (WARN, SOURCE_FAILURE, DISK_FAILURE, HALF_WRITTEN) else log.info)(
            "brain %s", json.dumps({"at": now_iso(), "mode": "shadow", "rule": d.rule, "action": d.action,
                                    "round": session_id, "target": d.target, "verdict": d.verdict,
                                    "reason": d.reason, "evidence": list(evidence), "enforced": False},
                                   ensure_ascii=False))
    return decisions


class Observer:
    """רץ בכל מחזור של K7: הגלים הפתוחים, ואלה שנסגרו מאז שהשרת עלה -- פעם אחת אחרי הסגירה."""

    def __init__(self, conn):
        self.conn = conn
        self.started = now_iso()
        self.final: set[str] = set()

    def tick(self) -> None:
        """לעולם אינו זורק: מוח שנפל הוא שורת לוג, לא מחזור תקציב שנפל."""
        try:
            if veto.mode(self.conn) == veto.OFF:
                return
            rows = self.conn.execute("SELECT id, state FROM sessions WHERE state IN ('open', 'running')"
                                     " OR closed_at >= ? ORDER BY created_at, id", (self.started,)).fetchall()
        except Exception:                       # noqa: BLE001
            log.exception("brain: the rounds for CRC/K4 could not be read")
            return
        for row in rows:
            if row["id"] in self.final:
                continue
            try:
                record_round(self.conn, row["id"])
                if row["state"] in ("closed", "failed"):
                    self.final.add(row["id"])
            except Exception:                   # noqa: BLE001 — הגל הבא עדיין נקרא
                log.exception("brain: CRC/K4 of %s could not be read", row["id"])
