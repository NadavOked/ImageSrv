"""#3654 (אפיק #3653, הכרעת נדב 18/11): הערוץ האחיד שבו המוח שואל את המשתמש, והמשתמש מכריע.

**יש שני מחליטים בלבד: המוח, והמשתמש בדברים מסוימים.** עד כאן כל שאלה למשתמש נולדה בנפרד -- מסך, תקרת המתנה וברירת
מחדל משלה, ואף אחת לא נרשמה ב-``brain_decisions``. כאן יש מנגנון אחד: כלל של המוח מגיש ``Spec`` (נושא, אפשרויות,
ברירת המחדל הבטוחה, תקרת המתנה, מי רשאי לענות), השרת מציג אותו בקונסולה ובמסך של המכונה, והתשובה חוזרת ונרשמת.

**מה הערוץ מבטיח** (עיקרונות 1, 5 ו-7):

- **המוח מציע, לא פוסל.** ``open_ask`` רושם שורת ``ASK`` ב-``brain_decisions`` (``ASKED``), ושורה שנייה כשהשאלה נסגרת
  (``ANSWERED`` / ``EXPIRED`` / ``CANCELLED``), עם מי ענה ומתי.
- **פקיעה אינה תשובה.** שאלה שאיש לא ענה עליה עוברת ל-``expired``, נרשמת ``EXPIRED``, ומחזירה את ברירת המחדל הבטוחה
  (``resolution``: ``by_user=False``). הערוץ **לעולם** לא מקפל "אף אחד לא ענה" ל"המשתמש אישר".
- **ברירת המחדל הבטוחה אינה הרסנית, בהגדרה.** ``Spec`` שבו היא הרסנית נדחה בבנייה; ו-``resolution`` מחזיר אפשרות הרסנית
  **רק** כשהמשתמש בחר בה בעצמו. אין טיימר שמבצע לבד פעולה הרסנית.
- **אין הקלדת מילה לאישור** (הכרעת נדב, מוקאפי 10/10): התשובה היא אפשרות אחת מרשימה סגורה, ובמסכים -- כפתור.
- **שאלה פתוחה שורדת הפעלה מחדש של השרת.** המצב נשמר ב-``brain_asks`` (לא בזיכרון), והחותמת היא של השרת. שורת
  ה-``ASK`` הפותחת נרשמת עם ``outcome`` משלה, כדי ש-``memory`` לא יסגור אותה כ"השרת הופעל מחדש" כשהשאלה עוד חיה.
- **לא הצלחנו לשאול = ברירת המחדל.** ``open_ask`` מחזיר ``None`` כשהכתיבה נכשלה (נרשם בלוג, לא נזרק): הקורא אינו
  מחכה לתשובה ופועל כמו כשאין תשובה.

**מה הערוץ אינו עושה:** אינו מעביר אף שאלה קיימת (``interrupt``, ``drawergate``, ``shrinkoffer``, ``bootorphans``, ``hold``,
``lcraw`` ממשיכות כמו קודם -- זו עבודת הארבעה הבאים באפיק), ואינו מחליט מתי לשאול: זו החלטה של הכלל שקורא ל-``open_ask``.
``mode`` נרשם כמות שהוא (``shadow`` / ``enforce``); מי שמתכוון להציג שאלה רק ב-enforce אינו קורא ל-``open_ask`` בצל.

**מי רשאי לענות:** ``admin`` (קונסולה) ו-``machine`` (מי שיושב ליד המסך של המכונה ששייכת לשאלה; אין שם משתמש מחובר,
והשרת מזהה את המכונה לפי ה-MAC, כמו ב-``shrink-note``). משתמש הפצה אינו נכנס לקונסולה (#1073).

מקור התנהגות לקונסולה ולסוכן: ``view`` -- אותו טקסט בכל מסך. החוזה ב-``docs/architecture/interfaces.md`` סעיף 50.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from server.brain import memory
from server.db import _settle, _write_lock, writing

log = logging.getLogger("imagesrv.brain")

#: מזהה הערוץ עצמו בקטלוג (#3321: גם הערוץ רשום כהחלטה).
CHANNEL = "K14"
ADMIN, MACHINE = "admin", "machine"
ROLES = (ADMIN, MACHINE)
ROLE_HE = {ADMIN: "מנהל", MACHINE: "המסך של המכונה"}
OPEN, ANSWERED, EXPIRED, CANCELLED = "open", "answered", "expired", "cancelled"
STATE_HE = {OPEN: "פתוחה", ANSWERED: "נענתה", EXPIRED: "פקעה בלי תשובה", CANCELLED: "בוטלה"}
#: תקרת המתנה: דקה עד יממה. הערכה: מתחת לדקה אדם לא מספיק לקרוא, ומעל יממה השאלה כבר לא על מה שקורה עכשיו
#: (תקרת `interrupt` -- 240 דקות -- בתוך הטווח).
MIN_TTL_S, MAX_TTL_S = 60, 24 * 3600
#: מכסה לאפשרויות, כדי שכל מסך (גם הגואי: `MAX_ASK_ROWS`) יצייר את כולן ולא יחתוך בשקט.
MAX_OPTIONS = 8
#: מוקאפ מאושר 18/11 (#3654): תווית עד 36 תווים -- בשורה אחת בחלון הצר, בלי חיתוך בשום מסך. הפועל של הכפתור האדום
#: ("מחיקת המידע") קצר ממנה, וההשלכה ("המידע הישן יימחק...") בשורה אחת מתחת לתווית.
MAX_LABEL, MAX_VERB, MAX_TEXT = 36, 24, 120
#: יותר מחמש אפשרויות מוצגות בשתי עמודות (מוקאפ n8; נקבע כאן פעם אחת, וכל מסך קורא `columns`).
TWO_COLUMNS_FROM = 6
#: ההיסטוריה שהשרת שומר ומציג: שלושים יום (הכרעת נדב 18/11). שאלה שנסגרה לפני כן נמחקת; ההחלטה נשארת ב-`brain_decisions`.
HISTORY_DAYS = 30
#: תקרת שורות בתשובת ההיסטוריה (30 יום של שאלות; השאר נאמר ב-``total``).
HISTORY_LIMIT = 500
#: שאלה שנסגרה נשארת בתשובה למכונה כל כך הרבה זמן, כדי שמסך שעדיין מחכה ילמד שהיא נענתה/פקעה (`for_machine`).
RECENT_CLOSED_S = 900
_ID = re.compile(r"[a-z][a-z0-9_]{0,31}")
_POINT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}")


class AskError(Exception):
    """סירוב בשם: ``code`` יציב לקוד, ``status`` ל-HTTP, וההודעה בעברית למפעיל."""

    def __init__(self, code: str, status: int, message_he: str):
        super().__init__(message_he)
        self.code, self.status, self.message_he = code, status, message_he


@dataclass(frozen=True)
class Option:
    """אפשרות אחת. ``verb_he`` = מה כתוב על הכפתור כשהיא נבחרה ("מחיקת המידע"): חובה באפשרות הרסנית (הכפתור האדום אומר
    מה הוא עושה), ובאחרות ריק = "אישור". ``consequence_he`` = ההשלכה שמוצגת כשבחרו בה (הרסנית: חובה). ``outcome_he`` = מה
    קורה אחרי שנבחרה ("הדיסק לא ייכתב"), לשורת "נרשמה" בגואי; ריק = לא נאמר."""

    id: str
    label_he: str
    destructive: bool = False
    verb_he: str = ""
    consequence_he: str = ""
    outcome_he: str = ""


@dataclass(frozen=True)
class Spec:
    """מה שכלל של המוח מגיש: השאלה, האפשרויות, מה קורה כשאין תשובה, וכמה מחכים. נבדק בבנייה -- הגדרה פגומה אינה
    שאלה בחצי: ``ValueError`` בזמן שהכלל נכתב, לא מול מפעיל."""

    point: str
    title_he: str
    options: tuple[Option, ...]
    safe_default: str
    ttl_s: int
    who: tuple[str, ...] = ROLES

    def __post_init__(self) -> None:
        if not _POINT.fullmatch(self.point):
            raise ValueError(f"point {self.point!r}: a catalog id such as A11")
        if not self.title_he.strip():
            raise ValueError("a question has a title")
        ids = [o.id for o in self.options]
        if not 2 <= len(ids) <= MAX_OPTIONS or len(set(ids)) != len(ids) or not all(_ID.fullmatch(i) for i in ids):
            raise ValueError(f"options: 2-{MAX_OPTIONS} distinct ids [a-z][a-z0-9_]*, got {ids}")
        if not all(o.label_he.strip() for o in self.options):
            raise ValueError("every option has a label")
        for o in self.options:
            if len(o.label_he) > MAX_LABEL:
                raise ValueError(f"option {o.id!r}: a label is up to {MAX_LABEL} characters, got {len(o.label_he)}")
            if len(o.verb_he) > MAX_VERB or len(o.consequence_he) > MAX_TEXT or len(o.outcome_he) > MAX_TEXT:
                raise ValueError(f"option {o.id!r}: the verb is up to {MAX_VERB} characters, the rest up to {MAX_TEXT}")
            if o.destructive and not (o.verb_he.strip() and o.consequence_he.strip()):
                raise ValueError(f"option {o.id!r} is destructive: its button names the act (verb_he) and the screen says "
                                 "what it costs (consequence_he)")
        default = {o.id: o for o in self.options}.get(self.safe_default)
        if default is None:
            raise ValueError(f"safe_default {self.safe_default!r} is not one of the options {ids}")
        if default.destructive:
            raise ValueError(f"safe_default {self.safe_default!r} is destructive: no answer may never do it")
        if isinstance(self.ttl_s, bool) or not isinstance(self.ttl_s, int) or not MIN_TTL_S <= self.ttl_s <= MAX_TTL_S:
            raise ValueError(f"ttl_s must be {MIN_TTL_S}-{MAX_TTL_S} seconds, got {self.ttl_s!r}")
        if not self.who or not set(self.who) <= set(ROLES):
            raise ValueError(f"who must be a non-empty subset of {ROLES}, got {self.who!r}")


@dataclass(frozen=True)
class Resolution:
    """מה הכלל שמחכה לשאלה עושה עכשיו. ``option`` = ``None`` כל עוד פתוחה; ``by_user`` רק ל-``answered``."""

    state: str
    option: str | None
    by_user: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _write(conn: sqlite3.Connection, sql: str, params) -> sqlite3.Cursor:
    _settle(conn)
    with _write_lock, writing(conn):
        return conn.execute(sql, tuple(params))


def _update(conn: sqlite3.Connection, sql: str, params) -> bool:
    """UPDATE מותנה שמסתיים ב-commit בכל מסלול: ``True`` = שורה אחת השתנתה."""
    return _write(conn, sql, params).rowcount == 1


def _minutes(seconds: int) -> str:
    return f"{max(seconds // 60, 1)} דקות"


# --- פתיחה --------------------------------------------------------------------------------------------------------


def open_ask(conn: sqlite3.Connection, spec: Spec, *, entity: tuple[str, str] | None = None, round_id: str | None = None,
             detail_he: str = "", mode: str = "shadow", now: datetime | None = None) -> int | None:
    """שאלה פתוחה, ומספרה. שאלה פתוחה אחת לכל (נקודה, ישות, סבב): קריאה חוזרת מחזירה את הקיימת ולא פותחת שנייה.
    ``None`` = השאלה לא נפתחה (הכתיבה נכשלה, נרשם בלוג): הקורא פועל כמו כשאין תשובה."""
    try:
        if mode not in ("shadow", "enforce"):
            raise ValueError(f"mode {mode!r}")
        if entity is not None and entity[0] not in memory.KINDS:
            raise ValueError(f"unknown entity kind {entity[0]!r}")
        now = now or _utcnow()
        expire_due(conn, now)             # שאלה ישנה שפקעה לא תיחשב "פתוחה" ולא תחסום את החדשה
        kind, key = entity if entity else (None, None)
        dedupe = "|".join((spec.point, kind or "", key or "", round_id or ""))
        try:
            cur = _write(conn, "INSERT INTO brain_asks (point, dedupe, title_he, detail_he, options_json, safe_default,"
                         " who_json, mode, entity_kind, entity_key, round_id, created_at, expires_at, state)"
                         " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (spec.point, dedupe, spec.title_he, detail_he, json.dumps([asdict(o) for o in spec.options], ensure_ascii=False), spec.safe_default, json.dumps(list(spec.who)), mode, kind, key,
                          round_id, _stamp(now), _stamp(now + timedelta(seconds=spec.ttl_s)), OPEN))
        except sqlite3.IntegrityError:    # כבר פתוחה: אותה שאלה, לא עוד אחת
            row = conn.execute("SELECT id FROM brain_asks WHERE dedupe = ? AND state = 'open'", (dedupe,)).fetchone()
            return row["id"] if row else None
        ask_id = cur.lastrowid
        default_he = next(o.label_he for o in spec.options if o.id == spec.safe_default)
        decision = memory.record(
            conn, mode=mode, rule=spec.point, action="ASK", verdict="ASKED", round_id=round_id, entity=entity,
            reason=f"{spec.title_he}. בלי תשובה תוך {_minutes(spec.ttl_s)}: «{default_he}» (ברירת המחדל הבטוחה)",
            evidence=[ask_id, [o.id for o in spec.options]], outcome="הוגשה למשתמש; ממתינה לתשובה", at=_stamp(now))
        _update(conn, "UPDATE brain_asks SET opened_decision = ? WHERE id = ?", (decision, ask_id))
        return ask_id
    except Exception:                     # noqa: BLE001 — שאלה שלא נפתחה אינה מפילה את הכלל ששאל
        log.exception("brain: the question %s was not opened", spec.point)
        return None


# --- סגירה: תשובה, פקיעה, ביטול ------------------------------------------------------------------------------------


def _row(conn: sqlite3.Connection, ask_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM brain_asks WHERE id = ?", (ask_id,)).fetchone()


def _label(row: sqlite3.Row, option: str | None) -> str:
    for o in json.loads(row["options_json"]):
        if o["id"] == option:
            return o["label_he"]
    return str(option)


def _entity(row: sqlite3.Row) -> tuple[str, str] | None:
    return (row["entity_kind"], row["entity_key"]) if row["entity_kind"] else None


def _close_row(conn: sqlite3.Connection, row: sqlite3.Row, *, verdict: str, reason: str, outcome: str, at: str) -> None:
    decision = memory.record(conn, mode=row["mode"], rule=row["point"], action="ASK", verdict=verdict, reason=reason,
                             evidence=[row["id"], row["opened_decision"]], round_id=row["round_id"],
                             entity=_entity(row), outcome=outcome, at=at)
    _update(conn, "UPDATE brain_asks SET closed_decision = ? WHERE id = ?", (decision, row["id"]))


def answer(conn: sqlite3.Connection, ask_id: int, option: str, *, by: str, role: str,
           now: datetime | None = None, only_entity: tuple[str, str] | None = None) -> dict:
    """תשובה של אדם. ``AskError``: ``unknown`` 404, ``closed`` 409 (נענתה, פקעה או בוטלה -- תשובה מאוחרת אינה נקלטת),
    ``forbidden`` 403, ``bad_option`` 400. התשובה היא אפשרות מהרשימה בלבד: אין טקסט חופשי ואין "הקלד שם".
    ``only_entity``: מכונה עונה רק על שאלה על עצמה -- שאלה על ישות אחרת היא ``forbidden``, לא ``unknown``."""
    now = now or _utcnow()
    expire_due(conn, now)
    row = _row(conn, ask_id)
    if row is None:
        raise AskError("unknown", 404, "אין שאלה כזו")
    if role not in json.loads(row["who_json"]) or (only_entity is not None and _entity(row) != only_entity):
        raise AskError("forbidden", 403, "השאלה הזו אינה מיועדת למי ששלח את התשובה")   # לפני "נסגרה": מי שאינו שלה לא לומד את מצבה
    if row["state"] != OPEN:
        raise AskError("closed", 409, f"השאלה כבר {STATE_HE[row['state']]}")
    if option not in {o["id"] for o in json.loads(row["options_json"])}:
        raise AskError("bad_option", 400, "האפשרות אינה אחת מאלה שהשאלה הציעה")
    stamp = _stamp(now)
    if not _update(conn, "UPDATE brain_asks SET state = ?, answer = ?, answered_by = ?, answered_role = ?,"
                      " closed_at = ? WHERE id = ? AND state = 'open' AND julianday(expires_at) > julianday(?)",
                      (ANSWERED, option, by, role, stamp, ask_id, stamp)):
        raise AskError("closed", 409, "השאלה כבר נסגרה")        # מישהו אחר ענה, או שהיא פקעה, בינתיים
    row = _row(conn, ask_id)
    label = _label(row, option)
    _close_row(conn, row, verdict="ANSWERED", at=stamp, outcome=f"{by} ענה: {label}",
               reason=f"{row['title_he']}. {by} ({ROLE_HE[role]}) ענה: «{label}»")
    return view(conn, _row(conn, ask_id), now)


def expire_due(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """שאלות פתוחות שהגיע זמנן: ``expired``, וברירת המחדל הבטוחה. נרשם ``EXPIRED`` -- **לא** תשובה. מחזיר כמה נסגרו;
    לעולם אינו זורק (הלולאה שקוראת לו ממשיכה). נקרא מהשעון של השרת, ובנוסף בכל פתיחה, תשובה וקריאה."""
    try:
        now = now or _utcnow()
        stamp = _stamp(now)
        due = conn.execute("SELECT * FROM brain_asks WHERE state = 'open' AND julianday(expires_at) <= julianday(?)",
                           (stamp,)).fetchall()
        closed = 0
        for row in due:
            if not _update(conn, "UPDATE brain_asks SET state = ?, closed_at = ? WHERE id = ? AND state = 'open'",
                              (EXPIRED, stamp, row["id"])):
                continue
            closed += 1
            usable = next((o for o in json.loads(row["options_json"]) if o["id"] == row["safe_default"]
                           and not o["destructive"]), None)
            if usable is None:            # שורה פגומה: אין ברירת מחדל בטוחה שאפשר לבצע, ואומרים את זה
                _close_row(conn, row, verdict="EXPIRED", at=stamp, outcome="לא נענתה; ברירת המחדל השמורה אינה תקינה, "
                           "ושום פעולה לא בוצעה", reason=f"{row['title_he']}. לא נענתה, וברירת המחדל השמורה אינה תקינה: "
                           "שום פעולה לא בוצעה. זו פקיעה, לא תשובה של משתמש")
                continue
            default = usable["label_he"]
            _close_row(conn, row, verdict="EXPIRED", at=stamp, outcome=f"לא נענתה; בוצעה ברירת המחדל הבטוחה: {default}",
                       reason=f"{row['title_he']}. לא נענתה תוך {_minutes(_ttl(row))}: «{default}» "
                              f"(ברירת המחדל הבטוחה). זו פקיעה, לא תשובה של משתמש")
        prune_history(conn, now)
        return closed
    except Exception:                     # noqa: BLE001
        log.exception("brain: expiring the open questions failed")
        return 0


def _ttl(row: sqlite3.Row) -> int:
    try:
        return int((datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])).total_seconds())
    except (TypeError, ValueError):
        return 0


def cancel(conn: sqlite3.Connection, ask_id: int, why_he: str, now: datetime | None = None) -> bool:
    """הכלל ששאל מושך את השאלה (המצב השתנה, והיא כבר לא רלוונטית). ברירת המחדל הבטוחה; נרשם ``CANCELLED``.
    ``False`` = לא הייתה פתוחה."""
    stamp = _stamp(now or _utcnow())
    if not _update(conn, "UPDATE brain_asks SET state = ?, closed_at = ? WHERE id = ? AND state = 'open'",
                      (CANCELLED, stamp, ask_id)):
        return False
    row = _row(conn, ask_id)
    _close_row(conn, row, verdict="CANCELLED", at=stamp, outcome=f"בוטלה: {why_he}",
               reason=f"{row['title_he']}. בוטלה ({why_he}): «{_label(row, row['safe_default'])}» (ברירת המחדל הבטוחה)")
    return True


# --- מה הכלל עושה, ומה המסכים מציגים ---------------------------------------------------------------------------------


def resolution(conn: sqlite3.Connection, ask_id: int, now: datetime | None = None) -> Resolution | None:
    """מה לעשות עם השאלה: ``answered`` -> מה שהמשתמש בחר; ``expired``/``cancelled`` -> ברירת המחדל הבטוחה, ו-
    ``by_user=False``; ``open`` -> ``option=None`` (מחכים). ``None`` = אין שאלה כזו (לא הצלחנו לבדוק: אין פעולה).
    ברירת מחדל שנשמרה הרסנית או לא מוכרת (שורה פגומה) אינה מבוצעת: ``option=None``."""
    expire_due(conn, now)
    row = _row(conn, ask_id)
    if row is None:
        return None
    if row["state"] == OPEN:
        return Resolution(OPEN, None, False)
    if row["state"] == ANSWERED:
        return Resolution(ANSWERED, row["answer"], True)
    default = next((o for o in json.loads(row["options_json"]) if o["id"] == row["safe_default"]), None)
    if default is None or default["destructive"]:
        log.error("brain: ask %s has an unusable safe default %r -- nothing is performed", ask_id, row["safe_default"])
        return Resolution(row["state"], None, False)
    return Resolution(row["state"], default["id"], False)


def view(conn: sqlite3.Connection, row: sqlite3.Row, now: datetime | None = None, *, closed_outcome: str | None = None) -> dict:
    """השורה כפי שהקונסולה והסוכן קוראים אותה -- אותו טקסט בכל מסך. ``expires_in_s`` רק לשאלה פתוחה."""
    now = now or _utcnow()
    out = {k: row[k] for k in ("id", "point", "title_he", "detail_he", "safe_default", "mode", "round_id", "created_at",
                              "expires_at", "state", "answer", "answered_by", "answered_role", "closed_at")}
    #: שורה שנכתבה לפני שדות הפועל וההשלכה (#3654, מוקאפ 18/11) -- שדה חסר = ריק, לא שגיאה.
    out["options"] = [{"verb_he": "", "consequence_he": "", "outcome_he": "", **o} for o in json.loads(row["options_json"])]
    out["columns"] = 2 if len(out["options"]) >= TWO_COLUMNS_FROM else 1
    out["ttl_s"] = _ttl(row)
    out["closed_note_he"] = _why(row, closed_outcome)
    out["who"] = json.loads(row["who_json"])
    out["entity"] = {"kind": row["entity_kind"], "key": row["entity_key"]} if row["entity_kind"] else None
    out["safe_default_he"] = _label(row, row["safe_default"])
    out["answer_he"] = _label(row, row["answer"]) if row["answer"] else None
    out["state_he"] = STATE_HE[row["state"]]
    try:
        left = (datetime.fromisoformat(row["expires_at"]) - now).total_seconds()
    except (TypeError, ValueError):
        left = None
    out["expires_in_s"] = max(int(left), 0) if row["state"] == OPEN and left is not None else None
    return out


def _why(row: sqlite3.Row, outcome: str | None) -> str | None:
    """למה שאלה בוטלה, כפי שהכלל ששאל רשם ("בוטלה: <סיבה>"; ``outcome`` מ-``memory.outcomes``). רק בהיסטוריה."""
    if row["state"] != CANCELLED or not outcome:
        return None
    return outcome.removeprefix("בוטלה: ")


def open_asks(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    expire_due(conn, now)
    return [view(conn, r, now) for r in conn.execute("SELECT * FROM brain_asks WHERE state = 'open' ORDER BY id")]


def recent_closed(conn: sqlite3.Connection, limit: int = 20, now: datetime | None = None) -> list[dict]:
    return [view(conn, r, now) for r in conn.execute(
        "SELECT * FROM brain_asks WHERE state != 'open' ORDER BY id DESC LIMIT ?", (max(int(limit), 0),))]


def history(conn: sqlite3.Connection, *, days: int = HISTORY_DAYS, state: str | None = None, who: str | None = None,
            kind: str | None = None, q: str | None = None, limit: int = HISTORY_LIMIT,
            now: datetime | None = None) -> dict:
    """שאלות שנסגרו ב-``days`` הימים האחרונים (עד ``HISTORY_DAYS``), החדשה ראשונה. ``total`` = כמה נסגרו בטווח בלי הסינון,
    כדי שהמסך יגיד "מוצגות 9 מתוך 14". סינון: ``state`` (answered/expired/cancelled), ``who`` (``admin`` / ``machine`` = מי
    ענה; ``none`` = אף אחד, כלומר פקעה או בוטלה), ``kind`` (סוג הישות), ``q`` (חיפוש בנוסח, בישות ובמי שענה).
    ``ValueError`` על ערך שאינו מוכר: סינון שלא הובן אינו "הכול" בשקט. ``today`` = כמה נענו וכמה פקעו מאז חצות (שעון
    השרת), לכרטיסים שבדף."""
    if state is not None and state not in (ANSWERED, EXPIRED, CANCELLED):
        raise ValueError(f"state {state!r}")
    if who is not None and who not in (*ROLES, "none"):
        raise ValueError(f"who {who!r}")
    if kind is not None and kind not in memory.KINDS:
        raise ValueError(f"kind {kind!r}")
    now = now or _utcnow()
    expire_due(conn, now)
    days = min(max(int(days), 1), HISTORY_DAYS)
    since = _stamp(now - timedelta(days=days))
    base = "FROM brain_asks a WHERE a.state != 'open' AND julianday(a.closed_at) >= julianday(?)"
    where, params = "", []
    if state:
        where += " AND a.state = ?"
        params.append(state)
    if who == "none":
        where += " AND a.state != 'answered'"
    elif who:
        where += " AND a.state = 'answered' AND a.answered_role = ?"
        params.append(who)
    if kind:
        where += " AND a.entity_kind = ?"
        params.append(kind)
    if q and q.strip():
        like = "%" + q.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        where += (" AND (a.title_he LIKE ? ESCAPE '!' OR a.detail_he LIKE ? ESCAPE '!' OR a.entity_key LIKE ? ESCAPE '!'"
                  " OR a.answered_by LIKE ? ESCAPE '!')")
        params += [like] * 4
    total = conn.execute("SELECT COUNT(*) " + base, (since,)).fetchone()[0]
    rows = conn.execute("SELECT a.* " + base + where + " ORDER BY a.id DESC LIMIT ?",
                        (since, *params, max(int(limit), 0))).fetchall()
    midnight = _stamp(now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc))
    today = {st: conn.execute("SELECT COUNT(*) FROM brain_asks WHERE state = ? AND julianday(closed_at) >= julianday(?)",
                              (st, midnight)).fetchone()[0] for st in (ANSWERED, EXPIRED)}
    why = memory.outcomes(conn, [r["closed_decision"] for r in rows if r["state"] == CANCELLED])
    return {"items": [view(conn, r, now, closed_outcome=why.get(r["closed_decision"])) for r in rows], "total": total,
            "days": days, "today": today}


def prune_history(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """שאלה שנסגרה לפני יותר מ-``HISTORY_DAYS`` ימים יוצאת מהטבלה (ההחלטה שלה נשארת ב-``brain_decisions`` לפי כלל השמירה
    שלה). לעולם אינו זורק; מחזיר כמה יצאו."""
    try:
        cutoff = _stamp((now or _utcnow()) - timedelta(days=HISTORY_DAYS))
        if conn.execute("SELECT 1 FROM brain_asks WHERE state != 'open' AND julianday(closed_at) < julianday(?) LIMIT 1",
                        (cutoff,)).fetchone() is None:
            return 0                      # קריאה בלבד: expire_due רץ בכל hello, ו-DELETE ריק היה לוקח את נעילת הכתיבה
        return _write(conn, "DELETE FROM brain_asks WHERE state != 'open' AND julianday(closed_at) < julianday(?)",
                      (cutoff,)).rowcount
    except Exception:                     # noqa: BLE001
        log.exception("brain: pruning the question history failed")
        return 0


def for_machine(conn: sqlite3.Connection, mac: str, now: datetime | None = None) -> list[dict]:
    """מה שה-hello אומר למכונה: השאלות הפתוחות עליה שהיא רשאית לענות עליהן, וגם שאלות שנסגרו בעשר-חמש-עשרה הדקות
    האחרונות (``RECENT_CLOSED_S``) -- מסך שעוד מחכה לומד מהן שנענתה במקום אחר או שפקעה, ומה ברירת המחדל. ריק = אין."""
    now = now or _utcnow()
    expire_due(conn, now)
    since = _stamp(now - timedelta(seconds=RECENT_CLOSED_S))
    rows = conn.execute("SELECT * FROM brain_asks WHERE entity_kind = 'machine' AND entity_key = ?"
                        " AND (state = 'open' OR julianday(closed_at) >= julianday(?)) ORDER BY id", (mac, since))
    out = []
    for row in rows:
        if MACHINE not in json.loads(row["who_json"]):
            continue
        item = view(conn, row, now)
        res = resolution(conn, row["id"], now)
        item["effective"] = res.option if res else None      # מה לעשות (ריק כל עוד פתוחה)
        item["by_user"] = bool(res and res.by_user)
        out.append(item)
    return out
