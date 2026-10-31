// ‏#1255: פאנל "משימות אחרונות" — התחלה / משך / יוזם היו "-" קבוע בקוד. עיקרון 5: ערך שלא ידוע = "לא ידוע".
// console.js רץ ב-vm מול DOM מזערי; השעון נשלט כדי שהמשך יהיה דטרמיניסטי.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

const T0='2026-10-10T13:25:59+00:00';
const at=(min)=>Date.parse(T0)+min*60000;

function setup() {
  const body={innerHTML:''}, count={textContent:''}, summary={textContent:''};
  const clock={now:at(5)};
  class FakeDate extends Date { constructor(...a){ if(a.length) super(...a); else super(clock.now); } static now(){ return clock.now; } }
  const el=()=>({value:'',textContent:'',innerHTML:'',hidden:false,dataset:{},style:{},classList:{add(){},remove(){},toggle(){},contains:()=>false},
    setAttribute(){},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],closest:()=>null,focus(){}});
  const doc={querySelector:(s)=>s==='#taskPanel tbody'?body:s==='.task-count'?count:s==='.task-summary'?summary:el(),querySelectorAll:()=>[],getElementById:()=>el(),
    createElement:()=>el(),documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},body:{appendChild(){}}};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date:FakeDate,Set,Map,Number,Math,JSON,Promise,String,Array,Object,encodeURIComponent,decodeURIComponent,
    document:doc,window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  for(const f of ['progress.js','console.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=(s)=>vm.runInContext(s,ctx);
  run('ME={timezone:"UTC",username:"admin",role:"admin",capabilities:{}}; overviewError="";');
  return {run,body,count,summary,clock};
}
const task=(over={})=>({id:'tsk_749e',mac:'b4:2e:99:07:1a:c4',machine:'HP-BUILD',type:'capture',disk:'sda',name:'Office 2024',folder:'Office',state:'running',
  created_at:T0,updated_at:T0,created_by:'nadav',created_by_role:'admin',bytes_total:100,bytes_written:50,...over});
// טקסט התא בלי תגיות — מעבר אחד תו-תו, כמו stripTags בקונסולה (CodeQL js/incomplete-multi-character-sanitization).
const text=(html)=>{let out='',tag=false;for(const ch of html){if(tag){if(ch==='>')tag=false;}else if(ch==='<')tag=true;else out+=ch;}return out;};
const cells=(html)=>[...html.matchAll(/<tr[^>]*>(.*?)<\/tr>/g)].map((m)=>[...m[1].matchAll(/<td[^>]*>(.*?)<\/td>/g)].map((c)=>text(c[1]).replaceAll('&#39;',"'")));

test('fmtSpan: seconds / minutes / hours; unknown or negative is null (not 0)',()=>{
  const {run}=setup();
  assert.equal(run('fmtSpan(42)'),"42 שנ'"); assert.equal(run('fmtSpan(300)'),"5 דק'");
  assert.equal(run('fmtSpan(3600)'),"1 שע'"); assert.equal(run('fmtSpan(3900)'),"1 שע' 5 דק'");
  assert.equal(run('fmtSpan(null)'),null); assert.equal(run('fmtSpan(-1)'),null); assert.equal(run('fmtSpan(NaN)'),null);
});

test('a running capture: start from created_at, duration = now - start, initiator = the user',()=>{
  const {run,body,count}=setup();
  run(`OVERVIEW={}; CAPTURE_TASKS=${JSON.stringify([task()])}; renderActivity()`);
  const [row]=cells(body.innerHTML);
  assert.equal(row[2],'היום 13:25','start (taskWhen)'); assert.equal(row[3],"5 דק'",'duration'); assert.equal(row[5],'nadav','initiator');
  assert.match(body.innerHTML,/<span class="pill info">/,'state as a pill'); assert.match(body.innerHTML,/<td class="num">היום 13:25<\/td><td class="num">5 דק&#39;<\/td>/,'times are tabular-nums cells');
  assert.equal(count.textContent,'1');
});

test('the duration is recomputed on every refresh',()=>{
  const {run,body,clock}=setup();
  run(`OVERVIEW={}; CAPTURE_TASKS=${JSON.stringify([task()])}; renderActivity()`);
  assert.equal(cells(body.innerHTML)[0][3],"5 דק'");
  clock.now=at(7); run('renderActivity()');
  assert.equal(cells(body.innerHTML)[0][3],"7 דק'");
});

test('a capture started from the build machine (deploy user) says "מחשב בנייה <name>"; an admin from the console says the user',()=>{
  const {run,body}=setup();
  run(`OVERVIEW={}; CAPTURE_TASKS=${JSON.stringify([task({created_by:'kiosk-user',created_by_role:'deploy'})])}; renderActivity()`);
  assert.equal(cells(body.innerHTML)[0][5],'מחשב בנייה HP-BUILD');
});

test('unknown values say "לא ידוע" — never "-" and never an empty cell (principle 5)',()=>{
  const {run,body}=setup();
  run(`OVERVIEW={}; CAPTURE_TASKS=${JSON.stringify([task({created_at:null,created_by:null,created_by_role:null})])}; renderActivity()`);
  const [row]=cells(body.innerHTML);
  assert.equal(row[2],'לא ידוע'); assert.equal(row[3],'לא ידוע'); assert.equal(row[5],'לא ידוע');
  assert.doesNotMatch(body.innerHTML,/<td[^>]*>-<\/td>|<td[^>]*><\/td>/);
});

test('a finished capture shows "הסתיים אחרי X" below the active ones and is not counted as active',()=>{
  const {run,body,count,summary}=setup();
  const done=task({id:'tsk_done',state:'done',updated_at:new Date(at(12)).toISOString()});
  run(`OVERVIEW={}; CAPTURE_TASKS=${JSON.stringify([done])}; renderActivity()`);
  assert.match(body.innerHTML,/אין העברות פעילות/);
  const rows=cells(body.innerHTML);
  assert.equal(rows.at(-1)[3],"הסתיים אחרי 12 דק'"); assert.equal(count.textContent,'0'); assert.equal(summary.textContent,'אין העברות פעילות');
});

test('a round: start = started_at (else created_at), opener = opened_by; a deploy opener is the build machine; a closed round shows its total time',()=>{
  const {run,body}=setup();
  const session={id:'s1',state:'running',image_name:'Windows 11',group_label:'חדר המשכפלים',opened_by:'nadav',opened_by_role:'admin',created_at:T0,started_at:new Date(at(1)).toISOString(),closed_at:null};
  run(`OVERVIEW={session:${JSON.stringify(session)}}; CAPTURE_TASKS=[]; renderActivity()`);
  let r=cells(body.innerHTML)[0];
  assert.equal(r[2],'היום 13:26'); assert.equal(r[3],"4 דק'"); assert.equal(r[5],'nadav');
  run(`OVERVIEW={session:${JSON.stringify({...session,opened_by_role:'deploy',started_at:null})}}; renderActivity()`);
  r=cells(body.innerHTML)[0]; assert.equal(r[2],'היום 13:25','no started_at yet → created_at'); assert.equal(r[5],'מחשב בנייה (nadav)');
  run(`OVERVIEW={session:${JSON.stringify({...session,state:'closed',closed_at:new Date(at(31)).toISOString()})}}; renderActivity()`);
  assert.equal(cells(body.innerHTML)[0][3],"הסתיים אחרי 30 דק'");
});

test('a pull: the machine itself is the initiator',()=>{
  const {run,body}=setup();
  const pull={image_name:'Ubuntu',created_at:T0,members:[{mac:'aa:bb',hostname:'LAB-07',state:'writing',bytes_written:1,bytes_total:2}]};
  run(`OVERVIEW={pulls:[${JSON.stringify(pull)}]}; CAPTURE_TASKS=[]; renderActivity()`);
  const r=cells(body.innerHTML)[0];
  assert.equal(r[5],'מחשב LAB-07'); assert.equal(r[2],'היום 13:25'); assert.equal(r[3],"5 דק'");
});

test('the panel header has the six columns, aligned from the start',()=>{
  const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
  assert.match(html,/<th>משימה<\/th><th>יעד<\/th><th>התחלה<\/th><th>משך<\/th><th>מצב<\/th><th>יוזם<\/th>/);
});

test('final mockup: a row with a known percentage carries a progress bar under the task name; an unknown one carries none (never a 0% bar); a failed row is marked',()=>{
  const {run,body}=setup();
  run(`OVERVIEW={}; CAPTURE_TASKS=${JSON.stringify([task({bytes_total:100,bytes_written:50}),task({id:'tsk_2',name:'Lab',bytes_total:null,bytes_written:0}),task({id:'tsk_3',name:'Broken',state:'failed',error:'I/O error'})])}; renderActivity()`);
  const rows=[...body.innerHTML.matchAll(/<tr[^>]*>.*?<\/tr>/g)].map((m)=>m[0]);
  const byName=(n)=>rows.find((r)=>r.includes('>'+n));
  assert.match(byName('Office 2024'),/role="progressbar"[^>]*aria-valuenow="50"/);
  assert.doesNotMatch(byName('Lab'),/progressbar/,'no denominator: no bar');
  assert.match(byName('Broken'),/^<tr class="fail[ "]/,'a failed task is marked for the red name');
});

test('final mockup: opening the panel marks the body so the dock header moves above the rows, and the panel height is measured from the table (96..260px)',()=>{
  const {run}=setup();
  run(`globalThis.cls=new Set(); globalThis.bodyCls=new Set(); globalThis.vars={};
    const mk=(set)=>({toggle(c){ set.has(c)?set.delete(c):set.add(c); },contains:(c)=>set.has(c)});
    const panel={classList:mk(cls),querySelector:()=>({scrollHeight:400})};
    document.getElementById=(id)=>id==='taskPanel'?panel:null;
    document.body={classList:mk(bodyCls),style:{setProperty:(k,v)=>{vars[k]=v;}}};`);
  run('toggleTaskPanel()');
  assert.equal(run('bodyCls.has("tasks-open")'),true); assert.equal(run('vars["--task-h"]'),'260px','capped at 260');
  run('toggleTaskPanel()');
  assert.equal(run('bodyCls.has("tasks-open")'),false);
  const css=fs.readFileSync(path.join(root,'console.css'),'utf8');
  assert.match(css,/body\.tasks-open \.app>\.task-dock\{bottom:var\(--task-h/,'the dock sits above the open rows');
});

// נדב 17/10: הדוק סגור בכל כניסה; מה שנפתח נזכר לאותו סשן בלבד; התפריט הצדדי נגמר מעל הדוק.
test('dock: closed on login (activity already running at login does not open it); a user-open is remembered in sessionStorage and showLogin forgets it',()=>{
  const {run}=setup();
  run(`globalThis.store={}; globalThis.sessionStorage={getItem:(k)=>k in store?store[k]:null,setItem:(k,v)=>{store[k]=v;},removeItem:(k)=>{delete store[k];}};
    globalThis.cls=new Set(); globalThis.bodyCls=new Set(); globalThis.toggles=0;
    const mk=(set)=>({toggle(c){ set.has(c)?set.delete(c):set.add(c); },contains:(c)=>set.has(c),remove(c){set.delete(c);}});
    globalThis.panel={classList:mk(cls),querySelector:()=>({scrollHeight:100})};
    document.getElementById=(id)=>id==='taskPanel'?panel:null;
    document.body={classList:mk(bodyCls),style:{setProperty(){}}};
    activityRows=()=>({rows:globalThis.rows});`);
  run('rows=[{}]; homeDockAuto()');
  assert.equal(run('cls.has("open")'),false,'activity already there at login: stays closed');
  run('rows=[]; homeDockAuto(); rows=[{}]; homeDockAuto()');
  assert.equal(run('cls.has("open")'),true,'activity that STARTS after login still opens it (#1300)');
  assert.equal(run('store["imagesrv.dock.open"]'),'1','remembered for this session');
  run('cls.delete("open"); bodyCls.delete("tasks-open"); restoreTaskDock()');
  assert.equal(run('cls.has("open")'),true,'a reload in the same session restores it');
  run('store={}; cls.delete("open"); restoreTaskDock()');
  assert.equal(run('cls.has("open")'),false,'nothing remembered = closed');
  run('dockStore(true)'); assert.equal(run('"imagesrv.dock.open" in store'),true);
  run('dockStore(false)'); assert.equal(run('"imagesrv.dock.open" in store'),false);
});

test('sidebar: nav and rail end above the dock (34px closed, + panel height open) so the foot card is never hidden',()=>{
  const css=fs.readFileSync(path.join(root,'console.css'),'utf8');
  assert.match(css,/\.sidebar\{--dock-h:34px\}/);
  assert.match(css,/body\.tasks-open \.sidebar\{--dock-h:calc\(34px \+ var\(--task-h/);
  assert.match(css,/\.sidebar \.nav-full,\.sidebar \.rail\{bottom:var\(--dock-h\)\}/);
});

test('Multicast dot: an udp-sender inside an open round is "sending" (green), an orphan is red with the reason in the tooltip',()=>{
  const {run}=setup();
  run(`globalThis.el={cls:new Set(),attrs:{},classList:{toggle(c,on){ on?el.cls.add(c):el.cls.delete(c); }},setAttribute(k,v){el.attrs[k]=v;}};
    document.querySelector=(s)=>s==='[data-svc="Multicast"]'?el:null; document.getElementById=()=>null;`);
  const paint=(state,detail)=>{ run(`HEALTH=[{id:"udp_sender",state:${JSON.stringify(state)},detail:${JSON.stringify(detail)}}]; el.cls.clear(); paintServiceStatus()`); return {cls:run('[...el.cls].join()'),title:run('el.attrs.title')}; };
  let r=paint('warn','רץ (PID 7) — יש סבב פתוח'); assert.equal(r.cls,'svc-ok'); assert.match(r.title,/משדר כעת/);
  r=paint('bad','רץ בלי סבב (PID 7) — יתום אחרי סגירה, יש לעצור אותו'); assert.equal(r.cls,'svc-err'); assert.match(r.title,/Multicast \(משדר udp-sender\): תקלה — רץ בלי סבב/);
  r=paint('warn','משהו אחר'); assert.equal(r.cls,'svc-warn');
});
