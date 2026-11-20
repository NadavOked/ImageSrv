/* ImageSrv -- #3185: הזיכרון ארוך-הטווח של המוח, בדפים הקיימים של הישויות (מוקאפ שנדב אישר ב-14/11).
   הכלל של נדב: כל ידע נמצא בדף של הישות שלו -- דיסק בדף "דיסקים", מחשב במגירת המחשב, סבב בהיסטוריית הסבבים --
   והיומן הוא אירועים בלבד. הקובץ בונה רק את התוכן; המספרים מחושבים בשרת (server/brain/memory_view.py,
   server/disks/disk_card.py) ועוברים כאן כמו שהם: סיכום ושורות חיות הם ספירה אחת.
   "לא נקרא" ו"לא נמדד" אינם 0 (עיקרון 5): שדה null מוצג במילים. משתמש ב-UI/api/esc/ltr/fmtDate מ-console.js. */
"use strict";

const MEM = {};   // נתיב -> {loading} | {error} | התשובה. נטען מחדש בכל פתיחה של כרטיס.
function memLoad(path, rerender) {
  MEM[path] = { loading: true };
  api(path).then((v) => { MEM[path] = v; }, (e) => { MEM[path] = { error: e.message }; }).then(rerender);
}
function memGet(path) { const v = MEM[path]; return v && !v.loading && !v.error ? v : null; }
function memError(path) { const v = MEM[path]; return v && v.error ? v.error : ""; }

const memGrp = (title, body) => `<div class="mgrp"><div class="grp">${title}</div>${body}</div>`;
const memCap = (html) => `<div class="cap">${html}</div>`;
const memNone = (text) => `<span class="muted">${esc(text)}</span>`;
function memRounds(n) { return n === 1 ? "סבב אחד" : `${n} סבבים`; }
/* "12/11" בשנה הנוכחית, "12/11/2025" אחרת -- כך שהעמודה הצרה של ציר הזמן אינה נשברת. */
function memDay(ts) {
  const full = fmtDate(ts);
  return full === "—" || !full.endsWith("/" + new Date().getFullYear()) ? full : full.slice(0, 5);
}
/* מספר עם יחידה: יחידה לטינית נשארת איתו בתוך ה-bdi; יחידה עברית ("ש'") מחוץ לו, אחרת היא מתהפכת ("'ש 41"). */
function memNum(v, unit = "") {
  if (v == null) return memNone("לא נמדד");
  if (!unit) return ltr(String(v));
  return /[֐-׿]/.test(unit) ? `${ltr(String(v))} ${esc(unit)}` : ltr(`${v} ${unit}`);
}
const memGbps = (v) => `${Number(v).toFixed(1)}`;

/* ---------------------------------------------------------------- החלטות המוח */
/* מה יצא מההחלטה. תוצאה שלא ידועה אינה "בוצע": `open` (עוד לא נסגרה), `unknown` (נסגרה בלי ראיה: השרת הופעל מחדש). */
function memOutcomePill(d) {
  if (d.outcome_state === "open") return UI.pill("info", "עוד פתוחה");
  if (d.outcome_state === "unknown") return UI.pill("", "תוצאה לא ידועה: השרת הופעל מחדש");
  return d.outcome ? UI.pill("", d.outcome) : UI.pill("", "תוצאה לא ידועה");
}
function memDecisionEvents(decisions) {
  return (decisions || []).map((d) => ({ at: d.at, t: memDay(d.at), cls: d.outcome_state === "open" ? "info" : "",
    text: `${esc(d.line_he || d.reason)} ${memOutcomePill(d)}`, who: "המוח" }));   // #3320 (R1): השורה בעברית מהשרת
}
const memByTimeDesc = (a, b) => (Date.parse(b.at) || 0) - (Date.parse(a.at) || 0);

/* ---------------------------------------------------------------- דף "דיסקים": עמודת "מגמה" וכרטיס דיסק */
function dkCardPath(serial) { return `/disks/${encodeURIComponent(serial)}/card`; }
/* לחיצה על שורה פותחת כרטיס; לחיצה על תיבת הסימון, הכפתור או הקישור שבשורה לא. */
function dkRowClick(event, serial) {
  if (event && event.target && event.target.closest && event.target.closest("input,button,a,label,select")) return;
  dkOpenCard(serial);
}
function dkOpenCard(serial) {
  const d = (DK.disks || []).find((x) => x.serial === serial);
  if (!d) return;
  openDrawer(serial, dkCardHtml(d, null));
  const gen = drawerGeneration;
  memLoad(dkCardPath(serial), () => { if (gen === drawerGeneration) setDrawerBody(dkCardHtml(d, memGet(dkCardPath(serial)))); });
}
function dkAssignOne(serial) {
  DK.picked = new Set([serial]);
  closeDrawer();
  diskAssignPickedSheet();
}
function dkOpenMachine(mac) { closeDrawer(); openMachineDetail(encodeURIComponent(mac)); }

