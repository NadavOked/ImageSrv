// #1331: the boot-storage outcome (drivers.boot -- bootstorage.sh) reaches the
// server (reports.well_formed_drivers keeps it, members[].drivers and
// drawer_list[].drivers carry it, boot_storage_failed is journaled), but the
// console drew only drivers.state. "The image has no stornvme service -- this
// disk will not boot" was a journal line and nothing next to the machine
// (עיקרון 5). Both renderers -- driversHtml (member row) and
// driversOutcomeText (drawer line) -- now say what bootstorage.sh did.
// Runs the shipped console.js in a vm, not a copy of the functions
// (same box as hostname_warning.test.cjs).
const {consoleFiles} = require('./console_src.cjs');
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
  for (const f of consoleFiles()) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run('ME={username:"u",role:"admin",idle_seconds:300,capabilities:{}}; toast=()=>{};');
  return run;
}

const STAGED = {state: 'staged', packages: ['intel-nic']};
const FAILED_BOOT = {state: 'failed', service: 'stornvme', error: "באימג' אין את השירות stornvme"};

test('#1331 member row (mockup v2): a failed boot-storage is red next to the machine -- the service, the reason, and "may not boot (0x7B)"', () => {
  const run = setup();
  const html = run(`driversHtml(${JSON.stringify({...STAGED, boot: FAILED_BOOT})})`);
  assert.match(html, /^<div class="sub">דרייברים הונחו \(חבילה אחת\): intel-nic<\/div>/, 'the staging line stays, with its count');
  assert.match(html, /<div class="err">דרייבר האחסון stornvme לא הוגדר לעלייה — באימג&#39; אין את השירות stornvme\. הדיסק עלול לא לעלות \(0x7B\) — לבדוק לפני שמחזירים לכיתה\.<\/div>$/);
});

test('#1331 member row: enabled says so (positive evidence, grey), already and skipped add nothing; two packages are counted', () => {
  const run = setup();
  const enabled = run(`driversHtml(${JSON.stringify({state: 'staged', packages: ['hp-800g6-win11', 'realtek-nic'], boot: {state: 'enabled', service: 'stornvme'}})})`);
  assert.equal(enabled, '<div class="sub">דרייברים הונחו (2 חבילות): hp-800g6-win11, realtek-nic</div><div class="sub">דרייבר האחסון stornvme הוגדר לעלייה</div>');
  const plain = run(`driversHtml(${JSON.stringify(STAGED)})`);
  for (const boot of [{state: 'already', service: 'storahci'}, {state: 'skipped', reason: 'unknown_bus'}]) {
    const html = run(`driversHtml(${JSON.stringify({...STAGED, boot})})`);
    assert.equal(html, plain, `${boot.state}: nothing to do, nothing added`);
  }
  assert.equal(run('driversHtml({state:"staged",packages:["p"],boot:{state:"failed"}})').includes('class="err"'), true,
    'a failed boot without service/error (server caps) is still a visible failure');
});

test('#1331 member row: drivers not placed is orange (the restore is done, #856), not red', () => {
  const run = setup();
  assert.equal(run('driversHtml({state:"failed",packages:[],error:"לא הצלחנו לעגן את מחיצת Windows"})'),
    '<div class="notice warn">דרייברים לא הונחו — לא הצלחנו לעגן את מחיצת Windows. המחשב יעלה בלי הדרייברים של הדגם.</div>');
  assert.equal(run('driversHtml({state:"no_match",packages:[]})'), '<div class="sub">דרייברים: אין חבילה תואמת לחומרה</div>');
});

test('#1331 drawer line (mockup v2): the words and the colour of each state', () => {
  const run = setup();
  const line = (o) => [run(`driversOutcomeText(${JSON.stringify(o)})`), run(`driversTone(${JSON.stringify(o)})`)];
  assert.deepEqual(line({...STAGED, boot: FAILED_BOOT}),
    ["דרייברים הונחו (חבילה אחת): intel-nic · דרייבר האחסון stornvme לא הוגדר לעלייה — באימג' אין את השירות stornvme", 'bad']);
  assert.deepEqual(line({state: 'staged', packages: ['a', 'b'], boot: {state: 'enabled', service: 'stornvme'}}),
    ['דרייברים הונחו (2 חבילות): a, b · דרייבר האחסון stornvme הוגדר לעלייה', 'muted']);
  assert.deepEqual(line({...STAGED, boot: {state: 'already', service: 'storahci'}}), ['דרייברים הונחו (חבילה אחת): intel-nic', 'muted'], 'already adds nothing');
  assert.deepEqual(line({state: 'failed', packages: [], error: 'לא הצלחנו לעגן את מחיצת Windows'}),
    ['דרייברים לא הונחו — לא הצלחנו לעגן את מחיצת Windows', 'warn']);
  assert.deepEqual(line({state: 'skipped', packages: [], reason: 'no_model'}), ['דרייברים: לא נבחר דגם למגירה — לא הונחו', 'muted']);
  assert.deepEqual(line({state: 'no_match', packages: []}), ['דרייברים: אין חבילה תואמת לחומרה', 'muted']);
});
