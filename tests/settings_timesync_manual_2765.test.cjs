// #2765 (נדב 09/11) — "ידני — בלי שרת זמן" בהגדרות ← מקור השעה: האפשרות השלישית, השעה ההתחלתית
// מהשעון של השרת, ומצב השעון שמוצג כמידע ולא כ"לא מתואם". שרת זמן שנבחר ולא עונה נשאר אזהרה.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = consoleJs();

function functionSource(name) {
  const lines = js.split('\n');
  const start = lines.findIndex(line => new RegExp(`^(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `${name} exists in console.js`);
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === '}');
  return lines.slice(start, end + 1).join('\n');
}

function ctxWith(state) {
  const calls = [];
  const UI = {pill: (cls, text) => `<pill class="${cls}">${text}</pill>`};
  const ctx = vm.createContext({...state, UI, calls, isAdmin: () => true,
    sheet: (o) => calls.push(['sheet', o]), post: async (url, body) => calls.push(['post', url, body]),
    toast: () => {}, loadHostCfg: async () => {}, renderCurrent: () => {},
    fmtDate: (ts) => '09/11/2026', fmtClock: (ts) => '14:32',
    zoneParts: () => ({y: '2026', mo: '11', d: '09', h: '14', mi: '32', s: '07', utc: false}),
    ghostBtn: (label) => `<btn>${label}</btn>`, settingUnreadHtml: (t) => t});
  vm.runInContext('var NTP_STATUS = null, ntpError = "";', ctx);
  vm.runInContext('const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };', ctx);
  for (const name of ['esc', 'ntpSourceHtml', 'ntpManualHtml', 'ntpStatusHtml', 'timesyncSheet']) vm.runInContext(functionSource(name), ctx);
  ctx.NTP_STATUS = state.NTP_STATUS;
  return ctx;
}
const plain = (value) => JSON.parse(JSON.stringify(value));   // objects made inside the vm context are another realm's
const SYNCED = {synchronized: true, server: '10.44.0.250', offset_ms: 0.4, checked_at: '2026-11-09T12:32:00+00:00'};
const NONE = {synchronized: false, server: null, offset_ms: null, checked_at: '2026-11-09T12:32:00+00:00'};
const manual = (over = {}) => ({NTP_STATUS: {ntp_servers: '', mode: 'manual', manual_at: '2026-11-09T12:32:00+00:00', status: NONE, ...over}});

test('manual is information: a calm pill with when it was set, never "לא מתואם", and nothing to "check now"', () => {
  const html = vm.runInContext('ntpStatusHtml()', ctxWith(manual()));
  assert.match(html, /<pill class="info">שעה ידנית · הוגדרה לאחרונה 09\/11\/2026 14:32 · לא נמדדת מול שרת זמן<\/pill>/);
  assert.match(html, /אימות דו-שלבי ותעודת TLS נשענים על השעה הזו/);
  assert.doesNotMatch(html, /לא מתואם|warn|בדוק עכשיו/);
});

test('an unrecorded moment is said to be unknown, not guessed', () => {
  const html = vm.runInContext('ntpStatusHtml()', ctxWith(manual({manual_at: ''})));
  assert.match(html, /מועד ההגדרה לא ידוע/);
});

test('a chosen time server that does not answer is still the warning it was', () => {
  const html = vm.runInContext('ntpStatusHtml()', ctxWith({NTP_STATUS: {ntp_servers: '10.44.0.250', mode: 'org', manual_at: '', status: NONE}}));
  assert.match(html, /<pill class="warn">לא מתואם — שרת הזמן טרם סנכרן את השעון<\/pill>/);
  const ok = vm.runInContext('ntpStatusHtml()', ctxWith({NTP_STATUS: {ntp_servers: '', mode: 'auto', manual_at: '', status: SYNCED}}));
  assert.match(ok, /<pill class="ok">● מתואם · 10\.44\.0\.250/);
});

test('the source row names the manual choice', () => {
  assert.match(vm.runInContext('ntpSourceHtml()', ctxWith(manual())), /ידני — בלי שרת זמן/);
  assert.match(vm.runInContext('ntpSourceHtml()', ctxWith({NTP_STATUS: {ntp_servers: '', mode: 'auto', status: NONE}})), /אוטומטי \(שרתי זמן באינטרנט\)/);
});

test('the sheet has three sources, starts the time from the server clock, and posts mode+time for manual only', async () => {
  const ctx = ctxWith(manual());
  vm.runInContext('timesyncSheet()', ctx);
  const [, opts] = ctx.calls.find(c => c[0] === 'sheet');
  const mode = opts.fields.find(f => f.id === 'mode');
  assert.deepEqual(plain(mode.options.map(o => o.value)), ['auto', 'custom', 'manual']);
  assert.equal(mode.value, 'manual');
  assert.equal(opts.fields.find(f => f.id === 'time').value, '2026-11-09 14:32:07');
  await opts.onSubmit({mode: 'manual', servers: '', time: '2026-11-09 14:40'});
  assert.deepEqual(plain(ctx.calls.find(c => c[0] === 'post')), ['post', '/timesync', {mode: 'manual', time: '2026-11-09 14:40'}]);
  ctx.calls.length = 0;
  await opts.onSubmit({mode: 'custom', servers: '10.44.0.250', time: '2026-11-09 14:40'});
  assert.deepEqual(plain(ctx.calls.find(c => c[0] === 'post')), ['post', '/timesync', {ntp_servers: '10.44.0.250'}], 'a time server never carries the manual time');
  ctx.calls.length = 0;
  await opts.onSubmit({mode: 'auto', servers: 'x', time: '2026-11-09 14:40'});
  assert.deepEqual(plain(ctx.calls.find(c => c[0] === 'post')), ['post', '/timesync', {ntp_servers: ''}]);
});

test('the health page knows the info state: a pill that is not red or orange, and not counted as a problem', () => {
  assert.match(js, /info: \["info", "מידע"\]/);
  assert.match(js, /const HEALTH_SHORT = \{ ok: "תקין", info: "מידע",/);
  // every counter of "needs attention" reads warn and bad by name -- an unknown state would not be one of them
  assert.match(js, /if \(c\.state === "warn" \|\| c\.state === "bad"\) n\+\+;/);
  assert.match(js, /const failing = HEALTH\.filter\(\(c\) => c\.state === "bad" \|\| c\.state === "warn"\);/);
  assert.match(js, /if \(c\.state !== "warn" && c\.state !== "bad"\) continue;/);
});
