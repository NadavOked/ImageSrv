"""תרגום היומן לעברית קריאה — לאדם, לא למתכנת.

השורות נשמרות גולמיות (MAC, מזהים) כדי שלא יישברו כששמות משתנים;
התרגום קורה בקריאה: MAC הופך ל"שם · קבוצה" לפי הטבלה הנוכחית,
מזהה אימג' הופך לשם שלו מהספרייה, ושמות האירועים מקבלים עברית.
"""

from __future__ import annotations

import re
import sqlite3

from server.disks import crcwarn, disk_sets, smart_history
from server.library.images import ImageLibrary
from server.console.journal_he_detail import he_detail

EVENTS_HE = {
    "login": "כניסה לקונסולה",
    "login_failed": "ניסיון כניסה כושל",
    "login_refused_console": "כניסה לקונסולה סורבה — משתמש הפצה (#1073)",
    "login_lockout": "חשבון ננעל אחרי ניסיונות כושלים",
    "login_account_lockout": "חשבון נחסם זמנית — ניסיונות כושלים מכמה כתובות (#2443)",
    "password_changed": "סיסמה הוחלפה",
    "password_reset": "סיסמה אופסה",
    "mfa_setup": "הקמת MFA",
    "mfa_enabled": "MFA הופעל",
    "mfa_disabled": "MFA נוטרל",
    "mfa_disable_failed": "ביטול MFA נדחה — קוד שגוי",   # נדב 18/10: ביטול עצמי
    "mfa_backup_codes_failed": "קודי גיבוי חדשים נדחו — סיסמה או קוד שגויים",   # #2470
    "mfa_clock_skew": "קוד MFA נדחה בזמן שהשעון בשרת לא מדויק (#1814)",
    "login_attempts_trimmed": "מוני ניסיונות כניסה ישנים פונו — יותר מדי שמות שונים (#2459)",
    "login_account_failures_trimmed": "רשומות כשלי כניסה לפי חשבון פונו — יותר מדי שמות שונים (#2478)",
    "mfa_reset": "אימות דו-שלבי אופס למשתמש",   # נדב 18/10: מנהל איפס למשתמש אחר; הפירוט = למי
    "sessions_revoked": "הפעלות בוטלו",
    "session_revoked": "סשן בקונסולה נותק",   # #2552: מי ניתק — ביומן; הפירוט = משתמש, מזהה, כתובת
    "agent_login": "כניסה ממסך תחנה",
    "local_clone": "שיכפול מקומי במחשב שיכפול (דיווח המחשב)",   # #1322
    "local_clone_note": "שיכפול מקומי — הושלם עם הערה (דיווח המחשב)",   # #2049: a done drawer with ntfs_note
    "agent_login_failed": "ניסיון כניסה כושל במסך תחנה",
    "agent_role_refused": "פתיחת סבב נדחתה — התפקיד אינו רשאי",
    "class_deploy_refused": "פתיחת סבב כיתה נדחתה — הפצה לכיתות כבויה",
    "user_create": "משתמש נוצר",
    "user_edit": "משתמש עודכן",
    "user_delete": "משתמש נמחק",
    "group_create": "קבוצה נוצרה",
    "group_edit": "שם קבוצה שונה",
    "group_reorder": "סדר הקבוצות שונה",
    "group_delete": "קבוצה נמחקה",
    "mac_import": "ייבוא טבלת MAC",
    "machine_add": "מכונה נוספה",
    "machine_edit": "מכונה עודכנה",
    "machine_mac_edit": "כתובת ה-MAC של המכונה שונתה",   # #977 §5
    "machine_delete": "מכונה נמחקה",
    "session_open": "סבב נפתח",
    "session_start_auto": "השידור התחיל — ההמתנה הסתיימה",
    "session_start_manual": "השידור התחיל ידנית",
    "session_close": "סבב נסגר",
    # ‏#1861: ‏`event=` של `Sessions.close` — סבב גמור שנסגר כשנפתח הבא (#35).
    "session_autoclose": "סבב שהסתיים נסגר — נפתח סבב חדש",
    # ‏#411: פיוס בעליית השרת — סבב שהיה `running` בלי משדר.
    "session_orphaned": "השרת עלה מחדש — המשדר אבד, הסבב נסגר ככישלון",
    "session_image_bound": "פתיחת סבב נדחתה — האימג' קשור למכונה אחרת",
    "pull_open": "משיכת יוניקאסט התחילה",
    "pull_done": "משיכת יוניקאסט הסתיימה",
    # ‏#1573: כישלון שהמכונה דיווחה, או שתיקה מעבר לתקרה — הסיבה בפירוט.
    "pull_failed": "משיכת יוניקאסט נכשלה",
    "pull_refused": "משיכת יוניקאסט נדחתה",
    "client_done": "מחשב סיים לכתוב",
    "client_failed": "כתיבה נכשלה במחשב",
    # ‏#1801/#1861: ‏`f"client_{state}"` (reports.py) — ‏partial = חלק מהמגירות נכתבו וחלק לא.
    "client_partial": "מחשב סיים חלקית — חלק מהדיסקים לא נכתבו",
    "wol_sent": "המחשבים הוערו (WoL)",
    "wol_failed": "הערת WoL נכשלה",
    "wol_arm_failed": "המחשב לא הצליח לדרוך Wake-on-LAN לפני כיבוי",
    "net_loss_jump": "האבדן ברשת ההפצה קפץ מול הבסיס שלה — סוויץ'/כבל/IGMP",
    "wol_resent": "WoL נשלח שוב — המחשב לא ענה בזמן הטיפוסי שלו",
    "wol_no_wake": "המחשב לא מתעורר מ-WoL — BIOS/כבל",
    "model_speed_dropped": "מדידת בדיקת הכתיבה של דיסק לא נשמרה — אין דגם או מספר סידורי ב-hello של המחשב (#2532)",
    "boot_step_slow": "שלב באתחול התארך פי 2 מהבסיס של המחשב",
    # ‏#1882: הזנב של לוג הסוכן נשמר בשרת (מסונן), עם הכשלים המוכרים שזוהו בו.
    "agent_log": "הלוג של המכונה מסוף הסבב נשמר בשרת",
    # ‏#1888: ה-IP של מכונה השתנה — חכירה חדשה, DHCP זר, או מכונה שעברה רשת.
    "ip_changed": "כתובת ה-IP של המכונה השתנתה",
    # ‏#1885: ה-throttle של מעבד המשכפל עלה בגל — הגל לא נכנס ללמידת הקצב.
    "cloner_throttled": "המשכפל התחמם/הואט בגל — בדקו אוורור; הגל לא נכנס ללמידת הקצב",
    # ‏#1884: השרת חיכה לדיסק של הספרייה חלק ניכר מזמן הזרם — הגל לא נכנס ללמידה.
    "server_disk_slow": "דיסק הספרייה בשרת היה צוואר הבקבוק — הגל לא נכנס ללמידת הקצב",
    # ‏#1886: לפני שהקליטה נוצרה — אין מקום לגרסה הקודמת של האימג' (סירוב), או שאין למה להשוות.
    "capture_space_refused": "קליטה סורבה לפני שהתחילה — אין מקום בשרת לגרסה הקודמת של האימג'",
    "capture_space_unchecked": "קליטה התחילה בלי השוואה לגרסה קודמת — המקום ייבדק כשהסוכן ימדוד",
    "room_open": "סבב חדר שיכפולים נפתח",
    "room_open_cloner": "סבב חדר נפתח ממסך מחשב שיכפול (המגירות שלו בלבד)",   # 16/10
    "cloner_round_refused": "פתיחת סבב ממסך מחשב שיכפול נדחתה",   # 16/10
    "room_wave": "גל חדש בחדר השיכפולים",
    "room_wave_lost": "הגל של סבב החדר נסגר לפני הזמן — ממתין לגל הבא",
    "room_drawer_changed": "המגירה הוחלפה אחרי סיום הכתיבה",
    # ‏#1386: "בטל דיסק זה" — הבקשה, ואחריה הראיה מהסוכן (המגירה דיווחה cancelled).
    "room_drawer_cancel_requested": "התבקש ביטול הכתיבה לדיסק אחד בגל",
    "room_drawer_cancelled": "הכתיבה לדיסק בוטלה — טבלת המחיצות נמחקה, שאר הדיסקים ממשיכים",
    # ‏#105: כונן בלי serial שסיים — לא הוחלף, פשוט אין זהות לספור.
    "room_drawer_unidentified": "לא ניתן לזהות את הכונן (אין serial) — הכתיבה לא נספרה",
    "room_drawer_serial_conflict": "אותו serial במכונה אחרת — הכתיבה לא נספרה, כדי לא לסמן 'נכתב' מגירה שלא נכתבה",   # #2421
    "room_drawer_wake_mismatch": "המשכפל עלה ובחריץ שנבחר אין את הדיסק שנבחר — המגירה דולגה ולא נכתבה",   # #1954
    "room_drawer_no_disk": "בחריץ שנבחר לסבב אין דיסק — המגירה נכשלה ולא נכתבה, שאר המגירות נכתבו",   # #2201
    "room_done": "סבב חדר השיכפולים הושלם",
    "room_failed": "סבב חדר השיכפולים נכשל — אף מגירה לא נכתבה בגל",   # 17/10
    # ‏#1721: ה-WoL של הגל נבלע (המשכפל עוד היה בספירת הכיבוי) — נשלח שוב, עם תקרה.
    "room_rewake": "מחשב שיכפול לא חזר לגל הפתוח — נשלח WoL חוזר",
    "room_wake_failed": "סבב חדר השיכפולים נכשל — מחשב שיכפול לא התעורר לגל",
    "room_close": "סבב חדר השיכפולים נסגר",
    # ‏#980: כיבוי החדר עובר דרך הסוכן (hello) — בקשה, מסירה, או פקיעה.
    # ‏#977 §4: אותו ערוץ לאתחול מחשב אחד; הפעולה עצמה בטקסט (כיבוי/אתחול).
    "room_poweroff": "התבקש כיבוי כל מחשבי השיכפול",
    "machine_reboot": "התבקש אתחול מרחוק",
    # ‏#65: "כבה אותו" — מחשב שיכפול אחד שחסר בגל הפתוח, כדי להחליף בו מגירות.
    "room_missing_poweroff": "התבקש כיבוי מחשב שיכפול שחסר בגל",
    "held_poweroff": "התבקש כיבוי משכפל שמחכה ללחיצה (דיסק עם בעיה)",   # #2031
    # ‏#432: הגל יצא לדרך עם אזהרת precheck על מגירה. ‏#2704: בלי סיבה בתווית הכללית (רשימת הסינון,
    # ושורה שאי-אפשר לקרוא) -- מאז #1844/#1845/#447 האזהרה היא גם למידה וכתיבה איטית, לא רק קישור/CRC.
    # הסוג לפי הפירוט: `precheck_he`.
    "room_precheck_warned": "הגל יצא לדרך למרות אזהרה על מגירה",
    "power_delivered": "בקשה מהקונסולה נמסרה לסוכן",
    "power_expired": "בקשה מהקונסולה פגה — המכונה לא ענתה בזמן, והבקשה בוטלה",
    "send_start": "השידור יצא לדרך",
    # ברזל 17/10: מחשב שיכפול בגל עומד על שאלת SMART — השולח לא מתחיל עד התשובה.
    "send_held_smart": "השידור ממתין לתשובה על דיסק (SMART) — הזמן לא נספר",
    "send_hold_released": "אין עוד שאלה פתוחה על דיסק — השידור ממשיך",
    # ‏#2202: וכל שאלה אחרת למפעיל (חלוקת המקום, RAID) — אותה עצירה, בשם משלה.
    "send_held_prompt": "השידור ממתין לתשובת מפעיל — הזמן לא נספר",
    "send_prompt_released": "אין עוד שאלה פתוחה למפעיל — השידור ממשיך",
    "prompt_hold_expired": "שאלה למפעיל לא נענתה בזמן — המכונה שוחררה מהגל, השאר ממשיכים",
    # ‏#1950: חבר שסומן "אבד" קיבל את המחיצה הקודמת עד סופה — השולח ממתין גם לו.
    "send_counts_lost": "מחשב שסומן כאבוד עדיין מקבל את הזרם — השידור ממתין גם לו",
    # ‏#1430: חבר בגל לא דיווח מעל דקה — המחיצה הבאה אינה סופרת אותו ויוצאת כשהשאר מוכנים.
    "send_quiet_wait": "מחשב בגל לא דיווח מעל דקה — המחיצה הבאה יוצאת בלי להמתין לו",
    "smart_ask_expired": "שאלת SMART לא נענתה בזמן — המכונה שוחררה מהגל, השאר ממשיכים",
    "wave_rate": "נקבע קצב לגל",
    "machine_nic_speed": "מהירות כרטיס הרשת של מכונה נקבעה ידנית",
    "drive_rates_failed": "קצב אוטומטי: בחירה או רישום נכשלו — בחירה שנכשלה יוצאת בקצב הפתיחה",
    "send_done": "השידור הסתיים",
    "send_failed": "השידור נכשל",
    "send_stopped": "השידור נעצר",
    # ‏#715: הפצה ישירה — המקור הוא מחשב הבנייה, השרת מתזמר בלבד.
    "room_open_direct": "סבב הפצה ישירה ממחשב הבנייה נפתח",
    "send_delegated": "הגל יצא — מחשב הבנייה משדר מהדיסק שלו",
    "direct_manifest": "מחשב הבנייה סיים לקרוא את הדיסק — המניפסט הגיע",
    "direct_done": "השידור ממחשב הבנייה הסתיים",
    "direct_failed": "השידור ממחשב הבנייה נכשל",
    "direct_cancel": "השידור ממחשב הבנייה בוטל",
    "task_boot_cancel": "משימה מהגואי בוטלה: המכונה אותחלה לפני תחילתה",
    "unknown_mac": "מחשב לא רשום ניסה לעלות",
    "journal_flood": "הצפה ביומן — אירועים ממקורות שונים נספרים ולא נרשמים אחד-אחד (#2498)",
    "disk_failure": "דיסק נכשל בכתיבה — נרשם לזיכרון הכשלים",
    "disk_failure_cleared": "רשומת כשל דיסק נוקתה",
    "boot_loop_local": "מחשב אתחל שוב ושוב — נשלח לדיסק המקומי",
    "boot_loop_unverified": "ספירת האתחולים נכשלה — המחשב נשלח לדיסק המקומי",
    "agent_loop": "מחשב הגיע לסוכן בלי משימה ובלי סבב",
    "gui_failed": "הגואי לא עלה",   # 11/10: gui_failure.py
    "agent_loop_unverified": "ספירת ההגעות לסוכן נכשלה",
    "report_from_nonmember": "דיווח ממחשב שאינו בסבב",
    # ‏#855: שומר הזהות — MAC מוצהר מול חכירת ה-DHCP.
    "identity_refused": "פנייה בשם מחשב מכתובת שאינה שלו — סורבה",
    "identity_unverifiable": "זהות מחשב לא ניתנת לבדיקה — סורבה",
    "image_fetch_refused": "בקשת אימג׳ ממחשב בלי סבב או משיכה פתוחים לאימג׳ — סורבה",   # #2657
    "setting_change": "הגדרה שונתה",
    "auto_reboot": "הפעלה מחדש אוטומטית",
    "storage_group_create": "קבוצת שרתי משנה נוצרה",
    "storage_group_edit": "שם קבוצת שרתי משנה שונה",
    "storage_group_reorder": "סדר קבוצות שרתי המשנה שונה",
    "storage_group_delete": "קבוצת שרתי משנה נמחקה",
    "storage_node_edit": "שרת משנה עודכן",
    "storage_node_disable": "שרת משנה הושבת",
    "storage_node_enable": "שרת משנה הופעל מחדש",
    "storage_node_delete": "שרת משנה הוסר",
    "storage_node_enroll": "שרת משנה נרשם",
    "storage_transfer_start": "העברת אימג' לשרת המשנה התחילה",
    "storage_transfer_done": "העברת אימג' לשרת המשנה הושלמה",
    "storage_transfer_failed": "העברת אימג' לשרת המשנה נכשלה",
    "storage_image_received": "אימג' התקבל מהשרת הראשי",
    "storage_monitor_tunnel": "מוניטור למכונה נפתח מהשרת הראשי",
    "monitor_connected": "מוניטור נפתח למכונה",
    "monitor_closed": "מוניטור נסגר",
    "monitor_refused": "מוניטור לא נפתח",   # ‏#1855: "<mac> <קוד> <סיבה>"
    "monitor_power": "פקודת כוח דרך המוניטור",
    "monitor_control_request": "בקשת שליטה במכונה במוניטור",
    "monitor_control_approved": "שליטה במוניטור הועברה באישור",
    "monitor_control_denied": "בקשת שליטה במוניטור נדחתה",
    "monitor_control_timeout": "בקשת שליטה במוניטור לא נענתה",
    "monitor_control_auto": "שליטה במוניטור הועברה — המחזיק לא נראה",
    # ‏#1334: עדכון אימג' בתוך VM על השרת.
    "vmupdate_start": "עדכון ב-VM: הדיסק נבנה מהאימג'",
    "vmupdate_running": "עדכון ב-VM: ה-VM הודלק",
    "vmupdate_failed": "עדכון ב-VM נכשל",
    "vmupdate_stopped": "עדכון ב-VM: ה-VM כבה",
    "vmupdate_shutdown": "עדכון ב-VM: בקשת כיבוי (ACPI)",
    "vmupdate_force_off": "עדכון ב-VM: כיבוי בכוח",
    "vmupdate_boot": "עדכון ב-VM: הדלקה חוזרת",
    "vmupdate_save": "עדכון ב-VM: השמירה כגרסה חדשה התחילה",
    "vmupdate_saved": "עדכון ב-VM: נשמרה גרסה חדשה של האימג'",
    "vmupdate_discard": "עדכון ב-VM: דיסק העבודה נמחק",
    "vmupdate_settings": "הגדרות ה-VM לעדכון שונו",
    "vmupdate_viewer_open": "מסך ה-VM נפתח",
    "vmupdate_viewer_closed": "מסך ה-VM נסגר",
    "vmupdate_expired": "עדכון ב-VM: דיסק עבודה שלא הוכרע 7 ימים נמחק",
    "vmupdate_firewall_lost": "עדכון ב-VM: בידוד הרשת נמחק ולא נטען מחדש — ה-VM נכבה",
    "vmupdate_network_set": "כרטיס לעדכוני VM שונה",
    "vmupdate_mac_created": "נוצר MAC ל-VM לעדכון — צריך לו כתובת שמורה ב-DHCP",
    "vmupdate_lease": "ה-VM לעדכון קיבל כתובת מה-DHCP",
    "vmupdate_no_lease": "ה-VM לעדכון לא קיבל כתובת — ה-VM נכבה",
    "vmupdate_net_teardown_failed": "פירוק הרשת של ה-VM לעדכון לא הושלם",
    "vmupdate_leftover_failed": "עדכון ב-VM: שאריות של שמירה קודמת לא נוקו",   # #1649
    "vmupdate_control_request": "עדכון ב-VM: בקשת שליטה",
    "vmupdate_control_granted": "עדכון ב-VM: השליטה הועברה באישור",
    "vmupdate_control_denied": "עדכון ב-VM: בקשת שליטה נדחתה",
    "vmupdate_control_timeout": "עדכון ב-VM: בקשת שליטה פגה בלי תשובה",
    "vmupdate_control_auto": "עדכון ב-VM: השליטה הועברה — השולט לא נמצא",
    "image_default_set": "גרסת ברירת המחדל של אימג' שונתה",
    "logo_set": "לוגו הוחלף",
    "logo_refused": "העלאת לוגו נדחתה",
    "logo_clear": "הלוגו הוסר",
    "console_tls_uploaded": "תעודת TLS לקונסולה הועלתה",
    "console_tls_reissued": "תעודת TLS עצמית חדשה הונפקה לקונסולה",
    "console_tls_refused": "העלאת תעודת TLS נדחתה",
    "console_tls_restart_failed": "תעודת TLS נכתבה אך אתחול השרת לא תוזמן",
    "console_tls_regenerated": "תעודת TLS של הקונסולה חודשה עבור שם השרת",
    "hostname_changed": "שם המארח של השרת שונה",
    "hostname_change_failed": "שינוי שם המארח נכשל",
    "timezone_changed": "אזור הזמן של השרת שונה",
    "timezone_change_failed": "שינוי אזור הזמן של השרת נכשל",
    "timezone_applied": "אזור הזמן של המערכת יושר להגדרה בעליית השרת",
    "timezone_apply_failed": "יישור אזור הזמן של המערכת בעליית השרת נכשל",
    "timesync_changed": "מקור השעה",
    "timesync_change_failed": "שינוי מקור השעה נכשל",
    "timesync_applied": "מקור השעה הוחל בעליית השרת",
    "timesync_apply_failed": "החלת מקור השעה בעליית השרת נכשלה",
    "hostname_restart_failed": "שם המארח שונה אך אתחול השרת לא תוזמן",
    "image_edit": "אימג' עודכן",
    "image_delete": "אימג' נמחק",
    "image_system_partition": "סומנה מחיצת המערכת (C:) באימג' ישן",   # #1896
    "image_used_bytes_derived": "נפח בשימוש של אימג' ישן הושלם מכותרת partclone",   # #1494
    "image_used_bytes_underived": "נפח בשימוש של אימג' ישן לא הושלם",               # #1494
    "image_readback_derived": "גיבוב לקריאה חוזרת נרשם לאימג'",                  # #1805
    "image_readback_underived": "גיבוב לקריאה חוזרת לא נרשם לאימג'",              # #1805
    "readback_list_not_fetched": "הדיסק הושלם ולא אומת — רשימת הבדיקה לא הורדה מהשרת",   # #1907
    "readback_mode_switched": "האימות אחרי הכתיבה עבר ממדגם למלא — בהפצה ישירה אין לשרת רשימת בדיקה",   # #2218
    "capture_start": "קליטת אימג' הוזמנה",
    "capture_verifying": "אימג' התקבל — מאמת לפני הכניסה לספרייה",
    "capture_done": "אימג' נקלט",
    "capture_failed": "קליטת אימג' נכשלה",
    "capture_space_warning": "אזהרת מקום בקליטה — יחס הדחיסה אינו ידוע",
    "capture_cancel": "קליטת אימג' בוטלה",
    "work_area_swept": "אזורי עבודה יתומים נמחקו",
    "work_area_kept": "אזורי עבודה נשארו — לא הוכח שהם יתומים",
    "retention_purged": "ניקוי יומי: רשומות ישנות נמחקו (#1737)",
    "retention_failed": "ניקוי יומי: חלק לא בוצע (#1737)",
    "image_download": "אימג' הורד למחשב",
    "image_upload": "אימג' הועלה מהמחשב",
    "iso_upload": "ISO הועלה לשרת",   # #404
    "iso_delete": "ISO נמחק מהשרת",
    "iso_folder_create": "תיקיית ISO נוצרה",
    "iso_folder_delete": "תיקיית ISO נמחקה",
    # ‏#1861: הקריאה פרוסה על שתי שורות ב-app.py, והסריקה שורה-שורה לא ראתה אותה.
    "image_manifest_rejected": "אימג' הוצא מהספרייה — המניפסט שלו פגום",
    "driver_upload": "חבילת דרייברים יובאה",
    "driver_delete": "חבילת דרייברים נמחקה",
    "tools_selection": "בחירת ארגז הכלים נשמרה",   # #649
    "drivers_staged": "דרייברים הונחו על הדיסק המשוחזר",
    "drivers_failed": "השחזור הושלם, דרייברים לא הונחו",
    "boot_storage_failed": "השחזור הושלם, דרייבר האחסון לא הוגדר לעלייה",   # #1331
    "linux_initramfs_warning": "השחזור הושלם, ה-initramfs של הלינוקס לא אומת לדיסק הזה",   # #1331
    "folder_create": "תיקייה נוצרה",
    "folder_reorder": "סדר התיקיות שונה",
    "folder_edit": "תיקייה עודכנה",
    "folder_delete": "תיקייה נמחקה",
    "net_add": "התקן נוסף לרשימת הרשת",
    "net_describe": "תיאור התקן עודכן",
    "net_forget": "התקן הוסר מרשימת הרשת",
    "dhcp_set": "הגדרת DHCP על כרטיס רשת שונתה",
    "dhcp_proxy_risk": "מצב proxy הודלק על גרסת dnsmasq שלא נבדקה",
    "nic_add": "כרטיס רשת נוסף",
    "nic_forget": "הגדרות כרטיס רשת הוסרו",
    "dhcp_apply_failed": "החלת הגדרת DHCP נכשלה",
    "known_macs_apply_failed": "עדכון רשימת המכונות הרשומות ל-dnsmasq נכשל",
    "dnsmasq_dropin_applied": "הניסיון החוזר של dnsmasq הותקן ונטען בעליית השרת",   # #2174
    "dnsmasq_dropin_failed": "התקנת הניסיון החוזר של dnsmasq בעליית השרת נכשלה",   # #2174
    "net_config": "הגדרת הרשת של כרטיס בשרת שונתה",
    "net_config_unverified": "שינוי הרשת לא אומת מול המצב בפועל",
    "net_rollback_armed": "שינוי רשת ממתין לאישור — בלעדיו יוחזר",
    "net_confirmed": "שינוי הרשת אושר — החיבור עדיין חי",
    "net_rollback": "הוחזרה תצורת רשת קודמת, השינוי לא אושר",
    "net_rollback_unreadable": "הוחזרה תצורת רשת, הפרטים לא ניתנים לקריאה",
    "net_rollback_marker_stuck": "סמן החזרה ישן של הרשת לא נמחק",   # #2076
    # ‏#705: שירותים לכל כרטיס רשת
    "nic_services_set": "השירותים של כרטיס רשת שונו",
    "nic_services_armed": "כיבוי הקונסולה בכרטיס ממתין לאישור — בלעדיו יחזור",
    "nic_services_confirmed": "השינוי בשירותי הכרטיס אושר מהכתובת האחרת",
    "nic_services_reverted": "השירותים של כרטיס רשת חזרו להגדרה הקודמת",
    "nic_services_apply_failed": "החלת השירותים של כרטיס רשת נכשלה — ההגדרה הקודמת הוחזרה",
    # ‏#292: 55 אירועים שנכתבו בפועל ולא היו כאן — הגיעו למסך כמפתח באנגלית.
    # ‏tests/test_journal_events_he.py מצליב מעכשיו כל `journal(conn, "…")` מול הטבלה.
    "admin_password_reset_dcui": "סיסמת המנהל אופסה ממסך השרת (DCUI)",
    "bind_address_changed": "כתובת ההאזנה של השרת השתנתה",
    "client_lost": "מכונה אבדה באמצע גל — לא דיווחה בזמן",
    # ‏#1947: שעון השרת זז (NTP, ‏snapshot, סנכרון מהמארח) — הזמנים של הסבבים הפעילים כוילו.
    "clock_jump": "שעון השרת קפץ — זמני הסבבים הפעילים כוילו מחדש",
    # ‏#1728: אתחול באמצע הזרם — יצא מהגל וממתין לגל הבא.
    "room_rejoin": "מחשב שיכפול אותחל באמצע הזרם — ממתין לגל הבא",
    "console_tls_by_name_only": "תעודת הקונסולה תקפה לשם בלבד, לא לכתובת",
    "dcui_auth_failure": "ניסיון כניסה כושל במסך השרת (DCUI)",
    "dcui_auth_locked": "מסך השרת ננעל אחרי ניסיונות כושלים",
    "dcui_auth_success": "כניסה למסך השרת (DCUI)",
    "dcui_management_network_restart": "רשת הניהול הופעלה מחדש ממסך השרת",
    "dcui_net_confirm_refused": "אישור שינוי רשת ממסך השרת נדחה — אין שינוי ממתין",
    "dcui_net_rollback_requested": "התבקשה החזרת רשת ממסך השרת",
    "dcui_power_action": "כיבוי/הפעלה מחדש ממסך השרת",
    "dcui_advertised_url": "הכתובת שהראשי רואה נשמרה במסך השרת",       # 16/10
    "dcui_power_refused": "כיבוי/הפעלה מחדש ממסך השרת נדחו — שם השרת לא תאם",
    "dcui_reset_failure": "ניסיון כושל לאפס את סיסמת המנהל ממסך השרת (DCUI)",
    "dcui_services_restart": "השירותים הופעלו מחדש ממסך השרת",
    "dcui_tls_display": "תעודת הקונסולה הוצגה במסך השרת",
    "dcui_wizard_rerun": "אשף ההתקנה הופעל מחדש ממסך השרת",
    "deploy_net_failed": "הגדרת רשת ההפצה נכשלה",
    "deploy_net_set": "רשת ההפצה הוגדרה",
    "net_deploy_saved": "הגדרות רשת ההפצה נשמרו (כתובת השרת נגזרה מהרשת)",
    "net_deploy_bond": "כרטיס שני ברשת ההפצה עודכן (bond0)",
    "net_deploy_bond_failed": "עדכון הכרטיס השני ברשת ההפצה נכשל — ההגדרה הקודמת הוחזרה",
    "dhcp_synced_at_startup": "תצורת DHCP/TFTP נכתבה מחדש מה-DB בעלייה",
    "disk_decision": "הכרעה על דיסק (SMART) נרשמה",
    "fw_changed": "הקושחה השתנתה מאז ההדלקה הקודמת",   # #2080
    "fw_accepted": "תמונת הקושחה נשמרה כהגדרה הנכונה",
    "disk_set_created": "סט דיסקים נוצר",   # #1857
    "disk_set_assigned": "דיסקים שויכו לסט",
    "disk_set_replaced": "דיסק בסט הוחלף",
    "disk_set_removed": "דיסק הוצא מסט",
    "disk_f1_marked": "נצפה F1 בהדלקה — דיסק סומן",   # #1858
    "disk_f1_cleared": "סימון \"נצפה F1\" של דיסק נוקה",
    "machine_drawer_count": "מספר המגירות של מכונה עודכן",
    "machine_drawer_slots": "מיפוי חיבורי הדיסקים של מכונה עודכן",
    "learning_changed": "הלמידה התאימה הגדרה",   # #2028
    "learning_done": "הלמידה הסתיימה",
    "learning_machine": "מנהל ענה על הצעת הלמידה למחשב",
    "usb_stick_sync": "סנכרון עם דיסק-און-קי שמחובר לשרת (#1920)",   # הפירוט: מצב, מזהה ההתקן, כמה נקלטו/נדחו/נכתבו
    "learning_not_learned": "הלמידה לא הגיעה למספר הנדרש, לא שונה דבר",
    "learning_overridden": "שינוי ידני גבר על הגדרה שהלמידה התאימה",
    "learning_rejected": "הלמידה דחתה מדידות כתיבה שאינן סבירות או שאינן מסבב רשום",
    "learning_reverted": "הגדרה שהלמידה התאימה הוחזרה",
    "learning_wave": "שיכפול נספר בסבב הלמידה",
    "machine_slot_map": "מיפוי החיבורים נשמר (מחשב או כל מחשבי הדגם)",
    "machine_slot_name": "שם של חיבור כונן במכונה עודכן",
    "monitor_stations": "מתג המוניטור בתחנות שונה",
    "net_config_refused": "שינוי רשת נדחה",
    "off_vlan_contact": "מכונה פנתה לשרת מחוץ לוילן ההפצה",
    "off_vlan_unverified": "לא ניתן היה לאמת מאיזו רשת פנתה המכונה",
    "port_toggle": "מתג פורט שונה",
    "port_unverified": "שינוי פורט לא אומת מול המצב בפועל",
    "pull_retry": "משיכה נפתחה מחדש למכונה",
    "report_on_closed_session": "דיווח הגיע לסבב שכבר נסגר",
    "report_on_closed_task": "דיווח הגיע למשימה שכבר נסגרה",
    "room_role_denied": "פעולה בחדר המשכפלים נדחתה — התפקיד אינו רשאי",
    "room_tick_failed": "שעון חדר המשכפלים נכשל — החדר אינו מתקדם",
    "pull_sweep_failed": "סגירת משיכות ברקע נכשלה — משיכה תקועה עלולה לחסום",
    "session_dedupe": "סבב כפול אוחד",
    "session_role_denied": "פעולה בסבב נדחתה — התפקיד אינו רשאי",
    "shrink_cleared": "רשומת כיווץ נוקתה",
    "shrink_note": "הערת כיווץ נרשמה לדיסק",
    "shrink_refused": "סגירת רשומת כיווץ נדחתה — הבקשה הגיעה ממכונה אחרת מזו שפתחה אותה",   # #2413
    "storage_iscsi_login": "התחברות iSCSI למיקום אחסון",
    "storage_location_connect": "התחברות למיקום אחסון",
    "storage_location_connected": "מיקום אחסון מחובר",
    "storage_location_create": "מיקום אחסון נוצר",
    "storage_location_delete": "מיקום אחסון נמחק",
    "storage_location_disconnect": "מיקום אחסון נותק",
    "storage_location_disconnect_failed": "ניתוק מיקום האחסון נכשל — המיקום נשאר מחובר",   # #2126
    "storage_location_format": "מיקום אחסון פורמט",
    "storage_location_mount": "מיקום אחסון עוגן",
    "storage_location_unreachable": "מיקום אחסון אינו נגיש",
    # ‏#1988: "הוסף כונן" (server/storage/disks.py)
    "storage_disk_prepare_start": "הכנת כונן חדש למיקום אחסון",
    "storage_disk_prepare_failed": "הכנת הכונן החדש נכשלה",
    "storage_disk_add_failed": "הוספת הכונן נכשלה — לא נוסף מיקום",
    "storage_location_add_disk": "כונן חדש נוסף כמיקום אחסון",
    "storage_monitor_tunnel_closed": "מנהרת מוניטור למכונה בשרת המשנה נסגרה",
    "storage_pairing_open": "צימוד שרת משנה נפתח",
    "storage_pairing_closed": "קוד הצימוד בוטל במסך השרת",            # DCUI F4, 16/10
    "storage_parent_cert_renewed": "תעודת השרת הראשי חודשה בשרת המשנה",
    "storage_parent_unbind": "שרת המשנה נותק מהראשי",
    "peer_write": "פעולת כתיבה בשרת משנה מהקונסולה של הראשי",   # #1179
    "smart_answered": "שאלת SMART על דיסק נענתה",               # #989
    "task_token_refused": "אסימון משימה נדחה",
    "settings_backup_downloaded": "גיבוי ההגדרות הורד",
    # ‏#2437: סיסמת עורך ה-GRUB (‏server/app.py `_grub_lock`), ו-#2450: מנהל ראה אותה.
    "grub_password_viewed": "סיסמת ה-GRUB הוצגה למנהל",
    "grub_server_lock_failed": "ה-GRUB של השרת לא ננעל בעדכון",   # #2452, tools/server-upgrade.sh
    "grub_password_saved": "מנהל סימן שסיסמת ה-GRUB נשמרה מחוץ לשרת",   # #2454
    "update_apply_busy": "החלת עדכון נדחתה — יחידת השדרוג כבר רצה",
    "update_forced": "עדכון הוחל מעל חסימה באישור מנהל",   # #1891
    "update_apply_failed": "החלת עדכון נכשלה",
    "update_apply_started": "החלת עדכון החלה",
    "update_check": "בדיקת עדכון מול הריפו הציבורי",
    "user_disabled": "משתמש הושבת",
    "user_enabled": "משתמש הופעל מחדש",
    "send_simulated": "שידור מדומה (סימולציה בלבד — אין udp-sender)",
    "shrink_open": "רשומת כיווץ נפתחה לדיסק — הטבלה המקורית נשמרה בשרת",
    # ‏#1861: ברירת המחדל של `event` ב-`shrink_records.close_record`.
    "shrink_close": "רשומת כיווץ נסגרה",
    # ‏#2442 (א): ‏server/net/rogue_dhcp.py
    "foreign_dhcp_seen": "נראה שרת DHCP או proxyDHCP זר ברשת ההפצה",
    "foreign_dhcp_more": "עוד תשובות DHCP זרות ממקורות שמעבר לתקרה — אולי הצפה (#2442)",
    # ‏#1973: ה-UPS (‏server/system/ups.py).
    "ups_on_battery": "החשמל נפל — השרת על סוללת UPS",
    "ups_power_restored": "החשמל חזר — השרת על חשמל",
    "ups_low_battery": "סוללת ה-UPS נמוכה",
    "ups_work_stopped": "עבודה נעצרה לפני כיבוי (UPS)",
    "ups_shutdown": "השרת נכבה מסודר — סוללת UPS נמוכה",
    "ups_shutdown_failed": "כיבוי השרת בסוללה נמוכה נכשל",
    "ups_unreachable": "ה-UPS לא נקרא",
    "ups_reachable": "ה-UPS נקרא שוב",
    "ups_config_changed": "הגדרת ה-UPS שונתה",
    "ups_config_failed": "הגדרת ה-UPS נכשלה",
    # #1751: התראות במייל
    "server_started": "השרת עלה",
    "update_available": "יש עדכון זמין",
    "peer_unreachable": "שרת ראשי או שרת משנה לא עונה",
    "peer_back": "שרת ראשי או שרת משנה חזר לענות",
    "low_space": "מקום פנוי נמוך בשרת — מתחת לרזרבת המערכת",   # #1751
    "smart_red": "דיסק אדום — SMART תקול",   # #1751
    "email_alert_sent": "התראה נשלחה במייל",
    "email_alert_refused": "נמענים דחו התראה במייל",
    "email_alert_failed": "שליחת התראה במייל נכשלה — תנסה שוב",
    "email_alert_gave_up": "התראה במייל לא נשלחה — הניסיונות נגמרו",
    "email_alert_requeued": "התראות שנקטעו באמצע שליחה חזרו לתור",
    "email_alerts_settings": "הגדרות ההתראות במייל שונו",
    "email_alerts_password_cleared": "סיסמת שרת הדואר נמחקה — היעד שונה בלי סיסמה",
    "email_alerts_step_failed": "תהליכון ההתראות במייל נתקל בתקלה",
    "email_test": "מייל בדיקה",
    "email_oauth_connected": "חשבון שליחה התחבר (OAuth)",
    "email_oauth_failed": "התחברות חשבון שליחה (OAuth) נכשלה",
    "grub_password_created": "נוצרה סיסמת GRUB לנעילת תפריט האתחול (#2437)",
    "grub_password_failed": "יצירת סיסמת GRUB נכשלה — התפריט נעול בלי סיסמה (#2437)",
    "grub_cfg_lock_failed": "נעילת grub.cfg הקיים על ה-TFTP נכשלה (#2437)",
}

