// מעבר ויזואלי (docs/design/visual-pass.md): תיקונים מהירים שנמדדו בדפדפן.
//   1. כשל רשת של fetch ("Failed to fetch") מתורגם בנקודה אחת (api) להודעה
//      בעברית; 4xx/5xx ממשיכים להציג את ה-detail של השרת. עיקרון 5: "השרת לא
//      ענה" ו"השרת ענה בשגיאה" הם שני מצבים, לא מקופלים לאחד.
//   2. toast בטוקנים (לא --ink/--on-ink שהפכו לאפור-בהיר עם טקסט לבן במצב כהה)
//      וצבוע "שגיאה" כשהטקסט בא מכשל API.
//   3. חץ העץ ב-RTL: מכווץ = ‹ (שמאלה), פתוח = ⌄. היה הפוך.
//   4. מגירה: עמודת ה-grid מוגבלת (minmax(0,1fr)) — טבלה לא מרחיבה את המגירה.
//   5. כפתור האישור במחיקת אימג' הוא danger, לא primary.
//   6. רענון תקופתי משמר פוקוס (F13).
const {consoleFiles} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');

function el(tag = 'div', extra = {}) {
  const classes = new Set();
  const node = {
    tagName: tag.toUpperCase(), children: [], parentElement: null, value: '', textContent: '', innerHTML: '',
    className: '', dataset: {}, style: {removeProperty() {}, setProperty() {}},
    classList: {add: (...c) => c.forEach((x) => classes.add(x)), remove: (...c) => c.forEach((x) => classes.delete(x)),
      toggle() {}, contains: (c) => classes.has(c)},
    addEventListener() {}, removeEventListener() {}, setAttribute() {}, removeAttribute() {},
    querySelector: () => null, querySelectorAll: () => [], focus() { node.ownerDoc.activeElement = node; },
    contains(n) { for (let x = n; x; x = x.parentElement) if (x === node) return true; return false; },
    setSelectionRange(s, e) { node.selectionStart = s; node.selectionEnd = e; },
    ...extra,
  };
  return node;
}

function setup(fetchImpl) {
  const nodes = new Map();
  const doc = {hidden: false, activeElement: null,
    getElementById: (id) => { if (!nodes.has(id)) { const n = el('div'); n.ownerDoc = doc; nodes.set(id, n); } return nodes.get(id); },
    querySelector: (sel) => doc.getElementById('q:' + sel), querySelectorAll: () => [], createElement: () => el(),
    documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}};
  const ctx = vm.createContext({console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, Array,
    encodeURIComponent, decodeURIComponent, document: doc,
    window: {addEventListener() {}, matchMedia: () => ({matches: false})},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {}, fetch: fetchImpl});
  vm.runInContext(read('progress.js'), ctx);
  for (const f of consoleFiles()) vm.runInContext(read(f), ctx);
  return {ctx, run: (s) => vm.runInContext(s, ctx), doc};
}

test('fetch network failure becomes a Hebrew message, not the raw browser text', async () => {
  const {run} = setup(async () => { throw new TypeError('Failed to fetch'); });
  const err = await run('post("/net/dhcp", {}).then(() => null, (e) => ({m: e.message, network: e.network, status: e.status}))');
  assert.match(err.m, /השרת לא עונה/);
  assert.doesNotMatch(err.m, /Failed to fetch/);
  assert.equal(err.network, true);
  assert.equal(err.status, undefined, 'no HTTP status: the server never answered');
});

test('AbortError and NetworkError are also "server did not answer"', async () => {
  for (const name of ['AbortError', 'NetworkError']) {
    const {run} = setup(async () => { const e = new Error('x'); e.name = name; throw e; });
    const err = await run('get_ = (p) => api(p); get_("/a").then(() => null, (e) => ({m: e.message, network: e.network}))');
    assert.equal(err.network, true, name);
    assert.match(err.m, /השרת לא עונה/);
  }
});

test('5xx with a detail shows the server detail, and is not a network failure', async () => {
  const {run} = setup(async () => ({status: 503, ok: false, headers: {get: () => null},
    json: async () => ({detail: 'dnsmasq לא הופעל מחדש'})}));
  const err = await run('post("/net/dhcp", {}).then(() => null, (e) => ({m: e.message, network: e.network, status: e.status}))');
  assert.equal(err.m, 'dnsmasq לא הופעל מחדש');
  assert.equal(err.status, 503);
  assert.equal(err.network, undefined, '"answered with an error" must not collapse into "did not answer"');
  assert.doesNotMatch(err.m, /השרת לא עונה/);
});

