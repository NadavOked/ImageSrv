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

function deployFormProblems(v) {
  const found = [];
  const server = firstHost(v.network, v.netmask);
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
  }
  if (!/^\d+$/.test(String(v.lease_n || "").trim()) || Number(v.lease_n) < 1) {
    found.push("משך חכירה: מספר שלם חיובי");
  }
  return found;
}

function deployServerNote(network, netmask) {
  const ip = firstHost(network, netmask);
  return ip ? `השרת יקבל אוטומטית את <b dir="ltr">${esc(ip)}</b> — הכתובת הראשונה ברשת.`
    : "כתובת השרת נגזרת מהרשת: הכתובת הראשונה בה.";
}

function editDeployNet(name) {
  const nic = (NICS || []).find((n) => n.name === name);
  if (!nic) { toast("כרטיס לא נמצא"); return; }
  if (isMgmtNic(name)) { toast(`${name} הוא כרטיס הניהול — הוא לא יכול להיות רשת ההפצה`); return; }
  const netmask = nic.netmask || "255.255.255.0";
  const network = networkOf(nic.server_ip, netmask);
  const lease = String(nic.lease || "12h").trim().toLowerCase().match(/^(\d+)([mhd])$/) || [null, "12", "h"];
  const firstTime = typeof NET_DEPLOY !== "undefined" && NET_DEPLOY && NET_DEPLOY.configured === false && !nic.enabled;
  const cards = deployCardsFields(name, nic);
  const initial = { network, netmask, range_start: nic.range_start || "", range_end: nic.range_end || "", lease_n: lease[1], lease_u: lease[2] };
  sheet({
    title: `רשת הפצה — ${name}`,
    sub: "ה-DHCP למחשבי השיכפול והבנייה רץ על הכרטיס הזה",
    note: (nic.trunk ? `<div class="sheet-note danger">הכרטיס מסומן כמחובר לרשת המכללה. DHCP כאן ישבית את הרשת.</div>`
      : !nic.enabled ? `<div class="sheet-note">ההגדרה המסוכנת ביותר במערכת: DHCP על רשת שכבר יש בה שרת משבית אותה. השרת יבדוק לפני ההדלקה אם מישהו כבר עונה.</div>` : "")
      + (firstTime ? `<div class="sheet-note">רשת ההפצה מוגדרת כאן <b>בפעם הראשונה</b>: השמירה משלימה את ההתקנה (grub.cfg, dnsmasq, חומת אש) ומאתחלת את השרת על כתובת ההפצה — הקונסולה תחזור תוך כמה שניות.</div>` : "")
      + `<div class="sheet-note" id="deploy-server-ip">${deployServerNote(network, netmask)}</div>`,
    size: "mid",
    fields: [
      { type: "section", label: "רשת ההפצה" },
      { id: "network", label: "כתובת רשת", value: network, dir: "ltr" },
      { id: "netmask", label: "מסכה", value: netmask, dir: "ltr" },
      { type: "section", label: "DHCP למחשבים" },
      { id: "range_start", label: "תחילת טווח", value: nic.range_start, dir: "ltr" },
      { id: "range_end", label: "סוף טווח", value: nic.range_end, dir: "ltr" },
      { id: "lease_n", label: "משך חכירה", value: lease[1], dir: "ltr" },
      { id: "lease_u", label: "יחידה", type: "select", value: lease[2],
        options: LEASE_UNITS.map(([value, label]) => ({ value, label })) },
      ...(nic.trunk && !nic.enabled ? [{ id: "confirm_trunk", type: "checkbox", value: false,
        label: "אני מבין שזו רשת המכללה ורוצה להדליק בכל זאת" }] : []),
      ...(cards ? cards.fields : []),
    ],
    submitLabel: "שמירה והחלה",
    onSubmit: async (v) => {
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
        });
      }
      if (cards && second !== cards.current) await saveDeploySecond(name, second);
    },
  });
  // כתובת השרת מתעדכנת תוך כדי הקלדה, והכפתור נדלק כשהשדות תקינים.
  const form = document.getElementById("sheet");
  const body = form && form.querySelector(".sbody");
  if (!body) return;
  const value = (id) => { const el = document.getElementById("sf-" + id); return el ? el.value : ""; };
  const sync = () => {
    const v = { network: value("network"), netmask: value("netmask"), range_start: value("range_start"),
                range_end: value("range_end"), lease_n: value("lease_n") };
    const note = document.getElementById("deploy-server-ip");
    if (note) note.innerHTML = deployServerNote(v.network, v.netmask);
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
  ] };
}

