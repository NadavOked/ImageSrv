"""‏#915/#916: שאריות העיצוב (#829) ב-HTML של הקונסולה שהוצגו כנתונים חיים.

בדיקות תוכן על הקבצים הסטטיים — לא מרנדרות ולא מריצות שרת. כל מחרוזת
כאן היא כזו שנדב ראה על המסך במעבדה (v0.26.1) והייתה קשיחה ב-HTML:
‏`v0.9` בכניסה ובשורת הסטטוס, ‏`הודעות: 2`, ‏`task-count 3`, סבב דמו
‏`LAB1 74%`, שלוש שורות משימות דמו, וכרטיס "כללי naming" עם `LAB1-05`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "static"


def _index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def _console_js() -> str:
    return (STATIC / "console.js").read_text(encoding="utf-8")


def test_index_html_has_no_hardcoded_version():
    """הגרסה מגיעה מ-`/api/console/me` (תג git). ‏`v0.9` בכניסה ובשורת
    הסטטוס היו מהעיצוב; המחזיק היחיד הוא `#serverVersion` — ריק ב-HTML."""
    page = _index()
    assert "v0.9" not in page
    assert re.search(r"\bv\d+\.\d+(\.\d+)?\b", page) is None, "גרסה קשיחה ב-HTML"
    assert '<span id="serverVersion"></span>' in page


def test_index_html_task_dock_has_no_demo_numbers():
    """המעגן והפאנל מתמלאים מ-`renderActivity` (‏`/overview`). ב-HTML: מונה 0,
    בלי סבב דמו, בלי 'הודעות: N', ובלי שורות דמו עם onclick לפונקציות."""
    page = _index()
    for demo in ("הודעות: 2", "LAB1 74%", "פעילות אחרונה",
                 "openLogDetail('", "openImageDetail('Ubuntu", "openMachineDetail('LAB1"):
        assert demo not in page, demo
    assert '<span class="task-count">0</span>' in page
    assert re.search(r'class="task-count">[1-9]', page) is None
    assert '<tbody><tr><td colspan="6">אין העברות פעילות</td></tr></tbody>' in page


def test_the_login_screen_shows_no_version_tag():
    """אין תג גרסה קשיח. הגרסה בתחתית מתמלאת מ-`/me` כשיש session; לפני כן
    רק hostname מהדפדפן (אין endpoint ציבורי לגרסה/fingerprint)."""
    page = _index()
    login = page[page.index('id="login"'):page.index('id="app"')]
    assert 'class="tag' not in login
    assert "גישה מאובטחת" not in login
    assert "זכור אותי" not in login
    assert "קונסולת ניהול ImageCtl" not in login
    assert 'id="login-body"' in login
    assert 'class="art"' in login


def test_login_screens_copy_is_in_console_js():
    """#1085 שלב ב': חמשת המסכים וההודעות לפי המוקאפ — ב-loginHtml, לא ב-HTML קשיח."""
    js = _console_js()
    for s in (
        "בחר סיסמה חדשה",
        "הקוד מאפליקציית האימות",
        "השתמש בקוד גיבוי",
        "הגדרת אימות דו-שלבי",
        "המשך לקונסולה",
        "שמרתי את הקודים במקום בטוח",
        "שם משתמש או סיסמה שגויים",
        "החשבון נעול ל-",
        "קוד שגוי",
        "מקומי · ללא MFA",
    ):
        assert s in js, s
    assert "function loginHtml(" in js
    assert "function passwordCanSubmit(" in js
    assert "function setupContinueEnabled(" in js
    assert "function otpFromPaste(" in js


def test_mfa_remember_checkbox_text_is_not_in_console_js():
    js = _console_js()
    assert "אל תבקש קוד בדפדפן הזה ל-7 שעות" not in js
    assert "mfa-remember" not in js


def test_console_js_fills_the_version_from_me():
    js = _console_js()
    assert 'getElementById("serverVersion")' in js
    assert "ME.version" in js


def test_the_notifications_drawer_lists_what_the_badge_counts():
    """התג בסרגל סופר (‏`updateAlertBadge`) מכונות שנכשלו ובדיקות בריאות
    ‏warn/bad; המגירה חייבת להציג את אותם פריטים — לא "טוען נתונים…" לנצח."""
    js = _console_js()
    fn = js[js.index("function topNotifications"):]
    fn = fn[:fn.index("\nfunction ", 1)]
    assert "טוען נתונים" not in fn
    assert "machineIsFailed" in fn
    assert 'c.state !== "warn" && c.state !== "bad"' in fn
    assert "אין התראות" in fn


def test_the_machines_page_has_no_demo_text_and_no_dead_tabs():
    """‏#916 → ‏#954 גל 3: בלי כרטיס "כללי naming" (‏`LAB1-05`), ובלי הלשוניות
    הישנות "כל התחנות / ניהול לפי סוג" — הדף הוא `machines(tab)` שמצייר את
    הכותרת והלשוניות בעצמו (`own: true`), ו-`machines.js` נמחק."""
    js = _console_js()
    assert "LAB1-05" not in js
    assert "כללי naming" not in js
    assert '"כל התחנות"' not in js and "ניהול לפי סוג" not in js
    assert "machineAdminPage" not in js and "loadMachinesTab" not in js
    assert 'tabs: ["כל המחשבים", "נראו ברשת", "דיסקים אדומים"], render: machines, load: loadMachines, own: true' in js
    assert "machines.js" not in _index()
    assert not (STATIC / "machines.js").exists()


def test_the_machines_filter_is_visible_and_clearable():
    """‏#916: הסינון מלחיצה על קבוצה בעץ נשאר בין מעברים; בלי חיווי הוא
    נראה כמו "אין מחשבים רשומים" מול עץ מאויש. הצומת "מחשבים" מאפס אותו
    (וגם את הכיתה הפתוחה כאובייקט, גל 3), והדף מציג את הקבוצה המסוננת עם
    ביטול וריק שמבחין בין המצבים."""
    js, page = _console_js(), _index()
    assert "MACHINES_FILTER=null;MACHINES_CLASS=null;MACHINES_ROLE=null;selectPageById('machines')" in page
    fn = js[js.index("function machinesTableCard("):js.index("function machinesTabs()")]
    assert "clearMachinesFilter()" in fn
    assert "מוצגת קבוצה" in fn
    table = js[js.index("function machinesTableHtml()"):js.index("function mchSelBar(")]
    assert "אין מחשבים בקבוצה" in table
    assert "אין מחשבים רשומים" in table        # המצב האמיתי של "אין בכלל" נשאר
    assert "function clearMachinesFilter() {\n  MACHINES_FILTER = null;" in js


def test_the_machines_page_lists_shrunk_source_disks_in_orange_with_clear():
    """‏#926: דיסק מקור שכווץ לקליטה ולא הוחזר לגודלו (הרשומה בשרת) מוצג
    בלשונית "דיסקים אדומים" ככרטיס משלו — כתום (`disk-smart` בלי
    `failed_last`: ווינדוס עולה ממנו), עם המחיצה והגודל המקורי במילים וכפתור
    "נקה" — לצד הדיסקים האדומים של #874, ומאותו `loadMachines`."""
    js = _console_js()
    assert 'api("/shrink-records")' in js
    fn = js[js.index("function shrinkRecordsCard"):]
    fn = fn[:fn.index("\nasync function clearShrinkRecord")]
    assert '<span class="disk-smart">' in fn and "failed_last" not in fn
    assert "כווצה לקליטה ולא הוחזרה לגודלה המקורי" in fn
    assert '["נקה", `clearShrinkRecord(' in fn
    assert "/shrink-records/${id}/clear" in js
    machines = js[js.index("function machines(tab = 0)"):js.index("function clearMachinesFilter")]
    assert "shrinkRecordsCard()" in machines and "diskFailuresCard()" in machines


# --- #954 גל 1: הסקירה הכללית נבנתה מחדש, הקוד הישן נמחק ---------------------


def test_static_includes_are_at_least_6_9():
    """‏#954 גל 1 = ‏`?v=6.9`. הבדיקה מונוטונית (≥) ולא שוויון — bump מוצדק
    הבא לא אמור להפיל אותה; שוויון בין כל ה-includes נבדק ב-console_rewrite."""
    versions = {float(v) for v in re.findall(r'\?v=([\d.]+)"', _index())}
    assert versions, "אין ?v= ב-index.html"
    assert min(versions) >= 6.9, versions


def test_old_home_page_code_is_gone():
    """הדף הישן נמחק, לא הוסתר: בלי `object-strip`, בלי "Details", בלי כרטיס
    "אין נתונים להצגה" בתחתית, בלי "+ פעולה" לסקירה, ובלי הלשונית הישנה
    "משימות אחרונות". הסקירה מציירת כותרת ולשוניות בעצמה (`own: true`)."""
    js = _console_js()
    fn = js[js.index("function home("):js.index("function deploy(")]
    for gone in ("object-strip", ">Details<", "אין נתונים להצגה", "emptyDataCard", "footer-note"):
        assert gone not in fn, gone
    assert 'render: home, load: loadHome, own: true' in js
    assert "משימות אחרונות" not in js[js.index("const pages = {"):js.index("let current = ")]
    actions = js[js.index("function openAction()"):js.index("function openDrawer")]
    assert "home:" not in actions, "לסקירה אין תפריט '+ פעולה' גנרי (README §מה שנשבר)"
    assert "if (page.own) return page.render(index);" in js


