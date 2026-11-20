"""#3591 חלק ב'1 -- יכולת כל יציאת SATA, כפי שהמוח למד אותה: לפי **דגם המחשב** ולפי ה-`link_gbps` שנצפה ביציאה **לאורך זמן**,
ולא לפי היכולת של הדיסק.

נדב 17/11: "וזה המוח צריך לדעת ולהכיר". ב-Q77 רק SATA 0 ו-1 הן 6.0 ו-SATA 2 ו-3 הן 3.0 **בחומרה**: יציאה שתמיד 3.0 היא תכונה של
הלוח, לא תקלה. חשד לכבל עולה **רק** כשיציאה שכבר ראתה מהירות גבוהה יורדת ממנה, או כש-CRC עולה (#2846) -- לא מעצם המהירות
(`ports._problem` אינו קורא אותה, #3591 חלק א'). מה שנלמד כאן הוא הקלט של שלושת השימושים של חלק ב': תקרת הקצב של כל יעד
(`ceiling_mbps`), הסיבה לאיטיות כשהיציאה הצוואר, והמלצת החיבור "עדיף 6.0 אם יש פנויה" (#3600).

**המקור:** `port_history` (#1844) -- שורה לכל (גל, מכונה, חיבור) שנמדד בה קישור, כפי שה-hello של המכונה אמר בסוף הגל.
`link_gbps` שם הוא **מה שהיציאה סיפקה בפועל** (האיטי מבין היציאה, הכבל והדיסק); `link_max_gbps` הוא התקרה של הדיסק והבקר ואינו נקרא כאן.

**מה נלמד.** `capability(conn, mac, port)`:

- `gbps` -- **הקישור הגבוה ביותר שנצפה** ביציאה הזו של המכונה הזו, ואם מחשב אחר מאותו **דגם** (`model_slots.model_of`) ראה בה יותר --
  גם הוא. זו ראיה חיובית ליכולת מינימלית: יציאה שראתה 6.0 **יכולה** 6.0. `None` = לא נמדד, ולא "איטי" ולא "תקין".
- `basis` -- `"machine"` (המכונה עצמה ראתה את המקסימום) או `"model"` (מחשב אחר מהדגם); `None` כשאין נתון.
- `known` -- היכולת **ידועה** (לא רק גבול תחתון): נצפו **6.0** (הגבוה ביותר ש-SATA III מגיע אליו), או ש-`MIN_WAVES` גלים קראו בה בדיוק את הערך הגבוה ביותר
  שנצפה (גלים שירדו ממנו אינם עדות ליכולת). יציאה ש"נראתה 3.0 פעם אחת" אינה "3.0 של הלוח": זה יכול להיות כבל שהוריד את המהירות. **`MIN_WAVES` הוא ניחוש מנומק, לא מדידה**
  (עיקרון 5ב): 3 גלים, כמו "כמה פעמים עד שמאמינים", ומוחלף בהכרעה של נדב.
- `waves`, `machines`, `model` -- על מה זה נשען (הערך נוקב במקורו).

**ירידה** (`drop`): הקישור **עכשיו** נמוך מהיכולת שנלמדה, **וגם** נמוך מהתקרה של הדיסק והבקר (`link_max_gbps`): דיסק SATA II ב-3.0 על
יציאה שראתה 6.0 עם דיסק אחר הוא התקין שלו, לא ירידה. זו הראיה היחידה לכבל מהמהירות.

**התקרות** (`ceiling_mbps`): 3.0 ≈ 280MB/s, 6.0 ≈ 550MB/s (נדב 17/11, #3591). **1.5 ≈ 140MB/s הוא הערכה** (חצי מ-3.0), לא מדידה.

**מה נשמר:** `port_history` נשמר 90 יום, ועוד `ports.WINDOW` התצפיות האחרונות לכל יציאה (housekeeping): יציאה שראתה 6.0 לפני יותר מ-90 יום ועוד
`WINDOW` גלים נמוכים אחריה כבר אינה זוכרת אותו. קריאה בלבד: שום דבר כאן אינו כותב, חוסם או משנה מיפוי.
"""

from __future__ import annotations

import sqlite3

from server.disks import model_slots

#: כמה גלים שבהם נקרא הקישור נדרשים לפני ש"הערך הגבוה ביותר שנצפה" נחשב היכולת ולא מקרה. ⚠️ ניחוש מנומק, לא מדידה.
MIN_WAVES = 3
#: הקישור הגבוה ביותר ש-SATA III מגיע אליו: ראייה אחת אליו מספיקה.
TOP_GBPS = 6.0

