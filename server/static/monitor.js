/* ImageSrv — לקוח RFB מרוחק (#690), vanilla JS, בלי ספרייה חיצונית.
   מדבר RFB 3.8 מול imagesrv-monitor (LibVNCServer, בלי סיסמה) דרך
   ה-WebSocket האדמיני /api/console/monitor/<mac>, ומצייר ל-canvas.

   הקונסולה כולה vanilla JS בלי CDN (ראו CLAUDE.md), ולכן זהו לקוח
   קומפקטי משלנו ולא חבילת noVNC המלאה: קידודי Raw + CopyRect בלבד —
   בדיוק מה ש-LibVNCServer מגיש כברירת מחדל כשהלקוח מציע רק אותם.
   החוזה מול השרת — בייטי RFB גולמיים על מסגרות WebSocket בינאריות —
   זהה למה ש-noVNC היה מצפה לו, ולכן החלפה לספרייה מלאה בעתיד אינה
   נוגעת בשרת. איננו דורשים subprotocol (ראו connect() למה).

   view/control ואריזת ה-initramfs נבדקים על ברזל (אימות של נדב). */
"use strict";

const params = new URLSearchParams(location.search);
const MAC = params.get("mac") || "";
/* ‏#1129: השם מהכתובת הוא כותרת בלבד. האישור לפעולת כוח נבדק **בשרת** מול
   השם הקנוני שבטבלה (`POST …/power`), לא כאן ולא מול `?name=`. */
const NAME = params.get("name") || MAC;

/* ‏#1129 (R48): תקרות לפרסור RFB — תחנה עוינת (או מוניטור שהשתבש) לא
   מפילה את הטאב. חריגה = שגיאה בשם, הלולאה נעצרת וה-WS נסגר. */
const MAX_RECT_BYTES = 32 * 1024 * 1024;        // w*h*4 של מלבן/מסך
const MAX_CUT_TEXT = 64 * 1024;                 // ServerCutText, ותיאור/שם
const MAX_STREAM_BYTES = 64 * 1024 * 1024;      // חוצץ הקלט של ByteStream
/* ‏#655 v1: ``node`` = מזהה שרת משנה — המוניטור עובר דרך הראשי אל שרת המשנה
   ומשם למכונה. אותו זרם RFB, אותה לחיצת-יד מול הדפדפן; רק הנתיב שונה. */
const NODE = params.get("node") || "";

const canvas = document.getElementById("screen");
const gctx = canvas.getContext("2d", { alpha: false });
const statusEl = document.getElementById("status");
const titleEl = document.getElementById("title");
const disconnectOverlay = document.getElementById("disconnect-overlay");
titleEl.textContent = NAME ? `מוניטור — ${NAME}` : "מוניטור";
document.title = titleEl.textContent;

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.title = text;              /* ‏#1837: הפס קוטע ב-…, המלא במעבר עכבר */
  statusEl.className = "status " + (cls || "");
}

function showMachineDisconnected(text) {
  canvas.classList.add("disconnected");
  disconnectOverlay.textContent = text;
  disconnectOverlay.classList.add("visible");
}

function clearMachineDisconnected() {
  canvas.classList.remove("disconnected");
  disconnectOverlay.textContent = "";
  disconnectOverlay.classList.remove("visible");
}

/* --- קורא בייטים סדרתי מעל ה-WebSocket -----------------------------------
   הפרוטוקול נכתב כקוד רציף עם await; onmessage מצרף לחוצץ ומעיר קריאה
   שממתינה. מסגרות WebSocket מתפצלות, ולכן חייבים צבירה ולא message=הודעה. */
class ByteStream {
  constructor() {
    this.chunks = [];
    this.length = 0;
    this._want = 0;
    this._resolve = null;
    this._reject = null;
    this.closed = false;
    this.error = null;
  }
  push(buf) {
    if (this.closed) return;
    if (this.length + buf.byteLength > MAX_STREAM_BYTES) {
      this.fail(new Error("חוצץ הקלט עבר " + (MAX_STREAM_BYTES >> 20) + "MB — המוניטור שולח מהר ממה שהדפדפן מצייר"));
      return;
    }
    this.chunks.push(new Uint8Array(buf));
    this.length += buf.byteLength;
    if (this._resolve && this.length >= this._want) {
      const r = this._resolve; this._resolve = null; this._reject = null; r(this._take(this._want));
    }
  }
  /* סגירה. עם error — הקורא הממתין (וכל קריאה הבאה) נכשל בשם; בלעדיו — null. */
  fail(error) {
    this.closed = true;
    if (error) this.error = error;
    const resolve = this._resolve, reject = this._reject;
    this._resolve = null; this._reject = null;
    if (error && reject) reject(error);
    else if (resolve) resolve(null);
  }
  _take(n) {
    const out = new Uint8Array(n);
    let off = 0;
    while (off < n) {
      const head = this.chunks[0];
      const need = n - off;
      if (head.length <= need) {
        out.set(head, off); off += head.length;
        this.chunks.shift();
      } else {
        out.set(head.subarray(0, need), off);
        this.chunks[0] = head.subarray(need); off += need;
      }
    }
    this.length -= n;
    return out;
  }
  read(n) {
    if (this.error) return Promise.reject(this.error);
    if (this.closed) return Promise.resolve(null);
    if (this.length >= n) return Promise.resolve(this._take(n));
    this._want = n;
    return new Promise((resolve, reject) => { this._resolve = resolve; this._reject = reject; });
  }
}

