// #3064 (שלב 2, נדב 14/11) -- המתג והספים של המוח הם שורות בעמוד ההגדרות, בכרטיס "המוח של השרת":
// מתג שלושה-מצבים (כבוי / צל / פעיל), ותקרת רשת ההפצה. ספים טכניים של המוח (חשד, תקוע, תקרת החזקה, ספי הזיכרון)
// יצאו מההגדרות (נדב 14/11 בערב): המוח קובע אותם.
// נדב: בלי הקלדת מילה לאישור -- מעבר ל"פעיל" עובר חלון אישור רגיל (לא אדום), ושום שדה טקסט.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = consoleJs();

function functionSource(name) {
  const lines = js.split('\n');
  const start = lines.findIndex(line => new RegExp(`^(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `${name} exists in console.js`);
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === '}');
  return lines.slice(start, end + 1).join('\n');
}
function ctx(settings = {}) {
  const posted = [], sheets = [], confirms = [], toasts = [];
  const c = vm.createContext({
    SETTINGS: settings, posted, sheets, confirms, toasts,
    isAdmin: () => true, sheet: (o) => sheets.push(o), toast: (t) => toasts.push(t),
    confirmSheet: (...args) => confirms.push(args),
    post: async (url, body) => { posted.push([url, body]); }, loadSettingsData: async () => {},
  });
  vm.runInContext('const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };', c);
  const from = js.indexOf('const SETTING_GROUPS = [');
  vm.runInContext(js.slice(from, js.indexOf('];', from) + 2), c);
  const rows = js.slice(js.indexOf('const SETTING_ROWS = ['), js.indexOf('];', js.indexOf('const SETTING_ROWS = [')) + 2);
  vm.runInContext(rows, c);
  const modes = js.slice(js.indexOf('const BRAIN_MODES = ['), js.indexOf('];', js.indexOf('const BRAIN_MODES = [')) + 2);
  vm.runInContext(modes, c);
  for (const n of ['esc', 'settingRow', 'settingCurrent', 'settingSecondsText', 'settingNumberText', 'settingsValueSheet',
                   'brainModeLabel', 'brainModeSave', 'brainModeSheet']) vm.runInContext(functionSource(n), c);
  return c;
}
test('#3064: the group is "המוח של השרת", with a foot that says shadow records only and that no percentage is fixed', () => {
  const c = ctx();
  const g = vm.runInContext('SETTING_GROUPS.find((x) => x.card === "brain")', c);
  assert.ok(g, 'a settings group with card "brain"');
  assert.equal(g.title, 'המוח של השרת');
  assert.match(g.foot, /צל/);
  assert.match(g.foot, /אין אחוז קבוע/);
});

test('#3064: the mode row, the network ceiling (the upper bound) and the CPU gate switch (#3456, off by default) and the scheduled-cast switch (#3569) are in that group, in Hebrew; no technical threshold', () => {
  const c = ctx();
  const rows = vm.runInContext('SETTING_ROWS.filter((r) => r.card === "brain")', c);
  assert.deepEqual(JSON.parse(JSON.stringify(rows.map((r) => r.key))),
    ['brain_veto_mode', 'brain_net_ceiling_mbps', 'brain_cpu_gate', 'brain_scheduled_cast']);
  assert.equal(rows[2].type, 'switch');
  assert.ok(!rows[2].defaultOn, 'the CPU gate is off unless the operator turns it on');
  assert.equal(rows[0].type, 'brainmode');
  for (const r of rows) assert.match(r.label, /[א-ת]/, r.key);
});

test('Nadav 14/11 evening: no technical threshold of the brain is a row in the settings screen -- the brain sets them', () => {
  const c = ctx();
  const keys = JSON.parse(JSON.stringify(vm.runInContext('SETTING_ROWS.map((r) => r.key)', c)));
  for (const gone of ['brain_suspect_seconds', 'brain_stalled_seconds', 'brain_hold_ceiling_seconds',
                      'brain_memory_min_returns', 'brain_memory_max_age_days']) {
    assert.ok(!keys.includes(gone), `no ${gone} row`);
    assert.ok(!js.includes(gone), `console.js does not name ${gone} at all`);
  }
});

test('#3064: unset or unknown mode reads as shadow, like the server', () => {
  const c = ctx();
  assert.equal(c.brainModeLabel(''), 'צל (רושם בלבד)');
  assert.equal(c.brainModeLabel('nonsense'), 'צל (רושם בלבד)');
  assert.equal(c.brainModeLabel('off'), 'כבוי');
  assert.equal(c.brainModeLabel('enforce'), 'פעיל (חוסם)');
});

test('#3064: the mode sheet is three radio choices and no typed word; shadow is selected when unset', () => {
  const c = ctx();
  c.brainModeSheet();
  const s = c.sheets[0];
  assert.equal(s.fields.length, 1);
  assert.equal(s.fields[0].type, 'radio');
  assert.equal(s.fields[0].value, 'shadow');
  assert.deepEqual(JSON.parse(JSON.stringify(s.fields[0].options.map((o) => o.value))), ['off', 'shadow', 'enforce']);
  for (const o of s.fields[0].options) assert.ok(o.hint, `${o.value} explains itself`);
});

test('#3064: choosing off or shadow saves at once; choosing the current mode saves nothing', async () => {
  const c = ctx({brain_veto_mode: 'shadow'});
  c.brainModeSheet();
  const s = c.sheets[0];
  assert.equal(await s.onSubmit({mode: 'shadow'}), undefined);
  assert.equal(c.posted.length, 0);
  assert.equal(await s.onSubmit({mode: 'off'}), undefined);
  assert.deepEqual(JSON.parse(JSON.stringify(c.posted)), [['/settings', {brain_veto_mode: 'off'}]]);
  assert.equal(c.confirms.length, 0);
});

test('#3064: enforce goes through a plain confirmation (not red, no typed word) and only then saves', async () => {
  const c = ctx({brain_veto_mode: 'shadow'});
  c.brainModeSheet();
  const next = await c.sheets[0].onSubmit({mode: 'enforce'});
  assert.equal(typeof next, 'function', 'the next window opens from onSubmit, as in the backup-target sheet');
  assert.equal(c.posted.length, 0, 'nothing is saved before the confirmation');
  next();
  assert.equal(c.confirms.length, 1);
  const [title, , label, onConfirm, impacts, danger] = c.confirms[0];
  assert.equal(title, 'להפעיל את הווטו?');
  assert.equal(label, 'הפעלה');
  assert.equal(danger, false, 'enforce is not a destructive button');
  assert.match(impacts[0][1], /בתקרה/);
  await onConfirm();
  assert.deepEqual(JSON.parse(JSON.stringify(c.posted)), [['/settings', {brain_veto_mode: 'enforce'}]]);
});

// stage 3 (K7): the one setting of the network budget -- an upper bound, 0 = measured from the card
const ceilingRange = (() => {
  const py = fs.readFileSync(path.resolve(__dirname, '../server/brain/budget.py'), 'utf8');
  const m = py.match(/CEILING_RANGE = \((\d+), ([\d_]+)\)/);
  assert.ok(m, 'budget.py names CEILING_RANGE');
  return [Number(m[1]), Number(m[2].replace(/_/g, ''))];
})();

test('#3064 stage 3: the ceiling row reads 0 as "measured from the card" and names the range the server validates', () => {
  const c = ctx({brain_net_ceiling_mbps: '0'});
  const r = vm.runInContext('settingRow("brain_net_ceiling_mbps")', c);
  assert.equal(r.type, 'number');
  assert.equal(r.unit, 'Mbps');
  assert.equal(r.min, 0, 'zero is a value: measured from the card');
  assert.equal(r.max, ceilingRange[1]);
  assert.match(r.rangeText, new RegExp(`${ceilingRange[0]}.${ceilingRange[1]}`), 'the refusal names the server range');
  assert.equal(c.settingNumberText(r, '0'), '<span class="muted">נמדד מהכרטיס</span>');
  assert.equal(c.settingNumberText(r, '600'), '<bdi>600 Mbps</bdi>');
});

test('#3064 stage 3: the ceiling sheet posts a number and 0, and refuses text and decimals before posting', async () => {
  const c = ctx({brain_net_ceiling_mbps: '0'});
  c.settingsValueSheet('brain_net_ceiling_mbps');
  const s = c.sheets[0];
  for (const bad of ['abc', '600.5', '', '-1', '100001']) {
    await assert.rejects(s.onSubmit({v: bad}), /תקרת רשת ההפצה: 0 \(נמדד מהכרטיס\) או 10–100000/);
  }
  assert.equal(c.posted.length, 0, 'nothing posted for a refused value');
  await s.onSubmit({v: '600'});
  await s.onSubmit({v: '0'});
  assert.deepEqual(JSON.parse(JSON.stringify(c.posted)),
    [['/settings', {brain_net_ceiling_mbps: '600'}], ['/settings', {brain_net_ceiling_mbps: '0'}]]);
});

