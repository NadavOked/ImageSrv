// #1852 (הכרעת נדב 19/10): מנהל שני שמקבל 4409 רואה "המכונה בצפייה של <שם>" וכפתור "בקש שליטה" —
// לא ספירה של 9 דקות שחוטפת את המכונה. המחזיק רואה את הבקשה עם אשר/סרב. ו"פתח בחלון נפרד"
// מוציא את ה-iframe מהדף. בקרה שלילית: על הקוד שלפני #1852 4409 נכנס לניסיון החוזר, אין כפתור,
// ואין monitorPopOut.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function element(tag) {
  return {tag, textContent: '', className: '', style: {}, title: '', onclick: null, disabled: false, children: [],
    classList: {_s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); }},
    appendChild(c) { this.children.push(c); return c; }, addEventListener() {}, focus() {},
    getContext() { return {fillRect() {}}; },
    set textContent(v) { this._t = v; if (v === '') this.children = []; }, get textContent() { return this._t || ''; }};
}

function setup() {
  const timers = [], sockets = [], calls = [];
  const nodes = {};
  class FakeWebSocket { constructor(url) { this.url = url; this.sent = []; sockets.push(this); } send() {} close() {} }
  const ctx = vm.createContext({
    console, URLSearchParams, Uint8Array, TextDecoder, TextEncoder, Promise, Math, Error, JSON, Date,
    encodeURIComponent, location: {search: '?mac=aa:bb:cc:dd:ee:ff&name=Cloner02', protocol: 'http:', host: 'srv:8081'},
    document: {getElementById: (id) => (nodes[id] ||= element('x')), createElement: (t) => element(t), title: '',
      fullscreenEnabled: false, addEventListener() {}, removeEventListener() {},
      documentElement: {classList: {add() {}, remove() {}}}},
    WebSocket: FakeWebSocket,
    setTimeout: (fn, ms) => { timers.push({fn, ms, done: false}); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].done = true; },
    fetch: async (url, options) => { calls.push({url, method: (options && options.method) || 'GET', body: options && options.body});
      const reply = ctx.__reply(url, options || {});
      return {ok: reply.status ? reply.status < 300 : true, status: reply.status || 200, json: async () => reply.body || reply}; },
  });
  ctx.__reply = () => ({});
  vm.runInContext(fs.readFileSync(path.join(root, 'monitor.js'), 'utf8'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };
  const control = () => nodes.control || element('x');
  const buttons = () => control().children.filter((c) => c.tag === 'button');
  const text = () => control().children.filter((c) => c.tag === 'span').map((c) => c.textContent).join(' ');
  const due = () => timers.filter((t) => !t.done);
  return {ctx, run, timers, sockets, calls, flush, buttons, text, due, nodes};
}

test('4409 from another admin: who holds it, and "בקש שליטה" — not a 9-minute retry', async () => {
  const {ctx, run, sockets, flush, buttons, due} = setup();
  ctx.__reply = () => ({holder: 'noc', you_hold: false, request: null, granted_to_you: false, last: null});
  const before = due().length;
  sockets[0].onclose({code: 4409, reason: 'המכונה בצפייה של noc'});
  await flush();
  assert.equal(run('statusEl.textContent'), 'המכונה בצפייה של noc');
  assert.deepEqual(buttons().map((b) => b.textContent), ['בקש שליטה']);
  assert.equal(due().length, before, 'no retry is scheduled');
});

