// ‏#1301: מסך העדכון — "השרת מתעדכן" (המוקאפ שאושר 13/10, design-approved/update/update.html).
// בלי דפדפן: console.js + update_screen.js ב-vm, fetch מזויף לפי נתיב, ושעון ביד הבדיקה.
// מה נבדק: כל מצב במוקאפ (שלבים, עולה מחדש, חזר, תקרת זמן, חתימה נדחתה, הורדה נכשלה, הוחזר),
// שהדף נטען מחדש רק על ראיה חיובית (stage done או /health/live עם התג בלי session),
// ושאחרי 10 דקות בלי התקדמות הדף מפסיק לחכות — לא "ממתין" לנצח (עיקרון 5).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const T0 = Date.parse('2026-10-13T11:02:04Z');
const iso = (ms) => new Date(ms).toISOString().replace('.000Z', '+00:00');
const at = (s) => iso(T0 + s * 1000);

function setup({search = '', fixtures = {}} = {}) {
  const nodes = new Map(), requests = [], navigated = [], replaced = [], toasts = [];
  function node(key) {
    if (!nodes.has(key)) {
      const cls = new Set(), attrs = {};
      nodes.set(key, {value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}}, inert: false,
        classList: {add: (c) => cls.add(c), remove: (c) => cls.delete(c), toggle() {}, contains: (c) => cls.has(c)},
        addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
        insertAdjacentHTML(_p, html) { this.innerHTML += html; }, setAttribute: (k, v) => { attrs[k] = String(v); },
        removeAttribute: (k) => { delete attrs[k]; }, getAttribute: (k) => attrs[k] ?? null, hasAttribute: (k) => k in attrs,
        closest() { return {style: {}}; }, focus() { this.focused = true; }, attrs});
    }
    return nodes.get(key);
  }
  const clock = {now: T0};
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, String, Array, Object, RegExp,
    encodeURIComponent, decodeURIComponent, Error,
    document: {hidden: false, body: node('body'), querySelector: node, querySelectorAll: () => [], getElementById: (id) => node('#' + id),
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); }, get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    location: {pathname: '/', search, hash: '', origin: 'https://10.10.10.10:8081', hostname: '10.10.10.10',
      href: 'https://10.10.10.10:8081/' + search, replace: (u) => navigated.push(u)},
    history: {replaceState: (_s, _t, u) => replaced.push(u)},
    navigator: {clipboard: {writeText: async (t) => { ctx.copied = t; }}},
    CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options = {}) => {
      const key = url.replace('/api/console', '').split('?')[0];
      requests.push({url: key, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
      let f = fixtures[key];
      if (typeof f === 'function') f = f();
      if (f === 'down') throw new TypeError('Failed to fetch');
      if (f && f.code) return {status: f.code, ok: false, headers: {get: () => null}, json: async () => ({detail: f.detail || 'x'})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => f ?? {ok: true}};
    },
  });
  for (const f of ['progress.js', 'console.js', 'update_screen.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  run('ME={timezone:"UTC",username:"nadav",role:"admin",server_name:"srv",version:"v0.71.0",capabilities:{}}; globalThis.sheets=[]; globalThis.toasts=[];'
    + ' sheet=o=>{sheets.push(o);}; toast=(m,ms,k)=>{toasts.push([m,k||""]);}; renderCurrent=()=>{};');
  ctx.__clock = clock;
  run('updClock = () => __clock.now;');
  const html = () => node('#upd').innerHTML;
  const poll = () => run('updPoll()');
  return {run, ctx, clock, requests, navigated, replaced, fixtures, html, poll, node};
}
const running = (stage, extra = {}) => ({state: 'running', tag: 'v0.72.0', from: 'v0.71.0', by: 'nadav', started_at: at(0), now: at(9),
  stage, stages: Object.fromEntries(['fetch', 'verify', 'install', 'restart', 'done'].slice(0, ['fetch', 'verify', 'install', 'restart', 'done'].indexOf(stage) + 1)
    .map((k, i) => [k, at([1, 19, 21, 128, 184][i])])), current: 'v0.71.0', verified: false, ...extra});
const failed = (reason, stage, extra = {}) => ({...running(stage), state: 'failed', reason, detail: 'סיבה', error: 'סיבה · פירוט', verified: undefined, ...extra});
function steps(html) {
  return [...html.matchAll(/<li class="(\w+)"/g)].map((m) => m[1]).slice(0, 4);
}
async function started(over = {}, stage = 'fetch') {
  const t = setup(over);
  t.fixtures['/update/status'] = running(stage);
  t.run('updStart({tag:"v0.72.0", from:"v0.71.0", by:"nadav"})');
  await t.poll();
  return t;
}

