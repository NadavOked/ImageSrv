// #2240 (הכרעה 03/11, אפשרות ב׳): כפתורי הכוח במוניטור, כשהמחשב באמצע שיכפול מקומי, מציגים את
// אזהרת חלון המחשב (#1367) — ו"כבה בכל זאת" הוא בקשה דרך הסוכן (`…/agent-power`,
// `confirm_local_copy`), לא `poweroff -f`. כפתור החירום נשאר: "המחשב לא עונה? כיבוי מיידי",
// אחרי אזהרה נוספת, שולח ל-`…/power` עם `immediate`. ושרת שמסרב (409, `local_copy`) — החלון
// עובר לאזהרה. בקרה שלילית: על monitor.js שלפני #2240 הלחיצה פותחת את החלון הרגיל ושולחת
// ל-`…/power` בלי לשאול, ואין `…/agent-power` כלל.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function setup({search = '?mac=aa:bb:cc:dd:ee:ff&name=Cloner02', copying = true} = {}) {
  const nodes = {};
  const listeners = {};
  const calls = [];
  // textContent = "" מנקה את הילדים, כמו ב-DOM
  const stub = (id) => ({id, _text: '', get textContent() { return this._text; },
    set textContent(v) { this._text = v; this.children = []; },
    className: '', style: {}, title: '', onclick: null, disabled: false,
    children: [], appendChild(c) { this.children.push(c); },
    classList: {_s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); }},
    addEventListener(type, fn) { (listeners[id + ':' + type] ||= []).push(fn); }, focus() {},
    getContext() { return {fillRect() {}}; }});
  class FakeWebSocket { constructor(url) { this.url = url; } send() {} close() {} }
  const answers = [];   // מה ש-POST עונה, לפי הסדר
  const ctx = vm.createContext({
    console, URLSearchParams, Uint8Array, TextDecoder, TextEncoder, Promise, Math, Error, JSON, Date, Object, Array,
    encodeURIComponent, location: {search, protocol: 'http:', host: 'srv:8081'},
    document: {getElementById: (id) => (nodes[id] ||= stub(id)), title: '', fullscreenEnabled: false,
      createElement: () => stub('new'), addEventListener() {}, removeEventListener() {},
      documentElement: {classList: {add() {}, remove() {}}}},
    WebSocket: FakeWebSocket,
    setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, opts) => {
      calls.push({url, method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null});
      if (!opts || !opts.method) {
        const row = {mac: 'aa:bb:cc:dd:ee:ff', name: 'Cloner02', local_copy: copying};
        return {ok: true, json: async () => (search.includes('node=') ? {connected: true, machines: [row]} : [row])};
      }
      return answers.shift() || {ok: true, json: async () => ({})};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'monitor.js'), 'utf8'), ctx);
  const el = (id) => nodes[id] || stub(id);
  const settle = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };
  const click = async (id) => { for (const fn of listeners[id + ':click'] || []) fn(); await settle(); };
  const ok = async () => { el('pmodal-ok').onclick(); await settle(); };
  const notes = () => el('pmodal-notes').children.map((c) => [c.className, c.textContent]);
  const posts = () => calls.filter((c) => c.method === 'POST');
  return {el, click, ok, notes, posts, answers, settle, status: () => el('status').textContent};
}

test('power-off over a local copy opens the warning, not the plain confirm', async () => {
  const {el, click, notes, posts} = setup();
  await click('poweroff-btn');
  assert.equal(el('pmodal-title').textContent, 'לכבות את Cloner02?');
  assert.equal(el('pmodal-msg').textContent, 'המחשב באמצע שיכפול מקומי');
  assert.equal(el('pmodal-ok').textContent, 'כבה בכל זאת');
  assert.equal(el('pmodal-danger').style.display, 'none');
  assert.deepEqual(notes()[0], ['notice warn',
    'המחשב באמצע שיכפול מקומי — כיבוי עכשיו — דיסק יעד שעדיין לא הושלם יישאר שבור ולא שמיש. לכבות בכל זאת?']);
  assert.match(notes()[1][1], /^הסוכן מסמן את היעדים כנכשלו ומדווח ליומן לפני שהוא נכבה$/);
  assert.equal(el('pmodal-alt').style.display, '');
  assert.equal(el('pmodal-alt-btn').textContent, 'כיבוי מיידי');
  assert.deepEqual(posts(), [], 'nothing is sent by opening the window');
});