function dkCardChips(d) {
  const [, text] = DK_STATE[d.state] || DK_STATE.unchecked, cls = { red: "d", orange: "w" }[d.state] || "n";
  return `<span class="n-meta"><span class="n-chip ${cls}">${esc(text)}</span><span class="n-chip n">${d.label ? esc(d.label) : "בלי סט"}</span></span>`;
}
function dkCardNote(d) {
  const f1 = d.f1 ? "נצפה F1 בהדלקה" : "";
  const why = f1 || (d.reason ? smartReasonHe(d.reason) : "");
  if (d.state !== "red" && d.state !== "orange") return "";
  return UI.note(d.state === "red" ? "err" : "warn", `<b>${esc((DK_STATE[d.state] || [])[1])}</b>${why ? `: ${esc(why)}` : ""}`);
}
function dkCardNow(d) {
  const where = d.in_machine && d.machine ? `${esc(d.machine)}${d.port != null ? ` · מגירה ${esc(d.port)}` : ""} ${UI.link("פתח", `dkOpenMachine(${jsStr(d.mac)})`)}` : dkWhere(d);
  return UI.kv([["איפה עכשיו", where], ["סט", d.label ? esc(d.label) : memNone("בלי סט")], ["כתיבה תקינה אחרונה", dkLastGood(d)]]);
}
function dkCardKnown(c) {
  const r = c.rounds, w = c.write, link = c.link;
  const since = c.first_seen ? ` <small>${esc(memRounds(r.total))}, מאז ${esc(fmtDate(c.first_seen))}</small>` : ` <small>${esc(memRounds(r.total))}</small>`;
  const speed = w.last_mbps == null ? memNone("לא נמדד")
    : `${ltr(`${w.last_mbps} MB/s`)} בסבב האחרון${w.model_avg_mbps ? UI.barRow(Math.round(100 * w.last_mbps / w.model_avg_mbps), 100 * w.last_mbps / w.model_avg_mbps < 70 ? "warn" : "")
      + memCap(`${Math.round(100 * w.last_mbps / w.model_avg_mbps)}% מהממוצע של הדגם: ${ltr(`${w.model_avg_mbps} MB/s`)}, לפי ${w.model_n} מדידות`) : ""}`;
  const connection = link.last_gbps == null ? memNone("לא נקרא")
    : `${ltr(`${memGbps(link.last_gbps)} Gb/s`)}${link.max_gbps != null ? ` מתוך ${ltr(memGbps(link.max_gbps))}` : ""} · ${link.crc_total == null ? "CRC לא נמדד" : link.crc_total === 0 ? "CRC לא עלה" : `CRC עלה ב-${link.crc_total}`}`
      + `${link.cable_fails ? ` · כשלי כבל: ${link.cable_fails}` : ""} · ${esc(memRounds(link.rounds))}`;
  return memGrp(`מה השרת יודע על הדיסק${since}`, UI.kv([
    ["נראה לראשונה", c.first_seen ? esc(fmtDate(c.first_seen)) : memNone("לא נרשם")],
    ["סבבים שנכתב בהם", r.total ? `${r.total} ${memNone(`· ${r.detailed} אחרונים בפירוט, ${r.summarized} בסיכום`)}` : memNone("עוד לא נכתב בסבב")],
    ["כשלי כתיבה", w.failures ? `${w.failures} <span class="muted">· האחרון ${esc(memDay(w.failures_last_at))}</span>` : `אין ${memNone(`· ב-${memRounds(r.total)}`)}`],
    ["מהירות כתיבה", speed], ["חיבור", connection]]));
}
function dkCardDecisions(c) {
  const body = c.decisions.length ? UI.timeline(memDecisionEvents(c.decisions)) + memCap("כאן: מה הוחלט ומה יצא מזה. הראיות המלאות נשארות במוח.")
    : `<div class="muted">המוח עוד לא החליט דבר על הדיסק הזה.</div>`;
  return memGrp(`החלטות המוח על הדיסק${c.decisions.length ? ` <small>${c.decisions.length} אחרונות</small>` : ""}`, body);
}

