// #1781 (מוקאפ שנדב אישר ב-07/11, PR #2675): "העבר לשרת משנה…" / "משוך משרת משנה…" בתפריט ⋯ של
// אימג' — חלון אחד עם מתג כיוון, בחירת שרת משנה ומיקום אחסון בצד המקבל; מיקום שהאימג' לא נכנס
// אליו מושבת עם המספרים. console.js + branch_transfer.js רצים ב-vm עם fetch מזויף ו-DOM קטן
// (רק מה שהחלון קורא: ערכי השדות, ההודעה, כפתור ההתחלה).
const {consoleFiles} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const GiB = 1024 ** 3;
const RESERVE = 10 * GiB;
const MARGIN = 256 * 1024 ** 2;

const stubNode = () => ({value: '', innerHTML: '', textContent: '', className: '', dataset: {}, style: {removeProperty() {}}, hidden: false,
  disabled: false, classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {},
  closest() { return this; }});

function setup(fixtures, me = {}) {
  const calls = [], toasts = [], registry = new Map();
  const node = (sel) => {
    sel = String(sel).replace(/^#/, '');
    if (!registry.has(sel)) registry.set(sel, stubNode());
    return registry.get(sel);
  };
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelectorAll: () => [], querySelector: node, getElementById: node,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options = {}) => {
      calls.push({url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
      const key = url.replace('/api/console', '').split('?')[0];
      if (fixtures[key] instanceof Error) return {status: Number(fixtures[key].message) || 500, ok: false, headers: {get: () => null}, json: async () => ({detail: 'אסור'})};
      if (key in fixtures) return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key]};
      if (options.method && options.method !== 'GET') return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({id: 't1', ok: true})};
      return {status: 404, ok: false, headers: {get: () => null}, json: async () => ({detail: 'אין'})};
    },
  });
  for (const f of ['progress.js', ...consoleFiles(), 'branches.js', 'branch_transfer.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  const meJson = JSON.stringify({timezone: 'UTC', username: 'admin', role: 'admin', idle_seconds: 300,
    capabilities: {interbranch_transfer: true, enroll_secondary: true}, ...me});
  run(`ME=${meJson}; toast=(m)=>__toasts.push(m); current="images"; currentTab=0;`);
  ctx.__toasts = toasts;
  return {run, calls, toasts, node};
}

const loc = (id, name, free, state = 'connected', extra = {}) =>
  ({id, name, state, state_label: state === 'connected' ? 'מחובר' : 'לא מחובר', free_bytes: free, total_bytes: 2000 * GiB, ...extra});
const IMG = {id: 'img_off', name: 'office365 v4', total_compressed_bytes: 19.5 * GiB, folder: ''};
const base = {
  '/storage-nodes': [{id: 'n_haifa', label: 'חיפה', disabled_at: null}, {id: 'n_off', label: 'אילת', disabled_at: '2026-10-01T00:00:00+00:00'}],
  '/storage-transfers': [],
  '/storage-locations': {locations: [loc('loc_local', 'מקומי', 12 * GiB), loc('loc_nas1', 'NAS מכללה', 1800 * GiB), loc('loc_down', 'דיסק D:', 900 * GiB, 'unreachable')]},
  '/overview': {storage: {reserve_bytes: RESERVE}},
  '/storage-nodes/n_haifa/console/storage-locations': {locations: [loc('loc_local', 'מקומי', 310 * GiB), loc('loc_2', 'דיסק נוסף', 25 * GiB)]},
  '/storage-nodes/n_haifa/console/overview': {storage: {reserve_bytes: RESERVE}},
  '/storage-nodes/n_haifa/images': {connected: true, error: null, images: [
    {id: 'img_off', name: 'office365 v4', size_bytes: 19.5 * GiB}, {id: 'img_new', name: 'ubuntu-lab 25.04', size_bytes: 9 * GiB}, {id: 'img_have', name: 'win11-edu', size_bytes: 31 * GiB}]},
};

// ---------- ה"נכנס": מספרים שנמדדו, לא ניחוש ----------

