// #1129 (R48): לקוח ה-RFB בדפדפן (monitor.js) עם תקרות — תחנה עוינת או
// מוניטור שהשתבש אינם מפילים את הטאב: מלבן של w*h*4 מעל 32MB, מלבן מחוץ
// ל-framebuffer, DesktopSize לא סביר, ServerCutText מעל 64KB וחוצץ קלט
// מעל 64MB נסגרים בשגיאה בשם. ‏monitor.js המשוגר רץ ב-vm עם DOM/WebSocket
// מזויפים; מזרימים בייטים ל-ByteStream ומריצים את הפרסור עצמו.
// וגם: פעולת כוח היא POST לשרת עם השם שהוקלד — לא ClientCutText על ה-WS.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function setup(search = '?mac=aa:bb:cc:dd:ee:ff&name=Cloner01', doc = {}, extra = {}) {
  const listeners = {}, docListeners = {}, timers = [];
  const classList = () => ({_s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); }});
  const stub = () => ({textContent: '', className: '', value: '', style: {}, onclick: null, disabled: false, title: '',
    classList: classList(),
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); (this.__l ||= {})[name] = fn; }, focus() {}, getBoundingClientRect() { return {left: 0, top: 0, width: 1, height: 1}; },
    getContext() { return {fillRect() {}, createImageData(w, h) { return {data: new Uint8ClampedArray(w * h * 4)}; }, putImageData() {}, drawImage() {}}; }});
  const sockets = [];
  const fetches = [];
  class FakeWebSocket {
    constructor(url) { this.url = url; this.sent = []; this.readyState = 1; this.closed = false; sockets.push(this); }
    send(b) { this.sent.push(b); }
    close() { this.closed = true; }
  }
  FakeWebSocket.OPEN = 1;
  const ctx = vm.createContext({
    console, URLSearchParams, Uint8Array, Uint8ClampedArray, TextEncoder, TextDecoder, Promise, Math, Error, Array, JSON, Number,
    encodeURIComponent, location: {search, protocol: 'http:', host: 'srv:8081'},
    document: {getElementById: (() => { const nodes = {}; return (id) => (nodes[id] ||= stub()); })(), title: '',
      addEventListener(name, fn) { (docListeners[name] ||= []).push(fn); }, removeEventListener() {},
      documentElement: {classList: {_s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); }}}, ...doc},
    WebSocket: FakeWebSocket, setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout() {},
    fetch: async (url, options) => { fetches.push({url, options}); return ctx.__fetchReply || {ok: true, json: async () => ({})}; },
    Date, ...extra,
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'monitor.js'), 'utf8'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  return {ctx, run, sockets, fetches, listeners, docListeners, timers};
}

test('machine-side close dims the last frame and shows the Hebrew disconnect message', () => {
  const {run, sockets} = setup();
  const reason = 'החיבור למחשב נותק — ייתכן שהמחשב כבה';
  sockets[0].onclose({code: 4513, reason: ''});
  // rc6 (18/10): ואחרי הסיבה — הספירה לניסיון החוזר (ראו "הריענון" למטה).
  assert.ok(run('statusEl.textContent').startsWith(reason + ' — ניסיון חוזר'), run('statusEl.textContent'));
  assert.ok(run('disconnectOverlay.textContent').startsWith(reason));
  assert.equal(run('canvas.classList.contains("disconnected")'), true);
  assert.equal(run('disconnectOverlay.classList.contains("visible")'), true);
});

/* מזרים למחלקת ByteStream של הדף בייטים ומריץ קורוטינה של הפרסור. */
function feed(run, bytes) {
  run('stream = new ByteStream(); fbWidth = 1024; fbHeight = 768;');
  run(`stream.push(new Uint8Array(${JSON.stringify(Array.from(bytes))}).buffer)`);
}
const u16 = (n) => [(n >> 8) & 255, n & 255];
const u32 = (n) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
const rect = (x, y, w, h, enc) => [...u16(x), ...u16(y), ...u16(w), ...u16(h), ...u32(enc >>> 0)];