test('run: the four steps of server-upgrade.sh, the current one live; subtitle and clock come from the server', async () => {
  const t = await started();
  let h = t.html();
  assert.match(h, /<h1 id="updT" tabindex="-1">השרת מתעדכן ל-<span class="mono">v0\.72\.0<\/span><\/h1>/);
  assert.match(h, /מ-<span class="mono">v0\.71\.0<\/span> · התחיל ב-11:02 על ידי nadav · אל תכבו את השרת/);
  assert.deepEqual(steps(h), ['cur', 'pend', 'pend', 'pend']);
  assert.match(h, /<li class="cur" aria-current="step">/);
  assert.match(h, /<span class="sr-only"> — עכשיו<\/span>/, 'each step has a screen-reader status');
  assert.match(h, /<b id="updEl">0:09<\/b><small>זמן שעבר<\/small>/, 'elapsed = server now - started_at, not the browser clock');
  assert.match(h, /<b>השרת עונה<\/b> · השלב נקרא מהשרת לפני <span class="num">0<\/span> ש׳/);
  assert.match(h, /אפשר לסגור את הדף — העדכון ממשיך בשרת/);
  t.fixtures['/update/status'] = running('verify', {now: at(20)});
  await t.poll(); h = t.html();
  assert.deepEqual(steps(h), ['done', 'cur', 'pend', 'pend']);
  assert.match(h, /<span class="sdur">0:18<\/span>/, 'a done step shows its duration from the stage stamps');
  t.fixtures['/update/status'] = running('install', {now: at(74)});
  await t.poll();
  assert.deepEqual(steps(t.html()), ['done', 'done', 'cur', 'pend']);
  assert.deepEqual([...new Set(t.requests.map((r) => r.url))], ['/update/status'], 'while the server answers: status only');
});

test('restart: the server not answering is "expected" — and the page switches to the unauthenticated /health/live', async () => {
  const t = await started({}, 'install');
  t.fixtures['/update/status'] = 'down'; t.fixtures['/health/live'] = 'down';
  t.clock.now += 3000; await t.poll();
  const h = t.html();
  assert.deepEqual(steps(h), ['done', 'done', 'done', 'cur'], 'off after install = the restart, even if the poll missed stage=restart');
  assert.match(h, /class="u-led w pulse"><\/span><span><b>השרת לא עונה — צפוי בזמן ההפעלה מחדש\.<\/b> ניסיון <span class="num">1<\/span>/);
  t.requests.length = 0; t.clock.now += 3000; await t.poll();
  assert.deepEqual(t.requests.map((r) => r.url), ['/health/live']);
  assert.match(t.html(), /ניסיון <span class="num">2<\/span>/);
});

test('a restart the poll missed is timed from the moment the server stopped answering', async () => {
  const t = setup();
  t.fixtures['/update/status'] = running('install', {now: at(74)});
  t.run('updStart({tag:"v0.72.0", from:"v0.71.0", by:"nadav"})');
  await t.poll();
  t.fixtures['/update/status'] = 'down'; t.fixtures['/health/live'] = 'down';
  t.clock.now += 3000; await t.poll();
  t.clock.now += 24000;
  const h = t.html();
  assert.match(h, /<span class="st">מתקין<\/span>[^]*?<span class="sdur">0:56<\/span>/, 'install = from its stamp to the disconnect');
  assert.equal(t.run('updStepLive()'), '0:24', 'the restart runs from the disconnect');
});

test('the server going away before install is not called expected', async () => {
  const t = await started({}, 'fetch');
  t.fixtures['/update/status'] = 'down';
  await t.poll();
  assert.match(t.html(), /<b>השרת לא עונה\.<\/b>/);
  assert.doesNotMatch(t.html(), /השרת לא עונה — צפוי/);
});

test('done: only when the script says stage=done; then 5 s and a full reload with ?updated=', async () => {
  const t = await started({}, 'install');
  t.fixtures['/update/status'] = 'down'; t.fixtures['/health/live'] = 'down';
  await t.poll();
  t.clock.now += 43000;
  t.fixtures['/health/live'] = {ok: true, version: 'v0.72.0', version_error: null};
  // השרת חזר עם הגרסה, אבל הסקריפט עוד מאמת (verify-boot-payload): ‏done/verified עם stage=restart אינו "עודכן"
  t.fixtures['/update/status'] = running('restart', {state: 'done', verified: true, current: 'v0.72.0', now: at(176)});
  await t.poll();
  assert.equal(t.run('UPD.phase'), 'run');
  assert.deepEqual(steps(t.html()), ['done', 'done', 'done', 'cur']);
  assert.match(t.html(), /<b>השרת עונה<\/b>/);
  t.fixtures['/update/status'] = {...running('done', {now: at(186)}), state: 'done', verified: true, current: 'v0.72.0'};
  await t.poll();
  const h = t.html();
  assert.equal(t.run('UPD.phase'), 'done');
  assert.match(h, /עודכן ל-<span class="mono">v0\.72\.0<\/span> ✓/);
  assert.match(h, /השרת חזר וענה עם הגרסה החדשה · לקח 3:04/);
  assert.deepEqual(steps(h), ['done', 'done', 'done', 'done']);
  assert.match(h, /<b>השרת חזר<\/b> · ענה עם <span class="mono">v0\.72\.0<\/span> אחרי <span class="num">43<\/span> שניות של הפעלה מחדש/);
  assert.match(h, /נכנס לקונסולה בעוד <span class="u-cd" id="updCd">5<\/span> ש׳/);
  assert.match(h, /onclick="updReload\(\)">המשך עכשיו</);
  assert.doesNotMatch(h, /id="updEl"/, 'no running clock once done');
  for (let i = 0; i < 4; i++) t.run('updCountdown()');
  assert.deepEqual(t.navigated, []);
  t.run('updCountdown()');
  assert.deepEqual(t.navigated, ['/?updated=v0.72.0'], 'a full reload — the page holds the old version\'s JS');
});