#: ‏#968: חומרת האירוע לציר-הזמן ולעמודת הצבע ביומן — נקבעת **כאן**, לצד
#: התרגום, ולא בהיוריסטיקה על שם ה-event בקונסולה (שהתיישנה עם כל
#: אירוע חדש). ארבעה ערכים: ``ok`` הושלם/אושר · ``warn`` דורש תשומת לב ·
#: ``err`` כשל/סירוב · ``info`` (ברירת המחדל לכל אירוע שאינו ברשימה,
#: כולל אירוע שאינו ב-EVENTS_HE — לא נופל, לא צובע).
_SEVERITY_ERR = {
    "login_failed", "login_refused_console", "login_lockout", "login_account_lockout",
    "mfa_backup_codes_failed",   # #2470
    "agent_login_failed", "agent_role_refused", "class_deploy_refused",
    "session_image_bound", "pull_refused", "pull_failed", "pull_sweep_failed", "cloner_round_refused", "client_failed", "wol_failed",
    "room_wave_lost", "room_failed", "room_wake_failed", "room_drawer_no_disk", "send_failed", "direct_failed", "disk_failure",
    "session_orphaned", "identity_refused", "storage_transfer_failed", "storage_disk_prepare_failed", "storage_disk_add_failed", "storage_location_disconnect_failed", "logo_refused",
    "capture_failed", "drivers_failed", "boot_storage_failed", "dhcp_apply_failed", "nic_services_apply_failed",
    "known_macs_apply_failed", "dnsmasq_dropin_failed", "vmupdate_failed", "vmupdate_firewall_lost", "retention_failed",
    "vmupdate_no_lease", "vmupdate_net_teardown_failed",
    "foreign_dhcp_seen", "foreign_dhcp_more",   # #2442
    "ups_low_battery", "ups_shutdown", "ups_shutdown_failed", "ups_unreachable", "ups_config_failed",
    "peer_unreachable", "email_alert_gave_up", "email_alerts_step_failed", "email_oauth_failed",   # #1751
    "smart_red",   # #1751
}
_SEVERITY_WARN = {
    "mfa_clock_skew",   # #1814
    "fw_changed",   # #2080
    "login_attempts_trimmed",   # #2459
    "login_account_failures_trimmed", "journal_flood",   # #2478, #2498
    "unknown_mac", "drive_rates_failed", "boot_loop_local", "boot_loop_unverified", "agent_loop",
    "agent_loop_unverified", "report_from_nonmember", "identity_unverifiable", "room_rejoin",
    "image_fetch_refused",   # #2657
    "capture_cancel", "direct_cancel", "task_boot_cancel", "send_stopped", "dhcp_proxy_risk",
    "net_config_unverified", "net_rollback_armed", "net_rollback",
    "net_rollback_unreadable", "net_rollback_marker_stuck", "work_area_kept", "nic_services_armed", "nic_services_reverted",
    "storage_node_disable",
    "room_drawer_changed", "room_drawer_unidentified", "room_drawer_wake_mismatch", "room_rewake", "clock_jump",
    "room_drawer_serial_conflict", "shrink_refused",   # #2421, #2413
    "power_expired", "gui_failed", "wol_arm_failed", "net_loss_jump", "model_speed_dropped", "wol_no_wake", "wol_resent", "boot_step_slow", "server_disk_slow", "capture_space_refused", "capture_space_unchecked", "cloner_throttled", "vmupdate_force_off", "vmupdate_expired", "vmupdate_control_auto",
    "vmupdate_mac_created", "vmupdate_leftover_failed", "linux_initramfs_warning",
    "send_counts_lost", "send_quiet_wait",   # #1430
    "readback_list_not_fetched",   # #1907
    "local_clone_note",   # #2049: a local clone done with a note (ntfs_note) -- orange, not "succeeded"
    "ups_on_battery", "ups_work_stopped",
    "update_available", "email_alert_failed", "email_alert_refused", "low_space",   # #1751
    "email_alert_requeued", "email_alerts_password_cleared",
}
_SEVERITY_OK = {
    "login", "capture_done", "client_done", "pull_done", "room_done",
    "send_done", "direct_done", "storage_transfer_done", "net_confirmed", "nic_services_confirmed",
    "disk_failure_cleared", "drivers_staged", "storage_image_received",
    "wol_sent", "vmupdate_saved", "vmupdate_lease",
    "ups_power_restored", "ups_reachable",
    "dnsmasq_dropin_applied",   # #2174
    "grub_password_saved",   # #2454
    "email_alert_sent", "peer_back", "email_oauth_connected",   # #1751
}


