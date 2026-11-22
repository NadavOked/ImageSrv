/* ImageSrv -- #3654 (K14): דף "שאלות המוח" (מוקאפ מאושר 18/11, lab/design-ask-1118/console-ask.html): לשונית "פתוחות" (הסבר, ארבעה KPI,
   טבלה לפי הזמן שנשאר, "ענה"), לשונית "היסטוריה" (30 יום, סינון לפי תוצאה / מי ענה / על מה / טווח, חיפוש) ומגירת פרטים לשאלה שנסגרה.
   הנתונים והמצב ב-brain_asks.js. הדף מורגש בשרת הראשי בלבד: שרת משנה מחוץ להיקף. */
"use strict";

/* מצב הסינון של ההיסטוריה. st/who/kind/range נוסעים לשרת (brainAsksQuery); q מסונן כאן. */
const BRAIN_VIEW = { state: "", who: "", kind: "", range: "m", q: "" };
const BRAIN_WHO_HE = { admin: "מנהל בקונסולה", machine: "המסך של המכונה" };
const BRAIN_STATE_FILTERS = [["", "הכול"], ["answered", "נענו"], ["expired", "פקעו"], ["cancelled", "בוטלו"]];
const BRAIN_RANGES = [["d", "היום"], ["w", "7 ימים"], ["m", "30 ימים"]];

const brainLtr = (s) => esc(s).replace(/([A-Za-z][A-Za-z0-9]*(?:[-.\/ ][A-Za-z0-9]+)*)/g, '<bdi dir="ltr">$1</bdi>');
function brainWhen(ts) {
  if (!ts) return "—";
  const ms = tsMs(ts);
  if (Number.isFinite(ms) && isToday(ts)) return `היום ${fmtClock(ts)}`;
  if (Number.isFinite(ms) && isToday(new Date(ms + 86400000).toISOString())) return `אתמול ${fmtClock(ts)}`;
  return `${fmtDate(ts)} ${fmtClock(ts)}`;
}
const brainTtlText = (s) => (s >= 3600 && s % 3600 === 0 ? `${s / 3600} שעות` : `${Math.max(Math.round(s / 60), 1)} דקות`);
function brainAgo(ms) {
  const s = Math.max(Math.round(ms / 1000), 0);
  return s < 90 ? `${s} שניות` : `${Math.round(s / 60)} דקות`;
}
function brainWhoAnswered(a) {
  if (a.state !== "answered") return "";
  if (a.answered_role === "machine") {
    const m = (MACHINES || []).find((x) => x.mac === a.answered_by);
    return `המסך של ${(m && machineName(m)) || a.answered_by}`;
  }
  return `${a.answered_by} · מנהל`;
}
const brainStatePill = (a) => (a.state === "answered" ? UI.pill("ok", "נענתה") : a.state === "expired" ? UI.pill("warn", "פקעה") : UI.pill("", "בוטלה"));

/* ---------- לשונית "פתוחות" ---------- */
function brainOpenTab() {
  const list = BRAIN_ASKS.open.slice().sort((x, y) => brainLeft(x) - brainLeft(y));
  const hist = BRAIN_ASKS.history, today = hist && hist.today ? hist.today : null;
  if (!list.length) {
    return `<div class="c12 card"><div class="card-h"><span>שאלות פתוחות</span></div><div class="card-b"><div class="empty"><span class="eic"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16.5 9.5"/></svg></span>`
      + `<b class="et">אין שאלה שממתינה לתשובה</b><p>המוח שואל רק כשההחלטה צריכה להיות של אדם. כשתהיה שאלה היא תופיע כאן, בפעמון ובמסך של המכונה.</p>`
      + `<button class="btn" type="button" onclick="activateTab(1)">להיסטוריה</button></div></div></div>`;
  }
  const near = brainLeft(list[0]);
  const kpis = `<div class="c12 kpis">${UI.kpi({ cls: "warn", label: "ממתינות לתשובה", value: String(list.length) })}`
    + `<div class="kpi ${near <= BRAIN_LOW_S ? "err" : "warn"}"><div class="l">עד שהראשונה מתבצעת לבד</div><div class="v"><bdi dir="ltr" id="askNearest">${brainMmss(near)}</bdi></div></div>`
    + UI.kpi({ cls: "ok", label: "נענו היום", value: today ? String(today.answered) : "—" })
    + UI.kpi({ label: "פקעו בלי תשובה היום", value: today ? String(today.expired) : "—" }) + `</div>`;
  const rows = list.map((a) => ({ attrs: 'class="ba-q"', cells: [
    `<span class="ba-ttl">${brainLtr(a.title_he)}</span><span class="ba-sub">${brainLtr(brainAskWho(a))}</span>`,
    UI.pill("", BRAIN_KIND_HE[(a.entity || {}).kind] || "—"), brainCd(a), brainLtr(brainLabel(a, a.safe_default)),
    `<button class="btn primary sm" type="button" onclick="brainAskSheet(${Number(a.id)})">ענה</button>`] }));
  const table = UI.datagrid({ columns: ["שאלה", "על מה", "נשאר זמן", "ברירת המחדל", { html: `<span class="sr-only">פעולה</span>` }], rows });
  return `<div class="c12">${UI.note("info", "בלי תשובה עד תום הזמן מתבצעת <b>ברירת המחדל הבטוחה</b>, ואף פעם לא פעולה שמוחקת. אפשר לענות גם במסך של המכונה: תשובה אחת סוגרת את השאלה בשני המקומות.")}</div>`
    + kpis + UI.card({ title: "שאלות פתוחות", small: "הקרובה להיגמר ראשונה", body: table, flush: true });
}

