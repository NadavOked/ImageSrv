"""פירוט אירועי יומן שנכתב באנגלית — מתורגם בקריאה (#1503).

‏`journal(conn, event, detail)` כותב את הפירוט כמו שהקוד כתב אותו; חלק
מהקוראים כתבו משפט באנגלית (``"<mac> from <ip>"``, ``"… (reconciled on
start)"``). המפעיל קורא את היומן בקונסולה בעברית RTL, ולכן `translate`
ב-`journal_he` מעביר כל פירוט דרך `he_detail` לפני שהוא מוצג.

תרגום **בקריאה** ולא בכתיבה, ממש כמו שאר `journal_he`: שורות שכבר נכתבו
לפני התיקון (המסד חי) מתורגמות גם הן, ואין צורך להגר את הנתונים.
המפתחות וה-MAC, ה-IP ושמות הקבצים נשארים כמות שהם — רק המילים
האנגליות שביניהם מוחלפות.

אירוע בלי כלל כאן אינו נשבר — הפירוט עובר כמות שהוא. הטסט
`tests/test_journal_detail_he.py` מחזיק את הרשימה ולא נותן לה להיסחף.
"""

from __future__ import annotations

import re

_LOGIN_HE = {
    "deploy": "משתמש deploy אינו נכנס לקונסולה",
    "kiosk": "כניסת מחשב בנייה",
    "must_change_password": "נדרשת החלפת סיסמה",
    "mfa_required": "נדרש קוד אימות",
    "mfa_enrollment_required": "נדרשת הגדרת אימות דו-שלבי",
    "mfa": "אימות דו-שלבי",
    "mfa_backup": "קוד גיבוי",
}
_ANSWER_HE = {"replace": "החלפה", "rescue": "כתיבה בכל זאת", "skip": "דילוג"}
_FROM = [(r" progress from ", " התקדמות מ-"), (r" from ", " מ-")]
_FOR = [(r" for ", " עבור ")]
_AT_OPEN = [(r" at station round open$", " בפתיחת סבב מהתחנה")]
_NET_NAME = r"^(\S+) "

