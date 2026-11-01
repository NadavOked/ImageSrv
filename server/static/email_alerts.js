/* ‏#1751 — לשונית "התראות במייל" בהגדרות, לפי המוקאפ המאושר #1762 (נדב 31/10, עם שש ההמלצות).
   ה-API: ‏/api/console/email-alerts (‏server/console/email_alerts.py, ‏interfaces.md §42) — admin בלבד.

   מה שונה מהמוקאפ, ולמה (המלצות 31/10; "כשהמוקאפ והשרת לא מתאימים, השרת קובע"):
   - Gmail: כתובת + סיסמת אפליקציה בלבד, עם קישור להסבר. אין "התחבר עם Google" (device code של Google
     אינו מתיר את ההרשאה של Gmail).
   - Microsoft: השרת דורש מזהה אפליקציה רשומה. "מזהה אפליקציה" ו-"tenant" — תחת "מתקדם" ← פרטי SMTP;
     כשהמזהה ריק, ליד "התחבר" יש משפט שמסביר מה המכללה צריכה לרשום. השרת אינו לומד את הכתובת מהאסימון,
     ולכן כתובת החשבון מוקלדת לפני ההתחברות. גם ב-relay וב-SMTP מותאם השרת דורש כתובת שולח.
   - ל-Relay התווית "הכי פשוט, אם יש במכללה שרת דואר פנימי". סדר הספקים כמו במוקאפ.
   - אין "המייל כפי שנשלח" ואין "שליחה עכשיו": חלון ההיסטוריה מציג רק מה שהשרת שומר.
   - נמען נשמר בשרת רק בתוך כלל — אין רשימת נמענים נפרדת.

   אבטחה: אף סוד לא מוצג ולא נשמר בדפדפן. שדות הסיסמה ריקים תמיד; סיסמה שמורה מוצגת כ"מוגדרת". הקוד של
   Microsoft ותוצאת הבדיקה נשמרים בזיכרון הדף בלבד (לא localStorage/sessionStorage). */

const EM_API = "/email-alerts";
let EM_VIEW = null, EM_ERR = "", EM_HIST = null, EM_HIST_ERR = "";
/* מצב המסך שאינו בשרת: ספק שנבחר ועוד לא נשמר, החלפת ספק, קוד ההתחברות, תוצאת הבדיקה, "מתקדם" פתוח. */
const EM = { pick: null, change: false, device: null, test: null, adv: false, pollT: null };

/* הסדר כמו במוקאפ. המפתח הוא ה-provider של השרת. */
const EM_PROV = [
  { key: "office365", name: "Office 365", sub: "חשבון Microsoft 365 של המכללה. מתחברים בדפדפן, בלי להקליד סיסמה כאן" },
  { key: "gmail", name: "Gmail / Google Workspace", sub: "חשבון Google, עם סיסמת אפליקציה (App Password)" },
  { key: "relay", name: "Relay פנימי / Exchange, בלי משתמש", sub: "שרת דואר ברשת המכללה שמקבל מהשרת הזה בלי סיסמה",
    tag: "הכי פשוט, אם יש במכללה שרת דואר פנימי" },
  { key: "custom", name: "SMTP מותאם", sub: "כל שרת אחר: כתובת, משתמש וסיסמה" },
];
const EM_PROV_NAME = { o365_direct: "Office 365 Direct Send" };
EM_PROV.forEach((p) => { EM_PROV_NAME[p.key] = p.name; });
const EM_SEV = { info: "מידע ומעלה", warn: "אזהרה ומעלה", err: "שגיאות בלבד" };
const EM_SEV_RULE = { info: "כל האירועים", warn: "שגיאות ואזהרות", err: "שגיאות" };
const EM_LANG = { he: "עברית", en: "English" };
const EM_SEC = { starttls: "STARTTLS", tls: "TLS", none: "בלי הצפנה" };
const EM_STATE = { pending: ["run", "ממתין"], sending: ["run", "בשליחה"], sent: ["ok", "נשלח"], failed: ["err", "נכשל"] };
const EM_GMAIL_HELP = "https://support.google.com/accounts/answer/185833";
const EM_MS_NEEDS = "כדי ש\"התחבר\" יעבוד, מנהל Microsoft 365 של המכללה צריך לרשום אפליקציה ב-Azure: הרשאת SMTP.Send והפעלה של Allow public client flows. את מזהה האפליקציה ואת ה-tenant מזינים תחת \"מתקדם\" ← פרטי SMTP.";

const emIsMail = (s) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s);
const emIsHost = (s) => /^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$/.test(s) && s.includes(".");
const emMail = (a) => `<bdi class="em-mail">${esc(a)}</bdi>`;
const emBtn = (label, fn, cls = "btn sm", attrs = "") => `<button class="${cls}" type="button" onclick="${fn}"${attrs}>${label}</button>`;
const emVal = (id) => { const el = document.getElementById(id); return el ? el.value : ""; };
const EM_ICON = {
  chev: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
  copy: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>',
  send: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 3 10 14M21 3l-7 18-4-7-7-4z"/></svg>',
  plus: '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
};

/* ---------- נתונים ---------- */
function emailAlertsAllowed() { return isAdmin() && !SERVER_CTX; }
async function loadEmailAlerts() {
  try { EM_VIEW = await api(EM_API); EM_ERR = ""; }
  catch (e) { EM_VIEW = null; EM_ERR = e.message; }
  if (EM.adv) await loadEmailHistory(false);
  emRerender();
}
async function loadEmailHistory(render = true) {
  try { EM_HIST = await api(EM_API + "/history"); EM_HIST_ERR = ""; }
  catch (e) { EM_HIST = null; EM_HIST_ERR = e.message; }
  if (render) emRerender();
}
function emRerender() { if (typeof current !== "undefined" && current === "settings") renderCurrent(); }
/* תשובת כל שינוי היא ה-GET המלא — מחליפים את התמונה בלי בקשה נוספת. */
function emTake(view) { if (view && Array.isArray(view.accounts)) EM_VIEW = view; return view; }

/* החשבון שהמסך מציג: של הכלל הראשון (אליו מצטרף נמען מ"לאן לשלוח"), אחרת הראשון. */
function emAccount() {
  const v = EM_VIEW;
  if (!v || !v.accounts || !v.accounts.length) return null;
  const r = v.rules && v.rules[0];
  return (r && v.accounts.find((a) => a.id === r.account_id)) || v.accounts[0];
}
function emConfigured(a) {
  if (!a) return false;
  if (a.auth === "oauth") return !!a.oauth_connected;
  if (a.provider === "gmail") return !!a.password_set && !!a.from_addr;
  return !!a.host && !!a.from_addr;
}
function emPrimaryTo() {
  const r = EM_VIEW && EM_VIEW.rules && EM_VIEW.rules[0];
  return r && r.recipients.length ? r.recipients[0].address : null;
}
/* נמענים — האיחוד של כל הכללים (השרת שומר נמען רק בתוך כלל). */
function emPeople() {
  const out = [];
  ((EM_VIEW && EM_VIEW.rules) || []).forEach((r) => r.recipients.forEach((x) => {
    if (!out.some((p) => p.address === x.address)) out.push({ address: x.address, lang: x.lang });
  }));
  return out;
}
function emCatLabel(key) {
  const c = ((EM_VIEW && EM_VIEW.categories) || []).find((x) => x.key === key);
  return c ? c.label : key;
}
function emCatsText(cats) {
  const all = ((EM_VIEW && EM_VIEW.categories) || []).length;
  return all && cats.length === all ? "הכול" : cats.map(emCatLabel).join(", ");
}
function emPreset(provider) { return ((EM_VIEW && EM_VIEW.providers) || []).find((p) => p.key === provider) || null; }
function emWhen(ts) { return ts ? fmtWhenShort(ts) : "—"; }
/* למה אי אפשר לשלוח — במילים קצרות. */
function emCantSend(a) {
  if (!a) return "לא נבחר ספק";
  if (a.secret_error) return "קובץ הסוד לא נקרא";
  if (a.auth === "oauth" && !a.oauth_connected) return "החשבון לא מחובר";
  if (a.auth === "password" && !a.password_set) return "אין סיסמה";
  if (!emConfigured(a)) return "ההגדרה לא הושלמה";
  return a.blocked ? "החשבון חסום" : "";
}

