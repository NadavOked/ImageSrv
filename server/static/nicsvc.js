/* ‏#705 — שירותים לכל כרטיס רשת (המוקאפ המאושר nicperm.html, נדב 13/10).
   השרת אוכף: כבוי = לא מאזין/לא משדר על הכרטיס (bind + nft). ‏GET/PUT ‏/net/services
   (docs/interfaces.md). כאן: עמודת "שירותים" בטבלת הכרטיסים, המגירה, חלון האישור,
   מסך המסירה, ופס "ממתין לאישור" שמופיע בכל דף.
   עיקרון 5: ‏/net/services שלא נקרא = "לא נקרא", לא "אף שירות"; "פעיל" נקבע לפי
   הסוקט (`listening`) כשיש מה למדוד. */

let NICSVC = null, NICSVC_ERR = "", NICSVC_TICK = null, NICSVC_POLL = null, NICSVC_LEFT = 0;
let NICSVC_DRAFT = null;      // { name, base:Set, draft:Set, tried:bool }

const NICSVC_ICONS = {
  console: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M6.5 6.5h.01M9 6.5h.01"/><path d="M7 13l3 2-3 2M12 17h5"/>',
  monitor: '<rect x="3" y="4" width="18" height="13" rx="1.5"/><path d="m10 8.5 5 2.5-5 2.5Z"/><path d="M8 21h8M12 17v4"/>',
  multicast: '<circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2"/>',
  netboot: '<path d="M12 3v9M6.5 6.5a8 8 0 1 0 11 0"/>',
  interserver: '<path d="M4 6h15l-4-4M20 18H5l4 4M20 6v6M4 18v-6"/>',
  ntp: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  warn: '<path d="M12 4 2.8 20h18.4z"/><path d="M12 10v4.5M12 17.5h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  ext: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  unplug: '<path d="M9 3v4M15 3v4M7 7h10v4a5 5 0 0 1-10 0z"/><path d="M12 16v5"/><path d="M3 3l18 18"/>',
  ok: '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16.5 9.5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
};
const nsIc = (name, cls = "") => `<svg class="n-ic ${cls}" viewBox="0 0 24 24" aria-hidden="true">${NICSVC_ICONS[name] || ""}</svg>`;

/* הסדר הקבוע, השמות וההסבר — מהמוקאפ (SVC). הפורט נקרא מ-/ports כשאפשר. */
const NICSVC_DEF = [
  { k: "console", n: "קונסולה", port: "http_console", d: "הממשק הזה. מי שנמצא ברשת של הכרטיס יגיע למסך הכניסה." },
  { k: "monitor", n: "מוניטור", d: "צפייה במסך של מחשב ושליטה בו מהדפדפן." },
  { k: "multicast", n: "שידור", pt: "multicast UDP", d: "שליחת אימג' לכמה מחשבים בבת אחת. תעבורה כבדה — רק ברשת של מחשבי השיכפול." },
  { k: "netboot", n: "אתחול מהרשת", pt: "DHCP 67 · TFTP 69", d: "מחשב שעולה מהרשת מקבל מהשרת כתובת ותפריט אתחול. השרת מחלק כתובות ברשת הזאת." },
  { k: "interserver", n: "בין-שרתים", port: "interserver", d: "הקו בין השרת הראשי לשרת המשנה — אימג'ים והגדרות." },
  { k: "ntp", n: "שרת זמן (NTP)", pt: "UDP 123", d: "השרת מספק שעה למחשבים ברשת של הכרטיס." },
];
const NICSVC_NAME = Object.fromEntries(NICSVC_DEF.map((s) => [s.k, s.n]));

/* צירופים חריגים — אזהרה כתומה, לא חסימה (נדב מחליט; הכרטיס שלו). */
const NICSVC_ODD = {
  mgmt: { netboot: "ברשת השרתים כבר יש DHCP של המכללה. שני שרתי DHCP באותה רשת — מחשבים יקבלו כתובת מהשרת הלא נכון.",
          multicast: "שידור מציף את הרשת בתעבורה כבדה. ברשת השרתים הוא יאט את כל מי שמחובר אליה." },
  dist: { console: "הקונסולה המלאה לא אמורה להיות זמינה ברשת ההפצה — כל מחשב שמחובר שם יגיע למסך הכניסה.",
          interserver: "הקו בין השרתים לא עובר ברשת ההפצה. שרת המשנה מתחבר דרך רשת השרתים." },
  office: { netboot: "ברשת המשרדים יש DHCP של המכללה. שני שרתי DHCP באותה רשת — מחשבי משרד יקבלו כתובת מהשרת הלא נכון.",
            multicast: "שידור מציף את הרשת בתעבורה כבדה. ברשת המשרדים הוא יאט את כל מי שמחובר אליה." },
};

