/* ‏#3181 (נדב 14/11): פס ייצור בקונסולה — עיצוב r1 המאושר (handoffs/production-line-mockup-1114, r1-live):
   פס "נכתבו X מתוך Y", טבלה לפי משכפל ומגירה, ובצדה "מוכן להוצאה" ו"החלטות המוח". הכול מ-/room
   (`round.production_line` + ‏`machines[].drawer_list`). הוראת נדב, ובלי שינוי מעבר לה:
   - דיסק תקין לפני הסבב: השורה ניטרלית, ועליה תג קטן "SMART תקין". צבע מלא לשורה רק ל"אפשר להוציא" ול"הושלם".
   - כל תג מצב בגודל של "SMART תקין" (‏.pl-tag = המידות של ‏.lb-chip).
   פס ייצור = יותר דיסקים ממגירות (`prodline.is_line` בשרת; ‏plIsLine כאן רק לחלון הפתיחה). אין מתג. */

/* מספר המגירות של המשכפלים שסומנו — אותו כלל של `prodline.drawers`: מגירות ה-SATA במיפוי, ולפחות מה שסומן */
function plDrawers(kids) {
  const by = new Map();
  roomSelected(kids).forEach((x) => by.set(x.m.mac, (by.get(x.m.mac) || 0) + 1));
  let total = 0;
  for (const [mac, picked] of by) {
    const rm = roomMachine(mac), slots = rm && Array.isArray(rm.drawer_slots) ? rm.drawer_slots : [];
    total += Math.max(slots.filter((k) => /^sata:\d+$/.test(String(k))).length, picked);
  }
  return total;
}
function plIsLine(kids) { return nmTarget(kids) > plDrawers(kids); }

const PL_TONE = { green: "ok", orange: "warn", red: "bad", yellow: "yel", grey: "mut" };
function plTag(tone, text) { return `<span class="pl-tag t-${tone}"><i></i>${esc(text)}</span>`; }
function plAgo(ts) {
  const ms = Date.parse(ts || ""), s = Number.isFinite(ms) ? Math.max(0, Math.round((Date.now() - ms) / 1000)) : null;
  return s == null ? "" : s < 60 ? `לפני ${s} שנ'` : `לפני ${Math.round(s / 60)} דק'`;
}
/* השורה של פס הייצור לחריץ הזה: לפי ה-serial שבחריץ, ובחריץ ריק — השם ששמור לו ("הכנס דיסק") */
function plDiskOf(pl, m, s) {
  const d = s.disk;
  if (d && d.serial) { const row = pl.disks.find((x) => x.serial === d.serial); if (row) return row; }
  const port = d && d.port != null ? Number(d.port) : /^sata:\d+$/.test(s.key || "") ? Number(s.key.slice(5)) : null;
  return pl.disks.find((x) => x.mac === m.mac && x.port === port && (d ? x.state !== "removed" : x.state === "reserved")) || null;
}
/* ‏#3064 (R5, המוקאפ המאושר: התג "מצטרף ומשלים" והשורה "חסרים N%"): מגירה שחזרה ועוד משלימה. ‏`rejoin` מהשרת (`room._rejoin`);
   אחוז רק כשיש `chunks` ו-`missing` אינו גדול ממנו — אחרת רק המספר ("לא נמדד" אינו 0%). המקור מ-`step` של הסוכן, ואם לא נאמר — בלעדיו */
