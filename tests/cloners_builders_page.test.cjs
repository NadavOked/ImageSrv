// ‏#954 גל 3א: מחשבי שיכפול (חדר השיכפולים) ומחשבי בנייה כאובייקטים, לפי
// docs/design/console-redesign/cloners.md ו-builders.md — על groupPage של גל 3.
// בלי דפדפן: console.js + net.js + progress.js רצים ב-vm מול DOM מזערי ו-fetch מזויף.
// עיקרון 5: "לא נבדק" ≠ ירוק; ROOM null (לא נקרא) ≠ חדר בלי סבב; /tasks שלא נקרא ≠ אין קליטות.
// מוסכמת נדב: "דיסק N · SATA N-1", לעולם לא sd*.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const minutesAgo=(n)=>new Date(Date.now()-n*60000).toISOString();
const GB256=256060514304;

// #2050 (mockup 07, Nadav 24/10): a drawer with a problem -- and one written AND read back -- gets the
// emphasis class in its own colour; the colour is slotClass's (unchanged), the row keeps its words.
test('#2050 drawer emphasis: red/orange problems and read-back green get the frame; a healthy, writing or merely "done" disk does not', () => {
  const {run}=setup();
  const cls=(render,disk)=>{
    const h=run(`${render}({mac:'test',drawer_count:1}, {n:1,disk:${JSON.stringify(disk)},live:true}, false)`);
    const m=h.match(/^<div class="(?:dsk|disk)([^"]*)"/); assert.ok(m,h); return [m[1],h];
  };
  const cases=[
    [{state:'failed',smart:'ok',error:'write failed'},'err'],
    [{smart:'fail'},'err'],
    [{state:'waiting',smart:'warn',smart_reason:'pending_sectors'},'warn'],
    [{state:'done',smart:'ok',error:'SMART: 187 עלה'},'warn'],
    [{state:'done',smart:'ok',readback:'unverified'},'warn'],
    [{state:'done',smart:'warn',readback:'verified'},'warn'],      // the severe colour wins
    [{state:'done',smart:'ok',readback:'verified'},'ok'],
    [{state:'done',smart:'ok',readback:'sampled'},'ok'],
    [{state:'done',smart:'ok'},''],                                 // no read-back evidence: not green (principle 5)
    [{smart:'ok'},''], [{state:'writing',smart:'ok'},''], [{state:'verifying',smart:'ok'},''],
    [{state:'writing',smart:'warn',cancel:'admin'},''],             // cancelling: grey, as on the GUI
    [{state:'cancelled',smart:'fail'},''],
  ];
  for (const render of ['dskRowHtml','roundDskHtml','slotHtml'])
    for (const [disk,tone] of cases) {
      const [c]=cls(render,disk);
      if (tone) assert.match(c,new RegExp(`(^| )bay-emphasis ${tone}$`),`${render} ${JSON.stringify(disk)}`);
      else assert.doesNotMatch(c,/bay-emphasis/,`${render} ${JSON.stringify(disk)}`);
    }
  // the words stay: the writing orange drawer still says its percent, the failed one "נכשל"
  assert.match(cls('roundDskHtml',{state:'writing',smart:'warn',bytes_written:37,bytes_total:100})[1],/<b>37%<\/b>/);
  assert.match(cls('dskRowHtml',{state:'failed',smart:'ok',error:'x'})[1],/<b>נכשל<\/b>/);
  assert.match(cls('slotHtml',{state:'failed',smart:'ok',error:'x'})[1],/<span class="bay-chip">נכשל<\/span>/);
  assert.match(cls('slotHtml',{smart:'warn'})[1],/<span class="bay-chip">אזהרה<\/span>/);
});

test('#2050 drawer emphasis CSS: a 3px frame on all four sides and the soft fill of its colour, no side stripe', () => {
  const css=fs.readFileSync(path.join(root,'console.css'),'utf8');
  const rules=[...css.matchAll(/([^{}]*bay-emphasis[^{}]*)\{([^}]*)\}/g)].map(m=>[m[1].trim(),m[2]]);
  assert.ok(rules.length>=5,'the emphasis rules exist');
  const frame=rules.find(([sel])=>/\.mach \.dsk\.bay-emphasis,/.test(sel)&&/\.slots \.disk\.bay-emphasis$/.test(sel));
  assert.ok(frame,'one rule frames both the cloner card rows and the slots');
  assert.match(frame[1],/(^|;)border:3px solid var\(--bay\)/); assert.match(frame[1],/background:var\(--bay-soft\)/);
  for (const [tone,tok] of [['err','danger'],['warn','warning'],['ok','success']]) {
    const r=rules.find(([sel])=>sel.endsWith(`.bay-emphasis.${tone}`)); assert.ok(r,tone);
    assert.ok(r[1].includes(`--bay:var(--clr-${tok});--bay-soft:var(--clr-${tok}-soft);--bay-fg:var(--clr-${tok}-fg)`),r[1]);
  }
  for (const [sel,body] of rules) assert.doesNotMatch(body,/border-(inline|left|right|block)|inset/,`no side tab: ${sel}`);
});

const B1='c8:d9:d2:0b:fe:32', B2='c8:d9:d2:0b:fe:34', C1='a0:48:1c:8a:18:40', C2='78:ac:c0:9b:11:c2', C3='78:ac:c0:9b:11:c3';
const drawer=(dev,port,serial,extra={})=>({dev,port,serial,model:'870 EVO',size_bytes:GB256,fresh:true,selected:false,state:null,error:null,bytes_written:0,bytes_total:0,stalled_s:null,crc_delta:null,smart:'unchecked',...extra});

