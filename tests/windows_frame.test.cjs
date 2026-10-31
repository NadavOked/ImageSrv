// שלב 1 (מוקאפ הקונסולה): מסגרת החלונות האחידה. sheet() ו-openModalContent() מציירים כותרת + X תמיד,
// פס תחתון קבוע (ביטול לידו, ראשי בקצה), רוחב לפי סוג, סיסמה אחת עם מדיניות חיה, ו-dropzone אחד.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../server/static');
const source = fs.readFileSync(path.join(root, 'console.js'), 'utf8');

function node() {
  const classes = new Set();
  const listeners = {};
  return {value: '', innerHTML: '', textContent: '', className: '', dataset: {}, disabled: false, hidden: false,
    style: {removeProperty() {}, setProperty() {}},
    classList: {add: (c) => classes.add(c), remove: (c) => classes.delete(c), toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), contains: (c) => classes.has(c)},
    addEventListener: (ev, fn) => { (listeners[ev] = listeners[ev] || []).push(fn); },
    fire: (ev) => (listeners[ev] || []).forEach((fn) => fn({preventDefault() {}, key: ''})),
    removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], setAttribute() {}, removeAttribute() {}, focus() {}, remove() {}};
}

function setup() {
  const registry = new Map();
  const get = (sel) => { const k = String(sel).replace(/^#/, ''); if (!registry.has(k)) registry.set(k, node()); return registry.get(k); };
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelectorAll: () => [], querySelector: get, getElementById: get, activeElement: null, body: {},
      createElement: () => node(), documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false})},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    requestAnimationFrame: () => 1, fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(source, ctx);
  return {run: (s) => vm.runInContext(s, ctx), get, ctx};
}

test('sheet: title + sub + X always, footer = hint, cancel, then the primary at the end', () => {
  const {run, get} = setup();
  run('sheet({title:"שינוי שם", sub:"CLONE-01", fields:[{id:"n",label:"שם",value:"a",help:"יופיע בעץ"}], onSubmit(){}})');
  const html = get('sheet').innerHTML;
  assert.match(html, /<h2 id="sheetTitle">שינוי שם<\/h2><p>CLONE-01<\/p>/);
  assert.match(html, /class="n-x"[^>]*id="sf-x"|id="sf-x"[^>]*>/, 'a close X in the header');
  assert.match(html, /<label for="sf-n">שם<\/label>\s*<input class="n-in"/, 'label above a 36px field');
  assert.match(html, /<div class="n-help">יופיע בעץ<\/div>/);
  const hint = html.indexOf('id="sf-hint"'), cancel = html.indexOf('id="sf-cancel"'), submit = html.indexOf('id="sf-submit"');
  assert.ok(hint > 0 && hint < cancel && cancel < submit, 'RTL order: hint, cancel, primary last (the end edge)');
  assert.match(html, /class="btn primary" type="submit"/);
  assert.equal(get('sheet').className, 'sheet win n-narrow');
});

test('sheet: width follows the content (narrow 440 by default, mid 600 for six fields, wide on request)', () => {
  const {run, get} = setup();
  const f = (i) => `{id:"f${i}",label:"x"}`;
  run(`sheet({title:"t", fields:[${[0, 1, 2, 3, 4, 5].map(f)}], onSubmit(){}})`);
  assert.equal(get('sheet').className, 'sheet win n-mid');
  run('sheet({title:"t", size:"wide", fields:[], onSubmit(){}})');
  assert.equal(get('sheet').className, 'sheet win n-wide');
});

test('sheet: a destructive confirmation is cancel + a red button, no typed field (#1270)', async () => {
  const {run, get} = setup();
  run('globalThis.done=false; sheet({title:"מחיקה", sub:"השלכות", danger:true, submitLabel:"מחק", onSubmit(){ globalThis.done=true; }})');
  const html = get('sheet').innerHTML;
  assert.match(html, /class="btn danger" type="submit"/);
  assert.match(html, /id="sf-cancel"/);
  assert.doesNotMatch(html, /<input|sf-verify/, 'no field to type in');
  assert.notEqual(get('sf-submit').disabled, true, 'enabled: the red button is the confirmation');
  await get('sheet').onsubmit({preventDefault() {}});
  assert.equal(run('done'), true);
});

