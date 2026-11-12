// #2781: finished/failed tasks in the task dock must show a final state,
// no progress bar, and never the "not yet read" text of a task that has not started.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../server/static');
const src = consoleJs();
function fn(name) {
  const lines = src.split(/\r?\n/);
  const start = lines.findIndex((l) => l.startsWith(`function ${name}(`));
  if (start < 0) return '';   // negative control: the helper does not exist at the base commit
  const end = lines.findIndex((l, i) => i > start && l === '}');
  return lines.slice(start, end + 1).join('\n');
}
function ctx(tasks, store = {}, skewMin = 0) {
  class SkewDate extends Date { static now() { return Date.now() + skewMin * 60000; } }
  const c = vm.createContext({
    Date: SkewDate,
    esc: String, OVERVIEW: null, HOME: {}, CAPTURE_TASKS: tasks, isAdmin: () => true,
    activityInitiator: () => 'x', ACTIVITY_UNKNOWN: '—', ACTIVITY_PILL: {done: 'ok', failed: 'err', cancelled: '', running: 'info', pending: 'info'},
    activityStart: String, fmtSpan: String, taskWhen: String, toast() {},
    localStorage: {getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; }},
    document: {querySelector: () => null}, $: () => null, overviewError: '', renderActivity() {},
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), c);
  vm.runInContext('const ACTIVITY_CLEARED_KEY = "imagesrv.activity.cleared"; const ACTIVITY_RECENT_MS = 600000; let SERVER_CLOCK_OFFSET = null;', c);
  for (const n of ['noteServerClock', 'serverNow', 'activityEndLabel', 'activityClearedAt', 'clearFinishedTasks', 'activityRows', 'activityDuration', 'activityRowHtml'])
    vm.runInContext(fn(n), c);
  return c;
}
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
const T = (o) => ({id: 'a', name: 'n', state: 'done', created_at: ago(20), updated_at: ago(6),
  bytes_written: 0, bytes_total: null, source_progress: null, error: null, ...o});
const rowsOf = (c) => vm.runInContext('activityRows()', c);

test('done capture: "הושלם", no bar, no 100%', () => {
  const c = ctx([T({bytes_written: 5e9, bytes_total: 5e9})]);
  const r = rowsOf(c).recent[0];
  assert.equal(r.label, 'הושלם');
  const html = vm.runInContext(`activityRowHtml(${JSON.stringify(r)})`, c);
  assert.doesNotMatch(html, /class="bar|100%|טרם נקראו/);
});
test('failed capture: "נכשל" with reason, never the unstarted text', () => {
  const c = ctx([T({state: 'failed', error: 'הדיסק לא נמצא'})]);
  const r = rowsOf(c).recent[0];
  assert.match(r.label, /^נכשל · הדיסק לא נמצא/);
  assert.equal(r.pct, null);
});
test('failed capture without reason: just "נכשל"', () => {
  assert.equal(rowsOf(ctx([T({state: 'failed'})])).recent[0].label, 'נכשל');
});
test('running capture that has not streamed keeps the unstarted text', () => {
  const r = rowsOf(ctx([T({state: 'running', updated_at: null})])).rows[0];
  assert.match(r.label, /טרם נקראו בייטים/);
});
test('clear: hides finished only, keeps running, persists, later task reappears', () => {
  const store = {}, six = ago(6);
  const c = ctx([T({id: '1', updated_at: six}), T({id: '2', state: 'failed', updated_at: six}), T({id: '3', state: 'running', updated_at: null})], store);
  vm.runInContext('clearFinishedTasks()', c);
  assert.ok(store['imagesrv.activity.cleared']);
  const r = rowsOf(c);
  assert.equal(r.recent.length, 0);
  assert.equal(r.rows.length, 1);
  const c2 = ctx([T({id: '1', updated_at: six}), T({id: '4', updated_at: ago(1)})], store);   // "refresh"
  assert.equal(rowsOf(c2).recent.length, 1);
});

// --- סבב 2: חלון 10 הדקות לפי שעון השרת (כמו Recent Tasks ב-vCenter) ---
test('finished 11 minutes ago is hidden, 9 minutes ago is shown', () => {
  const r = rowsOf(ctx([T({id: 'old', updated_at: ago(11)}), T({id: 'new', updated_at: ago(9)})]));
  assert.equal(r.recent.length, 1);
});
test('failed and cancelled follow the same window', () => {
  const r = rowsOf(ctx([T({state: 'failed', updated_at: ago(11)}), T({state: 'cancelled', updated_at: ago(11)}), T({state: 'failed', updated_at: ago(9)})]));
  assert.equal(r.recent.length, 1);
});
test('a running task from two days ago is still shown', () => {
  const r = rowsOf(ctx([T({state: 'running', created_at: ago(2 * 24 * 60), updated_at: ago(2 * 24 * 60)})]));
  assert.equal(r.rows.length, 1);
});
test('the window uses the SERVER clock: a browser clock 30 min ahead does not hide a fresh task', () => {
  // the browser clock runs 30 minutes ahead of the server; the task finished 2 minutes ago by the SERVER clock
  const c = ctx([T({updated_at: ago(2)})], {}, 30);
  assert.equal(rowsOf(c).recent.length, 0, 'sanity: with the skewed browser clock alone the task looks 32 minutes old');
  const serverTime = new Date(Date.now()).toUTCString();   // what the server's Date header says (true time)
  vm.runInContext(`noteServerClock({headers: {get: () => ${JSON.stringify(serverTime)}}})`, c);
  assert.equal(rowsOf(c).recent.length, 1, 'after reading the Date header the server clock decides');
});
test('no Date header: falls back to the browser clock, never NaN', () => {
  const c = ctx([T({updated_at: ago(2)})]);
  vm.runInContext('noteServerClock({headers: {get: () => null}})', c);
  assert.ok(Number.isFinite(vm.runInContext('serverNow()', c)));
  assert.equal(rowsOf(c).recent.length, 1);
});
test('all-tasks link exists in the panel header and goes to the journal page', () => {
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  assert.match(html, /id="allTasksLink"[^>]*onclick="selectPageById\('logs'\)"[^>]*>כל המשימות</);
  assert.match(html, /onclick="clearFinishedTasks\(\)"/, 'clear button stays');
});
