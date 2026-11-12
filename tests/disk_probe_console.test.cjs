// #402: "0 דיסקים" are three different findings with opposite actions, and
// the console must say which one it is. The agent sends `disk_probe` in hello
// (interfaces.md section 2): no_disks = SATA ports are up, no drive plugged
// (plug a drive); no_ports = the controller reports 0 ports -- disabled in
// firmware (go to the BIOS, a cable will not help); unchecked = we could not
// count (no ahci line in dmesg), which is NOT no_disks (rule 5); null = an old
// agent that never sent the field, which keeps the old text rather than a
// guess. Runs the shipped renderers from console.js, not copies.
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

const MAC = 'aa:bb:cc:00:04:02';
const zero = (probe) => JSON.stringify({mac: MAC, suffix: 'shich-402', group_id: 'grp_CLONERS', drawer_count: null, disks: [], disk_probe: probe});

const CASES = [
  ['no_disks', 'לא חוברו דיסקים'],
  ['no_ports', 'אין פורטי SATA בקושחה'],
  ['unchecked', 'לא נבדק'],
];

test('the machines table: 0 disks says why -- three different texts for three different findings', () => {
  const run = setupConsole();
  const texts = new Set();
  for (const [probe, expected] of CASES) {
    const html = run(`disksCell(${zero(probe)})`);
    assert.match(html, new RegExp(expected), probe);
    assert.doesNotMatch(html, /דיווח 0 דיסקים/, probe + ': the bare count is exactly the collapse #402 is about');
    texts.add(html);
  }
  assert.equal(texts.size, 3, 'three findings, three texts -- never the same line twice');
});

test('unchecked is not no_disks: "could not count" never reads as "plug a drive"', () => {
  const run = setupConsole();
  const html = run(`disksCell(${zero('unchecked')})`);
  assert.doesNotMatch(html, /לא חוברו/);
  assert.match(html, /לא נבדק/);
});

test('an old agent (no disk_probe) keeps the old text -- the console does not guess', () => {
  const run = setupConsole();
  const html = run(`disksCell(${zero(null)})`);
  assert.match(html, /דיווח 0 דיסקים/);
  assert.doesNotMatch(html, /לא חוברו|פורטי SATA|לא נבדק/);
});

test('the cloner card (no slots configured) and the machine drawer carry the same reason', () => {
  const run = setupConsole();
  for (const [probe, expected] of CASES) {
    assert.match(run(`clonerCardHtml(${zero(probe)}, true, false)`), new RegExp(expected), 'card ' + probe);
    assert.match(run(`diskBoxesHtml(${zero(probe)})`), new RegExp(expected), 'drawer ' + probe);
  }
});

test('the room merges disk_probe from /room (per machine) so the deploy page sees it without /machines', () => {
  const run = setupConsole();
  run(`ROOM={round:null,stream_stalled:false,disk_floor:null,machines:[{mac:'${MAC}',name:'shich-402',drawer_count:null,awake:true,drawers:0,disk_probe:'no_ports',fresh_drawers:0,drawer_list:[],joined:false,state:null,bytes_written:0,bytes_total:0,error:null}]};`);
  const kids = run('roomMachines()');
  assert.equal(kids.length, 1);
  assert.equal(kids[0].disk_probe, 'no_ports');
  assert.match(run('clonerCardHtml(roomMachines()[0], true, true)'), /אין פורטי SATA בקושחה/);
});
