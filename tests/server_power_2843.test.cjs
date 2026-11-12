// ‏#2843: הפעלה מחדש וכיבוי של השרת מתפריט המשתמש (מוקאפ mockups-1112/2843-server-power, נדב 11/11).
// הפריטים: בין "פרטי session" ל"התנתקות", עם מפרידים, למנהל בלבד. החלון: כפתור אדום בלי הקלדה; כשיש עבודה —
// אזהרה כתומה, רשימת המכונות ומה כל אחת עושה, והכפתור אומר כמה ייקטעו.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../server/static');
const source = fs.readFileSync(path.join(root, 'console.js'), 'utf8');
const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

function node() {
  const classes = new Set();
  return {value: '', innerHTML: '', textContent: '', className: '', dataset: {}, disabled: false, hidden: false,
    style: {removeProperty() {}, setProperty() {}},
    classList: {add: (c) => classes.add(c), remove: (c) => classes.delete(c), toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), contains: (c) => classes.has(c)},
    addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
    setAttribute() {}, removeAttribute() {}, focus() {}, remove() {}};
}

function setup(routes) {
  const registry = new Map();
  const get = (sel) => { const k = String(sel).replace(/^#/, ''); if (!registry.has(k)) registry.set(k, node()); return registry.get(k); };
  const calls = [];
  const fetch = async (url, opts = {}) => {
    calls.push({url, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null});
    const hit = await routes(url, opts);
    const status = hit.status || 200;
    return {status, ok: status < 400, headers: {get: () => null}, json: async () => hit.body};
  };
  const toasts = [];
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelectorAll: () => [], querySelector: get, getElementById: get, activeElement: null, body: {},
      createElement: () => node(), documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false})},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    requestAnimationFrame: () => 1, fetch,
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(source, ctx);
  vm.runInContext('toast = (m) => __toasts.push(m);', Object.assign(ctx, {__toasts: toasts}));
  return {run: (s) => vm.runInContext(s, ctx), get, calls, toasts};
}

const ADMIN = 'ME = {username: "admin", role: "admin", server_name: "imagesrv-haifa"};';
const idle = (url) => (url.endsWith('/room') ? {body: {machines: [], round: null}}
  : url.endsWith('/overview') ? {body: {session: null, pulls: []}} : url.endsWith('/tasks') ? {body: []} : {body: {ok: true}});
const busyRoom = (url) => {
  if (url.endsWith('/room')) return {body: {round: {state: 'active'}, machines: [
    {mac: 'aa', name: 'CLN-118', joined: true, drawer_list: [
      {state: 'writing', bytes_written: 64, bytes_total: 100, drawer: 1},
      {state: 'writing', bytes_written: 61, bytes_total: 100, drawer: 2}]},
    {mac: 'bb', name: 'CLN-59', joined: true, drawer_list: [{state: 'done', bytes_written: 100, bytes_total: 100}]},
    {mac: 'cc', name: 'IDLE-1', joined: false, drawer_list: []}]}};
  if (url.endsWith('/tasks')) return {body: [{state: 'running', machine: 'HP-BUILD-2', name: 'win11', bytes_written: 38, bytes_total: 100}]};
  return idle(url);
};

