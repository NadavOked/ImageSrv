// #1854: קלט בתוך המוניטור (iframe או חלון נפרד) נספר כפעילות בקונסולה — אחרת מפעיל שעובד
// במסך המכונה נזרק אחרי console_idle_seconds (ברירת מחדל 10 דק'). monitor.js ו-console.js רצים
// כאן בשני הקשרים נפרדים, מחוברים ב-BroadcastChannel מזויף אחד — כמו שני מסמכים באותו origin.
// בקרה שלילית: על הקוד שלפני #1854 monitor.js אינו שולח דבר, ו-noteActivity של הקונסולה לא נקרא.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const consoleJs = fs.readFileSync(path.join(root, 'console.js'), 'utf8');

function bus() {
  const channels = [];
  const sent = [];
  class FakeBroadcastChannel {
    constructor(name) { this.name = name; this.onmessage = null; channels.push(this); }
    postMessage(data) {
      sent.push({name: this.name, data});
      for (const other of channels)
        if (other !== this && other.name === this.name && other.onmessage) other.onmessage({data});
    }
  }
  return {FakeBroadcastChannel, sent};
}

function monitorPage(Channel, clock) {
  const docListeners = {};
  const stub = () => ({textContent: '', className: '', style: {}, title: '', classList: {add() {}, remove() {}, contains() { return false; }},
    addEventListener() {}, focus() {}, getContext() { return {fillRect() {}}; }});
  const nodes = {};
  const ctx = vm.createContext({
    console, URLSearchParams, Uint8Array, TextDecoder, TextEncoder, Promise, Math, Error, JSON,
    location: {search: '', protocol: 'http:', host: 'srv:8081'},
    document: {getElementById: (id) => (nodes[id] ||= stub()), title: '', fullscreenEnabled: false,
      addEventListener(name, fn) { (docListeners[name] ||= []).push(fn); }, removeEventListener() {},
      documentElement: {classList: {add() {}, remove() {}}}},
    WebSocket: class {}, setTimeout() { return 0; }, clearTimeout() {},
    Date: {now: () => clock.now}, BroadcastChannel: Channel,
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'monitor.js'), 'utf8'), ctx);
  const fire = (type) => (docListeners[type] || []).forEach((fn) => fn({type}));
  return {fire};
}

function consolePage(Channel) {
  const lines = consoleJs.split('\n');
  const start = lines.findIndex((l) => /^function listenMonitorActivity\(/.test(l));
  const seen = {count: 0};
  // a console.js without the listener is a console that hears nothing -- the behaviour, not a crash
  if (start < 0) return seen;
  const end = lines.findIndex((l, i) => i > start && l.trimEnd() === '}');
  const ctx = vm.createContext({BroadcastChannel: Channel, noteActivity: () => { seen.count++; }});
  vm.runInContext(lines.slice(start, end + 1).join('\n') + '\nlistenMonitorActivity();', ctx);
  return seen;
}

test('a key pressed in the monitor resets the console idle clock', () => {
  const {FakeBroadcastChannel, sent} = bus();
  const clock = {now: 1_000_000};
  const seen = consolePage(FakeBroadcastChannel);
  const monitor = monitorPage(FakeBroadcastChannel, clock);
  monitor.fire('keydown');
  assert.equal(seen.count, 1, 'the console did not hear the monitor');
  assert.deepEqual(sent.map((m) => m.data), ['activity'], 'only a bare "activity", no content');
});

test('mouse and wheel in the monitor count too, at most once every 5 seconds', () => {
  const {FakeBroadcastChannel} = bus();
  const clock = {now: 1_000_000};
  const seen = consolePage(FakeBroadcastChannel);
  const monitor = monitorPage(FakeBroadcastChannel, clock);
  monitor.fire('pointermove');
  clock.now += 1000;
  monitor.fire('wheel');
  monitor.fire('pointerdown');
  assert.equal(seen.count, 1, 'a message per event would flood the channel');
  clock.now += 5000;
  monitor.fire('pointerdown');
  assert.equal(seen.count, 2);
});

test('a monitor nobody touches says nothing — an open window is not activity', () => {
  const {FakeBroadcastChannel, sent} = bus();
  const clock = {now: 1_000_000};
  consolePage(FakeBroadcastChannel);
  monitorPage(FakeBroadcastChannel, clock);
  assert.deepEqual(sent, []);
});

test('the console only listens: it sends nothing toward the monitor', () => {
  const {FakeBroadcastChannel, sent} = bus();
  consolePage(FakeBroadcastChannel);
  assert.deepEqual(sent, []);
  assert.ok(consoleJs.includes('function listenMonitorActivity'), 'the console listens at all');
  assert.doesNotMatch(consoleJs.slice(consoleJs.indexOf('function listenMonitorActivity'),
    consoleJs.indexOf('listenMonitorActivity();')), /postMessage/);
});
