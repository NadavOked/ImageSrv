// #1032: סבב ליטוש אחרי 7 גלי #954 — שני התיקונים ה"גמורים" של הבקשה:
// (1) בלי layout shift ב-hover בשום טבלה — .acts תופס מקום תמיד (visibility,
//     לא display:none), ומודגש רק ב-hover/focus-within/sel/group/acts-on.
// (2) שורת סינון אחת, אותו גובה (30px), אותו יישור (flex-start — לא center:
//     center נתן היסט מדוד של 7px בין input ל-select באותו גובה מדויק בכרום,
//     כנראה קוונטת baseline לרכיבי טופס; flex-start ניטרלי ותלוי-ריפוד בלבד).
// בלי דפדפן: CSS נבדק כטקסט (lastDecl, כמו console_usability.test.cjs),
// והמבנה (כל שורה מכילה תא .acts) נבדק ע"י הרצת console.js ב-vm ובדיקת
// הפלט. המדידה בדפדפן אמיתי (CDP headless, 1000x400, שורה 2 בטבלה שמעולם
// לא עברה hover): לפני התיקון עמודותיה [194,384,239,60]px במנוחה קפצו ל-
// [151,298,186,244]px ברגע ש*שורה אחרת* עברה hover (הטבלה כולה "קפצה" —
// זה בדיוק התלונה); אחרי התיקון העמודות [151,298,186,244]px קבועות בשני
// המצבים.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');

/* הערך האחרון של מאפיין עבור סלקטור, מחוץ ל-@media (כמו ב-console_usability.test.cjs).
   תומך גם בכלל עם רשימת סלקטורים מופרדת בפסיקים (a,b{...}) — בודק כל איבר ברשימה. */
function lastDecl(css, selector, prop) {
  const noComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const noMedia = noComments.replace(/@media[^{]*\{(?:[^{}]*\{[^{}]*\})*\s*\}/g, '');
  let value = null;
  for (const m of noMedia.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = m[1].split(',').map((s) => s.trim());
    if (!selectors.includes(selector)) continue;
    const d = m[2].match(new RegExp('(?:^|;)\\s*' + prop + ':\\s*([^;]+)'));
    if (d) value = d[1].trim();
  }
  return value;
}
/* אותו בלוק שמאחד את כל כללי @media(max-width:740px) לגוף אחד. */
function narrowCss(css) {
  return [...css.matchAll(/@media\s*\(max-width:\s*740px\)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}/g)].map((m) => m[1]).join('\n');
}

// ---- 1: .acts תופס מקום תמיד; מודגש ב-visibility, לא ב-display ----------

test('1032/1: .page .dg td .acts is always display:flex (reserves its box) and starts visibility:hidden — not display:none', () => {
  const css = read('console.css');
  assert.equal(lastDecl(css, '.page .dg td .acts', 'display'), 'flex',
    'the cell must render (and take up space) even when nothing is hovered — a display:none cell has zero width, which is exactly what let the column width jump on hover');
  assert.equal(lastDecl(css, '.page .dg td .acts', 'visibility'), 'hidden',
    'hidden by visibility, not display — the box stays in the layout so no column ever resizes on hover');
});

test('1032/1: hover, focus-within, selection, group rows and acts-on tables reveal the buttons via visibility:visible (not display:flex, which would re-introduce the shift)', () => {
  const css = read('console.css');
  const revealSelectors = [
    '.page .dg tr:hover td .acts',
    '.page .dg tr:focus-within td .acts',
    '.page .dg tr.sel td .acts',
    '.page .dg tr.group td .acts',
    '.page .dg.acts-on td .acts',
  ];
  for (const sel of revealSelectors) {
    assert.equal(lastDecl(css, sel, 'visibility'), 'visible', `${sel} must set visibility:visible`);
    assert.equal(lastDecl(css, sel, 'display'), null, `${sel} must not set display (would fight the reserved-space rule)`);
  }
});

