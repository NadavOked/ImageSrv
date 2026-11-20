"""#3064: המוח של פס הייצור -- מי נשאר דלוק (B24), מה המטרה (B38), ואיך עונים על משכפל שכבה (B42). **בצל.**

**B24 (G13), נדב 14/11:** "אם פתחתי סבב של 20 דיסקים, ויש לי 6 משכפלים עם 3 מגירות, השרת צריך להבין שאחד
המשכפלים צריך להישאר דלוק לעוד סיבוב, ואת השאר לכבות." ואיזה: "לפי השם שלו", כלומר לפי המספר. אחרי כל גל
של פס ייצור (`prodline.line_of`) שהיעד שלו עוד לא הושג, `power` אומר מי נשאר דלוק: המשכפלים לפי המספר, מהנמוך,
עד שהמגירות שלהם מכסות את מה שנשאר. משכפל מוחזק (#2031, דלוק ומחכה ל"כבה") אינו מועמד: הוא לא ייכבה וגם
לא יקבל דיסקים לפני לחיצה. מגירות שלא נקראו (0 או `None`) = **לא הוכרע**, בשם המשכפל -- לא "אפס מגירות".
התוצאה (בגל הבא): מי הצטרף לגל בפועל, מול מה שהמוח אמר.

**B38, נדב 14/11:** "המטרה ... הוא לא בוחר. השרת מחליט לבד, לפי כמות הדיסקים והמחשבים." `objective`: הסבב
נכנס בגל אחד (היעד לא גדול מהמגירות) = ``FASTEST_DISK`` -- אין גל הבא לחכות לו, וכל דיסק נכתב הכי מהר שהוא
יכול. יותר מגל אחד = ``MOST_PER_HOUR`` -- הגל בקצב של המהירים והאיטי משלים (#3295 ב'), ומשכפל שלא צריך נכבה
(B24). נרשם פעם אחת לסבב, כשהגל הראשון פתוח. **אף קוד עוד אינו צורך את המטרה**: היא נרשמת בלבד.

**B42:** בפס ייצור אין שאלות (D33 מוחלף בהחלטה). כשמשכפל שכבה באמצע הסבב חזר (`interrupt.on_rejoin`), המוח
אומר ``REWRITE``: הסבב ממשיך, ומה שלא הושלם נכתב שוב (K11, #3291, מחליט אם להמשיך מהנקודה או מההתחלה).
התוצאה: מה שהמפעיל ענה בפועל (`interrupt.answer`).

**צל בלבד** (שלב 5 ב-`server/brain/README.md`: כיבוי וסגירה הם פעולות הרסניות). גם ב-``enforce`` רק נרשם.
המתג הוא ``brain_veto_mode``; ``off`` = לא מחושב ולא נרשם. **אף פונקציה כאן אינה זורקת** אל הסבב.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass

from server.brain import memory

log = logging.getLogger("imagesrv.brain")

RULE_POWER, RULE_LINE = "K10", "LINE"
POWER, OBJECTIVE, ANSWER = "LINE_POWER", "OBJECTIVE", "AUTO_ANSWER"
KEEP, UNDECIDED = "KEEP", "UNDECIDED"
FASTEST_DISK, MOST_PER_HOUR = "FASTEST_DISK", "MOST_PER_HOUR"
REWRITE = "REWRITE"
OFF, ENFORCE = "off", "enforce"
SAY = "צל: אף משכפל לא נשאר דלוק ואף שאלה לא נענתה -- הקוד הקיים ממשיך כרגיל"


@dataclass(frozen=True)
class Cloner:
    mac: str
    name: str                       # מה שהמפעיל רואה ("משכפל 2")
    number: int | None              # המספר שלו (`machines.suffix`); None = אין מספר
    drawers: int | None             # כמה מגירות יש לו; None = לא נקרא
    held: bool = False              # דלוק ומחכה ל"כבה" (#2031)


@dataclass(frozen=True)
class Finding:
    verdict: str
    reason: str
    keep: tuple[str, ...] = ()      # MAC-ים
    evidence: tuple[str, ...] = ()


# --- הטהור --------------------------------------------------------------------------------------------------


def power(remaining: int, cloners: list[Cloner]) -> Finding:
    """B24: מי נשאר דלוק לגל הבא. לפי המספר מהנמוך (בלי מספר -- אחרונים, לפי השם), עד שהמגירות מכסות."""
    held = [c for c in cloners if c.held]
    ready = sorted((c for c in cloners if not c.held),
                   key=lambda c: (c.number is None, c.number if c.number is not None else 0, c.name, c.mac))
    evidence = [f"remaining={remaining}"] + [f"{c.name}:drawers={c.drawers if c.drawers else 'unread'}"
                                               for c in ready] + [f"held={c.name}" for c in held]
    keep, room = [], 0
    for c in ready:
        if room >= remaining:
            break
        if not c.drawers:
            return Finding(UNDECIDED, f"לא הוכרע: מספר המגירות של {c.name} לא נקרא, ולכן אי אפשר לדעת כמה"
                                      " משכפלים צריך", evidence=tuple(evidence))
        keep.append(c)
        room += c.drawers
    off = [c for c in ready if c not in keep]
    names = ", ".join(c.name for c in keep) or "אף אחד"
    short = f" -- גם כולם יחד ({room}) לא מכסים, ויידרש עוד גל" if room < remaining else ""
    reason = (f"נשארו {remaining} דיסקים: {names} נשאר דלוק ({room} מגירות){short}"
              + (f"; {', '.join(c.name for c in off)} היה נכבה" if off else "")
              + (f". {', '.join(c.name for c in held)} מוחזק (דיסק עם בעיה) ואינו נספר" if held else ""))
    return Finding(KEEP, reason, tuple(c.mac for c in keep), tuple(evidence))


def objective(target: int, drawers: int | None, cloners: int) -> Finding:
    """B38: המטרה של הסבב, לפי כמה דיסקים, כמה מגירות וכמה משכפלים."""
    evidence = (f"target={target}", f"drawers={drawers if drawers else 'unread'}", f"cloners={cloners}")
    if not drawers:
        return Finding(UNDECIDED, "לא הוכרע: מספר המגירות במשכפלים שנבחרו לא נקרא", evidence=evidence)
    waves = math.ceil(target / drawers)
    if waves <= 1:
        return Finding(FASTEST_DISK, f"{target} דיסקים נכנסים ב-{drawers} מגירות, בגל אחד: אין גל הבא לחכות לו,"
                                     " וכל דיסק נכתב הכי מהר שהוא יכול", evidence=evidence + ("waves=1",))
    return Finding(MOST_PER_HOUR, f"{target} דיסקים ב-{drawers} מגירות של {cloners} משכפלים = לפחות {waves} גלים:"
                                  " הכי הרבה דיסקים בשעה -- הגל בקצב המהירים והאיטי משלים (#3295 ב'),"
                                  " ומשכפל שלא צריך נכבה (B24)", evidence=evidence + (f"waves={waves}",))


# --- הרישום -------------------------------------------------------------------------------------------------


def _mode(conn) -> str:
    from server.brain import veto                # כמו ב-`roundend._mode`: המתג מגיע מהווטו
    return veto.mode(conn)


def _log(mode: str, rule: str, action: str, round_id: str, f: Finding) -> None:
    say = SAY + (" (לפס הייצור אין אכיפה לפני שלב 5)" if mode == ENFORCE else "")
    log.info("brain %s", json.dumps({"mode": mode, "rule": rule, "action": action, "verdict": f.verdict,
                                      "round": round_id, "keep": list(f.keep), "say": say, "reason": f.reason,
                                      "evidence": list(f.evidence)}, ensure_ascii=False))


def _open(conn, round_id: str, rule: str, action: str) -> list[dict]:
    return [d for d in memory.decisions(conn, kind="round", key=round_id, rule=rule, limit=64)
            if d["action"] == action and d["outcome_state"] == "open"]


def after_wave(conn, *, round_id: str, wave_id: str, remaining: int, cloners: list[Cloner],
               joined: set[str]) -> Finding | None:
    """גל של פס ייצור הסתיים. סוגר את ההחלטה של הגל הקודם (מי הצטרף לגל הזה בפועל), ואם נשאר מה לכתוב --
    מחליט מי נשאר דלוק לגל הבא. פעם אחת לגל (``point`` = הגל). לעולם אינו זורק."""
    try:
        mode = _mode(conn)
        if mode == OFF:
            return None
        names = {c.mac: c.name for c in cloners}
        came = ", ".join(sorted(names.get(m, m) for m in joined)) or "אף משכפל"
        for d in _open(conn, round_id, RULE_POWER, POWER):
            if d.get("point") == wave_id:
                continue
            said = [e[len("keep="):] for e in d.get("evidence") or [] if isinstance(e, str) and e.startswith("keep=")]
            match = "תואם" if set(said) == {names.get(m, m) for m in joined} else "לא תואם"
            memory.resolve(conn, [d["id"]], f"בגל הבא הצטרפו: {came} ({match} למה שהמוח אמר)")
        if remaining <= 0:
            return None
        if any(d.get("point") == wave_id for d in memory.decisions(conn, kind="round", key=round_id,
                                                                   rule=RULE_POWER, limit=64)):
            return None
        f = power(remaining, cloners)
        memory.record(conn, mode=mode, rule=RULE_POWER, action=POWER, verdict=f.verdict, reason=f.reason,
                      evidence=f.evidence + tuple(f"keep={names[m]}" for m in f.keep), point=wave_id,
                      round_id=round_id, entity=("round", round_id))
        _log(mode, RULE_POWER, POWER, round_id, f)
        return f
    except Exception:                           # noqa: BLE001 -- הסבב ממשיך גם כשהמוח נכשל
        log.exception("brain: B24 after %s failed", wave_id)
        return None


#: סבבים שהמטרה שלהם כבר נרשמה בתהליך הזה -- כדי לא לקרוא את ה-DB בכל דופק. אחרי אתחול נבדק שוב פעם אחת.
_decided: set[str] = set()


def forget() -> None:
    """לבדיקות: התהליך מתחיל בלי סבבים שהוכרעו."""
    _decided.clear()


def decide_objective(conn, *, round_id: str, target: int, drawers: int | None, cloners: int) -> Finding | None:
    """B38: פעם אחת לסבב, כשהגל הראשון פתוח. לעולם אינו זורק."""
    if round_id in _decided:
        return None
    try:
        mode = _mode(conn)
        if mode == OFF:
            return None
        _decided.add(round_id)
        if any(d["action"] == OBJECTIVE
               for d in memory.decisions(conn, kind="round", key=round_id, rule=RULE_LINE, limit=64)):
            return None
        f = objective(target, drawers, cloners)
        memory.record(conn, mode=mode, rule=RULE_LINE, action=OBJECTIVE, verdict=f.verdict, reason=f.reason,
                      evidence=f.evidence, round_id=round_id, entity=("round", round_id),
                      outcome="נרשם בלבד: אף קוד עוד אינו צורך את המטרה")
        _log(mode, RULE_LINE, OBJECTIVE, round_id, f)
        return f
    except Exception:                           # noqa: BLE001
        log.exception("brain: B38 on %s failed", round_id)
        return None


def on_ask(conn, *, round_id: str, mac: str, name: str, incomplete: int) -> Finding | None:
    """B42: נרשמה שאלה על משכפל שכבה באמצע סבב של פס ייצור. המוח היה עונה לבד. לעולם אינו זורק."""
    try:
        mode = _mode(conn)
        if mode == OFF:
            return None
        f = Finding(REWRITE, f"בפס ייצור אין שאלות: {name} חזר, והסבב ממשיך -- {incomplete} דיסקים שלא הושלמו"
                             " נכתבים שוב (K11 מחליט אם מהנקודה או מההתחלה)",
                    evidence=(f"machine={name}", f"incomplete={incomplete}"))
        memory.record(conn, mode=mode, rule=RULE_LINE, action=ANSWER, verdict=f.verdict, reason=f.reason,
                      evidence=f.evidence, round_id=round_id, entity=("machine", mac))
        _log(mode, RULE_LINE, ANSWER, round_id, f)
        return f
    except Exception:                           # noqa: BLE001
        log.exception("brain: B42 on %s failed", round_id)
        return None


def on_answer(conn, *, round_id: str, choice: str, origin: str) -> None:
    """B42: המפעיל ענה. כל החלטה פתוחה של B42 בסבב מקבלת את התשובה בפועל. לעולם אינו זורק."""
    try:
        rows = [d["id"] for d in memory.decisions(conn, round_id=round_id, rule=RULE_LINE, limit=64)
                if d["action"] == ANSWER and d["outcome_state"] == "open"]
        if rows:
            same = "תואם" if choice == "rewrite" else "לא תואם"
            memory.resolve(conn, rows, f"המפעיל ענה {choice} ({origin}) -- {same} למה שהמוח היה עונה")
    except Exception:                           # noqa: BLE001
        log.exception("brain: B42 outcome on %s was not recorded", round_id)
