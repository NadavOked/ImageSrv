// #1858 (mockup 07/11, Nadav): "נצפה F1" per disk, by serial -- the console side.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = (f) => fs.readFileSync(path.resolve(__dirname, '../server/static', f), 'utf8');
const consoleJs = read('console.js'), disksJs = read('disks.js');

function fn(js, name) {
  const lines = js.split('\n');
  const start = lines.findIndex(l => new RegExp(`^(async )?function ${name}\\(`).test(l));
  assert.ok(start >= 0, `${name} exists`);
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  return lines.slice(start, lines.findIndex((l, i) => i > start && l.trimEnd() === '}') + 1).join('\n');
}
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (m) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[m]));

function make({admin = true} = {}) {
  const calls = {posts: [], dels: [], toasts: [], sheets: [], confirms: []};
  const c = vm.createContext({
    esc, ltr: (t) => `<bdi dir="ltr">${t}</bdi>`, isAdmin: () => admin, encodeId: encodeURIComponent,
    machineName: (m) => m.name, machineRole: () => 'cloner', monitorRow: () => null, isToday: () => true,
    fmtClock: (t) => 'C' + t, fmtWhenShort: (t) => 'W' + t, netFor: () => null, machineRedDisk: () => '',
    findMachine: (enc) => c.MACHINES.find((m) => encodeURIComponent(m.mac) === enc || m.mac === enc),
    MACHINES: [], toast: (t) => calls.toasts.push(t), loadMachines: async () => { calls.loaded = true; }, loadDisks: async () => { calls.loadedDisks = true; },
    post: async (u, b) => { calls.posts.push([u, b]); return {ok: true}; }, del: async (u) => { calls.dels.push(u); return {ok: true}; },
    sheet: (o) => calls.sheets.push(o), confirmSheet: (...a) => calls.confirms.push(a),
    UI: {pill: (cls, text) => `PILL(${cls}:${text})`, note: (k, h) => `NOTE(${k}:${h})`, rowMenu: (label, items) => `MENU(${label}:${JSON.stringify(items)})`},
    jsStr: (s) => JSON.stringify(s), smartReasonHe: (r) => r === 'f1_observed' ? 'נצפה F1 בהדלקה' : `R:${r}`,
    DK_STATE: {red: ['err', 'אדום'], ok: ['ok', 'תקין'], unchecked: ['', 'לא נבדק']},
    drawerSlots: (m) => m.drawer_slots || ['sata:1', 'sata:2', 'sata:3'],
    STATE_PILL: {}, calls,
  });
  vm.runInContext('const NEVER_WOKE_HE = "לא הגיע";', c);
  for (const n of ['neverWoke', 'neverWokeLabel', 'heDuration', 'neverWokeWhen', 'neverWokeText', 'neverWokeRowSub', 'neverWokeBanners',
    'f1Date', 'f1Why', 'f1PickDisks', 'f1DiskWhere', 'f1SmartHe', 'f1MarkSheet', 'connKey', 'connText', 'diskNumber'])
    vm.runInContext(fn(consoleJs, n), c);
  for (const n of ['dkF1Tag', 'dkStateCell', 'dkRowMenu', 'dkF1Mark', 'dkF1Clear'])
    vm.runInContext(fn(disksJs, n), c);
  return c;
}
const D = (port, serial, over = {}) => ({port, serial, model: `Model ${port}`, smart: 'ok', ...over});
const nw = (f1) => ({state: 'never_woke', sent_at: 'T', waited_s: 270, typical_s: 70, after_s: 180, resent: 0, f1});
const OBS = {at: 'x', date: '2026-10-24', source: 'manual'};
const M = (over = {}) => ({mac: 'aa:bb', name: 'CLONER-02', disks: [D(1, 'A1'), D(2, 'S62B')], ...over});

