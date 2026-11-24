// ‏#2901 / #2903: ניסוח למפעיל — בלי נתיבי API, שמות פונקציות ושגיאות כתיב בטקסט שמוצג.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../server/static');

test('#2901: operator-facing captions carry no API path, Depends, DMI/hello or "None"', () => {
  const src = consoleJs(), drv = fs.readFileSync(path.join(root, 'drivers.js'), 'utf8');
  assert.doesNotMatch(src, /small: "עד 20 האחרונות מ-\/tasks"/);
  assert.doesNotMatch(src, /נגזר מה-Depends/);
  assert.doesNotMatch(src, /const src = `נמדד: כתובות ופער — \/net\/config/);
  assert.doesNotMatch(drv, /small: "הדגם נקבע מ-DMI/);
  assert.match(drv, /real\(dmi\.sys_vendor\)/, 'an old stored inventory with "None" is cleaned in the label too');
});

test('#2903: "התכנאי" typo is gone, and one item is said in the singular', () => {
  const src = consoleJs();
  assert.doesNotMatch(src, /התכנאי/);
  assert.match(src, /הטכנאי\. אין לו כניסה לקונסולה/);
  assert.match(src, /o\.images === 1 \? "אימג' אחד"/);
  assert.match(src, /list\.length === 1 \? "משתמש אחד"/);
});
