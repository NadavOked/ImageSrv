// ‏#2552 — לשונית "סשנים" ב"משתמשים והרשאות", לפי המוקאפ המאושר (נדב 06/11): רשימה לפי משתמש, ניתוק סשן בודד,
// "צא מכל המכשירים" עם האזהרה לעצמך, הסשן הנוכחי לא ניתן לניתוק בשורה, ובלי הקלדת מילה לאישור (#1270).
// בלי דפדפן: progress.js + console.js + net.js + sessions.js ב-vm מול DOM מזערי ו-fetch מזויף.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

const NOW=Date.now(), iso=(msAgo)=>new Date(NOW-msAgo).toISOString();
const S=(o={})=>({sid:'a3f9c1',user:'nadav',role:'admin',ip:'192.168.68.20',since:iso(3600e3),last_seen:iso(1000),current:true,monitor:0,...o});
const LIST=[
  S(),
  S({sid:'7be204',ip:'192.168.68.77',since:iso(30*3600e3),last_seen:iso(3*3600e3),current:false}),
  S({sid:'c10d58',user:'dana',ip:'192.168.68.31',last_seen:iso(240e3),current:false,monitor:1}),
  S({sid:'e66a2f',user:'lab-deploy',role:'deploy',ip:'192.168.68.94',last_seen:iso(41*60e3),current:false}),
];

function setup(fixtures={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,disabled:false,dataset:{},style:{},
      classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(){},setAttribute(){},removeAttribute(){},getAttribute(){return null;},focus(){}});
    return nodes.get(key);
  }
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),createElement:()=>node('el'+Math.random()),
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){},body:{appendChild(){}}},
    navigator:{clipboard:{writeText:async()=>{}}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){throw new Error('localStorage written');}},
    sessionStorage:{getItem:()=>null,setItem(){throw new Error('sessionStorage written');}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0], method=options.method||'GET';
      requests.push({url:key,method,body:typeof options.body==='string'?JSON.parse(options.body):null});
      let f=fixtures[method+' '+key]??fixtures[key];
      if(typeof f==='function') f=f(requests[requests.length-1]);
      if(f && f.__status) return {status:f.__status,ok:false,json:async()=>({detail:f.detail})};
      return {status:200,ok:true,json:async()=>f ?? {}};
    }});
  for(const f of ['progress.js','console.js','net.js','sessions.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={timezone:"UTC",username:"nadav",role:"admin",version:"v0.78.0",capabilities:{}}; USERS=[]; current="permissions"; currentTab=1; globalThis.sheets=[]; globalThis.toasts=[]; globalThis.loggedOut=false;'
    +'sheet=o=>{sheets.push(o);}; toast=(m,ms,kind)=>{toasts.push([m,kind]);}; renderCurrent=()=>{globalThis.rendered=(globalThis.rendered||0)+1;}; showLogin=()=>{loggedOut=true;};');
  return {run,requests,ctx};
}
const lastSheet=(s)=>s.run('sheets[sheets.length-1]');
const withList=(s,list=LIST)=>s.run('SESS='+JSON.stringify(list)+'; sessErr=""; SESS_Q=""');
const plain=(html)=>html.replace(/<[^>]*>/g,'');

