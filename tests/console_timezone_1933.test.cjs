// ‏#1933 (נדב 21/10 05:33): "עודכן היום ב-02:22" כשהשעה בישראל הייתה 05:22. ‏fmtClock/fmtWhen/fmtHour
// חתכו את מחרוזת ה-ISO (UTC), ‏isToday השווה ליום UTC, ו-clockNow/fmtEndsAt/vmuHHMM לקחו את שעון הדפדפן.
// עכשיו: אזור הזמן של השרת (‏/me.timezone, ‏#1447) — לא של הדפדפן ולא UTC כלשונו. אזור לא ידוע → UTC עם
// הסימון "UTC" (עיקרון 5: לא UTC בשקט).
//
// הדפדפן כאן בכוונה בניו יורק: מה שעובר רק כי שעון התחנה במקרה בישראל — נכשל.
process.env.TZ = 'America/New_York';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');
const SOURCES = {console: read('console.js'), update: read('update_screen.js'), vmu: read('vmupdate.js'),
                 branches: read('branches.js')};

// פונקציה שאינה קיימת (העוזרים החדשים, על הקוד הישן) אינה נטענת — כדי שהבקרה השלילית תיכשל
// על הערכים של fmtClock/isToday הישנים, לא על "לא נמצא". שהעוזרים קיימים — הטסטים עצמם מראים.
function functionSource(js, name) {
  const lines = js.split('\n');
  const start = lines.findIndex((line) => new RegExp(`^(async )?function ${name}\\(`).test(line));
  if (start < 0) return '';
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === '}');
  return lines.slice(start, end + 1).join('\n');
}

const CONSOLE_FNS = ['esc', 'displayZone', 'tsMs', 'zoneParts', 'zoneDay', 'fmtDate', 'fmtClock', 'isToday',
  'fmtWhen', 'fmtHour', 'fmtWhenShort', 'clockNow', 'fmtEndsAt', 'wallToUtc', 'logSince', 'logParams',
  'markStatusFresh', 'fmtUptime', 'homeUptime'];

function ctxWith(state) {
  const ctx = vm.createContext({ME: null, OVERVIEW: null, SERVER_CTX: null, TZ_FMT: null, URLSearchParams, ...state});
  vm.runInContext('var ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };'
    + 'var overviewError = "", overviewLastOk = "";', ctx);
  for (const name of CONSOLE_FNS) vm.runInContext(functionSource(SOURCES.console, name), ctx);
  vm.runInContext(functionSource(SOURCES.update, 'updHms'), ctx);
  for (const name of ['vmuHHMM', 'vmuDefaultName']) vm.runInContext(functionSource(SOURCES.vmu, name), ctx);
  return ctx;
}
const IL = {ME: {timezone: 'Asia/Jerusalem'}};
const run = (ctx, expr) => vm.runInContext(expr, ctx);
const NOW = Date.parse('2026-10-21T02:33:00Z');   // 05:33 בישראל — הרגע שבו נדב ראה את הבאג

test('the reported case: 02:22 UTC is 05:22 in Israel (summer, +03:00)', () => {
  const ctx = ctxWith(IL);
  assert.equal(run(ctx, 'fmtClock("2026-10-21T02:22:00+00:00")'), '05:22');
  // "היום 05:22" רק ב-21/10; בכל יום אחר "21/10/2026 05:22" — השעה היא מה שנבדק (הטסט רץ גם אחרי חצות)
  assert.match(run(ctx, 'fmtWhenShort("2026-10-21T02:22:00+00:00")'), /(^היום |^21\/10\/2026 )05:22$/);
  assert.equal(run(ctx, 'fmtWhen("2026-10-21T02:22:05+00:00")'), '2026-10-21 05:22:05');
  assert.equal(run(ctx, 'fmtHour("2026-10-21T02:22:05+00:00")'), '05:22:05');
});

test('winter time is +02:00 — the zone rule, not a fixed offset', () => {
  const ctx = ctxWith(IL);
  assert.equal(run(ctx, 'fmtClock("2026-12-01T10:00:00Z")'), '12:00');
  assert.equal(run(ctx, 'fmtClock("2026-12-01T10:00:00.123456+00:00")'), '12:00');   // isoformat עם מיקרו-שניות
});

test('the date follows the zone across midnight, and "today" is the server\'s day', () => {
  const ctx = ctxWith(IL);
  assert.equal(run(ctx, 'fmtDate("2026-10-20T22:30:00+00:00")'), '21/10/2026');
  assert.equal(run(ctx, `isToday("2026-10-20T22:30:00+00:00", ${NOW})`), true, '01:30 on 21/10 in Israel');
  assert.equal(run(ctx, `isToday("2026-10-20T20:30:00+00:00", ${NOW})`), false, '23:30 on 20/10 in Israel');
  const dec = Date.parse('2026-12-02T00:33:00Z');   // 02:33 בחורף — רחוק מהיום שבו הטסט נכתב
  assert.equal(run(ctx, `isToday("2026-12-01T22:30:00Z", ${dec})`), true);
  assert.equal(run(ctx, `isToday("2026-12-01T21:30:00Z", ${dec})`), false);
  assert.equal(run(ctx, 'fmtDate("2026-10-21")'), '21/10/2026', 'a bare date stays as it is');
});

