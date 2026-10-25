// ביקורת לחיצות 16/10 (#1016) — התיקונים בקונסולה:
// ביטול קליטה פעילה נגיש במקלדת ובמגע, בלי אנגלית פנימית במסכים, "grp_grp_X",
// הפעלת משתמש אינה בחלון אדום, ומחיקת כיתה עם מחשבים לא מבטיחה מה שהשרת מסרב לו.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');

function setupConsole() {
  const node = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
    classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, insertAdjacentHTML(_p, html) { this.innerHTML += html; },
    setAttribute() {}, removeAttribute() {}, focus() {}});
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, String, Array, RegExp, encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelector: node, querySelectorAll: () => [], getElementById: node,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); }, get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false})}, localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, opts) => { (ctx.__fetches ||= []).push([url, opts && opts.body ? JSON.parse(opts.body) : null]); return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}; },
  });
  vm.runInContext(read('progress.js'), ctx);
  vm.runInContext(read('console.js'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  run.fetches = () => ctx.__fetches || [];
  return run;
}

test('1016/2: the active-capture cancel button is always visible (not visibility:hidden until hover)', () => {
  const css = read('console.css').replace(/\/\*[\s\S]*?\*\//g, '');
  assert.match(css, /\.page \.dg td \.acts\.always[^{]*\{[^}]*visibility:\s*visible/, 'an .acts.always rule must force visibility:visible');
  assert.match(css, /\.acts\.always \.btn:focus-visible\{[^}]*outline:/, 'and the button has a focus ring');
  const js = read('console.js');
  const cancels = [...js.matchAll(/<div class="([^"]*)"><button class="btn sm danger" onclick="cancelCapture\(/g)];
  assert.equal(cancels.length, 2, 'both capture-cancel buttons (images card, builder row)');
  for (const m of cancels) assert.match(m[1], /\balways\b/);
});

test('1016/3: no internal English labels on screen', () => {
  const js = read('console.js');
  assert.ok(!js.includes('<span class="v">Authenticated</span>'), 'Authenticated label');
  assert.ok(!js.includes('פרטי Session'), 'Session details title');
  assert.ok(!js.includes('מ-/room/history'), 'no API path in the user-facing history caption');
  assert.ok(!js.includes('· family ${'), 'no "family 256" in the image drawer');
});

test('1016/3: the image drawer shows the source disk size without "family"', () => {
  const run = setupConsole();
  run('ME={username:"admin",role:"admin",capabilities:{}};');
  run(`IMAGES=[{id:'img_1',name:'x',folder:'',family:256,os:'windows',created:'2026-01-01T00:00:00Z',source_disk_bytes:256060514304,total_compressed_bytes:1,partitions:2}]; CAPTURE_TASKS=[];`);
  const html = String(run('imageDrawerHtml(IMAGES[0])'));
  assert.match(html, /דיסק מקור/, 'the row is still there');
  assert.ok(!/family/i.test(html), 'but no "family 256"');
});

test('1016/3: "grp_X" typed as the short id does not become grp_grp_X', async () => {
  const run = setupConsole();
  run('ME={username:"admin",role:"admin",capabilities:{}}; classroomsOn=()=>true; globalThis.__posts=[]; globalThis.__sheet=null;');
  run('sheet=(o)=>{globalThis.__sheet=o;}; loadMachines=async()=>{};');
  for (const [typed, want] of [['grp_LAB5', 'grp_LAB5'], ['GRP_LAB5', 'grp_LAB5'], ['LAB5', 'grp_LAB5'], ['  ', '']]) {
    const before = run.fetches().length;
    run('addGroupSheet()');
    await run(`globalThis.__sheet.onSubmit({id:${JSON.stringify(typed)},label:'x'})`);
    const sent = run.fetches()[before];
    assert.equal(sent[0], '/api/console/groups');
    assert.equal(sent[1].id, want, `typed "${typed}"`);
  }
});

test('1016/3: enabling a user is not shown as a red danger sheet; disabling still is', () => {
  const run = setupConsole();
  run('ME={username:"admin",role:"admin",capabilities:{}}; globalThis.__sheets=[];');
  run('sheet=(o)=>{globalThis.__sheets.push(o);}; findUser=(n)=>({username:n});');
  run('userDisable("bob", false); userDisable("bob", true);');
  assert.equal(run('globalThis.__sheets[0].danger'), false, 'הפעלת משתמש: כפתור רגיל');
  assert.equal(run('globalThis.__sheets[1].danger'), true, 'השבתה נשארת אדומה');
});

test('1016/4: deleting a class that still has machines does not open a sheet promising to delete them', () => {
  const run = setupConsole();
  run('ME={username:"admin",role:"admin",capabilities:{}}; globalThis.__sheets=[]; globalThis.__toasts=[];');
  run('sheet=(o)=>{globalThis.__sheets.push(o);}; toast=(m,ms,k)=>{globalThis.__toasts.push([m,k]);};');
  run('GROUPS=[{id:"grp_LAB1",label:"LAB1"},{id:"grp_EMPTY",label:"E"}]; MACHINES=[{mac:"a",group_id:"grp_LAB1"},{mac:"b",group_id:"grp_LAB1"}];');
  run('machineGroupId=(m)=>m.group_id;');
  run('deleteGroup("grp_LAB1")');
  assert.equal(run('globalThis.__sheets.length'), 0);
  assert.match(run('globalThis.__toasts[0][0]'), /בכיתה יש 2 מחשבים/);
  assert.equal(run('globalThis.__toasts[0][1]'), 'err');
  run('deleteGroup("grp_EMPTY")');
  assert.equal(run('globalThis.__sheets.length'), 1);
  assert.doesNotMatch(run('globalThis.__sheets[0].note'), /על כל \d+ המחשבים/);
});

test('1016: cache-busting bumped to 11.47 or later everywhere', () => {
  // one version everywhere is test_console_static's; here: not older than this fix
  const html = read('index.html');
  // major.minor, not 11.x only: the lab branch passed 11.99 to 12.00 (int-1021b, 21/10)
  const vs = [...html.matchAll(/\?v=(\d+)\.(\d+)/g)].map((m) => [Number(m[1]), Number(m[2])]);
  assert.ok(vs.length > 0 && vs.every(([a, b]) => a > 11 || (a === 11 && b >= 47)), String(vs));
});
