/* ImageSrv -- #3654 (K14): השאלות שהמוח מגיש למשתמש בקונסולה. מוקאפ מאושר 18/11 (lab/design-ask-1118): דף "שאלות המוח" בעץ
   (brain_asks_page.js), חלון התשובה (כאן), וסעיף "שאלות מהמוח" בפעמון (כאן). אין הקלדת מילה לאישור (הכרעת נדב, 10/10), ואין
   חלון דפדפן מובנה. הנוסח, האפשרויות, הפועל של הכפתור האדום וברירת המחדל מהשרת -- אותו טקסט בכל מסך.
   שאלה שפקעה מוצגת "פקעה בלי תשובה" ולא כתשובה (עיקרון 5); כשל קריאה אינו הופך ל"אין שאלות".
   משתמש ב-api/post/sheet/impactList/UI/esc/fmtEta/toast/machineName/isAdmin/closeModal מ-console-core.js ו-console.js. */
"use strict";

let BRAIN_ASKS = null;                 // null = לא נקרא (לא "אין שאלות"); אחרת {open, recent, history}
let BRAIN_ASKS_AT = 0;                 // מתי ניסינו לקרוא לאחרונה
let BRAIN_ASKS_OK_AT = 0;              // מתי קראנו בהצלחה לאחרונה (ממנו נספר הזמן שנשאר)
let BRAIN_ASKS_ERR = "";               // "" = הקריאה האחרונה הצליחה
let BRAIN_SHEET_ID = null;             // השאלה שחלון התשובה פתוח עליה
const BRAIN_ASKS_EVERY_MS = 10000;
const BRAIN_KIND_HE = { machine: "משכפל", disk: "דיסק", slot: "מגירה", model: "דגם", network: "רשת", round: "סבב", image: "אימג'", settings: "הגדרה" };
const BRAIN_LOW_S = 60;                // "עומד להיגמר": מתחת לדקה

function brainAsksReset() { BRAIN_ASKS = null; BRAIN_ASKS_AT = 0; BRAIN_ASKS_OK_AT = 0; BRAIN_ASKS_ERR = ""; BRAIN_SHEET_ID = null; }

/* סינון ההיסטוריה (הדף) נוסע בשאילתה: מי ענה / תוצאה / על מה / טווח. הטקסט החופשי מסונן בדפדפן (כולל שם המכונה, שהשרת מכיר רק כ-MAC). */
function brainAsksQuery() {
  const v = typeof BRAIN_VIEW === "undefined" ? null : BRAIN_VIEW;
  if (!v) return "";
  const q = new URLSearchParams();
  q.set("days", v.range === "d" ? "1" : v.range === "w" ? "7" : "30");
  for (const k of ["state", "who", "kind"]) if (v[k]) q.set(k, v[k]);
  return "?" + q.toString();
}

/* רק בשרת הראשי: שרת משנה בתצוגה אינו מעביר את הנתיב הזה, והפעמון שלו הוא של בריאות. כשל קריאה משאיר את מה שנקרא
   קודם (ואינו הופך ל"אין שאלות"): העדכון הבא ינסה שוב, והדף אומר שהרשימה אינה בהכרח עדכנית. */
async function loadBrainAsks() {
  if (!isAdmin() || SERVER_CTX) return;
  BRAIN_ASKS_AT = Date.now();
  try {
    const got = await api("/brain/asks" + brainAsksQuery());
    if (!got || !Array.isArray(got.open) || !Array.isArray(got.recent)) throw new Error("תשובה בצורה לא מוכרת");
    BRAIN_ASKS = got; BRAIN_ASKS_OK_AT = Date.now(); BRAIN_ASKS_ERR = "";
  } catch (e) { BRAIN_ASKS_ERR = (e && e.message) || "לא נקרא"; }
  updateAlertBadge();
  brainSheetStillOpen();
  if (typeof current !== "undefined" && current === "asks" && typeof renderCurrent === "function") renderCurrent();
}
function brainAsksTick() {
  if (isAdmin() && !SERVER_CTX && Date.now() - BRAIN_ASKS_AT >= BRAIN_ASKS_EVERY_MS) loadBrainAsks();
}
function brainAskCount() { return BRAIN_ASKS ? BRAIN_ASKS.open.length : 0; }

