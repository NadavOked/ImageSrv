// ‏#2028 (נדב 23/10; מוקאפ 2028-learning, אושר 06/11): שורת המתג, חלון הלמידה והצעת הלמידה למחשב חדש.
// ‏learning.js ב-vm, מול מסגרת מזערית. החלק שבחלון "מיפוי חיבורים" נבדק ב-slot_map_2001.test.cjs.
const {consoleJs: readConsoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = fs.readFileSync(path.resolve(__dirname, '../server/static/learning.js'), 'utf8');
const consoleJs = readConsoleJs();
const index = fs.readFileSync(path.resolve(__dirname, '../server/static/index.html'), 'utf8');

function ctx({admin = true, role = 'cloner', state = null, fail = null, secondary = null} = {}) {
  const calls = {posts: [], sheets: [], toasts: [], reloads: 0, drawer: []};
  const c = vm.createContext({
    calls, SERVER_CTX: secondary, Number, Object, Array, Math, JSON, Promise, decodeURIComponent, encodeURIComponent,
    esc: (s) => String(s).replace(/[&<>"]/g, (x) => `&#${x.charCodeAt(0)};`),
    UI: {link: (t, fn) => `<a data-fn="${fn}">${t}</a>`, note: (cls, html) => `<div class="note ${cls}">${html}</div>`},
    fmtDate: (s) => String(s).slice(5, 10), isAdmin: () => admin, machineRole: () => role, encodeId: encodeURIComponent,
    settingSwitchHtml: (r) => `<switch ${r.key}>`, toast: (m) => calls.toasts.push(m),
    sheet: (o) => calls.sheets.push(o), document: {getElementById: () => null},
    api: async () => { if (fail) throw new Error(fail); return state; },
    post: async (url, body) => { calls.posts.push([url, body]); if (fail) throw new Error(fail); return {}; },
    loadSettingsData: async () => { calls.reloads += 1; }, loadMachines: async () => {},
    findMachine: (mac) => ({mac}), setDrawerBody: (h) => calls.drawer.push(h), machineDrawerHtml: () => 'drawer',
  });
  vm.runInContext(js, c);
  return c;
}
const levels = {disks: {count: 7, min_mbps: 118, max_mbps: 210},
  machines: {count: 2, ports: 9, links: {'6': 8, '3': 1}}, network: {top_mbit: 700, loss: 0.001, interface: 'ens224'}};
const run = (over = {}) => ({id: 1, state: 'collecting', required: 2, started_at: '2026-11-03T08:00:00+00:00', ended_at: null,
  attempts: 1, learned: 1, waves: [{at: '2026-11-03T09:00:00+00:00', ok: true, why_he: null}], running: true, levels, unchanged: [], ...over});
const change = (over = {}) => ({id: 5, key: 'writecheck_min_mbps', label_he: 'סף מהירות כתיבה לדיסק', previous_he: '50 MB/s',
  value_he: '75 MB/s', why_he: 'חציון הדיסקים בחדר: 150 MB/s', state: 'applied', at: '2026-11-04', ...over});
const state = (over = {}) => ({enabled: true, required: 2, run: run(), changes: [], adjusted_keys: [], ...over});
const load = async (c) => { await c.loadLearning(); };

test('the switch row sits next to the auto-rate row, in the same card, off by default', () => {
  const at = consoleJs.indexOf('key: "learned_rate_enabled"'), mine = consoleJs.indexOf('key: "learning_enabled"');
  assert.ok(at > 0 && mine > at && consoleJs.slice(at, mine).split('\n').length <= 3, 'right after the auto-rate row');
  assert.match(consoleJs.slice(mine, mine + 200), /card: "deploy", label: "למידה אוטומטית של הרשת והמחשבים", type: "switch"/);
  assert.doesNotMatch(consoleJs.slice(mine, mine + 200), /defaultOn/, 'a missing value is off');
});

test('the row says what is happening: off, collecting, done, not learned, unread', async () => {
  let c = ctx({state: state({enabled: false, run: null})}); await load(c);
  assert.match(c.learningRowSub(), /^כבוי: כל הגדרות המולטיקאסט ידניות/);
  assert.equal(c.learningRowHtml({key: 'learning_enabled'}), '<switch learning_enabled>', 'nothing to open yet');
  c = ctx({state: state()}); await load(c);
  assert.match(c.learningRowSub(), /1 מתוך 2/); assert.match(c.learningRowSub(), /לא נכתב שום דבר לדיסקים/);
  assert.match(c.learningRowHtml({key: 'learning_enabled'}), /data-fn="openLearning\(\)">פתח סיכום/);
  c = ctx({state: state({run: run({state: 'done', learned: 2, ended_at: '2026-11-04T10:00:00+00:00'}), adjusted_keys: ['a', 'b']})}); await load(c);
  assert.equal(c.learningRowSub(), 'השרת למד מ-2 שיכפולים (נגמר ב-11-04). 2 הגדרות הותאמו, אפשר לחזור לכל אחת.');
  c = ctx({state: state({run: run({state: 'not_learned', learned: 1})})}); await load(c);
  assert.equal(c.learningRowSub(), 'לא נלמד: 1 מתוך 2. לא שונה דבר.');
  c = ctx({fail: 'קריסה'}); await load(c);
  assert.equal(c.learningRowSub(), 'מצב הלמידה לא נקרא: קריסה', 'not read is not "off"');
});

test('the tag marks only what is adjusted now', async () => {
  const c = ctx({state: state({adjusted_keys: ['writecheck_min_mbps']})}); await load(c);
  assert.match(c.learningChip('writecheck_min_mbps'), /הותאם אוטומטית/);
  assert.equal(c.learningChip('min_free_gb'), '');
});

test('mid-learning: steps, three levels, and close-keeps-learning next to turn-off', async () => {
  const c = ctx();
  const v = c.learningView(state());
  assert.equal(v.title, 'למידה אוטומטית: שיכפול 2 מתוך 2');
  assert.match(v.sub, /לא נכתב שום דבר לדיסקים רק בשביל מדידה/);
  assert.match(v.note, /class="st done"><b>שיכפול 1<\/b>הסתיים/); assert.match(v.note, /class="st now"><b>שיכפול 2<\/b>רץ עכשיו/);
  for (const by of ['לפי סידורי', 'לפי MAC ופורט', 'לפי שרת ווילן']) assert.ok(v.note.includes(by), by);
  assert.match(v.note, /7 דיסקים נמדדו/); assert.match(v.note, /2 משכפלים, 9 חיבורים/);
  assert.match(v.note, /8 ב-<bdi dir="ltr">6 Gb\/s<\/bdi>, 1 ב-<bdi dir="ltr">3 Gb\/s/); assert.match(v.note, /אבדן חבילות 0\.10%/);
  assert.match(v.note, /מגירה נחשבת איטית רק אחרי/);
  assert.equal(v.submitLabel, 'סגור, הלמידה ממשיכה'); assert.equal(v.extra.label, 'כבה למידה');
});

test('nothing measured yet reads as not measured, never as zero', () => {
  const none = {disks: {count: 0, min_mbps: null, max_mbps: null}, machines: {count: 0, ports: 0, links: {}}, network: {top_mbit: null, loss: null, interface: null}};
  const html = ctx().learningLevelsHtml(none);
  assert.match(html, /עוד לא נמדד דיסק/); assert.match(html, /עוד לא נמדד חיבור/); assert.match(html, /עוד לא נמדד זרם/);
  assert.doesNotMatch(html, /אבדן חבילות 0/);
});

test('a wave that was not evidence says why, and not-learned changes nothing on screen', () => {
  const v = ctx().learningView(state({run: run({state: 'not_learned', attempts: 2, learned: 1, running: false, waves: [
    {at: 'x', ok: true, why_he: null}, {at: 'y', ok: false, why_he: 'השולח לא סיים את הזרם'}]})}));
  assert.equal(v.title, 'לא נלמד: 1 מתוך 2');
  assert.match(v.sub, /לא שונה דבר/);
  assert.match(v.note, /class="st bad"><b>שיכפול 2<\/b>לא נספר: השולח לא סיים את הזרם/);
  assert.doesNotMatch(v.note, /learn-delta/);
  assert.equal(v.extra, null, 'nothing to revert');
});

test('summary: old struck, new bold, a revert per change, an unchanged row, and revert-all', () => {
  const v = ctx().learningView(state({run: run({state: 'done', learned: 2, unchanged: [{label_he: 'אזהרת CRC למגירה', value_he: 'כבויה', why_he: 'אין היסטוריית CRC שמצדיקה סף'}]}),
    changes: [change(), change({id: 6, state: 'reverted', label_he: 'קצב פתיחה של המולטיקאסט'})]}));
  assert.equal(v.title, 'הלמידה הסתיימה: 2 שיכפולים');
  assert.match(v.note, /class="num old"><bdi dir="ltr">50 MB\/s/); assert.match(v.note, /class="num new"><bdi dir="ltr">75 MB\/s/);
  assert.match(v.note, /data-fn="learningRevert\(5\)">החזר/);
  assert.doesNotMatch(v.note, /learningRevert\(6\)/, 'what was reverted cannot be reverted again');
  assert.match(v.note, /הוחזר/); assert.match(v.note, /לא שונה \(<bdi>כבויה<\/bdi>\)/);
  assert.equal(v.extra.label, 'החזר הכול');
  assert.match(v.note, /וההגדרות נשארות כפי שהן/);
});

test('revert posts, reloads and reopens; a refusal is shown, not swallowed', async () => {
  let c = ctx({state: state({run: run({state: 'done'}), changes: [change()]})});
  await c.learningRevert(5);
  assert.equal(c.calls.posts.map((p) => p[0]).join(), '/learning/changes/5/revert');
  assert.equal(c.calls.reloads, 1);
  c = ctx({fail: 'הערך השתנה'});
  await c.learningRevertAll();
  assert.ok(c.calls.toasts.includes('הערך השתנה'));
  c = ctx({state: state()});
  await c.learningOff();
  assert.equal(JSON.stringify(c.calls.posts[0]), JSON.stringify(['/settings', {learning_enabled: 'false'}]));
});

test('the offer: only an admin, only build and cloner, gone once learned', () => {
  const m = (state, over = {}) => ({mac: 'aa:bb', learning: {state, waves: 3, ports: 4, required: 2}, ...over});
  const offer = ctx().learningOfferHtml(m('offer'));
  assert.match(offer, /המחשב הזה עוד לא נלמד/); assert.match(offer, /ב-2 השיכפולים הבאים/);
  assert.match(offer, /לא נכתב דבר לדיסקים רק בשביל מדידה/);
  assert.match(offer, /הפעל למידה/); assert.match(offer, /לא עכשיו/);
  assert.equal(ctx({admin: false}).learningOfferHtml(m('offer')), '');
  assert.equal(ctx({role: 'student'}).learningOfferHtml(m('offer')), '');
  assert.equal(ctx({secondary: 'node1'}).learningOfferHtml(m('offer')), '', 'the route is not in the channel between servers');
  assert.equal(ctx().learningOfferHtml({mac: 'x', learning: null}), '', 'not read: no offer');
  assert.equal(ctx().learningOfferHtml(m('dismissed')), '');
  const done = ctx().learningOfferHtml(m('learned'));
  assert.equal(done, '<div class="note ok">נלמד: 3 שיכפולים, 4 חיבורים נמדדו. אין מה להפעיל.</div>');
  assert.doesNotMatch(done, /הפעל למידה/);
  assert.match(ctx().learningRowPill(m('offer')), /לא נלמד/);
  assert.equal(ctx().learningRowPill(m('learned')), '');
});

test('answering the offer posts for that machine and redraws the drawer', async () => {
  const c = ctx();
  await c.learningMachine(encodeURIComponent('aa:bb'), 'dismiss');
  assert.equal(JSON.stringify(c.calls.posts[0]), JSON.stringify([`/machines/${encodeURIComponent('aa:bb')}/learning`, {action: 'dismiss'}]));
  assert.equal(c.calls.drawer.join(), 'drawer');
});

test('no screen offers a run that writes to disks', () => {
  assert.doesNotMatch(js, /הרץ מדידה|בדיקת כתיבה/);
});

test('the page loads the script and the style', () => {
  assert.match(index, /<script src="learning\.js\?v=[\d.]+"><\/script>/);
  assert.match(index, /<link rel="stylesheet" href="learning\.css\?v=[\d.]+">/);
});