/* ---------- לשונית "היסטוריה" ---------- */
function brainHistRows() {
  const items = (BRAIN_ASKS.history && BRAIN_ASKS.history.items) || [];
  const q = BRAIN_VIEW.q.trim().toLowerCase();
  return items.filter((a) => (BRAIN_VIEW.range !== "d" || isToday(a.closed_at))
    && (!q || [a.title_he, brainAskWho(a), a.answered_by, brainWhoAnswered(a), a.detail_he].join(" ").toLowerCase().includes(q)));
}
function brainResultHtml(a) {
  if (a.state === "answered") {
    const o = brainOpt(a, a.answer);
    return `<span class="ba-ttl">${brainLtr(a.answer_he || a.answer)}</span>${o && o.destructive ? `<span class="ba-sub">${UI.pill("err", "אפשרות הרסנית")}</span>` : ""}`;
  }
  const why = a.state === "cancelled" ? (a.closed_note_he || "הכלל ששאל משך אותה") : `עברה תקרת הזמן (${brainTtlText(a.ttl_s)})`;
  return `<span class="ba-ttl" style="font-weight:500">${brainLtr(a.safe_default_he)}</span><span class="ba-sub">ברירת המחדל — ${esc(why)}</span>`;
}
function brainHistTable() {
  const rows = brainHistRows(), hist = BRAIN_ASKS.history, total = hist ? hist.total : rows.length;
  const table = UI.datagrid({ columns: ["נשאלה", "שאלה", "על מה", "תוצאה", "מה בוצע", "מי ענה", "נסגרה"],
    empty: "אין שאלות לסינון הזה. נסו להסיר מסנן או לחפש ביטוי אחר.",
    rows: rows.map((a) => ({ attrs: `class="clk" tabindex="0" onclick="brainAskDrawer(${Number(a.id)})" onkeydown="if(event.key==='Enter')brainAskDrawer(${Number(a.id)})"`, cells: [
      `<span class="num">${esc(brainWhen(a.created_at))}</span>`,
      `<span class="ba-ttl">${brainLtr(a.title_he)}</span><span class="ba-sub">${brainLtr(brainAskWho(a))}</span>`,
      UI.pill("", BRAIN_KIND_HE[(a.entity || {}).kind] || "—"), brainStatePill(a), brainResultHtml(a),
      a.state === "answered" ? brainLtr(brainWhoAnswered(a)) : `<span class="muted">אף אחד — ברירת המחדל</span>`,
      `<span class="num">${esc(brainWhen(a.closed_at))}</span>`] })) });
  return `${table}<div class="ba-foot">מוצגות ${rows.length} מתוך ${total} · השרת שומר את 30 הימים האחרונים</div>`;
}
function brainHistBar() {
  const opt = (v, label, cur) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label)}</option>`;
  const seg = (items, cur, fn, label) => `<span class="seg" role="group" aria-label="${esc(label)}">${items.map(([v, t]) => `<button type="button" aria-pressed="${v === cur}" onclick="${fn}('${v}')">${esc(t)}</button>`).join("")}</span>`;
  const kinds = new Set(["machine", "image", BRAIN_VIEW.kind]);
  for (const a of (BRAIN_ASKS.history && BRAIN_ASKS.history.items) || []) if (a.entity && a.entity.kind) kinds.add(a.entity.kind);
  kinds.delete("");
  return `<div class="dg-bar"><input class="grow" id="askQ" type="search" placeholder="חיפוש בשאלות, במכונות ובמשתמשים" aria-label="חיפוש" value="${esc(BRAIN_VIEW.q)}" oninput="brainSearch(this.value)">`
    + seg(BRAIN_STATE_FILTERS, BRAIN_VIEW.state, "brainFilterState", "תוצאה")
    + `<select aria-label="מי ענה" onchange="brainFilter('who', this.value)">${opt("", "מי ענה: הכול", BRAIN_VIEW.who)}${opt("admin", "מנהל בקונסולה", BRAIN_VIEW.who)}${opt("machine", "המסך של המכונה", BRAIN_VIEW.who)}${opt("none", "אף אחד (ברירת המחדל)", BRAIN_VIEW.who)}</select>`
    + `<select aria-label="על מה" onchange="brainFilter('kind', this.value)">${opt("", "על מה: הכול", BRAIN_VIEW.kind)}${[...kinds].map((k) => opt(k, BRAIN_KIND_HE[k] || k, BRAIN_VIEW.kind)).join("")}</select>`
    + seg(BRAIN_RANGES, BRAIN_VIEW.range, "brainFilterRange", "טווח") + `</div>`;
}
function brainHistoryTab() {
  return UI.card({ title: "שאלות שנסגרו", body: `${brainHistBar()}<div id="askHistRows">${brainHistTable()}</div>`, flush: true });
}
function brainFilter(key, value) { BRAIN_VIEW[key] = value; loadBrainAsks(); }
function brainFilterState(v) { brainFilter("state", v); }
function brainFilterRange(v) { brainFilter("range", v); }
/* חיפוש: מצייר מחדש רק את הטבלה, כדי שהסמן יישאר בשדה */
function brainSearch(value) {
  BRAIN_VIEW.q = value;
  const box = document.getElementById("askHistRows");
  if (box && BRAIN_ASKS) box.innerHTML = brainHistTable();
}

/* ---------- הדף ---------- */
function brainAsksPage(tab = 0) {
  if (!BRAIN_ASKS && !BRAIN_ASKS_ERR) return pagePlaceholder();
  const crumbs = [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "שאלות המוח" }];
  const actions = `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  if (!BRAIN_ASKS) {   // לא נקרא אף פעם: "לא הצלחנו לקרוא", לא "אין שאלות" (עיקרון 5)
    const header = UI.objHeader({ crumbs, icon: "brain", name: "שאלות המוח", sub: "לא נקרא", pill: UI.pill("err", "לא נקרא"), actions, compact: true });
    return `<div class="page aligned">${header}<div class="body"><div class="c12">${UI.note("err", `לא הצלחנו לקרוא את השאלות מהשרת: ${esc(BRAIN_ASKS_ERR)}. ניסיון חוזר בעוד <bdi dir="ltr">10</bdi> שניות.`)}</div></div></div>`;
  }
  const n = BRAIN_ASKS.open.length, hist = BRAIN_ASKS.history;
  const sub = n ? `${n} ממתינות לתשובה · בלי תשובה מתבצעת ברירת המחדל הבטוחה` : "המוח שואל רק כשההחלטה צריכה להיות של אדם";
  const header = UI.objHeader({ crumbs, icon: "brain", name: "שאלות המוח", sub, pill: n ? UI.pill("warn", `${n} ממתינות`) : UI.pill("", "אין שאלות פתוחות"),
    actions, compact: true, tabs: [["פתוחות", String(n)], hist ? ["היסטוריה", String(hist.total)] : "היסטוריה"], tab });
  const stale = BRAIN_ASKS_ERR
    ? `<div class="c12">${UI.note("err", `לא הצלחנו לקרוא את השאלות מהשרת. מוצג מה שנקרא לפני ${brainAgo(Date.now() - BRAIN_ASKS_OK_AT)}, והרשימה אינה בהכרח עדכנית. ניסיון חוזר בעוד <bdi dir="ltr">10</bdi> שניות.`)}</div>` : "";
  const body = tab === 1 ? (hist ? brainHistoryTab() : UI.note("err", "ההיסטוריה לא נקראה: השרת לא החזיר אותה")) : brainOpenTab();
  return `<div class="page aligned">${header}<div class="body">${stale}${body}</div></div>`;
}