async function saveDeploySecond(name, second) {
  const result = await put(`/net/config/${encodeURIComponent(name)}/second`, { second });
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
  if (!result.dhcp) throw new Error(result.apply_error || "הכתובת לא הוחלה — ה-DHCP לא שונה");
  if (result.apply_error) toast("נשמר, אבל dnsmasq לא עודכן: " + result.apply_error);
  else toast(`רשת ההפצה נשמרה על ${name} — כתובת השרת ${result.server_ip}`);
  const d = result.deploy;
  if (d && d.ok) {
    toast(`רשת ההפצה הוגדרה על ${name} (${d.url}) — השרת מתאתחל, הקונסולה תחזור תוך כמה שניות`);
    if (typeof NET_DEPLOY !== "undefined") NET_DEPLOY = { configured: true, source: "console", interface: name, url: d.url, hint: null };
  }
  await loadNet();
}

/* --- בריאות המערכת — רמזור לכל בדיקה (health.py) ------------------------- */
/* השם loadHealthPanel כדי לא לדרוס את loadHealth של console.js. */

async function loadHealthPanel() {
  const list = $("#health-list");
  if (!list) return;
  const checks = await api("/health");
  list.innerHTML = checks.map((c) => `
    <div class="health-row">
      <span class="hlight ${c.state}"></span>
      <b>${esc(c.label)}</b>
      <span class="sub">${esc(c.detail)}</span>
    </div>`).join("");
}

const healthRefresh = $("#health-refresh");
if (healthRefresh) healthRefresh.addEventListener("click", () =>
  loadHealthPanel().catch((error) => toast("רענון נכשל: " + error.message)));

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
  if (seconds < 86400) return `לפני ${Math.round(seconds / 3600)} שע'`;
  return iso.slice(0, 10);
}

/* #400 — "כמה זמן עבר" לשלב האתחול האחרון (שלב האתחול — במגירת המחשב, ‏bootCell). */
function sinceSeconds(seconds) {
  if (seconds < 90) return `לפני ${Math.round(seconds)} שנ'`;
  if (seconds < 3600) return `לפני ${Math.round(seconds / 60)} דק'`;
  if (seconds < 86400) return `לפני ${Math.round(seconds / 3600)} שע'`;
  return `לפני ${Math.round(seconds / 86400)} ימים`;
}

/* ‏#954 גל 3: הרשימה עצמה מצוירת ב-console.js (seenDevicesCard, מ-NET); כאן
   נשארו שלושת הטפסים — תיאור, הסרה, הוספה ידנית — וכולם טוענים מחדש דרך
   loadMachines (שקורא גם /net). */
function netDeviceDescribe(macEnc) {
  let mac = macEnc; try { mac = decodeURIComponent(macEnc); } catch (e) {}
  const device = (NET || []).find((d) => d.mac === mac);
  if (!device) return;
  sheet({
    title: "תיאור ההתקן", sub: device.mac,
    fields: [{ id: "description", label: "תיאור חופשי",
               value: device.description, placeholder: "למשל: מדפסת מעבדה 2" }],
    onSubmit: async (v) => {
      await put(`/net/${encodeId(device.mac)}`, { description: v.description });
      await loadMachines();
    },
  });
}
function netDeviceForget(macEnc) {
  let mac = macEnc; try { mac = decodeURIComponent(macEnc); } catch (e) {}
  confirmSheet(
    "להסיר מהרשימה?",
    mac,
    "הסרה מהרשימה", async () => { await del(`/net/${encodeId(mac)}`); await loadMachines(); },
    [["w", "המכונה תוסר מהרשימה"], ["ok", "אם תדבר עם השרת שוב — היא תחזור לרשימה"]]);
}
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