/* הגרף של מונה אחד לאורך הסבבים. הציר מתחיל באפס; בלי שתי נקודות אין קו -- הודעה, לא ציור ריק. */
function dkCardChart(rounds, counter) {
  const pts = rounds.filter((r) => r[counter] != null);
  if (pts.length < 2) return UI.note("info", "אין עדיין גרף: פחות משתי מדידות SMART בפירוט.");
  const top = Math.max(...pts.map((r) => r[counter]), 1), x = (i) => 40 + (338 * i) / (pts.length - 1), y = (v) => 84 - (70 * v) / top;
  const line = pts.map((r, i) => `${x(i).toFixed(0)},${y(r[counter]).toFixed(0)}`).join(" ");
  const dots = pts.map((r, i) => `<circle cx="${x(i).toFixed(0)}" cy="${y(r[counter]).toFixed(0)}" r="3"/>`).join("");
  const rose = pts[pts.length - 1][counter] > pts[0][counter], ink = rose ? "var(--warn-ink)" : "var(--muted)";
  const label = `${DK_COUNTER_HE[counter]} בסבבים האחרונים: ${pts.map((r) => r[counter]).join(", ")}`;
  return `<svg viewBox="0 0 400 96" width="100%" height="96" role="img" aria-label="${esc(label)}" dir="ltr">`
    + `<line x1="8" y1="84" x2="392" y2="84" stroke="var(--hair)"/><line x1="8" y1="14" x2="392" y2="14" stroke="var(--hair)"/>`
    + `<polyline fill="none" stroke="${ink}" stroke-width="2" points="${line}"/><g fill="${ink}">${dots}</g>`
    + `<g fill="var(--muted)" font-size="11"><text x="8" y="10">${top}</text><text x="8" y="80">0</text>`
    + `<text x="40" y="95" text-anchor="middle">${esc(memDay(pts[0].at))}</text><text x="378" y="95" text-anchor="middle">${esc(memDay(pts[pts.length - 1].at))}</text></g></svg>`;
}
function dkCardSmart(c, d) {
  /* רק סבבים שנקרא בהם SMART: סבב שרק הכתיבה שלו עוד בטבלה אינו שורה של "לא נמדד" בארבעה טורים. */
  const rounds = c.smart.rounds.filter((r) => ["reallocated", "pending", "media_errors", "temp"].some((k) => r[k] != null)).reverse();   // מהישן לחדש
  const counter = c.trend.counter || (rounds.some((r) => r.reallocated != null) ? "reallocated" : rounds.some((r) => r.media_errors != null) ? "media_errors" : "reallocated");
  const delta = (i, field) => {
    const prev = rounds[i - 1], cur = rounds[i];
    return prev && prev[field] != null && cur[field] != null && cur[field] > prev[field] ? ` <span class="pill warn"><bdi dir="ltr">+${cur[field] - prev[field]}</bdi></span>` : "";
  };
  const cell = (i, field) => rounds[i][field] == null ? memNone("לא נמדד") : (i === rounds.length - 1 && delta(i, field) ? `<b>${rounds[i][field]}</b>` : String(rounds[i][field])) + delta(i, field);
  const old = c.smart.older, rows = [];
  if (old) rows.push([`ישנים <span class="sub">${esc(memRounds(old.rounds))}, מקסימום</span>`, memNum(old.reallocated), memNum(old.pending),
    old.temp == null ? memNone("לא נמדד") : `${old.temp}°`, "—"]);
  rounds.forEach((r, i) => rows.push([esc(memDay(r.at)), cell(i, "reallocated"), cell(i, "pending"), r.temp == null ? memNone("לא נמדד") : `${r.temp}°`, r.mbps == null ? memNone("לא נמדד") : String(r.mbps)]));
  const grid = rows.length ? UI.datagrid({ columns: ["סבב", "מוחלפים", "ממתינים", "טמפ'", "MB/s"], rows }) : `<div class="muted">אין עדיין מדידות SMART לדיסק הזה.</div>`;
  const more = d.in_machine && d.mac ? ` כל מאפייני ה-SMART של הסבב האחרון: ${UI.link("בכרטיס המחשב", `dkOpenMachine(${jsStr(d.mac)})`)}.` : "";
  return `<div><div class="sec">SMART לאורך זמן</div>${memGrp(`${esc(DK_COUNTER_HE[counter])} <small>הציר מתחיל באפס</small>`, dkCardChart(rounds, counter))}`
    + memGrp("סבב אחרי סבב", grid + memCap(`מוחלפים = מאפיין 5, ממתינים = מאפיין 197, טמפ' = מיד אחרי הכתיבה. סבבים שיצאו מהחלון בסיכום. ערך שלא נמדד מוצג "לא נמדד", לא 0.${more}`)) + `</div>`;
}
function dkCardWrites(c) {
  const w = c.write, pct = (r) => (r.pct_of_model == null ? memNone("לא נמדד") : UI.pill(r.pct_of_model < 70 ? "warn" : "", `${r.pct_of_model}%`));
  const rows = c.smart.rounds.filter((r) => r.mbps != null).map((r) => [esc(memDay(r.at)),
    r.machine ? `${esc(r.machine)}${r.port != null ? ` · מגירה ${esc(r.port)}` : ""}` : memNone("לא נרשם"), r.mbps == null ? memNone("לא נמדד") : String(r.mbps), pct(r)]);
  const older = w.older ? ` ${esc(memRounds(w.older.rounds))} ישנים בסיכום: ממוצע ${w.older.avg ?? "—"}, הנמוך ${w.older.min ?? "—"}.` : "";
  const l = c.link;
  return `<div><div class="sec">כתיבות</div>`
    + memGrp("הסבבים האחרונים", (rows.length ? UI.datagrid({ columns: ["סבב", "איפה", "MB/s", "מול הדגם"], rows }) : `<div class="muted">עוד לא נכתב בסבב.</div>`)
      + memCap(`מקור: בדיקת הכתיבה שלפני הזרם.${older}`))
    + memGrp("החיבור", UI.kv([["מהירות החיבור", l.last_gbps == null ? memNone("לא נקרא") : `${ltr(`${memGbps(l.last_gbps)} Gb/s`)}${l.max_gbps != null ? ` מתוך ${ltr(memGbps(l.max_gbps))}` : ""} · ${esc(memRounds(l.rounds))}`],
      ["CRC", l.crc_total == null ? memNone("לא נמדד") : l.crc_total === 0 ? `לא עלה ב-${esc(memRounds(l.rounds))}` : `עלה ב-${l.crc_total}`],
      ["כשלי כבל", l.cable_fails == null ? memNone("לא נמדד") : l.cable_fails ? String(l.cable_fails) : "אין"]])) + `</div>`;
}
function dkCardHistory(c) {
  const where = (r) => [r.machine, r.port != null ? `מגירה ${r.port}` : ""].filter(Boolean).join(" · ");
  const ev = c.smart.rounds.filter((r) => r.written).map((r) => ({ at: r.at, t: memDay(r.at), cls: "", text: `נכתב בסבב${where(r) ? ` · ${esc(where(r))}` : ""}`, who: "" }))
    .concat(c.failures.map((f) => ({ at: f.at, t: memDay(f.at), cls: "err", text: `כשל כתיבה (${esc(f.cause)})${where(f) ? ` · ${esc(where(f))}` : ""}`, who: "" })))
    .sort(memByTimeDesc);
  if (c.first_seen) ev.push({ at: c.first_seen, t: memDay(c.first_seen), cls: "", text: "נראה לראשונה", who: "" });
  return `<div><div class="sec" data-cnt="${ev.length || ""}">היסטוריה</div>${ev.length ? UI.timeline(ev) : `<div class="muted">אין אירועים.</div>`}`
    + memCap(`אירועים בלבד. הידע נמצא בלשוניות האחרות. ${UI.link("כל האירועים ביומן", "closeDrawer();selectPageById('logs')")}`) + `</div>`;
}
/* card = null: עוד נטען (הסקירה מוצגת מהשורה עצמה), או שנכשל -- ואז אומרים זאת, ולא מציגים כרטיס ריק. */
function dkCardHtml(d, card) {
  const path = dkCardPath(d.serial), err = memError(path);
  const sub = `${d.size_bytes ? `${dkSize(d.size_bytes)} · ` : ""}${esc(d.model || "—")}${dkCardChips(d)}`;
  const acts = isAdmin() ? `<div class="acts"><button class="btn" onclick="dkAssignOne(${jsStr(d.serial)})">שייך לסט…</button>`
    + `${d.in_machine && d.mac ? `<button class="btn primary" onclick="dkOpenMachine(${jsStr(d.mac)})">פתח את ${esc(d.machine || "המחשב")}</button>` : ""}</div>` : "";
  const known = card ? dkCardKnown(card) + dkCardDecisions(card)
    : err ? UI.note("warn", `מה שהשרת יודע על הדיסק לא נקרא: ${esc(err)}`) : `<div class="muted">טוען…</div>`;
  return `<div class="page drw"><div class="obj-sub">${sub}</div>${acts}${dkCardNote(d)}<div><div class="sec">עכשיו</div>${dkCardNow(d)}</div>${known}`
    + (card ? dkCardSmart(card, d) + dkCardWrites(card) + dkCardHistory(card) : "") + `</div>`;
}

