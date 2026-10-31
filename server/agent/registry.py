"""טבלת ה-MAC והקבוצות — הזיכרון המוסדי של המערכת.

הסיומת קבועה לנצח למכונה פיזית (סעיף 10 באפיון). לכן שני חוקים נאכפים
כאן ולא נדחים לקונסולה: סיומת חייבת להיות דו-ספרתית או INS, ו-MAC שכבר
רשום עם סיומת אחרת הוא שגיאה — לא הערה.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from server.core import hostname_rule
from server.db import _write_lock, journal, now_iso, writing
from server.rounds.sessions import HOSTNAME_MAX, NONCLASSROOM_NAME_MAX

_MAC_CLEAN = re.compile(r"[^0-9a-f]")
_SUFFIX_NUM = re.compile(r"^\d{1,2}$")

#: חדר המשכפלים: הסבב והגל הם session על הקבוצה הקבועה הזו.
CLONERS_GROUP = "grp_CLONERS"
#: ‏#1554: **חברי** החדר הם כל מחשב בקבוצה שתפקידה cloner — הקונסולה מציעה
#: קבוצות משכפלים נוספות ("חיפה — משכפלים"), ומשכפל בהן לא הוצג, לא נבחר
#: ולא הצטרף לגל. ‏SQL: ‏``f"m.group_id IN {ROOM_GROUPS}"``.
ROOM_GROUPS = "(SELECT id FROM groups WHERE role = 'cloner')"

#: הדחייה לשם לא-כיתתי ארוך מדי — נגזרת מ-`HOSTNAME_MAX` ומהקידומת
#: הארוכה ביותר, ונוקבת בשני המספרים כדי שמי שרושם יבין מה התקרה ומאין
#: היא באה. אותה מחרוזת בכל שלושת מסלולי הרישום (ייבוא/הוספה/עריכה) (#405).
_NAME_TOO_LONG = (
    f"שם באורך 1-{NONCLASSROOM_NAME_MAX} תווים — הקידומת והמקף חייבים "
    f"להיכנס יחד לתקרת {HOSTNAME_MAX} התווים של שם מחשב"
)
_SUFFIX_BAD = "סיומת חייבת להיות 01-99 או INS"


def name_error(role: str, text: object) -> str:
    """ההודעה לשם ש-`normalize_name` דחה — אותה בכל שלושת מסלולי הרישום.
    ‏#1997: שם שאינו שם מחשב (עברית, רווח, קו תחתון, מקף בקצה) נוקב בכלל;
    שם חוקי שארוך מהתקרה של מכונה לא-כיתתית — בתקרה (#405)."""
    if role == "classroom":
        return _SUFFIX_BAD
    name = text.strip() if isinstance(text, str) else text
    return hostname_rule.refusal(name, "שם המחשב") or _NAME_TOO_LONG

#: כל כתיבה כאן עוברת ב-``with _write_lock, writing(conn)`` — שני
#: המנגנונים של `db.py`, מאותה סיבה שבגללה `net_seen` קיבל אותם (#272,
#: ‏#356, ‏#313): כתיבה שנכשלה בלי ``rollback`` משאירה את החיבור בתוך
#: טרנזאקציה, ומשם **כל** כתיבה עליו נכשלת מיד עד אתחול השרת — כלומר
#: טבלת ה-MAC שהפסיקה לקבל רישומים. ‏`journal` נוטל את אותה נעילה
#: בעצמו, והיא ``Lock`` ולא ``RLock``, ולכן הוא נשאר **מחוץ** לבלוק.


def normalize_mac(text: str) -> str | None:
    """שלוש הווריאציות מסעיף 10 → התבנית הקנונית, או None.

    בכוונה מחמירה יותר מה-normalize של המחולל: לשם מגיע קלט מ-GRUB
    וכל חריגה מסתיימת בדיסק מקומי; לכאן מגיעה הקלדה של אדם, וטעות
    צריכה להיתפס, לא להיבלע.
    """
    if not isinstance(text, str):
        return None
    hexonly = _MAC_CLEAN.sub("", text.strip().lower())
    if len(hexonly) != 12:
        return None
    # מוודאים שלא נזרקו תווים "אמיתיים" — רק מפרידים מוכרים.
    if re.sub(r"[0-9a-fA-F:\-. ]", "", text.strip()):
        return None
    return ":".join(hexonly[i : i + 2] for i in range(0, 12, 2))


def normalize_suffix(text: str) -> str | None:
    """"5" → "05", "ins" → "INS", כל דבר אחר → None."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s.upper() == "INS":
        return "INS"
    if _SUFFIX_NUM.match(s):
        return s.zfill(2)
    return None


def normalize_name(role: str, text: str) -> str | None:
    """השם שניתן למכונה, לפי תפקיד הקבוצה.

    בכיתה השם הוא הסיומת שתיכנס לשם המחשב — ולכן החוקים נוקשים
    (01-99 או INS). מחשב בנייה ומחשב שיכפול מקבלים שם מחשב — ‏#1997 (נדב,
    22/10): שם מחשב הוא hostname באנגלית (`hostname_rule`), לא שם חופשי.
    גם הוא נכנס לשם המחשב אחרי הקידומת והמקף בפתיחת הסבב, ולכן אורכו חסום
    כאן — ברישום — ולא מאוחר על כל הקבוצה (#405).
    """
    if role == "classroom":
        return normalize_suffix(text)
    if not isinstance(text, str):
        return None
    s = text.strip()
    return s if hostname_rule.is_valid(s) and len(s) <= NONCLASSROOM_NAME_MAX else None


@dataclass
class ImportLine:
    line_no: int
    raw: str
    mac: str | None = None
    suffix: str | None = None
    error: str | None = None


def parse_paste(text: str, role: str = "classroom") -> list[ImportLine]:
    """מפרק הדבקה מרובה: שורה = MAC ואז השם, מופרדים ברווח/פסיק/טאב.

    לא נוגע ב-DB — פענוח בלבד, כדי שהקונסולה תוכל להציג תצוגה מקדימה
    לפני שמירה. כפילות בתוך ההדבקה עצמה מסומנת כשגיאה כאן.
    """
    lines: list[ImportLine] = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        item = ImportLine(line_no=i, raw=stripped)
        parts = re.split(r"[,\t ]+", stripped, maxsplit=1)
        if len(parts) != 2:
            item.error = "צריך MAC ואחריו שם"
        else:
            item.mac = normalize_mac(parts[0])
            name = parts[1].strip(" ,\t")
            item.suffix = normalize_name(role, name)
            if item.mac is None:
                item.error = "MAC לא תקין"
            elif item.suffix is None:
                item.error = name_error(role, name)
            elif item.mac in seen:
                item.error = f"כפילות בהדבקה — כבר הופיע בשורה {seen[item.mac]}"
            else:
                seen[item.mac] = i
        lines.append(item)
    return lines


def import_lines(
    conn: sqlite3.Connection, group_id: str, lines: list[ImportLine], user: str
) -> tuple[int, list[ImportLine]]:
    """שומר את השורות התקינות. מחזיר (כמה נשמרו, השורות שנדחו).

    MAC שכבר רשום עם אותה סיומת — עדכון קבוצה שקט. עם סיומת אחרת —
    דחייה: הסיומת קבועה לנצח, ושינוי שלה הוא פעולה מפורשת, לא ייבוא.
    """
    saved = 0
    rejected: list[ImportLine] = []
    # כיתה שלמה היא **רצף** כתיבות אחד: שורה שנכשלה באמצע משאירה את
    # הנעילה של הקודמות יתומה, ו"נשמרו 12 מתוך 30" הוא בדיוק המצב
    # שאיש אינו יודע לתקן ידנית.
    with _write_lock, writing(conn):
        for item in lines:
            if item.error or item.mac is None or item.suffix is None:
                rejected.append(item)
                continue
            row = conn.execute(
                "SELECT suffix FROM machines WHERE mac = ?", (item.mac,)
            ).fetchone()
            if row and row["suffix"] != item.suffix:
                item.error = (
                    f"רשום כבר עם הסיומת {row['suffix']} — "
                    "הסיומת קבועה למכונה; מחיקה מפורשת לפני שינוי"
                )
                rejected.append(item)
                continue
            conn.execute(
                "INSERT INTO machines (mac, suffix, group_id, added_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (mac) DO UPDATE SET group_id = excluded.group_id",
                (item.mac, item.suffix, group_id, now_iso()),
            )
            saved += 1
    journal(conn, "mac_import", f"group={group_id} saved={saved} rejected={len(rejected)}", user)
    return saved, rejected


def group_role(conn: sqlite3.Connection, group_id: str) -> str | None:
    row = conn.execute("SELECT role FROM groups WHERE id = ?", (group_id,)).fetchone()
    return row["role"] if row else None


def add_machine(
    conn: sqlite3.Connection, mac_raw: str, name_raw: str, group_id: str, user: str
) -> str:
    """הוספה ידנית של מכונה אחת. מחזיר את ה-MAC הקנוני; זורק ValueError."""
    role = group_role(conn, group_id)
    if role is None:
        raise ValueError("קבוצה לא קיימת")
    mac = normalize_mac(mac_raw)
    if mac is None:
        raise ValueError("MAC לא תקין")
    name = normalize_name(role, name_raw)
    if name is None:
        raise ValueError(name_error(role, name_raw))
    if conn.execute("SELECT 1 FROM machines WHERE mac = ?", (mac,)).fetchone():
        raise ValueError("ה-MAC כבר רשום — ערכו אותו במקום להוסיף שוב")
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, ?, ?, ?)",
            (mac, name, group_id, now_iso()),
        )
    journal(conn, "machine_add", f"{mac} name={name} group={group_id}", user)
    return mac


def update_machine(
    conn: sqlite3.Connection, mac_raw: str, name_raw: str | None,
    group_id: str | None, user: str,
) -> None:
    """עריכה מפורשת — הדרך היחידה לשנות שם קיים (הייבוא דוחה שינוי שקט)."""
    mac = normalize_mac(mac_raw)
    row = conn.execute(
        "SELECT m.suffix, m.group_id, g.role FROM machines m"
        " JOIN groups g ON g.id = m.group_id WHERE m.mac = ?", (mac,)
    ).fetchone() if mac else None
    if row is None:
        raise ValueError("מכונה לא רשומה")
    target_group = group_id or row["group_id"]
    role = group_role(conn, target_group)
    if role is None:
        raise ValueError("קבוצה לא קיימת")
    name = row["suffix"] if name_raw is None else normalize_name(role, name_raw)
    if name is None:
        raise ValueError(name_error(role, name_raw))
    with _write_lock, writing(conn):
        conn.execute(
            "UPDATE machines SET suffix = ?, group_id = ? WHERE mac = ?",
            (name, target_group, mac),
        )
    journal(conn, "machine_edit", f"{mac} name={name} group={target_group}", user)


class MacBusy(Exception):
    """עריכת MAC שנדחתה כי הכתובת תפוסה או שהמכונה עובדת עכשיו — 409."""


def change_mac(conn: sqlite3.Connection, mac_raw: str, new_raw: object,
               user: str) -> tuple[str, bool]:
    """‏#977 §5: כרטיס רשת שהוחלף. השורה עוברת לכתובת החדשה — שם, קבוצה
    וחריצים נשארים. מחזיר ``(new_mac, changed)``.

    ההיסטוריה (‏`net_devices`, מלאי, יומן) נשארת על הכתובת הישנה: היא של
    הכרטיס הישן, והחדש עוד לא נראה. מכונה עם משימה פתוחה או חברות בסבב
    פתוח נשארת עם הכתובת שלה — שניהם כתובים ל-MAC, והסוכן שעובד עכשיו
    היה מאבד את העבודה שלו.

    ‏ValueError = 400, ‏LookupError = 404, ‏MacBusy = 409."""
    mac = normalize_mac(mac_raw)
    if mac is None or conn.execute(
            "SELECT 1 FROM machines WHERE mac = ?", (mac,)).fetchone() is None:
        raise LookupError("מכונה לא רשומה")
    new = normalize_mac(new_raw) if isinstance(new_raw, str) else None
    if new is None:
        raise ValueError("MAC לא תקין")
    if new == mac:
        return mac, False
    if conn.execute("SELECT 1 FROM machines WHERE mac = ?", (new,)).fetchone():
        raise MacBusy("הכתובת כבר רשומה למכונה אחרת")
    if conn.execute(
            "SELECT 1 FROM tasks WHERE mac = ? AND state IN ('pending', 'running')",
            (mac,)).fetchone():
        raise MacBusy("למכונה יש משימה פתוחה — סיימו או בטלו אותה קודם")
    if conn.execute(
            "SELECT 1 FROM session_members sm JOIN sessions s ON s.id = sm.session_id"
            " WHERE sm.mac = ? AND s.state IN ('open', 'running')", (mac,)).fetchone():
        raise MacBusy("המכונה בסבב פתוח — עצרו אותו קודם")
    try:
        with _write_lock, writing(conn):
            conn.execute("UPDATE machines SET mac = ? WHERE mac = ?", (new, mac))
    except sqlite3.IntegrityError:
        # נרשמה בין הבדיקה לכתיבה — אותה תשובה, לא 500.
        raise MacBusy("הכתובת כבר רשומה למכונה אחרת") from None
    journal(conn, "machine_mac_edit", f"{mac} -> {new}", user)
    return new, True


def lookup(
    conn: sqlite3.Connection,
    mac: str,
    all_macs: list[str] | None = None,
) -> sqlite3.Row | None:
    """המכונה + הקבוצה + התפקיד, בשורה אחת. התפקיד נגזר מהחברות בקבוצה.

    ‏`mac` הוא המפתח המועדף (כרטיס האתחול). אם הוא אינו רשום, כל MAC
    ב-`all_macs` נבדק אחריו — מכונה עם שני כרטיסים שעולה על השני
    עדיין מזוהה (#524).
    """
    sql = (
        "SELECT m.mac, m.suffix, m.note, m.drawer_count, m.drawer_slots, "
        "g.id AS group_id, g.label, g.role "
        "FROM machines m JOIN groups g ON g.id = m.group_id WHERE m.mac = ?"
    )
    candidates = [mac]
    for extra in all_macs or ():
        if extra not in candidates:
            candidates.append(extra)
    for candidate in candidates:
        row = conn.execute(sql, (candidate,)).fetchone()
        if row is not None:
            return row
    return None


def all_macs(conn: sqlite3.Connection) -> list[str]:
    """כל ה-MAC הרשומים, לפי הסדר — המקור ל-dhcp.render_known_macs (#141).

    ‏ORDER BY קבוע כדי שקובץ ה-host לא יזוז בין קריאה לקריאה בלי סיבה
    (דיפים נקיים ב-`git diff`/לוגים, לא הבדל אמיתי במכונות)."""
    return [r["mac"] for r in conn.execute("SELECT mac FROM machines ORDER BY mac")]


def export_csv(conn: sqlite3.Connection) -> str:
    """mac,suffix,group — לגיבוי ולעריכה חיצונית."""
    rows = conn.execute(
        "SELECT mac, suffix, group_id FROM machines ORDER BY group_id, suffix"
    ).fetchall()
    out = ["mac,suffix,group"]
    out.extend(f"{r['mac']},{r['suffix']},{r['group_id']}" for r in rows)
    return "\n".join(out) + "\n"