function setup({roundOpen=false}={}) {
  const nodes=new Map(), requests=[], opened=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const roomIdle={round:null,stream_stalled:false,machines:[
    {mac:C1,name:'מחשב 1',drawer_count:2,awake:true,drawers:0,fresh_drawers:0,drawer_list:[],joined:false,state:null,bytes_written:0,bytes_total:0,error:null},
    {mac:C2,name:'מחשב 2',drawer_count:3,awake:true,drawers:3,fresh_drawers:3,joined:false,state:null,bytes_written:0,bytes_total:0,error:null,
      drawer_list:[drawer('sda',1,'S5Y30',{smart:'ok'}),drawer('sdb',2,'S5Y31'),drawer('sdc',3,'S5Y2NX0R12345',{smart:'ok'})]},
    {mac:C3,name:'מחשב 3',drawer_count:1,awake:false,drawers:0,fresh_drawers:0,drawer_list:[],joined:false,state:null,bytes_written:0,bytes_total:0,error:null}]};
  const roomLive={round:{id:'rnd_1',image_id:'img_1',image_name:'office365',target_drives:5,written_drives:2,remaining_drives:3,wave_number:2,wave_state:'running',ready_drives:3,opened_by:'nadav',source:null},
    stream_stalled:false,machines:[
    {...roomIdle.machines[0]},
    {...roomIdle.machines[1],joined:true,state:'running',bytes_written:38,bytes_total:100,
      drawer_list:[drawer('sda',1,'S5Y30',{smart:'ok',state:'writing',bytes_written:38,bytes_total:100,stalled_s:2}),
                   drawer('sdb',2,'S5Y31',{state:'writing',bytes_written:37,bytes_total:100,crc_delta:3,stalled_s:90}),
                   drawer('sdc',3,'S5Y2NX0R12345',{smart:'ok',state:'failed',error:'queue overflow: target lost bytes',bytes_written:4,bytes_total:100})]},
    {...roomIdle.machines[2]}]};
  const fixtures={
    '/groups':[
      {id:'grp_BUILD',label:'מחשבי בנייה',role:'build',sort:1,machines:2},
      {id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:2,machines:3},
      {id:'grp_LAB303',label:'כיתה 303 — סייבר',role:'classroom',sort:3,machines:1}],
    '/machines':[
      {mac:B1,suffix:'בנייה 1',group_id:'grp_BUILD',note:null,drawer_count:3,prompt:'menu',disks:[{dev:'sda',size_bytes:GB256,model:'870 EVO',serial:'S5YB1',port:1,smart:'ok'}],disks_reported_at:minutesAgo(180),
        inventory:{dmi:{sys_vendor:'LENOVO',product_name:'10SQS0AK00',product_version:'ThinkCentre M720q',board_name:'3132'},pci:[],tpm:{present:true,version:'2.0'}},inventory_seen_at:minutesAgo(180)},
      {mac:B2,suffix:'בנייה 2',group_id:'grp_BUILD',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null},
      {mac:C1,suffix:'מחשב 1',group_id:'grp_CLONERS',note:null,drawer_count:2,prompt:null,disks:[],disks_reported_at:minutesAgo(9),inventory:null,inventory_seen_at:null},
      {mac:C2,suffix:'מחשב 2',group_id:'grp_CLONERS',note:null,drawer_count:3,prompt:null,disks_reported_at:minutesAgo(9),inventory:null,inventory_seen_at:null,
        disks:[{dev:'sda',size_bytes:GB256,model:'870 EVO',serial:'S5Y30',port:1,smart:'ok'},{dev:'sdb',size_bytes:GB256,model:'870 EVO',serial:'S5Y31',port:2},{dev:'sdc',size_bytes:GB256,model:'870 EVO',serial:'S5Y2NX0R12345',port:3,smart:'ok'}]},
      {mac:C3,suffix:'מחשב 3',group_id:'grp_CLONERS',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null},
      {mac:'b4:2e:99:07:1a:c1',suffix:'01',group_id:'grp_LAB303',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null}],
    '/net':[
      {mac:B1,ip:'10.44.12.20',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(180),registered:true,name:'בנייה 1',group_id:'grp_BUILD',group_label:'מחשבי בנייה',role:'build',boot:null},
      {mac:B2,ip:'10.44.12.21',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(4),registered:true,name:'בנייה 2',group_id:'grp_BUILD',group_label:'מחשבי בנייה',role:'build',boot:null},
      {mac:C1,ip:'10.44.12.118',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(9),registered:true,name:'מחשב 1',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',boot:null},
      {mac:C2,ip:'10.44.12.59',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(9),registered:true,name:'מחשב 2',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',boot:null}],
    '/disk-failures':[{id:7,session_id:'r15',mac:C2,dev:'sdc',serial:'S5Y2NX0R12345',port:3,disk_number:3,ata_port:'ata3',at:'2026-09-15T14:22:00Z',cause:'cable',error:'ATA bus error: SError: CommWake',ata_log:['ata3.00: exception Emask','ata3: hard resetting link','ata3.00: failed command: WRITE FPDMA QUEUED']}],
    '/shrink-records':[],
    '/folders':[{name:'Office',description:'',images:0}],   // #1251: openCapture reads them itself
    '/monitor/machines':[
      {mac:B1,name:'בנייה 1',role:'build',ip:'10.44.12.20',online:false,prompt:'menu'},
      {mac:B2,name:'בנייה 2',role:'build',ip:'10.44.12.21',online:true,prompt:null},
      {mac:C1,name:'מחשב 1',role:'cloner',ip:'10.44.12.118',online:true,prompt:null},
      {mac:C2,name:'מחשב 2',role:'cloner',ip:'10.44.12.59',online:true,prompt:null},
      {mac:C3,name:'מחשב 3',role:'cloner',ip:null,online:false,prompt:null}],
    '/room':roundOpen?roomLive:roomIdle,
    '/tasks':[
      {id:'tsk_a1',mac:B1,machine:'בנייה 1',group_label:'מחשבי בנייה',type:'capture',disk:'sda',image_id:'img_a1',name:'Office 2024 v3',state:'pending',error:null,bytes_written:0,bytes_total:null,source_progress:null,created_at:minutesAgo(3),updated_at:minutesAgo(3)},
      {id:'tsk_b2',mac:B2,machine:'בנייה 2',group_label:'מחשבי בנייה',type:'capture',disk:'sda',image_id:'img_b2',name:'Kali 2026.2 — סייבר',state:'running',error:null,bytes_written:20000000000,bytes_total:null,source_progress:{partition:3,blocks_read:40,blocks_total:100},created_at:minutesAgo(10),updated_at:minutesAgo(2)},
      {id:'tsk_c3',mac:B1,machine:'בנייה 1',group_label:'מחשבי בנייה',type:'capture',disk:'sda',image_id:'img_c3',name:'Office 2024 — מרצה',state:'done',error:'המקור לא הוחזר לגודלו — להרחיב ליד המחשב',bytes_written:54000000000,bytes_total:54000000000,source_progress:null,created_at:'2026-09-10T11:00:00Z',updated_at:'2026-09-10T11:08:15Z'},
      {id:'tsk_d4',mac:B1,machine:'בנייה 1',group_label:'מחשבי בנייה',type:'capture',disk:'sda',image_id:'img_d4',name:'CAD Heavy',state:'failed',error:'ntfsresize נכשל — chkdsk',bytes_written:0,bytes_total:null,source_progress:null,created_at:'2026-08-19T14:02:00Z',updated_at:'2026-08-19T14:02:12Z'},
      {id:'tsk_e5',mac:'b4:2e:99:07:1a:c1',machine:'01',group_label:'כיתה 303 — סייבר',type:'capture',disk:'sda',image_id:'img_e5',name:'מכיתה',state:'done',error:null,bytes_written:1,bytes_total:1,source_progress:null,created_at:'2026-08-01T10:00:00Z',updated_at:'2026-08-01T10:30:00Z'}],
    '/overview':{images:2,machines:6,storage:null,pulls:[],room:null,session:null},
    '/journal':[],
    '/room/wake':{sent:3,failed:0,reasons:[]},
    '/room/poweroff':{requested:3,ttl_s:120},
    // ‏#980 §3: סבבי חדר שהסתיימו — מהחדש לישן, כמו שהשרת מחזיר.
    '/room/history':[
      {id:'rr_15',state:'failed',image_id:'img_1',image_name:'office365',source_kind:'library',target_drives:4,written_drives:1,opened_by:'nadav',created_at:'2026-09-15T08:00:00Z',closed_at:'2026-09-15T08:20:00Z',failed_reason:'send_failed'},
      {id:'rr_12',state:'closed',image_id:'img_9',image_name:'win11-base',source_kind:'library',target_drives:6,written_drives:6,opened_by:'nadav',created_at:'2026-09-12T08:00:00Z',closed_at:'2026-09-12T09:30:00Z',failed_reason:null},
      {id:'rr_10',state:'closed',image_id:'img_gone',image_name:'img_gone',source_kind:'library',target_drives:6,written_drives:2,opened_by:'yossi',created_at:'2026-09-10T08:00:00Z',closed_at:'2026-09-10T08:40:00Z',failed_reason:null}],
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(...a){opened.push(a);}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      if(fixtures[key]===403) return {status:403,ok:false,json:async()=>({detail:'admin only'})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] ?? {ok:true}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={timezone:"UTC",username:"admin",role:"admin",server_name:"srv",version:"v0.32.1",capabilities:{}}; current="machines"; sheet = o => { globalThis.formOptions=o; }; confirmSheet=(t,s,l,fn)=>{ globalThis.confirmFn=fn; }; selectPageById=(id)=>{ globalThis.selected=id; current=id; }; toast=(m)=>{ globalThis.toasted=m; }; renderCurrent=()=>{ globalThis.rendered=(globalThis.rendered||0)+1; };');
  for(const [name,key] of [['OVERVIEW','/overview'],['MACHINES','/machines'],['GROUPS','/groups'],['DISK_FAILURES','/disk-failures'],['SHRINK_RECORDS','/shrink-records'],['NET','/net'],['MONITOR_ROWS','/monitor/machines'],['ROOM','/room'],['ROOM_HISTORY','/room/history'],['CAPTURE_TASKS','/tasks']]) run(name+'='+JSON.stringify(fixtures[key]));
  run('CAPTURE_TASKS_READ=true; ROOM_KEY=JSON.stringify(ROOM);');
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
const CARD=/<div class="mcard(?: off)?"/g;
const after=(html,a,b)=>{ const i=html.indexOf(a); assert.ok(i>=0,'missing: '+a); const from=i+a.length, j=html.indexOf(b,from); assert.ok(j>=0,'missing: '+b); return html.slice(from,j); };
const card=(html,mac)=>html.slice(html.indexOf(`data-mac="${mac}"`)).split(/<div class="mcard(?: off)?"/)[0];

const MACH=/<div class="mach[^"]*" data-mac=/g;
const mach=(html,mac)=>html.slice(html.indexOf(`<div class="mach`, html.indexOf(`data-mac="${mac}"`)-60)).split(/(?=<div class="mach[ "])/)[0];
const dsk=(html,n)=>{ const m=html.match(new RegExp(`<div class="dsk[^"]*" data-slot="${n}"[^]*?</b></div>`)); assert.ok(m,'missing dsk '+n); return m[0]; };

test('#1485 cloners page = console-full.html P.cloners: header (counters, add, power off all), no tabs, no KPIs; the tree leaf and the group open the same page', () => {
  const {run}=setup();
  run("selectMachinesGroup('grp_CLONERS')"); assert.equal(run('MACHINES_CLASS'),'grp_CLONERS'); assert.equal(run('MACHINES_FILTER'),null);
  const html=run('machines(0)'); balanced(html);
  assert.match(html,/obj-name">מחשבי שיכפול</);
  assert.match(html,/<span>מחשבים<\/a>|onclick="openMachinesPage\(\)">מחשבים<\/a><span>\/<\/span><span>מחשבי שיכפול<\/span><\/div>/,'crumbs: … › מחשבים › מחשבי שיכפול');
  assert.match(html,/obj-sub" title="3 מחשבים · 2 מחוברים · דיסק אחד נכשל בשיכפול הקודם">/);   // #1580: orange
  assert.doesNotMatch(html,/role="tab"/,'the mockup page has no tabs');
  assert.doesNotMatch(html,/class="kpi/,'no KPI strip in the mockup');
  assert.match(html,/<button class="btn" onclick="openAddMachine\(\{group:'grp_CLONERS'\}\)">\+ מחשב שיכפול<\/button><button class="btn danger" onclick="poweroffRoom\('grp_CLONERS'\)">כיבוי כולם…<\/button>/);
  assert.doesNotMatch(between(html,'obj-actions','<div class="body">'),/openRoomRound|wakeRoom/,'a round opens from the deploy page');
  assert.match(html,/class="card-h"><span>המחשבים והחריצים <small>לחיצה על כרטיס פותחת פרטים<\/small>/);
  assert.match(html,/class="card-h"><span>מחשבים שלא דיווחו</);
  assert.doesNotMatch(html,/בקרוב|disabled/);
  run("selectMachinesRole('cloner')"); const leaf=run('machines(0)');
  assert.match(leaf,/obj-name">מחשבי שיכפול</); assert.match(leaf,/<div class="rgrid">/,'the tree leaf renders the same mockup page');
  run('openRoomRound()'); assert.equal(run("document.getElementById('modalTitle').textContent"),'התחל סבב','the room window still opens (deploy page)');
});

test('#1485 cloner cards (.mach): name + status pill, MAC, one row per slot "דיסק N" · bar · status, card click → panel, "חריצים" → panel on the disks tab; never sd*', () => {
  const {run}=setup();
  run("openClass('grp_CLONERS')"); const html=run('machines(0)'); balanced(html);
  const grid=between(html,'<div class="rgrid">','מחשבים שלא דיווחו');
  assert.equal((grid.match(MACH)||[]).length,3); assert.doesNotMatch(grid,/sd[abc]/,'never sd*');
  const c1=mach(grid,C1), c2=mach(grid,C2), c3=mach(grid,C3);
  assert.match(c1,/^<div class="mach" data-mac="a0:48:1c:8a:18:40" role="button" tabindex="0" onclick="openMachineDetail\('a0%3A48%3A1c%3A8a%3A18%3A40'\)" onkeydown="[^"]+"><div class="mt"><span>מחשב 1<\/span><span class="pill ok">מחובר<\/span><\/div><div class="ms mono">a0:48:1c:8a:18:40<\/div>/);
  assert.equal((c1.match(/<div class="dsk vacant" data-slot="\d" title="דיסק \d · SATA \d · ריק"><span>דיסק \d<\/span><div class="bar"><i style="--w:0%"><\/i><\/div><b>ריק<\/b><\/div>/g)||[]).length,2,'drawer_count=2, nothing reported in them → two empty rows');
  assert.match(dsk(c2,1),/<div class="dsk" data-slot="1" title="SATA 0 · 870 EVO · S5Y30 · SMART תקין"><span>דיסק 1<\/span><div class="bar ok"><i style="--w:100%"><\/i><\/div><b>תקין<\/b><\/div>/,'SMART ok = green');
  assert.match(dsk(c2,2),/<span>דיסק 2<\/span><div class="bar idle"><i style="--w:100%"><\/i><\/div><b>לא נבדק<\/b>/,'unchecked is grey, never green (principle 5)');
  assert.match(dsk(c2,3),/title="SATA 2 · 870 EVO · S5Y2NX0R12345 · נכשל בשיכפול הקודם 15\/09\/2026 · כבל\/חריץ SATA 2"><span>דיסק 3<\/span><div class="bar warn"><i style="--w:100%"><\/i><\/div><b>אזהרה<\/b>/,'#1580: failed last time = orange');
  run(`MACHINES.find(m=>m.mac==='${C2}').disks.find(d=>d.serial==='S5Y2NX0R12345').smart='fail'`);   // #1580: SMART fail = red
  const c2r=mach(between(run('machines(0)'),'<div class="rgrid">','מחשבים שלא דיווחו'),C2);
  assert.match(dsk(c2r,3),/<div class="bar err"><i style="--w:100%"><\/i><\/div><b>אדום<\/b>/,'SMART fail = red');
  assert.match(c2r,/^<div class="mach err"[^>]*><div class="mt"><span>מחשב 2<\/span><span class="pill ok">מחובר<\/span><span class="pill err">דיסק אדום<\/span>/,'a red disk paints the card red (mockup CLONE-04)');
  assert.match(c2,/<div class="acts"><button class="btn sm" type="button" onclick="event.stopPropagation\(\);openMachineDetail\('78%3Aac%3Ac0%3A9b%3A11%3Ac2','disks'\)">חריצים<\/button><\/div>/);
  assert.match(c3,/^<div class="mach off"/,'not connected → dashed'); assert.match(c3,/מעולם לא דיווח על דיסקים/);
  assert.doesNotMatch(grid,/editDrawerCount|monitorMachine\(|clearDiskFailure/,'the count dialog is gone; clearing a red disk is in the panel');
  const silent=between(html,'מחשבים שלא דיווחו');
  assert.match(silent,/<b>מחשב 3<\/b><\/td><td>מעולם לא<\/td><td><button class="btn sm" onclick="wakeMachine\('78%3Aac%3Ac0%3A9b%3A11%3Ac3'\)">WoL<\/button>/,'never seen → listed with WoL');
  assert.doesNotMatch(silent,/מחשב 1|מחשב 2/,'seen 9 minutes ago is not silent (10-minute rule)');
});

test('#1485 cloner cards during a round: write % per drawer from /room, failure memory wins, "in round" pill; stalled stream turns the header pill orange', () => {
  const {run}=setup({roundOpen:true});
  run("openClass('grp_CLONERS')"); const html=run('machines(0)'); balanced(html);
  assert.match(html,/pill info">סבב פעיל — גל 2, 2\/5</);
  const c2=mach(between(html,'<div class="rgrid">','מחשבים שלא דיווחו'),C2);
  assert.match(c2,/<span class="pill info">בסבב · 38%/);
  assert.match(dsk(c2,1),/<div class="bar "><i style="--w:38%"><\/i><\/div><b>38%<\/b>/);
  assert.match(dsk(c2,2),/CRC \+3 · לבדוק כבל · ללא תזוזה 90 ש&#39;[^]*<div class="bar warn"><i style="--w:37%"><\/i><\/div><b>37%<\/b>/,'CRC delta and stall are orange');
  assert.match(dsk(c2,3),/<div class="bar err"><i style="--w:100%"><\/i><\/div><b>נכשל<\/b>/);
  run('ROOM.stream_stalled=true'); assert.match(run('machines(0)'),/pill warn">סבב פעיל — גל 2, 2\/5 · הזרם עצר</);
  run('ROOM=null'); const unread=run('machines(0)');
  assert.match(unread,/pill ">החדר לא נקרא</);
  assert.match(dsk(mach(unread,C2),1),/<b>תקין<\/b>/,'unread room falls back to the hello report, not to "nothing"');
});

test('#1485 mapping: drawer_slots picks which connection is "דיסק N"; an unmapped drive is "SATA n · לא ממופה" (never a guessed number); a CD/DVD drive is its own row, never a disk', () => {
  const {run}=setup();
  run(`Object.assign(MACHINES.find(m=>m.mac==='${C2}'),{drawer_slots:['sata:3','sata:1'],drawer_slots_custom:true})`);
  run(`Object.assign(MACHINES.find(m=>m.mac==='${C1}'),{drawer_count:3,optical:[{dev:'sr0',port:3,model:'HL-DT-ST DVDRAM'}]})`);
  run("openClass('grp_CLONERS')"); const html=run('machines(0)'); balanced(html);
  const c2=mach(html,C2);
  assert.deepEqual([...c2.matchAll(/data-slot="(\d*)"/g)].map(m=>m[1]),['1','2',''],'two mapped drawers, then the unmapped drive');
  assert.match(dsk(c2,1),/title="SATA 2 · 870 EVO · S5Y2NX0R12345[^"]*נכשל בשיכפול הקודם[^"]*"><span>דיסק 1<\/span>[^]*<b>אזהרה<\/b>/,'SATA 2 is disk 1 on this machine — and its red memory follows the port');
  assert.match(dsk(c2,2),/title="SATA 0 · 870 EVO · S5Y30[^"]*"><span>דיסק 2<\/span>/);
  assert.match(c2,/<div class="dsk unmapped" data-slot="" title="SATA 1 — לא ממופה לאף דיסק · 870 EVO · S5Y31[^"]*"><span>SATA 1<\/span><div class="bar"><i style="--w:0%"><\/i><\/div><b>לא ממופה<\/b><\/div>/);
  const c1=mach(html,C1);
  assert.match(c1,/<div class="dsk opt" data-slot="3" title="כונן CD\/DVD · SATA 2 · HL-DT-ST DVDRAM · ממופה כדיסק 3 — לא יעד"><span>CD\/DVD<\/span>[^]*<b>SATA 2<\/b>/,'the slot of a CD drive is never "empty"');
  assert.equal(run(`failureWhere(DISK_FAILURES[0])`),'מחשב 2 · דיסק 1 · SATA 2','the red-disk line names the mapped number');
  run(`MACHINES.find(m=>m.mac==='${C2}').drawer_slots=['sata:1','sata:2']`);
  assert.equal(run(`failureWhere(DISK_FAILURES[0])`),'מחשב 2 · SATA 2 (לא ממופה)','an unmapped port is said, not numbered');
  // the deploy grid never offers an unmapped drive as a target (the server accepts only mapped ports)
  run("ROOM.machines.find(m=>m.mac==='"+C2+"').awake=true");
  const picks=run(`JSON.stringify(roomPickSlots(roomMachines().filter(m=>m.mac==='${C2}')).map(x=>[x.s.n,x.d&&x.d.port,x.ok]))`);
  assert.deepEqual(JSON.parse(picks).map(x=>x[0]),[1,2,null]);
  assert.equal(JSON.parse(picks)[2][2],false,'unmapped → not ok');
});

test('#1033/#1485: a 12×3 fixture renders 36 disk rows, stacked by disk number', () => {
  const {run}=setup();
  const macs=Array.from({length:12},(_,i)=>`aa:bb:cc:dd:ee:${String(i+10).padStart(2,'0')}`);
  const machines12=macs.map((mac,i)=>({mac,suffix:'מחשב '+(i+1),group_id:'grp_CLONERS',note:null,drawer_count:3,prompt:null,
    disks:[0,1,2].map(j=>({dev:'sd'+String.fromCharCode(97+j),size_bytes:GB256,model:'870 EVO',serial:'S'+i+'-'+j,port:j+1,smart:'ok'})),
    disks_reported_at:new Date().toISOString(),inventory:null,inventory_seen_at:null}));
  run(`GROUPS=[{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:1,machines:12}];`);
  run(`MACHINES=${JSON.stringify(machines12)};`);
  run("NET=[]; DISK_FAILURES=[]; MONITOR_ROWS=[]; ROOM=null;");
  run("openClass('grp_CLONERS')"); const html=run('machines(0)'); balanced(html);
  const grid=between(html,'<div class="rgrid">','מחשבים שלא דיווחו');
  assert.equal((grid.match(MACH)||[]).length,12);
  assert.equal((grid.match(/<div class="dsk" data-slot="\d" title="[^"]*"><span>דיסק \d<\/span><div class="bar ok"><i style="--w:100%"><\/i><\/div><b>תקין<\/b><\/div>/g)||[]).length,36);
  for(const mac of macs) assert.deepEqual([...mach(grid,mac).matchAll(/data-slot="(\d)"/g)].map(m=>Number(m[1])),[1,2,3]);
});

test('#1485 cloners: unread lists say so, empty group, and the deploy role', () => {
  const {run}=setup();
  run("openClass('grp_CLONERS'); DISK_FAILURES=null; MONITOR_ROWS=null; NET=null");
  let html=run('machines(0)');
  assert.match(html,/3 מחשבים · מחוברים: לא נקרא · כשלים קודמים: לא נקרא/);
  assert.match(html,/רשימת הרשת לא נקראה — אי אפשר לדעת מי לא דיווח/,'unread ≠ "everyone reported"');
  run("NET=[]; MACHINES=MACHINES.filter(m=>m.group_id!=='grp_CLONERS'); DISK_FAILURES=[]"); html=run('machines(0)'); balanced(html);
  assert.match(html,/0 מחשבים/); assert.match(html,/אין מחשבי שיכפול רשומים — הוסיפו את הראשון/);
  assert.match(html,/<b class="et">כל המחשבים הרשומים דיווחו<\/b>מחשב ללא hello מעל 10 דקות יופיע כאן עם אפשרות להעיר אותו \(WoL\)\./);
  run('ME.role="deploy"'); assert.equal(run("pageAllowed('machines')"),false,'deploy never reaches the machines page');
  html=run('machines(0)'); assert.doesNotMatch(html,/openRoomRound|wakeRoom|openAddMachine|clearDiskFailure|monitorMachine|openMachineDetail/,'no admin actions rendered for deploy even if the page were reached');
});

test('#1485 builders page = console-full.html P.build: header (counters, add), no tabs; a card per machine with address, MAC, drives, last capture, before-capture, and capture · details · rename', () => {
  const {run}=setup();
  run("selectMachinesGroup('grp_BUILD')"); assert.equal(run('MACHINES_CLASS'),'grp_BUILD');
  const html=run('machines(0)'); balanced(html);
  assert.match(html,/obj-name">מחשבי בנייה</);
  assert.match(html,/obj-sub" title="2 מחשבים · מחשב אחד מחובר">/);
  assert.match(html,/pill info">2 קליטות בתהליך</);
  assert.match(between(html,'obj-actions','<div class="body">'),/^obj-actions"><button class="btn" onclick="openAddMachine\(\{group:'grp_BUILD'\}\)">\+ מחשב בנייה<\/button><button class="btn" onclick="refreshPage\(\)">/);
  assert.doesNotMatch(html,/role="tab"|openDirectRound|wakeGroup/);
  assert.match(html,/<div class="c12 grid2"><div class="card bcard" data-mac="c8:d9:d2:0b:fe:32">/);
  assert.equal((html.match(/<div class="card bcard"/g)||[]).length,2);
  const b1=between(html,'data-mac="c8:d9:d2:0b:fe:32"','data-mac="c8:d9:d2:0b:fe:34"'), b2=between(html,'data-mac="c8:d9:d2:0b:fe:34"','קליטות אחרונות');
  assert.match(b1,/<span>בנייה 1 <span class="pill warn">ממתין למפעיל: תפריט<\/span> <span class="pill warn">קליטה ממתינה<\/span><\/span>/);
  assert.match(b1,/<dl class="kvd"><dt>כתובת<\/dt><dd><span class="mono">10.44.12.20<\/span><\/dd><dt>MAC<\/dt><dd><span class="mono">c8:d9:d2:0b:fe:32<\/span><\/dd><dt>דיסקים<\/dt><dd><ul class="drv-list"><li><b>דיסק 1<\/b> · <bdi dir="ltr">256GB<\/bdi> · <bdi>870<\/bdi> <span class="muted">· SATA 0<\/span><\/li><\/ul><\/dd>/);
  assert.match(b1,/<dt>קליטה אחרונה<\/dt><dd><div>Office 2024 v3 · ממתין ל-PXE<\/div><div class="cap"><span class="muted">PXE — ממתין שהמחשב יעלה<\/span><\/div><div><a[^>]*cancelCaptureVerified\('tsk_a1'\)">ביטול הקליטה…<\/a><\/div><\/dd>/);
  // an open capture → no second one (server would 409); #1505: its detail panel instead
  assert.match(b1,/<div class="acts"><button class="btn primary" onclick="openCaptureDetail\('tsk_a1'\)">פירוט קליטה<\/button><button class="btn" onclick="openMachineDetail\('c8%3Ad9%3Ad2%3A0b%3Afe%3A32'\)">פתח פרטים<\/button><button class="btn" onclick="renameMachine\('c8%3Ad9%3Ad2%3A0b%3Afe%3A32'\)">שינוי שם<\/button><\/div>/,'an open capture → no second one (server would 409)');
  assert.doesNotMatch(b1,/openCapture\(/,'an open capture → no capture button');
  assert.match(b2,/<span>בנייה 2 <span class="pill ok">מחובר<\/span> <span class="pill info">קליטה רצה<\/span>/);
  assert.match(b2,/<dt>דיסקים<\/dt><dd><span class="muted">לא דיווח<\/span><\/dd>/);
  assert.match(b2,/Kali 2026.2 — סייבר · 40% · מחיצה 3 \(בלוקים\) עכשיו<\/div>[^]*?role="progressbar"[^>]*aria-valuenow="40"/);
  assert.match(b2,/השלב לא דווח \(סוכן ישן\)/,'#980 §4: a running task without a stage is an old agent, not "first stage"');
  run("CAPTURE_TASKS=[]"); const idle=run('machines(0)'); balanced(idle);
  const i2=between(idle,'data-mac="c8:d9:d2:0b:fe:34"','קליטות אחרונות');
  assert.match(i2,/<dt>קליטה אחרונה<\/dt><dd>—<\/dd>/);
  assert.match(i2,/<button class="btn primary" onclick="openCapture\('c8%3Ad9%3Ad2%3A0b%3Afe%3A34'\)\.catch\(e => toast\(e\.message\)\)">קליטה<\/button>/);
  run("CAPTURE_TASKS="+JSON.stringify([{id:'tsk_c3',mac:B1,type:'capture',disk:'sda',name:'Office 2024 — מרצה',state:'done',updated_at:'2026-09-10T11:08:15Z',created_at:'2026-09-10T11:00:00Z'}]));
  assert.match(between(run('machines(0)'),'data-mac="c8:d9:d2:0b:fe:32"','data-mac="c8:d9:d2:0b:fe:34"'),/<dt>קליטה אחרונה<\/dt><dd>Office 2024 — מרצה · 10\/09\/2026<\/dd>/);
  run(`MONITOR_ROWS.find(r=>r.mac==='${B2}').online=false`);
  assert.match(between(run('machines(0)'),'data-mac="c8:d9:d2:0b:fe:34"','קליטות אחרונות'),/<p class="card-help">המחשב לא מחובר עכשיו — קליטה תמתין שיעלה ב-PXE\. אפשר להעיר אותו <a[^>]*wakeMachine\('c8%3Ad9%3Ad2%3A0b%3Afe%3A34'\)">דרך WoL<\/a>\.<\/p>/,'capture from an off machine stays possible (PXE flow); WoL is offered');
  return run("openCapture('c8%3Ad9%3Ad2%3A0b%3Afe%3A34')").then(()=>{
    const f=run('formOptions.fields').find(x=>x.id==='mac'); assert.equal(f.value,B2,'the card preselects its machine in the capture sheet');
    run("CAPTURE_TASKS_READ=false"); const unread=run('machines(0)');
    assert.match(unread,/<dt>קליטה אחרונה<\/dt><dd><span class="muted">לא נקרא<\/span><\/dd>/); assert.match(unread,/רשימת הקליטות לא נקראה/); assert.doesNotMatch(unread,/אין קליטות עדיין/,'unread ≠ empty');
    run("MACHINES=MACHINES.filter(m=>m.group_id!=='grp_BUILD')"); const empty=run('machines(0)');
    assert.match(empty,/אין מחשב בנייה רשום — הוסיפו אותו/); assert.match(empty,/openAddMachine\(\{group:'grp_BUILD'\}\)">\+ מחשב בנייה</);
  });
});

test('#1485 build machine: one disk + a CD drive → the card lists "דיסק 1" and "כונן CD/DVD", never an empty slot; the CD is never a capture source', async () => {
  const {run,fixtures}=setup();
  const b1={drawer_count:3,drawer_slots:null,disks:[{dev:'sda',size_bytes:500107862016,model:'Samsung SSD 860',serial:'S4EV',port:2,smart:'ok'}],optical:[{dev:'sr0',port:3,model:'HL-DT-ST DVDRAM'}]};
  Object.assign(fixtures['/machines'].find(m=>m.mac===B1),b1);   // openCapture re-reads /machines
  run(`Object.assign(MACHINES.find(m=>m.mac==='${B1}'),${JSON.stringify(b1)})`);
  run("openClass('grp_BUILD')"); let card1=between(run('machines(0)'),'data-mac="c8:d9:d2:0b:fe:32"','data-mac="c8:d9:d2:0b:fe:34"');
  assert.match(card1,/<ul class="drv-list"><li><b>דיסק 2<\/b> · <bdi dir="ltr">500GB<\/bdi> · <bdi>Samsung<\/bdi> <span class="muted">· SATA 1<\/span><\/li><li><b>כונן CD\/DVD<\/b> <span class="muted">· SATA 2 · <bdi>HL-DT-ST DVDRAM<\/bdi><\/span><\/li><\/ul>/,'convention: SATA 1 = disk 2; empty slot 1 is not listed; the CD keeps its own name');
  assert.doesNotMatch(card1,/ריק/);
  run(`MACHINES.find(m=>m.mac==='${B1}').drawer_slots=['sata:2']`); fixtures['/machines'].find(m=>m.mac===B1).drawer_slots=['sata:2'];
  card1=between(run('machines(0)'),'data-mac="c8:d9:d2:0b:fe:32"','data-mac="c8:d9:d2:0b:fe:34"');
  assert.match(card1,/<li><b>דיסק 1<\/b> · <bdi dir="ltr">500GB<\/bdi>/,'mapped: SATA 1 is disk 1 on this machine');
  run('CAPTURE_TASKS=[]');
  await run(`openCapture('${encodeURIComponent(B1)}')`);
  const disk=run('formOptions.fields').find(x=>x.id==='disk');
  const opts=JSON.stringify(disk.options||disk.optionsFor||run('formOptions.fields'));
  assert.doesNotMatch(opts,/sr0|CD\/DVD/,'the capture sheet never offers the optical drive');
  assert.match(opts,/דיסק 1 · 500GB · Samsung SSD 860/);
});

test('builders: recent captures table (group tasks only, 20 from /tasks) — state colours, bytes, duration, #927 warning, when', () => {
  const {run}=setup();
  run("openClass('grp_BUILD')"); let html=run('machines(0)'); balanced(html);
  html=between(html,'קליטות אחרונות'); assert.equal((html.match(/<table class="dg"/g)||[]).length,1);
  for(const col of ["אימג&#39;",'מחשב · דיסק','מצב','נקראו','משך','אזהרה / שגיאה','מתי']) assert.match(html,new RegExp('<th>'+col+'</th>'));
  assert.match(html,/<th>תיקייה<\/th>/,'#968: /tasks carries the folder'); assert.doesNotMatch(html,/דורש API/);
  const ids=[...html.matchAll(/<tr data-task="([^"]+)"/g)].map(m=>m[1]);
  assert.deepEqual(ids,['tsk_a1','tsk_b2','tsk_c3','tsk_d4'],'the classroom capture is not a builders capture');
  const r=(id)=>between(html,`data-task="${id}"`,'</tr>');
  assert.match(r('tsk_a1'),/<span class="st run">ממתין ל-PXE<\/span><\/td><td><span class="muted">—<\/span><\/td><td><span class="muted">—<\/span><\/td><td><span class="muted">—<\/span><\/td><td>היום \d\d:\d\d<\/td>/);
  assert.match(r('tsk_b2'),/<span class="st run">נקלט · 40% · מחיצה 3 \(בלוקים\)<\/span><\/td><td><bdi dir="ltr">18.6 GB<\/bdi><\/td><td>8:00<\/td>/);
  assert.match(r('tsk_c3'),/<span class="st warn">הושלם — אזהרה<\/span><\/td><td><bdi dir="ltr">50.3 GB<\/bdi><\/td><td>8:15<\/td><td><span class="st warn">המקור לא הוחזר לגודלו — להרחיב ליד המחשב<\/span><\/td><td>10\/09\/2026 11:00<\/td>/);
  assert.match(r('tsk_d4'),/<span class="st err">נכשל<\/span><\/td><td><bdi dir="ltr">0 B<\/bdi><\/td><td>0:12<\/td><td>ntfsresize נכשל — chkdsk<\/td><td>19\/08\/2026 14:02<\/td>/);
  assert.doesNotMatch(r('tsk_c3'),/st ok/,'done with a warning is never green');
});

test('data: loadMachines reads /room and /tasks; the 2s poll re-reads /room only for the open cloners object and re-renders only on change; task changes re-render the open build object', async () => {
  const {run,requests,fixtures}=setup();
  run('CAPTURE_TASKS_READ=false; ROOM=null; populateSidebarGroups=()=>{}; updateAlertBadge=()=>{}; renderActivity=()=>{};');
  await run('loadMachines()');
  assert.ok(requests.some(r=>r.url==='/api/console/room')); assert.ok(requests.some(r=>r.url==='/api/console/tasks'));
  assert.equal(run('ROOM.machines.length'),3); assert.equal(run('CAPTURE_TASKS_READ'),true);
  assert.ok(requests.some(r=>r.url==='/api/console/room/history')); assert.equal(run('ROOM_HISTORY.length'),3,'#980 §3: history read with the machines');
  run("MACHINES_CLASS='grp_BUILD'; current='machines'; rendered=0"); requests.length=0;
  await run('refreshGroupLive()'); assert.equal(requests.length,0,'build object: no /room poll');
  run("MACHINES_CLASS='grp_CLONERS'");
  await run('refreshGroupLive()'); assert.equal(requests.filter(r=>r.url==='/api/console/room').length,1); assert.equal(run('rendered'),0,'same answer → no re-render');
  fixtures['/room']={...fixtures['/room'],round:{id:'r2',image_id:'i',image_name:'x',target_drives:1,written_drives:0,remaining_drives:1,wave_number:1,wave_state:'open',ready_drives:0,opened_by:'n',source:null}};
  await run('refreshGroupLive()'); assert.equal(run('rendered'),1,'changed answer → re-render'); assert.equal(run('ROOM.round.id'),'r2');
  run("current='images'"); await run('refreshGroupLive()'); assert.equal(requests.filter(r=>r.url==='/api/console/room').length,2,'not on the machines page → no poll');
  assert.ok(!requests.some(r=>r.url==='/api/console/room/history'),'history is read on load, never in the 2s poll');
  run("current='machines'; MACHINES_CLASS='grp_BUILD'; rendered=0; MCH.tasksKey=''");
  await run('loadCaptures()'); assert.equal(run('rendered'),1,'new task list → build object re-rendered');
  await run('loadCaptures()'); assert.equal(run('rendered'),1,'same task list → not again');
});

test('the machines table links every group row to its object, fixed groups included', () => {
  const {run}=setup();
  run('MACHINES_CLASS=null'); const html=run('machines(0)');
  assert.match(html,/<tr class="group" data-group="grp_CLONERS">[^]*?openClass\('grp_CLONERS'\)">מחשבי שיכפול<\/a>/);
  assert.match(html,/<tr class="group" data-group="grp_BUILD">[^]*?openClass\('grp_BUILD'\)">פתח</);
});

test('#980 §1: "power off all" is a danger sheet behind the typed group name, posts confirm_name, and reports requests — not machines powered off', async () => {
  const {run,requests}=setup();
  run("poweroffRoom('grp_CLONERS')");
  assert.equal(run('formOptions.danger'),true);
  assert.ok(!run('formOptions.verify'),'#1270: no typed name');
  assert.match(run('formOptions.note'),/hello הבא/); assert.match(run('formOptions.note'),/WoL/,'console-full.html: the consequences are an impact list in the note, the sub names the group');
  await run('formOptions.onSubmit({})');
  const req=requests.find(r=>r.url==='/api/console/room/poweroff');
  assert.ok(req,'POST /room/poweroff was sent'); assert.equal(req.options.method,'POST');
  assert.deepEqual(JSON.parse(req.options.body),{confirm_name:'מחשבי שיכפול'});
  assert.match(run('toasted'),/בקשת כיבוי נשלחה ל-3 מחשבים/);
  run('ME.role="deploy"; formOptions=null'); run("poweroffRoom('grp_CLONERS')"); assert.equal(run('formOptions'),null,'not an admin → no sheet');
});

test('#968: the recent captures table shows the folder from /tasks — "" is the library root, a missing field is a dash', () => {
  const {run}=setup();
  run("CAPTURE_TASKS[0].folder='Office'; CAPTURE_TASKS[1].folder=''; delete CAPTURE_TASKS[2].folder;");
  run("openClass('grp_BUILD')"); const html=run('machines(0)'); balanced(html);
  const r=(id)=>between(html,`data-task="${id}"`,'</tr>');
  assert.match(r('tsk_a1'),/<\/td><td>Office<\/td><td>בנייה 1/);
  assert.match(r('tsk_b2'),/<\/td><td><span class="muted">שורש הספרייה<\/span><\/td>/);
  assert.match(r('tsk_c3'),/<\/td><td><span class="muted">—<\/span><\/td><td>בנייה 1/);
});

test('#980 §3: room history (deploy page) from /room/history — newest first, failed red with the reason, all-written green, short of the target grey; unread ≠ none', () => {
  const {run}=setup();
  let html=run('roomHistoryHtml()'); balanced(html);
  const rows=[...html.matchAll(/<tr>([^]*?)<\/tr>/g)].map(m=>m[0]).slice(1);
  assert.equal(rows.length,3);
  assert.match(html,/<th>#<\/th><th>אימג׳<\/th><th>מחשבים<\/th><th>נכתבו<\/th><th>משך<\/th><th[^>]*>מצב<\/th><th[^>]*>תאריך<\/th>/);
  assert.match(rows[0],/office365<\/td><td>—<\/td><td>1\/4<\/td><td>20 דק(?:'|&#39;)<\/td><td[^>]*><span class="pill err">נכשל<\/span><span class="sub">send_failed<\/span>/,'failed: red pill + the reason, duration from created/closed');
  assert.match(rows[1],/win11-base<\/td><td>—<\/td><td>6\/6<\/td><td>90 דק(?:'|&#39;)<\/td><td[^>]*><span class="pill ok">הצליח<\/span>/,'every declared drive written → green');
  assert.match(rows[2],/img_gone<\/td><td>—<\/td><td>2\/6<\/td><td>40 דק(?:'|&#39;)<\/td><td[^>]*><span class="pill warn">2 מתוך 6<\/span>/,'closed short of the target is not green');
  run('ROOM_HISTORY=[{id:"x",state:"closed",image_name:"a",target_drives:2,written_drives:2,opened_by:"n",created_at:"2026-09-10T08:00:00Z",closed_at:null}]'); html=run('roomHistoryHtml()');
  assert.match(html,/<td>—<\/td><td>2\/2<\/td><td>—<\/td><td[^>]*><span class="pill ok">הצליח/);
  run('ROOM_HISTORY=[]'); html=run('roomHistoryHtml()'); assert.match(html,/אין סבבים קודמים בחדר/);
  run('ROOM_HISTORY=null'); html=run('roomHistoryHtml()'); assert.match(html,/note warn[^]*היסטוריית הסבבים לא נקראה/,'unread is said, not shown as empty');
});

test('#980 §4: the capture stage chain (build card, "קליטה אחרונה") — done stages plain, the current one bold with its time, the rest grey; an unknown stage is not guessed', () => {
  const {run}=setup();
  const stages=()=>{ run("openClass('grp_BUILD')"); const html=run('machines(0)'); balanced(html); return after(between(html,'data-mac="c8:d9:d2:0b:fe:34"'),'<div class="cap">','</div>'); };
  run("CAPTURE_TASKS[1].stage='stream'; CAPTURE_TASKS[1].stage_started_at=new Date(Date.parse(CAPTURE_TASKS[1].updated_at)-125000).toISOString()");
  let s=stages();
  assert.match(s,/^בדיקת NTFS → כיווץ → <b>זרם \+ sha256<\/b> <span class="cap">\(2:05\)<\/span> → <span class="muted">החזרת הגודל<\/span> → <span class="muted">אימות<\/span>$/);
  run("CAPTURE_TASKS[1].stage='verify'; CAPTURE_TASKS[1].stage_started_at=null"); s=stages();
  assert.match(s,/החזרת הגודל → <b>אימות<\/b>$/,'no start time → no invented duration');
  run("CAPTURE_TASKS[1].stage='dance'"); s=stages();
  assert.match(s,/השלב לא דווח \(סוכן ישן\)/);
});

test('#980 §6: "before capture" (build card) — used from the last finished capture, NTFS from the machine probe; never captured / image gone / unmeasured / unchecked are separate texts', () => {
  const {run}=setup();
  const cell=()=>{ run("openClass('grp_BUILD')"); const html=run('machines(0)'); return after(between(html,'data-mac="c8:d9:d2:0b:fe:32"','data-mac="c8:d9:d2:0b:fe:34"'),'<dt>לפני קליטה</dt><dd>','</dd></dl>'); };
  const set=(f)=>run(`Object.assign(MACHINES.find(m=>m.mac===${JSON.stringify(B1)}),${JSON.stringify(f)})`);
  assert.match(cell(),/<span class="muted">לא נקלט מהמחשב הזה<\/span> · <span class="muted">NTFS לא נבדק<\/span>/,'no last_capture, no probe — said, not invented');
  set({last_capture:{task_id:'tsk_c3',image_id:'img_c3',name:'Office 2024 — מרצה',at:'2026-09-10T11:08:15Z',in_library:true,used_bytes:84825604096},
       probe:{ntfs:[{node:'/dev/sda3',state:'clean'}]}});
  let c=cell();
  assert.match(c,/בשימוש <bdi dir="ltr">79.0 GB<\/bdi> <span class="cap">\(מהקליטה האחרונה, 10\/09\/2026\)<\/span>/); assert.match(c,/<span class="st ok">NTFS נקי<\/span>/);
  set({probe:{ntfs:[{node:'/dev/sda1',state:'clean'},{node:'/dev/sda3',state:'hibernated'}]}}); c=cell();
  assert.match(c,/<span class="st warn">NTFS נעול ב-Fast Startup — כיבוי רגיל לא משחרר; powercfg \/h off כמנהל<\/span>/,'one hibernated partition is enough to refuse a capture');
  assert.doesNotMatch(c,/כיבוי מלא/,'#1504: a full shutdown does not release a Fast Startup disk (metal 16/10) — the fix is powercfg /h off');
  set({probe:{ntfs:[{node:'/dev/sda3',state:'dirty'}]}}); assert.match(cell(),/<span class="st warn">NTFS dirty — chkdsk ב-Windows<\/span>/);
  set({probe:{ntfs:[{node:'/dev/sda3',error:'ntfs-3g.probe rc 21'}]}}); assert.match(cell(),/<span class="st warn">NTFS: לא הצלחנו לבדוק \/dev\/sda3<\/span>/);
  set({probe:{ntfs:[]}}); assert.match(cell(),/<span class="muted">אין מחיצת NTFS<\/span>/);
  set({probe:{ntfs:{error:'blkid rc 4'}}}); assert.match(cell(),/לא הצלחנו לבדוק: blkid rc 4/);
  set({probe:{ntfs:null}}); assert.match(cell(),/<span class="muted">NTFS לא נבדק<\/span>/);
  set({last_capture:{task_id:'tsk_c3',image_id:'img_c3',name:'x',at:'2026-09-10T11:08:15Z',in_library:false,used_bytes:null}});
  assert.match(cell(),/<span class="muted">בשימוש — האימג׳ של הקליטה האחרונה כבר לא בספרייה<\/span>/);
  set({last_capture:{task_id:'tsk_c3',image_id:'img_c3',name:'x',at:'2026-09-10T11:08:15Z',in_library:true,used_bytes:null}});
  assert.match(cell(),/<span class="muted">בשימוש לא נמדד בקליטה האחרונה<\/span>/);
});

/* ---------- #1485: the "דיסקים" tab of the machine panel — the mapping editor ---------- */
function drawerSetup(opts) {
  const env=setup(opts);
  env.run('globalThis.drawerHtml=""; setDrawerBody=(h)=>{ globalThis.drawerHtml=h; }; openDrawer=(t,h)=>{ globalThis.drawerHtml=h; }; loadMachines=async()=>{ globalThis.reloaded=(globalThis.reloaded||0)+1; };');
  return env;
}
const tab=(html)=>{ const i=html.indexOf('">דיסקים</div>'); assert.ok(i>=0,'no disks section'); const from=html.lastIndexOf('<div class="sec"',i), to=html.indexOf('<div class="sec"',i); return html.slice(from,to<0?undefined:to); };
test('#1485 panel "דיסקים" tab = the mockup table (דיסק | חיבור | גודל | SMART), the CD/DVD row, and (#2001) one button to the "מיפוי חיבורים" window', () => {
  const {run}=drawerSetup();
  run(`Object.assign(MACHINES.find(m=>m.mac==='${C2}'),{optical:[{dev:'sr0',port:5,model:'HL-DT-ST DVDRAM'}]})`);
  run(`openMachineDetail('${encodeURIComponent(C2)}')`); const html=run('drawerHtml'); balanced(html);
  const t=tab(html);
  assert.match(t,/<div class="sec" data-cnt="3">דיסקים<\/div><table class="n-tbl disk-map"><thead><tr><th>דיסק<\/th><th>חיבור<\/th><th>גודל<\/th><th>SMART<\/th>/);
  assert.doesNotMatch(t,/<select|slotEdit/,'#2001: no editing in the table — the window edits');
  assert.match(t,/<tr class="" data-slot="1"><td><b>דיסק 1<\/b><\/td><td><span class="mono" title="SATA 0">דיסק 1<\/span><\/td>/,'#1375: the connection by drawer number (SATA k-1 in the title)');
  assert.match(t,/<td><span class="mono"><bdi dir="ltr">256GB<\/bdi><\/span> <span class="cap">870<\/span><\/td><td><span class="n-chip">תקין<\/span><\/td>/);
  assert.match(t,/data-slot="2">[^]*?<span class="n-chip n">לא נבדק<\/span>/,'unchecked is grey');
  assert.match(t,/data-slot="3">[^]*?<span class="n-chip w">נכשל בשיכפול הקודם<\/span>[^]*?clearDiskFailure\(7\)">נקה<\/a>/,'the red disk is cleared here now');
  assert.match(t,/<tr class="opt" data-slot=""><td><b>כונן CD\/DVD<\/b><\/td><td><span class="mono">SATA 4<\/span><\/td><td><span class="muted">HL-DT-ST DVDRAM<\/span><\/td><td><span class="muted">—<\/span><\/td>/,'the CD drive is a row of its own, not a slot, never "empty"');
  assert.match(t,/<div class="map-tools"><button class="btn sm" type="button" onclick="openSlotMap\('78%3Aac%3Ac0%3A9b%3A11%3Ac2'\)">מיפוי חיבורים…<\/button><\/div>/);
  assert.doesNotMatch(html,/editDrawerCount|מספר חריצי SATA/,'the separate count dialog is gone');
});

test('#1375 disks tab: the connection is "דיסק N" by drawer number, never "SATA n-1"; the size is the marketed one (500GB, not 466GiB); unmapped and CD/DVD keep their SATA port', () => {
  const {run}=drawerSetup();
  run(`MACHINES.find(m=>m.mac==='${C2}').disks[0].size_bytes=500107862016`);   // a "500GB" drive = 465.8 GiB
  run(`Object.assign(MACHINES.find(m=>m.mac==='${C2}'),{optical:[{dev:'sr0',port:5,model:'HL-DT-ST DVDRAM'}]})`);
  run(`openMachineDetail('${encodeURIComponent(C2)}')`); const t=tab(run('drawerHtml'));
  const shown=t.replace(/title="[^"]*"/g,'');   // what the eye reads: no tooltips
  assert.match(t,/data-slot="1"><td><b>דיסק 1<\/b><\/td><td><span class="mono" title="SATA 0">דיסק 1<\/span>/,'disk 1 sits on connection "דיסק 1"');
  assert.match(t,/<span class="mono"><bdi dir="ltr">500GB<\/bdi><\/span>/,'marketed size');
  assert.doesNotMatch(t,/GiB|465|466/,'never the binary size');
  assert.doesNotMatch(shown.replace(/<tr class="opt"[^]*?<\/tr>/,'').replace(/<p class="map-help">[^]*?<\/p>/,''),/SATA \d/,'no "SATA n" on a mapped row');
  assert.match(t,/<tr class="opt" data-slot=""><td><b>כונן CD\/DVD<\/b><\/td><td><span class="mono">SATA 4<\/span>/,'the CD drive is not a drawer: its port, not a number');
  run(`Object.assign(MACHINES.find(m=>m.mac==='${C2}'),{drawer_slots:['sata:3'],drawer_slots_custom:true})`);
  run(`openMachineDetail('${encodeURIComponent(C2)}')`);
  assert.match(tab(run('drawerHtml')),/<tr class="unmapped" data-slot=""><td><b>SATA 0 \(לא ממופה\)<\/b><\/td><td><span class="mono">SATA 0<\/span>/,'an unmapped drive gets no guessed drawer number (#1485)');
});

test('#1485 NVMe: a controller is shown by its PCI address (stable), not by nvme0n1', () => {
  const {run}=drawerSetup();
  run(`Object.assign(MACHINES.find(m=>m.mac==='${C2}'),{disks:[{dev:'nvme0n1',size_bytes:512110190592,model:'Samsung 980',serial:'NV1',port:null,pci:'0000:03:00.0',smart:'ok'},{dev:'sda',size_bytes:${GB256},model:'870 EVO',serial:'S5Y30',port:1,smart:'ok'}],drawer_slots:['nvme:0000:03:00.0','sata:1'],drawer_slots_custom:true})`);
  run(`openMachineDetail('${encodeURIComponent(C2)}')`); const t=tab(run('drawerHtml'));
  assert.match(t,/data-slot="1"><td><b>דיסק 1<\/b><\/td><td><span class="mono">NVMe 03:00.0<\/span>/);
  assert.match(t,/<bdi dir="ltr">512GB<\/bdi>/);
  assert.doesNotMatch(t,/nvme0n1/,'never the enumeration name');
  assert.equal(run(`diskLabel(MACHINES.find(m=>m.mac==='${C2}'), MACHINES.find(m=>m.mac==='${C2}').disks[0])`),'דיסק 1');
});
