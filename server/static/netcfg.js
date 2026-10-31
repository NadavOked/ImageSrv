/* ImageSrv — לשונית הרשת, הצד השני של הכרטיס: איך השרת עצמו מחובר
   (‏#55–#57). ‏net.js מציג מה השרת *מחלק*; כאן הכתובת שלו, השער, ה-DNS
   והנתיבים הסטטיים — כולם ל-`interfaces.d`, כלומר ששורדים אתחול.

   שני דברים שהמסך הזה עושה אחרת מכל מסך אחר בקונסולה:

   1. **הנורה אינה ההגדרה.** לכל שורה יש "מה ביקשנו" ולידו "מה
      ‏`ip addr` מראה עכשיו". כשהשניים נבדלים — מה שנקרא הוא הנכון,
      והשורה אדומה. ‏"נשמר" על כתובת שלא זזה הוא בדיוק המסך שמפיל
      מפעיל (עיקרון 5).
   2. **ספירה לאחור אחרי שינוי מסוכן.** שינוי שיכול לנתק את הקונסולה
      מוחזר תוך דקה אם לא נאמר במפורש "אני עדיין רואה אותה" (‏#56).
      הכפתור הזה הוא הראיה החיובית; היעדר ניתוק אינו ראיה, כי אולי
      המפעיל פשוט לא הצליח להגיע כדי לומר זאת. */
"use strict";

/* NETCFG מוצהר ב-console.js — כאן רק ממלאים אותו. redeclaration של let
   בין סקריפטים קלאסיים מפילה את הקובץ ב-SyntaxError. */
let NETCFG_TICK = null;

const MASKS = ["255.255.255.0", "255.255.255.128", "255.255.254.0",
               "255.255.252.0", "255.255.0.0", "255.0.0.0", "255.255.255.252"];

/* --- הבאנר של ההחזרה: הדבר היחיד שחשוב יותר מהטבלה ------------------------ */

function rollbackBanner(rb) {
  if (rb.corrupt) {
    return `<div class="sheet-note danger">סמן ההחזרה על הדיסק פגום ולא ניתן
      לפענוח. הוא לא יפעיל החזרה — בדקו את הרשת ידנית.</div>`;
  }
  if (rb.pending) {
    return `<div class="sheet-note danger" id="netcfg-pending">
      <b>ההגדרה של ${esc(rb.interface)} ממתינה לאישור.</b>
      אם לא תאשרו שהקונסולה עדיין נגישה, ההגדרה הקודמת תחזור בעוד
      <b id="netcfg-count">${rb.seconds_left}</b> שניות — גם אם השרת ייפול,
      וגם אם המכונה תאותחל.
      <button class="btn primary" id="netcfg-confirm" type="button" onclick="confirmNetRollback()">אשר שהחיבור עובד</button>
      </div>`;
  }
  if (!rb.armed) {
    // ‏apple-pending-4: אותו רכיב אזהרה כמו שאר הדף; שגיאת המערכת הגולמית — מאחורי "פרטים טכניים"
    return UI.noteTech("err", `ההחזרה האוטומטית (${esc(rb.unit)}) אינה פעילה`, rb.armed_detail,
      ". עד שתותקן, כל שינוי שיכול לנתק את הקונסולה ייחסם — ראו docs/server-install.md.");
  }
  return "";
}

function startCountdown() {
  clearInterval(NETCFG_TICK);
  const el = document.getElementById("netcfg-count");
  if (!el) return;
  NETCFG_TICK = setInterval(() => {
    const left = Math.max(0, Number(el.textContent) - 1);
    el.textContent = left;
    // כשהזמן נגמר טוענים מחדש: הזרוע כבר החזירה, והמסך חייב להראות
    // את מה שיש עכשיו ולא את מה שביקשנו.
    // כשהזמן נגמר טוענים מחדש. כשל טעינה כאן אינו "הרענון הצליח" —
    // הבאנר "ממתין לאישור" תקוע ב-0 שניות ייראה כאילו הכל תקין. מודיעים
    // שלא הצלחנו לקרוא את המצב, ולא בולעים בשקט (#517, עיקרון 5).
    if (left === 0) {
      clearInterval(NETCFG_TICK);
      loadNetcfg()
        .then(() => { if (typeof refreshNetPages === "function") refreshNetPages(); })
        .catch((error) =>
          toast("לא הצלחנו לרענן את מצב הרשת אחרי ההחזרה: " + error.message));
    }
  }, 1000);
}

