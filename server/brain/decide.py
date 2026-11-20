"""#3064, שלב 1: `decide(snapshot, cfg) -> list[Decision]` — פונקציה טהורה. רק הכללים שהווטו צריך.

**K1 — חיות.** לכל משכפל, לפי **OR** של הראיות בצילום (`observe`):

- ``LOST`` — אין שום ראיה בתוך `lost_s` (ההגדרה `lost_wait_seconds`, ברירת מחדל 180).
- ``SUSPECT`` — אין ראיה בתוך `suspect_s` (30, KT2). **הסף מכויל לכל משכפל** (`calibrate`, `Config.suspect_by_mac`): ערך
  ההתחלה כשאין מספיק היסטוריה, והסיבה כתובה בשורה. אינו מפעיל דבר, רק שורת יומן.
- ``STALLED`` — יש ראיית חיים, אבל ראיית העבודה (הורדה או יומן) לא זזה `stalled_s` (180).
- ``FINISHED`` — (#3836) החבר דיווח ``done``: מצב סופי, בלי קשר לגיל הראיה. ``failed``/``lost`` נשפטים כרגיל.
- ``ALIVE`` — אחרת.
- ``UNKNOWN`` — הראיות ישנות, **וחוש אחד לפחות לא נקרא**: אי אפשר להוכיח מוות (עיקרון 5).

**K2 — וטו.** לכל פעולה הרסנית שהקוד הקיים עומד לבצע (`Proposal`), אחד מאלה:

- ``PROCEED`` — לכשל יש ראיה חיובית משלו (readback, SMART אדום, D14, D12): הווטו לא נוגע בו.
- ``HOLD`` — הפעולה נוקבת בשם דיסק ``done`` (I4). בלי תקרה: דיסק שאומת לא נפגע לעולם.
- ``PROCEED`` — הפעולה כבר מוחזקת `ceiling_s` (30 דק'): כל החזקה נגמרת, בשם ועם סיבה.
- ``HOLD`` — **(#3330, KT5, I15)** השרת עלה לפני פחות מ-`grace_s` (300): "אין ראיה" הוא "לא הסתכלתי". אחרי התקרה
  (כל החזקה נגמרת) ואחרי הראיה המשלו והדיסק שאומת, כך שהחלון אינו חוסם כשל עם ראיה ואינו מחזיק לנצח.
- ``HOLD`` — לאחד מהמשכפלים שהפעולה נשענת על השתיקה שלהם יש ראיה בחלון (ALIVE/SUSPECT/STALLED/UNKNOWN).
- ``HOLD`` — **(שלב 5, הזיכרון)** כולם LOST, אבל משכפל אחד מהם **חזר בעבר** משתיקה ארוכה מזו שלו עכשיו, בלפחות
  `min_returns` סבבים בתוך `max_age_s`, ואין סבב בתוך החלון שבו הוא נקבע LOST ולא חזר (`remembered`). ממתינים עד
  השתיקה הארוכה ביותר שממנה חזר, ולא יותר מהתקרה.
- ``PROCEED`` — אחרת: כולם LOST, והפעולה עומדת על "אין ראיה" אמיתי.

**הזיכרון רק מאריך החזקה, ולעולם אינו מקצר אותה ואינו מבצע פעולה הרסנית** בגללו. זיכרון ריק, ישן, סותר או שלא
נקרא = ההחלטה של היום, מילה במילה (זיכרון ריק לא משנה אפילו את הסיבה). ראו #3064.

**בידוד (I3):** ההחלטה על פעולה נשענת **רק** על המשכפלים והמגירות שהיא נוגעת בהם.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from server.brain.observe import DONE, WORK, Cloner, Proposal, Recall, Snapshot

ALIVE, SUSPECT, STALLED, LOST, UNKNOWN = "ALIVE", "SUSPECT", "STALLED", "LOST", "UNKNOWN"
#: #3836: חבר שדיווח `done`. מצב סופי שדווח (ראיה חיובית), לא שתיקה: אינו LOST/SUSPECT/STALLED לעולם.
FINISHED = "FINISHED"
HOLD, PROCEED = "HOLD", "PROCEED"
#: מצבי חיות שחוסמים פעולה הרסנית שנשענת על שתיקה.
BLOCKING = frozenset({ALIVE, SUSPECT, STALLED, UNKNOWN})   # FINISHED אינו חוסם (הכרעת זאוס, #3836): חבר שסיים אינו סיבה לעכב


@dataclass(frozen=True)
class Config:
    """הספים, כולם מההגדרות עם ברירת מחדל (`veto.config`). אין כאן אחוז קבוע."""
    suspect_s: float = 30.0
    lost_s: float = 180.0
    stalled_s: float = 180.0
    ceiling_s: float = 1800.0
    #: #3330 (KT5): חלון החסד אחרי עליית השרת. ערך התחלה בקוד (`grace.GRACE_S`), לא הגדרה.
    grace_s: float = 300.0
    #: שלב 5 (ערכי התחלה בקוד, לא הגדרות): בכמה סבבים המשכפל חזר משתיקה לפני שסומכים על זה, ועד כמה ישן הזיכרון שנחשב.
    min_returns: int = 3
    max_age_s: float = 90 * 86400.0
    #: B61 (`repeatfail`): כמה גלים ברצף דיסק נכשל באותה סיבה לפני שהמוח ממליץ. ערך התחלה בקוד, לא הגדרה (נדב 14/11 בערב), והערכה:
    #: שלוש = כמו `min_returns`, בלי מדידה.
    fail_repeats: int = 3
    #: KT2, מכויל: הסף של משכפל שיש עליו מספיק היסטוריה (`calibrate.learn`), בשניות. חסר = `suspect_s`.
    suspect_by_mac: dict = field(default_factory=dict)
    #: ולכל משכפל שהוחלט לגביו: למה הסף הוא מה שהוא ("אין מספיק היסטוריה: 1 תצפיות...", או "מכויל: ..."). עיקרון 5.
    suspect_note: dict = field(default_factory=dict)

    def suspect_for(self, mac: str) -> float:
        got = self.suspect_by_mac.get(mac)
        return float(got) if isinstance(got, (int, float)) and not isinstance(got, bool) and got > 0 else self.suspect_s


@dataclass(frozen=True)
class Decision:
    rule: str                       # K1 / K2
    action: str                     # LIVENESS, או הפעולה ההרסנית שנשפטה
    target: str                     # mac, או מזהה הגל
    verdict: str                    # ALIVE/SUSPECT/STALLED/LOST/UNKNOWN, או HOLD/PROCEED
    reason: str
    evidence: tuple[str, ...] = ()
    memory: tuple[int, ...] = ()    # שלב 5: מספרי השורות ב-``brain_decisions`` שההחלטה נשענה עליהן


def _age(now: float, seen: float) -> str:
    return f"{max(now - seen, 0.0):.0f}s"


def liveness(c: Cloner | None, now: float, cfg: Config, blind: tuple[str, ...] = ()) -> tuple[str, str, tuple[str, ...]]:
    """(מצב, סיבה, ראיות) למשכפל אחד. משכפל שאינו בצילום = UNKNOWN: לא ראינו, לא "מת"."""
    if c is None:
        return UNKNOWN, "המשכפל אינו בצילום", ()
    unknown = (*c.unknown, *blind)
    evidence = tuple(f"{e.kind}@{_age(now, e.seen_at)} ({e.source})" for e in c.evidence)
    life = max((e.seen_at for e in c.evidence), default=None)
    work = max((e.seen_at for e in c.evidence if e.kind in WORK), default=None)
    if c.state == DONE:        # #3836: סיים ואומת. השתיקה אחרי זה היא המצב התקין, לא ראיה לאובדן
        return FINISHED, "סיים (state=done): מצב סופי שדווח, השתיקה אחריו אינה אובדן", evidence
    if life is None or now - life > cfg.lost_s:
        if unknown:
            return UNKNOWN, f"אין ראיה בתוך {cfg.lost_s:g}s, וחוש לא נקרא: {', '.join(unknown)}", evidence
        return LOST, f"אין שום ראיה בתוך {cfg.lost_s:g}s", evidence
    if now - life > cfg.suspect_for(c.mac):
        note = cfg.suspect_note.get(c.mac)
        return (SUSPECT, f"הראיה האחרונה לפני {_age(now, life)}"
                + (f" (סף חשד {cfg.suspect_for(c.mac):g}s: {note})" if note else ""), evidence)
    if work is not None and now - work > cfg.stalled_s:
        return STALLED, f"מדבר, אבל העבודה לא זזה {_age(now, work)}", evidence
    return ALIVE, f"ראיה לפני {_age(now, life)}", evidence


def _judge(p: Proposal, snap: Snapshot, states: dict[str, tuple[str, str, tuple[str, ...]]], cfg: Config) -> Decision:
    where = ",".join(p.targets) or snap.round_id
    if p.own_evidence:
        return Decision("K2", p.action, where, PROCEED, f"כשל עם ראיה משלו: {p.own_evidence}")
    touched = set(p.drawers)
    done = [f"{d.mac}/{d.serial or d.dev}" for d in snap.drawers if d.state == DONE and (d.mac, d.serial) in touched]
    if done:
        return Decision("K2", p.action, where, HOLD, f"הפעולה פוגעת בדיסק שאומת: {', '.join(done)}")
    if p.held_s >= cfg.ceiling_s:
        return Decision("K2", p.action, where, PROCEED,
                        f"מוחזקת {p.held_s:.0f}s, מעל התקרה {cfg.ceiling_s:g}s")
    if snap.up_at is not None and snap.now - snap.up_at < cfg.grace_s:
        age = f"{max(snap.now - snap.up_at, 0.0):.0f}s"
        return Decision("K2", p.action, where, HOLD,
                        f"השרת עלה לפני {age}, בתוך חלון החסד ({cfg.grace_s:g}s): 'אין ראיה' הוא 'לא הסתכלתי' (KT5)",
                        (f"server_up@{age}",))
    alive = [(mac, *states[mac]) for mac in p.targets if states[mac][0] in BLOCKING]
    if alive:
        return Decision("K2", p.action, where, HOLD,
                        "; ".join(f"{mac}={state}: {why}" for mac, state, why, _ev in alive),
                        tuple(e for *_rest, ev in alive for e in ev))
    held, notes, used = [], [], []
    for mac in p.targets:
        window, why, ids = remembered(snap.recall(mac), snap.now, cfg, snap.round_id)
        used += ids
        if window is None:
            if why != EMPTY:
                notes.append(f"{mac}: {why}")
            continue
        silence = _silence(snap.cloner(mac), snap.now, cfg, p)
        if silence < window:
            held.append(f"{mac} {why}, ועכשיו שותק {silence:.0f}s")
        else:
            notes.append(f"{mac}: שותק {silence:.0f}s, יותר מהשתיקה הארוכה שממנה חזר ({window:.0f}s)")
    if held:
        return Decision("K2", p.action, where, HOLD, "זיכרון: " + "; ".join(held), memory=tuple(sorted(set(used))))
    return Decision("K2", p.action, where, PROCEED,
                    f"אין ראיה חיה בתוך {cfg.lost_s:g}s לאף אחד מ-{where}"
                    + (f" (הזיכרון: {'; '.join(notes)})" if notes else ""), memory=tuple(sorted(set(used))))


#: הסיבה של "אין בזיכרון כלום": ההחלטה של היום בלי שום תוספת, גם לא בסיבה.
EMPTY = "הזיכרון ריק"
DAY_S = 86400.0


def remembered(r: Recall | None, now: float, cfg: Config, round_id: str) -> tuple[float | None, str, tuple[int, ...]]:
    """שלב 5: (חלון, סיבה, רשומות). החלון = השתיקה הארוכה ביותר שממנה המשכפל חזר, בשניות (לא מעל התקרה).
    ``None`` = אין בסיס, וההחלטה היא של היום: ריק, לא נקרא, סותר (סבב קודם בתוך החלון שבו נקבע LOST ולא חזר),
    ישן (כל החזרות ישנות מ-`max_age_s`), או פחות מ-`min_returns` סבבים. הסבב הנוכחי לעולם אינו "סותר": הוא עוד פתוח."""
    if r is None or not (r.returns or r.losses or r.unread):
        return None, EMPTY, ()
    if r.unread:
        return None, "הזיכרון לא נקרא", ()
    fresh = [x for x in r.returns if now - x[1] <= cfg.max_age_s]
    losses = [x for x in r.losses if now - x[1] <= cfg.max_age_s and x[2] != round_id]
    if losses:
        return None, f"הזיכרון סותר: נקבע LOST ולא חזר בסבב {losses[-1][2]}", tuple(x[0] for x in losses)
    if r.returns and not fresh:
        return None, f"הזיכרון ישן: אין חזרה ב-{cfg.max_age_s / DAY_S:g} הימים האחרונים", ()
    rounds = {x[3] for x in fresh}
    ids = tuple(x[0] for x in fresh)
    if len(rounds) < cfg.min_returns:
        return None, f"חזר משתיקה ב-{len(rounds)} סבבים, ונדרשים {cfg.min_returns}", ids
    window = min(max(x[2] for x in fresh), cfg.ceiling_s)
    return window, f"חזר משתיקה ב-{len(rounds)} סבבים, אחרי עד {window:.0f}s", ids


def _silence(c: Cloner | None, now: float, cfg: Config, p: Proposal) -> float:
    """כמה זמן המשכפל שותק. בלי ראיה בצילום (חבר שסומן `lost`): לפחות `lost_s` ועוד זמן ההחזקה -- חסם תחתון,
    כך שהזיכרון מחזיק פחות ולא יותר."""
    life = max((e.seen_at for e in c.evidence), default=None) if c is not None else None
    return now - life if life is not None else cfg.lost_s + p.held_s


def decide(snap: Snapshot, cfg: Config) -> list[Decision]:
    """K1 לכל משכפל (לפי mac), ואחריו K2 לכל פעולה, בסדר שבו הוצעה. דטרמיניסטי (I1)."""
    macs = sorted({c.mac for c in snap.cloners} | {m for p in snap.proposals for m in p.targets})
    states = {mac: liveness(snap.cloner(mac), snap.now, cfg, snap.unknown) for mac in macs}
    out = [Decision("K1", "LIVENESS", mac, state, why, ev) for mac, (state, why, ev) in states.items()]
    out += [_judge(p, snap, states, cfg) for p in snap.proposals]
    return out