test('the tab exists for an admin on the main server only; deploy and a secondary context get no tab', () => {
  const s=setup(); withList(s);
  assert.equal(s.run('sessionsAllowed()'),true);
  assert.match(s.run('permissions()'),/<button type="button" class="tab on"[^>]*>סשנים<\/button>/);
  assert.match(s.run('permissions()'),/<button type="button" class="tab"[^>]*>משתמשים<\/button>/);
  s.run('ME.role="deploy"'); assert.equal(s.run('sessionsAllowed()'),false);
  assert.doesNotMatch(s.run('permissions()'),/סשנים/);
  s.run('ME.role="admin"; SERVER_CTX="n1"'); assert.equal(s.run('sessionsAllowed()'),false);
  assert.doesNotMatch(s.run('permissions()'),/class="tab/);
});

test('first tab keeps the users table and its "+ משתמש"; the sessions tab has only refresh in the header', () => {
  const s=setup(); withList(s);
  s.run('currentTab=0'); const users=s.run('permissions()');
  assert.match(users,/openNewUser\(\)/); assert.doesNotMatch(users,/sess-table/);
  s.run('currentTab=1'); const tab=s.run('permissions()');
  assert.doesNotMatch(tab,/openNewUser\(\)/); assert.match(tab,/refreshPage\(\)/); assert.match(tab,/id="sess-table"/);
});

test('grouped by user, with the role and the count, and "צא מכל המכשירים" per group', () => {
  const s=setup(); withList(s);
  const html=s.run('sessionsTab()');
  assert.match(html,/<tr class="group"><td colspan="6">nadav<span class="cnt">מנהל · 2 סשנים<\/span>/);
  assert.match(html,/dana<span class="cnt">מנהל · 1 סשן<\/span>/);
  assert.match(html,/lab-deploy<span class="cnt">הפצה · 1 סשן<\/span>/);
  assert.equal((html.match(/sessionsAllSheet\('(nadav|dana|lab-deploy)'\)">צא מכל המכשירים<\/button>/g)||[]).length,3);
  for (const sid of ['a3f9c1','7be204','c10d58','e66a2f']) assert.match(html,new RegExp(`data-sid="${sid}"`));
  assert.match(html,/הרשימה כוללת רק סשנים שהשרת עדיין מקבל/);
});

test('the top button is "צא מכל המכשירים שלי…" for the signed-in user', () => {
  const s=setup(); withList(s);
  assert.match(s.run('sessionsTab()'),/sessionsAllSheet\('nadav'\)">צא מכל המכשירים שלי…<\/button>/);
});

test('the current session is marked and has no menu (its only action is forbidden); the others have one', () => {
  const s=setup(); withList(s);
  const html=s.run('sessionsTab()');
  const row=(sid)=>html.match(new RegExp(`<tr data-sid="${sid}"[^]*?</tr>`))[0];
  assert.match(row('a3f9c1'),/הסשן הזה/); assert.doesNotMatch(row('a3f9c1'),/class="kebab"/);
  assert.match(row('7be204'),/class="kebab"/); assert.doesNotMatch(row('7be204'),/הסשן הזה/);
  assert.match(row('7be204'),/ניתוק הסשן…/);
  assert.match(row('c10d58'),/מוניטור פתוח/); assert.doesNotMatch(row('7be204'),/מוניטור פתוח/);
});

test('single revoke: a red button, no typed word, says who and from where, and DELETEs by the short id', async () => {
  const s=setup({'DELETE /sessions/c10d58':{user:'dana',sid:'c10d58'},'GET /sessions':LIST.filter(x=>x.sid!=='c10d58')});
  withList(s);
  s.run("sessionRevokeSheet('c10d58')");
  const sh=lastSheet(s);
  assert.equal(s.run('sheets.length'),1);
  assert.equal(sh.danger,true); assert.equal(sh.submitLabel,'נתק את הסשן'); assert.match(sh.title,/לנתק את הסשן של dana\?/);
  assert.ok(!sh.fields||sh.fields.length===0,'#1270: no typed confirmation');
  for (const t of ['dana','192.168.68.31','c10d58','מנהל','גשר מוניטור פתוח','הסשן ייסגר מיד']) assert.ok(plain(sh.note).includes(t),t);
  await sh.onSubmit({});
  const req=s.requests.find(r=>r.method==='DELETE'); assert.equal(req.url,'/sessions/c10d58');
  assert.ok(s.requests.some(r=>r.method==='GET'&&r.url==='/sessions'),'the list is re-read after the revoke');
  assert.match(JSON.stringify(s.run('toasts')),/הסשן נותק/);
});

test('no bridge warning without a bridge; the current session, an unknown id and a non-admin never open a dialog', () => {
  const s=setup(); withList(s);
  s.run("sessionRevokeSheet('7be204')"); assert.doesNotMatch(lastSheet(s).note,/גשר מוניטור/);
  s.run("sessionRevokeSheet('a3f9c1')"); assert.equal(s.run('sheets.length'),1,'current session: no dialog');
  s.run("sessionRevokeSheet('ffffff')"); assert.equal(s.run('sheets.length'),1,'unknown id: no dialog');
  s.run('ME.role="deploy"'); s.run("sessionRevokeSheet('c10d58')"); assert.equal(s.run('sheets.length'),1,'not admin: no dialog');
});

test('a refused revoke is shown, not swallowed: onSubmit throws the server sentence', async () => {
  const s=setup({'DELETE /sessions/c10d58':{__status:404,detail:'הסשן כבר לא פעיל'}}); withList(s);
  s.run("sessionRevokeSheet('c10d58')");
  await assert.rejects(lastSheet(s).onSubmit({}),/הסשן כבר לא פעיל/);
});

test('all devices of another user: says how many, no typed word, POSTs the username', async () => {
  const s=setup({'POST /sessions/revoke':{ok:true},'GET /sessions':LIST}); withList(s);
  s.run("sessionsAllSheet('dana')");
  const sh=lastSheet(s);
  assert.match(sh.title,/להוציא את dana מכל המכשירים\?/); assert.equal(sh.danger,true); assert.equal(sh.submitLabel,'צא מכל המכשירים');
  assert.ok(!sh.fields||sh.fields.length===0); assert.match(sh.note,/הסיסמה לא משתנה/);
  assert.match(plain(sh.note),/הסשן של dana ייסגר מיד/);
  await sh.onSubmit({});
  const req=s.requests.find(r=>r.method==='POST'); assert.equal(req.url,'/sessions/revoke'); assert.deepEqual(req.body,{username:'dana'});
  assert.equal(s.run('loggedOut'),false,'revoking someone else does not log you out');
});

test('all devices for yourself: the warning names this session, and success sends you to the login screen', async () => {
  const s=setup({'POST /sessions/revoke':{ok:true}}); withList(s);
  s.run("sessionsAllSheet('nadav')");
  const sh=lastSheet(s);
  assert.match(sh.title,/לצאת מכל המכשירים שלך\?/);
  assert.match(plain(sh.note),/שני הסשנים שלך ייסגרו, כולל זה שאתה מחובר בו עכשיו. תועבר למסך הכניסה/);
  await sh.onSubmit({});
  assert.deepEqual(s.requests.find(r=>r.method==='POST').body,{username:'nadav'});
  assert.equal(s.run('loggedOut'),true); assert.equal(s.run('ME'),null);
});

test('filter by user narrows the table and says so when nothing matches', () => {
  const s=setup(); withList(s);
  s.run('SESS_Q="dan"'); const html=s.run('sessTableHtml()');
  assert.match(html,/dana/); assert.doesNotMatch(html,/lab-deploy|7be204/);
  s.run('SESS_Q="nobody"'); assert.match(s.run('sessTableHtml()'),/אין סשן שמתאים לסינון/);
  s.run('SESS=[]; SESS_Q=""'); assert.match(s.run('sessTableHtml()'),/אין סשנים פעילים/);
});

test('"not read" is its own state, never an empty list (principle 5)', () => {
  const s=setup();
  s.run('SESS=null; sessErr="שגיאה 500"'); assert.match(s.run('sessionsTab()'),/לא הצלחתי לקרוא את הסשנים: שגיאה 500/);
  s.run('SESS=null; sessErr=""'); assert.match(s.run('sessionsTab()'),/טוען נתונים/);
});

test('names and addresses are escaped, and the screen code knows only the short id', () => {
  const s=setup(); withList(s,[S({user:'<img src=x onerror=alert(1)>',ip:'"><script>',sid:'0123ab'})]);
  const html=s.run('sessionsTab()');
  assert.doesNotMatch(html,/<img src=x/); assert.doesNotMatch(html,/<script>/);
  assert.doesNotMatch(fs.readFileSync(path.join(root,'sessions.js'),'utf8'),/token_hash|document\.cookie/);
});

test('loadSessions reads GET /sessions, keeps a failure visible, and re-renders only while the tab is open', async () => {
  const s=setup({'GET /sessions':LIST}); await s.run('loadSessions()');
  assert.equal(s.run('SESS.length'),4); assert.equal(s.run('rendered'),1);
  s.run('currentTab=0'); await s.run('loadSessions()'); assert.equal(s.run('rendered'),1,'users tab: no re-render');
  const bad=setup({'GET /sessions':{__status:500,detail:'boom'}}); await bad.run('loadSessions()');
  assert.equal(bad.run('SESS'),null); assert.equal(bad.run('sessErr'),'boom');
});