test('done with a warning (#1334): the update is named done — but what was not installed is said, and nothing reloads by itself', async () => {
  const t = await started({}, 'restart');
  const warning = 'עדכון ב-VM לא זמין: apt-get install נכשל: E: <Unable> (קוד 3)';
  t.fixtures['/update/status'] = {...running('done', {now: at(186)}), state: 'done', verified: true, current: 'v0.72.0', warning};
  await t.poll();
  const h = t.html();
  assert.equal(t.run('UPD.phase'), 'done');
  assert.match(h, /עודכן ל-<span class="mono">v0\.72\.0<\/span> — אבל עדכון ב-VM לא זמין/);
  assert.doesNotMatch(h, /✓/, 'not a clean "updated"');
  assert.match(h, /E: &lt;Unable&gt; \(קוד 3\)/, 'the full warning, escaped');
  assert.match(h, /onclick="updReload\(\)">המשך לקונסולה</);
  assert.doesNotMatch(h, /id="updCd"/, 'no countdown');
  for (let i = 0; i < 8; i++) t.run('updCountdown()');
  assert.deepEqual(t.navigated, [], 'the warning is read, not reloaded away after 5 s');
});

test('no session: /health/live with the new version is the evidence; with the old one the page keeps waiting', async () => {
  const t = await started({}, 'install');
  t.fixtures['/update/status'] = 'down'; t.fixtures['/health/live'] = 'down';
  await t.poll();
  t.fixtures['/update/status'] = {code: 401};
  t.fixtures['/health/live'] = {ok: true, version: 'v0.71.0', version_error: null};
  await t.poll();
  assert.equal(t.run('UPD.phase'), 'run', 'old version + no session = cannot tell yet');
  t.fixtures['/health/live'] = {ok: true, version: 'v0.72.0', version_error: null};
  await t.poll();
  assert.equal(t.run('UPD.phase'), 'done');
  assert.ok(!t.requests.some((r) => r.url === '/me'), 'a 401 here never throws the console to the login screen');
});

test('timeout: 10 minutes without an answer stops the checking and names what to check — not "waiting" forever', async () => {
  const t = await started({}, 'install');
  t.fixtures['/update/status'] = 'down'; t.fixtures['/health/live'] = 'down';
  await t.poll();
  const offAt = t.clock.now;
  t.clock.now = offAt + 600000; t.run('updTick()');
  assert.equal(t.run('UPD.phase'), 'run', 'exactly 10:00 is still waiting');
  t.clock.now = offAt + 601000; t.run('updTick()');
  const h = t.html();
  assert.equal(t.run('UPD.phase'), 'timeout');
  assert.match(h, /<h1 id="updT" tabindex="-1">השרת לא חזר תוך 10 דקות<\/h1>/);
  assert.match(h, /הקונסולה הפסיקה לחכות/);
  assert.deepEqual(steps(h), ['done', 'done', 'done', 'warn']);
  assert.match(h, /השרת התנתק ב-11:02:13 ולא ענה מאז/, 'the time is on the server clock (skew from the last status)');
  assert.match(h, /<span class="u-led e"><\/span><span><b>אין תשובה מאז 11:02:13<\/b>/);
  assert.match(h, /<small>בלי תשובה<\/small>/);
  assert.match(h, /מסך השרת עצמו[\s\S]*https:\/\/10\.10\.10\.10:8081[\s\S]*\/var\/log\/imagesrv\/server-upgrade\.log/);
  assert.match(h, /הבדיקה האוטומטית נעצרה\. אף שלב לא בוטל בשרת\./);
  assert.match(h, /onclick="updWaitMore\(\)">חכה עוד 10 דקות<[\s\S]*onclick="updCheckNow\(\)">בדוק עכשיו</);
  assert.doesNotMatch(h, /updClose/, 'the console is unusable while the server is down — no way "back" into it');
  t.requests.length = 0; await t.poll();
  assert.equal(t.requests.length, 0, 'no automatic polling after the ceiling');
  await t.run('updCheckNow()');
  assert.equal(t.run('UPD.phase'), 'timeout', '"check now" with the server still down stays on the ceiling');
  t.fixtures['/health/live'] = {ok: true, version: 'v0.72.0'};
  t.fixtures['/update/status'] = {...running('done'), state: 'done', verified: true, current: 'v0.72.0'};
  await t.run('updCheckNow()');
  assert.equal(t.run('UPD.phase'), 'done');
});

