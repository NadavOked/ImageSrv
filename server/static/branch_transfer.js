/* ImageSrv — #1781 (מוקאפ שנדב אישר ב-07/11, PR #2675): העברת אימג' לשרת משנה ומשיכתו ממנו,
   מתפריט ⋯ של האימג' בדף האימג'ים. חלון אחד עם מתג בין שני הכיוונים, בחירת שרת משנה
   ובחירת **מיקום אחסון בצד המקבל**. מיקום שהאימג' לא נכנס אליו מושבת, עם המספרים; כשאין
   מיקום שנכנס אליו — כפתור ההתחלה מושבת והסיבה כתובה.

   הצד של השרת קיים (‏POST /storage-nodes/{id}/transfer ו-/pull, שניהם עם location_id; PR #2572).
   כאן רק החלון. ה-"נכנס" הוא אותו חישוב של השרת, ממספרים שנמדדו: גודל האימג' + שולי הבדיקה
   (‏256MiB, ‏storage_transfer.PULL_FREE_SPACE_MARGIN / interserver.FREE_SPACE_MARGIN) + הרזרבה
   למערכת של **הצד המקבל** (‏/overview ← storage.reserve_bytes, #1737) מול הפנוי של המיקום
   (‏/storage-locations). מה שלא נקרא נאמר שלא נקרא, ולא "נכנס" (עיקרון 5); והשרת בודק שוב
   לפני הבייט הראשון, כך שהחלון מייעץ ואינו המגן היחיד.

   שם הצד השני במוקאפ שונה משמו בממשק: "שרת משנה" (נדב 18/10; test_no_branch_word_on_any_console_screen).
   משתמש ב-UI/api/post/sheet/toast/esc/encodeId/fmtBytes/ltr/impactList/findImage/IMAGES מ-console.js. */
"use strict";

const BT_MARGIN_BYTES = 256 * 1024 * 1024;
const BT_TEXT = {
  push: { title: "העברת אימג' לשרת משנה", loc: "מיקום אחסון בשרת המשנה", submit: "התחל העברה",
    sub: "האימג' ייכנס לספריית שרת המשנה רק אחרי ששרת המשנה אימת sha256 של כל מחיצה. ניתוק באמצע = כשל גלוי, ומתחילים מחדש." },
  pull: { title: "משיכת אימג' משרת משנה אל הראשי", loc: "מיקום אחסון בראשי", submit: "התחל משיכה",
    sub: "האימג' ייכנס לספרייה של הראשי רק אחרי שהראשי אימת sha256 של כל מחיצה. ניתוק באמצע = כשל גלוי, ומתחילים מחדש." },
};
let BT_GEN = 0;
let BT_WATCH = null;

function branchTransferMenuItems(idEnc) {
  if (!ME || !ME.capabilities || !ME.capabilities.interbranch_transfer) return [];
  return [["העבר לשרת משנה…", `openBranchTransfer('push','${idEnc}')`],
          ["משוך משרת משנה…", `openBranchTransfer('pull','${idEnc}')`]];
}

/* ---------- חישוב ה"נכנס" — טהור, ממספרים שנמדדו ---------- */

/* ok: true = נכנס · false = לא נכנס / המיקום לא זמין (reason) · null = לא ניתן לחשב (reason) */
function btFit(size, reserve, loc) {
  if (!loc) return { ok: false, reason: "המיקום לא נמצא" };
  if (loc.state !== "connected") return { ok: false, reason: `לא זמין: ${loc.state_label || loc.state || "לא ידוע"}` };
  const sz = Number(size), keep = Number(reserve);
  if (!(sz > 0)) return { ok: null, reason: "גודל האימג' לא דווח" };
  if (reserve == null || !Number.isFinite(keep) || keep < 0) return { ok: null, reason: "הרזרבה למערכת לא נקראה" };
  if (loc.free_bytes == null || !Number.isFinite(Number(loc.free_bytes))) return { ok: null, reason: "המקום הפנוי לא נמדד" };
  const need = sz + BT_MARGIN_BYTES + keep, free = Number(loc.free_bytes);
  return free >= need ? { ok: true, need, free } : { ok: false, need, free, missing: need - free, reason: "לא נכנס" };
}

function btLocationLabel(loc, fit) {
  const tail = fit.ok === true ? `פנויים ${fmtBytes(fit.free)}`
    : fit.missing != null ? `לא נכנס: חסרים ${fmtBytes(fit.missing)}`
    : fit.reason;
  return `${loc.name} (${loc.id}) — ${tail}`;
}

/* ברירת מחדל: loc_local אם נכנס, אחרת הראשון שנכנס; אין כזה — loc_local (מושבת, והסיבה בהודעה). */
function btDefaultLocation(options) {
  const usable = (o) => !o.disabled;
  const local = options.find((o) => o.value === "loc_local");
  if (local && usable(local)) return local.value;
  const first = options.find(usable);
  return first ? first.value : (local || options[0] || { value: "" }).value;
}

