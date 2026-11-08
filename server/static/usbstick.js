/* ImageSrv — עמוד "דיסק-און-קי" בתשתית (#1920, מוקאפ docs/design/mockups-1107b/1920-usb.html,
   אושר 07/11): ההתקן שחובר פיזית לשרת, יומן השיכפולים שעליו, וכפתור אחד "סנכרן" עם חלון סיכום
   ואישור אחד. הדף קורא בלבד; הכתיבה היחידה היא ה-POST של הסנכרון, עם ה-digest של הסיכום שהמנהל ראה.
   משתמש ב-UI/api/post/sheet/toast/esc/ltr/fmtDate/fmtClock/fmtBytes מ-console.js. */
"use strict";

let USB_STICK = null, usbStickError = "";

const USBS_STATE_HE = {
  none: "לא מחובר", ambiguous: "יותר מהתקן אחד", unsafe: "לא נגעתי בו", unread: "לא נבדק", busy: "בשימוש",
};
const USBS_RESULT = { ok: ["ok", "הצליח"], failed: ["err", "נכשל"], stopped: ["warn", "נעצר"] };
const USBS_CTRL_HE = { ahci: "AHCI", ide: "IDE", other: "אחר" };

async function loadUsbStick() {
  try { USB_STICK = await api("/usb-stick"); usbStickError = ""; }
  catch (e) { USB_STICK = null; usbStickError = e.message; }   // לא נקרא ≠ לא מחובר (עיקרון 5)
  try { await loadUsbClone(); } catch (e) { /* שורת ההורדה אומרת "לא נטען" בעצמה */ }
  if (current === "usbstick") renderCurrent();
}

const usbsDate = (ts) => (ts ? `${fmtDate(ts)} ${fmtClock(ts)}` : "—");

function usbsRateText(r) {
  if (r.copy_mbps == null && r.verify_mbps == null) return `<span class="muted">לא נמדד</span>`;
  const part = (label, v) => (v == null ? "" : `${label} ${ltr(v + " MB/s")}`);
  return [part("כתיבה", r.copy_mbps), part("בדיקה", r.verify_mbps)].filter(Boolean).join(" · ");
}

// #2694: the slowest SATA link of the clone, as the stick wrote it into the report; null = it could not read one
const usbsLink = (r) => (r.link_gbps == null ? `<span class="muted">לא נקרא</span>` : ltr(`${r.link_gbps} Gb/s`));

function usbsRow(r) {
  const [cls, label] = USBS_RESULT[r.result] || ["", r.result];
  const ctrl = r.controller ? `${USBS_CTRL_HE[r.controller] || esc(r.controller)}${r.driver ? " · " + esc(r.driver) : ""}` : `<span class="muted">לא נקרא</span>`;
  const inServer = r.in_server ? UI.status("ok", `נקלט ${fmtDate(r.imported_at)}`) : UI.status("warn", "חדש");
  return [`<span class="mono">${esc(usbsDate(r.recorded_at))}</span>`,
    r.model ? esc(r.model) : `<span class="muted">דגם לא ידוע</span>`, ctrl, usbsLink(r),
    r.bytes != null ? ltr(fmtBytes(r.bytes)) : `<span class="muted">—</span>`,
    usbsRateText(r), UI.status(cls, label), inServer];
}

