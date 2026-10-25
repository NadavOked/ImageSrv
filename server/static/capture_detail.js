/* פירוט קליטה חי (#1505) — אותו מידע שמסך מחשב הבנייה מציג: קצב, מחיצות (תפקיד, מערכת קבצים,
   בשימוש / גודל, מצב), שלב, דחיסה, זמן שעבר וזמן שנותר.

   אין מוקאפ מאושר לפאנל הזה (docs/design/apple-mockups לא מכסה קליטה בקונסולה) — הפריסה משקפת את
   מסך השיכפול-המקומי הנייטיבי (native-gui/src/screens_lcrun.c: כותרת, שורת מדדים, מחיצות) בסגנון הקונסולה.

   המקור: `capture_detail` של /api/console/tasks (docs/interfaces.md). עיקרון 5: מה שלא נמדד נאמר במילים
   ("עוד לא נמדד", "לא נמדד"), לא 0 ולא "—" בלי הסבר. נטען אחרי console.js (esc, UI, fmtBytes, fmtSpan, Progress). */
"use strict";

const CAP_PART_STATE = { waiting: ["", "ממתין"], shrinking: ["warn", "מכווץ"], reading: ["run", "נקרא"], done: ["ok", "הושלם"] };
const CAP_ROLE = { esp: "ESP", msr: "MSR", recovery: "שחזור", windows: "Windows", linux: "Linux", swap: "swap", data: "נתונים" };
let CAPDETAIL_ID = null;

function capBytes(n) { return n == null ? `<span class="muted">לא נמדד</span>` : `<span title="${esc(n)} bytes">${ltr(fmtBytes(n))}</span>`; }

/* בשימוש / גודל, ומצב; מחיצה בקריאה — פס לפי done_bytes (ציר לא דחוס) כשיש מכנה. */
function capPartRows(parts) {
  return parts.map((p) => {
    const [cls, label] = CAP_PART_STATE[p.state] || ["", p.state];
    const known = p.used_bytes != null && p.used_bytes > 0 && p.done_bytes != null;
    const prog = p.state === "done" ? UI.barRow(100, "ok")
      : p.state === "reading" ? (known ? UI.barRow(100 * p.done_bytes / p.used_bytes) : UI.barRow(null, "", "נקרא · היקף לא ידוע"))
      : `<span class="muted">—</span>`;
    return [esc(p.index), `${esc(CAP_ROLE[p.role] || p.role)} <span class="mono muted">${esc(p.fs)}</span>`,
      `<bdi dir="ltr">${capBytes(p.used_bytes)} / ${capBytes(p.size_bytes)}</bdi>`, `${UI.status(cls, label)}${prog}`];
  });
}

/* הזמן שנותר — ולמה הוא לא ידוע, כשהוא לא. */
function capEta(t, d) {
  if (t.state !== "running") return `<span class="muted">—</span>`;
  if (d.total_used_bytes == null) return `<span class="muted">לא ידוע — לא כל המחיצות נמדדו</span>`;
  if (d.eta_s == null) return t.forecast && t.forecast.seconds != null   // #1843: מהקליטות הקודמות של המחשב
    ? forecastText(t.forecast, null) : `<span class="muted">עוד לא נמדד</span>`;
  return esc(fmtSpan(d.eta_s));
}

/* "נכתבו X מתוך Y שנקראו" — היחס הדחוס; בלי מכנה, שם האלגוריתם בלבד. */
function capCompression(t, d) {
  const algo = d.compression ? ltr(d.compression) : `<span class="muted">לא דווחה</span>`;
  if (d.read_bytes == null || !(d.read_bytes > 0) || !(t.bytes_written > 0)) return algo;
  return `${algo} · נכתבו ${ltr(fmtBytes(t.bytes_written))} מתוך ${ltr(fmtBytes(d.read_bytes))} שנקראו (${Math.round(100 * t.bytes_written / d.read_bytes)}%)`;
}

