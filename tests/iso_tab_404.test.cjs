// #404 — לשונית "ISO" בדף ספריית האימג'ים (מוקאפ 07/11, docs/design/mockups-1107b/404-iso.html):
// עץ תיקיות + טבלה + חיפוש, העלאה עם הצגת התקדמות ודחייה גלויה, ומחיקה בכפתור אדום בלי הקלדת מילה.
// בכוונה אין כאן "אתחל מחשב מה-ISO" ולא עמודת "אתחול ברשת" — ההכרעה על הגשת ה-ISO עוד לא התקבלה (#404),
// וכפתור שאין מאחוריו דבר היה שקר. הבדיקה מריצה את console.js + iso.js ב-vm עם fetch ו-XMLHttpRequest מזויפים.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function setup(fixtures, {role = 'admin', withIso = true} = {}) {
  const toasts = [], requests = [], xhrs = [], nodes = {};
  const node = (id) => nodes[id] || (nodes[id] = {id, value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
    classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {}, files: [], disabled: false,
    querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}, onclick: null});
  class XHR {
    constructor() { this.headers = {}; this.upload = {}; this.aborted = false; xhrs.push(this); }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(k, v) { this.headers[k] = v; }
    send(body) { this.body = body; }
    abort() { this.aborted = true; }
  }
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, Array, Object, Error,
    encodeURIComponent, decodeURIComponent, XMLHttpRequest: XHR,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: () => node('q'), getElementById: node,
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
  const files = ['progress.js', 'console.js'].concat(withIso ? ['iso.js'] : []);
  for (const f of files) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run(`ME={username:"u",role:"${role}",idle_seconds:300,capabilities:{}}; toast=(m)=>__toasts.push(m); current="images"; currentTab=3; globalThis.sheets=[]; sheet=(o)=>sheets.push(o); renderCurrent=()=>{ globalThis.__renders=(globalThis.__renders||0)+1; }; IMAGES=[]; FOLDERS=[]; OVERVIEW=null;`);
  ctx.__toasts = toasts;
  return {run, toasts, requests, xhrs, nodes, node};
}

const SHA_A = '3fa9' + 'e'.repeat(56) + 'c21e', SHA_B = 'b7d0' + '1'.repeat(56) + '19a4';
const ISO_A = {id: 'iso_aaaaaa', name: 'Windows 11 Pro 23H2', folder: 'Windows', filename: 'Win11_23H2_Hebrew_x64.iso', size: 5.9e9, sha256: SHA_A, uploaded_at: '2026-11-06T12:02:00+00:00', uploaded_by: 'noc', present: true};
const ISO_B = {id: 'iso_bbbbbb', name: 'Ubuntu 24.04', folder: '', filename: 'ubuntu.iso', size: 6.0e9, sha256: SHA_B, uploaded_at: '2026-11-03T07:41:00+00:00', uploaded_by: 'noc', present: true};
const LIST = {isos: [ISO_A, ISO_B], folders: [{name: 'Windows', isos: 1}, {name: 'Linux', isos: 0}]};
const loaded = async (fixtures = {'/isos': LIST}, opts) => { const s = setup(fixtures, opts); await s.run('loadIsos()'); return s; };

test('an admin gets a fourth tab "ISO" with its count; without iso.js or as another role the page keeps its three tabs', async () => {
  const {run} = await loaded();
  assert.equal(JSON.stringify(run('imagesTabs()')), JSON.stringify(['קבצים', 'קליטות', 'אחסון', 'ISO']));
  const html = run('images(3)');
  assert.match(html, /ISO<small>2<\/small>/, 'the count is on the tab');
  assert.equal(JSON.stringify(run('imagesTabs()').length), '4');
  const bare = setup({}, {withIso: false});
  assert.equal(bare.run('imagesTabs().length'), 3);
  const deploy = setup({}, {role: 'deploy'});
  assert.equal(deploy.run('imagesTabs().length'), 3, 'a deploy user has no console at all, and no ISO tab');
  const branch = await loaded();
  branch.run('SERVER_CTX = "sec_1"');
  assert.equal(branch.run('imagesTabs().length'), 3, 'a secondary server has no ISO library here');
});

