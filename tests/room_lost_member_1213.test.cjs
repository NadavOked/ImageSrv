// #1912 / #1213: a room machine the server gave up on (member state "lost", #450) must not
// look like one that is still writing. The card showed "בסבב · 63%" -- the last percent -- as
// if the copy were running. "lost" = we do not know whether anything was written (principle 5).
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

const C1='a0:48:1c:8a:18:40';
function setup(member) {
  const node=()=>({value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
    classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
    querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(){},setAttribute(){},removeAttribute(){},focus(){}});
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Error,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:node,
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  for(const f of ['progress.js',...consoleFiles(),'net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run(`ME={username:"admin",role:"admin",server_name:"srv",version:"v0.0.0",capabilities:{classrooms:true}};`);
  const room={round:{id:'rnd_1',image_id:'img_1',target_drives:2,written_drives:0,remaining_drives:2,wave_number:1,wave_state:'running',ready_drives:2,opened_by:'nadav',source:{kind:'library'}},
    stream_stalled:false,disk_floor:null,machines:[{mac:C1,name:'מחשב 1',drawer_count:1,awake:false,drawers:1,fresh_drawers:1,drawer_list:[],joined:true,error:null,
      bytes_written:63,bytes_total:100,state:member}]};
  run('ROOM='+JSON.stringify(room)+'; MACHINES=[{mac:"'+C1+'",suffix:"מחשב 1",group_id:"grp_CLONERS",disks:null}]; GROUPS=[{id:"grp_CLONERS",label:"x",role:"cloner",sort:1,machines:1}];');
  return run;
}

test('a writing member shows its progress (the control: the percent is real while it writes)', () => {
  const run=setup('running');
  const st=run(`roomCardState(MACHINES[0])`);
  assert.match(st.text+(st.round||''),/63%/);
});

test('a "lost" member does not show the last percent as if it were still writing', () => {
  const run=setup('lost');
  const st=run(`roomCardState(MACHINES[0])`);
  const shown=JSON.stringify(st);
  assert.doesNotMatch(shown,/63%/,'the last percent of a machine the server gave up on is shown as live progress: '+shown);
  assert.notEqual(st.cls,'run','a lost machine is not a running one: '+shown);
});

test('#2603: a "lost" member is an error in Hebrew on the card, the cloner row and the machine state', () => {
  const run=setup('lost');
  const st=run(`roomCardState(MACHINES[0])`);
  assert.equal(st.cls,'err',JSON.stringify(st));
  assert.match(st.text,/אבד הקשר/);
  assert.notEqual(st.text,'מחובר');
  const cs=run(`clonerState(MACHINES[0])`);
  assert.equal(cs.cls,'err');
  assert.match(cs.text,/אבד הקשר/);
});

test('#2603: a lost session member row is red and shows no percent', () => {
  const run=setup('lost');
  const html=run(`memberRow({mac:"${C1}",state:"lost",bytes_written:63,bytes_total:100,done:false},{kind:"multicast"})`);
  assert.match(html,/member failed/);
  assert.match(html,/<span class="pct">אבד הקשר<\/span>/,'the percent label must not show the last percent of a lost member');
  assert.match(html,/<div class="err">אבד הקשר/);
});

test('#2390: the progress bar of a lost member is stopped and carries no live value for a screen reader', () => {
  const run=setup('lost');
  const lost=run(`Progress.bar({state:"lost",bytes_written:63,bytes_total:100})`);
  assert.match(lost,/class="bar determinate stopped"/,lost);
  assert.doesNotMatch(lost,/aria-valuenow/,'a screen reader would read the last percent as live progress: '+lost);
  assert.doesNotMatch(lost,/aria-label="63%"/,lost);
  const row=run(`memberRow({mac:"${C1}",state:"lost",bytes_written:63,bytes_total:100,done:false},{kind:"multicast"})`);
  assert.match(row,/class="bar determinate stopped"/,row);
  assert.doesNotMatch(row,/aria-valuenow/,row);
});

test('#2390 guard: a running member keeps its live value, a failed one stays stopped with its value', () => {
  const run=setup('running');
  const live=run(`Progress.bar({state:"running",bytes_written:63,bytes_total:100})`);
  assert.match(live,/class="bar determinate"/);
  assert.match(live,/aria-valuenow="63"/);
  const failed=run(`Progress.bar({state:"failed",bytes_written:63,bytes_total:100})`);
  assert.match(failed,/determinate stopped/);
  assert.match(failed,/aria-valuenow="63"/);
});
