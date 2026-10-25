/* ImageCtl — מעטפת הקונסולה: אימות, ניווט, drawer/modal.
   תוכן העמודים (נתונים אמיתיים) נכנס בשלבים הבאים. */
"use strict";

const $ = (sel) => document.querySelector(sel);
let ME = null;
/* #1081: v1 hides classrooms. v2 turns ME.capabilities.classrooms on. */
function classroomsOn() {
  return !!(ME && ME.capabilities && ME.capabilities.classrooms);
}
/* v1 ships without the toolbox (Nadav, 19/09). v1.1 turns ME.capabilities.tools on. */
function toolsOn() {
  return !!(ME && ME.capabilities && ME.capabilities.tools);
}
let OVERVIEW = null;
let IMAGES = null, FOLDERS = null;
let IMAGES_FOLDER = null;
let MACHINES = null, GROUPS = null;
let DISK_FAILURES = null;   // #874: זיכרון כשלי הכתיבה (דיסקים אדומים)
let SHRINK_RECORDS = null;  // #926: דיסקי מקור שכווצו בקליטה ולא הוחזרו לגודלם (כתום)
let MACHINES_FILTER = null;
/* צומת-אב בעץ (כיתות / מחשבי בנייה / מחשבי שיכפול) — דף המחשבים ממוקד לתפקיד
   אחד, בלי לשונית "נראו ברשת" (נדב 17/09). */
let MACHINES_ROLE = null;
const ROLE_PAGE_HE = { classroom: "כיתות", build: "מחשבי בנייה", cloner: "מחשבי שיכפול" };
let HEALTH = null, NETCFG = null;
let MONITOR = null, monitorError = "";
let PORTS = null, portsError = "";
let USERS = null, JOURNAL = null;   // ‏#954 גל 6: JOURNAL = השורות שנטענו (LOG.rows), USERS = /users

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
  return response.json();
}
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

/* ---------- #954: שפת העיצוב המשותפת — רכיבי רינדור לכל עמוד חדש ----------
   המקור: docs/design/console-redesign (README §"שפת העיצוב", המוקאפים
   שנדב אישר 17/09). ה-CSS: החלק ".page" בסוף console.css. כל עמוד שנבנה
   מחדש מרכיב את עצמו מכאן — כותרת אובייקט, לשוניות, KPI, כרטיס, datagrid,
   פס מצב, מפתח-ערך, ציר-זמן, הודעה וריק-מצב. הפונקציות מחזירות HTML;
   טקסט חופשי עובר esc() אצל הקורא כשהוא HTML, וכאן כשהוא מחרוזת. */
const UI = {
  /* כותרת אובייקט: breadcrumb, אייקון, שם, שורת-משנה, תג מצב, פעולות, לשוניות. */
  /* compact (apple-pending-4 · 4): שורה אחת — אייקון, כותרת, פיל מצב, פעולות — ומתחתיה שורת משנה אחת
     (נחתכת עם … ובמלואה ב-title). לא משנה את המבנה של שאר הדפים. */
  objHeader({ crumbs = [], icon = "server", name = "", sub = "", pill = "", actions = "", tabs = [], tab = 0, tabClick = (i) => `activateTab(${i})`, compact = false }) {
    const crumbHtml = crumbs.map((c, i) => {
      const last = i === crumbs.length - 1;
      const label = i === 0 ? serverRootName(c.label) : c.label;   // שורש השביל = שורש העץ (שם השרת), כמו ב-vCenter
      const item = c.onclick && !last
        ? `<a role="link" tabindex="0" onclick="${c.onclick}">${esc(label)}</a>` : `<span>${esc(label)}</span>`;
      return (i ? `<span>/</span>` : "") + item;
    }).join("");
    const tabHtml = tabs.length > 1 ? `<div class="tabs" role="tablist">${tabs.map((t, i) =>
      `<button type="button" class="tab${i === tab ? " on" : ""}" role="tab" aria-selected="${i === tab}" tabindex="${i === tab ? 0 : -1}" onclick="${tabClick(i)}">${Array.isArray(t) ? `${esc(t[0])}<small>${esc(t[1])}</small>` : esc(t)}</button>`).join("")}</div>` : "";
    // ה-title הוא הטקסט עצמו: בלי תגיות, וישויות שכבר הוברחו מפוענחות לפני הבריחה החוזרת (#1300: "אימג&#39;ים").
    const plain = stripTags(sub).replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
    const subTitle = sub ? ` title="${esc(plain)}"` : "";
    return `<div class="obj${compact ? " compact" : ""}"><div class="crumbs">${crumbHtml}</div><div class="obj-row"><div class="obj-icon">${uiIcon(icon)}</div><div><div class="obj-name">${esc(name)}${pill}</div>${sub ? `<div class="obj-sub"${subTitle}>${sub}</div>` : ""}</div><div class="obj-actions">${actions}</div></div>${tabHtml}</div>`;
  },
  /* KPI: מספר גדול + תווית + שורת משמעות. cls: ok/warn/err/info/"" (אפור = אין/לא נבדק). */
  kpi({ cls = "", label, value, unit = "", sub = "", bar = null, barPct = false }) {
    const barEl = bar == null ? "" : `<div class="bar" style="margin-top:6px" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${bar}"><i style="--w:${bar}%"></i></div>`;
    // ‏barPct (stage 2): פס שימוש מציג גם את האחוז לידו; פס התקדמות רגיל — בלי.
    const barHtml = barEl && barPct ? `<div class="bar-row">${barEl}<span class="pct">${bar}%</span></div>` : barEl;
    return `<div class="kpi ${cls}"><div class="l">${esc(label)}</div><div class="v"><bdi dir="auto">${esc(value)}</bdi>${unit ? ` <small>${esc(unit)}</small>` : ""}</div>${barHtml}${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
  },
  card({ title, small = "", acts = "", body, cls = "c12", flush = false }) {
    return `<div class="${cls} card"><div class="card-h"><span>${esc(title)}${small ? ` <small>${esc(small)}</small>` : ""}</span>${acts ? `<div class="acts">${acts}</div>` : ""}</div><div class="card-b${flush ? " flush" : ""}">${body}</div></div>`;
  },
  /* מצב עם נקודה: ok/warn/err/run/"" (אפור = כבוי/לא נבדק). */
  status(cls, text) { return `<span class="st ${cls}">${esc(text)}</span>`; },
  pill(cls, text) { return `<span class="pill ${cls}">${esc(text)}</span>`; },
  /* datagrid: columns = תוויות; rows = מערכי HTML לתאים (מוברחים ע"י הקורא). */
  /* שורה: מערך תאים, או {cells, attrs} (תכונות ל-<tr>), או {html} (שורה מוכנה).
     עמודה: מחרוזת (מוברחת) או {html}. */
  datagrid({ columns, rows, empty = "אין נתונים", cls = "", opt = [] }) {
    if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
    const td = (c, i) => `<td${opt.includes(i) ? ` class="c-opt"` : ""}>${c}</td>`;   // opt = עמודות משניות: מוסתרות בכרטיס צר
    const tr = (r) => Array.isArray(r) ? `<tr>${r.map(td).join("")}</tr>`
      : r.html != null ? r.html : `<tr ${r.attrs || ""}>${r.cells.map(td).join("")}</tr>`;
    return `<table class="dg${cls ? " " + cls : ""}"><thead><tr>${columns.map((c, i) => `<th${opt.includes(i) ? ` class="c-opt"` : ""}>${typeof c === "string" ? esc(c) : c.html}</th>`).join("")}</tr></thead><tbody>${rows.map(tr).join("")}</tbody></table>`;
  },
  name(main, sub = "") { return `<span class="name">${esc(main)}</span>${sub ? `<span class="sub">${esc(sub)}</span>` : ""}`; },
  nameHtml(main, subHtml) { return `<span class="name">${esc(main)}</span>${subHtml ? `<span class="sub">${subHtml}</span>` : ""}`; },
  acts(buttons) { return `<div class="acts">${buttons.map(([label, onclick]) => `<button class="btn sm" onclick="${onclick}">${esc(label)}</button>`).join("")}</div>`; },
  /* פס התקדמות + אחוז. pct=null → פס ריק ו"—" (לא 0%: עיקרון 5). */
  barRow(pct, cls = "", text = null) {
    const known = typeof pct === "number" && Number.isFinite(pct);
    const w = known ? Math.max(0, Math.min(100, Math.round(pct))) : 0;
    const label = text != null ? text : (known ? w + "%" : "—");
    return `<div class="bar-row"><div class="bar ${cls}"><i style="--w:${w}%"></i></div><span class="pct">${esc(label)}</span></div>`;
  },
  /* שימוש = פס + המספרים עצמם: כמה תפוס מתוך כמה, ואחוז לידו (console-full.html useCell).
     total/free חסרים או total<=0 = "לא נמדד" — לא 0% (עיקרון 5). */
  usage(total, free) {
    const t = Number(total), f = Number(free);
    if (total == null || free == null || !Number.isFinite(t) || !Number.isFinite(f) || t <= 0) return `<span class="muted">לא נמדד</span>`;
    const used = Math.max(0, Math.min(t, t - f)), pct = Math.round(100 * used / t);
    return `<div class="usecell"><div class="bar ${pct >= 90 ? "err" : pct >= 75 ? "warn" : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="--w:${pct}%"></i></div><div class="usenums"><bdi dir="ltr">${esc(fmtBytes(used))} / ${esc(fmtBytes(t))}</bdi><span class="pct">${pct}%</span></div></div>`;
  },
  kv(pairs) { return `<div class="kv">${pairs.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${v}</span>`).join("")}</div>`; },
  note(cls, html) { return `<div class="note ${cls}"><span aria-hidden="true">●</span><span>${html}</span></div>`; },
  /* הודעה עם שגיאת מערכת (apple-pending-4, דשבורדים): ‏raw — הטקסט הגולמי שהשרת החזיר. כשהוא לטיני בעיקרו
     (נתיב, שם קובץ, שגיאת OS) הוא לא מוצג בגוף ההודעה אלא מאחורי "פרטים טכניים"; משפט עברי נשאר בגוף.
     ‏before/after הם HTML מוכן (הקורא מבריח). */
  noteTech(cls, before, raw, after = "") {
    const hasRaw = raw != null && String(raw) !== "";
    if (hasRaw && isRawTechnical(raw))
      return UI.note(cls, `${before}${after}<details class="tech"><summary>פרטים טכניים</summary><code dir="ltr">${esc(raw)}</code></details>`);
    return UI.note(cls, `${before}${hasRaw ? `: ${esc(raw)}` : ""}${after}`);
  },
  empty(text, action = "", title = "") { return `<div class="empty">${title ? `<b class="et">${esc(title)}</b>` : ""}${esc(text)}${action ? `<div>${action}</div>` : ""}</div>`; },
  /* ציר-זמן: [{t, cls, text(html), who}] */
  timeline(events) {
    return `<div class="timeline">${events.map((e) => `<div class="ev"><span class="t">${esc(e.t)}</span><span class="d ${e.cls || ""}"></span><span>${e.text}</span><span class="who">${esc(e.who || "")}</span></div>`).join("")}</div>`;
  },
  /* מה שאין לו API: הודעה ולא כפתור מנוטרל (README §8). */
  soon(label) { return `<span class="muted" title="דורש API">${esc(label)} — בקרוב</span>`; },
  link(label, onclick) { return `<a role="link" tabindex="0" onclick="${onclick}">${esc(label)}</a>`; },
  /* ‏⋯ בסוף שורה: כל פעולות השורה בתפריט אחד (עמודה דביקה בקצה — ‏console.css .kebab-col).
     items = [[תווית, onclick, "danger"?]]; הפעולה ההרסנית עוברת לתחתית, מופרדת ואדומה.
     הפעולה עצמה לא משתנה: הפעולה ההרסנית פותחת sheet אישור (שאלה, השלכות, כפתור אדום). */
  rowMenu(label, items) {
    const ordered = items.filter((i) => i[2] !== "danger").concat(items.filter((i) => i[2] === "danger"));
    return `<button type="button" class="kebab" aria-haspopup="menu" aria-expanded="false" aria-label="${esc("פעולות עבור " + label)}" data-menu="${esc(JSON.stringify(ordered))}" onclick="event.stopPropagation();openRowMenu(this)" onkeydown="rowMenuTriggerKey(event,this)">⋯</button>`;
  },
};

/* ---------- תפריט ⋯ (מקלדת: Enter/רווח/↓ פותחים, ↑↓ Home/End מזיזים, Esc סוגר והפוקוס חוזר לכפתור) ---------- */
let ROW_MENU_BTN = null, ROW_MENU_WATCH = null;
function rowMenuEl() {
  let m = document.getElementById("rowMenu");
  if (m) return m;
  m = document.createElement("div");
  m.id = "rowMenu"; m.className = "row-menu"; m.setAttribute("role", "menu");
  m.addEventListener("keydown", rowMenuKey);
  document.body.appendChild(m);
  return m;
}
function rowMenuHtml(items) {
  return items.map(([label, onclick, kind], i) => {
    const sep = kind === "danger" && i > 0 && items[i - 1][2] !== "danger" ? `<hr role="separator">` : "";
    // ‏#1334: פריט כבוי מוצג עם הסיבה (בתווית) ואינו מבצע דבר.
    if (kind === "disabled") return `${sep}<button type="button" role="menuitem" tabindex="-1" aria-disabled="true" disabled>${esc(label)}</button>`;
    return `${sep}<button type="button" role="menuitem" tabindex="-1"${kind === "danger" ? ` class="dg"` : ""} onclick="closeRowMenu(true);${onclick}">${esc(label)}</button>`;
  }).join("");
}
function openRowMenu(btn, focusLast = false) {
  if (ROW_MENU_BTN === btn) { closeRowMenu(true); return; }
  closeRowMenu(false);
  let items = [];
  try { items = JSON.parse(btn.getAttribute("data-menu") || "[]"); } catch (e) { items = []; }
  if (!items.length) return;
  const menu = rowMenuEl();
  if (!ROW_MENU_WATCH && typeof MutationObserver !== "undefined") {   // הדף צויר מחדש — הכפתור שפתח כבר לא קיים
    const host = document.getElementById("content");
    if (host) { ROW_MENU_WATCH = new MutationObserver(() => closeRowMenu(false)); ROW_MENU_WATCH.observe(host, { childList: true }); }
  }
  menu.innerHTML = rowMenuHtml(items);
  menu.setAttribute("aria-label", btn.getAttribute("aria-label") || "");
  menu.classList.remove("open");
  const r = btn.getBoundingClientRect(), w = menu.offsetWidth || 180, h = menu.offsetHeight || 0;
  const rtl = (document.documentElement.getAttribute("dir") || "rtl") === "rtl";
  let left = rtl ? r.left : r.right - w;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  let top = r.bottom + 4;
  if (h && top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
  menu.style.left = left + "px"; menu.style.top = top + "px";
  menu.style.setProperty("--ox", (r.left + r.width / 2 - left) + "px");
  menu.classList.add("open");
  btn.setAttribute("aria-expanded", "true");
  ROW_MENU_BTN = btn;
  const all = menu.querySelectorAll("[role=menuitem]");
  const first = all[focusLast ? all.length - 1 : 0];
  if (first && first.focus) first.focus();
}
function closeRowMenu(restoreFocus) {
  const btn = ROW_MENU_BTN;
  ROW_MENU_BTN = null;
  const menu = document.getElementById("rowMenu");
  if (menu) menu.classList.remove("open");
  if (!btn) return;
  btn.setAttribute("aria-expanded", "false");
  if (restoreFocus && btn.isConnected !== false && btn.focus) btn.focus();
}
function rowMenuTriggerKey(event, btn) {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); event.stopPropagation(); if (ROW_MENU_BTN === btn) closeRowMenu(false); openRowMenu(btn, event.key === "ArrowUp"); }
}
function rowMenuKey(event) {
  const menu = document.getElementById("rowMenu");
  const items = Array.from(menu.querySelectorAll("[role=menuitem]"));
  const at = items.indexOf(document.activeElement);
  const to = (i) => { event.preventDefault(); items[(i + items.length) % items.length].focus(); };
  if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeRowMenu(true); }
  else if (event.key === "ArrowDown") to(at + 1);
  else if (event.key === "ArrowUp") to(at < 0 ? -1 : at - 1);
  else if (event.key === "Home") to(0);
  else if (event.key === "End") to(items.length - 1);
  else if (event.key === "Tab") closeRowMenu(true);
}
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("click", () => closeRowMenu(false));
  window.addEventListener("scroll", () => closeRowMenu(false), true);
  window.addEventListener("resize", () => closeRowMenu(false));
}

/* תאריך dd/mm/yyyy ושעה hh:mm מחותמת ISO (#829 M11). */
/* רצף מספר+יחידה או תאריך+שעה בתוך משפט עברי: הרווח שביניהם נפתר RTL
   (UBA N1 — מספר נחשב R) והרצף מתהפך ("GB 38.4"). מבודדים אותו כ-LTR. */
function ltr(text) { return `<bdi dir="ltr">${esc(text)}</bdi>`; }
/* ‏#1933 (נדב 21/10): "עודכן 02:22" כשבישראל היה 05:22 — השעה נחתכה ממחרוזת ה-ISO, שהיא UTC.
   כל זמן למפעיל מוצג באזור הזמן של השרת (‏/me.timezone, ההגדרה של #1447) — לא של הדפדפן (מנהל
   מתחבר מכל מקום) ולא UTC כלשונו. החותמות עצמן נשארות UTC (now_iso). אזור לא ידוע (שרת ישן, או
   אזור שהדפדפן אינו מכיר) → UTC **עם הסימון "UTC"** בשעה — לא UTC בשקט (עיקרון 5). בהקשר שרת משנה
   ‏ME יורש את האזור של הראשי: התצוגה היא של הקונסולה שהמפעיל יושב מולה. */
let TZ_FMT = null;   // [אזור, Intl.DateTimeFormat] — נבנה פעם אחת לכל אזור
function displayZone() { return (ME && typeof ME.timezone === "string" && ME.timezone) || null; }
/* חותמת של השרת → מילישניות. בלי היסט = UTC (ה-DB שומר UTC); תאריך בלבד או זבל → NaN. */
function tsMs(ts) {
  const m = String(ts || "").match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$/i);
  if (!m) return NaN;
  const off = !m[4] || /z/i.test(m[4]) ? "Z" : m[4].replace(/^([+-]\d{2})(\d{2})$/, "$1:$2");
  return Date.parse(`${m[1]}T${m[2]}:${m[3] || "00"}${off}`);
}
/* רגע → שדות שעון-הקיר באזור התצוגה; ‏utc=true כשהאזור לא ידוע. */
function zoneParts(ms) {
  const zone = displayZone();
  if (zone) {
    try {
      if (!TZ_FMT || TZ_FMT[0] !== zone) {
        TZ_FMT = [zone, new Intl.DateTimeFormat("en-GB", { timeZone: zone, hourCycle: "h23", year: "numeric",
          month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })];
      }
      const p = {};
      for (const x of TZ_FMT[1].formatToParts(new Date(ms))) p[x.type] = x.value;
      return { y: p.year, mo: p.month, d: p.day, h: p.hour, mi: p.minute, s: p.second, utc: false };
    } catch (e) { /* אזור שהדפדפן אינו מכיר — UTC מסומן */ }
  }
  const iso = new Date(ms).toISOString();
  return { y: iso.slice(0, 4), mo: iso.slice(5, 7), d: iso.slice(8, 10), h: iso.slice(11, 13), mi: iso.slice(14, 16), s: iso.slice(17, 19), utc: true };
}
function zoneDay(ms) { const p = zoneParts(ms); return `${p.y}-${p.mo}-${p.d}`; }
function fmtDate(ts) {
  const ms = tsMs(ts);
  if (Number.isFinite(ms)) { const p = zoneParts(ms); return `${p.d}/${p.mo}/${p.y}`; }
  const m = String(ts || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "—";
}
function fmtClock(ts) {
  const ms = tsMs(ts);
  if (!Number.isFinite(ms)) return "—";
  const p = zoneParts(ms);
  return `${p.h}:${p.mi}${p.utc ? " UTC" : ""}`;
}
/* "היום" = היום של השרת, באזור התצוגה — לא יום ה-UTC (בין 00:00 ל-03:00 בישראל זה עוד "אתמול"). */
function isToday(ts, now = Date.now()) {
  const ms = tsMs(ts);
  const day = Number.isFinite(ms) ? zoneDay(ms) : (String(ts || "").match(/^\d{4}-\d{2}-\d{2}$/) || [""])[0];
  return !!day && day === zoneDay(now);
}
/* ‏#1933: שעון-קיר באזור התצוגה (‏"YYYY-MM-DD[THH:MM[:SS]]", כמו datetime-local) → ISO ב-UTC, הציר שהשרת
   משווה עליו. ההיסט נמדד ברגע עצמו (פעמיים — שעון קיץ), לא קבוע. */
function wallToUtc(wall) {
  const m = String(wall || "").match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?$/);
  if (!m) return "";
  const asUtc = Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  const offset = (ms) => { const p = zoneParts(ms); return Date.UTC(+p.y, +p.mo - 1, +p.d, +p.h, +p.mi, +p.s) - ms; };
  const guess = asUtc - offset(asUtc);
  return new Date(asUtc - offset(guess)).toISOString().slice(0, 19);
}

/* ===== מסגרת החלונות האחידה (שלב 1, windows.html) =====
   sheet() / openModalContent() / openDrawer() הם שלוש הכניסות היחידות לחלון, וכולם מציירים אותה מסגרת:
   כותרת + שורת משנה + X תמיד, גוף בסקשנים, פס תחתון קבוע (ראשי בקצה, ביטול לידו, הרסני אדום ומופרד).
   רוחב לפי סוג: narrow 440 · mid 600 · wide 860 (אשף). אין כאן לוגיקה עסקית — רק מסגרת. */
const WIN_SIZES = { narrow: "n-narrow", mid: "n-mid", wide: "n-wide" };
const winSizeClass = (size, dflt = "mid") => WIN_SIZES[size] || WIN_SIZES[dflt];
const winIcon = (d) => `<svg class="n-ic" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const WIN_ICONS = {
  x: winIcon('<path d="m6 6 12 12M18 6 6 18"/>'),
  err: winIcon('<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5h.01"/>'),
  ok: winIcon('<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16.5 9.5"/>'),
  up: winIcon('<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>'),
};
const winCloseBtn = (attrs) => `<button type="button" class="n-x" aria-label="סגור" ${attrs}>${WIN_ICONS.x}</button>`;

/* רכיב סיסמה אחד: "הצג/הסתר" + מדיניות חיה. המדיניות זהה לשרת (server/users.py validate_password):
   8 תווים, אותיות, ספרות, ותו מהקבוצה !@#$%^&*(),.?":{}|<> — לא "כל תו", אחרת הרשימה תהיה ירוקה והשרת יסרב. */
const PW_RULES = [["לפחות 8 תווים", /.{8,}/], ["אותיות", /[A-Za-z]/], ["ספרות", /[0-9]/], ["תו מיוחד", /[!@#$%^&*(),.?":{}|<>]/]];
/* ‏value + readonly (#1268): סיסמה זמנית שמוצגת פעם אחת — אותו שדה ואותו "הצג", רק לקריאה. */
function passwordFieldHtml({ id, label, policy = false, autocomplete = "new-password", value = "", readonly = false }) {
  const rules = policy ? `<ul class="pw-pol" aria-live="polite">${PW_RULES.map(([name], i) => `<li data-rule="${i}">${LOGIN_CHECK_ICON}${esc(name)}</li>`).join("")}</ul>` : "";
  const shown = (value ? ` value="${esc(value)}"` : "") + (readonly ? " readonly" : "");
  return `<div class="n-f" data-pwf><label for="${id}">${esc(label)}</label><div class="pw-wrap"><input class="n-in" type="password" dir="ltr" id="${id}" autocomplete="${autocomplete}"${shown}><button type="button" class="pw-eye" data-pw="${id}" aria-pressed="false">הצג</button></div>${rules}</div>`;
}
function passwordPolicyState(value) { return PW_RULES.map(([, re]) => re.test(value)); }
function wirePasswordFields(root) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll("[data-pwf]").forEach((box) => {
    const input = box.querySelector("input"), eye = box.querySelector(".pw-eye");
    if (!input || (box.dataset && box.dataset.wired)) return;
    if (box.dataset) box.dataset.wired = "1";
    if (eye) eye.onclick = () => {
      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      eye.textContent = showing ? "הצג" : "הסתר";
      eye.setAttribute("aria-pressed", String(!showing));
    };
    const items = box.querySelectorAll(".pw-pol li");
    if (items.length) input.addEventListener("input", () => {
      const state = passwordPolicyState(input.value);
      items.forEach((li, i) => { li.classList.toggle("ok", state[i]); });
    });
  });
}

/* רכיב העלאה אחד לכל החלונות: גרירה או לחיצה; ה-<input type=file> האמיתי נשאר (id של הקורא) ולכן
   הקוד ששולח את הקובץ לא משתנה. */
function dropzoneHtml({ id, accept = "", label = "גררו קובץ לכאן או לחצו לבחירה", help = "", aria = "בחירת קובץ" }) {
  return `<div class="n-drop" data-dz role="button" tabindex="0" aria-label="${esc(aria)}"><span class="dz-i">${WIN_ICONS.up}</span><strong class="dz-t">${esc(label)}</strong>${help ? `<span class="n-help">${help}</span>` : ""}<input type="file" id="${id}" accept="${esc(accept)}" class="sr-only" tabindex="-1" aria-hidden="true"></div>`;
}
function wireDropzones(root) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll("[data-dz]").forEach((dz) => {
    const input = dz.querySelector("input[type=file]");
    if (!input || (dz.dataset && dz.dataset.wired)) return;
    if (dz.dataset) dz.dataset.wired = "1";
    const title = dz.querySelector(".dz-t"), idle = title ? title.textContent : "";
    const show = () => {
      const f = input.files && input.files[0];
      dz.classList.toggle("has", !!f);
      if (title) title.textContent = f ? f.name : idle;
      let meta = dz.querySelector(".dz-m");
      if (f) {
        if (!meta) { meta = document.createElement("span"); meta.className = "n-help dz-m"; dz.appendChild(meta); }
        meta.innerHTML = `${ltr(fmtBytes(f.size))} · ללחוץ כדי להחליף`;
      } else if (meta) meta.remove();
    };
    dz.addEventListener("click", (e) => { if (e.target !== input) input.click(); });
    dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("on"); }));
    dz.addEventListener("dragleave", () => dz.classList.remove("on"));
    dz.addEventListener("drop", (e) => {
      e.preventDefault(); dz.classList.remove("on");
      const files = e.dataTransfer && e.dataTransfer.files;
      if (files && files.length) { try { input.files = files; } catch (err) { /* דפדפן שמסרב — הבחירה בלחיצה עדיין עובדת */ } show(); }
    });
    input.addEventListener("change", show);
  });
}
function wireWindowParts(root) { wirePasswordFields(root); wireDropzones(root); }

/* רכיב האשף: צעדים בצד, גוף משתנה, אחורה/הבא. render(step, state, body) מצייר את הצעד; valid(step, state)
   קובע אם "הבא" פעיל. נבנה על אותה מסגרת (n-wide), ובלי לוגיקה של חלון מסוים. */
function openWizard({ title, sub = "", steps, state = {}, finishLabel = "סיום", onFinish }) {
  let at = 0;
  const html = `<div class="n-wiz"><ol class="n-steps" id="wz-steps">${steps.map((s, i) => `<li data-i="${i}"><span class="num"></span><span class="lbl">${esc(s.label)}</span></li>`).join("")}</ol><div class="n-wcol"><div class="n-b" id="wz-body"></div></div></div>`;
  openModalContent(title, html, finishLabel, () => {}, false, sub, "wide");
  const ok = document.getElementById("modalOk"), body = () => document.getElementById("wz-body");
  const hint = document.getElementById("modalHint");
  const back = document.createElement("button");
  back.type = "button"; back.className = "btn"; back.id = "wz-back"; back.textContent = "הקודם";
  if (ok.parentNode && ok.parentNode.insertBefore) ok.parentNode.insertBefore(back, ok);
  const draw = () => {
    document.querySelectorAll("#wz-steps li").forEach((li, i) => {
      li.className = i < at ? "done" : i === at ? "cur" : "";
      li.querySelector(".num").textContent = i < at ? "✓" : String(i + 1);
    });
    steps[at].render(body(), state);
    const good = !steps[at].valid || steps[at].valid(state);
    ok.disabled = !good;
    ok.textContent = at === steps.length - 1 ? finishLabel : "הבא";
    back.style.visibility = at ? "visible" : "hidden";
    if (hint) hint.textContent = good ? "" : (steps[at].hint || "");
  };
  back.onclick = () => { at--; draw(); };
  ok.onclick = async () => {
    if (at < steps.length - 1) { at++; draw(); return; }
    try { await onFinish(state); closeModal(); } catch (e) { toast(e.message); }
  };
  state.redraw = draw;
  draw();
}

/* מודאל אחיד במקום prompt/confirm — net.js/netcfg.js קוראים ל-sheet().
   אפשרויות מעבר לקודמות: size (narrow|mid|wide), hint (טקסט בפס התחתון), field.help, field.type="section".
   cancelLabel; ו-onSubmit יכול להחזיר פונקציה — היא רצה אחרי שהחלון נסגר (חלון תוצאה אחרי חלון אישור, #1268). */
/* ‏"מה יקרה" בחלון הרסני (console-full.html .n-impact): [סוג, טקסט HTML מוברח]. ‏b = נמחק/אובד (אדום), w = אזהרה, ok = לא נפגע.
   רק עובדות שהקוד כבר יודע — לא "לא בשימוש" בלי ראיה (עיקרון 5). */
const IMPACT_ICONS = {
  b: '<svg class="n-ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
  w: '<svg class="n-ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4 2.8 20h18.4z"/><path d="M12 10v4.5M12 17.5h.01"/></svg>',
  ok: '<svg class="n-ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16.5 9.5"/></svg>',
};
function impactList(items) {
  return `<ul class="n-impact">${items.map(([kind, html]) => `<li class="${kind === "b" ? "bad" : kind === "ok" ? "imp-ok" : "imp-w"}">${IMPACT_ICONS[kind] || IMPACT_ICONS.w}<span>${html}</span></li>`).join("")}</ul>`;
}
/* נדב 18/10 (rc6f): ‏onSubmit שפותח את החלון הבא בעצמו (רישום שרת משנה: "בדוק SPKI" → שלב 2)
   מצייר אותו לתוך אותו #sheet — וה-close() שאחרי ה-await סגר את **החלון החדש**: שלב 2
   הופיע לשנייה ונעלם, בלי בקשה נוספת לשרת. כל sheet() מקבל מספר; החלון נסגר אחרי
   onSubmit רק אם הוא עדיין החלון שפתוח. */
let SHEET_GEN = 0;

function sheet({ title, sub = "", fields = [], submitLabel = "שמירה", cancelLabel = "ביטול",
                 danger = false, note = "", size = "", hint = "", extra = null, onSubmit }) {
  const gen = ++SHEET_GEN;
  const modal = $("#modal");
  const form = $("#sheet");
  const help = (f) => (f.help ? `<div class="n-help">${esc(f.help)}</div>` : "");
  const fieldHtml = (f) => {
    if (f.type === "section") return `<h3 class="n-sec-h">${esc(f.label)}</h3>`;
    if (f.type === "select") {
      return `<div class="n-f"><label for="sf-${f.id}">${esc(f.label)}</label>
        <select class="n-in" id="sf-${f.id}">${(f.options || []).map((o) =>
          `<option value="${esc(o.value)}" ${o.value === f.value ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>${help(f)}</div>`;
    }
    if (f.type === "checkbox") {
      return `<div class="n-f"><label class="n-sw check"><input type="checkbox" role="switch" id="sf-${f.id}" ${f.value ? "checked" : ""}><span class="track"></span><span>${esc(f.label)}</span></label>${help(f)}</div>`;
    }
    if (f.type === "radio") {
      return `<fieldset class="n-f radio-group" id="sf-${f.id}">
        ${f.label ? `<legend class="n-lbl">${esc(f.label)}</legend>` : ""}
        <div class="n-opts">${(f.options || []).map((o) =>
          `<label class="n-opt"><input type="radio" name="sf-${f.id}" value="${esc(o.value)}" ${o.value === f.value ? "checked" : ""}><strong>${esc(o.label)}</strong>${o.hint ? `<small>${esc(o.hint)}</small>` : ""}</label>`).join("")}</div>${help(f)}
      </fieldset>`;
    }
    if (f.type === "segment") {
      return `<div class="n-f"><label class="n-lbl" id="sf-${f.id}-label">${esc(f.label)}</label>
        <div class="n-seg" role="group" aria-labelledby="sf-${f.id}-label" data-segment="${esc(f.id)}">${(f.options || []).map((o) =>
          `<button type="button" data-value="${esc(o.value)}" aria-pressed="${o.value === f.value}">${esc(o.label)}</button>`).join("")}</div>
        <input type="hidden" id="sf-${f.id}" value="${esc(f.value || "")}">${help(f)}</div>`;
    }
    if (f.type === "textarea") {
      return `<div class="n-f"><label for="sf-${f.id}">${esc(f.label)}</label>
        <textarea class="n-in" id="sf-${f.id}" rows="3">${esc(f.value || "")}</textarea>${help(f)}</div>`;
    }
    if (f.type === "password") {
      return passwordFieldHtml({ id: "sf-" + f.id, label: f.label, policy: f.policy === undefined ? !!f.confirm : f.policy, autocomplete: f.autocomplete || undefined })
        + (f.confirm ? passwordFieldHtml({ id: "sf-" + f.id + "-confirm", label: f.confirm }) : "");
    }
    return `<div class="n-f"><label for="sf-${f.id}">${esc(f.label)}</label>
      <input class="n-in${f.dir === "ltr" ? " n-mono" : ""}" type="${f.type || "text"}" id="sf-${f.id}" value="${esc(f.value || "")}"
        placeholder="${esc(f.placeholder || "")}" ${f.dir ? `dir="${f.dir}"` : ""}>${help(f)}</div>`;
  };
  const sizeName = size || (fields.length >= 6 ? "mid" : "narrow");
  form.className = `sheet win ${winSizeClass(sizeName, "narrow")}`;
  form.innerHTML = `
    <div class="n-h shead"><div><h2 id="sheetTitle">${esc(title)}</h2>${sub ? `<p>${esc(sub)}</p>` : ""}</div>${winCloseBtn('id="sf-x"')}</div>
    <div class="n-b sbody">${SERVER_CTX ? `<div class="sheet-note${danger ? " danger" : ""}" data-target-server>הפעולה תתבצע על <b>${esc(serverRootName("שרת המשנה"))}</b> (שרת משנה), דרך הערוץ בין השרתים.</div>` : ""}${note}${fields.map(fieldHtml).join("")}<p class="error n-formerr" id="sf-error" role="alert"></p></div>
    <footer class="n-f-bar sfoot">
      <span class="n-hint" id="sf-hint">${esc(hint)}</span>
      <button class="btn" type="button" id="sf-cancel">${esc(cancelLabel)}</button>
      ${extra ? `<button class="btn" type="button" id="sf-extra">${esc(extra.label)}</button>` : ""}
      <button class="btn ${danger ? "danger" : "primary"}" type="submit" id="sf-submit">${esc(submitLabel)}</button>
    </footer>`;
  form.classList.remove("hidden");
  openModalFrame(modal, "sheet-mode");
  const first = form.querySelector("input, textarea, select");
  if (first) first.focus();
  const close = () => {
    document.removeEventListener("keydown", onKey);
    if (gen !== SHEET_GEN) return;   // another sheet owns the window now -- it is not ours to close
    form.onsubmit = null;
    closeModal();
  };
  const onKey = (event) => { if (event.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  $("#sf-cancel").onclick = close;
  $("#sf-x").onclick = close;
  if (extra) $("#sf-extra").onclick = () => { close(); extra.onClick(); };   // כפתור שני בתחתית: סוגר ופותח את הבא
  wireWindowParts(form);
  form.querySelectorAll("[data-segment]").forEach((group) => group.querySelectorAll("button[data-value]").forEach((button) => {
    button.onclick = () => {
      group.querySelectorAll("button[data-value]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      const input = $("#sf-" + group.getAttribute("data-segment"));
      if (input) input.value = button.getAttribute("data-value");
    };
  }));
  form.onsubmit = async (event) => {
    event.preventDefault();
    for (const f of fields) {
      if (f.type !== "password" || !f.confirm) continue;
      if ($("#sf-" + f.id).value !== $(`#sf-${f.id}-confirm`).value) {
        $("#sf-error").textContent = "הסיסמאות אינן זהות";
        return;
      }
    }
    const values = {};
    fields.forEach((f) => {
      if (f.type === "section") return;
      if (f.type === "radio") {
        const checked = form.querySelector(`input[name="sf-${f.id}"]:checked`);
        values[f.id] = checked ? checked.value : "";
        return;
      }
      const el = $("#sf-" + f.id);
      values[f.id] = f.type === "checkbox" ? !!(el && el.checked) : (el ? el.value : "");
    });
    let next;
    try { next = await onSubmit(values); close(); }
    catch (error) { $("#sf-error").textContent = error.message; return; }
    if (typeof next === "function") next();
  };
}

/* ‏impacts (אופציונלי): [[סוג, HTML מוברח], ...] — רשימת "מה יקרה" (impactList). בלעדיו: sub כמו קודם. */
function confirmSheet(title, sub, submitLabel, onConfirm, impacts = null, danger = true) {
  sheet({ title, sub: impacts ? sub : sub, submitLabel, danger, onSubmit: onConfirm, note: impacts ? impactList(impacts) : "" });
}

/* ---------- ערכת צבעים ---------- */
/* #1093: נשמר בשרת (users.theme): auto / light / dark — ערך אחד לקונסולה
   ול-GUI של מחשב הבנייה. ‏#1499 (נדב 16/10): ברירת המחדל לכולם בהיר —
   auto = בהיר, ושום דבר לא עוקב אחרי מערכת ההפעלה. המתג מחליף בין בהיר
   לכהה ונשמר למשתמש. localStorage הוא מטמון בלבד — מונע הבהוב בטעינה
   כשהבחירה אינה auto, ונמחק ב-logout וב-auto. */
const THEME_KEY = "imagectl-theme";
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

/* מתגי הצגת סיסמה במסך הכניסה — delegation ב-bindLogin. */

/* ---------- ניתוק אוטומטי בחוסר פעילות ---------- */
/* פעילות = עכבר/מקלדת/מגע של אדם. לא בקשות רשת: הלוח מתשאל את השרת
   כל 2 שניות, ואילו זה נחשב פעילות — אף אחד לא היה מנותק לעולם.
   המסך עומד בכיתה; מי שקם והלך לא משאיר קונסולה פתוחה. */
let lastActivity = Date.now();
let idleTimer = null;
let idleWarned = false;

function noteActivity() {
  lastActivity = Date.now();
  idleWarned = false;
}

["pointerdown", "keydown", "wheel", "touchstart", "mousemove"].forEach((type) =>
  document.addEventListener(type, noteActivity, { passive: true }));

/* ‏#1854: קלט בתוך המוניטור (iframe או חלון נפרד) אינו מגיע ל-document הזה —
   ‏monitor.js מודיע עליו ב-BroadcastChannel (אותו origin, הודעת "פעילות" בלי
   תוכן). רק הכיוון מוניטור → קונסולה; דבר אינו נשלח מכאן. */
function listenMonitorActivity() {
  if (typeof BroadcastChannel !== "function") return null;
  const channel = new BroadcastChannel("imagectl-activity");
  channel.onmessage = () => noteActivity();
  return channel;
}
listenMonitorActivity();

function startIdleWatch() {
  clearInterval(idleTimer);
  if (!ME || !ME.idle_seconds) return;
  noteActivity();
  let inFlight = false;
  idleTimer = setInterval(async () => {
    const idle = (Date.now() - lastActivity) / 1000;
    const left = ME.idle_seconds - idle;
    if (left <= 0) {
      if (inFlight) return;
      inFlight = true;
      try {
        await post("/logout");
        clearInterval(idleTimer);
        ME = null;
        showLogin();
        $("#login-error").textContent = "נותקת עקב חוסר פעילות.";
      } catch (error) {
        toast("הניתוק לא אושר (" + error.message + ") — הסשן עדיין פעיל. מנסה שוב.");
      } finally {
        inFlight = false;
      }
    } else if (left <= 30 && !idleWarned) {
      idleWarned = true;
      toast(`ניתוק בעוד ${Math.ceil(left)} שניות עקב חוסר פעילות`);
    }
  }, 1000);
}

/* ---------- כניסה (#1085 שלב ב') ----------
   חמשת המסכים מ-loginHtml(state) לפי המוקאפ. ה-API לא משתנה. */

const PW_SPECIAL_RE = /[!@#$%^&*(),.?":{}|<>]/;
const LOGIN_ALERT_ICON = '<svg class="ui-icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>';
const LOGIN_CHECK_ICON = '<i><svg class="ui-icon" viewBox="0 0 24 24"><path d="M5 13l4 4L19 7"/></svg></i>';

let LOGIN = { screen: "login", username: "", error: "",
              challenge: "", secret: "", otpauth: "", svg: "", backupCodes: [],
              savedAck: false, otpBackup: false, otp: ["", "", "", "", "", ""] };

function passwordPolicy(pw) {
  const v = String(pw || "");
  const len = v.length >= 8;
  const alpha = /[A-Za-z]/.test(v);
  const digit = /\d/.test(v);
  const special = PW_SPECIAL_RE.test(v);
  return { len, alpha, digit, special, all: len && alpha && digit && special };
}

function passwordCanSubmit(current, next, confirm) {
  return !!(passwordPolicy(next).all && String(current || "").length > 0
            && next === confirm && String(next).length > 0);
}

function setupContinueEnabled(ack) { return !!ack; }

function secretBlocks(secret) {
  return String(secret || "").replace(/\s+/g, "").replace(/(.{4})/g, "$1 ").trim();
}

function otpFromPaste(text) {
  const d = String(text || "").replace(/\D/g, "").slice(0, 6).split("");
  while (d.length < 6) d.push("");
  return d;
}

function loginErrorMessage(status, body, retryAfter) {
  body = body || {};
  if (status === 401) return "שם משתמש או סיסמה שגויים";
  if (status === 403 && body.error === "deploy_no_console")
    return body.message_he || "משתמש הפצה עובד ממחשב הבנייה, לא מהקונסולה";
  if (status === 429) {
    const sec = Number(retryAfter);
    const mins = Number.isFinite(sec) && sec > 0 ? Math.max(1, Math.ceil(sec / 60)) : 15;
    return "החשבון נעול ל-" + mins + " דקות";
  }
  if (status === 403 && retryAfter) {
    const sec = Number(retryAfter);
    const mins = Number.isFinite(sec) && sec > 0 ? Math.max(1, Math.ceil(sec / 60)) : 15;
    return "החשבון נעול ל-" + mins + " דקות";
  }
  return body.message_he || body.detail || ("שגיאה " + status);
}

function loginStateFromResponse(status, body, retryAfter, prev) {
  prev = prev || {};
  body = body || {};
  if (status === 200 && body.must_change_password)
    return { screen: "change", username: prev.username || "", error: "" };
  if (status === 200 && body.mfa_required)
    return { screen: "mfa", username: prev.username || "", challenge: body.challenge,
             otp: ["", "", "", "", "", ""], otpBackup: false, error: "" };
  if (status === 200 && body.mfa_enrollment_required)
    return { screen: "setup", username: prev.username || "", error: "" };
  if (status === 200 && (body.username || body.ok || body.mfa_enabled))
    return { screen: "app" };
  return { screen: "login", username: prev.username || "",
           error: loginErrorMessage(status, body, retryAfter), fieldErr: true };
}

function mfaRejectedState(body, prev) {
  // ‏#1120: קוד שגוי צורך את ה-challenge בשרת — אין למה להישאר במסך
  // הקוד. חזרה לכניסה עם השם שהוקלד ועם הודעת השרת כלשונה.
  prev = prev || {};
  return { screen: "login", username: prev.username || "",
           challenge: "", otp: ["", "", "", "", "", ""], otpBackup: false, fieldErr: false,
           error: (body && body.detail) || "קוד שגוי — היכנס מחדש" };
}

function loginMsg(text) {
  if (!text) return '<p class="msg" id="login-error" role="alert"></p>';
  return `<p class="msg" id="login-error" role="alert">${LOGIN_ALERT_ICON}<span>${esc(text)}</span></p>`;
}

function loginEye(id) {
  return `<button type="button" class="eye pw-eye" data-pw="${id}">הצג</button>`;
}

function loginFormHtml(state) {
  const err = state.fieldErr ? " err" : "";
  const user = esc(state.username || "");
  return `<form id="login-form">
    <h1 class="title">כניסה</h1>
    <div class="f${err}"><label for="login-user">שם משתמש</label>
      <input id="login-user" type="text" dir="ltr" autocomplete="username" value="${user}" autofocus></div>
    <div class="f pw${err}"><label for="login-pass">סיסמה</label>
      <input id="login-pass" type="password" dir="ltr" autocomplete="current-password">${loginEye("login-pass")}</div>
    ${state.notice ? `<p class="msg ok" id="login-notice" role="status"><span>${esc(state.notice)}</span></p>` : ""}${loginMsg(state.error)}
    <button class="btn" type="submit">כניסה</button>
  </form>`;
}

function loginChangeHtml(state) {
  const rules = passwordPolicy(state.next || "");
  const mismatch = !!(state.confirm && state.confirm !== state.next);
  const ready = passwordCanSubmit(state.current, state.next, state.confirm);
  const row = (key, label) =>
    `<li data-rule="${key}" class="${rules[key] ? "ok" : ""}">${LOGIN_CHECK_ICON}${label}</li>`;
  return `<form id="pw-change-form">
    <h1 class="title sub">בחר סיסמה חדשה</h1>
    <p class="lead">הסיסמה הראשונית של <span class="mono">${esc(state.username || "")}</span> חייבת להתחלף לפני הכניסה.</p>
    <div class="f pw"><label for="pw-change-current">סיסמה נוכחית</label>
      <input id="pw-change-current" type="password" dir="ltr" autocomplete="current-password" value="${esc(state.current || "")}">${loginEye("pw-change-current")}</div>
    <div class="f pw"><label for="pw-change-new">סיסמה חדשה</label>
      <input id="pw-change-new" type="password" dir="ltr" autocomplete="new-password" value="${esc(state.next || "")}">${loginEye("pw-change-new")}</div>
    <ul class="rules" id="pw-rules" aria-live="polite">
      ${row("len", "לפחות 8 תווים")}${row("alpha", "אותיות")}${row("digit", "ספרות")}${row("special", "תו מיוחד (‏!@#$…)")}
    </ul>
    <div class="f pw${mismatch ? " err" : ""}" id="pw-change-confirm-wrap"><label for="pw-change-confirm">אימות הסיסמה החדשה</label>
      <input id="pw-change-confirm" type="password" dir="ltr" autocomplete="new-password" value="${esc(state.confirm || "")}">${loginEye("pw-change-confirm")}</div>
    ${mismatch ? `<p class="msg" role="alert">${LOGIN_ALERT_ICON}<span>הסיסמאות אינן זהות</span></p>` : ""}
    ${loginMsg(state.error)}
    <button class="btn" type="submit" id="pw-change-go"${ready ? "" : " disabled"}>שמור והמשך</button>
  </form>`;
}

function loginMfaHtml(state) {
  const otp = (state.otp || otpFromPaste("")).slice(0, 6);
  const cells = otp.map((d, i) =>
    `<input dir="ltr" inputmode="numeric" maxlength="1" aria-label="ספרה ${i + 1}" value="${esc(d)}" data-otp="${i}">`).join("");
  const backup = state.otpBackup
    ? `<div class="f"><label for="mfa-backup">קוד גיבוי</label>
         <input id="mfa-backup" class="mono" type="text" dir="ltr" autocomplete="one-time-code" value="${esc(state.backupCode || "")}"></div>`
    : `<div class="otp" id="otp" aria-label="קוד אימות בן 6 ספרות">${cells}</div>`;
  return `<form id="mfa-form">
    <h1 class="title sub">הקוד מאפליקציית האימות</h1>
    <p class="lead"><span class="mono">${esc(state.username || "")}</span> · שישה תווים, מתחלף כל 30 שניות</p>
    ${backup}
    ${loginMsg(state.error)}
    <button class="btn" type="submit">אימות</button>
    ${state.otpBackup ? "" : `<button type="button" class="link" id="mfa-use-backup">השתמש בקוד גיבוי</button>`}
  </form>`;
}

function loginSetupHtml(state) {
  // ‏#1150: בלי svg = python3-qrcode חסר בשרת. אומרים זאת בשם, לא מסתירים.
  const qr = state.svg
    ? `<div class="qr" aria-label="קוד QR להגדרת האפליקציה">${state.svg}</div>`
    : `<p class="msg" role="alert">${LOGIN_ALERT_ICON}<span>אין QR — החבילה python3-qrcode חסרה בשרת; הקלד את הסוד ידנית</span></p>`;
  const account = `ImageSrv:${state.username || ""}`;
  return `<form id="setup-form">
    <h1 class="title sub">הגדרת אימות דו-שלבי</h1>
    <p class="lead">סרוק באפליקציית אימות (Google Authenticator, Microsoft Authenticator, Aegis) והקלד את הקוד שהיא מציגה.</p>
    <div class="qr-row">${qr}<div class="secret"><span>חשבון: <span class="mono">${esc(account)}</span></span>
      <div class="f pw"><label for="setup-secret">או הקלד את הסוד ידנית</label><input id="setup-secret" class="mono" type="password" dir="ltr" readonly value="${esc(secretBlocks(state.secret))}">${loginEye("setup-secret")}</div>
    </div></div>
    <div class="f"><label for="setup-code">הקוד מהאפליקציה</label>
      <input id="setup-code" class="mono" dir="ltr" inputmode="numeric" maxlength="6" autocomplete="one-time-code" value="${esc(state.setupCode || "")}"></div>
    ${loginMsg(state.error)}
    <button class="btn" type="submit" id="setup-verify">אימות והפעלה</button>
  </form>`;
}

function loginCodesHtml(state) {
  const codes = state.backupCodes || [];
  const ready = setupContinueEnabled(state.savedAck);
  return `<form id="codes-form">
    <h1 class="title sub">האימות הדו-שלבי פעיל</h1>
    <p class="lead">מעכשיו כל כניסה תבקש קוד. אם הטלפון אובד, קוד גיבוי הוא הדרך היחידה להיכנס.</p>
    <div class="backup">
      <h2>8 קודי גיבוי — שמור אותם</h2>
      <p>כל קוד עובד פעם אחת. הם לא יוצגו שוב.</p>
      <div class="codes">${codes.map((c) => `<span>${esc(c)}</span>`).join("")}</div>
    </div>
    <div class="actions2">
      <button class="btn sec" type="button" id="codes-print">הדפס</button>
      <button class="btn sec" type="button" id="codes-copy">העתק</button>
    </div>
    <label class="check" style="margin-top:18px"><input type="checkbox" id="saved-ack"${state.savedAck ? " checked" : ""}> שמרתי את הקודים במקום בטוח</label>
    <button class="btn" type="submit" id="setup-done"${ready ? "" : " disabled"}>המשך לקונסולה</button>
  </form>`;
}

function loginHtml(state) {
  const s = state || LOGIN;
  if (s.screen === "change") return loginChangeHtml(s);
  if (s.screen === "mfa") return loginMfaHtml(s);
  if (s.screen === "setup") return loginSetupHtml(s);
  if (s.screen === "codes") return loginCodesHtml(s);
  return loginFormHtml(s);
}

function paintLoginFoot() {
  const host = $("#login-foot-host");
  if (host) host.textContent = (ME && ME.server_name) || (typeof location !== "undefined" && location.hostname) || "";
  const ver = $("#login-foot-ver");
  if (ver) ver.textContent = (ME && ME.version)
    ? `${ME.version}${ME.version_verified === false ? " · לא מאומת" : ""}` : "";
  const label = $("#login-foot-tls-label");
  const tls = $("#login-foot-tls");
  const fp = ME && ME.tls && ME.tls.fingerprint_sha256;
  if (label) label.textContent = fp ? "TLS " + tlsModeHe(ME.tls) : "";
  if (tls) {
    tls.textContent = fp ? fp.slice(0, 23) + "…" : "";
    tls.title = fp ? "SHA-256 " + fp : "";
  }
  /* מפרידים רק בין ערכים שקיימים — לפני כניסה אין גרסה ו-TLS, ואז "host · ·" היה שורה עם נקודות יתומות. */
  const has = (el) => !!(el && el.textContent);
  const sep1 = $("#login-foot-sep1"), sep2 = $("#login-foot-sep2");
  if (sep1) sep1.hidden = !(has(host) && has(ver));
  if (sep2) sep2.hidden = !(has(tls) && (has(ver) || has(host)));
}

function loginRender(state) {
  if (state) Object.assign(LOGIN, state);
  const body = $("#login-body");
  if (body) body.innerHTML = loginHtml(LOGIN);
  paintLoginFoot();
  const first = body && body.querySelector("input:not([type=checkbox])");
  if (first && typeof first.focus === "function") first.focus();
}

function showLogin() {
  ME = null;
  OVERVIEW = IMAGES = FOLDERS = MACHINES = GROUPS = HEALTH = NETCFG = USERS = JOURNAL = SETTINGS = null; LOG.rows = null;
  SERVER_CTX = null; PRIMARY_ME = null;   // ‏#1179
  SESSION_MACHINES = {group:null,list:[]};
  CAPTURE_TASKS = [];
  dockStore(false); HOME_DOCK_HAD = null;   // הכניסה הבאה — דוק סגור
  try {
    const dock = document.getElementById("taskPanel");
    if (dock && dock.classList.contains("open")) { dock.classList.remove("open"); document.body.classList.remove("tasks-open"); }
  } catch (e) { /* קוסמטי */ }
  clearInterval(pollTimer);
  closeDrawer(); closeModal();
  clearInterval(idleTimer);
  $("#login").classList.remove("hidden");
  $("#app").classList.add("hidden");
  applyTheme("auto");   // #1093/#1499: מסך הכניסה בהיר (ברירת המחדל); המטמון נמחק
  const keptUser = LOGIN.username || "";
  const keptErr = LOGIN.error || "";
  LOGIN = { screen: "login", username: keptUser, error: keptErr,
            challenge: "", secret: "", otpauth: "", svg: "", backupCodes: [],
            savedAck: false, otpBackup: false, otp: ["", "", "", "", "", ""] };
  loginRender();
}

/* נדב 18/10 (TA, b951c1fd: "התחברתי עכשיו, והלשוניות לא היו סגורות"): אחרי **כל** כניסה — העץ במצב
   ההתחלתי של index.html (TA פתוח; מלאי, תשתית וניהול מקופלים; כל שרת משנה מקופל) ו"סקירה כללית" נבחרת
   (showApp). הקונסולה אינה חוזרת לדף אחרון (אין hash, אין זיכרון דף); אבל ה-DOM של העץ נשאר בין
   יציאה לכניסה באותו דף — מה שהמשתמש פתח לפני היציאה (או ניתוק בחוסר פעילות) נשאר פתוח. המצב
   נלכד פעם אחת בטעינת הדף (TREE_INITIAL, init); רענון (F5) בתוך session קיים — init → showApp, בלי איפוס. */
let TREE_INITIAL = null;
function treeBoxOpen(box) { return !(box.hasAttribute("hidden") || (box.style && box.style.display === "none")); }
function treeSnapshot() {
  const srv = document.getElementById("srvTA");
  if (!srv) return null;
  const snap = { srvTA: treeBoxOpen(srv) };
  srv.querySelectorAll(".inventory-children[id]").forEach((box) => { snap[box.id] = treeBoxOpen(box); });
  return snap;
}
function treeSetOpen(box, on) {
  if (on) { box.removeAttribute("hidden"); if (box.style) box.style.removeProperty("display"); }
  else box.setAttribute("hidden", "");
  const owner = box.previousElementSibling;
  if (!owner || !owner.classList || !owner.classList.contains("inventory-node")) return;
  owner.setAttribute("aria-expanded", String(on));
  const arrow = owner.querySelector(".tree-arrow");
  if (arrow) { arrow.dataset.open = String(on); arrow.textContent = on ? "▾" : "▸"; }
}
function resetTreeToInitial() {
  for (const [id, on] of Object.entries(TREE_INITIAL || {})) {
    const box = document.getElementById(id);
    if (box) treeSetOpen(box, on);
  }
  const sec = document.getElementById("secondaryServers");   // כל שרת משנה — מקופל, וכל מה שבתוכו
  if (sec) sec.querySelectorAll(".inventory-children").forEach((box) => treeSetOpen(box, false));
}

async function enterApp() {
  SERVER_CTX = null;
  resetTreeToInitial();   // נדב 18/10: אחרי כניסה — העץ ההתחלתי, לא מה שנשאר מלפני היציאה
  for (const k of Object.keys(SERVER_HEALTH)) delete SERVER_HEALTH[k];   // ונקודות המצב — נמדדות מחדש
  ME = PRIMARY_ME = await api("/me");
  LOGIN = { screen: "login", username: "", error: "",
            challenge: "", secret: "", otpauth: "", svg: "", backupCodes: [],
            savedAck: false, otpBackup: false, otp: ["", "", "", "", "", ""] };
  await showApp();
  restoreTaskDock();
}

async function showApp() {
  applyServerTheme(ME);
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  closeUserMenu();
  closeDrawer();
  closeModal();
  restoreNav();
  const userBtn = document.querySelector(".topbar .user");
  if (userBtn) {
    const avatar = userBtn.querySelector(".avatar");
    const label = userBtn.querySelector("span");
    if (avatar) avatar.textContent = ME.username.slice(0, 2);
    if (label) label.textContent = ME.username;
  }
  const menuHead = document.querySelector("#userMenu .user-menu-head strong");
  const menuSub = document.querySelector("#userMenu .user-menu-head small");
  if (menuHead) menuHead.textContent = ME.username;
  if (menuSub) menuSub.textContent = ME.role === "admin" ? "מנהל" : "הפצה";
  // ‏#915: הגרסה מגיעה מ-/me (תג git של עץ השרת) — לא מחרוזת ב-HTML.
  // ‏null = אין תג על העץ, ואז לא מציגים מספר שאין לו מקור.
  const versionEl = document.getElementById("serverVersion");
  if (versionEl) versionEl.textContent = ME.version
    ? `${ME.version}${ME.version_verified === false ? " · לא מאומת" : ""}` : "";
  // ‏#703 (tracer 5): מצב ה-TLS של הקונסולה מ-/me. טביעת האצבע (SHA-256
  // של התעודה, כמו בדפדפן) — מקוצרת בשורת הסטטוס, מלאה ב-title
  // וב"פרטי החיבור" — כדי שהאישור החד-פעמי של התעודה יהיה השוואה.
  const tlsEl = document.getElementById("serverTls");
  if (tlsEl) {
    const fp = ME.tls && ME.tls.fingerprint_sha256;
    tlsEl.innerHTML = fp ? `TLS ${tlsModeHe(ME.tls)} · <span dir="ltr">${esc(fp.slice(0, 23))}…</span>` : "";
    tlsEl.title = fp ? `SHA-256 ${fp}` : "";
  }
  document.querySelectorAll("[data-admin]").forEach(
    (el) => el.classList.toggle("hidden", ME.role !== "admin"));
  // רכיבים מותנים ביכולת נגזרת-שרת (#655/#723): מוצגים רק כשהדגל
  // המתאים ב-capabilities הוא true. הדגל הוא הסמכות — לא התפקיד לבדו.
  const caps = ME.capabilities || {};
  document.querySelectorAll("[data-cap]").forEach(
    (el) => el.classList.toggle("hidden", !el.dataset.cap.split("|").some((cap) => caps[cap])));   // "a|b" = אחת מהיכולות
  pages.deploy.tabs = deployTabs();
  applyServerName();
  renderActivity();
  selectPageById("home");
  if (isAdmin()) { loadMachines(); loadHealth(); }
  loadImages();
  populateSidebarImages();
  populateSidebarGroups();
  populateSidebarSecondaries().catch((e) => toast("טעינת שרתי המשנה נכשלה: " + e.message))
    .then(() => refreshServerDots());   // נדב 18/10: נקודת המצב של כל שרת בעץ
  if (isAdmin() && typeof loadNet === "function") {
    loadNet().catch((e) => toast("טעינת כרטיסי הרשת נכשלה: " + e.message));
  }
  if (isAdmin() && typeof nicsvcBoot === "function") nicsvcBoot();   // ‏#705: פס "ממתין לאישור" בכל דף
  updateAlertBadge();
  startIdleWatch();
  startStatusWatch();
  // ‏#1301 (update_screen.js): "עודכן ל-vX ✓" רק אם /me מדווח אותה גרסה; עדכון שרץ — המסך נפתח מעצמו.
  if (typeof updAfterReload === "function") updAfterReload();
  if (typeof updResume === "function") updResume().catch(() => {});
}

async function loginFetch(path, body) {
  const response = await apiFetch("/api/console" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await response.json(); } catch (e) {}
  return { ok: response.ok, status: response.status, body: data,
           retryAfter: response.headers.get("Retry-After") };
}

async function loginSubmitCredentials() {
  const username = ($("#login-user") && $("#login-user").value || "").trim();
  const password = ($("#login-pass") && $("#login-pass").value) || "";
  LOGIN.username = username;
  const r = await loginFetch("/login", { username, password });
  const next = loginStateFromResponse(r.status, r.body, r.retryAfter, LOGIN);
  if (next.screen === "app") { await enterApp(); return; }
  if (next.screen === "setup") { await loginStartSetup(next); return; }
  loginRender(next);
}

async function loginSubmitPassword() {
  const current = ($("#pw-change-current") && $("#pw-change-current").value) || "";
  const next = ($("#pw-change-new") && $("#pw-change-new").value) || "";
  const confirm = ($("#pw-change-confirm") && $("#pw-change-confirm").value) || "";
  if (!passwordCanSubmit(current, next, confirm)) return;
  const r = await loginFetch("/me/password", { current_password: current, new_password: next });
  if (!r.ok) { loginRender({ error: r.body.detail || r.body.message_he || "החלפת הסיסמה נכשלה" }); return; }
  if (r.body.mfa_enrollment_required) { await loginStartSetup({ screen: "setup", username: LOGIN.username }); return; }
  await enterApp();
}

function loginReadOtp() {
  if (LOGIN.otpBackup) return (($("#mfa-backup") && $("#mfa-backup").value) || "").trim();
  const cells = document.querySelectorAll("#otp input");
  let code = "";
  cells.forEach((c) => { code += (c.value || "").replace(/\D/g, "").slice(0, 1); });
  return code;
}

async function loginSubmitMfa() {
  const code = loginReadOtp();
  const r = await loginFetch("/login/mfa", {
    challenge: LOGIN.challenge, code,
  });
  if (r.status === 401) {
    loginRender(mfaRejectedState(r.body, LOGIN));
    return;
  }
  const next = loginStateFromResponse(r.status, r.body, r.retryAfter, LOGIN);
  if (next.screen === "app") { await enterApp(); return; }
  loginRender(next);
}

async function loginStartSetup(base) {
  const r = await loginFetch("/me/mfa/setup", {});
  if (!r.ok) { loginRender({ screen: "setup", username: (base && base.username) || LOGIN.username,
    error: r.body.detail || "הגדרת MFA נכשלה" }); return; }
  loginRender({
    screen: "setup", username: (base && base.username) || LOGIN.username, error: "",
    secret: r.body.secret || "", otpauth: r.body.otpauth_url || "", svg: r.body.svg || "",
    setupCode: "",
  });
}

async function loginSubmitSetup() {
  const code = (($("#setup-code") && $("#setup-code").value) || "").trim();
  const v = await loginFetch("/me/mfa/verify", { code });
  if (!v.ok) { loginRender({ error: "קוד שגוי", setupCode: code }); return; }
  const en = await loginFetch("/me/mfa/enable", { code });
  if (!en.ok) { loginRender({ error: en.body.detail || "קוד שגוי", setupCode: code }); return; }
  loginRender({ screen: "codes", backupCodes: en.body.backup_codes || [], savedAck: false, error: "" });
}

async function loginOnSubmit(event) {
  event.preventDefault();
  const id = event.target && event.target.id;
  try {
    if (id === "login-form") await loginSubmitCredentials();
    else if (id === "pw-change-form") await loginSubmitPassword();
    else if (id === "mfa-form") await loginSubmitMfa();
    else if (id === "setup-form") await loginSubmitSetup();
    else if (id === "codes-form") {
      if (!setupContinueEnabled(LOGIN.savedAck)) return;
      await enterApp();
    }
  } catch (error) {
    loginRender({ error: error.message });
  }
}

function loginToggleEye(eye) {
  const input = document.getElementById(eye.dataset.pw);
  if (!input) return;
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  eye.textContent = showing ? "הצג" : "הסתר";
}

function loginOnClick(event) {
  const eye = event.target.closest && event.target.closest("[data-pw]");
  if (eye) { loginToggleEye(eye); return; }
  if (event.target.id === "mfa-use-backup") {
    loginRender({ otpBackup: true, error: "" });
    return;
  }
  if (event.target.id === "codes-copy") {
    const text = (LOGIN.backupCodes || []).join("\n");
    if (navigator.clipboard && navigator.clipboard.writeText)
      navigator.clipboard.writeText(text).then(() => toast("הקודים הועתקו"), () => toast("ההעתקה נכשלה"));
    else toast("ההעתקה אינה זמינה");
    return;
  }
  if (event.target.id === "codes-print") {
    printBackupCodes(LOGIN.backupCodes || []);
  }
}

function printBackupCodes(codes) {
  const w = window.open("", "_blank");
  if (!w) { toast("הדפדפן חסם את חלון ההדפסה"); return; }
  w.document.write("<!doctype html><html dir=\"rtl\"><head><meta charset=\"utf-8\"><title>קודי גיבוי ImageSrv</title></head><body><h1>קודי גיבוי</h1><pre dir=\"ltr\">"
    + codes.map((c) => String(c)).join("\n") + "</pre></body></html>");
  w.document.close();
  w.print();
}

function loginOnInput(event) {
  const t = event.target;
  if (!t) return;
  if (t.id === "pw-change-current" || t.id === "pw-change-new" || t.id === "pw-change-confirm") {
    LOGIN.current = ($("#pw-change-current") && $("#pw-change-current").value) || "";
    LOGIN.next = ($("#pw-change-new") && $("#pw-change-new").value) || "";
    LOGIN.confirm = ($("#pw-change-confirm") && $("#pw-change-confirm").value) || "";
    const focused = t.id;
    loginRender();
    const again = document.getElementById(focused);
    if (again) { again.focus(); if (typeof again.setSelectionRange === "function") again.setSelectionRange(again.value.length, again.value.length); }
    return;
  }
  if (t.id === "saved-ack") {
    LOGIN.savedAck = !!t.checked;
    const btn = $("#setup-done");
    if (btn) btn.disabled = !setupContinueEnabled(LOGIN.savedAck);
    return;
  }
  if (t.dataset && t.dataset.otp != null) {
    t.value = t.value.replace(/\D/g, "").slice(0, 1);
    const i = Number(t.dataset.otp);
    if (t.value && t.parentNode) {
      const next = t.parentNode.querySelector(`[data-otp="${i + 1}"]`);
      if (next) next.focus();
    }
  }
}

function loginOnKeydown(event) {
  const t = event.target;
  if (!t || t.dataset.otp == null) return;
  if (event.key === "Backspace" && !t.value && t.parentNode) {
    const i = Number(t.dataset.otp);
    const prev = t.parentNode.querySelector(`[data-otp="${i - 1}"]`);
    if (prev) prev.focus();
  }
}

function loginOnPaste(event) {
  const t = event.target;
  if (!t || t.dataset.otp == null) return;
  const d = otpFromPaste((event.clipboardData && event.clipboardData.getData("text")) || "");
  if (d.filter(Boolean).length < 2) return;
  event.preventDefault();
  const cells = t.parentNode.querySelectorAll("[data-otp]");
  cells.forEach((c, k) => { c.value = d[k] || ""; });
  const last = Math.min(d.filter(Boolean).length, 5);
  if (cells[last]) cells[last].focus();
}

(function bindLogin() {
  const root = $("#login");
  if (!root || root.dataset.loginBound) return;
  root.dataset.loginBound = "1";
  root.addEventListener("submit", loginOnSubmit);
  root.addEventListener("click", loginOnClick);
  root.addEventListener("input", loginOnInput);
  root.addEventListener("keydown", loginOnKeydown);
  root.addEventListener("paste", loginOnPaste);
  loginRender({ screen: "login" });
})();

/* ‏#1179: בהקשר שרת משנה — לחיצה על צומת **של הראשי** בעץ יוצאת מההקשר לפני ה-onclick שלו
   (רוב הצמתים קוראים ל-selectPageById, שבהקשר שרת משנה היה נשאר בשרת המשנה); חץ פתיחה אינו ניווט.
   קישור הורדה של הקונסולה (CSV, אימג') היה מוריד קובץ של הראשי — נחסם. */
document.addEventListener("click", (event) => {
  const t = event.target;
  if (!SERVER_CTX || !t || !t.closest) return;
  const node = t.closest(".inventory-node");
  const handler = node ? node.getAttribute("onclick") || "" : "";
  if (node && handler && !handler.startsWith("toggleInventoryGroup") && !t.closest(".tree-arrow")
      && node.closest("#sidebar") && !node.closest("#secondaryServers")) setServerContext(null);
  if (t.closest('a[href^="/api/console/"]')) { event.preventDefault(); event.stopPropagation(); toast(SECONDARY_READ_ONLY); }
}, true);
$("#logout").addEventListener("click", async () => { await post("/logout"); ME = null; showLogin(); });

/* ---------- shell: עמודים, ניווט, drawer, modal ---------- */

function pagePlaceholder() {
  return '<div class="card"><div class="card-b">טוען נתונים…</div></div>';
}

function emptyDataCard() {
  return `<div class="grid"><div class="span-12"><div class="card"><div class="card-b"><div class="empty">אין נתונים להצגה</div></div></div></div></div>`;
}

function formatGB(bytes) {
  if (bytes == null) return "-";
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "—";
  return (n / (1024 ** 3)).toFixed(1) + " GB";
}

function fmtBytes(n) {
  if (n == null) return "–";
  let v = Number(n);
  if (!Number.isFinite(v) || v < 0) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(v >= 100 || i === 0 ? 0 : 1) + " " + units[i];
}

function librarySize(bytes) {
  return bytes == null ? "לא ידוע" : fmtBytes(bytes);
}

/* #953: "נכנס לדיסק מ-X GB" — GB עשרוני (כמו על מדבקת הכונן: 256GB =
   256×10⁹), מעוגל כלפי **מעלה** — הרצפה, לא קירוב. null = לא ניתן לקבוע. */
function fitGB(bytes) {
  const n = Number(bytes);
  if (bytes == null || !Number.isFinite(n) || n < 0) return null;
  return Math.ceil(n / 1e9) + " GB";
}

/* #953: שלושת המספרים של אימג' — לאיזה דיסק הוא נכנס (מ-`min_target_bytes`
   הנגזר, tooltip בבייטים), כמה תפוס בו (`used_bytes`; null = "לא ידוע",
   לא 0 — מחיצה שלא נעגנה, #84), וכמה הוא שוקל בשרת. פונקציית רינדור
   קטנה כדי שתעבור כמו שהיא למסך של #954. */
function imageSizeCells(img) {
  const fit = fitGB(img.min_target_bytes);
  const fitHtml = fit
    ? `<span title="${esc(String(img.min_target_bytes))} בייט">${esc(fit)}</span>`
    : "לא ידוע";
  return `נכנס לדיסק מ-${fitHtml} · בשימוש ${esc(librarySize(img.used_bytes))} · בשרת ${esc(librarySize(img.total_compressed_bytes))}`;
}

function imageOsLabel(os) {
  if (os === "windows") return "Windows";
  if (os === "linux") return "לינוקס";
  return os && os !== "unknown" ? os : "—";
}

function findImage(id) {
  if (id == null || id === "") return null;
  try { id = decodeURIComponent(id); } catch (e) {}
  return (IMAGES || []).find((x) => x.id === id) || null;
}

function imagesVisible() {
  const all = IMAGES || [];
  if (!IMAGES_FOLDER) return all;
  return all.filter((m) => m.folder === IMAGES_FOLDER);
}

function sessionImage(s) {
  return (s && (s.image_name || s.label || s.image_id)) || "";
}

function sessionGroup(s) {
  return (s && (s.group_label || s.group || s.class || s.group_id)) || "";
}

function sessionExpected(s) {
  if (!s) return 0;
  const n = s.expected_clients ?? s.expected;
  return Number(n) || 0;
}

function sessionProgressPct(s) {
  if (!s) return null;
  if (typeof s.progress === "number" && Number.isFinite(s.progress)) {
    return Math.max(0, Math.min(100, Math.round(s.progress)));
  }
  const members = s.members || [];
  let known = 0, sum = 0;
  for (const m of members) {
    if (m.done || m.state === "done") { sum += 100; known++; continue; }
    const p = Progress.view(m);
    if (p.percent != null) { sum += p.percent; known++; }
  }
  if (known) return Math.round(sum / known);
  return null;
}

function stateLabel(state) {
  if (state === "running") return "פעיל";
  if (state === "open") return "פתוח";
  if (state === "failed") return "נכשל";
  if (state === "closed") return "נסגר";
  return state || "—";
}

function sessionStateText(session) {
  if (session && session.state === "failed") return `נכשל: ${session.failed_reason || "סיבה לא ידועה"}`;
  return stateLabel(session && session.state);
}

function stateClass(state) {
  if (state === "running") return "ok";
  if (state === "open") return "warn";
  if (state === "closed" || state === "failed") return "err";
  return "";
}

/* עיקרון 5 (#517): קריאת /overview שנכשלה מסמנת את הכרטיס כישן — ולא
   חוזרת בשקט. "לא נקרא" ו"נקרא, ריק" הם שני מצבים נבדלים. */
function markStatusStale() {
  overviewError = "מצב השרת לא נקרא. עדכון אחרון: " + (overviewLastOk || "אף פעם");
  if (ME && (current === "home" || current === "deploy")) renderCurrent();
}
function markStatusFresh() {
  overviewError = ""; overviewLastOk = fmtHour(new Date().toISOString());   // #1933: שעון השרת
}
async function refreshStatus() {
  let data;
  try { data = await api("/overview"); } catch (e) {
    // #826: the task panel must show the failed read too — the old loadOverview
    // did this in `finally`, and the rewrite dropped it on the failure path.
    markStatusStale(); if (typeof renderActivity === "function") renderActivity(); return;
  }
  if (!ME) return;
  OVERVIEW = data; markStatusFresh();
  if (current === "home" || current === "deploy") renderCurrent();
  if (typeof renderActivity === "function") renderActivity();
}

async function loadImages() {
  try {
    [IMAGES, FOLDERS] = await Promise.all([api("/images"), api("/folders")]);
    if (IMAGES_FOLDER && !(FOLDERS || []).some((f) => f.name === IMAGES_FOLDER)) {
      IMAGES_FOLDER = null;
    }
    populateSidebarImages();
    if (current === "images") renderCurrent();
  } catch (e) {
    toast("טעינת הספרייה נכשלה: " + e.message);
  }
}

async function loadMachines() {
  try {
    [MACHINES, GROUPS, DISK_FAILURES, SHRINK_RECORDS] = await Promise.all([
      api("/machines"), api("/groups"), api("/disk-failures"), api("/shrink-records")]);
    // ‏#954 גל 3: IP / נראה לאחרונה / שלב אתחול מ-/net, "מחובר" מ-/monitor/machines.
    // קריאה שנכשלה = "לא נקרא" (null), לא רשימה ריקה (עיקרון 5).
    // ‏גל 3א: מצב המגירות בזמן סבב מ-/room (אובייקט המשכפלים); הקליטות מ-/tasks (אובייקט הבנייה).
    const [net, mon, room, hist] = await Promise.allSettled([api("/net"), api("/monitor/machines"), api("/room"), api("/room/history")]);
    ROOM_HISTORY = hist.status === "fulfilled" && Array.isArray(hist.value) ? hist.value : null;   // ‏#980 §3
    NET = net.status === "fulfilled" && Array.isArray(net.value) ? net.value : null;   // תשובה שאינה רשימה = לא נקרא
    NET_ERR = net.status === "rejected" ? (net.reason && net.reason.message) || "" : "";
    MONITOR_ROWS = mon.status === "fulfilled" && Array.isArray(mon.value) ? mon.value : null;
    ROOM = room.status === "fulfilled" && room.value && Array.isArray(room.value.machines) ? room.value : null;
    ROOM_KEY = JSON.stringify(ROOM);
    if (isAdmin()) await loadCaptures().catch(() => {});   // כשל = CAPTURE_TASKS_READ נשאר כפי שהיה
    populateSidebarGroups();
    updateAlertBadge();
    if (current === "machines" || current === "network") renderCurrent();   // ‏#954 גל 8: "רשום"/"הסר" ברשת ההפצה
  } catch (e) {
    toast("טעינת המחשבים נכשלה: " + e.message);
  }
}

async function loadHealth() {
  try {
    // ‏#1000: fetch ישיר ולא api() — זמן המדידה מגיע בכותרת X-Health-Checked-At
    // (שעון השרת, ISO); הגוף נשאר המערך שכל הקוראים תלויים בו.
    const response = await apiFetch(consoleUrl("/health"), { credentials: "same-origin" });
    if (response.status === 401) { showLogin(); throw new Error("לא מחובר"); }
    if (!response.ok) {
      let detail = "שגיאה " + response.status;
      try { detail = (await response.json()).detail || detail; } catch (e) {}
      const error = new Error(detail); error.status = response.status; throw error;
    }
    const checkedAt = response.headers.get("X-Health-Checked-At");
    HEALTH = await response.json();
    healthError = "";
    // חותמת שלא הגיעה = "זמן המדידה לא נמסר", לא שעון הדפדפן (עיקרון 5).
    HEALTH_AT = checkedAt ? fmtWhenShort(checkedAt) : "";
    noteServerHealth(SERVER_CTX || "", HEALTH, null);   // נדב 18/10: אותו נתון — הנקודה של השרת המוצג
    updateAlertBadge();
  } catch (e) {
    HEALTH = null;
    healthError = e.message;
    noteServerHealth(SERVER_CTX || "", null, e);
    paintServiceStatus();
    toast("טעינת הבריאות נכשלה: " + e.message);
  }
  if (current === "health") await loadHealthServices();   // #1267: כרטיס השירותים; העדכונים עברו להגדרות (stage 3)
  if (current === "home" || current === "health") renderCurrent();
}

/* ‏#954 גל 5 (stage 3: עבר להגדרות): כרטיס "עדכונים" — /update ו-/update/status
   (admin). שם השרת לאישור חזרה מגיע מ-ME.server_name (/me), לא מכתובת הקונסולה. */
async function loadHealthUpdate() {
  try {
    const info = await api("/update");
    HEALTH_UPDATE = info;
    UPDATE_INFO = info;
    UPDATE_STATUS = info.enabled ? await api("/update/status") : null;
    updateError = "";
  } catch (e) {
    HEALTH_UPDATE = null;
    updateError = e.message;
  }
}

async function loadPorts() {
  // #822: כשל קריאה הוא מצב משלו (portsError) ולא "אין פורטים" (עיקרון 5).
  try {
    PORTS = await api("/ports");
    portsError = "";
    PORTS_AT = clockNow();
  } catch (e) {
    PORTS = null;
    portsError = e.message;
    toast("טעינת הפורטים נכשלה: " + e.message);
  }
  // ‏/net/interfaces — הכתובת בכותרת כרטיס רשת ההפצה (#1267). כשל קריאה
  // מוצג "לא נקרא", לא "כבוי" (עיקרון 5).
  if (isAdmin()) {
    const [net] = await Promise.allSettled([loadNet()]);
    PORTS_NICS = net.status === "fulfilled" ? NICS : null;
    portsNicsError = net.status === "fulfilled" ? "" : net.reason.message;
  }
  if (current === "ports") renderCurrent();
  if (current === "network") renderCurrent();
}

async function loadMonitor() {
  // #822: "מחובר" מגיע מהשרת (monitor.py, חלון 90ש'), לא מחישוב בדפדפן.
  // כשל קריאה הוא מצב משלו (monitorError) ולא "אין מכונות" (עיקרון 5).
  try {
    if (!MACHINES || !GROUPS) await loadMachines();   // monitorMachine() נשען עליהם
    const [settings, machines, remote] = await Promise.all([api("/monitor/settings"), api("/monitor/machines"),
      loadMonitorRemote()]);
    MONITOR = { settings, machines, remote };
    monitorError = "";
  } catch (e) {
    MONITOR = null;
    monitorError = e.message;
    toast("טעינת המוניטור נכשלה: " + e.message);
  }
  if (current === "monitor") renderCurrent();
}

/* ‏#1008 (נדב 11/10: שרת המשנה הוא v1): במוניטור של הראשי — גם המכונות של כל שרת משנה,
   באותה רשימה ועם שם השרת (‏GET …/storage-nodes/{id}/machines, תמיד מול הראשי). שרת משנה שלא
   ענה — שורה "לא נקרא" עם הסיבה, לא היעלמות; מושבת — "לא נשאל". בהקשר שרת משנה (#1179) אין
   ענפים: זו רשימת שרת המשנה עצמו. */
async function loadMonitorRemote() {
  const me = PRIMARY_ME || ME;
  if (SERVER_CTX || !(me && me.capabilities && me.capabilities.enroll_secondary)) return [];
  let nodes;
  try { nodes = await api("/storage-nodes", { primary: true }); }
  catch (e) {
    if (e.status === 403 || e.status === 409) return [];
    return [{ node: null, label: "שרתי המשנה", connected: false, error: e.message, machines: [] }];
  }
  return Promise.all(nodes.map(async (n) => {
    if (n.disabled_at) return { node: n.id, label: n.label, connected: false, error: "השרת מושבת — לא נשאל", machines: [] };
    try {
      const a = await api(`/storage-nodes/${encodeId(n.id)}/machines`, { primary: true });
      return { node: n.id, label: n.label, connected: a.connected === true, error: a.error || "",
               machines: a.connected === true && Array.isArray(a.machines) ? a.machines : [] };
    } catch (e) { return { node: n.id, label: n.label, connected: false, error: e.message, machines: [] }; }
  }));
}
/* מכונה ברשימת המוניטור: מקומית (מפתח = MAC) או של שרת משנה (מפתח = "node|MAC", ‏node = שרת המשנה). */
function monitorKey(m) { return m.node ? `${m.node}|${m.mac}` : m.mac; }
function monitorList() {
  const local = MONITOR ? MONITOR.machines || [] : [];
  const remote = (MONITOR && MONITOR.remote || []).flatMap((r) => r.machines.map((m) => ({ ...m, node: r.node, server: r.label })));
  return local.concat(remote);
}

async function loadUsersData() {
  // ‏#954 גל 6: כשל קריאה הוא מצב משלו (usersError), לא "אין משתמשים" (עיקרון 5).
  try { USERS = await api("/users"); usersError = ""; }
  catch (e) { USERS = null; usersError = e.message; toast("טעינת המשתמשים נכשלה: " + e.message); }
  if (current === "permissions") renderCurrent();
}

function machineName(m) {
  return (m && (m.suffix || m.name)) || "";
}

function machineGroupId(m) {
  return (m && (m.group || m.group_id)) || "";
}

function groupLabel(id) {
  const g = (GROUPS || []).find((x) => x.id === id);
  return g ? g.label : (id || "—");
}

function findMachine(mac) {
  if (mac == null || mac === "") return null;
  try { mac = decodeURIComponent(mac); } catch (e) {}
  const want = String(mac).toLowerCase();
  return (MACHINES || []).find((m) => String(m.mac || "").toLowerCase() === want) || null;
}

function sortMachinesBySuffix(list) {
  return list.slice().sort((a, b) => {
    const sa = String(machineName(a));
    const sb = String(machineName(b));
    const na = /^\d+$/.test(sa);
    const nb = /^\d+$/.test(sb);
    if (na !== nb) return na ? 1 : -1;
    if (na && nb) return Number(sa) - Number(sb);
    return sa.localeCompare(sb, "en", { sensitivity: "base" });
  });
}

function sidebarChildOpen(tree) {
  const wasOpen = {};
  tree.querySelectorAll(":scope > .inventory-children").forEach((el) => {
    wasOpen[el.id] = !(el.hasAttribute("hidden") || el.style.display === "none");
  });
  return wasOpen;
}

/* ‏#1179: עצי-המשנה (קבוצות ומחשבים, תיקיות ואימג'ים, כרטיסי רשת) — אותן פונקציות.
   בהקשר שרת משנה הם נכתבים לענף של שרת המשנה בעץ (‏srv-<id>-…), לא לעץ של הראשי, והלחיצה עליהם
   נכנסת קודם להקשר של שרת המשנה — צומת ישן שנשאר בעץ לא יפעל על שרת אחר. */
function sideId(id) { return SERVER_CTX ? `srv-${SERVER_CTX}-${id}` : id; }
function sideClick(handler) {
  return SERVER_CTX ? `inSecondary('${encodeId(SERVER_CTX)}',()=>{${handler}})` : handler;
}
function inSecondary(nidEnc, fn) {
  let nid = nidEnc;
  try { nid = decodeURIComponent(nidEnc); } catch (e) {}
  setServerContext(nid, secondaryLabel(nid));
  fn();
}
function populateSidebarGroups() {
  const groups = GROUPS || [];
  const machines = MACHINES || [];
  fillSidebarTree("classesTree", classroomsOn() ? groups.filter((g) => g.role === "classroom") : [], machines, "group");
  fillSidebarTree("buildTree", groups.filter((g) => g.role === "build"), machines, "build");
  fillSidebarTree("clonerTree", groups.filter((g) => g.role === "cloner"), machines, "copy");
}

function populateSidebarImages() {
  const tree = document.getElementById(sideId("imagesTree"));
  if (!tree || !FOLDERS) return;
  if (!FOLDERS.length) {
    tree.innerHTML = '<div class="inventory-node muted">אין תיקיות</div>';
    return;
  }
  const wasOpen = sidebarChildOpen(tree);
  tree.innerHTML = FOLDERS.map((f, i) => {
    const fid = sideId("imgfolder" + i);
    const imgs = (IMAGES || []).filter((im) => im.folder === f.name);
    const open = !!wasOpen[fid];
    if (!imgs.length) {
      return `<div class="inventory-node" role="treeitem" tabindex="0"><span class="tree-arrow-sp"></span><span>${uiIcon("t-subfolder")}</span><span>${esc(f.name)}</span></div>`;
    }
    const rows = imgs.map((im) => {
      const idEnc = encodeId(im.id || "");
      return `<div class="inventory-node" role="treeitem" tabindex="0" onclick="${sideClick(`openImageDetail('${idEnc}')`)}"><span class="tree-arrow-sp"></span><span>${uiIcon("image")}</span><span>${esc(im.name)}</span></div>`;
    }).join("");
    return `<div class="inventory-node" role="treeitem" tabindex="0" aria-expanded="${open}" onclick="toggleInventoryGroup(this,'${fid}')"><span class="tree-arrow" data-open="${open}" onclick="event.stopPropagation();toggleInventoryGroup(this.closest('.inventory-node'),'${fid}')">${open ? "▾" : "▸"}</span><span>${uiIcon("t-subfolder")}</span><span>${esc(f.name)}</span></div><div id="${fid}" class="inventory-children"${open ? "" : " hidden"}>${rows}</div>`;
  }).join("");
}

function fillSidebarTree(treeId, groups, machines, icon) {
  const tree = document.getElementById(sideId(treeId));
  if (!tree) return;
  const wasOpen = sidebarChildOpen(tree);
  const leaf = (m) => `<div class="inventory-node" role="treeitem" tabindex="0" onclick="${sideClick(`openMachineDetail('${encodeId(m.mac)}')`)}"><span class="tree-arrow-sp"></span><span>${uiIcon("machine")}</span><span>${esc(machineName(m) || m.mac)}</span></div>`;
  /* owner 11/10: one group under the role is a level that says nothing —
     "מחשבי בנייה › מחשב בניית אימג'ים › HP-BUILD"; the machines sit under the role */
  if (groups.length === 1) {
    tree.innerHTML = sortMachinesBySuffix(machines.filter((m) => machineGroupId(m) === groups[0].id)).map(leaf).join("");
    return;
  }
  tree.innerHTML = groups.map((g) => {
    const boxId = sideId("sg-" + g.id);
    const open = !!wasOpen[boxId];
    const kids = sortMachinesBySuffix(machines.filter((m) => machineGroupId(m) === g.id));
    const gidEnc = encodeId(g.id);
    if (!kids.length) {
      return `<div class="inventory-node" role="treeitem" tabindex="0" onclick="${sideClick(`selectMachinesGroup('${gidEnc}')`)}"><span class="tree-arrow-sp"></span><span>${uiIcon(icon)}</span><span>${esc(g.label)}</span></div>`;
    }
    const childHtml = kids.map(leaf).join("");
    return `<div class="inventory-node" role="treeitem" tabindex="0" aria-expanded="${open}" onclick="treeNavToggle(this,'${esc(boxId)}',function(){${sideClick(`selectMachinesGroup('${gidEnc}')`)}})"><span class="tree-arrow" data-open="${open}" onclick="event.stopPropagation();toggleInventoryGroup(this.closest('.inventory-node'),'${esc(boxId)}')">${open ? "▾" : "▸"}</span><span style="display:contents"><span>${uiIcon(icon)}</span><span>${esc(g.label)}</span></span></div><div id="${esc(boxId)}" class="inventory-children"${open ? "" : " hidden"}>${childHtml}</div>`;
  }).join("");
}

function machineIsFailed(m) {
  const s = String((m && (m.status || m.state)) || "").toLowerCase();
  return s === "failed" || s === "fail" || s === "error" || s === "err" || s === "bad" || s === "נכשל";
}

/* שורת הסטטוס והכותרת התחתונה של העץ היו קבועות — ירוק "כל השירותים פעילים" גם כש-/health אדום.
   עכשיו הן נגזרות מאותן בדיקות, וללא נתון הן אפורות ("לא נבדק", עיקרון 5) ולא ירוקות. */
const SERVICE_DOTS = { API: ["server"], PXE: ["tftp_port", "boot_files"], Multicast: ["udp_sender"] };
const SERVICE_RANK = { bad: 4, warn: 3, unknown: 2, ok: 1, off: 0, not_applicable: 0 };
function serviceState(ids) {
  if (!Array.isArray(HEALTH)) return { state: "unknown", detail: "" };
  const found = HEALTH.filter((c) => ids.includes(c.id));
  if (!found.length) return { state: "unknown", detail: "" };
  let worst = found[0];
  for (const c of found) if ((SERVICE_RANK[c.state] ?? 2) > (SERVICE_RANK[worst.state] ?? 2)) worst = c;
  return { state: (SERVICE_RANK[worst.state] ?? 2) === 0 ? "off" : worst.state, detail: worst.detail || "" };
}
function paintServiceStatus() {
  for (const [name, ids] of Object.entries(SERVICE_DOTS)) {
    const el = document.querySelector(`[data-svc="${name}"]`);
    if (!el) continue;
    let { state, detail } = serviceState(ids);
    // משדר שרץ בתוך סבב פתוח הוא מצב תקין ("warn" בשרת רק כדי שהמפעיל ידע שיש שידור) — לא צהוב.
    const sending = name === "Multicast" && state === "warn" && /סבב פתוח/.test(detail);
    const cls = sending ? "ok" : healthStatusClass(state) || "off";
    // קידומת svc-: ל-.ok/.err הגלובליים יש כללי טקסט-הודעה (גודל, שוליים) שהזיזו את "API" 6px.
    for (const c of ["ok", "warn", "err", "unk", "off"]) el.classList.toggle("svc-" + c, c === cls);
    const what = name === "Multicast" ? "Multicast (משדר udp-sender)" : name;
    el.setAttribute("title", `${what}: ${sending ? "משדר כעת" : healthStatusLabel(state)}${detail ? " — " + detail : ""}`);
  }
}

/* נדב 18/10: "למה ליד חיפה יש עיגול ירוק וליד TA אין?" + "אין רשת הפצה בחיפה, והוא מראה ירוק בעץ וכתום
   בסקירה". נקודה אחת לכל שרת בעץ — TA וכל שרת משנה — מחושבת כאן בלבד (serverDotState), מאותן בדיקות
   ‏/health שהסקירה של **אותו** שרת מציגה ב"דורש טיפול": ירוק = מחובר וכל הבדיקות תקינות; כתום = מחובר
   ויש אזהרה (למשל "הגדר את רשת ההפצה"); אדום = תקלה, או שלא ענה; אפור = לא נמדד (עיקרון 5: לא ירוק).
   הקופסה שהייתה בתחתית העץ ("TA · מחובר / כל השירותים פעילים") יצאה — מה שהיה בה עבר לנקודה ול-title שלה.
   המקור: מה שכבר נטען (loadHealth של השרת המוצג), ולשאר השרתים קריאה אחת ל-/health לכל היותר
   פעם ב-SERVER_DOT_TTL (לשרת משנה — דרך ה-relay של הראשי, אותו נתיב שמזין את הסקירה שלו). */
const SERVER_HEALTH = {};   // "" = הראשי; מזהה שרת משנה → {checks, error, down, at}
const SERVER_DOT_TTL = 45000;
let SIDEBAR_NODES = [];
const SERVER_DOTS_INFLIGHT = new Set();   // מפתחות שבקשת /health שלהם עדיין פתוחה
function serverDotState(entry) {
  if (!entry) return { cls: "unk", text: "מצב השירותים לא נבדק" };
  if (entry.down) return { cls: "err", text: "לא מחובר" + (entry.error ? ": " + entry.error : "") };
  if (!Array.isArray(entry.checks)) return { cls: "unk", text: "מצב השירותים לא נקרא" + (entry.error ? ": " + entry.error : "") };
  const line = (c) => (c.label || c.id || "") + (c.detail ? " — " + c.detail : "");
  const bad = entry.checks.filter((c) => c.state === "bad"), warn = entry.checks.filter((c) => c.state === "warn");
  if (bad.length) return { cls: "err", text: [`מחובר · ${bad.length} שירותים בתקלה`, ...bad.map(line), ...warn.map(line)].join("\n") };
  if (warn.length) return { cls: "warn", text: ["מחובר · דורש טיפול", ...warn.map(line)].join("\n") };
  return { cls: "ok", text: "מחובר · כל השירותים פעילים" };
}
function paintServerDot(key) {
  const st = serverDotState(SERVER_HEALTH[key]);
  const el = key ? document.querySelector(`[data-secondary-status="${CSS.escape(key)}"]`) : document.getElementById("primaryServerStatus");
  if (el) { el.className = "status " + st.cls; el.setAttribute("title", st.text); el.setAttribute("aria-label", st.text); }
  if (!key) paintRailFoot(st.cls, st.text);   // ‏#1300: נורית השירותים בתחתית הפס — של הראשי
  return st;
}
/* ‏err.network = הדפדפן לא הגיע לראשי; ‏502 מה-relay = שרת המשנה לא ענה. שאר הכשלים — "לא נקרא" (אפור). */
function noteServerHealth(key, checks, err) {
  const down = !!err && (!!err.network || (!!key && err.status === 502));
  SERVER_HEALTH[key] = { checks: err ? null : checks, error: err ? String(err.message || "") : "", down, at: Date.now() };
  const n = key ? SIDEBAR_NODES.find((x) => x.id === key) : null;
  if (n && (down || !err)) markSecondaryStatus(n, !down, down ? SERVER_HEALTH[key].error : "");
  else paintServerDot(key);
}
async function fetchServerHealth(key) {
  const url = key ? `/api/console/storage-nodes/${encodeId(key)}/console/health` : "/api/console/health";
  const response = await apiFetch(url, { credentials: "same-origin" });
  if (!response.ok) {
    let detail = "שגיאה " + response.status;
    try { const b = await response.json(); detail = b.message_he || b.detail || detail; } catch (e) {}
    const error = new Error(detail); error.status = response.status; throw error;
  }
  return response.json();
}
/* TA 18/10 (d8763e5b): נעילה אחת לכל הסבב דילגה על הרענון שאחרי בניית העץ כשהדגימה התקופתית עוד מדדה
   את TA — וחיפה נשארה אפורה; בלשונית ברקע (document.hidden) הדגימה אינה רצה, ולא היה ניסיון שני.
   עכשיו "בדרך" הוא לכל שרת: רק שרת שבקשה שלו עדיין פתוחה מדולג. */
async function refreshServerDots(force = false) {
  if (!isAdmin()) return;   // ‏/health הוא admin בלבד — אחרים: אפור "לא נבדק"
  const stale = (k) => !SERVER_DOTS_INFLIGHT.has(k) && (force || !SERVER_HEALTH[k] || Date.now() - SERVER_HEALTH[k].at >= SERVER_DOT_TTL);
  const keys = [""].concat(SIDEBAR_NODES.filter((n) => !n.disabled_at).map((n) => n.id)).filter(stale);
  keys.forEach((k) => SERVER_DOTS_INFLIGHT.add(k));
  await Promise.all(keys.map((k) => fetchServerHealth(k)
    .then((c) => noteServerHealth(k, c, null), (e) => noteServerHealth(k, null, e))
    .finally(() => SERVER_DOTS_INFLIGHT.delete(k))));
}

function updateAlertBadge() {
  paintServiceStatus();
  const badge = document.getElementById("alertBadge");
  if (!badge) return;
  let n = 0;
  for (const m of MACHINES || []) {
    if (machineIsFailed(m)) n++;
  }
  if (Array.isArray(HEALTH)) {
    for (const c of HEALTH) {
      if (c.state === "warn" || c.state === "bad") n++;
    }
  }
  // נדב 18/10: ההתראות על כל השרתים — שרת משנה שנמדד ולא ענה (SECONDARY_STATUS, markSecondaryStatus)
  for (const st of Object.values(SECONDARY_STATUS || {})) if (st && st.connected === false) n++;
  if (n > 0) {
    badge.textContent = String(n);
    badge.classList.add("has-alerts");
    badge.removeAttribute("hidden");
  } else {
    badge.textContent = "";
    badge.classList.remove("has-alerts");
    badge.setAttribute("hidden", "");
  }
}

/* ‏#936: שם השרת הראשי בצומת העליון מגיע מ-/me (‏`server_name` = שם המארח,
   נדב 17/10) — לא מחרוזת דמו ב-HTML ולא localStorage. */
function serverRootName(fallback) { return (ME && ME.server_name) || fallback; }
function applyServerName() {
  const el = document.getElementById("primaryServerName");
  const me = PRIMARY_ME || ME;   // ‏#1179: הצומת העליון הוא תמיד הראשי, גם בהקשר שרת משנה
  if (el && me && me.server_name) el.textContent = me.server_name;
}

/* לחיצה כפולה על הצומת העליון בעץ — אותו חלון כמו "שינוי שם…" בהגדרות (נדב 17/10: שם אחד, מקום אחד). */
function renameServer(el) {
  if (!el || !isAdmin()) return;
  hostnameSheet(true);
}

/* ‏#936: צומת שרת לכל שרת משנה מ-GET /storage-nodes, כמו vCenter. הנתיב הוא
   admin+standalone (deploy → 403, שרת משנה → 409): במקרים האלה אין צמתים —
   לא שגיאה אדומה. מצב החיבור נמדד מול שרת המשנה (‏/machines, ‏connected) אחרי
   שהצמתים כבר על המסך; שרת משנה שלא ענה נשאר בעץ עם נקודה אדומה.

   ‏#1179: העץ של שרת משנה = אותו עץ של הראשי (‏#srvTA ב-index.html) עד רמת הדף, וכל
   עלה הוא **אותו עמוד של הראשי** בהקשר שרת המשנה (SERVER_CTX, ‏openSecondaryPage):
   סקירה כללית · סבב הפצה · מלאי ▸ · תשתית ▸ · ניהול ▸. העמוד, הלשוניות והכרטיסים
   זהים מעצם המבנה — הנתונים מגיעים משרת המשנה דרך ה-relay. "העברות" תחת מלאי הן
   ההעברות בין הראשי לשרת הזה (דף branch, נתוני הראשי) ואין להן מקבילה בראשי.
   בלי "ארגז כלים" (כבוי ב-v1), "שרתי משנה" (לשרת המשנה אין ילדים) ו"החשבון שלי" (של
   המשתמש בראשי, לא של השרת). */
const BRANCH_TREE = [
  ["home", "סקירה כללית", "t-home"],
  ["deploy", "סבב הפצה", "t-deploy"],
  // נדב 18/10: כמו הראשי בכניסה הראשונה — מלאי, תשתית וניהול מקופלים (index.html)
  { id: "inv", label: "מלאי", icon: "list", open: false, kids: [
    ["images", "אימג'ים", "t-images"], ["machines", "מחשבים", "t-machines"],
    ["transfers", "העברות", "deploy"]] },
  { id: "infra", label: "תשתית", icon: "t-infra", open: false, kids: [
    ["health", "בריאות ושירותים", "t-health"], ["storage", "אחסון", "storage"],
    ["network", "רשת", "t-network"], ["monitor", "מוניטור", "t-monitor"],
    ["drivers", "דרייברים", "t-drivers"]] },
  { id: "admin", label: "ניהול", icon: "t-admin", open: false, kids: [
    ["permissions", "משתמשים והרשאות", "t-key"], ["logs", "יומן", "t-log"],
    ["settings", "הגדרות", "t-sliders"]] },
];
const BRANCH_VIEWS = BRANCH_TREE.flatMap((x) => Array.isArray(x) ? [x] : x.kids);
let BRANCH_NODE = null;
let BRANCH_VIEW = "transfers";

/* צומת שנפתח/נסגר בעץ של שרת משנה — אותו מבנה של הצמתים ב-index.html. */
function branchBox(n, wasOpen, key, head, kids, open = false) {
  const boxId = `srv-${n.id}-${key}`;
  const isOpen = boxId in wasOpen ? wasOpen[boxId] : open;
  return `${head(boxId, isOpen)}<div id="${esc(boxId)}" class="inventory-children" role="group"${isOpen ? "" : " hidden"}>${kids}</div>`;
}
const branchArrow = (boxId, open) => `<span class="tree-arrow" data-open="${open}" onclick="event.stopPropagation();toggleInventoryGroup(this.closest('.inventory-node'),'${esc(boxId)}')">${open ? "▾" : "▸"}</span>`;
/* מתחת לעלה: אימג'ים ← תיקיות (מתמלא משרת המשנה); מחשבים ← כיתות/בנייה/שיכפול ← קבוצות;
   רשת ← חיבורים פיזיים (← כרטיסים) · רשת הפצה · פורטים. כמו #invTA/#netTA בראשי. */
function branchLeafKids(n, page, wasOpen) {
  const idEnc = encodeId(n.id);
  const go = (handler) => `inSecondary('${idEnc}',()=>{${handler}})`;
  const sub = (key, label, icon, handler, cap = "") => branchBox(n, wasOpen, key, (boxId, open) =>
    `<div class="inventory-node${cap && !(ME && ME.capabilities && ME.capabilities[cap]) ? " hidden" : ""}"${cap ? ` data-cap="${cap}"` : ""} role="treeitem" tabindex="0" aria-expanded="${open}" onclick="treeNavToggle(this,'${esc(boxId)}',function(){${go(handler)}})">${branchArrow(boxId, open)}<span>${uiIcon(icon)}</span><span>${esc(label)}</span></div>`, "");
  const plain = (label, icon, handler, attrs = "") =>
    `<div class="inventory-node"${attrs} role="treeitem" tabindex="0" onclick="${handler}"><span class="tree-arrow-sp"></span><span>${uiIcon(icon)}</span><span>${esc(label)}</span></div>`;
  if (page === "images") return "";   // התיקיות עצמן — populateSidebarImages בהקשר שרת המשנה
  if (page === "machines") {
    return sub("classesTree", "כיתות", "group", "selectMachinesRole('classroom')", "classrooms")
      + sub("buildTree", "מחשבי בנייה", "build", "selectMachinesRole('build')")
      + sub("clonerTree", "מחשבי שיכפול", "copy", "selectMachinesRole('cloner')");
  }
  if (page === "network") {
    return plain("תרשים", "t-network", go("openNetwork(0,null,this)"), ` data-net-tab="0"`)
      + `<div class="inventory-node" data-net-tab="1" id="srv-${esc(n.id)}-nicNode" role="treeitem" tabindex="0" onclick="${go("openNetwork(1,null,this)")}"><span class="tree-arrow-sp" id="srv-${esc(n.id)}-nicTreeArrow"></span><span>${uiIcon("t-nic")}</span><span>חיבורים פיזיים</span></div><div id="srv-${esc(n.id)}-nicTree" class="inventory-children" role="group" hidden></div>`
      + plain("רשת הפצה", "deploy", go("openNetwork(2,null,this)"), ` data-net-tab="2"`)
      + plain("פורטים", "t-ports", `openSecondaryPage('${idEnc}','ports',this)`, ` data-branch-page="ports" data-branch-node="${esc(n.id)}"`);
  }
  return null;
}
const BRANCH_TREE_BOXES = { images: "imagesTree", machines: "machineTree", network: "netTA" };

function branchTreeHtml(n, wasOpen) {
  const idEnc = encodeId(n.id);
  const leaf = ([page, label, icon]) => {
    if (BRANCH_TREE_BOXES[page]) {
      return branchBox(n, wasOpen, BRANCH_TREE_BOXES[page], (boxId, open) =>
        `<div class="inventory-node" data-branch-page="${esc(page)}" data-branch-node="${esc(n.id)}" role="treeitem" tabindex="0" aria-expanded="${open}" onclick="treeNavToggle(this,'${esc(boxId)}',function(){openSecondaryPage('${idEnc}','${page}',this)})">${branchArrow(boxId, open)}<span>${uiIcon(icon)}</span><span>${esc(label)}</span></div>`,
        branchLeafKids(n, page, wasOpen));
    }
    return leafPlain([page, label, icon]);
  };
  const leafPlain = ([page, label, icon]) => page === "transfers"
    ? `<div class="inventory-node" data-branch-view="transfers" data-branch-node="${esc(n.id)}" role="treeitem" tabindex="0" onclick="openBranchView('${idEnc}','transfers',this)"><span class="tree-arrow-sp"></span><span>${uiIcon(icon)}</span><span>${esc(label)}</span></div>`
    : `<div class="inventory-node" data-branch-page="${esc(page)}" data-branch-node="${esc(n.id)}" role="treeitem" tabindex="0" onclick="openSecondaryPage('${idEnc}','${page}',this)"><span class="tree-arrow-sp"></span><span>${uiIcon(icon)}</span><span>${esc(label)}</span></div>`;
  return BRANCH_TREE.map((x) => {
    if (Array.isArray(x)) return leaf(x);
    const boxId = `srv-${n.id}-${x.id}`;
    const open = boxId in wasOpen ? wasOpen[boxId] : x.open;
    return `<div class="inventory-node" role="treeitem" tabindex="0" aria-expanded="${open}" onclick="toggleInventoryGroup(this,'${esc(boxId)}')"><span class="tree-arrow" data-open="${open}" onclick="event.stopPropagation();toggleInventoryGroup(this.closest('.inventory-node'),'${esc(boxId)}')">${open ? "▾" : "▸"}</span><span>${uiIcon(x.icon)}</span><span>${esc(x.label)}</span></div><div id="${esc(boxId)}" class="inventory-children" role="group"${open ? "" : " hidden"}>${x.kids.map(leaf).join("")}</div>`;
  }).join("");
}

/* ‏#1179: כניסה ויציאה מהקשר שרת משנה. המטמונים הגלובליים מתאפסים בכל מעבר — אחרת עמוד
   של שרת המשנה היה מצייר נתון של הראשי שכבר נטען (MACHINES/GROUPS משותפים לכמה עמודים).
   ‏ME בהקשר שרת משנה: אותו משתמש ותפקיד (ההרשאה היא של הראשי), שם השרת = שם שרת המשנה,
   ומה שהוא של השרת ואינו נקרא משרת המשנה (גרסה, TLS, זמן פעילות, כתובת הפצה) — null,
   "לא נקרא", לא הערכים של הראשי. בלי "ניהול שרתי משנה" — לשרת המשנה אין ילדים. */
function setServerContext(nid, label) {
  const next = nid || null;
  if (next === SERVER_CTX) return;
  if (!PRIMARY_ME) PRIMARY_ME = ME;
  SERVER_CTX = next;
  resetServerCaches();
  ME = next && PRIMARY_ME ? { ...PRIMARY_ME, server_name: label || next, version: null, tls: null,
    uptime_seconds: null, deploy_ip: null,
    capabilities: { ...(PRIMARY_ME.capabilities || {}), enroll_secondary: false, interbranch_transfer: false } }
    : PRIMARY_ME;
}
function secondaryLabel(nid) {
  const el = document.querySelector(`[data-secondary="${CSS.escape(nid)}"] .server-name`);
  const known = (BRANCH_NODES || []).find((n) => n.id === nid);
  return (known && known.label) || (el && el.textContent) || nid;
}
function resetServerCaches() {
  OVERVIEW = null; IMAGES = null; FOLDERS = null; IMAGES_FOLDER = null;
  MACHINES = null; GROUPS = null; DISK_FAILURES = null; SHRINK_RECORDS = null;
  MACHINES_FILTER = null; MACHINES_ROLE = null; MACHINES_CLASS = null;
  HEALTH = null; HEALTH_AT = ""; healthError = ""; NETCFG = null;
  MONITOR = null; monitorError = ""; MONITOR_ROWS = null; MON_SEL = ""; MON_SETTINGS = null;
  PORTS = null; portsError = ""; PORTS_NICS = null; portsNicsError = ""; PORTS_AT = "";
  USERS = null; usersError = ""; JOURNAL = null; LOG = logInit();
  HOME = homeInit(); IMG = imgInit(); NETW = netwInit(); NET = null; NET_ERR = ""; NET_DEPLOY = null;
  ROOM = null; ROOM_HISTORY = null; ROOM_KEY = ""; DRIVER_MODELS = null;
  CAPTURE_TASKS = []; CAPTURE_TASKS_READ = false; SESSION_MACHINES = { group: null, list: [] };
  HEALTH_UPDATE = null; UPDATE_STATUS = null; UPDATE_CHECK = null; updateError = ""; UPDATE_INFO = {};
  HEALTH_SVC = null; healthSvcError = "";
  SETTINGS = null; settingsError = ""; TOOLS = null; toolsError = "";
  // הקבצים שנטענים אחרי console.js (בדפדפן כולם קיימים; ב-node רק חלקם).
  if (typeof DRIVERS !== "undefined") { DRIVERS = null; driversError = ""; }
  if (typeof STORAGE !== "undefined") { STORAGE = null; storageError = ""; clearInterval(STORAGE_TIMER); }
  if (typeof NICS !== "undefined") NICS = [];
  if (typeof NETCFG_TICK !== "undefined") clearInterval(NETCFG_TICK);
  if (typeof stopBranchPolling === "function") stopBranchPolling();
  if (typeof GS !== "undefined") { GS.items = []; GS.active = 0; GS.failed = []; }   // החיפוש הגלובלי — של השרת הנוכחי
}

/* עלה של שרת משנה בעץ → אותו עמוד של הראשי, בהקשר שרת המשנה. */
function openSecondaryPage(nid, page, el) {
  try { nid = decodeURIComponent(nid); } catch (e) {}
  setServerContext(nid, secondaryLabel(nid));
  selectPage(el || document.querySelector(
    `[data-branch-node="${CSS.escape(nid)}"][data-branch-page="${page}"]`)
    || document.querySelector(`[data-secondary="${CSS.escape(nid)}"]`), page);
}

async function populateSidebarSecondaries() {
  const host = document.getElementById("secondaryServers");
  if (!host) return;
  const me = PRIMARY_ME || ME;   // ‏#1179: הענפים הם של הראשי, גם כשמוצג שרת משנה
  const caps = (me && me.capabilities) || {};
  if (!caps.enroll_secondary) { host.innerHTML = ""; SIDEBAR_NODES = []; return; }
  let nodes = [];
  try { nodes = await api("/storage-nodes", { primary: true }); }
  catch (e) {
    if (e.status === 403 || e.status === 409) { host.innerHTML = ""; SIDEBAR_NODES = []; return; }
    throw e;
  }
  SIDEBAR_NODES = nodes;
  // ‏#1179: גם הקבוצות המקוננות (מלאי/תשתית) שומרות פתוח/סגור בין רינדורים.
  const wasOpen = {};
  host.querySelectorAll(".inventory-children").forEach((el) => {
    wasOpen[el.id] = !(el.hasAttribute("hidden") || el.style.display === "none");
  });
  host.innerHTML = nodes.map((n) => {
    const boxId = "srv-" + n.id;
    const open = !!wasOpen[boxId];
    const idEnc = encodeId(n.id);
    const kids = branchTreeHtml(n, wasOpen);
    const dot = n.disabled_at ? { cls: "", text: "מושבת" } : serverDotState(SERVER_HEALTH[n.id]);   // נדב 18/10: אותה נקודה כמו TA
    return `<div class="inventory-node server-node" data-secondary="${esc(n.id)}" data-conn="${n.disabled_at ? "off" : "checking"}" role="treeitem" tabindex="0" aria-expanded="${open}" onclick="treeNavToggle(this,'${esc(boxId)}',function(){openSecondaryPage('${idEnc}','home',this)})"><span class="tree-arrow" data-open="${open}" onclick="event.stopPropagation();toggleInventoryGroup(this.closest('.inventory-node'),'${esc(boxId)}')">${open ? "▾" : "▸"}</span><span>${uiIcon("server")}</span><strong class="server-name">${esc(n.label)}</strong><span class="status ${dot.cls}" data-secondary-status="${esc(n.id)}" title="${esc(dot.text)}" aria-label="${esc(dot.text)}"><i></i></span></div><div id="${esc(boxId)}" class="inventory-children" role="group"${open ? "" : " hidden"}>${kids}</div>`;
  }).join("");
  await Promise.all(nodes.filter((n) => !n.disabled_at).map((n) =>
    api(`/storage-nodes/${encodeId(n.id)}/machines`, { primary: true })
      .then((a) => markSecondaryStatus(n, !!a.connected, a.error))
      .catch((e) => markSecondaryStatus(n, false, e.message))));
}

function markSecondaryStatus(n, connected, error) {
  SECONDARY_STATUS[n.id] = { connected, error };   // ‏#954: "דורש טיפול" בסקירה קורא מכאן
  updateAlertBadge();   // נדב 18/10: גם הפעמון — לא רק הסקירה
  // ‏#1179: הענף כולו אדום כששרת המשנה לא ענה — לא רק הנקודה (console.css: data-conn).
  const row = document.querySelector(`[data-secondary="${CSS.escape(n.id)}"]`);
  if (row) row.setAttribute("data-conn", n.disabled_at ? "off" : (connected ? "ok" : "err"));
  // נדב 18/10: הנקודה — serverDotState, כמו TA: "מחובר" לבדו אינו ירוק; הבריאות של שרת המשנה מכריעה.
  if (!connected) SERVER_HEALTH[n.id] = { checks: null, error: error || "", down: true, at: Date.now() };
  else if (SERVER_HEALTH[n.id] && SERVER_HEALTH[n.id].down) delete SERVER_HEALTH[n.id];
  paintServerDot(n.id);
}

/* ‏#1179: דף "העברות" של שרת משנה (BRANCH_NODE) — דף של הראשי, ולכן מחוץ להקשר שרת המשנה.
   בלי el (מעבר מתוך הדף) — העלה בעץ נמצא ומודגש. */
function openBranchView(nid, view, el) {
  try { nid = decodeURIComponent(nid); } catch (e) {}
  setServerContext(null);   // ‏#1179: דף "העברות" הוא דף של הראשי על שרת המשנה
  BRANCH_NODE = nid;
  BRANCH_VIEW = "transfers";
  selectPage(el || document.querySelector(
    `[data-branch-node="${CSS.escape(nid)}"][data-branch-view="${BRANCH_VIEW}"]`), "branch");
}

/* F13: רענון תקופתי (חדר ההפצה, כל 2 שניות כשהנתונים השתנו) בונה את הדף מחדש
   — בלי זה הפוקוס, הסימון והטקסט שהמפעיל בעיצומו של הקלדה נעלמים. מזהים את
   האלמנט לפי הנתיב שלו מתוך #content (המבנה זהה בין שתי בניות של אותו דף). */
function focusLocator() {
  const host = document.getElementById("content"), a = document.activeElement;
  if (!host || !a || a === document.body || typeof host.contains !== "function" || !host.contains(a)) return null;
  const path = [];
  for (let n = a; n && n !== host; n = n.parentElement) path.unshift(Array.prototype.indexOf.call(n.parentElement.children, n));
  const text = typeof a.value === "string" && a.type !== "radio" && a.type !== "checkbox";
  return { path, tag: a.tagName, value: text ? a.value : null, start: a.selectionStart, end: a.selectionEnd };
}
function restoreFocus(loc) {
  if (!loc) return;
  let n = document.getElementById("content");
  for (const i of loc.path) { n = n && n.children[i]; }
  if (!n || n.tagName !== loc.tag || typeof n.focus !== "function") return;
  n.focus({ preventScroll: true });
  if (loc.value !== null && n.value !== loc.value) n.value = loc.value;
  try { if (loc.start != null) n.setSelectionRange(loc.start, loc.end); } catch (e) {}
}
function renderCurrent() {
  const page = pages[current];
  if (!page || !pageAllowed(current)) return;
  // ‏#954: הסקירה נצבעת מחדש כל 2 שניות (refreshStatus) — בלי זה הגלילה
  // קופצת לראש הדף בכל דגימה. הגולל הוא .page (עמוד חדש) או .scroll (ישן).
  const scroller = document.querySelector("#content .page, #content .scroll");
  const scrollTop = scroller ? scroller.scrollTop : 0;
  const focus = focusLocator();
  document.getElementById("content").innerHTML = layout(page, currentTab);
  restoreFocus(focus);
  if (scrollTop) {
    const again = document.querySelector("#content .page, #content .scroll");
    if (again) again.scrollTop = scrollTop;
  }
  const search = document.getElementById("globalSearch");
  if (search) search.value = searchQuery || "";
  if (current === "network") wireNetPage();
  wireRestoredPage();
  if (typeof vmuGlobal === "function") vmuGlobal();   // ‏#1334: הפס "VM רץ" נמחק עם העמוד שנבנה מחדש
}

/* ---------- סקירה כללית (#954 גל 1) ----------
   נבנה לפי docs/design/console-redesign/home.md: שלוש שאלות — השרת בסדר?
   מה רץ עכשיו? מה דורש אותי? — מה-API הקיים בלבד. ‏#968 סגר את הפערים:
   uptime_seconds ו-deploy_ip ב-/me, severity ב-/journal, folder ב-/tasks —
   ומה שלא נמדד (null) מוצג בשם ("לא נבדק"), לא כנתון מומצא. הנתונים המתחלפים (‏/overview,
   ‏/tasks) מגיעים מה-polling הקיים; השאר נקרא בכניסה לדף וברענון.
   כל קריאה שנכשלה נשמרת בשם (HOME.err) ומוצגת — לא נקראה ≠ ריקה. */
const homeInit = () => ({ net: null, journal: null, nodes: null, transfers: null, ports: null,
                          mon: null, err: {} });
let HOME = homeInit();
let SECONDARY_STATUS = {};   // ‏id → {connected, error}, נמדד ב-markSecondaryStatus

function homeTabs() { return isAdmin() ? ["סיכום", "משימות", "אירועים"] : ["סיכום", "משימות"]; }

async function loadHome() {
  const jobs = [];
  const grab = (key, path) => jobs.push(api(path)
    .then((v) => { HOME[key] = v; delete HOME.err[key]; })
    .catch((e) => { HOME[key] = null; HOME.err[key] = e.message; }));
  jobs.push(refreshStatus());
  jobs.push(loadCaptures().then(() => { delete HOME.err.tasks; }, (e) => { HOME.err.tasks = e.message; }));
  grab("net", "/net");
  grab("ports", "/ports");
  if (isAdmin()) {
    jobs.push(loadHealth());
    grab("journal", "/journal?limit=50");
    grab("mon", "/monitor/machines");   // ‏#1300: "מחשבים מחוברים" (admin_only)
  }
  if (ME && ME.capabilities && ME.capabilities.enroll_secondary) {
    grab("nodes", "/storage-nodes");
    grab("transfers", "/storage-transfers");
  }
  await Promise.all(jobs);
  if (current === "home") renderCurrent();
}

function homeHealth() {
  // ‏null = לא נקרא (deploy: 403; admin: עוד לא/נכשל) — אפור, לא ירוק.
  if (!Array.isArray(HEALTH)) return { cls: "", label: "לא נבדק", failing: [], total: 0 };
  const failing = HEALTH.filter((c) => c.state === "bad" || c.state === "warn");
  const bad = failing.some((c) => c.state === "bad");
  return { cls: bad ? "err" : (failing.length ? "warn" : "ok"),
           label: bad ? "תקלה" : (failing.length ? "אזהרה" : "תקין"), failing, total: HEALTH.length };
}

/* ‏"מחובר" — מ-/monitor/machines (השרת, monitor.py: חלון 90ש', מחשבי בנייה ושיכפול), לא חישוב בדפדפן.
   ‏null = לא נקרא (deploy: admin_only; או קריאה שנכשלה) — לא 0 (עיקרון 5). */
function homeConnected() {
  return Array.isArray(HOME.mon) ? HOME.mon.filter((m) => m.online).length : null;
}

/* ‏#1300 (הצעה ג', הכרעת נדב 12/10): שלושה KPI — אימג'ים · מחשבים מחוברים · אחסון. "דיסק אדום" ו"סבב
   פעיל" ירדו מהשורה: הדיסק האדום — ב"דורש טיפול", הסבב — בכרטיס "סבב פעיל" ובדוק המשימות.
   ‏null = לא נקרא — "—" אפור, לא 0. */
function homeKpis() {
  const o = OVERVIEW || {};
  const on = homeConnected();
  const images = UI.kpi({ cls: o.images == null ? "" : "info", label: "אימג'ים", value: o.images == null ? "—" : o.images });
  const machines = o.machines == null ? UI.kpi({ label: "מחשבים", value: "—" })
    : on == null ? UI.kpi({ cls: "", label: "מחשבים רשומים", value: o.machines })
    : UI.kpi({ cls: on ? "ok" : "", label: "מחשבים מחוברים", value: `⁦${on} / ${o.machines}⁩` });   // LRI…PDI: "4 / 6" ולא "6 / 4" בתוך RTL
  return `<div class="c12 kpis">${images}${machines}${homeStorageKpi()}</div>`;
}

/* ‏#1300: כרטיס האחסון (כמו ESXi) — פנוי בגדול, פס, בשימוש וקיבולת. המקור: ‏/overview.storage
   (‏shutil.disk_usage על ספריית האימג'ים — אותו מקור של כרטיס האחסון הקודם בסקירה). ‏storage=null =
   השרת לא הצליח לקרוא — "לא ידוע", לא 0 ולא ירוק (עיקרון 5). הספים: כתום מ-90%, אדום מ-95%.
   ‏#1737: ועוד סף במספרים מול ‏`reserve_bytes` (הרזרבה למערכת — השרת מסרב לכל כתיבת אימג' שתרד
   מתחתיה): אדום מתחת לפעמיים הרזרבה, כתום מתחת לחמש פעמים. בדיסק גדול האחוז קובע; בדיסק קטן —
   הרזרבה, שאחרת הייתה נחצית לפני שהכרטיס האדים. ‏reserve_bytes חסר (שרת ישן) = האחוז בלבד. */
function homeStoragePct(st) {
  return st && st.free_bytes != null && Number(st.total_bytes) > 0
    ? Math.max(0, Math.min(100, Math.round(100 * (st.total_bytes - st.free_bytes) / st.total_bytes))) : null;
}
function homeStorageReserve(st) { const r = st ? Number(st.reserve_bytes) : 0; return r > 0 ? r : 0; }
function homeStorageLevel(pct, st) {
  const r = homeStorageReserve(st), free = st ? Number(st.free_bytes) : NaN;
  const byPct = pct >= 95 ? "err" : pct >= 90 ? "warn" : "";
  const byReserve = r && free < 2 * r ? "err" : r && free < 5 * r ? "warn" : "";
  return byPct === "err" || byReserve === "err" ? "err" : byPct || byReserve;
}
function homeStorageKpi() {
  const st = OVERVIEW ? OVERVIEW.storage : null, pct = homeStoragePct(st);
  const label = `<div class="l">${esc("אחסון אימג'ים")}</div>`;
  if (pct == null) return `<div class="kpi kpi-store "><div class="v">${OVERVIEW ? "לא ידוע" : "—"}</div>${label}`
    + (OVERVIEW ? `<div class="store-nums"><span>השרת לא החזיר נתוני אחסון</span></div>` : "") + `</div>`;
  const used = Math.max(0, st.total_bytes - st.free_bytes), lvl = homeStorageLevel(pct, st);
  return `<div class="kpi kpi-store ${lvl || "info"}"><div class="v"><bdi dir="ltr">${esc(fmtBytes(st.free_bytes))}</bdi> <small>פנוי</small></div>${label}`
    + `<div class="bar-row"><div class="bar ${lvl}" role="progressbar" aria-label="שימוש באחסון" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="--w:${pct}%"></i></div><span class="pct">${pct}%</span></div>`
    + `<div class="store-nums"><span>בשימוש <bdi dir="ltr">${esc(fmtBytes(used))}</bdi></span><span>קיבולת <bdi dir="ltr">${esc(fmtBytes(st.total_bytes))}</bdi></span></div></div>`;
}

/* משנה הכותרת = סיכום (console-full.html): "N אימג'ים · N מחשבים (N מחוברים) · עודכן HH:MM".
   ‏"עודכן" = שעת השרת בקריאת /overview האחרונה (`now`). מה שלא נקרא — לא מוצג. */
function homeSummary() {
  const o = OVERVIEW;
  if (!o) return "";
  const on = homeConnected(), parts = [];
  if (o.images != null) parts.push(`${o.images} אימג'ים`);
  if (o.machines != null) parts.push(`${o.machines} מחשבים${on != null ? ` (${on} מחוברים)` : ""}`);
  if (o.now) parts.push(`עודכן ${fmtClock(o.now)}`);
  return esc(parts.join(" · "));
}

/* כרטיס "סבב פעיל" (console-full.html P.home): שם האימג', פס, ושורת משנה — מחשבים · קצב.
   סבב שיכפול: ההתקדמות היא דיסקים שנכתבו מתוך היעד (‏/overview.room — אין בו בייטים), הקצב
   מ-throughput_bps (#989; ‏null = לא נמדד — לא מוצג). אין סבב = כפתור "התחל סבב". */
function homeRoundBlock(name, pct, cls, meta, extra = "") {
  const known = typeof pct === "number" && Number.isFinite(pct);
  const w = known ? Math.max(0, Math.min(100, pct)) : 0;
  return `<div class="round"><div class="rh"><b>${esc(name)}</b><span class="pct">${known ? w + "%" : "—"}</span></div>`
    + `<div class="bar ${cls}" role="progressbar" aria-valuemin="0" aria-valuemax="100"${known ? ` aria-valuenow="${w}"` : ""}><i style="--w:${w}%"></i></div>`
    + `<p class="rm">${meta.filter(Boolean).join(" · ")}</p>${extra}</div>`;
}
/* נדב 17/10 (room_855f6a13): סבב שיכפול חי הופיע פעמיים — כ-`/overview.room` וגל שלו כ-`.session`
   ("0% · 1 מחשבים" מול "19% · 1/3 מחשבים"). הגל של הסבב הוא חלק מהסבב, לא סבב נוסף. */
function overviewSession(o) {
  const s = o.session;
  return s && o.room && o.room.wave_session_id && s.id === o.room.wave_session_id ? null : s;
}
/* האחוז של סבב השיכפול: בזמן שגל משדר — הבייטים של הגל (כמו wavePct במסך החדר); אחרת דיסקים
   שנכתבו מתוך היעד. ‏null = לא נמדד. */
function roomRoundPct(o) {
  const r = o.room, s = o.session;
  if (s && r.wave_session_id && s.id === r.wave_session_id && s.state === "running") {
    const p = sessionProgressPct(s);
    if (p != null) return p;
  }
  return r.target_drives ? Math.round(100 * (r.written_drives || 0) / r.target_drives) : null;
}
function homeRoundCard() {
  const o = OVERVIEW || {};
  const blocks = [];
  let acts = "";
  if (o.room) {
    const r = o.room, failed = r.state === "failed";
    const machines = Array.isArray(r.target_slots) ? r.target_slots.length : null;
    blocks.push(homeRoundBlock(r.image_name || "סבב שיכפול", roomRoundPct(o), failed ? "err" : "", [
      failed ? esc(`נכשל: ${r.failed_reason || "סיבה לא ידועה"}`) : esc(stateLabel(r.wave_state)),
      machines != null ? esc(`${machines} מחשבים`) : "",
      esc(`${r.written_drives || 0}/${r.target_drives || 0} דיסקים נכתבו`),
      r.rate_bps != null ? ltr(fmtBytes(r.rate_bps) + "/s") : ""]));
    acts = `<button class="btn sm" onclick="selectPageById('deploy')">לחדר המשכפלים</button>`;
  }
  if (overviewSession(o)) {
    const s = o.session, failed = s.state === "failed", open = s.state === "open";
    const wait = open && s.starts_in_seconds != null
      ? `מתחיל בעוד ${Math.floor(s.starts_in_seconds / 60)}:${String(s.starts_in_seconds % 60).padStart(2, "0")}` : "";
    blocks.push(homeRoundBlock(sessionImage(s) || "סבב הפצה", sessionProgressPct(s), failed ? "err" : "", [
      esc(failed ? sessionStateText(s) : open ? "ממתין להצטרפות" : "משדר"), esc(sessionGroup(s)),
      esc(`${s.joined || 0}/${sessionExpected(s)} מחשבים`), esc(wait)],
      open ? `<div class="acts"><button class="btn sm" onclick="startRound()">התחל עכשיו</button></div>` : ""));
    if (!acts) acts = `<button class="btn sm" onclick="openRoundDetail()">פרטים</button>`;
  }
  const body = blocks.length ? blocks.join("")
    : UI.empty("בחרו אימג' ומחשבי יעד כדי להתחיל.", `<button class="btn primary" onclick="selectPageById('deploy')">התחל סבב</button>`, "אין סבב פעיל");
  return UI.card({ title: "סבב פעיל", acts, cls: isAdmin() ? "c6" : "c12", body });
}

/* שורות "מה קורה עכשיו": סבב, סבב שיכפול, משיכות, קליטות, העברות לשרתי המשנה. */
function homeNowRows(all = false) {
  const rows = [];
  const o = OVERVIEW || {};
  const s = overviewSession(o);
  if (s) {
    const open = s.state === "open";
    rows.push([UI.name(`סבב הפצה — ${sessionImage(s)}`, `${sessionGroup(s)} · ${sessionStateText(s)}`),
      esc(`${sessionExpected(s)} תחנות`), UI.barRow(sessionProgressPct(s), "", open ? `${s.joined || 0}/${sessionExpected(s)}` : null),
      UI.status(s.state === "failed" ? "err" : open ? "run" : "ok", s.state === "failed" ? sessionStateText(s) : open ? "ממתין להצטרפות" : "משדר"),
      UI.acts(open ? [["התחל עכשיו", "startRound()"], ["פרטים", "openRoundDetail()"]] : [["פרטים", "openRoundDetail()"]])]);
  }
  if (o.room) {
    const r = o.room, machines = Array.isArray(r.target_slots) ? r.target_slots.length : null;
    rows.push([UI.name(`סבב שיכפול — ${r.image_name || ""}`, `גל ${r.wave_number || 1} · פתח ${r.opened_by || ""}`),
      esc(`${machines != null ? machines + " מחשבים · " : ""}${r.target_drives || 0} דיסקים`),
      UI.barRow(roomRoundPct(o), "", `${r.written_drives || 0}/${r.target_drives || 0}`),
      UI.status(r.wave_state === "running" ? "run" : "warn", stateLabel(r.wave_state)),
      UI.acts([["לחדר המשכפלים", "selectPageById('deploy')"]])]);
  }
  for (const p of o.pulls || []) for (const m of p.members || []) {
    const v = Progress.view(m);
    rows.push([UI.name(`משיכה — ${p.image_name || ""}`, m.hostname || m.name || m.mac || ""), "יוניקאסט",
      UI.barRow(v.percent, "", v.label), UI.status("run", "מושכת"), ""]);
  }
  for (const t of CAPTURE_TASKS || []) {
    const active = captureInFlight(t);
    if (!all && !active) continue;
    const v = Progress.view(t);
    const cls = t.state === "failed" ? "err" : t.state === "done" ? (t.error ? "warn" : "ok") : t.state === "pending" ? "warn" : "run";
    const label = t.state === "pending" ? "ממתין שהמחשב יעלה ב-PXE" : t.state === "running" ? "קולט" : t.state === "verifying" ? VERIFYING_LABEL
      : t.state === "done" ? (t.error ? "הושלם עם אזהרה" : "הושלם") : t.state === "failed" ? "נכשל" : t.state;
    // ‏#1253: דיסק המקור כווץ לקליטה הזו — יוחזר בסופה (לא "דורש טיפול"). ‏/tasks שלא נקרא — לא כאן.
    const shrunk = active && t.state !== "verifying" && !HOME.err.tasks ? (SHRINK_RECORDS || []).filter((r) => shrinkOfTask(r, t))
      .map((r) => ` · ${esc(portLabel(findMachine(r.mac), r.port))} מכווץ — יוחזר בסוף הקליטה`).join("") : "";
    rows.push([UI.nameHtml(`קליטת אימג' — ${t.name || ""}`, `${esc(t.machine || t.mac || "")}${t.disk ? " · " + ltr(t.disk) : ""} · ${ltr(fmtDate(t.created_at) + " " + fmtClock(t.created_at))}${shrunk}`),
      t.folder ? esc(`תיקיית ${t.folder}`) : "שורש הספרייה", UI.barRow(v.percent, cls === "err" ? "err" : ""),
      UI.status(cls, label + (t.error ? " — " + t.error : "")), UI.acts([["לספרייה", "selectPageById('images')"]])]);
  }
  for (const t of HOME.transfers || []) {
    const active = ["queued", "sending", "verifying"].includes(t.state);
    if (!all && !active && t.state !== "failed") continue;
    const pct = t.bytes_total ? Math.round(100 * (t.bytes_sent || 0) / t.bytes_total) : null;
    const cls = t.state === "failed" ? "err" : t.state === "done" ? "ok" : t.state === "queued" ? "warn" : "run";
    const label = { queued: "ממתין בתור", sending: "שולח", verifying: "מאמת", done: "הושלם", failed: "נכשל" }[t.state] || t.state;
    const primary = ME.server_name || "ראשי";
    // ‏#1462: קצב/ETA/זמן שעבר — אותה פונקציה של דף שרתי המשנה (branches.js), רק ממה שנמדד.
    const pace = typeof transferPace === "function" ? transferPace(t) : "";
    const title = t.direction === "pull"
      ? `העברה ${t.node_label || t.node_id} → ${primary} — ${t.image_name || t.image_id}`
      : `העברה לשרת המשנה ${t.node_label || t.node_id} — ${t.image_name || t.image_id}`;
    rows.push([UI.nameHtml(title, `${ltr(fmtDate(t.created_at) + " " + fmtClock(t.created_at))} · ${esc(t.started_by || "")}`),
      esc(t.node_label || t.node_id || ""), UI.barRow(pct, cls === "err" ? "err" : t.state === "verifying" ? "indeterminate" : ""),
      UI.status(cls, label + (pace ? " · " + pace : "") + (t.error ? " — " + t.error : "")),
      UI.acts([["לשרתי המשנה", "selectPageById('branches')"]])]);
  }
  return rows;
}

function homeNowCard(all = false) {
  const unread = [HOME.err.tasks && "משימות", HOME.err.transfers && "העברות"].filter(Boolean);
  const warn = unread.length ? UI.note("warn", esc(`לא נקראו: ${unread.join(", ")} — ${HOME.err.tasks || HOME.err.transfers}`)) : "";
  const grid = UI.datagrid({ columns: ["פעילות", "יעד", "התקדמות", "מצב", ""], rows: homeNowRows(all),
    empty: all ? "אין משימות עדיין" : "אין פעילות עכשיו — פתח סבב הפצה או קלוט אימג'" });
  return UI.card({ title: all ? "משימות" : "מה קורה עכשיו", small: all ? "20 הקליטות האחרונות וכל ההעברות" : "מתעדכן כל 2 שניות",
    cls: all || !isAdmin() ? "c12" : "c6", flush: true, body: warn + grid });   // ‏deploy: בלי "דורש טיפול" לצידו
}

/* "דורש טיפול" (apple-pending-4 · 4; ‏#1300: פתוח כברירת מחדל): שורה מתחת לכותרת עם מונה כתום, וכפתור פעולה לכל פריט.
   דיסקים אדומים, דיסקים מכווצים, מכונות לא רשומות, שרתי משנה שלא מגיבים, בדיקות בריאות
   warn/bad. ריק אמיתי ≠ "לא נקרא" (עיקרון 5). טקסט גולמי באנגלית (שגיאת מערכת מהשרת)
   לא מוצג בגוף: הוא עובר ל"פרטים טכניים" בתוך הפריט. */
let HOME_ATTN_OPEN = true;   // ‏#1300: פתוח כברירת מחדל (console-full.html); סגירה נשמרת בין רענונים
/* ‏#1253: רשומת כיווץ פתוחה מול משימות הקליטה. הרשומה שייכת לקליטה שלה
   (`task_id`); רשומה בלי task_id (מלפני השדה) — לקליטה על אותה מכונה. */
function shrinkOfTask(r, t) { return r.task_id ? t.id === r.task_id : t.mac === r.mac; }
/* "active" רק בראיה חיובית: המשימה של הרשומה ב-pending/running ב-/tasks
   שנקרא עכשיו. ‏/tasks שלא נקרא (או שהקריאה האחרונה נכשלה) = "unknown" —
   לא הסתרה ולא "נשכח" (עיקרון 5). משימה שאינה ברשימה = "stale": האזהרה. */
function shrinkCaptureState(r) {
  if (!CAPTURE_TASKS_READ || HOME.err.tasks) return "unknown";
  return (CAPTURE_TASKS || []).some((t) => (t.state === "pending" || t.state === "running") && shrinkOfTask(r, t)) ? "active" : "stale";
}
/* טקסט גולמי = רוב האותיות לטיניות (שגיאת מערכת/נתיב/שם קובץ): "timeout", "udp-sender לא נמצא".
   משפט עברי שמזכיר "SATA" או שם מכונה נשאר בגוף הפריט. */
function isRawTechnical(text) {
  const t = String(text == null ? "" : text);
  const latin = (t.match(/[A-Za-z]/g) || []).length, hebrew = (t.match(/[֐-׿]/g) || []).length;
  return latin >= 4 && latin > hebrew;
}
/* ‏action = [תווית, onclick] — כפתור בעמודה נפרדת בקצה השורה (console-full.html P.home), לא קישור בתוך הטקסט. */
function attnItem(cls, mainHtml, action = null, tech = "") {
  const techHtml = tech ? `<details class="tech"><summary>פרטים טכניים</summary><code dir="ltr">${esc(tech)}</code></details>` : "";
  const btn = action ? `<button class="btn sm flat" onclick="${action[1]}">${esc(action[0])}</button>` : "";
  return `<li class="attn-i ${cls}"><span class="dot" aria-hidden="true"></span><div class="b">${mainHtml}${techHtml}</div>${btn}</li>`;
}
function homeAttention() {
  const items = [];
  // ‏#1300: אחסון מ-90% — שורה כאן, באותם ספים של כרטיס האחסון (כתום 90%, אדום 95%);
  // ‏#1737: וגם כשהפנוי מתקרב לרזרבה למערכת — אז השורה אומרת מה השרת שומר ולמה.
  const st = OVERVIEW && OVERVIEW.storage, spct = homeStoragePct(st);
  const slvl = spct == null ? "" : homeStorageLevel(spct, st), keep = homeStorageReserve(st);
  const keepNote = keep && st.free_bytes < 5 * keep ? ` השרת שומר ${ltr(fmtBytes(keep))} למערכת (ה-DB, היומנים ועדכוני השרת), ומסרב לכל קליטה, העלאה או העברה של אימג' שתשאיר פחות מזה.` : "";
  if (slvl) items.push(attnItem(slvl, `<b>אחסון האימג'ים כמעט מלא</b> — ${ltr(spct + "%")} בשימוש, נשארו ${ltr(fmtBytes(st.free_bytes))} פנויים.${keepNote}`, ["לאחסון", "selectPageById('storage')"]));
  // נדב 18/10: שום מידע על דיסקים כאן — לא כשלי כתיבה (אדום/כתום), לא SMART ולא "נקה אחרי החלפה". הם בדף
  // "מחשבים" (לשונית "דיסקים אדומים", עם "נקה"), בחלון המכונה ("נקה אחרי החלפה"), במגירות ובמסכי המשכפל;
  // דף "דיסקים" — #1765.
  for (const r of SHRINK_RECORDS || []) {
    const st = shrinkCaptureState(r);
    if (st === "active") continue;   // ‏#1253: יוחזר בסוף הקליטה — שורה ב"מה קורה עכשיו"
    const where = `${esc(machineName(findMachine(r.mac)) || r.mac)} · ${esc(portLabel(findMachine(r.mac), r.port))}`;
    items.push(attnItem("warn", st === "unknown"
      ? `<b>דיסק מכווץ — לא נבדק</b> — ${where} כווץ לקליטה. רשימת הקליטות לא נקראה, ולכן לא ידוע אם הקליטה עדיין רצה ותחזיר אותו בסופה.`
      : `<b>דיסק מכווץ</b> — ${where} כווץ לקליטה ולא הוחזר לגודלו.`, ["למחשבים", "selectPageById('machines')"]));
  }
  for (const d of HOME.net || []) {
    if (d.registered) continue;
    items.push(attnItem("warn", `<b>מכונה לא רשומה</b> ברשת ההפצה — <span class="mono">${esc(d.mac)}</span>${d.ip ? ` קיבלה ${ltr(d.ip)}` : ""} · ${ltr(fmtDate(d.last_seen) + " " + fmtClock(d.last_seen))}.`, ["רשום", "openNewMachine()"]));
  }
  for (const n of HOME.nodes || []) {
    const st = SECONDARY_STATUS[n.id];
    if (n.disabled_at || !st || st.connected) continue;
    const raw = st.error && isRawTechnical(st.error);
    items.push(attnItem("err", `<b>שרת משנה ${esc(n.label)} לא מגיב</b>${st.error && !raw ? " — " + esc(st.error) : ""}.`, ["פרטי שרת המשנה", "selectPageById('branches')"], raw ? st.error : ""));
  }
  // נדב 18/10: בלי כרטיס "שרתי משנה" בסקירה — רשימה שלא נקראה נאמרת כאן, גם כשיש פריטים אחרים
  // (עד כאן היא הופיעה רק כשהרשימה ריקה, ובכרטיס). לא נקרא ≠ אין שרתי משנה (עיקרון 5).
  if (HOME.err.nodes) {
    const raw = isRawTechnical(HOME.err.nodes);
    items.push(attnItem("warn", `<b>רשימת שרתי המשנה לא נקראה</b>${raw ? "" : " — " + esc(HOME.err.nodes)} — לא ידוע אם הם עונים.`, ["לשרתי המשנה", "selectPageById('branches')"], raw ? HOME.err.nodes : ""));
  }
  for (const c of Array.isArray(HEALTH) ? HEALTH : []) {
    if (c.state !== "warn" && c.state !== "bad") continue;
    const action = c.id === "deploy_net" ? ["הגדר רשת הפצה", "openNetwork(2)"] : ["לבריאות", "selectPageById('health')"];
    const raw = c.detail && isRawTechnical(c.detail);
    items.push(attnItem(c.state === "bad" ? "err" : "warn", `<b>${esc(c.label)}</b>${c.detail && !raw ? " — " + esc(c.detail) : ""}.`, action, raw ? c.detail : ""));
  }
  // ‏#1049 שלב ב': שערי בדיקת המכונה (סוללה, שעון, NVMe, קריסה, כפילות IP, CRC)
  // — מוצגים, אינם עוצרים סבב. רשימה ריקה = לא נמצא במה שנמדד, לא "הכול תקין".
  for (const m of MACHINES || []) {
    for (const v of Array.isArray(m.probe_verdicts) ? m.probe_verdicts : []) {
      if (String(v.key || "").startsWith("nvme:")) continue;   // נדב 18/10: SMART של דיסק — בחלון המכונה, לא כאן
      items.push(attnItem(v.level === "err" ? "err" : "warn", `<b>${esc(machineName(m) || m.mac)}</b> — ${esc(v.text_he || v.key || "")}.`, ["פרטי המכונה", `openMachineDetail('${encodeId(m.mac)}')`]));
    }
  }
  // ‏#1997: שם שנרשם לפני הכלל ואינו שם מחשב — השרת מסמן (name_problem / label_problem /
  // server_name_problem), והפריט פותח את שינוי השם. אין מיגרציה שמשנה שם לבד.
  const nameItem = (what, name, problem, onclick) => attnItem("warn",
    `<b>${what} לא חוקי — ${esc(name)}</b> — ${esc(problem)}.`, ["שינוי שם", onclick]);
  if (!SERVER_CTX && ME && ME.server_name_problem) items.push(nameItem("שם השרת", ME.server_name, ME.server_name_problem, "hostnameSheet()"));
  for (const n of HOME.nodes || []) {
    if (n.label_problem) items.push(nameItem("שם שרת משנה", n.label, n.label_problem, `renameBranchNode('${encodeId(n.id)}')`));
  }
  for (const m of MACHINES || []) {
    if (m.name_problem) items.push(nameItem("שם מחשב", machineName(m), m.name_problem, `renameMachine('${encodeId(m.mac)}')`));
  }
  const unread = [!Array.isArray(HEALTH) && "בריאות", HOME.err.net && "רשת"].filter(Boolean);   // שרתי משנה — פריט משלו, למעלה
  if (!items.length) {
    // ריק אמיתי = שורה שקטה שאומרת זאת; "לא נקרא" = הודעה בשם (לא ניתן לומר שאין מה לטפל)
    return unread.length ? `<div class="c12">${UI.note("warn", esc(`לא נקראו: ${unread.join(", ")} — לא ניתן לומר שאין מה לטפל`))}</div>` : `<div class="c12 attn-none">אין פריטים לטיפול</div>`;
  }
  const worst = items.some((i) => i.includes('attn-i err')) ? "err" : "warn";
  return `<details class="c12 attn ${worst}" id="home-attn"${HOME_ATTN_OPEN ? " open" : ""} ontoggle="HOME_ATTN_OPEN=this.open"><summary>${uiIcon("warn")}<span>דורש טיפול</span><span class="cnt">${items.length}</span></summary><ul>${items.join("")}</ul></details>`;
}

/* ‏#968: החומרה מגיעה מהשרת (severity ב-/journal, נקבעת ב-journal_he.py לצד
   התרגום) — לא היוריסטיקה על שם ה-event. כאן רק מיפוי לשם המחלקה בציר-הזמן;
   ערך זר/חסר = "info" (ניטרלי), לא ניחוש. */
const JOURNAL_CLS = { ok: "ok", warn: "warn", err: "err", info: "info" };
function journalCls(row) { return JOURNAL_CLS[row && row.severity] || "info"; }

function homeEvents(limit) {
  const journal = Array.isArray(HOME.journal) ? HOME.journal : null;
  const acts = `<button class="btn sm flat" onclick="selectPageById('logs')">ליומן המלא ←</button>`;
  let body;
  if (!journal) body = UI.note("warn", esc("היומן לא נקרא" + (HOME.err.journal ? ": " + HOME.err.journal : " עדיין")));
  else if (!journal.length) body = UI.empty("אין אירועים עדיין");
  else body = UI.timeline(journal.slice(0, limit).map((r) => ({
    t: isToday(r.ts) ? fmtClock(r.ts) : fmtDate(r.ts), cls: journalCls(r),
    text: esc(r.label || r.text || r.event || ""), who: r.user || "" })));
  return UI.card({ title: limit >= 50 ? "אירועים" : "אירועים אחרונים", small: limit >= 50 ? "50 האחרונים" : "", acts, cls: "c12", body });
}

/* ‏"שירותים" (console-full.html P.home, #1300): ארבעה אריחים קצרים — שם, נקודת מצב ומילה אחת.
   ‏/ports ו-DHCP (‏/net של net.js); הפירוט (פורט, שגיאה) ב-title של האריח ובדף הבריאות.
   לא נקרא נשאר אפור ו"לא נבדק" — לא ירוק (עיקרון 5). שורת המשנה של הכרטיס: זהות השרת
   (שם · כתובת ההפצה · גרסה · זמן פעילות, ‏#968) — שורה שקטה מתחת לאריחים; עברה לכאן מכותרת העמוד. */
const SVC_WORD = { ok: "פעיל", warn: "דורש בדיקה", err: "תקלה" };
function homeServerCard() {
  const ports = Array.isArray(HOME.ports) ? HOME.ports : null;
  const port = (id) => {
    if (!ports) return ["", "לא נבדק", HOME.err.ports ? "לא נקרא: " + HOME.err.ports : "לא נקרא עדיין"];
    const p = ports.find((x) => x.id === id);
    if (!p) return ["", "לא נבדק", "לא מדווח"];
    const cls = healthStatusClass(p.state);
    return [cls, SVC_WORD[cls] || healthStatusLabel(p.state), `${healthStatusLabel(p.state)} · ${p.port}`];
  };
  const nic = (typeof NICS !== "undefined" && Array.isArray(NICS)) ? NICS.find((n) => n.enabled) : undefined;
  const dcls = nic ? dhcpLiveClass(nic.dhcp_live && nic.dhcp_live.state) : "";
  const dhcp = nic ? (dcls === "ok" || dcls === "warn" ? [dcls, SVC_WORD[dcls]] : ["", nic.dhcp_live_label || "לא נבדק"]).concat(`${nic.name} · ${nic.dhcp_live_label || ""}`)
    : (nic === undefined ? ["", "לא נבדק", "לא נקרא"] : ["", "לא מוגדר", "לא מוגדר"]);
  const tiles = [["שרת", ...port("http_console")], ["פורט TFTP", ...port("tftp")], ["DHCP הפצה", ...dhcp], ["מולטיקאסט", ...port("multicast")]];
  const acts = `<button class="btn sm flat" onclick="selectPageById('health')">לבריאות</button>`;
  const body = `<div class="tiles">${tiles.map(([name, cls, word, detail]) => `<div class="tile" title="${esc(detail)}"><span class="led ${cls}"></span><div><div class="tt">${esc(name)}</div><small>${esc(word)}</small></div></div>`).join("")}</div>`;
  const who = [esc(ME && ME.server_name || ""), homeDeployIp(), ME && ME.version ? esc(ME.version) : "", homeUptime()].filter(Boolean).join(" · ");
  return UI.card({ title: "שירותים", acts, cls: "c6", body: body + (who ? `<div class="svc-who">${who}</div>` : "") });
}

/* נדב 18/10: כרטיס "שרתי משנה" יצא מהסקירה — "יש בשביל זה את הלשוניות" (צמתי המשנים בעץ).
   ההתראות נשארות: שרת משנה שלא מגיב או רשימה שלא נקראה — ב"דורש תשומת לב" (homeAttention). */

/* ‏#1300 (הצעה ג'): "מה קורה עכשיו" הוא דוק המשימות — נפתח לבד כשמתחילה פעילות (מעבר מ-0 ליותר),
   ולא נפתח שוב אחרי שהמפעיל סגר אותו באמצע אותה פעילות. הספירה — activityRows של הדוק עצמו. */
let HOME_DOCK_HAD = null;   // null = עוד לא נדגם אחרי כניסה: פעילות שכבר רצה בכניסה לא פותחת את הדוק (נדב 17/10)
function homeDockAuto() {
  try {
    const n = activityRows().rows.length;
    const panel = document.getElementById("taskPanel");
    if (HOME_DOCK_HAD === null) { HOME_DOCK_HAD = n > 0; return; }
    if (n && !HOME_DOCK_HAD && panel && !panel.classList.contains("open")) toggleTaskPanel();
    HOME_DOCK_HAD = n > 0;
  } catch (e) { /* קוסמטי — בלי DOM מלא לא נופלים */ }
}

/* ‏#968: "פעיל 6 ימים 3 שעות" מ-/me.uptime_seconds (המכונה, /proc/uptime);
   ‏null = השרת לא הצליח לקרוא → "זמן פעילות לא נבדק" — בשם, לא 0 (עיקרון 5). */
function fmtUptime(seconds) {
  const s = Number(seconds);
  if (seconds == null || !Number.isFinite(s) || s < 0) return "";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d} ${d === 1 ? "יום" : "ימים"} ${h} ${h === 1 ? "שעה" : "שעות"}`;
  if (h) return `${h} ${h === 1 ? "שעה" : "שעות"} ${m} ${m === 1 ? "דקה" : "דקות"}`;
  return `${m} ${m === 1 ? "דקה" : "דקות"}`;
}
/* ‏#1932: בהקשר שרת משנה — מה-/overview שלו (‏/me אינו עובר בערוץ, ו-ME.uptime_seconds שם null). */
function homeUptime() {
  const s = SERVER_CTX ? (OVERVIEW ? OVERVIEW.uptime_seconds : null) : (ME ? ME.uptime_seconds : null);
  if (s == null) return `<span class="muted">זמן פעילות לא נבדק</span>`;
  return esc(`פעיל ${fmtUptime(s)}`);
}
/* ‏#968: כתובת השרת בעיני התחנות (/me.deploy_ip); ‏null = רשת ההפצה לא הוגדרה (#1088). */
function homeDeployIp() {
  if (!ME || !("deploy_ip" in ME)) return "";
  return ME.deploy_ip ? `<span class="mono">${esc(ME.deploy_ip)}</span>` : `<span class="muted">רשת הפצה לא הוגדרה</span>`;
}

function home(tab = 0) {
  const tabs = homeTabs();
  const o = OVERVIEW;
  const h = homeHealth();
  const pill = isAdmin()
    ? (Array.isArray(HEALTH) ? UI.pill(h.cls, !h.failing.length ? "שרת פעיל" : h.cls === "err" ? `${h.failing.length} ${h.failing.length === 1 ? "תקלה" : "תקלות"}` : `${h.failing.length} ${h.failing.length === 1 ? "אזהרה" : "אזהרות"}`) : UI.pill("", "לא נבדק"))
    : (o && o.session ? UI.pill(o.session.state === "failed" ? "err" : "info", sessionStateText(o.session)) : UI.pill("", "אין סבב"));
  // ‏#1300: כמו המוקאפ — "סבב חדש" ורענון. הקליטה — בדף האימג'ים ("קליטה ממחשב בנייה").
  const actions = `<button class="btn primary" onclick="selectPageById('deploy')">${uiIcon("plus")} סבב חדש</button>`
    + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים" }, { label: "סקירה כללית" }], icon: "t-home",
    name: "סקירה כללית", sub: homeSummary(), pill, actions, tabs, tab, compact: true });
  const stale = overviewError ? `<div class="c12">${UI.note("warn", esc(overviewError))}</div>` : "";
  let body;
  if (tab === 1) body = homeNowCard(true);
  else if (tab === 2 && isAdmin()) body = homeEvents(50);
  else if (!o && !overviewError) body = `<div class="c12">${pagePlaceholder()}</div>`;
  else body = (isAdmin() ? homeAttention() : "") + homeKpis() + homeRoundCard() + (isAdmin() ? homeServerCard() : "");
  homeDockAuto();
  return `<div class="page aligned">${header}<div class="body">${stale}${body}</div></div>`;
}

/* ---------- #954 גל 4: סבב הפצה — חדר המשכפלים (#695) ----------
   נבנה לפי docs/design/console-redesign/deploy.md (§"המבנה — חדר המשכפלים", מוקאפ
   deploy-room.png שנדב אישר 17/09). v1 = משכפלים בלבד (הכרעת נדב 17/09 10:20): הלשונית
   הראשונה היא החדר; "כיתה" = קוד ה-session הקיים כמו שהוא, עטוף בכרטיס; "היסטוריה" =
   דורש API. ‏18/10 (נדב: "לא לפי המוקאפ"): הלשונית הראשונה בנויה אחד-לאחד לפי
   docs/design/apple-mockups/console-full.html (P.deploy) — שלושה אריחים, כרטיס "חדר המשכפלים"
   וכרטיס לכל משכפל (roomMachHtml); בלי סבב — מצב ריק עם "התחל סבב". מקורות: /room (round, machines[].drawer_list,
   stream_stalled, disk_floor #953), /images (min_target_bytes #953), /machines (prompt —
   השאלה למפעיל #906), /disk-failures (אדום), /net (IP). כתיבה — רק ה-endpoints הקיימים:
   POST /room, /room/start, /room/wake, /room/close (חלון אישור, #1270); בלי שינוי שרת.
   ההכרעה על SMART (נדב 16/09): כתום = בלי תשובה ממשיך לכתוב, אדום = בלי תשובה מדלג —
   לעולם לא דילוג בשני המקרים; היא נענית ליד המכונה או מהקונסולה (#989 ס׳ 2, POST /room/answer — הראשונה גוברת);
   קצב הזרם ובלוקים ששודרו שוב — מ-/room.round (#989). מה שאין לו API — מספר הגלים הכולל, היסטוריה, דילוג מרחוק — טקסט,
   לא נתון מומצא (README §8, עיקרון 5). */
const DEPLOY = { err: "", roomErr: "", busy: false, form: { image: "", folder: null, src: "library", builder: "", disk: "", target: "", drv: false, drvMap: {}, sel: {} } };
/* ‏#1269: ‏GET /room/driver-models — ‏null = לא נקרא (≠ [] אין בספרייה דגם עם חבילה) */
let DRIVER_MODELS = null;
const WAVE_HE = { open: "ממתין להצטרפות", running: "משדר", failed: "הגל נכשל", closed: "הגל נסגר" };

function deployTabs() {
  return classroomsOn() ? ["חדר המשכפלים", "כיתה", "היסטוריה"] : ["חדר המשכפלים", "היסטוריה"];
}
function roomOperator() { return !!ME && ["admin", "deploy"].includes(ME.role); }   // ‏room.ROOM_OPERATOR_ROLES
/* רשומות המחשבים לגריד: המגירות תמיד מ-/room (‏drawer_list נושא port/serial/model/size/smart
   גם בלי סבב) — כך גם deploy, שאינו מגיע לדף המחשבים, רואה את החדר במלואו; prompt
   ו-drawer_count המוגדר מ-/machines כשנקרא. */
function roomMachines() {
  return sortMachinesBySuffix((ROOM && ROOM.machines || []).map((rm) => {
    const m = findMachine(rm.mac) || {};
    return { ...m, mac: rm.mac, suffix: machineName(m) || rm.name || rm.mac, group_id: m.group_id || "grp_CLONERS",
      drawer_count: rm.drawer_count != null ? rm.drawer_count : (m.drawer_count ?? null),
      drawer_slots: Array.isArray(rm.drawer_slots) ? rm.drawer_slots : (m.drawer_slots ?? null),   // #1485
      disks: Array.isArray(rm.drawer_list) ? rm.drawer_list : (m.disks ?? null), prompt: m.prompt || null,
      disk_probe: rm.disk_probe ?? m.disk_probe ?? null };
  }));
}
/* נדב 17/10: במסך הסבב — רק המשכפלים שהסבב כותב אליהם (‏round.target_slots); בלי סבב, או סבב
   ישן בלי בחירה (null), — כולם, כמו במסך הבחירה. אותו כלל בגואי (guistate.sh). */
function roundMachines(kids, r) {
  if (!r || !Array.isArray(r.target_slots)) return kids;
  const macs = new Set(r.target_slots.map((t) => t.mac));
  return kids.filter((m) => macs.has(m.mac));
}
/* אחוז הגל: ממוצע המגירות שהצטרפו ומדווחות (‏done = 100). ‏null = אין דיווח, לא 0 (עיקרון 5). */
function wavePct() {
  const ds = (ROOM && ROOM.machines || []).filter((rm) => rm.joined).flatMap((rm) => rm.drawer_list || [])
    .filter((d) => d.state === "done" || (d.state && d.bytes_total > 0));
  if (!ds.length) return null;
  return Math.round(ds.reduce((s, d) => s + (d.state === "done" ? 100 : 100 * (d.bytes_written || 0) / d.bytes_total), 0) / ds.length);
}
function roomStats(kids) {
  const all = kids.map((m) => ({ m, slots: machineSlots(m).slots }));
  const pick = (fn) => all.flatMap(({ m, slots }) => slots.filter((s) => fn(s, m)).map((s) => ({ m, s })));
  return {
    slots: all.reduce((n, x) => n + x.slots.length, 0), filled: pick((s) => s.disk).length,
    writing: pick((s) => s.live && (s.live.state === "writing" || s.live.state === "verifying")),
    red: pick((s) => s.disk && slotClass(s) === "err"), warn: pick((s) => s.disk && slotClass(s) === "warn"),
    empty: pick((s, m) => !s.disk && !!(roomMachine(m.mac) || {}).awake),   // חריץ פנוי = במכונה מחוברת; מכונה כבויה נספרת פעם אחת
    off: kids.filter((m) => { const rm = roomMachine(m.mac); return rm && !rm.awake; }),
    awake: kids.filter((m) => { const rm = roomMachine(m.mac); return rm && rm.awake; }).length,
    prompts: kids.filter((m) => m.prompt),
  };
}
/* "מחשב 1 · דיסק 1,2 — מחשב 2 · דיסק 1" */
function whereList(items) {
  const by = new Map();
  items.forEach(({ m, s }) => { const k = machineName(m); if (!by.has(k)) by.set(k, []); by.get(k).push(s.n != null && slotName(m, s.n, s.disk) ? slotName(m, s.n, s.disk) : s.n); });
  /* ‏#2001: שם החיבור במקום המספר; בלי שם — "דיסק 1,2" כמו קודם. */
  return [...by].map(([k, ns]) => `${k} · ${ns.every((x) => typeof x === "number" || x == null) ? `דיסק ${ns.join(",")}` : ns.map((x) => typeof x === "number" ? `דיסק ${x}` : x).join(", ")}`).join(" — ");
}
/* מצב כרטיס בחדר: ‏awake מ-/room (דיברה עם השרת ב-30 השניות האחרונות — ראיה חיובית),
   "בסבב · N%" כשהצטרפה, השאלה למפעיל כשיש. */
function roomCardState(m) {
  const rm = roomMachine(m.mac);
  if (!rm) return { cls: "", text: "לא בחדר" };
  if (roomRound() && rm.joined) {
    const state = clonerState(m);
    return state.cls === "err" ? state : { cls: "ok", text: "מחובר", round: state.text.replace(/^בסבב · /, "") };
  }
  if (rm.held) return { cls: "warn", text: "דלוק — מחכה ללחיצה", held: true };   // #2031: לא "לא מחובר"
  if (!rm.awake) return { cls: "", text: "לא מחובר", off: true };
  if (m.prompt) return { cls: "warn", text: waitingText(m) };
  return { cls: "ok", text: "מחובר" };
}
/* ‏#2031: משכפל שמחזיק את התוצאות עד "כבה" (#2024) — דלוק, אינו שולח hello ואינו פנוי לסבב.
   ‏held = ‏{at, problems:[{disk, level, reason}]} מ-/machines ומ-/room; ‏disk = שם ההתקן של הסוכן, מתורגם לדיסק N. */
const HELD_HE = "דלוק — מחכה ללחיצה: יש דיסק עם בעיה";
function heldLine(m, h) {
  const ps = (h && Array.isArray(h.problems) ? h.problems : []).map((p) => {
    const d = Array.isArray(m.disks) ? m.disks.find((x) => x.dev === p.disk) : null;
    return `${d ? diskLabel(m, d) : p.disk || "דיסק"} — ${p.reason}`;
  });
  return ps.length ? ps.join("; ") : "המגירות הבעייתיות לא נמסרו";
}
function heldButton(m) {
  return roomOperator() ? `<button class="btn sm danger" type="button" onclick="event.stopPropagation();poweroffHeld('${encodeId(m.mac)}')" title="${esc(HELD_HE)}">כבה</button>` : "";
}
/* "כבה" — בקשה שנוסעת בתשובת הדיווח held הבא (עד כ-15 ש'), והסוכן מכבה דרך safe_poweroff (WoL נדרך קודם). */
function poweroffHeld(macEnc) {
  const mac = decodeURIComponent(macEnc), m = findMachine(mac) || { mac };
  if (!roomOperator()) return;
  const name = machineName(m) || mac;
  sheet({ title: `לכבות את ${name}?`, sub: HELD_HE, danger: true, submitLabel: "כבה",
    note: impactList([["b", "כמו לחיצה על 'כבה' במסך של המחשב — התוצאות שעל המסך נסגרות"],
      ["ok", "Wake-on-LAN נדרך לפני הכיבוי: הסבב הבא יעיר אותו"]]),
    onSubmit: async () => { await post("/machines/" + encodeId(mac) + "/poweroff", { confirm_name: m.suffix }); toast(`בקשת כיבוי נשלחה ל-${name} — תגיע בדיווח הבא שלו (עד כ-15 שניות)`, 6000); } });
}
/* ‏#953: הסיבה שאימג' אינו נכנס למגירה הקטנה ביותר (‏disk_floor מ-GET /room) — **אותה
   מחרוזת בדיוק** ש-`room.fit_refusal` בשרת מחזיר ב-409, כדי שהמפעיל יראה סיבה אחת שרת משנה
   הצדדים. null = נכנס, או אין רצפה/דרישה ידועה. GB עשרוני כמו על מדבקת הכונן. */
function imageFitReason(img, floor) {
  const need = Number(img.min_target_bytes);
  if (!floor || img.min_target_bytes == null || !Number.isFinite(need) || need < 0 || need <= floor.size_bytes) return null;
  const slot = floor.label || (floor.port != null ? portLabel(findMachine(floor.mac), floor.port) : `דיסק ${floor.dev || "?"}`);
  /* ‏#1354: "צריך" כולל את המינימום הפנוי במחיצת המערכת — וכשהוא שהוסיף, המשפט אומר זאת (room.fit_refusal). */
  const layout = Number(img.layout_bytes);
  const extra = img.layout_bytes != null && Number.isFinite(layout) && need > layout ? ` (כולל ${img.min_free_gb}GB פנויים במחיצת המערכת)` : "";
  return `${slot} במחשב ${floor.name} הוא ${Math.round(floor.size_bytes / 1e9)}GB, האימג' צריך ${Math.ceil(need / 1e9)}GB${extra}`;
}
/* ‏#1494: אימג' שכלל המקום הפנוי אינו ניתן לבדיקה עליו (`used_bytes` של מחיצת המערכת לא נמדד) —
   הסוכן מסרב לו בכל מגירה, ולכן הוא מושבת עם הסיבה (מחרוזת הסוכן, `min_free_unchecked` מהשרת)
   ולא מוצע כ"גודל נדרש לא ידוע". null = אין חסימה כזו. השרת מסרב לו גם ב-POST /room (409). */
function imageDeployBlock(img) {
  return img && typeof img.min_free_unchecked === "string" && img.min_free_unchecked ? img.min_free_unchecked : null;
}
function roomImageLabel(r) {
  const direct = r.source && r.source.kind === "build_disk";
  if (direct) return esc(`מקור: מחשב הבנייה (${r.source.name || r.source.mac}:${r.source.disk})`);
  const img = (IMAGES || []).find((x) => x.id === r.image_id);
  return esc(r.image_name || r.image_id || "—") + (img && img.total_compressed_bytes ? ` · ${ltr(fmtBytes(img.total_compressed_bytes))} בשרת` : "");
}

function deploy(tab = 0) {
  const tabs = deployTabs(), r = roomRound(), kids = roundMachines(roomMachines(), r), st = roomStats(kids), pct = wavePct();
  const stalled = !!(ROOM && ROOM.stream_stalled), op = roomOperator();
  const classTab = classroomsOn() ? 1 : -1, histTab = classroomsOn() ? 2 : 1;
  /* מוקאפ console-full.html (P.deploy): "סבב הפצה" + תגית; שורת משנה "גל N · אימג' · N מחשבים". אין לסבב מספר
     סידורי ב-API (‏room_rounds.id הוא room_xxxx) — לכן הגל, לא "סבב 12". בורר מצב-הדוגמה ומתג השינויים שבראש המוקאפ
     הם כלי המוקאפ בלבד, לא המוצר. */
  let sub, pill;
  if (r) {
    const what = r.source && r.source.kind === "build_disk" ? roomImageLabel(r) : esc(r.image_name || r.image_id || "—");
    // ‏#1331 (מוקאפ 2): סבב עם דרייברים אומר זאת בשורת המשנה, אחרי האימג'
    sub = [`גל ${r.wave_number || 1}`, what, roomAnyDrivers(kids, r) ? "חבילת דרייברים לפי דגם המגירה" : "", `${kids.length} ${kids.length === 1 ? "מחשב" : "מחשבים"}`].filter(Boolean).join(" · ");
    pill = r.state === "failed" ? UI.pill("err", `נכשל: ${r.failed_reason || "סיבה לא ידועה"}`) : stalled ? UI.pill("warn", "הזרם עצר")
      : r.wave_state === "running" ? UI.pill("info", "פעיל")
      : r.wave_state === "open" ? UI.pill("info", `ממתין להצטרפות · ${r.ready_drives || 0} מוכנים`) : UI.pill("", WAVE_HE[r.wave_state] || r.wave_state || "—");
  } else {
    sub = ROOM == null ? "החדר לא נקרא" : "אין סבב פעיל";
    pill = UI.pill("", ROOM == null ? "החדר לא נקרא" : "פנוי");
  }
  const actions = tab === 0 && ROOM ? (op && !r ? `<button class="btn primary" onclick="openRoomRound()">${uiIcon("plus")} התחל סבב</button>` : "")
    + (op && r && r.state === "failed" ? `<button class="btn primary" onclick="reopenRoom()">פתח מחדש</button>` : "")
    + (op && r && r.state !== "failed" && r.wave_state === "open" ? `<button class="btn primary" onclick="startWave()">התחל גל (${r.ready_drives || 0} מוכנים)</button>` : "")
    + (op && r ? `<button class="btn danger" onclick="stopRoom()">עצירת הסבב…</button>` : "") : "";
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "סבב הפצה" }], icon: "t-deploy",
    name: tab === 0 ? "סבב הפצה" : tab === classTab ? "סבב כיתה" : "היסטוריית סבבים", sub: tab === 0 ? sub : tab === classTab ? "מחשבי כיתה — v2 (הכרעת נדב 17/09); מוצג כפי שקיים היום" : "סבבים קודמים", pill: tab === 0 ? pill : "", actions, tabs, tab, compact: true });
  const stale = overviewError ? `<div class="c12">${UI.note("warn", esc(overviewError))}</div>` : "";
  let body;
  if (tab === classTab) body = deployClassView();
  else if (tab === histTab) body = UI.card({ title: "היסטוריית סבבים", small: "סבבי החדר שהסתיימו", cls: "c12", body: roomHistoryHtml()
    + (classroomsOn() ? UI.note("info", `סבבים קודמים בכיתות — <b title="אין endpoint לסבבי כיתה סגורים">דורש API</b>. היום רק ${isAdmin() ? UI.link("ביומן", "selectPageById('logs')") : "ביומן (מנהל)"}.`) : "") });
  else if (ROOM == null) body = `<div class="c12">${UI.note("warn", `החדר לא נקרא${DEPLOY.roomErr ? ": " + esc(DEPLOY.roomErr) : ""} — הגריד והסבב לא ידועים.`)}</div>`;
  else if (r) {
    /* מה שאינו במוקאפ ונשען על נתון: הגל, היעד, הזמן והקצב — שורות שקטות בתחתית כרטיס החדר (roomFootHtml);
       מה שדורש פעולה — שאלות, אדום/כתום, גל שהסתיים — בהערות מתחת לכרטיס, רק כשיש מה לומר */
    body = (r.state === "failed" ? `<div class="c12">${UI.note("err", `נכשל: ${esc(r.failed_reason || "סיבה לא ידועה")}`)}</div>` : "")
      + `<div class="c12 kpis">${roomKpis(r, pct)}</div>` + roomGridCard(kids)
      + roomNotes(kids, st, roomWaveHint(r)) + roomDriversResults(kids) + roomWakeSkipped(kids, r);
  } else body = roomGridCard(kids) + roomNotes(kids, st);
  return `<div class="page">${header}<div class="body">${stale}${body}</div></div>`;
}


/* ‏#447 + קצב אוטומטי (11/10): באיזה קצב הגל יוצא, ולמה (/room.round.wave_rate). השרת
   מחליט לבד, לעולם לא מעל IMAGECTL_MAX_BITRATE (בלעדיו 900m). ‏null = לא ידוע (מחשב בנייה,
   גל בלי בחירה) — לא מוצג. ‏no_ceiling — רק בגלים שנבחרו לפני הקצב האוטומטי. */
/* #1758: climb/peak/back — הקצב נבחר לפי goodput (בייטים שנכתבו לשנייה), לא לפי האבדן */
const WAVE_RATE_WHY = { start: "קצב פתיחה — מחשב שיכפול בלי גל קודם",
  climb: "עלה — בקצב הגבוה ביותר שנוסה הגל היה הכי מהיר", peak: "נשאר — הקצב שבו הגל היה הכי מהיר",
  back: "ירד — הזרם לא רץ בקצב שלו", up: "עלה — הגל הקודם נקי",
  drawers: "נגזר מגל עם פחות מגירות — הקצב הוקטן לפי מספר המגירות",
  down: "ירד — שידורים חוזרים", held: "נשאר — הגל הקודם לא הוכיח שאפשר לעלות",
  unmeasured: "נשאר — הגל הקודם לא נמדד", ceiling: "בתקרה — לא עולים מעליה",
  disabled: "הקצב האוטומטי כבוי — קצב קבוע", no_ceiling: "לא הוגדרה תקרה",
  bad_ceiling: "התקרה אינה מספר — לא נלמד" };
function roomWaveRate(r) {
  const w = r.wave_rate;
  if (!w) return "";
  const value = w.max_bitrate ? ltr(w.max_bitrate) : "ללא תקרה";
  let why;
  if (w.basis === "learned" && w.by) {
    const m = (ROOM.machines || []).find((x) => x.mac === w.by.mac);
    const where = m ? ` · ${esc(machineName(m))}${w.by.port != null ? ` ${esc(portLabel(findMachine(m.mac), w.by.port))}` : ""}` : "";
    why = `נקבע לפי ${esc(w.by.model || "כונן")} <span class="mono">${esc(w.by.serial)}</span>${where}`;
  } else why = WAVE_RATE_WHY[w.basis] || esc(w.basis || "");
  const unknown = w.unknown_drives ? ` · ${w.unknown_drives} כוננים בלי היסטוריה` : "";
  return ` · ${w.projected ? "צפוי " : ""}קצב לגל: ${value} (${why})${unknown}${roomWaveLink(w)}`;
}
/* הכרעת נדב 15/10: הגג לפי כרטיס ההפצה של השרת (wave_rate.link). מהירות שלא נקראה — נאמר
   שלא נקראה ולמה, והתקרה הקבועה; לעולם לא "1Gb/s" מנוחש (עיקרון 5). בלי link — כלום. */
const LINK_WHY = { no_interface: "לא ידוע איזה כרטיס", absent: "הכרטיס לא נמצא", unreadable: "הקישור לא נקרא",
  unknown: "הכרטיס לא מדווח מהירות", virtual: "כרטיס וירטואלי — המהירות אינה של הקו", not_wired: "לא נבדק" };
function linkSpeedText(mbps) { return mbps >= 1000 ? `${mbps / 1000}Gb/s` : `${mbps}Mb/s`; }
function bitrateBytesText(raw) {
  const m = /^(\d+(?:\.\d+)?)([kmg]?)$/.exec(String(raw || "").toLowerCase());
  if (!m) return String(raw || "");
  const bytes = Number(m[1]) * ({ "": 1, k: 1e3, m: 1e6, g: 1e9 })[m[2]] / 8;
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)}GB/s` : `${Math.round(bytes / 1e6)}MB/s`;
}
function roomWaveLink(w) {
  const l = w.link, c = w.cloner;
  let out = "";
  if (l) {
    const card = l.applied ? ltr(linkSpeedText(l.speed_mbps)) : `<span title="${esc(l.why || "")}">${esc(LINK_WHY[l.why] || "מהירות לא נקראה")}</span>`;
    out += ` · כרטיס ההפצה: ${card}`;
  }
  /* 15/10 (ב): מחשב השיכפול האיטי קבע את התקרה — בשמו; מהירות שהונחה נאמרת כהנחה */
  if (c && w.cap_by === "cloner") {
    const m = (ROOM.machines || []).find((x) => x.mac === c.mac);
    const who = m ? machineName(m) : c.mac;
    out += ` · תקרה לפי ${esc(who)}: ${ltr(linkSpeedText(c.speed_mbps))}${c.source === "assumed" ? " (הנחה — המהירות לא ידועה)" : ""}`;
  }
  return l || (c && w.cap_by === "cloner") ? `${out} · תקרת שידור: ${ltr(bitrateBytesText(w.ceiling))}` : "";
}
/* שלושת אריחי המוקאפ (P.deploy ‏.kpis): התקדמות כוללת (ממוצע המגירות שהצטרפו — wavePct), קצב זרם ובלוקים
   ששודרו שוב — מה ש-udp-sender של השרת מדפיס (/room.round, #989). ‏null = "—" + "לא נמדד" עם הסיבה, לא 0
   (עיקרון 5); 0 הוא מדידה. הזרם שעצר — כתום על אריח הקצב. */
function roomKpis(r, pct) {
  const stalled = !!ROOM.stream_stalled;
  const tile = (cls, label, value, why, extra = "") => UI.kpi({ cls, label, value: value == null ? "—" : value,
    sub: [value == null ? `<b title="${esc(why)}">לא נמדד</b>` : "", extra].filter(Boolean).join(" · ") });
  return tile("info", "התקדמות כוללת", pct == null ? null : `${pct}%`, r.wave_state === "open" ? "הגל טרם התחיל" : "עוד אין דיווח כתיבה מהמגירות שהצטרפו")
    + tile(stalled ? "warn" : "info", "קצב זרם", r.throughput_bps != null ? `${fmtBytes(r.throughput_bps)}/s` : null,
      "‏/room.round.throughput_bps הוא null: אין עדיין שתי שורות מ-udp-sender של השרת, או שהמשדר אינו השרת", stalled ? "<b>הזרם עצר</b>" : "")
    + tile("info", "בלוקים שודרו שוב", r.loss_blocks != null ? String(r.loss_blocks) : null,
      "‏/room.round.loss_blocks הוא null: הלוג של udp-sender לא נקרא, או שהמשדר אינו השרת");
}
/* שורת ההסבר של הגל — אותה שורה שבגואי (screens_room.c, renderLive): גל פתוח = כמה מוכנות וכמה חסרות;
   גל רץ = עבר / קצב / נותרו, **רק מה שנמדד** (קצב null ≠ 0, עיקרון 5). */
/* ‏#1843: "צפוי להסתיים ב-HH:MM" — בשעון המקומי של הדפדפן (חותמות השרת ב-UTC). */
function fmtEndsAt(ms) { return Number.isFinite(ms) ? fmtClock(new Date(ms).toISOString()) : null; }   // #1933: שעון השרת
/* ‏#1843: ההערכה מההיסטוריה (‏/room.round.forecast, ‏/overview.pulls[].forecast, ‏/tasks[].forecast). ‏seconds null =
   "אין עדיין הערכה" — לעולם לא מספר מומצא (עיקרון 5ב). ‏liveEta (‏eta_s שנמדד עכשיו) גובר: ההערכה מתעדכנת לפי הקצב בפועל. */
function forecastText(f, liveEta) {
  if (liveEta != null && liveEta >= 0) return `צפוי להסתיים ב-${ltr(fmtEndsAt(Date.now() + liveEta * 1000))}`;
  if (!f) return "";
  if (f.seconds == null) return "אין עדיין הערכת סיום (אין היסטוריה)";
  const at = f.ends_at ? fmtEndsAt(Date.parse(f.ends_at)) : null;
  return at ? `צפוי להסתיים ב-${ltr(at)} (לפי היסטוריה)` : `לפי היסטוריה: כ-${fmtSpan(f.seconds)}`;
}
function hmsClock(s) { return s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
/* ‏#989 ס׳ 3: איפה הסבב עומד על פני גלים — רק שדות ש-/room כבר מחזיר (wave_number/written/target/remaining/ready).
   K = ‏ceil(נשארו / מגירות מוכנות עכשיו); אין מוכנות = "ממתין לדיסקים חדשים", לא הערכה. אותה לוגיקה בגואי (screens_room.c). */
function roomWaveProgress(r) {
  const left = r.remaining_drives, ready = r.ready_drives || 0;
  if (left == null || left <= 0) return "";
  const est = ready > 0 ? `משוער: עוד ${Math.ceil(left / ready)} גלים` : "ממתין לדיסקים חדשים";
  return `גל ${r.wave_number || 1} · נכתבו ${r.written_drives || 0} מתוך ${r.target_drives || 0} · נשארו ${left} · ${est}`;
}
/* גל קודם נגמר ויש עוד מה לכתוב — דורש פעולה, ולכן הערה ולא שורה בתחתית הכרטיס */
function roomWaveEnded(r) {
  const left = r.remaining_drives;
  if (left == null || left <= 0 || r.wave_state !== "open" || (r.wave_number || 1) <= 1) return "";
  return UI.note("warn", `הגל הסתיים — החליפו דיסקים והעירו את מחשבי השיכפול כדי להמשיך` + (roomOperator() ? ` <button class="btn" onclick="wakeRoom()">הער את מחשבי השיכפול</button>` : ""));
}
function roomWaveHint(r) {
  if (!r || r.state === "failed") return "";
  return roomWaveEnded(r) + roomWaveHint1(r) + roomPrecheckHtml(r) + roomMissingHtml(r);
}
/* 18/10 (מוקאפ P.deploy): כרטיס "חדר המשכפלים" במוקאפ הוא רק הגריד. מה שאינו בו ואינו דורש פעולה — הגל
   והיעד, הזמן והקצב, הקצב שנבחר לגל — שורות קטנות ומושתקות בתחתית הכרטיס, לא באנרים כחולים ברוחב הדף. */
function roomFootHtml(r) {
  if (!r || r.state === "failed") return "";
  const lines = [roomWaveProgress(r), roomWaveRunning(r), roomWaveRate(r).replace(/^ · /, "")].filter(Boolean);
  return lines.length ? `<div class="room-foot">${lines.map((x) => `<p>${x}</p>`).join("")}</div>` : "";
}
/* ‏#432: לפני "התחל" — מגירה שהגל יכתוב שהתחברה מתחת למהירות הפורט, או שה-CRC שלה עלה מאז
   הסבב הקודם שלה (/room.round.precheck, טקסט מהשרת — אותו טקסט בגואי). כתום: אזהרה, לא חסימה. */
function roomPrecheckHtml(r) {
  const w = r.wave_state === "open" && Array.isArray(r.precheck) ? r.precheck : [];
  if (!w.length) return "";
  return UI.note("warn", `<b>לפני ההתחלה — ${w.length === 1 ? "מגירה אחת" : `${w.length} מגירות`} בגל עם קישור חשוד</b>${impactList(w.map((x) => ["w", esc(x.text_he)]))}הגל ייכתב גם כך — אזהרה, לא חסימה. התחלה עם האזהרה נרשמת ביומן.`);
}
/* ‏#65: גל שמחכה ליותר מגירות ממה שהצטרפו — מי חסר ולמה (/room.round.missing), ושלוש הפעולות
   של האפיון: כבה אותו (להחליף בו מגירות) / התחל בכל זאת / בטל. הגל אינו יוצא מקוצר מעצמו. */
function roomMissingHtml(r) {
  const miss = r.wave_state === "open" && Array.isArray(r.missing) ? r.missing : [];
  if (!miss.length) return "";
  const op = roomOperator();
  const rows = impactList(miss.map((x) => ["w", esc(x.text_he) + (x.pxe && x.pxe.state !== "hello" ? ` — ${esc(x.pxe.he)}` : "") + (op && x.can_poweroff
    ? ` <button type="button" class="btn sm" onclick="poweroffMissing('${esc(x.mac)}')">כבה אותו</button>` : "")]));
  const acts = op ? `<div class="acts"><button type="button" class="btn primary" onclick="startWave()">התחל בכל זאת (${r.ready_drives || 0} מוכנים)</button><button type="button" class="btn danger" onclick="stopRoom()">בטל את הסבב</button></div>` : "";
  return UI.note("warn", `<b>חסרים בגל: ${miss.length === 1 ? "מחשב אחד" : `${miss.length} מחשבים`}</b> — ${r.ready_drives || 0} מגירות מוכנות, והגל לא יוצא מקוצר מעצמו.${rows}${acts}`);
}
function poweroffMissing(mac) {
  const r = roomRound(), x = r && (r.missing || []).find((m) => m.mac === mac);
  if (!roomOperator() || !x) return;
  const name = x.name || x.mac;
  if (x.local_copy) return localCopyPowerSheet(name, "כיבוי", async () => {   /* #1367 (Nadav 14/10) */
    await post(`/room/missing/${encodeURIComponent(mac)}/poweroff`, { confirm_name: name, confirm_local_copy: true });
    toast(`בקשת כיבוי נשלחה ל-${name}`); await loadDeploy(); });
  sheet({ title: `לכבות את ${name}?`, sub: "כדי להחליף בו מגירות",
    note: impactList([["b", "המחשב נכבה דרך הסוכן ויוצא מהגל הפתוח"], ["w", "אחרי ההחלפה מדליקים אותו (או WoL) — הוא מצטרף לגל לבד"]]),
    danger: true, submitLabel: "כיבוי",
    onSubmit: async () => { await post(`/room/missing/${encodeURIComponent(mac)}/poweroff`, { confirm_name: name }); toast(`בקשת כיבוי נשלחה ל-${name}`); await loadDeploy(); } });
}
/* #1367 (Nadav 14/10): the hello says a local clone is writing on this machine. A power-off or
   reboot now leaves the target disk broken; the request goes with confirm_local_copy, and only
   then does the agent act in the middle of the copy (without it the agent drops it, logged). */
function localCopyPowerSheet(name, verb, onSubmit) {
  const off = verb === "כיבוי";
  sheet({ title: off ? `לכבות את ${name}?` : `לאתחל את ${name}?`, sub: "המחשב באמצע שיכפול מקומי",
    note: impactList([["w", `המחשב באמצע שיכפול מקומי — ${off ? "כיבוי" : "אתחול"} עכשיו ישאיר את דיסק היעד שבור ולא שמיש. ${off ? "לכבות" : "לאתחל"} בכל זאת?`],
      ["b", "הסוכן מסמן את היעדים כנכשלו ומדווח ליומן לפני ש" + (off ? "הוא נכבה" : "הוא מאותחל")]]),
    danger: true, submitLabel: off ? "כבה בכל זאת" : "אתחל בכל זאת", onSubmit });
}
function roomWaveHint1(r) {
  if (r.wave_state === "open") {
    const ready = r.ready_drives || 0, need = Math.max(0, (r.remaining_drives || 0) - ready);
    const fc = forecastText(r.forecast, null);   // #1843
    return UI.note("info", `הגל ממתין: ${ready} מגירות מוכנות, צריך עוד ${need} — או "התחל גל". החלפתם מגירות? הדליקו את המכונות והן יצטרפו.${fc ? ` · ${fc}` : ""}`);
  }
  return "";
}
function roomWaveRunning(r) {
  if (r.wave_state !== "running") return "";
  const parts = [];
  if (r.elapsed_s != null) parts.push(`עבר ${hmsClock(r.elapsed_s)}`);
  if (r.rate_bps != null && r.rate_bps > 0) parts.push(ltr(`${fmtBytes(r.rate_bps)}/שנ'`));
  if (r.eta_s != null && r.rate_bps != null && r.rate_bps > 0) parts.push(`נותרו ~${hmsClock(r.eta_s)}`);
  else if (r.forecast && r.forecast.seconds != null) parts.push(forecastText(r.forecast, null));   // #1843: עד שהקצב נמדד
  else if (parts.length) parts.push("סיום לא נמדד");
  return `הגל משדר — ${parts.length ? parts.join(" · ") : "זמן וקצב טרם נמדדו"}. מכונה שסיימה — מכבים, מחליפים מגירות, מדליקים.`;
}
/* ‏#1954: דיסק שנבחר כשהמשכפל היה כבוי, ובחריץ שלו נמצא דיסק אחר (או אף דיסק) כשהוא עלה — השרת הוציא
   אותו מהסבב (round.target_slots[].wake_skipped) ולא כתב אליו. נאמר בגלוי, לא נעלם מהכרטיס בשקט. */
function roomWakeSkipped(kids, r) {
  const out = [];
  for (const t of r && Array.isArray(r.target_slots) ? r.target_slots : []) {
    const m = kids.find((k) => k.mac === t.mac) || { mac: t.mac };
    for (const w of Array.isArray(t.wake_skipped) ? t.wake_skipped : []) {
      const found = w.found ? `דיסק אחר (${ltr(w.found)})` : "אין דיסק";
      out.push(UI.note("warn", `<b>${esc(machineName(m))} · ${esc(diskLabel(m, { port: w.port }))}</b> — נבחר כשהמשכפל היה כבוי (${ltr(w.serial)}); כשעלה, בחריץ ${found}. <b>המגירה דולגה ולא נכתבה.</b>`));
    }
  }
  return out.length ? `<div class="c12 rnotes">${out.join("")}</div>` : "";
}
/* ‏#1269: מה קרה לדרייברים של כל מגירה (drawer_list[].drivers). ‏#1331 (מוקאפ 17/10, גרסה 2): השורה
   של כל דיסק יושבת עכשיו מתחת לפס שלו (roundDskHtml); כאן — סיכום אחד, ומתחתיו הערה לכל דיסק שדורש
   יד: אדום = דרייבר האחסון לא הוגדר (עלול לא לעלות), כתום = לא הונחו (#856: השחזור הושלם). */
function roomDriversResults(kids) {
  const r = roomRound(), out = [];
  let staged = 0, failed = 0, noModel = 0;
  for (const m of kids) {
    for (const s of r ? roundSlots(m, r) : []) {
      const o = s.live && s.disk.drivers;
      if (!o || !o.state) continue;
      const where = `<b>${esc(machineName(m))} · ${esc(slotLabel(m, s.n, s.disk))}</b>`;
      if (o.state === "staged") staged++;
      else if (o.state === "failed") failed++;
      else if (o.reason === "no_model") noModel++;
      if (driversTone(o) === "bad") out.push(UI.note("err", `<span>${where} — ${esc(bootStorageText(o.boot))}. <b>${BOOT_RISK_HE}.</b></span>`));
      else if (o.state === "failed") out.push(UI.note("warn", `<span>${where} — דרייברים לא הונחו — ${esc(o.error || "סיבה לא ידועה")}. המחשב יעלה בלי הדרייברים של הדגם; השחזור עצמו הושלם.</span>`));
    }
  }
  const counts = [staged ? `${staged} ${staged === 1 ? "מגירה הונחה" : "מגירות הונחו"}` : "", failed ? `${failed} ${failed === 1 ? "לא הונחה" : "לא הונחו"}` : "",
    noModel ? `${noModel} בלי דגם` : ""].filter(Boolean).join(", ");
  if (!counts && !out.length) return "";
  const over = r && r.wave_state && !["open", "running"].includes(r.wave_state)
    ? `גל ${r.wave_number} הסתיים — נכתבו ${r.written_drives || 0} מתוך ${r.target_drives || 0}. ` : "";
  return `<div class="c12 rnotes">${counts ? UI.note("info", `${over}דרייברים: ${counts}.`) : ""}${out.join("")}</div>`;
}
/* #1376 (נדב 17/10, גרסה ב׳ — אותה צורה כמו מסך השיכפול בגואי ודף "מחשבי שיכפול"): כרטיס לכל מחשב שבסבב,
   שורה לכל מגירה שנבחרה — "דיסק N", פס, מצב ואחוז. רק מה ש-round.target_slots (‏{mac, ports}) מנה;
   בלי בחירה (סבב ישן, null) — כל המגירות של המחשב. סיבת כשל נאמרת על המגירה, לא רק ב-tooltip. */
function roundSlots(m, r) {
  const t = r && Array.isArray(r.target_slots) ? r.target_slots.find((x) => x.mac === m.mac) : null;
  const ports = t && Array.isArray(t.ports) ? new Set(t.ports) : null;
  const slots = machineSlots(m).slots;
  return ports ? slots.filter((s) => s.disk && ports.has(s.disk.port)) : slots;
}
/* שורת דיסק בכרטיס המשכפל — המוקאפ (P.deploy, ‏.dsk): "דיסק N" · פס · אחוז. הכול מ-drawer_list של /room:
   אחוז = bytes_written/bytes_total (‏done = 100); אין דיווח = "—", לא 0% (עיקרון 5); נכשל = "נכשל" בפס אדום
   מלא; אדום שלא נכתב בסבב (SMART, #1732) = "דולג". המצב המילולי והסיבה הגולמית — בריחוף (diskTitle).
   ‏#1386: "בטל…" על שורה שכותבת, למפעיל סבבים — מופיע בריחוף/פוקוס מעל האחוז (בלי ריחוף — תמיד). ‏#1331: שורת
   הדרייברים מתחת לשורה, רק כשהדיסק דיווח. */
function roundDskHtml(m, s) {
  const d = s.disk, label = s.n != null ? slotLabel(m, s.n, d) : rowLabel(m, s);
  const live = !!(d && s.live && d.state), red = !!d && slotClass(s) === "err";
  const gone = live && d.state === "cancelled", going = live && d.state === "writing" && !!d.cancel;
  let pct = 0, bar = "", text, rc = "";
  if (!d) { text = "ריק"; rc = " vacant"; }
  else if (!live && s.live && d.cancelled_round) { text = "בוטל"; rc = " cancelled"; }   // #1936: בוטל בגל קודם — סגור בסבב, לא נכתב
  else if (!live && s.live && d.fresh === false && !d.skipped_red) { text = "נכתב"; bar = "ok"; pct = 100; }   // #1790: נכתב בגל קודם בסבב — אינו יעד בגל הזה
  else if (!live) { text = red ? "דולג" : "—"; bar = red ? "err" : ""; rc = red ? " bad" : ""; }
  else if (gone) { text = "בוטל"; rc = " cancelled"; }
  else if (going) { text = "מבטל…"; bar = "warn"; rc = " cancelling"; }
  else if (d.state === "failed") { text = "נכשל"; bar = "err"; pct = 100; rc = " bad"; }
  else {
    pct = d.bytes_total > 0 ? Math.round(100 * (d.bytes_written || 0) / d.bytes_total) : d.state === "done" ? 100 : 0;
    bar = d.state === "done" ? "ok" : slotClass(s) === "warn" ? "warn" : ""; text = `${pct}%`;
  }
  const meter = live && !gone && !going && d.state !== "failed" ? ` role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"` : "";
  const act = live && d.state === "writing" && !going && d.dev && roomOperator()
    ? `<button type="button" class="btn sm danger dx" onclick="cancelDrawer('${encodeId(m.mac)}',${Number(s.n)})" aria-label="בטל את הכתיבה ל${esc(label)}">בטל…</button>` : "";
  const drv = live && d.drivers && d.drivers.state ? `<span class="dline ${driversTone(d.drivers)}">${esc(driversOutcomeText(d.drivers))}</span>` : "";
  const rb = live && d.state !== "failed" ? readbackLine(d) : "";   // #1805
  const tl = live ? stagesLine(d) : "";   // #1883
  const warn = live && d.state === "done" && d.error ? `<span class="dline warn">${esc(d.error)}</span>` : "";   // #2042: done with a warning (an orange disk's "SMART: …"), as the cloner screen's "הושלם — …"
  return `<div class="dsk${rc}${bayEmphasis(s)}" data-slot="${s.n ?? ""}"${d ? ` title="${esc(diskTitle(m, s, d))}"` : ""}><span>${esc(label)}</span><div class="bar ${bar}"${meter}><i style="--w:${pct}%"></i></div><b>${esc(text)}</b>${act}${warn}${drv}${rb}${tl}</div>`;
}
/* ‏#1883: כמה זמן ארך כל שלב במגירה (‏drawer_list[].stages, ‏[{stage, seconds}]) — שורה מתחת לפס, אחרי
   שהמגירה הסתיימה. ‏null = לא נמדד: אין שורה (לא "0"). */
const STAGE_HE = { gpt: "טבלה", expand: "הרחבה", readback: "אימות", grow: "הגדלה" };
function stagesLine(d) {
  if (!Array.isArray(d.stages) || !d.stages.length) return "";
  const parts = d.stages.map((s) => `${/^p\d+$/.test(s.stage) ? "מחיצה " + s.stage.slice(1) : STAGE_HE[s.stage] || s.stage} ${hmsClock(Math.round(s.seconds))}`);
  return `<span class="dline" title="זמן כל שלב, לפי שעון המחשב">${esc("שלבים: " + parts.join(" · "))}</span>`;
}
/* כרטיס משכפל — המוקאפ: שם + תגית ("מחובר" / "לא מחובר" / "דיסק אדום"), שורת משנה (IP; לא מחובר — "לא הצטרף
   לסבב"; אדום — איזה דיסק ולמה, ומה כן נכתב), ושורה לכל דיסק שהסבב כותב אליו (round.target_slots). אדום = רקע
   אדום, לא מחובר = מקווקו. WoL למשכפל כבוי (#984) — בריחוף/פוקוס. ה-MAC בריחוף על הכרטיס. */
function roomMachHtml(m, r) {
  const st = roomCardState(m), rm = roomMachine(m.mac), slots = roundSlots(m, r), net = netFor(m.mac);
  const failed = slots.filter((s) => s.disk && slotClass(s) === "err");
  const done = slots.filter((s) => s.live && s.disk.state === "done");
  const gone = slots.filter((s) => s.live && s.live.state === "cancelled");   // #1386
  const bootRisk = slots.filter((s) => s.live && s.disk.drivers && driversTone(s.disk.drivers) === "bad");   // ‏#1331: הדיסק עלול לא לעלות
  const red = failed.length > 0 || bootRisk.length > 0;
  const name = (s) => slotLabel(m, s.n, s.disk);
  const why = (s) => s.disk.state === "failed" ? drawerErrorText(s.disk.error) : s.fail ? failureCauseText(s.fail) : "SMART נכשל";
  let sub;
  if (failed.length) sub = esc(failed.map((s) => `${name(s)} אדום — ${why(s)}`).join("; ")) + (done.length ? `; ${esc(done.map(name).join(", "))} ${done.length === 1 ? "משוחזר" : "משוחזרים"}` : "");
  else if (st.held) sub = esc(`${HELD_HE} — ${heldLine(m, rm.held)}`);   // #2031
  else if (st.off) sub = "לא מחובר — לא הצטרף לסבב";
  else sub = (net && net.ip ? `<bdi dir="ltr">${esc(net.ip)}</bdi>` : "כתובת לא ידועה") + (rm && rm.awake && !rm.joined && r.wave_state === "running" ? " · לא הצטרף לגל" : "");
  const pill = red ? UI.pill("err", "דיסק אדום") : UI.pill(st.cls, st.text);
  const wol = st.held ? heldButton(m) : st.off && roomOperator() ? `<button class="btn sm wx" onclick="wakeMachine('${encodeId(m.mac)}')" title="WoL למכונה הזו בלבד (#984)">WoL</button>` : "";
  const notes = bootRisk.map((s) => `<div class="mach-note err">${esc(name(s))} — ${BOOT_RISK_HE}</div>`).join("")   // ‏#1331
    + gone.map((s) => `<div class="mach-note">${esc(rowLabel(m, s))} — <bdi>${esc((s.live.cancel && s.live.cancel.text_he) || "בוטל")}</bdi> · ${esc(s.live.error || "טבלת המחיצות נמחקה, הדיסק יוצג ריק")}</div>`).join("");
  return `<div class="mach${red ? " err" : st.off ? " off" : ""}" data-mac="${esc(m.mac)}" title="${esc(m.mac)}"><div class="mt"><span>${esc(machineName(m) || m.mac)}</span>${pill}${wol}</div><div class="ms">${sub}</div><div class="dsks">${slots.map((s) => roundDskHtml(m, s)).join("")}</div>${notes}</div>`;
}
/* #1386 (נדב 17/10): "בטל דיסק זה" — חלון ה-danger הרגיל של הקונסולה (sheet + impactList), כמו בשאר הדפים.
   היציאה היא "המשך לכתוב" ולא "ביטול" — בחלון שכל עניינו ביטול, "ביטול" ליד "בטל" הוא תאונה. האישור הוא הכפתור האדום בלבד (#1270). */
function cancelDrawer(macEnc, n) {
  if (!roomOperator()) return;
  const mac = decodeURIComponent(macEnc), m = findMachine(mac) || { mac };
  const s = machineSlots(m).slots.find((x) => x.n === n && x.disk && x.disk.dev);
  if (!s) { toast("המגירה לא נמצאה בסבב — רעננו את המסך", 6000); return; }
  const d = s.disk, dev = d.dev, label = slotLabel(m, s.n, d);
  const pct = d.bytes_total > 0 ? Math.round(100 * (d.bytes_written || 0) / d.bytes_total) : null;
  const sub = [machineName(m) || mac, `מגירה ${s.n}`, d.model || "", d.size_bytes ? fmtBytes(d.size_bytes) : "",
    pct != null ? `עכשיו ב-${pct}%` : ""].filter(Boolean).join(" · ");
  sheet({ title: `לבטל את הכתיבה ל${label}?`, sub, danger: true,
    note: impactList([["b", "<b>הכתיבה לדיסק הזה נעצרת מיד.</b> מה שכבר נכתב — נזרק."],
      ["b", "<b>טבלת המחיצות שלו נמחקת</b> והוא יוצג ריק, בלי מערכת. הפעולה אינה הפיכה."],
      ["ok", "<b>שאר המגירות ממשיכות</b> באותו זרם, בלי עצירה."],
      ["ok", "בסבב הבא הדיסק ייבחר ככתיבה רגילה (ירוק). לא נחשב כשל."]]),
    hint: `יירשם ביומן: בוטל על ידי ${ME ? ME.username : ""}`,
    cancelLabel: "המשך לכתוב", submitLabel: `בטל את ${label}`,
    onSubmit: async () => { await post("/room/drawer/cancel", { mac, dev }); toast(`${label} — מבטל; שאר הדיסקים ממשיכים`); await loadDeploy(); } });
}
/* ‏#1331: מגירה כלשהי בסבב דיווחה מה קרה לדרייברים שלה — כמו dv_any בגואי הנייטיבי. */
function roomAnyDrivers(kids, r) {
  return kids.some((m) => roundSlots(m, r).some((s) => s.live && s.disk.drivers && s.disk.drivers.state));
}
/* מוקאפ console-full.html (P.deploy), אחד-לאחד: כרטיס "חדר המשכפלים" — בסבב, כרטיס לכל משכפל שבסבב;
   בלי סבב, מצב ריק עם "התחל סבב" (הגריד המלא של החדר מוצג בחלון פתיחת הסבב). "כיבוי כל המשכפלים…"
   בפעולות הכרטיס — למנהל בלבד (‏poweroffRoom), ובזמן סבב פעיל השרת מסרב והחלון אומר זאת. */
function roomGridCard(kids) {
  const r = roomRound(), admin = isAdmin(), op = roomOperator();
  const acts = admin ? `<button class="btn sm danger" onclick="poweroffRoom('${encodeId("grp_CLONERS")}')">כיבוי כל המשכפלים…</button>` : "";
  const legend = r && roomAnyDrivers(kids, r) ? `<div class="drv-legend">מתחת לכל דיסק — מה קרה לדרייברים שלו: אפור = הונחו או אין חבילה · כתום = לא הונחו (המחשב יעלה בלעדיהם) · אדום = דרייבר האחסון לא הוגדר (הדיסק עלול לא לעלות)</div>` : "";
  const body = !kids.length
    ? UI.empty("אין מחשבי שיכפול רשומים — הוסיפו אותם במחשבים (MAC + שם) לפני שפותחים סבב.", admin ? `<button class="btn primary" onclick="openClass('grp_CLONERS')">למחשבי השיכפול</button>` : "")
    : r ? `${legend}<div class="rgrid round">${kids.map((m) => roomMachHtml(m, r)).join("")}</div>${roomFootHtml(r)}`
    : `<div class="empty"><span class="eic">${uiIcon("t-deploy")}</span><b class="et">אין סבב פעיל</b><p>בחרו אימג' ומחשבי יעד כדי להתחיל. מחשבים כבויים יידלקו דרך WoL.</p>${op ? `<button class="btn primary" onclick="openRoomRound()">התחל סבב</button>` : ""}</div>`;
  return UI.card({ title: "חדר המשכפלים", small: r ? "כשל במחשב אחד לא עוצר את השאר" : "", acts, cls: "c12", body });
}
/* השאלה הפתוחה למפעיל — בדף, לא במודאל. הצבע = מה קורה בלי תשובה (הכרעת נדב 16/09):
   כתום ממשיך לכתוב, אדום מדלג. הפעולות: מוניטור (לענות ליד המסך), "נקה" על רשומת אדום
   (אחרי החלפת דיסק/כבל); תשובה מהקונסולה — roomAskNote. */
function roomNotes(kids, st, hint = "") {
  const admin = isAdmin(), out = hint ? [hint] : [], asked = new Set([...st.prompts, ...kids.filter((m) => (roomMachine(m.mac) || {}).ask)].map((m) => m.mac));
  const clears = (items) => admin ? items.filter((x) => x.s.fail).map((x) => UI.link(`נקה כשל קודם — ${machineName(x.m)} · ${slotLabel(x.m, x.s.n, x.s.disk)}`, `clearDiskFailure(${Number(x.s.fail.id)})`)) : [];
  const askOf = (m) => (roomMachine(m.mac) || {}).ask || null;
  for (const m of kids) { const a = askOf(m); if (a) out.push(roomAskNote(m, a)); }   // ‏#989 ס׳ 2: שאלה שאפשר לענות עליה מכאן
  for (const m of st.prompts.filter((x) => !askOf(x))) {
    const red = st.red.filter((x) => x.m.mac === m.mac), warn = st.warn.filter((x) => x.m.mac === m.mac);
    const fate = red.length ? `<b>אדום — בלי תשובה הסוכן מדלג</b> על ${esc(whereList(red))}` : warn.length ? `<b>כתום — בלי תשובה הסוכן ממשיך לכתוב</b> על ${esc(whereList(warn))}` : "<b>ממתין לתשובה ליד המכונה</b>";
    const acts = [admin ? UI.link("לענות ליד המסך — דף המוניטור", `selectPageById('monitor')`) : "", ...clears(red),
      `תשובה מהקונסולה אינה זמינה לשאלה הזאת — עונים ליד המכונה (תפריט טקסט, או סוכן ישן שאינו מציע אותה)`].filter(Boolean).join(" · ");
    out.push(UI.note(red.length ? "err" : "warn", `<b>${esc(machineName(m))}</b> ממתין למפעיל: <span dir="${/[א-ת]/.test(promptHe(m.prompt)) ? "rtl" : "ltr"}">${esc(promptHe(m.prompt))}</span>. ${fate}. ${acts}. השאלה חייבת תשובה מאדם — שיכפול תמיד עם מישהו ליד המחשבים.`));
  }
  if (roomRound()) {
    const warn = st.warn.filter((x) => !asked.has(x.m.mac)), red = st.red.filter((x) => !asked.has(x.m.mac));
    if (warn.length) out.push(UI.note("warn", `<b>${esc(whereList(warn))}</b> — SMART אזהרה / CRC / קישור איטי: <b>הסוכן ממשיך לכתוב</b> (כתום = כותב), והדיסק ייבדק שוב באימות. דילוג מרחוק — <b title="אין endpoint לדילוג על מגירה">דורש API</b>.${clears(warn).length ? " " + clears(warn).join(" · ") : ""}`));
    /* ‏#1767: אדום הוא שלוש סיבות שונות (slotClass) — הכתיבה בסבב נכשלה, קריאה בלבד (#1742), SMART נכשל.
       הערה לכל סיבה, בסיבה שלה; רק ל-SMART נכשל "לא כותבים... מדולג" — דיסק שהכתיבה אליו נכשלה כן נכתב. */
    const redWhy = (s) => s.disk.state === "failed" ? (s.disk.error ? `הכתיבה בסבב נכשלה: ${drawerErrorText(s.disk.error)}` : "הכתיבה בסבב נכשלה")
      : s.fail && s.fail.cause === "readonly" ? failureCauseText(s.fail) : null;
    const byWhy = new Map();
    red.forEach((x) => { const k = redWhy(x.s); if (!byWhy.has(k)) byWhy.set(k, []); byWhy.get(k).push(x); });
    for (const [why, items] of byWhy) {
      const tail = why == null ? ` (#1732: מדולג בגלוי, בלי שאלה)` : "";
      out.push(UI.note("err", `<b>${esc(whereList(items))}</b> — אדום: <b>${esc(why == null ? SMART_RED_HE : why)}</b>${tail}. ${[...clears(items), "החלפת דיסק או כבל — ליד המכונה"].join(" · ")}.`));
    }
  }
  return out.length ? `<div class="c12 rnotes">${out.join("")}</div>` : "";
}
/* ‏#989 ס׳ 2: שאלת ה-SMART של משכפל, ותשובה ממנה — אותם צעדים של התפריט ליד המכונה. ‏#1732 (נדב
   18/10, מחליף את "כתוב בכל זאת" של #1580): אדום = SMART תקול (fail) — לעולם אינו יעד: הסוכן אינו
   שואל עליו והשרת דוחה כל "כתוב" עליו, ולכן אין לו כפתור כתיבה כאן; כתום = נכשל בשיכפול הקודם
   (failed_last) — "נסה שוב" כותב; warn — "כתוב בכל זאת". התשובה הראשונה גוברת: אחרי שנענתה — מי ענה
   ומה, בלי כפתורים; 409 מוצג כפי שהוא. */
const SMART_RED_HE = "SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת";
const ASK_VERDICT_HE = { warn: "SMART אזהרה", fail: "SMART תקלה", failed_last: "נכשל בשיכפול הקודם" };
const ASK_ANSWER_HE = { replace: "החלף דיסק", rescue: "כתוב בכל זאת", skip: "דלג" };
function askAnswerHe(a) { return a.answer === "rescue" && a.verdict === "failed_last" ? "נסה שוב" : ASK_ANSWER_HE[a.answer] || a.answer; }
function roomAskNote(m, a) {
  const red = a.verdict === "fail", name = esc(machineName(m));
  const slot = esc(portLabel(findMachine(m.mac) || m, a.port));   // #2001: the connection's name
  const what = a.verdict === "fail" ? `${slot} — ${esc(smartF1Text(a.reason))}`   // #1366
    : `${slot} — ${ASK_VERDICT_HE[a.verdict] || "SMART"}${a.reason ? ` (${esc(smartReasonHe(a.reason))})` : ""}`;   // #1861: "(cable)" was raw
  if (a.answer) return UI.note("info", `<b>${name}</b> · ${what}: נענתה ע&quot;י ${esc(a.answered_by || "גורם לא ידוע")}: ${esc(askAnswerHe(a))}.`);
  const fate = red ? `<b>${esc(SMART_RED_HE)}</b>` : "<b>כתום — בלי תשובה הסוכן ממשיך לכתוב</b>";
  const btn = (ans, label, cls = "") => `<button type="button" class="btn sm${cls}" onclick="answerSmart('${esc(m.mac)}','${esc(a.nonce)}','${ans}','${esc(a.verdict)}')">${label}</button>`;
  const acts = roomOperator() ? `<div class="acts">${btn("replace", "החלף דיסק (כיבוי)")}${red ? "" : btn("rescue", a.verdict === "failed_last" ? "נסה שוב" : "כתוב בכל זאת")}${btn("skip", "דלג על הדיסק")}</div>` : "";   // #1732: no write on red
  return UI.note(red ? "err" : "warn", `<b>${name}</b> שואל: ${what}. ${fate}. התשובה הראשונה גוברת — גם ליד המכונה.${acts}`);
}
async function answerSmart(mac, nonce, answer, verdict) {
  if (!roomOperator()) return;
  if (verdict === "fail" && answer === "rescue") return toast(SMART_RED_HE, 6000);   // #1732: the server refuses it too
  const send = async () => {
    try { await post("/room/answer", { mac, nonce, answer }); toast("התשובה נשלחה — המכונה תקבל אותה בתוך כמה שניות"); }
    catch (e) { toast(e.message, 6000); }       // ‏409: כבר נענה ע"י … / השאלה התחלפה — מוצג כפי שהשרת אמר
    await loadDeploy();
  };
  const m = findMachine(mac), name = machineName(m || { mac });
  if (answer !== "replace") return send();
  sheet({ title: "להחליף את הדיסק?", sub: `${name} תיכבה כדי שיחליפו את הדיסק`, danger: true, submitLabel: "כיבוי והחלפה",
    note: impactList([["b", `${esc(name)} יכבה — כל המכונה, לא רק הדיסק`], ["w", "היא כבויה לפני כתיבה: אף מגירה שלה לא נכתבת בגל הזה"], ["w", "אדם צריך להחליף את הדיסק ליד המכונה ולהדליק אותה"], ["w", "שאר המכונות ממשיכות"]]),
    onSubmit: send });
}
/* ‏#1275: בחירת הדיסקים לפתיחת סבב — אותו גריד שנדב אישר במוקאפ מחשב הבנייה (native-full.html) ובגואי
   (screens_room.c): כרטיס לכל משכפל, מלבן לכל דיסק, לחיצה מחליפה בחירה (טבעת + ‏✓), "N נבחרו",
   "בחר / נקה הכל". ניתן לבחור רק דיסק קיים (עם port ו-serial); חריץ ריק — לא. ‏#1954 (נדב 22/10): דיסק של
   משכפל כבוי ניתן לבחירה לפי הדיווח האחרון שלו — "יידלק ב-WoL"; השרת קושר אותו ל-serial, ודיסק אחר בחריץ
   כשהמשכפל עולה מדולג בגלוי (`wake_skipped`).
   דיסק אדום אינו ניתן לבחירה; הצבע מתאר התאמה, והמסגרת הכחולה מתארת בחירה.
   בלי ברירת מחדל — אין סבב בלי דיסק אחד לפחות. המפתח "mac@port" הוא גם מפתח הדרייברים. */
function roomPickSlots(kids) {
  const image = (IMAGES || []).find((i) => i.id === DEPLOY.form.image) || null;
  const need = image && Number(image.min_target_bytes);
  return kids.flatMap((m) => {
    const rm = roomMachine(m.mac), awake = !!(rm && rm.awake);
    return machineSlots(m).slots.map((s) => {
      const d = s.disk, key = d && d.port != null ? `${m.mac}@${d.port}` : "";
      const health = d ? slotClass({ disk: d, fail: s.fail }) : "empty";
      const tooSmall = !!(d && Number.isFinite(need) && need > 0 && Number(d.size_bytes) < need);
      const preflightRed = !!(d && d.write_preflight && ["slow", "failed"].includes(d.write_preflight.state));
      const red = health === "err" || tooSmall || preflightRed;
      // #1485: כונן שאינו ממופה (‏s.n null) אינו יעד — השרת מקבל רק פורט שבמיפוי
      return { m, s, d, awake, key, health, tooSmall, preflightRed, red, wol: !awake, ok: !!d && !!d.serial && d.port != null && s.n != null && !red };
    });
  });
}
/* יעד הדיסקים כשלא הוקלד: מה שסומן; בלי סימון — המגירות המוכנות עכשיו (כמו "ready || …" של הגואי,
   screens_room.c); בלי אלה — החריצים שהוגדרו בחדר. תמיד ≥ 1. */
function roomTargetDefault(kids) {
  const nsel = roomSelected(kids).length, ready = roomPickSlots(kids).filter((x) => x.ok).length;
  const declared = kids.reduce((n, m) => n + (Number(m.drawer_count) || 0), 0);
  if (nsel) return { n: nsel, why: "הדיסקים שסומנו" };
  if (ready) return { n: ready, why: "מגירות מוכנות" };
  return { n: Math.max(1, declared), why: declared ? "חריצים שהוגדרו בחדר" : "דיסק אחד לפחות" };
}
function roomSelected(kids) { return roomPickSlots(kids).filter((x) => x.ok && DEPLOY.form.sel[x.key]); }
function roomPick(key) {
  const x = roomPickSlots(roomMachines()).find((y) => y.ok && y.key === key);
  if (!x) return;
  if (DEPLOY.form.sel[key]) delete DEPLOY.form.sel[key]; else DEPLOY.form.sel[key] = true;
  DEPLOY.err = ""; roomRoundRender();
}
function roomPickSet(list, on) { list.forEach((x) => { if (on) DEPLOY.form.sel[x.key] = true; else delete DEPLOY.form.sel[x.key]; }); DEPLOY.err = ""; roomRoundRender(); }
function roomPickMachine(mac) {
  const list = roomPickSlots(roomMachines()).filter((x) => x.ok && x.health !== "warn" && x.m.mac === mac);
  roomPickSet(list, true);
}
function roomPickClearMachine(mac) { roomPickSet(roomPickSlots(roomMachines()).filter((x) => x.m.mac === mac), false); }
function roomPickAll() {
  roomPickSet(roomPickSlots(roomMachines()).filter((x) => x.ok && x.health !== "warn"), true);
}
function roomPickClearAll() { roomPickSet(roomPickSlots(roomMachines()), false); }
function roomPickWarning(d) {
  const why = [];
  if (d.smart === "fail") why.push(smartF1Text(d.smart_reason)); else if (d.smart === "warn") why.push("SMART אזהרה");
  else if (d.smart === "failed_last") why.push("נכשל בשיכפול הקודם — \"נסה שוב\" כותב");   // #1580: orange
  if (d.write_mark === "incomplete") why.push(WRITE_MARK_HE);   // #1798: orange -- still a target
  if (d.crc_suspect === true) why.push(`כבל או מגירה חשודים — ${d.crc} שגיאות`);
  else if ((d.crc_delta || 0) > 0) why.push(`CRC +${d.crc_delta}`); else if ((d.crc_rise || 0) > 0) why.push(`CRC +${d.crc_rise}`);
  if (linkSlow(d)) why.push(linkText(d));
  return why.join(" · ");
}
function roomPickGrid(kids) {
  const all = roomPickSlots(kids), ready = all.filter((x) => x.ok), nsel = ready.filter((x) => DEPLOY.form.sel[x.key]).length;
  const cards = kids.map((m) => {
    const mine = all.filter((x) => x.m.mac === m.mac), awake = mine.length ? mine[0].awake : !!(roomMachine(m.mac) || {}).awake;
    const pick = mine.filter((x) => x.ok), n = pick.filter((x) => DEPLOY.form.sel[x.key]).length;
    const sub = !awake ? "כבוי · יידלק ב-WoL" : "ער";
    const slots = mine.map((x) => {
      const label = rowLabel(x.m, x.s);   // #1485: the mapped number; an unmapped drive is said, not numbered
      if (!x.d) return `<div class="dk st-e" role="img" aria-label="${esc(label)} ריק">${esc(label)} · ${x.s.optical ? "כונן CD/DVD" : "ריק"}</div>`;
      const warn = !x.red && x.health === "warn", cls = x.red ? "d" : warn ? "w" : "g", on = x.ok && !!DEPLOY.form.sel[x.key];
      const selectedImage = (IMAGES || []).find((i) => i.id === DEPLOY.form.image) || {};
      const fitWhy = x.tooSmall ? imageFitReason(selectedImage, { name: machineName(x.m), port: x.s.n, label: slotLabel(x.m, x.s.n, x.s.disk), size_bytes: Number(x.d.size_bytes) }) : null;
      const preflight = x.d.write_preflight || null;
      const why = fitWhy || (x.preflightRed ? (preflight.reason || (preflight.state === "slow" ? "בדיקת הכתיבה איטית" : "בדיקת הכתיבה נכשלה")) : x.red ? (smartText(x.d) || "הדיסק אינו ניתן לבחירה") : roomPickWarning(x.d))
        || (x.wol && x.ok ? "לפי הדיווח האחרון — דיסק אחר בחריץ ידולג" : "");   // #1954
      const state = x.red ? "לא ניתן לסימון" : x.wol ? "יידלק ב-WoL" : warn ? "אזהרה" : "נכנס";   // #1954
      return `<label class="dk st-${cls}${on ? " sel" : ""}" data-key="${esc(x.key)}"><input class="dk-in" type="checkbox"${on ? " checked" : ""}${x.ok ? ` onchange="roomPick('${esc(x.key)}')"` : " disabled"}><span class="dk-t">${esc(label)}</span><span class="dk-sz">${ltr(marketedDiskSize(x.d.size_bytes))}</span><span class="dk-br">${esc(x.d.model || "דגם לא ידוע")}</span>${why ? `<span class="dk-why">${esc(why)}</span>` : ""}<span class="n-chip${cls === "g" ? "" : cls === "w" ? " w" : " d"}">${esc(x.s.n == null ? "לא ממופה" : state)}</span></label>`;
    }).join("");
    return `<section class="rn-m${!awake ? " off" : ""}" data-mac="${esc(m.mac)}"><div class="rn-mh"><div class="rn-nm"><h4>${esc(machineName(m))}</h4><span class="st"><i class="led${awake ? " ok" : ""}"></i>${sub}</span></div><span class="rn-cnt" aria-live="polite"><b>${n}</b> מתוך ${pick.length} נבחרו</span><button class="btn sm" type="button" onclick="roomPickMachine('${esc(m.mac)}')">בחר הכול</button><button class="btn sm" type="button" onclick="roomPickClearMachine('${esc(m.mac)}')">נקה</button></div><div class="rn-ds">${slots || `<div class="muted">לא דווחו מגירות</div>`}</div></section>`;
  }).join("");
  return `<h3 class="n-sec-h full">מחשבים ומגירות</h3><div class="full rn-pick"><div class="rn-ph"><span>${ready.length} מגירות ניתנות לבחירה · <b>${nsel} נבחרו</b></span><span class="rn-all"><button type="button" class="btn sm" onclick="roomPickAll()">בחר הכול</button><button type="button" class="btn sm" onclick="roomPickClearAll()">נקה הכול</button></span></div><ul class="rn-legend" aria-label="מקרא"><li><i class="rn-sw sel"></i>נבחר</li><li><i class="rn-sw g"></i>נכנס</li><li><i class="rn-sw w"></i>אזהרה — אפשר לסמן</li><li><i class="rn-sw d"></i>לא ניתן לסימון</li><li><i class="rn-sw e"></i>ריק</li></ul>` + (kids.length ? `<div class="rn-grid">${cards}</div>` : `<div class="muted">אין משכפלים בחדר</div>`) + `<p class="muted">"בחר הכול" מסמן רק מגירות ירוקות; מגירה באזהרה מסמנים בנפרד. מחשב כבוי יידלק ב-WoL כשהסבב נפתח.</p></div>`;
}
/* חלון סבב חדר חדש: אימג' ("נכנס לדיסק מ-X GB"; #953: מה שלא נכנס למגירה
   הקטנה מושבת עם הסיבה — לא מוסתר) → מקור (ספרייה / דיסק מחשב בנייה, #715) → פתח סבב
   (‏POST /room) → ואז "התחל גל". הערכים נשמרים ב-DEPLOY.form כי הדף מצטייר מחדש כל 2 ש'. */
function roomNewWindow(kids) {
  const f = DEPLOY.form, floor = ROOM.disk_floor || null, op = roomOperator();
  /* ‏folder-first כמו בגואי (widgets.c, בקשת נדב 10/10): קודם תיקייה — "שם (מספר)" — ואז האימג'ים שבה בלבד.
     אימג' שנבחר קובע את התיקייה; "-" הוא התיקייה הריקה ("ללא תיקייה"), "" = עוד לא נבחרה. */
  const images = (IMAGES || []).slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));
  const folders = [...new Set(images.map((i) => i.folder || ""))].sort((a, b) => a.localeCompare(b));
  const chosen = images.find((i) => i.id === f.image) || null;
  const folder = chosen ? (chosen.folder || "") : (f.folder != null && folders.includes(f.folder) ? f.folder : null);
  const folderOpts = `<option value="">— בחר תיקייה —</option>` + folders.map((fo) =>
    `<option value="${esc(fo || "-")}"${folder === fo ? " selected" : ""}>${esc(fo || "ללא תיקייה")} (${images.filter((i) => (i.folder || "") === fo).length})</option>`).join("");
  const opts = `<option value="">${folder == null ? "— בחר קודם תיקייה —" : "— בחר אימג' —"}</option>` + (folder == null ? [] : images.filter((i) => (i.folder || "") === folder)).map((img) => {
    const blocked = imageDeployBlock(img), reason = blocked ? null : imageFitReason(img, floor), g = gbCeil(img.min_target_bytes);
    return `<option value="${esc(img.id)}"${blocked || reason ? " disabled" : ""}${img.id === f.image ? " selected" : ""}>${esc(img.name)}${g != null ? ` · נכנס לדיסק מ-${g} GB` : " · גודל נדרש לא ידוע"}${blocked ? " — לא ניתן לפרוס: " + esc(blocked) : reason ? " — לא נכנס: " + esc(reason) : ""}</option>`;
  }).join("");
  const floorNote = floor ? `המגירה הקטנה ביותר בחדר: ${marketedDiskSize(floor.size_bytes)} (${esc(floor.port != null ? portLabel(findMachine(floor.mac), floor.port) : `דיסק ${floor.dev}`)} במחשב ${esc(floor.name)}).` : "גודל הדיסקים לא ידוע — יסורב במכונה אם לא ייכנס.";
  const builders = (MACHINES || []).filter((m) => machineRole(m) === "build" && Array.isArray(m.disks) && m.disks.length);
  const builder = builders.find((m) => m.mac === f.builder) || builders[0] || null;
  const direct = f.src === "build_disk";
  const nsel = roomSelected(kids).length;
  const dflt = roomTargetDefault(kids), target = f.target !== "" ? f.target : String(dflt.n);
  const source = `<div class="n-seg rn-src" role="group" aria-label="מקור הסבב"><button type="button" aria-pressed="${!direct}" onclick="roomFormSet('src','library')">מהשרת (הספרייה)</button><button type="button" aria-pressed="${direct}" onclick="roomFormSet('src','build_disk')"${builders.length ? "" : " disabled"}>מדיסק מחשב בנייה (הפצה ישירה)</button></div>${builders.length ? "" : `<div class="muted">אין מחשב בנייה שדיווח על דיסקים</div>`}`;
  const fields = direct && builder
    ? `<h3 class="n-sec-h full">מקור</h3><label>מחשב בנייה<select id="rn-builder" onchange="roomFormSet('builder',this.value)">${builders.map((m) => `<option value="${esc(m.mac)}"${m.mac === builder.mac ? " selected" : ""}>${esc(machineName(m))}</option>`).join("")}</select></label>
       <label>דיסק המקור<select id="rn-disk" onchange="roomFormSet('disk',this.value)">${builder.disks.map((d, i) => `<option value="${esc(d.dev)}"${(f.disk || builder.disks[0].dev) === d.dev ? " selected" : ""}>${esc(diskLabel(builder, d))} · ${marketedDiskSize(d.size_bytes)} · ${esc(d.model || "")}</option>`).join("")}</select></label>
       <div class="full muted">היעד: הדיסקים שסומנו בגריד למטה — הפצה ישירה דורשת בחירה מפורשת. סבב יחיד, לא מצטבר.</div>`
    : `<h3 class="n-sec-h full">אימג'</h3><label>תיקייה<select id="rn-folder" onchange="roomFormSet('folder',this.value)">${folderOpts}</select></label>
       <label>אימג'<select id="rn-image" onchange="roomFormSet('image',this.value)"${folder == null ? " disabled" : ""}>${opts}</select></label>
       <label>גרסה<select id="rn-version"><option>הנוכחית (ברירת מחדל)</option></select></label>
       <div class="full n-help">${chosen && gbCeil(chosen.min_target_bytes) != null ? `האימג' נכנס לדיסק מ-<b>${ltr(gbCeil(chosen.min_target_bytes) + " GB")}</b>. ` : ""}אימג' שלא נכנס למגירה הקטנה ביותר בחדר מושבת ברשימה, עם הסיבה.</div>
       <h3 class="n-sec-h full">יעד</h3>
       <div class="full rn-2"><label>כמה דיסקים בסך הכול (בכל הגלים)<input type="number" id="rn-target" min="1" value="${esc(target)}" oninput="roomFormSet('target',this.value)"><span class="n-help">ברירת המחדל = ${dflt.n} ${dflt.why}.</span></label>
       <div><span class="n-lbl">המגירה הקטנה ביותר בחדר</span><div class="rn-floor">${floor ? ltr(marketedDiskSize(floor.size_bytes)) : "לא ידוע"}</div><div class="n-help">${floorNote}</div></div></div>`;
  const err = DEPLOY.err ? UI.note("err", `הסבב לא נפתח: ${esc(DEPLOY.err)}`) : "";
  const submit = op ? "" : `<div class="full muted">פתיחת סבב — למנהל או למפעיל הפצה.</div>`;
  return `<div class="rnew">${err ? `<div class="full">${err}</div>` : ""}<div class="full">${source}</div>${fields}${roomPickGrid(kids)}${roomDriversBlock(kids, direct)}${submit}</div>`;
}
/* ‏#1485 (נדב 16/10, המוקאפ S350): הפעולה הראשית בשורת התחתית של החלון — תמיד גלויה, גם כשהגריד ארוך —
   ולצידה הסיכום ("נבחרו N דיסקים ב-M מחשבים · משוער K גלים"). ‏null = אין מה לפתוח (לא מפעיל). */
/* שורת התחתית של החלון: "פתח סבב" ממתין לדיסק מסומן (פעולה שעוד אין לה קלט — לא "דורש API"). */
function roomFooterSync(kids) {
  const st = roomSubmitState(kids), ok = document.getElementById("modalOk"), hint = document.getElementById("modalHint");
  if (ok) { ok.disabled = !st.n || !!DEPLOY.busy; if (ok.setAttribute) ok.setAttribute("aria-busy", String(!!DEPLOY.busy)); }
  if (hint) { hint.textContent = st.text; hint.className = "n-hint rn-sum" + (st.n ? "" : " need"); if (hint.setAttribute) hint.setAttribute("aria-live", "polite"); }
}
function roomSubmitState(kids) {
  const f = DEPLOY.form, nsel = roomSelected(kids).length, direct = f.src === "build_disk";
  const target = f.target !== "" ? f.target : String(roomTargetDefault(kids).n);
  const machines = new Set(roomSelected(kids).map((x) => x.m.mac)).size, waves = nsel ? Math.ceil((direct ? nsel : Number(target) || nsel) / nsel) : 0;
  return { n: nsel, text: nsel ? `נבחרו ${nsel} ${nsel === 1 ? "דיסק" : "דיסקים"} ב-${machines} ${machines === 1 ? "מחשב" : "מחשבים"} · משוער ${waves === 1 ? "גל אחד" : `${waves} גלים`}` : "יש לסמן לפחות דיסק אחד" };
}
/* ‏#1269: דרייברים לפי דגם, לכל דיסק יעד — אופציונלי, כבוי כברירת מחדל. אין "אוטומטי": החומרה
   שזוהתה היא של המשכפל, לא של המכונה שתקבל את הדיסק (interfaces.md §17) — דגם מפורש או "בלי".
   היעדים הם המגירות המחוברות (serial + port), כמו בהפצה ישירה: השרת קושר דגם רק לדיסק שנבחר כיעד,
   ולכן עם דרייברים גם סבב מהספרייה נשלח עם target_slots — ונאמר במפורש. */
function roomDriverSlots(kids) {   // ‏#1275: רק הדיסקים שנבחרו בגריד — אותה בחירה, בלי רשימה שנייה
  return roomSelected(kids).map((x) => ({ m: x.m, port: x.d.port, key: x.key }));
}
function roomDriversOn() { return !!DEPLOY.form.drv && Array.isArray(DRIVER_MODELS) && DRIVER_MODELS.length > 0; }
/* "דרייברים: X ← 2 דיסקים ב-מחשב 1 · בלי ← מחשב 2" — לפי מכונה, מקובץ לפי דגם */
function roomDriversSummary(slots) {
  const parts = [], byMachine = new Map();
  slots.forEach((s) => { if (!byMachine.has(s.m.mac)) byMachine.set(s.m.mac, []); byMachine.get(s.m.mac).push(s); });
  for (const list of byMachine.values()) {
    const groups = new Map(), name = machineName(list[0].m);
    list.forEach((s) => { const c = DEPLOY.form.drvMap[s.key]; const k = c ? c.model : ""; groups.set(k, (groups.get(k) || 0) + 1); });
    for (const [model, n] of groups) parts.push(model ? `${model} ← ${n === 1 ? "דיסק אחד" : n + " דיסקים"} ב-${name}` : `בלי ← ${name}`);
  }
  const any = slots.some((s) => DEPLOY.form.drvMap[s.key]);
  return `דרייברים: ${any ? parts.join(" · ") : "אף דיסק לא קיבל דגם"}`;
}
function roomDriversBlock(kids, direct) {
  const models = DRIVER_MODELS;
  const why = models == null ? "רשימת הדגמים לא נקראה מהשרת" : !models.length ? "אין בספרייה חבילת דרייברים עם דגם מחשב — מוסיפים חבילה בדף הדרייברים" : "";
  const on = roomDriversOn();
  const sw = `<label><input type="checkbox" id="rn-drv"${on ? " checked" : ""}${why ? " disabled" : ""} onchange="roomFormSet('drv',this.checked)"> להוסיף דרייברים</label> <span class="muted">${esc(why || (on ? "החבילות שבשרת לדגם שנבחר יונחו על הדיסק אחרי השחזור" : "כבוי — לא יונחו דרייברים"))}</span>`;
  if (!on) return `<details class="full rn-drv"><summary>דרייברים לפי דגם <small>(אופציונלי)</small></summary><div class="rn-db">${sw}</div></details>`;
  const opts = (cur) => `<option value=""${cur ? "" : " selected"}>בלי</option>` + models.map((m, i) =>
    `<option value="${i}"${cur && cur.vendor === m.vendor && cur.model === m.model ? " selected" : ""}>${esc(m.vendor + " " + m.model)}</option>`).join("");
  const slots = roomDriverSlots(kids);
  const all = `<label>דגם לכל הדיסקים<select id="rn-drv-all" onchange="roomDrvAll(this.value)"><option value="-" selected>— להחיל על כולם —</option>${opts({})}</select></label>`;
  const rows = slots.map(({ m, port, key }) => `<label>${esc(machineName(m))} · ${esc(diskLabel(m, { port }))}<select data-drv="${esc(key)}" onchange="roomDrvSet('${esc(key)}',this.value)">${opts(DEPLOY.form.drvMap[key])}</select></label>`).join("");
  const scope = "";
  return `<details class="full rn-drv" open><summary>דרייברים לפי דגם <small>(אופציונלי)</small></summary><div class="rn-db">${sw}<div class="rn-drivers">${slots.length ? all + rows : `<div class="muted">סמנו דיסקים בגריד כדי לבחור להם דגם</div>`}<div class="muted">${esc(roomDriversSummary(slots))}${esc(scope)}</div></div></div></details>`;
}
function roomDrvSet(key, value) {
  const m = value === "" ? null : (DRIVER_MODELS || [])[Number(value)];
  if (m) DEPLOY.form.drvMap[key] = { vendor: m.vendor, model: m.model }; else delete DEPLOY.form.drvMap[key];
  renderCurrent();
}
function roomDrvAll(value) {
  if (value === "-") return;
  roomDriverSlots(roomMachines()).forEach((s) => { const m = value === "" ? null : (DRIVER_MODELS || [])[Number(value)]; if (m) DEPLOY.form.drvMap[s.key] = { vendor: m.vendor, model: m.model }; else delete DEPLOY.form.drvMap[s.key]; });
  renderCurrent();
}
/* מה שנשלח: ‏null = המתג כבוי (הגוף כמו לפני #1269); אחרת היעדים לפי מכונה והדגם לכל דיסק שקיבל אחד */
function roomDriverChoice(kids) {
  if (!roomDriversOn()) return null;
  const by = new Map(), models = [];
  for (const { m, port, key } of roomDriverSlots(kids)) {
    if (!by.has(m.mac)) by.set(m.mac, []);
    by.get(m.mac).push(port);
    const c = DEPLOY.form.drvMap[key];
    if (c) models.push({ mac: m.mac, port, vendor: c.vendor, model: c.model });
  }
  return { slots: [...by].map(([mac, ports]) => ({ mac, ports })), models };
}
function roomFormSet(key, value) {
  if (key === "folder") { DEPLOY.form.image = ""; value = value === "" ? null : value === "-" ? "" : value; }   // ‏תיקייה אחרת = אימג' חדש
  DEPLOY.form[key] = value;
  if (key === "src" || key === "builder" || key === "drv" || key === "folder") roomRoundRender();
}
function roomRoundRender() {
  renderCurrent();
  const body = document.getElementById("modalBody");
  if (body && body.querySelector && body.querySelector(".rnew")) openRoomRound();
}
async function openRoomRoundSubmit() {
  if (!roomOperator() || DEPLOY.busy) return;
  const f = DEPLOY.form; DEPLOY.err = "";
  let body;
  const picked = roomSelected(roomMachines()), by = new Map();
  picked.forEach((x) => { if (!by.has(x.m.mac)) by.set(x.m.mac, []); by.get(x.m.mac).push(x.d.port); });
  const target_slots = [...by].map(([mac, ports]) => ({ mac, ports }));   // ‏#1275: כמו room_slots_json של הגואי
  if (!target_slots.length) DEPLOY.err = "יש לסמן לפחות דיסק אחד";
  if (f.src === "build_disk") {
    const builders = (MACHINES || []).filter((m) => machineRole(m) === "build" && Array.isArray(m.disks) && m.disks.length);
    const builder = builders.find((m) => m.mac === f.builder) || builders[0];
    if (!builder && !DEPLOY.err) DEPLOY.err = "אין מחשב בנייה שדיווח על דיסקים";
    body = { source: { kind: "build_disk", mac: builder ? builder.mac : "", disk: f.disk || (builder ? builder.disks[0].dev : "") }, target_slots };
  } else {
    const target = f.target !== "" ? Number(f.target) || 0 : roomTargetDefault(roomMachines()).n;
    if (!f.image && !DEPLOY.err) DEPLOY.err = "בחר אימג'";
    else if (target < picked.length && !DEPLOY.err) DEPLOY.err = `היעד (${target}) קטן ממספר הדיסקים שסומנו (${picked.length})`;
    body = { image_id: f.image, target_drives: target, target_slots };
  }
  const drv = DEPLOY.err ? null : roomDriverChoice(roomMachines());   // ‏#1269
  if (drv && !drv.models.length) DEPLOY.err = 'לא נבחר דגם לאף דיסק — בחרו דגם, או כבו את "להוסיף דרייברים"';
  else if (drv) body.driver_models = drv.models;
  if (DEPLOY.err) { roomRoundRender(); return; }
  DEPLOY.busy = true;
  try {
    await post("/room", body);
    toast("הסבב נפתח — החדר הוער ב-WoL");
    DEPLOY.form.image = ""; DEPLOY.form.folder = null; DEPLOY.form.target = ""; DEPLOY.form.drv = false; DEPLOY.form.drvMap = {}; DEPLOY.form.sel = {};
    closeModal();
    await loadDeploy();
  } catch (e) {
    DEPLOY.err = e.message;   // ‏409: "כבר יש סבב" / fit_refusal (#953) — אותה מחרוזת שהאפשרות המושבתת מציגה
  } finally { DEPLOY.busy = false; }
  if (DEPLOY.err) roomRoundRender();
  else if (current === "deploy") renderCurrent();
}
async function startWave() {
  if (!roomOperator()) return;
  const go = async () => { await post("/room/start"); toast("הגל התחיל"); await loadDeploy(); };
  const r = roomRound(), warn = (r && Array.isArray(r.precheck)) ? r.precheck : [];
  if (warn.length) {   // ‏#432: כתום שואל — חלון עם המגירות, "התחל בכל זאת"; ביטול = הגל ממשיך לחכות
    sheet({ title: "להתחיל את הגל למרות האזהרה?", sub: "קישור איטי מהפורט או CRC שעלה — בדרך כלל כבל או מגירה",
      note: impactList(warn.map((x) => ["w", esc(x.text_he)])), submitLabel: "התחל בכל זאת", onSubmit: go });
    return;
  }
  try { await go(); }
  catch (e) { toast("הגל לא התחיל: " + e.message, 6000); }   // ‏409 — אף משכפל לא הצטרף / המניפסט טרם הגיע
}
async function reopenRoom() {
  const r = roomRound();
  if (!roomOperator() || !r || r.state !== "failed") return;
  try {
    await post("/room", { image_id: r.image_id, target_drives: r.target_drives,
      target_slots: r.target_slots, expand_partition: r.expand_partition });
    toast("הסבב נפתח מחדש");
    await loadDeploy();
  } catch (e) { toast("הסבב לא נפתח מחדש: " + e.message, 6000); }
}
function stopRoom() {
  const r = roomRound();
  if (!roomOperator() || !r) return;
  const name = r.image_name || r.image_id || "";
  sheet({ title: "לעצור את סבב החדר?", sub: `הסבב על "${name}" ייעצר`,
    note: impactList([["b", "השידור נפסק והגל נסגר"], ["w", "מגירות שלא נכתבו נשארות טריות"]]),
    danger: true, submitLabel: "עצירת הסבב",
    onSubmit: async () => { await post("/room/close", { confirm_name: name }); toast("הסבב נעצר"); await loadDeploy(); } });
}
/* לשונית "כיתה": קוד ה-session הקיים כמו שהוא (סבב כיתה = v2) — שורת הסבב, התחלה/עצירה,
   התחנות (memberRow + stuckNote), החסרות מרשימת הכיתה (נקראה בכניסה לדף), משיכות unicast. */
function deployClassView() {
  if (!OVERVIEW) return UI.card({ title: "סבב כיתה", cls: "c12", body: UI.note("warn", "‏/overview לא נקרא — הסבב לא ידוע.") });
  const s = OVERVIEW.session, pulls = OVERVIEW.pulls || [];
  // הגל של חדר המשכפלים הוא session על grp_CLONERS (‏/overview.session) — הוא שייך ללשונית החדר, לא לכיתה.
  const wave = s && (s.group_id === "grp_CLONERS" || (OVERVIEW.room && s.prefix === "ROOM"));
  if (!s || wave) return UI.card({ title: "סבב כיתה", small: "v2", cls: "c12", body: UI.empty(wave ? "אין סבב כיתה — הסבב הפעיל הוא גל בחדר המשכפלים (הלשונית הראשונה)." : "אין סבב כיתה פתוח. סבב כיתה נפתח ממסך התחנה; מחשבי כיתה בקונסולה — v2.") + (pulls.length ? `<div style="margin-top:12px">${pullsHtml(pulls, false)}</div>` : "") });
  const img = sessionImage(s), pct = sessionProgressPct(s);
  const bar = pct == null ? "—" : `<div class="progress" style="width:150px"><i style="width:${pct}%"></i></div>`;
  const table = `<table class="table"><thead><tr><th>סבב</th><th>אימג׳</th><th>כיתה</th><th>מחוברים</th><th>התקדמות</th><th>סטטוס</th></tr></thead><tbody><tr><td><strong>${esc(s.group_label || s.prefix || img)}</strong></td><td>${esc(img)}</td><td>${esc(sessionGroup(s))}</td><td>${s.joined || 0} / ${sessionExpected(s)}</td><td>${bar}</td><td><span class="status ${stateClass(s.state)}"><i></i>${esc(sessionStateText(s))}${s.state === "open" && s.starts_in_seconds != null ? ` · מתחיל בעוד ${s.starts_in_seconds} ש'` : ""}</span></td></tr></tbody></table>`;
  const op = roomOperator();
  const acts = op ? `<div class="acts">${s.state === "failed" ? `<button class="btn primary" onclick="reopenRound()">פתח מחדש</button>` : s.state === "open" ? `<button class="btn primary" onclick="startRound()">התחל עכשיו</button>` : ""}<button class="btn danger" onclick="stopRound()">עצור סבב</button></div>` : "";
  const roster = s.roster ? new Set(s.roster) : null, byMac = new Map((s.members || []).map((m) => [m.mac, m]));
  let rows = (s.members || []).map((m) => memberRow(m, s, stuckNote(s.stuck, m.mac))).join("");
  const machines = s.single ? [] : (SESSION_MACHINES.group === s.group_id ? SESSION_MACHINES.list : null);
  for (const m of machines || []) if ((!roster || roster.has(m.mac)) && !byMac.has(m.mac)) rows += `<div class="member"><b>${esc((s.prefix || "") + "-" + m.suffix)}</b><div class="sub">${esc(stuckNote(s.stuck, m.mac) || "טרם הצטרפה")}</div></div>`;
  const next = (machines || []).filter((m) => roster && !roster.has(m.mac)).map((m) => m.suffix);
  const note = machines === null && !s.single ? UI.note("warn", "רשימת הכיתה לא נקראה — תחנות חסרות אינן מוצגות.") : "";
  return UI.card({ title: "סבב כיתה", small: "v2 — כפי שקיים היום", cls: "c12", body: `<div class="table-wrap">${table}</div>${acts}` })
    + UI.card({ title: "תחנות בסבב", cls: "c12", body: note + (rows ? `<div class="list">${rows}</div>` : `<div class="empty">אין תחנות מצורפות</div>`) + (next.length ? `<div class="notice">הסבב הבא: ${esc(next.join(", "))}</div>` : "") + pullsHtml(pulls, true) });
}
/* טעינת הדף: /overview (הפולינג הקיים), /room, /images, ורשומות המחשבים (שם/prompt/דיסקים
   אדומים/IP) — deploy קורא את כולם (GET בלבד; הכתיבה היא של room_operator). */
async function loadDeploy() {
  const jobs = [refreshStatus()];
  jobs.push(api("/room").then((v) => { ROOM = v && Array.isArray(v.machines) ? v : null; DEPLOY.roomErr = ROOM ? "" : "תשובה שאינה חדר"; })
    .catch((e) => { ROOM = null; DEPLOY.roomErr = e.message; }));
  jobs.push(api("/images").then((v) => { if (Array.isArray(v)) IMAGES = v; }).catch(() => {}));
  jobs.push(api("/room/history").then((v) => { ROOM_HISTORY = Array.isArray(v) ? v : null; }).catch(() => { ROOM_HISTORY = null; }));   // ‏#980 §3
  jobs.push(api("/room/driver-models").then((v) => { DRIVER_MODELS = Array.isArray(v) ? v : null; }).catch(() => { DRIVER_MODELS = null; }));   // ‏#1269
  jobs.push(Promise.allSettled([api("/machines"), api("/groups"), api("/disk-failures"), api("/net")]).then(([m, g, f, n]) => {
    if (m.status === "fulfilled" && Array.isArray(m.value)) MACHINES = m.value;
    if (g.status === "fulfilled" && Array.isArray(g.value)) GROUPS = g.value;
    if (f.status === "fulfilled" && Array.isArray(f.value)) DISK_FAILURES = f.value;
    if (n.status === "fulfilled" && Array.isArray(n.value)) NET = n.value;
  }));
  await Promise.all(jobs);
  const s = OVERVIEW && OVERVIEW.session;
  if (s && !s.single && s.group_id) await sessionClassMachines(s.group_id);
  ROOM_KEY = JSON.stringify([ROOM, (MACHINES || []).map((m) => [m.mac, m.prompt])]);
  if (current === "deploy") renderCurrent();
}

/* ---------- ספריית אימג'ים (#954 גל 2) ----------
   נבנה לפי docs/design/console-redesign/images.md — דפדפן datastore: עץ
   תיקיות, טבלת התיקייה, מגירת אימג'. מה-API הקיים בלבד: ‏/images, ‏/folders,
   ‏/overview (אחסון, "בשימוש"), ‏/tasks (קליטות), ‏/journal (היסטוריה, admin),
   ‏POST /images/{id}/scrub (אימות חוזר). ‏#972: ‏GET /images/{id} (המניפסט
   הציבורי — מחיצות, כיווץ, boot_ca) למגירה, ו-last_scrub / rounds_30d ב-/images.
   ‏IMAGES_FOLDER: ‏null = כל האימג'ים · "" = ללא תיקייה · "שם" = תיקייה. */
const imgInit = () => ({ sel: new Set(), filter: "", scrub: {}, history: {}, manifest: {}, tasksKey: "" });
let IMG = imgInit();

function imagesTabs() { return ["קבצים", "קליטות", "אחסון"]; }

/* ‏GB עשרוני כלפי מעלה (#953) — "נכנס לדיסק מ-X GB"; הבייטים ב-tooltip. */
function gbCeil(bytes) {
  const n = Number(bytes);
  return bytes == null || !Number.isFinite(n) || n <= 0 ? null : Math.ceil(n / 1e9);
}
function fitsDisk(bytes) {
  const n = Number(bytes);
  if (bytes == null || !Number.isFinite(n) || n <= 0) return "—";
  const sizes = [[128e9, "128 GB"], [256e9, "256 GB"], [500e9, "500 GB"],
    [512e9, "512 GB"], [1e12, "1 TB"], [2e12, "2 TB"], [4e12, "4 TB"]];
  const match = sizes.find(([size]) => n <= size);
  return match ? match[1] : "> 4 TB";
}
/* ‏#1300 (console-full.html fitsDisk): "256 GB ומעלה" בשורה אחת, "נדרש לפחות N GB" מתחת.
   ‏drive — שורת "כונן מינימלי" במגירה: הכונן הקטן ביותר שהאימג' נכנס אליו, בלי "ומעלה". */
function imgFits(r, drive = false) {
  const g = gbCeil(r.min_target_bytes);
  if (g == null) return `<span class="muted">${drive ? "לא ידוע" : "—"}</span>`;
  const fit = fitsDisk(r.min_target_bytes);
  const main = fit === "> 4 TB" ? `<span class="danger-text">גדול מ-${ltr("4 TB")}</span>` : `<b>${ltr(fit)}</b>${drive ? "" : " ומעלה"}`;
  return `<span class="fit" title="${esc(r.min_target_bytes)} bytes">${main}</span><span class="sub muted">נדרש לפחות ${ltr(g + " GB")}</span>`;
}
/* ‏#1896 (נדב 22/10): אימג' ישן ששתי מחיצות ה-windows שלו שוות, ולא נשמר בקליטה איזו היא C: — "לא נבדק"
   עם קישור לסימון, עד שמסמנים. הסימון נשמר במניפסט של האימג' (השרת), ואחריו "נכנס לדיסק מ-X" כרגיל. */
function sysUncheckedCell(r) {
  return `<span class="fit" title="${esc(r.min_free_unchecked || "")}">${UI.status("warn", "לא נבדק")}</span>`
    + `<span class="sub"><a role="link" tabindex="0" onclick="markSystemPartition('${encodeId(r.id)}')">סמנו איזו מחיצה היא C:</a></span>`;
}
function markSystemPartition(id) {
  const img = findImage(id);
  if (!img || !(img.system_candidates || []).length) return;
  const gb = (b) => (b == null ? "לא ידוע" : `${(b / 1e9).toFixed(1)} GB`);
  const marked = img.system_candidates.find((c) => c.system);
  sheet({ title: "איזו מחיצה היא C:?", sub: img.name,
    note: UI.note("", "באימג' הזה יש מחיצות Windows באותו גודל, ולא נשמר בקליטה איזו מהן היא מחיצת המערכת. הבחירה נשמרת באימג', ולפיה נבדק המקום הפנוי ונכתב שם המחשב."),
    fields: [{ id: "index", label: "מחיצת המערכת", type: "radio", value: marked ? String(marked.index) : "",
      options: img.system_candidates.map((c) => ({ value: String(c.index), label: `מחיצה ${c.index}`,
        hint: `${gb(c.size_bytes)} · ${gb(c.used_bytes)} בשימוש` })) }],
    submitLabel: "שמירה",
    onSubmit: async (v) => {
      if (!v.index) throw new Error("בחרו מחיצה");
      await post(`/images/${encodeId(img.id)}/system-partition`, { index: Number(v.index) });
      toast(`מחיצה ${v.index} סומנה כ-C: — נשמר באימג'`, 2600, "ok");
      await loadImages();
    } });
}
/* "בשימוש" — סבב פעיל / סבב שיכפול מ-/overview. הספירה ל-30 יום — imgRounds (#972). */
function imgUsage(id) {
  const o = OVERVIEW || {}, out = [];
  if (o.session && o.session.image_id === id) out.push(`סבב ${sessionStateText(o.session)} · ${sessionGroup(o.session)}`);
  if (o.room && o.room.image_id === id) out.push(`סבב שיכפול · גל ${o.room.wave_number || 1}`);
  return out;
}
/* סבבים ב-30 יום (#972, ‏sessions + סבבי חדר). ‏null / שדה חסר = לא ידוע, לא 0. */
function imgRounds(r) {
  const n = r.rounds_30d;
  if (n == null) return `<span class="muted">סבבים ב-30 הימים האחרונים: לא ידוע</span>`;
  return esc(n === 0 ? "אין סבבים ב-30 הימים האחרונים" : n === 1 ? "סבב אחד ב-30 הימים האחרונים" : `${n} סבבים ב-30 הימים האחרונים`);
}
/* אימות: אימג' בספרייה = כל sha256 אומת בכניסה (עיקרון 6; מניפסט נכנס רק
   אחרי אימות). תוצאת scrub מהישיבה הזו גוברת, אחריה השמורה (last_scrub, #972). */
/* #1215: אימג' במיקום אחסון לא זמין — השרת לא הצליח לבדוק אותו. זה לא
   "תקין" וגם לא "לא תואם" (עיקרון 5): הוא לא פגום, הוא לא נבדק. */
const SCRUB_UNAVAILABLE = "המיקום לא זמין · לא נבדק";
function imgVerify(r) {
  const s = IMG.scrub[r.id], saved = r.last_scrub;
  if (s && s.state === "unavailable") return UI.status("warn", SCRUB_UNAVAILABLE);
  if (s) {
    const bad = (s.files || []).filter((f) => f.state !== "ok").map((f) => f.file);
    return s.state === "intact" ? UI.status("ok", "sha256 תקין · אומת עכשיו") : UI.status("err", "sha256 לא תואם: " + bad.join(", "));
  }
  if (saved) return saved.state === "intact" ? UI.status("ok", "sha256 תקין · " + fmtDate(saved.ts)) : UI.status("err", "sha256 לא תואם · " + fmtDate(saved.ts));
  return `<span title="כל sha256 אומת בכניסה לספרייה (קליטה או העלאה); אימות חוזר לא הורץ מאז">${UI.status("ok", "sha256 אומת")}</span>`;
}

function imagesVisible() {
  const all = IMAGES || [];
  let list = IMAGES_FOLDER == null ? all : all.filter((m) => (m.folder || "") === IMAGES_FOLDER);
  const q = IMG.filter.trim().toLowerCase();
  if (q) list = list.filter((m) => [m.name, m.description, m.folder].some((v) => String(v || "").toLowerCase().includes(q)));
  return list;
}
function selectImagesFolder(key) {
  IMAGES_FOLDER = key == null ? null : decodeURIComponent(key);
  IMG.sel.clear();
  if (current === "images") renderCurrent();
}
function imagesFilter(value) {
  IMG.filter = value || "";
  const box = document.getElementById("img-table");
  if (box) box.innerHTML = imagesTableHtml();
}

/* עץ התיקיות (220px): כל האימג'ים → תיקיות (מונה) → ללא תיקייה. גרירת אימג'
   על תיקייה = העברה; גרירת תיקייה על תיקייה = סדר. Enter/רווח בוחרים. */
function imagesTree() {
  const all = IMAGES || [], admin = isAdmin();
  const node = (label, key, cnt, opts = {}) => {
    const on = IMAGES_FOLDER === key;
    const keyJs = key == null ? "null" : `'${encodeId(key)}'`;
    const drag = opts.drag ? ` draggable="true" ondragstart="folderDragStart(event,'${encodeId(key)}')"` : "";
    const drop = opts.drop ? ` ondragover="imgDragOver(event)" ondragleave="this.classList.remove('over')" ondrop="imgDrop(event,${keyJs})"` : "";
    return `<div class="f${on ? " on" : ""}${opts.muted ? " muted" : ""}" role="treeitem" tabindex="0" aria-selected="${on}" onclick="selectImagesFolder(${keyJs})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();selectImagesFolder(${keyJs})}"${drag}${drop}>${uiIcon("folder")}<span>${esc(label)}</span><span class="cnt">${cnt}</span></div>`;
  };
  const folders = (FOLDERS || []).map((f) => node(f.name, f.name, f.images || 0, { drag: admin, drop: admin })).join("");
  const none = all.filter((m) => !m.folder).length;
  return `<div class="ftree" role="tree" aria-label="תיקיות">${node("כל האימג'ים", null, all.length)}<div class="kids">${folders}${node("ללא תיקייה", "", none, { muted: true, drop: admin })}</div></div>`
    + (admin ? `<div class="muted" style="margin-top:14px;font-size:12px">גרירת אימג' על תיקייה = העברה. סדר התיקיות והאימג'ים נקבע בגרירה (סדר התצוגה בתחנה).</div>` : "");
}

function imageRow(r) {
  const idEnc = encodeId(r.id), admin = isAdmin(), sel = IMG.sel.has(r.id);
  const drag = admin ? ` draggable="true" ondragstart="imgDragStart(event,'${idEnc}')" ondragover="imgDragOver(event)" ondragleave="this.classList.remove('over')" ondrop="imgDropOnRow(event,'${idEnc}')"` : "";
  return {
    attrs: `class="${sel ? "sel" : ""}" data-id="${esc(r.id)}"${drag}`,
    cells: [
      `<input type="checkbox" aria-label="בחירת ${esc(r.name)}" ${sel ? "checked" : ""} onchange="toggleImgSel('${idEnc}',this.checked)">`,
      `<span class="dskico">${uiIcon("storage")}</span><a role="link" tabindex="0" class="name" onclick="openImageDetail('${idEnc}')">${esc(r.name)}</a>${r.available === false ? ` ${UI.pill("err", "לא זמין")}` : ""}`,   // ‏#1066: המיקום לא נגיש — רשום, לא נמחק. ‏#1300: בלי תיאור מתחת לשם (הוא במגירה)
      esc(imageOsLabel(r.os)), ltr(fmtBytes(r.total_compressed_bytes)), r.system_unmarked ? sysUncheckedCell(r) : imgFits(r), r.folder ? esc(r.folder) : `<span class="muted">ללא תיקייה</span>`, imgVerify(r), ltr(fmtDate(r.created)),
      UI.rowMenu(r.name, [["פרטים", `openImageDetail('${idEnc}')`], ["הפץ", `deployImage('${idEnc}')`]]
        .concat((r.system_candidates || []).length ? [["סימון מחיצת המערכת (C:)…", `markSystemPartition('${idEnc}')`]] : [])   // ‏#1896
        .concat(admin && typeof vmuImageMenuItem === "function" ? [vmuImageMenuItem(r)].filter(Boolean) : [])   // ‏#1334
        .concat(admin ? [["שינוי שם…", `renameImage('${idEnc}')`], ["העבר לתיקייה…", `moveImage('${idEnc}')`], ["מחיקה…", `deleteImage('${idEnc}')`, "danger"]] : [])),
    ],
  };
}
/* התקדמות קליטה — Progress בלבד (‏#435: מכנה אפס = "לא ידוע", לא 0%).
   ‏#1254: בלי מכנה — כמה כבר נכתב לשרת, במספר (דחוס, מונה ה-pv); לא "נקראו בייטים" בלי מספר. */
/* #1470: `verifying` — מחשב הבנייה שוחרר, והשרת קורא שוב כל קובץ מהדיסק לפני שהאימג' נכנס לספרייה.
   בתהליך (מוצג כפעיל, עם התקדמות) — אבל אינו ניתן לביטול ואינו תופס את מחשב הבנייה. */
const VERIFYING_LABEL = "מאמת";
function captureInFlight(t) { return t.state === "pending" || t.state === "running" || t.state === "verifying"; }
function captureProgressCell(t, prefix = "") {
  const v = Progress.view(t), n = t.bytes_written;
  const label = v.percent == null && typeof n === "number" && n > 0
    ? `<bdi dir="ltr">${esc(fmtBytes(n))}</bdi> נכתבו לשרת · הסך לא ידוע` : esc(v.label);
  return Progress.bar(t) + `<span class="sub">${esc(prefix)}${label}</span>`;
}
/* קליטה בתהליך = שורה אפורה בטבלה (images.md), לא בר מעל. ‏/tasks אינו
   מחזיר תיקייה (#968) — מוצגת תחת "כל האימג'ים". */
function captureRowHtml(t) {
  const waiting = t.state === "pending", verifying = t.state === "verifying";
  const who = t.machine || t.mac || "";
  const sub = waiting ? `קליטה בתהליך — ממתין שמחשב הבנייה ${who} יעלה ב-PXE`
    : verifying ? "מאמת — כל מחיצה נקראת שוב מדיסק השרת; האימג' ייכנס לספרייה כשהאימות יעבור"
    : `קליטה בתהליך — ${who}${t.disk ? " · " + t.disk : ""}`;
  const cancel = isAdmin() && !verifying ? `<div class="acts always"><button class="btn sm danger" onclick="cancelCapture(decodeURIComponent('${encodeId(t.id)}'))">בטל קליטה</button></div>` : "";
  // ‏#1254: ההתקדמות בעמודת "בשימוש" (המצב של השורה); "אימות" נשאר ל-sha256, שנבדק בכניסה לספרייה.
  const verify = verifying ? UI.status("run", VERIFYING_LABEL)
    : `<span class="muted" title="sha256 של כל מחיצה נבדק כשהאימג' נכנס לספרייה">בסוף הקליטה</span>`;
  return `<tr class="task"><td></td><td>${UI.name(t.name || "", sub)}<div class="cap-prog">${waiting ? UI.status("run", "ממתין") + captureProgressCell(t) : captureProgressCell(t, verifying ? "מאמת… " : "נקלט… ")}</div></td><td class="c-opt">—</td><td>${ltr(fmtBytes(t.bytes_written))}</td><td>—</td><td class="c-opt">—</td><td>${verify}</td><td class="c-opt">${ltr(fmtDate(t.created_at))}</td><td>${cancel}</td></tr>`;
}
function imagesSelBar(list) {
  const n = IMG.sel.size;
  if (!n) return "";
  const admin = isAdmin(), one = n === 1 ? encodeId([...IMG.sel][0]) : null;
  return `<div class="dg-bar foot"><span class="n">${n === 1 ? "נבחר 1" : `נבחרו ${n}`} מתוך ${list.length}</span><span class="sp"></span>`
    + (admin ? `<button class="btn sm" onclick="bulkMove()">העבר לתיקייה…</button>` : "")
    + `<button class="btn sm" onclick="bulkDownload()">הורדה</button>`
    + (admin && one ? `<button class="btn sm" onclick="renameImage('${one}')">שינוי שם</button>` : "")
    + (admin ? `<button class="btn sm danger" onclick="bulkDelete()">מחיקה</button>` : "")
    + `<button class="btn sm flat" onclick="imgSelClear()">נקה בחירה</button></div>`;
}
function imagesTableHtml() {
  const list = imagesVisible(), admin = isAdmin(), all = IMAGES || [];
  const q = IMG.filter.trim().toLowerCase();
  const tasks = IMAGES_FOLDER == null
    ? (CAPTURE_TASKS || []).filter((t) => captureInFlight(t) && (!q || String(t.name || "").toLowerCase().includes(q)))
    : [];
  if (!all.length && !tasks.length) {
    return UI.empty("אין אימג'ים בספרייה — קלוט ממחשב בנייה או העלה קובץ tar.",
      admin ? `<button class="btn primary" onclick="openCapture().catch(e => toast(e.message))">+ קליטה ממחשב בנייה</button> <button class="btn" onclick="openImageIngest()">העלאת קובץ tar</button>` : "");
  }
  if (!list.length && !tasks.length) {
    return IMG.filter ? UI.empty("אין אימג'ים שתואמים לסינון") : UI.empty("גררו אליה אימג', או בחרו אותה בקליטה הבאה.", "", "התיקייה ריקה");
  }
  const allSel = list.length > 0 && list.every((r) => IMG.sel.has(r.id));
  const columns = [{ html: `<input type="checkbox" aria-label="בחירת הכול" ${allSel ? "checked" : ""} onchange="toggleImgSelAll(this.checked)">` },
    "שם", "מערכת", "גודל", "מתאים לדיסק", "תיקייה", "מצב", "נקלט", { html: `<span class="sr">פעולות</span>` }];
  // ‏console-full.html imgCols: מערכת, תיקייה ונקלט — עמודות משניות (נעלמות בטבלה צרה). "בשימוש" — בראש מגירת האימג' (#1300).
  return UI.datagrid({ columns, cls: "kebab-col", opt: [2, 5, 7], rows: list.map(imageRow).concat(tasks.map((t) => ({ html: captureRowHtml(t) }))) }) + imagesSelBar(list);
}
/* ‏#954 גל 2: הדף נצבע מחדש רק כשרשימת /tasks השתנתה — אחרת הבחירה
   והסינון היו נמחקים בכל דגימה (2 שניות). */
function imagesTasksChanged(all) {
  const key = JSON.stringify(all.map((t) => [t.id, t.state, t.bytes_written, t.error, t.source_progress, t.verify_bytes]));   // ‏#1254: גם האחוז; ‏#1470: גם האימות
  if (key === IMG.tasksKey) return false;
  IMG.tasksKey = key;
  return current === "images";
}
function imagesFilesCard() {
  const admin = isAdmin();
  const folder = IMAGES_FOLDER ? (FOLDERS || []).find((f) => f.name === IMAGES_FOLDER) : null;
  const crumb = IMAGES_FOLDER == null ? `<b>כל האימג'ים</b>`
    : `<a role="link" tabindex="0" onclick="selectImagesFolder(null)">כל האימג'ים</a><span>/</span><b>${esc(IMAGES_FOLDER === "" ? "ללא תיקייה" : IMAGES_FOLDER)}</b>`;
  const desc = folder && folder.description ? `<span class="muted">— ${esc(folder.description)}</span>` : "";
  const edit = admin && folder
    ? `<button class="btn sm" onclick="editFolderSheet(FOLDERS.find(f=>f.name===decodeURIComponent('${encodeId(folder.name)}')))">עריכת תיקייה</button>`
      + (folder.images ? "" : `<button class="btn sm danger" onclick="deleteFolder('${encodeId(folder.name)}')">מחק תיקייה</button>`)
    : "";
  // ‏#1300: "כל האימג'ים" — כותרת בולטת של הטבלה (.fcrumb, console-full.html), לא שביל פירורים קטן.
  const bar = `<div class="dg-bar"><span class="fcrumb">${crumb}</span>${desc}<span class="sp"></span><input type="search" value="${esc(IMG.filter)}" placeholder="חיפוש אימג'" aria-label="סינון אימג'ים" oninput="imagesFilter(this.value)" style="width:180px">${edit}</div>`;
  const warned = (CAPTURE_TASKS || []).filter((t) => t.state === "done" && t.error).map(captureWarningHtml).join("");
  const title = IMAGES_FOLDER == null ? "אימג'ים" : IMAGES_FOLDER === "" ? "ללא תיקייה" : IMAGES_FOLDER;
  return `<div class="c12 card"><div class="card-h"><span>${esc(title)}</span></div><div class="split"><div class="side">${imagesTree()}</div><div><div id="capture-bar">${warned}</div>${bar}<div id="img-table">${imagesTableHtml()}</div></div></div></div>`;
}

/* לשונית "קליטות": 20 המשימות האחרונות מ-/tasks. */
function captureStateView(t) {
  const cls = t.state === "failed" ? "err" : t.state === "done" ? (t.error ? "warn" : "ok") : t.state === "pending" ? "warn" : t.state === "cancelled" ? "" : "run";
  const label = t.state === "pending" ? "ממתין שהמחשב יעלה ב-PXE" : t.state === "running" ? "קולט" : t.state === "verifying" ? VERIFYING_LABEL
    : t.state === "done" ? (t.error ? "הושלם עם אזהרה" : "הושלם") : t.state === "failed" ? "נכשל" : t.state === "cancelled" ? "בוטל" : t.state;
  return { cls, label };
}
function imagesCapturesCard() {
  const tasks = CAPTURE_TASKS || [];
  const rows = tasks.map((t) => {
    const v = captureStateView(t);
    const active = t.state === "pending" || t.state === "running";   // ניתן לביטול — לא `verifying` (#1470)
    const acts = [];
    if (t.image_id && findImage(t.image_id)) acts.push(["פרטים", `openImageDetail('${encodeId(t.image_id)}')`]);
    const cancel = isAdmin() && active ? `<div class="acts always"><button class="btn sm danger" onclick="cancelCapture(decodeURIComponent('${encodeId(t.id)}'))">ביטול</button></div>` : "";
    return [UI.name(t.machine || t.mac || "", t.group_label || ""), `<span class="mono">${esc(t.disk || "—")}</span>`, UI.nameHtml(t.name || "", ltr(fmtDate(t.created_at) + " " + fmtClock(t.created_at))),
      UI.status(v.cls, v.label), captureInFlight(t) ? captureProgressCell(t) : ltr(fmtBytes(t.bytes_written)),
      t.error ? `<span class="${t.state === "failed" ? "muted" : "muted"}">${esc(t.error)}</span>` : "", (acts.length ? UI.acts(acts) : "") + cancel];
  });
  return UI.card({ title: "קליטות", small: "20 האחרונות", flush: true,
    body: UI.datagrid({ columns: ["מחשב בנייה", "דיסק", "אימג'", "מצב", "התקדמות", "הערה", ""], rows, empty: "אין קליטות עדיין — \"+ קליטה ממחשב בנייה\" פותח את הראשונה" }) });
}

/* לשונית "אחסון": storage מ-/overview + אימות (scrub) של הספרייה. */
function imagesStorageCard() {
  const st = OVERVIEW && OVERVIEW.storage;
  const read = !!(OVERVIEW && st);
  const total = read ? st.total_bytes : null, free = read ? st.free_bytes : null;
  const used = total != null && free != null ? total - free : null;
  const pct = total ? Math.round(100 * used / total) : null;
  const all = IMAGES || [];
  const lib = all.reduce((s, r) => s + (Number(r.total_compressed_bytes) || 0), 0);
  const kpis = `<div class="c12 kpis">`
    + UI.kpi({ cls: read ? (pct != null && pct >= 90 ? "warn" : "ok") : "", label: "פנוי בדיסק האימג'ים", value: read ? fmtBytes(free) : "—", sub: read ? `${ltr(fmtBytes(used))} בשימוש מתוך ${ltr(fmtBytes(total))}` : (OVERVIEW ? "אין נתון אחסון" : "לא נקרא"), bar: pct, barPct: true })
    + UI.kpi({ cls: "info", label: "הספרייה", value: fmtBytes(lib), sub: `${all.length} ${all.length === 1 ? "אימג'" : "אימג'ים"} · ${(FOLDERS || []).length} תיקיות` })
    + `</div>`;
  const results = Object.entries(IMG.scrub);
  const rows = results.map(([id, s]) => {
    const img = findImage(id);
    const bad = (s.files || []).filter((f) => f.state !== "ok");
    const shown = s.state === "intact" ? ["ok", "sha256 תקין"] : s.state === "unavailable" ? ["warn", SCRUB_UNAVAILABLE] : ["err", "sha256 לא תואם"];
    return [UI.name(img ? img.name : id, id), UI.status(...shown),
      esc(`${(s.files || []).length} קבצים`), bad.length ? esc(bad.map((f) => `${f.file}: ${f.state}${f.error ? " — " + f.error : ""}`).join(" · ")) : ""];
  });
  const scrub = isAdmin()
    ? UI.card({ title: "אימות הספרייה", small: "sha256 של כל קובץ מחיצה מול המניפסט", acts: `<button class="btn sm" onclick="scrubLibrary()">אמת את כל הספרייה</button>`, flush: true,
      body: UI.datagrid({ columns: ["אימג'", "תוצאה", "קבצים", "פרטים"], rows, empty: "לא הורץ אימות בישיבה הזו. התוצאה השמורה האחרונה לכל אימג' — בעמודת \"אימות\" בטבלת הקבצים." }) })
    : "";
  return kpis + scrub;
}

function images(tab = 0) {
  const tabs = imagesTabs(), admin = isAdmin(), all = IMAGES || [];
  const st = OVERVIEW && OVERVIEW.storage;
  const sub = [`${all.length} ${all.length === 1 ? "אימג'" : "אימג'ים"}`, `${(FOLDERS || []).length} תיקיות`]
    .concat(st ? [`${ltr(fmtBytes(st.total_bytes - st.free_bytes))} בשימוש`, `${ltr(fmtBytes(st.free_bytes))} פנוי`] : []).join(" · ");
  const actions = (admin
    ? `<button class="btn primary" onclick="openCapture().catch(e => toast(e.message))">+ קליטה ממחשב בנייה</button><button class="btn" onclick="openImageIngest()">העלאת קובץ tar</button><button class="btn" onclick="addFolderSheet()">+ תיקייה</button>`
    : "") + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "מלאי" }, { label: "אימג'ים" }],
    icon: "t-images", name: "ספריית אימג'ים", sub, actions, tab,
    tabs: IMAGES ? [[tabs[0], all.length], [tabs[1], (CAPTURE_TASKS || []).filter(captureInFlight).length], tabs[2]] : tabs });
  let body;
  if (!IMAGES) body = `<div class="c12">${pagePlaceholder()}</div>`;
  else if (tab === 1) body = imagesCapturesCard();
  else if (tab === 2) body = imagesStorageCard();
  else body = imagesFilesCard();
  return `<div class="page aligned">${header}<div class="body">${body}</div></div>`;
}

/* --- בחירה מרובה --- */
function toggleImgSel(id, on) {
  id = decodeURIComponent(id);
  if (on) IMG.sel.add(id); else IMG.sel.delete(id);
  imagesFilter(IMG.filter);
}
function toggleImgSelAll(on) {
  for (const r of imagesVisible()) { if (on) IMG.sel.add(r.id); else IMG.sel.delete(r.id); }
  imagesFilter(IMG.filter);
}
function imgSelClear() { IMG.sel.clear(); imagesFilter(IMG.filter); }
function bulkMove() {
  if (!isAdmin() || !IMG.sel.size) return;
  const ids = [...IMG.sel];
  sheet({
    title: "העברה לתיקייה", sub: ids.length === 1 ? "" : `${ids.length} אימג'ים נבחרו.`,
    fields: [{ id: "folder", label: "תיקייה", type: "select", value: IMAGES_FOLDER || "",
      options: [{ value: "", label: "ללא תיקייה" }].concat((FOLDERS || []).map((f) => ({ value: f.name, label: f.name }))) }],
    submitLabel: "העבר",
    onSubmit: async (v) => { await moveImages(ids, v.folder); },
  });
}
function bulkDownload() {
  if (secondaryReadOnly()) { toast(SECONDARY_READ_ONLY); return; }   // ‏#1179: הקובץ היה של הראשי
  for (const id of IMG.sel) {
    const a = document.createElement("a");
    a.href = "/api/console/images/" + encodeId(id) + "/download";
    a.download = id + ".tar";
    document.body.appendChild(a); a.click(); a.remove();
  }
}
/* מחיקה מרובה: חלון אישור אחד עם הרשימה (#1270); confirm_name נשלח מהשם הידוע — השרת מאמת כל שם בנפרד. */
function bulkDelete() {
  if (!isAdmin() || !IMG.sel.size) return;
  const imgs = [...IMG.sel].map((id) => findImage(id)).filter(Boolean);
  if (imgs.length === 1) { deleteImage(encodeId(imgs[0].id)); return; }
  sheet({
    title: `למחוק ${imgs.length} אימג'ים?`, danger: true, submitLabel: `מחיקת ${imgs.length} אימג'ים`,
    sub: imgs.map((m) => m.name).join(" · "),
    note: impactList([["b", "האימג'ים וכל קבציהם יימחקו מהספרייה"], ["b", "הפעולה אינה הפיכה"]]),
    onSubmit: async () => {
      for (const m of imgs) await post("/images/" + encodeId(m.id) + "/delete", { confirm_name: m.name });
      IMG.sel.clear(); closeDrawer(); toast(`${imgs.length} אימג'ים נמחקו`); await loadImages();
    },
  });
}

/* --- גרירה: אימג' → תיקייה = העברה; תיקייה → תיקייה = סדר; אימג' → אימג' = סדר בתיקייה --- */
function imgDragStart(ev, id) {
  id = decodeURIComponent(id);
  const ids = IMG.sel.has(id) ? [...IMG.sel] : [id];
  ev.dataTransfer.setData("application/x-imagectl-images", JSON.stringify(ids));
  ev.dataTransfer.effectAllowed = "move";
}
function folderDragStart(ev, name) {
  ev.dataTransfer.setData("application/x-imagectl-folder", decodeURIComponent(name));
  ev.dataTransfer.effectAllowed = "move";
}
function imgDragOver(ev) {
  if (!isAdmin()) return;
  ev.preventDefault();
  ev.currentTarget.classList.add("over");
}
async function imgDrop(ev, folder) {
  ev.preventDefault(); ev.currentTarget.classList.remove("over");
  folder = folder == null ? null : decodeURIComponent(folder);
  const ids = ev.dataTransfer.getData("application/x-imagectl-images");
  if (ids) { await moveImages(JSON.parse(ids), folder); return; }
  const name = ev.dataTransfer.getData("application/x-imagectl-folder");
  if (name && folder && name !== folder) await reorderFolderBefore(name, folder);
}
async function imgDropOnRow(ev, targetId) {
  ev.preventDefault(); ev.currentTarget.classList.remove("over");
  const ids = ev.dataTransfer.getData("application/x-imagectl-images");
  if (!ids) return;
  const [id] = JSON.parse(ids);
  await reorderImageBefore(id, decodeURIComponent(targetId));
}
async function moveImages(ids, folder) {
  try {
    for (const id of ids) await put("/images/" + encodeId(id), { folder: folder || "" });
    IMG.sel.clear();
    toast(ids.length === 1 ? "הועבר." : `${ids.length} אימג'ים הועברו.`);
    await loadImages();
  } catch (e) { toast(e.message); }
}
async function reorderFolderBefore(name, before) {
  const names = (FOLDERS || []).map((f) => f.name).filter((n) => n !== name);
  const i = names.indexOf(before);
  names.splice(i < 0 ? names.length : i, 0, name);
  try { await post("/folders/order", { names }); await loadImages(); } catch (e) { toast(e.message); }
}
async function reorderImageBefore(id, beforeId) {
  const img = findImage(id), target = findImage(beforeId);
  if (!img || !target || img === target || (img.folder || "") !== (target.folder || "")) return;
  const list = (IMAGES || []).filter((m) => (m.folder || "") === (img.folder || "") && m !== img);
  list.splice(list.indexOf(target), 0, img);
  try {
    for (let n = 0; n < list.length; n++) await put("/images/" + encodeId(list[n].id), { sort: n + 1 });
    await loadImages();
  } catch (e) { toast(e.message); }
}

/* --- פעולות --- */
/* ‏"פתח סבב עם אימג' זה" (מגירת אימג'): טופס הסבב בדף ההפצה נפתח עם האימג' כבר נבחר (מקור: הספרייה). */
/* ‏16/10 (נדב): "הפץ" על אימג' פותח את הסבב עם האימג' כבר בחלון — נשאר לבחור מגירות ולהתחיל.
   בודקים את החדר מחדש לפני (לא סומכים על מטמון); סבב פעיל = הודעה עם קישור אליו, בלי בחירה חדשה.
   "לא נקרא" ≠ "אין סבב": כשהחדר לא נקרא מוצגת שגיאה ולא חלון ריק. */
function roundBusyNote(idEnc) {
  const r = roomRound(), s = OVERVIEW && OVERVIEW.session;
  const what = r ? `${roomImageLabel(r)} (גל ${r.wave_number || 1})` : s ? esc(sessionImage(s)) : "";
  openModalContent("כבר יש סבב פעיל", UI.note("warn", `כבר יש סבב פעיל${what ? " — " + what : ""}. אי אפשר לפתוח סבב נוסף עד שהוא יסתיים או ייעצר.`) + `<p>${UI.link("מעבר לסבב הפעיל", "closeModal();selectPageById('deploy')")}</p>`,
    "מעבר לסבב", "closeModal();selectPageById('deploy')", false, "", "mid");
}
async function startRoundWith(idEnc) {
  let id = idEnc; try { id = decodeURIComponent(idEnc); } catch (e) {}
  if (!roomOperator()) return;
  closeDrawer();
  try { const v = await api("/room"); ROOM = v && Array.isArray(v.machines) ? v : null; DEPLOY.roomErr = ROOM ? "" : "תשובה שאינה חדר"; }
  catch (e) { ROOM = null; DEPLOY.roomErr = e.message; }
  if (ROOM == null) { selectPageById("deploy"); toast("החדר לא נקרא — לא ניתן לפתוח סבב: " + DEPLOY.roomErr, 6000); return; }
  if (roomRound()) { roundBusyNote(); return; }
  DEPLOY.form.src = "library"; DEPLOY.form.image = id;
  selectPageById("deploy");
  openRoomRound();
}
function openRoundWithImage(idEnc) { return startRoundWith(idEnc); }
function deployImage(id) { return startRoundWith(id); }
function cancelCapture(taskId) {
  confirmSheet("לבטל את הקליטה?", "", "ביטול הקליטה",
    async () => { await post(`/tasks/${encodeId(taskId)}/cancel`); await loadCaptures(); },
    [["b", "הקבצים שהתקבלו יימחקו"], ["w", "המשימה תבוטל"]]);
}
function deleteFolder(name) {
  if (!isAdmin()) return;
  name = decodeURIComponent(name);
  confirmSheet("למחוק את התיקייה?", name, "מחיקת התיקייה",
    async () => { await post(`/folders/${encodeId(name)}/delete`); IMAGES_FOLDER = null; await loadImages(); },
    [["b", "התיקייה הריקה תימחק"], ["ok", "אימג'ים אינם נמחקים לעולם מכאן"]]);
}
async function scrubImage(id) {
  if (!isAdmin()) return;
  const img = findImage(id); if (!img) return;
  toast(`מאמת sha256 של "${img.name}"…`, 6000);
  try {
    const r = await post("/images/" + encodeId(img.id) + "/scrub");
    for (const one of r.images || []) IMG.scrub[one.id] = one;
    toast(r.intact ? "sha256 תקין." : scrubVerdict(r.images) || "sha256 לא תואם — האימג' פגום!", 6000);
    if (current === "images") renderCurrent();
    openImageDetail(img.id);
  } catch (e) { toast(e.message); }
}
/* #1215: כשהסיבה היחידה ש-intact הוא false היא מיקום לא זמין, אין אימג' פגום
   — יש אימג' שלא נבדק. ‏null = נמצא פגום באמת, וההודעה הרגילה חלה. */
function scrubVerdict(images) {
  const list = images || [];
  if (!list.length || list.some((i) => i.state !== "intact" && i.state !== "unavailable")) return null;
  return SCRUB_UNAVAILABLE + " — אימג' במיקום אחסון לא זמין לא אומת.";
}
async function scrubLibrary() {
  if (!isAdmin()) return;
  toast("מאמת את כל הספרייה…", 8000);
  try {
    const r = await post("/images/scrub");
    for (const one of r.images || []) IMG.scrub[one.id] = one;
    toast(r.intact ? "כל הספרייה תקינה." : scrubVerdict(r.images) || "נמצא אימג' פגום — ראו את הטבלה.", 6000);
    if (current === "images") renderCurrent();
  } catch (e) { toast(e.message); }
}

function imageHistory(img) {
  const events = [];
  for (const t of CAPTURE_TASKS || []) {
    if (t.image_id !== img.id || t.state !== "done") continue;
    events.push({ ts: t.updated_at || t.created_at, cls: t.error ? "warn" : "ok",
      text: esc(`נקלט ממחשב הבנייה ${t.machine || t.mac || ""}${t.disk ? " · " + t.disk : ""}`) + (t.error ? ` — ${esc(t.error)}` : ""), who: "" });
  }
  const j = IMG.history[img.id];
  if (Array.isArray(j)) for (const r of j) events.push({ ts: r.ts, cls: journalCls(r), text: esc(r.label || r.event) + (r.text ? ` — ${esc(r.text)}` : ""), who: r.user });
  events.sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
  return events.map((e) => ({ t: isToday(e.ts) ? fmtClock(e.ts) : fmtDate(e.ts), cls: e.cls, text: e.text, who: e.who }));
}
/* ‏#972: מה שהמגירה מציגה מהמניפסט הציבורי (GET /images/{id}). שדה חסר
   (מניפסט ישן) = "לא נרשם"; ‏null = "לא נמדד"/"לא נגזר" — לא ערך מומצא. */
function imageBootCa(m) {
  if (!("boot_ca" in m)) return `<span class="muted">לא נרשם (אימג' מלפני #85)</span>`;
  if (m.boot_ca == null) return `<span class="muted">לא נגזר</span>${m.boot_ca_error ? " — " + esc(m.boot_ca_error) : ""}`;
  return m.boot_ca.length ? esc(m.boot_ca.join(" · ")) : "המטענים נקראו — אין עליהם חתימה";
}
function imageManifestKv(m) {
  const known = (v) => v == null || v === "" ? `<span class="muted">לא נרשם</span>` : esc(v);
  return [
    ["נקלט על ידי", known(m.created_by)],
    ["טבלת מחיצות", `${known(m.scheme)} · סקטור ${m.sector_size == null ? known(null) : ltr(m.sector_size + " B")}`],
    ["רשויות אתחול", imageBootCa(m)],
    ["דחיסה", `${known(m.compression)} · partclone ${known(m.partclone_version)}`],
  ];
}
/* טבלת המחיצות מהמניפסט; עמודת sha256 מתוצאת scrub מהישיבה כשיש, אחרת הערך הצפוי. */
function imagePartitionsGrid(m, s) {
  const scrubbed = new Map(((s && s.files) || []).map((f) => [f.index, f]));
  const bytes = (n) => n == null ? `<span class="muted">לא נמדד</span>` : `<span title="${esc(n)} bytes">${ltr(fmtBytes(n))}</span>`;
  const rows = (m.partitions || []).map((p) => {
    const f = scrubbed.get(p.index);
    const sha = !p.file ? `<span class="muted">לא נשמר (${esc(p.fs || "—")})</span>`
      : f ? UI.status(f.state === "ok" ? "ok" : "err", f.state === "ok" ? "תקין" : f.state)
      : `<span class="mono" title="${esc(p.sha256 || "")}">${esc(String(p.sha256 || "—").slice(0, 12))}…</span>`;
    return [esc(p.index), esc(p.role || "—"), esc(p.fs || "—"), bytes(p.size_bytes), bytes(p.used_bytes),
      p.shrunk_from_bytes == null ? `<span class="muted">—</span>` : bytes(p.shrunk_from_bytes), sha];
  });
  return UI.datagrid({ columns: ["#", "תפקיד", "מערכת קבצים", "גודל", "בשימוש", "כווץ מ-", "sha256"], rows, empty: "אין מחיצות במניפסט" });
}
/* ‏#1300: ראש המגירה (console-full.html W.imgDrawer) — תגיות מצב: sha256 (אותה הכרעה כמו
   imgVerify — scrub מהישיבה, אחריו השמור, אחרת "אומת בכניסה") ו"בשימוש" מ-/overview. */
function imageChips(img, s, use) {
  const saved = img.last_scrub;
  const sha = s ? (s.state === "intact" ? ["", "sha256 אומת"] : s.state === "unavailable" ? ["w", "sha256 לא נבדק"] : ["d", "sha256 לא תואם"])
    : saved && saved.state !== "intact" ? ["d", "sha256 לא תואם"] : ["", "sha256 אומת"];
  return `<span class="n-meta"><span class="n-chip ${sha[0]}">${esc(sha[1])}</span><span class="n-chip n">${esc(use.length ? "בשימוש: " + use.join(" · ") : "לא בסבב פעיל")}</span></span>`;
}
/* --- מגירת אימג' (#1300, console-full.html W.imgDrawer): ראש — שם, משנה קצרה ותגיות; לשוניות
   עם מונים; "סקירה" = פרטים (~5 שורות + "כונן מינימלי") והודעת sha256 בתחתית; "מחיצות" =
   הנתונים הטכניים מהמניפסט + טבלת המחיצות; "היסטוריה" = מי קלט, סבבים ב-30 יום, ויומן. --- */
function imageDrawerHtml(img) {
  const admin = isAdmin(), idEnc = encodeId(img.id);
  const use = imgUsage(img.id);
  const s = IMG.scrub[img.id];
  // ‏console-full.html S148: פס פעולות קבוע — "פתח סבב עם אימג' זה" (ראשי), "שינוי שם", ו"מחיקה…" (אדום, מופרד). הורדה והעברה — קישורים בגוף.
  const actions = `<div class="acts" style="display:flex;gap:6px;flex-wrap:wrap">${roomOperator() ? `<button class="btn primary" onclick="openRoundWithImage('${idEnc}')">פתח סבב עם אימג' זה</button>` : ""}${admin ? `<button class="btn" onclick="renameImage('${idEnc}')">שינוי שם</button>` : ""}</div>`;
  const saved = img.last_scrub, again = admin ? ` ${UI.link("הרץ עכשיו", `scrubImage('${idEnc}')`)}` : "";
  const when = (ts) => ltr(`${fmtDate(ts)} ${fmtClock(ts)}`);
  const verify = s
    ? (s.state === "intact" ? UI.note("ok", "sha256 של כל קובץ מחיצה תואם למניפסט — אומת עכשיו.")
      : s.state === "unavailable" ? UI.note("warn", SCRUB_UNAVAILABLE + ".")
      : UI.note("err", "sha256 לא תואם: " + esc((s.files || []).filter((f) => f.state !== "ok").map((f) => `${f.file} (${f.state})`).join(", ")) + " — האימג' פגום, אין להפיץ אותו."))
    : saved
      ? (saved.state === "intact" ? UI.note("ok", `sha256 של כל קובץ מחיצה תואם למניפסט — אימות חוזר אחרון ${when(saved.ts)}.${again}`)
        : UI.note("err", `האימות החוזר האחרון (${when(saved.ts)}) מצא sha256 לא תואם — האימג' פגום, אין להפיץ אותו.${again}`))
      : UI.note("ok", `sha256 של כל מחיצה אומת בכניסה לספרייה · אימות חוזר לא הורץ מאז.${admin ? ` אימות חוזר: ${UI.link("הרץ עכשיו", `scrubImage('${idEnc}')`)}` : ""}`);
  const m = IMG.manifest[img.id], mOk = !!m && !m.error;
  // ‏"מ-<מחשב>" רק כשמשימת הקליטה של האימג' ידועה (/tasks) — לא ניחוש.
  const task = (CAPTURE_TASKS || []).find((t) => t.image_id === img.id && t.state === "done");
  const from = task && (task.machine || task.mac) ? ` מ-${esc(task.machine || task.mac)}` : "";
  const sub = `${esc(imageOsLabel(img.os))} · ${ltr(fmtBytes(img.total_compressed_bytes))} · נקלט ${ltr(fmtDate(img.created))}${from}`;
  const kv = UI.kv([
    ["תיאור", esc(img.description || "—") + (admin ? ` ${UI.link("עריכה", `editImageDescription('${idEnc}')`)}` : "")],
    ["תיקייה", esc(img.folder || "ללא תיקייה") + (admin ? ` ${UI.link("העבר", `moveImage('${idEnc}')`)}` : "")],
    ["מזהה", `<span class="mono">${esc(img.id)}</span>`],
    ["דיסק מקור", img.source_disk_bytes == null ? `<span class="muted">לא ידוע</span>` : ltr(marketedDiskSize(img.source_disk_bytes))],   /* ‏#1016: בלי "family N" — מונח פנימי */
    ["כונן מינימלי", imgFits(img, true)],
    ["גודל בשרת", `${ltr(fmtBytes(img.total_compressed_bytes))} דחוס <a href="/api/console/images/${idEnc}/download">הורדה</a>`],
  ]);
  const tech = UI.kv([
    ["בשימוש במקור", img.used_bytes == null ? `<span class="muted">לא ידוע</span>` : ltr(fmtBytes(img.used_bytes))],
    ...(mOk ? imageManifestKv(m).filter(([k]) => k !== "נקלט על ידי") : []),
  ]);
  // עד שהמניפסט נקרא (או כשלא נקרא) — תוצאת scrub מהישיבה, אם יש, לפי קובץ.
  const scrubGrid = s && (s.files || []).length
    ? UI.datagrid({ columns: ["#", "קובץ", "sha256"], rows: s.files.map((f) => [esc(f.index), `<span class="mono">${esc(f.file)}</span>`, UI.status(f.state === "ok" ? "ok" : "err", f.state === "ok" ? "תקין" : f.state)]) })
    : "";
  const parts = mOk ? imagePartitionsGrid(m, s)
    : (m ? UI.note("warn", "המניפסט לא נקרא: " + esc(m.error)) : UI.note("", "טוען את טבלת המחיצות…")) + scrubGrid;
  const hist = imageHistory(img);
  const jErr = IMG.history[img.id] && IMG.history[img.id].error;
  const history = hist.length ? UI.timeline(hist)
    : jErr ? UI.note("warn", "היומן לא נקרא: " + esc(jErr))
    : `<div class="muted">${admin ? "אין אירועים ביומן לאימג' הזה (20 האחרונים)." : "היסטוריית היומן זמינה למנהל בלבד."}</div>`;
  const who = UI.kv([
    ...(mOk ? imageManifestKv(m).filter(([k]) => k === "נקלט על ידי") : []),
    ["סבבים", imgRounds(img)],
  ]);
  const foot = admin
    ? `<div style="border-top:1px solid var(--hair);padding-top:12px;display:flex;gap:6px;flex-wrap:wrap"><button class="btn sm danger" onclick="deleteImage('${idEnc}')">מחיקה…</button></div>`
    : "";
  // ‏data-cnt — המונה שבלשונית (drawerEnhance). מחיצות: מה-/images; היסטוריה: מה שנקרא עד עכשיו.
  return `<div class="page drw"><div class="obj-sub">${sub}${imageChips(img, s, use)}</div>${actions}<div><div class="sec">פרטים</div>${kv}</div>${verify}`
    + `<div><div class="sec" data-cnt="${esc(img.partitions ?? "")}">מחיצות</div>${tech}${parts}</div>`
    + `<div><div class="sec" data-cnt="${hist.length || ""}">היסטוריה</div>${who}${history}</div>`
    + (typeof vmuDrawerHtml === "function" ? vmuDrawerHtml(img) : "")   // ‏#1334: עדכון ב-VM + גרסאות
    + `${foot}</div>`;
}
function openImageDetail(id) {
  const img = findImage(id);
  if (!img) { toast("אימג' לא נמצא"); return; }
  openDrawer(img.name, imageDrawerHtml(img));
  const gen = drawerGeneration;
  const versions = () => { if (isAdmin() && typeof vmuLoadVersions === "function") vmuLoadVersions(img.id); };   // ‏#1334
  versions();
  const redraw = () => { if (gen === drawerGeneration) { setDrawerBody(imageDrawerHtml(img)); versions(); } };
  // ‏#972: המניפסט נקרא בכל פתיחה (לכל משתמש) — הקודם מוצג עד שהחדש מגיע.
  api("/images/" + encodeId(img.id))
    .then((m) => { IMG.manifest[img.id] = m; })
    .catch((e) => { IMG.manifest[img.id] = { error: e.message }; })
    .then(redraw);
  if (!isAdmin() || IMG.history[img.id]) return;
  api("/journal?q=" + encodeURIComponent(img.name) + "&limit=20")
    .then((rows) => { IMG.history[img.id] = rows; })
    .catch((e) => { IMG.history[img.id] = { error: e.message }; })
    .then(redraw);
}

function renameImage(id) {
  if (!isAdmin()) return;
  const img = findImage(id);
  if (!img) return;
  openModalContent(
    "שינוי שם",
    `<div class="form"><div class="field full"><label>השם החדש</label><input id="renameInput" autocomplete="off"></div></div>`,
    "שמור",
    () => applyRename(img.id)
  );
  const input = document.getElementById("renameInput");
  if (input) { input.value = img.name; input.focus(); }
}

async function applyRename(id) {
  const input = document.getElementById("renameInput");
  const name = input ? input.value : "";
  if (!name) { toast("שם לא יכול להיות ריק"); return; }
  try {
    await put("/images/" + encodeId(id), { name });
    closeModal();
    toast("נשמר.", 2600, "ok");
    await loadImages();
    openImageDetail(id);
  } catch (e) {
    toast(e.message);
  }
}

function moveImage(id) {
  if (!isAdmin()) return;
  const img = findImage(id);
  if (!img) return;
  const options = [`<option value="">ללא תיקייה</option>`]
    .concat((FOLDERS || []).map((f) => `<option value="${esc(f.name)}">${esc(f.name)}</option>`))
    .join("");
  openModalContent(
    "העברה לתיקייה",
    `<div class="form"><div class="field full"><label>תיקייה</label><select id="moveFolder">${options}</select></div></div>`,
    "העבר",
    () => applyMove(img.id)
  );
  const sel = document.getElementById("moveFolder");
  if (sel) sel.value = img.folder || "";
}

async function applyMove(id) {
  const sel = document.getElementById("moveFolder");
  const folder = sel ? sel.value : "";
  try {
    await put("/images/" + encodeId(id), { folder });
    closeModal();
    toast("נשמר.", 2600, "ok");
    await loadImages();
    openImageDetail(id);
  } catch (e) {
    toast(e.message);
  }
}

function deleteImage(id) {
  if (!isAdmin()) return;
  const img = findImage(id);
  if (!img) return;
  const size = img.total_compressed_bytes ? fmtBytes(img.total_compressed_bytes) : "";
  const use = OVERVIEW ? imgUsage(img.id) : null;   // ‏null = /overview לא נקרא: לא ידוע, לא "פנוי"
  sheet({
    title: "למחוק את האימג'?", danger: true, submitLabel: "מחיקה",
    sub: img.name + (size ? ` · ${size}` : ""),
    note: impactList([["b", size ? `${ltr(size)} יימחקו מהספרייה` : "האימג' וכל קבציו יימחקו מהספרייה"],
      use === null ? ["w", "לא נבדק אם האימג' בשימוש בסבב פעיל"]
        : use.length ? ["w", `האימג' בשימוש עכשיו: ${esc(use.join(" · "))}`] : ["ok", "לא בשימוש בסבב פעיל — אפשר למחוק בבטחה"],
      ["b", "הפעולה אינה הפיכה. מחשבים שכבר שוחזרו לא יושפעו."]]),
    onSubmit: async () => {
      await post("/images/" + encodeId(img.id) + "/delete", { confirm_name: img.name });
      closeDrawer();
      toast("האימג׳ נמחק", 2600, "ok");
      loadImages();
    },
  });
}

function openImageIngest() {
  if (!(ME && ME.role === "admin")) { toast("אין הרשאה"); return; }
  sheet({ title: "העלאת קובץ tar", sub: "אימג׳ שנוצר במקום אחר", submitLabel: "העלה", size: "mid",
    note: `<div class="form"><div class="field full">${dropzoneHtml({ id: "imageFile", accept: ".tar,application/x-tar", aria: "בחירת קובץ tar של אימג'" })}</div><div class="field full"><div class="form-note">הקובץ יועלה ויאומת מול ה-manifest. אימג׳ פגום לא ייכנס לספרייה.</div></div><div id="imageUploadProgress"></div></div>`,
    onSubmit: startImageUpload });
}

function startImageUpload() {
  const input = document.getElementById("imageFile");
  const file = input && input.files && input.files[0];
  if (!file) { toast("בחר קובץ לפני העלאה"); return; }
  return uploadImage(file);
}

function uploadImage(file) {
  if (secondaryReadOnly()) { toast(SECONDARY_READ_ONLY); return; }   // ‏#1179: XHR עוקף את api()
  const progress = document.getElementById("imageUploadProgress");
  const body = document.getElementById("modalBody");
  const ok = document.getElementById("modalOk") || document.getElementById("sf-submit");
  if (ok) ok.disabled = true;
  const draw = (pct, text) => {
    const html = `<div><div class="list-row"><div class="list-main"><strong>${esc(file.name)}</strong><small>${esc(text)}</small></div><div class="list-side">${pct}%</div></div><div class="progress" style="margin-top:7px"><i style="width:${pct}%"></i></div></div>`;
    if (progress) progress.innerHTML = html;
    if (body && body !== progress) body.innerHTML = html;
  };
  draw(0, "מתחיל…");

  return new Promise((resolve, reject) => { const request = new XMLHttpRequest();
  request.open("POST", "/api/console/images/upload");
  request.setRequestHeader("Content-Type", "application/x-tar");
  request.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round((100 * e.loaded) / e.total);
    draw(pct, `${pct}% · ${fmtBytes(e.loaded)} מתוך ${fmtBytes(e.total)}`);
  };
  request.onload = () => {
    if (request.status === 200) {
      let name = file.name;
      try { name = JSON.parse(request.responseText).name || name; } catch (e) {}
      toast(`"${name}" נוסף לספרייה.`);
      loadImages();
      resolve();
    } else {
      let message = "שגיאה " + request.status;
      try { message = JSON.parse(request.responseText).detail || message; } catch (e) {}
      if (ok) ok.disabled = false;
      reject(new Error("ההעלאה נכשלה: " + message));
    }
  };
  request.onerror = () => {
    if (ok) ok.disabled = false;
    reject(new Error("ההעלאה נכשלה — החיבור נותק."));
  };
  request.send(file); });
}

async function startRound() {
  if (!ME || !["admin", "deploy"].includes(ME.role)) return;
  const session = OVERVIEW && OVERVIEW.session;
  if (!session || !session.id) return;
  try {
    await post("/sessions/" + encodeId(session.id) + "/start");
    toast("הסבב התחיל");
    refreshStatus();
  } catch (e) {
    toast(e.message);
  }
}

async function reopenRound() {
  if (!ME || !["admin", "deploy"].includes(ME.role)) return;
  const s = OVERVIEW && OVERVIEW.session;
  if (!s || s.state !== "failed") return;
  const body = { group_id: s.group_id, image_id: s.image_id, prefix: s.prefix,
    expected_clients: s.expected_clients, expand_partition: s.expand_partition };
  if (Array.isArray(s.roster)) body.macs = s.roster;
  try {
    await post("/sessions", body);
    toast("הסבב נפתח מחדש");
    refreshStatus();
  } catch (e) { toast("הסבב לא נפתח מחדש: " + e.message, 6000); }
}

function stopRound() {
  if (!ME || !["admin", "deploy"].includes(ME.role)) return;
  const session = OVERVIEW && OVERVIEW.session;
  if (!session || !session.id) return;
  const sessionId = session.id;
  const name = sessionImage(session);
  sheet({
    title: "לעצור את הסבב?", danger: true, submitLabel: "עצירת הסבב",
    sub: `הסבב על "${name}" ייעצר`,
    note: impactList([["b", "השידור ייפסק"], ["w", "לא תתאפשר הצטרפות חדשה"]]),
    onSubmit: async () => {
      await post("/sessions/" + encodeId(sessionId) + "/close", { confirm_name: name });
      toast("הסבב נעצר");
      refreshStatus();
    },
  });
}

/* ---------- #954 גל 3: מחשבים — טבלה אחת מקובצת, מגירת מחשב, כיתה כאובייקט ----------
   נבנה לפי docs/design/console-redesign/machines.md (+ class.md, machine-drawer.md).
   תיקון נדב (17/09): מחשבי כיתה מחולקים **לפי כיתה** — כל כיתה קבוצה מתקפלת
   ושורת roll-up, כמו Hosts & Clusters. בנייה ושיכפול = הקבוצות הקבועות.
   מקורות: /machines, /groups, /net (join לפי MAC: IP, נראה לאחרונה, שלב אתחול),
   /disk-failures, /shrink-records, /monitor/machines ("מחובר" — בנייה/שיכפול
   בלבד, נקבע בשרת), /overview.session (סבב פעיל לכיתה). מה שאין לו API —
   "מחובר עכשיו" לתחנת כיתה, WoL למחשב יחיד, אתחול מרחוק, עריכת MAC, קידומת
   קבועה לכיתה, היסטוריית סבבים — מוצג כ"דורש API", לא מומצא (Issue מרוכז).
   עיקרון 5: `disks` null/[]/רשימה הם שלושה מצבים; "לא נקרא" ≠ ריק; "לא נבדק"
   לעולם לא ירוק. "דיסק 1/2/3 · SATA 0/1/2" — לעולם לא sd*. */
let NET = null, NET_ERR = "";            // ‏/net — null = לא נקרא (≠ רשימה ריקה)
let MONITOR_ROWS = null;                 // ‏/monitor/machines — online מהשרת; null = לא נקרא
let MACHINES_CLASS = null;               // הקבוצה הפתוחה כאובייקט (כיתה) — id
const MCH = { q: "", role: "", group: "", state: "", toggled: new Set(), sel: new Set(), history: {}, amTab: 0 };
const ROLE_HE = { classroom: "תחנת כיתה", build: "מחשב בנייה", cloner: "מחשב שיכפול", unknown: "ללא קבוצה" };
const ROLE_GROUP_HE = { classroom: "כיתות", build: "מחשבי בנייה", cloner: "מחשבי שיכפול" };
const ROLE_ORDER = { build: 0, cloner: 1, classroom: 2 };
const FOLD_ABOVE = 50;                   // מעל כך שורות — הקבוצות מקופלות כברירת מחדל

function machineGroup(m) { const id = machineGroupId(m); return (GROUPS || []).find((g) => g.id === id) || null; }
function machineRole(m) { const g = machineGroup(m); return g ? g.role : "unknown"; }
function netFor(mac) { return (NET || []).find((d) => d.mac === mac) || null; }
function monitorRow(mac) { return (MONITOR_ROWS || []).find((r) => r.mac === mac) || null; }
function seenAgo(iso) { return iso ? ago(iso) : "מעולם לא"; }
function groupSession(gid) { const s = OVERVIEW && OVERVIEW.session; return s && gid && s.group_id === gid ? s : null; }
function sessionMember(m) { const s = groupSession(machineGroupId(m)); return s ? (s.members || []).find((x) => x.mac === m.mac) || null : null; }
function machineGroupsInOrder() {
  return (GROUPS || []).slice()
    .filter((g) => classroomsOn() || g.role !== "classroom")
    .sort((a, b) => (ROLE_ORDER[a.role] ?? 9) - (ROLE_ORDER[b.role] ?? 9));
}
function machineModel(m) {
  const dmi = m.inventory && m.inventory.dmi;
  return dmi ? [dmi.sys_vendor, dmi.product_version || dmi.product_name].filter(Boolean).join(" ") : "";
}
function machineSub(m) {
  const parts = [machineModel(m)];
  const tpm = m.inventory && m.inventory.tpm;
  if (tpm && tpm.present) parts.push(`TPM ${tpm.version || ""}`.trim());
  if (["cloner", "build"].includes(machineRole(m)) && (m.drawer_count != null || Array.isArray(m.drawer_slots))) { const n = drawerSlots(m).length; parts.push(n === 1 ? "חריץ אחד" : `${n} חריצים`); }   // #1485
  if (machineRole(m) === "cloner" && m.nic_speed) parts.push(`כרטיס רשת: ${nicSpeedText(m.nic_speed)}`);
  return parts.filter(Boolean).join(" · ");
}
/* הכרעת נדב 15/10: מהירות כרטיס ההפצה של מכונה (/machines.nic_speed) — ידנית / מדווחת / הנחה.
   הנחה נאמרת כהנחה, לעולם לא כמדידה. */
const NIC_SPEED_SOURCE = { manual: "הוגדר ידנית", reported: "מדווח" };
function nicSpeedText(n) {
  if (!n || typeof n.speed_mbps !== "number") return "לא ידוע";
  if (n.source === "assumed") return `לא ידוע — הונח ${linkSpeedText(n.speed_mbps)}`;
  return `${linkSpeedText(n.speed_mbps)} (${NIC_SPEED_SOURCE[n.source] || n.source})`;
}
function editNicSpeed(mac) {
  const m = findMachine(mac); if (!m || !isAdmin()) return;
  const cur = m.nic_speed && m.nic_speed.source === "manual" ? String(m.nic_speed.speed_mbps) : "auto";
  sheet({ title: "מהירות כרטיס רשת", sub: `${machineName(m) || m.mac} — תקרת הגל לא תעבור את המחשב האיטי בגל`,
    fields: [{ id: "nic_speed", type: "segment", label: "מהירות", value: cur,
      help: "אוטומטי = מה שהמחשב מדווח; כשאינו מדווח — מניחים 1Gb/s.",
      options: [["auto", "אוטומטי"], ["1000", "1Gb/s"], ["2500", "2.5Gb/s"], ["10000", "10Gb/s"]].map(([value, label]) => ({ value, label })) }],
    onSubmit: async (v) => { await put("/machines/" + encodeId(m.mac), { nic_speed_mbps: v.nic_speed === "auto" ? null : Number(v.nic_speed) }); await loadMachines(); openMachineDetail(m.mac); } });
}
/* ‏#1485 (נדב 16/10): מיפוי מגירה → חיבור לכל משכפל ומחשב בנייה (‏/machines[].drawer_slots).
   איבר i הוא "דיסק i+1": ‏sata:<port> (‏ataN מה-hello; על הלוח "SATA port-1") או nvme:<כתובת PCI
   של הבקר> — יציב בין אתחולים, לא nvme0n1. בלי השדה (שרת ישן) — המוסכמה המוצהרת: דיסק k =
   ‏sata:k עד drawer_count. כונן שהחיבור שלו אינו במיפוי: "SATA n (לא ממופה)" — לעולם לא מספר
   מנוחש. ‏port נשאר הזהות על החוט (‏target_slots), המיפוי הוא המספר שהמפעיל רואה. */
function connKey(d) { return d && d.port != null ? `sata:${Number(d.port)}` : d && d.pci ? `nvme:${d.pci}` : null; }
function connText(key) {
  const s = /^sata:(\d+)$/.exec(key || ""), n = /^nvme:(.+)$/.exec(key || "");
  return s ? `SATA ${Number(s[1]) - 1}` : n ? `NVMe ${n[1].replace(/^0000:/, "")}` : "חיבור לא מזוהה";
}
function drawerSlots(m) {
  if (m && Array.isArray(m.drawer_slots)) return m.drawer_slots;
  const n = m && m.drawer_count != null ? Number(m.drawer_count) : 0;
  return Array.from({ length: n }, (_, i) => `sata:${i + 1}`);
}
/* מספר הדיסק של כונן לפי המיפוי; ‏null = לא ממופה. */
function diskNumber(m, d) { const k = connKey(d), i = k ? drawerSlots(m).indexOf(k) : -1; return i >= 0 ? i + 1 : null; }
/* ‏#704: השם שהמפעיל נתן לחיבור (‏/machines[].slot_names). המפתח הוא החיבור —
   ‏sata:<port>, ולכונן בלי port (‏NVMe/USB) ‏serial:<serial> — כמו server/slot_names.py.
   חריץ ריק (d=null) הוא החיבור שהמיפוי נותן לדיסק n. בלי שם: "דיסק N". */
function slotKey(d) { return d.port != null ? `sata:${Number(d.port)}` : d.serial ? `serial:${d.serial}` : null; }
function slotName(m, n, d) {
  const names = (m && m.slot_names) || {}, has = (k) => !!k && Object.prototype.hasOwnProperty.call(names, k);
  /* ‏#2001: חלון "מיפוי חיבורים" נותן שם לחיבור — ‏nvme:<PCI> ל-NVMe; ‏serial: הוא שם ישן (#704). */
  const k = (d ? [connKey(d), slotKey(d)] : [n != null ? drawerSlots(m)[n - 1] : null]).find(has);
  return k ? names[k] : null;
}
function slotLabel(m, n, d) { return slotName(m, n, d) || `דיסק ${n}`; }
/* ‏#2001: "דיסק N" של כונן שידוע רק לפי הפורט (רשומות כיווץ, שאלת SMART, רצפת החדר) — השם
   כשהפורט ממופה; אחרת הטקסט הקודם ("דיסק <port>"), בלי ניחוש. */
function portLabel(m, port) {
  if (port == null) return "דיסק ?";
  const n = m ? drawerSlots(m).indexOf(`sata:${Number(port)}`) + 1 : 0;
  const d = m && Array.isArray(m.disks) ? m.disks.find((x) => x && Number(x.port) === Number(port)) : null;
  return n > 0 ? slotLabel(m, n, d || null) : `דיסק ${port}`;
}
/* "דיסק N" (או שם החריץ) של כונן, ו"SATA n (לא ממופה)" כשהחיבור שלו אינו במיפוי. */
function diskLabel(m, d) { const n = diskNumber(m, d); return n != null ? slotLabel(m, n, d) : rowLabel(m, { n: null, key: connKey(d), disk: d, optical: null }); }
/* שורות החריצים של מכונה: דיסק 1..N לפי המיפוי, אחריהם כוננים שאינם ממופים, ואחריהם כונן
   CD/DVD (‏/machines[].optical) — אף פעם לא חריץ דיסק, לא "ריק", לא מקור ולא יעד. ‏src = הכוננים
   (‏/machines.disks, או drawer_list של /room בסבב). */
function driveRows(m, src) {
  const disks = Array.isArray(src) ? src : [], optical = Array.isArray(m.optical) ? m.optical : [], used = new Set();
  const opticalAt = (key) => optical.find((o) => o && o.port != null && `sata:${Number(o.port)}` === key) || null;
  const rows = drawerSlots(m).map((key, i) => {
    const disk = disks.find((d) => connKey(d) === key) || null;
    if (disk) used.add(disk);
    return { n: i + 1, key, disk, optical: disk ? null : opticalAt(key) };
  });
  disks.forEach((d) => { if (!used.has(d)) rows.push({ n: null, key: connKey(d), disk: d, optical: null }); });
  optical.forEach((o) => { if (o && !rows.some((r) => r.optical === o)) rows.push({ n: null, key: o.port != null ? `sata:${Number(o.port)}` : null, disk: null, optical: o }); });
  return rows;
}
/* החיבור של שורה כטקסט. בלי מפתח (סוכן ישן: NVMe בלי pci, USB, בקר SCSI) — אומרים מה ידוע, לא ממציאים. */
function rowConn(r) {
  if (r.key) return connText(r.key);
  const d = r.disk || {};
  return /^nvme/.test(d.dev || "") ? "NVMe — אין כתובת PCI" : d.removable ? "USB" : "חיבור לא מזוהה";
}
function rowLabel(m, r) {
  if (r.optical) return "כונן CD/DVD";
  if (r.n != null) return slotLabel(m, r.n, r.disk);
  const name = r.disk ? slotName(m, null, r.disk) : null;   // #704: שם לכונן בלי חריץ (serial:) נשאר שלו
  return `${name || rowConn(r)} (לא ממופה)`;
}
/* #874: אדום לפי סידורי (הדיסק נודד) **וגם** לפי מכונה+חריץ (לפעמים הפורט אשם). */
function machineDiskFailures(m) {
  const serials = new Set((m.disks || []).map((d) => d.serial).filter(Boolean));
  return (DISK_FAILURES || []).filter((f) => (f.mac === m.mac && f.port != null) || (f.serial && serials.has(f.serial)));
}
/* מספר הדיסק של רשומת כשל לפי המיפוי; ‏null = החיבור אינו ממופה (או הדיסק לא כאן). */
function failureSlot(m, f) {
  if (f.mac === m.mac && f.port != null) { const i = drawerSlots(m).indexOf(`sata:${Number(f.port)}`); return i >= 0 ? i + 1 : null; }
  const d = (m.disks || []).find((x) => x.serial && x.serial === f.serial);
  return d ? diskNumber(m, d) : null;
}
function failureLabel(m, f) {
  const n = failureSlot(m, f);
  return n != null ? slotLabel(m, n, null) : f.port != null ? `SATA ${f.port - 1} (לא ממופה)` : "דיסק ?";
}
/* כשל שייך לשורה: אותו סידורי, או אותה מכונה ואותו חיבור. */
function rowFailure(m, r, fails) {
  return fails.find((f) => (r.disk && f.serial && f.serial === r.disk.serial)
    || (f.mac === m.mac && f.port != null && r.key === `sata:${Number(f.port)}`)) || null;
}
function failureCauseText(f) {
  const cause = FAILURE_CAUSE_HE[f.cause] || f.cause || "";
  if (f.cause === "cable" && f.port != null) return `${cause} SATA ${f.port - 1}`;
  return cause;
}
/* ‏#402: "0 דיסקים" הם שלושה ממצאים שהפעולה עליהם הפוכה, ו-`disk_probe` מה-hello (ממשק 2) מבחין:
   no_disks = יש פורטי SATA, לא חובר כונן (חברו כונן); no_ports = בקר ה-SATA מדווח 0 פורטים — מנוטרל
   בקושחה (געו בביוס, כבל לא יעזור); unchecked = לא נספר (אין שורת ahci ב-dmesg) ≠ no_disks (עיקרון 5);
   null = סוכן ישן שלא שלח — הטקסט הישן, לא ניחוש. */
const DISK_PROBE_HE = { no_disks: "לא חוברו דיסקים", no_ports: "אין פורטי SATA בקושחה", unchecked: "0 דיסקים · לא נבדק" };
function zeroDisksText(m) { return DISK_PROBE_HE[m.disk_probe] || "דיווח 0 דיסקים"; }
function disksCell(m) {
  if (m.disks == null) return `<span class="muted">לא דיווח</span>`;
  if (!m.disks.length) return UI.status("warn", zeroDisksText(m));
  const sizes = [...new Set(m.disks.map((d) => marketedDiskSize(d.size_bytes)))].map(ltr).join(" / ");
  const red = [...new Set(machineDiskFailures(m).map((f) => failureSlot(m, f)).filter((n) => n != null))].sort();
  return `${m.disks.length} · ${sizes}${red.map((n) => " " + UI.pill("warn", `${slotLabel(m, n, null)} נכשל בשיכפול הקודם`)).join("")}`;   // #1580: orange
}
function waitingText(m) { return "ממתין למפעיל: " + promptHe(m.prompt); }
/* המצב: נקודה צבעונית + מילים. ירוק רק על ראיה חיובית (השרת אמר "מחובר",
   הסבב הושלם); "לא נבדק"/"לא נקרא" אפורים. */
function machineState(m) {
  if (m.held) return { cls: "warn", text: HELD_HE, held: true };   // #2031: דלוק, לא "לא מחובר"
  if (m.prompt) return { cls: "warn", text: waitingText(m) };
  const role = machineRole(m), net = netFor(m.mac);
  if (role === "build" || role === "cloner") {
    const mon = monitorRow(m.mac);
    if (!mon) return { cls: "", text: MONITOR_ROWS ? "לא נבדק" : "לא נקרא" };
    return mon.online ? { cls: "ok", text: "מחובר" } : { cls: "", text: net && net.last_seen ? "לא מחובר" : "לא נראה" };
  }
  const member = sessionMember(m);
  if (member) {
    if (member.state === "failed") return { cls: "err", text: "נכשל בסבב" + (member.error ? " — " + member.error : "") };
    if (member.done) return { cls: member.error ? "warn" : "ok", text: member.error ? "הסבב הושלם עם אזהרה" : "הסבב הושלם" };
    return { cls: "run", text: "בסבב · " + (member.state === "waiting" ? "ממתין להתחלה" : Progress.view(member).label) };
  }
  if (groupSession(machineGroupId(m))) return { cls: "warn", text: "חסר בסבב" };
  if (net && net.last_seen) return { cls: "", text: "נראה " + ago(net.last_seen) };
  return { cls: "", text: NET ? "לא נראה" : "לא נקרא" };
}
function stale30(mac) {
  const net = netFor(mac);
  return !net || !net.last_seen || Date.now() - new Date(net.last_seen).getTime() > 30 * 86400e3;
}
function machineMatches(m) {
  const q = MCH.q.trim().toLowerCase();
  if (q) {
    const net = netFor(m.mac);
    const hay = [machineName(m), m.mac, net && net.ip, machineModel(m)].filter(Boolean).join(" ").toLowerCase();
    if (!hay.includes(q)) return false;
  }
  if (MCH.role && machineRole(m) !== MCH.role) return false;
  if (MCH.group && machineGroupId(m) !== MCH.group) return false;
  if (MCH.state === "ok" && machineState(m).cls !== "ok") return false;
  if (MCH.state === "warn" && !m.prompt) return false;
  if (MCH.state === "err" && !machineDiskFailures(m).length) return false;
  if (MCH.state === "stale" && !stale30(m.mac)) return false;
  return true;
}
function machinesScope() {
  const gid = MACHINES_CLASS || MACHINES_FILTER;
  if (gid) return (MACHINES || []).filter((m) => machineGroupId(m) === gid);
  if (MACHINES_ROLE) return (MACHINES || []).filter((m) => machineRole(m) === MACHINES_ROLE);
  const all = MACHINES || [];
  if (classroomsOn()) return all;
  return all.filter((m) => machineRole(m) !== "classroom");
}
function machinesVisible() { return machinesScope().filter(machineMatches); }
function groupOpen(gid) {
  const def = (MACHINES || []).length <= FOLD_ABOVE;
  return MCH.toggled.has(gid) ? !def : def;
}
function toggleMchGroup(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  if (MCH.toggled.has(gid)) MCH.toggled.delete(gid); else MCH.toggled.add(gid);
  redrawMachinesTable();
}
function redrawMachinesTable() {
  const el = document.getElementById("mch-table");
  if (el) el.innerHTML = machinesTableHtml();
  const n = document.getElementById("mch-count");
  if (n) n.textContent = machinesCountText();
}
function machinesCountText() {
  const shown = machinesVisible().length, scope = machinesScope().length;
  return shown === scope ? `${scope} מחשבים` : `${shown} מתוך ${scope} מחשבים`;
}
function mchFilter(key, value) { MCH[key] = value; redrawMachinesTable(); }

function machineRowHtml(m) {
  const macEnc = encodeId(m.mac), net = netFor(m.mac), st = machineState(m), role = machineRole(m);
  const name = machineName(m) || m.mac, sub = machineSub(m), sel = MCH.sel.has(m.mac);
  // ‏⋯ אחד לשורה (console-full.html MENUS.machine): פרטים, ובמנהל — שם / קבוצה / חריצים (שיכפול) / הסרה (חלון אישור).
  const items = [["פרטים", `openMachineDetail('${macEnc}')`]];
  if (isAdmin()) {
    items.push(["שינוי שם…", `renameMachine('${macEnc}')`], ["העבר לקבוצה…", `moveMachineSheet('${macEnc}')`]);
    if (role === "cloner" || role === "build") items.push(["חריצים וחיבורים…", `openMachineDetail('${macEnc}','disks')`]);   // #1485: בלשונית "דיסקים" של הפאנל
    items.push(["הסרה מהרישום…", `removeMachine('${macEnc}')`, "danger"]);
  }
  return { attrs: `data-mac="${esc(m.mac)}"${sel ? ' class="sel"' : ""}`, cells: [
    `<input type="checkbox" aria-label="בחר ${esc(name)}"${sel ? " checked" : ""} onchange="toggleMchSel('${macEnc}',this.checked)">`,
    `<a class="name" role="link" tabindex="0" onclick="openMachineDetail('${macEnc}')">${esc(name)}</a>${sub ? `<span class="sub">${esc(sub)}</span>` : ""}`,
    `<span class="mono">${esc(m.mac)}</span>`,
    net && net.ip ? `<span class="mono">${esc(net.ip)}</span>` : `<span class="muted">—</span>`,
    net && net.last_seen ? esc(ago(net.last_seen)) : `<span class="muted">${NET ? "מעולם לא" : "לא נקרא"}</span>`,
    disksCell(m), UI.status(st.cls, st.text), UI.rowMenu(name, items)] };
}
/* שורת הקבוצה — roll-up: שם (כיתה → אובייקט), מונה, מחוברים/סבב רק כשיש נתון, פעולות. */
function groupRowHtml(g, kids) {
  const open = groupOpen(g.id), gidEnc = encodeId(g.id), fixed = g.role !== "classroom";
  const arrow = `<button type="button" class="btn sm flat" aria-expanded="${open}" aria-label="${open ? "קפל" : "פתח"} ${esc(g.label)}" onclick="toggleMchGroup('${gidEnc}')">${open ? "▾" : "▸"}</button>`;
  const title = `<a role="link" tabindex="0" onclick="openClass('${gidEnc}')">${esc(g.label)}</a>`;
  const s = groupSession(g.id);
  const meta = [fixed ? "קבוצה קבועה" : "כיתה", `${kids.length} ${kids.length === 1 ? "מחשב" : "מחשבים"}`];
  if (s && s.prefix) meta.push(`קידומת ${s.prefix}`);
  let live = "";
  if (fixed && MONITOR_ROWS) {
    const on = kids.filter((m) => (monitorRow(m.mac) || {}).online).length;
    live += UI.status(on ? "ok" : "", `${on} מחוברים`);
  }
  if (s) live += UI.status(s.state === "failed" ? "err" : "run", `סבב ${sessionStateText(s)} — ${sessionImage(s)}, ${s.joined || 0}/${sessionExpected(s)}`);
  const acts = fixed
    ? [["פתח", `openClass('${gidEnc}')`], ["+ מחשב", `openAddMachine({group:'${gidEnc}'})`]]
    : [["פתח כיתה", `openClass('${gidEnc}')`], ["+ מחשב לכיתה", `openAddMachine({group:'${gidEnc}'})`]];
  if (g.role === "cloner") acts.push(["WoL לחדר", "wakeRoom()"]);
  if (g.role === "build") acts.push(["WoL לבנייה", `wakeGroup('${gidEnc}')`]);   // ‏#984
  return { html: `<tr class="group" data-group="${esc(g.id)}"><td colspan="8"><div class="grp">${arrow}${title}<span class="muted">${esc(meta.join(" · "))}</span>${live}<span class="sp"></span>${UI.acts(acts)}</div></td></tr>` };
}
function machinesTableHtml() {
  if (!(MACHINES || []).length) {
    const addArgs = MACHINES_ROLE === "build" || MACHINES_ROLE === "cloner" ? `{role:'${MACHINES_ROLE}'}` : "{}";
    return UI.empty("אין מחשבים רשומים — הוסיפו את הראשון, או הדביקו רשימת MAC.",
      isAdmin() ? `<button class="btn primary" onclick="openAddMachine(${addArgs})">+ מחשב</button>` : "");
  }
  const list = machinesVisible();
  const gid = MACHINES_CLASS || MACHINES_FILTER;
  if (!list.length) {
    const filtered = MCH.q || MCH.role || MCH.group || MCH.state;
    return UI.empty(filtered ? "אין מחשבים שתואמים לסינון"
      : gid ? `אין מחשבים בקבוצה ${groupLabel(gid)} (${MACHINES.length} רשומים בסך הכול)` : "אין מחשבים רשומים");
  }
  const columns = [{ html: `<input type="checkbox" aria-label="בחר את כל המוצגים" onchange="toggleMchSelAll(this.checked)">` },
    "שם", "MAC", "IP אחרון", "נראה לאחרונה", "דיסקים", "מצב", { html: `<span class="sr">פעולות</span>` }];
  let rows;
  if (gid) rows = sortMachinesBySuffix(list).map(machineRowHtml);
  else {
    rows = [];
    const groups = machineGroupsInOrder();
    const orphan = list.filter((m) => !groups.some((g) => g.id === machineGroupId(m)));
    for (const g of groups.concat(orphan.length ? [{ id: "", label: "ללא קבוצה", role: "unknown" }] : [])) {
      const kids = sortMachinesBySuffix(g.id ? list.filter((m) => machineGroupId(m) === g.id) : orphan);
      if (!kids.length) continue;
      rows.push(groupRowHtml(g, kids));
      if (groupOpen(g.id)) rows.push(...kids.map(machineRowHtml));
    }
  }
  return UI.datagrid({ columns, rows, cls: "kebab-col", opt: [2, 4] }) + mchSelBar(list);
}
function mchSelBar(list) {
  const n = list.filter((m) => MCH.sel.has(m.mac)).length;
  if (!n) return "";
  return `<div class="dg-bar foot"><span class="n">נבחרו ${n} מתוך ${list.length}</span><span class="sp"></span><button class="btn sm" onclick="bulkMoveMachines()">העבר לקבוצה…</button><button class="btn sm danger" onclick="bulkRemoveMachines()">הסרה</button><button class="btn sm" onclick="mchSelClear()">נקה בחירה</button></div>`;
}
function toggleMchSel(macEnc, on) {
  let mac = macEnc; try { mac = decodeURIComponent(macEnc); } catch (e) {}
  if (on) MCH.sel.add(mac); else MCH.sel.delete(mac);
  redrawMachinesTable();
}
function toggleMchSelAll(on) {
  for (const m of machinesVisible()) { if (on) MCH.sel.add(m.mac); else MCH.sel.delete(m.mac); }
  redrawMachinesTable();
}
function mchSelClear() { MCH.sel.clear(); redrawMachinesTable(); }

function machinesBar(scoped) {
  const opt = (value, label, cur) => `<option value="${esc(value)}"${value === cur ? " selected" : ""}>${esc(label)}</option>`;
  const scopeSelects = scoped ? "" :
    `<select aria-label="תפקיד" onchange="mchFilter('role',this.value)">${opt("", "כל התפקידים", MCH.role)}${(classroomsOn() ? ["classroom", "build", "cloner"] : ["build", "cloner"]).map((r) => opt(r, ROLE_GROUP_HE[r], MCH.role)).join("")}</select>`
    + `<select aria-label="קבוצה" onchange="mchFilter('group',this.value)">${opt("", "כל הקבוצות", MCH.group)}${machineGroupsInOrder().map((g) => opt(g.id, g.label, MCH.group)).join("")}</select>`;
  const states = [["", "כל המצבים"], ["ok", "מחובר עכשיו"], ["warn", "ממתין למפעיל"], ["err", "נכשל בשיכפול הקודם"], ["stale", "לא נראה 30 יום"]];
  return `<div class="dg-bar"><input type="search" aria-label="חיפוש מחשב" placeholder="חיפוש: שם, MAC, IP…" value="${esc(MCH.q)}" oninput="mchFilter('q',this.value)" style="width:220px">${scopeSelects}<select aria-label="מצב" onchange="mchFilter('state',this.value)">${states.map(([v, l]) => opt(v, l, MCH.state)).join("")}</select><span class="sp"></span><span class="n" id="mch-count">${esc(machinesCountText())}</span></div>`;
}
/* הכרטיס עם הטבלה — בדף "מחשבים" (קבוצות) ובאובייקט הקבוצה (שטוח). */
function machinesTableCard(g = null, cls = "c12") {
  const gidEnc = g ? encodeId(g.id) : "";
  const foot = g && isAdmin()
    ? `<div class="dg-bar foot"><span class="n">${g.role === "classroom" ? "הוספה: מספר 01–99 או INS · " : ""}ייבוא בהדבקה (MAC + ${g.role === "classroom" ? "סיומת" : "שם"} בכל שורה)</span><span class="sp"></span><button class="btn sm" onclick="openAddMachine({group:'${gidEnc}'})">+ מחשב</button><button class="btn sm" onclick="openAddMachine({group:'${gidEnc}',tab:1})">ייבוא בהדבקה</button><a class="btn sm" href="/api/console/machines.csv" download>ייצוא CSV</a></div>`
    : "";
  const strip = !g && MACHINES_FILTER
    ? `<div class="c12" role="status">${UI.note("info", `מוצגת קבוצה: <b>${esc(groupLabel(MACHINES_FILTER))}</b> · ${UI.link("הצג את כל המחשבים", "clearMachinesFilter()")}`)}</div>` : "";
  const title = g ? (g.role === "classroom" ? "המחשבים בכיתה" : "המחשבים בקבוצה") : MACHINES_FILTER ? groupLabel(MACHINES_FILTER) : "כל המחשבים";
  const small = g ? 'אותה טבלה כמו ב"מחשבים", מסוננת' : (classroomsOn() ? "בנייה ושיכפול = קבוצה קבועה · כל כיתה = קבוצה משלה" : "בנייה ושיכפול = קבוצה קבועה");
  return strip + UI.card({ title, small, cls, flush: true, body: machinesBar(!!g || !!MACHINES_FILTER) + `<div id="mch-table">${machinesTableHtml()}</div>` + foot });
}
function machinesTabs() {
  const unreg = NET ? NET.filter((d) => !d.registered).length : null;
  const red = DISK_FAILURES ? `דיסקים אדומים (${DISK_FAILURES.length})` : "דיסקים אדומים";
  if (MACHINES_ROLE) return [ROLE_PAGE_HE[MACHINES_ROLE], red];   // בלי "נראו ברשת"
  return ["כל המחשבים", unreg == null ? "נראו ברשת" : `נראו ברשת (${unreg})`, red];
}
function machines(tab = 0) {
  if (!classroomsOn()) {
    if (MACHINES_ROLE === "classroom") MACHINES_ROLE = null;
    if (MACHINES_CLASS) {
      const cg = (GROUPS || []).find((x) => x.id === MACHINES_CLASS);
      if (cg && cg.role === "classroom") MACHINES_CLASS = null;
    }
  }
  const crumbs = [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "מלאי" }, { label: "מחשבים" }];
  if (!MACHINES || !GROUPS) {
    return `<div class="page">${UI.objHeader({ crumbs, icon: "t-machines", name: "מחשבים", tabs: machinesTabs(), tab })}<div class="body"><div class="c12">${pagePlaceholder()}</div></div></div>`;
  }
  if (MACHINES_CLASS) {
    const g = GROUPS.find((x) => x.id === MACHINES_CLASS);
    if (g) return groupPage(g, tab);
    MACHINES_CLASS = null;
  }
  /* ‏#1485: העלים "מחשבי בנייה" / "מחשבי שיכפול" בעץ הם הדף של הקבוצה הקבועה (P.build / P.cloners). */
  const fixedGroup = (MACHINES_ROLE === "build" || MACHINES_ROLE === "cloner") ? GROUPS.find((g) => g.role === MACHINES_ROLE) : null;
  if (fixedGroup) return groupPage(fixedGroup, 0);
  const all = machinesScope(), count = (role) => all.filter((m) => machineRole(m) === role).length;
  const classN = classroomsOn() ? GROUPS.filter((g) => g.role === "classroom").length : 0;
  const sub = MACHINES_ROLE
    ? [`${all.length} רשומים`, MACHINES_ROLE === "classroom" ? `${classN} כיתות` : ""].filter(Boolean)
    : [`${all.length} רשומים`, classroomsOn() ? `${classN} כיתות` : "", `${count("build")} מחשבי בנייה`, `${count("cloner")} משכפלים`].filter(Boolean);
  if (NET) {
    const macs = new Set(all.map((m) => m.mac));
    sub.push(`${NET.filter((d) => d.registered && isToday(d.last_seen) && (!MACHINES_ROLE || macs.has(d.mac))).length} נראו ברשת היום`);
  }
  const red = (DISK_FAILURES || []).length, unreg = NET ? NET.filter((d) => !d.registered).length : 0;
  const pill = (red ? UI.pill("warn", `${red} ${red === 1 ? "דיסק נכשל בשיכפול הקודם" : "דיסקים נכשלו בשיכפול הקודם"}`) : "")   // #1580: orange
    + (unreg ? UI.pill("warn", `${unreg} ${unreg === 1 ? "לא רשום" : "לא רשומים"}`) : "");
  const addRole = MACHINES_ROLE === "build" || MACHINES_ROLE === "cloner" ? `role:'${MACHINES_ROLE}'` : "";
  const actions = (isAdmin()
    ? `<button class="btn primary" onclick="openAddMachine({${addRole}})">+ מחשב</button>${!MACHINES_ROLE && classroomsOn() ? `<button class="btn" onclick="addGroupSheet()">+ כיתה</button>` : ""}${MACHINES_ROLE ? "" : `<button class="btn" onclick="openAddMachine({${addRole}${addRole ? "," : ""}tab:1})">ייבוא בהדבקה</button><a class="btn" href="/api/console/machines.csv" download>ייצוא CSV</a>`}`
    : "") + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const name = MACHINES_ROLE ? ROLE_PAGE_HE[MACHINES_ROLE] : "מחשבים";
  const header = UI.objHeader({ crumbs, icon: "machine", name, sub: esc(sub.join(" · ")), pill, actions, tabs: machinesTabs(), tab });
  const redTab = MACHINES_ROLE ? 1 : 2;
  const body = (!MACHINES_ROLE && tab === 1) ? seenDevicesCard() : tab === redTab ? diskFailuresCard() + shrinkRecordsCard() : machinesTableCard();
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}
function clearMachinesFilter() {
  MACHINES_FILTER = null;
  MACHINES_CLASS = null;
  MACHINES_ROLE = null;
  if (current === "machines") renderCurrent();
}
function openMachinesPage() { MACHINES_FILTER = null; MACHINES_CLASS = null; MACHINES_ROLE = null; selectPageById("machines"); }
/* צומת-אב בעץ: אותו דף מחשבים, ממוקד לתפקיד אחד (נדב 17/09: "חלון כמו של המחשבים
   אבל רק של כיתות / שיכפול / בנייה, בלי לשונית נראו ברשת"). */
function selectMachinesRole(role, el) {
  if (role === "classroom" && !classroomsOn()) return;
  MACHINES_ROLE = role; MACHINES_FILTER = null; MACHINES_CLASS = null; MCH.sel.clear();
  // ‏#1300 (console-full.html: "מחשבי בנייה" / "מחשבי שיכפול" הם עלים עם דף משלהם) — העלה עצמו מודגש
  if (el) selectPage(el, "machines"); else selectPageById("machines");
}
/* לחיצה על קבוצה בעץ: כל קבוצה → האובייקט שלה (כיתה — גל 3; בנייה/שיכפול — גל 3א). קבוצה
   שאינה רשומה ב-/groups → הטבלה מסוננת (#916). */
function selectMachinesGroup(groupId) {
  try { groupId = decodeURIComponent(groupId); } catch (e) {}
  const g = (GROUPS || []).find((x) => x.id === groupId);
  MACHINES_ROLE = null;
  if (g) { MACHINES_CLASS = groupId; MACHINES_FILTER = null; }
  else { MACHINES_FILTER = groupId; MACHINES_CLASS = null; }
  MCH.sel.clear();
  selectPageById("machines");
}
function openClass(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  MACHINES_CLASS = gid; MACHINES_FILTER = null; MACHINES_ROLE = null; MCH.sel.clear();
  selectPageById("machines");
}

/* ---------- הקבוצה כאובייקט (class.md, cloners.md, builders.md) — groupPage מקבל קבוצה, והתוכן לפי role ---------- */
function groupTabs(g) {
  if (g.role === "classroom") return ["סיכום", "מחשבים", "סבבים"];
  if (g.role === "cloner" || g.role === "build") return [];   // #1485: P.cloners / P.build — דף אחד בלי לשוניות
  return ["סיכום", "מחשבים"];
}
function groupKpis(g, kids) {
  const s = groupSession(g.id);
  const seen = NET ? kids.filter((m) => { const n = netFor(m.mac); return n && n.last_seen && isToday(n.last_seen); }).length : null;
  const red = kids.reduce((n, m) => n + machineDiskFailures(m).length, 0);
  const reported = kids.filter((m) => Array.isArray(m.disks)).length;
  const waiting = kids.filter((m) => m.prompt), failed = s ? (s.members || []).filter((x) => x.state === "failed") : [];
  const care = waiting.length + red + failed.length;
  const careSub = [waiting.length ? `${waiting.length} ממתינים למפעיל` : "", red ? `${red} דיסקים אדומים` : "", failed.length ? `${failed.length} נכשלו בסבב` : ""].filter(Boolean).join(" · ");
  const disks = DISK_FAILURES == null ? UI.kpi({ cls: "", label: "דיסקים", value: "לא נקרא", sub: "רשימת הכשלים לא נטענה" })
    : red ? UI.kpi({ cls: "err", label: "דיסקים", value: red, unit: "אדומים", sub: esc(kids.filter((m) => machineDiskFailures(m).length).map(machineName).join(", ")) })
    : UI.kpi({ cls: reported ? "ok" : "", label: "דיסקים", value: reported ? "אין אדומים" : "לא דווחו", sub: `${reported} מתוך ${kids.length} דיווחו על דיסקים` });
  return UI.kpi({ cls: "", label: "מחשבים", value: kids.length, sub: seen == null ? "הרשת לא נקראה" : `${seen} נראו ברשת היום` })
    + (s ? UI.kpi({ cls: s.state === "failed" ? "err" : "info", label: s.state === "failed" ? "סבב נכשל" : "סבב פעיל", value: s.joined || 0, unit: `/ ${sessionExpected(s)}`, sub: `${esc(sessionImage(s))} · ${esc(sessionStateText(s))} · ${UI.link("לסבב", "selectPageById('deploy')")}` })
         : UI.kpi({ cls: "", label: "סבב פעיל", value: "אין", sub: g.role === "classroom" && classroomsOn() ? UI.link("הפץ לכיתה…", `deployToGroup('${encodeId(g.id)}')`) : "" }))
    + disks
    + UI.kpi({ cls: care ? "warn" : "", label: "דורש טיפול", value: care, sub: careSub || "אין ממתינים, אדומים או כשלים" });
}
/* ‏#980 §3: סבבי החדר שהסתיימו, מ-GET /room/history (מהחדש לישן). null = לא נקרא ≠ [] אין סבבים.
   ירוק רק כשנכתבו כל הכוננים שהוצהרו (ראיה מהשרת); סבב שנסגר לפני היעד — אפור. */
/* ‏stage 3 (console-full.html P.deploy "היסטוריה"): טבלה — אימג', נכתבו, משך, תוצאה (פיל), נפתח ע"י, תאריך.
   משך = closed_at − created_at, ו-"—" כשאחד מהם חסר (לא נמדד ≠ 0). תוצאה: נכשל (עם הסיבה) / הצליח כשנכתבו כל היעדים /
   "N מתוך M" כשנסגר לפני היעד. */
function roomRoundMinutes(h) {
  const s = (new Date(h.closed_at).getTime() - new Date(h.created_at).getTime()) / 1000;
  if (!h.created_at || !h.closed_at || !Number.isFinite(s) || s < 0) return "—";
  return s < 60 ? "פחות מדקה" : `${Math.round(s / 60)} דק'`;
}
/* ‏#1889: כמה זמן הסבב חיכה לאדם (‏h.waits) — שורת משנה בעמודת "מצב". פחות מדקה — לא מוצג; ‏null — לא נמדד. */
function roomRoundWaits(h) {
  const w = h.waits || {};
  const parts = [];
  if (w.smart_hold_s >= 60) parts.push(`חיכה ${Math.round(w.smart_hold_s / 60)} דק' לתשובות SMART`);
  if (w.smart_unreleased > 0) parts.push("נסגר בזמן המתנה לתשובת SMART");
  return parts.length ? `<span class="sub">${esc(parts.join(" · "))}</span>` : "";
}
function roomRoundResult(h) {
  if (h.state === "failed") return UI.pill("err", "נכשל") + `<span class="sub">${esc(h.failed_reason || "סיבה לא ידועה")}</span>`;
  if (h.target_drives > 0 && h.written_drives >= h.target_drives) return UI.pill("ok", "הצליח");
  return UI.pill("warn", `${h.written_drives} מתוך ${h.target_drives}`);
}
function roomHistoryHtml() {
  if (ROOM_HISTORY == null) return UI.note("warn", "היסטוריית הסבבים לא נקראה");
  const active = roomRound();
  if (!ROOM_HISTORY.length && !active) return UI.note("info", "אין סבבים קודמים בחדר");
  const rounds = active ? [active, ...ROOM_HISTORY.filter((h) => h.id !== active.id)] : ROOM_HISTORY;
  return UI.datagrid({ opt: [5, 6], columns: ["#", "אימג׳", "מחשבים", "נכתבו", "משך", "מצב", "תאריך"],
    rows: rounds.map((h, i) => [String(h.wave_number || rounds.length - i), esc(h.image_name || h.image_id || ""), String(h.machine_count ?? h.machines ?? "—"),
      `${h.written_drives || 0}/${h.target_drives || 0}`, active && h === active ? "פעיל" : esc(roomRoundMinutes(h)), active && h === active ? UI.pill("info", WAVE_HE[h.wave_state] || "פעיל") : roomRoundResult(h) + roomRoundWaits(h),
      `<span class="mono">${esc(h.created_at ? (isToday(h.created_at) ? fmtClock(h.created_at) : fmtDate(h.created_at)) : "—")}</span>`]) });
}
/* ציר הסבבים של קבוצה: הסבב הפעיל (/overview.session) + ההיסטוריה ("דורש API"). סבבי חדר המשכפלים —
   בדף ההפצה (roomHistoryHtml); דף המשכפלים לפי המוקאפ אינו מציג אותם (#1485). */
function groupRoundsCard(g, cls) {
  const s = groupSession(g.id);
  const events = [];
  if (s) events.push({ t: "עכשיו", cls: s.state === "failed" ? "err" : "info", text: `${esc(sessionImage(s))} · ${esc(sessionStateText(s))}, ${s.joined || 0}/${sessionExpected(s)} הצטרפו · ${UI.link("לסבב", "selectPageById('deploy')")}`, who: "" });
  const now = events.length ? UI.timeline(events) + `<div style="margin-top:12px"></div>` : "";
  const what = g.role === "classroom" ? "לכיתה" : "לקבוצה";
  const note = UI.note("info", `היסטוריית סבבים ${what} — <b title="אין endpoint לסבבים סגורים לפי קבוצה">דורש API</b> (היום רק ${UI.link("ביומן", "selectPageById('logs')")}).`);
  return UI.card({ title: g.role === "classroom" ? "סבבים של הכיתה" : "סבבים", cls, body: now + note });
}
/* הכיתה (class.md): KPI, הטבלה שטוחה, סבבים. */
function classView(g, kids, tab) {
  const gidEnc = encodeId(g.id), s = groupSession(g.id), classroom = g.role === "classroom";
  const seen = NET ? kids.filter((m) => { const n = netFor(m.mac); return n && n.last_seen && isToday(n.last_seen); }).length : null;
  const sub = [classroom ? "קבוצת כיתה" : "קבוצה קבועה", s && s.prefix ? `קידומת ${s.prefix}` : "", `${kids.length} ${kids.length === 1 ? "מחשב" : "מחשבים"}`, seen == null ? "" : `${seen} נראו היום`].filter(Boolean).join(" · ");
  const pill = s ? UI.pill(s.state === "failed" ? "err" : "info", `סבב ${sessionStateText(s)}`) : "";
  const actions = isAdmin() ? (classroom && classroomsOn() ? `<button class="btn primary" onclick="deployToGroup('${gidEnc}')">הפץ לכיתה…</button>` : "")
    + `<button class="btn" onclick="openAddMachine({group:'${gidEnc}'})">+ מחשב${classroom ? " לכיתה" : ""}</button>`
    + (classroom ? `<button class="btn" onclick="renameGroup('${gidEnc}')">שינוי שם</button><button class="btn danger" onclick="deleteGroup('${gidEnc}')">מחיקה</button>` : "") : "";
  let body;
  if (tab === 1) body = machinesTableCard(g);
  else if (tab === 2) body = groupRoundsCard(g, "c12");
  else body = `<div class="c12 kpis">${groupKpis(g, kids)}</div>` + machinesTableCard(g, "c8") + groupRoundsCard(g, "c4");
  return { sub, pill, actions, body };
}
function groupPage(g, tab = 0) {
  const kids = (MACHINES || []).filter((m) => machineGroupId(m) === g.id);
  const view = g.role === "cloner" ? clonersView(g, kids) : g.role === "build" ? buildersView(g, kids) : classView(g, kids, tab);
  /* ‏#1485: הקבוצות הקבועות הן הדפים "מחשבי בנייה" / "מחשבי שיכפול" של המוקאפ — אותו שם ושביל כמו העלה בעץ. */
  const fixed = g.role === "build" || g.role === "cloner", name = fixed ? ROLE_PAGE_HE[g.role] : g.label;
  const header = UI.objHeader({
    crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "מלאי" }, { label: "מחשבים", onclick: "openMachinesPage()" }].concat(fixed ? [{ label: name }] : [{ label: ROLE_GROUP_HE[g.role] || "קבוצות" }, { label: g.label }]),
    icon: g.role === "classroom" ? "group" : g.role === "build" ? "build" : "copy", name, sub: esc(view.sub), pill: view.pill,
    actions: view.actions + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`, tabs: groupTabs(g), tab });
  return `<div class="page">${header}<div class="body">${view.body}</div></div>`;
}
function deployToGroup(gidEnc) {
  if (!classroomsOn() || !roomOperator()) return;
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  if (roomRound() || (OVERVIEW && OVERVIEW.session)) { roundBusyNote(); return; }
  selectPageById("deploy");
  toast(`סבב כיתה ל"${groupLabel(gid)}" נפתח מדף ההפצה`);
}
async function wakeRoom() {
  try {
    const r = await post("/room/wake");
    toast(`WoL נשלח ל-${r.sent} מחשבים${r.failed ? `, ${r.failed} נכשלו` : ""}${(r.reasons || []).length ? " — " + r.reasons.join("; ") : ""}`, 6000);
  } catch (e) { toast("WoL נכשל: " + e.message); }
}

/* ---------- #954 גל 3א: מחשבי שיכפול (חדר השיכפולים) ומחשבי בנייה — הקבוצות הקבועות כאובייקטים ----------
   נבנה לפי docs/design/console-redesign/cloners.md ו-builders.md (מוקאפים מאושרים 17/09),
   על groupPage של גל 3. מקורות: /groups, /machines (drawer_count, disks, inventory, prompt),
   /room (round + machines[].drawer_list — מצב כל מגירה בזמן סבב), /disk-failures
   (#874/#890), /tasks (קליטות), /monitor/machines ("מחובר"), /net (IP, נראה).
   חריץ = "דיסק N · SATA N-1" (מוסכמת נדב: SATA 0 → דיסק 1; לעולם לא sd*). צבע:
   ירוק רק על SMART `ok` או "נכתב"; "לא נבדק" אפור, לא ירוק (עיקרון 5). כיבוי כולם
   (§1), היסטוריית סבבים (§3), שלבי הקליטה (§4, ‏/tasks.stage), מצב הדיסק לפני
   קליטה (§6, ‏/machines.last_capture + probe.ntfs), WoL למחשב (#984) ותיקיית
   הקליטה (#968) — מה-API. */
let ROOM = null;                          // ‏GET /room — null = לא נקרא (≠ חדר בלי סבב)
let ROOM_HISTORY = null;                  // ‏GET /room/history (#980 §3) — null = לא נקרא (≠ [] אין סבבים קודמים)
let ROOM_KEY = "";                        // חתימת התשובה האחרונה — רינדור מחדש רק על שינוי
let CAPTURE_TASKS_READ = false;           // ‏/tasks נקרא לפחות פעם אחת (‏[] ריק ≠ לא נקרא)
/* נדב 17/10: אין אנגלית על המסך. ‏`error` של מגירה מגיע מהסוכן כמחרוזת גולמית, והגולמי נשאר ב-tooltip (diskTitle).
   רוב הסיבות כבר בעברית; האנגלית שנשארה היא (א) הסיבות של `fanout.c` — ‏`fail_target` ו-`t->reason` — שעוטפות
   `_write_fail` (verdict.sh: "partition N: … (fanout: <סיבה>; partclone rc=N; <שורת partclone>)") ו-lc_judge (lccopy.sh);
   (ב) שגיאות מערכת-הפעלה שמגיעות כמות שהן מ-dd (writecheck.sh) ומשורת היומן האחרונה של partclone; (ג) שברי lcsnap.sh.
   הרשימה המלאה ומקורותיה: tests/drawer_error_he.test.cjs (שנופל אם נוספה סיבה ל-fanout.c בלי מיפוי).
   ‏DRAWER_ERROR_HE = ביטוי באנגלית (בגבולות מילה) → עברית; ‏DRAWER_ERROR_RE = סיבות עם מספר או שם בתוכן. שני הניסוחים שחופפים
   ל-`fail_reason_he` של מסך הכשל בסוכן (#1605) — שגיאת I/O ומקום בדיסק — זהים לו. מה שלא מופה — נחתך, ולא מוצג באנגלית. */
const DRAWER_ERROR_HE = {
  "queue overflow: target lost bytes": "הדיסק לא עמד בקצב — אבדו בייטים",
  "write error": "שגיאת כתיבה לדיסק",
  "stalled with no write progress": "הדיסק לא עמד בקצב — הכתיבה נתקעה והוא הוצא מהסבב",
  "stalled the stream (too slow to drain)": "הדיסק לא עמד בקצב — הוא עיכב את הזרם והוצא מהסבב",   // #1796
  "timed out waiting for the writer pipeline": "פג הזמן — צינור הכתיבה של המגירה לא נפתח",
  "could not open": "לא ניתן לפתוח את המגירה לכתיבה",
  "internal error (queue capacity invariant)": "תקלה פנימית בסוכן (תור הכתיבה) — לא תקלת דיסק",
  "empty stream": "הזרם ריק — לא התקבל אף בייט",
  "fsync failed": "שטיפת המאגר לדיסק נכשלה — ייתכן שחלק מהנתונים לא נשמר",
  "close failed": "סגירת הדיסק נכשלה — ייתכן שהבייטים האחרונים אבדו",
  "unknown": "סיבה לא ידועה",
  "dmsetup ls failed -- the views could not be checked": "לא ניתן היה לבדוק את העותקים בזיכרון",
  "rmmod brd failed": "פריקת דיסק הזיכרון נכשלה",
  "brd is still loaded": "דיסק הזיכרון עדיין טעון",
  "Input/output error": "שגיאת קריאה/כתיבה בדיסק",
  "No space left on device": "אין מספיק מקום בדיסק",
  "No such file or directory": "ההתקן לא נמצא",
  "Read-only file system": "הדיסק במצב קריאה בלבד",
  "Permission denied": "אין הרשאה לכתוב להתקן",
  "Device or resource busy": "ההתקן תפוס",
  "Invalid argument": "פרמטר לא תקין",
};
/* הארוך קודם: "internal error (…)" לפני כל שבר שהוא מכיל. גבול מילה לפני ואחרי — "ok" לא נתפס בתוך מילה. */
const DRAWER_ERROR_KEYS = Object.keys(DRAWER_ERROR_HE).sort((a, b) => b.length - a.length)
  .map((k) => [new RegExp("\\b" + k.replace(/[.*+?^$()|[\]\\/]/g, "\\$&") + "(?![A-Za-z])", "g"), DRAWER_ERROR_HE[k]]);
const DRAWER_ERROR_RE = [
  [/\b(short|long) stream \(got (\d+) of (\d+)\)/g, (_, k, a, b) => `הזרם ${k === "short" ? "קצר" : "ארוך"} מהצפוי — התקבלו ${a} מתוך ${b} בייט`],
  [/\bdmsetup remove \S+ failed/g, () => "הסרת עותק מהזיכרון נכשלה"],
  [/\b\S+ is still listed/g, () => "עותק בזיכרון עדיין קיים"],
  [/\bfanout: ok(?![A-Za-z])/g, () => "ההפצה הסתיימה בהצלחה, אך הכתיבה לדיסק נכשלה"],   // verdict.sh: fanout אמר ok, ו-partclone/zstd לא
  [/\bfanout: /g, () => ""],
  [/\b(?:dd|sgdisk|partclone): (?=[A-Za-z])/g, () => ""],   // שם הכלי שפותח הודעה באנגלית ("dd: error writing …")
  [/\bpartition (\d+)/g, (_, n) => `מחיצה ${n}`],
];
/* שמות כלים וקיצורים שהסוכן עצמו כותב בתוך משפט עברי (מאז #1503) — לא "אנגלית". */
const DRAWER_TECH = /\b(?:partclone|zstd|sgdisk|fanout|mkswap|mkfifo|ntfsresize|ntfsfix|ntfs|btrfs|xfs|dd|cmp|umount|blockdev|efibootmgr|openssl|swap|rc|dirty|gpt|esp|guid|smart|uefi|bios|secure boot|windows|linux|mib|gib|gb|mb|udp-sender|dsend|dfanout|dzstd|dpcl)\b/gi;
/* אנגלית = מילה לטינית (2+ אותיות) שאינה כלי מהרשימה ואינה מזהה (מזהה = יש בו ספרה, נקודה, קו נטוי או קו תחתון, או גיבוב הקסדצימלי). */
function drawerEnglish(piece) {
  return /[A-Za-z]{2,}/.test(piece.replace(/[A-Za-z0-9_./-]*[0-9_./][A-Za-z0-9_./-]*|\b[0-9a-f]{8,}\b/gi, " ").replace(DRAWER_TECH, " "));
}
function drawerErrorText(raw) {
  if (!raw) return "נכשל בכתיבה";
  let t = String(raw);
  for (const [re, he] of DRAWER_ERROR_KEYS) t = t.replace(re, he);
  for (const [re, fn] of DRAWER_ERROR_RE) t = t.replace(re, fn);
  /* מה שנשאר: חותכים כל קטע (בין מפרידים) שיש בו אנגלית, ואת המפריד שלפניו; סוגריים שנשארו לא מאוזנים — מתוקנים. */
  const bits = t.split(/(\s·\s|;\s|:\s|\s—\s|[()])/);
  let out = "", dropped = false;
  for (let i = 0; i < bits.length; i += 2) {
    if (drawerEnglish(bits[i])) { dropped = true; continue; }
    out += (out ? bits[i - 1] : "") + bits[i];   // המפריד שלפני קטע שנחתך (או לפני הראשון שנשאר) לא נשאר תלוי
  }
  let d = 0, bal = "";
  for (const c of out) { if (c === ")" && !d) continue; d += c === "(" ? 1 : c === ")" ? -1 : 0; bal += c; }
  out = (bal + ")".repeat(d)).replace(/\(\s*\)/g, "").replace(/\s+/g, " ").trim();
  if (!/[א-ת]/.test(out)) return "הכתיבה נכשלה — הפרטים בריחוף על הדיסק";
  return dropped ? `${out} — הפרטים בריחוף על הדיסק` : out;
}
const DRAWER_STATE_HE = { writing: "כותב", done: "נכתב", failed: "נכשל", verifying: "מאמת", waiting: "ממתין", cancelled: "בוטל",
  skipped: "דולג", replacing: "ממתין להחלפה", asking: "ממתין להכרעת המפעיל" };   // #1386; #1861: every state the agent's target_set writes (smartgate.sh, drawergate.sh)
/* ‏#1805: הקריאה החוזרת מהדיסק אחרי הכתיבה. ‏`unverified` = אין רשימת בדיקה — הדיסק נכתב ולא נבדק מולה:
   כתום, במילים, לעולם לא ירוק "אומת". ‏`sampled` = מדגם (ברירת המחדל), ‏`verified` = הכול (`readback_full`);
   שניהם אפורים ושקטים. בלי השדה (סוכן ישן / השלב לא רץ) — כלום.
   כשל של הקריאה הוא `failed` עם הסיבה ב-`error` (drawerErrorText), לא שורה כאן. */
function readbackLine(d) {
  // ‏#1907 (נדב 22/10): הושלם, לא אומת — רשימת הבדיקה לא הורדה מהשרת אחרי כל הניסיונות; סיבה משלו, כתום.
  if (d.readback === "unverified" && d.readback_why === "list_not_fetched") return `<span class="dline warn" title="${esc(d.readback_reason || "")}">לא אומת — הרשימה לא הורדה</span>`;
  if (d.readback === "unverified") return `<span class="dline warn" title="${esc(d.readback_reason || "")}">לא אומת אחרי הכתיבה — אין עדיין רשימת בדיקה לאימג'</span>`;
  // ‏#1864 (נדב 20/10): ברירת המחדל היא מדגם — "נבדק במדגם", לעולם לא "אומת" סתם.
  if (d.readback === "sampled") return `<span class="dline" title="${esc(d.readback_reason || "")}">נבדק במדגם — חלק מהבלוקים נקרא בחזרה מהדיסק ותאם לאימג'</span>`;
  if (d.readback === "verified") return `<span class="dline">אומת במלואו — כל הבלוקים נקראו בחזרה מהדיסק ותאמו לאימג'</span>`;
  return "";
}

function roomRound() { return ROOM && ROOM.round ? ROOM.round : null; }
function roomMachine(mac) { return ROOM && Array.isArray(ROOM.machines) ? ROOM.machines.find((x) => x.mac === mac) || null : null; }
/* ‏#1366 (נדב 13/10): `fail` = מה שעוצר BIOS של HP על "SMART error, press F1" — שורת הבריאות
   FAILED, או 198/197 מעל 0. ‏#1580 (נדב 17/10): **אדום** — נכתב רק באישור מפורש, בלי תשובה מדלג. */
/* ‏#1798: סימן "כתיבה לא הושלמה" שעל הדיסק (hello `write_mark`, ‏agent/lib/writemark.sh) — כתום:
   אסור כמקור, מותר כיעד. ‏`unchecked` = הסימן לא נקרא — "לא נבדק", לעולם לא "תקין". */
const WRITE_MARK_HE = "כתיבה לא הושלמה — לא שמיש";
const SMART_REASON_HE = { health_failed: "בדיקת הבריאות נכשלה", pending_sectors: "סקטורים ממתינים (197)", uncorrectable: "סקטורים שלא ניתנים לתיקון (198)", media_readonly: "הבקר נעל את הדיסק לקריאה בלבד (NVMe)", health_unread: "ה-SMART לא נקרא — ה-BIOS עלול לעצור ב-F1" };   // #1921: 197/198 orange; NVMe read-only red; #2078: SMART unread red
/* #1861: the rest of smart.sh's reason words (unchecked) -- with SMART_REASON_HE and FAILURE_CAUSE_HE, every
   word the agent or the server puts in `reason` has Hebrew (tests/test_reasons_he_1861.py). Same words as smart.sh
   smart_reason_he and native-gui smart_reason_text. */
const SMART_UNCHECKED_HE = { no_health: "הדיסק אינו מדווח על בריאות", no_smartctl: "smartctl חסר ב-initramfs", timeout: "smartctl לא ענה בזמן",
  open_failed: "smartctl לא הצליח לפתוח את הדיסק", no_smart: "הדיסק אינו תומך ב-SMART", ro_unread: "לא נקרא אם הדיסק במצב קריאה בלבד",
  cmd_failed: "פקודת SMART לדיסק נכשלה", no_attrs: "הדיסק לא החזיר טבלת מאפייני SMART",   /* #1903 */
  no_table: "טבלת הידע של SMART חסרה — לא נבדק" };
/* #1859 (Nadav 20/10): the orange -- smart.sh SMART_WARN_REASONS; the learned ones are server/smart_history.py LEARNED_HE. */
const SMART_WARN_HE = { critical_rose: "מאפיין קריטי עלה מאז הסבב הקודם — להחליף את הדיסק", downshift_rose: "ירידות מהירות SATA (183) עלו מאז הסבב הקודם",
  timeout_rose: "פקודות שנתקעו (188) עלו מאז הסבב הקודם", hot: "הדיסק התחמם בכתיבה בסבב הקודם", reported_uncorrectable: "שגיאות שלא תוקנו (187)",
  reallocated: "סקטורים שהועברו (5)", realloc_event: "אירועי העברה (196)", spin_retry: "ניסיונות סיבוב חוזרים (10)", ssd_worn: "הדיסק שחוק — 90% או יותר",
  spare_exhausted: "הרזרבה של הדיסק נגמרה",
  /* #1921 (Nadav 20/10): the knowledge table's orange -- smart.sh smart_reason_he, the same sentences */
  hot_now: "הדיסק חם מעבר לסף שלו עכשיו — לבדוק אוורור", bad_blocks: "הדיסק מצא בלוקים פגומים בזמן עבודה (183)",
  unc_logged: "ביומן הדיסק יש קריאה שלא תוקנה (UNC)", media_errors: "הדיסק דיווח על שגיאות נתונים שלא תוקנו (NVMe)",
  program_fail: "הזיכרון של ה-SSD נכשל בכתיבה או במחיקה", data_path: "שגיאות בנתיב הנתונים בתוך הדיסק",
  selftest_failed: "הבדיקה העצמית האחרונה של הדיסק נכשלה", nvme_reliability: "הבקר מדווח שהאמינות של הדיסק נפגעה (NVMe)" };
function smartReasonHe(r) { return SMART_REASON_HE[r] || FAILURE_CAUSE_HE[r] || SMART_UNCHECKED_HE[r] || SMART_WARN_HE[r] || "סיבה לא מוכרת"; }
function smartF1Text(reason) {
  if (reason === "readonly") return FAILURE_CAUSE_HE.readonly;   // #1742: red like SMART fail, but no F1 and no SMART
  if (reason === "media_readonly") return SMART_REASON_HE.media_readonly;   // #1921: NVMe -- red, no F1
  if (reason === "health_unread") return SMART_REASON_HE.health_unread;   // #2078: not read -- not "the health check failed"
  return `ה-BIOS יעצור ב-F1 — SMART: ${SMART_REASON_HE[reason] || reason || "תקלה"}`;
}
function smartText(d) {
  if (d.smart === "ok") return "SMART תקין";
  if (d.smart === "warn") return d.smart_reason ? `SMART אזהרה — ${smartReasonHe(d.smart_reason)}` : "SMART אזהרה";   // #1859: why orange
  if (d.smart === "fail") return smartF1Text(d.smart_reason);   // #1580: red, never written
  if (d.smart === "failed_last") return "נכשל בשיכפול הקודם";
  return "SMART לא נבדק";
}
/* #1859 (Nadav 20/10): every SMART attribute of a disk, in Hebrew (server/smart_names.py), the critical
   ones marked, and what rose since this serial's previous round -- a critical one adds "להחליף את הדיסק".
   No previous round is "אין עדיין השוואה", never "stable". Fetched when the machine's panel opens. */
const SMART_ATTRS = {};
function loadSmartAttrs(mac, rerender) {
  if (SMART_ATTRS[mac]) return;
  SMART_ATTRS[mac] = { loading: true };
  api("/machines/" + encodeId(mac) + "/smart").then((v) => { SMART_ATTRS[mac] = v; }, (e) => { SMART_ATTRS[mac] = { error: e.message }; }).then(rerender);
}
function smartAttrRow(a) {
  const marks = [];
  if (a.critical) marks.push(`<span class="n-chip w">קריטי</span>`);
  if (a.rose_by != null) marks.push(`<span class="n-chip ${a.critical ? "d" : "w"}">עלה ב-${ltr(String(a.rose_by))} מאז הסבב הקודם${a.critical ? " — להחליף את הדיסק" : ""}</span>`);
  if (a.failing_now) marks.push(`<span class="n-chip d">בסף היצרן</span>`);
  const nums = a.value != null ? `${ltr(String(a.value))} / ${ltr(String(a.worst ?? "—"))} / ${ltr(String(a.thresh ?? "—"))}` : "—";
  const name = `${esc(a.name_he)}${a.known ? "" : ` <span class="muted">(${esc(a.note_he)})</span>`}`;
  return `<tr><td class="mono">${esc(String(a.id))}</td><td title="${esc(a.note_he)}">${name}${a.vendor ? ` <span class="cap mono">${esc(a.vendor)}</span>` : ""}</td>`
    + `<td>${nums}</td><td class="mono">${a.raw != null ? ltr(String(a.raw)) : "—"}</td><td>${marks.join(" ")}</td></tr>`;
}
function smartAttrsHtml(mac, d) {
  const all = SMART_ATTRS[mac];
  if (!all || all.loading) return `<div class="muted">טוען את מאפייני ה-SMART…</div>`;
  if (all.error) return UI.note("warn", "מאפייני ה-SMART לא נקראו: " + esc(all.error));
  const one = (all.disks || []).find((x) => (d.serial && x.serial === d.serial) || x.dev === d.dev);
  if (!one || !one.read) return `<div class="muted">SMART לא נקרא מהדיסק הזה — לא נבדק</div>`;
  const head = one.compared ? `<div class="cap">הושווה לסבב הקודם (${esc(fmtWhen(one.previous_at))})${one.learned ? ` · <b>${esc(smartReasonHe(one.learned))}</b>` : ""}</div>`
    : `<div class="cap">${esc(all.no_history_he || "אין עדיין השוואה")}</div>`;
  // #1921 (Nadav 20/10): the hardware table's notes (server/smart_names.py, the agent's hw-issues.tsv) -- the
  // cable or the drawer, said beside the disk and never as its colour
  const hw = (one.hw || []).map((h) => UI.note("warn", `הערת חומרה (מגירה/כבל — לא הדיסק): ${esc(h.he || h.word)}`)).join("");
  return hw + `<details class="smart-attrs"><summary>כל מאפייני ה-SMART (${one.attributes.length})</summary>${head}`
    + `<table class="n-tbl smart-attrs-tbl"><thead><tr><th>מזהה</th><th>שם</th><th>ערך / הכי נמוך / סף</th><th>גולמי</th><th></th></tr></thead><tbody>`
    + one.attributes.map(smartAttrRow).join("") + `</tbody></table></details>`;
}
/* מכונה · דיסק N · SATA N-1 של רשומת כשל (#874). */
function failureWhere(f) {
  const m = findMachine(f.mac);
  if (f.port == null) return m ? machineName(m) : f.mac;
  const mapped = m && failureSlot(m, f) != null;
  return (m ? machineName(m) : f.mac) + ` · ${m ? failureLabel(m, f) : `SATA ${f.port - 1}`}${mapped ? ` · SATA ${f.port - 1}` : ""}`;
}
function groupFailures(kids) {
  const macs = new Set(kids.map((m) => m.mac));
  const serials = new Set(kids.flatMap((m) => (m.disks || []).map((d) => d.serial).filter(Boolean)));
  return (DISK_FAILURES || []).filter((f) => macs.has(f.mac) || (f.serial && serials.has(f.serial)));
}
/* החריצים של משכפל, לפי "דיסק N". בסבב — drawer_list מ-/room (מצב, בייטים, SMART,
   CRC לכל מגירה); בלי סבב — /machines[].disks[] (dev, size, model, serial, port, smart).
   ‏#1485: השורות לפי המיפוי (driveRows) — דיסק 1..N, ואחריהם כונן שאינו ממופה (‏n=null,
   "SATA n (לא ממופה)") וכונן CD/DVD. כונן לא ממופה אינו מוסתר ואינו מקבל מספר מנוחש. */
function machineSlots(m) {
  const rm = roomMachine(m.mac);
  const live = !!(roomRound() && rm && Array.isArray(rm.drawer_list));
  const src = live ? rm.drawer_list : m.disks;
  const mm = rm && Array.isArray(rm.drawer_slots) ? { ...m, drawer_slots: rm.drawer_slots } : m;
  const fails = machineDiskFailures(m);
  const slots = driveRows(mm, src).map((r) => ({ ...r, live: live && r.disk ? r.disk : null, fail: r.optical ? null : rowFailure(m, r, fails) }));
  return { slots, live, declared: true };
}
/* צבע החריץ (#1580): אדום (SMART תקול / נכשל בסבב) > כתום (נכשל בשיכפול הקודם, אזהרה, CRC עלה/חשוד)
   > כחול (כותב/מאמת) > ירוק (SMART תקין / נכתב) > אפור (לא נבדק) · מקווקו = ריק. */
function slotClass(s) {
  if (!s.disk) return "empty";
  const d = s.disk;
  if (d.state === "failed" || d.smart === "fail" || (s.fail && s.fail.cause === "readonly")) return "err";   // #1580: SMART fail red; failed last orange; #1742: read-only red
  if (s.fail || d.smart === "failed_last" || d.smart === "warn" || d.smart_learned || (d.crc_delta != null && d.crc_delta > 0) || linkSlow(d) || d.crc_rise > 0 || d.crc_suspect === true || d.port_suspect === true || d.disk_suspect === true || (d.model_speed && d.model_speed.slow === true)) return "warn";   // #1366: crc_suspect · #1844/#1845: learned
  if (d.state === "writing" || d.state === "verifying") return "run";
  if (d.smart === "ok" || d.state === "done") return "ok";
  return "";
}
/* #2050 (mockup 07, Nadav 24/10): how loud a drawer row is -- presentation only. The colour is
   slotClass's (#1580/#1859/#2033) and the existing driver / done-with-a-warning signals, the
   severe one wins; green only on positive evidence that the disk was written AND read back
   (readback verified or sampled, #1805/#1864) -- healthy SMART or "done" alone is not that. */
function bayEmphasis(s) {
  const d = s.disk;
  if (!d || s.optical || d.state === "cancelled" || d.cancelled_round || (d.state === "writing" && d.cancel)) return "";   // #1386: cancelled/cancelling are grey, as on the GUI
  const colour = slotClass(s), drv = d.drivers ? driversTone(d.drivers) : "";
  const done = d.state === "done" || (!!s.live && !d.state && d.fresh === false && !d.skipped_red);
  if (colour === "err" || drv === "bad") return " bay-emphasis err";
  if (colour === "warn" || drv === "warn" || (done && (d.error || d.readback === "unverified"))) return " bay-emphasis warn";
  return done && (d.readback === "verified" || d.readback === "sampled") ? " bay-emphasis ok" : "";
}
/* ‏#432: קישור ה-SATA של מגירה — ב-/room עם `link_state` מהשרת, ב-/machines (בלי סבב) רק
   המספרים מה-hello. ‏null = לא נקרא: "לא ידוע", לעולם לא תקין (עיקרון 5). כתום = שואל, לא חוסם. */
function linkSlow(d) {
  if (d.link_state) return d.link_state === "slow";
  return d.link_gbps != null && d.link_max_gbps != null && d.link_gbps < d.link_max_gbps;
}
function linkText(d) {
  if (d.link_gbps == null) return "קישור SATA לא ידוע";
  const f = (x) => Number(x).toFixed(1);
  return `קישור ${f(d.link_gbps)}${d.link_max_gbps != null ? ` מתוך ${f(d.link_max_gbps)}` : ""} Gb/s${linkSlow(d) ? " · לבדוק כבל או מגירה" : ""}`;
}
/* #1033: מלבן הדיסק בגריד מציג רק שתי שורות — "דיסק N" והקיבולת. כל
   השאר (דגם, מספר סידורי, SATA, SMART, סיבת כשל) עובר ל-title (tooltip)
   ולמגירה (disksHtml) שלא השתנתה. */
function diskTitle(m, s, d) {
  if (s.optical) return ["כונן CD/DVD", rowConn(s), s.optical.model || "", s.n != null ? `ממופה כדיסק ${s.n} — לא יעד` : ""].filter(Boolean).join(" · ");
  if (!d) return `דיסק ${s.n} · ${rowConn(s)} · ${m.disks == null ? "לא דווח" : "ריק"}`;
  /* ‏#704: מלבן עם שם — ה-tooltip פותח ב"דיסק N", כדי שהמספר לא ייעלם. */
  const parts = [s.n != null && slotName(m, s.n, d) ? `דיסק ${s.n}` : "", s.n != null ? rowConn(s) : `${rowConn(s)} — לא ממופה לאף דיסק`, d.model || "", d.serial || ""].filter(Boolean);
  if (s.fail) parts.push(`נכשל בשיכפול הקודם ${fmtDate(s.fail.at)} · ${failureCauseText(s.fail)}`);   // #1580: orange
  else if (d.state === "failed") parts.push(`נכשל בסבב${d.error ? " — " + d.error : ""}`);
  if (s.live && d.state && d.state !== "failed") parts.push(DRAWER_STATE_HE[d.state] || d.state);
  if (d.crc_delta != null && d.crc_delta > 0) parts.push(`CRC +${d.crc_delta} · לבדוק כבל`);
  if (d.link_state || "link_gbps" in d) parts.push(linkText(d));            // ‏#432: רשומה שנושאת קישור (גם "לא ידוע")
  if (d.crc_rise > 0) parts.push(`CRC +${d.crc_rise} מאז הסבב הקודם`);
  if (d.crc_suspect === true) parts.push(`כבל או מגירה חשודים — ${d.crc} שגיאות`);   // #1366: the absolute count over crc_warn_threshold
  if (d.learned_he) parts.push(d.learned_he);   // #1844: what the server learned on this connection / disk (text from the server)
  if (d.model_speed && d.model_speed.text_he) parts.push(d.model_speed.text_he);   // #1845: slower than the usual for its model
  if (d.write_mbps != null) parts.push(`כתיבה לפני הגל: ${d.write_mbps} MB/s`);       // #1366: null = not measured, never shown as 0
  if (s.live && d.state === "writing" && d.stalled_s != null && d.stalled_s >= 60) parts.push(`ללא תזוזה ${d.stalled_s} ש'`);
  if (s.live && d.drivers) parts.push(driversOutcomeText(d.drivers));   // ‏#1269: ‏null = השלב לא רץ על המגירה
  if (!s.fail) parts.push(smartText(d));
  if (d.smart_learned) parts.push(smartReasonHe(d.smart_learned));   // #1859: what the server learned since the previous round
  else if (d.smart_compared === false) parts.push("SMART: אין עדיין השוואה לסבב קודם");
  return parts.join(" · ");
}
/* ‏#1269: drawer_list[].drivers — הונחו / לא הונחו (השחזור עצמו הושלם) / דולגו. ‏#1331 (מוקאפ
   מאושר 17/10, גרסה 2): אותן מילים מתחת לכל דיסק בדף החדר (roundDskHtml) וב-tooltip. */
/* ‏#1331 T8 — לינוקס: ‏`drivers.linux` (docs/interfaces.md סעיף 4/17) — מה בדיקת ה-initramfs מצאה ביעד לינוקס.
   רק ‏generic הוא ראיה שהדיסק יעלה על בקר אחר (אפור); ‏hostonly / unsupported / failed = כתום ("לא נבדק" אינו
   "תקין", עיקרון 5); ‏skipped עם unknown_bus נאמר בגלוי. אותן מילים כמו lc_bay_linux ב-lcbay.c
   (tests/test_linux_drivers_line_1331.py שומר שהן לא נסחפות). השורה מחליפה את "האימג' אינו Windows". */
function linuxDriversText(l) {
  const why = l.error || l.reason || "סיבה לא ידועה", mod = l.module ? ` (${l.module})` : "";
  if (l.state === "generic") return `לינוקס: קובץ האתחול כולל את מודול הדיסק${mod} — נבדק, הדיסק אמור לעלות גם על בקר אחר`;
  if (l.state === "hostonly") return "לינוקס: קובץ האתחול כולל רק את בקר הדיסק של המחשב המקורי — עלול לא לעלות כאן";
  if (l.state === "unsupported") return `לינוקס: קובץ האתחול לא נבדק (${why}) — לא ידוע אם הדיסק יעלה כאן`;
  if (l.state === "failed") return `לינוקס: בדיקת קובץ האתחול נכשלה — ${why} · לא ידוע אם הדיסק יעלה כאן`;
  if (l.state === "skipped" && l.reason === "unknown_bus") return "לינוקס: סוג החיבור של הדיסק לא מוכר — קובץ האתחול לא נבדק";
  return `לינוקס: בדיקת קובץ האתחול דולגה — ${why}`;
}
const LINUX_STATES = ["generic", "hostonly", "unsupported", "failed", "skipped"];
const hasLinuxDrivers = (o) => !!(o && o.linux && LINUX_STATES.includes(o.linux.state));   // ‏linux פגום נזנח לבדו
function driversOutcomeText(o) {
  if (hasLinuxDrivers(o)) return linuxDriversText(o.linux);
  const boot = bootStorageText(o.boot);
  if (o.state === "staged") return `${driversStagedText(o.packages)}${boot ? " · " + boot : ""}`;
  if (o.state === "failed") return `דרייברים לא הונחו — ${o.error || "סיבה לא ידועה"}`;
  if (o.state === "no_match") return "דרייברים: אין חבילה תואמת לחומרה";
  if (o.reason === "no_model") return "דרייברים: לא נבחר דגם למגירה — לא הונחו";
  const why = o.reason === "not_windows" ? "האימג' אינו Windows" : (o.reason || "");
  return `דרייברים דולגו${why ? " — " + why : ""}`;
}
/* ‏#1331: הצבע של שורת הדרייברים — אדום = דרייבר האחסון לא הוגדר (הדיסק עלול לא לעלות),
   כתום = לא הונחו (המחשב יעלה בלעדיהם, #856), אפור = הונחו או אין חבילה. */
function driversTone(o) {
  if (hasLinuxDrivers(o)) return ["generic", "skipped"].includes(o.linux.state) ? "muted" : "warn";   // ‏T8
  if (o.state === "staged" && o.boot && o.boot.state === "failed") return "bad";
  return o.state === "failed" ? "warn" : "muted";
}
function driversStagedText(packages) {
  const p = packages || [];
  return `דרייברים הונחו (${p.length === 1 ? "חבילה אחת" : `${p.length} חבילות`}): ${p.join(", ")}`;
}
const BOOT_RISK_HE = "הדיסק עלול לא לעלות (0x7B) — לבדוק לפני שמחזירים לכיתה";
/* ‏#1331: drivers.boot — מה bootstorage.sh עשה לדרייבר האחסון ה-in-box של הדיסק
   שנכתב. ‏enabled = הוגדר לעלייה (ראיה חיובית); ‏failed = הדיסק עלול לא לעלות
   (0x7B) — עד כאן זה היה שורת יומן בלבד, ועכשיו גם ליד המכונה (עיקרון 5);
   ‏already / skipped / חסר = אין מה להגיד. טקסט חשוף — המתקשר מבצע esc. */
function bootStorageText(b) {
  if (!b) return "";
  const svc = b.service ? " " + b.service : "";
  if (b.state === "enabled") return `דרייבר האחסון${svc} הוגדר לעלייה`;
  if (b.state === "failed") return `דרייבר האחסון${svc} לא הוגדר לעלייה — ${b.error || "סיבה לא ידועה"}`;
  return "";
}
function slotHtml(m, s, admin) {
  const cls = slotClass(s), d = s.disk, title = esc(diskTitle(m, s, d));
  if (s.optical) return `<div class="disk empty" data-slot="${s.n ?? ""}" title="${title}"><b>${esc(rowLabel(m, s))}</b><span class="cap">${esc(rowConn(s))}</span></div>`;
  if (!d) return `<div class="disk empty" data-slot="${s.n}" title="${title}"><b>${esc(rowLabel(m, s))}</b><span class="cap">${m.disks == null ? "לא דווח" : "ריק"}</span></div>`;
  const cap = d.size_bytes ? `<span class="cap mono">${ltr(marketedDiskSize(d.size_bytes))}</span>` : `<span class="cap">—</span>`;
  let bar = "";
  if (s.live && d.state) {
    const pct = d.bytes_total > 0 ? 100 * (d.bytes_written || 0) / d.bytes_total : d.state === "done" ? 100 : null;
    bar = UI.barRow(pct, d.state === "failed" ? "err" : d.state === "done" ? "ok" : cls === "warn" ? "warn" : "");
  }
  const clear = s.fail && admin ? `<button class="btn sm" onclick="clearDiskFailure(${Number(s.fail.id)})">נקה</button>` : "";
  const emph = bayEmphasis(s);   // #2050: the words of the chip are the cloner card's (dskRowHtml)
  const chip = !emph ? "" : `<span class="bay-chip">${d.state === "failed" ? "נכשל" : emph.endsWith("err") ? "אדום" : emph.endsWith("warn") ? "אזהרה" : "נכתב"}</span>`;
  return `<div class="disk ${cls}${emph}" data-slot="${s.n ?? ""}" title="${title}"><b>${esc(rowLabel(m, s))}</b>${chip}${cap}${bar}${clear}</div>`;
}
function clonerState(m) {
  const rm = roomMachine(m.mac);
  if (roomRound() && rm && rm.joined) {
    if (rm.state === "failed") return { cls: "err", text: "נכשל בסבב" + (rm.error ? " — " + rm.error : "") };
    return { cls: "run", text: "בסבב · " + (rm.state === "waiting" ? "ממתין להתחלה" : Progress.view(rm).label) };
  }
  return machineState(m);
}
/* ‏`room` (גל 4): אותו כרטיס בדף ההפצה — המצב מ-`awake` של /room, WoL כשהמכונה לא מחוברת. */
function clonerCardHtml(m, admin, room = false) {
  const macEnc = encodeId(m.mac), net = netFor(m.mac), st = room ? roomCardState(m) : clonerState(m), { slots, declared } = machineSlots(m);
  const meta = [esc(st.text), `${slots.length} ${slots.length === 1 ? "חריץ" : "חריצים"}${declared || !slots.length ? "" : " (לפי הדיווח)"}`,
    net && net.ip ? `<span class="mono">${esc(net.ip)}</span>` : "", esc(NET ? seenAgo(net && net.last_seen) : "לא נקרא")].filter(Boolean).join(" · ");
  /* #1235: ‏`fresh: null` = הכונן לא דיווח serial (#105) — הסבב סופר לפי serial ואינו יכול
     לדעת אם נכתב. נאמר לפי חריץ, מתחת לגריד (#1033: המלבן עצמו נשאר שתי שורות). ‏`=== null`
     ולא `!fresh`: ‏false = נכתב, ו-undefined = רשומת /machines שאין בה `fresh` כלל. */
  const unid = slots.filter((s) => s.disk && s.disk.fresh === null)
    .map((s) => UI.note("warn", `<b>לא ניתן לזהות את הכונן ב${s.n != null ? `חריץ ${s.n}` : esc(rowConn(s))}</b> — אין לו מספר סידורי, ולכן הסבב אינו יודע אם נכתב.`)).join("");
  const failed = slots.filter((s) => s.disk && slotClass(s) === "err");
  const failedLine = failed.map((s) => `<div class="machine-fail">${esc(rowLabel(m, s))} אדום — נכשל בכתיבה</div>`).join("");
  const roundProgress = st.round ? `<div class="machine-round">${esc(st.round)}</div>` : "";
  const heldNote = st.held ? UI.note("warn", `<b>${esc(HELD_HE)}</b> — ${esc(heldLine(m, (roomMachine(m.mac) || {}).held || m.held))}`) : "";   // #2031
  const body = slots.length ? `${roundProgress}<div class="slots">${slots.map((s) => slotHtml(m, s, admin)).join("")}</div>${failedLine}${unid}${heldNote}`
    : UI.note("", `מספר החריצים לא הוגדר והמכונה ${m.disks == null ? "מעולם לא דיווחה על דיסקים" : "דיווחה: " + zeroDisksText(m)}.${admin ? ` ${UI.link("הגדר חריצים", `openMachineDetail('${macEnc}','disks')`)}` : ""}`);
  const wol = `<button class="btn sm" onclick="wakeMachine('${macEnc}')" title="WoL למכונה הזו בלבד (#984); לכל החדר — בכותרת">WoL</button>`;
  const acts = room
    ? (st.held ? heldButton(m) : st.off && roomOperator() ? wol : "")
    : (admin ? `<button class="btn sm" onclick="openMachineDetail('${macEnc}')">פרטים</button><button class="btn sm" onclick="openMachineDetail('${macEnc}','disks')">חריצים</button>${wol}` : "") + (st.held ? heldButton(m) : "");
  if (!room) return `<div class="mcard${st.cls ? "" : " off"}" data-mac="${esc(m.mac)}"><div class="mcard-h"><span class="st ${st.cls}"><b>${esc(machineName(m) || m.mac)}</b></span><span class="muted">${meta}</span></div>${body}${acts ? `<div class="acts">${acts}</div>` : ""}</div>`;
  const details = meta.split(" · ").slice(1).join(" · ");
  return `<div class="mcard${st.off ? " off" : ""}${failed.length ? " err" : ""}" data-mac="${esc(m.mac)}"><div class="mcard-h"><b>${esc(machineName(m) || m.mac)}</b>${UI.pill(st.cls, st.text)}${details ? `<span class="muted">${details}</span>` : ""}</div>${body}${acts ? `<div class="acts">${acts}</div>` : ""}</div>`;
}
function clonerLegend() {
  return `<div class="legend"><span><i class="sw-ok"></i>SMART תקין / נכתב</span><span><i class="sw-run"></i>כותב</span><span><i class="sw-warn"></i>אזהרה — כותב</span><span><i class="sw-err"></i>אדום — נכשל, מדלג</span><span><i class="sw-empty"></i>חריץ ריק</span></div>`;
}
/* ‏#1485 (נדב 16/10): דף "מחשבי שיכפול" לפי המוקאפ המאושר (docs/design/apple-mockups/console-full.html,
   ‏P.cloners): כרטיס "המחשבים והחריצים" — כרטיס לכל מכונה (שם + מצב, MAC, שורת דיסק לכל חריץ:
   "דיסק N" · פס · מצב), לחיצה פותחת את הפאנל, "חריצים" פותח אותו בלשונית "דיסקים" — ושם
   המיפוי לחיבורים. אחריו "מחשבים שלא דיווחו" עם WoL. סבב נפתח מדף ההפצה; היסטוריית הסבבים שם. */
const MACH_PILL = { ok: "ok", warn: "warn", err: "err", run: "info" };
function dskRowHtml(m, s) {
  const d = s.disk, title = esc(diskTitle(m, s, d));
  const emph = bayEmphasis(s);   // #2050
  const row = (label, bar, status, cls = "", act = "") => `<div class="dsk${cls ? " " + cls : ""}${emph}" data-slot="${s.n ?? ""}" title="${title}"><span>${esc(label)}</span>${bar}<b>${esc(status)}</b>${act}</div>`;
  const full = (cls) => `<div class="bar ${cls}"><i style="--w:100%"></i></div>`, none = `<div class="bar"><i style="--w:0%"></i></div>`;
  if (s.optical) return row("CD/DVD", none, rowConn(s), "opt");
  if (s.n == null) return row(rowLabel(m, s).replace(/ \(לא ממופה\)$/, ""), none, "לא ממופה", "unmapped");
  if (!d) return row(slotLabel(m, s.n, null), none, m.disks == null ? "לא דווח" : "ריק", "vacant");
  const cls = slotClass(s);
  // #1386: "בטל דיסק זה" — בוטל: אפור, פס ריק (הדיסק ריק עכשיו, לא "41%"); ביקשו ועוד לא בוצע: "מבטל…".
  if (s.live && d.state === "cancelled") return row(slotLabel(m, s.n, d), none, "בוטל", "cancelled");
  if (s.live && d.state === "writing" && d.cancel) return row(slotLabel(m, s.n, d), `<div class="bar warn"><i style="--w:0%"></i></div>`, "מבטל…", "cancelling");
  if (s.live && d.state) {
    const pct = d.bytes_total > 0 ? Math.round(100 * (d.bytes_written || 0) / d.bytes_total) : d.state === "done" ? 100 : 0;
    const status = d.state === "failed" ? "נכשל" : d.state === "done" ? "נכתב" : d.state === "verifying" ? "מאמת" : `${pct}%`;
    return row(slotLabel(m, s.n, d), `<div class="bar ${d.state === "failed" ? "err" : d.state === "done" ? "ok" : cls === "warn" ? "warn" : ""}"><i style="--w:${d.state === "failed" ? 100 : pct}%"></i></div>`, status);
  }
  const status = cls === "err" ? "אדום" : cls === "warn" ? "אזהרה" : cls === "ok" ? "תקין" : "לא נבדק";
  return row(slotLabel(m, s.n, d), full(cls === "err" ? "err" : cls === "warn" ? "warn" : cls === "ok" ? "ok" : "idle"), status);
}
function clonerMachHtml(m, admin) {
  const macEnc = encodeId(m.mac), st = clonerState(m), { slots } = machineSlots(m);
  const red = slots.some((s) => s.disk && slotClass(s) === "err");
  const unid = slots.filter((s) => s.disk && s.disk.fresh === null)
    .map((s) => `<div class="mach-note">לא ניתן לזהות את הכונן ב${s.n != null ? `חריץ ${s.n}` : esc(rowConn(s))} — אין לו מספר סידורי</div>`).join("");
  const rows = slots.length ? slots.map((s) => dskRowHtml(m, s)).join("")
    : `<div class="mach-note">${m.disks == null ? "מעולם לא דיווח על דיסקים" : esc(zeroDisksText(m))}</div>`;
  const open = admin ? ` role="button" tabindex="0" onclick="openMachineDetail('${macEnc}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openMachineDetail('${macEnc}')}"` : "";
  const held = st.held ? `<div class="mach-note">${esc(heldLine(m, m.held))}</div>` : "";   // #2031
  const acts = admin || st.held ? `<div class="acts">${admin ? `<button class="btn sm" type="button" onclick="event.stopPropagation();openMachineDetail('${macEnc}','disks')">חריצים</button>` : ""}${st.held ? heldButton(m) : ""}</div>` : "";
  return `<div class="mach${red ? " err" : !st.cls ? " off" : ""}" data-mac="${esc(m.mac)}"${open}><div class="mt"><span>${esc(machineName(m) || m.mac)}</span>${UI.pill(MACH_PILL[st.cls] || "", st.text)}${red ? UI.pill("err", "דיסק אדום") : ""}</div><div class="ms mono">${esc(m.mac)}</div><div class="dsks">${rows}</div>${unid}${held}${acts}</div>`;
}
function clonerGridCard(g, kids) {
  const admin = isAdmin();
  const body = kids.length ? `<div class="rgrid">${sortMachinesBySuffix(kids).map((m) => clonerMachHtml(m, admin)).join("")}</div>`
    : UI.empty("אין מחשבי שיכפול רשומים — הוסיפו את הראשון (MAC + שם).", admin ? `<button class="btn primary" onclick="openAddMachine({group:'${encodeId(g.id)}'})">+ מחשב שיכפול</button>` : "");
  return UI.card({ title: "המחשבים והחריצים", small: admin && kids.length ? "לחיצה על כרטיס פותחת פרטים" : "", cls: "c12", body });
}
/* מחשב "לא דיווח": אין hello מעל 10 דקות (‏/net.last_seen), או שלא נראה מעולם. ‏/net לא נקרא = לא נבדק. */
function silentMachines(kids) {
  if (!NET) return null;
  return kids.filter((m) => { const n = netFor(m.mac); return !n || !n.last_seen || Date.now() - new Date(n.last_seen).getTime() > 10 * 60000; });
}
function silentCard(kids) {
  const admin = isAdmin(), silent = silentMachines(kids);
  const body = silent == null ? UI.note("warn", "רשימת הרשת לא נקראה — אי אפשר לדעת מי לא דיווח")
    : !silent.length ? UI.empty("מחשב ללא hello מעל 10 דקות יופיע כאן עם אפשרות להעיר אותו (WoL).", "", "כל המחשבים הרשומים דיווחו")
    : UI.datagrid({ columns: ["מחשב", "נראה לאחרונה", ""], rows: sortMachinesBySuffix(silent).map((m) => { const n = netFor(m.mac); return [
      `<b>${esc(machineName(m) || m.mac)}</b>`, esc(seenAgo(n && n.last_seen)),
      admin ? `<button class="btn sm" onclick="wakeMachine('${encodeId(m.mac)}')">WoL</button>` : ""]; }) });
  return UI.card({ title: "מחשבים שלא דיווחו", cls: "c12", flush: !!(silent && silent.length), body });
}
function clonerStats(kids) {
  const all = kids.map((m) => machineSlots(m).slots);
  const count = (fn) => all.reduce((n, slots) => n + slots.filter(fn).length, 0);
  return {
    slots: count((s) => s.n != null), filled: count((s) => s.disk), redInSlot: count((s) => s.fail),
    on: MONITOR_ROWS ? kids.filter((m) => (monitorRow(m.mac) || {}).online).length : null,
  };
}
function clonersView(g, kids) {
  const gidEnc = encodeId(g.id), admin = isAdmin(), r = roomRound(), st = clonerStats(kids), fails = groupFailures(kids);
  const sub = [`${kids.length} ${kids.length === 1 ? "מחשב" : "מחשבים"}`, st.on == null ? "מחוברים: לא נקרא" : `${st.on} מחוברים`,
    DISK_FAILURES ? (fails.length === 1 ? "דיסק אחד נכשל בשיכפול הקודם" : `${fails.length} נכשלו בשיכפול הקודם`) : "כשלים קודמים: לא נקרא"].join(" · ");   // #1580
  const pill = r ? UI.pill(ROOM.stream_stalled ? "warn" : "info", `סבב פעיל — גל ${r.wave_number || 1}, ${r.written_drives || 0}/${r.target_drives || 0}${ROOM.stream_stalled ? " · הזרם עצר" : ""}`)
    : ROOM == null ? UI.pill("", "החדר לא נקרא") : "";
  const actions = admin ? `<button class="btn" onclick="openAddMachine({group:'${gidEnc}'})">+ מחשב שיכפול</button><button class="btn danger" onclick="poweroffRoom('${gidEnc}')">כיבוי כולם…</button>` : "";
  return { sub, pill, actions, body: clonerGridCard(g, kids) + silentCard(kids) };
}
/* ‏#980 §1: כיבוי כל המשכפלים — **דרך הסוכן**: השרת רושם בקשה, היא נוסעת ב-hello הבא של כל
   משכפל, והסוכן דורך WoL (arm_wol, #587) ורק אז נכבה. לא poweroff -f מהשרת. התשובה היא כמה
   בקשות נרשמו, לא כמה נכבו — "מחוברים" יורד כשהם באמת נכבים. */
function poweroffRoom(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  const g = (GROUPS || []).find((x) => x.id === gid);
  if (!g || !isAdmin()) return;
  sheet({ title: "לכבות את כל מחשבי השיכפול?", sub: g.label,
    note: impactList([["w", "כל משכפל שממתין נכבה ב-hello הבא שלו (עד כ-15 שניות); מחשב שלא יענה תוך 2 דקות לא ייכבה"],
      ["ok", "הכיבוי עובר דרך הסוכן ורק אחרי שדרך WoL — כך אפשר להעיר אותו שוב"], ["w", "בזמן סבב פעיל השרת מסרב"]]),
    danger: true, submitLabel: "כיבוי כל המשכפלים",
    onSubmit: async () => { const r = await post("/room/poweroff", { confirm_name: g.label }); toast(`בקשת כיבוי נשלחה ל-${r.requested} מחשבים — "מחוברים" יירד כשהם נכבים`, 6000); } });
}
/* חלון "התחל סבב" משותף לדף ההפצה ולמסך החדר; כל ההתנהגות נשארת בתוכו. */
function openRoomRound() {
  if (!roomOperator() || ROOM == null || roomRound()) return;
  const kids = roomMachines();
  openModalContent("התחל סבב", roomNewWindow(kids), "פתח סבב", "openRoomRoundSubmit()", false,
    "בחרו אימג׳, יעד ומגירות · מחשבים כבויים יידלקו ב-WoL", "wide");
  roomFooterSync(kids);
}
/* רענון חי: /room כל 2 שניות כשאובייקט המשכפלים פתוח (כמו /overview בדף ההפצה); רינדור רק על שינוי. */
async function refreshGroupLive() {
  const g = (GROUPS || []).find((x) => x.id === MACHINES_CLASS), room = current === "deploy";
  if (!room && (!g || g.role !== "cloner" || current !== "machines")) return;
  // ‏גל 4: בחדר גם /machines — השאלה למפעיל (prompt, #906) משתנה בין דגימות.
  const [view, machines] = await Promise.all([api("/room"), room ? api("/machines").catch(() => null) : null]);
  ROOM = view && Array.isArray(view.machines) ? view : null;
  if (Array.isArray(machines)) MACHINES = machines;
  const key = room ? JSON.stringify([ROOM, (MACHINES || []).map((m) => [m.mac, m.prompt])]) : JSON.stringify(ROOM);
  if (key === ROOM_KEY) return;
  ROOM_KEY = key;
  renderCurrent();
}
/* קליטות שהשתנו (‏/tasks נדגם כל 2 שניות) — מרנדר את אובייקט הבנייה הפתוח. */
function groupTasksChanged(all) {
  const key = JSON.stringify(all.map((t) => [t.id, t.state, t.bytes_written, t.error, t.updated_at, t.verify_bytes]));
  if (key === MCH.tasksKey) return false;
  MCH.tasksKey = key;
  return current === "machines" && !!MACHINES_CLASS;
}

/* ---------- מחשבי בנייה (builders.md): קליטה בתהליך, כרטיסי מחשבים, קליטות אחרונות ---------- */
function groupTasks(kids) { const macs = new Set(kids.map((m) => m.mac)); return (CAPTURE_TASKS || []).filter((t) => macs.has(t.mac)); }
function activeTasks(kids) { return groupTasks(kids).filter((t) => t.state === "pending" || t.state === "running"); }
/* "דיסק N" לפי הדיווח האחרון של המכונה; כשהמכונה לא דיווחה — שם ההתקן שנבחר בקליטה, כמו שהוא. */
function taskDisk(t) {
  const m = findMachine(t.mac);
  const d = m && Array.isArray(m.disks) ? m.disks.find((x) => x.dev === t.disk) : null;
  return d ? esc(diskLabel(m, d)) : t.disk ? `<span class="mono">${esc(t.disk)}</span>` : "—";
}
function taskWho(t) { const m = findMachine(t.mac); return m ? machineName(m) || m.mac : t.machine || t.mac || ""; }
function fmtDuration(from, to) {
  const s = (new Date(to).getTime() - new Date(from).getTime()) / 1000;
  if (!from || !to || !Number.isFinite(s) || s < 0) return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return (h ? `${h}:${String(m).padStart(2, "0")}` : String(m)) + ":" + String(sec).padStart(2, "0");
}
function taskWhen(t) { return isToday(t.created_at) ? `היום ${fmtClock(t.created_at)}` : `${fmtDate(t.created_at)} ${fmtClock(t.created_at)}`; }
function taskStatus(t) {
  if (t.state === "pending") return UI.status("run", "ממתין ל-PXE");
  if (t.state === "running") return UI.status("run", "נקלט · " + Progress.view(t).label);
  if (t.state === "verifying") return UI.status("run", VERIFYING_LABEL + " · " + Progress.view(t).label);
  if (t.state === "done") return t.error ? UI.status("warn", "הושלם — אזהרה") : UI.status("ok", "הושלם");
  if (t.state === "failed") return UI.status("err", "נכשל");
  if (t.state === "cancelled") return UI.status("", "בוטל");
  return UI.status("warn", t.state || "—");
}
function taskWarning(t) {
  if (t.state === "done" && t.error) return UI.status("warn", t.error);
  if (t.state === "failed") return t.error ? esc(t.error) : `<span class="muted">בלי הודעה</span>`;
  return `<span class="muted">—</span>`;
}
function cancelCaptureVerified(id) {
  let tid = id; try { tid = decodeURIComponent(id); } catch (e) {}
  const t = (CAPTURE_TASKS || []).find((x) => x.id === tid);
  if (!t || !isAdmin()) return;
  sheet({ title: "לבטל את הקליטה?", sub: t.name, danger: true, submitLabel: "ביטול הקליטה",
    note: impactList([["b", "הקבצים שהתקבלו יימחקו"], ["w", "מחשב הבנייה שיעלה ב-PXE לא יקבל את הקליטה"]]),
    onSubmit: async () => { await post(`/tasks/${encodeId(t.id)}/cancel`); await loadCaptures(); if (current === "machines") renderCurrent(); } });
}
/* #980 §4: the stage the agent reported (/tasks.stage, server keeps the last known one). pending = PXE,
   before the agent took the task; a running task without a known stage is an old agent -- never guessed
   from bytes (principle 5a). The time is in the current stage only: stage_started_at -> the last report. */
const CAPTURE_STAGES = [["ntfs_check", "בדיקת NTFS"], ["shrink", "כיווץ"], ["stream", "זרם + sha256"], ["restore_size", "החזרת הגודל"], ["verify", "אימות"]];
function captureStagesHtml(t) {
  if (t.state === "pending") return `<span class="muted">PXE — ממתין שהמחשב יעלה</span>`;
  const at = CAPTURE_STAGES.findIndex(([k]) => k === t.stage);
  if (at < 0) return `<span class="muted">השלב לא דווח (סוכן ישן)</span>`;
  return CAPTURE_STAGES.map(([, label], i) => i < at ? esc(label)
    : i === at ? `<b>${esc(label)}</b>${t.stage_started_at ? ` <span class="cap">(${esc(fmtDuration(t.stage_started_at, t.updated_at))})</span>` : ""}`
    : `<span class="muted">${esc(label)}</span>`).join(" → ");
}
/* #980 §6: "before capture" -- used from the last finished capture (/machines.last_capture), NTFS from the
   machine probe (probe.ntfs). Never captured, image gone, unmeasured and unchecked are four different texts. */
function beforeCaptureHtml(m) {
  const lc = m.last_capture;
  const used = lc == null ? `<span class="muted">לא נקלט מהמחשב הזה</span>`
    : !lc.in_library ? `<span class="muted">בשימוש — האימג׳ של הקליטה האחרונה כבר לא בספרייה</span>`
    : lc.used_bytes == null ? `<span class="muted">בשימוש לא נמדד בקליטה האחרונה</span>`
    : `בשימוש ${ltr(fmtBytes(lc.used_bytes))} <span class="cap">(מהקליטה האחרונה, ${esc(fmtDate(lc.at))})</span>`;
  const ntfs = probeCell(m.probe && m.probe.ntfs, (list) => {
    if (!Array.isArray(list) || !list.length) return `<span class="muted">אין מחיצת NTFS</span>`;
    if (list.some((p) => p && p.state === "hibernated")) return UI.status("warn", "NTFS נעול ב-Fast Startup — כיבוי רגיל לא משחרר; powercfg /h off כמנהל");
    if (list.some((p) => p && p.state === "dirty")) return UI.status("warn", "NTFS dirty — chkdsk ב-Windows");
    const bad = list.find((p) => !p || p.state !== "clean");
    return bad ? UI.status("warn", `NTFS: לא הצלחנו לבדוק ${(bad && bad.node) || ""}`.trim()) : UI.status("ok", "NTFS נקי");
  }, "NTFS לא נבדק");
  return `${used} · ${ntfs}`;
}
/* ‏#1485 (נדב 16/10): דף "מחשבי בנייה" לפי המוקאפ (‏P.build): כרטיס לכל מחשב — כתובת, MAC,
   דיסקים (לפי המיפוי, כונן CD/DVD בשמו — לא "ריק" ולא חריץ דיסק), קליטה אחרונה (או הקליטה שרצה
   עכשיו, עם שלביה), ושלושה כפתורים: קליטה · פתח פרטים · שינוי שם. "לפני קליטה" (#980 §6) נשאר
   כשורה נוספת — הרחבה למוקאפ. קליטה ממחשב כבוי מותרת: המשימה ממתינה ל-PXE (זו הזרימה). */
function builderDrivesHtml(m) {
  const rows = driveRows(m, m.disks);
  if (m.disks == null && !rows.some((r) => r.optical)) return `<span class="muted">לא דיווח</span>`;
  const shown = rows.filter((r) => r.disk || r.optical);
  if (!shown.length) return `<span class="muted">${esc(m.disks && !m.disks.length ? zeroDisksText(m) : "אין כוננים בחריצים")}</span>`;
  return `<ul class="drv-list">${shown.map((r) => {
    if (r.optical) return `<li><b>כונן CD/DVD</b> <span class="muted">· ${esc(rowConn(r))}${r.optical.model ? ` · <bdi>${esc(r.optical.model)}</bdi>` : ""}</span></li>`;
    const d = r.disk;
    return `<li${r.n == null ? ' class="warn"' : ""}><b>${esc(rowLabel(m, r))}</b> · ${ltr(marketedDiskSize(d.size_bytes))} · <bdi>${esc(diskBrand(d.model))}</bdi>${r.n != null ? ` <span class="muted">· ${esc(rowConn(r))}</span>` : ""}</li>`;
  }).join("")}</ul>`;
}
function builderCaptureHtml(m, admin) {
  const task = activeTasks([m])[0] || null;
  if (task) {
    const v = Progress.view(task), waiting = task.state === "pending";
    return `<div>${esc(task.name || "")} · ${waiting ? "ממתין ל-PXE" : `${esc(v.label)} עכשיו`}</div>${waiting ? "" : Progress.bar(task)}<div class="cap">${captureStagesHtml(task)}</div>`
      + (admin ? `<div>${UI.link("ביטול הקליטה…", `cancelCaptureVerified('${encodeId(task.id)}')`)}</div>` : "");
  }
  if (!CAPTURE_TASKS_READ) return `<span class="muted">לא נקרא</span>`;
  const last = groupTasks([m]).filter((t) => t.state === "done").sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))[0];
  return last ? `${esc(last.name || "")} · ${esc(fmtDate(last.updated_at))}` : "—";
}
function builderCardHtml(m, admin) {
  const macEnc = encodeId(m.mac), net = netFor(m.mac), st = machineState(m), task = activeTasks([m])[0] || null;
  const kv = `<dl class="kvd">`
    + `<dt>כתובת</dt><dd>${net && net.ip ? `<span class="mono">${esc(net.ip)}</span>` : "—"}</dd>`
    + `<dt>MAC</dt><dd><span class="mono">${esc(m.mac)}</span></dd>`
    + `<dt>דיסקים</dt><dd>${builderDrivesHtml(m)}</dd>`
    + `<dt>קליטה אחרונה</dt><dd>${builderCaptureHtml(m, admin)}</dd>`
    + `<dt>לפני קליטה</dt><dd>${beforeCaptureHtml(m)}</dd></dl>`;
  const acts = admin ? `<div class="acts">${task ? `<button class="btn primary" onclick="openCaptureDetail('${encodeId(task.id)}')">פירוט קליטה</button>` : `<button class="btn primary" onclick="openCapture('${macEnc}').catch(e => toast(e.message))">קליטה</button>`}<button class="btn" onclick="openMachineDetail('${macEnc}')">פתח פרטים</button><button class="btn" onclick="renameMachine('${macEnc}')">שינוי שם</button></div>` : "";
  const help = st.cls ? "" : `<p class="card-help">המחשב לא מחובר עכשיו — קליטה תמתין שיעלה ב-PXE.${admin ? ` אפשר להעיר אותו ${UI.link("דרך WoL", `wakeMachine('${macEnc}')`)}.` : ""}</p>`;
  return `<div class="card bcard" data-mac="${esc(m.mac)}"><div class="card-h"><span>${esc(machineName(m) || m.mac)} ${UI.pill(MACH_PILL[st.cls] || "", st.text)}${task ? " " + UI.pill(task.state === "pending" ? "warn" : "info", task.state === "pending" ? "קליטה ממתינה" : "קליטה רצה") : ""}</span></div><div class="card-b">${kv}${acts}${help}</div></div>`;
}
function buildersCard(g, kids) {
  const admin = isAdmin();
  if (!kids.length) return UI.card({ title: "מחשבי בנייה", cls: "c12", body: UI.empty("אין מחשב בנייה רשום — הוסיפו אותו (MAC + שם) כדי לקלוט ממנו אימג'.", admin ? `<button class="btn primary" onclick="openAddMachine({group:'${encodeId(g.id)}'})">+ מחשב בנייה</button>` : "") });
  return `<div class="c12 grid2">${sortMachinesBySuffix(kids).map((m) => builderCardHtml(m, admin)).join("")}</div>`;
}
function capturesTableCard(kids, cls) {
  const rows = groupTasks(kids).map((t) => ({ attrs: `data-task="${esc(t.id)}"`, cells: [
    UI.name(t.name || "", t.image_id || ""),
    // ‏#968: ‏"" = שורש הספרייה; שדה חסר (שרת ישן) = "—", לא "שורש".
    t.folder == null ? `<span class="muted">—</span>` : t.folder ? esc(t.folder) : `<span class="muted">שורש הספרייה</span>`,
    `${esc(taskWho(t))} · ${taskDisk(t)}`, taskStatus(t),
    t.state === "pending" ? `<span class="muted">—</span>` : ltr(fmtBytes(t.bytes_written)),
    t.state === "pending" ? `<span class="muted">—</span>` : esc(fmtDuration(t.created_at, t.updated_at)),
    taskWarning(t), esc(taskWhen(t))] }));
  const body = !CAPTURE_TASKS_READ ? UI.note("warn", "רשימת הקליטות לא נקראה")
    : UI.datagrid({ columns: ["אימג'", "תיקייה", "מחשב · דיסק", "מצב", "נקראו", "משך", "אזהרה / שגיאה", "מתי"], rows, empty: "אין קליטות עדיין — \"+ קליטת אימג'…\" פותחת את הראשונה" });
  return UI.card({ title: "קליטות אחרונות", small: "עד 20 האחרונות מ-/tasks", cls, flush: true, body });
}
function buildersView(g, kids) {
  const gidEnc = encodeId(g.id), admin = isAdmin();
  const on = MONITOR_ROWS ? kids.filter((m) => (monitorRow(m.mac) || {}).online).length : null;
  const sub = [`${kids.length} ${kids.length === 1 ? "מחשב" : "מחשבים"}`, on == null ? "מחוברים: לא נקרא" : on === 1 ? "מחשב אחד מחובר" : `${on} מחוברים`].join(" · ");
  const active = activeTasks(kids), pending = active.filter((t) => t.state === "pending").length;
  const pill = !active.length ? "" : active.length > 1 ? UI.pill("info", `${active.length} קליטות בתהליך`) : UI.pill(pending ? "warn" : "info", pending ? "קליטה ממתינה" : "קליטה רצה");
  const actions = admin ? `<button class="btn" onclick="openAddMachine({group:'${gidEnc}'})">+ מחשב בנייה</button>` : "";
  return { sub, pill, actions, body: buildersCard(g, kids) + capturesTableCard(kids, "c12") };
}

/* ---------- לשונית "נראו ברשת" — /net כ-datagrid; "רשום" הוא הפעולה הראשית לשורה לא רשומה ---------- */
function bootCell(b) {
  if (!b) return `<span class="muted">—</span>`;
  const since = b.seconds == null ? "זמן לא נקרא" : sinceSeconds(b.seconds);
  return UI.status(b.stalled ? "warn" : "ok", `${b.label} (${b.index}/${b.total}) · ${since}`)
    + (b.stalled ? `<span class="sub">נעצר לפני: ${esc(b.next_label)}</span>` : "");
}
function seenDevicesCard() {
  const acts = isAdmin() ? `<button class="btn sm" onclick="netDeviceAdd()">+ הוספה ידנית</button>` : "";
  if (!NET) return UI.card({ title: "נראו ברשת", acts, body: UI.note("warn", `רשימת ההתקנים לא נקראה${NET_ERR ? ": " + esc(NET_ERR) : ""}`) });
  const rows = NET.filter((d) => classroomsOn() || d.role !== "classroom").map((d) => {
    const macEnc = encodeId(d.mac);
    const who = d.registered ? UI.name(d.name || "", d.group_label || "") : UI.pill("warn", "לא רשום");
    const primary = d.registered
      ? `<button class="btn sm" onclick="openMachineDetail('${macEnc}')">פרטים</button>`
      : `<button class="btn sm primary" onclick="openAddMachine({mac:'${macEnc}'})">רשום</button>`;
    const more = isAdmin() ? `<button class="btn sm" onclick="netDeviceDescribe('${macEnc}')">תיאור</button><button class="btn sm danger" onclick="netDeviceForget('${macEnc}')">הסר</button>` : "";
    return { attrs: `data-mac="${esc(d.mac)}"`, cells: [who, `<span class="mono">${esc(d.mac)}</span>`, d.ip ? `<span class="mono">${esc(d.ip)}</span>` : `<span class="muted">—</span>`,
      d.description ? esc(d.description) : `<span class="muted">—</span>`, esc(ago(d.last_seen)), bootCell(d.boot), `<div class="acts">${primary}${more}</div>`] };
  });
  return UI.card({ title: "נראו ברשת", small: "כל מכונה שדיברה עם השרת; לא רשומה = כרטיס שהוחלף או מחשב חדש", acts, flush: true,
    body: UI.datagrid({ columns: ["שם", "MAC", "IP", "תיאור", "נראה לאחרונה", "שלב האתחול", ""], rows, empty: "עוד לא נראו התקנים. מכונה שתעלה ב-PXE תופיע כאן." }) });
}

/* ---------- לשונית "דיסקים אדומים" — #874 (אדום) ו-#926 (כתום, כווץ) ---------- */
/* #874: דיסקים אדומים — זיכרון כשלי הכתיבה בשרת, במקום הסימון על הדיסק
   (#845). הסיווג הוא של השרת (ata_cause.py); "נקה" = הדיסק הוחלף / הכבל
   תוקן, והרשומה מפסיקה לצבוע. אדום לפי סידורי **וגם** לפי מכונה+חריץ. */
const FAILURE_CAUSE_HE = { readonly: "הדיסק במצב קריאה בלבד", cable: "כבל/חריץ", disk: "הדיסק", removed: "המגירה נשלפה", write_error: "שגיאת כתיבה לדיסק", unclassified: "לא סווג" };   // write_error: #1841
/* ‏`only` (גל 3א): רק הכשלים של המכונות האלה (לפי MAC או סידורי) — לאובייקט המשכפלים. */
function diskFailuresCard(only = null, cls = "c12") {
  const rows = (only ? groupFailures(only) : DISK_FAILURES || []).map((f) => {
    const log = f.ata_log || [];
    const evidence = log.length ? `<details><summary>${log.length} שורות קרנל</summary><pre class="ata-log">${esc(log.join("\n"))}</pre></details>` : `<span class="muted">—</span>`;
    return [`<span class="mono">${esc(f.serial || "—")}</span>`, esc(failureWhere(f)), esc(fmtWhen(f.at)), UI.status("err", failureCauseText(f)), esc(f.error || ""), evidence,
      isAdmin() ? UI.acts([["נקה", `clearDiskFailure(${Number(f.id)})`]]) : ""];
  });
  const body = DISK_FAILURES == null ? UI.note("warn", "רשימת הכשלים לא נטענה")
    : UI.datagrid({ columns: ["מספר סידורי", "מכונה · דיסק · חריץ", "מתי", "סיבה", "שגיאה", "ראיה", ""], rows, empty: "אין דיסקים אדומים" });
  return UI.card({ title: "דיסקים אדומים — נכשלו בכתיבה", small: 'אדום במסך המחשב לפני הסבב; "נקה" אחרי החלפת דיסק או כבל', cls, flush: true, body });
}
async function clearDiskFailure(id) {
  try {
    await post(`/disk-failures/${id}/clear`);
    toast("הרשומה נוקתה");
    await loadMachines();
  } catch (e) {
    toast("הניקוי נכשל: " + e.message);
  }
}
/* #926: דיסקים מכווצים — מחיצת המקור כווצה לקליטה (#87) ולא הוחזרה לגודלה
   (אובדן חשמל / אתחול לפני סוף הקליטה). הרשומה נפתחת בשרת **לפני** הכיווץ
   ונסגרת אחרי ההחזרה; מה שנשאר פתוח הוא דיסק שעדיין מכווץ. כתום, לא אדום:
   ווינדוס עולה ממנו. ההחזרה נעשית ליד המחשב (מחשב הבנייה מציע באתחול);
   "נקה" = המפעיל הרחיב בעצמו / הדיסק הוחלף. */
function shrinkRecordsCard() {
  const rows = (SHRINK_RECORDS || []).map((r) => {
    const m = findMachine(r.mac);
    const where = (m ? machineName(m) : r.mac) + (r.port != null ? ` · ${portLabel(m, r.port)} · SATA ${r.port - 1}` : "");
    const gb = (sectors) => sectors != null ? (sectors * 512 / 1e9).toFixed(1) + " GB" : "—";
    // ‏#929: רשומה אחת לדיסק עם `partitions`; רשומה מלפני — המחיצה שבשדות העליונים.
    const text = (r.partitions || [r]).map((p) => `מחיצה ${p.idx} כווצה לקליטה ולא הוחזרה לגודלה המקורי (${gb(p.size_sectors)})`).join("; ");
    // הסיבה מהסוכן (shrink-note): "הטבלה הוחזרה, מערכת הקבצים לא נמתחה" — למה עדיין פתוח.
    const note = r.note ? `<span class="sub">${esc(r.note)}</span>` : "";
    return [UI.name(r.serial || "—", r.model || ""), esc(where), esc(fmtWhen(r.opened_at)), `<span class="disk-smart">${esc(text)}</span>${note}`, esc(r.image_name || ""),
      isAdmin() ? UI.acts([["נקה", `clearShrinkRecord(${Number(r.id)})`]]) : ""];
  });
  const body = SHRINK_RECORDS == null ? UI.note("warn", "רשימת הדיסקים המכווצים לא נטענה")
    : UI.datagrid({ columns: ["מספר סידורי", "מכונה · דיסק · חריץ", "מתי", "מצב", "אימג'", ""], rows, empty: "אין דיסקים מכווצים" });
  return UI.card({ title: "דיסקים מכווצים — מחיצת המקור לא הוחזרה לגודלה", small: 'מחשב הבנייה מציע להחזיר באתחול; "נקה" אחרי הרחבה ידנית או החלפת דיסק', flush: true, body });
}
async function clearShrinkRecord(id) {
  try {
    await post(`/shrink-records/${id}/clear`);
    toast("הרשומה נוקתה");
    await loadMachines();
  } catch (e) {
    toast("הניקוי נכשל: " + e.message);
  }
}

/* ---------- מגירת מחשב (machine-drawer.md) ---------- */
function diskBoxesHtml(m) {
  if (m.disks == null) return UI.note("", "המכונה מעולם לא דיווחה על כוננים.");
  if (!m.disks.length) return UI.note("warn", `המכונה דיווחה — ואין בה אף כונן: ${zeroDisksText(m)}.`);
  const fails = machineDiskFailures(m);
  const boxes = m.disks.map((d, i) => {
    const n = d.port != null ? Number(d.port) : i + 1;   // תחנת כיתה: בלי מיפוי (#1485 — משכפל/בנייה בלבד)
    const red = fails.find((f) => (f.mac === m.mac && f.port != null ? Number(f.port) === n : f.serial && f.serial === d.serial));
    const inc = d.write_mark === "incomplete";   // #1798: orange, below SMART's red
    const cls = d.smart === "fail" ? "err" : red || inc || d.smart === "warn" || d.smart === "failed_last" ? "warn" : d.smart === "ok" ? "ok" : "";   // #1580: SMART fail red, failed last orange
    const smart = smartText(d);   // #1859: one wording ("SMART SMART אזהרה" was the old warn), with why
    const mark = inc ? ` · ${WRITE_MARK_HE}` : d.write_mark === "unchecked" ? " · לא נבדק אם הכתיבה האחרונה הושלמה" : "";
    return `<div class="disk ${cls}"><b>${esc(slotLabel(m, n, d))}</b><span>SATA ${n - 1}</span><span>${ltr(marketedDiskSize(d.size_bytes))}</span><span class="cap">${esc(d.model || "—")}${d.serial ? ` · <span class="mono">${esc(d.serial)}</span>` : ""}</span><span class="cap">${red && d.smart !== "fail" ? `נכשל בשיכפול הקודם — ${esc(failureCauseText(red))}` : esc(smart)}${esc(mark)}</span></div>`;
  }).join("");
  return `<div class="disks">${boxes}</div>`;
}
function diskBrand(model) {
  const word = String(model || "").trim().split(/\s+/)[0];
  return word ? word.charAt(0).toUpperCase() + word.slice(1).toLowerCase() : "—";
}
function marketedDiskSize(bytes) {
  /* Physical-drive labels need the fuller commodity ladder than image fit and
     tolerate firmware reporting slightly above the round advertised capacity. */
  const n = Number(bytes);
  if (!Number.isFinite(n) || n <= 0) return fitsDisk(bytes).replace(" ", "");
  const sizes = [[120e9, "120GB"], [128e9, "128GB"], [240e9, "240GB"],
    [250e9, "250GB"], [256e9, "256GB"], [480e9, "480GB"], [500e9, "500GB"],
    [512e9, "512GB"], [1e12, "1TB"], [2e12, "2TB"], [4e12, "4TB"]];
  const match = sizes.find(([size]) => n <= size * 1.01);
  return match ? match[1] : fitsDisk(n).replace(" ", "");
}
/* ‏#1049 שלב ב': "בריאות המכונה" — מה שהסוכן מדד ב-hello (probe). כל שדה
   בשלושה מצבים (עיקרון 5): נמדד · null = "לא נבדק" (אפור, לא "תקין") ·
   {error} = "לא הצלחנו לבדוק: …" (כתום). השערים (probe_verdicts) מהשרת
   מוצגים מעל הקבוצה ואינם עוצרים סבב — עצירה היא הכרעה נפרדת. */
function probeCell(value, render, unchecked = "לא נבדק") {
  if (value == null) return `<span class="muted">${esc(unchecked)}</span>`;
  if (typeof value === "object" && !Array.isArray(value) && value.error != null) return UI.status("warn", `לא הצלחנו לבדוק: ${value.error}`);
  return render(value);
}
/* #1048: מתג/פורט (LLDP) וכבל (ethtool) — שלושת המצבים, בלי לקפל unheard לתקין. */
function netprobeLldpHtml(np) {
  if (np == null) return `<span class="muted">לא נבדק</span>`;
  if (np.pending) return `<span class="muted">ממתין</span>`;
  const v = np.lldp;
  if (v == null) return `<span class="muted">לא נבדק</span>`;
  if (v.error != null) return UI.status("warn", "לא הצלחנו להאזין");
  if (v.unheard) return `<span class="muted">לא נקלט (35ש')</span>`;
  const sw = v.switch || "", port = v.port || "";
  if (!sw && !port) return `<span class="muted">לא נבדק</span>`;
  const tip = [v.port_desc, v.chassis].filter(Boolean).join(" · ");
  return `<span class="mono" dir="ltr"${tip ? ` title="${esc(tip)}"` : ""}>${esc(sw)}${sw && port ? " · " : ""}${esc(port)}</span>`;
}
function netprobeCableHtml(np) {
  if (np == null) return `<span class="muted">לא נבדק</span>`;
  if (np.pending) return `<span class="muted">ממתין</span>`;
  const v = np.cable;
  if (v == null) return `<span class="muted">לא נבדק</span>`;
  if (v.error != null) return UI.status("warn", `לא הצלחנו לבדוק: ${v.error}`);
  if (v.skipped) return `<span class="muted">לא נבדק — יש קישור</span>`;
  if (v.status === "ok") return UI.status("ok", "תקין");
  if (v.status === "open" || v.status === "short") {
    const he = v.status === "open" ? "פתוח" : "קצר";
    const bad = (Array.isArray(v.pairs) ? v.pairs : []).find((p) => p && String(p.code || "").toLowerCase() === v.status);
    const pair = (bad && bad.pair) || "?", n = bad && bad.length_m != null ? bad.length_m : null;
    return UI.status("err", n != null ? `זוג ${pair} ${he} ב-${n} מ'` : `זוג ${pair} ${he}`);
  }
  return `<span class="muted">לא נבדק</span>`;
}
function probeSkewText(seconds) {
  const s = Math.abs(Number(seconds) || 0);
  return s >= 86400 ? `${Math.floor(s / 86400)} ימים` : s >= 3600 ? `${Math.floor(s / 3600)} שעות` : s >= 60 ? `${Math.floor(s / 60)} דק'` : `${s} שנ'`;
}
function machineVerdictsHtml(m) {
  return (Array.isArray(m.probe_verdicts) ? m.probe_verdicts : []).map((v) => UI.note(v.level === "err" ? "err" : "warn", esc(v.text_he || v.key || ""))).join("");
}
function machineHealthHtml(m) {
  const p = m.probe;
  if (p == null) return UI.note("", "המכונה מעולם לא דיווחה בדיקת מכונה (סוכן ישן).");
  const nvme = Array.isArray(p.disks) ? p.disks.filter((d) => d && d.nvme_smart !== null && d.nvme_smart !== undefined) : [];
  const rows = [
    ["חשמל", probeCell(p.power, (v) => v.on_battery ? UI.status("err", `על סוללה${v.supply ? ` (${v.supply})` : ""}`) : UI.status("ok", `חשמל${v.supply ? ` (${v.supply})` : ""}`))],
    ["שעון", probeCell(p.rtc, (v) => v.skew_seconds == null ? `<span class="muted">לא נבדק</span>`
      : Math.abs(v.skew_seconds) > 300 ? UI.status("warn", `סוטה ב-${probeSkewText(v.skew_seconds)} — סוללת BIOS חשודה`) : UI.status("ok", `סטייה ${probeSkewText(v.skew_seconds)}`))],
    ["מעבד", probeCell(p.cpu, (v) => `${esc(v.model || "—")}${v.cores != null ? ` · ${esc(v.cores)} ליבות` : ""}${v.microcode ? ` · מיקרוקוד <span class="mono">${esc(v.microcode)}</span>` : ""}`)],
    ["זיכרון", probeCell(p.memory, (v) => [
      v.total_bytes != null ? ltr(fmtBytes(v.total_bytes)) : `<span class="muted">סה"כ לא נבדק</span>`,
      probeCell(v.dimms, (d) => Array.isArray(d) ? `${d.length} DIMMs` : `<span class="muted">DIMMs לא נבדקו</span>`, "DIMMs לא נבדקו"),
      probeCell(v.ecc, (e) => e.ue_count > 0 ? UI.status("err", `ECC: ${e.ue_count} לא-מתוקנות`) : e.ce_count > 0 ? UI.status("warn", `ECC: ${e.ce_count} מתוקנות`) : UI.status("ok", "ECC תקין"), "ECC לא נבדק"),
    ].join(" · "))],
    ["טמפ' מקס'", probeCell(p.thermal, (v) => {
      const temps = (Array.isArray(v) ? v : []).map((z) => z && Number(z.temp_c)).filter((t) => Number.isFinite(t));
      if (!temps.length) return `<span class="muted">אין אזור תרמי</span>`;
      const max = Math.max(...temps);
      return UI.status(max >= 90 ? "err" : max >= 80 ? "warn" : "ok", `${max}°C`);
    })],
    ["רשת", probeCell(p.nic, (v) => {
      if (!Array.isArray(v) || !v.length) return `<span class="muted">אין כרטיס עם קישור</span>`;
      return v.map((n) => {
        const link = `<span class="mono">${esc(n.name || "?")}</span> ${n.speed_mbps != null ? `${esc(n.speed_mbps)}Mb/s` : "—"}${n.duplex ? ` ${esc(n.duplex)}` : ""}`;
        const cnt = probeCell(n.stats, (x) => (x.rx_crc_errors > 0 ? UI.status("warn", `CRC ${x.rx_crc_errors}`) : `CRC ${esc(x.rx_crc_errors ?? "—")}`) + ` · dropped ${esc(x.rx_dropped ?? "—")}`, "מונים לא נבדקו");
        const dup = probeCell(n.ip_conflict, (x) => x.duplicate ? UI.status("err", "כפילות IP") : `IP ללא כפילות`, "כפילות IP לא נבדקה");
        return `${link} · ${cnt} · ${dup}`;
      }).join("<br>");
    })],
    ["מתג ופורט", netprobeLldpHtml(p.netprobe)],
    ["כבל", netprobeCableHtml(p.netprobe)],
    ["NVMe", p.disks == null ? `<span class="muted">לא נבדק</span>` : !nvme.length ? `<span class="muted">לא נבדק (אין NVMe, או nvme לא ארוז)</span>`
      : nvme.map((d) => `<span class="mono">${esc(d.name || "?")}</span>: ` + probeCell(d.nvme_smart, (x) => {
        const bad = (x.critical_warning != null && x.critical_warning !== 0) || x.media_errors > 0;
        const text = `${x.percentage_used != null ? `${x.percentage_used}% בלאי` : "בלאי לא דווח"} · ${x.media_errors != null ? `${x.media_errors} שגיאות מדיה` : "שגיאות מדיה לא דווחו"}${x.critical_warning ? ` · critical_warning=${x.critical_warning}` : ""}`;
        return UI.status(bad ? "err" : x.percentage_used >= 90 ? "warn" : "ok", text);
      })).join("<br>")],
    ["קריסה קודמת", probeCell(p.pstore, (v) => v.crashed
      ? UI.status("warn", "קרס לפני האתחול הזה") + (Array.isArray(v.files) && v.files.length ? ` <span class="mono">${esc(v.files.join(", "))}</span>` : "") + (v.excerpt ? ` <details class="inline"><summary>קטע</summary><pre class="ata-log">${esc(v.excerpt)}</pre></details>` : "")
      : `<span class="muted">לא</span>`)],
    ["מפתח OEM", probeCell(p.oem_key, (v) => `<span class="mono" dir="ltr">${esc(v)}</span>`)],
    ["PCI בלי דרייבר", probeCell(p.pci_without_driver, (v) => !Array.isArray(v) || !v.length ? `<span class="muted">אין</span>`
      : `${UI.status("warn", `${v.length} ${v.length === 1 ? "התקן" : "התקנים"}`)} <span class="mono" title="${esc(v.join(" "))}">${esc(v.slice(0, 3).join(" · "))}${v.length > 3 ? " …" : ""}</span>${isAdmin() ? ` · ${UI.link("לדף הדרייברים", "selectPageById('drivers')")}` : ""}`)],
    ["הצפנה", probeCell(p.encryption, (v) => !Array.isArray(v) || !v.length ? `<span class="muted">אין</span>`
      : v.map((e) => `${esc(e.type === "crypto_LUKS" ? "LUKS" : e.type || "?")} <span class="mono">${esc(e.node || "")}</span>`).join(" · "))],
  ];
  return UI.kv(rows);
}
/* #977 §7: firmware/secure_boot as the agent measured them (#500). null = not reported, never "off". */
function firmwareHtml(m) {
  if (m.firmware == null) return `<span class="muted">לא דווח</span>`;
  if (m.firmware !== "uefi") return esc(String(m.firmware).toUpperCase());
  const sb = m.secure_boot == null ? `<span class="muted">Secure Boot לא דווח</span>` : m.secure_boot ? "Secure Boot דלוק" : "Secure Boot כבוי";
  return `UEFI · ${sb}`;
}
/* ‏#1300 (הכרעת נדב 12/10): מגירת מחשב כמו console-full.html W.machDrawer — תגיות בראש, שלוש
   לשוניות (סקירה · דיסקים · היסטוריה), כפתור ראשי ושורת כפתורים אחת — אבל עם כל הפרטים שהיו:
   הסקירה מקובצת ל"מצב עכשיו", "זיהוי ורשת", "מערכת וחומרה" ו"בריאות המכונה" במקום רשימה אחת
   ארוכה. פעולות לפי ההקשר: WoL ואתחול מרחוק ליד המצב, "העבר"/"עריכה" ליד הקבוצה וה-MAC. */
/* ‏#1300 (הכרעת נדב 12/10): "פתח במוניטור" במגירה פותח את המציג עצמו — אותו monitor.html שדף המוניטור
   פותח ("פתח בחלון נפרד", ‏monitorMachine) — ולא מנווט לדף. כשאין למה להתחבר — כפתור מנוטרל עם הסיבה,
   באותם מצבים שכרטיס הצפייה בדף המוניטור אומר: מתג כבוי / המחשב לא מחובר / לא נקרא (עיקרון 5). */
let MON_SETTINGS = null;   // ‏GET /monitor/settings למגירה, כשדף המוניטור עוד לא נטען; ‏null = לא נקרא
function machineMonitorBlock(m) {
  const st = MONITOR && MONITOR.settings ? MONITOR.settings : MON_SETTINGS;
  if (!st) return "מצב המוניטור לא נקרא עדיין";
  if (st.enabled !== true) return "המוניטור כבוי — מדליקים אותו בדף המוניטור";
  if (MONITOR_ROWS === null) return "מצב החיבור לא נקרא";
  const row = monitorRow(m.mac);
  if (!row || !row.online) return "המחשב לא מחובר — אין למה להתחבר";
  return "";
}
function machineDrawerHtml(m) {
  const macEnc = encodeId(m.mac), role = machineRole(m), g = machineGroup(m), net = netFor(m.mac), st = machineState(m), admin = isAdmin();
  const station = ["build", "cloner"].includes(role);
  const disks = Array.isArray(m.disks) ? m.disks.length : null;
  const redN = machineDiskFailures(m).length;
  const chipCls = { ok: "", warn: "w", err: "d" };
  const chips = `<span class="n-meta"><span class="n-chip ${chipCls[st.cls] ?? "n"}">${esc(st.text)}</span>`
    + (disks != null ? `<span class="n-chip n">${esc(disks === 1 ? "דיסק אחד" : `${disks} דיסקים`)}</span>` : "")
    + (redN ? `<span class="n-chip w">${esc(redN === 1 ? "דיסק נכשל בשיכפול הקודם" : `${redN} נכשלו בשיכפול הקודם`)}</span>` : "") + `</span>`;   // #1580: orange
  const sub = [esc(ROLE_HE[role] || role), net && net.ip ? `<span class="mono">${esc(net.ip)}</span>` : "", `<span class="mono">${esc(m.mac)}</span>`].filter(Boolean).join(" · ");
  const red = machineDiskFailures(m).map((f) => {
    const n = failureSlot(m, f), log = f.ata_log || [];
    return UI.note("err", `<b>${n != null ? esc(slotLabel(m, n, null)) : "דיסק ?"} אדום</b> — <span class="mono">${esc(f.serial || "—")}</span> נכשל בכתיבה ${esc(fmtWhen(f.at))}, סיבה: ${esc(failureCauseText(f))}${f.error ? ` (${esc(f.error)})` : ""}. התחנה תסמן אותו באדום לפני כל סבב.${admin ? ` ${UI.link("נקה אחרי החלפה", `clearDiskFailure(${Number(f.id)})`)}` : ""}${log.length ? ` <details class="inline"><summary>${log.length} שורות קרנל</summary><pre class="ata-log">${esc(log.join("\n"))}</pre></details>` : ""}`);
  }).join("");
  // פעולות על המכונה עצמה — ליד המצב שלה. לתחנת כיתה: הסבר, לא כפתור מנוטרל.
  const power = (m.held ? UI.note("warn", `<b>${esc(HELD_HE)}</b> — ${esc(heldLine(m, m.held))} ${heldButton(m)}`) : "")   // #2031
    + (station
    ? (admin ? `<div class="grp-acts"><button class="btn sm" onclick="wakeMachine('${macEnc}')">WoL</button><button class="btn sm" onclick="rebootMachine('${macEnc}')">אתחול מרחוק</button></div>` : "")
    : `<div class="cap" title="v2">WoL ואתחול מרחוק — לתחנות כיתה ב-v2</div>`);
  const now = UI.kv([
    ["מה המכונה עושה", UI.status(st.cls, st.text)],
    ["שלב אתחול אחרון", net && net.boot ? bootCell(net.boot) : `<span class="muted">${NET ? "לא נרשם פירור" : "לא נקרא"}</span>`],
    ["נראה לאחרונה", esc(NET ? seenAgo(net && net.last_seen) : "לא נקרא")],
  ]);
  const ident = UI.kv([
    ["קבוצה", (g ? esc(g.label) : `<span class="muted">ללא קבוצה</span>`) + (admin ? ` ${UI.link("העבר", `moveMachineSheet('${macEnc}')`)}` : "")],
    ["MAC", `<span class="mono">${esc(m.mac)}</span>` + (admin ? ` ${UI.link("עריכה", `editMachineMac('${macEnc}')`)}` : "")],
    ["IP אחרון", net && net.ip ? `<span class="mono">${esc(net.ip)}</span>` : `<span class="muted">—</span>`],
    ["כרטיס רשת", esc(nicSpeedText(m.nic_speed)) + (admin ? ` ${UI.link("שנה", `editNicSpeed('${macEnc}')`)}` : "")],
  ].concat(m.note ? [["הערה", esc(m.note)]] : []));
  const inv = m.inventory, dmi = (inv && inv.dmi) || {};
  let drivers = `<span class="muted">דף הדרייברים לא נטען</span>`;
  if (typeof DRIVERS !== "undefined" && Array.isArray(DRIVERS)) {
    const pk = DRIVERS.filter((p) => (p.matches || []).includes(m.mac));
    drivers = pk.length ? `${pk.length} ${pk.length === 1 ? "חבילה" : "חבילות"} — ${esc(pk.map((p) => p.name).join(", "))}` : `<span class="muted">אין חבילה מתאימה</span>`;
  }
  const sys = [
    ["קושחה", firmwareHtml(m)],
    ["סוכן", m.agent_version == null ? `<span class="muted">לא דווח</span>` : m.agent_version === "unknown" ? `<span class="muted">לא ידוע — סוכן ישן</span>` : `<span class="mono">${esc(m.agent_version)}</span>`],
  ];
  const hw = UI.kv(inv == null ? sys.concat([["חומרה", `<span class="muted">המכונה מעולם לא דיווחה על חומרה (סוכן ישן).</span>`]]) : sys.concat([
    ["דגם", esc([dmi.sys_vendor, dmi.product_name].filter(Boolean).join(" ") || "—") + (dmi.product_version ? ` · ${esc(dmi.product_version)}` : "")],
    ["לוח", esc(dmi.board_name || "—")],
    ["TPM", inv.tpm == null ? "לא נבדק" : inv.tpm.present ? `יש${inv.tpm.version ? " · " + esc(inv.tpm.version) : ""}` : "אין"],
    ["PCI רשת/אחסון", (inv.pci || []).map((p) => `<span class="mono">${esc(p)}</span>`).join(" · ") || "—"],
    ["דרייברים מתאימים", drivers],
  ]));
  const grp = (title, body) => `<div class="mgrp"><div class="grp">${title}</div>${body}</div>`;
  const h = MCH.history[m.mac];
  const history = Array.isArray(h) && h.length
    ? UI.timeline(h.slice(0, 20).map((r) => ({ t: isToday(r.ts) ? fmtClock(r.ts) : fmtDate(r.ts), cls: journalCls(r), text: esc(r.label || r.event || "") + (r.text ? " — " + esc(r.text) : ""), who: r.user || "" })))
    : h && h.error ? UI.note("warn", "היומן לא נקרא: " + esc(h.error))
    : `<div class="muted">${Array.isArray(h) ? "אין אירועים ביומן למחשב הזה (20 האחרונים)." : "טוען את היומן…"}</div>`;
  // פס הפעולות (drawerEnhance): "הסרה מהרישום…" אדום ומופרד · "שינוי שם" · "פתח במוניטור" ראשי (בנייה/שיכפול) —
  // פותח את המציג של המכונה (monitorMachine), או מנוטרל עם הסיבה.
  const block = admin && station ? machineMonitorBlock(m) : "";
  const monBtn = !(admin && station) ? "" : block
    ? `<button class="btn primary" disabled title="${esc(block)}">פתח במוניטור</button>`
    : `<button class="btn primary" onclick="monitorMachine('${macEnc}')">פתח במוניטור</button>`;
  const actions = `<div class="acts">${admin ? `<button class="btn" onclick="renameMachine('${macEnc}')">שינוי שם</button>` : ""}${monBtn}</div>`;
  const foot = admin ? `<div style="border-top:1px solid var(--hair);padding-top:12px;display:flex;gap:6px"><button class="btn sm danger" onclick="removeMachine('${macEnc}')">הסרה מהרישום…</button></div>` : "";
  return `<div class="page drw"><div class="obj-sub">${sub}${chips}</div>${actions}${red}${machineCaptureWarningHtml(m.mac)}${machineRestoreWarningHtml(m.mac)}`
    + `<div><div class="sec">מצב עכשיו</div>${now}${power}${block ? `<div class="cap mon-why">מוניטור: ${esc(block)}</div>` : ""}</div>`
    + grp("זיהוי ורשת", ident)
    + grp(`מערכת וחומרה${m.inventory_seen_at ? ` <small>(${esc(ago(m.inventory_seen_at))})</small>` : ""}`, hw)
    + grp(`בריאות המכונה${m.probe_seen_at ? ` <small>(נדגם ${esc(ago(m.probe_seen_at))})</small>` : ""}`, machineVerdictsHtml(m) + machineHealthHtml(m))
    + grp("נתונים שנאספו", machineDataHtml(m))   // #1882-#1889
    + `<div><div class="sec" data-cnt="${station ? drawerSlots(m).length : disks ?? ""}">דיסקים</div>${station ? diskMapHtml(m, admin) : diskBoxesHtml(m)}</div>`
    + (Array.isArray(m.disks) && m.disks.length ? `<div><div class="sec">SMART</div>${m.disks.map((d, i) => `<div class="mgrp"><div class="grp">${esc(slotLabel(m, d.port != null ? Number(d.port) : i + 1, d))}</div>${smartAttrsHtml(m.mac, d)}</div>`).join("")}</div>` : "")   // #1859
    + `<div><div class="sec" data-cnt="${Array.isArray(h) && h.length ? Math.min(h.length, 20) : ""}">היסטוריה</div>${history}</div>${foot}</div>`;
}
/* ‏#1882–#1889: מה שהשרת אסף על המכונה (‏GET /machines/{mac}/data). מפתח חסר/null = "לא נקרא", לא "אין". */
const MACHINE_DATA = {};
function loadMachineData(mac, rerender) {
  if (MACHINE_DATA[mac]) return;
  MACHINE_DATA[mac] = { loading: true };
  api("/machines/" + encodeId(mac) + "/data").then((v) => { MACHINE_DATA[mac] = v; }, (e) => { MACHINE_DATA[mac] = { error: e.message }; }).then(rerender);
}
const AGENT_LOG_REASON_HE = { failed: "כשל", round_end: "סוף סבב" };
/* ‏#1882: הלוג של המכונה מסוף כל סבב — מתי, למה, והכשלים המוכרים שזוהו בו (מידע בלבד). */
function agentLogsHtml(logs) {
  if (!Array.isArray(logs)) return `<span class="muted">לא נקרא</span>`;
  if (!logs.length) return `<span class="muted">לא התקבל לוג מהמכונה</span>`;
  return logs.slice(0, 5).map((l) => `<span class="mono">${esc(fmtWhen(l.at))}</span> · ${esc(AGENT_LOG_REASON_HE[l.reason] || l.reason)} · ${esc(String(l.lines))} שורות${l.truncated ? " (הזנב)" : ""} ${UI.link("הצג", `showAgentLog(${Number(l.id)})`)}`
    + (Array.isArray(l.signatures) ? l.signatures.map((g) => `<br>${UI.status("warn", esc(g.he))}`).join("") : "")).join("<br>");
}
function machineDataHtml(m) {
  const d = MACHINE_DATA[m.mac];
  if (!d || d.loading) return `<div class="muted">טוען…</div>`;
  if (d.error) return UI.note("warn", "הנתונים לא נקראו: " + esc(d.error));
  return UI.kv([["לוג המכונה", agentLogsHtml(d.agent_logs)], ["מעבד בגל", cpuWavesHtml(d.cpu)], ["DHCP/PXE מאז ההערה", pxeHtml(d.pxe)],
    ["היסטוריית IP", ipHistoryHtml(d.ip_history)], ["כרטיס רשת לאורך זמן", nicHistoryHtml(d.nic_history)], ["זריקות בגלים", waveDropsHtml(d.wave_drops)]]);
}
/* ‏#1885: מעבד, throttle וטמפ' בגלים האחרונים. ‏null בשדה = לא נמדד ("—"), לא 0. */
function cpuWavesHtml(rows) {
  if (!Array.isArray(rows)) return `<span class="muted">לא נקרא</span>`;
  if (!rows.length) return `<span class="muted">לא דווח באף גל</span>`;
  const n = (v, u = "") => v == null ? "—" : `${v}${u}`;
  return rows.slice(0, 3).map((c) => `<span class="mono">${esc(fmtWhen(c.at))}</span> · עומס ${esc(n(c.busy_pct, "%"))} · iowait ${esc(n(c.iowait_pct, "%"))} · מקס' ${esc(n(c.temp_max_c, "°C"))} · `
    + (c.throttled ? UI.status("warn", `throttle +${c.throttle_delta} — בדקו אוורור`) : c.throttle_delta == null ? `<span class="muted">throttle לא נקרא</span>` : "בלי throttle")).join("<br>");
}
/* ‏#1887: מה ש-dnsmasq ראה מאז ההערה האחרונה — "לא שלח DHCP" / "קיבל DHCP ולא טען" / "טען ולא הגיע". */
const PXE_KIND_HE = { discover: "DISCOVER", offer: "OFFER", request: "REQUEST", ack: "ACK", tftp: "TFTP", tftp_error: "שגיאת TFTP" };
function pxeHtml(p) {
  if (!p) return `<span class="muted">לא נקרא</span>`;
  const head = p.state === "hello" ? UI.status("ok", p.he) : p.state === "unchecked" ? `<span class="muted">${esc(p.he)}</span>` : UI.status("warn", p.he);
  const ev = (p.events || []).slice(-6).map((e) => `<span class="mono">${esc(fmtClock(e.at))}</span> ${esc(PXE_KIND_HE[e.kind] || e.kind)}${e.detail ? " " + esc(e.detail) : ""}`).join(" · ");
  return head + (ev ? `<br><span class="cap">${ev}</span>` : "");
}
/* ‏#1888: היסטוריית רשת — IP שהשתנה בכתום; מונה שלא נקרא "—", לא 0. */
function ipHistoryHtml(rows) {
  if (!Array.isArray(rows)) return `<span class="muted">לא נקרא</span>`;
  if (!rows.length) return `<span class="muted">לא נרשם</span>`;
  return rows.slice(0, 5).map((r) => `<span class="mono">${esc(fmtWhen(r.at))}</span> ` + (r.previous ? UI.status("warn", `${r.previous} ← ${r.ip}`) : `<span class="mono">${esc(r.ip)}</span>`)).join("<br>");
}
function nicHistoryHtml(rows) {
  if (!Array.isArray(rows)) return `<span class="muted">לא נקרא</span>`;
  if (!rows.length) return `<span class="muted">לא נדגם</span>`;
  const n = (v) => v == null ? "—" : String(v);
  return rows.slice(0, 5).map((r) => `<span class="mono">${esc(fmtWhen(r.at))}</span> <span class="mono">${esc(r.iface)}</span> ${esc(n(r.speed_mbps))}Mb/s · CRC ${esc(n(r.rx_crc_errors))} · dropped ${esc(n(r.rx_dropped))}`).join("<br>");
}
function waveDropsHtml(rows) {
  if (!Array.isArray(rows)) return `<span class="muted">לא נקרא</span>`;
  if (!rows.length) return `<span class="muted">לא היה בגל</span>`;
  const n = (v) => v == null ? "לא נקרא" : String(v);
  return rows.slice(0, 5).map((r) => `<span class="mono">${esc(fmtWhen(r.at))}</span> rcvbuf ${esc(n(r.rcvbuf))} · inerrors ${esc(n(r.inerrors))}`).join("<br>");
}
async function showAgentLog(id) {
  try {
    const v = await api("/agent-logs/" + Number(id));
    openModalContent(`לוג המכונה · ${fmtWhen(v.at)}`, v.text == null ? UI.note("warn", "הרשומה פגומה ולא נקראה") : `<pre class="ata-log" dir="ltr">${esc(v.text)}</pre>`, "סגירה");
  } catch (e) { toast(e.message); }
}
function openMachineDetail(mac, tab = "") {
  if (!isAdmin()) return;
  try { mac = decodeURIComponent(mac); } catch (e) {}
  const m = findMachine(mac);
  if (!m) { toast("מחשב לא נמצא"); return; }
  openDrawer(machineName(m) || m.mac, machineDrawerHtml(m));
  if (tab === "disks") {   // ‏#1485: "חריצים" — ישר ללשונית "דיסקים"
    const t = [...document.querySelectorAll("#drawerTabs .n-tab")].findIndex((b) => /^דיסקים/.test(b.textContent || ""));
    if (t >= 0) selectDrawerTab(t);
  }
  const gen = drawerGeneration;
  // ‏#1300: מצב מתג המוניטור — לכפתור "פתח במוניטור" (דף המוניטור, אם נטען, כבר נשא אותו)
  if (!(MONITOR && MONITOR.settings) && !MON_SETTINGS && ["build", "cloner"].includes(machineRole(m)))
    api("/monitor/settings").then((v) => { MON_SETTINGS = v; if (gen === drawerGeneration) setDrawerBody(machineDrawerHtml(m)); }, () => {});
  if (["build", "cloner"].includes(machineRole(m)) || Array.isArray(m.disks)) loadSmartAttrs(m.mac, () => { if (gen === drawerGeneration) setDrawerBody(machineDrawerHtml(m)); });   // #1859
  delete MACHINE_DATA[m.mac];   // #1882-#1889: נטען מחדש בכל פתיחה — הנתונים מתעדכנים בכל סבב
  loadMachineData(m.mac, () => { if (gen === drawerGeneration) setDrawerBody(machineDrawerHtml(m)); });
  if (MCH.history[m.mac]) return;
  api("/journal?machine=" + encodeURIComponent(machineName(m) || m.mac) + "&limit=20")
    .then((rows) => { MCH.history[m.mac] = rows; })
    .catch((e) => { MCH.history[m.mac] = { error: e.message }; })
    .then(() => { if (gen === drawerGeneration) setDrawerBody(machineDrawerHtml(m)); });
}
function groupOptions(selected) {
  return machineGroupsInOrder().map((g) => ({ value: g.id, label: g.label, selected: g.id === selected }));
}
/* ‏#1997 (נדב, 22/10): שם מחשב או שרת הוא ה-hostname. הכלל נאכף בשרת (hostname_rule); כאן ההסבר בלבד. */
const MACHINE_NAME_RULE = "שם מחשב — באנגלית, עד 15 תווים: אותיות, ספרות ומקף, לא בהתחלה ולא בסוף";
function renameMachine(mac) {
  const m = findMachine(mac); if (!m || !isAdmin()) return;
  const classroom = machineRole(m) === "classroom";
  sheet({ title: "שינוי שם", sub: m.mac, fields: [{ id: "name", label: classroom ? "סיומת (01–99 או INS)" : "שם מחשב", value: machineName(m), dir: "ltr",
    help: classroom ? "" : MACHINE_NAME_RULE }],
    onSubmit: async (v) => { await put("/machines/" + encodeId(m.mac), { name: v.name }); MCH.history = {}; await loadMachines(); openMachineDetail(m.mac); } });
}
/* #977 §4: reboot is a request that rides the machine's next hello (power_action), like the room
   power-off (#980). The agent reboots only while it waits; a busy machine is refused by the server.
   The answer is "requested", not "rebooted" -- "connected" changes when it really comes back. */
function rebootMachine(mac) {
  const m = findMachine(mac); if (!m || !isAdmin()) return;
  const name = machineName(m) || m.mac;
  if (m.local_copy) return localCopyPowerSheet(name, "אתחול", async () => {   /* #1367 (Nadav 14/10) */
    await post("/machines/" + encodeId(m.mac) + "/reboot", { confirm_name: m.suffix, confirm_local_copy: true });
    toast(`בקשת אתחול נשלחה ל-${name}`, 6000); });
  sheet({ title: `לאתחל את ${name}?`, sub: m.mac, danger: true, submitLabel: "אתחול",
    note: impactList([["w", "המחשב יאותחל ב-hello הבא שלו (עד כ-15 שניות), רק אם הוא ממתין; מחשב שלא יענה תוך 2 דקות לא יאותחל"],
      ["w", "מחשב עם קליטה פתוחה, או משכפל בזמן סבב חדר — השרת מסרב"], ["ok", "האתחול עובר דרך הסוכן, לא ניתוק חשמל"]]),
    onSubmit: async () => { await post("/machines/" + encodeId(m.mac) + "/reboot", { confirm_name: m.suffix }); toast(`בקשת אתחול נשלחה ל-${name} — "מחובר" יתעדכן כשהוא יעלה מחדש`, 6000); } });
}
/* #977 §5: a replaced network card. The row moves; the history stays on the old MAC (it belongs to the old card). */
function editMachineMac(mac) {
  const m = findMachine(mac); if (!m || !isAdmin()) return;
  sheet({ title: "עריכת MAC", sub: machineName(m) || m.mac,
    fields: [{ id: "mac", label: "כתובת MAC", value: m.mac, dir: "ltr", help: "כרטיס רשת שהוחלף. ההיסטוריה (דיסקים, חומרה, יומן) נשארת על הכתובת הקודמת; המחשב יזוהה בכתובת החדשה מהאתחול הבא." }],
    onSubmit: async (v) => {
      const r = await put("/machines/" + encodeId(m.mac), { mac: v.mac });
      dhcpNotice(r); MCH.sel.delete(m.mac); MCH.history = {};
      await loadMachines(); openMachineDetail(r.mac);
    } });
}
function moveMachineSheet(mac) {
  const m = findMachine(mac); if (!m || !isAdmin()) return;
  sheet({ title: "העבר לקבוצה", sub: machineName(m) || m.mac, fields: [{ id: "group_id", type: "select", label: "קבוצה", options: groupOptions(machineGroupId(m)), value: machineGroupId(m) }],
    onSubmit: async (v) => { await put("/machines/" + encodeId(m.mac), { group_id: v.group_id }); await loadMachines(); openMachineDetail(m.mac); } });
}
/* ‏#1485 (נדב 16/10): לשונית "דיסקים" בפאנל המכונה (המוקאפ: דיסק | גודל | SMART) + עמודת "חיבור".
   ‏#2001 (נדב 22/10): העריכה עברה לחלון אחד, "מיפוי חיבורים" (openSlotMap) — הלשונית מציגה, והכפתור
   שמתחתיה פותח את החלון. */
/* תג SMART כמו במוקאפ (‏pill → ‏.n-chip: ירוק / w כתום / d אדום / n אפור — "לא נבדק" אינו ירוק). */
function smartPill(d, fail) {
  const chip = (cls, text) => `<span class="n-chip${cls ? " " + cls : ""}">${esc(text)}</span>`;
  if (d.smart === "fail") return chip("d", d.smart_reason === "readonly" ? "קריאה בלבד" : "SMART תקול");     // #1580: red, never written; #1742
  if (d.write_mark === "incomplete") return chip("w", WRITE_MARK_HE);   // #1798: not a source; a target
  if (fail || d.smart === "failed_last") return chip("w", "נכשל בשיכפול הקודם");
  if (d.smart === "ok") return chip("", "תקין");
  if (d.smart === "warn") return chip("w", "אזהרה");
  return chip("n", "לא נבדק");
}
/* ‏#1375 (נדב 17/10): בלשונית "דיסקים" חיבור נקרא במספר המגירה — "דיסק 1", לא "SATA 0" — כמו "דיסק 1/2/3"
   בשאר המוצר (המוסכמה: מגירה k = SATA k-1). NVMe נשאר בכתובתו. כונן מחוץ למיפוי וכונן CD/DVD נשארים
   "SATA n" (‏rowConn) — לעולם לא מספר מגירה מנוחש (#1485). ‏connText (‏"SATA n") נשאר ב-title. */
function tabConnText(key) { const s = /^sata:(\d+)$/.exec(key || ""); return s ? `דיסק ${Number(s[1])}` : connText(key); }
function diskMapHtml(m, admin) {
  const rows = driveRows(m, m.disks), fails = machineDiskFailures(m);
  const body = rows.map((r) => {
    const d = r.disk, fail = r.optical ? null : rowFailure(m, r, fails);
    const conn = `<span class="mono"${r.n != null && r.key && /^sata:/.test(r.key) ? ` title="${esc(connText(r.key))}"` : ""}>${esc(r.n != null && r.key ? tabConnText(r.key) : rowConn(r))}</span>`;
    const size = r.optical ? `<span class="muted">${esc(r.optical.model || "כונן אופטי")}</span>`
      : d ? `<span class="mono">${ltr(marketedDiskSize(d.size_bytes))}</span> <span class="cap">${esc(diskBrand(d.model))}</span>`
      : `<span class="muted">${m.disks == null ? "לא דווח" : "ריק"}</span>`;
    const smart = r.optical ? `<span class="muted">—</span>` : d ? smartPill(d, fail) : "";
    const acts = admin && fail ? UI.link("נקה", `clearDiskFailure(${Number(fail.id)})`) : "";
    const cls = r.optical ? "opt" : r.n == null ? "unmapped" : !d ? "vacant" : "";
    return `<tr class="${cls}" data-slot="${r.n ?? ""}"><td><b>${esc(rowLabel(m, r))}</b></td><td>${conn}</td><td>${size}</td><td>${smart}</td><td class="acts-cell">${acts}</td></tr>`;
  }).join("");
  const table = rows.length ? `<table class="n-tbl disk-map"><thead><tr><th>דיסק</th><th>חיבור</th><th>גודל</th><th>SMART</th><th><span class="sr">פעולות</span></th></tr></thead><tbody>${body}</tbody></table>`
    : UI.note("", m.disks == null ? "המכונה מעולם לא דיווחה על כוננים." : `המכונה דיווחה — ואין בה אף כונן: ${zeroDisksText(m)}.`);
  if (!admin) return table;
  const src = SLOT_MAP_SOURCE_HE[m.slot_map_source] || "";
  return table + `<div class="map-tools"><button class="btn sm" type="button" onclick="openSlotMap('${encodeId(m.mac)}')">מיפוי חיבורים…</button>${src ? `<span class="map-src">${esc(src)}</span>` : ""}</div>`
    + `<p class="map-help">איזה חיבור במחשב פעיל (מקור או יעד בסבב), ואיזה שם הוא מקבל בכל המסכים. כונן CD/DVD אינו חריץ דיסק ולעולם אינו מקור קליטה או יעד.</p>`;
}
const SLOT_MAP_SOURCE_HE = { machine: "מיפוי של המחשב הזה", model: "מיפוי של כל מחשבי הדגם", default: "ברירת המחדל — אין מיפוי שמור" };

/* ---------- ‏#2001: חלון "מיפוי חיבורים" (המוקאפ המאושר, נדב 22/10) ----------
   שורה לכל חיבור שהמחשב מציג (SATA לפי הפורט, NVMe, ריקים, CD/DVD); לכל שורה: מה מחובר עכשיו, שם, פעיל.
   ההכרעות: (1) השם מחליף את "דיסק N"; שורה פעילה בלי שם = "דיסק N" לפי הסדר, והסדר הוא סדר החיבורים.
   (2) "חל על": המחשב הזה / כל מחשבי הדגם (‏DMI product), ברירת מחדל — כל מחשבי הדגם. (3) בלי מיפוי
   שמור: חיבור שיש בו דיסק פעיל, ריק כבוי; מה שסומן פעיל נשאר פעיל גם ריק. (4) הוספה ידנית — סוג ומספר,
   בלי PCI (‏NVMe n → ‏nvme#n, והשרת משלים את הכתובת כשנראה בו כונן). שמירה אחת: PUT /machines/{mac}/slot-map. */
let SMAP = null;
function smapOrder(k) {
  const s = /^sata:(\d+)$/.exec(k), o = /^nvme#(\d+)$/.exec(k);
  return s ? [0, Number(s[1]), ""] : /^nvme:/.test(k) ? [1, 0, k] : [2, o ? Number(o[1]) : 0, ""];
}
function smapCmp(a, b) { const x = smapOrder(a), y = smapOrder(b); return x[0] - y[0] || x[1] - y[1] || (x[2] < y[2] ? -1 : x[2] > y[2] ? 1 : 0); }
/* "SATA 0" / "NVMe 1" — NVMe לפי מקומו בין כתובות ה-PCI שהמחשב מציג, כמו שהשרת משלים nvme#n. */
function smapConnName(k, nvmes) {
  const s = /^sata:(\d+)$/.exec(k), o = /^nvme#(\d+)$/.exec(k);
  if (s) return `SATA ${Number(s[1]) - 1}`;
  if (o) return `NVMe ${o[1]}`;
  return `NVMe ${nvmes.indexOf(k) + 1}`;
}
function smapModelCount(m) {
  return (MACHINES || []).filter((x) => x.slot_model && x.slot_model === m.slot_model).length;
}
/* מה מוצג כשנפתח: מה שחל על המחשב עכשיו — המיפוי שלו, של הדגם, או ברירת המחדל (הכרעה 3). */
function smapSource(m) {
  if (m.slot_map_source === "machine") return { slots: drawerSlots(m), names: m.slot_names || {} };
  if (m.model_slot_map) return { slots: m.model_slot_map.slots, names: { ...m.model_slot_map.names, ...(m.slot_names || {}) } };
  return { slots: (m.disks || []).map(connKey).filter(Boolean), names: m.slot_names || {}, proposal: true };
}
function smapBuild(m) {
  const src = smapSource(m), disks = Array.isArray(m.disks) ? m.disks : [], optical = Array.isArray(m.optical) ? m.optical : [];
  const ports = [...disks.map((d) => d.port), ...optical.map((o) => o.port), ...src.slots.map((k) => (/^sata:(\d+)$/.exec(k) || [])[1])]
    .map(Number).filter((n) => Number.isInteger(n) && n > 0);
  const keys = Array.from({ length: ports.length ? Math.max(...ports) : 0 }, (_, i) => `sata:${i + 1}`);
  const add = (k) => { if (k && !keys.includes(k)) keys.push(k); };
  disks.forEach((d) => add(connKey(d)));
  src.slots.forEach(add);
  keys.sort(smapCmp);
  const rows = keys.map((key) => {
    const disk = disks.find((d) => connKey(d) === key) || null;
    const opt = disk ? null : optical.find((o) => o && o.port != null && `sata:${Number(o.port)}` === key) || null;
    const legacy = disk && disk.serial ? src.names[`serial:${disk.serial}`] : null;   // #704: שם ישן של NVMe לפי סידורי
    return { key, disk, optical: opt, on: !opt && src.slots.includes(key), name: src.names[key] || legacy || "", manual: /^nvme#/.test(key) };
  });
  return { rows, proposal: !!src.proposal };
}
function openSlotMap(macEnc) {
  let mac = macEnc; try { mac = decodeURIComponent(macEnc); } catch (e) {}
  const m = findMachine(mac); if (!m || !isAdmin()) return;
  const built = smapBuild(m);
  SMAP = { mac: m.mac, scope: m.slot_model && m.slot_map_source !== "machine" ? "model" : "machine",
    rows: built.rows, base: JSON.stringify(built.rows.map((r) => [r.key, r.on, r.name])), proposal: built.proposal,
    add: null, err: null, note: "" };
  const when = m.disks_reported_at ? ` · דיווח אחרון ${seenAgo(m.disks_reported_at)}` : "";
  openModalContent("מיפוי חיבורים", `<div class="smap" id="smap"></div>`, "שמירה", "smapSave()", false,
    /* כל חלק מבודד (FSI…PDI): שם דגם ו-MAC לטיניים לא הופכים את סדר השורה */
    [machineName(m) || m.mac, m.slot_model, m.mac].filter(Boolean).map((x) => `⁨${x}⁩`).join(" · ") + when, "wide");
  const reset = document.createElement("button");
  reset.type = "button"; reset.className = "btn ghost"; reset.id = "smap-reset"; reset.textContent = "החזר לברירת המחדל";
  reset.onclick = () => smapReset();
  const cancel = document.getElementById("modalCancel");
  if (cancel && cancel.parentNode && cancel.parentNode.insertBefore) cancel.parentNode.insertBefore(reset, cancel);
  smapDraw();
}
function smapMachine() { return SMAP ? findMachine(SMAP.mac) : null; }
/* "דיסק N" (placeholder) של כל שורה פעילה — לפי הסדר; ‏null לשורה כבויה. */
/* נדב 22/10: בלי מיפוי שמור (ההצעה, לפני שינוי) — המוסכמה של השרת: SATA 0 = דיסק 1, SATA 1 = דיסק 2…
   (‏sata:k = ‏ataK = "SATA k-1" = דיסק k), ו-NVMe אחרי ה-SATA הגבוה בטבלה. מיפוי שנשמר או נערך — לפי הסדר (הכרעה 1). */
function smapNumbers() {
  if (SMAP.proposal && !smapDirty()) {
    const top = Math.max(0, ...SMAP.rows.map((r) => Number((/^sata:(\d+)$/.exec(r.key) || [])[1] || 0)));
    let nv = 0;
    return SMAP.rows.map((r) => { const k = /^sata:(\d+)$/.exec(r.key); if (!k) nv += 1; return r.on ? (k ? Number(k[1]) : top + nv) : null; });
  }
  let n = 0; return SMAP.rows.map((r) => r.on ? ++n : null);
}
function smapLabels() { const nums = smapNumbers(); return SMAP.rows.map((r, i) => r.on ? (r.name.trim() || `דיסק ${nums[i]}`) : null); }
function smapDirty() {
  const base = new Map(JSON.parse(SMAP.base).map(([k, on, name]) => [k, [on, name]]));
  return SMAP.rows.filter((r) => { const b = base.get(r.key); return !b || b[0] !== r.on || (r.on && b[1] !== r.name.trim()); }).length;
}
/* שורה ששמה (או ה"דיסק N" שלה) כבר תפוס — ‏{שורה: השורה הראשונה עם אותו שם}. */
function smapDupes() {
  const labels = smapLabels(), seen = new Map(), out = new Map();
  labels.forEach((l, i) => { if (l == null) return; const k = l.toLocaleLowerCase(); if (seen.has(k)) out.set(i, seen.get(k)); else seen.set(k, i); });
  return out;
}
function smapSavedHere(m) { return SMAP.scope === "machine" ? m.slot_map_source === "machine" : !!m.model_slot_map; }
function smapFoot() {
  const m = smapMachine(); if (!m || !SMAP) return;
  const active = SMAP.rows.filter((r) => r.on).length, dirty = smapDirty();
  const hint = document.getElementById("modalHint");
  if (hint) hint.innerHTML = `<span class="smap-n">${active} פעילים${SMAP.proposal && !dirty ? " · ברירת המחדל" : ""}</span>${dirty ? `<span class="smap-dirty"> · ${dirty === 1 ? "שינוי אחד לא נשמר" : `${dirty} שינויים לא נשמרו`}</span>` : ""}`;
  const reset = document.getElementById("smap-reset");
  if (reset) reset.hidden = !smapSavedHere(m);   // אין מה להחזיר — לא כפתור מנוטרל (README §8)
}
function smapDraw() {
  const m = smapMachine(), el = document.getElementById("smap"); if (!m || !el || !SMAP) return;
  const nvmes = SMAP.rows.map((r) => r.key).filter((k) => /^nvme:/.test(k));
  const nums = smapNumbers(), dupes = SMAP.err === "dup" ? smapDupes() : new Map(), conn = (i) => smapConnName(SMAP.rows[i].key, nvmes);
  const count = smapModelCount(m);
  const scope = `<div class="smap-scope"><span id="smap-scope-l">חל על</span><div class="n-seg" role="group" aria-labelledby="smap-scope-l">`
    + `<button type="button" aria-pressed="${SMAP.scope === "machine"}" onclick="smapScope('machine')">המחשב הזה</button>`
    + (m.slot_model ? `<button type="button" aria-pressed="${SMAP.scope === "model"}" onclick="smapScope('model')">כל ה-<bdi>${esc(m.slot_model)}</bdi> (${count === 1 ? "מחשב אחד" : `${count} מחשבים`})</button>` : "")
    + `</div>${m.slot_model ? "" : `<span class="smap-why">המחשב לא דיווח דגם — אפשר לשמור רק לו</span>`}</div>`;
  const note = SMAP.note ? `<div class="smap-note err" role="alert">${SMAP.note}</div>`
    : SMAP.proposal && !smapDirty() ? `<div class="smap-note"><b>אין מיפוי שמור</b> — זו ברירת המחדל: SATA 0 = דיסק 1, SATA 1 = דיסק 2, וכן הלאה; כל חיבור שיש בו דיסק פעיל. שינוי ושמירה יוצרים מיפוי, ובו שורה בלי שם היא "דיסק N" לפי הסדר.</div>` : "";
  const rows = SMAP.rows.map((r, i) => {
    const pci = /^nvme:/.test(r.key) ? `<span class="pci mono">${esc(r.key.slice(5).replace(/^0000:/, ""))}</span>` : "";
    const head = `<td class="conn"><b>${esc(conn(i))}</b>${pci}</td>`;
    if (r.optical) return `<tr class="off">${head}<td class="now"><span class="model">כונן CD/DVD · <bdi>${esc(r.optical.model || "")}</bdi></span></td><td class="name"><span class="na">לא דיסק — לא יעד ולא מקור</span></td><td><span class="n-sw smap-sw na-sw" aria-hidden="true"><span class="track"></span></span></td></tr>`;   // לא מתג: אין מה להדליק
    const d = r.disk;
    const now = d ? `<span class="model">${esc(d.model || "—")}</span> · <span class="sz">${ltr(marketedDiskSize(d.size_bytes))}</span>`
      : `<span class="vac">${m.disks == null ? "לא דווח" : "ריק"}</span>${r.manual ? `<span class="tag">נוסף ידנית · לא נמצא במחשב</span>` : ""}`;
    const bad = dupes.has(i);
    const name = !r.on ? `<span class="na">לא בשימוש</span>`
      : `<input class="n-in smap-in${bad ? " bad" : ""}" type="text" maxlength="24" value="${esc(r.name)}" placeholder="דיסק ${nums[i]}" aria-label="השם של ${esc(conn(i))}"${bad ? ` aria-invalid="true" aria-describedby="smap-e${i}"` : ""} oninput="smapName(${i},this.value)">`
        + (bad ? `<div class="ferr" id="smap-e${i}">השם כבר בשימוש ב-${esc(conn(dupes.get(i)))}</div>` : "");
    return `<tr class="${r.on ? "" : "off"}${r.manual ? " new" : ""}" data-key="${esc(r.key)}">${head}<td class="now">${now}</td><td class="name">${name}</td><td><label class="n-sw smap-sw"><input type="checkbox" role="switch"${r.on ? " checked" : ""} aria-label="${esc(conn(i))} פעיל" onchange="smapToggle(${i})"><span class="track"></span></label></td></tr>`;
  }).join("");
  const addRow = SMAP.add ? "" : `<tr class="addrow"><td colspan="4"><button type="button" class="link" onclick="smapAddOpen()">+ הוסף חיבור</button><span class="na">לחיבור שהמחשב לא מציג</span></td></tr>`;
  el.innerHTML = scope + note
    + `<table class="smap-t"><thead><tr><th>חיבור</th><th>מחובר עכשיו</th><th>שם</th><th>פעיל</th></tr></thead><tbody>${rows}${addRow}</tbody></table>`
    + (SMAP.add ? smapAddHtml() : "")
    + `<p class="smap-help">פעיל = דיסק שמופיע בסבב, כמקור או כיעד. השם הוא מה שרואים בכל המסכים — "דיסק 1", "מגירה עליונה". בלי שם: "דיסק N" לפי הסדר כאן.</p>`;
  smapFoot();
}
function smapAddHtml() {
  const a = SMAP.add;
  return `<div class="smap-add"><div><span class="lbl" id="smap-at">סוג</span><div class="n-seg" role="group" aria-labelledby="smap-at">${["SATA", "NVMe"].map((t) => `<button type="button" aria-pressed="${a.type === t}" onclick="smapAddType('${t}')">${t}</button>`).join("")}</div></div>`
    + `<div><label class="lbl" for="smap-anum">מספר</label><input class="n-in" id="smap-anum" type="number" min="${a.type === "SATA" ? 0 : 1}" max="${a.type === "SATA" ? 31 : 8}" value="${esc(a.num)}" oninput="SMAP.add.num=this.value"></div>`
    + `<div><label class="lbl" for="smap-aname">שם</label><input class="n-in" id="smap-aname" type="text" maxlength="24" value="${esc(a.name)}" placeholder="אפשר להשאיר ריק" oninput="SMAP.add.name=this.value" onkeydown="if(event.key==='Enter'){event.preventDefault();smapAddCommit();}"></div>`
    + `<div class="acts"><button class="btn primary" type="button" onclick="smapAddCommit()">הוסף</button><button class="btn" type="button" onclick="smapAddCancel()">ביטול</button></div>`
    + `<p class="help">לחיבור שהמחשב לא מציג עכשיו — למשל חריץ M.2 ריק, או מגירה שהבקר שלה כבוי. ${a.type === "NVMe" ? "כתובת ה-PCI נשלמת לבד כשיופיע בו דיסק. " : ""}השורה תסומן "לא נמצא במחשב" עד שיופיע בו דיסק.</p>${a.err ? `<p class="ferr" role="alert">${esc(a.err)}</p>` : ""}</div>`;
}
function smapScope(s) { if (!SMAP) return; SMAP.scope = s; SMAP.note = ""; smapDraw(); }
function smapToggle(i) { const r = SMAP && SMAP.rows[i]; if (!r || r.optical) return; r.on = !r.on; SMAP.err = null; SMAP.note = ""; smapDraw(); }
/* הקלדה אינה מציירת מחדש (הפוקוס נשאר בשדה) — רק הפס התחתון. */
function smapName(i, v) { const r = SMAP && SMAP.rows[i]; if (!r) return; r.name = v; smapFoot(); }
function smapAddOpen() { SMAP.add = { type: "SATA", num: "", name: "", err: "" }; smapDraw(); }
function smapAddType(t) { SMAP.add.type = t; SMAP.add.err = ""; smapDraw(); }
function smapAddCancel() { SMAP.add = null; smapDraw(); }
function smapAddCommit() {
  const a = SMAP.add, n = Number(a.num), sata = a.type === "SATA";
  if (String(a.num).trim() === "" || !Number.isInteger(n) || n < (sata ? 0 : 1) || n > (sata ? 31 : 8)) { a.err = sata ? "מספר SATA בין 0 ל-31" : "מספר NVMe בין 1 ל-8"; smapDraw(); return; }
  const nvmes = SMAP.rows.map((r) => r.key).filter((k) => /^nvme:/.test(k));
  const key = sata ? `sata:${n + 1}` : nvmes[n - 1] || `nvme#${n}`;
  const at = SMAP.rows.findIndex((r) => r.key === key);
  if (at >= 0) {   // החיבור כבר בטבלה — מדליקים אותו ולא מוסיפים שורה שנייה
    if (SMAP.rows[at].optical) { a.err = `${a.type} ${n} הוא כונן CD/DVD`; smapDraw(); return; }
    SMAP.rows[at].on = true; if (a.name.trim()) SMAP.rows[at].name = a.name.trim();
  } else {
    SMAP.rows.push({ key, disk: null, optical: null, on: true, name: a.name.trim(), manual: true });
    SMAP.rows.sort((x, y) => smapCmp(x.key, y.key));
  }
  SMAP.add = null; smapDraw();
}
/* מחשבים מהדגם שיש להם מיפוי משלהם — שמירה לדגם תחליף אותו (נדב 22/10: שואלים קודם). */
function smapOwnMaps(m) {
  return (MACHINES || []).filter((x) => x.slot_model && x.slot_model === m.slot_model && x.slot_map_source === "machine");
}
async function smapSave(confirmed = false) {
  const m = smapMachine(); if (!m || !SMAP) return;
  const live = SMAP.rows.filter((r) => r.on);
  if (!live.length) { SMAP.note = "<b>לא נשמר:</b> אין אף חיבור פעיל — צריך לפחות אחד."; smapDraw(); return; }
  const dupes = smapDupes();
  if (dupes.size) {
    const first = smapLabels()[[...dupes.keys()][0]];
    SMAP.err = "dup"; SMAP.note = `<b>לא נשמר:</b> שתי שורות עם אותו שם ("${esc(first)}"). כל שם צריך להיות שונה — אחרת לא יודעים לאיזה דיסק התכוונת בסבב.`;
    smapDraw(); return;
  }
  const own = SMAP.scope === "model" && !confirmed ? smapOwnMaps(m) : [];
  if (own.length) { smapAskReplace(m, own); return; }
  try { await smapPut(m, confirmed); }
  catch (e) {
    if (e.code === "machine_maps" && !confirmed) { smapAskReplace(m, [], e.message); return; }   // נתונים ישנים בדפדפן — השרת שאל
    SMAP.err = null; SMAP.note = `<b>לא נשמר:</b> ${esc(e.message)}`; smapDraw();
  }
}
async function smapPut(m, confirmed) {
  const live = SMAP.rows.filter((r) => r.on), names = {};
  live.forEach((r) => { if (r.name.trim()) names[r.key] = r.name.trim(); });
  const res = await put(`/machines/${encodeId(m.mac)}/slot-map`, { scope: SMAP.scope, slots: live.map((r) => r.key), names, ...(confirmed ? { replace_machine_maps: true } : {}) });
  const n = res && Array.isArray(res.machines) ? res.machines.length : 1;
  SMAP = null; closeModal(); await loadMachines();
  const fresh = findMachine(m.mac); if (fresh) setDrawerBody(machineDrawerHtml(fresh));
  toast(n > 1 ? `המיפוי נשמר — חל על ${n} מחשבים` : "המיפוי נשמר");
}
/* האישור — באותו עיצוב sheet. "חזרה למיפוי" פותח שוב את החלון עם העריכה כמו שהייתה. */
function smapAskReplace(m, own, msg = "") {
  const saved = SMAP;
  const head = msg || `ל-${own.length === 1 ? "מחשב אחד" : `${own.length} מחשבים`} מהדגם יש מיפוי משלהם — הוא יוחלף`;
  sheet({ title: "להחליף את המיפוי של מחשבים מהדגם?", sub: m.slot_model || "", danger: true, submitLabel: "החלף ושמור",
    note: impactList([["w", esc(head) + (own.length ? `: ${own.map((x) => esc(machineName(x) || x.mac)).join(", ")}` : "")],
      ["ok", "כל מחשבי הדגם יקבלו את המיפוי שבחלון"]]),
    extra: { label: "חזרה למיפוי", onClick: () => smapRestore(saved) },
    onSubmit: async () => { SMAP = saved; try { await smapPut(m, true); } catch (e) { SMAP = null; throw e; } } });
}
function smapRestore(saved) { openSlotMap(encodeId(saved.mac)); SMAP = saved; smapDraw(); }
async function smapReset() {
  const m = smapMachine(); if (!m || !SMAP) return;
  try {
    await put(`/machines/${encodeId(m.mac)}/slot-map`, { scope: SMAP.scope, slots: null });
    SMAP = null; closeModal(); await loadMachines();
    const fresh = findMachine(m.mac); if (fresh) setDrawerBody(machineDrawerHtml(fresh));
    toast("המיפוי חזר לברירת המחדל");
  } catch (e) { SMAP.note = `<b>לא הוחזר:</b> ${esc(e.message)}`; smapDraw(); }
}
function removeMachine(mac) {
  const m = findMachine(mac); if (!m || !isAdmin()) return;
  const name = machineName(m) || m.mac;
  sheet({ title: "להסיר מהרישום?", sub: `${name} (${m.mac})`, danger: true, submitLabel: "הסרה מהרישום",
    note: impactList([["w", "המחשב יוסר מהטבלה"], ["w", "באתחול הבא הוא ידווח כלא רשום"]]),
    onSubmit: async () => { dhcpNotice(await del("/machines/" + encodeId(m.mac))); closeDrawer(); MCH.sel.delete(m.mac); await loadMachines(); } });
}
function bulkMoveMachines() {
  const macs = [...MCH.sel]; if (!macs.length || !isAdmin()) return;
  sheet({ title: "העבר לקבוצה", sub: `${macs.length} מחשבים`, fields: [{ id: "group_id", type: "select", label: "קבוצה", options: groupOptions(""), value: "" }],
    onSubmit: async (v) => { for (const mac of macs) await put("/machines/" + encodeId(mac), { group_id: v.group_id }); MCH.sel.clear(); await loadMachines(); } });
}
/* פעולה הרסנית מאחורי חלון אישור עם הרשימה (#1270). */
function bulkRemoveMachines() {
  const macs = [...MCH.sel]; if (!macs.length || !isAdmin()) return;
  const names = macs.map((mac) => { const m = findMachine(mac); return m ? machineName(m) || mac : mac; });
  sheet({ title: `להסיר ${macs.length} מחשבים מהרישום?`, sub: names.join(" · "), danger: true, submitLabel: `הסרת ${macs.length} מחשבים`,
    note: impactList([["w", "כל מחשב יוסר מהטבלה"], ["w", "באתחול הבא כל אחד מהם ידווח כלא רשום"]])
      + `<div class="sheet-note"><ul>${macs.map((mac, i) => `<li>${esc(names[i])} (${esc(mac)})</li>`).join("")}</ul></div>`,
    onSubmit: async () => {
      for (const mac of macs) dhcpNotice(await del("/machines/" + encodeId(mac)));
      MCH.sel.clear(); await loadMachines();
    } });
}

/* ---------- + מחשב: מודאל עם שתי לשוניות — אחד / הדבקה עם תצוגה מקדימה (dry_run) ---------- */
/* קבוצה קבועה = שדה נסתר (אותו id, כדי ש-submit לא ישתנה); אחרת בחירה. */
function amGroupField(id, group, fixed, half = false) {
  if (fixed) return `<input type="hidden" id="${id}" value="${esc(fixed.id)}">`;
  return `<div class="field${half ? "" : " full"}"><label for="${id}">קבוצה</label><select id="${id}">${groupOptionsHtml(group)}</select>${half ? `<p class="form-note">קובעת את תפריט האתחול</p>` : ""}</div>`;
}
function groupOptionsHtml(selected) {
  return machineGroupsInOrder().map((g) => `<option value="${esc(g.id)}"${g.id === selected ? " selected" : ""}>${esc(g.label)}</option>`).join("");
}
function openAddMachine({ group = "", role = "", mac = "", tab = 0 } = {}) {
  if (!isAdmin()) return;
  try { group = decodeURIComponent(group); } catch (e) {}
  try { mac = decodeURIComponent(mac); } catch (e) {}
  MCH.amTab = tab;
  /* הכרעת נדב: מדף "מחשבי בנייה"/"מחשבי שיכפול" הקבוצה קבועה מהדף — בלי בחירה, והכותרת אומרת איזה מחשב. */
  const fixed = machineGroupsInOrder().find((g) => (group ? g.id === group : g.role === role) && (g.role === "build" || g.role === "cloner")) || null;
  const title = fixed && fixed.role === "build" ? "+ מחשב בנייה" : fixed && fixed.role === "cloner" ? "+ מחשב שיכפול" : "+ מחשב";
  const tabBtn = (i, label) => `<button type="button" class="tab${tab === i ? " on" : ""}" role="tab" aria-selected="${tab === i}" onclick="amTab(${i})">${label}</button>`;
  const body = `<div class="page drw am"><div class="tabs" role="tablist">${tabBtn(0, "מחשב אחד")}${tabBtn(1, "הדבקה — הרבה בבת אחת")}</div>
<div id="am-one" class="form"${tab === 1 ? " hidden" : ""}><div class="field full"><label for="am-mac">כתובת MAC</label><input id="am-mac" dir="ltr" value="${esc(mac)}" placeholder="b4:2e:99:07:1a:c4" autocomplete="off"><p class="form-note">מופיעה על מדבקת המחשב ובמסך ה-PXE</p></div><div class="field${fixed ? " full" : ""}"><label for="am-name">שם / סיומת</label><input id="am-name" autocomplete="off" dir="ltr" placeholder="${classroomsOn() ? "01–99 או INS לכיתה; שם מחשב — באנגלית, עד 15 תווים" : "שם מחשב — באנגלית, עד 15 תווים"}"><p class="form-note">${MACHINE_NAME_RULE}</p></div>${amGroupField("am-group", group, fixed, true)}</div>
<div id="am-paste" class="form"${tab === 0 ? " hidden" : ""}>${amGroupField("am-pgroup", group, fixed)}<div class="field full"><label for="am-text">שורה לכל מחשב: MAC ואז סיומת/שם</label><textarea id="am-text" rows="7" dir="ltr" placeholder="b4:2e:99:07:1a:c4 01&#10;B4-2E-99-07-1A-C5 02"></textarea></div><div class="field full"><button type="button" class="btn sm" onclick="previewMachineImport()">תצוגה מקדימה</button></div><div id="am-preview" class="stack"></div></div></div>`;
  openModalContent(title, body, "הוסף", submitAddMachine, false, fixed ? `יירשם בקבוצת ${fixed.label}` : "רישום מחשב ברשת ההפצה", "narrow");
}
function amTab(i) {
  MCH.amTab = i;
  const one = document.getElementById("am-one"), paste = document.getElementById("am-paste");
  if (one) one.hidden = i !== 0;
  if (paste) paste.hidden = i !== 1;
  document.querySelectorAll("#modalBody .tab").forEach((b, j) => { b.classList.toggle("on", j === i); b.setAttribute("aria-selected", String(j === i)); });
}
function importPreviewHtml(lines) {
  if (!lines.length) return UI.note("", "אין שורות לייבוא");
  return lines.map((l) => l.error ? UI.status("err", `${l.raw} ← ${l.error}`) : UI.status("ok", `${l.mac} ${l.suffix}`)).join("");
}
async function previewMachineImport() {
  const body = { group_id: $("#am-pgroup").value, text: $("#am-text").value, dry_run: true };
  try { const r = await post("/machines/import", body); $("#am-preview").innerHTML = importPreviewHtml(r.preview || []); }
  catch (e) { $("#am-preview").innerHTML = UI.note("err", esc(e.message)); }
}
async function submitAddMachine() {
  try {
    if (MCH.amTab === 1) {
      const r = await post("/machines/import", { group_id: $("#am-pgroup").value, text: $("#am-text").value });
      closeModal();
      dhcpNotice(r) || toast(`נשמרו ${r.saved}. נדחו ${(r.rejected || []).length}.`);
    } else {
      const name = $("#am-name").value, mac = $("#am-mac").value, group_id = $("#am-group").value;
      if (!name.trim() || !mac.trim() || !group_id) { toast("נדרשים שם, קבוצה ו-MAC"); return; }
      const r = await post("/machines", { mac, name, group_id });
      closeModal();
      dhcpNotice(r) || toast("המחשב נוסף");
    }
    await loadMachines();
  } catch (e) { toast(e.message); }
}
function openNewMachine() { openAddMachine({}); }   // הסקירה (homeAttention) קוראת לזה
function addGroupSheet() {
  if (!isAdmin() || !classroomsOn()) return;
  sheet({
    title: "כיתה חדשה", sub: "כל כיתה היא קבוצה. השם חופשי — עברית, אנגלית או מספרים.",
    fields: [
      { id: "label", label: "שם הכיתה", placeholder: "למשל: כיתה 303 — סייבר" },
      { id: "id", label: "מזהה קצר באנגלית (לא חובה — נגזר מהשם)", placeholder: "LAB303", dir: "ltr" },
    ],
    submitLabel: "צור כיתה",
    onSubmit: async (v) => { const short = v.id.trim().replace(/^grp_/i, ""); await post("/groups", { id: short ? "grp_" + short : "", label: v.label, role: "classroom" }); await loadMachines(); },
  });
}
function renameGroup(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  const g = (GROUPS || []).find((x) => x.id === gid); if (!g || !isAdmin()) return;
  sheet({ title: "שינוי שם הכיתה", fields: [{ id: "label", label: "השם החדש", value: g.label }],
    onSubmit: async (v) => { await put(`/groups/${encodeId(g.id)}`, { label: v.label }); await loadMachines(); } });
}
function deleteGroup(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  const g = (GROUPS || []).find((x) => x.id === gid); if (!g || !isAdmin()) return;
  const n = (MACHINES || []).filter((m) => machineGroupId(m) === g.id).length;
  /* ‏#1016: הכיתה אינה נמחקת עם מחשביה — השרת מסרב; לא מציגים חלון שמבטיח אחרת. */
  if (n > 0) { toast(`בכיתה יש ${n} מחשבים — העבירו או הסירו אותם לפני מחיקת הכיתה`, 6000, "err"); return; }
  sheet({ title: "למחוק את הכיתה?", sub: g.label, danger: true, submitLabel: "מחיקת הכיתה",
    note: impactList([["b", "הכיתה ריקה ותימחק"]]),
    onSubmit: async () => { await del(`/groups/${encodeId(g.id)}`); MACHINES_CLASS = null; await loadMachines(); } });
}

/* ---------- #954 גל 5: בריאות ושירותים — טבלת בדיקות + גרסה ועדכון ----------
   docs/design/console-redesign/health.md. בדיקות ועדכון בלבד: המתגים (מוניטור,
   DHCP) עברו לדף הפורטים (הכרעת נדב 17/09 06:12). חמישה מצבים,
   חמישה צבעים — "לא נבדק" לעולם לא ירוק (עיקרון 5). */
const HEALTH_STATES = { ok: ["ok", "תקין"], warn: ["warn", "אזהרה"], bad: ["err", "תקלה"], off: ["", "כבוי"], not_applicable: ["", "לא ישים"], unknown: ["unk", "לא נבדק"] };
function healthStatusClass(state) { return (HEALTH_STATES[state] || HEALTH_STATES.unknown)[0]; }
function healthStatusLabel(state) { return HEALTH_STATES[state] ? HEALTH_STATES[state][1] : (state || "—"); }
/* שורות דינמיות למכונה (agent_loop:<mac>, off_vlan:<mac>) מקובצות תחת שורת-קבוצה
   מיד אחרי שורת הסיכום שלהן (interfaces.md §10–11). */
const HEALTH_GROUPS = [["agent_loop:", "לולאות אתחול", "agent_loops"], ["off_vlan:", "מכונות בוילן זר", "off_vlan"],
  ["storage_", "אחסון — מיקומים", "storage_summary"]];   // ‏#1066: שורה לכל מיקום (storage_<id>), בלי שורת סיכום → קבוצה בסוף
/* עמודת "פעולה": לאן הולכים לטפל — הדף שבו יושב מה שנבדק. אין ב-/health שדה
   "איך מתקנים"; ה-detail של השרת כבר נושא את ההוראה ("הריצו את המתקין"). */
const HEALTH_GOTO = { deploy_net: ["רשת הפצה", "openNetwork(2)"], dhcp_port: ["רשת הפצה", "openNetwork(2)"], tftp_port: ["פורטים", "selectPageById('ports')"], dnsmasq: ["רשת הפצה", "openNetwork(2)"],
  server: ["פורטים", "selectPageById('ports')"], udp_sender: ["פורטים", "selectPageById('ports')"], nics: ["חיבורים פיזיים", "openNetwork(1)"],
  agent_loops: ["מחשבים", "machines"], off_vlan: ["מחשבים", "machines"] };
let healthError = "", HEALTH_AT = "";
let HEALTH_UPDATE = null, UPDATE_STATUS = null, UPDATE_CHECK = null, updateError = "";
function clockNow(now = Date.now()) { return fmtClock(new Date(now).toISOString()); }   // #1933: שעון השרת, לא הדפדפן

function healthRow(c, grouped = false) {
  let act = "";
  if (grouped && String(c.id).startsWith("storage_")) {
    act = UI.acts([["פתח באחסון", "selectPageById('storage')"]]);
  } else if (grouped) {
    const mac = String(c.id).slice(String(c.id).indexOf(":") + 1);
    if (findMachine(mac)) act = UI.acts([["פרטים", `openMachineDetail('${encodeId(mac)}')`]]);
  } else if (HEALTH_GOTO[c.id]) {
    act = UI.acts([[HEALTH_GOTO[c.id][0] + " ←", HEALTH_GOTO[c.id][1]]]);
  }
  return [`<span class="name${grouped ? " grp-kid" : ""}">${esc(c.label)}</span>`,
    UI.pill(healthStatusClass(c.state), healthStatusLabel(c.state)),
    healthDetailHtml(c), act];
}
/* ‏#1300 (console-full.html P.health): שורה קצרה. טקסט גולמי באנגלית (נתיב, שגיאת מערכת) לא
   נכנס לשורה — במקומו שורה עברית קצרה לפי המצב, והטקסט המלא מאחורי "פרטים". משפט עברי ארוך
   נחתך בסימן " — " הראשון, והמשכו ב"פרטים". מה שהשרת אמר אינו נמחק — רק מקופל. */
const HEALTH_SHORT = { ok: "תקין", warn: "דורש בדיקה", bad: "נמצאה תקלה", off: "כבוי", not_applicable: "לא ישים", unknown: "לא נבדק" };
function healthDetailHtml(c) {
  const d = String(c.detail || "");
  if (!d) return "";
  const raw = isRawTechnical(d);
  const short = raw ? (HEALTH_SHORT[c.state] || "פרטים") : d.split(" — ")[0];
  if (short === d) return `<span class="wrap">${esc(d)}</span>`;
  const full = raw ? `<code dir="ltr">${esc(d)}</code>` : `<div>${esc(d)}</div>`;
  return `<span class="wrap">${esc(short)}</span><details class="tech"><summary>פרטים</summary>${full}</details>`;
}
/* מה שלא תקין, מחוץ לטבלה המלאה (console-full.html "תקלות פתוחות"): תקלה, אזהרה, ו"לא נבדק" — כל אחד
   בשמו. ריק אמיתי = כל הבדיקות נבדקו ואף אחת לא בתקלה; בדיקה שלא נבדקה אינה "אין תקלות" (עיקרון 5). */
function healthOpenCard(checks) {
  const open = checks.filter((c) => ["bad", "warn", "unknown"].includes(c.state));
  const rank = { bad: 0, warn: 1, unknown: 2 };
  const body = open.length
    ? UI.datagrid({ columns: ["בדיקה", "מצב", "הסיבה", ""], rows: open.slice().sort((a, b) => rank[a.state] - rank[b.state]).map((c) => healthRow(c)) })
    : UI.empty("אין תקלות — כל הבדיקות נבדקו ואף אחת לא נכשלה.");
  return UI.card({ title: "תקלות פתוחות", small: open.length ? `${open.length} דורשות מבט` : "", body, flush: open.length > 0 });
}
function healthKpis(checks) {
  const n = (s) => checks.filter((c) => c.state === s).length;
  return `<div class="c12 kpis">` + [
    UI.kpi({ cls: n("ok") ? "ok" : "", label: "תקינים", value: n("ok") }),
    UI.kpi({ cls: n("warn") ? "warn" : "", label: "באזהרה", value: n("warn") }),
    UI.kpi({ cls: n("bad") ? "err" : "", label: "תקלות", value: n("bad") }),
    // ‏#1300: שלושה KPI כמו המוקאפ. "לא נבדקו" נשאר רק כשיש כאלה — "לא נבדק" אינו תקין (עיקרון 5);
    // כבויים / לא ישימים — בשורות עצמן.
    n("unknown") ? UI.kpi({ cls: "warn", label: "לא נבדקו", value: n("unknown") }) : "",
  ].join("") + `</div>`;
}

function healthRows(checks) {
  const kids = new Map(HEALTH_GROUPS.map(([p]) => [p, []]));
  const prefixOf = (c) => (HEALTH_GROUPS.find(([p]) => String(c.id).startsWith(p)) || [null])[0];
  for (const c of checks) if (prefixOf(c)) kids.get(prefixOf(c)).push(c);
  const rows = [];
  const groupBlock = ([p, label]) => {
    const list = kids.get(p);
    if (!list.length) return;
    rows.push({ html: `<tr class="group"><td colspan="4">${esc(label)} · ${list.length}</td></tr>` });
    list.forEach((c) => rows.push(healthRow(c, true)));
    kids.set(p, []);
  };
  for (const c of checks) {
    if (prefixOf(c)) continue;
    rows.push(healthRow(c));
    const g = HEALTH_GROUPS.find(([, , summary]) => summary === c.id);
    if (g) groupBlock(g);
  }
  HEALTH_GROUPS.forEach(groupBlock);   // ילדים בלי שורת סיכום — בסוף, עדיין מקובצים
  return rows;
}

/* ‏#1301: "היום ב-14:05" / "ב-12/10/2026 14:05" — חותמת השרת. */
function updateWhen(ts) {
  if (!ts) return "";
  return isToday(ts) ? `היום ב-${fmtClock(ts)}` : `ב-${fmtDate(ts)} ${fmtClock(ts)}`;
}
function updateStatusHtml(status) {
  if (!status || !status.state || status.state === "idle") return "";
  const v = (t) => `<span class="mono">${esc(t)}</span>`;
  const stages = status.stages || {};
  // ‏#1301 (המוקאפ, "הגדרות" אחרי): שלוש תוצאות בשם — חתימה נדחתה, הוחזר, עודכן ואומת.
  if (status.state === "failed" && status.reason === "signature")
    return UI.note("err", `העדכון ל-${v(status.tag)} נדחה ${esc(updateWhen(stages.verify || status.started_at))}: הגרסה לא חתומה. השרת ממשיך על ${status.from ? v(status.from) : "הגרסה הקודמת"}.`);
  if (status.state === "failed" && status.reason === "rollback")
    return UI.note("err", `העדכון ל-${v(status.tag)} נכשל ${esc(updateWhen(stages[status.stage] || status.started_at))} והשרת חזר ל-${status.from ? v(status.from) : "הגרסה הקודמת"}. הפירוט ביומן הפעולות.`);
  if (status.state === "failed") return UI.status("err", `העדכון ל-${status.tag} נכשל: ${status.error || ""}`);
  // ‏#1334: עודכן ואומת, אבל רכיב לא הותקן (למשל "עדכון ב-VM לא זמין") — לפני ה"עודכן" הירוק.
  if (status.state === "done" && status.verified && status.warning)
    return UI.note("warn", `עודכן ל-${v(status.tag)} ואומת — אבל ${esc(status.warning)}`);
  if (status.state === "done" && status.verified && status.stage === "done")
    return UI.note("ok", `עודכן${status.from ? ` מ-${v(status.from)}` : ""} ל-${v(status.tag)} ${esc(updateWhen(stages.done))}${status.by ? ` על ידי ${esc(status.by)}` : ""} · אומת: השרת ענה עם הגרסה החדשה.`);
  if (status.state === "running" && !status.verified) return UI.status("run", `העדכון ל-${status.tag} הופעל — ממתין לאתחול השרת. הראיה החיובית: גרסת השרת אחרי האתחול תואמת את התג.`);
  if (status.state === "done" && status.verified) return UI.status("ok", `אומת: השרת רץ על ${status.tag}.`);
  return "";
}

/* ‏#1000: "היום HH:MM" / "dd/mm/yyyy HH:MM" מחותמת ISO של השרת. */
function fmtWhenShort(ts) {
  if (!ts) return "";
  return isToday(ts) ? `היום ${fmtClock(ts)}` : `${fmtDate(ts)} ${fmtClock(ts)}`;
}
/* ‏#1000: שורת "בדיקה אחרונה" — מ-/update.last_check (השרת זוכר), לא מהסשן.
   ‏null = מעולם לא נבדק בשרת הזה. "הבדיקה נכשלה" (reason) ≠ "אין חדש". */
/* ‏#1301: הבדיקה נשמרה על הגרסה הקודמת, ומאז עודכנו בדיוק אליה — "יש עדכון" כבר אינו נכון. */
function updateCheckOnLatest(u, c) {
  return !!(c && c.latest && u && u.current && String(u.current).split("-")[0] === c.latest);
}
function updateLastCheckText(u) {
  if (UPDATE_CHECK && UPDATE_CHECK.failed) return `${UPDATE_CHECK.at} — ${UPDATE_CHECK.reason}`;   // הבקשה עצמה נכשלה — לא נשמרה בשרת
  const c = u && u.last_check;
  if (!c) return "מעולם לא נבדק בשרת הזה";
  const onLatest = updateCheckOnLatest(u, c);
  const what = c.available && !onLatest ? `יש עדכון: ${c.current || "?"} → ${c.latest} · חתימה: ${signatureText(c.signature)}`
    : onLatest ? `כבר על הגרסה העדכנית (${u.current})`
    : c.latest ? `כבר על הגרסה העדכנית (${c.current})` : (c.message || (c.reason ? `הבדיקה נכשלה: ${c.reason}` : "לא נמצאה גרסה חדשה יותר"));
  return `${fmtWhenShort(c.at)} — ${what}`;
}

/* ‏#1277: חתימת התג החדש. שלושה מצבים, ו"לא נבדק" אינו "לא חתום" — וגם בדיקה
   שנשמרה לפני שהשדה היה (signature חסר) היא "לא נבדק", לא "חתום". */
const SIGNATURE_TEXT = { signed: "חתום", unsigned: "לא חתום", unchecked: "לא נבדק" };
function signatureText(sig) {
  return SIGNATURE_TEXT[sig && sig.state] || SIGNATURE_TEXT.unchecked;
}

function updateLastCheckHtml(u) {
  const main = esc(updateLastCheckText(u));
  const c = u && u.last_check;
  const detail = c && (c.detail || (c.available && !updateCheckOnLatest(u, c) && c.signature && c.signature.detail));
  return detail
    ? `${main}<small class="muted" style="display:block;margin-top:3px">${esc(detail)}</small>`
    : main;
}

/* ‏#1300/#1301 (סידור 17/10): מתחת לשורות של "עדכונים ותחזוקה" — הודעה על גרסה חדשה (עם החתימה), תוצאת העדכון
   האחרון, וכפתורי החלה/חזרה. רק כשיש מה להציג; "גרסה מותקנת" ו"בדיקה אחרונה" הן שורות. */
function updateActionsHtml() {
  if (updateError || !HEALTH_UPDATE || !HEALTH_UPDATE.enabled) return "";
  const u = HEALTH_UPDATE;
  const st = updateStatusHtml(UPDATE_STATUS);
  const canApply = UPDATE_CHECK && UPDATE_CHECK.available && UPDATE_CHECK.latest;
  // ‏#1273: עדכון שרץ (ועוד לא אומת) — אין החלה/חזרה נוספת; השרת גם מחזיר 409.
  const busy = UPDATE_STATUS && UPDATE_STATUS.state === "running" && !UPDATE_STATUS.verified
    ? ` disabled title="עדכון ל-${esc(UPDATE_STATUS.tag || "")} רץ — ממתינים לאתחול השרת"` : "";
  // ‏#1277: תג שנבדק ואינו חתום — השרת יסרב לו (server-upgrade.sh); "לא נבדק" נשאר לחיץ, והשרת מכריע.
  const unsigned = canApply && UPDATE_CHECK.signature && UPDATE_CHECK.signature.state === "unsigned"
    ? ` disabled title="התג ${esc(UPDATE_CHECK.latest)} אינו חתום במפתח השחרור — השרת יסרב לעדכן אליו"` : "";
  const btns = [
    canApply ? `<button class="btn primary"${unsigned || busy} onclick="confirmUpdateApply(UPDATE_INFO.latest)">החל עדכון ${esc(UPDATE_CHECK.latest)}</button>` : "",
    u.previous ? `<button class="btn danger"${busy} onclick="confirmUpdateRevert(UPDATE_INFO.previous)">חזור ל-${esc(u.previous)}</button>` : "",
  ].join("");
  const sig = canApply ? UPDATE_CHECK.signature : null;
  const found = canApply ? UI.note("info", `קיימת גרסה חדשה: <b class="mono">${esc(UPDATE_CHECK.latest)}</b>${UPDATE_CHECK.at ? ` · נבדק ${esc(fmtWhenShort(UPDATE_CHECK.at))}` : ""} · חתימה: ${esc(signatureText(sig))}`
    + (sig && sig.detail ? `<small class="muted" style="display:block;margin-top:3px">${esc(sig.detail)}</small>` : "")) : "";
  if (!found && !st && !btns) return "";
  return `<div class="mupd">${found}${st ? `<div class="mupd-st">${st}</div>` : ""}${btns ? `<div class="macts">${btns}</div>` : ""}</div>`;
}
/* ‏#1301: "מעודכן" — רק כשהעדכון האחרון אומת **וגם** הבדיקה האחרונה לא מצאה גרסה חדשה ממנו. */
function updateUpToDate() {
  const s = UPDATE_STATUS, lc = HEALTH_UPDATE && HEALTH_UPDATE.last_check;
  return !!(s && s.state === "done" && s.verified && s.stage === "done" && lc && lc.latest === s.tag);
}

/* ‏#1128: "הורד גיבוי הגדרות" — tar.gz של ה-DB (עותק עקבי) ושאר תיקיית
   הנתונים, בלי אימג'ים. הארכיון מכיל סודות (סיסמאות מגובבות, סודות MFA,
   מפתח התעודה) — ולכן אזהרה לפני ההורדה, לא אחריה. */
function confirmSettingsBackup() {
  sheet({
    title: "הורדת גיבוי הגדרות",
    sub: "הקובץ מכיל סודות: סיסמאות מגובבות, סודות MFA ומפתח התעודה. לשמור אותו כמו סיסמה — לא בתיקייה משותפת ולא במייל. האימג'ים אינם בפנים (הם בתיקיית האימג'ים ומגובים ב-rsync).",
    submitLabel: "הורד",
    onSubmit: async () => {
      if (secondaryReadOnly()) throw new Error(SECONDARY_READ_ONLY);   // ‏#1179: הגיבוי היה של הראשי
      const a = document.createElement("a");
      a.href = "/api/console/update/backup";
      document.body.appendChild(a); a.click(); a.remove();
    },
  });
}

/* ‏#1914: הדיסק-און-קי לשיכפול בלי שרת. "לא נקרא" (הבקשה נכשלה) ≠ "לא נבנה בשרת הזה" (עיקרון 5). */
let USB_CLONE = null, usbCloneError = "";
async function loadUsbClone() {
  try { USB_CLONE = await api("/usb-clone"); usbCloneError = ""; } catch (e) { USB_CLONE = null; usbCloneError = e.message; }
}
function usbCloneRowHtml() {
  if (!USB_CLONE) return settingUnreadHtml(usbCloneError || "לא נטען");
  if (!USB_CLONE.available) return `<span class="muted">${esc(USB_CLONE.reason || "לא זמין")}</span>`;
  const mb = Math.round(USB_CLONE.size / 1048576);
  return `<span class="sv"><bdi>${esc(USB_CLONE.version || "גרסה לא ידועה")} · ${mb} MB</bdi></span> ${ghostBtn("הורדה…", "usbCloneSheet()")}`;
}
function usbCloneSheet() {
  const u = USB_CLONE;
  if (!u || !u.available) return;
  sheet({
    title: "הורדת דיסק-און-קי לשיכפול",
    sub: `${u.file} · ${Math.round(u.size / 1048576)} MB${u.built_at ? " · נבנה " + fmtDate(u.built_at) : ""}. ` +
         `לפני הצריבה ודאו שה-SHA256 של הקובץ שהורד הוא: ${u.sha256}. ` +
         "צורבים ב-Rufus במצב DD (\"DD Image\") — זו הדרך שנבדקה; balenaEtcher נכשל בשטח. הצריבה מוחקת את כל מה שעל הדיסק-און-קי. כל גרסה דורשת הורדה וצריבה מחדש.",
    submitLabel: "הורד",
    onSubmit: async () => {
      const a = document.createElement("a");
      a.href = "/api/console/usb-clone/download";
      document.body.appendChild(a); a.click(); a.remove();
    },
  });
}

async function healthUpdateCheck() {
  try {
    const r = await post("/update/check", {});
    // ‏#1000: התשובה היא המסמך שהשרת שמר (עם `at` שלו) — אותו דבר ש-GET /update יחזיר.
    UPDATE_CHECK = r;
    if (HEALTH_UPDATE) HEALTH_UPDATE.last_check = r;
    UPDATE_INFO.latest = r.latest;
  } catch (e) {
    // הבקשה לשרת נכשלה (404 מתג כבוי / רשת): לא נשמר בשרת, ולכן שעון הדפדפן
    // ובשם — "הבדיקה נכשלה" ≠ "אין חדש" (עיקרון 5)
    UPDATE_CHECK = { at: clockNow(), available: false, latest: null, failed: true, reason: "הבדיקה נכשלה: " + e.message };
    toast(e.message);
  }
  if (current === "settings") renderCurrent();
}

function health() {
  if (!HEALTH && !healthError) return pagePlaceholder();
  const checks = Array.isArray(HEALTH) ? HEALTH : [];
  const n = (s) => checks.filter((c) => c.state === s).length;
  let pill;
  if (!HEALTH) pill = UI.pill("err", "לא נקרא");
  else if (!checks.length) pill = UI.pill("", "אין בדיקות");
  else if (n("bad")) pill = UI.pill("err", "תקלה");
  else if (n("warn")) pill = UI.pill("warn", "אזהרה");
  else if (n("unknown")) pill = UI.pill("warn", "לא נבדק");
  else if (n("off")) pill = UI.pill("", "חלק כבוי");
  else if (n("not_applicable")) pill = UI.pill("", "חלק לא ישים");
  else pill = UI.pill("ok", "הכל תקין");
  // ‏#1300 (console-full.html P.health): משנה קצרה — כמה בדיקות ומתי; הספירה לפי מצב — ב-KPI.
  const sub = HEALTH
    ? [`${checks.length} בדיקות`, HEALTH_AT ? `נבדק ${HEALTH_AT}` : "זמן המדידה לא נמסר"].map(esc).join(" · ")
    : `‏/health לא נקרא: ${esc(healthError)}`;
  const actions = `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "בריאות ושירותים" }],
    icon: "t-health", name: "בריאות ושירותים", sub, pill, actions, compact: true });
  const body = !HEALTH
    ? UI.note("err", `לא הצלחתי לקרוא את הבדיקות: ${esc(healthError)}`)
    : UI.datagrid({ columns: ["בדיקה", "מצב", "פירוט", ""], rows: healthRows(checks), empty: "אין בדיקות — השרת החזיר רשימה ריקה" });
  const checksCard = UI.card({ title: "בדיקות", body, flush: !!HEALTH && checks.length > 0 });
  return `<div class="page aligned">${header}<div class="body">${HEALTH ? healthKpis(checks) : ""}${checksCard}${HEALTH ? healthOpenCard(checks) : ""}${healthServicesCard()}</div></div>`;
}

/* ---------- #1267 המשך (נדב 11/10): עצירה/הפעלה של שירות — כאן, ליד כל שירות ----------
   אותו API שדף הפורטים קרא לו (PUT toggle_url = /ports/{id}; ‏#996/#1013) — רק שורות ‏/ports
   שהמתג שלהן חי שם: TFTP 69, ‏8080, ‏8081, ‏8443. ‏DHCP/proxy — ברשת הפצה; מוניטור — בדף שלו.
   עצירה = חלון אישור עם ההשלכות וכפתור אדום, בלי הקלדה (#1270); ‏toggle "confirm" — המילה
   שהשרת דורש נשלחת מהשורה (confirm_word). הפעלה = כפתור רגיל. לא נקרא = "לא נבדק", בלי כפתור. */
let HEALTH_SVC = null, healthSvcError = "";
async function loadHealthServices() {
  try {
    const list = await api("/ports");
    // תשובה שאינה רשימה = "לא נבדק", לא רשימה ריקה ולא קריסת ציור (עיקרון 5)
    if (!Array.isArray(list)) throw new Error("‏/ports החזיר תשובה שאינה רשימה");
    HEALTH_SVC = list; healthSvcError = "";
  }
  catch (e) { HEALTH_SVC = null; healthSvcError = e.message; }
}
const healthSvcList = () => (HEALTH_SVC || []).filter((p) => p.toggle && p.toggle !== "none" && String(p.toggle_url || "").startsWith("/api/console/ports/"));
const healthSvcUnread = (p) => p.listening === null || p.listening === undefined || p.enabled === null || p.enabled === undefined;
function healthSvcRow(p) {
  const k = encodeId(p.id);
  const label = healthSvcUnread(p) ? "לא נבדק" : p.enabled && p.listening ? "פועל" : !p.enabled && !p.listening ? "עצור" : healthStatusLabel(p.state);
  const state = `${UI.status(healthSvcUnread(p) ? "unk" : healthStatusClass(p.state), label)}<span class="sub">${esc(p.detail || "")}</span>`;
  const act = healthSvcUnread(p) ? ""
    : p.enabled ? `<button class="btn sm danger" onclick="healthSvcStop('${k}')">עצור…</button>`
    : `<button class="btn sm" onclick="healthSvcStart('${k}')">הפעל</button>`;
  return [UI.nameHtml(p.name, esc(p.desc || "")), `<span class="mono">${esc(p.port)}/${esc(p.proto)}</span>`, state, act];
}
function healthServicesCard() {
  const body = HEALTH_SVC === null
    ? UI.note("", `לא נבדק — ‏/ports לא נקרא${healthSvcError ? `: ${esc(healthSvcError)}` : ""}`)
    : UI.datagrid({ columns: ["שירות", "פורט", "מצב", ""], rows: healthSvcList().map(healthSvcRow), empty: "אין שירות שאפשר לעצור או להפעיל מכאן" });
  return UI.card({ title: "שירותים", small: "עצירה והפעלה — מצב נמדד מ-/ports, נקרא מחדש אחרי כל שינוי", body, flush: HEALTH_SVC !== null && healthSvcList().length > 0 });
}
async function healthSvcSend(p, enabled) {
  const body = { enabled };
  if (!enabled && p.toggle === "confirm") body.confirm = p.confirm_word || ME.server_name;
  const r = await put(p.toggle_url.replace(/^\/api\/console/, ""), body);
  toast(r && r.ok ? `${p.name} ${p.port} — ${enabled ? "הופעל" : "נעצר"}, ואומת`
    : `${p.name} ${p.port} — נשמר, אבל לא אומת${r && r.detail ? `: ${r.detail}` : ""}`);
  await loadHealthServices();
  if (current === "health") renderCurrent();
}
function healthSvcStop(idEnc) {
  const id = portIdOf(idEnc), p = healthSvcList().find((x) => x.id === id);
  if (!p || healthSvcUnread(p)) return;
  const warn = p.warning_he || (p.off_means ? `מה קורה אם עוצרים: ${p.off_means}` : "");
  sheet({ title: `לעצור את ${p.name} ${p.port}/${p.proto}?`, sub: p.desc || "", danger: true, submitLabel: "עצירה",
    note: warn ? `<div class="sheet-note danger" data-testid="svc-warning">${esc(warn)}</div>` : "",
    onSubmit: () => healthSvcSend(p, false) });
}
async function healthSvcStart(idEnc) {
  const id = portIdOf(idEnc), p = healthSvcList().find((x) => x.id === id);
  if (!p || healthSvcUnread(p)) return;
  try { await healthSvcSend(p, true); } catch (e) { toast(`ההפעלה נכשלה: ${e.message}`); }
}

/* ‏#1933: "YYYY-MM-DD HH:MM:SS" / "HH:MM:SS" באזור התצוגה (‏zoneParts); מה שאינו חותמת — כפי שהוא. */
function fmtWhen(ts) {
  const s = String(ts || "");
  if (!s) return "—";
  const ms = tsMs(s);
  if (!Number.isFinite(ms)) return s.slice(0, 19);
  const p = zoneParts(ms);
  return `${p.y}-${p.mo}-${p.d} ${p.h}:${p.mi}:${p.s}${p.utc ? " UTC" : ""}`;
}

function fmtHour(ts) {
  const s = String(ts || "");
  const ms = tsMs(s);
  if (!Number.isFinite(ms)) return s || "—";
  const p = zoneParts(ms);
  return `${p.h}:${p.mi}:${p.s}${p.utc ? " UTC" : ""}`;
}

/* ---------- #954 גל 6: הרשאות ----------
   נבנה לפי docs/design/console-redesign/permissions.md: טבלת משתמשים אחת
   (‏/users) במקום שתיים, ומטריצת "מה כל תפקיד רואה" שנגזרת מהקוד — מה
   מחזיר 403 בשרת (‏auth.dependencies: current_user / admin_only, ו-round_operator
   / room_operator = admin+deploy). "כניסה אחרונה" אין ב-/users — לא מומצא.
   המנהל הפעיל האחרון: בלי "תפקיד/השבת/מחיקה" (השרת מסרב; users.py). */
let usersError = "";

/* שורה = אזור; [מנהל], [הפצה] כ-[cls, טקסט]; המקור = ה-Depends של ה-endpoint
   (server/*.py). ‏#1073 (הכרעת נדב 18/09): למשתמש הפצה אין כניסה לקונסולה —
   הכניסה מסרבת לו (deploy_no_console) וכל נתיב ניהול מחזיר לו 403. מה
   שמסומן "ממחשב הבנייה" פתוח לו רק שם — דרך allowlist הקיוסק (kiosk.py). */
const ROLE_MATRIX = [
  ["כניסה לקונסולה (ניהול וובי)", ["ok", "כן"], ["", "לא"], "POST /login — deploy → 403 deploy_no_console; כל /api/console/* — auth.console_only (#1073)"],
  ["אימג'ים, תיקיות, קבוצות — צפייה", ["ok", "כן"], ["ok", "ממחשב הבנייה"], "GET /images, /folders, /groups — allowlist הקיוסק; /overview, /net — קונסולה בלבד"],
  ["חדר המשכפלים: סבב, גל, WoL לחדר; שחזור לדיסק מחשב הבנייה", ["ok", "כן"], ["ok", "ממחשב הבנייה"], "POST /room, /room/start|wake|close — room_operator, allowlist הקיוסק; single_restore (#706/#1073)"],
  ["קליטה, העלאה, מחיקה ועריכה של אימג'ים ותיקיות", ["ok", "כן"], ["", "לא"], "POST /tasks/capture, /images/upload, /images/{id}/delete, PUT /images/{id}, /folders — admin_only"],
  ["מחשבים: צפייה, הוספה, עריכה, הסרה, WoL למחשב, ניקוי דיסק אדום", ["ok", "כן"], ["", "לא"], "GET/POST/PUT/DELETE /machines, /machines/{mac}/wake, /groups/{gid}/wake, /disk-failures — קונסולה בלבד"],
  ["מוניטור", ["ok", "כן"], ["", "לא"], "GET/PUT /monitor/settings, GET /monitor/machines — admin_only"],
  ["דרייברים: צפייה, ייבוא ומחיקה", ["ok", "כן"], ["", "לא"], "GET /drivers, POST /drivers/upload, /drivers/{name}/delete — קונסולה בלבד"],
  ["רשת, פורטים, DHCP, בריאות, עדכון", ["ok", "כן"], ["", "לא"], "/net/config, PUT /net/interfaces/{n}, /health, /update — admin_only"],
  ["הרשאות, הגדרות, יומן", ["ok", "כן"], ["", "לא"], "/users, /settings, /journal — admin_only"],
  ["שרתי משנה", ["ok", "כן"], ["", "לא"], "/storage-nodes… — require_standalone (admin_only)"],
];

function findUser(name) {
  try { name = decodeURIComponent(name); } catch (e) {}
  return (USERS || []).find((u) => u.username === name) || null;
}
function activeAdminCount() { return (USERS || []).filter((u) => u.role === "admin" && !u.disabled).length; }

function userMfaLabel(u) {
  if (u.is_builtin) return "מקומי";
  if (u.mfa_enabled) return "✓";
  return "—";
}

/* ‏stage 3 (console-full.html P.users): עמודת MFA כפיל. המנהל שנוצר בהתקנה — "מקומי · ללא MFA" (בכוונה, דלת חירום);
   מנהל עם MFA — "פעיל"; מנהל בלי — "כבוי" (אזהרה); משתמש הפצה אינו נכנס לקונסולה, ולכן "—" ולא "כבוי". */
function userMfaPill(u) {
  if (u.is_builtin) return UI.pill("info", "מקומי · ללא MFA");
  if (u.mfa_enabled) return UI.pill("ok", "פעיל") + (u.mfa_required ? " " + UI.pill("", "דרוש") : "");
  if (u.role === "deploy") return u.mfa_required ? UI.pill("", "דרוש") : "—";
  // נדב 18/10: מסומן "דרוש" ועוד לא נרשם — יירשם בכניסה הבאה
  return u.mfa_required ? UI.pill("warn", "דרוש · יירשם בכניסה") : UI.pill("warn", "כבוי");
}
function userRowHtml(u) {
  const enc = encodeId(u.username), self = !!ME && u.username === ME.username;
  const lastAdmin = u.role === "admin" && !u.disabled && activeAdminCount() <= 1;
  // ‏⋯ אחד לשורה (UI.rowMenu): ההרסני (מחיקה — חלון אישור) בתחתית, באדום.
  // ‏#1268: אין הקלדת סיסמה בטבלה. למשתמש אחר רק "איפוס סיסמה…" (סיסמה זמנית
  // חד-פעמית, והוא בוחר חדשה בכניסה הבאה); לעצמך — "שנה סיסמה" ב"החשבון שלי".
  // ‏#1262: המנהל שנוצר בהתקנה הוא דלת החירום — השרת מסרב (409) למחיקה,
  // השבתה והורדה, ולכן התפריט מציע רק איפוס סיסמה וניתוק הפעלות.
  const items = [];
  const locked = lastAdmin || u.is_builtin;
  // ‏#1300: שמות הפעולות כמו MENUS.user של console-full.html.
  if (!self && !locked) items.push(["שנה תפקיד…", `userRoleSheet('${enc}')`]);
  if (!self) items.push(["איפוס סיסמה…", `userResetPassword('${enc}')`]);
  if (!self && !locked) items.push([u.disabled ? "הפעל משתמש" : "השבת משתמש…", `userDisable('${enc}', ${u.disabled ? "false" : "true"})`]);
  if (!self && !u.is_builtin) items.push([u.mfa_required ? "הסר את דרישת האימות הדו-שלבי…" : "דרוש אימות דו-שלבי…", `userMfaRequiredSheet('${enc}')`]);   // נדב 18/10
  if (!self && !u.is_builtin && u.mfa_enabled) items.push(["איפוס אימות דו-שלבי…", `userMfaDisable('${enc}')`]);
  if (!self) items.push(["נתק את כל ההפעלות…", `userRevokeSessions('${enc}')`]);
  if (!self && !locked) items.push(["מחק משתמש…", `userDeleteSheet('${enc}')`, "danger"]);
  if (self) items.push(["שנה סיסמה (החשבון שלי)", "openAccountPage()"]);   // ‏#1268: הסיסמה שלך — רק ב"החשבון שלי"
  const note = u.is_builtin ? "נוצר בהתקנה · לא ניתן למחיקה"
    : self ? "זה אתה" : lastAdmin ? "המנהל הפעיל האחרון — לא ניתן למחיקה, להורדה או להשבתה" : "";
  // ‏#1300 (console-full.html P.users): משתמש · תפקיד · נראה לאחרונה · MFA. "מושבת" — פיל ליד השם;
  // תאריך היצירה — ב-title של השם.
  return { attrs: `data-user="${esc(u.username)}"`, cells: [
    `<b class="uname" title="נוצר ${esc(fmtDate(u.created_at))}">${esc(u.username)}</b>${u.disabled ? " " + UI.pill("err", "מושבת") : ""}${note ? `<span class="sub">${esc(note)}</span>` : ""}`,
    esc(u.role === "admin" ? "מנהל" : "הפצה"),
    userLastLoginHtml(u),
    userMfaPill(u),
    items.length ? UI.rowMenu(u.username, items) : ""] };
}
/* ‏#1008: last_login_at/from מ-/users (הכניסה האחרונה שהנפיקה עוגייה). null =
   "לא נרשמה" — משתמש חדש, או שלא נכנס מאז העדכון — לא "מעולם" ולא תאריך. */
/* ‏#1300 (console-full.html P.users "נראה לאחרונה"): "‎10.44.0.7 · לפני 3 דק'" — הכתובת ואחריה מתי; הזמן המלא ב-title. */
function userLastLoginHtml(u) {
  if (!u.last_login_at) return `<span class="muted" title="אין כניסה רשומה בשרת (או שלא נכנס מאז העדכון)">לא נרשמה</span>`;
  const full = `${fmtDate(u.last_login_at)} ${fmtClock(u.last_login_at)}`;
  const recent = Date.now() - Date.parse(u.last_login_at) < 86400000;   // יום אחרון — "לפני N…"; מוקדם יותר — "dd/mm" (כמו המוקאפ)
  return `<span title="${esc(full)}">${u.last_login_from ? `<bdi dir="ltr">${esc(u.last_login_from)}</bdi> · ` : ""}${esc(recent ? ago(u.last_login_at) : fmtDate(u.last_login_at).slice(0, 5))}</span>`;
}

function permissions() {
  if (!USERS && !usersError) return pagePlaceholder();
  const list = USERS || [];
  const deployers = list.filter((u) => u.role === "deploy").length;
  const roleNames = [list.some((u) => u.role === "admin") ? "מנהל" : "", deployers ? "הפצה" : ""].filter(Boolean);
  const sub = USERS
    ? [`${list.length} משתמשים`, roleNames.length ? `תפקידים: ${roleNames.join(", ")}` : ""].filter(Boolean).map(esc).join(" · ")
    : `‏/users לא נקרא: ${esc(usersError)}`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "ניהול" }, { label: "משתמשים והרשאות" }],
    icon: "t-key", name: "משתמשים והרשאות", sub, pill: USERS ? "" : UI.pill("err", "לא נקרא"),
    actions: `<button class="btn primary" onclick="openNewUser()">${uiIcon("plus")} משתמש</button><button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>` });
  const table = !USERS
    ? UI.note("err", `לא הצלחתי לקרוא את המשתמשים: ${esc(usersError)}`)
    : UI.datagrid({ cls: "kebab-col", opt: [2], columns: ["משתמש", "תפקיד", "נראה לאחרונה", "MFA", { html: `<span class="sr">פעולות</span>` }], rows: list.map(userRowHtml), empty: "אין משתמשים — השרת החזיר רשימה ריקה" });
  const usersCard = UI.card({ title: "משתמשים", cls: "c12", body: table, flush: !!USERS && list.length > 0 });
  // ‏#1300: כרטיס לכל תפקיד (כמו המוקאפ). המטריצה המפורטת לא ירדה — מקופלת ב"הרשאות מפורטות".
  const roleCard = (title, text) => UI.card({ title, cls: "c6", body: `<p class="cap">${esc(text)}</p>` });
  const roles = roleCard("מנהל", "גישה מלאה: אימג'ים, מחשבים, תשתית, משתמשים והגדרות.")
    + roleCard("הפצה", "תפקיד לתחנה ולתהליך התכנאי. אין לו כניסה לקונסולה.");
  const matrix = UI.datagrid({ columns: ["", "מנהל", "הפצה"],
    rows: ROLE_MATRIX.map(([area, a, d, src]) => [`<span title="${esc(src)}">${esc(area)}</span>`, UI.status(a[0], a[1]), UI.status(d[0], d[1])]) })
    + `<div class="cap" style="padding:10px 14px">אין הרשאה שקטה: מה שאינו "כן" מחזיר 403 בשרת, לא רק מוסתר בממשק. למשתמש הפצה אין כניסה לקונסולה (#1073) — הוא עובד ממחשב הבנייה: שיכפול מאימג' בשרת, שיכפול ישיר מהדיסק, ושחזור לדיסק שלו. המנהל הפעיל האחרון אינו ניתן למחיקה או להורדה. ריחוף על אזור מציג את ה-endpoint.</div>`;
  const matrixCard = `<details class="c12 card perm-more"><summary class="card-h"><span>הרשאות מפורטות <small>מה כל תפקיד רואה — נגזר מה-Depends של כל endpoint</small></span></summary><div class="card-b flush">${matrix}</div></details>`;
  return `<div class="page aligned">${header}<div class="body">${usersCard}${roles}${matrixCard}</div></div>`;
}

/* ‏#1268: איפוס על ידי מנהל — POST /users/{u}/reset-password. אישור בלי הקלדת מילה, ואחריו חלון
   שמציג את הסיסמה הזמנית פעם אחת. תשובה בלי new_password היא כשל גלוי (עיקרון 5), לא חלון ריק. */
function userResetPassword(name) {
  const u = findUser(name); if (!u || !isAdmin() || (ME && u.username === ME.username)) return;
  sheet({ title: "לאפס את הסיסמה?", sub: u.username, danger: true, submitLabel: "איפוס סיסמה",
    note: impactList([["w", "תיווצר סיסמה זמנית חד-פעמית — היא תוצג לך פעם אחת, כדי שתמסור אותה למשתמש"],
      ["b", "בכניסה הבאה המשתמש יחויב לבחור סיסמה חדשה, והוא ינותק מכל הדפדפנים"]]),
    onSubmit: async () => {
      const r = await post(`/users/${encodeId(u.username)}/reset-password`);
      const pw = r && typeof r.new_password === "string" ? r.new_password : "";
      if (!pw) throw new Error("השרת לא החזיר סיסמה זמנית — לא ידוע אם הסיסמה אופסה");
      await loadUsersData();
      return () => userResetResult(u.username, pw);
    } });
}
function userResetResult(username, pw) {
  sheet({ title: "הסיסמה הזמנית", sub: username, submitLabel: "העתקה", cancelLabel: "סגירה", hint: "לא תוצג שוב",
    note: UI.note("info", "מסור אותה למשתמש — בכניסה הבאה הוא יבחר סיסמה חדשה.")
      + passwordFieldHtml({ id: "sf-onetime", label: "סיסמה זמנית", value: pw, readonly: true, autocomplete: "off" }),
    onSubmit: async () => {
      const clip = navigator.clipboard;
      if (!clip || !clip.writeText) throw new Error("ההעתקה אינה זמינה בדפדפן הזה — לחץ \"הצג\" והעתק ידנית");
      try { await clip.writeText(pw); } catch (e) { throw new Error("ההעתקה נכשלה — לחץ \"הצג\" והעתק ידנית"); }
      toast("הסיסמה הועתקה");
    } });
}
function userRoleSheet(name) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  sheet({ title: "שינוי תפקיד", sub: u.username,
    note: UI.note("", "המשתמש יתנתק וייכנס מחדש כדי שההרשאות החדשות יחולו."),
    fields: [{ id: "role", label: "תפקיד", type: "radio", value: u.role,
      options: [{ value: "deploy", label: "הפצה", hint: "לתחנה ולכלי הטכנאי — בלי כניסה לקונסולה" }, { value: "admin", label: "מנהל", hint: "הכול" }] }],
    onSubmit: async (v) => { await put(`/users/${encodeId(u.username)}`, { role: v.role }); toast("התפקיד שונה — נרשם ביומן"); await loadUsersData(); } });
}
/* חסימה אינה מחיקה: הפיכה, ושורות היומן נשארות מצביעות על מישהו. */
function userDisable(name, disable) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  confirmSheet(disable ? "להשבית את המשתמש?" : "להפעיל את המשתמש?", u.username,
    disable ? "השבתת משתמש" : "הפעלת משתמש",
    async () => { await put(`/users/${encodeId(u.username)}`, { disabled: !!disable }); toast(disable ? "המשתמש הושבת" : "המשתמש הופעל"); await loadUsersData(); },
    disable ? [["b", "המשתמש לא יוכל להיכנס"], ["w", "סשן פתוח שלו נסגר מיד"], ["ok", "אפשר להפעיל אותו שוב"]] : [["ok", `${esc(u.username)} יוכל להיכנס שוב`]], !!disable);   // ‏#1016: הפעלה אינה הרסנית
}
function userDeleteSheet(name) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  sheet({ title: "למחוק את המשתמש?", sub: u.username,
    note: impactList([["b", "המשתמש יאבד גישה מיידית"], ["ok", "שורות היומן שלו נשארות"]]),
    danger: true, submitLabel: "מחיקת המשתמש",
    onSubmit: async () => { await del(`/users/${encodeId(u.username)}`); toast(`המשתמש ${u.username} נמחק`); await loadUsersData(); } });
}
/* נדב 18/10: "איפוס אימות דו-שלבי" — כפתור אדום, בלי הקלדת מילה (#1270). השרת מוחק את הסוד וקודי
   הגיבוי ומנתק את כל ההפעלות של המשתמש; בכניסה הבאה — רישום מחדש אם האימות חובה לו, אחרת סיסמה בלבד. */
function userMfaDisable(name) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  confirmSheet("לאפס את האימות הדו-שלבי?", u.username,
    "איפוס אימות דו-שלבי",
    async () => { await post(`/users/${encodeId(u.username)}/mfa/disable`); toast("האימות הדו-שלבי אופס — נרשם ביומן"); await loadUsersData(); },
    [["b", "האפליקציה וקודי הגיבוי שלו יפסיקו לעבוד"], ["w", "כל ההפעלות שלו ינותקו מיד"],
     ["w", u.mfa_required ? "בכניסה הבאה יידרש לרשום אימות דו-שלבי מחדש (מסומן לו \"דרוש\")"
                          : "בכניסה הבאה ייכנס בסיסמה בלבד — אלא אם החיוב למנהלים דלוק בהגדרות"]]);
}
/* נדב 18/10: "דרוש אימות דו-שלבי" למשתמש — מנהל כזה יירשם ל-MFA בכניסה הבאה גם כשהחיוב הכללי כבוי. */
function userMfaRequiredSheet(name) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  sheet({ title: "אימות דו-שלבי", sub: u.username,
    fields: [{ id: "mfa_required", label: "דרוש אימות דו-שלבי", type: "checkbox", value: !!u.mfa_required,
      help: "מסומן: המשתמש חייב לרשום אימות דו-שלבי בכניסה, ולא יוכל לבטל אותו בעצמו. חל על מנהלים." }],
    onSubmit: async (v) => {
      await put(`/users/${encodeId(u.username)}`, { mfa_required: !!v.mfa_required });
      toast(v.mfa_required ? "אימות דו-שלבי נדרש — נרשם ביומן" : "הדרישה הוסרה — נרשם ביומן");
      await loadUsersData();
    } });
}
function userRevokeSessions(name) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  confirmSheet("לנתק את כל ההפעלות?", u.username,
    "ניתוק ההפעלות",
    async () => { await post("/sessions/revoke", { username: u.username }); toast("ההפעלות נותקו"); await loadUsersData(); },
    [["b", "כל הסשנים והדפדפנים הזכורים של המשתמש יבוטלו מיד"]]);
}

/* ---------- #954 גל 6: יומן ----------
   נבנה לפי docs/design/console-redesign/logs.md: יומן אחד (לא "אירועים/Audit"),
   שורת סינון בדף (q · סוג · משתמש · טווח) — לא במודאל — מול
   ‏/journal?q&event&user&from&to&limit; "עוד" מביא את הדף הבא עם before=<id של
   השורה האחרונה> ומוסיף (#1008). ‏X-Journal-Total = הסך; חסר (סינון מכונה/חיפוש)
   = לא ידוע, לא 0. ייצוא = /journal.csv עם אותו סינון. חומרה = severity מהשרת
   (#968; ok/info/warn/err); "יעד" כעמודה מבנית — דורש API, ולכן text מוצג מתחת למשפט. */
const logInit = () => ({ q: "", event: "", user: "", range: "", since: "", until: "", limit: 200,
                         rows: null, err: "", truncated: false, events: null, total: null, more: false });
let LOG = logInit();
const LOG_RANGES = [["", "כל הזמן"], ["today", "היום"], ["7d", "7 ימים"], ["30d", "30 יום"], ["custom", "טווח…"]];
/* ‏stage 3: עמודת "חומרה" כפיל עם מילה (console-full.html logTable) — לא נקודה צבעונית לבדה. */
const LOG_SEV_SHORT = { ok: "הושלם", info: "מידע", warn: "אזהרה", err: "שגיאה" };
const LOG_SEV_HE = { ok: "הושלם / אושר", info: "מידע", warn: "דורש תשומת לב", err: "כשל / סירוב" };

/* חותמות השרת הן UTC ISO (now_iso) — הטווח מחושב באותו ציר. ‏#1933: "היום" מתחיל בחצות של השרת. */
function logSince(range, now = Date.now()) {
  if (range === "today") return wallToUtc(zoneDay(now));
  const days = range === "7d" ? 7 : range === "30d" ? 30 : 0;
  return days ? new Date(Date.now() - days * 86400000).toISOString().slice(0, 19) : "";
}
function logParams() {
  const p = new URLSearchParams();
  if (LOG.q) p.set("q", LOG.q);
  if (LOG.event) p.set("event", LOG.event);
  if (LOG.user) p.set("user", LOG.user);
  // ‏#1933: הטווח המוקלד הוא שעון-הקיר של השרת (כמו הזמנים בטבלה) — נשלח לשרת ב-UTC.
  const since = LOG.range === "custom" ? wallToUtc(LOG.since) : logSince(LOG.range);
  if (since) p.set("from", since);
  // "עד" הוא דקה שלמה (datetime-local): בלי :59, "10:00" כמחרוזת פוסל 10:00:15.
  if (LOG.range === "custom" && LOG.until) p.set("to", wallToUtc(LOG.until + ":59"));
  return p;
}
function logQuery(before) {
  const p = logParams();
  p.set("limit", String(LOG.limit));
  if (before != null) p.set("before", String(before));
  return "?" + p.toString();
}
/* ‏#1008: הייצוא — אותו סינון, בלי limit (השרת: עד חלון הסריקה, 20,000). */
function logCsvHref() {
  const qs = logParams().toString();
  return "/api/console/journal.csv" + (qs ? "?" + qs : "");
}
async function loadJournalData(append = false) {
  try {
    if (!LOG.events) LOG.events = await api("/journal/events");
  } catch (e) { LOG.events = null; }   // הסינון לפי סוג לא זמין — הרשימה עצמה עדיין נקראת
  try {
    // fetch ישיר ולא api(): הכותרת X-Journal-Search-Truncated — "לא בדקנו
    // הכל" אינו "אין תוצאות" (עיקרון 5).
    const last = append && LOG.rows && LOG.rows.length ? LOG.rows[LOG.rows.length - 1].id : null;
    const response = await apiFetch(consoleUrl("/journal" + logQuery(last)), { credentials: "same-origin" });
    if (response.status === 401) { showLogin(); return; }
    if (!response.ok) {
      let detail = "שגיאה " + response.status;
      try { detail = (await response.json()).detail || detail; } catch (e) {}
      throw new Error(detail);
    }
    LOG.truncated = response.headers.get("X-Journal-Search-Truncated") === "true";
    const total = response.headers.get("X-Journal-Total");
    LOG.total = total != null && /^\d+$/.test(total) ? Number(total) : null;
    const page = await response.json();
    LOG.more = page.length >= LOG.limit;
    LOG.rows = last != null ? LOG.rows.concat(page) : page;
    JOURNAL = LOG.rows;
    LOG.err = "";
  } catch (e) {
    LOG.rows = null; LOG.err = e.message;
    toast("טעינת היומן נכשלה: " + e.message);
  }
  if (current === "logs") renderCurrent();
}
function logFilter(key, value) {
  LOG[key] = value;
  if (key === "range" && value === "custom" && !LOG.since && !LOG.until) { renderCurrent(); return; }   // קודם התאריכים
  loadJournalData();
}
function logMore() { loadJournalData(true); }
function logEventLabel(ev) { const e = (LOG.events || []).find((x) => x.event === ev); return e ? e.label : ev; }

function logRowHtml(r, i) {
  const cls = journalCls(r);
  const t = isToday(r.ts) ? fmtHour(r.ts) : `${fmtDate(r.ts)} ${fmtHour(r.ts)}`;
  return { attrs: `onclick="openLogDetail(${i})" tabindex="0"`, cells: [
    `<span class="name">${esc(r.label || r.event || "")}</span>${r.text ? `<span class="sub">${esc(r.text)}</span>` : ""}`,
    esc(r.user || "המערכת"),
    `<span class="mono">${esc(t)}</span>`,
    `<span title="${esc(LOG_SEV_HE[cls] || "")}">${UI.pill(cls, LOG_SEV_SHORT[cls] || cls)}</span>`,
    `<div class="acts"><button class="btn sm" onclick="event.stopPropagation();openLogDetail(${i})">פרטים</button></div>`] };
}
function logBarHtml(rows) {
  const users = new Set((USERS || []).map((u) => u.username));
  for (const r of rows) if (r.user) users.add(r.user);
  if (LOG.user) users.add(LOG.user);
  const opt = (v, label, cur) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label)}</option>`;
  const events = LOG.events
    ? `<select aria-label="סוג אירוע" onchange="logFilter('event', this.value)">${opt("", "כל סוגי האירועים", LOG.event)}${LOG.events.map((e) => opt(e.event, e.label, LOG.event)).join("")}</select>`
    : `<span class="cap" title="‏/journal/events לא נקרא">סוג: לא נקרא</span>`;
  const custom = LOG.range === "custom"
    ? `<input type="datetime-local" aria-label="מתאריך" value="${esc(LOG.since)}" onchange="logFilter('since', this.value)"><input type="datetime-local" aria-label="עד תאריך" value="${esc(LOG.until)}" onchange="logFilter('until', this.value)">`
    : "";
  return `<div class="dg-bar"><input type="search" value="${esc(LOG.q)}" placeholder="חיפוש בטקסט (מחשב, כיתה, אימג')…" aria-label="חיפוש ביומן" onchange="logFilter('q', this.value.trim())" style="width:240px">${events}<select aria-label="משתמש" onchange="logFilter('user', this.value)">${opt("", "כל המשתמשים", LOG.user)}${[...users].sort().map((u) => opt(u, u, LOG.user)).join("")}</select><select aria-label="טווח זמן" onchange="logFilter('range', this.value)">${LOG_RANGES.map(([v, l]) => opt(v, l, LOG.range)).join("")}</select>${custom}<span class="sp"></span><span class="n">${rows.length} אירועים</span></div>`;
}

function logs() {
  if (!LOG.rows && !LOG.err) return pagePlaceholder();
  const rows = LOG.rows || [];
  const filters = [LOG.q ? `"${LOG.q}"` : "", LOG.event ? logEventLabel(LOG.event) : "", LOG.user,
    LOG.range ? (LOG.range === "custom" ? `${LOG.since || "…"} – ${LOG.until || "…"}` : LOG_RANGES.find(([v]) => v === LOG.range)[1]) : ""].filter(Boolean);
  const sub = LOG.rows
    ? [LOG.total != null ? `${rows.length} מתוך ${LOG.total} אירועים` : `${rows.length} אירועים מוצגים (הסך לא נמסר)`, filters.length ? "מסונן: " + filters.join(" · ") : "בלי סינון — האחרונים"].map(esc).join(" · ")
    : `‏/journal לא נקרא: ${esc(LOG.err)}`;
  const pill = !LOG.rows ? UI.pill("err", "לא נקרא") : LOG.truncated ? UI.pill("warn", "חיפוש חלקי") : "";
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "ניהול" }, { label: "יומן" }],
    icon: "t-log", name: "יומן", sub, pill,
    actions: `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button><a class="btn" href="${esc(logCsvHref())}" download>ייצוא CSV</a>` });
  let body;
  if (!LOG.rows) body = UI.note("err", `לא הצלחתי לקרוא את היומן: ${esc(LOG.err)}`);
  else {
    const truncated = LOG.truncated ? UI.note("warn", "החיפוש כיסה רק את השורות האחרונות ביומן — צמצמו עם טווח תאריכים או סוג אירוע.") : "";
    const table = UI.datagrid({ cls: "acts-on", columns: ["מה קרה", "מי", "זמן", "חומרה", ""], rows: rows.map(logRowHtml),
      empty: filters.length ? "אין אירועים שתואמים לסינון" : "היומן ריק — עדיין לא נרשם אירוע" });
    const more = LOG.more
      ? `<button class="btn sm" onclick="logMore()">עוד ${LOG.limit}</button>`
      : `<span class="cap">זה הכול לסינון הזה</span>`;
    body = `<div class="c12 card"><div class="card-h"><span>אירועים</span></div>${logBarHtml(rows)}${truncated ? `<div style="padding:10px 12px 0">${truncated}</div>` : ""}<div class="card-b flush">${table}</div><div class="dg-bar foot"><span class="n">מוצגים ${rows.length}</span><span class="sp"></span>${more}</div></div>`;
  }
  const cap = `<div class="c12 cap">לחיצה על שורה פותחת את הפרטים הטכניים (event, המזהים). החומרה היא כפי שהשרת קבע (severity ב-/journal). הייצוא הוא אותו סינון, עד 20,000 שורות. עמודת "יעד" מבנית — דורש API.</div>`;
  return `<div class="page">${header}<div class="body">${body}${cap}</div></div>`;
}
function openLogDetail(i) {
  const row = (LOG.rows || [])[i];
  if (!row) { toast("אירוע לא נמצא"); return; }
  const cls = journalCls(row);
  const who = row.user || "המערכת";
  openDrawer("פרטי אירוע", `<div class="page drw">${UI.kv([
    ["זמן", `<span class="mono">${esc(fmtDate(row.ts))} ${esc(fmtHour(row.ts))}</span>`],
    ["חומרה", UI.status(cls, LOG_SEV_HE[cls] || cls)],
    ["מה קרה", esc(row.label || "")],
    ["פירוט", esc(row.text || "") || `<span class="muted">—</span>`],
    ["event", `<span class="mono">${esc(row.event || "")}</span>`],
    ["מי", esc(who)]])}</div>`, {
      sub: `${row.label || "אירוע"} · ${fmtDate(row.ts)} · ${fmtHour(row.ts)}`,
      meta: UI.pill("", who) + (cls === "err" ? UI.pill("err", "שגיאה") : UI.pill(cls, LOG_SEV_HE[cls] || cls)),
    });
}

/* ---------- #954 גל 8: רשת כאובייקט — תרשים · חיבורים פיזיים · רשת הפצה · פורטים ----------
   docs/design/console-redesign/network.md, network-nics.md, network-deploy.md.
   "רשת" בעץ הוא דף (הכרעת נדב על העץ: קליק = הדף, דאבל = פותח/סוגר), והצמתים
   "חיבורים פיזיים / רשת הפצה" פותחים את אותו אובייקט בלשונית. "פורטים" =
   הדף הקיים של גל 5 (pages.ports) — לא נגענו, רק מקושר כלשונית.
   מקורות (docs/interfaces.md, לא ממציאים שדות): /net/interfaces (net.js:
   loadNet → NICS), /net/config (NETCFG), /net (NET — מי נראה ברשת), /ports
   (bind — "מה מותר על כל וילן" עד מודל וילן, #705), /monitor/machines (משכפלים/בנייה, online), /storage-nodes
   (+ …/machines.connected — שרתי משנה), /net/interfaces/{n}/probe (מי עוד עונה),
   /sessions (מי מחובר לקונסולה, #1039), /net/interfaces/{n}/leases (חכירות dnsmasq של כרטיס ההפצה, #1039).
   כל מקור נכשל בנפרד (NETW.err) ומוצג "לא נקרא" — לא "אין" (עיקרון 5).
   וילן = הכרטיס (1:1, "לפי הגדרה") — מודל וילן אמיתי דורש API (#705).
   כיתות = תיבה סטטית "v2" — לא נבנה מעבר לזה (v1 = בנייה/שיכפול/שרתים).
   ‏#1263: כל כרטיס — אופן הגדרה אחד (רשת הפצה / DHCP מהרשת / כתובת ידנית)
   בחלון "הגדרת כרטיס…" (editNicConfig, netcfg.js); הגדרות רשת ההפצה בחלון
   של לשונית "רשת הפצה" (editDeployNet, net.js). כיבוי DHCP — חלון אישור
   הכרטיס (#53). שינוי כתובת עדיין חמוש בהחזרה של #56. */
const netwInit = () => ({ nics: null, cfg: null, ports: null, mon: null, nodes: null, sessions: null, leases: null, probe: {}, sel: null, at: "", err: {} });
let NETW = netwInit();
// ‏#1088: מקור כתובת ההפצה — /net/deploy: {configured, source, interface, url, hint}. null = לא נקרא.
let NET_DEPLOY = null;

async function loadNetwork() {
  if (!isAdmin()) return;
  const err = {};
  const grab = async (key, fn) => { try { return await fn(); } catch (e) { err[key] = e.message; return null; } };
  const list = async (url) => { const r = await api(url); if (!Array.isArray(r)) throw new Error("תשובה שאינה רשימה"); return r; };
  // ‏#705: השירותים לכל כרטיס (nicsvc.js) — במקביל, נכשל בנפרד ומוצג "לא נקרא" בעמודה שלו
  const services = grab("services", () => (typeof nicsvcLoad === "function" ? nicsvcLoad() : null));
  // ‏#1468: "כרטיס לעדכוני VM" (vmupdate.js) — נכשל בנפרד ומוצג "לא נקרא" בכרטיס שלו
  const vmnet = typeof vmnetLoad === "function" ? vmnetLoad() : null;
  const [nics, cfg, net, ports, mon, nodes, deploy, sessions] = await Promise.all([
    grab("interfaces", async () => { await loadNet(); return Array.isArray(NICS) ? NICS : null; }),
    grab("config", () => api("/net/config")),
    grab("net", () => api("/net")),
    grab("ports", () => api("/ports")),
    grab("monitor", () => api("/monitor/machines")),
    grab("nodes", loadNetworkNodes),
    grab("deploy", () => api("/net/deploy")),
    grab("sessions", () => list("/sessions")),
  ]);
  await services;
  await vmnet;
  NETW = { ...NETW, nics, cfg, ports: Array.isArray(ports) ? ports : null, mon: Array.isArray(mon) ? mon : null,
           nodes, sessions, leases: null, at: clockNow(), err };
  // ‏#1039: חכירות dnsmasq — של כרטיס ההפצה בלבד (הלשונית שמציגה אותן); הכרטיס ידוע רק אחרי /net/interfaces.
  const focus = netDeployNic();
  if (focus) NETW.leases = await grab("leases", () => api(`/net/interfaces/${encodeId(focus.name)}/leases`));
  NET_DEPLOY = deploy && typeof deploy === "object" ? deploy : null;
  if (cfg) NETCFG = cfg;                                   // netcfg.js (editNicConfig, נתיבים, rollback) קורא מכאן
  if (Array.isArray(net)) { NET = net; NET_ERR = ""; } else { NET = null; NET_ERR = err.net || "תשובה שאינה רשימה"; }
  PORTS_NICS = nics; portsNicsError = err.interfaces || "";
  if (!NETW.sel || !(nics || []).some((n) => n.name === NETW.sel)) NETW.sel = (netDeployNic() || (nics || [])[0] || {}).name || null;
  populateSidebarNics();
  if (current === "network") renderCurrent();
}

/* שרתי משנה: /storage-nodes (403/409 = אין שרתי משנה, לא שגיאה — כמו העץ) + מצב חיבור נמדד לכל אחד. */
async function loadNetworkNodes() {
  if (!(ME && ME.capabilities && ME.capabilities.enroll_secondary)) return [];
  let nodes;
  try { nodes = await api("/storage-nodes"); }
  catch (e) { if (e.status === 403 || e.status === 409) return []; throw e; }
  return Promise.all(nodes.map(async (n) => {
    if (n.disabled_at) return { ...n, connected: null, error: "" };
    try { const a = await api(`/storage-nodes/${encodeId(n.id)}/machines`); return { ...n, connected: !!a.connected, error: a.error || "" }; }
    catch (e) { return { ...n, connected: false, error: e.message }; }
  }));
}

/* --- עזרי נתונים (טהורים — נבדקים ב-node) --- */
function netCfgRow(name) { return ((NETCFG && NETCFG.interfaces) || []).find((r) => r.name === name) || null; }
/* ‏#1264: ה-bond שהכרטיס חבר בו (‏/net/config member_of), או null. */
function netBondMember(name) { return (netCfgRow(name) || {}).member_of || null; }
function netLiveChecked() { return !!(NETCFG && NETCFG.live && NETCFG.live.checked); }
function netLiveAddrs(n) { const cfg = netCfgRow(n.name); return ((cfg && netLiveChecked()) ? cfg.live_addresses : n.addresses) || []; }
function ipInt(ip) { const m = String(ip || "").match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/); return m ? (((+m[1] << 24) | (+m[2] << 16) | (+m[3] << 8) | +m[4]) >>> 0) : null; }
function cidrParts(cidr) {
  const m = String(cidr || "").match(/^(\d+\.\d+\.\d+\.\d+)\/(\d+)$/);
  if (!m) return null;
  const bits = Math.min(32, +m[2]), mask = bits ? (0xffffffff << (32 - bits)) >>> 0 : 0;
  return { net: (ipInt(m[1]) & mask) >>> 0, mask, bits };
}
function nicHasIp(n, ip) { const x = ipInt(ip); return x !== null && netLiveAddrs(n).some((c) => { const p = cidrParts(c); return !!p && ((x & p.mask) >>> 0) === p.net; }); }
function netNicFor(ip) { return (NETW.nics || []).find((n) => nicHasIp(n, ip)) || null; }
function netDeployNic() {
  const l = (NETW.nics || []).filter((n) => classroomsOn() || !n.proxy);
  return l.find((n) => n.enabled) || l.find((n) => (n.dhcp_live || {}).state === "serving") || null;
}
function netNetworkOf(n) { const c = netLiveAddrs(n)[0]; const p = cidrParts(c); if (!p) return c || ""; return `${[24, 16, 8, 0].map((s) => (p.net >>> s) & 255).join(".")}/${p.bits}`; }
function urlHost(u) { const m = String(u || "").match(/^[a-z]+:\/\/\[?([^\]/:]+)/i); return m ? m[1] : ""; }

/* "מה מותר על כל וילן" — רק מה ש-/ports.bind אומר (#996); בלי bind בכלל = דורש API (#705). null = לא ידוע.
   ‏#1039: ‏bind null = טבלת הסוקטים לא נקראה ≠ [] = נקראה ואף אחד לא מאזין. שורה אחת שלא נקראה = לא ידוע (עיקרון 5). */
function netBindUnread() { return Array.isArray(NETW.ports) && NETW.ports.some((p) => p.bind === null); }
function netAllowedUnknown() { return netBindUnread() ? "מה מותר — טבלת הסוקטים לא נקראה" : "מה מותר — דורש API (#705)"; }
function netServicesOn(n) {
  const ports = NETW.ports;
  // ‏bind בשרת: רשימת "כתובת[/bits][:port]" (ריקה = נקראה ואף אחד לא מאזין), null = לא נקרא, או מחרוזת (שרת ישן).
  const bindsOf = (p) => (Array.isArray(p.bind) ? p.bind : p.bind ? [p.bind] : []).map((b) => String(b).replace(/:\d+$/, "").replace(/\/\d+$/, ""));
  if (!Array.isArray(ports) || !ports.some((p) => p.bind !== undefined) || netBindUnread()) return null;
  const ips = netLiveAddrs(n).map((c) => c.split("/")[0]);
  const out = [];
  for (const p of ports) {
    const binds = bindsOf(p);
    if (binds.some((b) => b === "0.0.0.0" || b === "::" || b === "[::]" || b === "*" || ips.includes(b))) out.push(`${p.name} ${p.port}`);
  }
  return out;
}
/* סוג הוילן "לפי הגדרה" (1:1 עם הכרטיס; מודל וילן = #705): הפצה / proxy / רשת המכללה / שרתים / לא מוגדר.
   מודל האתרים (נדב 18/09): לכל אתר שלושה וילנים — שרתים / הפצה / כיתות. אין וילן "ניהול" נפרד: הקונסולה, הערוץ
   הבין-שרתי (8443) ו-DHCP המשרדים כולם על וילן השרתים; כרטיס שנושא רק 8443 הוא עדיין וילן השרתים. */
function netVlanOf(n) {
  const svc = netServicesOn(n) || [];
  if (n.enabled) return { kind: "deploy", label: "וילן ההפצה" };
  if (n.proxy) return { kind: "proxy", label: "PXE proxy — DHCP של רשת אחרת" };
  if (n.trunk) return { kind: "trunk", label: "רשת המכללה (trunk)" };
  if (svc.some((s) => /8081/.test(s))) return { kind: "mgmt", label: "שרתים — קונסולה" };
  if (svc.some((s) => /8443/.test(s))) return { kind: "inter", label: "שרתים — בין-שרתי" };
  return { kind: "none", label: "לא מוגדר" };
}
function netRoleLabel(n) {
  const parts = [];
  if (isMgmtNic(n.name)) parts.push("ניהול");   // #1263: נבחר בהתקנה (NETCFG.management_interface)
  if (netBondMember(n.name)) parts.push(`הפצה · ${netBondMember(n.name)}`);   // #1264: חבר ב-bond של רשת ההפצה
  if (n.enabled) parts.push("הפצה · DHCP");
  if (n.proxy) parts.push("PXE proxy");
  if (n.trunk) parts.push("רשת המכללה");
  const svc = netServicesOn(n);
  if (svc && svc.length) parts.push(svc.join(" · "));
  if (parts.length) return esc(parts.join(" · "));
  if (svc === null && netBindUnread()) return `<span class="muted" title="bind של /ports: טבלת הסוקטים לא נקראה">— לא נקרא</span>`;
  return svc === null ? `<span class="muted" title="דורש API (#705): מודל וילן / bind">— דורש API</span>` : `<span class="muted">—</span>`;
}
function netLinkStatus(n) {
  if (!n.present || n.state === "missing") return UI.status("", "לא קיים במערכת");
  // ‏"1000 Mbps" לטיני בתוך משפט עברי — בלי bdi ה-RTL מהפך אותו ל-"Mbps 1000".
  if (n.state === "up") return `<span class="st ok">מחובר${typeof n.speed_mbps === "number" && n.speed_mbps > 0 ? ` · <bdi dir="ltr">${esc(n.speed_mbps)} Mbps</bdi>` : ""}</span>`;
  if (n.state === "down") return UI.status("", "מנותק");
  return UI.status("unk", "לא נקרא");
}
/* פער = מצב, לא הערה: לא נקרא (unk) ≠ לא מנוהל (אפור) ≠ תואם (ירוק) ≠ לא תואם (אדום). */
function netGapStatus(n) {
  if (!NETCFG) return UI.status("unk", "לא נקרא");
  const cfg = netCfgRow(n.name);
  if (!netLiveChecked()) return UI.status("unk", "לא נקרא");
  if (!cfg || cfg.mode === "manual") return UI.status("", "לא מנוהל");
  return (cfg.mismatches || []).length ? UI.status("err", cfg.mismatches.join(" · ")) : UI.status("ok", "תואם");
}
function netConfiguredText(n) {
  if (!NETCFG) return "לא נקרא";
  const cfg = netCfgRow(n.name);
  if (!cfg || cfg.mode === "manual") return "לא מנוהל";
  if (cfg.mode === "dhcp") return "DHCP";
  const bits = typeof maskBits === "function" && cfg.netmask ? `/${maskBits(cfg.netmask)}` : "";
  return `static ${cfg.address || "—"}${bits}${cfg.gateway ? ` · gw ${cfg.gateway}` : ""}`;   // mono LTR — בלי מילה עברית שתהפוך את הסדר
}
function netDhcpCell(n, withRange = true) {
  const live = n.dhcp_live || { state: "unknown" };
  const cls = dhcpLiveClass(live.state);
  const st = UI.status(cls === "ok" ? "ok" : cls === "warn" ? (live.state === "unknown" ? "unk" : "warn") : "", n.dhcp_live_label || "לא ידוע");
  const stored = `שמור: ${n.enabled ? "מופעל" : n.proxy ? "proxy" : "כבוי"}`;
  const range = withRange && n.enabled && n.range_start ? ` · ${n.range_start}–${n.range_end}` : "";
  const div = n.dhcp_diverged ? ` ${UI.pill("warn", "לא תואם למצב הפעיל")}` : "";
  return `${st}<span class="sub">${esc(stored)}${range ? ` · ${ltr(range.slice(3))}` : ""}${div}</span>`;   // הטווח LTR מבודד — אחרת "200–100"
}
function netProbeStatus(name) {
  const p = NETW.probe[name];
  if (!p) return { cls: "", text: "לא נבדק" };
  if (p.running) return { cls: "run", text: "בודק…" };
  if (p.error) return { cls: "unk", text: `הבדיקה נכשלה (${p.at}): ${p.error}` };
  if (!p.checked) return { cls: "unk", text: `הבדיקה לא רצה (${p.at}) — לא ידוע מי עונה` };
  return p.servers.length ? { cls: "err", text: `עונים: ${p.servers.join(", ")} (נבדק ${p.at})` } : { cls: "ok", text: `אף שרת אחר לא עונה (נבדק ${p.at})` };
}
async function netProbe(nameEnc) {
  let name = nameEnc; try { name = decodeURIComponent(nameEnc); } catch (e) {}
  NETW.probe[name] = { running: true };
  if (current === "network") renderCurrent();
  try {
    const r = await api(`/net/interfaces/${encodeId(name)}/probe`);
    NETW.probe[name] = { checked: r.checked === true, servers: Array.isArray(r.servers) ? r.servers : [], at: clockNow() };
  } catch (e) { NETW.probe[name] = { error: e.message, at: clockNow() }; toast("בדיקת DHCP נכשלה: " + e.message); }
  if (current === "network") renderCurrent();
}
function netNic(nameEnc) { let name = nameEnc; try { name = decodeURIComponent(nameEnc); } catch (e) {} return (NETW.nics || []).find((n) => n.name === name) || null; }
function netEditNic(nameEnc) { const n = netNic(nameEnc); if (n) editNicConfig(n.name); else toast("כרטיס לא נמצא"); }
function netEditDeploy(nameEnc) { const n = netNic(nameEnc); if (n) editDeployNet(n.name); else toast("כרטיס לא נמצא"); }
/* ‏#1263: "כיבוי DHCP…" — בכותרת כרטיס ה-DHCP ובתפריט של כרטיס ההפצה. חלון אישור (#53, #1270). */
function netDhcpOff(nameEnc) {
  const n = netNic(nameEnc); if (!n) return;
  sheet({ title: "לכבות את ה-DHCP?", sub: n.name, danger: true, submitLabel: "כיבוי DHCP",
    note: impactList([["w", "הכרטיס מפסיק לחלק כתובות ולענות ל-PXE"]]) + offMeansNote(PORT_OFF_MEANS.dhcp),
    onSubmit: async () => { await saveNic(n.name, nicBody(n, { enabled: false, proxy: false })); await loadNetwork(); } });
}
function netDescribe(nameEnc) {
  const n = netNic(nameEnc); if (!n) return;
  sheet({ title: "תיאור הכרטיס", sub: n.name, fields: [{ id: "description", label: "תיאור חופשי", value: n.description, placeholder: "למשל: וילן 700" }],
    onSubmit: async (v) => { await put(`/net/interfaces/${encodeId(n.name)}/description`, { description: v.description }); await loadNetwork(); } });
}
function netForget(nameEnc) {
  const n = netNic(nameEnc); if (!n) return;
  confirmSheet("להסיר את הגדרות הכרטיס?", n.name, "הסרת ההגדרות",
    async () => { await del(`/net/interfaces/${encodeId(n.name)}`); await loadNetwork(); },
    [["b", "ההגדרות והתיאור של הכרטיס יימחקו"], ["w", "אם רץ עליו DHCP — הוא ייכבה"]]);
}
function netRegister(macEnc) { let mac = macEnc; try { mac = decodeURIComponent(macEnc); } catch (e) {} openAddMachine({ mac }); }

/* --- מודל התרשים (טהור): מסלולים = כרטיס → וילן (לפי הגדרה) → מי מחובר --- */
/* רשימת כתובות בתיבה: עד שתיים ו-"+N" — הרשימה המלאה ב-tip (title). תיבה ברוחב קבוע, והטקסט חייב להישאר בתוכה (18/09).
   כל כתובת מבודדת לבדה (bidi): " · " אינו ASCII, ורצף של שתי כתובות סביבו היה מתהפך בשורה עברית. */
function netAddrList(ips) {
  if (!ips.length) return "ללא כתובת";
  return ips.slice(0, 2).map(bidi).join(" · ") + (ips.length > 2 ? ` · ${bidi(`+${ips.length - 2}`)}` : "");
}
/* שורת "מותר" קצרה: שירותים באותו שם מתקפלים לפורט אחד — "HTTP 8080/8081". הרשימה הגולמית (netServicesOn) נשארת בכרטיס הנבחר. */
function netAllowedShort(list) {
  const byName = new Map();
  for (const item of list) {
    const i = item.lastIndexOf(" "), name = i > 0 ? item.slice(0, i) : item, port = i > 0 ? item.slice(i + 1) : "";
    if (!byName.has(name)) byName.set(name, []);
    if (port) byName.get(name).push(port);
  }
  return [...byName].map(([name, ports]) => (ports.length ? `${name} ${ports.join("/")}` : name)).join(" · ");
}
function netDiagramModel() {
  const nics = NETW.nics || [];
  const lanes = nics.map((n) => {
    const led = !n.present ? "off" : n.state === "up" ? "ok" : n.state === "down" ? "off" : "warn";
    return { nic: n, vlan: netVlanOf(n), allowed: netServicesOn(n), led, clients: [], rows: [] };
  });
  const orphans = [];
  const laneOf = (kinds) => kinds.map((k) => lanes.find((l) => l.vlan.kind === k)).find(Boolean) || null;
  const place = (client, ip, fallback) => {
    const nic = ip ? netNicFor(ip) : null;
    const lane = (nic && lanes.find((l) => l.nic === nic)) || (fallback ? fallback() : null);
    if (lane) { lane.clients.push(client); if (client.row) lane.rows.push(client.row); } else orphans.push(client);
  };
  // משכפלים ובנייה — /monitor/machines (online נקבע בשרת); בלי כתובת → וילן ההפצה (שם הם יופיעו כשיעלו)
  const roles = [["cloner", "משכפלים"], ["build", "מחשבי בנייה"]];
  for (const [role, title] of roles) {
    const list = (NETW.mon || []).filter((m) => m.role === role);
    const byLane = new Map();
    for (const m of list) {
      const nic = m.ip ? netNicFor(m.ip) : null;
      const lane = (nic && lanes.find((l) => l.nic === nic)) || laneOf(["deploy"]) || null;
      if (!lane) { orphans.push({ title: `${title} — ${m.name || m.mac}`, sub: m.ip || "ללא כתובת", led: "", dashed: true }); continue; }
      if (!byLane.has(lane)) byLane.set(lane, []);
      byLane.get(lane).push(m);
    }
    for (const [lane, ms] of byLane) {
      const on = ms.filter((m) => m.online).length;
      const ips = ms.map((m) => m.ip).filter(Boolean), tail = ` · ${on} מתוך ${ms.length} מחוברים`;
      ms.forEach((m) => lane.rows.push({ name: m.name || m.mac, ip: m.ip || "", kind: role, ok: !!m.online }));
      lane.clients.push({ kind: role, title: `${title} — ${ms.map((m) => m.name || m.mac).join(", ")}`,
        sub: `${netAddrList(ips)}${tail}`, tip: `${ips.map(bidi).join(" · ") || "ללא כתובת"}${tail}`,
        led: on === ms.length ? "ok" : on ? "warn" : "", dashed: on === 0 });
    }
  }
  // הקונסולה — אנחנו (הדף הזה נטען = ראיה); מי מחובר — /sessions (#1039; last_seen מדויק עד דקה)
  const ses = NETW.sessions;
  const who = ses ? ses.map((x) => `${x.user} (${bidi(x.ip || "—")})`).join(" · ") : "";
  place({ kind: "console", title: `קונסולה — ${ME ? ME.username : ""}`, led: "ok",
    sub: ses === null ? `session פעיל · מי מחובר — לא נקרא${NETW.err.sessions ? `: ${NETW.err.sessions}` : ""}` : `${ses.length} מחוברים: ${who || "—"}`,
    tip: ses === null ? undefined : ses.map((x) => `${x.user} · ${bidi(x.ip || "—")} · נכנס ${ago(x.since)} · נראה ${ago(x.last_seen)}`).join(" · ") || undefined }, null,
    () => laneOf(["mgmt", "trunk", "inter", "none", "proxy", "deploy"]));
  // שרתי משנה — /storage-nodes + connected נמדד. שרת המשנה יושב מאחורי FW משלו (interfaces.md §18, נדב 18/09): הראשי יוזם,
  // ורק 8443 פתוח ביניהם — ולכן הקו מקווקו (מעבר ל-FW, לא לקוח על הוילן), והתיבה נשארת לפי connected.
  for (const n of NETW.nodes || []) {
    const off = !!n.disabled_at;
    const state = off ? "מושבת" : n.connected ? "מחובר" : `לא ענה${n.error ? `: ${n.error}` : ""}`;
    place({ kind: "branch", row: { name: n.label, ip: urlHost(n.base_url), kind: "branch", ok: off ? false : !!n.connected }, title: `${n.label} — שרת משנה`, led: off ? "" : n.connected ? "ok" : "err", dashed: off, viaFw: true,
      sub: `דרך FW · 8443 בלבד · ${bidi(n.base_url || "")} · ${state}`,
      tip: `שרת המשנה מאחורי FW משלו; הראשי יוזם, רק 8443 · ${bidi(n.base_url || "")} · ${state}` },
      urlHost(n.base_url), () => laneOf(["inter", "mgmt", "trunk", "none"]));
  }
  // לא רשומים — /net registered=false, לפי הכתובת שקיבלו
  const unreg = (NET || []).filter((d) => d.registered === false);
  const unregByLane = new Map();
  for (const d of unreg) {
    const nic = d.ip ? netNicFor(d.ip) : null;
    const lane = (nic && lanes.find((l) => l.nic === nic)) || laneOf(["deploy"]);
    if (!lane) { orphans.push({ title: `לא רשום — ${d.mac}`, sub: d.ip || "ללא כתובת", led: "warn" }); continue; }
    if (!unregByLane.has(lane)) unregByLane.set(lane, []);
    unregByLane.get(lane).push(d);
  }
  for (const [lane, ds] of unregByLane) {
    lane.unreg = ds.length;
    ds.forEach((d) => lane.rows.push({ name: d.mac, ip: d.ip || "", kind: "unreg", ok: null }));
    const last = ds[0];
    lane.clients.push({ kind: "unreg", led: "warn",
      title: ds.length === 1 ? `לא רשום — ${bidi(last.mac)}` : `לא רשומים — ${ds.length} (${bidi(ds.slice(0, 2).map((d) => d.mac).join(", "))}…)`,
      sub: `${netAddrList(ds.map((d) => d.ip).filter(Boolean))}${last.boot && last.boot.label ? ` · ${last.boot.label}` : ""} · ${ago(last.last_seen)}`,
      tip: `${ds.map((d) => d.ip).filter(Boolean).map(bidi).join(" · ") || "ללא כתובת"}${last.boot && last.boot.label ? ` · ${last.boot.label}` : ""} · ${ago(last.last_seen)}` });
  }
  // כיתות — v2: תיבה סטטית בלבד. #1081: v1 לא מצייר אותה.
  if (classroomsOn()) {
    const classLane = laneOf(["proxy", "trunk", "none", "mgmt"]);
    if (classLane) classLane.clients.push({ kind: "class", title: "כיתות — v2", sub: "מחשבי כיתה אינם במהדורה זו", led: "", dashed: true });
  }
  return { lanes, orphans };
}

/* --- תרשים הרשת (שלב 1, המוקאפ המאושר): השרת למעלה, וילן לכל כרטיס מוגדר. בכל וילן הכרטיס וה-IP של השרת,
   ואדום כשהכרטיס מנותק. לחיצה פותחת מגירה (סקירה + מחשבים). הנתונים כולם מה-API הקיים — המודל netDiagramModel;
   וילני כיתות (proxy) לא מוצגים ב-v1 (v1.2). כרטיס ללא תפקיד ("לא מוגדר") נשאר בלשונית "חיבורים פיזיים". --- */
/* רצף ASCII (כתובת, MAC, מהירות) בתוך משפט עברי מתהפך ב-RTL ("Mb/s 1000") — מבודדים אותו ב-LRI…PDI (כמו ltr() ב-HTML). */
function bidi(text) { const t = String(text); return /^[\x20-\x7e]+$/.test(t) ? `⁦${t}⁩` : t; }
const clipText = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + "…" : String(s));
function netMapLanes(model) {
  return model.lanes.filter((l) => l.vlan.kind !== "none" && (classroomsOn() || l.vlan.kind !== "proxy"));
}
function netLaneIp(n) { const c = netLiveAddrs(n)[0]; return c ? String(c).split("/")[0] : ""; }
function netLaneServices(lane) {
  if (lane.allowed === null) return netBindUnread() ? "טבלת הסוקטים לא נקראה" : "מה מותר: דורש API";
  return lane.allowed.length ? netAllowedShort(lane.allowed) : "אף שירות לא מאזין";
}
/* מחשבים שכתובתם מחוץ לרשת של כל כרטיס — אזהרה מתחת לתרשים (היתה ב-main; נעלמה בשכתוב של שלב 1). */
function netOrphansNote(model) {
  const o = model.orphans || [];
  return o.length ? UI.note("warn", `${o.length} מחוברים עם כתובת מחוץ לרשתות הכרטיסים: ${esc(o.map((x) => x.title).join(" · "))}`) : "";
}
function netMapBody(model) {
  return (netMapLanes(model).length ? netMapSvg(model)
    : UI.note("warn", "אין כרטיס שמוגדר כוילן (הפצה, ניהול או בין-שרתי) — ראו את הלשונית \"חיבורים פיזיים\".") + netMapSvg(model)) + netOrphansNote(model);
}
function netMapSvg(model) {
  const lanes = netMapLanes(model), n = lanes.length;
  const srv = ME && ME.server_name ? ME.server_name : "שרת אימג'ים";
  const BW = n <= 2 ? 300 : n <= 3 ? 250 : 200, GAP = 16, TW = n * BW + Math.max(0, n - 1) * GAP, W = Math.max(760, TW + 40), x0 = (W - TW) / 2;
  const SW = 300, sx = (W - SW) / 2, T = (x, y, cls, text) => `<text class="${cls}" x="${x}" y="${y}" text-anchor="middle">${esc(text)}</text>`;
  let wires = "", ports = "", boxes = "";
  lanes.forEach((lane, i) => {
    const bad = lane.led === "off", warn = lane.led === "warn", cx = x0 + i * (BW + GAP) + BW / 2;
    const px = n === 1 ? W / 2 : sx + 30 + i * ((SW - 60) / (n - 1)), ly = 118 + Math.min(i, n - 1 - i, 2) * 14;
    const cls = bad ? " bad" : warn ? " warn" : "";
    wires += `<path class="nm-wire${cls}" d="M${px} 94V${ly}H${cx}V170"/>`;
    ports += `<rect class="nm-port${cls}" x="${px - 7}" y="87" width="14" height="14" rx="3"/>`;
    const nic = lane.nic, ip = netLaneIp(nic), enc = encodeId(nic.name);
    const nicLine = bad ? `${nic.name} · מנותק` : warn ? `${nic.name} · קישור לא נקרא` : `${nic.name} · ${ip || "ללא כתובת"}`;
    const chip = bad ? "מנותק" : netLaneServices(lane);
    boxes += `<g class="nm-vl" role="button" tabindex="0" aria-label="פרטי ${esc(lane.vlan.label)}" onclick="netLaneOpen('${enc}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();netLaneOpen('${enc}')}">`
      + `<title>${esc(`${lane.vlan.label} — ${nicLine} — ${chip}`)}</title>`
      + `<rect class="nm-box${cls}" x="${cx - BW / 2}" y="170" width="${BW}" height="130" rx="9"/>`
      + T(cx, 198, "nm-t" + cls, clipText(lane.vlan.label, Math.floor(BW / 8)))
      + T(cx, 216, "nm-m", bidi(netNetworkOf(nic) || "—"))
      + T(cx, 250, "nm-i" + cls, bidi(nicLine))
      + `<rect class="nm-chip${cls}" x="${cx - (BW - 40) / 2}" y="266" width="${BW - 40}" height="20" rx="10"/>`
      + T(cx, 280, "nm-c" + cls, clipText(chip, Math.floor((BW - 50) / 6.5))) + `</g>`;
  });
  const sub = n ? `מחובר ל-${n} וילנים` : "אין כרטיס מוגדר";
  // ‏gotcha (CLAUDE.md): SVG עם viewBox בלבד קורס ל-0 תחת max-height — width/height מפורשים.
  return `<svg class="netmap-svg" xmlns="http://www.w3.org/2000/svg" width="${W}" height="340" viewBox="0 0 ${W} 340" role="img" aria-label="תרשים הרשת: השרת ו-${n} וילנים">`
    + `<rect class="nm-srv" x="${sx}" y="14" width="${SW}" height="80" rx="9"/>${T(W / 2, 46, "nm-st", srv)}${T(W / 2, 68, "nm-m", `${ME && ME.version ? ME.version + " · " : ""}${sub}`)}`
    + `${wires}${ports}${boxes}${T(W / 2, 326, "nm-m", "לחיצה על וילן פותחת פרטים ורשימת מחשבים")}</svg>`;
}
/* מגירת הוילן: כרטיס · כתובת · שירותים (סקירה) ורשימת המחשבים — מ-lane.rows של המודל. */
function netLaneOpen(nameEnc) {
  let name = nameEnc; try { name = decodeURIComponent(nameEnc); } catch (e) {}
  const lane = netDiagramModel().lanes.find((l) => l.nic.name === name);
  if (!lane) { toast("כרטיס לא נמצא"); return; }
  openDrawer(lane.vlan.label, netLaneDrawerHtml(lane));
}
function netLaneDrawerHtml(lane) {
  const n = lane.nic, cfg = netCfgRow(n.name), up = lane.led === "ok", bad = lane.led === "off";
  const kindOf = { cloner: "משכפל", build: "בנייה", unreg: "לא רשום", branch: "שרת משנה" };
  const note = bad ? UI.note("err", `הכרטיס ${esc(n.name)} מנותק. מחשבים בוילן לא יראו את השרת עד שהקישור יחזור.`) : lane.led === "warn" ? UI.note("warn", "מצב הקישור לא נקרא — לא ידוע אם הכרטיס מחובר.") : "";
  const card = UI.kv([["שם", `<span class="mono">${esc(n.name)}</span>`], ["MAC", `<span class="mono">${esc(n.mac || "—")}</span>`],
    ["מהירות", typeof n.speed_mbps === "number" && n.speed_mbps > 0 ? ltr(`${n.speed_mbps} Mbps`) : "—"], ["קישור", netLinkStatus(n)]]);
  const addr = UI.kv([["כתובת / רשת", `<span class="mono">${esc(netLiveAddrs(n).join(" · ") || "—")}</span> · <span class="mono">${esc(netNetworkOf(n) || "—")}</span>`],
    ["שער", `<span class="mono">${esc((cfg && cfg.gateway) || "—")}</span>`], ["DNS", `<span class="mono">${esc(((cfg && cfg.dns) || []).join(", ") || "—")}</span>`]]);
  const svc = UI.kv([["מאזינים", lane.allowed === null ? esc(netAllowedUnknown()) : esc(lane.allowed.join(" · ") || "אף שירות לא מאזין")], ["DHCP", netDhcpCell(n)]]);
  const rows = lane.rows || [];
  const machines = rows.length ? UI.datagrid({ columns: ["מחשב", "IP", "סוג", "מצב"], rows: rows.map((r) => [esc(r.name), `<span class="mono">${esc(r.ip || "—")}</span>`, esc(kindOf[r.kind] || r.kind),
    r.ok === null ? UI.status("warn", "לא רשום") : r.ok ? UI.status("ok", "מחובר") : UI.status("", "לא מחובר")]) })
    : `<div class="muted">${up ? "אין מחשבים בוילן הזה כרגע." : "אין מחשבים זמינים כשהכרטיס מנותק."}</div>`;
  return `<div class="page drw"><div class="obj-sub">${esc(lane.vlan.label)} · <span class="mono">${esc(netNetworkOf(n) || "—")}</span></div>${note}`
    + `<div><div class="sec">כרטיס</div>${card}<div class="sec" style="margin-top:14px">כתובת</div>${addr}<div class="sec" style="margin-top:14px">שירותים</div>${svc}</div>`
    + `<div><div class="sec">מחשבים (${rows.length})</div>${machines}</div>`
    + `<div class="acts" style="display:flex;gap:6px"><button class="btn" onclick="closeDrawer()">סגירה</button><button class="btn primary" onclick="closeDrawer();openNetwork(1)">לכרטיסי הרשת</button></div></div>`;
}

function netUnreadNotes() {
  const e = NETW.err, out = [];
  const T = UI.noteTech;
  // ‏#1088: אחרי התקנה נקייה — מה שהמנהל צריך לעשות, לפני כל אזהרה אחרת.
  if (NET_DEPLOY && NET_DEPLOY.configured === false) out.push(UI.note("warn", `${esc(NET_DEPLOY.hint || "רשת ההפצה לא הוגדרה")} — <a href="#" onclick="openNetwork(2);return false;">לשונית רשת הפצה</a>`));
  else if (e.deploy) out.push(T("warn", "‏/net/deploy לא נקרא", e.deploy, " — לא ידוע אם רשת ההפצה הוגדרה"));
  if (e.config) out.push(T("warn", "‏/net/config לא נקרא", e.config, ' — "מוגדר" ו"פער" אינם ידועים'));
  else if (NETCFG && NETCFG.live && !NETCFG.live.checked) out.push(T("warn", "המצב בפועל לא נקרא", NETCFG.live.reason || "", " — אף כתובת אינה מאומתת"));
  if (NETCFG && NETCFG.sourced === false) out.push(UI.note("err", "‏/etc/network/interfaces אינו טוען את interfaces.d — כל מה שנכתב שם לא ייקרא באתחול"));
  if (e.net) out.push(T("warn", "‏/net לא נקרא", e.net, ' — "מי נראה ברשת" ו"לא רשומים" אינם ידועים'));
  if (e.ports) out.push(T("warn", "‏/ports לא נקרא", e.ports, ' — "מה מותר על כל וילן" אינו ידוע'));
  if (e.sessions) out.push(T("warn", "‏/sessions לא נקרא", e.sessions, " — מי מחובר לקונסולה אינו ידוע"));
  if (e.leases) out.push(T("warn", "חכירות dnsmasq לא נקראו", e.leases, " — מכונה שקיבלה כתובת ולא דיברה עם השרת אינה נראית"));
  if (e.monitor) out.push(T("warn", "‏/monitor/machines לא נקרא", e.monitor, " — משכפלים ובנייה אינם בתרשים"));
  if (e.nodes) out.push(T("warn", "‏/storage-nodes לא נקרא", e.nodes, " — שרתי משנה אינם בתרשים"));
  const rb = NETCFG && NETCFG.rollback && typeof rollbackBanner === "function" ? rollbackBanner(NETCFG.rollback) : "";
  return (rb ? `<div class="c12">${rb}</div>` : "") + out.map((h) => `<div class="c12">${h}</div>`).join("");
}
const NET_TABS = ["תרשים", "חיבורים פיזיים", "רשת הפצה", "פורטים"];
function netTabClick(i) { return i === 3 ? "selectPageById('ports')" : `openNetwork(${i})`; }
/* ‏#1300 (console-full.html P.network): כותרת אחת לארבע הלשוניות — "רשת", פיל DHCP, ומשנה = הרשתות
   (ניהול · הפצה) כפי שנמדדו. הפעולות של כל לשונית יושבות בכרטיסים שלה; בכותרת רק רענון.
   דף הפורטים קורא את הכרטיסים ל-PORTS_NICS — אותה כותרת משם. לא נקרא = "לא נקרא", לא ריק. */
function netCommonHead() {
  const nics = NETW.nics !== null ? NETW.nics : (typeof PORTS_NICS !== "undefined" ? PORTS_NICS : null);
  if (nics === null) return { sub: `‏/net/interfaces לא נקרא: ${esc(NETW.err.interfaces || (typeof portsNicsError !== "undefined" ? portsNicsError : "") || "")}`, pill: UI.pill("err", "לא נקרא") };
  const mgmt = nics.find((n) => isMgmtNic(n.name));
  const deploy = NETW.nics !== null ? netDeployNic() : nics.find((n) => n.enabled) || null;
  const sub = esc([mgmt ? `ניהול ${netNetworkOf(mgmt) || mgmt.name}` : "ניהול: לא הוגדר", deploy ? `הפצה ${netNetworkOf(deploy) || deploy.name}` : "הפצה: לא הוגדרה"].join(" · "));
  const dcls = deploy ? dhcpLiveClass(deploy.dhcp_live && deploy.dhcp_live.state) : "";
  const pill = !deploy ? UI.pill("", "DHCP כבוי")
    : deploy.dhcp_diverged ? UI.pill("warn", "DHCP לא תואם למצב הפעיל")
    : dcls === "ok" ? UI.pill("ok", "DHCP פעיל") : UI.pill(dcls === "warn" ? "warn" : "", `DHCP: ${deploy.dhcp_live_label || "לא נקרא"}`);
  return { sub, pill };
}
function netHeader(tab) {
  const crumbs = [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "רשת" }];
  const { sub, pill } = netCommonHead();
  return UI.objHeader({ crumbs, icon: "t-network", name: "רשת", sub, pill, actions: `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`, tabs: NET_TABS, tab, tabClick: netTabClick, compact: true });
}

/* --- לשונית 0: התרשים --- */
function networkDiagramTab() {
  const nics = NETW.nics;
  // ‏apple-pending-4 · 4: מצב ה-DHCP — בפיל של כותרת הרשת (netCommonHead). ‏"+ כרטיס" — בכרטיס "כרטיסי רשת".
  const header = netHeader(0);
  let body;
  if (nics === null) body = `<div class="c12">${UI.note("err", `לא הצלחתי לקרוא את כרטיסי הרשת: ${esc(NETW.err.interfaces || "")} — אין מה לצייר`)}</div>`;
  else if (!nics.length) body = `<div class="c12">${UI.empty("לא נמצאו כרטיסי רשת — השרת לא רואה אף כרטיס ב-/sys/class/net, ואין הגדרה שמורה", `<button class="btn" onclick="refreshPage()">רענון</button>`)}</div>`;
  else {
    const model = netDiagramModel();
    // מקור כל נתון (ההערה שהייתה מתחת לתרשים) — ב-title של שורת המשנה של הכרטיס
    const src = `נמדד: כתובות ופער — /net/config · DHCP חי — /net/interfaces · מחוברים — /monitor/machines, /net, /storage-nodes · "מה מותר" — bind של /ports בלבד. וילנים ומדיניות — דורש API (#705).${classroomsOn() ? " כיתות — v2." : ""}`;
    body = netUnreadNotes()
      + `<div class="c12 card"><div class="card-h"><span>מפת הרשת <small title="${esc(src)}">לחיצה על וילן פותחת פרטים</small></span></div><div class="card-b netdiag">${netMapBody(model)}</div></div>`;
  }
  return `<div class="page aligned">${header}<div class="body">${body}</div></div>`;
}

/* --- לשונית 1: חיבורים פיזיים --- */
/* ‏#1300 (console-full.html P.network "physical", אחד-לאחד): כרטיס "כרטיסי רשת" אחד — כרטיס · תיאור · MAC ·
   קישור · כתובת · תפקיד (פיל) · ⋯. בלי כרטיס "נבחר" ובלי כרטיס נתיבים בדף: הנתיבים הסטטיים של כרטיס —
   בפריט "נתיבים סטטיים…" בתפריט שלו (מגירה). הכתובת בפועל בלבד; ההגדרה בקובץ ב-title, ופער (לא תואם) — פיל אדום. */
function netRolePill(n) {
  const why = stripTags(netRoleLabel(n));   // השירותים (bind של /ports) — ב-title
  const t = (cls, text) => `<span title="${esc(why)}">${UI.pill(cls, text)}</span>`;
  if (isMgmtNic(n.name)) return t("info", "ניהול");
  if (n.enabled || netBondMember(n.name)) {
    const host = n.enabled ? n : (NETW.nics || []).find((x) => x.enabled) || n;
    const cls = dhcpLiveClass(host.dhcp_live && host.dhcp_live.state);
    return cls === "ok" ? t("ok", "הפצה · פעיל") : t(cls === "warn" ? "warn" : "", `הפצה · ${host.dhcp_live_label || "לא נקרא"}`);
  }
  if (n.proxy) return t("", "PXE proxy");
  if (n.trunk) return t("warn", "רשת המכללה");
  return t("", "לא מוגדר");
}
function netLinkText(n) {
  if (!n.present || n.state === "missing") return `<span class="muted">לא קיים במערכת</span>`;
  if (n.state === "up") return netSpeedText(n) === "—" ? "מחובר" : `<bdi dir="ltr">${esc(netSpeedText(n))}</bdi>`;
  if (n.state === "down") return "ללא קישור";
  return `<span class="muted">לא נקרא</span>`;
}
function netAddrText(n) {
  const cfg = netCfgRow(n.name);
  if (NETCFG && !netLiveChecked()) return `<span class="muted" title="${esc(`מוגדר: ${netConfiguredText(n)}`)}">לא נקרא</span>`;
  const live = netLiveAddrs(n).map((a) => String(a).split("/")[0]);
  const gap = cfg && (cfg.mismatches || []).length ? ` <span title="${esc(cfg.mismatches.join(" · "))}">${UI.pill("err", "לא תואם להגדרה")}</span>` : "";
  return `<span title="${esc(`מוגדר: ${netConfiguredText(n)}`)}">${esc(live.join(" · ") || "—")}</span>${gap}`;
}
function netNicRow(n) {
  const enc = encodeId(n.name);   // ‏#1300: בלי שורה "נבחרת" צבועה — במוקאפ אין
  // ‏⋯ אחד לשורה (MENUS.nic): הגדרת כרטיס · תיאור · נתיבים סטטיים · כיבוי DHCP (כרטיס ההפצה) · שכחה (כרטיס שאינו במערכת)
  const acts = UI.rowMenu(n.name, [...(typeof nicsvcOpen === "function" ? [["שירותים…", `nicsvcOpen('${enc}')`]] : []), ["הגדרת כרטיס…", `netEditNic('${enc}')`], ["תיאור…", `netDescribe('${enc}')`], ["נתיבים סטטיים…", `netRoutesDrawer('${enc}')`],
    ...(n.enabled ? [["כיבוי DHCP…", `netDhcpOff('${enc}')`, "danger"]] : []),
    ...(n.present ? [] : [["שכחה…", `netForget('${enc}')`, "danger"]])]);
  // ‏#705: "שירותים" (nicsvc.js) — מה השרת נותן דרך הכרטיס; קליק על השורה פותח את מגירת השירותים
  const svc = typeof nicsvcCell === "function";
  return { attrs: `data-nic="${esc(n.name)}"${svc ? nicsvcRowAttrs(n) : ""}`,
    cells: [svc ? nicsvcNameCell(n) : `<b>${esc(n.name)}</b>`, n.description ? esc(n.description) : "—", `<span class="mono">${esc(n.mac || "—")}</span>`, netLinkText(n), netAddrText(n), netRolePill(n),
      ...(svc ? [nicsvcCell(n)] : []), acts] };
}
/* נתיבים סטטיים של כרטיס — מגירה מהתפריט שלו. ראיה חיובית מטבלת הניתוב (נקודה ירוקה), מחיקה דרך ה-sheet של netcfg.js. */
function netRoutesHtml(name) {
  if (!NETCFG) return UI.note("warn", `‏/net/config לא נקרא${NETW.err.config ? `: ${esc(NETW.err.config)}` : ""}`);
  const live = new Set((NETCFG.live && NETCFG.live.routes) || []), checked = netLiveChecked();
  const nic = (NETCFG.interfaces || []).find((x) => x.name === name);
  const rows = ((nic && nic.routes) || []).map((r, i) => {
    const seen = live.has(`${r.destination}/${maskBits(r.netmask)} via ${r.gateway}`);
    return [`${UI.status(!checked ? "unk" : seen ? "ok" : "err", "")}<span class="mono">${esc(r.destination)}/${maskBits(r.netmask)}</span>`, `<span class="mono">${esc(r.gateway)}</span>`,
      `<div class="acts on"><button class="btn sm danger" onclick="routeDeleteSheet('${encodeId(name)}',${i})">מחק</button></div>`];
  });
  return UI.datagrid({ columns: ["יעד", "דרך", ""], rows, cls: "stable", empty: "אין נתיבים סטטיים — נתיב שנוסף כאן נשאר גם אחרי אתחול" })
    + `<div class="cap" style="margin-top:8px">${checked ? "נקודה ירוקה = בטבלת הניתוב" : "המצב בפועל לא נקרא"}</div>`;
}
function netRoutesDrawer(nameEnc) {
  let name = nameEnc; try { name = decodeURIComponent(nameEnc); } catch (e) {}
  openDrawer(`נתיבים סטטיים — ${name}`, `<div class="page drw"><div>${netRoutesHtml(name)}</div><div class="acts"><button class="btn primary" onclick="closeDrawer();addRoute()">+ נתיב</button></div></div>`);
}
function networkNicsTab() {
  const nics = NETW.nics;
  const header = netHeader(1);
  let body;
  if (nics === null) body = `<div class="c12">${UI.note("err", `לא הצלחתי לקרוא את כרטיסי הרשת: ${esc(NETW.err.interfaces || "")}`)}</div>`;
  else {
    const svc = typeof nicsvcCell === "function" ? [{ html: `שירותים <span class="ns-help">· מה השרת נותן דרכו</span>` }] : [];
    const table = UI.datagrid({ columns: ["כרטיס", "תיאור", "MAC", "קישור", "כתובת", "תפקיד", ...svc, { html: `<span class="sr">פעולות</span>` }], rows: nics.map(netNicRow), cls: "kebab-col", opt: [2],
      empty: "לא נמצאו כרטיסי רשת — השרת לא רואה אף כרטיס, ואין הגדרה שמורה" });
    body = netUnreadNotes() + (svc.length ? nicsvcTableNotes() : "") + UI.card({ title: "כרטיסי רשת", acts: `<button class="btn primary sm" onclick="addNic()">${uiIcon("plus")} כרטיס</button>`, body: table, flush: nics.length > 0 })
      + (typeof vmnetCardHtml === "function" ? vmnetCardHtml() : "");
  }
  return `<div class="page aligned">${header}<div class="body">${body}</div></div>`;
}

/* --- לשונית 2: רשת הפצה (console-full.html P.network "dist", אחד-לאחד): "רשת ההפצה" (dl.kv) לצד "כרטיסים ברשת
   ההפצה", ומתחת "חכירות פעילות". "מי עוד עונה" (#53) — שורת kv עם "בדוק", באותה תבנית של "שם השרת · שינוי שם". --- */
/* "בעוד 9 ש'" / "בעוד 12 דק'" / תאריך — עד מתי החכירה; ‏null = אין תפוגה. */
function netUntil(iso) {
  const t = Date.parse(iso || "");
  if (!Number.isFinite(t)) return "—";
  const s = Math.round((t - Date.now()) / 1000);
  if (s <= 0) return "פגה";
  if (s < 3600) return `בעוד ${Math.max(1, Math.round(s / 60))} דק'`;
  if (s < 86400) return `בעוד ${Math.round(s / 3600)} ש'`;
  return fmtDate(new Date(t).toISOString());
}
function netLeaseName(l) {
  const m = findMachine(l.mac);
  if (m) return esc(machineName(m) || l.mac);
  return `${UI.pill("warn", "לא רשום")}${l.hostname ? ` ${esc(l.hostname)}` : ""} ${ghostBtn("רשום", `netRegister('${encodeId(l.mac)}')`)}`;
}
function netLeasesCard(focus) {
  const l = NETW.leases;
  let body;
  if (!l) body = UI.note("warn", `חכירות dnsmasq לא נקראו${NETW.err.leases ? `: ${esc(NETW.err.leases)}` : ""}`);
  else if (!l.checked) body = UI.noteTech("warn", "חכירות dnsmasq לא נקראו", l.reason || "");
  else body = UI.datagrid({ columns: ["כתובת", "MAC", "שם", "פוקעת"], opt: [3], cls: "stable",
    rows: l.leases.map((x) => ({ attrs: `data-mac="${esc(x.mac)}"`, cells: [`<bdi dir="ltr">${esc(x.ip)}</bdi>`, `<span class="mono">${esc(x.mac)}</span>`, netLeaseName(x), esc(netUntil(x.expires))] })),
    empty: `אין חכירות על ${focus.name} — כשמחשב יעלה ב-PXE הוא יופיע כאן` });
  return UI.card({ title: "חכירות פעילות", cls: "c12", body, flush: !!(l && l.checked && l.leases.length) });
}
/* ‏#1264: "כרטיסים ברשת ההפצה" — כל כרטיס, תפקידו, הקישור ומצבו. ב-bond המצב הוא
   מה ש-‏/proc/net/bonding אומר (‏NETCFG.deploy_bond); לא נקרא = "לא נבדק", לעולם לא
   "פעיל" (עיקרון 5). כרטיס יחיד — הקישור החי שלו (‏/net/interfaces). */
const BOND_MEMBER_HE = { active: ["ok", "פעיל"], down: ["err", "מנותק"], missing: ["err", "לא חבר ב-bond"], unchecked: ["warn", "לא נבדק"] };
const BOND_STATE_HE = { ok: ["ok", "פעיל — שני הכרטיסים"], degraded: ["warn", "פעיל על כרטיס אחד"], down: ["err", "שני הכרטיסים מנותקים"],
  missing: ["err", "לא תואם להגדרה"], unchecked: ["warn", "לא נבדק"] };
function netSpeedText(n) {
  const s = n && n.speed_mbps;
  return typeof s === "number" && s > 0 ? (s >= 1000 ? `${s / 1000} Gb` : `${s} Mb`) : "—";
}
/* ‏#1274: הכרטיסים הוחלפו על המכונה אבל ההשלמה (חומת אש/אתחול/רישום) נכשלה — ‏/net/deploy
   עדיין על השם הקודם: כרטיס 1 של ה-bond (בהוספה), או bond0 כשהרשת כבר על כרטיס אחד (בהסרה).
   מחזיר את ‏second לשליחה חוזרת ל-‏/second ("" = בלי כרטיס שני), או null = אין מה להשלים.
   ‏/net/deploy שלא נקרא = null: בלי ראיה אין כפתור (עיקרון 5). */
function netDeployPendingSecond(focus, bond) {
  const d = typeof NET_DEPLOY !== "undefined" ? NET_DEPLOY : null;
  if (!d || d.source !== "console") return null;
  if (bond) return bond.members[1] && bond.members.some((m) => m.name === d.interface) ? bond.members[1].name : null;
  return d.interface === "bond0" ? "" : null;
}
function netDeployCardsCard(focus) {
  const bond = NETCFG && NETCFG.deploy_bond && NETCFG.deploy_bond.name === focus.name ? NETCFG.deploy_bond : null;
  const pending = netDeployPendingSecond(focus, bond);
  const nicOf = (name) => (NETW.nics || []).find((n) => n.name === name) || null;
  const cells = (name, role, n, [cls, label]) => [`<b dir="ltr">${esc(name)}</b>`, `כרטיס ${role}`, `<span dir="ltr">${esc(netSpeedText(n))}</span>`, UI.pill(cls, label)];
  let rows, note, acts = "";
  if (bond) {
    rows = bond.members.map((m) => cells(m.name, m.role, nicOf(m.name), BOND_MEMBER_HE[m.state] || BOND_MEMBER_HE.unchecked));
    const [cls, label] = BOND_STATE_HE[bond.state] || BOND_STATE_HE.unchecked;
    acts = UI.pill(cls, label);
    note = bond.checked
      ? UI.note("info", `שני הכרטיסים עובדים יחד על הכתובת <span class="mono">${esc(focus.server_ip || "")}</span> (${esc(bond.name)}, ‏balance-alb) — תעבורה למחשבים שונים מתחלקת ביניהם, אבל סבב שיכפול יוצא בכרטיס אחד בכל רגע. אם כרטיס מתנתק השני ממשיך: סבב שרץ נעצר לשניות ספורות ומסתיים כרגיל. לא נדרשת הגדרה בסוויץ'.`)
      : UI.note("warn", `מצב ${esc(bond.name)} לא נקרא (${esc(bond.reason || "")}) — לא ידוע אם שני הכרטיסים פעילים`);
  } else {
    const st = focus.state === "up" ? ["ok", "פעיל"] : focus.state === "down" ? ["err", "מנותק"] : ["warn", "לא נקרא"];
    rows = [cells(focus.name, 1, focus, st)];
    note = UI.note("info", "כרטיס אחד. אפשר להוסיף כרטיס שני ב\"עריכה…\" — שני הכרטיסים יעבדו יחד על אותה כתובת, ואם אחד ייפול השני ימשיך.");
  }
  if (pending !== null) {
    acts += `<button class="btn primary sm" onclick="completeDeploySecond('${encodeId(focus.name)}','${encodeId(pending)}')">השלם הגדרה</button>`;
    note = UI.note("warn", `ההגדרה לא הושלמה: הכרטיסים הוחלפו על המכונה, אבל השלמת רשת ההפצה (חומת אש, אתחול השרת) נכשלה — השרת עדיין רשום על ${esc(NET_DEPLOY.interface)}. "השלם הגדרה" מנסה שוב בלי לגעת בכרטיסים.`) + note;
  }
  return UI.card({ title: "כרטיסים ברשת ההפצה", cls: "c6", acts, flush: true,
    body: UI.datagrid({ columns: ["כרטיס", "תפקיד", "קישור", "מצב"], rows, cls: "stable" }) + note });
}
/* ‏"12h" של dnsmasq → "12 שעות" (כמו המוקאפ); ערך אחר — כמו שהוא. */
function netLeaseText(v) {
  const m = /^(\d+)([smhd])$/.exec(String(v || "").trim());
  if (!m) return v || "—";
  const n = Number(m[1]), u = { s: ["שנייה", "שניות"], m: ["דקה", "דקות"], h: ["שעה", "שעות"], d: ["יום", "ימים"] }[m[2]];
  return n === 1 ? u[0] : `${n} ${u[1]}`;
}
/* ‏#1263: כרטיס הניהול אינו יכול להיות רשת ההפצה. */
function deployCandidates() { return (NETW.nics || []).filter((n) => n.present && !n.trunk && !isMgmtNic(n.name)); }
/* ‏#1556: ברירת המחדל אינה גם הכרטיס שהקונסולה מחוברת דרכו (בשרת חיפה כרטיס
   הניהול לא היה ידוע, והראשון — eth0 של הקונסולה — נבחר). כששניהם לא ידועים
   אין ברירת מחדל: מי שלא יודע דרך מה הוא מחובר חייב להניח שזה כל כרטיס. */
function deployDefaultNic(candidates) {
  const via = NETCFG && NETCFG.console_interface;
  if (!via && !(NETCFG && NETCFG.management_interface)) return null;
  return candidates.find((n) => n.name !== via) || null;
}
function netPickDeploy() {
  const via = NETCFG && NETCFG.console_interface;
  sheet({ title: "הגדר כרשת הפצה", sub: "על איזה כרטיס ירוץ ה-DHCP למחשבי השיכפול והבנייה", submitLabel: "המשך",
    fields: [{ id: "nic", label: "כרטיס", type: "select", value: "",
      options: [{ value: "", label: "— בחרו כרטיס —" },
        ...deployCandidates().map((n) => ({ value: n.name, label: n.name + (n.name === via ? " — הקונסולה מחוברת דרכו" : "") }))] }],
    onSubmit: async (v) => {
      if (!v.nic) throw new Error("בחרו כרטיס");
      return () => editDeployNet(v.nic);
    } });
}
function networkDeployTab() {
  const nics = NETW.nics, focus = netDeployNic();
  const candidates = deployCandidates(), candidate = deployDefaultNic(candidates);
  const header = netHeader(2);
  let body;
  if (nics === null) body = `<div class="c12">${UI.note("err", `לא הצלחתי לקרוא את כרטיסי הרשת: ${esc(NETW.err.interfaces || "")}`)}</div>`;
  else if (!focus) {
    const act = candidate ? `<button class="btn primary" onclick="netEditDeploy('${encodeId(candidate.name)}')">הגדר כרשת הפצה</button>`
      : candidates.length ? `<button class="btn primary" onclick="netPickDeploy()">הגדר כרשת הפצה</button>` : "";
    body = netUnreadNotes() + UI.card({ title: "רשת ההפצה", cls: "c12",
      body: UI.empty("אין כרטיס מוגדר כרשת הפצה — \"הגדר כרשת הפצה\" על אחד הכרטיסים. DHCP הוא ההגדרה המסוכנת ביותר במערכת (#53): השרת בודק לפני ההדלקה מי כבר עונה.", act) });
  } else {
    const enc = encodeId(focus.name), live = focus.dhcp_live || {}, pr = netProbeStatus(focus.name);
    const dhcp = !live.checked ? UI.pill("unk", focus.enabled ? "מופעל — המצב הפעיל לא נקרא" : "לא נקרא")
      : focus.dhcp_diverged ? UI.pill("warn", `${focus.enabled ? "מופעל" : "כבוי"} — לא תואם למצב הפעיל`)
      : live.state === "serving" ? UI.pill("ok", "פעיל — מחלק כתובות") : UI.pill(dhcpLiveClass(live.state) === "warn" ? "warn" : "", focus.dhcp_live_label || "—");
    const net = focus.server_ip && focus.netmask ? `${networkOf(focus.server_ip, focus.netmask) || "—"} / ${focus.netmask}` : "—";
    const kv = settingsKv([
      ["כתובת רשת", `<span class="mono">${esc(net)}</span>`],
      ["כתובת השרת", `<span class="mono">${esc(focus.server_ip || "—")}</span> <small class="muted">(אוטומטית, הראשונה ברשת)</small>`],
      ["DHCP", `<span title="${esc(String(netDhcpCell(focus)).replace(/<[^>]*>/g, " "))}">${dhcp}</span>`],
      ["טווח", `<span class="mono">${esc(focus.range_start && focus.range_end ? `${focus.range_start} – ${focus.range_end}` : "—")}</span>`],
      ["חכירה", esc(netLeaseText(focus.lease))],
      ["מי עוד עונה", `${UI.status(pr.cls, pr.text)} ${ghostBtn("בדוק", `netProbe('${enc}')`)}`],
    ]);
    body = netUnreadNotes()
      + UI.card({ title: "רשת ההפצה", cls: "c6", body: kv,
        acts: `<button class="btn sm" onclick="netEditDeploy('${enc}')">עריכה…</button><button class="btn sm danger" onclick="netDhcpOff('${enc}')">כיבוי DHCP…</button>` })
      + netDeployCardsCard(focus)
      + netLeasesCard(focus);
  }
  return `<div class="page aligned">${header}<div class="body">${body}</div></div>`;
}

function networkPage(tab = 0) {
  if (NETW.nics === null && !Object.keys(NETW.err).length) return pagePlaceholder();
  if (tab === 1) return networkNicsTab();
  if (tab === 2) return networkDeployTab();
  return networkDiagramTab();
}

/* ניווט: מהעץ (הצומת "רשת" = הדף; הילדים = לשונית; כרטיס בעץ = לשונית 1 עם הכרטיס נבחר), ומדף הפורטים. */
function openNetwork(tab = 0, nicEnc = null, el = null) {
  if (nicEnc) { let name = nicEnc; try { name = decodeURIComponent(nicEnc); } catch (e) {} NETW.sel = name; }
  if (!pageAllowed("network")) return;
  const t = Number(tab) || 0;
  if (current !== "network") selectPage(el || document.querySelector('.inventory-node[data-page="network"]'), "network");
  else if (el) markTreeSelection(el, "network");
  if (t !== currentTab) activateTab(t); else renderCurrent();
  closeSidebar();   // ‏#1300: גם כשכבר בדף הרשת — בחירת לשונית/כרטיס מהעץ נכנסת אליו
}
function populateSidebarNics() {
  const treeId = sideId("nicTree");
  const tree = document.getElementById(treeId);
  if (!tree) return;
  const nics = NETW.nics || [];
  const parent = document.getElementById(sideId("nicNode")) || tree.previousElementSibling;
  const arrow = document.getElementById(sideId("nicTreeArrow")) || (parent && parent.querySelector(".tree-arrow, .tree-arrow-sp"));
  if (!nics.length) {
    tree.innerHTML = ""; tree.setAttribute("hidden", "");
    if (arrow) { arrow.className = "tree-arrow-sp"; arrow.textContent = ""; arrow.onclick = null; }
    return;
  }
  if (arrow) {
    const open = !tree.hasAttribute("hidden");
    arrow.className = "tree-arrow"; arrow.dataset.open = String(open); arrow.textContent = open ? "▾" : "▸";
    arrow.onclick = (event) => { event.stopPropagation(); toggleInventoryGroup(parent, treeId); };
  }
  tree.innerHTML = nics.map((n) => {
    const on = (current === "network" && currentTab === 1 && NETW.sel === n.name) ? " active" : "";
    return `<div class="inventory-node${on}" role="treeitem" tabindex="0" onclick="${sideClick(`openNetwork(1,'${encodeId(n.name)}',this)`)}"><span class="tree-arrow-sp"></span><span>${uiIcon("network")}</span><span dir="ltr">${esc(n.name)}</span></div>`;
  }).join("");
}
/* net.js (אחרי שמירת DHCP) ו-netcfg.js (אחרי כתובת/נתיב/אישור החזרה) קוראים לזה. */
function refreshNetPages() {
  populateSidebarNics();
  if (current !== "network") return;
  if (refreshNetPages._busy) return;
  refreshNetPages._busy = true;
  try { loadNetwork().catch((e) => toast("רענון הרשת נכשל: " + e.message)); }
  finally { refreshNetPages._busy = false; }
}
/* אחרי כל ציור של דף הרשת: הספירה לאחור של ההחזרה (netcfg.js) — היא חיה ב-DOM. */
function wireNetPage() {
  if (current !== "network") return;
  if (typeof startCountdown === "function") startCountdown();
}

function healthCheckById(id) {
  return (Array.isArray(HEALTH) ? HEALTH : []).find((c) => c.id === id) || null;
}

/* ---------- #1267: רשת › פורטים — כרטיס לכל רשת ----------
   מוקאפ: docs/design/apple-mockups/console-full.html (P.network ports, W.portDrawer);
   docs/interfaces.md §16. הכול מ-/ports: השורות, `network` (לאיזו רשת שייכת השורה)
   ו-`firewall`/`firewall_detail` (טבלת inet imagectl של השרת — רק לפורטים שבין רשתות).
   נדב 10/10: ברשת ההפצה השרת מנהל את הפורטים — אין עמודת חומת אש ואין פעולות;
   בניהול ובין שרתים — חומת אש (פתוח=ירוק, חסום=אדום, לא נבדק=אפור) ו"מה לפתוח
   בחומת האש של המכללה" מה-note. אין "פתח/סגור פורט" בשום מקום — רק "בדוק עכשיו"
   (קריאה חוזרת של /ports) ו"הצג פרטים" (מגירה). */
let PORTS_NICS = null, portsNicsError = "";
let PORTS_AT = "";
const PORT_OFF_MEANS = {
  dhcp: "אף מכונה בוילן ההפצה לא מקבלת כתובת — אין PXE, אין סבב",
  pxe_proxy: "תחנות בוילן שה-DHCP בו אינו שלנו לא רואות את התפריט — עולות מהדיסק",
  monitor: "תופס באתחול הבא של כל תחנה — מכונה שעולה לא מפעילה שירות צפייה",
};
const PORT_API_NEEDED = "דורש API (#996)";
const offMeansNote = (text) => `<div class="sheet-note danger">מה קורה אם מכבים: ${esc(text)}</div>`;

function nicBody(n, over) {
  return { enabled: n.enabled, proxy: n.proxy, trunk: n.trunk, range_start: n.range_start, range_end: n.range_end,
    netmask: n.netmask, gateway: n.gateway, dns: n.dns, lease: n.lease, server_ip: n.server_ip, confirm: n.name, ...over };
}

// שם כרטיס/כתובת בודדת (מחרוזת) — mono LTR; טקסט עברי ("בתחנה, לא בשרת") —
// רגיל. רשימת bind מהשרת (#1015, תמיד מערך) — כל הכתובות, mono LTR; ריק = "—".
/* רשימת כתובות ההאזנה (#1015 `bind`): שורה לכתובת, IPv4 קודם, link-local של IPv6
   מקופל ל-"+N" עם tooltip — אחרת שורת TFTP (8 כתובות) מתחה את הטבלה מעבר למסך
   והמתגים נעלמו מימין (נדב 17/09). */
function portNicHtml(nic) {
  if (nic === null) return UI.status("unk", "לא נקרא");
  if (Array.isArray(nic)) {
    if (!nic.length) return `<span class="muted">—</span>`;
    const main = nic.filter((a) => !a.startsWith("[fe80") && !a.startsWith("[::1]"));
    const rest = nic.filter((a) => !main.includes(a));
    const shown = (main.length ? main : nic).slice(0, 4);
    const hidden = nic.filter((a) => !shown.includes(a));
    const more = hidden.length
      ? ` <span class="muted" title="${esc(hidden.join(" · "))}">+${hidden.length}</span>` : "";
    return `<span class="mono bindlist" dir="ltr">${shown.map((a) => esc(a)).join("<br>")}</span>${more}`;
  }
  if (!nic) return `<span class="muted" title="${PORT_API_NEEDED}: כתובת ההאזנה">—</span>`;
  return /^[ -~]+$/.test(nic) ? `<span class="mono">${esc(nic)}</span>` : esc(nic);
}

/* הרשתות לפי הסדר במוקאפ; שורה בלי `network` מוכר נופלת לכרטיס "ללא שיוך" — לא נעלמת. */
const PORT_NETWORKS = [
  ["deploy", "רשת הפצה", "השרת מנהל את רשת ההפצה ואת הפורטים שלה — אין בה חומת אש ואין מה לפתוח."],
  ["mgmt", "רשת ניהול", "חומת האש של המכללה לא נראית מהשרת — העמודה האחרונה אומרת מה לפתוח בה."],
  ["interserver", "בין שרתים", "חומת האש של המכללה לא נראית מהשרת — העמודה האחרונה אומרת מה לפתוח בה."],
  ["per_nic", "לפי כרטיס רשת", "השירות מופעל בנפרד בכל כרטיס דרך רשת ← חיבורים פיזיים ← שירותים."],
];
const PORT_FW = { open: ["ok", "פתוח"], blocked: ["err", "חסום"] };   // כל ערך אחר (null) = "לא נבדק", אפור (עיקרון 5)
const portCross = (p) => p.network === "mgmt" || p.network === "interserver" || p.network === "per_nic";
const portHidden = (p) => p.id === "firewall";
const portFwLabel = (p) => (PORT_FW[p.firewall] || ["", "לא נבדק"])[1];
const portFwHtml = (p) => `<span title="${esc(p.firewall_detail || "")}">${UI.pill((PORT_FW[p.firewall] || [""])[0], portFwLabel(p))}</span>`;
const portOpenNote = (p) => String(p.note || "").replace(/^לפתוח ב-FW:\s*/, "") || "—";
const portStateHtml = (p) => `${UI.status(healthStatusClass(p.state), healthStatusLabel(p.state))}<span class="sub">${esc(p.detail || "")}</span>`;
/* ‏#1300 (console-full.html P.network "ports"): עמודת "מאזין" = פיל אחד — מאזין / לא מאזין / כבוי / בזמן סבב / לא נבדק;
   מה שהשרת מדד (detail) — ב-title. */
const PORT_LISTEN_PILL = { ok: ["ok", "מאזין"], err: ["err", "לא מאזין"], warn: ["warn", "דורש בדיקה"], "": ["", "כבוי"], unk: ["", "לא נבדק"] };
function portListenPill(p) {
  const [cls, text] = p.id === "multicast" && p.state !== "bad" ? ["info", "בזמן סבב"] : PORT_LISTEN_PILL[healthStatusClass(p.state)] || ["", healthStatusLabel(p.state)];
  return `<span title="${esc(p.detail || "")}">${UI.pill(cls, text)}</span>`;
}
const portIdOf = (idEnc) => { try { return decodeURIComponent(idEnc); } catch (e) { return idEnc; } };

/* כותרת הכרטיס: הפצה = הכרטיסים שמשרתים DHCP/proxy (מ-/ports) + הכתובת שלהם (מ-/net/interfaces,
   אם נקרא); ניהול = הכתובות ש-8081 מאזין עליהן בפועל (bind), בלי loopback ו-link-local. */
function portCardTitle(net, label, rows) {
  if (net === "deploy") {
    const dhcp = (PORTS || []).find((p) => p.id === "dhcp");
    const names = ((dhcp && dhcp.interfaces) || []).filter((i) => i.enabled || i.proxy).map((i) => i.name);
    return [label, ...names.map((n) => {
      const nic = (PORTS_NICS || []).find((x) => x.name === n), ip = nic && (nic.addresses || [])[0];
      return ip ? `${n} · ${String(ip).split("/")[0]}` : n;
    })].join(" · ");
  }
  if (net === "mgmt") {
    const c = rows.find((p) => p.id === "http_console");
    const addrs = (c && Array.isArray(c.bind) ? c.bind : [])
      .filter((a) => !/^(127\.|\[::1\]|\[fe80)/.test(a)).map((a) => a.replace(/:\d+$/, ""));
    return [label, ...addrs].join(" · ");
  }
  return label;
}

function portCells(p) {
  const cells = [`<span class="mono">${esc(p.port)}/${esc(p.proto)}</span>`, esc(p.desc ? `${p.name} — ${p.desc}` : p.name), esc(p.target || "—"), portListenPill(p)];
  if (!portCross(p)) return cells;
  const k = encodeId(p.id);
  return cells.concat([portFwHtml(p), `<span class="wrap">${esc(portOpenNote(p))}</span>`,
    UI.rowMenu(`${p.name} ${p.port}`, [["בדוק עכשיו", `portCheckNow('${k}')`], ["הצג פרטים", `portDetail('${k}')`]])]);
}

function portCard(net, label, explain, rows) {
  const cross = net === "mgmt" || net === "interserver" || net === "per_nic";
  const columns = ["פורט", "שירות", "מי פונה", "מאזין"].concat(cross ? ["חומת אש", "מה לפתוח בחומת האש של המכללה", { html: `<span class="sr">פעולות</span>` }] : []);
  const grid = UI.datagrid({ columns, rows: rows.map(portCells), cls: cross ? "kebab-col" : "", opt: cross ? [5] : [] });
  const intro = explain ? `<p class="port-intro">${esc(explain)}</p>` : "";
  return UI.card({ title: portCardTitle(net, label, rows), body: intro + grid, flush: true });
}

/* "הצג פרטים" — מגירה (W.portDrawer): מה נמדד בשרת, ומה לפתוח במכללה. */
function portDetail(idEnc) {
  const id = portIdOf(idEnc);
  const p = (PORTS || []).find((x) => x.id === id);
  if (!p) { toast("הפורט לא מופיע ברשימה שנקראה"); return; }
  const measured = UI.kv([["מאזין", portStateHtml(p)], ["כתובות האזנה", portNicHtml(p.bind)],
    ["חומת אש בשרת", `${portFwHtml(p)} ${esc(p.firewall_detail || "")}`], ["נבדק", esc(PORTS_AT || "—")]]);
  const college = UI.kv([["מה לפתוח", esc(portOpenNote(p))]])
    + UI.note("info", "חומת האש של המכללה לא נראית מהשרת. &quot;בדוק עכשיו&quot; בודק רק את מה שבשרת עצמו.");
  openDrawer(`${p.port}/${p.proto} · ${p.name}`, `<div class="page drw"><div class="obj-sub">${esc(p.desc || "")} · ${esc(p.target || "")}</div>`
    + `<div><div class="sec">מצב שנמדד</div>${measured}<div class="sec">חומת האש של המכללה</div>${college}</div>`
    + `<div style="border-top:1px solid var(--hair);padding-top:12px;display:flex;gap:6px"><button class="btn sm primary" onclick="portCheckNow('${encodeId(p.id)}', true)">בדוק עכשיו</button></div></div>`);
}

/* "בדוק עכשיו" — קריאה חוזרת של /ports (ss + nft בשרת, קריאה בלבד) ודיווח מה נמדד. כשל = "הבדיקה נכשלה", לא "תקין". */
async function portCheckNow(idEnc, reopen = false) {
  const id = portIdOf(idEnc);
  await loadPorts();
  if (!PORTS) { toast(`הבדיקה נכשלה: ${portsError}`); return; }
  const p = PORTS.find((x) => x.id === id);
  if (!p) { toast("הפורט לא מופיע ברשימה שנקראה עכשיו"); return; }
  toast(`${p.port}/${p.proto}: ${healthStatusLabel(p.state)} — ${p.detail || ""} · חומת אש בשרת: ${portFwLabel(p)}`);
  if (reopen) portDetail(id);
}

function ports() {
  if (!PORTS && !portsError) return pagePlaceholder();
  const rows = PORTS ? PORTS.filter((p) => !portHidden(p)) : [];
  // ‏#1300 (console-full.html P.network): אותה כותרת "רשת" כמו שלוש הלשוניות האחרות (‏#954 גל 8: לשונית של אובייקט הרשת).
  const header = netHeader(3);
  if (!PORTS) return `<div class="page aligned">${header}<div class="body">${UI.note("err", `לא הצלחתי לקרוא את רשימת הפורטים: ${esc(portsError)}`)}</div></div>`;
  // ‏#1300 (console-full.html P.network "ports", אחד-לאחד): רק הכרטיסים. חומת האש של השרת — הודעה רק כשהיא לא תקינה
  // או לא נקראה (המצב לכל פורט — בעמודת "חומת אש").
  const fw = PORTS.find((p) => p.id === "firewall");
  const fwNote = !fw ? `<div class="c12">${UI.note("warn", "חומת האש של השרת: לא נקרא")}</div>`
    : healthStatusClass(fw.state) === "ok" ? "" : `<div class="c12">${UI.note(healthStatusClass(fw.state) === "err" ? "err" : "warn", esc(`חומת האש של השרת: ${fw.detail}`))}</div>`;
  const known = PORT_NETWORKS.map(([net]) => net);
  const cards = PORT_NETWORKS.map(([net, label, explain]) => [net, label, explain, rows.filter((p) => p.network === net)])
    .filter(([, , , list]) => list.length).map((c) => portCard(...c)).join("");
  const other = rows.filter((p) => !known.includes(p.network));
  const otherCard = other.length ? portCard("", "ללא שיוך לרשת", "השרת לא אמר לאיזו רשת שייכות השורות האלה.", other) : "";
  return `<div class="page aligned">${header}<div class="body">${fwNote}${cards}${otherCard}</div></div>`;
}

/* ---------- #954 גל 6: מוניטור — הרשימה ----------
   נבנה לפי docs/design/console-redesign/monitor.md: טבלה אחת (בלי לשוניות),
   מתג "מוניטור לתחנות" (#827) בכותרת, "מחובר" כפי שהשרת קבע (‏/monitor/machines,
   חלון 90ש'), "נראה" מ-/net (loadMachines), "מה על המסך" מ-prompt.
   זה המקום היחיד לכפתור המוניטור (נדב 17/09); monitor.html/.js — לא נגענו. */
/* ‏console-full.html P.monitor: רשימה + מציג בכרטיס בתוך הדף. המציג הוא monitor.html ב-iframe (אותו לקוח RFB, בלי שכתוב);
   "פתח בחלון נפרד" נשאר קישור שרת משנה. בלי מכונה נבחרת / מתג כבוי / מכונה לא מחוברת — הסבר, לא iframe ריק. */
let MON_SEL = "";
function monitorSelect(macEnc) {
  try { MON_SEL = decodeURIComponent(macEnc); } catch (e) { MON_SEL = macEnc; }
  renderCurrent();
}
function monitorViewerHtml(list, enabled) {
  const m = list.find((x) => monitorKey(x) === MON_SEL);
  // ‏#1300 (console-full.html P.monitor): מצב ריק עם סמל, כותרת ושורת הסבר — לא iframe ריק.
  const say = (t) => `<div class="empty mon-empty"><span class="eic">${uiIcon("t-monitor")}</span><b class="et">${esc(t[0])}</b>${esc(t[1])}</div>`;
  let body;
  if (!enabled) body = say(["המוניטור כבוי", "הדלק את המתג בכותרת כדי לצפות במסך של תחנה. הדלקה חושפת 5900 על וילן ההפצה ותופסת באתחול הבא של כל מכונה."]);
  else if (!m) body = say(["בחר מחשב לצפייה", "המסך של התחנה יוצג כאן. אפשר לשלוח מקשים בלבד כשהתחנה מחוברת לסוכן."]);
  else if (!m.online) body = say([`${m.name || m.mac} אינה מחוברת`, "אין למה להתחבר: התחנה לא נראתה ב-90 השניות האחרונות. אפשר להעיר אותה (WoL) מהרשימה."]);
  else {
    // ‏#1179/#1008: מכונה של שרת משנה — המציג עובר דרך שרת המשנה (#1129, ‏monitor.js ‏node=).
    const node = m.node || SERVER_CTX;
    const q = `monitor.html?mac=${encodeId(m.mac)}&name=${encodeId(m.name || m.mac)}${node ? `&node=${encodeId(node)}` : ""}`;
    body = `<div class="mon-bar"><strong>${esc(m.name || m.mac)}</strong><span class="mono">${esc(m.ip || "")}</span><span class="sp"></span>`
      + `<a class="btn sm" href="${q}" target="imagectl-monitor-${esc(m.mac)}" rel="noopener" onclick="monitorPopOut()">פתח בחלון נפרד</a></div>`
      + `<iframe class="mon-frame" title="מסך ${esc(m.name || m.mac)}" src="${q}"></iframe>`;
  }
  return UI.card({ title: "צפייה במסך", small: m ? m.name || m.mac : "", cls: "c6", flush: !!m && m.online && enabled, body });
}
/* ‏#1852 (הכרעת נדב): "פתח בחלון נפרד" **מעביר** את הצפייה — ה-iframe יוצא מהדף לפני שהחלון
   מתחבר (השרת גם מעביר בעצמו: אותו מנהל במקום אחר = 4410 לישן, בלי ניסיון חוזר). */
function monitorPopOut() {
  const frame = document.querySelector("iframe.mon-frame");   // מציג אחד בדף
  if (!frame) return;
  const note = document.createElement("div");
  note.className = "empty mon-empty";
  note.textContent = "המסך פתוח בחלון נפרד";
  frame.replaceWith(note);
}
/* ‏#1300 (console-full.html P.monitor): מחשב מחובר = כפתור אחד — שם ו-IP; לחיצה = צפייה. "מה על המסך"
   (prompt) — שורה קטנה בתוך הכפתור. */
function monitorRowHtml(m) {
  const macEnc = encodeId(monitorKey(m)), on = monitorKey(m) === MON_SEL;
  const screen = m.prompt ? `<span class="sub">${UI.status("warn", waitingText(m))}</span>` : "";
  return `<button type="button" class="btn mon-pick${on ? " on" : ""}" data-mac="${esc(m.mac)}"${m.node ? ` data-node="${esc(m.node)}"` : ""} onclick="monitorSelect('${macEnc}')" aria-pressed="${on}">`
    + `<span class="mn"><b>${esc(m.name || m.mac)}</b>${m.server ? ` <span class="muted" data-server>· ${esc(m.server)}</span>` : ""}${screen}</span>`
    + `<span class="mono">${esc(m.ip || "")}</span></button>`;
}
/* מכונה שאינה מחוברת — לא במוקאפ, אבל לא נעלמת: שורה שקטה עם מתי נראתה ו-WoL (#984). */
function monitorOffRowHtml(m) {
  const macEnc = encodeId(monitorKey(m)), net = m.node ? null : netFor(m.mac);
  let seen;
  if (m.node) seen = "לא מחובר";   // "נראה" של שרת המשנה אינו נקרא כאן
  else if (NET == null) seen = "לא מחובר · נראה: לא נקרא";
  else if (net && net.last_seen) seen = `לא מחובר · נראה ${ago(net.last_seen)}`;
  else seen = "מעולם לא נראה";
  // WoL של מכונת שרת משנה אינו עובר ב-relay — רק מקומית.
  const wol = m.node ? "" : `<button class="btn sm" onclick="wakeMachine('${macEnc}')">WoL</button>`;
  return `<div class="mon-off" data-mac="${esc(m.mac)}"${m.node ? ` data-node="${esc(m.node)}"` : ""}><span class="mn">${esc(m.name || m.mac)}${m.server ? ` <span data-server>· ${esc(m.server)}</span>` : ""}</span>${UI.status("", seen)}${wol}</div>`;
}
/* שרת משנה שלא נקרא — שורה משלו ברשימה, לא היעלמות של המכונות שלו (עיקרון 5). */
function monitorUnreadRow(r) {
  return `<div class="mon-off" data-node="${esc(r.node || "")}" data-unread><span class="mn">${esc(r.label)} <span class="muted">· שרת משנה</span></span>${UI.status("unk", "לא נקרא")}<span class="sub">${esc(r.error || "בלי סיבה בתשובה")}</span></div>`;
}
function monitorPage() {
  if (!MONITOR && !monitorError) return pagePlaceholder();
  const list = MONITOR ? monitorList() : [];
  const enabled = MONITOR && MONITOR.settings ? MONITOR.settings.enabled === true : null;
  const online = list.filter((m) => m.online), offline = list.filter((m) => !m.online);
  const sub = MONITOR
    ? ["צפייה בתחנות מרחוק", `${online.length} מתוך ${list.length} מחוברות`].map(esc).join(" · ")
    : `‏/monitor לא נקרא: ${esc(monitorError)}`;
  // ‏#1300: המתג לבד (בלי פיל מצב בכותרת ובלי הערה למעלה) — המשמעות ב-title שלו ובכרטיס הצפייה.
  const sw = !MONITOR ? "" : `<span class="swrow" title="כבוי כברירת מחדל — הדלקה חושפת 5900 על וילן ההפצה ותופסת באתחול הבא של כל מכונה"><button type="button" class="sw${enabled ? " on" : ""}" role="switch" aria-checked="${enabled ? "true" : "false"}" aria-label="מוניטור לתחנות" onclick="monitorToggle(${enabled ? "false" : "true"})"></button><span class="cap">מוניטור לתחנות</span></span>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "מוניטור" }],
    icon: "t-monitor", name: "מוניטור", sub, pill: MONITOR ? "" : UI.pill("err", "לא נקרא"), actions: sw + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`, compact: true });
  let body;
  if (!MONITOR) body = UI.note("err", `לא הצלחתי לקרוא את רשימת המוניטור: ${esc(monitorError)}`);
  else {
    const unread = (MONITOR.remote || []).filter((r) => !r.connected);
    const picks = online.length ? `<div class="mon-list">${online.map(monitorRowHtml).join("")}</div>`
      : `<div class="muted">${list.length ? "אף מחשב לא מחובר עכשיו." : "אין מחשבי בנייה או שיכפול רשומים — הוסיפו אותם בדף המחשבים"}</div>`;
    const rest = offline.length || unread.length
      ? `<div class="mon-rest"><div class="grp">לא מחוברים · ${offline.length}</div>${offline.map(monitorOffRowHtml).join("")}${unread.map(monitorUnreadRow).join("")}</div>` : "";
    body = UI.card({ title: "מחשבים מחוברים", small: "נראו ב-90 השניות האחרונות", cls: "c6", body: picks + rest }) + monitorViewerHtml(list, enabled);
  }
  return `<div class="page aligned">${header}<div class="body">${body}</div></div>`;
}

/* ---------- #954 גל 6: הגדרות ----------
   סידור מחדש (נדב 17/10, המוקאפ C:\ImageCtl-Lab\evening-1015\settings-redesign, apple-design §16): חמש קבוצות
   לפי נושא — כללי · שיכפול והפצה · אבטחה · עדכונים ותחזוקה. כל הגדרה שורה אחת של `dl.mkv`: התווית בתחילת
   השורה, השליטה (ערך + "שינוי…", מתג או כפתור) בסופה, באותו טור בכל השורות. ההסבר — הערת קבוצה גלויה מתחת
   לכרטיס, לא רק title בריחוף. **כל שינוי נשמר מיד** (ערך מחלון קטן, מתג בלחיצה) ונרשם ביומן; מתג שכיבויו
   מסוכן פותח קודם חלון אישור עם כפתור אדום, בלי הקלדת מילה (#1270). אין "שמירה"/"ביטול" בכותרת.
   שם השרת = שם המארח (שם אחד, ‏POST /hostname); "מראה" עבר ל"החשבון שלי". */
let SETTINGS = null, settingsError = "";
const SETTING_GROUPS = [
  { card: "general", title: "כללי", foot: "השם מופיע בעץ, בכותרת, במסכי התחנות וברשת (hostname). שינוי השם מפעיל את השרת מחדש." },
  { card: "deploy", title: "שיכפול והפצה", foot: "סבב מתחיל לבד כשעבר הזמן הזה בלי מצטרף חדש. קצב אוטומטי כבוי = תמיד התקרה הקבועה; השינוי חל מהגל הבא. יעד שיישאר עם פחות מקום יוצג כ\"לא נכנס\"." },
  { card: "security", title: "אבטחה", foot: "מי שעומד ליד מחשב ומשחזר אליו אימג' מהשרת, כשאין סבב פתוח, מתבקש להיכנס עם משתמש וסיסמה של הקונסולה; בסבב פתוח לא מתבקשת כניסה. השרת מקבל פנייה ממחשב רק אם כתובת ה-IP שלו היא זו שהשרת נתן ל-MAC שלו — כך מחשב אחד לא יכול להתחזות לאחר. כיבוי של כל אחד מהמתגים נרשם ביומן." },
  { card: "update", title: "עדכונים ותחזוקה", foot: "החיבור היוצא לריפו הציבורי נפתח רק בזמן בדיקה או עדכון. ההפעלה מחדש נדחית כשיש עבודה פעילה. קובץ הגיבוי מכיל סודות — לשמור אותו כמו סיסמה." },
];
/* שורה: key = מפתח /settings, או שורה מיוחדת (type: name/tls/version/lastcheck/reboot/backup) שהערך שלה בא ממקור אחר.
   ‏more — ההסבר המלא (title). ‏confirmOff — מתג שכיבויו מסוכן: חלון אישור לפני השמירה. */
const SETTING_ROWS = [
  { key: "server_name", card: "general", label: "שם השרת", type: "name", more: "שם אחד: בעץ, בכותרת, במסכי התחנות וברשת (hostname)" },
  // ‏#1447 (נדב 17/10): לא ב-/settings — ‏GET/POST /timezone (‏timedatectl וההגדרה יחד); ‏timezoneRow.
  { key: "timezone", card: "general", label: "אזור זמן", type: "timezone", action: "שינוי", more: "השעה המקומית במסך השרת, במסכי התחנות ובהפעלה מחדש האוטומטית. השרת שומר זמנים ב-UTC — רק התצוגה מקומית" },
  { key: "ntp_source", card: "general", label: "מקור השעה", type: "ntpsource" },
  { key: "ntp_status", card: "general", label: "מצב השעון", type: "ntpstatus" },
  { key: "session_wait_seconds", card: "deploy", label: "המתנה מהמצטרף האחרון", type: "number", min: 30, more: "סבב מתחיל לבד כשעברו כך וכך שניות בלי מצטרף חדש" },
  // ‏#1303 (נדב 12/10): הקצב הנלמד של המולטיקאסט (‏drive_rates); ‏"false" בלבד מכבה — חסר = אוטומטי.
  { key: "learned_rate_enabled", card: "deploy", label: "קצב שידור אוטומטי לפי הכוננים בחדר", more: "כבוי = תמיד התקרה הקבועה (IMAGECTL_MAX_BITRATE). השינוי חל מהגל הבא", type: "switch", defaultOn: true },
  // ‏#1354 (נדב 13/10): יעד שיישארו בו פחות מזה במחיצת המערכת — "לא נכנס". הסוכן מקבל ב-hello.
  { key: "min_free_gb", card: "deploy", label: "מקום פנוי מינימלי אחרי שחזור", type: "number", min: 0, max: 500, unit: "GB", more: "יעד שיישאר עם פחות מזה יוצג כ'לא נכנס'" },
  // ‏#1366 (נדב 13/10): מגירה שמונה ה-CRC שלה מעל זה — כתומה: "כבל או מגירה חשודים". 0 = כבוי, ברירת המחדל. הסוכן מקבל ב-hello.
  { key: "crc_warn_threshold", card: "deploy", label: "אזהרת CRC למגירה", type: "number", min: 0, max: 1000000, unit: "שגיאות", zeroText: "כבויה", more: "מגירה שמונה ה-CRC שלה מעל זה מסומנת בהערה — כבל או מגירה חשודים (לא צבע הדיסק). ברירת המחדל 500; 0 = כבוי; האזהרה העיקרית היא עליית המונה מאז הסבב הקודם" },
  // ‏#1864 (נדב 20/10): כבוי (ברירת המחדל) = מדגם של ~5% מהבלוקים; דלוק = כולם. השרת מאמת true/false.
  { key: "readback_full", card: "deploy", label: "אימות מלא אחרי הכתיבה", type: "switch",
    more: "אימות מלא מוסיף כ-5 דק' לסבב של 4 מגירות. כבוי (ברירת המחדל): נבדק במדגם — כ-5% מהבלוקים בשימוש, מפוזרים על כל המחיצה (התחלה, אמצע, סוף ואקראי)" },
  // ‏#1914: הדיסק-און-קי לשיכפול בלי שרת — הקובץ שהשרת בנה (tools/build_usb_clone.sh --publish); הורדה למנהל.
  { key: "usbclone", card: "deploy", label: "דיסק-און-קי לשיכפול בלי שרת", type: "usbclone",
    more: "קובץ IMG לצריבה על דיסק-און-קי ב-Rufus במצב DD (balenaEtcher נכשל בשטח; בלינוקס: dd). המחשב עולה ממנו ישר למסך השיכפול המקומי, בלי שרת ובלי רשת. כל גרסה דורשת הורדה וצריבה מחדש" },
  // ‏#1972 (נדב 22/10): השרת שולח שאילתות IGMP ברשת ההפצה — סוויץ' עם IGMP snooping בלי querier מציף או חוסם. חסר = דלוק.
  { key: "igmp_querier", card: "deploy", label: "IGMP querier ברשת ההפצה", type: "switch", defaultOn: true, confirmOff: true,
    more: "השרת שולח שאילתת IGMP כל 125 שניות בכרטיס ההפצה, כדי שסוויץ' עם IGMP snooping ידע לאן לשלוח את השידור. כשיש ברשת querier אחר עם כתובת נמוכה יותר — השרת שותק לבד. המצב במסך הבריאות." },
  { key: "class_deploy_enabled", card: "deploy", label: "הפצה לכיתה ממסך התחנה", more: "כבוי במהדורת השיכפול — הכרטיס יורד מהתפריט והשרת מסרב לסבב כיתה", type: "switch" },
  { key: "cloner_beep", card: "deploy", label: "צליל בסיום סבב שיכפול", type: "switch", defaultOn: true,
    more: "דלוק (ברירת המחדל): שלושה צפצופים קצרים בהצלחה, צפצוף ארוך בכשל, דרך הרמקול הפנימי של המחשב" },
  // נדב 18/10: מתג + "אחרי N דקות" (1–1440). ‏console_idle_seconds נשאר בשניות בשרת, 0 = כבוי.
  { key: "console_idle_seconds", card: "security", label: "ניתוק אוטומטי בחוסר פעילות", type: "idleswitch",
    more: "קונסולה פתוחה בלי מגע (עכבר/מקלדת) — מתנתקת אחרי הזמן שנקבע; תקף מיד לסשן הזה. כבוי — אין ניתוק בחוסר פעילות" },
  { key: "console_idle_seconds", card: "security", label: "אחרי", type: "idleminutes" },
  // נדב 18/10: "תכלס לא צריך לזה MFA. אם בנוי, אפשר להפעיל או לא" — כבוי כברירת מחדל.
  { key: "mfa_required_for_admins", card: "security", label: "חיוב אימות דו-שלבי למנהלים", type: "switch",
    more: "דלוק: כל מנהל (חוץ מהמקומי שנוצר בהתקנה) חייב לרשום MFA בכניסה הבאה. כבוי (ברירת המחדל): אין חיוב — מי שהפעיל MFA ממשיך להתבקש לקוד, ואפשר להפעיל או לבטל מ\"החשבון שלי\"." },
  { key: "recovery_require_login", card: "security", label: "שחזור מהשרת בלי סבב דורש כניסה", type: "switch", confirmOff: true,
    more: "מי שעומד ליד מחשב ומשחזר אליו אימג' מהשרת, כשאין סבב פתוח, מתבקש להיכנס עם משתמש וסיסמה של הקונסולה. בסבב פתוח לא מתבקשת כניסה. מומלץ דלוק." },
  { key: "identity_check", card: "security", label: "אימות מחשבים לפי כתובת ה-DHCP", type: "switch", defaultOn: true, confirmOff: true,
    more: "השרת מקבל פנייה ממחשב רק אם כתובת ה-IP שלו היא זו שהשרת נתן ל-MAC שלו. כך מחשב אחד לא יכול להתחזות לאחר. לכבות רק כשה-DHCP ברשת ההפצה אינו השרת הזה. הכיבוי נרשם ביומן." },
  // ‏#1973 (נדב 22/10): ‏GET/POST /ups — אין UPS / USB / רשת (שרת NUT). המצב החי מאותו מנטר שמכבה את השרת.
  { key: "ups", card: "general", label: "UPS (אל-פסק)", type: "ups",
    more: "בנפילת חשמל השרת רושם ביומן; בסוללה נמוכה הוא עוצר בגלוי את מה שרץ ונכבה מסודר" },
  { key: "tls", card: "security", label: "תעודת TLS", type: "tls" },
  { key: "version", card: "update", label: "גרסה מותקנת", type: "version" },
  { key: "update_enabled", card: "update", label: "עדכון גרסה מהקונסולה", more: "כבוי כברירת מחדל; החיבור היוצא לריפו הציבורי נפתח רק בזמן בדיקה/עדכון", type: "switch" },
  { key: "last_check", card: "update", label: "בדיקה אחרונה", type: "lastcheck" },
  { key: "auto_reboot", card: "update", label: "הפעלה מחדש אוטומטית", type: "reboot", more: "כבוי / כל יום / פעם בשבוע; בזמן עבודה ההפעלה נדחית" },
  // ‏#1895/#1956 (נדב 22/10): לאן המסד מגובה כל שעה (נשמר 30 יום) — תיקייה / SMB / NFS. לא נבחר = כתום בבריאות;
  // השרת כותב וקורא בחזרה קובץ בדיקה לפני שהוא שומר (‏POST /backup-target).
  { key: "backup_target", card: "update", label: "יעד גיבוי המסד", type: "backuptarget",
    more: "תיקייה בדיסק אחר, שיתוף SMB או NFS. המסד מגובה אליו כל שעה, וגיבויים נשמרים 30 יום. גיבוי שעבר עליו יותר משעתיים מסומן 'ישן' בבריאות" },
  { key: "backup", card: "update", label: "גיבוי הגדרות", type: "backup", more: "קובץ עם הגדרות השרת, לשחזור אחרי התקנה מחדש" },
];
function settingRow(key) { return SETTING_ROWS.find((r) => r.key === key); }
/* הערך כפי שהשרת החזיר, מנורמל: מתג → boolean (#855: חסר = דלוק), השאר מחרוזת. */
function settingCurrent(key) {
  const r = settingRow(key), v = SETTINGS ? SETTINGS[key] : undefined;
  if (r && r.type === "switch") return r.defaultOn ? v !== "false" : v === "true";
  return v == null ? "" : String(v);
}
/* שניות → מה שאדם קורא: ‏600 → "10 דקות". */
function settingSecondsText(v) {
  const n = Number(v);
  if (v === "" || !Number.isFinite(n)) return "לא נקבע";
  return n % 60 === 0 && n >= 60 ? `${n / 60} ${n === 60 ? "דקה" : "דקות"}` : `${n} שניות`;
}
function ghostBtn(label, fn) { return `<button class="btn sm flat" type="button" onclick="${fn}">${esc(label)}</button>`; }
/* ‏dl.kv של המוקאפ: [dt, dd(html)] */
function settingsKv(rows) { return `<dl class="mkv">${rows.map(([dt, dd, attrs]) => `<dt${attrs || ""}>${esc(dt)}</dt><dd${attrs || ""}>${dd}</dd>`).join("")}</dl>`; }
/* ‏#1676: "לא נקרא" נשאר גלוי (עיקרון 5), אבל שגיאת מערכת גולמית (נתיב, Errno) — מאחורי "פרטים טכניים",
   כמו UI.noteTech; משפט עברי מהשרת נשאר בשורה. */
function settingUnreadHtml(err) {
  const raw = err == null ? "" : String(err);
  if (raw && isRawTechnical(raw)) return `<span class="muted">לא נקרא</span><details class="tech"><summary>פרטים טכניים</summary><code dir="ltr">${esc(raw)}</code></details>`;
  return `<span class="muted">לא נקרא${raw ? ": " + esc(raw) : ""}</span>`;
}
/* ‏#1675: יחידה עברית אינה עטופה ב-ltr ("שגיאות 0"); ‏0 עם zeroText = כבוי. */
function settingNumberText(r, v) {
  if (v === "") return `<span class="muted">לא נקבע</span>`;
  if (r.zeroText && Number(v) === 0) return `<span class="muted">${esc(r.zeroText)}</span>`;
  const text = r.unit ? `${v} ${r.unit}` : settingSecondsText(v);
  return `<bdi>${esc(r.prefix ? `${r.prefix} ${text}` : text)}</bdi>`;
}
const REBOOT_DAYS = ["יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "שבת", "יום ראשון"];
/* שורה אחת: "כבוי" / "כל יום ב-03:00" / "כל יום שני ב-03:00". */
function autoRebootText() {
  const mode = settingCurrent("auto_reboot"), at = settingCurrent("auto_reboot_time");
  if (mode === "off" || mode === "") return "כבוי";
  if (mode === "daily") return `כל יום ב-${at}`;
  if (mode === "weekly") return `כל ${REBOOT_DAYS[Number(settingCurrent("auto_reboot_weekday"))] || "שבוע"} ב-${at}`;
  return mode;
}
function settingSwitchHtml(r) {
  const off = secondaryReadOnlyRow(r) ? " disabled" : "";
  return `<label class="msw solo" id="srow-${r.key}"><input type="checkbox" role="switch" aria-label="${esc(r.label)}"${settingCurrent(r.key) ? " checked" : ""}${off} onchange="settingsSwitch('${r.key}', this)"><span class="track"></span></label>`;
}
/* שינוי שם בהקשר של שרת משנה: ‏/hostname אינו בערוץ בין השרתים (מפעיל את שרת המשנה מחדש) — משנים מהקונסולה שלו. */
function secondaryReadOnlyRow(r) { return r.type === "name" && !!SERVER_CTX; }
function tlsRowHtml() {
  if (!TLS_STATUS) return settingUnreadHtml(tlsError || "לא נטען");
  if (!TLS_STATUS.enabled) return `<span class="muted">בלי TLS (loopback בלבד)</span>`;
  const c = TLS_STATUS.on_disk;
  const warn = (TLS_STATUS.pending_restart ? ` ${UI.pill("warn", "ממתינה לאתחול")}` : "")
    + (TLS_STATUS.covers_hostname ? "" : ` ${UI.pill("warn", "השם אינו בתעודה")}`);
  return `<span class="sv">${esc(c.self_signed ? "תעודה עצמית" : "תעודה חתומה")} <span class="muted">· בתוקף עד</span> ${tlsExpiryHtml(c.not_after)}</span>${warn} ${ghostBtn("פרטים…", "tlsDetailsSheet()")}`;
}
/* נדב 18/10: ניתוק בחוסר פעילות — בדקות בממשק, בשניות בשרת (0 = כבוי). */
const IDLE_MAX_MINUTES = 1440, IDLE_DEFAULT_SECONDS = 600;
function idleSecondsNow() {
  const n = Number(SETTINGS && SETTINGS.console_idle_seconds);
  return Number.isFinite(n) ? n : IDLE_DEFAULT_SECONDS;
}
function idleMinutesText(seconds) {
  const m = Math.max(1, Math.round(seconds / 60));
  return m === 1 ? "דקה אחת" : `${m} דקות`;
}
async function idleSave(seconds) {
  await post("/settings", { console_idle_seconds: String(seconds) });
  if (ME) { ME.idle_seconds = seconds; startIdleWatch(); }   // תקף מיד, בלי כניסה מחדש
  toast(seconds ? `ניתוק אוטומטי: אחרי ${idleMinutesText(seconds)} — נרשם ביומן` : "ניתוק אוטומטי כבוי — נרשם ביומן");
  await loadSettingsData();
}
function idleSwitch(input) {
  if (!isAdmin()) return;
  const want = !!(input && input.checked);
  input.disabled = true;
  idleSave(want ? IDLE_DEFAULT_SECONDS : 0).catch((e) => { input.checked = !want; input.disabled = false; toast(e.message); });
}
function idleMinutesSheet() {
  if (!isAdmin()) return;
  sheet({ title: "ניתוק אוטומטי בחוסר פעילות", sub: "אחרי כמה דקות בלי מגע הקונסולה מתנתקת.",
    fields: [{ id: "v", label: `דקות (1–${IDLE_MAX_MINUTES})`, value: String(Math.max(1, Math.round(idleSecondsNow() / 60))), dir: "ltr" }],
    onSubmit: async (v) => {
      const value = String(v.v == null ? "" : v.v).trim(), n = Number(value);
      if (value === "" || !Number.isInteger(n) || n < 1 || n > IDLE_MAX_MINUTES)
        throw new Error(`מספר שלם של דקות, 1–${IDLE_MAX_MINUTES}`);
      await idleSave(n * 60);
    } });
}

function settingDd(r) {
  if (r.type === "switch") return settingSwitchHtml(r);
  if (r.type === "idleswitch")
    return `<label class="msw solo" id="srow-idle"><input type="checkbox" role="switch" aria-label="${esc(r.label)}"${idleSecondsNow() > 0 ? " checked" : ""} onchange="idleSwitch(this)"><span class="track"></span></label>`;
  if (r.type === "idleminutes") return `<span class="sv">${esc(idleMinutesText(idleSecondsNow()))}</span> ${ghostBtn("שינוי…", "idleMinutesSheet()")}`;
  if (r.type === "tls") return tlsRowHtml();
  if (r.type === "timezone") return timezoneRow(r)[1];   // #1447
  if (r.type === "ntpsource") return ntpSourceHtml();
  if (r.type === "ntpstatus") return ntpStatusHtml();
  if (r.type === "ups") return upsRowHtml();   // #1973
  if (r.type === "name") {
    const v = settingCurrent(r.key);
    const btn = SERVER_CTX ? `<button class="btn sm flat" type="button" disabled title="שם של שרת משנה משנים מהקונסולה שלו — השינוי מפעיל אותו מחדש">שינוי שם…</button>` : ghostBtn("שינוי שם…", "hostnameSheet()");
    return `${v ? `<span class="sv mono" dir="ltr">${esc(v)}</span>` : `<span class="muted">לא נקרא</span>`} ${btn}`;
  }
  if (r.type === "version") {
    if (updateError) return settingUnreadHtml(updateError);
    const u = HEALTH_UPDATE;
    return u && u.current ? `<span class="sv mono" dir="ltr">${esc(u.current)}</span>` : `<span class="muted">לא ידועה (אין תגית git על העץ)</span>`;
  }
  if (r.type === "lastcheck") return `<span class="sv">${updateLastCheckHtml(HEALTH_UPDATE)}</span> ${ghostBtn("בדוק עכשיו", "healthUpdateCheck()")}`;
  if (r.type === "reboot") return `<span class="sv">${esc(autoRebootText())}</span> ${ghostBtn("שינוי…", "autoRebootSheet()")}`;
  if (r.type === "backup") return ghostBtn("הורדה…", "confirmSettingsBackup()");
  if (r.type === "usbclone") return usbCloneRowHtml();   // #1914
  if (r.type === "backuptarget") return backupTargetRowHtml();   // #1956
  const v = settingCurrent(r.key);
  return `<span class="sv">${r.type === "number" ? settingNumberText(r, v) : (v ? esc(v) : `<span class="muted">לא נקבע</span>`)}</span> ${ghostBtn("שינוי…", `settingsValueSheet('${r.key}')`)}`;
}
function settingRowVisible(r) {
  if (r.key === "class_deploy_enabled") return classroomsOn();
  if (r.type === "idleminutes") return idleSecondsNow() > 0;   // "אחרי N דקות" — רק כשהניתוק פעיל
  // ‏"בדיקה אחרונה" רק כשהמתג דלוק — בלעדיו השרת אינו בודק (404).
  if (r.type === "lastcheck") return !!(HEALTH_UPDATE && HEALTH_UPDATE.enabled);
  return true;
}
function settingsGroupHtml(g) {
  const rows = SETTING_ROWS.filter((r) => r.card === g.card && settingRowVisible(r))
    .map((r) => [r.label, settingDd(r), r.more ? ` title="${esc(r.more)}"` : ""]);
  const extra = g.card === "update" ? updateActionsHtml() : "";
  const acts = g.card === "update" && updateUpToDate() ? UI.pill("ok", "✓ מעודכן") : "";
  return `<section class="set-grp">${UI.card({ title: g.title, acts, body: settingsKv(rows) + extra })}<p class="set-foot">${esc(g.foot)}</p></section>`;
}
/* מתג נשמר מיד. כיבוי של מתג confirmOff — קודם חלון אישור (כפתור אדום); עד האישור המתג חוזר למצבו. */
function settingsSwitch(key, input) {
  const r = settingRow(key), want = !!(input && input.checked);
  if (!r || !isAdmin()) return;
  const save = async () => {
    await post("/settings", { [key]: want ? "true" : "false" });
    toast(`${r.label}: ${want ? "דלוק" : "כבוי"} — נשמר ונרשם ביומן`);
    await loadSettingsData();
  };
  if (r.confirmOff && !want) {
    input.checked = true;
    confirmSheet(`לכבות: ${r.label}?`, r.more || "", "כיבוי", save, [["w", "השינוי חל מיד ונרשם ביומן"]]);
    return;
  }
  input.disabled = true;
  save().catch((e) => { input.checked = !want; input.disabled = false; toast(e.message); });
}
/* ערך (שם / שניות) — חלון קטן שנשמר מיד, לא שדה פתוח בדף. */
function settingsValueSheet(key) {
  const r = settingRow(key);
  if (!r || !isAdmin()) return;
  sheet({ title: r.label, sub: r.more || "",
    fields: [{ id: "v", label: r.unit ? `${r.label} (${r.unit}, ${r.min}–${r.max})` : `${r.label} (בשניות, ${r.min} לפחות)`, value: settingCurrent(key) }],
    onSubmit: async (v) => {
      const value = String(v.v == null ? "" : v.v).trim();
      const n = Number(value);
      if (value === "" || !Number.isInteger(n) || n < r.min || (r.max != null && n > r.max)) throw new Error(`${r.label}: מספר שלם, ${r.max != null ? `${r.min}–${r.max}` : `${r.min} לפחות`}`);
      await post("/settings", { [key]: String(n) });
      toast("נשמר — נרשם ביומן");
      await loadSettingsData();
    } });
}
/* ‏#1956: יעד גיבוי המסד — תיקייה / SMB / NFS. השורה: מה נבחר, ובכתום כשלא נבחר, כשהתיקייה על דיסק המסד, או כשלא
   נבדק אם היא על דיסק אחר. "לא הגיע מהשרת" ≠ "לא נבחר" (עיקרון 5). הסיסמה לא חוזרת מהשרת לעולם. */
function backupTargetRowHtml() {
  const t = SETTINGS && SETTINGS.backup_target;
  if (!t || !t.type) return settingUnreadHtml("יעד הגיבוי לא הגיע מהשרת");
  if (t.type === "none") return `${UI.pill("warn", "לא נבחר — המסד אינו מגובה")} ${ghostBtn("בחירה…", "backupTargetSheet()")}`;
  const kind = { local: "תיקייה", smb: "SMB", nfs: "NFS" }[t.type] || t.type;
  const warn = t.type !== "local" ? "" : t.same_disk === true ? ` ${UI.pill("warn", "אותו דיסק — לא מגן מתקלת דיסק")}`
    : t.same_disk == null ? ` ${UI.pill("warn", "לא נבדק אם על דיסק אחר")}` : "";
  return `<span class="sv">${esc(kind)} · <bdi dir="ltr">${esc(t.display || t.dest)}</bdi></span>${warn} ${ghostBtn("שינוי…", "backupTargetSheet()")}`;
}
/* גוף הבקשה מערכי החלון: השדות של הסוג בלבד; הכול trim חוץ מהסיסמה. */
function backupTargetBody(kind, v) {
  const keys = { local: ["path"], smb: ["server", "share", "username", "secret", "domain"], nfs: ["server", "export", "version"] }[kind] || [];
  const params = {};
  keys.forEach((k) => { const raw = v[k] == null ? "" : String(v[k]); params[k] = k === "secret" ? raw : raw.trim(); });
  return { type: kind, params };
}
async function backupTargetSave(kind, v) {
  const r = await post("/backup-target", backupTargetBody(kind, v));
  const warn = r.warnings && r.warnings.length ? r.warnings.join(" · ") + " — " : "";
  toast(warn + (kind === "none" ? "היעד בוטל — נרשם ביומן" : "קובץ הבדיקה נכתב ונקרא בחזרה — נשמר ונרשם ביומן"));
  await loadSettingsData();
}
function backupTargetSheet() {
  if (!isAdmin()) return;
  const t = (SETTINGS && SETTINGS.backup_target) || { type: "none" };
  const options = [
    { value: "local", label: "תיקייה בשרת", hint: "דיסק שני או USB מעוגן. על דיסק המסד — אזהרה: לא מגן מתקלת דיסק" },
    { value: "smb", label: "שיתוף SMB", hint: "שיתוף Windows או NAS, עם משתמש וסיסמה" },
    { value: "nfs", label: "NFS", hint: "ייצוא NFS מ-NAS" }];
  if (t.type !== "none") options.push({ value: "none", label: "ביטול היעד", hint: "המסד לא יגובה, והבריאות תהיה כתומה" });
  sheet({ title: "יעד גיבוי המסד", sub: "המסד מגובה כל שעה, וגיבויים נשמרים 30 יום. בשמירה השרת כותב קובץ בדיקה ליעד וקורא אותו בחזרה",
    fields: [{ id: "kind", label: "לאן לגבות", type: "radio", value: t.type === "none" ? "local" : t.type, options }],
    submitLabel: "המשך",
    onSubmit: async (v) => {
      if (v.kind === "none") { await backupTargetSave("none", {}); return undefined; }
      return () => backupTargetDetailsSheet(v.kind, t.type === v.kind ? t : {});
    } });
}
function backupTargetDetailsSheet(kind, t) {
  const fields = {
    local: [{ id: "path", label: "נתיב התיקייה", value: t.dest || "", placeholder: "/mnt/backup", dir: "ltr", help: "תיקייה קיימת על דיסק מעוגן; לא שורש מערכת הקבצים" }],
    smb: [{ id: "server", label: "שרת (IP או שם)", value: t.server || "", dir: "ltr" },
      { id: "share", label: "שם השיתוף", value: t.share || "", dir: "ltr" },
      { id: "username", label: "משתמש", value: t.username || "", dir: "ltr" },
      { id: "secret", label: "סיסמה", type: "password", policy: false, autocomplete: "new-password" },
      { id: "domain", label: "דומיין (לא חובה)", value: t.domain || "", dir: "ltr" }],
    nfs: [{ id: "server", label: "שרת (IP או שם)", value: t.server || "", dir: "ltr" },
      { id: "export", label: "נתיב הייצוא", value: t.export || "", placeholder: "/pool/backup", dir: "ltr" },
      { id: "version", label: "גרסת NFS", value: t.version || "4.1", dir: "ltr" }] }[kind];
  const title = { local: "תיקייה בשרת", smb: "שיתוף SMB", nfs: "NFS" }[kind];
  sheet({ title: `יעד גיבוי המסד — ${title}`, sub: "השרת יעגן את היעד, יכתוב אליו קובץ בדיקה ויקרא אותו בחזרה. יעד שלא עובר — לא נשמר",
    fields, submitLabel: "בדיקה ושמירה", onSubmit: async (v) => { await backupTargetSave(kind, v); } });
}
/* ‏#1447: אזור הזמן — "לא נקרא" ≠ ערך (עיקרון 5); מערכת שמדווחת אזור אחר מההגדרה (שרת שהותקן ב-UTC
   ועוד לא עלה מחדש, או timedatectl שנכשל) — נאמר בשורה, לא מוסתר. */
let TZ_STATUS = null, tzError = "";
function timezoneRow(r) {
  const attrs = r.more ? ` title="${esc(r.more)}"` : "";
  if (!TZ_STATUS) return [r.label, `<span class="muted">לא נקרא${tzError ? ": " + esc(tzError) : ""}</span>`, attrs];
  const sys = TZ_STATUS.system;
  const differs = sys === TZ_STATUS.zone ? "" : ` <span class="muted">· מסך השרת: ${sys ? ltr(sys) : "לא נקרא"}</span>`;
  return [r.label, `${ltr(TZ_STATUS.zone)}${differs} ${ghostBtn(r.action, "timezoneSheet()")}`, attrs];
}
function timezoneSheet() {
  if (!isAdmin() || !TZ_STATUS) return;
  sheet({ title: "אזור זמן", sub: "מסך השרת, מסכי התחנות והפעלה מחדש אוטומטית יציגו את השעה לפי האזור הזה",
    fields: [{ id: "zone", label: "אזור זמן", type: "select", value: TZ_STATUS.zone, options: TZ_STATUS.zones.map((z) => ({ value: z, label: z })) }],
    onSubmit: async (v) => {
      await post("/timezone", { zone: v.zone });
      toast("נשמר — נרשם ביומן"); await loadSettingsData();
    } });
}
let NTP_STATUS = null, ntpError = "";
function ntpSourceHtml() {
  if (!NTP_STATUS) return settingUnreadHtml(ntpError || "לא נטען");
  const value = NTP_STATUS.ntp_servers;
  const text = value ? `שרת זמן של הארגון: ${value}` : "אוטומטי (שרתי זמן באינטרנט)";
  return `<span class="sv">${esc(text)}</span> ${ghostBtn("שינוי…", "timesyncSheet()")}`;
}
function ntpStatusHtml() {
  if (!NTP_STATUS || !NTP_STATUS.status || NTP_STATUS.status.synchronized == null)
    return `<span class="muted">לא נבדק</span> ${ghostBtn("בדוק עכשיו", "timesyncCheckNow()")}`;
  const s = NTP_STATUS.status;
  if (!s.synchronized)
    return `${UI.pill("warn", "לא מתואם — שרת הזמן טרם סנכרן את השעון")} ${ghostBtn("בדוק עכשיו", "timesyncCheckNow()")}`;
  const when = fmtClock(s.checked_at);   // #1933: the server's clock zone, not the browser's ("—" when unread)
  const offset = s.offset_ms == null ? "—" : Number(s.offset_ms).toFixed(3);
  return `${UI.pill("ok", `● מתואם · ${s.server || "—"} · סטייה ${offset} ms · נבדק ${when}`)} ${ghostBtn("בדוק עכשיו", "timesyncCheckNow()")}`;
}
function timesyncSheet() {
  if (!isAdmin() || !NTP_STATUS) return;
  const custom = !!NTP_STATUS.ntp_servers;
  sheet({ title: "מקור השעה", submitLabel: "שמירה", fields: [
    { id: "mode", type: "radio", value: custom ? "custom" : "auto", options: [
      { value: "auto", label: "אוטומטי (שרתי זמן באינטרנט)" },
      { value: "custom", label: "שרת זמן של הארגון" }] },
    { id: "servers", label: "כתובות (IP או שם, מופרדות ברווח)", value: NTP_STATUS.ntp_servers, dir: "ltr" }],
    onSubmit: async (v) => {
      await post("/timesync", { ntp_servers: v.mode === "custom" ? v.servers : "" });
      toast("נשמר — נרשם ביומן"); await loadHostCfg(); renderCurrent();
    } });
}
async function timesyncCheckNow() {
  try { const status = await post("/timesync/check", {}); NTP_STATUS.status = status; renderCurrent(); }
  catch (e) { toast(e.message); await loadHostCfg(); renderCurrent(); }
}
/* ‏#1973: ה-UPS — המצב השמור והקריאה האחרונה של המנטר. "לא נקרא" ≠ "על חשמל" (עיקרון 5). */
let UPS_STATUS = null, upsError = "";
const UPS_PILL = { ok: "ok", warn: "warn", bad: "err" };
function upsRowHtml() {
  if (!UPS_STATUS) return settingUnreadHtml(upsError || "לא נטען");
  const u = UPS_STATUS, btn = ghostBtn("שינוי…", "upsSheet()");
  const mode = (u.modes && u.modes[u.mode]) || u.mode;
  const where = u.mode === "net" && u.target ? ` · <span class="mono" dir="ltr">${esc(u.target)}</span>` : "";
  if (u.mode === "none") return `<span class="sv">${esc(mode)}</span> ${btn}`;
  const kind = UPS_PILL[u.state];
  const state = kind ? UI.pill(kind, u.detail || "") : `<span class="muted">לא נבדק${u.detail ? ": " + esc(u.detail) : ""}</span>`;
  return `<span class="sv">${esc(mode)}${where}</span> ${state} ${btn}`;
}
function upsSheet() {
  if (!isAdmin() || !UPS_STATUS) return;
  const u = UPS_STATUS;
  sheet({ title: "UPS (אל-פסק)", sub: "בנפילת חשמל — שורה ביומן; בסוללה נמוכה — עצירה גלויה של מה שרץ, ואז כיבוי מסודר",
    submitLabel: "שמירה", fields: [
      { id: "mode", type: "radio", value: u.mode, options: ["none", "usb", "net"].map((m) => ({ value: m, label: u.modes[m] })) },
      { id: "target", label: "שרת NUT (ups@כתובת, למשל ups@10.0.0.5)", value: u.mode === "net" ? u.target : "", dir: "ltr" }],
    onSubmit: async (v) => {
      const r = await post("/ups", { mode: v.mode, target: v.mode === "net" ? v.target : "" });
      const err = r.applied && r.applied.error;
      toast(err ? `נשמר — אבל ה-UPS לא נקרא: ${err}` : "נשמר — נרשם ביומן", err ? 6000 : undefined);
      await loadHostCfg(); renderCurrent();
    } });
}
function autoRebootSheet() {
  if (!isAdmin()) return;
  sheet({ title: "הפעלה מחדש אוטומטית", sub: "לפי השעה המקומית של השרת",
    fields: [
      { id: "mode", label: "תדירות", type: "select", value: settingCurrent("auto_reboot"), options: [{value:"off", label:"כבוי"}, {value:"daily", label:"כל יום"}, {value:"weekly", label:"פעם בשבוע"}] },
      { id: "time", label: "שעה", type: "time", value: settingCurrent("auto_reboot_time") },
      { id: "weekday", label: "יום (בהפעלה שבועית)", type: "select", value: settingCurrent("auto_reboot_weekday"), options: REBOOT_DAYS.map((label, i) => ({ value: String(i), label })) }],
    onSubmit: async (v) => {
      await post("/settings", { auto_reboot: v.mode, auto_reboot_time: v.time, auto_reboot_weekday: v.weekday });
      toast("נשמר — נרשם ביומן"); await loadSettingsData();
    } });
}
/* ‏לשונית "שרתי משנה" (נדב 18/10): רק בראשי/עצמאי ורק ל-admin; בשרת משנה היכולות כבויות — אין לשונית כלל. */
function settingsSecondariesAllowed() {
  return isAdmin() && !SERVER_CTX && !!(ME.capabilities && (ME.capabilities.enroll_secondary || ME.capabilities.interbranch_transfer));
}
function settings() {
  if (!SETTINGS && !settingsError) return pagePlaceholder();
  const tabs = settingsSecondariesAllowed() ? ["כללי", "שרתי משנה"] : [];
  const tab = tabs.length ? Math.min(currentTab, 1) : 0;
  const sub = SETTINGS ? ["הגדרות השרת", ME && ME.version ? `גרסה ${ME.version}` : "", "כל שינוי נשמר מיד ונרשם ביומן"].filter(Boolean).map(esc).join(" · ") : `‏/settings לא נקרא: ${esc(settingsError)}`;
  const pill = !SETTINGS ? UI.pill("err", "לא נקרא") : "";
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "ניהול" }, { label: "הגדרות" }],
    icon: "t-sliders", name: "הגדרות", sub, pill, tabs, tab });
  const body = !SETTINGS
    ? UI.note("err", `לא הצלחתי לקרוא את ההגדרות: ${esc(settingsError)}`)
    : tab === 1 ? settingsSecondariesTab()
    : `<div class="set-col">${SETTING_GROUPS.map(settingsGroupHtml).join("")}</div>`;
  return `<div class="page aligned settings">${header}<div class="body">${body}</div></div>`;
}
async function loadSettingsData() {
  try { SETTINGS = await api("/settings"); settingsError = ""; }
  catch (e) { SETTINGS = null; settingsError = e.message; toast("טעינת ההגדרות נכשלה: " + e.message); }
  await loadHealthUpdate();   // קבוצת העדכונים
  await loadHostCfg();       // #153: תעודה
  await loadUsbClone();      // #1914: הדיסק-און-קי לשיכפול
  if (current === "settings") renderCurrent();
  if (settingsSecondariesAllowed()) loadSettingsSecondaries();   // הלשונית "שרתי משנה" — לא חוסמת את שאר העמוד
}
/* ---------- #153: תעודת TLS ושם השרת ----------
   שתיהן מסתיימות באתחול השרת (התעודה נטענת רק בעליית uvicorn; שם המארח — ‏hostnamectl) — ולכן חלון
   אישור עם ההשלכות וכפתור אדום, בלי הקלדת מילה (#1270). "לא נקרא" ≠ "אין" (עיקרון 5). */
let TLS_STATUS = null, tlsError = "";
async function loadHostCfg() {
  try { TLS_STATUS = await api("/tls"); tlsError = ""; } catch (e) { TLS_STATUS = null; tlsError = e.message; }
  try { TZ_STATUS = await api("/timezone"); tzError = ""; } catch (e) { TZ_STATUS = null; tzError = e.message; }   // #1447
  try { NTP_STATUS = await api("/timesync"); ntpError = ""; } catch (e) { NTP_STATUS = null; ntpError = e.message; }
  try { UPS_STATUS = await api("/ups"); upsError = ""; } catch (e) { UPS_STATUS = null; upsError = e.message; }   // #1973
}
function tlsModeHe(tls) { return tls && tls.mode === "ca-signed" ? "תעודה חתומה" : "תעודה עצמית"; }
function tlsExpiryHtml(iso) {
  const days = Math.floor((Date.parse(iso) - Date.now()) / 86400000);
  const when = `<span dir="ltr">${esc(fmtDate(iso))}</span>`;
  if (!(days >= 0)) return `${when} ${UI.pill("err", "פג תוקף")}`;
  return days < 30 ? `${when} ${UI.pill("warn", `עוד ${days} ימים`)}` : when;
}
/* "פרטים…" של שורת התעודה: חמש השורות הטכניות והאזהרות — רמה אחת פנימה (§16 Simplicity), ו"העלאת תעודה…". */
function tlsDetailsSheet() {
  if (!TLS_STATUS || !TLS_STATUS.enabled) return;
  const c = TLS_STATUS.on_disk, fp = esc(c.fingerprint_sha256);
  const pairs = [["סוג", c.self_signed ? "תעודה עצמית" : `חתומה בידי ${esc(c.issuer || "—")}`],
    ["הונפקה ל", `<span class="mono" dir="ltr">${esc(c.subject || "—")}</span>`],
    ["שמות (SAN)", c.sans.length ? `<span class="mono" dir="ltr">${c.sans.map(esc).join(", ")}</span>` : "—"],
    ["בתוקף עד", tlsExpiryHtml(c.not_after)],
    ["SHA-256", `<span class="mono" dir="ltr" title="${fp}">${esc(c.fingerprint_sha256.slice(0, 23))}…</span>`]];
  const notes = (TLS_STATUS.pending_restart ? UI.note("warn", "התעודה על הדיסק שונה מזו שבשימוש — היא תיכנס לשימוש באתחול השרת") : "")
    + (TLS_STATUS.covers_hostname ? "" : UI.note("warn", `שם השרת <span class="mono" dir="ltr">${esc(TLS_STATUS.hostname)}</span> אינו בתעודה — דפדפן שפונה בשם הזה יזהיר`));
  sheet({ title: "תעודת TLS", sub: "הקונסולה מוצגת בתעודה הזו", note: settingsKv(pairs) + notes,
    submitLabel: "העלאת תעודה…", cancelLabel: "סגירה", onSubmit: async () => tlsUploadSheet,
    // ‏"הנפקת תעודה חדשה" — רק לתעודה עצמית, ליד "העלאת תעודה…" (נדב 17/10)
    extra: c.self_signed ? { label: "הנפקת תעודה חדשה…", onClick: tlsReissueSheet } : null });
}
function tlsReissueSheet() {
  confirmSheet("הנפקת תעודה חדשה", "תעודה עצמית חדשה לקונסולה, במקום הנוכחית",
    "הנפקה והפעלה מחדש", async () => {
      const r = await post("/tls/reissue", {});
      toast(`תעודה חדשה הונפקה (בתוקף עד ${fmtDate(r.certificate.not_after)}) — השרת מופעל מחדש`, 6000);
      await loadHostCfg();
      if (current === "settings") renderCurrent();
    },
    [["w", "הדפדפן יבקש לאשר את התעודה מחדש, פעם אחת"],
     ["w", "התעודה החדשה נכנסת לתוקף בהפעלה מחדש של השרת — הקונסולה תתנתק לכמה שניות"],
     ["w", "התעודה הקודמת נשמרת ליד הנוכחית (‎console.crt.prev‎)"]]);
}
function tlsUploadSheet() {
  sheet({
    title: "העלאת תעודת TLS", sub: "תעודה ומפתח פרטי בפורמט PEM; המפתח לא מוצפן",
    fields: [{ id: "cert", label: "תעודה (‎.crt / .pem‎, כולל שרשרת)", type: "file", dir: "ltr" },
             { id: "key", label: "מפתח פרטי (‎.key‎)", type: "file", dir: "ltr" }],
    note: impactList([["w", "נבדק לפני ההחלה: PEM, התאמה בין התעודה למפתח, ותוקף"],
      ["w", "השרת יופעל מחדש והקונסולה תתנתק לכמה שניות"],
      ["w", "הזוג הקודם נשמר ליד הנוכחי (‎console.crt.prev‎)"]]),
    danger: true, submitLabel: "העלאה והפעלה מחדש",
    onSubmit: async () => {
      const cert = $("#sf-cert").files[0], key = $("#sf-key").files[0];
      if (!cert || !key) throw new Error("יש לבחור את שני הקבצים");
      const r = await post("/tls", { cert_pem: await cert.text(), key_pem: await key.text() });
      toast(`התעודה הוחלפה (בתוקף עד ${fmtDate(r.certificate.not_after)}) — השרת מופעל מחדש`, 6000);
      await loadHostCfg();
      if (current === "settings") renderCurrent();
    },
  });
}
/* ‏שינוי שם השרת — המקום היחיד (נדב 17/10): השם הוא שם המארח, ולכן ‏POST /hostname (‏hostnamectl, קריאה חוזרת,
   הפעלה מחדש). ‏primary — מהצומת העליון בעץ (לחיצה כפולה), תמיד על הראשי. */
function hostnameSheet(primary = false) {
  if (!isAdmin()) return;
  if (SERVER_CTX) { toast(primary ? "כדי לשנות את שם השרת הראשי — חזרו קודם לשרת הראשי" : "שם של שרת משנה משנים מהקונסולה שלו"); return; }
  const name = (SETTINGS && SETTINGS.server_name) || (ME && ME.server_name) || "";
  sheet({
    title: "שינוי שם השרת", sub: name,
    fields: [{ id: "hostname", label: "שם חדש", value: name, dir: "ltr", help: MACHINE_NAME_RULE }],
    note: impactList([["w", "השם מתחלף בעץ, בכותרת ובמסכי התחנות"],
      ["w", "שם המכונה ברשת (hostname) מתחלף איתו — השרת יופעל מחדש והקונסולה תתנתק לכמה שניות"],
      ["w", "אם השם החדש אינו בתעודת ה-TLS, הדפדפן יזהיר בגישה בשם"]]),
    danger: true, submitLabel: "שינוי שם והפעלה מחדש",
    onSubmit: async (v) => {
      const r = await post("/hostname", { hostname: v.hostname });
      toast(`שם השרת שונה ל-${r.hostname} — השרת מופעל מחדש`, 6000);
      if (ME) ME.server_name = r.hostname;
      if (PRIMARY_ME) PRIMARY_ME.server_name = r.hostname;
      applyServerName();
      if (current === "settings") await loadSettingsData();
    },
  });
}

/* ---------- #649 שלב 1: ארגז כלים ----------
   הקטלוג (server/tools_catalog.json ← docs/tools/TOOLS-CHOICE.md + CATALOG.md)
   כקבוצה = כרטיס, טבלה אחת לכל קבוצה: ☐ בנייה/שיכפול | ☐ תלמיד | כלי | בינארי |
   סיכון | גודל. שתי בחירות נפרדות (הכרעת נדב 17/09 — תלמיד = v2, הבחירה
   נשמרת כבר עכשיו); מה שמסומן ייארז ל-initrd בשלב 2. הסימון חי ב-TOOLS_DRAFT
   (Set לכל יעד) עד "שמור" (PUT /tools/selection); הכותרת, כפתור השמירה ומוני
   הקבוצות מתעדכנים במקום — בלי לצבוע את הדף מחדש בכל ☐ (הסינון לא מאבד פוקוס). */
let TOOLS = null, toolsError = "", TOOLS_DRAFT = null;
const TOOLS_FILTER = { q: "", risk: "", rec: false, packed: false, scope: "build" };
const TOOLS_TARGETS = ["build", "student"];
const TOOLS_RISK = { ro: ["", "קריאה", "ro — קריאה בלבד: מציג מידע ולא משנה כלום במחשב"],
                     rw: ["warn", "משנה", "rw — משנה משהו הפיך (סדר אתחול, איפוס USB, הערת מחשב)"],
                     destroy: ["err", "מוחק", "destroy — מוחק נתונים: דורש חלון אישור לפני ההרצה"] };

async function loadTools() {
  try {
    TOOLS = await api("/tools/catalog");
    toolsError = "";
    TOOLS_DRAFT = { build: new Set(TOOLS.selection.build), student: new Set(TOOLS.selection.student) };
  } catch (e) {
    TOOLS = null; TOOLS_DRAFT = null;   // לא נקרא ≠ קטלוג ריק (עיקרון 5)
    toolsError = e.message;
    toast("טעינת ארגז הכלים נכשלה: " + e.message);
  }
  if (current === "tools") renderCurrent();
}

function toolsDirty() {
  if (!TOOLS || !TOOLS_DRAFT) return false;
  return TOOLS_TARGETS.some((t) => {
    const saved = TOOLS.selection[t] || [];
    return saved.length !== TOOLS_DRAFT[t].size || saved.some((id) => !TOOLS_DRAFT[t].has(id));
  });
}
/* ספירה וגודל משוער של הסימון — כמו summarize() בשרת: ארוז = 0, בלי מספר = "לא נמדד" (נספר, לא נסכם). */
function toolsSummary(target) {
  const chosen = (TOOLS.tools || []).filter((t) => TOOLS_DRAFT[target].has(t.id));
  const known = chosen.reduce((s, t) => s + (!t.packed && t.size_kb != null ? t.size_kb : 0), 0);
  return { count: chosen.length, size_kb_known: known, size_unknown: chosen.filter((t) => !t.packed && t.size_kb == null).length };
}
function toolsSizeText(kb) { return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} kB`; }
function toolsPillText() {
  const b = toolsSummary("build"), s = toolsSummary("student");
  const kb = b.size_kb_known + s.size_kb_known, unknown = b.size_unknown + s.size_unknown;
  return `${b.count} לבנייה · ${s.count} לתלמיד · תוספת משוערת ~${toolsSizeText(kb)}${unknown ? ` (+${unknown} לא נמדדו)` : ""}`;
}
function toolsVisible(t) {
  const f = TOOLS_FILTER, q = f.q.trim().toLowerCase();
  if (q && !`${t.title_he} ${t.what} ${t.binary} ${t.id}`.toLowerCase().includes(q)) return false;
  if (f.risk && t.risk !== f.risk) return false;
  if (f.rec && !t.recommended) return false;
  if (f.packed && !t.packed) return false;
  return true;
}
function toolsFilter(key, value) {
  TOOLS_FILTER[key] = value;
  if (key === "scope") return;
  const box = document.getElementById("tools-groups");
  if (box) box.innerHTML = toolsGroupsHtml();
  const n = document.getElementById("tools-count");
  if (n) n.textContent = toolsCountText();
}
function toolsCountText() {
  const all = TOOLS.tools.length, shown = TOOLS.tools.filter(toolsVisible).length;
  return shown === all ? `${all} כלים` : `${shown} מתוך ${all} כלים`;
}
/* ☐ אחד: מעדכן את הטיוטה ואת מה שתלוי בה — בלי לצבוע מחדש. */
function toolsToggle(id, target, on) {
  if (!TOOLS_DRAFT) return;
  if (on) TOOLS_DRAFT[target].add(id); else TOOLS_DRAFT[target].delete(id);
  toolsRefreshHeader();
}
function toolsRefreshHeader() {
  const pill = document.getElementById("tools-pill"); if (pill) pill.textContent = toolsPillText();
  const dirty = toolsDirty();
  const save = document.getElementById("tools-save"); if (save) save.disabled = !dirty;
  const cancel = document.getElementById("tools-cancel"); if (cancel) cancel.hidden = !dirty;
  const state = document.getElementById("tools-state"); if (state) state.innerHTML = dirty ? UI.pill("warn", "שינויים לא נשמרו") : "";
  (TOOLS.groups || []).forEach((g, i) => { const el = document.getElementById(`tools-grp-${i}`); if (el) el.textContent = toolsGroupCaption(g); });
}
function toolsGroupCaption(group) {
  const list = TOOLS.tools.filter((t) => t.group === group);
  const n = (target) => list.filter((t) => TOOLS_DRAFT[target].has(t.id)).length;
  return `${list.length} כלים · ${n("build")} לבנייה · ${n("student")} לתלמיד`;
}
/* "סמן את המומלצים" / "נקה" לקבוצה — על היעד שנבחר בשורת הסינון (בנייה/תלמיד/שניהם).
   "סמן את המומלצים" מוסיף ואינו מוריד סימון קיים; "נקה" מוריד את כל הקבוצה. */
function toolsGroupMark(groupIndex, mode) {
  const group = TOOLS.groups[groupIndex];
  const targets = TOOLS_FILTER.scope === "both" ? TOOLS_TARGETS : [TOOLS_FILTER.scope];
  for (const t of TOOLS.tools.filter((x) => x.group === group)) {
    for (const target of targets) {
      if (mode === "clear") TOOLS_DRAFT[target].delete(t.id);
      else if (t.recommended) TOOLS_DRAFT[target].add(t.id);
      const box = document.getElementById(`tool-${target}-${t.id}`);
      if (box) box.checked = TOOLS_DRAFT[target].has(t.id);
    }
  }
  toolsRefreshHeader();
}
function toolsCancel() {
  TOOLS_DRAFT = { build: new Set(TOOLS.selection.build), student: new Set(TOOLS.selection.student) };
  renderCurrent();
}
async function toolsSave() {
  if (!TOOLS_DRAFT || !toolsDirty()) return;
  const body = { build: [...TOOLS_DRAFT.build], student: [...TOOLS_DRAFT.student] };
  try {
    const r = await put("/tools/selection", body);
    toast(`הבחירה נשמרה: ${r.summary.build.count} לבנייה · ${r.summary.student.count} לתלמיד`);
  } catch (e) { toast("השמירה נכשלה: " + e.message); return; }
  await loadTools();
}

function toolRowHtml(t) {
  const box = (target, label) => `<input type="checkbox" id="tool-${target}-${esc(t.id)}" aria-label="${esc(label)}: ${esc(t.title_he)}"${TOOLS_DRAFT[target].has(t.id) ? " checked" : ""} onchange="toolsToggle('${esc(t.id)}','${target}',this.checked)">`;
  const [cls, label, title] = TOOLS_RISK[t.risk] || ["", t.risk, ""];
  const risk = `<span title="${esc(title)}">${UI.pill(cls, label)}</span>`;
  let size;
  if (t.packed) size = `<span title="${esc(t.size_source)}">${UI.status("ok", "ארוז")}</span>`;
  else if (t.size_kb != null) size = `<span title="${esc(t.size_source)}">${esc(toolsSizeText(t.size_kb))}</span>`;
  else size = `<span class="muted" title="${esc(t.size_source)}">לא נמדד</span>`;
  const rec = t.recommended ? ` <span class="pill info" title="בשורת 'ההמלצה שלי' של הקבוצה">מומלץ</span>` : "";
  const moved = t.moved_from ? `<span class="sub">מקבוצת "${esc(t.moved_from)}" במסמך (הכרעת נדב 17/09)</span>` : "";
  return { attrs: `data-tool="${esc(t.id)}"`, cells: [
    box("build", "בנייה/שיכפול"), box("student", "לתלמיד (v2)"),
    `<span class="name">${esc(t.title_he)}${rec}</span><span class="sub">${esc(t.what)}</span>${moved}`,
    `<span class="mono">${esc(t.binary)}</span>`, risk, size] };
}
function toolsGroupsHtml() {
  const cols = [{ html: `<span title="ייארז ל-initrd של מחשבי הבנייה והשיכפול">בנייה/שיכפול</span>` },
                { html: `<span title="מחשב תלמיד = v2; הבחירה נשמרת כבר עכשיו">לתלמיד (v2)</span>` }, "כלי", "בינארי", "סיכון", "גודל"];
  return TOOLS.groups.map((g, i) => {
    const rows = TOOLS.tools.filter((t) => t.group === g && toolsVisible(t)).map(toolRowHtml);
    const table = UI.datagrid({ cls: "tools", columns: cols, rows, empty: "אין כלים בקבוצה הזו שמתאימים לסינון" });
    const acts = `<button class="btn sm" onclick="toolsGroupMark(${i},'recommended')">סמן את המומלצים</button><button class="btn sm" onclick="toolsGroupMark(${i},'clear')">נקה</button>`;
    return `<div class="c12 card"><div class="card-h"><span>${esc(g)} <small id="tools-grp-${i}">${esc(toolsGroupCaption(g))}</small></span><div class="acts">${acts}</div></div><div class="card-b${rows.length ? " flush" : ""}">${table}</div></div>`;
  }).join("");
}
function toolsBarHtml() {
  const f = TOOLS_FILTER, opt = (v, l, cur) => `<option value="${v}"${v === cur ? " selected" : ""}>${esc(l)}</option>`;
  const risks = [["", "כל הסיכונים"], ["ro", "קריאה בלבד (ro)"], ["rw", "משנה (rw)"], ["destroy", "מוחק (destroy)"]];
  const scopes = [["build", "בנייה/שיכפול"], ["student", "תלמיד"], ["both", "שניהם"]];
  return `<div class="dg-bar"><input type="search" value="${esc(f.q)}" placeholder="חיפוש: שם, מה זה עושה, בינארי…" aria-label="חיפוש כלי" oninput="toolsFilter('q',this.value)" style="width:240px"><select aria-label="סיכון" onchange="toolsFilter('risk',this.value)">${risks.map(([v, l]) => opt(v, l, f.risk)).join("")}</select><label class="chk"><input type="checkbox"${f.rec ? " checked" : ""} onchange="toolsFilter('rec',this.checked)"> רק מומלצים</label><label class="chk"><input type="checkbox"${f.packed ? " checked" : ""} onchange="toolsFilter('packed',this.checked)"> רק ארוזים</label><span class="sp"></span><label class="chk">סמן/נקה קבוצה עבור: <select aria-label="היעד של פעולות הקבוצה" onchange="toolsFilter('scope',this.value)">${scopes.map(([v, l]) => opt(v, l, f.scope)).join("")}</select></label><span class="n" id="tools-count">${esc(toolsCountText())}</span></div>`;
}
function toolsPage() {
  if (!TOOLS && !toolsError) return pagePlaceholder();
  const dirty = toolsDirty();
  const sub = TOOLS
    ? esc(`${TOOLS.tools.length} כלים ב-${TOOLS.groups.length} קבוצות לפי מצב שימוש · מה שמסומן ייארז ל-initrd (שלב 2) · תלמיד = v2, הבחירה נשמרת כבר עכשיו`)
    : `‏/tools/catalog לא נקרא: ${esc(toolsError)}`;
  const pill = TOOLS ? `<span class="pill info" id="tools-pill">${esc(toolsPillText())}</span><span id="tools-state">${dirty ? UI.pill("warn", "שינויים לא נשמרו") : ""}</span>` : UI.pill("err", "לא נקרא");
  const actions = (TOOLS ? `<button class="btn primary" id="tools-save" onclick="toolsSave()"${dirty ? "" : " disabled"}>שמור</button><button class="btn" id="tools-cancel" onclick="toolsCancel()"${dirty ? "" : " hidden"}>בטל שינויים</button>` : "")
    + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "ארגז כלים" }],
    icon: "tools", name: "ארגז כלים", sub, pill, actions });
  let body;
  if (!TOOLS) body = UI.note("err", `לא הצלחתי לקרוא את קטלוג הכלים: ${esc(toolsError)}`);
  else body = `<div class="c12 card">${toolsBarHtml()}</div><div id="tools-groups" style="display:contents">${toolsGroupsHtml()}</div>`
    + `<div class="c12">${UI.note("info", `${UI.pill("", "קריאה")} מציג ולא משנה · ${UI.pill("warn", "משנה")} שינוי הפיך · ${UI.pill("err", "מוחק")} מוחק נתונים — יופעל רק אחרי חלון אישור. ${UI.status("ok", "ארוז")} = הבינארי כבר ב-initrd היום (תוספת 0). הגדלים הם גודל חבילה מותקנת <b>עם המקור ב-tooltip</b> — לא תוספת נמדדת ל-initrd הדחוס; "לא נמדד" נספר בנפרד ואינו מקופל ל-0.`)}</div>`;
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}

const pages = {
  settings: { crumb: "הגדרות", title: "הגדרות", tabs: [], render: settings, load: loadSettingsData, own: true },   // ‏#954 גל 6
  // ‏#954 גל 7: כותרת אובייקט + datagrid (UI.*, כמו drivers.js) — שרתים/העברות/קבוצות.
  branches: { crumb: "שרתי משנה", title: "שרתי משנה", tabs: ["שרתים", "העברות", "קבוצות"], render: (i) => branchesPage(i), load: () => loadBranchesData(), own: true },
  // ‏#936: הדף של שרת משנה אחד (נבחר בעץ, BRANCH_NODE) — אותם נתונים ואותן
  // פונקציות של "שרתי משנה" (branches.js). ‏#1179: בלי לשוניות — העמוד הוא העלה
  // שנבחר בעץ של שרת המשנה (BRANCH_VIEW), כמו שכל עמוד של הראשי הוא עלה בעץ שלו.
  branch: {crumb:"שרת משנה", title:"שרת משנה", desc:"אותו עץ כמו הראשי, מול הנתונים של שרת המשנה — כפי שהראשי מדד מולו", tabs: [], render:pagePlaceholder},
  // ‏#954: הדף מצייר את הכותרת והלשוניות שלו (own) לפי שפת העיצוב החדשה; הלשוניות בפועל לפי תפקיד (homeTabs).
  home: { crumb: "סקירה כללית", title: "סקירה כללית", tabs: ["סיכום", "משימות", "אירועים"], render: home, load: loadHome, own: true },
  images: { crumb: "אימג'ים", title: "ספריית אימג'ים", tabs: imagesTabs(), render: images, load: loadImages, own: true },
  deploy: { crumb: "סבב הפצה", title: "סבב הפצה", tabs: deployTabs(), render: deploy, load: loadDeploy, own: true },
  machines: { crumb: "מחשבים", title: "מחשבים", tabs: ["כל המחשבים", "נראו ברשת", "דיסקים אדומים"], render: machines, load: loadMachines, own: true },
  health: { crumb: "בריאות ושירותים", title: "בריאות ושירותים", tabs: [], render: health, load: loadHealth, own: true },   // ‏#954 גל 5: בלי לשוניות — בדיקות + עדכון
  // ‏#954 גל 8: רשת כאובייקט — תרשים · חיבורים פיזיים · רשת הפצה · פורטים (=pages.ports). deploy: לא מוצג (pageAllowed).
  network: { crumb: "רשת", title: "רשת", tabs: NET_TABS, render: (i) => networkPage(i), load: loadNetwork, own: true },
  account: { crumb: "החשבון שלי", title: "החשבון שלי", tabs: [], render: account, load: async () => {}, own: true },
  permissions: { crumb: "משתמשים והרשאות", title: "משתמשים והרשאות", tabs: [], render: permissions, load: loadUsersData, own: true },   // ‏#954 גל 6: טבלה אחת + מטריצה
  logs: { crumb: "יומן", title: "יומן", tabs: [], render: logs, load: loadJournalData, own: true },   // ‏#954 גל 6: יומן אחד עם סינון בדף
  monitor: { crumb: "מוניטור", title: "מוניטור", tabs: [], render: monitorPage, load: loadMonitor, own: true },   // ‏#954 גל 6: הרשימה כטבלה
  drivers: { crumb: "דרייברים", title: "דרייברים", tabs: ["דגמים", "חבילות", "כיסוי לפי מכונה"], render: (i) => driversPage(i), load: () => loadDrivers(), own: true },   // ‏#954 גל 6; lazily: drivers.js loads after this file
  tools: { crumb: "ארגז כלים", title: "ארגז כלים", tabs: [], render: toolsPage, load: loadTools, own: true },   // ‏#649 שלב 1: קבוצה = כרטיס, ☐ בנייה/שיכפול | ☐ תלמיד
  storage: { crumb: "אחסון", title: "אחסון", tabs: [], render: () => storagePage(), load: () => loadStorage(), own: true },   // ‏#1066 שלב ב': מיקומי הספרייה (storage.js, נטען אחרי הקובץ הזה)
  ports: { crumb: "פורטים", title: "פורטים", tabs: ["חיבורים פיזיים", "רשת הפצה", "פורטים"], render: ports, load: loadPorts, own: true },   // ‏#954 גל 5: מתג בכל שורה
};
let current = "home";
let currentTab = 0;
let searchQuery = "";

function tabRender(pageId, index) {
  const renderers = {
    branch: [() => `<div id="branch-view" class="stack" data-view="${esc(BRANCH_VIEW)}">${pagePlaceholder()}</div>`],
  };
  const fn = renderers[pageId]?.[index];
  return fn ? fn() : `<div class="card"><div class="card-b">אין תוכן עבור הכרטיס הזה.</div></div>`;
}

/* ‏#1179: בהקשר שרת משנה העמוד מצויר מתשובה של שרת אחר. תשובה בצורה שהעמוד אינו
   מכיר (אובייקט במקום רשימה) לא תשבור את הקונסולה — העמוד אומר "לא נקרא" והסיבה. */
function layout(page, index = 0) {
  if (!SERVER_CTX) return layoutOf(page, index);
  try { return layoutOf(page, index); }
  catch (e) {
    return `<div class="page"><div class="body">${UI.note("err", `לא הצלחתי לצייר את העמוד מהתשובה של ${esc(serverRootName("שרת המשנה"))} — לא נקרא: ${esc(e && e.message)}`)}</div></div>`;
  }
}
function layoutOf(page, index) {
  if (page.own) return page.render(index);   // ‏#954: עמוד שנבנה מחדש מצייר כותרת+לשוניות בעצמו
  const stale = overviewError && ["home","deploy"].includes(current) ? `<div class="notice warn" role="status">${esc(overviewError)}</div>` : "";
  return `<div class="breadcrumbs"><span>${esc(serverRootName("שרת אימג'ים"))}</span><span class="sep">/</span><strong>${page.crumb}</strong></div><div class="toolbar"><div class="title-block"><h1>${page.title}</h1><p>${page.desc}</p></div><div class="toolbar-actions"><button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>${PAGE_ACTIONS[current] ? `<div class="rel"><button class="btn primary" id="pageActionBtn" aria-haspopup="menu" aria-expanded="false" onclick="openAction()">+ פעולה</button><div id="pageActionMenu" class="action-menu" role="menu"></div></div>` : ""}</div></div><div class="vcenter-tabs" role="tablist">${page.tabs.map((t, i) => `<button type="button" class="vcenter-tab ${i === index ? "active" : ""}" role="tab" aria-selected="${i === index}" tabindex="${i === index ? 0 : -1}" onclick="activateTab(${i})">${t}</button>`).join("")}</div><div class="scroll">${stale}${tabRender(current, index)}<div class="footer-note">ImageSrv Console</div></div>`;
}

/* S1 (#829): הצומת של הדף הנוכחי מודגש בעץ. הצמתים הם .inventory-node
   עם data-page; הורים סגורים נפתחים כדי שההדגשה תיראה. */
function markTreeSelection(el, id) {
  document.querySelectorAll(".inventory-node.active, .inventory-node[aria-current], .inventory-node.anc").forEach((x) => {
    x.classList.remove("active", "anc");
    x.removeAttribute("aria-current");
  });
  const node = el || document.querySelector(`.inventory-node[data-page="${id}"]`);
  if (!node) { drawRail(); return; }
  node.classList.add("active");
  node.setAttribute("aria-current", "page");
  for (let box = node.parentElement; box && box.classList; box = box.parentElement) {
    if (!box.classList.contains("inventory-children")) continue;
    if (box.hasAttribute("hidden") || box.style.display === "none") {
      const owner = box.previousElementSibling;
      toggleInventoryGroup(owner && owner.classList.contains("inventory-node") ? owner : null, box.id);
    }
  }
  markTreeAncestor(node);
  drawRail();   // ‏#1300: הפס עוקב אחרי אותו סימון
}

/* apple-pending-4 · 2: הלשונית היא המקור והעץ עוקב אחריה. הצומת של הלשונית מסומן במלואו
   (רקע + פס בצד); ‏#1300 (console-full.html hasCur): כל צומת-אב שלו — "רשת", "תשתית", "ניהול", "מחשבים"
   מעל "מחשבי בנייה" — מקבל סימון רך (נקודה + צבע), כך שרואים גם "איפה אני" וגם "באיזה אזור".
   שורת השרת אינה צומת-אב במוקאפ (root) ואינה מסומנת. */
function markTreeAncestor(node) {
  for (let box = node.parentElement; box && box.classList; box = box.parentElement) {
    if (!box.classList.contains("inventory-children")) continue;
    const owner = box.previousElementSibling;
    if (owner && owner !== node && owner.classList && owner.classList.contains("inventory-node") && !owner.classList.contains("server-node")) owner.classList.add("anc");
  }
}

/* מזהה הצומת של הלשונית הנוכחית. התרשים (0) = צומת "תרשים" (כמו במוקאפ); פורטים (3 / דף ports) = צומת הפורטים. */
function treeNodeForTab() {
  const q = (sel) => document.querySelector(sel);
  if (current === "ports") return q('.inventory-node[data-page="ports"]');
  if (current !== "network") return null;
  if (currentTab === 1) return q('.inventory-node[data-net-tab="1"]');   // ‏#1300: עלה, כמו במוקאפ (בלי תת-עץ כרטיסים)
  if (currentTab === 2) return q('.inventory-node[data-net-tab="2"]');
  if (currentTab === 3) return q('.inventory-node[data-page="ports"]');
  return q('.inventory-node[data-net-tab="0"]') || q('.inventory-node[data-page="network"]');
}
function syncTreeToTab() {
  const node = treeNodeForTab();
  if (node) markTreeSelection(node, current);
}

function selectPage(el, id) {
  // ‏#1179: צומת של שרת משנה (data-branch-node / שורת השרת) נכנס להקשר שלו. היציאה היא
  // לחיצה על צומת של הראשי (מאזין ה-capture), "העברות" (openBranchView) או עמוד שהוא
  // של הראשי בלבד — לא צומת שפונקציה מתוך העמוד מעבירה (openNetwork, selectMachinesRole
  // מוצאות את הצומת של הראשי); בהקשר שרת משנה הוא מוחלף בצומת של שרת המשנה.
  const ownNode = (x) => !!(x && ((x.dataset && (x.dataset.branchNode || x.dataset.secondary))
    || (x.closest && x.closest("#secondaryServers"))));
  if (el && el.dataset && !el.dataset.branchView) {
    const nid = el.dataset.branchNode || el.dataset.secondary;
    if (nid) setServerContext(nid, secondaryLabel(nid));
  }
  if (SERVER_CTX && PRIMARY_ONLY_PAGES.includes(id)) setServerContext(null);
  if (SERVER_CTX && !ownNode(el)) {
    el = document.querySelector(`[data-branch-node="${CSS.escape(SERVER_CTX)}"][data-branch-page="${id}"]`)
      || document.querySelector(`[data-secondary="${CSS.escape(SERVER_CTX)}"]`);
  }
  if (!pageAllowed(id)) return;
  markTreeSelection(el, id);
  const page = pages[id];
  if (!page) return;
  if (current === "vmupdate" && id !== "vmupdate" && typeof vmuDisconnect === "function") vmuDisconnect();   // ‏#1334
  current = id;
  currentTab = 0;
  document.getElementById("content").innerHTML = layout(page, 0);
  if (typeof vmuGlobal === "function") vmuGlobal();
  const search = document.getElementById("globalSearch");
  if (search) search.value = searchQuery || "";
  if (id === "network") wireNetPage();
  wireRestoredPage();
  if (page.load) page.load();
  closeSidebar();
}

/* עמודים של הראשי בלבד (של המשתמש או של ניהול הענפים) — פותחים אותם מחוץ להקשר שרת משנה. */
const PRIMARY_ONLY_PAGES = ["account", "branches", "branch", "tools"];
function selectPageById(id) {
  // ‏#1179: בהקשר שרת משנה — העלה של שרת המשנה לאותו עמוד, אם יש; אחרת בלי צומת (ההקשר נשאר).
  selectPage(SERVER_CTX
    ? document.querySelector(`[data-branch-node="${CSS.escape(SERVER_CTX)}"][data-branch-page="${id}"]`)
    : document.querySelector(`.inventory-node[data-page="${id}"]`), id);
}

function selectInventory(el, id) {
  if (!pageAllowed(id)) return;
  document.querySelectorAll(".inventory-node").forEach((x) => x.classList.remove("active"));
  if (el) el.classList.add("active");
  const page = pages[id];
  if (!page) return;
  current = id;
  currentTab = 0;
  document.getElementById("content").innerHTML = layout(page, 0);
  const search = document.getElementById("globalSearch");
  if (search) search.value = searchQuery || "";
  if (id === "network") wireNetPage();
  wireRestoredPage();
  if (page.load) page.load();
  closeSidebar();   // ‏#1300: בחירת עמוד מהעץ (גם צומת-משנה) נכנסת לעמוד — השכבה בחלון צר נסגרת
}

/* הכרעת נדב: סגירת ענף סוגרת גם כל תת-ענף שבתוכו, כך שפתיחה מחדש מציגה רק את הרמה הראשונה סגורה.
   המצב נקרא מה-DOM (sidebarChildOpen), ולכן גם בנייה מחדש של העץ שומרת את אותו כלל. */
function collapseInventoryDescendants(box) {
  if (!box || !box.querySelectorAll) return;
  box.querySelectorAll(".inventory-children").forEach((inner) => {
    inner.setAttribute("hidden", "");
    const owner = inner.previousElementSibling;
    if (!owner || !owner.classList || !owner.classList.contains("inventory-node")) return;
    owner.setAttribute("aria-expanded", "false");
    const a = owner.querySelector(".tree-arrow");
    if (a) { a.dataset.open = "false"; a.textContent = "▸"; }
  });
}
function toggleInventoryGroup(nodeEl, childId) {
  const box = childId ? document.getElementById(childId) : null;
  const arrow = nodeEl && nodeEl.querySelector(".tree-arrow");
  const opening = box
    ? (box.hasAttribute("hidden") || box.style.display === "none")
    : !!(arrow && (arrow.textContent === "▸" || arrow.textContent === "›"));
  if (box) {
    if (opening) {
      box.removeAttribute("hidden");
      box.style.removeProperty("display");
    } else {
      box.setAttribute("hidden", "");
      box.style.removeProperty("display");
      collapseInventoryDescendants(box);
    }
  }
  if (arrow) arrow.dataset.open = String(opening);
  if (arrow) arrow.textContent = opening ? "▾" : "▸";
  if (nodeEl && nodeEl.setAttribute) nodeEl.setAttribute("aria-expanded", String(opening));
}

/* ‏#1300 (נדב 12/10 — "בדיוק כמו המוקאפ", console-full.html ‏#tree click): ענף שהוא גם דף (מחשבים, רשת)
   — לחיצה כשהדף שלו אינו הדף הנוכחי = ניווט + פתיחה; כשכבר בו (הצומת מודגש, או "רשת" כשאחת הלשוניות
   שלו פתוחה) = פתיחה/סגירה. החץ — תמיד פתיחה/סגירה (stopPropagation ב-index.html). ענף בלי דף (מלאי,
   תשתית, ניהול) — toggleInventoryGroup; עלה — ניווט. הסימון הדביק ‏.here שהיה כאן נמחק: הוא נשאר על הצומת
   אחרי שעברנו לדף אחר, ואז לחיצה על "מחשבים" רק פתחה/סגרה ולא נכנסה לדף (הבאג שנדב ראה). */
function treeNavToggle(node, boxId, nav) {
  // "כבר בו" = הצומת הוא הדף הנוכחי; ו"רשת" גם כשאחת הלשוניות שלו פתוחה — כמו המקרה המפורש של
  // המוקאפ (n.id === "network" && S.page === "network"). ‏"מחשבים" מעל "מחשבי בנייה" מסומן רך (anc) אבל
  // אינו "בו" — במוקאפ "מחשבי בנייה" הוא דף אחר, ולחיצה על "מחשבים" נכנסת לכל המחשבים.
  const page = node.dataset && node.dataset.page;
  const onIt = hasClass(node, "active") || (hasClass(node, "anc") && page === "network" && current === "network");
  if (onIt) { toggleInventoryGroup(node, boxId); return; }
  nav.call(node);   // כך ש-this בתוך ה-function הוא השורה (openBranchView מסמן אותה)
  const box = document.getElementById(boxId);
  if (box && (box.hasAttribute("hidden") || box.style.display === "none")) toggleInventoryGroup(node, boxId);
}

/* S4 (#829): ניווט מקלדת. הלשוניות: חצים (RTL — ימינה=הקודמת, שמאלה=הבאה),
   Home/End. העץ: Enter/Space מפעילים את מה שהעכבר היה מפעיל, חץ למטה/למעלה
   בין צמתים גלויים, שמאלה פותח קבוצה, ימינה סוגר או עולה להורה. */
function tablistKeydown(e) {
  const tab = e.target.closest && e.target.closest('[role="tab"]');
  if (!tab || !tab.parentElement) return;
  const tabs = [...tab.parentElement.querySelectorAll('[role="tab"]')];
  const i = tabs.indexOf(tab);
  const next = {ArrowRight: i - 1, ArrowLeft: i + 1, Home: 0, End: tabs.length - 1}[e.key];
  if (next === undefined || i < 0) return;
  e.preventDefault();
  const target = (next + tabs.length) % tabs.length;
  activateTab(target);
  const focusTo = document.querySelectorAll('[role="tab"]')[target];
  if (focusTo && focusTo.focus) focusTo.focus();
}

function treeVisibleNodes(tree) {
  return [...tree.querySelectorAll('.inventory-node[tabindex]')].filter((n) => {
    for (let p = n.parentElement; p && p !== tree; p = p.parentElement) {
      if (p.hasAttribute("hidden") || (p.classList && p.classList.contains("hidden"))) return false;
    }
    return !n.classList.contains("hidden");
  });
}

function treeKeydown(e) {
  const node = e.target.closest && e.target.closest(".inventory-node");
  if (!node) return;
  const tree = e.currentTarget;
  const arrow = node.querySelector(".tree-arrow");
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    const clickable = node.getAttribute("onclick") ? node
      : (node.querySelector("span[onclick]:not(.tree-arrow)") || arrow || node);
    clickable.click();
    return;
  }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const nodes = treeVisibleNodes(tree);
    const to = nodes[nodes.indexOf(node) + (e.key === "ArrowDown" ? 1 : -1)];
    if (to) to.focus();
    return;
  }
  if (e.key === "ArrowLeft" && arrow && node.getAttribute("aria-expanded") === "false") {
    e.preventDefault();
    arrow.click();
    return;
  }
  if (e.key === "ArrowRight") {
    e.preventDefault();
    if (arrow && node.getAttribute("aria-expanded") === "true") { arrow.click(); return; }
    const box = node.parentElement && node.parentElement.classList.contains("inventory-children") ? node.parentElement : null;
    const owner = box && box.previousElementSibling;
    if (owner && owner.classList.contains("inventory-node") && owner.focus) owner.focus();
  }
}

/* ‏#1300 (נדב 13/10, אושר — C:\ImageCtl-Lab\design-approved\rail\rail.html): ☰ מחליף בין העץ המלא לפס
   אייקונים (264↔56px; מעבר רוחב של עמודת הגריד ב-CSS — לחיצה שנייה באמצע מתהפכת מהערך הנוכחי). הפס
   שטוח כמו ESXi: הקבוצות בלי דף (מלאי / תשתית / ניהול) הופכות למפרידים, וכל דף הוא אייקון. לחיצה = ניווט,
   תמיד; tooltip עם השם; תפריט צף (ריחוף 250ms או ←) רק לצמתים עם רמה שלישית — מחשבים ורשת. בתת-עמוד
   הסימון על ההורה הנראה ("רשת, נבחר: פורטים", aria-current=location). עד 900px הפס הוא ברירת המחדל, ו-☰
   פותח את העץ המלא כמגירה עם מסך מעומעם (Esc / לחיצה על הרקע סוגרים, והפוקוס חוזר ל-☰). הבחירה נשמרת
   רק במסך רחב (localStorage — נוחות בלבד). הפס נבנה מהעץ שב-index.html עצמו: אותם צמתים, אייקונים,
   הרשאות (hidden) ופעולות — אין רשימת ניווט שנייה שתיסחף. */
const NAV_KEY = "imagectl.nav";
let NAV_PREF = "full", NAV_MQ = null;
function navIsNarrow() { return window.matchMedia("(max-width:900px)").matches; }   // ‏#1300: כמו המוקאפ
function navStored() { try { return localStorage.getItem(NAV_KEY); } catch (e) { return null; } }
function navStore(v) { try { localStorage.setItem(NAV_KEY, v); } catch (e) { /* העדפה בלבד */ } }

/* המגירה (מסך צר בלבד): .open על הסיידבר + המסך המעומעם. במסך רחב .open אינו משנה דבר. */
function toggleSidebar(force) {
  const side = document.getElementById("sidebar");
  if (!side) return;
  const wasOpen = hasClass(side, "open");   // ‏#1300: נקרא גם מ-openDrawer — בלי להניח DOM מלא
  const open = force === undefined ? !wasOpen : !!force;
  if (open === wasOpen) return;
  side.classList.toggle("open", open);
  const backdrop = document.getElementById("sidebarBackdrop");
  if (backdrop) backdrop.classList.toggle("open", open);
  navSyncButton();
  const btn = document.querySelector(".nav-toggle");
  if (open) {
    const first = side.querySelector(".inventory-node.active") || side.querySelector(".inventory-node");
    if (first && first.focus) first.focus();
  } else if (btn && btn.focus && document.activeElement && side.contains && side.contains(document.activeElement)) {
    btn.focus();  // רק כשהמגירה באמת הייתה פתוחה והפוקוס בתוכה
  }
}
function closeSidebar() { toggleSidebar(false); }
/* לחיצה על הרקע המעומעם: סוגרת ומחזירה את הפוקוס ל-☰ (המוקאפ: closeDrawer) */
function closeSidebarFromScrim() {
  closeSidebar();
  const btn = document.querySelector(".nav-toggle");
  if (btn && btn.focus) btn.focus();
}

/* ☰: מצב, תווית ו-aria-expanded — "פתוח" = העץ המלא נראה (במסך צר: המגירה פתוחה). */
function navSyncButton() {
  const narrow = navIsNarrow(), side = document.getElementById("sidebar");
  const app = document.getElementById("app");
  const mode = app && app.getAttribute("data-nav") === "rail" ? "rail" : "full";
  const expanded = narrow ? hasClass(side, "open") : mode === "full";
  const label = narrow ? (expanded ? "סגירת העץ המלא" : "פתיחת העץ המלא") : (mode === "full" ? "כיווץ לפס אייקונים" : "הצגת העץ המלא");
  const btn = document.querySelector(".nav-toggle");
  if (!btn) return;
  btn.setAttribute("aria-expanded", String(expanded));
  btn.setAttribute("aria-label", label);
  btn.setAttribute("title", label);
}
function applyNavMode(remember) {
  const app = document.getElementById("app");
  if (!app) return;
  const narrow = navIsNarrow();
  app.classList.toggle("narrow", narrow);
  if (!narrow) closeSidebar();
  app.setAttribute("data-nav", narrow ? "rail" : NAV_PREF);
  navSyncButton();
  if (remember && !narrow) navStore(NAV_PREF);
  closeNavFly(); hideNavTip();
}
function navButton() {
  if (navIsNarrow()) { toggleSidebar(); return; }
  const side = document.getElementById("sidebar");
  const hadFocus = !!(side && side.contains && document.activeElement && side.contains(document.activeElement));
  NAV_PREF = NAV_PREF === "full" ? "rail" : "full";
  applyNavMode(true);
  // פוקוס שהיה בעמודה עובר לפריט המקביל בצורה החדשה — לא נשאר על אלמנט שנעלם
  if (hadFocus) {
    const t = NAV_PREF === "rail" ? document.querySelector('#rail .ri[tabindex="0"]')
      : (document.querySelector(".sidebar .inventory-node.active") || document.querySelector(".sidebar .inventory-node"));
    if (t && t.focus) t.focus();
  }
}
function restoreNav() {
  NAV_PREF = navStored() === "rail" ? "rail" : "full";
  applyNavMode(false);
  if (!NAV_MQ) {
    NAV_MQ = window.matchMedia("(max-width:900px)");
    if (NAV_MQ && NAV_MQ.addEventListener) NAV_MQ.addEventListener("change", () => applyNavMode(false));
  }
}

/* ---- הפס: נגזר מהעץ ---- */
const RAIL_KIDS = { machines: "machineTree", network: "netTA" };   // רמה שלישית — לתפריט הצף
let RAIL_NODES = [];
const railShown = (el) => !!el && !hasClass(el, "hidden");
function railLabel(node) {
  const spans = [...(node.children || [])].filter((c) => c.tagName === "SPAN" || c.tagName === "STRONG");
  const last = spans[spans.length - 1];
  return last ? String(last.textContent || "").trim() : "";
}
function railIcon(node) {
  const svg = node.querySelector && node.querySelector("svg");
  return svg && svg.outerHTML ? svg.outerHTML : uiIcon("list");
}
function railKids(node) {
  const box = RAIL_KIDS[node.dataset && node.dataset.page] && document.getElementById(RAIL_KIDS[node.dataset.page]);
  if (!box || !railShown(box)) return [];
  return [...box.children].filter((c) => hasClass(c, "inventory-node") && railShown(c));
}
function railWithin(box, el) { for (let x = el; x; x = x.parentElement) if (x === box) return true; return false; }
/* קבוצות הפס: [ראשי: סקירה, סבב] · [מלאי: …] · [תשתית: …] · [ניהול: …] — הצמתים שיש להם דף, מתוך השרת הראשי */
function railModel() {
  const srv = document.getElementById("srvTA");
  if (!srv) return [];
  const out = [];
  let top = null;
  for (const el of srv.children || []) {
    if (!hasClass(el, "inventory-node") || !railShown(el)) continue;
    if (el.dataset && el.dataset.page) {
      if (!top) { top = { label: "ראשי", items: [] }; out.push(top); }
      top.items.push(el);
    } else if (hasClass(el, "group")) {
      top = null;
      const box = el.nextElementSibling;
      const items = box && railShown(box) ? [...box.children].filter((c) => hasClass(c, "inventory-node") && railShown(c) && c.dataset && c.dataset.page) : [];
      if (items.length) out.push({ label: railLabel(el), items });
    }
  }
  return out;
}
function railActive() { const srv = document.getElementById("srvTA"); return srv && srv.querySelector ? srv.querySelector(".inventory-node.active") : null; }
function railOwner(node, act) {
  if (!act) return false;
  if (node === act) return true;
  const box = RAIL_KIDS[node.dataset.page] && document.getElementById(RAIL_KIDS[node.dataset.page]);
  return !!box && railWithin(box, act);
}
function drawRail() {
  const rail = document.getElementById("rail");
  if (!rail || typeof rail.querySelector !== "function") return;   // בלי DOM מלא (טסטים)
  const act = railActive(), model = railModel();
  RAIL_NODES = [];
  // תחנת Tab אחת לפס (roving tabindex): האייקון המסומן, ואם אין — הראשון; חצים בתוכו
  const all = model.flatMap((g) => g.items), stop = Math.max(0, all.findIndex((n) => railOwner(n, act)));
  const groups = model.map((g) => `<div class="rgrp" role="group" aria-label="${esc(g.label)}">` + g.items.map((n) => {
    const i = RAIL_NODES.push(n) - 1, kids = railKids(n).length > 0;
    const own = railOwner(n, act), deep = own && act !== n, label = railLabel(n);
    const name = deep ? `${label}, נבחר: ${railLabel(act)}` : label;
    return `<button type="button" class="ri${own ? " cur" : ""}${kids ? " kids" : ""}" data-ri="${i}" tabindex="${i === stop ? 0 : -1}" aria-label="${esc(name)}"`
      + `${own ? (deep ? ' aria-current="location"' : ' aria-current="page"') : ""}${kids ? ' aria-haspopup="menu" aria-expanded="false" aria-controls="navFly"' : ""}>${railIcon(n)}</button>`;
  }).join("") + "</div>");
  const { cls: fcls, text: ftxt } = serverDotState(SERVER_HEALTH[""]);   // נדב 18/10: הנקודה של הראשי (הקופסה בתחתית העץ יצאה)
  rail.innerHTML = groups.join(`<div class="rsep" aria-hidden="true"></div>`)
    + `<div class="rfoot"><span class="rled" role="img" aria-label="${esc(ftxt)}" data-tipx="${esc(ftxt)}"><span class="led ${esc(fcls)}"></span></span></div>`;
}
function railMove(root, sel, from, delta) {
  const items = [...root.querySelectorAll(sel)];
  if (!items.length) return null;
  let i = items.indexOf(from);
  i = delta === "home" ? 0 : delta === "end" ? items.length - 1 : (i + delta + items.length) % items.length;
  items.forEach((b) => { b.tabIndex = -1; });
  items[i].tabIndex = 0; items[i].focus();
  return items[i];
}
const railNode = (btn) => RAIL_NODES[Number(btn && btn.dataset && btn.dataset.ri)];
/* לחיצה על אייקון = ניווט, תמיד (כמו ESXi) — לדף של הצומת, כמו הלחיצה עליו בעץ כשלא נמצאים בו */
function railGo(node) {
  const page = node.dataset.page;
  if (page === "machines") openMachinesPage();
  else if (page === "network") openNetwork(0);
  else node.click();
}
function railFocusNode(node) {
  const rail = document.getElementById("rail"), i = RAIL_NODES.indexOf(node);
  const b = i >= 0 && rail && rail.querySelector ? rail.querySelector(`.ri[data-ri="${i}"]`) : null;
  if (b && b.focus) { [...rail.querySelectorAll(".ri")].forEach((x) => { x.tabIndex = x === b ? 0 : -1; }); b.focus({ preventScroll: true }); }
  return b;
}

/* ---- tooltip: השהיה ראשונה 350ms, ומעבר בין אייקונים מיידי ("חימום"); מיד בפוקוס מקלדת ---- */
let NAV_TIP_T = 0, NAV_TIP_WARM = 0;
function showNavTip(el, now) {
  const tip = document.getElementById("navTip"), rail = document.getElementById("rail");
  if (!tip || !rail) return;
  clearTimeout(NAV_TIP_T);
  const run = () => {
    const n = railNode(el), act = railActive();
    let html = n ? esc(railLabel(n)) : esc(el.dataset.tipx || "");
    if (n && hasClass(el, "cur") && act && act !== n) html += `<small>› ${esc(railLabel(act))}</small>`;
    if (n && hasClass(el, "kids") && el.matches && el.matches(":focus-visible")) html += `<kbd>← תת-עמודים</kbd>`;
    tip.innerHTML = html;
    const r = el.getBoundingClientRect();
    tip.style.right = (window.innerWidth - rail.getBoundingClientRect().left + 6) + "px";
    tip.classList.add("show");
    tip.style.top = (r.top + r.height / 2 - tip.offsetHeight / 2) + "px";
    NAV_TIP_WARM = Date.now();
  };
  (now || Date.now() - NAV_TIP_WARM < 600) ? run() : (NAV_TIP_T = setTimeout(run, 350));
}
function hideNavTip() {
  clearTimeout(NAV_TIP_T);
  const tip = document.getElementById("navTip");
  if (!tip || !tip.classList) return;
  if (hasClass(tip, "show")) NAV_TIP_WARM = Date.now();
  tip.classList.remove("show");
}

/* ---- תפריט צף: תוספת לצמתים עם ילדים בלבד; הלחיצה על האייקון עצמו תמיד מנווטת ---- */
let NAV_FLY_FOR = null, NAV_FLY_OPEN_T = 0, NAV_FLY_CLOSE_T = 0;
function openNavFly(el, focusFirst) {
  const fly = document.getElementById("navFly"), rail = document.getElementById("rail");
  const n = railNode(el);
  if (!fly || !rail || !n) return;
  clearTimeout(NAV_FLY_OPEN_T); clearTimeout(NAV_FLY_CLOSE_T); hideNavTip();
  if (NAV_FLY_FOR && NAV_FLY_FOR !== el) NAV_FLY_FOR.setAttribute("aria-expanded", "false");
  NAV_FLY_FOR = el; el.setAttribute("aria-expanded", "true");
  const label = railLabel(n);
  fly.setAttribute("aria-label", label);
  const item = (k, i, parent) => {
    const here = !parent && hasClass(k, "active");
    return `<button type="button" class="fi${parent ? " parent" : ""}" role="menuitem" tabindex="-1" data-fi="${i}"${here ? ' aria-current="page"' : ""}>${railIcon(k)}<span>${esc(parent ? `כל ה${label}` : railLabel(k))}</span>${here ? `<span class="ck">כאן</span>` : ""}</button>`;
  };
  const kids = railKids(n);
  NAV_FLY_ITEMS = (n.dataset.page === "network" ? [] : [n]).concat(kids);
  const parentRow = n.dataset.page === "network" ? "" : item(n, 0, true) + "<hr>";
  const off = parentRow ? 1 : 0;
  fly.innerHTML = `<div class="fh" aria-hidden="true">${esc(label)}</div>${parentRow}${kids.map((k, i) => item(k, i + off)).join("")}`;
  const r = el.getBoundingClientRect();
  fly.style.right = (window.innerWidth - rail.getBoundingClientRect().left + 6) + "px";
  fly.classList.add("show");
  const top = Math.max(56, Math.min(r.top - 6, window.innerHeight - fly.offsetHeight - 42));
  fly.style.top = top + "px";
  fly.style.transformOrigin = `right ${r.top + r.height / 2 - top}px`;
  if (focusFirst) setTimeout(() => {
    const f = fly.querySelector(".fi[aria-current]") || fly.querySelector(".fi");
    if (f) { f.tabIndex = 0; f.focus(); }
  }, 10);
}
let NAV_FLY_ITEMS = [];
function closeNavFly(returnFocus) {
  clearTimeout(NAV_FLY_OPEN_T); clearTimeout(NAV_FLY_CLOSE_T);
  if (!NAV_FLY_FOR) return;
  const el = NAV_FLY_FOR; NAV_FLY_FOR = null;
  el.setAttribute("aria-expanded", "false");
  const fly = document.getElementById("navFly");
  if (fly && fly.classList) fly.classList.remove("show");
  if (returnFocus && el.focus) el.focus();
}
function scheduleNavFlyClose() { clearTimeout(NAV_FLY_OPEN_T); clearTimeout(NAV_FLY_CLOSE_T); NAV_FLY_CLOSE_T = setTimeout(() => closeNavFly(), 260); }

function wireRail() {
  const rail = document.getElementById("rail"), fly = document.getElementById("navFly"), tip = document.getElementById("navTip");
  if (!rail || !fly || !tip || !rail.addEventListener) return;
  tip.addEventListener("pointerenter", () => clearTimeout(NAV_TIP_T));
  tip.addEventListener("pointerleave", hideNavTip);
  fly.addEventListener("pointerenter", () => clearTimeout(NAV_FLY_CLOSE_T));
  fly.addEventListener("pointerleave", scheduleNavFlyClose);
  fly.addEventListener("click", (e) => {
    const b = e.target.closest(".fi");
    if (!b) return;
    const n = NAV_FLY_ITEMS[Number(b.dataset.fi)], owner = NAV_FLY_FOR && railNode(NAV_FLY_FOR);
    closeNavFly();
    if (!n) return;
    if (owner === n) railGo(n); else n.click();   // "כל המחשבים" = הדף; ילד = הלחיצה עליו בעץ
    if (e.detail === 0 && owner) railFocusNode(owner);
  });
  fly.addEventListener("keydown", (e) => {
    const b = e.target.closest(".fi");
    if (!b) return;
    if (e.key === "ArrowDown") railMove(fly, ".fi", b, 1);
    else if (e.key === "ArrowUp") railMove(fly, ".fi", b, -1);
    else if (e.key === "Home") railMove(fly, ".fi", b, "home");
    else if (e.key === "End") railMove(fly, ".fi", b, "end");
    else if (e.key === "Escape" || e.key === "ArrowRight") closeNavFly(true);
    else if (e.key === "Tab") { const o = NAV_FLY_FOR; closeNavFly(); if (o) o.focus(); return; }
    else return;
    e.preventDefault(); e.stopPropagation();
  });
  rail.addEventListener("pointerover", (e) => {
    const el = e.target.closest(".ri,.rled");
    if (!el || e.pointerType === "touch") return;
    if (hasClass(el, "kids")) {
      clearTimeout(NAV_FLY_CLOSE_T); clearTimeout(NAV_FLY_OPEN_T);
      if (NAV_FLY_FOR === el) return;
      NAV_FLY_OPEN_T = setTimeout(() => openNavFly(el), NAV_FLY_FOR ? 0 : 250);   // כוונה: 250ms; כבר פתוח — מעבר מיידי
      if (!NAV_FLY_FOR) showNavTip(el);
    } else { if (NAV_FLY_FOR) scheduleNavFlyClose(); showNavTip(el); }
  });
  rail.addEventListener("pointerout", (e) => {
    const el = e.target.closest(".ri,.rled");
    if (!el || el.contains(e.relatedTarget)) return;
    clearTimeout(NAV_FLY_OPEN_T);
    if (NAV_FLY_FOR) scheduleNavFlyClose();
    if (!tip.contains(e.relatedTarget)) setTimeout(() => { if (!tip.matches(":hover")) hideNavTip(); }, 120);
  });
  rail.addEventListener("click", (e) => {
    const el = e.target.closest(".ri");
    const n = railNode(el);
    if (!n) return;
    const wasOpen = NAV_FLY_FOR === el;
    closeNavFly(); hideNavTip();
    railGo(n);   // הלחיצה תמיד מנווטת
    const nb = railFocusNode(n);
    // במגע אין ריחוף: הרשימה נפתחת אחרי הניווט; בעכבר — נשארת פתוחה אם כבר הייתה
    if (nb && hasClass(nb, "kids") && (e.pointerType === "touch" || wasOpen)) openNavFly(nb);
  });
  rail.addEventListener("focusin", (e) => { const el = e.target.closest(".ri"); if (el && el.matches(":focus-visible")) showNavTip(el, true); });
  rail.addEventListener("focusout", (e) => { if (!fly.contains(e.relatedTarget)) { hideNavTip(); if (!rail.contains(e.relatedTarget)) closeNavFly(); } });
  rail.addEventListener("keydown", (e) => {
    const el = e.target.closest(".ri");
    if (!el) return;
    const k = e.key;
    if (k === "ArrowDown" || k === "ArrowUp" || k === "Home" || k === "End") {
      closeNavFly();
      const t = railMove(rail, ".ri", el, k === "ArrowDown" ? 1 : k === "ArrowUp" ? -1 : k === "Home" ? "home" : "end");
      if (t) showNavTip(t, true);
    } else if (k === "ArrowLeft" && hasClass(el, "kids")) openNavFly(el, true);   // RTL: ← = פנימה
    else if (k === "Escape") { if (NAV_FLY_FOR) closeNavFly(true); else hideNavTip(); }
    else return;
    e.preventDefault(); e.stopPropagation();
  });
  document.addEventListener("pointerdown", (e) => { if (NAV_FLY_FOR && !fly.contains(e.target) && !rail.contains(e.target)) closeNavFly(); });
  window.addEventListener("resize", () => { closeNavFly(); hideNavTip(); });
}
function paintRailFoot(cls, text) {
  const rail = document.getElementById("rail");
  const r = rail && rail.querySelector ? rail.querySelector(".rled") : null;
  if (!r || !r.setAttribute) return;
  r.setAttribute("aria-label", text); r.setAttribute("data-tipx", text);
  const l = r.querySelector(".led");
  if (l) l.className = "led " + cls;
}

/* ‏console-full.html: הפאנל נפתח בתוך הדוק — כותרת הדוק (משימות · N פעילות · סטטוס) בראש האזור הפתוח, והשורות מתחתיה.
   גובה הפאנל נמדד מהטבלה (עד 260px) ומועבר ל-CSS כ-‎--task-h; ‏body.tasks-open מזיז את הדוק מעל הפאנל. */
function syncTaskPanel() {
  try {   // ‏קוסמטי בלבד — בסביבה בלי DOM מלא (טסטים) לא נופלים
    const panel = document.getElementById("taskPanel");
    if (!panel || !panel.classList.contains("open")) return;
    const wrap = panel.querySelector(".table-wrap");
    const h = Math.max(96, Math.min(260, ((wrap && wrap.scrollHeight) || 0) + 2));
    document.body.style.setProperty("--task-h", h + "px");
  } catch (e) { /* no layout here */ }
}
/* נדב 17/10: הדוק סגור בכל כניסה. מה שהמשתמש פתח נזכר ב-sessionStorage — לאותו סשן בלבד (רענון), ו-showLogin מוחק. */
const DOCK_KEY = "imagectl.dock.open";
function dockStore(v) { try { if (v) sessionStorage.setItem(DOCK_KEY, "1"); else sessionStorage.removeItem(DOCK_KEY); } catch (e) { /* העדפה בלבד */ } }
function toggleTaskPanel() {
  const panel = document.getElementById("taskPanel");
  panel.classList.toggle("open");
  const open = panel.classList.contains("open");
  document.body.classList.toggle("tasks-open", open);
  dockStore(open);
  syncTaskPanel();
}
function restoreTaskDock() {
  try {
    const panel = document.getElementById("taskPanel");
    if (sessionStorage.getItem(DOCK_KEY) === "1" && panel && !panel.classList.contains("open")) toggleTaskPanel();
  } catch (e) { /* בלי sessionStorage — סגור */ }
}

function activateTab(index) {
  currentTab = Number(index) || 0;
  const page = pages[current];
  if (!page) return;
  document.getElementById("content").innerHTML = layout(page, currentTab);
  const search = document.getElementById("globalSearch");
  if (search) search.value = searchQuery || "";
  if (current === "network") wireNetPage();
  wireRestoredPage();
  syncTreeToTab();   // apple-pending-4 · 2: העץ עוקב אחרי הלשונית
}

function refreshPage() {
  const page = pages[current];
  if (!page) return;
  if (page.load) { wireRestoredPage(); page.load(); }
  else renderCurrent();
  const search = document.getElementById("globalSearch");
  if (search) search.value = searchQuery || "";
  toast("הנתונים עודכנו");
}

/* ביקורת 10/10: כפתור "+ פעולה" בכותרת הגנרית נפתח לתפריט ריק בשרת המשנה (אין לו רשומה כאן). עכשיו הוא מצויר רק
   לעמוד שיש לו פעולות. */
const PAGE_ACTIONS = {
  images: [["אימג׳ חדש", "openCapture().catch(e => toast(e.message))"], ["קליטת אימג׳", "openImageIngest()"], ["אימות ספרייה", "scrubLibrary()"]],   // ‏#1303: POST /images/scrub
};
function openAction() {
  if (!isAdmin()) { selectPageById("images"); return; }
  const menu = document.getElementById("pageActionMenu");
  if (!menu) return;
  menu.innerHTML = (PAGE_ACTIONS[current] || []).map(([label, fn]) =>
    `<button type="button" role="menuitem" onclick="${fn};closeActionMenu()">${label}</button>`).join("");
  menu.style.display = "block";
  const btn = document.getElementById("pageActionBtn");
  if (btn) btn.setAttribute("aria-expanded", "true");
  const first = menu.querySelector("button");
  if (first && first.focus) first.focus();
}

function closeActionMenu() {
  const menu = document.getElementById("pageActionMenu");
  if (menu) menu.style.display = "none";
  const btn = document.getElementById("pageActionBtn");
  if (btn) btn.setAttribute("aria-expanded", "false");
}

/* מגירה: רוחב קבוע, כותרת + שורת משנה + X, לשוניות, ופס פעולות קבוע בתחתית.
   הבונים (imageDrawerHtml, machineDrawerHtml, storageDrawerHtml…) ממשיכים להחזיר מחרוזת אחת; drawerEnhance
   מחלק אותה בדפדפן אמיתי: .obj-sub -> שורת המשנה, .acts וה-foot -> הפס הקבוע, כל .sec אחרי הראשון -> לשונית. */
let drawerTab = 0;
function openDrawer(title, body, opts = {}) {
  closeSidebar();   // ‏#1300: עלה בעץ (אימג', מכונה) פותח מגירה — בחלון צר שכבת העץ נסגרת לפניה
  dialogRemember();
  drawerGeneration++;
  drawerTab = 0;
  document.getElementById("drawerTitle").textContent = title;
  const sub = document.getElementById("drawerSub");
  if (sub) sub.textContent = opts.sub || "";
  const meta = document.getElementById("drawerMeta");
  if (meta) meta.innerHTML = opts.meta || "";
  setDrawerBody(body);
  document.getElementById("drawer").classList.add("open");
  document.getElementById("detailBackdrop").classList.add("show");
  dialogFocusIn();
}

function setDrawerBody(html) {
  const body = document.getElementById("drawerBody");
  body.innerHTML = html;
  drawerEnhance(body);
}

function drawerEnhance(body) {
  const sub = document.getElementById("drawerSub"), tabs = document.getElementById("drawerTabs"), foot = document.getElementById("drawerFoot");
  if (tabs) { tabs.innerHTML = ""; tabs.hidden = true; }
  if (foot) { foot.innerHTML = ""; foot.hidden = true; }
  const root = body && body.firstElementChild;
  if (!root || !hasClass(root, "drw") || !body.appendChild || !tabs || !foot || !root.querySelector) return;   // לא בדפדפן אמיתי, או תוכן שאינו בונה-מגירה
  const kids = [...root.children];
  const objSub = kids.find((k) => hasClass(k, "obj-sub"));
  if (objSub && sub) { sub.innerHTML = objSub.innerHTML; objSub.remove(); }
  const acts = kids.find((k) => hasClass(k, "acts"));
  const last = kids[kids.length - 1];
  const tail = last && last !== acts && /border-top/.test(last.getAttribute("style") || "") ? last : null;
  const boxes = [acts, tail].filter(Boolean);
  const buttons = boxes.flatMap((box) => [...box.querySelectorAll("button, a.btn")]);
  const isDanger = (b) => hasClass(b, "danger"), isPrimary = (b) => hasClass(b, "primary");
  const ordered = [...buttons.filter(isDanger), ...buttons.filter((b) => !isDanger(b) && !isPrimary(b)), ...buttons.filter(isPrimary)];
  ordered.forEach((b, i) => {
    b.classList.remove("sm");
    if (isDanger(b)) b.classList.add("ghost-dng");
    if (i > 0 && !isDanger(b) && isDanger(ordered[i - 1])) { const sp = document.createElement("span"); sp.className = "n-hint"; foot.appendChild(sp); }
    foot.appendChild(b);
  });
  // ‏לא מאבדים תוכן: מה שנשאר בקופסה אחרי שהכפתורים עברו לשורת הפעולות (הסבר, "בקרוב", תווית WoL) נשאר בגוף; רק קופסה ריקה נמחקת.
  boxes.forEach((box) => { if (!box.children.length && !String(box.textContent || "").trim()) box.remove(); });
  if (!ordered.length || ordered.every(isDanger)) {
    const sp = document.createElement("span"); sp.className = "n-hint"; foot.appendChild(sp);
    const close = document.createElement("button"); close.type = "button"; close.className = "btn primary"; close.textContent = "סגירה";
    close.onclick = () => closeDrawer(); foot.appendChild(close);
  }
  foot.hidden = false;
  const blocks = [...root.children].filter((k) => k.firstElementChild && hasClass(k.firstElementChild, "sec"));
  const extra = blocks.slice(1);
  if (!extra.length) return;
  const overview = document.createElement("div");
  overview.className = "n-pane page drw";
  [...root.children].filter((k) => !extra.includes(k)).forEach((k) => overview.appendChild(k));
  const panes = [overview], labels = ["סקירה"], counts = [""];
  extra.forEach((blk) => {
    const heading = blk.firstElementChild;
    labels.push(heading.textContent.trim()); heading.remove();
    counts.push(heading.getAttribute("data-cnt") || "");   // ‏#1300: מונה בלשונית ("מחיצות 3")
    blk.classList.add("n-pane"); panes.push(blk);
  });
  body.innerHTML = "";
  panes.forEach((pn, i) => { pn.setAttribute("role", "tabpanel"); pn.id = "dpn" + i; body.appendChild(pn); });
  labels.forEach((label, i) => {
    const t = document.createElement("button");
    t.type = "button"; t.className = "n-tab"; t.setAttribute("role", "tab"); t.id = "dtb" + i; t.textContent = label;
    if (counts[i]) { const n = document.createElement("small"); n.textContent = counts[i]; t.appendChild(n); }
    t.setAttribute("aria-controls", "dpn" + i);
    t.onclick = () => selectDrawerTab(i);
    t.onkeydown = (e) => {
      const d = e.key === "ArrowLeft" ? 1 : e.key === "ArrowRight" ? -1 : 0;   // RTL: חץ שמאלה = הבא
      if (!d) return;
      e.preventDefault();
      const n = (i + d + labels.length) % labels.length;
      selectDrawerTab(n); document.getElementById("dtb" + n).focus();
    };
    tabs.appendChild(t);
  });
  tabs.hidden = false;
  selectDrawerTab(Math.min(drawerTab, labels.length - 1));
}

function selectDrawerTab(i) {
  drawerTab = i;
  document.querySelectorAll("#drawerTabs .n-tab").forEach((t, j) => {
    t.setAttribute("aria-selected", String(i === j)); t.tabIndex = i === j ? 0 : -1;
    const pane = document.getElementById("dpn" + j);
    if (pane) pane.hidden = i !== j;
  });
}

function closeDrawer() {
  drawerGeneration++;
  const wasOpen = hasClass(document.getElementById("drawer"), "open");
  document.getElementById("drawer").classList.remove("open");
  document.getElementById("detailBackdrop").classList.remove("show");
  if (wasOpen) dialogRestoreFocus();
}

// F06: החלון נפתח/נסגר במצבי CSS (class "show"), לא ב-display. הניקוי של
// sheet-mode וה-form ממתין לסוף ה-fade כדי שהתוכן לא יתחלף בזמן שהוא דוהה,
// ופתיחה חוזרת באמצע מבטלת אותו.
const MODAL_CLOSE_MS = 260;
let modalCleanupTimer = null;

/* ביקורת ממשק 10/10: modal / sheet / מגירה הם דיאלוג. הפוקוס עובר פנימה בפתיחה, Tab נשאר בתוכו,
   ובסגירה חוזר לכפתור שפתח. לפני זה: ב-modal ובמגירה הפוקוס נשאר בעמוד שמאחורי ה-scrim, Tab המשיך
   דרך העמוד, ואחרי סגירה הפוקוס נפל לשורש העץ. */
let dialogOpener = null;
const hasClass = (el, name) => !!(el && el.classList && el.classList.contains && el.classList.contains(name));
const DIALOG_FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
function dialogRoot() {
  if (hasClass(document.getElementById("modal"), "show")) {
    const form = document.getElementById("sheet");
    return form && !hasClass(form, "hidden") ? form : document.getElementById("modalBox");
  }
  const drawer = document.getElementById("drawer");
  return hasClass(drawer, "open") ? drawer : null;
}
function dialogFocusables(root) {
  return [...root.querySelectorAll(DIALOG_FOCUSABLE)].filter((e) => e.offsetParent !== null);
}
function dialogRemember() {
  const active = document.activeElement;
  if (active && active !== document.body && !dialogRoot()) dialogOpener = active;   // חלון על חלון: נשמר הראשון
}
function dialogFocusIn() {
  if (typeof requestAnimationFrame !== "function") return;
  requestAnimationFrame(() => {
    const root = dialogRoot();
    if (!root || root.contains(document.activeElement)) return;   // sheet() כבר פוקס על השדה הראשון
    const first = root.querySelector(".modal-b input,.modal-b select,.modal-b textarea,#modalOk") || root;
    if (first.focus) first.focus();
  });
}
function dialogRestoreFocus() {
  if (dialogRoot()) return;   // עדיין יש חלון פתוח
  const opener = dialogOpener;
  dialogOpener = null;
  if (opener && opener.isConnected !== false && opener.focus) opener.focus();
}
function dialogKeydown(e) {
  if (e.key !== "Tab") return;
  const root = dialogRoot();
  if (!root) return;
  const items = dialogFocusables(root);
  if (!items.length) { e.preventDefault(); if (root.focus) root.focus(); return; }
  const first = items[0], last = items[items.length - 1], active = document.activeElement;
  if (!root.contains(active) || active === root) { e.preventDefault(); (e.shiftKey ? last : first).focus(); return; }
  if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
}


// נקודת המוצא של האנימציה בלבד (קישוט): כשאין כפתור שפתח — מרכז החלון.
function modalOrigin(modal, mode) {
  const trigger = document.activeElement;
  const box = document.getElementById(mode === "sheet-mode" ? "sheet" : "modalBox");
  const usable = box && typeof box.offsetLeft === "number" && trigger && trigger !== document.body
    && typeof trigger.getBoundingClientRect === "function" && !(modal.contains && modal.contains(trigger))
    && modal.style && typeof modal.style.setProperty === "function";
  if (!usable) {
    if (modal.style && typeof modal.style.removeProperty === "function") modal.style.removeProperty("--origin");
    return;
  }
  const t = trigger.getBoundingClientRect();
  modal.style.setProperty("--origin",
    `${t.left + t.width / 2 - box.offsetLeft}px ${t.top + t.height / 2 - box.offsetTop}px`);
}

/* ‏#1334: חלון שהוא החלטה מחייבת (שמירת ה-VM) — בלי סגירה ב-Esc או ברקע. */
function modalLocked() {
  const modal = document.getElementById("modal");
  return !!(modal && modal.dataset && modal.dataset.locked === "1");
}

function openModalFrame(modal, mode) {
  clearTimeout(modalCleanupTimer);
  dialogRemember();
  if (mode === "sheet-mode") modal.classList.add("sheet-mode");
  else modal.classList.remove("sheet-mode");
  modalOrigin(modal, mode);
  modal.classList.add("show");
  dialogFocusIn();
}

function closeModal() {
  const modal = document.getElementById("modal");
  const wasOpen = hasClass(modal, "show");
  modal.classList.remove("show");
  if (wasOpen) dialogRestoreFocus();
  const form = document.getElementById("sheet");
  if (form) form.onsubmit = null;
  clearTimeout(modalCleanupTimer);
  modalCleanupTimer = setTimeout(() => {
    modal.classList.remove("sheet-mode");
    if (form) form.classList.add("hidden");
  }, MODAL_CLOSE_MS);
}

function toast(message, ms = 2600, kind = "") {
  const el = document.getElementById("toast");
  const text = String(message);
  // שגיאה = הטקסט בא מכשל API שקרה ב-15 השניות האחרונות (ראו noteApiFailure)
  if (!kind && lastApiFailure.text && text.includes(lastApiFailure.text) && Date.now() - lastApiFailure.at < 15000) kind = "err";
  el.textContent = message;
  el.classList.remove("err", "ok");
  if (kind) el.classList.add(kind);
  el.classList.add("show");
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => {
    el.classList.remove("show");
  }, ms);
}

// #857: הוספה/מחיקה/ייבוא של מכונה מחזירים `network` — האם known-macs
// ו-dnsmasq עודכנו. ה-DB נשמר גם כשלא; המפעיל חייב לדעת שהמכונה לא
// תקבל GRUB (fail-closed של #141) עד שהרשת תתוקן. מחזיר true כשהוצג.
function dhcpNotice(r) {
  if (!r || !r.network || r.network.applied !== false) return false;
  toast("המכונה נשמרה, אבל ה-DHCP לא עודכן — לא תקבל GRUB עד תיקון: "
    + (r.network.error || "סיבה לא ידועה"), 9000);
  return true;
}

function openModalContent(title, body, okLabel = "אישור", okFn = "closeModal()", danger = false, sub = "", size = "mid") {
  document.getElementById("modalTitle").textContent = title;
  const subEl = document.getElementById("modalSub");
  if (subEl) { subEl.textContent = sub || ""; subEl.hidden = !sub; }
  const hintEl = document.getElementById("modalHint");
  if (hintEl) hintEl.textContent = "";
  const back = document.getElementById("wz-back");   // כפתור "הקודם" של אשף קודם
  if (back && back.remove) back.remove();
  const smReset = document.getElementById("smap-reset");   // #2001: "החזר לברירת המחדל" של חלון המיפוי
  if (smReset && smReset.remove) smReset.remove();
  const bodyEl = document.getElementById("modalBody");
  bodyEl.innerHTML = body;
  const box = document.getElementById("modalBox");
  if (box) box.className = `modal win ${winSizeClass(size)}`;
  const ok = document.getElementById("modalOk");
  ok.textContent = okLabel;
  ok.className = "btn " + (danger ? "danger" : "primary");   // פעולה הרסנית לא נראית כמו אישור רגיל
  ok.disabled = false;
  ok.onclick = typeof okFn === "function" ? okFn : new Function(okFn);
  const modal = document.getElementById("modal");
  const form = document.getElementById("sheet");
  if (form) form.classList.add("hidden");
  wireWindowParts(bodyEl);
  openModalFrame(modal, "box");
}

/* ביקורת 10/10: החיפוש בסרגל סינן רק `.table tbody tr`, אבל כל העמודים שנבנו מחדש (#954) משתמשים ב-`table.dg` —
   אז "build" בדף המחשבים החזיר "0 תוצאות" מול שורה build-01 שעל המסך. עכשיו שני סוגי הטבלאות, ושורת כותרת קבוצה
   (tr.group) לא נספרת כתוצאה: היא מוצגת רק כשיש מתחתיה שורה תואמת. */
const SEARCH_ROWS = ".table tbody tr, table.dg tbody tr";
function doGlobalSearch(value) {
  searchQuery = value;
  const q = value.trim().toLowerCase();
  const rows = [...document.querySelectorAll(SEARCH_ROWS)];
  if (!q) {
    rows.forEach((r) => { r.style.display = ""; });
    return;
  }
  let hits = 0;
  for (const r of rows) {
    if (r.classList.contains("group")) continue;
    const match = r.textContent.toLowerCase().includes(q);
    r.style.display = match ? "" : "none";
    if (match) hits++;
  }
  rows.forEach((r, i) => {
    if (!r.classList.contains("group")) return;
    let any = false;
    for (let j = i + 1; j < rows.length && !rows[j].classList.contains("group"); j++) if (rows[j].style.display !== "none") any = true;
    r.style.display = any ? "" : "none";
  });
  toast("חיפוש: " + value + " · " + hits + " תוצאות בעמוד");
}

/* ---------- ‏#554: חיפוש גלובלי (שורת החיפוש העליונה) ----------
   הכרעת נדב 11/10: החיפוש חוצה את המערכת — אימג'ים, מחשבי בנייה/שיכפול, משתמשים, שרתי משנה ודפים.
   בלי API חדש: הנתונים הם הרשימות שהקונסולה כבר טוענת (IMAGES/MACHINES/USERS/BRANCH_NODES); מה שעוד לא נטען נקרא
   פעם אחת מאותם נתיבים. ההרשאה לפי pageAllowed/isAdmin — מפעיל הפצה (deploy) רואה אימג'ים ודפים מותרים בלבד.
   היומן אינו נכלל (החלטה פתוחה). השורה הראשונה תמיד "סנן בעמוד הנוכחי" — ההתנהגות הישנה, בלחיצה או Enter. */
const GS = { items: [], active: 0, loading: 0, failed: [] };
const GS_LIMIT = 6;
function gsHit(q, ...fields) { return fields.some((f) => String(f == null ? "" : f).toLowerCase().includes(q)); }
function globalSearchGroups(value) {
  const q = String(value || "").trim().toLowerCase();
  if (!q || !ME) return [];
  const out = [];
  const add = (title, rows) => { if (rows.length) out.push({ title, rows: rows.slice(0, GS_LIMIT) }); };
  add("אימג'ים", (IMAGES || []).filter((i) => gsHit(q, i.name, i.id))
    .map((i) => ({ label: i.name || i.id, sub: i.id, go: () => openImageDetail(encodeId(i.id)) })));
  if (isAdmin()) {
    const buildOrCloner = (m) => ["build", "cloner"].includes(machineRole(m));
    add("מחשבים", (MACHINES || []).filter((m) => buildOrCloner(m) && gsHit(q, machineName(m), m.mac))
      .map((m) => ({ label: machineName(m) || m.mac, sub: m.mac, go: () => openMachineDetail(m.mac) })));
    add("משתמשים", (USERS || []).filter((u) => gsHit(q, u.username))
      .map((u) => ({ label: u.username, sub: u.role === "admin" ? "מנהל" : "מפעיל הפצה", go: () => selectPageById("permissions") })));
  }
  if (pageAllowed("branches") && typeof BRANCH_NODES !== "undefined") {
    add("שרתי משנה", (BRANCH_NODES || []).filter((n) => gsHit(q, n.name, n.id))
      .map((n) => ({ label: n.name || n.id, sub: n.url || "", go: () => openBranchView(n.id, "overview") })));
  }
  add("דפים", Object.keys(pages).filter((id) => id !== "branch" && pageAllowed(id) && gsHit(q, pages[id].title, pages[id].crumb))
    .map((id) => ({ label: pages[id].title, sub: "", go: () => selectPageById(id) })));
  return out;
}
/* ‏"נקרא" ≠ "ריק": רשימה שלא נטענה מסומנת ב-GS.failed ומוצגת בשם, לא נעלמת בשקט (עיקרון 5). */
function gsEnsureData() {
  if (!ME) return;
  const need = [];
  if (!IMAGES) need.push(["אימג'ים", () => api("/images"), (v) => { if (Array.isArray(v)) IMAGES = v; else throw new Error("bad"); }]);
  if (isAdmin()) {
    if (!MACHINES || !GROUPS) need.push(["מחשבים", () => Promise.all([api("/machines"), api("/groups")]), (v) => { MACHINES = v[0]; GROUPS = v[1]; }]);
    if (!USERS) need.push(["משתמשים", () => api("/users"), (v) => { USERS = v; }]);
    if (pageAllowed("branches") && typeof BRANCH_NODES !== "undefined" && !(BRANCH_NODES && BRANCH_NODES.length))
      need.push(["שרתי משנה", () => api("/storage-nodes"), (v) => { BRANCH_NODES = v; }]);
  }
  GS.failed = GS.failed.filter((n) => !need.some((x) => x[0] === n));
  for (const [name, get, put] of need) {
    GS.loading++;
    get().then(put).catch(() => { GS.failed.push(name); })
      .then(() => { GS.loading--; gsRender(); });
  }
}
function gsRender() {
  const panel = document.getElementById("gsPanel"), input = document.getElementById("globalSearch");
  if (!panel || !input) return;
  const value = input.value || "";
  if (!value.trim()) { gsClose(); return; }
  const groups = globalSearchGroups(value);
  const items = [{ label: "סנן בעמוד הנוכחי: " + value.trim(), sub: "", go: () => doGlobalSearch(value), filter: true }];
  const row = (it, i) => `<div class="gs-item${i === GS.active ? " active" : ""}" role="option" id="gsOpt${i}" aria-selected="${i === GS.active}" data-gs="${i}"><span class="gs-label">${esc(it.label)}</span>${it.sub ? `<span class="gs-sub">${esc(it.sub)}</span>` : ""}</div>`;
  let html = row(items[0], 0);
  for (const g of groups) {
    html += `<div class="gs-group" role="presentation">${esc(g.title)}</div>`;
    for (const it of g.rows) { items.push(it); html += row(it, items.length - 1); }
  }
  if (!groups.length) html += `<div class="gs-empty" role="status">${GS.loading ? "טוען…" : "אין תוצאות"}</div>`;
  if (GS.failed.length) html += `<div class="gs-empty" role="status">לא נקרא: ${esc(GS.failed.join(", "))} — התוצאות חלקיות</div>`;
  GS.items = items;
  GS.active = Math.min(GS.active, items.length - 1);
  panel.innerHTML = html;
  panel.hidden = false;
  input.setAttribute("aria-expanded", "true");
}
function gsClose() {
  const panel = document.getElementById("gsPanel"), input = document.getElementById("globalSearch");
  if (panel) { panel.hidden = true; panel.innerHTML = ""; }
  if (input) input.setAttribute("aria-expanded", "false");
  GS.items = []; GS.active = 0;
}
function gsInput(value) {
  GS.active = 0;
  if (!String(value).trim()) { doGlobalSearch(""); gsClose(); return; }
  gsEnsureData();
  gsRender();
}
function gsPick(i) {
  const it = GS.items[i];
  if (!it) return;
  const input = document.getElementById("globalSearch");
  if (it.filter) { gsClose(); it.go(); return; }
  if (input) input.value = "";
  if (searchQuery) doGlobalSearch("");
  gsClose();
  it.go();
}
function gsKeydown(e) {
  if (!GS.items.length) return;
  const n = GS.items.length;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    GS.active = (GS.active + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
    gsRender();
    const opt = document.getElementById("gsOpt" + GS.active);
    if (opt && opt.scrollIntoView) opt.scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter") { e.preventDefault(); gsPick(GS.active); }
  else if (e.key === "Escape") { gsClose(); }
}

function openUserMenu() { document.getElementById("userMenu").classList.toggle("open"); }
function closeUserMenu() { document.getElementById("userMenu").classList.remove("open"); }

/* ‏#915: המגירה מציגה בדיוק את מה שהתג בסרגל סופר (updateAlertBadge):
   מכונות שנכשלו ובדיקות בריאות במצב אזהרה/תקלה. תג בלי רשימה שמסבירה
   אותו הוא מספר שאי אפשר לאמת. */
function topNotifications() {
  const rows = [];
  for (const m of MACHINES || []) {
    if (!machineIsFailed(m)) continue;
    rows.push(`<div class="list-row"><div class="list-main"><strong>${esc(machineName(m) || m.mac)}</strong><small>מכונה במצב כשל</small></div><span class="status err"><i></i>נכשל</span></div>`);
  }
  // ‏#2060: ‏HEALTH שאינו מערך = הבדיקות לא נקראו — "לא נבדק", לא "אין בעיות" (עיקרון 5)
  const healthRead = Array.isArray(HEALTH);
  if (!healthRead) {
    rows.push(`<div class="list-row"><div class="list-main"><strong>בדיקת הבריאות לא נקראה: ${esc(healthError || "עדיין לא נטענה")}</strong><small>אין ראיה שהשרת תקין</small></div><span class="status warn"><i></i>לא נבדק</span></div>`);
  }
  for (const c of (healthRead ? HEALTH : [])) {
    if (c.state !== "warn" && c.state !== "bad") continue;
    rows.push(`<div class="list-row"><div class="list-main"><strong>${esc(c.label)}</strong><small>${esc(c.detail || "")}</small></div><span class="status ${healthStatusClass(c.state)}"><i></i>${esc(healthStatusLabel(c.state))}</span></div>`);
  }
  const body = rows.length
    ? `<div class="list">${rows.join("")}</div>`
    : `<div class="empty">אין התראות</div>`;
  const count = rows.length;
  openDrawer("התראות", body, {
    sub: count ? `${count} ${count === 1 ? "דורשת" : "דורשות"} טיפול` : "אין התראות פתוחות",
    meta: UI.pill(count ? "warn" : "ok", !healthRead ? "לא נבדק" : count ? "פתוחות" : "הכול תקין"),
  });
}

function help() {
  openDrawer("עזרה מהירה", `<div class="section-title">איך משתמשים במערכת?</div><div class="list"><div class="list-row"><div class="list-main"><strong>1. קליטת אימג׳</strong><small>העלה אימג׳ ואמת manifest + SHA-256.</small></div></div><div class="list-row"><div class="list-main"><strong>2. פתיחת סבב</strong><small>בחר אימג׳, כיתה וכלל התחלה.</small></div></div><div class="list-row"><div class="list-main"><strong>3. מעקב</strong><small>עקוב אחרי הצטרפות, כתיבה וכשלים לכל תחנה.</small></div></div><div class="list-row"><div class="list-main"><strong>4. מחשב שנכשל</strong><small>בודד אותו, בדוק PXE/WoL, וטפל בו בלי לעצור את הסבב.</small></div></div></div>`, {
    sub: "איך משתמשים בקונסולה",
    meta: UI.pill("info", "מדריך מהיר"),
  });
}

/* ‏"החשבון שלי" (console-full.html P.account), על הנתונים של /me. ‏admin שנוצר בהתקנה (is_builtin) — בכוונה בלי MFA ובלי
   קודי גיבוי: הוא דלת החירום, ולכן רק התווית "מקומי · ללא MFA" והסבר. מנהל רגיל: כרטיס MFA, וכרטיס קודי גיבוי רק כש-MFA פעיל
   (mfa_enabled). ‏"הפעל" רק למנהל — כמו לפני כן. */
function accountCardsHtml(asPage = false) {
  const name = esc(ME && ME.username || "");
  const role = ME && ME.role === "admin" ? "מנהל" : "הפצה";
  const builtin = !!(ME && ME.is_builtin), mfaOn = !!(ME && ME.mfa_enabled), admin = !!(ME && ME.role === "admin");
  const half = asPage ? "c6" : "c12";   // בדף: שני כרטיסים בשורה (console-full.html P.account); במגירה: טור אחד
  const me = (asPage ? "" : UI.card({ title: "חשבון", body: UI.kv([["משתמש", name], ["תפקיד", esc(role)], ["מצב", UI.status("ok", "מחובר")]]) }))
    + UI.card({ title: "סיסמה", cls: half, body: `<button class="btn primary sm" onclick="accountChangePassword()">שנה סיסמה</button>` });
  let mfa, codes = "";
  if (builtin) mfa = UI.pill("", "מקומי · ללא MFA")
    + `<p class="muted" style="margin-top:8px">משתמש המנהל שנוצר בהתקנת השרת אינו משתמש ב-MFA — בכוונה, כדי שתמיד תהיה דרך להיכנס. למשתמשי מנהל אחרים MFA מופעל מכאן.</p>`;
  else if (mfaOn) {
    // נדב 18/10: מי שהפעיל יכול גם לבטל — עם קוד (השרת מסרב כשהחיוב למנהלים דלוק)
    mfa = UI.status("ok", "פעיל") + ` <button class="btn sm" onclick="accountDisableMfa()">בטל…</button>`;
    codes = UI.card({ title: "קודי גיבוי", cls: half, body: `<p class="muted" style="margin-bottom:10px">כל קוד עובד פעם אחת. יצירת קודים חדשים פוסלת את הקיימים.</p><button class="btn sm" onclick="accountNewBackupCodes()">צור קודי גיבוי חדשים</button>` });
  } else mfa = UI.status("", "כבוי") + (admin ? ` <button class="btn sm" onclick="accountEnableMfa()">הפעל</button>` : "");
  return me + UI.card({ title: "אימות דו-שלבי (MFA)", cls: half, body: mfa }) + codes + (asPage ? appearanceCard() : "");
}
/* ‏"מראה" — העדפה של המשתמש (users.theme), לא של השרת: עבר מההגדרות ל"החשבון שלי" (נדב 17/10). */
function appearanceCard() {
  const seg = THEME_CYCLE.map((t) => `<button type="button" onclick="setThemePref('${t}')" aria-pressed="${currentTheme() === t}">${esc(THEME_TITLE[t])}</button>`).join("");
  return UI.card({ title: "מראה", cls: "c6", body: `<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap"><span>ערכת נושא</span><span class="seg" role="group" aria-label="ערכת נושא">${seg}</span></div><p class="muted" style="margin-top:8px">חל על המשתמש שלך בלבד, בכל מחשב שתיכנס ממנו.</p>` });
}
function openAccount() {
  closeUserMenu();
  openDrawer("המשתמש שלי", `<div class="page drw">${accountCardsHtml()}</div>`);
}
/* ‏"החשבון שלי" כדף בעץ (ניהול), כמו console-full.html P.account — אותם כרטיסים כמו במגירה. */
function account() {
  const role = ME && ME.role === "admin" ? "מנהל" : "הפצה";
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "ניהול" }, { label: "החשבון שלי" }],
    icon: "t-me", name: "החשבון שלי", sub: esc(ME && ME.username || ""), pill: UI.pill("info", role),
    actions: `<button class="btn" onclick="openSessionInfo()">פרטי החיבור</button>` });
  return `<div class="page">${header}<div class="body">${accountCardsHtml(true)}</div></div>`;
}
function openAccountPage() { closeUserMenu(); selectPageById("account"); }

/* ‏#1268: החלפת סיסמה עצמית — POST /me/password עם הנוכחית, כמו מסך "בחר סיסמה חדשה". המדיניות נבדקת לפני
   שליחה (passwordPolicy, אותם כללים כמו בשרת), ותשובה בלי ok אינה "הוחלפה" (עיקרון 5). מנהל בלי MFA מקבל
   מהשרת עוגיית הגדרת-MFA בלבד — ולכן עוברים למסך ההגדרה, כמו בכניסה. */
function accountChangePassword() {
  const name = (ME && ME.username) || "";
  sheet({ title: "שינוי סיסמה", sub: name, submitLabel: "שמירה",
    fields: [
      { id: "current", label: "סיסמה נוכחית", type: "password", policy: false, autocomplete: "current-password" },
      { id: "next", label: "סיסמה חדשה", type: "password", confirm: "אימות הסיסמה החדשה", policy: true, autocomplete: "new-password" }],
    onSubmit: async (v) => {
      if (!passwordPolicy(v.next).all) throw new Error("הסיסמה החדשה אינה עומדת במדיניות");
      const r = await post("/me/password", { current_password: v.current, new_password: v.next });
      if (!r || r.ok !== true) throw new Error("השרת לא אישר את החלפת הסיסמה");
      if (r.mfa_enrollment_required) { showLogin(); await loginStartSetup({ screen: "setup", username: name }); return; }
      toast("הסיסמה הוחלפה — שאר ההפעלות שלך נותקו");
    } });
}

async function accountEnableMfa() {
  try {
    const setup = await post("/me/mfa/setup");
    sheet({
      title: "הגדרת אימות דו-שלבי",
      sub: "הקלד את הסוד באפליקציה ואז את הקוד שהיא מציגה.",
      note: `<p class="mono" dir="ltr">${esc(secretBlocks(setup.secret || ""))}</p>`
        + (setup.otpauth_url ? `<p><a class="mono" href="${esc(setup.otpauth_url)}">${esc(setup.otpauth_url)}</a></p>` : ""),
      fields: [{ id: "code", label: "הקוד מהאפליקציה", dir: "ltr" }],
      submitLabel: "אימות והפעלה",
      onSubmit: async (v) => {
        await post("/me/mfa/verify", { code: v.code });
        const en = await post("/me/mfa/enable", { code: v.code });
        if (ME) ME.mfa_enabled = true;
        showBackupCodesDrawer(en.backup_codes || []);
      },
    });
  } catch (e) { toast(e.message); }
}

/* נדב 18/10: ביטול MFA לעצמי — עם קוד מהאפליקציה או קוד גיבוי (POST /me/mfa/disable). */
function accountDisableMfa() {
  sheet({
    title: "לבטל את האימות הדו-שלבי?",
    sub: "הכניסה הבאה תהיה בסיסמה בלבד. לאישור — הסיסמה הנוכחית, והקוד מהאפליקציה או קוד גיבוי.",
    fields: [{ id: "current_password", label: "הסיסמה הנוכחית", type: "password", autocomplete: "current-password" },
             { id: "code", label: "קוד", dir: "ltr" }],
    danger: true, submitLabel: "בטל MFA",
    onSubmit: async (v) => {
      await post("/me/mfa/disable", { current_password: v.current_password, code: v.code });
      if (ME) ME.mfa_enabled = false;
      toast("האימות הדו-שלבי בוטל");
      if (current === "account") renderCurrent();
    },
  });
}

function accountNewBackupCodes() {
  sheet({
    title: "קודי גיבוי חדשים",
    sub: "הקוד מאפליקציית האימות. הקודים הישנים יבוטלו.",
    fields: [{ id: "code", label: "הקוד מהאפליקציה", dir: "ltr" }],
    submitLabel: "צור קודים",
    onSubmit: async (v) => {
      const en = await post("/me/mfa/enable", { code: v.code });
      showBackupCodesDrawer(en.backup_codes || []);
    },
  });
}

function showBackupCodesDrawer(codes) {
  const list = (codes || []).map((c) => `<span class="mono">${esc(c)}</span>`).join("<br>");
  openDrawer("קודי גיבוי", `<p>כל קוד עובד פעם אחת. הם לא יוצגו שוב.</p><div class="backup" style="margin-top:10px">${list}</div>`
    + `<div class="action-strip"><button class="btn sm" onclick='copyBackupCodes(${JSON.stringify(codes || [])})'>העתק</button>`
    + `<button class="btn sm" onclick='printBackupCodes(${JSON.stringify(codes || [])})'>הדפס</button></div>`);
}

function copyBackupCodes(codes) {
  const text = (codes || []).join("\n");
  if (navigator.clipboard && navigator.clipboard.writeText)
    navigator.clipboard.writeText(text).then(() => toast("הקודים הועתקו"), () => toast("ההעתקה נכשלה"));
  else toast("ההעתקה אינה זמינה");
}

function openSessionInfo() {
  closeUserMenu();
  const name = esc(ME && ME.username || "");
  // ‏#703: טביעת האצבע המלאה של תעודת הקונסולה — להשוואה מול "פרטי תעודה" בדפדפן.
  const fp = ME && ME.tls && ME.tls.fingerprint_sha256;
  const tls = fp
    ? `<div class="detail-box"><span class="k">הצפנה (TLS)</span><span class="v">${tlsModeHe(ME.tls)} · SHA-256 <span dir="ltr" style="word-break:break-all">${esc(fp)}</span></span></div>`
    : `<div class="detail-box"><span class="k">הצפנה (TLS)</span><span class="v">כבויה (loopback)</span></div>`;
  const role = ME && ME.role === "admin" ? "מנהל" : "הפצה";
  openDrawer("פרטי החיבור", `<div class="detail-grid"><div class="detail-box"><span class="k">משתמש</span><span class="v">${name}</span></div><div class="detail-box"><span class="k">חיבור</span><span class="v">מחובר ומאומת</span></div>${tls}</div>`, {
    sub: `${ME && ME.username || ""} · ${role}`,
    meta: UI.pill("ok", "פעיל"),
  });
}

function logout() {
  closeUserMenu();
  post("/logout").then(() => { ME = null; showLogin(); }).catch(() => { ME = null; showLogin(); });
}


/* #906: המכונה עומדת על שאלה לאדם (SMART/אדום, מסך FAILED) — ה-hello
   ממשיך עם `prompt`, והשאלה מוצגת כאן במקום "לא נראתה". null = לא ממתינה.
   #908/#912: המסכים של מחשב הבנייה שולחים מילה קבועה (הסוכן מדפיס ASCII
   בלבד) — כאן היא מתורגמת; כל טקסט אחר הוא השורה שעל המסך, ומוצג כמו שהוא. */
/* 18/10 (rc6, משכפל 2 בדף המוניטור): גם השאלות הקבועות של מסכי השיכפול המקומי (`attended_start` ב-
   localclone.sh/lcpull.sh) עולות כ-`prompt` באנגלית — הן מתורגמות כאן, מילה במילה מול הסוכן
   (test_hebrew_screen_strings בודק שלכל שאלה כזו יש שורה). */
const PROMPT_HE = { menu: "תפריט", signin: "כניסה",
  "Local clone screen: from the server, or a local clone": "מסך הבחירה: מהשרת או שיכפול מקומי",
  "Local clone at the machine -- not from the server": "שיכפול מקומי במחשב — לא מהשרת",
  "Restore an image from the server onto this machine's drawers": "שחזור אימג' מהשרת למגירות של המחשב הזה" };
/* 17/10 (נדב): סיבות כשל מוצגות בעברית בלבד. שורת ה-hold של הסוכן (ui_error_hold) עולה כ-`prompt`
   באנגלית עם הקידומת "FAILED: "; כאן היא מתורגמת לאותן כותרות שמסך הכשל של המחשב כותב
   (failscreen.sh). שורה שאין לה מקרה → "המשימה נכשלה" (כמו שם) — לא האנגלית הגולמית.
   הסדר חשוב: הראשון שתואם. */
const PROMPT_FAILED_HE = [
  ["all drawers skipped", "כל המגירות דולגו — לא נכתב דבר"], ["the disk was skipped", "הדיסק דולג — לא נכתב דבר"],
  ["no drawer completed", "אף מגירה לא הסתיימה בהצלחה"], ["restore cancelled", "השחזור בוטל, אך טבלת המחיצות לא נמחקה"],
  ["restore did not complete", "השחזור נכשל"], ["capture did not complete", "הקליטה נכשלה"],
  ["direct deployment did not", "השליחה הישירה נכשלה"], ["the work finished but", "המשימה הסתיימה — והשרת לא אישר"],
];
function promptHe(p) {
  if (PROMPT_HE[p]) return PROMPT_HE[p];
  if (typeof p !== "string") return p;
  if (p.startsWith("Replace the flagged disk")) return "החליפו את הדיסק שסומן: כיבוי ידני";
  if (!p.startsWith("FAILED: ")) return p;
  const line = p.slice(8), hit = PROMPT_FAILED_HE.find(([en]) => line.startsWith(en));
  return (hit ? hit[1] : "המשימה נכשלה") + (/server was not told$/.test(line) ? " — והשרת לא יודע" : "");
}

/* #927: אותה אזהרת shrink-restore שב-captureWarningHtml, הפעם ליד
   המחשב עצמו — זה מי שהדיסק שלו נשאר מכווץ. המשימה האחרונה של ה-MAC
   הזה בלבד (CAPTURE_TASKS ממוין created_at DESC מהשרת). */
function machineCaptureWarningHtml(mac) {
  const t = (CAPTURE_TASKS || []).find((x) => x.mac === mac && x.state === "done" && x.error);
  if (!t) return "";
  return `<div class="section-title">אזהרת קליטה אחרונה</div><div class="notice warn">${esc(t.error)}</div>`;
}




function init() {
  const search = document.getElementById("globalSearch");
  if (search) {
    search.addEventListener("input", (e) => gsInput(e.target.value));
    search.addEventListener("keydown", gsKeydown);
    search.addEventListener("focus", () => { if (search.value.trim()) gsInput(search.value); });
  }
  const gsPanel = document.getElementById("gsPanel");
  if (gsPanel) gsPanel.addEventListener("mousedown", (e) => {
    const t = e.target.closest ? e.target.closest("[data-gs]") : null;
    if (!t) return;
    e.preventDefault();
    gsPick(Number(t.getAttribute("data-gs")));
  });
  document.getElementById("modal").addEventListener("click", (e) => { if (e.target.id === "modal" && !modalLocked()) closeModal(); });
  document.getElementById("detailBackdrop").addEventListener("click", closeDrawer);
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".user-menu") && !e.target.closest(".user")) closeUserMenu();
    if (!e.target.closest(".search")) gsClose();
    if (!e.target.closest("#pageActionMenu") && !e.target.closest("#pageActionBtn")) closeActionMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { if (!modalLocked()) closeModal(); closeDrawer(); closeUserMenu(); closeSidebar(); closeActionMenu(); closeNavFly(); hideNavTip(); }
    dialogKeydown(e);
  });
  document.getElementById("content").addEventListener("keydown", tablistKeydown);
  const tree = document.querySelector(".tree");
  if (tree) tree.addEventListener("keydown", treeKeydown);
  TREE_INITIAL = treeSnapshot();   // נדב 18/10: מצב העץ של index.html — אליו חוזרים בכל כניסה
  wireRail();   // ‏#1300: פס האייקונים — ריחוף, tooltip, תפריט צף ומקלדת
}

window.addEventListener("DOMContentLoaded", () => {
  // המטמון (אם יש) כבר הוחל ב-inline שב-index.html. כאן מעדכנים את
  // הכפתור, ואחרי /me — את העדפת השרת (auto מוחק את המטמון).
  const cached = localStorage.getItem(THEME_KEY);
  applyTheme(cached === "dark" || cached === "light" ? cached : "auto");
  init();
  api("/me").then((me) => { ME = me; return showApp(); }).catch((e) => {
    showLogin();
    const notice = typeof updLoginNotice === "function" ? updLoginNotice() : "";
    if (notice) loginRender({ notice });   // ‏#1301: אחרי עדכון — לא מסך כניסה שקט
    if (e && e.status === 403 && e.message === "password_change_required")
      loginRender({ screen: "change", error: "" });
    else if (e && e.status === 403 && e.message === "mfa_enrollment_required")
      loginStartSetup({ screen: "setup" });
  });
});

/* Restored baseline actions; existing API contracts and sheet confirmations. */
function stuckNote(stuck, mac) {
  const s = stuck && stuck[mac];
  if (!s) return "";
  return s.blocked
    ? `נכשל באתחול ${s.attempts} פעמים — נשלח לדיסק המקומי`
    : `אתחל ${s.attempts} פעמים ולא הצטרף`;
}

/* בריאות SMART וההכרעה על דיסק פגום (#652), לצפייה בלבד. הכותרת היא
   "דיסק N" לפי החריץ (port) ולא שם ההתקן — אותה מוסכמה של הסוכן. רק
   דיסק שאיננו תקין או שנעשתה עליו הכרעה מוצג, כדי לא להציף.
   ‏#1580 (נדב 17/10): שלושה צבעים — fail (SMART תקול) אדום ולא נכתב, failed_last
   (נכשל בשיכפול הקודם) כתום, ok/unchecked ירוק; warn מסוכן ישן — כתום. */
const SMART_HE = {ok: "תקין", warn: "SMART אזהרה", fail: "ה-BIOS יעצור ב-F1",   // #1366
                  failed_last: "נכשל בשיכפול הקודם", unchecked: "לא נבדק"};
const DECISION_HE = {replace: "להחלפה", rescue: "נכתב שוב", skip: "דולג"};
function disksHtml(disks) {
  if (!Array.isArray(disks) || !disks.length) return "";
  const chips = disks
    .filter((d) => d.verdict !== "ok" || d.decision)
    .map((d) => {
      const name = d.slot_name ? esc(d.slot_name) : d.disk_number != null ? `דיסק ${d.disk_number}` : esc(d.disk);   // #2001
      const health = d.verdict === "fail" ? esc(smartF1Text(d.reason)) : SMART_HE[d.verdict] || "SMART לא נבדק";   // #1861: why F1 / read-only
      const hint = d.reason === "crc" ? " (בדוק כבל)" : "";
      const dec = d.decision ? ` · ${DECISION_HE[d.decision] || esc(d.decision)}` : "";
      return `<span class="disk-smart ${esc(d.verdict)}">${name}: ${health}${hint}${dec}</span>`;
    });
  return chips.length ? `<div class="disks">${chips.join("")}</div>` : "";
}

/* #720: תוצאת ה-staging של הדרייברים בדיווח הסיום. null = השלב לא דיווח
   (סוכן ישן) — לא מוצג; `failed` הוא "הושלם, דרייברים לא הונחו" — מצב
   גלוי ליד המחשב, לא שורת יומן אבודה (עיקרון 5). */
function driversHtml(d) {
  if (!d) return "";
  if (hasLinuxDrivers(d)) return driversTone(d) === "warn" ? `<div class="notice warn">${esc(linuxDriversText(d.linux))}</div>` : `<div class="sub">${esc(linuxDriversText(d.linux))}</div>`;   // ‏#1331 T8
  // ‏#1331: drivers.boot רוכב על staged בלבד — bootstorage.sh רץ רק אחרי שהחבילות על הדיסק.
  // ‏#1331 (מוקאפ 17/10): אדום רק לדרייבר האחסון שלא הוגדר; "לא הונחו" — כתום, השחזור הושלם (#856).
  const boot = bootStorageText(d.boot), failedBoot = d.boot && d.boot.state === "failed";
  const bootHtml = !boot ? "" : failedBoot ? `<div class="err">${esc(boot)}. ${BOOT_RISK_HE}.</div>` : `<div class="sub">${esc(boot)}</div>`;
  if (d.state === "staged") return `<div class="sub">${esc(driversStagedText(d.packages))}</div>${bootHtml}`;
  if (d.state === "no_match") return `<div class="sub">דרייברים: אין חבילה תואמת לחומרה</div>`;
  if (d.state === "skipped") return "";
  return `<div class="notice warn">דרייברים לא הונחו — ${esc(d.error || "סיבה לא ידועה")}. המחשב יעלה בלי הדרייברים של הדגם.</div>`;
}

/* #856: החבר של ה-MAC הזה בסבב המוצג שהסתיים `done` ועדיין נושא `error`
   — "הושלם, עם אזהרה" — במגירת התחנה. אותו .notice.warn כמו בכרטיס. */
function machineRestoreWarningHtml(mac) {
  const m = (OVERVIEW?.session?.members || []).find((x) => x.mac === mac && (x.done || x.state === "done") && x.error);
  if (!m) return "";
  return `<div class="section-title">אזהרת שחזור אחרונה</div><div class="notice warn">${esc(m.error)}</div>`;
}

function memberRow(m, session, note = "") {
  const progress = Progress.view(m);
  const cls = m.done || m.state === "done" ? "done" : m.state === "failed" ? "failed" : "";
  // ‏#856: `done` שעדיין נושא `error` הוא "הושלם, עם אזהרה" — שם המחשב לא
  // נכתב (hostname.sh), ההרחבה נדחתה (#648) — לא כשל. עד כאן כל `error`
  // נצבע אדום (.err) גם מתחת לכרטיס ירוק, כמו כונן שנכשל. כתום — אותו
  // .notice.warn של captureWarningHtml (#927); אדום נשאר לכשל בלבד.
  const err = !m.error ? "" : cls === "done"
    ? `<div class="notice warn">${esc(m.error)}</div>` : `<div class="err">${esc(m.error)}</div>`;
  // מזוהה בשם המחשב שייכתב לו; מכונה שאינה רשומה נופלת חזרה ל-MAC.
  const label = m.hostname || m.name || m.mac;
  const single = session.kind === "unicast"
    ? `<span class="tag">יוניקאסט</span>`
    : session.single ? `<span class="tag">תחנה בודדת</span>` : "";
  return `<div class="member ${cls}">
    <div class="who-line" title="${esc(m.mac)}">
      <b>${esc(label)}</b>${single}
      ${m.hostname ? "" : `<span class="mono sub-mac">${esc(m.mac)}</span>`}
    </div>
    ${Progress.bar(m)}
    <span class="pct">${m.state === "waiting" ? "ממתין" : progress.label}</span>
    ${disksHtml(m.disks)}
    ${err}
    ${driversHtml(m.drivers)}
    ${note && !m.done ? `<div class="err">${esc(note)}</div>` : ""}
  </div>`;
}

/* משיכות יוניקאסט — תחנות שמושכות אימג' ב-HTTP, במקביל לשידור (#60).

   הן אינן "הסבב": אין להן קידומת, אין מי שמצטרף אליהן, והן אינן תופסות
   את חריץ השידור. הן כן עבודה אמיתית על השרת — ולכן הן מוצגות. שרת
   ששתי תחנות מושכות ממנו לא ייראה פנוי. */
function pullsHtml(pulls, afterRound) {
  if (!pulls.length) return "";
  const rows = pulls.map((p) => `<div class="pull">
      <div class="sub">מושכת: <b>${esc(p.image_name)}</b>${p.forecast ? ` · ${forecastText(p.forecast, null)}` : ""}</div>
      ${p.members.map((m) => memberRow(m, p)).join("")}
    </div>`).join("");
  return `${afterRound ? `<div class="divide"></div>` : ""}
    <div class="sub">משיכות יוניקאסט (${pulls.length}) — אינן תופסות את חריץ השידור</div>
    <div class="session-members">${rows}</div>`;
}

let SESSION_MACHINES = { group: null, list: [] };
async function sessionClassMachines(groupId) {
  if (SESSION_MACHINES.group !== groupId) {
    try {
      SESSION_MACHINES = {
        group: groupId,
        list: await api("/machines?group=" + encodeURIComponent(groupId)),
      };
    } catch (error) { return null; }   // null = לא נקרא, נבדל מרשימה ריקה (#517)
  }
  return SESSION_MACHINES.list;
}

/* #927: קליטה שהצליחה אבל שההחזרה של דיסק המקור לגודלו נכשלה (#87,
   ‏agent/lib/shrink.sh) מגיעה לשרת כמשימה `done` עם `error` לא-ריק
   (‏reports.py: ה-COALESCE שומר את אזהרת ה-shrink גם אחרי שהמניפסט
   סגר את המשימה). עד כאן `loadCaptures` סינן רק pending/running, אז
   ברגע שהמשימה עברה ל-done האזהרה נעלמה בלי שהמפעיל ראה שדיסק הבנייה
   נשאר מכווץ. כתום ולא אדום (#874: אזהרה, לא כשל קליטה) — אותם
   ‏var(--warn-*) של `.notice.warn` הקיים, בלי CSS חדש. */
function captureWarningHtml(t) {
  if (t.state !== "done" || !t.error) return "";
  return `<div class="note warn"><span aria-hidden="true">●</span><span><b>${esc(t.name)}</b>: ${esc(t.error)}</span></div>`;
}

async function loadCaptures() {
  // ‏CAPTURE_TASKS מחזיק את כל 20 המשימות האחרונות (לא רק הפעילות) —
  // openMachineDetail, renderActivity והספרייה מסננים כל אחד לפי מה שהוא צריך.
  const all = await api("/tasks");
  if (!Array.isArray(all)) throw new Error("/tasks: תשובה שאינה רשימה");   // לא נקרא ≠ אין קליטות (עיקרון 5)
  CAPTURE_TASKS = all;
  if (typeof capDetailRefresh === "function") capDetailRefresh();   // ‏#1505: פאנל הפירוט הפתוח
  CAPTURE_TASKS_READ = true;
  renderActivity();
  // ‏#954 גל 3א: אובייקט הבנייה/השיכפול הפתוח מתרענן כשקליטה משתנה.
  if (typeof groupTasksChanged === "function" && groupTasksChanged(all)) renderCurrent();
  // ‏#927: קליטה שהסתיימה עם אזהרה (המקור לא הוחזר לגודלו) אינה נעלמת —
  // הודעה מעל טבלת הספרייה. קליטה בתהליך היא שורה בטבלה (#954 גל 2), לא כאן.
  const bar = $("#capture-bar");
  if (bar) bar.innerHTML = all.filter((t) => t.state === "done" && t.error).map(captureWarningHtml).join("");
  if (typeof imagesTasksChanged === "function" && imagesTasksChanged(all)) renderCurrent();
}

async function openCapture(preMac = "") {
  if (!isAdmin()) return;
  try { preMac = decodeURIComponent(preMac); } catch (e) {}   // ‏גל 3א: "קלוט מכאן" מכרטיס מחשב בנייה
  /* בוחרים מחשב בנייה ואת הדיסק שהוא דיווח עליו ב-hello — כך אין
     ניחוש של שם התקן, והשרת יודע מה באמת מחובר שם. */
  const machines = await api("/machines");
  const groups = await api("/groups");
  const buildIds = new Set(groups.filter((g) => g.role === "build").map((g) => g.id));
  const builders = machines.filter((m) => buildIds.has(m.group_id));
  if (!builders.length) {
    toast("אין מחשב בנייה רשום — הוסיפו אותו בלשונית המחשבים.");
    return;
  }
  const devices = await api("/net");
  const seen = new Map(devices.map((d) => [d.mac, d]));
  /* ‏#1251 (נדב, 10/10): אימג' חדש נשמר תמיד בתיקייה — אין "ללא תיקייה".
     הרשימה נקראת כאן ולא מ-FOLDERS, שאינו טעון כשפותחים מדף מחשבי הבנייה:
     "לא נטען" אינו "אין תיקיות". */
  const folders = await api("/folders");
  if (!folders.length) {
    toast("אין עדיין תיקיות. צרו תיקייה בקונסולה ← אימג'ים (+ תיקייה), ואז הזמינו קליטה.");
    return;
  }
  const preFolder = folders.some((f) => f.name === IMAGES_FOLDER) ? IMAGES_FOLDER : "";

  const selected = builders.find((m) => m.mac === preMac) || builders[0];
  /* ‏#1485: רק כוננים (‏disks) — כונן CD/DVD אינו ברשימה ולעולם אינו מקור; המספר לפי המיפוי. */
  const diskOptions = (m) => (Array.isArray(m && m.disks) ? m.disks : []).map((d) => ({
    value: d.dev,
    label: `${diskLabel(m, d)} · ${marketedDiskSize(d.size_bytes)} · ${d.model || "דגם לא ידוע"}`,
  }));
  sheet({
    title: "קליטה ממחשב בנייה",
    sub: "הדיסק ייקרא ויועלה לספרייה כאימג' חדש",
    note: UI.note("", "המחשב חייב להישאר דלוק ומחובר לרשת לאורך הקליטה (כ-45 דקות ל-100 GB)."),
    fields: [
      {
        id: "mac", label: "מחשב בנייה", type: "select", value: preMac || undefined,
        options: builders.map((m) => ({
          value: m.mac,
          label: `${m.suffix} · ${m.mac}` + (seen.has(m.mac) ? "" : " (טרם נראה ברשת)"),
        })),
      },
      { id: "disk", label: "דיסק המקור", type: "select", value: diskOptions(selected)[0]?.value || "",
        options: diskOptions(selected).length ? diskOptions(selected) : [{ value: "", label: "המחשב לא דיווח על דיסקים" }] },
      { id: "name", label: "שם האימג'", placeholder: "למשל: Office 2024 — סטנדרט" },
      { id: "description", label: "תיאור (לא חובה)", type: "textarea" },
      {
        id: "folder", label: "תיקיית יעד", type: "select", value: preFolder,
        options: [{ value: "", label: "בחרו תיקייה" }].concat(
          folders.map((f) => ({ value: f.name, label: f.name }))),
      },
    ],
    submitLabel: "צור משימת קליטה",
    onSubmit: async (v) => {
      if (!v.disk) throw new Error("מחשב הבנייה לא דיווח על דיסק מקור.");
      if (!v.folder) throw new Error("בחרו תיקיית יעד — אימג' חדש נשמר תמיד בתיקייה.");
      await post("/tasks/capture", v); await loadCaptures();
    },
  });
  const machineSelect = document.getElementById("sf-mac");
  const diskSelect = document.getElementById("sf-disk");
  if (machineSelect && diskSelect) machineSelect.onchange = () => {
    const machine = builders.find((m) => m.mac === machineSelect.value);
    const options = diskOptions(machine);
    diskSelect.innerHTML = (options.length ? options : [{ value: "", label: "המחשב לא דיווח על דיסקים" }])
      .map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
  };
}
function editFolderSheet(folder) {
  sheet({
    title: "עריכת תיקייה",
    sub: folder.images
      ? `שינוי השם יעדכן גם את ${folder.images} האימג'ים שבתוכה.`
      : "התיקייה ריקה.",
    fields: [
      { id: "name", label: "שם התיקייה", value: folder.name },
      { id: "description", label: "תיאור", type: "textarea", value: folder.description },
    ],
    onSubmit: async (v) => {
      const result = await put(`/folders/${encodeId(folder.name)}`,
                               { name: v.name, description: v.description });
      IMAGES_FOLDER = result.name;
      await loadImages();
      toast("נשמר.", 2600, "ok");
    },
  });
}

function addFolderSheet() {
  if (!isAdmin()) return;
  if (!isAdmin()) return;
  sheet({
  title: "תיקייה חדשה",
  fields: [
    { id: "name", label: "שם התיקייה", placeholder: "למשל: סייבר" },
    { id: "description", label: "תיאור קצר (לא חובה)", type: "textarea" },
  ],
  submitLabel: "צור תיקייה",
  onSubmit: async (v) => {
    await post("/folders", { name: v.name, description: v.description });
    await loadImages();
  },
});
}


let UPDATE_INFO = {};

function confirmUpdateApply(tag) {
  if (!tag) return;
  // ‏#1273: לחיצה כפולה על "החלת העדכון" — שתי הלחיצות ממתינות לאותה בקשה, ולא שולחות שתיים.
  // סירוב (409 "כבר רץ") מוצג בחלון ומשחרר אותו לניסיון נוסף.
  // ‏#1301 (המוקאפ שאושר 13/10): שלוש השלכות, כפתור אדום, בלי הקלדה; אחרי "החלה" — מסך העדכון.
  let pending = null;
  const from = (HEALTH_UPDATE && HEALTH_UPDATE.current) || (ME && ME.version) || "";
  const session = OVERVIEW && OVERVIEW.session;
  const roundOpen = !!(session && (session.state === "open" || session.state === "running"));
  sheet({
    title: `להחיל עדכון ${tag}?`, sub: from ? `מגרסה ${from}` : "",
    note: `<div class="upd-impact">${impactList([
      ["w", "השרת יופעל מחדש. הקונסולה תראה את שלבי העדכון ותיכנס לבד כשהשרת יחזור — בדרך כלל 8–10 דקות."],
      ["w", "מחשב שיעלה מהרשת בדקות האלה יעלה מהדיסק המקומי."],
      ["ok", `רק גרסה חתומה מותקנת. אם משהו נכשל, השרת נשאר על ${from ? `<span class="mono">${esc(from)}</span>` : "הגרסה הנוכחית"} או חוזר אליה.`],
    ])}</div>`,
    hint: !OVERVIEW ? "" : roundOpen ? "יש סבב פתוח — השרת יסרב לעדכן עד שיסתיים" : "אין סבב פתוח — אפשר לעדכן עכשיו",
    danger: true, submitLabel: "החלת העדכון",
    onSubmit: () => pending || (pending = (async () => {
      try { await post("/update/apply", { tag }); }
      catch (e) { if (e.code === "update_busy") return () => confirmUpdateForce(tag, e.message, from); throw e; }
      return () => updStart({ tag, from: from || null, by: ME && ME.username });
    })().catch((error) => { pending = null; throw error; })),
  });
}
/* ‏#1891 (סקירה): השרת עסוק — חלק מהסיבות הן דיווח של מחשבים, ומחשב תקוע היה
   חוסם עדכון לעד. אזהרה עם הסיבה (והמחשבים), וכפתור אדום בלי הקלדה (נדב 10/10).
   העקיפה נרשמת ביומן בשרת. `revert` — אותו חלון לחזרה לגרסה הקודמת. */
function confirmUpdateForce(tag, reason, from, revert = false) {
  let pending = null;
  sheet({
    title: "השרת עסוק — לעדכן בכל זאת?", sub: tag,
    note: impactList([["w", esc(reason)],
      ["w", "עדכון עכשיו יפעיל מחדש את השרת באמצע העבודה הזו. העקיפה תירשם ביומן."]]),
    danger: true, submitLabel: "עדכן בכל זאת",
    onSubmit: () => pending || (pending = (async () => {
      await post(revert ? "/update/revert" : "/update/apply", revert ? { force: true } : { tag, force: true });
      return () => updStart({ tag, from: from || null, by: ME && ME.username });
    })().catch((error) => { pending = null; throw error; })),
  });
}
function confirmUpdateRevert(tag) {
  if (!tag) return;
  // ‏#1778 (הכרעת #1270): כפתור אדום בלי הקלדה ובלי שם שרת בגוף — החזרה הפיכה כמו "החל" (#1192).
  sheet({
    title: "לחזור לגרסה הקודמת?", sub: tag,
    note: impactList([["w", `השרת יחזור ל-${esc(tag)} ויופעל מחדש`]]),
    danger: true, submitLabel: "חזרה לגרסה",
    onSubmit: async () => {
      // ‏#1301: אותו סקריפט ואותו restart — אותו מסך.
      const from = (HEALTH_UPDATE && HEALTH_UPDATE.current) || (ME && ME.version) || null;
      try { await post("/update/revert"); }
      catch (e) { if (e.code === "update_busy") return () => confirmUpdateForce(tag, e.message, from, true); throw e; }
      return () => updStart({ tag, from, by: ME && ME.username });
    },
  });
}
function openNewUser() { sheet({
  title: "משתמש חדש",
  fields: [
    { id: "username", label: "שם משתמש", dir: "ltr" },
    {
      id: "password", label: "סיסמה (8 תווים לפחות)", type: "password",
      confirm: "אימות סיסמה",
    },
    {
      id: "role", label: "תפקיד", type: "radio", value: "deploy",
      options: [
        { value: "deploy", label: "הפצה", hint: "לתחנה ולכלי הטכנאי — בלי כניסה לקונסולה" },
        { value: "admin", label: "מנהל", hint: "הכול, כולל משתמשים והגדרות" },
      ],
    },
    // נדב 18/10: מנהל מסומן יירשם ל-MFA בכניסה הראשונה, גם כשהחיוב הכללי כבוי
    { id: "mfa_required", label: "דרוש אימות דו-שלבי", type: "checkbox", value: false,
      help: "המשתמש יירשם לאימות דו-שלבי בכניסה הראשונה ולא יוכל לבטל אותו בעצמו. חל על מנהלים." },
  ],
  submitLabel: "צור משתמש",
  onSubmit: async (v) => { await post("/users", { ...v, mfa_required: !!v.mfa_required }); toast(`המשתמש ${v.username} נוצר`); await loadUsersData(); },
}); }

/* ---------- #984: WoL למחשב בודד ולקבוצת הבנייה (#954 גל 6) ----------
   POST /machines/{mac}/wake · POST /groups/{gid}/wake → {sent, failed, reasons},
   כמו /room/wake (החדר נשאר wakeRoom). ‏build/cloner בלבד — תחנות כיתה = v2
   (השרת מחזיר 403). **"נשלח" ≠ "התעוררה":** חבילת WoL היא UDP בלי ACK (#528),
   ולכן הטקסט אומר נשלח. שרת ישן (אין את המסלול) = 404 "Not Found". */
function wolResultText(r, who) {
  const reasons = (r.reasons || []).length ? " — " + r.reasons.join("; ") : "";
  return `WoL נשלח ל-${who} (${r.sent})${r.failed ? `, ${r.failed} נכשלו` : ""}${reasons}`;
}
function wolErrorText(e) {
  if (e.status === 404 && /not found/i.test(e.message)) return "WoL למחשב יחיד דורש שרת חדש יותר (#984) — השרת הזה מכיר רק את WoL לחדר";
  return "WoL נכשל: " + e.message;
}
async function wakeMachine(macEnc) {
  const m = findMachine(macEnc);
  if (!m) { toast("מכונה לא נמצאה"); return; }
  if (!["build", "cloner"].includes(machineRole(m))) { toast("WoL לתחנות כיתה — v2"); return; }
  try {
    const r = await post(`/machines/${encodeId(m.mac)}/wake`);
    toast(wolResultText(r, machineName(m) || m.mac), 6000);
  } catch (e) { toast(wolErrorText(e), 6000); }
}
async function wakeGroup(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  try {
    const r = await post(`/groups/${encodeId(gid)}/wake`);
    toast(wolResultText(r, groupLabel(gid)), 6000);
  } catch (e) { toast(wolErrorText(e), 6000); }
}

function isAdmin() { return !!ME && ME.role === "admin"; }
function pageAllowed(id) {
  if (!ME) return false;
  // 16/10: גם בלי שרת משנה — ראשי/עצמאי (enroll_secondary) צריך את הדף כדי לרשום את **הראשון**
  if (id === "branches") return isAdmin() && !!(ME.capabilities?.interbranch_transfer || ME.capabilities?.enroll_secondary);
  if (id === "branch") return isAdmin() && !!ME.capabilities?.enroll_secondary;
  if (id === "tools") return isAdmin() && toolsOn();   // v1 בלי ארגז הכלים; v1.1 מדליק
  return ["home", "images", "deploy"].includes(id) || isAdmin();
}
function wireRestoredPage() {
  if (current === "images") loadCaptures().catch(e => toast(e.message));
  if (current === "branches") wireBranchesGroupsTab();   // ‏own page: הטעינה עצמה דרך page.load (loadBranchesData)
  if (current === "branch") loadBranchView().catch(e => toast(e.message));
}
let pollTimer = null, overviewBusy = false, overviewError = "", overviewLastOk = null;
let CAPTURE_TASKS = [];
let drawerGeneration = 0;
function startStatusWatch() {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (!ME || document.hidden) return;
    refreshStatus();
    // ‏#1253: כשל בדגימה = הרשימה שבזיכרון אינה ראיה שקליטה עדיין רצה (HOME.err.tasks).
    if (isAdmin() || current === "images") loadCaptures().then(() => { delete HOME.err.tasks; }, (e) => { HOME.err.tasks = e.message; toast(e.message); });
    if ((current === "machines" && MACHINES_CLASS) || current === "deploy") refreshGroupLive().catch(() => {});   // ‏גל 3א/4: /room חי באובייקט המשכפלים ובחדר
    refreshServerDots();   // נדב 18/10: רק שרת שהמדידה שלו ישנה מ-SERVER_DOT_TTL
    if (typeof vmuTick === "function") vmuTick();   // ‏#1334: עדכון ב-VM — הפס הגלובלי ועמוד ה-VM (vmupdate.js)
  }, 2000);
}
/* ‏#1255: פאנל "משימות אחרונות" (כמו Recent Tasks של vCenter). התחלה / משך / יוזם היו "-" קבוע בקוד
   למרות שהנתונים קיימים. עיקרון 5: ערך שהשרת לא החזיר = "לא ידוע", לא "-" ולא ריק.
   התחלה: ‏`taskWhen` (היום HH:MM / dd/mm/yyyy HH:MM). משך: עכשיו פחות התחלה, מתעדכן בכל רענון;
   למשימה שהסתיימה "הסתיים אחרי X". יוזם: המשתמש; ‏`deploy` אינו נכנס לקונסולה (#1073) ולכן
   ההפעלה שלו היא ממחשב הבנייה — "מחשב בנייה ‹שם›". */
const ACTIVITY_UNKNOWN = "לא ידוע";
function fmtSpan(seconds) {
  const s = Number(seconds);
  if (seconds == null || !Number.isFinite(s) || s < 0) return null;
  if (s < 60) return `${Math.floor(s)} שנ'`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} דק'`;
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h} שע' ${r} דק'` : `${h} שע'`;
}
/* end = null → עדיין רץ (משך = עכשיו - התחלה). ‏ts חסר/פגום = "לא ידוע". */
function activityDuration(startTs, endTs) {
  const a = Date.parse(startTs || "");
  if (!Number.isFinite(a)) return ACTIVITY_UNKNOWN;
  const b = endTs ? Date.parse(endTs) : Date.now();
  const d = Number.isFinite(b) ? fmtSpan((b - a) / 1000) : null;
  if (d == null) return ACTIVITY_UNKNOWN;
  return endTs ? `הסתיים אחרי ${d}` : d;
}
function activityStart(ts) { return ts ? taskWhen({ created_at: ts }) : ACTIVITY_UNKNOWN; }
function activityInitiator(user, role, machine) {
  if (!user) return ACTIVITY_UNKNOWN;
  if (role === "deploy") return machine ? `מחשב בנייה ${machine}` : `מחשב בנייה (${user})`;
  return user;
}
const ACTIVITY_PILL = { pending: "info", running: "info", verifying: "info", open: "info", done: "ok", closed: "", failed: "err", cancelled: "" };
function activityRows() {
  const rows = [], recent = [];
  const s = OVERVIEW?.session;
  if (s) {
    const ended = s.state === "closed" || s.state === "failed";
    rows.push({ name: sessionImage(s), target: sessionGroup(s), start: s.started_at || s.created_at, end: ended ? s.closed_at : null,
      label: sessionStateText(s), cls: ACTIVITY_PILL[s.state] || "", by: activityInitiator(s.opened_by, s.opened_by_role, ""), ended,
      pct: ended ? null : sessionProgressPct(s) });
  }
  // משיכה (pull) מתחילה מהמחשב עצמו — הוא היוזם
  for (const p of OVERVIEW?.pulls || []) for (const m of p.members || []) {
    const who = m.hostname || m.name || m.mac;
    rows.push({ name: p.image_name, target: who, start: p.started_at || p.created_at, end: null, label: Progress.view(m).label, cls: "info",
      by: who ? `מחשב ${who}` : ACTIVITY_UNKNOWN, pct: Progress.view(m).percent });
  }
  // ‏#1300: סבב שיכפול פעיל והעברות בין-אתרים הם פעילות של הדוק — גם הם פותחים אותו מעצמו
  // בסקירה (homeDockAuto). ‏HOME.transfers נקרא ב-loadHome; ‏null = לא נקרא — אין שורה, לא "אין".
  const room = OVERVIEW?.room;
  if (room && room.state === "active") {
    rows.push({ name: room.image_name || "סבב שיכפול", target: `${room.written_drives || 0}/${room.target_drives || 0} דיסקים`,
      start: room.started_at || null, end: null, label: stateLabel(room.wave_state), cls: "info",
      by: activityInitiator(room.opened_by, null, ""), pct: room.target_drives ? Math.round(100 * (room.written_drives || 0) / room.target_drives) : null });
  }
  for (const t of (typeof HOME !== "undefined" && Array.isArray(HOME.transfers)) ? HOME.transfers : []) {
    if (!["queued", "sending", "verifying"].includes(t.state)) continue;
    rows.push({ name: t.image_name || t.image_id, target: t.node_label || t.node_id, start: t.created_at, end: null,
      label: { queued: "ממתין בתור", sending: "שולח", verifying: "מאמת" }[t.state], cls: "info",
      by: activityInitiator(t.started_by, null, ""), pct: t.bytes_total ? Math.round(100 * (t.bytes_sent || 0) / t.bytes_total) : null });
  }
  // ‏#1391: עדכון ב-VM (vmupdate.js) — הכנה, ריצה ושמירה הן משימה; לחיצה מחזירה לעמוד ה-VM.
  const vm = typeof vmuActivityRow === "function" ? vmuActivityRow() : null;
  if (vm) rows.push({ ...vm, by: vm.by || ACTIVITY_UNKNOWN });
  // ‏#927: CAPTURE_TASKS מחזיק גם משימות שהסתיימו (לאזהרת ה-shrink) — "העברה פעילה" רק pending/running;
  // שהסתיימו (עד 5 אחרונות) מופיעות מתחת, בלי להיספר.
  if (isAdmin()) for (const t of CAPTURE_TASKS) {
    const active = ["pending", "running", "verifying"].includes(t.state);   // = captureInFlight; inline: this block is also loaded alone (capture_shrink_warning.test.cjs)
    const row = { name: t.name, target: t.folder ? `תיקיית ${t.folder}` : "ספריית האימג'ים", start: t.created_at,
      end: active ? null : t.updated_at, label: Progress.view(t).label, cls: ACTIVITY_PILL[t.state] || "",
      by: activityInitiator(t.created_by, t.created_by_role, t.machine || t.mac), ended: !active, pct: Progress.view(t).percent,
      open: `openCaptureDetail('${encodeURIComponent(t.id).replace(/'/g, "%27")}')` };   // ‏#1505: פירוט קליטה
    if (active) rows.push(row);
    else if (["done", "failed", "cancelled"].includes(t.state) && recent.length < 5) recent.push(row);
  }
  return { rows, recent };
}
function activityRowHtml(r) {
  const duration = r.ended && !r.end ? ACTIVITY_UNKNOWN : activityDuration(r.start, r.end);
  const bar = r.pct == null || !Number.isFinite(Number(r.pct)) ? ""
    : `<div class="bar${r.cls === "err" ? " err" : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(r.pct)}"><i style="--w:${Math.round(r.pct)}%"></i></div>`;
  // ‏#1391: שורה עם `open` (קוד JS קבוע מהקוד שלנו, לא מהשרת) — לחיצה/Enter פותחים את המשימה.
  const cls = [r.cls === "err" ? "fail" : "", r.open ? "go" : ""].filter(Boolean).join(" ");
  const go = r.open ? ` tabindex="0" onclick="${r.open}" onkeydown="if(event.key==='Enter'){${r.open}}"` : "";
  return `<tr${cls ? ` class="${cls}"` : ""}${go}><td>${esc(r.name)}${bar}</td><td>${esc(r.target)}</td><td class="num">${esc(activityStart(r.start))}</td><td class="num">${esc(duration)}</td>`
    + `<td><span class="pill ${r.cls}">${esc(r.label)}</span></td><td>${esc(r.by)}</td></tr>`;
}
function renderActivity() {
  const { rows, recent } = activityRows();
  const body = document.querySelector("#taskPanel tbody");
  if (body) body.innerHTML = (rows.length ? rows.map(activityRowHtml).join("") : '<tr><td colspan="6">אין העברות פעילות</td></tr>')
    + recent.map(activityRowHtml).join("");
  if (typeof syncTaskPanel === "function") syncTaskPanel();
  const count = $(".task-count"); if (count) count.textContent = String(rows.length);
  const summary = $(".task-summary"); if (summary) summary.textContent = overviewError || (rows.length ? rows.length + " העברות פעילות" : "אין העברות פעילות");
}
function editImageDescription(id) {
  const img = findImage(id); if (!img || !isAdmin()) return;
  sheet({title:"תיאור האימג'", fields:[{id:"description",label:"תיאור",type:"textarea",value:img.description}], onSubmit:async v => {
    await put("/images/"+encodeId(img.id), v); await loadImages(); openImageDetail(img.id);
  }});
}
function monitorMachine(mac) {
  const m=findMachine(mac), g=(GROUPS || []).find(g=>g.id===machineGroupId(m));
  if (!isAdmin() || !m || !["build","cloner"].includes(g?.role)) return;
  window.open("monitor.html?mac="+encodeId(m.mac)+"&name="+encodeId(machineName(m)),"imagectl-monitor-"+m.mac);
}

/* #827: הדלקה דורשת הקלדת
   מילת האישור, כיבוי לא. ‏confirm חייב לתאום בדיוק את מה שהשרת דורש
   (imagectl.monitor, #690).
   ‏#1227 (עיקרון 5): ההודעה נגזרת מה-`enabled` שחזר בתשובה, לא ממה
   שביקשנו — תשובה בלי `enabled` היא "לא אומת", לא הצלחה בשתיקה. */
function monitorToggle(enabling) {
  if (!isAdmin()) return;
  const send = async (extra) => {
    const result = await put("/monitor/settings", { enabled: enabling, ...extra });
    if (!("enabled" in result)) toast("לא אומת — התשובה לא כללה enabled");
    else if (result.enabled !== enabling)
      toast(`המוניטור לא ${enabling ? "הודלק" : "כובה"}: השרת מחזיר ${result.enabled ? "דלוק" : "כבוי"}`);
    else toast(enabling
      ? "המוניטור הודלק — תופס באתחול הבא של כל תחנה"
      : "המוניטור כובה — תופס באתחול הבא של כל תחנה");
    if (current === "ports") await loadPorts(); else await loadMonitor();
  };
  if (!enabling) {
    send({}).catch((error) => toast(error.message));
    return;
  }
  sheet({
    title: "להדליק את המוניטור לתחנות?", sub: "",
    danger: true, submitLabel: "הדלקת המוניטור",
    note: impactList([["w", "כל מחשב בנייה/שיכפול שיעלה מעכשיו יריץ שירות צפייה מרחוק (RFB, בלי סיסמה)"],
      ["ok", "תופס באתחול הבא של כל תחנה — לא במכונות שכבר רצות"]]) + offMeansNote(PORT_OFF_MEANS.monitor),
    onSubmit: () => send({ confirm: "imagectl.monitor" }),
  });
}
/* ‏#954 גל 4: אין עוד מגירת סבב — "פרטים" מוביל ללשונית "כיתה" בדף ההפצה. */
function openRoundDetail() { selectPageById("deploy"); activateTab(classroomsOn() ? 1 : 0); }

const UI_ICON_PATHS = {
  up: '<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>',   // console-full.html n-up ("הורד גיבוי הגדרות")
  tools: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.4 2.4-2.1-.6-.6-2.1Z"/>',
  "server": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"6\" rx=\"1\"/><rect x=\"3\" y=\"14\" width=\"18\" height=\"6\" rx=\"1\"/><path d=\"M7 7h.01M7 17h.01M11 7h6M11 17h6\"/>",
  "home": "<path d=\"m3 11 9-8 9 8M5 9v12h5v-7h4v7h5V9\"/>",
  "image": "<rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"2\"/><path d=\"m3 17 6-6 4 4 3-3 5 5\"/><circle cx=\"16\" cy=\"8\" r=\"1\"/>",
  "warn": "<path d=\"M12 4 2.8 20h18.4Z\"/><path d=\"M12 10v4.5M12 17.5h.01\"/>",
  "folder": "<path d=\"M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z\"/>",
  "group": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"16\" rx=\"1\"/><path d=\"M3 10h18M9 4v16M15 4v16\"/>",
  "build": "<path d=\"m14 5 5 5M3 21l8-8M9 3l12 12-6 6L3 9Z\"/>",
  "copy": "<rect x=\"8\" y=\"8\" width=\"13\" height=\"13\" rx=\"1\"/><path d=\"M16 8V3H3v13h5\"/>",
  "machine": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"13\" rx=\"1\"/><path d=\"M8 21h8M12 17v4\"/>",
  "health": "<path d=\"M3 12h4l3-8 4 16 3-8h4\"/>",
  "network": "<path d=\"M12 7v6M5 17v-4h14v4\"/><rect x=\"9\" y=\"2\" width=\"6\" height=\"5\" rx=\"1\"/><rect x=\"2\" y=\"17\" width=\"6\" height=\"5\" rx=\"1\"/><rect x=\"16\" y=\"17\" width=\"6\" height=\"5\" rx=\"1\"/>",
  "deploy": "<path d=\"M4 6h15l-4-4M20 18H5l4 4M20 6v6M4 18v-6\"/>",
  "user": "<circle cx=\"12\" cy=\"7\" r=\"4\"/><path d=\"M4 21v-2a8 8 0 0 1 16 0v2\"/>",
  "list": "<path d=\"M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01\"/>",
  "settings": "<circle cx=\"12\" cy=\"12\" r=\"7\"/><circle cx=\"12\" cy=\"12\" r=\"2\"/><path d=\"M12 2v3M12 19v3M2 12h3M19 12h3\"/>",
  "refresh": "<path d=\"M20 7a9 9 0 1 0 1 7M20 2v6h-6\"/>",
  "search": "<circle cx=\"10\" cy=\"10\" r=\"7\"/><path d=\"m15 15 6 6\"/>",
  "bell": "<path d=\"M4 17h16l-2-3V8a6 6 0 0 0-12 0v6ZM9 21h6\"/>",
  "close": "<path d=\"m6 6 12 12M18 6 6 18\"/>",
  "help": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M9 8a3 3 0 0 1 6 0c0 2-3 2-3 5M12 17h.01\"/>",
  "sun": "<circle cx=\"12\" cy=\"12\" r=\"4\"/><path d=\"M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1 1M18 18l1 1M5 19l1-1M18 6l1-1\"/>",
  "moon": "<path d=\"M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z\"/>",
  "storage": "<ellipse cx=\"12\" cy=\"5\" rx=\"8\" ry=\"3\"/><path d=\"M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3\"/>",
  "plus": "<path d=\"M12 5v14M5 12h14\"/>",
  /* apple-pending-4 · 3: אייקוני העץ — כל אחד ייחודי (בלי כפילויות), קו 1.6, ‏currentColor */
  "t-home": '<rect x="3" y="3" width="8" height="10" rx="1.5"/><rect x="13" y="3" width="8" height="6" rx="1.5"/><rect x="13" y="11" width="8" height="10" rx="1.5"/><rect x="3" y="15" width="8" height="6" rx="1.5"/>',
  "t-deploy": '<circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2"/>',
  "t-images": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',   /* נדב 17/09: אימג'ים = תיקייה */
  "t-subfolder": '<g stroke-width="1.2"><path d="M5.5 8.5a1.5 1.5 0 0 1 1.5-1.5h2.8l1.4 1.5h5.8a1.5 1.5 0 0 1 1.5 1.5v6.5a1.5 1.5 0 0 1-1.5 1.5H7a1.5 1.5 0 0 1-1.5-1.5Z"/></g>',   /* תיקיות משנה בעץ: וריאנט קטן ודק, שלא ייראה כמו צומת האימג'ים */
  "t-machines": '<rect x="6" y="3" width="12" height="18" rx="2"/><path d="M9 7h6M9 11h6M12 17h.01"/>',
  "t-health": '<path d="M12 20.5S3.5 15.4 3.5 9.3A4.8 4.8 0 0 1 12 6.6a4.8 4.8 0 0 1 8.5 2.7c0 6.1-8.5 11.2-8.5 11.2Z"/><path d="M7 12h3l1.5-2.5 2 5 1.5-2.5h2"/>',
  "t-network": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3.2 3 3.2 15 0 18M12 3c-3.2 3-3.2 15 0 18"/>',
  "t-monitor": '<rect x="3" y="4" width="18" height="13" rx="1.5"/><path d="m10 8.5 5 2.5-5 2.5Z"/><path d="M8 21h8M12 17v4"/>',
  "t-drivers": '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4"/>',
  "t-admin": '<path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6Z"/><path d="m9 12 2 2 4-4"/>',
  "t-nic": '<rect x="4" y="6" width="16" height="11" rx="1.5"/><path d="M8 17v3M16 17v3M8 10v3M12 10v3M16 10v3"/>',
  "t-ports": '<path d="M9 3v5M15 3v5M6 8h12v4a6 6 0 0 1-12 0Z"/><path d="M12 18v3"/>',
  "t-sliders": '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
  "t-me": '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a8 8 0 0 1 16 0v1"/>',
  "t-key": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20v-1a6.5 6.5 0 0 1 13 0v1M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 5.5V20"/>',   // ‏#1300: console-full.html n-users (שני אנשים) — השם נשאר, הצורה כמו במוקאפ
  "t-log": '<path d="M6 3h9l4 4v14H6Z"/><path d="M14 3v5h5M9 13h7M9 17h5"/>',
  "t-infra": '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>'
};
function uiIcon(name) { return '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">'+(UI_ICON_PATHS[name] || UI_ICON_PATHS.list)+'</svg>'; }

function encodeId(value) { return encodeURIComponent(value).replaceAll("'", "%27"); }
