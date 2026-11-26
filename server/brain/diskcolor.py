"""#3666 (אפיק #3653, הכרעת נדב 18/11), K3 / ``DISK_COLOR``: המוח מסווג דיסק -- איזה צבע, ולמה, ועל איזו ראיה.

**מה זה.** צבע הדיסק נקבע היום ב-4 מקומות (`disk_rule.py`, הקונסולה, הגואי והסוכן), וכל אחד מהם קבע אותו בעצמו. מכאן
המקום שמחליט הוא אחד: `decide`. הוא מחיל על הדיסק את הטבלה האחת (``agent/lib/data/disk-rule.tsv``, דרך
`disk_rule.judge_disk`), **רושם את הסיווג כהחלטה** ב-``brain_decisions`` ביחד עם הראיות שעליהן הוא נשען, ומחזיר את הצבע,
ההרשאות (מקור, יעד) והסיבה (וגם המשפט בעברית) **מוכנים**. הקונסולה והגואי מציגים אותם; הם אינם מחשבים.

**הטבלה היא קבוצה (ג), בטיחות קשיחה -- והמוח אינו משנה אותה.** דיסק אדום לא נכתב; דיסק צהוב לא משמש מקור; "לא נקרא אינו
תקין" (ללא SMART = אפור, לעולם לא ירוק; עיקרון 5). המוח אינו מחליט "אולי כן" בכל אלה: הוא מגיש את הסיווג של הטבלה, ומי
שרוצה לשנות מדיניות משנה את הקובץ (ובשער הרגיל), לא כלל כאן. לכן ההגשה **אינה תלויה במתג המוח**: ``brain_veto_mode`` שולט רק
ברישום. ``off`` = הסיווג עדיין מוגש (דיסק אדום לא הופך ללא-אדום כי מישהו כיבה את הרישום), ולא נרשם דבר.

**מה נרשם.** שורה על ישות ``disk`` (הסידורי) **רק כשהסיווג שלה משתנה** מהשורה האחרונה של אותו דיסק (כמו שאר הכללים):
פסק = ``DISK_RED``/``DISK_YELLOW``/``DISK_ORANGE``/``DISK_GREEN``/``DISK_NOT_CHECKED``, והסיבה בעברית. הראיות = העובדות שהסיווג קרא: פסק ה-SMART
והסיבה שלו, רשומת כשל פתוחה ומה סיבתה, סימן "כתיבה לא הושלמה", בעיות SATA, "איטי בסבב הזה". **עובדה שלא נקראה נרשמת
``null``**, לא ``false`` (עיקרון 5): `sata_issues` שהסוכן לא שלח אינו "אין בעיית SATA".

**מה לא כאן.** הטיפול בעובדות עצמן (פסק ה-SMART בסוכן, הסלמת רשומת הכשל, "נלמד" והחום, סימון F1 אוטומטי) עוד לא עבר:
הן נשארות חושים שמזינים את הסיווג, ונרשמות בראיות. ``write_slow`` בא מהקורא (בדיקת הכתיבה של הסבב הזה).

כל מה שכאן לעולם אינו זורק אל הקורא: כשל ברישום הוא שורת לוג, והסיווג מוגש כרגיל.
"""

from __future__ import annotations

import logging

from server.brain import memory, smartgate
from server.disks import disk_rule

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "K3", "DISK_COLOR"
#: מי אמר: נושא כל סיווג שמוגש, כדי שמי שמקבל אותו יוכל להבחין בו מסיווג שלא עבר דרך המוח (`rule` חסר = לא נבדק).
BY = f"{RULE}/{ACTION}"
#: מפתחות ייחודיים לכלל הזה (`say.VERDICT_HE` גלובלי, ו-"RED" שם הוא של B62: "אדום: איטי עכשיו")
NOT_CHECKED = "DISK_NOT_CHECKED"
VERDICT = {"red": "DISK_RED", "yellow": "DISK_YELLOW", "orange": "DISK_ORANGE", "green": "DISK_GREEN", "grey": NOT_CHECKED}
COLOR_HE = {"red": "אדום", "yellow": "צהוב", "orange": "כתום", "green": "ירוק", "grey": "לא נבדק"}
#: הסיבה של צבע שאינו אדום או צהוב -- ממה שה-SMART אמר, לא מהטבלה (שם אין לו משפט).
SMART_HE = {
    "orange": "SMART חצי תקין: סימן מוקדם, והדיסק עובד",
    "green": "SMART תקין",
    "grey": "SMART לא נבדק: זה אינו תקין ואינו נכשל (עיקרון 5)",
}
#: #3566: שער ארבעת המדדים (5/187/197/198) -- ב**צל**. נרשם כשהוא חולק על דיסק ירוק, ואינו משנה את הצבע המוצג (החלטה של נדב).
GATE_ACTION = "SMART_GATE"
GATE_VERDICT = {"orange": "GATE_ORANGE", "grey": "GATE_NOT_KNOWN"}
GATE_HE = {
    "orange": "ארבעת מדדי ה-SMART (5/187/197/198): אחד מהם מעל 0 או עלה מאז הסבב הקודם -- הצבע המוצג נשאר ירוק",
    "grey": "ארבעת מדדי ה-SMART (5/187/197/198): מדד אחד לפחות לא נקרא -- 'לא ידוע', לא 'תקין'. הצבע המוצג נשאר ירוק",
}
GATE_OUTCOME_HE = "צל: הצבע המוצג לא שונה. הפיכת ירוק לכתום או לאפור היא החלטה של נדב"
OUTCOME_HE = "הסיווג הוגש לקונסולה ולגואי כמו שהוא (טבלה קבועה, קבוצה ג): נרשם ואינו תלוי במתג"


