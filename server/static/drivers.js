/* ImageSrv — דף "דרייברים" (#720, #959; ‏#954 גל 6 לפי
   docs/design/console-redesign/drivers.md): ספריית חבילות הדרייברים כטבלה
   אחת — שם, כלל ההתאמה (PCI / דגם / pci_any), קבצים, "מתאים ל-" בשמות —
   ולשונית "כיסוי לפי מכונה" (בנייה/שיכפול; כיתות v2) שהופכת את הטבלה: אילו
   חבילות יונחו על כל מכונה שדיווחה חומרה, ואילו בקרי PCI נשארו בלי חבילה.
   ייבוא tar (admin; השרת מאמת sha256 של כל קובץ לפני הכניסה, tools/drivers/
   build-package.py בונה אותו) ומחיקה מאחורי חלון אישור. משתמש ב-UI/api/post/
   sheet/toast/esc מ-console.js; המכונות מ-/machines (loadMachines). */
"use strict";

let DRIVERS = null, driversError = "";

async function loadDrivers() {
  try {
    if (!MACHINES || !GROUPS) await loadMachines();   // שמות, תפקידים והמלאי (inventory) לכיסוי
    DRIVERS = await api("/drivers");
    driversError = "";
  } catch (e) {
    DRIVERS = null;   // לא נקרא ≠ ספרייה ריקה (עיקרון 5)
    driversError = e.message;
    toast("טעינת הדרייברים נכשלה: " + e.message);
  }
  if (current === "drivers") renderCurrent();
}

/* הכלל כ-HTML: הטקסט העברי בחוץ, המזהים ב-LTR מבודד — אחרת "אחד מ-4:" מתהפך בתוך .mono. */
function driverRuleHtml(rule) {
  const ids = (list) => `<span class="mono">${esc(list.join(" | "))}</span>`;
  if (rule.pci) return `PCI ${ids(rule.pci)}`;
  if (rule.pci_any) return `PCI אחד מ-${rule.pci_any.length}: ${ids(rule.pci_any.slice(0, 3))}${rule.pci_any.length > 3 ? " …" : ""}`;
  return `דגם: <span dir="auto">${esc(rule.vendor)}</span> · <span dir="auto">${esc(rule.model)}</span>`;
}
function driverMachineName(mac) { const m = findMachine(mac); return m ? machineName(m) || mac : mac; }
function driverMatchesCell(pkg) {
  const macs = pkg.matches || [];
  if (!macs.length) return `<span class="muted">אף מכונה שדיווחה</span>`;
  const names = macs.map(driverMachineName);
  const shown = names.slice(0, 4).join(", ") + (names.length > 4 ? ` ועוד ${names.length - 4}` : "");
  return `${macs.length} ${macs.length === 1 ? "מכונה" : "מכונות"} · <span class="cap" title="${esc(names.join(", "))}">${esc(shown)}</span>`;
}
function driverRowHtml(p, admin) {
  const enc = encodeId(p.name);
  const acts = `<div class="acts"><button class="btn sm" onclick="openDriverDetail('${enc}')">פרטים</button>${admin ? `<button class="btn sm danger" onclick="deleteDriver('${enc}')">מחיקה</button>` : ""}</div>`;
  return { attrs: `data-pkg="${esc(p.name)}"`, cells: [
    UI.name(p.name, p.description || ""),
    (p.match || []).map((r) => `<span style="display:block">${driverRuleHtml(r)}</span>`).join("") || `<span class="muted">—</span>`,
    `<span class="mono">${(p.files || []).length}</span>`,
    driverMatchesCell(p),
    UI.status("ok", "sha256 בייבוא"),   // השרת מאמת כל קובץ לפני שהחבילה נכנסת (import_tar) — חבילה בספרייה = חבילה שאומתה
    acts] };
}

