/* ImageSrv -- דף "דיסקים" תחת מלאי + לשונית "סטים" (#1857, מוקאפ 04 מאושר, נדב 24/10).
   סט = שם + קידומת ("קוריאים" / xx): מדבקות xx-INS, xx-1 … xx-29. השם הולך עם הדיסק לפי הסידורי, לא עם
   המגירה, ותצוגה בלבד. ה-API: server/console_disk_sets.py (interfaces.md). "לא נקרא" אינו "אין" (עיקרון 5):
   כשל בקריאה מוצג כהודעה, לא כרשימה ריקה. משתמש ב-UI/api/post/sheet/toast/esc/fmtWhenShort/smartReasonHe
   מ-console.js (נטען אחריו). */
"use strict";

const DK = { disks: null, sets: null, rounds: null, err: "", tab: 0, selSet: null, members: null, membersErr: "",
  filter: { q: "", mac: "", round: "", drawer: "", image: "", state: "", model: "", noset: false }, picked: new Set() };
const DK_TABS = ["כל הדיסקים", "אדומים", "כתומים", "סטים"];
const DK_STATE = { red: ["err", "אדום"], orange: ["warn", "כתום"], ok: ["ok", "תקין"], unchecked: ["", "לא נבדק"] };

async function loadDisks() {
  try {
    [DK.disks, DK.sets, DK.rounds] = await Promise.all([api("/disks"), api("/disk-sets"), api("/disk-sets/rounds")]);
    DK.disks = DK.disks.disks;
    DK.err = "";
    if (DK.selSet != null && !DK.sets.some((s) => s.id === DK.selSet)) DK.selSet = null;
    if (DK.selSet == null && DK.sets.length) DK.selSet = DK.sets[0].id;
    await loadDiskSetMembers();
  } catch (e) {
    DK.disks = DK.sets = DK.rounds = null;   // לא נקרא ≠ אין דיסקים
    DK.err = e.message;
    toast("טעינת הדיסקים נכשלה: " + e.message);
  }
  if (current === "disks") renderCurrent();
}
async function loadDiskSetMembers() {
  DK.members = null; DK.membersErr = "";
  if (DK.selSet == null) return;
  try { DK.members = (await api(`/disk-sets/${DK.selSet}/members`)).disks; }
  catch (e) { DK.membersErr = e.message; }
}

function dkImageName(id) {
  const img = Array.isArray(IMAGES) ? IMAGES.find((i) => i.id === id) : null;
  return img && img.name ? img.name : (id || "");
}
function dkSize(b) { return Number.isFinite(b) && b > 0 ? `<bdi dir="ltr">${esc(fmtBytes(b))}</bdi>` : ""; }
function dkDays(ts) {
  const ms = Date.parse(ts);
  if (!Number.isFinite(ms)) return "";
  const d = Math.floor((Date.now() - ms) / 86400000);
  return d <= 0 ? "היום" : d === 1 ? "אתמול" : `לפני ${d} ימים`;
}
/* "איפה עכשיו": מכונה ומגירה כשהדיסק בה, אחרת "לא במשכפל" (עם המקום האחרון ב-title). */
function dkWhere(d) {
  if (d.in_machine && d.machine) return `${esc(d.machine)}${d.port != null ? ` · מגירה ${esc(d.port)}` : ""}`;
  return `<span class="muted"${d.machine ? ` title="${esc(`נראה לאחרונה ב${d.machine}${d.port != null ? `, מגירה ${d.port}` : ""} · ${fmtWhenShort(d.last_seen)}`)}"` : ""}>לא במשכפל</span>`;
}
function dkStateCell(d) {
  const [cls, text] = DK_STATE[d.state] || DK_STATE.unchecked;
  let sub = d.reason ? smartReasonHe(d.reason) : (d.state === "unchecked" && !d.last_good_write ? "לא נכתב מאז ששויך" : "");
  return `${UI.pill(cls, text)}${sub ? `<span class="sub">${esc(sub)}</span>` : ""}`;
}
function dkLastGood(d) {
  if (!d.last_good_write) return `<span class="muted">—</span>`;
  const fail = d.last_fail && d.last_fail > d.last_good_write ? `<span class="sub">נכשל ב-${esc(fmtWhenShort(d.last_fail))}</span>` : "";
  return `${esc(dkDays(d.last_good_write))}<span class="sub">${esc(fmtWhenShort(d.last_good_write))}</span>${fail}`;
}
function dkLabel(d) { return d.label ? `<span class="lbl${d.suffix === "INS" ? " ins" : ""}">${esc(d.label)}</span>` : `<span class="muted">בלי סט</span>`; }

