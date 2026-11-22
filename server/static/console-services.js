/* ImageSrv -- #3887: דף "שירותים" במקום דף "פורטים" (נדב 20/11, מוקאפ מאושר
   handoffs/backlog-mockups-1115/m3887-services.html). שורה לכל שירות: מצב נמדד, "בעלייה" (קריאה
   בלבד), פורט כמידע, מתג, והגדרות. המקור: GET /ports (ss בשרת, לא ההגדרה) ו-GET /ups.

   ההכרעות שבתוך המוקאפ ואחריו:
   - הגישה מרחוק של #1266, ‏proxy DHCP ומולטיקאסט אינם בדף (#1266, #1303, #3173). חומת האש — בבריאות.
   - הקונסולה לא נכבית מתוך עצמה: המתג שלה דלוק ומנוטרל. DHCP — מצב בלבד, ההגדרה בלשונית "רשת הפצה".
   - כיבוי של HTTP, ‏TFTP ובין-שרתים (מוקאפ, סעיף 2) = חלון אישור עם ההשלכות וכפתור אדום, בלי הקלדת
     מילה (#1270). מילת האישור שהשרת דורש נקראת מ-/ports ברגע השליחה (#1780), לא מוקלדת.
   - שלושה מצבים: פועל / כבוי / "לא ידוע" (הבדיקה עצמה לא הצליחה) — "לא ידוע" אף פעם לא נצבע
     כבוי, ובשורה כזו אין מתג אלא "בדיקה חוזרת" (עיקרון 5).
   - המזהה הפנימי של הדף נשאר `ports` (pages.ports), כדי שכל הקישורים הקיימים ימשיכו לעבוד. */

let SVC_UPS = null, svcUpsError = "";
async function loadServices() {
  await Promise.all([loadPorts(), svcLoadUps()]);
  if (current === "ports") renderCurrent();
}
async function svcLoadUps() {
  if (!isAdmin()) return;
  try { SVC_UPS = await api("/ups"); svcUpsError = ""; } catch (e) { SVC_UPS = null; svcUpsError = e.message; }
}

/* הסדר והשמות של המוקאפ. id = שורת /ports; `ups` = GET /ups. */
const SVC_ROWS = [
  { id: "http_console", name: "קונסולה (HTTPS)", boot: "תמיד" },
  { id: "http_boot", name: "HTTP: אתחול וסוכן", desc: "תפריט אתחול, קרנל, hello ואימג'ים (/boot)", boot: "תמיד" },
  { id: "tftp", name: "TFTP", desc: "טוען האתחול: shim, GRUB והתפריט", boot: "תמיד" },
  { id: "dhcp", name: "DHCP", desc: "חלוקת כתובות ל-PXE (dnsmasq). מצב בלבד", boot: "לפי הגדרה" },
  { id: "ntp", name: "שרת זמן (NTP)", desc: "שעה למחשבים ברשת. מופעל בנפרד בכל כרטיס", boot: "לפי כרטיס" },
  { id: "monitor", name: "מוניטור", desc: "צפייה ושליטה במסך של מחשבי בנייה ושיכפול", boot: "תמיד" },
  { id: "interserver", name: "בין-שרתים", desc: "קו בין הראשי לשרת משנה (mTLS): אימג'ים והגדרות", boot: "תמיד" },
];
/* מצב → [מחלקת הפיל, מילה]. warn/bad נשארים בצבע שלהם: מה שהשרת מדד כתקלה אינו "כבוי". */
const SVC_PILL = { ok: ["ok", "פועל"], info: ["ok", "פועל"], off: ["off", "כבוי"], not_applicable: ["off", "לא ישים"],
  unknown: ["unk", "? לא ידוע"], warn: ["warn", "דורש בדיקה"], bad: ["err", "תקלה"] };
const svcPill = (state) => { const [cls, word] = SVC_PILL[state] || SVC_PILL.unknown; return `<span class="pill ${cls}">${esc(word)}</span>`; };
const svcUnread = (p) => p.state === "unknown" || p.listening === null || p.listening === undefined;
const svcGear = (label, onclick) => `<button class="svc-ic" type="button" title="${esc(label)}" aria-label="${esc(label)}" onclick="${onclick}">${uiIcon("settings")}</button>`;
const svcSwitch = (on, { disabled = false, label = "", onclick = "" } = {}) =>
  `<button type="button" class="svc-tg${on ? " on" : ""}" role="switch" aria-checked="${on}" aria-label="${esc(label)}"${disabled ? ` disabled title="${esc(label)}"` : ` onclick="${onclick}"`}></button>`;
const svcRecheck = () => `<button class="btn sm" type="button" onclick="refreshPage()">בדיקה חוזרת</button>`;
/* הסבב צריך HTTP ו-TFTP תמיד, ו-DHCP כשהשרת מוגדר לחלק כתובות (brain/services.py, `needed`). */
function svcNeeded() {
  const dhcp = (PORTS || []).find((p) => p.id === "dhcp");
  return ["http_boot", "tftp"].concat(dhcp && dhcp.enabled ? ["dhcp"] : []);
}

