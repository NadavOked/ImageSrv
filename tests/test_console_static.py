"""#915/#916: שאריות העיצוב (#829) ב-HTML של הקונסולה שהוצגו כנתונים חיים.

בדיקות תוכן על הקבצים הסטטיים — לא מרנדרות ולא מריצות שרת. כל מחרוזת
כאן היא כזו שנדב ראה על המסך במעבדה (v0.26.1) והייתה קשיחה ב-HTML:
`v0.9` בכניסה ובשורת הסטטוס, `הודעות: 2`, `task-count 3`, סבב דמו
`LAB1 74%`, שלוש שורות משימות דמו, וכרטיס "כללי naming" עם `LAB1-05`.
"""

from __future__ import annotations

import re
from pathlib import Path

from console_src import console_files, console_js

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "static"


def _index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def _console_js() -> str:
    return console_js()


def test_index_html_has_no_hardcoded_version():
    """הגרסה מגיעה מ-`/api/console/me` (תג git). `v0.9` בכניסה ובשורת
    הסטטוס היו מהעיצוב; המחזיק היחיד הוא `#serverVersion` — ריק ב-HTML."""
    page = _index()
    assert "v0.9" not in page
    assert re.search(r"\bv\d+\.\d+(\.\d+)?\b", page) is None, "גרסה קשיחה ב-HTML"
    assert '<span id="serverVersion"></span>' in page


def test_index_html_task_dock_has_no_demo_numbers():
    """המעגן והפאנל מתמלאים מ-`renderActivity` (`/overview`). ב-HTML: מונה 0,
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
    """התג בסרגל סופר (`updateAlertBadge`) מכונות שנכשלו ובדיקות בריאות
    warn/bad; המגירה חייבת להציג את אותם פריטים — לא "טוען נתונים…" לנצח."""
    js = _console_js()
    fn = js[js.index("function topNotifications"):]
    fn = fn[:fn.index("\nfunction ", 1)]
    assert "טוען נתונים" not in fn
    assert "machineIsFailed" in fn
    assert 'c.state !== "warn" && c.state !== "bad"' in fn
    assert "אין התראות" in fn


def test_the_machines_page_has_no_demo_text_and_no_dead_tabs():
    """#916 → #954 גל 3: בלי כרטיס "כללי naming" (`LAB1-05`), ובלי הלשוניות
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
    """#916: הסינון מלחיצה על קבוצה בעץ נשאר בין מעברים; בלי חיווי הוא
    נראה כמו "אין מחשבים רשומים" מול עץ מאויש. הצומת "מחשבים" מאפס אותו
    (וגם את הכיתה הפתוחה כאובייקט, גל 3), והדף מציג את הקבוצה המסוננת עם
    ביטול וריק שמבחין בין המצבים."""
    js, page = _console_js(), _index()
    assert "MACHINES_FILTER=null;MACHINES_CLASS=null;MACHINES_ROLE=null;selectPageById('machines')" in page
    fn = js[js.index("function machinesTableCard("):js.index("function machinesTabs()")]
    assert "clearMachinesFilter()" in fn
    assert "מוצגת קבוצה" in fn
    table = js[js.index("function machinesTableHtml()"):js.index("function mchBulkHtml(")]
    assert "אין מחשבים בקבוצה" in table
    assert "אין מחשבים רשומים" in table        # המצב האמיתי של "אין בכלל" נשאר
    assert "function clearMachinesFilter() {\n  MACHINES_FILTER = null;" in js