/* ---------------------------------------------------------------- כותרת */
function dkCounts() {
  const all = DK.disks || [];
  return { all: all.length, red: all.filter((d) => d.state === "red").length, orange: all.filter((d) => d.state === "orange").length,
    noset: all.filter((d) => !d.set_id).length, sets: (DK.sets || []).length };
}
function dkHeader() {
  const c = dkCounts();
  const tabs = DK.disks ? [`${DK_TABS[0]} (${c.all})`, `${DK_TABS[1]} (${c.red})`, `${DK_TABS[2]} (${c.orange})`, `${DK_TABS[3]} (${c.sets})`] : DK_TABS;
  const sub = DK.disks ? esc(`${c.all} דיסקים · ${c.sets} סטים · ${c.noset} דיסקים בלי סט`) : `‏/disks לא נקרא: ${esc(DK.err)}`;
  const pill = DK.disks && c.red ? UI.pill("err", `${c.red} אדומים`) : (DK.disks ? "" : UI.pill("err", "לא נקרא"));
  const actions = `<button class="btn" onclick="diskSetNewSheet()">+ סט חדש</button><button class="btn primary" onclick="diskAssignRoundSheet()">שייך מסבב</button>`
    + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  return UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "מלאי" }, { label: "דיסקים" }],
    icon: "t-disks", name: "דיסקים", sub, pill, actions, tabs, tab: DK.tab, tabClick: (i) => `disksTab(${i})`, compact: true });
}
function disksTab(i) { DK.tab = i; if (current === "disks") renderCurrent(); }

function disksPage() {
  if (!DK.disks && !DK.err) return pagePlaceholder();
  const body = !DK.disks ? UI.note("err", `לא הצלחתי לקרוא את הדיסקים: ${esc(DK.err)}`)
    : DK.tab === 3 ? dkSetsTab() : dkListTab(DK.tab === 1 ? "red" : DK.tab === 2 ? "orange" : "");
  return `<div class="page aligned">${dkHeader()}<div class="body">${body}</div></div>`;
}

