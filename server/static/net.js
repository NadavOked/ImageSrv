/* ImageSrv — לשונית הרשת (אפיון סעיף 24): מה השרת ראה בפועל, ומה השרת
   מחלק — DHCP של רשת ההפצה, כבוי כברירת מחדל; הכיבוי מאחורי חלון אישור, וההגדרה בחלון "רשת הפצה" (‏#1263). */
"use strict";

// --- כרטיסי רשת ו-DHCP -------------------------------------------------------

//: כרטיסי הרשת האחרונים שנקראו (‏/net/interfaces) — לחלונות "הגדרת כרטיס"
// ו"רשת הפצה" (‏#1263) ולדף הרשת.
let NICS = [];

/* ‏#762: תווית מצב DHCP חי — ‏dhcp_live_label מגיע מהשרת (אמת נקראת, לא
   ניחוש). ‏dhcp_diverged מסמן פער בין מה שנשמר לבין מה שבאמת רץ. */
function dhcpLiveClass(state) {
  if (state === "serving") return "ok";
  if (state === "configured_not_running") return "warn";
  if (state === "off") return "off";
  return "warn";
}

/* ‏#1263: עזרי IPv4 וכרטיס הניהול — כאן ולא ב-netcfg.js, כי net.js נטען ראשון
   וגם דפים שטוענים רק אותו (פורטים) פותחים את חלון רשת ההפצה. */
const IPV4_RE = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

function validIp(text) { return IPV4_RE.test(String(text || "").trim()); }
function ipNum(text) { return String(text).trim().split(".").reduce((a, p) => ((a << 8) | Number(p)) >>> 0, 0); }
function numIp(n) { return [24, 16, 8, 0].map((s) => (n >>> s) & 255).join("."); }
/* מסכה = רצף אחדות. ‏255.0.255.0 נראית כמו מסכה ואינה (כמו netcfg._valid_mask). */
function validMask(text) {
  return validIp(text) && /^1*0*$/.test(ipNum(text).toString(2).padStart(32, "0"));
}
function networkOf(ip, netmask) {
  return validIp(ip) && validMask(netmask) ? numIp((ipNum(ip) & ipNum(netmask)) >>> 0) : "";
}
/* מה שהשרת יגזור (‏netcfg.deploy_address): הכתובת הראשונה ברשת. ‏"" = לא
   כתובת רשת, או רשת קטנה מדי (‏/31, ‏/32) — כמו השרת, בלי עיגול שקט. */
function firstHost(network, netmask) {
  if (!validIp(network) || !validMask(netmask) || ipNum(netmask) > 0xfffffffc) return "";
  if (networkOf(network, netmask) !== String(network).trim()) return "";
  return numIp(ipNum(network) + 1);
}

function isMgmtNic(name) {
  return !!(NETCFG && NETCFG.management_interface && NETCFG.management_interface === name);
}
async function saveNic(name, body) {
  const result = await put(`/net/interfaces/${encodeURIComponent(name)}`, body);
  if (result.apply_error) toast("נשמר, אבל dnsmasq לא עודכן: " + result.apply_error);
  else toast("הגדרת DHCP עודכנה");
  // ‏#1088: ההדלקה הראשונה הגדירה את רשת ההפצה — מה הושלם, ומה לא.
  // ‏#1121: השלמה שנכשלה היא 500 (נזרק למעלה, שום דבר לא נרשם); ‏`deploy`
  // בתשובה מגיע רק כשהכול הושלם ונרשם.
  const d = result.deploy;
  if (d && d.ok) {
    toast(`רשת ההפצה הוגדרה על ${name} (${d.url}) — השרת מתאתחל, הקונסולה תחזור תוך כמה שניות`);
    if (typeof NET_DEPLOY !== "undefined") NET_DEPLOY = { configured: true, source: "console", interface: name, url: d.url, hint: null };
  }
}

