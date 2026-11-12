"use strict";
// Shared DOM, HTTP, escaping, theme, and toast helpers; loads before console.js.
const $ = (sel) => document.querySelector(sel);
/* עיקרון 5: "השרת לא ענה" ו"השרת ענה בשגיאה" הם שני מצבים. כשל רשת של
   fetch (TypeError / "Failed to fetch" / NetworkError / AbortError) הופך
   כאן, במקום אחד, להודעה בעברית עם `error.network = true` ובלי `status`;
   4xx/5xx ממשיכים להציג את ה-`detail` של השרת עם `error.status`. */
const NET_DOWN_HE = "השרת לא עונה — בדוק שהוא דלוק ונסה שוב";
async function apiFetch(url, options) {
  try {
    return await fetch(url, options);
  } catch (e) {
    const name = e && e.name;
    if (name === "TypeError" || name === "AbortError" || name === "NetworkError"
        || /failed to fetch|networkerror|load failed|network request failed/i.test(String(e && e.message))) {
      const error = new Error(NET_DOWN_HE);
      error.network = true;
      noteApiFailure(NET_DOWN_HE);
      throw error;
    }
    throw e;
  }
}
/* ה-toast צובע "שגיאה" רק כשהטקסט שלו בא מכשל API שזה עתה קרה — כך
   `toast(e.message)` ב-200 האתרים הקיימים נצבע נכון בלי לשנות אף אחד מהם. */
let lastApiFailure = { text: "", at: 0 };
function noteApiFailure(text) { lastApiFailure = { text: String(text), at: Date.now() }; }
/* ‏#1179: "הקשר שרת". כשנבחר שרת משנה בעץ, SERVER_CTX הוא המזהה שלו, וכל בקשת קונסולה
   הולכת דרך ה-relay של הראשי (‏/storage-nodes/{id}/console/…, ‏8443) — אותן
   פונקציות ה-load וה-render של הראשי מציירות את שרת המשנה, ולכן אותם עמודים ואותן לשוניות.
   ‏/me, כניסה ויציאה הם של המשתמש בראשי. גם post/put/del בהקשר שרת משנה הולכים ל-relay
   (שלב 2) — לעולם לא לנתיב של הראשי; ה-relay מעביר רק פעולות מהרשימה המותרת
   (משתמשים · הגדרות · רשת) ומסרב לשאר. העלאה/הורדה של קבצים אינה עוברת בערוץ (JSON
   בלבד) — נחסמת כאן, כדי שלא תפעל על הראשי. */
let SERVER_CTX = null, PRIMARY_ME = null;
const PRIMARY_ONLY_PATH = /^\/(me|login|logout|session)(\/|\?|$)/;
const SECONDARY_READ_ONLY = "לא זמין על שרת המשנה מכאן — קבצים (העלאה/הורדה) אינם עוברים בערוץ בין השרתים";
function consoleUrl(path, primary = false) {
  if (primary || !SERVER_CTX || PRIMARY_ONLY_PATH.test(path)) return "/api/console" + path;
  return `/api/console/storage-nodes/${encodeId(SERVER_CTX)}/console${path}`;
}
function secondaryReadOnly() {
  if (!SERVER_CTX) return false;
  noteApiFailure(SECONDARY_READ_ONLY);
  return true;
}
async function api(path, options = {}) {
  const { primary = false, ...fetchOptions } = options;
  const response = await apiFetch(consoleUrl(path, primary), {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...fetchOptions,
  });
  if (response.status === 401) { showLogin(); throw new Error("לא מחובר"); }
  if (!response.ok) {
    let detail = "שגיאה " + response.status;
    // ‏#1073: `message_he` הוא החוזה של סירובים עם קוד (`deploy_no_console`
    // בכניסה) — מוצג כלשונו; `detail` הוא הצורה של HTTPException.
    let code;
    try { const body = await response.json(); detail = body.message_he || body.detail || detail; code = body.code; } catch (e) {}
    const error = new Error(detail);
    error.status = response.status;   // ‏#936: הקורא מבחין בין 403/409 לכשל אמיתי
    error.code = code;                // ‏#1891: סירוב עם קוד (`update_busy`) — הקורא מציע עקיפה
    noteApiFailure(detail);
    throw error;
  }
  noteServerClock(response);
  return response.json();
}
/* ‏#2781: שעון השרת, לא שעון הדפדפן — "10 דקות אחרונות" בפאנל המשימות חייב להיות זהה אצל כל המנהלים.
   ה-API אינו מחזיר את השעה בגוף התשובה, לכן הסטייה נלקחת מכותרת Date של כל תשובה (דיוק שנייה; בלי הכותרת, למשל
   בקריאה בין-מקורות ש-CORS מסתיר, נשארים עם שעון הדפדפן). */
let SERVER_CLOCK_OFFSET = null;
function noteServerClock(response) {
  const server = Date.parse((response && response.headers && response.headers.get("Date")) || "");
  if (Number.isFinite(server)) SERVER_CLOCK_OFFSET = server - Date.now();
}
function serverNow() { return Date.now() + (SERVER_CLOCK_OFFSET || 0); }
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
const put = (path, body) => api(path, { method: "PUT", body: JSON.stringify(body || {}) });
const del = (path) => api(path, { method: "DELETE" });