/* ---------------------------------------------------------------- כל הדיסקים / אדומים / כתומים */
function dkFiltered(stateOnly) {
  const f = DK.filter, q = f.q.trim().toLowerCase();
  const inRound = f.round ? new Set(((DK.rounds || []).find((r) => `${r.session_id}|${r.mac}` === f.round) || { drawers: [] }).drawers.map((d) => d.serial)) : null;
  const inImage = f.image ? new Set((DK.rounds || []).filter((r) => r.image_id === f.image).flatMap((r) => r.drawers.map((d) => d.serial))) : null;
  return DK.disks.filter((d) =>
    (!stateOnly || d.state === stateOnly) && (!f.state || d.state === f.state) && (!f.mac || d.mac === f.mac)
    && (!f.drawer || String(d.port) === f.drawer) && (!f.model || d.model === f.model) && (!f.noset || !d.set_id)
    && (!inRound || inRound.has(d.serial)) && (!inImage || inImage.has(d.serial))
    && (!q || [d.serial, d.label, d.set_name, d.model].some((x) => x && String(x).toLowerCase().includes(q))));
}
function dkSelect(key, label, options, cur) {
  return `<select class="dk-sel${cur ? " on" : ""}" aria-label="${esc(label)}" onchange="dkFilter('${key}',this.value)"><option value="">${esc(label)}</option>${options.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(cur) ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
}
function dkFilterBar() {
  const f = DK.filter, all = DK.disks;
  const uniq = (xs) => [...new Set(xs.filter((x) => x != null && x !== ""))];
  const machines = uniq(all.map((d) => d.mac)).map((m) => [m, (all.find((d) => d.mac === m) || {}).machine || m]);
  const rounds = (DK.rounds || []).map((r) => [`${r.session_id}|${r.mac}`, `${r.machine} · ${fmtWhenShort(r.at)}`]);
  const images = uniq((DK.rounds || []).map((r) => r.image_id)).map((i) => [i, dkImageName(i)]);
  const states = Object.entries(DK_STATE).map(([k, [, t]]) => [k, t]);
  return `<div class="dg-bar dk-bar"><input type="search" class="dk-q" value="${esc(f.q)}" placeholder="חיפוש: מספר סידורי, שם בסט, דגם…" aria-label="חיפוש דיסק" oninput="dkFilter('q',this.value)">`
    + dkSelect("mac", "כל המכונות", machines, f.mac) + dkSelect("round", "כל הסבבים", rounds, f.round)
    + dkSelect("drawer", "כל המגירות", uniq(all.map((d) => d.port)).sort((a, b) => a - b).map((p) => [String(p), `מגירה ${p}`]), f.drawer)
    + dkSelect("image", "כל האימג׳ים", images, f.image) + dkSelect("state", "כל המצבים", states, f.state)
    + dkSelect("model", "כל הדגמים", uniq(all.map((d) => d.model)).sort().map((m) => [m, m]), f.model)
    + `<label class="dk-chk"><input type="checkbox"${f.noset ? " checked" : ""} onchange="dkFilter('noset',this.checked)"> בלי סט</label></div>`;
}
function dkFilter(key, value) {
  DK.filter[key] = value;
  const keep = key === "q" ? document.querySelector(".dk-q") : null;
  const pos = keep ? keep.selectionStart : 0;
  if (current === "disks") renderCurrent();
  if (key === "q") { const q = document.querySelector(".dk-q"); if (q) { q.focus(); q.setSelectionRange(pos, pos); } }
}
function dkPick(serial, on) { if (on) DK.picked.add(serial); else DK.picked.delete(serial); if (current === "disks") renderCurrent(); }
function dkPickAll(on, serials) { serials.forEach((s) => (on ? DK.picked.add(s) : DK.picked.delete(s))); if (current === "disks") renderCurrent(); }
function dkListTab(stateOnly) {
  const rows = dkFiltered(stateOnly);
  const serials = rows.map((d) => d.serial);
  const chosen = rows.filter((d) => DK.picked.has(d.serial));
  const allOn = rows.length && chosen.length === rows.length;
  const cols = [{ html: `<input type="checkbox" aria-label="סימון הכול"${allOn ? " checked" : ""} onchange='dkPickAll(this.checked,${JSON.stringify(serials)})'>` },
    "מספר סידורי", "דגם", "סט", "מצב", "איפה עכשיו", "כתיבה תקינה אחרונה"];
  const body = rows.map((d) => ({ attrs: DK.picked.has(d.serial) ? 'class="sel"' : "", cells: [
    `<input type="checkbox" aria-label="סימון ${esc(d.serial)}"${DK.picked.has(d.serial) ? " checked" : ""} onchange="dkPick('${esc(d.serial)}',this.checked)">`,
    `<span class="mono" dir="ltr">${esc(d.serial)}</span>`, `${esc(d.model || "—")}${d.size_bytes ? ` · ${dkSize(d.size_bytes)}` : ""}`,
    dkLabel(d), dkStateCell(d), dkWhere(d), dkLastGood(d)] }));
  const assign = chosen.length ? `<div class="dk-pick"><button class="btn primary" onclick="diskAssignPickedSheet()">שייך ${chosen.length} נבחרים לסט…</button></div>` : "";
  return `<div class="c12 card">${dkFilterBar()}${assign}${UI.datagrid({ columns: cols, rows: body, empty: DK.disks.length ? "אין דיסקים שמתאימים לסינון" : "השרת עוד לא ראה אף דיסק — הם נרשמים כשמשכפל או מחשב בנייה מתחבר" })}</div>`;
}

/* ---------------------------------------------------------------- סטים */
function dkSetCard(s) {
  const c = s.counts, chips = [c.red && UI.pill("err", `${c.red} אדום`), c.orange && UI.pill("warn", `${c.orange} כתומים`),
    c.ok && UI.pill("ok", `${c.ok} תקינים`), c.unchecked && UI.pill("", `${c.unchecked} לא נבדק`)].filter(Boolean).join("");
  const cls = { red: "b", orange: "w", ok: "", unchecked: "n", empty: "e" };
  const grid = s.grid.map((g, i) => `<i class="${cls[g]}" title="${esc(`${s.prefix}-${s.plan[i]}`)}"></i>`).join("");
  const range = `${s.instructor ? `${s.prefix}-INS, ` : ""}${s.prefix}-1${s.students > 1 ? ` … ${s.prefix}-${s.students}` : ""}`;
  return `<div class="setcard${s.id === DK.selSet ? " sel" : ""}" role="button" tabindex="0" onclick="diskSetSelect(${s.id})" onkeydown="if(event.key==='Enter')diskSetSelect(${s.id})">
    <h5>${esc(s.name)} <span class="pre">${esc(s.prefix)}</span></h5><small>${s.capacity} דיסקים · <bdi dir="ltr">${esc(range)}</bdi> · משויכים ${s.assigned}</small>
    <div class="health">${chips || UI.pill("", "אין דיסקים משויכים")}</div><div class="grid">${grid}</div></div>`;
}
async function diskSetSelect(id) { DK.selSet = id; await loadDiskSetMembers(); if (current === "disks") renderCurrent(); }
let DK_ALL = false;
function dkSetsTab() {
  const cards = DK.sets.map(dkSetCard).join("")
    + `<div class="setcard add" role="button" tabindex="0" onclick="diskSetNewSheet()"><div><b>+ סט חדש</b><br><small>שם, קידומת, וכמה דיסקים</small></div></div>`;
  const set = DK.sets.find((s) => s.id === DK.selSet);
  let table = "";
  if (set) {
    let inner;
    if (DK.membersErr) inner = UI.note("err", `לא הצלחתי לקרוא את דיסקי הסט: ${esc(DK.membersErr)}`);
    else if (!DK.members) inner = pagePlaceholder();
    else {
      const shown = DK_ALL ? DK.members : DK.members.slice(0, 10);
      const cols = ["מדבקה", "מצב", "דגם ומספר סידורי", "איפה עכשיו", "כתיבה תקינה אחרונה", ""];
      const rows = shown.map((d) => ({ attrs: d.state === "red" ? 'class="dk-red"' : "", cells: [
        dkLabel(d), dkStateCell(d), `${esc(d.model || "—")}<span class="sub mono" dir="ltr">${esc(d.serial)}</span>`, dkWhere(d), dkLastGood(d),
        `<button class="btn sm${d.state === "red" ? " danger" : ""}" onclick="diskReplaceSheet(${set.id},'${esc(d.suffix)}')">החלף…</button>`] }));
      const more = DK.members.length > 10 ? `<button class="btn sm flat" onclick="DK_ALL=!DK_ALL;renderCurrent()">${DK_ALL ? "הצג 10" : `הצג את כל ${DK.members.length}`}</button>` : "";
      inner = UI.datagrid({ columns: cols, rows, empty: "אין עדיין דיסקים בסט הזה — שייכו מסבב או מרשימת הדיסקים" }) + (more ? `<div class="dk-more">${more}</div>` : "");
    }
    table = `<div class="c12 card"><div class="card-h"><span>${esc(set.name)} · <bdi dir="ltr">${esc(set.prefix)}</bdi> <small>מסודר לפי המצב, ואז לפי המדבקה${DK.members ? ` · מציג ${Math.min(DK.members.length, DK_ALL ? DK.members.length : 10)} מתוך ${DK.members.length}` : ""}</small></span></div><div class="card-b flush">${inner}</div></div>`;
  }
  return `<div class="c12 setcards">${cards}</div>${table}`;
}

/* ---------------------------------------------------------------- חלונות */
function diskSetNewSheet() {
  if (!isAdmin()) return;
  sheet({ title: "סט חדש", sub: "השם מוצג בקונסולה ובמסך המשכפל, ליד המגירה. הוא לא נכתב לדיסק.",
    fields: [{ id: "name", label: "שם הסט", value: "" }, { id: "prefix", label: "קידומת", value: "", dir: "ltr", help: "1–8 אותיות לטיניות או ספרות, למשל xx" },
      { id: "students", label: "כמה דיסקים לתלמידים", value: "29", dir: "ltr" }, { id: "instructor", label: "דיסק מרצה (INS)", type: "checkbox", value: true }],
    note: UI.note("info", "השמות יהיו <bdi dir=\"ltr\"><b>xx-INS, xx-1 … xx-N</b></bdi> לפי הקידומת והמספר שבחרתם. הדיסקים עצמם משויכים אחר כך, מסבב או מהרשימה."),
    submitLabel: "צור סט",
    onSubmit: async (v) => {
      const students = Number(String(v.students).trim());
      if (!Number.isInteger(students)) throw new Error("כמה דיסקים לתלמידים: מספר שלם");
      const r = await post("/disk-sets", { name: String(v.name).trim(), prefix: String(v.prefix).trim(), students, instructor: !!v.instructor });
      DK.selSet = r.id; DK.tab = 3;
      toast("הסט נוצר — נרשם ביומן");
      await loadDisks();
    } });
}
/* חלון 1: בחירת סבב וסט. חלון 2 (diskAssignNamesSheet): שם לכל דיסק -- ריק = לא משייכים. */
function diskAssignRoundSheet() {
  if (!isAdmin()) return;
  if (!DK.sets || !DK.sets.length) { toast("אין עדיין סט — צרו סט חדש קודם"); diskSetNewSheet(); return; }
  if (!DK.rounds || !DK.rounds.length) { toast("אין סבבים עם דיסקים רשומים — אפשר לשייך מרשימת הדיסקים"); return; }
  const rounds = DK.rounds.map((r, i) => ({ value: String(i), label: `${r.machine} · ${fmtWhenShort(r.at)} · ${dkImageName(r.image_id) || "—"} · ${r.drawers.length} מגירות` }));
  sheet({ title: "שיוך מסבב לסט", sub: "בוחרים סבב, ומקבלים את הדיסקים שנכתבו בו לפי סדר המגירות.",
    fields: [{ id: "round", label: "הסבב", type: "select", value: "0", options: rounds },
      { id: "set", label: "לסט", type: "select", value: String(DK.selSet ?? DK.sets[0].id), options: DK.sets.map((s) => ({ value: String(s.id), label: `${s.name} · ${s.prefix}` })) }],
    submitLabel: "הבא",
    onSubmit: async (v) => {
      const round = DK.rounds[Number(v.round)], set = DK.sets.find((s) => String(s.id) === v.set);
      return () => diskAssignNamesSheet(set, round.drawers.map((d) => ({ serial: d.serial, model: d.model, port: d.port, written: d.written, in_set: d.in_set })), `${round.machine} · ${fmtWhenShort(round.at)}`);
    } });
}
function diskAssignPickedSheet() {
  const picked = DK.disks.filter((d) => DK.picked.has(d.serial));
  if (!picked.length) return;
  if (!DK.sets.length) { toast("אין עדיין סט — צרו סט חדש קודם"); diskSetNewSheet(); return; }
  sheet({ title: `שיוך ${picked.length} דיסקים לסט`, sub: "בוחרים את הסט, ואז נותנים לכל דיסק את המספר שלו.",
    fields: [{ id: "set", label: "לסט", type: "select", value: String(DK.selSet ?? DK.sets[0].id), options: DK.sets.map((s) => ({ value: String(s.id), label: `${s.name} · ${s.prefix}` })) }],
    submitLabel: "הבא",
    onSubmit: async (v) => {
      const set = DK.sets.find((s) => String(s.id) === v.set);
      return () => diskAssignNamesSheet(set, picked.map((d) => ({ serial: d.serial, model: d.model, port: d.port, written: true, in_set: d.label })), "הדיסקים שנבחרו");
    } });
}
function diskAssignNamesSheet(set, drawers, where) {
  // המספר הפנוי הבא לכל דיסק זכאי; דיסק שכבר בסט, או שלא נכתב בסבב, מתחיל ריק
  const taken = new Set((DK.disks || []).filter((d) => d.set_id === set.id).map((d) => d.suffix));
  let next = 1;
  const free = () => { while (taken.has(String(next))) next++; return String(next++); };
  const fields = drawers.map((d, i) => {
    const off = !!d.in_set || !d.written;
    return { id: `s${i}`, label: `${d.port != null ? `מגירה ${d.port} · ` : ""}${d.model || "דיסק"} · ${d.serial}${d.in_set ? ` — כבר בסט: ${d.in_set}` : !d.written ? " — לא נכתב בסבב" : ""}`,
      value: off ? "" : free(), dir: "ltr", help: off ? "ריק = לא משייכים" : undefined };
  });
  sheet({ title: `שיוך ל${set.name}`, sub: `${where}. כותבים את המספר (או INS); ריק = לא משייכים. שני דיסקים לא יכולים לקבל אותו שם.${set.next_free ? ` המספר הפנוי הבא: ${set.next_free}.` : ""}`,
    fields, submitLabel: "שייך",
    onSubmit: async (v) => {
      const items = drawers.map((d, i) => ({ serial: d.serial, suffix: String(v[`s${i}`] || "").trim() })).filter((x) => x.suffix);
      if (!items.length) throw new Error("לא נבחר דיסק לשיוך");
      const seen = new Set();
      for (const it of items) { const k = it.suffix.toUpperCase(); if (seen.has(k)) throw new Error(`יש שם כפול: ${set.prefix}-${it.suffix} — תקנו כדי להמשיך`); seen.add(k); }
      const r = await post(`/disk-sets/${set.id}/assign`, { items });
      DK.picked.clear(); DK.selSet = set.id; DK.tab = 3;
      toast(`${r.assigned} דיסקים שויכו ל${set.name} — נרשם ביומן`);
      await loadDisks();
    } });
}
function diskReplaceSheet(setId, suffix) {
  if (!isAdmin()) return;
  const set = DK.sets.find((s) => s.id === setId), old = (DK.members || []).find((d) => d.suffix === suffix);
  if (!set || !old) return;
  const cands = DK.disks.filter((d) => !d.set_id).sort((a, b) => Number(b.in_machine) - Number(a.in_machine));
  if (!cands.length) { toast("אין דיסק בלי סט שאפשר להחליף אליו"); return; }
  const opt = (d) => ({ value: d.serial, label: `${d.in_machine && d.machine ? `${d.machine}${d.port != null ? ` · מגירה ${d.port}` : ""} · ` : ""}${d.model || "דיסק"} · ${d.serial} · בלי סט` });
  sheet({ title: `החלפת ${old.label}`, sub: `הדיסק הישן (${old.serial}) יוצא מהסט ונשמר בהיסטוריה. השם ${old.label} עובר לדיסק החדש.`,
    fields: [{ id: "new", label: "הדיסק החדש", type: "select", value: cands[0].serial, options: cands.map(opt), help: "מוצעים רק דיסקים בלי סט. דיסק שכבר בסט אחר צריך לצאת ממנו קודם." }],
    submitLabel: "החלף", danger: old.state === "red",
    extra: { label: "הוצא מהסט בלי תחליף", onClick: () => confirmSheet(`להוציא את ${old.label} מהסט?`, `הדיסק ${old.serial} יוצא מהסט ו${old.label} מתפנה.`, "הוצא", async () => {
      await del(`/disk-sets/members/${encodeURIComponent(old.serial)}`);
      toast("הדיסק יצא מהסט — נרשם ביומן"); await loadDisks();
    }) },
    onSubmit: async (v) => {
      await post(`/disk-sets/${set.id}/replace`, { suffix, new_serial: v.new });
      toast(`${old.label} עבר לדיסק החדש — נרשם ביומן`);
      await loadDisks();
    } });
}