/* ‏#1263: חלון "רשת הפצה" — הגדרות ה-DHCP של ההפצה על הכרטיס שסומן.
   המפעיל נותן כתובת רשת + מסכה, וכתובת השרת **נגזרת** (הראשונה ברשת,
   ‏firstHost ב-netcfg.js = ‏netcfg.deploy_address בשרת) — היא אינה שדה.
   בלי proxy (הכרעת נדב 10/10: לכל היותר ברשת כיתות, v1.2), בלי שער ובלי
   ‏DNS (רשת סגורה: dnsmasq רץ ב-port=0 והשרת אינו מנתב), ובלי חלון
   אישור נוסף. השמירה היא בקשה אחת — ‏PUT /net/config/{שם}/deploy — שמחילה את
   הכתובת ואת ה-DHCP יחד, כדי שלא יהיה רגע של כרטיס שמכריז על כתובת
   שאינה שלו. שער/DNS שנשמרו בהגדרה קיימת נשארים בשרת כפי שהם. */
const LEASE_UNITS = [["h", "שעות"], ["m", "דקות"], ["d", "ימים"]];
/* ‏#2889: תקרת החכירה — אותה של השרת (‏dhcp.MAX_LEASE_DAYS). */
const MAX_LEASE_DAYS = 365;
const LEASE_DAYS_PER = { m: 1 / 1440, h: 1 / 24, d: 1 };

function deployFormProblems(v) {
  const found = [];
  // ‏#2668: על כרטיס הניהול כתובת השרת היא הכתובת הקיימת שלו, לא נגזרת.
  const server = v.server || firstHost(v.network, v.netmask);
  if (!validIp(v.network)) found.push("כתובת הרשת אינה תקינה");
  else if (!validMask(v.netmask)) found.push("מסכה אינה תקינה");
  else if (!server) {
    const net = networkOf(v.network, v.netmask);
    found.push(net !== String(v.network).trim()
      ? `${String(v.network).trim()} אינה כתובת רשת — עם המסכה הזו הרשת היא ${net}`
      : "הרשת קטנה מדי — אין בה מקום לשרת ולטווח");
  }
  if (!validIp(v.range_start)) found.push("תחילת הטווח אינה תקינה");
  if (!validIp(v.range_end)) found.push("סוף הטווח אינו תקין");
  if (server && validIp(v.range_start) && validIp(v.range_end)) {
    const net = v.network.trim(), mask = v.netmask.trim();
    const start = ipNum(v.range_start), end = ipNum(v.range_end), me = ipNum(server);
    if (start > end) found.push("תחילת הטווח גדולה מסופו");
    if (networkOf(v.range_start, mask) !== net || networkOf(v.range_end, mask) !== net) {
      found.push("הטווח חייב להיות בתוך הרשת");
    }
    if (start <= me && me <= end) found.push(`כתובת השרת (${server}) נמצאת בתוך הטווח`);
    // ‏#2889: כתובת הרשת והשידור אינן כתובות של מחשב (כמו dhcp.validate בשרת).
    const base = ipNum(networkOf(net, mask)), bcast = (base | (~ipNum(mask) >>> 0)) >>> 0;
    if (ipNum(mask) < 0xfffffffe) {
      if (start === base) found.push(`הטווח מתחיל בכתובת הרשת (${numIp(base)})`);
      if (end === bcast) found.push(`הטווח מסתיים בכתובת השידור (${numIp(bcast)})`);
    }
  }
  if (!/^\d+$/.test(String(v.lease_n || "").trim()) || Number(v.lease_n) < 1) {
    found.push("משך חכירה: מספר שלם חיובי");
  } else if (Number(v.lease_n) * (LEASE_DAYS_PER[v.lease_u || "h"] || 1 / 24) > MAX_LEASE_DAYS) {
    found.push(`משך חכירה ארוך מדי — עד ${MAX_LEASE_DAYS} ימים`);
  }
  return found;
}