/* ---------- כיסוי לפי מכונה: חישוב בלקוח, אותם כללים כמו drivers.match ----------
   מזהה עם class (3 חלקים) = זהות מלאה; בלי class = אותו vendor:device בכל class.
   כלל pci תואם כש**כל** מזהיו במלאי, pci_any כש**אחד**; כלל דגם — vendor ו-model
   מול sys_vendor / product_name / product_version (בלי רישיות ורווחי קצה).
   "בלי כיסוי" = בקר PCI שאף כלל PCI של חבילה תואמת לא מכסה; חבילת דגם מכסה
   לפי דגם, לא לפי בקר — ולכן היא נספרת אך אינה מסירה בקר מ"בלי כיסוי". */
function driverIdPresent(pci, have) {
  const p = String(pci).toLowerCase();
  return p.split(":").length === 3 ? have.includes(p) : have.some((h) => h.startsWith(p + ":"));
}
function driverNorm(v) { return String(v == null ? "" : v).trim().toLowerCase(); }
function driverRuleHits(rule, inv) {
  const have = (inv.pci || []).map((x) => String(x).toLowerCase());
  if (rule.pci) return rule.pci.every((p) => driverIdPresent(p, have)) ? { by: "pci", ids: rule.pci } : null;
  if (rule.pci_any) { const ids = rule.pci_any.filter((p) => driverIdPresent(p, have)); return ids.length ? { by: "pci", ids } : null; }
  const dmi = inv.dmi || {};
  if (driverNorm(rule.vendor) !== driverNorm(dmi.sys_vendor)) return null;
  const model = driverNorm(rule.model);
  return model && [driverNorm(dmi.product_name), driverNorm(dmi.product_version)].includes(model) ? { by: "model", ids: [] } : null;
}
function driverCoverage(m) {
  const inv = m.inventory || {}, have = (inv.pci || []).map((x) => String(x).toLowerCase());
  const covered = new Set(), pkgs = [];
  for (const p of DRIVERS || []) {
    const hits = (p.match || []).map((r) => driverRuleHits(r, inv)).filter(Boolean);
    if (!hits.length) continue;
    const by = hits.some((h) => h.by === "pci") ? "pci" : "model";
    pkgs.push({ name: p.name, by });
    for (const h of hits) for (const id of h.ids) for (const x of have) if (driverIdPresent(id, [x])) covered.add(x);
  }
  return { pkgs, uncovered: have.filter((x) => !covered.has(x)) };
}
function driverCoverageRow(m) {
  const macEnc = encodeId(m.mac), inv = m.inventory, dmi = (inv && inv.dmi) || {};
  const model = [dmi.sys_vendor, dmi.product_version || dmi.product_name].filter(Boolean).join(" ");
  const name = UI.nameHtml(machineName(m) || m.mac, `${esc(ROLE_HE[machineRole(m)] || "")}${model ? " · " + esc(model) : ""}`);
  if (inv == null) return { attrs: `data-mac="${esc(m.mac)}"`, cells: [name, `<span class="muted">לא דיווחה חומרה (סוכן ישן, או מעולם לא עלתה)</span>`, `<span class="muted">—</span>`, UI.status("unk", "לא נבדק"), UI.acts([["פרטים", `openMachineDetail('${macEnc}')`]])] };
  const { pkgs, uncovered } = driverCoverage(m);
  const pci = (inv.pci || []).length ? (inv.pci || []).map((x) => `<span class="mono" style="display:block">${esc(x)}</span>`).join("") : `<span class="muted">לא דיווחה בקרי PCI</span>`;
  const staged = pkgs.length
    ? pkgs.map((p) => `<span style="display:block">${esc(p.name)} <span class="cap">${p.by === "pci" ? "לפי PCI" : "לפי דגם"}</span></span>`).join("")
    : `<span class="muted">אין חבילה מתאימה</span>`;
  const state = !(inv.pci || []).length ? UI.status("unk", "אין מה לבדוק")
    : !uncovered.length ? UI.status("ok", "כל הבקרים מכוסים")
    : pkgs.length && pkgs.every((p) => p.by === "model") ? UI.status("warn", `חבילת דגם בלבד — ${uncovered.length} בקרים לא אומתו לפי PCI`)
    : UI.status("warn", `${uncovered.length} ${uncovered.length === 1 ? "בקר" : "בקרים"} בלי חבילה`) + `<span class="sub">${uncovered.map((x) => `<span class="mono">${esc(x)}</span>`).join(" · ")}</span>`;
  return { attrs: `data-mac="${esc(m.mac)}"`, cells: [name, pci, staged, state, UI.acts([["פרטים", `openMachineDetail('${macEnc}')`]])] };
}
function driversCoverageCard() {
  const v1 = (MACHINES || []).filter((m) => ["build", "cloner"].includes(machineRole(m)));
  const reported = v1.filter((m) => m.inventory != null);
  const rows = sortMachinesBySuffix(v1).map(driverCoverageRow);
  const table = MACHINES == null
    ? UI.note("err", "רשימת המחשבים לא נקראה — אין מה לחשב.")
    : UI.datagrid({ cls: "acts-on", columns: ["מכונה", "בקרי PCI (רשת/אחסון)", "חבילות שיונחו", "כיסוי", ""], rows, empty: "אין מחשבי בנייה או שיכפול רשומים — הוסיפו אותם בדף המחשבים" });
  const classroom = (MACHINES || []).filter((m) => machineRole(m) === "classroom").length;
  return UI.card({ title: "כיסוי לפי מכונה", small: `${reported.length} מתוך ${v1.length} דיווחו חומרה · חושב בקונסולה לפי אותם כללים של השרת`, cls: "c12", body: table, flush: !!MACHINES && rows.length > 0 })
    + `<div class="c12">${UI.note("info", `"הונח" אינו "הותקן": בקר אחסון שבלעדיו Windows לא עולה אינו מכוסה (v2). חבילת דגם מכסה לפי דגם ולא לפי בקר — ולכן בקר שרק היא מכסה מוצג כ"לא אומת לפי PCI".${classroom ? ` תחנות כיתה (${classroom}) — v2, לא מוצגות.` : ""}`)}</div>`;
}

