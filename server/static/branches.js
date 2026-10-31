/* ImageSrv — #954 גל 7: דף "שרתי משנה" (רשימת שרתי המשנה) ודף "שרת משנה" (הצומת
   בעץ, #936). לפי docs/design/console-redesign/branches.md.

   "שרתי משנה" הוא דף "own" (UI.objHeader/datagrid כמו drivers.js): כותרת עם
   מונים ותג "N לא מגיב/ים" · לשוניות שרתים/העברות/קבוצות. חיבור כל שרת
   **נמדד** בפועל מול ה-machines endpoint של שרת המשנה, ולא נשאר "בודק
   חיבור…" קפוא (עיקרון 5) — אחרי שהטבלה על המסך, כל שרת נבדק במקביל
   ותא ה"חיבור" שלו מתעדכן במקום.

   "שרת משנה" (branch, BRANCH_NODE) נשאר בדפוס הישן (לא "own" — הלשוניות
   שלו טוענות נתונים שונים כל אחת) עם כותרת אובייקט/KPI/note שנוספו כאן;
   ‏loadBranchMachines/renderBranchTransfers/openTransferSheet משותפים
   לשני הדפים. קבוצות שרתי המשנה (יצירה/שם/סדר/מחיקה) נשארות ב-storage_nodes.js
   ומוצגות כלשונית "קבוצות" כאן. משתמש ב-UI/api/post/put/del/sheet/toast/
   esc/encodeId/fmtDate/fmtClock/formatGB/confirmSheet מ-console.js. */
"use strict";

let BRANCH_TIMER = null;          // פולינג העברות בדף שרת משנה יחיד (branch)
let BRANCHES_LIST_TIMER = null;   // פולינג לשונית "העברות" בדף שרתי המשנה
let BRANCH_LIVE_TIMER = null;     // מצב חדר/קליטה חי בכרטיסי שרתי המשנה
let BRANCH_TRANSFERS = [];
let BRANCH_GROUPS = [];
let BRANCH_NODES = [];
let BRANCH_READY = false, BRANCH_ERR = "";   // לשונית "שרתי משנה" בהגדרות: נטען / נכשל (לא נקרא ≠ אין שרתי משנה)
let BRANCH_CONN = {};   // id → {state:"checking"|"ok"|"err"|"disabled", error, machines, lastSeenAt, lastErrorAt}
// ‏#1017: הזמנים הם של **השרת** (last_seen_at / last_error_at על storage_nodes,
// נכתבים בכל קריאה בין-שרתית) — לא שעון הדפדפן ברגע שהדף קרא. בלי שדה מהשרת
// אין זמן, לא ניחוש.

const TRANSFER_STATE = {
  queued: ["ממתין", ""], sending: ["שולח", ""], verifying: ["שרת המשנה מאמת sha256", ""],
  done: ["הושלם", "ok"], failed: ["נכשל", "err"],
};

/* ‏#1462: קצב, ETA וזמן שעבר — רק ממה שנמדד. הקצב הוא הפרש בין שתי דגימות
   (bytes_sent, updated_at) שהדף ראה, **שתיהן בשעון השרת**, בחלון של עד דקה;
   פחות משתי דגימות או פחות משתי שניות ביניהן = "מודד קצב…", לא 0. הזמן שעבר
   בשלב האימות הוא seconds_since_update מהשרת (null = לא מוצג), לא שעון הדפדפן. */
const TRANSFER_SAMPLES = new Map();   // id → [{t: ms (updated_at), b: bytes_sent}]
const RATE_WINDOW_MS = 60000;
const RATE_MIN_SPAN_MS = 2000;        // updated_at ברזולוציית שניות
const STALL_AFTER_S = 30;             // בלי עדכון מנה (64MiB) זמן כזה — אומרים את זה, לא ETA

function transferRate(t) {
  if (t.state !== "sending") { TRANSFER_SAMPLES.delete(t.id); return null; }
  const at = Date.parse(t.updated_at || "");
  const bytes = Number(t.bytes_sent);
  const list = TRANSFER_SAMPLES.get(t.id) || [];
  if (Number.isFinite(at) && Number.isFinite(bytes)) {
    const last = list[list.length - 1];
    if (!last || at > last.t) list.push({ t: at, b: bytes });
    while (list.length > 2 && at - list[1].t >= RATE_WINDOW_MS) list.shift();
    TRANSFER_SAMPLES.set(t.id, list);
  }
  if (list.length < 2) return null;
  const a = list[0], z = list[list.length - 1];
  if (z.t - a.t < RATE_MIN_SPAN_MS || z.b < a.b) return null;
  return (z.b - a.b) * 1000 / (z.t - a.t);
}

function transferPace(t) {
  const since = t.seconds_since_update;
  if (t.state === "verifying") {
    const spent = fmtSpan(since);
    return spent ? `עברו ${spent}` : "";
  }
  if (t.state !== "sending") return "";
  const rate = transferRate(t);
  if (since != null && since >= STALL_AFTER_S) return `אין התקדמות ${fmtSpan(since)}`;
  if (rate == null) return "מודד קצב…";
  const speed = `${(rate / 1e6).toFixed(1)} MB/s`;
  const left = Number(t.bytes_total) - Number(t.bytes_sent);
  const eta = rate > 0 && left >= 0 ? fmtSpan(left / rate) : null;
  return eta ? `${speed} · נותרו כ-${eta}` : speed;
}

function transferStateLabel(t) {
  if (t.state === "verifying" && t.direction === "pull") return ["הראשי מאמת sha256", ""];
  return TRANSFER_STATE[t.state] || [t.state, ""];
}

function stopBranchPolling() { clearInterval(BRANCH_TIMER); BRANCH_TIMER = null; }
function stopBranchesListPolling() { clearInterval(BRANCHES_LIST_TIMER); BRANCHES_LIST_TIMER = null; }
function stopBranchLivePolling() { clearInterval(BRANCH_LIVE_TIMER); BRANCH_LIVE_TIMER = null; }

/* ---------- דף "שרתי משנה" (רשימת שרתי המשנה, own) ---------- */

async function loadBranchesData() {
  stopBranchesListPolling();
  stopBranchLivePolling();
  const [groups, nodes, transfers] = await Promise.all([
    api("/storage-node-groups"), api("/storage-nodes"), api("/storage-transfers"),
  ]);
  BRANCH_GROUPS = groups;
  BRANCH_NODES = nodes;
  BRANCH_TRANSFERS = transfers;
  BRANCH_READY = true; BRANCH_ERR = "";
  await loadTransferCap();
  for (const n of nodes) {
    // עוד לפני הבדיקה — מה שהשרת זוכר: "ענתה לאחרונה HH:MM" גם כשהעמוד לא היה בפוקוס.
    const remembered = { lastSeenAt: n.last_seen_at || null, lastErrorAt: n.last_error_at || null, error: n.last_error || "" };
    BRANCH_CONN[n.id] = n.disabled_at ? { state: "disabled", ...remembered } : { state: "checking", ...remembered };
  }
  if (current === "branches" || current === "settings") renderCurrent();
  await Promise.all(nodes.filter((n) => !n.disabled_at).map((n) =>
    checkBranchConnection(n).catch(() => {})));
  maybeStartBranchesListPolling();
  maybeStartBranchLivePolling();
}

/* ‏probe=true (#1017, "בדוק חיבור" מהשורה): POST …/check — /ping בלבד, בלי מכונות;
   אחרת GET …/machines (טעינת הדף: גם מונה המחשבים). שניהם מחזירים את שדות המגע. */