/* --- נתונים --- */
async function nicsvcLoad() {
  try {
    const v = await api("/net/services");
    if (!v || !Array.isArray(v.nics)) throw new Error("תשובה שאינה מצב שירותים");
    NICSVC = v; NICSVC_ERR = "";
  } catch (e) { NICSVC = null; NICSVC_ERR = e.message; nicsvcBanner(); throw e; }
  nicsvcBanner();
  return NICSVC;
}
/* מ-showApp: הפס בכל דף. כשל קריאה כאן אינו נבלע — הוא נשאר ב-NICSVC_ERR ומוצג בדף הרשת. */
function nicsvcBoot() { return nicsvcLoad().catch(() => null); }
function nicsvcRow(name) { return ((NICSVC && NICSVC.nics) || []).find((r) => r.name === name) || null; }
/* הכרטיס מ-/net/interfaces: של דף הרשת (NETW), ובכל דף אחר — מה ש-net.js כבר קרא (NICS) */
function nicsvcNic(name) {
  const list = (typeof NETW !== "undefined" && NETW.nics) || (typeof NICS !== "undefined" && Array.isArray(NICS) ? NICS : []);
  return list.find((n) => n.name === name) || null;
}
function nicsvcPort(id, fallback) {
  const row = ((typeof NETW !== "undefined" && NETW.ports) || []).find((p) => p.id === id);
  return row && row.port ? `${row.port}/tcp` : fallback;
}
function nicsvcPt(s) {
  if (s.k === "console") return nicsvcPort("http_console", "8081/tcp");
  if (s.k === "interserver") return nicsvcPort("interserver", "8443/tcp");
  if (s.k === "ntp") return "123/udp";
  return s.pt || "";
}
/* תפקיד הכרטיס לצורך אזהרות: ניהול · הפצה · משרדים (יש כתובת) · אחר */
function nicsvcKind(name) {
  if (typeof isMgmtNic === "function" && isMgmtNic(name)) return "mgmt";
  const n = nicsvcNic(name);
  const dep = typeof NET_DEPLOY !== "undefined" && NET_DEPLOY && NET_DEPLOY.interface === name;
  if (dep || (n && n.enabled) || (typeof netBondMember === "function" && netBondMember(name))) return "dist";
  const row = nicsvcRow(name);
  return row && row.address ? "office" : "none";
}
function nicsvcOdd(name, k) { return (NICSVC_ODD[nicsvcKind(name)] || {})[k] || ""; }
/* אחרים שהקונסולה פתוחה בהם (כרטיס קיים במערכת בלבד) */
function nicsvcOtherConsole(name) { return ((NICSVC && NICSVC.nics) || []).filter((r) => r.present && r.name !== name && r.services.includes("console")); }
function nicsvcYou() { return (NICSVC && NICSVC.you) || {}; }
/* "דרכו אתה מחובר" — כולל "לא ידוע" (null): מי שלא יודע דרך מה הוא מחובר מניח שזה הכרטיס שלו (עיקרון 5) */
function nicsvcIsYou(name) { const y = nicsvcYou().interface; return y === name || (y === null && !!NICSVC); }
function nicsvcConsoleUrl(row) {
  const port = (nicsvcPort("http_console", "8081/tcp") || "").split("/")[0];
  const scheme = typeof ME !== "undefined" && ME && ME.tls ? "https" : (location && location.protocol === "http:" ? "http" : "https");
  return `${scheme}://${row.address}${port ? ":" + port : ""}`;
}