test('"wait 10 more minutes" resumes polling with a fresh ceiling', async () => {
  const t = await started({}, 'install');
  t.fixtures['/update/status'] = 'down'; t.fixtures['/health/live'] = 'down';
  await t.poll();
  t.clock.now += 601000; t.run('updTick()');
  t.run('updWaitMore()');
  assert.equal(t.run('UPD.phase'), 'run');
  t.clock.now += 599000; t.run('updTick()');
  assert.equal(t.run('UPD.phase'), 'run');
  t.clock.now += 2000; t.run('updTick()');
  assert.equal(t.run('UPD.phase'), 'timeout');
});

test('stuck while the server answers: the same ceiling, named "did not progress", with a way back to the console', async () => {
  const t = await started({}, 'install');
  t.clock.now += 601000; t.run('updTick()');
  const h = t.html();
  assert.match(h, /העדכון לא התקדם תוך 10 דקות/);
  assert.match(h, /<b>השלב לא התקדם מאז/);
  assert.match(h, /onclick="updClose\(\)">חזרה לקונסולה</);
});

test('#1463: the hint says the measured 8–10 minutes, not 2–4', async () => {
  const t = await started({}, 'install');
  const h = t.html();
  assert.match(h, /בדרך כלל 8–10 דקות, רובן בבניית קבצי האתחול של המחשבים/);
  assert.doesNotMatch(h, /2–4/);
});

test('#1463: payload (the initrd build) is a stage of its own — shown as "installing", with its own 25-minute ceiling', async () => {
  const t = await started({}, 'install');
  t.clock.now += 300000; t.run('updTick()');   // 5 minutes of install
  const stages = {...running('install').stages, payload: at(330)};
  t.fixtures['/update/status'] = running('install', {stage: 'payload', stages, now: at(330)});
  await t.poll();
  let h = t.html();
  assert.deepEqual(steps(h), ['done', 'done', 'cur', 'pend'], 'payload is drawn as the install step');
  assert.match(h, /בונה את קבצי האתחול של המחשבים — החלק הארוך/);
  const entered = t.run('UPD.changeAt');
  t.clock.now = entered + 11 * 60000; t.run('updTick()');
  assert.equal(t.run('UPD.phase'), 'run', '11 minutes into the build is not "did not progress"');
  t.clock.now = entered + 25 * 60000; t.run('updTick()');
  assert.equal(t.run('UPD.phase'), 'run', 'exactly 25:00 is still waiting');
  t.clock.now = entered + 25 * 60000 + 1000; t.run('updTick()');
  h = t.html();
  assert.equal(t.run('UPD.phase'), 'timeout', 'the ceiling still exists (principle 5)');
  assert.match(h, /העדכון לא התקדם תוך 25 דקות/);
  assert.match(h, /onclick="updWaitMore\(\)">חכה עוד 25 דקות</);
});

test('#1411: packages (Debian security updates from the release) is drawn as installing, with its own 25-minute ceiling', async () => {
  const t = await started({}, 'install');
  const stages = {...running('install').stages, packages: at(20)};
  t.fixtures['/update/status'] = running('install', {stage: 'packages', stages, now: at(20)});
  await t.poll();
  const h = t.html();
  assert.deepEqual(steps(h), ['done', 'done', 'cur', 'pend'], 'packages is the install step, not the first one');
  assert.match(h, /מתקין את עדכוני האבטחה של Debian שבגרסה/);
  const entered = t.run('UPD.changeAt');
  t.clock.now = entered + 21 * 60000; t.run('updTick()');
  assert.equal(t.run('UPD.phase'), 'run', 'apt has 20 minutes on the server; the screen waits longer');
  t.clock.now = entered + 25 * 60000 + 1000; t.run('updTick()');
  assert.equal(t.run('UPD.phase'), 'timeout');
});

test('#1463: entering payload resets the ceiling — install and payload each get their own window', async () => {
  const t = await started({}, 'install');
  t.clock.now += 9 * 60000; t.run('updTick()');
  assert.equal(t.run('UPD.phase'), 'run');
  const stages = {...running('install').stages, payload: at(560)};
  t.fixtures['/update/status'] = running('install', {stage: 'payload', stages, now: at(560)});
  await t.poll();
  t.clock.now += 2 * 60000; t.run('updTick()');   // 11 minutes since the run entered install
  assert.equal(t.run('UPD.phase'), 'run');
});

test('#1463: the server not answering during payload keeps the 10-minute ceiling — that is a restart, not the build', async () => {
  const t = await started({}, 'install');
  t.fixtures['/update/status'] = running('install', {stage: 'payload', stages: {...running('install').stages, payload: at(30)}, now: at(30)});
  await t.poll();
  t.fixtures['/update/status'] = 'down'; t.fixtures['/health/live'] = 'down';
  await t.poll();
  t.clock.now += 601000; t.run('updTick()');
  assert.equal(t.run('UPD.phase'), 'timeout');
  assert.match(t.html(), /השרת לא חזר תוך 10 דקות/);
});

