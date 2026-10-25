// #954 גל 7 — דף "סניפים" (רשימת שרתי המשנה): כותרת אובייקט עם מונים ותג "N
// לא מגיב/ים", טבלת שרתים עם חיבור **נמדד** בפועל (לא "בודק…" קפוא),
// לשונית העברות עם התקדמות/מצב/נסה שוב, לשונית קבוצות, ורישום שרת משנה
// (preview SPKI → קוד). מוניטור למכונת שרת המשנה נשאר דרך monitor.html?node=.
// console.js + branches.js + storage_nodes.js (קבוצות) + reorder.js רצים
// ב-vm עם fetch מזויף, כמו secondaries_tree.test.cjs.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', className: '', dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

function setup(fixtures, me = {}) {
  const opened = [];
  const toasts = [];
  const calls = [];
  const registry = new Map();
  const node = (sel) => {
    sel = String(sel).replace(/^#/, '');
    if (!registry.has(sel)) registry.set(sel, stubNode());
    return registry.get(sel);
  };
  const listeners = {};
  // #1085 שלב ב': הכניסה עברה מבינדינג ישיר על login-form לדלגציה על מיכל #login
  registry.set('login', {...stubNode(), addEventListener(type, fn) { listeners[type] = fn; }});
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: node, getElementById: node,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open: (...a) => opened.push(a)},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options = {}) => {
      calls.push({url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
      const key = url.replace('/api/console', '').split('?')[0];
      if (options.method === 'POST' && key === '/login') return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({username: 'noc', role: 'admin', idle_seconds: 300})};
      if (fixtures[key] instanceof Error) return {status: Number(fixtures[key].message) || 500, ok: false, headers: {get: () => null}, json: async () => ({detail: fixtures[key].message})};
      if (key in fixtures) return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key]};
      if (options.method && options.method !== 'GET') return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({id: 't1', ok: true})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({})};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'reorder.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'branches.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'storage_nodes.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  const meJson = JSON.stringify({timezone: 'UTC', username: 'admin', role: 'admin', idle_seconds: 300, capabilities: {interbranch_transfer: true, enroll_secondary: true}, ...me});
  run(`ME=${meJson}; toast=(m)=>__toasts.push(m); current="branches"; currentTab=0;`);
  ctx.__toasts = toasts;
  return {ctx, run, opened, toasts, calls, node, listeners};
}

const NODE = 'n0de';
const base = {
  '/storage-nodes': [{id: NODE, label: 'חיפה', node_id: 'sn_0123456789abcdef', base_url: 'https://10.20.0.30:8443/api/interserver/v1', group_id: null, group_label: null, enrolled_at: '2026-09-16T09:00:00+00:00', disabled_at: null}],
  '/storage-node-groups': [],
  '/storage-transfers': [
    {id: 't0', node_id: NODE, node_label: 'חיפה', image_id: 'img_7f3a91', image_name: 'Office 365', state: 'sending', bytes_sent: 3 * 1024 ** 3, bytes_total: 12 * 1024 ** 3, error: null, started_by: 'admin', created_at: '2026-09-16T10:00:00+00:00'},
    {id: 't9', node_id: NODE, node_label: 'חיפה', image_id: 'img_2c8e04', image_name: 'Old', state: 'failed', bytes_sent: 0, bytes_total: 5, error: 'אימות נכשל: p1 אינו תואם ל-sha256', started_by: 'admin', created_at: '2026-09-15T10:00:00+00:00'},
  ],
  '/images': [{id: 'img_7f3a91', name: 'Office 365', folder: 'Windows', total_compressed_bytes: 12 * 1024 ** 3}],
  [`/storage-nodes/${NODE}/machines`]: {connected: true, error: null, node_id: 'sn_0123456789abcdef', machines: [
    {mac: 'aa:bb:cc:dd:ee:01', name: 'Builder-H', role: 'build', ip: '10.20.0.77', online: true, last_capture: {state: 'running', image_name: 'Haifa capture'}},
    {mac: 'aa:bb:cc:dd:ee:02', name: 'Cloner-H', role: 'cloner', ip: null, online: false, disks: [{port: 1, smart: 'ok'}, {port: 2, smart: 'warn'}]},
  ]},
  [`/storage-nodes/${NODE}/console/room`]: {round: null, machines: [{mac: 'aa:bb:cc:dd:ee:02', awake: true, drawer_list: [{port: 1, smart: 'ok'}, {port: 2, smart: 'warn'}]}]},
  [`/storage-nodes/${NODE}/console/images`]: [{id: 'img_h1', name: 'Windows Haifa'}],
};

