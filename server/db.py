"""שכבת האחסון — SQLite יחיד על דיסק השרת.

הספרייה של האימג'ים היא *לא* כאן: אימג' הוא תיקייה עם manifest.json,
והדיסק הוא מקור האמת (ראו images.py). ה-DB מחזיק את מה שאין לו ייצוג
טבעי כקבצים: טבלת ה-MAC, קבוצות, סבבים, משתמשים ויומן.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

#: התור של הכותבים בתוך התהליך. חיבור נפרד לכל תהליכון (ראו Database
#: למטה) פותר את *שיתוף מצב הטרנזאקציה*, אבל לא את **ההוגנות**: ‏sqlite
#: אינו מבטיח שכותב ממתין יקבל את הנעילה אי פעם. הממתין ישן ב-busy
#: handler, ובזמן השינה האחרים כותבים שוב ושוב; כשכל commit ארוך (דיסק
#: איטי, כמו של runner ב-CI) ההסתברות שהנעילה תהיה פנויה בדיוק ברגע
#: ההתעוררות שואפת לאפס, וההמתנה מתכלה עד ``busy_timeout`` מלא. התוצאה
#: היא ``database is locked`` שנראה כמו עומס והוא בסך הכל חוסר הוגנות
#: ‏(#272 — ‏`journal` מעולם לא סבל מזה, ‏`net_seen` כן, וזה כל ההבדל).
#:
#: לכן כתיבה חמה שרצה מכמה תהליכונים עוברת כאן: התור בפייתון הוגן,
#: ו-sqlite אינו רואה תחרות בין התהליכונים שלנו. הנעילה נלקחת סביב
#: **הטרנזאקציה כולה** ולא סביב execute בודד — אחרת אין לה משמעות.
#:
#: **סדר הנעילות, ואין ממנו חריג (#457):** קודם ``_write_lock`` ורק
#: אחריו נעילת הכתיבה של sqlite. מי שכבר מחזיק בנעילת הכתיבה — כלומר
#: יש לו טרנזאקציה פתוחה — ‏**אינו רשאי להמתין כאן**, אחרת שני הכיוונים
#: נפגשים בנעילה משולבת. ‏`_settle` הוא האכיפה, והוא נקרא לפני **כל**
#: נטילה כאן.
_write_lock = threading.Lock()


class SchemaError(RuntimeError):
    """סכימה קיימת שאינה תואמת ואי אפשר למגר בשקט (#732).

    ‏``CREATE TABLE IF NOT EXISTS`` הוא no-op על טבלה קיימת גם כשהיא
    חסרה עמודות — ואז הכשל מתגלה רק מאוחר, מול כיתה, כ-``no such
    column``. עדיף להיכשל בקול באתחול, ליד ההתקנה (עיקרון 5)."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id       TEXT PRIMARY KEY,
    label    TEXT NOT NULL,
    role     TEXT NOT NULL CHECK (role IN ('build', 'cloner', 'classroom')),
    sort     INTEGER NOT NULL DEFAULT 0    -- הסדר שנקבע בגרירה בקונסולה
);

CREATE TABLE IF NOT EXISTS machines (
    mac      TEXT PRIMARY KEY,          -- קנוני: lowercase עם נקודתיים
    suffix   TEXT NOT NULL,             -- "01".."99" או "INS"
    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    note     TEXT NOT NULL DEFAULT '',
    drawer_count INTEGER NOT NULL DEFAULT 3    -- מספר המגירות/פורטים שהוגדר במסוף
                 CHECK (drawer_count BETWEEN 1 AND 8),
    drawer_slots TEXT,                    -- NULL = drawer k is sata:k
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id               TEXT PRIMARY KEY,
    group_id         TEXT NOT NULL REFERENCES groups(id),
    image_id         TEXT NOT NULL,
    prefix           TEXT NOT NULL,
    expected_clients INTEGER NOT NULL,
    wait_seconds     INTEGER NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ('open', 'running', 'failed', 'closed')),
    opened_by        TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_join_at     REAL NOT NULL,     -- epoch; הטיימר מתאפס בכל מצטרף
    started_at       TEXT,
    closed_at        TEXT,
    failed_reason    TEXT,
    roster_json      TEXT,              -- בחירת מחשבים; NULL = כל הקבוצה
    -- הזרם: 'multicast' הוא udp-sender, ולכן אחד בכל המערכת. 'unicast'
    -- הוא משיכת HTTP של תחנה בודדת — לא נוגעת בשידור, וכמה כאלה יחד.
    kind             TEXT NOT NULL DEFAULT 'multicast'
                     CHECK (kind IN ('multicast', 'unicast')),
    -- בחירת המחיצה להרחבה, מהקונסולה בפתיחת הסבב (#59). NULL = לא
    -- נבחר במפורש, כלומר הבחירה האוטומטית — בדיוק ההתנהגות מלפני #59,
    -- וזה מה שכל פותח סבב שלא עודכן (pulls.py, station.py) עדיין כותב.
    -- 'none' מכבה את ההרחבה; מחרוזת ספרות היא אינדקס מחיצה שנבחר ביד.
    expand_partition TEXT
);

CREATE TABLE IF NOT EXISTS session_members (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    mac        TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'waiting',
    done       INTEGER NOT NULL DEFAULT 0,
    bytes_written INTEGER NOT NULL DEFAULT 0,
    bytes_total   INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, mac)
);

