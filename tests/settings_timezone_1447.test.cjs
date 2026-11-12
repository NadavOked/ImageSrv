// #1447 (נדב 17/10) — שורת "אזור זמן" בכרטיס "כללי" של ההגדרות: האזור שבתוקף, מסך שרת שמדווח
// אזור אחר נאמר, "לא נקרא" שונה מערך, והשינוי הולך ל-POST /timezone (לא /settings) מחלון בחירה.
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
  const ctx = vm.createContext({...state, calls, isAdmin: () => true,
    sheet: (o) => calls.push(['sheet', o]), post: async (url, body) => calls.push(['post', url, body]),
    toast: () => {}, loadSettingsData: async () => calls.push(['reload'])});
  for (const name of ['esc', 'ltr', 'ghostBtn', 'settingRow', 'settingUnreadHtml', 'isRawTechnical', 'timezoneRow', 'timezoneSheet']) vm.runInContext(functionSource(name), ctx);
  vm.runInContext('const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };', ctx);
  const rows = js.slice(js.indexOf('const SETTING_ROWS'), js.indexOf('];', js.indexOf('const SETTING_ROWS')) + 2);
  vm.runInContext(rows.replace('const SETTING_ROWS', 'var SETTING_ROWS'), ctx);
  return ctx;
}
const STATUS = {zone: 'Asia/Jerusalem', system: 'Asia/Jerusalem', zones: ['Asia/Jerusalem', 'Europe/London', 'UTC']};
const row = (ctx) => vm.runInContext('timezoneRow(settingRow("timezone"))', ctx);   // settingDd → timezoneRow(r)[1] (#1685)

test('the general card has a timezone row, written through /timezone and never /settings', () => {
  assert.match(js, /key: "timezone", card: "general", label: "אזור זמן", type: "timezone"/);
  assert.match(js, /TZ_STATUS = await api\("\/timezone"\)/);
});

test('the row shows the zone in effect, LTR, with a change link', () => {
  const [label, dd] = row(ctxWith({TZ_STATUS: STATUS, tzError: ''}));
  assert.equal(label, 'אזור זמן');
  assert.match(dd, /^<bdi dir="ltr">Asia\/Jerusalem<\/bdi> <button[^>]*onclick="timezoneSheet\(\)">שינוי<\/button>$/);
});

test('an OS zone that differs from the setting is said; unread differs from a value', () => {
  const [, differs] = row(ctxWith({TZ_STATUS: {...STATUS, system: 'Etc/UTC'}, tzError: ''}));
  assert.match(differs, /מסך השרת: <bdi dir="ltr">Etc\/UTC<\/bdi>/);
  const [, unknown] = row(ctxWith({TZ_STATUS: {...STATUS, system: null}, tzError: ''}));
  assert.match(unknown, /מסך השרת: לא נקרא/);
  const [, unread] = row(ctxWith({TZ_STATUS: null, tzError: 'שגיאה 500'}));
  assert.match(unread, /לא נקרא: שגיאה 500/);
  assert.doesNotMatch(unread, /timezoneSheet/, 'nothing to change when nothing was read');
});

test('#1676: a raw Python error is behind "פרטים טכניים", not in the row', () => {
  const err = "רשימת אזורי הזמן לא נקראה: [Errno 2] No such file or directory: '/usr/share/zoneinfo/zone.tab'";
  const [, dd] = row(ctxWith({TZ_STATUS: null, tzError: err}));
  const visible = dd.replace(/<details[\s\S]*<\/details>/, '');
  assert.match(visible, /^<span class="muted">לא נקרא<\/span>$/);
  assert.doesNotMatch(visible, /Errno|zone\.tab/);
  assert.match(dd, /<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">[^<]*Errno 2[^<]*<\/code><\/details>/);
});

test('the sheet is a select of the server\'s zones and saves to POST /timezone', async () => {
  const ctx = ctxWith({TZ_STATUS: STATUS, tzError: ''});
  vm.runInContext('timezoneSheet()', ctx);
  const [kind, opts] = ctx.calls[0];
  assert.equal(kind, 'sheet');
  assert.equal(opts.fields[0].type, 'select');
  assert.equal(opts.fields[0].value, 'Asia/Jerusalem');
  assert.deepEqual(JSON.parse(JSON.stringify(opts.fields[0].options.map(o => o.value))), STATUS.zones);
  await opts.onSubmit({zone: 'Europe/London'});
  assert.deepEqual(JSON.parse(JSON.stringify(ctx.calls.slice(1))), [['post', '/timezone', {zone: 'Europe/London'}], ['reload']]);
});