/* ---------- דגמים (stage 3, console-full.html P.drivers): מבט לפי דגם — הדגם מ-DMI שהמחשב דיווח ב-hello ----------
   אותו חישוב כיסוי של לשונית "כיסוי לפי מכונה" (driverCoverage), מקובץ לפי דגם. מכונה נספרת "מכוסה" רק כשדיווחה
   בקרי PCI וכולם מכוסים; "חסרה" כשיש בקר בלי חבילה; "לא נבדקה" כשלא דיווחה בקרים בכלל — ולא נספרת כמכוסה (עיקרון 5).
   מכונה שלא דיווחה חומרה כלל אין לה דגם: שורה משלה, "לא נבדק". גרסה/גודל/סוג לכל חבילה — אין בנתוני /drivers. */
function driverModelLabel(m) {
  if (m.inventory == null) return null;
  const dmi = m.inventory.dmi || {};
  return [dmi.sys_vendor, dmi.product_version || dmi.product_name].filter(Boolean).join(" ") || "דגם לא דווח";
}
function driverMachineVerdict(m) {
  const pci = (m.inventory && m.inventory.pci) || [];
  if (!pci.length) return "unchecked";
  return driverCoverage(m).uncovered.length ? "missing" : "covered";
}
function driverModels() {
  const v1 = (MACHINES || []).filter((m) => ["build", "cloner"].includes(machineRole(m)));
  const map = new Map(), unreported = [];
  for (const m of v1) {
    const label = driverModelLabel(m);
    if (label == null) { unreported.push(m); continue; }
    const e = map.get(label) || { model: label, machines: [], covered: 0, missing: 0, unchecked: 0, pkgs: new Set() };
    e.machines.push(m);
    e[driverMachineVerdict(m)]++;
    driverCoverage(m).pkgs.forEach((p) => e.pkgs.add(p.name));
    map.set(label, e);
  }
  return { models: [...map.values()].sort((a, b) => a.model.localeCompare(b.model)), unreported };
}
/* ‏#1300: עמודת "לא נבדקו" ירדה (console-full.html) — מה שלא נבדק נאמר בפיל עצמו, לא נבלע ב"מכוסה". */
function driverModelPill(e) {
  if (e.missing && !e.pkgs.size) return UI.pill("err", "אין דרייברים");
  if (e.missing) return UI.pill("warn", "חלקי");
  if (e.unchecked) return UI.pill("", e.unchecked === e.machines.length ? "לא נבדק" : `${e.unchecked} לא נבדקו`);
  return UI.pill("ok", "מכוסה");
}
function driversModelsCard() {
  if (MACHINES == null) return UI.note("err", "רשימת המחשבים לא נקראה — אין מה לחשב.");
  const { models, unreported } = driverModels();
  // ‏#1300 (console-full.html P.drivers): דגם · מחשבים · מכוסים · חסרים · מצב · ⋯
  const menu = (e) => UI.rowMenu(e.model, [["פרטים", `openDriverModel('${encodeId(e.model)}')`]].concat(isAdmin() ? [["העלאת דרייבר", "openDriverImport()"]] : []));
  const rows = models.map((e) => ({ attrs: `class="clk" data-model="${esc(e.model)}" tabindex="0" onclick="openDriverModel('${encodeId(e.model)}')"`,
    cells: [`<b dir="auto">${esc(e.model)}</b>`, String(e.machines.length), String(e.covered), String(e.missing), driverModelPill(e), menu(e)] }));
  if (unreported.length) rows.push({ attrs: `data-model=""`, cells: [`<span class="muted">לא דיווחו חומרה</span>`, String(unreported.length), "—", "—", UI.pill("", "לא נבדק"), ""] });
  const table = UI.datagrid({ cls: "kebab-col", columns: ["דגם", "מחשבים", "מכוסים", "חסרים", "מצב", { html: `<span class="sr">פעולות</span>` }], rows, empty: "אין מחשבי בנייה או שיכפול רשומים — הוסיפו אותם בדף המחשבים" });
  return UI.card({ title: "דגמי מחשבים שנמצאו ברשת", small: "הדגם נקבע מ-DMI שהמחשב מדווח ב-hello", cls: "c12", body: table, flush: rows.length > 0 });
}
function openDriverModel(enc) {
  let label = enc; try { label = decodeURIComponent(enc); } catch (e) {}
  const e = driverModels().models.find((x) => x.model === label);
  if (!e) { toast("דגם לא נמצא"); return; }
  const lines = e.machines.map((m) => {
    const v = driverMachineVerdict(m), un = v === "missing" ? driverCoverage(m).uncovered : [];
    return `<span style="display:block">${esc(driverMachineName(m.mac))} ${v === "covered" ? UI.pill("ok", "מכוסה") : v === "missing" ? UI.pill("warn", `${un.length} בקרים בלי חבילה`) : UI.pill("", "לא נבדק")}${un.length ? ` <span class="cap mono">${esc(un.join(" · "))}</span>` : ""}</span>`;
  }).join("");
  const pk = [...e.pkgs].map((n) => `<span style="display:block">${esc(n)}</span>`).join("") || `<span class="muted">אין חבילה מתאימה</span>`;
  const warn = e.missing && !e.pkgs.size ? UI.note("warn", `${e.machines.length} מחשבים מהדגם הזה רשומים, ואין להם דרייברים. אימג' שישוחזר אליהם עלול לעלות בלי דרייבר אחסון או רשת.`) : "";
  const imp = isAdmin() ? `<div class="acts"><button class="btn primary" onclick="openDriverImport()">העלאת דרייבר לדגם הזה</button></div>` : "";
  openDrawer(`דגם — ${e.model}`, `<div class="page drw">${warn}${UI.kv([["מחשבים", lines], ["חבילות שיונחו", pk]])}${imp}</div>`);
}

