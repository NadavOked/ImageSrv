// Cloners page badge: Hebrew-only failure reason + no overflow. Runs the shipped console.js.
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


const LINE = 'FAILED: the work finished but the server never confirmed it';
const MAC = '78:ac:c0:9b:11:c2';

// 17/10 (owner): failure reasons are Hebrew only. The agent sends the English hold
// line as `prompt`; the console shows the Hebrew title the machine's own screen shows.
test('the hold lines reach the badge in Hebrew, never in English', () => {
  const run = setupConsole();
  const he = (p) => run(`promptHe(${JSON.stringify(p)})`);
  assert.equal(he(LINE), 'המשימה הסתיימה — והשרת לא אישר');
  assert.equal(he('FAILED: capture did not complete, and the server was not told'), 'הקליטה נכשלה — והשרת לא יודע');
  assert.equal(he('FAILED: direct deployment did not complete'), 'השליחה הישירה נכשלה');
  assert.equal(he('FAILED: restore did not complete'), 'השחזור נכשל');
  assert.equal(he('FAILED: the disk was skipped -- nothing written'), 'הדיסק דולג — לא נכתב דבר');
  assert.equal(he('FAILED: something nobody wrote a case for'), 'המשימה נכשלה');
  assert.equal(he('Replace the flagged disk: power off by hand'), 'החליפו את הדיסק שסומן: כיבוי ידני');
  assert.equal(he('menu'), 'תפריט');
});

test('the cloner card badge says it in Hebrew and may wrap inside the card', () => {
  const run = setupConsole();
  run(`MACHINES=[{mac:"${MAC}",name:"מחשב שיכפול 2",prompt:${JSON.stringify(LINE)},disks:[],online:true}]; NET=[{mac:"${MAC}",ip:"10.0.0.2",last_seen:Date.now()/1000}];`);
  const html = run(`clonerMachHtml(MACHINES[0], true)`);
  assert.match(html, /ממתין למפעיל: המשימה הסתיימה — והשרת לא אישר/);
  assert.doesNotMatch(html, /never confirmed|FAILED/);
  const css = read('console.css');
  assert.match(css, /\.page \.mach \.mt \.pill\{[^}]*white-space:normal[^}]*max-width:100%/);
});

// rc6, 18/10: the monitor page's "מחשבים מחוברים" showed cloner 2 as
// "ממתין למפעיל: Local clone screen: from the server, or a local clone".
test('the local clone questions reach the monitor list in Hebrew', () => {
  const run = setupConsole();
  const said = (p) => run(`waitingText({prompt: ${JSON.stringify(p)}})`);
  assert.equal(said('Local clone screen: from the server, or a local clone'), 'ממתין למפעיל: מסך הבחירה: מהשרת או שיכפול מקומי');
  assert.equal(said('Local clone at the machine -- not from the server'), 'ממתין למפעיל: שיכפול מקומי במחשב — לא מהשרת');
  assert.equal(said("Restore an image from the server onto this machine's drawers"), "ממתין למפעיל: שחזור אימג' מהשרת למגירות של המחשב הזה");
  const html = run(`monitorRowHtml({mac: "${MAC}", name: "מחשב שיכפול 2", ip: "10.44.0.196", online: true, prompt: "Local clone screen: from the server, or a local clone"})`);
  assert.match(html, /ממתין למפעיל: מסך הבחירה: מהשרת או שיכפול מקומי/);
  assert.doesNotMatch(html, /Local clone/);
});
