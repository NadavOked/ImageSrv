// #936 — שרתי המשנה בעץ הניווט נבנים מ-GET /storage-nodes (כמו vCenter):
// צומת שרת לכל שרת משנה עם נקודת מצב (ירוק מחובר / אדום לא ענה / אפור מושבת),
// ומתחתיו אותו עץ כמו הראשי (#1179) — סקירה כללית · סבב הפצה · מלאי ▸ · תשתית ▸ · ניהול ▸ —
// שכל עלה בו הוא העמוד של הראשי בהקשר שרת המשנה (secondary_context.test.cjs), ו"העברות"
// הוא דף `branch` (branches.js).
// קליק = מציג, דאבל-קליק = פותח/סוגר (#931).
// console.js + branches.js רצים ב-vm עם fetch מזויף, כמו branches_page.test.cjs.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', className: '', attrs: {}, dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute(k, v) { this.attrs[k] = String(v); },
  removeAttribute(k) { delete this.attrs[k]; }, hasAttribute(k) { return k in this.attrs; }, focus() {}});

function setup(fixtures, me = {}, slow = null) {
  const calls = [];
  const toasts = [];
  const registry = new Map();
  // getElementById("x") ו-querySelector("#x") הם אותו אלמנט.
  // אלמנט בתוך הדף של שרת המשנה קיים רק אם ה-HTML הנוכחי של #branch-view מכיל
  // אותו — כמו בדפדפן, שם querySelector על id מלשונית שכבר הוחלפה מחזיר null.
  const node = (sel) => {
    sel = sel.replace(/^#/, '');
    if (sel.startsWith('branch-') && sel !== 'branch-view' && registry.has('branch-view')
        && !registry.get('branch-view').innerHTML.includes(`id="${sel}"`)) return null;
    if (!registry.has(sel)) registry.set(sel, stubNode());
    return registry.get(sel);
  };
  registry.set('#login-form', {...stubNode(), addEventListener() {}});
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: node, getElementById: node,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options = {}) => {
      calls.push({url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
      const key = url.replace('/api/console', '').split('?')[0];
      if (slow && key.includes(slow)) await new Promise((r) => setTimeout(r, 30));   // host timer, not the stubbed one
      if (fixtures[key] instanceof Error) return {status: Number(fixtures[key].message) || 500, ok: false, headers: {get: () => null}, json: async () => ({detail: 'אסור'})};
      if (options.method && options.method !== 'GET') return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({ok: true})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key] || {}};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'drivers.js'), 'utf8'), ctx);   // before branches.js, as in index.html
  vm.runInContext(fs.readFileSync(path.join(root, 'branches.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  ctx.__toasts = toasts;
  const meJson = JSON.stringify({username: 'admin', role: 'admin', idle_seconds: 300, server_name: 'imagesrv-main',
    capabilities: {interbranch_transfer: true, enroll_secondary: true}, ...me});
  run(`ME=${meJson}; toast=(m)=>__toasts.push(m); current="home"; currentTab=0;`);
  return {run, calls, toasts, node};
}

const NODE = 'n0de';
const base = {
  '/storage-nodes': [
    {id: NODE, label: 'חיפה', base_url: 'https://10.30.0.8:8443/api/interserver/v1', group_id: null, group_label: null, enrolled_at: '2026-09-16T09:00:00+00:00', disabled_at: null},
    {id: 'off1', label: 'באר שבע', base_url: 'https://10.40.0.8:8443/api/interserver/v1', group_id: null, group_label: 'דרום', enrolled_at: '2026-09-10T09:00:00+00:00', disabled_at: '2026-09-12T09:00:00+00:00'},
  ],
  [`/storage-nodes/${NODE}/machines`]: {connected: true, error: null, node_id: 'sn_0123456789abcdef', machines: [
    {mac: 'aa:bb:cc:dd:ee:01', name: 'Builder-H', role: 'build', ip: '10.30.0.77', online: true},
    {mac: 'aa:bb:cc:dd:ee:02', name: 'Cloner-H', role: 'cloner', ip: null, online: false},
  ]},
  '/storage-transfers': [
    {id: 't1', node_id: NODE, image_id: 'img_7f3a91', image_name: 'Office 365', state: 'done', bytes_sent: 5, bytes_total: 5, error: null, created_at: '2026-09-16T10:00:00+00:00', updated_at: '2026-09-16T10:20:00+00:00'},
    {id: 't2', node_id: NODE, image_id: 'img_7f3a91', image_name: 'Office 365', state: 'done', bytes_sent: 5, bytes_total: 5, error: null, created_at: '2026-09-15T10:00:00+00:00', updated_at: '2026-09-15T10:20:00+00:00'},
    {id: 't3', node_id: NODE, image_id: 'img_2c8e04', image_name: 'Old', state: 'failed', bytes_sent: 0, bytes_total: 5, error: 'sha256 לא תואם', created_at: '2026-09-14T10:00:00+00:00'},
  ],
  '/images': [{id: 'img_7f3a91', name: 'Office 365', folder: 'Windows', total_compressed_bytes: 12 * 1024 ** 3}],
  [`/storage-nodes/${NODE}/console/storage-locations`]: {locations: [
    {id: 'loc_local', name: 'דיסק השרת', state: 'connected'},
    {id: 'loc_1234abcd', name: 'דיסק 200GB', state: 'connected'},
  ]},
  [`/storage-nodes/${NODE}/images`]: {connected: true, error: null, images: [
    {id: 'img_c0ffee', name: 'Haifa Local', size_bytes: 40, created_at: '2026-09-18T08:00:00+00:00', sha256: {}},
    {id: 'img_7f3a91', name: 'Office 365', size_bytes: 40, created_at: '2026-09-16T10:00:00+00:00', sha256: {}},
  ]},
};

test('a server node per secondary from /storage-nodes, with the tree under it; click shows, dblclick toggles', async () => {
  const {run, node, calls} = setup(base);
  await run('populateSidebarSecondaries()');
  const html = node('#secondaryServers').innerHTML;
  assert.ok(calls.some((c) => c.url.endsWith('/storage-nodes')));
  assert.match(html, /class="inventory-node server-node" data-secondary="n0de"/);
  assert.match(html, /<strong class="server-name">חיפה<\/strong>/);
  assert.match(html, /<strong class="server-name">באר שבע<\/strong>/, 'a disabled secondary is still in the tree');
  assert.doesNotMatch(html, /יוגדר בהמשך|תל אביב/);
  // #931 + the gate's tree (owner 11/10): one click opens the secondary's overview (in its context), the next toggles; the arrow toggles alone.
  assert.match(html, /data-secondary="n0de"[^>]*onclick="treeNavToggle\(this,'srv-n0de',function\(\)\{openSecondaryPage\('n0de','home',this\)\}\)"/);
  assert.match(html, /<span class="tree-arrow" data-open="false" onclick="event\.stopPropagation\(\);toggleInventoryGroup\(this\.closest\('\.inventory-node'\),'srv-n0de'\)">▸<\/span>/);
  assert.match(html, /<div id="srv-n0de" class="inventory-children" role="group" hidden>/, 'closed by default');
  for (const [page, label] of [['home', 'סקירה כללית'], ['machines', 'מחשבים'], ['images', "אימג&#39;ים"], ['health', 'בריאות ושירותים'], ['permissions', 'משתמשים והרשאות']]) {
    const re = new RegExp(`data-branch-page="${page}" data-branch-node="n0de" role="treeitem" tabindex="0"(?: aria-expanded="(?:true|false)")? onclick="(?:treeNavToggle\\(this,'srv-n0de-[A-Za-z]+',function\\(\\)\\{)?openSecondaryPage\\('n0de','${page}',this\\)[^"]*"[^>]*>.*?<span>${label}</span>`);
    assert.match(html, re, `child ${page}`);
  }
  assert.match(html, /data-branch-view="transfers" data-branch-node="n0de" role="treeitem" tabindex="0" onclick="openBranchView\('n0de','transfers',this\)">.*?<span>העברות<\/span>/);
  assert.match(html, /role="treeitem" tabindex="0" aria-expanded="false" onclick="treeNavToggle\(this,'srv-n0de',function\(\)\{openSecondaryPage\('n0de'/, 'server node is a focusable treeitem');
});

test('the status dot is measured against the secondary: green only when it answered and its health is clean, red with the reason when not, grey when disabled', async () => {
  const {run, node, calls} = setup(base);
  await run('populateSidebarSecondaries()');
  const dot = node('[data-secondary-status="n0de"]');
  // נדב 18/10: "מחובר" לבדו אינו ירוק — הבריאות שלו לא נקראה עדיין (tree_server_dot.test.cjs)
  assert.equal(dot.className, 'status unk');
  assert.equal(dot.attrs.title, 'מצב השירותים לא נבדק');
  run('noteServerHealth("n0de", [{id: "server", label: "שרת", state: "ok"}], null)');
  assert.equal(dot.className, 'status ok');
  assert.equal(dot.attrs.title, 'מחובר · כל השירותים פעילים');
  assert.ok(!calls.some((c) => c.url.includes('/storage-nodes/off1/machines')), 'a disabled secondary is not asked');
  assert.match(node('#secondaryServers').innerHTML, /data-secondary-status="off1" title="מושבת"/);

  const down = setup({...base, [`/storage-nodes/${NODE}/machines`]: {connected: false, error: 'החיבור נסגר לפני סוף הכותרות', machines: []}});
  await down.run('populateSidebarSecondaries()');
  assert.match(down.node('#secondaryServers').innerHTML, /חיפה/, 'an unreachable secondary does not vanish');
  const redDot = down.node('[data-secondary-status="n0de"]');
  assert.equal(redDot.className, 'status err');
  assert.equal(redDot.attrs.title, 'לא מחובר: החיבור נסגר לפני סוף הכותרות');
  assert.equal(redDot.attrs['aria-label'], redDot.attrs.title);
  assert.equal(down.toasts.length, 0, 'not a red error toast');
});

// TA 18/10 (d8763e5b): Haifa (v0.75.0) stayed grey "לא נבדק" while TA's log had the relay's /health 200.
// /health as v0.75.0 answers it (server/system/health.py at the tag: check() = {id,label,state,detail}).
const HEALTH_V075 = [
  {id: 'time_sync', label: 'סנכרון שעון', state: 'ok', detail: 'NTP פעיל והשעון מסונכרן'},
  {id: 'deploy_net', label: 'הגדר את רשת ההפצה', state: 'warn', detail: 'רשת ההפצה לא הוגדרה — בחר כרטיס בלשונית רשת הפצה והגדר את הרשת שלו; כתובת השרת נגזרת ממנה (#1263)'},
  {id: 'tftp_port', label: 'פורט 69 (TFTP)', state: 'not_applicable', detail: 'לא רלוונטי עדיין'},
];
test('TA 18/10: the login flow (tree, then the dots) paints a v0.75 secondary orange from its /health through the relay -- also when the poll was already measuring TA', async () => {
  const fx = {...base, '/health': [{id: 'server', label: 'שרת', state: 'ok', detail: ''}], [`/storage-nodes/${NODE}/console/health`]: HEALTH_V075};
  const {run, node, calls} = setup(fx, {}, '/health');   // /health answers late, like a real relay
  const poll = run('refreshServerDots()');   // the 2s poll ticks while the tree is still being built: TA only
  await run('populateSidebarSecondaries().then(() => refreshServerDots())');   // showApp
  await poll;
  await new Promise((r) => setTimeout(r, 80));
  assert.ok(calls.some((c) => c.url === `/api/console/storage-nodes/${NODE}/console/health`), 'the relay was asked');
  const dot = node('[data-secondary-status="n0de"]');
  assert.equal(dot.className, 'status warn', 'orange, as its overview says');
  assert.match(dot.attrs.title, /רשת ההפצה לא הוגדרה/);
});

test('without the capability (deploy, or a secondary server) no node is built and /storage-nodes is not called; a 403 is not a red error', async () => {
  const deploy = setup(base, {role: 'deploy', capabilities: {interbranch_transfer: false, enroll_secondary: false}});
  await deploy.run('populateSidebarSecondaries()');
  assert.equal(deploy.node('#secondaryServers').innerHTML, '');
  assert.ok(!deploy.calls.some((c) => c.url.includes('/storage-nodes')));
  assert.equal(deploy.run('pageAllowed("branch")'), false);

  const forbidden = setup({...base, '/storage-nodes': new Error('403')});
  await forbidden.run('populateSidebarSecondaries()');
  assert.equal(forbidden.node('#secondaryServers').innerHTML, '');
  assert.equal(forbidden.toasts.length, 0);

  const broken = setup({...base, '/storage-nodes': new Error('500')});
  await assert.rejects(broken.run('populateSidebarSecondaries()'), /אסור/, 'a real failure still surfaces');
});

test('the primary server node takes its name from /me (server_name); a double click opens the one rename window (POST /hostname)', async () => {
  const {run, node, calls} = setup(base);
  run('applyServerName()');
  assert.equal(node('#primaryServerName').textContent, 'imagesrv-main');
  // נדב 17/10: שם אחד = שם המארח, ומקום אחד לשנות אותו — אותו חלון כמו "שינוי שם…" בהגדרות.
  run('globalThis.__sheets=[]; sheet=(o)=>__sheets.push(o); renameServer(document.getElementById("primaryServerName"))');
  const s = run('__sheets.at(-1)');
  assert.equal(s.title, 'שינוי שם השרת');
  assert.equal(s.danger, true, 'the rename restarts the server — a red button');
  await s.onSubmit({hostname: 'imagesrv-ta'});
  const save = calls.find((c) => c.method === 'POST' && c.url.endsWith('/hostname'));
  assert.ok(save, 'the name is the host name — POST /hostname, not a separate display setting');
  assert.ok(!calls.some((c) => c.method === 'POST' && c.url.endsWith('/settings')), 'no POST /settings server_name any more');
  assert.equal(JSON.stringify(save.body), JSON.stringify({hostname: 'imagesrv-ta'}));
  assert.doesNotMatch(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), /imagesrv-server-names/, 'no localStorage-only rename');
});

test('"העברות" is a primary page about the secondary: openBranchView leaves the secondary context and opens the branch page', async () => {
  const {run} = setup(base);
  assert.equal(run('pageAllowed("branch")'), true);
  assert.equal(JSON.stringify(run('pages.branch.tabs')), '[]');
  run('closeSidebar=()=>{}; setServerContext("n0de", "חיפה"); openBranchView("n0de", "transfers", null)');
  assert.equal(run('SERVER_CTX'), null, 'the branch page talks to the primary');
  assert.equal(run('current'), 'branch');
  assert.equal(run('BRANCH_NODE'), 'n0de');
  assert.match(run('tabRender("branch", 0)'), /id="branch-view" class="stack" data-view="transfers"/);
});

test('the transfers page: the secondary library with "העבר לראשי", "העבר אימג\'" and the transfers log', async () => {
  const {run, node} = setup(base);
  run('BRANCH_NODE="n0de"; current="branch"');
  await run('loadBranchView()');
  const html = node('#branch-view').innerHTML;
  assert.match(html, /id="branch-view-transfer"/);
  assert.match(html, /ספריית האימג'ים של שרת המשנה/);
  assert.match(html, /Haifa Local/);
  assert.match(html, /העבר לראשי/);
  assert.match(html, /class="status ok"><i><\/i>מחובר/);
  const log = node(`#branch-transfers-${NODE}`).innerHTML;
  assert.match(log, /Office 365/);
  assert.match(log, /נכשל/);
  assert.match(log, /sha256 לא תואם/);
});

test('a secondary that does not answer: the transfers page says "חיפה לא ענה" with the reason, and the branch turns red', async () => {
  const reason = 'connect timed out';
  const {run, node} = setup({...base, [`/storage-nodes/${NODE}/images`]: {connected: false, error: reason, images: []}});
  await run('populateSidebarSecondaries()');
  run('BRANCH_NODE="n0de"; current="branch"');
  await run('loadBranchView()');
  assert.match(node('#branch-view').innerHTML, /חיפה לא ענה: connect timed out/);
  assert.equal(node('[data-secondary="n0de"]').attrs['data-conn'], 'err');
});

test('when the request to the primary fails, the transfers page says "לא נבדק" — not that the secondary did not answer (5א)', async () => {
  const {run, node} = setup({...base, [`/storage-nodes/${NODE}/images`]: new Error('500')});
  run('BRANCH_NODE="n0de"; current="branch"');
  await run('loadBranchView()');
  assert.match(node('#branch-view').innerHTML, /לא נבדק אם חיפה עונה — הבקשה לראשי נכשלה: אסור/);
  assert.doesNotMatch(node('#branch-view').innerHTML, /חיפה לא ענה/);
});

test('a stale load (another secondary chosen during the await) does not write into a DOM that is gone', async () => {
  const {run, node, toasts} = setup(base, {}, '/storage-nodes/n0de/images');
  run('closeSidebar=()=>{}');
  run('openBranchView("n0de", "transfers", null); openBranchView("off1", "transfers", null)');
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(toasts.length, 0, 'no error toast: ' + JSON.stringify(toasts));
  assert.doesNotMatch(node('#branch-view').innerHTML, /Haifa Local/, 'the slow load did not overwrite the newer page');
});

test('"התחל העברה" and "העבר לראשי" start the transfer and stay on the transfers page', async () => {
  const {run, node, calls} = setup(base);
  run('BRANCH_NODE="n0de"; current="branch"; closeSidebar=()=>{}; __submit=null; sheet=(o)=>{ __submit=o.onSubmit; }');
  await run('loadBranchView()');
  await run('document.querySelector("#branch-view-transfer").onclick()');
  await run('__submit({image_id: "img_7f3a91", location_id: "loc_1234abcd"})');
  const push = calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/n0de/transfer'));
  assert.ok(push && push.body.image_id === 'img_7f3a91' && push.body.location_id === 'loc_1234abcd',
    'the transfer was started with the selected secondary location');
  await run('startPullFromSecondary("n0de","img_c0ffee")');
  const pull = calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/n0de/pull'));
  assert.ok(pull && pull.body.image_id === 'img_c0ffee', 'the pull was started');
  await new Promise((r) => setTimeout(r, 20));
  assert.match(node('#branch-view').innerHTML, /branch-transfers-n0de/);
});

// ---------- #1179: the secondary's tree = the primary's tree ----------

// Reads a sidebar tree out of HTML: every .inventory-node (it holds no <div>), its
// label (the <strong> server name, else the last plain <span>text</span>), whether it
// starts hidden, and the .inventory-children box that follows it as its kids.
function readTree(html) {
  const root = {kids: []};
  const boxes = [root];
  const open = [];
  let last = null;
  const re = /<div\b([^>]*)>|<\/div>/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[0] === '</div>') { if (open.pop() === 'box') boxes.pop(); continue; }
    const attrs = m[1];
    if (/class="inventory-node/.test(attrs)) {
      const inner = html.slice(re.lastIndex, html.indexOf('</div>', re.lastIndex));
      const strong = inner.match(/<strong[^>]*>([^<]*)<\/strong>/);
      const spans = [...inner.matchAll(/<span>([^<]*)<\/span>/g)];
      const label = (strong ? strong[1] : spans.length ? spans[spans.length - 1][1] : '').replaceAll('&#39;', "'");
      last = {label, hidden: /class="inventory-node[^"]*\bhidden\b/.test(attrs), kids: []};
      boxes[boxes.length - 1].kids.push(last);
      open.push('node');
    } else if (/class="inventory-children/.test(attrs)) {
      boxes.push(last);
      open.push('box');
    } else open.push('other');
  }
  return root.kids;
}