test('a Raw rect larger than 32MB fails by name instead of allocating', async () => {
  const {run} = setup();
  feed(run, [0, 0, 1, ...rect(0, 0, 4096, 4096, 0)]);         // 64MB
  await assert.rejects(run('framebufferUpdate()'), /גדול מדי: 4096×4096/);
});

test('a rect outside the framebuffer fails by name (Raw and CopyRect)', async () => {
  const {run} = setup();
  feed(run, [0, 0, 1, ...rect(1000, 0, 100, 10, 0)]);
  await assert.rejects(run('framebufferUpdate()'), /מחוץ למסך: 1000,0 100×10 במסך 1024×768/);
  feed(run, [0, 0, 1, ...rect(0, 700, 10, 100, 1)]);
  await assert.rejects(run('framebufferUpdate()'), /CopyRect מחוץ למסך/);
  feed(run, [0, 0, 1, ...rect(0, 0, 10, 10, 1), ...u16(1020), ...u16(0)]);   // מקור מחוץ למסך
  await assert.rejects(run('framebufferUpdate()'), /מקור CopyRect מחוץ למסך/);
});

test('a sane Raw rect still draws and a DesktopSize within limits resizes', async () => {
  const {run} = setup();
  feed(run, [0, 0, 2, ...rect(10, 10, 2, 2, 0), ...new Array(16).fill(7), ...rect(0, 0, 1280, 720, -223)]);
  await run('framebufferUpdate()');
  assert.equal(run('fbWidth'), 1280); assert.equal(run('fbHeight'), 720);
});

test('DesktopSize beyond 32MB is refused by name', async () => {
  const {run} = setup();
  feed(run, [0, 0, 1, ...rect(0, 0, 65535, 65535, -223)]);
  await assert.rejects(run('framebufferUpdate()'), /גודל מסך לא סביר: 65535×65535/);
});

test('ServerCutText above 64KB is refused by name', async () => {
  const {run} = setup();
  feed(run, [0, 0, 0, ...u32(64 * 1024 + 1)]);
  await assert.rejects(run('serverCutText()'), /ServerCutText ארוך מדי: 65537 בייט/);
  feed(run, [0, 0, 0, ...u32(3), 65, 66, 67]);
  await run('serverCutText()');                                 // בגבול — עובר
});

test('ByteStream refuses to buffer more than 64MB and fails the pending read by name', async () => {
  const {run} = setup();
  run('stream = new ByteStream();');
  const pending = run('stream.read(1)');                        // ממתין
  for (let i = 0; i < 65; i++) run('stream.push(new ArrayBuffer(1024 * 1024))');   // 65MB: הראשון משחרר את הקריאה, השאר נצברים עד התקרה
  // הקריאה הממתינה קיבלה את הבייט הראשון; הבאה נכשלת בשם.
  await pending;
  await assert.rejects(run('stream.read(1)'), /חוצץ הקלט עבר 64MB/);
  assert.equal(run('stream.closed'), true);
});

test('handshake refuses an oversized reject-reason and server name', async () => {
  const {run} = setup();
  const enc = (s) => Array.from(new TextEncoder().encode(s));
  feed(run, [...enc('RFB 003.008\n'), 0, ...u32(64 * 1024 + 1)]);
  await assert.rejects(run('handshake()'), /סיבה ארוכה מדי/);
  feed(run, [...enc('RFB 003.008\n'), 1, 1, ...u32(0), ...u16(800), ...u16(600), ...new Array(16).fill(0), ...u32(1 << 20)]);
  await assert.rejects(run('handshake()'), /שם המסך ארוך מדי/);
});