test('an observed disk is said with its date; a SMART red one alongside it keeps both proofs', () => {
  const c = make();
  const f = {drawer: 2, model: 'Samsung SSD 870 EVO', serial: 'S62B', reason: 'health_failed', basis: 'observed', smart_red: false, observed: OBS};
  assert.equal(c.f1Why(f), 'נצפה F1 ב-<bdi dir="ltr">24/10</bdi>');
  assert.equal(c.f1Why({...f, smart_red: true}), 'SMART אדום, נצפה F1 ב-<bdi dir="ltr">24/10</bdi>');
  assert.equal(c.f1Why({...f, observed: null, smart_red: true, basis: 'smart_red'}), 'SMART אדום');
  assert.equal(c.f1Why({...f, observed: {...OBS, date: null}}), 'נצפה F1');
  assert.equal(c.f1Why({...f, smart_red: true, observed: OBS}, ' · '), 'SMART אדום · נצפה F1 ב-<bdi dir="ltr">24/10</bdi>');
});

test('the banner text and the row sub-line carry the observed proof', () => {
  const c = make();
  const f = {drawer: 2, model: 'Samsung SSD 870 EVO', serial: 'S62B', reason: 'health_failed', basis: 'observed', smart_red: true, observed: OBS};
  assert.match(c.neverWokeText(M(), nw(f)).body, /^במגירה 2 יש דיסק שידוע שעוצר ב-F1: <bdi dir="ltr">Samsung SSD 870 EVO<\/bdi>, SMART אדום, נצפה F1 ב-<bdi dir="ltr">24\/10<\/bdi>\. WoL נשלח/);
  assert.equal(c.neverWokeRowSub(nw(f)), '<span class="sub">מגירה 2: SMART אדום · נצפה F1 ב-<bdi dir="ltr">24/10</bdi></span>');
});

test('the banner offers "סמן F1 לדיסק…" to an admin whose machine reported serials, even with no F1 evidence', () => {
  const c = make();
  const html = c.neverWokeBanners([M({never_woke: nw(null)})]);
  assert.match(html, /<button class="btn sm" onclick="f1MarkSheet\('aa%3Abb'\)">סמן F1 לדיסק…<\/button>/);
  assert.doesNotMatch(html, /F1 של ה-BIOS/);   // no evidence: still not a word about F1 in the sentence
  assert.doesNotMatch(make({admin: false}).neverWokeBanners([M({never_woke: nw(null)})]), /סמן F1/);
  assert.doesNotMatch(c.neverWokeBanners([M({never_woke: nw(null), disks: [D(1, '')]})]), /סמן F1/);
  assert.doesNotMatch(c.neverWokeBanners([M({never_woke: nw(null), disks: null})]), /סמן F1/);
});

test('an already marked disk is not offered again', () => {
  const c = make();
  const m = M({disks: [D(1, 'A1'), D(2, 'S62B', {f1_observed: OBS})]});
  assert.deepEqual(Array.from(c.f1PickDisks(m), (d) => d.serial), ['A1']);
  const all = M({disks: [D(1, 'A1', {f1_observed: OBS})]});
  assert.doesNotMatch(c.neverWokeBanners([{...all, never_woke: nw(null)}]), /סמן F1/);
});

test('the picker lists disks by serial with drawer, model and SMART, and posts the serial with the machine', async () => {
  const c = make();
  c.MACHINES.push(M({disks: [D(1, 'A1'), D(2, 'S62B', {smart: 'fail', smart_reason: 'health_failed'})]}));
  c.f1MarkSheet('aa%3Abb');
  const s = c.calls.sheets[0];
  assert.equal(s.title, 'איזה דיסק עצר את ההדלקה ב-F1?');
  assert.deepEqual(Array.from(s.fields[0].options, (o) => [o.value, o.hint]),
    [['A1', 'Model 1 · מגירה 1 · SMART תקין'], ['S62B', 'Model 2 · מגירה 2 · SMART אדום']]);
  await assert.rejects(() => s.onSubmit({serial: ''}), /בחרו דיסק/);          // nothing is guessed
  assert.equal(c.calls.posts.length, 0);
  await s.onSubmit({serial: 'S62B'});
  assert.equal(JSON.stringify(c.calls.posts), JSON.stringify([['/disks/S62B/f1', {mac: 'aa:bb'}]]));
  assert.ok(c.calls.loaded);
  assert.match(s.note, /נצבע אדום בשיכפול/);
  assert.equal(s.submitLabel, 'רשום F1');
});

