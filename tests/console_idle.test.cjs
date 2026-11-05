// Nadav 18/10: automatic logout when idle -- a switch, and "אחרי N דקות" while it is on. Minutes in the
// console, seconds on the server (console_idle_seconds; 0 = off; a fresh install: 600 = on, 10 minutes).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = fs.readFileSync(path.resolve(__dirname, '../server/static/console.js'), 'utf8');

function functionSource(name) {
  const lines = js.split('\n');
  const start = lines.findIndex(line => new RegExp(`^(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `${name} exists in console.js`);
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === '}');
  return lines.slice(start, end + 1).join('\n');
}
function ctx(settings, extra = {}) {
  const c = vm.createContext({SETTINGS: settings, ...extra});
  vm.runInContext('const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };', c);
  const rows = js.slice(js.indexOf('const SETTING_ROWS = ['), js.indexOf('];', js.indexOf('const SETTING_ROWS = [')) + 2);
  vm.runInContext(rows + '\n' + js.match(/const IDLE_MAX_MINUTES = .*;/)[0], c);
  for (const n of ['esc', 'ghostBtn', 'classroomsOn', 'idleSecondsNow', 'idleMinutesText', 'settingRowVisible', 'settingDd', 'idleMinutesSheet', 'startIdleWatch'])
    vm.runInContext(functionSource(n), c);
  return c;
}
const rowsOf = (c) => vm.runInContext('SETTING_ROWS.filter((r) => r.key === "console_idle_seconds")', c);

test('a fresh install: the switch is on and the row says "אחרי 10 דקות"', () => {
  const c = ctx({console_idle_seconds: '600'});
  const [sw, after] = rowsOf(c);
  assert.equal(sw.label, 'ניתוק אוטומטי בחוסר פעילות');
  assert.match(c.settingDd(sw), /role="switch"[^>]* checked onchange="idleSwitch\(this\)"/);
  assert.equal(c.settingRowVisible(after), true);
  assert.match(c.settingDd(after), /<span class="sv">10 דקות<\/span>/);
  assert.equal(after.label, 'אחרי');
});

test('TA keeps 300: shown as on, 5 minutes; one minute in Hebrew', () => {
  const c = ctx({console_idle_seconds: '300'});
  assert.match(c.settingDd(rowsOf(c)[1]), /5 דקות/);
  assert.equal(c.idleMinutesText(60), 'דקה אחת');
});

test('off (0): the switch is off and there is no "after" row', () => {
  const c = ctx({console_idle_seconds: '0'});
  const [sw, after] = rowsOf(c);
  assert.doesNotMatch(c.settingDd(sw), / checked/);
  assert.equal(c.settingRowVisible(after), false);
});

test('no seconds anywhere in the console rows', () => {
  const c = ctx({console_idle_seconds: '600'});
  for (const r of rowsOf(c)) assert.doesNotMatch(c.settingDd(r) + r.label + (r.more || ''), /שניות/);
});

test('the minutes window: whole minutes 1–1440 are sent as seconds; anything else is refused in Hebrew', async () => {
  const posted = [];
  let opened = null;
  const c = ctx({console_idle_seconds: '600'}, {
    isAdmin: () => true, sheet: (o) => { opened = o; },
    idleSave: async (s) => posted.push(s),
  });
  c.idleMinutesSheet();
  assert.equal(opened.fields[0].value, '10');
  assert.match(opened.fields[0].label, /דקות \(1–1440\)/);
  for (const bad of ['0', '1441', '2.5', 'abc', '']) await assert.rejects(opened.onSubmit({v: bad}), /דקות, 1–1440/, bad);
  await opened.onSubmit({v: '7'});
  assert.deepEqual(posted, [420]);
});

test('the client timer does not run when the value is 0, and runs when it is on', () => {
  let started = 0;
  const base = {idleTimer: null, setInterval: () => { started++; return 1; }, clearInterval() {}, noteActivity() {}};
  ctx({}, {...base, ME: {idle_seconds: 0}}).startIdleWatch();
  assert.equal(started, 0, '0 = off: no timer, no logout');
  ctx({}, {...base, ME: {idle_seconds: 600}}).startIdleWatch();
  assert.equal(started, 1);
});

// #2414: the server now also drops a cookie it has not seen for console_idle_seconds (+1 min). A hidden
// console tab does not poll, so activity in a separate monitor window (#1854) has to reach the server
// some other way: at most one GET /me a minute, and only when there was activity since the last one.
test('a hidden console with activity pings the server at most once a minute; a visible or idle one does not', () => {
  const calls = [];
  const c = vm.createContext({document: {hidden: true}, api: (p) => { calls.push(p); return Promise.resolve({}); }, Date});
  vm.runInContext('let lastActivity = 0; let idleLastPing = 0;\n' + functionSource('idleKeepAlive'), c);
  const t0 = 10_000_000;
  vm.runInContext(`lastActivity = ${t0}`, c);
  assert.equal(c.idleKeepAlive(t0 + 1000), true);
  assert.deepEqual(calls, ['/me']);
  vm.runInContext(`lastActivity = ${t0 + 2000}`, c);
  assert.equal(c.idleKeepAlive(t0 + 30_000), false, 'not twice within a minute');
  assert.equal(c.idleKeepAlive(t0 + 61_001), true, 'activity since the last ping, a minute later');
  assert.equal(c.idleKeepAlive(t0 + 200_000), false, 'no activity since the last ping: no ping');
  vm.runInContext(`lastActivity = ${t0 + 300_000}; document.hidden = false;`, c);
  assert.equal(c.idleKeepAlive(t0 + 400_000), false, 'a visible tab already polls every 2 seconds');
  assert.equal(calls.length, 2);
});

test('the idle tick calls the keep-alive while time is left', () => {
  assert.match(functionSource('startIdleWatch'), /if \(left > 0\) idleKeepAlive\(\);/);
});
