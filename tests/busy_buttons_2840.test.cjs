// ‏#2840 (המשך): כל כפתור שמחכה לשרת — כבוי, ‏aria-busy, גלגל, ולחיצה נוספת נבלעת.
// ‏sheet() כבר כך מאז #2860 (windows_frame.test.cjs); כאן `withBusy` שמכסה את כל השאר:
// חלון openModalContent (למשל "הוסף" מחשב), כפתורי onclick ישירים (למשל "שכפל כלל") ו-vmuAct.
// בלי דפדפן: console*.js + email_alerts.js + vmupdate.js ב-vm מול DOM מזערי; כל POST מחכה עד release().
const {consoleFiles} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

// כפתור עם מצב אמיתי (disabled / attributes / classList), לא stub שבולע הכול.
function button(text = 'אישור') {
  const attrs = new Map(), classes = new Set();
  return {nodeType: 1, disabled: false, textContent: text, value: '', innerHTML: '', dataset: {}, style: {removeProperty() {}}, hidden: false,
    setAttribute(k, v) { attrs.set(k, String(v)); }, removeAttribute(k) { attrs.delete(k); }, getAttribute: (k) => attrs.get(k) ?? null,
    classList: {add: (c) => classes.add(c), remove: (c) => classes.delete(c), toggle() {}, contains: (c) => classes.has(c)},
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }, focus() {},
    get busy() { return classes.has('busy'); }, get ariaBusy() { return attrs.get('aria-busy') ?? null; }};
}

function setup() {
  const nodes = new Map(), requests = [], timers = [], held = [];
  const node = (key) => { if (!nodes.has(key)) nodes.set(key, button()); return nodes.get(key); };
  const fixtures = {
    '/groups': [{id: 'grp_LAB303', label: 'כיתה 303', role: 'classroom', sort: 1, machines: 0}],
    '/machines': [],
  };
  const ctx = vm.createContext({console, URLSearchParams, URL, Date, Set, Map, WeakMap, Number, Math, JSON, Promise, String, Array, Object,
    encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelector: node, querySelectorAll: () => [], getElementById: (id) => node('#' + id),
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}}, localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {},
    setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout(id) { if (timers[id - 1]) timers[id - 1].cleared = true; },
    fetch: async (url, options = {}) => {
      const key = url.replace('/api/console', '').split('?')[0], method = options.method || 'GET';
      requests.push({key, method, body: options.body ? JSON.parse(options.body) : null});
      if (method !== 'GET') await new Promise((resolve) => held.push(resolve));   // השרת עוד לא ענה
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => (key in fixtures ? fixtures[key] : {ok: true, saved: 1, rejected: []})};
    }});
  for (const f of ['progress.js', ...consoleFiles(), 'email_alerts.js', 'vmupdate.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",capabilities:{}}; current="machines"; GROUPS=' + JSON.stringify(fixtures['/groups']) + '; MACHINES=[]; loadMachines=async()=>{}');
  const release = async () => { held.splice(0).forEach((r) => r()); for (let i = 0; i < 8; i++) await Promise.resolve(); await new Promise((r) => setImmediate(r)); };
  const posts = (key) => requests.filter((r) => r.method === 'POST' && r.key === key).length;
  return {run, node, requests, release, posts, timers, ctx};
}
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

test('withBusy: disabled + aria-busy + the spinner class while it runs; a second entry is swallowed and returns the same promise', async () => {
  const {run, ctx} = setup();
  const b = button('שמירה'); ctx.b = b; let calls = 0, finish;
  ctx.work = () => { calls++; return new Promise((resolve) => { finish = resolve; }); };
  const first = run('withBusy(b, work)'), second = run('withBusy(b, work)');
  assert.equal(calls, 1, 'the second entry never reached fn');
  assert.equal(first, second, 'the swallowed entry gets the running promise');
  assert.equal(b.disabled, true); assert.equal(b.ariaBusy, 'true'); assert.equal(b.busy, true);
  finish('done'); assert.equal(await first, 'done');
  assert.equal(b.disabled, false); assert.equal(b.ariaBusy, null); assert.equal(b.busy, false);
  await run('withBusy(b, async () => 1)'); assert.equal(calls, 1, 'after release a new press runs again (the other fn)');
});

