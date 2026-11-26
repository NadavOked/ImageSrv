// #2551 (mockup m2551-v2, nadav 22/11): the images table column is "עודכן" (was "נקלט"), the date is
// the newest version of the lineage (`updated`), and "ב-VM" sits under it when that version came
// from a VM update. The drawer subtitle says the same. No "stale" mark. Runs the shipped console.js.
const {consoleJs, consoleFiles} = require('./console_src.cjs');
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

const VM_IMG = {id: 'img_a10001', name: 'office365', os: 'windows', total_compressed_bytes: 38.2e9,
  created: '2026-03-03T12:00:00Z', updated: '2026-11-10T12:00:00Z', updated_origin: 'vm'};
const CAP_IMG = {id: 'img_b20001', name: 'win11-lab', os: 'windows', total_compressed_bytes: 33.5e9,
  created: '2026-10-20T12:00:00Z', updated: '2026-10-20T12:00:00Z', updated_origin: 'capture'};

test('the column is "עודכן" with a "עודכן לאחרונה" hint, and "נקלט" is gone from the header', () => {
  const run = setup();
  run(`IMAGES=[${JSON.stringify(VM_IMG)}]; IMAGES_FOLDER=null; FOLDERS=[];`);
  const table = run('images()');
  assert.match(table, /<th class="c-opt"><span title="עודכן לאחרונה">עודכן<\/span><\/th>/);
  assert.doesNotMatch(table, /<th[^>]*>נקלט<\/th>/);
});

test('the cell shows the newest version date, and "ב-VM" under it only when it came from a VM', () => {
  const run = setup();
  assert.equal(run(`imgUpdatedCell(${JSON.stringify(VM_IMG)})`),
    '<bdi dir="ltr">10/11/2026</bdi><span class="sub muted">ב-VM</span>');
  assert.equal(run(`imgUpdatedCell(${JSON.stringify(CAP_IMG)})`), '<bdi dir="ltr">20/10/2026</bdi>');
  // an older server without the field: the capture date, never a blank
  assert.equal(run(`imgUpdatedCell(${JSON.stringify({created: '2026-10-20T12:00:00Z'})})`), '<bdi dir="ltr">20/10/2026</bdi>');
});

test('the library table renders the VM row with the "ב-VM" line and the other without', () => {
  const run = setup();
  run(`IMAGES=[${JSON.stringify(VM_IMG)},${JSON.stringify(CAP_IMG)}]; IMAGES_FOLDER=null; FOLDERS=[];`);
  const table = run('images()');
  assert.match(table, /<bdi dir="ltr">10\/11\/2026<\/bdi><span class="sub muted">ב-VM<\/span>/);
  assert.match(table, /<bdi dir="ltr">20\/10\/2026<\/bdi><\/td>/);
  assert.doesNotMatch(table, /התיישן|ישן/);
});

test('the drawer subtitle says "עודכן ב-VM <date>" or "עודכן <date>"', () => {
  const run = setup();
  assert.equal(run(`imgUpdatedWord(${JSON.stringify(VM_IMG)})`), 'עודכן ב-VM');
  assert.equal(run(`imgUpdatedWord(${JSON.stringify(CAP_IMG)})`), 'עודכן');
  const drawer = run(`imageDrawerHtml(${JSON.stringify(VM_IMG)})`);
  assert.match(drawer, /עודכן ב-VM <bdi dir="ltr">10\/11\/2026<\/bdi>/);
  assert.doesNotMatch(drawer, /נקלט <bdi/);
  assert.doesNotMatch(consoleJs(), /· נקלט \$\{ltr\(fmtDate\(img\.created\)\)\}/);
});