function driversPage(tab = 0) {
  if (DRIVERS === null && !driversError) return pagePlaceholder();
  const admin = isAdmin(), list = DRIVERS || [];
  // ‏#1300 (console-full.html P.drivers): משנה = דגמים · מחשבים · מכוסים; "העלאת דרייבר"; מונה בלשונית "דגמים".
  // לשונית "חבילות" (רשימת החבילות, פרטים ומחיקה) נשארת — אין לה מקום אחר במוקאפ.
  const dm = MACHINES ? driverModels() : null;
  const machines = dm ? dm.models.reduce((n, e) => n + e.machines.length, 0) + dm.unreported.length : null;
  const covered = dm ? dm.models.reduce((n, e) => n + e.covered, 0) : null;
  const sub = DRIVERS
    ? (dm ? [`${dm.models.length} דגמים`, `${machines} מחשבים`, `${covered} מכוסים`] : ["מכונות: לא נקרא"]).map(esc).join(" · ")
    : `‏/drivers לא נקרא: ${esc(driversError)}`;
  const actions = (admin ? `<button class="btn primary" onclick="openDriverImport()">העלאת דרייבר</button>` : "") + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "דרייברים" }],
    icon: "t-drivers", name: "דרייברים", sub, pill: DRIVERS ? "" : UI.pill("err", "לא נקרא"), actions,
    tabs: [dm ? ["דגמים", String(dm.models.length)] : "דגמים", DRIVERS ? ["חבילות", String(list.length)] : "חבילות", "כיסוי לפי מכונה"], tab });
  let body;
  if (!DRIVERS) body = UI.note("err", `לא הצלחתי לקרוא את רשימת הדרייברים: ${esc(driversError)}`);
  else if (tab === 0) body = driversModelsCard();
  else if (tab === 2) body = driversCoverageCard();
  else {
    const table = UI.datagrid({ cls: "acts-on", columns: ["חבילה", "התאמה", "קבצים", "מתאים ל-", "אומת", ""], rows: list.map((p) => driverRowHtml(p, admin)),
      empty: "אין חבילות דרייברים — ייבוא קובץ tar שנבנה ב-tools/drivers/build-package.py (manifest.json + sha256 לכל קובץ; docs/driver-packages.md)" });
    body = `<div class="c12 card"><div class="card-b${list.length ? " flush" : ""}">${table}${!list.length && admin ? `<div style="text-align:center;padding-top:10px"><button class="btn primary" onclick="openDriverImport()">ייבוא חבילה (tar)</button></div>` : ""}</div></div>`
      + `<div class="c12">${UI.note("info", `"הונח" אינו "הותקן": הסוכן מניח את החבילות התואמות ב-<span dir="ltr">\\ImageSrv\\Drivers</span> ורושם אותן ב-DevicePath — Windows סורק אותן בעלייה הראשונה. בקר אחסון שבלעדיו Windows לא עולה אינו מכוסה (v2). הלשונית "כיסוי לפי מכונה" הופכת את הטבלה — לכל מכונה, אילו חבילות יונחו ואילו בקרים בלי כיסוי.`)}</div>`;
  }
  return `<div class="page aligned">${header}<div class="body">${body}</div></div>`;
}