/* ‏#2889 + ‏#2883: מה שאינו חוסם את השמירה אבל המפעיל חייב לראות לפניה. */
function deployFormWarnings(v, before) {
  const found = [];
  const server = v.server || firstHost(v.network, v.netmask);
  if (server && validIp(v.range_start) && validIp(v.range_end)) {
    const size = ipNum(v.range_end) - ipNum(v.range_start) + 1;
    const known = typeof MACHINES !== "undefined" && Array.isArray(MACHINES) ? MACHINES.length : null;
    if (size === 1) found.push("הטווח הוא כתובת אחת — רק מחשב אחד יקבל כתובת בכל פעם");
    if (known !== null && size > 0 && size < known) {
      found.push(`בטווח ${size} כתובות, ורשומים ${known} מחשבים — לא כולם יקבלו כתובת יחד`);
    }
  }
  // ‏#2883: רשת הפצה קיימת שעוברת לכתובת אחרת.
  if (before && before.enabled && (String(v.network || "").trim() !== String(before.network || "").trim()
      || String(v.netmask || "").trim() !== String(before.netmask || "").trim())) {
    found.push("שינוי כתובת רשת ההפצה: מחשבים שכבר קיבלו כתובת ברשת הישנה יאבדו את השרת עד שיאותחלו, "
      + "והשרת יאותחל על הכתובת החדשה. בזמן סבב או קליטה השרת מסרב לשינוי.");
  }
  return found;
}

function deployServerNote(network, netmask) {
  const ip = firstHost(network, netmask);
  return ip ? `השרת יקבל אוטומטית את <b dir="ltr">${esc(ip)}</b> — הכתובת הראשונה ברשת.`
    : "כתובת השרת נגזרת מהרשת: הכתובת הראשונה בה.";
}

/* ‏#2668: שרת עם כרטיס יחיד — כרטיס הניהול הוא גם רשת ההפצה. הכתובת הידנית שלו
   נשארת כתובת השרת (הקונסולה ממשיכה בה), הרשת היא הרשת שלה ואינה שדה, והשמירה
   היא כפתור אדום עם ההסבר (בלי הקלדת מילה, #1270). השרת בודק שאין DHCP אחר,
   ו"לא הצלחנו לבדוק" הוא סירוב. ‏null = אין לכרטיס כתובת ידנית — אין חלון. */
function mgmtDeployBase(name) {
  const row = ((NETCFG && NETCFG.interfaces) || []).find((r) => r.name === name);
  if (!row || row.mode !== "static" || !validIp(row.address) || !validMask(row.netmask)) return null;
  return { server: row.address, netmask: row.netmask, network: networkOf(row.address, row.netmask) };
}

