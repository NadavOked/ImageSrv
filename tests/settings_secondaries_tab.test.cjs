// נדב 18/10 — לשונית "שרתי משנה" בהגדרות: רק admin בראשי/עצמאי (enroll_secondary || interbranch_transfer), לא בשרת משנה;
// מצב ריק עם "+ רישום שרת משנה"; שורה אחת לשרת המשנה עם סטטוס כ-pill; "לא נקרא" ≠ "אין שרתי משנה".
const {consoleJs: readConsoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');
const consoleJs = readConsoleJs(), branchesJs = read('branches.js');

function fn(src, name) {
  const lines = src.split('\n');
  const start = lines.findIndex((l) => new RegExp('^(async )?function ' + name + '[(]').test(l));
  assert.ok(start >= 0, `${name} exists`);
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  const end = lines.findIndex((l, i) => i > start && l.trimEnd() === '}');
  return lines.slice(start, end + 1).join('\n');
}
function ctx(state = {}) {
  const UI = {
    pill: (cls, text) => `<pill ${cls}>${text}</pill>`,
    card: ({title, acts, body}) => `<card ${title}>${acts || ''}${body}</card>`,
    empty: (text, action, title) => `<empty ${title}>${action}</empty>`,
    note: (cls, html) => `<note ${cls}>${html}</note>`,
  };
  const c = vm.createContext({UI, SERVER_CTX: null, BRANCH_NODES: [], BRANCH_CONN: {}, BRANCH_READY: true, BRANCH_ERR: '',
    ME: {role: 'admin', capabilities: {enroll_secondary: true, interbranch_transfer: false}},
    fmtDate: () => '12/10/2026', fmtClock: () => '08:41', isToday: () => true, encodeId: (x) => x, isRawTechnical: (s) => /Errno/.test(s),
    isAdmin: function () { return !!this.ME && this.ME.role === 'admin'; }, ...state});
  vm.runInContext('const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };', c);
  vm.runInContext('function isAdmin() { return !!ME && ME.role === "admin"; }', c);
  for (const n of ['esc', 'ghostBtn', 'settingsSecondariesAllowed']) vm.runInContext(fn(consoleJs, n), c);
  for (const n of ['branchWhen', 'branchPillHtml', 'settingsSecondaryRow', 'settingsSecondariesTab']) vm.runInContext(fn(branchesJs, n), c);
  return c;
}
const node = (extra = {}) => ({id: 'n1', label: 'סניף חיפה', base_url: 'https://10.30.0.2:8443', enrolled_at: 'x', ...extra});

test('the tab exists only for an admin on a primary/standalone — never in a secondary context, never for deploy', () => {
  assert.equal(ctx().settingsSecondariesAllowed(), true, 'enroll_secondary alone is enough: the first secondary can be added');
  assert.equal(ctx({ME: {role: 'admin', capabilities: {interbranch_transfer: true}}}).settingsSecondariesAllowed(), true);
  assert.equal(ctx({ME: {role: 'admin', capabilities: {}}}).settingsSecondariesAllowed(), false);
  assert.equal(ctx({ME: {role: 'deploy', capabilities: {enroll_secondary: true}}}).settingsSecondariesAllowed(), false);
  assert.equal(ctx({SERVER_CTX: {id: 'n1'}}).settingsSecondariesAllowed(), false);
});

test('empty state: one explanation and the "+ רישום שרת משנה" button; the tree item shows for either capability', () => {
  const html = ctx().settingsSecondariesTab();
  assert.match(html, /אין עדיין שרת משנה/);
  assert.match(html, /onclick="openEnrollSheet\(\)">\+ רישום שרת משנה/);
  assert.match(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), /data-page="branches" data-cap="interbranch_transfer\|enroll_secondary"/);
  assert.match(consoleJs, /split\("\|"\)\.some\(\(cap\) => caps\[cap\]\)/);
});

test('one row per secondary: connection as a pill with the time, actions at the end; a failing one says why', () => {
  const c = ctx({BRANCH_NODES: [node(), node({id: 'n2', label: 'באר שבע'})], BRANCH_CONN: {
    n1: {state: 'ok', lastSeenAt: 't', machines: [1, 2, 3]}, n2: {state: 'err', error: 'timeout', lastErrorAt: 't'}}});
  const html = c.settingsSecondariesTab();
  assert.equal((html.match(/<dt class="sec">/g) || []).length, 2);
  assert.match(html, /<pill ok>מחובר<\/pill><span class="sub">ענתה 08:41/);
  assert.match(html, /<pill err>לא ענה<\/pill><span class="sub">08:41 · timeout/);
  assert.match(html, /3 מחשבים/);
  for (const a of ["checkBranchNow('n1')", "editBranchNode('n1')", "openSecondaryPage('n1','home',null)"]) assert.ok(html.includes(a), a);
});

test('a disabled secondary is "מושבת" with no check/open; a raw system error sits behind "פרטים טכניים"', () => {
  const c = ctx({BRANCH_NODES: [node({disabled_at: 'x'}), node({id: 'n2'})], BRANCH_CONN: {
    n1: {state: 'disabled'}, n2: {state: 'err', error: '[Errno 111] refused'}}});
  const html = c.settingsSecondariesTab();
  assert.match(html, /<pill >מושבת<\/pill>/);
  assert.doesNotMatch(html, /checkBranchNow\('n1'\)/);
  assert.match(html, /<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">\[Errno 111\] refused/);
});

test('unread is not empty: a failed list says so and shows no enroll-first empty state; before the first read it says loading', () => {
  const failed = ctx({BRANCH_ERR: 'שגיאה 500'}).settingsSecondariesTab();
  assert.match(failed, /<note err>לא הצלחתי לקרוא את רשימת שרתי המשנה: שגיאה 500/);
  assert.doesNotMatch(failed, /אין עדיין שרת משנה/);
  assert.match(ctx({BRANCH_READY: false}).settingsSecondariesTab(), /טוען…/);
});
