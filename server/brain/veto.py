"""#3064, שלב 1: הווטו — לפני כל פעולה הרסנית שהקוד הקיים עומד לבצע, "יש ראיה חיה בחלון?".

כל נקודה הרסנית קוראת ל-`check(...)` פעם אחת לפני הביצוע. המתג הוא ההגדרה ``brain_veto_mode``:

- ``off`` — `check` מחזיר "בצע" מיד, בלי לקרוא דבר נוסף ובלי לרשום. ההתנהגות של לפני #3064, בדיוק.
- ``shadow`` (**ברירת המחדל**) — המוח מצלם, מחליט ורושם שורה לכל החלטה ("הווטו היה חוסם: …"),
  ו**תמיד** מחזיר "בצע". שום דבר לא נחסם.
- ``enforce`` — ממומש, וכבוי. כשהמוח אומר HOLD, `proceed` הוא False: הפעולה ההרסנית לא מתבצעת,
  והנקודה ממשיכה להמתין ושואלת שוב. כל החזקה נגמרת בתקרה (`Config.ceiling_s`, ערך התחלה בקוד).

**הווטו לא חוסם כשל שיש לו ראיה משלו** (`own_evidence`: readback שנכשל, SMART אדום, כשל כתיבה, D14,
D12). הוא מגן רק מפני החלטה שנשענת על "אין ראיה".

**שלב 5 -- הזיכרון:** לפני ההחלטה נטען לצילום מה שהזיכרון אומר על המשכפלים שהפעולה נשענת על השתיקה שלהם
(`recall.load`), והכלל "משכפל שחוזר משתיקה" (`decide.remembered`) יכול **רק להאריך** החזקה. החלטה שנשענה על הזיכרון נרשמת
ב-``brain_decisions`` עם הרשומות שלו (``memory#N`` בראיות). ב-``shadow`` היא "היה חוסם" ולא יותר.

**הווטו לעולם אינו זורק** לתוך הנקודה ההרסנית: מוח שנפל הוא שורת יומן, וב-``enforce`` הוא HOLD עד התקרה
("לא הצלחנו לבדוק" אינו "בדקנו, אין ראיה" — עיקרון 5).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from server.agent import timing as agent_timing
from server.brain import calibrate, decide as brain, grace, memory, observe, partition, recall, recording, served as served_module
from server.db import get_setting, journal

log = logging.getLogger("imagesrv.brain")

SETTING = "brain_veto_mode"
OFF, SHADOW, ENFORCE = "off", "shadow", "enforce"
MODES = (OFF, SHADOW, ENFORCE)
DEFAULT = SHADOW
#: **הכרעת נדב 14/11 בערב: "הספים בהגדרות כנראה מפריעים למוח. הוא צריך לשלוט בהם, והם לא צריכים להיות בהגדרות."**
#: אין כאן ספים: כל סף טכני של המוח הוא ערך התחלה בקוד (`decide.Config`), וסף שהיה הגדרה עבר ל-`RETIRED`.
#: בהגדרות נשארים רק המתג (מדיניות) והגבול העליון של תקציב הרשת (`budget.SETTING`, נדב 14/11).
#: LOST אינו כאן: הוא `lost_wait_seconds` הקיים (`agent_timing.lost_seconds`), מותר רק להגדיל.

#: כל מפתחות ההגדרות של המוח: `WRITE_SETTINGS` בקונסולה.
KEYS = (SETTING,)
#: התוויות בעברית — המסך, השגיאות ושורות היומן (`journal_he.SETTINGS_HE`) קוראים מכאן, שלא יסתחפו.
LABELS_HE = {
    SETTING: "המוח: וטו על פעולות הרסניות",
    # יצאו מההגדרות: התוויות נשארות לשורות יומן ישנות ולשורת `note_retired`
    "brain_suspect_seconds": "המוח: חשד אחרי שתיקה (שניות)",
    "brain_stalled_seconds": "המוח: העבודה לא זזה (שניות)",
    "brain_hold_ceiling_seconds": "המוח: תקרת החזקה (שניות)",
}
#: ספים שיצאו מההגדרות: המפתח -> (השדה של `decide.Config` שהוא ערך ההתחלה שלו בקוד, מכפיל להצגה, יחידה). ערך שנשאר
#: שמור ב-DB **אינו נקרא** (`config`) **ואינו נמחק**; `note_retired` רושם אותו ביומן פעם אחת.
RETIRED = {
    "brain_suspect_seconds": ("suspect_s", 1.0, "ש'"),                      # KT2
    "brain_stalled_seconds": ("stalled_s", 1.0, "ש'"),                      # T38
    "brain_hold_ceiling_seconds": ("ceiling_s", 1.0, "ש'"),                 # KT6
    "brain_memory_min_returns": ("min_returns", 1.0, "סבבים"),              # שלב 5
    "brain_memory_max_age_days": ("max_age_s", 1 / brain.DAY_S, "ימים"),    # שלב 5
}
RETIRED_EVENT = "brain_setting_retired"
MODES_HE = {OFF: "כבוי", SHADOW: "צל (רושם בלבד)", ENFORCE: "פעיל (חוסם)"}

#: רשומת החזקה שאיש לא נגע בה מעל זה נשכחת (הנקודה נגמרה בלי לשאול שוב): הרשם אינו גדל לנצח.
STALE_HOLD_S = 24 * 3600

_held: dict[tuple, float] = {}           # (גל, פעולה, מחיצה, יעדים) -> מתי הוחזקה לראשונה (monotonic)
_held_lock = threading.Lock()
#: #3185: ההחזקות הפתוחות בזיכרון ארוך-הטווח -- אותו מפתח -> מספרי השורות ב-``brain_decisions``.
_remembered: dict[tuple, list] = {}

#: התוצאה שנרשמת מיד, כשההחלטה לא מחזיקה דבר.
DONE_HE = "הפעולה בוצעה"
SHADOW_DONE_HE = "צל: הקוד הקיים ביצע את הפעולה"
NO_HOLD_HE = "הפעולה בוצעה: לנקודה אין החזקה"


@dataclass(frozen=True)
class Verdict:
    proceed: bool           # True = הקוד הקיים מבצע את הפעולה
    mode: str
    would_block: bool       # המוח אמר HOLD (ב-shadow: "היה חוסם")
    reason: str
    snapshot: str = ""      # hash הצילום


def mode(conn) -> str:
    """המתג. ערך חסר, לא מוכר, או שלא נקרא = ``shadow``: רושם בלבד, לעולם לא חוסם ולא שותק."""
    try:
        raw = get_setting(conn, SETTING)
    except Exception:                       # noqa: BLE001 — הנקודה ההרסנית אינה נופלת בגלל המוח
        log.exception("brain: %s unreadable, using %s", SETTING, DEFAULT)
        return DEFAULT
    return raw if raw in MODES else DEFAULT


def parse_mode(raw: object) -> str | None:
    """המתג: off / shadow / enforce בדיוק — או ``None`` (הקונסולה דוחה ב-400)."""
    return raw if isinstance(raw, str) and raw in MODES else None


def effective(conn) -> dict[str, str]:
    """הערכים שבתוקף, לקונסולה: ברירת המחדל כשלא נקבע או כשהשמור פגום — לא "לא נקבע" (כמו `agent_timing`)."""
    return {SETTING: mode(conn)}


def config(conn, macs=(), now: float | None = None) -> brain.Config:
    """הספים. `macs`: המשכפלים שהכיול של סף החשד שלהם (KT2, `calibrate`) נקרא ונרשם בצילום; בלעדיהם -- ערך ההתחלה."""
    # רק LOST (`lost_wait_seconds`, הגדרה קיימת של זמני הסוכן) נקרא מההגדרות. כל שאר הספים הם ערכי ההתחלה שבקוד
    # (`decide.Config`) והמוח הוא ששולט בהם (נדב 14/11 בערב).
    cfg = brain.Config(lost_s=agent_timing.lost_seconds(conn))
    return calibrate.apply(conn, cfg, macs, now=time.time() if now is None else now) if macs else cfg


def note_retired(conn) -> list[str]:
    """פעם בעליית השרת: סף שיצא מההגדרות וערך עדיין שמור לו ב-DB (שהמפעיל קבע לפני כן). הערך **מתעלמים ממנו ולא
    מוחקים אותו**, ובמקום זה נרשמת שורת יומן אחת לכל ערך שמור -- כדי שמי ששינה אותו ידע שהוא כבר לא פועל, ושהמוח
    קובע את הסף. מחזיר את המפתחות שנרשמו. לעולם אינו זורק (עיקרון 5: "לא הצלחנו לבדוק" אינו "אין ערך")."""
    start, noted = brain.Config(), []
    for key, (field, scale, unit) in RETIRED.items():
        try:
            raw = get_setting(conn, key)
            if raw in (None, ""):
                continue
            label = LABELS_HE.get(key) or recall.LABELS_HE.get(key, key)
            detail = (f"{label}: הערך השמור ({raw}) אינו נקרא יותר. המוח קובע את הסף בעצמו, "
                      f"וערך ההתחלה בקוד הוא {getattr(start, field) * scale:g} {unit}. הערך לא נמחק")
            if conn.execute("SELECT 1 FROM journal WHERE event = ? AND detail = ? LIMIT 1",
                            (RETIRED_EVENT, detail)).fetchone() is None:
                journal(conn, "brain_setting_retired", detail)
            noted.append(key)
        except Exception:                   # noqa: BLE001 — עליית השרת אינה נופלת בגלל שורת יומן
            log.exception("brain: the retired setting %s was not noted", key)
    return noted


def line(d: brain.Decision, *, now: float, mode_: str, snap: str, round_id: str, part: int | None,
         point: str = "", trigger: str = "") -> str:
    """שורה מובנית אחת להחלטה: זמן, כלל, יעד, סיבה, ראיות, hash הצילום. `point` = הנקודה ההרסנית בקוד (D16...),
    `trigger` = מה שהקוד הקיים אמר על עצמו (למשל ``later_start_timeout 780s``)."""
    say = ""
    if d.rule == "K2" and d.verdict == brain.HOLD:
        say = "הווטו חסם" if mode_ == ENFORCE else "הווטו היה חוסם"
    return "brain " + json.dumps({
        "at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "mode": mode_, "round": round_id, "part": part, "snap": snap, "rule": d.rule, "point": point,
        "action": d.action,
        "target": d.target, "verdict": d.verdict, **({"say": say} if say else {}),
        **({"trigger": trigger} if trigger and d.rule == "K2" else {}),
        "reason": d.reason, "evidence": list(d.evidence), **({"memory": list(d.memory)} if d.memory else {})},
        ensure_ascii=False)


def attach(conn, served_source) -> None:
    """מחבר ל-`conn` את מקור ה-V6 (`served_source(now, window_s) -> (evidence, unknown)`), כך שכל נקודה הרסנית
    שמקבלת את ה-`conn` — גם `room._mark_lost` שאינה מכירה את האפליקציה — רואה את אותו מקור. `conn` שאינו מקבל
    תכונות (חיבור sqlite גולמי, בבדיקות) = אין מקור, והמוח נשען על מה שהיה בשלב 1."""
    try:
        conn.brain_served = served_source
    except AttributeError:
        log.warning("brain: %s cannot carry the served-bytes source; V6 is off for it", type(conn).__name__)


def _served_evidence(conn, served, now: float, window_s: float) -> tuple[list, list[str]]:
    """ראיות V6 לצילום. מקור שנפל = חוש `served_http` שלא נקרא, לא "אין בייטים" (עיקרון 5)."""
    source = served if served is not None else getattr(conn, "brain_served", None)
    if source is None:
        return [], []
    try:
        return source(now, window_s)
    except Exception:                       # noqa: BLE001 — הווטו אינו נופל בגלל חוש אחד
        log.exception("brain: the served-bytes source failed")
        return [], [served_module.UNKNOWN_SENSE]


def _remember(conn, key: tuple, decisions: list, verdict: brain.Decision, *, mode_: str, held: bool,
              rule: str, round_id: str, held_s: float) -> list:
    """#3185: ההחלטה לזיכרון ארוך-הטווח -- שורה לכל משכפל שהיא נוגעת בו (או לגל, כשאין משכפל), פעם אחת לכל
    החזקה ולא בכל בדיקה חוזרת. החזקה שמשתחררת מקבלת את התוצאה שלה: כמה זמן, ולמה."""
    with _held_lock:
        open_ = _remembered.get(key)
        if open_ is not None and held:
            return []                           # אותה החזקה, נשאלת שוב: אין החלטה חדשה
        if open_ is not None:
            del _remembered[key]
    if open_ is not None:
        memory.resolve(conn, open_, f"שוחררה אחרי {held_s:.0f}s ({verdict.verdict}): {verdict.reason}. {DONE_HE}")
        return []
    if held:
        outcome = None
    elif verdict.verdict == brain.HOLD:
        outcome = SHADOW_DONE_HE if mode_ != ENFORCE else NO_HOLD_HE
    else:
        outcome = DONE_HE
    states = [f"{d.target}={d.verdict}" for d in decisions if d.rule == "K1"]
    targets = key[3]
    used = [f"memory#{i}" for i in verdict.memory]     # שלב 5: הרשומות מהזיכרון שההחלטה נשענה עליהן
    ids = [memory.record(conn, mode=mode_, rule=verdict.rule, point=rule, action=verdict.action,
                         verdict=verdict.verdict, reason=verdict.reason, evidence=(*states, *verdict.evidence, *used),
                         round_id=round_id, entity=entity, outcome=outcome)
           for entity in ([("machine", mac) for mac in targets] or [("round", round_id)])]
    if held:
        with _held_lock:
            _remembered[key] = ids
    return ids


#: #3320 (K6): התוצאה של החלטת תחילת מחיצה -- תמיד צל, גם ב-enforce (`partition.py`).
K6_SHADOW_HE = "צל: K6 רק נרשם, המחיצה התנהלה כמו בקוד הקיים"


def _partition(conn, snap, cfg, *, rule: str, round_id: str, targets, held_s: float, now: float) -> None:
    """#3320 (K6): בנקודה שבה מחיצה מתחילה (D16), מה K6 היה מחליט -- פעם אחת לכל החזקה, בצל. לעולם אינו זורק."""
    try:
        d = partition.decide(snap, cfg, members=targets, held_s=held_s)
        log.info("%s", line(d, now=now, mode_=SHADOW, snap=snap.digest(), round_id=round_id, part=snap.part,
                            point=rule))
        ids = [memory.record(conn, mode=SHADOW, rule=d.rule, point=rule, action=d.action, verdict=d.verdict,
                             reason=d.reason, evidence=d.evidence, round_id=round_id, entity=("round", round_id),
                             outcome=K6_SHADOW_HE)]
        recording.write(conn, round_id, kind=partition.RULE, snap=snap, cfg=cfg, decisions=[d], ids=ids,
                        mode=SHADOW, point=rule, held_s=held_s, members=targets)
    except Exception:                       # noqa: BLE001 — K6 אינו מפיל את הנקודה
        log.exception("brain: K6 at %s of %s failed", rule, round_id)