/* ---------- 1. איך שולחים ---------- */
function emCard(title, body, { small = "", acts = "", flush = false, foot = "" } = {}) {
  return `<section class="set-grp">${UI.card({ title, small, acts, body, flush })}${foot ? `<p class="set-foot">${esc(foot)}</p>` : ""}</section>`;
}
function emKv(rows) {
  return `<dl class="mkv">${rows.map(([dt, dd]) => `<dt${dt.includes('class="sub"') ? ' class="two"' : ""}>${dt}</dt><dd>${dd}</dd>`).join("")}</dl>`;
}
function emChooser(a) {
  const pick = EM.pick || (a && EM_PROV_NAME[a.provider] && a.provider) || null;
  const opts = EM_PROV.map((p) => `<label class="em-opt"><input type="radio" name="em-prov" value="${p.key}" onchange="emPick('${p.key}')"${p.key === pick ? " checked" : ""}><span class="em-ring" aria-hidden="true"></span><span><strong>${esc(p.name)}</strong><small>${esc(p.sub)}</small>${p.tag ? `<span class="em-tag">${esc(p.tag)}</span>` : ""}</span></label>`).join("");
  const panel = { office365: emO365Panel, gmail: emGmailPanel, relay: emRelayPanel, custom: emCustomPanel }[pick];
  const keep = EM.change && a ? `<div class="em-keep">${emBtn(`ביטול — להשאיר את ${esc(EM_PROV_NAME[a.provider] || a.provider)}`, "emChangeCancel()", "btn sm flat")}</div>` : "";
  return emCard("איך שולחים", `<div class="em-pad"><div class="em-opts" role="radiogroup" aria-label="איך שולחים">${opts}</div>${panel ? panel(a && a.provider === pick ? a : null) : ""}${keep}</div>`,
    { foot: "בחירת ספק קובעת לבד את השרת, הפורט וההצפנה. אפשר לראות ולשנות אותם תחת \"מתקדם\"." });
}
function emErrSlot(id) { return `<p class="em-err" id="${id}" role="alert"></p>`; }
function emO365Panel(a) {
  if (EM.device) {
    const d = EM.device, left = Math.max(0, Math.ceil((d.started + d.expires_in * 1000 - Date.now()) / 60000));
    const uri = /^https:\/\//.test(d.verification_uri || "") ? d.verification_uri : "https://microsoft.com/devicelogin";
    return `<div class="em-panel" aria-live="polite"><ol class="em-steps">
      <li><div>בדפדפן שלך, בכל מחשב, פתח את <a class="em-url" href="${esc(uri)}" target="_blank" rel="noopener noreferrer">${esc(uri.replace(/^https:\/\//, ""))}</a></div></li>
      <li><div>הקלד את הקוד:<br><span class="em-code"><b>${esc(d.user_code)}</b>${emBtn(`${EM_ICON.copy} העתקה`, "emCopyCode()")}</span></div></li>
      <li><div>היכנס עם החשבון שממנו ישלח השרת (${emMail(d.addr)}) ואשר "שליחת מייל".</div></li></ol>
      <div class="em-wait"><span class="em-spin" aria-hidden="true"></span><span class="grow">ממתין לאישור שלך ב-Microsoft… הקוד בתוקף עוד ${left} דקות.</span>${emBtn("ביטול", "emDeviceCancel()", "btn sm flat")}</div></div>`;
  }
  const addr = a ? a.from_addr || a.username : "";
  const needs = !(a && a.oauth_client_id);
  const pending = a && a.oauth_pending ? `<p class="em-help">יש התחברות פתוחה מחלון אחר. "התחבר" מתחיל אותה מחדש עם קוד חדש.</p>` : "";
  return `<div class="em-panel"><label class="em-lbl" for="em-o365-addr">כתובת החשבון ששולח</label>
    <div class="em-row"><input class="em-in mono" id="em-o365-addr" type="text" dir="ltr" placeholder="imagesrv@college.ac.il" autocomplete="off" value="${esc(addr)}">${emBtn("התחבר עם Microsoft", "emConnect()", "btn primary em-tall")}</div>${emErrSlot("em-o365-err")}
    ${needs ? `<p class="em-need" data-testid="em-ms-needs">${esc(EM_MS_NEEDS)} ${emBtn("להזנת המזהה…", "emSmtpSheet()", "btn sm flat")}</p>`
      : `<p class="em-help">יופיע קוד קצר שמקלידים בדפדפן שלך, בכל מחשב. הסיסמה לא עוברת דרך השרת.</p>`}${pending}</div>`;
}
function emGmailPanel(a) {
  const addr = a ? a.from_addr || a.username : "";
  return `<div class="em-panel"><div class="em-opts em-fields">
    <div><label class="em-lbl" for="em-g-addr">כתובת Gmail</label><input class="em-in mono" id="em-g-addr" type="text" dir="ltr" placeholder="imagesrv.alerts@gmail.com" autocomplete="off" value="${esc(addr)}"></div>
    <div><label class="em-lbl" for="em-g-pass">סיסמת אפליקציה</label><input class="em-in mono" id="em-g-pass" type="password" dir="ltr" autocomplete="new-password"></div></div>
    <div class="em-row em-save">${emBtn("שמירה", "emSaveGmail()", "btn primary em-tall")}</div>${emErrSlot("em-g-err")}
    <p class="em-help">סיסמת אפליקציה (App Password) נוצרת בחשבון Google, ודורשת אימות דו-שלבי בחשבון. <a class="em-link" href="${EM_GMAIL_HELP}" target="_blank" rel="noopener noreferrer">איך יוצרים סיסמת אפליקציה</a>. הסיסמה נשמרת בשרת ולא מוצגת שוב.</p></div>`;
}
function emRelayPanel(a) {
  return `<div class="em-panel"><div class="em-opts em-fields">
    <div><label class="em-lbl" for="em-relay">כתובת שרת הדואר</label><input class="em-in mono" id="em-relay" type="text" dir="ltr" placeholder="mail.college.local" autocomplete="off" value="${esc(a ? a.host : "")}"></div>
    <div><label class="em-lbl" for="em-relay-from">כתובת השולח</label><input class="em-in mono" id="em-relay-from" type="text" dir="ltr" placeholder="imagesrv@college.ac.il" autocomplete="off" value="${esc(a ? a.from_addr : "")}"></div></div>
    <div class="em-row em-save">${emBtn("שמירה", "emSaveRelay()", "btn primary em-tall")}</div>${emErrSlot("em-relay-err")}
    <p class="em-help">פורט 25, בלי משתמש וסיסמה. שרת הדואר צריך לקבל מכתובת ה-IP של השרת הזה.</p></div>`;
}
function emCustomPanel(a) {
  return `<div class="em-panel"><div class="em-opts em-fields">
    <div><label class="em-lbl" for="em-host">שרת הדואר</label><input class="em-in mono" id="em-host" type="text" dir="ltr" placeholder="smtp.example.org" autocomplete="off" value="${esc(a ? a.host : "")}"></div>
    <div><label class="em-lbl" for="em-from">כתובת השולח</label><input class="em-in mono" id="em-from" type="text" dir="ltr" placeholder="imagesrv@college.ac.il" autocomplete="off" value="${esc(a ? a.from_addr : "")}"></div>
    <div><label class="em-lbl" for="em-user">משתמש</label><input class="em-in mono" id="em-user" type="text" dir="ltr" autocomplete="off" value="${esc(a ? a.username : "")}"></div>
    <div><label class="em-lbl" for="em-pass">סיסמה</label><input class="em-in mono" id="em-pass" type="password" dir="ltr" autocomplete="new-password"></div></div>
    <div class="em-row em-save">${emBtn("שמירה", "emSaveCustom()", "btn primary em-tall")}</div>${emErrSlot("em-custom-err")}
    <p class="em-help">פורט 587 עם STARTTLS, אלא אם תשנה תחת "מתקדם". בלי משתמש — שליחה בלי אימות. הסיסמה נשמרת בשרת ולא מוצגת שוב.</p></div>`;
}
function emHowConfigured(a) {
  const prov = `<span class="sv">${esc(EM_PROV_NAME[a.provider] || a.provider)}</span> ${emBtn("שינוי…", "emProviderSheet()", "btn sm flat")}`;
  const remove = ["", emBtn("הסרת ההגדרה…", "emRemoveSheet()", "btn sm flat")];
  const secretErr = a.secret_error ? UI.noteTech("err", "<b>קובץ הסוד של החשבון לא נקרא.</b> השליחה חסומה עד שיוזן מחדש", a.secret_error).replace('class="note', 'class="em-res em-in-card note') : "";
  if (a.auth === "oauth") {
    return emCard("איך שולחים", secretErr + emKv([["ספק", prov],
      ["חשבון השולח", `${UI.pill("ok", "מחובר")} ${emMail(a.username || a.from_addr)} ${emBtn("התנתקות…", "emRemoveSheet()", "btn sm flat")}`]]),
      { foot: "החיבור ל-Microsoft מתחדש לבד בכל שליחה. אם הוא יבוטל בצד שלהם, מייל הבדיקה ייכשל עם הסיבה." });
  }
  if (a.auth === "none") {
    return emCard("איך שולחים", emKv([["ספק", prov],
      ["שרת הדואר", `<span class="sv mono" dir="ltr">${esc(a.host)}</span> ${emBtn("שינוי…", "emSmtpSheet()", "btn sm flat")}`],
      ["כתובת השולח", emMail(a.from_addr)], remove]),
      { foot: `פורט ${a.port}, בלי אימות. שרת הדואר צריך לקבל מכתובת ה-IP של השרת הזה.` });
  }
  const pw = a.password_set ? `<span class="sv">מוגדרת</span> ${emBtn("החלפה…", "emPasswordSheet()", "btn sm flat")}`
    : `${UI.pill("warn", "אין סיסמה")} ${emBtn("הזנת סיסמה…", "emPasswordSheet()", "btn sm primary")}`;
  const warn = !a.password_set && !a.secret_error
    ? UI.note("warn", `<b>אין סיסמה שמורה.</b> ${esc(a.blocked || "")}`).replace('class="note', 'class="em-res em-in-card note') : "";
  const rows = [["ספק", prov]];
  if (a.provider !== "gmail") rows.push(["שרת הדואר", `<span class="sv mono" dir="ltr">${esc(a.host)}</span> ${emBtn("שינוי…", "emSmtpSheet()", "btn sm flat")}`]);
  rows.push(["משתמש", `<span class="sv mono" dir="ltr">${esc(a.username)}</span> ${emBtn("שינוי…", "emSmtpSheet()", "btn sm flat")}`], ["סיסמה", pw], remove);
  return emCard("איך שולחים", secretErr + warn + emKv(rows), { foot: "הסיסמה נשמרת בשרת ואף פעם לא מוצגת שוב. שינוי השרת, הפורט, ההצפנה או המשתמש מוחק אותה." });
}