def severity(event: str) -> str:
    if event in _SEVERITY_ERR:
        return "err"
    if event in _SEVERITY_WARN:
        return "warn"
    if event in _SEVERITY_OK:
        return "ok"
    return "info"


def idle_minutes_he(seconds: str) -> str:
    """‏`console_idle_seconds` כמו שהקונסולה מציגה אותו: "כבוי" (0), "אחרי N דקות"."""
    try:
        n = int(seconds)
    except ValueError:
        return seconds
    if n <= 0:
        return "כבוי"
    minutes = max(1, round(n / 60))
    return "אחרי דקה אחת" if minutes == 1 else f"אחרי {minutes} דקות"


SETTINGS_HE = {
    "recovery_require_login": "שחזור בודד דורש כניסה",
    "session_wait_seconds": "המתנה מהמצטרף האחרון (שניות)",
    "console_idle_seconds": "ניתוק אוטומטי בחוסר פעילות",   # נדב 18/10: בדקות, 0 = כבוי
    "class_deploy_enabled": "הפצה לכיתות ממחשב הבנייה",
    "learned_rate_enabled": "קצב שידור אוטומטי",
    "learning_enabled": "למידה אוטומטית של הרשת והמחשבים",   # #2028
    "mfa_required_for_admins": "חיוב אימות דו-שלבי למנהלים",   # נדב 18/10
    "crc_warn_threshold": "סף שגיאות CRC לאזהרה",   # #1366
    "min_free_gb": "מינימום מקום פנוי אחרי שחזור (GB)",   # #1354
    "writecheck_min_mbps": "סף מהירות כתיבה לדיסק (MB/s)",   # #2338
    "unregistered_autoboot_seconds": "מחשב לא רשום: עלייה אוטומטית מהדיסק (שניות)",   # #1802
    "intersite_rate_cap_mbps": "הגבלת קצב בין אתרים (Mb/s)",   # #1473
    "igmp_querier": "IGMP querier ברשת ההפצה",   # #1972
    # ‏#2056/#2059 (מוקאפ 05): אותן תוויות כמו בהגדרות הקונסולה; הערך בשניות
    "lost_wait_seconds": "המתנה למחשב שהתנתק באמצע שיכפול (שניות)",
    "gui_idle_poweroff_seconds": "כיבוי כשאין פעילות (שניות)",
    "gui_end_poweroff_seconds": "ספירה לכיבוי בסוף משימה (שניות)",
    "auto_reboot": "הפעלה מחדש אוטומטית",
    "auto_reboot_time": "שעה",
    "auto_reboot_weekday": "יום",
    "cloner_beep": "צלילי המשכפל ומחשב הבנייה",   # #1967, as in console.js
    "backup_target": "יעד גיבוי המסד",   # #1956: הסוג, ואחריו היעד (בלי סיסמה)
    "backup_policy": "מדיניות גיבוי המסד",   # #1956: השעה והמקום השמור
    # ‏#1991: אותן תוויות כמו בהגדרות הקונסולה (console.js)
    "identity_check": "אימות מחשבים לפי כתובת ה-DHCP",
    "readback_full": "אימות מלא אחרי הכתיבה",
    "update_enabled": "עדכון גרסה מהקונסולה",
}