async function checkBranchConnection(n, probe = false) {
  let result;
  const prev = BRANCH_CONN[n.id] || {};
  try {
    const answer = probe ? await post(`/storage-nodes/${encodeId(n.id)}/check`, {})
      : await api(`/storage-nodes/${encodeId(n.id)}/machines`);
    const contact = { lastSeenAt: answer.last_seen_at || null, lastErrorAt: answer.last_error_at || null };
    // ‏/check אינו שולף מכונות — המונה מהקריאה האחרונה ל-/machines נשאר (הוא נמדד, לא הומצא).
    const machines = probe ? prev.machines : (answer.connected ? answer.machines : prev.machines);
    let room = prev.room, images = prev.images, liveError = "";
    if (!probe && answer.connected) {
      const prefix = `/storage-nodes/${encodeId(n.id)}/console`;
      const live = await Promise.allSettled([api(`${prefix}/room`), api(`${prefix}/images`)]);
      if (live[0].status === "fulfilled") room = live[0].value;
      else liveError = live[0].reason.message;
      if (live[1].status === "fulfilled") images = live[1].value;
      else liveError = liveError || live[1].reason.message;
    }
    result = answer.connected
      ? { state: "ok", machines, room, images, liveError, ...contact }
      : { state: "err", error: answer.error || "", machines, ...contact };
  } catch (e) {
    // הבקשה לראשי עצמו נכשלה — לא נרשם בשרת, ולכן בלי זמן (לא שעון הדפדפן).
    result = { state: "err", error: e.message, machines: prev.machines, lastSeenAt: prev.lastSeenAt || null, lastErrorAt: null };
  }
  BRANCH_CONN[n.id] = result;
  markSecondaryStatus(n, result.state === "ok", result.error);
  const connCell = $(`#branch-row-conn-${CSS.escape(n.id)}`);
  const machinesCell = $(`#branch-row-machines-${CSS.escape(n.id)}`);
  if (connCell) { const st = branchStatusHtml(n.id); connCell.className = st.cls; connCell.innerHTML = st.html; }
  if (machinesCell) machinesCell.textContent = Array.isArray(result.machines) ? String(result.machines.length) : "—";
  if (current === "branches" && currentTab === 0) updateAlertBadge();
  if (current === "settings" && currentTab === 1) renderCurrent();   // הלשונית "שרתי משנה" מציירת את הסטטוס כ-pill
}

async function refreshBranchLive(n) {
  const prev = BRANCH_CONN[n.id] || {};
  if (prev.state !== "ok") return;
  const prefix = `/storage-nodes/${encodeId(n.id)}/console`;
  const live = await Promise.allSettled([api(`${prefix}/room`), api(`${prefix}/images`)]);
  BRANCH_CONN[n.id] = { ...prev,
    room: live[0].status === "fulfilled" ? live[0].value : prev.room,
    images: live[1].status === "fulfilled" ? live[1].value : prev.images,
    liveError: live.find((x) => x.status === "rejected")?.reason?.message || "" };
}

function maybeStartBranchLivePolling() {
  if (current !== "branches" || currentTab !== 0 || !BRANCH_NODES.some((n) => !n.disabled_at)) return;
  stopBranchLivePolling();
  BRANCH_LIVE_TIMER = setInterval(async () => {
    if (current !== "branches" || currentTab !== 0 || document.hidden) { stopBranchLivePolling(); return; }
    await Promise.all(BRANCH_NODES.filter((n) => !n.disabled_at).map((n) => refreshBranchLive(n)));
    renderCurrent();
  }, 2000);
}

/* "בודק חיבור…" רק לפני המדידה הראשונה; אחריה — "מחובר · ענתה HH:MM" או
   "לא ענה — HH:MM" + הסיבה. הזמנים מהשרת (#1017): last_seen_at / last_error_at;
   בלי שדה — בלי זמן. לא "בודק…" שלעולם לא מתעדכן, ולא שעון הדפדפן. */
function branchWhen(ts) { return ts ? (isToday(ts) ? fmtClock(ts) : `${fmtDate(ts)} ${fmtClock(ts)}`) : ""; }
function branchStatusHtml(nid) {
  const c = BRANCH_CONN[nid] || { state: "checking" };
  const seen = branchWhen(c.lastSeenAt);
  if (c.state === "disabled") return { cls: "status", html: `<i></i>מושבת${seen ? `<span class="sub">ענתה לאחרונה ${esc(seen)}</span>` : ""}` };
  if (c.state === "checking") return { cls: "status", html: `<i></i>בודק חיבור…${seen ? `<span class="sub">ענתה לאחרונה ${esc(seen)}</span>` : ""}` };
  if (c.state === "ok") return { cls: "status ok", html: `<i></i>מחובר${seen ? ` · ענתה ${esc(seen)}` : ""}` };
  const failed = branchWhen(c.lastErrorAt);
  return { cls: "status err", html: `<i></i>לא ענה${failed ? " — " + esc(failed) : ""}${c.error ? `<span class="sub">${esc(c.error)}</span>` : ""}${seen ? `<span class="sub">ענתה לאחרונה ${esc(seen)}</span>` : ""}` };
}

/* ‏#1017: "בדוק חיבור" מהשורה — /check בלבד; התא חוזר ל"בודק…" בזמן הבדיקה. */
async function checkBranchNow(nid) {
  try { nid = decodeURIComponent(nid); } catch (e) {}
  const n = BRANCH_NODES.find((x) => x.id === nid);
  if (!n) { toast("שרת משנה לא נמצא"); return; }
  if (n.disabled_at) { toast("השרת מושבת — לא נשאל"); return; }
  BRANCH_CONN[n.id] = { ...(BRANCH_CONN[n.id] || {}), state: "checking" };
  const connCell = $(`#branch-row-conn-${CSS.escape(n.id)}`);
  if (connCell) { const st = branchStatusHtml(n.id); connCell.className = st.cls; connCell.innerHTML = st.html; }
  await checkBranchConnection(n, true);
}

function branchNodeRow(n) {
  const disabled = !!n.disabled_at;
  const st = branchStatusHtml(n.id);
  const c = BRANCH_CONN[n.id] || {};
  const machinesCell = Array.isArray(c.machines) ? String(c.machines.length) : "—";   // המונה כפי שנמדד לאחרונה; "—" = לא נקרא
  const done = BRANCH_TRANSFERS.filter((t) => t.node_id === n.id && t.state === "done");
  const seen = new Set();
  const transferred = done.filter((t) => !seen.has(t.image_id) && seen.add(t.image_id)).length;
  const last = BRANCH_TRANSFERS.find((t) => t.node_id === n.id);   // כבר ממוין חדש→ישן
  const lastCell = last
    ? `${esc(last.image_name)}<span class="sub mono" dir="ltr">${esc(fmtDate(last.created_at))} ${esc(fmtClock(last.created_at))}</span>`
    : `<span class="muted">—</span>`;
  const idEnc = encodeId(n.id);
  const acts = UI.acts([
    ["פתח", `openSecondaryPage('${idEnc}','home',null)`],
    ["בדוק חיבור", `checkBranchNow('${idEnc}')`],
    ["העבר אימג'", `openTransferSheetForNode('${idEnc}')`],
    ["עריכה", `editBranchNode('${idEnc}')`],
    [disabled ? "הפעל" : "השבת", `toggleBranchNode('${idEnc}')`],
    ["הסר", `removeBranchNode('${idEnc}')`],
  ]);
  return { attrs: `data-node-row="${esc(n.id)}"${disabled ? ' class="row-off"' : ""}`, cells: [
    UI.nameHtml(n.label, `<span class="mono muted" dir="ltr">${esc(n.node_id || "—")}</span> · נרשם ${esc(fmtDate(n.enrolled_at))}`),
    `<span class="mono" dir="ltr">${esc(n.base_url)}</span>`,
    esc(n.group_label || "ללא קבוצה"),
    `<span class="${st.cls}" id="branch-row-conn-${esc(n.id)}">${st.html}</span>`,
    `<span id="branch-row-machines-${esc(n.id)}">${machinesCell}</span>`,
    String(transferred),
    lastCell,
    acts,
  ] };
}

function branchesServersTab() {
  const table = UI.datagrid({
    cls: "acts-on",
    columns: ["שרת", "כתובת", "קבוצה", "חיבור", "מחשבים", "אימג'ים שהועברו", "העברה אחרונה", ""],
    rows: BRANCH_NODES.map(branchNodeRow),
    empty: "אין שרתי משנה רשומים.",
  });
  return `<div class="c12 card"><div class="card-b${BRANCH_NODES.length ? " flush" : ""}">${table}</div></div>
    ${BRANCH_NODES.map(branchControlCard).join("")}`;
}