/* ---------- מודל התצוגה: מה על המסך לפי מה שנבחר ---------- */

/* impactList עם סמל "אזהרה" גם לשגיאה: סמל ה-"b" שלה הוא פח (מחיקה), ופה שום דבר לא נמחק. */
function btNoteHtml(kind, text) {
  const cls = kind === "b" ? "bad" : kind === "ok" ? "imp-ok" : "imp-w";
  return `<ul class="n-impact"><li class="${cls}">${kind === "b" ? IMPACT_ICONS.w : IMPACT_ICONS[kind]}<span>${text}</span></li></ul>`;
}

/* "ב" + שם: עברית בלי מקף ("בחיפה"), לטינית עם ("ב-NAS מכללה"). */
const btIn = (name) => (/^[֐-׿]/.test(String(name)) ? `ב${name}` : `ב-${name}`);

function btView(st, v) {
  const push = v.dir !== "pull";
  const text = BT_TEXT[push ? "push" : "pull"];
  const node = st.nodes.find((n) => n.id === v.nid) || st.nodes[0];
  const rem = st.remote[node.id] || { done: false };
  const where = push ? rem : st.main;
  const view = { dir: push ? "push" : "pull", title: text.title, sub: text.sub, locLabel: text.loc, submitLabel: text.submit,
    nodeId: node.id, showRemoteImage: !push, remoteOptions: [], locOptions: [], locValue: "", imageId: push ? st.img.id : v.rimg,
    fit: null, canSubmit: false, noteHtml: "" };
  const fail = (html) => { view.noteHtml = btNoteHtml("b", html); return view; };
  if (!where.done || (!push && !rem.done)) { view.noteHtml = btNoteHtml("w", "טוען מיקומי אחסון ומקום פנוי…"); return view; }
  const side = push ? `בשרת המשנה ${esc(node.label)}` : "בראשי";

  let size = null, imageName = st.img.name;
  if (push) {
    size = st.img.total_compressed_bytes;
    if (rem.images && rem.images.some((m) => m.id === st.img.id))
      return fail(`<b>האימג' כבר קיים ${esc(btIn(node.label))}.</b> אין מה להעביר.`);
  } else {
    if (!rem.images) return fail(`<b>רשימת האימג'ים של ${esc(node.label)} לא נקראה:</b> ${esc(rem.err.images || "בלי סיבה")}.`);
    const have = new Set((IMAGES || []).map((m) => m.id));
    view.remoteOptions = rem.images.map((m) => ({ value: m.id, disabled: have.has(m.id),
      label: `${m.name} · ${fmtBytes(m.size_bytes)}${have.has(m.id) ? " — קיים בראשי" : ""}` }));
    const pickable = view.remoteOptions.filter((o) => !o.disabled);
    if (!pickable.length) return fail(`<b>אין ${esc(btIn(node.label))} אימג' למשוך.</b> כל מה שיש שם כבר קיים בראשי.`);
    const chosen = pickable.find((o) => o.value === v.rimg) || pickable.find((o) => o.value === st.img.id) || pickable[0];
    view.imageId = chosen.value;
    const picked = rem.images.find((m) => m.id === chosen.value);
    size = picked.size_bytes; imageName = picked.name;
  }
  if (!where.locations) return fail(`<b>מיקומי האחסון ${side} לא נקראו:</b> ${esc(where.err.locations || "בלי סיבה")}.`);
  const options = where.locations.map((loc) => {
    const fit = btFit(size, where.reserve, loc);
    return { value: loc.id, label: btLocationLabel(loc, fit), disabled: fit.ok === false, fit, loc };
  });
  if (!options.length) return fail(`<b>אין מיקום אחסון ${side}.</b>`);
  view.locOptions = options;
  view.locValue = options.some((o) => o.value === v.loc) ? v.loc : btDefaultLocation(options);
  const chosenLoc = options.find((o) => o.value === view.locValue);
  const fit = chosenLoc.fit;
  view.fit = fit;
  const num = (n) => ltr(fmtBytes(n));
  if (fit.ok === true) {
    view.canSubmit = true;
    view.noteHtml = btNoteHtml("ok", `<b>נכנס.</b> נדרשים ${num(fit.need)} (האימג' ${num(size)}, כולל רזרבה למערכת). פנויים ${esc(btIn(chosenLoc.loc.name))}: ${num(fit.free)}.<br>אזור הביניים, בדיקת sha256 והכניסה לספרייה ירוצו כולם על ${esc(chosenLoc.loc.name)}.`);
  } else if (fit.ok === false && fit.missing != null) {
    view.noteHtml = btNoteHtml("b", `<b>לא נכנס.</b> נדרשים ${num(fit.need)} (האימג' ${num(size)}, כולל רזרבה למערכת). פנויים ${esc(btIn(chosenLoc.loc.name))}: ${num(fit.free)}. חסרים ${num(fit.missing)}.<br>בחרו מיקום אחר, או פנו מקום. ${push ? "ההעברה" : "המשיכה"} לא תתחיל.`);
  } else if (fit.ok === false) {
    view.noteHtml = btNoteHtml("b", `<b>${esc(chosenLoc.loc.name)} — ${esc(fit.reason)}.</b> בחרו מיקום אחר. ${push ? "ההעברה" : "המשיכה"} לא תתחיל.`);
  } else {
    // לא ניתן לחשב — נאמר בשם. ההתחלה מותרת: השרת בודק מקום לפני הבייט הראשון, ושגיאתו גלויה.
    view.canSubmit = true;
    view.noteHtml = btNoteHtml("w", `<b>לא ניתן לבדוק אם ${esc(imageName)} נכנס:</b> ${esc(fit.reason)}. השרת יבדוק שוב לפני הבייט הראשון ויכשיל בגלוי אם אין מקום.`);
  }
  return view;
}

