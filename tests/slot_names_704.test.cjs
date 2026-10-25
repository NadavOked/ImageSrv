// ‏#704: שם לכל חיבור של כונן במחשב השיכפול (נדב, 11/10): "שיראה לי לאיזה
// חריצים מחוברים כוננים ... ואז אני אתן שם לכל התקן" + "NVMe ... 'דיסק מיוחד'".
// המפתח: sata:<port> (השם הולך עם החריץ), serial:<serial> לכונן בלי port.
// בלי שם — התצוגה כמו היום ("דיסק N", SATA 0 → דיסק 1). השם הוא תווית בלבד.
// בלי דפדפן: console.js + net.js + progress.js ב-vm מול DOM מזערי ו-fetch מזויף.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const GB256=256060514304;
const C1='a0:48:1c:8a:18:40', C2='78:ac:c0:9b:11:c2';
const disk=(dev,port,serial,extra={})=>({dev,port,serial,model:'870 EVO',size_bytes:GB256,smart:'ok',...extra});
const drawer=(dev,port,serial,extra={})=>({dev,port,serial,model:'870 EVO',size_bytes:GB256,fresh:true,selected:false,state:null,error:null,bytes_written:0,bytes_total:0,stalled_s:null,crc_delta:null,smart:'ok',...extra});

function setup({names2={'sata:2':'האמצעית','serial:NV-1':'דיסק מיוחד'},names1={'sata:2':'תחתונה'},round=false,admin=true}={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const c2disks=[disk('sda',1,'S-1'),disk('sdb',3,'S-3'),disk('nvme0n1',null,'NV-1'),disk('sdc',null,null,{model:'USB stick',removable:true})];
  const fixtures={
    '/groups':[{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:2,machines:2}],
    '/machines':[
      {mac:C1,suffix:'מחשב 1',group_id:'grp_CLONERS',note:null,drawer_count:2,prompt:null,disks:[],disks_reported_at:'2026-10-11T08:00:00Z',inventory:null,inventory_seen_at:null,slot_names:names1},
      {mac:C2,suffix:'מחשב 2',group_id:'grp_CLONERS',note:null,drawer_count:3,prompt:null,disks:c2disks,disks_reported_at:'2026-10-11T08:00:00Z',inventory:null,inventory_seen_at:null,slot_names:names2}],
    '/net':[], '/disk-failures':[], '/shrink-records':[], '/monitor/machines':[], '/tasks':[], '/room/history':[], '/journal':[],
    '/room':{round:round?{id:'rnd_1',image_id:'img_1',image_name:'office365',target_drives:3,written_drives:0,remaining_drives:3,wave_number:1,wave_state:'running',ready_drives:3,opened_by:'nadav',source:null}:null,stream_stalled:false,machines:[
      {mac:C1,name:'מחשב 1',drawer_count:2,awake:true,drawers:0,fresh_drawers:0,drawer_list:[],joined:false,state:null,bytes_written:0,bytes_total:0,error:null,slot_names:names1},
      {mac:C2,name:'מחשב 2',drawer_count:3,awake:true,drawers:2,fresh_drawers:2,joined:round,state:round?'running':null,bytes_written:0,bytes_total:0,error:null,slot_names:names2,
        drawer_list:[drawer('sda',1,'S-1',round?{state:'writing',bytes_written:10,bytes_total:100}:{}),drawer('sdb',2,'S-9',round?{state:'writing',bytes_written:10,bytes_total:100}:{})]}]},
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] ?? {ok:true}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run(`ME={username:"nadav",role:"${admin?'admin':'deploy'}",server_name:"srv",version:"v0.67.1",capabilities:{}}; current="machines"; sheet = o => { globalThis.formOptions=o; }; toast=(m)=>{ globalThis.toasted=m; }; renderCurrent=()=>{}; selectPageById=(id)=>{ current=id; }; openDrawer=(t,h)=>{ globalThis.drawerHtml=h; }; setDrawerBody=(h)=>{ globalThis.drawerHtml=h; };`);
  for(const [name,key] of [['MACHINES','/machines'],['GROUPS','/groups'],['DISK_FAILURES','/disk-failures'],['SHRINK_RECORDS','/shrink-records'],['NET','/net'],['MONITOR_ROWS','/monitor/machines'],['ROOM','/room'],['ROOM_HISTORY','/room/history'],['CAPTURE_TASKS','/tasks']]) run(name+'='+JSON.stringify(fixtures[key]));
  run('CAPTURE_TASKS_READ=true; ROOM_KEY=JSON.stringify(ROOM);');
  return {run,requests,fixtures};
}
// ‏#1485: the cloner page is the mockup's .mach card — a row per slot: <span>label</span> · bar · <b>status</b>
const card=(html,mac)=>{ const i=html.lastIndexOf('<div class="mach',html.indexOf(`data-mac="${mac}"`)); return html.slice(i).split(/(?=<div class="mach[ "])/)[0]; };
const slot=(html,n)=>{ const m=html.match(new RegExp(`<div class="dsk[^"]*" data-slot="${n}"[^>]*>[^]*?</b></div>`)); assert.ok(m,'slot '+n); return m[0]; };
const diskTab=(html)=>{ const i=html.indexOf('">דיסקים</div>'); assert.ok(i>=0,'no disks section'); const to=html.indexOf('<div class="sec"',i); return html.slice(i,to<0?undefined:to); };