/* ---------- מגירת הפרטים של שאלה שנסגרה ---------- */
function brainAskDrawer(id) {
  const a = ((BRAIN_ASKS && BRAIN_ASKS.history && BRAIN_ASKS.history.items) || []).find((x) => x.id === id);
  if (!a) { toast("השאלה לא נמצאה"); return; }
  const def = brainLabel(a, a.safe_default), who = brainWhoAnswered(a), ans = brainOpt(a, a.answer);
  const q = (t) => `«${brainLtr(t)}»`;
  const ev = [`<div class="ev"><span class="t">${esc(brainWhen(a.created_at))}</span><span class="d"></span><span><b>המוח שאל</b><small class="ba-sub">תקרת המתנה ${esc(brainTtlText(a.ttl_s))}. בלי תשובה: ${q(def)}</small></span><span class="who"></span></div>`];
  const step = (title, when, note, cls = "") => ev.push(`<div class="ev"><span class="t">${esc(when)}</span><span class="d ${cls}"></span><span><b>${title}</b>${note ? `<small class="ba-sub">${note}</small>` : ""}</span><span class="who"></span></div>`);
  if (a.state === "answered") {
    step(`${brainLtr(who)} ענה`, brainWhen(a.closed_at), `${q(a.answer_he)}${ans && ans.destructive ? " · אפשרות הרסנית, בחירה של אדם" : ""}`);
    step("בוצע", "", q(a.answer_he), "ok");
  } else if (a.state === "expired") {
    step("תם הזמן בלי תשובה", brainWhen(a.closed_at), "לא נרשמה תשובה, ולא נחשב כאישור", "warn");
    step("בוצעה ברירת המחדל", "", q(def), "ok");
  } else {
    step("השאלה בוטלה", brainWhen(a.closed_at), esc(a.closed_note_he || ""));
    step("בוצעה ברירת המחדל", "", q(def), "ok");
  }
  const check = `<svg class="n-ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16.5 9.5"/></svg>`;
  const opts = `<ul class="ba-opt-list">${a.options.map((o) => `<li>${a.state === "answered" && o.id === a.answer ? check : `<span style="width:16px;flex:none"></span>`}<span>${brainLtr(o.label_he)}</span>`
    + `${o.id === a.safe_default ? UI.pill("", "ברירת המחדל") : ""}${a.state === "answered" && o.id === a.answer && o.destructive ? UI.pill("err", "הרסנית") : ""}</li>`).join("")}</ul>`;
  const chips = `${a.state === "answered" ? `<span class="n-chip">נענתה</span>` : a.state === "expired" ? `<span class="n-chip w">פקעה בלי תשובה</span>` : `<span class="n-chip n">בוטלה</span>`}<span class="n-chip n">${esc(BRAIN_KIND_HE[(a.entity || {}).kind] || "—")}</span>`;
  const allowed = (a.who || []).map((r) => BRAIN_WHO_HE[r] || r).join(" · ");
  openDrawer(a.title_he, `<div class="page drw"><div><div class="ba-sec" style="margin-top:0">מה קרה</div><div class="timeline">${ev.join("")}</div></div>`
    + `<div><div class="ba-sec">האפשרויות</div>${opts}</div>`
    + `<div><div class="ba-sec">פרטים</div>${UI.kv([["מספר שאלה", `<span class="mono">${Number(a.id)}</span>`], ["נשאלה", esc(brainWhen(a.created_at))], ["נסגרה", esc(brainWhen(a.closed_at))], ["מי רשאי לענות", esc(allowed)]])}</div>`
    + `<div class="acts"><button class="btn primary" type="button" onclick="closeDrawer()">סגירה</button></div></div>`, { sub: brainAskWho(a), meta: chips });
  brainDrawerChrome("סקירה", "נשמר ביומן ההחלטות של המוח");
}
/* המגירה הכללית מציירת לשוניות רק כשיש בה כמה חלקים; במוקאפ יש לשונית אחת ("סקירה") ושורת הערה בתחתית. */
function brainDrawerChrome(tab, hint) {
  const tabs = document.getElementById("drawerTabs"), foot = document.getElementById("drawerFoot");
  if (tabs && !tabs.children.length) { tabs.hidden = false; tabs.innerHTML = `<button type="button" class="n-tab" role="tab" aria-selected="true">${esc(tab)}</button>`; }
  if (foot && hint && !foot.querySelector(".ba-hint")) foot.insertAdjacentHTML("afterbegin", `<span class="n-hint ba-hint">${esc(hint)}</span>`);
}