/* --- טבלה --- */
function nicsvcCell(n) {
  if (!NICSVC) return `<span class="muted" title="${esc(NICSVC_ERR || "")}">לא נקרא</span>`;
  const bond = typeof netBondMember === "function" ? netBondMember(n.name) : null;
  if (bond && !nicsvcRow(n.name)?.configured) return `<span class="ns-sc none">דרך ${esc(bond)}</span>`;
  const row = nicsvcRow(n.name);
  if (!row) return `<span class="muted">לא נקרא</span>`;
  if (!row.configured) return `<span class="ns-sc unset">לא הוגדר · הכול כבוי</span>`;
  const on = new Set(row.services);
  const chips = NICSVC_DEF.filter((s) => on.has(s.k)).map((s) => {
    const odd = nicsvcOdd(n.name, s.k);
    return `<span class="ns-sc${odd ? " w" : ""}"${odd ? ` title="${esc(odd)}"` : ""}>${nsIc(odd ? "warn" : s.k)}${esc(s.n)}${odd ? '<span class="sr"> — צירוף חריג</span>' : ""}</span>`;
  });
  const p = NICSVC.pending;
  if (p && p.interface === n.name) {
    const off = (p.previous || []).filter((k) => !p.applied.includes(k));
    const what = off.includes("console") ? "קונסולה כובתה" : "שינוי ממתין";
    chips.unshift(`<span class="ns-sc w">${nsIc("clock")}${what} · <span data-ns-cd>${NICSVC_LEFT || p.seconds_left}</span> שנ׳</span>`);
  }
  if (!chips.length) return `<span class="ns-sc none">אף שירות</span>`;
  return `<div class="ns-svcs">${chips.join("")}</div>`;
}
function nicsvcNameCell(n) {
  const you = NICSVC && nicsvcYou().interface === n.name && !(NICSVC.pending && NICSVC.pending.interface === n.name);
  return `<div class="ns-nm"><b>${esc(n.name)}</b>${you ? `<small><i></i>החיבור שלך</small>` : ""}</div>`;
}
function nicsvcRowAttrs(n) {
  const row = nicsvcRow(n.name);
  const fresh = row && !row.configured && n.present && n.state === "up";
  return ` tabindex="0" class="${fresh ? "ns-new" : ""}" onclick="nicsvcRowClick(event,'${encodeId(n.name)}')" onkeydown="if(event.key==='Enter')nicsvcRowClick(event,'${encodeId(n.name)}')"`;
}
function nicsvcRowClick(event, enc) {
  const t = event && event.target;
  if (t && t.closest && t.closest("button,a,input,.kebab")) return;
  nicsvcOpen(enc);
}
/* פסים מעל הטבלה: כרטיס מחובר שלא הוגדר, וצירוף חריג שנשמר */
function nicsvcTableNotes() {
  if (!NICSVC) return NICSVC_ERR ? `<div class="c12">${UI.note("warn", `‏/net/services לא נקרא: ${esc(NICSVC_ERR)} — השירותים לכל כרטיס אינם ידועים`)}</div>` : "";
  const out = [];
  for (const row of NICSVC.nics) {
    const n = nicsvcNic(row.name);
    if (!n) continue;
    if (!row.configured && n.present && n.state === "up" && !(typeof netBondMember === "function" && netBondMember(n.name)))
      out.push(UI.note("info", `<b>כרטיס מחובר שלא הוגדר: ${esc(n.name)}</b>${n.speed_mbps ? ` — קישור ${esc(netSpeedText(n))}` : ""}. השרת לא מאזין עליו לשום דבר עד שתבחר אילו שירותים לתת דרכו. <button class="btn sm" type="button" onclick="nicsvcOpen('${encodeId(n.name)}')">בחירת שירותים…</button>`));
    if (row.source === "saved") {
      const on = new Set(row.services);
      const k = NICSVC_DEF.map((s) => s.k).find((key) => on.has(key) && nicsvcOdd(row.name, key));
      if (k) out.push(UI.note("warn", `<b>${esc(row.name)} נותן ${esc(NICSVC_NAME[k])}${nicsvcKind(row.name) === "mgmt" ? " ברשת השרתים" : ""}.</b> ${esc(nicsvcOdd(row.name, k))} <button class="btn sm" type="button" onclick="nicsvcOpen('${encodeId(row.name)}')">פתיחת השירותים…</button>`));
    }
    if (row.unreadable) out.push(UI.note("err", `ההגדרה השמורה של ${esc(row.name)} לא נקראה — מוצגת ברירת המחדל של היום`));
  }
  return out.map((h) => `<div class="c12">${h}</div>`).join("");
}

