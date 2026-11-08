// #1858 (mockup 10, approved by Nadav 24/10): "המכונה לא הגיעה", and F1 only when the server knows of a disk that stops.
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
function make({admin = true, online = false, role = 'cloner', redDisk = ''} = {}) {
  const c = vm.createContext({
    esc: (s) => String(s ?? '').replace(/[&<>"]/g, (m) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[m])),
    ltr: (t) => `<bdi dir="ltr">${t}</bdi>`,
    machineName: (m) => m.name, machineRole: () => role, encodeId: encodeURIComponent, isAdmin: () => admin,
    monitorRow: () => (online ? {online: true} : null),
    isToday: () => true, fmtClock: (t) => 'C' + t, fmtWhenShort: (t) => 'W' + t,
    netFor: (mac) => (mac === 'aa:bb' ? {last_seen: 'LS'} : null),
    machineRedDisk: () => redDisk, UI: {pill: (cls, text) => `PILL(${cls}:${text})`},
    STATE_PILL: {ok: 'ok', warn: 'warn', err: 'err', run: 'info', '': ''},
  });
  vm.runInContext('const NEVER_WOKE_HE = "לא הגיע";', c);
  for (const n of ['neverWoke', 'neverWokeLabel', 'heDuration', 'neverWokeWhen', 'neverWokeText', 'neverWokeRowSub', 'neverWokeBanners', 'machinePill', 'f1Date', 'f1Why', 'f1PickDisks'])
    vm.runInContext(fn(n), c);
  return c;
}
const F1 = {drawer: 2, model: 'Samsung SSD 870 EVO', serial: 'S2', reason: 'health_failed', basis: 'smart_red', smart_red: true, observed: null};
const nw = (over = {}) => ({state: 'never_woke', sent_at: 'T', waited_s: 270, typical_s: 70, after_s: 180, resent: 0, f1: null, ...over});
const M = (over = {}) => ({mac: 'aa:bb', name: 'CLONER-02', ...over});

test('with a known F1 disk: the title says "maybe stopped on F1", the body names drawer, model and why', () => {
  const t = make().neverWokeText(M(), nw({f1: F1}));
  assert.equal(t.title, 'CLONER-02 לא הגיע — ייתכן שנעצר במסך F1 של ה-BIOS');
  assert.match(t.body, /^במגירה 2 יש דיסק שידוע שעוצר ב-F1: <bdi dir="ltr">Samsung SSD 870 EVO<\/bdi>, SMART אדום\. WoL נשלח ב-CT, ועברו 4:30\.$/);
});

test('an unread SMART says so; an unmapped drawer says "במחשב", never a guessed number', () => {
  const t = make().neverWokeText(M(), nw({f1: {...F1, drawer: null, reason: 'health_unread', model: null}}));
  assert.match(t.body, /^במחשב יש דיסק שידוע שעוצר ב-F1: דיסק, SMART אדום, לא נקרא\./);
});

test('without evidence: the general message, and not one word about F1', () => {
  const t = make().neverWokeText(M(), nw());
  assert.equal(t.title, 'CLONER-02 לא הגיע אחרי 4 דקות');
  assert.equal(t.body, 'WoL נשלח ב-CT. בדרך כלל הוא עונה תוך דקה ו-10 שניות. הוא נראה לאחרונה ב-WLS.');
  assert.doesNotMatch(t.title + t.body, /F1|BIOS/);
  const bare = make().neverWokeText(M({mac: 'zz'}), nw({typical_s: null}));
  assert.equal(bare.body, 'WoL נשלח ב-CT.');   // no learned time and never seen: nothing is invented
});

test('the pill and the label say "maybe F1" only with a known disk', () => {
  const c = make();
  assert.equal(c.neverWokeLabel(nw()), 'לא הגיע');
  assert.equal(c.neverWokeLabel(nw({f1: F1})), 'לא הגיע · ייתכן F1');
  assert.equal(c.machinePill(M({never_woke: nw()})), 'PILL(warn:לא הגיע)');
  assert.equal(c.machinePill(M({never_woke: nw({f1: F1})})), 'PILL(warn:לא הגיע · ייתכן F1)');
});

test('"never arrived" wins over "red disk" (the same evidence), but not over a machine the monitor sees online', () => {
  assert.equal(make({redDisk: 'err'}).machinePill(M({never_woke: nw({f1: F1})})), 'PILL(warn:לא הגיע · ייתכן F1)');
  assert.equal(make({redDisk: 'err'}).machinePill(M()), 'PILL(err:דיסק אדום)');
  assert.equal(make({online: true}).neverWoke(M({never_woke: nw()})), null);
});

test('"unchecked" and a missing field claim nothing', () => {
  const c = make();
  assert.equal(c.neverWoke(M({never_woke: {state: 'unchecked'}})), null);
  assert.equal(c.neverWoke(M()), null);
  assert.equal(c.neverWokeBanners([M({never_woke: {state: 'unchecked'}}), M()]), '');
});

test('the banner has the WoL button for an admin on a cloner, and not for a classroom machine or a non-admin', () => {
  const html = make().neverWokeBanners([M({never_woke: nw({f1: F1})})]);
  assert.match(html, /class="nw-alert" role="alert"/); assert.match(html, /שלח WoL שוב/); assert.match(html, /wakeMachine\('aa%3Abb'\)/);
  assert.doesNotMatch(make({admin: false}).neverWokeBanners([M({never_woke: nw()})]), /wakeMachine/);
  assert.doesNotMatch(make({role: 'classroom'}).neverWokeBanners([M({never_woke: nw()})]), /wakeMachine/);
});

test('a machine name cannot inject markup into the banner', () => {
  const html = make().neverWokeBanners([M({name: '<img onerror=x>', never_woke: nw()})]);
  assert.doesNotMatch(html, /<img/); assert.match(html, /&lt;img onerror=x&gt;/);
});

test('the row sub-line names the drawer and the reason only with a known disk', () => {
  const c = make();
  assert.equal(c.neverWokeRowSub(nw({f1: F1})), '<span class="sub">מגירה 2: SMART אדום</span>');
  assert.equal(c.neverWokeRowSub(nw()), ''); assert.equal(c.neverWokeRowSub(null), '');
});

test('durations read like the mockup', () => {
  const c = make();
  assert.equal(c.heDuration(70), 'דקה ו-10 שניות'); assert.equal(c.heDuration(120), '2 דקות'); assert.equal(c.heDuration(45), '45 שניות');
});

test('the machines page wires the banners, the header chip and the state (and the version moved)', () => {
  assert.match(js, /neverWokeBanners\(all\) \+ machinesTableCard\(\)/);
  assert.match(js, /UI\.pill\("warn", `\$\{noShow\} \$\{NEVER_WOKE_HE\}`\)/);
  const state = fn('machineState');
  assert.ok(state.indexOf('neverWoke(m)') > state.indexOf('m.prompt') && state.indexOf('neverWoke(m)') < state.indexOf('machineRole(m)'));
});
