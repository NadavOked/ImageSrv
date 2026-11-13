// #649 שלב 1 — דף "ארגז כלים": הקטלוג מ-/tools/catalog כקבוצה = כרטיס, טבלה
// עם ☐ בנייה/שיכפול ו-☐ תלמיד לכל כלי (שתי בחירות נפרדות — הכרעת נדב 17/09),
// סיכון כ-pill, גודל "ארוז"/מספר/"לא נמדד", סינון, "סמן את המומלצים"/"נקה"
// לקבוצה על היעד שנבחר, ו"שמור" שנדלק רק כשיש שינוי ושולח PUT עם שתי הרשימות.
// הבדיקה מריצה את console.js המשוגר ב-vm עם fetch מזויף (כמו drivers_page).
const {consoleFiles} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}}, hidden: false, disabled: false, checked: false,
  classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

function setup(fixtures, role = 'admin') {
  const toasts = [], requests = [], nodes = {};
  const byId = (id) => (nodes[id] ||= stubNode());
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: stubNode, getElementById: byId,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options = {}) => {
      const key = url.replace('/api/console', '').split('?')[0];
      requests.push({url: key, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
      const f = typeof fixtures[key] === 'function' ? fixtures[key](requests.at(-1)) : fixtures[key];
      if (f instanceof Error) return {status: f.status || 500, ok: false, headers: {get: () => null}, json: async () => ({detail: f.message})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => f ?? {}};
    },
  });
  for (const f of ['progress.js', ...consoleFiles()]) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run(`ME={username:"u",role:"${role}",idle_seconds:300,capabilities:{}}; toast=(m)=>__toasts.push(m); current="tools"; globalThis.rendered=0; renderCurrent=()=>{ rendered++; };`);
  ctx.__toasts = toasts;
  return {run, toasts, requests, nodes};
}

const TOOL = (o) => ({id: o.id, group: o.group, title_he: o.title, what: o.what || 'מה זה עושה', binary: o.binary || o.id, risk: o.risk || 'ro',
  risk_note: null, packed: !!o.packed, size_kb: o.size_kb ?? null, size_source: o.size_source || (o.packed ? 'כבר ארוז' : 'לא נמצא'),
  recommended: !!o.rec, moved_from: o.moved_from || null});
const G1 = 'המחשב לא עולה', G2 = 'שחזור קבצים';
const TOOLS = [
  TOOL({id: 'boot-order', group: G1, title: 'שינוי סדר האתחול', binary: 'efibootmgr -o', risk: 'rw', size_kb: 93, size_source: '93kB', rec: true}),
  TOOL({id: 'testdisk', group: G1, title: 'שחזור מחיצות <b>וסקטור</b>', binary: 'testdisk', risk: 'rw', size_kb: 1536, size_source: '1.5MB (1/2) · 1.8MB (3)', rec: true}),
  TOOL({id: 'win-bcd', group: G1, title: 'קריאת BCD', binary: 'win-bcd', packed: true}),
  TOOL({id: 'zap', group: G2, title: 'מחיקת טבלת מחיצות', binary: 'sgdisk -Z', risk: 'destroy', packed: true, size_source: 'כבר נארז'}),
  TOOL({id: 'photorec', group: G2, title: 'שחזור לפי חתימה', binary: 'photorec', size_source: 'כלול ב-testdisk', rec: true}),
  TOOL({id: 'ddrescue-image', group: G2, title: 'שחזור דיסק גוסס', binary: 'ddrescue', risk: 'rw', size_kb: 466, size_source: '466kB (R11/disk)', moved_from: 'הדיסק חשוד'}),
];
const CATALOG = (selection = {build: [], student: []}) => ({groups: [G1, G2], tools: TOOLS, selection,
  summary: {build: {count: selection.build.length, size_kb_known: 0, size_unknown: 0}, student: {count: selection.student.length, size_kb_known: 0, size_unknown: 0}}});