/* ---------- 2. לאן לשלוח + בדיקה ---------- */
function emWhere(a) {
  const to = emPrimaryTo();
  if (!to) {
    return emCard("לאן לשלוח", `<div class="em-pad"><label class="em-lbl" for="em-to">כתובת</label>
      <div class="em-row"><input class="em-in mono" id="em-to" type="text" dir="ltr" placeholder="it@college.ac.il" autocomplete="off">${emBtn("שמירה", "emSaveTo()", "btn em-tall" + (emConfigured(a) ? " primary" : ""))}</div>
      ${emErrSlot("em-to-err")}<p class="em-help">אפשר להוסיף עוד נמענים תחת "מתקדם".</p></div>`);
  }
  const cant = emCantSend(a), t = EM.test;
  const lbl = t ? "שלח שוב" : "שלח בדיקה";
  let state, res = "";
  if (t && t.state === "sending") state = UI.status("run", "שולח… ממתין לאישור שרת הדואר");
  else if (t && t.ok) {
    state = UI.status("ok", `שרת הדואר אישר · ${t.at}`);
    res = UI.note("ok", `מייל הבדיקה נמסר לשרת הדואר, והוא אישר שקיבל אותו (<code dir="ltr">${esc(t.code)}</code>). אם הוא לא מופיע בתיבה תוך דקה, כדאי לבדוק בדואר הזבל.`);
  } else if (t) {
    state = UI.status("err", `נכשלה · ${t.at}`);
    const before = t.code != null ? "<b>שרת הדואר סירב.</b> " : "<b>לא הגענו לתשובה של שרת הדואר.</b> ";
    res = UI.noteTech("err", before + (t.refused && t.refused.length ? `נדחו: ${t.refused.map(emMail).join(", ")}. ` : ""),
      t.code != null ? `${t.code} ${t.message || ""}` : t.message);
  } else state = `<span class="sv">${cant ? `לא ניתן לשלוח — ${esc(cant)}` : "עוד לא נשלחה"}</span>`;
  const busy = t && t.state === "sending";
  const btn = emBtn(`${EM_ICON.send} ${lbl}`, `emTest('${encodeId(to)}')`, "btn sm" + (!t && !cant ? " primary" : ""), busy || cant ? " disabled" : "");
  return emCard("לאן לשלוח", emKv([["כתובת", `${emMail(to)} ${emBtn("שינוי…", "emToSheet()", "btn sm flat")}`], ["מייל בדיקה", `${state} ${btn}`]])
    + (res ? res.replace('class="note', 'class="em-res note') : ""),
    { foot: "הצלחה מוצגת רק כששרת הדואר אישר שקיבל את המייל. כשל מוצג עם הסיבה שהשרת החזיר." });
}

/* ---------- 3. מה נשלח ---------- */
function emWhatSent() {
  const r = EM_VIEW.rules[0], to = emPrimaryTo();
  if (!to) return emCard("מה נשלח", `<div class="em-pad"><p class="em-lead">ייווצר לבד כששומרים כתובת: <b>שגיאות ואזהרות</b> מכל המערכת יישלחו לכתובת הזו, בעברית.</p></div>`);
  const langs = [...new Set(r.recipients.map((x) => EM_LANG[x.lang] || x.lang))];
  const lang = langs.length === 1 && langs[0] === "עברית" ? "בעברית" : langs.join(", ");
  const cats = emCatsText(r.categories) === "הכול" ? r.categories.map(emCatLabel).join(", ") : emCatsText(r.categories);
  return emCard("מה נשלח", emKv([[`${esc(EM_SEV_RULE[r.min_severity] || r.min_severity)}${r.enabled ? "" : " (כבוי)"}<span class="sub">${esc(cats)}</span>`,
    `<span class="sv">נשלח ל-</span>${r.recipients.map((x) => emMail(x.address)).join(", ")}<span class="sv"> · ${esc(lang)}</span> ${emBtn("עריכה…", `emRuleSheet('${encodeId(r.id)}')`, "btn sm flat")}`]]),
    { foot: "נוצר לבד. אירועים צמודים מאוחדים למייל אחד, ואותו סוג אירוע לא נשלח שוב בתוך 15 דקות." });
}