test('btFit: need = image + 256MiB + the receiver\'s reserve; short says by how much; an unmounted location is not a number question; unread is "unknown"', () => {
  const {run} = setup(base);
  const fit = (size, reserve, l) => JSON.parse(run(`JSON.stringify(btFit(${size}, ${reserve}, ${JSON.stringify(l)}))`));
  const need = 19.5 * GiB + MARGIN + RESERVE;
  assert.deepEqual(fit(19.5 * GiB, RESERVE, loc('a', 'A', need)), {ok: true, need, free: need}, 'exactly enough fits');
  const short = fit(19.5 * GiB, RESERVE, loc('a', 'A', 12 * GiB));
  assert.equal(short.ok, false); assert.equal(short.need, need); assert.equal(short.missing, need - 12 * GiB);
  assert.equal(fit(1, RESERVE, loc('a', 'A', 1000 * GiB, 'unreachable')).ok, false, 'a location that is not connected never fits');
  assert.match(fit(1, RESERVE, loc('a', 'A', 1000 * GiB, 'unreachable')).reason, /לא זמין/);
  assert.equal(fit(19.5 * GiB, null, loc('a', 'A', 1000 * GiB)).ok, null, 'reserve unread → unknown, not "fits"');
  assert.equal(fit(19.5 * GiB, RESERVE, loc('a', 'A', null)).ok, null, 'free not measured → unknown');
  assert.equal(fit(0, RESERVE, loc('a', 'A', 1000 * GiB)).ok, null, 'size not reported → unknown');
});

// ---------- התפריט ----------

test('the image ⋯ menu offers both directions to an admin with interbranch_transfer, in the middle (delete stays last), and nothing otherwise', () => {
  const {run} = setup(base);
  const items = JSON.parse(run(`JSON.stringify(branchTransferMenuItems('img_off'))`));
  assert.deepEqual(items.map((i) => i[0]), ['העבר לשרת משנה…', 'משוך משרת משנה…']);
  assert.equal(items[0][1], "openBranchTransfer('push','img_off')");
  assert.equal(items[1][1], "openBranchTransfer('pull','img_off')");
  run(`IMAGES=[${JSON.stringify(IMG)}]; IMG.sel=new Set();`);
  const row = run(`imageRow(${JSON.stringify(IMG)}).cells.at(-1)`);
  const menu = JSON.parse(row.match(/data-menu="([^"]*)"/)[1].replaceAll('&quot;', '"').replaceAll('&#39;', "'"));
  const labels = menu.map((i) => i[0]);
  assert.ok(labels.includes('העבר לשרת משנה…') && labels.includes('משוך משרת משנה…'));
  assert.equal(labels.at(-1), 'מחיקה…', 'the destructive item stays at the bottom');
  assert.ok(labels.indexOf('העבר לתיקייה…') < labels.indexOf('העבר לשרת משנה…'));
  run('ME.capabilities.interbranch_transfer=false');   // a secondary server, or a standalone with no secondary registered
  assert.equal(run('branchTransferMenuItems("x").length'), 0);
  const quiet = run(`imageRow(${JSON.stringify(IMG)}).cells.at(-1)`);
  assert.doesNotMatch(quiet, /שרת משנה…/);
});

// ---------- החלון: כיוון, שרת משנה, מיקום ----------

async function open(s, dir) {
  s.run('globalThis.__sheet=null; sheet=(o)=>{ __sheet=o; }; findImage=(id)=>id==="img_off"?' + JSON.stringify(IMG) + ':null;');
  s.run('IMAGES=[' + JSON.stringify(IMG) + ', {id:"img_have", name:"win11-edu"}]');
  await s.run(`openBranchTransfer('${dir}','img_off')`);
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  return s.run('__sheet');
}
// the window's controls as the operator left them
const set = (s, vals) => { for (const [id, v] of Object.entries(vals)) s.node(id).value = v; };
const view = (s, vals) => { set(s, vals); return JSON.parse(s.run(`JSON.stringify((()=>{ return btView(__stobj, btValues()); })())`)); };

test('the window: a direction switch, only the enabled secondary servers, a read-only image, and a location select; not opened for a non-admin', async () => {
  const s = setup(base);
  const sheet = await open(s, 'push');
  assert.equal(sheet.title, 'העברת אימג\' לשרת משנה');
  const ids = sheet.fields.map((f) => f.id);
  assert.equal(JSON.stringify(ids), JSON.stringify(['dir', 'node', 'limg', 'rimg', 'loc', 'fit']));
  assert.equal(JSON.stringify(sheet.fields[0].options.map((o) => o.value)), JSON.stringify(['push', 'pull']));
  assert.equal(sheet.fields[0].type, 'segment');
  assert.equal(JSON.stringify(sheet.fields[1].options.map((o) => o.label)), JSON.stringify(['חיפה']), 'a disabled secondary is not offered');
  assert.equal(sheet.fields[2].options.length, 1, 'push: the image is the row\'s, nothing to choose');
  assert.equal(sheet.fields[5].type, 'html');
  assert.ok(!s.run('JSON.stringify(__sheet)').includes('"danger":true'), 'a safe, reversible action: no red button, no typed word');
  const guest = setup(base, {role: 'deploy'});
  guest.run('sheet=()=>{ throw new Error("opened") }');
  await guest.run(`openBranchTransfer('push','img_off')`);
  assert.equal(guest.calls.length, 0, 'a non-admin makes no request');
});

