const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const js = consoleJs();

// סידור ההגדרות (נדב 17/10): ההפעלה מחדש היא שורה אחת בקבוצת "עדכונים ותחזוקה" ("כל יום ב-03:00"),
// וחלון אחד עורך את שלושת הערכים — לא שלוש שורות עם שלושה כפתורי "שינוי".
test('scheduled reboot is one row in the maintenance group and uses one sheet', () => {
  assert.match(js, /key: "auto_reboot", card: "update", label: "הפעלה מחדש אוטומטית", type: "reboot"/);
  for (const key of ['auto_reboot_time', 'auto_reboot_weekday'])
    assert.doesNotMatch(js, new RegExp(`key: "${key}", card:`), `${key} is edited in the sheet, not a row of its own`);
  assert.match(js, /title: "הפעלה מחדש אוטומטית"/);
  assert.match(js, /label: "שעה"/);
  assert.match(js, /label: "יום \(בהפעלה שבועית\)"/);
  assert.doesNotMatch(js, /UI\.card\(\{ title: "הפעלה מחדש אוטומטית"/);
});
