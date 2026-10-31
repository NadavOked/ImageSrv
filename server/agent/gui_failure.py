""""הגואי לא עלה במחשב X" — הדיווח של הסוכן לפני האתחול (11/10).

נדב, 11/10: אין מסך טקסט חלופי במכונות. מסך שצריך אדם (מחשב בנייה,
recovery) ולא קיבל גואי מכוון את האתחול הבא לדיסק המקומי (BootNext) —
אחרת מחשב build/classroom בלי משימה חוזר לסוכן בלולאה — ושולח hello אחד,
לא-מצטרף, עם השדה ``gui_failed`` (interfaces.md, סעיף 2). כאן הוא נרשם
ביומן, והקונסולה מציגה אותו.

**התראה, לא שער**, כמו ``foreign_vlan``: לא נוגע בתשובה ולא זורק. שדה
פגום נזנח כמו שדה לא ידוע. ה-MAC נשמר גולמי בפרט, ומתורגם לשם המכונה
בקריאה (``journal_he``).
"""

from __future__ import annotations

import logging
import sqlite3

from server.db import journal

log = logging.getLogger("imagesrv.gui_failure")

SCREENS_HE = {"build": "מסך מחשב הבנייה", "recovery": "מסך השחזור"}
BOOT_NEXT_HE = {
    "set": "האתחול הבא כוון לדיסק המקומי",
    "failed": "לא הצלחנו לכוון את האתחול הבא לדיסק המקומי",
    "skipped": "האתחול הבא לא כוון לדיסק המקומי",
}
DETAIL_MAX = 200


def well_formed(raw: object) -> dict | None:
    """``{"screen": build|recovery, "boot_next": set|failed|skipped,
    "detail": str}`` — או None."""
    if not isinstance(raw, dict):
        return None
    screen, boot_next, detail = raw.get("screen"), raw.get("boot_next"), raw.get("detail", "")
    if screen not in SCREENS_HE or boot_next not in BOOT_NEXT_HE or not isinstance(detail, str):
        return None
    return {"screen": screen, "boot_next": boot_next,
            "detail": " ".join(detail.split())[:DETAIL_MAX]}


def note(conn: sqlite3.Connection, mac: str, raw: object, *, known: bool) -> bool:
    """רושם ``gui_failed`` ביומן כשהשדה תקין והמכונה רשומה. מחזיר אם נרשם."""
    report = well_formed(raw)
    if report is None or not known:
        return False
    text = f"במחשב {mac} ({SCREENS_HE[report['screen']]}) — {BOOT_NEXT_HE[report['boot_next']]}"
    if report["boot_next"] != "set" and report["detail"]:
        text += f": {report['detail']}"
    try:
        journal(conn, "gui_failed", text)
    except sqlite3.Error:
        # ‏#1535: ה-MAC מגיע מה-hello — שבירת שורה אינה מגיעה ליומן.
        log.exception("gui_failed for %s was not journaled",
                      mac.replace("\r", " ").replace("\n", " "))
        return False
    return True