function branchMachineName(m) { return m.name || m.suffix || m.hostname || m.mac; }
function branchMachineRole(m) {
  const role = m.role || m.kind || "";
  return role === "cloner" || m.group_id === "grp_CLONERS" ? "cloner" : role === "build" ? "build" : role;
}
function branchSmartClass(d) {
  if (d.state === "failed" || d.smart === "failed_last") return "err";
  if (d.smart === "warn" || d.smart === "fail") return "warn";
  if (d.state === "writing" || d.state === "verifying") return "run";
  if (d.smart === "ok" || d.state === "done") return "ok";
  return "";
}
function branchDrawerHtml(d, i) {
  const label = d.slot_name || `דיסק ${d.slot || d.port || i + 1}`;
  const state = d.state ? (DRAWER_STATE_HE[d.state] || d.state) : smartText(d);
  return `<span class="disk ${branchSmartClass(d)}" title="${esc(state)}"><b>${esc(label)}</b><span class="cap">${esc(state)}</span></span>`;
}
function branchCaptureText(m) {
  const c = m.last_capture || m.capture;
  if (!c) return "אין קליטה פעילה";
  const state = c.state === "running" ? "קליטה פעילה" : c.state === "failed" ? "קליטה נכשלה" : c.state === "done" ? "קליטה הושלמה" : c.state;
  return `${state}${c.image_name ? ` · ${c.image_name}` : ""}`;
}
function branchMachineHtml(nid, m, roomMachine) {
  const role = branchMachineRole(m), live = roomMachine || {};
  const disks = Array.isArray(live.drawer_list) ? live.drawer_list : (Array.isArray(m.disks) ? m.disks : []);
  const roleHe = role === "cloner" ? "משכפל" : role === "build" ? "מחשב בנייה" : "מחשב";
  const status = live.awake === true || m.online === true || m.awake === true ? "מחובר" : "לא מחובר";
  const drawers = role === "cloner" ? `<div class="slots">${disks.length ? disks.map(branchDrawerHtml).join("") : `<span class="muted">לא דווחו מגירות</span>`}</div>` : "";
  const capture = role === "build" ? `<div class="sub">${esc(branchCaptureText(m))}</div>` : "";
  return `<div class="mcard" data-branch-machine="${esc(m.mac)}"><div class="mcard-h"><b>${esc(branchMachineName(m))}</b><span class="muted">${roleHe} · ${status}</span></div>${drawers}${capture}<div class="acts"><button class="btn sm" onclick="branchMonitor('${encodeId(nid)}','${encodeId(m.mac)}','${encodeId(branchMachineName(m))}')">מוניטור</button></div></div>`;
}
function branchRoundHtml(n, c) {
  const r = c.room && c.room.round;
  if (!r) return `<span class="muted">אין סבב פעיל</span>`;
  const state = r.state === "failed" ? "נכשל" : r.wave_state === "active" ? "משדר" : r.wave_state === "open" ? "ממתין להתחלה" : (r.state || "פעיל");
  return `<b>${esc(r.image_name || r.image_id || "סבב")}</b> · ${esc(state)} · נכתבו ${Number(r.written_drives || 0)} מתוך ${Number(r.target_drives || 0)}`;
}
function branchControlCard(n) {
  const c = BRANCH_CONN[n.id] || {};
  if (n.disabled_at) return `<div class="c12 card"><div class="card-h"><span>${esc(n.label)} · שליטה</span></div><div class="card-b">${UI.note("warn", "השרת מושבת — לא נשאל")}</div></div>`;
  if (c.state !== "ok") return `<div class="c12 card"><div class="card-h"><span>${esc(n.label)} · שליטה</span></div><div class="card-b">${UI.note("warn", `לא נקרא: ${esc(c.error || "השרת לא ענה")}`)}</div></div>`;
  const roomMachines = new Map((((c.room || {}).machines) || []).map((m) => [m.mac, m]));
  const machines = (c.machines || []).filter((m) => ["cloner", "build"].includes(branchMachineRole(m)));
  const actions = c.room && c.room.round
    ? `<button class="btn danger" onclick="stopBranchRound('${encodeId(n.id)}')">עצור סבב</button>`
    : `<button class="btn primary" onclick="openBranchRound('${encodeId(n.id)}')" ${(c.images || []).length ? "" : "disabled"}>פתח סבב</button>`;
  return `<div class="c12 card" data-branch-control="${esc(n.id)}"><div class="card-h"><span>${esc(n.label)} · חדר ומחשבים</span><span>${branchRoundHtml(n, c)}</span></div><div class="card-b">${c.liveError ? UI.note("warn", `מצב חי לא נקרא: ${esc(c.liveError)}`) : ""}<div class="action-strip">${actions}</div><div class="machine-grid">${machines.length ? machines.map((m) => branchMachineHtml(n.id, m, roomMachines.get(m.mac))).join("") : UI.empty("אין מחשבי בנייה או משכפלים רשומים")}</div></div></div>`;
}

function openBranchRound(nidEnc) {
  let nid = nidEnc; try { nid = decodeURIComponent(nid); } catch (e) {}
  const c = BRANCH_CONN[nid] || {}, n = BRANCH_NODES.find((x) => x.id === nid);
  const images = Array.isArray(c.images) ? c.images : [];
  if (!n || !images.length) { toast("אין אימג'ים בספריית שרת המשנה"); return; }
  const count = Math.max(1, (c.machines || []).filter((m) => branchMachineRole(m) === "cloner").reduce((sum, m) => sum + (Array.isArray(m.disks) ? m.disks.length : 0), 0));
  sheet({ title: `פתיחת סבב ב-${n.label}`, sub: "האימג' והסבב שייכים לשרת המשנה.",
    fields: [{ id: "image_id", label: "אימג'", type: "select", value: images[0].id, options: images.map((x) => ({ value: x.id, label: x.name })) },
      { id: "target_drives", label: "מספר דיסקי יעד", type: "number", value: String(count) }],
    submitLabel: "פתח סבב", onSubmit: async (v) => {
      const target = Number(v.target_drives);
      if (!Number.isInteger(target) || target < 1) throw new Error("מספר דיסקי היעד חייב להיות מספר שלם וחיובי");
      await post(`/storage-nodes/${encodeId(nid)}/console/room`, { image_id: v.image_id, target_drives: target, expand_partition: false });
      toast(`הסבב נפתח ב-${n.label}`); await refreshBranchLive(n); renderCurrent();
    } });
}

function stopBranchRound(nidEnc) {
  let nid = nidEnc; try { nid = decodeURIComponent(nid); } catch (e) {}
  const c = BRANCH_CONN[nid] || {}, n = BRANCH_NODES.find((x) => x.id === nid), r = c.room && c.room.round;
  if (!n || !r) return;
  const name = r.image_name || r.image_id || "";
  sheet({ title: `לעצור את הסבב ב-${n.label}?`, sub: `הסבב על "${name}" ייעצר`, danger: true,
    note: impactList([["b", "השידור נפסק והגל נסגר"], ["w", "מגירות שלא נכתבו נשארות טריות"]]), submitLabel: "עצירת הסבב",
    onSubmit: async () => { await post(`/storage-nodes/${encodeId(nid)}/console/room/close`, { confirm_name: name }); toast(`הסבב נעצר ב-${n.label}`); await refreshBranchLive(n); renderCurrent(); } });
}

function transferDirectionHtml(t) {
  const primary = (typeof ME !== "undefined" && ME.server_name) ? ME.server_name : "ראשי";
  const node = t.node_label || t.node_id || "";
  if (t.direction === "pull") return `${esc(node)} → ${esc(primary)}`;
  return esc(node);
}

