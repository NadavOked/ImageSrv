// #1634: hardening the console against XSS. Every check feeds a hostile value
// through the real console.js and looks at what the browser would run.
//
// The sinks:
// (1) on*="fn('${esc(x)}')". esc() is HTML escaping, and the browser decodes
//     &#39; back to ' before the JS runs. So x = "x');pwn;//" ran as code. Each
//     handler is decoded here the way the browser decodes it, run in a sandbox,
//     and checked for two things: nothing ran, and the function got the
//     original value (jsStr = encodeId + decodeURIComponent).
// (2) cls in UI.status/pill/note/kpi/card/datagrid/barRow/timeline and in
//     vmuNote went into class="" without esc.
// (3) openModalContent ran new Function(okFn) on a string.
// (4) A static guard: no '${esc(' inside an on*="..." handler is left
//     anywhere in server/static.
const {consoleFiles} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

// One string that ends a '...' literal, runs code, and carries a quote and a tag.
const EVIL = `x');globalThis.pwned=1;//"<b>&#39;`;

function setup() {
  const nodes = new Map();
  const node = (key) => {
    if (!nodes.has(key)) nodes.set(key, {key, value: '', innerHTML: '', textContent: '', className: '', hidden: false, dataset: {}, style: {removeProperty() {}},
      classList: {add() {}, remove() {}, toggle() {}, contains: () => false}, addEventListener() {}, removeEventListener() {}, remove() {},
      querySelector: () => null, querySelectorAll: () => [], setAttribute() {}, removeAttribute() {}, hasAttribute: () => false, focus() {}});
    return nodes.get(key);
  };
  const ctx = vm.createContext({console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, String, Array, encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelector: node, querySelectorAll: () => [], getElementById: (id) => node('#' + id),
      createElement: () => node('new'), documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false})}, localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})})});
  for (const f of ['progress.js', ...consoleFiles(), 'disks.js', 'vmupdate.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx, {filename: f});
  const run = (s) => vm.runInContext(s, ctx);
  run('ME={timezone:"UTC",username:"admin",role:"admin",capabilities:{classrooms:true}};');
  return {ctx, run, node};
}

// What the browser does to an attribute value before it hands it to the JS engine.
const decodeAttr = (s) => s.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&#96;/g, '`')
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

// The handlers in html that call fn: [attribute, decoded JS].
function handlers(html, fn) {
  return [...html.matchAll(/\s(on[a-z]+)="([^"]*)"/g)].map((m) => [m[1], decodeAttr(m[2])]).filter(([, js]) => js.includes(fn + '('));
}

// Runs one handler in a sandbox where fn only records its arguments. Returns [calls, pwned].
function fire(js, fn) {
  const calls = [];
  const sandbox = {[fn]: (...args) => calls.push(args), decodeURIComponent, event: {stopPropagation() {}}};
  vm.createContext(sandbox);
  // the handler on its own lines: a payload ending in // comments out the rest of its line, as in the browser
  vm.runInContext(`(function(){\n${js}\n}).call({checked:true,value:'0',closest:()=>({})})`, sandbox);
  return [calls, sandbox.pwned];
}

// Every handler that calls fn: nothing ran, and argument `at` came through as EVIL.
function assertNeutralised(html, fn, at, expected = EVIL) {
  const hs = handlers(html, fn);
  assert.ok(hs.length > 0, `no ${fn}( handler in:\n${html.slice(0, 400)}`);
  for (const [attr, js] of hs) {
    const [calls, pwned] = fire(js, fn);
    assert.equal(pwned, undefined, `${attr} ran the injected code: ${js}`);
    assert.equal(calls.length, 1, `${attr}: ${fn} called once: ${js}`);
    assert.equal(calls[0][at], expected, `${attr}: ${fn} got the original value`);
  }
}

// --- (1) the handler sites ---------------------------------------------------------------

test('poweroffMissing: a missing machine whose mac is hostile', () => {
  const {run} = setup();
  run('roomOperator=()=>true;');
  const html = run(`roomMissingHtml({wave_state:'open',ready_drives:1,missing:[{mac:${JSON.stringify(EVIL)},text_he:'t',can_poweroff:true}]})`);
  assertNeutralised(html, 'poweroffMissing', 0);
});

/* answerSmart/roomAskNote were removed with the SMART question (#2044) — nothing left to harden there. */

test('toolsToggle: a tool id', () => {
  const {run} = setup();
  run('TOOLS_DRAFT={build:new Set(),student:new Set()};');
  const row = run(`toolRowHtml({id:${JSON.stringify(EVIL)},title_he:'t',risk:'ro',what:'',binary:'b'})`);
  assertNeutralised(row.cells.join(''), 'toolsToggle', 0);
});

test('the tree: a group id under a role (fillSidebarTree) opens and closes its own box', () => {
  const {run, node} = setup();
  run(`sidebarChildOpen=()=>({}); fillSidebarTree('buildTree',[{id:${JSON.stringify(EVIL)},label:'a'},{id:'g2',label:'b'}],
    [{mac:'m1',name:'HP-1',group:${JSON.stringify(EVIL)}}],'build')`);   // g2 has no machines: a leaf, no box
  const html = node('#buildTree').innerHTML;
  assertNeutralised(html, 'toggleInventoryGroup', 1, 'sg-' + EVIL);
  assertNeutralised(html, 'treeNavToggle', 1, 'sg-' + EVIL);
});

test("the tree: a secondary's own boxes (branchTreeHtml), with a hostile node id", () => {
  const {run} = setup();
  const html = run(`branchTreeHtml({id:${JSON.stringify(EVIL)},label:'n'},{})`);
  const ids = handlers(html, 'toggleInventoryGroup').map(([, js]) => fire(js, 'toggleInventoryGroup')[0][0][1]);
  assert.ok(ids.length >= 4 && ids.every((id) => id.startsWith(`srv-${EVIL}-`)), ids.join(' | '));
  for (const fn of ['toggleInventoryGroup', 'treeNavToggle']) {
    for (const [attr, js] of handlers(html, fn)) assert.equal(fire(js, fn)[1], undefined, `${attr}: ${js}`);
  }
});

test('the disks page: dkPick (a serial) and diskReplaceSheet (a suffix)', () => {
  const {run} = setup();
  run(`DK.disks=[{serial:${JSON.stringify(EVIL)},state:'ok'}]; DK.picked=new Set(); dkFiltered=()=>DK.disks;`);
  assertNeutralised(run('dkListTab(false)'), 'dkPick', 0);
  run(`DK.sets=[{id:7,name:'s',prefix:'xx',capacity:1,students:1,assigned:1,instructor:false,counts:{},grid:[],plan:[]}]; DK.selSet=7;
    DK.members=[{serial:'S1',suffix:${JSON.stringify(EVIL)},state:'red'}];`);
  assertNeutralised(run('dkSetsTab()'), 'diskReplaceSheet', 1);
});

// #2352: "select all" carried the serials as raw JSON inside onchange='...'. A serial
// (from hello, deploy VLAN, no auth) with a ' closed the attribute and opened live ones.
test('#2352: a serial with a quote cannot open attributes on "select all", and the list arrives intact', () => {
  const {run} = setup();
  const serials = ["WD-1' autofocus onfocus='globalThis.pwned=1", 'S"2', 'S3'];
  run(`DK.disks=${JSON.stringify(serials.map((serial) => ({serial, state: 'ok'})))}; DK.picked=new Set(); dkFiltered=()=>DK.disks;`);
  const html = run('dkListTab(false)');
  const start = html.indexOf('<input type="checkbox" aria-label="סימון הכול"');
  assert.ok(start >= 0, html.slice(0, 300));
  const tag = html.slice(start, html.indexOf('>', start) + 1);
  // the attributes the browser would see, with either quote
  const attrs = [...tag.slice('<input'.length).matchAll(/\s([a-z-]+)(?:=(?:"([^"]*)"|'([^']*)'))?/g)]
    .map((m) => [m[1], m[2] ?? m[3] ?? '']);
  assert.deepEqual(attrs.map(([n]) => n), ['type', 'aria-label', 'onchange'], `attributes: ${tag}`);
  const js = decodeAttr(attrs[2][1]);
  const calls = [];
  const sandbox = {dkPickAll: (...a) => calls.push(a), decodeURIComponent, JSON};
  vm.createContext(sandbox);
  vm.runInContext(`(function(){\n${js}\n}).call({checked:true})`, sandbox);
  assert.equal(sandbox.pwned, undefined, js);
  assert.equal(calls.length, 1, js);
  assert.deepEqual(calls[0], [true, serials], 'every serial, unchanged');
});

test('a plain value is passed unchanged: jsStr is decodeURIComponent of encodeId, nothing else', () => {
  const {run} = setup();
  for (const v of ['aa:bb:cc:dd:ee:ff', 'sda@2', 'אימג׳ 1', "it's", '100%', '']) {
    const js = run(`jsStr(${JSON.stringify(v)})`);
    assert.doesNotMatch(js.slice("decodeURIComponent('".length, -2), /['"&<>`\\]/, js);
    assert.equal(vm.runInNewContext(js, {decodeURIComponent}), v);
  }
  assert.equal(vm.runInNewContext(run('jsStr(null)'), {decodeURIComponent}), '', 'null is "", like esc(null)');
});

// --- (2) cls in the helpers --------------------------------------------------------------

test('cls cannot leave class="" in the UI helpers and in vmuNote', () => {
  const {run} = setup();
  const C = JSON.stringify('x" onmouseover="globalThis.pwned=1');
  const outs = {
    status: run(`UI.status(${C},'t')`), pill: run(`UI.pill(${C},'t')`), note: run(`UI.note(${C},'t')`),
    kpi: run(`UI.kpi({cls:${C},label:'l',value:1})`), card: run(`UI.card({title:'t',body:'b',cls:${C}})`),
    datagrid: run(`UI.datagrid({columns:['a'],rows:[['1']],cls:${C}})`), barRow: run(`UI.barRow(5,${C})`),
    timeline: run(`UI.timeline([{t:'1',cls:${C},text:'x'}])`), vmuNote: run(`vmuNote(${C},'t')`),
  };
  for (const [name, html] of Object.entries(outs)) {
    assert.doesNotMatch(html, /onmouseover="/, `${name}: a live attribute: ${html}`);
    assert.match(html, /x&quot; onmouseover=&quot;/, `${name} keeps the value, escaped: ${html}`);
  }
});

// --- (3) openModalContent ----------------------------------------------------------------

test('openModalContent takes a function and refuses a string -- no new Function', () => {
  const {run, node} = setup();
  run('wireWindowParts=()=>{}; openModalFrame=()=>{};');
  assert.throws(() => run('openModalContent("t","<p>x</p>","ok","globalThis.pwned=1")'), /okFn must be a function/);
  assert.equal(run('globalThis.pwned'), undefined);
  run('globalThis.okRan=0; openModalContent("t","<p>x</p>","ok",()=>{globalThis.okRan++})');
  node('#modalOk').onclick(); assert.equal(run('okRan'), 1);
  run('openModalContent("t","<p>x</p>")');
  assert.equal(node('#modalOk').onclick, run('closeModal'), 'the default is closeModal itself');
});

// --- (4) static guard over all of server/static ------------------------------------------

// #2422: the guard looked only for '${esc( -- esc right after a quote. esc() is HTML escaping and is wrong
// anywhere inside a JS handler, so it now catches every ${…esc(…} in on*="…": unquoted, after &quot;, or
// inside a ternary. The samples below are the guard's own negative control -- the old regex missed three.
const ESC_IN_HANDLER = /\bon[a-z]+="[^"]*\$\{[^}"]*\besc\(/;

test('#2422: the handler guard catches every form of esc() inside on*="…", and only those', () => {
  for (const bad of [`onclick="f('\${esc(x)}')"`, `onclick="f(\${esc(x)})"`, `onclick="f(&quot;\${esc(x)}&quot;)"`,
    `onchange="g('\${a ? esc(x) : ""}')"`])
    assert.match(bad, ESC_IN_HANDLER, bad);
  for (const good of [`onclick="f('\${jsStr(x)}')"`, `title="\${esc(x)}" onclick="f()"`, `onclick="f()" title="\${esc(x)}"`,
    `onclick="f(\${Number(n)})"`])
    assert.doesNotMatch(good, ESC_IN_HANDLER, good);
});

test("no esc() left inside an on*=\"…\" handler, and no new Function, in server/static and the wizard", () => {
  const bad = [];
  const wizard = path.resolve(__dirname, '../server/wizard/static');
  for (const dir of [root, wizard]) for (const f of fs.readdirSync(dir).filter((n) => n.endsWith('.js'))) {
    fs.readFileSync(path.join(dir, f), 'utf8').split('\n').forEach((line, i) => {
      if (ESC_IN_HANDLER.test(line)) bad.push(`${f}:${i + 1} esc in handler`);
      if (/new Function\(/.test(line)) bad.push(`${f}:${i + 1} new Function`);
    });
  }
  assert.deepEqual(bad, []);
});