/* --- הטבלה ---------------------------------------------------------------- */

async function loadNetcfg() {
  NETCFG = await api("/net/config");
  const live = NETCFG.live;
  const foot = live.checked
    ? `נתיבים כרגע: ${live.routes.join(" · ") || "אין"} · ‏DNS: `
      + `${live.nameservers.join(", ") || "אין"}`
    : `המצב בפועל לא נקרא (${live.reason}) — אף שורה בטבלה אינה מאומתת`;
  const notSourced = NETCFG.sourced === false
    ? `<div class="sheet-note danger">‏/etc/network/interfaces אינו טוען את
       interfaces.d — כל מה שנכתב שם לא ייקרא באתחול.</div>` : "";

  // ‏#761: אין יותר פאנל/טבלה נפרדים לכתובת השרת — רק הבאנר החשוב (ההחזרה)
  // וההודעה על interfaces.d, מעל הטבלה המאוחדת (#net-table).
  const banner = $("#net-banner");
  if (banner) banner.innerHTML = rollbackBanner(NETCFG.rollback) + notSourced
    + `<p class="pad sub" dir="ltr">${esc(foot)}</p>`;
  renderRoutes();
  startCountdown();
}

async function confirmNetRollback() {
  try {
    await post("/net/config/confirm",
               { interface: NETCFG.rollback.interface });
    toast("אושר — ההגדרה נשארת");
  } catch (error) { toast(error.message); }
  await loadNetcfg();
  if (typeof refreshNetPages === "function") refreshNetPages();
}

/* --- עריכת הכתובת --------------------------------------------------------- */

function bodyOf(row, over) {
  return { mode: row.mode, address: row.address, netmask: row.netmask,
           gateway: row.gateway, dns: row.dns, routes: row.routes,
           confirm: row.name, ...over };
}

async function saveAddress(name, body) {
  const result = await put(`/net/config/${encodeURIComponent(name)}`, body);
  if (result.apply_error) toast("ההחלה נכשלה: " + result.apply_error);
  else if (!result.verified)
    toast("נכתב — אבל המצב בפועל לא תואם: " + result.mismatches.join(" · "));
  else if (result.rollback.pending)
    toast("הוחל. אשרו תוך דקה שהקונסולה עדיין נגישה, אחרת יוחזר.");
  else toast("הוחל, ואומת מול ip addr");
  await loadNetcfg();
  if (typeof refreshNetPages === "function") refreshNetPages();
}

/* ‏"/24" → מסכה: הכתובת החיה (‏"10.44.9.10/24") ממלאת את שדות הכתובת הידנית. */
function bitsToMask(bits) {
  const n = Math.max(0, Math.min(32, Number(bits) || 0));
  const full = 0xffffffff << (32 - n) >>> 0;
  return [24, 16, 8, 0].map((s) => (full >>> s) & 255).join(".");
}

function liveReadback(row) {
  const live = NETCFG && NETCFG.live;
  if (!live || !live.checked) {
    return `<div class="live-readback">בפועל כעת: <b>לא ידוע — קריאת מצב הרשת נכשלה</b></div>`;
  }
  const addr = (row.live_addresses || []).join(" · ");
  return `<div class="live-readback">בפועל כעת: <b>${esc(addr) || "אין כתובת"}</b></div>`;
}

