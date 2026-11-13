// #3025 (Nadav 12/11): basic / advanced view in the console. The approved mockup is
// mockup-console-basic-advanced-1112; "network" moved to advanced later the same day.
// Covered here: which tree nodes the basic view hides (index.html), which tab an open
// alert marks (red = fault, orange = warning), the "shown temporarily" section (order,
// "resolved" while inside, gone on leaving, opened from a link), the dot on a group,
// the user-menu switch, and ISO only in advanced. The server half is
// tests/test_user_view_3025.py.
//
// Negative control: on the code before #3025 index.html has no data-adv, no
// "פרטי ההתחברות" and no #viewToggle, and console.js has none of viewAdvanced /
// tabAlerts / tempTabsModel / treeDotLevel -- every test here fails.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../server/static');
const source = consoleJs();
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const cut = (a, b) => { const i = source.indexOf(a), j = source.indexOf(b, i); assert.ok(i >= 0 && j > i, a); return source.slice(i, j); };

function load(extra = {}) {
  const calls = [];
  const ctx = vm.createContext({
    ME: null, PRIMARY_ME: null, calls, current: 'home', currentTab: 0, pages: {},
    put: async (p, body) => { calls.push([p, body]); return {ok: true}; },
    toast: (m) => calls.push(['toast', m]),
    document: {getElementById: () => null, querySelectorAll: () => []},
    imagesTabs: () => ['a'], activateTab() {}, paintTabAlerts() { calls.push(['paint']); },
    ...extra,
  });
  vm.runInContext(cut('function viewAdvanced()', 'const tabNodeKey')
    + ';this.t={viewAdvanced,toggleView,tabAlerts,tempTabsModel,treeDotLevel,tabDotHtml};', ctx);
  // the vm's arrays/objects are another realm's: copy them out, so deepEqual compares values
  const plain = (f) => (...a) => JSON.parse(JSON.stringify(f(...a)));
  const t = {...ctx.t, tabAlerts: plain(ctx.t.tabAlerts), tempTabsModel: plain(ctx.t.tempTabsModel)};
  return {t, ctx, calls};
}

// ---- the filter: what index.html marks advanced -----------------------------------------

