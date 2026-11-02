// #720 — דף "דרייברים" בקונסולה אינו placeholder: הוא מציג את חבילות
// הדרייברים (שם, כללי התאמה, קבצים, "מתאים ל-" בשמות) עם ייבוא ומחיקה
// ל-admin בלבד, ו-memberRow מציג את תוצאת ה-staging ליד המחשב.
// ‏#954 גל 6 (drivers.md): עמוד own — טבלה אחת (datagrid), לשונית "כיסוי לפי
// מכונה" שחושבת בלקוח לפי אותם כללים של drivers.match (pci / pci_any / דגם;
// מזהה בלי class = כל class), מחיקה בחלון אישור, "לא נקרא" ≠ ריק.
// הבדיקה מריצה את console.js + drivers.js המשוגרים ב-vm עם fetch מזויף.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

function setup(fixtures, role = 'admin') {
  const toasts = [], requests = [];
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: stubNode, getElementById: stubNode,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options = {}) => {
      const key = url.replace('/api/console', '').split('?')[0];
      requests.push({url: key, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
      const f = fixtures[key];
      if (f instanceof Error) return {status: 500, ok: false, headers: {get: () => null}, json: async () => ({detail: f.message})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => f ?? {}};
    },
  });
  for (const f of ['progress.js', 'console.js', 'net.js', 'drivers.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run(`ME={username:"u",role:"${role}",idle_seconds:300,capabilities:{}}; toast=(m)=>__toasts.push(m); current="drivers"; globalThis.sheets=[]; sheet=(o)=>sheets.push(o); globalThis.drawer=""; openDrawer=(t,b)=>{ drawer=t+"|"+b; }; renderCurrent=()=>{};`);
  ctx.__toasts = toasts;
  return {run, toasts, requests};
}

const MAC_B = 'b4:2e:99:07:1a:c4', MAC_C = 'a0:48:1c:8a:18:40', MAC_L = '78:ac:c0:9b:11:c2', MAC_N = '78:ac:c0:9b:11:c3', MAC_S = '00:11:22:33:44:55';
const PKG = {
  name: 'intel-nic', description: 'Intel I219 <b>LM</b>',
  match: [{pci: ['8086:15bc:020000']}, {vendor: 'LENOVO', model: 'ThinkCentre M720q'}],
  files: [{path: 'e1d68x64.inf', sha256: 'a'.repeat(64)}, {path: 'x64/e1d68x64.sys', sha256: 'b'.repeat(64)}],
  matches: [MAC_B, MAC_L],
};
const PKG_ANY = {name: 'hp-800-g6', description: '', match: [{pci_any: ['8086:15fa', '10ec:8168', '8086:a0a0', '1234:5678']}], files: [{path: 'a.inf', sha256: 'c'.repeat(64)}], matches: [MAC_C]};
const BASE = {
  '/drivers': [PKG, PKG_ANY],
  '/groups': [{id: 'grp_BUILD', role: 'build', label: 'מחשבי בנייה'}, {id: 'grp_CLONERS', role: 'cloner', label: 'מחשבי שיכפול'}, {id: 'grp_LAB1', role: 'classroom', label: 'כיתה 1'}],
  '/machines': [
    {mac: MAC_B, suffix: 'בנייה 1', group_id: 'grp_BUILD', inventory: {pci: ['8086:15bc:020000', '8086:a0d3:010601'], dmi: {sys_vendor: 'Dell Inc.', product_name: 'OptiPlex 7090'}}},
    {mac: MAC_C, suffix: 'מחשב 1', group_id: 'grp_CLONERS', inventory: {pci: ['10ec:8168:020000'], dmi: {sys_vendor: 'HP', product_name: 'HP EliteDesk 800 G6'}}},
    {mac: MAC_L, suffix: 'מחשב 2', group_id: 'grp_CLONERS', inventory: {pci: ['8086:1533:020000'], dmi: {sys_vendor: 'LENOVO', product_name: '10SQS0AK00', product_version: 'ThinkCentre M720q'}}},
    {mac: MAC_N, suffix: 'מחשב 3', group_id: 'grp_CLONERS', inventory: null},
    {mac: MAC_S, suffix: '05', group_id: 'grp_LAB1', inventory: {pci: ['8086:15bc:020000']}},
  ],
  '/disk-failures': [], '/shrink-records': [], '/net': [], '/monitor/machines': [], '/room': {machines: []}, '/tasks': [],
};
const row = (html, needle) => { const i = html.indexOf(needle); assert.ok(i >= 0, 'missing row: ' + needle); const s = html.lastIndexOf('<tr', i); return html.slice(s, html.indexOf('</tr>', i) + 5); };

test('the drivers page is wired: own page with three tabs, load + render, not a placeholder', () => {
  const {run} = setup(BASE);
  assert.equal(run('typeof pages.drivers.load'), 'function');
  assert.match(run('pages.drivers.render.toString()'), /driversPage/);
  assert.equal(run('pages.drivers.own'), true);
  assert.equal(JSON.stringify(run('pages.drivers.tabs')), JSON.stringify(['דגמים', 'חבילות', 'כיסוי לפי מכונה']));
  assert.equal(run('driversPage()'), run('pagePlaceholder()'), 'before the first read: placeholder');
});

test('packages tab: one datagrid — name+description (escaped), rules (pci / model / pci_any), file count, matches by name, verified, details + typed-name delete', async () => {
  const {run, requests} = setup(BASE);
  await run('loadDrivers()');
  assert.ok(requests.some((r) => r.url === '/machines'), 'machines are read for names and coverage');
  const html = run('driversPage(1)');
  assert.match(html, /<table class="dg acts-on">/); assert.match(html, /<th>חבילה<\/th><th>התאמה<\/th><th>קבצים<\/th><th>מתאים ל-<\/th><th>אומת<\/th>/);
  const r = row(html, 'intel-nic');
  assert.match(r, /&lt;b&gt;LM&lt;\/b&gt;/, 'description is escaped');
  assert.match(r, /PCI <span class="mono">8086:15bc:020000<\/span>/); assert.match(r, /דגם: <span dir="auto">LENOVO<\/span> · <span dir="auto">ThinkCentre M720q<\/span>/, 'Hebrew outside, ids isolated LTR');
  assert.match(r, /<td><span class="mono">2<\/span><\/td>/, 'file count');
  assert.match(r, /2 מכונות · <span class="cap" title="בנייה 1, מחשב 2">בנייה 1, מחשב 2</, 'the machines are named, not their MACs');
  assert.match(r, /class="st ok">sha256 בייבוא</);
  assert.match(r, /openDriverDetail\('intel-nic'\)">פרטים</); assert.match(r, /class="btn sm danger" onclick="deleteDriver\('intel-nic'\)">מחיקה</);
  assert.match(row(html, 'hp-800-g6'), /PCI אחד מ-4: <span class="mono">8086:15fa \| 10ec:8168 \| 8086:a0a0<\/span> …/);
  // ‏#1300 (console-full.html P.drivers): משנה = דגמים · מחשבים · מכוסים; מונים בלשוניות; "העלאת דרייבר"
  assert.match(html, /<div class="obj-sub"[^>]*>\d+ דגמים · \d+ מחשבים · \d+ מכוסים<\/div>/);
  assert.match(html, /role="tab"[^>]*>חבילות<small>2<\/small>/, 'the packages tab stays (no other place in the mockup), with its count');
  assert.match(html, /openDriverImport\(\)">העלאת דרייבר</);
  assert.match(html, /role="tab" aria-selected="true"[^>]*>חבילות</); assert.match(html, /role="tab" aria-selected="false"[^>]*>כיסוי לפי מכונה</);
  assert.doesNotMatch(html, /class="table"|title="[^"]*">מתאים ל-\d+ מכונות/, 'the old table and the tooltip-only names are gone');
  // מחיקה — חלון אישור (#1270) → POST /drivers/{name}/delete עם confirm_name
  run("deleteDriver('intel-nic')");
  const s = run('sheets.at(-1)'); assert.equal(s.danger, true); assert.equal(s.verify, undefined, '#1270: no typed name');
  await s.onSubmit();
  assert.deepEqual(requests.filter((x) => x.method === 'POST').at(-1), {url: '/drivers/intel-nic/delete', method: 'POST', body: {confirm_name: 'intel-nic'}});
  // פרטים — מגירה עם הכללים, הקבצים וה-sha256
  run("openDriverDetail('intel-nic')");
  assert.match(run('drawer'), /חבילה — intel-nic\|/); assert.match(run('drawer'), /e1d68x64\.inf<\/span> <span class="cap mono">aaaaaaaaaaaa…/);
});

test('coverage tab: a row per build/cloner machine — packages that will be staged (by PCI / by model), uncovered controllers, no inventory is "not checked"; classroom is v2', async () => {
  const {run} = setup(BASE);
  await run('loadDrivers()');
  const html = run('driversPage(2)');
  assert.match(html, /<th>מכונה<\/th><th>בקרי PCI \(רשת\/אחסון\)<\/th><th>חבילות שיונחו<\/th><th>כיסוי<\/th>/);
  const b = row(html, 'בנייה 1');
  assert.match(b, /intel-nic <span class="cap">לפי PCI</);
  assert.match(b, /class="st warn">1 בקר בלי חבילה<\/span><span class="sub"><span class="mono">8086:a0d3:010601/, 'the storage controller no rule covers');
  const c = row(html, 'מחשב 1');
  assert.match(c, /hp-800-g6 <span class="cap">לפי PCI</, 'pci_any without class matches vendor:device in any class');
  assert.match(c, /class="st ok">כל הבקרים מכוסים</);
  const l = row(html, 'מחשב 2');
  assert.match(l, /intel-nic <span class="cap">לפי דגם</, 'model rule against product_version (Lenovo)');
  assert.match(l, /class="st warn">חבילת דגם בלבד — 1 בקרים לא אומתו לפי PCI</);
  const n = row(html, 'מחשב 3');
  assert.match(n, /לא דיווחה חומרה/); assert.match(n, /class="st unk">לא נבדק</);
  assert.doesNotMatch(html, /data-mac="00:11:22:33:44:55"/, 'classroom machines are v2 — not in the coverage table');
  assert.match(html, /תחנות כיתה \(1\) — v2, לא מוצגות/);
  assert.match(html, /3 מתוך 4 דיווחו חומרה/);
  assert.match(html, /role="tab" aria-selected="true"[^>]*>כיסוי לפי מכונה</);
});

test('an empty library says so with the build tool; a failed read is "not read" — not an empty library', async () => {
  let s = setup({...BASE, '/drivers': []});
  await s.run('loadDrivers()');
  let html = s.run('driversPage(1)');
  assert.match(html, /class="empty">אין חבילות דרייברים — ייבוא קובץ tar שנבנה ב-tools\/drivers\/build-package\.py/);
  assert.match(html, /docs\/guides\/driver-packages\.md/);
  s = setup({...BASE, '/drivers': new Error('disk gone')});
  await s.run('loadDrivers()');
  html = s.run('driversPage(1)');
  assert.notEqual(html, s.run('pagePlaceholder()'));
  assert.match(html, /note err.*disk gone/); assert.match(html, /class="pill err">לא נקרא</);
  assert.doesNotMatch(html, /אין חבילות|<table/);
  assert.match(s.toasts.join('|'), /disk gone/);
});

test('a deploy user sees the list but no import or delete (GET /drivers is current_user; upload/delete are admin_only)', async () => {
  const {run} = setup({...BASE, '/drivers': [{...PKG, matches: []}]}, 'deploy');
  await run('loadDrivers()');
  const html = run('driversPage(1)');
  assert.match(html, /intel-nic/);
  assert.match(html, /אף מכונה שדיווחה/);
  assert.doesNotMatch(html, /deleteDriver/);
  assert.doesNotMatch(html, /openDriverImport/);
});

test('memberRow shows the staging outcome — and failed is visible, not silent', () => {
  const {run} = setup({});
  const row = (drivers) => run(`memberRow(${JSON.stringify({mac: 'b4:2e:99:07:1a:c4', state: 'done', done: true,
    bytes_written: 1, bytes_total: 1, disks: [], drivers})}, {})`);
  assert.match(row({state: 'staged', packages: ['intel-nic']}), /דרייברים הונחו \(חבילה אחת\): intel-nic/);
  assert.match(row({state: 'no_match', packages: []}), /אין חבילה תואמת/);
  assert.match(row({state: 'failed', packages: [], error: 'sha256 mismatch: x <script>'}),
    /class="notice warn">דרייברים לא הונחו — sha256 mismatch: x &lt;script&gt;\. המחשב יעלה בלי הדרייברים של הדגם\./);   // #1331: orange, the restore is done
  assert.doesNotMatch(row({state: 'skipped', packages: [], reason: 'not_windows'}), /דרייברים/);
  assert.doesNotMatch(row(null), /דרייברים/, 'null = the stage did not report');
});

test('index.html loads drivers.js with the same cache version as the rest', () => {
  const page = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const versions = new Set([...page.matchAll(/\?v=([0-9.]+)/g)].map((m) => m[1]));
  assert.equal(versions.size, 1, [...versions].join(','));
  assert.match(page, /drivers\.js\?v=/);
  assert.match(page, /data-page="drivers"/);
});

test('stage 3: models tab (first) — a row per DMI model with machines / covered / missing / unchecked and a state pill; unreported machines are their own "not checked" row, never counted as covered', async () => {
  const {run} = setup(BASE);
  await run('loadDrivers()');
  const html = run('driversPage()');
  assert.match(html, /role="tab" aria-selected="true"[^>]*>דגמים</);
  assert.match(html, /דגמי מחשבים שנמצאו ברשת/); assert.match(html, /הדגם נקבע מ-DMI שהמחשב מדווח ב-hello/);
  // ‏#1300: עמודת "לא נבדקו" ירדה — מה שלא נבדק נאמר בפיל המצב; תפריט ⋯ לכל דגם
  assert.match(html, /<th>דגם<\/th><th>מחשבים<\/th><th>מכוסים<\/th><th>חסרים<\/th><th>מצב<\/th><th><span class="sr">פעולות<\/span><\/th>/);
  assert.match(html, /role="tab" aria-selected="true"[^>]*>דגמים<small>\d+<\/small>/);
  const dell = row(html, 'Dell Inc. OptiPlex 7090');
  assert.match(dell, /<td>1<\/td><td>0<\/td><td>1<\/td><td><span class="pill warn">חלקי<\/span>/, 'a controller without a package → partial');
  assert.match(dell, /data-menu=/);
  const hp = row(html, 'HP HP EliteDesk 800 G6');
  assert.match(hp, /<td>1<\/td><td>1<\/td><td>0<\/td><td><span class="pill ok">מכוסה<\/span>/);
  const lenovo = row(html, 'LENOVO ThinkCentre M720q');
  assert.match(lenovo, /<td>0<\/td><td>1<\/td>/, 'a model-rule package does not cover a controller by itself');
  const unk = row(html, 'לא דיווחו חומרה');
  assert.match(unk, /<td>1<\/td><td>—<\/td><td>—<\/td><td><span class="pill ">לא נבדק<\/span>/, 'no inventory = not checked, with dashes — not 0 covered');
  assert.doesNotMatch(html, /05|00:11:22:33:44:55/, 'classroom machines are v2 and are not listed');
  assert.match(html, /openDriverImport\(\)">העלאת דרייבר</);
});

test('stage 3: a model with no matching package at all is "אין דרייברים" (red); a model whose machines reported no controllers is "לא נבדק", not "מכוסה"', async () => {
  const {run} = setup({...BASE, '/drivers': [], '/machines': [
    {mac: MAC_B, suffix: 'בנייה 1', group_id: 'grp_BUILD', inventory: {pci: ['8086:a0d3:010601'], dmi: {sys_vendor: 'Dell Inc.', product_name: 'OptiPlex 7090'}}},
    {mac: MAC_C, suffix: 'מחשב 1', group_id: 'grp_CLONERS', inventory: {pci: [], dmi: {sys_vendor: 'HP', product_name: 'Z'}}}]});
  await run('loadDrivers()');
  const html = run('driversPage(0)');
  assert.match(row(html, 'Dell Inc. OptiPlex 7090'), /pill err">אין דרייברים</);
  assert.match(row(html, 'HP Z'), /<td>1<\/td><td>0<\/td><td>0<\/td><td><span class="pill ">לא נבדק</, 'all unchecked → "not checked" (the count used to be its own column)');
  // ‏#1300: חלק נבדק וחלק לא — הפיל אומר כמה לא נבדקו, לא "מכוסה"
  assert.equal(run(`driverModelPill({missing:0,pkgs:new Set(),unchecked:1,machines:[1,2]})`), '<span class="pill ">1 לא נבדקו</span>');
});

test('stage 3: a model row opens a drawer — each machine with its verdict and uncovered controllers, the packages that will be staged, the empty-model warning; MACHINES unread is a red note', async () => {
  const {run} = setup(BASE);
  await run('loadDrivers()');
  run("openDriverModel(encodeId('Dell Inc. OptiPlex 7090'))");
  const d = run('drawer');
  assert.match(d, /דגם — Dell Inc\. OptiPlex 7090\|/); assert.match(d, /בנייה 1 <span class="pill warn">1 בקרים בלי חבילה<\/span> <span class="cap mono">8086:a0d3:010601/);
  assert.match(d, /intel-nic/); assert.match(d, /openDriverImport\(\)/);
  const s2 = setup({...BASE, '/drivers': []}); await s2.run('loadDrivers()');
  s2.run("openDriverModel(encodeId('Dell Inc. OptiPlex 7090'))");
  assert.match(s2.run('drawer'), /note warn[^]*רשומים, ואין להם דרייברים/); assert.match(s2.run('drawer'), /אין חבילה מתאימה/);
  s2.run("openDriverModel(encodeId('nope'))"); assert.match(s2.toasts.join('|'), /דגם לא נמצא/);
  s2.run('MACHINES=null'); assert.match(s2.run('driversPage(0)'), /note err[^]*רשימת המחשבים לא נקראה/);
});