test('withBusy: released after a failure too (finally) — and the error still reaches the caller; a synchronous throw as well', async () => {
  const {run, ctx} = setup();
  const b = button(); ctx.b = b;
  ctx.boom = async () => { throw new Error('השרת סירב'); };
  await assert.rejects(run('withBusy(b, boom)'), /השרת סירב/);
  assert.equal(b.disabled, false); assert.equal(b.ariaBusy, null); assert.equal(b.busy, false);
  ctx.boomSync = () => { throw new Error('סינכרוני'); };
  await assert.rejects(run('withBusy(b, boomSync)'), /סינכרוני/);
  assert.equal(b.disabled, false); assert.equal(b.busy, false);
  let calls = 0; ctx.ok = async () => { calls++; };
  await run('withBusy(b, ok)'); assert.equal(calls, 1, 'not locked forever after the failure');
});

test('withBusy: a button that was disabled stays disabled afterwards; the label shows only after `after` seconds and is restored', async () => {
  const {run, ctx, timers} = setup();
  const b = button('שמור'); b.disabled = true; ctx.b = b; let finish;
  ctx.work = () => new Promise((resolve) => { finish = resolve; });
  const p = run('withBusy(b, work, {label: "עדיין שומר…", after: 4})');
  assert.equal(b.textContent, 'שמור', 'no label at first');
  const t = timers.find((x) => x.ms === 4000); assert.ok(t, 'one timer, after 4 seconds'); t.fn();
  assert.equal(b.textContent, 'עדיין שומר…');
  finish(); await p;
  assert.equal(b.textContent, 'שמור'); assert.equal(b.disabled, true, 'it was disabled before — restored to that, not enabled');
  assert.ok(timers.find((x) => x.ms === 4000).cleared, 'the timer is cleared');
});

test('withBusy: the same button inside a busy button (a window whose ok calls a guarded action) is not left disabled', async () => {
  const {run, ctx} = setup();
  const b = button(); ctx.b = b;
  await run('withBusy(b, () => withBusy(b, async () => 1))');
  assert.equal(b.disabled, false); assert.equal(b.busy, false); assert.equal(b.ariaBusy, null);
});

test('withBusy: keys are independent — two different machines do not block each other, the same one does', async () => {
  const {run, ctx} = setup(); let calls = 0; ctx.work = async () => { calls++; await tick(); };
  const a = run('withBusy(null, work, {key: "wake|a"})'), c = run('withBusy(null, work, {key: "wake|c"})'), a2 = run('withBusy(null, work, {key: "wake|a"})');
  assert.equal(calls, 2); assert.equal(a, a2); await Promise.all([a, c]);
});

test('#2840: a double click on "הוסף" of "+ מחשב" sends ONE POST /machines (the DHCP is touched once), and the button is a spinner until the server answers', async () => {
  const {run, node, posts, release} = setup();
  run('openAddMachine({group:"grp_LAB303"})');
  node('#am-name').value = '04'; node('#am-mac').value = 'de:ad:be:ef:00:01'; node('#am-group').value = 'grp_LAB303';
  const ok = node('#modalOk');
  const first = ok.onclick({}), second = ok.onclick({});
  await tick();
  assert.equal(posts('/machines'), 1, 'two clicks, one request');
  assert.equal(ok.disabled, true); assert.equal(ok.ariaBusy, 'true'); assert.equal(ok.busy, true);
  await release(); await first; await second;
  assert.equal(posts('/machines'), 1);
  assert.equal(ok.disabled, false); assert.equal(ok.busy, false);
});

test('#2840: the paste tab of "+ מחשב" — a double click sends one POST /machines/import', async () => {
  const {run, node, posts, release} = setup();
  run('openAddMachine({group:"grp_LAB303", tab:1})');
  node('#am-pgroup').value = 'grp_LAB303'; node('#am-text').value = 'b4:2e:99:07:1a:c4 04';
  const ok = node('#modalOk'); ok.onclick({}); ok.onclick({}); await tick();
  assert.equal(posts('/machines/import'), 1);
  await release();
});

test('#2840: a window that opens the next window from its ok is not blocked by the previous one (a fresh lock per opening)', async () => {
  const {run, node, ctx} = setup(); let second = 0;
  ctx.second = () => { second++; };
  run('openModalContent("א", "<p>1</p>", "הבא", () => { openModalContent("ב", "<p>2</p>", "סיום", second); })');
  const ok = node('#modalOk');
  await ok.onclick({}); await tick();
  assert.equal(ok.disabled, false, 'the new window starts enabled');
  assert.equal(ok.ariaBusy, null);
  await ok.onclick({}); assert.equal(second, 1, 'the second window\'s ok ran');
});

