"""#3320 (#3064, K6): מתי מחיצה מתחילה, ומי ממתין למי -- **בצל בלבד**.

**הנקודה.** D16 בקוד הקיים: המנוע מחכה ל-``listening`` על המחיצה הבאה, ו"אף מחשב לא התייצב" מכשיל את הגל כולו.
ב-13/11 (room_714f980d) משכפל שהשלים מגירה שחזרה לא אמר ``listening`` על p4, ה-progress שלו הגיע כל הזמן, ואחרי
780 ש' הגל נכשל ומגירה תקינה ב-96% נמחקה. K6 אומר מה היה נכון לעשות, לפי הראיות של אותו צילום (`observe`):

- ``START`` -- יש מי שמוכן (``listening`` על המחיצה הזו): מתחילים איתו. מי שעוד משלים ממשיך **לבד** ב-HTTP, ואף
  אחד אינו מחכה לו (I19).
- ``HOLD_PARTITION`` -- אף אחד אינו מוכן, אבל למישהו יש **ראיית עבודה** טרייה (ledger, הורדה, V6): הוא משלים. ממתינים
  לו, לעולם לא ``FAIL_WAVE`` ולא מחיקה (I18 א).
- ``HOLD`` -- אף אחד אינו מוכן ואין עבודה, אבל יש **ראיית חיים** (progress) או חוש שלא נקרא: ממתינים (I18 ב).
- ``PARK`` -- ההחזקה הגיעה לתקרה, או שאין לאף אחד ראיה בתוך ``lost_s`` (LOST, I18 ג). **PARK אינו כשל ואינו
  מחיקה:** המגירות נשארות כמו שהן, להשלמה מאוחרת (I8: רק FAILED נמחק).

**אין כאן ``FAIL_WAVE``.** הכלל לעולם אינו מציע להכשיל גל או למחוק: זו נקודת ההבדל מהקוד הקיים.

**צל בלבד, גם ב-``enforce``:** ההחלטה נרשמת ב-``brain_decisions`` (כלל ``K6``, ``mode=shadow``) ובלוג, ושום דבר
בשידור אינו משתנה. מי שאוכף את D16 היום הוא הווטו (K2), לא K6. **לא נמדד:** מה המנוע עושה כשחלק מהגל מוכן וחלק
משלים (הנקודה הזו אינה קוראת למוח: `chunksend.py` אינו בתחום של #3320), ולכן I19 נבדק כאן ברמת הכלל בלבד.
"""

from __future__ import annotations

from server.brain import decide as brain
from server.brain.observe import LISTENING, WORK, Snapshot

RULE = "K6"
ACTION = "PARTITION"
START, HOLD_PARTITION, HOLD, PARK = "START", "HOLD_PARTITION", "HOLD", "PARK"
VERDICTS = (START, HOLD_PARTITION, HOLD, PARK)
#: הנקודות בקוד הקיים שבהן מחיצה מתחילה (או לא): K6 נשפט רק בהן.
POINTS = frozenset({"D16"})


def _ready(snap: Snapshot, mac: str, part: int | None, cfg: brain.Config) -> bool:
    """``listening`` טרי על **המחיצה הזו** (`observe.wave_evidence`: ``chunk-pass p<N> ...``)."""
    c = snap.cloner(mac)
    if c is None or part is None:
        return False
    tag = f"chunk-pass p{part} "
    return any(e.kind == LISTENING and e.source.startswith(tag) and snap.now - e.seen_at <= cfg.lost_s
               for e in c.evidence)


def _working(snap: Snapshot, mac: str, cfg: brain.Config) -> bool:
    c = snap.cloner(mac)
    return c is not None and any(e.kind in WORK and snap.now - e.seen_at <= cfg.stalled_s for e in c.evidence)


def decide(snap: Snapshot, cfg: brain.Config, *, members=(), held_s: float = 0.0) -> brain.Decision:
    """פונקציה טהורה: (צילום, ספים, חברי הגל, כמה זמן מוחזקת) -> החלטה אחת על תחילת ``snap.part``.

    ``members``: המשכפלים שהמחיצה מחכה להם; ריק = כל מי שבצילום. דטרמיניסטי (I1): הסדר לפי MAC."""
    macs = sorted(set(members) or {c.mac for c in snap.cloners})
    where = ",".join(macs) or snap.round_id
    states = {m: brain.liveness(snap.cloner(m), snap.now, cfg, snap.unknown) for m in macs}
    evidence = tuple(f"{m}={s}" for m, (s, _w, _e) in states.items())
    ready = [m for m in macs if _ready(snap, m, snap.part, cfg)]
    if ready:
        done = [m for m in macs if m not in ready and states[m][0] == brain.FINISHED]     # #3836: סיים, לא "ממשיך לבד"
        rest = [m for m in macs if m not in ready and m not in done and states[m][0] != brain.LOST]
        tail = f"; {', '.join(rest)} ממשיך לבד ב-HTTP, ואיש אינו מחכה לו" if rest else ""
        tail += f"; {', '.join(done)} סיים" if done else ""
        return brain.Decision(RULE, ACTION, ",".join(ready), START,
                              f"מוכן למחיצה {snap.part}: {', '.join(ready)}{tail}",
                              (*evidence, *(f"continue_alone={m}" for m in rest)))
    if held_s >= cfg.ceiling_s:
        return brain.Decision(RULE, ACTION, where, PARK,
                              f"מוחזקת {held_s:.0f}s, מעל התקרה {cfg.ceiling_s:g}s: המגירות נשמרות להשלמה, לא נמחקות",
                              evidence)
    working = [m for m in macs if _working(snap, m, cfg)]
    if working:
        return brain.Decision(RULE, ACTION, where, HOLD_PARTITION,
                              f"אף אחד לא מוכן למחיצה {snap.part}, ו-{', '.join(working)} עוד עובד (משלים): ממתינים לו",
                              evidence)
    waiting = [m for m in macs if states[m][0] in brain.BLOCKING]
    if waiting:
        return brain.Decision(RULE, ACTION, where, HOLD,
                              f"אף אחד לא מוכן ואין ראיית עבודה, אבל יש ראיית חיים או חוש שלא נקרא: "
                              f"{'; '.join(f'{m}={states[m][0]}' for m in waiting)}", evidence)
    return brain.Decision(RULE, ACTION, where, PARK,
                          f"אין שום ראיה בתוך {cfg.lost_s:g}s לאף אחד מ-{where}: LOST, המגירות נשמרות ולא נמחקות",
                          evidence)
