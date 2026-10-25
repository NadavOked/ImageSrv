"""קליטת דיווחי ההתקדמות — ממשק 4.

הדיווח מגיע כל 2 שניות מכל מכונה כותבת. נשמר מצב אחרון לכל חבר סבב,
כדי שהקונסולה תציג התקדמות חיה; מעברים חשובים (done, failed) נרשמים
ביומן פעם אחת, לא כל 2 שניות.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime

from boot.grub_menu import normalize_mac as lenient_mac

from . import capture_detail, cloner_cpu, disk_failures, drawer_cancel, restore_stages, slot_names, smart_history
from .db import journal, now_iso, update_one
from .tasks import CAPTURE_STAGES, OPEN_STATES, claim_task

log = logging.getLogger("imagectl.reports")

#: כמה מהמחרוזת הגולמית נכנס ליומן ולפלט. ‏`lenient_mac` מוריד כל תו
#: שאינו הקסה, ולכן גוף עוין יכול לעטוף 12 ספרות בכמה שירצה — היומן
#: מראה את מה שנשלח, לא מאחסן אותו לפי אורכו.
_RAW_SHOWN = 32

#: המצבים שבהם המכונה סיימה את חלקה — יש מה לרשום ביומן, ואין מה לחכות
#: לו יותר (ממשק 4). ‏`partial` הוא השלישי שבהם (#67): חלק מהמגירות
#: נכתבו וחלק נכשלו. הוא מסיים כמו `done` — אחרת גל שלם היה נתקע על
#: מכונה שכבר אמרה את דברה — אבל **אינו** מדליק את `done`, שהוא הראיה
#: החיובית להצלחה מלאה ומכאן הלאה נספר ככזו (עיקרון 5).
TERMINAL = ("done", "failed", "partial")

#: כל מצב שהסוכן מדווח בו על עצמו (`build_progress` ב-`progress.sh`,
#: השדה `state` העליון). מקורם: ‏`waiting` (ברירת המחדל), ‏`writing`,
#: ‏`verifying`, ‏`naming`, ‏`done`, ‏`failed`, ‏`partial` במסלול הסבב/
#: המגירות, ו-`capturing`/`failed` במסלול הקליטה. ‏#772: מצב שאינו כאן
#: נדחה במקום להיכתב בשקט — דיווח בלי `state` (שהופך ל-`""`) או עם ערך
#: שרירותי היה מציב מצב לא-טרמינלי לנצח ותוקע את הגל (עיקרון 5).
#: ‏`sending` (‏#715) — מחשב הבנייה שמשדר מהדיסק שלו (משימת `direct_send`).
VALID_STATES = frozenset(
    {"waiting", "writing", "verifying", "naming", "staging", "capturing", "sending"} | set(TERMINAL)
)

#: ‏#720: מצבי ה-staging של הדרייברים בדיווח הסיום (`drivers.state`).
#: ‏`skipped` = לא יעד Windows; ‏`no_match` = יש מלאי ואין חבילה; ‏`failed`
#: = ניסינו ולא הצלחנו (הסיבה ב-`error`) — והשחזור עצמו `done` בכל מקרה.
DRIVER_STATES = frozenset({"staged", "no_match", "skipped", "failed"})


def well_formed_drivers(value: object) -> dict | None:
    """השדה `drivers` של הדיווח בצורתו התקנית, או ``None`` — שדה חסר או
    פגום אינו נכתב (ואינו דורס ערך קודם), כמו כל שדה לא ידוע."""
    if not isinstance(value, dict) or value.get("state") not in DRIVER_STATES:
        return None
    packages = value.get("packages")
    if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
        return None
    out: dict = {"state": value["state"], "packages": packages[:64]}
    for key in ("error", "reason"):
        if isinstance(value.get(key), str):
            out[key] = value[key][:300]
    boot = _well_formed_boot(value.get("boot"))
    if boot is not None:
        out["boot"] = boot
    linux = _well_formed_linux(value.get("linux"))
    if linux is not None:
        out["linux"] = linux
    return out


def well_formed_udp_drops(payload: dict) -> dict | None:
    """‏`udp_drops` של הדיווח (הכרעת נדב 17/10, ‏`agent/lib/waits.sh`):
    ‏`{"rcvbuf", "inerrors"}` — מספר שלם לא שלילי, או ‏null = "לא נקרא".

    ‏None = השדה **חסר** — הערך הקודם נשאר (‏COALESCE; דיווח לפני המחיצה
    הראשונה). שדה **פגום** אינו נזנח אלא נשמר כ"לא נקרא": הסכום גדל רק
    לאורך הזרם, ודיווח סופי פגום שהיה משאיר את האפס של מחיצה קודמת היה
    נקרא כ"המקלט נקי" (עיקרון 5)."""
    if "udp_drops" not in payload:
        return None
    value = payload["udp_drops"]
    unread = {"rcvbuf": None, "inerrors": None}
    if not isinstance(value, dict):
        return unread
    out = {}
    for key in unread:
        got = value.get(key)
        if got is not None and (not isinstance(got, int) or isinstance(got, bool)
                                or got < 0):
            return unread
        out[key] = got
    return out


#: ‏#1331: דרייבר האחסון ה-in-box של הדיסק שנכתב (`agent/lib/bootstorage.sh`).
#: ‏`enabled` = נכתב ונקרא חזרה · ‏`already` = כבר boot-start · ‏`skipped` =
#: סוג דיסק שאין לו מיפוי · ‏`failed` = ניסינו ולא הצלחנו (הסיבה ב-`error`).
BOOT_STATES = frozenset({"enabled", "already", "skipped", "failed"})


def _well_formed_boot(value: object) -> dict | None:
    """‏`drivers.boot` בצורתו התקנית, או ``None``. פגום נזנח לבדו —
    תוצאת ה-staging שסביבו נשמרת."""
    if not isinstance(value, dict) or value.get("state") not in BOOT_STATES:
        return None
    out: dict = {"state": value["state"]}
    for key, cap in (("service", 32), ("error", 300), ("reason", 300)):
        if isinstance(value.get(key), str):
            out[key] = value[key][:cap]
    return out


#: ‏#1331 T8: ה-initramfs של יעד לינוקס (`agent/lib/linuxdrivers.sh`, בדיקה
#: בלבד). ‏`generic` = מודול ה-bus של הדיסק נמצא ב-initrd או מובנה בקרנל ·
#: ‏`hostonly` = לא נמצא — הדיסק עלול לא לעלות על מכונה אחרת · ‏`unsupported`
#: = מבנה שלא נבדק (בלי initrd, ‏/boot נפרד) · ‏`failed` = הבדיקה עצמה נשברה ·
#: ‏`skipped` = סוג דיסק שאין לו מיפוי. רק `generic` הוא ראיה (עיקרון 5).
LINUX_STATES = frozenset({"generic", "hostonly", "unsupported", "failed", "skipped"})


def _well_formed_linux(value: object) -> dict | None:
    """‏`drivers.linux` בצורתו התקנית, או ``None``. פגום נזנח לבדו."""
    if not isinstance(value, dict) or value.get("state") not in LINUX_STATES:
        return None
    out: dict = {"state": value["state"]}
    for key, cap in (("module", 32), ("kver", 64), ("initrd", 128), ("os", 80),
                     ("error", 300), ("reason", 300)):
        if isinstance(value.get(key), str):
            out[key] = value[key][:cap]
    return out


def _journal_linux(conn: sqlite3.Connection, outcome: dict | None, where: str,
                   named: str = "") -> None:
    """‏#1331: יעד לינוקס שה-initramfs שלו לא אומת — שורת יומן, פעם אחת, עם
    המעבר הטרמינלי. ‏`generic` ו-`skipped` אינם נרשמים."""
    linux = outcome.get("linux") if outcome else None
    if linux and linux["state"] in ("hostonly", "unsupported", "failed"):
        journal(conn, "linux_initramfs_warning",
                f"{where} — {linux['state']}: {linux.get('error', '?')}{named}")


def _journal_boot(conn: sqlite3.Connection, outcome: dict | None, where: str,
                  named: str = "") -> None:
    """‏#1331: דרייבר אחסון שלא הוגדר לעלייה — שורת יומן, פעם אחת, עם
    המעבר הטרמינלי. ‏`enabled`/`already` אינם נרשמים: אין בהם מה לעשות."""
    boot = outcome.get("boot") if outcome else None
    if boot and boot["state"] == "failed":
        journal(conn, "boot_storage_failed",
                f"{where} — {boot.get('service', '?')}: {boot.get('error', '?')}{named}")

#: ‏`lost` — השרת קבע שחבר לא דיווח כלל מעל הספָּף. ‏**לא** `done` (אין
#: ראיה שכתב) ו**לא** `failed` (הוא מעולם לא דיווח כישלון): פשוט לא ידוע
#: מה קרה לו (#450). זהו מצב ש**השרת** קובע ב-`room.tick`, ולכן הוא
#: **אינו** ב-VALID_STATES — סוכן אינו יכול לטעון אותו על עצמו (#772).
LOST = "lost"

#: החברים שהגל אינו ממתין להם עוד: הטרמינליים שהסוכן מדווח, ועוד `lost`
#: שהשרת קובע. ‏`tick` בודק מול זה, לא מול `TERMINAL` — מכונה שנעלמה
#: לעולם אינה מדווחת מצב סופי, ובלי `lost` הגל היה תקוע לנצח (#450).
FINAL = frozenset({*TERMINAL, LOST})


def ingest(conn: sqlite3.Connection, payload: dict,
           token: str | None = None) -> dict:
    """‏`token` — כותרת `X-Imagectl-Task-Token` של הבקשה, אם נשלחה.
    נבדק **רק** במסלול המשימה (#855); לסבב אין אסימון."""
    session_id = payload.get("session_id")
    task_id = payload.get("task_id")
    raw_mac = payload.get("mac")
    state = payload.get("state", "")
    targets = payload.get("targets")
    if not raw_mac or not isinstance(targets, list) or not (session_id or task_id):
        return {"ok": False, "error": "missing session_id/task_id, mac or targets",
                "code": "bad_report"}

    # ‏#772: המצב מאומת מול הקבוצה המוכרת לפני כל כתיבה. ערך ריק או לא
    # מוכר אינו "התקדמות שלא זיהינו" אלא קלט פגום — הוא נדחה בשמו ואינו
    # נכתב, אחרת מצב החבר נהיה `""`, לעולם אינו טרמינלי, ו-tick נתקע.
    if state not in VALID_STATES:
        return {"ok": False, "error": f"unknown state: {state!r}",
                "code": "bad_state"}

    # ‏MAC מנורמל לפני כל נגיעה ב-DB — אותה פונקציה שבה משתמשים hello
    # ו-pulls, כי הן אלה שיצרו את השורה שאנחנו מחפשים. הצד השני של
    # ההשוואה קנוני לפי הסכימה (`db.py`: "קנוני: lowercase עם נקודתיים"),
    # והשוואה גולמית מולו החזירה "אינה חברה" על מכונה שכן חברה (#108).
    mac = lenient_mac(raw_mac)
    if mac is None:
        # עיקרון 5: "לא הצלחנו לקרוא את המזהה" ו"המכונה אינה חברה" הם
        # שני מצבים שונים. קלט שאינו MAC אינו טענה על חברוּת, ולכן הוא
        # מקבל קוד משלו ואינו נרשם כדיווח-מחוץ-לסבב.
        log.warning("progress report with a malformed mac: %r",
                    # codeql[py/log-injection] false positive: %r מבריח שורה חדשה ותווי בקרה (#291)
                    str(raw_mac)[:_RAW_SHOWN])
        return {"ok": False, "error": "missing or malformed mac", "code": "bad_mac"}
    sent_as = _sent_as(raw_mac, mac)

    if task_id:
        return _ingest_task(conn, task_id, mac, state, targets, sent_as, token,
                            stage=payload.get("stage"))

    # ‏#557: סבב שנסגר אינו מקבל דיווחים. עד כאן הוא קיבל — והחזיר
    # `200 OK` — ולכן `progress_loop` בסוכן, שלולאתו אינסופית ובולעת
    # את התשובה ב-`|| true`, המשיך לדווח עליו **לנצח**. נמדד 08/09:
    # שתי מכונות דיווחו על `ses_277091d3` במשך 50 דקות אחרי שנסגר,
    # אלפי בקשות — **ולכן לא הצטרפו לגל הבא.**
    #
    # זה בדיוק מה ש-#535 תיקן למשימות; מסלול הסבב לא קיבל את אותו
    # טיפול. אותה תשובה בשם, ואותו רישום ביומן.
    session = conn.execute(
        "SELECT state, failed_reason FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None or session["state"] not in ("open", "running"):
        journal(conn, "report_on_closed_session", f"{mac} for {session_id}{sent_as}")
        closed = {"ok": False, "error": "this session is no longer open", "code": "not_open"}
        # ‏#1928 (S4): סבב שנכשל אומר למכונה **למה** — "קובץ מחיצה חסר באימג'"
        # ולא רק "השרת סגר את הסבב", כדי שהמפעיל לא ינסה שוב ושוב.
        reason = _closed_reason(conn, session_id, session)
        if reason:
            closed["reason"] = reason
        return closed

    row = conn.execute(
        "SELECT state, done, targets_json FROM session_members"
        " WHERE session_id = ? AND mac = ?",
        (session_id, mac),
    ).fetchone()
    if row is None:
        # מדווח שאינו חבר — לא מפוצצים, אבל גם לא סופרים אותו בשקט.
        # ‏`sent_as` נכנס כדי שהיומן יאבחן ולא יסתיר: הצורה הקנונית היא
        # מה שמחפשים בטבלה, והצורה שנשלחה היא מה שמסביר לקוח חריג.
        journal(conn, "report_from_nonmember", f"{mac} for {session_id}{sent_as}")
        return {"ok": False, "error": "not a member of this session", "code": "not_member"}

    # ‏#771: מצב טרמינלי הוא בלתי-משתנה. חבר שכבר `done`/`failed`/`partial`
    # סיים את חלקו — יש לו ראיה חיובית להשלמה — ודיווח נוסף idempotent:
    # לא דורסים state ולא targets_json. בלי השומר הזה `done` כפול עם
    # `targets=[]` היה מוחק את targets_json → `_tally` סופר 0 מגירות
    # שהושלמו → גל שיכפול נוסף (re-image!); ו-`writing` מאוחר היה מרגרס
    # את `done` ל-לא-טרמינלי → תוקע את הגל. מחזירים ok בלי לסגת (עיקרון 5).
    if row["state"] in TERMINAL:
        return {"ok": True}

    previous_targets = row["targets_json"]
    was_cancelled = _cancelled_devs(previous_targets)
    _clean_target_drivers(targets)
    restore_stages.clean_targets(targets)   # #1883: marks → durations, or dropped
    targets = stamp_movement(previous_targets, targets)
    targets = stamp_done_serials(conn, mac, previous_targets, targets)
    bytes_written = sum(_int(t.get("bytes_written")) for t in targets)
    bytes_total = sum(_int(t.get("bytes_total")) for t in targets)
    errors = "; ".join(
        f"{t.get('dev', '?')}: {t['error']}" for t in targets if t.get("error")
    )
    # ‏#720: תוצאת ה-staging — נכתבת כשהגיעה, ונשארת (COALESCE) כשלא.
    drivers = well_formed_drivers(payload.get("drivers"))
    drops = well_formed_udp_drops(payload)
    cpu = cloner_cpu.well_formed(payload.get("cpu"))   # #1885: רק בדיווח הסופי

    previous = row["state"]
    conn.execute(
        "UPDATE session_members SET state = ?, bytes_written = ?, bytes_total = ?,"
        " error = ?, done = ?, targets_json = ?, updated_at = ?,"
        " drivers_json = COALESCE(?, drivers_json),"
        " udp_drops_json = COALESCE(?, udp_drops_json),"
        " cpu_json = COALESCE(?, cpu_json)"
        " WHERE session_id = ? AND mac = ?",
        (
            state, bytes_written, bytes_total, errors or None,
            1 if state == "done" else row["done"],
            json.dumps(targets), now_iso(),
            json.dumps(drivers, ensure_ascii=False) if drivers else None,
            json.dumps(drops) if drops else None,
            json.dumps(cpu) if cpu else None,
            session_id, mac,
        ),
    )
    conn.commit()

    if state != previous and state in TERMINAL:
        journal(conn, f"client_{state}", f"{mac} in {session_id}" + (f" — {errors}" if errors else ""))
        # ‏#720: "הושלם, דרייברים לא הונחו" הוא מצב שהמפעיל צריך לראות ביומן,
        # לא רק בכרטיס — פעם אחת, עם המעבר הטרמינלי.
        if drivers and drivers["state"] == "failed":
            journal(conn, "drivers_failed",
                    f"{mac} in {session_id} — {drivers.get('error', '?')}")
        elif drivers and drivers["state"] == "staged":
            journal(conn, "drivers_staged",
                    f"{mac} in {session_id} — {', '.join(drivers['packages'])}")
        _journal_boot(conn, drivers, f"{mac} in {session_id}")
        _journal_linux(conn, drivers, f"{mac} in {session_id}")
        cloner_cpu.note_final(conn, mac, session_id, cpu)   # ‏#1885: throttle שעלה — ביומן
        # ‏#1883: ציר הזמן של כל מגירה — פעם אחת, במעבר הטרמינלי של המכונה.
        for t in targets:
            if isinstance(t, dict) and isinstance(t.get("dev"), str):
                restore_stages.record(conn, session_id, mac, t,
                                      smart_history.serial_of(conn, mac, t["dev"]))
        # ‏#1269: מגירות של מחשב שיכפול — התוצאה לכל מגירה, פעם אחת, באותו מעבר.
        for t in targets:
            outcome = t.get("drivers") if isinstance(t, dict) else None
            # ‏#704: השם שהמפעיל נתן לחריץ של המגירה (‏dev → port מה-hello).
            named = (slot_names.journal_suffix(slot_names.label(conn, mac, dev=t.get("dev")))
                     if outcome and (outcome["state"] in ("failed", "staged")
                                     or "linux" in outcome) else "")
            if outcome and outcome["state"] == "failed":
                journal(conn, "drivers_failed", f"{mac} {t.get('dev', '?')} in {session_id}"
                        f" — {outcome.get('error', '?')}{named}")
            elif outcome and outcome["state"] == "staged":
                journal(conn, "drivers_staged", f"{mac} {t.get('dev', '?')} in {session_id}"
                        f" — {', '.join(outcome['packages'])}{named}")
            _journal_boot(conn, outcome, f"{mac} {t.get('dev', '?')} in {session_id}", named)
            _journal_linux(conn, outcome, f"{mac} {t.get('dev', '?')} in {session_id}", named)
        # ‏#1907 (נדב 22/10): מגירה שהושלמה ולא אומתה כי רשימת הבדיקה לא הורדה — ביומן, בשמה.
        for t in targets:
            if (isinstance(t, dict) and t.get("state") == "done" and t.get("readback") == "unverified"
                    and t.get("readback_why") == "list_not_fetched"):
                journal(conn, "readback_list_not_fetched", f"{mac} {t.get('dev', '?')} in {session_id}"
                        + slot_names.journal_suffix(slot_names.label(conn, mac, dev=t.get("dev"))))
    # ‏#874: יעד שנכשל בכתיבה נכנס לזיכרון הכשלים (סידורי + חריץ + שורות
    # ה-ATA). פעם אחת ליעד — הדיווח חוזר כל 2 שניות, ו-`record` מתעלם מכפול.
    for t in targets:
        # ‏#1859: החום מיד אחרי הכתיבה, על שורת הסבב של הסידורי (‏smart_history).
        if isinstance(t.get("temp_c"), int) and isinstance(t.get("dev"), str):
            smart_history.record_temp(conn, smart_history.serial_of(conn, mac, t["dev"]), session_id, t["temp_c"], mac)
        if t.get("state") == "failed":
            disk_failures.record(conn, session_id, mac, t)
        # ‏#1386: "בוטל" נרשם כשהסוכן דיווח אותו — פעם אחת, במעבר. לא disk_failure:
        # ביטול אינו כשל, ובסבב הבא הדיסק ירוק.
        elif t.get("state") == "cancelled" and isinstance(t.get("dev"), str)                 and t["dev"] not in was_cancelled:
            named = slot_names.label(conn, mac, dev=t["dev"]) or t["dev"]
            drawer_cancel.confirmed(conn, session_id, mac, t["dev"], f"{mac} {named}")
    # ‏#1386: בקשות "בטל דיסק זה" מהקונסולה/מחשב הבנייה נמסרות בתשובה הזו.
    cancel = drawer_cancel.pending(conn, session_id, mac)
    return {"ok": True, "cancel": cancel} if cancel else {"ok": True}


def _closed_reason(conn: sqlite3.Connection, session_id: str,
                   session: sqlite3.Row | None) -> str | None:
    """‏#1928: למה הסבב כבר אינו פתוח — מה שמגירת המשכפל כותבת אחרי "השרת סגר את הסבב".

    ‏`failed` — ה-`failed_reason` שלו (‏S4: "קובץ מחיצה חסר באימג'…"). ‏`closed` בידי
    משתמש מחובר (עצירת הסבב מהקונסולה או מחדר השיכפולים — `session_close` ביומן עם
    המשתמש, ‏FI O1 21/10) — "המפעיל ‹משתמש› עצר את הסבב". השרת שסגר בעצמו (גל שהסתיים,
    משיכה שנגמרה — `session_close` בלי משתמש) — ‏None: הנוסח הישן, לא סיבה מומצאת."""
    if session is None:
        return None
    if session["state"] == "failed":
        return session["failed_reason"] or None
    row = conn.execute(
        "SELECT user FROM journal WHERE event = 'session_close' AND detail = ? AND user != ''"
        " ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
    return f"המפעיל {row['user']} עצר את הסבב" if row is not None else None


def _cancelled_devs(previous_json: str | None) -> set[str]:
    try:
        previous = json.loads(previous_json or "[]")
    except (TypeError, ValueError):
        previous = []
    return {t.get("dev") for t in previous
            if isinstance(t, dict) and t.get("state") == "cancelled"}


def _clean_target_drivers(targets: list) -> None:
    """‏#1269: ‏`drivers` של יעד (מגירה) עובר את אותה `well_formed_drivers`
    כמו השדה העליון. פגום — נזנח מהיעד ואינו נשמר ב-`targets_json`."""
    for t in targets:
        if isinstance(t, dict) and "drivers" in t:
            clean = well_formed_drivers(t["drivers"])
            if clean is None:
                del t["drivers"]
            else:
                t["drivers"] = clean


def stamp_done_serials(conn: sqlite3.Connection, mac: str,
                       previous_json: str | None, targets: list) -> list:
    """קושר יעד שהושלם למגירה הפיזית שהייתה בו ברגע ההשלמה.

    הסוכן מדווח נתיב התקן; מלאי ה-hello האחרון בשרת הוא מקור האמת ל-serial
    שהיה באותו נתיב. בלי serial החותמת נשארת חסרה; `_tally` שומר תאימות
    לשורות ישנות כאלה וסופר לפי ה-serial הנוכחי אם הוא זמין, אך אינו מנחש
    זהות כשגם המלאי הנוכחי חסר serial.
    """
    row = conn.execute(
        "SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)
    ).fetchone()
    try:
        disks = json.loads(row["disks_json"] or "[]") if row else []
    except (TypeError, ValueError):
        disks = []
    serial_of = {
        disk.get("dev"): disk.get("serial")
        for disk in disks if isinstance(disk, dict)
    }
    try:
        previous = json.loads(previous_json or "[]")
    except (TypeError, ValueError):
        previous = []
    completed = {
        target.get("dev"): target.get("serial")
        for target in previous
        if isinstance(target, dict) and target.get("state") == "done"
    }
    stamped = []
    for target in targets:
        if not isinstance(target, dict):
            stamped.append(target)
            continue
        item = dict(target)
        if item.get("state") == "done":
            item.pop("serial", None)
            dev = item.get("dev")
            serial = completed.get(dev) if dev in completed else serial_of.get(dev)
            if serial:
                item["serial"] = serial
        stamped.append(item)
    return stamped


def stamp_movement(previous_json: str | None, targets: list) -> list:
    """מוסיף לכל יעד `moved_at` — הפעם האחרונה ש**הבייטים שלו גדלו**.

    ⚠️ **‏`updated_at` אינו התקדמות, וזה נמדד ולא הונח.** בסבב 08/09
    המגירה שמתה הוסיפה לרענן את החותמת שלה כל 5 שניות במשך 32 דקות
    בזמן שהמונה שלה קפא — כלומר סימון "תקוע" שנשען על זמן הדיווח
    **לעולם לא היה נדלק על המגירה היחידה שבאמת הייתה תקועה.**

    לכן החותמת כאן זזה על ראיה חיובית אחת בלבד: מספר שגדל. יעד חדש,
    יעד שהמספר שלו **ירד** (דיווח שחזר אחורה — אין לו פרשנות אחרת
    חוץ מ"התחלנו מחדש"), ויעד שאין לו מספר קודם — כולם מקבלים `now`,
    כי אף אחד מהם אינו ראיה לקיפאון.
    """
    was = {}
    try:
        for t in json.loads(previous_json or "[]"):
            if isinstance(t, dict) and t.get("dev"):
                was[t["dev"]] = t
    except (TypeError, ValueError):
        # ‏`targets_json` פגום אינו "אין תנועה" — הוא "לא ידענו".
        # עיקרון 5: החותמת נדחפת קדימה ולא נשארת ישנה, אחרת שדה שבור
        # במסד היה מדליק אזעקת קיפאון על כל המגירות.
        was = {}
    stamped = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        before = was.get(t.get("dev"))
        if (before is not None
                and _int(t.get("bytes_written")) == _int(before.get("bytes_written"))
                and before.get("moved_at")):
            t = {**t, "moved_at": before["moved_at"]}
        else:
            t = {**t, "moved_at": now_iso()}
        stamped.append(_stamp_window(before, _stamp_first_movement(before, t)))
    return stamped


#: ‏17/10: על פני כמה שניות של דיווחים לפחות נמדד הקצב העדכני של מגירה.
RATE_WINDOW_S = 10


def _stamp_window(before: dict | None, t: dict) -> dict:
    """‏17/10 (ברזל): ‏`recent_bps` — הבייטים שהמגירה כתבה בחלון האחרון של
    ‏`RATE_WINDOW_S` שניות לפחות, חלקי אורכו. חותמות שרת בלבד.

    הקצב שהמסך הציג היה ממוצע מאז שהגל יצא: ‏`ses_fbd8f9ca` יצא 08:24:11 והבייט
    הראשון זז 08:24:49 (‏`first_moved_at`), כלומר 38 שניות של המתנה בתוך המכנה —
    ובגל עם שאלת SMART פתוחה ההמתנה היא דקות. ‏`rate_at`/`rate_bytes` הם תחילת
    החלון הנוכחי; כשהוא מלא — הקצב נמדד והחלון מתחיל מחדש. מונה שירד (התחלה
    מחדש) או חותמת פגומה מתחילים חלון בלי קצב: "לא נמדד", לא 0."""
    t = {k: v for k, v in t.items() if k not in ("rate_at", "rate_bytes", "recent_bps")}
    now, written = now_iso(), _int(t.get("bytes_written"))
    since, base = (before or {}).get("rate_at"), _int((before or {}).get("rate_bytes"))
    try:
        seconds = (datetime.fromisoformat(now) - datetime.fromisoformat(since)).total_seconds()
    except (TypeError, ValueError):
        seconds = None
    if seconds is None or seconds < 0 or written < base:
        return {**t, "rate_at": now, "rate_bytes": written}
    if seconds < RATE_WINDOW_S:
        kept = {"recent_bps": before["recent_bps"]} if "recent_bps" in before else {}
        return {**t, "rate_at": since, "rate_bytes": base, **kept}
    return {**t, "rate_at": now, "rate_bytes": written,
            "recent_bps": int((written - base) / seconds)}


def _stamp_first_movement(before: dict | None, t: dict) -> dict:
    """‏#447: ‏`first_moved_at` — הפעם הראשונה שהמונה של היעד עלה מאפס.

    יחד עם `moved_at` הוא הזמן שבו המגירה באמת כתבה, בלי ההמתנה למקבלים
    לפני הבייט הראשון (`drive_rates.record_wave`). נשמר כל עוד המונה לא
    ירד; מונה שירד התחיל מחדש, והחותמת מתחילה איתו. חותמת השרת בלבד —
    שדה באותו שם שהגיע מהסוכן נזרק."""
    t = {k: v for k, v in t.items() if k != "first_moved_at"}
    written = _int(t.get("bytes_written"))
    if (before is not None and before.get("first_moved_at")
            and written >= _int(before.get("bytes_written"))):
        t["first_moved_at"] = before["first_moved_at"]
    elif written > 0:
        t["first_moved_at"] = now_iso()
    return t


def _ingest_task(conn: sqlite3.Connection, task_id: str, mac: str,
                 state: str, targets: list, sent_as: str = "",
                 token: str | None = None, stage: object = None) -> dict:
    """דיווח קליטה: יעד אחד — דיסק המקור — ומצב על המשימה עצמה.

    ‏`mac` כאן כבר קנוני (‏`ingest` מנרמל): ‏`tasks.mac` נכתב דרך
    ‏`registry.normalize_mac` בפתיחת המשימה, והשוואה גולמית מולו נשברת
    בדיוק כמו ב-`session_members` (#108).
    """
    row = conn.execute(
        "SELECT state, type FROM tasks WHERE id = ? AND mac = ?", (task_id, mac)
    ).fetchone()
    if row is None:
        journal(conn, "report_from_nonmember", f"{mac} for {task_id}{sent_as}")
        return {"ok": False, "error": "not this machine's task", "code": "not_member"}
    # ‏#855: משימה **פתוחה** משתנה רק בידי מי שמחזיק את האסימון שלה —
    # אותו אסימון שההעלאה דורשת (`capture.task_for`). עד כאן `task_id`
    # (65,536 ניחושים) ו-MAC (מתפרסם ב-ARP) הספיקו לדיווח `failed`
    # שהרג קליטה חיה. הסדר מכוון: משימה שכבר נסגרה עונה `not_open`
    # כמו היום, גם בלי אסימון — זה מה ש-`report_final` של הסוכן מקבל
    # אחרי שהמניפסט סגר את המשימה (#127), ואסור שייהפך ל-403.
    if row["state"] in OPEN_STATES and claim_task(conn, task_id, token) is None:
        journal(conn, "task_token_refused", f"{task_id} progress from {mac}{sent_as}")
        return {"ok": False, "error": "this is not your task", "code": "bad_token"}

    known_stage = stage if isinstance(stage, str) and stage in CAPTURE_STAGES else None
    capture_detail.sanitize(targets)   # ‏#1505: טבלת המחיצות — תקינה או נזרקת בשלמותה
    now = now_iso()
    written = sum(_int(t.get("bytes_written")) for t in targets)
    total = sum(_int(t.get("bytes_total")) for t in targets)
    errors = "; ".join(f"{t.get('dev', '?')}: {t['error']}"
                       for t in targets if t.get("error"))
    # מצב 'done' נקבע בשרת כשהמניפסט מתקבל ומאומת, לא לפי הצהרת הסוכן.
    # ‏#715: במשימת `direct_send` אין מניפסט שסוגר — הבייטים לא עברו בשרת
    # — והדיווח הסופי של המקור (‏`done`, אחרי שכל מחיצה שודרה ונחתמה שווה
    # למניפסט) הוא הראיה החיובית היחידה שיש, ולכן הוא מה שסוגר.
    new_state = "failed" if state == "failed" else (
        "done" if state == "done" and row["type"] == "direct_send" else
        "running" if row["state"] == "pending" else row["state"]
    )
    # ‏#535: תביעת מצב ב-`WHERE`. עד כאן דיווח `failed` הפך משימה
    # ל-`failed` **בלי קשר למצבה** — גם `done`, גם `cancelled` —
    # ובלי אימות מי הדווח. מכאן, משימה שאינה פתוחה אינה משתנה,
    # והדיווח מקבל תשובה שאומרת את זה בשמה.
    if not update_one(
        conn,
        # COALESCE: דיווח failed של הסוכן בלי שגיאות-יעד לא דורס סיבה
        # שהשרת כבר רשם (דחיית מניפסט, למשל) — error נשאר עם ההסבר.
        # ‏#980 §4: השלב — רק ערך מהרשימה; חסר/לא מוכר משאיר את הקודם (סוכן ישן
        # אינו מוחק שלב, וקלט פגום אינו ממציא שלב). ‏`stage_started_at` זז רק
        # כשהשלב **השתנה** — זה "כמה זמן בשלב הזה" שהקונסולה מציגה.
        "UPDATE tasks SET state = ?, bytes_written = ?, bytes_total = ?,"
        " error = COALESCE(?, error), targets_json = ?, updated_at = ?,"
        # ‏#1843: הדיווח הראשון — תחילת המשימה, לזמן הצפוי (`learn_eta.capture`)
        " started_at = COALESCE(started_at, ?),"
        " stage_started_at = CASE WHEN ? IS NOT NULL AND stage IS NOT ?"
        " THEN ? ELSE stage_started_at END,"
        " stage = COALESCE(?, stage)"
        " WHERE id = ? AND state IN ('pending', 'running')",
        (new_state, written, total, errors or None, json.dumps(targets),
         now, now, known_stage, known_stage, now, known_stage, task_id),
    ):
        journal(conn, "report_on_closed_task", f"{mac} for {task_id}{sent_as}")
        return {"ok": False, "error": "the task is no longer open",
                "code": "not_open"}
    conn.commit()
    capture_detail.record(task_id, targets, written)   # ‏#1505: דגימה לקצב ול-ETA
    if state == "failed" and row["state"] != "failed":
        journal(conn, "capture_failed", f"{task_id} {errors or 'agent reported failure'}")
    if new_state == "done":
        journal(conn, "direct_done", task_id)
    return {"ok": True}


def _sent_as(raw: object, mac: str) -> str:
    """הצורה שנשלחה, רק כשהיא נבדלת מהקנונית — אחרת מחרוזת ריקה.

    ‏`_MAC` ב-`journal_he` מתרגם רק את הצורה הקנונית לשם המחשב, ולכן
    הצורה הגולמית נשארת קריאה כפי שהיא לצד השם.
    """
    text = raw if isinstance(raw, str) else str(raw)
    return "" if text == mac else f" (sent as {text[:_RAW_SHOWN]})"


def _int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