test('the branches page has three tabs: שרתים, העברות, קבוצות', () => {
  const {run} = setup(base);
  assert.equal(JSON.stringify(run('pages.branches.tabs')), JSON.stringify(['שרתים', 'העברות', 'קבוצות']));
  assert.equal(run('pageAllowed("branches")'), true);
  // 16/10: a primary/standalone without an active secondary still gets the page (to register the first)
  run('ME.capabilities.interbranch_transfer=false');
  assert.equal(run('pageAllowed("branches")'), true, 'enroll_secondary alone opens the page');
  run('ME.capabilities.enroll_secondary=false');
  assert.equal(run('pageAllowed("branches")'), false, 'a secondary server (neither capability): no page');
  run('ME.capabilities.enroll_secondary=true; ME.role="deploy"');
  assert.equal(run('pageAllowed("branches")'), false, 'deploy: no page');
});

test('16/10: with no secondary yet the page is an empty state with a prominent register button, no transfers', async () => {
  const {run, calls} = setup({...base, '/storage-nodes': [], '/storage-transfers': []},
    {capabilities: {interbranch_transfer: false, enroll_secondary: true}});
  await run('loadBranchesData()');
  assert.equal(run('pageAllowed("branches")'), true);
  for (const tab of [0, 1, 2]) {
    const html = run(`branchesPage(${tab})`);
    assert.match(html, /data-testid="branches-empty"/);
    assert.match(html, /אין עדיין שרת משנה/);
    assert.match(html, /data-testid="enroll-first" onclick="openEnrollSheet\(\)">\+ רישום שרת משנה</);
    assert.doesNotMatch(html, /העברות/, 'no transfers tab while there is no secondary');
    assert.doesNotMatch(html, /הגבלת קצב בין אתרים/);
  }
  run('__opened=null; sheet=(o)=>{ __opened=o; };');
  run('openEnrollSheet()');
  assert.match(run('__opened.title'), /רישום שרת משנה — שלב 1/);
  assert.ok(calls.find((c) => c.url.endsWith('/storage-nodes')));
});

test('16/10: once a secondary exists the page has its three tabs and the transfers come back', async () => {
  const {run} = setup(base, {capabilities: {interbranch_transfer: true, enroll_secondary: true}});
  await run('loadBranchesData()');
  const html = run('branchesPage(1)');
  assert.doesNotMatch(html, /data-testid="branches-empty"/);
  assert.match(html, /העברות/);
  assert.match(html, /הגבלת קצב בין אתרים/);
});

test('the servers tab lists each secondary with a measured connection, not a frozen "checking…"', async () => {
  const {run} = setup(base);
  await run('loadBranchesData()');
  const html = run('branchesPage(0)');
  assert.match(html, /חיפה/);
  assert.match(html, /sn_0123456789abcdef/);
  assert.match(html, /10\.20\.0\.30:8443/);
  assert.match(html, /class="status ok" id="branch-row-conn-n0de"><i><\/i>מחובר/);
  assert.doesNotMatch(html, /בודק חיבור/, 'the connection check already resolved before the row was read');
  assert.match(html, /Office 365/, 'the last transfer shows in the row');
  assert.match(html, /data-node-row="n0de"/);
  assert.match(html, /data-branch-control="n0de"/);
  assert.match(html, /Builder-H/); assert.match(html, /מחשב בנייה · מחובר/);
  assert.match(html, /קליטה פעילה · Haifa capture/);
  assert.match(html, /Cloner-H/); assert.match(html, /משכפל · מחובר/);
  assert.match(html, /SMART תקין/); assert.match(html, /SMART אזהרה/);
  assert.match(html, /פתח סבב/); assert.match(html, /מוניטור/);
});

test('opening a secondary round uses one of its images and only the console room proxy', async () => {
  const {run, calls} = setup(base);
  await run('loadBranchesData()');
  run('__sheet=null; sheet=(o)=>{ __sheet=o; };');
  run(`openBranchRound('${NODE}')`);
  assert.match(run('__sheet.title'), /פתיחת סבב ב-חיפה/);
  assert.equal(run('__sheet.fields[0].options[0].label'), 'Windows Haifa');
  await run('__sheet.onSubmit({image_id: "img_h1", target_drives: "2"})');
  const req = calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/n0de/console/room'));
  assert.deepEqual(req.body, {image_id: 'img_h1', target_drives: 2, expand_partition: false});
});

test('stopping a secondary round is a destructive sheet and posts its returned image name', async () => {
  const fixtures = {...base, [`/storage-nodes/${NODE}/console/room`]: {round: {state: 'active', wave_state: 'active', image_id: 'img_h1', image_name: 'Windows Haifa', target_drives: 2, written_drives: 1}, machines: []}};
  const {run, calls} = setup(fixtures);
  await run('loadBranchesData()');
  const html = run('branchesPage(0)');
  assert.match(html, /Windows Haifa/); assert.match(html, /משדר/); assert.match(html, /נכתבו 1 מתוך 2/);
  run('__sheet=null; sheet=(o)=>{ __sheet=o; };'); run(`stopBranchRound('${NODE}')`);
  assert.equal(run('__sheet.danger'), true); assert.equal(run('__sheet.fields'), undefined);
  await run('__sheet.onSubmit()');
  const req = calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/n0de/console/room/close'));
  assert.deepEqual(req.body, {confirm_name: 'Windows Haifa'});
});

