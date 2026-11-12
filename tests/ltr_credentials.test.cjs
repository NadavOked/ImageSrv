// שדות שם משתמש / סיסמה / קוד תמיד באנגלית: dir="ltr" (טקסט וסמן משמאל),
// התוויות נשארות עברית RTL, וכפתור "הצג" יושב בצד ימין פיזי בתוך השדה
// עם padding-right כך שאינו מכסה את מה שהוקלד. בדיקת מקור סטטית.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('path');
const root = path.resolve(__dirname, '../server/static');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');
const js = consoleJs();
const css = read('console.css');

function inputTag(src, id) {
  const m = src.match(new RegExp('<input[^>]*id="' + id + '"[^>]*>'));
  assert.ok(m, 'input #' + id + ' exists');
  return m[0];
}

test('login-screen credential inputs are dir="ltr"', () => {
  for (const id of ['login-user', 'login-pass', 'pw-change-current', 'pw-change-new',
    'pw-change-confirm', 'mfa-backup', 'setup-secret', 'setup-code']) {
    assert.match(inputTag(js, id), /dir="ltr"/, id);
  }
  assert.match(js, /<input dir="ltr" inputmode="numeric" maxlength="1"/, 'OTP cells');
});

test('unified password component input is dir="ltr"', () => {
  assert.match(js, /type="password" dir="ltr" id="\$\{id\}"/);
});

test('sheet username and code fields are ltr', () => {
  assert.match(js, /id: "username", label: "שם משתמש", dir: "ltr"/);
  assert.equal((js.match(/id: "code", label: "הקוד מהאפליקציה", dir: "ltr"/g) || []).length, 2);
  // 16/10 (מוקאפ #1525): קוד הצימוד הוא שמונה תיבות — בתוך מיכל ltr
  assert.match(read('branches.js'), /<div class="enroll-cin" dir="ltr">/);
  assert.match(read('storage.js'), /type === "password" \? ' dir="ltr"'/);
});

test('show toggle is on the physical right with right padding on the input', () => {
  assert.match(css, /#login \.f \.eye,#login \.pw-eye\{[^}]*right:5px;left:auto/);
  assert.match(css, /#login \.f\.pw input\{padding-right:64px/);
  assert.match(css, /\n\.pw-eye\{position:absolute;right:5px;left:auto;top:50%;transform:translateY\(-50%\)/);
  assert.match(css, /\.pw-wrap input\{padding-right:68px/);
  assert.match(css, /\.win \.pw-wrap \.n-in\{padding-right:72px/);
  assert.doesNotMatch(css, /pw-eye[^}]*inset-inline-end/, 'no logical end offset on the toggle');
});

test('one "הצג" style: plain action-colour text, no border, soft hover; no second .win variant', () => {
  assert.match(css, /\n\.pw-eye\{[^}]*border:0;background:transparent;[^}]*color:var\(--clr-action-fg\)/);
  assert.match(css, /\n\.pw-eye:hover\{background:var\(--clr-action-soft\)\}/);
  assert.doesNotMatch(css, /\.win \.pw-eye/);
});

test('sheet policy list uses the login rules markup: check icon in a circle, no text glyphs', () => {
  assert.match(js, /<li data-rule="\$\{i\}">\$\{LOGIN_CHECK_ICON\}/);
  assert.doesNotMatch(js, /class="pm"/);
  assert.match(css, /\.pw-pol li\.ok i\{border-color:var\(--clr-success\);background:var\(--clr-success\)/);
});

test('new-user and user-password sheets carry a confirm field', () => {
  assert.match(js, /type: "password", confirm: "אימות הסיסמה החדשה"/);
  assert.match(js, /type: "password",\s+confirm: "אימות סיסמה"/);
});