ROLES_HE = {"classroom": "כיתה", "cloner": "חדר שיכפולים", "build": "מחשב בנייה"}

FIELDS_HE = {"name": "שם", "description": "תיאור", "folder": "תיקייה", "sort": "סדר"}

#: למה הוחזרה תצורת הרשת (‏#56). שתי הסיבות היחידות שהזרוע כותבת.
ROLLBACK_HE = {"expired": "לא אושר בתוך חלון הזמן",
               "boot": "המכונה אותחלה לפני שהשינוי אושר"}

NETFIELDS_HE = {"mode": "מצב", "address": "כתובת", "netmask": "מסכה",
                "gateway": "שער", "dns": "DNS", "routes": "נתיבים סטטיים"}

_MAC = re.compile(r"\b([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b")
_IMG = re.compile(r"\b(img_[0-9a-f]+)\b")
_SES = re.compile(r"\b(ses_[0-9a-f]+)\b")
_GRP = re.compile(r"\b(grp_\w+)\b")
_EXPAND = re.compile(r"expand=(\S+)")
_SERIAL = re.compile(r"\bserial=(\S+)")
#: ‏#1857: שורות יומן על דיסק לפי הסידורי -- מקבלות בקריאה את הסט והמדבקה הנוכחיים ("קוריאים · xx-7"),
#: כדי שמי שקורא "דיסק נכשל" יידע איזה דיסק לשלוף. בקריאה ולא בכתיבה: דיסק ששויך אחרי הכשל מקבל שם גם בשורה הישנה.
_SET_EVENTS = ("disk_failure", "disk_failure_cleared")


