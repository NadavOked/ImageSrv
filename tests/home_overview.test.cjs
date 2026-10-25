// ‏#954 גל 1: רכיבי הרינדור המשותפים (UI.*) והסקירה הכללית שנבנתה מחדש
// לפי docs/design/console-redesign/home.md. בלי דפדפן: console.js רץ ב-vm
// מול DOM מזערי ו-fetch מזויף, והפלט נבדק כטקסט — מצב מלא, מצב ריק,
// תפקיד deploy, ומה כל לשונית מציגה. עיקרון 5: "לא נקרא" אינו "אין".
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const today=new Date().toISOString().slice(0,10);

function setup() {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const fixtures={
    '/overview':{images:6,machines:12,storage:{total_bytes:1000*1024**3,free_bytes:312*1024**3},
      session:{id:'s1',group_id:'grp_303',group_label:'כיתה 303',image_name:'Office 2024',state:'open',joined:2,expected_clients:3,starts_in_seconds:252,members:[]},
      room:null,pulls:[],now:today+'T09:45:00Z'},
    '/tasks':[{id:'t1',name:'Office 2024 v3',machine:'בנייה 1',disk:'sda',folder:'Office',state:'pending',created_at:today+'T09:40:00Z'},
              {id:'t0',name:'Kali',machine:'בנייה 1',disk:'sda',state:'done',error:null,created_at:'2026-09-12T09:40:00Z'}],
    '/net':[{mac:'de:ad:be:ef:00:01',ip:'10.44.12.199',registered:false,last_seen:today+'T09:38:00Z'},
            {mac:'aa:aa:aa:aa:aa:aa',registered:true,last_seen:today+'T09:42:00Z'},
            {mac:'bb:bb:bb:bb:bb:bb',registered:true,last_seen:'2026-09-10T09:42:00Z'}],
    '/ports':[{id:'http_console',name:'HTTP',port:'8081',state:'ok'},{id:'tftp',name:'TFTP',port:'69',state:'ok'},{id:'multicast',name:'Multicast',port:'9000–9001',state:'warn'}],
    '/health':[{id:'server',label:'השרת בכתובת ההפצה',state:'ok',detail:'עונה'},{id:'udp_sender',label:'מולטיקאסט',state:'warn',detail:'udp-sender לא נמצא'}],
    '/journal':[{ts:today+'T09:41:00Z',user:'nadav',event:'session_open',label:'סבב הפצה נפתח לכיתה 303',severity:'info'},
                {ts:'2026-09-16T08:12:00Z',user:'server',event:'transfer_failed',label:'העברה נכשלה',severity:'err'}],
    '/update':{current:'v0.30.0',enabled:true},
    '/storage-nodes':[{id:'haifa',label:'חיפה',disabled_at:null}],
    '/storage-transfers':[{id:'x1',node_id:'haifa',node_label:'חיפה',image_name:'Ubuntu 24.04',state:'failed',bytes_sent:38,bytes_total:100,error:'שרת המשנה לא ענה',created_at:today+'T08:10:00Z',started_by:'nadav'}],
    '/images':[{id:'i1',name:'Kali 2026.2',folder:'Linux',created:'2026-09-12T10:00:00Z'},{id:'i2',name:'Office',folder:'Office',created:'2026-09-01T10:00:00Z'}],
    '/folders':[{name:'Linux'},{name:'Office'}],
    '/machines':[{mac:'cc:cc:cc:cc:cc:cc',suffix:'2',group_id:'grp_CLONERS'}],'/groups':[],
    '/disk-failures':[{id:1,mac:'cc:cc:cc:cc:cc:cc',disk_number:3,serial:'S5Y2NX0R',cause:'כבל/חריץ SATA 2'}],
    '/shrink-records':[],
    // ‏#1300: "מחובר" — מ-/monitor/machines (admin_only); אחד מתוך שניים מחובר
    '/monitor/machines':[{mac:'cc:cc:cc:cc:cc:cc',online:true},{mac:'aa:aa:aa:aa:aa:aa',online:false}],
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false})},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      if(fixtures[key] instanceof Error) throw fixtures[key];
      if(fixtures[key]===403) return {status:403,ok:false,json:async()=>({detail:'admin only'})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] || {}};
    }});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx);
  vm.runInContext(fs.readFileSync(path.join(root,'console.js'),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={timezone:"UTC",username:"admin",role:"admin",server_name:"imagectl-srv",version:"v0.30.0",uptime_seconds:532802,deploy_ip:"10.44.12.1",capabilities:{enroll_secondary:true}}; current="home";');
  return {run,node,requests,fixtures};
}
function balanced(html) {
  const stack=[]; const voids=new Set(['input','br','hr','img','meta','link','source','wbr','path','rect','circle']);
  for(const m of html.matchAll(/<\/?([a-z][\w-]*)\b[^>]*>/gi)) {
    const tag=m[1].toLowerCase();
    if(m[0].endsWith('/>') || voids.has(tag)) continue;
    if(m[0].startsWith('</')) assert.equal(stack.pop(),tag,'Unbalanced '+m[0]); else stack.push(tag);
  }
  assert.deepEqual(stack,[],'Unclosed tags');
}
async function loaded(role='admin') {
  const s=setup(); s.run(`ME.role="${role}"`);
  for(const [name,key] of [['IMAGES','/images'],['FOLDERS','/folders'],['MACHINES','/machines'],['DISK_FAILURES','/disk-failures'],['SHRINK_RECORDS','/shrink-records']]) s.run(name+'='+JSON.stringify(s.fixtures[key]));   // נטענים ב-showApp, לא ב-loadHome
  if(role==='deploy') for(const k of ['/health','/journal','/update','/storage-nodes','/storage-transfers','/monitor/machines']) s.fixtures[k]=403;
  await s.run('loadHome()');
  return s;
}

// ---- רכיבי הרינדור המשותפים -------------------------------------------------

test('UI.kpi: class, label, value, meaning line, optional bar; empty class = grey (not green)',()=>{
  const {run}=setup();
  const html=run('UI.kpi({cls:"warn",label:"בריאות",value:"אזהרה",sub:"1 מתוך 9"})');
  assert.match(html,/<div class="kpi warn">/); assert.match(html,/בריאות/); assert.match(html,/אזהרה/); assert.match(html,/1 מתוך 9/); balanced(html);
  const bar=run('UI.kpi({cls:"ok",label:"אחסון",value:"312",unit:"GB פנוי",bar:66})');
  assert.match(bar,/aria-valuenow="66"/); assert.match(bar,/--w:66%/);
  assert.match(run('UI.kpi({label:"x",value:"לא נבדק"})'),/<div class="kpi ">/);
  assert.match(run('UI.kpi({label:"<b>",value:"<i>"})'),/&lt;b&gt;/,'text is escaped');
});
test('ltr() isolates number+unit and date+time runs so RTL text does not swap them',()=>{
  const {run}=setup();
  assert.equal(run('ltr("38.4 GB")'),'<bdi dir="ltr">38.4 GB</bdi>'); assert.equal(run('ltr("<x>")'),'<bdi dir="ltr">&lt;x&gt;</bdi>');
});
test('the header pill names faults as faults, warnings as warnings',async()=>{
  const s=await loaded(); assert.match(s.run('home(0)'),/<span class="pill warn">1 אזהרה<\/span>/);
  s.run('HEALTH=[{id:"a",state:"bad",label:"א"},{id:"b",state:"warn",label:"ב"}]'); assert.match(s.run('home(0)'),/<span class="pill err">2 תקלות<\/span>/);
});
test('UI.status / UI.pill: the empty class is grey and no class maps "not checked" to ok',()=>{
  const {run}=setup();
  assert.equal(run('UI.status("","לא נבדק")'),'<span class="st ">לא נבדק</span>');
  assert.equal(run('UI.pill("err","3")'),'<span class="pill err">3</span>');
});
test('UI.datagrid renders columns and rows, and an empty state that says what to do',()=>{
  const {run}=setup();
  const html=run('UI.datagrid({columns:["א","ב"],rows:[["1","2"],["3","4"]]})');
  assert.match(html,/<table class="dg"><thead><tr><th>א<\/th><th>ב<\/th>/); assert.equal((html.match(/<tr>/g)||[]).length,3); balanced(html);
  assert.equal(run('UI.datagrid({columns:["א"],rows:[],empty:"אין אימג\'ים — קלוט"})'),"<div class=\"empty\">אין אימג&#39;ים — קלוט</div>");
});
test('UI.barRow: unknown progress is a dash, never 0%',()=>{
  const {run}=setup();
  assert.match(run('UI.barRow(null)'),/--w:0%.*>—</); assert.doesNotMatch(run('UI.barRow(null)'),/0%</);
  assert.match(run('UI.barRow(38,"err")'),/bar err.*--w:38%.*>38%</);
  assert.match(run('UI.barRow(50,"","2\\/3")'),/>2\/3</);
});
test('UI.objHeader: crumbs, name, sub, pill, actions and role=tab buttons with roving tabindex; no tabs when only one',()=>{
  const {run}=setup();
  const html=run('UI.objHeader({crumbs:[{label:"שרת"},{label:"סקירה"}],name:"שרת אימג\'ים",sub:"x",pill:UI.pill("ok","תקין"),actions:"<button>a</button>",tabs:["א","ב","ג"],tab:1})');
  // ביקורת 10/10: הפריט הראשון בשביל הוא שורש העץ = שם השרת מ-/me (כמו ב-vCenter), לא הטקסט שהוזן
  assert.match(html,/<div class="crumbs"><span>imagectl-srv<\/span><span>\/<\/span><span>סקירה<\/span><\/div>/);
  const tabs=[...html.matchAll(/<button type="button" class="tab( on)?" role="tab" aria-selected="(true|false)" tabindex="(0|-1)"/g)];
  assert.deepEqual(tabs.map(m=>[m[1]||'',m[2],m[3]]),[['','false','-1'],[' on','true','0'],['','false','-1']]);
  assert.match(html,/role="tablist"/); balanced(html);
  assert.doesNotMatch(run('UI.objHeader({name:"x",tabs:["רק אחת"]})'),/role="tab"/,'a single question has no tabs (README §2)');
});
test('UI.kv, UI.timeline, UI.note, UI.empty, UI.soon are balanced and escape text',()=>{
  const {run}=setup();
  for(const s of ['UI.kv([["k","<i>v</i>"]])','UI.timeline([{t:"09:41",cls:"ok",text:"x",who:"nadav"}])','UI.note("warn","<b>x</b>")','UI.empty("ריק","<button>a</button>")','UI.card({title:"t",body:"<p>b</p>"})']) balanced(run(s));
  assert.match(run('UI.timeline([{t:"09:41",cls:"err",text:"x",who:"<s>"}])'),/class="d err".*&lt;s&gt;/);
  assert.equal(run('UI.soon("זמן פעילות")'),'<span class="muted" title="דורש API">זמן פעילות — בקרוב</span>');
});
test('#968: journal severity is the server\'s field, never derived from the event name; a foreign/missing value is neutral',()=>{
  const {run}=setup();
  const cls=r=>run(`journalCls(${JSON.stringify(r)})`);
  // אותו event, חומרה שונה מהשרת → הצבע עוקב אחרי השדה, לא אחרי השם
  assert.equal(cls({event:'capture_failed',severity:'err'}),'err');
  assert.equal(cls({event:'capture_failed',severity:'ok'}),'ok','the name says "failed" — the server said ok; the server wins');
  assert.equal(cls({event:'capture_done',severity:'warn'}),'warn');
  assert.equal(cls({event:'unknown_mac',severity:'info'}),'info');
  // חסר/זר = ניטרלי, לא ניחוש לפי השם
  assert.equal(cls({event:'capture_failed'}),'info','no severity field → neutral, not "err" from the name');
  assert.equal(cls({event:'x',severity:'purple'}),'info');
  assert.equal(run('typeof journalSeverity'),'undefined','the client-side heuristic is gone, not kept beside the field');
});
test('#968: uptime is formatted from /me.uptime_seconds; null is named, never 0',()=>{
  const {run}=setup();
  assert.equal(run('fmtUptime(532802)'),'6 ימים 4 שעות');
  assert.equal(run('fmtUptime(86400)'),'1 יום 0 שעות');
  assert.equal(run('fmtUptime(3660)'),'1 שעה 1 דקה');
  assert.equal(run('fmtUptime(125)'),'2 דקות');
  assert.equal(run('fmtUptime(null)'),'');
  assert.equal(run('homeUptime()'),'פעיל 6 ימים 4 שעות');
  run('ME.uptime_seconds=null');
  assert.equal(run('homeUptime()'),'<span class="muted">זמן פעילות לא נבדק</span>');
  run('ME.uptime_seconds=0');
  assert.equal(run('homeUptime()'),'פעיל 0 דקות','0 is a measurement (just booted), not "unknown"');
});
test('#968: the deploy IP comes from /me.deploy_ip; null is "not configured" (#1088), an older server without the field shows nothing',()=>{
  const {run}=setup();
  assert.equal(run('homeDeployIp()'),'<span class="mono">10.44.12.1</span>');
  run('ME.deploy_ip=null');
  assert.equal(run('homeDeployIp()'),'<span class="muted">רשת הפצה לא הוגדרה</span>');
  run('delete ME.deploy_ip');
  assert.equal(run('homeDeployIp()'),'');
});
test('the global .ok form-message rule is neutralised under .page',()=>{
  const css=fs.readFileSync(path.join(root,'console.css'),'utf8');
  assert.match(css,/\n\.ok\{color:var\(--clr-success-fg\); font-size:var\(--t-sm\); min-height:18px; margin-top:6px\}/,'precondition: the global rule still exists for form messages');
  const pageCss=css.slice(css.indexOf('.page{--rp:4px'));
  assert.match(pageCss,/\.page \.ok\{font-size:inherit;min-height:0;margin-top:0;color:inherit\}/);
});
test('fmtDate is dd/mm/yyyy, fmtClock is hh:mm, unknown is a dash',()=>{
  const {run}=setup();
  assert.equal(run('fmtDate("2026-09-17T09:41:00Z")'),'17/09/2026'); assert.equal(run('fmtClock("2026-09-17T09:41:00Z")'),'09:41');
  assert.equal(run('fmtDate(null)'),'—'); assert.equal(run('fmtClock("")'),'—');
});

// ---- הסקירה הכללית: מצב מלא ------------------------------------------------

test('home renders its own header (no generic toolbar, no "+ פעולה", no footer) and the page is balanced',async()=>{
  const {run}=await loaded();
  const html=run('layout(pages.home,0)');
  balanced(html);
  // ‏#1300: ‏.aligned — כותרת דביקה וצל לכרטיסים (console-full.html), רק בדפים שהוכרעו
  assert.match(html,/^<div class="page aligned"><div class="obj compact">/,'apple-pending-4: one compact row + one sub line');
  assert.match(html,/<div class="obj-name">סקירה כללית<span class="pill /,'the status pill sits in the title line (console-full.html .htxt h1)');
  // ‏#1300: משנה הכותרת = סיכום; זהות השרת (#968) עברה לכרטיס השירותים
  assert.match(html,/<div class="obj-sub"[^>]*>6 אימג&#39;ים · 12 מחשבים \(1 מחוברים\) · עודכן 09:45<\/div>/);
  assert.match(html,/<div class="svc-who">imagectl-srv · <span class="mono">10\.44\.12\.1<\/span> · v0\.30\.0 · פעיל 6 ימים 4 שעות<\/div>/,'#968: hostname · IP · version · uptime, all from /me');
  assert.doesNotMatch(html,/זמן פעילות — בקרוב/);
  // codeql[js/incomplete-sanitization] false positive: test code -- a constant list with no backslash becomes a RegExp for assert.doesNotMatch (#291)
  for(const gone of ['pageActionBtn','+ פעולה','footer-note','object-strip','vcenter-tabs','>Details<','אין נתונים להצגה']) assert.doesNotMatch(html,new RegExp(gone.replace(/[+]/g,'\\+')),gone);
  assert.match(html,/onclick="selectPageById\('deploy'\)"><svg[^]*?<\/svg> סבב חדש</); assert.match(html,/refreshPage\(\)/);
  assert.doesNotMatch(html,/openCapture\(\)/,'#1300: like the mockup — capture lives on the images page');
});
test('#1300 (option C, Nadav 12/10): three KPIs — images, connected machines, storage; no red-disk or round tiles',async()=>{
  const {run}=await loaded();
  const html=run('home(0)');
  assert.match(html,/<span class="pill warn">1 אזהרה<\/span>/,'server health lives in the header pill (console-full.html), not in a tile');
  assert.equal((html.match(/<div class="kpi /g)||[]).length,3,'images · machines · storage');
  assert.match(html,/<div class="kpi info"><div class="l">אימג&#39;ים<\/div><div class="v"><bdi dir="auto">6<\/bdi><\/div><\/div>/);
  assert.match(html,/<div class="kpi ok"><div class="l">מחשבים מחוברים<\/div><div class="v"><bdi dir="auto">\u20661 \/ 12\u2069<\/bdi><\/div><\/div>/,'LRI…PDI keeps "1 / 12" in reading order inside RTL');
  assert.doesNotMatch(html,/<div class="l">דיסק אדום<\/div>|<div class="l">סבב פעיל<\/div>|<div class="l">סבב נכשל<\/div>/,'the red disk is in "needs attention", the round in its card and the dock');
  assert.doesNotMatch(html,/<div class="s">/,'no meaning lines under the numbers (console-full.html .kpi)');
});
test('#1300: the storage card is ESXi-style — free, a bar with the percent, used and capacity — from /overview.storage',async()=>{
  const {run}=await loaded();
  const card=run('homeStorageKpi()');
  // 312 פנוי מתוך 1000 → 69% בשימוש — מתחת לסף: כחול
  assert.match(card,/^<div class="kpi kpi-store info"><div class="v"><bdi dir="ltr">312 GB<\/bdi> <small>פנוי<\/small><\/div><div class="l">אחסון אימג&#39;ים<\/div>/);
  assert.match(card,/<div class="bar " role="progressbar" aria-label="שימוש באחסון" aria-valuemin="0" aria-valuemax="100" aria-valuenow="69"><i style="--w:69%"><\/i><\/div><span class="pct">69%<\/span>/);
  assert.match(card,/<span>בשימוש <bdi dir="ltr">688 GB<\/bdi><\/span><span>קיבולת <bdi dir="ltr">1000 GB<\/bdi><\/span>/);
  balanced(card);
  assert.ok(run('home(0)').includes(card),'the card sits in the KPI row');
});
test('#1300: storage thresholds — orange from 90%, red from 95%, and a "needs attention" row from 90%',async()=>{
  const {run}=await loaded();
  const at=(free)=>{ run(`OVERVIEW={...OVERVIEW,storage:{total_bytes:100*1024**3,free_bytes:${free}*1024**3}}`); return run('home(0)'); };
  let html=at(11);   // 89%
  assert.match(html,/kpi kpi-store info/); assert.doesNotMatch(html,/אחסון האימג'ים כמעט מלא/);
  html=at(10);       // 90%
  assert.match(html,/kpi kpi-store warn/); assert.match(html,/<div class="bar warn" role="progressbar"/);
  assert.match(html,/<li class="attn-i warn"><span class="dot" aria-hidden="true"><\/span><div class="b"><b>אחסון האימג'ים כמעט מלא<\/b> — <bdi dir="ltr">90%<\/bdi> בשימוש, נשארו <bdi dir="ltr">10.0 GB<\/bdi> פנויים\.<\/div><button class="btn sm flat" onclick="selectPageById\('storage'\)">לאחסון<\/button><\/li>/);
  html=at(5);        // 95%
  assert.match(html,/kpi kpi-store err/); assert.match(html,/<li class="attn-i err">.*אחסון האימג'ים כמעט מלא/);
});
test('#1737: near the system reserve the card colours and the row says what the server keeps — even on a disk the percent calls fine',async()=>{
  const {run}=await loaded();
  // דיסק של 4TB עם הרבה רזרבות פנויות — הסף החדש אינו מוסיף רעש
  const at=(freeGb)=>{ run(`OVERVIEW={...OVERVIEW,storage:{total_bytes:4096*1024**3,free_bytes:${freeGb}*1024**3,reserve_bytes:10*1024**3}}`); return run('home(0)'); };
  let html=at(500);  // 88%, ‏50 רזרבות
  assert.match(html,/kpi kpi-store info/); assert.doesNotMatch(html,/אחסון האימג'ים כמעט מלא/);
  html=at(3500);     // 15% — ושוב שקט
  assert.doesNotMatch(html,/כמעט מלא/);
  // בלי reserve_bytes (שרת ישן) — האחוז בלבד, כמו #1300
  run('OVERVIEW={...OVERVIEW,storage:{total_bytes:100*1024**3,free_bytes:40*1024**3}}');
  assert.doesNotMatch(run('home(0)'),/כמעט מלא/);
  // ‏40GB מתוך 100GB — 60% באחוזים, אבל פחות מ-5 רזרבות: כתום, והשורה מסבירה
  run('OVERVIEW={...OVERVIEW,storage:{total_bytes:100*1024**3,free_bytes:40*1024**3,reserve_bytes:10*1024**3}}');
  html=run('home(0)');
  assert.match(html,/kpi kpi-store warn/);
  assert.match(html,/<li class="attn-i warn">.*<b>אחסון האימג'ים כמעט מלא<\/b> — <bdi dir="ltr">60%<\/bdi> בשימוש, נשארו <bdi dir="ltr">40.0 GB<\/bdi> פנויים\. השרת שומר <bdi dir="ltr">10.0 GB<\/bdi> למערכת \(ה-DB, היומנים ועדכוני השרת\), ומסרב לכל קליטה, העלאה או העברה של אימג' שתשאיר פחות מזה\./);
  // ‏19GB — פחות מ-2 רזרבות: אדום
  run('OVERVIEW={...OVERVIEW,storage:{total_bytes:100*1024**3,free_bytes:19*1024**3,reserve_bytes:10*1024**3}}');
  html=run('home(0)');
  assert.match(html,/kpi kpi-store err/); assert.match(html,/<li class="attn-i err">.*השרת שומר <bdi dir="ltr">10.0 GB<\/bdi> למערכת/);
});
test('#1300: storage the server could not read is "unknown" — never 0 and never coloured (principle 5)',async()=>{
  const {run}=await loaded();
  run('OVERVIEW={...OVERVIEW,storage:null}');
  const card=run('homeStorageKpi()');
  assert.match(card,/<div class="kpi kpi-store "><div class="v">לא ידוע<\/div>.*השרת לא החזיר נתוני אחסון/);
  assert.doesNotMatch(card,/progressbar|0%/);
  run('OVERVIEW=null');
  assert.match(run('homeStorageKpi()'),/<div class="v">—<\/div>/,'not read yet = a dash');
});
test('#1300: an unread /monitor/machines is "registered", never "0 connected" (principle 5)',async()=>{
  const s=setup(); s.fixtures['/monitor/machines']=new Error('down');
  s.run('DISK_FAILURES=[];SHRINK_RECORDS=[];toast=()=>{}');
  await s.run('loadHome()');
  const html=s.run('home(0)');
  assert.match(html,/<div class="kpi "><div class="l">מחשבים רשומים<\/div><div class="v"><bdi dir="auto">12<\/bdi>/);
  assert.match(html,/12 מחשבים · עודכן/,'the header does not claim a connected count it did not read');
  assert.doesNotMatch(html,/מחוברים\)|0 מחוברים/);
});
test('#1300: the "active round" card — image, bar, machines; the now-table moved to the tasks tab',async()=>{
  const {run}=await loaded();
  const html=run('home(0)');
  assert.doesNotMatch(html,/מה קורה עכשיו/,'console-full.html P.home: a round card, not an activity table');
  const card=html.slice(html.indexOf('<span>סבב פעיל'),html.indexOf('<span>שירותים'));
  assert.match(card,/onclick="openRoundDetail\(\)">פרטים<\/button>/);
  assert.match(card,/<div class="rh"><b>Office 2024<\/b><span class="pct">—<\/span><\/div>/,'no member progress yet = a dash, not 0%');
  assert.match(card,/<p class="rm">ממתין להצטרפות · כיתה 303 · 2\/3 מחשבים · מתחיל בעוד 4:12<\/p>/);
  assert.match(card,/onclick="startRound\(\)">התחל עכשיו/);
  const tasks=run('layout(pages.home,1)');
  assert.match(tasks,/קליטת אימג&#39; — Office 2024 v3<\/span><span class="sub">בנייה 1 · <bdi dir="ltr">sda<\/bdi> · <bdi dir="ltr">\d\d\/\d\d\/\d{4} 09:40<\/bdi>.*<td>תיקיית Office<\/td>.*<span class="st warn">ממתין שהמחשב יעלה ב-PXE<\/span>/,'#968: the target column is the capture folder from /tasks');
  assert.match(tasks,/העברה לשרת המשנה חיפה — Ubuntu 24.04.*bar err.*--w:38%.*<span class="st err">נכשל — שרת המשנה לא ענה<\/span>/);
});
test('#1300: a cloning round shows written/target drives and the rate; unmeasured rate is not shown',async()=>{
  const {run}=await loaded();
  // 17/10: the rate is round.rate_bps (the last 10 s, the GUI's number), not udp-sender's throughput_bps
  run('OVERVIEW={...OVERVIEW,session:null,room:{state:"active",image_name:"Office 2024",target_drives:4,written_drives:1,wave_state:"running",target_slots:[{mac:"a"},{mac:"b"}],rate_bps:111149056,throughput_bps:53687091}}');
  let card=run('homeRoundCard()');
  assert.match(card,/<b>Office 2024<\/b><span class="pct">25%<\/span>/);
  assert.match(card,/2 מחשבים · 1\/4 דיסקים נכתבו · <bdi dir="ltr">106 MB\/s<\/bdi>/);
  assert.match(card,/selectPageById\('deploy'\)">לחדר המשכפלים/);
  run('OVERVIEW.room.rate_bps=null');
  assert.doesNotMatch(run('homeRoundCard()'),/MB\/s/);
  run('OVERVIEW={...OVERVIEW,room:null}');
  assert.match(run('homeRoundCard()'),/אין סבב פעיל.*selectPageById\('deploy'\)">התחל סבב</,'empty = a start button');
});
test('17/10 (room_855f6a13): a live cloning round is one row — its wave is not a second round; one percent; machines by target_slots',async()=>{
  const {run}=await loaded();
  run('OVERVIEW={...OVERVIEW,session:{id:"ses_51a29f5a",state:"running",image_name:"test2",group_label:"מחשבי שיכפול",group_id:"grp_CLONERS",joined:1,expected_clients:3,progress:19},room:{state:"active",wave_session_id:"ses_51a29f5a",image_name:"test2",target_drives:2,written_drives:0,wave_state:"running",target_slots:[{mac:"78:ac:c0:9b:11:c2",ports:[2,3]}],rate_bps:53687091}}');
  const card=run('homeRoundCard()');
  assert.equal((card.match(/<div class="round">/g)||[]).length,1,'one round, one block');
  assert.match(card,/<b>test2<\/b><span class="pct">19%<\/span>/,'the wave percent while it runs — not 0% and 19% side by side');
  assert.match(card,/פעיל · 1 מחשבים · 0\/2 דיסקים נכתבו · <bdi dir="ltr">51.2 MB\/s<\/bdi>/);
  assert.doesNotMatch(card,/1\/3 מחשבים/,'never the whole group');
  const now=run('homeNowRows(true)').map(r=>r.join('|')).join(' // ');
  assert.doesNotMatch(now,/סבב הפצה — test2/); assert.match(now,/סבב שיכפול — test2.*1 מחשבים · 2 דיסקים.*--w:19%/);
  run('OVERVIEW.room.wave_session_id="ses_other"');
  assert.equal((run('homeRoundCard()').match(/<div class="round">/g)||[]).length,2,'a different session is still shown');
});
test('"needs attention" lists the unregistered machine, the unreachable branch and the health warning — each with an action button (Nadav 18/10: no disk)',async()=>{
  const {run}=await loaded();
  run('SECONDARY_STATUS={haifa:{connected:false,error:"timeout"}}');
  const html=run('home(0)');
  const card=html.slice(html.indexOf('id="home-attn"'),html.indexOf('</ul>',html.indexOf('id="home-attn"')));
  assert.match(card,/<summary>.*דורש טיפול<\/span><span class="cnt">3<\/span><\/summary>/,'a row with a counter');
  assert.match(html,/id="home-attn" open/,'#1300: open by default (console-full.html)');
  assert.ok(html.indexOf('id="home-attn"')<html.indexOf('<div class="kpi '),'the row sits between the header and the numbers');
  // ‏#1300: הפעולה — כפתור בעמודה נפרדת בסוף השורה, לא קישור בתוך הטקסט
  const btn=(label,fn)=>new RegExp(`<\\/div><button class="btn sm flat" onclick="${fn}">${label}<\\/button><\\/li>`);
  assert.doesNotMatch(card,/S5Y2NX0R|נכשל בשיכפול הקודם|נקה אחרי החלפה/,'Nadav 18/10: the failed disk is not here');
  assert.match(card,/<b>מכונה לא רשומה<\/b> ברשת ההפצה — <span class="mono">de:ad:be:ef:00:01<\/span> קיבלה <bdi dir="ltr">10.44.12.199<\/bdi>/);
  assert.match(card,btn('רשום','openNewMachine\\(\\)'));
  // טקסט גולמי באנגלית (שגיאת מערכת מהשרת) לא בגוף הפריט — מאחורי "פרטים טכניים"
  assert.match(card,/<b>שרת משנה חיפה לא מגיב<\/b>\.<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">timeout<\/code><\/details><\/div><button class="btn sm flat" onclick="selectPageById\('branches'\)">פרטי שרת המשנה<\/button>/);
  assert.match(card,/<b>מולטיקאסט<\/b>\.<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">udp-sender לא נמצא<\/code><\/details><\/div><button class="btn sm flat" onclick="selectPageById\('health'\)">לבריאות<\/button>/);
  assert.doesNotMatch(card,/<div class="b">[^<]*<a /,'no inline link inside the item text');
  assert.doesNotMatch(card.replace(/<details class="tech">.*?<\/details>/g,''),/timeout|udp-sender/,'no raw English outside the technical details');
  assert.doesNotMatch(card,/השרת בכתובת ההפצה/,'ok checks are not attention items');
});
test('events come from /journal (dd/mm for older days, severity from the server) and link to the journal — on the events tab; no recent-images card on the summary (#1300)',async()=>{
  const {run,requests}=await loaded();
  assert.ok(requests.some(r=>r.url==='/api/console/journal?limit=50'));
  const summary=run('home(0)');
  assert.doesNotMatch(summary,/class="timeline"/,'console-full.html P.home: the summary has no event list');
  assert.doesNotMatch(summary,/אימג&#39;ים אחרונים|openImageDetail/,'#1300 (Nadav 12/10): "recent images" is gone');
  const html=run('layout(pages.home,2)');
  assert.match(html,/<span class="t">09:41<\/span><span class="d info"><\/span><span>סבב הפצה נפתח לכיתה 303<\/span><span class="who">nadav<\/span>/);
  assert.match(html,/<span class="t">16\/09\/2026<\/span><span class="d err"><\/span><span>העברה נכשלה<\/span>/);
  assert.match(html,/selectPageById\('logs'\)">ליומן המלא/);
});
test('#1300: the "services" card — four short tiles (one word each), detail in the tile title; no SSH tile (#1266)',async()=>{
  const {run}=await loaded();
  const html=run('home(0)');
  const card=html.slice(html.indexOf('<span>שירותים'),html.indexOf('<span>סניפים'));
  const tile=(n,cls,word,title)=>new RegExp('<div class="tile" title="'+title+'"><span class="led '+cls+'"></span><div><div class="tt">'+n+'</div><small>'+word+'</small>');
  assert.match(card,/<div class="tiles">/);
  assert.equal((card.match(/<div class="tile"/g)||[]).length,4);
  assert.match(card,tile('שרת','ok','פעיל','תקין · 8081'));
  assert.match(card,tile('פורט TFTP','ok','פעיל','תקין · 69'));
  assert.match(card,tile('מולטיקאסט','warn','דורש בדיקה','אזהרה · 9000–9001'));
  assert.match(card,tile('DHCP הפצה','','לא נבדק','לא נקרא'),'DHCP without net.js is "not checked", not off');
  assert.doesNotMatch(card,/SSH/,'#1266: SSH is not part of the product');
  assert.match(card,/selectPageById\('health'\)">לבריאות</);
});
test('#1300: services — an unread /ports is grey "not checked", never green',async()=>{
  const s=setup(); s.fixtures['/ports']=new Error('ports down');
  s.run('DISK_FAILURES=[];SHRINK_RECORDS=[];toast=()=>{}');
  await s.run('loadHome()');
  const card=s.run('homeServerCard()');
  assert.match(card,/<div class="tile" title="לא נקרא: ports down"><span class="led "><\/span><div><div class="tt">שרת<\/div><small>לא נבדק<\/small>/);
  assert.doesNotMatch(card,/led ok/);
});
test('tabs: "משימות" is the full 20-task table, "אירועים" is 50 events; activateTab keeps the header',async()=>{
  const {run}=await loaded();
  const tasks=run('layout(pages.home,1)');
  assert.match(tasks,/class="tab on" role="tab" aria-selected="true" tabindex="0" onclick="activateTab\(1\)">משימות/);
  assert.match(tasks,/קליטת אימג&#39; — Kali.*<span class="st ok">הושלם<\/span>/,'the history tab shows finished captures');
  assert.doesNotMatch(tasks,/class="kpi/);
  const events=run('layout(pages.home,2)');
  assert.match(events,/>אירועים <small>50 האחרונים<\/small>/); assert.match(events,/העברה נכשלה/); balanced(events);
});
test('loadHome reads every source once and re-renders; the summary polls only /overview and /tasks',async()=>{
  const {requests,node}=await loaded();
  const urls=requests.map(r=>r.url.replace('/api/console',''));
  for(const u of ['/overview','/tasks','/net','/ports','/health','/journal?limit=50','/monitor/machines','/storage-nodes','/storage-transfers']) assert.ok(urls.includes(u),u);
  assert.ok(!urls.includes('/images'),'#1300: no recent-images card — the overview does not read the library');
  assert.ok(!urls.includes('/update'),'#1300: the update tile is gone from the overview — /update is read on the settings page');
  assert.ok(!urls.includes('/ssh'),'#1266: no SSH endpoint');
  assert.match(node('#content').innerHTML,/class="kpi/);
});

test('the 2-second re-render keeps the scroll position of the page',async()=>{
  const {run,node}=await loaded();
  // הגולל המזויף: הצבת innerHTML ב-#content "מאפסת" אותו כמו דפדפן אמיתי
  const scroller=node('#content .page, #content .scroll'); scroller.scrollTop=420;
  const content=node('#content'); let html=content.innerHTML;
  Object.defineProperty(content,'innerHTML',{get(){return html;},set(v){html=v;scroller.scrollTop=0;}});
  run('renderCurrent()');
  assert.equal(scroller.scrollTop,420);
  assert.match(html,/class="kpi/);
});

// ---- מצבים ריקים ולא-נקראים ---------------------------------------------------

test('empty server: KPIs say 0 in their colours, the round card offers to start one, attention is an honest empty',async()=>{
  const s=setup();
  Object.assign(s.fixtures,{'/overview':{images:0,machines:0,storage:null,session:null,room:null,pulls:[]},'/tasks':[],'/images':[],'/net':[],'/health':[],'/journal':[],'/storage-nodes':[],'/storage-transfers':[],'/disk-failures':[],'/monitor/machines':[]});
  s.run('IMAGES=[];FOLDERS=[];DISK_FAILURES=[];SHRINK_RECORDS=[]');
  await s.run('loadHome()');
  const html=s.run('home(0)');
  assert.match(html,/<div class="kpi kpi-store "><div class="v">לא ידוע<\/div>/,'storage:null is unknown, not 0');
  assert.match(html,/<div class="kpi "><div class="l">מחשבים מחוברים<\/div><div class="v"><bdi dir="auto">\u20660 \/ 0\u2069<\/bdi>/);
  assert.match(html,/<div class="empty"><b class="et">אין סבב פעיל<\/b>/);
  assert.match(html,/<div class="c12 attn-none">אין פריטים לטיפול<\/div>/,'a clean server says so (no folded row, no counter)');
  assert.doesNotMatch(html,/id="home-attn"/);
  assert.match(s.run('layout(pages.home,2)'),/<div class="empty">אין אירועים עדיין<\/div>/);
  assert.doesNotMatch(html,/<span>סניפים<\/span>/,'#1300: no branches configured = no branches card at all');
  assert.match(html,/<span class="pill ok">שרת פעיל<\/span>/,'#1300: all checks ok = "server running" (console-full.html)');
  balanced(html);
});
test('a source that could not be read is shown as unread — never as green or as "nothing to do" (principle 5)',async()=>{
  const s=setup();
  s.fixtures['/health']=new Error('offline'); s.fixtures['/net']=new Error('net down'); s.fixtures['/journal']=new Error('no journal');
  s.run('DISK_FAILURES=[];SHRINK_RECORDS=[];HEALTH=null;toast=()=>{}');
  await s.run('loadHome()');
  const html=s.run('home(0)');
  assert.match(html,/<span class="pill ">לא נבדק<\/span>/);
  assert.match(html,/לא נקראו: בריאות, רשת — לא ניתן לומר שאין מה לטפל/);
  assert.doesNotMatch(html,/אין פריטים לטיפול/);
  assert.match(s.run('layout(pages.home,2)'),/היומן לא נקרא: no journal/,'the events tab says it is unread');
  assert.doesNotMatch(html,/class="kpi ok"><div class="l">בריאות/);
});
test('a failed /overview read marks the page stale instead of pretending',async()=>{
  const s=setup(); s.fixtures['/overview']=new Error('offline'); s.run('renderActivity=()=>{}');
  await s.run('refreshStatus()');
  assert.match(s.run('home(0)'),/<div class="note warn">.*מצב השרת לא נקרא/);
});

// ---- תפקיד deploy --------------------------------------------------------------

test('deploy sees the summary without attention, events and services; two tabs; no red-disk KPI; the round card is full width',async()=>{
  const {run,requests}=await loaded('deploy');
  const urls=requests.map(r=>r.url.replace('/api/console',''));
  for(const u of ['/health','/journal?limit=50','/monitor/machines']) assert.ok(!urls.includes(u),u+' must not be requested by deploy');
  const html=run('layout(pages.home,0)');
  assert.equal((html.match(/role="tab"/g)||[]).length,2);
  assert.doesNotMatch(html,/דורש טיפול|אירועים אחרונים|>שירותים<|openCapture|דיסק אדום/);
  assert.equal((html.match(/<div class="kpi /g)||[]).length,3,'images · machines · storage');
  assert.doesNotMatch(html,/<span>סניפים<\/span>/,'deploy cannot list branches — no card');
  assert.match(html,/<div class="kpi "><div class="l">מחשבים רשומים<\/div>/,'deploy cannot read /monitor/machines — no connected count');
  assert.match(html,/<div class="c12 card"><div class="card-h"><span>סבב פעיל/,'deploy has no services card beside it — full width');
  assert.match(html,/<span class="pill info">פתוח<\/span>/);
  assert.match(run('layout(pages.home,2)'),/class="kpi/,'an events tab index falls back to the summary for deploy');
});
test('the images tree node is the folder icon (Nadav, 17/09); its sub-folders carry a smaller thinner variant',()=>{
  const {run,node}=setup();
  const index=fs.readFileSync(path.join(root,'index.html'),'utf8');
  const start=index.indexOf('data-page="images"');
  const imagesNode=index.slice(start,index.indexOf("אימג'ים</span>",start));
  assert.match(imagesNode,/<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"\/>/);
  assert.doesNotMatch(imagesNode,/m12 3 9 5-9 5-9-5Z/,'the layers icon is gone');
  assert.doesNotMatch(imagesNode,/circle cx="16"/,'the picture icon is gone from the images node');
  run('FOLDERS=[{name:"Linux"},{name:"Empty"}]; IMAGES=[{id:"i1",name:"Kali",folder:"Linux"}]; populateSidebarImages()');
  const tree=node('#imagesTree').innerHTML;
  assert.equal((tree.match(/M3 7a2 2 0 0 1 2-2h4l2 2h8/g)||[]).length,0,'no sub-folder repeats the images-node icon');
  assert.equal((tree.match(/stroke-width="1.2"/g)||[]).length,2,'both sub-folders carry the thin variant');
  assert.equal((tree.match(/circle cx="16"/g)||[]).length,1,'the image inside keeps the picture icon');
});

// apple-pending-4 · 4: כותרת הסקירה — שורה אחת, שורת משנה אחת, האזהרות מקופלות
test('overview header: compact (one row + one ellipsis sub-line with the full text in title); the attention row keeps its state across the 2s refresh',async()=>{
  const {run}=await loaded();
  const html=run('home(0)');
  assert.match(html,/<div class="obj compact">/);
  assert.match(html,/<div class="obj-sub" title="6 אימג&#39;ים · 12 מחשבים \(1 מחוברים\) · עודכן 09:45">/);
  assert.doesNotMatch(run('UI.objHeader({name:"x",sub:"y"})'),/compact/,'other pages keep the non-compact header');
  assert.match(html,/ id="home-attn"[^>]* open/);
  run('HOME_ATTN_OPEN=false');
  assert.doesNotMatch(run('home(0)'),/id="home-attn"[^>]* open/,'closed stays closed after a re-render');
  run('HOME_ATTN_OPEN=true');
});
test('raw technical text goes to "technical details"; a Hebrew sentence that mentions SATA stays in the body',()=>{
  const {run}=setup();
  assert.equal(run('isRawTechnical("Server status could not be read: [Errno 2] No such file")'),true);
  assert.equal(run('isRawTechnical("leases file /var/lib/misc/dnsmasq.leases not readable")'),true);
  assert.equal(run('isRawTechnical("udp-sender לא נמצא")'),true);
  assert.equal(run('isRawTechnical("כבל/חריץ SATA 2")'),false);
  assert.equal(run('isRawTechnical("")'),false);
  assert.equal(run('isRawTechnical(null)'),false);
});

// apple-pending-4 · דשבורדים: UI.noteTech — שגיאת מערכת גולמית מאחורי "פרטים טכניים", משפט עברי נשאר בגוף
test('UI.noteTech: raw Latin error text goes behind "technical details"; a Hebrew-dominant detail stays inline; no raw text = no details',()=>{
  const {run}=setup();
  const raw=run(`UI.noteTech("warn","‏/net לא נקרא","db locked"," — אינם ידועים")`);
  assert.match(raw,/^<div class="note warn"><span aria-hidden="true">●<\/span><span>‏\/net לא נקרא — אינם ידועים<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">db locked<\/code><\/details><\/span><\/div>$/);
  assert.doesNotMatch(raw.replace(/<details.*<\/details>/,''),/db locked/,'the raw text is not in the body');
  const heb=run(`UI.noteTech("err","‏/ports לא נקרא","ss חסר"," — לא ידוע")`);
  assert.match(heb,/‏\/ports לא נקרא: ss חסר — לא ידוע/); assert.doesNotMatch(heb,/details/);
  const none=run(`UI.noteTech("warn","לא נקרא","","!")`);
  assert.match(none,/<span>לא נקרא!<\/span>/); assert.doesNotMatch(none,/details|: /);
  assert.match(run(`UI.noteTech("warn","a","<script>alert(1)</script>")`),/&lt;script&gt;/,'escaped');
});

// ---- נדב 18/10: אין כרטיס "שרתי משנה" בסקירה ("יש בשביל זה את הלשוניות") — ההתראות נשארות ----
test('Nadav 18/10: the overview has no secondary-servers card, with secondaries configured', async () => {
  const s=await loaded();
  s.run(`HOME.nodes=[{id:'haifa',label:'חיפה',disabled_at:null,last_seen_at:'${today}T09:44:00Z'}]`);
  s.run('SECONDARY_STATUS={haifa:{connected:true}}');
  const html=s.run('home(0)');
  assert.doesNotMatch(html,/<span>שרתי משנה<\/span>/,'no card titled שרתי משנה');
  assert.doesNotMatch(html,/<div class="tt">חיפה<\/div>/,'no tile per secondary');
  assert.doesNotMatch(html,/>לשרתי המשנה</,'no "to the secondaries" card button');
  assert.equal(s.run('typeof homeBranchesCard'),'undefined');
  balanced(html);
});
test('Nadav 18/10: without the card the alerts stay -- a silent secondary and an unread list are in "דורש טיפול"', async () => {
  const s=await loaded();
  s.run(`HOME.nodes=[{id:'haifa',label:'חיפה',disabled_at:null}]`);
  s.run('SECONDARY_STATUS={haifa:{connected:false,error:"timeout"}}');
  assert.match(s.run('homeAttention()'),/<b>שרת משנה חיפה לא מגיב<\/b>/);
  s.run('HOME.nodes=null;HOME.err.nodes="db locked";SECONDARY_STATUS={}');
  const att=s.run('homeAttention()');
  assert.match(att,/<b>רשימת שרתי המשנה לא נקראה<\/b> — לא ידוע אם הם עונים\./,'said, not hidden, even with nothing else to handle');
  assert.match(att,/<code dir="ltr">db locked<\/code>/,'the raw reason in the technical details');
  assert.match(att,/selectPageById\('branches'\)">לשרתי המשנה</);
});
test('Nadav 18/10: the alert bell counts a secondary that did not answer, not one that did', async () => {
  const s=await loaded();
  const badge=s.node('#alertBadge'); badge.removeAttribute=()=>{}; badge.setAttribute=()=>{};
  s.run('MACHINES=[];HEALTH=[];SECONDARY_STATUS={haifa:{connected:true}}');
  s.run('updateAlertBadge()'); assert.equal(badge.textContent,'');
  s.run('SECONDARY_STATUS={haifa:{connected:false,error:"timeout"},bs:{connected:true}}');
  s.run('updateAlertBadge()'); assert.equal(badge.textContent,'1');
});
test('#1300: the task dock ("what is happening now") opens by itself when activity starts — once, not against a manual close',async()=>{
  const s=setup();
  let open=false, toggles=0;
  s.node('#taskPanel').classList={contains:(c)=>c==='open'&&open,toggle(){},add(){},remove(){}};
  s.run('DISK_FAILURES=[];SHRINK_RECORDS=[];toast=()=>{}');
  s.run('(f)=>{toggleTaskPanel=f}')(()=>{toggles++;open=!open;});
  s.run('OVERVIEW={images:1,machines:1,storage:null,session:null,room:null,pulls:[]};CAPTURE_TASKS=[];HOME_DOCK_HAD=false');
  s.run('home(0)'); assert.equal(toggles,0,'no activity = the dock stays closed');
  s.run('OVERVIEW.session={id:"s1",image_name:"Office",state:"open",joined:0,expected_clients:2,members:[]}');
  s.run('home(0)'); assert.equal(toggles,1,'activity starts = the dock opens');
  s.run('home(0)'); assert.equal(toggles,1,'already open = untouched');
  open=false; s.run('home(0)'); assert.equal(toggles,1,'closed by the operator mid-activity = stays closed');
  s.run('OVERVIEW.session=null'); s.run('home(0)');
  s.run('OVERVIEW.session={id:"s2",image_name:"Office",state:"open",joined:0,expected_clients:2,members:[]}'); s.run('home(0)');
  assert.equal(toggles,2,'a new activity opens it again');
});

test('#1300: an active cloner round and inter-site transfers are dock activity too — they open it by themselves; done/failed ones do not',async()=>{
  const s=setup();
  s.run('ME.role="admin";CAPTURE_TASKS=[];HOME.transfers=null');
  s.run('OVERVIEW={images:1,machines:1,storage:null,session:null,pulls:[],room:{state:"active",image_name:"Office 2024",target_drives:4,written_drives:1,wave_state:"running",opened_by:"nadav"}}');
  let rows=s.run('activityRows().rows');
  assert.equal(rows.length,1,'the round is a row');
  assert.equal(rows[0].name,'Office 2024'); assert.equal(rows[0].target,'1/4 דיסקים'); assert.equal(rows[0].pct,25); assert.equal(rows[0].by,'nadav');
  s.run('OVERVIEW.room.state="failed"'); assert.equal(s.run('activityRows().rows.length'),0,'a failed round is not activity');
  s.run('OVERVIEW.room=null');
  s.run(`HOME.transfers=[{id:'a',node_label:'חיפה',image_name:'Linux',state:'sending',bytes_sent:25,bytes_total:100,started_by:'nadav',created_at:'${today}T09:00:00Z'},{id:'b',node_label:'חיפה',image_name:'Old',state:'done'},{id:'c',node_label:'חיפה',image_name:'Bad',state:'failed'},{id:'d',node_label:'באר שבע',image_name:'Q',state:'queued'}]`);
  rows=s.run('activityRows().rows');
  assert.deepEqual(JSON.parse(JSON.stringify(rows.map(r=>[r.name,r.target,r.label,r.pct]))),[['Linux','חיפה','שולח',25],['Q','באר שבע','ממתין בתור',null]]);
  // הדוק נפתח מעצמו גם להן — ולא נגד סגירה ידנית
  let open=false, toggles=0;
  s.node('#taskPanel').classList={contains:(c)=>c==='open'&&open,toggle(){},add(){},remove(){}};
  s.run('(f)=>{toggleTaskPanel=f}')(()=>{toggles++;open=!open;});
  s.run('DISK_FAILURES=[];SHRINK_RECORDS=[];HOME.transfers=[];HOME_DOCK_HAD=false');
  s.run('home(0)'); assert.equal(toggles,0);
  s.run(`HOME.transfers=[{id:'a',node_label:'חיפה',image_name:'Linux',state:'sending',bytes_sent:1,bytes_total:100}]`);
  s.run('home(0)'); assert.equal(toggles,1,'a transfer opens the dock');
  open=false; s.run('home(0)'); assert.equal(toggles,1,'closed mid-transfer = stays closed');
  s.run('HOME.transfers=[]'); s.run('home(0)');
  s.run('OVERVIEW.room={state:"active",image_name:"X",target_drives:2,written_drives:0,wave_state:"running"}');
  s.run('home(0)'); assert.equal(toggles,2,'a cloner round opens it');
});
test('Nadav 18/10: no disk information in "needs attention" — not a failed write (red or orange), not NVMe SMART, not "נקה אחרי החלפה"; the machines page and the machine drawer still show it and clear it',async()=>{
  const {run}=await loaded();
  run('SECONDARY_STATUS={}; HOME.net=[]; HEALTH=[]');
  run('DISK_FAILURES='+JSON.stringify([{id:2,mac:'cc:cc:cc:cc:cc:cc',port:3,disk_number:3,serial:'S6PN',cause:'readonly'},
                                        {id:3,mac:'cc:cc:cc:cc:cc:cc',port:1,disk_number:1,serial:'S1X',cause:'cable'}]));
  run(`MACHINES[0].probe_verdicts=[{key:'nvme:nvme0n1',level:'err',text_he:'NVMe nvme0n1: 2 שגיאות מדיה'},{key:'battery',level:'err',text_he:'המחשב על סוללה'}]`);
  const html=run('home(0)');
  const card=html.slice(html.indexOf('id="home-attn"'),html.indexOf('</ul>',html.indexOf('id="home-attn"')));
  assert.doesNotMatch(html,/S6PN|S1X|נכשל בשיכפול הקודם|נקה אחרי החלפה|קריאה בלבד|NVMe/,'not a word about disks on the overview');
  assert.match(card,/<span class="cnt">1<\/span>/,'only the battery verdict -- a machine item that is not a disk stays');
  assert.match(card,/המחשב על סוללה/);
  assert.match(run('machinesTabs().join("|")'),/דיסקים אדומים \(2\)/);
  const tab=run('diskFailuresCard()');   // the body of that tab
  assert.match(tab,/clearDiskFailure\(2\)/); assert.match(tab,/clearDiskFailure\(3\)/);
  const drawer=run('machineDrawerHtml(MACHINES[0])');
  assert.match(drawer,/clearDiskFailure\(2\)">נקה אחרי החלפה/,'cleared from the machine drawer');
  assert.match(drawer,/clearDiskFailure\(3\)">נקה אחרי החלפה/);
});
