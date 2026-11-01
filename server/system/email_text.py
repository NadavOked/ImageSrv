"""טקסטי המייל — קטלוג אחד עם מפתח שפה (#1751, דרישת נדב 18/10).

המייל נשלח בשפת הממשק של השרת: היום עברית, מימין לשמאל; אחרי v2, כשיהיה
ממשק באנגלית (#1700), אנגלית משמאל לימין. **כל** טקסט שנכנס למייל — נושא,
כותרת, שורות האירוע, התחתית, וגם המשפטים ש-``email_alerts`` כותב ליומן
ומשם נכנסים למייל (עלייה, שרת שלא עונה) — עובר דרך ``t(lang, key)``.
אין מחרוזות מפוזרות בקוד.

השפה: ההגדרה ``ui_language`` אם קיימת וידועה, אחרת ``he``. #1700 יקבע את
המפתח הסופי; ערך לא מוכר נופל לעברית, כמו היום (``hello`` מכריז ``he``).

‏``en`` הוא מקום שמור: מלא, כדי שטסט יפיל מפתח שנוסף לעברית בלי אנגלית,
אבל אינו נבחר בשום מקום עד #1700.
"""

from __future__ import annotations

from server.db import get_setting

LANGUAGE_KEY = "ui_language"
DEFAULT_LANGUAGE = "he"
DIRECTION = {"he": "rtl", "en": "ltr"}
ALIGN = {"rtl": "right", "ltr": "left"}

