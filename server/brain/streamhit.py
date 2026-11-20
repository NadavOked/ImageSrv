"""#3064 (X10): הבסיס של "זרם נפגע" -- כמה חתיכות נטושות במעבר נקי -- והשיפוט של כל גל חתיכות מולו, במוח. **בצל:**
נרשם בלבד, ושום דבר לא זז.

**למה:** תנאי המעבר לשלב 3 (Tuning, `server/brain/README.md`) הוא "אין פגיעה בזרם מעבר לבסיס (X10)". בלי בסיס אין
דרך לומר שהתקציב של ה-HTTP (K7) לא פגע בזרם.

**המדידה כבר קיימת, ולא משוכפלת כאן:** `server/learn/netloss.py` (#3164) לומד מכל גל חתיכות את ``abandoned`` (חתיכות
שהמקלט נטש באמצע, `chunkloss.sh`, G7) לכל GB שהמחשבים קיבלו, מול חציון של הגלים הקודמים באותם תנאים (כרטיס, מגירות,
קצב), בלי גלים עם ניתוק (#3141) ובלי גלים שהשרת האט בהם (#1884). זה הכיול של X10 לפי מה שנמדד בכל גל.

**מה המודול הזה מוסיף:**

- **ערך התחלתי** לגלים הראשונים, כש-#3164 עוד "לא נבדק" (פחות מ-``netloss.MIN_WAVES`` גלים דומים): ``START_CHUNKS``
  = 716 חתיכות נטושות למחשב בגל. **המקור:** `research-3064` 1ד (ברזל, 13/11): "במעבר נקי נוטשות 4–716 חתיכות",
  כש-SO_RCVBUF נקטע ל-416KB (T11). הקצה העליון נבחר כדי לא לקרוא "נפגע" לגל שנראה כמו גל נקי שכבר נמדד. אחרי
  התיקון של rmem_max הבסיס הנלמד יירד לבד, כי הוא חציון של הגלים האחרונים.
- **השורה במוח:** ``brain_decisions``, כלל ``X10``, פעולה ``STREAM_HIT``, פעם אחת לגל:
  - ``HIT`` -- מעל הבסיס: הנלמד (``state = jump`` של #3164), או הערך ההתחלתי כשאין בסיס.
  - ``CLEAN`` -- בתוך הבסיס.
  - ``NOT_CLEAN`` -- גל עם ניתוק, או גל שהשרת האט בו: לא שופטים את הזרם בגל כזה.
  גל ש-#3164 לא מדד (מונה שלא נקרא, גל שלא הסתיים ב-``send_done``) -- אין שורה. "לא נמדד" אינו "נקי".

**החיבור:** `netloss.evaluate_chunk`, ששני הקוראים שלו (סוף השידור והחדר שסוגר את הגל) כבר קוראים. שורה אחת לגל גם
כששניהם מעריכים. המתג הוא ``brain_veto_mode``: ``off`` = לא נרשם. **לעולם אינו זורק.**
"""

from __future__ import annotations

import json
import logging

from server.brain import memory

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "X10", "STREAM_HIT"
OFF = "off"
HIT, CLEAN, NOT_CLEAN = "HIT", "CLEAN", "NOT_CLEAN"
#: research-3064 1ד (ברזל 13/11, לפני התיקון של rmem_max, T11): 4–716 חתיכות נטושות במעבר נקי, למחשב. הקצה העליון.
START_CHUNKS = 716


def _machines(conn, session_id: str) -> int | None:
    row = conn.execute("SELECT decision FROM wave_rates WHERE session_id = ?", (session_id,)).fetchone()
    try:
        roster = (json.loads(row["decision"]) or {}).get("roster") if row is not None else None
    except (TypeError, ValueError):
        return None
    macs = {d.get("mac") for d in roster or [] if isinstance(d, dict) and d.get("mac")}
    return len(macs) or None


def judge(conn, session_id: str, got: dict | None) -> int | None:
    """``got`` = `netloss.assess` של גל חתיכות. מחזיר את מספר השורה, או ``None`` (לא נרשם). לעולם אינו זורק."""
    try:
        if not got or got.get("kind") != "chunk":
            return None
        from server.brain import veto            # noqa: PLC0415 -- המתג הכללי של המוח
        mode = veto.mode(conn)
        if mode == OFF:
            return None
        if any(d["action"] == ACTION for d in memory.decisions(conn, round_id=session_id, rule=RULE, limit=64)):
            return None                          # הגל כבר נשפט: שני קוראים, שורה אחת (דרך `memory`, נקודת הקריאה היחידה)
        state, abandoned, loss = got.get("state"), got.get("abandoned"), got.get("loss")
        machines = _machines(conn, session_id)
        evidence = [f"state={state}", f"abandoned={abandoned}", f"loss_per_gb={loss:.3f}" if loss is not None
                    else "loss_per_gb=unmeasured", f"machines={machines if machines else 'unmeasured'}",
                    f"waves={got.get('waves')}"]
        if state in ("net_gap", "server_disk"):
            verdict, source = NOT_CLEAN, "none"
            reason = ("ניתוק בגל: לא שופטים את הזרם" if state == "net_gap"
                      else "השרת האט את הגל (דיסק הספרייה): לא שופטים את הזרם")
        elif state in ("ok", "jump"):
            source, base = "learned", got.get("baseline")
            verdict = HIT if state == "jump" else CLEAN
            evidence.append(f"baseline_per_gb={base:.3f}" if base is not None else "baseline_per_gb=unmeasured")
            reason = (f"{'הזרם נפגע' if verdict == HIT else 'הזרם נקי'}: {loss:.2f} חתיכות נטושות לכל GB מול בסיס"
                      f" נלמד {base:.2f} ({got.get('waves')} גלים דומים, #3164)")
        elif state == "unchecked" and isinstance(abandoned, int) and machines:
            source, per = "start", abandoned / machines
            verdict = HIT if per > START_CHUNKS else CLEAN
            evidence.append(f"start_chunks={START_CHUNKS}")
            reason = (f"{'הזרם נפגע' if verdict == HIT else 'הזרם נקי'}: {per:.0f} חתיכות נטושות למחשב מול הערך ההתחלתי"
                      f" {START_CHUNKS} (מחקר 1ד, ברזל 13/11) -- עוד אין בסיס נלמד ({got.get('waves')} גלים דומים)")
        else:
            return None                          # לא נמדד מספיק כדי לשפוט: אין שורה, לא "נקי"
        evidence.append(f"source={source}")
        return memory.record(conn, mode=mode, rule=RULE, action=ACTION, verdict=verdict, reason=reason,
                             evidence=evidence, round_id=session_id, entity=("round", session_id),
                             outcome="צל: נרשם בלבד")
    except Exception:                           # noqa: BLE001 -- המוח אינו מפיל את סוף הגל
        log.exception("brain: the stream baseline of %s failed", session_id)
        return None