function findDriver(name) {
  try { name = decodeURIComponent(name); } catch (e) {}
  return (DRIVERS || []).find((p) => p.name === name) || null;
}
function openDriverDetail(name) {
  const p = findDriver(name);
  if (!p) { toast("חבילה לא נמצאה"); return; }
  const files = (p.files || []).map((f) => `<span style="display:block"><span class="mono">${esc(f.path)}</span> <span class="cap mono">${esc(String(f.sha256 || "").slice(0, 12))}…</span></span>`).join("") || `<span class="muted">—</span>`;
  const macs = p.matches || [];
  openDrawer(`חבילה — ${p.name}`, `<div class="page drw">${UI.kv([
    ["תיאור", esc(p.description || "") || `<span class="muted">—</span>`],
    ["כללי התאמה", (p.match || []).map((r) => `<span style="display:block">${driverRuleHtml(r)}</span>`).join("") || `<span class="muted">—</span>`],
    ["מתאים ל-", macs.length ? esc(macs.map(driverMachineName).join(", ")) : `<span class="muted">אף מכונה שדיווחה</span>`],
    ["אומת", UI.status("ok", "sha256 של כל קובץ נבדק בייבוא")],
    [`קבצים (${(p.files || []).length})`, files]])}</div>`);
}

function openDriverImport() {
  if (!isAdmin()) { toast("אין הרשאה"); return; }
  openModalContent(
    "ייבוא חבילת דרייברים",
    `<div class="form"><div class="field full">${dropzoneHtml({ id: "driverFile", accept: ".tar,application/x-tar", aria: "בחירת קובץ tar של חבילת דרייברים" })}</div><div class="field full"><div class="form-note">חבילה שנבנתה ב-<span dir="ltr" class="mono">tools/drivers/build-package.py</span> (‏<span dir="ltr" class="mono">docs/driver-packages.md</span>): תיקייה אחת עם manifest.json והקבצים. כל קובץ מאומת מול ה-sha256 שבמניפסט; חבילה עם קובץ חסר, עודף או שונה לא תיכנס.</div></div></div>`,
    "ייבוא",
    startDriverUpload
  );
}