test('the menu: separator, reboot, red poweroff, separator, logout -- after "פרטי session", admin-only and hidden by default', () => {
  const start = index.indexOf('<div class="user-menu" id="userMenu">');
  const end = index.indexOf('התנתקות</button></div>', start);
  assert.ok(start > 0 && end > start, 'the user menu is in index.html');
  const html = index.slice(start, end);
  const at = (re) => { const m = html.match(re); assert.ok(m, String(re)); return html.indexOf(m[0]); };
  const session = at(/openSessionInfo\(\)">פרטי session<\/button>/);
  const reboot = at(/<button class="hidden" data-admin type="button" onclick="openServerPower\('reboot'\)">הפעלה מחדש של השרת…<\/button>/);
  const off = at(/<button class="danger-item hidden" data-admin type="button" onclick="openServerPower\('poweroff'\)">כיבוי השרת…<\/button>/);
  const logout = at(/<button id="logout"/);
  const seps = [...html.matchAll(/<div class="user-menu-sep[^"]*"[^>]*role="separator"><\/div>/g)].map((m) => m.index);
  assert.equal(seps.length, 2);
  assert.ok(session < seps[0] && seps[0] < reboot && reboot < off && off < seps[1] && seps[1] < logout, 'order as in the mockup');
  assert.match(html, /<div class="user-menu-sep hidden" data-admin role="separator">/, 'the first separator goes with the admin items');
});

test('the items are admin-only: showApp() hides every [data-admin] for a non-admin', () => {
  // the toggle the items rely on -- one rule for all of [data-admin]
  assert.match(source, /document\.querySelectorAll\("\[data-admin\]"\)\.forEach\(\s*\(el\) => el\.classList\.toggle\("hidden", ME\.role !== "admin"\)\)/);
});

test('a deploy user opening it directly gets nothing: no window, no request', async () => {
  const t = setup(idle);
  t.run('ME = {username: "labtech", role: "deploy"};');
  await t.run('openServerPower("poweroff")');
  assert.equal(t.calls.length, 0);
  assert.equal(t.get('sheet').innerHTML, '');
});

test('nothing running: red button "כיבוי השרת", what happens, no orange warning; the click posts once', async () => {
  const t = setup(idle);
  t.run(ADMIN);
  await t.run('openServerPower("poweroff")');
  const html = t.get('sheet').innerHTML;
  assert.match(html, /<h2 id="sheetTitle">לכבות את השרת\?<\/h2><p>imagesrv-haifa · הקונסולה תתנתק, וכדי להדליק צריך גישה פיזית או WoL\.<\/p>/);
  assert.match(html, /class="btn danger" type="submit" id="sf-submit">כיבוי השרת<\/button>/);
  assert.doesNotMatch(html, /nw-alert/);
  assert.match(html, /הפעולה נרשמת ביומן על שם admin/);
  assert.doesNotMatch(html, /<input class="n-in"/, 'no typed confirmation (#1270)');
  await t.run('$("#sheet").onsubmit({preventDefault() {}})');
  const posts = t.calls.filter((c) => c.method === 'POST');
  assert.deepEqual(posts.map((c) => [c.url, c.body]), [['/api/console/server-power', {action: 'poweroff', force: false}]]);
  assert.equal(t.toasts.length, 1);
});

test('reboot uses the same window without the word "כיבוי"', async () => {
  const t = setup(idle);
  t.run(ADMIN);
  await t.run('openServerPower("reboot")');
  const html = t.get('sheet').innerHTML;
  assert.match(html, /להפעיל מחדש את השרת\?/);
  assert.match(html, /id="sf-submit">הפעלה מחדש של השרת<\/button>/);
  assert.doesNotMatch(html, /כיבוי/);
});

test('work running: orange warning, each machine and what it does, and the button counts the machines', async () => {
  const t = setup(busyRoom);
  t.run(ADMIN);
  await t.run('openServerPower("poweroff")');
  const html = t.get('sheet').innerHTML;
  assert.match(html, /<div class="nw-alert spw-alert" role="alert">/);
  assert.match(html, /הכיבוי יקטע אותן\. הדיסקים שלהן יישארו לא שלמים\./, 'mockup wording');
  assert.match(html, /סבב הפצה פעיל: 2 מכונות באמצע עבודה/);
  assert.match(html, /<ul class="spw-impact"><li><span aria-hidden="true">•<\/span><span>כל המכונות במעבדה מאבדות את השרת/, 'plain bullets and the mockup wording');
  assert.doesNotMatch(html, /n-impact|ברשת ההפצה|שבכתיבה/);
  assert.match(html, /<li><b>CLN-118<\/b><span>שיכפול · דיסק 1: 64% · דיסק 2: 61%<\/span><\/li>/);
  assert.match(html, /<li><b>HP-BUILD-2<\/b><span>קליטה · /);
  assert.doesNotMatch(html, /CLN-59|IDLE-1/, 'done and idle machines are not "in the middle of work"');
  assert.match(html, /id="sf-submit">כיבוי השרת וקטיעת 2 מכונות<\/button>/);
  await t.run('$("#sheet").onsubmit({preventDefault() {}})');
  const post = t.calls.find((c) => c.method === 'POST');
  assert.deepEqual(post.body, {action: 'poweroff', force: true}, 'the admin saw the list -- the red button is the decision');
});

test('the server finds work the list did not show: 409 power_busy keeps the window, says why, the next click forces', async () => {
  let n = 0;
  const t = setup((url, opts) => (opts.method === 'POST'
    ? (n++ ? {body: {ok: true}} : {status: 409, body: {detail: 'העברה בין אתרים רצה — הכיבוי יקטע את העבודה', code: 'power_busy', busy: 'x'}})
    : idle(url)));
  t.run(ADMIN);
  await t.run('openServerPower("poweroff")');
  await t.run('$("#sheet").onsubmit({preventDefault() {}})');
  assert.match(t.get('sf-error').textContent, /העברה בין אתרים רצה.*לחיצה נוספת תקטע אותה/);
  assert.equal(t.get('sf-submit').textContent, 'כיבוי השרת וקטיעת העבודה');
  assert.equal(t.toasts.length, 0);
  await t.run('$("#sheet").onsubmit({preventDefault() {}})');
  const posts = t.calls.filter((c) => c.method === 'POST').map((c) => c.body);
  assert.deepEqual(posts, [{action: 'poweroff', force: false}, {action: 'poweroff', force: true}]);
});

test('busy feedback while the request runs (the #2840 busy of sheet)', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const t = setup((url, opts) => (opts.method === 'POST' ? gate.then(() => ({body: {ok: true}})) : idle(url)));
  t.run(ADMIN);
  await t.run('openServerPower("reboot")');
  const pending = t.run('$("#sheet").onsubmit({preventDefault() {}})');
  await new Promise((r) => setImmediate(r));
  assert.equal(t.get('sf-submit').disabled, true);
  assert.equal(t.get('sf-busy').textContent, 'מתזמן את ההפעלה מחדש…');
  assert.equal(t.get('sf-busy').hidden, false);
  release();
  await pending;
  assert.equal(t.get('sf-submit').disabled, false);
  assert.equal(t.toasts.length, 1);
});

test('capture only (no round): the orange title says "קליטה פעילה", not "סבב הפצה פעיל"', async () => {
  const t = setup((url) => (url.endsWith('/tasks')
    ? {body: [{state: 'running', machine: 'HP-BUILD-2', name: 'win11', bytes_written: 38, bytes_total: 100}]} : idle(url)));
  t.run(ADMIN);
  await t.run('openServerPower("poweroff")');
  const html = t.get('sheet').innerHTML;
  assert.match(html, /קליטה פעילה: מכונה אחת באמצע עבודה/);
  assert.doesNotMatch(html, /סבב הפצה פעיל/);
});

test('the window drops the line above the buttons (mockup) -- and keeps the shared X of sheet()', async () => {
  const t = setup(idle);
  t.run(ADMIN);
  await t.run('openServerPower("reboot")');
  assert.match(t.get('sheet').innerHTML, /id="sf-x"/, 'sheet() always draws the X; not specific to this window');
  assert.match(fs.readFileSync(path.join(root, 'console.css'), 'utf8'), /\.win\.spw-sheet \.n-f-bar\{border-top:0;flex-direction:row-reverse;padding-bottom:20px\}/);
});