/* --- המגירה --- */
function nicsvcOpen(enc) {
  let name = enc; try { name = decodeURIComponent(enc); } catch (e) {}
  if (!NICSVC) { toast(`‏/net/services לא נקרא${NICSVC_ERR ? ": " + NICSVC_ERR : ""}`); return; }
  const bond = typeof netBondMember === "function" ? netBondMember(name) : null;
  if (bond && !nicsvcRow(name)?.configured && nicsvcRow(bond)) name = bond;   // חבר ב-bond: השירותים של ה-bond
  const row = nicsvcRow(name);
  if (!row) { toast(`אין כרטיס ${name} במצב השירותים`); return; }
  NICSVC_DRAFT = { name, base: new Set(row.services), draft: new Set(row.services), tried: false };
  const n = nicsvcNic(name) || {};
  const role = typeof netRolePill === "function" && n.name ? stripTags(netRolePill(n)) : "";
  const sub = [n.description, row.address || "בלי כתובת", role].filter(Boolean).join(" · ");
  openDrawer(`שירותים — ${name}`, nicsvcDrawerHtml(), { sub });
  nicsvcFoot();
}
function nicsvcStatus(s, on, was, row) {
  if (on !== was) return `<span class="ns-st chg">${on ? "יופעל בשמירה" : "יכובה בשמירה"}</span>`;
  const heard = (row.listening || {})[s.k];
  if (on && heard === false) return `<span class="ns-st w">${nsIc("warn")}מוגדר פעיל — אבל השרת לא מאזין כאן כרגע (ראו "פורטים")</span>`;
  if (!on && heard === true) return `<span class="ns-st w">${nsIc("warn")}כבוי בהגדרה — אבל השרת עדיין מאזין כאן</span>`;
  if (on) return `<span class="ns-st on">${nsIc("ok")}${s.k === "multicast" ? "פעיל — השרת משדר כאן" : heard ? "פעיל — השרת מאזין כאן" : "פעיל"}</span>`;
  return `<span class="ns-st off">כבוי — השרת ${s.k === "multicast" ? "לא משדר" : "לא מאזין"} כאן</span>`;
}
function nicsvcDrawerHtml() {
  const D = NICSVC_DRAFT, row = nicsvcRow(D.name), n = nicsvcNic(D.name) || {};
  const others = nicsvcOtherConsole(D.name);
  const last = D.base.has("console") && !others.length;
  const you = nicsvcIsYou(D.name);
  const items = NICSVC_DEF.map((s) => {
    const on = D.draft.has(s.k), was = D.base.has(s.k), locked = s.k === "console" && last;
    const odd = on ? nicsvcOdd(D.name, s.k) : "";
    let msg = "";
    if (locked && D.tried) msg = `<div class="ns-msg bad" role="alert">${nsIc("lock")}<span><b>אי אפשר לכבות כאן.</b> זה הכרטיס היחיד שהקונסולה פתוחה בו — כיבוי ינעל את כולם מחוץ לשרת. כדי להעביר את הקונסולה: הפעל אותה קודם בכרטיס אחר, התחבר משם, ואז חזור לכאן.</span></div>`;
    else if (locked) msg = `<div class="ns-msg info">${nsIc("lock")}<span>זה הכרטיס היחיד עם קונסולה, ולכן היא נעולה כאן. הפעל קונסולה בכרטיס אחר כדי לשחרר.</span></div>`;
    else if (s.k === "console" && you && was && !on) msg = `<div class="ns-msg">${nsIc("warn")}<span><b>אתה מחובר דרך הכרטיס הזה.</b> אחרי השמירה תתנתק, ותצטרך לאשר תוך ${NICSVC.window_seconds} שניות מ-<span class="ns-addr">${esc((others[0] || {}).address || "")}</span> — אחרת השינוי חוזר לבד.</span></div>`;
    else if (odd) msg = `<div class="ns-msg" role="status">${nsIc("warn")}<span><b>צירוף חריג.</b> ${esc(odd)} אפשר לשמור — רק שתדע.</span></div>`;
    else if (s.k === "netboot" && on && !n.enabled && !(typeof netBondMember === "function" && netBondMember(D.name))) msg = `<div class="ns-msg info">${nsIc("info")}<span>אין DHCP מוגדר בכרטיס הזה — השרת יגיש בו TFTP וערוץ סוכן (8080), אבל לא יחלק כתובות. חלוקת כתובות מופעלת ב"הגדרת כרטיס…".</span></div>`;
    const pt = nicsvcPt(s);
    return `<li class="ns-svr${on ? " on" : ""}" data-k="${s.k}"><div class="rw"><span class="ico">${nsIc(s.k)}</span>
      <div class="t"><label for="ns-${s.k}">${esc(s.n)}${pt ? `<span class="pt">${esc(pt)}</span>` : ""}</label><p id="ns-d-${s.k}">${esc(s.d)}</p>${nicsvcStatus(s, on, was, row)}</div>
      <label class="n-sw"${locked ? ' aria-disabled="true"' : ""}>${locked ? nsIc("lock", "ns-lock") : ""}<input id="ns-${s.k}" type="checkbox" role="switch" ${on ? "checked" : ""}${locked ? ' aria-disabled="true"' : ""} aria-describedby="ns-d-${s.k}" onchange="nicsvcToggle('${s.k}',this)"><span class="track"></span><span class="sr">${esc(s.n)}</span></label>
      </div>${msg}</li>`;
  }).join("");
  const ch = NICSVC_DEF.filter((s) => D.draft.has(s.k) !== D.base.has(s.k));
  const sum = ch.length ? `<div class="ns-chg" aria-live="polite"><b>ישתנה בשמירה:</b><ul>${ch.map((s) => `<li>${esc(s.n)} — ${D.draft.has(s.k) ? "יופעל" : "יכובה"}</li>`).join("")}</ul></div>` : "";
  const y = nicsvcYou();
  const top = !row.configured
    ? `<div class="ns-note">${nsIc("info")}<span><b>כרטיס שלא הוגדר — הכול כבוי.</b> זו ברירת המחדל הבטוחה: עד שתבחר, השרת לא מאזין עליו לשום דבר. הכתובת שלו מוגדרת ב"הגדרת כרטיס…".</span></div>`
    : y.interface === D.name ? `<div class="ns-note">${nsIc("info")}<span><b>אתה מחובר דרך הכרטיס הזה</b>${y.peer ? ` — מהמחשב <span class="ns-addr">${esc(y.peer)}</span>` : ""} לכתובת <span class="ns-addr">${esc(row.address || y.address || "")}</span></span></div>`
    : y.interface === null ? `<div class="ns-note">${nsIc("info")}<span><b>לא ידוע דרך איזה כרטיס אתה מחובר</b> (${esc(y.address || "?")}) — כיבוי קונסולה בכל כרטיס יטופל כאילו הוא החיבור שלך.</span></div>` : "";
  const risky = you && D.base.has("console") && !D.draft.has("console");
  return `<div class="page drw">${top}<div class="ns-sec"><h3>מה השרת נותן דרך הכרטיס הזה</h3><ul class="ns-svl">${items}</ul></div>${sum}
    <p class="ns-fixed">כבוי = השרת באמת לא מאזין ולא משדר על הכרטיס הזה (גם לא בפורט פתוח שמישהו מנחש). המצב המלא לפי פורט — בלשונית "פורטים".</p>
    <div class="acts"><button class="btn" type="button" onclick="closeDrawer()">ביטול</button><button class="btn primary" type="button" id="nsSave" onclick="nicsvcSave()" ${ch.length ? "" : "disabled"}>${risky ? "שמירה והחלה…" : "שמירה והחלה"}</button></div></div>`;
}
/* רמז בפס התחתון (כמו n-hint של המוקאפ) — drawerEnhance בונה את הפס מחדש בכל ציור */
function nicsvcFoot() {
  const foot = document.getElementById("drawerFoot");
  if (!foot || !NICSVC_DRAFT || !foot.insertBefore) return;
  const n = NICSVC_DEF.filter((s) => NICSVC_DRAFT.draft.has(s.k) !== NICSVC_DRAFT.base.has(s.k)).length;
  const hint = document.createElement("span");
  hint.className = "n-hint";
  hint.textContent = n ? `${n === 1 ? "שינוי אחד" : n + " שינויים"} · חל מיד` : "אין שינויים";
  const old = foot.querySelector && foot.querySelector(".n-hint");
  if (old && old.remove) old.remove();
  foot.insertBefore(hint, foot.firstChild);
}
function nicsvcRedraw(k) {
  const body = document.getElementById("drawerBody");
  const top = body ? body.scrollTop : 0;
  setDrawerBody(nicsvcDrawerHtml());
  nicsvcFoot();
  if (body) body.scrollTop = top;
  const input = document.getElementById("ns-" + k);
  if (input && input.focus) input.focus({ preventScroll: true });
}
function nicsvcToggle(k, input) {
  const D = NICSVC_DRAFT;
  if (!D) return;
  if (input && input.getAttribute && input.getAttribute("aria-disabled") === "true") {
    input.checked = true; D.tried = true; nicsvcRedraw(k); return;
  }
  if (input.checked) D.draft.add(k); else D.draft.delete(k);
  nicsvcRedraw(k);
}
function nicsvcWanted() { return NICSVC_DEF.map((s) => s.k).filter((k) => NICSVC_DRAFT.draft.has(k)); }
async function nicsvcSave() {
  const D = NICSVC_DRAFT;
  if (!D) return;
  if (nicsvcIsYou(D.name) && D.base.has("console") && !D.draft.has("console")) return nicsvcConfirm();
  const name = D.name, wanted = nicsvcWanted();
  try {
    await put(`/net/services/${encodeId(name)}`, { services: wanted });
  } catch (e) { toast(e.message); return; }
  closeDrawer();
  toast(`נשמר — ${name} נותן עכשיו: ${wanted.map((k) => NICSVC_NAME[k]).join(" · ") || "אף שירות"}`);
  await nicsvcRefresh();
}
async function nicsvcRefresh() {
  try { await nicsvcLoad(); } catch (e) { toast("מצב השירותים לא נקרא: " + e.message); }
  if (typeof current !== "undefined" && current === "network" && typeof renderCurrent === "function") renderCurrent();
}