#: ‏#2704/#2707: סוג האזהרות ביומן -- אותה טבלה ואותן מילים כמו כותרת ה-precheck בעמוד החדר
#: (‏`PRECHECK_NOUN_HE`/`precheckSigns` ב-console.js; ‏tests/test_precheck_kind_2707.py שומר שהן לא נסחפות).
#: ‏#2707: "הדיסק עצמו" ו"איטי לדגם" אינם קישור; כמה סוגים -- בסדר הזה, מחוברים ב"או".
PRECHECK_NOUN_HE = {"link": "קישור חשוד", "slow": "כתיבה איטית", "disk": "דיסק חשוד"}
#: מילות `smart_learned` (#1859) לפי סוג: 183/188 שעלו -- כנראה הכבל (smart_history.ROSE_LINK); קריטי/חם -- הדיסק.
PRECHECK_SMART_KIND = {"link": ("downshift_rose", "timeout_rose"), "disk": ("critical_rose", "hot")}
_PRECHECK_SIGN = {"learned=port": "link", "learned=slow_write": "slow", "learned=slow_model": "slow",
                  "learned=disk": "disk",
                  **{f"smart={w}": k for k, words in PRECHECK_SMART_KIND.items() for w in words}}
#: כל אסימון ש-`room.journal_precheck` כותב, בעברית -- ראשי המשפטים של המסך (room_precheck.py,
#: ‏crcwarn.text_he, ‏learn/ports.py, ‏learn/models.py), בלי המספרים שהיומן אינו נושא.
PRECHECK_REASON_HE = {"learned=port": "כנראה כבל/מחבר, לא הדיסק", "learned=disk": "הדיסק עצמו",
                      "learned=slow_model": "איטי מהרגיל לדגם",
                      "learned=slow_write": "בבדיקת הכתיבה הקודמת נמדד איטי"}