function usbsDeviceCard(s) {
  const d = s.device, plan = s.plan;
  if (s.state !== "connected" || !d) {
    const why = USBS_STATE_HE[s.state] || "לא מחובר";
    const main = s.state === "none"
      ? `<b>לא זוהה דיסק-און-קי עם התווית IMGSRV_DATA.</b><small>מחברים אותו פיזית לשרת, בחדר השרתים. אין קליטה אוטומטית בחיבור: אחרי החיבור לוחצים "סנכרן".</small>`
      : `<b>${esc(s.reason || "לא ניתן להשתמש בהתקן")}</b>${(s.notes || []).map((n) => `<small>${esc(n)}</small>`).join("")}`;
    const retry = `<button class="btn" type="button" onclick="refreshPage()">בדיקה מחדש</button>`;
    const vm = `<div class="usbs-help"><b>השרת הוא VM?</b><small><b>Hyper-V:</b> דיסק pass-through לאורח. <b>ESXi:</b> USB passthrough מהמארח לאורח. במכונה פיזית ההתקן זמין ישירות. השרת מזהה רק התקן שמופיע לו על אפיק USB; דיסק שהמארח מציג לו כדיסק רגיל לא יזוהה, והעמוד יאמר זאת. ההתקן נקרא לקריאה בלבד.</small></div>`;
    return UI.card({ title: "ההתקן המחובר", acts: UI.pill("", why), body: `<div class="usbs-none"><div class="usbs-help">${main}</div>${retry}</div>${vm}` });
  }
  const pend = plan
    ? (plan.to_server.new ? `<b>${plan.to_server.new}</b> רשומות חדשות, ${plan.to_server.already} שכבר בשרת` : "אין רשומות שממתינות")
    : `<span class="muted">לא נקרא</span>`;
  const last = (s.rows[0] || {}).recorded_at;
  const sub = [d.size_bytes != null ? ltr(fmtBytes(d.size_bytes)) : "", d.fstype ? esc(d.fstype.toUpperCase()) : "",
    `מזהה ההתקן: <span class="mono">${esc(d.device_id)}</span>`].filter(Boolean).join(" · ");
  const head = `<div class="usbs-head"><span class="usbs-icon">${uiIcon("t-usb")}</span><div><b>${esc(d.label)}</b><div class="muted">${sub}</div></div></div>`;
  const kv = UI.kv([["עדכון אחרון על ההתקן", last ? `${esc(usbsDate(last))} <span class="sub">היומן נכתב עליו בשטח, בלי שרת</span>` : `<span class="muted">אין רשומות</span>`],
    ["ממתינות לקליטה בשרת", pend]]);
  return UI.card({ title: "ההתקן המחובר", small: "זוהה לפי התווית IMGSRV_DATA", acts: UI.pill("ok", "מחובר · לקריאה בלבד"),
    body: `${head}${kv}${s.read_error ? UI.note("err", `לא הצלחתי לקרוא את היומן מההתקן: ${esc(s.read_error)}`) : ""}` });
}

function usbsHistoryCard(s) {
  if (!s.rows.length) return "";
  const rows = s.rows.map(usbsRow);
  const table = UI.datagrid({ cls: "nowrap", columns: ["תאריך", "דגם המחשב", "בקר", "קישור", "גודל", "קצב לפי שלב", "תוצאה", "בשרת"], rows });
  return UI.card({ title: "היסטוריית שיכפולים על ההתקן", small: "מחשבים בלבד: בלי serial של דיסקים ובלי SMART", body: table, flush: true });
}

function usbsRejectedNote(s) {
  const rej = s.plan ? s.plan.to_server.rejected : [];
  if (!rej.length) return "";
  return `<div class="c12">${UI.note("warn", `<b>${rej.length} ${rej.length === 1 ? "רשומה בהתקן אינה תקינה" : "רשומות בהתקן אינן תקינות"}</b> ולא ייקלטו: `
    + rej.slice(0, 5).map((r) => `<span class="mono">${esc(r.name)}</span> — ${esc(r.reason)}`).join(" · ") + (rej.length > 5 ? ` ועוד ${rej.length - 5}` : ""))}</div>`;
}

function usbsTruncNote(s) {
  const t = s.plan ? s.plan.to_server : null;
  if (!t || !(t.beyond_cap || t.ignored)) return "";
  return `<div class="c12">${UI.note("info", [t.beyond_cap ? `${t.beyond_cap} דוחות ישנים מאוד לא נקראו (נקראו החדשים)` : "", t.ignored ? `${t.ignored} קבצים בתיקיית הדוחות אינם דוחות שיכפול ודולגו` : ""].filter(Boolean).join(" · "))}</div>`;
}

