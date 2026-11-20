// #3654 (K14) -- דף "שאלות המוח" בקונסולה (מוקאפ מאושר 18/11): brain_asks_page.js + brain_asks.js, עם ה-UI האמיתי מ-console.js.
//
// מה נבדק: לשונית "פתוחות" (KPI, טבלה לפי הזמן שנשאר, "ענה", מצב ריק); כשל קריאה אינו "אין שאלות" וקריאה ישנה נאמרת כישנה;
// לשונית "היסטוריה" -- פקיעה מוצגת "פקעה" ולא כתשובה, ביטול אומר למה, אפשרות הרסנית מסומנת, ומי ענה (מנהל / המסך של המכונה /
// אף אחד); הסינון נוסע לשרת (תוצאה, מי ענה, על מה, טווח) והחיפוש בדפדפן כולל שם המכונה; מגירת הפרטים; והבריחה של טקסט מהשרת.
//
// בקרה שלילית: ראו את גוף ה-PR.
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.resolve(__dirname, '../server/static');
const read = (n) => fs.readFileSync(path.join(STATIC, n), 'utf8');
// ה-UI האמיתי (objHeader / datagrid / kpi / card / note / kv / pill) מחולץ מ-console.js, לא מועתק לכאן
const consoleLines = read('console.js').split('\n');
const uiStart = consoleLines.findIndex((l) => l === 'const UI = {');
const uiEnd = consoleLines.findIndex((l, i) => i > uiStart && l === '};');
const UI_SRC = consoleLines.slice(uiStart, uiEnd + 1).join('\n');
const PAGE = read('brain_asks_page.js'), ASKS = read('brain_asks.js');

const NOW = Date.now();
const iso = (msAgo) => new Date(NOW - msAgo).toISOString().replace(/\.\d+Z$/, 'Z');
const OPT = (id, label, extra = {}) => ({id, label_he: label, destructive: false, verb_he: '', consequence_he: '', outcome_he: '', ...extra});
const WIPE = OPT('wipe', 'למחוק את המידע הישן', {destructive: true, verb_he: 'מחיקת המידע', consequence_he: 'המידע הישן יימחק'});
const SKIP = OPT('skip', 'לדלג על הדיסק');
const BASE = {point: 'T1', detail_he: '', options: [SKIP, WIPE], safe_default: 'skip', safe_default_he: SKIP.label_he, ttl_s: 1800,
  entity: {kind: 'machine', key: 'aa:bb:cc:00:00:01'}, who: ['admin', 'machine'], columns: 1, closed_note_he: null};
const open = (id, left, title = `שאלה ${id}`) => ({...BASE, id, title_he: title, state: 'open', expires_in_s: left, created_at: iso(60000), closed_at: null,
  answer: null, answer_he: null, answered_by: null, answered_role: null});
const closed = (id, state, extra = {}) => ({...BASE, id, title_he: `סגורה ${id}`, state, expires_in_s: null, created_at: iso(3 * 3600e3), closed_at: iso(2 * 3600e3),
  answer: null, answer_he: null, answered_by: null, answered_role: null, ...extra});
const ANSWERED_ADMIN = closed(20, 'answered', {answer: 'skip', answer_he: SKIP.label_he, answered_by: 'noc', answered_role: 'admin'});
const ANSWERED_WIPE = closed(21, 'answered', {answer: 'wipe', answer_he: WIPE.label_he, answered_by: 'dana', answered_role: 'admin'});
const ANSWERED_MACHINE = closed(22, 'answered', {answer: 'skip', answer_he: SKIP.label_he, answered_by: 'aa:bb:cc:00:00:01', answered_role: 'machine'});
const EXPIRED = closed(23, 'expired', {ttl_s: 1200});
const CANCELLED = closed(24, 'cancelled', {closed_note_he: 'הסבב נסגר לפני שנענתה'});