def test_the_machines_page_lists_shrunk_source_disks_in_orange_with_clear():
    """#926: דיסק מקור שכווץ לקליטה ולא הוחזר לגודלו (הרשומה בשרת) מוצג
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
    # נדב 18/10 (console-full.html): הכרטיס מוצג כשיש רשומות, או כשהרשימה לא נקראה — לא נעלם בשקט
    assert "SHRINK_RECORDS == null || SHRINK_RECORDS.length ? shrinkRecordsCard()" in machines


# --- #954 גל 1: הסקירה הכללית נבנתה מחדש, הקוד הישן נמחק ---------------------


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
    """#968 סגר את "דורש API" של home.md: זמן הפעילות וכתובת ההפצה מגיעים
    מ-`/me` (uptime_seconds, deploy_ip); null מוצג בשם — לא "בקרוב", לא 0,
    ולא 127.0.0.1. ההיוריסטיקה `journalSeverity` נמחקה, לא נשארה לצד השדה."""
    js = _console_js()
    fn = js[js.index("function home("):js.index("function deploy(")]
    assert 'UI.soon("זמן פעילות")' not in fn
    # #1300: משנה הכותרת היא סיכום; זהות השרת (כולל זמן פעילות וכתובת ההפצה) — בכרטיס "שירותים"
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

def test_static_includes_carry_the_placeholder_and_never_a_number():
    """#4041: ה-`?v=` בקבצי המקור הוא placeholder אחד קבוע, בכל ה-includes של
    `index.html` ושל `monitor.html` (גם `monitor.js`). מספר שנכתב ביד חוזר
    להתנגשות בכל PR שנוגע בקונסולה; השרת ממלא אותו בגרסת המוצר בהגשה
    (test_console_static_pages_4041). בלי placeholder בקובץ אין מה להחליף —
    וה-include נשאר עם מספר ישן."""
    for name in ("index.html", "monitor.html"):
        page = (STATIC / name).read_text(encoding="utf-8")
        values = re.findall(r'\?v=([^"\s&>]*)', page)
        assert values, f"אין ?v= ב-{name}"
        assert set(values) == {"__IMAGESRV_V__"}, (name, set(values))
    mon = (STATIC / "monitor.html").read_text(encoding="utf-8")
    assert 'monitor.js?v=__IMAGESRV_V__' in mon


def test_iso_tab_script_is_included_after_console_js():
    """#404: iso.js נשען על UI/api/sheet מ-console.js, ולכן נטען אחריו, באותה גרסה."""
    page = _index()
    assert '<script src="iso.js?v=__IMAGESRV_V__"></script>' in page
    assert page.index('src="console.js?v=') < page.index('src="iso.js?v=')


def test_update_apply_and_revert_are_red_button_sheets_without_a_name():
    """#1192: apply הוא אישור הפיך, ולכן אין בו input/verify. #1778 (הכרעת
    #1270): גם "חזור ל-…" — חלון אישור עם כפתור אדום, בלי הקלדה ובלי שם שרת
    בגוף הבקשה. מחיקה ועצירת סבב נשארות עם שם היעד שלהן."""
    js = _console_js()
    apply = js[js.index("function confirmUpdateApply("):js.index("function confirmUpdateRevert(")]
    assert "sheet({" in apply
    assert "verify:" not in apply and "<input" not in apply
    assert "prompt(" not in apply and "confirm(" not in apply
    # רשימת השלכות (impactList) — #1301: שלוש ההשלכות של החלון שאושר 13/10 (S338)
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
    """#1150: כש-`svg` ריק (python3-qrcode חסר בשרת) מסך ההגדרה מציג הודעה
    אדומה שאומרת זאת בשם — לא מסתיר את המקום בשקט. ה-QR עצמו נכנס inline
    ל-`.qr` (ה-CSS `#login .qr svg` שולט בגודל)."""
    js = _console_js()
    fn = js[js.index("function loginSetupHtml("):js.index("function loginCodesHtml(")]
    assert "state.svg" in fn
    assert "אין QR — החבילה python3-qrcode חסרה בשרת; הקלד את הסוד ידנית" in fn
    assert 'role="alert"' in fn