/* הסיכום של הסנכרון האחרון — כל כיוון לחוד; "הושלם" רק כשהכול עבר. */
function usbsSummaryLists(r) {
  const li = (cls, html) => `<li class="${cls}">${html}</li>`;
  const ts = r.to_server || {}, tk = r.to_stick || {};
  const a = [];
  if (ts.error) a.push(li("bad", esc(ts.error)));
  else a.push(li("okc", `<b>${ts.imported}</b> רשומות נקלטו`));
  if (ts.already) a.push(li("m", `${ts.already} כבר בשרת, דולגו`));
  for (const x of ts.rejected || []) a.push(li("bad", `<b>1</b> נדחתה: "${esc(x.reason)}" (<span class="mono">${esc(x.name)}</span>). היא לא נקלטה.`));
  const b = [];
  if (tk.error) b.push(li("bad", esc(tk.error)));
  else if (tk.skipped) b.push(li("m", "אין שינוי: לא נכתב כלום להתקן"));
  else {
    b.push(li("okc", `קצבים ל-${tk.models} דגמי מחשב נכתבו`));
    b.push(li("okc", `טבלת ידע SMART נכתבה (גרסה <span class="mono">${esc(tk.smart_version)}</span>)`));
    b.push(li("m", "כל קובץ נקרא בחזרה מההתקן והושווה למה שנכתב"));
  }
  return [a, b];
}

function usbsLastCard(s) {
  const r = s.last_sync;
  if (!r) return "";
  const pill = r.state === "ok" ? UI.pill("ok", "הושלם")
    : r.state === "partial" ? UI.pill("warn", usbsPartialText(r)) : UI.pill("err", "נכשל");
  const [a, b] = usbsSummaryLists(r);
  const body = `<div class="usbs-cols"><div><h5>מהדיסק-און-קי אל השרת</h5><ul class="usbs-ul">${a.join("")}</ul></div>`
    + `<div><h5>מהשרת אל הדיסק-און-קי</h5><ul class="usbs-ul">${b.join("")}</ul></div></div>`;
  return UI.card({ title: "סיכום הסנכרון האחרון", small: `${usbsDate(r.at)} · מנהל: ${r.by}`, acts: pill, body });
}
function usbsPartialText(r) {
  const n = ((r.to_server || {}).rejected || []).length;
  const bad = ["to_server", "to_stick"].filter((k) => (r[k] || {}).error).length;
  if (bad) return "הושלם חלקית: כיוון אחד נכשל";
  return n === 1 ? "הושלם עם דחייה אחת" : `הושלם עם ${n} דחיות`;
}

function usbStickPage() {
  if (!USB_STICK && !usbStickError) return pagePlaceholder();
  const s = USB_STICK;
  const connected = !!s && s.state === "connected" && !!s.plan;
  const dl = typeof USB_CLONE !== "undefined" && USB_CLONE && USB_CLONE.available;
  const sync = connected
    ? `<button class="btn primary" type="button" onclick="usbStickSync()">סנכרן</button>`
    : `<button class="btn" type="button" disabled title="${esc(s ? "אין התקן שאפשר לסנכרן" : "הדף לא נקרא")}">סנכרן</button>`;
  const download = `<button class="btn" type="button" onclick="usbCloneSheet()"${dl ? "" : " disabled"} title="${dl ? "" : esc((USB_CLONE && USB_CLONE.reason) || "לא זמין")}">הורדת קובץ ה-USB…</button>`;
  const header = UI.objHeader({
    crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "דיסק-און-קי" }],
    icon: "t-usb", name: "דיסק-און-קי", sub: "יומן הלמידה של דיסק-און-קי השיכפול, וסנכרון עם השרת · מנהל בלבד",
    actions: sync + download, compact: true });
  const body = !s
    ? UI.note("err", `לא הצלחתי לקרוא את מצב הדיסק-און-קי: ${esc(usbStickError)}`)
    : usbsDeviceCard(s) + usbsRejectedNote(s) + usbsTruncNote(s) + usbsHistoryCard(s) + usbsLastCard(s);
  return `<div class="page aligned usbs">${header}<div class="body">${body}</div></div>`;
}