test('offsets other than UTC and timestamps without an offset (the DB keeps UTC)', () => {
  const ctx = ctxWith(IL);
  assert.equal(run(ctx, 'fmtClock("2026-10-21T05:22:00+03:00")'), '05:22');
  assert.equal(run(ctx, 'fmtClock("2026-10-21 02:22:00")'), '05:22');
  assert.equal(run(ctx, 'fmtClock("")'), '—');
  assert.equal(run(ctx, 'fmtDate(null)'), '—');
  assert.equal(run(ctx, 'fmtWhen("")'), '—');
});

test('an unknown zone is UTC said out loud — never silently, never the browser\'s', () => {
  for (const state of [{ME: null}, {ME: {}}, {ME: {timezone: null}}, {ME: {timezone: 'Not/AZone'}}]) {
    const ctx = ctxWith(state);
    assert.equal(run(ctx, 'fmtClock("2026-10-21T02:22:00+00:00")'), '02:22 UTC', JSON.stringify(state));
    assert.equal(run(ctx, 'fmtWhen("2026-10-21T02:22:05+00:00")'), '2026-10-21 02:22:05 UTC');
    assert.equal(run(ctx, 'fmtHour("2026-10-21T02:22:05+00:00")'), '02:22:05 UTC');
  }
});

test('"now" clocks are the server\'s zone, not the browser\'s', () => {
  const ctx = ctxWith(IL);
  assert.equal(run(ctx, `clockNow(${Date.parse('2026-10-21T02:22:00Z')})`), '05:22');
  assert.equal(run(ctx, `fmtEndsAt(${Date.parse('2026-10-21T02:22:00Z')})`), '05:22');
  assert.equal(run(ctx, `updHms(${Date.parse('2026-10-21T02:22:05Z')})`), '05:22:05');
  assert.equal(run(ctx, 'vmuHHMM("2026-10-21T02:22:00Z")'), '05:22');
  assert.equal(run(ctx, 'vmuHHMM("")'), null);
  assert.equal(run(ctx, `vmuDefaultName(${Date.parse('2026-12-31T22:30:00Z')})`), 'Windows Updates 01-01-2027');
  run(ctx, 'markStatusFresh()');
  assert.match(run(ctx, 'overviewLastOk'), /^\d{2}:\d{2}:\d{2}$/, 'the server\'s HH:MM:SS, not toLocaleTimeString');
});

test('the log\'s "today" and a typed range are the server\'s wall clock, sent as UTC', () => {
  const ctx = ctxWith(IL);
  assert.equal(run(ctx, `logSince("today", ${Date.parse('2026-12-02T00:33:00Z')})`), '2026-12-01T22:00:00', 'midnight, winter');
  assert.equal(run(ctx, `logSince("today", ${NOW})`), '2026-10-20T21:00:00', 'midnight in Israel');
  assert.equal(run(ctx, 'wallToUtc("2026-12-01T00:00")'), '2026-11-30T22:00:00', 'winter: +02:00');
  run(ctx, 'var LOG = {q: "", event: "", user: "", range: "custom", since: "2026-10-21T05:00", until: "2026-10-21T05:59", limit: 200}');
  const p = new URLSearchParams(run(ctx, 'logParams().toString()'));
  assert.equal(p.get('from'), '2026-10-21T02:00:00');
  assert.equal(p.get('to'), '2026-10-21T02:59:59');
});

test('#1932: a secondary\'s uptime comes from its /overview, not "not checked"', () => {
  const ctx = ctxWith({ME: {uptime_seconds: null}, SERVER_CTX: 'haifa', OVERVIEW: {uptime_seconds: 7200}});
  assert.equal(run(ctx, 'homeUptime()'), 'פעיל 2 שעות 0 דקות');
  const unread = ctxWith({ME: {uptime_seconds: null}, SERVER_CTX: 'haifa', OVERVIEW: null});
  assert.match(run(unread, 'homeUptime()'), /זמן פעילות לא נבדק/);
});

test('no operator-facing file reads the browser\'s clock zone or slices UTC out of an ISO stamp', () => {
  const bad = /toLocaleTimeString|toLocaleDateString|\.getHours\(|\.getMinutes\(|\.getDate\(\)|\.getMonth\(\)|toISOString\(\)\.slice\(11|\.replace\("T", " "\)/;
  for (const [name, js] of Object.entries(SOURCES)) {
    const hits = js.split('\n').map((l, i) => [i + 1, l]).filter(([, l]) => bad.test(l));
    assert.deepEqual(hits, [], `${name}: ${hits.map(([n, l]) => `${n}: ${l.trim().slice(0, 100)}`).join('\n')}`);
  }
});
