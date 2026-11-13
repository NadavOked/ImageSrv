"""הכלל האחד: איזה צבע לדיסק, והאם מותר להעתיק ממנו (מקור) ולכתוב עליו (יעד) — #2848.

הכרעת נדב 11/11 — **אין הבדל בין השרת, הקונסולה, מחשב הבנייה ומחשב השיכפול**:

| מצב | צבע | מקור | יעד |
|---|---|---|---|
| SMART תקין | ירוק | ✅ | ✅ |
| SMART חצי תקין | כתום | ✅ | ✅ |
| נכשל בשיכפול הקודם / כתיבה לא הושלמה | צהוב | ❌ | ✅ |
| SMART לא תקין | אדום | ✅ | ❌ |
| איטי **בסבב הזה** | אדום | ✅ | ❌ |
| קישור SATA איטי / בעיית SATA | אדום | ✅ | ❌ |

שתי ההרשאות נפרדות: דיסק אדום **וגם** צהוב אינו מקור ואינו יעד, והצבע שלו אדום.
"איטי" הוא של הסבב שבו נמדד בלבד: סבב חדש מודד מחדש, ומדידה מסבב קודם אינה עובדה כאן.

**אותה טבלה בשלוש שפות** — `diskRule` ב-`server/static/console.js` ו-`disk_rule()` ב-
`native-gui/src/disk_rule.c` — ו-`tests/test_disk_rule_parity_2848.py` מריץ את שלושתן על אותה
רשימת דיסקים ודורש הכרעה זהה, כולל הטקסט. מי ששינה צד אחד בלבד — נופל שם.

הקלט (`facts`) הוא שמות פשוטים, אותם בשלוש השפות:
``smart`` (ok/warn/fail/failed_last/unchecked), ``failed_last`` ו-``write_incomplete``,
``link_slow``, ``sata_issue``, ``write_slow`` (בוליאניים). שדה חסר = לא ידוע = לא מחמיר.
"""

from __future__ import annotations

#: הסיבות לפי סדר הקדימות — הראשונה שחלה היא שנאמרת. אדום: לא יעד; צהוב: לא מקור.
RED = ("smart_fail", "link_slow", "sata_issue", "write_slow")
YELLOW = ("failed_last", "write_incomplete")

#: הטקסט של כל סיבה — **אותו טקסט** בסירוב של השרת, בקונסולה ובגואי (טסט ה-parity).
REASON_HE = {
    "smart_fail": "SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת",   # smart.sh SMART_RED_HE
    "link_slow": "קישור SATA איטי — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו",
    "sata_issue": "בעיית SATA (הקישור התאפס או המגירה התנתקה) — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו",
    "write_slow": "נפסל כאיטי בבדיקת הכתיבה בסבב הזה — לא כותבים עליו בסבב הזה; אפשר להעתיק ממנו",
    "failed_last": "נכשל בשיכפול הקודם — לא מעתיקים ממנו; אפשר לכתוב עליו",
    "write_incomplete": "כתיבה לא הושלמה — לא מעתיקים ממנו; אפשר לכתוב עליו",
}


def judge(facts: dict) -> dict:
    """`{"color", "source", "target", "reason", "source_reason", "target_reason"}`.

    `color`: red / yellow / orange / green / grey (grey = SMART לא נבדק — לא ירוק, עיקרון 5).
    `reason` — הסיבה של הצבע (אדום או צהוב), None בשאר. `target_reason` — למה אינו יעד,
    `source_reason` — למה אינו מקור; None = מותר."""
    smart = facts.get("smart")
    on = {
        "smart_fail": smart == "fail",
        "link_slow": facts.get("link_slow") is True,
        "sata_issue": facts.get("sata_issue") is True,
        "write_slow": facts.get("write_slow") is True,
        "failed_last": facts.get("failed_last") is True or smart == "failed_last",
        "write_incomplete": facts.get("write_incomplete") is True,
    }
    red = next((r for r in RED if on[r]), None)
    yellow = next((r for r in YELLOW if on[r]), None)
    color = ("red" if red else "yellow" if yellow else "orange" if smart == "warn"
             else "green" if smart == "ok" else "grey")
    return {"color": color, "source": yellow is None, "target": red is None,
            "reason": red or yellow, "source_reason": yellow, "target_reason": red}


