// #953: every image in the library says which disk it fits from
// ("נכנס לדיסק מ-X GB", from the derived min_target_bytes, rounded UP to a
// decimal GB, bytes in the tooltip), how much is used (null = "לא ידוע",
// never 0 -- a partition that would not mount, #84), and how much it weighs
// on the server. Runs the shipped console.js in a vm, not a copy.
const {consoleJs, consoleFiles} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

function setup() {
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: stubNode, getElementById: stubNode,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  for (const f of consoleFiles()) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run('ME={username:"u",role:"admin",idle_seconds:300,capabilities:{}}; toast=()=>{};');
  return run;
}

const IMAGE = {id: 'img_fit230', name: 'Fits 256', min_target_bytes: 229556000000,
               used_bytes: 84540833792, total_compressed_bytes: 57982058496};
test('an unknown requirement is a dash and never a number', () => {
  const run = setup();
  assert.match(run(`imgFits(${JSON.stringify({...IMAGE, min_target_bytes: null})})`),
               /class="muted">—<\/span>/);
});

test('market disk fit uses decimal sold sizes and unknown stays a dash', () => {
  const run = setup();
  // ‏#1300: "256 GB" עם רווח, כמו המוקאפ (console-full.html DISK_CLASSES)
  for (const [bytes, want] of [[100e9, '128 GB'], [128e9, '128 GB'], [129e9, '256 GB'],
    [501e9, '512 GB'], [3e12, '4 TB'], [5e12, '> 4 TB'], [0, '—']]) {
    assert.equal(run(`fitsDisk(${bytes})`), want);
  }
  assert.match(consoleJs(), /"מתאים לדיסק"/);
});

test('the library table and the image drawer both use it', () => {
  const run = setup();
  run(`IMAGES=[${JSON.stringify(IMAGE)}]; IMAGES_FOLDER=null; FOLDERS=[];`);
  // ‏#954 גל 2: הטבלה החדשה — גודל שוק, הבייטים ב-title, הערך LTR.
  const table = run('images()');
  assert.match(table, /<th>מתאים לדיסק<\/th>/);
  // ‏#1300: "256 GB ומעלה" בשורה אחת, הדרישה מתחת
  assert.match(table, /<span class="fit" title="229556000000 bytes"><b><bdi dir="ltr">256 GB<\/bdi><\/b> ומעלה<\/span><span class="sub muted">נדרש לפחות <bdi dir="ltr">230 GB<\/bdi><\/span>/);
  const drawer = run(`imageDrawerHtml(${JSON.stringify(IMAGE)})`);
  // ‏#1300 (נדב): שורה ברורה "כונן מינימלי" — הכונן הקטן ביותר שהאימג' נכנס אליו, מאותה לוגיקה (imgFits)
  assert.match(drawer, /<span class="k">כונן מינימלי<\/span><span class="v"><span class="fit" title="229556000000 bytes"><b><bdi dir="ltr">256 GB<\/bdi><\/b><\/span><span class="sub muted">נדרש לפחות <bdi dir="ltr">230 GB<\/bdi><\/span>/);
  assert.doesNotMatch(drawer, /נכנס לדיסק מ-/);
  assert.match(run(`imageDrawerHtml(${JSON.stringify({...IMAGE, min_target_bytes: null})})`), /כונן מינימלי<\/span><span class="v"><span class="muted">לא ידוע<\/span>/, 'unknown is named, not a dash or a number');
  assert.match(drawer, /בשימוש במקור/);
  assert.match(run(`imageDrawerHtml(${JSON.stringify({...IMAGE, used_bytes: null})})`), /בשימוש במקור<\/[^>]+><[^>]+><span class="muted">לא ידוע/);
});