#: event -> [(regex, replacement)], applied in order with re.sub.
RULES: dict[str, list[tuple[str, str]]] = {
    "task_token_refused": _FROM,
    "off_vlan_contact": _FROM,
    "unknown_mac": _FROM,
    "report_on_closed_session": _FOR,
    "report_from_nonmember": _FOR,
    "report_on_closed_task": _FOR,
    "room_tick_failed": [(r"^background — ", "ברקע — ")],
    # ‏#1988: "<מיקום> <התקן> UUID=<uuid> <שם>"
    "storage_location_add_disk": [(r"\bUUID=", "מזהה מערכת הקבצים (UUID)=")],
    # ‏#1947: "<±שניות>s <סבבים>"; ‏"?" = חותמת בעתיד שהגלאי לא ראה את הקפיצה שלה.
    "clock_jump": [(r"^\? ", "גודל לא ידוע (חותמת בעתיד) "), (r"^([+-]\d+)s\b", r"\1 שניות")],
    "capture_done": [(r"\(reconciled on start\)", "(הותאם בעלייה)"),
                     # ‏#1470: האימות ברקע — כמה ובכמה זמן; ואימות שהושלם לפני אתחול
                     (r" verified (\d+) bytes in ([\d.]+)s$", r" אומת: \1 בייט ב-\2 שניות"),
                     (r"\(verified before the restart; reconciled on start\)",
                      "(אומת לפני האתחול; הותאם בעלייה)")],
    "capture_verifying": [(r" (\d+) bytes$", r" \1 בייט"),
                          (r" — the server restarted; verifying again from the start$",
                           " — השרת אותחל; האימות מתחיל מההתחלה")],   # #1470
    "vmupdate_lease": [(r"\(DHCP ", "(שרת DHCP ")],   # #1468
    "shrink_open": [(r" partitions ", " מחיצות "), (r"(\d+) sectors", r"\1 סקטורים")],
    "class_deploy_refused": _AT_OPEN,
    "agent_role_refused": _AT_OPEN,
    "update_apply_failed": [(r": script — ", ": הסקריפט — ")],
    "settings_backup_downloaded": [(r" (\d+) bytes$", r" \1 בייט")],
    "vmupdate_saved": [(r" from ", " מתוך ")],
    "nic_services_apply_failed": [(r" errors: ", " שגיאות: "), (r" · restore: ", " · שחזור: "),
                                  (r" · restored$", " · שוחזר")],
    "dhcp_apply_failed": [(r"^startup ", "בעלייה: ")],
    "dhcp_synced_at_startup": [(r"^TFTP/DHCP conf rewritten from the DB$",
                                "קובץ ההגדרות של TFTP/DHCP נכתב מחדש מהמסד")],
    "driver_upload": [(r" \((\d+) files\)", r" (\1 קבצים)")],
    "login_failed": [(r"\(mfa\)", "(אימות דו-שלבי)")],
    "smart_answered": [(r" console:", " בקונסולה · "), (r" machine$", " במכונה")],
    # ---- המסך הפיזי של השרת (DCUI) ----
    "dcui_auth_failure": [(r"^admin attempt=(\d+)", r"admin · ניסיון \1")],
    "dcui_reset_failure": [(r"^admin attempt=(\d+) reason=(\S+?)(?: locked=(\d+)s)?$",
                            r"admin · ניסיון \1 · סיבה: \2")],
    "admin_password_reset_dcui": [
        (r"^refused: no admin account$", "נדחה: אין חשבון admin"),
        (r"^admin password reset from the server console \(DCUI\); verified=(\w+)$",
         r"איפוס סיסמת admin מהמסך הפיזי של השרת (DCUI); אומת=\1")],
    "dcui_tls_display": [(r"^console certificate$", "תעודת הקונסולה")],
    "net_config_refused": [
        (r"^validation fields=", "בדיקת הטופס נכשלה · שדות="),
        (_NET_NAME + r"conflicts=(\d+)$", r"\1 · התנגשויות=\2"),
        (_NET_NAME + r"pending change$", r"\1 · יש שינוי ממתין לאישור"),
        (_NET_NAME + r"rollback timer inactive$", r"\1 · טיימר ההחזרה אינו פעיל"),
        (_NET_NAME + r"marker write failed$", r"\1 · כתיבת סמן ההחזרה נכשלה")],
    "net_config_unverified": [(r" live state mismatch$", " · המצב החי שונה מהמבוקש")],
    "dcui_management_network_restart": [
        (r" ok=false no saved configuration$", " · אין הגדרה שמורה"),
        (r" ok=false invalid saved configuration$", " · ההגדרה השמורה אינה תקינה"),
        (r" ok=false pending change$", " · יש שינוי ממתין לאישור"),        # #1537
        (r" ok=false apply failed$", " · ההפעלה מחדש נכשלה"),
        (r" ok=false live state mismatch$", " · הכתובת לא נקראה בחזרה")],
    "dcui_net_confirm_refused": [
        (r"^no pending change$", "אין שינוי ממתין"),
        (_NET_NAME + r"expired$", r"\1 · פג תוקף האישור"),
        (_NET_NAME + r"marker still present$", r"\1 · סמן ההחזרה עדיין קיים"),
        (_NET_NAME + r"management selection not read back$",
         r"\1 · בחירת כרטיס הניהול לא נקראה בחזרה")],
    "dcui_net_rollback_requested": [(r"^no pending change$", "אין שינוי ממתין")],
    # ‏#1848 / #1846: הלמידה — מזהים ומספרים נשארים, המילים בעברית
    "boot_step_slow": [(r" baseline=", " (רגיל: "), (r"s \((\d+) boots\)$", r"s, לפי \1 אתחולים)")],
    "net_loss_jump": [(r" baseline=", " (רגיל: "), (r"% \((\d+) waves\)", r"%, לפי \1 גלים)")],
}


def he_detail(event: str, detail: str) -> str:
    """הפירוט בעברית; אירוע או ניסוח לא מוכרים עוברים כמות שהם."""
    if event in ("login", "login_refused_console") and detail in _LOGIN_HE:
        return _LOGIN_HE[detail]
    text = detail
    for pattern, repl in RULES.get(event, ()):
        text = re.sub(pattern, repl, text)
    if event == "smart_answered":
        for word, he in _ANSWER_HE.items():
            text = re.sub(rf" {word}( |$)", rf" {he}\1", text)
    return text
