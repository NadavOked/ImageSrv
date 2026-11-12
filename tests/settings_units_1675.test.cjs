// #1675 — שורת ערך עם יחידה: יחידה עברית אינה עטופה ב-dir="ltr" (שם "0 שגיאות" נקרא "שגיאות 0"),
// ו-0 של הגדרה שבה 0 = כבוי מוצג "כבויה". ובשורה האחת של ההפעלה מחדש (סידור 17/10).
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
  const c = vm.createContext({SETTINGS: settings});
  vm.runInContext('const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };', c);
  const rows = js.slice(js.indexOf('const SETTING_ROWS = ['), js.indexOf('];', js.indexOf('const SETTING_ROWS = [')) + 2);
  const days = js.match(/const REBOOT_DAYS = \[[^\]]*\];/)[0];
  vm.runInContext(rows + '\n' + days, c);
  for (const n of ['esc', 'settingRow', 'settingCurrent', 'settingSecondsText', 'settingNumberText', 'autoRebootText']) vm.runInContext(functionSource(n), c);
  return c;
}

test('#1675: a Hebrew unit reads in Hebrew order — no dir="ltr" around "N שגיאות"', () => {
  const c = ctx();
  const crc = vm.runInContext('settingRow("crc_warn_threshold")', c);
  const html = c.settingNumberText(crc, '25');
  assert.equal(html, '<bdi>25 שגיאות</bdi>');
  assert.doesNotMatch(html, /dir="ltr"/);
  assert.doesNotMatch(js, /ltr\(`\$\{v\} \$\{r\.unit\}`\)/, 'the old ltr() around value + unit is gone');
});

test('#1675: 0 of the CRC warning means off — shown as "כבויה", never "0 שגיאות"', () => {
  const c = ctx();
  const crc = vm.runInContext('settingRow("crc_warn_threshold")', c);
  assert.equal(c.settingNumberText(crc, '0'), '<span class="muted">כבויה</span>');
  const gb = vm.runInContext('settingRow("min_free_gb")', c);
  assert.equal(c.settingNumberText(gb, '0'), '<bdi>0 GB</bdi>', 'only a setting where 0 = off says off');
  assert.equal(c.settingNumberText(gb, '20'), '<bdi>20 GB</bdi>');
  assert.equal(c.settingNumberText(gb, ''), '<span class="muted">לא נקבע</span>');
  // the idle logout is a switch + "אחרי N דקות" since 18/10 (console_idle.test.cjs)
});

test('scheduled reboot reads as one sentence: off / every day at / every <day> at', () => {
  assert.equal(ctx({auto_reboot: 'off', auto_reboot_time: '03:00', auto_reboot_weekday: '0'}).autoRebootText(), 'כבוי', 'off does not show a time and a day');
  assert.equal(ctx({auto_reboot: 'daily', auto_reboot_time: '03:00', auto_reboot_weekday: '0'}).autoRebootText(), 'כל יום ב-03:00');
  assert.equal(ctx({auto_reboot: 'weekly', auto_reboot_time: '04:30', auto_reboot_weekday: '6'}).autoRebootText(), 'כל יום ראשון ב-04:30');
});