/* ---------------------------------------------------------------- מגירת מחשב */
function memMachinePath(mac) { return `/machines/${encodeURIComponent(mac)}/memory`; }
function memMachine(mac) { return memGet(memMachinePath(mac)); }
function memMachineError(mac) { return memError(memMachinePath(mac)); }
function memMachineTitle(mem) { return `נתונים שנאספו${mem && mem.since ? ` <small>מאז ${esc(fmtDate(mem.since))}</small>` : ""}`; }
/* "5 הדגימות האחרונות": הגולמי נשאר, מקופל מאחורי הסיכום. */
const memFold = (label, raw) => `<details class="tech"><summary>${esc(label)}</summary>${raw}</details>`;

function memNicCell(mem, raw) {
  const n = mem && mem.nic;
  if (!n) return raw;
  const s = n.speed;
  const head = !s ? memNone("מהירות הקישור לא נמדדה")
    : s.min < s.max ? `${ltr(`${s.last} Mb/s`)} עכשיו. ירד ל-${ltr(`${s.min} Mb/s`)}${s.low_at ? ` ב-${esc(memDay(s.low_at))}` : ""} · ${esc(n.samples)} דגימות`
    : `${ltr(`${s.last} Mb/s`)} בכל ${n.samples} הדגימות`;
  const crc = n.crc && n.crc.last > n.crc.first ? memCap(`CRC עלה מ-${n.crc.first} ל-${n.crc.last}`) : n.crc ? memCap(`CRC לא עלה (${n.crc.last})`) : memCap("CRC לא נמדד");
  return head + crc + memFold("5 הדגימות האחרונות", raw);
}
function memDropsCell(mem, raw) {
  const d = mem && mem.drops;
  if (!d) return raw;
  const part = (f, label) => (d[f].read === 0 ? `${label}: לא נקרא` : `${label}: ${d[f].sum}`);
  const clean = d.rcvbuf.read > 0 && d.rcvbuf.sum === 0;
  const waves = d.waves === 1 ? "גל אחד" : `${d.waves} גלים`;
  const head = d.rcvbuf.read === 0 ? "זריקות buffer לא נקראו" : clean ? `אין זריקות ב-${waves}` : `זריקות buffer: ${d.rcvbuf.sum} ב-${waves}`;
  const tail = ` · ${part("inerrors", "שגיאות קלט")}${d.abandoned.read ? ` · ${part("abandoned", "ויתר")}` : ""}`;
  return `${head}${esc(tail)}${memCap(`נקרא ב-${d.rcvbuf.read} מתוך ${d.waves} גלים`)}${memFold("4 הגלים האחרונים", raw)}`;
}
/* שורות חדשות בקבוצה: WoL, שלב האתחול האיטי, גרסאות חומרה. אין נתונים = "לא נאסף", לא 0. */
function memMachineRows(mem) {
  if (!mem) return [];
  const w = mem.wol, b = mem.boot, h = mem.hardware;
  return [
    ["שלב האתחול האיטי ביותר", b ? `<span class="mono">${esc(b.slowest.step)}</span> · ממוצע ${memNum(b.slowest.avg_s, "ש'")} · הכי איטי ${memNum(b.slowest.max_s, "ש'")}${memCap(`${b.transitions} מעברים נמדדו, ב-${b.steps} שלבים`)}` : memNone("לא נמדד")],
    ["הדלקות מרחוק (WoL)", w ? `נענה ב-${w.answered ?? 0} מתוך ${w.sent} · ${w.avg_s == null ? "זמן לא נמדד" : `ממוצע ${memNum(w.avg_s, "ש'")}`}${w.resent ? ` · שליחה חוזרת: ${w.resent}` : " · בלי שליחה חוזרת"}` : memNone("עוד לא נשלחה הדלקה")],
    ["גרסאות חומרה", h ? `${h.versions} ${h.versions === 1 ? "גרסה נרשמה" : "גרסאות נרשמו"} · האחרונה ב-${esc(memDay(h.last_at))}` : memNone("לא נרשמה חומרה")]];
}

