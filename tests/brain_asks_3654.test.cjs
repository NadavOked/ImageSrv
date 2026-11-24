// #3654 (K14) -- שאלות המוח למשתמש בקונסולה: שורות בפעמון הקיים, ו-sheet() הקיים לתשובה. brain_asks.js האמיתי רץ ב-vm
// עם התלויות שלו מ-console.js כפונקציות מזעריות.
//
// מה נבדק: שאלה פתוחה היא שורה עם כפתור "ענה" ונספרת; מה שפקע מוצג "פקעה בלי תשובה" ולא כתשובה ואינו נספר; ברירת המחדל
// הבטוחה מסומנת מראש ואפשרות הרסנית לעולם לא; הכפתור אדום רק כשבחרו בה; התשובה נשלחת כאפשרות מהרשימה בלבד (בלי הקלדת
// מילה); 409 נאמר ואינו נבלע; כשל קריאה אינו הופך ל"אין שאלות"; ושרת משנה / לא-מנהל לא קוראים.
//
// בקרה שלילית: ראו את גוף ה-PR.
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.resolve(__dirname, '../server/static/brain_asks.js'), 'utf8');

const WIPE = {id: 'wipe', label_he: 'למחוק את המידע הישן', destructive: true, verb_he: 'מחיקת המידע',
  consequence_he: 'המידע הישן יימחק ולא יהיה אפשר לשחזר אותו', outcome_he: ''};
const SKIP = {id: 'skip', label_he: 'לדלג ולהשאיר את הדיסק', destructive: false, verb_he: '', consequence_he: '', outcome_he: 'הדיסק לא ייכתב'};
const OPEN = {id: 7, title_he: 'נמצא מידע RAID ישן על הדיסק', detail_he: '', state: 'open', options: [WIPE, SKIP],
  safe_default: 'skip', safe_default_he: SKIP.label_he, expires_in_s: 600, ttl_s: 1800, columns: 1, entity: {kind: 'machine', key: 'aa:bb:cc:00:00:01'}};
const EXPIRED = {...OPEN, id: 8, state: 'expired', expires_in_s: null, answer: null, answer_he: null};
const ANSWERED = {...OPEN, id: 9, state: 'answered', expires_in_s: null, answer: 'wipe', answer_he: WIPE.label_he};

function load({admin = true, serverCtx = null, apiResult = {open: [OPEN], recent: [EXPIRED, ANSWERED]}, postFails = null} = {}) {
  const calls = {sheets: [], posts: [], toasts: [], apis: [], badge: 0};
  const radios = [];
  const submit = {classes: new Set(['primary']), textContent: 'אישור', classList: {toggle(c, on) { on ? submit.classes.add(c) : submit.classes.delete(c); }}};
  let picked = null;
  const ctx = vm.createContext({
    Date, JSON, Number, String, Math, Array, Set, console,
    isAdmin: () => admin, SERVER_CTX: serverCtx, MACHINES: [{mac: 'aa:bb:cc:00:00:01', display_name: 'משכפל-1'}], closeModal: () => { calls.closed = (calls.closed || 0) + 1; },
    machineName: (m) => m.display_name, fmtEta: (s) => `~${Math.round(s / 60)} דק'`,
    esc: (s) => String(s ?? ''), UI: {pill: (cls, text) => `[${cls}:${text}]`},
    impactList: (items) => items.map(([k, h]) => `<${k}>${h}`).join(''),
    toast: (...a) => calls.toasts.push(a), updateAlertBadge: () => { calls.badge++; },
    api: async (p) => { calls.apis.push(p); if (apiResult instanceof Error) throw apiResult; return apiResult; },
    post: async (p, b) => { calls.posts.push([p, b]); if (postFails) throw postFails; return {}; },
    sheet: (spec) => { calls.sheets.push(spec); },
    document: {
      getElementById: (id) => (id === 'sf-submit' ? submit : {textContent: 'התראות'}),
      querySelector: () => (picked ? {value: picked} : null),
      querySelectorAll: (sel) => (/name="ba"\]$/.test(sel) ? radios : []),
    },
  });
  vm.runInContext(SRC, ctx);
  return {ctx, calls, radios, submit, pick: (v) => { picked = v; radios.forEach((r) => r.handler && r.handler()); }};
}
const plain = (x) => JSON.parse(JSON.stringify(x));   // מערכים מה-vm אינם מאותו realm
const set = (h, value) => vm.runInContext(`BRAIN_ASKS = ${JSON.stringify(value)}; BRAIN_ASKS_OK_AT = Date.now()`, h.ctx);
const rows = (h) => vm.runInContext('brainAskRows()', h.ctx);
const count = (h) => vm.runInContext('brainAskCount()', h.ctx);