const be16 = (a, i) => (a[i] << 8) | a[i + 1];
const be32 = (a, i) => (a[i] * 0x1000000) + (a[i + 1] << 16) + (a[i + 2] << 8) + a[i + 3];

let ws = null;
let stream = null;
let fbWidth = 0;
let fbHeight = 0;
let viewOnly = false;
/* 18/10 (rc6, "סוג אבטחה לא צפוי"): קלט יוצא רק אחרי ServerInit. ה-WS פתוח עוד לפני
   שהשרת הגיע למכונה (‏#1686: עד 15 ש' כשהיא עולה), והפרוקסי קורא את 12 הבייטים הראשונים
   מהדפדפן כגרסת RFB — PointerEvent שנשלח בזמן "מתחבר…" הפך לגרסה ולבחירת אבטחה שגויה. */
let rfbReady = false;

function wsUrl() {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  if (NODE) {
    return `${scheme}//${location.host}/api/console/storage-nodes/${encodeURIComponent(NODE)}/monitor/${encodeURIComponent(MAC)}`;
  }
  return `${scheme}//${location.host}/api/console/monitor/${encodeURIComponent(MAC)}`;
}

function send(bytes) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(new Uint8Array(bytes));
}

/* --- לחיצת יד RFB 3.8 (None auth) ---------------------------------------- */
async function handshake() {
  const version = await stream.read(12);
  if (!version) throw new Error("החיבור נסגר לפני לחיצת היד");
  send(new TextEncoder().encode("RFB 003.008\n"));

  const nTypes = (await stream.read(1))[0];
  if (nTypes === 0) {
    const len = be32(await stream.read(4), 0);
    if (len > MAX_CUT_TEXT) throw new Error("השרת דחה (סיבה ארוכה מדי: " + len + " בייט)");
    const reason = new TextDecoder().decode(await stream.read(len));
    throw new Error("השרת דחה: " + reason);
  }
  const types = await stream.read(nTypes);
  if (!Array.from(types).includes(1))
    throw new Error("השרת דורש אימות שאינו נתמך");
  send([1]);                                   // None
  const result = be32(await stream.read(4), 0);
  if (result !== 0) throw new Error("אימות ה-RFB נכשל");

  send([1]);                                   // ClientInit: shared
  const init = await stream.read(24);
  const w = be16(init, 0), h = be16(init, 2);
  const nameLen = be32(init, 20);
  if (nameLen > MAX_CUT_TEXT) throw new Error("שם המסך ארוך מדי: " + nameLen + " בייט");
  if (nameLen) await stream.read(nameLen);
  resizeTo(w, h);
}

/* מלבן בתוך המסך ובגודל סביר — אחרת שגיאה בשם (לא הקצאה של GB). */
function checkRect(x, y, w, h, what) {
  if (w * h * 4 > MAX_RECT_BYTES) throw new Error(`${what} גדול מדי: ${w}×${h}`);
  if (x + w > fbWidth || y + h > fbHeight) throw new Error(`${what} מחוץ למסך: ${x},${y} ${w}×${h} במסך ${fbWidth}×${fbHeight}`);
}

function resizeTo(w, h) {
  if (w * h * 4 > MAX_RECT_BYTES) throw new Error(`גודל מסך לא סביר: ${w}×${h}`);
  fbWidth = w; fbHeight = h;
  canvas.width = w; canvas.height = h;
  gctx.fillStyle = "#000"; gctx.fillRect(0, 0, w, h);
}

/* פורמט פיקסל שאנחנו קובעים: 32bpp, little-endian, true-colour, B,G,R,X. */
function setPixelFormat() {
  send([0, 0, 0, 0,
        32, 24, 0, 1,
        0, 255, 0, 255, 0, 255,
        16, 8, 0, 0, 0, 0]);
}