test('power goes to POST …/power with the typed name; the client never builds ClientCutText', async () => {
  const {run, sockets, fetches} = setup();
  assert.equal(sockets.length, 1, 'connected on load');
  await run('sendPower("reboot", "typed-name")');
  assert.equal(fetches.length, 1);
  assert.equal(fetches[0].url, '/api/console/monitor/aa%3Abb%3Acc%3Add%3Aee%3Aff/power');
  assert.equal(fetches[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(fetches[0].options.body), {action: 'reboot', confirm: 'typed-name'});
  assert.equal(sockets[0].sent.length, 0, 'nothing on the RFB channel');
  const js = fs.readFileSync(path.join(root, 'monitor.js'), 'utf8');
  assert.doesNotMatch(js, /clientCutText|imagesrv-power/);
  // דרך משני: הנתיב של המשני, אותו גוף.
  const remote = setup('?mac=aa:bb:cc:dd:ee:ff&node=n0de');
  await remote.run('sendPower("poweroff", "x")');
  assert.equal(remote.fetches[0].url, '/api/console/storage-nodes/n0de/monitor/aa%3Abb%3Acc%3Add%3Aee%3Aff/power');
});

test('a 403 from the server surfaces its detail — the name check is the server’s, not the page’s', async () => {
  const {run, ctx} = setup();
  ctx.__fetchReply = {ok: false, status: 403, json: async () => ({detail: 'השם שהוקלד אינו זהה לשם המחשב'})};
  await assert.rejects(run('sendPower("reboot", "wrong")'), /השם שהוקלד אינו זהה לשם המחשב/);
});

test('every printable ASCII key uses its US physical keysym, independent of layout and Shift', () => {
  const {run} = setup();
  const rows = [
    ['Backquote', '`~'], ['Digit1', '1!'], ['Digit2', '2@'], ['Digit3', '3#'],
    ['Digit4', '4$'], ['Digit5', '5%'], ['Digit6', '6^'], ['Digit7', '7&'],
    ['Digit8', '8*'], ['Digit9', '9('], ['Digit0', '0)'], ['Minus', '-_'],
    ['Equal', '=+'], ['BracketLeft', '[{'], ['BracketRight', ']}'],
    ['Backslash', '\\|'], ['Semicolon', ';:'], ['Quote', "'\""],
    ['Comma', ',<'], ['Period', '.>'], ['Slash', '/?'], ['Space', ' '],
  ];
  for (let i = 0; i < 26; i++) rows.push([`Key${String.fromCharCode(65 + i)}`, String.fromCharCode(97 + i, 65 + i)]);
  const covered = new Set();
  for (const [code, characters] of rows) {
    const expected = characters.charCodeAt(0);
    for (const key of characters) {
      covered.add(key);
      assert.equal(run(`keysymFor(${JSON.stringify({code, key})})`), expected, `${JSON.stringify(key)} via ${code}`);
    }
  }
  assert.deepEqual([...covered].sort(), Array.from({length: 95}, (_, i) => String.fromCharCode(32 + i)).sort());
  assert.equal(run(`keysymFor(${JSON.stringify({code: 'KeyT', key: 'א'})})`), 't'.charCodeAt(0), 'Hebrew layout still sends the physical T key');
});

/* ---------- מסך מלא (stage 3, console-full.html) ---------- */
test('stage 3: fullscreen button — the whole page goes fullscreen (so the power-action modal stays visible), the bar shows on mouse move and hides after 2.5s, Esc/exit restores', async () => {
  const calls = [];
  const root = {requestFullscreen: async () => { calls.push('enter'); }, classList: {_s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); }}};
  const doc = {fullscreenEnabled: true, fullscreenElement: null, exitFullscreen: async () => { calls.push('exit'); }, documentElement: root};
  const {run, ctx, docListeners, timers} = setup(undefined, doc);
  assert.equal(run("fsBtn.disabled"), false);
  await run("fsBtn").__l.click();
  assert.deepEqual(calls, ['enter'], 'enter = requestFullscreen on the documentElement, not on a sub-tree');
  assert.ok(docListeners.fullscreenchange && docListeners.mousemove, 'document listeners wired');
  // כניסה
  ctx.document.fullscreenElement = root;
  docListeners.fullscreenchange.forEach((f) => f());
  assert.equal(run("fsBtn.textContent"), 'יציאה ממסך מלא');
  assert.ok(root.classList.contains('bar-on'), 'bar visible right after entering');
  assert.equal(timers.at(-1).ms, 2500, 'hides after 2.5s'); timers.at(-1).fn(); assert.ok(!root.classList.contains('bar-on'), 'the timer hides it');
  // תזוזת עכבר מחזירה את הסרגל
  root.classList.remove('bar-on'); docListeners.mousemove.forEach((f) => f());
  assert.ok(root.classList.contains('bar-on'));
  // מחוץ למסך מלא: תזוזה לא מציגה כלום
  ctx.document.fullscreenElement = null; docListeners.fullscreenchange.forEach((f) => f());
  assert.equal(run("fsBtn.textContent"), 'מסך מלא'); assert.ok(!root.classList.contains('bar-on'));
  ctx.document.fullscreenElement = root; await run("fsBtn").__l.click();
  assert.deepEqual(calls, ['enter', 'exit'], 'the same button leaves fullscreen');
  ctx.document.fullscreenElement = null;
  docListeners.mousemove.forEach((f) => f()); assert.ok(!root.classList.contains('bar-on'), 'mouse move outside fullscreen does not show the bar');
});