test('push: the location comes from the secondary; "מקומי" that cannot hold the image is disabled with the numbers, the default moves to one that fits', async () => {
  const s = setup({...base, '/storage-nodes/n_haifa/console/storage-locations': {locations: [loc('loc_local', 'מקומי', 12 * GiB), loc('loc_2', 'דיסק נוסף', 640 * GiB)]}});
  await open(s, 'push');
  s.run(`__stobj = ({ img: ${JSON.stringify(IMG)}, nodes: ${JSON.stringify(base['/storage-nodes'])}.filter(n=>!n.disabled_at), main: {done:true}, remote: {n_haifa: ${JSON.stringify({done: true, locations: [loc('loc_local', 'מקומי', 12 * GiB), loc('loc_2', 'דיסק נוסף', 640 * GiB)], reserve: RESERVE, images: [], err: {}})}} })`);
  const v = view(s, {'sf-dir': 'push', 'sf-node': 'n_haifa', 'sf-loc': ''});
  const local = v.locOptions.find((o) => o.value === 'loc_local');
  assert.equal(local.disabled, true);
  assert.match(local.label, /לא נכנס: חסרים 17\.8 GB/);
  assert.equal(v.locOptions.find((o) => o.value === 'loc_2').disabled, false);
  assert.equal(v.locValue, 'loc_2', 'the default is the first that fits');
  assert.equal(v.canSubmit, true);
  assert.match(v.noteHtml, /נכנס\./);
  assert.match(v.noteHtml, /דיסק נוסף/);
  assert.equal(v.dir, 'push'); assert.equal(v.imageId, 'img_off');
  assert.equal(v.submitLabel, 'התחל העברה');
});

test('nothing fits: "מקומי" stays selected, the start button is disabled, and the note says needed / free / missing', async () => {
  const s = setup(base);
  await open(s, 'push');
  s.run(`__stobj = ({ img: ${JSON.stringify(IMG)}, nodes: [{id:'n_haifa',label:'חיפה'}], main: {done:true}, remote: {n_haifa: ${JSON.stringify({done: true, locations: [loc('loc_local', 'מקומי', 12 * GiB), loc('loc_2', 'דיסק נוסף', 14 * GiB)], reserve: RESERVE, images: [], err: {}})}} })`);
  const v = view(s, {'sf-dir': 'push', 'sf-node': 'n_haifa', 'sf-loc': ''});
  assert.equal(v.locValue, 'loc_local');
  assert.equal(v.canSubmit, false);
  assert.match(v.noteHtml, /לא נכנס\./);
  assert.match(v.noteHtml, /נדרשים/); assert.match(v.noteHtml, /פנויים במקומי/);   // Hebrew name: no hyphen
  assert.match(s.run("btIn('NAS מכללה')"), /^ב-NAS/); assert.equal(s.run("btIn('חיפה')"), 'בחיפה'); assert.match(v.noteHtml, /חסרים/);
  assert.match(v.noteHtml, /ההעברה לא תתחיל/);
});

