// #1179 — the secondary's pages are the primary's pages in the secondary's context.
// When a secondary is chosen in the tree (SERVER_CTX), api() sends every console GET
// through the primary's relay (/storage-nodes/{id}/console/…, 8443), and the same
// load/render functions draw the answer: the same tabs and cards by construction.
// Phase 1 is read-only: post/put/del in that context are refused before any request.
// All the console scripts run in a vm with a stub fetch, in index.html's order.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', className: '', attrs: {}, dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute(k, v) { this.attrs[k] = String(v); },
  getAttribute(k) { return this.attrs[k] ?? null; }, closest() { return null; },
  removeAttribute(k) { delete this.attrs[k]; }, hasAttribute(k) { return k in this.attrs; }, focus() {}, click() {}});

const NODE = 'n0de';
const RELAY = `/storage-nodes/${NODE}/console`;

function setup(fixtures) {
  const calls = [];
  const toasts = [];
  const listeners = [];
  const registry = new Map();
  const node = (sel) => {
    sel = sel.replace(/^#/, '');
    if (!registry.has(sel)) registry.set(sel, stubNode());
    return registry.get(sel);
  };
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, Object, Array, String, RegExp, Error,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: node, getElementById: node,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); }, click() {}, style: {},
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, body: stubNode(),
      addEventListener(type, fn, capture) { listeners.push({type, fn, capture}); }, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    XMLHttpRequest: function () { calls.push({url: 'XHR', method: 'XHR'}); this.open = () => {}; this.setRequestHeader = () => {}; this.send = () => {}; this.upload = {}; },
    fetch: async (url, options = {}) => {
      calls.push({url, method: options.method || 'GET'});
      const key = url.replace('/api/console', '').split('?')[0];
      const value = fixtures[key];
      if (value instanceof Error) return {status: Number(value.message) || 500, ok: false, headers: {get: () => null}, json: async () => ({detail: `שגיאה מ-${key}`})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => (value === undefined ? {} : JSON.parse(JSON.stringify(value)))};
    },
  });
  for (const f of ['progress.js', 'console.js', 'reorder.js', 'net.js', 'netcfg.js', 'storage_nodes.js', 'drivers.js', 'storage.js', 'disks.js', 'branches.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx, {filename: f});
  }
  const run = (s) => vm.runInContext(s, ctx);
  ctx.__toasts = toasts;
  run(`ME=${JSON.stringify({username: 'noc', role: 'admin', idle_seconds: 300, server_name: 'imagectl-main', version: 'v0.67.0',
    tls: {fingerprint_sha256: 'AA'}, uptime_seconds: 99, deploy_ip: '10.44.0.1',
    capabilities: {interbranch_transfer: true, enroll_secondary: true}})}; PRIMARY_ME=ME;
    toast=(m)=>__toasts.push(m); current="home"; currentTab=0; closeSidebar=()=>{}; renderCurrent=()=>{};
    BRANCH_NODES=[{id:"${NODE}", label:"חיפה"}];`);
  return {run, calls, toasts, listeners};
}