test('an unreachable secondary shows the reason and the server-recorded time (#1017), not an empty page and not the browser clock', async () => {
  const fixtures = {...base, [`/storage-nodes/${NODE}/machines`]: {connected: false, error: 'החיבור נסגר לפני סוף הכותרות', machines: [],
    last_seen_at: '2026-09-18T07:30:00+00:00', last_error: 'החיבור נסגר לפני סוף הכותרות', last_error_at: '2026-09-19T08:12:00+00:00'}};
  const {run} = setup(fixtures);
  await run('loadBranchesData()');
  const html = run('branchesPage(0)');
  assert.match(html, /class="status err" id="branch-row-conn-n0de"><i><\/i>לא ענה — (19\/09\/2026 )?08:12<span class="sub">החיבור נסגר לפני סוף הכותרות<\/span><span class="sub">ענתה לאחרונה 18\/09\/2026 07:30<\/span>/,
    'the failure time and the last answer are the server\u2019s last_error_at / last_seen_at');
  assert.equal(run('pages.branches.tabs.length'), 3);
});

test('#1017: without server timestamps there is no time at all — never the browser clock', async () => {
  const fixtures = {...base, [`/storage-nodes/${NODE}/machines`]: {connected: false, error: 'timeout', machines: []}};
  const {run} = setup(fixtures);
  await run('loadBranchesData()');
  const html = run('branchesPage(0)');
  assert.match(html, /<i><\/i>לא ענה<span class="sub">timeout<\/span><\/span>/);
  assert.doesNotMatch(html, /לא ענה — \d\d:\d\d/, 'no last_error_at → no invented time');
});

test('#1017: a connected secondary shows "ענתה HH:MM" from last_seen_at; before the check the row shows what the server remembers', async () => {
  const fixtures = {...base,
    '/storage-nodes': [{...base['/storage-nodes'][0], last_seen_at: '2026-09-18T07:30:00+00:00', last_error: null, last_error_at: null}],
    [`/storage-nodes/${NODE}/machines`]: {...base[`/storage-nodes/${NODE}/machines`], last_seen_at: '2026-09-19T09:41:00+00:00', last_error: null, last_error_at: null}};
  const {run} = setup(fixtures);
  const p = run('loadBranchesData()');
  await new Promise((r) => setImmediate(r));
  const before = run('branchStatusHtml("n0de")');
  assert.match(before.html, /^<i><\/i>(בודק חיבור…<span class="sub">ענתה לאחרונה 18\/09\/2026 07:30<\/span>|מחובר · ענתה)/, 'remembered time from GET /storage-nodes while (or until) the check runs');
  await p;
  const html = run('branchesPage(0)');
  assert.match(html, /class="status ok" id="branch-row-conn-n0de"><i><\/i>מחובר · ענתה (19\/09\/2026 )?09:41</);
});

test('#1237: after a reload, what GET /storage-nodes remembers is shown where no live answer replaces it', async () => {
  // דטרמיניסטי, בלי תזמון: שרת משנה מושבת אינו נשאל כלל, ושרת משנה שהבקשה לראשי עצמו
  // נכשלה עליו נשאר עם מה שהשרת זכר. ‏null (לא נמדד) = בלי זמן, לא "ענתה".
  const row = base['/storage-nodes'][0];
  const fixtures = {...base,
    '/storage-nodes': [
      {...row, id: 'off1', label: 'אילת', disabled_at: '2026-09-20T10:00:00+00:00', last_seen_at: '2026-09-18T07:30:00+00:00', last_error: null, last_error_at: null},
      {...row, id: 'off2', label: 'עכו', disabled_at: '2026-09-20T10:00:00+00:00', last_seen_at: null, last_error: null, last_error_at: null},
      {...row, id: 'on1', label: 'חיפה', last_seen_at: '2026-09-17T06:15:00+00:00', last_error: null, last_error_at: null},
    ],
    '/storage-nodes/on1/machines': new Error('500')};
  const {run, calls} = setup(fixtures);
  await run('loadBranchesData()');
  assert.equal(calls.filter((c) => /storage-nodes\/off\d/.test(c.url)).length, 0, 'a disabled secondary is not asked');
  assert.match(run('branchStatusHtml("off1")').html, /^<i><\/i>מושבת<span class="sub">ענתה לאחרונה 18\/09\/2026 07:30<\/span>$/);
  assert.equal(run('branchStatusHtml("off2")').html, '<i></i>מושבת', 'never answered (null) → no time at all');
  const on = run('branchStatusHtml("on1")');
  assert.equal(on.cls, 'status err');
  assert.match(on.html, /<span class="sub">ענתה לאחרונה 17\/09\/2026 06:15<\/span>$/, 'the request to the primary failed — the remembered answer stays');
  assert.doesNotMatch(on.html, /לא ענה — /, 'the primary recorded no failure time, so none is shown');
});