function branchTransferRow(t) {
  const [label, cls] = transferStateLabel(t);
  const active = ["sending", "verifying", "queued"].includes(t.state);
  const pct = t.bytes_total ? Math.min(100, Math.round(100 * t.bytes_sent / t.bytes_total)) : 0;
  const pace = transferPace(t);
  const progress = (active
    ? UI.barRow(pct, t.state === "verifying" ? "indeterminate" : "", `${esc(formatGB(t.bytes_sent))} / ${esc(formatGB(t.bytes_total))}`)
    : (t.state === "done" ? UI.barRow(100) : UI.barRow(null)))
    + (pace ? `<div class="sub transfer-pace">${esc(pace)}</div>` : "");
  const state = UI.status(cls, label) + (t.error ? `<div class="sub">${esc(t.error)}</div>` : "");
  const retry = t.state === "failed"
    ? `<button class="btn sm" onclick="retryBranchTransfer('${encodeId(t.node_id)}','${encodeId(t.image_id)}')">נסה שוב</button>` : "";
  const cancel = t.direction === "pull" && active
    ? `<button class="btn sm" onclick="cancelBranchTransfer('${encodeId(t.id)}')">ביטול</button>` : "";
  return { cells: [
    UI.name(t.image_name, t.image_id),
    transferDirectionHtml(t),
    progress,
    state,
    `<span class="mono" dir="ltr">${esc(fmtDate(t.created_at))} ${esc(fmtClock(t.created_at))}</span>`,
    esc(t.started_by || "—"),
    retry + cancel,
  ] };
}

function branchesTransfersTab() {
  const table = UI.datagrid({
    columns: ["אימג'", "אל", "התקדמות", "מצב", "התחיל", "מי", ""],
    rows: BRANCH_TRANSFERS.map(branchTransferRow),
    empty: "אין העברות.",
  });
  return `<div class="c12 card">${transferCapStrip("card-h")}<div class="card-b${BRANCH_TRANSFERS.length ? " flush" : ""}">${table}</div></div>`;
}

function maybeStartBranchesListPolling() {
  if (!BRANCH_TRANSFERS.some((t) => ["queued", "sending", "verifying"].includes(t.state))) return;
  stopBranchesListPolling();
  BRANCHES_LIST_TIMER = setInterval(() => {
    if (current !== "branches" || currentTab !== 1 || document.hidden) { stopBranchesListPolling(); return; }
    api("/storage-transfers").then((rows) => {
      BRANCH_TRANSFERS = rows;
      renderCurrent();
      if (!rows.some((t) => ["queued", "sending", "verifying"].includes(t.state))) stopBranchesListPolling();
    }).catch(() => {});
  }, 2000);
}

async function retryBranchTransfer(nidEnc, imageIdEnc) {
  let nid = nidEnc, image_id = imageIdEnc;
  try { nid = decodeURIComponent(nid); image_id = decodeURIComponent(image_id); } catch (e) {}
  try {
    const row = BRANCH_TRANSFERS.find((t) => t.node_id === nid && t.image_id === image_id);
    const kind = row && row.direction === "pull" ? "pull" : "transfer";
    const body = { image_id };
    if (kind === "transfer") body.location_id = (row && row.location_id) || "loc_local";
    await post(`/storage-nodes/${encodeId(nid)}/${kind}`, body);
    toast("ההעברה הופעלה מחדש");
    BRANCH_TRANSFERS = await api("/storage-transfers");
    renderCurrent();
    maybeStartBranchesListPolling();
  } catch (e) { toast(e.message); }
}

async function cancelBranchTransfer(tidEnc) {
  let tid = tidEnc;
  try { tid = decodeURIComponent(tid); } catch (e) {}
  try {
    await post(`/storage-transfers/${encodeId(tid)}/cancel`, {});
    toast("ההעברה בוטלה");
    BRANCH_TRANSFERS = await api("/storage-transfers");
    renderCurrent();
  } catch (e) { toast(e.message); }
}

async function startPullFromSecondary(nidEnc, imageIdEnc) {
  let nid = nidEnc, image_id = imageIdEnc;
  try { nid = decodeURIComponent(nid); image_id = decodeURIComponent(image_id); } catch (e) {}
  try {
    await post(`/storage-nodes/${encodeId(nid)}/pull`, { image_id });
    toast("ההעברה לראשי התחילה");
    if (current === "branch") openBranchView(encodeId(nid), "transfers", null);
    else {
      BRANCH_TRANSFERS = await api("/storage-transfers");
      renderCurrent();
      maybeStartBranchesListPolling();
    }
  } catch (e) { toast(e.message); }
}

function branchesGroupsTab() {
  return `<div class="c12 card"><div class="chead"><div><h2>קבוצות שרתי משנה</h2><p>ארגון שרתי המשנה לקבוצות תצוגה.</p></div>${isAdmin() ? `<button class="btn" id="add-branch-group">+ קבוצה חדשה</button>` : ""}</div><div id="branch-groups"></div></div>`;
}

/* מתעדכנת לאחר כל רינדור של לשונית "קבוצות" — renderBranchGroups
   (storage_nodes.js) כותבת ל-#branch-groups ומחברת את הכפתורים/הגרירה. */
function wireBranchesGroupsTab() {
  if (current !== "branches" || currentTab !== 2 || !(BRANCH_NODES || []).length) return;
  renderBranchGroups(BRANCH_GROUPS, loadBranchesData);
}

/* 16/10 (ביקורת הקונסולה): הדף מוצג לראשי/עצמאי גם כשאין עדיין שרת משנה —
   אחרת אין מאיפה לרשום את הראשון. בלי שרת משנה: לשונית אחת, מצב ריק וכפתור
   רישום בולט; העברות וקבוצות מופיעות רק כשיש שרת משנה רשום. */
function branchesEmptyState() {
  const button = isAdmin()
    ? `<button class="btn primary" data-testid="enroll-first" onclick="openEnrollSheet()">+ רישום שרת משנה</button>` : "";
  return `<div class="c12 card"><div class="card-b branches-empty" data-testid="branches-empty">
    <h3>אין עדיין שרת משנה</h3>
    <p class="muted">שרת משנה הוא שרת ImageSrv באתר אחר. במסך השרת של שרת המשנה, <b>F4</b> מציג את הכתובת, את טביעת הזהות ואת קוד הצימוד — ואותם מקלידים כאן.</p>
    ${button}</div></div>`;
}

function branchesPage(tab = 0) {
  const nodes = BRANCH_NODES || [];
  const groups = BRANCH_GROUPS || [];
  if (!nodes.length) {
    const header = UI.objHeader({
      crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "שרתי משנה" }],
      icon: "server", name: "שרתי משנה", sub: "אין שרתי משנה", pill: "", actions: "",
      tabs: ["שרתים"], tab: 0,
    });
    return `<div class="page">${header}<div class="body">${branchesEmptyState()}</div></div>`;
  }
  const notResponding = nodes.filter((n) => (BRANCH_CONN[n.id] || {}).state === "err").length;
  const sub = `${nodes.length} ${nodes.length === 1 ? "שרת משנה" : "שרתי משנה"} · ${groups.length} ${groups.length === 1 ? "קבוצה" : "קבוצות"}`;
  const pill = notResponding ? UI.pill("err", `${notResponding} ${notResponding === 1 ? "לא מגיב" : "לא מגיבים"}`) : "";
  const actions = isAdmin()
    ? `<button class="btn primary" onclick="openEnrollSheet()">+ רישום שרת משנה</button><button class="btn" onclick="openBranchGroupSheet()">+ קבוצה</button>`
    : "";
  const header = UI.objHeader({
    crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "שרתי משנה" }],
    icon: "server", name: "שרתי משנה", sub, pill, actions,
    tabs: ["שרתים", "העברות", "קבוצות"], tab,
  });
  const body = tab === 1 ? branchesTransfersTab() : tab === 2 ? branchesGroupsTab() : branchesServersTab();
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}

/* ---------- לשונית "שרתי משנה" בהגדרות (נדב 18/10) ----------
   כרטיס אחד, שורה אחת לשרת המשנה: שם וכתובת בתחילת השורה, סטטוס החיבור כ-pill ופעולות בסופה. אותם נתונים
   ופעולות של דף שרתי המשנה (BRANCH_NODES / BRANCH_CONN, ‏openEnrollSheet, ‏editBranchNode, ‏checkBranchNow). */
