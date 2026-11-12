// נדב 18/10: "למה ליד חיפה יש עיגול ירוק וליד TA אין?" ו"אין רשת הפצה בחיפה, והוא מראה ירוק בעץ וכתום
// בסקירה". נקודה אחת לכל שרת בעץ — TA וכל שרת משנה — מאותה פונקציה ומאותן בדיקות /health שהסקירה של אותו
// שרת מציגה ב"דורש טיפול". מחובר לבדו אינו ירוק; לא נמדד אינו ירוק (עיקרון 5). הקופסה בתחתית העץ יצאה.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../server/static');
const source = consoleJs();
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const cut = (a, b) => { const i = source.indexOf(a), j = source.indexOf(b, i); assert.ok(i > 0 && j > i, a); return source.slice(i, j); };

function dot() {
  const attrs = {};
  return {className: 'status unk', attrs, setAttribute(k, v) { attrs[k] = v; }, getAttribute(k) { return attrs[k]; }};
}
const DEPLOY_WARN = {id: 'deploy_net', label: 'הגדר את רשת ההפצה', state: 'warn', detail: 'רשת ההפצה לא הוגדרה — בחר כרטיס בלשונית רשת הפצה'};
const OK = [{id: 'server', label: 'שרת', state: 'ok'}, {id: 'tftp_port', label: 'TFTP', state: 'ok'}];

function setup(answers = {}) {
  const dots = {'': dot(), haifa: dot()}, rows = {haifa: {attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }}};
  const calls = [], rail = [];
  const ctx = vm.createContext({
    ME: {role: 'admin'}, SECONDARY_STATUS: {}, calls, rail, Date,
    CSS: {escape: (s) => s},
    document: {
      getElementById: (id) => (id === 'primaryServerStatus' ? dots[''] : null),
      querySelector: (sel) => {
        const m = sel.match(/data-secondary(-status)?="([^"]*)"/);
        if (!m) return null;
        return m[1] ? dots[m[2]] || null : rows[m[2]] || null;
      },
    },
    isAdmin: () => true, updateAlertBadge() {}, paintRailFoot: (cls, text) => rail.push([cls, text]),
    encodeId: (v) => encodeURIComponent(v),
    apiFetch: async (url) => {
      calls.push(url);
      const a = answers[url];
      if (!a) { const e = new Error('אין רשת'); e.network = true; throw e; }
      return {ok: a.status === 200, status: a.status, json: async () => a.body};
    },
  });
  vm.runInContext(cut('const SERVER_HEALTH = {};', 'function updateAlertBadge()')
    + cut('function markSecondaryStatus(', '/* F13: רענון תקופתי')
    + ';this.t={serverDotState,paintServerDot,noteServerHealth,refreshServerDots,markSecondaryStatus,SERVER_HEALTH,setNodes:(n)=>{SIDEBAR_NODES=n;}};', ctx);
  return {t: ctx.t, dots, rows, calls, rail, ctx};
}
const TA = '/api/console/health', HAIFA = '/api/console/storage-nodes/haifa/console/health';

test('Nadav 18/10: a connected secondary with "deploy network not set" is orange in the tree -- the same as its overview -- and the tooltip says why', async () => {
  const {t, dots, calls} = setup({[TA]: {status: 200, body: OK}, [HAIFA]: {status: 200, body: [...OK, DEPLOY_WARN]}});
  const haifa = {id: 'haifa', label: 'חיפה'};
  t.setNodes([haifa]);
  t.markSecondaryStatus(haifa, true, '');   // the existing connectivity probe: connected
  assert.notEqual(dots.haifa.className, 'status ok', 'connected alone is not green -- its health was not read yet');
  assert.equal(dots.haifa.className, 'status unk');
  await t.refreshServerDots();
  assert.deepEqual(calls.sort(), [TA, HAIFA].sort(), 'one /health per server; the secondary through the relay that feeds its overview');
  assert.equal(dots.haifa.className, 'status warn', 'orange, as its overview says');
  assert.match(dots.haifa.attrs.title, /רשת ההפצה לא הוגדרה/, 'the tooltip says why');
  assert.equal(dots.haifa.attrs['aria-label'], dots.haifa.attrs.title);
  assert.equal(dots[''].className, 'status ok', 'TA has the same dot -- green when every check is ok');
  assert.match(dots[''].attrs.title, /מחובר · כל השירותים פעילים/, 'what the bottom box said moves into the tooltip');
});