test('the bell has a section of the open questions: title, who, a countdown, an answer button; only open ones are counted', () => {
  const h = load();
  set(h, {open: [OPEN], recent: [EXPIRED, ANSWERED]});
  const html = rows(h);
  assert.match(html, /שאלות מהמוח · 1/);
  assert.match(html, /נמצא מידע RAID ישן על הדיסק/);
  assert.match(html, /onclick="brainAskSheet\(7\)"/);
  assert.match(html, /משכפל-1/);
  assert.match(html, /data-cd="7"/);
  assert.equal(count(h), 1);
});

test('what closed is not in the bell (it is in the history), so an expiry is never shown beside the open ones as if it were an answer', () => {
  const h = load();
  set(h, {open: [], recent: [EXPIRED, ANSWERED]});
  assert.equal(rows(h), '');
  assert.equal(count(h), 0);
});

test('before anything was read there are no rows and no count, which is not the same as "no questions"', () => {
  const h = load();
  assert.equal(rows(h), '');
  assert.equal(count(h), 0);
  assert.equal(vm.runInContext('BRAIN_ASKS', h.ctx), null);
});

test('the answer window preselects the safe default, never a destructive option, and names the verb, the cost and the clock', () => {
  const h = load();
  set(h, {open: [OPEN], recent: []});
  vm.runInContext('brainAskSheet(7)', h.ctx);
  const [spec] = h.calls.sheets;
  assert.equal(spec.title, OPEN.title_he);
  assert.equal(spec.submitLabel, 'אישור');
  assert.equal(spec.hint, 'אפשר לענות גם במסך של המכונה');
  const [field] = spec.fields;
  assert.equal(field.type, 'html');
  assert.match(field.html, /value="skip" checked/);
  assert.doesNotMatch(field.html, /value="wipe" checked/);
  assert.match(field.html, /<small>פעולה הרסנית — לא תתבצע בלי התשובה הזו<\/small>/);
  assert.match(field.html, /<small>ברירת המחדל כשאין תשובה<\/small>/);
  assert.doesNotMatch(field.html, /יימחק ולא יהיה אפשר לשחזר/, 'the cost is shown only when the destructive option is picked');
  assert.match(field.html, /רק תשובה שלך מבצעת אותה/);
  assert.match(spec.note, /בלי תשובה תבוצע ברירת המחדל בעוד/);
  assert.match(spec.note, /«לדלג ולהשאיר את הדיסק»<\/b>\. הדיסק לא ייכתב/);
});

test('the answer window says "soon" when under a minute is left', () => {
  const h = load();
  set(h, {open: [{...OPEN, expires_in_s: 12}], recent: []});
  vm.runInContext('brainAskSheet(7)', h.ctx);
  assert.match(h.calls.sheets[0].note, /ba-cd low/);
  assert.match(h.calls.sheets[0].note, /עוד רגע: בלי תשובה/);
  assert.match(h.calls.sheets[0].note, /00:12/);
});

test('picking the destructive option opens its cost and turns the button red with its verb; picking the default turns it back to "אישור"', () => {
  const h = load();
  set(h, {open: [OPEN], recent: []});
  h.radios.push({handler: null, addEventListener(_, fn) { this.handler = fn; }});
  vm.runInContext('brainAskSheet(7)', h.ctx);
  assert.ok(h.submit.classes.has('primary') && !h.submit.classes.has('danger'));
  assert.equal(h.submit.textContent, 'אישור');
  h.pick('wipe');
  assert.ok(h.submit.classes.has('danger') && !h.submit.classes.has('primary'));
  assert.equal(h.submit.textContent, 'מחיקת המידע');
  h.pick('skip');
  assert.ok(h.submit.classes.has('primary') && !h.submit.classes.has('danger'));
  assert.equal(h.submit.textContent, 'אישור');
});

test('submitting posts the chosen option from the list to this question only, with no typed word', async () => {
  const h = load();
  set(h, {open: [OPEN], recent: []});
  vm.runInContext('brainAskSheet(7)', h.ctx);
  h.pick('wipe');
  await h.calls.sheets[0].onSubmit({});
  assert.deepEqual(plain(h.calls.posts), [['/brain/asks/7/answer', {option: 'wipe'}]]);
  assert.equal(h.calls.toasts[0][0], 'התשובה נרשמה');
  assert.ok(h.calls.apis.includes('/brain/asks'), 'the list is read again after the answer');
});