async function loadSettingsSecondaries() {
  try { await loadBranchesData(); }
  catch (e) { BRANCH_ERR = e.message; if (current === "settings") renderCurrent(); }
}
function branchPillHtml(n) {
  const c = BRANCH_CONN[n.id] || { state: "checking" };
  const seen = branchWhen(c.lastSeenAt), failed = branchWhen(c.lastErrorAt);
  if (c.state === "disabled") return [UI.pill("", "מושבת"), seen ? `ענתה לאחרונה ${esc(seen)}` : ""];
  if (c.state === "checking") return [UI.pill("", "בודק חיבור…"), seen ? `ענתה לאחרונה ${esc(seen)}` : ""];
  if (c.state === "ok") return [UI.pill("ok", "מחובר"), seen ? `ענתה ${esc(seen)}` : ""];
  const err = c.error ? (isRawTechnical(c.error) ? `<details class="tech"><summary>פרטים טכניים</summary><code dir="ltr">${esc(c.error)}</code></details>` : esc(c.error)) : "";
  return [UI.pill("err", "לא ענה"), [failed ? esc(failed) : "", err].filter(Boolean).join(" · ")];
}
function settingsSecondaryRow(n) {
  const idEnc = encodeId(n.id), c = BRANCH_CONN[n.id] || {};
  const [pill, sub] = branchPillHtml(n);
  const meta = [`נרשם ${esc(fmtDate(n.enrolled_at))}`, Array.isArray(c.machines) ? `${c.machines.length} מחשבים` : "", n.group_label ? esc(n.group_label) : ""].filter(Boolean).join(" · ");
  const acts = (n.disabled_at ? "" : ghostBtn("בדוק חיבור", `checkBranchNow('${idEnc}')`)) + ghostBtn("עריכה…", `editBranchNode('${idEnc}')`)
    + (n.disabled_at ? "" : ghostBtn("פתח", `openSecondaryPage('${idEnc}','home',null)`));
  return `<dt class="sec"><span class="name">${esc(n.label)}</span><span class="sub mono" dir="ltr">${esc(n.base_url)}</span><span class="sub">${meta}</span></dt>`
    + `<dd class="sec" data-node-row="${esc(n.id)}">${pill}${sub ? `<span class="sub">${sub}</span>` : ""}${acts}</dd>`;
}
function settingsSecondariesTab() {
  if (BRANCH_ERR) return UI.note("err", `לא הצלחתי לקרוא את רשימת שרתי המשנה: ${esc(BRANCH_ERR)}`);
  if (!BRANCH_READY) return `<div class="set-col"><div class="muted">טוען…</div></div>`;
  const nodes = BRANCH_NODES || [];
  const enroll = `<button class="btn primary" type="button" data-testid="enroll-first" onclick="openEnrollSheet()">+ רישום שרת משנה</button>`;
  const body = nodes.length ? `<dl class="mkv sec-list">${nodes.map(settingsSecondaryRow).join("")}</dl>`
    : UI.empty("שרת משנה הוא שרת ImageSrv באתר אחר. מרשמים אותו כאן, ורק אז מופיעים דף שרתי המשנה והעברת אימג'ים.", enroll, "אין עדיין שרת משנה");
  const acts = nodes.length ? `<button class="btn sm" type="button" onclick="openEnrollSheet()">+ רישום שרת משנה</button>` : "";
  return `<div class="set-col"><section class="set-grp">${UI.card({ title: "שרתי משנה", acts, body })}`
    + `<p class="set-foot">שרת משנה הוא שרת ImageSrv באתר אחר. במסך השרת של שרת המשנה, F4 מציג את הכתובת, את טביעת הזהות ואת קוד הצימוד — ואותם מקלידים ברישום. העברת אימג'ים וקבוצות בין שרתי המשנה — בדף "שרתי משנה".</p></section></div>`;
}

/* ---------- פעולות על שרת משנה מהטבלה (עריכה/השבתה/הסרה) ---------- */

/* ‏#1997 (נדב, 22/10): שם שרת משנה הוא שם מחשב. הכלל נאכף בשרת (hostname_rule) — כאן רק ההסבר. */
const NODE_NAME_HELP = "שם מחשב — באנגלית, עד 15 תווים: אותיות, ספרות ומקף, לא בהתחלה ולא בסוף";
/* ‏"דורש טיפול" בסקירה: שינוי שם של שרת משנה ששמו אינו חוקי ("חיפה"), גם כשדף שרתי המשנה עוד לא נטען. */
async function renameBranchNode(idEnc) {
  try { [BRANCH_GROUPS, BRANCH_NODES] = await Promise.all([api("/storage-node-groups"), api("/storage-nodes")]); }
  catch (e) { toast(e.message); return; }
  editBranchNode(idEnc);
}

function editBranchNode(idEnc) {
  let id = idEnc; try { id = decodeURIComponent(id); } catch (e) {}
  const node = BRANCH_NODES.find((n) => n.id === id);
  if (!node) return;
  const options = [{ value: "", label: "ללא קבוצה" }].concat(BRANCH_GROUPS.map((g) => ({ value: g.id, label: g.label })));
  sheet({
    title: "עריכת שרת משנה", sub: node.base_url,
    fields: [
      { id: "label", label: "שם השרת", value: node.label, dir: "ltr", help: NODE_NAME_HELP },
      { id: "group_id", label: "קבוצה", type: "select", value: node.group_id || "", options },
    ],
    onSubmit: async (v) => {
      await put(`/storage-nodes/${encodeId(node.id)}`, { label: v.label, group_id: v.group_id || null });
      toast("נשמר.");
      await loadBranchesData();
    },
  });
}

async function toggleBranchNode(idEnc) {
  let id = idEnc; try { id = decodeURIComponent(id); } catch (e) {}
  const node = BRANCH_NODES.find((n) => n.id === id);
  if (!node) return;
  try {
    await post(`/storage-nodes/${encodeId(id)}/disabled`, { disabled: !node.disabled_at });
    await loadBranchesData();
  } catch (e) { toast(e.message); }
}

function removeBranchNode(idEnc) {
  let id = idEnc; try { id = decodeURIComponent(id); } catch (e) {}
  const node = BRANCH_NODES.find((n) => n.id === id);
  if (!node) return;
  sheet({
    title: "להסיר את שרת המשנה?", sub: node.label,
    note: impactList([["b", "השרת יוסר מהמרשם"], ["w", "הרישום מחדש דורש כתובת ואישורים"]]),
    danger: true, submitLabel: "הסרת השרת",
    onSubmit: async () => { await del(`/storage-nodes/${encodeId(id)}`); await loadBranchesData(); },
  });
}

function openTransferSheetForNode(idEnc) {
  let id = idEnc; try { id = decodeURIComponent(id); } catch (e) {}
  const node = BRANCH_NODES.find((n) => n.id === id);
  if (!node) return;
  api("/images").then((images) => openTransferSheet(node, images, loadBranchesData)).catch((e) => toast(e.message));
}

/* ---------- רישום שרת משנה (enroll): כתובת → preview SPKI → קוד ----------
   ‏POST /storage-nodes/enroll/preview קורא את ה-SPKI של שרת המשנה שבכתובת,
   ורק אחרי שהמנהל השווה אותו למה שמוצג על שרת המשנה עצמו הוא מקליד את הקוד
   החד-פעמי; ‏POST /storage-nodes/enroll מצמיד את ה-SPKI מהשלב הקודם
   (לא מהצהרת ה-JSON של שרת המשנה — #883). גרסת הפרוטוקול קבועה כמו בשרת
   (interserver_auth.PROTOCOL_VERSION). */
const ENROLL_PROTOCOL_VERSION = "2.1";

/* נדב 18/10: ברישום מקלידים רק את כתובת ה-IP של שרת המשנה — הקונסולה בונה את
   ‏https://<ip>:<פורט>. הפורט הוא פורט ה-API הבין-שרתי של השרת
   (health.DEFAULT_OWN_PORTS["interserver"] = ברירת המחדל של ‎--interserver-port
   ב-main.py); טסט ב-test_console_static מצמיד את שניהם, כדי שלא ייסחפו. */