/* ---------- מתקדם ---------- */
function emAdvanced(a) {
  const rules = EM_VIEW.rules, people = emPeople();
  const rulesHtml = rules.length ? UI.datagrid({ cls: "kebab-col", columns: ["קטגוריות", "חומרה", "נמענים", { html: '<span class="sr">פעולות</span>' }],
    rows: rules.map((r, i) => [UI.nameHtml(emCatsText(r.categories), [i === 0 ? "ברירת המחדל" : "", r.name && r.name !== "ברירת מחדל" ? esc(r.name) : "", r.enabled ? "" : "כבוי"].filter(Boolean).join(" · ")),
      esc(EM_SEV[r.min_severity] || r.min_severity),
      r.recipients.length ? r.recipients.map((x) => emMail(x.address)).join(", ") : UI.status("warn", "בלי נמענים — כבוי"),
      UI.rowMenu(emCatsText(r.categories), [["עריכה…", `emRuleSheet('${encodeId(r.id)}')`], ["שכפול", `emRuleDup('${encodeId(r.id)}')`], ["מחיקה…", `emRuleDelete('${encodeId(r.id)}')`, "danger"]])]) })
    : `<div class="empty">אין כללים. לא יישלח שום מייל.<div>${emBtn("כלל חדש…", "emRuleSheet('')", "btn")}</div></div>`;
  const peopleHtml = people.length ? UI.datagrid({ cls: "kebab-col", columns: ["כתובת", "שפה", "כללים", { html: '<span class="sr">פעולות</span>' }],
    rows: people.map((p) => [emMail(p.address), esc(EM_LANG[p.lang] || p.lang), String(rules.filter((r) => r.recipients.some((x) => x.address === p.address)).length),
      UI.rowMenu(p.address, [["עריכה…", `emPersonSheet('${encodeId(p.address)}')`], ["שליחת מייל בדיקה", `emTest('${encodeId(p.address)}')`], ["מחיקה…", `emPersonDelete('${encodeId(p.address)}')`, "danger"]])]) })
    : `<div class="empty">אין נמענים.<div>${emBtn("נמען חדש…", "emPersonSheet('')", "btn")}</div></div>`;
  const prov = a ? a.provider : EM.pick, preset = emPreset(prov);
  let smtp;
  if (a || preset) {
    const s = a || { host: preset.host || "", port: preset.port, security: preset.security, auth: preset.auth[0], username: "", from_addr: "", oauth_client_id: "", oauth_tenant: "organizations" };
    const auth = s.auth === "oauth" ? "OAuth של Microsoft (לא סיסמה)" : s.auth === "password" ? `משתמש ${s.username || "—"}, ${a && a.password_set ? "סיסמה מוגדרת" : "אין סיסמה"}` : "בלי אימות";
    const rows = [["שרת", s.host ? `<span class="sv mono" dir="ltr">${esc(s.host)}</span>` : '<span class="muted">לא הוזן</span>'],
      ["פורט והצפנה", `<span class="sv"><bdi dir="ltr">${esc(s.port)}</bdi> · ${esc(EM_SEC[s.security] || s.security)}</span>`],
      ["אימות", `<span class="sv">${esc(auth)}</span>`],
      ["כתובת השולח", s.from_addr ? emMail(s.from_addr) : '<span class="muted">לא הוזנה</span>']];
    if (prov === "office365") {
      rows.push(["מזהה אפליקציה", s.oauth_client_id ? `<span class="sv mono" dir="ltr">${esc(s.oauth_client_id)}</span>` : UI.pill("warn", "לא הוזן")],
        ["tenant", `<span class="sv mono" dir="ltr">${esc(s.oauth_tenant || "organizations")}</span>`]);
    }
    smtp = emKv(rows);
  } else smtp = `<div class="em-pad"><p class="em-lead">ייקבעו לבד כשתבחר ספק למעלה.</p></div>`;
  const hist = EM_HIST_ERR ? UI.note("err", `לא הצלחתי לקרוא את ההיסטוריה: ${esc(EM_HIST_ERR)}`)
    : !EM_HIST ? `<div class="em-pad muted">טוען…</div>`
    : EM_HIST.queue.length ? UI.datagrid({ cls: "em-hist", columns: ["זמן", "מצב", "כלל", "נושא", "קוד"],
      rows: EM_HIST.queue.map((h) => {
        const st = EM_STATE[h.state] || ["", h.state];
        const txt = h.state === "pending" && h.attempts ? `${st[1]} · ניסיון ${h.attempts + 1}` : st[1];
        return { attrs: `class="clickable" tabindex="0" role="button" aria-label="${esc("פרטים: " + h.label)}" onclick="emHistSheet(${Number(h.id)})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();emHistSheet(${Number(h.id)})}"`,
          cells: [`<bdi>${esc(emWhen(h.sent_at || h.created_at))}</bdi>`, UI.status(st[0], txt), esc(emRuleLabel(h.rule_id, h.rule_name)), esc(h.label), `<span class="mono">${h.code != null ? esc(h.code) : "—"}</span>`] };
      }) })
    : `<div class="empty">עוד לא נשלח מייל.</div>`;
  return `<details class="em-adv"${EM.adv ? " open" : ""} ontoggle="emAdvToggle(this)"><summary>מתקדם <small>כללים נוספים, נמענים ושפה, פרטי SMTP, היסטוריה</small>${EM_ICON.chev}</summary>
    ${emCard("כללים", rulesHtml, { flush: true, acts: emBtn(`${EM_ICON.plus} כלל חדש…`, "emRuleSheet('')"), foot: "כל אירוע נבדק מול כל הכללים. כלל שולח אירוע אם הקטגוריה שלו מסומנת והחומרה שלו לפחות כמו בכלל." })}
    ${emCard("נמענים", peopleHtml, { flush: true, acts: emBtn(`${EM_ICON.plus} נמען חדש…`, "emPersonSheet('')"), foot: "השפה היא לכל נמען: אותו אירוע יוצא בעברית לאחד ובאנגלית לאחר. נמען נשמר בתוך כלל." })}
    ${emCard("פרטי SMTP", smtp, { acts: a || preset ? emBtn("עריכה ידנית…", "emSmtpSheet()", "btn sm flat") : "", foot: "נקבעו לבד לפי הספק. שינוי השרת, הפורט, ההצפנה, המשתמש או מזהה האפליקציה מוחק סיסמה או חיבור שמורים." })}
    ${emCard("היסטוריה", hist, { flush: true, small: "50 האחרונים · לחיצה על שורה פותחת את הפרטים", foot: "מייל שלא נשלח נשאר בתור ומנוסה שוב, בהפסקות שגדלות. כל ניסיון נרשם גם ביומן." })}
  </details>`;
}
function emAdvToggle(el) {
  if (!el.isConnected || EM.adv === el.open) return;
  EM.adv = el.open;
  if (EM.adv) loadEmailHistory();
}

/* ---------- שורת המצב ---------- */
function emTopNote(a) {
  const n = (cls, html) => UI.note(cls, html).replace('class="note', 'class="em-top note');
  const out = [];
  if (EM_VIEW.config_error) out.push(n("err", `<b>ההגדרות השמורות לא פוענחו.</b> שום מייל לא יוצא עד שמירה מחדש. ${esc(EM_VIEW.config_error)}`));
  if (!EM_VIEW.running) out.push(n("err", "<b>שליחת ההתראות לא רצה בשרת.</b> שום מייל לא יוצא, גם כשההגדרה תקינה. הפעלה מחדש של השרת מפעילה אותה."));
  (EM_VIEW.warnings || []).forEach((w) => out.push(n("warn", esc(w))));
  const to = emPrimaryTo();
  if (!emConfigured(a) || EM.change) {
    if (EM.device) out.push(n("info", "<b>מתחבר ל-Microsoft.</b> השלב הבא מחכה לך בדפדפן."));
    else if (EM.pick || a) out.push(n("info", "<b>כמעט מוכן.</b> השלם את הספק שבחרת, ואז שמור כתובת ושלח בדיקה."));
    else out.push(n("info", "<b>התראות במייל לא מוגדרות.</b> השרת לא ישלח מייל עד שתבחר איך לשלוח ולאן. זה לוקח דקה."));
  } else if (a.auth === "password" && !a.password_set) out.push(n("warn", "<b>התראות מושהות:</b> אין סיסמה לשרת הדואר. מיילים חדשים ממתינים בתור עד שתוזן סיסמה."));
  else if (!to) out.push(n("info", "<b>כמעט מוכן.</b> שמור את הכתובת שאליה לשלוח."));
  else if (EM.test && EM.test.state !== "sending" && !EM.test.ok) out.push(n("err", "<b>מייל הבדיקה נכשל.</b> התראות לא יגיעו עד שזה יתוקן. הסיבה מופיעה למטה, ליד הבדיקה."));
  else if ((EM.test && EM.test.ok) || EM_VIEW.queue.last_sent_at) {
    const r = EM_VIEW.rules[0];
    out.push(n("ok", `<b>התראות פעילות.</b> ${esc(EM_SEV_RULE[r.min_severity] || "")} נשלחות ל-${emMail(to)}.`));
  } else out.push(n("info", "<b>כמעט מוכן.</b> שלח מייל בדיקה כדי לוודא שהוא מגיע."));
  return out.join("");
}

