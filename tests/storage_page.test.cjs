// #1066 שלב ב' — דף "אחסון" תחת תשתית, לפי המוקאפ
// docs/design/console-redesign/storage-mockup-2026-09-18.html: רשימה ·
// הוסף → סוג · טופס NFS · SMB · iSCSI א' · iSCSI ב' (שלושה מצבים) · מגירה.
// כל מסך מרונדר מ-JSON קבוע; "שמור ועגן" מושבת עד POST /test מוצלח (עיקרון 5);
// דיסק unknown בלי כפתור פירמוט; פירמוט והסרה בחלון אישור (#1270).
// הבדיקה מריצה את console.js + storage.js ב-vm עם fetch מזויף (כמו drivers_page).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}}, disabled: false, title: '',
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
      const method = options.method || 'GET';
      requests.push({url: key, method, body: options.body ? JSON.parse(options.body) : null});
      const f = fixtures[method + ' ' + key] ?? fixtures[key];
      if (f instanceof Error) return {status: f.status || 500, ok: false, headers: {get: () => null}, json: async () => ({detail: f.message})};
      const val = typeof f === 'function' ? f(requests[requests.length - 1]) : f;
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => val ?? {}};
    },
  });
  for (const f of ['progress.js', 'console.js', 'net.js', 'storage.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run(`ME={timezone:"UTC",username:"u",role:"${role}",idle_seconds:300,capabilities:{}}; toast=(m)=>__toasts.push(m); current="storage"; globalThis.sheets=[]; sheet=(o)=>sheets.push(o); confirmSheet=(t,s,l,f)=>sheets.push({title:t,sub:s,submitLabel:l,onSubmit:f}); globalThis.drawer=""; openDrawer=(t,b)=>{ drawer=t+"|"+b; }; closeDrawer=()=>{}; renderCurrent=()=>{};`);
  ctx.__toasts = toasts;
  return {run, toasts, requests};
}

const IQN = 'iqn.2005-10.org.freenas.ctl:images', PORTAL = '10.44.3.75:3260';
const LOCAL = {id: 'loc_local', name: 'מקומי', type: 'local', type_label: 'תיקייה', target: '/srv/imagectl/images', mount_point: '/srv/imagectl/images',
  state: 'connected', state_label: 'מחובר', state_since: '2026-09-18T09:00:00+00:00', state_detail: '', free_bytes: 312 * 1024 ** 3, total_bytes: 926 * 1024 ** 3,
  images: 6, unavailable_images: 0, subtitle: 'דיסק השרת · ברירת המחדל לקליטה', params: {}, removable: false};
const NAS = {id: 'loc_aaaa0001', name: 'nas-images', type: 'nfs', type_label: 'NFS', target: '10.44.10.20:/mnt/pool/images', mount_point: '/srv/imagectl/storage/nas-images',
  state: 'connected', state_label: 'מחובר', state_since: '2026-09-18T09:00:00+00:00', state_detail: '', free_bytes: 1.8 * 1024 ** 4, total_bytes: 4 * 1024 ** 4,
  images: 14, unavailable_images: 0, subtitle: 'nfs 4.1', params: {server: '10.44.10.20', export: '/mnt/pool/images', version: '4.1', readonly: false}, removable: true};
const SAN = {id: 'loc_bbbb0002', name: 'san-lun1', type: 'iscsi', type_label: 'iSCSI', target: IQN, mount_point: '/srv/imagectl/storage/san-lun1',
  state: 'unreachable', state_label: 'לא נגיש', state_since: '2026-09-18T09:12:00+00:00', state_detail: 'no route to ' + PORTAL, free_bytes: 800 * 1000 ** 3, total_bytes: 800 * 1000 ** 3,
  images: 2, unavailable_images: 2, subtitle: 'ext4 · images', params: {portal: PORTAL, iqn: IQN, auto: true, fs_type: 'ext4', fs_label: 'images'}, removable: true};
const OFF = {...NAS, id: 'loc_cccc0003', name: 'old-nas', state: 'disconnected', state_label: 'מנותק', state_detail: 'המפעיל ניתק', images: 0, unavailable_images: 0, free_bytes: null, total_bytes: null};
const LIST = {locations: [LOCAL, NAS, SAN], summary: {locations: 3, unreachable: 1, images: 22, unavailable_images: 2, free_bytes: 2.1 * 1024 ** 4, total_bytes: 5 * 1024 ** 4, checked_at: '2026-09-18T09:40:00+00:00'}};
const BASE = {'/storage-locations': LIST, '/images': [
  {id: 'win-build-v4', name: 'win-build v4', location_id: SAN.id, available: false, total_compressed_bytes: 67e9, created: '2026-09-14T10:00:00+00:00'},
  {id: 'office365-v2', name: 'office365 v2', location_id: SAN.id, available: false, total_compressed_bytes: 20e9, created: '2026-09-12T10:00:00+00:00'},
  {id: 'ubuntu', name: 'ubuntu', location_id: LOCAL.id, available: true, total_compressed_bytes: 5e9, created: '2026-09-10T10:00:00+00:00'},
]};
// ‏#1300: פעולות השורה בתפריט ⋯ (UI.rowMenu) — הפריטים מתוך data-menu
const menuOf = (r) => { const m = r.match(/data-menu="([^"]*)"/); assert.ok(m, 'row has a ⋯ menu'); return JSON.parse(m[1].replaceAll('&quot;', '"').replaceAll('&#39;', "'").replaceAll('&amp;', '&')); };
const row = (html, needle) => { const i = html.indexOf(needle); assert.ok(i >= 0, 'missing row: ' + needle); const s = html.lastIndexOf('<tr', i); return html.slice(s, html.indexOf('</tr>', i) + 5); };
const tick = () => new Promise(r => setImmediate(r));
const settle = async () => { for (let i = 0; i < 8; i++) await tick(); };

test('the storage page is wired: own admin page, load + render, placeholder before the first read', () => {
  const {run} = setup(BASE);
  assert.equal(run('typeof pages.storage.load'), 'function');
  assert.match(run('pages.storage.render.toString()'), /storagePage/);
  assert.equal(run('pages.storage.own'), true);
  assert.equal(run('storagePage()'), run('pagePlaceholder()'), 'before the first read: placeholder');
  assert.equal(run('pageAllowed("storage")'), true);
});

test('1. list (#1300, console-full.html P.storage): short header, unavailable-images note, one row per location (name · type · address · usage · state) with a ⋯ menu; the state legend is the pill title', async () => {
  const {run, requests} = setup(BASE);
  await run('loadStorage()');
  assert.deepEqual(requests.map(r => r.method + ' ' + r.url), ['GET /storage-locations']);
  const html = run('storagePage()');
  assert.match(html, /<span class="pill err">1 לא נגיש<\/span>/, 'a pill only when something is wrong');
  assert.match(html, /<div class="obj-sub"[^>]*>מיקומי האימג&#39;ים והקיבולת · נבדק 09:40 \(כל 60 שניות\)<\/div>/);
  assert.match(html, /onclick="storageGo\('add'\)"><svg[^]*?<\/svg> מיקום אחסון</);
  assert.match(html, /<span>מיקומי אחסון<\/span><div class="acts"><button class="btn sm" onclick="storageCheckAll\(\)">בדוק הכול</);
  assert.match(html, /2 אימג'ים לא זמינים כרגע/, 'the note names the count');
  assert.match(html, /san-lun1<\/span> \(לא נגיש מאז 09:12\)/, 'and the location behind it');
  assert.match(html, /storageOpenDrawer\('loc_bbbb0002'\)/, 'פרטים opens the drawer');
  assert.match(html, /<th>שם<\/th><th>סוג<\/th><th class="c-opt">כתובת<\/th><th>שימוש<\/th><th>מצב<\/th>/);
  const local = row(html, 'data-loc="loc_local"');
  assert.match(local, /title="דיסק השרת · ברירת המחדל לקליטה">מקומי</, 'the subtitle moved to the name title');
  assert.match(local, /<span class="st ok" title="מחובר — מעוגן ונקרא בבדיקה האחרונה">מחובר<\/span>/);
  assert.match(local, /614 GB \/ 926 GB<\/bdi><span class="pct">66%<\/span>/, 'stage 2: used / total with the percent next to the bar, not free / total');
  assert.match(local, /<div class="bar "[^>]*aria-valuenow="66"/);
  assert.deepEqual(menuOf(local).map(i => i[0]), ['פרטים', 'בדוק'], 'local is not removable');
  const san = row(html, 'data-loc="loc_bbbb0002"');
  assert.match(san, /<span class="st err" title="לא נגיש[^"]*">לא נגיש <span class="muted">· מאז 09:12<\/span><\/span>/);
  assert.match(san, /נמדד לאחרונה/, 'stale df is labelled, not shown as live');
  assert.match(san, /<span class="muted">האימג&#39;ים לא זמינים<\/span>|<span class="muted">האימג'ים לא זמינים<\/span>/);
  assert.deepEqual(menuOf(san).map(i => i[1]), ["storageOpenDrawer('loc_bbbb0002')", "storageCheck('loc_bbbb0002')", "storageDisconnect('loc_bbbb0002')"], 'remove only after disconnect');
  assert.match(san, /<span class="sub mono">10\.44\.3\.75:3260<\/span>/, 'portal under the IQN');
  assert.doesNotMatch(html, /legend-states/, 'the legend is the title of each state pill');
});
test('list: a disconnected location offers חבר + הסר; delete is a confirm sheet (#1270) and sends DELETE with confirm = the known name', async () => {
  const fixtures = {...BASE, '/storage-locations': {locations: [LOCAL, OFF], summary: {locations: 2, unreachable: 0, images: 6, unavailable_images: 0, free_bytes: 1, total_bytes: 2, checked_at: null}},
    'DELETE /storage-locations/loc_cccc0003': {ok: true}};
  const {run, requests} = setup(fixtures);
  await run('loadStorage()');
  const off = row(run('storagePage()'), 'data-loc="loc_cccc0003"');
  assert.deepEqual(menuOf(off).slice(-2), [['חבר', "storageConnect('loc_cccc0003')"], ['הסר', "storageDelete('loc_cccc0003')", 'danger']], 'destructive last, marked danger');
  run(`storageDelete('loc_cccc0003')`);
  assert.equal(run('sheets.length'), 1);
  assert.equal(run('sheets[0].danger'), true);
  assert.equal(run('sheets[0].fields'), undefined, '#1270: no typed field');
  assert.equal(requests.filter(r => r.method === 'DELETE').length, 0, 'nothing sent before the sheet is confirmed');
  await run(`sheets[0].onSubmit({})`);
  const d = requests.find(r => r.method === 'DELETE');
  assert.deepEqual(d, {url: '/storage-locations/loc_cccc0003', method: 'DELETE', body: {confirm: 'old-nas'}});
  // a location that is still connected refuses at the client too (the server says 409 anyway)
  run(`storageDelete('loc_local')`);
  assert.equal(run('sheets.length'), 1, 'no sheet for a non-disconnected location');
});

test('2. add → type: four cards in Proxmox order (folder → NFS → SMB → iSCSI), each with בחר, and the _netdev/R10 note', async () => {
  const {run} = setup(BASE);
  await run('loadStorage()');
  run(`storageGo('add')`);
  const html = run('storagePage()');
  assert.match(html, /שלב 1 מתוך 2/);
  const order = ['storageGo(\'local\')', 'storageGo(\'nfs\')', 'storageGo(\'smb\')', 'storageGo(\'iscsi-a\')'].map(s => html.indexOf(s));
  assert.ok(order.every((v, i) => v >= 0 && (i === 0 || v > order[i - 1])), 'order ' + order);
  assert.equal((html.match(/class="type"/g) || []).length, 4);
  assert.match(html, /_netdev/);
  assert.match(html, /R10/);
});

test('3. NFS: scan fills the picklist from POST /scan; save is disabled until POST /test says ok; a failed test keeps it disabled and shows the reason; save sends tested:true', async () => {
  let testOk = false;
  const fixtures = {...BASE,
    'POST /storage-locations/scan': {ok: true, items: [{export: '/mnt/pool/images', clients: '10.44.10.0/24 · rw'}, {export: '/mnt/pool/iso', clients: '10.44.10.0/24 · ro'}]},
    'POST /storage-locations/test': () => testOk ? {ok: true, free_bytes: 1.8 * 1024 ** 4, total_bytes: 4 * 1024 ** 4, warnings: ['האחסון (10.44.10.20) יושב ב-subnet של כרטיס ההפצה']} : {ok: false, reason: 'mount.nfs: access denied by server', warnings: []},
    'POST /storage-locations': (r) => ({...NAS, name: r.body.name})};
  const {run, requests, toasts} = setup(fixtures);
  await run('loadStorage()');
  run(`storageGo('nfs')`);
  let html = run('storagePage()');
  assert.match(html, /שלב 2 מתוך 2/);
  assert.match(html, /id="st-save" onclick="storageSave\(\)" disabled/, 'save starts disabled');
  assert.match(html, /<span class="st">לא נבדק<\/span>/);
  assert.match(html, /עדיין לא נסרק/);
  run(`storageField('server', '10.44.10.20')`);
  await run('storageScan()'); await settle();
  const scan = requests.find(r => r.url === '/storage-locations/scan');
  assert.deepEqual(scan.body, {type: 'nfs', server: '10.44.10.20'});
  html = run('storagePage()');
  assert.match(html, /class="picklist"/);
  assert.match(html, /\/mnt\/pool\/iso/);
  assert.match(html, /מורשה: 10\.44\.10\.0\/24 · rw/);
  run(`storagePick('export', '/mnt/pool/images'); storageField('name', 'nas-images')`);
  // test → fails
  await run('storageTest()'); await settle();
  html = run('storagePage()');
  const t1 = requests.filter(r => r.url === '/storage-locations/test');
  assert.deepEqual(t1[0].body, {type: 'nfs', params: {server: '10.44.10.20', export: '/mnt/pool/images', version: '4.1', readonly: false}});
  assert.match(html, /<span class="st err">נכשל<\/span>/);
  assert.match(html, /mount\.nfs: access denied by server/);
  assert.match(html, /id="st-save" onclick="storageSave\(\)" disabled/, 'failed test keeps save disabled');
  await run('storageSave()');
  assert.equal(requests.filter(r => r.method === 'POST' && r.url === '/storage-locations').length, 0, 'save without a green test sends nothing');
  assert.match(toasts.at(-1), /רק אחרי בדיקת חיבור מוצלחת/);
  // test → ok (with the R10 warning)
  testOk = true;
  await run('storageTest()'); await settle();
  html = run('storagePage()');
  assert.match(html, /<span class="st ok">הצליח<\/span>/);
  assert.match(html, /1\.8 TB/);
  assert.match(html, /class="note warn"/);
  assert.match(html, /subnet של כרטיס ההפצה/);
  assert.match(html, /id="st-save" onclick="storageSave\(\)" >שמור ועגן/, 'save enabled only now');
  // a change to a tested field invalidates the result (DOM-only, no re-render)
  run(`storageField('export', '/mnt/pool/iso')`);
  assert.equal(run('SV.test'), null);
  assert.match(run('storagePage()'), /disabled/);
  run(`storagePick('export', '/mnt/pool/images')`);
  await run('storageTest()'); await settle();
  await run('storageSave()'); await settle();
  const create = requests.find(r => r.method === 'POST' && r.url === '/storage-locations');
  assert.deepEqual(create.body, {name: 'nas-images', type: 'nfs', params: {server: '10.44.10.20', export: '/mnt/pool/images', version: '4.1', readonly: false}, tested: true});
  assert.equal(run('SV.view'), 'list', 'back to the list after save');
  assert.match(toasts.at(-1), /נשמר ועוגן/);
});

test('4. SMB: identity fields, scan sends the creds, params carry username/secret/domain; the secret is never rendered back', async () => {
  const fixtures = {...BASE, 'POST /storage-locations/scan': {ok: true, items: [{share: 'images', type: 'Disk'}, {share: 'software', type: 'Disk'}]},
    'POST /storage-locations/test': {ok: true, free_bytes: 640 * 1024 ** 3, total_bytes: 2 * 1024 ** 4, warnings: []}};
  const {run, requests} = setup(fixtures);
  await run('loadStorage()');
  run(`storageGo('smb')`);
  let html = run('storagePage()');
  assert.match(html, /SMB2 ומעלה · SMB1 אינו מוצע/);
  assert.match(html, /id="st-secret" type="password"/);
  run(`storageField('username','imagectl'); storageField('secret','p4ss'); storageField('domain','COLLEGE'); storageField('server','fs01.college.local')`);
  await run('storageScan()'); await settle();
  assert.deepEqual(requests.find(r => r.url === '/storage-locations/scan').body, {type: 'smb', server: 'fs01.college.local', creds: {username: 'imagectl', secret: 'p4ss', domain: 'COLLEGE'}});
  html = run('storagePage()');
  assert.match(html, /software/);
  run(`storagePick('share', 'images'); storageField('readonly', true)`);
  await run('storageTest()'); await settle();
  assert.deepEqual(requests.find(r => r.url === '/storage-locations/test').body, {type: 'smb', params: {server: 'fs01.college.local', share: 'images', username: 'imagectl', secret: 'p4ss', domain: 'COLLEGE', readonly: true}});
  html = run('storagePage()');
  assert.match(html, /<span class="st ok">הצליח<\/span>/);
  assert.match(html, /לקריאה בלבד — לא נבדקה/);
  assert.doesNotMatch(html.replace(/value="p4ss"/g, ''), /p4ss/, 'the secret appears only as the field value, never in text');
});

test('5. iSCSI א\': discover → pick target → CHAP fields → התחבר runs test → create → iscsi-login and shows the session; then 6. ב\' reads the disk', async () => {
  const created = {...SAN, state: 'unchecked', state_label: 'לא נבדק', params: {portal: PORTAL, iqn: IQN, auto: true, chap_user: 'imagectl'}};
  let disk = {device: '/dev/sdb', disk_status: 'fs', fs_type: 'ext4', fs_label: 'images', fs_uuid: '7c1e0000-0000-0000-0000-00000000a94f', fs_size: 800 * 1000 ** 3};
  const fixtures = {...BASE,
    'POST /storage-locations/scan': {ok: true, items: [{portal: PORTAL, iqn: IQN}, {portal: PORTAL, iqn: IQN.replace('images', 'backup')}]},
    'POST /storage-locations/test': {ok: true, free_bytes: null, total_bytes: null, warnings: [], targets: [{portal: PORTAL, iqn: IQN}]},
    'POST /storage-locations': created,
    'POST /storage-locations/loc_bbbb0002/iscsi-login': {ok: true, device_by_path: '/dev/sdb', iqn: IQN, portal: PORTAL},
    'GET /storage-locations/loc_bbbb0002/disk': () => disk,
    'POST /storage-locations/loc_bbbb0002/mount': {...created, state: 'connected', state_label: 'מחובר'},
    'POST /storage-locations/loc_bbbb0002/format': {...created, state: 'connected', state_label: 'מחובר'},
  };
  const {run, requests, toasts} = setup(fixtures);
  await run('loadStorage()');
  run(`storageGo('iscsi-a')`);
  let html = run('storagePage()');
  assert.match(html, /<span class="step cur">1 פורטל<\/span>/);
  assert.match(html, /id="st-login" onclick="storageIscsiLogin\(\)" disabled/);
  assert.match(html, /<span class="st">לא מחובר<\/span>/);
  assert.match(html, /עדיין לא בוצע גילוי/);
  assert.doesNotMatch(html, /id="st-chap_user"/, 'CHAP fields hidden until the box is ticked');
  run(`storageField('portal', PORTAL)`.replace('PORTAL', JSON.stringify(PORTAL)));
  await run('storageScan()'); await settle();
  assert.deepEqual(requests.find(r => r.url === '/storage-locations/scan').body, {type: 'iscsi', server: '', portal: PORTAL});
  html = run('storagePage()');
  assert.match(html, /<span class="step done">1 פורטל<\/span>/);
  assert.match(html, /<span class="step done">2 גילוי<\/span>/);
  assert.match(html, /freenas\.ctl:backup/);
  run(`storagePick('iqn', ${JSON.stringify(IQN)}); storageField('chap', true); storageField('chap_user', 'imagectl'); storageField('chap_secret', 'secretsecret'); storageField('name', 'san-lun1')`);
  html = run('storagePage()');
  assert.match(html, /id="st-chap_user"/);
  assert.match(html, /<span class="step done">3 יעד ו-CHAP<\/span>/);
  assert.match(html, /<span class="step cur">4 התחברות<\/span>/);
  assert.match(html, /id="st-login" onclick="storageIscsiLogin\(\)" >התחבר/);
  await run('storageIscsiLogin()'); await settle();
  const seq = requests.filter(r => r.method === 'POST').map(r => r.url);
  assert.deepEqual(seq, ['/storage-locations/scan', '/storage-locations/test', '/storage-locations', '/storage-locations/loc_bbbb0002/iscsi-login']);
  const create = requests.find(r => r.method === 'POST' && r.url === '/storage-locations');
  assert.deepEqual(create.body, {name: 'san-lun1', type: 'iscsi', params: {portal: PORTAL, iqn: IQN, auto: true, chap_user: 'imagectl', chap_secret: 'secretsecret'}, tested: true});
  html = run('storagePage()');
  assert.match(html, /<span class="st ok">session פעילה<\/span>/);
  assert.match(html, /\/dev\/sdb/);
  assert.match(html, /automatic — שורד אתחול שרת/);
  assert.match(html, /storageIscsiDisk\(\)/, 'המשך לשלב ב\'');
  assert.match(html, /<span class="step done">4 התחברות<\/span>/);
  // ---- ב' (א) מערכת קבצים קיימת: עגן בלי פירמוט (ברירת המחדל) או פרמט… מאחורי IQN
  await run('storageIscsiDisk()'); await settle();
  assert.equal(run('SV.view'), 'iscsi-b');
  html = run('storagePage()');
  assert.match(html, /<span class="step cur">5 הדיסק<\/span>/);
  assert.match(html, /<span class="pill ok">session פעילה<\/span>/);
  assert.match(html, /<span class="st ok">ext4<\/span> · תווית <span class="mono">images<\/span>/);
  assert.match(html, /7c1e…a94f/);
  assert.match(html, /class="btn primary" onclick="storageMount\('loc_bbbb0002'\)"/);
  assert.match(html, /class="btn danger" onclick="storageFormatSheet\('loc_bbbb0002'\)"/);
  assert.match(html, /<input value="san-lun1" disabled>/, 'name fixed after create');
  assert.match(html, /\/srv\/imagectl\/storage\/san-lun1/);
  run(`storageFormatSheet('loc_bbbb0002')`);
  assert.equal(run('sheets.length'), 1);
  assert.equal(run('sheets[0].danger'), true);
  assert.match(run('sheets[0].note'), /יימחקו לצמיתות/);
  assert.match(run('sheets[0].note'), /mkfs\.ext4 -L san-lun1 \/dev\/sdb/);
  assert.equal(run('sheets[0].fields'), undefined, '#1270: no typed IQN');
  assert.equal(requests.filter(r => r.url.endsWith('/format')).length, 0, 'nothing sent before the sheet is confirmed');
  await run(`sheets[0].onSubmit({})`); await settle();
  const fmt = requests.find(r => r.url.endsWith('/format'));
  assert.deepEqual(fmt.body, {confirm: IQN, wipe: true}, 'a disk with a filesystem is wiped only with wipe:true');
  assert.match(toasts.at(-1), /פורמט ועוגן/);
  assert.equal(run('SV.view'), 'list');
  // ---- ב' (ב) דיסק ריק: פירמוט הוא הראשי, בלי wipe; ---- (ג) unknown: אין כפתור פירמוט כלל
  run(`SV.view='iscsi-b'; SV.iscsi={session:'ok', loc: ${JSON.stringify(created)}, device:'/dev/sdb'}`);
  disk = {device: '/dev/sdb', disk_status: 'empty', fs_type: null, fs_label: null, fs_size: 800 * 1000 ** 3};
  await run('storageIscsiDisk()'); await settle();
  html = run('storagePage()');
  assert.match(html, /<span class="st">אין<\/span> — ‏blkid לא זיהה חתימה/);
  assert.match(html, /1 MB הראשונים והאחרונים — אפסים/);
  assert.match(html, /class="btn primary" onclick="storageFormatSheet\('loc_bbbb0002'\)"/);
  assert.doesNotMatch(html, /storageMount/, 'nothing to mount without a filesystem');
  run(`storageFormatSheet('loc_bbbb0002')`);
  await run(`sheets[1].onSubmit({confirm: ${JSON.stringify(IQN)}})`); await settle();
  assert.deepEqual(requests.filter(r => r.url.endsWith('/format')).at(-1).body, {confirm: IQN, wipe: false});
  run(`SV.view='iscsi-b'; SV.iscsi={session:'ok', loc: ${JSON.stringify(created)}, device:'/dev/sdb'}`);
  disk = {device: '/dev/sdb', disk_status: 'unknown', reason: 'blkid /dev/sdb: Input/output error', fs_size: null};
  await run('storageIscsiDisk()'); await settle();
  html = run('storagePage()');
  assert.match(html, /לא הצלחנו לקרוא את הדיסק/);
  assert.match(html, /Input\/output error/);
  assert.doesNotMatch(html, /storageFormatSheet|storageMount|פרמט/, 'unknown is not empty: no format button at all (principle 5)');
  assert.match(html, /onclick="storageIscsiDisk\(\)">נסה לקרוא שוב/);
  assert.match(html, /storageDisconnect\('loc_bbbb0002'\)">התנתק מהיעד/);
  assert.match(html, /מה לבדוק ב-NAS/);
  assert.match(html, /Initiators/);
  const before = requests.length;
  run(`storageFormatSheet('loc_bbbb0002')`);
  assert.equal(run('sheets.length'), 2, 'even a direct call refuses to open the format sheet on unknown');
  assert.equal(requests.length, before);
});

test('7. drawer for an unreachable location: reason + since, affected images by name from /images, what a round does, the three buttons explained, הסר only when disconnected', async () => {
  const {run, requests} = setup(BASE);
  await run('loadStorage()');
  run(`storageOpenDrawer('loc_bbbb0002')`); await settle();
  assert.ok(requests.some(r => r.url === '/images'), 'image names come from /images');
  // re-render with the images now loaded
  run(`storageOpenDrawer('loc_bbbb0002')`);
  const d = run('drawer');
  assert.match(d, /^san-lun1\|/);
  assert.match(d, /<span class="pill err">לא נגיש<\/span>/);
  assert.match(d, /class="note err"/);
  assert.match(d, /no route to 10\.44\.3\.75:3260/);
  assert.match(d, /מאז 18\/09\/2026 09:12 · המנטר מנסה שוב כל 60 שניות/);
  assert.match(d, /win-build v4/);
  assert.match(d, /office365 v2/);
  assert.doesNotMatch(d, /ubuntu/, 'only this location\'s images');
  assert.match(d, /האימג' לא זמין — <span class="mono">san-lun1<\/span> לא נגיש מאז 09:12/, 'the exact 409 wording the round will show');
  assert.match(d, /ההבדל בין הכפתורים/);
  assert.match(d, /storageCheck\('loc_bbbb0002'\)">בדוק עכשיו/);
  assert.match(d, /storageConnect\('loc_bbbb0002'\)">נסה שוב/);
  assert.match(d, /storageDisconnect\('loc_bbbb0002'\)">נתק/);
  assert.doesNotMatch(d, /storageDelete/, 'remove only after disconnect');
  assert.match(d, /selectPageById\('logs'\)">פתח ביומן/);
  // local: no disconnect/delete, and no "what happens in a round" scare
  run(`storageOpenDrawer('loc_local')`);
  const l = run('drawer');
  assert.doesNotMatch(l, /storageDisconnect|storageDelete|ההבדל בין הכפתורים|מה קורה בסבב/);
  assert.match(l, /312 GB/);
});

test('row actions call the right endpoints: בדוק → /check (re-renders the row; opens the drawer when not connected), נתק → confirm then /disconnect, חבר → /connect, בדוק הכול → every row', async () => {
  const checked = {...SAN, state: 'connected', state_label: 'מחובר', state_detail: '', unavailable_images: 0};
  const fixtures = {...BASE,
    'POST /storage-locations/loc_bbbb0002/check': checked,
    'POST /storage-locations/loc_local/check': LOCAL,
    'POST /storage-locations/loc_aaaa0001/check': {...NAS, state: 'unreachable', state_label: 'לא נגיש', state_detail: 'Stale file handle'},
    'POST /storage-locations/loc_bbbb0002/disconnect': {...SAN, state: 'disconnected', state_label: 'מנותק'},
    'POST /storage-locations/loc_bbbb0002/connect': checked};
  const {run, requests, toasts} = setup(fixtures);
  await run('loadStorage()');
  await run(`storageCheck('loc_bbbb0002')`); await settle();
  assert.equal(requests.at(-1).url, '/storage-locations/loc_bbbb0002/check');
  assert.match(toasts.at(-1), /san-lun1: מחובר/);
  assert.equal(run('storageLoc("loc_bbbb0002").state'), 'connected', 'the row is replaced from the response');
  run(`drawer=""`);
  await run(`storageCheck('loc_aaaa0001')`); await settle();
  assert.match(toasts.at(-1), /nas-images: לא נגיש — Stale file handle/);
  assert.match(run('drawer'), /^nas-images\|/, 'a failed check opens the drawer with the reason');
  run(`storageDisconnect('loc_bbbb0002')`);
  assert.equal(requests.filter(r => r.url.endsWith('/disconnect')).length, 0, 'disconnect waits for the confirm sheet');
  await run('sheets.at(-1).onSubmit()'); await settle();
  assert.equal(requests.filter(r => r.url.endsWith('/disconnect')).length, 1);
  await run(`storageConnect('loc_bbbb0002')`); await settle();
  assert.ok(requests.some(r => r.url.endsWith('/connect')));
  const n = requests.length;
  await run('storageCheckAll()'); await settle();
  const checks = requests.slice(n).filter(r => r.url.endsWith('/check')).map(r => r.url);
  assert.deepEqual(checks, ['/storage-locations/loc_local/check', '/storage-locations/loc_aaaa0001/check', '/storage-locations/loc_bbbb0002/check']);
});

test('a failed read is "not read", not "no locations": error pill, error note, no table; the refresh timer only re-reads on the list view', async () => {
  const {run} = setup({...BASE, '/storage-locations': new Error('שגיאה 500')});
  await run('loadStorage()');
  const html = run('storagePage()');
  assert.match(html, /<span class="pill err">לא נקרא<\/span>/);
  assert.match(html, /לא הצלחתי לקרוא את מיקומי האחסון/);
  assert.doesNotMatch(html, /<table/);
  assert.match(run('startStorageTimer.toString()'), /30000/);
  assert.match(run('startStorageTimer.toString()'), /SV\.view === "list"/);
});

test('8. health: storage_<id> rows are grouped under one "אחסון" row with a "פתח באחסון" action; the tree node is admin-only and sits after health', () => {
  const {run} = setup(BASE);
  const rows = run(`JSON.stringify(healthRows([
    {id: "server", label: "שרת (API)", state: "ok", detail: "uvicorn"},
    {id: "storage_loc_local", label: "מקומי", state: "ok", detail: "מחובר · 6 אימג'ים"},
    {id: "storage_loc_bbbb0002", label: "san-lun1", state: "bad", detail: "לא נגיש · no route"},
    {id: "udp_sender", label: "מולטיקאסט", state: "warn", detail: "לא נמצא"}]))`);
  const parsed = JSON.parse(rows);
  const group = parsed.find(r => r.html && r.html.includes('class="group"'));
  assert.ok(group, 'a group row');
  assert.match(group.html, /אחסון — מיקומים · 2/);
  const san = parsed.find(r => Array.isArray(r) && r[0].includes('san-lun1'));
  assert.match(san[1], /<span class="pill err">תקלה<\/span>/);   // stage 3: מצב הבדיקה הוא pill (המוקאפ המאושר)
  assert.match(san[3], /selectPageById\('storage'\)">פתח באחסון/);
  const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const node = index.slice(index.indexOf('data-page="storage"') - 40, index.indexOf('data-page="storage"') + 420);
  assert.match(node, /data-admin/);
  assert.match(node, /selectPageById\('storage'\)/);
  assert.match(node, /אחסון/);
  assert.ok(index.indexOf('data-page="health"') < index.indexOf('data-page="storage"') && index.indexOf('data-page="storage"') < index.indexOf('data-page="network"'), 'health → storage → network');
  assert.match(index, /<script src="storage\.js\?v=/);
});

test('images library: an image whose location is unreachable carries a "לא זמין" pill (registered, not deleted)', () => {
  const {run} = setup(BASE);
  run(`IMAGES=[{id:"a",name:"win-build v4",available:false,location_id:"loc_bbbb0002",os:"windows"},{id:"b",name:"ubuntu",available:true,location_id:"loc_local",os:"linux"}]; IMG.sel=new Set(); IMG.filter="";`);
  const a = run('imageRow(IMAGES[0]).cells[1]'), b = run('imageRow(IMAGES[1]).cells[1]');
  assert.match(a, /<span class="pill err">לא זמין<\/span>/);
  assert.doesNotMatch(b, /לא זמין/);
});

test('stage 2: capacity card = used/total/percent from the summary; unmeasured is "לא נמדד", never 0%; no invented backup card', async () => {
  const fresh = {...BASE, '/storage-locations': {locations: [LOCAL, NAS, SAN].map(l => ({...l})), summary: {...LIST.summary}}};   // not the shared LIST: earlier tests mutate it in place
  const {run} = setup(fresh);
  await run('loadStorage()');
  const html = run('storagePage()');
  assert.match(html, /<div class="card-h"><span>קיבולת הספרייה<\/span><\/div>/);
  assert.match(html, /<b><bdi dir="ltr">2\.8 TB<\/bdi> בשימוש<\/b><span class="muted">מתוך <bdi dir="ltr">4\.9 TB<\/bdi> · 22 אימג'ים<\/span>/, 'sum of the CONNECTED locations only (local + nas)');
  assert.match(html, /מיקום אחד לא נכלל בסכום — לא מחובר כרגע/, 'the unreachable san-lun1 is excluded, and the card says so');
  assert.match(html, /<span class="pct">57%<\/span>/, 'percent next to the bar');
  assert.doesNotMatch(html, /גיבוי/, 'the mockup backup card is invented — no such API');
  assert.match(run('UI.usage(null, 5)'), /לא נמדד/); assert.match(run('UI.usage(0, 0)'), /לא נמדד/);
  assert.doesNotMatch(run('UI.usage(null, null)'), /0%/);
  assert.match(run('UI.usage(1000, 250)'), /750 B \/ 1000 B|750 B/);
});

test('stage 2: capacity card keeps three states apart - never measured / last check failed / measured', async () => {
  const card = (locs, summary) => { const {run} = setup({...BASE, '/storage-locations': {locations: locs, summary: {locations: locs.length, unreachable: 0, images: 1, unavailable_images: 0, free_bytes: 0, total_bytes: 0, checked_at: null, ...summary}}}); return run('loadStorage()').then(() => run('storagePage()')); };
  const never = await card([{...LOCAL, state: 'unchecked', state_label: 'לא נבדק', free_bytes: null, total_bytes: null}], {});
  assert.match(never, /לא נמדד עדיין/); assert.doesNotMatch(never, /הבדיקה האחרונה לא הצליחה/);
  const failed = await card([{...NAS, state: 'unreachable', state_label: 'לא נגיש'}], {checked_at: '2026-09-18T09:40:00+00:00'});
  assert.match(failed, /הבדיקה האחרונה לא הצליחה — אין מיקום מחובר שנמדד כרגע \(נבדק לאחרונה 09:40\)/); assert.doesNotMatch(failed, /לא נמדד עדיין/);
  assert.doesNotMatch(failed, /בשימוש<\/b>/, 'a stale measurement is not presented as the current usage');
  const ok = await card([LOCAL], {checked_at: '2026-09-18T09:40:00+00:00'});
  assert.match(ok, /614 GB<\/bdi> בשימוש/); assert.doesNotMatch(ok, /לא נכלל/, 'nothing excluded - no sub-line');
});

test('#1988 add disk: only empty disks are selectable, the rest show why; the red button is disabled until a disk and a name; the sheet says all data is erased and sends confirm_erase with serial and size', async () => {
  const DISK = {device: '/dev/sdb', name: 'sdb', size_bytes: 4000787030016, model: 'WDC WD40EFRX', serial: 'WD-WCC7K1234567', bus: 'sata'};
  const fixtures = {...BASE,
    'GET /storage-locations/disks': {items: [DISK], hidden: [{device: '/dev/sdc', size_bytes: 2e12, model: 'ST2000', serial: 'Z1', bus: 'sata', reason: 'blkid מצא עליו ntfs'}]},
    'POST /storage-locations/add-disk': () => ({...LOCAL, id: 'loc_dddd0004', name: 'big-disk', type_label: 'כונן', removable: true})};
  const {run, requests} = setup(fixtures);
  await run('loadStorage()');
  assert.match(run('storagePage()'), /storageGo\('disk'\)[^<]*>[\s\S]*?הוסף כונן/, 'the list header offers הוסף כונן');
  run(`storageGo('disk')`);
  await settle();
  let html = run('storagePage()');
  assert.match(html, /name="st-pick-disk" value="\/dev\/sdb"/);
  assert.doesNotMatch(html, /name="st-pick-disk" value="\/dev\/sdc"/, 'a disk with data is not selectable');
  assert.match(html, /\/dev\/sdc[\s\S]*ntfs/, 'and the reason is shown');
  assert.match(html, /id="st-add-disk"[^>]*disabled/, 'disabled before a choice');
  run(`storagePick('disk','/dev/sdb')`);
  assert.match(run('storagePage()'), /id="st-add-disk"[^>]*disabled/, 'still disabled without a name');
  run(`SV.form.name='big-disk'`);
  html = run('storagePage()');
  assert.doesNotMatch(html, /id="st-add-disk"[^>]*disabled/);
  assert.match(html, /class="btn danger" id="st-add-disk"/);
  run('storageAddDiskSheet()');
  assert.equal(run('sheets.length'), 1);
  assert.equal(run('sheets[0].danger'), true);
  assert.match(run('sheets[0].sub + sheets[0].note'), /כל המידע בכונן יימחק/);
  await run('sheets[0].onSubmit({})');
  const sent = requests.find(r => r.method === 'POST' && r.url === '/storage-locations/add-disk');
  assert.deepEqual(sent.body, {device: '/dev/sdb', serial: 'WD-WCC7K1234567', size_bytes: 4000787030016, name: 'big-disk', confirm_erase: true});
  assert.equal(run('SV.view'), 'list');
});

test('#1988 add disk: a list that could not be read says so — it is not "no empty disk"', async () => {
  const err = Object.assign(new Error('lsblk לא נמצא במערכת'), {status: 503});
  const {run} = setup({...BASE, 'GET /storage-locations/disks': err});
  await run('loadStorage()');
  run(`storageGo('disk')`);
  await settle();
  const html = run('storagePage()');
  assert.match(html, /רשימת הכוננים לא נקראה/);
  assert.match(html, /lsblk/);
  assert.doesNotMatch(html, /name="st-pick-disk"/);
});
