// #2885: a direct send in the task dock says what happened to the drawers -- "X מתוך Y דיסקים נכתבו"
// from /tasks[].round -- not "ספריית האימג'ים", and a failed round is red with its reason even when the
// task itself is done (the source streamed, the drawers were not written).
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
function ctx(tasks) {
  const c = vm.createContext({
    esc: String, OVERVIEW: null, HOME: {}, CAPTURE_TASKS: tasks, isAdmin: () => true,
    activityInitiator: () => 'x', ACTIVITY_UNKNOWN: 'לא ידוע',
    ACTIVITY_PILL: {done: 'ok', failed: 'err', cancelled: '', running: 'info', pending: 'info'},
    activityStart: String, fmtSpan: String, taskWhen: String, toast() {},
    localStorage: {getItem: () => null, setItem() {}},
    document: {querySelector: () => null}, $: () => null, overviewError: '', renderActivity() {},
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), c);
  vm.runInContext('const ACTIVITY_CLEARED_KEY = "k"; const ACTIVITY_RECENT_MS = 600000; let SERVER_CLOCK_OFFSET = null;', c);
  for (const n of ['noteServerClock', 'serverNow', 'directTaskRound', 'activityEndLabel', 'activityClearedAt', 'activityRows'])
    vm.runInContext(fn(n), c);
  return c;
}
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
const T = (o) => ({id: 'a', type: 'direct_send', name: 'n', state: 'done', created_at: ago(20), updated_at: ago(2),
  bytes_written: 0, bytes_total: null, source_progress: null, error: null, folder: '', ...o});
const R = (o) => ({id: 'room_1', state: 'closed', target_drives: 3, written_drives: 3, skipped_drives: 0, failed_reason: null, ...o});
const recent = (t) => vm.runInContext('activityRows()', ctx([t])).recent[0];

test('a finished direct send names its drawers, not the library', () => {
  const r = recent(T({round: R({written_drives: 2})}));
  assert.equal(r.target, '2 מתוך 3 דיסקים נכתבו');
  assert.equal(r.label, 'הושלם');
});
test('a failed round is red with its reason, even when the task is done', () => {
  const r = recent(T({round: R({state: 'failed', written_drives: 0, failed_reason: 'המשכפל הפסיק לדווח באימות'})}));
  assert.equal(r.target, '0 מתוך 3 דיסקים נכתבו');
  assert.equal(r.label, 'נכשל · המשכפל הפסיק לדווח באימות');
  assert.equal(r.cls, 'err');
});
test('no round is "unknown", never the library and never a number', () => {
  assert.equal(recent(T({round: null})).target, 'לא ידוע');
  assert.equal(recent(T({round: R({written_drives: null})})).target, 'לא ידוע');
});
test('a capture keeps its folder', () => {
  assert.equal(recent(T({type: 'capture', folder: 'win11', round: null})).target, 'תיקיית win11');
});