/* --- נעילה עצמית: חלון אישור (כפתור אדום, פוקוס על ביטול, בלי הקלדה — #1270) --- */
function nicsvcConfirm() {
  const D = NICSVC_DRAFT, name = D.name, other = nicsvcOtherConsole(name)[0] || {};
  const otherNic = nicsvcNic(other.name) || {};
  const url = other.address ? nicsvcConsoleUrl(other) : "";
  const also = NICSVC_DEF.filter((s) => s.k !== "console" && D.draft.has(s.k) !== D.base.has(s.k));
  // ‏"מה יקרה" (.n-impact של console.css) עם האייקונים של המוקאפ: ניתוק · שעון · חזרה
  const impacts = [
    ["bad", "unplug", `<b>תתנתק ברגע ההחלה.</b> הדף הזה יפסיק לענות.`],
    ["clk", "clock", `<b>יש לך ${NICSVC.window_seconds} שניות</b> להיכנס מ${esc(otherNic.description || other.name || "כרטיס אחר")}, בכתובת <span class="ns-addr">${esc(url)}</span>, ולאשר שם.`],
    ["und", "undo", `<b>לא אישרת בזמן? אין מה לעשות.</b> השרת מחזיר לבד את ההגדרה הקודמת, והקונסולה חוזרת ל-${esc(name)}.`],
  ];
  if (also.length) impacts.push(["", "info", `באותה שמירה: ${also.map((s) => `${esc(s.n)} ${D.draft.has(s.k) ? "יופעל" : "יכובה"}`).join(", ")}.`]);
  sheet({ title: `לכבות את הקונסולה ב-${name}?`, sub: "אתה מחובר לשרת דרך הכרטיס הזה.", submitLabel: "לכבות ולהתנתק", danger: true,
    note: `<ul class="n-impact">${impacts.map(([cls, ic, html]) => `<li class="${cls}">${nsIc(ic)}<span>${html}</span></li>`).join("")}</ul>`,
    onSubmit: async () => {
      const out = await put(`/net/services/${encodeId(name)}`, { services: nicsvcWanted(), handoff: true });
      return () => nicsvcHandoff(name, out);
    } });
  const cancel = document.getElementById("sf-cancel");
  if (cancel && cancel.focus) setTimeout(() => cancel.focus(), 40);
}

