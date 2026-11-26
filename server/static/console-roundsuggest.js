// ‏#3897 (נדב 20/11, המוקאפ handoffs/backlog-mockups-1115/m3897-round-open.html, מצבים 1–3): "הצעת השרת" בחלון "התחל סבב".
// המפעיל כותב כמה דיסקים; השרת (‏GET /room/suggest) מציע משכפלים ומגירות לפי המספר שלהם, והחלון ממלא את הבחירה.
// ‏#1410 נשאר: הבחירה מפורשת, והאישור הוא אותו "פתח סבב". הקונסולה לא מחליטה דבר — היא מציגה את ההצעה, ולחיצה על
// מגירה משנה את הבחירה (ואז "נערך ידנית", ו"חזרה להצעת השרת" מביאה הצעה חדשה). כחול = נבחרה; אדום = אסור כיעד.
const RS = { timer: 0, seq: 0 };

function rsTarget() {
  const n = Number(DEPLOY.form.target);
  return DEPLOY.form.target !== "" && Number.isInteger(n) && n > 0 ? n : 0;
}
// הקלדה → הצעה אחרי הפסקה קצרה. ‏null = אין מה להציע (בלי כמות, או הפצה ישירה — שם היעד מפורש תמיד).
function rsSchedule() {
  clearTimeout(RS.timer);
  if (!rsTarget() || DEPLOY.form.src === "build_disk") { RS.seq++; DEPLOY.form.sug = null; return; }
  RS.timer = setTimeout(() => rsFetch(), 300);
}
async function rsFetch() {
  const n = rsTarget(), seq = ++RS.seq;
  if (!n) return;
  let data = null, err = "";
  try { data = await api(`/room/suggest?target_drives=${n}`); } catch (e) { err = e && e.message ? e.message : String(e); }
  if (seq !== RS.seq) return;   // הוקלד מספר אחר בינתיים
  if (data && !Array.isArray(data.selected)) { data = null; err = "תשובת ההצעה אינה תקינה"; }
  // כשל בקריאה: הבחירה הידנית נשארת, והכרטיס אומר שההצעה לא נקראה (לא "אין משכפלים פנויים").
  DEPLOY.form.sug = { n, data, err, edited: false };
  if (data) {
    DEPLOY.form.sel = {};
    data.selected.forEach((it) => it.ports.forEach((p) => { DEPLOY.form.sel[`${it.mac}@${p}`] = true; }));
  }
  rsRender();
}
function rsRender() {
  const had = typeof document !== "undefined" && document.activeElement && document.activeElement.id === "rn-target";
  roomRoundRender();
  const el = had ? document.getElementById("rn-target") : null;
  if (el && el.focus) { el.focus(); try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) { /* ‏type=number */ } }
}
function rsEdited() { if (DEPLOY.form.sug) DEPLOY.form.sug.edited = true; }
function rsStep(delta) {
  const n = Math.max(1, (rsTarget() || Number(roomTargetDefault(roomMachines()).n) || 1) + delta);
  DEPLOY.form.target = String(n);
  rsFetch();
}
function rsReset() { rsFetch(); }
function rsOnly(n) { DEPLOY.form.target = String(n); rsFetch(); }
// לחיצה על מגירה בכרטיס = אותה בחירה כמו [יעד] בטבלה.
function rsTile(key) { roomPick(key); }
function rsMachine(mac) {
  const sug = DEPLOY.form.sug, m = sug && sug.data && sug.data.machines.find((x) => x.mac === mac);
  if (!m) return;
  const keys = m.drawers.filter((d) => d.state === "selected" || d.state === "spare").map((d) => `${mac}@${d.port}`);
  const on = keys.every((k) => DEPLOY.form.sel[k]);
  roomPickSet(roomPickSlots(roomMachines()).filter((x) => x.ok && keys.includes(x.key)), !on);
}
function rsClear() { roomPickClearAll(); }