_PRECHECK = re.compile(r"^(\S+) wave=(\S+) (.+)$", re.S)
_PRECHECK_ITEM = re.compile(r", (?=[0-9a-f]{2}(?::[0-9a-f]{2}){5}:)")


def precheck_he(detail: str) -> tuple[str, list[str]] | None:
    """‏#2704: ‏(הסוג, הסיבות בעברית) של שורת `room_precheck_warned`; ‏None = הפירוט לא נקרא --
    התווית הכללית, לעולם לא סוג מנוחש. ‏#2707: כל אסימון לסוג שלו (`_PRECHECK_SIGN`; ‏`link=`/‏`crc+`/‏`crc=`
    -- קישור); בלי אסימון מוכר -- "עם אזהרה"."""
    m = _PRECHECK.match(detail)
    if not m:
        return None
    kinds: set[str] = set()
    reasons: list[str] = []
    for item in _PRECHECK_ITEM.split(m.group(3)):
        tokens = item.split()[1:]
        kinds.update("link" if t.startswith(("link=", "crc+", "crc=")) else _PRECHECK_SIGN.get(t, "")
                     for t in tokens)
        for t in tokens:
            if t.startswith("link=") and "/" in t:
                said = "קישור {} מתוך {} Gb/s".format(*t[5:].split("/", 1))
            elif t.startswith("crc+"):
                said = f"מונה CRC עלה ב-{t[4:]} מאז הסבב הקודם"
            elif t.startswith("crc=") and t[4:].isdigit():
                said = crcwarn.text_he(int(t[4:]))
            elif t.startswith("smart="):
                said = smart_history.LEARNED_HE.get(t[6:])   # #2707: #1859's word, as the room note says it
            else:
                said = PRECHECK_REASON_HE.get(t)
            if said and said not in reasons:
                reasons.append(said)
    nouns = [PRECHECK_NOUN_HE[k] for k in ("link", "slow", "disk") if k in kinds]
    return (f"עם {' או '.join(nouns)}" if nouns else "עם אזהרה"), reasons