test('#1017: the row action "בדוק חיבור" posts to …/check (ping only), keeps the machines count, and updates the cell from the response', async () => {
  const fixtures = {...base,
    [`/storage-nodes/${NODE}/check`]: {connected: false, error: 'ping נכשל (503)', checked_at: '2026-09-19T10:00:00+00:00', node_id: null, protocol_version: null,
      last_seen_at: '2026-09-19T09:41:00+00:00', last_error: 'ping נכשל (503)', last_error_at: '2026-09-19T10:00:00+00:00'},
    [`/storage-nodes/${NODE}/machines`]: {...base[`/storage-nodes/${NODE}/machines`], last_seen_at: '2026-09-19T09:41:00+00:00', last_error: null, last_error_at: null}};
  const {run, calls, node} = setup(fixtures);
  await run('loadBranchesData()');
  run('branchesPage(0)');
  await run('checkBranchNow("n0de")');
  const check = calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/n0de/check'));
  assert.ok(check, 'a dedicated check, not another /machines');
  assert.equal(calls.filter((c) => c.url.endsWith('/storage-nodes/n0de/machines')).length, 1, '/machines was read once, on load');
  const c = run('BRANCH_CONN.n0de');
  assert.equal(c.state, 'err'); assert.equal(c.lastErrorAt, '2026-09-19T10:00:00+00:00'); assert.equal(c.lastSeenAt, '2026-09-19T09:41:00+00:00');
  assert.equal(c.machines.length, 2, 'the machines count from the load is kept — /check does not fetch machines');
  const cell = node('branch-row-conn-n0de');
  assert.equal(cell.className, 'status err');
  assert.match(cell.innerHTML, /לא ענה — (19\/09\/2026 )?10:00<span class="sub">ping נכשל \(503\)<\/span><span class="sub">ענתה לאחרונה (19\/09\/2026 )?09:41<\/span>/);
  const html = run('branchesPage(0)');
  assert.match(html, /onclick="checkBranchNow\('n0de'\)">בדוק חיבור</);
  assert.match(html, /id="branch-row-machines-n0de">2</, 'the count survives a failed ping');
});

test('the header shows a count and "לא מגיב" tag when a secondary did not answer', async () => {
  const fixtures = {...base, [`/storage-nodes/${NODE}/machines`]: new Error('500')};
  const {run} = setup(fixtures);
  await run('loadBranchesData()');
  const html = run('branchesPage(0)');
  assert.match(html, /1 לא מגיב</);
});

test('the transfers tab shows progress, state in Hebrew, and a retry button only for a failed transfer', async () => {
  const {run} = setup(base);
  await run('loadBranchesData()');
  const html = run('branchesPage(1)');
  assert.match(html, /Office 365/);
  assert.match(html, /שולח/);
  assert.match(html, /נכשל/);
  assert.match(html, /אינו תואם ל-sha256/);
  assert.match(html, /admin/);
  assert.match(html, /נסה שוב/);
  assert.equal((html.match(/נסה שוב/g) || []).length, 1, 'only the failed transfer offers retry');
});

test('retrying a failed transfer re-posts the same node/image and reloads the list', async () => {
  const {run, calls} = setup(base);
  await run('loadBranchesData()');
  await run(`retryBranchTransfer('${NODE}', 'img_2c8e04')`);
  const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/n0de/transfer'));
  assert.ok(post && post.body.image_id === 'img_2c8e04');
});

test('the groups tab reuses storage_nodes.js: create, rename, delete a group', async () => {
  const fixtures = {...base, '/storage-node-groups': [{id: 'g1', label: 'צפון', nodes: 1, sort: 0}]};
  const {run, node} = setup(fixtures);
  await run('loadBranchesData()');
  run('currentTab=2; wireBranchesGroupsTab();');
  const html = node('branch-groups').innerHTML;
  assert.match(html, /צפון/);
  assert.match(html, /1 שרתי משנה/);
});

const PREVIEW = {secondary_spki: 'a1b2c3d4e5f607189c3e5d27b04f8a61e2d7c93b5a0f14e8d6b27c39a5e01f84', node_id: 'sn_a1b2c3d4e5f60718'};

