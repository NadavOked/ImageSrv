// #829 S1–S6 — שוברי-השימוש בקונסולה החדשה. כל בדיקה נצמדת למדידה שבדוח
// (logs/2026-09-14/224824-front-design-audit.md): selector, מאפיין, שורת CSS.
// בלי דפדפן ובלי JSDOM: console.js רץ ב-vm מול DOM מזערי שמדמה רק את מה
// שהעץ צריך (classList/attributes/parentElement), והשאר נבדק כטקסט.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');

/* DOM מזערי: אלמנט עם classList, attributes, style, ילדים והורה. */
function el(tag, attrs = {}, children = []) {
  const e = {
    tag, attrs: {...attrs}, children, parentElement: null, innerHTML: '', textContent: '', value: '',
    style: {removeProperty(k) { delete this[k]; }},
    listeners: {},
    get classList() {
      const self = this;
      const list = () => (self.attrs.class || '').split(/\s+/).filter(Boolean);
      return {
        add(c) { if (!list().includes(c)) self.attrs.class = [...list(), c].join(' '); },
        remove(c) { self.attrs.class = list().filter((x) => x !== c).join(' '); },
        toggle(c, force) { const has = list().includes(c); const want = force === undefined ? !has : !!force; if (want && !has) this.add(c); if (!want && has) this.remove(c); return want; },
        contains(c) { return list().includes(c); },
      };
    },
    get id() { return this.attrs.id || ''; },
    get tagName() { return String(this.tag).toUpperCase(); },
    get nextElementSibling() { const p = this.parentElement; if (!p) return null; const i = p.children.indexOf(this); return i >= 0 && i < p.children.length - 1 ? p.children[i + 1] : null; },
    get outerHTML() { return this.tag === 'svg' ? `<svg class="ui-icon" data-k="${this.attrs['data-k'] || ''}"></svg>` : ''; },
    getBoundingClientRect() { return {top: 100, left: 1200, height: 40, width: 40}; }, offsetHeight: 30,
    matches(sel) { return sel === ':hover' || sel === ':focus-visible' ? false : matches(this, sel); },
    get dataset() { const d = {}; for (const [k, v] of Object.entries(this.attrs)) if (k.startsWith('data-')) d[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v; return d; },
    hasAttribute(k) { return k in this.attrs; },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    removeAttribute(k) { delete this.attrs[k]; },
    get previousElementSibling() { const p = this.parentElement; if (!p) return null; const i = p.children.indexOf(this); return i > 0 ? p.children[i - 1] : null; },
    querySelector(sel) { return all(this).find((x) => matches(x, sel)) || null; },
    querySelectorAll(sel) { return all(this).filter((x) => matches(x, sel)); },
    closest(sel) { for (let n = this; n; n = n.parentElement) if (matches(n, sel)) return n; return null; },
    addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); },
    removeEventListener() {}, focus() { focused = this; }, click() {},
    insertAdjacentHTML(_p, html) { this.innerHTML += html; },
  };
  for (const c of children) c.parentElement = e;
  return e;
}
let focused = null;
function all(e) { const out = []; (function walk(n) { for (const c of n.children) { out.push(c); walk(c); } })(e); return out; }
/* selector מינימלי: רשימת ".a.b[attr=v]#id" מופרדת בפסיקים, בלי צאצאים. */
function matches(e, selector) {
  return selector.split(',').some((part) => {
    part = part.trim();
    const tag = (part.match(/^([a-z]+)/) || [])[1];
    if (tag && e.tag !== tag) return false;
    const classes = [...part.matchAll(/\.([\w-]+)/g)].map((m) => m[1]);
    const ids = [...part.matchAll(/#([\w-]+)/g)].map((m) => m[1]);
    const attrs = [...part.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)];
    return classes.every((c) => e.classList.contains(c)) && ids.every((i) => e.id === i)
      && attrs.every(([, k, v]) => v === undefined ? e.hasAttribute(k) : e.getAttribute(k) === v);
  });
}

/* עץ בצורת index.html: שרת → [סקירה, הפצה, מלאי → [אימג׳ים, מחשבים], תשתית(סגור) → [בריאות, מוניטור]]. */
function buildTree() {
  const node = (page, extra = {}) => el('div', {class: 'inventory-node', 'data-page': page, tabindex: '0', ...extra});
  const infra = node(undefined, {class: 'inventory-node'});
  delete infra.attrs['data-page'];
  const infraChildren = el('div', {id: 'infraTA', class: 'inventory-children', hidden: ''}, [node('health'), node('monitor')]);
  const invChildren = el('div', {id: 'invTA', class: 'inventory-children'}, [node('images'), node('machines')]);
  const inv = el('div', {class: 'inventory-node', tabindex: '0'}, [el('span', {class: 'tree-arrow', 'data-open': 'true'})]);
  const srvChildren = el('div', {id: 'srvTA', class: 'inventory-children'}, [node('home'), node('deploy'), inv, invChildren, infra, infraChildren]);
  const srv = el('div', {class: 'inventory-node server-node active', tabindex: '0'}, [el('span', {class: 'tree-arrow', 'data-open': 'true'})]);
  const tree = el('div', {class: 'tree'}, [srv, srvChildren]);
  const toggle = el('button', {class: 'icon-btn nav-toggle', 'aria-expanded': 'false'});
  const sidebar = el('aside', {class: 'sidebar', id: 'sidebar'}, [tree]);
  return el('div', {id: 'app'}, [el('header', {class: 'topbar'}, [toggle]), sidebar, el('div', {class: 'sidebar-backdrop', id: 'sidebarBackdrop'})]);
}

function setup(build = buildTree) {
  focused = null;
  const tree = build();
  const byId = new Map();
  const stub = (id) => { if (!byId.has(id)) byId.set(id, el('div', {id})); return byId.get(id); };
  const document = {
    hidden: false,
    querySelector: (sel) => tree.querySelector(sel) || stub('q:' + sel),
    querySelectorAll: (sel) => tree.querySelectorAll(sel),
    getElementById: (id) => tree.querySelector('#' + id) || stub(id),
    createElement: () => { let text = ''; return {set textContent(v) { text = String(v); }, get innerHTML() { return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }}; },
    documentElement: {setAttribute() {}}, removeEventListener() {},
    listeners: {}, addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); },
    body: el('body'),
  };
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, encodeURIComponent, decodeURIComponent,
    document, window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}, innerWidth: 1280, innerHeight: 800},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(read('progress.js'), ctx);
  vm.runInContext(read('console.js'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",idle_seconds:300,capabilities:{}}; toast=()=>{};');
  return {run, tree, document};
}

// ---- S1: הדף הנוכחי מודגש בעץ ---------------------------------------------

test('S1: every navigating tree node in index.html carries data-page', () => {
  const html = read('index.html');
  const lines = html.split('\n').filter((l) => l.includes('class="inventory-node') && /selectPageById\('([a-z]+)'\)/.test(l));
  assert.ok(lines.length >= 13, 'expected the tree nodes, got ' + lines.length);   // ‏#954 גל 8: nic+netdeploy → network (אחד במקום שניים)
  for (const line of lines) {
    const page = line.match(/selectPageById\('([a-z]+)'\)/)[1];
    assert.match(line, new RegExp(`<div class="inventory-node[^"]*" data-page="${page}"`), `node for '${page}' lacks data-page on the .inventory-node div`);
  }
});

test('S1: selectPageById highlights the .inventory-node of the page, and only it', () => {
  const {run, tree} = setup();
  run('selectPageById("deploy")');
  const active = tree.querySelectorAll('.inventory-node.active');
  assert.deepEqual(active.map((n) => n.attrs['data-page']), ['deploy'], 'exactly the deploy node is .active');
  assert.equal(active[0].getAttribute('aria-current'), 'page');
  run('selectPageById("images")');
  assert.deepEqual(tree.querySelectorAll('.inventory-node.active').map((n) => n.attrs['data-page']), ['images']);
  assert.deepEqual(tree.querySelectorAll('.inventory-node[aria-current]').map((n) => n.attrs['data-page']), ['images'], 'aria-current moves with the selection');
});

test('S1: selecting a page inside a collapsed group opens the group so the highlight is visible', () => {
  const {run, tree} = setup();
  const infra = tree.querySelector('#infraTA');
  assert.ok(infra.hasAttribute('hidden'), 'precondition: infra group starts collapsed');
  run('selectPageById("health")');
  assert.ok(!infra.hasAttribute('hidden'), 'infra group was expanded');
  assert.equal(infra.previousElementSibling.querySelector('.tree-arrow'), null, 'test tree: owner without arrow tolerated');
  assert.deepEqual(tree.querySelectorAll('.inventory-node.active').map((n) => n.attrs['data-page']), ['health']);
});

// ---- S2: ניווט במסך צר — המבורגר שפותח את הסיידבר כשכבה --------------------

/* כל הכללים בתוך בלוקי @media(max-width:740px) של console.css, כמחרוזת אחת. */
function narrowCss() {
  const css = read('console.css');
  return [...css.matchAll(/@media\s*\(max-width:\s*740px\)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}/g)].map((m) => m[1]).join('\n');
}