function setEncodings() {
  const encs = [1, 0, -223];                   // CopyRect, Raw, DesktopSize
  const msg = [2, 0, (encs.length >> 8) & 255, encs.length & 255];
  for (const e of encs) msg.push((e >>> 24) & 255, (e >>> 16) & 255, (e >>> 8) & 255, e & 255);
  send(msg);
}

function requestUpdate(incremental) {
  send([3, incremental ? 1 : 0,
        (0 >> 8) & 255, 0 & 255, 0, 0,
        (fbWidth >> 8) & 255, fbWidth & 255,
        (fbHeight >> 8) & 255, fbHeight & 255]);
}

/* --- לולאת ההודעות מהשרת -------------------------------------------------- */
async function pump() {
  await handshake();
  rfbReady = true;
  retries = 0;
  clearMachineDisconnected();
  setPixelFormat();
  setEncodings();
  setStatus("מחובר", "ok");
  setControl("");
  watchControl();                              // #1852: נוכחות + בקשות שליטה
  requestUpdate(false);

  while (!stream.closed) {
    const head = await stream.read(1);
    if (!head) break;
    if (head[0] === 0) await framebufferUpdate();
    else if (head[0] === 2) { /* Bell */ }
    else if (head[0] === 3) await serverCutText();
    else break;                                // הודעה לא מוכרת — נסגר בגלוי
  }
}

async function framebufferUpdate() {
  const h = await stream.read(3);              // padding(1) + n-rects(2)
  const rects = be16(h, 1);
  for (let i = 0; i < rects; i++) {
    const r = await stream.read(12);
    const x = be16(r, 0), y = be16(r, 2), w = be16(r, 4), hgt = be16(r, 6);
    const enc = be32(r, 8) | 0;
    if (enc === 0) await rawRect(x, y, w, hgt);
    else if (enc === 1) await copyRect(x, y, w, hgt);
    else if (enc === -223) resizeTo(w, hgt);
    else throw new Error("קידוד לא נתמך: " + enc);
  }
  requestUpdate(true);
}

async function rawRect(x, y, w, h) {
  checkRect(x, y, w, h, "מלבן Raw");
  const data = await stream.read(w * h * 4);   // B,G,R,X
  const img = gctx.createImageData(w, h);
  const px = img.data;
  for (let i = 0, j = 0; i < data.length; i += 4, j += 4) {
    px[j] = data[i + 2]; px[j + 1] = data[i + 1]; px[j + 2] = data[i]; px[j + 3] = 255;
  }
  gctx.putImageData(img, x, y);
}

async function copyRect(x, y, w, h) {
  checkRect(x, y, w, h, "מלבן CopyRect");
  const s = await stream.read(4);
  const sx = be16(s, 0), sy = be16(s, 2);
  checkRect(sx, sy, w, h, "מקור CopyRect");
  gctx.drawImage(canvas, sx, sy, w, h, x, y, w, h);
}

async function serverCutText() {
  const h = await stream.read(7);              // padding(3) + length(4)
  const len = be32(h, 3);
  if (len > MAX_CUT_TEXT) throw new Error("ServerCutText ארוך מדי: " + len + " בייט");
  if (len) await stream.read(len);
}

/* --- קלט: עכבר ומקלדת (רק כשלא view-only) --------------------------------- */
/* ‏#1475: הפיקסל שמתחת לסמן — floor ולא round (round הסיט חצי פיקסל-fb),
   ומיקום מחוץ ל-canvas (סרגל הכלים, הפס השחור, גרירה החוצה) נצמד לקצה. */
function fbCoords(event) {
  const rect = canvas.getBoundingClientRect();
  const x = Math.floor((event.clientX - rect.left) * fbWidth / rect.width);
  const y = Math.floor((event.clientY - rect.top) * fbHeight / rect.height);
  return [Math.max(0, Math.min(fbWidth - 1, x)), Math.max(0, Math.min(fbHeight - 1, y))];
}

/* ‏#1475: תזוזה נשלחת לכל היותר פעם ב-POINTER_MS, והאחרונה תמיד יוצאת
   (trailing). כל PointerEvent גורם לגואי במכונה לצייר פריים מלא, ודפדפן
   שולח 60–144 תזוזות בשנייה — יותר ממה שהמוניטור מחזיר (25fps בזמן קלט),
   ועל חשבון ה-CPU של השיכפול. לחיצה ושחרור אינם מושהים לעולם. */
const POINTER_MS = 33;
let buttonMask = 0;
let pendingMove = null;
let moveTimer = null;
let lastPointerAt = -Infinity;