// 16/10 (מוקאפ #1525, ג׳1–ג׳3): תיבות קוד מזויפות — ערך, מחלקות ומאזינים — כדי
// שהחיווט (wireEnrollCode) וה-onSubmit יקראו את מה שהמפעיל "הקליד".
function fakeCodeForm(run, chars = '') {
  run(`__boxes = Array.from({length: 8}, (_, i) => { const cls = new Set(), on = {};
    return {value: ${JSON.stringify(chars)}[i] || '', focused: false, cls, on,
      classList: {add: (c) => cls.add(c), remove: (c) => cls.delete(c)},
      addEventListener: (t, fn) => { on[t] = fn; }, focus() { this.focused = true; }}; });
    __submit = {disabled: false};
    __form = document.querySelector('#sheet');
    __form.querySelectorAll = (sel) => sel === '.enroll-c' ? __boxes : [];
    __form.querySelector = (sel) => sel === '#sf-submit' ? __submit : null;`);
}

test('registering a secondary: address → SPKI preview → code (enroll/preview then enroll)', async () => {
  const fixtures = {...base, '/storage-nodes/enroll/preview': PREVIEW};
  const {run, calls} = setup(fixtures);
  await run('loadBranchesData()');
  run('__step1=null; __step2=null; let n=0; sheet=(o)=>{ n++; if(n===1) __step1=o; else __step2=o; };');
  run('openEnrollSheet()');
  await run('__step1.onSubmit({url: "https://203.0.113.40:8443", label: "צפת", group_id: ""})');
  const preview = calls.find((c) => c.url.endsWith('/storage-nodes/enroll/preview'));
  assert.ok(preview && preview.body.url === 'https://203.0.113.40:8443');
  assert.equal(run('typeof __step2.onSubmit'), 'function', 'a second sheet asks for the code, with the previewed SPKI shown');
  const note = run('__step2.note');
  // the four groups the secondary shows as "זהות השרת", highlighted; then the whole SPKI in groups
  assert.match(note, /<span class="enroll-hl">A1B2<\/span> <span class="enroll-hl">C3D4<\/span> <span class="enroll-hl">E5F6<\/span> <span class="enroll-hl">0718<\/span><\/div>/);
  assert.match(note, />A1B2 C3D4 E5F6 0718 9C3E 5D27 B04F 8A61 E2D7 C93B 5A0F 14E8 D6B2 7C39 A5E0 1F84</);
  assert.match(note, /sn_a1b2c3d4e5f60718/);
  assert.match(note, /https:\/\/203\.0\.113\.40:8443/, 'the address that was checked');
  assert.equal((note.match(/class="enroll-c"/g) || []).length, 8, 'eight code boxes');
  assert.equal(run('__step2.fields'), undefined, 'no free-text code field any more');
  fakeCodeForm(run, 'K7M4X9QD');
  await run('__step2.onSubmit({})');
  const enroll = calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/enroll'));
  assert.ok(enroll);
  assert.equal(enroll.body.expected_secondary_spki, PREVIEW.secondary_spki);
  assert.equal(enroll.body.code, 'K7M4-X9QD');
  assert.equal(enroll.body.label, 'צפת');
});

test('the register button stays off until all eight boxes hold a code character', () => {
  const {run} = setup(base);
  fakeCodeForm(run);
  run('wireEnrollCode(__form)');
  assert.equal(run('__submit.disabled'), true, 'empty: off');
  const type = (i, text) => run(`__boxes[${i}].value = ${JSON.stringify(text)}; __boxes[${i}].on.input();`);
  'k7m4x9q'.split('').forEach((c, i) => type(i, c));
  assert.equal(run('__boxes.map((b) => b.value).join("")'), 'K7M4X9Q', 'upper-cased as typed');
  assert.equal(run('__submit.disabled'), true, 'seven of eight: still off');
  type(7, '0');
  assert.equal(run('__boxes[7].value'), '', '0/O/1/I are not in the code alphabet');
  assert.equal(run('__submit.disabled'), true);
  type(7, 'd');
  assert.equal(run('__submit.disabled'), false, 'eight valid characters: on');
  // a whole code pasted into the first box spreads over the eight
  fakeCodeForm(run);
  run('wireEnrollCode(__form)');
  type(0, 'k7m4-x9qd');
  assert.equal(run('__boxes.map((b) => b.value).join("")'), 'K7M4X9QD');
  assert.equal(run('__submit.disabled'), false);
});

test('a refused code marks the boxes and says to take a new one on the secondary (F5)', async () => {
  const fixtures = {...base, '/storage-nodes/enroll/preview': PREVIEW, '/storage-nodes/enroll': new Error('502')};
  const {run} = setup(fixtures);
  await run('loadBranchesData()');
  run('__step2=null; let n=0; sheet=(o)=>{ n++; if(n===2) __step2=o; else __step1=o; };');
  run('openEnrollSheet()');
  await run('__step1.onSubmit({url: "https://203.0.113.40:8443", label: "צפת", group_id: ""})');
  fakeCodeForm(run, 'K7M4X9QB');
  await assert.rejects(run('__step2.onSubmit({})'), /F5 ← קוד חדש/);
  assert.equal(run('__boxes.every((b) => b.cls.has("err"))'), true);
});