function emailAlertsTab() {
  if (EM_ERR) return `<div class="set-col">${UI.note("err", `לא הצלחתי לקרוא את הגדרות ההתראות במייל: ${esc(EM_ERR)}`)}</div>`;
  if (!EM_VIEW) return `<div class="set-col"><div class="muted">טוען…</div></div>`;
  const a = emAccount();
  return `<div class="set-col em">${emTopNote(a)}${!emConfigured(a) || EM.change ? emChooser(a) : emHowConfigured(a)}${emWhere(a)}${emWhatSent()}${emAdvanced(a)}</div>`;
}

/* ---------- פעולות: ספק ---------- */
function emPick(key) { EM.pick = key; renderCurrent(); }
function emChangeCancel() { EM.change = false; EM.pick = null; renderCurrent(); }
function emPanelErr(id, text) { const el = document.getElementById(id); if (el) el.textContent = text; }
/* שמירת החשבון: חדש (POST) או שינוי (PUT) — רק השדות שהשתנו; ספק נשלח רק כשהוא משתנה,
   כי השרת מאפס לפיו פורט, הצפנה ומארח (ושינוי כזה מוחק סודות). */
async function emSaveAccount(fields) {
  const a = emAccount();
  if (!a) { const r = emTake(await post(EM_API + "/accounts", fields)); return EM_VIEW.accounts.find((x) => x.id === r.account_id); }
  const body = {};
  Object.keys(fields).forEach((k) => { if (k === "password" || a[k] !== fields[k]) body[k] = fields[k]; });
  if (Object.keys(body).length) emTake(await put(`${EM_API}/accounts/${encodeURIComponent(a.id)}`, body));
  return EM_VIEW.accounts.find((x) => x.id === a.id);
}
async function emSaved(text) { EM.change = false; EM.pick = null; EM.test = null; toast(text, 3200, "ok"); renderCurrent(); }
async function emSaveRelay() {
  const host = emVal("em-relay").trim(), from = emVal("em-relay-from").trim();
  if (!emIsHost(host)) return emPanelErr("em-relay-err", host ? "זו לא כתובת שרת. לדוגמה: mail.college.local" : "הקלד את כתובת שרת הדואר");
  if (!emIsMail(from)) return emPanelErr("em-relay-err", "הקלד את כתובת השולח, לדוגמה imagesrv@college.ac.il");
  try { await emSaveAccount({ provider: "relay", host, from_addr: from }); }
  catch (e) { return emPanelErr("em-relay-err", e.message); }
  emSaved("נשמר. שלח בדיקה כדי לוודא ששרת הדואר מקבל מהשרת הזה.");
}
async function emSaveCustom() {
  const host = emVal("em-host").trim(), from = emVal("em-from").trim(), user = emVal("em-user").trim(), pass = emVal("em-pass");
  const errs = [];
  if (!emIsHost(host)) errs.push("כתובת שרת לא תקינה");
  if (!emIsMail(from)) errs.push("כתובת שולח לא תקינה");
  if (user && !pass) errs.push("חסרה סיסמה");
  if (!user && pass) errs.push("סיסמה בלי משתמש");
  if (errs.length) return emPanelErr("em-custom-err", errs.join(" · "));
  const fields = { provider: "custom", host, from_addr: from, auth: user ? "password" : "none", username: user };
  if (pass) fields.password = pass;
  try { await emSaveAccount(fields); }
  catch (e) { return emPanelErr("em-custom-err", e.message); }
  emSaved(pass ? "נשמר. הסיסמה לא תוצג שוב." : "נשמר.");
}
async function emSaveGmail() {
  const addr = emVal("em-g-addr").trim(), pass = emVal("em-g-pass");
  if (!emIsMail(addr)) return emPanelErr("em-g-err", addr ? "זו לא כתובת מייל תקינה" : "הקלד את כתובת ה-Gmail");
  if (!pass) return emPanelErr("em-g-err", "הקלד סיסמת אפליקציה");
  try { await emSaveAccount({ provider: "gmail", auth: "password", username: addr, from_addr: addr, password: pass }); }
  catch (e) { return emPanelErr("em-g-err", e.message); }
  emSaved("נשמר. הסיסמה לא תוצג שוב.");
}
/* "התחבר עם Microsoft": שומר את הכתובת (חשבון office365/oauth), ורק עם מזהה אפליקציה מבקש קוד. */
async function emConnect() {
  const addr = emVal("em-o365-addr").trim();
  if (!emIsMail(addr)) return emPanelErr("em-o365-err", addr ? "זו לא כתובת מייל תקינה" : "הקלד את כתובת החשבון ששולח");
  let a;
  try { a = await emSaveAccount({ provider: "office365", auth: "oauth", username: addr, from_addr: addr }); }
  catch (e) { return emPanelErr("em-o365-err", e.message); }
  EM.pick = "office365";
  if (!a.oauth_client_id) { renderCurrent(); return emPanelErr("em-o365-err", "עוד אין מזהה אפליקציה. " + EM_MS_NEEDS); }
  let d;
  try { d = await post(`${EM_API}/accounts/${encodeURIComponent(a.id)}/oauth/start`); }
  catch (e) { renderCurrent(); return emPanelErr("em-o365-err", e.message); }
  EM.device = { user_code: d.user_code, verification_uri: d.verification_uri, expires_in: d.expires_in || 900,
    interval: d.interval || 5, started: Date.now(), id: a.id, addr };
  renderCurrent();
  emPollLater();
}
function emPollLater() {
  clearTimeout(EM.pollT);
  if (EM.device) EM.pollT = setTimeout(emPoll, Math.max(2, EM.device.interval) * 1000);
}
/* השרת שומר את האסימון רק בתשובה ל-poll — ולכן ממשיכים לשאול גם כשעוברים לעמוד אחר. */
async function emPoll() {
  const d = EM.device;
  if (!d) return;
  let r;
  try { r = await post(`${EM_API}/accounts/${encodeURIComponent(d.id)}/oauth/poll`); }
  catch (e) { r = { state: "error", message: e.message }; }
  if (EM.device !== d) return;
  if (r.state === "pending") { if (r.interval) d.interval = r.interval; emRerender(); emPollLater(); return; }
  EM.device = null;
  if (r.state === "connected") { EM.change = false; EM.pick = null; EM.test = null; toast(`מחובר כ-${d.addr}`, 3200, "ok"); }
  else toast(r.message || "ההתחברות לא הושלמה", 6000, "err");
  await loadEmailAlerts();
}
async function emDeviceCancel() {
  const d = EM.device;
  EM.device = null; clearTimeout(EM.pollT);
  try { if (d) emTake(await del(`${EM_API}/accounts/${encodeURIComponent(d.id)}/oauth`)); } catch (e) { toast(e.message); }
  toast("ההתחברות בוטלה. הקוד לא יעבוד יותר.");
  renderCurrent();
}
function emCopyCode() {
  const code = EM.device && EM.device.user_code;
  if (!code) return;
  const ok = () => toast(`הקוד ${code} הועתק`, 2600, "ok"), fail = () => toast("לא הצלחתי להעתיק — סמן את הקוד והעתק ידנית");
  try { navigator.clipboard.writeText(code).then(ok, fail); } catch (e) { fail(); }
}
function emProviderSheet() {
  const a = emAccount();
  if (!a) return;
  const what = a.auth === "oauth" ? `מנתקת את החשבון ${a.username || a.from_addr}` : a.auth === "password" ? "מוחקת את הסיסמה השמורה" : "מחליפה את שרת הדואר";
  sheet({ title: "שינוי ספק", sub: "איך השרת שולח מייל.", size: "mid", submitLabel: "המשך",
    fields: [{ id: "prov", type: "radio", value: a.provider, options: EM_PROV.map((p) => ({ value: p.key, label: p.name, hint: p.sub })) }],
    note: impactList([["w", `ההגדרה הנוכחית נשארת פעילה עד שתשמור את הספק החדש. השמירה ${esc(what)}.`], ["ok", "הכתובת, הכללים וההיסטוריה נשמרים."]]),
    onSubmit: async (v) => {
      if (!v.prov) throw new Error("בחר ספק.");
      if (v.prov === a.provider) throw new Error("זה הספק הנוכחי. בחר ספק אחר, או ביטול.");
      EM.change = true; EM.pick = v.prov; EM.device = null;
      renderCurrent();
    } });
}
/* הסרה/התנתקות — כפתור אדום, בלי הקלדה (#1270). */
function emRemoveSheet() {
  const a = emAccount();
  if (!a) return;
  const oauth = a.auth === "oauth";
  confirmSheet(oauth ? "להתנתק מחשבון Microsoft?" : "להסיר את הגדרת שרת הדואר?",
    oauth ? `השרת יפסיק לשלוח מייל מ-${a.username || a.from_addr}.` : `השרת יפסיק לשלוח מייל דרך ${a.host || EM_PROV_NAME[a.provider]}.`,
    oauth ? "התנתקות" : "הסרה",
    async () => {
      if (oauth) emTake(await del(`${EM_API}/accounts/${encodeURIComponent(a.id)}/oauth`));
      else {
        const body = { clear_secrets: true };
        if (a.provider !== "gmail") body.host = "";
        emTake(await put(`${EM_API}/accounts/${encodeURIComponent(a.id)}`, body));
      }
      EM.test = null; EM.change = false; EM.pick = null;
      toast(oauth ? "נותק. התראות במייל לא יוצאות עד שתתחבר שוב." : "ההגדרה הוסרה.");
      renderCurrent();
    },
    [["w", "התראות לא יישלחו עד שתגדיר שוב איך שולחים. אירועים בינתיים ממתינים בתור, בלי לבזבז ניסיונות."],
      ["ok", "הכתובת, הכללים, הנמענים וההיסטוריה נשמרים."], ["ok", "הפעולה נרשמת ביומן."]]);
}
function emPasswordSheet() {
  const a = emAccount();
  if (!a) return;
  sheet({ title: "סיסמה לשרת הדואר", sub: `${a.username} ב-${a.host}`,
    fields: [{ id: "pw", type: "password", label: a.provider === "gmail" ? "סיסמת אפליקציה" : "סיסמה" }],
    note: `<p class="n-help">נשמרת בשרת ולא מוצגת שוב. היא תימחק אם השרת, הפורט, ההצפנה או המשתמש ישתנו.</p>`,
    onSubmit: async (v) => {
      if (!v.pw) throw new Error("הקלד סיסמה");
      emTake(await put(`${EM_API}/accounts/${encodeURIComponent(a.id)}`, { password: v.pw }));
      EM.test = null; toast("הסיסמה נשמרה. שלח בדיקה.", 3200, "ok"); renderCurrent();
    } });
}
/* פרטי SMTP ידניים. בלי חשבון — יוצר אותו עם הספק שנבחר למעלה (כך מזינים מזהה אפליקציה לפני "התחבר"). */
function emSmtpSheet() {
  const a = emAccount(), prov = a ? a.provider : EM.pick || "office365", preset = emPreset(prov);
  if (!a && !preset) return;
  const s = a || { host: preset.host || "", port: preset.port, security: preset.security, auth: preset.auth[0], username: "", from_addr: "", oauth_client_id: "", oauth_tenant: "organizations" };
  const fields = [
    { id: "host", label: "שרת", value: s.host, dir: "ltr" },
    { id: "port", label: "פורט", type: "number", value: String(s.port), dir: "ltr" },
    { id: "security", label: "הצפנה", type: "select", value: s.security, options: [{ value: "starttls", label: "STARTTLS" }, { value: "tls", label: "TLS (465)" }, { value: "none", label: "בלי הצפנה" }] }];
  if (s.auth !== "oauth") fields.push({ id: "from_addr", label: "כתובת השולח", value: s.from_addr, dir: "ltr" });
  if (s.auth === "password") fields.push({ id: "username", label: "משתמש", value: s.username, dir: "ltr" },
    { id: "password", type: "password", label: "סיסמה (ריק = בלי שינוי)" });
  if (prov === "office365") fields.push({ id: "oauth_client_id", label: "מזהה אפליקציה (Application / client ID)", value: s.oauth_client_id, dir: "ltr", placeholder: "00000000-0000-0000-0000-000000000000" },
    { id: "oauth_tenant", label: "tenant (Directory ID או דומיין; ריק = organizations)", value: s.oauth_tenant === "organizations" ? "" : s.oauth_tenant, dir: "ltr" });
  const lose = s.auth === "oauth" ? "ינתק את החשבון המחובר" : s.auth === "password" ? "ימחק את הסיסמה השמורה, אלא אם תזין אותה כאן" : "יחול מהמייל הבא";
  const reset = preset ? `<p class="n-help">${emBtn("החזרה לברירת המחדל של הספק", `emSmtpReset('${encodeId(prov)}')`, "btn sm flat")}</p>` : "";
  sheet({ title: "פרטי SMTP", sub: "נקבעו לבד לפי הספק. לשנות רק אם מנהל הדואר ביקש.", size: "mid",
    note: impactList([["w", `שינוי השרת, הפורט, ההצפנה${s.auth === "password" ? ", המשתמש" : ""}${prov === "office365" ? ", מזהה האפליקציה או ה-tenant" : ""} ${lose}.`]]) + reset
      + (s.auth === "oauth" && s.from_addr ? `<p class="n-help">כתובת השולח: ${emMail(s.from_addr)} — משתנה ב"התחבר" עם כתובת אחרת.</p>` : ""),
    fields,
    onSubmit: async (v) => {
      const port = Number(v.port);
      if (v.host.trim() && !emIsHost(v.host.trim())) throw new Error("כתובת שרת לא תקינה. לדוגמה: smtp.office365.com");
      if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("פורט בין 1 ל-65535");
      if ("from_addr" in v && v.from_addr.trim() && !emIsMail(v.from_addr.trim())) throw new Error("כתובת שולח לא תקינה");
      const fieldsOut = { host: v.host.trim(), port, security: v.security };
      ["from_addr", "username", "oauth_client_id"].forEach((k) => { if (k in v) fieldsOut[k] = v[k].trim(); });
      if ("oauth_tenant" in v) fieldsOut.oauth_tenant = v.oauth_tenant.trim() || "organizations";
      if (v.password) fieldsOut.password = v.password;
      if (!a) fieldsOut.provider = prov;
      await emSaveAccount(fieldsOut);
      EM.test = null; EM.device = null;
      toast("פרטי ה-SMTP נשמרו.", 2600, "ok"); renderCurrent();
    } });
}
function emSmtpReset(provEnc) {
  const p = emPreset(decodeURIComponent(provEnc));
  if (!p) return;
  const set = (id, val) => { const el = document.getElementById("sf-" + id); if (el) el.value = val; };
  if (p.host) set("host", p.host);
  set("port", String(p.port)); set("security", p.security);
  toast("הוחזרו ערכי ברירת המחדל של הספק (עוד לא נשמר).");
}

