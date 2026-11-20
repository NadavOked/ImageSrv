"""#3566 (מחקר המוח 16/11): שער SMART לפי ארבעת המדדים שהמחקרים הוכיחו -- 5, 187, 197, 198 -- ב**צל**.

**מה המחקר אמר** (Backblaze, Google FAST'07; המספרים עוד לא אומתו במקור): ערך גדול מ-0 באחד הארבעה, או עלייה שלו בין שני סבבים,
הוא סימן; **מדד שחסר הוא "לא ידוע", לא "תקין"** (עיקרון 5). ל-23% עד 56% מהדיסקים שנכשלו לא היה אף סימן, ולכן "אין סימן" אינו "בריא".

**מה כבר קיים** (ולכן מה אין כאן): ערך מעל 0 ב-5/187/197/198 כבר כתום (`smart-knowledge.tsv`, בסוכן), ועלייה מאז הסבב הקודם כבר כתומה
(`smart_history.learned` -> `critical_rose`). **מה שלא קיים:** דיסק ש-`-H` אמר PASSED והטבלה נקראה, אבל אחד מארבעת המדדים לא הודפס בה
(Samsung 860 EVO מדפיס 5 ו-187 ואינו מדפיס 197/198), נשאר ירוק -- `_smart_int` של הסוכן מחזיר 0 למדד שחסר.

**למה צל.** הפיכת דיסק ירוק לאפור משנה צבע שהמפעיל רואה, ומשכפלי המכללה הם Samsung SSD: רובם היו הופכים לאפור. זו החלטה של נדב.
לכן המודול הזה **לא נוגע בצבע**: הוא נקרא מ-`diskcolor.decide` רק על דיסק שהטבלה צבעה ירוק, ורושם (`K3/SMART_GATE`, מצב צל) מה היה קורה.
המדד נקבע לפי המזהה בלבד כשה-hello לא נשא שם יצרן; כשיש שם, הוא חייב להיות המילה של אותו מדד בטבלת הידע (198 של OCZ הוא `Host_Reads_GiB`).
NVMe אין לו מיפוי למדדים האלה (פתוח ב-#3566): כל הארבעה "חסרים".
"""

from __future__ import annotations

import json

from server.disks import smart_history, smart_names

#: מזהה -> המילה של טבלת הידע שהמדד הזה אומר.
METRICS = {5: "reallocated", 187: "reported_uncorrectable", 197: "pending_sectors", 198: "uncorrectable"}
MISSING, ZERO, POSITIVE, ROSE = "missing", "zero", "positive", "rose"


def _raw(attrs: object) -> dict[int, int | None]:
    """הערך הגולמי של כל אחד מהארבעה; ``None`` = לא הודפס, או שם יצרן שאינו המדד הזה, או ערך שלא נקרא."""
    out: dict[int, int | None] = {i: None for i in METRICS}
    for i, r in smart_history._rows(attrs).items():
        if i in METRICS and (r["vendor"] is None or smart_names.word(i, r["vendor"]) == METRICS[i]):
            out[i] = r["raw"]
    return out


def judge(attrs: object, before: object = None) -> dict | None:
    """``{"color": orange|grey|green, "metrics": {id: state}}``, או ``None`` = ה-SMART של הדיסק לא נקרא כאן (אין מה לשפוט).

    ``before`` = המדידה של הסבב הקודם (`attrs_json` כמילון), או ``None`` = אין השוואה. כתום: מדד מעל 0 או שעלה. אפור: אין כתום,
    ומדד אחד לפחות חסר. ירוק: ארבעתם נקראו, כולם 0, ולא עלו."""
    if not isinstance(attrs, dict):
        return None
    now, was = _raw(attrs), _raw(before)
    states = {}
    for i, v in now.items():
        states[i] = (MISSING if v is None else ROSE if was[i] is not None and v > was[i] else POSITIVE if v > 0 else ZERO)
    color = ("orange" if any(s in (POSITIVE, ROSE) for s in states.values())
             else "grey" if MISSING in states.values() else "green")
    return {"color": color, "metrics": states}


def previous_attrs(conn, serial: str | None) -> dict | None:
    """המדידה האחרונה של הסידורי הזה, או ``None``. קריאה שנכשלה = "אין השוואה", לא "לא עלה"."""
    try:
        row = smart_history.previous(conn, serial)
        return json.loads(row["attrs_json"] or "{}") if row is not None else None
    except Exception:                           # noqa: BLE001
        return None