function editDeployNet(name) {
  const nic = (NICS || []).find((n) => n.name === name);
  if (!nic) { toast("כרטיס לא נמצא"); return; }
  const mgmt = isMgmtNic(name) ? mgmtDeployBase(name) : null;
  if (isMgmtNic(name) && !mgmt) {
    toast(`${name} הוא כרטיס הניהול: כדי שתהיה עליו גם רשת ההפצה, קבעו לו קודם כתובת ידנית (הגדרת כרטיס ← כתובת ידנית)`);
    return;
  }
  const netmask = mgmt ? mgmt.netmask : nic.netmask || "255.255.255.0";
  const network = mgmt ? mgmt.network : networkOf(nic.server_ip, netmask);
  const lease = String(nic.lease || "12h").trim().toLowerCase().match(/^(\d+)([mhd])$/) || [null, "12", "h"];
  const firstTime = typeof NET_DEPLOY !== "undefined" && NET_DEPLOY && NET_DEPLOY.configured === false && !nic.enabled;
  const cards = mgmt ? null : deployCardsFields(name, nic);
  const initial = { network, netmask, range_start: nic.range_start || "", range_end: nic.range_end || "", lease_n: lease[1], lease_u: lease[2] };
  const fixed = mgmt ? { network, netmask, server: mgmt.server } : {};
  const mgmtNote = mgmt ? `<div class="sheet-note danger"><b>זה כרטיס הניהול — הקונסולה נכנסת דרכו.</b> בשרת עם כרטיס יחיד הוא יכול להיות גם רשת ההפצה, אבל אז ה-DHCP של ההפצה יענה לכל מחשב ברשת שלו (<b dir="ltr">${esc(network)}</b>): אם זו רשת המכללה, הוא ישבית אותה. לפני ההדלקה השרת בודק שאין ברשת שרת DHCP אחר — אם נמצא שרת, או שהבדיקה לא הצליחה לרוץ, ההדלקה נעצרת.</div>`
    + `<div class="sheet-note" id="deploy-server-ip">הכתובת של השרת נשארת <b dir="ltr">${esc(mgmt.server)}</b> — הקונסולה ממשיכה בה. ההגדרה הראשונה מאתחלת את השרת, והקונסולה תחזור תוך כמה שניות.</div>` : "";
  sheet({
    title: `רשת הפצה — ${name}`,
    sub: mgmt ? "כרטיס הניהול · שרת עם כרטיס יחיד" : "ה-DHCP למחשבי השיכפול והבנייה רץ על הכרטיס הזה",
    danger: !!mgmt,
    note: mgmt ? mgmtNote : (nic.trunk ? `<div class="sheet-note danger">הכרטיס מסומן כמחובר לרשת המכללה. DHCP כאן ישבית את הרשת.</div>`
      : !nic.enabled ? `<div class="sheet-note">ההגדרה המסוכנת ביותר במערכת: DHCP על רשת שכבר יש בה שרת משבית אותה. השרת יבדוק לפני ההדלקה אם מישהו כבר עונה.</div>` : "")
      + (firstTime ? `<div class="sheet-note">רשת ההפצה מוגדרת כאן <b>בפעם הראשונה</b>: השמירה משלימה את ההתקנה (grub.cfg, dnsmasq, חומת אש) ומאתחלת את השרת על כתובת ההפצה — הקונסולה תחזור תוך כמה שניות.</div>` : "")
      + `<div class="sheet-note" id="deploy-server-ip">${deployServerNote(network, netmask)}</div>`
      + `<div class="sheet-note danger" id="deploy-warn" hidden></div>`,
    size: "mid",
    fields: [
      ...(mgmt ? [] : [{ type: "section", label: "רשת ההפצה" },
        { id: "network", label: "כתובת רשת", value: network, dir: "ltr" },
        { id: "netmask", label: "מסכה", value: netmask, dir: "ltr" }]),
      { type: "section", label: "DHCP למחשבים" },
      { id: "range_start", label: "תחילת טווח", value: nic.range_start, dir: "ltr" },
      { id: "range_end", label: "סוף טווח", value: nic.range_end, dir: "ltr" },
      { id: "lease_n", label: "משך חכירה", value: lease[1], dir: "ltr" },
      { id: "lease_u", label: "יחידה", type: "select", value: lease[2],
        options: LEASE_UNITS.map(([value, label]) => ({ value, label })) },
      ...(nic.trunk && !nic.enabled ? [{ id: "confirm_trunk", type: "checkbox", value: false,
        label: "אני מבין שזו רשת המכללה ורוצה להדליק בכל זאת" }] : []),
      ...(cards ? cards.fields : []),
      // ‏#2970: כתובת שהוחלה וממתינה לאישור — האישור כאן, בחלון, וממשיכים ממנו ל-DHCP.
      { type: "html", html: `<div class="sheet-note danger" id="deploy-confirm" role="alert" hidden></div>` },
    ],
    submitLabel: mgmt ? "הדלקת רשת הפצה על כרטיס הניהול" : "שמירה והחלה",
    // ‏#2840: השמירה היא בקשה אחת שאורכת שניות (בהגדרה הראשונה — עד כדקה); המפעיל רואה שהיא רצה.
    busy: { label: "שומר ומחיל… (עד כדקה — לא לסגור את החלון)", after: 5,
            slow: "זה צפוי: השרת בודק שאין ברשת שרת DHCP אחר, מחיל את הכתובת על הכרטיס ומפעיל את ה-DHCP"
              + (firstTime || mgmt ? ", ובהגדרה הראשונה גם משלים את ההתקנה ומאתחל את עצמו." : ".") },
    onSubmit: async (raw) => {
      const v = { ...raw, ...fixed };
      const problems = deployFormProblems(v);
      if (problems.length) throw new Error(problems.join(" · "));
      // ‏#1264: רק הכרטיס השני השתנה — רק ‏/second; הרשת לא נשמרת שוב (ואין
      // אתחול dnsmasq מיותר). שניהם — קודם הרשת, ואז הכרטיס (שמעביר אותה ל-bond).
      const second = cards ? String(v.second || "") : "";
      const changedNet = Object.keys(initial).some((k) => String(v[k] || "").trim() !== String(initial[k] || "").trim());
      if (!cards || second === cards.current || changedNet) {
        await saveDeployNet(name, {
          network: v.network.trim(), netmask: v.netmask.trim(),
          range_start: v.range_start.trim(), range_end: v.range_end.trim(),
          lease: `${Number(v.lease_n)}${v.lease_u}`,
          confirm: name, confirm_trunk: v.confirm_trunk === true,
          ...(mgmt ? { confirm_management: true } : {}),
        });
      }
      if (cards && second !== cards.current) await saveDeploySecond(name, second, v.second_only_deploy === true);
    },
  });
  // כתובת השרת מתעדכנת תוך כדי הקלדה, והכפתור נדלק כשהשדות תקינים.
  const form = document.getElementById("sheet");
  const body = form && form.querySelector(".sbody");
  if (!body) return;
  const value = (id) => { const el = document.getElementById("sf-" + id); return el ? el.value : ""; };
  const sync = () => {
    const v = { network: value("network"), netmask: value("netmask"), range_start: value("range_start"),
                range_end: value("range_end"), lease_n: value("lease_n"), lease_u: value("lease_u"), ...fixed };
    const note = !mgmt && document.getElementById("deploy-server-ip");
    if (note) note.innerHTML = deployServerNote(v.network, v.netmask);
    const warn = document.getElementById("deploy-warn");
    if (warn) {
      const warnings = deployFormWarnings(v, nic.enabled ? { enabled: true, network, netmask } : null);
      warn.innerHTML = warnings.map(esc).join("<br>");
      warn.hidden = warnings.length === 0;
    }
    const problems = deployFormProblems(v);
    const submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = problems.length > 0;
    const hint = document.getElementById("sf-hint");
    if (hint) hint.textContent = problems[0] || "";
  };
  body.addEventListener("input", sync);
  sync();
}

