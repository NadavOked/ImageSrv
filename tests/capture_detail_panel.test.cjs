// #1505 — פאנל "פירוט קליטה" בקונסולה: מחיצות, קצב, שלב, דחיסה, זמן שעבר ושנותר.
// עיקרון 5: מה שלא נמדד נאמר במילים ("עוד לא נמדד", "לא נמדד") ולא נראה כאפס. בלי דפדפן:
// progress.js + console.js + capture_detail.js ב-vm מול DOM מזערי.
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const GB=1024**3, MAC='c8:d9:d2:0b:fe:32';

function setup() {
  const nodes=new Map();
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){},contains(){return false}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},setAttribute(){},removeAttribute(){},getAttribute(){return null;},focus(){}});
    return nodes.get(key);
  }
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  for(const f of ['progress.js',...consoleFiles(),'net.js','capture_detail.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",idle_seconds:600,version:"v0.74.2",capabilities:{}}; globalThis.toasts=[]; toast=(m)=>{ toasts.push(m); }; globalThis.drawers=[]; openDrawer=(t,b)=>{ drawers.push(t+"|"+b); };');
  run(`MACHINES=[{mac:"${MAC}",suffix:"בנייה 1",group_id:"grp_BUILD",disks:[]}]; GROUPS=[{id:"grp_BUILD",label:"מחשבי בנייה",role:"build"}]; NET=[]; DISK_FAILURES=[]; SHRINK_RECORDS=[]; MONITOR_ROWS=[]; CAPTURE_TASKS_READ=true;`);
  return {run,node};
}
const part=(index,state,used,over={})=>({index,role:'windows',fs:'ntfs',state,size_bytes:used==null?200*GB:used*2,used_bytes:used,done_bytes:state==='done'?used:0,...over});
function detail(over={}) {
  return {partitions:[part(1,'done',90*1024**2,{role:'esp',fs:'vfat',size_bytes:100*1024**2}),
    part(3,'reading',100*GB,{done_bytes:40*GB}),part(4,'waiting',20*GB,{role:'data'})],
    compression:'zstd-3',read_bytes:40*GB+90*1024**2,total_used_bytes:120*GB+90*1024**2,rate_bps:90*1024**2,eta_s:900,elapsed_s:1500,...over};
}
function task(over={}) {
  return {id:'tsk_1',type:'capture',name:'Office 2025',machine:'בנייה 1',mac:MAC,disk:'sda',state:'running',bytes_written:18*GB,bytes_total:null,
    source_progress:{partition:3,blocks_read:400,blocks_total:1000},stage:'stream',stage_started_at:'2026-10-16T10:00:00Z',
    created_at:'2026-10-16T09:50:00Z',updated_at:'2026-10-16T10:25:00Z',created_by:'admin',capture_detail:detail(),...over};
}
const html=(t)=>{const s=setup(); return s.run(`captureDetailHtml(${JSON.stringify(t)})`);};

test('#1505: every partition shows role, fs, used / size and its state; the one being read has a bar',()=>{
  const h=html(task());
  assert.match(h,/ESP/); assert.match(h,/vfat/); assert.match(h,/Windows/); assert.match(h,/ntfs/);
  assert.match(h,/100 GB/,'used'); assert.match(h,/200 GB/,'size');
  for(const w of ['הושלם','נקרא','ממתין']) assert.ok(h.includes(w),w);
  assert.match(h,/--w:40%/,'40 of 100 GB read = 40% on the partition bar');
  assert.match(h,/--w:100%/,'a finished partition is full');
});

test('#1505: a partition whose used space was not measured says so — never 0',()=>{
  const h=html(task({capture_detail:detail({partitions:[part(1,'waiting',null)],total_used_bytes:null,read_bytes:null,eta_s:null})}));
  assert.match(h,/לא נמדד/); assert.doesNotMatch(h,/\b0 B\b/);
});