/* --- ‏#1263: "הגדרת כרטיס…" — אופן הגדרה אחד מתוך שלושה -------------------
   רשת הפצה · DHCP מהרשת · כתובת ידנית (הכרעת נדב 10/10). כרטיס הניהול
   (נבחר בהתקנה, ‏NETCFG.management_interface) — DHCP או ידנית בלבד.
   הגדרות רשת ההפצה עצמן (רשת, מסכה, טווח, חכירה) נערכות בחלון של לשונית
   "רשת הפצה" (‏editDeployNet ב-net.js): כאן "רשת הפצה" מסמן את הכרטיס
   וממשיך לשם. בלי הקלדה (נדב 10/10) — השם בכותרת, והשרת ממשיך
   לקבל `confirm` = השם, מהמסך ולא מהמקלדת. כרטיס הניהול מוגן בהחזרה
   האוטומטית של ‏#56 (60 שניות), שלא השתנתה. "+ כרטיס" פותח את אותו חלון
   עם בחירה מתוך הכרטיסים שזוהו. */

const NIC_MODES = [["deploy", "רשת הפצה"], ["dhcp", "DHCP מהרשת"], ["static", "כתובת ידנית"]];
/* אופן ההגדרה כפי שהוא היום: DHCP מלא דלוק = רשת הפצה; אחרת מה שנשמר
   בהגדרת הכתובת. ‏"manual" = לא מנוהל מהקונסולה (שרת שהגדרתו קודמת). */
function nicModeOf(n, row) {
  if (n && n.enabled) return "deploy";
  if (row && (row.mode === "static" || row.mode === "dhcp")) return row.mode;
  return "manual";
}
function netcfgRowOf(name) {
  return ((NETCFG && NETCFG.interfaces) || []).find((r) => r.name === name) || null;
}

/* בעיות הטופס בעברית — רשימה ריקה = מותר לשמור. השרת בודק שוב (ובעוד
   דברים: חפיפה לכרטיס אחר, שער בר-השגה); זה רק כדי שהכפתור יידע. */
function nicFormProblems(mode, v) {
  if (mode !== "static") return [];
  const found = [];
  if (!validIp(v.address)) found.push("כתובת IP אינה תקינה");
  if (!validMask(v.netmask)) found.push("מסכה אינה תקינה");
  for (const [key, label] of [["gateway", "שער"], ["dns1", "DNS ראשי"], ["dns2", "DNS משני"]]) {
    if (String(v[key] || "").trim() && !validIp(v[key])) found.push(`${label} אינו כתובת תקינה`);
  }
  return found;
}

function nicLinkText(n) {
  const speed = typeof n.speed_mbps === "number" && n.speed_mbps > 0
    ? (n.speed_mbps >= 1000 ? `${n.speed_mbps / 1000} Gb` : `${n.speed_mbps} Mb`) : "";
  const state = n.state === "up" ? "מחובר" : n.state === "down" ? "מנותק" : "קישור לא נקרא";
  return [speed, state].filter(Boolean).join(" · ");
}

/* שדות הכתובת הידנית מוצגים רק באופן "כתובת ידנית"; ההערות לפי האופן;
   הכפתור נדלק כשהשדות תקינים, והסיבה בפס התחתון (#sf-hint). ‏`.sbody` נבנה
   מחדש בכל sheet(), ולכן המאזינים עליו אינם מצטברים בין חלונות. כל שדה
   יושב ב-`.n-f` (מסגרת החלונות של שלב 1) — מסתירים אותו, לא את התווית. */
function wireNicWindow(start, now) {
  const form = document.getElementById("sheet");
  const body = form && form.querySelector(".sbody");
  if (!body) return;
  const value = (id) => { const el = document.getElementById("sf-" + id); return el ? el.value : ""; };
  const sync = () => {
    const picked = form.querySelector('input[name="sf-mode"]:checked');
    const mode = picked ? picked.value : start;
    for (const id of ["address", "netmask", "gateway", "dns1", "dns2"]) {
      const el = document.getElementById("sf-" + id);
      const wrap = el && (el.closest(".n-f") || el.closest("label"));
      if (wrap) wrap.style.display = mode === "static" ? "" : "none";
    }
    body.querySelectorAll("[data-nic-pane]").forEach((el) => {
      el.hidden = el.dataset.nicPane === "leave-deploy" ? mode === "deploy" : el.dataset.nicPane !== mode;
    });
    const values = { address: value("address"), netmask: value("netmask"), gateway: value("gateway"),
                     dns1: value("dns1"), dns2: value("dns2") };
    const problems = nicFormProblems(mode, values);
    if (now === "deploy" && mode !== "deploy") problems.unshift("כבו קודם את ה-DHCP של ההפצה");
    const submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = problems.length > 0;
    const hint = document.getElementById("sf-hint");
    if (hint) hint.textContent = problems[0] || "";
  };
  body.addEventListener("input", sync);
  body.addEventListener("change", sync);
  sync();
}