/* --- מסך המסירה: הדפדפן יודע שהחיבור ייפול, ומראה מראש לאן לעבור --- */
function nicsvcRing(sec, total, big) {
  return `<div class="ns-ring${big ? " big" : ""}" style="--p:${total ? sec / total : 0}" role="timer" aria-label="נשארו ${sec} שניות"><svg viewBox="0 0 36 36"><circle class="bg" cx="18" cy="18" r="16"/><circle class="fg" cx="18" cy="18" r="16" pathLength="100.5"/></svg><b data-ns-cd>${sec}</b>${big ? '<span class="lbl2">שניות</span>' : ""}</div>`;
}
function nicsvcHandoff(name, out) {
  closeDrawer();
  const total = (out && out.pending && out.pending.window_seconds) || 60;
  const go = (out && out.go && out.go[0]) || "";
  const row = nicsvcRow(name) || {};
  const form = document.getElementById("sheet"), modal = document.getElementById("modal");
  form.className = "sheet win n-narrow";
  form.onsubmit = (e) => e.preventDefault();
  form.innerHTML = `<div class="n-b sbody"><div class="ns-hand" role="alertdialog" aria-labelledby="nsHandH">
      ${nicsvcRing(total, total, true)}
      <h3 id="nsHandH">השינוי הוחל. החיבור הזה מתנתק.</h3>
      <p>כדי לשמור אותו, היכנס מהכתובת שהקונסולה עדיין פתוחה בה ואשר שם:</p>
      <div class="go">${go ? `<a class="btn primary" id="nsGo" href="${esc(go)}">${nsIc("ext")}<span class="mono" dir="ltr">${esc(go)}</span></a><button class="btn" type="button" onclick="nicsvcCopy('${encodeId(go)}')">${nsIc("copy")}העתקה</button>` : `<span class="muted">לא נמצאה כתובת אחרת שהקונסולה פתוחה בה</span>`}</div>
      <p class="later">לא תגיע בזמן? אין צורך לעשות דבר — כשהספירה נגמרת, השרת מחזיר את ההגדרה הקודמת והקונסולה חוזרת ל-${esc(name)}${row.address ? `, בכתובת <span class="ns-addr">${esc(row.address)}</span>` : ""}</p>
    </div></div>`;
  form.classList.remove("hidden");
  openModalFrame(modal, "sheet-mode");
  const link = document.getElementById("nsGo");
  if (link && link.focus) setTimeout(() => link.focus(), 40);
  nicsvcCountdown(total, total, () => { closeModal(); nicsvcBoot().then(() => { if (typeof renderCurrent === "function") renderCurrent(); }); });
}
function nicsvcCopy(enc) {
  let text = enc; try { text = decodeURIComponent(enc); } catch (e) {}
  const done = () => toast("הכתובת הועתקה");
  try { navigator.clipboard.writeText(text).then(done, () => toast(text)); } catch (e) { toast(text); }
}
function nicsvcCountdown(from, total, done) {
  clearInterval(NICSVC_TICK);
  NICSVC_LEFT = from;
  NICSVC_TICK = setInterval(() => {
    NICSVC_LEFT = Math.max(0, NICSVC_LEFT - 1);
    document.querySelectorAll("[data-ns-cd]").forEach((e) => { e.textContent = NICSVC_LEFT; });
    document.querySelectorAll(".ns-ring").forEach((r) => { r.style.setProperty("--p", total ? NICSVC_LEFT / total : 0); r.setAttribute("aria-label", `נשארו ${NICSVC_LEFT} שניות`); });
    const live = document.getElementById("nsLive");
    if (live && (NICSVC_LEFT === 30 || NICSVC_LEFT === 10)) live.textContent = `נשארו ${NICSVC_LEFT} שניות לאישור השינוי ברשת`;
    if (NICSVC_LEFT <= 0) { clearInterval(NICSVC_TICK); NICSVC_TICK = null; if (done) done(); }
  }, 1000);
}