function load({admin = true, serverCtx = null, apiResult = null} = {}) {
  const calls = {apis: [], drawers: [], sheets: [], toasts: [], tabs: [], renders: 0};
  let current = 'asks';
  const hist = (items) => ({items, total: items.length + 3, days: 30, today: {answered: 4, expired: 1}});
  const ctx = vm.createContext({
    Date, JSON, Number, String, Math, Array, Set, URLSearchParams, console, Promise,
    isAdmin: () => admin, SERVER_CTX: serverCtx, MACHINES: [{mac: 'aa:bb:cc:00:00:01', name: 'CLONE-01'}],
    machineName: (m) => m.name, esc: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    stripTags: (s) => String(s).replace(/<[^>]*>/g, ''), serverRootName: (s) => s, uiIcon: (n) => `<i:${n}>`, impactList: () => '', fmtEta: (s) => `${s}s`,
    tsMs: (ts) => Date.parse(ts), isToday: (ts) => new Date(ts).toDateString() === new Date(NOW).toDateString(),
    fmtClock: (ts) => { const d = new Date(ts); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; },
    fmtDate: (ts) => new Date(ts).toISOString().slice(0, 10), pagePlaceholder: () => '<placeholder>',
    toast: (...a) => calls.toasts.push(a), updateAlertBadge: () => {}, sheet: (s) => calls.sheets.push(s), closeModal: () => {},
    openDrawer: (title, body, opts) => calls.drawers.push({title, body, opts}),
    renderCurrent: () => { calls.renders++; }, activateTab: (i) => calls.tabs.push(i), refreshPage: () => {},
    api: async (p) => { calls.apis.push(p); if (apiResult instanceof Error) throw apiResult; return apiResult || {open: [], recent: [], history: hist([])}; },
    post: async () => ({}),
    document: {getElementById: () => null, querySelector: () => null, querySelectorAll: () => []},
  });
  vm.runInContext('let current = "asks";', ctx);
  vm.runInContext(UI_SRC, ctx);
  vm.runInContext(ASKS, ctx);
  vm.runInContext(PAGE, ctx);
  return {ctx, calls, hist,
    set: (value, {err = '', at = Date.now()} = {}) => vm.runInContext(`BRAIN_ASKS = ${JSON.stringify(value)}; BRAIN_ASKS_ERR = ${JSON.stringify(err)}; BRAIN_ASKS_OK_AT = ${at}`, ctx),
    page: (tab = 0) => vm.runInContext(`brainAsksPage(${tab})`, ctx)};
}
const data = (h, open = [], items = []) => ({open, recent: [], history: h.hist(items)});

test('before the first read the page is a placeholder; a first read that failed says it could not read, never "no questions"', () => {
  const h = load();
  assert.equal(h.page(), '<placeholder>');
  h.set(null, {err: 'שגיאה 500'});
  const html = h.page();
  assert.match(html, /לא הצלחנו לקרוא את השאלות מהשרת: שגיאה 500/);
  assert.doesNotMatch(html, /אין שאלה שממתינה/);
  assert.doesNotMatch(html, /אין שאלות פתוחות/);
});

test('open tab: the count, the clock to the nearest, the answered and expired today, and the rows nearest-first with an answer button', () => {
  const h = load();
  h.set(data(h, [open(1, 1500, 'רחוקה'), open(2, 40, 'קרובה')]));
  const html = h.page();
  assert.match(html, /2 ממתינות/);
  assert.match(html, /ממתינות לתשובה/);
  assert.match(html, /id="askNearest">00:40</);
  assert.match(html, /נענו היום[\s\S]*?>4</);
  assert.match(html, /פקעו בלי תשובה היום[\s\S]*?>1</);
  assert.ok(html.indexOf('קרובה') < html.indexOf('רחוקה'), 'the nearest to expire first');
  assert.match(html, /onclick="brainAskSheet\(2\)"/);
  assert.match(html, /ברירת המחדל הבטוחה<\/b>, ואף פעם לא פעולה שמוחקת/);
  assert.match(html, /CLONE-01/);
});

test('open tab with nothing waiting says so and offers the history; a failed re-read keeps the list and says how old it is', () => {
  const h = load();
  h.set(data(h));
  let html = h.page();
  assert.match(html, /אין שאלה שממתינה לתשובה/);
  assert.match(html, /activateTab\(1\)/);
  assert.match(html, /אין שאלות פתוחות/);
  h.set(data(h, [open(1, 600)]), {err: 'timeout', at: Date.now() - 40000});
  html = h.page();
  assert.match(html, /מוצג מה שנקרא לפני 40 שניות, והרשימה אינה בהכרח עדכנית/);
  assert.match(html, /brainAskSheet\(1\)/, 'what was read stays on the page');
});

test('history rows: an expiry is "פקעה" with the default that ran, never an answer; a cancellation says why; who answered is told', () => {
  const h = load();
  h.set(data(h, [], [ANSWERED_ADMIN, ANSWERED_WIPE, ANSWERED_MACHINE, EXPIRED, CANCELLED]));
  const html = h.page(1);
  const row = (id) => html.split('<tr').find((r) => r.includes(`brainAskDrawer(${id})`));
  assert.match(row(23), /פקעה/);
  assert.doesNotMatch(row(23), /נענתה/);
  assert.match(row(23), /ברירת המחדל — עברה תקרת הזמן \(20 דקות\)/);
  assert.match(row(23), /אף אחד — ברירת המחדל/);
  assert.match(row(24), /בוטלה/);
  assert.match(row(24), /הסבב נסגר לפני שנענתה/);
  assert.match(row(21), /אפשרות הרסנית/);
  assert.doesNotMatch(row(20), /אפשרות הרסנית/);
  assert.match(row(20), /noc[\s\S]*מנהל/);
  assert.match(row(22), /המסך של <bdi dir="ltr">CLONE-01/);
  assert.match(html, /מוצגות 5 מתוך 8 · השרת שומר את 30 הימים האחרונים/);
});