test('#1505: rate and ETA are words until measured, with the reason; the numbers when measured',()=>{
  let h=html(task({capture_detail:detail({rate_bps:null,eta_s:null})}));
  assert.match(h,/עוד לא נמדד/); assert.doesNotMatch(h,/\d+(\.\d+)? [KMGT]?B\/s/,'no rate was invented, and not a zero one');
  h=html(task({capture_detail:detail({rate_bps:null,eta_s:null,total_used_bytes:null,read_bytes:null})}));
  assert.match(h,/לא כל המחיצות נמדדו/,'the total is unknown, and the page says why there is no time left');
  h=html(task());
  assert.match(h,/90\.0 MB\/s/); assert.match(h,/15 דק&#39;/,'eta 900s'); assert.match(h,/25 דק&#39;/,'elapsed 1500s');
});

test('#1505: the big number is the share of the used space when it is known, else the compressed bytes written',()=>{
  assert.match(html(task()),/33%/,'40.09 of 120.09 GB');
  const h=html(task({capture_detail:detail({total_used_bytes:null,read_bytes:null})}));
  assert.match(h,/18\.0 GB/); assert.match(h,/הסך לא ידוע/); assert.doesNotMatch(h,/\b0%/);
});

test('#1505: compression shows the algorithm and, when measurable, written out of read',()=>{
  const h=html(task());
  assert.match(h,/zstd-3/); assert.match(h,/נכתבו[^]*18\.0 GB[^]*שנקראו/);
  assert.match(html(task({capture_detail:detail({compression:null,read_bytes:null})})),/לא דווחה/);
});

test('#1505: the stage chain is in the panel, and a shrinking partition is labelled as such',()=>{
  const h=html(task({stage:'shrink',capture_detail:detail({partitions:[part(3,'shrinking',100*GB),part(4,'waiting',20*GB)]})}));
  assert.match(h,/<b>כיווץ<\/b>/); assert.match(h,/מכווץ/);
});

test('#1505: an old agent (no table) gets a note and the bytes written — not an empty table',()=>{
  const h=html(task({capture_detail:null}));
  assert.match(h,/לא דיווח פירוט מחיצות/); assert.doesNotMatch(h,/<table/); assert.match(h,/18\.0 GB/);
});

test('#1505: a task still waiting for PXE has no numbers to show',()=>{
  const h=html(task({state:'pending',capture_detail:null,bytes_written:0}));
  assert.match(h,/יעלה ב-PXE/); assert.doesNotMatch(h,/<table|kpi/);
});

test('#1505: server strings are escaped; the failure reason is shown',()=>{
  const h=html(task({state:'failed',error:'<img onerror=x>',capture_detail:detail({partitions:[part(3,'reading',GB,{role:'<b>',fs:'a"b'})]})}));
  assert.doesNotMatch(h,/<img onerror|<td><b>/); assert.match(h,/&lt;img/); assert.match(h,/&lt;b&gt;/); assert.match(h,/a&quot;b/);
});

test('#1505: the task dock row and the build machine card open the panel',()=>{
  // #1485 replaced the separate "capture in progress" card with one card per
  // build machine, which shows the running capture itself -- the button lives there.
  const s=setup();
  s.run(`CAPTURE_TASKS=${JSON.stringify([task()])}; OVERVIEW={images:1,machines:1,storage:null,session:null,room:null,pulls:[]}`);
  const rows=Array.from(s.run('activityRows().rows')).filter((r)=>r.name==='Office 2025');
  assert.equal(rows.length,1); assert.equal(rows[0].open,"openCaptureDetail('tsk_1')");
  const card=s.run(`builderCardHtml(findMachine('${MAC}'), true)`);
  assert.match(card,/onclick="openCaptureDetail\('tsk_1'\)">פירוט קליטה</);
  s.run('CAPTURE_TASKS=[]');
  assert.doesNotMatch(s.run(`builderCardHtml(findMachine('${MAC}'), true)`),/openCaptureDetail/,'no capture, no detail button');
});

test('#1505: opening the panel fills the drawer; the 2s refresh updates it in place and leaves another drawer alone',()=>{
  const s=setup();
  s.run(`CAPTURE_TASKS=${JSON.stringify([task()])}`);
  s.run("openCaptureDetail('tsk_1')");
  assert.equal(s.run('drawers.length'),1); assert.match(s.run('drawers[0]'),/^קליטה: Office 2025\|/);
  // the open drawer is the capture panel: a refresh re-renders with the new snapshot
  s.run('globalThis.bodies=[]; setDrawerBody=(h)=>{ bodies.push(h); }');
  const el=s.node('#drawerBody'); el.querySelector=()=>({getAttribute:()=>'tsk_1'});
  s.run(`CAPTURE_TASKS=${JSON.stringify([task({capture_detail:detail({rate_bps:123456})})])}; capDetailRefresh()`);
  assert.equal(s.run('bodies.length'),1); assert.match(s.run('bodies[0]'),/120\.6 KB\/s|121 KB\/s|120\.6 KB/);
  // another drawer took over: no write, and the panel forgets the task
  el.querySelector=()=>null; s.run('capDetailRefresh()');
  assert.equal(s.run('bodies.length'),1); assert.equal(s.run('CAPDETAIL_ID'),null);
  s.run("openCaptureDetail('nope')"); assert.match(s.run('toasts.join("|")'),/לא נמצאה/);
});

test('#1505: index.html loads the panel after console.js, with the same ?v= as everything else',()=>{
  const page=fs.readFileSync(path.join(root,'index.html'),'utf8');
  const at=(n)=>page.indexOf(`<script src="${n}?v=`);
  assert.ok(at('capture_detail.js')>at('console.js')&&at('console.js')>0);
  assert.equal(new Set(page.match(/\?v=[\d.]+"/g)).size,1);
});
