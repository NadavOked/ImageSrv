// #3157 (נדב 14/11, אפשרות ג'): ביומן, ברירת המחדל "דורש תשומת לב" (warn/err), ו"הכול" בלחיצה.
// הבורר יושב בשורת הסינון הקיימת, כמו "סוג" ו"משתמש" -- לא מסך ולא אזור חדש.
'use strict';
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');

const JS = consoleJs();

function functionSource(name) {
  const lines = JS.split('\n');
  const start = lines.findIndex((line) => new RegExp(`^(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `${name} not found`);
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === '}');
  return lines.slice(start, end + 1).join('\n');
}

function constSource(name) {
  // `const X = ...;` בשורה אחת, או עד השורה הראשונה שנגמרת ב-`;` (logInit). חסר = undefined,
  // כדי שהבקרה השלילית תיכשל על ההתנהגות ולא על "לא נמצא".
  const lines = JS.split('\n');
  const start = lines.findIndex((line) => line.startsWith(`const ${name} = `));
  if (start < 0) return `var ${name} = undefined;`;
  const end = lines.findIndex((line, i) => i >= start && line.trimEnd().endsWith(';'));
  return lines.slice(start, end + 1).join('\n').replace(/^const /, 'var ');
}

function ctx() {
  const c = vm.createContext({URLSearchParams, USERS: []});
  vm.runInContext('var ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };', c);
  for (const name of ['logInit', 'LOG_RANGES', 'LOG_LEVELS']) vm.runInContext(constSource(name), c);
  for (const name of ['esc', 'logSince', 'logParams', 'logBarHtml']) vm.runInContext(functionSource(name), c);
  vm.runInContext('var LOG = logInit();', c);
  return c;
}

test('the journal opens on "needs attention" and asks the server for it', () => {
  const c = ctx();
  assert.equal(vm.runInContext('LOG.level', c), 'attention');
  const p = new URLSearchParams(vm.runInContext('logParams().toString()', c));
  assert.equal(p.get('level'), 'attention');
});

test('"all" sends no level, so the server returns every severity', () => {
  const c = ctx();
  vm.runInContext('LOG.level = ""', c);
  const p = new URLSearchParams(vm.runInContext('logParams().toString()', c));
  assert.equal(p.has('level'), false);
});

test('the severity picker is one more select in the existing filter bar', () => {
  const c = ctx();
  const html = vm.runInContext('logBarHtml([])', c);
  assert.match(html, /^<div class="dg-bar">/);
  const sel = html.match(/<select aria-label="חומרה" onchange="logFilter\('level', this.value\)">(.*?)<\/select>/);
  assert.ok(sel, html);
  assert.match(sel[1], /<option value="attention" selected>דורש תשומת לב<\/option>/);
  assert.match(sel[1], /<option value="">הכול<\/option>/);
});
