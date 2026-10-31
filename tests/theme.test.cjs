// #1093: ערכת נושא לפי משתמש. בלי דפדפן — console.js + ה-inline של
// index.html רצים ב-vm מול localStorage ו-matchMedia מזויפים.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function inlineScript() {
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const m = html.match(/<script>\s*\/\*[\s\S]*?\*\/\s*(\(function \(\) \{[\s\S]*?\}\)\(\);)\s*<\/script>/);
  assert.ok(m, 'inline theme script in index.html');
  return m[1];
}

function setup({cache = null, systemDark = false} = {}) {
  const store = {};
  if (cache != null) store['imagesrv-theme'] = cache;
  const attrs = {};
  const requests = [];
  const mediaListeners = [];
  let system = systemDark;
  function node(key) {
    if (!node.map.has(key))
      node.map.set(key, {
        value: '', innerHTML: '', textContent: '', title: '', dataset: {},
        style: {removeProperty() {}},
        classList: {add() {}, remove() {}, toggle() {}},
        addEventListener() {}, removeEventListener() {},
        querySelector() { return null; }, querySelectorAll() { return []; },
        insertAdjacentHTML(_p, html) { this.innerHTML += html; },
        setAttribute() {}, removeAttribute() {}, focus() {},
      });
    return node.map.get(key);
  }
  node.map = new Map();
  const localStorage = {
    getItem: (k) => Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null,
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false,
      querySelector: node,
      querySelectorAll: () => [],
      getElementById: (id) => node('#' + id),
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); }, get innerHTML() { return text; }}; },
      documentElement: {
        setAttribute(name, value) { attrs[name] = value; },
        removeAttribute(name) { delete attrs[name]; },
      },
      addEventListener() {}, removeEventListener() {},
    },
    window: {
      addEventListener() {},
      matchMedia: (q) => ({
        matches: system,
        media: q,
        addEventListener(type, fn) { if (type === 'change') mediaListeners.push(fn); },
        removeEventListener(type, fn) {
          const i = mediaListeners.indexOf(fn);
          if (i >= 0) mediaListeners.splice(i, 1);
        },
        addListener(fn) { mediaListeners.push(fn); },
        removeListener(fn) {
          const i = mediaListeners.indexOf(fn);
          if (i >= 0) mediaListeners.splice(i, 1);
        },
      }),
    },
    localStorage,
    CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options = {}) => {
      requests.push({url, options});
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({ok: true})};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",idle_seconds:300,theme:"auto",capabilities:{}};');
  return {
    run, attrs, store, requests, mediaListeners,
    setSystem(dark) {
      system = dark;
      for (const fn of mediaListeners.slice()) fn({matches: dark});
    },
  };
}

test('inline script applies cached dark before /me (no flash)', () => {
  const attrs = {};
  const ctx = vm.createContext({
    localStorage: {getItem: (k) => k === 'imagesrv-theme' ? 'dark' : null},
    document: {documentElement: {setAttribute(name, value) { attrs[name] = value; }}},
    window: {matchMedia: () => ({matches: false})},
  });
  vm.runInContext(inlineScript(), ctx);
  assert.equal(attrs['data-theme'], 'dark');
});

// #1499 (Nadav 16/10): the default for everyone is light -- "auto" in the DB
// is light and nothing follows the operating system. Every test below runs
// with the system set to dark: that is the setting the old code obeyed.

test('inline script is light when there is no cache, even on a dark system', () => {
  const attrs = {};
  const ctx = vm.createContext({
    localStorage: {getItem: () => null},
    document: {documentElement: {setAttribute(name, value) { attrs[name] = value; }}},
    window: {matchMedia: () => ({matches: true})},
  });
  vm.runInContext(inlineScript(), ctx);
  assert.equal(attrs['data-theme'], 'light');
});

test('auto is light on a dark system, uncached, and does not follow it', () => {
  const s = setup({systemDark: true});
  s.run('applyServerTheme({theme:"auto"})');
  assert.equal(s.attrs['data-theme'], 'light');
  assert.equal(s.store['imagesrv-theme'], undefined);
  assert.equal(s.mediaListeners.length, 0, 'nothing listens to prefers-color-scheme');
  s.setSystem(false); s.setSystem(true);
  assert.equal(s.attrs['data-theme'], 'light');
});

test('a user who saved dark gets dark, cached', () => {
  const s = setup({systemDark: false});
  s.run('applyServerTheme({theme:"dark"})');
  assert.equal(s.attrs['data-theme'], 'dark');
  assert.equal(s.store['imagesrv-theme'], 'dark');
});

test('a theme the server does not define is light', () => {
  const s = setup({systemDark: true});
  s.run('applyServerTheme({theme:"blue"})');
  assert.equal(s.attrs['data-theme'], 'light');
  s.run('applyServerTheme({})');
  assert.equal(s.attrs['data-theme'], 'light');
});

test('logout clears the cache and returns to light', () => {
  const s = setup({systemDark: true});
  s.run('applyServerTheme({theme:"dark"})');
  assert.equal(s.store['imagesrv-theme'], 'dark');
  s.run('showLogin()');
  assert.equal(s.store['imagesrv-theme'], undefined);
  assert.equal(s.attrs['data-theme'], 'light');
});

test('header toggle switches what is shown -- auto → dark → light -- and saves each', async () => {
  const s = setup({systemDark: true});
  s.run('applyServerTheme({theme:"auto"})');
  await s.run('toggleTheme()');
  assert.equal(s.attrs['data-theme'], 'dark');
  assert.equal(s.run('themePref'), 'dark');
  await s.run('toggleTheme()');
  assert.equal(s.attrs['data-theme'], 'light');
  assert.equal(s.run('themePref'), 'light');
  const puts = s.requests.filter((r) => r.url === '/api/console/me/theme');
  assert.deepEqual(puts.map((r) => JSON.parse(r.options.body).theme), ['dark', 'light']);
  assert.ok(puts.every((r) => r.options.method === 'PUT'));
});

test('the appearance card offers light and dark only; auto shows light pressed', () => {
  const s = setup({systemDark: true});
  s.run('applyServerTheme({theme:"auto"})');
  const html = s.run('appearanceCard()');
  assert.doesNotMatch(html, /לפי המערכת/);
  assert.match(html, /setThemePref\('light'\)" aria-pressed="true">בהיר/);
  assert.match(html, /setThemePref\('dark'\)" aria-pressed="false">כהה/);
  assert.equal(s.run('setThemePref("auto")'), undefined, 'auto is not a choice any more');
});