/* ---------- ספירה לאחור: נספרת מרגע הקריאה, ומתעדכנת כל שנייה בלי לצייר את הדף מחדש ---------- */
function brainLeft(a) {
  if (a.expires_in_s == null) return 0;
  return Math.max(0, a.expires_in_s - Math.floor((Date.now() - BRAIN_ASKS_OK_AT) / 1000));
}
const brainMmss = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
function brainPct(a) { return a.ttl_s > 0 ? Math.max(0, Math.min(100, Math.round(100 * brainLeft(a) / a.ttl_s))) : 0; }
const brainCdLabel = (low) => `${low ? "עוד רגע: " : ""}בלי תשובה תבוצע ברירת המחדל בעוד`;
function brainCd(a) {
  const left = brainLeft(a), low = left <= BRAIN_LOW_S, pct = brainPct(a);
  return `<div class="ba-t${low ? " low" : ""}" data-cd="${Number(a.id)}"><div class="bar${low ? " warn" : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="${Number(a.ttl_s) || 0}" aria-valuenow="${left}"><i style="--w:${pct}%"></i></div><b>${brainMmss(left)}</b></div>`;
}
function brainPaintCd() {
  if (!BRAIN_ASKS || typeof document === "undefined" || typeof document.querySelectorAll !== "function") return;
  let ended = false;
  for (const el of document.querySelectorAll("[data-cd]")) {
    const a = BRAIN_ASKS.open.find((x) => x.id === Number(el.dataset.cd));
    if (!a) continue;
    const left = brainLeft(a), low = left <= BRAIN_LOW_S, box = el.querySelector(".bar"), i = el.querySelector(".bar > i"), b = el.querySelector("b");
    if (b) b.textContent = brainMmss(left);
    if (i) i.style.setProperty("--w", `${brainPct(a)}%`);
    if (box) { box.classList.toggle("warn", low); box.setAttribute("aria-valuenow", String(left)); }
    el.classList.toggle("low", low);
    if (el.classList.contains("ba-cd")) { const lbl = el.querySelector(".l span"); if (lbl) lbl.textContent = brainCdLabel(low); }
    if (left === 0) ended = true;
  }
  const near = document.getElementById("askNearest"), first = BRAIN_ASKS.open.map(brainLeft).sort((x, y) => x - y)[0];
  if (near && first != null) near.textContent = brainMmss(first);
  if (ended && Date.now() - BRAIN_ASKS_AT >= 2000) loadBrainAsks();   // השרת סוגר את השאלה בשעון שלו; כאן רק שואלים מה קרה
}
if (typeof setInterval === "function") setInterval(brainPaintCd, 1000);

/* ---------- על מה השאלה ---------- */
function brainAskWho(a) {
  const e = a.entity;
  if (!e || !e.key) return "";
  const name = (mac) => { const m = (MACHINES || []).find((x) => x.mac === mac); return (m && machineName(m)) || mac; };
  if (e.kind === "machine") return name(e.key);
  if (e.kind === "slot") { const [mac, port] = String(e.key).split("/"); return port ? `${name(mac)} · פורט ${port}` : name(mac); }
  return `${BRAIN_KIND_HE[e.kind] || e.kind} ${e.key}`;
}
const brainOpt = (a, id) => a.options.find((o) => o.id === id) || null;
const brainLabel = (a, id) => { const o = brainOpt(a, id); return o ? o.label_he : ""; };

/* ---------- הפעמון: סעיף "שאלות מהמוח" באותה מגירה, ו"לכל שאלות המוח" בתחתית ---------- */
function brainAskRows() {
  if (!BRAIN_ASKS || !BRAIN_ASKS.open.length) return "";
  const rows = BRAIN_ASKS.open.map((a) => `<div class="ba-qrow"><div class="m"><b>${esc(a.title_he)}</b><small>${esc(brainAskWho(a))}</small>${brainCd(a)}</div>`
    + `<button class="btn primary sm" type="button" onclick="brainAskSheet(${Number(a.id)})">ענה</button></div>`).join("");
  return `<div class="ba-sec">שאלות מהמוח · ${BRAIN_ASKS.open.length}</div>${rows}`;
}
const brainBellActs = () => `<div class="acts"><button class="btn" type="button" onclick="closeDrawer();selectPageById('asks')">לכל שאלות המוח</button><button class="btn primary" type="button" onclick="closeDrawer()">סגירה</button></div>`;

