// #2182: since #2067 the switch governs five tunes on the cloner and the build machine
// (agent/lib/core/roundbeep.sh); the description must say so.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const js = fs.readFileSync(path.resolve(__dirname, '../server/static/console.js'), 'utf8');

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
