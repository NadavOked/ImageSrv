// ‏#2874 (הכרעת נדב 14/11, ב): בסבב פעיל אחרי ה-idle הקונסולה עוברת לצפייה בלבד ולא מתנתקת.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const src = consoleJs();
const css = fs.readFileSync(path.resolve(__dirname, '../server/static/console.css'), 'utf8');

test('#2874: with an active round the idle timer sets view-only and does not log out; without one it logs out as before', () => {
  const i = src.indexOf('function startIdleWatch('), body = src.slice(i, src.indexOf('\n}\n', i));
  const viewOnly = body.indexOf('if (left <= 0 && activeRoundNow())'), logout = body.indexOf('await post("/logout")');
  assert.ok(viewOnly > 0 && logout > viewOnly, 'the view-only branch comes before the logout');
  assert.match(body, /setViewOnly\(true\);\s*return;/, 'it returns: no logout while the round runs');
  assert.match(body, /if \(VIEW_ONLY\) setViewOnly\(false\)/, 'when the round ends the normal idle logout applies');
});

test('#2874: the banner offers re-login, every other action is stopped in view-only, navigation is not', () => {
  assert.match(src, /id="viewOnlyLogin">כניסה מחדש</);
  assert.match(src, /צפייה בלבד\.<\/b> לא הייתה פעילות והסבב עדיין רץ/);
  const i = src.indexOf('document.addEventListener("click", (e) => {\n  if (!VIEW_ONLY'), blk = src.slice(i, src.indexOf('}, true);', i));
  assert.match(blk, /e\.preventDefault\(\); e\.stopPropagation\(\)/);
  for (const keep of ['.inventory-node', '.tab', '#viewOnlyBar', '.user-menu']) assert.ok(blk.includes(keep), keep + ' stays usable');
  assert.match(src, /async function viewOnlyReenter\(\)[\s\S]*post\("\/logout"\)[\s\S]*showLogin\(\)/);
});

test('#2874: the banner is a blue info note, not the orange reserved for disks', () => {
  const m = css.match(/\.view-only-bar\{([^}]*)\}/);
  assert.ok(m);
  assert.match(m[1], /--clr-info-soft/); assert.doesNotMatch(m[1], /warning|warn/);
});