test('#2840: a double click on "שכפל כלל" creates ONE copy', async () => {
  const {run, posts, release} = setup();
  run('EM_VIEW={accounts:[{id:"a1"}],rules:[{id:"r1",name:"שגיאות",enabled:true,account_id:"a1",categories:[],min_severity:"error",recipients:[]}]}; renderCurrent=()=>{}');
  const a = run('emRuleDup("r1")'), b = run('emRuleDup("r1")');
  await tick();
  assert.equal(posts('/email-alerts/rules'), 1, 'two clicks, one copy');
  await release(); await a; await b;
  assert.equal(posts('/email-alerts/rules'), 1);
  const c = run('emRuleDup("r1")'); await tick();
  assert.equal(posts('/email-alerts/rules'), 2, 'a later, separate click is a new copy — the lock is released');
  await release(); await c;
});

test('#2840: a direct onclick button (window.event.currentTarget) gets the spinner, and the second click is swallowed — "הער את מחשבי השיכפול"', async () => {
  const {run, ctx, posts, release} = setup();
  const b = button('הער'); ctx.window.event = {type: 'click', currentTarget: b};
  const first = run('wakeRoom()'); ctx.window.event = {type: 'click', currentTarget: b}; const second = run('wakeRoom()');
  await tick();
  assert.equal(posts('/room/wake'), 1);
  assert.equal(b.disabled, true); assert.equal(b.busy, true); assert.equal(b.ariaBusy, 'true');
  await release(); await first; await second;
  assert.equal(b.disabled, false); assert.equal(b.busy, false);
});

test('#2840: a keyboard event is not a button — Enter in a field must not disable the field', async () => {
  const {run, ctx, release} = setup();
  const field = button('שדה'); ctx.window.event = {type: 'keydown', currentTarget: field};
  const p = run('wakeRoom()'); await tick();
  assert.equal(field.disabled, false); assert.equal(field.busy, false);
  await release(); await p;
});

test('#2840: "אמת את כל הספרייה" — a double click is ONE scrub (not two full sha256 passes)', async () => {
  const {run, posts, release} = setup();
  const a = run('scrubLibrary()'), b = run('scrubLibrary()'); await tick();
  assert.equal(posts('/images/scrub'), 1);
  await release(); await a; await b;
});

test('#2840: vmuAct — one action at a time (the server state machine answers the second with 409 after the first succeeded)', async () => {
  const {run, ctx} = setup(); let calls = 0, finish;
  ctx.act = () => { calls++; return new Promise((resolve) => { finish = resolve; }); };
  run('vmuGlobal=()=>{}; vmuPaint=()=>{}');
  const a = run('vmuAct(act)'), b = run('vmuAct(act)');
  assert.equal(calls, 1); finish({state: 'running'}); await a; await b;
  run('vmuAct(act)'); assert.equal(calls, 2, 'released after the first finished'); finish({});
});

test('#2840: every button listed in the audit is wrapped (a new unguarded copy of the pattern fails here)', () => {
  const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');
  const wrapped = {
    'console.js': ['scrubLibrary', 'startRound', 'reopenRound', 'startWave', 'reopenRoom', 'wakeRoom', 'wakeMachine', 'wakeGroup', 'clearDiskFailure', 'clearShrinkRecord', 'healthSvcStart'],
    'nicsvc.js': ['nicsvcSave', 'nicsvcKeep', 'nicsvcUndo'], 'netcfg.js': ['confirmNetRollback'], 'update_screen.js': ['updRetry'],
    'storage.js': ['storageConnect', 'storageScan'], 'learning.js': ['learningMachine'], 'vmupdate.js': ['vmuAct'],
    'email_alerts.js': ['emRuleDup', 'emSaveRelay', 'emSaveCustom', 'emSaveGmail', 'emSaveTo', 'emConnect'],
  };
  for (const [file, names] of Object.entries(wrapped)) for (const n of names)
    assert.match(read(file), new RegExp(`^${n} = guarded\\("${n}", ${n}`, 'm'), `${file}: ${n} is not wrapped in guarded()`);
  assert.match(read('console.js'), /\(event\) => withBusy\(ok, \(\) => okFn\(event\)/, 'openModalContent wraps the ok button');
});
