// ‏#2645/#2971 (נדב 07/11, 12/11): מחשב שלא מדווח — הודעה אחת, כפתור הדלקה (WoL), בלי iframe שמנסה להתחבר.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('#2645: the monitor viewer of an offline machine says it is off, offers WoL and draws no iframe', () => {
  const src = consoleJs();
  const i = src.indexOf('function monitorViewerHtml('), body = src.slice(i, src.indexOf('\n}\n', i));
  const off = body.slice(body.indexOf('else if (!m.online)'), body.indexOf('else if (MON_OUT)'));
  assert.match(off, /כבוי או לא מחובר/);
  assert.match(off, /wakeMachine\('\$\{encodeId\(m\.mac\)\}'\)/, 'WoL: same flow as the machines page');
  assert.match(off, /הדלקה \(WoL\)/);
  assert.match(off, /isAdmin\(\)/, 'only an admin gets the button');
  assert.doesNotMatch(off, /<iframe/, 'no connection attempt, so no "retry in N s"');
});

test('#2971: the "no monitor connection" refusal no longer contradicts the confirm text', () => {
  const py = [path.resolve(__dirname, '../server/console/monitor.py'), path.resolve(__dirname, '../server/console/storage.py')]
    .map((f) => fs.readFileSync(f, 'utf8')).join('\n');
  assert.doesNotMatch(py, /אין מוניטור פתוח למכונה/);
  assert.match(py, /המחשב לא מחובר, ולכן אי אפשר לשלוח לו את הפקודה/);
});