/* --- הפס בכל דף: ממתין לאישור / חזר לבד --- */
const NICSVC_DISMISS = "imagesrv-nicsvc-dismissed";
function nicsvcDismissed(at) { try { return sessionStorage.getItem(NICSVC_DISMISS) === at; } catch (e) { return false; } }
function nicsvcDismiss(enc) {
  let at = enc; try { at = decodeURIComponent(enc); } catch (e) {}
  try { sessionStorage.setItem(NICSVC_DISMISS, at); } catch (e) {}
  nicsvcBanner();
}
function nicsvcBannerHtml() {
  if (!NICSVC) return "";
  const p = NICSVC.pending;
  if (p && p.corrupt) return `<div class="ns-pend bad" role="alert">${nsIc("warn")}<div class="tx"><b>סמן ההמתנה של שינוי ברשת פגום</b><p>השרת יחזיר את ההגדרה הקודמת. אפשר להחזיר עכשיו.</p></div><div class="acts"><button class="btn" type="button" onclick="nicsvcUndo()">החזר עכשיו</button></div></div>`;
  if (p) {
    const n = nicsvcNic(p.interface) || {};
    const off = (p.previous || []).filter((k) => !p.applied.includes(k)), on = p.applied.filter((k) => !(p.previous || []).includes(k));
    const what = [off.length ? `${off.map((k) => NICSVC_NAME[k]).join(", ")} כובתה` : "", on.length ? `${on.map((k) => NICSVC_NAME[k]).join(", ")} הופעלה` : ""].filter(Boolean).join(" · ") || "השירותים שונו";
    const left = NICSVC_LEFT || p.seconds_left;
    return `<div class="ns-pend" role="region" aria-label="שינוי ברשת ממתין לאישור">${nicsvcRing(left, p.window_seconds)}
      <div class="tx"><b>שינוי ברשת ממתין לאישור שלך — חוזר בעוד <span data-ns-cd>${left}</span> שנ׳</b><p>${esc(what)} ב-${esc(p.interface)}${n.description ? ` (${esc(n.description)})` : ""}. אם לא תאשר, ההגדרה הקודמת תחזור לבד.</p><span class="sr" id="nsLive" aria-live="polite"></span></div>
      <div class="acts"><button class="btn primary" type="button" onclick="nicsvcKeep()">השאר את השינוי</button><button class="btn" type="button" onclick="nicsvcUndo()">החזר עכשיו</button></div></div>`;
  }
  const last = NICSVC.last;
  const fresh = last && last.at && Date.now() - Date.parse(last.at) < 15 * 60 * 1000;
  if (last && last.event === "reverted" && fresh && !nicsvcDismissed(last.at)) {
    const row = nicsvcRow(last.interface) || {};
    const why = last.reason === "expired" ? `לא אושר תוך ${NICSVC.window_seconds} שניות, ולכן השרת החזיר את ההגדרה הקודמת`
      : last.reason === "operator" ? "הוחזר לבקשתך" : last.reason === "boot" ? "השרת אותחל לפני שהשינוי אושר, ולכן ההגדרה הקודמת חזרה"
      : last.reason === "apply_failed" ? "ההחלה נכשלה, ולכן ההגדרה הקודמת חזרה" : (last.reason_he || "ההגדרה הקודמת חזרה");
    const errs = (last.errors || []).length ? ` שגיאה: ${esc(last.errors.join(" · "))}` : "";
    const back = row.services && row.services.includes("console") ? `: הקונסולה פעילה שוב ב-${esc(last.interface)}${row.address ? `, בכתובת <span class="ns-addr">${esc(row.address)}</span>` : ""}` : "";
    return `<div class="ns-pend ${errs ? "bad" : "info"}" role="status">${nsIc("undo")}<div class="tx"><b>השינוי ברשת חזר${last.reason === "operator" ? "" : " לבד"}</b><p>${why}${back}.${errs}</p></div><button class="x" type="button" aria-label="סגירה" onclick="nicsvcDismiss('${encodeId(last.at)}')">${nsIc("x")}</button></div>`;
  }
  return "";
}
function nicsvcBanner() {
  const slot = document.getElementById("nicPend");
  if (slot) slot.innerHTML = nicsvcBannerHtml();
  const p = NICSVC && NICSVC.pending;
  if (p && !p.corrupt) {
    if (!NICSVC_TICK) nicsvcCountdown(p.seconds_left, p.window_seconds, () => nicsvcRefresh());
    if (!NICSVC_POLL) NICSVC_POLL = setInterval(() => { if (!document.hidden) nicsvcRefresh(); }, 5000);
  } else {
    if (NICSVC_POLL) { clearInterval(NICSVC_POLL); NICSVC_POLL = null; }
  }
}
async function nicsvcKeep() {
  const p = NICSVC && NICSVC.pending;
  try { await post("/net/services/confirm", {}); } catch (e) { toast(e.message); await nicsvcRefresh(); return; }
  clearInterval(NICSVC_TICK); NICSVC_TICK = null;
  toast(p ? `השינוי נשמר — ${(p.previous || []).includes("console") && !p.applied.includes("console") ? `הקונסולה כבויה ב-${p.interface}` : `${p.interface} עודכן`}.${nicsvcYou().address ? ` נכנסים מ-${nicsvcYou().address}.` : ""}` : "השינוי נשמר");
  await nicsvcRefresh();
}
async function nicsvcUndo() {
  try { await post("/net/services/revert", {}); } catch (e) { toast(e.message); }
  clearInterval(NICSVC_TICK); NICSVC_TICK = null;
  await nicsvcRefresh();
}