test('the page is a placeholder before the read, and an error note (not an empty catalog) when /tools/catalog fails', async () => {
  const s = setup({'/tools/catalog': new Error('שרת נפל')});
  assert.match(s.run('toolsPage()'), /placeholder|טוען/i);
  await s.run('loadTools()');
  const html = s.run('toolsPage()');
  assert.match(html, /לא נקרא/);
  assert.match(html, /שרת נפל/);
  assert.doesNotMatch(html, /tools-save/, 'אין "שמור" על קטלוג שלא נקרא');
  assert.equal(s.toasts.length, 1);
});

test('each group is a card with its own table; each tool row has a build checkbox AND a student checkbox, risk pill, size cell — and no .acts (#1032)', async () => {
  const s = setup({'/tools/catalog': CATALOG()});
  await s.run('loadTools()');
  const html = s.run('toolsPage()');
  assert.equal((html.match(/class="c12 card"><div class="card-h"><span>/g) || []).length, 2, 'כרטיס לכל קבוצה');
  assert.match(html, new RegExp(`<span>${G1} <small id="tools-grp-0">3 כלים · 0 לבנייה · 0 לתלמיד</small>`));
  assert.match(html, /id="tool-build-boot-order"/);
  assert.match(html, /id="tool-student-boot-order"/);
  assert.match(html, /aria-label="בנייה\/שיכפול: שינוי סדר האתחול"/);
  assert.match(html, /aria-label="לתלמיד \(v2\): שינוי סדר האתחול"/);
  assert.match(html, /שחזור מחיצות &lt;b&gt;וסקטור&lt;\/b&gt;/, 'הכותרת מוברחת');
  assert.match(html, /<span class="mono">efibootmgr -o<\/span>/);
  assert.match(html, /<span class="pill warn">משנה<\/span>/);
  assert.match(html, /<span class="pill err">מוחק<\/span>/);
  assert.match(html, /title="destroy — מוחק נתונים: דורש חלון אישור לפני ההרצה"/);
  assert.match(html, /<span class="pill ">קריאה<\/span>/);
  assert.match(html, /<span class="st ok">ארוז<\/span>/);
  assert.match(html, /title="1.5MB \(1\/2\) · 1.8MB \(3\)">1.5 MB</, 'המספר עם המקור ב-title');
  assert.match(html, /title="כלול ב-testdisk">לא נמדד</, '"לא נמדד" ולא 0');
  assert.match(html, /מקבוצת "הדיסק חשוד" במסמך/);
  assert.match(html, /pill info" title="בשורת 'ההמלצה שלי' של הקבוצה">מומלץ/);
  const bodies = html.match(/<tbody>[\s\S]*?<\/tbody>/g);
  assert.equal(bodies.length, 2);
  for (const rows of bodies) assert.doesNotMatch(rows, /class="acts"/, 'שום דבר לא מופיע/נעלם ב-hover (הכפתורים רק בכותרת הכרטיס)');
  assert.match(html, /id="tools-save"[^>]*disabled/, '"שמור" כבוי בלי שינוי');
  assert.match(html, /id="tools-cancel"[^>]* hidden/);
  assert.match(html, /id="tools-pill">0 לבנייה · 0 לתלמיד · תוספת משוערת ~0 kB</);
});

test('ticking boxes updates the header pill, the group caption and the save button in place — without re-rendering — and the two selections stay separate', async () => {
  const s = setup({'/tools/catalog': CATALOG()});
  await s.run('loadTools()');
  s.run('toolsPage(); rendered = 0');
  s.run("toolsToggle('boot-order','build',true); toolsToggle('testdisk','build',true); toolsToggle('photorec','student',true); toolsToggle('win-bcd','build',true)");
  assert.equal(s.run('rendered'), 0, 'בלי renderCurrent — הסינון לא מאבד פוקוס');
  assert.equal(s.nodes['tools-pill'].textContent, '3 לבנייה · 1 לתלמיד · תוספת משוערת ~1.6 MB (+1 לא נמדדו)');
  assert.equal(s.nodes['tools-save'].disabled, false);
  assert.equal(s.nodes['tools-cancel'].hidden, false);
  assert.equal(s.nodes['tools-grp-0'].textContent, '3 כלים · 3 לבנייה · 0 לתלמיד');
  assert.equal(s.nodes['tools-grp-1'].textContent, '3 כלים · 0 לבנייה · 1 לתלמיד');
  assert.equal(s.run('JSON.stringify([...TOOLS_DRAFT.build])'), JSON.stringify(['boot-order', 'testdisk', 'win-bcd']));
  assert.equal(s.run('JSON.stringify([...TOOLS_DRAFT.student])'), JSON.stringify(['photorec']));
  s.run("toolsToggle('boot-order','build',false)");
  assert.equal(s.run('JSON.stringify([...TOOLS_DRAFT.build])'), JSON.stringify(['testdisk', 'win-bcd']));
  assert.equal(s.run('toolsSummary("build").size_kb_known'), 1536, 'ארוז = 0, לא נמדד לא נסכם');
});

test('"סמן את המומלצים" / "נקה" act on the group and on the target chosen in the filter bar (build / student / both)', async () => {
  const s = setup({'/tools/catalog': CATALOG()});
  await s.run('loadTools()');
  s.run('toolsPage()');
  s.run("toolsGroupMark(0,'recommended')");
  assert.equal(s.run('JSON.stringify([...TOOLS_DRAFT.build])'), JSON.stringify(['boot-order', 'testdisk']));
  assert.equal(s.run('JSON.stringify([...TOOLS_DRAFT.student])'), JSON.stringify([]));
  s.run("toolsFilter('scope','student'); toolsGroupMark(1,'recommended')");
  assert.equal(s.run('JSON.stringify([...TOOLS_DRAFT.student])'), JSON.stringify(['photorec']));
  assert.equal(s.run('JSON.stringify([...TOOLS_DRAFT.build])'), JSON.stringify(['boot-order', 'testdisk']), 'הקבוצה השנייה לא נגעה בבנייה');
  s.run("toolsFilter('scope','both'); toolsToggle('zap','build',true); toolsGroupMark(1,'clear')");
  assert.equal(s.run('JSON.stringify([...TOOLS_DRAFT.build])'), JSON.stringify(['boot-order', 'testdisk']));
  assert.equal(s.run('JSON.stringify([...TOOLS_DRAFT.student])'), JSON.stringify([]));
  assert.equal(s.nodes['tool-build-zap'].checked, false, 'התיבה עצמה מתעדכנת');
});

test('the filter bar narrows rows by text, risk, recommended and packed — inside the groups container, keeping empty groups as an empty state', async () => {
  const s = setup({'/tools/catalog': CATALOG()});
  await s.run('loadTools()');
  s.run('toolsPage(); rendered = 0');
  s.run("toolsFilter('q','efiboot')");
  const html = s.nodes['tools-groups'].innerHTML;
  assert.match(html, /id="tool-build-boot-order"/);
  assert.doesNotMatch(html, /id="tool-build-testdisk"/);
  assert.match(html, /אין כלים בקבוצה הזו שמתאימים לסינון/, 'הקבוצה השנייה ריקה ולא נעלמת');
  assert.equal(s.nodes['tools-count'].textContent, '1 מתוך 6 כלים');
  s.run("toolsFilter('q',''); toolsFilter('risk','destroy')");
  assert.equal(s.run('JSON.stringify(TOOLS.tools.filter(toolsVisible).map(t=>t.id))'), JSON.stringify(['zap']));
  s.run("toolsFilter('risk',''); toolsFilter('rec',true)");
  assert.equal(s.run('JSON.stringify(TOOLS.tools.filter(toolsVisible).map(t=>t.id))'), JSON.stringify(['boot-order', 'testdisk', 'photorec']));
  s.run("toolsFilter('rec',false); toolsFilter('packed',true)");
  assert.equal(s.run('JSON.stringify(TOOLS.tools.filter(toolsVisible).map(t=>t.id))'), JSON.stringify(['win-bcd', 'zap']));
  assert.equal(s.run('rendered'), 0);
});

test('"שמור" PUTs both lists, toasts the server counts and reloads; a refused save keeps the draft', async () => {
  let saved = null;
  const s = setup({
    '/tools/catalog': () => CATALOG(saved || {build: [], student: []}),
    '/tools/selection': (req) => { saved = req.body; return {ok: true, selection: req.body, summary: {build: {count: req.body.build.length}, student: {count: req.body.student.length}}}; },
  });
  await s.run('loadTools()');
  s.run('toolsPage()');
  await s.run('toolsSave()');
  assert.equal(s.requests.filter(r => r.method === 'PUT').length, 0, 'בלי שינוי — אין PUT');
  s.run("toolsToggle('testdisk','build',true); toolsToggle('testdisk','student',true); toolsToggle('zap','build',true)");
  await s.run('toolsSave()');
  const put = s.requests.find(r => r.method === 'PUT');
  assert.equal(put.url, '/tools/selection');
  assert.deepEqual(put.body, {build: ['testdisk', 'zap'], student: ['testdisk']});
  assert.deepEqual(s.toasts, ['הבחירה נשמרה: 2 לבנייה · 1 לתלמיד']);
  assert.equal(s.run('JSON.stringify(TOOLS.selection)'), JSON.stringify({build: ['testdisk', 'zap'], student: ['testdisk']}), 'נקרא מחדש מהשרת');
  assert.equal(s.run('toolsDirty()'), false);
  assert.equal(s.run('rendered'), 2, 'renderCurrent אחרי כל loadTools (טעינה + אחרי השמירה)');

  const refused = setup({'/tools/catalog': CATALOG(), '/tools/selection': Object.assign(new Error('build: כלים שאינם בקטלוג: nope'), {status: 422})});
  await refused.run('loadTools()');
  refused.run("toolsPage(); toolsToggle('testdisk','build',true)");
  await refused.run('toolsSave()');
  assert.match(refused.toasts.at(-1), /השמירה נכשלה: build: כלים שאינם בקטלוג: nope/);
  assert.equal(refused.run('JSON.stringify([...TOOLS_DRAFT.build])'), JSON.stringify(['testdisk']), 'הטיוטה נשארת לתיקון');
  assert.equal(refused.run('toolsDirty()'), true);
});

test('"בטל שינויים" returns the draft to the saved selection and re-renders', async () => {
  const s = setup({'/tools/catalog': CATALOG({build: ['win-bcd'], student: []})});
  await s.run('loadTools()');
  s.run("toolsPage(); toolsToggle('testdisk','build',true); toolsToggle('win-bcd','build',false)");
  assert.equal(s.run('toolsDirty()'), true);
  s.run('toolsCancel()');
  assert.equal(s.run('JSON.stringify([...TOOLS_DRAFT.build])'), JSON.stringify(['win-bcd']));
  assert.equal(s.run('toolsDirty()'), false);
  assert.equal(s.run('rendered'), 2, 'הטעינה + הביטול');
});

test('the tools page is admin-only: pageAllowed refuses deploy, and the tree node carries data-admin', () => {
  const s = setup({}, 'deploy');
  s.run('ME.capabilities.tools=true');
  assert.equal(s.run("pageAllowed('tools')"), false);
  const admin = setup({});
  admin.run('ME.capabilities.tools=true');
  assert.equal(admin.run("pageAllowed('tools')"), true);
  const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  assert.match(index, /<div class="inventory-node hidden" data-page="tools" data-admin data-cap="tools" data-adv onclick="selectPageById\('tools'\)"/);   // #3025: advanced only
});

// --- v1 בלי ארגז הכלים (הכרעת נדב 19/09; v1.1 = הכלים) --------------------------------
test('v1: with capabilities.tools off the sidebar node starts hidden behind data-cap and the page is unreachable even for admin', () => {
  const admin = setup({});                                   // capabilities:{} — כמו /me של v1
  assert.equal(admin.run("pageAllowed('tools')"), false, 'v1: admin cannot route to #tools');
  admin.run('ME.capabilities.tools=false');
  assert.equal(admin.run("pageAllowed('tools')"), false);
  admin.run('ME.capabilities.tools=true');
  assert.equal(admin.run("pageAllowed('tools')"), true, 'v1.1: the same flag brings the page back');
  // הצומת בסיילד: כמו כיתות (#1081) — מתחיל מוסתר, showApp מדליק לפי data-cap.
  const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const node = index.slice(index.indexOf('data-page="tools"') - 60, index.indexOf('data-page="tools"') + 60);
  assert.match(node, /class="inventory-node hidden"/);
  assert.match(node, /data-cap="tools"/);
});
