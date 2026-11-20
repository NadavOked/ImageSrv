// #3320 (R1): החלטת המוח בקונסולה מוצגת בשורה בעברית שהשרת בונה (`line_he`, server/brain/say.py), ובלעדיה
// בסיבה (`reason`), כמו לפני #3320. memory_ui.js האמיתי רץ ב-vm, עם התלויות שלו מ-console.js כפונקציות מזעריות.
//
// בקרה שלילית: החזרת `esc(d.reason)` ב-memDecisionEvents מפילה את הטסט הראשון.
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function load() {
  const ctx = vm.createContext({
    Date, JSON, Number, String, Math,
    esc: (s) => String(s ?? ''), ltr: (s) => s, fmtDate: () => '15/11', api: async () => ({}),
    UI: {pill: (cls, text) => `[${text}]`, timeline: (ev) => ev.map((e) => e.text).join('|')},
  });
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../server/static/memory_ui.js'), 'utf8'), ctx);
  return ctx;
}

test('a decision shows the Hebrew line the server built', () => {
  const ctx = load();
  const [ev] = ctx.memDecisionEvents([{at: '2026-11-15T10:00:00+00:00', reason: 'aa=ALIVE', outcome: 'x',
    outcome_state: 'closed', line_he: 'משכפל aa → הכשלת הגל: החזקה. aa=ALIVE (צל: רק נרשם)'}]);
  assert.match(ev.text, /^משכפל aa → הכשלת הגל: החזקה\. aa=ALIVE \(צל: רק נרשם\)/);
  assert.equal(ev.who, 'המוח');
});

test('a decision from a server without line_he still shows its reason', () => {
  const ctx = load();
  const [ev] = ctx.memDecisionEvents([{at: '2026-11-15T10:00:00+00:00', reason: 'הסיבה', outcome: null,
    outcome_state: 'open'}]);
  assert.match(ev.text, /^הסיבה \[עוד פתוחה\]/);
});
