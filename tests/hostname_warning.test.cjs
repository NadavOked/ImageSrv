// #856: a station whose restore finished but whose computer name could not
// be written reports `done` with an `error` on the target ("שם המחשב לא
// נכתב: <reason>"). Before this fix memberRow painted any `error` red
// (.err) under a green "done" card -- the same look as a failed drive --
// and the machine drawer showed nothing at all. Same pattern as #927
// (captureWarningHtml): done + error = orange `.notice.warn`, never red.
// Runs the shipped console.js in a vm, not a copy of the functions.
const {consoleFiles} = require('./console_src.cjs');
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

const MAC = 'b4:2e:99:07:1a:c4';
const WARNING = 'שם המחשב לא נכתב: registry edit <b>failed</b>';
const member = (over) => ({mac: MAC, hostname: 'LAB1-05', state: 'done', done: true,
  bytes_written: 1, bytes_total: 1, disks: [], drivers: null, ...over});

test('no_linux_partition reaches the operator as the agent Hebrew error', () => {
  const {spawnSync} = require('node:child_process');
  const os = require('node:os');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'imagesrv-2586-text-'));
  try {
    const env = {...process.env, RUN_DIR: tmp.replaceAll('\\', '/')};
    const got = spawnSync('sh', ['-s'], {cwd: path.join(__dirname, '..'), env,
      encoding: 'utf8', input: `
PATH=/usr/bin:/bin:$PATH
LOG_FILE="$RUN_DIR/log"
. agent/lib/postdeploy/hostname.sh
. agent/lib/postdeploy/hostname_linux.sh
. agent/lib/postdeploy/postdeploy.sh   # plan_bitlocker (#1840), as imagesrv-agent sources it
log() { printf '%s\\n' "$*" >> "$LOG_FILE"; }
manifest_plan() { echo '1|guid|data|ext4|2048|4096|p1|sha|false'; }
_linux_marked() { return 0; }
assert_target() { return 0; }
compose_hostname() { echo LAB1-05; }
json_get() {
    case "$2" in
        .session.prefix) echo LAB1;; .group.suffix) echo 05;;
        .error) sed -n 's/.*"error":"\\([^"]*\\)".*/\\1/p' "$1";;
        *) return 1;;
    esac
}
target_set() { printf '%s\\n' "$3"; }
mount() { echo unexpected-mount >&2; return 1; }
name_this_machine sda sess1
`});
    assert.ifError(got.error);
    assert.equal(got.status, 0, got.stderr);
    assert.equal(got.stderr, '');
    const result = JSON.parse(fs.readFileSync(path.join(tmp, 'hostname.json'), 'utf8'));
    assert.equal(result.code, 'no_linux_partition');
    const warning = 'שם המחשב לא נכתב: אין מחיצת לינוקס במניפסט';
    assert.equal(got.stdout.trim(), warning);
    const run = setup();
    const html = run(`memberRow(${JSON.stringify(member({error: warning}))}, {})`);
    assert.ok(html.includes(warning));
    assert.match(html, /notice warn/);
  } finally {
    fs.rmSync(tmp, {recursive: true, force: true});
  }
});

test('a done member with a hostname warning is orange, not red, and says why', () => {
  const run = setup();
  const html = run(`memberRow(${JSON.stringify(member({error: WARNING}))}, {})`);
  assert.match(html, /class="member done"/, 'the restore itself completed');
  assert.match(html, /notice warn/, 'done + error = warning');
  assert.match(html, /שם המחשב לא נכתב: registry edit &lt;b&gt;failed&lt;\/b&gt;/, 'escaped reason shown');
  assert.doesNotMatch(html, /class="err"/, 'not the red failed-drive look');
});

test('a failed member keeps the red error -- the fix does not soften failures', () => {
  const run = setup();
  const html = run(`memberRow(${JSON.stringify(member({state: 'failed', done: false, error: 'sda: fsync errno=5'}))}, {})`);
  assert.match(html, /class="err">sda: fsync errno=5/);
  assert.doesNotMatch(html, /notice warn/);
});

test('a clean done member shows no warning box at all', () => {
  const run = setup();
  const html = run(`memberRow(${JSON.stringify(member({}))}, {})`);
  assert.doesNotMatch(html, /notice warn/);
  assert.doesNotMatch(html, /class="err"/);
});

test('the machine drawer shows the last restore warning of this MAC', () => {
  const run = setup();
  run(`OVERVIEW = {session: {members: [${JSON.stringify(member({error: WARNING}))}]}}`);
  const html = run(`machineRestoreWarningHtml(${JSON.stringify(MAC)})`);
  assert.match(html, /notice warn/);
  assert.match(html, /שם המחשב לא נכתב/);
  assert.equal(run(`machineRestoreWarningHtml("00:00:00:00:00:01")`), '', 'another machine: nothing');
  run(`OVERVIEW = {session: {members: [${JSON.stringify(member({state: 'failed', done: false, error: 'x'}))}]}}`);
  assert.equal(run(`machineRestoreWarningHtml(${JSON.stringify(MAC)})`), '', 'a failure is not a warning');
  run('OVERVIEW = {session: null}');
  assert.equal(run(`machineRestoreWarningHtml(${JSON.stringify(MAC)})`), '', 'no session: nothing');
  assert.match(run('machineDrawerHtml.toString()'), /machineRestoreWarningHtml/, 'wired into the drawer (#954 wave 3)');
});

test('index.html was bumped so the browser does not run the old console.js', () => {
  const page = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const versions = new Set([...page.matchAll(/\?v=([0-9.]+)/g)].map((m) => m[1]));
  assert.equal(versions.size, 1, [...versions].join(','));
  // numeric, not lexical: '10.0' >= '6.6' is false as strings (caught at ?v=10.0, #1128)
  assert.ok(parseFloat([...versions][0]) >= 6.6, `?v=${[...versions][0]} — #856 ships as 6.6`);
});