test('the request waits for the holder, and connects when control is granted', async () => {
  const {ctx, sockets, calls, flush, buttons, text, due} = setup();
  let granted = false;
  ctx.__reply = (url, options) => {
    if (url.endsWith('/control/request')) return {outcome: 'pending', seconds_left: 60};
    if (granted) return {holder: null, you_hold: false, request: null, granted_to_you: true, last: {by: 'ada', outcome: 'approved'}};
    return {holder: 'noc', you_hold: false, request: {by: 'ada', seconds_left: 57}, granted_to_you: false, last: null};
  };
  sockets[0].onclose({code: 4409, reason: 'המכונה בצפייה של noc'});
  await flush();
  buttons()[0].onclick();
  await flush();
  assert.ok(calls.some((c) => c.url.endsWith('/control/request') && c.method === 'POST'));
  assert.match(text(), /הבקשה נשלחה ל-noc — ממתין לאישור/);
  due().at(-1).fn();                                   // שאלה ראשונה: עוד ממתין
  await flush();
  assert.match(text(), /\(57 ש'\)/);
  granted = true;
  due().at(-1).fn();
  await flush();
  assert.equal(sockets.length, 2, 'connected again once granted');
});

test('a refusal is said by name, and the button comes back', async () => {
  const {ctx, sockets, flush, buttons, text, due} = setup();
  let answered = false;
  ctx.__reply = (url) => {
    if (url.endsWith('/control/request')) return {outcome: 'pending', seconds_left: 60};
    return answered
      ? {holder: 'noc', you_hold: false, request: null, granted_to_you: false, last: {by: 'ada', outcome: 'denied'}}
      : {holder: 'noc', you_hold: false, request: null, granted_to_you: false, last: null};
  };
  sockets[0].onclose({code: 4409, reason: 'המכונה בצפייה של noc'});
  await flush();
  buttons()[0].onclick();
  await flush();
  answered = true;
  due().at(-1).fn();
  await flush();
  assert.match(text(), /noc סירב לבקשה/);
  assert.deepEqual(buttons().map((b) => b.textContent), ['בקש שליטה']);
});

test('the holder sees the request with approve / refuse', async () => {
  const {ctx, run, calls, flush, buttons, text, due} = setup();
  ctx.__reply = (url) => url.endsWith('/answer') ? {outcome: 'approved'}
    : {holder: 'noc', you_hold: true, request: {by: 'ada', seconds_left: 42}, granted_to_you: false, last: null};
  run('rfbReady = true; watchControl()');
  due().at(-1).fn();
  await flush();
  assert.match(text(), /ada מבקש שליטה במכונה \(42 ש'\)/);
  assert.deepEqual(buttons().map((b) => b.textContent), ['אשר', 'סרב']);
  buttons()[0].onclick();
  await flush();
  const answer = calls.find((c) => c.url.endsWith('/control/answer'));
  assert.deepEqual(JSON.parse(answer.body), {approve: true});
});

test('a view moved to another window (4410) or handed over (4411) is not retried', async () => {
  const {sockets, flush, due, run} = setup();
  const before = due().length;
  sockets[0].onclose({code: 4410, reason: 'המוניטור נפתח בחלון אחר'});
  await flush();
  assert.equal(due().length, before);
  assert.equal(run('statusEl.textContent'), 'המוניטור נפתח בחלון אחר');
});

test('"פתח בחלון נפרד" takes the iframe out of the console page', () => {
  const js = fs.readFileSync(path.join(root, 'console.js'), 'utf8');
  const lines = js.split('\n');
  const start = lines.findIndex((l) => /^function monitorPopOut\(/.test(l));
  assert.ok(start >= 0, 'monitorPopOut exists');
  const end = lines.findIndex((l, i) => i > start && l.trimEnd() === '}');
  let replaced = null;
  const frame = {replaceWith(n) { replaced = n; }};
  const ctx = vm.createContext({document: {querySelector: () => frame, createElement: () => ({})}});
  vm.runInContext(lines.slice(start, end + 1).join('\n') + '\nmonitorPopOut();', ctx);
  assert.ok(replaced, 'the iframe was replaced');
  assert.equal(replaced.textContent, 'המסך פתוח בחלון נפרד');
  assert.match(js, /rel="noopener" onclick="monitorPopOut\(\)">פתח בחלון נפרד/);
});
