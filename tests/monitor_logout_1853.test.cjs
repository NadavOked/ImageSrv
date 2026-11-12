// #1853 — showLogin() רק מסתיר את #app. iframe של צופה מוניטור שנשאר בדף המוניטור המשיך לרוץ מוסתר אחרי
// יציאה (כולל יציאה אוטומטית בחוסר פעילות): הוא החזיק את הגשר — מקלדת ועכבר למכונה — ואת המנעול (4409).
// הטסט מריץ את console.js האמיתי ובודק שהיציאה מוציאה את ה-iframe מה-DOM ומנקה את הבחירה.
// בקרה שלילית: על console.js שלפני #1853 ה-iframe נשאר בדף.
const {consoleFiles} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

test('logging out (showLogin) takes the monitor viewer iframe out of the page and clears the selection', () => {
  const frames = new Set();
  const frame = (src) => { const f = {src, remove() { frames.delete(f); }}; frames.add(f); return f; };
  const nodes = new Map();
  const node = (sel) => (nodes.has(sel) ? nodes.get(sel) : nodes.set(sel, stubNode()).get(sel));
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelector: node, getElementById: (id) => node(`#${id}`),
      querySelectorAll: (sel) => (sel === 'iframe.mon-frame' || sel === 'iframe' ? [...frames] : []),
      createElement: () => stubNode(), documentElement: {setAttribute() {}, classList: {add() {}, remove() {}}},
      body: {classList: {add() {}, remove() {}}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  for (const f of ['progress.js', ...consoleFiles()]) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  run('ME={username:"noc",role:"admin",idle_seconds:300,capabilities:{}}; current="monitor"; MON_SEL="aa:bb:cc:dd:ee:01";');
  frame('monitor.html?mac=aa%3Abb%3Acc%3Add%3Aee%3A01&name=Builder');
  run('showLogin()');
  assert.equal(frames.size, 0, 'the hidden monitor iframe kept running after logout — it holds the bridge and the 4409 lock');
  assert.equal(run('MON_SEL'), '', 'the next login must not reopen the previous viewer by itself');
  assert.equal(run('ME'), null);
});
