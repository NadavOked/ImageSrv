/* ‏#2028 (נדב 23/10; מוקאפ docs/design/mockups-1107/2028-learning.html, אושר 06/11): הלמידה האוטומטית.
   שורת המתג בהגדרות, חלון הלמידה (באמצע / הסתיימה / לא נלמד) והצעת הלמידה למחשב חדש. מנהל בלבד.
   הלמידה נשענת על שיכפולים אמיתיים, ושום כפתור כאן אינו מריץ מדידה שכותבת לדיסק.
   "לא נקרא" נשאר גלוי (עיקרון 5): LEARNING ריק עם שגיאה אינו "אין למידה". */
let LEARNING = null, learningError = "";
const LEARNING_OFF_HE = "כבוי: כל הגדרות המולטיקאסט ידניות, לפי ההמלצות. דלוק: השרת לומד מכמה שיכפולים ומתאים את ההגדרות לעיבוד היעיל ביותר.";
const LEARNING_NO_WRITE_HE = "לא נכתב שום דבר לדיסקים רק בשביל מדידה.";

async function loadLearning() {
  try { LEARNING = await api("/learning"); learningError = ""; }
  catch (e) { LEARNING = null; learningError = e.message; }
}
function learningNotLearnedHe(run) { return `לא נלמד: ${run.learned} מתוך ${run.required}`; }
/* השורה הקטנה מתחת למתג: מה קורה עכשיו, במילים של המוקאפ. */
function learningRowSub() {
  if (!LEARNING) return learningError ? `מצב הלמידה לא נקרא: ${learningError}` : LEARNING_OFF_HE;
  const run = LEARNING.run;
  if (run && run.state === "collecting") return `השרת לומד מהשיכפולים הרגילים: ${run.attempts} מתוך ${run.required}. ${LEARNING_NO_WRITE_HE}`;
  if (!LEARNING.enabled) return LEARNING_OFF_HE;
  if (run && run.state === "not_learned") return `${learningNotLearnedHe(run)}. לא שונה דבר.`;
  if (run && run.state === "done") {
    const n = LEARNING.adjusted_keys.length;
    const when = run.ended_at ? ` (נגמר ב-${fmtDate(run.ended_at)})` : "";
    return `השרת למד מ-${run.learned} שיכפולים${when}. ${n ? `${n === 1 ? "הגדרה אחת הותאמה" : `${n} הגדרות הותאמו`}, אפשר לחזור לכל אחת.` : "לא הותאמה אף הגדרה."}`;
  }
  return LEARNING_OFF_HE;
}
function learningRowHtml(r) {
  const open = LEARNING && (LEARNING.run || LEARNING.changes.length) ? `${UI.link("פתח סיכום", "openLearning()")} ` : "";
  return open + settingSwitchHtml(r);
}
/* התג ליד הגדרה שהלמידה שינתה. שינוי ידני מסיר אותו בשרת. */
function learningChip(key) {
  return LEARNING && LEARNING.adjusted_keys.includes(key) ? ` <span class="n-chip act">הותאם אוטומטית</span>` : "";
}