test('1032/1: at <=740px (touch — no hover concept) .acts is unconditionally visible, not display:flex', () => {
  const narrow = narrowCss(read('console.css'));
  assert.match(narrow, /\.page \.dg td \.acts\{visibility:visible\}/);
  assert.doesNotMatch(narrow, /\.page \.dg td \.acts\{display:flex\}/, 'the old mobile override must be gone too — it would fight the reserved-space rule the same way');
});

// ---- 2: שורת סינון אחת, אותו גובה, אותו יישור ----------------------------

test('1032/2: .dg-bar is a single non-wrapping row by default (flex-start — center measured a 7px offset between <input> and <select> at identical CSS height)', () => {
  const css = read('console.css');
  assert.equal(lastDecl(css, '.page .dg-bar', 'display'), 'flex');
  assert.equal(lastDecl(css, '.page .dg-bar', 'flex-wrap'), 'nowrap',
    'nowrap: the old flex-wrap:wrap let the search input/selects/count split across lines unpredictably — the exact bug nadav photographed on the machines page');
  assert.equal(lastDecl(css, '.page .dg-bar', 'align-items'), 'flex-start');
  assert.equal(lastDecl(css, '.page .dg-bar', 'overflow-x'), 'auto', 'safety net for a filter bar with many long group names — scroll, not silent clipping (principle 4)');
});

test('1032/2: the filter bar controls share one height (30px) across input, select and the datetime-local variant', () => {
  const css = read('console.css');
  assert.equal(lastDecl(css, '.page .dg-bar input', 'height'), '30px');
  assert.equal(lastDecl(css, '.page .dg-bar select', 'height'), '30px');
  assert.equal(lastDecl(css, '.page .dg-bar input[type="datetime-local"]', 'height'), '30px');
});

test('1032/2: at <=740px the bar goes back to wrapping (stacked, full-width controls) in both machines-page and image-tree contexts', () => {
  const narrow = narrowCss(read('console.css'));
  const blocks = narrow.split(/(?=\.page \.dg-bar\{flex-wrap:wrap\})/).filter((b) => b.includes('.page .dg-bar{flex-wrap:wrap}'));
  assert.ok(blocks.length >= 2, `expected the wrap-override in both the split (images) and the machines-table mobile blocks, found ${blocks.length}`);
});

// ---- מבנה: כל שורה מכילה תא .acts, גם כשאין הרשאת עריכה ------------------

function setupConsole() {
  const node = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
    classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, insertAdjacentHTML(_p, html) { this.innerHTML += html; },
    setAttribute() {}, removeAttribute() {}, focus() {}});
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, String, Array, encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelector: node, querySelectorAll: () => [], getElementById: node,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); }, get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false})}, localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(read('progress.js'), ctx);
  vm.runInContext(read('console.js'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  return run;
}

test('1032: an image row always carries a (non-empty) .acts cell — the column that must never disappear and reappear', () => {
  const run = setupConsole();
  run('ME={username:"admin",role:"admin",capabilities:{}};');
  const row = run(`imageRow({id:'img1',name:'Test',folder:'',family:256,os:'windows',created:'2026-01-01T00:00:00Z',total_compressed_bytes:1})`);
  const acts = row.cells[row.cells.length - 1];
  assert.match(acts, /^<button type="button" class="kebab"/, 'stage 2: the last cell is the row ⋯ menu — never an empty reserved cell');
  assert.match(acts, /data-menu="[^"]*פרטים/);
});

test('1032: a machine row always carries a non-empty .acts cell (at least "פרטים")', () => {
  const run = setupConsole();
  const row = run(`machineRowHtml({mac:'aa:bb:cc:dd:ee:ff'})`);
  const acts = row.cells[row.cells.length - 1];
  assert.match(acts, /^<button type="button" class="kebab"/, 'stage 2: the last cell is the row ⋯ menu — never an empty reserved cell');
  assert.match(acts, /data-menu="[^"]*פרטים/);
});
