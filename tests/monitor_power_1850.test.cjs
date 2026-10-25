// #1850: פקודת כוח מהמוניטור אומרת מה באמת קרה. ‏200 מ-…/power הוא "נכתב ל-socket", לא "בוצע"
// (עיקרון 5): הדף אומר שהפקודה נשלחה ומחכה לניתוק. אחרי הפעלה מחדש — מתחבר מחדש לבד; אחרי
// כיבוי — "המחשב כבה" ולא ספירה של 9 דקות; בלי ניתוק בזמן — בגלוי. ושגיאת רשת — בעברית, לא
// "Failed to fetch". בקרה שלילית: על הקוד שלפני #1850 המודאל נסגר בשקט והסטטוס נשאר "מחובר".
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function setup() {
  const timers = [];
  const sockets = [];
  const nodes = {};
  const stub = () => ({textContent: '', className: '', style: {}, title: '', onclick: null, disabled: false,
    classList: {_s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); }},
    addEventListener() {}, focus() {}, getContext() { return {fillRect() {}}; }});
  class FakeWebSocket {
    constructor(url) { this.url = url; this.sent = []; sockets.push(this); }
    send(b) { this.sent.push(b); }
    close() { this.closed = true; }
  }
  const ctx = vm.createContext({
    console, URLSearchParams, Uint8Array, TextDecoder, TextEncoder, Promise, Math, Error, JSON, Date,
    encodeURIComponent, location: {search: '?mac=aa:bb:cc:dd:ee:ff&name=Cloner02', protocol: 'http:', host: 'srv:8081'},
    document: {getElementById: (id) => (nodes[id] ||= stub()), title: '', fullscreenEnabled: false,
      addEventListener() {}, removeEventListener() {}, documentElement: {classList: {add() {}, remove() {}}}},
    WebSocket: FakeWebSocket,
    setTimeout: (fn, ms) => { timers.push({fn, ms, done: false}); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].done = true; },
    fetch: async () => ctx.__fetch(),
  });
  ctx.__fetch = async () => ({ok: true, json: async () => ({ok: true})});
  vm.runInContext(fs.readFileSync(path.join(root, 'monitor.js'), 'utf8'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  const confirm = async (action, label) => {
    run(`powerModal(${JSON.stringify(action)}, ${JSON.stringify(label)})`);
    run('document.getElementById("pmodal-ok").onclick()');
    for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
  };
  return {ctx, run, timers, sockets, confirm, status: () => run('statusEl.textContent')};
}

test('after a reboot is sent the page says so and waits — not a silent "מחובר"', async () => {
  const {confirm, status} = setup();
  await confirm('reboot', 'הפעלה מחדש');
  assert.match(status(), /^נשלחה פקודת הפעלה מחדש — ממתין שהמחשב יתנתק ויעלה מחדש/);
});

test('the disconnect after a reboot is the evidence: it reconnects by itself', async () => {
  const {confirm, status, sockets, timers} = setup();
  await confirm('reboot', 'הפעלה מחדש');
  const before = timers.length;
  sockets[0].onclose({code: 4513, reason: 'החיבור למחשב נותק — ייתכן שהמחשב כבה'});
  assert.match(status(), /^המחשב מאתחל \(פקודת הפעלה מחדש\) — מתחבר מחדש כשיעלה — ניסיון חוזר/);
  assert.ok(timers.length > before, 'a retry is scheduled');
});

test('after a power-off the page says the machine is off and does not count down 9 minutes', async () => {
  const {confirm, status, sockets, timers} = setup();
  await confirm('poweroff', 'כיבוי');
  assert.match(status(), /ממתין שהמחשב ייכבה/);
  const pending = timers.filter((t) => !t.done).length;
  sockets[0].onclose({code: 4513, reason: ''});
  assert.equal(status(), 'המחשב כבה (פקודת כיבוי)');
  assert.equal(timers.filter((t) => !t.done).length, pending - 1, 'the wait timer is cleared and no retry is added');
});

test('no disconnect within the wait is said plainly', async () => {
  const {confirm, status, timers} = setup();
  await confirm('reboot', 'הפעלה מחדש');
  const wait = timers.find((t) => t.ms === 90000 && !t.done);
  assert.ok(wait, 'a 90s wait');
  wait.fn();
  assert.match(status(), /פקודת הפעלה מחדש נשלחה, אבל המחשב לא התנתק תוך 90 שניות — ייתכן שלא בוצעה/);
});

test('a network failure reaches the modal in Hebrew, not "Failed to fetch"', async () => {
  const {ctx, run} = setup();
  ctx.__fetch = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(run('sendPower("reboot", "Cloner02")'), /^Error: אין חיבור לשרת — הפקודה לא נשלחה$/);
});
