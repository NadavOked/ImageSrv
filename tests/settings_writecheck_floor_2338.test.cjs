// #2338 (נדב 03/11) — סף מהירות הכתיבה לפני הזרם הוא שורה בעמוד ההגדרות: "סף מהירות כתיבה לדיסק (MB/s)",
// מספר שלם 10–500 בכרטיס "שיכפול והפצה", עם ההסבר שדיסק איטי מעכב את כל הגל. נשמר מחלון קטן, כמו שאר המספרים.
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
function ctx(settings = {}) {
  const posted = [], sheets = [];
  const c = vm.createContext({
    SETTINGS: settings, posted, sheets,
    isAdmin: () => true, sheet: (o) => sheets.push(o), toast: () => {},
    post: async (url, body) => { posted.push([url, body]); }, loadSettingsData: async () => {},
  });
  vm.runInContext('const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };', c);
  const rows = js.slice(js.indexOf('const SETTING_ROWS = ['), js.indexOf('];', js.indexOf('const SETTING_ROWS = [')) + 2);
  vm.runInContext(rows, c);
  for (const n of ['esc', 'settingRow', 'settingCurrent', 'settingSecondsText', 'settingNumberText', 'settingsValueSheet']) vm.runInContext(functionSource(n), c);
  return c;
}

test('#2338: the row is in "שיכפול והפצה", a whole number 10–500 MB/s, and says why', () => {
  const c = ctx();
  const r = vm.runInContext('settingRow("writecheck_min_mbps")', c);
  assert.ok(r, 'writecheck_min_mbps has a row in SETTING_ROWS');
  assert.equal(r.card, 'deploy');
  assert.equal(r.type, 'number');
  assert.equal(`${r.label} (${r.unit})`, 'סף מהירות כתיבה לדיסק (MB/s)');
  assert.deepEqual([r.min, r.max], [10, 500]);
  assert.match(r.more, /דיסק איטי מעכב את כל הגל/);
});

test('#2338: the value reads "50 MB/s"', () => {
  const c = ctx({writecheck_min_mbps: '50'});
  const r = vm.runInContext('settingRow("writecheck_min_mbps")', c);
  assert.equal(c.settingNumberText(r, '50'), '<bdi>50 MB/s</bdi>');
  assert.equal(c.settingNumberText(r, ''), '<span class="muted">לא נקבע</span>');
});

test('#2338: the sheet names the unit and the range, posts 40, and refuses 9 and 501 before posting', async () => {
  const c = ctx({writecheck_min_mbps: '50'});
  c.settingsValueSheet('writecheck_min_mbps');
  assert.equal(c.sheets.length, 1);
  const s = c.sheets[0];
  assert.equal(s.fields[0].label, 'סף מהירות כתיבה לדיסק (MB/s, 10–500)');
  assert.equal(s.fields[0].value, '50');
  for (const bad of ['9', '501', '40.5', '']) {
    await assert.rejects(s.onSubmit({v: bad}), /סף מהירות כתיבה לדיסק: מספר שלם, 10–500/);
  }
  assert.equal(c.posted.length, 0, 'nothing posted for a refused value');
  await s.onSubmit({v: '40'});
  assert.deepEqual(JSON.parse(JSON.stringify(c.posted)), [['/settings', {writecheck_min_mbps: '40'}]]);
});
