// #153 — תעודת TLS ושם השרת בדף ההגדרות. הפונקציות המשוגרות עצמן רצות ב-vm (כמו couldnt_read_negative):
// "לא נקרא" חייב להיראות שונה מ"אין TLS", תוקף שפג מסומן, והשדות של חומר אמון/שם מארח הם LTR.
// סידור 17/10 (נדב): התעודה היא שורה אחת בקבוצת "אבטחה" ("תעודה עצמית · בתוקף עד …" + "פרטים…"),
// והפרטים הטכניים — בחלון. שם השרת = שם המארח: חלון אחד ("שינוי שם השרת"), ‏POST /hostname.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const js = fs.readFileSync(path.join(root, 'console.js'), 'utf8');

function functionSource(name) {
  const lines = js.split('\n');
  const start = lines.findIndex(line => new RegExp(`^(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `${name} exists in console.js`);
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === '}');
  return lines.slice(start, end + 1).join('\n');
}

function ctxWith(state) {
  const UI = {
    card: ({title, body}) => `<card ${title}>${body}</card>`,
    note: (cls, html) => `<note ${cls}>${html}</note>`,
    pill: (cls, text) => `<pill ${cls}>${text}</pill>`,
  };
  const sheets = [];
  const ctx = vm.createContext({UI, Date, Math, sheets, sheet: (o) => sheets.push(o), ME: {timezone: 'UTC'}, TZ_FMT: null, ...state});
  for (const name of ['esc', 'displayZone', 'tsMs', 'zoneParts', 'fmtDate', 'tlsModeHe', 'tlsExpiryHtml', 'tlsRowHtml', 'tlsDetailsSheet', 'tlsReissueSheet', 'settingUnreadHtml', 'isRawTechnical', 'settingsKv', 'ghostBtn']) {
    vm.runInContext(functionSource(name), ctx);
  }
  vm.runInContext('const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };', ctx);
  return ctx;
}

const DAY = 86400000;
const cert = (overrides = {}) => ({
  subject: 'imagesrv.college.ac.il', issuer: 'College CA', self_signed: false,
  sans: ['imagesrv.college.ac.il'], not_after: new Date(Date.now() + 400 * DAY).toISOString(),
  fingerprint_sha256: Array(32).fill('AB').join(':'), ...overrides,
});
const tls = (extra = {}) => ({TLS_STATUS: {enabled: true, on_disk: cert(), pending_restart: false, covers_hostname: true, hostname: 'imagesrv', ...extra}});

test('unread /tls differs from a console that runs without TLS', () => {
  const unread = ctxWith({TLS_STATUS: null, tlsError: 'שגיאה 500'}).tlsRowHtml();
  const off = ctxWith({TLS_STATUS: {enabled: false}, tlsError: ''}).tlsRowHtml();
  assert.match(unread, /לא נקרא: שגיאה 500/);
  assert.match(off, /בלי TLS/);
  assert.notEqual(unread, off);
  assert.doesNotMatch(off, /tlsDetailsSheet|tlsUploadSheet/, 'no details/upload when there is no TLS listener');
});

test('the row is one line — kind and expiry — and "פרטים…" opens the details with the issuer and an upload button', () => {
  const ctx = ctxWith(tls());
  const row = ctx.tlsRowHtml();
  assert.match(row, /<span class="sv">תעודה חתומה <span class="muted">· בתוקף עד<\/span> <span dir="ltr">\d\d\/\d\d\/\d{4}<\/span><\/span> <button class="btn sm flat" type="button" onclick="tlsDetailsSheet\(\)">פרטים…<\/button>/);
  assert.doesNotMatch(row, /SHA-256|SAN/, 'the technical rows are one level deeper');
  ctx.tlsDetailsSheet();
  const s = ctx.sheets.at(-1);
  assert.match(s.note, /<dt>בתוקף עד<\/dt><dd><span dir="ltr">\d\d\/\d\d\/\d{4}<\/span><\/dd>/);
  assert.match(s.note, /חתומה בידי College CA/);
  assert.match(s.note, /<dt>SHA-256<\/dt>/);
  assert.equal(s.submitLabel, 'העלאת תעודה…');
  assert.doesNotMatch(s.note, /<note/);
});

test('expired and soon-to-expire certificates are flagged in the row itself', () => {
  const expired = ctxWith(tls({on_disk: cert({not_after: new Date(Date.now() - 2 * DAY).toISOString()})})).tlsRowHtml();
  assert.match(expired, /<pill err>פג תוקף/);
  const soon = ctxWith(tls({on_disk: cert({not_after: new Date(Date.now() + 10.5 * DAY).toISOString()})})).tlsRowHtml();
  assert.match(soon, /<pill warn>עוד 10 ימים/);
});

test('pending restart and a hostname missing from the cert are said in the row and explained in the details, not hidden', () => {
  const ctx = ctxWith(tls({pending_restart: true, covers_hostname: false, hostname: 'imagesrv-ta'}));
  const row = ctx.tlsRowHtml();
  assert.match(row, /<pill warn>ממתינה לאתחול/);
  assert.match(row, /<pill warn>השם אינו בתעודה/);
  ctx.tlsDetailsSheet();
  const note = ctx.sheets.at(-1).note;
  assert.match(note, /באתחול השרת/);
  assert.match(note, /<span class="mono" dir="ltr">imagesrv-ta<\/span> אינו בתעודה/);
});

test('#1676: an unread value says "לא נקרא"; a raw system error (path, Errno) sits behind "פרטים טכניים", a Hebrew sentence stays in the row', () => {
  const ctx = ctxWith({});
  const raw = ctx.settingUnreadHtml("רשימת אזורי הזמן לא נקראה: [Errno 2] No such file or directory: '\\usr\\share\\zoneinfo\\zone.tab'");
  assert.match(raw, /^<span class="muted">לא נקרא<\/span><details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">[^<]*Errno 2[^<]*<\/code><\/details>$/);
  assert.equal(ctx.settingUnreadHtml('שגיאה 500'), '<span class="muted">לא נקרא: שגיאה 500</span>');
  assert.equal(ctx.settingUnreadHtml(''), '<span class="muted">לא נקרא</span>');
});

test('the footer names a CA-signed certificate as such', () => {
  const ctx = ctxWith({});
  assert.equal(ctx.tlsModeHe({mode: 'ca-signed'}), 'תעודה חתומה');
  assert.equal(ctx.tlsModeHe({mode: 'self-signed'}), 'תעודה עצמית');
  assert.doesNotMatch(js, /"TLS תעודה עצמית"/, 'no hard-coded self-signed label left');
});

test('settings page has the TLS row and loads it; the host name is not a second card any more', () => {
  assert.match(js, /key: "tls", card: "security", label: "תעודת TLS", type: "tls"/);
  assert.match(functionSource('loadSettingsData'), /await loadHostCfg\(\)/);
  assert.doesNotMatch(js, /function hostnameCardHtml|function tlsCardHtml|title: "שם המארח"/);
});

test('credential files and the name field are LTR; both restart the server behind a red button with consequences; no typed confirmation word', () => {
  const upload = functionSource('tlsUploadSheet'), rename = functionSource('hostnameSheet');
  assert.equal((upload.match(/type: "file", dir: "ltr"/g) || []).length, 2);
  assert.match(rename, /id: "hostname", label: "שם חדש", value: name, dir: "ltr"/);
  assert.match(rename, /post\("\/hostname", \{ hostname: v\.hostname \}\)/, 'one name: the host name');
  for (const src of [upload, rename]) {
    assert.match(src, /danger: true/);
    assert.match(src, /impactList\(/);
    assert.doesNotMatch(src, /confirm_name|הקלד/);
  }
});

test('"הנפקת תעודה חדשה…" appears only for a self-signed cert, next to the upload button (נדב 17/10)', () => {
  const signed = ctxWith(tls());
  signed.tlsDetailsSheet();
  assert.equal(signed.sheets.at(-1).extra, null, 'a CA-signed cert is replaced by upload, not by a new self-signed one');
  const own = ctxWith(tls({on_disk: cert({self_signed: true, issuer: 'imagesrv-console'})}));
  own.tlsDetailsSheet();
  const s = own.sheets.at(-1);
  assert.equal(s.submitLabel, 'העלאת תעודה…');
  assert.equal(s.extra.label, 'הנפקת תעודה חדשה…');
});

test('the reissue window is a red-button confirmation: browser re-approval once, restart, previous kept; POST /tls/reissue; no typed word', () => {
  const src = functionSource('tlsReissueSheet');
  assert.match(src, /confirmSheet\("הנפקת תעודה חדשה"/);
  assert.match(src, /הדפדפן יבקש לאשר את התעודה מחדש, פעם אחת/);
  assert.match(src, /בהפעלה מחדש של השרת/);
  assert.match(src, /post\("\/tls\/reissue", \{\}\)/);
  assert.doesNotMatch(src, /confirm_name|הקלד/);
  assert.match(functionSource('confirmSheet'), /danger = true/, 'confirmSheet is red by default');
});
