// #3185 (מוקאפ שנדב אישר ב-14/11): הזיכרון ארוך-הטווח של המוח בדפים של הישויות -- דף "דיסקים" (עמודת "מגמה" וכרטיס
// דיסק), מגירת המחשב ("נתונים שנאספו" כסיכום, "מה היה בחריצים", החלטות המוח) והיסטוריית הסבבים ("קצב ואיבוד", שורה
// שנפתחת). בלי jsdom: console.js, disks.js ו-memory_ui.js האמיתיים רצים ב-vm עם DOM מזערי, והבדיקות קוראות את ה-HTML
// שהמסך בונה. המספרים עצמם נבדקים בצד השרת (tests/test_memory_ui_3185.py).
//
// מה נבדק (ובקרה שלילית לכל אחד בגוף ה-PR):
//   * "ערך שלא נמדד אינו אפס": null מוצג במילים ("לא נמדד" / "לא נשמר" / "לא נקרא"), לעולם לא 0.
//   * "תוצאה שלא ידועה אינה בוצע": החלטה שנסגרה בהפעלה מחדש מוצגת "תוצאה לא ידועה", ופתוחה "עוד פתוחה".
//   * חמש השורות הגולמיות לא נעלמות: הן מקופלות מאחורי "5 הדגימות האחרונות".
//   * פחות משתי מדידות הוא "אין עדיין מגמה", לא "יציב"; גרף בלי שתי נקודות הוא הודעה, לא ציור ריק.
//   * ציר הזמן של לשוניות המגירה מקבל את העיצוב (הפאנים חוזרים להיות .page.drw).
'use strict';
const {consoleFiles} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

class El {
  constructor(tag, cls = '') {
    this.tag = tag; this.cls = new Set(cls.split(' ').filter(Boolean)); this.style = ''; this.text = ''; this.children = [];
    this.parent = null; this.hidden = false; this.dataset = {}; this.attrs = {};
    const self = this; this.classList = {contains: (c) => self.cls.has(c), add: (...c) => c.forEach((x) => self.cls.add(x)), remove: (c) => self.cls.delete(c)};
  }
  get firstElementChild() { return this.children[0] || null; }
  set className(v) { this.cls = new Set(String(v).split(' ').filter(Boolean)); }
  getAttribute(k) { return k === 'style' ? this.style : this.attrs[k] ?? null; }
  setAttribute(k, v) { this.attrs[k] = v; } addEventListener() {} removeEventListener() {}
  appendChild(c) { if (c.parent) c.remove(); c.parent = this; this.children.push(c); return c; }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter((x) => x !== this); this.parent = null; } }
  get textContent() { return this.text + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this.text = String(v); }
  set innerHTML(v) { this.children = []; this.text = ''; }
  descendants() { return this.children.flatMap((c) => [c, ...c.descendants()]); }
  querySelectorAll(sel) { return this.descendants().filter((e) => (sel.includes('button') && e.tag === 'button')); }
  querySelector() { return this.children[0] || null; }
}
const el = (tag, cls, kids = [], text = '') => { const e = new El(tag, cls); e.text = text; kids.forEach((k) => e.appendChild(k)); return e; };