function rsMachineName(m) { return m.display_name || m.name || m.mac; }
function rsJoin(names) { return names.length < 2 ? names.join("") : names.slice(0, -1).join(", ") + " ו-" + names[names.length - 1]; }
function rsTileHtml(mac, d) {
  const n = d.drawer != null ? d.drawer : d.port != null ? d.port : "—", key = `${mac}@${d.port}`;
  if (d.state === "red") return `<span class="dt red" role="img" aria-label="${esc(`מגירה ${n} אדומה — לא יעד${d.reason_he ? ": " + d.reason_he : ""}`)}" title="${esc(d.reason_he || "אדומה — לא יעד")}"><span class="led"></span>✕ ${n}</span>`;
  if (d.state === "absent") return `<span class="dt none" aria-label="${esc(`מגירה ${n} ריקה`)}">${n}</span>`;
  const on = !!DEPLOY.form.sel[key], manual = on && d.state === "spare";
  return `<button type="button" class="dt${on ? " sel2" : ""}${manual ? " add" : ""}" aria-pressed="${on}" aria-label="${esc(`מגירה ${n}${on ? ", נבחרה" : ""}`)}" onclick="rsTile(${jsStr(key)})"><span class="led"></span>${on ? "✓ " : ""}${n}</button>`;
}
function rsCloner(m) {
  if (m.state === "busy") {
    return `<div class="cl busy"><div class="t"><span>${esc(rsMachineName(m))}</span><span class="tg m">בסבב</span></div>`
      + `<div class="rn">${esc(m.busy_in === "local_copy" ? "שיכפול מקומי" : m.busy_in || "")}</div></div>`;
  }
  const chip = m.awake ? `<span class="tg m">ער</span>` : `<span class="tg or">כבוי · WoL</span>`;
  return `<div class="cl"><div class="t"><button type="button" class="nm" onclick="rsMachine(${jsStr(m.mac)})" title="לחיצה: כל המגירות של המשכפל">${esc(rsMachineName(m))}</button>${chip}</div>`
    + `<div class="dw">${m.drawers.map((d) => rsTileHtml(m.mac, d)).join("")}</div></div>`;
}
// הודעות מעל המפה. ‏info — אדומה שדולגה והשלמה חלקית; ‏warn — אין מספיק פנויים: פס ייצור, גלים, וחלופה ב-"רק N דיסקים".
function rsBanners(sug) {
  const d = sug.data, out = [];
  if (sug.edited) return out;
  const picked = d.machines.filter((m) => m.drawers.some((x) => x.state === "selected")), last = picked[picked.length - 1];
  const reds = d.machines.filter((m) => m.state !== "busy" && (!last || (m.number != null && last.number != null ? m.number <= last.number : true)))
    .flatMap((m) => m.drawers.filter((x) => x.state === "red").map((x) => ({ m, x })));
  const partial = last && last.drawers.some((x) => x.state === "spare");
  if (reds.length && d.selected_count > 0) {
    const what = reds.map(({ m, x }) => `${rsMachineName(m)} מגירה ${x.drawer != null ? x.drawer : x.port} אדומה`).join(", ");
    out.push(`<div class="alert info"><span class="tx"><b>${esc(what)}</b> ולכן לא הוצעה (דיסק אדום אינו יעד).`
      + `<small>${esc(`הכמות שבחרתם (${d.target_drives}) קובעת${partial ? `: ${rsMachineName(last)} נכנס חלקית, ולא כל הפנויים` : ""}.`)}</small></span></div>`);
  }
  if (d.shortfall > 0) {
    const free = d.machines.filter((m) => m.drawers.some((x) => x.state === "selected")), busy = d.machines.filter((m) => m.state === "busy");
    const sizes = [], full = d.selected_count;
    for (let left = d.target_drives; full > 0 && left > 0; left -= full) sizes.push(Math.min(full, left));
    const lineNote = d.line ? ` <span class="tg w"><i></i>פס ייצור</span>` : "";
    const tx = d.selected_count
      ? `פנויים: ${esc(rsJoin(free.map(rsMachineName)))} (${d.selected_count} מגירות).${busy.length ? ` ${esc(rsJoin(busy.map(rsMachineName)))} בסבב אחר או בשיכפול מקומי ולא נכלל${busy.length > 1 ? "ו" : ""}.` : ""}`
      : "אין משכפל פנוי עם מגירה שאפשר לכתוב אליה.";
    out.push(`<div class="alert warn"><span class="tx"><b>אין מספיק משכפלים פנויים ל-${d.target_drives} דיסקים בבת אחת.</b>${lineNote}<small>${tx}</small>`
      + (d.line ? `<div class="waves">${sizes.map((s, i) => `<div>גל ${i + 1} · ${s} דיסקים</div>`).join("")}</div>` : "") + `</span>`
      + (d.selected_count ? `<button type="button" class="btn sm" onclick="rsOnly(${d.selected_count})">רק ${d.selected_count} דיסקים</button>` : "") + `</div>`);
  }
  return out;
}
function rsCard() {
  const sug = DEPLOY.form.sug, f = DEPLOY.form;
  const head = (sub, acts) => `<div class="card prop"><div class="th">הצעת השרת<span class="s">${sub}</span><span class="acts">${acts}</span></div>`;
  if (!sug) {
    const direct = f.src === "build_disk";
    return head(direct ? "" : "כתבו כמה דיסקים לשכפל", "") + `<div class="body"><div class="hn">${direct ? "בהפצה ישירה היעד מפורש: מסמנים את הדיסקים בטבלה." : "השרת יציע את המשכפלים הפנויים לפי המספר שלהם, ואתם מאשרים. אפשר גם לסמן ידנית בטבלה."}</div></div></div>`;
  }
  if (!sug.data) {
    return head("", "") + `<div class="body"><div class="alert warn"><span class="tx"><b>ההצעה לא נקראה</b><small>${esc(sug.err || "")}. אפשר לסמן ידנית בטבלה.</small></span><button type="button" class="btn sm" onclick="rsReset()">נסה שוב</button></div></div></div>`;
  }
  const d = sug.data, nsel = Object.keys(f.sel).length, macs = new Set(Object.keys(f.sel).map((k) => k.split("@")[0])).size;
  const sub = `${nsel} דיסקים ב-${macs} משכפלים · ${sug.edited ? "<b>נערך ידנית</b>" : "לפי סדר המספרים"}`;
  const acts = (sug.edited ? `<button type="button" class="btn sm" onclick="rsReset()">חזרה להצעת השרת</button>` : "") + `<button type="button" class="btn sm" onclick="rsClear()">נקה הכול</button>`;
  const legend = `<div class="lgn"><span><span class="sq s"></span>נבחרה (לחיצה מורידה)</span><span><span class="sq"></span>פנויה, לא נבחרה (לחיצה מוסיפה)</span><span><span class="sq r"></span>אדומה, לא יעד</span><span><span class="sq b"></span>בסבב אחר</span><span>לחיצה על שם משכפל: כולו</span></div>`;
  return head(sub, acts) + `<div class="body">${rsBanners(sug).join("")}<div class="cls">${d.machines.map(rsCloner).join("")}</div>${legend}</div></div>`;
}
// שדה הכמות עם −/+ (המוקאפ).
function rsStepper(target) {
  return `<div class="num"><button type="button" class="st" aria-label="פחות דיסק אחד" onclick="rsStep(-1)">−</button>`
    + `<input type="number" id="rn-target" min="1" value="${esc(target)}" oninput="roomFormSet('target',this.value)" aria-label="כמה דיסקים בסך הכול">`
    + `<button type="button" class="st" aria-label="עוד דיסק אחד" onclick="rsStep(1)">+</button></div>`;
}
// שורת הסיכום בתחתית: ‏WoL — משכפל כבוי שנבחר יידלק, והסבב ימתין לו.
function rsWolNote(kids) {
  const sug = DEPLOY.form.sug, off = [];
  if (!sug || !sug.data) return "";
  const macs = new Set(Object.keys(DEPLOY.form.sel).map((k) => k.split("@")[0]));
  sug.data.machines.forEach((m) => { if (m.state !== "busy" && !m.awake && macs.has(m.mac)) off.push(rsMachineName(m)); });
  return off.length ? ` · ${rsJoin(off)} ${off.length > 1 ? "כבויים" : "כבוי"}: ${off.length > 1 ? "יידלקו" : "יידלק"} ב-WoL והסבב ימתין ${off.length > 1 ? "להם" : "לו"}` : "";
}