test('sigfail: refused, the server stays on the old version, and there is no "try again"', async () => {
  const t = await started();
  t.fixtures['/update/status'] = failed('signature', 'verify', {detail: 'התג v0.72.0 לא אומת כחתום — העדכון נדחה, העץ לא הוחלף: unsigned: no signature', error: 'התג v0.72.0 לא אומת כחתום — unsigned: no signature'});
  await t.poll();
  const h = t.html();
  assert.equal(t.run('UPD.phase'), 'sigfail');
  assert.match(h, /העדכון נדחה: הגרסה לא חתומה/);
  assert.match(h, /השרת ממשיך לעבוד על <span class="mono">v0\.71\.0<\/span>\. שום דבר לא הוחלף ולא הופעל מחדש\./);
  assert.deepEqual(steps(h), ['done', 'fail', 'skip', 'skip']);
  assert.match(h, /<span class="sdur">0:18<\/span>/);
  assert.match(h, /<b>לא לנסות שוב<\/b>/);
  assert.match(h, /<summary>פרטים טכניים<\/summary><pre>התג v0\.72\.0 לא אומת כחתום — unsigned: no signature<\/pre>/);
  assert.doesNotMatch(h, /נסה שוב|updRetry/);
  assert.match(h, /onclick="updCopy\(\)">העתק פרטים<[\s\S]*onclick="updClose\(\)">חזרה לקונסולה</);
  assert.doesNotMatch(h, /id="updEl"/);
  await t.run('updCopy()');
  assert.match(t.ctx.copied, /reason: signature · stage: verify/);
  assert.equal(t.node('#updFootHint').textContent, 'הפרטים הועתקו', 'feedback inside the screen — the console toast is behind it');
});

test('sigfail when the signature could not be checked says so — "not checked" is not "unsigned"', async () => {
  const t = await started();
  t.fixtures['/update/status'] = failed('signature', 'verify', {detail: 'לא אומת כחתום: unchecked: allowed-signers missing'});
  await t.poll();
  assert.match(t.html(), /העדכון נדחה: החתימה לא נבדקה/);
  assert.doesNotMatch(t.html(), /הגרסה לא חתומה/);
});

test('dlfail: firewall and DNS to check, and "try again" re-applies the same tag', async () => {
  const t = await started();
  t.fixtures['/update/status'] = failed('fetch', 'fetch', {error: "git fetch --tags נכשל: fatal: unable to access 'https://github.com/NadavOked/ImageSrv.git/': Could not resolve host: github.com"});
  await t.poll();
  const h = t.html();
  assert.equal(t.run('UPD.phase'), 'dlfail');
  assert.match(h, /<h1 id="updT" tabindex="-1">ההורדה נכשלה<\/h1>/);
  assert.deepEqual(steps(h), ['fail', 'skip', 'skip', 'skip']);
  assert.match(h, /השרת לא הצליח להגיע ל-github\.com/);
  assert.match(h, /חומת האש של המכללה[\s\S]*שרת ה-DNS/);
  assert.match(h, /<h3>מה לבדוק<\/h3><ol class="u-chk">/);
  assert.doesNotMatch(h, /ההורדה נכשלה מסיבה שאינה קשורה לרשת/);
  assert.match(h, /onclick="updRetry\(\)">נסה שוב</);
  t.fixtures['/update/status'] = running('fetch');
  await t.run('updRetry()');
  const apply = t.requests.filter((r) => r.url === '/update/apply');
  assert.deepEqual(apply.map((r) => r.body), [{tag: 'v0.72.0'}]);
  assert.equal(t.run('UPD.phase'), 'run');
});

for (const error of [
  'git fetch נכשל: cannot open .git/FETCH_HEAD: Read-only file system',
  'git fetch נכשל: ! [rejected] v0.76.4 -> v0.76.4 (would clobber existing tag)',
]) test(`dlfail without network advice: ${error}`, async () => {
  const t = await started();
  t.fixtures['/update/status'] = failed('fetch', 'fetch', {error});
  await t.poll();
  const h = t.html();
  assert.equal(t.run('UPD.phase'), 'dlfail');
  assert.doesNotMatch(h, /לא הצליח להגיע ל-github\.com|חומת האש|DNS|class="u-chk"|מה לבדוק/);
  assert.match(h, /ההורדה נכשלה מסיבה שאינה קשורה לרשת/);
  assert.match(h, /הפרטים הטכניים למטה מציגים את השגיאה שהתקבלה/);
  assert.ok(h.includes(error.replaceAll('>', '&gt;')), 'technical evidence remains visible and escaped');
});

