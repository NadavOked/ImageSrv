// #1334: the VM-update console UI (vmupdate.js) -- the global "VM רץ" strip, the
// view-only/take-control strip, the "גם X צופה ב-VM" presence strip, the mandatory
// save decision, the controller's approve/deny dialog, the image ⋯ item, and the
// paste-as-typing guard. Runs the shipped console.js + vmupdate.js, not copies.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');

function setup({user = 'nadav', role = 'admin'} = {}) {
  const els = new Map();
  const node = (id) => {
    if (!els.has(id)) els.set(id, {id, value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
      classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }}, addEventListener() {}, removeEventListener() {},
      querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});
    return els.get(id);
  };
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, String, Array, Object, Error,
    encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelector: (s) => node(s), querySelectorAll: () => [], getElementById: node,
      createElement: () => node('tmp' + Math.random()), documentElement: {setAttribute() {}},
      addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false})},
    localStorage: {getItem: () => null, setItem() {}}, sessionStorage: {getItem: () => null, setItem() {}},
    CSS: {escape: (x) => x}, location: {protocol: 'https:', host: 'srv:8081'},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(read('progress.js'), ctx);
  vm.runInContext(read('console.js'), ctx);
  vm.runInContext(read('vmupdate.js'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  run(`ME={timezone:"UTC",username:${JSON.stringify(user)},role:${JSON.stringify(role)},capabilities:{}};`);
  return {run, node};
}

const BASE = {state: 'running', image_id: 'img_7f3a91', image_name: 'Office 2024', image_version: 3, lineage: 'img_7f3a91',
  controller: 'nadav', controller_since: '2026-10-13T09:00:00+03:00', you_control: true, runtime_seconds: 1394,
  egress_interface: 'eno1', resources: {cpus: 4, memory_mib: 16384, accel: 'kvm'}, viewers: [], control_request: null};
const st = (o) => JSON.stringify({...BASE, ...o});

test('the global strip shows an active VM on every page except the VM page', () => {
  const {run} = setup();
  run('current = "home"');
  const html = run(`vmuGlobalHtml(${st({})})`);
  assert.match(html, /VM רץ:/);
  assert.match(html, /Office 2024/);
  assert.match(html, /vmuOpen\(\)/);
  assert.equal(run(`vmuGlobalHtml(${st({state: 'idle'})})`), '');
  assert.equal(run(`vmuGlobalHtml(${st({state: 'saved'})})`), '');
  assert.match(run(`vmuGlobalHtml(${st({state: 'stopped', expires_in_seconds: 3 * 86400})})`), /ממתין להחלטה.*בעוד 3 ימים/);
  run('current = "vmupdate"');
  assert.equal(run(`vmuGlobalHtml(${st({})})`), '');
});

test('a viewer who does not control sees a view-only strip, disabled input tools, and no power buttons', () => {
  const {run} = setup({user: 'dana'});
  const s = st({you_control: false});
  const strip = run(`vmuControlHtml(${s})`);
  assert.match(strip, /<b>nadav<\/b> עובד ב-VM מאז \d\d:\d\d — אתם בצפייה בלבד/);
  assert.match(strip, /vmuRequestControl\(\)/);
  const tool = run(`vmuToolHtml(${s})`);
  assert.match(tool, /disabled onclick="vmuCtrlAltDel\(\)"/);
  assert.match(tool, /disabled onclick="vmuPasteDialog\(\)"/);
  assert.doesNotMatch(tool, /vmuShutdown|vmuForceDialog/);
  // the controller has them, and no view-only strip
  assert.equal(run(`vmuControlHtml(${st({})})`), '');
  assert.match(run(`vmuToolHtml(${st({})})`), /vmuShutdown\(\)[\s\S]*vmuForceDialog\(\)/);
});

test('a pending request shows a countdown to the requester; a denial is said out loud', () => {
  const {run} = setup({user: 'dana'});
  const pending = run(`vmuControlHtml(${st({you_control: false, control_request: {by: 'dana', seconds_left: 42}})})`);
  assert.match(pending, /ממתין לאישור של nadav · 42 שנ'/);
  assert.doesNotMatch(pending, /vmuRequestControl/);
  const denied = run(`vmuControlHtml(${st({you_control: false, control_last: {by: 'dana', outcome: 'denied'}})})`);
  assert.match(denied, /הבקשה שלך נדחתה/);
});

test('the presence strip names the other viewers, address and time, with +N and a list', () => {
  const {run} = setup();
  const viewers = [
    {id: 'a', user: 'nadav', address: '10.20.0.15', since: '2026-10-13T09:00:00+03:00', controller: true, you: true},
    {id: 'b', user: 'dana', address: '10.20.0.31', since: '2026-10-13T09:20:00+03:00', controller: false, you: false},
    {id: 'c', user: 'yossi', address: '10.20.0.44', since: '2026-10-13T09:25:00+03:00', controller: false, you: false},
  ];
  const html = run(`vmuPresenceHtml(${st({viewers})})`);
  assert.match(html, /גם <b>dana<\/b> צופה ב-VM · מ-<span class="mono">10\.20\.0\.31<\/span> · מאז \d\d:\d\d/);
  assert.match(html, />\+1</);
  run('VMU.prs.pop = true');
  const pop = run(`vmuPresenceHtml(${st({viewers})})`);
  assert.match(pop, /צופים ב-VM · 3/);
  assert.match(pop, /nadav<\/span><span class="prs-you">אתה<\/span><span class="prs-you">שולט<\/span>/);
  // alone: no strip
  assert.equal(run(`vmuPresenceHtml(${st({viewers: [viewers[0]]})})`), '');
});

test('names and addresses are escaped', () => {
  const {run} = setup();
  const html = run(`vmuPresenceHtml(${st({viewers: [{id: 'x', user: '<img src=x onerror=alert(1)>', address: '"><b>', since: '', controller: false, you: false}]})})`);
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;img src=x/);
});

test('when the VM stops, the controller gets the save decision once -- locked, no X', () => {
  const {run, node} = setup();
  run('current = "vmupdate"');
  run('openModalFrame = () => {}');
  run(`VMU.st = ${st({state: 'stopped'})}; vmuDialogs()`);
  assert.equal(run('VMU.dlg'), 'save');
  assert.equal(node('modal').dataset.locked, '1');
  assert.doesNotMatch(node('sheet').innerHTML, /vmuCloseWin\(\)"[^>]*aria-label="סגור"|class="n-x"/);
  assert.match(node('sheet').innerHTML, /שמור כ-v4/);
  assert.equal(run('modalLocked()'), true);
  // closed by a decision, it does not pop again for the same stop
  run('vmuCloseWin(); vmuDialogs()');
  assert.equal(run('VMU.dlg'), '');
  // a viewer never gets it
  const viewer = setup({user: 'dana'});
  viewer.run('current = "vmupdate"; openModalFrame = () => {}');
  viewer.run(`VMU.st = ${st({state: 'stopped', you_control: false})}; vmuDialogs()`);
  assert.equal(viewer.run('VMU.dlg'), '');
});

test('the controller is asked to approve a take-control request, and the dialog closes when it is gone', () => {
  const {run, node} = setup();
  run('openModalFrame = () => {}; closeModal = () => {}');
  run(`VMU.st = ${st({control_request: {by: 'dana', seconds_left: 51}})}; vmuDialogs()`);
  assert.equal(run('VMU.dlg'), 'control');
  assert.match(node('sheet').innerHTML, /dana מבקש שליטה ב-VM/);
  assert.match(node('sheet').innerHTML, /vmuAnswer\(true\)[\s\S]*|vmuAnswer\(false\)/);
  run(`VMU.st = ${st({control_request: null})}; vmuDialogs()`);
  assert.equal(run('VMU.dlg'), '');
});

test('the image ⋯ item: Windows opens the start window; Linux and a drifted image are disabled with the reason', () => {
  const {run} = setup();
  const win = run(`vmuImageMenuItem({id: "img_7f3a91", os: "windows", last_scrub: null})`);
  assert.equal(win[0], 'עדכן ב-VM…');
  assert.match(win[1], /vmuStartDialog\('img_7f3a91'\)/);
  const linux = run(`vmuImageMenuItem({id: "img_2c8e04", os: "linux"})`);
  assert.equal(linux[2], 'disabled');
  assert.match(linux[0], /רק לאימג' Windows/);
  const drift = run(`vmuImageMenuItem({id: "img_2", os: "windows", last_scrub: {state: "drift"}})`);
  assert.equal(drift[2], 'disabled');
  assert.match(run(`rowMenuHtml([["x (רק לאימג' Windows)", "", "disabled"]])`), /aria-disabled="true" disabled/);
  // deploy users never see it
  assert.equal(setup({role: 'deploy'}).run(`vmuImageMenuItem({id: "a", os: "windows"})`), null);
});

test('paste is typing on a US layout: ASCII and Enter/Tab only', () => {
  const {run} = setup();
  assert.deepEqual(Array.from(run('vmuKeysyms("ab C\\n\\t1")')), [97, 98, 32, 67, 0xff0d, 0xff09, 49]);
  assert.equal(run('vmuKeysyms("שלום")'), null);
  assert.throws(() => run('vmuType("שלום")'), /רק תווים באנגלית/);
});

test('viewer disconnect stops reconnecting when the live status says auth was lost', async () => {
  const {run} = setup();
  run('current = "vmupdate"; let reconnects = 0; setTimeout = () => { reconnects++ };'
    + ' apiFetch = async () => ({status: 401, ok: false}); showLogin = () => {}; toast = (m) => { VMU.err = m }');
  await run('vmuReconnectAfterDisconnect()');
  assert.equal(run('reconnects'), 0);
  assert.equal(run('VMU.rfbState'), 'auth-closed');
  assert.match(run('VMU.err'), /להתחבר מחדש/);
});

test('viewer disconnect reconnects only after a positive running status', async () => {
  const {run} = setup();
  run('current = "vmupdate"; let reconnects = 0; setTimeout = () => { reconnects++ };'
    + ' apiFetch = async () => ({status: 200, ok: true, json: async () => ({state: "running"})})');
  await run('vmuReconnectAfterDisconnect()');
  assert.equal(run('reconnects'), 1);
});

test('a stage card of the same step is updated in place, not rebuilt (the flicker fix)', () => {
  const {run} = setup();
  const prep = (done) => run(`vmuPrepCard(${st({state: 'preparing', progress: {stage: 'disk', done_bytes: done, total_bytes: 100e9, partitions: 3}})})`);
  assert.match(prep(10e9), /data-card="prep"/);
  assert.match(prep(10e9), /data-k="prepbar" style="width:10%"/);
  assert.match(prep(55e9), /data-k="prepbar" style="width:55%"/);
});

// #1391 (Nadav 13/10): "צמצם" while the disk is prepared -- the work goes on in the
// background, its progress is a row in the task dock and in the global strip, the row
// opens the VM page, and the operator is told when the VM is up.
const PREP = {state: 'preparing', created_at: '2026-10-13T09:00:00+03:00', started_by: 'nadav',
  progress: {stage: 'disk', done_bytes: 27.8e9, total_bytes: 44.9e9, partitions: 3}};

test('the prep card has a minimize button -- for the controller and for a viewer', () => {
  const {run} = setup();
  assert.match(run(`vmuPrepCard(${st(PREP)})`), /data-testid="vmu-minimize" onclick="vmuMinimize\(\)">צמצם</);
  assert.match(run(`vmuPrepCard(${st({...PREP, you_control: false})})`), /onclick="vmuMinimize\(\)"/);
});

test('minimize goes back to where the operator came from and opens the task dock', () => {
  const {run} = setup();
  run('globalThis.WENT = []; selectPageById = (id) => WENT.push(id); selectPage = () => {}; globalThis.TOGGLED = 0; toggleTaskPanel = () => { TOGGLED++; }');
  run('current = "machines"; vmuOpen(); current = "vmupdate"; vmuMinimize()');
  assert.deepEqual(Array.from(run('WENT')), ['machines']);
  assert.equal(run('TOGGLED'), 1);
  // opened straight on the VM page (no page before it): the images page
  run('WENT.length = 0; VMU.back = undefined; vmuMinimize()');
  assert.deepEqual(Array.from(run('WENT')), ['images']);
});

test('the dock shows the preparation as a task: target, start, state with percent, initiator; the row opens the VM', () => {
  const {run} = setup();
  run(`VMU.st = ${st(PREP)}`);
  const vm = Array.from(run('activityRows().rows')).find((r) => r.open === 'vmuOpen()');
  assert.ok(vm, 'no VM row in the task dock');
  // the dock is text (esc), not <bdi>: the name and the sizes ride in Unicode isolates
  assert.equal(vm.name, 'עדכון ב-VM: ⁨Office 2024 v3⁩');
  assert.equal(vm.target, 'דיסק זמני · ⁦27.8 GB / 44.9 GB⁩');
  assert.equal(vm.label, 'מכין דיסק · 62%');
  assert.equal(vm.pct, 62);
  assert.equal(vm.by, 'nadav');
  assert.equal(vm.start, PREP.created_at);
  const html = run(`activityRowHtml(${JSON.stringify(vm)})`);
  assert.match(html, /<tr class="go" tabindex="0" onclick="vmuOpen\(\)"/);
  assert.match(html, /aria-valuenow="62"/);
  // running, and gone when the VM is closed
  run(`VMU.st = ${st({})}`);
  assert.match(Array.from(run('activityRows().rows')).find((r) => r.open).label, /^רץ · 23:14$/);
  run(`VMU.st = ${st({state: 'idle'})}`);
  assert.equal(Array.from(run('activityRows().rows')).filter((r) => r.open).length, 0);
});

test('the global strip carries the preparation progress', () => {
  const {run} = setup();
  run('current = "images"');
  const html = run(`vmuGlobalHtml(${st(PREP)})`);
  assert.match(html, /מכין דיסק ל-VM:<\/b> Office 2024 <span class="m">62% · <bdi dir="ltr">27.8 GB \/ 44.9 GB<\/bdi><\/span>/);
  // before the server knows the total: no number, not 0%
  assert.doesNotMatch(run(`vmuGlobalHtml(${st({...PREP, progress: {stage: 'disk', done_bytes: 0, total_bytes: null}})})`), /%/);
});

test('when the prepared VM comes up on another page, the operator is told -- once, and not on the VM page', () => {
  const {run} = setup();
  run('globalThis.TOASTS = []; toast = (m, ms, kind) => TOASTS.push([m, kind])');
  run(`current = "images"; vmuReadyNotice("preparing", ${st({})})`);
  assert.equal(run('TOASTS.length'), 1);
  assert.match(run('TOASTS[0][0]'), /ה-VM מוכן — Office 2024 v3 עלה/);
  assert.equal(run('TOASTS[0][1]'), 'ok');
  run(`vmuReadyNotice("running", ${st({})})`);              // already running: nothing new
  run(`vmuReadyNotice(undefined, ${st({})})`);              // first sample: not "now ready"
  run(`current = "vmupdate"; vmuReadyNotice("preparing", ${st({})})`);   // the screen is right there
  assert.equal(run('TOASTS.length'), 1);
});

test('the status poll refreshes the dock row and announces the VM that came up', async () => {
  const {run} = setup();
  run('globalThis.TOASTS = []; toast = (m) => TOASTS.push(m); globalThis.PAINTED = 0; renderActivity = () => { PAINTED++; }; vmuGlobal = () => {}');
  run(`current = "images"; VMU.st = ${st(PREP)}; VMU.last = 0; vmuCall = async () => (${st({})})`);
  await run('vmuTick(true)');
  assert.equal(run('PAINTED'), 1);
  assert.equal(run('TOASTS.length'), 1);
});

// #1468: the VM's own network identity -- the chip on the VM screen, the named
// network-failure card, and the "כרטיס לעדכוני VM" card on the NICs tab.
test('the VM screen shows the address the VM got, or how long it still waits for one', () => {
  const {run} = setup();
  const net = (o) => ({interface: 'eno2', mac: '0a:1b:2c:3d:4e:5f', address: null, server: null, at: null, seconds_left: null, ...o});
  const leased = run(`vmuToolHtml(${st({network: net({state: 'leased', address: '10.20.30.40'})})})`);
  assert.match(leased, /כתובת <b class="mono">10\.20\.30\.40<\/b>/);
  assert.match(leased, /eno2/);
  assert.doesNotMatch(leased, /כרטיס הניהול/);           // the slirp-era chip is gone
  const waiting = run(`vmuToolHtml(${st({network: net({state: 'waiting', seconds_left: 75})})})`);
  assert.match(waiting, /ממתין לכתובת מה-DHCP/);
  assert.match(waiting, /עוד <b class="mono">1:15<\/b>/);
});

test('no lease is a named network card with the hint and a way to the NIC settings', () => {
  const {run} = setup();
  const msg = 'ה-VM ביקש כתובת ב-DHCP (3 בקשות) ולא נענה. השרת רץ ב-ESXi: Forged transmits = Accept';
  const html = run(`vmuFailCard(${st({state: 'failed', disk_ready: true, booted_at: '2026-10-16T10:00:00+03:00',
    error: {reason: 'no_lease', message: msg}})})`);
  assert.match(html, /ה-VM לא קיבל כתובת ברשת/);
  assert.match(html, /Forged transmits/);
  assert.match(html, /openNetwork\(1\)/);
  assert.match(html, /vmuBoot\(\)/);
  assert.doesNotMatch(html, /נעצר בלי כיבוי/);
});

test('the NICs tab card shows the MAC with the reservation text, the lease, and a refusal by name', () => {
  const {run} = setup();
  run(`VMNET = {interface: 'eno2', mac: '0a:1b:2c:3d:4e:5f', deploy_interface: 'eno3', platform: 'microsoft',
    hint: 'Enable MAC address spoofing', problem: null, lease_timeout_seconds: 300,
    lease: {address: '10.20.30.40', server: '10.20.30.1', at: '2026-10-16T10:00:00+03:00', mac: '0a:1b:2c:3d:4e:5f'}}`);
  const html = run('vmnetCardHtml()');
  assert.match(html, /כרטיס לעדכוני VM/);
  assert.match(html, /0a:1b:2c:3d:4e:5f/);
  assert.match(html, /כתובת שמורה/);
  assert.match(html, /10\.20\.30\.40/);
  assert.match(html, /MAC address spoofing/);
  run(`VMNET = {...VMNET, interface: null, lease: null}`);
  assert.match(run('vmnetCardHtml()'), /לא נבחר — ה-VM לא יודלק/);
  run(`VMNET = {...VMNET, interface: 'eno3', problem: {code: 'vm_nic_is_deploy', error: 'eno3 הוא כרטיס ההפצה'}}`);
  assert.match(run('vmnetCardHtml()'), /eno3 הוא כרטיס ההפצה/);
  run(`VMNET = null; VMNET_ERR = 'שגיאה 500'`);
  assert.match(run('vmnetCardHtml()'), /לא נקרא: שגיאה 500/);   // unread is said, not shown as "no card"
});

test('#1535: the start dialog titles each source version with its plain text (stripTags, not the regex)', async () => {
  const {run} = setup();
  run(`api = async () => ({latest_id: 'img_b', versions: [
      {id: 'img_b', version: 2, name: 'Office', origin: 'vm', description: '<b>אימג׳ים</b> & עדכון', created_by: 'nadav',
       created: '2026-10-13T09:00:00+03:00', total_compressed_bytes: 2e9, is_default: true},
      {id: 'img_a', version: 1, name: 'Office', origin: 'capture', created_by: 'dana', created: '2026-10-01T09:00:00+03:00'}]});
    vmuCall = async () => ({ok: true, memory_mib: 16384, cpus: 4, disk_bytes: 1e11});
    vmuWin = (o) => { globalThis.VMU_TEST_BODY = o.body; };`);
  await run(`vmuStartDialog('img_b')`);
  const titles = [...run('VMU_TEST_BODY').matchAll(/class="vmu-vopt" title="([^"]*)"/g)].map((m) => m[1]);
  assert.equal(titles.length, 2);
  for (const t of titles) assert.ok(!t.includes('<') && !t.includes('>'), t);
  assert.match(titles[0], /אימג׳ים.* · עודכן ב-VM · nadav/);
  assert.match(titles[1], /^גרסה ראשונה · נקלט ממחשב בנייה · dana/);
});
