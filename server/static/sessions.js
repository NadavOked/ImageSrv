/* ‏#2552 — לשונית "סשנים" ב"משתמשים והרשאות", לפי המוקאפ המאושר (נדב 06/11, עם ההמלצות שבו):
   docs/design/mockups-1107/2552-sessions.html בענף docs/mockups-1107.

   ה-API (‏interfaces.md, "מי מחובר לקונסולה"): ‏GET /sessions (רשימה), ‏DELETE /sessions/{sid} (ניתוק סשן בודד),
   ‏POST /sessions/revoke {username} ("צא מכל המכשירים" — למשתמש אחר או לעצמך). הכול למנהל בלבד.

   אבטחה: אין טוקן ואין גיבוב מלא בדפדפן. ‏sid הוא ששת התווים הראשונים של ה-sha256, והוא כל מה שהשרת מוסר.
   הסשן הנוכחי אינו ניתן לניתוק בשורה (לשם כך יש "יציאה"). אין הקלדת מילה לאישור (#1270): הכפתור האדום בחלון הוא האישור.

   מה שונה מהמוקאפ, ולמה: ‏(1) בשורה של הסשן הנוכחי אין ⋮ — הוא ריק בכוונה, כי הפעולה היחידה בו אסורה;
   ‏(2) שבב "מוניטור פתוח" בלי שם המכונה — השרת מכיר רק כמה גשרים פתוחים על העוגייה, לא לאיזו מכונה (המוקאפ עצמו
   מתיר את זה: "אם אין קישור, השבב נופל"); ‏(3) הטקסטים בלשון ניטרלית, כי השרת אינו יודע את מין המשתמש. */

let SESS = null, sessErr = "", SESS_Q = "";

/* לשונית "סשנים": למנהל בלבד, ורק בשרת הראשי — ביטול סשן הוא עניין של השרת שהעוגייה שלו. */
function sessionsAllowed() { return isAdmin() && !SERVER_CTX; }
function sessionsTabOpen() { return current === "permissions" && sessionsAllowed() && currentTab === 1; }

async function loadSessions() {
  try { SESS = await api("/sessions"); sessErr = ""; }
  catch (e) { SESS = null; sessErr = e.message; }   // "לא נקרא" אינו "אין מחוברים" (עיקרון 5)
  if (sessionsTabOpen()) renderCurrent();
}

const SESS_ROLE = { admin: "מנהל", deploy: "הפצה" };
function sessWhen(iso) {
  const ms = tsMs(iso);
  if (!Number.isFinite(ms)) return "—";
  if (isToday(iso)) return `היום ${fmtClock(iso)}`;
  if (zoneDay(ms) === zoneDay(Date.now() - 86400000)) return `אתמול ${fmtClock(iso)}`;
  return `${fmtDate(iso).slice(0, 5)} ${fmtClock(iso)}`;
}
const sessMine = (user) => !!ME && String(user).toLowerCase() === String(ME.username).toLowerCase();
const sessOf = (user) => (SESS || []).filter((s) => String(s.user).toLowerCase() === String(user).toLowerCase());
const sessCount = (n) => n === 1 ? "1 סשן" : `${n} סשנים`;

function sessRow(s) {
  const chips = s.monitor ? UI.pill("warn", s.monitor > 1 ? `מוניטור פתוח (${s.monitor})` : "מוניטור פתוח") : "";
  return { attrs: `data-sid="${esc(s.sid)}"${s.current ? ` class="sess-me"` : ""}`, cells: [
    `<b>${esc(s.user)}</b>${s.current ? " " + UI.pill("info", "הסשן הזה") : ""}`,
    `<bdi dir="ltr" class="mono">${esc(s.ip || "—")}</bdi>`,
    esc(sessWhen(s.since)),
    esc(ago(s.last_seen)),
    `<bdi dir="ltr" class="mono sess-hash">${esc(s.sid)}</bdi>`,
    chips,
    s.current ? "" : UI.rowMenu(`${s.user} ${s.sid}`, [["ניתוק הסשן…", `sessionRevokeSheet('${esc(s.sid)}')`, "danger"]])] };
}

function sessRows(list) {
  const rows = [], seen = new Set();
  for (const first of list) {
    const key = String(first.user).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const mine = list.filter((s) => String(s.user).toLowerCase() === key);
    rows.push({ html: `<tr class="group"><td colspan="6">${esc(first.user)}<span class="cnt">${esc(SESS_ROLE[first.role] || first.role)} · ${esc(sessCount(mine.length))}</span></td>`
      + `<td><button class="btn sm danger" type="button" onclick="sessionsAllSheet('${encodeId(first.user)}')">צא מכל המכשירים</button></td></tr>` });
    mine.forEach((s) => rows.push(sessRow(s)));
  }
  return rows;
}