test('S2 (#1300 rail): the topbar ☰ controls the sidebar — expanded = the full tree is showing; the aside is the main navigation landmark', () => {
  const html = read('index.html');
  assert.match(html, /<button class="icon-btn nav-toggle" type="button" aria-label="כיווץ לפס אייקונים" title="כיווץ לפס אייקונים" aria-controls="sidebar" aria-expanded="true" onclick="navButton\(\)">/);
  assert.match(html, /<aside class="sidebar" id="sidebar" aria-label="ניווט ראשי">\n  <div class="nav-full" id="navFull">\n  <div class="tree" role="tree"/);
  assert.match(html, /<div class="rail" id="rail" aria-label="ניווט מצומצם"><\/div>/);
  assert.match(html, /<div id="app" class="app hidden" data-nav="full">/);
  assert.match(html, /<div class="nav-tip" id="navTip" role="tooltip"><\/div>/); assert.match(html, /<div class="nav-fly" id="navFly" role="menu"><\/div>/);
  assert.doesNotMatch(html, /id="navCollapse"|sidebar-head/, 'one control — ☰ in the header (rail.html), not a second button in the panel');
  assert.match(read('console.css'), /\.topbar \.nav-toggle\{display:inline-flex/);
});
test('S2 (#1300 rail): on a narrow screen toggleSidebar is the drawer — aria-expanded follows it; navigation and Escape close it', () => {
  const {run, tree, document} = setup();
  run('window.matchMedia=()=>({matches:true})');
  const side = tree.querySelector('#sidebar'), btn = tree.querySelector('.nav-toggle');
  run('toggleSidebar()');
  assert.ok(side.classList.contains('open'));
  assert.equal(btn.getAttribute('aria-expanded'), 'true'); assert.equal(btn.getAttribute('aria-label'), 'סגירת העץ המלא');
  run('selectPageById("deploy")');
  assert.ok(!side.classList.contains('open'), 'navigating from the drawer closes it');
  assert.equal(btn.getAttribute('aria-expanded'), 'false'); assert.equal(btn.getAttribute('aria-label'), 'פתיחת העץ המלא');
  run('init()');
  run('toggleSidebar()');
  for (const fn of document.listeners.keydown) fn({key: 'Escape', target: side});
  assert.ok(!side.classList.contains('open'), 'Escape closes the drawer');
});
test('#1300 rail: on a wide screen ☰ toggles full tree ↔ icon rail, and the choice is remembered (localStorage, try/catch)', () => {
  const {run, tree, document} = setup();
  run('globalThis.__st={}; localStorage={getItem:(k)=>__st[k]??null,setItem:(k,v)=>{__st[k]=String(v);}}');
  const app = document.getElementById('app'), top = tree.querySelector('.nav-toggle');
  run('restoreNav()');
  assert.equal(app.getAttribute('data-nav'), 'full', 'nothing stored = the full tree');
  run('navButton()');
  assert.equal(app.getAttribute('data-nav'), 'rail', 'wide: the click makes it a rail');
  assert.equal(top.getAttribute('aria-expanded'), 'false'); assert.equal(top.getAttribute('aria-label'), 'הצגת העץ המלא');
  assert.equal(run('__st["imagesrv.nav"]'), 'rail', 'remembered');
  run('navButton()');
  assert.equal(app.getAttribute('data-nav'), 'full', 'a second click brings the tree back');
  assert.equal(top.getAttribute('aria-expanded'), 'true'); assert.equal(top.getAttribute('aria-label'), 'כיווץ לפס אייקונים');
  assert.equal(run('__st["imagesrv.nav"]'), 'full');
  run('__st["imagesrv.nav"]="rail"; NAV_PREF="full"; restoreNav()');
  assert.equal(app.getAttribute('data-nav'), 'rail', 'a stored rail is restored after a reload');
  run('localStorage={getItem(){throw new Error("blocked")},setItem(){throw new Error("blocked")}}; NAV_PREF="rail"; restoreNav()');
  assert.equal(app.getAttribute('data-nav'), 'full', 'unreadable storage = the full tree, no crash');
  run('navButton()'); assert.equal(app.getAttribute('data-nav'), 'rail', 'and toggling still works without storage');
  assert.doesNotMatch(read('console.js'), /nav-collapsed|setNavCollapsed|NAV_COLLAPSE_KEY|restoreNavCollapsed/, 'the old hide-the-tree collapse is gone');
});
test('#1300 rail: up to 900px the rail is the default whatever is stored; ☰ opens the full tree as a drawer and does not change the stored choice; Escape never touches the wide choice', () => {
  const {run, tree, document} = setup();
  run('globalThis.__st={"imagesrv.nav":"full"}; localStorage={getItem:(k)=>__st[k]??null,setItem:(k,v)=>{__st[k]=String(v);}}');
  const side = tree.querySelector('#sidebar'), app = document.getElementById('app'), back = document.getElementById('sidebarBackdrop');
  run('window.matchMedia=()=>({matches:true}); restoreNav()');
  assert.equal(app.getAttribute('data-nav'), 'rail'); assert.ok(app.classList.contains('narrow'));
  run('navButton()');
  assert.ok(side.classList.contains('open') && back.classList.contains('open'), 'narrow: ☰ opens the drawer and the scrim');
  assert.equal(app.getAttribute('data-nav'), 'rail', 'the rail stays under the drawer');
  assert.equal(run('__st["imagesrv.nav"]'), 'full', 'the drawer is not a stored choice');
  run('closeSidebarFromScrim()'); assert.ok(!side.classList.contains('open'), 'the scrim closes it');
  run('window.matchMedia=()=>({matches:false}); applyNavMode(false)');
  assert.equal(app.getAttribute('data-nav'), 'full', 'back to wide: the stored choice again'); assert.ok(!app.classList.contains('narrow'));
  run('init()');
  for (const fn of document.listeners.keydown || []) fn({key: 'Escape', target: side});
  assert.equal(app.getAttribute('data-nav'), 'full', 'Escape does not collapse the tree');
});
test('#1300 rail: the width is one animated grid column (264↔56, reversible), the layers cross-fade, the narrow drawer slides; reduced motion is honoured', () => {
  const css = read('console.css');
  assert.match(css, /\.main\{grid-template-columns:var\(--inventory-width\) minmax\(0,1fr\);transition:grid-template-columns var\(--dur-sheet\) var\(--ease\)\}/);
  assert.match(css, /\.app\[data-nav="rail"\] \.main\{grid-template-columns:var\(--rail-w\) minmax\(0,1fr\)\}/);
  assert.match(css, /:root\{--rail-w:56px;--tile:40px\}/);
  assert.match(css, /\.app\[data-nav="rail"\] \.sidebar \.nav-full\{opacity:0;visibility:hidden/, 'the hidden layer leaves the Tab order (visibility)');
  assert.match(css, /\.app\[data-nav="rail"\] \.sidebar \.rail\{opacity:1;visibility:visible/);
  assert.match(css, /\.app\.narrow \.sidebar \.nav-full\{position:fixed;inset-block:48px 0;[^}]*visibility:hidden;transform:translateX\(105%\)/);
  assert.match(css, /\.app\.narrow \.sidebar\.open \.nav-full\{visibility:visible;transform:none/);
  assert.match(css, /@media \(prefers-reduced-motion:reduce\)\{\s*\.main\{transition:none\}/);
  assert.match(css, /@media \(prefers-reduced-transparency:reduce\)/);
  assert.doesNotMatch(css, /nav-collapsed/);
});
test('audit: the login footer draws a separator only between values that exist (no orphan dots before sign-in)', () => {
  const {run, document} = setup();
  assert.match(read('index.html'), /id="login-foot-sep1" hidden/);
  const sep1 = document.querySelector('#login-foot-sep1'), sep2 = document.querySelector('#login-foot-sep2');
  run('ME=null; globalThis.location={hostname:"srv"}; paintLoginFoot()');
  assert.equal(sep1.hidden, true, 'host only: no separator');
  assert.equal(sep2.hidden, true);
  run('ME={server_name:"srv",version:"v1.2.3",tls:{fingerprint_sha256:"ab".repeat(32)}}; paintLoginFoot()');
  assert.equal(sep1.hidden, false, 'host and version: separated');
  assert.equal(sep2.hidden, false, 'version and TLS: separated');
});

test('audit: the topbar search filters table.dg rows too, never counts a group header as a hit, and hides empty groups', () => {
  const {run, document} = setup();
  const row = (text, group = false) => ({textContent: text, style: {}, classList: {contains: (c) => group && c === 'group'}});
  const rows = [row('מחשב בניית אימג׳ים', true), row('build-01 aa:bb'), row('מחשבי שיכפול', true), row('01 aa:cc'), row('02 aa:dd')];
  document.querySelectorAll = (sel) => { assert.match(sel, /table\.dg tbody tr/, 'the selector covers the rebuilt pages\' tables'); return rows; };
  run('doGlobalSearch("build")');
  assert.deepEqual(rows.map((r) => r.style.display), ['', '', 'none', 'none', 'none'], 'only the matching row and its own group header remain');
  run('doGlobalSearch("")');
  assert.deepEqual(rows.map((r) => r.style.display), ['', '', '', '', ''], 'clearing restores everything');
  run('doGlobalSearch("aa")');
  assert.equal(rows.filter((r) => r.style.display === 'none').length, 0, 'a term in every data row keeps both groups');
});

test('audit: the status bar dots follow /health — green only on "ok", grey when nothing was measured', () => {
  const {run, document} = setup();
  const html = read('index.html');
  for (const n of ['API', 'PXE', 'Multicast']) assert.match(html, new RegExp(`class="statusbar-item svc-unk" data-svc="${n}"`), n + ' starts grey, not green');
  assert.doesNotMatch(html, /כל השירותים פעילים/, 'the static green claim is gone from the markup');
  // נדב 18/10: הקופסה בתחתית העץ יצאה — מצב השרת הוא הנקודה ליד שמו (tree_server_dot.test.cjs)
  const dot = (n) => document.querySelector(`[data-svc="${n}"]`);
  run('HEALTH=null; healthError=""; paintServiceStatus()');
  assert.ok(dot('Multicast').classList.contains('svc-unk'), 'no data: grey');
  run('HEALTH=[{id:"udp_sender",state:"bad",detail:"לא נמצא"},{id:"tftp_port",state:"ok"},{id:"boot_files",state:"warn"},{id:"server",state:"ok"}]; paintServiceStatus()');
  assert.ok(dot('Multicast').classList.contains('svc-err'), 'a failing udp_sender makes Multicast red');
  assert.ok(dot('PXE').classList.contains('warn') === false && dot('PXE').classList.contains('svc-warn'), 'PXE = the worst of tftp_port + boot_files; the class is prefixed so the global .ok/.err message rules do not apply');
  assert.ok(dot('API').classList.contains('svc-ok'));
});

// ---- S3: שורת הסטטוס אינה מכוסה ע"י ה-dock הקבוע --------------------------

/* הערך האחרון של מאפיין עבור סלקטור, מחוץ ל-@media (הסדר ב-CSS מכריע). */
function lastDecl(css, selector, prop) {
  const noMedia = css.replace(/@media[^{]*\{(?:[^{}]*\{[^{}]*\})*\s*\}/g, '');
  const esc = selector.replace(/[.#]/g, (c) => '\\' + c);
  let value = null;
  for (const m of noMedia.matchAll(new RegExp('(?:^|[}\\s,])' + esc + '\\{([^}]*)\\}', 'g'))) {
    const d = m[1].match(new RegExp('(?:^|;)\\s*' + prop + ':\\s*([^;]+)'));
    if (d) value = d[1].trim();
  }
  return value;
}
const px = (v) => { assert.match(String(v), /^(0|-?\d+(\.\d+)?px)$/, 'px value, got ' + v); return parseFloat(v); };

test('S3 (stage 1): tasks and status are ONE bar — the status items live inside the dock, the dock sits on the bottom edge, the task panel on the dock', () => {
  const css = read('console.css');
  assert.equal(lastDecl(css, '.task-dock', 'position'), 'fixed');
  const html = read('index.html');
  const dock = html.slice(html.indexOf('<div class="task-dock">'), html.indexOf('<div class="user-menu"'));
  assert.match(dock, /class="task-dock-head"[\s\S]*class="task-summary"[\s\S]*<div class="statusbar">[\s\S]*data-svc="API"[\s\S]*id="serverVersion"/, 'tasks, summary and the status line are in one dock');
  assert.doesNotMatch(html.replace(dock, ''), /<div class="statusbar">/, 'there is no second status bar');
  assert.equal(px(lastDecl(css, '.task-dock', 'bottom')), 0, 'the dock rests on the bottom edge (no 22px bar under it any more)');
  assert.equal(px(lastDecl(css, '.task-panel', 'bottom')), px(lastDecl(css, '.task-dock', 'height')), 'task panel rests on the dock');
});

test('audit: the drawer ends above the dock, so its action bar is never covered', () => {
  const css = read('console.css');
  const dockH = px(lastDecl(css, '.task-dock', 'height'));
  assert.equal(px(lastDecl(css, '.drawer', 'bottom')), dockH, `the drawer clears the ${dockH}px dock`);
  assert.match(narrowCss(), /\.drawer\{bottom:34px\}/, 'narrow: the same 34px dock');
  assert.match(css, /\.drawer \.list-row \.status\{font-size:var\(--t-xs\)/, 'a status label in a drawer list is not larger than its 11px title');
});

test('S3: at <=740px the status items fold away and the dock stays on the bottom edge', () => {
  const narrow = narrowCss();
  assert.match(narrow, /\.statusbar\{display:none\}/, 'the status items are hidden when narrow');
  assert.match(narrow, /\.task-dock\{bottom:0\}/);
  assert.match(narrow, /\.task-panel\{bottom:34px\}/);
});

// ---- S4: ניווט מקלדת — role/tabindex/aria בפלט הרינדור, ומקשים שמפעילים ----

test('S4: tabs render as <button role="tab"> inside role="tablist" with aria-selected and roving tabindex', () => {
  const {run} = setup();
  // ‏#954: העמודים החדשים (page.own) מציירים לשוניות משלהן — הבדיקה על המסגרת הגנרית עברה לשרת המשני (גל 8),
  // ומ-#1179 גם לו אין לשוניות (כל עמוד שלו הוא עלה בעץ) — ולכן עמוד סינתטי עם 4 לשוניות על אותה מסגרת.
  const html = run('layout({crumb: "x", title: "x", desc: "", tabs: ["א", "ב", "ג", "ד"]}, 1)');
  assert.match(html, /<div class="vcenter-tabs" role="tablist">/);
  const tabs = [...html.matchAll(/<button type="button" class="vcenter-tab ?(active)?" role="tab" aria-selected="(true|false)" tabindex="(0|-1)"/g)];
  assert.equal(tabs.length, 4, 'four tabs as buttons');
  assert.deepEqual(tabs.map((m) => [m[1] || '', m[2], m[3]]), [['', 'false', '-1'], ['active', 'true', '0'], ['', 'false', '-1'], ['', 'false', '-1']]);
  assert.doesNotMatch(html, /<div class="vcenter-tab[ "]/, 'no div tabs left');
  assert.doesNotMatch(html, /pageActionBtn/, 'ביקורת 10/10: עמוד בלי פעולות (כמו השרת המשני) אינו מקבל כפתור "+ פעולה" שנפתח לתפריט ריק');
  const withActions = run('current="images"; layout({crumb: "x", title: "x", desc: "", tabs: ["א"]}, 0)');
  assert.match(withActions, /id="pageActionBtn" aria-haspopup="menu" aria-expanded="false"/);
  assert.match(withActions, /id="pageActionMenu" class="action-menu" role="menu"/);
});

test('S4: static tree nodes in index.html carry role="treeitem" tabindex="0"; groups carry aria-expanded', () => {
  const html = read('index.html');
  assert.match(html, /<div class="tree" role="tree"/);
  const nodes = [...html.matchAll(/<div class="inventory-node[^"]*"[^>]*>/g)].map((m) => m[0]).filter((t) => !t.includes('muted'));
  assert.ok(nodes.length >= 22, 'got ' + nodes.length);   // 25 עד #936: הצומת הסטטי של המשני ושני ילדיו נמחקו
  for (const t of nodes) assert.match(t, /role="treeitem" tabindex="0"/, t.slice(0, 80));
  const arrows = html.match(/<span class="tree-arrow"/g).length;
  const expanded = nodes.filter((t) => /aria-expanded="(true|false)"/.test(t)).length;
  assert.equal(expanded, arrows, 'every node with an arrow declares aria-expanded');
});

test('S4: dynamically rendered tree nodes (folders, groups, machines) are focusable treeitems', () => {
  const {run, document} = setup();
  run('FOLDERS=[{name:"F"}]; IMAGES=[{id:"i1",name:"Img",folder:"F"}]; populateSidebarImages()');
  const images = document.getElementById('imagesTree').innerHTML;
  const nodes = images.match(/<div class="inventory-node[^>]*>/g);
  assert.equal(nodes.length, 2, 'folder + image');
  for (const t of nodes) assert.match(t, /role="treeitem" tabindex="0"/, t);
  assert.match(nodes[0], /aria-expanded="false"/, 'folder group declares expansion');
  run('ME.capabilities.classrooms=true; GROUPS=[{id:"g1",label:"Class",role:"classroom"}]; MACHINES=[{mac:"aa",suffix:"01",group_id:"g1"}]; populateSidebarGroups()');
  const classes = document.getElementById('classesTree').innerHTML;
  for (const t of classes.match(/<div class="inventory-node[^>]*>/g)) assert.match(t, /role="treeitem" tabindex="0"/, t);
});

test('S4: Enter/Space activate a tree node, arrows move between visible nodes, aria-expanded follows toggles', () => {
  const {run, tree, document} = setup();
  run('init()');
  const keydown = (target, key) => { let prevented = false; for (const fn of tree.querySelector('.tree').listeners.keydown) fn({key, target, currentTarget: tree.querySelector('.tree'), preventDefault() { prevented = true; }}); return prevented; };
  const deploy = tree.querySelector('.inventory-node[data-page="deploy"]');
  let clicked = 0; deploy.click = () => { clicked++; }; deploy.attrs.onclick = 'selectPageById("deploy")';
  assert.equal(keydown(deploy, 'Enter'), true, 'Enter is consumed');
  assert.equal(keydown(deploy, ' '), true, 'Space is consumed');
  assert.equal(clicked, 2, 'both activate the node the way a click would');
  // ArrowDown from deploy: next visible node is "מלאי" (the group), not the hidden infra children
  const home = tree.querySelector('.inventory-node[data-page="home"]');
  keydown(home, 'ArrowDown');
  assert.equal(focused, deploy);
  const images = tree.querySelector('.inventory-node[data-page="images"]');
  keydown(images, 'ArrowDown');
  assert.equal(focused.attrs['data-page'], 'machines');
  keydown(focused, 'ArrowDown');
  assert.ok(!focused.attrs['data-page'], 'after machines comes the collapsed infra group itself, not its hidden children');
  // aria-expanded tracks toggleInventoryGroup
  const inv = tree.querySelector('#invTA').previousElementSibling;
  inv.setAttribute('aria-expanded', 'true');
  run('toggleInventoryGroup(document.getElementById("invTA").previousElementSibling, "invTA")');
  assert.equal(inv.getAttribute('aria-expanded'), 'false');
  assert.ok(tree.querySelector('#invTA').hasAttribute('hidden'));
});

test('S4: tablist arrows move the active tab (RTL: ArrowLeft = next) and Escape closes the action menu', () => {
  const {run, tree, document} = setup();
  run('init()');
  const content = document.getElementById('content');
  // לשוניות מדומות: הרינדור מייצר innerHTML בלבד, לכן הקשר [role=tab] נבנה ידנית
  const tabs = [0, 1, 2].map((i) => el('button', {role: 'tab', 'data-i': String(i)}));
  const list = el('div', {role: 'tablist'}, tabs);
  for (const t of tabs) t.closest = (sel) => (sel === '[role="tab"]' ? t : null);
  document.querySelectorAll = (sel) => (sel === '[role="tab"]' ? tabs : tree.querySelectorAll(sel));
  run('current="home"; currentTab=0');
  for (const fn of content.listeners.keydown) fn({key: 'ArrowLeft', target: tabs[0], preventDefault() {}});
  assert.equal(run('currentTab'), 1, 'ArrowLeft advanced to the next tab');
  assert.equal(focused, tabs[1], 'focus followed the active tab');
  for (const fn of content.listeners.keydown) fn({key: 'ArrowRight', target: tabs[1], preventDefault() {}});
  assert.equal(run('currentTab'), 0);
  for (const fn of content.listeners.keydown) fn({key: 'End', target: tabs[0], preventDefault() {}});
  assert.equal(run('currentTab'), 2);
  // Escape closes the action menu and resets aria-expanded on its button
  const menu = document.getElementById('pageActionMenu'), btn = document.getElementById('pageActionBtn');
  menu.style.display = 'block'; btn.setAttribute('aria-expanded', 'true');
  for (const fn of document.listeners.keydown) fn({key: 'Escape', target: menu});
  assert.equal(menu.style.display, 'none');
  assert.equal(btn.getAttribute('aria-expanded'), 'false');
});

// ---- S5: תפריט "+ פעולה" נפתח פנימה ולא נחתך ב-overflow:hidden ------------

test('S5: .action-menu anchors to inline-end (into the content), not inline-start (out of it)', () => {
  const css = read('console.css');
  assert.equal(lastDecl(css, '.content', 'overflow'), 'hidden', 'precondition: the content clips — that is what made it a breaker');
  assert.equal(lastDecl(css, '.action-menu', 'position'), 'absolute');
  assert.equal(lastDecl(css, '.action-menu', 'inset-inline-end'), '0', 'menu edge on the inner side of the button');
  assert.equal(lastDecl(css, '.action-menu', 'inset-inline-start'), 'auto', 'the original inset-inline-start:0 is neutralised');
});

// ---- S6: תפריט המשתמש נפתח מתחת לכפתור שלו ---------------------------------

test('S6: .user-menu is fixed at inline-end, the same side as .user (margin-inline-start:auto pushes it there)', () => {
  const css = read('console.css');
  assert.equal(lastDecl(css, '.top-actions', 'margin-inline-start'), 'auto', 'precondition: the user button sits at inline-end of the topbar');
  assert.equal(lastDecl(css, '.user-menu', 'position'), 'fixed');
  assert.equal(lastDecl(css, '.user-menu', 'inset-inline-end'), '10px');
  assert.equal(lastDecl(css, '.user-menu', 'inset-inline-start'), 'auto', 'the original inset-inline-start:10px (the far side in RTL) is neutralised');
});

// ---- S7 (#931): הסרגל נגלל, וקליק/דאבל-קליק על צומת עובד כמצופה -----------

/* #931: `.main` הוא grid item עם גובה קבוע מ-.app (48px + 1fr), אבל
   `.sidebar` עצמו לא היה חתוך אליו — התוכן פשוט גדל מעבר לגבול (overflow
   גלוי כברירת מחדל) ודחף את הגוף כולו, בלי scrollbar ובלי שה-wheel יגיע
   לאף אלמנט שבאמת גולל. נמדד בדפדפן אמיתי (127.0.0.1:8081, 1366×768):
   .sidebar.clientHeight גדל יחד עם .scrollHeight (937 שניהם) לפני התיקון,
   ואחריו .clientHeight נשאר 698 (קבוע) בעוד .scrollHeight גדל ל-847 —
   בדיוק ההפרש שמאפשר ל-`.tree{overflow-y:auto}` הקיים לעבוד.
   התיקון עצמו: overflow:hidden על .sidebar (לא height — ראו למטה). */
test('S7: .sidebar clips to its grid row (overflow:hidden), so .tree\'s own vertical scroll can bound it', () => {
  const css = read('console.css');
  assert.equal(lastDecl(css, '.sidebar', 'overflow'), 'hidden',
    'without this the sidebar grows past .main\'s fixed height instead of scrolling internally');
  assert.equal(lastDecl(css, '.tree', 'overflow-y'), 'auto');
  assert.equal(lastDecl(css, '.tree', 'overflow-x'), 'hidden', 'no horizontal scroll (גמור #931)');
  // height:100% on .sidebar was tried and rejected: it over-constrains the
  // <=740px overlay (.sidebar.open{position:fixed;top:48px;bottom:0}),
  // which would push the overlay 48px past the viewport bottom.
  assert.match(css, /\.app\.narrow \.sidebar \.nav-full\{position:fixed;inset-block:48px 0/, '#1300: the narrow drawer pins to the viewport bottom');
});

/* #931 — תיקון ספק מנדב אחרי בדיקה במעבדה (16/09 21:30): הסמנטיקה
   ההפוכה ממה שבנתי קודם. "קליק אחד מראה את מה שלחצתי — נגיד לחצתי
   'מחשבים' אז את המחשבים; שני קליקים פותח וסוגר". כלומר קליק בודד על
   שורת צומת אף פעם לא מטגל (רק בורר/מנווט אם יש לו יעד — אחרת לא עושה
   כלום); דאבל-קליק על השורה מטגל בדיוק פעם אחת; החץ הקטן ממשיך לטגל
   בקליק בודד כמו קודם. toggleInventoryGroup עצמה חזרה לחתימה הפשוטה
   (nodeEl, childId) בלי שמירת event.detail — אין יותר צורך בה: קליק
   בודד על שורה כבר לא קורא לפונקציה בכלל, ודאבל-קליק native קורא לה
   פעם אחת בדיוק (הדפדפן מבטיח את זה, לא הקוד). */
test('S7 (follow-up): the tree scrolls without a visible scrollbar, and a double-click on a row does not select the word', () => {
  // נדב 16/09 22:22: "אני רוצה גלילה בלי שיהיה את זה בתצוגה" — פס הגלילה
  // הנייטיבי נראה כמו רכיב UI; והדאבל-קליק שפותח/סוגר סימן את שם התיקייה.
  const css = read('console.css');
  assert.equal(lastDecl(css, '.tree', 'scrollbar-width'), 'none', 'Firefox: hidden scrollbar, still scrollable');
  assert.match(css, /\.tree::-webkit-scrollbar\{[^}]*display:none/, 'Chromium: hidden scrollbar, still scrollable');
  assert.equal(lastDecl(css, '.inventory-node', 'user-select'), 'none', 'dblclick toggles the node, it must not select its label');
});

test('S7 (reversed 11/10, like the mockup): a single click on a branch row toggles it (or navigates and opens); the arrow only toggles; no dblclick toggling', () => {
  const html = read('index.html');
  assert.doesNotMatch(html, /ondblclick="[^"]*toggleInventoryGroup/, 'dblclick no longer toggles any row');
  // כל שורה שיש לה חץ (כלומר יש לה ילדים) — הקליק על השורה פותח/סוגר (ישירות, או ניווט+פתיחה אם יש לה דף)
  const arrowRows = [...html.matchAll(/<div class="inventory-node[^>]*>(?:(?!<\/div>).)*?<span class="tree-arrow"[^>]*>/g)];
  // ‏#1300: בדיוק הענפים של console-full.html (TREE): השרת, מלאי, מחשבים, תשתית, רשת, ניהול. אימג'ים, מחשבי
  // בנייה/שיכפול וחיבורים פיזיים — עלים (בלי חץ ובלי תת-עץ)
  assert.equal(arrowRows.length, 6, 'expected exactly the mockup branches, got ' + arrowRows.length);
  // ענף בלי דף (מלאי / תשתית / ניהול) — .group, מודגש כמו .tn.group במוקאפ
  for (const box of ['invTA', 'infraTA', 'adminTA']) assert.match(html, new RegExp(`<div class="inventory-node group" onclick="toggleInventoryGroup\\(this,'${box}'\\)"`), box);
  assert.match(read('console.css'), /\.sidebar \.inventory-node\.group\{font-weight:600\}/);
  for (const [rowHtml] of arrowRows) {
    assert.match(rowHtml, /<div class="inventory-node[^>]*\bonclick="(?:toggleInventoryGroup\(this,'\w+'\)|treeNavToggle\(this,'\w+',function\(\)\{[^"]*\}\))"/,
      `row must toggle on a single click: ${rowHtml.slice(0, 90)}`);
  }
  // והחץ עצמו מטגל בקליק בודד ולא מנווט (stopPropagation)
  assert.doesNotMatch(html, /<span class="tree-arrow"(?![^>]*onclick=)[^>]*>/,
    'every .tree-arrow keeps its own single-click toggle');
});

/* #931: toggleInventoryGroup חזרה לחתימה הפשוטה — קריאה בודדת = טוגל
   בודד, תמיד (בלי dedup פנימי). זה נכון כי עכשיו רק שני מקומות קוראים
   לה: החץ (קליק בודד, קורה פעם אחת) והשורה (dblclick, שהדפדפן מבטיח
   שיורה פעם אחת לכל מחווה) — אין יותר "שני click לפני דאבל-קליק" שצריך
   סינון, כי קליק בודד על שורה כבר לא קורא לפונקציה כלל. */
test('S7: toggleInventoryGroup has no built-in de-dup — each call toggles once, matching "arrow-click or row-dblclick, called exactly once per gesture"', () => {
  const {run, tree} = setup();
  const box = tree.querySelector('#invTA');
  assert.ok(!box.hasAttribute('hidden'), 'precondition: invTA starts open');
  run('toggleInventoryGroup(document.getElementById("invTA").previousElementSibling, "invTA")');
  assert.ok(box.hasAttribute('hidden'), 'one call (the dblclick, or the arrow click) closes it');
  run('toggleInventoryGroup(document.getElementById("invTA").previousElementSibling, "invTA")');
  assert.ok(!box.hasAttribute('hidden'), 'a second call re-opens it — no hidden guard eating it');
});

/* #931 גמור: "renameServer נשאר על שם השרת בלבד" — דאבל-קליק על שאר
   השורה מטגל; דאבל-קליק מדויק על הטקסט קורא ל-renameServer בלבד
   (עוצר propagation כדי שלא יגיע גם ל-ondblclick של השורה). קליק בודד
   בשום מקום בשורה הזו לא עושה כלום (לשורת שרת אין יעד ניווט).
   נבדק חי בדפדפן (127.0.0.1:8081): דאבל-קליק על אזור האייקון טגל את
   srvTA; דאבל-קליק מדויק על הטקסט קרא ל-renameServer פעם אחת ו-srvTA
   נשאר פתוח (renameCalls:1, srvTAHidden:false). */
test('S7: the primary server row (the only static one since #936) has no click-to-anything; dblclick toggles the row, and .server-name\'s own dblclick stops it from also reaching the row', () => {
  const html = read('index.html');
  const rows = [...html.matchAll(/<div class="inventory-node server-node[^>]*>/g)];
  assert.equal(rows.length, 1, '#936: only the primary is static; secondaries come from /storage-nodes');
  for (const [rowTag] of rows) {
    assert.match(rowTag, /\bonclick="toggleInventoryGroup\(this,'srvTA'\)"/, 'the TA root toggles (collapses the whole tree) on a single click, like the mockup');
    assert.doesNotMatch(rowTag, /ondblclick=/, 'no dblclick on the row itself');
  }
  const nameHandlers = [...html.matchAll(/<strong class="server-name"[^>]* ondblclick="([^"]*)">/g)].map((m) => m[1]);
  assert.equal(nameHandlers.length, 1, 'renameServer is wired on the primary .server-name only');
  for (const handler of nameHandlers) {
    assert.match(handler, /event\.stopPropagation\(\)/, 'must stop the dblclick from also reaching the row\'s toggle');
    assert.match(handler, /renameServer\(this\)/);
  }
});

// ---- #1300: חלון צר עד 900px כמו המוקאפ — העץ שכבה סגורה, ונסגר בכל כניסה לעמוד ------------------

test('#1300 rail: up to 900px the column is the rail (not hidden — the content keeps its navigation); navIsNarrow is the mockup breakpoint', () => {
  const css = read('console.css');
  assert.match(css, /@media \(max-width:900px\)\{\.main\{grid-template-columns:var\(--rail-w\) minmax\(0,1fr\)\}\}/);
  assert.doesNotMatch(css, /\.sidebar\{display:none\}/, 'the sidebar is never removed — narrow shows the rail');
  assert.doesNotMatch(css, /\.sidebar\.open\{[^}]*position:fixed/, 'the old whole-sidebar overlay is gone');
  assert.match(read('console.js'), /function navIsNarrow\(\) \{ return window\.matchMedia\("\(max-width:900px\)"\)\.matches; \}/);
});
test('#1300: every way into a page or a drawer from the tree closes the overlay', () => {
  const {run, tree} = setup();
  const side = tree.querySelector('#sidebar');
  const open = () => { run('toggleSidebar(true)'); assert.ok(side.classList.contains('open')); };
  open(); run('selectInventory(null,"images")'); assert.ok(!side.classList.contains('open'), 'selectInventory (sub-nodes)');
  open(); run('IMAGES=[{id:"i1",name:"x"}]; IMG.manifest={}; api=()=>new Promise(()=>{}); openImageDetail("i1")'); assert.ok(!side.classList.contains('open'), 'a drawer opened from a tree leaf');
  open(); run('toggleInventoryGroup(document.querySelector("#infraTA"),"infraTA")'); assert.ok(side.classList.contains('open'), 'opening/closing a group is not navigation — the overlay stays');
});

// ‏#1303: "אימות ספרייה" בתפריט "+ פעולה" הציג "בקרוב" למרות ש-POST /images/scrub קיים — מחובר ל-scrubLibrary();
// ה-stubs המתים של soon() נמחקו (כלל נדב 11/10: מה שבוטל — נמחק, לא מוסתר).
test('#1303: the library-verify action is the real scrub, and the soon() stubs are gone', () => {
  const js = read('console.js');
  assert.match(js, /\["אימות ספרייה", "scrubLibrary\(\)"\]/);
  for (const dead of ['function soon()', 'function createImage()', 'function openNewImage()', 'function verifyLibrary()', 'function exportRounds()',
    'function runHealthCheck()', 'function openServicesDrawer()', 'function testNetwork()', 'function rollbackNetwork()']) assert.ok(!js.includes(dead), dead);
  assert.doesNotMatch(js, /\bsoon\(\)/, 'nothing calls soon() any more');
});

/* ‏#1300 (נדב 12/10, עדיפות גבוהה — "העץ לא מתנהג כמו המוקאפ"): רצפי לחיצה מלאים מול ה-onclick האמיתי
   של index.html, לפי console-full.html (#tree click): ענף עם דף — לא בו = ניווט + פתיחה; בו = פתיחה/סגירה;
   ענף בלי דף = פתיחה/סגירה (וסגירה סוגרת גם את מה שמתחתיו); עלה = ניווט. */
test('#1300: tree click sequences are the mockup\'s — מחשבים navigates from anywhere, toggles only on its own page; leaves navigate; a group toggles', () => {
  const {run, tree} = setup();
  const html = read('index.html');
  const onclickOf = (re) => { const m = html.match(re); assert.ok(m, String(re)); return m[1].replace(/&quot;/g, '"'); };
  const machinesClick = onclickOf(/data-page="machines" data-admin onclick="([^"]+)"/);
  const buildClick = onclickOf(/data-role="build" onclick="([^"]+)"/);
  // מחשבים בצורת index.html: ענף פתוח כברירת מחדל, ו"מחשבי בנייה" עלה תחתיו
  const machines = tree.querySelector('[data-page="machines"]');
  machines.attrs['aria-expanded'] = 'true';
  const build = el('div', {class: 'inventory-node', 'data-role': 'build', tabindex: '0'});
  const mbox = el('div', {id: 'machineTree', class: 'inventory-children'}, [build]);
  const inv = tree.querySelector('#invTA');
  inv.children.push(mbox); mbox.parentElement = inv;
  const click = (n, code) => run(`(function(){${code}}).call(document.querySelector('${n}'))`);
  const page = () => run('current'), active = () => tree.querySelectorAll('.inventory-node.active').map((n) => n.attrs['data-page'] || n.attrs['data-role']);
  const open = () => !mbox.hasAttribute('hidden');

  run('selectPageById("home")');
  click('[data-page="machines"]', machinesClick);                      // 1. מהסקירה: ניווט, נשאר פתוח
  assert.equal(page(), 'machines'); assert.deepEqual(active(), ['machines']); assert.ok(open());
  click('[data-page="machines"]', machinesClick);                      // 2. שוב, כשכבר בו: סגירה בלבד
  assert.equal(page(), 'machines'); assert.ok(!open(), 'on its own page a click closes it');
  click('[data-page="machines"]', machinesClick);                      // 3. ושוב: פתיחה
  assert.ok(open());
  click('[data-role="build"]', buildClick);                            // 4. עלה: הדף המסונן, העלה מודגש
  assert.equal(run('MACHINES_ROLE'), 'build'); assert.deepEqual(active(), ['build']);
  // ‏hasCur של המוקאפ: כל צומת-אב מסומן רך — "מחשבים" ו"מלאי"; שורת השרת (root) לא
  assert.ok(machines.classList.contains('anc')); assert.ok(inv.previousElementSibling.classList.contains('anc'));
  assert.ok(!tree.querySelector('.server-node').classList.contains('anc'), 'the server row is the root, never an ancestor mark');
  click('[data-page="machines"]', machinesClick);                      // 5. מ"מחשבי בנייה": ניווט לכל המחשבים
  assert.equal(run('MACHINES_ROLE'), null, 'all machines, not the build filter'); assert.deepEqual(active(), ['machines']); assert.ok(open());
  run('selectPageById("health")');                                     // 6. הבאג: אחרי ביקור קודם ודף אחר
  click('[data-page="machines"]', machinesClick);
  assert.equal(page(), 'machines', 'מחשבים enters its page — it does not "only expand"'); assert.ok(open());
  run('toggleInventoryGroup(document.getElementById("invTA").previousElementSibling, "invTA")');   // 7. "מלאי": סגירה סוגרת גם את מחשבים
  assert.ok(inv.hasAttribute('hidden')); assert.ok(!open(), 'closing a branch closes what is under it (mockup toggle())');
  assert.equal(page(), 'machines', 'a group toggle is not navigation');
});

/* ---- #1300: פס האייקונים (rail.html, אושר 13/10) — נבנה מהעץ, שטוח, לחיצה מנווטת, תפריט צף למחשבים ולרשת ---- */
function buildRailTree() {
  const ico = (k) => el('span', {}, [el('svg', {'data-k': k})]);
  const lbl = (t) => { const x = el('span'); x.textContent = t; return x; };
  const leaf = (attrs, k, t, extra = {}) => el('div', {class: 'inventory-node', tabindex: '0', ...attrs, ...extra}, [el('span', {class: 'tree-arrow-sp'}), ico(k), lbl(t)]);
  const branch = (attrs, k, t) => el('div', {class: 'inventory-node', tabindex: '0', 'aria-expanded': 'true', ...attrs}, [el('span', {class: 'tree-arrow'}), ico(k), lbl(t)]);
  const group = (k, t) => el('div', {class: 'inventory-node group', tabindex: '0', 'aria-expanded': 'true'}, [el('span', {class: 'tree-arrow'}), ico(k), lbl(t)]);
  const box = (id, kids, extra = {}) => el('div', {id, class: 'inventory-children', ...extra}, kids);
  const srv = box('srvTA', [
    leaf({'data-page': 'home'}, 'home', 'סקירה כללית'), leaf({'data-page': 'deploy'}, 'deploy', 'סבב הפצה'),
    group('list', 'מלאי'), box('invTA', [leaf({'data-page': 'images'}, 'folder', "אימג'ים"), branch({'data-page': 'machines'}, 'machines', 'מחשבים'),
      box('machineTree', [leaf({'data-role': 'classroom'}, 'group', 'כיתות', {class: 'inventory-node hidden'}), leaf({'data-role': 'build'}, 'build', 'מחשבי בנייה'), leaf({'data-role': 'cloner'}, 'copy', 'מחשבי שיכפול')])]),
    group('infra', 'תשתית'), box('infraTA', [leaf({'data-page': 'health'}, 'health', 'בריאות ושירותים'), branch({'data-page': 'network'}, 'network', 'רשת'),
      box('netTA', [leaf({'data-net-tab': '0'}, 'diagram', 'תרשים'), leaf({'data-net-tab': '1'}, 'nic', 'חיבורים פיזיים'), leaf({'data-net-tab': '2'}, 'sync', 'רשת הפצה'), leaf({'data-page': 'ports'}, 'ports', 'פורטים')]),
      leaf({'data-page': 'tools'}, 'tools', 'ארגז כלים', {class: 'inventory-node hidden'})]),
    group('admin', 'ניהול'), box('adminTA', [leaf({'data-page': 'permissions'}, 'key', 'משתמשים והרשאות'), leaf({'data-page': 'settings'}, 'sliders', 'הגדרות')]),
  ]);
  const navFull = el('div', {class: 'nav-full', id: 'navFull'}, [el('div', {class: 'tree'}, [srv])]);
  const side = el('aside', {class: 'sidebar', id: 'sidebar'}, [navFull, el('div', {class: 'rail', id: 'rail'})]);
  const toggle = el('button', {class: 'icon-btn nav-toggle', 'aria-expanded': 'true'});
  return el('div', {id: 'app', 'data-nav': 'full'}, [el('header', {class: 'topbar'}, [toggle]), side, el('div', {class: 'sidebar-backdrop', id: 'sidebarBackdrop'}),
    el('div', {id: 'navTip', class: 'nav-tip'}), el('div', {id: 'navFly', class: 'nav-fly'})]);
}
const railButtons = (html) => [...html.matchAll(/<button type="button" class="ri([^"]*)" data-ri="(\d+)" tabindex="(-?\d)" aria-label="([^"]*)"([^>]*)>(<svg[^>]*><\/svg>)<\/button>/g)]
  .map(([, cls, i, tab, label, rest, svg]) => ({cls: cls.trim(), i: +i, tab: +tab, label, rest, k: (svg.match(/data-k="([^"]*)"/) || [])[1]}));

test('#1300 rail: flat like ESXi — the groups without a page become separators, every page is an icon (same icon as its tree node), hidden nodes stay out', () => {
  const {run, tree} = setup(buildRailTree);
  run('SERVER_HEALTH[""]={checks:[{id:"server",state:"ok"}],at:Date.now()}; markTreeSelection(null,"home")');   // נדב 18/10: הנורית = הנקודה של TA
  const html = tree.querySelector('#rail').innerHTML;
  const groups = [...html.matchAll(/<div class="rgrp" role="group" aria-label="([^"]*)">/g)].map((m) => m[1]);
  assert.deepEqual(groups, ['ראשי', 'מלאי', 'תשתית', 'ניהול']);
  assert.equal((html.match(/<div class="rsep" aria-hidden="true"><\/div>/g) || []).length, 3, 'a thin separator between groups');
  const b = railButtons(html);
  assert.deepEqual(b.map((x) => x.label), ['סקירה כללית', 'סבב הפצה', 'אימג&#39;ים', 'מחשבים', 'בריאות ושירותים', 'רשת', 'משתמשים והרשאות', 'הגדרות'], 'no group rows, no third level, no hidden (capability) node');
  assert.deepEqual(b.map((x) => x.k), ['home', 'deploy', 'folder', 'machines', 'health', 'network', 'key', 'sliders'], 'the tree node\'s own icon');
  assert.deepEqual(b.filter((x) => /\bkids\b/.test(x.cls)).map((x) => x.label), ['מחשבים', 'רשת'], 'only the nodes with a third level carry the flyout hint');
  for (const x of b.filter((y) => /\bkids\b/.test(y.cls))) assert.match(x.rest, /aria-haspopup="menu" aria-expanded="false" aria-controls="navFly"/);
  assert.equal(b[0].cls, 'cur'); assert.match(b[0].rest, /aria-current="page"/);
  assert.deepEqual(b.map((x) => x.tab), [0, -1, -1, -1, -1, -1, -1, -1], 'one Tab stop — the current icon');
  assert.match(html, /<div class="rfoot"><span class="rled" role="img" aria-label="מחובר · כל השירותים פעילים" data-tipx="מחובר · כל השירותים פעילים"><span class="led ok"><\/span><\/span><\/div>/, 'the services light moves to the bottom');
});

test('#1300 rail: on a sub-page the visible parent carries the mark — "רשת, נבחר: פורטים" with aria-current=location; a build-machines leaf marks מחשבים', () => {
  const {run, tree} = setup(buildRailTree);
  run('markTreeSelection(null,"ports")');
  let b = railButtons(tree.querySelector('#rail').innerHTML);
  const net = b.find((x) => x.k === 'network');
  assert.equal(net.cls, 'cur kids'); assert.equal(net.label, 'רשת, נבחר: פורטים'); assert.match(net.rest, /aria-current="location"/);
  assert.equal(b.filter((x) => /\bcur\b/.test(x.cls)).length, 1);
  run('markTreeSelection(document.querySelector(\'[data-role="build"]\'),"machines")');
  b = railButtons(tree.querySelector('#rail').innerHTML);
  const m = b.find((x) => x.k === 'machines');
  assert.equal(m.label, 'מחשבים, נבחר: מחשבי בנייה'); assert.equal(m.tab, 0, 'the marked icon is the Tab stop');
  run('markTreeSelection(null,"health")');
  b = railButtons(tree.querySelector('#rail').innerHTML);
  assert.equal(b.find((x) => x.k === 'health').label, 'בריאות ושירותים'); assert.match(b.find((x) => x.k === 'health').rest, /aria-current="page"/);
});

test('#1300 rail: a click always navigates — מחשבים = all machines (role cleared), רשת = its first tab, a leaf = its tree action', () => {
  const {run, tree} = setup(buildRailTree);
  run('selectPageById=(id)=>{current=id;}; markTreeSelection(null,"home"); MACHINES_ROLE="build"');
  run('railGo(RAIL_NODES[3])');
  assert.equal(run('current'), 'machines'); assert.equal(run('MACHINES_ROLE'), null, 'all machines, not the build filter');
  run('markTreeSelection(null,"machines")');
  run('railGo(RAIL_NODES[3])'); assert.equal(run('current'), 'machines', 'on its own page it stays a navigation (never a toggle)');
  run('globalThis.__net=[]; openNetwork=(t)=>{__net.push(t);}; railGo(RAIL_NODES[5])');
  assert.deepEqual(JSON.parse(run('JSON.stringify(__net)')), [0]);
  let clicked = 0; tree.querySelector('[data-page="images"]').click = () => { clicked++; };
  run('railGo(RAIL_NODES[2])'); assert.equal(clicked, 1, 'a leaf runs its own onclick from the tree');
});

test('#1300 rail: the flyout (מחשבים, רשת) lists the third level — "כל המחשבים" first for machines, "כאן" on the current child; ← opens it, a child navigates, Escape/→ close it back to the icon', () => {
  const {run, tree} = setup(buildRailTree);
  run('init()');
  run('markTreeSelection(document.querySelector(\'[data-role="cloner"]\'),"machines")');
  const fly = tree.querySelector('#navFly'), rail = tree.querySelector('#rail');
  const owner = el('button', {id: 'ownerBtn', class: 'ri kids', 'data-ri': '3', 'aria-expanded': 'false'});
  let back = 0; owner.focus = () => { back++; };
  rail.children.push(owner); owner.parentElement = rail;
  // ← על אייקון עם ילדים פותח את התפריט (RTL: ← = פנימה)
  let prevented = 0;
  for (const fn of rail.listeners.keydown) fn({key: 'ArrowLeft', target: owner, preventDefault() { prevented++; }, stopPropagation() {}});
  assert.equal(prevented, 1); assert.ok(fly.classList.contains('show')); assert.equal(owner.getAttribute('aria-expanded'), 'true');
  const items = [...fly.innerHTML.matchAll(/<button type="button" class="fi([^"]*)" role="menuitem" tabindex="-1" data-fi="(\d)"([^>]*)><svg[^>]*><\/svg><span>([^<]*)<\/span>(<span class="ck">כאן<\/span>)?<\/button>/g)]
    .map(([, cls, i, rest, label, here]) => ({cls: cls.trim(), i: +i, current: /aria-current="page"/.test(rest), label, here: !!here}));
  assert.match(fly.innerHTML, /^<div class="fh" aria-hidden="true">מחשבים<\/div>/);
  assert.deepEqual(items.map((x) => x.label), ['כל המחשבים', 'מחשבי בנייה', 'מחשבי שיכפול'], 'the parent row, then the visible children (classrooms hidden)');
  assert.equal(items[0].cls, 'parent');
  assert.deepEqual(items.map((x) => x.here), [false, false, true], '"כאן" on the current child');
  assert.ok(items[2].current);
  // לחיצה על ילד = הפעולה שלו בעץ; התפריט נסגר
  let clicked = 0; tree.querySelector('[data-role="build"]').click = () => { clicked++; };
  const fi = (n) => { const b = el('button', {class: 'fi', 'data-fi': String(n)}); return b; };
  for (const fn of fly.listeners.click) fn({target: fi(1), detail: 1});
  assert.equal(clicked, 1); assert.ok(!fly.classList.contains('show')); assert.equal(owner.getAttribute('aria-expanded'), 'false');
  // Escape / → בתוך התפריט — נסגר והפוקוס חוזר לאייקון
  for (const key of ['Escape', 'ArrowRight']) {
    run('openNavFly(document.getElementById("ownerBtn"))'); assert.ok(fly.classList.contains('show'));
    const b0 = back;
    for (const fn of fly.listeners.keydown) fn({key, target: fi(0), preventDefault() {}, stopPropagation() {}});
    assert.ok(!fly.classList.contains('show'), key); assert.equal(back, b0 + 1, key + ' returns focus to the icon');
  }
  // רשת: בלי שורת "כל ה…" — הלשוניות בלבד
  owner.attrs['data-ri'] = '5';
  run('openNavFly(document.getElementById("ownerBtn"))');
  assert.match(fly.innerHTML, /^<div class="fh" aria-hidden="true">רשת<\/div><button type="button" class="fi" role="menuitem" tabindex="-1" data-fi="0">/);
  assert.doesNotMatch(fly.innerHTML, /כל הרשת|class="fi parent"/);
  assert.equal((fly.innerHTML.match(/role="menuitem"/g) || []).length, 4);
  run('closeNavFly()');
});

test('#1300 rail: the tooltip names the icon (and "› child" on a marked parent); a hover on מחשבים/רשת opens the flyout after 250ms, not at once', () => {
  const {run, tree} = setup(buildRailTree);
  const timers = [];
  run('init()');
  run('markTreeSelection(null,"ports")');
  const rail = tree.querySelector('#rail'), tip = tree.querySelector('#navTip'), fly = tree.querySelector('#navFly');
  const net = el('button', {id: 'netBtn', class: 'ri cur kids', 'data-ri': '5', 'aria-expanded': 'false'});
  rail.children.push(net); net.parentElement = rail;
  run('showNavTip(document.getElementById("netBtn"), true)');
  assert.ok(tip.classList.contains('show')); assert.equal(tip.innerHTML, 'רשת<small>› פורטים</small>');
  run('hideNavTip()'); assert.ok(!tip.classList.contains('show'));
  run('globalThis.__t=[]; setTimeout=(f,ms)=>{__t.push(ms); return __t.length;}');
  for (const fn of rail.listeners.pointerover) fn({target: net, pointerType: 'mouse'});
  assert.ok(run('__t.includes(250)'), 'the flyout waits 250ms (intent), it does not open on a pass-by');
  assert.ok(!fly.classList.contains('show'));
  void timers;
});