function load() {
  const nodes = {drawerSub: new El('div'), drawerTabs: new El('div'), drawerFoot: new El('div')};
  const ctx = vm.createContext({console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, String, Array, encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelector: () => new El('div'), querySelectorAll: () => [], getElementById: (id) => nodes[id] || new El('div'), createElement: (t) => new El(t),
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false})}, localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})})});
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  for (const f of consoleFiles()) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  for (const f of ['disks.js', 'memory_ui.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  return {nodes, ctx, run: (s) => vm.runInContext(s, ctx)};
}
const strip = (html) => html.replace(/<[^>]+>/g, ' ').replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ');

const TREND = (o) => ({state: 'rose', counter: 'reallocated', from: 0, to: 24, rounds: 4, ...o});
const DISK = (o) => ({serial: 'S21PNXAG812001', model: 'Samsung MZ7LN256', size_bytes: 256e9, set_id: null, label: null, suffix: null, state: 'orange', reason: 'critical_rose',
  last_good_write: '2026-11-12T10:47:00+00:00', last_fail: null, in_machine: true, mac: '64:31:50:42:b1:9c', machine: 'משכפל 01', port: 2, last_seen: null, f1: null,
  first_seen: '2026-07-14T10:00:00+00:00', trend: TREND(), ...o});
const ROUND = (o) => ({session_id: 's', at: '2026-11-05T11:12:00+00:00', mac: '64:31:50:42:b1:9c', machine: 'משכפל 01', port: 2, temp: 41, reallocated: 9, pending: 1, media_errors: null,
  mbps: 352, link_gbps: 6, crc_delta: 0, written: true, pct_of_model: 82, ...o});
const CARD = (o) => ({serial: 'S21PNXAG812001', model: 'Samsung MZ7LN256', first_seen: '2026-07-14T10:00:00+00:00', rounds: {total: 13, detailed: 4, summarized: 9}, trend: TREND(),
  smart: {rounds: [ROUND({at: '2026-11-12T11:12:00+00:00', reallocated: 24, pending: 3, mbps: 301, pct_of_model: 67}), ROUND({reallocated: 9}), ROUND({at: '2026-10-25T11:12:00+00:00', reallocated: 2, pending: 0}),
    ROUND({at: '2026-10-04T11:12:00+00:00', reallocated: 0, pending: 0})], older: {rounds: 9, reallocated: 0, pending: 0, media_errors: null, temp: 41}},
  write: {failures: 0, failures_last_at: null, last_mbps: 301, model_avg_mbps: 452, model_n: 38, older: {rounds: 9, avg: 388, min: 371}, mbps: {n: 13, avg: 377}},
  link: {rounds: 13, last_gbps: 6, max_gbps: 6, crc_total: 0, cable_fails: 0}, failures: [], decisions: [], ...o});
const DECISION = (o) => ({at: '2026-11-12T10:00:00+00:00', reason: 'סקטורים מוחלפים עלו מ-9 ל-24: הומלץ להחליף', outcome: null, outcome_state: 'open', ...o});

// --- דף הדיסקים --------------------------------------------------------------------------------------------------

test('the trend column says what rose, from what to what, and in how many rounds', () => {
  const {ctx} = load();
  const html = ctx.dkTrendCell(TREND());
  assert.match(html, /<span class="pill warn">סקטורים מוחלפים עלו<\/span>/);
  assert.match(strip(html), /מ-\s*0\s*ל-\s*24\s*ב-4 סבבים/);
  assert.match(ctx.dkTrendCell(TREND({counter: 'pending'})), /סקטורים ממתינים עלו/);
});

test('fewer than two measurements is "no trend yet", never "stable"; a trend the server did not send is "unread"', () => {
  const {ctx} = load();
  assert.match(ctx.dkTrendCell({state: 'none', counter: null, from: null, to: null, rounds: 1}), /אין עדיין מגמה/);
  assert.doesNotMatch(ctx.dkTrendCell({state: 'none', counter: null, rounds: 1}), /יציב/);
  assert.match(ctx.dkTrendCell({state: 'stable', counter: 'reallocated', from: 0, to: 0, rounds: 4}), /יציב · 4 סבבים/);
  assert.match(ctx.dkTrendCell(undefined), /לא נקרא/);
  assert.match(ctx.dkTrendCell(TREND({state: 'fell', from: 9, to: 2})), /סקטורים מוחלפים ירדו/);
});

test('the disks table has a "מגמה" column after the state, and a row opens the card; the checkbox and the menu do not', () => {
  const {ctx, run} = load();
  ctx.IMAGES = []; ctx.isAdmin = () => true;
  run(`DK.disks = ${JSON.stringify([DISK()])}; DK.sets = []; DK.rounds = [];`);
  const html = run('dkListTab("")');
  const head = html.match(/<thead>.*<\/thead>/s)[0];
  assert.ok(head.indexOf('מצב') < head.indexOf('מגמה') && head.indexOf('מגמה') < head.indexOf('איפה עכשיו'), 'the column sits between state and place');
  assert.match(html, /<tr class="clk" tabindex="0" onclick="dkRowClick\(event,/);
  assert.match(html, /סקטורים מוחלפים עלו/);
  const opened = [];
  ctx.dkOpenCard = (s) => opened.push(s);
  ctx.dkRowClick({target: {closest: (sel) => (/input/.test(sel) ? {} : null)}}, 'S1');
  assert.deepEqual(opened, [], 'a click on the checkbox / menu button is not a click on the row');
  ctx.dkRowClick({target: {closest: () => null}}, 'S2');
  assert.deepEqual(opened, ['S2']);
});

test('the card has the four tabs the mockup approved, in order, and every tab is a .sec block drawerEnhance can lift', () => {
  const {ctx} = load();
  ctx.isAdmin = () => true;
  const html = ctx.dkCardHtml(DISK(), CARD());
  const secs = [...html.matchAll(/<div class="sec"[^>]*>([^<]*)<\/div>/g)].map((m) => m[1]);
  assert.deepEqual(secs, ['עכשיו', 'SMART לאורך זמן', 'כתיבות', 'היסטוריה']);
  assert.match(html, /^<div class="page drw"><div class="obj-sub">/);
  assert.match(html, /<div class="acts">.*שייך לסט….*פתח את משכפל 01/s);
});

test('the overview says what the server knows: first seen, the rounds split into detail and summary, speed against the model', () => {
  const {ctx} = load();
  const text = strip(ctx.dkCardHtml(DISK(), CARD()));
  assert.match(text, /נראה לראשונה 14\/07\/2026/);
  assert.match(text, /סבבים שנכתב בהם 13 · 4 אחרונים בפירוט, 9 בסיכום/);
  assert.match(text, /301 MB\/s בסבב האחרון/);
  assert.match(text, /67% מהממוצע של הדגם: 452 MB\/s\s*, לפי 38 מדידות/);
  assert.match(ctx.dkCardHtml(DISK(), CARD()), /<div class="bar warn">/, 'under 70% of the model is the same orange as the brain\'s slow ratio');
});

test('a value the server did not measure is said so in words, never as 0', () => {
  const {ctx} = load();
  const card = CARD({write: {...CARD().write, last_mbps: null}, link: {rounds: 3, last_gbps: null, max_gbps: null, crc_total: null, cable_fails: null}, first_seen: null});
  card.smart.rounds = [ROUND({temp: null, reallocated: null, pending: null, mbps: null, written: false}), ROUND({temp: 40, reallocated: 0, pending: null, mbps: null})];
  const html = ctx.dkCardHtml(DISK({first_seen: null}), card);
  assert.match(strip(html), /מהירות כתיבה לא נמדד/);
  assert.match(strip(html), /חיבור לא נקרא/);
  assert.match(strip(html), /נראה לראשונה לא נרשם/);
  const smart = html.match(/<div class="sec">SMART לאורך זמן<\/div>.*?<div class="sec">כתיבות/s)[0];
  assert.match(strip(smart), /ממתינים/);
  assert.match(smart, /לא נמדד/, 'the pending counter that was never reported is "not measured"');
});

test('the SMART chart starts at zero, has an explicit height, and with fewer than two points is a message, not an empty drawing', () => {
  const {ctx} = load();
  const svg = ctx.dkCardHtml(DISK(), CARD()).match(/<svg[^>]*>/)[0];
  assert.match(svg, /height="96"/); assert.match(svg, /viewBox="0 0 400 96"/);
  const one = CARD(); one.smart.rounds = [ROUND()];
  const html = ctx.dkCardHtml(DISK(), one);
  assert.doesNotMatch(html, /<svg/);
  assert.match(html, /אין עדיין גרף: פחות משתי מדידות SMART/);
});

test('SMART rows: a counter that rose is bold with its delta; old rounds are one summary row with the maximum', () => {
  const {ctx} = load();
  const html = ctx.dkCardHtml(DISK(), CARD());
  assert.match(html, /ישנים <span class="sub">9 סבבים, מקסימום<\/span>/);
  assert.match(html, /<b>24<\/b> <span class="pill warn"><bdi dir="ltr">\+15<\/bdi><\/span>/);
  assert.doesNotMatch(html, /‎/, 'no bidi marks in the markup');
});

test('the brain\'s decisions come with their outcome: an unknown outcome is "תוצאה לא ידועה" and never "בוצע"', () => {
  const {ctx} = load();
  const decisions = [DECISION({outcome: 'הקוד הקיים כתב על הדיסק', outcome_state: 'closed'}), DECISION({reason: 'קודם', outcome: 'השרת הופעל מחדש לפני שההחלטה נסגרה: התוצאה לא ידועה', outcome_state: 'unknown'}),
    DECISION({reason: 'פתוחה'})];
  const html = ctx.dkCardHtml(DISK(), CARD({decisions}));
  const text = strip(html);
  assert.match(text, /הקוד הקיים כתב על הדיסק/);
  assert.match(text, /תוצאה לא ידועה: השרת הופעל מחדש/);
  assert.match(text, /עוד פתוחה/);
  assert.doesNotMatch(text, /בוצע/);
  assert.match(text, /3 אחרונות/);
  assert.match(strip(ctx.dkCardHtml(DISK(), CARD({decisions: []}))), /המוח עוד לא החליט דבר על הדיסק הזה/);
});

test('while the card loads the overview comes from the row; if it fails the card says so instead of showing nothing', () => {
  const {ctx, run} = load();
  const loading = ctx.dkCardHtml(DISK(), null);
  assert.match(strip(loading), /טוען…/);
  assert.deepEqual([...loading.matchAll(/<div class="sec"[^>]*>([^<]*)<\/div>/g)].map((m) => m[1]), ['עכשיו']);
  run('MEM["/disks/S21PNXAG812001/card"] = {error: "קריאה נכשלה"}');
  assert.match(strip(ctx.dkCardHtml(DISK(), null)), /מה שהשרת יודע על הדיסק לא נקרא: קריאה נכשלה/);
});

// --- מגירת המחשב -------------------------------------------------------------------------------------------------

const RAW_NIC = '<span class="mono">2026-11-12 12:05:00</span> eth0 1000Mb/s';
const MEMORY = (o) => ({mac: '64:31:50:42:b1:9c', since: '2026-08-03T10:00:00+00:00',
  nic: {samples: 13, since: '2026-08-03T10:00:00+00:00', speed: {last: 1000, min: 100, max: 1000, low_at: '2026-11-05T10:05:00+00:00', n: 13}, crc: {first: 0, last: 2, max: 2}, dropped: {last: 0, max: 0}},
  drops: {waves: 13, rcvbuf: {read: 13, sum: 0}, inerrors: {read: 13, sum: 3}, abandoned: {read: 0, sum: null}},
  wol: {sent: 3, answered: 3, avg_s: 38, resent: 0, early: 0, alerted: 0, since: '2026-08-03T10:00:00+00:00'},
  boot: {transitions: 13, steps: 4, since: '2026-08-03T10:00:00+00:00', slowest: {step: 'agent-net', avg_s: 41, max_s: 74, n: 4}},
  hardware: {versions: 2, last_at: '2026-10-10T10:00:00+00:00'},
  slots: [{port: 1, rounds: 13, since: '2026-08-03T10:00:00+00:00', crc: 0, cable_fails: 0, link: {last: 6, min: 6}, link_max: 6, failures: 0, disk: null},
    {port: 2, rounds: 13, since: '2026-08-03T10:00:00+00:00', crc: 0, cable_fails: 0, link: {last: 6, min: 6}, link_max: 6, failures: 0,
      disk: {serial: 'S21PNXAG812001', first_seen: '2026-07-14T10:00:00+00:00', trend: TREND()}},
    {port: 3, rounds: 0, since: null, crc: null, cable_fails: null, link: null, link_max: null, failures: 0, disk: null}],
  decisions: [DECISION({reason: 'הדלקה איטית מהרגיל: 74 ש\' מול ממוצע 41', outcome: 'השרת הופעל מחדש לפני שההחלטה נסגרה: התוצאה לא ידועה', outcome_state: 'unknown'})], ...o});
function machineCtx(mem) {
  const t = load();
  const {ctx, run} = t;
  ctx.isAdmin = () => true; ctx.ago = () => 'לפני דקה';
  run(`MACHINE_DATA["m"] = {nic_history: [], wave_drops: [], agent_logs: [], cpu: [], pxe: null, ip_history: [], boot_marks: []};`);
  ctx.nicHistoryHtml = () => RAW_NIC; ctx.waveDropsHtml = () => '<span class="mono">RAW-WAVES</span>';
  if (mem) run(`MEM["/machines/m/memory"] = ${JSON.stringify(mem)}`);
  ctx.encodeURIComponent = (x) => x;
  return t;
}

test('"נתונים שנאספו" shows the NIC and the drops as a summary, and the raw rows are folded behind "5 הדגימות האחרונות"', () => {
  const {ctx} = machineCtx(MEMORY());
  const html = ctx.machineDataHtml({mac: 'm'});
  const text = strip(html);
  assert.match(text, /1000 Mb\/s עכשיו\. ירד ל-\s*100 Mb\/s ב-05\/11 · 13 דגימות/);
  assert.match(text, /CRC עלה מ-0 ל-2/);
  assert.match(html, /<details class="tech"><summary>5 הדגימות האחרונות<\/summary><span class="mono">2026-11-12 12:05:00<\/span> eth0 1000Mb\/s<\/details>/, 'the raw row is still there, folded');
  assert.match(text, /אין זריקות ב-13 גלים · שגיאות קלט: 3/);
  assert.match(html, /<summary>4 הגלים האחרונים<\/summary><span class="mono">RAW-WAVES<\/span>/);
});

test('a receiver that was never read is "not read", not "no drops"', () => {
  const {ctx} = machineCtx(MEMORY({drops: {waves: 13, rcvbuf: {read: 0, sum: null}, inerrors: {read: 0, sum: null}, abandoned: {read: 0, sum: null}}}));
  const text = strip(ctx.machineDataHtml({mac: 'm'}));
  assert.match(text, /זריקות buffer לא נקראו/);
  assert.doesNotMatch(text, /אין זריקות/);
  assert.match(text, /שגיאות קלט: לא נקרא/);
});

test('the group has the three new rows: slowest boot step, wake-on-LAN, hardware versions; and the title says since when', () => {
  const {ctx} = machineCtx(MEMORY());
  const text = strip(ctx.machineDataHtml({mac: 'm'}));
  assert.match(text, /שלב האתחול האיטי ביותר agent-net · ממוצע 41 ש' · הכי איטי 74 ש'/);
  assert.match(text, /הדלקות מרחוק \(WoL\) נענה ב-3 מתוך 3 · ממוצע 38 ש' · בלי שליחה חוזרת/);
  assert.match(text, /גרסאות חומרה 2 גרסאות נרשמו · האחרונה ב-10\/10/);
  assert.match(strip(ctx.memMachineTitle(MEMORY())), /נתונים שנאספו מאז 03\/08\/2026/);
  assert.equal(ctx.memMachineTitle(null), 'נתונים שנאספו');
});

test('before the memory arrives (or if it fails) the raw rows stay exactly as they were, and a failure says so', () => {
  const t = machineCtx(null);
  const raw = t.ctx.machineDataHtml({mac: 'm'});
  assert.match(raw, /eth0 1000Mb\/s/); assert.doesNotMatch(raw, /<details class="tech"><summary>5 הדגימות/);
  assert.doesNotMatch(strip(raw), /WoL/);
  t.run('MEM["/machines/m/memory"] = {error: "אין הרשאה"}');
  assert.match(strip(t.ctx.machineDataHtml({mac: 'm'})), /זיכרון המוח לא נקרא: אין הרשאה/);
});

test('"מה היה בחריצים": a line per slot; a slot with no history says so; the disk that rose is named', () => {
  const {ctx} = machineCtx(MEMORY());
  ctx.portLabel = (m, port) => `דיסק ${port}`;
  const html = ctx.memSlotsHtml({mac: 'm'});
  const text = strip(html);
  assert.match(text, /מה היה בחריצים מאז 03\/08\/2026/);
  assert.match(text, /דיסק 1 אין תקלות · 13 סבבים/);
  assert.match(text, /דיסק 2 סקטורים מוחלפים עלו הדיסק הנוכחי בחריץ מאז 14\/07\/2026/);
  assert.match(text, /דיסק 3 עוד לא נרשם בו סבב/);
  assert.doesNotMatch(text, /דיסק 3 אין תקלות/, 'a slot with no rounds is not "no faults"');
  assert.match(text, /החריץ אינו הדיסק: דיסק שהוחלף משאיר את ההיסטוריה של החריץ/);
});

test('a slot that had cable and write failures lists them (#3591: not the link speed)', () => {
  const {ctx} = machineCtx(MEMORY({slots: [{port: 1, rounds: 5, since: null, crc: 4, cable_fails: 2, link: {last: 1.5, min: 1.5}, link_max: 6, failures: 1, disk: null}]}));
  ctx.portLabel = () => 'דיסק 1';
  const text = strip(ctx.memSlotsHtml({mac: 'm'}));
  assert.match(text, /כשלי כבל: 2 · CRC עלה ב-4 · כשלי כתיבה: 1/);
  assert.doesNotMatch(text, /הקישור ירד/);
});

test('#3591: a slot whose link sits at 3.0 or 1.5 of 6.0 has no faults to list', () => {
  for (const min of [3, 1.5]) {
    const {ctx} = machineCtx(MEMORY({slots: [{port: 1, rounds: 5, since: null, crc: 0, cable_fails: 0, link: {last: min, min}, link_max: 6, failures: 0, disk: null}]}));
    ctx.portLabel = () => 'דיסק 1';
    const text = strip(ctx.memSlotsHtml({mac: 'm'}));
    assert.doesNotMatch(text, /הקישור ירד/);
    assert.match(text, /דיסק 1 אין תקלות/);
  }
});

test('the machine history is the journal and the brain\'s decisions together, newest first, each decision with its outcome', () => {
  const {ctx} = machineCtx(MEMORY());
  const journal = [{at: '2026-11-13T10:00:00+00:00', t: '13/11', cls: '', text: 'אירוע ביומן', who: 'demo'}];
  const events = ctx.memHistoryEvents(journal, MEMORY({decisions: [DECISION({at: '2026-11-14T10:00:00+00:00', outcome_state: 'unknown', outcome: 'x'}), DECISION({at: '2026-11-01T10:00:00+00:00', reason: 'ישנה'})]}));
  assert.deepEqual(events.map((e) => e.who), ['המוח', 'demo', 'המוח']);
  assert.match(events[0].text, /תוצאה לא ידועה: השרת הופעל מחדש/);
  assert.match(events[2].text, /עוד פתוחה/);
  assert.deepEqual(Object.keys(events[0]).sort(), ['cls', 't', 'text', 'who'], 'only what UI.timeline reads');
});

// --- היסטוריית הסבבים --------------------------------------------------------------------------------------------

const H = (o) => ({id: 'rr1', state: 'closed', image_id: 'img', image_name: 'win-f2-1113', target_drives: 6, written_drives: 5, created_at: '2026-11-05T10:00:00+00:00',
  closed_at: '2026-11-05T10:35:00+00:00', failed_reason: null, waits: {waves: 1}, flow: {waves: 1, kept: 1, rate_bps: 260e6, loss_blocks: 41}, ...o});
function roundCtx(history) {
  const t = load();
  t.ctx.isAdmin = () => true; t.ctx.renderCurrent = () => {};
  t.run(`ROOM = null; ROOM_HISTORY = ${JSON.stringify(history)}; current = "deploy";`);
  return t;
}

test('#3978 (נדב 21/11): the round table has no "קצב ואיבוד" column; "הרשת" says it alone', () => {
  const {ctx, run} = roundCtx([H(), H({id: 'rr2', flow: {waves: 1, kept: 1, rate_bps: 300e6, loss_blocks: 0}})]);
  const html = run('roomHistoryHtml()');
  assert.doesNotMatch(html, /קצב ואיבוד|איבוד 41 בלוקים|260 Mbps/);
  assert.match(html, /<th[^>]*>מצב<\/th><th[^>]*>הרשת<\/th><th[^>]*>תאריך<\/th>/);
  assert.equal(typeof ctx.memFlowCell, 'undefined');
});

test('a row opens to a summary on a click or Enter/Space, and only one round is open at a time', () => {
  const {ctx, run} = roundCtx([H(), H({id: 'rr2'})]);
  const html = run('roomHistoryHtml()');
  assert.match(html, /<tr class="clickable" tabindex="0" role="button" aria-expanded="false" onclick="memRoundToggle\(/);
  assert.match(html, /event\.key==='Enter'\|\|event\.key===' '/);
  const loaded = [];
  ctx.memLoad = (p) => loaded.push(p);
  run('memRoundToggle("rr1")');
  assert.equal(run('MEM_OPEN_ROUND'), 'rr1'); assert.deepEqual(loaded, ['/room/history/rr1/summary']);
  assert.match(run('roomHistoryHtml()'), /aria-expanded="true"/);
  assert.match(run('roomHistoryHtml()'), /<tr class="rd"><td colspan="9">/);
  run('memRoundToggle("rr2")');
  assert.equal((run('roomHistoryHtml()').match(/<tr class="rd">/g) || []).length, 1);
  run('memRoundToggle("rr2")');
  assert.equal(run('MEM_OPEN_ROUND'), null); assert.doesNotMatch(run('roomHistoryHtml()'), /<tr class="rd">/);
});

const SUMMARY = (o) => ({waves: [{session_id: 's', kept: true, rate_bps: 260e6, sent_bytes: 17.2e9, loss_blocks: 41, at: 'x', created_at: 'x'}], flow: {}, 
  receivers: [{mac: 'a', machine: 'משכפל 01', rcvbuf: 0, inerrors: 3, abandoned: null}, {mac: 'b', machine: 'משכפל 02', rcvbuf: 0, inerrors: 0, abandoned: null}],
  drives: [{mac: 'a', machine: 'משכפל 01', port: 2, serial: 'S21PNXAG812001', model: 'Samsung MZ7LN256', mbps: 352, state: 'done', pct_of_model: 78},
    {mac: 'b', machine: 'משכפל 02', port: 2, serial: '50026B7782A1C8D4', model: 'KINGSTON SV300', mbps: null, state: 'failed', pct_of_model: null},
    {mac: 'b', machine: 'משכפל 02', port: 3, serial: 'X', model: null, mbps: null, state: null, pct_of_model: null}],
  decisions: [DECISION({reason: 'הקצב ירד ל-260 Mbps', outcome_state: 'unknown', outcome: 'x'})], ...o});

test('the opened row shows the wave, the receivers, the drives and the brain\'s decisions, and says "not read" where it was not', () => {
  const {ctx, run} = roundCtx([H()]);
  run(`MEM["/room/history/rr1/summary"] = ${JSON.stringify(SUMMARY())}`);
  const text = strip(ctx.memRoundDetail(H(), 9));
  assert.match(text, /win-f2-1113 · 05\/11\/2026/);
  assert.match(text, /קצב שנבחר 260 Mbps/); assert.match(text, /נשלחו 16\.0 GB/); assert.match(text, /חתיכות שאבדו 41 · שודרו מחדש/);
  assert.match(text, /משכפל 01 0 3 לא נקרא/, 'abandoned was never read: "not read", not 0');
  assert.match(text, /352 נכתב 78% מהממוצע של הדגם/);
  assert.match(text, /לא נכתב/); assert.match(text, /לא ידוע/);
  assert.match(text, /הקצב ירד ל-260 Mbps תוצאה לא ידועה: השרת הופעל מחדש/);
  assert.doesNotMatch(text, /בוצע/);
});

test('a wave that was not kept is "לא נשמר" in the opened row, not zero loss; several waves become a table', () => {
  const {ctx, run} = roundCtx([H()]);
  run(`MEM["/room/history/rr1/summary"] = ${JSON.stringify(SUMMARY({waves: [{session_id: 's', kept: false, created_at: 'x'}]}))}`);
  const one = strip(ctx.memRoundDetail(H(), 9));
  assert.match(one, /קצב שנבחר לא נשמר/); assert.match(one, /חתיכות שאבדו לא נשמר/); assert.doesNotMatch(one, /בלוקים שאבדו/);
  run(`MEM["/room/history/rr1/summary"] = ${JSON.stringify(SUMMARY({waves: [{session_id: 'a', kept: true, rate_bps: 300e6, sent_bytes: 5e9, loss_blocks: 0, created_at: 'x'}, {session_id: 'b', kept: false, created_at: 'y'}]}))}`);
  const html = ctx.memRoundDetail(H(), 9);
  assert.match(html, /<th>גל<\/th><th>קצב<\/th>/);
  assert.match(strip(html), /2 לא נשמר — לא נשמר לא נשמר|2 לא נשמר/);
});

test('while the summary loads, and if it fails, the row says so', () => {
  const {ctx, run} = roundCtx([H()]);
  assert.match(strip(ctx.memRoundDetail(H(), 9)), /טוען…/);
  run('MEM["/room/history/rr1/summary"] = {error: "אין סבב כזה"}');
  assert.match(strip(ctx.memRoundDetail(H(), 9)), /סיכום הסבב לא נקרא: אין סבב כזה/);
});

// --- ציר הזמן בלשוניות -------------------------------------------------------------------------------------------

test('the drawer panes after "סקירה" keep .page.drw, so the timeline and the groups have their design (the old finding on .page .timeline)', () => {
  const {nodes, ctx, run} = load();
  const sec = (t) => el('div', 'sec', [], t);
  const rootEl = el('div', 'page drw', [el('div', '', [sec('פרטים'), el('div', '', [], 'גוף')]), el('div', '', [sec('כתיבות'), el('div', '', [], 'טבלה')]), el('div', '', [sec('היסטוריה'), el('div', 'timeline')])]);
  const body = el('div'); body.appendChild(rootEl);
  ctx.__body = body; run('drawerEnhance(__body)');
  const panes = body.children;
  assert.equal(panes.length, 3);
  panes.forEach((p, i) => assert.ok(p.cls.has('n-pane') && p.cls.has('page') && p.cls.has('drw'), `pane ${i} is .n-pane.page.drw: ${[...p.cls]}`));
  assert.deepEqual(nodes.drawerTabs.children.map((t) => t.text), ['סקירה', 'כתיבות', 'היסטוריה']);
});

test('the console still loads in the order index.html gives, with memory_ui.js after console.js and disks.js', () => {
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const at = (n) => html.indexOf(`src="${n}?v=`);
  assert.ok(at('console.js') > 0 && at('disks.js') > at('console.js') && at('memory_ui.js') > at('disks.js'));
  assert.equal(new Set([...html.matchAll(/\?v=([^"]+)"/g)].map((m) => m[1])).size, 1, 'one ?v= for every include');
});

test('#3173: a chunk wave says its loss in chunks (lost_chunks) under "חתיכות שאבדו"; the old sender\'s blocks win where a row has them', () => {
  const {ctx, run} = roundCtx([H()]);
  run(`MEM["/room/history/rr1/summary"] = ${JSON.stringify(SUMMARY({waves: [{session_id: 's', kept: true, rate_bps: 300e6, sent_bytes: 5e9, loss_blocks: null, lost_chunks: 7, created_at: 'x'}]}))}`);
  assert.match(strip(ctx.memRoundDetail(H(), 9)), /חתיכות שאבדו 7 · שודרו מחדש/);
  run(`MEM["/room/history/rr1/summary"] = ${JSON.stringify(SUMMARY({waves: [{session_id: 's', kept: true, rate_bps: 300e6, sent_bytes: 5e9, loss_blocks: null, lost_chunks: null, created_at: 'x'}]}))}`);
  assert.match(strip(ctx.memRoundDetail(H(), 9)), /חתיכות שאבדו לא נמדד/, 'not measured is not "none"');
  run(`MEM["/room/history/rr1/summary"] = ${JSON.stringify(SUMMARY({waves: [{session_id: 'a', kept: true, rate_bps: 300e6, sent_bytes: 5e9, loss_blocks: null, lost_chunks: 3, created_at: 'x'}, {session_id: 'b', kept: true, rate_bps: 300e6, sent_bytes: 4e9, loss_blocks: null, lost_chunks: 0, created_at: 'y'}], drives: []}))}`);
  assert.doesNotMatch(strip(ctx.memRoundDetail(H(), 9)), /לא נמדד/, 'both waves measured their loss in chunks');
});