def check(conn, *, action: str, rule: str, round_id: str, targets, reason: str, drawers=(),
          own_evidence: str | None = None, part: int | None = None, engine=(), can_hold: bool = True,
          now: float | None = None, mono=time.monotonic, served=None) -> Verdict:
    """פעולה הרסנית אחת, לפני שהיא מתבצעת. `can_hold=False`: לנקודה אין החזקה מוגדרת — היא נרשמת
    גם ב-``enforce``, ומתבצעת (כיום אין נקודה כזו: D19 קיבלה החזקה בשלב 2). `served`: מקור V6 מפורש (בדיקות);
    ברירת המחדל — זה שחובר ל-`conn` ב-`attach`."""
    mode_ = mode(conn)
    if mode_ == OFF:
        return Verdict(True, OFF, False, "")
    now = time.time() if now is None else now
    targets = tuple(sorted(set(targets)))
    key = (round_id, action, part, targets)
    with _held_lock:
        first = _held.get(key)
    held_s = mono() - first if first is not None else 0.0
    proposal = observe.Proposal(action, rule, targets, reason, tuple(drawers), own_evidence, round(held_s, 1))
    snap = cfg = None
    try:
        cfg = config(conn, targets, now)
        sv_evidence, sv_unknown = _served_evidence(conn, served, now, cfg.lost_s)
        recalled = recall.load(conn, targets, now=now, max_age_s=cfg.max_age_s)
        snap = observe.from_db(conn, round_id, now, engine=engine, part=part, proposals=(proposal,),
                               served=sv_evidence, unknown=sv_unknown, memory=recalled, up_at=grace.up_at(conn))
        decisions = brain.decide(snap, cfg)
        digest = snap.digest()
    except Exception as exc:                # noqa: BLE001 — המוח נפל: לא ידוע, לא "אין ראיה"
        log.exception("brain: %s %s of %s: the brain itself failed", rule, action, round_id)
        decisions = [brain.Decision("K2", action, ",".join(targets) or round_id,
                                    brain.HOLD if held_s < brain.Config().ceiling_s else brain.PROCEED,
                                    f"המוח לא הצליח להחליט ({type(exc).__name__}: {exc})")]
        digest = "-"
        snap = None                          # אין צילום שההחלטה נשענה עליו: אין מה להקליט
    verdict = decisions[-1]
    for d in decisions:
        text = line(d, now=now, mode_=mode_, snap=digest, round_id=round_id, part=part, point=rule,
                    trigger=reason)
        (log.warning if d.rule == "K2" and d.verdict == brain.HOLD else log.info)("%s", text)
    blocks = verdict.verdict == brain.HOLD
    # שני צעדים ולא `not (a and b and c)` אחד: על הצורה הזו CodeQL 2.27.5 מסמן את `proceed` כ"עלול לא להיות
    # מאותחל" (py/uninitialized-local-variable) וחוסם את השער. נמדד בבידוד על tr1, 20/11: צורה אחת = התראה, שתיים = 0
    holds = blocks and mode_ == ENFORCE and can_hold
    proceed = not holds
    fresh = released = False
    with _held_lock:
        fresh = not proceed and key not in _held
        released = proceed and _held.pop(key, None) is not None
        if not proceed:
            if fresh:
                for stale in [k for k, at in _held.items() if mono() - at > STALE_HOLD_S]:
                    del _held[stale]        # החזקה שאיש לא שאל עליה יום: נקודה שהסתיימה בלי שחרור
            _held.setdefault(key, mono())
    ids = _remember(conn, key, decisions, verdict, mode_=mode_, held=not proceed, rule=rule, round_id=round_id,
                    held_s=held_s)
    if snap is not None and first is None:
        # #3320 (R14): הצילום המלא שההחלטה נשענה עליו, להקלטת הגל; ו-K6 בנקודה שבה מחיצה מתחילה
        recording.write(conn, round_id, kind="K2", snap=snap, cfg=cfg, decisions=decisions, ids=ids, mode=mode_,
                        point=rule, held_s=held_s)
        if rule in partition.POINTS:
            _partition(conn, snap, cfg, rule=rule, round_id=round_id, targets=targets, held_s=held_s, now=now)
    if mode_ == ENFORCE and (fresh or released):
        # למפעיל, פעם אחת לכל החזקה ופעם אחת בשחרור — לא בכל בדיקה חוזרת
        try:
            journal(conn, "brain_veto", f"{'הווטו חסם' if not proceed else 'הווטו שחרר'} {rule} {action}"
                    f" {round_id} {','.join(targets)}: {verdict.reason} (נקודה: {reason})", round_id=round_id)
        except Exception:                   # noqa: BLE001 — השורה בלוג למעלה כבר נכתבה
            log.exception("brain: the journal line of %s %s was not written", rule, action)
    return Verdict(proceed, mode_, blocks, verdict.reason, digest)


