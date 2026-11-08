// #1462 — קצב, ETA וזמן שעבר בהעברה בין אתרים (branches.js; מוצג ברשימת המשימות), רק ממה שנמדד:
// קצב = הפרש בין שתי דגימות (bytes_sent, updated_at) של השרת; פחות משתיים =
// "מודד קצב…" ולא 0. בשלב האימות — פס נע וזמן שעבר מ-seconds_since_update
// (שעון השרת), ובלי השדה — בלי זמן. console.js + branches.js רצים ב-vm.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function setup() {
  const stub = () => ({value: '', innerHTML: '', textContent: '', className: '', dataset: {}, style: {removeProperty() {}},
    classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelectorAll: () => [], querySelector: stub, getElementById: stub,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  for (const f of ['progress.js', 'reorder.js', 'console.js', 'branches.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  }
  vm.runInContext('ME={username:"admin",role:"admin",server_name:"ראשי"};', ctx);
  const call = (fn, t) => vm.runInContext(`${fn}(${JSON.stringify(t)})`, ctx);
  // ‏#1781: אין עוד טבלת העברות בדף שרתי המשנה — השורה היא שורת המשימות בסקירה (homeNowRows).
  const taskRow = (t) => vm.runInContext(`HOME.transfers=[${JSON.stringify(t)}]; HOME.err={}; CAPTURE_TASKS=[]; homeNowRows(true)[0]`, ctx);
  return {call, taskRow};
}

const GB = 1024 ** 3;
const row = (over) => ({id: 't1', node_id: 'n', node_label: 'חיפה', image_id: 'img_7f3a91', image_name: 'Office',
  state: 'sending', direction: 'push', bytes_total: 10 * GB, bytes_sent: 0, error: null, started_by: 'admin',
  created_at: '2026-10-16T10:00:00+00:00', updated_at: '2026-10-16T10:00:00+00:00', seconds_since_update: 1, ...over});

test('one sample is not a rate: "מודד קצב…", never 0 MB/s', () => {
  const {call} = setup();
  assert.equal(call('transferPace', row({bytes_sent: 64 * 1024 ** 2})), 'מודד קצב…');
  // אותה דגימה שוב (אותו updated_at) — עדיין דגימה אחת.
  assert.equal(call('transferPace', row({bytes_sent: 64 * 1024 ** 2})), 'מודד קצב…');
});

test('two server samples give MB/s and an ETA from the bytes left', () => {
  const {call} = setup();
  call('transferPace', row({bytes_sent: 1e9, updated_at: '2026-10-16T10:00:00+00:00'}));
  const text = call('transferPace', row({bytes_sent: 2e9, updated_at: '2026-10-16T10:00:10+00:00'}));
  // ‏1e9 בייטים ב-10 שניות = 100MB/s; נותרו 10GiB-2e9 ≈ 8.74e9 → ~87 שניות → "1 דק'".
  assert.equal(text, "100.0 MB/s · נותרו כ-1 דק'");
});

test('samples less than two seconds apart are not a rate (updated_at has whole seconds)', () => {
  const {call} = setup();
  call('transferPace', row({bytes_sent: 1e9, updated_at: '2026-10-16T10:00:00+00:00'}));
  assert.equal(call('transferPace', row({bytes_sent: 1.1e9, updated_at: '2026-10-16T10:00:01+00:00'})), 'מודד קצב…');
});

test('no progress update for 30s or more is said in words, with no ETA', () => {
  const {call} = setup();
  call('transferPace', row({bytes_sent: 1e9, updated_at: '2026-10-16T10:00:00+00:00'}));
  const text = call('transferPace', row({bytes_sent: 2e9, updated_at: '2026-10-16T10:00:10+00:00', seconds_since_update: 45}));
  assert.equal(text, "אין התקדמות 45 שנ'");
});

test('verifying: a moving bar and the elapsed time from the server; without the field, no time', () => {
  const {call, taskRow} = setup();
  const v = row({state: 'verifying', bytes_sent: 10 * GB, seconds_since_update: 125});
  assert.equal(call('transferPace', v), "עברו 2 דק'");
  const cells = taskRow(v);
  assert.match(cells[2], /class="bar indeterminate"/);
  assert.match(cells[3], /עברו 2 דק(&#39;|')/);
  assert.equal(call('transferPace', row({state: 'verifying', seconds_since_update: null})), '');
  assert.doesNotMatch(taskRow(row({state: 'sending'}))[2], /indeterminate/);
});

test('pull is verified by the primary, push by the secondary', () => {
  const {call, taskRow} = setup();
  assert.match(taskRow(row({state: 'verifying', direction: 'pull'}))[3], /הראשי מאמת sha256/, 'the tasks list says who verifies');
  assert.match(taskRow(row({state: 'verifying', direction: 'push'}))[3], /שרת המשנה מאמת sha256/);
  assert.equal(call('transferStateLabel', row({state: 'verifying', direction: 'pull'}))[0], 'הראשי מאמת sha256');
  assert.equal(call('transferStateLabel', row({state: 'verifying', direction: 'push'}))[0], 'שרת המשנה מאמת sha256');
});
