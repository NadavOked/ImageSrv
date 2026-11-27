// #3673 -- פירוט בשאלה (`.ba-rows`, מוקאפ מאושר 20/11) בחלון התשובה של הקונסולה. brain_asks.js האמיתי רץ ב-vm.
//
// מה נבדק: השאלה עם פירוט מציירת טבלה אחת בין ההודעה לספירה (כותרת, עמודות, שורות, הערת תחתית); חץ, גופן קבוע, גוון
// ושורה קטנה מצוירים כמו במוקאפ; כל טקסט עובר esc (שום תו מהשרת אינו HTML); שאלה בלי פירוט, או עם פירוט שאינו בצורה
// המוכרת (שורה חלקית, עמודה חסרה), נשארת שאלה -- בלי טבלה ובלי חצי-טבלה.
//
// בקרה שלילית: ראו את גוף ה-PR.
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.resolve(__dirname, '../server/static/brain_asks.js'), 'utf8');
const SKIP = {id: 'skip', label_he: 'לדלג על הדיסק', destructive: false, verb_he: '', consequence_he: '', outcome_he: ''};
const WIPE = {id: 'wipe', label_he: 'למחוק את המידע', destructive: true, verb_he: 'מחיקת המידע', consequence_he: 'יימחק', outcome_he: ''};
const cell = (text, extra = {}) => ({text, style: '', tone: '', sub: '', sub_style: '', arrow: false, ...extra});
const ROWS = {title_he: 'מה ייכתב', cols: ['מחיצה', 'עכשיו', 'אחרי'], more_he: 'הדיסק: 512 GB', rows: [
  [cell('2 · Windows (C:)'), cell('84 GB', {style: 'mono'}), cell('225 GB', {style: 'mono', arrow: true})],
  [cell('Boot0003', {style: 'mono'}), cell('Windows Boot Manager'), cell('אינו במחשב', {tone: 'bad', sub: '3f2a9c1e-…', sub_style: 'mono'})]]};
const ASK = {id: 7, title_he: 'חלוקת המקום', detail_he: 'הדיסק גדול מהמקור', state: 'open', options: [SKIP, WIPE], safe_default: 'skip',
  safe_default_he: SKIP.label_he, expires_in_s: 600, ttl_s: 1800, columns: 1, rows: ROWS, entity: {kind: 'machine', key: 'aa:bb'}};

function load() {
  const calls = {sheets: [], posts: []};
  const ctx = vm.createContext({
    Date, JSON, Number, String, Math, Array, Set, console,
    isAdmin: () => true, SERVER_CTX: null, MACHINES: [], closeModal() {}, machineName: (m) => m.display_name,
    fmtEta: (s) => String(s), esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])),
    UI: {pill: () => ''}, impactList: () => '', toast() {}, updateAlertBadge() {},
    api: async () => ({}), post: async (p, b) => { calls.posts.push([p, b]); return {}; }, sheet: (spec) => { calls.sheets.push(spec); },
    document: {getElementById: () => null, querySelector: () => null, querySelectorAll: () => []},
  });
  vm.runInContext(SRC, ctx);
  return {ctx, calls};
}
const rowsHtml = (ask) => { const h = load(); h.ctx.__a = ask; return vm.runInContext('brainRowsHtml(__a)', h.ctx); };
function sheetNote(ask) {
  const h = load();
  vm.runInContext(`BRAIN_ASKS = {open: [${JSON.stringify(ask)}], recent: []}; BRAIN_ASKS_OK_AT = Date.now()`, h.ctx);
  vm.runInContext('brainAskSheet(7)', h.ctx);
  return h.calls.sheets[0].note;
}

test('the rows are one table: a title, the column heads, a body row per row and the footer note', () => {
  const html = rowsHtml(ASK);
  assert.match(html, /^<div class="ba-rows"><h4>מה ייכתב<\/h4><table>/);
  assert.equal((html.match(/<th>/g) || []).length, 3);
  assert.equal((html.match(/<tr>/g) || []).length, 3);            // כותרת + שתי שורות
  assert.match(html, /<div class="more">הדיסק: <bdi dir="ltr">512 GB<\/bdi><\/div>/);   // הגודל מבודד: לא "GB 512"
});

test('an arrow, a fixed font, a tone and a small line are drawn as the mockup draws them', () => {
  const html = rowsHtml(ASK);
  assert.match(html, /<td><span class="arr">←<\/span><span class="mono">225 GB<\/span><\/td>/);
  assert.match(html, /<span class="badc">אינו במחשב<\/span><span class="dim"><span class="mono">3f2a9c1e-…<\/span><\/span>/);
  assert.match(html, /<td>2 · Windows \(C:\)<\/td>/);
});

test('nothing the server sends is HTML', () => {
  const evil = {...ASK, rows: {...ROWS, title_he: '<img src=x onerror=1>', more_he: '<b>x</b>', rows: [[cell('<script>'), cell('"><i>'), cell('&', {sub: '<u>'})]]}};
  const html = rowsHtml(evil);
  assert.doesNotMatch(html, /<img|<script|<i>|<u>|<b>x/);
  assert.match(html, /&lt;script&gt;/);
});

test('a question without rows, or with rows in a shape it does not know, is still a question: no table, not half a table', () => {
  assert.equal(rowsHtml({...ASK, rows: null}), '');
  assert.equal(rowsHtml({...ASK, rows: undefined}), '');
  assert.equal(rowsHtml({...ASK, rows: {...ROWS, rows: [[cell('a'), cell('b')]]}}), '');                 // שורה חסרת תא
  assert.equal(rowsHtml({...ASK, rows: {...ROWS, rows: [[cell('a'), cell('b'), {sub: 'x'}]]}}), '');     // תא בלי טקסט
  assert.equal(rowsHtml({...ASK, rows: {...ROWS, rows: []}}), '');
  assert.match(sheetNote({...ASK, rows: {...ROWS, rows: [[cell('a')]]}}), /הדיסק גדול מהמקור/);          // ההודעה נשארת
});

test('in the answer window the table sits between the message and the countdown', () => {
  const note = sheetNote(ASK);
  const at = (re) => note.search(re);
  assert.ok(at(/n-note/) >= 0 && at(/n-note/) < at(/ba-rows/) && at(/ba-rows/) < at(/ba-cd/), note);
});