#: תקרת הקצב של קישור SATA, ב-MB/s. 3.0 ו-6.0: נדב 17/11 (#3591). 1.5: הערכה (חצי מ-3.0), לא מדידה.
CEILING_MBPS: dict[float, int] = {1.5: 140, 3.0: 280, 6.0: 550}


def ceiling_mbps(gbps: object) -> int | None:
    """תקרת הכתיבה של קישור, ב-MB/s, או None כשהמהירות לא נקראה או אינה אחת משלוש ה-SATA. לא מנחשים בין הערכים."""
    if isinstance(gbps, bool) or not isinstance(gbps, (int, float)):
        return None
    return CEILING_MBPS.get(round(float(gbps), 1))


def _seen(conn: sqlite3.Connection, macs: list[str], port: int) -> dict | None:
    """מה שנצפה ביציאה `port` של המכונות `macs`: `{"max", "waves", "machines"}`, או None כשלא נקרא בה קישור מעולם."""
    marks = ",".join("?" * len(macs))
    row = conn.execute(
        f"SELECT MAX(link_gbps) AS top, COUNT(DISTINCT session_id) AS waves, COUNT(DISTINCT mac) AS machines"
        f" FROM port_history WHERE port = ? AND link_gbps IS NOT NULL AND link_gbps > 0 AND mac IN ({marks})",
        (port, *macs)).fetchone()
    return None if row is None or row["top"] is None else {
        "max": float(row["top"]), "waves": row["waves"], "machines": row["machines"]}


def _waves_at(conn: sqlite3.Connection, macs: list[str], port: int, top: float) -> int:
    """בכמה גלים נקרא ביציאה הקישור **הגבוה ביותר** (`top`): גלים שבהם ירד ממנו אינם עדות שזו היכולת."""
    marks = ",".join("?" * len(macs))
    return conn.execute(f"SELECT COUNT(DISTINCT session_id) AS n FROM port_history WHERE port = ?"
                        f" AND link_gbps >= ? AND mac IN ({marks})", (port, top - 0.01, *macs)).fetchone()["n"]


def capability(conn: sqlite3.Connection, mac: str, port: object) -> dict:
    """היכולת שנלמדה של היציאה `port` של `mac` -- ראו את ראש המודול. `{"gbps", "basis", "known", "waves", "machines", "model"}`;
    `gbps` None = לא נמדד. כשל בשאילתה עולה למעלה: "לא הצלחנו לבדוק" אינו "אין נתון"."""
    out = {"gbps": None, "basis": None, "known": False, "waves": 0, "machines": 0, "model": None}
    if isinstance(port, bool) or not isinstance(port, int) or port < 0:
        return out
    mine = _seen(conn, [mac], port)
    model = model_slots.model_of(conn, mac)
    mates = sorted({m for m, name in model_slots.models_by_mac(conn).items() if name == model} - {mac}) if model else []
    theirs = _seen(conn, mates, port) if mates else None
    best = max((s for s in (mine, theirs) if s is not None), key=lambda s: s["max"], default=None)
    if best is None:
        return {**out, "model": model}
    basis = "machine" if mine is not None and mine["max"] >= best["max"] else "model"
    waves = mine["waves"] if basis == "machine" and mine is not None else best["waves"]
    # "ידוע" = נצפה 6.0, או שהערך הגבוה ביותר נקרא ב-MIN_WAVES גלים לפחות (אצל המכונה ואצל מחשבי הדגם שלה)
    known = best["max"] >= TOP_GBPS or _waves_at(conn, [mac, *mates], port, best["max"]) >= MIN_WAVES
    return {"gbps": best["max"], "basis": basis, "known": known, "waves": waves,
            "machines": (mine["machines"] if mine else 0) + (theirs["machines"] if theirs else 0), "model": model}


def drop(cap: dict, now_gbps: object, disk_max_gbps: object = None) -> bool | None:
    """האם הקישור **עכשיו** ירד מהיכולת שנלמדה. None = אי אפשר לשפוט (אין יכולת, או הקישור לא נקרא): לא "תקין" ולא "ירידה".
    נמוך גם מהתקרה של הדיסק והבקר (`link_max_gbps`) -- אחרת דיסק SATA II ב-3.0 על יציאה של 6.0 היה "ירידה"."""
    if cap.get("gbps") is None or isinstance(now_gbps, bool) or not isinstance(now_gbps, (int, float)) or now_gbps <= 0:
        return None
    limit = cap["gbps"]
    if isinstance(disk_max_gbps, (int, float)) and not isinstance(disk_max_gbps, bool) and disk_max_gbps > 0:
        limit = min(limit, float(disk_max_gbps))
    return float(now_gbps) < limit