/* ---------- טעינה: מיקומים, רזרבה ורשימת אימג'ים — כל אחד נקרא לעצמו ---------- */

async function btRead(path, pick) {
  try { return { v: pick(await api(path)), e: "" }; } catch (e) { return { v: null, e: e.message }; }
}

function btLoadSide(prefix, withImages, nid) {
  const rec = { done: false, locations: null, reserve: null, images: null, err: {} };
  const jobs = [
    btRead(`${prefix}/storage-locations`, (r) => (Array.isArray(r && r.locations) ? r.locations : null)),
    btRead(`${prefix}/overview`, (r) => (r && r.storage && r.storage.reserve_bytes != null ? Number(r.storage.reserve_bytes) : null)),
  ];
  if (withImages) jobs.push(btRead(`/storage-nodes/${encodeId(nid)}/images`, (r) => {
    if (!r || !r.connected) throw new Error((r && r.error) || "שרת המשנה לא ענה");
    return Array.isArray(r.images) ? r.images : null;
  }));
  return Promise.all(jobs).then(([locations, reserve, images]) => {
    rec.locations = locations.v; rec.err.locations = locations.e || (locations.v ? "" : "תשובה בלי מיקומים");
    rec.reserve = reserve.v; rec.err.reserve = reserve.e || (reserve.v != null ? "" : "השרת לא החזיר reserve_bytes");
    if (images) { rec.images = images.v; rec.err.images = images.e || (images.v ? "" : "תשובה בלי אימג'ים"); }
    rec.done = true;
    return rec;
  });
}

/* ---------- החלון ---------- */

function btValues() {
  const val = (id) => { const el = document.getElementById(id); return el ? el.value : ""; };
  return { dir: val("sf-dir"), nid: val("sf-node"), rimg: val("sf-rimg"), loc: val("sf-loc") };
}

function btFill(id, options, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = options.map((o) => `<option value="${esc(o.value)}"${o.disabled ? " disabled" : ""}>${esc(o.label)}</option>`).join("");
  el.value = value;
}

function btPaint(view) {
  const set = (sel, text) => { const el = document.querySelector(sel); if (el) el.textContent = text; };
  set("#sheetTitle", view.title);
  set("#sheet .shead p", view.sub);
  set('#sheet label[for="sf-loc"]', view.locLabel);
  const remote = document.getElementById("sf-rimg"), local = document.getElementById("sf-limg");
  if (remote && remote.closest(".n-f")) remote.closest(".n-f").hidden = !view.showRemoteImage;
  if (local && local.closest(".n-f")) local.closest(".n-f").hidden = view.showRemoteImage;
  if (view.showRemoteImage) btFill("sf-rimg", view.remoteOptions, view.imageId);
  btFill("sf-loc", view.locOptions, view.locValue);
  const note = document.getElementById("bt-note");
  if (note) note.innerHTML = view.noteHtml;
  const submit = document.getElementById("sf-submit");
  if (submit) { submit.disabled = !view.canSubmit; submit.textContent = view.submitLabel; }
}