test('rollback: the failed step in red, the restart as "returned", and the server answers on the old version', async () => {
  const t = await started({}, 'install');
  t.fixtures['/update/status'] = failed('rollback', 'install', {detail: 'בניית ה-initrd נכשלה (הפירוט ב-/var/log/imagesrv/server-upgrade.log)',
    error: 'בניית ה-initrd נכשלה · הוחזר ל-v0.71.0 (וה-initrd הקודם הוחזר)', current: 'v0.71.0'});
  await t.poll();
  const h = t.html();
  assert.equal(t.run('UPD.phase'), 'rollback');
  assert.match(h, /העדכון נכשל — השרת חזר ל-<span class="mono">v0\.71\.0<\/span>/);
  assert.deepEqual(steps(h), ['done', 'done', 'fail', 'back']);
  assert.match(h, /<span class="st">מתקין<\/span><span class="sr-only"> — נכשל<\/span><span class="sd">בניית ה-initrd נכשלה<\/span>/, 'the path in parentheses stays in the technical details, not in the RTL step line');
  assert.match(h, /הופעל מחדש על <span class="mono">v0\.71\.0<\/span> — השרת עונה/);
  assert.match(h, /<b>השרת עונה<\/b> · עם <span class="mono">v0\.71\.0<\/span>/);
  assert.match(h, /אפשר לנסות שוב אחרי שהבעיה ביומן טופלה/);
});

test('any other failure shows the server text as is — escaped', async () => {
  const t = await started({}, 'install');
  t.fixtures['/update/status'] = failed('install', 'install', {detail: '<img src=x onerror=alert(1)>', error: '<script>x</script> · אין תג קודם שמור — לא הוחזר'});
  await t.poll();
  const h = t.html();
  assert.equal(t.run('UPD.phase'), 'failed');
  assert.match(h, /העדכון ל-<span class="mono">v0\.72\.0<\/span> נכשל/);
  assert.doesNotMatch(h, /<script>|<img src/);
  assert.match(h, /&lt;script&gt;x&lt;\/script&gt;/);
  for (const reason of ['timeout', undefined, 'something-new']) {
    const u = await started({}, 'restart');
    u.fixtures['/update/status'] = failed(reason, 'restart');
    await u.poll();
    assert.equal(u.run('UPD.phase'), 'failed', `reason ${reason} → the generic failure, never "waiting"`);
  }
});

test('the screen opens by itself when the console loads during an update — and reads a stale run straight into the ceiling', async () => {
  let t = setup({fixtures: {'/update/status': running('install', {now: at(80)})}});
  await t.run('updResume()');
  assert.equal(t.run('UPD && UPD.phase'), 'run');
  assert.ok(t.node('#upd').classList.contains('show'));
  assert.equal(t.node('#app').inert, true, 'the console behind it is inert');
  assert.equal(t.node('#app').getAttribute('aria-hidden'), 'true');
  t = setup({fixtures: {'/update/status': running('install', {now: at(21 + 3600)})}});
  await t.run('updResume()');
  assert.equal(t.run('UPD.phase'), 'timeout', 'stage stamp an hour old = no progress for an hour');
  for (const st of [{state: 'idle'}, failed('fetch', 'fetch'), {...running('done'), state: 'done', verified: true}]) {
    t = setup({fixtures: {'/update/status': st}});
    await t.run('updResume()');
    assert.equal(t.run('UPD'), null, `${st.state}/${st.stage}: not running → no screen`);
  }
  t = setup({fixtures: {'/update/status': 'down'}});
  await t.run('updResume()');
  assert.equal(t.run('UPD'), null, 'a failed read does not open the screen');
});

test('"back to the console" closes the screen and restores it', async () => {
  const t = await started();
  t.fixtures['/update/status'] = failed('signature', 'verify');
  await t.poll();
  t.run('updClose()');
  assert.equal(t.run('UPD'), null);
  assert.ok(!t.node('#upd').classList.contains('show'));
  assert.equal(t.node('#app').inert, false);
});

test('after the reload: "updated ✓" only when /me reports the same version; the parameter is dropped', () => {
  let t = setup({search: '?updated=v0.72.0'});
  t.run('ME.version="v0.72.0"; updAfterReload()');
  assert.equal(JSON.stringify(t.run('toasts')), JSON.stringify([['עודכן ל-v0.72.0 ✓', 'ok']]));
  assert.deepEqual(t.replaced, ['/']);
  t = setup({search: '?updated=v0.72.0'});
  t.run('ME.version="v0.71.0"; updAfterReload()');
  assert.match(t.run('toasts[0][0]'), /השרת מדווח v0\.71\.0/);
  assert.equal(t.run('toasts[0][1]'), 'err');
  t = setup({search: '?updated=<b>x</b>'});
  t.run('updAfterReload()');
  assert.equal(t.run('toasts.length'), 0, 'only a vX.Y.Z tag is read from the URL');
});

test('login after an update whose session did not survive says why', () => {
  const t = setup({search: '?updated=v0.72.0'});
  assert.equal(t.run('updLoginNotice()'), 'עודכן ל-v0.72.0 — יש להתחבר מחדש');
  assert.match(t.run('loginFormHtml({notice: updLoginNotice()})'), /<p class="msg ok" id="login-notice" role="status"><span>עודכן ל-v0\.72\.0 — יש להתחבר מחדש<\/span><\/p>/);
  assert.equal(setup().run('updLoginNotice()'), '');
});

