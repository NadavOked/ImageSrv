"""הכלל האחד: איזה צבע לדיסק, והאם מותר להעתיק ממנו (מקור) ולכתוב עליו (יעד) — #2848.

הכרעת נדב 11/11 — **אין הבדל בין השרת, הקונסולה, מחשב הבנייה ומחשב השיכפול**:

| מצב | צבע | מקור | יעד |
|---|---|---|---|
| SMART תקין | ירוק | ✅ | ✅ |
| SMART חצי תקין | כתום | ✅ | ✅ |
| נכשל בשיכפול הקודם / כתיבה לא הושלמה | צהוב | ❌ | ✅ |
| חיבור לא תקין (רשומת כשל שסיבתה כבל/חריץ, #3008) | צהוב | ❌ | ✅ |
| SMART לא תקין | אדום | ✅ | ❌ |
| איטי **בסבב הזה** | אדום | ✅ | ❌ |
| בעיית SATA (הקישור התאפס / המגירה התנתקה) | אדום | ✅ | ❌ |

**#3591 (נדב 17/11): מהירות קישור SATA אינה סיבה לצבע ולא לחסימה -- לא 1.5, לא 3.0 ולא 6.0.** "הקישור 3.0 מתוך 6.0" הוא
מידע על המחשב הישן שלנו, לא כבל גרוע. הקישור נרשם כ"סיבה לאיטיות" רק כשהוא באמת צוואר הבקבוק מול קיבולת הרשת שנמדדה
(המוח, #3591 חלק ב'). מה שכן ראיה לכבל: `sata_issue` (איפוס קישור / ניתוק מגירה) ו-CRC שעולה בזמן כתיבה.

שתי ההרשאות נפרדות: דיסק אדום **וגם** צהוב אינו מקור ואינו יעד, והצבע שלו אדום.
"איטי" הוא של הסבב שבו נמדד בלבד: סבב חדש מודד מחדש, ומדידה מסבב קודם אינה עובדה כאן.

**#3666 (הכרעת נדב 18/11, אפיק #3653): הטבלה היא קובץ אחד** -- ``agent/lib/data/disk-rule.tsv`` -- והיא קבוצה (ג): מדיניות
קבועה, בטיחות קשיחה (דיסק אדום לא נכתב). הקובץ הזה רק טוען אותה ומחיל אותה (`judge`); **מי שמחליט מה הדיסק הוא המוח**
(``server/brain/diskcolor.py``, K3/DISK_COLOR): הוא קורא ל-`judge_disk`, רושם את הסיווג והראיות ב-``brain_decisions``, והקונסולה
והגואי מקבלים ממנו את הצבע והסיבה מוכנים ואינם מחשבים אותם. אין עותק של הטבלה ב-JS וב-C; סוכן במצב בלי שרת (שיכפול מקומי,
אין מוח) קורא את אותו קובץ (``agent/lib/smart/diskrule.sh``).

הקלט (`facts`) הוא שמות פשוטים: ``smart`` (ok/warn/fail/failed_last/unchecked), ``failed_last``, ``cable_failed`` ו-``write_incomplete``,
``sata_issue``, ``write_slow`` (בוליאניים). שדה חסר = לא ידוע = לא מחמיר.

**#3008 (נדב 12/11, הוכרע 23/11): כבל/חריץ הוא של החיבור ולא של הדיסק.** רשומת כשל שסיבתה ``cable`` אינה ``failed_last`` אלא
``cable_failed``: אותו צבע (צהוב) ואותן הרשאות, אבל המילים הן "חיבור לא תקין", ולא "נכשל בשיכפול הקודם" שמטיל את האשמה על הדיסק.
כשל של הדיסק עצמו (סיבה אחרת, או ``smart=failed_last``) גובר: דיסק שנכשל באמת לא נעלם מאחורי כבל.
"""

from __future__ import annotations

from server.paths import AGENT_LIB_DATA

TABLE = AGENT_LIB_DATA / "disk-rule.tsv"
COLUMNS = ("kind", "word", "fact", "color", "short_he", "he")


def _load(path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) != len(COLUMNS):
            raise ValueError(f"{path.name}: {len(cells)} columns, not {len(COLUMNS)}: {line[:60]!r}")
        rows.append({k: (None if v == "-" else v) for k, v in zip(COLUMNS, cells)})
    if not rows or rows[-1]["kind"] != "smart" or rows[-1]["fact"] != "*":
        raise ValueError(f"{path.name}: the last row must be the smart default (fact *): not read is not green")
    return rows


ROWS = _load(TABLE)
#: הסיבות לפי סדר הקדימות -- הראשונה שחלה היא שנאמרת. אדום: לא יעד; צהוב: לא מקור.
RED = tuple(r["word"] for r in ROWS if r["kind"] == "red")
YELLOW = tuple(r["word"] for r in ROWS if r["kind"] == "yellow")
#: הטקסט של כל סיבה -- **אותו טקסט** בסירוב של השרת, בקונסולה ובגואי: הוא יוצא מכאן מוכן, ואין לו עותק.
REASON_HE = {r["word"]: r["he"] for r in ROWS if r["kind"] in ("red", "yellow")}
#: הסיבה בקצרה -- לכרטיס המכונה.
REASON_SHORT_HE = {r["word"]: r["short_he"] for r in ROWS if r["kind"] in ("red", "yellow")}


