"""הסבב כפי שהמסך מציג אותו — בשמות, לא במזהים.

הופרד מ-`sessions.py` כדי שניהול המצב (מי תופס את חריץ השידור, מתי
מתחילים, מי סיים) לא יגדל יחד עם התצוגה. אין כאן לוגיקה — רק תרגום
של שורה בבסיס הנתונים לצורה שהקונסולה ומסך התחנה קוראים.
"""

from __future__ import annotations

import json
import sqlite3

from . import bootguard, disk_events, learn_eta
from .sessions import MULTICAST


def label(session: sqlite3.Row, library) -> str:
    """שם הסבב כפי שהמפעיל רואה אותו — שם האימג' שהוא משדר.

    לסבב אין שם משלו: יש `id` אקראי שאינו מופיע על שום מסך. מה שהמפעיל
    **קורא** בכותרת (`image_name` למטה, ובמסך התחנה "משדר: ...") הוא שם
    האימג', וזה מה שהוא מקליד כדי לעצור (עיקרון 7, ‏#581). הנפילה חזרה
    ל-`image_id` היא אותה נפילה שב-`round_label` של חדר השיכפולים,
    ומאותה סיבה: **מקום אחד**, אחרת סבב שהאימג' שלו נמחק באמצע היה
    בלתי-ניתן לעצירה — תיקון שגרוע מהבאג.
    """
    manifest = library.get(session["image_id"])
    return manifest["name"] if manifest else session["image_id"]


def role_of(conn, username):
    """תפקיד המשתמש (admin/deploy) או None כשהמשתמש נמחק/לא ידוע."""
    if not username:
        return None
    row = conn.execute(
        "SELECT role FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    return row["role"] if row else None


def console_extras(store, row: sqlite3.Row) -> dict:
    """‏#1255: שדות "משימות אחרונות" — **רק** לתשובת הקונסולה המאומתת (`/overview`).

    ‏`build()` משותף גם ל-`station.active_session_view`, שמוגש לסוכן על 8080 בלי הרשאת קונסולה:
    שם המשתמש שפתח את הסבב ותפקידו לא יוצאים לשם. כאן: מי פתח (`None` = לא ידוע), מתי התחיל ומתי נסגר."""
    return {
        "opened_by": row["opened_by"] or None,
        "opened_by_role": role_of(store.conn, row["opened_by"]),
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "closed_at": row["closed_at"],
    }


def build(store, session: sqlite3.Row, library) -> dict:
    """מחשב מזוהה בשם המחשב שייכתב לו (קידומת-סיומת), לא ב-MAC: זה מה
    שמופיע על המסך בכיתה, וזה מה שמחפשים כשמשהו נכשל. ה-MAC נשאר
    בנתונים לטכנאי, אבל הוא לא הכותרת.
    """
    conn = store.conn
    group = conn.execute(
        "SELECT label, role FROM groups WHERE id = ?", (session["group_id"],)
    ).fetchone()
    names = {
        row["mac"]: row["suffix"]
        for row in conn.execute(
            "SELECT mac, suffix FROM machines WHERE group_id = ?",
            (session["group_id"],),
        )
    }
    # שם המחשב נגזר מקידומת הסבב, ולמשיכת יוניקאסט אין קידומת אמיתית:
    # אחרי שחזור כזה השם נקבע מתוך Windows (ראו ui.sh בסוכן).
    classroom = (group is not None and group["role"] == "classroom"
                 and session["kind"] == MULTICAST)

    # בריאות SMART וההכרעות של הסבב (#652), מקובצות לפי מכונה, כדי
    # שהקונסולה תראה "דיסק N: אזהרה -> rescue" ליד כל מחשב (צפייה בלבד
    # ב-v1; ההכרעה נעשתה בסוכן).
    disks_by_mac: dict[str, list] = {}
    for ev in disk_events.for_session(conn, session["id"]):
        disks_by_mac.setdefault(ev["mac"], []).append(ev)

    members = []
    for m in store.members(session["id"]):
        suffix = names.get(m["mac"])
        hostname = f'{session["prefix"]}-{suffix}' if classroom and suffix else None
        members.append({
            "mac": m["mac"],
            "name": suffix,
            "hostname": hostname,
            "state": m["state"],
            "done": bool(m["done"]),
            "bytes_written": m["bytes_written"],
            "bytes_total": m["bytes_total"],
            "error": m["error"],
            "updated_at": m["updated_at"],
            "disks": disks_by_mac.get(m["mac"], []),
            # ‏#720: תוצאת ה-staging של הדרייברים; null = השלב לא דיווח.
            "drivers": json.loads(m["drivers_json"]) if m["drivers_json"] else None,
        })

    return {
        "id": session["id"],
        "state": session["state"],
        "failed_reason": session["failed_reason"],
        # הזרם: 'multicast' תופס את חריץ השידור היחיד, 'unicast' לא (#60).
        "kind": session["kind"],
        # בחירת המחשבים של הסבב; None = כל הקבוצה. הקונסולה מציגה
        # לפי זה מי שייך לסבב הזה ומי יחכה לסבב הבא.
        "roster": json.loads(session["roster_json"])
        if session["roster_json"] else None,
        "group_id": session["group_id"],
        "group_label": group["label"] if group else session["group_id"],
        "group_role": group["role"] if group else "unknown",
        "image_id": session["image_id"],
        "image_name": label(session, library),
        "prefix": session["prefix"],
        "expected_clients": session["expected_clients"],
        "expand_partition": session["expand_partition"] or "auto",
        # סבב של מכונה אחת אינו הפצה לכיתה — הקונסולה מסמנת אותו אחרת.
        "single": session["expected_clients"] == 1,
        "joined": len(members),
        # מי אתחל שוב ושוב לסבב הזה ולא הגיע — לפי MAC. מכונה שהסוכן
        # שלה נכשל לפני `hello` אינה חברה בסבב, ולכן אין לה שורה
        # ברשימה למעלה: בלי זה היא נראית בדיוק כמו מחשב שלא נדלק (#64).
        # `blocked` = התקציב נגמר והשרת כבר מוריד אותה לדיסק (#75).
        "stuck": bootguard.repeats(conn, f'session:{session["id"]}'),
        "starts_in_seconds": store.starts_in_seconds(session)
        if session["state"] == "open" else 0,
        "members": members,
    }


def pull_forecast(store, session: sqlite3.Row, library) -> dict | None:
    """‏#1843: משיכה (שחזור) שרצה — כמה זמן היא צפויה לקחת לפי המשיכות הקודמות
    של אותה מכונה (‏`learn_eta.pull`). לקונסולה בלבד (‏`/overview.pulls[]`), לא
    לתשובה שמוגשת לסוכן. ‏None = אינה משיכה רצה של מכונה אחת."""
    members = store.members(session["id"])
    if session["kind"] == MULTICAST or session["state"] != "running" or len(members) != 1:
        return None
    manifest = library.get(session["image_id"]) if library is not None else None
    size = manifest.get("total_compressed_bytes") if isinstance(manifest, dict) else None
    return learn_eta.pull(store.conn, members[0]["mac"], size, session["started_at"],
                          session["id"])
