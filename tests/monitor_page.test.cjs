// #822 — דף "מוניטור" בקונסולה החדשה אינו placeholder: הוא מציג את
// מכונות ה-build/cloner עם שם, IP, מצב "מחובר" כפי שהשרת קבע, וכפתור
// שקורא ל-monitorMachine (הקיים). ‏#954 גל 6: הרשימה היא טבלה (monitor.md) —
// עמוד own בלי לשוניות, מתג #827 בכותרת, "נראה" מ-/net, "מה על המסך" מ-prompt,
// WoL למכונה לא-מחוברת (#984). הבדיקה מריצה את console.js המשוגר ב-vm
// עם fetch מזויף — החזרת render:pagePlaceholder מפילה אותה התנהגותית.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

function setup(fixtures) {
  const opened = [];
  const toasts = [];
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: stubNode, getElementById: stubNode,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open: (...a) => opened.push(a)},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url) => {
      const key = url.replace('/api/console', '').split('?')[0];
      if (fixtures[key] instanceof Error) return {status: 500, ok: false, headers: {get: () => null}, json: async () => ({detail: fixtures[key].message})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key] || {}};
    },
  });
  for (const f of ['progress.js', 'console.js', 'net.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);   // net.js: ago()
  const run = s => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",idle_seconds:300,capabilities:{}}; toast=(m)=>__toasts.push(m); current="monitor";');
  ctx.__toasts = toasts;
  return {ctx, run, opened, toasts};
}

/* #827: המתג להדלקה/כיבוי דורש להריץ בפועל את sheet() (מודאל האישור
   האמיתי) — כדי לבדוק "פתיחת החלון לבדה אינה
   שולחת fetch" צריך DOM שמחזיק זהות עקבית לאלמנט בין הרינדור
   לבין ה-submit, ולא צומת חד-פעמי כמו ב-stubNode של setup() הרגיל. */
function setupWithDom(fixtures, putResponses = {}) {
  const opened = [];
  const toasts = [];
  const calls = [];
  const registry = new Map();
  const node = (sel) => {
    if (!registry.has(sel)) registry.set(sel, stubNode());
    return registry.get(sel);
  };
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
      // ‏#1227: כברירת מחדל תשובת PUT מדמה הצלחה תואמת (enabled: true) —
      // טסטים שצריכים לבדוק אי-התאמה/היעדר enabled מזריקים putResponses[key].
      if (options.method && options.method !== 'GET') {
        const body = Object.prototype.hasOwnProperty.call(putResponses, key) ? putResponses[key] : {enabled: true};
        return {status: 200, ok: true, headers: {get: () => null}, json: async () => body};
      }
      if (fixtures[key] instanceof Error) return {status: 500, ok: false, headers: {get: () => null}, json: async () => ({detail: fixtures[key].message})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key] || {}};
    },
  });
  for (const f of ['progress.js', 'console.js', 'net.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",idle_seconds:300,capabilities:{}}; toast=(m)=>__toasts.push(m); current="monitor";');
  ctx.__toasts = toasts;
  return {ctx, run, opened, toasts, calls, node};
}

const base = {
  '/groups': [{id: 'grp_BUILD', role: 'build', label: 'בנייה', machines: 1}, {id: 'grp_CLONERS', role: 'cloner', label: 'שיכפול', machines: 1}],
  '/machines': [{mac: 'aa:bb:cc:dd:ee:01', suffix: 'Builder', group_id: 'grp_BUILD'}, {mac: 'aa:bb:cc:dd:ee:02', suffix: 'Cloner-1', group_id: 'grp_CLONERS'}],
  '/monitor/settings': {port: 5900, enabled: true},
  '/monitor/machines': [
    {mac: 'aa:bb:cc:dd:ee:01', name: 'Builder', role: 'build', ip: '10.44.12.50', online: true, prompt: 'signin'},
    {mac: 'aa:bb:cc:dd:ee:02', name: 'Cloner-1', role: 'cloner', ip: null, online: false},
  ],
  '/net': [{mac: 'aa:bb:cc:dd:ee:02', ip: '10.44.12.60', last_seen: new Date(Date.now() - 3 * 3600 * 1000).toISOString()}],
};

test('monitor page is wired to a real renderer, not the placeholder; own page without tabs (#954 גל 6)', () => {
  const {run} = setup(base);
  assert.equal(run('pages.monitor.render === pagePlaceholder'), false, 'pages.monitor.render must not be pagePlaceholder');
  assert.equal(run('typeof pages.monitor.load'), 'function', 'the page needs a loader');
  assert.equal(run('pages.monitor.own'), true); assert.equal(run('pages.monitor.tabs.length'), 0, 'no tabs — one question');
});