function learningStepsHtml(run) {
  const cells = Array.from({ length: run.required }, (_, i) => {
    const w = run.waves[i], name = `<b>שיכפול ${i + 1}</b>`;
    if (w) return w.ok ? `<div class="st done">${name}הסתיים · ${esc(fmtDate(w.at))}</div>`
      : `<div class="st bad">${name}לא נספר: ${esc(w.why_he || "אין ראיה")}</div>`;
    if (run.running && i === run.waves.length) return `<div class="st now">${name}רץ עכשיו</div>`;
    return `<div class="st">${name}ממתין לסבב הבא</div>`;
  });
  return `<div class="learn-steps">${cells.join("")}</div>`;
}
function learningCount(n, one, many) { return n === 1 ? one : `${n} ${many}`; }
function learningLevelsHtml(l) {
  const card = (by, title, line, small) => `<div><span class="by">${esc(by)}</span><h6>${esc(title)}</h6>${line}<small>${small}</small></div>`;
  const mbps = (v) => `<bdi dir="ltr">${esc(v)} MB/s</bdi>`;
  const d = l.disks, m = l.machines, n = l.network;
  const links = Object.entries(m.links).sort((a, b) => Number(b[0]) - Number(a[0]))
    .map(([gbps, count]) => `${count} ב-<bdi dir="ltr">${esc(gbps)} Gb/s</bdi>`).join(", ");
  return `<div class="learn-levels">`
    + card("לפי סידורי", "דיסקים", d.count ? `${esc(learningCount(d.count, "דיסק אחד נמדד", "דיסקים נמדדו"))}.` : "עוד לא נמדד דיסק.",
      d.count ? `כתיבה בין ${esc(d.min_mbps)} ל-${mbps(d.max_mbps)}. הנתון נודד עם הדיסק.` : "הנתון נודד עם הדיסק.")
    + card("לפי MAC ופורט", "מחשבים וחיבורים", m.count ? `${esc(learningCount(m.count, "משכפל אחד", "משכפלים"))}, ${esc(learningCount(m.ports, "חיבור אחד", "חיבורים"))}.` : "עוד לא נמדד חיבור.",
      `${links ? `קישור SATA: ${links}. ` : ""}נשאר במקום.`)
    + card("לפי שרת ווילן", "הרשת", n.top_mbit != null ? `זרם עד <bdi dir="ltr">${esc(n.top_mbit)} Mb/s</bdi>.` : "עוד לא נמדד זרם.",
      n.loss != null ? `אבדן חבילות ${esc((n.loss * 100).toFixed(2))}%${n.interface ? `, בכרטיס <bdi dir="ltr">${esc(n.interface)}</bdi>` : ""}.` : "בווילן ההפצה.")
    + `</div>`;
}
const LEARNING_STATE_HE = { reverted: "הוחזר", overridden: "שונה ידנית", replaced: "הוחלף בלמידה מאוחרת" };
function learningDeltaHtml(changes, unchanged) {
  const num = (cls, v) => `<td class="num ${cls}"><bdi dir="ltr">${esc(v)}</bdi></td>`;
  const rows = changes.map((c) => `<tr><td>${esc(c.label_he)}</td>${num("old", c.previous_he)}${num("new", c.value_he)}<td>${esc(c.why_he)}</td>`
    + `<td class="act">${c.state === "applied" ? UI.link("החזר", `learningRevert(${Number(c.id)})`) : `<span class="muted">${esc(LEARNING_STATE_HE[c.state] || c.state)}</span>`}</td></tr>`)
    .concat(unchanged.map((u) => `<tr><td>${esc(u.label_he)}</td><td colspan="2">לא שונה (<bdi>${esc(u.value_he)}</bdi>)</td><td>${esc(u.why_he)}</td><td></td></tr>`));
  return `<table class="learn-delta"><thead><tr><th>הגדרה</th><th>קודם</th><th>חדש</th><th>למה</th><th><span class="sr">פעולות</span></th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}
const LEARNING_SLOW_DRAWER_HE = "<b>למה לא מסבירים מגירה איטית כבר עכשיו:</b> דיסק לא חוזר לאותו מקום. מגירה נחשבת איטית רק אחרי שרואים את אותו דיסק במקום אחר, או דיסק אחר באותו מקום.";
/* מה שהחלון מציג: ‏{title, sub, note, submitLabel, extra}. פונקציה נפרדת מ-sheet כדי שאפשר לבדוק אותה. */
function learningView(state) {
  const run = state.run, applied = state.changes.filter((c) => c.state === "applied");
  if (run && run.state === "collecting") {
    return { title: `למידה אוטומטית: שיכפול ${Math.min(run.attempts + 1, run.required)} מתוך ${run.required}`,
      sub: `השרת לומד מהשיכפולים הרגילים שלכם. ${LEARNING_NO_WRITE_HE}`,
      note: learningStepsHtml(run) + learningLevelsHtml(run.levels) + UI.note("warn", LEARNING_SLOW_DRAWER_HE),
      submitLabel: "סגור, הלמידה ממשיכה", extra: { label: "כבה למידה", onClick: learningOff } };
  }
  if (run && run.state === "not_learned") {
    return { title: learningNotLearnedHe(run), sub: "לא שונה דבר: בלי ראיה אין שינוי. כדי לנסות שוב מכבים ומדליקים את המתג.",
      note: learningStepsHtml(run) + (state.changes.length ? learningDeltaHtml(state.changes, []) : ""), submitLabel: "אישור",
      extra: applied.length ? { label: "החזר הכול", onClick: learningRevertAll } : null };
  }
  const n = applied.length;
  return { title: run ? `הלמידה הסתיימה: ${run.learned} שיכפולים` : "למידה אוטומטית",
    sub: n ? `השרת התאים ${n === 1 ? "הגדרה אחת" : `${n} הגדרות`}. ערך קודם בקו, ערך חדש מודגש. אפשר לחזור לכל אחת.` : "אף הגדרה לא הותאמה: לא נמצאה ראיה לשינוי.",
    note: (run ? learningLevelsHtml(run.levels) : "") + learningDeltaHtml(state.changes, run ? run.unchanged : [])
      + UI.note("ok", "הלמידה נרשמה ביומן. מעכשיו הקצב ממשיך להשתפר מעצמו לפי \"קצב שידור אוטומטי\". אפשר לכבות את המתג בכל רגע, וההגדרות נשארות כפי שהן."),
    submitLabel: "אישור", extra: n ? { label: "החזר הכול", onClick: learningRevertAll } : null };
}
async function openLearning() {
  if (!isAdmin()) return;
  await loadLearning();
  if (!LEARNING) { toast(`מצב הלמידה לא נקרא: ${learningError}`); return; }
  sheet({ ...learningView(LEARNING), size: "wide", onSubmit: async () => {} });
  const cancel = document.getElementById("sf-cancel");
  if (cancel) cancel.style.display = "none";   // שני כפתורים, כמו במוקאפ; ‏.btn גובר על hidden
}
async function learningAfter(action, done) {
  try { await action(); toast(done); }
  catch (e) { toast(e.message); }
  await loadSettingsData();
  openLearning();
}
function learningRevert(id) { return learningAfter(() => post(`/learning/changes/${Number(id)}/revert`, {}), "ההגדרה הוחזרה — נרשם ביומן"); }
function learningRevertAll() { return learningAfter(() => post("/learning/revert-all", {}), "ההגדרות הוחזרו — נרשם ביומן"); }
async function learningOff() {
  try { await post("/settings", { learning_enabled: "false" }); toast("הלמידה כובתה — ההגדרות נשארו כפי שהן"); }
  catch (e) { toast(e.message); }
  await loadSettingsData();
}

/* ---------- מחשב חדש (מוקאפ, חלק 3) ----------
   ההצעה מופיעה במחשב בנייה או שיכפול שעוד לא נמדדו החיבורים שלו. "נלמד" נגזר מהמדידות בשרת, ולכן
   היא נעלמת לבד ולא חוזרת. ‏m.learning ריק = לא מנהל, או שהמצב לא נקרא: אין הצעה. */
function learningOfferHtml(m) {
  const l = m.learning;
  if (!l || SERVER_CTX || !isAdmin() || !["build", "cloner"].includes(machineRole(m))) return "";   // הנתיב אינו בערוץ בין השרתים
  const macEnc = encodeId(m.mac);
  if (l.state === "learned") return UI.note("ok", esc(`נלמד: ${l.waves} שיכפולים, ${l.ports} חיבורים נמדדו. אין מה להפעיל.`));
  if (l.state === "started") return UI.note("info", esc(`למידה פעילה: ${l.waves} מתוך ${l.required} שיכפולים נמדדו. לא נכתב דבר לדיסקים רק בשביל מדידה.`));
  if (l.state !== "offer") return "";
  return `<div class="learn-offer"><span class="t"><b>המחשב הזה עוד לא נלמד.</b> אם תפעילו למידה, השרת ימדוד את הדיסקים והחיבורים שלו ב-${esc(l.required)} השיכפולים הבאים. לא נכתב דבר לדיסקים רק בשביל מדידה.</span>`
    + `<button class="btn primary sm" type="button" onclick="learningMachine('${macEnc}','start')">הפעל למידה</button>`
    + `<button class="btn sm" type="button" onclick="learningMachine('${macEnc}','dismiss')">לא עכשיו</button></div>`;
}
function learningRowPill(m) {
  return m.learning && m.learning.state === "offer" && !SERVER_CTX && isAdmin() && ["build", "cloner"].includes(machineRole(m)) ? ` <span class="n-chip n">לא נלמד</span>` : "";
}
async function learningMachine(macEnc, action) {
  if (!isAdmin()) return;
  try { await post(`/machines/${macEnc}/learning`, { action }); }
  catch (e) { toast(e.message); return; }
  await loadMachines();
  let mac = macEnc; try { mac = decodeURIComponent(macEnc); } catch (e) {}
  const fresh = findMachine(mac);
  if (fresh) setDrawerBody(machineDrawerHtml(fresh));
}
