// ‏#1254 (נמדד על ברזל 10/10, קליטה tsk_749e מ-HP-BUILD): שורת הקליטה בדף
// האימג'ים הציגה בעמודת "אימות" פס ריק ו"נקראו בייטים · הסך לא ידוע", אחרי
// ‏50.8GB שכבר נכתבו. "אימות" הוא sha256 — אין מה לאמת לפני סוף הקליטה.
// ההתקדמות עוברת לעמודת "בשימוש" (המצב של השורה): "נקלט… N% · מחיצה K מתוך M",
// ובלי מכנה — כמה נכתב לשרת, במספר. בלי דפדפן: console.js ב-vm, הפלט כטקסט.
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const GB=1024**3;

function setup(tasks) {
  const nodes=new Map();
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false})},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx);
  for (const f of consoleFiles()) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",capabilities:{}}; current="images";');
  run(`IMAGES=[];FOLDERS=[];OVERVIEW={};CAPTURE_TASKS=${JSON.stringify(tasks)}`);
  return run;
}
function capture(over={}) {
  return {id:'tsk_749e',type:'capture',name:'Vm',machine:'HP-BUILD',mac:'cc',disk:'sdb',state:'running',
    bytes_written:50.8*GB,bytes_total:null,source_progress:null,created_at:'2026-10-10T13:25:59Z',...over};
}
/* תאי שורת הקליטה, לפי סדר העמודות (console-full.html imgCols, ‏#1300): ‏["", שם, מערכת, גודל, מתאים לדיסק, תיקייה, מצב, נקלט, פעולות].
   ‏"בשימוש" ירדה מהטבלה (#1300 — היא בראש מגירת האימג'). ההתקדמות יושבת מתחת לשם (תא 1) — כך היא נשארת
   גלויה גם כשהעמודות המשניות (מערכת/תיקייה/נקלט) מוסתרות בטבלה צרה. "אימות" = עמודת "מצב" (sha256). */
function cells(task) {
  const html=setup([task])('images(0)');
  const row=html.match(/<tr class="task">([^]*?)<\/tr>/);
  assert.ok(row,'the capture row is in the table');
  const out=row[1].split(/<td[^>]*>/).slice(1).map(c=>c.replace(/<\/td>$/,''));
  assert.equal(out.length,9,row[1]);
  return {verify:out[6],state:out[1]};
}

test('#1254: "אימות" carries no read progress while capturing — sha256 is checked when the image enters the library',()=>{
  for(const task of [capture(),capture({state:'pending',bytes_written:0}),
    capture({source_progress:{partition:3,blocks_read:370,blocks_total:1000,partition_seq:3,partitions_total:4}})]) {
    const {verify}=cells(task);
    assert.doesNotMatch(verify,/progressbar|נקראו|הסך לא ידוע|%/,task.state);
    assert.match(verify,/בסוף הקליטה/,task.state);
  }
});

test('#1254: the state column says "נקלט… N% · מחיצה K מתוך M" with a determinate bar',()=>{
  const {state}=cells(capture({source_progress:{partition:3,blocks_read:370,blocks_total:1000,partition_seq:3,partitions_total:4}}));
  assert.match(state,/role="progressbar"[^>]*aria-valuenow="37"/);
  assert.match(state,/נקלט… 37% · מחיצה 3 מתוך 4/);
});

test('#1254: with no denominator the state column names what was written, with a number — never "bytes were read" alone',()=>{
  const {state}=cells(capture());
  assert.match(state,/נקלט… <bdi dir="ltr">50\.8 GB<\/bdi> נכתבו לשרת · הסך לא ידוע/);
  // הטקסט הגלוי. ה-aria-label של הפס הוא Progress.view המשותף (מסך הבנייה הנייטיבי מעתיק אותו).
  assert.doesNotMatch(state.replace(/<div class="bar[^>]*>/,''),/נקראו בייטים/);
  assert.match(state,/indeterminate/,'unknown is not drawn as a measured 0%');
});

test('#1254: K of M only when the agent sent a consistent pair; otherwise the partition index, as before',()=>{
  for(const extra of [{},{partition_seq:5,partitions_total:4},{partition_seq:0,partitions_total:4},{partition_seq:'3',partitions_total:4}]) {
    const {state}=cells(capture({source_progress:{partition:3,blocks_read:370,blocks_total:1000,...extra}}));
    assert.match(state,/נקלט… 37% · מחיצה 3 \(בלוקים\)/,JSON.stringify(extra));
    assert.doesNotMatch(state,/מתוך/,JSON.stringify(extra));
  }
});

test('#1254: a capture waiting for PXE is "ממתין" in the state column, with the idle bar',()=>{
  const {state}=cells(capture({state:'pending',bytes_written:0}));
  assert.match(state,/<span class="st run">ממתין<\/span>/);
  assert.match(state,/unknown-idle/);
});
