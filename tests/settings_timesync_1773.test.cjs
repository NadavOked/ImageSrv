const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const js = fs.readFileSync('server/static/console.js', 'utf8');

test('general settings render time source and clock state directly after timezone', () => {
  const zone = js.indexOf('label: "אזור זמן"');
  const source = js.indexOf('label: "מקור השעה"');
  const status = js.indexOf('label: "מצב השעון"');
  assert.ok(zone >= 0 && zone < source && source < status);
  for (const text of ['אוטומטי (שרתי זמן באינטרנט)', 'שרת זמן של הארגון:', '● מתואם ·', 'לא מתואם —', 'לא נבדק', 'בדוק עכשיו']) assert.ok(js.includes(text), text);
});

test('time source uses sheet radio and field, never prompt', () => {
  const start = js.indexOf('function timesyncSheet()');
  const end = js.indexOf('async function timesyncCheckNow()', start);
  const body = js.slice(start, end);
  assert.match(body, /sheet\(\{/);
  assert.match(body, /type: "radio"/);
  assert.match(body, /כתובות \(IP או שם, מופרדות ברווח\)/);
  assert.match(body, /submitLabel: "שמירה"/);
  assert.doesNotMatch(body, /prompt\s*\(/);
  assert.match(body, /post\("\/timesync"/);
});

test('per-NIC services expose NTP on UDP 123', () => {
  const nic = fs.readFileSync('server/static/nicsvc.js', 'utf8');
  assert.match(nic, /k: "ntp", n: "שרת זמן \(NTP\)", pt: "UDP 123"/);
  assert.match(nic, /if \(s\.k === "ntp"\) return "123\/udp"/);
});
