// #1851 — "רענון" וכל renderCurrent בדף המוניטור בנו את ה-iframe של הצופה מחדש: WebSocket חדש,
// לחיצת-יד RFB, ומרוץ 4409. ורשימת "מחשבים מחוברים" לא התעדכנה לבד. הטסט מריץ את console.js האמיתי
// מול DOM מזערי שמפענח innerHTML לעץ (בלי jsdom), ובודק זהות (===) של אלמנט ה-iframe.
// בקרה שלילית: על console.js שלפני #1851 — אלמנט חדש בכל רינדור, ואין דגימה של /monitor/machines.
const {consoleFiles} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

/* ---- DOM מזערי: מספיק ל-innerHTML, ‏querySelector פשוט ותזוזת צמתים ---- */
const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr']);
const decode = (s) => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.attrs = new Map(); this.childNodes = []; this.parentNode = null; this.style = {}; this.dataset = {}; }
  get children() { return this.childNodes.filter((c) => c instanceof El); }
  get parentElement() { return this.parentNode; }
  get className() { return this.attrs.get('class') || ''; }
  set className(v) { this.attrs.set('class', String(v)); }
  get classList() { const el = this; return {contains: (c) => el.className.split(/\s+/).includes(c), add() {}, remove() {}, toggle() {}}; }
  get id() { return this.attrs.get('id') || ''; }
  getAttribute(n) { return this.attrs.has(n) ? this.attrs.get(n) : null; }
  setAttribute(n, v) { this.attrs.set(n, String(v)); }
  removeAttribute(n) { this.attrs.delete(n); }
  get textContent() { return this.childNodes.map((c) => (c instanceof El ? c.textContent : c.text)).join(''); }
  set textContent(v) { this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = [{text: String(v), parentNode: this}]; }
  set innerHTML(html) { this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = []; parse(String(html), this); }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.childNodes.push(c); return c; }
  insertBefore(c, ref) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.childNodes.splice(this.childNodes.indexOf(ref), 0, c); return c; }
  removeChild(c) { const i = this.childNodes.indexOf(c); assert.ok(i >= 0, 'removeChild of a non-child'); this.childNodes.splice(i, 1); c.parentNode = null; return c; }
  replaceWith(n) { this.parentNode.insertBefore(n, this); this.parentNode.removeChild(this); }
  contains(n) { for (; n; n = n.parentNode) if (n === this) return true; return false; }
  focus() {} addEventListener() {} removeEventListener() {} scrollIntoView() {}
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => { for (const c of n.children) { if (sel.split(',').some((s) => matches(c, s.trim().split(/\s+/), this))) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
function matchOne(el, simple) {
  const m = simple.match(/^([a-z][\w-]*)?((?:[#.][\w-]+|\[[^\]]+\])*)$/i);
  if (!m) return false;   // ‏:not() וכו' — רק בקוד המעטפת; הבוררים של הטסט עצמו נתמכים ונבדקים חיובית
  if (m[1] && el.tagName !== m[1].toUpperCase()) return false;
  for (const part of m[2].match(/[#.][\w-]+|\[[^\]]+\]/g) || []) {
    if (part[0] === '#' && el.id !== part.slice(1)) return false;
    if (part[0] === '.' && !el.classList.contains(part.slice(1))) return false;
    if (part[0] === '[') { const [k, v] = part.slice(1, -1).split('='); if (!el.attrs.has(k) || (v !== undefined && el.attrs.get(k) !== v.replace(/"/g, ''))) return false; }
  }
  return true;
}
function matches(el, parts, scope) {
  if (!matchOne(el, parts[parts.length - 1])) return false;
  const rest = parts.slice(0, -1);
  for (let a = el.parentNode; rest.length && a && a !== scope.parentNode; a = a.parentNode) if (matchOne(a, rest[rest.length - 1])) rest.pop();
  return rest.length === 0;
}
function parse(html, into) {
  const re = /<!--[\s\S]*?-->|<\/([a-z][\w-]*)\s*>|<([a-z][\w-]*)((?:\s+[^\s=>/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|[^<]+|</gi;
  const stack = [into];
  for (const t of html.matchAll(re)) {
    const top = stack[stack.length - 1];
    if (t[0].startsWith('<!--')) continue;
    if (t[1]) { const i = stack.map((n) => n.tagName).lastIndexOf(t[1].toUpperCase()); if (i > 0) stack.length = i; continue; }
    if (t[2]) {
      const el = new El(t[2]);
      for (const a of (t[3] || '').matchAll(/([^\s=>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) el.attrs.set(a[1], decode(a[2] ?? a[3] ?? a[4] ?? ''));
      top.appendChild(el);
      if (!t[4] && !VOID.has(t[2].toLowerCase())) stack.push(el);
      continue;
    }
    top.childNodes.push({text: decode(t[0]), parentNode: top});
  }
}

function setup(fixtures) {
  const body = new El('body');
  body.innerHTML = '<div id="content"></div><div id="app"></div><div id="login"></div><input id="globalSearch">';
  // רכיבי מעטפת שאינם בדף (‎#theme-toggle‎ וכו') — צומת קבוע לכל id, מחוץ ל-body
  const shell = new Map();
  const byId = (id) => body.querySelector(`#${id}`) || (shell.has(id) ? shell.get(id) : shell.set(id, new El('div')).get(id));
  const calls = [], intervals = [];
  const clock = {t: Date.now()};
  class FakeDate extends Date { static now() { return clock.t; } }
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date: FakeDate, Set, Map, Number, Math, JSON, Promise, Array,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, body, activeElement: null,
      getElementById: byId, querySelector: (s) => (/^#[\w-]+$/.test(s) ? byId(s.slice(1)) : body.querySelector(s)),
      querySelectorAll: (s) => body.querySelectorAll(s), createElement: (t) => new El(t),
      documentElement: {setAttribute() {}, classList: {add() {}, remove() {}}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: (fn) => { intervals.push(fn); return intervals.length; }, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url) => {
      const key = url.replace('/api/console', '').split('?')[0];
      calls.push(key);
      const v = typeof fixtures[key] === 'function' ? fixtures[key]() : fixtures[key];
      if (v instanceof Error) return {status: 500, ok: false, headers: {get: () => null}, json: async () => ({detail: v.message})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => v || {}};
    },
  });
  for (const f of ['progress.js', ...consoleFiles(), 'net.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",idle_seconds:300,capabilities:{}}; toast=()=>{}; current="monitor";');
  const content = body.querySelector('#content');
  const frame = () => content.querySelector('iframe.mon-frame');
  return {run, calls, intervals, clock, content, frame};
}

const machines = () => [
  {mac: 'aa:bb:cc:dd:ee:01', name: 'Builder', role: 'build', ip: '10.44.12.50', online: true},
  {mac: 'aa:bb:cc:dd:ee:02', name: 'Cloner-1', role: 'cloner', ip: '10.44.12.60', online: false},
];
const base = {
  '/groups': [{id: 'grp_BUILD', role: 'build', label: 'בנייה', machines: 1}, {id: 'grp_CLONERS', role: 'cloner', label: 'שיכפול', machines: 1}],
  '/machines': [{mac: 'aa:bb:cc:dd:ee:01', suffix: 'Builder', group_id: 'grp_BUILD'}, {mac: 'aa:bb:cc:dd:ee:02', suffix: 'Cloner-1', group_id: 'grp_CLONERS'}],
  '/monitor/settings': {port: 5900, enabled: true},
  '/net': [],
};
const flush = async () => { for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r)); };

async function viewing(fixtures) {
  const s = setup(fixtures);
  await s.run('loadMonitor()');
  s.run("monitorSelect('aa%3Abb%3Acc%3Add%3Aee%3A01')");
  const f = s.frame();
  assert.ok(f, 'the selected online machine is shown in an iframe');
  assert.match(f.getAttribute('src'), /^monitor\.html\?mac=aa%3Abb%3Acc%3Add%3Aee%3A01/);
  return {...s, f};
}

test('renderCurrent twice with the same machine selected keeps the same iframe element (===), still in #content', async () => {
  const {run, content, frame, f} = await viewing({...base, '/monitor/machines': machines()});
  run('renderCurrent()');
  assert.equal(frame(), f, 'the viewer iframe was rebuilt — a new WebSocket and RFB handshake');
  assert.ok(content.contains(f), 'the kept iframe is still attached to the page');
  assert.equal(content.querySelectorAll('iframe').length, 1, 'one viewer, not two');
});

test('"רענון" (refreshPage → loadMonitor) and picking the same machine again do not touch the viewer, but the list is redrawn', async () => {
  const list = machines();
  const {run, content, frame, f} = await viewing({...base, '/monitor/machines': () => list});
  list[1].online = true;   // המשכפל עלה בין שתי הקריאות
  run('refreshPage()');
  await flush();
  assert.equal(frame(), f, '"רענון" rebuilt the viewer');
  assert.ok(content.querySelector('button.mon-pick[data-mac="aa:bb:cc:dd:ee:02"]'), 'the list itself was redrawn from the new answer');
  run("monitorSelect('aa%3Abb%3Acc%3Add%3Aee%3A01')");
  assert.equal(frame(), f, 'picking the machine that is already shown rebuilt the viewer');
});

test('another machine, or the shown machine going offline, does replace the viewer — the old iframe leaves the DOM (lock 4409)', async () => {
  const list = machines(); list[1].online = true;
  const {run, content, frame, f} = await viewing({...base, '/monitor/machines': () => list});
  run("monitorSelect('aa%3Abb%3Acc%3Add%3Aee%3A02')");
  const g = frame();
  assert.notEqual(g, f); assert.ok(!content.contains(f), 'the iframe of a machine no longer selected must leave the page');
  assert.match(g.getAttribute('src'), /mac=aa%3Abb%3Acc%3Add%3Aee%3A02/);
  run('MONITOR.machines.find((m) => m.mac === "aa:bb:cc:dd:ee:02").online = false; renderCurrent()');
  assert.equal(frame(), null, 'offline → no iframe');
  assert.ok(!content.contains(g));
});

test('the connected list refreshes by itself from the status watch, on a gentle cadence, without touching the viewer', async () => {
  const list = machines();
  const {run, calls, intervals, clock, content, frame, f} = await viewing({...base, '/monitor/machines': () => list});
  run('startStatusWatch()');
  const tick = intervals[intervals.length - 1];
  const polls = () => calls.filter((c) => c === '/monitor/machines').length;
  const before = polls();
  tick(); await flush();
  assert.equal(polls(), before, 'right after loading the page there is no extra read (2-second poll is not the cadence)');
  list[1].online = true;
  clock.t += 10000;
  tick(); await flush();
  assert.equal(polls(), before + 1, 'after 10 s the status watch reads /monitor/machines again');
  assert.ok(content.querySelector('button.mon-pick[data-mac="aa:bb:cc:dd:ee:02"]'), 'the machine that came back is listed without pressing "רענון"');
  assert.equal(frame(), f, 'the automatic refresh rebuilt the viewer');
});

test('a failed automatic read is shown by name and does not wipe the page or the viewer (principle 5)', async () => {
  let fail = false;
  const {run, intervals, clock, content, frame, f} = await viewing({...base, '/monitor/machines': () => (fail ? new Error('server down') : machines())});
  run('startStatusWatch()');
  fail = true; clock.t += 10000;
  intervals[intervals.length - 1](); await flush();
  assert.match(content.textContent, /הרשימה לא עודכנה: server down/);
  assert.equal(frame(), f, 'a failed poll must not tear down the viewer');
});

test('"פתח בחלון נפרד" moved the view out: the automatic refresh does not bring the iframe back; picking the machine does', async () => {
  const {run, frame, f, content} = await viewing({...base, '/monitor/machines': machines()});
  run('monitorPopOut()');
  assert.ok(!content.contains(f));
  run('renderCurrent()');
  assert.equal(frame(), null, 'a re-render after pop-out reopened the viewer here and would steal it from the separate window');
  assert.match(content.textContent, /המסך פתוח בחלון נפרד/);
  run("monitorSelect('aa%3Abb%3Acc%3Add%3Aee%3A01')");
  assert.ok(frame(), 'picking the machine brings the view back');
});