test('grid: a named slot shows its name; an unnamed slot stays "דיסק N"; the number survives in the tooltip', () => {
  const {run}=setup();
  run("openClass('grp_CLONERS')"); const html=run('machines(0)');
  const c2=card(html,C2);
  assert.match(slot(c2,1),/<span>דיסק 1<\/span>/,'no name → as today');
  assert.match(slot(c2,3),/<span>דיסק 3<\/span>/);
  const c1=card(html,C1);
  assert.match(slot(c1,2),/<span>תחתונה<\/span><div class="bar"><i style="--w:0%"><\/i><\/div><b>ריק<\/b>/,'an empty slot keeps its name — the name is the slot, not the disk');
  assert.match(slot(c1,2),/title="דיסק 2 · SATA 1 · ריק"/,'the tooltip still says the number');
  assert.match(slot(c1,1),/<span>דיסק 1<\/span>[^]*<b>ריק<\/b>/);
});

test('grid during a round: the name follows the slot — a new disk (other serial) in SATA 1 is still "האמצעית"', () => {
  const {run}=setup({round:true});
  run("openClass('grp_CLONERS')"); const html=run('machines(0)');
  const s2=slot(card(html,C2),2);
  assert.match(s2,/<span>האמצעית<\/span>/);
  assert.match(s2,/title="דיסק 2 · SATA 1 · 870 EVO · S-9/,'named slot: the tooltip opens with "דיסק N"');
});

test('a name is escaped, never HTML', () => {
  const {run}=setup({names2:{'sata:1':'a<b>&"'}});
  run("openClass('grp_CLONERS')"); const html=run('machines(0)');
  assert.match(slot(card(html,C2),1),/<span>a&lt;b&gt;&amp;&quot;<\/span>/);
});

test('drawer disk table: drawers 1..N by the mapping, then drives outside it (named NVMe keeps its name); naming is in the #2001 window', () => {
  const {run}=setup();
  run(`openMachineDetail('${C2}')`); const html=run('drawerHtml');
  const sec=diskTab(html);
  assert.doesNotMatch(sec,/>S-1<|>S-3<|>NV-1</,'serials stay out of the table');
  const rows=[...sec.matchAll(/<tr class="([^"]*)" data-slot="([^"]*)">([^]*?)<\/tr>/g)].map(m=>[m[1],m[2],m[3]]);
  assert.deepEqual(rows.map(r=>[r[0],r[1]]),[['','1'],['vacant','2'],['','3'],['unmapped',''],['unmapped','']],'SATA 0..2 by the convention (2 empty), then NVMe and USB, which have no connection key');
  const [s0,s1,s2,nv,usb]=rows.map(r=>r[2]);
  assert.match(s0,/<b>דיסק 1<\/b>/); assert.match(s0,/title="SATA 0">דיסק 1</); assert.match(s0,/256GB[^]*870/);
  assert.match(s1,/<b>האמצעית<\/b>/); assert.match(s1,/title="SATA 1">דיסק 2</); assert.match(s1,/ריק/);
  assert.match(s2,/<b>דיסק 3<\/b>/); assert.match(s2,/title="SATA 2">דיסק 3</);
  assert.match(nv,/<b>דיסק מיוחד \(לא ממופה\)<\/b>/,'#704 name kept; no number guessed'); assert.match(nv,/NVMe — אין כתובת PCI/);
  assert.match(usb,/<b>USB \(לא ממופה\)<\/b>/);
  assert.doesNotMatch(sec,/renameSlot/,'#2001: one window names the connections');
  assert.match(sec,new RegExp(`openSlotMap\\('${encodeURIComponent(C2)}'\\)`));
});

test('drawer disk table: marketing size and vendor; SMART is a chip in its own column (mockup: תקין / נכשל)', () => {
  const {run}=setup();
  run(`MACHINES[1].disks=[
    {dev:'sda',port:1,serial:'a',model:'SAMSUNG MZ7LN256',size_bytes:238*1024**3,smart:'ok'},
    {dev:'sdb',port:2,serial:'b',model:'WESTERN WD500',size_bytes:466*1024**3,smart:'warn'},
    {dev:'sdc',port:3,serial:'c',model:'KINGSTON X',size_bytes:931*1024**3,smart:'fail'}];`);
  const html=diskTab(run(`machineDrawerHtml(findMachine('${C2}'))`));
  assert.match(html,/256GB<\/bdi><\/span> <span class="cap">Samsung<\/span><\/td><td><span class="n-chip">תקין<\/span>/);
  assert.match(html,/500GB<\/bdi><\/span> <span class="cap">Western<\/span><\/td><td><span class="n-chip w">אזהרה<\/span>/);
  assert.match(html,/1TB<\/bdi><\/span> <span class="cap">Kingston<\/span><\/td><td><span class="n-chip d">SMART תקול<\/span>/,'#1580: SMART fail is red');
});

test('build machines use the same table and mapping, and the same window', () => {
  const {run}=setup();
  run(`GROUPS[0].role='build'`);
  const html=diskTab(run(`machineDrawerHtml(findMachine('${C2}'))`));
  assert.match(html,/<th>דיסק<\/th><th>חיבור<\/th><th>גודל<\/th><th>SMART<\/th>/);
  assert.match(html,/title="SATA 0">דיסק 1</); assert.match(html,/256GB[^]*870/); assert.match(html,/openSlotMap\(/,'#1485/#2001: the mapping for build machines too');
  assert.doesNotMatch(html,/renameSlot/);
});

test('the drawer for a non-admin has no mapping button, and the window does not open', () => {
  const {run}=setup({admin:false});
  const html=run(`machineDrawerHtml(findMachine('${C2}'))`);
  assert.ok(html.includes('דיסקים'));
  assert.doesNotMatch(html,/renameSlot|openSlotMap/);
  run(`openSlotMap('${encodeURIComponent(C2)}')`);
  assert.equal(run('SMAP'),null,'no window for a non-admin');
});

test('without any names everything reads exactly as before', () => {
  const {run}=setup({names1:{},names2:{}});
  run("openClass('grp_CLONERS')"); const html=run('machines(0)');
  for(const n of [1,2,3]) assert.match(slot(card(html,C2),n),new RegExp(`<span>דיסק ${n}</span>`));
  assert.match(slot(card(html,C1),2),/title="דיסק 2 · SATA 1 · ריק"/);
  const {run:run2}=setup({names1:{},names2:{}});
  // old server: no slot_names field at all
  run2('MACHINES.forEach(m=>delete m.slot_names)');
  run2("openClass('grp_CLONERS')");
  assert.match(slot(card(run2('machines(0)'),C2),1),/<span>דיסק 1<\/span>/);
});