/* ‏#1264: "כרטיסים" בחלון רשת ההפצה — כרטיס 1 (מוצג; החלפתו היא התקנה מחדש) וכרטיס
   2 (רשות). שני הכרטיסים פעילים יחד על אותה כתובת (bond, ‏balance-alb). רק אחרי
   שרשת ההפצה הוגדרה מהקונסולה — בהגדרה הראשונה השרת מתאתחל, ואין עוד על מה לחבר.
   כרטיס 2 = כרטיס שאינו מוגדר לשום דבר: לא ניהול, לא DHCP/proxy, לא רשת המכללה,
   בלי כתובת ובלי הגדרה. השרת בודק שוב, ובעוד דברים (#53 "מי כבר עונה"). */
function deployCardsFields(name, nic) {
  const d = typeof NET_DEPLOY !== "undefined" ? NET_DEPLOY : null;
  if (!d || d.configured !== true || d.source !== "console" || !nic.enabled) return null;
  const bond = NETCFG && NETCFG.deploy_bond && NETCFG.deploy_bond.name === name ? NETCFG.deploy_bond : null;
  const primary = bond ? bond.members[0].name : name;
  const current = bond && bond.members[1] ? bond.members[1].name : "";
  const rowOf = (n) => ((NETCFG && NETCFG.interfaces) || []).find((r) => r.name === n) || {};
  const free = (NICS || []).filter((n) => n.present && n.name !== primary && n.name !== name && !isMgmtNic(n.name)
    && !n.enabled && !n.proxy && !n.trunk && !(n.addresses || []).length
    && (rowOf(n.name).mode || "manual") === "manual" && !(rowOf(n.name).slaves || []).length
    && (!rowOf(n.name).member_of || n.name === current));
  const label = (n) => { const nicRow = (NICS || []).find((x) => x.name === n); return nicRow ? `${n} — ${nicLinkText(nicRow)}` : n; };
  const options = [{ value: "", label: "בלי כרטיס שני" }, ...free.map((n) => ({ value: n.name, label: label(n.name) }))];
  if (current && !options.some((o) => o.value === current)) options.push({ value: current, label: label(current) });
  return { current, fields: [
    { type: "section", label: "כרטיסים" },
    { id: "card1", label: "כרטיס 1", type: "select", value: primary, options: [{ value: primary, label: label(primary) }],
      help: "כרטיס ההפצה — החלפתו דורשת התקנה מחדש" },
    { id: "second", label: "כרטיס 2 (רשות)", type: "select", value: current, options,
      help: "שני הכרטיסים עובדים יחד על אותה כתובת: תעבורה למחשבים שונים מתחלקת ביניהם (סבב שיכפול יוצא בכרטיס אחד), ואם אחד נופל השני ממשיך — סבב שרץ נעצר לשניות ספורות ומסתיים כרגיל" },
    // ‏#2891: כשהבדיקה "האם כבר יש DHCP" לא יכולה לרוץ על הכרטיס (בלי קישור), השרת מסרב ומפנה
    // לתיבה הזו בשמה — זו הדרך היחידה להמשיך, והיא כבויה עד שמסמנים אותה.
    { id: "second_only_deploy", type: "checkbox", value: false, label: SECOND_ONLY_DEPLOY_LABEL,
      help: "רק אם השרת אמר שהבדיקה לא רצה, ורק אם הכרטיס מחובר לרשת ההפצה בלבד: DHCP על רשת שכבר יש בה שרת משבית אותה" },
  ] };
}

