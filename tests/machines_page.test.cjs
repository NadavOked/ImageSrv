// ‏#954 גל 3: דף המחשבים שנבנה מחדש לפי docs/design/console-redesign/machines.md
// (+ class.md, machine-drawer.md) — טבלה אחת מקובצת (בנייה, שיכפול, ואז כיתה
// לכל קבוצה — תיקון נדב 17/09), סינון, בחירה→פס פעולות, מגירת מחשב, הכיתה
// כאובייקט, לשוניות "נראו ברשת"/"דיסקים אדומים". בלי דפדפן: console.js + net.js
// רצים ב-vm מול DOM מזערי ו-fetch מזויף, והפלט נבדק כטקסט.
// עיקרון 5: disks null/[]/רשימה הם שלושה מצבים; "לא נקרא" ≠ ריק; "לא נבדק" לא ירוק.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const today=new Date().toISOString().slice(0,10);
const minutesAgo=(n)=>new Date(Date.now()-n*60000).toISOString();

function setup() {
  const nodes=new Map(), requests=[], opened=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const fixtures={
    '/groups':[
      {id:'grp_BUILD',label:'מחשבי בנייה',role:'build',sort:1,machines:2},
      {id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:2,machines:2},
      {id:'grp_LAB303',label:'כיתה 303 — סייבר',role:'classroom',sort:3,machines:3},
      {id:'grp_LAB305',label:'כיתה 305 — משרד',role:'classroom',sort:4,machines:1}],
    '/machines':[
      {mac:'c8:d9:d2:0b:fe:32',suffix:'בנייה 1',group_id:'grp_BUILD',note:null,drawer_count:null,prompt:'menu',disks:[{dev:'sda',size_bytes:256060514304,model:'870 EVO',serial:'S5Y30',port:1,smart:'ok'}],disks_reported_at:minutesAgo(180),
        inventory:{dmi:{sys_vendor:'LENOVO',product_name:'10SQS0AK00',product_version:'ThinkCentre M720q',board_name:'3132'},pci:['8086:15bc:020000','8086:a352:010601'],tpm:{present:true,version:'2.0'}},inventory_seen_at:minutesAgo(180)},
      {mac:'c8:d9:d2:0b:fe:34',suffix:'בנייה 3',group_id:'grp_BUILD',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null},
      {mac:'a0:48:1c:8a:18:40',suffix:'מחשב 1',group_id:'grp_CLONERS',note:null,drawer_count:2,prompt:null,disks:[],disks_reported_at:minutesAgo(9),inventory:null,inventory_seen_at:null},
      {mac:'78:ac:c0:9b:11:c2',suffix:'מחשב 2',group_id:'grp_CLONERS',note:'ליד הדלת',drawer_count:3,prompt:null,disks_reported_at:minutesAgo(9),inventory:{dmi:{sys_vendor:'LENOVO',product_name:'M720q'},pci:[],tpm:null},inventory_seen_at:minutesAgo(9),
        disks:[{dev:'sda',size_bytes:256060514304,model:'870 EVO',serial:'S5Y30',port:1,smart:'ok'},{dev:'sdb',size_bytes:256060514304,model:'870 EVO',serial:'S5Y31',port:2},{dev:'sdc',size_bytes:256060514304,model:'870 EVO',serial:'S5Y2NX0R12345',port:3,smart:'ok'}]},
      {mac:'b4:2e:99:07:1a:c1',suffix:'01',group_id:'grp_LAB303',note:null,drawer_count:null,prompt:null,disks:[{dev:'sda',size_bytes:256060514304,model:'870 EVO',serial:'L1'}],disks_reported_at:minutesAgo(1),inventory:null,inventory_seen_at:null},
      {mac:'b4:2e:99:07:1a:c2',suffix:'02',group_id:'grp_LAB303',note:null,drawer_count:null,prompt:null,disks:[{dev:'sda',size_bytes:256060514304,model:'870 EVO',serial:'L2'}],disks_reported_at:minutesAgo(1),inventory:null,inventory_seen_at:null},
      {mac:'b4:2e:99:07:1a:c3',suffix:'03',group_id:'grp_LAB303',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null},
      {mac:'b4:2e:99:07:1b:c1',suffix:'INS',group_id:'grp_LAB305',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null}],
    '/net':[
      {mac:'c8:d9:d2:0b:fe:32',ip:'10.44.12.20',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(180),registered:true,name:'בנייה 1',group_id:'grp_BUILD',group_label:'מחשבי בנייה',role:'build',boot:{step:'agent-hello',label:'לפני ה-hello הראשון',index:9,total:9,seconds:10800,next_step:null,next_label:'hello לשרת',stalled:false}},
      {mac:'a0:48:1c:8a:18:40',ip:'10.44.12.118',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(9),registered:true,name:'מחשב 1',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',boot:null},
      {mac:'78:ac:c0:9b:11:c2',ip:'10.44.12.59',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(9),registered:true,name:'מחשב 2',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',boot:{step:'entry',label:'GRUB נכנס לערך ImageSrv',index:2,total:9,seconds:540,next_step:'http-ok',next_label:'מודול ה-HTTP נטען',stalled:true}},
      {mac:'b4:2e:99:07:1a:c1',ip:'10.44.12.101',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(1),registered:true,name:'01',group_id:'grp_LAB303',group_label:'כיתה 303 — סייבר',role:'classroom',boot:null},
      {mac:'b4:2e:99:07:1a:c2',ip:'10.44.12.102',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(1),registered:true,name:'02',group_id:'grp_LAB303',group_label:'כיתה 303 — סייבר',role:'classroom',boot:null},
      {mac:'de:ad:be:ef:00:01',ip:'10.44.12.187',description:'מדפסת?',first_seen:today+'T07:00:00Z',last_seen:minutesAgo(30),registered:false,name:null,group_id:null,group_label:null,role:null,boot:{step:'menu',label:'תפריט האתחול נמסר',index:1,total:9,seconds:1800,next_step:'entry',next_label:'GRUB נכנס לערך ImageSrv',stalled:true}}],
    '/disk-failures':[{id:7,session_id:'r15',mac:'78:ac:c0:9b:11:c2',dev:'sdc',serial:'S5Y2NX0R12345',port:3,disk_number:3,ata_port:'ata3',at:'2026-09-15T10:00:00Z',cause:'cable',error:'ATA timeout',ata_log:['ata3.00: exception Emask','ata3: hard resetting link','ata3.00: failed command: WRITE FPDMA QUEUED']}],
    '/shrink-records':[],
    '/monitor/machines':[
      {mac:'c8:d9:d2:0b:fe:32',name:'בנייה 1',role:'build',ip:'10.44.12.20',online:false,prompt:'menu'},
      {mac:'c8:d9:d2:0b:fe:34',name:'בנייה 3',role:'build',ip:null,online:false,prompt:null},
      {mac:'a0:48:1c:8a:18:40',name:'מחשב 1',role:'cloner',ip:'10.44.12.118',online:true,prompt:null},
      {mac:'78:ac:c0:9b:11:c2',name:'מחשב 2',role:'cloner',ip:'10.44.12.59',online:true,prompt:null}],
    '/overview':{images:4,machines:8,storage:null,pulls:[],room:null,
      session:{id:'s1',state:'open',kind:'multicast',roster:null,group_id:'grp_LAB303',group_label:'כיתה 303 — סייבר',group_role:'classroom',image_id:'img_7f3a91',image_name:'Office 2024 — סטנדרט',prefix:'LAB303',expected_clients:3,single:false,joined:2,stuck:{},starts_in_seconds:0,
        members:[{mac:'b4:2e:99:07:1a:c1',name:'01',hostname:'LAB303-01',state:'waiting',done:false,bytes_written:0,bytes_total:null,error:null,disks:[]},
                 {mac:'b4:2e:99:07:1a:c2',name:'02',hostname:'LAB303-02',state:'waiting',done:false,bytes_written:0,bytes_total:null,error:null,disks:[]}]}},
    '/journal':[{ts:'2026-09-15T10:00:00Z',user:'סוכן',event:'disk_failure',label:'דיסק נכשל בכתיבה',text:'מחשב 2 — דיסק 3 (S5Y2NX0R12345)',severity:'err'},
                {ts:'2026-09-12T09:00:00Z',user:'nadav',event:'machine_add',label:'מכונה נוספה',text:'מחשב 2',severity:'info'}],
    '/machines/import':{preview:[{raw:'b4:2e:99:07:1a:c4 04',mac:'b4:2e:99:07:1a:c4',suffix:'04',error:null},{raw:'zz 05',mac:null,suffix:null,error:'MAC לא תקין'}],saved:1,rejected:[],network:{applied:true}},
    '/room/wake':{sent:2,failed:0,reasons:[]},
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(...a){opened.push(a);}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      if(fixtures[key] instanceof Error) throw fixtures[key];
      if(fixtures[key]===403) return {status:403,ok:false,json:async()=>({detail:'admin only'})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] ?? {ok:true}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",version:"v0.31.1",capabilities:{classrooms:true}}; current="machines"; sheet = o => { globalThis.formOptions=o; }; confirmSheet=(t,s,l,fn)=>{ globalThis.confirmFn=fn; }; selectPageById=(id)=>{ globalThis.selected=id; current=id; };');
  for(const [name,key] of [['OVERVIEW','/overview'],['MACHINES','/machines'],['GROUPS','/groups'],['DISK_FAILURES','/disk-failures'],['SHRINK_RECORDS','/shrink-records'],['NET','/net'],['MONITOR_ROWS','/monitor/machines']]) run(name+'='+JSON.stringify(fixtures[key]));
  return {run,node,requests,fixtures,ctx,opened};
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
const between=(html,a,b)=>html.slice(html.indexOf(a),b?html.indexOf(b,html.indexOf(a)):undefined);

test('one grouped table: header counters + pills, three tabs, build → cloners → a group per class, rows joined with /net', () => {
  const {run}=setup(); const html=run('machines(0)'); balanced(html);
  assert.match(html,/class="page"/); assert.match(html,/obj-name">מחשבים</);
  // "בנייה 1" נראה לפני 3 שעות: בין 00:00 ל-03:00 UTC (isToday משווה תאריך ISO) זה עוד אתמול — נפל במעבדה 18/09 02:35 UTC.
  // ‏#1219: כל מכונה רשומה לפי הגיל שלה (180, 9, 9, 1, 1 דק׳) — התיקון הקודם כיסה רק את 180,
  // ושתי המכונות של "לפני 9 דק׳" נפלו בין 00:00 ל-00:09 UTC (שער 0924b).
  const seenToday = [180, 9, 9, 1, 1].filter((n) => new Date(Date.now()-n*60000).toISOString().slice(0,10)===today).length;
  assert.match(html,new RegExp(`8 רשומים · 2 כיתות · 2 מחשבי בנייה · 2 משכפלים · ${seenToday} נראו ברשת היום`));
  assert.match(html,/pill warn">1 דיסק נכשל בשיכפול הקודם</);   // #1580: orange assert.match(html,/pill warn">1 לא רשום</);
  for(const t of ['כל המחשבים','נראו ברשת \\(1\\)','דיסקים אדומים \\(1\\)']) assert.match(html,new RegExp('role="tab"[^>]*>'+t+'<'));
  assert.match(html,/onclick="openAddMachine\(\{\}\)">\+ מחשב</); assert.match(html,/addGroupSheet\(\)">\+ כיתה</); assert.match(html,/href="\/api\/console\/machines.csv" download>ייצוא CSV</);
  assert.equal((html.match(/<table class="dg kebab-col"/g)||[]).length,1,'one table, not tabs per role');
  const groups=[...html.matchAll(/<tr class="group" data-group="([^"]*)"/g)].map(m=>m[1]);
  assert.deepEqual(groups,['grp_BUILD','grp_CLONERS','grp_LAB303','grp_LAB305'],'fixed groups first, then a collapsible group per class');
  assert.match(html,/openClass\('grp_LAB303'\)">כיתה 303 — סייבר</,'the class name links to the class object');
  assert.match(html,/כיתה · 3 מחשבים · קידומת LAB303/,'prefix only from the live session (no group field)');
  assert.match(html,/st run">סבב פתוח — Office 2024 — סטנדרט, 2\/3</);
  assert.match(html,/קבוצה קבועה · 2 מחשבים<\/span><span class="st ok">2 מחוברים</,'cloners: connected count from /monitor/machines');
  assert.match(html,/openClass\('grp_LAB303'\)">פתח כיתה</); assert.match(html,/openAddMachine\(\{group:'grp_LAB303'\}\)">\+ מחשב לכיתה</); assert.match(html,/wakeRoom\(\)">WoL לחדר</);
  for(const col of ['שם','IP אחרון','דיסקים','מצב']) assert.match(html,new RegExp('<th>'+col+'</th>'));
  for(const col of ['MAC','נראה לאחרונה']) assert.match(html,new RegExp('<th class="c-opt">'+col+'</th>'),'stage 2: secondary columns drop out first in a narrow card');
  assert.match(html,/<th><span class="sr">פעולות<\/span><\/th>/);
  const row=html.slice(html.indexOf('data-mac=')); const menu=JSON.parse(row.match(/data-menu="([^"]*)"/)[1].replaceAll('&quot;','"').replaceAll('&#39;',"'"));
  assert.ok(menu.map(i=>i[0]).includes('פרטים')&&menu.some(i=>i[2]==='danger'&&/removeMachine/.test(i[1])),'stage 2: one ⋯ menu per machine row; removal is last, in danger, and still opens the type-the-name sheet');
  const b1=between(html,'data-mac="c8:d9:d2:0b:fe:32"','data-mac="c8:d9:d2:0b:fe:34"');
  assert.match(b1,/openMachineDetail\('c8%3Ad9%3Ad2%3A0b%3Afe%3A32'\)">בנייה 1<\/a><span class="sub">LENOVO ThinkCentre M720q · TPM 2.0</,'model from inventory.dmi');
  assert.match(b1,/<span class="mono">c8:d9:d2:0b:fe:32<\/span>/); assert.match(b1,/<span class="mono">10.44.12.20<\/span>/); assert.match(b1,/לפני 3 שע&#39;/);
  assert.match(b1,/st warn">ממתין למפעיל: תפריט</,'prompt beats everything');
  assert.doesNotMatch(b1,/monitorMachine\(/,'monitor lives only on the monitor page (Nadav 17/09: "במקום החדש שביקשתי ורק שם")');
  const c2=between(html,'data-mac="78:ac:c0:9b:11:c2"','data-mac="b4:2e:99:07:1a:c1"');
  assert.match(c2,/3 חריצים/,'#1485: the slot count of a cloner (by its mapping)'); assert.match(c2,/3 · <bdi dir="ltr">256GB<\/bdi> <span class="pill warn">דיסק 3 נכשל בשיכפול הקודם</);   // #1580: orange
  assert.doesNotMatch(c2,/sd[abc]/,'never sd*'); assert.match(c2,/st ok">מחובר</);
  const l1=between(html,'data-mac="b4:2e:99:07:1a:c1"','data-mac="b4:2e:99:07:1a:c2"');
  assert.match(l1,/st run">בסבב · ממתין להתחלה</); assert.doesNotMatch(l1,/monitorMachine/,'classroom rows: no monitor (server allows build/cloner only)');
  const l3=between(html,'data-mac="b4:2e:99:07:1a:c3"','data-mac="b4:2e:99:07:1b:c1"');
  assert.match(l3,/st warn">חסר בסבב</); assert.match(l3,/muted">מעולם לא</);
});

test('the three disk states stay three: null = never reported, [] = reported none, list = count · size; state is never green without evidence', () => {
  const {run}=setup(); let html=run('machines(0)');
  const b3=between(html,'data-mac="c8:d9:d2:0b:fe:34"','data-mac="a0:48:1c:8a:18:40"');
  assert.match(b3,/muted">לא דיווח</); assert.match(b3,/st ">לא נראה</); assert.doesNotMatch(b3.split('</tr>')[0],/st ok/,'offline/never seen is grey');
  const c1=between(html,'data-mac="a0:48:1c:8a:18:40"','data-mac="78:ac:c0:9b:11:c2"');
  assert.match(c1,/st warn">דיווח 0 דיסקים</,'zero disks is a finding, not "no data"');
  run('MONITOR_ROWS=null'); html=run('machines(0)');
  assert.match(between(html,'data-mac="c8:d9:d2:0b:fe:34"','data-mac="a0:48:1c:8a:18:40"'),/st ">לא נקרא</,'monitor unread ≠ offline');
  assert.doesNotMatch(html,/מחוברים</,'no connected count without /monitor/machines');
  run('NET=null'); html=run('machines(0)');
  assert.doesNotMatch(html,/נראו ברשת היום/); assert.match(html,/role="tab"[^>]*>נראו ברשת</,'no counter when /net was not read');
  assert.match(between(html,'data-mac="b4:2e:99:07:1b:c1"'),/muted">לא נקרא</);
});

test('collapse/expand per group; above 50 machines groups start folded; the filter bar narrows the table in place', () => {
  const {run,node}=setup();
  let html=run('machines(0)');
  assert.match(html,/aria-expanded="true" aria-label="קפל מחשבי שיכפול"/);
  run("toggleMchGroup('grp_CLONERS')"); html=node('#mch-table').innerHTML;
  assert.match(html,/aria-expanded="false" aria-label="פתח מחשבי שיכפול"/); assert.doesNotMatch(html,/data-mac="78:ac:c0:9b:11:c2"/);
  assert.match(html,/data-mac="c8:d9:d2:0b:fe:32"/,'other groups untouched');
  run("toggleMchGroup('grp_CLONERS')"); assert.match(node('#mch-table').innerHTML,/data-mac="78:ac:c0:9b:11:c2"/);
  run('MACHINES=MACHINES.concat(Array.from({length:50},(_,i)=>({mac:"aa:aa:aa:aa:aa:"+String(i).padStart(2,"0"),suffix:String(i),group_id:"grp_LAB305",disks:null})))');
  html=run('machines(0)');
  assert.equal((html.match(/<tr data-mac=/g)||[]).length,0,'58 machines: every group folded by default');
  assert.equal((html.match(/<tr class="group"/g)||[]).length,4);
  run("toggleMchGroup('grp_BUILD')"); assert.equal((node('#mch-table').innerHTML.match(/<tr data-mac=/g)||[]).length,2);
  run('MCH.toggled.clear(); MACHINES=MACHINES.slice(0,8)');
  run("mchFilter('q','10.44.12.59')"); assert.deepEqual([...node('#mch-table').innerHTML.matchAll(/data-mac="([^"]+)"/g)].map(m=>m[1]),['78:ac:c0:9b:11:c2'],'search by IP via /net');
  assert.equal(node('#mch-count').textContent,'1 מתוך 8 מחשבים');
  run("mchFilter('q',''); mchFilter('role','classroom')"); assert.equal((node('#mch-table').innerHTML.match(/<tr data-mac=/g)||[]).length,4);
  run("mchFilter('role',''); mchFilter('group','grp_BUILD')"); assert.equal((node('#mch-table').innerHTML.match(/<tr data-mac=/g)||[]).length,2);
  run("mchFilter('group',''); mchFilter('state','err')"); assert.deepEqual([...node('#mch-table').innerHTML.matchAll(/data-mac="([^"]+)"/g)].map(m=>m[1]),['78:ac:c0:9b:11:c2']);
  run("mchFilter('state','ok')"); assert.equal((node('#mch-table').innerHTML.match(/<tr data-mac=/g)||[]).length,2,'connected = the two online cloners');
  run("mchFilter('state','stale')"); assert.equal((node('#mch-table').innerHTML.match(/<tr data-mac=/g)||[]).length,3,'never seen = stale');
  run("mchFilter('state','zzz'); mchFilter('q','nothing-here')"); assert.match(node('#mch-table').innerHTML,/אין מחשבים שתואמים לסינון/);
});

test('selection → action bar; move PUTs group_id per machine; removal is one confirm sheet listing the machines (#1270)', async () => {
  const {run,node,requests}=setup(); run('loadMachines=async()=>{}');
  assert.doesNotMatch(run('machines(0)'),/dg-bar foot/);
  run("toggleMchSel('78%3Aac%3Ac0%3A9b%3A11%3Ac2',true); toggleMchSel('a0:48:1c:8a:18:40',true)");
  const html=node('#mch-table').innerHTML;
  assert.match(html,/נבחרו 2 מתוך 8/); assert.match(html,/bulkMoveMachines\(\)">העבר לקבוצה…/); assert.match(html,/bulkRemoveMachines\(\)">הסרה</);
  assert.equal((html.match(/<tr data-mac="[^"]*" class="sel"/g)||[]).length,2);
  run('bulkMoveMachines()'); assert.equal(run('formOptions.fields[0].type'),'select');
  await run('formOptions.onSubmit({group_id:"grp_BUILD"})');
  const puts=requests.filter(r=>r.options.method==='PUT');
  assert.deepEqual(puts.map(r=>[r.url,JSON.parse(r.options.body).group_id]).sort(),[['/api/console/machines/78%3Aac%3Ac0%3A9b%3A11%3Ac2','grp_BUILD'],['/api/console/machines/a0%3A48%3A1c%3A8a%3A18%3A40','grp_BUILD']]);
  assert.equal(run('MCH.sel.size'),0);
  run("toggleMchSel('78:ac:c0:9b:11:c2',true); toggleMchSel('b4:2e:99:07:1a:c3',true); bulkRemoveMachines()");
  assert.equal(run('formOptions.fields'),undefined,'#1270: no typed fields'); assert.equal(run('formOptions.danger'),true);
  assert.match(run('formOptions.note'),/מחשב 2/); assert.match(run('formOptions.note'),/03/);
  assert.ok(!requests.some(r=>r.options.method==='DELETE'),'nothing deleted before the sheet is confirmed');
  await run('formOptions.onSubmit({})');
  assert.deepEqual(requests.filter(r=>r.options.method==='DELETE').map(r=>r.url),['/api/console/machines/78%3Aac%3Ac0%3A9b%3A11%3Ac2','/api/console/machines/b4%3A2e%3A99%3A07%3A1a%3Ac3']);
  run('toggleMchSelAll(true)'); assert.match(node('#mch-table').innerHTML,/נבחרו 8 מתוך 8/);
  run('mchSelClear()'); assert.doesNotMatch(node('#mch-table').innerHTML,/dg-bar foot/);
});

test('the machine drawer: header line, actions (monitor, WoL for the room, "requires API" for reboot/MAC), red-disk note with clear + kernel lines, disks by slot, hardware, journal history', async () => {
  const {run,node,requests}=setup();
  run("openMachineDetail('78%3Aac%3Ac0%3A9b%3A11%3Ac2')");
  assert.equal(node('#drawerTitle').textContent,'מחשב 2');
  let html=node('#drawerBody').innerHTML; balanced(html);
  // ‏#1300 (console-full.html W.machDrawer): משנה קצרה (תפקיד · IP · MAC) ותגיות — מצב, דיסקים, אדום
  assert.match(html,/class="page drw"/); assert.match(html,/<div class="obj-sub">מחשב שיכפול · <span class="mono">10.44.12.59<\/span> · <span class="mono">78:ac:c0:9b:11:c2<\/span><span class="n-meta"><span class="n-chip ">מחובר<\/span><span class="n-chip n">3 דיסקים<\/span><span class="n-chip w">דיסק נכשל בשיכפול הקודם<\/span><\/span><\/div>/);
  // הפרטים מקובצים (לא רשימה אחת): מצב עכשיו · זיהוי ורשת · מערכת וחומרה · בריאות המכונה
  for(const g of ['זיהוי ורשת','מערכת וחומרה','בריאות המכונה']) assert.match(html,new RegExp('<div class="mgrp"><div class="grp">'+g));
  assert.match(html,/<span class="k">קבוצה<\/span><span class="v">מחשבי שיכפול <a[^>]*moveMachineSheet\(/); assert.match(html,/<span class="k">נראה לאחרונה<\/span><span class="v">לפני 9 דק&#39;/);
  assert.match(html,/class="btn primary" (onclick="monitorMachine\('78%3Aac%3Ac0%3A9b%3A11%3Ac2'\)"|disabled title="[^"]+")>פתח במוניטור</,'#1300 (Nadav 12/10): the primary opens the machine viewer, or says why it cannot');
  assert.match(html,/<div class="grp-acts"><button class="btn sm" onclick="wakeMachine\('78%3Aac%3Ac0%3A9b%3A11%3Ac2'\)">WoL</,'#984: per-machine WoL in the cloner drawer, next to its state'); assert.doesNotMatch(html,/wakeRoom/);
  assert.match(html,/renameMachine\(/); assert.match(html,/rebootMachine\('78%3Aac%3Ac0%3A9b%3A11%3Ac2'\)">אתחול מרחוק</,'#977 §4'); assert.match(html,/editMachineMac\('78%3Aac%3Ac0%3A9b%3A11%3Ac2'\)">עריכה</,'#977 §5 — next to the MAC');
  assert.doesNotMatch(html,/בקרוב/,'#977: nothing in the drawer is "soon" any more');
  assert.match(html,/<div class="grp">קושחה וחיבורים/,'#2080 (mockup 06): "קושחה וחיבורים" above "מערכת וחומרה", in place of the old firmware row'); assert.doesNotMatch(html,/<span class="k">קושחה<\/span>/);
  assert.match(html,/<span class="k">סוכן<\/span><span class="v"><span class="muted">לא דווח<\/span>/);
  assert.match(html,/note err"[^]*<b>דיסק 3 אדום<\/b> — <span class="mono">S5Y2NX0R12345<\/span> נכשל בכתיבה .*סיבה: כבל\/חריץ SATA 2 \(ATA timeout\)/);
  assert.match(html,/clearDiskFailure\(7\)">נקה אחרי החלפה</); assert.match(html,/<summary>3 שורות קרנל<\/summary><pre class="ata-log">ata3.00: exception Emask/);
  assert.match(html,/<span class="k">מה המכונה עושה<\/span><span class="v"><span class="st ok">מחובר</);
  assert.match(html,/שלב אתחול אחרון<\/span><span class="v"><span class="st warn">GRUB נכנס לערך ImageSrv \(2\/9\) · לפני 9 דק&#39;<\/span><span class="sub">נעצר לפני: מודול ה-HTTP נטען/);
  assert.match(html,/<span class="k">הערה<\/span><span class="v">ליד הדלת/);
  assert.doesNotMatch(html,/SSH|SHA256:/,'#1266: no SSH host-key row in the drawer');
  assert.match(html,/<div class="sec" data-cnt="3">דיסקים<\/div>/,'#1300: the disks tab carries its count');
  assert.doesNotMatch(html,/דיווח אחרון/,'the compact disk list omits the report caption');
  assert.match(html,/<table class="n-tbl disk-map"><thead><tr><th>דיסק<\/th><th>חיבור<\/th><th>גודל<\/th><th>SMART<\/th>/,'#1485: the mockup table (W.machDrawer) + the connection column');
  assert.match(html,/<tr class="" data-slot="1"><td><b>דיסק 1<\/b><\/td><td><span class="mono" title="SATA 0">דיסק 1<\/span><\/td><td><span class="mono"><bdi dir="ltr">256GB<\/bdi><\/span> <span class="cap">870<\/span><\/td>/);
  assert.doesNotMatch(html,/S5Y30|SMART תקין|SMART לא נבדק/,'serials are absent from the disk list; SMART is a chip in its own column');
  assert.doesNotMatch(html,/sd[abc]/,'never sd*');
  assert.match(html,/openSlotMap\('78%3Aac%3Ac0%3A9b%3A11%3Ac2'\)">מיפוי חיבורים…</,'#2001: the mapping window opens from the disks tab — the separate dialog is gone'); assert.doesNotMatch(html,/editDrawerCount/);
  assert.match(html,/<span class="k">דגם<\/span><span class="v">LENOVO M720q</); assert.match(html,/<span class="k">TPM<\/span><span class="v">לא נבדק</); assert.match(html,/דף הדרייברים לא נטען/);
  assert.match(html,/טוען את היומן…/);
  const j=requests.find(r=>r.url.startsWith('/api/console/journal?'));
  assert.equal(new URL(j.url,'http://x').searchParams.get('machine'),'מחשב 2','history is filtered by the machine name (the journal translates MACs to names)');
  await new Promise(r=>setImmediate(r)); await new Promise(r=>setImmediate(r));
  html=node('#drawerBody').innerHTML;
  assert.match(html,/<span class="d err"><\/span><span>דיסק נכשל בכתיבה — מחשב 2 — דיסק 3/); assert.match(html,/15\/09\/2026/); assert.match(html,/מכונה נוספה — מחשב 2/);
  assert.match(html,/moveMachineSheet\(/); assert.match(html,/removeMachine\(/);
  run('removeMachine("78:ac:c0:9b:11:c2")'); assert.equal(run('formOptions.verify'),undefined,'#1270: no typed name'); assert.equal(run('formOptions.danger'),true);

  run("openMachineDetail('c8%3Ad9%3Ad2%3A0b%3Afe%3A34')"); html=node('#drawerBody').innerHTML; balanced(html);
  assert.match(html,/המכונה מעולם לא דיווחה על חומרה/); assert.match(html,/המכונה מעולם לא דיווחה על כוננים/,'no slots declared and nothing reported is said, not drawn');
  run("openMachineDetail('a0:48:1c:8a:18:40')"); html=node('#drawerBody').innerHTML;
  assert.match(html,/<div class="sec" data-cnt="2">דיסקים/);
  assert.match(html,/data-slot="1"[^]*title="SATA 0">דיסק 1[^]*ריק[^]*data-slot="2"[^]*title="SATA 1">דיסק 2[^]*ריק/,'reported-empty cloner renders its configured slots as empty rows');
  run("openMachineDetail('b4:2e:99:07:1a:c3')"); html=node('#drawerBody').innerHTML;
  assert.match(html,/title="v2">WoL ואתחול מרחוק — לתחנות כיתה ב-v2</,'classroom: WoL (#984) and reboot are v2, not buttons'); assert.doesNotMatch(html,/monitorMachine|openMachineMonitor/); assert.match(html,/st warn">חסר בסבב</);
  assert.doesNotMatch(html,/rebootMachine\(|wakeMachine\(/);
});

test('#977 §7 + #2080: boot mode / Secure Boot come from the firmware image; the agent version from /machines — reported, not reported and unknown are different texts', () => {
  const {run,node}=setup();
  const mac='c8:d9:d2:0b:fe:32';
  const cell=(k)=>{ const h=node('#drawerBody').innerHTML; const m=h.match(new RegExp(`<span class="k">${k}<\/span><span class="v">(.*?)<\/span>(?=<span class="k">|<\/div>)`)); return m&&m[1]; };
  const fw=(cur)=>run(`FIRMWARE['${mac}']=${JSON.stringify({current:cur,changes:[],slots:[],peers:[]})}; openMachineDetail('${mac}')`);
  const set=(f)=>run(`Object.assign(MACHINES.find(m=>m.mac==='${mac}'),${JSON.stringify(f)}); openMachineDetail('${mac}')`);
  fw({boot:'uefi',secure_boot:true,sata_mode:'ahci',model:'HP',bios_version:'v1'});
  assert.match(cell('אתחול'),/UEFI · Secure Boot דלוק/); assert.match(cell('בקר הדיסקים'),/AHCI/);
  fw({boot:'uefi',secure_boot:false}); assert.match(cell('אתחול'),/UEFI · Secure Boot כבוי/);
  fw({boot:'uefi',secure_boot:null}); assert.match(cell('אתחול'),/^UEFI$/,'null Secure Boot is not shown as off');
  fw({boot:null,secure_boot:null,sata_mode:null}); assert.match(cell('אתחול'),/לא דווח/); assert.match(cell('בקר הדיסקים'),/לא דווח/,'not reported is not a mode');
  set({agent_version:'v0.66.0'}); assert.match(cell('סוכן'),/<span class="mono">v0.66.0<\/span>/);
  set({agent_version:'unknown'}); assert.match(cell('סוכן'),/לא ידוע — סוכן ישן/,'the server stores "unknown" for a missing/invalid version');
});

test('Nadav 15/10: the NIC speed in the drawer and on the cloner card — reported, set by hand, or assumed (said as an assumption); admin edit PUTs nic_speed_mbps', async () => {
  const {run,node,requests}=setup();
  const mac='78:ac:c0:9b:11:c2';
  const cell=()=>{ const h=node('#drawerBody').innerHTML; const m=h.match(/<span class="k">כרטיס רשת<\/span><span class="v">(.*?)<\/span>(?=<span class="k">|<\/div>)/); return m&&m[1]; };
  const set=(f)=>run(`Object.assign(MACHINES.find(m=>m.mac==='${mac}'),${JSON.stringify(f)}); openMachineDetail('${mac}')`);
  set({nic_speed:{speed_mbps:1000,source:'reported',why:null}}); assert.match(cell(),/^1Gb\/s \(מדווח\) <a[^>]*editNicSpeed\(/);
  set({nic_speed:{speed_mbps:2500,source:'manual',why:null}}); assert.match(cell(),/^2.5Gb\/s \(הוגדר ידנית\)/);
  set({nic_speed:{speed_mbps:1000,source:'assumed',why:'no_probe'}}); assert.match(cell(),/^לא ידוע — הונח 1Gb\/s/,'an assumption is never shown as a measurement');
  assert.doesNotMatch(cell(),/מדווח/);
  set({nic_speed:undefined}); assert.match(cell(),/^לא ידוע/,'old server: no field = unknown');
  assert.equal(run(`machineSub(Object.assign({},MACHINES.find(m=>m.mac==='${mac}'),{nic_speed:{speed_mbps:10000,source:'reported'}}))`).includes('כרטיס רשת: 10Gb/s (מדווח)'),true);
  set({nic_speed:{speed_mbps:2500,source:'manual',why:null}});
  run(`editNicSpeed('${encodeURIComponent(mac)}')`);
  assert.equal(run('formOptions.fields[0].value'),'2500');
  assert.deepEqual(JSON.parse(run('JSON.stringify(formOptions.fields[0].options.map(o=>o.label))')),['אוטומטי','1Gb/s','2.5Gb/s','10Gb/s']);
  await run('formOptions.onSubmit({nic_speed:"auto"})');
  const put=requests.filter(r=>r.options.method==='PUT').pop();
  assert.equal(put.url,'/api/console/machines/78%3Aac%3Ac0%3A9b%3A11%3Ac2'); assert.deepEqual(JSON.parse(put.options.body),{nic_speed_mbps:null});
  await run('formOptions.onSubmit({nic_speed:"10000"})');
  assert.deepEqual(JSON.parse(requests.filter(r=>r.options.method==='PUT').pop().options.body),{nic_speed_mbps:10000});
});

test('#977 §4: reboot from the drawer — a confirm window with the consequences and a red button (#1270), the machine name rides along, the answer is "requested"', async () => {
  const {run,requests,fixtures}=setup();
  fixtures['/machines/78%3Aac%3Ac0%3A9b%3A11%3Ac2/reboot']={requested:1,ttl_s:120};
  run('rebootMachine("78%3Aac%3Ac0%3A9b%3A11%3Ac2")');
  assert.equal(run('formOptions.danger'),true); assert.equal(run('formOptions.fields'),undefined,'#1270: nothing to type');
  assert.match(run('formOptions.title'),/לאתחל את מחשב 2\?/);
  const note=run('formOptions.note'); assert.match(note,/ב-hello הבא/); assert.match(note,/השרת מסרב/);
  assert.ok(!requests.some(r=>r.url.includes('/reboot')),'nothing sent before the window is confirmed');
  await run('formOptions.onSubmit({})');
  const r=requests.find(x=>x.url.includes('/reboot'));
  assert.equal(r.url,'/api/console/machines/78%3Aac%3Ac0%3A9b%3A11%3Ac2/reboot'); assert.equal(r.options.method,'POST');
  assert.deepEqual(JSON.parse(r.options.body),{confirm_name:'מחשב 2'});
});

test('#977 §5: MAC edit — one LTR field, the answer moves the drawer to the new MAC and a DHCP failure is said', async () => {
  const {run,requests,fixtures}=setup();
  fixtures['/machines/c8%3Ad9%3Ad2%3A0b%3Afe%3A34']={ok:true,mac:'c8:d9:d2:0b:fe:99',network:{applied:false,error:'dnsmasq לא הגיב'}};
  run('editMachineMac("c8%3Ad9%3Ad2%3A0b%3Afe%3A34")');
  assert.equal(run('formOptions.fields.length'),1); assert.equal(run('formOptions.fields[0].dir'),'ltr'); assert.equal(run('formOptions.fields[0].value'),'c8:d9:d2:0b:fe:34');
  assert.match(run('formOptions.fields[0].help'),/ההיסטוריה .* נשארת על הכתובת הקודמת/);
  run('globalThis.toasts=[]; toast=(t)=>toasts.push(t); loadMachines=async()=>{ MACHINES.find(m=>m.mac==="c8:d9:d2:0b:fe:34").mac="c8:d9:d2:0b:fe:99"; }; openMachineDetail=(m)=>{ globalThis.reopened=m; }');
  await run('formOptions.onSubmit({mac:"C8-D9-D2-0B-FE-99"})');
  const r=requests.find(x=>x.options.method==='PUT');
  assert.equal(r.url,'/api/console/machines/c8%3Ad9%3Ad2%3A0b%3Afe%3A34'); assert.deepEqual(JSON.parse(r.options.body),{mac:'C8-D9-D2-0B-FE-99'});
  assert.equal(run('reopened'),'c8:d9:d2:0b:fe:99','the drawer follows the canonical MAC the server answered');
  assert.match(run('toasts.join("|")'),/ה-DHCP לא עודכן/);
});

test('the class as an object: crumbs, header, KPIs with meaning only, the same table filtered, rounds tab says "requires API"', () => {
  const {run,node}=setup();
  run("openClass('grp_LAB303')"); assert.equal(run('selected'),'machines'); assert.equal(run('MACHINES_CLASS'),'grp_LAB303');
  let html=run('machines(0)'); balanced(html);
  assert.match(html,/crumbs"><a[^>]*>srv<\/a><span>\/<\/span><span>מלאי<\/span><span>\/<\/span><a[^>]*openMachinesPage\(\)">מחשבים<\/a><span>\/<\/span><span>כיתות<\/span><span>\/<\/span><span>כיתה 303 — סייבר<\/span>/);
  assert.match(html,/obj-name">כיתה 303 — סייבר</); assert.match(html,/קבוצת כיתה · קידומת LAB303 · 3 מחשבים · 2 נראו היום/); assert.match(html,/pill info">סבב פתוח</);
  assert.match(html,/deployToGroup\('grp_LAB303'\)">הפץ לכיתה…</); assert.match(html,/openAddMachine\(\{group:'grp_LAB303'\}\)">\+ מחשב לכיתה</); assert.match(html,/renameGroup\(/); assert.match(html,/deleteGroup\(/);
  for(const t of ['סיכום','מחשבים','סבבים']) assert.match(html,new RegExp('role="tab"[^>]*>'+t+'<'));
  assert.match(html,/<div class="kpi info"><div class="l">סבב פעיל<\/div><div class="v"><bdi dir="auto">2<\/bdi> <small>\/ 3<\/small>/);
  assert.match(html,/<div class="kpi ok"><div class="l">דיסקים<\/div><div class="v"><bdi dir="auto">אין אדומים<\/bdi><\/div><div class="s">2 מתוך 3 דיווחו על דיסקים/);
  assert.match(html,/<div class="kpi "><div class="l">דורש טיפול<\/div><div class="v"><bdi dir="auto">0</);
  assert.doesNotMatch(html,/אימג' אחרון שהופץ/,'no KPI without data behind it');
  assert.equal((html.match(/<tr class="group"/g)||[]).length,0,'flat table inside the class');
  assert.deepEqual([...html.matchAll(/<tr data-mac="([^"]+)"/g)].map(m=>m[1]),['b4:2e:99:07:1a:c1','b4:2e:99:07:1a:c2','b4:2e:99:07:1a:c3']);
  assert.doesNotMatch(html,/aria-label="תפקיד"|aria-label="קבוצה"/,'scope selects hidden inside a group');
  assert.match(html,/הוספה: מספר 01–99 או INS · ייבוא בהדבקה/); assert.match(html,/openAddMachine\(\{group:'grp_LAB303',tab:1\}\)">ייבוא בהדבקה</);
  assert.match(html,/סבבים של הכיתה/); assert.match(html,/Office 2024 — סטנדרט · פתוח, 2\/3 הצטרפו/); assert.match(html,/היסטוריית סבבים לכיתה — <b[^>]*>דורש API<\/b>/);
  html=run('machines(2)'); assert.match(html,/דורש API/); assert.doesNotMatch(html,/<table class="dg"/);
  html=run('machines(1)'); assert.equal((html.match(/<table class="dg kebab-col"/g)||[]).length,1); assert.doesNotMatch(html,/class="kpi/);
  // #1016: class with machines is not deleted - no window promising otherwise; an empty class: red window, no typed word.
  run('globalThis.formOptions=undefined'); run("deleteGroup('grp_LAB303')"); assert.equal(run('formOptions'),undefined,'#1016: a class with machines opens no delete window');
  run("GROUPS.push({id:'grp_EMPTY',label:'empty'})"); run("deleteGroup('grp_EMPTY')"); assert.equal(run('formOptions.verify'),undefined,'#1270: no typed name'); assert.equal(run('formOptions.danger'),true);
  run('OVERVIEW.session=null; DISK_FAILURES=null'); html=run('machines(0)');
  assert.doesNotMatch(html,/קידומת/,'prefix comes only from a session'); assert.match(html,/<div class="kpi "><div class="l">סבב פעיל<\/div><div class="v"><bdi dir="auto">אין/);
  assert.match(html,/<div class="kpi "><div class="l">דיסקים<\/div><div class="v"><bdi dir="auto">לא נקרא/,'failures unread ≠ none');
  run("openClass('grp_CLONERS')"); html=run('machines(0)');
  assert.match(html,/poweroffRoom\('grp_CLONERS'\)">כיבוי כולם…</); assert.doesNotMatch(html,/הפץ לכיתה|renameGroup|role="tab"/,'#1485: the cloners page of the mockup — no tabs');
  assert.match(html,/crumbs">[^]*openMachinesPage\(\)">מחשבים<\/a><span>\/<\/span><span>מחשבי שיכפול<\/span><\/div>/,'the fixed group is the "מחשבי שיכפול" page of the mockup');
});

test('tree clicks: every group opens its object (wave 3a: build/cloner too); an unknown group id filters the table with a visible, clearable strip', () => {
  const {run}=setup();
  run("selectMachinesGroup('grp_LAB305')"); assert.equal(run('MACHINES_CLASS'),'grp_LAB305'); assert.equal(run('MACHINES_FILTER'),null);
  run("selectMachinesGroup('grp_BUILD')"); assert.equal(run('MACHINES_CLASS'),'grp_BUILD'); assert.equal(run('MACHINES_FILTER'),null);
  run("MACHINES_CLASS=null; MACHINES_FILTER='grp_BUILD'");
  let html=run('machines(0)');
  assert.match(html,/role="status"><div class="note info">[^]*מוצגת קבוצה: <b>מחשבי בנייה<\/b> · <a[^>]*clearMachinesFilter\(\)">הצג את כל המחשבים/);
  assert.equal((html.match(/<tr data-mac=/g)||[]).length,2); assert.equal((html.match(/<tr class="group"/g)||[]).length,0);
  run('renderCurrent=()=>{globalThis.rendered=true}; clearMachinesFilter()'); assert.equal(run('MACHINES_FILTER'),null); assert.equal(run('rendered'),true);
  run("MACHINES_FILTER='grp_LAB305'; MACHINES=MACHINES.filter(m=>m.group_id!=='grp_LAB305')"); assert.match(run('machines(0)'),/אין מחשבים בקבוצה כיתה 305 — משרד \(7 רשומים בסך הכול\)/);
  run('openMachinesPage()'); assert.equal(run('MACHINES_FILTER'),null); assert.equal(run('selected'),'machines');
});

test('"seen on the network" tab: /net as a datagrid with "register" as the primary action on an unregistered row; "red disks" tab with clear', async () => {
  const {run,node,requests}=setup();
  let html=run('machines(1)'); balanced(html);
  assert.equal((html.match(/<table class="dg"/g)||[]).length,1);
  const unreg=between(html,'data-mac="de:ad:be:ef:00:01"');
  assert.match(unreg,/pill warn">לא רשום</); assert.match(unreg,/btn sm primary" onclick="openAddMachine\(\{mac:'de%3Aad%3Abe%3Aef%3A00%3A01'\}\)">רשום</);
  assert.match(unreg,/netDeviceDescribe\(/); assert.match(unreg,/netDeviceForget\(/); assert.match(unreg,/st warn">תפריט האתחול נמסר \(1\/9\) · לפני 30 דק&#39;<\/span><span class="sub">נעצר לפני: GRUB נכנס לערך ImageSrv/);
  const reg=between(html,'data-mac="c8:d9:d2:0b:fe:32"','data-mac="a0:48:1c:8a:18:40"');
  assert.match(reg,/name">בנייה 1<\/span><span class="sub">מחשבי בנייה</); assert.match(reg,/openMachineDetail\(/); assert.doesNotMatch(reg,/>רשום</);
  assert.match(html,/netDeviceAdd\(\)">\+ הוספה ידנית</);
  run('netDeviceDescribe("de%3Aad%3Abe%3Aef%3A00%3A01")'); assert.equal(run('formOptions.sub'),'de:ad:be:ef:00:01');
  run('loadMachines=async()=>{}'); await run('formOptions.onSubmit({description:"מדפסת מעבדה 2"})');
  const put=requests.find(r=>r.options.method==='PUT'); assert.equal(put.url,'/api/console/net/de%3Aad%3Abe%3Aef%3A00%3A01');
  run('NET=null; NET_ERR="timeout"'); assert.match(run('machines(1)'),/note warn">[^]*רשימת ההתקנים לא נקראה: timeout/); run('NET=[]'); assert.match(run('machines(1)'),/עוד לא נראו התקנים/);
  html=run('machines(2)'); balanced(html);
  assert.match(html,/דיסקים אדומים — נכשלו בכתיבה/); assert.match(html,/<td>מחשב 2 · דיסק 3 · SATA 2<\/td>/); assert.match(html,/st err">כבל\/חריץ SATA 2</); assert.match(html,/clearDiskFailure\(7\)">נקה</); assert.match(html,/<summary>3 שורות קרנל</);
  assert.match(html,/דיסקים מכווצים/); assert.match(html,/אין דיסקים מכווצים/);
  run('DISK_FAILURES=null'); assert.match(run('machines(2)'),/רשימת הכשלים לא נטענה/);
});

test('"+ machine": one modal, two tabs — single (POST /machines) and paste with a dry_run preview before saving', async () => {
  const {run,node,requests}=setup(); run('loadMachines=async()=>{}');
  run("openAddMachine({group:'grp_LAB303',mac:'de%3Aad%3Abe%3Aef%3A00%3A01'})");
  assert.equal(node('#modalTitle').textContent,'+ מחשב');
  const html=node('#modalBody').innerHTML; balanced(html);
  assert.match(html,/role="tab"[^>]*>מחשב אחד</); assert.match(html,/role="tab"[^>]*>הדבקה — הרבה בבת אחת</);
  assert.match(html,/<select id="am-group"><option value="grp_BUILD">[^]*<option value="grp_LAB303" selected>/,'fixed groups come first, the class is preselected');
  assert.match(html,/<input id="am-mac" dir="ltr" value="de:ad:be:ef:00:01"/); assert.match(html,/<label for="am-name">/,'labels, not placeholders only');
  assert.match(html,/id="am-paste" class="form" hidden/); assert.match(html,/previewMachineImport\(\)">תצוגה מקדימה</);
  node('#am-name').value='04'; node('#am-mac').value='de:ad:be:ef:00:01'; node('#am-group').value='grp_LAB303';
  await run('submitAddMachine()');
  let post=requests.find(r=>r.url==='/api/console/machines'); assert.deepEqual(JSON.parse(post.options.body),{mac:'de:ad:be:ef:00:01',name:'04',group_id:'grp_LAB303'});
  run("openAddMachine({tab:1})"); assert.match(node('#modalBody').innerHTML,/id="am-one" class="form" hidden/);
  node('#am-pgroup').value='grp_LAB303'; node('#am-text').value='b4:2e:99:07:1a:c4 04\nzz 05';
  await run('previewMachineImport()');
  const dry=requests.find(r=>r.url==='/api/console/machines/import'); assert.equal(JSON.parse(dry.options.body).dry_run,true);
  assert.match(node('#am-preview').innerHTML,/st ok">b4:2e:99:07:1a:c4 04</); assert.match(node('#am-preview').innerHTML,/st err">zz 05 ← MAC לא תקין</);
  requests.length=0; await run('submitAddMachine()');
  post=requests.find(r=>r.url==='/api/console/machines/import'); assert.equal(JSON.parse(post.options.body).dry_run,undefined); assert.equal(JSON.parse(post.options.body).group_id,'grp_LAB303');
  run('addGroupSheet()'); assert.equal(run('formOptions.title'),'כיתה חדשה');
  await run('formOptions.onSubmit({label:"כיתה 307",id:""})'); assert.deepEqual(JSON.parse(requests.find(r=>r.url==='/api/console/groups').options.body),{id:'',label:'כיתה 307',role:'classroom'});
});

test('build/cloner role pages fix the add dialog group; the generic machines page keeps the group dropdown', () => {
  const {run,node}=setup();
  run("openAddMachine({role:'cloner'})");
  assert.equal(node('#modalTitle').textContent,'+ מחשב שיכפול');
  assert.equal(node('#modalSub').textContent,'יירשם בקבוצת מחשבי שיכפול');
  assert.doesNotMatch(node('#modalBody').innerHTML,/<select id="am-(?:p)?group"/);
  assert.match(node('#modalBody').innerHTML,/<input type="hidden" id="am-group" value="grp_CLONERS">/);

  run("openAddMachine({role:'build',tab:1})");
  assert.equal(node('#modalTitle').textContent,'+ מחשב בנייה');
  assert.equal(node('#modalSub').textContent,'יירשם בקבוצת מחשבי בנייה');
  assert.doesNotMatch(node('#modalBody').innerHTML,/<select id="am-(?:p)?group"/);
  assert.match(node('#modalBody').innerHTML,/<input type="hidden" id="am-pgroup" value="grp_BUILD">/);

  run('openAddMachine({})');
  assert.equal(node('#modalTitle').textContent,'+ מחשב');
  assert.equal(node('#modalSub').textContent,'רישום מחשב ברשת ההפצה');
  assert.match(node('#modalBody').innerHTML,/<select id="am-group"><option value="grp_BUILD">/,'generic page retains the existing first-option behaviour');
});

test('empty and unread states, and the old page is gone (machines.js, sub-tabs, per-group forms)', () => {
  const {run}=setup();
  run('MACHINES=[]'); let html=run('machines(0)'); assert.match(html,/אין מחשבים רשומים — הוסיפו את הראשון/); assert.match(html,/openAddMachine\(\{\}\)/);
  run('MACHINES=null'); html=run('machines(0)'); assert.match(html,/טוען נתונים/); assert.doesNotMatch(html,/אין מחשבים/);
  assert.equal(run('pages.machines.own'),true); assert.match(run('layout(pages.machines,0)'),/class="page"/);
  assert.equal(run('typeof machineAdminPage'),'undefined'); assert.equal(run('typeof loadMachinesTab'),'undefined'); assert.equal(run('typeof renderSeenDevices'),'undefined');
  assert.ok(!fs.existsSync(path.join(root,'machines.js')));
  const page=fs.readFileSync(path.join(root,'index.html'),'utf8');
  assert.doesNotMatch(page,/machines\.js/); assert.match(page,/onclick="treeNavToggle\(this,'machineTree',function\(\)\{MACHINES_FILTER=null;MACHINES_CLASS=null;MACHINES_ROLE=null;selectPageById\('machines'\)\}\)"/,'the tree node resets the filter, the open class and the role (#1300: from "מחשבי בנייה" a click on מחשבים shows all machines)');
});

test('deploy role: the page is not allowed at all, and the tree node is admin-only', () => {
  const {run}=setup(); run('ME.role="deploy"');
  assert.equal(run('pageAllowed("machines")'),false);
  const page=fs.readFileSync(path.join(root,'index.html'),'utf8');
  assert.match(page,/data-page="machines"[^>]*>[^]*?<span data-admin/);
  assert.match(page,/id="machineTree" class="inventory-children" role="group" data-admin>/,'#1300: open by default, like console-full.html (S.open[id] !== false)');
  // ‏#1300: "מחשבי בנייה" / "מחשבי שיכפול" הם עלים — בלי חץ ובלי תת-עץ, לחיצה = הדף המסונן, והעלה עצמו מודגש
  for (const role of ['build','cloner']) assert.match(page,new RegExp(`data-role="${role}" onclick="selectMachinesRole\\('${role}',this\\)" role="treeitem" tabindex="0"><span class="tree-arrow-sp"></span>`),role);
  assert.doesNotMatch(page,/id="(buildTree|clonerTree|classesTree|imagesTree|nicTree)"/,'no sub-trees under leaves');
});

// --- נדב 17/09: צומת-אב בעץ = דף המחשבים ממוקד לתפקיד, בלי "נראו ברשת" ---------
test('#1485: the tree leaves "מחשבי בנייה" / "מחשבי שיכפול" open the mockup pages of the fixed groups; "כיתות" stays the focused table', () => {
  const {run}=setup();
  run("selectMachinesRole('build')");
  assert.equal(run('MACHINES_ROLE'),'build'); assert.equal(run('MACHINES_CLASS'),null); assert.equal(run('MACHINES_FILTER'),null);
  let html=run('machines(0)'); balanced(html);
  assert.match(html,/obj-name">מחשבי בנייה</,'the page is named after the role');
  assert.doesNotMatch(html,/role="tab"/,'P.build has no tabs');
  assert.match(html,/openAddMachine\(\{group:'grp_BUILD'\}\)">\+ מחשב בנייה/);
  assert.doesNotMatch(html,/ייבוא בהדבקה|ייצוא CSV/,'role pages use cards instead of filtered-table controls');
  assert.match(html,/class="c12 grid2"/);
  assert.doesNotMatch(html,/data-mac="78:ac:c0:9b:11:c2"/,'cloners are not in the build page');
  assert.match(html,/data-mac="c8:d9:d2:0b:fe:32"/,'the build machine is');
  run('MACHINES=[]'); html=run('machines(0)');
  assert.match(html,/אין מחשב בנייה רשום/); assert.match(html,/openAddMachine\(\{group:'grp_BUILD'\}\)">\+ מחשב בנייה/,'the zero-machine page keeps the fixed build group');
  run("selectMachinesRole('cloner')"); html=run('machines(0)');
  assert.match(html,/openAddMachine\(\{group:'grp_CLONERS'\}\)">\+ מחשב שיכפול/);
  assert.doesNotMatch(html,/ייבוא בהדבקה|ייצוא CSV|wakeRoom/); assert.match(html,/poweroffRoom\('grp_CLONERS'\)">כיבוי כולם…/);
  run("selectMachinesRole('classroom')"); html=run('machines(0)');
  assert.match(html,/obj-name">כיתות</); assert.match(html,/2 כיתות/);
  assert.doesNotMatch(html,/data-mac="c8:d9:d2:0b:fe:32"/);
  run('openMachinesPage()'); assert.equal(run('MACHINES_ROLE'),null);
  assert.match(run('machines(0)'),/obj-name">מחשבים</);
});

// --- #1081: v1 hides classrooms; the tests above keep proving v2 with the flag on ---
test('#1081 v1: classrooms flag off hides classes from the machines page', () => {
  const {run}=setup();
  run('ME.capabilities.classrooms=false');
  const html=run('machines(0)'); balanced(html);
  assert.match(html,/obj-name">מחשבים</);
  assert.doesNotMatch(html,/כיתות/);
  assert.doesNotMatch(html,/כיתה/);
  assert.doesNotMatch(html,/classroom/i);
  assert.doesNotMatch(html,/\+ כיתה/);
  assert.doesNotMatch(html,/addGroupSheet/);
  assert.doesNotMatch(html,/grp_LAB303|grp_LAB305/);
  assert.match(html,/מחשבי בנייה/);
  assert.match(html,/משכפלים/);
  assert.match(html,/data-mac="c8:d9:d2:0b:fe:32"/);
  assert.doesNotMatch(html,/data-mac="b4:2e:99:07:1a:c1"/);
  run("selectMachinesRole('classroom')");
  assert.equal(run('MACHINES_ROLE'),null,'selecting classroom is a no-op when the flag is off');
  run('addGroupSheet()');
  assert.equal(run('typeof formOptions'),'undefined','+ class sheet does not open');
});