def reason_he(got: dict, disk: dict) -> str:
    """הסיבה בעברית של הצבע: המשפט של הטבלה לאדום ולצהוב, ופסק ה-SMART (עם הסיבה שלו) לשאר."""
    if got["reason"]:
        return got["reason_he"]
    line = SMART_HE[got["color"]]
    why = disk.get("smart_reason")
    return f"{line} ({why})" if got["color"] == "orange" and isinstance(why, str) and why else line


def evidence(disk: dict, failures: list[dict] | None, write_slow: bool) -> dict:
    """העובדות שהסיווג קרא. ``null`` = לא נקרא (שדה שהסוכן לא שלח), לעולם לא ``false``."""
    own = disk_rule.failures_of(failures or [], disk)
    sata = disk.get("sata_issues")
    return {"smart": disk.get("smart") or "unchecked", "smart_reason": disk.get("smart_reason") or None,
            "failed_last": own["failed_last"], "cable_failed": own["cable_failed"], "readonly": own["readonly"],
            "write_mark": disk.get("write_mark") if isinstance(disk.get("write_mark"), str) else None,
            "sata_issues": sata if isinstance(sata, list) else None, "write_slow": bool(write_slow),
            "table": disk_rule.TABLE.name}


def classify(disk: dict, failures: list[dict] | None = None, *, write_slow: bool = False) -> dict:
    """הסיווג, טהור: `disk_rule.judge_disk` + מי אמר (``by``) + הצבע בעברית (``color_he``) + הפסק של ההחלטה (``verdict``)."""
    got = disk_rule.judge_disk(disk, failures, write_slow=write_slow)
    return {**got, "by": BY, "verdict": VERDICT[got["color"]], "color_he": COLOR_HE[got["color"]]}


def _serial(disk: dict) -> str | None:
    value = disk.get("serial")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _unchanged(conn, serial: str, verdict: str, reason: str) -> bool:
    """השורה האחרונה של הדיסק הזה כבר אומרת את זה. קריאה שנכשלה = "לא ידוע": רושמים (כפילות עדיפה על חור)."""
    try:
        for d in memory.decisions(conn, kind="disk", key=serial, rule=RULE, limit=12):
            if d["action"] == ACTION:
                return d["verdict"] == verdict and d["reason"] == reason
    except Exception:                           # noqa: BLE001
        log.exception("brain: the last %s of %s was not read", ACTION, serial)
    return False


def _shadow_gate(conn, disk: dict, serial: str, got: dict, round_id: str | None, point: str) -> None:
    """#3566: דיסק שהטבלה צבעה ירוק, והשער של ארבעת המדדים היה צובע אחרת -- נרשם בצל. הצבע המוצג אינו משתנה."""
    if got["color"] != "green":
        return
    gate = smartgate.judge(disk.get("smart_attrs"), smartgate.previous_attrs(conn, serial))
    if gate is None or gate["color"] == "green":
        return
    verdict, reason = GATE_VERDICT[gate["color"]], GATE_HE[gate["color"]]
    try:
        for d in memory.decisions(conn, kind="disk", key=serial, rule=RULE, limit=12):
            if d["action"] == GATE_ACTION:
                if d["verdict"] == verdict and d["evidence"] and d["evidence"][0].get("metrics") == {str(i): s for i, s in gate["metrics"].items()}:
                    return
                break
    except Exception:                           # noqa: BLE001 -- קריאה שנכשלה = "לא ידוע": רושמים
        log.exception("brain: the last %s of %s was not read", GATE_ACTION, serial)
    memory.record(conn, mode="shadow", rule=RULE, action=GATE_ACTION, verdict=verdict, reason=reason, point=point or None,
                  evidence=[{"metrics": {str(i): s for i, s in gate["metrics"].items()}, "displayed": got["color"]}],
                  round_id=round_id, entity=("disk", serial), outcome=GATE_OUTCOME_HE)


def decide(conn, disk: dict, failures: list[dict] | None = None, *, write_slow: bool = False, point: str = "",
           round_id: str | None = None, mac: str | None = None) -> dict:
    """הסיווג המוגש (`classify`), ונרשם כהחלטה כשהוא השתנה. לעולם אינו זורק, וההגשה אינה תלויה בכשל הרישום או במתג."""
    got = classify(disk, failures, write_slow=write_slow)
    try:
        from server.brain import veto            # כמו ב-`slotfill`: veto טוען את החלטות המוח
        serial, mode = _serial(disk), veto.mode(conn)
        if serial is None or mode == veto.OFF:
            return got
        _shadow_gate(conn, disk, serial, got, round_id, point)
        reason = reason_he(got, disk)
        if _unchanged(conn, serial, got["verdict"], reason):
            return got
        facts = {**evidence(disk, failures, write_slow), "reasons": [r["word"] for r in got["reasons"]], "mac": mac, "port": disk.get("port")}
        memory.record(conn, mode=mode, rule=RULE, action=ACTION, verdict=got["verdict"], reason=reason, point=point or None,
                      evidence=[facts], round_id=round_id, entity=("disk", serial), outcome=OUTCOME_HE)
    except Exception:                           # noqa: BLE001 -- הרישום אינו מפיל הגשה של סיווג
        log.exception("brain: %s of %s was not recorded", ACTION, disk.get("serial"))
    return got
