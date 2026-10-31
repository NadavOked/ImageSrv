// #1085 שלב ב': חמשת מסכי הכניסה מ-JSON קבוע + כללי נוהל חיים + 6 תאים +
// "המשך" נעול עד ☐. בלי דפדפן — console.js ב-vm.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function setup() {
  const nodes = new Map();
  function node(key) {
    if (!nodes.has(key)) nodes.set(key, {
      value: '', innerHTML: '', textContent: '', title: '', dataset: {},
      style: {removeProperty() {}},
      classList: {add() {}, remove() {}, toggle() {}},
      addEventListener() {}, removeEventListener() {},
      querySelector() { return null; }, querySelectorAll() { return []; },
      insertAdjacentHTML(_p, html) { this.innerHTML += html; },
      setAttribute() {}, removeAttribute() {}, focus() {},
    });
    return nodes.get(key);
  }
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent, String,
    document: {
      hidden: false, querySelector: node, querySelectorAll: () => [],
      getElementById: (id) => node('#' + id),
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); }, get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}, removeAttribute() {}},
      addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false})},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    location: {hostname: 'imagesrv-srv'},
    navigator: {clipboard: {writeText: async () => {}}},
    CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 401, ok: false, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  // codeql[js/hardcoded-data-interpreted-as-code] false positive: the test harness runs console.js expressions with visible JSON fixtures in a vm sandbox (#291)
  const run = (s) => vm.runInContext(s, ctx);
  return {ctx, run, node};
}

const FIX = {
  login: {screen: 'login'},
  error401: {status: 401, body: {detail: 'שם משתמש או סיסמה שגויים'}},
  error403: {status: 403, body: {error: 'deploy_no_console', message_he: 'משתמש הפצה עובד ממחשב הבנייה, לא מהקונסולה'}},
  error429: {status: 429, body: {detail: 'נסה שוב בעוד 900 שניות'}, retryAfter: 900},
  change: {must_change_password: true},
  mfa: {mfa_required: true, challenge: 'chal-1'},
  enroll: {mfa_enrollment_required: true},
  ok: {username: 'nadav', role: 'admin', idle_seconds: 300},
};

