/* ‏#1334: עדכון אימג' ב-VM — הממשק. לפי המוקאפ שנדב אישר 13/10
   (C:\ImageCtl-Lab\design-approved\vmupdate) ו-docs/vm-update.md. החוזה:
   docs/interfaces.md סעיף 33. admin בלבד.

   מבנה: עמוד אחד (`pages.vmupdate` — "אימג'ים › <אימג'> › עדכון ב-VM"), פס
   גלובלי "VM רץ" בכל עמוד אחר, חלון התחלה (בחירת גרסה), וחלונות הכרעה (שמירה —
   חובה, בלי X ובלי Esc; מחיקה; כיבוי בכוח; הדבקה כטקסט; בקשת שליטה).

   קצב: אין טיימר משלו — `vmuTick` רץ מהדגימה הקיימת (startStatusWatch, 2 שנ'):
   כל דגימה כשהעמוד פתוח או שיש עדכון פעיל, אחרת פעם ב-15 שנ'. דגימה שנכשלה
   משאירה את המצב האחרון ואומרת זאת (עיקרון 5) — היא אינה "אין VM".

   הבהוב (תיקון 13/10 במוקאפ): העמוד אינו נבנה מחדש בכל דגימה. הכותרת, הפסים
   והכלים מצוירים מחדש; הכרטיס על הבמה מוחלף רק כשהשלב שלו משתנה, ובשאר הזמן
   מתעדכנים רק הפס והמספרים (`data-k`). מסך ה-VM (noVNC) אינו נוגע לעולם —
   הוא נבנה פעם אחת לכל צומת `#vmuScreen`. */

const VMU_FAST_MS = 2000;
const VMU_SLOW_MS = 15000;
const VMU_ACTIVE = ["preparing", "running", "stopping", "stopped", "saving", "failed"];
const VMU = {
  st: null, err: "", last: 0, busy: false,
  rfb: null, rfbEl: null, rfbState: "", rfbModule: null,
  dlg: "", dismissedSave: false, seenRequestAt: "", savedAt: 0, prs: { pop: false },
  fs: false,
};

const VMU_ICON = {
  vm: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/><path d="m10 8 2 2 2-2"/></svg>',
  power: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v8"/><path d="M6.3 6.3a8 8 0 1 0 11.4 0"/></svg>',
  bolt: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m13 2-8 12h6l-1 8 8-12h-6z"/></svg>',
  kbd: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg>',
  paste: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="4" width="10" height="4" rx="1"/><path d="M9 6H6a1 1 0 0 0-1 1v13a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V7a1 1 0 0 0-1-1h-3"/><path d="M9 13h6M9 17h4"/></svg>',
  full: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
  disk: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 5 5L20 7"/></svg>',
  x: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>',
  user: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21v-1a8 8 0 0 1 16 0v1"/></svg>',
  trash: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
};

/* ---------- הרשת ---------- */

/* ה-api של הקונסולה קורא `detail`/`message_he`; הסירובים כאן הם התבנית הרוחבית
   (`error`/`code`/`details`) — ולכן קריאה משלנו, דרך apiFetch (אותו "השרת לא עונה"). */
