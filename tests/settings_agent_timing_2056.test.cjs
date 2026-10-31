// #2056/#2059 (mockup 05, 24/10): wait / power-off timing rows on the settings page.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = fs.readFileSync(path.resolve(__dirname, '../server/static/console.js'), 'utf8');

function functionSource(name) {
  const lines = js.split('\n');
  const start = lines.findIndex(line => new RegExp(`^(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `${name} exists in console.js`);
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === '}');
  return lines.slice(start, end + 1).join('\n');
}
function ctx(settings) {
  const c = vm.createContext({SETTINGS: settings, uiIcon: () => '<svg/>'});
  vm.runInContext('const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };', c);
  const rows = js.slice(js.indexOf('const SETTING_ROWS = ['), js.indexOf('];', js.indexOf('const SETTING_ROWS = [')) + 2);
  const groups = js.slice(js.indexOf('const SETTING_GROUPS = ['), js.indexOf('];', js.indexOf('const SETTING_GROUPS = [')) + 2);
  vm.runInContext(groups + '\n' + rows, c);
  for (const n of ['esc', 'settingRow', 'settingCurrent', 'settingSecondsText', 'ghostBtn', 'agentTimeAppliedHtml', 'agentTimeRowHtml', 'settingsKv'])
    vm.runInContext(functionSource(n), c);
  return c;
}
const base = {lost_wait_seconds: '180', gui_idle_poweroff_seconds: '300', gui_end_poweroff_seconds: '20'};
const row = (c, k) => vm.runInContext(`settingRow("${k}")`, c);

test('the three rows exist, in the cards of the mockup, with the mockup labels', () => {
  const c = ctx(base);
  const lost = row(c, 'lost_wait_seconds'), idle = row(c, 'gui_idle_poweroff_seconds'), end = row(c, 'gui_end_poweroff_seconds');
  assert.equal(lost.card, 'deploy'); assert.equal(lost.label, 'המתנה למחשב שהתנתק באמצע שיכפול');
  assert.equal(idle.card, 'guipower'); assert.equal(idle.label, 'כיבוי כשאין פעילות');
  assert.equal(end.card, 'guipower'); assert.equal(end.label, 'ספירה לכיבוי בסוף משימה');
  assert.ok(vm.runInContext('SETTING_GROUPS', c).some(g => g.card === 'guipower' && g.title === 'כיבוי אוטומטי בגואי' && g.small === 'משכפל · מחשב בנייה · שחזור'));
  assert.ok(vm.runInContext('SETTING_ROWS', c).some(r => r.type === 'lockedrule'), 'the #2024 rule is shown locked');
});

test('values read like the mockup: 3 דקות / אחרי 5 דקות / 20 שניות', () => {
  const c = ctx(base);
  assert.match(c.agentTimeRowHtml(row(c, 'lost_wait_seconds')), /<bdi>3 דקות<\/bdi>/);
  assert.match(c.agentTimeRowHtml(row(c, 'gui_idle_poweroff_seconds')), /<bdi>אחרי 5 דקות<\/bdi>/);
  assert.match(c.agentTimeRowHtml(row(c, 'gui_end_poweroff_seconds')), /<bdi>20 שניות<\/bdi>/);
});

test('"applied on N of M" comes only from what the agents read back (principle 5)', () => {
  const lost = k => { const c = ctx({...base, agent_timing_applied: k}); return c.agentTimeAppliedHtml(row(c, 'lost_wait_seconds')); };
  assert.match(lost({lost_wait_seconds: {applied: 3, connected: 3}}), /class="applied ok">הוחל ב-3 מתוך 3 משכפלים מחוברים</);
  const partial = lost({lost_wait_seconds: {applied: 1, connected: 3}});
  assert.match(partial, /class="applied warn">הוחל ב-1 מתוך 3/); assert.match(partial, /הערך הקודם/);
  assert.match(lost({lost_wait_seconds: {applied: 0, connected: 0}}), /אין מכונות מחוברות/);
  assert.equal(lost(undefined), '', 'a server that did not say anything is not "applied"');
  assert.equal(lost({lost_wait_seconds: {applied: 'x', connected: 3}}), '');
});

test('the dt carries the small help line under the label', () => {
  const c = ctx(base);
  const html = c.settingsKv([['תווית', 'ערך', '', 'הסבר קטן']]);
  assert.match(html, /<dt class="hint">תווית<small class="sub">הסבר קטן<\/small><\/dt>/);
  assert.doesNotMatch(c.settingsKv([['תווית', 'ערך', '']]), /hint/);
});

test('ranges in the sheet = the server ranges (3-30 min, 1-60 min, 10-300 s)', () => {
  const c = ctx(base);
  const r = k => row(c, k);
  assert.deepEqual([r('lost_wait_seconds').min, r('lost_wait_seconds').max, r('lost_wait_seconds').def], [180, 1800, 180]);
  assert.deepEqual([r('gui_idle_poweroff_seconds').min, r('gui_idle_poweroff_seconds').max, r('gui_idle_poweroff_seconds').def], [60, 3600, 300]);
  assert.deepEqual([r('gui_end_poweroff_seconds').min, r('gui_end_poweroff_seconds').max, r('gui_end_poweroff_seconds').def], [10, 300, 20]);
  assert.match(js, /post\("\/settings", \{ \[key\]: String\(n \* div\) \}\)/);
});