function captureDetailHtml(t) {
  const d = t.capture_detail, waiting = t.state === "pending", running = t.state === "running";
  const who = typeof taskWho === "function" ? taskWho(t) : (t.machine || t.mac || "");
  const stage = typeof captureStagesHtml === "function" ? captureStagesHtml(t) : "";
  const cancel = typeof isAdmin === "function" && isAdmin() && (waiting || running)
    ? `<div class="acts"><button class="btn danger" onclick="cancelCaptureVerified('${encodeId(t.id)}')">בטל קליטה</button></div>` : "";
  const sub = `<div class="obj-sub">${esc(who)} · <span class="mono">${esc(t.disk || "—")}</span> · ${esc(captureStateView(t).label)}</div>`;
  if (waiting) return `<div class="page drw cap-detail" data-task="${esc(t.id)}">${sub}${UI.note("warn", `ממתין ש${esc(who)} יעלה ב-PXE ויקבל את המשימה`)}${cancel}</div>`;

  const total = d && d.total_used_bytes, read = d && d.read_bytes;
  const pct = total > 0 && read != null ? Math.min(100, Math.round(100 * read / total)) : null;
  const rate = d && d.rate_bps != null ? `${fmtBytes(d.rate_bps)}/s` : null;
  const elapsed = d && d.elapsed_s != null ? fmtSpan(d.elapsed_s) : null;
  const kpis = `<div class="kpis">`
    + UI.kpi({ cls: "info", label: "התקדמות", value: pct != null ? pct + "%" : fmtBytes(t.bytes_written), bar: pct,
               sub: pct != null ? `${ltr(fmtBytes(read))} מתוך ${ltr(fmtBytes(total))} בשימוש` : "בייטים דחוסים שנכתבו · הסך לא ידוע" })
    + UI.kpi({ label: "קצב", value: rate || "עוד לא נמדד", sub: rate ? "דחוס · חלון של 30 שנ'" : "" })
    + UI.kpi({ label: "זמן שעבר", value: elapsed || "לא נמדד" })
    + UI.kpi({ label: "זמן שנותר", value: d && d.eta_s != null && running ? fmtSpan(d.eta_s) : "—", sub: d && !(d.eta_s != null && running) ? capEta(t, d) : "" })
    + `</div>`;
  const parts = d
    ? `<div class="sec">מחיצות</div>` + UI.datagrid({ columns: ["#", "תפקיד · מערכת קבצים", "בשימוש / גודל", "מצב"],
        rows: capPartRows(d.partitions), empty: "אין מחיצות" })
    : UI.note("warn", "הסוכן לא דיווח פירוט מחיצות (סוכן ישן, או טבלה פגומה שנזרקה) — מוצג רק מה שנכתב לשרת.");
  const kv = UI.kv([
    ["שלב", stage],
    ["דחיסה", d ? capCompression(t, d) : `<span class="muted">לא דווחה</span>`],
    ["נכתבו לשרת", ltr(fmtBytes(t.bytes_written))],
  ]);
  const err = t.error ? UI.note(t.state === "failed" ? "err" : "warn", esc(t.error)) : "";
  return `<div class="page drw cap-detail" data-task="${esc(t.id)}">${sub}${err}${kpis}${kv}${parts}${cancel}</div>`;
}

function openCaptureDetail(id) {
  let tid = id; try { tid = decodeURIComponent(id); } catch (e) { /* מזהה גולמי */ }
  const t = (CAPTURE_TASKS || []).find((x) => x.id === tid);
  if (!t) { toast("הקליטה לא נמצאה ברשימה"); return; }
  CAPDETAIL_ID = t.id;
  openDrawer(`קליטה: ${t.name || t.id}`, captureDetailHtml(t), { sub: "" });
}

/* נקרא בסוף loadCaptures (כל ~2 שניות): הפאנל הפתוח מתעדכן במקום; מגירה אחרת שנפתחה אחריו — לא נוגעים. */
function capDetailRefresh() {
  if (!CAPDETAIL_ID) return;
  const body = document.getElementById("drawerBody");
  const shown = body && body.querySelector && body.querySelector(".cap-detail");
  if (!shown || shown.getAttribute("data-task") !== CAPDETAIL_ID) { CAPDETAIL_ID = null; return; }
  const t = (CAPTURE_TASKS || []).find((x) => x.id === CAPDETAIL_ID);
  if (t) setDrawerBody(captureDetailHtml(t));
}