test('sheet: onSubmit may return a follow-up that runs after the window closes; the cancel label can be renamed', async () => {
  const {run, get} = setup();
  run('globalThis.order = []; sheet({title:"t", cancelLabel:"סגירה", onSubmit: async () => () => { order.push(document.getElementById("modal").classList.contains("show") ? "open" : "closed"); }})');
  assert.match(get('sheet').innerHTML, /<button class="btn" type="button" id="sf-cancel">סגירה<\/button>/);
  await get('sheet').onsubmit({preventDefault() {}});
  assert.equal(run('order.join(",")'), 'closed', 'the follow-up runs once, after close');
  run('sheet({title:"t", onSubmit(){}})');
  assert.match(get('sheet').innerHTML, /id="sf-cancel">ביטול</, 'default stays "ביטול"');
});

test('#1268: the change-password window draws the login-style fields — current without rules, new with the green-check policy list, then confirm', () => {
  const {run, get} = setup();
  run('ME={username:"nadav",role:"admin"}; accountChangePassword()');
  const html = get('sheet').innerHTML;
  const cur = html.indexOf('id="sf-current"'), nxt = html.indexOf('id="sf-next"'), pol = html.indexOf('class="pw-pol"'), cnf = html.indexOf('id="sf-next-confirm"');
  assert.ok(cur > 0 && cur < nxt && nxt < pol && pol < cnf, 'current, new, rules, confirm');
  assert.match(html, /<input class="n-in" type="password" dir="ltr" id="sf-current" autocomplete="current-password">/);
  assert.match(html, /<input class="n-in" type="password" dir="ltr" id="sf-next" autocomplete="new-password">/);
  assert.equal((html.match(/class="pw-pol"/g) || []).length, 1, 'one rules list, under the new password');
  assert.equal((html.match(/class="pw-eye"/g) || []).length, 3, '"הצג" in each of the three');
  assert.match(html, /<li data-rule="0"><i><svg[^]*?<\/svg><\/i>לפחות 8 תווים<\/li>/, 'the login check icon');
});

test('password field: a read-only value for one-time display (#1268) — escaped, LTR, same "הצג" button', () => {
  const {run} = setup();
  const html = run('passwordFieldHtml({id:"x", label:"סיסמה", value:"a\\"b<c", readonly:true, autocomplete:"off"})');
  assert.match(html, /<input class="n-in" type="password" dir="ltr" id="x" autocomplete="off" value="a&quot;b&lt;c" readonly>/);
  assert.match(html, /class="pw-eye" data-pw="x" aria-pressed="false">הצג</);
  assert.doesNotMatch(run('passwordFieldHtml({id:"y", label:"l"})'), /value=|readonly/, 'the editable field is unchanged');
});

test('password: one component — show/hide button, live policy that mirrors the server (not "any character")', () => {
  const {run, get} = setup();
  run('sheet({title:"סיסמה", fields:[{id:"password",label:"סיסמה חדשה",type:"password",confirm:"אימות"}], onSubmit(){}})');
  const html = get('sheet').innerHTML;
  assert.match(html, /class="pw-eye" data-pw="sf-password"/);
  assert.equal((html.match(/class="pw-eye"/g) || []).length, 2, 'the confirm box has its own show button');
  assert.equal((html.match(/<ul class="pw-pol"/g) || []).length, 1, 'the policy list is on the new password only');
  assert.equal(JSON.stringify(run('passwordPolicyState("abc12!xy")')), '[true,true,true,true]');
  assert.equal(JSON.stringify(run('passwordPolicyState("abc12")')), '[false,true,true,false]');
  // "_" אינו תו מיוחד אצל השרת (server/console/users.py) — הרשימה לא תהיה ירוקה כשהשרת יסרב
  assert.equal(run('passwordPolicyState("abcdefg1_")[3]'), false);
  assert.equal(run('passwordPolicyState("abcdefg1#")[3]'), true);
});