CATALOG: dict[str, dict[str, str]] = {
    "he": {
        # סוגי ההתראה — גם התוויות במסך ההגדרות
        "kind.update_available": "יש עדכון זמין",
        "kind.update_installed": "עדכון הותקן",
        "kind.update_failed": "עדכון נכשל",
        "kind.server_started": "השרת עלה מחדש",
        "kind.auto_reboot": "הפעלה מחדש אוטומטית",
        "kind.round_failed": "סבב שיכפול נכשל",
        "kind.disk_red": "דיסק אדום",
        "kind.low_space": "מקום פנוי נמוך",
        "kind.peer": "שרת ראשי או שרת משנה לא עונה",
        "kind.test": "מייל בדיקה",
        # קטגוריות של כללי ההתראה
        "cat.cloning": "שיכפול",
        "cat.disks": "דיסקים",
        "cat.updates": "עדכונים",
        "cat.system": "מערכת",
        "cat.peers": "שרתי משנה",
        # שורת אירוע במייל, לפי האירוע ביומן
        "item.update_available": "יש גרסה חדשה",
        "item.update_apply_done": "העדכון הותקן",
        "item.update_apply_failed": "העדכון נכשל",
        "item.server_started": "השרת עלה",
        "item.auto_reboot": "הפעלה מחדש אוטומטית",
        "item.room_failed": "סבב שיכפול נכשל",
        "item.disk_failure": "דיסק נכשל בכתיבה",
        "item.low_space": "מקום פנוי נמוך",
        "item.peer_unreachable": "שרת לא עונה",
        "item.peer_back": "שרת חזר לענות",
        "item.test": "מייל בדיקה מההגדרות",
        "item_line": "{when} — {text}: {detail}",
        "item_line_plain": "{when} — {text}",
        # מבנה המייל
        "subject": "[ImageSrv] {server}: {label}",
        "subject_count": "[ImageSrv] {server}: {label} ({count})",
        "heading": "שרת ImageSrv ‏{server}: {label}",
        "dropped": "ועוד {count} אירועים — ראו את יומן השרת",
        "footer": "נשלח משרת ImageSrv ‏{server} · לשינוי ההתראות: הגדרות › התראות במייל",
        # זמן
        "time_unknown": "לא ידוע",
        "utc_suffix": " UTC",
        "dur.lt_minute": "פחות מדקה",
        "dur.minutes": "{minutes} דקות",
        "dur.hours": "{hours} שעות",
        "dur.hours_minutes": "{hours} שעות ו-{minutes} דקות",
        # עליית השרת (נכתב ליומן, ומשם למייל)
        "start.up": "עלה ב-{now}",
        "start.unreadable": "{up}. הדופק הקודם לא נקרא ({error}) — לא ידוע ממתי השרת לא רץ",
        "start.none": "{up}. אין דופק קודם בדיסק (התקנה חדשה או שהקובץ נמחק) — לא ידוע ממתי השרת לא רץ",
        "start.future": "{up}. הדופק הקודם ({prev}) מאוחר מעכשיו — השעון קפץ, ואי אפשר לדעת כמה זמן השרת לא רץ",
        "start.down": "{up}. השרת לא רץ בערך {duration}: מ-{prev} (הדופק האחרון שנשמר) עד {now}",
        # שרת ראשי/משני (נכתב ליומן, ומשם למייל)
        "peer.primary_down": "השרת הראשי: לא פנה לשרת הזה מאז {seen}",
        "peer.primary_back": "השרת הראשי פונה שוב לשרת הזה",
        "peer.node_down": "{name}: לא עונה מאז {seen}; כשל אחרון {failed}: {error}",
        "peer.never": "שנרשם — מעולם לא ענה",
        "peer.node_back": "{name}: עונה שוב",
    },
    "en": {
        "kind.update_available": "Update available",
        "kind.update_installed": "Update installed",
        "kind.update_failed": "Update failed",
        "kind.server_started": "Server restarted",
        "kind.auto_reboot": "Scheduled restart",
        "kind.round_failed": "Cloning round failed",
        "kind.disk_red": "Red disk",
        "kind.low_space": "Low free space",
        "kind.peer": "Primary or secondary server not answering",
        "kind.test": "Test email",
        "cat.cloning": "Cloning",
        "cat.disks": "Disks",
        "cat.updates": "Updates",
        "cat.system": "System",
        "cat.peers": "Secondary servers",
        "item.update_available": "New version",
        "item.update_apply_done": "Update installed",
        "item.update_apply_failed": "Update failed",
        "item.server_started": "Server started",
        "item.auto_reboot": "Scheduled restart",
        "item.room_failed": "Cloning round failed",
        "item.disk_failure": "Disk write failure",
        "item.low_space": "Low free space",
        "item.peer_unreachable": "Server not answering",
        "item.peer_back": "Server answering again",
        "item.test": "Test email from the settings",
        "item_line": "{when} — {text}: {detail}",
        "item_line_plain": "{when} — {text}",
        "subject": "[ImageSrv] {server}: {label}",
        "subject_count": "[ImageSrv] {server}: {label} ({count})",
        "heading": "ImageSrv server {server}: {label}",
        "dropped": "and {count} more events — see the server log",
        "footer": "Sent by ImageSrv server {server} · To change alerts: Settings › Email alerts",
        "time_unknown": "unknown",
        "utc_suffix": " UTC",
        "dur.lt_minute": "less than a minute",
        "dur.minutes": "{minutes} minutes",
        "dur.hours": "{hours} hours",
        "dur.hours_minutes": "{hours} hours and {minutes} minutes",
        "start.up": "Started at {now}",
        "start.unreadable": "{up}. The previous heartbeat was not read ({error}) — unknown since when the server was down",
        "start.none": "{up}. No previous heartbeat on disk (new install, or the file was deleted) — unknown since when the server was down",
        "start.future": "{up}. The previous heartbeat ({prev}) is later than now — the clock jumped; downtime unknown",
        "start.down": "{up}. The server was down for about {duration}: from {prev} (last saved heartbeat) until {now}",
        "peer.primary_down": "Primary server: has not contacted this server since {seen}",
        "peer.primary_back": "Primary server contacts this server again",
        "peer.node_down": "{name}: not answering since {seen}; last failure {failed}: {error}",
        "peer.never": "enrolment — never answered",
        "peer.node_back": "{name}: answering again",
    },
}


LANGUAGES = tuple(CATALOG)


def language(conn) -> str:
    """שפת הממשק של השרת; ``he`` כשאין הגדרה, כשהיא לא מוכרת, או כשלא נקראה."""
    try:
        value = get_setting(conn, LANGUAGE_KEY)
    except Exception:                                          # noqa: BLE001
        return DEFAULT_LANGUAGE
    return value if value in CATALOG else DEFAULT_LANGUAGE


def t(lang: str, key: str, **values) -> str:
    """הטקסט של ``key`` בשפה ``lang``. מפתח חסר זורק — לא נופל בשקט לשפה אחרת."""
    return CATALOG[lang][key].format(**values)


def direction(lang: str) -> str:
    return DIRECTION[lang]