/* ‏#2891: אותה תווית שהשרת מצטט בסירוב (‏console/netcfg.py, ‏SECOND_ONLY_DEPLOY_LABEL). */
const SECOND_ONLY_DEPLOY_LABEL = "הכרטיס השני מחובר רק לרשת ההפצה — לדלג על הבדיקה";

async function saveDeploySecond(name, second, ignoreExisting = false) {
  const result = await put(`/net/config/${encodeURIComponent(name)}/second`,
                           ignoreExisting && second ? { second, ignore_existing: true } : { second });
  if (typeof refreshNetPages === "function") refreshNetPages();
  // ההעברה על המכונה לא אומתה — השרת החזיר את הקבצים הקודמים; החלון נשאר פתוח עם הסיבה.
  if (!result.deploy) throw new Error(result.apply_error || "הכרטיסים לא הוחלפו");
  if (result.apply_error) toast(`רשת ההפצה עברה ל-${result.interface}, אבל: ${result.apply_error}`);
  else toast(second
    ? `${second} נוסף לרשת ההפצה — שני הכרטיסים פעילים יחד על ${result.server_ip}. השרת מתאתחל, הקונסולה תחזור תוך כמה שניות`
    : `הכרטיס השני הוסר — רשת ההפצה על ${result.interface} בלבד. השרת מתאתחל, הקונסולה תחזור תוך כמה שניות`);
  if (result.deploy.ok && typeof NET_DEPLOY !== "undefined" && NET_DEPLOY) {
    NET_DEPLOY = { ...NET_DEPLOY, interface: result.interface, url: result.deploy.url };
  }
  await loadNet();
}

/* ‏#1274: "השלם הגדרה" — אחרי שהכרטיסים הוחלפו על המכונה וההשלמה נכשלה. אותו ‏/second
   עם אותו כרטיס: השרת מזהה שהמכונה כבר במצב הזה ומשלים בלבד (#1264). חלון אישור עם
   ההשלכות, בלי הקלדת מילה (#1270). כשל נוסף נשאר בחלון, בשם השלב שנפל. */
function completeDeploySecond(nameEnc, secondEnc) {
  const name = decodeURIComponent(nameEnc), second = decodeURIComponent(secondEnc);
  sheet({
    title: "להשלים את הגדרת רשת ההפצה?", sub: second ? `${name} — ${second} ככרטיס שני` : `${name} — כרטיס אחד`,
    note: impactList([["ok", "לא נוגעים בכרטיסים — הם כבר הוחלפו ואומתו"],
      ["w", "חומת האש ו-grub.cfg ייכתבו מחדש לשם הזה"], ["w", "השרת יאותחל — הקונסולה תחזור תוך כמה שניות"]]),
    submitLabel: "השלם הגדרה",
    onSubmit: async () => {
      const result = await put(`/net/config/${encodeURIComponent(name)}/second`, { second });
      if (typeof refreshNetPages === "function") refreshNetPages();
      if (!result.ok) throw new Error(result.apply_error || "ההשלמה לא הסתיימה");
      if (result.deploy && result.deploy.ok && typeof NET_DEPLOY !== "undefined" && NET_DEPLOY) {
        NET_DEPLOY = { ...NET_DEPLOY, interface: result.interface, url: result.deploy.url };
      }
      toast(`הגדרת רשת ההפצה על ${result.interface} הושלמה — השרת מתאתחל, הקונסולה תחזור תוך כמה שניות`);
    },
  });
}

