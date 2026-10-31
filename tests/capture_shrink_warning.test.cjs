// #927: after a capture that ran fine but whose source-restore step failed
// (#87, agent/lib/shrink/shrink.sh writes the warning into the target's `error`),
// the server closes the task as `done` with that `error` still attached
// (reports.py COALESCE). Before this fix console.js only ever rendered the
// capture bar for tasks whose state was "pending"/"running" -- the moment a
// task reached `done` it vanished from #capture-bar, warning and all, and
// the operator had no way to learn the build machine's disk was left
// shrunk. Runs the shipped functions from server/static/console.js, not a
// copy of them.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../server/static');
const CONSOLE_JS = path.join(root, 'console.js');
const PROGRESS_JS = path.join(root, 'progress.js');

function functionSource(file, name) {
  const source = fs.readFileSync(file, 'utf8');
  const lines = source.split('\n');
  const start = lines.findIndex(
    (line) => new RegExp(`^(\\s*)(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `renderer ${name} exists in ${path.basename(file)}`);
  const indent = lines[start].match(/^\s*/)[0];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === indent + '}');
  return lines.slice(start, end + 1).join('\n');
}

function makeElements() {
  const elements = new Map();
  const $ = (key) => {
    if (!elements.has(key)) {
      elements.set(key, {innerHTML: '', textContent: '', style: {},
        classList: {add() {}, remove() {}, toggle() {}}, setAttribute() {}, removeAttribute() {}});
    }
    return elements.get(key);
  };
  return {elements, $};
}

// A finished capture task carrying the shrink-restore warning, shaped like
// what GET /api/console/tasks actually returns (server/library/capture.py, reports.py).
function warnedTask(overrides = {}) {
  return {
    id: 't1', mac: 'b4:2e:99:07:1a:c4', name: 'office365', state: 'done',
    error: 'sda: המקור לא הוחזר לגודלו אחרי הקליטה: ntfsresize could not grow the filesystem back',
    bytes_written: 20000000000, bytes_total: null, source_progress: null,
    ...overrides,
  };
}

async function runLoadCaptures(tasks) {
  const {elements, $} = makeElements();
  const context = vm.createContext({
    document: {querySelectorAll: () => []},
    $, esc: String, fmtBytes: (n) => `${n} bytes`,
    api: async () => tasks,
    renderActivity() {},
    CAPTURE_TASKS: [],
  });
  vm.runInContext(fs.readFileSync(PROGRESS_JS, 'utf8'), context);
  vm.runInContext(functionSource(CONSOLE_JS, 'captureWarningHtml'), context);
  vm.runInContext(functionSource(CONSOLE_JS, 'loadCaptures'), context);
  await vm.runInContext('loadCaptures()', context);
  return $('#capture-bar').innerHTML;
}

test('a done capture task with a restore warning is not silently dropped', async () => {
  const html = await runLoadCaptures([warnedTask()]);
  assert.match(html, /המקור לא הוחזר לגודלו/, 'the full warning text reaches the DOM');
  assert.match(html, /class="note warn"/, 'rendered with the orange note style (#954 .page), not swallowed');
});

test('a done capture task with a restore warning is not shown as green success', async () => {
  const html = await runLoadCaptures([warnedTask()]);
  // #829: reuse the existing warn styling -- never the plain "done" bar markup
  // used for a capture that finished with no error.
  assert.doesNotMatch(html, /class="note ok"|class="notice ok"|class="notice success"/);
});

test('a done capture task with no error still renders nothing (unchanged behaviour)', async () => {
  const html = await runLoadCaptures([warnedTask({error: null})]);
  assert.equal(html, '');
});

test('an in-progress capture task is not a warning: it is a row in the library table (#954 gal 2)', async () => {
  // The running capture is rendered by captureRowHtml inside the datagrid
  // (tests/images_library.test.cjs); the bar above the table carries only
  // done+error warnings, so it stays empty here.
  const html = await runLoadCaptures([warnedTask({id: 't2', state: 'running', error: null})]);
  assert.equal(html, '');
});

test('captureWarningHtml ignores a still-open task even if error is set', () => {
  // #106: error is populated on a rejected-before-any-byte-moved task too --
  // that is a *rejection*, not the done-with-warning case #927 is about, and
  // showing it here would duplicate whatever "failed" surfacing exists.
  const context = vm.createContext({esc: String});
  vm.runInContext(functionSource(CONSOLE_JS, 'captureWarningHtml'), context);
  const html = vm.runInContext(
    `captureWarningHtml(${JSON.stringify(warnedTask({state: 'running'}))})`, context);
  assert.equal(html, '');
});

test('machine screen shows the last done+error capture task for that MAC', () => {
  const context = vm.createContext({esc: String,
    CAPTURE_TASKS: [warnedTask(), warnedTask({id: 't0', mac: 'aa:bb:cc:dd:ee:ff'})]});
  vm.runInContext(functionSource(CONSOLE_JS, 'machineCaptureWarningHtml'), context);
  const html = vm.runInContext(`machineCaptureWarningHtml("b4:2e:99:07:1a:c4")`, context);
  assert.match(html, /המקור לא הוחזר לגודלו/);
});

test('machine screen shows nothing for a MAC with no warned task', () => {
  const context = vm.createContext({esc: String, CAPTURE_TASKS: [warnedTask()]});
  vm.runInContext(functionSource(CONSOLE_JS, 'machineCaptureWarningHtml'), context);
  const html = vm.runInContext(`machineCaptureWarningHtml("00:00:00:00:00:00")`, context);
  assert.equal(html, '');
});

test('renderActivity does not count a finished, warned capture as an active transfer', () => {
  const {elements, $} = makeElements();
  const taskPanelBody = {innerHTML: ''};
  const context = vm.createContext({
    document: {querySelector: (sel) => (sel === '#taskPanel tbody' ? taskPanelBody : null)},
    $, esc: String, isAdmin: () => true, OVERVIEW: {}, sessionImage: () => '', sessionGroup: () => '',
    Progress: {view: () => ({label: ''})}, taskWhen: () => '', sessionStateText: () => '', Date, Number, Math,
    CAPTURE_TASKS: [warnedTask()], overviewError: '',
  });
  // ‏#1255: renderActivity נשען על עוזרי הפאנל (משך/יוזם) שמוגדרים לידו
  const src = require('node:fs').readFileSync(CONSOLE_JS, 'utf8');
  vm.runInContext(src.slice(src.indexOf('const ACTIVITY_UNKNOWN'), src.indexOf('function editImageDescription')), context);
  vm.runInContext('renderActivity()', context);
  assert.match(taskPanelBody.innerHTML, /אין העברות פעילות/,
    'a done task, even one with a lingering warning, is not "an active transfer"');
});