async function openBranchTransfer(dir, idEnc) {
  if (!isAdmin()) return;
  let id = idEnc; try { id = decodeURIComponent(id); } catch (e) {}
  const img = findImage(id);
  if (!img) return;
  let nodes;
  try { nodes = (await api("/storage-nodes")).filter((n) => !n.disabled_at); }
  catch (e) { toast(`רשימת שרתי המשנה לא נקראה: ${e.message}`); return; }
  if (!nodes.length) { toast("אין שרת משנה פעיל — רושמים אחד בדף שרתי המשנה"); return; }
  const gen = ++BT_GEN;
  const st = { img, nodes, main: { done: false }, remote: {} };
  const live = () => gen === BT_GEN && document.getElementById("bt-note");
  let last = null;
  const repaint = () => {
    if (!live()) return;
    const v = btValues();
    if (last && (last.dir !== v.dir || last.nid !== v.nid)) v.loc = "";   // צד אחר = מיקומים אחרים
    const view = btView(st, v);
    last = { dir: view.dir, nid: view.nodeId };
    btPaint(view);
  };
  const ensure = (nid) => {
    if (st.remote[nid]) return;
    st.remote[nid] = { done: false };
    btLoadSide(`/storage-nodes/${encodeId(nid)}/console`, true, nid).then((rec) => { st.remote[nid] = rec; repaint(); });
  };
  btLoadSide("", false).then((rec) => { st.main = rec; repaint(); });
  ensure(nodes[0].id);
  const dirs = [{ value: "push", label: "העבר לשרת משנה" }, { value: "pull", label: "משוך משרת משנה" }];
  sheet({
    title: BT_TEXT[dir === "pull" ? "pull" : "push"].title, sub: BT_TEXT[dir === "pull" ? "pull" : "push"].sub, size: "mid",
    fields: [
      { id: "dir", label: "כיוון", type: "segment", value: dir === "pull" ? "pull" : "push", options: dirs },
      { id: "node", label: "שרת משנה", type: "select", value: nodes[0].id, options: nodes.map((n) => ({ value: n.id, label: n.label })) },
      { id: "limg", label: "אימג'", type: "select", value: img.id, options: [{ value: img.id, label: `${img.name} · ${fmtBytes(img.total_compressed_bytes)}` }] },
      { id: "rimg", label: "אימג' בשרת המשנה", type: "select", value: "", options: [] },
      { id: "loc", label: BT_TEXT.push.loc, type: "select", value: "", options: [] },
      { id: "fit", type: "html", html: `<div id="bt-note" role="status" aria-live="polite"></div>` },
    ],
    submitLabel: BT_TEXT[dir === "pull" ? "pull" : "push"].submit,
    onSubmit: async () => {
      const view = btView(st, btValues());   // מה שעל המסך ברגע הלחיצה, לא מה שצויר לפני כן
      if (!view || !view.canSubmit) throw new Error(view && view.fit && view.fit.ok === false ? "האימג' לא נכנס למיקום שנבחר" : "הנתונים עדיין נטענים או לא נקראו");
      const body = { image_id: view.imageId, location_id: view.locValue };
      await post(`/storage-nodes/${encodeId(view.nodeId)}/${view.dir === "pull" ? "pull" : "transfer"}`, body);
      toast(view.dir === "pull" ? "המשיכה התחילה — היא מופיעה במשימות" : "ההעברה התחילה — היא מופיעה במשימות");
      await refreshTransfers().catch(() => {});   // ההעברה התחילה; רענון המשימות שנכשל לא הופך אותה לכישלון
    },
  });
  const form = document.getElementById("sheet");
  const group = form && form.querySelector ? form.querySelector('[data-segment="dir"]') : null;
  if (group) group.addEventListener("click", () => { ensure(btValues().nid); repaint(); });
  for (const sel of ["sf-node", "sf-rimg", "sf-loc"]) {
    const el = document.getElementById(sel);
    if (el) el.addEventListener("change", () => { ensure(btValues().nid); repaint(); });
  }
  const limg = document.getElementById("sf-limg");
  if (limg) limg.disabled = true;
  repaint();
}

/* ---------- אחרי ההתחלה: ההעברה במשימות ---------- */

function btActive(rows) { return (rows || []).some((t) => ["queued", "sending", "verifying"].includes(t.state)); }

async function refreshTransfers() {
  const rows = await api("/storage-transfers");
  BRANCH_TRANSFERS = rows;
  if (typeof HOME !== "undefined") { HOME.transfers = rows; delete HOME.err.transfers; }
  if (typeof renderActivity === "function") renderActivity();
  if (current === "home" || current === "branches") renderCurrent();
  clearInterval(BT_WATCH); BT_WATCH = null;
  if (btActive(rows)) BT_WATCH = setInterval(() => {
    if (document.hidden) return;
    api("/storage-transfers").then((now) => {
      BRANCH_TRANSFERS = now;
      if (typeof HOME !== "undefined") HOME.transfers = now;
      if (typeof renderActivity === "function") renderActivity();
      if (current === "home") renderCurrent();
      if (!btActive(now)) { clearInterval(BT_WATCH); BT_WATCH = null; }
    }).catch(() => {});
  }, 2000);
}