test('an answer the server refused (someone answered first, or it expired) is said, and the list is read again', async () => {
  const refused = Object.assign(new Error('השאלה כבר נענתה'), {status: 409});
  const h = load({postFails: refused});
  set(h, {open: [OPEN], recent: []});
  vm.runInContext('brainAskSheet(7)', h.ctx);
  h.pick('skip');
  await assert.rejects(h.calls.sheets[0].onSubmit({}), /השאלה כבר נענתה/);
  assert.ok(h.calls.apis.includes('/brain/asks'));
  assert.equal(h.calls.toasts.length, 0, 'no "recorded" toast for an answer that was not');
});

test('a question that closed meanwhile is not offered: the user is told and the list is read again', () => {
  const h = load();
  set(h, {open: [], recent: []});
  vm.runInContext('brainAskSheet(7)', h.ctx);
  assert.equal(h.calls.sheets.length, 0);
  assert.equal(h.calls.toasts[0][0], 'השאלה כבר נסגרה');
});

test('an open answer window is closed, with what happened, when its question expires or is answered elsewhere', async () => {
  const h = load({apiResult: {open: [], recent: [EXPIRED]}});
  set(h, {open: [{...OPEN, id: 8}], recent: []});
  vm.runInContext('brainAskSheet(8)', h.ctx);
  await vm.runInContext('loadBrainAsks()', h.ctx);
  assert.equal(h.calls.closed, 1);
  assert.match(h.calls.toasts.at(-1)[0], /^תם הזמן — בוצעה ברירת המחדל: «לדלג ולהשאיר את הדיסק»$/);
});

test('a failed read keeps what was read before; a good read replaces it and updates the bell', async () => {
  const bad = load({apiResult: new Error('שגיאה 500')});
  set(bad, {open: [OPEN], recent: []});
  await vm.runInContext('loadBrainAsks()', bad.ctx);
  assert.equal(count(bad), 1, 'a failure is not "no questions"');
  const good = load();
  await vm.runInContext('loadBrainAsks()', good.ctx);
  assert.equal(count(good), 1);
  assert.equal(good.calls.badge, 1);
});

test('only an admin on the primary server reads the questions', async () => {
  for (const opts of [{admin: false}, {serverCtx: 'srv_2'}]) {
    const h = load(opts);
    await vm.runInContext('loadBrainAsks()', h.ctx);
    vm.runInContext('brainAsksTick()', h.ctx);
    assert.deepEqual(h.calls.apis, [], JSON.stringify(opts));
  }
});

test('the questions are read at most every ten seconds', async () => {
  const h = load();
  await vm.runInContext('loadBrainAsks()', h.ctx);
  vm.runInContext('brainAsksTick()', h.ctx);
  vm.runInContext('brainAsksTick()', h.ctx);
  assert.equal(h.calls.apis.length, 1);
});

test('the module never opens a browser dialog', () => {
  assert.doesNotMatch(SRC, /\b(prompt|confirm|alert)\(/);
});

test('the console loads it after console.js, on the same version, and wires the bell and the poll to it', () => {
  const html = fs.readFileSync(path.resolve(__dirname, '../server/static/index.html'), 'utf8');
  const at = (n) => html.indexOf(`src="${n}?v=`);
  assert.ok(at('console.js') > 0 && at('brain_asks.js') > at('console.js') && at('brain_asks_page.js') > at('brain_asks.js'));
  const v = (n) => new RegExp(`src="${n}\\?v=([^"]+)"`).exec(html)[1];
  assert.equal(v('brain_asks.js'), v('console.js'));
  assert.equal(v('brain_asks_page.js'), v('console.js'));
  const lines = fs.readFileSync(path.resolve(__dirname, '../server/static/console.js'), 'utf8').split('\n');
  const body = (name) => {
    const start = lines.findIndex((l) => new RegExp(`^(async )?function ${name}\\(`).test(l));
    assert.ok(start >= 0, `${name} exists in console.js`);
    return lines.slice(start, lines.findIndex((l, i) => i > start && l.trimEnd() === '}') + 1).join('\n');
  };
  assert.match(body('updateAlertBadge'), /brainAskCount\(\)/, 'the bell counts the open questions');
  assert.match(body('topNotifications'), /brainAskRows\(\)/, 'the bell drawer lists them');
  assert.match(body('startStatusWatch'), /brainAsksTick\(\)/, 'the poll reads them');
  assert.match(body('resetServerCaches'), /brainAsksReset\(\)/, 'a server switch forgets them');
  assert.match(body('showApp'), /loadBrainAsks\(\)/, 'the first load reads them');
});