async function vmuCall(method, path, body) {
  const response = await apiFetch("/api/console/vmupdate" + path, {
    method, credentials: "same-origin", headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401) { showLogin(); throw new Error("לא מחובר"); }
  let data = null;
  try { data = await response.json(); } catch (e) { data = null; }
  if (!response.ok || (data && data.ok === false)) {
    const msg = (data && (data.error || data.detail)) || `שגיאה ${response.status}`;
    const error = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    error.code = data && data.code;
    error.details = (data && data.details) || {};
    error.status = response.status;
    noteApiFailure(error.message);
    throw error;
  }
  return data;
}

function vmuActive(st) { return !!st && VMU_ACTIVE.includes(st.state); }

async function vmuTick(force = false) {
  if (typeof isAdmin !== "function" || !isAdmin()) { if (VMU.st) { VMU.st = null; vmuGlobal(); } return; }
  const fast = current === "vmupdate" || vmuActive(VMU.st);
  const gap = fast ? VMU_FAST_MS - 100 : VMU_SLOW_MS;
  if (VMU.busy || (!force && VMU.last && Date.now() - VMU.last < gap)) return;
  VMU.busy = true;
  VMU.last = Date.now();
  const was = VMU.st && VMU.st.state;
  try {
    VMU.st = await vmuCall("GET", "/status");
    VMU.err = "";
  } catch (e) {
    VMU.err = e.message;   // עיקרון 5: המצב האחרון נשאר, והעמוד אומר שלא נקרא
  } finally {
    VMU.busy = false;
  }
  vmuGlobal();
  if (typeof renderActivity === "function") renderActivity();   // #1391: שורת ה-VM בדוק המשימות
  vmuReadyNotice(was, VMU.st);
  if (current === "vmupdate") vmuPaint();
  vmuDialogs();
}

/* ‏#1391: ההכנה הסתיימה בזמן שהמפעיל בעמוד אחר ("צמצם") — התראה. מעבר
   preparing → running בלבד: דגימה ראשונה (אין מצב קודם) אינה "עכשיו מוכן". */
function vmuReadyNotice(was, st) {
  if (was !== "preparing" || !st || st.state !== "running" || current === "vmupdate") return;
  toast(`ה-VM מוכן — ${vmuName(st)} ${vmuVersion(st)} עלה. "חזור ל-VM" בפס העליון או בדוק המשימות`.replace(/\s+/g, " "), 10000, "ok");
}

/* ---------- עזרים ---------- */

/* ‏#1933: HH:MM באזור הזמן של השרת (‏fmtClock של console.js), לא בשעון הדפדפן; ‏null = אין חותמת. */
function vmuHHMM(iso) {
  return Number.isFinite(tsMs(iso)) ? fmtClock(iso) : null;
}
function vmuClock(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(r).padStart(2, "0");
}
function vmuGB(bytes) { return bytes == null ? "—" : (Number(bytes) / 1e9).toFixed(1) + " GB"; }
function vmuDays(seconds) { const d = Math.ceil((Number(seconds) || 0) / 86400); return d <= 1 ? "יום" : `${d} ימים`; }
function vmuName(st) { return (st && st.image_name) || "אימג'"; }
function vmuVersion(st) { return st && st.image_version ? `v${st.image_version}` : ""; }
function vmuMine(st) { return !!st && st.you_control; }
/* הודעה בתוך חלון — UI.note מעוצב רק בתוך ‏.page, החלונות יושבים מחוצה לו. */
function vmuNote(kind, html) { return `<div class="n-note vmu-note ${kind}"><span>${html}</span></div>`; }
function vmuPct(done, total) { return total ? Math.max(0, Math.min(100, Math.round(100 * done / total))) : null; }
/* ‏#1391: "62% · 27.8 / 44.9 GB" — רק כשהשרת כבר יודע את הגודל הכולל; לפני כן כלום, לא 0%. */
function vmuPrepProgress(st, before = "", after = "") {
  const p = (st && st.progress) || {}, pct = vmuPct(p.done_bytes, p.total_bytes);
  return pct == null ? "" : `${before}${pct}% · <bdi dir="ltr">${vmuGB(p.done_bytes)} / ${vmuGB(p.total_bytes)}</bdi>${after}`;
}

/* ---------- הפס הגלובלי ("VM רץ") — בכל עמוד חוץ מעמוד ה-VM ---------- */

/* הפס יושב כילד ראשון של #content — לא כאח שלו: #content הוא פריט ברשת של
   ‏#app, ואח נוסף היה שובר את העמודות. עמוד שנבנה מחדש מוחק אותו, ולכן
   ‏selectPage/renderCurrent קוראים ל-vmuGlobal אחרי הציור. */
function vmuGlobalEl(create) {
  const content = document.getElementById("content");
  if (!content || typeof content.querySelector !== "function") return null;
  let el = content.querySelector(":scope > #vmuGlobal");
  if (el || !create || typeof document.createElement !== "function") return el;
  el = document.createElement("div");
  el.id = "vmuGlobal";
  content.insertBefore(el, content.firstChild);
  return el;
}
function vmuGlobalHtml(st) {
  if (!vmuActive(st) || current === "vmupdate") return "";
  const running = st.state === "running" || st.state === "stopping";
  const who = st.controller ? ` · ${esc(st.controller)}` : "";
  const what = running
    ? `<b>VM רץ:</b> ${esc(vmuName(st))} <span class="m">${vmuVersion(st)} · <bdi dir="ltr">${vmuClock(st.runtime_seconds)}</bdi>${who}</span>`
    : st.state === "preparing" ? `<b>מכין דיסק ל-VM:</b> ${esc(vmuName(st))}${vmuPrepProgress(st, " <span class=\"m\">", "</span>")}`
    : st.state === "saving" ? `<b>שומר גרסה חדשה:</b> ${esc(vmuName(st))}`
    : `<b>עדכון ב-VM ממתין להחלטה:</b> ${esc(vmuName(st))}`
      + (st.expires_in_seconds != null ? ` <span class="m">· הדיסק הזמני יימחק בעוד ${vmuDays(st.expires_in_seconds)}</span>` : "");
  return `<div class="vmu-pend" role="status"><span class="vmu-thumb" aria-hidden="true"></span><div class="tx">${what}</div>`
    + `<button class="btn sm primary" type="button" onclick="vmuOpen()">חזור ל-VM</button></div>`;
}
function vmuGlobal() {
  const html = vmuGlobalHtml(VMU.st);
  const el = vmuGlobalEl(!!html);
  if (!el) return;
  if (el.innerHTML !== html) el.innerHTML = html;
}

/* ---------- העמוד ---------- */

function vmuOpen() {
  if (current !== "vmupdate") VMU.back = current;   // ‏#1391: לאן "צמצם" מחזיר
  if (typeof selectPage === "function") selectPage(null, "vmupdate");
  vmuTick(true);
}

/* ‏#1391 (נדב 13/10): "צמצם" — ההכנה ממשיכה בשרת; חוזרים לעמוד שממנו באו (אחרת
   אימג'ים), וההתקדמות בדוק המשימות ובפס הגלובלי. כשה-VM עולה — התראה (vmuReadyNotice). */
function vmuMinimize() {
  const back = VMU.back && VMU.back !== "vmupdate" ? VMU.back : "images";
  if (typeof selectPageById === "function") selectPageById(back);
  if (typeof renderActivity === "function") renderActivity();
  const panel = document.getElementById("taskPanel");
  if (panel && panel.classList && !panel.classList.contains("open") && typeof toggleTaskPanel === "function") toggleTaskPanel();
}

/* ‏#1391: שורת ה-VM בדוק המשימות (activityRows) — כמו המוקאפ המאושר (console-full.html:
   משימה · יעד · התחלה · משך · מצב · מי התחיל). לחיצה — חזרה לעמוד ה-VM. */
const VMU_DOCK_LABEL = { preparing: "מכין דיסק", running: "רץ", stopping: "נכבה", stopped: "כבוי — ממתין להחלטה",
  saving: "שומר גרסה חדשה", failed: "נכשל — ממתין להחלטה" };
function vmuActivityRow(st = VMU.st) {
  if (!vmuActive(st)) return null;
  const p = st.progress || {};
  const pct = st.state === "preparing" || st.state === "saving" ? vmuPct(p.done_bytes, p.total_bytes) : null;
  const label = VMU_DOCK_LABEL[st.state] + (pct != null ? ` · ${pct}%` : st.state === "running" ? ` · ${vmuClock(st.runtime_seconds)}` : "");
  // הדוק מציג טקסט (esc) ולא <bdi> — שם האימג' והגדלים מבודדים בתווי בידוד (FSI/LRI…PDI),
  // אחרת "27.8 GB / 44.9 GB" ו-"Office 2024 v3" מתערבבים עם העברית שסביבם.
  const target = st.state === "preparing" && p.total_bytes ? `דיסק זמני · ⁦${vmuGB(p.done_bytes)} / ${vmuGB(p.total_bytes)}⁩` : "VM בשרת";
  return { name: `עדכון ב-VM: ⁨${`${vmuName(st)} ${vmuVersion(st)}`.trim()}⁩`, target, start: st.created_at, end: null, label,
    cls: st.state === "failed" ? "err" : st.state === "stopped" ? "" : "info", by: st.started_by || "",
    pct, open: "vmuOpen()" };
}

function vmuPageHtml() {
  return `<div class="page aligned vmu-page"><div id="vmuHead"></div><div class="body"><div id="vmuStrips" class="c12"></div>`
    + `<div class="c12 card vmu-card"><div id="vmuTool"></div><div class="vmu-stage" id="vmuStage">`
    + `<div id="vmuScreen" class="vmu-screen" tabindex="0" aria-label="מסך ה-VM"></div><div id="vmuLay" class="vmu-lay"></div></div>`
    + `<div id="vmuFoot"></div></div></div></div>`;
}

const VMU_STATE_LABEL = {
  idle: ["", "אין VM פתוח"], preparing: ["info", "מכין"], running: ["ok", "רץ"], stopping: ["warn", "נכבה"],
  stopped: ["", "כבוי"], saving: ["info", "שומר"], saved: ["ok", "נשמר"], failed: ["err", "נכשל"],
};

function vmuHeadHtml(st) {
  const [cls, label] = VMU_STATE_LABEL[st.state] || ["", st.state];
  const pill = label && st.state !== "idle" ? ` ${UI.pill(cls, label)}` : "";
  const sub = st.state === "idle" ? "אין עדכון פתוח"
    : [st.image_version ? `ממקור ${vmuVersion(st)}` : "", st.booted_at ? `התחיל ${vmuHHMM(st.booted_at)}` : "",
      st.started_by ? esc(st.started_by) : ""].filter(Boolean).join(" · ");
  const crumbs = [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" },
    { label: "אימג'ים", onclick: "selectPageById('images')" }]
    .concat(st.image_id ? [{ label: vmuName(st), onclick: `vmuOpenImage('${encodeId(st.image_id)}')` }] : [])
    .concat([{ label: "עדכון ב-VM" }]);
  return UI.objHeader({ crumbs, icon: "t-monitor", name: st.image_id ? `${vmuName(st)} — עדכון ב-VM` : "עדכון ב-VM",
    sub, pill });
}

function vmuOpenImage(idEnc) {
  selectPageById("images");
  setTimeout(() => { if (typeof openImageDetail === "function") openImageDetail(decodeURIComponent(idEnc)); }, 300);
}

/* פס הנוכחות — "גם X צופה ב-VM" (נדב 13/10; המבנה של #1339 / presence.js),
   ופס השליטה: "X עובד ב-VM מאז HH:MM — צפייה בלבד" + "קח שליטה". */
function vmuPresenceHtml(st) {
  const viewers = (st && st.viewers) || [];
  const others = viewers.filter((v) => !v.you);
  if (!others.length) return "";
  const first = others[0], more = others.length - 1;
  const addr = (v) => (v.address ? `<span class="mono">${esc(v.address)}</span>` : "כתובת לא ידועה");
  const since = (v) => (vmuHHMM(v.since) ? `מאז ${vmuHHMM(v.since)}` : "");
  const line = `גם <b>${esc(first.user)}</b> צופה ב-VM${first.controller ? " (שולט)" : ""} · מ-${addr(first)}${since(first) ? " · " + since(first) : ""}`;
  const pop = more && VMU.prs.pop
    ? `<div class="prs-pop" role="dialog" aria-label="צופים ב-VM"><h3>צופים ב-VM · ${viewers.length}</h3>`
      + viewers.map((v) => `<div class="prs-r">${VMU_ICON.user}<span class="prs-n">${esc(v.user)}</span>`
        + `${v.you ? '<span class="prs-you">אתה</span>' : ""}${v.controller ? '<span class="prs-you">שולט</span>' : ""}`
        + `${addr(v)}<span class="prs-m">${since(v)}</span></div>`).join("") + "</div>" : "";
  return `<div class="prs" role="status" aria-label="צופים נוספים ב-VM"><span class="prs-dot" aria-hidden="true"></span>${VMU_ICON.user}`
    + `<div class="prs-tx">${line}</div>`
    + (more ? `<button class="prs-chip" type="button" aria-haspopup="dialog" aria-expanded="${VMU.prs.pop}" onclick="vmuTogglePop(event)">+${more}</button>` : "")
    + pop + "</div>";
}
function vmuTogglePop(event) { if (event && event.stopPropagation) event.stopPropagation(); VMU.prs.pop = !VMU.prs.pop; vmuPaint(); }

function vmuControlHtml(st) {
  if (!st || st.state === "idle" || st.state === "saved" || !st.controller || st.you_control) return "";
  const since = vmuHHMM(st.controller_since);
  const req = st.control_request;
  const last = st.control_last;
  const mine = req && req.by === ME.username;
  const tail = mine ? `<span class="m">ממתין לאישור של ${esc(st.controller)} · ${req.seconds_left} שנ'</span>`
    : req ? `<span class="m">${esc(req.by)} ביקש שליטה</span>`
    : `<button class="btn sm" type="button" onclick="vmuRequestControl()">קח שליטה</button>`;
  const note = last && last.by === ME.username && !req
    ? (last.outcome === "denied" ? " · הבקשה שלך נדחתה" : last.outcome === "timeout" ? " · הבקשה שלך לא נענתה תוך דקה" : "") : "";
  return `<div class="vmu-ctl" role="status">${VMU_ICON.user}<div class="tx"><b>${esc(st.controller)}</b> עובד ב-VM${since ? " מאז " + since : ""} — אתם בצפייה בלבד${note}</div>${tail}</div>`;
}

function vmuChip(label, value) { return `<span class="vmu-chip">${label}${value ? ` <b class="mono">${value}</b>` : ""}</span>`; }

function vmuToolHtml(st) {
  const live = st.state === "running" || st.state === "stopping";
  if (!live) return "";
  const lock = !st.you_control || st.state === "stopping";
  const dis = lock ? " disabled" : "";
  const res = st.resources || {};
  const left = `<div class="grp"><button class="btn sm" type="button"${dis} onclick="vmuCtrlAltDel()">${VMU_ICON.kbd} Ctrl+Alt+Del</button>`
    + `<button class="btn sm" type="button"${dis} onclick="vmuPasteDialog()">${VMU_ICON.paste} הדבקה כטקסט</button>`
    + `<button class="btn sm" type="button" onclick="vmuFullscreen(true)">${VMU_ICON.full} מסך מלא</button></div>`;
  const chips = `<div class="chips">${vmuNetChip(st)}`
    + `<span class="vmu-chip">זמן ריצה <b class="mono" data-k="runtime">${vmuClock(st.runtime_seconds)}</b></span>`
    + `<span class="vmu-chip opt"><b class="mono">${res.cpus || "?"}</b> ליבות · <b class="mono">${res.memory_mib ? Math.round(res.memory_mib / 1024) : "?"} GB</b></span>`
    + `<span class="vmu-chip opt">${esc(res.accel === "kvm" ? "KVM" : res.accel === "tcg" ? "אמולציה (TCG)" : "")}</span></div>`;
  const right = st.you_control
    ? `<span class="vmu-sep"></span><div class="grp"><button class="btn sm primary" type="button"${st.state === "stopping" ? " disabled" : ""} onclick="vmuShutdown()">${VMU_ICON.power} כיבוי מסודר</button>`
      + `<button class="btn sm danger" type="button" onclick="vmuForceDialog()">${VMU_ICON.bolt} כיבוי בכוח</button></div>` : "";
  return `<div class="vmu-tool">${left}${chips}${right}</div>`;
}

function vmuFootHtml(st) {
  if (st.state !== "running" && st.state !== "stopping") return "";
  return `<div class="vmu-foot"><span>${st.you_control ? "המקלדת והעכבר עוברים ל-VM כשהסמן על המסך" : "צפייה בלבד — המקלדת והעכבר אינם עוברים"}</span>`
    + `<span><kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Del</kbd> נשלח רק מהכפתור</span>`
    + `<span class="sp"></span><span>השרת ממשיך להריץ את ה-VM גם אם תסגרו את הדפדפן</span></div>`;
}

/* --- כרטיסי הבמה (הכנה / שמירה / כשל / נשמר) --- */

function vmuSteps(rows) {
  return `<ol class="vmu-us">${rows.map((r) => `<li class="${r.cls}"><span class="si">${r.cls === "done" ? VMU_ICON.check : r.cls === "bad" ? VMU_ICON.x : r.cls === "cur" ? "<i></i>" : ""}</span>`
    + `<span><span class="stt">${esc(r.title)}</span><span class="sd">${r.sub || ""}</span></span><span class="sdur">${r.dur || ""}</span></li>`).join("")}</ol>`;
}
function vmuBar(pct, key) {
  return `<span class="vmu-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct == null ? "" : pct}"><i data-k="${key}" style="width:${pct == null ? 4 : pct}%"></i></span>`;
}
function vmuCard({ key, badge = "", badgeCls = "", title, sub = "", time = "", body = "", hint = "", foot = "" }) {
  return `<div class="vmu-uc" data-card="${esc(key)}"><div class="uh"><span class="ubadge ${badgeCls}">${badge || VMU_ICON.disk}</span>`
    + `<div class="utxt"><h2 tabindex="-1">${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ""}</div>${time}</div>${body}`
    + (hint ? `<p class="uhint">${hint}</p>` : "") + (foot ? `<div class="ufoot">${foot}</div>` : "") + "</div>";
}

function vmuPrepCard(st) {
  const p = st.progress || {}, pct = vmuPct(p.done_bytes, p.total_bytes);
  const disk = `${p.partitions ? `${p.partitions} מחיצות מהאימג'` : "שחזור המחיצות מהאימג'"}`
    + ` · <bdi dir="ltr" data-k="prepnum">${vmuGB(p.done_bytes)} / ${vmuGB(p.total_bytes)}</bdi>` + vmuBar(pct, "prepbar");
  const body = vmuSteps([
    { cls: "done", title: "בדיקת משאבים", sub: `${st.memory_mib ? Math.round(st.memory_mib / 1024) + " GB" : ""} זיכרון ו-${st.cpus || "?"} ליבות פנויים · ${st.accel === "kvm" ? "KVM" : "TCG"}` },
    { cls: "cur", title: "בניית דיסק וירטואלי", sub: "טבלת מחיצות + " + disk },
    { cls: "", title: "הפעלת ה-VM", sub: `UEFI${st.secure_boot ? " · Secure Boot" : ""} · דיסק AHCI · כרטיס e1000e` },
    { cls: "", title: "חיבור המסך", sub: "המסך ייפתח כאן לבד" },
  ]);
  return vmuCard({ key: "prep", title: "מכין את הדיסק…", sub: `בונה דיסק וירטואלי מ-${esc(vmuName(st))} ${vmuVersion(st)}`, body,
    hint: `האימג' המקורי (${vmuVersion(st)}) לא נפתח לכתיבה — ה-VM עובד על עותק זמני. אפשר לצאת מהעמוד; ההכנה ממשיכה בשרת.`,
    foot: `<span class="n-hint">ביטול עכשיו לא משנה דבר באימג'.</span>${st.you_control ? `<button class="btn" type="button" onclick="vmuCancelPrep()">ביטול</button>` : ""}`
      + `<button class="btn primary" type="button" data-testid="vmu-minimize" onclick="vmuMinimize()">צמצם</button>` });
}

const VMU_SAVE_STAGES = [["capture", "כיווץ ובדיקה", "BitLocker, מצב שינה, כיווץ NTFS — בתוך appliance נפרד"],
  ["stream", "קריאה ודחיסה", ""], ["verify", "אימות sha256", "כל קובץ נקרא שוב מהדיסק"], ["enter", "מניפסט וכניסה לספרייה", ""]];
function vmuSavingCard(st) {
  const p = st.progress || {}, at = Math.max(0, VMU_SAVE_STAGES.findIndex(([k]) => k === p.stage));
  const rows = VMU_SAVE_STAGES.map(([k, title, sub], i) => ({
    cls: i < at ? "done" : i === at ? "cur" : "", title,
    sub: k === "stream" && i === at ? `מחיצה ${p.partition || "?"}/${p.partitions || "?"} · <bdi dir="ltr" data-k="savenum">${vmuGB(p.done_bytes)}</bdi> נכתבו` : sub,
  }));
  return vmuCard({ key: "saving:" + p.stage, title: "שומר כגרסה חדשה…", sub: `${esc(vmuName(st))} — הגרסה החדשה תיכנס לספרייה רק אחרי שכל ה-sha256 תואמים`,
    body: vmuSteps(rows), hint: "אין גרסה חלקית: כשל באמצע משאיר את הדיסק הזמני ואת הגרסה הקודמת כמו שהן." });
}

function vmuSavedCard(st) {
  const r = st.result || {};
  const body = `<div class="ubody"><dl class="n-dl"><dt>גרסה חדשה</dt><dd>v${esc(r.version)} · ${esc(r.name)}</dd>`
    + `<dt>מבוססת על</dt><dd class="mono">${esc(r.previous_id || "")}</dd><dt>ברירת מחדל</dt><dd>כן — סבב חדש יקבל אותה; סבב פתוח ממשיך עם הגרסה שלו</dd></dl></div>`;
  return vmuCard({ key: "saved", badge: VMU_ICON.check, badgeCls: "ok", title: `גרסה v${r.version || "?"} נשמרה`, sub: esc(vmuName(st)), body,
    foot: `<span class="n-hint">${(st.warnings || []).map(esc).join(" · ")}</span><button class="btn primary" type="button" onclick="vmuOpenImage('${encodeId(r.image_id || "")}')">לאימג'</button>` });
}

function vmuFailCard(st) {
  const e = st.error || {}, booted = !!st.booted_at;
  const tech = `<details class="vmu-tech"><summary>פרטים טכניים</summary><pre>${esc(e.reason || "")}: ${esc(e.message || "")}</pre></details>`;
  const saveFail = ["hibernated", "unchecked", "bitlocker", "layout", "fs_changed", "shrink", "capture", "appliance", "verify", "disk_space", "table", "loop", "internal"].includes(e.reason) && booted;
  if (!st.disk_ready) {
    return vmuCard({ key: "faildisk:" + e.reason, badge: VMU_ICON.x, badgeCls: "bad",
      title: saveFail ? "השמירה נכשלה — הדיסק הזמני אינו שלם" : "הדיסק לא נבנה",
      sub: esc(vmuName(st)),
      body: `<div class="ubody">${UI.note("err", esc(e.message || "כשל לא ידוע"))}${tech}</div>`,
      hint: saveFail ? "אין גרסה חדשה. אפשר רק למחוק את הדיסק הזמני ולהתחיל מחדש מהאימג'." : "האימג' לא השתנה. אפשר לנסות שוב.",
      foot: st.you_control ? `<button class="btn danger" type="button" onclick="vmuDiscardDialog()">${VMU_ICON.trash} מחיקה</button>`
        + (saveFail ? "" : `<button class="btn primary" type="button" onclick="vmuRetry()">נסה שוב</button>`) : "" });
  }
  if (saveFail || e.reason === "hibernated") {
    const hib = e.reason === "hibernated";
    return vmuCard({ key: "failsave:" + e.reason, badge: VMU_ICON.x, badgeCls: "bad", title: "השמירה נכשלה",
      sub: `${esc(vmuName(st))} — הגרסה החדשה לא נוצרה, ואין גרסה חלקית`,
      body: `<div class="ubody">${UI.note(hib ? "warn" : "err", esc(e.message || ""))}${UI.note("info", "הדיסק הזמני נשמר — שום עבודה לא אבדה.")}${tech}</div>`,
      foot: st.you_control ? `<button class="btn danger" type="button" onclick="vmuDiscardDialog()">${VMU_ICON.trash} מחק שינויים…</button><span class="sp"></span>`
        + `<button class="btn" type="button" onclick="vmuBoot()">הפעל שוב</button><button class="btn primary" type="button" onclick="vmuSaveDialog()">נסה שוב</button>` : "" });
  }
  if (["no_lease", "macvtap", "firewall"].includes(e.reason) || String(e.reason || "").startsWith("vm_nic")) {
    return vmuCard({ key: "failnet:" + e.reason, badge: VMU_ICON.x, badgeCls: "bad",
      title: e.reason === "no_lease" ? "ה-VM לא קיבל כתובת ברשת — הוא כובה" : "ה-VM לא חובר לרשת",
      sub: `${esc(vmuName(st))} — הדיסק הזמני שלם, שום עבודה לא אבדה`,
      body: `<div class="ubody">${UI.note("err", esc(e.message || ""))}${tech}</div>`,
      hint: "אחרי התיקון — הפעל שוב. הכרטיס וה-MAC: רשת › כרטיסי רשת › כרטיס לעדכוני VM.",
      foot: `<button class="btn" type="button" onclick="openNetwork(1)">כרטיסי רשת</button><span class="sp"></span>`
        + (st.you_control ? `<button class="btn danger" type="button" onclick="vmuDiscardDialog()">${VMU_ICON.trash} מחיקה</button>`
          + `<button class="btn primary" type="button" onclick="vmuBoot()">${VMU_ICON.power} הפעל שוב</button>` : "") });
  }
  return vmuCard({ key: "failcrash:" + e.reason, badge: VMU_ICON.x, badgeCls: "bad",
    title: e.reason === "interrupted" ? "השרת הופעל מחדש באמצע" : "ה-VM נעצר בלי כיבוי",
    sub: `${esc(vmuName(st))} — השינויים בדיסק הזמני`,
    body: `<div class="ubody">${UI.note("warn", esc(e.message || ""))}${tech}</div>`,
    hint: "מומלץ להדליק שוב ולכבות מתוך Windows — כך מערכת הקבצים נשמרת נקייה.",
    foot: st.you_control ? `<button class="btn" type="button" onclick="vmuSaveDialog(true)">לשמור או למחוק…</button><span class="sp"></span>`
      + `<button class="btn primary" type="button" onclick="vmuBoot()">${VMU_ICON.power} הפעל שוב</button>` : "" });
}

function vmuStoppedCard(st) {
  return vmuCard({ key: "stopped", badge: VMU_ICON.power, title: "ה-VM כבוי — ממתין להחלטה",
    sub: `${esc(vmuName(st))}${st.expires_in_seconds != null ? ` · הדיסק הזמני יימחק בעוד ${vmuDays(st.expires_in_seconds)} אם לא יוכרע` : ""}`,
    foot: st.you_control ? `<button class="btn" type="button" onclick="vmuBoot()">הפעל שוב</button><span class="sp"></span><button class="btn primary" type="button" onclick="vmuSaveDialog()">לשמור או למחוק…</button>`
      : `<span class="n-hint">${esc(st.controller || "")} מחליט — אתם בצפייה בלבד</span>` });
}

function vmuIdleCard() {
  return vmuCard({ key: "idle", badge: VMU_ICON.vm, title: "אין VM פתוח",
    sub: "עדכון ב-VM נפתח מהאימג' עצמו: אימג'ים › ⋯ › עדכן ב-VM…",
    foot: `<button class="btn primary" type="button" onclick="selectPageById('images')">לאימג'ים</button>` });
}

function vmuLayHtml(st) {
  switch (st.state) {
    case "preparing": return vmuPrepCard(st);
    case "saving": return vmuSavingCard(st);
    case "saved": return vmuSavedCard(st);
    case "failed": return vmuFailCard(st);
    case "stopped": return vmuStoppedCard(st);
    case "stopping": return `<div class="vmu-pillmsg">${VMU_ICON.power} נשלחה בקשת כיבוי (ACPI) — Windows מסיים לעבוד · <bdi dir="ltr" data-k="waited">${vmuClock(st.shutdown_waited_seconds)}</bdi>`
      + (st.force_offered && st.you_control ? ` · עברו 15 דק' <button class="btn sm" type="button" onclick="vmuForceDialog()">כיבוי בכוח…</button>` : "") + "</div>";
    case "running": return "";
    default: return vmuIdleCard();
  }
}

/* כרטיס על הבמה מוחלף רק כשהמפתח שלו (השלב) משתנה; אחרת — רק פסים ומספרים. */
function vmuPaintLay(lay, html) {
  const box = typeof document.createElement === "function" ? document.createElement("div") : null;
  if (!box) { lay.innerHTML = html; return; }
  box.innerHTML = html;
  const nextCard = box.firstElementChild, prevCard = lay.firstElementChild;
  const key = (el) => (el && el.getAttribute ? el.getAttribute("data-card") || el.className : "");
  if (nextCard && prevCard && key(nextCard) === key(prevCard) && nextCard.getAttribute("data-card")) {
    for (const fresh of nextCard.querySelectorAll("[data-k]")) {
      const old = prevCard.querySelector(`[data-k="${fresh.getAttribute("data-k")}"]`);
      if (!old) continue;
      if (fresh.style && fresh.style.width) old.style.width = fresh.style.width;
      else if (old.textContent !== fresh.textContent) old.textContent = fresh.textContent;
    }
    return;
  }
  lay.innerHTML = html;
  if (nextCard && lay.firstElementChild && lay.firstElementChild.classList) lay.firstElementChild.classList.add("still");
}

function vmuPaint() {
  const head = document.getElementById("vmuHead");
  if (!head) return;
  const st = VMU.st || { state: "idle" };
  head.innerHTML = vmuHeadHtml(st);
  const strips = document.getElementById("vmuStrips");
  if (strips) strips.innerHTML = (VMU.err ? UI.note("warn", `מצב ה-VM לא נקרא בדגימה האחרונה (${esc(VMU.err)}) — מוצג המצב האחרון שנקרא`) : "")
    + vmuControlHtml(st) + vmuPresenceHtml(st);
  const tool = document.getElementById("vmuTool");
  if (tool) tool.innerHTML = vmuToolHtml(st);
  const foot = document.getElementById("vmuFoot");
  if (foot) foot.innerHTML = vmuFootHtml(st);
  const stage = document.getElementById("vmuStage");
  const live = st.state === "running" || st.state === "stopping";
  if (stage && stage.classList) stage.classList.toggle("cardmode", !live);
  const lay = document.getElementById("vmuLay");
  if (lay) vmuPaintLay(lay, vmuLayHtml(st));
  vmuScreen(st);
}

/* ---------- מסך ה-VM — noVNC (MPL-2.0, server/static/vendor/novnc) ---------- */

async function vmuRfbClass() {
  if (VMU.rfbModule) return VMU.rfbModule;
  const mod = await import("./vendor/novnc/core/rfb.js");
  VMU.rfbModule = mod.default;
  return VMU.rfbModule;
}
function vmuWsUrl() {
  return (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/console/vmupdate/viewer";
}
function vmuDisconnect() {
  const rfb = VMU.rfb;
  VMU.rfb = null; VMU.rfbEl = null; VMU.rfbState = "";
  if (rfb) { try { rfb.disconnect(); } catch (e) { /* כבר סגור */ } }
}
async function vmuReconnectAfterDisconnect() {
  if (current !== "vmupdate") return;
  let response;
  try {
    response = await apiFetch("/api/console/vmupdate/status", {credentials: "same-origin"});
  } catch (e) {
    setTimeout(() => vmuScreen(VMU.st || {}), 2000);
    return;
  }
  if ([401, 403, 409].includes(response.status)) {
    const reasons = {401: "החיבור למסך נסגר: יש להתחבר מחדש",
                     403: "החיבור למסך נסגר: אין הרשאה לצפייה",
                     409: "החיבור למסך נסגר: המסך פתוח בחיבור אחר"};
    VMU.rfbState = "auth-closed";
    toast(reasons[response.status]);
    if (response.status === 401) showLogin();
    return;
  }
  if (!response.ok) {
    setTimeout(() => vmuScreen(VMU.st || {}), 2000);
    return;
  }
  const latest = await response.json();
  VMU.st = latest;
  if (latest && (latest.state === "running" || latest.state === "stopping"))
    setTimeout(() => vmuScreen(latest), 2000);
}
async function vmuScreen(st) {
  const el = document.getElementById("vmuScreen");
  const live = st && (st.state === "running" || st.state === "stopping");
  if (!el || !live) { if (VMU.rfb && (!el || !live)) vmuDisconnect(); return; }
  if (VMU.rfb && VMU.rfbEl === el) { VMU.rfb.viewOnly = !st.you_control; return; }
  if (VMU.rfbState === "connecting") return;
  vmuDisconnect();
  VMU.rfbState = "connecting";
  let RFB;
  try { RFB = await vmuRfbClass(); } catch (e) { VMU.rfbState = ""; toast("לקוח המסך (noVNC) לא נטען: " + e.message); return; }
  if (document.getElementById("vmuScreen") !== el) { VMU.rfbState = ""; return; }
  const rfb = new RFB(el, vmuWsUrl(), { shared: true });
  rfb.scaleViewport = true;
  rfb.resizeSession = false;
  rfb.viewOnly = !st.you_control;
  rfb.focusOnClick = true;
  rfb.addEventListener("connect", () => { VMU.rfbState = "on"; });
  rfb.addEventListener("disconnect", () => {
    if (VMU.rfb === rfb) { VMU.rfb = null; VMU.rfbEl = null; VMU.rfbState = ""; }
    vmuReconnectAfterDisconnect();
  });
  VMU.rfb = rfb; VMU.rfbEl = el;
}

function vmuCtrlAltDel() { if (VMU.rfb && vmuMine(VMU.st)) VMU.rfb.sendCtrlAltDel(); }

/* הדבקה כטקסט = הקלדה מדומה (פריסת US): כל תו הוא keysym, ולכן רק ASCII. */
const VMU_PASTE_MAX = 2000;
function vmuKeysyms(text) {
  const out = [];
  for (const ch of String(text)) {
    if (ch === "\n") out.push(0xff0d);
    else if (ch === "\t") out.push(0xff09);
    else if (ch === "\r") continue;
    else {
      const code = ch.codePointAt(0);
      if (code < 0x20 || code > 0x7e) return null;
      out.push(code);
    }
  }
  return out;
}
function vmuType(text) {
  const keys = vmuKeysyms(text);
  if (!keys) throw new Error("רק תווים באנגלית (פריסת US) — ההדבקה היא הקלדה מדומה");
  if (!VMU.rfb) throw new Error("המסך לא מחובר");
  for (const k of keys) VMU.rfb.sendKey(k);
  return keys.length;
}

function vmuFullscreen(on) {
  const stage = document.getElementById("vmuStage");
  if (!stage) return;
  if (on && stage.requestFullscreen) stage.requestFullscreen().catch(() => {});
  else if (!on && document.exitFullscreen && document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

/* ---------- חלונות ---------- */

/* חלון בתבנית של sheet() (‏#sheet בתוך #modal). ‏`locked` — אין X, אין Esc ואין
   סגירה ברקע: חלון השמירה הוא החלטה מחייבת (המוקאפ). */
function vmuWin({ title, sub = "", icon = "", iconCls = "", body = "", foot = "", size = "n-narrow", locked = false, onOpen = null, key = "" }) {
  const modal = document.getElementById("modal"), form = document.getElementById("sheet");
  if (!modal || !form) return;
  VMU.dlg = key;
  modal.dataset.locked = locked ? "1" : "";
  form.className = `sheet win ${size}`;
  form.onsubmit = (e) => e.preventDefault();
  form.innerHTML = `<div class="n-h shead">${icon ? `<span class="n-hic ${iconCls}">${icon}</span>` : ""}<div><h2 id="sheetTitle">${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ""}</div>`
    + (locked ? "" : `<button class="n-x" type="button" aria-label="סגור" onclick="vmuCloseWin()">${VMU_ICON.x.replace("<svg", '<svg class="n-ic"')}</button>`)
    + `</div><div class="n-b sbody">${body}<p class="error n-formerr" id="vmuErr" role="alert"></p></div><footer class="n-f-bar sfoot">${foot}</footer>`;
  form.classList.remove("hidden");
  openModalFrame(modal, "sheet-mode");
  if (onOpen) onOpen(form);
}
function vmuCloseWin() {
  const modal = document.getElementById("modal");
  if (modal) modal.dataset.locked = "";
  VMU.dlg = "";
  closeModal();
}
function vmuWinError(message) { const el = document.getElementById("vmuErr"); if (el) el.textContent = message; }
async function vmuAct(fn, okMessage = "") {
  try {
    const st = await fn();
    if (st && st.state) VMU.st = st;
    if (okMessage) toast(okMessage, 2600, "ok");
    vmuGlobal();
    if (current === "vmupdate") vmuPaint();
    return true;
  } catch (e) {
    vmuWinError(e.message);
    if (!document.getElementById("vmuErr")) toast(e.message);
    return false;
  }
}

/* --- התחלה: בחירת גרסת מקור --- */

async function vmuStartDialog(idEnc) {
  const id = decodeURIComponent(idEnc);
  let versions = null, pre = null;
  try {
    [versions, pre] = await Promise.all([api("/images/" + encodeId(id) + "/versions", { primary: true }),
      vmuCall("GET", "/precheck?image_id=" + encodeURIComponent(id)).catch((e) => ({ ok: false, code: e.code, error: e.message, details: e.details }))]);
  } catch (e) { toast(e.message); return; }
  const rows = versions.versions || [];
  const chosen = rows.some((v) => v.id === id) ? id : (versions.latest_id || id);
  const head = rows.find((v) => v.id === chosen) || {};
  const opt = (v, i) => {
    const prev = rows[i + 1];
    const how = v.origin === "vm" ? "עודכן ב-VM" : "נקלט ממחשב בנייה";
    const chips = (v.id === versions.latest_id ? '<span class="n-chip i">הכי עדכנית</span>' : "")
      + (v.is_default ? '<span class="n-chip">ברירת מחדל</span>' : "")
      + (v.open_rounds ? `<span class="n-chip n">בסבב ${v.open_rounds}</span>` : "");
    const what = v.description ? esc(v.description) : prev ? `מבוסס על v${prev.version}` : "גרסה ראשונה";
    const line2 = [what, how, esc(v.created_by || ""), vmuHHMM(v.created) ? fmtDate(v.created) : "", vmuGB(v.total_compressed_bytes)].filter(Boolean).join(" · ");
    return `<label class="vmu-vopt" title="${esc(stripTags(line2))}"><input type="radio" name="vmuSrc" value="${esc(v.id)}"${v.id === chosen ? " checked" : ""}><span class="rd"></span>`
      + `<span class="vt"><span class="l1"><b>v${esc(v.version)}</b> · <span class="nm">${esc(v.name)}</span>${chips}</span><small>${line2}</small></span></label>`;
  };
  const busy = pre && pre.code === "busy", nokvm = pre && pre.code === "no_kvm";
  const refuse = pre && pre.ok === false;
  const res = pre && pre.ok ? pre : null;
  const resources = `<div class="vmu-res"><div class="rt"><b><bdi dir="ltr">${res ? Math.round(res.memory_mib / 1024) + " GB" : "—"}</bdi></b><small>זיכרון</small></div>`
    + `<div class="rt"><b>${res ? res.cpus + " ליבות" : "—"}</b><small>מעבד</small></div>`
    + `<div class="rt"><b><bdi dir="ltr">${res ? vmuGB(res.disk_bytes) : "—"}</bdi></b><small>דיסק זמני · בגודל הדיסק המקורי</small></div></div>`;
  const problem = busy
    ? vmuNote("warn", `כבר פתוח עדכון ב-VM: <b>${esc(pre.details.image_name || "")}</b>${pre.details.controller ? " · " + esc(pre.details.controller) : ""} — VM אחד בכל פעם. ${UI.link("עבור ל-VM הפעיל", "vmuCloseWin();vmuOpen()")}`)
    : nokvm ? vmuNote("bad", `${esc(pre.error)}<br>הדליקו ב-BIOS של השרת את Intel VT-x / AMD-V (SVM) ואתחלו — בלי זה ה-VM לא יופעל.`)
    : refuse ? vmuNote("bad", esc(pre.error)) : "";
  const body = `${problem}<div class="n-sec"><h3>גרסת מקור</h3><div class="vmu-vsel">${rows.map(opt).join("")}</div></div>`
    + `<div class="n-sec"><h3>משאבים</h3>${resources}</div>`
    + `<div class="n-sec">${vmuNote("info", "<b>ל-VM יש אינטרנט דרך כרטיס הניהול</b> — בשביל Windows Update. רשת ההפצה והשרת עצמו חסומים בפני ה-VM, וסבב שרץ לא מושפע.")}</div>`
    + `<div class="n-sec"><h3>מה יקרה בסוף</h3><ol class="vmu-ol"><li>מכבים את Windows מתוכו, או בכפתור "כיבוי מסודר".</li>`
    + `<li>בוחרים: <b>לשמור כגרסה חדשה</b> או למחוק את השינויים.</li><li>הגרסה שנבחרה נשמרת כמו שהיא בכל מקרה.</li></ol></div>`;
  const foot = `<span class="n-hint">ההכנה לוקחת כמה דקות · אפשר לצאת מהעמוד בינתיים</span><button class="btn" type="button" onclick="vmuCloseWin()">ביטול</button>`
    + (busy ? `<button class="btn primary" type="button" onclick="vmuCloseWin();vmuOpen()">עבור ל-VM הפעיל</button>`
      : `<button class="btn primary" type="button" id="vmuGo"${refuse ? " disabled" : ""} onclick="vmuStart()">${VMU_ICON.power} הפעל VM</button>`);
  vmuWin({ key: "start", title: "עדכון ב-VM", sub: `${esc(head.name || "")} · ${esc(imageOsLabel ? imageOsLabel("windows") : "")}`, icon: VMU_ICON.vm, body, foot, size: "n-mid" });
}
async function vmuStart() {
  const sel = document.querySelector('input[name="vmuSrc"]:checked');
  if (!sel) { vmuWinError("בחרו גרסת מקור"); return; }
  const ok = await vmuAct(() => vmuCall("POST", "/start", { image_id: sel.value }));
  if (ok) { vmuCloseWin(); vmuOpen(); }
}

/* --- כיבוי בכוח --- */

function vmuForceDialog() {
  vmuWin({ key: "force", title: "לכבות את ה-VM בכוח?", icon: VMU_ICON.bolt, iconCls: "d",
    sub: "כמו ניתוק חשמל — Windows לא מסיים את מה שהוא עושה",
    body: impactList([["b", "עדכון שמותקן עכשיו עלול להיקטע — Windows יחזור אחורה בהדלקה הבאה"],
      ["b", "מערכת הקבצים עלולה להישאר לא נקייה — השמירה תסרב עד שתדליקו ותכבו כרגיל"],
      ["w", "הדיסק הזמני נשאר: אפשר להדליק שוב ולהמשיך"]]),
    foot: `<span class="n-hint"></span><button class="btn" type="button" onclick="vmuCloseWin()">חזרה</button><button class="btn danger" type="button" onclick="vmuForce()">כיבוי בכוח</button>` });
}
async function vmuForce() { if (await vmuAct(() => vmuCall("POST", "/force-off"))) vmuCloseWin(); }
async function vmuShutdown() { await vmuAct(() => vmuCall("POST", "/shutdown"), "נשלחה בקשת כיבוי ל-Windows"); }
async function vmuBoot() { if (await vmuAct(() => vmuCall("POST", "/boot"))) { VMU.dismissedSave = false; vmuCloseWin(); } }
async function vmuCancelPrep() { await vmuAct(() => vmuCall("POST", "/discard"), "ההכנה מבוטלת"); }
async function vmuRetry() {
  const st = VMU.st || {};
  const image = st.image_id;
  if (!(await vmuAct(() => vmuCall("POST", "/discard")))) return;
  if (image) vmuStartDialog(encodeId(image));
}
async function vmuRequestControl() { await vmuAct(() => vmuCall("POST", "/control/request")); }
async function vmuAnswer(approve) { if (await vmuAct(() => vmuCall("POST", "/control/answer", { approve }))) vmuCloseWin(); }

/* --- שמירה (חובה) ומחיקה --- */

function vmuDefaultName(now = Date.now()) {
  const [y, m, d] = zoneDay(now).split("-");   // #1933: היום של השרת, לא של הדפדפן
  return `Windows Updates ${d}-${m}-${y}`;
}
function vmuSaveDialog(afterCrash = false) {
  const st = VMU.st || {};
  const forced = afterCrash || st.forced || (st.error && st.error.reason === "qemu_exit");
  const next = st.image_version ? `v${Number(st.image_version) + 1}` : "גרסה חדשה";
  const res = st.resources || {};
  const warn = forced ? vmuNote("warn", "ה-VM כובה בכוח או קרס — Windows לא סיים לכבות. מומלץ <b>להפעיל שוב</b> ולכבות מתוכו; שמירה עכשיו תסורב אם מערכת הקבצים לא נקייה.") : "";
  const body = `${warn}<div class="n-f"><label for="vmuName">שם הגרסה</label><input class="n-in" id="vmuName" maxlength="48" value="${esc(vmuDefaultName())}"></div>`
    + `<div class="n-f"><label for="vmuDesc">תיאור <span class="muted">(לא חובה)</span></label><textarea class="n-in" id="vmuDesc" rows="2" maxlength="500" placeholder="מה הותקן — למשל: עדכון מצטבר 2026-10, Defender, ‎.NET"></textarea></div>`
    + `<div class="n-sec"><h3>סיכום</h3><dl class="n-dl"><dt>גרסה חדשה</dt><dd>${esc(next)} · מבוססת על ${esc(vmuVersion(st))}</dd>`
    + `<dt>זמן ב-VM</dt><dd>${st.runtime_seconds != null ? fmtSpan(st.runtime_seconds) || "—" : "—"}</dd>`
    + `<dt>נכתב לדיסק</dt><dd class="mono">${vmuGB(res.disk_allocated_bytes)}</dd>`
    + `<dt>${esc(vmuVersion(st) || "המקור")}</dt><dd>נשמרת כמו שהיא${st.image_version ? "" : ""}</dd></dl></div>`;
  const foot = `<button class="btn vmu-ghost-dng" type="button" onclick="vmuDiscardDialog()">${VMU_ICON.trash} לבטל ולמחוק את השינויים…</button><span class="sp"></span>`
    + (forced ? `<button class="btn" type="button" onclick="vmuBoot()">הפעל שוב</button>` : "")
    + `<button class="btn primary" type="button" onclick="vmuSave()">שמור כ-${esc(next)}</button>`;
  vmuWin({ key: "save", title: "לשמור כגרסה חדשה?", sub: `ה-VM כבוי · ${esc(vmuName(st))}`, icon: VMU_ICON.disk, body, foot, locked: true });
}
async function vmuSave() {
  const name = (document.getElementById("vmuName") || {}).value || "";
  const description = (document.getElementById("vmuDesc") || {}).value || "";
  if (await vmuAct(() => vmuCall("POST", "/save", { name, description }))) vmuCloseWin();
}
function vmuDiscardDialog() {
  const st = VMU.st || {};
  vmuWin({ key: "discard", title: "למחוק את השינויים?", icon: VMU_ICON.trash, iconCls: "d", sub: esc(vmuName(st)),
    body: impactList([["b", "הדיסק הזמני וכל מה שהותקן ב-VM יימחקו"], ["b", "הפעולה אינה הפיכה"],
      ["ok", `${esc(vmuVersion(st) || "האימג'")} נשאר כמו שהוא — סבבים לא מושפעים`]]),
    locked: true,
    foot: `<button class="btn" type="button" onclick="${st.state === "stopped" || st.state === "failed" ? "vmuSaveDialog()" : "vmuCloseWin()"}">חזרה</button><span class="sp"></span><button class="btn danger" type="button" onclick="vmuDiscard()">מחק שינויים</button>` });
}
async function vmuDiscard() {
  if (await vmuAct(() => vmuCall("POST", "/discard"), "השינויים נמחקו")) { VMU.dismissedSave = false; vmuCloseWin(); }
}

/* --- הדבקה כטקסט --- */

function vmuPasteDialog() {
  vmuWin({ key: "paste", title: "הדבקה כטקסט", icon: VMU_ICON.paste,
    sub: "הטקסט מוקלד ל-VM תו אחרי תו",
    body: `<div class="n-f"><textarea class="n-in mono" id="vmuPaste" dir="ltr" rows="6" maxlength="${VMU_PASTE_MAX}" oninput="vmuPasteCount()"></textarea>`
      + `<div class="n-help"><span id="vmuPasteN">0</span> / ${VMU_PASTE_MAX}</div></div>`
      + vmuNote("warn", "פריסת US בלבד: אותיות אנגליות, ספרות וסימנים. אל תדביקו סיסמאות שאינן של ה-VM — הן עוברות ברשת כהקשות."),
    foot: `<span class="n-hint"></span><button class="btn" type="button" onclick="vmuCloseWin()">ביטול</button><button class="btn primary" type="button" onclick="vmuPasteSend()">הקלד ב-VM</button>`,
    onOpen: () => { const t = document.getElementById("vmuPaste"); if (t && t.focus) t.focus(); } });
}
function vmuPasteCount() {
  const t = document.getElementById("vmuPaste"), n = document.getElementById("vmuPasteN");
  if (t && n) n.textContent = String(t.value.length);
}
function vmuPasteSend() {
  const t = document.getElementById("vmuPaste");
  try { const n = vmuType(t ? t.value : ""); vmuCloseWin(); toast(`הוקלדו ${n} תווים`, 2600, "ok"); }
  catch (e) { vmuWinError(e.message); }
}

/* --- בקשת שליטה (אצל השולט) והחלטה אחרי כיבוי --- */

function vmuDialogs() {
  const st = VMU.st;
  if (!st) return;
  const req = st.control_request;
  if (st.you_control && req && VMU.dlg !== "control") {
    vmuWin({ key: "control", title: `${req.by} מבקש שליטה ב-VM`, icon: VMU_ICON.user,
      sub: "אם תאשרו, המקלדת והעכבר יעברו אליו ואתם תעברו לצפייה",
      body: `<p>בלי תשובה תוך <b id="vmuReqLeft">${req.seconds_left}</b> שניות הבקשה נדחית.</p>`,
      foot: `<span class="n-hint"></span><button class="btn" type="button" onclick="vmuAnswer(false)">דחה</button><button class="btn primary" type="button" onclick="vmuAnswer(true)">אשר</button>`,
      locked: true });
    return;
  }
  if (VMU.dlg === "control") {
    if (!req || !st.you_control) vmuCloseWin();
    else { const left = document.getElementById("vmuReqLeft"); if (left) left.textContent = String(req.seconds_left); }
    return;
  }
  if (current === "vmupdate" && st.state === "stopped" && st.you_control && !VMU.dlg && !VMU.dismissedSave) {
    VMU.dismissedSave = true;   // פעם אחת לכל כיבוי; ‏"לשמור או למחוק…" בכרטיס פותח שוב
    vmuSaveDialog(!!st.forced);
  }
  if (st.state !== "stopped" && st.state !== "failed") VMU.dismissedSave = false;
  if (VMU.dlg === "save" && !["stopped", "failed"].includes(st.state)) vmuCloseWin();
}

/* ---------- נקודות הכניסה מעמוד האימג'ים ---------- */

/* פריט "עדכן ב-VM…" בתפריט ⋯ של שורת אימג' — [תווית, onclick, סוג]. ‏Linux
   וגרסה שה-sha256 שלה לא תואם — כבויים, עם הסיבה (המוקאפ). */
function vmuImageMenuItem(img) {
  if (!isAdmin()) return null;
  const idEnc = encodeId(img.id);
  if (img.os !== "windows") return ["עדכן ב-VM… (רק לאימג' Windows)", "", "disabled"];
  if (img.last_scrub && img.last_scrub.state === "drift") return ["עדכן ב-VM… (אמת קודם — sha256 לא תואם)", "", "disabled"];
  return ["עדכן ב-VM…", `vmuStartDialog('${idEnc}')`];
}
/* כרטיס "עדכון ב-VM" במגירת האימג' + הגרסאות עם "קבע כברירת מחדל". */
function vmuDrawerHtml(img) {
  if (!isAdmin()) return "";
  const item = vmuImageMenuItem(img);
  const st = VMU.st;
  const here = st && vmuActive(st) && st.lineage && img.lineage === st.lineage;
  const btn = here ? `<button class="btn primary" type="button" onclick="closeDrawer();vmuOpen()">חזור ל-VM</button>`
    : item && item[2] !== "disabled" ? `<button class="btn" type="button" onclick="vmuStartDialog('${encodeId(img.id)}')">${VMU_ICON.vm} עדכן ב-VM…</button>`
    : `<span class="muted">${esc(item ? item[0].replace(/^עדכן ב-VM… /, "") : "")}</span>`;
  const text = here
    ? `עדכון פתוח (${esc(st.state)}) — ${esc(st.controller || "")}${st.expires_in_seconds != null ? ` · הדיסק הזמני יימחק בעוד ${vmuDays(st.expires_in_seconds)}` : ""}`
    : "פתיחת האימג' כמכונה וירטואלית בשרת: Windows Update, ושמירה כגרסה חדשה. הגרסה הנוכחית נשמרת.";
  return `<div><div class="sec">גרסאות</div><div class="vmu-dcard">${VMU_ICON.vm}<span>${text}</span>${btn}</div>`
    + `<div id="vmuVersions" data-image="${esc(img.id)}" class="vmu-vers"><span class="muted">טוען גרסאות…</span></div></div>`;
}
async function vmuLoadVersions(id) {
  const box = document.getElementById("vmuVersions");
  if (!box) return;
  let data;
  try { data = await api("/images/" + encodeId(id) + "/versions", { primary: true }); }
  catch (e) { box.innerHTML = UI.note("warn", "הגרסאות לא נקראו: " + esc(e.message)); return; }
  if (document.getElementById("vmuVersions") !== box) return;
  box.innerHTML = vmuVersionsHtml(data);
}
function vmuVersionsHtml(data) {
  const rows = (data.versions || []).map((v) => [
    `<b>v${esc(v.version)}</b>`,
    `${esc(v.name)}<small class="muted" style="display:block">${v.origin === "vm" ? "עודכן ב-VM" : "נקלט ממחשב בנייה"} · ${esc(v.created_by || "")}${v.open_rounds ? ` · בשימוש בסבב (${v.open_rounds})` : ""}</small>`,
    v.is_default ? UI.pill("ok", "ברירת מחדל") : `<button class="btn sm" type="button" onclick="vmuMakeDefault('${encodeId(v.id)}')">קבע כברירת מחדל</button>`,
  ]);
  return UI.datagrid({ columns: ["גרסה", "שם", { html: '<span class="sr">ברירת מחדל</span>' }], rows });
}
async function vmuMakeDefault(idEnc) {
  try {
    const data = await put("/images/" + idEnc + "/default", {});
    const box = document.getElementById("vmuVersions");
    if (box) box.innerHTML = vmuVersionsHtml(data);
    toast("ברירת המחדל לסבב חדש שונתה — אף גרסה לא נמחקה", 3200, "ok");
    if (typeof loadImages === "function") loadImages();
  } catch (e) { toast(e.message); }
}

if (typeof pages === "object" && pages) {
  pages.vmupdate = { crumb: "עדכון ב-VM", title: "עדכון ב-VM", tabs: [], own: true,
    render: () => vmuPageHtml(), load: () => { vmuPaint(); return vmuTick(true); } };
}
if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
  document.addEventListener("click", (e) => {
    if (VMU.prs.pop && !(e.target && e.target.closest && e.target.closest(".prs-pop"))) { VMU.prs.pop = false; if (current === "vmupdate") vmuPaint(); }
  });
}

/* ---------- ‏#1468: "כרטיס לעדכוני VM" (לשונית כרטיסי רשת) ----------
   ל-VM זהות משלו: macvtap על הכרטיס שנבחר, עם MAC שהשרת ייצר פעם אחת. כאן:
   הבחירה (sheet), ה-MAC עם ההנחיה לכתובת שמורה, והכתובת שה-VM קיבל בפועל.
   ‏GET/PUT ‏/vmupdate/network (docs/interfaces.md סעיף 33). לא נקרא = "לא נקרא". */
let VMNET = null, VMNET_ERR = "";

async function vmnetLoad() {
  try {
    const v = await vmuCall("GET", "/network");
    if (!v || typeof v.mac !== "string") throw new Error("תשובה שאינה הגדרת הרשת של ה-VM");
    VMNET = v; VMNET_ERR = "";
  } catch (e) { VMNET = null; VMNET_ERR = e.message; }
  return VMNET;
}

function vmnetCardHtml() {
  if (!VMNET) return `<div class="c12">${UI.note("warn", `‏/vmupdate/network לא נקרא${VMNET_ERR ? `: ${esc(VMNET_ERR)}` : ""} — הכרטיס וה-MAC של ה-VM אינם ידועים`)}</div>`;
  const v = VMNET, lease = v.lease;
  const nic = v.interface
    ? `<b class="mono">${esc(v.interface)}</b>`
    : `<span class="muted">לא נבחר — ה-VM לא יודלק</span>`;
  const got = lease
    ? `<b class="mono">${esc(lease.address)}</b> <span class="muted">· ${esc(vmuHHMM(lease.at) || "")}${lease.server ? ` · מ-DHCP ${esc(lease.server)}` : ""}</span>`
    : `<span class="muted">עוד לא נראתה — תוצג אחרי שה-VM יקבל כתובת</span>`;
  const kv = UI.kv([
    ["כרטיס", nic],
    ["MAC של ה-VM", `<b class="mono" data-testid="vmnet-mac">${esc(v.mac)}</b> <button class="btn sm" type="button" onclick="vmnetCopy()">העתקה</button>`],
    ["הכתובת שה-VM קיבל", got],
  ]);
  const minutes = Math.round((v.lease_timeout_seconds || 300) / 60);
  const notes = (v.problem ? UI.note("err", esc(v.problem.error)) : "")
    + UI.note("info", `צרו ב-DHCP של הרשת הזאת <b>כתובת שמורה</b> ל-MAC <b class="mono">${esc(v.mac)}</b> — כך חומת האש של המכללה תזהה את ה-VM לפי הכתובת שלו. ה-MAC קבוע: הוא לא משתנה באתחול או בעדכון של השרת.`)
    + UI.note("info", `ה-VM מקבל כתובת בעצמו (DHCP). את השרת עצמו הוא אינו רואה ברשת הזאת; מה שעובר דרך הנתב — חומת האש של המכללה צריכה לחסום. אם תוך ${minutes} דק' מההדלקה לא תגיע כתובת — הוא נכבה, וההודעה אומרת למה. ${esc(v.hint || "")}`);
  return UI.card({ title: "כרטיס לעדכוני VM", cls: "c12",
    acts: `<button class="btn sm" type="button" onclick="vmnetChoose()">בחירה…</button>`,
    body: kv + notes });
}

function vmnetCopy() {
  const mac = VMNET && VMNET.mac;
  if (!mac) return;
  try { navigator.clipboard.writeText(mac).then(() => toast("ה-MAC הועתק"), () => toast(mac)); } catch (e) { toast(mac); }
}

function vmnetChoose() {
  const v = VMNET || {};
  const nics = (typeof NETW !== "undefined" && Array.isArray(NETW.nics)) ? NETW.nics : [];
  const options = [{ value: "", label: "— לא נבחר (ה-VM לא יודלק) —" }].concat(nics
    .filter((n) => n.name && n.name !== "lo" && n.name !== "imagectl-vm0")
    .map((n) => ({ value: n.name, label: n.name === v.deploy_interface ? `${n.name} — כרטיס ההפצה (לא ניתן)` : n.name })));
  sheet({ title: "כרטיס לעדכוני VM", sub: "הרשת שבה ה-VM לעדכון יקבל כתובת משלו",
    fields: [{ id: "nic", type: "select", label: "כרטיס", value: v.interface || "", options,
      help: "כרטיס ההפצה אינו ניתן. כרטיס הניהול מותר — גם כשהוא רשת הכיתות." }],
    note: UI.note("info", `ה-MAC של ה-VM: <b class="mono">${esc(v.mac || "")}</b> — לכתובת שמורה ב-DHCP.`),
    onSubmit: async (values) => {
      const chosen = values.nic || null;
      VMNET = await vmuCall("PUT", "/network", { interface: chosen });
      toast(chosen ? `ה-VM יתחבר דרך ${chosen}` : "הבחירה בוטלה");
      if (typeof renderCurrent === "function") renderCurrent();
    } });
}

/* במסך ה-VM: הכתובת שה-VM קיבל, או כמה נשאר לחכות לה. */
function vmuNetChip(st) {
  const net = st.network;
  if (!net) return vmuChip("כרטיס", esc(st.egress_interface || ""));
  const where = ` · <b class="mono">${esc(net.interface || "")}</b>`;
  if (net.state === "leased") return `<span class="vmu-chip"><span class="vmu-led"></span>כתובת <b class="mono">${esc(net.address)}</b>${where}</span>`;
  if (net.state === "no_lease") return `<span class="vmu-chip">לא התקבלה כתובת${where}</span>`;
  const left = net.seconds_left != null ? ` · עוד <b class="mono">${vmuClock(net.seconds_left)}</b>` : "";
  return `<span class="vmu-chip">ממתין לכתובת מה-DHCP${where}${left}</span>`;
}