/* "מה היה בחריצים": שורה לכל חריץ, בלשונית "דיסקים". החריץ אינו הדיסק: דיסק שהוחלף משאיר את ההיסטוריה של החריץ. */
function memSlotLine(m, s) {
  const bad = [];
  if (s.cable_fails) bad.push(`כשלי כבל: ${s.cable_fails}`);
  if (s.crc) bad.push(`CRC עלה ב-${s.crc}`);
  if (s.failures) bad.push(`כשלי כתיבה: ${s.failures}`);
  const t = s.disk && s.disk.trend;
  const rose = t && t.state === "rose" ? `<b>${esc(DK_COUNTER_HE[t.counter] || "מונה SMART")} עלו</b>${memCap(`הדיסק הנוכחי בחריץ מאז ${esc(fmtDate(s.disk.first_seen))}`)}` : "";
  if (!s.rounds && !s.failures && !rose) return [esc(portLabel(m, s.port)), memNone("עוד לא נרשם בו סבב")];
  const verdict = bad.length || rose ? `${esc(bad.join(" · "))}${bad.length && rose ? "<br>" : ""}${rose}` : `אין תקלות${s.rounds ? ` ${memNone(`· ${memRounds(s.rounds)}`)}` : ""}`;
  return [esc(portLabel(m, s.port)), verdict];
}
function memSlotsHtml(m) {
  const mem = memMachine(m.mac);
  if (!mem || !mem.slots.length) return "";
  const since = mem.slots.map((s) => s.since).filter(Boolean).sort()[0];
  return `<div class="mem-slots">${memGrp(`מה היה בחריצים${since ? ` <small>מאז ${esc(fmtDate(since))}</small>` : ""}`, UI.kv(mem.slots.map((s) => memSlotLine(m, s))))}`
    + `${memCap("מקור: חיבורי ה-SATA בכל סבב וכשלי כתיבה. החריץ אינו הדיסק: דיסק שהוחלף משאיר את ההיסטוריה של החריץ.")}</div>`;
}
/* ציר הזמן של המחשב: היומן + החלטות המוח, החדש ראשון. מוחזר ככלי UI.timeline. */
function memHistoryEvents(journalEvents, mem) {
  return journalEvents.concat(memDecisionEvents(mem && mem.decisions)).sort(memByTimeDesc)
    .map(({ t, cls, text, who }) => ({ t, cls, text, who }));
}