test('1085: five screens from fixed JSON — login, error, change, mfa, setup/codes', () => {
  const {run} = setup();
  const login = run('loginHtml(' + JSON.stringify(FIX.login) + ')');
  assert.match(login, /<h1 class="title">כניסה<\/h1>/);
  assert.match(login, /<label for="login-user">שם משתמש<\/label>/);
  assert.match(login, />הצג</);
  assert.doesNotMatch(login, /גישה מאובטחת|זכור אותי|הזן את פרטי|קונסולת ניהול/);

  const st401 = run('loginStateFromResponse(401, ' + JSON.stringify(FIX.error401.body) + ', null, {username:"nadav"})');
  assert.equal(st401.screen, 'login');
  assert.equal(st401.error, 'שם משתמש או סיסמה שגויים');
  assert.equal(st401.username, 'nadav');
  const err = run('loginHtml(' + JSON.stringify({screen: 'login', username: 'nadav', error: st401.error, fieldErr: true}) + ')');
  assert.match(err, /שם משתמש או סיסמה שגויים/);
  assert.match(err, /value="nadav"/);
  assert.match(err, /class="f err"/);

  const st403 = run('loginStateFromResponse(403, ' + JSON.stringify(FIX.error403.body) + ', null, {username:"labtech"})');
  assert.equal(st403.error, 'משתמש הפצה עובד ממחשב הבנייה, לא מהקונסולה');

  const st429 = run('loginStateFromResponse(429, ' + JSON.stringify(FIX.error429.body) + ', 900, {username:"nadav"})');
  assert.equal(st429.error, 'החשבון נעול ל-15 דקות');

  const stCh = run('loginStateFromResponse(200, ' + JSON.stringify(FIX.change) + ', null, {username:"admin"})');
  assert.equal(stCh.screen, 'change');
  const ch = run('loginHtml(' + JSON.stringify({screen: 'change', username: 'admin'}) + ')');
  assert.match(ch, /בחר סיסמה חדשה/);
  assert.match(ch, /לפחות 8 תווים/);
  assert.match(ch, /אותיות/);
  assert.match(ch, /ספרות/);
  assert.match(ch, /תו מיוחד/);
  assert.match(ch, /id="pw-change-go" disabled/);

  const stMfa = run('loginStateFromResponse(200, ' + JSON.stringify(FIX.mfa) + ', null, {username:"nadav"})');
  assert.equal(stMfa.screen, 'mfa');
  assert.equal(stMfa.challenge, 'chal-1');
  const mfa = run('loginHtml(' + JSON.stringify({screen: 'mfa', username: 'nadav', otp: ['', '', '', '', '', '']}) + ')');
  assert.match(mfa, /הקוד מאפליקציית האימות/);
  assert.equal((mfa.match(/data-otp="/g) || []).length, 6);
  assert.match(mfa, /השתמש בקוד גיבוי/);

  const stEn = run('loginStateFromResponse(200, ' + JSON.stringify(FIX.enroll) + ', null, {username:"nadav"})');
  assert.equal(stEn.screen, 'setup');
  const setupHtml = run('loginHtml(' + JSON.stringify({screen: 'setup', username: 'nadav', secret: 'JBSWY3DPEHPK3PXP', otpauth: 'otpauth://totp/ImageSrv:nadav?secret=JBSWY3DPEHPK3PXP'}) + ')');
  assert.match(setupHtml, /הגדרת אימות דו-שלבי/);
  // #1177: the otpauth URI is never shown; the manual secret sits behind "הצג"
  assert.doesNotMatch(setupHtml, /otpauth:\/\//);
  assert.match(setupHtml, /JBSW Y3DP EHPK 3PXP/);
  assert.match(setupHtml, /id="setup-secret" class="mono" type="password"/);
  assert.match(setupHtml, /data-pw="setup-secret"/);

  const codes = run('loginHtml(' + JSON.stringify({screen: 'codes', backupCodes: ['AAAA', 'BBBB'], savedAck: false}) + ')');
  assert.match(codes, /המשך לקונסולה/);
  assert.match(codes, /שמרתי את הקודים במקום בטוח/);
  assert.match(codes, /id="setup-done" disabled/);
  assert.match(codes, /העתק/);
  assert.match(codes, /הדפס/);

  const app = run('loginStateFromResponse(200, ' + JSON.stringify(FIX.ok) + ', null, {})');
  assert.equal(app.screen, 'app');
});

test('1085: live password policy — button lights only when all four checks and confirm match', () => {
  const {run} = setup();
  const asPlain = (v) => JSON.parse(JSON.stringify(v)); // run() חוצה realm של vm — deepEqual דורש עצם רגיל
  assert.deepEqual(asPlain(run('passwordPolicy("Aa12345!")')), {len: true, alpha: true, digit: true, special: true, all: true});
  assert.equal(run('passwordPolicy("Aa123456").special'), false);
  assert.equal(run('passwordPolicy("aa12345!").all'), true, 'lowercase letters still count');
  assert.equal(run('passwordPolicy("12345678").alpha'), false);
  assert.equal(run('passwordCanSubmit("old", "Aa12345!", "Aa12345!")'), true);
  assert.equal(run('passwordCanSubmit("old", "Aa123456", "Aa123456")'), false, 'no special → button stays off');
  assert.equal(run('passwordCanSubmit("", "Aa12345!", "Aa12345!")'), false, 'no current password');
  assert.equal(run('passwordCanSubmit("old", "Aa12345!", "Aa12345?")'), false, 'mismatch');
  const lit = run('loginHtml(' + JSON.stringify({screen: 'change', current: 'old', next: 'Aa12345!', confirm: 'Aa12345!'}) + ')');
  assert.doesNotMatch(lit, /id="pw-change-go" disabled/);
  assert.match(lit, /data-rule="len" class="ok"/);
  const dark = run('loginHtml(' + JSON.stringify({screen: 'change', current: 'old', next: 'short', confirm: 'short'}) + ')');
  assert.match(dark, /id="pw-change-go" disabled/);
});

test('1085 negative: lighting the button without all four checks must fail this test', () => {
  const {run} = setup();
  // בקרה שלילית: אם passwordCanSubmit יחזיר true בלי ✓ — הטסט נופל.
  assert.equal(run('passwordCanSubmit("x", "abcdefgh", "abcdefgh")'), false);
  assert.equal(run('passwordCanSubmit("x", "abcd1234", "abcd1234")'), false);
});

test('1085: six OTP cells — paste of 6 digits fills all; backspace helper is otpFromPaste', () => {
  const {run} = setup();
  const asPlain = (v) => JSON.parse(JSON.stringify(v)); // run() חוצה realm של vm — deepEqual דורש עצם רגיל
  assert.deepEqual(asPlain(run('otpFromPaste("123456")')), ['1', '2', '3', '4', '5', '6']);
  assert.deepEqual(asPlain(run('otpFromPaste("12 34-56")')), ['1', '2', '3', '4', '5', '6']);
  assert.deepEqual(asPlain(run('otpFromPaste("12")')), ['1', '2', '', '', '', '']);
  const mfa = run('loginHtml(' + JSON.stringify({screen: 'mfa', username: 'nadav', otp: ['1', '2', '3', '4', '5', '6']}) + ')');
  assert.match(mfa, /value="1"/);
  assert.match(mfa, /value="6"/);
  const backup = run('loginHtml(' + JSON.stringify({screen: 'mfa', username: 'nadav', otpBackup: true, backupCode: 'ABCD1234'}) + ')');
  assert.match(backup, /id="mfa-backup"/);
  assert.doesNotMatch(backup, /id="otp"/);
});

test('1085: continue stays locked until the saved-codes checkbox', () => {
  const {run} = setup();
  assert.equal(run('setupContinueEnabled(false)'), false);
  assert.equal(run('setupContinueEnabled(true)'), true);
  const off = run('loginHtml(' + JSON.stringify({screen: 'codes', backupCodes: ['A'], savedAck: false}) + ')');
  assert.match(off, /id="setup-done" disabled/);
  const on = run('loginHtml(' + JSON.stringify({screen: 'codes', backupCodes: ['A'], savedAck: true}) + ')');
  assert.doesNotMatch(on, /id="setup-done" disabled/);
});

test('1085 negative: continue without checkbox must fail this test', () => {
  const {run} = setup();
  assert.equal(run('setupContinueEnabled(0)'), false);
  assert.equal(run('setupContinueEnabled("")'), false);
});

test('1085: MFA column and my-user labels', () => {
  const {run} = setup();
  assert.equal(run('userMfaLabel({is_builtin:true})'), 'מקומי');
  assert.equal(run('userMfaLabel({mfa_enabled:true})'), '✓');
  assert.equal(run('userMfaLabel({})'), '—');
  run('globalThis.drawn=""; openDrawer=(t,b)=>{ drawn=t+"|"+b; }; closeUserMenu=()=>{};');
  run('ME={username:"nadav",role:"admin",is_builtin:false,mfa_enabled:true}');
  run('openAccount()');
  const html = run('drawn');
  assert.match(html, /המשתמש שלי/);
  assert.match(html, /צור קודי גיבוי חדשים/);
  run('ME.is_builtin=true; ME.mfa_enabled=false; openAccount()');
  assert.match(run('drawn'), /מקומי · ללא MFA/);
  run('ME.is_builtin=false; ME.mfa_enabled=false; openAccount()');
  assert.match(run('drawn'), />הפעל</);
});

test('1085: 401/403/429 mapping does not drop the typed username', () => {
  const {run} = setup();
  const prev = {username: 'nadav'};
  for (const [status, body, ra] of [
    [401, {}, null],
    [403, {error: 'deploy_no_console', message_he: 'משתמש הפצה עובד ממחשב הבנייה, לא מהקונסולה'}, null],
    [429, {}, 60],
  ]) {
    const st = run(`loginStateFromResponse(${status}, ${JSON.stringify(body)}, ${ra === null ? 'null' : ra}, ${JSON.stringify(prev)})`);
    assert.equal(st.username, 'nadav', String(status));
    assert.equal(st.screen, 'login');
  }
});

test('the login screens have no "remember this browser" checkbox', async () => {
  const {run, node} = setup();
  const login = run('loginHtml({screen: "login"})');
  assert.doesNotMatch(login, /login-remember|mfa-remember|7 שעות/);
  const mfa = run('loginHtml(' + JSON.stringify({screen: 'mfa', username: 'nadav', otp: ['', '', '', '', '', '']}) + ')');
  assert.doesNotMatch(mfa, /mfa-remember|7 שעות|זכור את הדפדפן/);
  const sent = [];
  run('globalThis.__sent = []; loginFetch = async (p, b) => { __sent.push([p, b]); return {ok: false, status: 401, body: {}, retryAfter: null}; };');
  node('#login-user').value = 'nadav';
  node('#login-pass').value = 'Aa12345!';
  await run('loginSubmitCredentials()');
  sent.push(...JSON.parse(run('JSON.stringify(__sent)')));
  assert.deepEqual(sent, [['/login', {username: 'nadav', password: 'Aa12345!'}]]);
});

test('1120: wrong MFA code returns to the login screen with the server message and no challenge', () => {
  const {run} = setup();
  const prev = {screen: 'mfa', username: 'nadav', challenge: 'chal-1', otp: ['1', '2', '3', '4', '5', '6']};
  const st = run('mfaRejectedState(' + JSON.stringify({detail: 'קוד שגוי — היכנס מחדש'}) + ', ' + JSON.stringify(prev) + ')');
  assert.equal(st.screen, 'login');
  assert.equal(st.username, 'nadav');
  assert.equal(st.challenge, '');
  assert.equal(st.error, 'קוד שגוי — היכנס מחדש');
  const html = run('loginHtml(' + JSON.stringify(st) + ')');
  assert.match(html, /היכנס מחדש/);
  assert.equal((html.match(/data-otp="/g) || []).length, 0);
  const fallback = run('mfaRejectedState({}, {username: "x"})');
  assert.equal(fallback.error, 'קוד שגוי — היכנס מחדש');
});

test('stage 2: account - the built-in admin has no MFA button and no backup-codes card; a regular admin gets backup codes only while MFA is on', () => {
  const {run} = setup();
  run('globalThis.drawn=""; openDrawer=(t,b)=>{ drawn=b; }; closeUserMenu=()=>{};');
  const open = me => { run(`ME=${JSON.stringify(me)}; openAccount()`); return run('drawn'); };
  const builtin = open({username: 'admin', role: 'admin', is_builtin: true, mfa_enabled: false});
  assert.match(builtin, /<span class="pill ">מקומי · ללא MFA<\/span>/);
  assert.match(builtin, /בכוונה, כדי שתמיד תהיה דרך להיכנס/, 'one explanatory line');
  assert.doesNotMatch(builtin, /<button(?![^>]*accountChangePassword)/, 'no MFA button, no backup-codes button (the password button is #1268)');
  assert.doesNotMatch(builtin, /קודי גיבוי|accountEnableMfa|accountNewBackupCodes/);
  const on = open({username: 'nadav', role: 'admin', is_builtin: false, mfa_enabled: true});
  assert.match(on, /<span>קודי גיבוי<\/span>/); assert.match(on, /accountNewBackupCodes\(\)">צור קודי גיבוי חדשים/);
  assert.match(on, /<span class="st ok">פעיל<\/span>/); assert.doesNotMatch(on, /accountEnableMfa/);
  const off = open({username: 'nadav', role: 'admin', is_builtin: false, mfa_enabled: false});
  assert.match(off, /accountEnableMfa\(\)">הפעל/); assert.doesNotMatch(off, /קודי גיבוי|accountNewBackupCodes/, 'backup codes are gated on mfa_enabled');
  const deploy = open({username: 'lab', role: 'deploy', is_builtin: false, mfa_enabled: false});
  assert.doesNotMatch(deploy, /accountEnableMfa|accountNewBackupCodes/, 'a deploy user has nothing to enable here');
  // ‏#1268: "שנה סיסמה" — לכל אחד על עצמו, גם ל-builtin (דלת חירום שדלפה = מחליפים סיסמה).
  for (const html of [builtin, on, off, deploy]) {
    assert.match(html, /<span>סיסמה<\/span>/);
    assert.match(html, /<button class="btn primary sm" onclick="accountChangePassword\(\)">שנה סיסמה<\/button>/);
  }
  assert.equal((builtin.match(/<button/g) || []).length, 1, 'the built-in admin: the password button only');
});

test('final mockup: "החשבון שלי" is a page in the tree (ניהול), same cards as the drawer, two per row, no "חשבון" card (the header carries it)', () => {
  const {run} = setup();
  assert.match(run('pages.account.title'), /החשבון שלי/);
  run('ME={username:"nadav",role:"admin",is_builtin:false,mfa_enabled:true}');
  const html = run('account()');
  assert.match(html, /<div class="obj-name">החשבון שלי<span class="pill info">מנהל<\/span><\/div>/);
  assert.match(html, /class="obj-sub"[^>]*>nadav</);
  assert.match(html, /openSessionInfo\(\)">פרטי החיבור/);
  assert.equal((html.match(/c6 card/g) || []).length, 4, 'password, MFA, backup codes and "מראה" are half-width cards');
  // סידור ההגדרות (נדב 17/10): "מראה" הוא העדפה של המשתמש — עבר מההגדרות לכאן.
  assert.match(html, /<span>מראה<\/span>[\s\S]*setThemePref\('dark'\)/);
  assert.doesNotMatch(html, /<span>חשבון<\/span>/);
  assert.match(html, /accountChangePassword\(\)">שנה סיסמה/);
  run('ME.is_builtin=true; ME.mfa_enabled=false');
  assert.doesNotMatch(run('account()'), /accountEnableMfa|accountNewBackupCodes/, 'the built-in admin keeps no MFA controls');
});

test('#1268: "שנה סיסמה" in my account — current + new + confirm, POST /me/password with the current one; a weak or mismatched password never leaves the browser', async () => {
  const {run, ctx} = setup();
  const calls = [];
  let answer = {status: 200, body: {ok: true}};
  ctx.fetch = async (url, o = {}) => { calls.push({url, method: o.method, body: o.body ? JSON.parse(o.body) : null}); return {status: answer.status, ok: answer.status < 300, headers: {get: () => null}, json: async () => answer.body}; };
  run('ME={username:"nadav",role:"admin",is_builtin:false,mfa_enabled:true}; globalThis.sheets=[]; sheet=(o)=>sheets.push(o); globalThis.toasts=[]; toast=(m)=>toasts.push(m); closeDrawer=()=>{};');
  run('accountChangePassword()');
  const s = run('sheets.at(-1)');
  assert.equal(s.title, 'שינוי סיסמה'); assert.equal(s.sub, 'nadav');
  assert.deepEqual(JSON.parse(JSON.stringify(s.fields.map((f) => [f.id, f.type, f.label, f.confirm || '', !!f.policy, f.autocomplete || '']))), [
    ['current', 'password', 'סיסמה נוכחית', '', false, 'current-password'],
    ['next', 'password', 'סיסמה חדשה', 'אימות הסיסמה החדשה', true, 'new-password']]);
  await assert.rejects(s.onSubmit({current: 'Old-pass-1!', next: 'short'}), /אינה עומדת במדיניות/);
  assert.equal(calls.length, 0, 'the policy is checked before anything is sent');
  await s.onSubmit({current: 'Old-pass-1!', next: 'New-pass-2!'});
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [{url: '/api/console/me/password', method: 'POST', body: {current_password: 'Old-pass-1!', new_password: 'New-pass-2!'}}]);
  assert.match(run('toasts.at(-1)'), /הסיסמה הוחלפה/);
  // הנוכחית שגויה — הסירוב של השרת בחלון, כלשונו.
  answer = {status: 400, body: {detail: 'הסיסמה הנוכחית שגויה'}};
  await assert.rejects(s.onSubmit({current: 'x', next: 'New-pass-2!'}), /הסיסמה הנוכחית שגויה/);
  // ‏200 בלי ok — לא "הוחלפה" (עיקרון 5).
  answer = {status: 200, body: {}};
  await assert.rejects(s.onSubmit({current: 'Old-pass-1!', next: 'New-pass-2!'}), /לא אישר/);
  // מנהל בלי MFA: השרת מחזיר עוגיית הגדרת-MFA בלבד — עוברים למסך ההגדרה, לא נשארים בקונסולה שתסרב.
  answer = {status: 200, body: {ok: true, mfa_enrollment_required: true}};
  run('globalThis.flow=[]; showLogin=()=>flow.push("login"); loginStartSetup=(b)=>{ flow.push("setup:"+b.username); };');
  await s.onSubmit({current: 'Old-pass-1!', next: 'New-pass-2!'});
  assert.equal(run('flow.join(",")'), 'login,setup:nadav');
});