/* "סנכרן": קוראים את ההתקן מחדש (לא סומכים על מה שצויר לפני דקה), מציגים מה יעבור, ומחכים לאישור. */
async function usbStickSync() {
  if (!isAdmin()) return;
  try { USB_STICK = await api("/usb-stick"); usbStickError = ""; } catch (e) { toast(e.message); return; }
  if (current === "usbstick") renderCurrent();
  const s = USB_STICK, plan = s.plan;
  if (s.state !== "connected" || !plan) { toast(s.reason || s.read_error || "אין התקן שאפשר לסנכרן"); return; }
  if (plan.nothing_to_do) { toast("אין מה לסנכרן: כל הרשומות כבר בשרת וההתקן מעודכן"); return; }
  sheet({
    title: "סנכרון עם הדיסק-און-קי", sub: "זה מה שיעבור בכל כיוון. לא ייכתב כלום עד שתאשרו.",
    submitLabel: "סנכרן", size: "mid", note: usbsPlanHtml(plan),
    onSubmit: async () => {
      const r = await post("/usb-stick/sync", { digest: plan.digest, confirm: true });
      toast(r.state === "ok" ? "הסנכרון הושלם" : r.state === "partial" ? "הסנכרון הושלם חלקית — ראו את הסיכום בעמוד" : "הסנכרון נכשל — ראו את הסיכום בעמוד", 4200);
      return loadUsbStick();
    },
  });
}

function usbsPlanHtml(plan) {
  const t = plan.to_server, k = plan.to_stick;
  const li = (cls, html) => `<li class="${cls}">${html}</li>`;
  const a = [];
  a.push(t.new ? li("", `<b>${t.new}</b> רשומות חדשות ייקלטו, וכל אחת תסומן במקור: USB, מזהה ההתקן, תאריך`) : li("m", "אין רשומות חדשות"));
  if (t.already) a.push(li("m", `${t.already} כבר בשרת (לפי המזהה של כל רשומה) ידולגו`));
  for (const x of t.rejected.slice(0, 5)) a.push(li("bad", `תידחה: "${esc(x.reason)}" (<span class="mono">${esc(x.name)}</span>)`));
  if (t.rejected.length > 5) a.push(li("bad", `ועוד ${t.rejected.length - 5} רשומות יידחו`));
  a.push(li("m", "מה שהשרת מדד בעצמו לא נדרס"));
  const b = [];
  if (k.nothing_to_write) b.push(li("m", "אין שינוי: לא ייכתב כלום להתקן"));
  else {
    b.push(li("", `קצבים ל-<b>${k.models}</b> דגמי מחשב`));
    b.push(li("", k.smart_changed
      ? `טבלת ידע SMART, גרסה <span class="mono">${esc(k.smart_version)}</span> (על ההתקן: ${k.smart_on_stick ? `<span class="mono">${esc(k.smart_on_stick)}</span>` : "אין"})`
      : `טבלת ידע SMART, גרסה <span class="mono">${esc(k.smart_version)}</span> (כבר על ההתקן)`));
    b.push(li("m", "הכתיבה רק לתיקייה server-sync של ImageSrv על ההתקן; שאר הקבצים לא נוגעים"));
  }
  b.push(li("m", "יומן שטח שלא נקלט עדיין בשרת לא נדרס"));
  return `<div class="usbs-cols"><div class="usbs-sum"><h5>מהדיסק-און-קי אל השרת</h5><ul class="usbs-ul">${a.join("")}</ul></div>`
    + `<div class="usbs-sum"><h5>מהשרת אל הדיסק-און-קי</h5><ul class="usbs-ul">${b.join("")}</ul></div></div>`
    + `<div class="usbs-info">נתון לא תקין נדחה בגלוי, עם הסיבה, ולא נקלט חלקית. מידע על דיסקים (serial, SMART לפי דיסק) לא עובר בשום כיוון.</div>`;
}