test('#1300 (console-full.html P.monitor): connected machines are buttons (name + IP) that pick the viewer; the rest stay listed with last-seen and WoL', async () => {
  const {run} = setup(base);
  assert.equal(run('monitorPage()'), run('pagePlaceholder()'), 'before load: placeholder');
  await run('loadMonitor()');
  const html = run('monitorPage()');
  assert.notEqual(html, run('pagePlaceholder()'));
  assert.doesNotMatch(html, /<table/, 'no table — a list of buttons like the mockup');
  const pick = html.slice(html.indexOf('<button type="button" class="btn mon-pick'), html.indexOf('</button>', html.indexOf('mon-pick')));
  assert.match(pick, /data-mac="aa:bb:cc:dd:ee:01" onclick="monitorSelect\('aa%3Abb%3Acc%3Add%3Aee%3A01'\)" aria-pressed="false"><span class="mn"><b>Builder<\/b>/);
  assert.match(pick, /<span class="mono">10\.44\.12\.50<\/span>/);
  // המצב הוא מה שהשרת אמר — לא נגזר בדפדפן; "מה על המסך" מ-prompt
  assert.match(pick, /class="st warn">ממתין למפעיל: כניסה</, '"what is on the screen" from prompt');
  assert.doesNotMatch(pick, /wakeMachine/, 'online → no WoL');
  const off = html.slice(html.indexOf('<div class="mon-rest">'));
  assert.match(off, /<div class="grp">לא מחוברים · 1<\/div>/);
  assert.match(off, /Cloner-1[^]*class="st ">לא מחובר · נראה לפני 3 שע&#39;</, 'last seen from /net, not from /monitor/machines');
  assert.doesNotMatch(off, /monitorSelect\(|monitorMachine\(/, 'not online → nothing to connect to');
  assert.match(off, /wakeMachine\('aa%3Abb%3Acc%3Add%3Aee%3A02'\)">WoL</, '#984: WoL for a machine that is not connected');
  assert.match(html, /<div class="obj-sub"[^>]*>צפייה בתחנות מרחוק · 1 מתוך 2 מחוברות<\/div>/);
  assert.doesNotMatch(html, /המתג כבוי|המתג דלוק|class="note warn"/, '#1300: no state pill and no note — the switch itself');
  assert.doesNotMatch(html, /role="tablist"|detail-grid|class="switch/, 'no tabs, no per-machine cards, no old switch');
});
test('a machine that never talked and /net that could not be read are two different states', async () => {
  let s = setup({...base, '/net': []});
  await s.run('loadMonitor()');
  assert.match(s.run('monitorPage()'), /class="st ">מעולם לא נראה</);
  s = setup({...base, '/net': new Error('net down')});
  await s.run('loadMonitor()');
  assert.match(s.run('monitorPage()'), /לא מחובר · נראה: לא נקרא/);
});

test('monitor button opens monitor.html for the machine through monitorMachine', async () => {
  const {run, opened} = setup(base);
  await run('loadMonitor()');
  run("monitorMachine('aa%3Abb%3Acc%3Add%3Aee%3A01')");
  assert.equal(opened.length, 1);
  assert.match(opened[0][0], /^monitor\.html\?mac=aa%3Abb%3Acc%3Add%3Aee%3A01&name=Builder$/);
});

test('switch off shows the warning; empty list is an empty state, not an error', async () => {
  const {run} = setup({...base, '/monitor/settings': {port: 5900, enabled: false}, '/monitor/machines': []});
  await run('loadMonitor()');
  const html = run('monitorPage()');
  // ‏#1300: ההסבר עבר לכרטיס הצפייה ול-title של המתג (בלי הערה בראש הדף ובלי פיל)
  assert.match(html, /<b class="et">המוניטור כבוי<\/b>[^<]*הדלקה חושפת 5900 על וילן ההפצה/);
  assert.match(html, /title="כבוי כברירת מחדל — הדלקה חושפת 5900/);
  assert.match(html, /class="muted">אין מחשבי בנייה או שיכפול רשומים/);
  assert.doesNotMatch(html, /note err/);
});

test('read failure is a distinct error state, not an empty list (principle 5)', async () => {
  const {run, toasts} = setup({...base, '/monitor/machines': new Error('DB down')});
  await run('loadMonitor()');
  const html = run('monitorPage()');
  assert.match(html, /note err/);
  assert.match(html, /DB down/); assert.match(html, /class="pill err">לא נקרא</);
  assert.doesNotMatch(html, /class="empty"|<table/);
  assert.equal(toasts.length, 1);
});

// ‏#1300 (הכרעת נדב 12/10 — מחליפה את "אין מוניטור במגירה" של 14/09 ו-17/09): "פתח במוניטור" במגירת המחשב
// פותח את המציג של אותה מכונה — monitor.html, בדיוק כמו "פתח בחלון נפרד" בדף המוניטור (monitorMachine) — לא מנווט לדף.
test('#1300: the machine drawer "פתח במוניטור" opens that machine\'s viewer (monitor.html) — disabled with the reason when there is nothing to connect to', async () => {
  const {run, ctx, opened} = setup(base);
  await run('loadMonitor()');
  run('DISK_FAILURES=[]; SHRINK_RECORDS=[]');   // the stub fetch answers {} for endpoints this file does not model
  let drawer = '';
  ctx.openDrawer = (_title, body) => { drawer = body; };
  run("openMachineDetail('aa%3Abb%3Acc%3Add%3Aee%3A01')");   // Builder: online, switch on
  assert.match(drawer, /<button class="btn primary" onclick="monitorMachine\('aa%3Abb%3Acc%3Add%3Aee%3A01'\)">פתח במוניטור<\/button>/);
  assert.doesNotMatch(drawer, /openMachineMonitor|selectPageById\('monitor'\)/, 'it does not navigate to the monitor page');
  run("monitorMachine('aa%3Abb%3Acc%3Add%3Aee%3A01')");
  assert.match(opened.at(-1)[0], /^monitor\.html\?mac=aa%3Abb%3Acc%3Add%3Aee%3A01&name=Builder$/, 'the same viewer the monitor page opens');
  run("openMachineDetail('aa%3Abb%3Acc%3Add%3Aee%3A02')");   // Cloner-1: not connected
  assert.match(drawer, /<button class="btn primary" disabled title="המחשב לא מחובר — אין למה להתחבר">פתח במוניטור<\/button>/);
  assert.match(drawer, /<div class="cap mon-why">מוניטור: המחשב לא מחובר — אין למה להתחבר<\/div>/);
  run('MONITOR.settings.enabled=false'); run("openMachineDetail('aa%3Abb%3Acc%3Add%3Aee%3A01')");
  assert.match(drawer, /disabled title="המוניטור כבוי — מדליקים אותו בדף המוניטור">פתח במוניטור</);
  run('MONITOR=null; MON_SETTINGS=null; api=()=>new Promise(()=>{})'); run("openMachineDetail('aa%3Abb%3Acc%3Add%3Aee%3A01')");
  assert.match(drawer, /disabled title="מצב המוניטור לא נקרא עדיין">/, 'unread is not "enabled" (principle 5)');
});

test('final mockup: the viewer card explains instead of an empty iframe (switch off / nothing selected / machine offline); a selected online machine gets monitor.html in an iframe plus a separate-window link', async () => {
  const {run} = setup(base);
  await run('loadMonitor()');
  run('MONITOR.settings.enabled=false; MON_SEL=""');
  let html = run('monitorPage()');
  assert.match(html, /המוניטור כבוי/); assert.doesNotMatch(html, /<iframe/);
  run('MONITOR.settings.enabled=true');
  html = run('monitorPage()'); assert.match(html, /בחר מחשב לצפייה/); assert.doesNotMatch(html, /<iframe/);
  const online = run('MONITOR.machines.find((m) => m.online)'), off = run('MONITOR.machines.find((m) => !m.online)');
  if (off) { run(`MON_SEL=${JSON.stringify(off.mac)}`); html = run('monitorPage()'); assert.match(html, /אינה מחוברת/); assert.doesNotMatch(html, /<iframe/); }
  assert.ok(online, 'fixture has an online machine');
  run(`MON_SEL=${JSON.stringify(online.mac)}`); html = run('monitorPage()');
  assert.match(html, /<iframe class="mon-frame"[^>]*src="monitor\.html\?mac=/); assert.match(html, /פתח בחלון נפרד/);
});

// --- #827: מתג "מוניטור לתחנות" בראש דף המוניטור --------------------------

test('the switch renders at the top of the page, reflecting server state', async () => {
  const {run} = setup(base); // enabled: true
  await run('loadMonitor()');
  const htmlOn = run('monitorPage()');
  const toggleIdx = htmlOn.indexOf('מוניטור לתחנות');
  const listIdx = htmlOn.indexOf('Builder');
  assert.ok(toggleIdx >= 0 && listIdx >= 0 && toggleIdx < listIdx, 'the switch must render before the machine list');
  assert.match(htmlOn, /class="sw on" role="switch" aria-checked="true" aria-label="מוניטור לתחנות" onclick="monitorToggle\(false\)"/);

  const {run: run2} = setup({...base, '/monitor/settings': {port: 5900, enabled: false}});
  await run2('loadMonitor()');
  const htmlOff = run2('monitorPage()');
  assert.match(htmlOff, /class="sw" role="switch" aria-checked="false"[^>]*onclick="monitorToggle\(true\)"/);
  assert.match(htmlOff, /<span class="swrow" title="כבוי כברירת מחדל — הדלקה חושפת 5900 על וילן ההפצה[^"]*">/);   // #1077 → ‏#1300: ההסבר ב-title, המתג לבד
});

test('turning off sends PUT with no confirm word and no sheet() prompt', async () => {
  const {run, calls} = setupWithDom(base); // enabled: true
  await run('loadMonitor()');
  await run('monitorToggle(false)');
  const puts = calls.filter(c => c.method === 'PUT');
  assert.equal(puts.length, 1);
  assert.deepEqual(puts[0].body, {enabled: false});
  assert.equal(puts[0].url, '/api/console/monitor/settings');
});

test('turning on is a confirm sheet with no typed field (#1270) — opening it sends no PUT', async () => {
  const {run, calls, node} = setupWithDom({...base, '/monitor/settings': {port: 5900, enabled: false}});
  await run('loadMonitor()');
  run('monitorToggle(true)'); // opens the sheet() — does not itself call fetch
  assert.equal(calls.filter(c => c.method === 'PUT').length, 0, 'opening the sheet must not send a PUT by itself');

  assert.doesNotMatch(node('#sheet').innerHTML, /sf-verify/);
  assert.match(node('#sheet').innerHTML, /id="sf-cancel"/); assert.match(node('#sheet').innerHTML, /class="btn danger"/);
});

test('turning on, confirmed in the sheet, sends PUT with the confirm word from code, then reloads', async () => {
  const {run, calls, node} = setupWithDom({...base, '/monitor/settings': {port: 5900, enabled: false}});
  await run('loadMonitor()');
  run('monitorToggle(true)');
  const form = node('#sheet');
  await form.onsubmit({preventDefault() {}});
  const puts = calls.filter(c => c.method === 'PUT');
  assert.equal(puts.length, 1);
  assert.deepEqual(puts[0].body, {enabled: true, confirm: 'imagesrv.monitor'});
  // אחרי ה-PUT נטען המצב מחדש מהשרת
  assert.ok(calls.some(c => c.method === 'GET' && c.url === '/api/console/monitor/settings' && calls.indexOf(c) > calls.indexOf(puts[0])));
});

// --- #1227: ההודעה נגזרת מה-enabled שחזר, לא ממה שביקשנו ------------------

// monitorToggle(false) לא ממתין ל-send() שלו (fire-and-forget, כמו הטסט
// הקיים למעלה שבודק רק calls) — toast() מגיע אחרי כמה תורי-מיקרו על שרשרת
// fetch→json→api→put. ‏setImmediate מרוקן את כולם לפני שהוא רץ.
const tick = () => new Promise((r) => setImmediate(r));

test('turning off: server confirms enabled:false — success toast, not an error (#1227)', async () => {
  const {run, toasts} = setupWithDom(base, {'/monitor/settings': {enabled: false}}); // base: enabled true before the toggle
  await run('loadMonitor()');
  run('monitorToggle(false)');
  await tick();
  assert.equal(toasts.length, 1);
  assert.match(toasts[0], /^המוניטור כובה/);
});

test('turning off: server-returned enabled disagrees (still true) — error toast, not "כובה" (#1227)', async () => {
  const {run, toasts} = setupWithDom(base, {'/monitor/settings': {enabled: true}});
  await run('loadMonitor()');
  run('monitorToggle(false)');
  await tick();
  assert.equal(toasts.length, 1);
  assert.match(toasts[0], /המוניטור לא כובה: השרת מחזיר דלוק/);
  assert.doesNotMatch(toasts[0], /^המוניטור כובה/);
});

test('turning on: server-returned enabled disagrees (still false) — error toast, not "הודלק" (#1227)', async () => {
  const {run, calls, node, toasts} = setupWithDom(
    {...base, '/monitor/settings': {port: 5900, enabled: false}},
    {'/monitor/settings': {enabled: false}});
  await run('loadMonitor()');
  run('monitorToggle(true)');
  const form = node('#sheet');
  await form.onsubmit({preventDefault() {}});
  assert.equal(calls.filter(c => c.method === 'PUT').length, 1);
  assert.equal(toasts.length, 1);
  assert.match(toasts[0], /המוניטור לא הודלק: השרת מחזיר כבוי/);
  assert.doesNotMatch(toasts[0], /^המוניטור הודלק/);
});

test('PUT response without an "enabled" field is "לא אומת", not a silent success (principle 5, #1227)', async () => {
  const {run, toasts} = setupWithDom(base, {'/monitor/settings': {}});
  await run('loadMonitor()');
  run('monitorToggle(false)');
  await tick();
  assert.equal(toasts.length, 1);
  assert.match(toasts[0], /לא אומת/);
  assert.doesNotMatch(toasts[0], /הודלק|כובה/);
});