test('the serial is encoded in the URL; a non-admin and an unknown machine do nothing', async () => {
  const c = make();
  c.MACHINES.push(M({disks: [D(1, 'A/1 #x')]}));
  c.f1MarkSheet('aa%3Abb');
  await c.calls.sheets[0].onSubmit({serial: 'A/1 #x'});
  assert.equal(c.calls.posts[0][0], '/disks/A%2F1%20%23x/f1');
  const n = make({admin: false}); n.MACHINES.push(M()); n.f1MarkSheet('aa%3Abb');
  assert.equal(n.calls.sheets.length, 0);
  const u = make(); u.f1MarkSheet('nope');
  assert.deepEqual(u.calls.toasts, ['מכונה לא נמצאה']);
});

test('the disks page: the tag says date and source, the disk is red, and the sub-line does not repeat it', () => {
  const c = make();
  const cell = c.dkStateCell({state: 'red', reason: 'f1_observed', f1: {date: '2026-11-05', source: 'manual'}, last_good_write: null});
  assert.equal(cell, 'PILL(err:אדום)<br>PILL(err f1-tag:נצפה F1 · 05/11 · סומן ידנית)');
  const auto = c.dkStateCell({state: 'red', reason: 'health_failed', f1: {date: '2026-10-24', source: 'auto'}});
  assert.match(auto, /PILL\(err f1-tag:נצפה F1 · 24\/10 · סומן אוטומטית\)/);
  assert.match(auto, /<span class="sub">R:health_failed<\/span>/);       // its own SMART red stays
  assert.equal(c.dkStateCell({state: 'ok', reason: null, f1: null, last_good_write: 'x'}), 'PILL(ok:תקין)');   // unmarked: as today
});

test('the row menu: clear for a marked disk (mark disabled with the reason), mark for an unmarked one, nothing for a non-admin', () => {
  const c = make();
  const marked = JSON.parse(c.dkRowMenu({serial: 'S1', f1: OBS}).replace(/^MENU\(S1:/, '').slice(0, -1));
  assert.deepEqual(marked, [['נקה סימון F1', 'dkF1Clear("S1")'], ['סמן: נצפה F1 בהדלקה… — כבר מסומן', '', 'disabled']]);
  const plain = JSON.parse(c.dkRowMenu({serial: 'S1', f1: null}).replace(/^MENU\(S1:/, '').slice(0, -1));
  assert.deepEqual(plain, [['סמן: נצפה F1 בהדלקה…', 'dkF1Mark("S1")']]);
  assert.equal(make({admin: false}).dkRowMenu({serial: 'S1', f1: null}), '');
});

test('mark and clear from the disks page go through a confirm sheet (no typed word) and reload', async () => {
  const c = make();
  c.dkF1Mark('S1');
  const [title, , label, run, impacts, danger] = c.calls.confirms[0];
  assert.equal(label, 'סמן: נצפה F1'); assert.equal(danger, false); assert.ok(impacts.length);
  assert.match(title, /^לסמן/);
  await run();
  assert.equal(JSON.stringify(c.calls.posts), JSON.stringify([['/disks/S1/f1', {}]])); assert.ok(c.calls.loadedDisks);
  c.dkF1Clear('S1');
  assert.equal(c.calls.confirms[1][5], false);   // clearing a mark is not destructive: no red button
  await c.calls.confirms[1][3]();
  assert.equal(JSON.stringify(c.calls.dels), JSON.stringify(['/disks/S1/f1']));
});

test('the reason has its Hebrew words (console, F1 text) -- not "SMART תקול"', () => {
  assert.match(consoleJs, /f1_observed: "נצפה F1 בהדלקה"/);
  assert.match(consoleJs, /if \(reason === "f1_observed"\) return SMART_REASON_HE\.f1_observed;/);
});