function sendInput(bytes) {
  if (rfbReady) send(bytes);                   // לפני ServerInit — נזרק (ראו rfbReady)
}

function sendPointer(x, y) {
  lastPointerAt = Date.now();
  sendInput([5, buttonMask, (x >> 8) & 255, x & 255, (y >> 8) & 255, y & 255]);
}

function flushMove() {
  moveTimer = null;
  if (!pendingMove) return;
  const [x, y] = pendingMove;
  pendingMove = null;
  sendPointer(x, y);
}

function pointer(event) {
  if (viewOnly) return;
  pendingMove = fbCoords(event);
  if (moveTimer) return;
  const wait = lastPointerAt + POINTER_MS - Date.now();
  if (wait <= 0) flushMove();
  else moveTimer = setTimeout(flushMove, wait);
}

function pointerButton(event) {
  if (viewOnly) return;
  if (moveTimer) { clearTimeout(moveTimer); moveTimer = null; }
  pendingMove = null;
  const [x, y] = fbCoords(event);
  sendPointer(x, y);
}

function keyEvent(keysym, down) {
  if (viewOnly || !keysym) return;
  sendInput([4, down ? 1 : 0, 0, 0,
        (keysym >>> 24) & 255, (keysym >>> 16) & 255, (keysym >>> 8) & 255, keysym & 255]);
}

/* מיפוי אירוע מקלדת ל-keysym של X11 (מה ש-monitor.c מכיר ב-keycode_for). */
const SPECIAL = {
  Enter: 0xff0d, Backspace: 0xff08, Tab: 0xff09, Escape: 0xff1b, " ": 0x20,
  ArrowUp: 0xff52, ArrowDown: 0xff54, ArrowLeft: 0xff51, ArrowRight: 0xff53,
  Home: 0xff50, End: 0xff57, PageUp: 0xff55, PageDown: 0xff56,
  Insert: 0xff63, Delete: 0xffff,
  Shift: 0xffe1, Control: 0xffe3, Alt: 0xffe9, Meta: 0xffeb,
  F1: 0xffbe, F2: 0xffbf, F3: 0xffc0, F4: 0xffc1, F5: 0xffc2, F6: 0xffc3,
  F7: 0xffc4, F8: 0xffc5, F9: 0xffc6, F10: 0xffc7, F11: 0xffc8, F12: 0xffc9,
};
/* RFB carries X11 keysyms, while KeyboardEvent.code identifies the physical
   key independently of the operator's active layout.  Send the unshifted US
   keysym here: the browser sends Shift as its own down/up event, so the remote
   kernel applies it exactly as it would to a local keyboard. */
const PHYSICAL = {
  Space: " ", Minus: "-", Equal: "=", BracketLeft: "[", BracketRight: "]",
  Semicolon: ";", Quote: "'", Backquote: "`", Backslash: "\\",
  Comma: ",", Period: ".", Slash: "/",
};
for (let i = 0; i < 26; i++) PHYSICAL[`Key${String.fromCharCode(65 + i)}`] = String.fromCharCode(97 + i);
for (let i = 0; i < 10; i++) PHYSICAL[`Digit${i}`] = String(i);
function keysymFor(event) {
  if (PHYSICAL[event.code]) return PHYSICAL[event.code].charCodeAt(0);
  if (event.key.length === 1) return event.key.charCodeAt(0);  // Latin-1
  return SPECIAL[event.key] || 0;
}

/* --- כוח: הפעלה מחדש / כיבוי של המחשב המנוטר (#781, #1129) ----------------
   ‏#1129: הפקודה אינה נשלחת מכאן על ה-RFB. הדפדפן קורא ל-`POST …/power`
   עם השם הידוע של המחשב; השרת משווה אותו לשם הקנוני שבטבלה (לא ל-`?name=`),
   כותב את אסימון הכוח (ClientCutText, `monitor.power_frame`) אל המכונה דרך הגשר
   הפתוח, ורושם ביומן מי כיבה מה. ‏ClientCutText מהדפדפן נדחה בשרת (4403).
   ‏monitor.c (root) מזהה את האסימון וקורא ל-`reboot -f`/`poweroff -f`.
   אין תלות ב-view-only: כוח אינו קלט מסך. */
function powerUrl() {
  const mac = encodeURIComponent(MAC);
  return NODE
    ? `/api/console/storage-nodes/${encodeURIComponent(NODE)}/monitor/${mac}/power`
    : `/api/console/monitor/${mac}/power`;
}

