// Run the shipped renderers, including on the pre-fix sources. No DOM package.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function functionSource(file, name) {
  const source = consoleJs();
  const lines = source.split('\n');
  const start = lines.findIndex(line => new RegExp(`^(\\s*)(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `renderer ${name} exists`);
  const indent = lines[start].match(/^\s*/)[0];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === indent + '}');
  return lines.slice(start, end + 1).join('\n');
}

async function render(file, name, written, total, extra = {}) {
  const elements = new Map();
  const $ = key => {
    if (!elements.has(key)) elements.set(key, {innerHTML: '', textContent: '', style: {},
      classList: {add() {}, remove() {}, toggle() {}}, setAttribute() {}, removeAttribute() {}});
    return elements.get(key);
  };
  const item = {bytes_written: written, bytes_total: total, state: 'running',
    name: 'test', id: 'task', mac: 'mac', joined: true, ...extra};
  const context = vm.createContext({document: {querySelectorAll: () => []}, $, esc: String, fmtBytes: String, show() {},
    api: async () => [item], shown: null, steps: () => '', drawerLine: () => '', disksHtml: () => '', driversHtml: () => '',
    DRAWER_STATE: {}, session: {id: 'session', state: 'running', members: [item]}, item});
  const helper = path.join(root, 'progress.js');
  if (fs.existsSync(helper)) vm.runInContext(fs.readFileSync(helper, 'utf8'), context);
  vm.runInContext(functionSource(file, name), context);
  const result = await vm.runInContext(name === 'machineRows' ? `${name}([item], true)`
    : name === 'renderLive' ? `${name}(session)`
    : name === 'memberRow' ? `${name}(item, session)` : `${name}(item)`, context);
  // Compare the progress markup itself, excluding the already-present byte label.
  if (name === 'drawProgress') return JSON.stringify([$('#st-bar').style, $('#st-bar').className, $('#st-pct').textContent]);
  const html = result || $('#cls-machines').innerHTML;
  return html;
}

for (const [file, name] of [['console.js', 'captureProgressCell'], ['console.js', 'memberRow']]) {
  for (const total of [undefined, null, 0]) test(`${file}: unknown ${total} differs from zero progress`, async () => {
    const moving = await render(file, name, 8192, total);
    const idle = await render(file, name, 0, total);
    assert.ok(moving, 'renderer produced markup');
    assert.notEqual(moving, idle, 'unknown moving progress was rendered as zero');
    assert.match(moving, /indeterminate|לא ידוע/);
  });
  test(`${file}: known zero, halfway, and complete stay determinate`, async () => {
    for (const [written, pct] of [[0, 0], [50, 50], [100, 100]]) {
      const markup = await render(file, name, written, 100);
      assert.match(markup, new RegExp(`${pct}%`));
      assert.doesNotMatch(markup, /indeterminate|לא ידוע/);
    }
  });
  test(`${file}: source blocks name the partition and do not use compressed bytes`, async () => {
    const markup = await render(file, name, 8192, null, {source_progress:
      {partition: 3, blocks_read: 118, blocks_total: 1000}});
    assert.match(markup, /12%/);
    assert.match(markup, /מחיצה 3/);
  });
  test(`${file}: invalid denominator stays unknown`, async () => {
    for (const total of [-1, '100', NaN, Infinity]) {
      const markup = await render(file, name, 8192, total);
      assert.match(markup, /indeterminate|לא ידוע/);
      assert.doesNotMatch(markup, /NaN%|Infinity%/);
    }
  });
}