def settle(conn, *, round_id: str, action: str, part: int | None) -> None:
    """הנקודה ההרסנית יצאה בלי לשאול שוב (הדיווח שחיכתה לו הגיע, או שהגל נעצר): ההחזקה שלה נגמרה. בלי זה
    ההחזקה הבאה על אותו גל, פעולה ומחיצה (D19 אחרי השלמה נוספת) הייתה נספרת מההחזקה הישנה, והתקרה נגמרת מיד.
    למפעיל נכתבת שורת "שחרר" — אחרת "הווטו חסם" ביומן נראה כהחזקה שלא נגמרה."""
    with _held_lock:
        gone = [k for k in _held if k[:3] == (round_id, action, part)]
        for key in gone:
            del _held[key]
        open_ = [i for k in [k for k in _remembered if k[:3] == (round_id, action, part)]
                 for i in _remembered.pop(k)]
    memory.resolve(conn, open_, "שוחררה: הדיווח שהמתנו לו הגיע, והפעולה ההרסנית לא בוצעה")
    for _round, _action, _part, targets in gone:
        try:
            journal(conn, "brain_veto", f"הווטו שחרר {action} {round_id} {','.join(targets)}:"
                    " הדיווח שהמתנו לו הגיע, והפעולה ההרסנית כבר אינה נדרשת", round_id=round_id)
        except Exception:                   # noqa: BLE001
            log.exception("brain: the release line of %s %s was not written", action, round_id)


def forget(round_id: str | None = None) -> None:
    """מנקה את רשם ההחזקות (סוף גל, ובדיקות)."""
    with _held_lock:
        for key in [k for k in _held if round_id is None or k[0] == round_id]:
            del _held[key]
        for key in [k for k in _remembered if round_id is None or k[0] == round_id]:
            del _remembered[key]       # בלי conn כאן: נשארת פתוחה עד העלייה הבאה (`memory.ORPHAN_HE`)