test('modal: openModalContent draws title, sub, the width class and a primary/danger button', () => {
  const {run, get} = setup();
  run('openModalContent("מחיקה","<p>x</p>","מחק","closeModal()",true,"תת","narrow")');
  assert.equal(get('modalTitle').textContent, 'מחיקה');
  assert.equal(get('modalSub').textContent, 'תת');
  assert.equal(get('modalSub').hidden, false);
  assert.equal(get('modalBox').className, 'modal win n-narrow');
  assert.equal(get('modalOk').className, 'btn danger');
  run('openModalContent("אישור","<p>x</p>")');
  assert.equal(get('modalSub').hidden, true, 'no sub, no empty line');
  assert.equal(get('modalBox').className, 'modal win n-mid', 'mid (600) is the default for hand-built bodies');
  assert.equal(get('modalOk').className, 'btn primary');
});

test('dropzone: one component for the upload windows — the real file input keeps its id', () => {
  const {run} = setup();
  const html = run('dropzoneHtml({id:"imageFile", accept:".tar", help:"עזרה"})');
  assert.match(html, /<div class="n-drop" data-dz role="button" tabindex="0"/);
  assert.match(html, /<input type="file" id="imageFile" accept="\.tar"/);
  assert.match(html, /גררו קובץ לכאן או לחצו לבחירה/);
});

test('the two upload windows use the dropzone, and no window body has a bare file input', () => {
  const c = source;
  const d = fs.readFileSync(path.join(root, 'drivers.js'), 'utf8');
  assert.match(c, /dropzoneHtml\(\{ id: "imageFile"/);
  assert.match(d, /dropzoneHtml\(\{ id: "driverFile"/);
  assert.doesNotMatch(c + d, /<input type="file" id="(imageFile|driverFile)"/);
});

test('new user: the password is permanent — no must-change option in the sheet or the POST', () => {
  const body = source.slice(source.indexOf('function openNewUser()'), source.indexOf('/* ---------- #984'));
  assert.doesNotMatch(body, /must_change|temporary|זמנית/);
  assert.match(body, /type: "password"/);
  assert.match(body, /post\("\/users", \{ \.\.\.v, mfa_required: !!v\.mfa_required \}\)/);   // + "דרוש אימות דו-שלבי" (18/10)
});

test('wizard: steps on the side, the next button follows step validity, finish calls onFinish with the state', async () => {
  const {run, get} = setup();
  run(`globalThis.st={}; globalThis.fin=null; openWizard({title:"אשף", steps:[
    {label:"א", render(){}, valid:(s)=>!!s.ok, hint:"מלאו"},
    {label:"ב", render(){}}], state:globalThis.st, finishLabel:"סיום", onFinish: async (s)=>{ globalThis.fin=s; }})`);
  assert.equal(get('modalBox').className, 'modal win n-wide');
  assert.match(get('modalBody').innerHTML, /<ol class="n-steps" id="wz-steps">/);
  assert.equal(get('modalOk').disabled, true, 'step 1 is invalid');
  assert.equal(get('modalHint').textContent, 'מלאו');
  run('st.ok = true; st.redraw()');
  assert.equal(get('modalOk').disabled, false);
  assert.equal(get('modalOk').textContent, 'הבא');
  await get('modalOk').onclick();
  assert.equal(get('modalOk').textContent, 'סיום', 'the last step offers the finish label');
  await get('modalOk').onclick();
  assert.equal(run('fin === st'), true, 'finish hands over the collected state');
});

test('no scrollbar is drawn anywhere, and the scrolling regions of a window can take keyboard focus', () => {
  const css = fs.readFileSync(path.join(root, 'console.css'), 'utf8');
  assert.match(css, /\*\{scrollbar-width:none\}/);
  assert.match(css, /\*::-webkit-scrollbar\{display:none;width:0;height:0\}/);
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  assert.match(html, /id="modalBody" tabindex="0"/);
  assert.match(html, /id="drawerBody" tabindex="0"/);
});