function plRejoinNote(rj) {
  const pct = rj.chunks > 0 && rj.missing <= rj.chunks ? ` (${Math.round(100 * rj.missing / rj.chunks)}% מהמחיצה)` : "";
  const part = rj.part != null ? `משלים מחיצה ${ltr(String(rj.part))} · ` : "משלים · ";
  const src = rj.step === "neighbor" ? " · ממגירה שכנה" : rj.step === "http" ? " · מהשרת (HTTP)" : "";
  return `${part}חסרות ${ltr(rj.missing.toLocaleString("he-IL"))} חתיכות${pct}${src}`;
}
/* מצב השורה: [תגים, שורה צבועה?, התקדמות, הערה] */
function plRow(r, pl, m, s) {
  const d = s.disk, row = plDiskOf(pl, m, s), live = !!(d && s.live && d.state);
  const pct = live && d.bytes_total > 0 ? Math.min(100, Math.round(100 * (d.bytes_written || 0) / d.bytes_total)) : d && d.state === "done" ? 100 : null;
  const bar = pct == null ? "" : `<span class="mono">${pct}%</span><span class="pl-mini"><i style="width:${pct}%"></i></span>`;
  if (!d) {
    if (row && row.state === "reserved") return [plTag("mut", "ממתין לדיסק"), false, "", row.name ? `הבא: ${ltr(row.name)}` : "הכנס דיסק"];
    return [plTag("mut", "ריקה"), false, "", ""];
  }
  const [ctone, ctext] = bayChip(s), health = ctone === "green" ? "" : plTag(PL_TONE[ctone] || "mut", ctext);
  if (row && row.state === "removable") return [plTag("ok", "אפשר להוציא"), true, bar, `נותק בבטחה · ${esc(plAgo(row.changed_at))}`];
  if (live && d.state === "done") return [plTag("ok", "הושלם"), true, bar, row && row.state === "done" ? "מנתק בבטחה — עדיין לא להוציא" : ""];
  if (live && drawerPulled(d)) return [plTag("mut", "נשלף"), false, "", PULLED_HE];   // #3209: grey -- the same tag and words as the cloner screen; the name has not returned to the queue
  if (live && d.state === "failed") return [plTag("bad", "נכשל"), false, "", esc(drawerDisplayError(d)) + (row && row.name ? ` · השם ${ltr(row.name)} נשמר לדיסק` : "")];
  if (live && d.state === "writing" && d.rejoin) return [plTag("w", "מצטרף ומשלים") + (health ? " " + health : ""), false, bar, plRejoinNote(d.rejoin)];
  if (live && d.state === "writing") return [plTag("w", "כותב") + (health ? " " + health : ""), false, bar, ""];
  if (live && d.state === "verifying") return [plTag("mut", "מאמת") + (health ? " " + health : ""), false, bar, ""];
  if (live && d.state === "cancelled") return [plTag("mut", "בוטל"), false, "", ""];
  return [plTag(PL_TONE[ctone] || "mut", ctext), false, "", ""];   /* לפני הסבב: תקין = תג ירוק קטן על שורה ניטרלית */
}
/* ‏#3181: משכפל שכל הדיסקים שלו סיימו ונשארו שמות — "ממתין לדיסק" עם הזמן שעבר, בשורת המשכפל. מ-`waiting` של השרת:
   הסוכן אמר שהוא ממתין (‏line_wait), ויש לו למה. משכפל ששתק אינו ברשימה, ולכן אינו מוצג כממתין */
