// #2182: since #2067 the switch governs five tunes on the cloner and the build machine
// (agent/lib/core/roundbeep.sh); the description must say so.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const js = consoleJs();

test('#2182: cloner_beep describes all five tunes and the build machine', () => {
  const match = js.match(/\{ key: "cloner_beep"[\s\S]*?\},?\r?\n/);
  assert.ok(match, 'cloner_beep exists in SETTING_ROWS');
  const entry = match[0];
  assert.doesNotMatch(entry, /שלושה צפצופים/);
  assert.doesNotMatch(entry, /צפצוף ארוך/);
  for (const words of ['הצלחה', 'כשל', 'ניתוק רשת', 'הרשת חזרה', 'מגירה שנשלפה', 'מחשב הבנייה']) {
    assert.ok(entry.includes(words), `cloner_beep mentions ${words}`);
  }
});

// #2067: the label said "צליל בסיום סבב שיכפול" -- but the switch also governs the build machine
// and the tunes in the middle of a job (a drawer pulled, the network lost and back).
test('#2067: the cloner_beep label covers the build machine and the events in the middle', () => {
  const match = js.match(/\{ key: "cloner_beep"[^\n]*\n/);
  assert.ok(match, 'cloner_beep exists in SETTING_ROWS');
  assert.match(match[0], /label: "צלילי המשכפל ומחשב הבנייה"/);
  assert.doesNotMatch(match[0], /label: "[^"]*בסיום סבב/, 'not "at the end of a round" only');
});