test('"כבה בכל זאת" is a request through the agent, with confirm_local_copy', async () => {
  const {click, ok, posts, status} = setup();
  await click('poweroff-btn');
  await ok();
  assert.deepEqual(posts(), [{url: '/api/console/monitor/aa-bb-cc-dd-ee-ff/agent-power', method: 'POST',
    body: {action: 'poweroff', confirm: 'Cloner02', confirm_local_copy: true}}]);
  assert.match(status(), /^נשלחה פקודת כיבוי — ממתין שהמחשב ייכבה/);
});

test('reboot over a local copy says "אתחל בכל זאת" and goes through the agent too', async () => {
  const {el, click, ok, posts, notes} = setup();
  await click('reboot-btn');
  assert.equal(el('pmodal-title').textContent, 'לאתחל את Cloner02?');
  assert.equal(el('pmodal-ok').textContent, 'אתחל בכל זאת');
  assert.equal(el('pmodal-alt-btn').textContent, 'אתחול מיידי');
  assert.match(notes()[0][1], /— אתחול עכשיו — דיסק יעד שעדיין לא הושלם יישאר שבור ולא שמיש\. לאתחל בכל זאת\?$/);
  await ok();
  assert.deepEqual(posts().map((p) => [p.url, p.body.action, p.body.confirm_local_copy]),
    [['/api/console/monitor/aa-bb-cc-dd-ee-ff/agent-power', 'reboot', true]]);
});

test('the emergency button stays: "כיבוי מיידי" warns again, then sends immediate to …/power', async () => {
  const {el, click, ok, posts, notes} = setup();
  await click('poweroff-btn');
  el('pmodal-alt-btn').onclick();
  assert.equal(el('pmodal-title').textContent, 'כיבוי מיידי של Cloner02');
  assert.equal(el('pmodal-msg').textContent, 'עוקף את הסוכן. רק למחשב שלא עונה');
  assert.equal(el('pmodal-ok').textContent, 'כבה מיד, בלי סימון');
  assert.equal(el('pmodal-cancel').textContent, 'חזרה');
  assert.equal(notes()[0][0], 'danger-confirm');
  assert.match(notes()[0][1], /^זה כיבוי חשמלי\. הסוכן לא יסמן את היעדים/);
  assert.equal(el('pmodal-alt').style.display, 'none');
  // "חזרה" — בחזרה לאזהרה, לא סגירה
  el('pmodal-cancel').onclick();
  assert.equal(el('pmodal-ok').textContent, 'כבה בכל זאת');
  el('pmodal-alt-btn').onclick();
  await ok();
  assert.deepEqual(posts(), [{url: '/api/console/monitor/aa%3Abb%3Acc%3Add%3Aee%3Aff/power', method: 'POST',
    body: {action: 'poweroff', confirm: 'Cloner02', immediate: true}}]);
});

test('without a local copy the plain confirm is unchanged', async () => {
  const {el, click, ok, posts} = setup({copying: false});
  await click('poweroff-btn');
  assert.equal(el('pmodal-title').textContent, 'כיבוי — אישור');
  assert.notEqual(el('pmodal-danger').style.display, 'none');   // שומר: עובר גם על הקוד שלפני #2240
  assert.notEqual(el('pmodal-alt').style.display, '');
  await ok();
  assert.deepEqual(posts(), [{url: '/api/console/monitor/aa%3Abb%3Acc%3Add%3Aee%3Aff/power', method: 'POST',
    body: {action: 'poweroff', confirm: 'Cloner02'}}]);
});

test('the server is what decides: a 409 local_copy turns the plain confirm into the warning', async () => {
  const {el, click, ok, posts, answers} = setup({copying: false});
  await click('poweroff-btn');
  answers.push({ok: false, status: 409, json: async () => ({detail: 'באמצע שיכפול מקומי', local_copy: true})});
  await ok();
  assert.equal(el('pmodal-title').textContent, 'לכבות את Cloner02?');
  assert.equal(el('pmodal-ok').textContent, 'כבה בכל זאת');
  assert.equal(el('pmodal-error').textContent, '');
  await ok();
  assert.deepEqual(posts().map((p) => p.url), ['/api/console/monitor/aa%3Abb%3Acc%3Add%3Aee%3Aff/power',
    '/api/console/monitor/aa-bb-cc-dd-ee-ff/agent-power']);
});

test('through a secondary the warning reads its list and the request takes the relay', async () => {
  const {click, ok, posts} = setup({search: '?mac=aa:bb:cc:dd:ee:ff&name=Cloner02&node=n1'});
  await click('poweroff-btn');
  await ok();
  assert.deepEqual(posts().map((p) => p.url),
    ['/api/console/storage-nodes/n1/console/monitor/aa-bb-cc-dd-ee-ff/agent-power']);
});
