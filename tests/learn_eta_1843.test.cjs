// #1843 — זמן סיום צפוי לפי היסטוריה: הקונסולה מציגה את ההערכה מהשרת, ו"אין עדיין הערכה" כשאין.
// הקצב שנמדד עכשיו (eta_s) גובר — ההערכה מתעדכנת לפי הקצב בפועל. בלי דפדפן: vm מול DOM מזערי.
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
  for(const f of ['progress.js','console.js','net.js','capture_detail.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",idle_seconds:600,version:"v0.74.2",capabilities:{}}; globalThis.toasts=[]; toast=(m)=>{ toasts.push(m); }; globalThis.drawers=[]; openDrawer=(t,b)=>{ drawers.push(t+"|"+b); };');
  run(`MACHINES=[{mac:"${MAC}",suffix:"בנייה 1",group_id:"grp_BUILD",disks:[]}]; GROUPS=[{id:"grp_BUILD",label:"מחשבי בנייה",role:"build"}]; NET=[]; DISK_FAILURES=[]; SHRINK_RECORDS=[]; MONITOR_ROWS=[]; CAPTURE_TASKS_READ=true;`);
  return {run,node};
}

const s=setup();
const fc=(f,live)=>s.run(`forecastText(${JSON.stringify(f)}, ${JSON.stringify(live)})`);

test('#1843: no history is said in words, never a number',()=>{
  const h=fc({seconds:null,ends_at:null,why:'no_history'},null);
  assert.match(h,/אין עדיין הערכת סיום/); assert.doesNotMatch(h,/\d/);
});

test('#1843: an open wave shows the learned length, a running one the end time',()=>{
  assert.match(fc({seconds:1259,ends_at:null,why:null},null),/לפי היסטוריה: כ-20 דק/);
  assert.match(fc({seconds:600,ends_at:'2026-10-21T10:10:00+00:00',why:null},null),/צפוי להסתיים ב-.*\d\d:\d\d.*\(לפי היסטוריה\)/);
});

test('#1843: the measured rate wins over history',()=>{
  const h=fc({seconds:600,ends_at:'2026-10-21T10:10:00+00:00',why:null},120);
  assert.match(h,/צפוי להסתיים ב-/); assert.doesNotMatch(h,/לפי היסטוריה/);
});

test('#1843: the capture panel falls back to the history estimate only while the rate is unmeasured',()=>{
  const t={id:'tsk_1',type:'capture',name:'x',machine:'m',mac:MAC,disk:'sda',state:'running',bytes_written:1,bytes_total:null,
    created_at:'2026-10-16T09:50:00Z',updated_at:'2026-10-16T10:25:00Z',created_by:'admin',
    capture_detail:{partitions:[],compression:null,read_bytes:1,total_used_bytes:10*GB,rate_bps:null,eta_s:null,elapsed_s:5},
    forecast:{seconds:900,ends_at:'2026-10-16T10:05:00+00:00',why:null}};
  assert.match(s.run(`capEta(${JSON.stringify(t)}, ${JSON.stringify(t.capture_detail)})`),/לפי היסטוריה/);
  t.forecast={seconds:null,ends_at:null,why:'no_history'};
  assert.match(s.run(`capEta(${JSON.stringify(t)}, ${JSON.stringify(t.capture_detail)})`),/עוד לא נמדד/);
});