function startDriverUpload() {
  const input = document.getElementById("driverFile");
  const file = input && input.files && input.files[0];
  if (!file) { toast("בחר קובץ לפני הייבוא"); return; }
  if (secondaryReadOnly()) { toast(SECONDARY_READ_ONLY); return; }   // ‏#1179: XHR עוקף את api()
  const body = document.getElementById("modalBody");
  const ok = document.getElementById("modalOk");
  if (ok) ok.disabled = true;
  body.innerHTML = `<div class="notice">מעלה ומאמת את "${esc(file.name)}"…</div>`;
  const request = new XMLHttpRequest();
  request.open("POST", "/api/console/drivers/upload");
  request.setRequestHeader("Content-Type", "application/x-tar");
  request.onload = () => {
    if (request.status === 200) {
      let name = file.name;
      try { name = JSON.parse(request.responseText).name || name; } catch (e) {}
      closeModal();
      toast(`החבילה "${name}" נקלטה — sha256 אומת לכל קובץ.`);
      loadDrivers();
    } else {
      let message = "שגיאה " + request.status;
      try { message = JSON.parse(request.responseText).detail || message; } catch (e) {}
      body.innerHTML = `<div class="notice err">הייבוא נדחה: ${esc(message)}</div>`;
      if (ok) { ok.disabled = false; ok.textContent = "סגור"; ok.onclick = closeModal; }
    }
  };
  request.onerror = () => {
    body.innerHTML = `<div class="notice err">הייבוא נכשל — החיבור נותק.</div>`;
    if (ok) { ok.disabled = false; ok.textContent = "סגור"; ok.onclick = closeModal; }
  };
  request.send(file);
}

function deleteDriver(name) {
  const p = findDriver(name);
  if (!isAdmin() || !p) return;
  sheet({
    title: "למחוק את חבילת הדרייברים?", sub: p.name,
    note: impactList([["b", "החבילה תימחק מהספרייה"], ["ok", "מכונות שכבר קיבלו אותה אינן מושפעות"]]),
    danger: true, submitLabel: "מחיקת החבילה",
    onSubmit: async () => {
      await post("/drivers/" + encodeURIComponent(p.name) + "/delete", { confirm_name: p.name });
      toast("החבילה נמחקה.");
      await loadDrivers();
    },
  });
}