function sessTableHtml() {
  const q = SESS_Q.trim().toLowerCase();
  const list = q ? SESS.filter((s) => String(s.user).toLowerCase().includes(q)) : SESS;
  return UI.datagrid({ cls: "kebab-col",
    columns: ["משתמש", "כתובת", "מאז", "פעילות אחרונה", "מזהה סשן", "", { html: `<span class="sr">פעולות</span>` }],
    rows: sessRows(list), empty: q ? "אין סשן שמתאים לסינון" : "אין סשנים פעילים" });
}

function sessionsFilter(value) {
  SESS_Q = String(value || "");
  const box = document.getElementById("sess-table");
  if (box && SESS) box.innerHTML = sessTableHtml();
}

function sessionsTab() {
  if (SESS === null && !sessErr) return pagePlaceholder();
  if (SESS === null) return UI.note("err", `לא הצלחתי לקרוא את הסשנים: ${esc(sessErr)}`);
  const bar = `<div class="dg-bar"><input type="search" id="sess-q" placeholder="סינון לפי משתמש" aria-label="סינון לפי משתמש" value="${esc(SESS_Q)}" oninput="sessionsFilter(this.value)" style="width:240px">`
    + `<span class="sp"></span><button class="btn danger" type="button" onclick="sessionsAllSheet('${encodeId(ME.username)}')">צא מכל המכשירים שלי…</button></div>`;
  const card = UI.card({ title: "סשנים פעילים", small: String(SESS.length), flush: true,
    body: `${bar}<div id="sess-table">${sessTableHtml()}</div>` });
  return `${card}<p class="cap c12">הרשימה כוללת רק סשנים שהשרת עדיין מקבל. סשן שפג או נותק לא מוצג. מזהה הסשן הוא קיצור של הגיבוב, לא העוגייה.</p>`;
}

/* ניתוק סשן בודד: החלון אומר מי, מאיפה ומה קורה. הכפתור האדום הוא האישור. */
function sessionRevokeSheet(sid) {
  const s = (SESS || []).find((x) => x.sid === sid);
  if (!s || s.current || !isAdmin()) return;
  sheet({ title: `לנתק את הסשן של ${s.user}?`, danger: true, submitLabel: "נתק את הסשן",
    note: `<p>הסשן ייסגר מיד. בבקשה הבאה מהדפדפן הזה יופיע מסך הכניסה.</p>`
      + UI.kv([["משתמש", `${esc(s.user)} · ${esc(SESS_ROLE[s.role] || s.role)}`], ["כתובת", `<bdi dir="ltr">${esc(s.ip || "—")}</bdi>`],
        ["מזהה סשן", `<bdi dir="ltr">${esc(s.sid)}</bdi>`], ["פעילות", esc(ago(s.last_seen))]])
      + (s.monitor ? UI.note("warn", "<b>גשר מוניטור פתוח.</b> הצפייה תיסגר יחד עם הסשן.") : ""),
    onSubmit: async () => {
      await del(`/sessions/${encodeId(s.sid)}`);
      toast("הסשן נותק — נרשם ביומן");
      await loadSessions();
    } });
}

/* "צא מכל המכשירים": הגדלת ה-epoch של המשתמש (POST /sessions/revoke). לעצמך — גם הסשן הנוכחי נסגר. */
function sessionsAllSheet(name) {
  try { name = decodeURIComponent(name); } catch (e) {}
  if (!isAdmin()) return;
  const mine = sessMine(name), n = sessOf(name).length;
  const text = mine
    ? `${n === 2 ? "שני הסשנים שלך ייסגרו" : n === 1 ? "הסשן שלך ייסגר" : `${n} הסשנים שלך ייסגרו`}, כולל זה שאתה מחובר בו עכשיו. תועבר למסך הכניסה.`
    : `${n === 1 ? "הסשן של" : `כל ${n} הסשנים של`} <b>${esc(name)}</b> ${n === 1 ? "ייסגר" : "ייסגרו"} מיד, והמשתמש יצטרך להיכנס מחדש. הסיסמה לא משתנה.`;
  sheet({ title: mine ? "לצאת מכל המכשירים שלך?" : `להוציא את ${name} מכל המכשירים?`, danger: true, submitLabel: "צא מכל המכשירים",
    note: `<p>${text}</p>`,
    onSubmit: async () => {
      await post("/sessions/revoke", { username: name });
      if (mine) { ME = null; showLogin(); return; }
      toast("המשתמש נותק מכל המכשירים — נרשם ביומן");
      await loadSessions();
    } });
}