def test_mfa_setup_hides_the_manual_secret_and_never_renders_otpauth_url():
    js = _console_js()
    fn = js[js.index("function loginSetupHtml("):js.index("function loginCodesHtml(")]
    assert "otpauth://" not in fn and "state.otpauth" not in fn
    assert "ImageSrv:${state.username" in fn       # #1302: כמו ה-issuer ב-server/console/totp.py
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
    (#953, null = לא ידוע), "בשימוש" מ-`/overview`, קליטה כשורה בטבלה, ומה שאין
    לו API ("טבלת המחיצות") מוצג כ"דורש API" — לא מומצא."""
    js = _console_js()
    for needed in ("function images(tab = 0)", "own: true", "function imagesTree()", "role=\"tree\"",
                   "min_target_bytes", "לא ידוע", "function captureRowHtml(", "function imageDrawerHtml(",
                   "דורש API", "function bulkDelete()", "confirm_name", "function imgDrop(", "/folders/order"):
        assert needed in js, needed
    assert "images: [images, emptyDataCard]" not in js


# ---------- #954 גל 3: מחשבים ----------

def test_machines_page_is_one_flat_table_from_the_api_only():
    """טבלה אחת שטוחה (console-full.html P.machines — נדב 18/10; קודם: מקובצת, 17/09),
    מגירה, הכיתה כאובייקט; מה-API הקיים בלבד. "דיסק N · SATA N-1" ולעולם לא
    sd*; מה שאין לו API (היסטוריית סבבים לכיתה) מוצג כ"דורש API" — לא כפתור
    מנוטרל ולא נתון מומצא. "אתחול מרחוק" ו"עריכת MAC" קיבלו API ב-#977."""
    js = _console_js()
    for needed in ("function machines(tab = 0)", "function machineRowHtml(", "function groupPage(",
                   "function machineDrawerHtml(", "function seenDevicesCard()", "function openAddMachine(", "dry_run: true",
                   "rebootMachine('${macEnc}')", "editMachineMac('${macEnc}')", "דורש API", "SATA ${n - 1}", "function connText("):
        assert needed in js, needed
    for gone in ('UI.soon("אתחול מרחוק")', 'UI.soon("עריכת MAC")'):
        assert gone not in js, gone
    block = js[js.index("/* ---------- #954 גל 3"):js.index("function healthStatusClass(")]
    # #1300 (נדב 12/10): "פתח במוניטור" במגירה מנוטרל רק כשאין למה להתחבר — עם הסיבה (title + שורה), לא "דורש API"
    mon = '<button class="btn primary" disabled title="${esc(block)}">פתח במוניטור</button>'
    assert mon in block
    assert "disabled" not in block.replace(mon, ""), "כפתור מנוטרל במקום 'דורש API' (README §8)"
    assert "sda" not in block and "sdb" not in block, "שם sd* בדף המחשבים"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .dg-bar .bulk{", ".page .disks{", ".page .disk.err{", ".page .dg-bar select{"):
        assert sel in css, sel


# ---------- #954 גל 3א: מחשבי שיכפול ומחשבי בנייה כאובייקטים ----------

def test_cloners_and_builders_objects_are_built_on_group_page_from_the_api_only():
    """הקבוצות הקבועות כאובייקטים על `groupPage` של גל 3 (לא מערכת חדשה):
    גריד חריצים "דיסק N · SATA N-1" (לעולם לא sd*), צבע SMART ירוק רק על
    `ok`, אדום מזיכרון הכשלים עם "נקה", מגירות מ-`/room` בסבב ומ-`/machines`
    בלעדיו; מחשבי בנייה — קליטה בתהליך מ-`/tasks` עם ביטול מאחורי הקלדת שם.
    #980: כיבוי כולם (`POST /room/poweroff`), היסטוריית הסבבים
    (`GET /room/history`), שלבי הקליטה (`/tasks.stage`) ומצב הדיסק לפני
    קליטה (`/machines.last_capture` + `probe.ntfs`) — מה-API, בלי "דורש API"."""
    js = _console_js()
    # #1485: הדפים לפי console-full.html (P.cloners / P.build) — כרטיס .mach עם שורות "דיסק N"
    # לפי מיפוי החיבורים (drawer_slots), כרטיס בנייה עם kv, ולשונית "דיסקים" שפותחת את חלון "מיפוי חיבורים" (#2001).
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
    """#954 גל 4: דף ההפצה הוא חדר המשכפלים (deploy.md) — על אותו גריד של
    גל 3א (`clonerCardHtml` במצב חדר, `machineSlots`, `slotHtml`), לא עותק.
    כתיבה רק ל-endpoints הקיימים (`POST /room`, `/room/start`, `/room/wake`,
    `/room/close` מאחורי הקלדת שם); סבב חדש inline (לא sheet); ההכרעה על
    SMART (כתום ממשיך, אדום מדלג) גלויה בדף; קצב הזרם ובלוקים ששודרו שוב —
    מ-`/room.round` (#989), null = "לא נמדד"; מה שאין לו API — היסטוריה,
    דילוג מרחוק, תשובה מהקונסולה — "דורש API" בטקסט. הדף הישן
    (סבבים / הצטרפות חיה, בקרת סבב, המגירה) איננו.
    18/10 (נדב: "לא לפי המוקאפ"): הלשונית הראשונה אחד-לאחד לפי
    docs/design/apple-mockups/console-full.html (P.deploy) — שלושה אריחים, כרטיס
    "חדר המשכפלים" וכרטיס לכל משכפל (`roomMachHtml`); בלי סבב — מצב ריק. בורר מצב-הדוגמה
    ומתג השינויים של המוקאפ אינם במוצר, וגם "תצוגה גדולה" (אינה במוקאפ)."""
    js = _console_js()
    for needed in ("function deploy(tab = 0)", "function loadDeploy(", "function roomKpis(r, pct)", "function roomGridCard(kids)", "function roomNotes(",
                   "function roomMachHtml(", "function roundDskHtml(",
                   "function roomNewWindow(", "function stopRoom(", "function startWave(", "function imageFitReason(", "function deployClassView(",
                   'post("/room", body)', 'post("/room/start")', 'post("/room/close", { confirm_name: name })',
                   'title: "חדר המשכפלים"', "כשל במחשב אחד לא עוצר את השאר", "עצירת הסבב…", "לא מחובר — לא הצטרף לסבב",
                   "בלי תשובה הסוכן ממשיך לכתוב", "בלי תשובה הסוכן מדלג", "r.throughput_bps != null", "function roomNetCard(r)", "דורש API",
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


def test_health_and_services_pages_are_tables_from_the_api():
    """#954 גל 5: בריאות = טבלת בדיקות (חמישה מצבים, "לא נבדק" אינו ירוק,
    לולאות אתחול מקובצות) — בלי הלשוניות הזהות "סקירה/שירותים/בדיקות" (#829 M5).
    #3887 (נדב 20/11): דף "פורטים" הוחלף בדף "שירותים" (console-services.js): בלי כרטיס לכל רשת,
    בלי "מה לפתוח בחומת האש של המכללה" (עובר לוויקי, #3515), בלי "בדוק עכשיו"/"הצג פרטים";
    כרטיס "שירותים" ירד מהבריאות. #1266: SSH יצא מהמוצר — אין /ssh."""
    js = _console_js()
    for needed in ("function health()", "function healthRows(", "function updateActionsHtml(", "async function loadHealthUpdate(",
                   'unknown: ["unk", "לא נבדק"]', 'off: ["", "כבוי"]', '["agent_loop:", "לולאות אתחול", "agent_loops"]',
                   "function servicesPage()", "async function loadServices()", "function svcStop(", "async function svcStart(",
                   "nicBody(n, { enabled: false, proxy: false })",
                   'onSubmit: () => send({ confirm: "imagesrv.monitor" })',
                   'health: { crumb: "בריאות ושירותים", title: "בריאות ושירותים", tabs: [], render: health, load: loadHealth, own: true }',
                   'ports: { crumb: "שירותים", title: "שירותים", tabs: [], render: () => servicesPage(), load: () => loadServices(), own: true }'):
        assert needed in js, needed
    for gone in ('id="ssh-body"', "function loadSsh(", "function sshLight(", "SSH_LIGHT", 'title="בקרוב (נדרש endpoint)"',
                 'tabs: ["סקירה", "שירותים", "בדיקות"]', "health: [health,", "ports: [ports]",
                 "function ports()", "function portCard(", "function portDetail(", "function portCheckNow(", "PORT_NETWORKS",
                 "מה לפתוח בחומת האש של המכללה", "function healthServicesCard(", "function healthSvcStop(",
                 "function portRows(", "function portServerToggle(", "function portToggleUrl(", "havePortsDhcp", "PORTS_MONITOR",
                 "function portSwitchHtml(", "function portSshNic(", "function sshToggle(", "SSH_STATE", 'api("/ssh")'):
        assert gone not in js, gone
    svc = (STATIC / "console-services.js").read_text(encoding="utf-8")
    assert "prompt(" not in svc and "confirm(" not in svc, "prompt/confirm חסומים — רק sheet()"
    assert "verify:" not in svc and "fields:" not in svc, "בלי הקלדת מילה (#1270): המילה נקראת מ-/ports, לא מוקלדת"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .st.unk::before{", ".page .sw{", ".page .sw.unk{", ".page dl.mkv{", ".page label.msw{",
                ".svc-tg{", ".svc-tg.on{background:var(--clr-action)}", ".svc-page .pill.unk{"):
        assert sel in css, sel


def test_small_pages_are_own_tables_from_the_api_and_wol_is_per_machine():
    """#954 גל 6: מוניטור (רשימה), דרייברים, הגדרות, הרשאות ויומן נבנו מחדש
    כעמודי `own` — טבלה אחת (datagrid) לכל אחד, מתגים `role="switch"`, הקלדת
    שם למחיקת משתמש/חבילה, "לא נקרא" כמצב משלו — והדפים הישנים ("Settings"
    באנגלית, `usersAdminPage` + `permissions` כפולים, "אירועים / Audit",
    כרטיס-לכל-מכונה במוניטור) נמחקו. #984: WoL למחשב בודד — `UI.soon("WoL")`
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
    for sel in (".page dl.mkv dt,.page dl.mkv dd{", ".page label.msw.changed{"):   # #1300: dl.kv / label.sw של המוקאפ
        assert sel in css, sel


def test_the_class_build_and_cloner_tree_nodes_open_a_role_focused_machines_page():
    """נדב 17/09 (עם צילום): לשלושת צומתי האב בעץ — כיתות / מחשבי בנייה / מחשבי
    שיכפול — "אמור להיות חלון כמו של המחשבים, אבל רק של כיתות / שיכפול / בנייה,
    בלי לשונית נראו ברשת". קליק = הדף הממוקד (selectMachinesRole), דאבל =
    פותח/סוגר את הענף. הקבוצות שמתחתיהם (grp_BUILD וכו') נשארות אובייקטים."""
    page = _index()
    # #1300 (נדב 12/10, "כמו המוקאפ"): ב-console-full.html "מחשבי בנייה" / "מחשבי שיכפול" הם עלים —
    # קליק = הדף הממוקד, העלה עצמו מודגש; בלי חץ ובלי תת-עץ קבוצות/מחשבים
    for role, box in (("classroom", "classesTree"), ("build", "buildTree"), ("cloner", "clonerTree")):
        assert f"data-role=\"{role}\" onclick=\"selectMachinesRole('{role}',this)\"" in page, role
        assert f'id="{box}"' not in page, box
    js = console_js()
    assert "function selectMachinesRole(role, el)" in js
    assert 'if (MACHINES_ROLE) return [ROLE_PAGE_HE[MACHINES_ROLE], red];' in js
def test_network_is_one_object_page_with_the_diagram_nics_and_deploy_tabs():
    """#954 גל 8: "רשת" בעץ הוא דף (קליק = הדף, דאבל = פותח/סוגר), ושלוש
    הלשוניות שנבנו לפי `network.md` / `network-nics.md` / `network-deploy.md`
    (תרשים SVG inline, טבלת כרטיסים בפועל/מוגדר/פער, רשת הפצה — מה מחולק בפועל
    מול השמור) + "פורטים" = הדף הקיים של גל 5, שלא נגעו בו מלבד רצועת הלשוניות.
    הדפים הישנים `nic`/`netdeploy`/`network` (כרטיס-לכל-NIC, `object-strip`,
    "פורטיים" קבועים, 10.44.12.0/24 קשיח) נמחקו. #1263: כרטיס = חלון "הגדרת
    כרטיס" (netcfg.js), רשת ההפצה = חלון משלה (net.js). #1032: תא הפעולות שומר מקום."""
    js, page, css = _console_js(), _index(), (STATIC / "console.css").read_text(encoding="utf-8")
    # העץ: הצומת "רשת" מנווט לדף; הילדים פותחים את האובייקט בלשונית; "פורטים" כמו שהיה
    assert ("""<div class="inventory-node" data-page="network" data-adv onclick="treeNavToggle(this,'netTA',"""   # #3025: מתקדם
            """function(){selectPageById('network')})\"""") in page
    # #1300: "חיבורים פיזיים" הוא עלה כמו ב-console-full.html — בלי תת-עץ כרטיסים
    assert 'data-net-tab="1" onclick="openNetwork(1,null,this)"' in page and 'id="nicNode"' not in page and 'id="nicTree"' not in page
    assert 'data-net-tab="2" onclick="openNetwork(2,null,this)"' in page
    assert """<div class="inventory-node" data-page="ports" onclick="selectPageById('ports')\"""" in page
    for gone in ('data-page="nic"', 'data-page="netdeploy"', "NIC_HIGHLIGHT"):
        assert gone not in page, gone
    for needed in ("function networkPage(", "function loadNetwork(", "function netDiagramModel(", "function netMapSvg(", "function netLaneOpen(", "function netLaneDrawerHtml(", "function networkDiagramTab(",
                   "function networkNicsTab(", "function networkDeployTab(", "function netServicesOn(", "function netProbe(", "function openNetwork(",
                   'network: { crumb: "רשת", title: "רשת", get tabs() { return netTabs(); }, render: (i) => networkPage(i), load: loadNetwork, own: true }',
                   "function netTabs() { return netDeployUnset() ? NET_TABS.slice(0, 2) : NET_TABS; }",   # #3029: "רשת הפצה" רק אחרי שהוגדרה
                   'const NET_TABS = ["תרשים", "חיבורים פיזיים", "רשת הפצה"];',   # #3887: "שירותים" — דף משלו בעץ
                   'width="${W}" height="340" viewBox="0 0 ${W} 340"',   # gotcha: SVG עם height מפורש
                   "מה מותר: דורש API", 'title: "כיתות — v2"', "`/net/interfaces/${encodeId(name)}/probe`",
                   "editNicConfig(n.name)", "editDeployNet(n.name)", "routeDeleteSheet(", 'cls: "stable",'):
        assert needed in js, needed
    for gone in ("function renderNicCard(", "function netBannerHtml(", "function nic()", "function netdeploy()", "function network()", "function saveNetwork(",
                 "function loadNetcfgData(", "function loadNetPages(", "function editDeployNic(", "function nicUnion(", "10.44.12.0/24", "פורטיים",
                 'netdeploy: [netdeploy]', 'nic: [nic]', "selectPageById('nic')", "selectPageById('netdeploy')",
                 # #1263: "קבצי dnsmasq" ו"הקובץ של הכרטיס" הוסרו מהקונסולה
                 "previewDnsmasq", "netShowFile", "קבצי dnsmasq", "editNic(", "editAddress("):
        assert gone not in js, gone
    block = js[js.index("/* ---------- #954 גל 8: רשת כאובייקט"):js.index("/* ---------- #3887: מה שנשאר מדף \"פורטים\"")]
    assert "prompt(" not in block and "confirm(" not in block.replace("confirmSheet(", ""), "prompt/confirm חסומים — רק sheet()"
    assert "disabled" not in block.replace("disabled_at", ""), "כפתור מנוטרל במקום 'דורש API' (README §8)"
    assert "function routeDeleteSheet(" in (STATIC / "netcfg.js").read_text(encoding="utf-8")
    # #1032: תא הפעולות שומר מקום (visibility), לא display:none
    assert ".page .dg.stable td .acts{display:flex;visibility:hidden" in css
    for sel in (".page .netmap-svg{", ".page .netmap-svg .bad.nm-wire{stroke:var(--clr-danger);stroke-dasharray:5 4}", ".page .netmap-svg .bad.nm-box{stroke:var(--clr-danger)", ".page .kv3{"):
        assert sel in css, sel


# ---------- #649 שלב 1: ארגז כלים ----------

def test_tools_page_is_an_admin_tree_node_with_two_selections_per_tool():
    """#649 שלב 1 (הכרעת נדב 17/09): צומת "ארגז כלים" תחת "תשתית" (admin בלבד,
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
    """#1266 (הכרעת נדב 10/10): כל מה שקשור לפורט 22 מחוץ למוצר. אין מתג
    SSH לתחנות או לשרת, אין שורת 22 בפורטים, אין טביעת מפתח בכרטיס המכונה
    ואין קריאה ל-/ssh — בשום קובץ שהקונסולה מגישה."""
    files = sorted(p for p in STATIC.iterdir() if p.suffix in (".js", ".html", ".css"))
    assert files, "לא נמצאו קבצי קונסולה — הבדיקה לא בדקה דבר"
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        # "ssh" בתחילת מילה בלבד: sshToggle, /ssh, "SSH לשרת" — לא classShow.
        for banned in (r"(?<![a-z])ssh", "dropbear", r"imagesrv\.debug"):
            assert not re.search(banned, text), f"{path.name}: {banned}"


def test_the_machine_drawer_has_a_health_group_with_three_states_per_field():
    """#1049 שלב ב': קבוצת "בריאות המכונה" במגירה — השערים מעליה, וכל שדה
    בשלושה מצבים: null = "לא נבדק" (אפור), {error} = "לא הצלחנו לבדוק" (כתום),
    ערך = מוצג. בעמוד הבית: כל verdict הוא UI.note ב"דורש טיפול"."""
    js = _console_js()
    assert 'grp(`בריאות המכונה' in js   # #1300: קבוצה בלשונית "סקירה" (.grp), לא לשונית משלה
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
    """#1066 שלב ב': צומת "אחסון" תחת "תשתית" (admin בלבד, בין "בריאות
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
                   'disabled title="נדלק רק אחרי בדיקה מוצלחת"',                   'unknown: אין כפתור פירמוט כלל', "}, 30000);", "STORAGE_STATE_HELP", "kebab-col storage"):   # #1300: המקרא = title של פיל המצב; פעולות ב-⋯
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
    for name in (*console_files(), "drivers.js", "storage.js", "net.js", "netcfg.js", "branches.js"):
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
    `INTERSERVER_PORT` ב-branches.js הוא הפורט הבין-שרתי של השרת — מוצמד כאן
    ל-health.DEFAULT_OWN_PORTS ולברירת המחדל של --interserver-port, כדי שלא ייסחף."""
    from server.system import health
    js = (STATIC / "branches.js").read_text(encoding="utf-8")
    port = int(re.search(r"^const INTERSERVER_PORT = (\d+);$", js, flags=re.M).group(1))
    assert port == health.DEFAULT_OWN_PORTS["interserver"]
    main = (STATIC.parent / "main.py").read_text(encoding="utf-8")
    from server.core import ports as product_ports
    assert product_ports.INTERSERVER == port  # #3871: the default comes from the one port table
    assert '"--interserver-port", type=int, default=product_ports.INTERSERVER,' in main


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
    המזהים (branches, data-page="branches", שמות קבצים) אינם טקסט ונשארים."""
    roots = [STATIC, STATIC.parent / "dcui", STATIC.parent.parent / "native-gui" / "src"]
    hits = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".js", ".html", ".css", ".py", ".c", ".h"} or "vendor" in path.parts:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "סניף" in line or "סניפ" in line:
                    hits.append(f"{path.name}:{n}")
    journal = (STATIC.parent / "console" / "journal_he.py").read_text(encoding="utf-8")
    hits += [f"journal_he.py:{n}" for n, line in enumerate(journal.splitlines(), 1) if "סניף" in line or "סניפ" in line]
    assert not hits, hits
    js = _console_js()
    assert 'branches: { crumb: "שרתי משנה", title: "שרתי משנה"' in js   # the page, one name with the settings tab


def test_one_name_for_a_secondary_server_on_every_screen():
    """נדב 18/10: שם אחד לדבר אחד — "שרת משנה" / "שרתי משנה" גם ביחיד: לא "שרת משני",
    "השרת המשני", "המשני" או "משניים" — בקונסולה, במסך השרת, בגואי הנייטיבי, ובכל
    מחרוזת שהשרת מחזיר או רושם (לא docstrings והערות פייתון). "DNS משני" אינו שרת."""
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
