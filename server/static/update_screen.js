/* ‏#1301: מסך הביניים "השרת מתעדכן" — לפי המוקאפ שנדב אישר 13/10
   (design-approved/update/update.html). מסך מלא מעל הקונסולה: ארבעת השלבים של
   tools/server-upgrade.sh (fetch · verify · install · restart), שעון, ושורת בדיקה מול השרת.

   האמת היא מה שהשרת עונה, לא מה שהדף זוכר: כל 3 שניות ‏/update/status (admin) כל עוד
   השרת עונה; כשהוא לא עונה — ‏/health/live (ללא הזדהות) עד שהוא חוזר. "עודכן" רק על ראיה
   חיובית: הסטטוס done+verified **ו**-stage done (הסקריפט סיים, כולל verify-boot-payload),
   או — כשאין session — ‏/health/live שעונה עם התג. אז טעינה מלאה של הדף (‏?updated=),
   כי קבצי ה-JS/CSS בדף הם של הגרסה הישנה (‏?v=). תקרה: 10 דקות בלי התקדמות → "תקרת זמן",
   לא "ממתין" לנצח (עיקרון 5). המסמך: docs/interfaces.md, "מסמך הסטטוס (‏#1301)". */
"use strict";

const UPD_POLL_MS = 3000;          // בקשה זעירה כל 3 שניות, מדפדפן אחד
const UPD_CEIL_MS = 10 * 60 * 1000; // תקרת הזמן: 10 דקות בלי התקדמות
/* ‏#1463: תקרה לשלב, כשהשרת עונה. בניית ה-initrd לבדה: 498 ש' בשרת המעבדה (6 vCPU, הרצה נפרדת,
   ‏16/10) — 10 דקות היו מכריזות "לא התקדם" על שרת איטי יותר שעדיין בונה. */
/* ‏#1411: ‏packages — עדכוני Debian מהמאגר שבגרסה; ל-apt בשרת תקרה של 20 דקות (deploy/security-upgrade.sh). */
const UPD_STAGE_CEIL_MS = { payload: 25 * 60 * 1000, packages: 25 * 60 * 1000 };
const UPD_RELOAD_S = 5;            // "נכנס לקונסולה בעוד 5 ש׳"
const UPD_LOG = "/var/log/imagesrv/server-upgrade.log";
/* ארבעת השלבים — מול stage שהסקריפט כותב (‏done = אחרי האחרון). ‏payload (‏#1463) הוא חלק
   מ"מתקין" על המסך (המוקאפ של 13/10), אבל שלב משלו לתקרה ולתיאור. */
const UPD_STEPS = [
  { k: "fetch", t: "מוריד את הגרסה", d: "מ-github.com" },
  { k: "verify", t: "בודק חתימה", d: "מול המפתח שמותקן בשרת" },
  { k: "install", t: "מתקין", d: "קבצי השרת וקבצי האתחול של המחשבים" },
  { k: "restart", t: "עולה מחדש", d: "הקונסולה מתנתקת לרגע — זה צפוי" },
];
const UPD_STAGE_IDX = { fetch: 0, verify: 1, install: 2, packages: 2, payload: 2, restart: 3, done: 4 };
const UPD_FAIL_PHASE = { signature: "sigfail", fetch: "dlfail", rollback: "rollback" };
const UPD_NET_RE = /could not resolve host|couldn't resolve host|timed out|timeout|connection refused|network is unreachable|no route to host/i;
const UPD_IC = {
  logo: '<svg viewBox="4 16 56 30" aria-hidden="true"><g fill="currentColor"><rect x="6" y="20" width="6" height="6" rx=".75"/><rect x="14" y="20" width="6" height="6" rx=".75"/><rect x="14" y="28" width="6" height="6" rx=".75"/><rect x="22" y="28" width="6" height="6" rx=".75"/><rect x="6" y="36" width="6" height="6" rx=".75"/><rect x="22" y="36" width="6" height="6" rx=".75"/><rect x="36" y="20" width="6" height="6" rx=".75"/><rect x="44" y="20" width="6" height="6" rx=".75"/><rect x="44" y="28" width="6" height="6" rx=".75"/><rect x="52" y="28" width="6" height="6" rx=".75"/><rect x="36" y="36" width="6" height="6" rx=".75"/><rect x="52" y="36" width="6" height="6" rx=".75"/></g></svg>',
  ok: '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16.5 9.5"/>',
  warn: '<path d="M12 4 2.8 20h18.4z"/><path d="M12 10v4.5M12 17.5h.01"/>',
  err: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5h.01"/>',
  shield: '<path d="M12 3 5 6v5.5c0 4.3 2.9 8 7 9.5 4.1-1.5 7-5.2 7-9.5V6z"/><path d="m9.5 9.5 5 5M14.5 9.5l-5 5"/>',
  cloud: '<path d="M7 18h10.5a4 4 0 0 0 .5-7.97A6 6 0 0 0 6.3 9.6 4.2 4.2 0 0 0 7 18z"/><path d="m4 4 16 16"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
};
const UPD_STEP_IC = {
  done: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
  fail: '<svg viewBox="0 0 24 24"><path d="m7 7 10 10M17 7 7 17"/></svg>',
  warn: '<svg viewBox="0 0 24 24"><path d="M12 7v6M12 17h.01"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>',
  cur: "<i></i>",
};
const UPD_STEP_SR = { done: "הושלם", cur: "עכשיו", fail: "נכשל", warn: "לא הסתיים", skip: "לא בוצע", back: "הוחזר", pend: "ממתין" };

