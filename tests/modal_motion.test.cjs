// apple-design F06: החלון נפתח/נסגר במצבי class (לא display), נקודת המוצא לפי הכפתור,
// והניקוי של sheet-mode ממתין לסוף ה-fade ובטל בפתיחה חוזרת.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = consoleJs();
const start = source.indexOf('const MODAL_CLOSE_MS');
const end = source.indexOf('function toast(');
assert.ok(start > 0 && end > start, 'modal helpers not found');

function classSet() {
  const set = new Set();
  return {
    add: (...c) => c.forEach((x) => set.add(x)),
    remove: (...c) => c.forEach((x) => set.delete(x)),
    toggle: (c, on) => { if (on) set.add(c); else set.delete(c); },
    contains: (c) => set.has(c),
    has: (c) => set.has(c),
  };
}

function setup(trigger) {
  const timers = [];
  const props = {};
  const modal = {
    classList: classSet(), style: {
      setProperty: (k, v) => { props[k] = v; }, removeProperty: (k) => { delete props[k]; },
    }, contains: () => false,
  };
  const form = {classList: classSet(), onsubmit: () => {}};
  const box = {offsetLeft: 100, offsetTop: 50};
  const els = {modal, sheet: form, modalBox: box};
  const ctx = {
    document: {getElementById: (id) => els[id], activeElement: trigger, body: {}},
    setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; },
    clearTimeout: (id) => { if (id) timers[id - 1].cancelled = true; },
  };
  vm.createContext(ctx);
  vm.runInContext(source.slice(start, end) + '\n;this.api={openModalFrame,closeModal};', ctx);
  return {api: ctx.api, modal, form, props, timers};
}

test('open shows via class only and takes origin from the trigger button', () => {
  const trigger = {getBoundingClientRect: () => ({left: 300, top: 200, width: 40, height: 20})};
  const {api, modal, props} = setup(trigger);
  api.openModalFrame(modal, 'box');
  assert.ok(modal.classList.contains('show'));
  assert.equal('display' in modal.style, false);
  assert.equal(props['--origin'], '220px 160px');
});

test('no usable trigger falls back to centre (no --origin)', () => {
  const {api, modal, props} = setup(undefined);
  api.openModalFrame(modal, 'sheet-mode');
  assert.equal(props['--origin'], undefined);
  assert.ok(modal.classList.contains('sheet-mode'));
});

test('close drops show at once but defers sheet-mode cleanup until the fade ends', () => {
  const {api, modal, form, timers} = setup(undefined);
  api.openModalFrame(modal, 'sheet-mode');
  api.closeModal();
  assert.equal(modal.classList.contains('show'), false);
  assert.ok(modal.classList.contains('sheet-mode'), 'content must not swap while fading');
  assert.equal(form.onsubmit, null);
  const cleanup = timers[timers.length - 1];
  assert.ok(cleanup.ms >= 240);
  cleanup.fn();
  assert.equal(modal.classList.contains('sheet-mode'), false);
  assert.ok(form.classList.contains('hidden'));
});

test('reopening mid-close cancels the pending cleanup (direction flips, input not blocked)', () => {
  const {api, modal, timers} = setup(undefined);
  api.openModalFrame(modal, 'sheet-mode');
  api.closeModal();
  api.openModalFrame(modal, 'sheet-mode');
  assert.ok(modal.classList.contains('show'));
  assert.equal(timers[timers.length - 1].cancelled, true);
});

// ---- ביקורת ממשק 10/10: חלון = דיאלוג (פוקוס פנימה, Tab כלוא, חזרה לפותח) ------------------

let ctxDoc = null;
function focusable(name, log) {
  const e = {name, offsetParent: {}, focus() { log.focused = name; ctxDoc.activeElement = e; }};
  return e;
}

function dialogSetup() {
  const log = {};
  const a = focusable('a', log), b = focusable('b', log);
  const opener = {name: 'opener', focus() { log.focused = 'opener'; ctxDoc.activeElement = opener; }};
  const modal = {classList: classSet(), style: {setProperty() {}, removeProperty() {}}, contains: () => false};
  const form = {classList: classSet(), onsubmit: null, contains: (x) => x === a || x === b,
    querySelectorAll: () => [a, b], querySelector: () => null, focus() { log.focused = 'root'; }};
  const frames = [];
  const els = {modal, sheet: form, modalBox: {offsetLeft: 0, offsetTop: 0}, drawer: {classList: classSet()}};
  ctxDoc = {getElementById: (id) => els[id], activeElement: opener, body: {}};
  const ctx = {document: ctxDoc, requestAnimationFrame: (fn) => frames.push(fn), setTimeout: () => 1, clearTimeout() {}};
  vm.createContext(ctx);
  vm.runInContext(source.slice(start, end) + '\n;this.api={openModalFrame,closeModal,dialogKeydown,dialogRoot};', ctx);
  return {api: ctx.api, modal, form, a, b, log, frames};
}

test('a window remembers its opener, takes focus on open, and gives it back on close', () => {
  const {api, modal, form, log, frames} = dialogSetup();
  api.openModalFrame(modal, 'sheet-mode');
  assert.equal(api.dialogRoot(), form, 'the visible sheet is the dialog root');
  frames.forEach((f) => f());
  assert.equal(log.focused, 'root', 'focus moved into the dialog (the root: nothing inside was focused yet)');
  api.closeModal();
  assert.equal(log.focused, 'opener', 'focus returns to the button that opened it');
});

test('Tab is trapped inside the open window: last wraps to first, Shift+Tab on first wraps to last', () => {
  const {api, modal, a, b, log} = dialogSetup();
  api.openModalFrame(modal, 'sheet-mode');
  const key = (shiftKey) => { const e = {key: 'Tab', shiftKey, prevented: false, preventDefault() { this.prevented = true; }}; api.dialogKeydown(e); return e; };
  ctxDoc.activeElement = b;
  assert.equal(key(false).prevented, true); assert.equal(log.focused, 'a');
  ctxDoc.activeElement = a;
  assert.equal(key(true).prevented, true); assert.equal(log.focused, 'b');
  ctxDoc.activeElement = {};
  const outside = key(false);
  assert.equal(outside.prevented, true, 'focus that escaped is pulled back in');
});

test('index.html: the three windows are role=dialog aria-modal with a name, and every close X has an accessible name', () => {
  const html = fs.readFileSync(path.resolve(__dirname, '../server/static/index.html'), 'utf8');
  for (const id of ['drawer', 'modalBox', 'sheet']) {
    assert.match(html, new RegExp(`id="${id}"[^>]*role="dialog"[^>]*aria-modal="true"[^>]*aria-labelledby="\\w+"`), id);
  }
  for (const fn of ['closeDrawer', 'closeModal', 'toggleTaskPanel']) {
    assert.match(html, new RegExp(`aria-label="סגור"[^>]*onclick="${fn}\\(\\)"`), fn);
  }
});
