// #554 — חיפוש גלובלי בשורה העליונה. console.js ב-vm מול DOM מזערי; הנתונים מוזרקים לרשימות שהקונסולה כבר טוענת.
// נבדק: קבוצות לפי תפקיד (deploy לא רואה מחשבים/משתמשים/שרתים), "אין תוצאות", שורת הסינון הראשונה,
// מקלדת (חצים/Enter/Esc), ו"לא נקרא" כשרשימה נכשלה (עיקרון 5).
const {consoleFiles} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function setup() {
  const doc = {listeners: {}, nodes: new Map()};
  const mkEl = (id) => ({id, innerHTML: '', value: '', hidden: false, attrs: {}, listeners: {}, style: {}, dataset: {},
    classList: {add() {}, remove() {}, toggle() {}, contains: () => false},
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] ?? null; },
    addEventListener(t, fn) { this.listeners[t] = fn; }, removeAttribute() {}, hasAttribute: () => false,
    querySelector: () => null, querySelectorAll: () => [], closest: () => null, focus() {}});
  const input = mkEl('globalSearch'), panel = mkEl('gsPanel');
  doc.nodes.set('globalSearch', input); doc.nodes.set('gsPanel', panel);
  doc.getElementById = (id) => doc.nodes.get(id) || null;
  doc.createElement = () => mkEl('');
  doc.querySelector = () => mkEl('x'); doc.querySelectorAll = () => [];
  doc.documentElement = {getAttribute: () => 'rtl', setAttribute() {}};
  doc.addEventListener = (t, fn) => { doc.listeners[t] = fn; };
  doc.body = {appendChild() {}};
  const calls = [];
  const ctx = vm.createContext({console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, String, Array, Object,
    encodeURIComponent, decodeURIComponent, document: doc,
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), innerWidth: 1024, innerHeight: 768, open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})})});
  for (const f of ['progress.js', ...consoleFiles()]) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = (s) => JSON.parse(JSON.stringify(vm.runInContext(s, ctx)) ?? 'null');
  const call = (s) => vm.runInContext(s, ctx);
  ctx.__calls = calls;
  return {run, call, input, panel, calls, ctx};
}

const DATA = `
  IMAGES = [{id: 'win11-office', name: 'Office Win11'}, {id: 'ubuntu', name: 'Ubuntu'}];
  GROUPS = [{id: 'g-b', role: 'build'}, {id: 'g-c', role: 'cloner'}, {id: 'g-s', role: 'classroom'}];
  MACHINES = [{mac: 'aa:01', name: 'win-build', group: 'g-b'}, {mac: 'aa:02', name: 'win-cloner', group: 'g-c'}, {mac: 'aa:03', suffix: 'win-student', group: 'g-s'}];
  USERS = [{username: 'winston', role: 'admin'}, {username: 'bob', role: 'deploy'}];
  BRANCH_NODES = [{id: 'n1', name: 'win-haifa', url: 'https://h'}];
  ME = {username: 'x', role: ROLE, capabilities: {interbranch_transfer: true, enroll_secondary: true}};
`;
const load = (t, role) => t.run(`var ROLE='${role}';` + DATA);
const titles = (t, q) => t.run(`globalSearchGroups(${JSON.stringify(q)})`).map((g) => g.title);

test('admin: images, build/cloner machines (not classroom), users, secondaries and pages — grouped', () => {
  const t = setup(); load(t, 'admin');
  const groups = t.run('globalSearchGroups("win")');
  assert.deepEqual(groups.map((g) => g.title), ["אימג'ים", 'מחשבים', 'משתמשים', 'שרתי משנה']);
  const machines = groups.find((g) => g.title === 'מחשבים').rows.map((r) => r.label);
  assert.deepEqual(machines, ['win-build', 'win-cloner'], 'a classroom machine is not shown in v1');
  assert.ok(titles(t, 'סקירה').includes('דפים'), 'pages by name');
});

test('deploy: sees images and allowed pages only — never machines, users or secondaries', () => {
  const t = setup(); load(t, 'deploy');
  const g = titles(t, 'win');
  assert.deepEqual(g, ["אימג'ים"]);
  assert.deepEqual(titles(t, 'bob'), [], 'users hidden from deploy');
  const pageRows = t.run('globalSearchGroups("הגדרות")');
  assert.deepEqual(pageRows, [], 'settings page is not allowed for deploy');
  assert.ok(titles(t, 'אימג').includes('דפים'), 'images page is allowed');
});

test('panel: first row is the page filter; empty result shows "אין תוצאות"; accessibility roles', () => {
  const t = setup(); load(t, 'admin');
  t.input.value = 'zzzz'; t.run('gsInput("zzzz")');
  assert.match(t.panel.innerHTML, /data-gs="0"[^>]*>.*סנן בעמוד הנוכחי: zzzz/s);
  assert.match(t.panel.innerHTML, /אין תוצאות/);
  assert.equal(t.panel.hidden, false);
  assert.equal(t.input.attrs['aria-expanded'], 'true');
  t.input.value = 'ubu'; t.run('gsInput("ubu")');
  assert.doesNotMatch(t.panel.innerHTML, /אין תוצאות/);
  assert.match(t.panel.innerHTML, /role="option"[^>]*>.*Ubuntu/s);
  t.input.value = ''; t.run('gsInput("")');
  assert.equal(t.panel.hidden, true);
});

test('keyboard: arrows move, Enter opens the item and clears the box, Esc closes', () => {
  const t = setup(); load(t, 'admin');
  t.run('var opened=[]; openImageDetail=(id)=>opened.push(id);');
  t.input.value = 'ubuntu'; t.run('gsInput("ubuntu")');
  const key = (k) => { const e = {key: k, prevented: false, preventDefault() { this.prevented = true; }}; t.call('gsKeydown')(e); return e; };
  assert.equal(t.run('GS.active'), 0);
  assert.equal(key('ArrowDown').prevented, true);
  assert.equal(t.run('GS.active'), 1);
  assert.equal(key('ArrowDown').prevented, true);
  assert.equal(t.run('GS.active'), 0, 'wraps around');
  key('ArrowUp');
  assert.equal(t.run('GS.active'), t.run('GS.items.length') - 1);
  t.run('GS.active = 1');
  key('Enter');
  assert.deepEqual(t.run('opened'), ['ubuntu']);
  assert.equal(t.input.value, '');
  assert.equal(t.panel.hidden, true);
  t.input.value = 'ubuntu'; t.run('gsInput("ubuntu")');
  key('Escape');
  assert.equal(t.panel.hidden, true);
});

test('Enter on the first row runs the old page filter', () => {
  const t = setup(); load(t, 'admin');
  t.run('var filtered=[]; doGlobalSearch=(v)=>filtered.push(v);');
  t.input.value = 'ubuntu'; t.run('gsInput("ubuntu")');
  t.call('gsKeydown')({key: 'Enter', preventDefault() {}});
  assert.deepEqual(t.run('filtered'), ['ubuntu']);
});

test('a list that failed to load is named, not shown as empty (principle 5)', async () => {
  const t = setup(); load(t, 'admin');
  t.run('USERS = null; api = async () => { throw new Error("boom"); };');
  t.input.value = 'zzz'; t.run('gsInput("zzz")');
  await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
  assert.match(t.panel.innerHTML, /לא נקרא: .*משתמשים/);
  assert.match(t.panel.innerHTML, /חלקיות/);
});