function treeNodes() {
  const start = html.indexOf('<div id="srvTA"'), end = html.indexOf('<div id="secondaryServers"');
  assert.ok(start > 0 && end > start);
  const part = html.slice(start, end);
  const stack = [], out = {};
  for (const m of part.matchAll(/<div\b([^>]*)>|<\/div>/g)) {
    if (!m[1] && m[0] === '</div>') { stack.pop(); continue; }
    const attrs = m[1] || '';
    const adv = /\sdata-adv\b/.test(attrs) || stack.some(Boolean);
    stack.push(/\sdata-adv\b/.test(attrs));
    if (!/class="inventory-node\b/.test(attrs)) continue;
    const page = attrs.match(/data-page="([^"]+)"/), net = attrs.match(/data-net-tab="([^"]+)"/);
    if (page) out[page[1]] = adv; else if (net) out['net-' + net[1]] = adv;
  }
  return out;
}

test("Nadav's marking (12/11, network moved to advanced): basic keeps the daily work, advanced is everything else", () => {
  const nodes = treeNodes();
  const basic = Object.keys(nodes).filter((k) => !nodes[k]).sort();
  const advanced = Object.keys(nodes).filter((k) => nodes[k]).sort();
  assert.deepEqual(basic, ['account', 'deploy', 'disks', 'home', 'images', 'logs', 'machines', 'monitor', 'settings'].sort());
  assert.deepEqual(advanced, ['branches', 'drivers', 'health', 'net-0', 'net-1', 'net-2', 'network', 'permissions', 'ports', 'storage', 'tools', 'usbstick'].sort());
});

test('the machine children (build / cloner) stay in basic: editing and WoL live there (decision 1)', () => {
  const box = html.slice(html.indexOf('<div id="machineTree"'), html.indexOf('data-page="disks"'));
  assert.ok(box.includes('data-role="build"') && box.includes('data-role="cloner"'));
  assert.doesNotMatch(box, /data-adv/);
});

test('the temporary section sits at the bottom of the primary tree, hidden until something is in it', () => {
  const srv = html.slice(html.indexOf('<div id="srvTA"'), html.indexOf('<div id="secondaryServers"'));
  const at = srv.indexOf('id="treeTemp"');
  assert.ok(at > srv.indexOf('id="adminTA"'), 'after the last group -- no row above it moves');
  assert.match(srv, /class="tree-temp" id="treeTemp" role="group" aria-label="מוצגות זמנית" hidden>/);
  assert.match(srv, /מוצגות זמנית — יש התראה פתוחה/);
});

// ---- the user menu -------------------------------------------------------------------------

test('the user menu: "פרטי ההתחברות", and an "advanced view" switch that is a menuitemcheckbox', () => {
  const menu = html.slice(html.indexOf('<div class="user-menu" id="userMenu">'));
  assert.ok(menu.includes('>פרטי ההתחברות</button>'));
  assert.ok(!html.includes('פרטי session'));
  assert.match(menu, /<button class="um-switch" id="viewToggle" type="button" role="menuitemcheckbox" aria-checked="false" onclick="toggleView\(\)"><span>תצוגה מתקדמת<small>מציג את כל הלשוניות<\/small><\/span><span class="sw" aria-hidden="true"><\/span><\/button>/);
  assert.ok(menu.indexOf('viewToggle') < menu.indexOf("openServerPower('reboot')"), 'between the account rows and reboot / power off');
});

test('the switch flips the saved view at once and saves it with PUT /me/view (no confirmation)', async () => {
  const me = {username: 'a', view: 'basic'};
  const {t, calls} = load({ME: me, PRIMARY_ME: me});
  assert.equal(t.viewAdvanced(), false);
  await t.toggleView();
  assert.equal(me.view, 'advanced');
  assert.equal(t.viewAdvanced(), true);
  assert.deepEqual(JSON.parse(JSON.stringify(calls.filter((c) => c[0] === '/me/view'))), [['/me/view', {view: 'advanced'}]]);
  assert.ok(calls.some((c) => c[0] === 'paint'), 'the tree is repainted right away');
  await t.toggleView();
  assert.equal(me.view, 'basic');
});

test('a /me without view (a server from before #3025) is basic', () => {
  const me = {username: 'a'};
  assert.equal(load({ME: me, PRIMARY_ME: me}).t.viewAdvanced(), false);
});

// ---- which tab an alert marks --------------------------------------------------------------

const DEPLOY_NET = {id: 'deploy_net', label: 'הגדר את רשת ההפצה', state: 'warn', detail: 'רשת ההפצה לא הוגדרה'};

test('alerts mark their tab: a fault is red (err), a warning orange (warn), the worst wins', () => {
  const {t} = load();
  const out = t.tabAlerts({
    health: [DEPLOY_NET, {id: 'storage_nas1', state: 'bad'}, {id: 'server', state: 'ok'}],
    machines: [{mac: 'm1', probe_verdicts: [{level: 'warn'}]}, {mac: 'm2'}],
    isFailed: (m) => m.mac === 'm2',
    net: [{mac: 'x', registered: false}, {mac: 'y', registered: true}],
    secondary: {haifa: {connected: false}, tlv: {connected: true}},
    storageLevel: '',
  });
  assert.deepEqual(out, {health: 'err', 'net-2': 'warn', storage: 'err', machines: 'err', branches: 'err'});
});

test('nothing open = no dot anywhere', () => {
  const {t} = load();
  assert.deepEqual(t.tabAlerts({health: [{id: 'server', state: 'ok'}], machines: [], net: [], secondary: {}}), {});
});

test('the dot: a page has its own or its children\'s; a group without a page only when collapsed', () => {
  const {t} = load();
  assert.equal(t.treeDotLevel('', ['err'], true, true), 'err', '"רשת" is red when "רשת הפצה" is, open or not');
  assert.equal(t.treeDotLevel('warn', ['', 'err'], true, false), 'err');
  assert.equal(t.treeDotLevel('', ['warn'], false, false), 'warn', 'a collapsed group shows its worst child');
  assert.equal(t.treeDotLevel('', ['warn'], false, true), '', 'an open group does not: the child itself is marked');
  assert.equal(t.treeDotLevel('', [], true, false), '');
  assert.match(t.tabDotHtml('err'), /class="status err tabdot" role="img" aria-label="תקלה"/);
  assert.match(t.tabDotHtml('warn'), /class="status warn tabdot" role="img" aria-label="אזהרה"/);
});

test('the dot colours are the design tokens: red = --clr-danger, orange = --clr-warning, 8px', () => {
  const css = fs.readFileSync(path.join(root, 'console.css'), 'utf8');
  assert.match(css, /\.tabdot\.err i\{background:var\(--clr-danger\)\}/);
  assert.match(css, /\.tabdot\.warn i\{background:var\(--clr-warning\)\}/);
  assert.match(css, /\.tabdot i\{width:8px;height:8px\}/);
});

// ---- the temporary section -----------------------------------------------------------------

const HIDDEN = ['health', 'storage', 'network', 'net-0', 'net-1', 'net-2', 'drivers', 'permissions'];

test('basic: advanced tabs with an open alert are shown, in tree order; advanced: no section at all', () => {
  const {t} = load();
  const alerts = {health: 'err', storage: 'err', machines: 'warn'};
  assert.deepEqual(t.tempTabsModel([], HIDDEN, alerts, 'home', false).map((x) => [x.key, x.level, x.resolved]),
    [['health', 'err', false], ['storage', 'err', false]], 'machines is basic -- it is not "temporary"');
  assert.deepEqual(t.tempTabsModel([], HIDDEN, alerts, 'health', true), []);
});

test('"deploy network not set" reaches a basic user: its tab is shown temporarily, orange', () => {
  const {t} = load();
  const alerts = t.tabAlerts({health: [DEPLOY_NET], machines: [], net: [], secondary: {}});
  const temp = t.tempTabsModel([], HIDDEN, alerts, 'home', false);
  assert.deepEqual(temp.map((x) => [x.key, x.level]), [['health', 'warn'], ['net-2', 'warn']]);
});

test('the order holds: a new alert joins at the bottom, nothing above it moves', () => {
  const {t} = load();
  const prev = t.tempTabsModel([], HIDDEN, {storage: 'err'}, 'home', false);
  const next = t.tempTabsModel(prev, HIDDEN, {storage: 'err', health: 'warn'}, 'home', false);
  assert.deepEqual(next.map((x) => x.key), ['storage', 'health']);
});

test('resolved while the user is inside: it stays, marked resolved, and leaves when they move to another tab', () => {
  const {t} = load();
  const open = t.tempTabsModel([], HIDDEN, {health: 'err', storage: 'err'}, 'storage', false);
  const resolved = t.tempTabsModel(open, HIDDEN, {health: 'err'}, 'storage', false);
  assert.deepEqual(resolved.map((x) => [x.key, x.resolved]), [['health', false], ['storage', true]]);
  const still = t.tempTabsModel(resolved, HIDDEN, {health: 'err'}, 'storage', false);
  assert.deepEqual(still.map((x) => [x.key, x.resolved]), [['health', false], ['storage', true]], 'a repaint keeps it');
  const left = t.tempTabsModel(still, HIDDEN, {health: 'err'}, 'home', false);
  assert.deepEqual(left.map((x) => x.key), ['health'], 'gone once they left; the one below moves up one row');
});

test('resolved while the user is elsewhere: it simply leaves', () => {
  const {t} = load();
  const open = t.tempTabsModel([], HIDDEN, {storage: 'err'}, 'home', false);
  assert.deepEqual(t.tempTabsModel(open, HIDDEN, {}, 'home', false), []);
});

test('a link to a hidden tab (an alert, search) opens it in the section without turning advanced on', () => {
  const {t} = load();
  const here = t.tempTabsModel([], HIDDEN, {}, 'permissions', false);
  assert.deepEqual(here.map((x) => [x.key, x.level, x.resolved]), [['permissions', '', false]]);
  assert.deepEqual(t.tempTabsModel(here, HIDDEN, {}, 'home', false), [], 'and it leaves with the user');
});

// ---- alerts are never filtered by the view -------------------------------------------------

test('the bell and "needs attention" do not look at the view: every alert shows in basic too', () => {
  for (const [a, b] of [['function homeAttention()', '\n}\n'], ['function updateAlertBadge()', '\n}\n']]) {
    const body = cut(a, b);
    assert.doesNotMatch(body, /viewAdvanced|view-hide|TEMP_TABS/, a);
  }
});

// ---- ISO in the images page: advanced only -------------------------------------------------

test('the ISO tab of the images page is advanced only', () => {
  const run = (adv) => {
    const ctx = vm.createContext({isoTabs: () => true, viewAdvanced: () => adv});
    vm.runInContext(cut('function imagesTabs()', '/* ‏GB עשרוני') + ';this.f=imagesTabs;', ctx);
    return ctx.f();
  };
  assert.ok(run(true).includes('ISO'));
  assert.ok(!run(false).includes('ISO'));
});


// ---- #3033 (Nadav 12/11): no group with a single item ---------------------------------------
// Negative control: on 5773cc8b (before #3033) groupIsFlat / treeAlerts / BRANCH_ADVANCED /
// TREE_TEMP_HTML do not exist and the secondary tree carries no data-adv -- these fail.

function basicKidsPerGroup() {
  const out = {};
  for (const id of ['invTA', 'infraTA', 'adminTA']) {
    const start = html.indexOf(`<div id="${id}"`);
    let depth = 0, end = start;
    for (const m of html.slice(start).matchAll(/<div\b[^>]*>|<\/div>/g)) {
      depth += m[0] === '</div>' ? -1 : 1;
      if (depth === 0) { end = start + m.index; break; }
    }
    const box = html.slice(start, end);
    const top = [];
    let d = 0;
    for (const m of box.matchAll(/<div\b([^>]*)>|<\/div>/g)) {
      if (m[0] === '</div>') { d--; continue; }
      d++;
      if (d === 2 && /class="inventory-node(?! hidden)\b/.test(m[1]) && !/\sdata-adv\b/.test(m[1])) top.push(m[1]);
      if (/\/>$/.test(m[0])) d--;
    }
    out[id] = top.length;
  }
  return out;
}

test('#3033: a group left with one item in the current view is shown as the item itself; a temporary item brings the group back', () => {
  const {ctx} = load();
  vm.runInContext(cut('function groupIsFlat(', '/* ‏data-page בעץ הראשי') + ';this.flat=groupIsFlat;', ctx);
  const flat = ctx.flat;
  assert.equal(flat(1, 0), true, 'one item: no heading');
  assert.equal(flat(0, 0), true, 'nothing left: no empty heading either');
  assert.equal(flat(1, 1), false, 'the temporary section adds one of its items: the group is back');
  assert.equal(flat(2, 0), false);
  const kids = basicKidsPerGroup();
  assert.deepEqual(kids, {invTA: 3, infraTA: 1, adminTA: 3}, 'basic: תשתית keeps only מוניטור');
  assert.equal(flat(kids.infraTA, 0), true, '"תשתית" disappears in basic and מוניטור stands alone');
  assert.equal(flat(kids.invTA, 0) || flat(kids.adminTA, 0), false, 'מלאי and ניהול stay groups');
  const paint = cut('function paintTree(', '\n}\n');
  assert.match(paint, /groupIsFlat\(/, 'the tree paint uses it');
  assert.match(paint, /!adv && groupIsFlat/, 'advanced never flattens');
  const css = fs.readFileSync(path.join(root, 'console.css'), 'utf8');
  assert.match(css, /\.sidebar \.inventory-node\.view-flat\{display:none\}/);
  assert.match(css, /\.sidebar \.inventory-children\.view-flat-box\{display:block!important\}/);
});

// ---- #3033 (Nadav 12/11): a secondary server's tree is filtered the same way ----------------

test('#3033: the secondary tree marks the same tabs advanced as the main tree', () => {
  const ctx = vm.createContext({
    esc: (s) => String(s), uiIcon: () => '<svg></svg>', encodeId: (v) => encodeURIComponent(v),
    jsStr: (v) => JSON.stringify(v), ME: {capabilities: {}},
  });
  vm.runInContext(cut('const BRANCH_TREE = [', '/* ‏#1179: כניסה ויציאה מהקשר שרת משנה')
    + ';this.html=branchTreeHtml({id:"haifa",label:"חיפה"},{});', ctx);
  const marks = {};
  for (const m of ctx.html.matchAll(/<div class="inventory-node"([^>]*)>/g)) {
    const page = m[1].match(/data-branch-page="([^"]+)"/);
    if (page) marks[page[1]] = /\sdata-adv\b/.test(m[1]);
  }
  const main = treeNodes();
  assert.ok(Object.keys(marks).length >= 10, JSON.stringify(marks));
  for (const [page, adv] of Object.entries(marks)) {
    if (page in main) assert.equal(adv, main[page], `${page}: the same as the main tree`);
  }
  assert.deepEqual(Object.keys(marks).filter((p) => marks[p]).sort(), ['drivers', 'health', 'network', 'permissions', 'ports', 'storage'].filter((p) => p in marks).sort());
});

test('#3033: every secondary tree gets its own "shown temporarily" section, and is painted when built', () => {
  const pop = cut('async function populateSidebarSecondaries()', '\nfunction markSecondaryStatus(');
  assert.match(pop, /\$\{kids\}\$\{TREE_TEMP_HTML\}<\/div>/, 'at the bottom of that server\'s tree');
  assert.match(pop, /paintTabAlerts\(\);/);
  assert.match(source, /const TREE_TEMP_HTML = `<div class="tree-temp" role="group" aria-label="מוצגות זמנית" hidden><div class="tree-temp-label">מוצגות זמנית — יש התראה פתוחה<\/div><div class="tree-temp-list"><\/div><\/div>`;/);
  const all = cut('function paintTabAlerts()', '\nfunction paintTree(');
  assert.match(all, /#secondaryServers > \.inventory-children/, 'each secondary server tree is painted');
  assert.match(all, /#secondaryServers \[data-adv\]/, 'and filtered by the user\'s view');
});

test('#3033: a secondary tree\'s dots come from that server\'s own alerts, never from the main server\'s', () => {
  const ctx = vm.createContext({
    SERVER_CTX: null, HEALTH: [{id: 'udp_sender', state: 'bad'}], MACHINES: [], NET: [], HOME: {net: []},
    OVERVIEW: null, SECONDARY_STATUS: {}, isAdmin: () => true, machineIsFailed: () => false,
    homeStoragePct: () => null, homeStorageLevel: () => '',
    SERVER_HEALTH: {haifa: {checks: [{id: 'storage_nas', state: 'bad'}, {id: 'deploy_net', state: 'warn'}]}, tlv: {checks: null, down: true}},
  });
  vm.runInContext(cut('const TAB_NET_CHECKS', '/* הקטע הזמני:') + cut('const TEMP_TABS = {};', 'function paintTabAlerts()')
    + ';this.f=(k)=>JSON.stringify(treeAlerts(k));', ctx);
  assert.deepEqual(JSON.parse(ctx.f('haifa')), {health: 'err', storage: 'err', 'net-2': 'warn'});
  assert.deepEqual(JSON.parse(ctx.f('tlv')), {}, 'a server that did not answer has no tab alerts (its own dot is red)');
  assert.deepEqual(JSON.parse(ctx.f('')), {health: 'err'}, 'the main tree: its own health');
  vm.runInContext('isAdmin = () => false;', ctx);
  assert.deepEqual(JSON.parse(ctx.f('haifa')), {});
});
