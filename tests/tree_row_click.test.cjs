// הכרעת נדב 11/10: פתיחה/סגירה בעץ כמו המוקאפ — לחיצה על שורה (לא דאבל-קליק), חץ רק מחליף מצב.
// ועוד: תפקיד עם קבוצה אחת מציג את המחשבים ישירות תחתיו; עם שתיים — צמתי קבוצה כמו קודם.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../server/static');
const source = fs.readFileSync(path.join(root, 'console.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const cut = (a, b) => { const i = source.indexOf(a), j = source.indexOf(b); assert.ok(i > 0 && j > i, a); return source.slice(i, j); };

function node(cls = []) {
  const set = new Set(cls), attrs = new Map();
  return {set, attrs, classList: {contains: (c) => set.has(c), add: (c) => set.add(c)}, setAttribute: (k, v) => attrs.set(k, v),
    querySelector: () => null};
}
function box(hidden) {
  return {hidden, style: {removeProperty() {}}, hasAttribute(k) { return k === 'hidden' && this.hidden; },
    setAttribute(k) { if (k === 'hidden') this.hidden = true; }, removeAttribute(k) { if (k === 'hidden') this.hidden = false; },
    querySelectorAll: () => []};
}
function navSetup(boxes, hereNode, current = 'home') {
  const ctx = vm.createContext({current, document: {getElementById: (id) => boxes[id] || null, querySelector: () => hereNode || null},
    hasClass: (el, c) => !!(el && el.classList && el.classList.contains(c))});
  vm.runInContext(cut('function collapseInventoryDescendants(', '/* S4 (#829)') + '\n;this.api={treeNavToggle};', ctx);
  return ctx.api;
}

test('a branch that is also a page: click when elsewhere = navigate and open (not toggle)', () => {
  const n = node(), b = box(true); let navs = 0, self = null;
  navSetup({machineTree: b}).treeNavToggle(n, 'machineTree', function () { navs++; self = this; });
  assert.equal(navs, 1);
  assert.equal(self, n, '`this` in the inline handler is the row (openBranchView(…,this) needs it)');
  assert.equal(b.hidden, false, 'opened by the click');
  assert.equal(n.attrs.get('aria-expanded'), 'true');
});

test('a branch that is also a page: click when already on it = toggle only, no navigation', () => {
  const n = node(['active']), b = box(false); let navs = 0;
  navSetup({machineTree: b}).treeNavToggle(n, 'machineTree', () => { navs++; });
  assert.equal(navs, 0);
  assert.equal(b.hidden, true, 'closed by the click');
});

test('navigating to an already-open branch keeps it open (a click never closes a branch you are leaving for)', () => {
  const n = node(), b = box(false);
  navSetup({machineTree: b}).treeNavToggle(n, 'machineTree', () => {});
  assert.equal(b.hidden, false);
  assert.ok(!n.set.has('here'), '#1300: no sticky ".here" mark is left on the row');
});

// ‏#1300 (נדב 12/10, הבאג): "אני לוחץ על מחשבים — הוא רק נפתח, לא מכניס אותי לדף". הסימון הדביק ‏.here נשאר
// על "מחשבים" אחרי מעבר לדף אחר, והלחיצה הבאה רק פתחה/סגרה. כמו המוקאפ (console-full.html #tree click):
// "on it" = הדף של הצומת הוא הנוכחי — רק אז פתיחה/סגירה.
test('#1300: a page branch you were on before and left is a navigation again (the "only expands" bug)', () => {
  const machines = node(['here']), b = box(false); let navs = 0;   // שארית של הסימון הישן — אינה "כבר בו"
  navSetup({machineTree: b}).treeNavToggle(machines, 'machineTree', () => { navs++; });
  assert.equal(navs, 1, 'one click on מחשבים enters the Machines page');
  assert.equal(b.hidden, false, 'and leaves it open');
});

test('#1300: "רשת" while one of its tabs is open (anc) toggles, like the mockup (S.page === "network")', () => {
  const net = node(['anc']), b = box(false); let navs = 0;
  net.dataset = {page: 'network'};
  navSetup({netTA: b}, null, 'network').treeNavToggle(net, 'netTA', () => { navs++; });
  assert.equal(navs, 0); assert.equal(b.hidden, true);
});

test('#1300: מחשבים marked soft (anc) above an active "מחשבי בנייה" is NOT "on it" — the click enters all machines (mockup: page "build" !== "machines")', () => {
  const machines = node(['anc']), b = box(false); let navs = 0;
  machines.dataset = {page: 'machines'};
  navSetup({machineTree: b}, null, 'machines').treeNavToggle(machines, 'machineTree', () => { navs++; });
  assert.equal(navs, 1); assert.equal(b.hidden, false);
});

test('no tree row toggles on dblclick any more (only the server-name rename keeps it)', () => {
  const left = (html + source).match(/ondblclick="[^"]*toggleInventoryGroup/g);
  assert.equal(left, null, 'ondblclick must not toggle branches');
  assert.match(html, /class="inventory-node server-node active" onclick="toggleInventoryGroup\(this,'srvTA'\)"/, 'TA root toggles on a single click');
  for (const id of ['invTA', 'infraTA', 'adminTA']) assert.match(html, new RegExp(`onclick="toggleInventoryGroup\\(this,'${id}'\\)"`), id);
});

function fillSetup() {
  const trees = {buildTree: {innerHTML: '', querySelectorAll: () => []}};
  const ctx = vm.createContext({document: {getElementById: (id) => trees[id] || null}, trees,
    uiIcon: () => '', esc: (x) => String(x), encodeId: (x) => String(x), jsStr: (x) => `'${x}'`, machineName: (m) => m.name, sortMachinesBySuffix: (a) => a,
    machineGroupId: (m) => m.group, sidebarChildOpen: () => ({}),
    // #1179: the primary's context — sideId/sideClick are the identity (secondary_context.test.cjs covers the other)
    sideId: (x) => x, sideClick: (h) => h});
  vm.runInContext(cut('function fillSidebarTree(', 'function machineIsFailed(') + '\n;this.fill=fillSidebarTree;', ctx);
  return ctx;
}
const machines = [{mac: 'a', name: 'HP-1', group: 'g1'}, {mac: 'b', name: 'HP-2', group: 'g1'}, {mac: 'c', name: 'HP-3', group: 'g2'}];

test('role with ONE group: machines are listed directly under the role, no group node', () => {
  const ctx = fillSetup();
  ctx.fill('buildTree', [{id: 'g1', label: 'מחשב בניית אימג\'ים'}], machines, 'build');
  const out = ctx.trees.buildTree.innerHTML;
  assert.ok(out.includes('HP-1') && out.includes('HP-2'));
  assert.ok(!out.includes('HP-3'), 'only that group\'s machines');
  assert.ok(!out.includes('בניית אימג'), 'no group level');
  assert.ok(!out.includes('aria-expanded'), 'nothing to expand below the role');
});

test('role with TWO groups: group nodes, each with its machines, as before', () => {
  const ctx = fillSetup();
  ctx.fill('buildTree', [{id: 'g1', label: 'קבוצה א'}, {id: 'g2', label: 'קבוצה ב'}], machines, 'build');
  const out = ctx.trees.buildTree.innerHTML;
  assert.ok(out.includes('קבוצה א') && out.includes('קבוצה ב'));
  assert.ok(out.includes('id="sg-g1"') && out.includes('id="sg-g2"'));
  assert.ok(out.includes('HP-3'));
});

// Nadav 18/10 (the first login, with a picture; like vCenter with several hosts): TA open, סקירה כללית
// chosen and סבב הפצה shown under it, מלאי / תשתית / ניהול folded; each secondary is a root node
// like TA, after it, folded. There is no saved open/closed state of the tree today, so this is it.
test('Nadav 18/10: the tree at the first login -- TA open, its three categories folded, the overview is the page, secondaries are folded roots after TA', () => {
  const head = (id) => html.match(new RegExp(`<div class="inventory-node[^"]*" onclick="toggleInventoryGroup\\(this,'${id}'\\)"[^>]*aria-expanded="(true|false)"><span class="tree-arrow"[^>]*data-open="(true|false)">(.)<`));
  const boxTag = (id) => html.match(new RegExp(`<div id="${id}" class="inventory-children"[^>]*>`))[0];
  const ta = head('srvTA');
  assert.ok(ta, 'the TA row');
  assert.deepEqual([ta[1], ta[2], ta[3]], ['true', 'true', '▾'], 'TA open');
  assert.doesNotMatch(boxTag('srvTA'), /hidden/);
  for (const id of ['invTA', 'infraTA', 'adminTA']) {
    const h = head(id);
    assert.ok(h, id);
    assert.deepEqual([h[1], h[2], h[3]], ['false', 'false', '▸'], `${id} folded`);
    assert.match(boxTag(id), /\shidden>$/, `${id} box hidden`);
  }
  // סקירה כללית and סבב הפצה are direct rows of TA (shown while the categories are folded)
  const srv = html.slice(html.indexOf('<div id="srvTA"'), html.indexOf("toggleInventoryGroup(this,'invTA')"));
  assert.match(srv, /data-page="home"/); assert.match(srv, /data-page="deploy"/);
  // after the login the overview is selected (markTreeSelection marks it active)
  const showApp = source.slice(source.indexOf('async function showApp()'));
  const body = showApp.slice(0, showApp.indexOf('\n}\n'));
  assert.match(body, /selectPageById\("home"\);/, 'showApp opens the overview');
  // the secondaries are roots after TA's box -- not inside it
  let depth = 0, i = html.indexOf('<div id="srvTA"'), end = -1;
  const tag = /<div\b|<\/div>/g; tag.lastIndex = i;
  for (let m; (m = tag.exec(html));) { depth += m[0] === '</div>' ? -1 : 1; if (depth === 0) { end = m.index; break; } }
  assert.ok(end > 0 && html.indexOf('<div id="secondaryServers"') > end, 'secondaries after TA, at its level');
  // a secondary root starts folded (populateSidebarSecondaries: open only when it was open)
  assert.match(source, /const open = !!wasOpen\[boxId\];/);
});

// Nadav 18/10 (TA, b951c1fd): "I logged in and the categories were not folded". The page keeps its tree DOM
// between a logout (or the idle logout) and the next login; after every login the tree is index.html's again.
function fakeBox(id, open) {
  const arrow = {dataset: {open: String(open)}, textContent: open ? '▾' : '▸'};
  const owner = {attrs: {'aria-expanded': String(open)}, classList: {contains: (c) => c === 'inventory-node'},
    setAttribute(k, v) { this.attrs[k] = v; }, querySelector: (s) => (s === '.tree-arrow' ? arrow : null)};
  const box = {id, hidden: !open, style: {display: '', removeProperty() { this.display = ''; }}, previousElementSibling: owner, arrow, owner,
    hasAttribute(k) { return k === 'hidden' && this.hidden; }, setAttribute(k) { if (k === 'hidden') this.hidden = true; },
    removeAttribute(k) { if (k === 'hidden') this.hidden = false; }};
  return box;
}
test('Nadav 18/10: every login lands on the overview with the first tree -- TA open, its categories and every secondary folded (a #settings hash changes nothing)', async () => {
  const boxes = {srvTA: fakeBox('srvTA', true), invTA: fakeBox('invTA', false), infraTA: fakeBox('infraTA', false),
    adminTA: fakeBox('adminTA', false), machineTree: fakeBox('machineTree', true), 'srv-n0de': fakeBox('srv-n0de', false)};
  const inside = ['invTA', 'infraTA', 'adminTA', 'machineTree'];
  boxes.srvTA.querySelectorAll = () => inside.map((id) => boxes[id]);
  const secondary = {querySelectorAll: () => [boxes['srv-n0de']]};
  let pages = [];
  const ctx = vm.createContext({
    document: {getElementById: (id) => (id === 'secondaryServers' ? secondary : boxes[id] || null)},
    location: {hash: '#settings'}, SERVER_CTX: null, ME: null, PRIMARY_ME: null, LOGIN: null, SERVER_HEALTH: {'': {}},
    api: async () => ({username: 'nadav', role: 'admin'}),
    showApp: async () => { pages.push('home'); }, restoreTaskDock() {},
  });
  vm.runInContext(cut('let TREE_INITIAL = null;', 'async function enterApp()') + cut('async function enterApp()', 'async function showApp()')
    + ';this.api2={treeSnapshot,resetTreeToInitial,enterApp};this.set=(v)=>{TREE_INITIAL=v;};', ctx);
  ctx.set(ctx.api2.treeSnapshot());   // init(): the page as index.html drew it
  // the session before: settings open under ניהול, מלאי open, TA folded, a secondary open
  for (const [id, on] of [['adminTA', true], ['invTA', true], ['srvTA', false], ['srv-n0de', true]]) {
    boxes[id].hidden = !on; boxes[id].arrow.textContent = on ? '▾' : '▸';
  }
  await ctx.api2.enterApp();
  const state = (id) => [boxes[id].hidden, boxes[id].arrow.textContent, boxes[id].owner.attrs['aria-expanded']];
  assert.deepEqual(state('srvTA'), [false, '▾', 'true'], 'TA open');
  for (const id of ['invTA', 'infraTA', 'adminTA']) assert.deepEqual(state(id), [true, '▸', 'false'], `${id} folded`);
  assert.deepEqual(state('srv-n0de'), [true, '▸', 'false'], 'a secondary folded');
  assert.equal(boxes.machineTree.hidden, false, 'a nested box goes back to its first state too');
  assert.deepEqual(pages, ['home'], 'showApp -- which selects the overview');
  assert.deepEqual(Object.keys(ctx.SERVER_HEALTH), [], 'the previous session server dots are measured again');
  assert.doesNotMatch(source, /location\.hash/, 'no page is restored from the URL');
  // and F5 inside a session (init -> /me -> showApp) does not go through enterApp: no reset there
  const boot = source.slice(source.indexOf('window.addEventListener("DOMContentLoaded"'));
  assert.match(boot, /api\("\/me"\)\.then\(\(me\) => \{ ME = me; return showApp\(\); \}\)/);
});
