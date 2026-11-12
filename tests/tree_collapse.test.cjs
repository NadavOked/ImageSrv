// הכרעת נדב (שלד, שלב 1): סגירת ענף סוגרת גם כל תת-ענף שבתוכו; החץ רק מחליף מצב ולא מנווט.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../server/static');
const source = consoleJs();
const start = source.indexOf('function collapseInventoryDescendants(');
const end = source.indexOf('/* S4 (#829)');
assert.ok(start > 0 && end > start, 'tree helpers not found');

function group(open) {   // צומת + תיבת ילדים, כמו ב-index.html
  const attrs = new Map([['aria-expanded', String(open)]]);
  const arrow = {dataset: {open: String(open)}, textContent: open ? '▾' : '▸'};
  const node = {attrs, classList: {contains: (c) => c === 'inventory-node'}, setAttribute: (k, v) => attrs.set(k, v),
    querySelector: (s) => (s === '.tree-arrow' ? arrow : null), arrow};
  const box = {hidden: !open, kids: [], previousElementSibling: node, style: {removeProperty() {}, display: ''},
    hasAttribute(k) { return k === 'hidden' && this.hidden; },
    setAttribute(k) { if (k === 'hidden') this.hidden = true; },
    removeAttribute(k) { if (k === 'hidden') this.hidden = false; },
    querySelectorAll(sel) { return sel === '.inventory-children' ? this.kids : []; }};
  return {node, box};
}

function setup(boxes) {
  const ctx = vm.createContext({document: {getElementById: (id) => boxes[id] || null}});
  vm.runInContext(source.slice(start, end) + '\n;this.api={toggleInventoryGroup};', ctx);
  return ctx.api;
}

test('closing a branch closes every sub-branch under it; reopening shows only the first level, closed', () => {
  const inventory = group(true), machines = group(true), images = group(true);
  inventory.box.kids = [machines.box, images.box];
  const api = setup({invTA: inventory.box});
  api.toggleInventoryGroup(inventory.node, 'invTA');   // סגירת "מלאי"
  assert.equal(inventory.box.hidden, true);
  for (const g of [machines, images]) {
    assert.equal(g.box.hidden, true, 'the sub-branch is closed with its parent');
    assert.equal(g.node.attrs.get('aria-expanded'), 'false');
    assert.equal(g.node.arrow.textContent, '▸');
    assert.equal(g.node.arrow.dataset.open, 'false');
  }
  api.toggleInventoryGroup(inventory.node, 'invTA');   // פתיחה מחדש
  assert.equal(inventory.box.hidden, false);
  assert.equal(machines.box.hidden, true, 'the child stays closed after reopening');
  assert.equal(inventory.node.attrs.get('aria-expanded'), 'true');
});

test('closing the root (the server node) closes all the groups under it', () => {
  const rootG = group(true), inventory = group(true), infra = group(true), machines = group(true);
  rootG.box.kids = [inventory.box, infra.box, machines.box];   // querySelectorAll מחזיר כל צאצא
  const api = setup({srvTA: rootG.box});
  api.toggleInventoryGroup(rootG.node, 'srvTA');
  assert.deepEqual([inventory, infra, machines].map((g) => g.box.hidden), [true, true, true]);
  api.toggleInventoryGroup(rootG.node, 'srvTA');
  assert.deepEqual([inventory, infra, machines].map((g) => g.box.hidden), [true, true, true], 'all groups closed on reopen');
});

test('opening a branch does not touch its children (only closing cascades)', () => {
  const outer = group(false), inner = group(true);
  outer.box.kids = [inner.box];
  setup({o: outer.box}).toggleInventoryGroup(outer.node, 'o');
  assert.equal(outer.box.hidden, false);
  assert.equal(inner.box.hidden, false, 'an inner box that was open is not force-closed by opening the parent');
});

test('every tree chevron stops propagation, so it toggles and never navigates; the root has one', () => {
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const arrows = html.match(/<span class="tree-arrow"[^>]*>/g) || [];
  assert.ok(arrows.length >= 5);
  for (const a of arrows) assert.match(a, /stopPropagation/, a);
  const rootNode = html.match(/class="inventory-node server-node active"[^>]*>(<span class="tree-arrow"[^>]*>)/);
  assert.ok(rootNode, 'the root (server name) has its own chevron');
  assert.match(rootNode[1], /toggleInventoryGroup\(this\.closest\('\.inventory-node'\),'srvTA'\)/);
});