/* ---------- פעולות: לאן לשלוח ובדיקה ---------- */
async function emSaveTo() {
  const v = emVal("em-to").trim();
  if (!emIsMail(v)) return emPanelErr("em-to-err", v ? "זו לא כתובת מייל תקינה" : "הקלד כתובת מייל");
  let r;
  try { r = emTake(await post(EM_API + "/recipients", { address: v })); }
  catch (e) { return emPanelErr("em-to-err", e.message); }
  EM.test = null;
  toast(r.rule_created ? "נשמר. נוצר כלל: שגיאות ואזהרות לכתובת הזו." : "נשמר.", 3200, "ok");
  renderCurrent();
}
/* החלפת כתובת בכל הכללים, באותו מקום ובאותה שפה. */
async function emReplaceAddress(oldAddr, newAddr, lang = null) {
  for (const r of EM_VIEW.rules) {
    if (!r.recipients.some((x) => x.address === oldAddr)) continue;
    const recipients = r.recipients.filter((x) => x.address !== newAddr || x.address === oldAddr)
      .map((x) => x.address === oldAddr ? { address: newAddr, lang: lang || x.lang } : x);
    emTake(await put(`${EM_API}/rules/${encodeURIComponent(r.id)}`, { recipients }));
  }
}
function emToSheet() {
  const old = emPrimaryTo();
  if (!old) return;
  sheet({ title: "לאן לשלוח", sub: "הכתובת הראשית. נמענים נוספים מוגדרים תחת \"מתקדם\".",
    fields: [{ id: "to", label: "כתובת", value: old, dir: "ltr", help: "הכתובת מוחלפת בכל הכללים שהיא מופיעה בהם. מייל הבדיקה יתאפס." }],
    onSubmit: async (v) => {
      const to = v.to.trim();
      if (!emIsMail(to)) throw new Error(to ? "זו לא כתובת מייל תקינה. לדוגמה: it@college.ac.il" : "הקלד כתובת מייל");
      if (to === old) return;
      await emReplaceAddress(old, to);
      EM.test = null; toast("הכתובת עודכנה. שלח בדיקה לכתובת החדשה.", 3200, "ok"); renderCurrent();
    } });
}
async function emTest(toEnc) {
  const a = emAccount(), to = decodeURIComponent(toEnc);
  if (!a) return toast("אי אפשר לשלוח בדיקה: עוד לא נבחר איך שולחים.", 3200, "err");
  const lang = (emPeople().find((p) => p.address === to) || {}).lang;
  EM.test = { state: "sending", to }; renderCurrent();
  let r;
  try { r = await post(`${EM_API}/accounts/${encodeURIComponent(a.id)}/test`, lang ? { to, lang } : { to }); }
  catch (e) { r = { ok: false, code: null, message: e.message, refused: [] }; }
  EM.test = { state: "done", to, ok: !!r.ok, code: r.code, message: r.message, refused: r.refused || [], at: `היום ${new Date().toTimeString().slice(0, 5)}` };
  toast(r.ok ? `שרת הדואר אישר את מייל הבדיקה ל-${to}.` : "מייל הבדיקה נכשל. הסיבה מופיעה ליד הבדיקה.", 3200, r.ok ? "ok" : "err");
  if (EM.adv) await loadEmailHistory(false);
  renderCurrent();
}

