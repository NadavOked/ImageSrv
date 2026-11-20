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

// #3418: since #1331 (owner's decision 15/11) the storage driver is made boot-start next to EVERY drivers
// state, also with no package (boot_storage_alone). The console drew drivers.boot only next to `staged`, so a
// failed step beside no_match / no_model / failed was a journal line and nothing next to the machine (principle 5).
// The backslashes are doubled on purpose: RISK and the template go into a RegExp, and a single backslash in a JS
// string literal is dropped, which left "(0x7B)" as a capture group matching "0x7B" without the parentheses the console prints.
const RISK = 'הדיסק עלול לא לעלות \\(0x7B\\) — לבדוק לפני שמחזירים לכיתה';
const ERR_LINE = new RegExp(`<div class="err">דרייבר האחסון stornvme לא הוגדר לעלייה — באימג&#39; אין את השירות stornvme\\. ${RISK}\\.</div>$`);

test('#3418 member row: a failed boot-storage is the same red line next to no_match / no_model / failed -- the existing line stays above it', () => {
  const run = setup();
  const nm = run(`driversHtml(${JSON.stringify({state: 'no_match', packages: [], boot: FAILED_BOOT})})`);
  assert.match(nm, /^<div class="sub">דרייברים: אין חבילה תואמת לחומרה<\/div>/);
  assert.match(nm, ERR_LINE);
  const skipped = run(`driversHtml(${JSON.stringify({state: 'skipped', packages: [], reason: 'no_model', boot: FAILED_BOOT})})`);
  assert.match(skipped, /^<div class="err">/, 'skipped drew nothing before: now only the red line');
  assert.match(skipped, ERR_LINE);
  const failed = run(`driversHtml(${JSON.stringify({state: 'failed', packages: [], error: 'לא הצלחנו לעגן', boot: FAILED_BOOT})})`);
  assert.match(failed, /^<div class="notice warn">דרייברים לא הונחו — לא הצלחנו לעגן\./);
  assert.match(failed, ERR_LINE);
});

test('#3418 member row: enabled / already / skipped beside a state that is not staged add nothing (the grey stays)', () => {
  const run = setup();
  for (const state of [{state: 'no_match', packages: []}, {state: 'skipped', packages: [], reason: 'no_model'},
                       {state: 'failed', packages: [], error: 'x'}]) {
    const plain = run(`driversHtml(${JSON.stringify(state)})`);
    for (const boot of [{state: 'enabled', service: 'stornvme'}, {state: 'already', service: 'storahci'}, {state: 'skipped', reason: 'unknown_bus'}]) {
      assert.equal(run(`driversHtml(${JSON.stringify({...state, boot})})`), plain, `${state.state} + boot ${boot.state}`);
    }
  }
});

test('#3418 drawer line: the red words and the red tone for every state; the other tones do not move', () => {
  const run = setup();
  const line = (o) => [run(`driversOutcomeText(${JSON.stringify(o)})`), run(`driversTone(${JSON.stringify(o)})`)];
  const boot = "דרייבר האחסון stornvme לא הוגדר לעלייה — באימג' אין את השירות stornvme";
  assert.deepEqual(line({state: 'no_match', packages: [], boot: FAILED_BOOT}), [`דרייברים: אין חבילה תואמת לחומרה · ${boot}`, 'bad']);
  assert.deepEqual(line({state: 'skipped', packages: [], reason: 'no_model', boot: FAILED_BOOT}), [`דרייברים: לא נבחר דגם למגירה — לא הונחו · ${boot}`, 'bad']);
  assert.deepEqual(line({state: 'failed', packages: [], error: 'לא הצלחנו לעגן', boot: FAILED_BOOT}), [`דרייברים לא הונחו — לא הצלחנו לעגן · ${boot}`, 'bad']);
  assert.deepEqual(line({state: 'skipped', packages: [], reason: 'not_windows', boot: FAILED_BOOT}), [`דרייברים דולגו — האימג' אינו Windows · ${boot}`, 'bad']);
  assert.deepEqual(line({state: 'no_match', packages: [], boot: {state: 'enabled', service: 'stornvme'}}), ['דרייברים: אין חבילה תואמת לחומרה', 'muted']);
  assert.deepEqual(line({state: 'failed', packages: [], error: 'x', boot: {state: 'already'}}), ['דרייברים לא הונחו — x', 'warn']);
  assert.equal(run('bayEmphasis({disk:{rule:{color:"green"},drivers:{state:"no_match",boot:{state:"failed"}}},live:{}})'), ' bay-emphasis err',
    'the bay of a disk whose boot-storage failed is red whatever the drivers state');
});
