// #1857 (mockup 04): the disks page + sets tab. disks.js runs in a vm with the few console.js helpers stubbed.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const disks = fs.readFileSync(path.resolve(__dirname, '../server/static/disks.js'), 'utf8');
const consoleJs = fs.readFileSync(path.resolve(__dirname, '../server/static/console.js'), 'utf8');

function make(state = {}) {
  const sheets = [];
  const c = vm.createContext({
    current: 'disks', IMAGES: [{id: 'img_x', name: 'office365 v4'}], ME: {role: 'admin'},
    esc: (s) => String(s ?? '').replace(/[&<>"']/g, (m) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[m])),
    UI: {pill: (cls, t) => `<pill ${cls}>${t}</pill>`, datagrid: ({columns, rows}) => JSON.stringify({columns, rows}), note: (c2, h) => h,
      objHeader: (o) => JSON.stringify({name: o.name, tabs: o.tabs, sub: o.sub}), },
    smartReasonHe: (r) => 'he:' + r, fmtWhenShort: (t) => 'W:' + t, fmtBytes: (b) => b + 'B', uiIcon: () => '', isAdmin: () => true,
    toast: () => {}, pagePlaceholder: () => 'WAIT', renderCurrent: () => {}, refreshPage: () => {},
    sheet: (o) => sheets.push(o), confirmSheet: () => {}, post: async () => ({}), api: async () => ({}), del: async () => ({}),
  });
  vm.runInContext(disks, c);
  c.DK = vm.runInContext('DK', c);   // top-level const is lexical, not a property of the context
  Object.assign(c.DK, state);
  c.sheets = sheets;
  return c;
}
const D = (o) => ({serial: 'S', model: 'M', size_bytes: null, set_id: null, set_name: null, label: null, suffix: null, state: 'ok', reason: null,
  last_good_write: null, last_fail: null, in_machine: false, mac: null, machine: null, port: null, last_seen: null, ...o});

test('"not checked" is shown as such and never as ok; a reason is translated', () => {
  const c = make();
  assert.match(c.dkStateCell(D({state: 'unchecked'})), /<pill >לא נבדק<\/pill><span class="sub">לא נכתב מאז ששויך</);
  assert.match(c.dkStateCell(D({state: 'red', reason: 'pending'})), /<pill err>אדום<\/pill><span class="sub">he:pending</);
  assert.doesNotMatch(c.dkStateCell(D({state: 'unchecked', last_good_write: '2026-10-23T22:15:00+00:00'})), /לא נכתב מאז/);
});

test('the sticker follows the serial: label chip, and a disk without a set says so', () => {
  const c = make();
  assert.match(c.dkLabel(D({label: 'xx-12', suffix: '12'})), /<span class="lbl">xx-12<\/span>/);
  assert.match(c.dkLabel(D({label: 'xx-INS', suffix: 'INS'})), /class="lbl ins"/);
  assert.match(c.dkLabel(D({})), /בלי סט/);
});

test('"where now": in a machine -> machine and drawer; pulled out -> "לא במשכפל" with the last place in the title', () => {
  const c = make();
  assert.match(c.dkWhere(D({in_machine: true, machine: 'משכפל 2', port: 4})), /משכפל 2 · מגירה 4/);
  const out = c.dkWhere(D({in_machine: false, machine: 'משכפל 2', port: 4, last_seen: 'T'}));
  assert.match(out, /לא במשכפל/); assert.match(out, /title="נראה לאחרונה במשכפל 2, מגירה 4/);
});

test('filters: state tab, no-set, round, search; the unread state is not an empty list', () => {
  const all = [D({serial: 'A', state: 'red', set_id: 1, label: 'xx-1'}), D({serial: 'B', state: 'ok'}), D({serial: 'C', state: 'ok', model: 'WD'})];
  const c = make({disks: all, rounds: [{session_id: 's', mac: 'm', image_id: 'img_x', drawers: [{serial: 'B'}]}]});
  assert.deepEqual(c.dkFiltered('red').map((d) => d.serial), ['A']);
  c.DK.filter.noset = true; assert.deepEqual(c.dkFiltered('').map((d) => d.serial), ['B', 'C']);
  c.DK.filter.round = 's|m'; assert.deepEqual(c.dkFiltered('').map((d) => d.serial), ['B']);
  c.DK.filter.round = ''; c.DK.filter.noset = false; c.DK.filter.q = 'wd'; assert.deepEqual(c.dkFiltered('').map((d) => d.serial), ['C']);
  const failed = make({disks: null, err: 'boom'});
  assert.match(failed.disksPage(), /לא הצלחתי לקרוא את הדיסקים: boom/);
});

test('set card: counts, and the grid has one square per planned sticker', () => {
  const c = make();
  const html = c.dkSetCard({id: 1, name: 'קוריאים', prefix: 'xx', students: 2, instructor: true, capacity: 3, assigned: 2,
    counts: {red: 1, orange: 0, unchecked: 0, ok: 1}, grid: ['red', 'ok', 'empty'], plan: ['INS', '1', '2']});
  assert.match(html, /1 אדום/); assert.match(html, /1 תקינים/); assert.equal((html.match(/<i class=/g) || []).length, 3);
  assert.match(html, /class="b" title="xx-INS"/); assert.match(html, /class="e" title="xx-2"/);
});

test('assign sheet: next free numbers skip the taken ones; a disk already in a set or not written starts empty', () => {
  const c = make({disks: [D({set_id: 1, suffix: '1'}), D({set_id: 1, suffix: '2', serial: 'T'})]});
  c.diskAssignNamesSheet({id: 1, name: 'קוריאים', prefix: 'xx', next_free: 'xx-3'}, [
    {serial: 'A', model: 'M', port: 1, written: true, in_set: null}, {serial: 'B', model: 'M', port: 2, written: true, in_set: null},
    {serial: 'C', model: 'M', port: 3, written: true, in_set: 'L303-7'}, {serial: 'D', model: 'M', port: 4, written: false, in_set: null}], 'x');
  const f = c.sheets[0].fields;
  assert.deepEqual(f.map((x) => x.value), ['3', '4', '', '']);
  assert.match(f[2].label, /כבר בסט: L303-7/); assert.match(f[3].label, /לא נכתב בסבב/);
});

test('assign sheet refuses a duplicate name before it talks to the server', async () => {
  const c = make({disks: []});
  c.diskAssignNamesSheet({id: 1, name: 'ק', prefix: 'xx', next_free: null}, [{serial: 'A', written: true}, {serial: 'B', written: true}], 'x');
  await assert.rejects(c.sheets[0].onSubmit({s0: '5', s1: '5'}), /שם כפול: xx-5/);
  await assert.rejects(c.sheets[0].onSubmit({s0: '', s1: ''}), /לא נבחר דיסק/);
});

test('the page is wired: nav node, page registration and script include', () => {
  const html = fs.readFileSync(path.resolve(__dirname, '../server/static/index.html'), 'utf8');
  assert.match(html, /data-page="disks"[^>]*selectPageById\('disks'\)/); assert.match(html, /<script src="disks\.js\?v=/);
  assert.match(consoleJs, /disks: \{ crumb: "דיסקים", title: "דיסקים"[^}]*render: \(\) => disksPage\(\), load: \(\) => loadDisks\(\)/);
});
