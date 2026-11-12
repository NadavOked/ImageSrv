// #1235 (follow-up of #105): a drive that reported no serial comes back from
// GET /room as `fresh: null` -- the round counts by serial and cannot tell
// whether it was written. The build machine's text screen and kiosk already
// say "cannot identify"; the console must say it too, by slot, and never for
// `fresh: false` (written) or a /machines disk record that has no `fresh`.
// Runs the shipped renderers from console.js, not copies.
const {consoleFiles} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');

function setupConsole() {
  const node = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
    classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, insertAdjacentHTML(_p, html) { this.innerHTML += html; },
    setAttribute() {}, removeAttribute() {}, focus() {}});
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, String, Array, encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelector: node, querySelectorAll: () => [], getElementById: node,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); }, get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false})}, localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(read('progress.js'), ctx);
  for (const f of consoleFiles()) vm.runInContext(read(f), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",capabilities:{}}; MACHINES=[]; GROUPS=[]; DISK_FAILURES=[]; NET=[]; ROOM=null;');
  return run;
}

const MAC = 'aa:bb:cc:00:12:35';
const drawer = (port, serial, fresh, extra = {}) =>
  ({dev: 'sd' + 'abc'[port - 1], port, serial, model: 'M', size_bytes: 256060514304, smart: 'ok', fresh, ...extra});
const MESSAGE = (n) => new RegExp(`לא ניתן לזהות את הכונן בחריץ ${n}`);

function room(round, drawers) {
  return JSON.stringify({round, machines: [{mac: MAC, name: 'CLONE-01', awake: true, joined: !!round,
    drawer_count: 3, fresh_drawers: 1, drawer_list: drawers}]});
}

test('deploy page (no round): a fresh:null drive says "cannot identify" by its slot', () => {
  const run = setupConsole();
  run(`ROOM = ${room(null, [drawer(1, 'SER-A', true), drawer(2, null, null), drawer(3, 'SER-C', false)])};`);
  const html = run('clonerCardHtml(roomMachines()[0], true, true)');
  assert.match(html, MESSAGE(2));
  assert.doesNotMatch(html, MESSAGE(1), 'a fresh drive with a serial is identified');
  assert.doesNotMatch(html, MESSAGE(3), 'fresh:false is "written" -- positive evidence, not unidentified');
});

test('live round (machines page grid): the same message on the drawer_list slot', () => {
  const run = setupConsole();
  const round = {state: 'active', wave_state: 'running', wave_number: 1, image_name: 'img'};
  run(`ROOM = ${room(round, [drawer(1, null, null, {state: 'writing', bytes_written: 1, bytes_total: 4})])};`);
  const m = JSON.stringify({mac: MAC, suffix: 'CLONE-01', group_id: 'grp_CLONERS', drawer_count: 3, disks: []});
  assert.match(run(`clonerCardHtml(${m}, true, false)`), MESSAGE(1));
});

test('a /machines disk record has no `fresh` at all -- that is not "cannot identify"', () => {
  const run = setupConsole();
  const disks = [{dev: 'sda', port: 1, serial: null, model: 'M', size_bytes: 1, smart: 'ok'}];
  const m = JSON.stringify({mac: MAC, suffix: 'CLONE-01', group_id: 'grp_CLONERS', drawer_count: 1, disks});
  assert.doesNotMatch(run(`clonerCardHtml(${m}, true, false)`), /לא ניתן לזהות/);
});
