// #2080 (mockup 06): "קושחה וחיבורים" in the machine drawer, and the machines-page markers.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = fs.readFileSync(path.resolve(__dirname, '../server/static/console.js'), 'utf8');

function fn(name) {
  const lines = js.split('\n');
  const start = lines.findIndex(l => new RegExp(`^(async )?function ${name}\\(`).test(l));
  assert.ok(start >= 0, `${name} exists`);
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  return lines.slice(start, lines.findIndex((l, i) => i > start && l.trimEnd() === '}') + 1).join('\n');
}
function make(admin = true) {
  const c = vm.createContext({
    esc: (s) => String(s ?? '').replace(/[&<>"]/g, (m) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[m])),
    UI: {kv: (p) => p.map(([k, v]) => `[${k}=${v}]`).join(''), note: (c2, h) => `NOTE:${h}`, datagrid: ({rows}) => 'ROWS:' + rows.map((r) => r.cells.join('|') + (r.attrs ? '#chg' : '')).join('~')},
    fmtBytes: (b) => b + 'B', fmtWhenShort: (t) => 'W' + t, encodeId: encodeURIComponent, isAdmin: () => admin, FIRMWARE: {},
  });
  for (const n of ['fwValueHe', 'firmwareTitle', 'firmwareSectionHtml']) vm.runInContext(fn(n), c);
  return c;
}
const M = {mac: 'aa:bb'};
const cur = {boot: 'uefi', secure_boot: false, sata_mode: 'ide', model: 'HP Compaq 8100 Elite CMT', bios_version: '786H1 v01.13', at: 'N'};

test('a changed controller mode reads "IDE (היה AHCI)" in orange, with the alert and the two buttons', () => {
  const html = make().firmwareSectionHtml(M, {current: cur, ref_kind: 'previous', slots: [], peers: [{}, {}, {}],
    changes: [{kind: 'field', field: 'sata_mode', was: 'ahci', now: 'ide', text: 'מצב בקר הדיסקים השתנה: AHCI ← IDE'}]});
  assert.match(html, /<span class="fw-chg">IDE <span class="muted">\(היה AHCI\)<\/span><\/span>/);
  assert.match(html, /הקושחה השתנתה מאז ההדלקה הקודמת/); assert.match(html, /AHCI ← IDE/);
  assert.match(html, /זו ההגדרה הנכונה — שמור/); assert.match(html, /השווה לדגם \(4 מכונות\)/);
  assert.match(html, /\[BIOS=786H1 v01\.13\]/);
});

test('no change: no alert and no "save" button; a not-reported field says so and is not a change', () => {
  const html = make().firmwareSectionHtml(M, {current: {...cur, sata_mode: null, bios_version: null}, changes: [], slots: [], peers: []});
  assert.doesNotMatch(html, /fw-alert|זו ההגדרה הנכונה/);
  assert.match(html, /\[בקר הדיסקים=<span class="muted">לא דווח<\/span>\]/); assert.match(html, /\[BIOS=<span class="muted">לא דווח<\/span>\]/);
});

test('after "this is the right setting" the alert says it compares to the saved image', () => {
  const html = make().firmwareSectionHtml(M, {current: cur, ref_kind: 'baseline', slots: [], peers: [],
    changes: [{kind: 'field', field: 'boot', was: 'bios', now: 'uefi', text: 'x'}]});
  assert.match(html, /מאז התמונה שנשמרה/);
});

test('connections: a vacated connection says "ריק" and what was there; a moved disk says where from', () => {
  const html = make().firmwareSectionHtml(M, {current: cur, changes: [], peers: [], slots: [
    {connection: 'SATA 1', disk: null, was: {model: 'TOSHIBA', serial: 'Y5'}, note: null, changed: true},
    {connection: 'SATA 2', disk: {model: 'TOSHIBA', serial: 'Y5', size_bytes: 5, link_gbps: 3}, was: null, note: 'עבר מ-SATA 1', changed: true}]});
  assert.match(html, /<b>ריק<\/b><span class="sub">היה: TOSHIBA · Y5<\/span>/); assert.match(html, /עבר מ-SATA 1/); assert.match(html, /#chg/);
});

test('only an admin gets the buttons; loading / error / never-reported are stated, not shown as healthy', () => {
  const a = make(false);
  assert.doesNotMatch(a.firmwareSectionHtml(M, {current: cur, changes: [{kind: 'field', field: 'boot', was: 'a', now: 'b', text: 't'}], slots: [], peers: []}), /fw-acts/);
  const c = make();
  assert.match(c.firmwareSectionHtml(M, {loading: true}), /טוען/);
  assert.match(c.firmwareSectionHtml(M, {error: 'boom'}), /לא נקראה: boom/);
  assert.match(c.firmwareSectionHtml(M, {current: null}), /עוד לא דיווחה/);
});

test('the machines page marks machines whose firmware changed (header chip + row chip)', () => {
  assert.match(js, /קושחה השתנתה ב-\$\{fwChanged\}/); assert.match(js, /m\.fw_changes > 0 \? ` \$\{UI\.pill\("warn", "קושחה השתנתה"\)\}`/);
});