test('confirm (S338): three consequences, a red button, no typing — and "apply" opens the screen', async () => {
  const t = setup();
  t.run('HEALTH_UPDATE={current:"v0.71.0",enabled:true}; OVERVIEW={session:null}');
  t.run('confirmUpdateApply("v0.72.0")');
  const s = t.run('sheets[0]');
  assert.equal(s.title, 'להחיל עדכון v0.72.0?');
  assert.equal(s.sub, 'מגרסה v0.71.0');
  assert.equal(s.danger, true); assert.equal(s.submitLabel, 'החלת העדכון'); assert.equal(s.fields, undefined);
  assert.equal((s.note.match(/<li/g) || []).length, 3);
  assert.match(s.note, /השרת יופעל מחדש\. הקונסולה תראה את שלבי העדכון ותיכנס לבד כשהשרת יחזור — בדרך כלל 8–10 דקות\./);
  assert.match(s.note, /מחשב שיעלה מהרשת בדקות האלה יעלה מהדיסק המקומי\./);
  assert.match(s.note, /רק גרסה חתומה מותקנת\. אם משהו נכשל, השרת נשאר על <span class="mono">v0\.71\.0<\/span> או חוזר אליה\./);
  assert.equal(s.hint, 'אין סבב פתוח — אפשר לעדכן עכשיו');
  t.fixtures['/update/status'] = running('fetch');
  const next = await s.onSubmit();
  assert.deepEqual(t.requests.find((r) => r.url === '/update/apply').body, {tag: 'v0.72.0'});
  assert.equal(t.run('UPD'), null, 'the screen opens after the window closes, not under it');
  t.ctx.next = next; t.run('next()');
  assert.equal(t.run('UPD.phase'), 'run');
  assert.equal(t.run('UPD.from'), 'v0.71.0');
  t.run('OVERVIEW={session:{state:"running"}}; confirmUpdateApply("v0.72.0")');
  assert.equal(t.run('sheets[1].hint'), 'יש סבב פתוח — השרת יסרב לעדכן עד שיסתיים');
});

test('settings card after the run: updated / refused / returned are named notes; "up to date" only with evidence', async () => {
  const t = setup();
  const card = (status, last) => {
    t.ctx.st = status; t.ctx.lc = last || null;
    t.run('HEALTH_UPDATE={current:"v0.72.0",enabled:true,previous:"v0.71.0",last_check:lc}; UPDATE_STATUS=st; UPDATE_CHECK=null; updateError=""');
    // סידור ההגדרות (17/10): הקבוצה "עדכונים ותחזוקה" — השורות, ומתחתן ההודעות והכפתורים
    return t.run('settingsGroupHtml(SETTING_GROUPS.find((g) => g.card === "update"))');
  };
  const today = new Date().toISOString().slice(0, 10);
  const done = {state: 'done', verified: true, stage: 'done', tag: 'v0.72.0', from: 'v0.71.0', by: 'nadav', stages: {done: `${today}T11:05:00+00:00`}};
  let c = card(done, {at: `${today}T10:58:00+00:00`, current: 'v0.71.0', latest: 'v0.72.0', available: true});
  assert.match(c, /class="note ok"[\s\S]*עודכן מ-<span class="mono">v0\.71\.0<\/span> ל-<span class="mono">v0\.72\.0<\/span> היום ב-11:05 על ידי nadav · אומת: השרת ענה עם הגרסה החדשה\./);
  assert.match(c, /class="pill ok">✓ מעודכן</);
  assert.match(c, /כבר על הגרסה העדכנית \(v0\.72\.0\)/, 'the check stored on v0.71.0 no longer says "there is an update"');
  assert.doesNotMatch(c, /יש עדכון/);
  c = card({...done, stage: 'restart'});
  assert.doesNotMatch(c, /אומת: השרת ענה|מעודכן/, 'not before the script finished');
  c = card(done, {at: `${today}T10:58:00+00:00`, current: 'v0.72.0', latest: 'v0.73.0', available: true});
  assert.doesNotMatch(c, /מעודכן</, 'a newer version is known — not "up to date"');
  c = card({state: 'failed', reason: 'signature', tag: 'v0.72.0', from: 'v0.71.0', stages: {verify: `${today}T11:02:23+00:00`}});
  assert.match(c, /class="note err"[\s\S]*העדכון ל-<span class="mono">v0\.72\.0<\/span> נדחה היום ב-11:02: הגרסה לא חתומה\. השרת ממשיך על <span class="mono">v0\.71\.0<\/span>\./);
  c = card({state: 'failed', reason: 'rollback', stage: 'install', tag: 'v0.72.0', from: 'v0.71.0', stages: {install: `${today}T11:02:25+00:00`}});
  assert.match(c, /העדכון ל-<span class="mono">v0\.72\.0<\/span> נכשל היום ב-11:02 והשרת חזר ל-<span class="mono">v0\.71\.0<\/span>\. הפירוט ביומן הפעולות\./);
  c = card({state: 'failed', reason: 'install', tag: 'v0.72.0', error: 'x'});
  assert.match(c, /class="st err">העדכון ל-v0\.72\.0 נכשל: x</, 'other failures keep the server text');
});