test('stage 3: a browser without fullscreen gets a disabled button with a reason — not a button that does nothing', () => {
  const {run} = setup();   // fullscreenEnabled חסר = לא מאומת
  assert.equal(run("fsBtn.disabled"), true); assert.match(run("fsBtn.title"), /אינו מאפשר מסך מלא/);
});

/* ---------- #1475: מיפוי העכבר, יציאה מה-canvas, וקצב התזוזות ---------- */
/* ה-canvas כפי שנמדד בדף האמיתי (Chromium, viewport 1600×700, fb 1920×1080,
   16/10): מתחת לסרגל של 48px, מוקטן ל-0.6037 ומרוכז. */
const SCALED = {left: 220.45, top: 48, width: 1159.1063, height: 651.9938};
const FULL = {left: 0, top: 0, width: 1920, height: 1080};       // מסך מלא, 1:1
const HALF = {left: 0, top: 0, width: 960, height: 540};

function pointerPage(rect) {
  const clock = {now: 1000};
  const page = setup(undefined, {}, {Date: {now: () => clock.now}});
  page.run('fbWidth = 1920; fbHeight = 1080; rfbReady = true;');   // אחרי לחיצת היד (rc6)
  const canvas = page.run('canvas');
  canvas.getBoundingClientRect = () => ({...rect, right: rect.left + rect.width, bottom: rect.top + rect.height});
  canvas.setPointerCapture = (id) => { (canvas.captured ||= []).push(id); };
  const sent = () => page.sockets[0].sent.map((b) => [b[1], (b[2] << 8) | b[3], (b[4] << 8) | b[5]]);
  const fire = (name, clientX, clientY, more = {}) =>
    canvas.__l[name]({clientX, clientY, button: 0, pointerId: 7, preventDefault() {}, ...more});
  return {...page, clock, canvas, sent, fire};
}