test('the ISO tab shows upload + folder + refresh, and none of the image-only buttons', async () => {
  const {run} = await loaded();
  const html = run('images(3)');
  assert.match(html, /openIsoUpload\(\)/);
  assert.match(html, /addIsoFolderSheet\(\)/);
  assert.doesNotMatch(html, /openCapture|openImageIngest|addFolderSheet\(\)/);
  const files = run('images(0)');
  assert.match(files, /openCapture/, 'the files tab is unchanged');
  assert.doesNotMatch(files, /openIsoUpload/);
});

test('the table: name + original filename, size, shortened sha256 with the full one on hover, verified, uploaded; one ⋯ with a red delete', async () => {
  const {run} = await loaded();
  const html = run('isoTableHtml()');
  assert.match(html, /Windows 11 Pro 23H2/);
  assert.match(html, /Win11_23H2_Hebrew_x64\.iso/);
  assert.match(html, /5\.5 GB/, 'fmtBytes of 5.9e9');
  assert.ok(html.includes('3fa9…c21e'), 'short hash');
  assert.ok(html.includes(`title="${SHA_A}"`), 'full hash in the tooltip');
  assert.match(html, /אומת/);
  assert.match(html, /\d\d\/\d\d\/\d{4}/);
  const menus = [...html.matchAll(/data-menu="([^"]+)"/g)].map((m) => JSON.parse(m[1].replaceAll('&quot;', '"').replaceAll('&#39;', "'")));
  assert.equal(menus.length, 2);
  assert.deepEqual(menus[0], [['מחיקה…', "deleteIso('iso_aaaaaa')", 'danger']]);
});

test('no boot offer anywhere on the tab: not a button, not a column — the serving decision is still open', async () => {
  const {run} = await loaded();
  const all = run('images(3)');
  assert.doesNotMatch(all, /אתחל|אתחול ברשת|boot/i);
});

test('a name or filename that is markup is escaped, never executed', async () => {
  const evil = {...ISO_A, name: '<img src=x onerror=alert(1)>', filename: '"><script>x</script>.iso'};
  const {run} = await loaded({'/isos': {isos: [evil], folders: []}});
  const html = run('isoTableHtml()');
  assert.doesNotMatch(html, /<img src=x|<script>x/);
  assert.match(html, /&lt;img src=x/);
});

test('a file that is gone or resized is flagged in red, not shown as verified', async () => {
  const {run} = await loaded({'/isos': {isos: [{...ISO_A, present: false}], folders: []}});
  const html = run('isoTableHtml()');
  assert.match(html, /הקובץ חסר או השתנה בדיסק/);
  assert.doesNotMatch(html, /st ok/);
});

test('"not read" is not "no ISO": a failed read says so; an empty library offers the first upload', async () => {
  const failed = await loaded({'/isos': new Error('boom')});
  assert.equal(failed.run('ISOS'), null);
  const html = failed.run('isoTableHtml()');
  assert.match(html, /לא נקראה/);
  assert.match(html, /boom/);
  assert.doesNotMatch(html, /אין קובצי ISO/);
  const empty = await loaded({'/isos': {isos: [], folders: []}});
  assert.match(empty.run('isoTableHtml()'), /אין קובצי ISO בספרייה[\s\S]*openIsoUpload/);
});

test('an answer that is not the expected shape is "not read", not an empty list', async () => {
  const odd = await loaded({'/isos': {isos: 'nope'}});
  assert.equal(odd.run('ISOS'), null);
  assert.match(odd.run('isoTableHtml()'), /לא נקראה/);
});