test('Nadav 18/10: a server that was not measured is never green; one that does not answer is red; TA uses the same function', async () => {
  const {t, dots, rows, ctx, rail} = setup({[TA]: {status: 200, body: [...OK, {id: 'udp_sender', label: 'משדר', state: 'bad', detail: 'לא נמצא'}]},
    [HAIFA]: {status: 502, body: {detail: 'חיפה לא ענה: timeout'}}});
  assert.equal(t.serverDotState(undefined).cls, 'unk', 'never measured: grey');
  assert.equal(t.serverDotState({checks: null, error: '403', down: false}).cls, 'unk', 'read failed: grey, "not read"');
  t.setNodes([{id: 'haifa', label: 'חיפה'}]);
  await t.refreshServerDots();
  assert.equal(dots.haifa.className, 'status err', 'the relay says it did not answer: red');
  assert.match(dots.haifa.attrs.title, /לא מחובר: חיפה לא ענה/);
  assert.equal(ctx.SECONDARY_STATUS.haifa.connected, false, 'the bell and the red row follow');
  assert.equal(rows.haifa.attrs['data-conn'], 'err');
  assert.equal(dots[''].className, 'status err', 'TA with a failing service: red');
  assert.match(dots[''].attrs.title, /1 שירותים בתקלה\nמשדר — לא נמצא/);
  assert.deepEqual(rail.at(-1)[0], 'err', 'the rail light is TA\'s dot');
});

test('Nadav 18/10: no new heavy polling -- a server measured within SERVER_DOT_TTL is not asked again; the shown server\'s loadHealth feeds its dot', async () => {
  const {t, calls} = setup({[TA]: {status: 200, body: OK}, [HAIFA]: {status: 200, body: OK}});
  t.setNodes([{id: 'haifa', label: 'חיפה'}, {id: 'off', label: 'מושבת', disabled_at: '2026-10-01'}]);
  await t.refreshServerDots();
  await t.refreshServerDots();
  assert.equal(calls.length, 2, 'twice in a row = one round; a disabled secondary is never asked');
  assert.match(source, /const SERVER_DOT_TTL = (\d+);/);
  const ttl = Number(source.match(/const SERVER_DOT_TTL = (\d+);/)[1]);
  assert.ok(ttl >= 30000 && ttl <= 60000, '30-60 seconds');
  const load = cut('async function loadHealth()', 'if (current === "health") await loadHealthServices()');
  assert.match(load, /noteServerHealth\(SERVER_CTX \|\| "", HEALTH, null\)/, 'what the page already loaded is the dot\'s data');
  assert.match(load, /noteServerHealth\(SERVER_CTX \|\| "", null, e\)/);
});

test('Nadav 18/10: the bottom box is gone from the tree; TA carries a dot in the same place and markup as a secondary', () => {
  assert.doesNotMatch(html, /tree-foot|treeFoot/, 'no bottom box');
  assert.match(html, /<strong class="server-name" id="primaryServerName"[^>]*>[^<]*<\/strong><span class="status unk" id="primaryServerStatus" title="מצב השירותים לא נבדק" aria-label="מצב השירותים לא נבדק"><i><\/i><\/span><\/div>/,
    'right after the name, grey until measured');
  assert.match(source, /<strong class="server-name">\$\{esc\(n\.label\)\}<\/strong><span class="status \$\{dot\.cls\}" data-secondary-status=/,
    'a secondary: the same span right after its name');
  assert.match(source, /: serverDotState\(SERVER_HEALTH\[n\.id\]\);/, 'drawn by the same function');
  assert.doesNotMatch(source, /secondaryDotClass|treeFootStatus/);
});