async function sendPower(action, confirm) {
  let response;
  try {
    response = await fetch(powerUrl(), {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, confirm }),
    });
  } catch (e) {
    /* ‏#1850: "Failed to fetch" של הדפדפן, באנגלית, אינו אומר למפעיל דבר. */
    throw new Error("אין חיבור לשרת — הפקודה לא נשלחה");
  }
  if (response.ok) return;
  let detail = "שגיאה " + response.status;
  try { const body = await response.json(); detail = body.detail || detail; } catch (e) {}
  throw new Error(detail);
}

/* --- חיבור וניתוק -------------------------------------------------------- */
/* 18/10 (rc6, "הריענון"): סגירה אינה משאירה מסך שחור תקוע. הסיבה נכתבת על המסך עצמו
   (המסגרת האחרונה מוחשכת — היא אינה חיה), עם ספירה לאחור, והחיבור מנסה שוב לבד: מכונה
   שאותחלה, מוניטור שעוד עולה (4502), ניתוק (4513), ‏4409 של iframe קודם שעוד משתחרר.
   ההשהיה גדלה ונעצרת בתקרה — ואז "התחבר מחדש" ידני. מה שלא ישתנה עם הזמן — התחברות,
   הרשאה, מכונה לא מוכרת, וסגירת חוסר-הפעילות המכוונת (#1129) — אינו חוזר לבד. */
const RETRY_DELAYS_S = [2, 4, 8, 15, 30];      // האחרון חוזר עד התקרה
const RETRY_MAX = 20;                          // ~9 דק' של ניסיונות, ואז ידני
/* ‏#1852: ‏4410 — הצפייה עברה לחלון אחר של אותו מנהל; ‏4411 — השליטה הועברה למנהל
   אחר. ניסיון חוזר היה מחזיר את המכונה הלוך ושוב. */
const NO_RETRY = new Set([4401, 4403, 4404, 4408, 4410, 4411]);
let retries = 0;
let retryTimer = null;

function cancelRetry() {
  if (retryTimer) clearTimeout(retryTimer);
  retryTimer = null;
}

function showClosed(text) {
  setStatus(text, "bad");
  showMachineDisconnected(text);
}

/* ‏#1850: ‏200 מ-…/power אומר "נכתב ל-socket", לא "בוצע" (עיקרון 5). הדף אומר
   שהפקודה נשלחה ומחכה לניתוק; ניתוק = הראיה. אחרי כיבוי — אין למה להתחבר;
   אחרי הפעלה מחדש — מתחברים מחדש לבד כשהמחשב עולה. בלי ניתוק בזמן — בגלוי. */
const POWER_WAIT_S = 90;
let powerPending = null;                       // {action, label, timer}
function awaitPower(action, label) {
  if (powerPending) clearTimeout(powerPending.timer);
  setStatus(`נשלחה פקודת ${label} — ממתין שהמחשב ${action === "reboot"
    ? "יתנתק ויעלה מחדש" : "ייכבה"}`, "");
  const pending = { action, label, timer: 0 };
  pending.timer = setTimeout(() => {
    if (powerPending !== pending) return;
    powerPending = null;
    setStatus(`פקודת ${label} נשלחה, אבל המחשב לא התנתק תוך ${POWER_WAIT_S} שניות — ייתכן שלא בוצעה`, "bad");
  }, POWER_WAIT_S * 1000);
  powerPending = pending;
}
/* --- ‏#1852: מכונה אחת, צופה אחד — ובקשת שליטה (הכרעת נדב 19/10) -------------
   מנהל שני שמקבל 4409 רואה מי מחזיק וכפתור "בקש שליטה" — לא ניסיון חוזר שחוטף.
   המחזיק שואל כל 5 ש' (זו גם ה"נוכחות" שלו בשרת) ורואה בקשה עם אשר/סרב; 60 ש'
   בלי תשובה = סירוב, ומחזיק שלא נראה 2 דק' — השרת מאשר לבד. רק במוניטור המקומי. */