// Same data for both servers, so any difference between the pages is the context's.
const DATA = {
  '/overview': {session: null, images: 0, machines: 0, storage: {}}, '/machines': [], '/disks': {disks: []}, '/disk-sets': [], '/disk-sets/rounds': [], '/groups': [],
  '/disk-failures': [], '/shrink-records': [], '/net': [], '/net/config': {interfaces: []},
  '/net/deploy': {configured: true, source: 'cli', interface: null, url: null, hint: null},
  '/net/interfaces': [], '/room': {round: null, machines: []}, '/room/history': [], '/room/driver-models': [],
  '/images': [], '/folders': [], '/tasks': [], '/health': [{id: 'dnsmasq', label: 'dnsmasq', state: 'ok', detail: 'פעיל'}],
  '/ports': [], '/storage-locations': {locations: [], summary: {}}, '/drivers': [],
  '/monitor/machines': [{mac: 'aa:bb:cc:dd:ee:01', name: 'B1', role: 'build', ip: '10.0.0.5', online: true}],
  '/monitor/settings': {enabled: true}, '/users': [{username: 'noc', role: 'admin'}], '/journal': [], '/journal/events': [],
  '/settings': {}, '/update': {}, '/update/status': {}, '/me': {},
  // the primary's own branches (a secondary has none; in its context these are never asked)
  '/storage-nodes': [], '/storage-transfers': [],
};
const both = () => {
  const f = {};
  for (const [k, v] of Object.entries(DATA)) { f[k] = v; f[RELAY + k] = v; }
  return f;
};
// The secondary's pages: every leaf of its tree except "העברות" (a primary page about it).
const PAGES = ['home', 'deploy', 'images', 'machines', 'disks', 'health', 'storage', 'network', 'monitor', 'drivers', 'permissions', 'logs', 'settings'];

const tabsOf = (html) => [...html.matchAll(/role="tab"[^>]*>([^<]*)/g)].map((m) => m[1]);
const cardsOf = (html) => [...html.matchAll(/class="card-h"><span>([^<]*)/g)].map((m) => m[1]);

async function draw(t, page, tab, inSecondary) {
  t.run(inSecondary ? `setServerContext("${NODE}", "חיפה")` : 'setServerContext(null)');
  t.run(`current=${JSON.stringify(page)}; currentTab=${tab}`);
  await t.run(`Promise.resolve(pages[${JSON.stringify(page)}].load && pages[${JSON.stringify(page)}].load())`);
  await new Promise((r) => setImmediate(r));
  return t.run(`layout(pages[${JSON.stringify(page)}], ${tab})`);
}

test('the tree leaves are the primary\'s pages', () => {
  const t = setup(both());
  const leaves = t.run('BRANCH_VIEWS.map((v) => v[0])');
  assert.equal(JSON.stringify(leaves.filter((p) => p !== 'transfers')), JSON.stringify(PAGES));
  for (const p of PAGES) assert.ok(t.run(`!!pages[${JSON.stringify(p)}]`), p);
});