test('folders: the tree counts, selecting filters, only an empty selected folder offers delete', async () => {
  const {run} = await loaded();
  const tree = run('isoTree()');
  assert.match(tree, /כל ה-ISO[\s\S]*cnt">2/);
  assert.match(tree, /Windows[\s\S]*cnt">1/);
  assert.match(tree, /Linux[\s\S]*cnt">0/);
  assert.match(tree, /ללא תיקייה/);
  run(`selectIsoFolder("Windows")`);
  let html = run('isoTableHtml()');
  assert.match(html, /Windows 11 Pro/);
  assert.doesNotMatch(html, /Ubuntu/);
  assert.doesNotMatch(run('isoTabCard()'), /deleteIsoFolder/, 'a folder with an ISO cannot be deleted');
  run(`selectIsoFolder("Linux")`);
  assert.match(run('isoTableHtml()'), /התיקייה ריקה/);
  assert.match(run('isoTabCard()'), /deleteIsoFolder\('Linux'\)/);
});

test('search narrows by name, filename and folder', async () => {
  const {run} = await loaded();
  run(`ISO_FILTER = "ubuntu"`);
  assert.deepEqual(JSON.parse(run('JSON.stringify(isoVisible().map(m => m.id))')), ['iso_bbbbbb']);
  run(`ISO_FILTER = "win11_23h2"`);
  assert.deepEqual(JSON.parse(run('JSON.stringify(isoVisible().map(m => m.id))')), ['iso_aaaaaa']);
  run(`ISO_FILTER = "zzz"`);
  assert.match(run('isoTableHtml()'), /אין ISO שתואמים לחיפוש/);
});

test('loadIsos repaints only when something changed, so a refresh does not wipe the search box', async () => {
  const s = await loaded();
  const before = s.run('globalThis.__renders || 0');
  await s.run('loadIsos()');
  assert.equal(s.run('globalThis.__renders || 0'), before, 'same data: no repaint');
  s.requests.length = 0;
  await s.run('loadIsos()');
  assert.equal(s.requests.filter((r) => r.url === '/isos').length, 1);
});

test('opening the images page reads the ISO list too (for the tab count), as an admin only', async () => {
  const s = setup({'/isos': LIST, '/images': [], '/folders': []});
  await s.run('loadImages()');
  await Promise.resolve();
  assert.ok(s.requests.some((r) => r.url === '/isos'));
  const d = setup({'/isos': LIST, '/images': [], '/folders': []}, {role: 'deploy'});
  await d.run('loadImages()');
  assert.ok(!d.requests.some((r) => r.url === '/isos'));
});

test('delete: a red-button sheet with no field to type, which posts to the delete route and reloads', async () => {
  const s = await loaded();
  s.run(`deleteIso('iso_aaaaaa')`);
  const opts = s.run('sheets[0]');
  assert.equal(opts.danger, true);
  assert.equal(opts.submitLabel, 'מחיקה');
  assert.ok(!(opts.fields || []).length, 'no typed confirmation (owner 10/10)');
  assert.match(opts.sub, /Windows 11 Pro 23H2/);
  assert.match(opts.note, /אינה הפיכה/);
  s.requests.length = 0;
  await opts.onSubmit({});
  assert.deepEqual(s.requests.map((r) => [r.method, r.url]), [['POST', '/isos/iso_aaaaaa/delete'], ['GET', '/isos']]);
  assert.ok(s.toasts.some((t) => /נמחק/.test(t)));
});

test('folder create and delete post to their routes', async () => {
  const s = await loaded();
  s.run('addIsoFolderSheet()');
  await s.run('sheets[0]').onSubmit({name: 'Tools'});
  assert.ok(s.requests.some((r) => r.method === 'POST' && r.url === '/iso-folders' && r.body.name === 'Tools'));
  s.run(`deleteIsoFolder('Linux')`);
  const del = s.run('sheets[1]');
  assert.equal(del.danger, true);
  await del.onSubmit({});
  assert.ok(s.requests.some((r) => r.method === 'POST' && r.url === '/iso-folders/Linux/delete'));
});

test('the mutating entry points refuse a non-admin without calling the server', async () => {
  const s = setup({'/isos': LIST}, {role: 'deploy'});
  s.run(`deleteIso('iso_aaaaaa'); addIsoFolderSheet(); deleteIsoFolder('x'); openIsoUpload();`);
  assert.equal(s.run('sheets.length'), 0);
  assert.equal(s.requests.length, 0);
});

test('the upload sheet: file drop zone, a name and a folder (the current folder preselected), per the mockup', async () => {
  const s = await loaded();
  s.run(`selectIsoFolder("Windows"); openIsoUpload();`);
  const o = s.run('sheets[0]');
  assert.equal(o.title, 'העלאת ISO');
  assert.equal(o.submitLabel, 'העלה');
  assert.match(o.note, /id="isoFile"/);
  assert.match(o.note, /isoUploadProgress/);
  assert.equal(JSON.stringify(o.fields.map((f) => f.id)), JSON.stringify(['name', 'folder']));
  const folder = o.fields[1];
  assert.equal(folder.value, 'Windows');
  assert.equal(JSON.stringify(folder.options.map((x) => x.value)), JSON.stringify(['', 'Windows', 'Linux']));
});

test('uploadIso sends the raw file with name, folder and original filename in the query', async () => {
  const s = await loaded();
  const file = {name: 'Win11 (final).iso', size: 5.9e9};
  const done = s.run('uploadIso')(file, {name: 'Windows 11', folder: 'Windows'});
  const x = s.xhrs[0];
  assert.equal(x.method, 'POST');
  const url = new URL(x.url, 'http://x');
  assert.equal(url.pathname, '/api/console/isos/upload');
  assert.equal(url.searchParams.get('name'), 'Windows 11');
  assert.equal(url.searchParams.get('folder'), 'Windows');
  assert.equal(url.searchParams.get('filename'), 'Win11 (final).iso');
  assert.equal(x.body, file, 'the body is the file itself, not multipart');
  x.upload.onprogress({lengthComputable: true, loaded: 3.7e9, total: 5.9e9});
  assert.match(s.node('isoUploadProgress').innerHTML, /63%/);
  x.upload.onload();
  assert.match(s.node('isoUploadProgress').innerHTML, /sha256/);
  x.status = 200; x.responseText = JSON.stringify({name: 'Windows 11'});
  s.requests.length = 0;
  x.onload();
  await done;
  assert.ok(s.toasts.some((t) => /Windows 11.*נוסף לספרייה/.test(t)));
  assert.ok(s.requests.some((r) => r.url === '/isos'), 'the list is reloaded');
});

test('a rejected upload surfaces the server reason verbatim, re-enables the button and leaves no progress bar', async () => {
  const s = await loaded();
  const reason = 'הקובץ אינו ISO תקין, ולא נשמר. לא נמצא מבנה ISO 9660 בקובץ (הוא נראה כמו ZIP).';
  const p = s.run('uploadIso')({name: 'win11-backup.zip', size: 5.9e9}, {name: 'x', folder: ''});
  const x = s.xhrs[0];
  assert.equal(s.node('sf-submit').disabled, true, 'no double submit while it runs');
  x.status = 400; x.responseText = JSON.stringify({detail: reason});
  x.onload();
  await assert.rejects(p, (e) => e.message === reason);
  assert.equal(s.node('sf-submit').disabled, false);
  assert.equal(s.node('isoUploadProgress').innerHTML, '');
});

test('a non-JSON error body and a dropped connection are still reported in Hebrew', async () => {
  const s = await loaded();
  const p1 = s.run('uploadIso')({name: 'a.iso', size: 1}, {name: 'a', folder: ''});
  s.xhrs[0].status = 502; s.xhrs[0].responseText = '<html>bad gateway</html>';
  s.xhrs[0].onload();
  await assert.rejects(p1, (e) => e.message === 'שגיאה 502');
  const p2 = s.run('uploadIso')({name: 'b.iso', size: 1}, {name: 'b', folder: ''});
  s.xhrs[1].onerror();
  await assert.rejects(p2, (e) => /החיבור נותק/.test(e.message));
});

test('closing the sheet while it uploads aborts the request, and the cancel button says so', async () => {
  const s = await loaded();
  let closed = 0;
  s.node('sf-cancel').onclick = () => { closed++; };
  s.node('sf-x').onclick = () => { closed++; };
  s.run('uploadIso')({name: 'a.iso', size: 1}, {name: 'a', folder: ''}).catch(() => {});
  assert.equal(s.node('sf-cancel').textContent, 'ביטול העלאה');
  s.node('sf-cancel').onclick();
  assert.equal(s.xhrs[0].aborted, true);
  assert.equal(closed, 1, 'the sheet still closes');
});

test('the name suggested from a filename is one the server accepts', async () => {
  const {run} = await loaded();
  assert.equal(run(`isoSuggestName("Win11_23H2_Hebrew_x64.iso")`), 'Win11_23H2_Hebrew_x64');
  assert.equal(run(`isoSuggestName("Win 11 (final)!.ISO")`), 'Win 11 _final');
  assert.ok(run(`isoSuggestName("x".repeat(100) + ".iso")`).length <= 48);
  assert.equal(run('isoSuggestName("")'), '');
});

test('an upload from a secondary-server context is refused before any request', async () => {
  const s = await loaded();
  s.run('SERVER_CTX = "sec_1"');
  await assert.rejects(s.run('uploadIso')({name: 'a.iso', size: 1}, {name: 'a', folder: ''}));
  assert.equal(s.xhrs.length, 0);
});