const CONTROL_POLL_MS = 5000;
let controlTimer = null;
function controlUrl(tail) {
  return `/api/console/monitor/${encodeURIComponent(MAC)}/control${tail || ""}`;
}
async function controlCall(tail, body) {
  let response;
  try {
    response = await fetch(controlUrl(tail), body === undefined
      ? { credentials: "same-origin" }
      : { method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (e) {
    throw new Error("אין חיבור לשרת");
  }
  let data = {};
  try { data = await response.json(); } catch (e) {}
  if (!response.ok) throw new Error(data.detail || "שגיאה " + response.status);
  return data;
}
function setControl(text, buttons) {
  const box = document.getElementById("control");
  box.textContent = "";
  if (text) {
    const span = document.createElement("span");
    span.textContent = text;
    box.appendChild(span);
  }
  for (const [label, onclick, cls] of buttons || []) {
    const b = document.createElement("button");
    b.className = "btn sm" + (cls ? " " + cls : "");
    b.textContent = label;
    b.onclick = onclick;
    box.appendChild(b);
  }
}
function stopControl() { clearTimeout(controlTimer); controlTimer = null; }
/* המחזיק: שואל, מציג בקשה פתוחה. */
function watchControl() {
  stopControl();
  if (NODE) return;
  const tick = async () => {
    controlTimer = null;
    if (!rfbReady) return;
    try {
      const state = await controlCall("");
      if (state.you_hold && state.request) {
        const by = state.request.by;
        setControl(`${by} מבקש שליטה במכונה (${state.request.seconds_left} ש')`, [
          ["אשר", () => controlCall("/answer", { approve: true }).catch((e) => setControl(e.message)), "danger"],
          ["סרב", () => controlCall("/answer", { approve: false }).then(() => setControl("")).catch((e) => setControl(e.message))],
        ]);
      } else setControl("");
    } catch (e) { /* הבדיקה הבאה תנסה שוב */ }
    if (rfbReady) controlTimer = setTimeout(tick, CONTROL_POLL_MS);
  };
  controlTimer = setTimeout(tick, 0);
}
/* המבקש: 4409 — מי מחזיק? */
async function heldBySomeoneElse(reason) {
  if (NODE) return false;
  let state;
  try { state = await controlCall(""); } catch (e) { return false; }
  if (state.granted_to_you || !state.holder || state.you_hold) return false;
  showClosed(reason);
  offerRequest(state.holder);
  return true;
}
function offerRequest(holder, text) {
  setControl(text || "", [["בקש שליטה", () => requestControl(holder), ""]]);
}
async function requestControl(holder) {
  let asked;
  try { asked = await controlCall("/request", {}); }
  catch (e) { setControl(e.message); return; }
  if (asked.outcome !== "pending") { setControl(""); retries = 0; connect(); return; }
  const wait = async () => {
    controlTimer = null;
    let state;
    try { state = await controlCall(""); } catch (e) { controlTimer = setTimeout(wait, 2000); return; }
    if (state.granted_to_you || !state.holder) { setControl(""); retries = 0; connect(); return; }
    if (state.request) {
      setControl(`הבקשה נשלחה ל-${holder} — ממתין לאישור (${state.request.seconds_left} ש')`);
      controlTimer = setTimeout(wait, 2000);
      return;
    }
    const last = state.last || {};
    offerRequest(holder, last.outcome === "denied" ? `${holder} סירב לבקשה` : `${holder} לא ענה תוך 60 שניות`);
  };
  setControl(`הבקשה נשלחה ל-${holder} — ממתין לאישור`);
  controlTimer = setTimeout(wait, 2000);
}

/* ניתוק אחרי פקודת כוח: true = טופל כאן. */
function powerClosed() {
  if (!powerPending) return false;
  const done = powerPending;
  clearTimeout(done.timer);
  powerPending = null;
  if (done.action === "poweroff") { showClosed("המחשב כבה (פקודת כיבוי)"); return true; }
  retries = 0;
  scheduleRetry("המחשב מאתחל (פקודת הפעלה מחדש) — מתחבר מחדש כשיעלה");
  return true;
}

function scheduleRetry(reason) {
  if (retries >= RETRY_MAX) {
    showClosed(`${reason} — ${RETRY_MAX} ניסיונות נכשלו; לחצו "התחבר מחדש"`);
    return;
  }
  let left = RETRY_DELAYS_S[Math.min(retries, RETRY_DELAYS_S.length - 1)];
  retries++;
  const tick = () => {
    retryTimer = null;
    if (left <= 0) { connect(); return; }
    showClosed(`${reason} — ניסיון חוזר בעוד ${left} שנ'`);
    left--;
    retryTimer = setTimeout(tick, 1000);
  };
  tick();
}

function connect() {
  cancelRetry();
  rfbReady = false;
  /* החיבור הקודם נסגר, וה-handlers שלו קשורים לזרם ול-socket **שלו** (‏`bytes`/`sock`):
     עד כאן הם קראו ל-`stream`/`ws` הגלובליים, ולכן ה-onclose המאוחר של הישן סגר את
     הזרם של החיבור החדש, והבייטים שלו נכנסו אליו. הקורא שממתין על הזרם הישן משתחרר
     עם null ויוצא. */
  if (ws) ws.close();
  if (stream) stream.fail(null);
  setStatus("מתחבר…", "");
  const bytes = stream = new ByteStream();
  /* בלי לבקש subprotocol: ‏`new WebSocket(url, "binary")` היה מחייב את
     השרת להחזיר "binary" ב-101, אחרת Chrome סוגר מיד (1006 → "החיבור
     נסגר", מסך ריק). ‏uvicorn עם מימוש ה-WS ‎wsproto **אינו** מהדהד את
     ה-subprotocol (‎websockets כן) — נמדד על הברזל. ‏RFB עובד על מסגרות
     בינאריות גולמיות בלי שום subprotocol, אז פשוט לא דורשים אותו וזה
     חסין לשני המימושים. */
  const sock = ws = new WebSocket(wsUrl());
  let ended = false, failure = "";
  sock.binaryType = "arraybuffer";
  sock.onmessage = (event) => bytes.push(event.data);
  sock.onopen = () => pump().catch((error) => {
    /* סגירה מהשרת כבר טופלה ב-onclose (והיא מה שהפיל את הקריאה); כאן רק שגיאה של
       הלקוח עצמו — היא נסגרת, וה-onclose מציג אותה ומנסה שוב. */
    if (ended || ws !== sock) return;
    failure = "שגיאה: " + error.message;
    setStatus(failure, "bad");
    sock.close();
  });
  sock.onclose = (event) => {
    ended = true;
    bytes.fail(event.reason ? new Error(event.reason) : null);
    if (ws !== sock) return;                   // חיבור שהוחלף — אין לו מה לומר לדף
    rfbReady = false;
    /* ‏#904: השרת מקבל ואז סוגר, ולכן `event.reason` העברית שלו מגיעה
       (לפני כן הסגירה קדמה ל-accept → HTTP 403 → 1006 בלי קוד ובלי
       סיבה). הטבלה היא גיבוי לסיבה ריקה בלבד. */
    const reasons = { 4401: "נדרשת התחברות", 4403: "פעולה למנהל בלבד",
      4404: "מכונה לא מוכרת", 4408: "המוניטור נסגר — 10 דק' בלי תעבורה",
      4409: "מוניטור כבר פתוח למכונה זו",
      4502: "שירות המוניטור במכונה אינו זמין",
      4512: "הפרוקסי לא הזדהה מול המוניטור במכונה",
      4513: "החיבור למחשב נותק — ייתכן שהמחשב כבה" };
    const reason = failure || event.reason || reasons[event.code] || "החיבור נסגר";
    if (powerClosed()) return;                 // #1850: הניתוק שחיכינו לו
    stopControl();
    if (event.code === 4409) {                 // #1852: מי מחזיק — ובקשת שליטה, לא ניסיון חוזר
      heldBySomeoneElse(reason).then((held) => { if (!held && ws === sock) scheduleRetry(reason); });
      return;
    }
    if (NO_RETRY.has(event.code)) showClosed(reason);
    else scheduleRetry(reason);
  };
  sock.onerror = () => { if (ws === sock) setStatus("החיבור נכשל", "bad"); };
}

/* קלט המסך: קליק תופס פוקוס, המקלדת נלכדת כל עוד ה-canvas ממוקד. */
/* ‏#1475: אירועי pointer עם לכידה — גרירה שיוצאת מה-canvas ממשיכה,
   והשחרור מגיע גם מחוצה לו (בלעדיה הכפתור נשאר לחוץ במכונה). יציאה
   מה-canvas שולחת את המיקום המוצמד לקצה: עד כאן הסמן המרוחק נשאר
   בדגימה האחרונה שנפלה בפנים — "נמוך מדי" כשהעכבר עולה לסרגל. */
canvas.addEventListener("pointermove", pointer);
canvas.addEventListener("pointerleave", pointer);
canvas.addEventListener("pointerdown", (e) => {
  e.preventDefault(); canvas.focus();
  if (canvas.setPointerCapture) canvas.setPointerCapture(e.pointerId);
  buttonMask |= (1 << e.button); pointerButton(e);
});
canvas.addEventListener("pointerup", (e) => { buttonMask &= ~(1 << e.button); pointerButton(e); });
canvas.addEventListener("contextmenu", (e) => e.preventDefault());
canvas.addEventListener("keydown", (e) => { e.preventDefault(); keyEvent(keysymFor(e), true); });
canvas.addEventListener("keyup", (e) => { e.preventDefault(); keyEvent(keysymFor(e), false); });

/* ‏#1854: קלט של המפעיל כאן הוא פעילות בקונסולה — אחרת מי שעובד במסך המכונה
   (iframe או חלון נפרד) נזרק אחרי console_idle_seconds. ‏BroadcastChannel: אותו
   origin בלבד מעצם הגדרתו, מגיע גם לחלון שנפתח עם noopener, ונושא רק "פעילות"
   — לא תוכן ולא קלט. רק קלט נספר, לא מסגרות: מסך שמשתנה לבד (פס התקדמות) אינו
   אדם ליד המקלדת. לכל היותר הודעה אחת ב-5 ש'. */
const ACTIVITY_CHANNEL = "imagesrv-activity";
const ACTIVITY_EVERY_MS = 5000;
const activityChannel = typeof BroadcastChannel === "function"
  ? new BroadcastChannel(ACTIVITY_CHANNEL) : null;
let activitySentAt = -Infinity;
function noteMonitorActivity() {
  const now = Date.now();
  if (!activityChannel || now - activitySentAt < ACTIVITY_EVERY_MS) return;
  activitySentAt = now;
  activityChannel.postMessage("activity");
}
["pointerdown", "pointermove", "keydown", "wheel", "touchstart"].forEach((type) =>
  document.addEventListener(type, noteMonitorActivity, { passive: true, capture: true }));

/* מודאל אישור לפעולת כוח (#1270): שאלה, השלכה וכפתור אדום — בלי הקלדה,
   במקום prompt/confirm החסומים. משתמש ב-DOM ובמחלקות של console.css. */
function powerModal(action, label) {
  const modal = document.getElementById("pmodal");
  const error = document.getElementById("pmodal-error");
  document.getElementById("pmodal-title").textContent = label + " — אישור";
  document.getElementById("pmodal-msg").textContent =
    `הפעולה "${label}" תתבצע על המחשב המנוטר עכשיו.`;
  document.getElementById("pmodal-name").textContent = NAME;
  document.getElementById("pmodal-ok").textContent = label;
  error.textContent = "";
  modal.style.display = "flex";
  document.getElementById("pmodal-cancel").focus();

  const close = () => {
    modal.style.display = "none";
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (event) => { if (event.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  document.getElementById("pmodal-cancel").onclick = close;
  document.getElementById("pmodal-ok").onclick = () => {
    /* ‏#1129: ההשוואה בשרת. השם הידוע (NAME) נשלח כ-confirm; 403 = לא זהה. */
    error.textContent = "";
    sendPower(action, NAME).then(() => { close(); awaitPower(action, label); },
      (e) => { error.textContent = e.message; });
  };
}

document.getElementById("reboot-btn").addEventListener(
  "click", () => powerModal("reboot", "הפעלה מחדש"));
document.getElementById("poweroff-btn").addEventListener(
  "click", () => powerModal("poweroff", "כיבוי"));
document.getElementById("reconnect").addEventListener("click", () => {
  retries = 0; connect();                      // connect() מנתק את הקודם
});

/* מסך מלא (console-full.html): הדף כולו, כדי שחלון האישור של פעולת כוח ייראה. הסרגל צף ומוסתר, ומופיע 2.5 שניות
   אחרי תזוזת עכבר. Esc יוצא (הדפדפן מטפל בו — הוא לא מגיע ל-canvas). דפדפן שלא מאשר מסך מלא = כפתור מנוטרל עם
   הסבר, לא כפתור שלא עושה כלום. */
const fsBtn = document.getElementById("fs-btn");
let fsTimer = null;
function inFullscreen() { return !!document.fullscreenElement; }
function showFsBar() {
  const root = document.documentElement;
  root.classList.add("bar-on");
  clearTimeout(fsTimer);
  fsTimer = setTimeout(() => root.classList.remove("bar-on"), 2500);
}
function syncFullscreen() {
  const on = inFullscreen();
  fsBtn.textContent = on ? "יציאה ממסך מלא" : "מסך מלא";
  if (on) { showFsBar(); canvas.focus(); }
  else { clearTimeout(fsTimer); document.documentElement.classList.remove("bar-on"); }
}
if (!document.fullscreenEnabled) {
  fsBtn.disabled = true;
  fsBtn.title = "הדפדפן אינו מאפשר מסך מלא בדף הזה";
} else {
  fsBtn.addEventListener("click", () => {
    const done = inFullscreen() ? document.exitFullscreen() : document.documentElement.requestFullscreen();
    Promise.resolve(done).catch((e) => setStatus("מסך מלא נדחה: " + e.message, "bad"));
  });
  document.addEventListener("fullscreenchange", syncFullscreen);
  document.addEventListener("mousemove", () => { if (inFullscreen()) showFsBar(); });
}

if (!MAC) setStatus("חסר MAC בכתובת", "bad");
else connect();
