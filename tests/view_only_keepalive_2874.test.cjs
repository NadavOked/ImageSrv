// #2874 (Nadav 23/11, mockup approved: "the session is not closed"): with an active round the view-only
// console is not disconnected after the idle minutes -- not in the browser, and not on the server, which
// revokes a cookie it has not seen for console_idle_seconds (+1 min, #2414) while a hidden tab polls nothing.
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const js = consoleJs();

function functionSource(name) {
  const lines = js.split('\n');
  const start = lines.findIndex((line) => new RegExp(`^(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `${name} exists in console.js`);
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === '}');
  return lines.slice(start, end + 1).join('\n');
}

// startIdleWatch with a clock we move by hand; the tick it registers is called directly.
function watch({idle = 600, sinceActivity, round, extra = {}}) {
  const calls = {api: [], post: [], viewOnly: [], login: 0};
  let tick = null, now = 1_000_000_000;
  const c = vm.createContext({
    ME: {idle_seconds: idle}, ROOM: null, idleTimer: null, idleWarned: false,
    setInterval: (fn) => { tick = fn; return 1; }, clearInterval() {}, noteActivity() {},
    document: {hidden: true}, toast() {}, $: () => ({}), showLogin: () => { calls.login++; },
    api: (p) => { calls.api.push(p); return Promise.resolve({machines: [], round: round ? {id: 1} : null}); },
    post: (p) => { calls.post.push(p); return Promise.resolve({}); },
    setViewOnly: (on) => { calls.viewOnly.push(on); c.VIEW_ONLY = on; },
    activeRoundNow: () => round, idleKeepAlive() {}, Date: {now: () => now},
    VIEW_ONLY: false, lastActivity: now - sinceActivity * 1000, ...extra,
  });
  vm.runInContext('let roundPingAt = 0;\n' + functionSource('roundKeepAlive') + '\n' + functionSource('startIdleWatch'), c);
  c.startIdleWatch();
  // startIdleWatch calls noteActivity (stubbed), so lastActivity stays what the test set.
  return {c, calls, tick: async (secs = 0) => { now += secs * 1000; await tick(); }};
}

test('#2874: in the last minute before the idle limit the page reads /room from the server, at most once a minute', async () => {
  const w = watch({sinceActivity: 560, round: true});
  await w.tick(); await w.tick(10); await w.tick(20);
  assert.deepEqual(w.calls.api, ['/room'], 'one GET /room, not one per second');
  await w.tick(40);
  assert.equal(w.calls.api.length, 2, 'a minute later, the next one');
});

test('#2874: well before the last minute there is no extra request', async () => {
  const w = watch({sinceActivity: 100, round: true});
  await w.tick(); await w.tick(30);
  assert.deepEqual(w.calls.api, []);
});

test('#2874: idle limit passed with an active round: view-only, no logout, and the pings go on (the session stays open on the server)', async () => {
  const w = watch({sinceActivity: 700, round: true});
  await w.tick(); await w.tick(61); await w.tick(61);
  assert.deepEqual(w.calls.post, [], 'no logout while the round runs');
  assert.deepEqual(w.calls.viewOnly, [true], 'view-only is set once');
  assert.equal(w.calls.api.length, 3, 'a ping every minute while view-only');
  assert.ok(w.calls.api.every((p) => p === '/room'));
});

test('#2874: idle limit passed without a round: the regular logout, and no ping that would keep the session', async () => {
  const w = watch({sinceActivity: 700, round: false});
  await w.tick();
  assert.deepEqual(w.calls.post, ['/logout']);
  assert.deepEqual(w.calls.api, []);
  assert.deepEqual(w.calls.viewOnly, []);
});

test('#2874: /room is the server\'s answer -- a round that ended there is what ends the view-only (ROOM.round is read)', async () => {
  const w = watch({sinceActivity: 560, round: false});
  await w.tick();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(w.c.ROOM, {machines: [], round: null}, 'ROOM is replaced by the server\'s answer');
});

test('#2874: a failed /room read changes nothing (the server sees silence, the old ROOM stays)', async () => {
  const w = watch({sinceActivity: 560, round: true, extra: {api: () => Promise.reject(new Error('down'))}});
  w.c.ROOM = {machines: [], round: {id: 7}};
  await w.tick();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(w.c.ROOM, {machines: [], round: {id: 7}});
});