function plWait(pl, m) {
  const w = (Array.isArray(pl.waiting) ? pl.waiting : []).find((x) => x.mac === m.mac), span = w ? fmtSpan(w.since_s) : null;
  return w ? " " + plTag("mut", `ממתין לדיסק${span ? ` · ${span}` : ""}`) : "";
}
function plTable(kids, r, pl) {
  const head = `<tr><th>מגירה</th><th>שם</th><th>דיסק</th><th>מצב</th><th>התקדמות</th><th>הערה</th></tr>`;
  const inRound = new Set((r.target_slots || []).map((t) => t.mac));
  const rows = kids.filter((m) => inRound.has(m.mac)).map((m) => {
    const slots = machineSlots(m).slots.filter((s) => !s.optical);
    const group = `<tr class="g"><td colspan="6">${esc(machineName(m) || m.mac)}<span>${slots.length} מגירות</span>${plWait(pl, m)}</td></tr>`;
    return group + slots.map((s) => {
      const d = s.disk, row = plDiskOf(pl, m, s), [tags, rdy, prog, note] = plRow(r, pl, m, s);
      const disk = d ? `${d.size_bytes ? esc(marketedDiskSize(d.size_bytes)) : ""} <span class="mono">${esc(d.model || "")}</span>` : "ריקה";
      return `<tr${rdy ? ` class="rdy"` : ""}><td>מגירה ${esc(s.n != null ? String(s.n) : "—")}</td><td class="pl-nm mono">${row && row.name && d ? esc(row.name) : "—"}</td>`
        + `<td class="muted">${disk}</td><td>${tags}</td><td>${prog}</td><td class="muted">${note}</td></tr>`;
    }).join("");
  }).join("");
  return `<div class="pl-tbl card"><div class="card-h"><span>לפי משכפל ומגירה</span><small>מתעדכן חי</small></div><table class="pl-t">${head}${rows}</table></div>`;
}
function plReady(kids, pl) {
  const items = pl.disks.filter((x) => x.state === "removable");
  const where = (x) => { const m = kids.find((k) => k.mac === x.mac); return `${esc(m ? machineName(m) : x.mac)} · מגירה ${esc(String(x.port != null ? x.port : "—"))}`; };
  const list = items.length ? items.map((x) => `<div class="pl-rd"><span class="pl-big mono">${esc(x.name || "—")}</span><span class="pl-w">${where(x)}<small>${esc(plAgo(x.changed_at))}</small></span>${plTag("ok", "אפשר להוציא")}</div>`).join("")
    : `<div class="pl-dc muted">אין כרגע דיסק שאפשר להוציא</div>`;
  return `<div class="card"><div class="card-h"><span>מוכן להוצאה</span>${plTag("ok", String(items.length))}</div>${list}</div>`;
}
function plDecisions(pl) {
  const ds = Array.isArray(pl.decisions) ? pl.decisions : [];
  const list = ds.length ? ds.map((x) => `<div class="pl-dc">${esc(x.reason || "")}<time>${esc(fmtClock(x.at))}${x.mode === "shadow" ? " · צל — נרשם, לא בוצע" : ""}</time></div>`).join("")
    : `<div class="pl-dc muted">המוח עוד לא החליט דבר בסבב הזה</div>`;
  return `<div class="card"><div class="card-h"><span>החלטות המוח</span></div>${list}</div>`;
}
function plBody(kids, r) {
  const pl = r.production_line, inRound = new Set((r.target_slots || []).map((t) => t.mac));
  const live = kids.filter((m) => inRound.has(m.mac)).flatMap((m) => machineSlots(m).slots.filter((s) => s.live && s.disk.state === "writing"));
  const written = pl.written, target = pl.target || 1, writing = live.length, named = pl.disks.some((x) => x.name);
  const band = `<div class="c12 card pl-band"><div class="pl-bigline"><span>נכתבו ${esc(String(written))} מתוך ${esc(String(pl.target))} ${named ? "שמות" : "דיסקים"} · נשארו ${esc(String(pl.remaining))}</span>`
    + `${pl.next_name ? `<small>השם הבא: ${ltr(pl.next_name)}</small>` : ""}</div>`
    + `<div class="pl-seg"><i class="g" style="width:${100 * written / target}%"></i><i class="b" style="width:${100 * Math.min(writing, target - written) / target}%"></i></div>`
    + `<div class="pl-lg"><span><b class="g"></b>נכתבו</span><span><b class="b"></b>בכתיבה עכשיו</span><span><b></b>נשארו</span></div></div>`;
  const awake = kids.filter((m) => inRound.has(m.mac) && (roomMachine(m.mac) || {}).awake).length;
  const stat = (l, v) => `<div class="card pl-st"><small>${esc(l)}</small><b class="mono">${v}</b></div>`;
  const stats = `<div class="c12 pl-stats">${stat("משכפלים בסבב", `${awake} / ${inRound.size}`)}${stat("מגירות כותבות", `${writing} / ${pl.drawers}`)}`
    + `${stat("קצב זרם", r.throughput_bps != null ? esc(`${fmtBytes(r.throughput_bps)}/s`) : "לא נמדד")}</div>`;
  return band + stats + `<div class="c12 pl-cols">${plTable(kids, r, pl)}<div class="pl-side">${plReady(kids, pl)}${plDecisions(pl)}</div></div>`;
}