async function saveDeployNet(name, body) {
  const result = await put(`/net/config/${encodeURIComponent(name)}/deploy`, body);
  if (typeof refreshNetPages === "function") refreshNetPages();
  // הכתובת לא הוחלה/לא אומתה/ממתינה לאישור — ה-DHCP לא שונה, והחלון נשאר פתוח עם הסיבה.
  if (!result.dhcp) {
    // ‏#2970: הכתובת הוחלה וממתינה לאישור — האישור בחלון הזה, והשמירה ממשיכה ממנו לבד.
    const rb = result.address && result.address.ok && result.address.rollback;
    if (rb && rb.pending) {
      showDeployConfirm(rb, result.server_ip);
      throw new Error("ה-DHCP עוד לא הוחל: אשרו כאן בחלון שהקונסולה עדיין נגישה, והשמירה תמשיך לבד");
    }
    throw new Error(result.apply_error || "הכתובת לא הוחלה — ה-DHCP לא שונה");
  }
  const d = result.deploy;
  // ‏#2883: השלמת הכתובת החדשה (grub.cfg, חומת אש, אתחול) נכשלה — זו לא תקלת dnsmasq.
  if (result.apply_error) toast((d && !d.ok ? "נשמר, אבל: " : "נשמר, אבל dnsmasq לא עודכן: ") + result.apply_error);
  else toast(`רשת ההפצה נשמרה על ${name} — כתובת השרת ${result.server_ip}`);
  if (d && d.ok) {
    toast(`רשת ההפצה הוגדרה על ${name} (${d.url}) — השרת מתאתחל, הקונסולה תחזור תוך כמה שניות`);
    if (typeof NET_DEPLOY !== "undefined") NET_DEPLOY = { configured: true, source: "console", interface: name, url: d.url, hint: null };
  }
  await loadNet();
}

/* ‏#2970: "ממתינה לאישור" בתוך חלון רשת ההפצה. האישור (‏/net/config/confirm) ואז אותה
   שמירה שוב, לבד (`requestSubmit`): הכתובת כבר שמורה, ולכן השמירה החוזרת ממשיכה ישר ל-DHCP.
   הספירה לאחור מהשרת (‏seconds_left), וכשהיא נגמרת — נאמר שההגדרה הקודמת חוזרת. */
let DEPLOY_CONFIRM_TICK = null;

function deployConfirmHtml(rb, ip) {
  return `<b>הכתובת <span dir="ltr">${esc(ip || "")}</span> הוחלה על ${esc(rb.interface)} וממתינה לאישור.</b> `
    + "אשרו שהקונסולה עדיין נגישה, והשמירה תמשיך ל-DHCP מיד, בלי לשמור שוב. בלי אישור, ההגדרה הקודמת "
    + `תחזור בעוד <b id="deploy-confirm-count">${Math.max(0, Number(rb.seconds_left) || 0)}</b> שניות. `
    + `<button class="btn primary" id="deploy-confirm-btn" type="button" onclick="confirmDeployAndContinue('${encodeId(rb.interface)}')">אשר שהחיבור עובד והמשך</button>`;
}

function showDeployConfirm(rb, ip) {
  const box = document.getElementById("deploy-confirm");
  if (!box) return;
  box.innerHTML = deployConfirmHtml(rb, ip);
  box.hidden = false;
  if (box.scrollIntoView) box.scrollIntoView({ block: "nearest" });
  clearInterval(DEPLOY_CONFIRM_TICK);
  DEPLOY_CONFIRM_TICK = setInterval(deployConfirmTick, 1000);
}