def _expand_note(detail: str) -> str:
    """#59: מה שנכתב ל-`session_open`/`room_open` כשההרחבה לא הייתה
    "auto" — כיבוי או בחירה ידנית. "auto" עצמו אינו מגיע לכאן כלל
    (הוא לא נכתב לפרטים מלכתחילה), ולכן שקט הוא ברירת המחדל."""
    m = _EXPAND.search(detail)
    if not m:
        return ""
    value = m.group(1)
    if value == "none":
        return " · הרחבה כבויה"
    return f" · הרחבת מחיצה {value} (בחירה ידנית)"


class JournalTranslator:
    """בונה את מילוני השמות פעם אחת לכל בקשת יומן — לא שאילתה לשורה."""

    def __init__(self, conn: sqlite3.Connection, library: ImageLibrary):
        self._machines = {
            row["mac"]: f'{row["suffix"]} · {row["label"]}'
            for row in conn.execute(
                "SELECT m.mac, m.suffix, g.label FROM machines m"
                " JOIN groups g ON g.id = m.group_id"
            )
        }
        self._groups = {
            row["id"]: row["label"]
            for row in conn.execute("SELECT id, label FROM groups")
        }
        self._images = {
            image_id: manifest["name"]
            for image_id, manifest in library.scan().items()
        }
        self._sessions = {
            row["id"]: f'{row["prefix"]} · {self._groups.get(row["group_id"], row["group_id"])}'
            for row in conn.execute("SELECT id, prefix, group_id FROM sessions")
        }
        self._sets = disk_sets.all_labels(conn)   # ‏#1857

    def _names(self, text: str) -> str:
        text = _MAC.sub(lambda m: self._machines.get(m.group(1), m.group(1)), text)
        text = _IMG.sub(lambda m: f'"{self._images[m.group(1)]}"'
                        if m.group(1) in self._images else m.group(1), text)
        text = _GRP.sub(lambda m: self._groups.get(m.group(1), m.group(1)), text)
        return text

    def translate(self, event: str, detail: str) -> tuple[str, str]:
        """(תווית בעברית, פירוט בעברית). נופל חזרה לגולמי, לא נשבר."""
        label = EVENTS_HE.get(event, event)
        text = detail

        if event == "mac_import":
            m = re.search(r"group=(\S+) saved=(\d+) rejected=(\d+)", detail)
            if m:
                group = self._groups.get(m.group(1), m.group(1))
                text = f"{group}: נשמרו {m.group(2)}, נדחו {m.group(3)}"
        elif event == "session_open":
            m = re.search(r"(ses_\w+) (\S+) (\S+) prefix=(\S+)", detail)
            if m:
                group = self._groups.get(m.group(2), m.group(2))
                image = self._images.get(m.group(3), m.group(3))
                text = (f'{group} — "{image}", קידומת {m.group(4)}'
                        f'{_expand_note(detail)}')
        elif event == "room_open":
            text = detail + _expand_note(detail)
        elif event == "room_precheck_warned":
            text = self._names(detail)
            got = precheck_he(detail)
            if got is not None:   # #2704: the kind in the label; the raw tokens stay -- they are the evidence
                label = f"{label} {got[0]}"
                text += f" — {'; '.join(got[1])}" if got[1] else ""
        elif event == "pull_open":
            m = re.search(r"(ses_\w+) (\S+) (\S+) mac=(\S+)", detail)
            if m:
                image = self._images.get(m.group(3), m.group(3))
                text = f'{m.group(4)} — "{image}"'
        elif event in ("pull_done", "pull_failed"):
            text = _SES.sub(lambda m: self._sessions.get(m.group(1), m.group(1)), detail)
        elif event in ("client_done", "client_failed", "client_partial", "report_from_nonmember", "room_rejoin"):
            text = self._names(_SES.sub("הסבב", detail)).replace(" in ", " בתוך ")
        elif event in ("send_held_smart", "send_hold_released", "smart_ask_expired",
                       "send_held_prompt", "send_prompt_released", "prompt_hold_expired"):
            text = self._names(_SES.sub(lambda m: self._sessions.get(m.group(1), m.group(1)),
                                        detail))
        elif event in ("session_start_auto", "session_start_manual", "session_close",
                       "send_done", "send_stopped"):
            text = _SES.sub(lambda m: self._sessions.get(m.group(1), m.group(1)), detail)
        elif event == "send_start":
            m = re.search(r"(ses_\w+) (img_\w+) partitions=(\d+)", detail)
            if m:
                where = self._sessions.get(m.group(1), m.group(1))
                image = self._images.get(m.group(2), m.group(2))
                text = f'{where} — "{image}", {m.group(3)} מחיצות'
        elif event in ("boot_loop_local", "boot_loop_unverified"):
            m = re.search(r"(\S+) (session|task):(\S+)", detail)
            if m:
                where = (self._sessions.get(m.group(3), m.group(3))
                         if m.group(2) == "session" else f"משימה {m.group(3)}")
                text = f"{self._machines.get(m.group(1), m.group(1))} — {where}"
                attempts = re.search(r"attempts=(\d+)", detail)
                if attempts:
                    text += f", {attempts.group(1)} ניסיונות אתחול"
        elif event == "group_create":
            m = re.search(r"(\S+) \((\w+)\)", detail)
            if m:
                group = self._groups.get(m.group(1), m.group(1))
                text = f"{group} ({ROLES_HE.get(m.group(2), m.group(2))})"
        elif event in ("image_download", "image_upload", "iso_upload", "iso_delete"):
            m = re.search(r'(?:img|iso)_\w+ "(.+)"', detail)
            if m:
                text = f'"{m.group(1)}"'
        elif event == "image_edit":
            m = re.search(r"(img_\w+) (.+)", detail, flags=re.DOTALL)
            if m:
                name = self._images.get(m.group(1), m.group(1))
                changed = [FIELDS_HE.get(c.split("=", 1)[0], c.split("=", 1)[0])
                           for c in m.group(2).split(", ")]
                text = f'"{name}" — עודכנו: {", ".join(changed)}'
        elif event == "work_area_swept":
            m = re.search(r"swept=(\d+) freed=(\S+)", detail)
            if m:
                text = f"{m.group(1)} אזורי עבודה · {m.group(2)} התפנו"
        elif event == "wol_sent":
            m = re.search(r"(\S+) count=(\d+)", detail)
            if m:
                group = self._groups.get(m.group(1), m.group(1))
                text = f"{group} — {m.group(2)} מחשבים"
        elif event == "dhcp_proxy_risk":
            m = re.search(r"(\S+) dnsmasq=(\S+)", detail)
            if m:
                version = ("הגרסה לא נקראה" if m.group(2) == "unknown"
                           else f"dnsmasq {m.group(2)}")
                text = f"{m.group(1)} — {version}"
        elif event == "net_rollback":
            # הזמן שמדווח הוא **זמן ההחזרה** שבפירור, ולא זמן הקריאה:
            # השורה נכתבת בהפעלה שאחרי, ולפעמים ימים אחרי האירוע (‏#56).
            m = re.search(r"(\S+) at=(\S+) reason=(\S+)", detail)
            if m:
                text = (f"{m.group(1)} — {ROLLBACK_HE.get(m.group(3), m.group(3))}, "
                        f"בוצע ב-{m.group(2).replace('T', ' ')[:16]}")
                if "errors=" in detail:
                    text += f" · ההחזרה עצמה דיווחה שגיאות: {detail.split(': ', 1)[-1]}"
        elif event == "net_config":
            m = re.search(r"(\S+) (\S+)(?: (\S+))? changed=(\S*)$", detail)
            if m:
                mode = {"static": "כתובת סטטית", "dhcp": "לקוח DHCP",
                        "manual": "לא מנוהל מהקונסולה"}.get(m.group(2), m.group(2))
                changed = [NETFIELDS_HE.get(c, c) for c in m.group(4).split(",") if c]
                text = f"{m.group(1)} — {mode}"
                if m.group(3):
                    text += f" {m.group(3)}"
                if changed:
                    text += f" · עודכנו: {', '.join(changed)}"
        elif event == "net_rollback_armed":
            m = re.search(r"(\S+) window=(\S+)", detail)
            if m:
                text = (f"{m.group(1)} — יש לאשר תוך {m.group(2)} שהחיבור "
                        "לקונסולה עדיין חי")
        elif event == "setting_change":
            m = re.search(r"(\S+)=(\S+)", detail)
            if m:
                name = SETTINGS_HE.get(m.group(1), m.group(1))
                value = {"true": "פעיל", "false": "כבוי"}.get(m.group(2), m.group(2))
                if m.group(1) == "console_idle_seconds":   # נדב 18/10: דקות, לא שניות
                    value = idle_minutes_he(m.group(2))
                text = f"{name}: {value}"
                rest = detail[m.end():].strip(" —")
                if rest:                               # שורה עם הסבר (השדרוג של MFA, 18/10)
                    text += f" — {rest}"
        elif event in ("power_delivered", "power_expired"):
            # ‏"<mac> <poweroff|reboot>" — המכונה בשמה, הפעולה בעברית.
            m = re.match(r"(\S+) (\S+)$", detail)
            if m:
                action = {"poweroff": "כיבוי", "reboot": "אתחול"}.get(m.group(2), m.group(2))
                text = f"{self._machines.get(m.group(1), m.group(1))} · {action}"
        elif event in ("monitor_closed", "monitor_power", "monitor_refused"):
            # ‏#1855: ‏monitor_refused — "<mac> <קוד> <סיבה>"; ‏refused — "refused — <סיבה>".
            # ‏#1129: "<mac> <סיבה/פעולה>[ node=<nid>]" — המילה השנייה מתורגמת.
            words = {"browser": "הדפדפן סגר", "machine": "המכונה סגרה",
                     "idle": "נסגר אחרי 10 דק' בלי תעבורה",
                     "refused": "נסגר — הודעה אסורה מהדפדפן", "error": "כשל",
                     "session_revoked": "נסגר — ההתחברות לקונסולה הסתיימה",   # ‏#1853
                     # ‏#2325: ‏4410 (אותו מנהל, חלון אחר) או 4411 (השליטה הועברה) — #1852
                     "moved": "נסגר — הצפייה עברה לחלון אחר או למנהל אחר",
                     "reboot": "הפעלה מחדש", "poweroff": "כיבוי"}
            m = re.match(r"(\S+) (\S+)(.*)$", detail)
            text = self._names(detail) if m is None else " · ".join(
                part for part in (self._machines.get(m.group(1), m.group(1)),
                                  words.get(m.group(2), m.group(2)),
                                  m.group(3).strip().removeprefix("— ")) if part)
        elif event == "machine_mac_edit":
            # ‏#977 §5: "<ישן> -> <חדש>" — שתי הכתובות, כי הישנה היא שההיסטוריה עליה.
            m = re.search(r"([0-9a-f:]{17}) -> ([0-9a-f:]{17})", detail)
            if m:
                text = f"{m.group(1)} הוחלפה ב-{m.group(2)}"
        elif event in ("machine_add", "machine_edit", "machine_delete"):
            m = re.search(r"([0-9a-f:]{17})(?: name=(\S+))?(?: group=(\S+))?", detail)
            if m:
                parts = [m.group(1)]
                if m.group(2):
                    parts.append(f"השם: {m.group(2)}")
                if m.group(3):
                    parts.append(self._groups.get(m.group(3), m.group(3)))
                text = " · ".join(parts)
        else:
            text = self._names(detail)

        text = he_detail(event, text)
        if event in _SET_EVENTS:
            m = _SERIAL.search(detail)
            tag = disk_sets.text_of(self._sets, m.group(1)) if m else None
            if tag:
                text += f" · {tag}"
        return label, text