function svcStateCell(def, p) {
  if (def.id === "monitor") {
    const sub = "תופס באתחול הבא של כל תחנה. הסוקט נמדד בתחנה, לא בשרת";
    return `<span class="pill ${p.enabled ? "ok" : "off"}">${p.enabled ? "מופעל" : "כבוי"}</span><span class="sub">${esc(sub)}</span>`;
  }
  let sub = p.detail || "";
  if (def.id === "ntp" && Array.isArray(p.interfaces) && p.interfaces.length && Array.isArray(PORTS_NICS))
    sub = `בכרטיס ${p.interfaces.join(", ")}, מתוך ${PORTS_NICS.length} כרטיסים`;
  const need = svcNeeded().includes(def.id) ? ` <span class="pill need">הסבב צריך</span>` : "";
  return `${svcPill(p.state)}${need}${sub ? `<span class="sub">${esc(sub)}</span>` : ""}`;
}
function svcPortCell(def, p) {
  const at = def.id === "monitor" ? `<span class="sub">בתחנה</span>` : "";
  return `<span class="mono">${esc(p.port)}/${esc(p.proto)}</span>${at}`;
}
function svcSwitchCell(def, p) {
  const k = encodeId(p.id);
  if (def.id === "http_console") return svcSwitch(true, { disabled: true, label: "הקונסולה לא נכבית מתוך עצמה" }) + `<span class="sub">לא נכבית מכאן</span>`;
  if (def.id === "dhcp") return `<span class="muted">מצב בלבד</span>`;
  if (def.id === "ntp") return `<button class="btn sm" type="button" onclick="openNetwork(1)">לפי כרטיס…</button>`;
  if (def.id === "monitor") return svcSwitch(!!p.enabled, { label: "מוניטור לתחנות", onclick: `monitorToggle(${!p.enabled})` });
  if (svcUnread(p)) return svcRecheck();
  if (p.toggle === "none" || !p.toggle) return `<span class="muted" title="${esc(p.off_means || "")}">אין מתג</span>`;
  return svcSwitch(!!p.enabled, { label: def.name, onclick: p.enabled ? `svcStop('${k}')` : `svcStart('${k}')` });
}
function svcRow(def, p) {
  const desc = def.id === "http_console" ? `ממשק הניהול הזה, עם ${typeof tlsModeHe === "function" ? tlsModeHe(ME && ME.tls) : "תעודה עצמית"}` : def.desc;
  const gear = def.id === "dhcp" ? svcGear("הגדרות DHCP", "openNetwork(2)") : "";
  return { attrs: `data-svc="${esc(def.id)}"`, cells: [`<b class="nm">${esc(def.name)}</b><span class="sub">${esc(desc)}</span>`,
    svcStateCell(def, p), `<span class="muted">${esc(def.boot)}</span>`, svcPortCell(def, p), svcSwitchCell(def, p), gear] };
}
/* UPS: מצב בלבד, מ-GET /ups. ‏ok/warn/bad = נקרא; כל השאר (ומה שלא נקרא) = "לא ידוע", עם "בדיקה חוזרת". */
function svcUpsRow() {
  const u = SVC_UPS, desc = "כיבוי מסודר בסוללה נמוכה. מצב בלבד";
  let state, sub;
  if (!u) { state = "unknown"; sub = `‏/ups לא נקרא${svcUpsError ? ": " + svcUpsError : ""}`; }
  else if (u.mode === "none") { state = "off"; sub = "לא מוגדר UPS"; }
  else { state = { ok: "ok", warn: "warn", bad: "bad" }[u.state] || "unknown"; sub = [u.target, u.detail].filter(Boolean).join(" · ") || "טרם נקרא"; }
  const act = state === "unknown" ? svcRecheck() : "";
  const gear = u && typeof upsSheet === "function" ? svcGear("הגדרות UPS", "svcUpsSettings()") : "";
  return { attrs: `data-svc="ups"${state === "unknown" ? ` class="svc-flash"` : ""}`, cells: [`<b class="nm">UPS (NUT)</b><span class="sub">${esc(desc)}</span>`,
    `${svcPill(state)}<span class="sub">${esc(sub)}</span>`, `<span class="muted">—</span>`, `<span class="mono">3493/tcp</span>`, act, gear] };
}
function svcUpsSettings() { UPS_STATUS = SVC_UPS; upsSheet(); }

