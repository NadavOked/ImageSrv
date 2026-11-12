// #2845 (שרת העבודה, 11/11): שיכפול ישיר (`direct_send`, tsk_2976, image_id=live_e76d8534) הופיע בדף
// האימג'ים כאילו אימג' עולה לשרת. בשיכפול ישיר שום דבר לא נשמר בשרת. `/tasks` מחזיר כל סוג משימה,
// והדף לא סינן לפי `type`. הצפוי: ישיר = סבב בלבד; הדף והפעילות מציגים רק `capture`.
// בלי דפדפן: console.js ב-vm, הפלט כטקסט.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {consoleJs}=require('./console_src.cjs');   // #2594: the console's files in load order
const root=path.resolve(__dirname,'../server/static');

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
  vm.runInContext(consoleJs(),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",capabilities:{}}; current="images";');
  run(`IMAGES=[];FOLDERS=[];OVERVIEW={};CAPTURE_TASKS=${JSON.stringify(tasks)};CAPTURE_TASKS_READ=true`);
  return run;
}
const direct=(over={})=>({id:'tsk_2976',type:'direct_send',name:'HP-BUILD:sda',machine:'HP-BUILD',mac:'cc',disk:'sda',
  image_id:'live_e76d8534',state:'running',bytes_written:0,bytes_total:null,source_progress:null,
  created_at:'2026-11-11T09:00:00Z',...over});
const capture=(over={})=>({id:'tsk_c1',type:'capture',name:'Office 2024',machine:'HP-BUILD',mac:'cc',disk:'sdb',
  image_id:'img_aaaaaa',state:'running',bytes_written:1024**3,bytes_total:null,source_progress:null,
  created_at:'2026-11-11T08:00:00Z',...over});

test('#2845: a running direct send is not a row of the images page, a capture still is',()=>{
  const html=setup([direct(),capture()])('images(0)');
  assert.match(html,/Office 2024/,'the real capture is still shown');
  assert.doesNotMatch(html,/HP-BUILD:sda|live_e76d8534|tsk_2976/);
});

test('#2845: the "קליטות" tab counts and lists captures only',()=>{
  const run=setup([direct(),direct({id:'tsk_2977',state:'pending'}),capture()]);
  assert.match(run('images(0)'),/קליטות[^]{0,80}?>1</,'tab count is the one capture');
  const card=run('imagesCapturesCard()');
  assert.match(card,/Office 2024/);
  assert.doesNotMatch(card,/HP-BUILD:sda/);
  assert.equal(run('capturesOnly(CAPTURE_TASKS).length'),1);
});

test('#2845: a finished direct send with an error is not announced as a capture warning',()=>{
  const run=setup([direct({state:'done',error:'x'})]);
  assert.doesNotMatch(run('images(0)'),/note warn/);
});

test('#2845: the home activity names no direct send as a capture, and still names a capture',()=>{
  const run=setup([direct(),capture()]);
  run('HOME={err:{},transfers:[]}');
  const rows=JSON.stringify(run('homeNowRows(true)'));
  assert.match(rows,/קליטת אימג[^]{0,6}— Office 2024/);
  assert.doesNotMatch(rows,/HP-BUILD:sda/);
});
