// ‏#1470 (הכרעת נדב 16/10): אחרי ההעלאה מחשב הבנייה משתחרר, והשרת קורא שוב כל קובץ
// מהדיסק לפני שהאימג' נכנס לספרייה. בזמן הזה המשימה `verifying`, והקונסולה מציגה
// "מאמת" עם ההתקדמות של הקריאה החוזרת (`verify_bytes` מתוך `verify_total`, ‏GET /tasks).
// אין ביטול — האימות אינו עוצר באמצע ואינו תופס את מחשב הבנייה. בלי דפדפן: console.js ב-vm.
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
function verifying(over={}) {
  return {id:'tsk_1470',type:'capture',name:'Win11',machine:'HP-BUILD',mac:'cc',disk:'sdb',state:'verifying',stage:'verify',
    bytes_written:146*GB,bytes_total:146*GB,source_progress:null,verify_bytes:58.4*GB,verify_total:146*GB,
    created_at:'2026-10-16T13:25:59Z',updated_at:'2026-10-16T14:10:00Z',...over};
}
function row(html) {
  const m=html.match(/<tr class="task">([^]*?)<\/tr>/);
  assert.ok(m,'the verifying capture is a row in the library table');
  const out=m[1].split(/<td[^>]*>/).slice(1).map(c=>c.replace(/<\/td>$/,''));
  assert.equal(out.length,9,m[1]);
  return {state:out[1],verify:out[6],acts:out[8]};
}

test('#1470: the library table shows a verifying capture as "מאמת" with the re-read progress, and no cancel',()=>{
  const {state,verify,acts}=row(setup([verifying()])('images(0)'));
  assert.match(state,/role="progressbar"[^>]*aria-valuenow="40"/);
  assert.match(state,/מאמת… 40% · אימות/);
  assert.match(state,/נקראת שוב מדיסק השרת/);
  assert.match(verify,/<span class="st run">מאמת<\/span>/);
  assert.doesNotMatch(acts,/cancelCapture/);
});

test('#1470: the captures tab says "מאמת" and draws the verification bar',()=>{
  const html=setup([verifying()])('images(1)');
  assert.match(html,/<span class="st run">מאמת<\/span>/);
  assert.match(html,/aria-valuenow="40"/);
  assert.doesNotMatch(html,/cancelCapture/);
});

test('#1470: without a verification total the bar is not a measured percent of the re-read',()=>{
  const run=setup([]);
  const view=run(`Progress.view(${JSON.stringify(verifying({verify_total:null,verify_bytes:null}))})`);
  assert.doesNotMatch(view.label,/אימות/);
});
