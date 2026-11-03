// #1331 T8: drivers.linux (the check of the initramfs of a Linux target, docs/architecture/interfaces.md
// section 4/17) was in the contract and in the report but on no screen. The console says it now,
// in the same two renderers as drivers.boot: driversHtml (member row) and driversOutcomeText +
// driversTone (the drawer line). Only `generic` is evidence the disk boots on another bus.
// Runs the shipped console.js in a vm (same box as drivers_boot_console.test.cjs).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

function setup() {
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: stubNode, getElementById: stubNode,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run('ME={username:"u",role:"admin",idle_seconds:300,capabilities:{}}; toast=()=>{};');
  return run;
}


const LINUX = (linux) => ({state: 'skipped', packages: [], reason: 'not_windows', linux});
const text = (run, o) => run(`driversOutcomeText(${JSON.stringify(o)})`);
const tone = (run, o) => run(`driversTone(${JSON.stringify(o)})`);

test('#1331 T8 generic: grey, positive evidence, names the module; replaces "not Windows"', () => {
  const run = setup(), o = LINUX({state: 'generic', module: 'nvme', kver: '6.12.48+deb13-amd64'});
  assert.equal(text(run, o), 'לינוקס: קובץ האתחול כולל את מודול הדיסק (nvme) — נבדק, הדיסק אמור לעלות גם על בקר אחר');
  assert.equal(tone(run, o), 'muted');
  assert.doesNotMatch(text(run, o), /Windows/);
});

test('#1331 T8 hostonly: orange, "may not boot here" in the words of the task', () => {
  const run = setup(), o = LINUX({state: 'hostonly', module: 'ahci'});
  assert.equal(text(run, o), 'לינוקס: קובץ האתחול כולל רק את בקר הדיסק של המחשב המקורי — עלול לא לעלות כאן');
  assert.equal(tone(run, o), 'warn');
});

test('#1331 T8 unsupported and failed are "not checked" -- orange, never "fine"; the reason is shown', () => {
  const run = setup();
  const u = LINUX({state: 'unsupported', error: 'Alpine 3.20'}), f = LINUX({state: 'failed', error: 'update-initramfs: exit 1'});
  assert.match(text(run, u), /^לינוקס: קובץ האתחול לא נבדק \(Alpine 3\.20\) — לא ידוע אם הדיסק יעלה כאן$/);
  assert.match(text(run, f), /^לינוקס: בדיקת קובץ האתחול נכשלה — update-initramfs: exit 1 · לא ידוע אם הדיסק יעלה כאן$/);
  assert.equal(tone(run, u), 'warn'); assert.equal(tone(run, f), 'warn');
  assert.match(text(run, LINUX({state: 'failed'})), /סיבה לא ידועה/, 'no error text is said, not blank');
});

test('#1331 T8 skipped + unknown_bus is said out loud (grey, not hidden); another reason too', () => {
  const run = setup();
  const o = LINUX({state: 'skipped', reason: 'unknown_bus'});
  assert.equal(text(run, o), 'לינוקס: סוג החיבור של הדיסק לא מוכר — קובץ האתחול לא נבדק');
  assert.equal(tone(run, o), 'muted');
  assert.match(text(run, LINUX({state: 'skipped', reason: 'x'})), /דולגה — x$/);
});

test('#1331 T8 a malformed linux is dropped alone: the Windows line stays as it was', () => {
  const run = setup();
  assert.equal(text(run, LINUX({state: 'bogus'})), "דרייברים דולגו — האימג' אינו Windows");
  assert.equal(text(run, LINUX('x')), "דרייברים דולגו — האימג' אינו Windows");
  assert.equal(text(run, {state: 'staged', packages: ['a']}), 'דרייברים הונחו (חבילה אחת): a', 'no linux = unchanged');
});

test('#1331 T8 member row: orange notice for a warning, grey sub for generic; XSS in the error is escaped', () => {
  const run = setup();
  assert.match(run(`driversHtml(${JSON.stringify(LINUX({state: 'hostonly'}))})`), /^<div class="notice warn">לינוקס: קובץ האתחול כולל רק/);
  assert.match(run(`driversHtml(${JSON.stringify(LINUX({state: 'generic', module: 'nvme'}))})`), /^<div class="sub">לינוקס: .*\(nvme\)/);
  const x = run(`driversHtml(${JSON.stringify(LINUX({state: 'failed', error: '<img onerror=1>'}))})`);
  assert.doesNotMatch(x, /<img/); assert.match(x, /&lt;img/);
});

test('#1331 T8 a Linux warning is never the red "may not boot (0x7B)" of the Windows boot storage', () => {
  const run = setup();
  for (const s of ['hostonly', 'unsupported', 'failed']) assert.notEqual(tone(run, LINUX({state: s})), 'bad');
});