test('index.html loads update_screen.js after console.js with the same cache-buster', () => {
  const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const m = index.match(/<script src="console\.js\?v=([\d.]+)"><\/script>\n<script src="update_screen\.js\?v=([\d.]+)"><\/script>/);
  assert.ok(m, 'update_screen.js right after console.js');
  assert.equal(m[1], m[2]);
});

// ‏#1776: "עדכן" בלשונית של שרת משנה בעץ — אותו חלון ואותו מסך, אבל כל בקשה הולכת למשני דרך
// ה-relay של הראשי. ‏/health/live של הראשי אינו ראיה על המשני, ובסוף אין טעינה של הדף (הראשי לא השתנה).
const RELAY = '/storage-nodes/haifa/console';
async function secondaryStarted(stage = 'fetch') {
  const t = setup();
  t.run('SERVER_CTX="haifa"; HEALTH_UPDATE={current:"v0.71.0",enabled:true}; OVERVIEW={session:null}');
  t.fixtures[RELAY + '/update/status'] = running(stage);
  t.run('confirmUpdateApply("v0.72.0")');
  const next = await t.run('sheets[0]').onSubmit();
  t.ctx.next = next; t.run('next()');
  await t.poll();
  return t;
}

test('secondary (#1776): apply and every status read go to the secondary through the relay — never to the primary', async () => {
  const t = await secondaryStarted();
  assert.deepEqual(t.requests.find((r) => r.url === RELAY + '/update/apply').body, {tag: 'v0.72.0'});
  assert.deepEqual([...new Set(t.requests.map((r) => r.url))], [RELAY + '/update/apply', RELAY + '/update/status']);
  assert.equal(t.run('UPD.from'), 'v0.71.0', 'from = the secondary\'s version, not the primary\'s /me');
  assert.deepEqual(steps(t.html()), ['cur', 'pend', 'pend', 'pend']);
  t.fixtures[RELAY + '/update/status'] = running('install', {now: at(74)});
  await t.poll();
  assert.deepEqual(steps(t.html()), ['done', 'done', 'cur', 'pend']);
});

test('secondary (#1776): the relay failing while it restarts is "not answering" — the primary\'s /health/live is never asked', async () => {
  const t = await secondaryStarted('install');
  t.fixtures[RELAY + '/update/status'] = {code: 502, detail: 'חיפה לא ענה'};
  t.fixtures['/health/live'] = {ok: true, version: 'v0.71.0'};
  t.fixtures['/update/status'] = {state: 'idle'};
  t.clock.now += 3000; await t.poll();
  t.clock.now += 3000; await t.poll();
  assert.equal(t.run('UPD.off'), true);
  assert.match(t.html(), /השרת לא עונה — צפוי בזמן ההפעלה מחדש/);
  assert.ok(!t.requests.some((r) => r.url === '/health/live' || r.url === '/update/status'), 'only the relay');
  t.fixtures[RELAY + '/update/status'] = {...running('done', {now: at(186)}), state: 'done', verified: true, current: 'v0.72.0'};
  await t.poll();
  assert.equal(t.run('UPD.phase'), 'done');
  assert.match(t.html(), /חזרה ללשונית של שרת המשנה/);
  for (let i = 0; i < 5; i++) t.run('updCountdown()');
  assert.deepEqual(t.navigated, [], 'no reload — the files in the page are the primary\'s, which did not change');
  assert.equal(t.run('UPD'), null, 'the screen closed');
  assert.equal(JSON.stringify(t.run('toasts')), JSON.stringify([['שרת המשנה עודכן ל-v0.72.0 ✓', 'ok']]));
});

test('secondary (#1776): the secondary refusing an unsigned tag shows the refusal and its reason', async () => {
  const t = await secondaryStarted('fetch');
  t.fixtures[RELAY + '/update/status'] = failed('signature', 'verify', {error: 'התג v0.72.0 לא אומת כחתום: no principal'});
  await t.poll();
  const h = t.html();
  assert.equal(t.run('UPD.phase'), 'sigfail');
  assert.match(h, /העדכון נדחה: הגרסה לא חתומה/);
  assert.match(h, /לא אומת כחתום: no principal/);
});

test('secondary (#1776): a refusal of the apply itself (relay or secondary) stays in the window, visible', async () => {
  const t = setup();
  t.run('SERVER_CTX="haifa"; HEALTH_UPDATE={current:"v0.71.0",enabled:true}; OVERVIEW={session:null}');
  t.fixtures[RELAY + '/update/apply'] = {code: 404, detail: 'הפעולה אינה זמינה על שרת המשנה מכאן'};
  t.run('confirmUpdateApply("v0.72.0")');
  await assert.rejects(t.run('sheets[0]').onSubmit(), /הפעולה אינה זמינה על שרת המשנה מכאן/);
  assert.equal(t.run('UPD'), null);
});