/* ---------------------------------------------------------------- היסטוריית הסבבים */
let MEM_OPEN_ROUND = null;
function memRoundPath(id) { return `/room/history/${encodeURIComponent(id)}/summary`; }
function memRateText(bps) { return bps == null ? "" : `${Math.round(bps / 1e6)} Mbps`; }
/* "קצב ואיבוד": הקצב של הגל האחרון ששרד, ובלוקים שאבדו. גל ש-wave_rates כבר לא זוכר = "לא נשמר" (לא 0). */
function memFlowCell(flow) {
  if (!flow) return memNone("לא נקרא");
  if (!flow.kept) return `<span class="muted" title="הגל נמחק מהזיכרון הקצר, ואין לו סיכום">לא נשמר</span>`;
  const rate = flow.rate_bps == null ? memNone("קצב לא נמדד") : `<span class="mono">${esc(memRateText(flow.rate_bps))}</span>`;
  const lost = flow.loss_blocks == null ? "איבוד לא נמדד" : flow.loss_blocks === 0 ? "בלי איבוד" : `איבוד ${flow.loss_blocks} בלוקים`;
  return `${rate}<span class="sub">${esc(lost)}</span>`;
}
function memRoundToggle(id) {
  MEM_OPEN_ROUND = MEM_OPEN_ROUND === id ? null : id;
  if (MEM_OPEN_ROUND) memLoad(memRoundPath(id), () => { if (MEM_OPEN_ROUND === id && current === "deploy") renderCurrent(); });
  if (current === "deploy") renderCurrent();
}
function memRoundRowAttrs(h) {
  const open = MEM_OPEN_ROUND === h.id;
  return `class="clickable${open ? " row-selected" : ""}" tabindex="0" role="button" aria-expanded="${open}" `
    + `onclick="memRoundToggle(${jsStr(h.id)})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();memRoundToggle(${jsStr(h.id)})}"`;
}
function memRoundDetail(h, cols) {
  const s = memGet(memRoundPath(h.id)), err = memError(memRoundPath(h.id));
  const when = `${esc(fmtDate(h.created_at))}`;
  const head = `${esc(h.image_name || h.image_id || "")} · ${when}`;
  let body;
  if (err) body = UI.note("warn", `סיכום הסבב לא נקרא: ${esc(err)}`);
  else if (!s) body = `<div class="muted">טוען…</div>`;
  else {
    const kept = s.waves.filter((w) => w.kept), w = kept[kept.length - 1];
    const wave = s.waves.length === 0 ? UI.note("info", "לא נמצא גל לסבב הזה.")
      : s.waves.length === 1 ? UI.kv([["קצב שנבחר", w && w.rate_bps != null ? ltr(memRateText(w.rate_bps)) : memNone(w ? "לא נמדד" : "לא נשמר")],
        ["נשלחו", w && w.sent_bytes != null ? ltr(fmtBytes(w.sent_bytes)) : memNone(w ? "לא נמדד" : "לא נשמר")],
        ["בלוקים שאבדו", w && w.loss_blocks != null ? (w.loss_blocks ? `<b>${w.loss_blocks}</b> · שודרו מחדש` : "אין") : memNone(w ? "לא נמדד" : "לא נשמר")],
        ["זמן הסבב", esc(roomRoundMinutes(h))]])
      : UI.datagrid({ columns: ["גל", "קצב", "נשלחו", "בלוקים שאבדו"], rows: s.waves.map((x, i) => [String(i + 1), x.kept && x.rate_bps != null ? ltr(memRateText(x.rate_bps)) : memNone(x.kept ? "לא נמדד" : "לא נשמר"),
        x.kept && x.sent_bytes != null ? ltr(fmtBytes(x.sent_bytes)) : memNone("—"), x.kept && x.loss_blocks != null ? String(x.loss_blocks) : memNone(x.kept ? "לא נמדד" : "לא נשמר")]) });
    const n = (v) => (v == null ? memNone("לא נקרא") : String(v));
    const receivers = s.receivers.length ? UI.datagrid({ columns: ["משכפל", "זריקות buffer", "שגיאות קלט", "ויתר"], rows: s.receivers.map((r) => [esc(r.machine || r.mac), n(r.rcvbuf), r.inerrors ? `<b>${r.inerrors}</b>` : n(r.inerrors), n(r.abandoned)]) })
      + memCap("זריקות ושגיאות קלט בסוף הגל. לא נקרא מוצג כך, לא 0.") : `<div class="muted">אין נתוני מקלטים לסבב הזה.</div>`;
    const drives = s.drives.length ? UI.datagrid({ columns: ["מגירה", "דיסק", "דגם", "MB/s", "תוצאה"], rows: s.drives.map((x) => [`${esc(x.machine || x.mac)}${x.port != null ? ` · ${esc(x.port)}` : ""}`,
      `<span class="mono">${esc(x.serial)}</span>`, esc(x.model || "—"), x.mbps == null ? memNone("לא נמדד") : String(x.mbps),
      (x.state === "done" ? UI.pill("ok", "נכתב") : x.state ? UI.pill("warn", "לא נכתב") : UI.pill("", "לא ידוע")) + (x.pct_of_model != null ? `<span class="sub">${x.pct_of_model}% מהממוצע של הדגם</span>` : "")]) })
      : `<div class="muted">אין דיסקים רשומים לסבב הזה.</div>`;
    const dec = s.decisions.length ? UI.timeline(memDecisionEvents(s.decisions)) : `<div class="muted">המוח לא החליט דבר בסבב הזה.</div>`;
    body = `<div class="detail-grid"><div><div class="section-title">הגל</div>${wave}</div><div><div class="section-title">מקלטים</div>${receivers}</div></div>`
      + `<div class="section-title">דיסקים בסבב</div>${drives}<div class="section-title">החלטות המוח בסבב</div>${dec}`;
  }
  return `<tr class="rd"><td colspan="${cols}"><div class="mgrp"><div class="grp">${head}</div>${body}</div></td></tr>`;
}