function editNicConfig(name, { add = false } = {}) {
  // ‏#1264: כרטיס שחבר ב-bond של רשת ההפצה אינו "כרטיס פנוי" — הוא מוסר בחלון רשת ההפצה.
  const fresh = add ? (NICS || []).filter((n) => n.present && !n.enabled && !n.proxy && !n.description
    && !(netcfgRowOf(n.name) || {}).member_of) : [];
  if (add && !fresh.length) { toast("כל הכרטיסים שמחוברים כבר מוגדרים."); return; }
  const target = add ? fresh[0].name : name;
  const nic = (NICS || []).find((n) => n.name === target) || { name: target, enabled: false };
  const row = netcfgRowOf(target);
  const now = add ? "manual" : nicModeOf(nic, row);
  const mgmt = !add && isMgmtNic(target);
  // כרטיס לא מנוהל: הכתובת שהוא נושא בפועל ממלאת את "כתובת ידנית" — לא שדות ריקים.
  const [liveIp, liveBits] = (((row && row.live_addresses) || [])[0] || "").split("/");
  const st = row && row.mode === "static" ? row
    : { address: liveIp || "", netmask: liveBits ? bitsToMask(liveBits) : MASKS[0], gateway: "", dns: [] };
  const start = add ? "dhcp" : now === "manual" ? (liveIp ? "static" : "dhcp") : now;
  const seconds = (NETCFG && NETCFG.rollback && NETCFG.rollback.window_seconds) || 60;
  const note = (mgmt ? `<div class="sheet-note">זה כרטיס הניהול — נבחר בהתקנה, ודרכו נכנסים לקונסולה. שינוי כתובת עלול לנתק אותך: אם לא תאשר תוך ${esc(seconds)} שניות, ההגדרה הקודמת תחזור.</div>` : "")
    + (!add && now === "manual" ? `<div class="sheet-note">הכרטיס אינו מנוהל היום מהקונסולה. שמירה תכתוב לו קובץ ב-/etc/network/interfaces.d, ששורד אתחול.</div>` : "")
    + (add ? "" : liveReadback(row || {}))
    + `<div class="sheet-note" data-nic-pane="deploy">הגדרות רשת ההפצה — כתובת הרשת, טווח ה-DHCP והחכירה — נערכות בחלון של לשונית "רשת הפצה", שייפתח מכאן. השרת מקבל על הכרטיס את הכתובת הראשונה ברשת.</div>`
    + `<div class="sheet-note" data-nic-pane="dhcp">הכרטיס יקבל כתובת, מסכה, שער ו-DNS מה-DHCP של הרשת שהוא מחובר אליה.</div>`
    + (now === "deploy" ? `<div class="sheet-note danger" data-nic-pane="leave-deploy">הכרטיס מחלק היום DHCP להפצה. כדי לשנות את אופן ההגדרה, כבו קודם את ה-DHCP ("כיבוי DHCP…" בלשונית רשת הפצה).</div>` : "");
  let added = false;
  sheet({
    title: add ? "הוספת כרטיס" : `הגדרת כרטיס — ${target}`,
    sub: mgmt ? "כרטיס הניהול · רשת השרתים" : "נשמר ושורד אתחול",
    size: "mid",
    note,
    fields: [
      ...(add ? [{ id: "nic", label: "כרטיס שזוהה", type: "select", value: target,
                   options: fresh.map((n) => ({ value: n.name, label: `${n.name} — ${nicLinkText(n)}` })) },
                 { id: "description", label: "תיאור", placeholder: "למשל: רשת כיתה 3" }] : []),
      { id: "mode", label: "אופן הגדרה", type: "radio", value: start,
        options: NIC_MODES.filter(([m]) => !(mgmt && m === "deploy")).map(([value, label]) => ({ value, label })) },
      { id: "address", label: "כתובת IP", value: st.address, dir: "ltr" },
      { id: "netmask", label: "מסכה", value: st.netmask || MASKS[0], dir: "ltr" },
      { id: "gateway", label: "שער (לא חובה)", value: st.gateway, dir: "ltr" },
      { id: "dns1", label: "DNS ראשי (לא חובה)", value: (st.dns || [])[0] || "", dir: "ltr" },
      { id: "dns2", label: "DNS משני (לא חובה)", value: (st.dns || [])[1] || "", dir: "ltr" },
    ],
    submitLabel: add ? "הוספה והחלה" : "שמירה והחלה",
    onSubmit: async (v) => {
      const nicName = add ? v.nic : target;
      const problems = nicFormProblems(v.mode, v);
      if (problems.length) throw new Error(problems.join(" · "));
      if (v.mode === "deploy" && isMgmtNic(nicName)) {
        throw new Error(`${nicName} הוא כרטיס הניהול — DHCP מהרשת או כתובת ידנית בלבד`);
      }
      if (now === "deploy" && v.mode !== "deploy") {
        throw new Error("כבו קודם את ה-DHCP של ההפצה על הכרטיס (\"כיבוי DHCP…\" בלשונית רשת הפצה)");
      }
      if (add && !added) {           // ניסיון חוזר אחרי שגיאה לא מוסיף פעמיים (409)
        await post("/net/interfaces", { name: nicName, description: v.description });
        added = true;
      }
      if (v.mode === "deploy") {
        // החלון הזה נסגר כש-onSubmit חוזר; חלון רשת ההפצה נפתח אחריו.
        setTimeout(() => editDeployNet(nicName), 0);
        return;
      }
      const base = netcfgRowOf(nicName)
        || { name: nicName, mode: "manual", address: "", netmask: MASKS[0], gateway: "", dns: [], routes: [] };
      const body = v.mode === "static"
        ? bodyOf(base, { mode: "static", address: v.address.trim(), netmask: v.netmask.trim(),
                         gateway: v.gateway.trim(), dns: [v.dns1, v.dns2].map((s) => s.trim()).filter(Boolean) })
        : bodyOf(base, { mode: "dhcp", address: "", netmask: MASKS[0], gateway: "", dns: [] });
      body.confirm = nicName;
      const preview = await post(`/net/config/${encodeURIComponent(nicName)}/preview`, body);
      if (preview.problems.length) throw new Error(preview.problems.join(" · "));
      await saveAddress(nicName, body);
    },
  });
  wireNicWindow(start, now);
}

