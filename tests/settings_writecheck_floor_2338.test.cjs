// #2338 (נדב 03/11) הציב את סף מהירות הכתיבה לפני הזרם כשורה בעמוד ההגדרות. נדב 14/11 בערב: "ספים טכניים לא בהגדרות",
// ובין הדוגמאות "מהירות מינימלית לדיסק איטי". השורה יצאה, והסף נקבע בשרת (הלמידה, LR5): אין בקונסולה אף שורה שנוגעת בו,
// וגם לא משפט שמפנה את המפעיל להגדרה.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const js = consoleJs();

test('the write-check floor is not a row in the settings screen, and nothing in the console sends the operator to it', () => {
  const rows = js.slice(js.indexOf('const SETTING_ROWS = ['), js.indexOf('];', js.indexOf('const SETTING_ROWS = [')) + 2);
  const c = vm.createContext({});
  vm.runInContext(rows, c);
  const keys = JSON.parse(JSON.stringify(vm.runInContext('SETTING_ROWS.map((r) => r.key)', c)));
  assert.ok(keys.length > 10, 'the rows were read');
  assert.ok(!keys.includes('writecheck_min_mbps'));
  assert.ok(!js.includes('writecheck_min_mbps'), 'console.js does not name it');
  assert.ok(!js.includes('סף מהירות כתיבה לדיסק'), 'no label of a row that does not exist');
});