test('every page of the secondary has the primary\'s tabs and cards — same names, same order — on every tab', async () => {
  for (const page of PAGES) {
    const t = setup(both());
    const first = await draw(t, page, 0, false);
    const tabs = tabsOf(first);
    // a reader that finds no tabs proves nothing: these pages have tabs on the primary.
    if (['home', 'deploy', 'images', 'machines', 'network', 'drivers'].includes(page)) {
      assert.ok(tabs.length >= 2, `${page}: the primary page has tabs (${JSON.stringify(tabs)})`);
    }
    assert.ok(cardsOf(first).length + tabs.length > 0 || /class="obj/.test(first), `${page}: something was drawn`);
    // נדב 18/10: הלשונית "שרתי משנה" בהגדרות קיימת בראשי בלבד — בהקשר שרת משנה היא אינה קיימת כלל.
    const count = page === 'settings' ? 1 : Math.max(1, tabs.length);
    if (page === 'settings') assert.equal(JSON.stringify(tabs), JSON.stringify(['כללי', 'שרתי משנה']), 'settings: the primary has the secondaries tab');
    for (let i = 0; i < count; i++) {
      const p = setup(both());
      const s = setup(both());
      const primary = await draw(p, page, i, false);
      const secondary = await draw(s, page, i, true);
      assert.equal(JSON.stringify(tabsOf(secondary)), JSON.stringify(page === 'settings' ? [] : tabsOf(primary)), `${page} tab ${i}: tabs`);
      assert.equal(JSON.stringify(cardsOf(secondary)), JSON.stringify(cardsOf(primary)), `${page} tab ${i}: cards`);
      assert.doesNotMatch(secondary, /לא הצלחתי לצייר את העמוד/, `${page} tab ${i} rendered`);
    }
  }
});

test('the network page keeps its four tabs on the secondary (תרשים / חיבורים פיזיים / רשת הפצה / פורטים)', async () => {
  const t = setup(both());
  const html = await draw(t, 'network', 0, true);
  assert.equal(JSON.stringify(tabsOf(html)), JSON.stringify(['תרשים', 'חיבורים פיזיים', 'רשת הפצה', 'פורטים']));
});

test('in the secondary\'s context every console GET goes through the relay; /me stays the primary\'s', async () => {
  for (const page of PAGES) {
    const t = setup(both());
    t.run(`setServerContext("${NODE}", "חיפה")`);
    const before = t.calls.length;
    await draw(t, page, 0, true);
    const urls = t.calls.slice(before).map((c) => c.url);
    assert.ok(urls.length > 0, `${page} asked something`);
    for (const url of urls) {
      assert.ok(url.startsWith(`/api/console${RELAY}/`) || /^\/api\/console\/me(\/|\?|$)/.test(url),
        `${page}: ${url} did not go through the relay`);
    }
  }
  const t = setup(both());
  assert.equal(t.run(`setServerContext("${NODE}", "חיפה"); consoleUrl("/me")`), '/api/console/me');
  assert.equal(t.run('consoleUrl("/users")'), `/api/console${RELAY}/users`);
  assert.equal(t.run('consoleUrl("/storage-nodes", true)'), '/api/console/storage-nodes', 'the sidebar asks the primary');
});

test('post / put / del in the secondary\'s context go to the relay — never to the primary\'s own routes; files (XHR, backup, download) are blocked', async () => {
  const t = setup(both());
  t.run(`setServerContext("${NODE}", "חיפה")`);
  for (const call of ['post("/users", {username: "x"})', 'put("/settings", {a: 1})', 'del("/users/x")',
    'post("/images/img_1/delete", {})', 'put("/ports/tftp", {enabled: false})']) {
    const before = t.calls.length;
    await t.run(call);
    const sent = t.calls.slice(before);
    assert.equal(sent.length, 1, call);
    assert.ok(sent[0].url.startsWith(`/api/console${RELAY}/`), `${call} → ${sent[0].url}`);
  }
  const before = t.calls.length;
  t.run('uploadImage({name: "a.tar", size: 1})');
  t.run('IMG.sel = new Set(["img_1"]); bulkDownload()');
  t.run('document.getElementById("driverFile").files = [{name: "d.tar"}]; startDriverUpload()');
  assert.equal(t.calls.length, before, 'no file left the browser: ' + JSON.stringify(t.calls.slice(before)));
  assert.ok(t.toasts.filter((m) => /קבצים/.test(m)).length >= 3, JSON.stringify(t.toasts));
  // back on the primary, the same call goes to the primary's own route
  t.run('setServerContext(null)');
  await t.run('post("/users", {username: "x"})');
  assert.equal(t.calls[t.calls.length - 1].url, '/api/console/users');
});

test('a sheet in the secondary\'s context says which server the action lands on', () => {
  const t = setup(both());
  t.run(`setServerContext("${NODE}", "חיפה"); sheet({title: "מחיקת משתמש", danger: true, onSubmit: () => {}})`);
  assert.match(t.run('document.getElementById("sheet").innerHTML'), /data-target-server>הפעולה תתבצע על <b>חיפה<\/b>/);
  t.run('setServerContext(null); sheet({title: "x", onSubmit: () => {}})');
  assert.doesNotMatch(t.run('document.getElementById("sheet").innerHTML'), /data-target-server/);
});

test('switching servers resets every page cache — a secondary page never draws the primary\'s data', () => {
  const t = setup(both());
  t.run('MACHINES=[{mac:"aa"}]; GROUPS=[{id:"g"}]; HEALTH=[{id:"x"}]; DRIVERS=[{name:"p"}]; STORAGE={locations:[]}; USERS=[{}]; ROOM={machines:[]}; NET=[]; HOME.net=[1]; NETW.nics=[1]; LOG.rows=[1]');
  t.run(`setServerContext("${NODE}", "חיפה")`);
  for (const v of ['MACHINES', 'GROUPS', 'HEALTH', 'DRIVERS', 'STORAGE', 'USERS', 'ROOM', 'NET', 'HOME.net', 'NETW.nics', 'LOG.rows']) {
    assert.equal(t.run(v), null, v);
  }
  t.run('MACHINES=[{mac:"bb"}]; setServerContext(null)');
  assert.equal(t.run('MACHINES'), null, 'and back');
});

test('ME in the secondary\'s context: same user and role, the secondary\'s name, and what is not read from it is null — not the primary\'s', () => {
  const t = setup(both());
  t.run(`setServerContext("${NODE}", "חיפה")`);
  assert.equal(t.run('ME.username + "/" + ME.role'), 'noc/admin');
  assert.equal(t.run('ME.server_name'), 'חיפה');
  for (const k of ['version', 'tls', 'uptime_seconds', 'deploy_ip']) assert.equal(t.run(`ME.${k}`), null, k);
  assert.equal(t.run('ME.capabilities.enroll_secondary'), false);
  assert.equal(t.run('serverRootName("x")'), 'חיפה', 'the page header names the secondary');
  t.run('setServerContext(null)');
  assert.equal(t.run('ME === PRIMARY_ME && ME.server_name'), 'imagectl-main');
});

test('a page header in the secondary\'s context names the secondary in its crumbs', async () => {
  const t = setup(both());
  const html = await draw(t, 'health', 0, true);
  assert.match(html, /<div class="crumbs"><a[^>]*>חיפה<\/a>/);
  assert.doesNotMatch(html, /imagectl-main/);
});

test('a click on a primary tree node leaves the secondary\'s context before its own onclick; an expand arrow does not', () => {
  const t = setup(both());
  t.run(`setServerContext("${NODE}", "חיפה")`);
  const capture = t.listeners.find((l) => l.type === 'click' && l.capture);
  assert.ok(capture, 'a capture-phase click listener is registered');
  const target = (opts) => ({closest(sel) {
    if (sel === '.inventory-node') return {getAttribute: () => opts.onclick, closest: (s) => (s === '#sidebar' ? {} : s === '#secondaryServers' ? (opts.secondary ? {} : null) : null)};
    if (sel === '.tree-arrow') return opts.arrow ? {} : null;
    return null;
  }});
  const ev = (opts) => ({target: target(opts), preventDefault() {}, stopPropagation() {}});
  capture.fn(ev({onclick: "selectPageById('health')", arrow: true}));
  assert.equal(t.run('SERVER_CTX'), NODE, 'the arrow only toggles');
  capture.fn(ev({onclick: "openSecondaryPage('n0de','health',this)", secondary: true}));
  assert.equal(t.run('SERVER_CTX'), NODE, 'a secondary leaf keeps it');
  capture.fn(ev({onclick: "selectPageById('health')"}));
  assert.equal(t.run('SERVER_CTX'), null, 'a primary node leaves it');
});

test('a console download link in the secondary\'s context is blocked (it would download the primary\'s file)', () => {
  const t = setup(both());
  t.run(`setServerContext("${NODE}", "חיפה")`);
  const capture = t.listeners.find((l) => l.type === 'click' && l.capture);
  let prevented = false;
  capture.fn({target: {closest: (sel) => (sel.startsWith('a[href') ? {} : null)}, preventDefault() { prevented = true; }, stopPropagation() {}});
  assert.ok(prevented);
  assert.ok(t.toasts.some((m) => /קבצים/.test(m)));
});

test('the monitor viewer of a secondary\'s machine goes through the secondary (#1129, node=)', async () => {
  const t = setup(both());
  await draw(t, 'monitor', 0, true);
  t.run('MON_SEL="aa:bb:cc:dd:ee:01"');
  const html = t.run('layout(pages.monitor, 0)');
  assert.match(html, /monitor\.html\?mac=aa%3Abb%3Acc%3Add%3Aee%3A01&amp;name=B1&amp;node=n0de|monitor\.html\?mac=aa%3Abb%3Acc%3Add%3Aee%3A01&name=B1&node=n0de/);
  t.run('setServerContext(null)');
  await draw(t, 'monitor', 0, false);
  t.run('MON_SEL="aa:bb:cc:dd:ee:01"');
  assert.doesNotMatch(t.run('layout(pages.monitor, 0)'), /node=/);
});

test('a secondary that did not answer: the page says "לא נקרא" with the reason from the relay — not an empty page', async () => {
  const down = both();
  for (const k of Object.keys(DATA)) down[RELAY + k] = new Error('502');
  const t = setup(down);
  const health = await draw(t, 'health', 0, true);
  assert.match(health, /לא נקרא/);
  assert.match(health, /שגיאה מ-/);
  const drivers = await draw(t, 'drivers', 0, true);
  assert.match(drivers, /לא נקרא/);
});

// What the relay sends after peer_safe.defang: no string carries <>"'`&. A hostile
// secondary can still send objects where the page expects lists — the page must not
// break and must not render markup.
const DEFANGED = '‹img src=x onerror=alert(1)›';
test('a hostile (defanged) secondary answer renders as text, and a shape the page does not know does not break the console', async () => {
  const hostile = both();
  hostile[RELAY + '/drivers'] = [{name: DEFANGED, description: DEFANGED, files: {length: DEFANGED}, matches: DEFANGED,
    match: [{pci: [DEFANGED]}, {vendor: DEFANGED, model: DEFANGED}]}];
  hostile[RELAY + '/health'] = [{id: DEFANGED, label: DEFANGED, state: DEFANGED, detail: DEFANGED}];
  hostile[RELAY + '/users'] = [{username: DEFANGED, role: {length: DEFANGED}}];
  // drivers tab 1 = the packages; the other tabs are computed from the machines.
  for (const [page, tab, shows] of [['drivers', 0, false], ['drivers', 1, true], ['drivers', 2, false],
    ['health', 0, true], ['permissions', 0, true]]) {
    const t = setup(hostile);
    const html = await draw(t, page, tab, true);
    assert.doesNotMatch(html, /<img/, `${page} ${tab}`);
    if (shows) assert.ok(html.includes(DEFANGED) || /לא הצלחתי לצייר את העמוד/.test(html), `${page} ${tab}: shown as text or "לא נקרא"`);
  }
  // a list where an object was expected — the page says it could not draw, it does not throw
  const broken = both();
  broken[RELAY + '/drivers'] = [{name: 'x', match: {length: 1}}];
  const t = setup(broken);
  const html = await draw(t, 'drivers', 1, true);
  assert.ok(typeof html === 'string' && html.length > 0);
});

// ---------- #1008: the primary's monitor lists the secondaries' machines too ----------

test('#1008: the primary\'s monitor lists each secondary\'s machines with the server\'s name; one that did not answer is "לא נקרא" with the reason', async () => {
  const f = both();
  f['/storage-nodes'] = [{id: 'n0de', label: 'חיפה'}, {id: 'off1', label: 'באר שבע', disabled_at: '2026-10-01T00:00:00+00:00'},
    {id: 'dn', label: 'אילת'}];
  f['/storage-nodes/n0de/machines'] = {connected: true, error: null, machines: [
    {mac: 'aa:bb:cc:dd:ee:21', name: 'H21', role: 'cloner', ip: '10.30.0.21', online: true}]};
  f['/storage-nodes/dn/machines'] = {connected: false, error: 'connect timed out', machines: []};
  const t = setup(f);
  const html = await draw(t, 'monitor', 0, false);
  assert.match(html, /data-mac="aa:bb:cc:dd:ee:01"/, 'the local machine is still there');
  assert.match(html, /data-mac="aa:bb:cc:dd:ee:21" data-node="n0de"/);
  // ‏#1300: מחובר = כפתור (שם · שרת · IP), כמו המוקאפ
  assert.match(html, /<b>H21<\/b> <span class="muted" data-server>· חיפה<\/span><\/span><span class="mono">10\.30\.0\.21<\/span>/);
  assert.match(html, /data-node="dn" data-unread>.*?אילת.*?לא נקרא<\/span><span class="sub">connect timed out/);
  assert.match(html, /data-node="off1" data-unread>.*?באר שבע.*?השרת מושבת — לא נשאל/);
  assert.ok(!t.calls.some((c) => c.url.includes('/storage-nodes/off1/')), 'a disabled secondary is not asked');
  const row = html.slice(html.indexOf('data-node="n0de"'), html.indexOf('</tr>', html.indexOf('data-node="n0de"')));
  assert.doesNotMatch(row, /wakeMachine/, 'no WoL for a secondary\'s machine (it does not go through the relay)');
  t.run('MON_SEL="n0de|aa:bb:cc:dd:ee:21"');
  assert.match(t.run('layout(pages.monitor, 0)'), /monitor\.html\?mac=aa%3Abb%3Acc%3Add%3Aee%3A21&name=H21&node=n0de/);
});

test('#1008: a request to the primary for the secondaries that failed is one "לא נקרא" row — not silence', async () => {
  const f = both();
  f['/storage-nodes'] = new Error('500');
  const t = setup(f);
  const html = await draw(t, 'monitor', 0, false);
  assert.match(html, /data-unread>.*?שרתי המשנה.*?לא נקרא/);
});

test('#1008: in the secondary\'s context the monitor is that server\'s list only — no branches are asked', async () => {
  const t = setup(both());
  await draw(t, 'monitor', 0, true);
  assert.ok(!t.calls.some((c) => /\/api\/console\/storage-nodes(\?|$)/.test(c.url)));
});


// ---------- #1179: the tree's sub-nodes are filled for the secondary as for the primary ----------

test('in the secondary\'s context the sub-trees (groups+machines, folders+images, NICs) go into its branch, and a click there enters its context first', async () => {
  const f = both();
  // two build groups: the group level shows (with one, the gate's tree lists the machines under the role)
  f[RELAY + '/groups'] = [{id: 'grp_BUILD', label: 'בנייה חיפה', role: 'build'}, {id: 'grp_B2', label: 'בנייה חיפה 2', role: 'build'}, {id: 'grp_CLONERS', label: 'שיכפול חיפה', role: 'cloner'}];
  f[RELAY + '/machines'] = [{mac: 'aa:bb:cc:dd:ee:31', name: 'H31', group_id: 'grp_BUILD', role: 'build'}];
  f[RELAY + '/folders'] = [{name: 'Windows'}];
  f[RELAY + '/images'] = [{id: 'img_h1', name: 'Haifa Win', folder: 'Windows'}];
  const t = setup(f);
  t.run('document.getElementById("buildTree").innerHTML = "PRIMARY"; document.getElementById("imagesTree").innerHTML = "PRIMARY"');
  t.run(`setServerContext("${NODE}", "חיפה")`);
  await t.run('loadMachines()');
  await t.run('loadImages()');
  const build = t.run(`document.getElementById("srv-${NODE}-buildTree").innerHTML`);
  assert.match(build, /בנייה חיפה/);
  assert.match(build, /H31/);
  assert.match(build, /onclick="inSecondary\('n0de',\(\)=>\{openMachineDetail\('aa%3Abb%3Acc%3Add%3Aee%3A31'\)\}\)"/);
  assert.match(build, /id="srv-n0de-sg-grp_BUILD"/, 'no id clash with the primary tree');
  const images = t.run(`document.getElementById("srv-${NODE}-imagesTree").innerHTML`);
  assert.match(images, /Haifa Win/);
  assert.match(images, /inSecondary\('n0de',\(\)=>\{openImageDetail\('img_h1'\)\}\)/);
  assert.equal(t.run('document.getElementById("buildTree").innerHTML'), 'PRIMARY', 'the primary tree is untouched');
  assert.equal(t.run('document.getElementById("imagesTree").innerHTML'), 'PRIMARY');
  // back on the primary the same functions fill the primary tree, with plain clicks
  t.run('setServerContext(null); GROUPS=[{id:"grp_BUILD",label:"בנייה ראשי",role:"build"},{id:"grp_X",label:"בנייה ראשי 2",role:"build"}]; MACHINES=[]; populateSidebarGroups()');
  assert.match(t.run('document.getElementById("buildTree").innerHTML'), /בנייה ראשי/);
  assert.doesNotMatch(t.run('document.getElementById("buildTree").innerHTML'), /inSecondary/);
});

test('a click on a secondary sub-node from the primary\'s context enters the secondary first (an old node never acts on another server)', () => {
  const t = setup(both());
  assert.equal(t.run('SERVER_CTX'), null);
  t.run(`__seen = null; inSecondary("${NODE}", () => { __seen = SERVER_CTX; })`);
  assert.equal(t.run('__seen'), NODE);
});

test('openNetwork / selectMachinesRole from inside the secondary keep its context (they look up the primary\'s node); a primary-only page leaves it', () => {
  const t = setup(both());
  t.run(`setServerContext("${NODE}", "חיפה")`);
  t.run('selectPage({dataset: {page: "network"}, closest: () => null}, "network")');
  assert.equal(t.run('SERVER_CTX'), NODE);
  t.run('selectMachinesRole("build")');
  assert.equal(t.run('SERVER_CTX'), NODE);
  assert.equal(t.run('current'), 'machines');
  t.run('selectPageById("account")');
  assert.equal(t.run('SERVER_CTX'), null, 'the account is the primary user\'s');
});


// ---------- the gate's new pages and functions (11/10) in the secondary's context ----------

test('global search in the secondary\'s context searches the secondary (its lists come through the relay)', async () => {
  const f = both();
  f[RELAY + '/images'] = [{id: 'img_h1', name: 'Haifa Win', folder: ''}];
  f[RELAY + '/machines'] = [{mac: 'aa:bb:cc:dd:ee:31', name: 'H31', group_id: 'grp_BUILD'}];
  const t = setup(f);
  t.run('IMAGES=[{id:"img_p",name:"Primary Only"}]; GS.failed=["x"]');
  t.run(`setServerContext("${NODE}", "חיפה")`);
  assert.equal(t.run('IMAGES'), null, 'the primary\'s list is gone');
  assert.equal(JSON.stringify(t.run('GS.failed')), '[]', 'the other server\'s failures are forgotten');
  const before = t.calls.length;
  t.run('gsEnsureData()');
  await new Promise((r) => setImmediate(r));
  const urls = t.calls.slice(before).map((c) => c.url);
  assert.ok(urls.length >= 2, JSON.stringify(urls));
  for (const u of urls) assert.ok(u.startsWith(`/api/console${RELAY}/`), u);
  assert.ok(!urls.some((u) => u.endsWith('/storage-nodes')), 'a secondary has no branches to search');
});

test('answering a SMART question in the secondary\'s context goes to the relay — never to the primary\'s route (the relay refuses it: not in CONSOLE_WRITES)', async () => {
  const t = setup(both());
  t.run(`setServerContext("${NODE}", "חיפה")`);
  const before = t.calls.length;
  await t.run('post("/room/answer", {mac: "aa:bb", nonce: "n", answer: "continue"})');
  const sent = t.calls.slice(before);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, `/api/console${RELAY}/room/answer`);
});