/* ---------- חלון התשובה: sheet() אחד ---------- */
const brainNote = (kind, text) => `<div class="n-note ${kind}"><svg class="n-ic" viewBox="0 0 24 24" aria-hidden="true">${kind === "info" ? '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>' : '<path d="M12 4 2.8 20h18.4z"/><path d="M12 10v4.5M12 17.5h.01"/>'}</svg><span>${text}</span></div>`;
/* #3673 (מוקאפ 20/11, `.ba-rows`): הפירוט שהכלל מצרף לשאלה -- טבלה קטנה בין ההודעה לספירה. תצוגה בלבד: אין בה פעולה, והתשובה היא האפשרות.
   כל טקסט עובר esc(); גוון (warn/bad) נאמר גם בהדגשה, לא רק בצבע. פירוט שאינו בצורה המוכרת אינו מצויר (השאלה נשארת). */
function brainSub(c) {
  if (!c.sub) return "";
  return `<span class="dim">${brainStyled(c.sub, c.sub_style)}</span>`;
}
/* mono = גופן קבוע (מספר, מזהה); ltr = לטיני בתוך משפט עברי, מבודד ובלי גופן אחר */
function brainStyled(text, style) {
  return style === "mono" ? `<span class="mono">${esc(text)}</span>` : style === "ltr" ? `<bdi dir="ltr">${esc(text)}</bdi>` : esc(text);
}
/* הערת תחתית: רצפים לטיניים (גודל דיסק, מזהה) מבודדים -- בלי זה "512 GB" בתוך משפט עברי מתהפך ל-"GB 512". */
function brainLtr(text) {
  return String(text).split(/([A-Za-z0-9][A-Za-z0-9.:\/_-]*(?: [A-Za-z0-9][A-Za-z0-9.:\/_-]*)*)/).map((part, i) => (i % 2 ? `<bdi dir="ltr">${esc(part)}</bdi>` : esc(part))).join("");
}
function brainCell(c) {
  if (!c || typeof c.text !== "string") return null;
  let t = brainStyled(c.text, c.style);
  if (c.tone === "warn" || c.tone === "bad") t = `<span class="${c.tone === "bad" ? "badc" : "warnc"}">${t}</span>`;
  return `<td>${c.arrow ? '<span class="arr">←</span>' : ""}${t}${brainSub(c)}</td>`;
}
function brainRowsHtml(a) {
  const r = a && a.rows;
  if (!r || !Array.isArray(r.cols) || !Array.isArray(r.rows) || !r.cols.length || !r.rows.length) return "";
  const body = [];
  for (const line of r.rows) {
    const cells = Array.isArray(line) && line.length === r.cols.length ? line.map(brainCell) : null;
    if (!cells || cells.includes(null)) return "";          // שורה חלקית אינה נראית כמו שורה שלמה: בלי טבלה
    body.push(`<tr>${cells.join("")}</tr>`);
  }
  return `<div class="ba-rows"><h4>${esc(r.title_he)}</h4><table><thead><tr>${r.cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>`
    + `<tbody>${body.join("")}</tbody></table>${r.more_he ? `<div class="more">${brainLtr(r.more_he)}</div>` : ""}</div>`;
}
function brainOptHint(a, o, on) {
  if (o.destructive) return on ? `<small class="bad">${esc(o.consequence_he)}</small>` : `<small>פעולה הרסנית — לא תתבצע בלי התשובה הזו</small>`;
  return o.id === a.safe_default ? `<small>ברירת המחדל כשאין תשובה</small>` : "";
}
function brainAskSheet(id, sel) {
  const a = BRAIN_ASKS && BRAIN_ASKS.open.find((x) => x.id === id);
  if (!a) { toast("השאלה כבר נסגרה"); return loadBrainAsks(); }
  const pick = sel && brainOpt(a, sel) ? sel : a.safe_default, left = brainLeft(a), low = left <= BRAIN_LOW_S;
  const def = brainOpt(a, a.safe_default), hasD = a.options.some((o) => o.destructive);
  const where = brainAskWho(a);
  const countdown = `<div class="ba-cd${low ? " low" : ""}" data-cd="${Number(a.id)}"><div class="l"><span>${brainCdLabel(low)}</span><b>${brainMmss(left)}</b></div>`
    + `<div class="bar${low ? " warn" : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="${Number(a.ttl_s) || 0}" aria-valuenow="${left}"><i style="--w:${brainPct(a)}%"></i></div>`
    + `<p>ברירת המחדל: <b>«${esc(def.label_he)}»</b>${def.outcome_he ? `. ${esc(def.outcome_he)}` : ""}.</p></div>`;
  const opts = `<div class="ba-opts" role="radiogroup" aria-label="האפשרויות">${a.options.map((o) => `<label class="n-opt"><input type="radio" name="ba" value="${esc(o.id)}"${o.id === pick ? " checked" : ""}><strong>${esc(o.label_he)}</strong>${brainOptHint(a, o, o.id === pick)}</label>`).join("")}</div>`;
  const impact = impactList([["ok", "ברירת המחדל אינה פעולה שמוחקת"]].concat(hasD ? [["b", "אפשרות הרסנית אינה נבחרת לבד: רק תשובה שלך מבצעת אותה"]] : []));
  BRAIN_SHEET_ID = a.id;
  sheet({ title: a.title_he, sub: `${where ? where + " · " : ""}שאלה מהמוח`,
    note: (a.detail_he ? brainNote("info", esc(a.detail_he)) : "") + brainRowsHtml(a) + countdown,
    fields: [{ type: "html", html: opts + impact }], submitLabel: "אישור", size: "narrow", hint: "אפשר לענות גם במסך של המכונה",
    onSubmit: async () => {
      const chosen = document.querySelector('#sheet input[name="ba"]:checked');
      if (!chosen) throw new Error("בחרו אפשרות");
      try { await post(`/brain/asks/${Number(a.id)}/answer`, { option: chosen.value }); }
      catch (e) { await loadBrainAsks(); throw e; }   // 409: מישהו אחר ענה או שהיא פקעה -- השגיאה אומרת איזה
      BRAIN_SHEET_ID = null;
      toast("התשובה נרשמה");
      await loadBrainAsks();
      if (document.getElementById("drawerTitle").textContent === "התראות" && typeof topNotifications === "function") topNotifications();
    } });
  const paint = () => {
    const picked = document.querySelector('#sheet input[name="ba"]:checked'), o = picked ? brainOpt(a, picked.value) : null;
    const button = document.getElementById("sf-submit");
    if (button) { button.classList.toggle("danger", !!(o && o.destructive)); button.classList.toggle("primary", !(o && o.destructive)); button.textContent = o && o.destructive ? o.verb_he : "אישור"; }
    document.querySelectorAll('#sheet .ba-opts .n-opt').forEach((label) => {
      const input = label.querySelector("input"), opt = brainOpt(a, input.value), small = label.querySelector("small");
      if (small) small.remove();
      const hint = brainOptHint(a, opt, input.checked);
      if (hint) label.insertAdjacentHTML("beforeend", hint);
    });
  };
  document.querySelectorAll('#sheet input[name="ba"]').forEach((r) => r.addEventListener("change", paint));
  paint();
}

/* השאלה שחלון התשובה פתוח עליה נסגרה בלי הפועל הזה (פקעה, או נענתה במקום אחר): החלון נסגר ואומר מה קרה, לא נשאר פתוח על שאלה מתה. */
function brainSheetStillOpen() {
  if (BRAIN_SHEET_ID == null || !BRAIN_ASKS) return;
  if (BRAIN_ASKS.open.some((x) => x.id === BRAIN_SHEET_ID)) return;
  const done = (BRAIN_ASKS.recent || []).concat((BRAIN_ASKS.history && BRAIN_ASKS.history.items) || []).find((x) => x.id === BRAIN_SHEET_ID);
  BRAIN_SHEET_ID = null;
  if (typeof closeModal === "function") closeModal();
  toast(!done ? "השאלה כבר נסגרה"
    : done.state === "expired" ? `תם הזמן — בוצעה ברירת המחדל: «${done.safe_default_he}»`
    : done.state === "answered" ? `השאלה נענתה כבר: «${done.answer_he || done.answer}»` : "השאלה בוטלה");
}