/* ‏#1129: הברחה מפורשת של ששת התווים, לא textContent→innerHTML — הסריאליזציה
   של הדפדפן ממירה רק `&<>` בצומת טקסט, ו-esc() משמש ב-67 attributes
   (`value="…"`, `title="…"`, `data-*`). ערך עם `"` היה סוגר את ה-attribute
   ומזריק `onfocus=`; המקורות אינם בשליטת המפעיל (שמות שיתופי SMB/NFS
   מסריקת רשת, IQN, ‏switch_name מ-LLDP, dmidecode). בתוך `onclick="fn('…')"`
   זה עדיין לא מספיק — שם משתמשים ב-encodeId + decodeURIComponent. */
const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" };
function esc(text) {
  return (text == null ? "" : String(text)).replace(/[&<>"'`]/g, (c) => ESC_MAP[c]);
}
/* הטקסט של HTML שהקונסולה עצמה בנתה, בלי התגיות — ל-title (שעובר esc() אחר כך).
   מעבר אחד, תו אחרי תו, ולא ‏/<[^>]*>/g: ה-regex הוא הסרה "חלקית" (CodeQL
   js/incomplete-multi-character-sanitization) — מ-"<<b>script>" הוא משאיר "<script>".
   כאן כל "<" פותח תגית שנזרקת עד ה-">" שסוגר אותה, ולכן בפלט לא נשאר "<" אחד.
   ישויות (&#39;) נשארות כמו שהן; עברית ושאר התווים עוברים ללא שינוי. */
function stripTags(html) {
  let out = "", inTag = false;
  for (const ch of String(html == null ? "" : html)) {
    if (inTag) { if (ch === ">") inTag = false; }
    else if (ch === "<") inTag = true;
    else out += ch;
  }
  return out;
}

/* ---------- ערכת צבעים ---------- */
/* #1093: נשמר בשרת (users.theme): auto / light / dark — ערך אחד לקונסולה
   ול-GUI של מחשב הבנייה. ‏#1499 (נדב 16/10): ברירת המחדל לכולם בהיר —
   auto = בהיר, ושום דבר לא עוקב אחרי מערכת ההפעלה. המתג מחליף בין בהיר
   לכהה ונשמר למשתמש. localStorage הוא מטמון בלבד — מונע הבהוב בטעינה
   כשהבחירה אינה auto, ונמחק ב-logout וב-auto. */
const THEME_KEY = "imagesrv-theme";
const THEME_CYCLE = ["light", "dark"];
const THEME_TITLE = { light: "בהיר", dark: "כהה" };
let themePref = "auto";

function currentTheme() {
  return themePref === "dark" ? "dark" : "light";
}

function paintTheme() {
  const id = currentTheme();
  document.documentElement.setAttribute("data-theme", id);
  const button = $("#theme-toggle");
  if (!button) return;
  button.innerHTML = uiIcon(id === "dark" ? "sun" : "moon");
  button.title = THEME_TITLE[id];
}

function applyTheme(pref) {
  themePref = pref === "dark" || pref === "light" ? pref : "auto";
  if (themePref === "auto") {
    if (typeof localStorage.removeItem === "function") localStorage.removeItem(THEME_KEY);
  } else {
    localStorage.setItem(THEME_KEY, themePref);
  }
  paintTheme();
}

function applyServerTheme(me) {
  const t = me && me.theme;
  applyTheme(t === "light" || t === "dark" ? t : "auto");
}

/* בחירה מפורשת (כרטיס "מראה" ב"החשבון שלי"): אותו מנגנון כמו הכפתור שבכותרת, ונשמרת בשרת (users.theme). */
function setThemePref(pref) {
  if (!THEME_CYCLE.includes(pref)) return;
  applyTheme(pref);
  if (!ME) return;
  ME.theme = pref;
  const done = put("/me/theme", { theme: pref }).catch((e) => toast(e.message));
  if (current === "account") renderCurrent();   // הכרטיס "מראה" עבר ל"החשבון שלי" (נדב 17/10)
  return done;
}
/* ‏#1499: מה שרואים מתחלף — auto מצויר בהיר, ולכן הלחיצה ממנו היא לכהה. */
function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  applyTheme(next);
  if (!ME) return;
  ME.theme = next;
  return put("/me/theme", { theme: next }).catch((e) => toast(e.message));
}

$("#theme-toggle").addEventListener("click", toggleTheme);


function encodeId(value) { return encodeURIComponent(value).replaceAll("'", "%27"); }
/* ‏#1634: ערך כמחרוזת JS בתוך on*="…". ‏esc() אינו מספיק שם — הדפדפן מפענח &#39; חזרה ל-' לפני שה-JS רץ;
   אחרי encodeId לא נשאר גרש, & או < לפענח, והפונקציה מקבלת את הערך המקורי. */
function jsStr(value) { return `decodeURIComponent('${encodeId(value == null ? "" : value)}')`; }