test('pull: the image is chosen from the secondary\'s library (one the main already has is disabled), and the location is the MAIN\'s with the main\'s reserve', async () => {
  const s = setup(base);
  await open(s, 'pull');
  const haifa = {done: true, locations: [loc('loc_local', 'חיפה-מקומי', 5 * GiB)], reserve: 99 * GiB, err: {},
    images: [{id: 'img_off', name: 'office365 v4', size_bytes: 19.5 * GiB}, {id: 'img_have', name: 'win11-edu', size_bytes: 31 * GiB}, {id: 'img_new', name: 'ubuntu-lab 25.04', size_bytes: 9 * GiB}]};
  s.run(`__stobj = ({ img: ${JSON.stringify(IMG)}, nodes: [{id:'n_haifa',label:'חיפה'}], main: ${JSON.stringify({done: true, locations: base['/storage-locations'].locations, reserve: RESERVE, err: {}})}, remote: {n_haifa: ${JSON.stringify(haifa)}} })`);
  s.run('IMAGES=[{id:"img_off"},{id:"img_have"}]');   // the main already has these two
  const v = view(s, {'sf-dir': 'pull', 'sf-node': 'n_haifa', 'sf-rimg': '', 'sf-loc': ''});
  assert.equal(v.dir, 'pull'); assert.equal(v.submitLabel, 'התחל משיכה'); assert.match(v.title, /משיכת אימג'/);
  assert.equal(v.showRemoteImage, true);
  assert.deepEqual(v.remoteOptions.map((o) => [o.value, o.disabled]), [['img_off', true], ['img_have', true], ['img_new', false]]);
  assert.match(v.remoteOptions[0].label, /קיים בראשי/);
  assert.equal(v.imageId, 'img_new', 'the default is the first the main does not have');
  assert.deepEqual(v.locOptions.map((o) => o.value), ['loc_local', 'loc_nas1', 'loc_down'], "the main's own locations, not the secondary's");
  assert.deepEqual(v.locOptions.map((o) => o.disabled), [true, false, true], 'מקומי (12 GB free) cannot hold 9 GB + 256MiB + 10GiB, the unmounted one never can');
  assert.match(v.locOptions[0].label, /לא נכנס: חסרים /);
  assert.equal(v.locValue, 'loc_nas1');
  assert.equal(v.canSubmit, true);
  assert.equal(v.locLabel, 'מיקום אחסון בראשי');
  // 9 GiB + margin + the MAIN's 10 GiB reserve — not the secondary's 99 GiB
  assert.equal(v.fit.need, 9 * GiB + MARGIN + RESERVE);
});

test('pull: everything the secondary has is already on the main → nothing to pull, start disabled; a secondary library that was not read is said, not an empty list', async () => {
  const s = setup(base);
  await open(s, 'pull');
  const st = (haifa) => `__stobj = ({ img: ${JSON.stringify(IMG)}, nodes: [{id:'n_haifa',label:'חיפה'}], main: ${JSON.stringify({done: true, locations: base['/storage-locations'].locations, reserve: RESERVE, err: {}})}, remote: {n_haifa: ${JSON.stringify(haifa)}} })`;
  s.run('IMAGES=[{id:"img_off"}]');
  s.run(st({done: true, locations: [], reserve: RESERVE, err: {}, images: [{id: 'img_off', name: 'office365 v4', size_bytes: 1}]}));
  let v = view(s, {'sf-dir': 'pull', 'sf-node': 'n_haifa'});
  assert.equal(v.canSubmit, false); assert.match(v.noteHtml, /אין בחיפה אימג' למשוך/);
  s.run(st({done: true, locations: [], reserve: RESERVE, images: null, err: {images: 'connect timed out'}}));
  v = view(s, {'sf-dir': 'pull', 'sf-node': 'n_haifa'});
  assert.equal(v.canSubmit, false); assert.match(v.noteHtml, /רשימת האימג'ים של חיפה לא נקראה:<\/b> connect timed out/);
});

test('push: an image the secondary already has is said, not offered; still-loading data blocks the button; an unread reserve is said and the server re-checks', async () => {
  const s = setup(base);
  await open(s, 'push');
  const stFor = (haifa) => `__stobj = ({ img: ${JSON.stringify(IMG)}, nodes: [{id:'n_haifa',label:'חיפה'}], main: {done:true}, remote: {n_haifa: ${JSON.stringify(haifa)}} })`;
  s.run(stFor({done: false}));
  let v = view(s, {'sf-dir': 'push', 'sf-node': 'n_haifa'});
  assert.equal(v.canSubmit, false); assert.match(v.noteHtml, /טוען/);
  s.run(stFor({done: true, locations: [loc('loc_local', 'מקומי', 500 * GiB)], reserve: RESERVE, err: {}, images: [{id: 'img_off', name: 'office365 v4'}]}));
  v = view(s, {'sf-dir': 'push', 'sf-node': 'n_haifa'});
  assert.equal(v.canSubmit, false); assert.match(v.noteHtml, /כבר קיים בחיפה/);
  s.run(stFor({done: true, locations: [loc('loc_local', 'מקומי', 500 * GiB)], reserve: null, err: {reserve: 'x'}, images: []}));
  v = view(s, {'sf-dir': 'push', 'sf-node': 'n_haifa'});
  assert.equal(v.fit.ok, null);
  assert.match(v.noteHtml, /הרזרבה למערכת לא נקראה/);
  assert.match(v.noteHtml, /השרת יבדוק שוב/);
  assert.doesNotMatch(v.noteHtml, /נכנס\./, 'unknown is never shown as "fits"');
  s.run(stFor({done: true, locations: null, reserve: RESERVE, err: {locations: 'שרת המשנה לא ענה'}, images: []}));
  v = view(s, {'sf-dir': 'push', 'sf-node': 'n_haifa'});
  assert.equal(v.canSubmit, false); assert.match(v.noteHtml, /מיקומי האחסון בשרת המשנה חיפה לא נקראו/);
});

// ---------- שליחה ----------

test('submitting posts to /transfer (push) or /pull with the chosen location_id, refuses a location that does not fit, and the transfer lands in the tasks list', async () => {
  const s = setup({...base, '/storage-transfers': [{id: 't1', node_id: 'n_haifa', node_label: 'חיפה', image_id: 'img_new', image_name: 'ubuntu-lab 25.04', direction: 'pull', state: 'sending', bytes_sent: 1, bytes_total: 9, created_at: '2026-11-07T10:00:00Z', started_by: 'admin'}]});
  const sheet = await open(s, 'pull');
  // the live window: everything the operator sees and chose
  set(s, {'sf-dir': 'pull', 'sf-node': 'n_haifa', 'sf-rimg': 'img_new', 'sf-loc': 'loc_nas1'});
  s.run('IMAGES=[{id:"img_off"},{id:"img_have"}]');
  // wait for the window's own data load to finish (it fetched the locations, reserve and library itself)
  for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
  s.run('HOME.transfers=null; HOME.err={}; CAPTURE_TASKS=[]; renderActivity=()=>{ globalThis.__rendered = (globalThis.__rendered||0)+1; }');
  await sheet.onSubmit({});
  const pull = s.calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/n_haifa/pull'));
  assert.ok(pull, 'POST …/pull');
  assert.deepEqual(pull.body, {image_id: 'img_new', location_id: 'loc_nas1'});
  assert.ok(!s.calls.some((c) => c.method === 'POST' && c.url.endsWith('/transfer')), 'the other direction was not touched');
  assert.equal(s.run('HOME.transfers.length'), 1, 'the tasks list was refreshed from /storage-transfers');
  assert.ok(s.run('__rendered') >= 1);
  assert.match(s.toasts.at(-1), /המשיכה התחילה — היא מופיעה במשימות/);
  // push, and a location that does not fit is refused with no request at all
  const push = await open(s, 'push');
  set(s, {'sf-dir': 'push', 'sf-node': 'n_haifa', 'sf-loc': 'loc_2'});
  for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r));
  const before = s.calls.length;
  await assert.rejects(push.onSubmit({}), /לא נכנס|עדיין נטענים|לא נקראו/);
  assert.equal(s.calls.slice(before).filter((c) => c.method === 'POST').length, 0, 'refused without a request');
});

// ---------- משימות: ההעברה מופיעה שם ----------

test('a running transfer is a task in the dock with its direction; a pull has its destination = the main and a cancel on the tasks page', () => {
  const s = setup(base);
  s.run('OVERVIEW=null; CAPTURE_TASKS=[]; ME.server_name="TA"');
  s.run(`HOME.transfers=[
    {id:'p1', direction:'pull', node_id:'n_haifa', node_label:'חיפה', image_name:'ubuntu-lab 25.04', state:'sending', bytes_sent:1, bytes_total:4, created_at:'2026-11-07T10:00:00Z', started_by:'admin'},
    {id:'s1', direction:'push', node_id:'n_haifa', node_label:'חיפה', image_name:'office365 v4', state:'verifying', bytes_sent:4, bytes_total:4, created_at:'2026-11-07T10:01:00Z', started_by:'admin'},
    {id:'d1', direction:'push', node_id:'n_haifa', node_label:'חיפה', image_name:'Old', state:'done'}]`);
  const rows = JSON.parse(s.run('JSON.stringify(activityRows().rows)'));
  assert.equal(rows.length, 2, 'running ones only');
  assert.equal(rows[0].name, 'משיכה מחיפה: ubuntu-lab 25.04');
  assert.equal(rows[0].target, 'TA', 'a pull lands on the main');
  assert.equal(rows[1].name, 'office365 v4'); assert.equal(rows[1].target, 'חיפה');
  assert.equal(rows[1].label, 'מאמת');
  const page = s.run('homeNowRows(true)').map((r) => r.join('|')).join('\n');
  assert.match(page, /cancelBranchTransfer\('p1'\)/, 'a running pull can be cancelled from the tasks page');
  assert.doesNotMatch(page, /cancelBranchTransfer\('s1'\)/, 'only a pull is cancellable (the server says so)');
});
