// #1857 (mockup 04): the set and sticker of a disk ("קוריאים · xx-7") beside the disk in the console -- on the machine
// drawer's disk boxes, in the red disks table (so the person knows which disk to pull) and in the drawer's tooltip.
// The server adds `set_text` (null = no set); a disk with no set looks exactly as it did. console.js runs in a vm.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

function setup() {
  const el=()=>({value:'',textContent:'',innerHTML:'',hidden:false,dataset:{},style:{},classList:{add(){},remove(){},toggle(){},contains:()=>false},
    setAttribute(){},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],closest:()=>null,focus(){}});
  const doc={querySelector:()=>el(),querySelectorAll:()=>[],getElementById:()=>el(),
    createElement:()=>el(),documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},body:{appendChild(){}}};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,RegExp,encodeURIComponent,decodeURIComponent,
    document:doc,window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  for(const f of ['progress.js','console.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  return (code)=>vm.runInContext(code,ctx);
}
const run=setup();
const text=(html)=>html.replace(/<[^>]*>/g,' ').replace(/\s+/g,' ');
const MAC='aa:bb:cc:dd:ee:01';
const disk=(serial,port,over={})=>({dev:'sd'+port,port,serial,size_bytes:500107862016,model:'Samsung SSD 870',smart:'ok',...over});

test('the machine drawer shows the set and sticker beside a disk that is in a set, and nothing for one that is not', () => {
  const m={mac:MAC,disks:[disk('S7',1,{set_text:'קוריאים · xx-7'}),disk('S8',2,{set_text:null}),disk('S9',3)]};
  const html=run(`diskBoxesHtml(${JSON.stringify(m)})`);
  assert.equal((html.match(/קוריאים · xx-7/g)||[]).length,1);
  assert.match(html,/<span class="pill info">קוריאים · xx-7<\/span>/);
  assert.equal((html.match(/class="pill info"/g)||[]).length,1,'S8 (null) and S9 (absent) draw no chip');
  const bare=run(`diskBoxesHtml(${JSON.stringify({mac:MAC,disks:[disk('S8',2)]})})`);
  assert.doesNotMatch(bare,/pill info/);
});

test('a set name is text, not markup', () => {
  const html=run(`diskBoxesHtml(${JSON.stringify({mac:MAC,disks:[disk('S7',1,{set_text:'<img src=x onerror=1> · xx-7'})]})})`);
  assert.doesNotMatch(html,/<img/); assert.match(html,/&lt;img/);
});

test('the red disks table says which disk to pull: the set and sticker, from the failure or from the disk', () => {
  run('SHRINK_RECORDS=[]');
  run('MACHINES='+JSON.stringify([{mac:MAC,suffix:'מחשב 2',group_id:'grp_CLONERS',drawer_count:3,disks:[
    disk('S7',1,{set_text:'קוריאים · xx-7'}),disk('S5',2,{smart:'fail',smart_reason:'pending_sectors',set_text:'קוריאים · xx-12'}),disk('S6',3)]}]));
  run('DISK_FAILURES='+JSON.stringify([
    {id:1,mac:MAC,port:1,disk_number:1,serial:'S7',cause:'disk',set_text:'קוריאים · xx-7'},
    {id:2,mac:MAC,port:3,disk_number:3,serial:'S6',cause:'cable',set_text:null}]));
  const t=text(run('diskFailuresCard()'));
  assert.match(t,/קוריאים · xx-7/,'an open failure of a disk in a set');
  assert.match(t,/קוריאים · xx-12/,'a SMART-red disk with no failure record: the name from the disk');
  assert.equal((t.match(/xx-/g)||[]).length,2,'the disk with no set has no name');
});

test('the drawer tooltip names the set after the connection', () => {
  const d=disk('S7',1,{set_text:'קוריאים · xx-7'});
  run('MACHINES='+JSON.stringify([{mac:MAC,suffix:'מחשב 2',group_id:'grp_CLONERS',drawer_count:2,disks:[d]}]));
  const title=run(`diskTitle(MACHINES[0],{n:1,key:'sata:1',disk:MACHINES[0].disks[0],optical:null},MACHINES[0].disks[0])`);
  assert.match(title,/קוריאים · xx-7/);
  const noset=run(`diskTitle(MACHINES[0],{n:1,key:'sata:1',disk:${JSON.stringify(disk('S8',1))},optical:null},${JSON.stringify(disk('S8',1))})`);
  assert.doesNotMatch(noset,/xx-/);
});