def facts_of(disk: dict, *, failed_last: bool = False, readonly: bool = False,
             write_slow: bool = False) -> dict:
    """העובדות מרשומת דיסק של ה-hello (או מ-`drawer_list`, שנושא את אותם שדות).

    `failed_last` — לשרת יש רשומת כשל פתוחה על הדיסק (`disk_failures.open_for`); `readonly` —
    רשומה כזו שסיבתה קריאה-בלבד (#1742/#1760): אדום כמו SMART נכשל, גם לפני שה-hello אומר זאת;
    `write_slow` — בדיקת הכתיבה **של הסבב הזה** נפסלה. שניהם מהקורא: ה-hello אינו נושא אותם."""
    gbps, top = disk.get("link_gbps"), disk.get("link_max_gbps")
    # `link_state` של השרת (drawer_list) כשיש; ב-hello — המספרים עצמם (linkSlow של הקונסולה, אותו דבר)
    link_slow = disk.get("link_state") == "slow" if disk.get("link_state") else (
        _num(gbps) is not None and _num(top) is not None and gbps < top)
    sata = disk.get("sata_issues")
    return {
        "smart": "fail" if readonly else disk.get("smart") or "unchecked",
        "failed_last": bool(failed_last),
        "write_incomplete": disk.get("write_mark") == "incomplete",
        "link_slow": bool(link_slow),
        "sata_issue": isinstance(sata, list) and any(w in ("link_reset", "drawer_gone") for w in sata),
        "write_slow": bool(write_slow),
    }


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def failures_of(failures: list[dict], disk: dict) -> dict:
    """`{"failed_last", "readonly"}` של הדיסק לפי רשומות `open_for` של המכונה: אותו סידורי, או
    אותו חריץ (`port` שאינו None — רק של המכונה הזו). `readonly` אדום ולא צהוב (#1742)."""
    serial, port = disk.get("serial"), disk.get("port")
    out = {"failed_last": False, "readonly": False}
    for f in failures or []:
        if (serial and f.get("serial") == serial) or (port is not None and f.get("port") == port):
            out["readonly" if f.get("cause") == "readonly" else "failed_last"] = True
    return out


def judge_disk(disk: dict, failures: list[dict] | None = None, *, write_slow: bool = False) -> dict:
    """`judge` על רשומת דיסק אחת: העובדות שלה, רשומות הכשל של המכונה (`open_for`) ובדיקת
    הכתיבה של הסבב הזה. `reason_he` / `source_reason_he` / `target_reason_he` — הטקסט."""
    got = judge(facts_of(disk, **failures_of(failures or [], disk), write_slow=write_slow))
    for key in ("reason", "source_reason", "target_reason"):
        got[key + "_he"] = REASON_HE.get(got[key]) if got[key] else None
    return got


def machine_failures(conn, mac: str, disks: list) -> list[dict]:
    """רשומות הכשל הפתוחות שה-hello של המכונה עונה עליהן (`disk_failures.open_for`) —
    אותן רשומות שמהן הסוכן מציב `failed_last`."""
    from server.disks import disk_failures   # noqa: PLC0415 -- disk_failures → db; this module stays light
    return disk_failures.open_for(conn, mac, [d for d in disks or [] if isinstance(d, dict)])


def source_refusal(conn, mac: str, dev: str) -> str | None:
    """למה הדיסק `dev` של המכונה אינו מקור (קליטה, הפצה ישירה) — None = מותר, או שהדיסק לא
    דווח (הקורא כבר מסרב לדיסק שלא דווח, או שהסוכן מכריע לפי הסידורי; לא נוחש כאן)."""
    from server.rounds import room   # noqa: PLC0415 -- room imports this module
    disks = [d for d in room._disks(conn, mac) if isinstance(d, dict)]
    disk = next((d for d in disks if isinstance(d.get("dev"), str)
                 and d["dev"].removeprefix("/dev/") == dev.removeprefix("/dev/")), None)
    if disk is None:
        return None
    return judge_disk(disk, machine_failures(conn, mac, disks))["source_reason_he"]