test('a code that is not eight characters is not sent', async () => {
  const fixtures = {...base, '/storage-nodes/enroll/preview': PREVIEW};
  const {run, calls} = setup(fixtures);
  run('__step2=null; let n=0; sheet=(o)=>{ n++; if(n===2) __step2=o; else __step1=o; };');
  run('openEnrollSheet()');
  await run('__step1.onSubmit({url: "https://203.0.113.40:8443", label: "צפת", group_id: ""})');
  fakeCodeForm(run, 'K7M4');
  await assert.rejects(run('__step2.onSubmit({})'), /8 התווים/);
  assert.ok(!calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/enroll')));
});

test('Nadav 18/10: the address is the IP alone -- the console builds https://<ip>:<interserver port>; a pasted full address is normalized', () => {
  const {run} = setup(base);
  assert.equal(run('INTERSERVER_PORT'), 8443);
  const norm = (x) => run(`enrollUrlFromInput(${JSON.stringify(x)})`);
  assert.equal(norm('10.20.0.5'), 'https://10.20.0.5:8443');
  assert.equal(norm('  10.20.0.5  '), 'https://10.20.0.5:8443');
  assert.equal(norm('https://10.20.0.5:8443/'), 'https://10.20.0.5:8443');
  assert.equal(norm('10.20.0.5:8443'), 'https://10.20.0.5:8443');
  assert.equal(norm('https://10.20.0.5:8443/api/interserver/v1'), 'https://10.20.0.5:8443');
  assert.equal(norm('10.20.0.5:9443'), 'https://10.20.0.5:9443', 'an explicit port is kept');
  assert.equal(norm('Haifa-Srv.local'), 'https://haifa-srv.local:8443', 'a host name too');
});

test('Nadav 18/10: what is not an IP or a host name is refused aloud, in Hebrew, and nothing is sent', async () => {
  for (const bad of ['10.20.0.256', '10.20.0', 'ftp://10.20.0.5', '10.20.0.5/other', 'a b', 'user@10.20.0.5', '10.20.0.5:99999', '-bad-.local']) {
    assert.throws(() => setup(base).run(`enrollUrlFromInput(${JSON.stringify(bad)})`), /כתובת לא תקינה|פורט לא תקין/, bad);
  }
  assert.throws(() => setup(base).run('enrollUrlFromInput("")'), /חסרה כתובת IP/);
  const {run, calls} = setup({...base, '/storage-nodes/enroll/preview': PREVIEW});
  run('__step1=null; sheet=(o)=>{ __step1=o; };');
  run('openEnrollSheet()');
  const url = run('__step1.fields.find((f) => f.id === "url")');
  assert.equal(url.label, 'כתובת IP של שרת המשנה');
  assert.equal(url.value, '', 'empty -- no "https://" typed in for the operator');
  assert.equal(url.placeholder, '10.20.0.5');
  await assert.rejects(run('__step1.onSubmit({url: "10.20.0.999", label: "צפת", group_id: ""})'), /כתובת לא תקינה/);
  assert.ok(!calls.find((c) => c.url.endsWith('/storage-nodes/enroll/preview')), 'a refused address is not sent');
  await run('__step1.onSubmit({url: "203.0.113.40", label: "צפת", group_id: ""})');
  const preview = calls.find((c) => c.url.endsWith('/storage-nodes/enroll/preview'));
  assert.equal(preview.body.url, 'https://203.0.113.40:8443', 'the server still gets a full base_url');
});

test('"+ קבוצה חדשה…" in the register window opens the group window, and the new group comes back selected', async () => {
  const fixtures = {...base, '/storage-node-groups': [{id: 'g1', label: 'מרכז'}]};
  const {run} = setup(fixtures);
  await run('loadBranchesData()');
  run('__sheets=[]; sheet=(o)=>{ __sheets.push(o); };');
  run('openEnrollSheet()');
  const options = run('__sheets[0].fields.find((f) => f.id === "group_id").options');
  assert.equal(options.map((o) => o.label).join('|'), 'ללא קבוצה|מרכז|+ קבוצה חדשה…');
  assert.equal(options[options.length - 1].value, run('ENROLL_NEW_GROUP'), 'the last option');
  // the operator typed an address and a name, then picks "+ קבוצה חדשה…"
  run('$("#sf-url").value = "10.20.0.5"; $("#sf-label").value = "צפת"; $("#sf-group_id").value = ENROLL_NEW_GROUP; $("#sf-group_id").onchange();');
  assert.equal(run('__sheets[1].title'), 'קבוצת שרתי משנה חדשה', 'the existing group window, in the console sheet');
  assert.equal(run('__sheets[1].extra.label'), 'חזרה');
  fixtures['/storage-node-groups'] = [{id: 'g1', label: 'מרכז'}, {id: 't1', label: 'צפון'}];
  // the server answers POST /storage-node-groups with {id} (console_storage.create_group);
  // the fake fetch keys by path only, so the POST is answered here
  run(`__posted=[]; const __f = fetch; fetch = async (u, o = {}) => o.method === "POST" && u.endsWith("/storage-node-groups")
    ? (__posted.push(JSON.parse(o.body)), {status: 200, ok: true, headers: {get: () => null}, json: async () => ({id: "t1"})})
    : __f(u, o);`);
  const reopen = await run('__sheets[1].onSubmit({label: "צפון"})');
  assert.equal(run('JSON.stringify(__posted)'), JSON.stringify([{label: 'צפון'}]));
  reopen();
  const back = run('__sheets[2]');
  assert.equal(back.title, 'רישום שרת משנה — שלב 1 מתוך 2: כתובת');
  assert.equal(back.fields.find((f) => f.id === 'group_id').value, 't1', 'the new group is selected');
  assert.ok(back.fields.find((f) => f.id === 'group_id').options.some((o) => o.value === 't1' && o.label === 'צפון'));
  assert.equal(back.fields.find((f) => f.id === 'url').value, '10.20.0.5', 'what was typed is kept');
  assert.equal(back.fields.find((f) => f.id === 'label').value, 'צפת');
  // "חזרה" without creating: back to the register window, nothing posted
  run('__sheets.length = 0; openEnrollNewGroup({url: "10.20.0.6", label: "x"}); __sheets[0].extra.onClick();');
  assert.equal(run('__sheets[1].fields.find((f) => f.id === "url").value'), '10.20.0.6');
});

test('rc6f (Nadav 18/10): after "בדוק SPKI" the code step stays open -- the first step does not close the window it opened, and a refresh tick does not either', async () => {
  const fixtures = {...base, '/storage-nodes': [], '/storage-nodes/enroll/preview': PREVIEW};
  const {run, calls} = setup(fixtures);
  await run('loadBranchesData()');
  // the real sheet(), on a modal whose classes are tracked
  run(`__cls = new Set(); $("#modal").classList = {add: (c) => __cls.add(c), remove: (c) => __cls.delete(c),
        contains: (c) => __cls.has(c), toggle() {}};`);
  run('openEnrollSheet()');
  assert.equal(run('__cls.has("show")'), true, 'step 1 is open');
  run('$("#sf-url").value = "203.0.113.40"; $("#sf-label").value = "צפת"; $("#sf-group_id").value = "";');
  await run('$("#sheet").onsubmit({preventDefault() {}})');
  assert.ok(calls.find((c) => c.url.endsWith('/storage-nodes/enroll/preview')), 'the preview was asked');
  const stillOpen = () => {
    assert.equal(run('__cls.has("show")'), true, 'the window is open');
    assert.match(run('$("#sheet").innerHTML'), /id="enroll-c-0"/, 'the code boxes are there');
    assert.match(run('$("#sheet").innerHTML'), /שלב 2 מתוך 2/);
    assert.equal(run('typeof $("#sheet").onsubmit'), 'function', 'step 2 still answers its button');
  };
  stillOpen();
  // a background refresh tick of the page (the 2 s list poll / a re-render)
  await run('loadBranchesData()');
  run('renderCurrent()');
  stillOpen();
});

test('removing a secondary requires typing its name', async () => {
  const {run} = setup(base);
  await run('loadBranchesData()');
  run('__sheet=null; sheet=(o)=>{ __sheet=o; };');
  run(`removeBranchNode('${NODE}')`);
  assert.ok(!run('__sheet.verify'), '#1270: no typed name'); assert.equal(run('__sheet.fields'), undefined);
  assert.equal(run('__sheet.danger'), true);
});

test('the monitor button on the branch (single secondary) page opens monitor.html through the secondary (node= in the URL)', () => {
  const {run, opened} = setup(base);
  run(`branchMonitor("${NODE}", "aa:bb:cc:dd:ee:01", "Builder-H")`);
  assert.equal(opened.length, 1);
  assert.match(opened[0][0], /^monitor\.html\?node=n0de&mac=aa%3Abb%3Acc%3Add%3Aee%3A01&name=Builder-H$/);
  run('ME.role="deploy"');
  run(`branchMonitor("${NODE}", "aa:bb:cc:dd:ee:01", "Builder-H")`);
  assert.equal(opened.length, 1, 'deploy: nothing opens');
});

test('monitor.js connects to the remote path when node= is present, and to the local one otherwise', () => {
  const urls = [];
  const mk = (search) => {
    const el = () => ({addEventListener() {}, getContext: () => ({fillRect() {}, putImageData() {}, createImageData: () => ({data: new Uint8ClampedArray(4)})}),
      textContent: '', className: '', classList: {add() {}, remove() {}, toggle() {}}, style: {}, width: 0, height: 0, onclick: null, value: ''});   // #1686: the disconnect overlay
    const ctx = vm.createContext({
      console, URLSearchParams, Uint8Array, Uint8ClampedArray, DataView, ArrayBuffer, Math, Number, JSON, Promise, Date, String,
      setTimeout: () => 1, clearTimeout() {},
      location: {search, protocol: 'http:', host: '10.10.10.8:8080'},
      document: {getElementById: el, addEventListener() {}, removeEventListener() {}, title: ''},
      window: {addEventListener() {}},
      WebSocket: class { constructor(url) { urls.push(url); this.readyState = 0; } close() {} send() {} static get OPEN() { return 1; } },
    });
    vm.runInContext(fs.readFileSync(path.join(root, 'monitor.js'), 'utf8'), ctx);
  };
  mk('?node=n0de&mac=aa%3Abb%3Acc%3Add%3Aee%3A01&name=Builder-H');
  mk('?mac=aa%3Abb%3Acc%3Add%3Aee%3A01&name=Builder');
  assert.equal(JSON.stringify(urls), JSON.stringify([
    'ws://10.10.10.8:8080/api/console/storage-nodes/n0de/monitor/aa%3Abb%3Acc%3Add%3Aee%3A01',
    'ws://10.10.10.8:8080/api/console/monitor/aa%3Abb%3Acc%3Add%3Aee%3A01',
  ]));
});

test('after login the console holds the /me capabilities, so the branches tab is reachable without a reload', async () => {
  const fixtures = {...base, '/me': {timezone: 'UTC', username: 'noc', role: 'admin', idle_seconds: 300, capabilities: {interbranch_transfer: true, enroll_secondary: true, open_local_pairing: false}},
    '/overview': {session: null, pulls: [], room: null, machines: 0, images: 0, storage: null}};
  const {run, listeners} = setup(fixtures);
  run('ME=null; showApp=async()=>{};');
  // #1085 שלב ב': loginOnSubmit מדלג לפי event.target.id (דלגציה על #login)
  await listeners.submit({preventDefault() {}, target: {id: 'login-form'}});
  assert.equal(run('ME && ME.capabilities && ME.capabilities.interbranch_transfer'), true);
  assert.equal(run('pageAllowed("branches")'), true);
});

// #1473 (נדב 16/10): הגבלת קצב בין אתרים — אופציונלית, ברירת מחדל ללא הגבלה, ליד ההעברות.
test('#1473: the transfers tab shows the inter-site rate cap in force; unlimited by default; unread is said', async () => {
  let {run} = setup({...base, '/settings': {intersite_rate_cap_mbps: '0'}});
  await run('loadBranchesData()');
  let html = run('branchesPage(1)');
  assert.match(html, /הגבלת קצב בין אתרים: <b>ללא הגבלה<\/b>/);
  assert.match(html, /onclick="transferCapSheet\(\)"/, 'admin can change it from here');
  ({run} = setup({...base, '/settings': {intersite_rate_cap_mbps: '200'}}));
  await run('loadBranchesData()');
  html = run('branchesPage(1)');
  assert.match(html, /עד <bdi dir="ltr">200 Mb\/s<\/bdi> \(<bdi dir="ltr">25 MB\/s<\/bdi>\) לכל העברה/);
  ({run} = setup({...base, '/settings': new Error('500')}));
  await run('loadBranchesData()');
  html = run('branchesPage(1)');
  assert.match(html, /הגבלת קצב בין אתרים: <b>לא נקרא: /, 'an unread setting is never shown as "unlimited"');
  assert.doesNotMatch(html, /ללא הגבלה/);
  assert.doesNotMatch(html, /transferCapSheet/, 'no change button on a value that was not read');
});

test('#1473: the change sheet posts the whole number to /settings and refuses anything else', async () => {
  const {run, calls} = setup({...base, '/settings': {intersite_rate_cap_mbps: '0'}});
  await run('loadBranchesData()');
  run('__sheet=null; sheet=(o)=>{ __sheet=o; };');
  run('transferCapSheet()');
  assert.equal(run('__sheet.fields[0].value'), '0');
  for (const bad of ['-1', '1.5', 'abc', '', '100001']) {
    await assert.rejects(run(`__sheet.onSubmit({v: ${JSON.stringify(bad)}})`), /מספר שלם 0–100000/);
  }
  assert.ok(!calls.some((c) => c.method === 'POST' && c.url.endsWith('/settings')), 'nothing posted for a bad value');
  await run('__sheet.onSubmit({v: "50"})');
  const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/settings'));
  assert.deepEqual(post.body, {intersite_rate_cap_mbps: 50});
});

test('#1473: a deploy user sees the cap but has no change button', async () => {
  const {run} = setup({...base, '/settings': {intersite_rate_cap_mbps: '100'}}, {role: 'deploy'});
  await run('loadBranchesData()');
  const html = run('branchesTransfersTab()');
  assert.match(html, /עד <bdi dir="ltr">100 Mb\/s<\/bdi>/);
  assert.doesNotMatch(html, /transferCapSheet/);
});