const INTERSERVER_PORT = 8443;
const ENROLL_NEW_GROUP = "__new__";
const ENROLL_IPV4_RE = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const ENROLL_HOST_RE = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/i;

/* מה שהמפעיל הקליד → ‏base_url מלא, או חריגה עם הסירוב בעברית. כתובת מלאה
   שהודבקה (‏https://10.20.0.5:8443/, ‏10.20.0.5:8443, גם עם ‎/api/interserver/v1)
   מנורמלת ולא נדחית; פורט מפורש נשמר. השרת ממשיך לקבל base_url מלא. */
function enrollUrlFromInput(raw) {
  const bad = "כתובת לא תקינה — הקלידו את כתובת ה-IP של שרת המשנה (למשל 10.20.0.5) או את שם השרת";
  let s = String(raw || "").trim();
  if (!s) throw new Error("חסרה כתובת IP של שרת המשנה");
  const scheme = s.match(/^([a-z][a-z0-9+.-]*):\/\//i);
  if (scheme) {
    if (!/^https?$/i.test(scheme[1])) throw new Error(bad);
    s = s.slice(scheme[0].length);
  }
  s = s.replace(/\/api\/interserver\/v1\/?$/i, "").replace(/\/+$/, "");
  if (/[/?#@\s]/.test(s)) throw new Error(bad);
  const m = s.match(/^([^:]+)(?::(\d{1,5}))?$/);
  if (!m) throw new Error(bad);
  const host = m[1].toLowerCase(), port = m[2] === undefined ? INTERSERVER_PORT : Number(m[2]);
  if (!(port >= 1 && port <= 65535)) throw new Error("פורט לא תקין בכתובת שרת המשנה");
  const ipLike = /^[\d.]+$/.test(host);
  if (ipLike ? !ENROLL_IPV4_RE.test(host) : !ENROLL_HOST_RE.test(host)) throw new Error(bad);
  return `https://${host}:${port}`;
}

/* ‏prefill: {url, label, group_id} — כשחוזרים מ"+ קבוצה חדשה…" עם הקבוצה שנוצרה. */
function openEnrollSheet(prefill = {}) {
  const options = [{ value: "", label: "ללא קבוצה" }]
    .concat(BRANCH_GROUPS.map((g) => ({ value: g.id, label: g.label })))
    .concat(isAdmin() ? [{ value: ENROLL_NEW_GROUP, label: "+ קבוצה חדשה…" }] : []);
  sheet({
    title: "רישום שרת משנה — שלב 1 מתוך 2: כתובת",
    sub: "השלב הבא יציג את טביעת ה-TLS של שרת המשנה (SPKI) — יש להשוות אותה למה ששרת המשנה עצמו מציג, לפני שליחת הקוד.",
    fields: [
      { id: "url", label: "כתובת IP של שרת המשנה", value: prefill.url || "", placeholder: "10.20.0.5", dir: "ltr",
        help: `מספיק ה-IP — הקונסולה מוסיפה https:// ואת הפורט הבין-שרתי (${INTERSERVER_PORT}).` },
      { id: "label", label: "שם השרת", value: prefill.label || "", dir: "ltr", placeholder: "imagesrv-haifa", help: NODE_NAME_HELP },
      { id: "group_id", label: "קבוצה", type: "select", value: prefill.group_id || "", options },
    ],
    submitLabel: "בדוק SPKI",
    onSubmit: async (v) => {
      const url = enrollUrlFromInput(v.url);
      const step1 = { ...v, url, group_id: v.group_id === ENROLL_NEW_GROUP ? "" : v.group_id };
      const preview = await post("/storage-nodes/enroll/preview", { url, label: v.label });   // ‏#1997: השם נבדק כבר בשלב 1
      openEnrollConfirmSheet(step1, preview);
    },
  });
  const select = $("#sf-group_id");
  if (select) select.onchange = () => {
    if (select.value !== ENROLL_NEW_GROUP) return;
    const keep = { url: ($("#sf-url") || {}).value || "", label: ($("#sf-label") || {}).value || "" };
    openEnrollNewGroup(keep);
  };
}

/* "+ קבוצה חדשה…" ברשימת הקבוצות של הרישום: הקבוצה נוצרת בחלון הקיים (אותו גוף כמו
   ‏openBranchGroupSheet), ואחריו חלון הרישום חוזר — עם מה שהוקלד, והקבוצה החדשה
   בחורה. "חזרה" — חוזר בלי ליצור. עד 18/10 הייתה רק "ללא קבוצה": יצירת קבוצה ישבה
   בלשונית "קבוצות" של דף "שרתי משנה", שמוצג רק כשכבר יש שרת משנה. */
function openEnrollNewGroup(keep) {
  sheet({
    title: "קבוצת שרתי משנה חדשה",
    fields: [{ id: "label", label: "שם הקבוצה", placeholder: "למשל: צפון" }],
    submitLabel: "צור קבוצה",
    extra: { label: "חזרה", onClick: () => openEnrollSheet(keep) },
    onSubmit: async (v) => {
      const created = await post("/storage-node-groups", { label: v.label });
      BRANCH_GROUPS = await api("/storage-node-groups");
      return () => openEnrollSheet({ ...keep, group_id: created && created.id ? created.id : "" });
    },
  });
}

/* 16/10 (מוקאפ #1525, ג׳1–ג׳3): שרת המשנה מציג את הקוד ואת "זהות השרת" במסך
   השרת שלו (F4). כאן: הטביעה בקבוצות של ארבעה, עם ארבע הראשונות מודגשות —
   הן "זהות השרת" שעל שרת המשנה וגם סיומת מזהה שרת המשנה — ושמונה תיבות לקוד
   (interserver_auth: ‏XXXX-XXXX, בלי 0/O/1/I/L). הכפתור כבוי עד שכל
   השמונה מלאות. */
const ENROLL_CODE_LEN = 8;
const ENROLL_CODE_CHAR = /^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]$/;

function enrollSpkiGroups(spki) {
  return String(spki || "").toUpperCase().match(/.{1,4}/g) || [];
}

function enrollConfirmHtml(step1, preview) {
  const groups = enrollSpkiGroups(preview.secondary_spki);
  const boxes = Array.from({ length: ENROLL_CODE_LEN }, (_, i) =>
    `${i === ENROLL_CODE_LEN / 2 ? '<span class="enroll-d" aria-hidden="true">-</span>' : ""}<input class="enroll-c" id="enroll-c-${i}" maxlength="1" inputmode="text" autocomplete="off" spellcheck="false" aria-label="תו ${i + 1} מתוך ${ENROLL_CODE_LEN}">`).join("");
  return `<div class="enroll-cmp"><div class="t">① השווה לטביעה שמוצגת על מסך שרת המשנה — שם "זהות השרת"</div>
      <div class="enroll-fp mono" dir="ltr">${groups.slice(0, 4).map((g) => `<span class="enroll-hl">${esc(g)}</span>`).join(" ")}</div>
      <div class="enroll-rest mono" dir="ltr">${esc(groups.join(" "))}</div></div>
    <div class="detail-grid"><div class="detail-box"><span class="k">מזהה שרת משנה</span><span class="v mono" dir="ltr">${esc(preview.node_id)}</span></div><div class="detail-box"><span class="k">כתובת שנבדקה</span><span class="v mono" dir="ltr">${esc(step1.url)}</span></div></div>
    <div class="enroll-code-h">② קוד חד-פעמי (מוצג על שרת המשנה)</div>
    <div class="enroll-cin" dir="ltr">${boxes}</div>
    <div class="n-help">מוצג על מסך שרת המשנה תחת "צימוד לשרת ראשי" (F4). 8 תווים, שימוש חד-פעמי.</div>`;
}

function enrollCodeBoxes(form) {
  return form && form.querySelectorAll ? Array.from(form.querySelectorAll(".enroll-c")) : [];
}

function enrollCodeValue(form) {
  return enrollCodeBoxes(form).map((b) => String(b.value || "").toUpperCase()).join("");
}

function wireEnrollCode(form) {
  const boxes = enrollCodeBoxes(form);
  const submit = form && form.querySelector ? form.querySelector("#sf-submit") : null;
  const sync = () => {
    const ready = boxes.length === ENROLL_CODE_LEN && boxes.every((b) => ENROLL_CODE_CHAR.test(b.value));
    if (submit) submit.disabled = !ready;
    boxes.forEach((b) => b.classList.remove("err"));
  };
  boxes.forEach((box, i) => {
    box.addEventListener("input", () => {
      const typed = String(box.value || "").toUpperCase().split("").filter((c) => ENROLL_CODE_CHAR.test(c));
      box.value = typed[0] || "";
      // הדבקה של הקוד כולו (או המשכו) לתיבה אחת — מתפזרת קדימה
      typed.slice(1).forEach((c, k) => { if (boxes[i + 1 + k]) boxes[i + 1 + k].value = c; });
      const next = boxes[Math.min(i + typed.length, boxes.length - 1)];
      if (typed.length && next && next.focus) next.focus();
      sync();
    });
    box.addEventListener("keydown", (event) => {
      if (event.key === "Backspace" && !box.value && boxes[i - 1]) boxes[i - 1].focus();
    });
  });
  sync();
}

function openEnrollConfirmSheet(step1, preview) {
  sheet({
    title: "רישום שרת משנה — שלב 2 מתוך 2: אישור והקוד",
    sub: "הטביעה למטה נקראה מהכתובת שהוקלדה. אם היא אינה זהה למה שמוצג על שרת המשנה — אל תמשיך.",
    note: enrollConfirmHtml(step1, preview),
    submitLabel: "רשום שרת משנה",
    onSubmit: async () => {
      const form = $("#sheet");
      const code = enrollCodeValue(form);
      if (code.length !== ENROLL_CODE_LEN) throw new Error("יש להקליד את כל 8 התווים של הקוד.");
      try {
        await post("/storage-nodes/enroll", {
          url: step1.url, protocol_version: ENROLL_PROTOCOL_VERSION,
          code: `${code.slice(0, 4)}-${code.slice(4)}`,
          expected_secondary_spki: preview.secondary_spki,
          label: step1.label, group_id: step1.group_id || null,
        });
      } catch (error) {
        enrollCodeBoxes(form).forEach((b) => b.classList.add("err"));
        throw new Error(`${error.message} — אם הקוד שגוי או שפג תוקפו: על שרת המשנה F5 ← קוד חדש, ואז נסה שוב.`);
      }
      toast(`שרת המשנה "${step1.label}" נרשם.`);
      await loadBranchesData();
    },
  });
  wireEnrollCode($("#sheet"));
}

function openBranchGroupSheet() {
  sheet({
    title: "קבוצת שרתי משנה חדשה",
    fields: [{ id: "label", label: "שם הקבוצה", placeholder: "למשל: צפון" }],
    submitLabel: "צור קבוצה",
    onSubmit: async (v) => { await post("/storage-node-groups", { label: v.label }); await loadBranchesData(); },
  });
}

/* ---------- משותף לשני הדפים: מכונות שרת המשנה, העברת אימג' ---------- */

function branchMonitor(nid, mac, name) {
  if (!isAdmin()) return;
  window.open(`monitor.html?node=${encodeId(nid)}&mac=${encodeId(mac)}&name=${encodeId(name)}`,
              `imagesrv-monitor-${nid}-${mac}`);
}

function renderBranchTransfers(nid, target = null, limit = 8) {
  const box = target || $(`#branch-transfers-${CSS.escape(nid)}`);
  if (!box) return;
  const rows = BRANCH_TRANSFERS.filter((t) => t.node_id === nid).slice(0, limit);
  if (!rows.length) { box.innerHTML = `<div class="sub">אין העברות.</div>`; return; }
  box.innerHTML = rows.map((t) => {
    const [label, cls] = transferStateLabel(t);
    const pct = t.bytes_total ? Math.min(100, Math.round(100 * t.bytes_sent / t.bytes_total)) : 0;
    const pace = transferPace(t);
    const meter = t.state === "verifying"
      ? `<div class="bar indeterminate" style="margin-top:6px"><i></i></div>`
      : `<div class="progress" style="margin-top:6px"><i style="width:${pct}%"></i></div>`;
    const bar = ["sending", "verifying", "queued"].includes(t.state)
      ? `${meter}<span class="sub">${esc(formatGB(t.bytes_sent))} מתוך ${esc(formatGB(t.bytes_total))} (${pct}%)${pace ? ` · <span class="transfer-pace">${esc(pace)}</span>` : ""}</span>`
      : "";
    const err = t.error ? `<div class="notice warn" role="status">${esc(t.error)}</div>` : "";
    const dir = t.direction === "pull" ? transferDirectionHtml(t) : "";
    const cancel = t.direction === "pull" && ["sending", "verifying", "queued"].includes(t.state)
      ? `<button class="btn sm" onclick="cancelBranchTransfer('${encodeId(t.id)}')">ביטול</button>` : "";
    return `<div class="member"><b>${esc(t.image_name)}</b> <span class="status ${cls}"><i></i>${esc(label)}</span>
      ${dir ? `<div class="sub">${dir}</div>` : ""}
      <div class="sub mono" dir="ltr">${esc(t.created_at ? fmtWhen(t.created_at) : "")}</div>${bar}${err}${cancel}</div>`;
  }).join("");
}

async function openTransferSheet(n, images, afterSubmit) {
  if (!images.length) { toast("אין אימג'ים בספרייה להעברה"); return; }
  let locations;
  try {
    locations = (await api(`/storage-nodes/${encodeId(n.id)}/console/storage-locations`)).locations;
  } catch (e) {
    toast(`לא ניתן לקרוא את מיקומי האחסון של ${n.label}: ${e.message}`);
    return;
  }
  const writable = (locations || []).filter((loc) => loc.state === "connected");
  if (!writable.length) { toast("אין בשרת המשנה מיקום אחסון זמין לקליטה"); return; }
  const options = images.map((m) => ({
    value: m.id, label: `${m.folder ? m.folder + " / " : ""}${m.name} (${formatGB(m.total_compressed_bytes)})`,
  }));
  sheet({
    title: `העברת אימג' אל ${n.label}`,
    sub: "האימג' ייכנס לספריית שרת המשנה רק אחרי ששרת המשנה אימת sha256 של כל מחיצה. ניתוק באמצע = כשל גלוי, ומתחילים מחדש.",
    fields: [
      { id: "image_id", label: "אימג'", type: "select", value: options[0].value, options },
      { id: "location_id", label: "מיקום אחסון בשרת המשנה", type: "select",
        value: writable.some((loc) => loc.id === "loc_local") ? "loc_local" : writable[0].id,
        options: writable.map((loc) => ({value: loc.id, label: `${loc.name} (${loc.id})`})) },
    ],
    submitLabel: "התחל העברה",
    onSubmit: async (v) => {
      await post(`/storage-nodes/${encodeId(n.id)}/transfer`,
                 { image_id: v.image_id, location_id: v.location_id });
      toast("ההעברה התחילה");
      await afterSubmit();
    },
  });
}

/* ---------- #936: דף "העברות" של שרת משנה אחד ----------
   ‏#1179: שאר העלים בעץ של שרת המשנה הם העמודים של הראשי עצמם בהקשר שרת המשנה (SERVER_CTX
   ב-console.js). כאן נשאר מה שאין לו מקבילה בראשי: ספריית שרת המשנה עם "העבר לראשי",
   ו"העבר אימג'" + יומן ההעברות. שרת משנה שלא ענה — "‹שם› לא ענה: ‹סיבה›", לא דף ריק. */

let BRANCH_VIEW_GEN = 0;

function branchDownNote(label, error) {
  return `<div class="notice err" role="status" data-branch-down>${esc(label)} לא ענה: ${esc(error || "בלי סיבה בתשובה")}</div>`;
}

function branchConnHtml(n, conn) {
  if (conn.disabled) return { status: `<span class="status"><i></i>מושבת</span>`, note: `<div class="notice err" role="status">השרת מושבת — לא נשאל.</div>` };
  if (conn.unchecked) return { status: `<span class="status"><i></i>לא נבדק</span>`, note: `<div class="notice warn" role="status">לא נבדק אם ${esc(n.label)} עונה — הבקשה לראשי נכשלה: ${esc(conn.error)}</div>` };
  if (!conn.connected) return { status: `<span class="status err"><i></i>לא מחובר</span>`, note: branchDownNote(n.label, conn.error) };
  return { status: `<span class="status ok"><i></i>מחובר</span>`, note: "" };
}

async function loadBranchView() {
  const host = $("#branch-view");
  if (!host) return;
  stopBranchPolling();
  // מעבר מהיר בין שרתי משנה בעץ — טעינה שהתיישנה בזמן ה-await לא כותבת ל-DOM שכבר הוחלף.
  const gen = ++BRANCH_VIEW_GEN;
  const stale = () => gen !== BRANCH_VIEW_GEN || $("#branch-view") !== host;
  const nid = BRANCH_NODE;
  const nodes = await api("/storage-nodes");
  if (stale()) return;
  const n = nodes.find((x) => x.id === nid);
  if (!n) {
    host.innerHTML = `<div class="card"><div class="card-b"><div class="empty">שרת המשנה אינו רשום עוד. בחר שרת אחר בעץ.</div></div></div>`;
    return;
  }
  BRANCH_NODES = nodes;
  const disabled = !!n.disabled_at;
  const remoteP = disabled
    ? Promise.resolve({ connected: false, error: "שרת המשנה מושבת", images: [] })
    : api(`/storage-nodes/${encodeId(n.id)}/images`).catch((e) => (
      { connected: false, unchecked: true, error: e.message, images: [] }));
  const [transfers, images, remote] = await Promise.all([
    api("/storage-transfers"), api("/images"), remoteP, loadTransferCap(),
  ]);
  if (stale()) return;
  BRANCH_TRANSFERS = transfers;
  if (!disabled && !remote.unchecked) markSecondaryStatus(n, !!remote.connected, remote.error);
  const c = branchConnHtml(n, disabled ? { disabled: true } : remote);
  const localIds = new Set(images.map((m) => m.id));
  const libRows = (remote.images || []).map((img) => {
    const present = localIds.has(img.id);
    const pullBtn = `<button class="btn sm primary" ${present || disabled ? "disabled" : ""} onclick="startPullFromSecondary('${encodeId(n.id)}','${encodeId(img.id)}')">העבר לראשי</button>`;
    return `<tr>
      <td><b>${esc(img.name)}</b> <span class="mono muted" dir="ltr">${esc(img.id)}</span></td>
      <td>${esc(formatGB(img.size_bytes || 0))}</td>
      <td class="mono" dir="ltr">${esc(fmtWhen(img.created_at))}</td>
      <td>${present ? "✓" : "—"}</td>
      <td>${pullBtn}</td>
    </tr>`;
  }).join("");
  const libBody = !remote.connected ? c.note
    : (libRows
      ? `<table><thead><tr><th>שם</th><th>גודל</th><th>תאריך</th><th>קיים בראשי</th><th></th></tr></thead><tbody>${libRows}</tbody></table>`
      : `<div class="empty">אין אימג'ים בספריית שרת המשנה.</div>`);
  host.innerHTML = `<div class="card"><div class="card-h"><span>${esc(n.label)} · העברות <small class="muted">${esc(n.group_label || "ללא קבוצה")}</small></span>${c.status}</div><div class="card-b">
      <div class="action-strip"><button class="btn primary" id="branch-view-transfer" ${disabled ? "disabled" : ""}>העבר אימג'</button></div>
      <h4>ספריית האימג'ים של שרת המשנה</h4>
      ${libBody}
      <h4>העברות</h4>
      ${transferCapStrip("sub")}
      <div id="branch-transfers-${esc(n.id)}"></div>
    </div></div>`;
  $("#branch-view-transfer").onclick = () => openTransferSheet(n, images, async () => loadBranchView());
  renderBranchTransfers(n.id, null, 20);
  BRANCH_TIMER = setInterval(() => {
    if (current !== "branch" || BRANCH_NODE !== n.id || document.hidden) { stopBranchPolling(); return; }
    if (!BRANCH_TRANSFERS.some((t) => ["queued", "sending", "verifying"].includes(t.state))) return;
    api("/storage-transfers").then((rows) => {
      BRANCH_TRANSFERS = rows;
      renderBranchTransfers(n.id, null, 20);
    }).catch(() => {});
  }, 2000);
}

/* ---------- #1473: הגבלת קצב להעברות בין אתרים (הכרעת נדב 16/10) ----------
   אופציונלי, ברירת מחדל ללא הגבלה; Mb/s לכל העברה, על push ועל pull. הערך הוא
   מה שבתוקף בשרת (GET /settings → intersite_rate_cap_mbps, "0" = ללא הגבלה);
   לא נקרא — נאמר שלא נקרא ולמה, לא "ללא הגבלה" (עיקרון 5). השינוי תופס גם
   העברה שכבר רצה, תוך שתי שניות (server/interserver/transfer_cap.py). */
const TRANSFER_CAP_KEY = "intersite_rate_cap_mbps";
const TRANSFER_CAP_MAX = 100000;
let TRANSFER_CAP = { mbps: null, error: "" };

async function loadTransferCap() {
  try {
    const values = await api("/settings");
    const n = Number(values && values[TRANSFER_CAP_KEY]);
    TRANSFER_CAP = Number.isInteger(n) && n >= 0
      ? { mbps: n, error: "" } : { mbps: null, error: "השרת לא החזיר ערך" };
  } catch (e) { TRANSFER_CAP = { mbps: null, error: e.message }; }
}

function transferCapText() {
  const m = TRANSFER_CAP.mbps;
  if (m == null) return `לא נקרא: ${esc(TRANSFER_CAP.error || "בלי סיבה")}`;
  if (m === 0) return "ללא הגבלה";
  const mb = m / 8;
  return `עד ${ltr(`${m} Mb/s`)} (${ltr(`${Number.isInteger(mb) ? mb : mb.toFixed(1)} MB/s`)}) לכל העברה`;
}

/* cls: "card-h" — שורת כותרת בכרטיס (לשונית "העברות"); "sub" — שורה מתחת ל-h4 (דף שרת המשנה). */
function transferCapStrip(cls) {
  const btn = isAdmin() && TRANSFER_CAP.mbps != null
    ? ` <button class="btn sm flat" type="button" onclick="transferCapSheet()">שינוי</button>` : "";
  return `<div class="${cls} transfer-cap" title="חל על העברה לשרת המשנה ועל העברה משרת המשנה לראשי; השינוי תופס גם העברה שכבר רצה"><span>הגבלת קצב בין אתרים: <b>${transferCapText()}</b></span>${btn}</div>`;
}

function transferCapSheet() {
  if (!isAdmin()) return;
  sheet({ title: "הגבלת קצב בין אתרים",
    sub: "כמה מהקו בין האתרים מותר להעברת אימג' אחת — לשרת המשנה ומשרת המשנה. 0 = ללא הגבלה (ברירת המחדל). השינוי תופס גם העברה שכבר רצה.",
    fields: [{ id: "v", label: `קצב מרבי לכל העברה (Mb/s, 0–${TRANSFER_CAP_MAX})`, type: "number", value: String(TRANSFER_CAP.mbps || 0) }],
    onSubmit: async (v) => {
      const raw = String(v.v == null ? "" : v.v).trim();
      const n = Number(raw);
      if (raw === "" || !Number.isInteger(n) || n < 0 || n > TRANSFER_CAP_MAX) {
        throw new Error(`הגבלת קצב: מספר שלם 0–${TRANSFER_CAP_MAX} (Mb/s)`);
      }
      await post("/settings", { [TRANSFER_CAP_KEY]: n });
      toast(n ? `הקצב בין אתרים מוגבל ל-${n} Mb/s — נרשם ביומן` : "ללא הגבלת קצב בין אתרים — נרשם ביומן");
      await loadTransferCap();
      if (current === "branch") loadBranchView(); else renderCurrent();
    } });
}