function deployConfirmTick() {
  const box = document.getElementById("deploy-confirm");
  const count = document.getElementById("deploy-confirm-count");
  if (!box || box.hidden || !count) { clearInterval(DEPLOY_CONFIRM_TICK); return; }   // החלון נסגר
  const left = Math.max(0, Number(count.textContent) - 1);
  count.textContent = left;
  if (left > 0) return;
  clearInterval(DEPLOY_CONFIRM_TICK);
  box.innerHTML = "<b>חלון האישור נסגר — ההגדרה הקודמת של הכרטיס חוזרת.</b> ה-DHCP לא שונה. סגרו את החלון, "
    + "בדקו בלשונית הרשת שהכתובת חזרה, ורק אז שמרו שוב.";
}

async function confirmDeployAndContinue(ifaceEnc) {
  const iface = decodeURIComponent(ifaceEnc);
  const button = document.getElementById("deploy-confirm-btn");
  if (button) button.disabled = true;
  try {
    await post("/net/config/confirm", { interface: iface });
  } catch (error) {
    if (button) button.disabled = false;
    const shown = document.getElementById("sf-error");
    if (shown) shown.textContent = error.message;
    return;
  }
  clearInterval(DEPLOY_CONFIRM_TICK);
  const box = document.getElementById("deploy-confirm");
  if (box) { box.hidden = true; box.innerHTML = ""; }
  toast("אושר — הכתובת נשארת, ממשיכים ל-DHCP");
  const form = document.getElementById("sheet");
  if (form && form.requestSubmit) form.requestSubmit();
}

async function loadNet() {
  // הלשונית כולה היא הכרטיסים: כל שורה היא כרטיס רשת של השרת,
  // שיכול להיות DHCP או לא. מכונות שנראו ברשת — בלשונית המחשבים.
  NICS = await api("/net/interfaces");
  const addBtn = $("#nic-add");
  if (addBtn) addBtn.onclick = addNic;
  if (typeof populateSidebarNics === "function") populateSidebarNics();
}

/* ‏#1263: "+ כרטיס" פותח את חלון "הגדרת כרטיס" עם בחירה מתוך מה שזוהה. */
function addNic() { editNicConfig(null, { add: true }); }

// --- "נראו ברשת" — מוצג בלשונית המחשבים (machines.js קורא לזה) -------------

function ago(iso) {
  if (!iso) return "—";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 90) return "עכשיו";
  if (seconds < 3600) return `לפני ${Math.round(seconds / 60)} דק'`;
  if (seconds < 86400) { const h = Math.round(seconds / 3600); return h === 1 ? "לפני שעה" : `לפני ${h} שע'`; }   // #1503: לא "1 שע'"
  return iso.slice(0, 10);
}

/* #400 — "כמה זמן עבר" לשלב האתחול האחרון (שלב האתחול — במגירת המחשב, ‏bootCell). */
function sinceSeconds(seconds) {
  // #1503: יחיד במקום "לפני 1 ימים" / "1 שע'"
  if (seconds < 90) { const s = Math.round(seconds); return s === 1 ? "לפני שנייה" : `לפני ${s} שנ'`; }
  if (seconds < 3600) return `לפני ${Math.round(seconds / 60)} דק'`;
  if (seconds < 86400) { const h = Math.round(seconds / 3600); return h === 1 ? "לפני שעה" : `לפני ${h} שע'`; }
  const d = Math.round(seconds / 86400);
  return d === 1 ? "לפני יום" : `לפני ${d} ימים`;
}

/* ‏#954 גל 3: הרשימה עצמה מצוירת ב-console.js (seenDevicesCard, מ-NET); כאן נשאר טופס
   ההוספה הידנית ("התקן ידנית" בכותרת דף המחשבים), שטוען מחדש דרך loadMachines (שקורא גם /net). */
function netDeviceAdd() {
  sheet({
    title: "הוספת התקן ידנית",
    sub: "למכונה שעוד לא דיברה עם השרת.",
    fields: [
      { id: "mac", label: "MAC", placeholder: "b4:2e:99:07:1a:c4", dir: "ltr" },
      { id: "ip", label: "כתובת IP (לא חובה)", placeholder: "10.44.12.187", dir: "ltr" },
      { id: "description", label: "תיאור", placeholder: "למשל: עמדת מרצה חדשה" },
    ],
    submitLabel: "הוסף",
    onSubmit: async (v) => { await post("/net", v); await loadMachines(); },
  });
}
