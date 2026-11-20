// #3457 (K13) -- שורת "שירותי השרת שהסבב צריך" בבריאות: כפתור "להדליק" רק למה שהשרת הציע (`actions`), רק למנהל,
// ולא בשרת משנה; והלחיצה שולחת ל-endpoint של המוח ומודיעה "הודלק" רק כשהשרת אימת מהסוקט (`ok`).
const {consoleJs} = require('./console_src.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
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
function ctx({admin = true, serverCtx = null, post = async () => ({ok: true})} = {}) {
  const posted = [], toasts = [], loads = [];
  const c = vm.createContext({
    posted, toasts, loads, SERVER_CTX: serverCtx, HEALTH_GOTO: {}, isAdmin: () => admin,
    findMachine: () => null, encodeId: (v) => encodeURIComponent(v).replaceAll("'", '%27'),
    esc: (s) => String(s), healthStatusClass: () => 'warn', healthStatusLabel: () => 'אזהרה', healthDetailHtml: (r) => r.detail,
    UI: {acts: (list) => JSON.stringify(list), pill: (cls, label) => `${cls}:${label}`},
    post: async (url, body) => { posted.push([url, body]); return post(url, body); },
    toast: (...args) => toasts.push(args), loadHealth: async () => { loads.push(1); },
  });
  vm.runInContext(functionSource('healthRow'), c);
  vm.runInContext(functionSource('brainServiceStart'), c);
  return c;
}
const ROW = {id: 'brain_services', label: 'שירותי השרת שהסבב צריך', state: 'warn', detail: 'TFTP כבוי, הסבב ייכשל',
  actions: [{id: 'tftp', label_he: 'להדליק TFTP', url: '/api/console/brain/services/tftp/start'}]};

test('#3457: an admin sees the button the server offered, wired to that service only', () => {
  const act = ctx().healthRow(ROW)[3];
  assert.deepEqual(JSON.parse(act), [['להדליק TFTP', "brainServiceStart('tftp')"]]);
});

test('#3457: no button for a deploy user, on a secondary server, or when the server offered none (DHCP never has one)', () => {
  assert.equal(ctx({admin: false}).healthRow(ROW)[3], '');
  assert.equal(ctx({serverCtx: 'srv_2'}).healthRow(ROW)[3], '');
  assert.equal(ctx().healthRow({...ROW, actions: undefined, detail: 'DHCP כבוי, הסבב ייכשל'})[3], '');
  assert.equal(ctx().healthRow({...ROW, actions: []})[3], '');
});

test('#3457: a click posts to the brain endpoint, says "הודלק" only when the server verified it, and reloads the health', async () => {
  const c = ctx();
  await c.brainServiceStart('tftp');
  assert.equal(c.posted.length, 1);
  assert.equal(c.posted[0][0], '/brain/services/tftp/start');
  assert.equal(c.toasts[0][0], 'הודלק');
  assert.equal(c.loads.length, 1);
});

test('#3457: a start that did not take is reported by its reason, in the error style -- never as started', async () => {
  const c = ctx({post: async () => ({ok: false, verified: false, detail: 'dnsmasq לא הגיב ל-restart'})});
  await c.brainServiceStart('tftp');
  assert.match(c.toasts[0][0], /^לא הודלק: dnsmasq לא הגיב/);
  assert.equal(c.toasts[0][2], 'err');
  const none = ctx({post: async () => ({ok: false, verified: false, detail: ''})});
  await none.brainServiceStart('tftp');
  assert.match(none.toasts[0][0], /הסוקט עדיין לא מאזין/);
});

test('#3457: a refusal from the server (409) is shown, and the health is reloaded anyway', async () => {
  const c = ctx({post: async () => { throw new Error('TFTP: כבר פעיל'); }});
  await c.brainServiceStart('tftp');
  assert.match(c.toasts[0][0], /^לא הודלק: TFTP: כבר פעיל/);
  assert.equal(c.loads.length, 1);
});

test('#3457: a button that restarts dnsmasq is shown disabled with the reason while a round is live; the others stay buttons', () => {
  const row = {...ROW, actions: [{id: 'tftp', label_he: 'להדליק TFTP', disabled: true, reason_he: 'יש סבב פעיל'},
                                {id: 'http_boot', label_he: 'להדליק HTTP'}]};
  const act = ctx().healthRow(row)[3];
  assert.match(act, /להדליק TFTP — יש סבב פעיל/);
  assert.doesNotMatch(act, /brainServiceStart\('tftp'\)/, 'no handler for the disabled one');
  assert.match(act, /brainServiceStart\('http_boot'\)/);
  const only = ctx().healthRow({...ROW, actions: [row.actions[0]]})[3];
  assert.match(only, /יש סבב פעיל/); assert.doesNotMatch(only, /brainServiceStart/);
});