test('#1475: canvas corners and centre land on the right framebuffer pixel — scaled under the toolbar, full screen, half size', () => {
  const at = (rect, x, y) => pointerPage(rect).run(`fbCoords({clientX: ${x}, clientY: ${y}})`);
  const r = SCALED;
  assert.deepEqual(Array.from(at(r, r.left, r.top)), [0, 0], 'top-left corner');
  assert.deepEqual(Array.from(at(r, r.left + r.width - 0.01, r.top + r.height - 0.01)), [1919, 1079], 'bottom-right corner');
  assert.deepEqual(Array.from(at(r, r.left + r.width / 2, r.top + r.height / 2)), [960, 540], 'centre');
  assert.deepEqual(Array.from(at(r, r.left + r.width / 2, 24)), [960, 0], 'over the toolbar = the top row, not an offset');
  assert.deepEqual(Array.from(at(r, r.left - 50, r.top + r.height + 50)), [0, 1079], 'outside = clamped to the edge');
  // 1:1 (מסך מלא): הפיקסל שמתחת לסמן. ‏round היה נותן 101,201.
  assert.deepEqual(Array.from(at(FULL, 100.7, 200.6)), [100, 200], 'full screen 1:1 is the pixel under the pointer');
  assert.deepEqual(Array.from(at(FULL, 1919.99, 1079.99)), [1919, 1079]);
  // חצי גודל: ‏CSS 5.9 מכסה את פיקסלי fb ‏10–11; floor(11.8) = 11.
  assert.deepEqual(Array.from(at(HALF, 5.9, 5.2)), [11, 10]);
});

test('#1475: leaving the canvas sends the edge position — the remote pointer reaches the top instead of staying at the last inside sample', () => {
  const {fire, sent, clock, timers} = pointerPage(SCALED);
  const midX = SCALED.left + SCALED.width / 2;
  fire('pointermove', midX, SCALED.top + SCALED.height / 2);   // מרכז
  clock.now += 100;
  fire('pointerleave', midX, 24);                              // עלה לסרגל
  timers.splice(0).forEach((t) => t.fn());
  assert.deepEqual(sent().at(-1), [0, 960, 0], `sent: ${JSON.stringify(sent())}`);
});

test('#1475: moves are coalesced to one per 33 ms and the latest always goes out; buttons are never delayed and capture the pointer', () => {
  const {fire, sent, clock, timers, canvas} = pointerPage(FULL);
  fire('pointermove', 10, 10);                                 // מיד
  fire('pointermove', 11, 11);                                 // בתוך החלון — ממתין
  fire('pointermove', 12, 12);                                 // מחליף את הממתין
  assert.deepEqual(sent(), [[0, 10, 10]]);
  assert.equal(timers.length, 1, 'one trailing timer, not one per move');
  assert.equal(timers[0].ms, 33);
  clock.now += 33; timers.splice(0)[0].fn();
  assert.deepEqual(sent(), [[0, 10, 10], [0, 12, 12]], 'the latest position, once');
  fire('pointerdown', 13, 13);                                 // בתוך החלון — ובכל זאת מיד
  fire('pointerup', 300, 2000);                                // שחרור מחוץ ל-canvas (לכוד)
  assert.deepEqual(sent().slice(2), [[1, 13, 13], [0, 300, 1079]]);
  assert.deepEqual(canvas.captured, [7], 'pointer captured on press: a drag out of the canvas keeps going');
});