/* --- נתיבים סטטיים (‏#57): רשימה, הוספה, ומחיקה לכל שורה ------------------ */

function renderRoutes() {
  const mount = $("#netroutes-body");
  if (!mount || !NETCFG) return;
  const all = [];
  NETCFG.interfaces.forEach((nic) =>
    (nic.routes || []).forEach((r, index) => all.push({ nic, r, index })));
  const live = new Set(NETCFG.live.routes || []);
  mount.innerHTML = `
    <table>
      <thead><tr><th></th><th>יעד</th><th>מסכה</th><th>שער</th><th>כרטיס</th>
        <th></th></tr></thead>
      <tbody>${all.map(({ nic, r, index }) => {
        // אותה ראיה חיובית: הנתיב נחשב קיים רק אם הוא בטבלת הניתוב.
        const seen = live.has(`${r.destination}/${maskBits(r.netmask)} via ${r.gateway}`);
        return `<tr>
          <td><span class="hlight ${NETCFG.live.checked ? (seen ? "ok" : "bad") : "bad"}"></span></td>
          <td class="mono" dir="ltr">${esc(r.destination)}</td>
          <td class="mono" dir="ltr">${esc(r.netmask)}</td>
          <td class="mono" dir="ltr">${esc(r.gateway)}</td>
          <td dir="ltr">${esc(nic.name)}</td>
          <td><button class="btn danger" data-route-del="${esc(nic.name)}"
                data-route-index="${index}">מחק</button></td></tr>`;
      }).join("") || `<tr><td colspan="6">אין נתיבים סטטיים.
        נתיב שנוסף כאן נשאר גם אחרי אתחול.</td></tr>`}</tbody>
    </table>`;

  document.querySelectorAll("[data-route-del]").forEach((b) => b.onclick = () =>
    routeDeleteSheet(b.dataset.routeDel, Number(b.dataset.routeIndex)));
}