CREATE TABLE IF NOT EXISTS room_rounds (
    id              TEXT PRIMARY KEY,
    image_id        TEXT NOT NULL,
    target_drives   INTEGER NOT NULL,      -- כמה כוננים צריך הפעם, סה"כ
    written_drives  INTEGER NOT NULL DEFAULT 0,
    written_serials TEXT NOT NULL DEFAULT '[]',  -- JSON; מגירה נספרת פעם אחת
    skipped_serials TEXT NOT NULL DEFAULT '[]',  -- JSON; ‏#1760: מגירות שדולגו כאדומות — סגורות, לא נכתבו
    cancelled_serials TEXT NOT NULL DEFAULT '[]',  -- JSON; ‏#1936: מגירות שמפעיל ביטל — סגורות בסבב, לא נכשלו
    target_slots_json TEXT,                -- בחירת דיסקי יעד לכל מכונה; NULL=סבב ישן
    expand_partition TEXT,                 -- בחירת ההרחבה לכל גלי הסבב (#59); NULL=אוטומטי
    driver_models_json TEXT,               -- דגם דרייברים לכל דיסק יעד (#1269); NULL=בלי דרייברים
    -- ‏#715: המקור. 'library' = udp-sender של השרת על אימג' מהספרייה;
    -- 'build_disk' = מחשב הבנייה משדר מהדיסק שלו, ו-image_id הוא מזהה
    -- חי (live_…) שהמניפסט שלו הוא live_manifest_json. סבב יחיד, בלי גלים.
    source_kind     TEXT NOT NULL DEFAULT 'library',
    source_mac      TEXT,                  -- מחשב הבנייה (build_disk בלבד)
    source_disk     TEXT,                  -- הדיסק שלו, כפי שדווח ב-hello
    source_task_id  TEXT,                  -- משימת direct_send שנפתחה לו
    live_manifest_json TEXT,               -- המניפסט שמחשב הבנייה דיווח; NULL = טרם
    state           TEXT NOT NULL CHECK (state IN ('active', 'closing', 'failed', 'closed')),
    wave_session_id TEXT,                  -- הגל הנוכחי הוא session רגיל
    wave_number     INTEGER NOT NULL DEFAULT 1,
    opened_by       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    closed_at       TEXT,
    failed_reason   TEXT
);

-- ‏#980: בקשת כוח מהקונסולה שממתינה ל-hello של המכונה (‏`room_console.py`).
-- שורה אחת למכונה; נמחקת כשנמסרה או כשפגה. אין שורה = אין בקשה.
CREATE TABLE IF NOT EXISTS power_requests (
    mac          TEXT PRIMARY KEY,
    action       TEXT NOT NULL CHECK (action IN ('poweroff', 'reboot')),   -- reboot: #977 §4
    requested_at TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    confirmed    INTEGER NOT NULL DEFAULT 0   -- #1367: אושר מעל אזהרת שיכפול מקומי
);

-- ‏#1386: "בטל דיסק זה" באמצע גל (`drawer_cancel.py`). שורה למגירה בגל; ‏`done_at`
-- = הסוכן דיווח `cancelled` (הראיה). ‏source: console | build | cloner (מסך המשכפל).
CREATE TABLE IF NOT EXISTS drawer_cancels (
    session_id   TEXT NOT NULL,
    mac          TEXT NOT NULL,
    dev          TEXT NOT NULL,
    requested_by TEXT,
    source       TEXT NOT NULL CHECK (source IN ('console', 'build', 'cloner')),
    requested_at TEXT NOT NULL,
    done_at      TEXT,
    PRIMARY KEY (session_id, mac, dev)
);

-- ‏#989 ס׳ 2: שאלת ה-SMART של משכפל (`smart_ask.py`) — שורה אחת למכונה, והתשובה
-- הראשונה (הקונסולה או המכונה עצמה) נכתבת ב-UPDATE מותנה. `closed` = המכונה כבר
-- שלחה hello בלי השאלה; nonce חוזר אחרי אתחול, ושורה סגורה אינה שאלה חדשה.
CREATE TABLE IF NOT EXISTS smart_asks (
    mac         TEXT PRIMARY KEY,
    nonce       TEXT NOT NULL,
    port        INTEGER NOT NULL,
    verdict     TEXT NOT NULL,
    reason      TEXT NOT NULL,
    asked_at    TEXT NOT NULL,
    answer      TEXT CHECK (answer IN ('replace', 'rescue', 'skip')),
    answered_by TEXT,
    answered_at TEXT,
    closed      INTEGER NOT NULL DEFAULT 0
);

-- ‏#972: תוצאת האימות החוזר (scrub) האחרונה לכל אימג'. אין לה ייצוג
-- טבעי כקובץ (עיקרון 3) — המניפסט אומר מה *צריך* להיות, לא מתי נבדק.
-- אין שורה = לא אומת מאז שנכנס לספרייה.
CREATE TABLE IF NOT EXISTS image_scrubs (
    image_id TEXT PRIMARY KEY,
    ts       TEXT NOT NULL,
    state    TEXT NOT NULL CHECK (state IN ('intact', 'drift'))
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    pw_hash  TEXT NOT NULL,
    role     TEXT NOT NULL CHECK (role IN ('admin', 'deploy')),
    created_at TEXT NOT NULL,
    disabled_at TEXT,
    -- #1093: ערכת הנושא של המשתמש. auto = לפי מערכת ההפעלה.
    theme    TEXT NOT NULL DEFAULT 'auto'
             CHECK (theme IN ('auto', 'light', 'dark')),
    must_change_password INTEGER NOT NULL DEFAULT 0,
    mfa_secret TEXT,
    mfa_enabled INTEGER NOT NULL DEFAULT 0,
    mfa_enrolled_at TEXT,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    auth_epoch INTEGER NOT NULL DEFAULT 0,
    mfa_last_step INTEGER,
    -- #1008: הכניסה האחרונה שהנפיקה עוגייה, ומאיזו כתובת. NULL = לא נרשמה.
    last_login_at TEXT,
    last_login_from TEXT,
    -- נדב 18/10: "דרוש אימות דו-שלבי" למשתמש — רישום MFA בכניסה גם כשהחיוב הכללי כבוי
    mfa_required INTEGER NOT NULL DEFAULT 0
);

-- ‏#1085: קודי גיבוי חד-פעמיים ל-TOTP. מוצגים פעם אחת בהפעלה, נשמרים כ-hash.
CREATE TABLE IF NOT EXISTS mfa_backup_codes (
    username  TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    used_at   TEXT,
    PRIMARY KEY (username, code_hash)
);

-- ‏#1085/#1075: מונה כשלונות כניסה לפי משתמש+IP. 5 → השהיה 2^n, 10 → נעילה 15 דק'.
CREATE TABLE IF NOT EXISTS login_attempts (
    key          TEXT PRIMARY KEY,
    failures     INTEGER NOT NULL DEFAULT 0,
    locked_until REAL,
    last_at      REAL NOT NULL
);

-- ‏#1085: סכמת עבר של "דפדפן זכור"; נשארת לתאימות, ואינה נקראת או נכתבת.
CREATE TABLE IF NOT EXISTS trusted_browsers (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    expires_at REAL NOT NULL,
    created_at TEXT NOT NULL,
    ua         TEXT NOT NULL DEFAULT ''
);

-- ‏#1085: אתגר MFA חד-פעמי אחרי סיסמה נכונה, בלי session.
CREATE TABLE IF NOT EXISTS mfa_challenges (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);

-- ‏#1039: מי מחובר לקונסולה (`console_presence`). שורה לעוגייה — לפי
-- ה-sha256 שלה, לא העוגייה עצמה. ‏last_seen נכתב לכל היותר פעם בדקה.
CREATE TABLE IF NOT EXISTS console_sessions (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    ip         TEXT NOT NULL DEFAULT '',
    since      TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    expires_at REAL NOT NULL,
    auth_epoch INTEGER NOT NULL DEFAULT 0
);

-- ‏#1633: עוגיות שיצאו ב-logout. העוגייה חסרת-מצב, ובלי השורה הזו עותק
-- שלה נשאר תקף עד התפוגה. לפי ה-sha256 שלה; נמחקת אחרי ``expires_at``.
CREATE TABLE IF NOT EXISTS revoked_tokens (
    token_hash TEXT PRIMARY KEY,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS journal (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    user   TEXT NOT NULL DEFAULT '',    -- ריק = המערכת עצמה
    event  TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    mac         TEXT NOT NULL,             -- המכונה שהמשימה מיועדת לה
    type        TEXT NOT NULL,
    disk        TEXT NOT NULL,
    image_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    folder      TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'pending',
    error       TEXT,
    bytes_written INTEGER NOT NULL DEFAULT 0,
    bytes_total   INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- כמה פעמים השרת כבר שלח מכונה לסוכן עבור אותה עבודה, ומתי. זה מה
-- שעוצר את לולאת האתחול של #75: התפריט הוא של השרת, ולכן הספירה כאן
-- היא ראיה חיובית ולא הסתמכות על דיווח ממכונה שאולי אין לה רשת בכלל.
-- ההקשר תחום בזמן ('session:<id>' / 'task:<id>') וכל הקשר חדש מאפס.
CREATE TABLE IF NOT EXISTS boot_attempts (
    mac      TEXT PRIMARY KEY,       -- קנוני: lowercase עם נקודתיים
    context  TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    first_at TEXT NOT NULL,
    last_at  TEXT NOT NULL
);

-- מי הגיע לסוכן אף שהשרת שלח אותו לדיסק המקומי (#112). זו התמונה
-- החיה בלבד, לא ארכיון: שורה מתארת את הלולאה *הנוכחית*, והמונה מתאפס
-- כשעברו LOOP_SILENCE_SECONDS בלי hello. טבלה נפרדת ולא עמודה
-- ב-net_devices, מפני ש-net_devices היא מלאי קבוע (first_seen שלא
-- מתאפס אף פעם) ואילו כאן כל שדה הוא בן-חלוף לפי הגדרה. הארכיון הוא
-- היומן, שאינו נמחק — ראו server/agent/hello.py.
CREATE TABLE IF NOT EXISTS agent_loops (
    mac      TEXT PRIMARY KEY,       -- קנוני: lowercase עם נקודתיים
    hits     INTEGER NOT NULL DEFAULT 0,
    first_at TEXT NOT NULL,
    last_at  TEXT NOT NULL
);

-- מי פנה לשרת מכתובת מקומית שאינה כתובת וילן ההפצה (#137). כמו
-- agent_loops זו התמונה החיה בלבד: השורה יורדת אחרי SILENCE_SECONDS
-- בלי פנייה, והמונה איתה. `address` היא הרגל של השרת שעליה הבקשה
-- התקבלה — כלומר מאיזו רשת המכונה מדברת. הארכיון הוא היומן.
CREATE TABLE IF NOT EXISTS off_vlan_contacts (
    mac      TEXT PRIMARY KEY,       -- קנוני: lowercase עם נקודתיים
    address  TEXT NOT NULL,
    hits     INTEGER NOT NULL DEFAULT 0,
    first_at TEXT NOT NULL,
    last_at  TEXT NOT NULL
);

-- שביל הפירורים של האתחול (#400): עד לאן הגיעה כל מכונה בין תפריט
-- ה-GRUB לבין ה-hello הראשון. מחשב שיכפול חסר-ראש שנעצר באמצע אינו
-- משאיר שום עקבה אחרת, וזה מה שחסם את הבדיקה על הברזל.
--
-- כמו agent_loops זו התמונה **החיה** בלבד: שורה אחת למכונה, שנדרסת
-- בכל אתחול. ‏`idx` הוא מקומו של הצעד ברשימה המנויה (boot/trace.py),
-- ושמור כאן כדי שהאיפוס — צעד שחזר אחורה, כלומר אתחול חדש — יוכרע
-- בתוך ה-UPSERT עצמו ולא בקריאה-ואז-כתיבה שיש בה מרוץ.
CREATE TABLE IF NOT EXISTS boot_steps (
    mac      TEXT PRIMARY KEY,       -- קנוני: lowercase עם נקודתיים
    step     TEXT NOT NULL,
    idx      INTEGER NOT NULL,
    at       TEXT NOT NULL,
    first_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS net_devices (
    mac         TEXT PRIMARY KEY,       -- קנוני: lowercase עם נקודתיים
    ip          TEXT,
    description TEXT NOT NULL DEFAULT '',
    first_seen  TEXT,
    last_seen   TEXT,
    disks_json  TEXT,                   -- הדיסקים מה-hello האחרון, כפי שדווחו
    optical_json TEXT                   -- NULL = the agent never reported optical drives
);

-- בריאות ה-SMART וההכרעה על כל דיסק יעד בסבב שחזור (#652). שורה אחת
-- לכל (סבב, מכונה, דיסק), נדרסת בכל disk_event — התמונה החיה של
-- הבחירות שהמפעיל עשה ליד המכונה. השרת **אינו** מכריע ממנה: ההכרעה
-- על דיסק פגום נעשית בסוכן (ליד המכונה, ASCII), וכאן נשמרת לצפייה
-- (‏#380) ולריבוט-החלפה (זיהוי מחדש לפי port+serial). לכן `verdict`
-- כולל `unchecked` — לא-נבדק, לא כשל (עיקרון 5).
CREATE TABLE IF NOT EXISTS disk_events (
    session_id    TEXT NOT NULL,
    mac           TEXT NOT NULL,        -- קנוני: lowercase עם נקודתיים
    disk          TEXT NOT NULL,        -- שם ההתקן שהסוכן דיווח (sda)
    port          INTEGER,              -- החריץ הפיזי; ממנו הקונסולה גוזרת "דיסק N"
    serial        TEXT,
    verdict       TEXT NOT NULL,        -- ok|warn|fail|unchecked
    reason        TEXT,
    realloc       INTEGER NOT NULL DEFAULT 0,
    pending       INTEGER NOT NULL DEFAULT 0,
    uncorrectable INTEGER NOT NULL DEFAULT 0,
    crc           INTEGER NOT NULL DEFAULT 0,
    write_state   TEXT,                 -- pending|rescue|skipped|replacing|...
    decision      TEXT,                 -- NULL|replace|rescue|skip
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, mac, disk)
);

-- ‏#704: השם שהמפעיל נתן לכל חיבור של כונן במכונה ("דיסק 1", "דיסק מיוחד").
-- ‏`slot` הוא החיבור ולא הדיסק: ‏`sata:<port>` (ה-ataN מ-hello), או
-- ‏`serial:<serial>` לכונן בלי port (‏NVMe/USB — אין מזהה חיבור ב-hello).
-- תווית בלבד: הבחירה ו-target_slots נשארים לפי port (`server/disks/slot_names.py`).
-- ‏CASCADE: עריכת MAC (#977 §5) מעבירה את השמות, הסרת המכונה מוחקת אותם.
CREATE TABLE IF NOT EXISTS machine_slot_names (
    mac    TEXT NOT NULL REFERENCES machines(mac) ON UPDATE CASCADE ON DELETE CASCADE,
    slot   TEXT NOT NULL,
    name   TEXT NOT NULL,
    set_at TEXT NOT NULL,
    set_by TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (mac, slot)
);

-- ‏#1857 (מוקאפ 04, נדב 24/10): סטים של דיסקים -- "קוריאים", קידומת xx, מדבקות xx-INS, xx-1 …
-- ‏השם הולך עם הדיסק לפי הסידורי ולא עם המגירה, ותצוגה בלבד (לא נכתב ל-Windows).
-- ‏`students` = כמה מספרים מתוכננים (‏xx-1…xx-N), ‏`instructor` = יש xx-INS.
CREATE TABLE IF NOT EXISTS disk_sets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    prefix     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    students   INTEGER NOT NULL,
    instructor INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
);
-- ‏דיסק אחד בסט אחד לכל היותר (serial = מפתח), ושם אחד לדיסק אחד בסט (UNIQUE set_id+suffix).
CREATE TABLE IF NOT EXISTS disk_set_members (
    serial      TEXT PRIMARY KEY,
    set_id      INTEGER NOT NULL REFERENCES disk_sets(id) ON DELETE CASCADE,
    suffix      TEXT NOT NULL,          -- "INS" או מספר
    assigned_at TEXT NOT NULL,
    assigned_by TEXT NOT NULL DEFAULT '',
    UNIQUE (set_id, suffix)
);
-- ‏דיסק שיצא מסט (הוחלף / הוסר): נשמר בהיסטוריה, השם עבר הלאה.
CREATE TABLE IF NOT EXISTS disk_set_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    serial    TEXT NOT NULL,
    set_id    INTEGER NOT NULL,
    suffix    TEXT NOT NULL,
    left_at   TEXT NOT NULL,
    reason    TEXT NOT NULL,            -- replaced|removed
    replaced_by TEXT,
    left_by   TEXT NOT NULL DEFAULT ''
);
-- ‏כל דיסק שהשרת ראה ב-hello של משכפל/מחשב בנייה: דגם, גודל ומקום אחרון (לרשימת "כל הדיסקים" ולשיוך).
CREATE TABLE IF NOT EXISTS disk_inventory (
    serial     TEXT PRIMARY KEY,
    model      TEXT,
    size_bytes INTEGER,
    mac        TEXT,
    port       INTEGER,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);

-- ‏#2080 (מוקאפ 06, נדב 24/10): תמונת הקושחה של כל מכונה -- שורה חדשה רק כשהתמונה השתנתה
-- (מצב אתחול, מצב בקר הדיסקים, גרסת BIOS, דיסק בכל חיבור SATA). ‏`fw_baseline` = "זו ההגדרה הנכונה".
CREATE TABLE IF NOT EXISTS fw_snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    mac       TEXT NOT NULL,
    at        TEXT NOT NULL,
    snap_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fw_snapshots_mac ON fw_snapshots (mac, id);
CREATE TABLE IF NOT EXISTS fw_baseline (
    mac       TEXT PRIMARY KEY,
    at        TEXT NOT NULL,
    snap_json TEXT NOT NULL,
    by        TEXT NOT NULL DEFAULT ''
);

-- ‏#2001: מיפוי חיבורים לפי דגם (‏DMI ``product_name`` מה-hello) — "כל מחשבי הדגם".
-- ‏`slots_json` = החיבורים הפעילים בסדר החיבורים (‏sata:<port> / nvme:<PCI> /
-- ‏nvme#<n> שעוד לא נראה כונן בו); ‏`names_json` = ‏{חיבור: שם}. עדיפות: מכונה
-- (‏machines.drawer_slots) ← דגם (כאן) ← ברירת מחדל (`server/disks/model_slots.py`).
CREATE TABLE IF NOT EXISTS model_slot_maps (
    model      TEXT PRIMARY KEY,
    slots_json TEXT NOT NULL,
    names_json TEXT NOT NULL DEFAULT '{}',
    set_at     TEXT NOT NULL,
    set_by     TEXT NOT NULL DEFAULT ''
);

-- זיכרון כשלי כתיבה (#874): במקום סימון על הדיסק (#845, נפסל) השרת זוכר
-- **איזה דיסק (סידורי) ואיזה חריץ (מכונה+פורט)** נכשלו, ומה הקרנל אמר.
-- הסוכן שולח את שורות ה-ATA; הסיווג (cable/disk/removed/unclassified) נעשה כאן
-- (`ata_cause.py`). ‏`cleared_at` הוא "נקה" מהקונסולה — הדיסק הוחלף או
-- הכבל תוקן; רשומה מנוקה אינה צובעת עוד. שורה אחת לכל (סבב, מכונה, יעד):
-- הדיווח חוזר כל 2 שניות, והכשל נרשם פעם אחת.
CREATE TABLE IF NOT EXISTS disk_failures (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    mac        TEXT NOT NULL,        -- קנוני: lowercase עם נקודתיים
    dev        TEXT NOT NULL,        -- שם ההתקן באותו אתחול (sda) -- מתחלף, לא זהות
    serial     TEXT,                 -- זהות הדיסק
    port       INTEGER,              -- החריץ (SATA N, כמו ב-hello) -- זהות המקום
    ata_port   INTEGER,              -- ata<N> של הקרנל, לקריאה מול dmesg
    at         TEXT NOT NULL,
    cause      TEXT NOT NULL,        -- cable|disk|removed|unclassified
    error      TEXT,
    ata_log    TEXT,                 -- JSON: שורות הקרנל, הראיה
    cleared_at TEXT,
    UNIQUE (session_id, mac, dev)
);

-- ‏#1859 (נדב 20/10): כל מאפייני ה-SMART של דיסק בכל סבב, לפי סידורי — ממנו השרת
-- "לומד" מה עלה מאז הסבב הקודם (server/disks/smart_history.py). שורה לכל (סידורי, סבב):
-- ה-disk_event של הסבב דורס אותה; `temp_after` = מעלות מיד אחרי הכתיבה (דוח הסיום).
CREATE TABLE IF NOT EXISTS smart_history (
    serial      TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    mac         TEXT NOT NULL,
    at          TEXT NOT NULL,
    attrs_json  TEXT NOT NULL,       -- {"ata": [[id, value, worst, thresh, raw, name], ...], "nvme": {...}|null}
    temp_after  INTEGER,             -- NULL = לא נמדד, לא 0
    -- ‏mac בתוך המפתח: רק המכונה שדיווחה את הסידורי ב-hello שלה כותבת לו (smart_history.record)
    PRIMARY KEY (serial, session_id, mac)
);

-- זיכרון הכיווץ של דיסק המקור (#926): הפריסה המקורית של מחיצת ה-NTFS
-- שכווצה בקליטה (#87), נרשמת **לפני** הכתיבה הראשונה למקור. עד כאן היא
-- ישבה ב-tmpfs של הסוכן, ואובדן חשמל בין הכיווץ להחזרה השאיר את דיסק
-- הבנייה מכווץ בלי שאיש יודע. הסידורי הוא הזהות (הדיסק נודד); mac/dev/
-- port הם המקום, לתצוגה. `closed_at` = הוחזר (הסוכן) או "נקה" מהקונסולה.
CREATE TABLE IF NOT EXISTS shrink_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    mac          TEXT NOT NULL,        -- קנוני: lowercase עם נקודתיים
    dev          TEXT,                 -- שם ההתקן באותו אתחול (sda) -- מתחלף, לא זהות
    serial       TEXT NOT NULL,        -- זהות הדיסק
    port         INTEGER,              -- החריץ (SATA N, כמו ב-hello)
    model        TEXT,
    image_name   TEXT,                 -- הקליטה שבמהלכה כווץ
    task_id      TEXT,
    idx          INTEGER NOT NULL,     -- הכניסה ב-GPT
    start_sector INTEGER NOT NULL,
    size_sectors INTEGER NOT NULL,     -- הגודל **המקורי**, לפני הכיווץ
    type_guid    TEXT NOT NULL,
    unique_guid  TEXT,
    attrs        TEXT,                 -- 16 ספרות הקס, כמו sgdisk -i
    name         TEXT,                 -- שם המחיצה
    ntfs_bytes   INTEGER,              -- גודל מערכת הקבצים לפני הכיווץ
    opened_at    TEXT NOT NULL,
    note         TEXT,                 -- למה הרשומה עדיין פתוחה (הטבלה הוחזרה, המתיחה נפלה)
    partitions   TEXT,                 -- #929: JSON, **כל** המחיצות שכווצו; העמודות למעלה = הראשונה
    closed_at    TEXT,
    closed_by    TEXT                  -- agent | console | <user>
);
CREATE INDEX IF NOT EXISTS shrink_records_serial ON shrink_records (serial, closed_at);

-- המלאי החומרתי מה-hello (#720, schema 2): DMI, PCI של רשת+אחסון, TPM.
-- **מגורסת**: שורה חדשה רק כשה-JSON הקנוני השתנה (server/agent/inventory.py);
-- seen_at = מתי הגרסה הזו נראתה לראשונה. השורה האחרונה לכל MAC היא
-- המלאי הנוכחי, ולפיו מותאמות חבילות הדרייברים (server/library/drivers.py).
CREATE TABLE IF NOT EXISTS machine_inventory (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    mac            TEXT NOT NULL,        -- קנוני: lowercase עם נקודתיים
    seen_at        TEXT NOT NULL,
    inventory_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS machine_inventory_mac ON machine_inventory (mac, id);

-- בדיקת המכונה מה-hello (#1049 שלב ב'): חשמל, שעון, מעבד, זיכרון, רשת,
-- NVMe, pstore, מפתח OEM… **מגורסת** כמו machine_inventory: שורה חדשה רק
-- כשהתוכן היציב השתנה (server/agent/probe.py); שדות נדיפים (rtc, temp_c, מוני
-- רשת, probe_seconds) מרעננים את השורה האחרונה במקום — sampled_at זז,
-- seen_at (הגרסה נראתה לראשונה) נשאר. השורה האחרונה לכל MAC היא הדגימה
-- הנוכחית, ולפיה מחושבים השערים (verdicts).
CREATE TABLE IF NOT EXISTS machine_probe (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    mac        TEXT NOT NULL,        -- קנוני: lowercase עם נקודתיים
    seen_at    TEXT NOT NULL,
    sampled_at TEXT NOT NULL,
    probe_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS machine_probe_mac ON machine_probe (mac, id);

-- Storage Nodes (#655, tracer 1.1 / #723): רישום המשניים שהראשי מחזיק,
-- וקבוצות האחסון שמשייכות אותם. קיים על **כל** בסיס לצורך עקביות
-- הסכימה — גם על משני, ששם הוא נשאר ריק (מוטציה מסורבת בשכבת ה-service,
-- שלב הבא בשרשרת). מזהים הם TEXT (UUID/מזהה יציב), כמו שאר הטבלאות כאן.
CREATE TABLE IF NOT EXISTS storage_node_groups (
    id       TEXT PRIMARY KEY,
    label    TEXT NOT NULL,
    sort     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS storage_nodes (
    id              TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    base_url        TEXT NOT NULL UNIQUE,
    group_id        TEXT REFERENCES storage_node_groups(id) ON DELETE SET NULL,
    -- ‏#740: ``tls_fingerprint`` יורד מלהיות חומר אמון — הוא תצוגה/ביקורת
    -- בלבד. חומר האמון הוא ``pinned_spki`` (טביעת ה-SubjectPublicKeyInfo
    -- של המשני, שאליה הראשי נצמד) ו-``client_cert_ref`` (הפניית sha256
    -- לתעודת-הלקוח של הראשי, שאותה המשני קושר לטוקן — RFC 8705).
    tls_fingerprint TEXT,
    -- ‏#740: מזהה המשני היציב, נגזר מ-SPKI התעודה שלו (לא UUID מקומי).
    node_id         TEXT,
    protocol_version TEXT,
    pinned_spki     TEXT,
    client_cert_ref TEXT,
    -- מזהה קובץ-אישורים קריא-ל-root מתחת ל-data-dir, לא האישור עצמו.
    credential_ref  TEXT NOT NULL,
    enrolled_at     TEXT NOT NULL,
    -- חותמת ולא דגל: משני מושבת אינו נמחק, ו-NULL הוא "פעיל" (כמו
    -- users.disabled_at). ``enabled_node_count`` סופר NULL בלבד.
    disabled_at     TEXT
);

-- Storage Nodes — enrollment מוקשח (#740, tracer 2.1). שתי טבלאות
-- singleton על **המשני**: רשומת אישור-האב היחיד, וחלון ה-pairing
-- המקומי. ה-PK הקבוע (=1) הוא אכיפת אב-יחיד ברמת ה-DB — "בדוק ואז
-- הכנס" לבדו אינו מספיק תחת מקביליות (עיקרון 5). ריקות על ראשי/עצמאי.
--
-- ‏storage_identity (node_id/SPKI/cert/key) ורשומת המשני-הרשום על
-- הראשי (node_id/pinned_spki/client_cert_ref) ממתינות לשכבת ה-TLS
-- של #740 — הן נגזרות מהתעודה שאותה שכבה מפיקה, ואין להן ערך בלעדיה.
CREATE TABLE IF NOT EXISTS parent_credentials (
    singleton          INTEGER PRIMARY KEY CHECK (singleton = 1),
    parent_id          TEXT NOT NULL,
    token_hash         BLOB NOT NULL,
    bound_cert_ref     TEXT NOT NULL,
    pinned_parent_spki TEXT NOT NULL,
    protocol_version   TEXT NOT NULL,
    enrolled_at        TEXT NOT NULL,
    last_contact_at    TEXT
);

CREATE TABLE IF NOT EXISTS pairing_windows (
    singleton          INTEGER PRIMARY KEY CHECK (singleton = 1),
    code_hash          BLOB NOT NULL,
    code_salt          BLOB NOT NULL,
    opened_at          TEXT NOT NULL,
    expires_at         TEXT NOT NULL,
    attempts_remaining INTEGER NOT NULL
);

-- ‏#740 (tracer 2.1): זהות ה-TLS של השרת עצמו — singleton על **כל** בסיס.
-- ‏node_id נגזר מ-SPKI התעודה; ``server_cert_ref``/``server_key_ref`` הן
-- הפניות קובץ (לא החומר עצמו — המפתח הפרטי לעולם אינו ב-DB, עיקרון 6/7
-- של הדגם הנעול). ריקה עד שנוצרת זהות (‏interserver_auth.ensure_identity).
CREATE TABLE IF NOT EXISTS storage_identity (
    singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
    node_id         TEXT NOT NULL UNIQUE,
    server_spki     TEXT NOT NULL,
    server_cert_ref TEXT NOT NULL,
    server_key_ref  TEXT NOT NULL
);

-- ‏#655 v1: העברת אימג' ראשי→משני. **תיאום זמני בלבד** — לא מלאי: "האם
-- למשני יש אימג' X" נענה מהספרייה שעל הדיסק של המשני (עיקרון 3), וכאן
-- רק ההתקדמות והתוצאה של ניסיון אחד, כדי שהקונסולה תציג אותם ושכשל
-- יהיה גלוי (עיקרון 4/5: ניתוק = ``failed`` עם סיבה, לא "נתקע").
CREATE TABLE IF NOT EXISTS storage_transfers (
    id          TEXT PRIMARY KEY,
    node_id     TEXT NOT NULL REFERENCES storage_nodes(id) ON DELETE CASCADE,
    image_id    TEXT NOT NULL,
    image_name  TEXT NOT NULL,
    state       TEXT NOT NULL CHECK (
                    state IN ('queued', 'sending', 'verifying', 'done', 'failed')
                ),
    bytes_sent  INTEGER NOT NULL DEFAULT 0,
    bytes_total INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    started_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    direction   TEXT NOT NULL DEFAULT 'push'
                CHECK (direction IN ('push', 'pull')),
    location_id TEXT NOT NULL DEFAULT 'loc_local'
);

-- מיקומי אחסון לספרייה (#1066 שלב א'): תיקייה / NFS / SMB / iSCSI.
-- `--images` הוא loc_local. last_images_json הוא המטמון כשהדיסק לא נגיש
-- (אין ייצוג טבעי כקבצים אז — עיקרון 3).
CREATE TABLE IF NOT EXISTS storage_locations (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    type             TEXT NOT NULL CHECK (type IN ('local', 'nfs', 'smb', 'iscsi')),
    params_json      TEXT NOT NULL DEFAULT '{}',
    mount_point      TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (
                         state IN ('connected', 'unreachable', 'disconnected', 'unchecked')
                     ),
    state_since      TEXT NOT NULL,
    state_detail     TEXT NOT NULL DEFAULT '',
    created_by       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    last_images_json TEXT NOT NULL DEFAULT '[]',
    last_df_json     TEXT
);

-- ‏#447: הקצב שכל כונן באמת כתב בגל שהסתיים (`server/rounds/drive_rates.py`).
-- זהות = דגם + סידורי (הכונן נודד בין מגירות). ‏`cap_bps` הוא תקרת הגל
-- שבו נמדד — בלעדיה אי-אפשר לדעת אם הכונן הגביל את הזרם או רק עמד בו.
-- שורה אחת לכל (גל, סידורי); בייטים לשנייה, כמו `rate_bps` במסך החדר.
CREATE TABLE IF NOT EXISTS drive_rates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    serial     TEXT NOT NULL,
    model      TEXT,
    session_id TEXT NOT NULL,
    rate_bps   INTEGER NOT NULL,
    cap_bps    INTEGER NOT NULL,
    bytes      INTEGER NOT NULL,
    seconds    INTEGER NOT NULL,
    at         TEXT NOT NULL,
    UNIQUE (session_id, serial)
);

-- ‏#447: התקרה שנבחרה לגל לפני שיצא, ולמה (JSON של `drive_rates.choose`).
-- טבלה נפרדת ולא עמודה ב-`sessions`: ‏`_rebuild_state_tables` מעתיק את
-- `sessions` לפי רשימת עמודות מפורשת, ועמודה חדשה הייתה נמחקת שם.
-- ‏`stream` — מה ש-udp-sender ספר בסוף הגל (`drive_rates.record_stream`):
-- ‏JSON של `sent_bytes` ו-`loss_blocks`; ‏NULL = השולח לא דיווח.
-- ‏`drops` — ‏`udp_drops` של כל מקלט בגל (`drive_rates.record_wave`, 17/10):
-- ‏JSON ‏{mac: {"rcvbuf", "inerrors"} | null}; ‏NULL = הגל לא נסגר דרך החדר.
-- ‏`delivered` — מה שכל מחשב שיכפול כתב בגל (‏#1758, `drive_rates.record_wave`):
-- ‏JSON ‏{mac: {"drawers", "done", "written_bytes"} | null} — המונה של goodput.
CREATE TABLE IF NOT EXISTS wave_rates (
    session_id TEXT PRIMARY KEY,
    decision   TEXT NOT NULL,
    at         TEXT NOT NULL,
    stream     TEXT,
    drops      TEXT,
    delivered  TEXT
);

-- ‏#1761: קצב שהיה לא בטוח למכונה (`drive_rates._unsafe` — udp-sender ניתק
-- מקלט, או מגירה אבדה עם שידורים חוזרים) — נזכר גם אחרי שהגל יוצא מחלון
-- ה-goodput, ומעליו לא מחפשים. שורה לכל (מכונה, מגירות, קצב): חסומה במספר
-- הקצבים, ואינה נמחקת (`housekeeping` אינו נוגע בה) — לשכוח היא הבאג.
-- ‏#1844: מה שנמדד בכל חיבור SATA של מחשב שיכפול בכל גל (`server/learn/learn_ports.py`) —
-- ממנו השרת מבחין "כנראה כבל" (אותו חיבור, דיסקים שונים) מ"הדיסק" (אותו דיסק, חיבורים
-- שונים). ‏NULL = לא נמדד/לא נקרא, לא 0. חומרה ומדידות בלבד (#1924).
CREATE TABLE IF NOT EXISTS port_history (
    session_id    TEXT NOT NULL,
    mac           TEXT NOT NULL,
    port          INTEGER NOT NULL,
    serial        TEXT NOT NULL,
    crc_delta     INTEGER,
    link_gbps     REAL,
    link_max_gbps REAL,
    cable_fail    INTEGER NOT NULL DEFAULT 0,
    at            TEXT NOT NULL,
    PRIMARY KEY (session_id, mac, port)
);
CREATE INDEX IF NOT EXISTS port_history_port ON port_history (mac, port, at);
CREATE INDEX IF NOT EXISTS port_history_serial ON port_history (serial, at);

-- ‏#1845: ה-MB/s של בדיקת הכתיבה שלפני הזרם (`write_preflight`) לכל דיסק בכל גל, עם הדגם
-- מה-hello (`server/learn/learn_models.py`) — הבסיס של "איטי מהרגיל לדגם". חומרה ומספרים בלבד (#1924).
CREATE TABLE IF NOT EXISTS model_speeds (
    session_id TEXT NOT NULL,
    serial     TEXT NOT NULL,
    model      TEXT NOT NULL,
    mac        TEXT NOT NULL,
    mbps       INTEGER NOT NULL,
    at         TEXT NOT NULL,
    PRIMARY KEY (session_id, serial)
);
CREATE INDEX IF NOT EXISTS model_speeds_model ON model_speeds (model, at);
CREATE INDEX IF NOT EXISTS model_speeds_serial ON model_speeds (serial, at);

-- ‏#1847: כל הערת WoL למכונה שלא נראתה (`server/learn/learn_wol.py`), ומתי היא ענתה — הפירור
-- הראשון של האתחול או hello. ‏`answered_at` NULL = עוד לא ענתה (או לא התעוררה, אחרי
-- ‏`ANSWER_S`). ‏`early` — נשלחה שליחה חוזרת מוקדמת; ‏`alerted` — ההתראה על הרצף נרשמה.
CREATE TABLE IF NOT EXISTS wol_wakes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mac         TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    sent_epoch  REAL NOT NULL,
    resent      INTEGER NOT NULL DEFAULT 0,
    answered_at TEXT,
    seconds     REAL,
    early       INTEGER NOT NULL DEFAULT 0,
    alerted     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS wol_wakes_mac ON wol_wakes (mac, sent_epoch);

-- ‏#1848: כמה זמן ארך כל מעבר בשרשרת האתחול, לכל מכונה (`server/learn/learn_boot.py`) — הבסיס
-- לזיהוי שלב שהתארך, ולהשוואה לפני/אחרי עדכון (`version` = גרסת השרת בזמן המדידה; NULL = לא נקראה).
CREATE TABLE IF NOT EXISTS boot_step_times (
    mac     TEXT NOT NULL,
    step    TEXT NOT NULL,
    seconds REAL NOT NULL,
    version TEXT,
    at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS boot_step_times_mac ON boot_step_times (mac, step);
CREATE INDEX IF NOT EXISTS boot_step_times_version ON boot_step_times (version, step);

-- ‏#1882: הקטע האחרון של לוג הסוכן, פעם אחת בסוף כל סבב (`server/agent/agent_logs.py`). הטקסט
-- **אחרי** סינון סודות ומידע מהדיסק (#1924), דחוס. תקרה בכל הכנסה (מכונה/סך הכול) + 90 יום.
CREATE TABLE IF NOT EXISTS agent_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mac             TEXT NOT NULL,
    session_id      TEXT,
    reason          TEXT NOT NULL,
    at              TEXT NOT NULL,
    lines           INTEGER NOT NULL,
    bytes           INTEGER NOT NULL,
    truncated       INTEGER NOT NULL DEFAULT 0,
    signatures_json TEXT NOT NULL DEFAULT '[]',
    text_gz         BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_logs_mac ON agent_logs (mac, id);

-- ‏#1883: כמה זמן ארך כל שלב בשחזור, לכל (גל, מכונה, מגירה) — `server/agent/restore_stages.py`. משכים לפי
-- שעון המכונה; ‏`serial` NULL = לא דווח. 90 יום (`housekeeping`).
CREATE TABLE IF NOT EXISTS restore_stages (
    session_id TEXT NOT NULL,
    mac        TEXT NOT NULL,
    dev        TEXT NOT NULL,
    serial     TEXT,
    stage      TEXT NOT NULL,
    seconds    REAL NOT NULL,
    at         TEXT NOT NULL,
    PRIMARY KEY (session_id, mac, dev, stage)
);
CREATE INDEX IF NOT EXISTS restore_stages_mac ON restore_stages (mac, session_id);

-- ‏#1884: דיסק הספרייה ולחץ ה-I/O של השרת לכל גל/קליטה (`server/learn/server_io.py`) — צילום בהתחלה,
-- תוצאה בסוף. ‏`basis` ‏server_disk/ok/NULL (לא נבדק). 90 יום (`housekeeping`).
CREATE TABLE IF NOT EXISTS server_io (
    kind        TEXT NOT NULL,
    ref         TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    start_json  TEXT NOT NULL,
    ended_at    TEXT,
    result_json TEXT,
    basis       TEXT,
    PRIMARY KEY (kind, ref)
);

-- ‏#1886: מקום פנוי לכל מיקום אחסון לאורך זמן (`server/storage/storage_history.py`) — יומי, ובכל קליטה/סבב.
CREATE TABLE IF NOT EXISTS storage_usage (
    location_id TEXT NOT NULL,
    at          TEXT NOT NULL,
    total_bytes INTEGER NOT NULL,
    free_bytes  INTEGER NOT NULL,
    source      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS storage_usage_loc ON storage_usage (location_id, at);

-- ‏#1887: מה ש-dnsmasq רשם ל-journal לכל MAC (`server/net/pxe_log.py`) — קריאה בלבד. 14 יום + תקרת שורות.
CREATE TABLE IF NOT EXISTS pxe_events (
    mac    TEXT NOT NULL,
    kind   TEXT NOT NULL,
    at     TEXT NOT NULL,
    ip     TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS pxe_events_mac ON pxe_events (mac, at);
CREATE INDEX IF NOT EXISTS pxe_events_ip ON pxe_events (ip, at);

-- ‏#1888: היסטוריית IP לכל MAC (שורה כשהשתנה; `db.net_seen`) ודגימות מוני כרטיס הרשת מבדיקת המכונה
-- (`server/learn/net_history.py`). ערך שלא נמדד — NULL. תקרה בשורות בכל הכנסה + `housekeeping`.
CREATE TABLE IF NOT EXISTS ip_history (
    mac      TEXT NOT NULL,
    ip       TEXT NOT NULL,
    previous TEXT,
    at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ip_history_mac ON ip_history (mac, at);
CREATE TABLE IF NOT EXISTS nic_history (
    mac           TEXT NOT NULL,
    iface         TEXT NOT NULL,
    at            TEXT NOT NULL,
    speed_mbps    INTEGER,
    rx_crc_errors INTEGER,
    rx_dropped    INTEGER
);
CREATE INDEX IF NOT EXISTS nic_history_mac ON nic_history (mac, iface, at);

CREATE TABLE IF NOT EXISTS unsafe_rates (
    mac        TEXT NOT NULL,
    drawers    INTEGER NOT NULL,
    rate_bps   INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    at         TEXT NOT NULL,
    PRIMARY KEY (mac, drawers, rate_bps)
);
"""

#: הקבוצות הקבועות: חדר שיכפולים ומחשב הבנייה הם יחידים במערכת —
#: אין ניהול קבוצות עבורם, רק רשימת מכונות.
FIXED_GROUPS = [
    ("grp_CLONERS", "מחשבי שיכפול", "cloner"),
    ("grp_BUILD", "מחשב בניית אימג'ים", "build"),
]

#: ברירות מחדל. recovery דורש כניסה כל עוד אין endpoint כניסה בסוכן —
#: הצד הבטוח. לפתיחה זמנית (הדגמה) יש מתג בקונסולה.
DEFAULT_SETTINGS = {
    "recovery_require_login": "true",
    "session_wait_seconds": "300",
    # ניתוק אוטומטי של הקונסולה בחוסר פעילות. המסך עומד בכיתה או במשרד
    # פתוח — מי שקם והלך לא משאיר אחריו קונסולת ניהול פתוחה.
    "console_idle_seconds": "600",
    # ‏#880: v1 "מהדורת שיכפול" — הפצה לכיתות ממחשב הבנייה כבויה כברירת
    # מחדל. הקוד נשאר (v2); המתג בקונסולה, מסך ההגדרות.
    "class_deploy_enabled": "false",
    # #1967: audible result on cloning machines; an explicit false disables it.
    "cloner_beep": "true",
    # ‏#447 + הכרעת נדב 11/10: קצב אוטומטי לכל גל — עולה על גל נקי, יורד על
    # שידורים חוזרים וכוננים איטיים, לעולם לא מעל `IMAGESRV_MAX_BITRATE`
    # (בלעדיו 900m). ‏"false" = קצב הפתיחה (600m), קבוע.
    "learned_rate_enabled": "true",
    # נדב 18/10: "תכלס לא צריך לזה MFA" — אין חיוב MFA למנהלים, גם בשדרוג של שרת שבו
    # היה (‏console_mfa.SETTING; ‏_initialize רושם ביומן כשהשדרוג מכבה חיוב שהיה).
    "mfa_required_for_admins": "false",
}

#: השורה ביומן כששדרוג מכבה את החיוב שהיה לכל מנהל עד rc6 (נדב 18/10).
MFA_UPGRADE_NOTE = ("mfa_required_for_admins=false — בשדרוג: חיוב אימות דו-שלבי למנהלים"
                    " כבוי (הכרעת נדב 18/10). מי שהפעיל MFA ממשיך להתבקש לקוד;"
                    " להחזרת החיוב: הגדרות ← אבטחה")


def now_iso() -> str:
    """זמן בפורמט המוסכם: ISO 8601 עם אזור זמן."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: עמודות שנוספו אחרי שכבר היו התקנות בשטח. CREATE TABLE IF NOT EXISTS
#: לא מוסיף עמודה לטבלה קיימת, ולכן ההשלמה נעשית כאן במפורש.
ADDED_COLUMNS = [
    ("groups", "sort", "INTEGER NOT NULL DEFAULT 0"),
    ("net_devices", "disks_json", "TEXT"),
    # ‏#839: סוד המוניטור שהמכונה הגרילה באתחול ודיווחה ב-hello. הפרוקסי
    # (server/console/monitor.py) מזדהה איתו מול 5900 של אותה מכונה, ושום תשובת
    # קונסולה אינה מחזירה אותו.
    ("net_devices", "monitor_secret", "TEXT"),
    # ‏#500: הקושחה ומצב ה-Secure Boot שהסוכן מודד ושולח בכל hello — נמדדו
    # על ברזל ונזרקו (07/09). NULL = הסוכן לא דיווח (ישן / hello בלי השדה).
    ("net_devices", "firmware", "TEXT"),
    ("net_devices", "secure_boot", "INTEGER"),
    ("net_devices", "agent_version", "TEXT"),
    # ‏#1077: ``hmac`` כשהסוכן תומך באתגר-תגובה. NULL = סוכן ישן; הפרוקסי
    # מסרב ולא נופל לשליחת הסוד הגולמי.
    ("net_devices", "monitor_auth", "TEXT"),
    # ‏#1837: הסוד שהשרת עזב ב-`monitor_rekeyed` — hello שנבנה לפני האימות
    # ונושא אותו אינו דורס את הסוד הבא (`net_seen`). NULL = אין מה לזכור.
    ("net_devices", "monitor_secret_prev", "TEXT"),
    # ‏#1839: הדיווח (progress) האחרון שהתקבל מהמכונה (`agent_seen`). נפרד
    # מ-`last_seen` (= hello), שעליו נשען room (#1721). NULL = לא דיווחה.
    ("net_devices", "progress_seen", "TEXT"),
    # ‏#906: השאלה שהמכונה ממתינה עליה לאדם (‏hello עם `waiting_for`).
    # NULL = לא ממתינה; כל hello בלי השדה מנקה אותה.
    ("net_devices", "prompt", "TEXT"),
    # ‏#1367 (נדב 14/10): ``copying`` = שיכפול מקומי כותב במכונה עכשיו (hello
    # עם `local_clone`). NULL = לא; נכתב תמיד, כמו prompt — hello בלי השדה מנקה.
    ("net_devices", "local_clone", "TEXT"),
    # ‏#2031: משכפל שמחזיק את התוצאות עד "כבה" (#2024) — הדיווח held האחרון
    # (‏server/agent/held.py). NULL = לא מוחזק; hello מצטרף ומסירת כיבוי מנקים.
    ("net_devices", "held_at", "TEXT"),
    ("net_devices", "held_json", "TEXT"),
    # ‏#1367: בקשת כוח שהמפעיל אישר מעל אזהרת "באמצע שיכפול מקומי". רק היא
    # מכבה מכונה שכותבת; בקשה בלי אישור נזרקת אצל הסוכן ונרשמת ביומן שלו.
    ("power_requests", "confirmed", "INTEGER NOT NULL DEFAULT 0"),
    # ‏#402: מה הסוכן מצא כשספר אפס דיסקים — `drives` / `no_disks` /
    # `no_ports` / `unchecked` (ממשק 2). בלי זה "מגירות=0" קיפל "לא חובר
    # כונן" ו"בקר ה-SATA מנוטרל בקושחה" — פעולה הפוכה — לאותו מספר.
    # NULL = סוכן ישן שלא שלח; COALESCE כמו disks_json.
    ("net_devices", "disk_probe", "TEXT"),
    # מספר המגירות שהוגדר לכל מחשב שכפול במסוף (#695).
    ("machines", "drawer_count",
     "INTEGER NOT NULL DEFAULT 3 CHECK (drawer_count BETWEEN 1 AND 8)"),
    ("machines", "drawer_slots", "TEXT"),
    ("net_devices", "optical_json", "TEXT"),
    # ‏#2056/#2059: מה הסוכן החזיר ב-hello שהוא מחיל (``applied_timing``, JSON). NULL = לא
    # דיווח (סוכן ישן) -- "לא הוחל", לא "הוחל" (עיקרון 5).
    ("net_devices", "applied_timing", "TEXT"),
    # בחירת דיסקי היעד לסבב, לכל מכונה. NULL = סבב ישן (התנהגות "כל הדיסקים").
    # '[]' הוא בחירה ריקה מפורשת ואינו חוקי לסבב חדש (#695).
    ("room_rounds", "target_slots_json", "TEXT"),
    # ‏#1269: דגם הדרייברים שנבחר לכל דיסק יעד (`server/rounds/room_drivers.py`).
    # NULL = הסבב בלי דרייברים — בדיוק ההתנהגות מלפני ה-Issue.
    ("room_rounds", "driver_models_json", "TEXT"),
    # הדיווח האחרון כפי שהגיע, יעד-יעד. סבב החדר סופר ממנו אילו
    # מגירות (לפי serial) נכתבו בהצלחה.
    ("session_members", "targets_json", "TEXT"),
    # ‏#720: תוצאת ה-staging של הדרייברים כפי שהסוכן דיווח בדיווח הסיום
    # (‏staged / no_match / skipped / failed + סיבה). NULL = השלב לא רץ
    # (סוכן ישן, או שהדיווח הסופי הגיע בלי השדה) — לא "אין התאמה".
    ("session_members", "drivers_json", "TEXT"),
    # הכרעת נדב 17/10: מה שהקרנל של המקלט זרק בזמן הזרם (`udp_drops` בדיווח,
    # סכום על מחיצות המולטיקאסט). NULL = לא דווח — לא "אפס", והקצב לא עולה.
    ("session_members", "udp_drops_json", "TEXT"),
    # ‏#1885: מעבד/throttle/טמפ' של המשכפל בגל (`cloner_cpu.well_formed`). NULL = לא דווח.
    ("session_members", "cpu_json", "TEXT"),
    # סבב לחלק מהכיתה: רשימת ה-MAC שנבחרו. NULL = כל הקבוצה.
    # רק הנבחרים מוערים ב-WoL ורק הם מצטרפים — השאר עולים מהדיסק.
    ("sessions", "roster_json", "TEXT"),
    # הזרם (#60). התקנה קיימת מכילה סבבי מולטיקאסט בלבד, ולכן ברירת
    # המחדל היא בדיוק מה שהיה — ואף שורה קיימת לא משנה משמעות.
    ("sessions", "kind",
     "TEXT NOT NULL DEFAULT 'multicast'"
     " CHECK (kind IN ('multicast', 'unicast'))"),
    # ‏#186: חסימת משתמש. **חותמת ולא דגל** — "מתי נחסם" הוא מידע
    # שהמפעיל צריך, ו-NULL הוא "פעיל". התקנה קיימת מקבלת NULL בכל
    # שורה, כלומר אף משתמש קיים לא נחסם על ידי המיגרציה.
    ("users", "disabled_at", "TEXT"),
    # ‏#1093: ערכת נושא לפי משתמש. התקנה קיימת מקבלת auto — לפי המערכת,
    # כמו לפני שהבחירה עברה מהדפדפן לשרת.
    ("users", "theme", "TEXT NOT NULL DEFAULT 'auto'"),
    # ‏#1085: החלפת סיסמה כפויה, MFA, החשבון המקומי, וביטול sessions (#1075).
    ("users", "must_change_password", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "mfa_secret", "TEXT"),
    ("users", "mfa_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "mfa_enrolled_at", "TEXT"),
    ("users", "is_builtin", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "auth_epoch", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "mfa_last_step", "INTEGER"),
    # ‏#1008: כניסה אחרונה ומאיפה. NULL בכל משתמש קיים = "לא נרשמה כניסה
    # מאז העדכון" — לא "מעולם לא נכנס", ולא נגזר בדיעבד מהיומן (שורת
    # ה-login שם אינה נושאת כתובת).
    ("users", "last_login_at", "TEXT"),
    ("users", "last_login_from", "TEXT"),
    # נדב 18/10: "דרוש אימות דו-שלבי" למשתמש (console_mfa.enrollment_required). קיים — 0.
    ("users", "mfa_required", "INTEGER NOT NULL DEFAULT 0"),
    # ‏#530: האסימון שמוכיח שהפונה הוא בעל המשימה. ‏NULL בהתקנה קיימת,
    # כלומר משימות שנוצרו לפני המיגרציה **אינן ניתנות לכתיבה** —
    # ‏`claim` מסרב על `token` ריק. זו הכרעה: משימה ישנה שתיתקע עדיפה
    # על משימה ישנה שכל אחד יכול לכתוב עליה.
    ("tasks", "token", "TEXT"),
    # ‏#435: הדיווח האחרון של הקליטה כפי שהגיע, יעד-יעד. משם
    # ‏`capture_progress` שולף את `source_progress` — מכנה ההתקדמות בציר
    # הלא-דחוס (בלוקי partclone), שאין לו ייצוג בעמודות bytes_* הדחוסות.
    # ‏NULL בשורות שקדמו למיגרציה, כלומר "אין דיווח מפורט", לא "אפס".
    ("tasks", "targets_json", "TEXT"),
    # ‏#1576 (נדב 17/10): מאיפה הוזמנה המשימה. ‏`build_gui` = הגואי של מחשב
    # הבנייה -- ערוץ הסוכן, ‏kiosk.mark_build_gui; משימה כזו שלא התחילה כשהמכונה אותחלה
    # מבוטלת (boot_cancel.py). שורה קיימת = `console` -- לא מבוטלת.
    ("tasks", "origin", "TEXT NOT NULL DEFAULT 'console'"),
    # ‏#980 §4: שלב הקליטה שהסוכן דיווח (‏`tasks.CAPTURE_STAGES`) ומתי נכנס
    # אליו. NULL = לא דווח (סוכן ישן, או משימה שטרם החלה) — לא "שלב ראשון".
    ("tasks", "stage", "TEXT"),
    ("tasks", "stage_started_at", "TEXT"),
    # ‏#1470: האימות ברקע אחרי הקליטה — בייטים שנקראו שוב מהדיסק מתוך הסך.
    # ‏NULL = המשימה לא הגיעה לאימות (כל שורה שקדמה למיגרציה), לא "אפס".
    ("tasks", "verify_bytes", "INTEGER"),
    ("tasks", "verify_total", "INTEGER"),
    # ‏#1843: מתי הגיע הדיווח הראשון של המשימה (‏`reports._ingest_task`) — תחילת
    # הקליטה כפי שהשרת ראה אותה, ממנה `learn_eta.capture` לומד כמה זמן קליטה
    # לוקחת. NULL = לא דווח (כל שורה שקדמה למיגרציה) — אינה מדידה, לא "אפס".
    ("tasks", "started_at", "TEXT"),
    # ‏#1901: ה-serial של דיסק המקור כפי שה-hello דיווח בזמן ההזמנה. NULL = לא
    # ידוע (משימה ישנה, או דיסק בלי serial) — הסוכן לא מנחש לפי השם.
    ("tasks", "disk_serial", "TEXT"),
    # ‏#1848: מתי נרשם הפירור האחרון, בשבר שנייה (‏`time.time()` של השרת) — `at` הוא
    # ISO בשניות, ומעברים של שבר שנייה היו נמדדים 0 או 1. NULL = שורה מלפני העמודה —
    # המעבר הבא ממנה אינו נמדד (לא "0 שניות").
    ("boot_steps", "at_epoch", "REAL"),
    # ‏#740: חומר האמון הבין-שרתי על הראשי. NULL בשורות משני שקדמו ל-#740
    # (נרשמו לפני שכבת ה-mTLS) — הן תצוגה בלבד עד שנרשמות מחדש. שורה
    # חדשה מ-``enroll_node`` ממלאת את כולן.
    ("storage_nodes", "node_id", "TEXT"),
    ("storage_nodes", "protocol_version", "TEXT"),
    ("storage_nodes", "pinned_spki", "TEXT"),
    ("storage_nodes", "client_cert_ref", "TEXT"),
    # ‏#59: בחירת המחיצה להרחבה מהקונסולה בפתיחת הסבב. NULL בכל שורה
    # קיימת — התקנה שמוגרת רואה בדיוק את הבחירה האוטומטית שהייתה לה.
    ("sessions", "expand_partition", "TEXT"),
    # ‏#854: סיבת כשל המשדר נשמרת בנפרד מסגירה יזומה. העמודה לבדה אינה
    # מספיקה להתקנה קיימת — ה-CHECK הישן נבנה מחדש למטה.
    ("sessions", "failed_reason", "TEXT"),
    ("room_rounds", "expand_partition", "TEXT"),
    # ‏#715: המקור של סבב החדר. כל שורה קיימת היא סבב מספרייה — ברירת
    # המחדל אומרת בדיוק את זה, ואף סבב ישן אינו משנה משמעות.
    ("room_rounds", "source_kind", "TEXT NOT NULL DEFAULT 'library'"),
    ("room_rounds", "source_mac", "TEXT"),
    ("room_rounds", "source_disk", "TEXT"),
    ("room_rounds", "source_task_id", "TEXT"),
    ("room_rounds", "live_manifest_json", "TEXT"),
    ("room_rounds", "failed_reason", "TEXT"),
    # ‏#1760: מגירות שהסוכן דילג עליהן כאדומות (‏SMART fail / קריאה בלבד) בסבב —
    # סגורות, לא נכתבו: אינן "טריות", אינן פותחות גל ואינן מעירות. סבב קיים — אין.
    ("room_rounds", "skipped_serials", "TEXT NOT NULL DEFAULT '[]'"),
    # ‏#1936 (FI O2): מגירות שמפעיל ביטל ("בטל דיסק זה") — סגורות בסבב, לא נכתבו ולא
    # נכשלו: אינן פותחות גל ואינן נכתבות שוב בו. סבב קיים — אין.
    ("room_rounds", "cancelled_serials", "TEXT NOT NULL DEFAULT '[]'"),
    # ‏#929: רשומת כיווץ לדיסק עם כמה מחיצות. NULL בשורה שנפתחה לפני —
    # רשומה של מחיצה אחת, והעמודות idx/start_sector/… הן היא.
    ("shrink_records", "partitions", "TEXT"),
    # ‏#1071: כיוון ההעברה. התקנה קיימת — כל השורות הן push, ברירת המחדל.
    ("storage_transfers", "direction",
     "TEXT NOT NULL DEFAULT 'push'"),
    # #1697: יעד ה-push במשני. שורות קיימות נשלחו ל-loc_local.
    ("storage_transfers", "location_id",
     "TEXT NOT NULL DEFAULT 'loc_local'"),
    # ‏#1017: מתי המשני ענה לאחרונה, ומה הכשל של הקריאה האחרונה (ומתי).
    # נכתבים בכל קריאה בין-שרתית (`storage_nodes.record_contact`). NULL
    # בשורה קיימת = "לא נמדד עדיין", לא "לא ענה".
    ("storage_nodes", "last_seen_at", "TEXT"),
    ("storage_nodes", "last_error", "TEXT"),
    ("storage_nodes", "last_error_at", "TEXT"),
    # ‏#1300: גרסת התוכנה שהמשני דיווח (`server_version` בתשובת /ping ו-/machines),
    # ומתי נקראה. NULL ב-`server_version_at` = "לא נבדקה"; נקראה ו-NULL = המשני
    # לא יודע את הגרסה שלו (אין תג). נכתבות ב-`storage_nodes.record_version`.
    ("storage_nodes", "server_version", "TEXT"),
    ("storage_nodes", "server_version_at", "TEXT"),
    # #1564: positive evidence on the secondary itself. Updated only after
    # the paired parent passes the shared mTLS/SPKI/token guard.
    ("parent_credentials", "last_contact_at", "TEXT"),
    # קצב אוטומטי (הכרעת נדב 11/10): מה ש-udp-sender ספר בסוף הגל. NULL בשורות
    # שקדמו למיגרציה = "לא נמדד" — הקצב אינו עולה מהן.
    ("wave_rates", "stream", "TEXT"),
    # הכרעת נדב 17/10: ‏`udp_drops` של מקלטי הגל. NULL בשורות שקדמו = "לא נקרא".
    ("wave_rates", "drops", "TEXT"),
    # ‏#1758: מה שכל מחשב שיכפול כתב בגל — goodput. NULL בשורות שקדמו = "לא נמדד",
    # והגל ההוא נבחן בכלל של לפני #1758 (`drive_rates._next_from`).
    ("wave_rates", "delivered", "TEXT"),
]


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    # ‏#1127: כל ה-ALTERים בטרנזאקציה אחת — מיגרציה שנקטעה באמצע השאירה
    # סכימה חצי-מעודכנת שהעלייה הבאה לא יודעת לזהות.
    with writing(conn):
        for table, column, definition in ADDED_COLUMNS:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _state_check_has(conn: sqlite3.Connection, table: str, state: str) -> bool:
    """האם אילוץ המצב של הטבלה כבר מכיר את הערך.

    ‏sqlite אינו מאפשר ``ALTER CHECK``. בדיקת עמודות אינה מספיקה כאן:
    התקנה יכולה כבר להכיל ``failed_reason`` ועדיין לסרב ל-``failed``.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return bool(row and row["sql"] and f"'{state}'" in row["sql"])


def _rebuild_state_tables(conn: sqlite3.Connection) -> None:
    """מרחיב CHECK-ים קיימים בלי לאבד שורות או לשבור מפתחות זרים.

    יוצרים טבלה חדשה, מעתיקים, מוחקים את הישנה ורק אז משנים שם. לא
    משנים את שם הטבלה הישנה: בגרסאות sqlite חדשות פעולה כזאת משכתבת את
    ה-FK של ``session_members`` לשם הזמני ומשאירה אותו שבור אחרי המחיקה.
    """
    sessions = not _state_check_has(conn, "sessions", "failed")
    rooms = (not _state_check_has(conn, "room_rounds", "failed")
             or not _state_check_has(conn, "room_rounds", "closing"))
    # ‏#977 §4: ‏`power_requests.action` מכיר גם `reboot`. אין לטבלה מפתחות
    # זרים, והבקשות שממתינות מועתקות כמו כל שורה אחרת כאן.
    power = not _state_check_has(conn, "power_requests", "reboot")
    if not sessions and not rooms and not power:
        return

    # אי-אפשר לשנות foreign_keys בתוך טרנזאקציה. ה-ALTER-ים שלמעלה
    # נשמרים קודם; כל בנייה מחדש עצמה אטומית.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        if sessions:
            conn.execute("""
                CREATE TABLE sessions_new (
                    id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL REFERENCES groups(id),
                    image_id TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    expected_clients INTEGER NOT NULL,
                    wait_seconds INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('open', 'running', 'failed', 'closed')),
                    opened_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_join_at REAL NOT NULL,
                    started_at TEXT,
                    closed_at TEXT,
                    roster_json TEXT,
                    kind TEXT NOT NULL DEFAULT 'multicast'
                         CHECK (kind IN ('multicast', 'unicast')),
                    expand_partition TEXT,
                    failed_reason TEXT
                )
            """)
            conn.execute("""
                INSERT INTO sessions_new
                    (id, group_id, image_id, prefix, expected_clients, wait_seconds,
                     state, opened_by, created_at, last_join_at, started_at, closed_at,
                     roster_json, kind, expand_partition, failed_reason)
                SELECT id, group_id, image_id, prefix, expected_clients, wait_seconds,
                       state, opened_by, created_at, last_join_at, started_at, closed_at,
                       roster_json, kind, expand_partition, failed_reason
                  FROM sessions
            """)
            conn.execute("DROP TABLE sessions")
            conn.execute("ALTER TABLE sessions_new RENAME TO sessions")
        if rooms:
            conn.execute("""
                CREATE TABLE room_rounds_new (
                    id TEXT PRIMARY KEY,
                    image_id TEXT NOT NULL,
                    target_drives INTEGER NOT NULL,
                    written_drives INTEGER NOT NULL DEFAULT 0,
                    written_serials TEXT NOT NULL DEFAULT '[]',
                    skipped_serials TEXT NOT NULL DEFAULT '[]',
                    cancelled_serials TEXT NOT NULL DEFAULT '[]',
                    target_slots_json TEXT,
                    expand_partition TEXT,
                    driver_models_json TEXT,
                    source_kind TEXT NOT NULL DEFAULT 'library',
                    source_mac TEXT,
                    source_disk TEXT,
                    source_task_id TEXT,
                    live_manifest_json TEXT,
                    state TEXT NOT NULL CHECK (state IN ('active', 'closing', 'failed', 'closed')),
                    wave_session_id TEXT,
                    wave_number INTEGER NOT NULL DEFAULT 1,
                    opened_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    closed_at TEXT,
                    failed_reason TEXT
                )
            """)
            conn.execute("""
                INSERT INTO room_rounds_new
                    (id, image_id, target_drives, written_drives, written_serials,
                     skipped_serials, cancelled_serials,
                     target_slots_json, expand_partition, driver_models_json,
                     source_kind, source_mac,
                     source_disk, source_task_id, live_manifest_json, state,
                     wave_session_id, wave_number, opened_by, created_at, closed_at,
                     failed_reason)
                SELECT id, image_id, target_drives, written_drives, written_serials,
                       skipped_serials, cancelled_serials,
                       target_slots_json, expand_partition, driver_models_json,
                       source_kind, source_mac,
                       source_disk, source_task_id, live_manifest_json, state,
                       wave_session_id, wave_number, opened_by, created_at, closed_at,
                       failed_reason
                  FROM room_rounds
            """)
            conn.execute("DROP TABLE room_rounds")
            conn.execute("ALTER TABLE room_rounds_new RENAME TO room_rounds")
        if power:
            conn.execute("""
                CREATE TABLE power_requests_new (
                    mac          TEXT PRIMARY KEY,
                    action       TEXT NOT NULL CHECK (action IN ('poweroff', 'reboot')),
                    requested_at TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    confirmed    INTEGER NOT NULL DEFAULT 0
                )
            """)
            # ‏#1367: `confirmed` נוסף ב-_add_missing_columns, שרץ לפני הבנייה הזו
            conn.execute("INSERT INTO power_requests_new (mac, action, requested_at, requested_by, confirmed)"
                         " SELECT mac, action, requested_at, requested_by, confirmed FROM power_requests")
            conn.execute("DROP TABLE power_requests")
            conn.execute("ALTER TABLE power_requests_new RENAME TO power_requests")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


#: העמודות שכל טבלת אחסון (#655/#723/#727) חייבת. טבלה חלקית שנוצרה
#: לפני שהסכימה התייצבה אינה ממוגרת ע"י ``CREATE TABLE IF NOT EXISTS``,
#: וגם ``ADDED_COLUMNS`` אינו מכסה אותה (חלק מעמודותיה ``NOT NULL`` בלי
#: ברירת מחדל, ו-``ALTER TABLE ADD COLUMN`` פשוט אינו יכול להוסיף כאלה).
#: לכן במקום מיגרציה שקטה — בדיקה שנכשלת בקול (#732).
_STORAGE_SCHEMA_COLUMNS = {
    "storage_node_groups": {"id", "label", "sort"},
    "storage_nodes": {"id", "label", "base_url", "group_id", "tls_fingerprint",
                      "node_id", "protocol_version", "pinned_spki",
                      "client_cert_ref", "credential_ref", "enrolled_at",
                      "disabled_at"},
    "parent_credentials": {"singleton", "parent_id", "token_hash",
                           "bound_cert_ref", "pinned_parent_spki",
                           "protocol_version", "enrolled_at", "last_contact_at"},
    "pairing_windows": {"singleton", "code_hash", "code_salt", "opened_at",
                        "expires_at", "attempts_remaining"},
    "storage_identity": {"singleton", "node_id", "server_spki",
                         "server_cert_ref", "server_key_ref"},
    "storage_transfers": {"id", "node_id", "image_id", "image_name", "state",
                          "bytes_sent", "bytes_total", "error", "started_by",
                          "created_at", "updated_at", "direction", "location_id"},
    "storage_locations": {"id", "name", "type", "params_json", "mount_point",
                          "state", "state_since", "state_detail", "created_by",
                          "created_at", "last_images_json", "last_df_json"},
}


def _verify_storage_schema(conn: sqlite3.Connection) -> None:
    """נכשל בקול אם טבלת אחסון קיימת חסרה עמודות (#732).

    רץ **אחרי** ``executescript``: אם הטבלה נוצרה זה עתה היא מלאה
    ותעבור; אם קדמה לה טבלה חלקית, ``CREATE TABLE IF NOT EXISTS`` לא
    נגע בה, וכאן זה נתפס — לא בשאילתה הראשונה שמפילה 500 מול כיתה."""
    for table, expected in _STORAGE_SCHEMA_COLUMNS.items():
        columns = {row["name"]
                   for row in conn.execute(f"PRAGMA table_info({table})")}
        if not columns:
            continue                 # לא קיימת — executescript נכשל קודם אם צריך
        missing = expected - columns
        if missing:
            raise SchemaError(
                f"טבלת '{table}' קיימת אך חסרה עמודות: "
                f"{', '.join(sorted(missing))}. שחזרו את קובץ הנתונים מגיבוי "
                f"או מחקו את הטבלה החלקית — אתחול לא ממגר אותה בשקט.")


#: מה ש"בדיקה ואז כתיבה" בקוד לא יכולה לאכוף. שני תהליכונים, שני
#: חיבורים, ובין ה-SELECT ל-INSERT יש חלון של כמה שאילתות — ‏#103 (שני
#: שידורים פתוחים) ו-#104 (שתי משיכות לאותה תחנה). האינדקס החלקי
#: הייחודי מכריע ברמת ה-DB: ה-INSERT השני **נכשל**, ולא מנצח בשקט.
#:
#: ‏`roster_json` של משיכה הוא תמיד רשימה בת MAC אחד (‏`open_pull`),
#: ולכן ייחודיות עליו היא בדיוק "משיכה פעילה אחת לתחנה". ‏NULL אינו
#: ייחודי ב-sqlite, ולכן שורות ישנות בלי roster לא חוסמות דבר — הן
#: נבדקות בקוד (`pulls.active_for`).
_ACTIVE = "state IN ('open', 'running')"
UNIQUE_ACTIVES = [
    ("one_active_broadcast", "kind", "kind = 'multicast'"),
    ("one_active_pull_per_station", "roster_json", "kind = 'unicast'"),
]


def _close_duplicate_actives(conn: sqlite3.Connection) -> None:
    """התקנה בשטח יכולה כבר להחזיק שורות שמפרות את האינדקס, ואז יצירתו
    נכשלת והשרת לא עולה. אלה בדיוק סבבי הרפאים של #103: ‏`active_broadcast`
    מחזירה ``LIMIT 1``, ולכן כל מה שמעבר לראשון היה בלתי נראה ממילא — אף
    מכונה לא הצטרפה אליו ואף מפעיל לא ראה אותו.

    נשמר הראשון (אותו סדר בדיוק: ‏``created_at`` ואז ``rowid``), והשאר
    נסגרים **ביומן**. סגירה שקטה של סבב היא מה שאסור; סגירה רשומה של
    סבב שלא היה נראה מעולם היא ניקוי המצב שהבאג הותיר.
    """
    for name, key, kind in UNIQUE_ACTIVES:
        extra = conn.execute(
            f"SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY {key}"
            f" ORDER BY created_at, rowid) AS rn FROM sessions"
            f" WHERE {kind} AND {_ACTIVE} AND {key} IS NOT NULL) WHERE rn > 1"
        ).fetchall()
        for row in extra:
            conn.execute(
                "UPDATE sessions SET state = 'closed', closed_at = ? WHERE id = ?",
                (now_iso(), row["id"]),
            )
            journal(conn, "session_dedupe", f"{row['id']} — {name}")
        conn.commit()


def _create_unique_indexes(conn: sqlite3.Connection) -> None:
    """אחרי הפינוי — והוא חייב להצליח. אינדקס שלא נוצר הוא אכיפה שאינה
    קיימת, ושרת שעולה בלעדיו נראה תקין בדיוק כמו שרת מוגן (עיקרון 5)."""
    for name, key, kind in UNIQUE_ACTIVES:
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON sessions ({key})"
            f" WHERE {kind} AND {_ACTIVE}"
        )
    conn.commit()


def _open(path: str) -> sqlite3.Connection:
    """חיבור גולמי אחד, עם ההגדרות שכל חיבור לבסיס הזה חייב."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    # ‏#1941: commit שהוחזר כהצלחה שורד נפילת חשמל -- FULL במפורש, לא ברירת המחדל של הספרייה
    # (2 בדביאן 13, נמדד 21/10; ‏NORMAL בבנייה אחרת מאבד את ה-commit האחרון). הקריאה בחזרה היא הראיה.
    conn.execute("PRAGMA synchronous = FULL")
    found = conn.execute("PRAGMA synchronous").fetchone()[0]
    if found != 2:
        conn.close()
        raise RuntimeError(f"PRAGMA synchronous = FULL did not hold on {path}: {found}")
    conn.execute("PRAGMA foreign_keys = ON")
    # ממתינים על נעילה במקום להיכשל מיד. עם חיבור לכל תהליכון זו כבר
    # לא חגורה תיאורטית: WAL מרשה כותב אחד בכל רגע, והשאר ממתינים כאן.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _restrict_mode(path: str) -> None:
    """‏#1632: הקובץ מחזיק את ``console_secret`` (חותם העוגיות) ואת גיבובי
    הסיסמאות, ו-umask ברירת המחדל יצר אותו 0644. ‏0600 — רק root (השרת,
    ה-DCUI והמתקין רצים כ-root). ‏sqlite יוצר את ``-wal``/``-shm`` בהרשאות
    של הקובץ הראשי, אבל קבצים שכבר נוצרו 0644 נשארים כך — ולכן גם הם.
    ‏``chmod`` שנכשל זורק: השרת לא עולה עם סוד קריא לכל (עיקרון 5)."""
    if os.name != "posix":
        return
    for suffix in ("", "-wal", "-shm"):
        target = path + suffix
        if os.path.exists(target):
            os.chmod(target, 0o600)


#: ‏#1894: גרסת הסכמה, נכתבת ל-``PRAGMA user_version`` בסוף כל ``_initialize``.
#: **כל שינוי סכמה מעלה אותה** (עמודה ב-``ADDED_COLUMNS``, טבלה, ‏CHECK שהורחב) —
#: ‏``tests/test_schema_version_1894.py`` נכשל כשהסכמה השתנתה והמספר לא. קוד
#: ישן שפוגש DB מגרסה גבוהה משלו (חזרה לגרסה קודמת אחרי שהחדשה כבר מיגרה)
#: מסרב לעלות — ואינו מריץ עליו את המיגרציות שלו.
SCHEMA_VERSION = 7   # ‏7: net_devices.applied_timing (#2056/#2059), disk_sets/disk_set_members/disk_set_history/disk_inventory (#1857), fw_snapshots/fw_baseline (#2080)


def _initialize(conn: sqlite3.Connection) -> None:
    """הסכימה וברירות המחדל — פעם אחת לקובץ, לא פעם אחת לחיבור."""
    found = conn.execute("PRAGMA user_version").fetchone()[0]
    if found > SCHEMA_VERSION:
        raise SchemaError(
            f"ה-DB מגרסת סכמה {found}, והקוד הזה מכיר עד {SCHEMA_VERSION} — ה-DB נוצר או "
            "עודכן בגרסה חדשה יותר (חזרה לגרסה קודמת אחרי מיגרציה?). השרת אינו עולה על "
            "DB שהוא אינו מכיר: שחזרו את העותק שלפני העדכון "
            "(imagesrv.db.pre-upgrade) או חזרו לגרסה החדשה (#1894)")
    conn.executescript(SCHEMA)
    # #1178: בדיקת boot מקומית ישנה נרשמה כתחנה. הניקוי אידמפוטנטי
    # ורץ לפני שהקונסולה יכולה לקרוא את net_devices.
    conn.execute("DELETE FROM net_devices WHERE lower(mac) = '00:00:00:00:00:00'")
    # ‏#1266: SSH יצא מהמוצר. מתגי ה-SSH שהתקנה ותיקה השאירה (‏`ssh:stations`,
    # ‏`ssh:iface:<nic>`) וטבלת מפתחות ה-host של התחנות אין להם משמעות בלי
    # הקוד שקרא אותם. הניקוי אידמפוטנטי, כמו #1178 שמעליו.
    conn.execute("DELETE FROM settings WHERE key = 'ssh:stations'"
                 " OR key LIKE 'ssh:iface:%'")
    conn.execute("DROP TABLE IF EXISTS machine_ssh_hostkey")
    _add_missing_columns(conn)
    _rebuild_state_tables(conn)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS users_username_nocase"
        " ON users (username COLLATE NOCASE)"
    )
    # ‏#1123: שני מיקומים על נקודת עיגון אחת = שתי שורות fstab על יעד אחד.
    # נכשל בקול על DB ותיק עם כפילות — אין "פינוי" אוטומטי למיקומי אחסון.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS storage_locations_mount_point"
        " ON storage_locations (mount_point)"
    )
    _verify_storage_schema(conn)
    _close_duplicate_actives(conn)
    _create_unique_indexes(conn)
    # נדב 18/10: שרת קיים שבו כל מנהל (לא המקומי) חויב ב-MFA — השדרוג מכבה את החיוב,
    # ובגלוי: שורת יומן. התקנה חדשה (אין עדיין מנהל כזה) — בלי שורה.
    mfa_upgrade = (
        conn.execute("SELECT 1 FROM settings WHERE key = 'mfa_required_for_admins'").fetchone() is None
        and conn.execute("SELECT 1 FROM users WHERE role = 'admin' AND is_builtin = 0"
                         " LIMIT 1").fetchone() is not None)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
    # סוד חתימת ה-cookie של הקונסולה נולד פעם אחת לכל התקנה.
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("console_secret", secrets.token_hex(32)),
    )
    for gid, label, role in FIXED_GROUPS:
        conn.execute(
            "INSERT OR IGNORE INTO groups (id, label, role) VALUES (?, ?, ?)",
            (gid, label, role),
        )
    if found < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    if mfa_upgrade:
        journal(conn, "setting_change", MFA_UPGRADE_NOTE, "")


class Database:
    """אובייקט אחד שמחזיק חיבור sqlite נפרד לכל תהליכון.

    זה לא ליטוש: חיבור sqlite אחד המשותף לכמה תהליכונים מחזיק *מצב
    טרנזאקציה אחד*. ‏uvicorn מריץ endpoint שהוא ``async def`` על לולאת
    האירועים, אבל ``def`` רגיל — וגם כל dependency סינכרוני, כולל
    ``current_user`` של הקונסולה — רץ בתהליכון מהמאגר. כלומר שני
    תהליכונים כותבים על אותו חיבור בכל בקשת קונסולה שמתרחשת תוך כדי
    hello. התוצאה היא לא איטיות אלא חריגות אקראיות —
    "cannot start a transaction within a transaction",
    "cannot commit - no transaction is active", "bad parameter or other
    API misuse" — שיוצאות מה-endpoint כ-500. במדידה: כ-15% מהכתיבות
    נופלות כששלושה תהליכונים חולקים חיבור.

    לכן ההגנה אינה נעילה סביב כל execute (טרנזאקציה נפרשת על כמה
    קריאות, ונעילה לכל קריאה לא מגינה עליה) אלא חיבור לכל תהליכון:
    לכל טרנזאקציה יש מצב משלה, ו-WAL מסדר את הכותבים ביניהם.

    האובייקט מתחזה לחיבור (``execute``/``commit``/``close``) כדי שכל
    מי שמקבל ``conn`` ימשיך לעבוד בלי לדעת. תהליכון שמת משחרר את
    החיבור שלו עם ה-``threading.local``.
    """

    def __init__(self, path: str | Path):
        self._path = str(path)
        self._local = threading.local()
        self._ready = False
        self._init_lock = threading.Lock()

    @property
    def connection(self) -> sqlite3.Connection:
        """החיבור של התהליכון הנוכחי; נפתח בפעם הראשונה שנדרש."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = _open(self._path)
            with self._init_lock:
                if not self._ready:
                    _initialize(conn)
                    _restrict_mode(self._path)
                    self._ready = True
            self._local.conn = conn
        return conn

    def execute(self, sql: str, parameters=()) -> sqlite3.Cursor:
        return self.connection.execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters) -> sqlite3.Cursor:
        return self.connection.executemany(sql, seq_of_parameters)

    def executescript(self, sql: str) -> sqlite3.Cursor:
        return self.connection.executescript(sql)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    @property
    def in_transaction(self) -> bool:
        """האם לתהליכון הזה יש טרנזאקציה פתוחה — כלומר נעילת כתיבה בידו.

        ‏``sqlite3.Connection`` מפרסם את זה, וכל מי שמקבל ``conn`` צריך
        לשאול בלי לדעת אם קיבל חיבור גולמי או את העטיפה (‏`_settle`).
        """
        return self.connection.in_transaction

    def close(self) -> None:
        """סוגר את החיבור של התהליכון הקורא בלבד — לשאר יש משלהם."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def connect(path: str | Path) -> Database:
    """פותח (ובמידת הצורך יוצר) את בסיס הנתונים, מוכן לעבודה.

    מוחזר ``Database`` ולא ``sqlite3.Connection``: אותו ממשק, אבל
    בטוח לתהליכונים. ראו את ההסבר ב-``Database``.
    """
    db = Database(path)
    db.connection            # יצירת הסכימה כאן, על התהליכון שקרא
    return db


def update_one(conn, sql: str, parameters=()) -> bool:
    """‏UPDATE מותנה: מחזיר האם השורה השתנתה, ולא משאיר נעילה מאחוריו.

    כתיבה פותחת טרנזאקציה גם כשלא תאמה אף שורה, ו-sqlite אוחז בנעילת
    הכתיבה עד ``commit`` או ``rollback``. עם חיבור משותף זה היה בלתי
    נראה — כולם ישבו באותה טרנזאקציה. עם חיבור לכל תהליכון, קורא
    שהפסיד במרוץ וזרק חריגה משאיר **נעילה יתומה**: כל שאר התהליכונים
    ממתינים ``busy_timeout`` שלם ואז מקבלים "database is locked".

    הכשל מתחזה לעומס, והוא בסך הכל מנעול שאיש לא שחרר — ולכן השחרור
    יושב כאן, במקום אחד, ולא כשורה שצריך לזכור בכל אתר קריאה.
    """
    cur = conn.execute(sql, parameters)
    if cur.rowcount == 1:
        return True
    conn.rollback()
    return False


@contextmanager
def writing(conn):
    """רצף כתיבות שמסתיים ב-``commit`` — ובכל מסלול יציאה אחר ב-``rollback``.

    זו ההשלמה של ``update_one`` לרצפים. ברצף, הכתיבה הראשונה כבר תפסה
    את נעילת הכתיבה, ואם השנייה זורקת ואיש אינו עושה ``rollback`` —
    הנעילה נשארת יתומה. החיבור הוא של התהליכון (``Database``) והוא
    נשאר בחיים, ולכן היא משוחררת רק בכתיבה המוצלחת הבאה *באותו
    תהליכון*; בינתיים כל השאר ממתינים ``busy_timeout`` שלם ומקבלים
    "database is locked" (#54, ‏#184, ‏#200, ‏#290).

    **וגם כתיבה בודדת צריכה את זה** — כאן עמדה קודם טענה הפוכה
    ("כתיבה בודדת שנכשלת אינה משאירה דבר"), והיא נכונה רק לגבי
    ה*נעילה*. ‏pysqlite מוציא ``BEGIN`` לפני כל DML; כתיבה בודדת
    שנכשלה ב-``database is locked`` לא תפסה נעילה, אבל **הטרנזאקציה
    נשארת פתוחה על החיבור**. מכאן והלאה כל כתיבה עליו נכשלת *מיד*, כי
    ‏sqlite אינו מפעיל את ה-busy handler לשדרוג נעילה בתוך טרנזאקציה
    פתוחה. תהליכון של uvicorn חי לאורך זמן וממוחזר, ולכן אירוע עומס
    חולף אחד היה הופך אותו למורעל עד אתחול התהליך (#272).

    הכשל מתחזה לעומס, ולכן השחרור יושב **כאן, במקום אחד**, ולא כשורה
    שצריך לזכור בכל אתר קריאה.

    ‏``BaseException`` ולא ``Exception``: ‏``KeyboardInterrupt`` בזמן
    כיבוי השרת היה משאיר בדיוק את הנעילה שכאן נסגרת.

    החריגה **ממשיכה הלאה** — רצף שנכשל בשקט הוא בדיוק מה שעיקרון 5
    אוסר: מכונה שהצטרפה ואינה רשומה.

    ה-``commit`` עצמו נמצא בתוך השמירה: גם הוא יכול לזרוק (דיסק מלא
    ברגע הכתיבה), וגם אז הנעילה צריכה להשתחרר.
    """
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _settle(conn) -> None:
    """סוגר טרנזאקציה שהקורא עוד מחזיק, **לפני** ההמתנה ל-``_write_lock``.

    זהו סדר הנעילות של #457 באכיפה. כתיבה שהצליחה משאירה את הטרנזאקציה
    פתוחה — ‏`update_one` עושה ``rollback`` רק כשלא תאם שורה — ולכן
    הקורא ממשיך ל-`journal` כשנעילת הכתיבה של sqlite עדיין בידו. שם הוא
    ממתין ל-`_write_lock`, ובאותו רגע כותב אחר יכול להחזיק את
    ‏`_write_lock` ולהמתין לנעילת sqlite שלו. שניהם ממתינים זה לזה עד
    ש-``busy_timeout`` שובר, והצד שנשבר מדווח ``database is locked``.
    הכשל **מתחזה לעומס** — בדיוק כמו ב-#54.

    ‏``commit`` ולא ``rollback``: הקורא כתב בכוונה, וכל אתר קריאה כאן
    ממילא עושה ``commit`` בשורה הבאה. גם ``writing`` שבתוך הבלוק היה
    מקמט את הטרנזאקציה הזו — ההבדל היחיד הוא **מתי**, ו"מתי" הוא כל
    הבאג: אחרי הנעילה זו נעילה משולבת, לפניה זו לא.

    לכן זה יושב כאן, במקום אחד, ולא כשורה שצריך לזכור בכל אתר קריאה —
    אותה צורה שנבחרה ב-#54 עבור `update_one`.

    **מה זה לא עושה:** אטומיות של "כתיבה + שורת יומן" אינה קיימת כאן
    ממילא. ‏`_write_lock` אינו ``RLock`` (‏`test_the_write_lock_is_not_reentrant`),
    ולכן קריאה ל-`journal` **בתוך** בלוק כתיבה נתקעת לנצח. שתי
    הטרנזאקציות היו נפרדות בכל מקרה; ‏`_settle` רק מפריד אותן במפורש.
    """
    if conn.in_transaction:
        conn.commit()


#: מתחת לגיל הזה של ``last_seen``, ‏hello חוזר אינו מצדיק כתיבה (#136).
#:
#: כל hello היה טרנזאקציית כתיבה מלאה: 30 כתיבות בדקה לכל מכונה, 600
#: לכיתה של 20 — ודווקא ברגע שבו השרת עסוק בפתיחת סבב. זה המזון של
#: ‏#54. החניקה היא על ה*כתיבה* ולא על הבקשה: ה-hello נענה כרגיל.
#:
#: הסף חייב להישאר קטן מ-``room.AWAKE_SECONDS`` (30), אחרת "חסכנו
#: כתיבות" הופך מכונה חיה לכבויה על המסך. 15 משאיר מרווח של פי שניים,
#: ו-`test_net_seen_throttle` שומר על היחס.
NET_SEEN_MIN_INTERVAL_SECONDS = 15


def _net_seen_unchanged(row: sqlite3.Row, ip: str | None,
                        disks_json: str | None, now: datetime,
                        monitor_secret: str | None = None,
                        monitor_auth: str | None = None,
                        prompt: str | None = None,
                        disk_probe: str | None = None,
                        firmware: str | None = None,
                        secure_boot: int | None = None,
                        agent_version: str | None = None,
                        local_clone: str | None = None,
                        optical_json: str | None = None,
                        update_optical: bool = False) -> bool:
    """האם השורה כבר אומרת בדיוק את מה שהכתיבה הזו הייתה כותבת.

    ראיה חיובית בלבד (עיקרון 5): חותמת שאי אפשר לפענח, חותמת בלי אזור
    זמן, חותמת מהעתיד (שעון שקפץ), או כל שינוי בכתובת/בדיסקים —
    כולם מחזירים False, כלומר **כותבים**. "לא הצלחנו לבדוק אם צריך
    לכתוב" אינו "בדקנו, אין צורך".
    """
    if ip is not None and ip != row["ip"]:
        return False
    if disks_json is not None and disks_json != row["disks_json"]:
        return False
    if monitor_secret is not None and monitor_secret != row["monitor_secret"]:
        return False
    if monitor_secret is not None and (row["monitor_auth"] or None) != (monitor_auth or None):
        return False
    if prompt != row["prompt"]:   # #906: גם המעבר שאלה→אין-שאלה נכתב
        return False
    if local_clone != row["local_clone"]:   # #1367: גם הסוף שלו נכתב
        return False
    if update_optical and optical_json != row["optical_json"]:
        return False
    if disk_probe is not None and disk_probe != row["disk_probe"]:   # #402
        return False
    if firmware is not None and firmware != row["firmware"]:          # #500
        return False
    if secure_boot is not None and secure_boot != row["secure_boot"]:
        return False
    if agent_version is not None and agent_version != row["agent_version"]:
        return False
    try:
        last = datetime.fromisoformat(row["last_seen"])
    except (TypeError, ValueError):
        return False
    if last.tzinfo is None:
        return False
    age = (now - last).total_seconds()
    return 0 <= age < NET_SEEN_MIN_INTERVAL_SECONDS


def net_seen(
    conn: sqlite3.Connection, mac: str, ip: str | None,
    disks_json: str | None = None,
    monitor_secret: str | None = None,
    monitor_auth: str | None = None,
    prompt: str | None = None,
    disk_probe: str | None = None,
    firmware: str | None = None,
    secure_boot: int | None = None,
    agent_version: str | None = None,
    local_clone: str | None = None,
    optical_json: str | None = None,
    update_optical: bool = False,
) -> None:
    """כל מגע של מכונה עם השרת — hello או תפריט אתחול — נרשם כאן.

    זו טבלת "מה חי לי ברשת": גם מחשבים שאינם רשומים מופיעים בה,
    עם הכתובת האחרונה שנראתה. התיאור החופשי נשמר בין עדכונים.

    הדיסקים מה-hello נשמרים כפי שדווחו — מסך מחשב הבנייה מציג מהם
    את "מה מותקן עכשיו", והקליטה נפתחת על כונן שקיים באמת.

    ‏hello חוזר בתוך ``NET_SEEN_MIN_INTERVAL_SECONDS`` שאינו מוסיף
    מידע חדש אינו נכתב (#136). הבדיקה היא ``SELECT`` — קורא, ולכן
    ב-WAL הוא אינו נוגע בנעילת הכתיבה בכלל; רק כשיש מה לכתוב נפתחת
    טרנזאקציה. ‏`agent_loops.note` סופר את **הגעת** ה-hello ולא את
    הכתיבה כאן, ולכן החניקה אינה משנה את הספירה שלו.

    ‏#906: ``prompt`` הוא השאלה שהמכונה ממתינה עליה לאדם — ובניגוד
    לשאר השדות הוא **נכתב תמיד**, גם כ-NULL: ‏hello בלי השדה אומר
    "כבר לא ממתינה", ו-COALESCE היה משאיר שאלה שכבר נענתה על המסך.
    """
    now = datetime.now(timezone.utc)
    row = conn.execute(
        "SELECT ip, last_seen, disks_json, monitor_secret, monitor_auth, prompt,"
        " disk_probe, firmware, secure_boot, agent_version, local_clone, optical_json,"
        " monitor_secret_prev"
        " FROM net_devices WHERE mac = ?", (mac,)
    ).fetchone()
    # ‏#1837: hello שנבנה לפני אימות מוניטור והגיע אחריו נושא את הסוד
    # שהמכונה והשרת כבר עזבו (`monitor_rekeyed`). דריסה בו הייתה מחזירה
    # בדיוק את ה-"bad HMAC" שהתיקון סוגר. נזנח **פעם אחת** — ה-hello הבא
    # שנושא אותו הוא ראיה שהמכונה באמת נשארה עליו (כתיבת הקובץ שם נכשלה).
    stale_secret = (row is not None and monitor_secret is not None
                    and monitor_secret == row["monitor_secret_prev"]
                    and monitor_secret != row["monitor_secret"])
    if stale_secret:
        monitor_secret = None
    if row is not None and not stale_secret and _net_seen_unchanged(row, ip, disks_json, now,
                                               monitor_secret, monitor_auth,
                                               prompt, disk_probe,
                                               firmware, secure_boot, agent_version,
                                               local_clone=local_clone,
                                               optical_json=optical_json,
                                               update_optical=update_optical):
        return

    ts = now.isoformat(timespec="seconds")
    # ‏`_write_lock` — זו הכתיבה שכיתה שלמה דורכת עליה בו-זמנית, והיא
    # חייבת תור הוגן ולא מרוץ על נעילת sqlite (#272). ‏`writing` —
    # כתיבה שנכשלה חייבת להשאיר חיבור נקי; ראו שם. ‏`_settle` — סדר
    # הנעילות (#457): לא ממתינים כאן עם נעילת כתיבה ביד.
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO net_devices (mac, ip, first_seen, last_seen, disks_json,"
            " monitor_secret, monitor_auth, prompt, disk_probe, firmware, secure_boot, agent_version,"
            " local_clone, optical_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (mac) DO UPDATE SET ip = COALESCE(excluded.ip, ip),"
            " last_seen = ?, disks_json = COALESCE(excluded.disks_json, disks_json),"
            " monitor_secret = COALESCE(excluded.monitor_secret, monitor_secret),"
            " monitor_auth = CASE WHEN excluded.monitor_secret IS NOT NULL"
            " THEN excluded.monitor_auth ELSE monitor_auth END,"
            " monitor_secret_prev = CASE WHEN ? THEN NULL ELSE monitor_secret_prev END,"
            " prompt = excluded.prompt,"
            " local_clone = excluded.local_clone,"   # #1367: always, like prompt
            " optical_json = CASE WHEN ? THEN excluded.optical_json ELSE optical_json END,"
            " disk_probe = COALESCE(excluded.disk_probe, disk_probe),"
            " firmware = COALESCE(excluded.firmware, firmware),"
            " secure_boot = COALESCE(excluded.secure_boot, secure_boot),"
            " agent_version = COALESCE(excluded.agent_version, agent_version)",
            (mac, ip, ts, ts, disks_json, monitor_secret, monitor_auth, prompt,
             disk_probe, firmware, secure_boot, agent_version, local_clone, optical_json,
             ts, int(stale_secret), int(update_optical)),
        )
        # ‏#1888: היסטוריית IP — שורה רק כשה-IP השתנה (או ראשון), באותה טרנזאקציה.
        moved = ip is not None and (row is None or ip != row["ip"])
        if moved:
            from server.learn.net_history import KEEP_IPS   # noqa: PLC0415 — net_history מייבא את db
            conn.execute("INSERT INTO ip_history (mac, ip, previous, at) VALUES (?, ?, ?, ?)",
                         (mac, ip, row["ip"] if row is not None else None, ts))
            conn.execute("DELETE FROM ip_history WHERE mac = ? AND rowid NOT IN (SELECT rowid"
                         " FROM ip_history WHERE mac = ? ORDER BY rowid DESC LIMIT ?)",
                         (mac, mac, KEEP_IPS))
    if moved and row is not None and row["ip"]:
        journal(conn, "ip_changed", f"{mac} {row['ip']} → {ip}")


def monitor_rekeyed(conn: sqlite3.Connection, mac: str, used: str,
                    following: str) -> bool:
    """‏#1837: אימות מוניטור הצליח עם ``used``, והמכונה כבר עברה ל-``following``
    (`monitor.next_secret` / `rekey_secret` ב-`agent/monitor.c`). השרת עובר
    איתה עכשיו — לא ב-hello הבא — ולכן החיבור הבא מצליח בניסיון הראשון.

    מותנה ב-``used``: hello שהביא בינתיים סוד אחר (המכונה אותחלה) חדש יותר,
    ואינו נדרס. ``used`` נזכר ב-`monitor_secret_prev`, כדי ש-hello שנבנה לפני
    האימות לא יחזיר אותו (`net_seen`). מחזיר האם השורה עברה."""
    _settle(conn)
    with _write_lock, writing(conn):
        cur = conn.execute(
            "UPDATE net_devices SET monitor_secret = ?, monitor_secret_prev = ?"
            " WHERE mac = ? AND monitor_secret = ?",
            (following, used, mac, used),
        )
        return cur.rowcount == 1


def agent_seen(conn: sqlite3.Connection, mac: str,
               now: datetime | None = None) -> bool:
    """‏#1839: בקשה מאומתת מהסוכן שאינה hello (progress) היא ראיה שהמכונה חיה.

    בזמן כתיבה הסוכן אינו שולח hello — רק progress כל 2ש' — ו-`last_seen`
    נעצר בתחילת הגל: אחרי 90ש' המשכפל הכותב הוצג "לא מחובר" והמוניטור סירב
    לו. הראיה נכתבת לעמודה **משלה**, `progress_seen`, ולא ל-`last_seen`:
    ‏`last_seen` הוא "שלח hello", ובזה נשען `room` — משכפל בספירת הכיבוי
    אינו שולח hello, ולכן hello אחרי פתיחת הגל = "עלה מחדש" (‏#1721). הדיווח
    האחרון של גל 1 היה נראה שם כהתעוררות, וה-WoL החוזר לא היה נשלח.
    ‏`monitor` קורא את שתיהן. בחניקה של #136: ``SELECT`` קורא, וכתיבה רק
    כשהחותמת ישנה מ-15ש' — או כשאי אפשר לקרוא אותה (עיקרון 5). מכונה שמעולם
    לא שלחה hello אינה נוצרת כאן. מחזיר האם נכתב."""
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT progress_seen FROM net_devices WHERE mac = ?",
                       (mac,)).fetchone()
    if row is None:
        return False
    try:
        last = datetime.fromisoformat(row["progress_seen"])
    except (TypeError, ValueError):
        last = None
    if (last is not None and last.tzinfo is not None
            and 0 <= (now - last).total_seconds() < NET_SEEN_MIN_INTERVAL_SECONDS):
        return False
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("UPDATE net_devices SET progress_seen = ? WHERE mac = ?",
                     (now.isoformat(timespec="seconds"), mac))
    return True


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """מסלול הכתיבה של **כל** מסכי ההגדרות — DHCP, כתובות, מתגים,
    רשימת התיקיות. ‏`_write_lock` ו-`writing` מאותה סיבה כמו ב-`net_seen`
    (#313): כתיבה שנכשלה כאן משאירה את החיבור בטרנזאקציה, ומשם הקונסולה
    מפסיקה לשמור עד אתחול השרת. ‏`_settle` — סדר הנעילות של #457."""
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def set_settings(conn: sqlite3.Connection, pairs: dict[str, str],
                 remove: tuple[str, ...] | list[str] = ()) -> None:
    """כותב כמה הגדרות ב**טרנזאקציה אחת** — הכל-או-כלום (#732).

    כמה קריאות ל-`set_setting` הן כמה טרנזאקציות: אם השנייה נכשלת
    (דיסק מלא, נעילה) הראשונה כבר נכתבה, והתוצאה היא מצב מעורב שנקרא
    כתצורה תקינה (עיקרון 5). כאן שתי הכתיבות באותו בלוק ``writing``,
    וכישלון מגלגל את שתיהן. אותו מסלול נעילות של `set_setting`.

    ‏`remove` (‏#1264): מפתחות שנמחקים באותה טרנזאקציה — רשת ההפצה שעוברת
    מ-eth1 ל-bond0 היא כתיבה של אחד ומחיקה של השני, ואסור שתיעצר באמצע."""
    _settle(conn)
    with _write_lock, writing(conn):
        for key in remove:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        for key, value in pairs.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


log = logging.getLogger("imagesrv.db")

#: ‏#1892: הכתיבה האחרונה שנכשלה ב-SQLITE_FULL — ‏{"at", "what"}, או ריק.
#: מתנקה בשורת יומן מוצלחת: זו הראיה החיובית שהמחיצה התפנתה.
_last_full: dict = {}


def disk_full(exc: BaseException) -> bool:
    """‏SQLITE_FULL לפי **קוד השגיאה** של sqlite, לא לפי נוסח ההודעה (#1892)."""
    return (isinstance(exc, sqlite3.OperationalError)
            and getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL)


def note_disk_full(what: str) -> None:
    """רושם כתיבה שנכשלה כי המחיצה מלאה — לבריאות וליומן השירות."""
    _last_full.update({"at": now_iso(), "what": what})
    log.error("database write failed, disk is full: %s (#1892)", what)


def last_disk_full() -> dict | None:
    """‏{"at", "what"} של הכשל האחרון שעוד לא נוקה, או ``None``."""
    return dict(_last_full) or None


def journal(conn: sqlite3.Connection, event: str, detail: str = "", user: str = "") -> None:
    """שורת יומן. נובע ישירות מריבוי המשתמשים — מי עשה מה ומתי.

    נעול, כי זו הכתיבה היחידה שמגיעה גם מתהליכון הרקע של השידור.

    ‏`writing` מאותה סיבה כמו ב-`net_seen` וב-`set_setting` (#379):
    הנעילה מסדרת את הכותבים **שלנו** בתור הוגן, אבל היא אינה עושה
    ``rollback`` ואינה מגנה מפני כותב חיצוני לתהליך — ‏`sqlite3` ידני
    על השרת או גיבוי. שורת יומן שנכשלה בלי ``rollback`` משאירה את
    החיבור בתוך טרנזאקציה, ומשם כל כתיבה עליו נכשלת **מיד** עד אתחול
    השרת (#272). ‏`journal` נקרא כמעט מכל מסלול בשרת, ולכן חיבור
    שהורעל כאן נודד לכל מי שיקבל את התהליכון אחריו — ‏uvicorn ממחזר
    אותם.

    ‏`_settle` הוא סדר הנעילות של #457, וכאן הוא נדרש יותר מבכל אתר
    אחר: ‏`journal` הוא מה שנקרא **מיד אחרי** כתיבה מותנית שהצליחה, וגם
    מה שתהליכון הרקע של השידור כותב — כלומר שני הצדדים של הנעילה
    המשולבת נפגשים כאן.
    """
    _settle(conn)
    try:
        with _write_lock, writing(conn):
            conn.execute(
                "INSERT INTO journal (ts, user, event, detail) VALUES (?, ?, ?, ?)",
                (now_iso(), user, event, detail),
            )
    except sqlite3.OperationalError as exc:
        # ‏#1892: מחיצה מלאה — היומן אינו מפיל את המסלול שקרא לו (hello של
        # מכונה לא רשומה חייב לענות known:false, עיקרון 1). הכשל **אינו
        # נבלע**: הוא נרשם כאן ומוצג כאדום בבריאות (`last_disk_full`).
        # כל שגיאה אחרת ממשיכה הלאה כמו תמיד.
        if not disk_full(exc):
            raise
        note_disk_full(f"journal {event}")
        return
    _last_full.clear()   # ראיה חיובית שהכתיבה חזרה לעבוד
