// ‏#2870 (סעיפים 1 ו-4) ו-#2921 (3, 6): מוניטור צר לא חותך כפתורים, הודעת כשל בהגדרות עוטפת, מוקד עם outline, canvas עם שם.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../server/static');
const css = fs.readFileSync(path.join(root, 'console.css'), 'utf8');
const mon = fs.readFileSync(path.join(root, 'monitor.html'), 'utf8');

test('#2870.1: the monitor bar wraps in a narrow iframe instead of clipping the reconnect button', () => {
  const m = mon.match(/\.mon-bar \{([^}]*)\}/);
  assert.ok(m);
  assert.match(m[1], /flex-wrap: wrap/); assert.match(m[1], /min-height: var\(--header-height\)/); assert.doesNotMatch(m[1], /[^-]height: var\(--header-height\)/);
});

test('#2870.4: a long update-check message wraps inside its own settings column', () => {
  assert.match(css, /\.page\.settings dl\.mkv\{grid-template-columns:minmax\(0,1fr\) minmax\(0,max-content\)\}/);
  assert.match(css, /\.page\.settings dl\.mkv dd\{[^}]*max-width:min\(560px,65vw\)/);
  assert.match(css, /\.page\.settings dl\.mkv \.sv\{[^}]*overflow-wrap:anywhere/);
});

test('#2921: focus shows an outline (not only a border colour), and the monitor canvas has a name', () => {
  assert.match(css, /#login \.otp input:focus\{outline:2px solid/);
  assert.match(css, /\.win \.n-in:focus[^{]*\{outline:2px solid/);
  assert.match(mon, /<canvas id="screen"[^>]*aria-label="/);
});