/* ---------- פעולות: כללים ---------- */
function emRuleById(idEnc) { const id = decodeURIComponent(idEnc || ""); return EM_VIEW.rules.find((r) => r.id === id) || null; }
function emChecks(name, items) {
  return `<div class="em-checks">${items.map(([value, label, on]) => `<label class="check"><input type="checkbox" name="${name}" value="${esc(value)}"${on ? " checked" : ""}> ${label}</label>`).join("")}</div>`;
}
function emChecked(name) { return [...document.querySelectorAll(`#sheet input[name="${name}"]:checked`)].map((i) => i.value); }
function emSegHtml(id, label, options, value, help) {
  return `<div class="n-f"><label class="n-lbl" id="sf-${id}-label">${esc(label)}</label><div class="n-seg" role="group" aria-labelledby="sf-${id}-label" data-segment="${id}">${options.map(([v, l]) => `<button type="button" data-value="${v}" aria-pressed="${v === value}">${esc(l)}</button>`).join("")}</div><input type="hidden" id="sf-${id}" value="${esc(value)}"><div class="n-help">${esc(help)}</div></div>`;
}
function emRuleSheet(idEnc) {
  const r = emRuleById(idEnc), people = emPeople();
  const cur = r || { categories: [], min_severity: "warn", recipients: [] };
  const body = `<fieldset class="n-f"><legend class="n-lbl">קטגוריות</legend>${emChecks("em-rc", EM_VIEW.categories.map((c) => [c.key, esc(c.label), cur.categories.includes(c.key)]))}</fieldset>`
    + emSegHtml("sev", "מהחומרה", [["info", "מידע"], ["warn", "אזהרה"], ["err", "שגיאה"]], cur.min_severity, "\"אזהרה\" שולח אזהרות ושגיאות. \"מידע\" שולח גם אירועים רגילים, כמו עדכון שהותקן.")
    + `<fieldset class="n-f"><legend class="n-lbl">נמענים</legend>${people.length ? emChecks("em-rt", people.map((p) => [p.address, `${emMail(p.address)} · ${esc(EM_LANG[p.lang] || p.lang)}`, cur.recipients.some((x) => x.address === p.address)])).replace('class="em-checks"', 'class="em-checks one"') : '<p class="n-help">אין נמענים עדיין. נמען ראשון נשמר ב"לאן לשלוח".</p>'}</fieldset>`;
  sheet({ title: r ? "עריכת כלל" : "כלל חדש", sub: "מה שולחים, מאיזו חומרה, ולמי.", size: "mid", note: body,
    extra: r ? { label: "מחיקת הכלל", onClick: () => emRuleDelete(idEnc) } : null,
    onSubmit: async () => {
      const categories = emChecked("em-rc"), to = emChecked("em-rt"), sev = emVal("sf-sev") || "warn";
      if (!categories.length) throw new Error("בחר לפחות קטגוריה אחת.");
      if (!to.length) throw new Error("בחר לפחות נמען אחד. נמען חדש מוסיפים בכרטיס \"נמענים\".");
      const recipients = to.map((address) => ({ address, lang: (people.find((p) => p.address === address) || {}).lang || "he" }));
      if (r) emTake(await put(`${EM_API}/rules/${encodeURIComponent(r.id)}`, { categories, min_severity: sev, recipients }));
      else {
        const a = emAccount();
        emTake(await post(EM_API + "/rules", { categories, min_severity: sev, recipients, ...(a ? { account_id: a.id } : {}) }));
      }
      toast(r ? "הכלל עודכן." : "הכלל נוסף.", 2600, "ok"); renderCurrent();
    } });
}
async function emRuleDup(idEnc) {
  const r = emRuleById(idEnc);
  if (!r) return;
  try {
    emTake(await post(EM_API + "/rules", { name: r.name ? `${r.name} (עותק)` : "", enabled: r.enabled, account_id: r.account_id,
      categories: r.categories, min_severity: r.min_severity, recipients: r.recipients }));
    toast("נוצר עותק של הכלל. ערוך אותו מהתפריט ⋯.", 3200, "ok");
  } catch (e) { toast(e.message); }
  renderCurrent();
}
function emRuleDelete(idEnc) {
  const r = emRuleById(idEnc);
  if (!r) return;
  const last = EM_VIEW.rules.length === 1, first = EM_VIEW.rules[0] === r;
  confirmSheet("למחוק את הכלל?", `${emCatsText(r.categories)} · ${EM_SEV[r.min_severity] || r.min_severity} ← ${r.recipients.map((x) => x.address).join(", ") || "בלי נמענים"}`, "מחיקה",
    async () => { emTake(await del(`${EM_API}/rules/${encodeURIComponent(r.id)}`)); toast("הכלל נמחק."); renderCurrent(); },
    [last ? ["w", "זה הכלל האחרון: אחרי המחיקה לא יישלח שום מייל."] : ["ok", "הנמענים נשארים בכללים האחרים, ויקבלו מיילים רק מהם."],
      first && !last ? ["w", "זה כלל ברירת המחדל. הכלל הבא ברשימה יהיה הראשי, ו\"לאן לשלוח\" יציג את הנמען הראשון שלו."] : ["ok", "אפשר ליצור אותו שוב בכל עת."],
      ["ok", "המחיקה נרשמת ביומן."]]);
}