test('#1475: one shared cursor — the browser hides its own over the canvas; the one drawn by the GUI is the cursor', () => {
  // נדב 16/10: הסמן המצויר במכונה הוא הסמן; מי שליד המכונה רואה אותו. סמן
  // דפדפן גלוי מעל ה-canvas = שני סמנים. מחוץ ל-canvas (סרגל) הדפדפן מראה כרגיל.
  const html = fs.readFileSync(path.join(root, 'monitor.html'), 'utf8');
  const rule = html.match(/#screen\s*\{([^}]*)\}/);
  assert.ok(rule, 'no #screen rule in monitor.html');
  assert.match(rule[1], /cursor:\s*none\s*;/);
});

/* ---------- 18/10 (rc6): "שגיאה: סוג אבטחה לא צפוי" — קלט לפני לחיצת היד ----------
   ‏#1686 השאיר את ה-WebSocket פתוח עד 15 ש' בזמן שהשרת ממתין למוניטור של מכונה שעולה.
   כל אותו זמן ה-canvas קיבל אירועי עכבר/מקלדת, ו-send() שלח אותם כי ה-WS "פתוח" — לפני
   גרסת ה-RFB. הפרוקסי (monitor.bridge_browser) קרא אותם כ-12 בייטי הגרסה, והבייט שאחריהם
   כבחירת אבטחה ≠ 1 → ‏close 1002 "סוג אבטחה לא צפוי", מסך שחור בלי ניסיון חוזר. */
const enc = (s) => Array.from(new TextEncoder().encode(s));
const SERVER_INIT = [...u16(4), ...u16(2), ...new Array(16).fill(0), ...u32(0)];
const tick = () => new Promise((r) => setImmediate(r));
function serverSays(sock, bytes) { sock.onmessage({data: new Uint8Array(bytes).buffer}); }
const sentBytes = (sock) => sock.sent.flatMap((b) => Array.from(b));

test('rc6: pointer and keys before the RFB handshake are not sent — the proxy would read them as the version', async () => {
  const {fire, sockets, run} = pointerPage(FULL);
  run('connect()');                                          // מה שהדף עושה בטעינה / בניסיון חוזר
  const sock = sockets.at(-1);
  sock.onopen();                                             // WS פתוח, השרת עוד מתחבר למכונה
  fire('pointermove', 10, 10);
  fire('pointerdown', 11, 11);
  fire('pointerup', 11, 11);
  fire('keydown', 0, 0, {code: 'KeyA', key: 'a'});
  assert.deepEqual(sock.sent, [], 'input went out before the handshake');
  serverSays(sock, enc('RFB 003.008\n'));
  await tick();
  serverSays(sock, [1, 1]);
  await tick();
  serverSays(sock, u32(0));
  await tick();
  serverSays(sock, SERVER_INIT);
  await tick();
  const bytes = sentBytes(sock);
  assert.deepEqual(bytes.slice(0, 12), enc('RFB 003.008\n'), 'the first 12 bytes the proxy reads are the version');
  assert.equal(bytes[12], 1, 'the 13th byte is the security choice None');
  assert.equal(bytes[13], 1, 'then ClientInit');
});

test('rc6: after the handshake, input goes out as before', async () => {
  const {fire, sockets, timers} = pointerPage(FULL);
  const sock = sockets[0];
  sock.onopen();
  serverSays(sock, [...enc('RFB 003.008\n'), 1, 1, ...u32(0), ...SERVER_INIT]);
  for (let i = 0; i < 5; i++) await tick();
  const before = sock.sent.length;
  fire('pointerdown', 1, 1);
  timers.splice(0).forEach((t) => t.fn());
  assert.equal(sock.sent.length, before + 1);
  assert.equal(sock.sent.at(-1)[0], 5, 'a PointerEvent');
});

/* ---------- 18/10 (rc6): "הריענון" — אחרי סגירה המסך לא נשאר שחור ותקוע ----------
   עד כאן כל סגירה (מכונה שעולה, 4502, 1002, ‏4513) השאירה canvas שחור ושורת סטטוס, ורק
   "התחבר מחדש" ידני החזיר תמונה. עכשיו: הסיבה גלויה על המסך עצמו, ספירה לאחור, וניסיון
   חוזר לבד — בהשהיה שגדלה ועם תקרה. מה שחוזר על עצמו בלי קשר לזמן (התחברות, הרשאה, מכונה
   לא מוכרת, סגירת חוסר-פעילות מכוונת) — לא חוזר לבד. */
function drainUntil(page, done, limit = 200) {
  for (let i = 0; i < limit && !done(); i++) {
    const t = page.timers.shift();
    if (!t) break;
    t.fn();
  }
}

test('rc6: a retryable close shows the reason on the screen with a countdown, then reconnects by itself', () => {
  const page = setup();
  const reason = 'שירות המוניטור במכונה אינו זמין לאחר 15 שניות: המכונה סירבה לחיבור';
  page.sockets[0].onclose({code: 4502, reason});
  assert.match(page.run('statusEl.textContent'), /אינו זמין לאחר 15 שניות: המכונה סירבה לחיבור — ניסיון חוזר בעוד 2 שנ'/);
  assert.equal(page.run('disconnectOverlay.classList.contains("visible")'), true, 'the black canvas says why');
  assert.match(page.run('disconnectOverlay.textContent'), /המכונה סירבה לחיבור/);
  drainUntil(page, () => page.sockets.length > 1);
  assert.equal(page.sockets.length, 2, 'reconnected without a click');
  assert.equal(page.run('statusEl.textContent'), 'מתחבר…');
  assert.equal(page.run('disconnectOverlay.classList.contains("visible")'), true, 'not live until the handshake succeeds');
});

test('rc6: the unexpected-security-type close is retried too', () => {
  const page = setup();
  page.sockets[0].onclose({code: 1002, reason: 'סוג אבטחה לא צפוי'});
  drainUntil(page, () => page.sockets.length > 1);
  assert.equal(page.sockets.length, 2);
});

test('rc6: sign-in, permission, unknown machine and the deliberate idle close are not retried', () => {
  for (const code of [4401, 4403, 4404, 4408]) {
    const page = setup();
    page.sockets[0].onclose({code, reason: ''});
    drainUntil(page, () => page.sockets.length > 1);
    assert.equal(page.sockets.length, 1, `code ${code} retried`);
  }
});

test('rc6: the wait grows between failures and stops at a ceiling with a visible "התחבר מחדש"', () => {
  const page = setup();
  const waits = [];
  for (let i = 0; i < 40; i++) {
    page.sockets.at(-1).onclose({code: 4502, reason: 'x'});
    const m = page.run('statusEl.textContent').match(/בעוד (\d+) שנ'/);
    if (!m) break;
    waits.push(Number(m[1]));
    const n = page.sockets.length;
    drainUntil(page, () => page.sockets.length > n);
  }
  assert.deepEqual(waits.slice(0, 5), [2, 4, 8, 15, 30]);
  assert.ok(waits.length < 40, 'retries forever');
  assert.ok(waits.every((w) => w <= 30));
  assert.match(page.run('statusEl.textContent'), /התחבר מחדש/);
  assert.equal(page.timers.length, 0, 'no timer left after the ceiling');
});

test('rc6: a completed handshake clears the overlay and resets the backoff', async () => {
  const page = setup();
  page.sockets[0].onclose({code: 4502, reason: 'x'});
  drainUntil(page, () => page.sockets.length > 1);
  const sock = page.sockets[1];
  sock.onopen();
  serverSays(sock, [...enc('RFB 003.008\n'), 1, 1, ...u32(0), ...SERVER_INIT]);
  for (let i = 0; i < 5; i++) await tick();
  assert.equal(page.run('statusEl.textContent'), 'מחובר');
  assert.equal(page.run('disconnectOverlay.classList.contains("visible")'), false);
  sock.onclose({code: 4513, reason: ''});
  assert.match(page.run('statusEl.textContent'), /בעוד 2 שנ'/, 'the backoff starts over after a good connection');
});

test('rc6: "התחבר מחדש" leaves the old socket unable to touch the new connection', () => {
  const page = setup();
  const old = page.sockets[0];
  const onclose = old.onclose, onmessage = old.onmessage;
  page.run('document.getElementById("reconnect")').__l.click();
  assert.equal(page.sockets.length, 2);
  assert.equal(old.closed, true);
  onmessage({data: new Uint8Array([9, 9, 9]).buffer});
  onclose({code: 1006, reason: ''});
  assert.equal(page.run('stream.closed'), false, 'the old close failed the new stream');
  assert.equal(page.run('stream.length'), 0, 'old bytes landed in the new stream');
  assert.equal(page.run('statusEl.textContent'), 'מתחבר…');
  drainUntil(page, () => page.sockets.length > 2);
  assert.equal(page.sockets.length, 2, 'the old close scheduled a retry');
});