/* ‏#954 גל 8: גם דף הרשת (console.js, כרטיס "נתיבים סטטיים") קורא לזה בשם. */
function routeDeleteSheet(nameEnc, index) {
  let name = nameEnc; try { name = decodeURIComponent(nameEnc); } catch (e) {}
  const nic = NETCFG.interfaces.find((n) => n.name === name);
  if (!nic || !nic.routes[index]) { toast("הנתיב לא נמצא"); return; }
  const gone = nic.routes.filter((_, i) => i !== index);
  sheet({
    title: "למחוק את הנתיב הסטטי?",
    sub: `${nic.routes[index].destination} · ${nic.name}`,
    danger: true, submitLabel: "מחיקת הנתיב",
    note: impactList([["b", "הנתיב יוסר מהקובץ ומטבלת הניתוב"]]),
    onSubmit: () => saveAddress(nic.name, bodyOf(nic, { routes: gone })),
  });
}

function maskBits(netmask) {
  return (netmask || "").split(".")
    .reduce((bits, part) => bits + ((Number(part) >>> 0).toString(2).match(/1/g) || []).length, 0);
}

function addRoute() {
  const usable = NETCFG.interfaces.filter((n) => n.mode === "static");
  if (!usable.length) {
    toast("נתיב סטטי דורש כרטיס עם כתובת סטטית. הגדירו כתובת קודם.");
    return;
  }
  sheet({
    title: "הוספת נתיב סטטי",
    sub: "נכתב לקובץ של הכרטיס, ולכן נשאר גם אחרי אתחול.",
    fields: [
      { id: "destination", label: "רשת היעד", dir: "ltr",
        placeholder: "10.20.0.0" },
      { id: "netmask", label: "מסכה", type: "select", value: MASKS[0],
        options: MASKS.map((m) => ({ value: m, label: m })) },
      { id: "gateway", label: "דרך מי (שער)", dir: "ltr",
        placeholder: "10.10.10.9" },
      { id: "name", label: "על איזה כרטיס", type: "select",
        value: usable[0].name,
        options: usable.map((n) => ({ value: n.name,
                                      label: `${n.name} · ${n.address}` })) },
    ],
    submitLabel: "הוסף",
    onSubmit: async (v) => {
      const nic = NETCFG.interfaces.find((n) => n.name === v.name);
      const body = bodyOf(nic, {
        routes: [...nic.routes, { destination: v.destination,
                                  netmask: v.netmask, gateway: v.gateway }],
      });
      const preview = await post(
        `/net/config/${encodeURIComponent(nic.name)}/preview`, body);
      if (preview.problems.length) throw new Error(preview.problems.join(" · "));
      await saveAddress(nic.name, body);
    },
  });
}

const netrouteAdd = $("#netroute-add");
if (netrouteAdd) netrouteAdd.addEventListener("click", () => addRoute());
const netRefresh = $("#net-refresh");
if (netRefresh) netRefresh.addEventListener("click", () =>
  Promise.all([loadNet(), loadNetcfg()])
    .then(() => { if (typeof refreshNetPages === "function") refreshNetPages(); })
    .catch((error) => toast("רענון נכשל: " + error.message)));