// The tree down to the page level: groups with their pages. Under a page (image
// folders, machine roles, NICs) is that page's own content, not the tree. What starts
// hidden behind a capability (ארגז כלים in v1) is out.
const skeleton = (nodes, depth = 2) => nodes.filter((x) => !x.hidden)
  .map((x) => depth > 1 && x.kids.length ? [x.label, skeleton(x.kids, depth - 1)] : x.label);

test('the secondary\'s tree is the primary\'s tree — same groups, pages and sub-nodes, same order — apart from the server label (+ its transfers; ניהול without the primary-only pages)', async () => {
  const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const sidebar = index.slice(index.indexOf('<div class="tree" role="tree"'), index.indexOf('<div id="secondaryServers"'));
  const [primary] = readTree(sidebar);
  // down to the sub-nodes (Nadav: the same tree): מחשבים ▸ בנייה/שיכפול, רשת ▸ תרשים/חיבורים פיזיים/רשת הפצה/פורטים.
  const want = ['סקירה כללית', 'סבב הפצה', ['מלאי', ["אימג'ים", ['מחשבים', ['מחשבי בנייה', 'מחשבי שיכפול']], 'דיסקים']],
    ['תשתית', ['בריאות ושירותים', 'אחסון', ['רשת', ['תרשים', 'חיבורים פיזיים', 'רשת הפצה', 'פורטים']], 'מוניטור', 'דרייברים']],
    ['ניהול', ['משתמשים והרשאות', 'יומן', 'הגדרות', 'החשבון שלי']]];   // סניפים starts hidden (capability)
  assert.equal(JSON.stringify(skeleton(primary.kids, 3)), JSON.stringify(want), 'the reader sees the primary tree (a parser that sees nothing proves nothing)');

  const {run, node} = setup(base);
  await run('populateSidebarSecondaries()');
  const [haifa, beersheva] = readTree(node('#secondaryServers').innerHTML);
  assert.equal(haifa.label, 'חיפה');
  assert.notEqual(haifa.label, primary.label, 'the label is the only difference');
  // "סניפים" (a secondary has no children) and "החשבון שלי" (the primary user's) are not the server's pages.
  const expected = skeleton(primary.kids, 3).map((x) => !Array.isArray(x) ? x
    : x[0] === 'מלאי' ? [x[0], [...x[1], 'העברות']]
    : x[0] === 'ניהול' ? [x[0], x[1].filter((k) => k !== 'סניפים' && k !== 'החשבון שלי')] : x);
  assert.equal(JSON.stringify(skeleton(haifa.kids, 3)), JSON.stringify(expected));
  assert.equal(JSON.stringify(skeleton(beersheva.kids, 3)), JSON.stringify(expected), 'a disabled secondary has the same tree');
  // like the primary (Nadav 18/10, the first login): מלאי, תשתית and ניהול all start closed.
  assert.match(node('#secondaryServers').innerHTML, /<div id="srv-n0de-inv" class="inventory-children" role="group" hidden>/);
  assert.match(node('#secondaryServers').innerHTML, /<div id="srv-n0de-infra" class="inventory-children" role="group" hidden>/);
  assert.match(node('#secondaryServers').innerHTML, /<div id="srv-n0de-admin" class="inventory-children" role="group" hidden>/);
});