function svcNeedNote() {
  const names = { http_boot: "HTTP", tftp: "TFTP", dhcp: "DHCP" };
  const need = svcNeeded(), rows = need.map((id) => (PORTS || []).find((p) => p.id === id)).filter(Boolean);
  const list = need.map((id) => `<b>${names[id]}</b>`);
  const listHtml = list.length > 1 ? `${list.slice(0, -1).join(", ")} ו-${list[list.length - 1]}` : list.join("");
  const down = rows.filter((p) => p.state !== "ok" && p.state !== "info" && p.state !== "not_applicable");
  const na = rows.some((p) => p.state === "not_applicable");
  const when = `<span class="mono muted">נבדק ${esc(PORTS_AT || "—")}</span>`;
  if (down.length) {
    const what = down.map((p) => `${names[p.id]} ${p.state === "unknown" ? "לא נבדק" : "לא פועל"}`).join(", ");
    return UI.note("warn", `הסבב צריך: ${listHtml}. ${esc(what)} — הסבב לא יתחיל. ${when}`);
  }
  if (na) return UI.note("info", `הסבב צריך: ${listHtml}. לא ישים עד שתוגדר רשת ההפצה. ${when}`);
  return UI.note("info", `הסבב צריך: ${listHtml}. כולם פועלים. כיבוי אחד מהם עוצר את ה-PXE, והסבב לא יתחיל. ${when}`);
}

function servicesPage() {
  if (!PORTS && !portsError) return pagePlaceholder();
  const crumbs = [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "רשת", onclick: "openNetwork(0)" }, { label: "שירותים" }];
  const sub = "מה השרת מפעיל ומה הוא מגיש. המצב נמדד עכשיו. הפורט מופיע כמידע; הקונסולה היא היחידה שהפורט שלה ניתן לעריכה.";
  const header = UI.objHeader({ crumbs, icon: "t-ports", name: "שירותים", sub: esc(sub), compact: true,
    actions: `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>` });
  if (!PORTS) return `<div class="page aligned">${header}<div class="body"><div class="c12">${UI.note("err", `לא הצלחתי לקרוא את מצב השירותים: ${esc(portsError)}`)}</div></div></div>`;
  const rows = SVC_ROWS.map((def) => [def, PORTS.find((p) => p.id === def.id)]).filter(([, p]) => p).map(([def, p]) => svcRow(def, p));
  if (isAdmin()) rows.push(svcUpsRow());
  const table = UI.datagrid({ columns: ["שירות", "מצב", "בעלייה", "פורט", "הפעלה", "הגדרות"], rows, cls: "svc", empty: "השרת לא החזיר אף שירות" });
  const legend = `<p class="svc-legend">שלוש רמות של מצב: <span class="pill ok">פועל</span> ראיה חיובית שמאזין · <span class="pill off">כבוי</span> ראיה חיובית שלא מאזין · <span class="pill unk">? לא ידוע</span> הבדיקה עצמה לא הצליחה. "לא ידוע" אף פעם לא נצבע כבוי, ובשורה כזאת אין מתג, רק "בדיקה חוזרת".</p>`;
  return `<div class="page aligned svc-page">${header}<div class="body"><div class="c12">${svcNeedNote()}</div>${UI.card({ title: "שירותים", body: table, flush: rows.length > 0 })}<div class="c12">${legend}</div></div></div>`;
}

/* ---------- הפעלה וכיבוי: אותו API של #996/#1013 (PUT /ports/{id}) ----------
   ‏#1780: שורה מסוג "confirm" — המילה שהשרת דורש נקראת מ-/ports ברגע השליחה, לא מהזיכרון. */
async function svcSend(p, enabled) {
  const body = { enabled };
  if (!enabled && p.toggle === "confirm") {
    const list = await api("/ports");
    const now = Array.isArray(list) ? list.find((x) => x.id === p.id) : null;
    if (!now || !now.confirm_word) throw new Error(`${p.name} ${p.port} — לא נשלח: ‏/ports לא החזיר את מילת האישור של השורה`);
    body.confirm = now.confirm_word;
  }
  const r = await put(p.toggle_url.replace(/^\/api\/console/, ""), body);
  toast(r && r.ok ? `${p.name} ${p.port} — ${enabled ? "הופעל" : "נעצר"}, ואומת`
    : `${p.name} ${p.port} — נשמר, אבל לא אומת${r && r.detail ? `: ${r.detail}` : ""}`);
  await loadPorts();
}
const svcFind = (idEnc) => {
  const id = portIdOf(idEnc), p = (PORTS || []).find((x) => x.id === id);
  return p && !svcUnread(p) && String(p.toggle_url || "").startsWith("/api/console/ports/") ? p : null;
};
function svcStop(idEnc) {
  const p = svcFind(idEnc), def = p && SVC_ROWS.find((d) => d.id === p.id);
  if (!p || !def || def.id === "http_console") return;   // הקונסולה לא נכבית מתוך עצמה
  const warn = p.warning_he || (p.off_means ? `מה קורה אם עוצרים: ${p.off_means}` : "");
  sheet({ title: `לעצור את ${def.name} (${p.port})?`, sub: def.desc || "", danger: true, submitLabel: "עצירה",
    note: warn ? `<div class="sheet-note danger" data-testid="svc-warning">${esc(warn)}</div>` : "",
    onSubmit: () => svcSend(p, false) });
}
async function svcStart(idEnc) {
  const p = svcFind(idEnc);
  if (!p) return;
  try { await svcSend(p, true); } catch (e) { toast(`ההפעלה נכשלה: ${e.message}`); }
}
svcStart = guarded("svcStart", svcStart);   // ‏#2840: לחיצה כפולה = בקשה אחת