def _holds(fact: str, facts: dict) -> bool:
    """`name` = בוליאנית נכונה, `name=value` = שווה, `a|b` = אחת מהן, `*` = תמיד. עובדה חסרה אינה מחמירה."""
    for alt in fact.split("|"):
        name, eq, value = alt.partition("=")
        if alt == "*" or (facts.get(name) == value if eq else facts.get(name) is True):
            return True
    return False


def judge(facts: dict) -> dict:
    """`{"color", "source", "target", "reason", "source_reason", "target_reason"}`.

    `color`: red / yellow / orange / green / grey (grey = SMART לא נבדק — לא ירוק, עיקרון 5).
    `reason` — הסיבה של הצבע (אדום או צהוב), None בשאר. `target_reason` — למה אינו יעד,
    `source_reason` — למה אינו מקור; None = מותר."""
    first = {kind: next((r for r in ROWS if r["kind"] == kind and _holds(r["fact"], facts)), None)
             for kind in ("red", "yellow")}
    smart = next(r for r in ROWS if r["kind"] == "smart" and _holds(r["fact"], facts))   # השורה האחרונה היא `smart` + `*`, תמיד חלה
    red = first["red"]["word"] if first["red"] else None
    yellow = first["yellow"]["word"] if first["yellow"] else None
    color = "red" if red else "yellow" if yellow else smart["color"]
    return {"color": color, "source": yellow is None, "target": red is None,
            "reason": red or yellow, "source_reason": yellow, "target_reason": red,
            #: #3666: **כל** הסיבות שחלות, לפי סדר הטבלה -- `reason` היא הראשונה בלבד (זו שנקבע לפיה הצבע).
            "reasons": [r["word"] for r in ROWS if r["kind"] in ("red", "yellow") and _holds(r["fact"], facts)]}


def facts_of(disk: dict, *, failed_last: bool = False, readonly: bool = False,
             write_slow: bool = False, cable_failed: bool = False) -> dict:
    """העובדות מרשומת דיסק של ה-hello (או מ-`drawer_list`, שנושא את אותם שדות).

    `failed_last` — לשרת יש רשומת כשל פתוחה על הדיסק (`disk_failures.open_for`); `readonly` —
    רשומה כזו שסיבתה קריאה-בלבד (#1742/#1760): אדום כמו SMART נכשל, גם לפני שה-hello אומר זאת;
    `write_slow` — בדיקת הכתיבה **של הסבב הזה** נפסלה. שניהם מהקורא: ה-hello אינו נושא אותם."""
    sata = disk.get("sata_issues")
    return {
        "smart": "fail" if readonly else disk.get("smart") or "unchecked",
        "failed_last": bool(failed_last),
        "cable_failed": bool(cable_failed),
        "write_incomplete": disk.get("write_mark") == "incomplete",
        "sata_issue": isinstance(sata, list) and any(w in ("link_reset", "drawer_gone") for w in sata),
        "write_slow": bool(write_slow),
    }


def failures_of(failures: list[dict], disk: dict) -> dict:
    """`{"failed_last", "readonly", "cable_failed"}` של הדיסק לפי רשומות `open_for` של המכונה: אותו סידורי, או
    אותו חריץ (`port` שאינו None — רק של המכונה הזו). `readonly` אדום ולא צהוב (#1742); `cable` הוא של החיבור, לא
    `failed_last` של הדיסק (#3008)."""
    serial, port = disk.get("serial"), disk.get("port")
    out = {"failed_last": False, "readonly": False, "cable_failed": False}
    for f in failures or []:
        if (serial and f.get("serial") == serial) or (port is not None and f.get("port") == port):
            out[{"readonly": "readonly", "cable": "cable_failed"}.get(f.get("cause"), "failed_last")] = True
    return out


def judge_disk(disk: dict, failures: list[dict] | None = None, *, write_slow: bool = False) -> dict:
    """`judge` על רשומת דיסק אחת: העובדות שלה, רשומות הכשל של המכונה (`open_for`) ובדיקת
    הכתיבה של הסבב הזה. `reason_he` / `source_reason_he` / `target_reason_he` — הטקסט."""
    got = judge(facts_of(disk, **failures_of(failures or [], disk), write_slow=write_slow))
    for key in ("reason", "source_reason", "target_reason"):
        got[key + "_he"] = REASON_HE.get(got[key]) if got[key] else None
        got[key + "_short_he"] = REASON_SHORT_HE.get(got[key]) if got[key] else None   # #3666: the card's words come ready
    got["reasons"] = [{"word": w, "he": REASON_HE[w], "short_he": REASON_SHORT_HE[w]} for w in got["reasons"]]
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
    from server.brain import diskcolor   # noqa: PLC0415 -- the brain imports this module; the classification is its (#3666)
    return diskcolor.decide(conn, disk, machine_failures(conn, mac, disks), point="source_refusal", mac=mac)["source_reason_he"]