let UPD = null;                      // מצב המסך; null = סגור
let updPollTimer = null, updTickTimer = null, updCdTimer = null;
let updClock = () => Date.now();     // הבדיקות מחליפות
let updNavigate = (url) => location.replace(url);

const updIcon = (name) => `<svg class="u-ic" viewBox="0 0 24 24" aria-hidden="true">${UPD_IC[name]}</svg>`;
const updV = (t) => `<span class="mono">${esc(t || "?")}</span>`;
function updMmss(s) { s = Math.max(0, Math.floor(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
/* שעון השרת: ‏now של התשובה האחרונה פחות שעון הדפדפן באותו רגע. */
function updServerNow() { return updClock() + (UPD ? UPD.skew : 0); }
/* ‏HH:MM:SS באזור הזמן של השרת (‏fmtHour, ‏#1933) — לא שעון הדפדפן ולא UTC כלשונו. */
function updHms(serverMs) { return Number.isFinite(serverMs) ? fmtHour(new Date(serverMs).toISOString()) : "—"; }
function updTs(key) {
  const st = UPD && UPD.status, t = st && st.stages && st.stages[key];
  const ms = Date.parse(t || "");
  if (Number.isFinite(ms)) return ms;
  // ההפעלה מחדש שהדגימה פספסה (נכתבת שנייה לפני ה-restart): מתחילה, לכל המאוחר, ברגע שהשרת הפסיק לענות.
  if (key === "restart" && UPD && UPD.offAt != null && UPD.stageIdx >= 2) return UPD.offAt + UPD.skew;
  return null;
}
/* תיאור שלב: הסיבה מהשרת בלי סוגריים עם נתיב בסופה (הנתיב המלא בפרטים הטכניים) — נתיב LTR בתוך שורה RTL מתהפך. */
function updShort(text) {
  return String(text || "").replace(/\s*\([^()]*\/[^()]*\)\s*$/, "");
}

/* ---------- מצב ---------- */

/* פותח את המסך. ‏status — מסמך שכבר נקרא (טעינה עם עדכון רץ); בלעדיו — מיד אחרי "החל". */
function updStart({ tag, from = null, by = null, status = null }) {
  const now = updClock();
  // ‏#1776: "עדכן" בלשונית של שרת משנה — המסך עוקב אחרי **שרת המשנה**, דרך ה-relay של הראשי.
  const node = typeof SERVER_CTX !== "undefined" && SERVER_CTX ? SERVER_CTX : null;
  UPD = { tag, from, by, node, phase: "run", status: null, skew: 0, startedMs: null, stageIdx: -1,
          changeAt: now, off: false, offAt: null, tries: 0, backAfter: null, mode: "status",
          lastReadAt: null, noSession: false, cd: UPD_RELOAD_S, liveVersion: null, stuck: false, stage: null };
  if (status) { updApply(status, true); updCheckCeiling(); }
  if (typeof pollTimer !== "undefined") clearInterval(pollTimer);   // הדגימה הרגילה של הקונסולה נעצרת — השרת עומד לרדת
  updShow();
  updRender();
  clearInterval(updTickTimer);
  updTickTimer = setInterval(updTick, 1000);
  if (UPD.phase === "run") updSchedule(status ? UPD_POLL_MS : 0);
}

function updSchedule(ms = UPD_POLL_MS) {
  clearTimeout(updPollTimer);
  if (UPD && UPD.phase === "run") updPollTimer = setTimeout(updPoll, ms);
}

/* ‏#1776: הסטטוס של השרת שמתעדכן — של הראשי עצמו, או של שרת המשנה דרך ה-relay (אותו ערוץ מאומת
   של שאר הלשוניות; ‏/update/status ב-CONSOLE_GETS). */
function updStatusUrl() {
  return UPD && UPD.node ? `/api/console/storage-nodes/${encodeURIComponent(UPD.node)}/console/update/status`
    : "/api/console/update/status";
}

/* בקשה בלי api(): ‏401 כאן אינו "התנתקת" (api() היה מציג את מסך הכניסה), וכשל רשת הוא מצב. */
async function updGet(url) {
  try {
    const r = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    let body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    return { ok: r.ok && body != null, status: r.status, body };
  } catch (e) {
    return { ok: false, status: 0, body: null };
  }
}

function updGoOff() {
  const now = updClock();
  if (!UPD.off) { UPD.off = true; UPD.offAt = now; UPD.tries = 0; UPD.changeAt = now; }
  UPD.tries += 1;
  UPD.mode = "live";
}
function updBack() {
  if (!UPD.off) return;
  const now = updClock();
  UPD.backAfter = Math.round((now - UPD.offAt) / 1000);
  UPD.off = false; UPD.changeAt = now;
}

/* מסמך סטטוס → מצב המסך. ‏first = הקריאה הראשונה (טעינה עם עדכון רץ): ה"התקדמות האחרונה"
   נלקחת מחותמת השלב בשרת, כך שסטטוס תקוע מלפני שעה מגיע לתקרה מיד ולא אחרי עוד 10 דקות. */
function updApply(st, first = false) {
  const now = updClock();
  UPD.status = st; UPD.lastReadAt = now;
  const serverNow = Date.parse(st.now || "");
  if (Number.isFinite(serverNow)) UPD.skew = serverNow - now;
  if (st.tag) UPD.tag = st.tag;
  if (st.from) UPD.from = st.from;
  if (st.by) UPD.by = st.by;
  const started = Date.parse(st.started_at || "");
  if (Number.isFinite(started)) UPD.startedMs = started;
  if (UPD.off) updBack();
  if (st.state === "failed") { UPD.phase = UPD_FAIL_PHASE[st.reason] || "failed"; return; }
  const finished = st.state === "done" && st.verified === true && (st.stage == null || st.stage === "done");
  if (finished) { updDone(); return; }
  if (st.state !== "running" && st.state !== "done") { UPD.phase = "failed"; return; }   // idle / לא מוכר — לא "ממתין"
  const idx = st.stage in UPD_STAGE_IDX ? UPD_STAGE_IDX[st.stage] : 0;
  if (idx !== UPD.stageIdx || st.stage !== UPD.stage) {
    UPD.stageIdx = idx; UPD.stage = st.stage;
    const at = updTs(st.stage) || started;
    UPD.changeAt = first && Number.isFinite(at) ? at - UPD.skew : now;
  }
  if (UPD.phase === "timeout" && now - UPD.changeAt <= updCeil()) UPD.phase = "run";
}

function updDone() {
  UPD.phase = "done"; UPD.cd = UPD_RELOAD_S;
  clearTimeout(updPollTimer);
  clearInterval(updCdTimer);
  // ‏#1334: עודכן — אבל משהו לא הותקן (למשל "עדכון ב-VM לא זמין"): בלי טעינה
  // אוטומטית, כדי שהאזהרה תיקרא ולא תיעלם אחרי 5 ש'.
  if (UPD.status && UPD.status.warning) return;
  updCdTimer = setInterval(updCountdown, 1000);
}
function updCountdown() {
  if (!UPD || UPD.phase !== "done" || (UPD.status && UPD.status.warning)) { clearInterval(updCdTimer); return; }
  UPD.cd -= 1;
  const el = document.getElementById("updCd");
  if (el) el.textContent = String(Math.max(0, UPD.cd));
  if (UPD.cd <= 0) updReload();
}
/* טעינה מלאה — הקבצים בדף הם של הגרסה הישנה. ‏?updated= אומר לדף הבא להציג "עודכן ✓". */
function updReload() {
  clearInterval(updCdTimer);
  // ‏#1776: התעדכן שרת המשנה — הקבצים בדף הם של הראשי, שלא השתנה: בלי טעינה, חזרה ללשונית.
  if (UPD && UPD.node) {
    const tag = UPD.tag;
    updClose();
    if (typeof toast === "function") toast(`שרת המשנה עודכן ל-${tag} ✓`, 5000, "ok");
    return;
  }
  updNavigate(location.pathname + "?updated=" + encodeURIComponent(UPD.tag));
}

/* התקרה של השלב הנוכחי. שרת שלא עונה — תמיד 10 דקות: זו ההפעלה מחדש, לא הבנייה. */
function updCeil() { return (UPD && !UPD.off && UPD_STAGE_CEIL_MS[UPD.stage]) || UPD_CEIL_MS; }
const updCeilMin = () => Math.round(updCeil() / 60000);
function updCheckCeiling() {
  if (UPD.phase === "run" && updClock() - UPD.changeAt > updCeil()) {
    UPD.phase = "timeout"; UPD.stuck = !UPD.off;
    clearTimeout(updPollTimer);
  }
}

/* מחזור בדיקה אחד. ‏manual = "בדוק עכשיו" מתוך תקרת הזמן. */
async function updPoll(manual = false) {
  if (!UPD || (UPD.phase !== "run" && !(manual && UPD.phase === "timeout"))) return;
  const before = UPD.phase;
  if (UPD.node) {
    // ‏#1776: שרת המשנה נשאל רק דרך ה-relay. ‏/health/live של הראשי אינו ראיה על שרת המשנה (הוא עונה
    // תמיד), ולכן אין מצב live: כל עוד ה-relay לא ענה (502 — שרת המשנה עולה מחדש) — "לא עונה".
    const r = await updGet(updStatusUrl());
    if (r.ok) updApply(r.body);
    else updGoOff();
  } else if (UPD.mode === "status") {
    const r = await updGet(updStatusUrl());
    if (r.ok) updApply(r.body);
    else if (r.status === 401 || r.status === 403) { UPD.noSession = true; UPD.mode = "live"; }
    else updGoOff();
  } else {
    const live = await updGet("/api/console/health/live");
    if (!live.ok) updGoOff();
    else {
      UPD.liveVersion = live.body.version || null;
      updBack();
      const r = await updGet("/api/console/update/status");
      if (r.ok) { UPD.mode = "status"; updApply(r.body); }
      else if (r.status === 401 || r.status === 403) {
        UPD.noSession = true;
        if (UPD.liveVersion === UPD.tag) updDone();   // אין session: הראיה היא הגרסה ש-/health/live מדווח
      }
    }
  }
  if (!UPD) return;
  if (manual && before === "timeout" && UPD.phase === "timeout" && updClock() - UPD.changeAt <= updCeil()) UPD.phase = "run";
  updCheckCeiling();
  updRender();
  updSchedule();
}

function updTick() {
  if (!UPD) { clearInterval(updTickTimer); return; }
  const before = UPD.phase;
  updCheckCeiling();
  if (UPD.phase !== before) { updRender(); return; }
  const el = document.getElementById("updEl");
  if (el) el.textContent = updClockText();
  const se = document.getElementById("updStepEl");
  if (se) se.textContent = updStepLive();
  const poll = document.getElementById("updPoll");
  if (poll) poll.innerHTML = updPollHtml();
}

/* ---------- פעולות ---------- */

function updWaitMore() {
  if (!UPD) return;
  UPD.phase = "run"; UPD.stuck = false; UPD.changeAt = updClock();
  updRender(); updSchedule(0);
}
function updCheckNow() { return updPoll(true); }
/* "חזרה לקונסולה": השרת עונה (כשל בשם, או תקוע כשהוא עונה) — הקונסולה שמישה. */
function updClose() {
  clearTimeout(updPollTimer); clearInterval(updTickTimer); clearInterval(updCdTimer);
  UPD = null;
  updHide();
  if (typeof startStatusWatch === "function") startStatusWatch();
  if (typeof current !== "undefined" && current === "settings" && typeof loadHealthUpdate === "function")
    loadHealthUpdate().then(() => renderCurrent());
}
/* "נסה שוב" (הורדה נכשלה בלבד): אותו תג, אותה בקשה של "החל". */
async function updRetry() {
  if (!UPD) return;
  const { tag, from } = UPD;
  try { await post("/update/apply", { tag }); }
  catch (e) { updFlash(`הניסיון החוזר נכשל: ${e.message}`); return; }
  updStart({ tag, from, by: ME && ME.username });
}
function updCopy() {
  const st = UPD && UPD.status;
  const text = [`ImageSrv: עדכון ל-${UPD && UPD.tag} מ-${(UPD && UPD.from) || "?"}`,
    st && st.reason ? `reason: ${st.reason} · stage: ${st.stage || "-"}` : "",
    (st && st.error) || ""].filter(Boolean).join("\n");
  const clip = typeof navigator !== "undefined" && navigator.clipboard;
  if (!clip) { updFlash("ההעתקה לא זמינה בדפדפן הזה — הפרטים מופיעים בפרטים הטכניים"); return Promise.resolve(); }
  return clip.writeText(text).then(() => updFlash("הפרטים הועתקו"),
    () => updFlash("ההעתקה נכשלה — הפרטים מופיעים בפרטים הטכניים"));
}
/* משוב בתוך המסך: ה-toast של הקונסולה יושב ב-#app, שמאחורי המסך ו-inert בזמן שהוא פתוח. */
function updFlash(text) {
  const el = document.getElementById("updFootHint");
  if (el) el.textContent = text;
}

/* ---------- תצוגה ---------- */

function updClockText() {
  if (!UPD) return "";
  if (UPD.phase === "timeout") return updMmss((updClock() - UPD.changeAt) / 1000);
  const start = UPD.startedMs != null ? UPD.startedMs : null;
  return start == null ? "0:00" : updMmss((updServerNow() - start) / 1000);
}
function updStepLive() {
  const idx = updShownStep();
  const at = idx >= 0 && idx < 4 ? updTs(UPD_STEPS[idx].k) : null;
  return at == null ? "" : updMmss((updServerNow() - at) / 1000);
}
/* השלב שמוצג כ"עכשיו": שרת שלא עונה אחרי שהתחלנו להתקין — זו ההפעלה מחדש, גם אם
   הדגימה פספסה את הכתיבה של restart (היא נכתבת שנייה לפני ה-restart עצמו). */
function updShownStep() {
  if (UPD.off && UPD.stageIdx >= 2) return 3;
  return Math.max(0, Math.min(UPD.stageIdx, 3));
}
function updDur(i) {
  const a = updTs(UPD_STEPS[i].k), b = updTs(i < 3 ? UPD_STEPS[i + 1].k : "done");
  return a != null && b != null ? updMmss((b - a) / 1000) : "";
}

function updStepsHtml(states, desc = {}) {
  return UPD_STEPS.map((s, i) => {
    const st = states[i];
    const d = desc[i] || (st === "skip" ? "לא בוצע — לא הגענו לשלב הזה" : s.d);
    const dur = st === "done" ? updDur(i) : st === "cur" ? `<span id="updStepEl">${updStepLive()}</span>` : "";
    return `<li class="${st}"${st === "cur" ? ' aria-current="step"' : ""}><span class="si">${UPD_STEP_IC[st] || ""}</span>`
      + `<span><span class="st">${esc(s.t)}</span><span class="sr-only"> — ${UPD_STEP_SR[st]}</span><span class="sd">${d}</span></span>`
      + `<span class="sdur">${dur}</span></li>`;
  }).join("");
}

function updPollHtml() {
  const u = UPD, led = (cls, b, rest) => `<span class="u-led${cls}"></span><span><b>${b}</b>${rest ? ` · ${rest}` : ""}</span>`;
  const num = (n) => `<span class="num">${n}</span>`;
  if (u.phase === "run" && u.off) {
    const next = Math.max(1, Math.ceil((UPD_POLL_MS - ((updClock() - u.offAt) % UPD_POLL_MS)) / 1000));
    const expected = u.stageIdx >= 2;
    return `<span class="u-led w pulse"></span><span><b>${expected ? "השרת לא עונה — צפוי בזמן ההפעלה מחדש." : "השרת לא עונה."}</b> ניסיון ${num(u.tries)} · הבא בעוד ${num(next)} ש׳</span>`;
  }
  if (u.phase === "run") {
    const ago = u.lastReadAt == null ? null : Math.floor((updClock() - u.lastReadAt) / 1000);
    return led("", "השרת עונה", ago == null ? "ממתין לתשובה הראשונה" : `השלב נקרא מהשרת לפני ${num(ago)} ש׳`);
  }
  if (u.phase === "timeout") {
    return u.stuck
      ? led(" e", `השלב לא התקדם מאז ${updHms(u.changeAt + u.skew)}`, "השרת עונה")
      : led(" e", `אין תשובה מאז ${updHms(u.offAt + u.skew)}`, `${num(u.tries)} ניסיונות, כל ${UPD_POLL_MS / 1000} שניות`);
  }
  if (u.phase === "done") {
    return u.backAfter != null
      ? led("", "השרת חזר", `ענה עם ${updV(u.tag)} אחרי ${num(u.backAfter)} שניות של הפעלה מחדש`)
      : led("", "השרת עונה", `עם ${updV(u.tag)}`);
  }
  const cur = u.status && u.status.current;
  if (u.phase === "rollback" || u.phase === "failed") return led("", "השרת עונה", cur ? `עם ${updV(cur)}` : "");
  return led("", "השרת עונה", u.backAfter == null ? "הקונסולה לא התנתקה" : "");
}

function updBadge(kind, icon) {
  const inner = kind ? updIcon(icon) : UPD_IC.logo;
  return `<div class="ubadge${kind ? " u-" + kind : ""}">${inner}</div>`;   // u-: ‏.ok גלובלי בקונסולה
}
function updTech(text) {
  return `<details class="u-tech"><summary>פרטים טכניים</summary><pre>${esc(text || "השרת לא מסר פירוט")}</pre></details>`;
}
const updBtn = (label, fn, cls = "") => `<button class="n-btn${cls ? " " + cls : ""}" type="button" onclick="${fn}">${label}</button>`;

/* כל מצב → כותרת, שורת משנה, שלבים, גוף, רמז ופס תחתון. מחזיר HTML של כל הכרטיס. */
function updHtml() {
  const u = UPD, st = u.status || {}, TO = u.tag, FROM = u.from;
  const fromTxt = FROM ? updV(FROM) : "הגרסה הקודמת";
  let badge, title, sub, steps, body = "", hint = "", foot = "", time = true, timeLabel = "זמן שעבר";
  if (u.phase === "run") {
    badge = updBadge("");
    title = `השרת מתעדכן ל-${updV(TO)}`;
    const startTs = st.started_at;
    sub = [FROM ? `מ-${updV(FROM)}` : "", startTs ? `התחיל ב-${esc(fmtClock(startTs))}${u.by ? ` על ידי ${esc(u.by)}` : ""}` : "",
      "אל תכבו את השרת"].filter(Boolean).join(" · ");
    const at = updShownStep();
    steps = updStepsHtml(UPD_STEPS.map((_, i) => i < at ? "done" : i === at ? "cur" : "pend"),
      u.stage === "payload" && !u.off ? { 2: "בונה את קבצי האתחול של המחשבים — החלק הארוך" }
        : u.stage === "packages" && !u.off ? { 2: "מתקין את עדכוני האבטחה של Debian שבגרסה" } : {});
    // ‏#1463: נמדד 16/10 בשרת המעבדה (update_status.stages) — 8:49 מקצה לקצה, 8:43 מהם בשלב install.
    hint = "בדרך כלל 8–10 דקות, רובן בבניית קבצי האתחול של המחשבים. אם שלב לא יתקדם בזמן הצפוי, נעצור ונסביר מה לבדוק. אפשר לסגור את הדף — העדכון ממשיך בשרת.";
  } else if (u.phase === "done") {
    badge = updBadge("ok", "ok");
    title = `עודכן ל-${updV(TO)} ✓`;
    const total = u.startedMs != null && updTs("done") != null ? updMmss((updTs("done") - u.startedMs) / 1000) : "";
    sub = `השרת חזר וענה עם הגרסה החדשה${total ? ` · לקח ${total}` : ""}`;
    steps = updStepsHtml(["done", "done", "done", "done"], { 3: "השרת ענה עם הגרסה החדשה" });
    time = false;
    foot = `<span class="n-hint" id="updFootHint">נכנס לקונסולה בעוד <span class="u-cd" id="updCd">${u.cd}</span> ש׳ — ${u.node ? "חזרה ללשונית של שרת המשנה" : "הדף נטען מחדש עם קבצי הגרסה החדשה"}</span>${updBtn("המשך עכשיו", "updReload()", "pri")}`;
    if (st.warning) {
      // ‏#1334: העדכון עצמו עבר ואומת; רכיב לא הותקן — אומרים זאת, לא "✓" נקי.
      badge = updBadge("warn", "warn");
      title = `עודכן ל-${updV(TO)} — אבל ${esc(st.warning.split(":")[0])}`;
      body = `<div class="n-note"><span>${esc(st.warning)}</span></div>`;
      foot = `<span class="n-hint" id="updFootHint">השרת עודכן ורץ. מה שלא הותקן מפורט למעלה ובהגדרות › עדכונים.</span>${updBtn("המשך לקונסולה", "updReload()", "pri")}`;
    }
  } else if (u.phase === "timeout") {
    badge = updBadge("warn", "warn");
    title = u.stuck ? `העדכון לא התקדם תוך ${updCeilMin()} דקות` : "השרת לא חזר תוך 10 דקות";
    sub = `העדכון ל-${updV(TO)} אולי עדיין רץ, ואולי נתקע. הקונסולה הפסיקה לחכות.`;
    const w = updShownStep();
    const why = u.stuck ? `לא התקדם מאז ${updHms(u.changeAt + u.skew)}` : `השרת התנתק ב-${updHms(u.offAt + u.skew)} ולא ענה מאז`;
    steps = updStepsHtml(UPD_STEPS.map((_, i) => i < w ? "done" : i === w ? "warn" : "pend"), { [w]: why });
    timeLabel = u.stuck ? "בלי התקדמות" : "בלי תשובה";
    const origin = typeof location !== "undefined" && location.origin ? location.origin : "";
    body = `<h3>מה לבדוק, לפי הסדר</h3><ol class="u-chk">
      <li><span>מסך השרת עצמו<small>האם הוא דלוק, ומה הגרסה שמוצגת בו. אם רשום ${updV(TO)} — העדכון עבר, ורק הדרך מהמחשב הזה אליו חסומה.</small></span></li>
      <li><span>הכתובת של הקונסולה בלשונית חדשה<small><span class="mono">${esc(origin)}</span></small></span></li>
      <li><span>יומן העדכון בשרת<small><span class="mono">${UPD_LOG}</span> — השורה האחרונה אומרת באיזה שלב זה עצר.</small></span></li>
    </ol>`;
    foot = `<span class="n-hint" id="updFootHint">הבדיקה האוטומטית נעצרה. אף שלב לא בוטל בשרת.</span>${u.stuck ? updBtn("חזרה לקונסולה", "updClose()") : ""}${updBtn(`חכה עוד ${updCeilMin()} דקות`, "updWaitMore()")}${updBtn("בדוק עכשיו", "updCheckNow()", "pri")}`;
  } else if (u.phase === "sigfail") {
    const unchecked = /unchecked:/.test(String(st.detail || st.error || ""));
    badge = updBadge("bad", "shield");
    title = unchecked ? "העדכון נדחה: החתימה לא נבדקה" : "העדכון נדחה: הגרסה לא חתומה";
    sub = `השרת ממשיך לעבוד על ${fromTxt}. שום דבר לא הוחלף ולא הופעל מחדש.`;
    steps = updStepsHtml(["done", "fail", "skip", "skip"],
      { 1: unchecked ? `לא ניתן היה לבדוק את החתימה על ${updV(TO)}` : `החתימה על ${updV(TO)} לא תואמת אף מפתח שמותקן בשרת` });
    time = false;
    body = `<div class="u-note bad">${updIcon("shield")}<span>השרת מתקין רק גרסאות שנחתמו במפתח של ImageSrv. גרסה בלי חתימה יכולה להיות קוד שלא אנחנו פרסמנו — ולכן <b>לא לנסות שוב</b>, אלא לדווח למי שמתחזק את ImageSrv עם הפרטים למטה.</span></div>${updTech(st.error)}`;
    foot = `<span class="n-hint" id="updFootHint">נרשם ביומן הפעולות</span>${updBtn("העתק פרטים", "updCopy()")}${updBtn("חזרה לקונסולה", "updClose()", "pri")}`;
  } else if (u.phase === "dlfail") {
    const net = UPD_NET_RE.test(String(st.error || ""));
    badge = updBadge("bad", "cloud");
    title = "ההורדה נכשלה";
    sub = `השרת ממשיך לעבוד על ${fromTxt}. שום דבר לא הוחלף.`;
    steps = updStepsHtml(["fail", "skip", "skip", "skip"], { 0: net ? "השרת לא הצליח להגיע ל-github.com" : "ההורדה מ-github.com נכשלה — הפירוט למטה" });
    time = false;
    body = (net ? `<h3>מה לבדוק</h3><ol class="u-chk">
      <li><span>חומת האש של המכללה<small>השרת צריך יציאה ל-<span class="mono">github.com</span> בפורט <span class="mono">443</span>.</small></span></li>
      <li><span>שרת ה-DNS בהגדרות הרשת של השרת<small>ההודעה <span class="mono">Could not resolve host</span> פירושו שהשם לא תורגם לכתובת.</small></span></li>
    </ol>` : `<p>ההורדה נכשלה מסיבה שאינה קשורה לרשת. הפרטים הטכניים למטה מציגים את השגיאה שהתקבלה.</p>`) + updTech(st.error);
    foot = `<span class="n-hint" id="updFootHint">נרשם ביומן הפעולות</span>${updBtn("חזרה לקונסולה", "updClose()")}${updBtn("נסה שוב", "updRetry()", "pri")}`;
  } else if (u.phase === "rollback") {
    badge = updBadge("bad", "undo");
    title = `העדכון נכשל — השרת חזר ל-${fromTxt}`;
    sub = "השרת עובד, על הגרסה הקודמת. המחשבים לא הושפעו.";
    const f = Math.min(3, Math.max(0, UPD_STAGE_IDX[st.stage] != null ? UPD_STAGE_IDX[st.stage] : 2));
    const states = UPD_STEPS.map((_, i) => i < f ? "done" : i === f ? "fail" : "skip");
    states[3] = "back";
    const desc = { 3: `הופעל מחדש על ${fromTxt} — השרת עונה` };
    if (f < 3) desc[f] = esc(updShort(st.detail) || "ההתקנה נכשלה — הפירוט למטה");
    else desc[3] = `${esc(updShort(st.detail) || "השרת החדש לא אומת")} · הופעל מחדש על ${fromTxt}`;
    steps = updStepsHtml(states, desc);
    time = false;
    body = updTech(st.error);
    foot = `<span class="n-hint" id="updFootHint">אפשר לנסות שוב אחרי שהבעיה ביומן טופלה</span>${updBtn("העתק פרטים", "updCopy()")}${updBtn("חזרה לקונסולה", "updClose()", "pri")}`;
  } else {   // failed: install / timeout / round / בלי reason / מצב לא מוכר — הטקסט של השרת כלשונו
    const round = st.reason === "round";
    badge = updBadge("bad", "err");
    title = round ? "העדכון לא התחיל: יש סבב פתוח" : `העדכון ל-${updV(TO)} נכשל`;
    sub = round ? `השרת ממשיך לעבוד על ${fromTxt}. אפשר לעדכן אחרי שהסבב יסתיים.`
      : `השרת עונה עם ${st.current ? updV(st.current) : "גרסה לא ידועה"}. הפירוט למטה וביומן העדכון בשרת.`;
    const f = round ? -1 : Math.min(3, Math.max(0, UPD_STAGE_IDX[st.stage] != null ? UPD_STAGE_IDX[st.stage] : 0));
    steps = updStepsHtml(UPD_STEPS.map((_, i) => f < 0 ? "skip" : i < f ? "done" : i === f ? "fail" : "skip"),
      f >= 0 ? { [f]: esc(updShort(st.detail) || "השלב נכשל — הפירוט למטה") } : {});
    time = false;
    body = updTech(st.error || (st.state && st.state !== "failed" ? `מצב לא צפוי מהשרת: ${st.state}` : ""));
    foot = `<span class="n-hint" id="updFootHint">הפירוט ביומן העדכון בשרת: <span class="mono">${UPD_LOG}</span></span>${updBtn("העתק פרטים", "updCopy()")}${updBtn("חזרה לקונסולה", "updClose()", "pri")}`;
  }
  return `<div class="uh">${badge}<div class="utxt"><h1 id="updT" tabindex="-1">${title}</h1><p id="updS">${sub}</p></div>`
    + (time ? `<div class="utime"><b id="updEl">${updClockText()}</b><small>${timeLabel}</small></div>` : "")
    + `</div><ol class="us" aria-label="שלבי העדכון">${steps}</ol>`
    + `<div class="upoll" id="updPoll" role="status" aria-live="polite">${updPollHtml()}</div>`
    + `<div class="ubody">${body}</div><div class="uhint">${hint}</div>${foot ? `<div class="ufoot">${foot}</div>` : ""}`;
}

function updRoot() {
  let root = document.getElementById("upd");
  if (!root && document.body && typeof document.createElement === "function") {
    root = document.createElement("div");
    root.id = "upd";
    root.className = "upd";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "updT");
    root.setAttribute("aria-describedby", "updS");
    document.body.appendChild(root);
    const live = document.createElement("div");
    live.id = "updLive"; live.className = "sr-only"; live.setAttribute("aria-live", "assertive");
    document.body.appendChild(live);
  }
  return root;
}
let updLastTitle = "";
function updRender() {
  const root = updRoot();
  if (!root || !UPD) return;
  root.innerHTML = `<div class="uc">${updHtml()}</div>`;
  const h = document.getElementById("updT");
  const text = h ? h.textContent : "";
  if (text !== updLastTitle) {   // מצב חדש: פוקוס לכותרת, והקראה — לא בכל תקתוק
    updLastTitle = text;
    if (h && typeof h.focus === "function") h.focus({ preventScroll: true });
    const live = document.getElementById("updLive");
    if (live) live.textContent = text;
  }
}
function updShow() {
  const root = updRoot();
  if (root) { root.classList.add("show"); root.setAttribute("aria-hidden", "false"); }
  document.body.classList.add("upd-on");
  const app = document.getElementById("app");
  if (app) { app.setAttribute("aria-hidden", "true"); app.inert = true; }
}
function updHide() {
  const root = document.getElementById("upd");
  if (root) { root.classList.remove("show"); root.setAttribute("aria-hidden", "true"); root.innerHTML = ""; }
  document.body.classList.remove("upd-on");
  const app = document.getElementById("app");
  if (app) { app.removeAttribute("aria-hidden"); app.inert = false; }
  updLastTitle = "";
}

/* ---------- כניסה לקונסולה ---------- */

/* עדכון שרץ (נפתח בלשונית אחרת, או שהדף רוענן באמצע) — המסך נפתח מעצמו. "רץ" = ‏running,
   או done שהסקריפט עוד לא סיים (stage לפני done). כשל קריאה = לא פותחים (הקונסולה ממשיכה). */
async function updResume() {
  if (UPD || !ME || ME.role !== "admin") return;
  const r = await updGet("/api/console/update/status");
  if (!r.ok || !r.body) return;
  const st = r.body;
  const inProgress = st.state === "running" || (st.state === "done" && st.stage && st.stage !== "done");
  if (inProgress && st.tag) updStart({ tag: st.tag, from: st.from || null, by: st.by || null, status: st });
}

/* אחרי הטעינה מחדש (‏?updated=vX): "עודכן ✓" רק כש-/me מדווח את אותה גרסה — ראיה, לא הפרמטר. */
function updParam() {
  try {
    const tag = new URLSearchParams(location.search).get("updated") || "";
    return /^v\d+\.\d+\.\d+$/.test(tag) ? tag : "";
  } catch (e) { return ""; }
}
function updDropParam() {
  try {
    const url = new URL(location.href);
    url.searchParams.delete("updated");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  } catch (e) { /* בלי history — הפרמטר נשאר, והבדיקה מול /me חוזרת על עצמה בלבד */ }
}
function updAfterReload() {
  const tag = updParam();
  if (!tag || !ME) return;
  updDropParam();
  const running = String(ME.version || "").match(/^v\d+\.\d+\.\d+/);
  if (running && running[0] === tag) toast(`עודכן ל-${tag} ✓`, 5000, "ok");
  else toast(`הדף נטען אחרי עדכון ל-${tag}, אבל השרת מדווח ${ME.version || "גרסה לא ידועה"} — בדקו בהגדרות › עדכונים`, 9000, "err");
}
/* ‏/me החזיר 401 אחרי העדכון (העוגייה פגה בזמנו): מסך הכניסה אומר למה — לא מופיע בשקט. */
function updLoginNotice() {
  const tag = updParam();
  return tag ? `עודכן ל-${tag} — יש להתחבר מחדש` : "";
}