test('every filter that the server understands goes in the query; the default is 30 days with nothing else', async () => {
  const h = load({apiResult: {open: [], recent: [], history: {items: [], total: 0, days: 30, today: {answered: 0, expired: 0}}}});
  await vm.runInContext('loadBrainAsks()', h.ctx);
  assert.equal(h.calls.apis.at(-1), '/brain/asks?days=30');
  vm.runInContext(`brainFilterState('expired'); `, h.ctx);
  vm.runInContext(`BRAIN_VIEW.who = 'machine'; BRAIN_VIEW.kind = 'image'; BRAIN_VIEW.range = 'w'`, h.ctx);
  await vm.runInContext('loadBrainAsks()', h.ctx);
  assert.deepEqual([...new URLSearchParams(h.calls.apis.at(-1).split('?')[1])].sort(), [['days', '7'], ['kind', 'image'], ['state', 'expired'], ['who', 'machine']]);
});

test('choosing a filter reads again from the server; searching does not, and it finds a machine by its name', async () => {
  const h = load();
  h.set(data(h, [], [ANSWERED_ADMIN, {...EXPIRED, entity: {kind: 'machine', key: 'zz:zz'}, title_he: 'אחרת'}]));
  await vm.runInContext(`brainFilter('who', 'admin')`, h.ctx);
  assert.equal(h.calls.apis.length, 1);
  assert.match(h.calls.apis[0], /who=admin/);
  vm.runInContext('BRAIN_VIEW.q = "CLONE-01"', h.ctx);
  h.set(data(h, [], [ANSWERED_ADMIN, {...EXPIRED, entity: {kind: 'machine', key: 'zz:zz'}, title_he: 'אחרת'}]));
  const html = h.page(1);
  assert.match(html, /brainAskDrawer\(20\)/);
  assert.doesNotMatch(html, /brainAskDrawer\(23\)/);
  assert.match(html, /מוצגות 1 מתוך 5/);
});

test('"today" shows only what closed today', () => {
  const h = load();
  const yesterday = closed(30, 'answered', {closed_at: iso(30 * 3600e3), answer: 'skip', answer_he: SKIP.label_he, answered_by: 'noc', answered_role: 'admin'});
  h.set(data(h, [], [ANSWERED_ADMIN, yesterday]));
  vm.runInContext('BRAIN_VIEW.range = "d"', h.ctx);
  const html = h.page(1);
  assert.match(html, /brainAskDrawer\(20\)/);
  assert.doesNotMatch(html, /brainAskDrawer\(30\)/);
});

test('the details drawer tells what happened in each state, and says an expiry was not an approval', () => {
  const h = load();
  h.set(data(h, [], [ANSWERED_WIPE, EXPIRED, CANCELLED]));
  vm.runInContext('brainAskDrawer(21)', h.ctx);
  vm.runInContext('brainAskDrawer(23)', h.ctx);
  vm.runInContext('brainAskDrawer(24)', h.ctx);
  const [wiped, expired, cancelled] = h.calls.drawers;
  assert.equal(wiped.title, 'סגורה 21');
  assert.match(wiped.body, /dana · מנהל<\/bdi>? ?ענה|dana[\s\S]*ענה/);
  assert.match(wiped.body, /אפשרות הרסנית, בחירה של אדם/);
  assert.match(wiped.body, /הרסנית/);
  assert.match(expired.body, /תם הזמן בלי תשובה/);
  assert.match(expired.body, /לא נרשמה תשובה, ולא נחשב כאישור/);
  assert.match(expired.body, /בוצעה ברירת המחדל/);
  assert.doesNotMatch(expired.body, /ענה/);
  assert.match(cancelled.body, /השאלה בוטלה/);
  assert.match(cancelled.body, /הסבב נסגר לפני שנענתה/);
  assert.match(expired.body, /מנהל בקונסולה · המסך של המכונה/);
  assert.match(expired.opts.meta, /פקעה בלי תשובה/);
});

test('text from the server is escaped everywhere it is drawn', () => {
  const h = load();
  const evil = '<img src=x onerror=alert(1)>';
  h.set(data(h, [{...open(1, 100), title_he: evil}], [{...EXPIRED, title_he: evil, closed_note_he: evil}, {...CANCELLED, title_he: evil, closed_note_he: evil}]));
  vm.runInContext('brainAskDrawer(24)', h.ctx);
  for (const html of [h.page(0), h.page(1), h.calls.drawers[0].body, vm.runInContext('brainAskRows()', h.ctx)]) assert.doesNotMatch(html, /<img/);
});

test('the page is for the primary server only: the tree entry is admin-only, and a secondary server cannot reach it', () => {
  const html = read('index.html'), js = read('console.js');
  assert.match(html, /data-page="asks" data-admin onclick="selectPageById\('asks'\)"/);
  assert.ok(html.indexOf('data-page="asks"') > html.indexOf('data-page="deploy"'), 'under "סבב הפצה"');
  assert.ok(html.indexOf('data-page="asks"') < html.indexOf('data-page="images"'));
  assert.match(js, /const PRIMARY_ONLY_PAGES = \[[^\]]*"asks"/);
  assert.match(js, /asks: \{ crumb: "שאלות המוח"/);
  const h = load({admin: false});
  assert.ok(h.ctx.BRAIN_ASKS === undefined || h.ctx.BRAIN_ASKS === null);
});
