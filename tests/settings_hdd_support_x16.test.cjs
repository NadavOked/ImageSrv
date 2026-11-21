// X16 (נדב 14/11) — "תמיכה ב-HDD" היא מדיניות, לא סף: מתג אחד בכרטיס "שיכפול והפצה", באותו דפוס של
// שאר המתגים, כבוי כברירת מחדל, ובלי מספר. "סף מהירות כתיבה לדיסק" (#2338) יצא מההגדרות (#3368, נדב 14/11 בערב):
// הלמידה (LR5) כותבת אותו, ולכן אין בהגדרות שום מספר מהירות — לא לדיסק רגיל ולא ל-HDD.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');

function settingRows() {
  const js = consoleJs();
  const start = js.indexOf('const SETTING_ROWS = [');
  assert.ok(start >= 0, 'SETTING_ROWS not found');
  const context = vm.createContext({});
  vm.runInContext(js.slice(start, js.indexOf('];', start) + 2), context);
  return vm.runInContext('SETTING_ROWS', context);
}

test('X16: the HDD switch is a switch like the others, off by default, with no number', () => {
  const rows = settingRows();
  const row = rows.find(r => r.key === 'hdd_support');
  assert.ok(row, 'no hdd_support row');
  assert.equal(row.card, 'deploy');
  assert.equal(row.label, 'תמיכה ב-HDD');
  assert.equal(row.type, 'switch');
  assert.notEqual(row.defaultOn, true);                      // off, like the server's default
  for (const k of ['min', 'max', 'unit']) assert.equal(row[k], undefined, k);
  assert.match(row.more, /כמקור בלבד/);
  const other = rows.find(r => r.key === 'learned_rate_enabled');
  assert.deepEqual(Object.keys(row).filter(k => !['more', 'defaultOn'].includes(k)).sort(),
                   Object.keys(other).filter(k => !['more', 'defaultOn'].includes(k)).sort());
});

test('X16: no speed number in the settings (#3368, Nadav 14/11 evening): the write floor is learned, and the HDD switch stands alone', () => {
  const rows = settingRows();
  assert.equal(rows.find(r => r.key === 'writecheck_min_mbps'), undefined, 'the write floor row is back in the settings');
  assert.ok(rows.some(r => r.key === 'hdd_support'), 'no hdd_support row');
  for (const r of rows) if (/hdd|writecheck/i.test(r.key)) assert.equal(r.type, 'switch', `${r.key}: a drive-speed number is back in the settings`);
});