/* ---------- פעולות: נמענים ---------- */
function emPersonSheet(addrEnc) {
  const orig = addrEnc ? decodeURIComponent(addrEnc) : "", p = orig ? emPeople().find((x) => x.address === orig) : null;
  const rules = EM_VIEW.rules;
  const ruleBox = rules.length ? emChecks("em-pr", rules.map((r) => [r.id, `${esc(emCatsText(r.categories))} · ${esc(EM_SEV[r.min_severity] || r.min_severity)}`,
    p ? r.recipients.some((x) => x.address === orig) : rules.length === 1])).replace('class="em-checks"', 'class="em-checks one"')
    : '<p class="n-help">אין כללים עדיין. הנמען ייכנס לכלל ברירת המחדל, שנוצר לבד.</p>';
  sheet({ title: p ? "עריכת נמען" : "נמען חדש", sub: "כתובת, שפת המייל, ואילו כללים נשלחים אליו.", size: "mid",
    fields: [{ id: "addr", label: "כתובת", value: orig, dir: "ltr", placeholder: "helpdesk@college.ac.il" },
      { id: "lang", type: "segment", label: "שפת המייל", value: p ? p.lang : "he", options: Object.entries(EM_LANG).map(([value, label]) => ({ value, label })),
        help: "גוף המייל והנושא בשפה הזו. שמות מחשבים ואימג'ים לא מתורגמים." }],
    note: "", hint: "", extra: p ? { label: "מחיקת הנמען", onClick: () => emPersonDelete(addrEnc) } : null,
    onSubmit: async (v) => {
      const addr = v.addr.trim(), lang = v.lang || "he";
      if (!emIsMail(addr)) throw new Error(addr ? "זו לא כתובת מייל תקינה" : "הקלד כתובת מייל");
      if (addr !== orig && emPeople().some((x) => x.address === addr)) throw new Error("הכתובת הזו כבר ברשימה.");
      if (!rules.length) { emTake(await post(EM_API + "/recipients", { address: addr, lang })); toast("הנמען נוסף.", 2600, "ok"); renderCurrent(); return; }
      const want = emChecked("em-pr");
      if (!want.length) throw new Error("בחר לפחות כלל אחד — נמען נשמר בתוך כלל.");
      for (const r of rules) {
        const had = r.recipients.some((x) => x.address === orig && orig);
        let recipients;
        if (want.includes(r.id)) recipients = had ? r.recipients.map((x) => x.address === orig ? { address: addr, lang } : x).filter((x, i, all) => all.findIndex((y) => y.address === x.address) === i)
          : r.recipients.filter((x) => x.address !== addr).concat([{ address: addr, lang }]);
        else recipients = r.recipients.filter((x) => x.address !== orig && x.address !== addr);
        if (JSON.stringify(recipients) !== JSON.stringify(r.recipients)) emTake(await put(`${EM_API}/rules/${encodeURIComponent(r.id)}`, { recipients }));
      }
      toast(p ? "הנמען עודכן." : "הנמען נוסף.", 2600, "ok"); renderCurrent();
    } });
  // רשימת הכללים — אחרי השדות, כמו במוקאפ
  const form = document.getElementById("sheet"), err = document.getElementById("sf-error");
  if (form && err) err.insertAdjacentHTML("beforebegin", `<fieldset class="n-f"><legend class="n-lbl">כללים</legend>${ruleBox}</fieldset>`);
}
function emPersonDelete(addrEnc) {
  const addr = decodeURIComponent(addrEnc);
  if (addr === emPrimaryTo()) {
    sheet({ title: "אי אפשר למחוק את הכתובת הראשית", note: `<p class="n-help">${emMail(addr)} היא הכתובת שב"לאן לשלוח". קודם שנה אותה שם, ואז אפשר יהיה למחוק אותה מכאן.</p>`,
      cancelLabel: "סגירה", submitLabel: "לשינוי הכתובת", onSubmit: async () => emToSheet });
    return;
  }
  const used = EM_VIEW.rules.filter((r) => r.recipients.some((x) => x.address === addr));
  const orphan = used.filter((r) => r.recipients.length === 1);
  confirmSheet("למחוק את הנמען?", addr, "מחיקה",
    async () => {
      for (const r of used) emTake(await put(`${EM_API}/rules/${encodeURIComponent(r.id)}`, { recipients: r.recipients.filter((x) => x.address !== addr) }));
      toast("הנמען נמחק."); renderCurrent();
    },
    [["ok", used.length ? `יוסר מ-${used.length} כללים.` : "הוא לא מופיע באף כלל."],
      ...(orphan.length ? [["w", `${orphan.length} כללים יישארו בלי נמענים ולא ישלחו: ${orphan.map((r) => esc(emCatsText(r.categories))).join("; ")}.`]] : []),
      ["ok", "המחיקה נרשמת ביומן."]]);
}

/* ---------- היסטוריה: מה שהשרת שומר — מצב, תשובת השרת, ניסיונות ושורות האירוע ---------- */
/* שם הכלל, ובלעדיו — הקטגוריות שלו (כמו בטבלת הכללים). */
function emRuleLabel(id, name) {
  if (name) return name;
  const r = EM_VIEW && EM_VIEW.rules.find((x) => x.id === id);
  return r ? emCatsText(r.categories) : "—";
}
function emHistSheet(id) {
  const h = EM_HIST && EM_HIST.queue.find((x) => x.id === id);
  if (!h) return;
  const st = EM_STATE[h.state] || ["", h.state];
  const reply = h.code != null || h.error ? `<code dir="ltr" class="mono">${esc([h.code, h.error].filter((x) => x != null && x !== "").join(" "))}</code>` : '<span class="muted">עוד אין תשובה</span>';
  const next = h.state === "sent" ? ["נשלח", emWhen(h.sent_at)] : h.state === "pending" && h.next_at ? ["הניסיון הבא", emWhen(h.next_at)] : null;
  const rows = [["מצב", UI.status(st[0], st[1])], ["נמענים", h.recipients.length ? h.recipients.map(emMail).join(", ") : '<span class="muted">אין נמענים בשפה הזו</span>'],
    ["תשובת שרת הדואר", reply], ["ניסיונות", `${esc(h.attempts)} מתוך ${esc(h.max_attempts)}`], ["נוצר", esc(emWhen(h.created_at))]];
  if (next) rows.push([next[0], esc(next[1])]);
  const items = h.items.length ? `<h3 class="n-sec-h">האירוע</h3><ul class="em-items">${h.items.map((x) => `<li>${esc(typeof x === "string" ? x : x.detail || JSON.stringify(x))}</li>`).join("")}</ul>${h.count > h.items.length ? `<p class="n-help">ועוד ${h.count - h.items.length} שורות.</p>` : ""}` : "";
  openModalContent(h.label, `<dl class="mkv em-kv">${rows.map(([dt, dd]) => `<dt>${esc(dt)}</dt><dd>${dd}</dd>`).join("")}</dl>${items}`,
    "סגירה", "closeModal()", false, `${emWhen(h.created_at)} · כלל: ${emRuleLabel(h.rule_id, h.rule_name)}`, "mid");
}
