// #1802 (נדב 04/11) — מחשב לא רשום ברשת ההפצה נשאר על המסך עד F2. השורה בעמוד ההגדרות:
// "מחשב לא רשום: עלייה אוטומטית מהדיסק אחרי N שניות" — מספר שלם 0–600 בכרטיס "שיכפול והפצה",
// 0 = "כבויה" (ברירת המחדל). נשמר מחלון קטן, כמו שאר המספרים.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = fs.readFileSync(path.resolve(__dirname, '../server/static/console.js'), 'utf8');
const KEY = 'unregistered_autoboot_seconds';

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

test('#1802: the row is in "שיכפול והפצה", a whole number 0–600 seconds', () => {
  const r = vm.runInContext(`settingRow("${KEY}")`, ctx());
  assert.ok(r, `${KEY} has a row in SETTING_ROWS`);
  assert.equal(r.card, 'deploy');
  assert.equal(r.type, 'number');
  assert.equal(r.label, 'מחשב לא רשום: עלייה אוטומטית מהדיסק');
  assert.deepEqual([r.min, r.max, r.unit], [0, 600, 'שניות']);
});

test('#1802: the value reads "אחרי N שניות", and 0 reads "כבויה"', () => {
  const c = ctx({[KEY]: '0'});
  const r = vm.runInContext(`settingRow("${KEY}")`, c);
  assert.equal(c.settingNumberText(r, '30'), '<bdi>אחרי 30 שניות</bdi>');
  assert.equal(c.settingNumberText(r, '0'), '<span class="muted">כבויה</span>');
});

test('#1802: the rendered row reads, word for word, the line Nadav approved (04/11)', () => {
  const c = ctx({[KEY]: '30'});
  const r = vm.runInContext(`settingRow("${KEY}")`, c);
  const text = (html) => html.replace(/<[^>]+>/g, '');
  // dt = התווית, dd = הערך (settingsKv) — השורה כפי שהיא נקראת
  assert.equal(`${r.label} ${text(c.settingNumberText(r, '30'))}`, 'מחשב לא רשום: עלייה אוטומטית מהדיסק אחרי 30 שניות');
  assert.equal(`${r.label} ${text(c.settingNumberText(r, '0'))}`, 'מחשב לא רשום: עלייה אוטומטית מהדיסק כבויה');
});

test('#1802: the sheet posts 30, and refuses 601 and -1 before posting', async () => {
  const c = ctx({[KEY]: '0'});
  c.settingsValueSheet(KEY);
  assert.equal(c.sheets.length, 1);
  const s = c.sheets[0];
  for (const bad of ['601', '-1', '2.5', '']) {
    await assert.rejects(s.onSubmit({v: bad}), (e) => e.message === 'הערך חייב להיות בין 0 ל-600 שניות');   // מילה במילה
  }
  assert.equal(c.posted.length, 0, 'nothing posted for a refused value');
  await s.onSubmit({v: '30'});
  assert.deepEqual(JSON.parse(JSON.stringify(c.posted)), [['/settings', {[KEY]: '30'}]]);
});