test('a non-fetch programming error is not translated into a network message', async () => {
  const {run} = setup(async () => { throw new RangeError('boom'); });
  const err = await run('post("/x").then(() => null, (e) => e.message)');
  assert.equal(err, 'boom');
});

test('toast is marked err only for text that came from a recent API failure', async () => {
  const {run, doc} = setup(async () => { throw new TypeError('Failed to fetch'); });
  const toastEl = doc.getElementById('toast');
  run('toast("הכל טוב")');
  assert.equal(toastEl.classList.contains('err'), false);
  await run('post("/x").catch((e) => toast("טעינה נכשלה: " + e.message))');
  assert.equal(toastEl.classList.contains('err'), true);
  assert.match(toastEl.textContent, /השרת לא עונה/);
  run('toast("נשמר.", 2600, "ok")');
  assert.equal(toastEl.classList.contains('ok'), true);
  assert.equal(toastEl.classList.contains('err'), false, 'kinds do not stack');
});

test('toast CSS uses surface tokens (dark-mode safe) with danger/success accents', () => {
  const css = read('console.css');
  const rule = css.match(/#toast\{position:fixed;[^}]*\}/)[0];
  assert.match(rule, /background:var\(--surface\)/);
  assert.match(rule, /color:var\(--ink\)/);
  assert.doesNotMatch(rule, /on-ink/, 'white-on-light-grey toast in dark mode (the reported bug)');
  assert.match(css, /#toast\.err\{[^}]*var\(--danger\)/);
  assert.match(css, /#toast\.ok\{[^}]*var\(--led-ok\)/);
});

test('RTL tree arrow: collapsed points left, expanded points down', () => {
  const css = read('console.css');
  assert.match(css, /\.tree-arrow::before\{[^}]*transform:rotate\(135deg\)\}/);
  assert.match(css, /\.tree-arrow\[data-open="true"\]::before\{transform:rotate\(45deg\)\}/);
});

test('drawer grid column is bounded so a wide table cannot widen the drawer', () => {
  const css = read('console.css');
  assert.match(css, /\.page\.drw\{grid-template-columns:minmax\(0,1fr\)\}/);
  assert.match(css, /\.page\.drw table\.dg\{display:block;overflow-x:auto\}/);
});

test('image delete is a danger sheet with no typed field (#1270), and modals reset the confirm button to primary', () => {
  const {run, doc} = setup(async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}));
  run('ME={username:"a",role:"admin",capabilities:{}}; IMAGES=[{id:"i1",name:"Img"}]; findImage=(id)=>IMAGES.find(i=>i.id===id); openModalFrame=()=>{};');
  run('globalThis.cfg=null; sheet=(o)=>{ globalThis.cfg=o; }; deleteImage("i1")');
  assert.equal(run('cfg.danger'), true); assert.equal(run('cfg.verify'), undefined); assert.equal(run('cfg.fields'), undefined);
  run('openModalContent("x","y","ok",closeModal,true)');
  assert.equal(doc.getElementById('modalOk').className, 'btn danger');
  run('openModalContent("x","y")');
  assert.equal(doc.getElementById('modalOk').className, 'btn primary');
});

test('periodic re-render keeps focus, selection and typed text (F13)', () => {
  const {run, doc} = setup(async () => ({}));
  const build = (typed) => {
    const host = doc.getElementById('content'); host.children = [];
    const page = el('div'); page.parentElement = host; host.children.push(page);
    const input = el('input', {type: 'text', value: typed}); input.ownerDoc = doc; input.parentElement = page; page.children.push(input);
    return input;
  };
  const before = build('Lin'); before.focus(); before.selectionStart = 1; before.selectionEnd = 2;
  const loc = run('focusLocator')();
  assert.ok(loc, 'focused element inside #content is located');
  const after = build('');            // הדף נבנה מחדש: אלמנט חדש, ערך ריק, בלי פוקוס
  doc.activeElement = null;
  run('restoreFocus')(loc);
  assert.equal(doc.activeElement, after);
  assert.equal(after.value, 'Lin');
  assert.deepEqual([after.selectionStart, after.selectionEnd], [1, 2]);
});

test('focus outside #content is left alone', () => {
  const {run, doc} = setup(async () => ({}));
  const outside = el('input'); outside.ownerDoc = doc; doc.activeElement = outside;
  assert.equal(run('focusLocator')(), null);
  run('restoreFocus')(null);   // לא זורק
});