def test_shared_ui_renderers_exist_for_the_next_pages():
    """שפת העיצוב המשותפת (README §"שפת העיצוב") יושבת ב-`UI` בראש
    console.js — כותרת אובייקט, KPI, כרטיס, datagrid, מצב, מפתח-ערך,
    ציר-זמן, הודעה, ריק-מצב, ו"בקרוב" למה שדורש API."""
    js = _console_js()
    block = js[js.index("const UI = {"):js.index("function fmtDate(")]
    for name in ("objHeader(", "kpi(", "card(", "datagrid(", "status(", "pill(", "barRow(",
                 "kv(", "note(", "empty(", "timeline(", "soon("):
        assert name in block, name
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .obj{", ".page .kpi{", ".page table.dg{", ".page .st{", ".page .kv{",
                ".page .ev{", ".page .note{", ".page .empty{"):
        assert sel in css, sel


def test_uptime_and_deploy_ip_come_from_me_and_null_is_named():
    """‏#968 סגר את "דורש API" של home.md: זמן הפעילות וכתובת ההפצה מגיעים
    מ-`/me` (uptime_seconds, deploy_ip); ‏null מוצג בשם — לא "בקרוב", לא 0,
    ולא 127.0.0.1. ההיוריסטיקה `journalSeverity` נמחקה, לא נשארה לצד השדה."""
    js = _console_js()
    fn = js[js.index("function home("):js.index("function deploy(")]
    assert 'UI.soon("זמן פעילות")' not in fn
    # ‏#1300: משנה הכותרת היא סיכום; זהות השרת (כולל זמן פעילות וכתובת ההפצה) — בכרטיס "שירותים"
    start = js.index("function homeServerCard(")
    card = js[start:js.index("\n}\n", start)]   # the services card (נדב 18/10: no branches card after it)
    assert "homeUptime()" in card and "homeDeployIp()" in card
    assert "ME.uptime_seconds" in js and "ME.deploy_ip" in js
    assert "זמן פעילות לא נבדק" in js and "רשת הפצה לא הוגדרה" in js
    assert "function journalSeverity" not in js
    assert "function journalCls" in js and "row.severity" in js


def test_images_tree_node_is_the_folder_icon_and_sub_folders_carry_a_thin_variant():
    """17/09 (נדב): אייקון "אימג'ים" בעץ → תיקייה. apple-pending-4 החליף לשכבות; הוחזר ב-10/10 (הכרעת נדב).
    כדי שלא תהיה כפילות עם תיקיות המשנה, הן מקבלות וריאנט קטן ודק (t-subfolder); האימג' עם אייקון תמונה."""
    page, js = _index(), _console_js()
    start = page.index('data-page="images"')
    node = page[start:page.index("אימג'ים</span>", start)]
    assert 'M3 7a2 2 0 0 1 2-2h4l2 2h8' in node
    assert 'm12 3 9 5-9 5-9-5Z' not in node
    assert 'circle cx="16"' not in node
    fn = js[js.index("function populateSidebarImages"):js.index("function fillSidebarTree")]
    assert fn.count('uiIcon("t-subfolder")') == 2
    assert 'uiIcon("folder")' not in fn
    assert fn.count('uiIcon("image")') == 1


# ---------- #954 גל 2: ספריית האימג'ים ----------

def test_static_includes_are_at_9_9():
    """גל 2 = ‏7.0; גל 3 (מחשבים) = ‏7.1; גל 3א = ‏7.3; השער (מוניטור רק בדף
    המוניטור) = ‏7.4; גל 5 (בריאות + פורטים) = ‏7.5; גל 6 (מוניטור, דרייברים,
    הגדרות, הרשאות, יומן + WoL למחשב) = ‏7.6; #703 (TLS) = ‏7.7; דף הפורטים
    לחוזה #996/#1015 וגל 7 (סניפים ושרת משני) נכנסו יחד בשער = ‏7.9; #1032
    (סבב ליטוש — hover, שורת סינון, כפתורים כפולים) = ‏8.1 (גל 8, הרשת,
    לקח את 8.0 במקביל); בשער be שניהם יחד = ‏8.2; #649 שלב 1 (ארגז כלים) = ‏8.3;
    #1049 שלב ב' (בריאות המכונה בכרטיס) = ‏8.4; #1048 (כבל ו-LLDP) ותרשים
    הרשת — טקסט התיבות ב-foreignObject במקום <text> שגלש על הקווים = ‏8.6;
    מודל האתרים (וילן השרתים במקום "ניהול", משני דרך FW · 8443 בקו מקווקו) = ‏8.7;
    #1073 (הפצה בלי וובי — `message_he` בכניסה, מטריצת ההרשאות) = ‏8.8;
    #1066 שלב ב' (דף "אחסון", storage.js) = ‏8.9;
    #1093 (ערכת נושא לפי משתמש, נשמרת בשרת) = ‏8.9;
    #1081 (v1 בלי כיתות — `capabilities.classrooms`) = ‏8.9;
    #1077 (מוניטור כבוי בברירת מחדל — אזהרת 5900) = ‏8.9;
    #1085 שלב ב' (מסכי כניסה לפי המוקאפ) = ‏9.0;
    #1033 (גריד הקלונרים — מלבן אנכי, דיסקים מוערמים לפי SATA, שתי שורות
    בתיבת דיסק) = ‏9.0;
    #1071 (pull אימג' מהמשני לראשי) = ‏9.0;
    #1080 (fingerprint SSH בכרטיס המכונה) — הגייט bk איחד את #1085ב/#1033/#1071/#1080 ל-‏9.1;
    #1088 (רשת ההפצה מהקונסולה — הערה בדף הרשת, ההדלקה הראשונה) = ‏9.2;
    #1013 (מתג TFTP 69 בדף הפורטים — אזהרה לפני הקלדת השם) = ‏9.3;
    אזהרה לפני כיבוי של **כל** פורט, לא רק 69 (נדב 19/09) = ‏9.4;
    #1129 (esc() מבריח גרשיים; onclick דרך encodeId) = ‏9.4.
    #1121/#402 (net.js: כשל השלמה = 500; console.js: `disk_probe` — "לא חוברו דיסקים" / "אין פורטי SATA בקושחה" / "לא נבדק"
    במקום "0 דיסקים") = ‏9.5 — v0.48.1 יצא עם 9.4, ולכן bump;
    #1150 (מסך ה-MFA: הודעה אדומה כשאין QR כי python3-qrcode חסר) = ‏9.6;
    #1192 (אישור עדכון בלי הקלדת שם) = ‏9.7;
    #1148/#1177 (מצב "לא ישים" ומסך MFA בלי otpauth גלוי) = ‏9.8;
    #1126/#854 (מצב כשל מפורש לסבב) = ‏9.9;
    #1128 (כפתור "הורד גיבוי הגדרות" בכרטיס הגרסה) = ‏10.0;
    #972 (מגירת האימג': מחיצות מהמניפסט, אימות שמור, סבבים ב-30 יום) = ‏10.1;
    #1039 (אובייקט הרשת: /sessions, חכירות dnsmasq, bind null) = ‏10.2;
    #1215 (אימג' במיקום לא זמין: "לא נבדק", לא "לא תואם") = ‏10.3;
    #989 (קצב הזרם ובלוקים ששודרו שוב בחדר המשכפלים) = ‏10.4;
    #1008 (כניסה אחרונה בהרשאות, דפדוף before ו-CSV ביומן) = ‏10.5;
    #980 (כיבוי כל המשכפלים, היסטוריית סבבי החדר, עמודת התיקייה בקליטות) = ‏10.6;
    #1227 (monitorToggle נגזר מ-enabled שחזר בתשובה, לא ממה שביקשו) = ‏10.7;
    #1179 (העץ של המשני = העץ של הראשי; "‹שם› לא ענה" בכל עמוד) = ‏10.8;
    #1235 (כונן בלי serial: "לא ניתן לזהות את הכונן בחריץ N" בכרטיס המשכפל) = ‏10.9;
    apple-quick-wins (‏:active, reduced-motion, toast/backdrop, טוקני תנועה, long-press עכבר) = ‏11.0;
    F06 (חלון צומח מהכפתור, מגירה 320ms) ו-F09 (קנה מידה טיפוגרפי ב-rem) = ‏11.1;
    visual-pass (toast בטוקנים + שגיאת רשת בעברית, חץ העץ ב-RTL, מגירה בלי גלישה, כפתור מחיקה אדום) = ‏11.2;
    apple-pending-4 (עמודת פעולות דביקה + ⋯, סימון עץ לפי לשונית, אייקוני עץ ייחודיים, כותרת סקירה, דשבורדים, חלונות, פאנל משימות) = ‏11.3.
    #1251 (קליטה: תיקיית יעד חובה) + #1252 = ‏11.4;
    #1253/#1254 (דיסק מכווץ בקליטה פעילה, התקדמות קליטה בדף האימג'ים) = ‏11.6
    (‏11.5 תפוס בענף מקביל של ביקורת הממשק);
    #1243 (בלי "זכור" במסך הכניסה הראשון) + איחוד עם #1253/#1254 = ‏11.7;
    ביקורת ממשק מלאה (אייקון תיקייה, כיווץ העץ, דיאלוג, סטטוס מ-/health…) = ‏11.8;
    שלב 1 של מוקאפ הקונסולה (טוקני ניגודיות, מסגרת חלונות אחידה, תרשים רשת, שורת משימות+סטטוס אחת) = ‏11.9;
    שלב 2 (ספריית אימג'ים, אחסון, סקירה, מחשבים, חשבון) = ‏11.10 — ההשוואה כאן במחרוזות, כי float('11.10') == 11.1.
    ‏#1262 (admin שנוצר בהתקנה מוגן) = ‏11.11.
    שדות סיסמה LTR + "הצג" אחד, ותיקוני הגואי הנייטיבי = ‏11.12;
    #1263 (כרטיס רשת בשלושה אופנים, חלון רשת הפצה, בלי קבצי dnsmasq) = ‏11.13; #1268/#1270/מתאים לדיסק/בלי זכירת MFA = ‏11.15.
    #1267 (פורטים לפי רשת, שירותים בבריאות) + #1264 (bond בהפצה) + #1269 (דרייברים לפי דיסק) + שלב 3 של המוקאפ = ‏11.16; בחירת דיסקים לסבב בקונסולה (#1275) = ‏11.17; יישור סופי למוקאפ + מוניטור מוטמע = ‏11.18; עץ כמו במוקאפ, TLS/שם מארח, #1273/#1274 = ‏11.19; פערי API (#977/#980), #1281 = ‏11.20; חיפוש גלובלי, זרימת הפצה, קצב לומד, שרת משני, שמות חריצים = ‏11.21.
    #1266 (SSH יצא מהמוצר — בלי מתג, בלי שורת 22, בלי טביעת מפתח) על gate-1011g = ‏11.22; התקדמות על פני גלים (#989) = ‏11.23; קצב אוטומטי = ‏11.24; חתימת גרסאות בבדיקת העדכון (#1277) = ‏11.25; סקירה/אימג'ים/מגירות/בריאות/הגדרות/משתמשים כמו המוקאפ (#1300) = ‏11.26; הגדרות/משתמשים/רשת אחד-לאחד + מוניטור מהמגירה (#1300, אחרי הדחיפה) = ‏11.27; פס האייקונים (#1300) = ‏11.29; הסקירה לפי הצעה ג' (#1300) = ‏11.27 בענף המקביל feat/1300-overview; איחוד gate-1013a (#1311/#1309/#1303/#1277/#1302/#1304/#1300) + לוגו ImageSrv בקונסולה (#1302 שלב ב׳) = ‏11.30; מסך העדכון (#1301) ובדיקת מהירות קישור (#432) = ‏11.31 בענפים מקבילים; שירותים לכל כרטיס רשת (#705) = ‏11.32; בדיקת הכתיבה לפני הגל, F1 כתום וסף CRC (#1366) = ‏11.34 (‏11.33 תפוס ב-#1357 המקביל); מינימום מקום פנוי אחרי שחזור (#1354) + איחוד gate-1014a = ‏11.35; gate-1014b (סבב חדש, מוניטור מנוטרל עם סיבה, #1417–#1422) = ‏11.36; תקרת קצב לפי כרטיס ההפצה (15/10) = ‏11.37; מהירות כרטיס לכל מכונה ותקרה לפי המשכפל האיטי (15/10 ב׳) = ‏11.38; קצב, ETA וזמן אימות בהעברה בין אתרים (#1462) = ‏11.40 (‏11.39 תפוס ב-#1463 המקביל); קצב הגל לפי מחשב השיכפול (#1471) והגבלת קצב בין אתרים (#1473) = ‏11.41 (‏11.39/11.40 תפוסים ב-#1463/#1462 המקבילים); קליטה מאמתת ברקע — מצב "מאמת" (#1470) = ‏11.39 בענף המקביל perf/capture-verify; כרטיס לעדכוני VM ומסך הרשת של ה-VM (#1468) = ‏11.39 בענף המקביל feat/vm-own-mac; איחוד integration/v0743-speed (#1469/#1482/#1476/#1489/#1490/#1498/#1486/#1479/#1481) = ‏11.42; אימג׳ שכלל המקום הפנוי אינו ניתן לבדיקה עליו מושבת בחלון הסבב (#1494) = ‏11.39 בענף המקביל fix/old-image-used-bytes; ערכת נושא אחת למשתמש, ברירת מחדל בהירה (#1499) = ‏11.39 בענף המקביל fix/gui-user-theme; דפי משכפלים/בנייה לפי המוקאפ ומיפוי חיבורי הדיסקים (#1485) = ‏11.39 בענף המקביל feat/sata-slot-mapping; פירוט קליטה חי בקונסולה (#1505) = ‏11.39 בענף המקביל feat/console-capture-detail; איחוד integration/v0743-speed סבב 2 (#1507/#1511/#1501/#1510/#1506/#1503/#1509/#1505) = ‏11.43; ביקורת לחיצות (#1016) — שגיאות בעברית, ביטול קליטה נגיש, מחיקת כיתה = ‏11.47 (‏11.46 שמור לענף הצימוד); stripTags גם ב-vmupdate.js וב-nicsvc.js (#1535) = ‏11.48; ברירת המחדל של "הגדר כרשת הפצה" אינה כרטיס הקונסולה (#1556) = ‏11.50; צבעי הדיסק (#1580: SMART תקול אדום עם אישור מפורש, נכשל בשיכפול הקודם כתום) = ‏11.50 בענף המקביל fix/smart-rescue-failed-last; ענף הניסיון rc2 (integration/v0760-rc1) = ‏11.52; מסך הסבב בקונסולה (#1376) = ‏11.53, עצת Fast Startup (#1504) = ‏11.54 ושלב "packages" במסך העדכון (#1411) = ‏11.55 בענפים מקבילים; ענף הניסיון rc4 = ‏11.56; ענף הניסיון rc5 (כולל "בטל דיסק זה", #1386) = ‏11.57; תיקוני rc5 (סיבה "המגירה נשלפה", כותרת קומפקטית לחדר ההפצה) = ‏11.58; הטקסט של שני כרטיסי ההפצה לפי המדידה (#1264, 17/10) = ‏11.57 בענף המקביל fix/1264-alb-measured; ענף הניסיון rc6 (עמוד ההגדרות #1689, מוניטור #1686/#1698, #1264, ImageSrv #1716, קוד מת #1713) = ‏11.59, ‏monitor.js = ‏3.35; שורת הדרייברים לפי המוקאפ (#1331) = ‏11.57 בענף המקביל feat/1331-drivers-mockup-v2, ועל rc6 (feat/1331-drivers-on-rc6) = ‏11.60; שורת הדרייברים של יעד לינוקס (drivers.linux, #1331 T8) = ‏11.61; דיסק אדום לעולם אינו יעד (#1732) באיחוד על rc6 = ‏11.62; רזרבת מקום למערכת בכרטיס האחסון (#1737) באיחוד על rc6 = ‏11.63; דיסק לקריאה בלבד אדום (#1742) = ‏11.64; אייקון ל"סניפים" בעץ, רישום משני לפי IP ו"+ קבוצה חדשה…" ברישום (נדב 18/10) = ‏11.65; חלון שנפתח מתוך onSubmit אינו נסגר על ידי הקודם (שלב 2 של הרישום, rc6f) = ‏11.66; העץ בכניסה הראשונה (מלאי/תשתית/ניהול מקופלים) והזחת העץ של כל משני כמו TA (נדב 18/10) = ‏11.67; מתג "חיוב אימות דו-שלבי למנהלים" וביטול MFA עצמי (נדב 18/10) = ‏11.68; ניתוק אוטומטי: מתג + דקות (נדב 18/10) = ‏11.69; ביטול MFA עצמי עם סיסמה ומוגבל (סקירת אבטחה) = ‏11.70; "שרתי משנה" במקום "סניפים" ובלי כרטיס בסקירה (נדב 18/10) = ‏11.71; "שרת משנה" גם ביחיד (נדב 18/10) = ‏11.72; איפוס אימות דו-שלבי למשתמש אחר ו"דרוש אימות דו-שלבי" (נדב 18/10) = ‏11.73; העץ ההתחלתי ו"סקירה כללית" אחרי כל כניסה (נדב 18/10) = ‏11.74; נקודת מצב אחת לכל שרת בעץ (TA ושרתי משנה) מבדיקות /health, בלי הקופסה התחתונה (נדב 18/10) = ‏11.75; בלי מידע על דיסקים ב"דורש טיפול" של הסקירה (נדב 18/10) = ‏11.76; נקודת שרת משנה אינה נשארת אפורה כשהדגימה מודדת את TA (מרוץ, TA 18/10) = ‏11.77; המוניטור: קלט רק אחרי לחיצת היד, ניסיון חוזר גלוי, ושאלות השיכפול המקומי בעברית (#1766, rc6, 18/10) = ‏11.74 בענף fix/monitor-security-type, ובאיחוד על rc6 = ‏11.78, ‏monitor.js = ‏3.36; סבב הפצה לפי המוקאפ (#1769) באיחוד על rc6 = ‏11.79; עדכון שרת משנה מהלשונית שלו בעץ (#1776) = ‏11.80; "חזור ל-…" בלי שם שרת בגוף (#1778) = ‏11.80 בענף המקביל; איחוד rc7-cand (#1776/#1778/#1697) = ‏11.81; ועם #1761 (קצב לפי goodput) = ‏11.82; ענף המעבדה lab/rc7-nologin עם #1795/#1797 = ‏11.84; פס הסטטוס של המוניטור בשורה אחת (#1837) — ‏monitor.js = ‏3.37; ענף המעבדה עם #1805 (קריאה חוזרת, "לא אומת") = ‏11.85; ועם #1867 (מוניטור #1849/#1850/#1852/#1854) = ‏11.86; ועם #1865/#1869 (עברית לסיבות, SMART מלא) = ‏11.87; ועם #1864 (מדגם/מלא) = ‏11.88; ועם #1876 (סימן "כתיבה לא הושלמה", #1798) ו-#1866 (קצב לפי מספר המגירות, #1842) = ‏11.89; זמן סיום צפוי לפי היסטוריה (#1843) = ‏11.90; "כנראה כבל" / "הדיסק" לפי חיבור (#1844) = ‏11.91; "איטי מהרגיל לדגם" (#1845) = ‏11.92; הלוג של המכונה במגירת המכונה (#1882) = ‏11.93; ציר זמן לשלבי השחזור לכל מגירה (#1883) = ‏11.94; מעבד/throttle/טמפ' בגל במגירת המכונה (#1885) = ‏11.95; DHCP/PXE מאז ההערה במגירת המכונה ובחסרים בגל (#1887) = ‏11.96; היסטוריית רשת ו-IP במגירת המכונה (#1888) = ‏11.97; כמה זמן הסבב חיכה לתשובות SMART בהיסטוריית הסבבים (#1889) = ‏11.98; בענף המקביל fix/1937-usb-field: הורדת הדיסק-און-קי לשיכפול בהגדרות (#1914) = ‏11.90; טבלאות SMART והחומרה (#1921) = ‏11.91 (index.html בלבד — monitor.html ומבחן זה נשארו על 11.90); הוראות הצריבה Rufus במצב DD (#1937) = ‏11.92; האיחוד על ענף המעבדה (lab/int-1021b) = ‏12.00; ועם 30ff9cf6 של feat/1921-smart-tables (סף CRC 500, ICRC לטבלת החומרה; שם 11.92) = ‏12.01; ועם #1936/#1928 (PR #1938), #1933/#1932 (PR #1939), #1934 (PR #1942), QA עיקרון 5 (#1945) ו-QA-G43 (#1944) — lab/int-1021c = ‏12.02; ועם PR #1750/#1834/#1838/#1836/#1835/#1774 (‏#1746/#1803/#1806/#1812/#1826/#1773) — lab/int-1022-prs = ‏12.10; ועם PR #1953/#1961/#1998/#1958/#1965/#1986/#1982/#1984/#1985/#1990/#1992/#1995/#2002/#1996/#1999/#2003/#2004/#2005/#2007 — lab/int-1022 = ‏12.30 (‏12.03–12.29 בענפים המקבילים); ועם PR #2012 (#2001, מיפוי החיבורים; שם 12.40) — lab/int-1022 סבב שני = ‏12.41; ועם PR #2027/#2029/#2036 (#1662, #2024, משכפל מוחזק "דלוק — מחכה ללחיצה" וכפתור "כבה" #2031; שם 12.11) — lab/int-1022 סבב רביעי = ‏12.42; ועם PR #2063 (הערת "הושלם עם אזהרה" של מגירה בעמוד החדר, #2042, כתום בלי שאלה; שם 12.42 בענף feat/2042-orange-no-ask-round) — lab/int-1022 סבב רביעי = ‏12.43; ועם PR #2070 (מגירת ההתראות כשבדיקת הבריאות לא נקראה, #2060; שם 12.44) — lab/int-1022 סבב חמישי = ‏12.45; ועם #2078 (SMART שלא נקרא אדום, "SMART תקול") — lab/int-1022 סבב שישי = ‏12.46; ‏12.47 שמור; הערת "אדום" בחדר לפי הסיבה (#1767, fix/iso-small-bugs-1024) = ‏12.48; ‏12.49 (#2012 round 7); ‏12.50 שמור לסוכן המקביל; הגדרות זמני ההמתנה והכיבוי האוטומטי (#2056/#2059, מוקאפ 05, feat/mockups-web-1024) = ‏12.51; איחוד סבב 15 (lab/int-1030-r15: סבב 14 על ‏12.50 + PR #2145 על ‏12.51) = ‏12.52.
    שוויון על כל ה-includes — bump חלקי הוא הבאג."""

    page = _index()
    versions = set(re.findall(r'\?v=(\d+\.\d+)"', page))
    assert versions == {"12.52"}, versions
    # ביקורת 10/10: monitor.html מושך את אותם console.css ו-design-tokens.css — הם נשארו על 3.29 לנצח,
    # ולכן דפדפן שזכר אותם הציג את המוניטור בעיצוב ישן. monitor.js לבדו שומר על גרסה משלו (test_monitor).
    mon = (STATIC / "monitor.html").read_text(encoding="utf-8")
    shared = set(re.findall(r'(?:design-tokens|console)\.css\?v=(\d+\.\d+)"', mon))
    assert shared == {"12.52"}, shared
    assert 'monitor.js?v=3.40' in mon


def test_update_apply_and_revert_are_red_button_sheets_without_a_name():
    """‏#1192: apply הוא אישור הפיך, ולכן אין בו input/verify. ‏#1778 (הכרעת
    #1270): גם "חזור ל-…" — חלון אישור עם כפתור אדום, בלי הקלדה ובלי שם שרת
    בגוף הבקשה. מחיקה ועצירת סבב נשארות עם שם היעד שלהן."""
    js = _console_js()
    apply = js[js.index("function confirmUpdateApply("):js.index("function confirmUpdateRevert(")]
    assert "sheet({" in apply
    assert "verify:" not in apply and "<input" not in apply
    assert "prompt(" not in apply and "confirm(" not in apply
    # רשימת השלכות (impactList) — ‏#1301: שלוש ההשלכות של החלון שאושר 13/10 (S338)
    assert "השרת יופעל מחדש. הקונסולה תראה את שלבי העדכון" in apply and "יעלה מהדיסק המקומי" in apply
    assert 'await post("/update/apply", { tag })' in apply
    assert 'status.state === "running"' in js

    revert = js[js.index("function confirmUpdateRevert("):js.index("function openNewUser(")]
    assert "sheet({" in revert and "danger: true" in revert
    assert "verify:" not in revert and "mustEqual" not in revert and "<input" not in revert, "#1270: no typed word"
    assert "prompt(" not in revert and "confirm(" not in revert
    assert 'await post("/update/revert");' in revert, "#1778: no body — the server checks no name"
    assert "confirm_name" not in revert and "server_name" not in revert and "hostname" not in revert

    delete = js[js.index("function deleteImage("):js.index("function openImageIngest(")]
    stop = js[js.index("function stopRound("):js.index("/* ---------- #954 גל 3: מחשבים")]
    assert "sheet({" in delete and "danger: true" in delete and "confirm_name: img.name" in delete and "<input" not in delete
    assert "sheet({" in stop and "danger: true" in stop and "sessionImage(session)" in stop and "confirm_name: name" in stop and "<input" not in stop


def test_mfa_setup_screen_names_the_missing_qr_package():
    """‏#1150: כש-`svg` ריק (python3-qrcode חסר בשרת) מסך ההגדרה מציג הודעה
    אדומה שאומרת זאת בשם — לא מסתיר את המקום בשקט. ה-QR עצמו נכנס inline
    ל-`.qr` (ה-CSS ‏`#login .qr svg` שולט בגודל)."""
    js = _console_js()
    fn = js[js.index("function loginSetupHtml("):js.index("function loginCodesHtml(")]
    assert "state.svg" in fn
    assert "אין QR — החבילה python3-qrcode חסרה בשרת; הקלד את הסוד ידנית" in fn
    assert 'role="alert"' in fn


def test_mfa_setup_hides_the_manual_secret_and_never_renders_otpauth_url():
    js = _console_js()
    fn = js[js.index("function loginSetupHtml("):js.index("function loginCodesHtml(")]
    assert "otpauth://" not in fn and "state.otpauth" not in fn
    assert "ImageSrv:${state.username" in fn       # #1302: כמו ה-issuer ב-server/totp.py
    assert 'id="setup-secret"' in fn and 'type="password"' in fn
    assert 'loginEye("setup-secret")' in fn


def test_old_images_page_code_is_gone():
    """הדף הישן נמחק, לא הוסתר: כרטיסי "כללי בטיחות"/"פעולות מהירות" עם
    אפסים קבועים, עמודות גרסה/SHA-256/סטטוס שאין להן שדה ב-`/images`,
    כפתורי Up/Down/Edit באנגלית, ו-`library.js` המת שהיה מוער ב-index.html."""
    js = _console_js()
    for gone in ("כללי בטיחות", "פעולות מהירות", "מטא־נתונים", "checksum שגוי",
                 "function filterImagesFolder", "function reorderImage(", "function reorderFolder(",
                 'aria-label="Move folder up"', "<th>SHA-256</th>", "<th>גרסה</th>"):
        assert gone not in js, gone
    assert "library.js" not in _index()
    assert not (STATIC / "library.js").exists()


def test_images_page_is_the_datastore_browser_from_the_api_only():
    """עץ תיקיות + טבלה אחת + מגירה, מה-API הקיים: "נכנס לדיסק מ-" מ-`min_target_bytes`
    (‏#953, ‏null = לא ידוע), "בשימוש" מ-`/overview`, קליטה כשורה בטבלה, ומה שאין
    לו API ("טבלת המחיצות") מוצג כ"דורש API" — לא מומצא."""
    js = _console_js()
    for needed in ("function images(tab = 0)", "own: true", "function imagesTree()", "role=\"tree\"",
                   "min_target_bytes", "לא ידוע", "function captureRowHtml(", "function imageDrawerHtml(",
                   "דורש API", "function bulkDelete()", "confirm_name", "function imgDrop(", "/folders/order"):
        assert needed in js, needed
    assert "images: [images, emptyDataCard]" not in js


# ---------- #954 גל 3: מחשבים ----------

def test_machines_page_is_one_grouped_table_from_the_api_only():
    """טבלה אחת מקובצת (בנייה, שיכפול, ואז קבוצה לכל כיתה — תיקון נדב 17/09),
    מגירה, הכיתה כאובייקט; מה-API הקיים בלבד. "דיסק N · SATA N-1" ולעולם לא
    sd*; מה שאין לו API (היסטוריית סבבים לכיתה) מוצג כ"דורש API" — לא כפתור
    מנוטרל ולא נתון מומצא. ‏"אתחול מרחוק" ו"עריכת MAC" קיבלו API ב-#977."""
    js = _console_js()
    for needed in ("function machines(tab = 0)", "function groupRowHtml(", "function machineRowHtml(", "function groupPage(",
                   "function machineDrawerHtml(", "function seenDevicesCard()", "function openAddMachine(", "dry_run: true",
                   "rebootMachine('${macEnc}')", "editMachineMac('${macEnc}')", "דורש API", "SATA ${n - 1}", "function connText("):
        assert needed in js, needed
    for gone in ('UI.soon("אתחול מרחוק")', 'UI.soon("עריכת MAC")'):
        assert gone not in js, gone
    block = js[js.index("/* ---------- #954 גל 3"):js.index("function healthStatusClass(")]
    # ‏#1300 (נדב 12/10): "פתח במוניטור" במגירה מנוטרל רק כשאין למה להתחבר — עם הסיבה (title + שורה), לא "דורש API"
    mon = '<button class="btn primary" disabled title="${esc(block)}">פתח במוניטור</button>'
    assert mon in block
    assert "disabled" not in block.replace(mon, ""), "כפתור מנוטרל במקום 'דורש API' (README §8)"
    assert "sda" not in block and "sdb" not in block, "שם sd* בדף המחשבים"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .dg tr.group td .grp{", ".page .disks{", ".page .disk.err{", ".page .dg-bar select{"):
        assert sel in css, sel


# ---------- #954 גל 3א: מחשבי שיכפול ומחשבי בנייה כאובייקטים ----------

def test_cloners_and_builders_objects_are_built_on_group_page_from_the_api_only():
    """הקבוצות הקבועות כאובייקטים על `groupPage` של גל 3 (לא מערכת חדשה):
    גריד חריצים "דיסק N · SATA N-1" (לעולם לא sd*), צבע SMART ירוק רק על
    `ok`, אדום מזיכרון הכשלים עם "נקה", מגירות מ-`/room` בסבב ומ-`/machines`
    בלעדיו; מחשבי בנייה — קליטה בתהליך מ-`/tasks` עם ביטול מאחורי הקלדת שם.
    ‏#980: כיבוי כולם (‏`POST /room/poweroff`), היסטוריית הסבבים
    (‏`GET /room/history`), שלבי הקליטה (‏`/tasks.stage`) ומצב הדיסק לפני
    קליטה (‏`/machines.last_capture` + ‏`probe.ntfs`) — מה-API, בלי "דורש API"."""
    js = _console_js()
    # ‏#1485: הדפים לפי console-full.html (‏P.cloners / P.build) — כרטיס .mach עם שורות "דיסק N"
    # לפי מיפוי החיבורים (‏drawer_slots), כרטיס בנייה עם kv, ולשונית "דיסקים" שפותחת את חלון "מיפוי חיבורים" (#2001).
    for needed in ("function clonersView(", "function buildersView(", "function machineSlots(", "function slotClass(",
                   "function slotHtml(", "function capturesTableCard(", "function cancelCaptureVerified(",
                   "function refreshGroupLive(", 'api("/room")', "drawer_list",
                   'post("/room/poweroff", { confirm_name: g.label })',
                   'api("/room/history")', "function captureStagesHtml(", "function beforeCaptureHtml(",
                   'return "SMART לא נבדק"', "function driveRows(", "function diskMapHtml(", "function clonerMachHtml(",
                   "function builderCardHtml(", "function openSlotMap(", "/slot-map`", "(לא ממופה)"):
        assert needed in js, needed
    assert "function captureNowCard(" not in js and "function editDrawerCount(" not in js, \
        "#1485: the capture is on the build card; the count dialog moved into the disks tab"
    block = js[js.index("/* ---------- #954 גל 3א"):js.index("/* ---------- לשונית \"נראו ברשת\"")]
    assert "דורש API" not in block[block.index("function captureStagesHtml("):block.index("function buildersCard(")], \
        "#980 §4/§6: שלבים ו'לפני קליטה' מגיעים מה-API"
    assert "disabled" not in block, "כפתור מנוטרל במקום 'דורש API' (README §8)"
    assert "sda" not in block and "sdb" not in block, "שם sd* בקוד המשכפלים/הבנייה"
    assert 'd.smart === "ok"' in block and 'return ""' in block, "'לא נבדק' חייב להישאר אפור"
    assert "function groupPage(g, tab = 0)" in js and js.count("function groupPage(") == 1, "אובייקט אחד לכל הקבוצות"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .mgrid{", ".page .slots{", ".page .disk.run{", ".page .disk.empty{", ".page .legend i.sw-err{",   # the deploy room
                ".page .rgrid{", ".page .mach{", ".page .mach .dsk{", ".page .grid2{", ".page dl.kvd{", ".n-tbl{", ".win .n-seg{"):
        assert sel in css, sel


def test_deploy_page_is_the_cloners_room_from_the_api_only():
    """‏#954 גל 4: דף ההפצה הוא חדר המשכפלים (deploy.md) — על אותו גריד של
    גל 3א (`clonerCardHtml` במצב חדר, `machineSlots`, `slotHtml`), לא עותק.
    כתיבה רק ל-endpoints הקיימים (`POST /room`, `/room/start`, `/room/wake`,
    `/room/close` מאחורי הקלדת שם); סבב חדש inline (לא sheet); ההכרעה על
    SMART (כתום ממשיך, אדום מדלג) גלויה בדף; קצב הזרם ובלוקים ששודרו שוב —
    מ-`/room.round` (#989), ‏null = "לא נמדד"; מה שאין לו API — היסטוריה,
    דילוג מרחוק, תשובה מהקונסולה — "דורש API" בטקסט. הדף הישן
    (סבבים / הצטרפות חיה, בקרת סבב, המגירה) איננו.
    ‏18/10 (נדב: "לא לפי המוקאפ"): הלשונית הראשונה אחד-לאחד לפי
    docs/design/apple-mockups/console-full.html (P.deploy) — שלושה אריחים, כרטיס
    "חדר המשכפלים" וכרטיס לכל משכפל (`roomMachHtml`); בלי סבב — מצב ריק. בורר מצב-הדוגמה
    ומתג השינויים של המוקאפ אינם במוצר, וגם "תצוגה גדולה" (אינה במוקאפ)."""
    js = _console_js()
    for needed in ("function deploy(tab = 0)", "function loadDeploy(", "function roomKpis(r, pct)", "function roomGridCard(kids)", "function roomNotes(",
                   "function roomMachHtml(", "function roundDskHtml(",
                   "function roomNewWindow(", "function stopRoom(", "function startWave(", "function imageFitReason(", "function deployClassView(",
                   'post("/room", body)', 'post("/room/start")', 'post("/room/close", { confirm_name: name })',
                   'title: "חדר המשכפלים"', "כשל במחשב אחד לא עוצר את השאר", "עצירת הסבב…", "לא מחובר — לא הצטרף לסבב",
                   "בלי תשובה הסוכן ממשיך לכתוב", "בלי תשובה הסוכן מדלג", "r.throughput_bps != null", "r.loss_blocks != null", "דורש API",
                   'deploy: { crumb: "סבב הפצה", title: "סבב הפצה", tabs: deployTabs(), render: deploy, load: loadDeploy, own: true }'):
        assert needed in js, needed
    block = js[js.index("/* ---------- #954 גל 4"):js.index("/* ---------- ספריית אימג'ים (#954 גל 2)")]
    assert "sda" not in block and "sdb" not in block, "שם sd* בקוד החדר"
    assert 'disabled>' not in block.replace('${reason ? " disabled" : ""}', ""), "כפתור מנוטרל במקום 'דורש API' (README §8) — רק אפשרות אימג' שלא נכנסת (#953) מושבתת"
    assert "דלג בשניהם" not in block
    assert "prompt()" not in block and "confirm(" not in block, "prompt/confirm חסומים — רק sheet()"
    for gone in ("הצטרפות חיה", "בקרת סבב", "async function openRoundDetail", "startSelectedRound", "openNewDeployment", "roundDrawerOpen", "deploy: [deploy, emptyDataCard]",
                 "function toggleRoomBig(", "DEPLOY.big", "מצב לדוגמה", "הצג מה השתנה", "hint-demo", "data-round="):
        assert gone not in js, gone
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .rnew{", ".page .rnotes{", ".page .rnew .radios{"):
        assert sel in css, sel


def test_failed_rounds_show_the_reason_and_offer_close_or_reopen():
    js = _console_js()
    for needed in (
        'return `נכשל: ${session.failed_reason || "סיבה לא ידועה"}`',
        'UI.pill("err", `נכשל: ${r.failed_reason || "סיבה לא ידועה"}`)',
        'onclick="reopenRound()">פתח מחדש',
        'onclick="reopenRoom()">פתח מחדש',
        "function reopenRound()",
        "function reopenRoom()",
        'onclick="stopRound()"',
        'onclick="stopRoom()"',
    ):
        assert needed in js, needed


def test_health_and_ports_pages_are_tables_from_the_api_and_ports_have_no_switches():
    """‏#954 גל 5: בריאות = טבלת בדיקות (חמישה מצבים, "לא נבדק" אינו ירוק,
    לולאות אתחול מקובצות) + כרטיס גרסה ועדכון (הקלדת שם השרת) — בלי מתגים
    ובלי הלשוניות הזהות "סקירה/שירותים/בדיקות" (#829 M5).
    ‏#1267 (נדב 10/10): פורטים = כרטיס לכל רשת (הפצה / ניהול / בין שרתים),
    חומת אש רק בין רשתות, ו**אין "פתח/סגור פורט"** — רק "בדוק עכשיו" ו"הצג
    פרטים". המתגים של #996/#1015 יצאו מהדף, ומתג המוניטור בדף המוניטור.
    ‏#1266: SSH יצא מהמוצר — אין מתג SSH לשרת בחיבורים פיזיים ואין /ssh."""
    js = _console_js()
    for needed in ("function health()", "function healthRows(", "function updateActionsHtml(", "async function loadHealthUpdate(",
                   'unknown: ["unk", "לא נבדק"]', 'off: ["", "כבוי"]', '["agent_loop:", "לולאות אתחול", "agent_loops"]',
                   "function ports()", "function portCard(", "function portDetail(", "async function portCheckNow(",
                   '"מה לפתוח בחומת האש של המכללה"', 'const PORT_FW = { open: ["ok", "פתוח"], blocked: ["err", "חסום"] }',
                   "nicBody(n, { enabled: false, proxy: false })",
                   'onSubmit: () => send({ confirm: "imagectl.monitor" })',
                   'health: { crumb: "בריאות ושירותים", title: "בריאות ושירותים", tabs: [], render: health, load: loadHealth, own: true }',
                   'render: ports, load: loadPorts, own: true }'):
        assert needed in js, needed
    for gone in ('id="ssh-body"', "function loadSsh(", "function sshLight(", "SSH_LIGHT", 'title="בקרוב (נדרש endpoint)"',
                 'tabs: ["סקירה", "שירותים", "בדיקות"]', "health: [health,", "ports: [ports]",
                 "function portRows(", "function portServerToggle(", "function portToggleUrl(", "havePortsDhcp", "PORTS_MONITOR",
                 "function portSwitchHtml(", "function portSshNic(", "function sshToggle(", "SSH_STATE", 'api("/ssh")'):
        assert gone not in js, gone
    block = js[js.index("/* ---------- #1267: רשת › פורטים"):js.index("/* ---------- #954 גל 6: מוניטור — הרשימה")]   # גל 6: המוניטור אחרי הפורטים
    assert 'role="switch"' not in block[block.index("const PORT_NETWORKS"):], "אין מתג בדף הפורטים (#1267)"
    assert "disabled" not in block, "כפתור מנוטרל במקום 'דורש API' (README §8)"
    assert "prompt(" not in block and "confirm(" not in block.replace("confirmSheet(", ""), "prompt/confirm חסומים — רק sheet()"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .st.unk::before{", ".page .sw{", ".page .sw.unk{", ".page dl.mkv{", ".page label.msw{"):   # ‏#1300: ‏.upd → dl.kv/label.sw של המוקאפ
        assert sel in css, sel


def test_small_pages_are_own_tables_from_the_api_and_wol_is_per_machine():
    """‏#954 גל 6: מוניטור (רשימה), דרייברים, הגדרות, הרשאות ויומן נבנו מחדש
    כעמודי `own` — טבלה אחת (datagrid) לכל אחד, מתגים `role="switch"`, הקלדת
    שם למחיקת משתמש/חבילה, "לא נקרא" כמצב משלו — והדפים הישנים (‏"Settings"
    באנגלית, `usersAdminPage` + `permissions` כפולים, "אירועים / Audit",
    כרטיס-לכל-מכונה במוניטור) נמחקו. ‏#984: WoL למחשב בודד — `UI.soon("WoL")`
    ו"אין WoL למחשב יחיד" הוחלפו ב-`wakeMachine` / `wakeGroup`; "נשלח" ≠
    "התעוררה". מתג זהות המכונה (#855) נשאר בהגדרות."""
    js, drivers, page = _console_js(), (STATIC / "drivers.js").read_text(encoding="utf-8"), _index()
    for needed in ("function monitorPage()", "function monitorRowHtml(", "function settings()", "function settingSwitchHtml(", "function settingsValueSheet(", "function settingsSwitch(",
                   "function permissions()", "const ROLE_MATRIX = [", "function userDeleteSheet(", "function logs()", "function logBarHtml(", "function logMore()",
                   "async function wakeMachine(", "async function wakeGroup(", "function wolResultText(",
                   'key: "identity_check"', "WoL נשלח ל-", 'consoleUrl("/journal" + logQuery(last))', 'X-Journal-Search-Truncated',
                   'settings: { crumb: "הגדרות", title: "הגדרות", tabs: [], render: settings, load: loadSettingsData, own: true }',
                   'permissions: { crumb: "משתמשים והרשאות", title: "משתמשים והרשאות", tabs: [], render: permissions, load: loadUsersData, own: true }',
                   'logs: { crumb: "יומן", title: "יומן", tabs: [], render: logs, load: loadJournalData, own: true }',
                   'monitor: { crumb: "מוניטור", title: "מוניטור", tabs: [], render: monitorPage, load: loadMonitor, own: true }',
                   'tabs: ["דגמים", "חבילות", "כיסוי לפי מכונה"], render: (i) => driversPage(i), load: () => loadDrivers(), own: true'):
        assert needed in js, needed
    for gone in ("function usersAdminPage(", "function journalPage(", "function settingsPage(", "function loadUsersAdmin(", "function openRolesDrawer(",
                 "function openLogFilter(", "JOURNAL_FILTER_FIELDS", "function loadUpdateInfo(", "function checkForUpdate(", 'title:"Settings"',
                 'tabs: ["אירועים", "Audit"]', 'tabs: ["משתמשים", "תפקידים"]', 'tabs: ["מכונות"]', "detail-grid\"><div class=\"detail-box\"><span class=\"k\">MAC",
                 "function wakeMachine() { soon(); }", 'UI.soon("WoL")', "אין WoL למחשב יחיד", 'UI.soon("Wake-on-LAN")', 'UI.soon("הער את כולם (WoL)")',
                 "permissions: [usersAdminPage, permissions]", "logs: [journalPage, logs]", "settings: [settingsPage]", "monitor: [monitorPage]"):
        assert gone not in js, gone
    for needed in ("function driversPage(tab = 0)", "function driversCoverageCard()", "function driverRuleHits(", "function openDriverDetail(", "tools/drivers/build-package.py"):
        assert needed in drivers, needed
    assert 'class="table"' not in drivers, "הטבלה הישנה"
    assert "<span>הגדרות</span>" in page and "<span>Settings</span>" not in page
    assert "<span>יומן</span>" in page and "יומן / Audit" not in page
    block = js[js.index("/* ---------- #954 גל 6: הרשאות"):js.index("/* ---------- #954 גל 8: רשת כאובייקט")]   # גל 8 החליף את "כרטיסי רשת / רשת הפצה"
    assert "prompt(" not in block and "confirm(" not in block.replace("confirmSheet(", ""), "prompt/confirm חסומים — רק sheet()"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page dl.mkv dt,.page dl.mkv dd{", ".page label.msw.changed{"):   # ‏#1300: dl.kv / label.sw של המוקאפ
        assert sel in css, sel


def test_the_class_build_and_cloner_tree_nodes_open_a_role_focused_machines_page():
    """נדב 17/09 (עם צילום): לשלושת צומתי האב בעץ — כיתות / מחשבי בנייה / מחשבי
    שיכפול — "אמור להיות חלון כמו של המחשבים, אבל רק של כיתות / שיכפול / בנייה,
    בלי לשונית נראו ברשת". קליק = הדף הממוקד (selectMachinesRole), דאבל =
    פותח/סוגר את הענף. הקבוצות שמתחתיהם (grp_BUILD וכו') נשארות אובייקטים."""
    page = _index()
    # ‏#1300 (נדב 12/10, "כמו המוקאפ"): ב-console-full.html "מחשבי בנייה" / "מחשבי שיכפול" הם עלים —
    # קליק = הדף הממוקד, העלה עצמו מודגש; בלי חץ ובלי תת-עץ קבוצות/מחשבים
    for role, box in (("classroom", "classesTree"), ("build", "buildTree"), ("cloner", "clonerTree")):
        assert f"data-role=\"{role}\" onclick=\"selectMachinesRole('{role}',this)\"" in page, role
        assert f'id="{box}"' not in page, box
    js = (STATIC / "console.js").read_text(encoding="utf-8")
    assert "function selectMachinesRole(role, el)" in js
    assert 'if (MACHINES_ROLE) return [ROLE_PAGE_HE[MACHINES_ROLE], red];' in js
def test_network_is_one_object_page_with_the_diagram_nics_and_deploy_tabs():
    """‏#954 גל 8: "רשת" בעץ הוא דף (קליק = הדף, דאבל = פותח/סוגר), ושלוש
    הלשוניות שנבנו לפי `network.md` / `network-nics.md` / `network-deploy.md`
    (תרשים SVG inline, טבלת כרטיסים בפועל/מוגדר/פער, רשת הפצה — מה מחולק בפועל
    מול השמור) + "פורטים" = הדף הקיים של גל 5, שלא נגעו בו מלבד רצועת הלשוניות.
    הדפים הישנים `nic`/`netdeploy`/`network` (כרטיס-לכל-NIC, `object-strip`,
    "פורטיים" קבועים, 10.44.12.0/24 קשיח) נמחקו. ‏#1263: כרטיס = חלון "הגדרת
    כרטיס" (netcfg.js), רשת ההפצה = חלון משלה (net.js). ‏#1032: תא הפעולות שומר מקום."""
    js, page, css = _console_js(), _index(), (STATIC / "console.css").read_text(encoding="utf-8")
    # העץ: הצומת "רשת" מנווט לדף; הילדים פותחים את האובייקט בלשונית; "פורטים" כמו שהיה
    assert ("""<div class="inventory-node" data-page="network" onclick="treeNavToggle(this,'netTA',"""
            """function(){selectPageById('network')})\"""") in page
    # ‏#1300: "חיבורים פיזיים" הוא עלה כמו ב-console-full.html — בלי תת-עץ כרטיסים
    assert 'data-net-tab="1" onclick="openNetwork(1,null,this)"' in page and 'id="nicNode"' not in page and 'id="nicTree"' not in page
    assert 'data-net-tab="2" onclick="openNetwork(2,null,this)"' in page
    assert """<div class="inventory-node" data-page="ports" onclick="selectPageById('ports')\"""" in page
    for gone in ('data-page="nic"', 'data-page="netdeploy"', "NIC_HIGHLIGHT"):
        assert gone not in page, gone
    for needed in ("function networkPage(", "function loadNetwork(", "function netDiagramModel(", "function netMapSvg(", "function netLaneOpen(", "function netLaneDrawerHtml(", "function networkDiagramTab(",
                   "function networkNicsTab(", "function networkDeployTab(", "function netServicesOn(", "function netGapStatus(", "function netProbe(", "function openNetwork(",
                   'network: { crumb: "רשת", title: "רשת", tabs: NET_TABS, render: (i) => networkPage(i), load: loadNetwork, own: true }',
                   'const NET_TABS = ["תרשים", "חיבורים פיזיים", "רשת הפצה", "פורטים"];',
                   'width="${W}" height="340" viewBox="0 0 ${W} 340"',   # gotcha: SVG עם height מפורש
                   "מה מותר: דורש API", 'title: "כיתות — v2"', "`/net/interfaces/${encodeId(name)}/probe`",
                   "editNicConfig(n.name)", "editDeployNet(n.name)", "routeDeleteSheet(", 'cls: "stable",'):
        assert needed in js, needed
    for gone in ("function renderNicCard(", "function netBannerHtml(", "function nic()", "function netdeploy()", "function network()", "function saveNetwork(",
                 "function loadNetcfgData(", "function loadNetPages(", "function editDeployNic(", "function nicUnion(", "10.44.12.0/24", "פורטיים",
                 'netdeploy: [netdeploy]', 'nic: [nic]', "selectPageById('nic')", "selectPageById('netdeploy')",
                 # ‏#1263: "קבצי dnsmasq" ו"הקובץ של הכרטיס" הוסרו מהקונסולה
                 "previewDnsmasq", "netShowFile", "קבצי dnsmasq", "editNic(", "editAddress("):
        assert gone not in js, gone
    block = js[js.index("/* ---------- #954 גל 8: רשת כאובייקט"):js.index("function healthCheckById(id) {")]
    assert "prompt(" not in block and "confirm(" not in block.replace("confirmSheet(", ""), "prompt/confirm חסומים — רק sheet()"
    assert "disabled" not in block.replace("disabled_at", ""), "כפתור מנוטרל במקום 'דורש API' (README §8)"
    assert "function routeDeleteSheet(" in (STATIC / "netcfg.js").read_text(encoding="utf-8")
    # ‏#1032: תא הפעולות שומר מקום (visibility), לא display:none
    assert ".page .dg.stable td .acts{display:flex;visibility:hidden" in css
    for sel in (".page .netmap-svg{", ".page .netmap-svg .bad.nm-wire{stroke:var(--clr-danger);stroke-dasharray:5 4}", ".page .netmap-svg .bad.nm-box{stroke:var(--clr-danger)", ".page .kv3{"):
        assert sel in css, sel


# ---------- #649 שלב 1: ארגז כלים ----------

def test_tools_page_is_an_admin_tree_node_with_two_selections_per_tool():
    """‏#649 שלב 1 (הכרעת נדב 17/09): צומת "ארגז כלים" תחת "תשתית" (admin בלבד,
    אחרי "דרייברים"), עמוד `own` שבו קבוצה = כרטיס וטבלה עם ☐ בנייה/שיכפול
    **ו-☐ תלמיד** לכל כלי (תלמיד = v2, הבחירה נשמרת כבר עכשיו); סיכון = pill
    (ro אפור / rw כתום / destroy אדום עם "הקלדת שם"); גודל = ארוז ירוק / מספר /
    "לא נמדד" (לא 0 — עיקרון 5); שמירה ב-`PUT /tools/selection` נדלקת רק כשיש
    שינוי; בלי .acts בשורות (#1032: שום דבר לא זז ב-hover)."""
    page, js = _index(), _console_js()
    infra = page[page.index('id="infraTA"'):page.index('id="adminTA"')]
    node = infra[infra.index('data-page="tools"') - 40:infra.index('data-page="tools"') + 200]
    assert 'data-admin' in node and "selectPageById('tools')" in node
    assert infra.index('data-page="drivers"') < infra.index('data-page="tools"'), "אחרי דרייברים"
    assert "<span>ארגז כלים</span>" in infra
    for needed in ("function toolsPage()", "async function loadTools()", "function toolRowHtml(", "function toolsGroupsHtml(", "function toolsBarHtml(",
                   "function toolsToggle(", "function toolsGroupMark(", "async function toolsSave()", "function toolsDirty()", "function toolsSummary(",
                   'await api("/tools/catalog")', 'await put("/tools/selection", body)', 'const body = { build: [...TOOLS_DRAFT.build], student: [...TOOLS_DRAFT.student] };',
                   'box("build", "בנייה/שיכפול"), box("student", "לתלמיד (v2)")', 'id="tools-save"', "סמן את המומלצים", "לא נמדד", 'UI.status("ok", "ארוז")',
                   "דורש חלון אישור", 'destroy: ["err"', 'rw: ["warn"', 'ro: [""',
                   'tools: { crumb: "ארגז כלים", title: "ארגז כלים", tabs: [], render: toolsPage, load: loadTools, own: true }'):
        assert needed in js, needed
    block = js[js.index("/* ---------- #649 שלב 1: ארגז כלים"):js.index("const pages = {")]
    assert 'class="acts"' not in block.split("function toolRowHtml(")[1].split("function toolsGroupsHtml(")[0], "#1032: בלי .acts בשורה"
    assert "prompt(" not in block and "confirm(" not in block.replace("confirmSheet(", ""), "prompt/confirm חסומים — רק sheet()"
    assert "disabled" not in block.replace('${dirty ? "" : " disabled"}', "").replace("save.disabled = !dirty", ""), "כפתור מנוטרל רק לשמירה בלי שינוי"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page table.dg.tools{", ".page .dg-bar label.chk{"):
        assert sel in css, sel


def test_the_console_has_no_ssh():
    """‏#1266 (הכרעת נדב 10/10): כל מה שקשור לפורט 22 מחוץ למוצר. אין מתג
    SSH לתחנות או לשרת, אין שורת 22 בפורטים, אין טביעת מפתח בכרטיס המכונה
    ואין קריאה ל-/ssh — בשום קובץ שהקונסולה מגישה."""
    files = sorted(p for p in STATIC.iterdir() if p.suffix in (".js", ".html", ".css"))
    assert files, "לא נמצאו קבצי קונסולה — הבדיקה לא בדקה דבר"
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        # ‏"ssh" בתחילת מילה בלבד: sshToggle, /ssh, "SSH לשרת" — לא classShow.
        for banned in (r"(?<![a-z])ssh", "dropbear", r"imagectl\.debug"):
            assert not re.search(banned, text), f"{path.name}: {banned}"


def test_the_machine_drawer_has_a_health_group_with_three_states_per_field():
    """‏#1049 שלב ב': קבוצת "בריאות המכונה" במגירה — השערים מעליה, וכל שדה
    בשלושה מצבים: null = "לא נבדק" (אפור), {error} = "לא הצלחנו לבדוק" (כתום),
    ערך = מוצג. בעמוד הבית: כל verdict הוא UI.note ב"דורש טיפול"."""
    js = _console_js()
    assert 'grp(`בריאות המכונה' in js   # ‏#1300: קבוצה בלשונית "סקירה" (‏.grp), לא לשונית משלה
    assert "machineVerdictsHtml(m)" in js and "machineHealthHtml(m)" in js
    health = js[js.index("function machineHealthHtml"):js.index("function machineDrawerHtml")]
    for label in ("חשמל", "שעון", "מעבד", "זיכרון", "טמפ' מקס'", "רשת",
                  "מתג ופורט", "כבל", "NVMe",
                  "קריסה קודמת", "מפתח OEM", "PCI בלי דרייבר", "הצפנה"):
        assert f'["{label}"' in health, label
    assert "netprobeLldpHtml" in health and "netprobeCableHtml" in js
    assert "לא נקלט (35ש')" in js and "לא נבדק — יש קישור" in js
    assert "לא הצלחנו להאזין" in js
    cell = js[js.index("function probeCell"):js.index("function probeSkewText")]
    assert "לא נבדק" in cell and "לא הצלחנו לבדוק" in cell
    assert 'UI.status("warn", `לא הצלחנו לבדוק' in cell, "שגיאה בכתום, לא באפור ולא בירוק"
    assert "מעולם לא דיווחה בדיקת מכונה" in health
    assert "selectPageById('drivers')" in health, "PCI בלי דרייבר מקשר לדף הדרייברים"
    home = js[js.index("function homeAttention"):js.index("function journalCls")]
    assert "m.probe_verdicts" in home and "openMachineDetail(" in home


def test_storage_page_is_an_admin_tree_node_with_all_eight_mockup_screens():
    """‏#1066 שלב ב': צומת "אחסון" תחת "תשתית" (admin בלבד, בין "בריאות
    ושירותים" ל"רשת"), עמוד `own` ב-`storage.js` שכל שמונת מסכי המוקאפ
    (`docs/design/console-redesign/storage-mockup-2026-09-18.html`) הם מצבים
    שלו: רשימה · הוסף→סוג · NFS · SMB · iSCSI א' · iSCSI ב' (שלושה מצבי דיסק) ·
    מגירה · שורות הבריאות מקובצות. "שמור ועגן" נדלק רק אחרי `POST /test` מוצלח
    (עיקרון 5); פירמוט והסרה מאחורי הקלדת IQN / שם (עיקרון 7); ב-`unknown` אין
    כפתור פירמוט כלל. הרינדור עצמו נבדק ב-`storage_page.test.cjs`."""
    page, js = _index(), _console_js()
    storage = (STATIC / "storage.js").read_text(encoding="utf-8")
    infra = page[page.index('id="infraTA"'):page.index('id="adminTA"')]
    node = infra[infra.index('data-page="storage"') - 40:infra.index('data-page="storage"') + 420]
    assert 'data-admin' in node and "selectPageById('storage')" in node
    assert "<span>אחסון</span>" in node
    assert infra.index('data-page="health"') < infra.index('data-page="storage"') < infra.index('data-page="network"'), "בין בריאות לרשת"
    assert '<script src="storage.js?v=' in page
    assert page.index('src="console.js?v=') < page.index('src="storage.js?v='), "storage.js אחרי console.js (UI/api/sheet)"
    assert 'storage: { crumb: "אחסון", title: "אחסון", tabs: [], render: () => storagePage(), load: () => loadStorage(), own: true }' in js
    for icon in ('"storage": "<ellipse', '"plus": "<path'):
        assert icon in js, icon
    assert '["storage_", "אחסון — מיקומים", "storage_summary"]' in js, "שורות storage_<id> בבריאות מקובצות"
    assert 'act = UI.acts([["פתח באחסון", "selectPageById(\'storage\')"]]);' in js
    assert 'r.available === false ? ` ${UI.pill("err", "לא זמין")}`' in js, "אימג' על מיקום לא נגיש — pill בספרייה"
    for needed in ("function storagePage()", "async function loadStorage()", "function startStorageTimer()", "function storageListView()",
                   "function storageAddView()", "function storageLocalView()", "function storageNfsView()", "function storageSmbView()",
                   "function storageIscsiAView()", "function storageIscsiBView()", "function storageDrawerHtml(loc)",
                   "async function storageScan()", "async function storageTest()", "async function storageSave()",
                   "async function storageIscsiLogin()", "async function storageIscsiDisk()", "async function storageMount(id)",
                   "function storageFormatSheet(id)", "function storageDelete(id)", "function storageDisconnect(id)",
                   'post(SL + "/scan", body)', 'post(SL + "/test", { type: storageType(), params: storageParams() })',
                   "tested: true", "/iscsi-login`", "/disk`", "/mount`", "/format`", "/check`", "/disconnect`", "/connect`", 'method: "DELETE"',
                   'disabled title="נדלק רק אחרי בדיקה מוצלחת"',                   'unknown: אין כפתור פירמוט כלל', "}, 30000);", "STORAGE_STATE_HELP", "kebab-col storage"):   # ‏#1300: המקרא = title של פיל המצב; פעולות ב-⋯
        assert needed in storage, needed
    assert "prompt(" not in storage and "confirm(" not in storage.replace("confirmSheet(", ""), "prompt/confirm חסומים — רק sheet()"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .dg.show-acts td .acts{visibility:visible}", ".page .types{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))",
                ".page .picklist{", ".page .steps{", ".page .disk-state{display:grid;grid-template-columns:minmax(0,1fr) 250px",
                ".page .big-choice{", ".page .btn:disabled{"):
        assert sel in css, sel
    narrow = css[css.index("#1066 שלב ב'"):css.index("/* ===== משוב ותנועה", css.index("#1066 שלב ב'"))]
    assert "grid-template-columns:1fr" not in narrow, "1fr לבדו = minmax(auto,1fr) וגולש ב-375 (הלקח מהמוקאפ)"
    assert ".page table.dg.storage{min-width:760px}" in narrow


def test_every_refresh_button_goes_through_refreshpage_so_it_gives_feedback():
    """ביקורת 10/10: ב-logs/monitor/tools/drivers/storage כפתור "רענון" קרא ישר ל-loader — בלי toast ובלי
    אייקון, בעוד שבדפים האחרים (refreshPage) יש 'הנתונים עודכנו'. כל כפתור רענון: אותו handler; בכותרות גם אותו אייקון."""
    seen = 0
    for name in ("console.js", "drivers.js", "storage.js", "net.js", "netcfg.js", "branches.js"):
        text = (STATIC / name).read_text(encoding="utf-8")
        for m in re.finditer(r'<button class="btn"[^>]*onclick="([^"]*)"[^>]*>([^<]*)רענון</button>', text):
            seen += 1
            assert m.group(1) == "refreshPage()", f"{name}: {m.group(0)[:90]}"
    assert seen >= 10, seen
    for name in ("drivers.js", "storage.js"):
        assert 'onclick="refreshPage()">${uiIcon("refresh")} רענון</button>' in (STATIC / name).read_text(encoding="utf-8"), name


def test_ports_table_switch_cell_wraps_so_the_table_fits_its_card():
    """ביקורת 10/10: .swrow היה nowrap ועמודת "מתג" יצאה 423px — הטבלה גלשה (1250 מול כרטיס 1074 ב-1440) והעמודה
    "מה קורה אם מכבים" נחתכה מאחורי העמודה הדביקה. בטבלה דחוסה הטקסט עוטף, ברוחב מוגבל."""
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    m = re.findall(r"\.page \.dg\.dense \.swrow\{([^}]*)\}", css)
    assert m, "אין כלל ל-.dg.dense .swrow"
    body = m[-1]
    assert "white-space:normal" in body and re.search(r"max-width:\d+px", body)


def test_upload_modal_title_matches_the_button_that_opens_it():
    """הכפתור "העלאת קובץ tar" פתח חלון בשם "קליטת אימג׳" — שם שהוא גם של הקליטה ממחשב בנייה."""
    js = _console_js()
    start = js.index("function openImageIngest")
    assert '"העלאת קובץ tar"' in js[start:start + 600]


def test_stage2_css_defines_what_the_js_emits_and_no_invented_items():
    """שלב 2: כל מחלקה שה-JS החדש פולט מוגדרת ב-CSS; ופריטים שהמוקאפ המציא
    (כרטיס גיבוי, בקשות הרשאה, ממצאי ביקורת) לא נבנו כי אין להם API."""
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for cls in ("usecell", "usenums", "usewrap", "tiles", "tile"):
        assert f".page .{cls}" in css or f".page .tile .{cls}" in css, cls
    assert ".page .tile .led" in css and ".dskico" in css
    js = _console_js() + (STATIC / "storage.js").read_text(encoding="utf-8")
    assert 'usage(total, free)' in js and 'class="tiles"' in js and 'class="dskico"' in js
    # שלב 3: כרטיס "גיבוי" כן נבנה — יש לו API (GET /update/backup, #1128) והמוקאפ המאושר מציג אותו בהגדרות.
    for invented in ("בקשות הרשאה", "ממצאי ביקורת"):
        assert invented not in js, invented


def test_stage3_css_defines_the_segmented_control_the_settings_page_emits():
    """stage 3: כרטיס "מראה" פולט class="seg"; בלי כלל CSS הכפתורים נראים כברירת מחדל של הדפדפן (נמצא בבדיקה חיה)."""
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    js = _console_js()
    assert 'class="seg"' in js
    assert ".seg{" in css and ".seg button[aria-pressed=true]" in css


def test_every_page_node_in_the_tree_carries_an_icon():
    """נדב 18/10: "סניפים" היה הפריט היחיד בעץ בלי `ui-icon` — שורה בלי אייקון
    בעמודה של אייקונים. כל `inventory-node` שהוא דף (`data-page`) נושא אחד."""
    page = _index()
    nodes = re.findall(r'<div class="inventory-node[^"]*"[^>]*data-page="([^"]+)"[^>]*>(.*?)</div>',
                       page, flags=re.S)
    assert len(nodes) >= 10, nodes
    missing = [name for name, inner in nodes if 'class="ui-icon"' not in inner]
    assert not missing, f"פריטי עץ בלי אייקון: {missing}"
    assert "branches" in {name for name, _ in nodes}


def test_the_register_window_builds_the_address_on_the_interserver_port():
    """נדב 18/10: ברישום משני מקלידים רק IP, והקונסולה בונה https://<ip>:<פורט>.
    ‏`INTERSERVER_PORT` ב-branches.js הוא הפורט הבין-שרתי של השרת — מוצמד כאן
    ל-health.DEFAULT_OWN_PORTS ולברירת המחדל של ‎--interserver-port, כדי שלא ייסחף."""
    from server import health
    js = (STATIC / "branches.js").read_text(encoding="utf-8")
    port = int(re.search(r"^const INTERSERVER_PORT = (\d+);$", js, flags=re.M).group(1))
    assert port == health.DEFAULT_OWN_PORTS["interserver"]
    main = (STATIC.parent / "main.py").read_text(encoding="utf-8")
    assert f'"--interserver-port", type=int, default={port},' in main


def test_a_secondary_root_is_indented_like_ta():
    """נדב 18/10 (vCenter, כמה hosts): כל משני הוא צומת שורש אחרי TA, והעץ שלו מוזח
    באותם צעדים כמו של TA (30/46/62). כללי ההזחה היו על `#srvTA` בלבד, ולכן הדפים
    של משני נצמדו לשוליים ונראו כרשימה שטוחה."""
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    nest = " .inventory-children"
    for depth, pad in ((1, 30), (2, 46), (3, 62)):
        ta = ".sidebar #srvTA" + nest * depth + " .inventory-node{padding-inline-start:%dpx}" % pad
        sec = ".sidebar #secondaryServers > .inventory-children" + nest * depth + " .inventory-node{padding-inline-start:%dpx}" % pad
        assert ta in css, ta
        assert sec in css, sec


def test_no_branch_word_on_any_console_screen():
    """נדב 18/10: "סניפים" → "שרתי משנה", ו"סניף" → "שרת משנה" — בכל הממשק: תפריט, כותרות
    ופירורים, לשוניות, כפתורים, הודעות, חלון הרישום, היומן, מסך השרת והגואי הנייטיבי.
    המזהים (branches, ‏data-page="branches", שמות קבצים) אינם טקסט ונשארים."""
    roots = [STATIC, STATIC.parent / "dcui", STATIC.parent.parent / "native-gui" / "src"]
    hits = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".js", ".html", ".css", ".py", ".c", ".h"} or "vendor" in path.parts:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "סניף" in line or "סניפ" in line:
                    hits.append(f"{path.name}:{n}")
    journal = (STATIC.parent / "journal_he.py").read_text(encoding="utf-8")
    hits += [f"journal_he.py:{n}" for n, line in enumerate(journal.splitlines(), 1) if "סניף" in line or "סניפ" in line]
    assert not hits, hits
    js = _console_js()
    assert 'branches: { crumb: "שרתי משנה", title: "שרתי משנה"' in js   # the page, one name with the settings tab


def test_one_name_for_a_secondary_server_on_every_screen():
    """נדב 18/10: שם אחד לדבר אחד — "שרת משנה" / "שרתי משנה" גם ביחיד: לא "שרת משני",
    "השרת המשני", "המשני" או "משניים" — בקונסולה, במסך השרת, בגואי הנייטיבי, ובכל
    מחרוזת שהשרת מחזיר או רושם (לא docstrings והערות פייתון). ‏"DNS משני" אינו שרת."""
    import io
    import tokenize
    word = re.compile(r"(?<![א-ת])[ובלמשכה]{0,3}משני(ים)?(?![א-ת])")
    allowed = ("DNS משני",)
    clean = lambda text: [w for w in [text] if not any(a in text for a in allowed)]   # noqa: E731
    hits = []
    for root, exts in ((STATIC, {".js", ".html", ".css"}), (STATIC.parent / "dcui", {".py"}),
                       (STATIC.parent.parent / "native-gui" / "src", {".c", ".h"})):
        for path in sorted(root.rglob("*")):
            if path.suffix not in exts or "vendor" in path.parts:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if word.search(line) and clean(line):
                    hits.append(f"{path.name}:{n}")
    for path in sorted(STATIC.parent.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for tok in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
            if (tok.type == tokenize.STRING and word.search(tok.string)
                    and not tok.string.lstrip("rbfuRBFU").startswith(('"""', "'''"))):
                hits.append(f"{path.name}:{tok.start[0]}")
    assert not hits, hits
