// #2847: מהירות קישור הרשת שנמדדה (/machines.nic_link_mbps) בכרטיס המכונה, במסך המכונה ובפתיחת סבב.
// מתחת ל-1000 = כתום עם "כבל או פורט בסוויץ'"; null = לא נמדד = שום דבר (עיקרון 5: "לא ידוע" אינו "תקין").
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {consoleJs}=require('./console_src.cjs');   // #2594: the console's files in load order
const root=path.join(path.resolve(__dirname,'..'),'server/static');

function setup() {
  const nodes=new Map();
  const node=(key)=>{
    if(!nodes.has(key)) nodes.set(key,{value:'',textContent:'',innerHTML:'',hidden:false,dataset:{},style:{},classList:{add(){},remove(){},toggle(){},contains:()=>false},
      setAttribute(){},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],closest:()=>null,focus(){}});
    return nodes.get(key);
  };
  const doc={querySelector:()=>node('?'),querySelectorAll:()=>[],getElementById:(id)=>node('#'+id),
    createElement:()=>node('new'),documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},body:{appendChild(){}}};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,RegExp,Error,encodeURIComponent,decodeURIComponent,
    document:doc,window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx); vm.runInContext(consoleJs(),ctx);
  const run=(code)=>vm.runInContext(code,ctx);
  run(`ME={username:"admin",role:"admin",server_name:"srv",version:"v0",capabilities:{}}; current="machines";`);
  return {run};
}

const WARN="קישור 100Mb/s — כבל או פורט בסוויץ'";
const mk=(mbps,extra='')=>`({mac:"aa:bb:cc:00:00:01",name:"m1",nic_link_mbps:${mbps}${extra}})`;

test('below 1000 is an orange pill and a warning note with the cable/switch-port reason', () => {
  const {run}=setup();
  assert.equal(run(`nicLinkSlow(${mk(100)})`),true);
  assert.match(run(`nicLinkMeta(${mk(100)})`),/pill warn/);
  const note=run(`nicLinkNote(${mk(100)})`);
  assert.match(note,/note warn/);
  assert.ok(note.includes(WARN.replace("'","&#39;"))||note.includes(WARN),note);
  assert.ok(run(`nicLinkStatus(${mk(100)})`).includes('warn'));
});

test('1000 and above is shown plainly, never orange', () => {
  const {run}=setup();
  for(const v of [1000,2500,10000]) {
    assert.equal(run(`nicLinkSlow(${mk(v)})`),false);
    assert.doesNotMatch(run(`nicLinkMeta(${mk(v)})`),/warn/);
    assert.equal(run(`nicLinkNote(${mk(v)})`),'');
  }
  assert.match(run(`nicLinkMeta(${mk(1000)})`),/קישור 1Gb\/s/);
});

test('an unmeasured link (null / missing / junk) shows nothing orange and is not called OK', () => {
  const {run}=setup();
  for(const v of ['null','undefined','0','-1','"100"']) {
    assert.equal(run(`nicLinkSlow(${mk(v)})`),false,v);
    assert.equal(run(`nicLinkMeta(${mk(v)})`),'',v);
    assert.equal(run(`nicLinkNote(${mk(v)})`),'',v);
    assert.match(run(`nicLinkStatus(${mk(v)})`),/לא נמדד/,v);
  }
});

test('the cards carry it: cloner card, builder card and the machine detail', () => {
  const {run}=setup();
  run(`netFor=()=>null; machineSlots=()=>({slots:[],declared:true}); clonerState=()=>({text:"דלוק",cls:"ok"});`);
  assert.match(run(`clonerCardHtml(${mk(100)}, true)`),/pill warn[^>]*>קישור 100Mb\/s/);
  assert.match(run(`clonerCardHtml(${mk(100)}, true)`),/note warn/);
  assert.doesNotMatch(run(`clonerCardHtml(${mk(1000)}, true)`),/note warn[^]*כבל או פורט/);
  assert.match(run(`builderCardHtml(${mk(100)}, true)`),/<dt>קישור<\/dt><dd>[^]*warn/);
});

test('round-open warning: names the slow machine and estimates time = compressed size / slowest rate', () => {
  const {run}=setup();
  run(`MACHINES=[${mk(100)}, {mac:"aa:bb:cc:00:00:02",name:"m2",nic_link_mbps:1000}];
       roomSelected=()=>[{m:{mac:"aa:bb:cc:00:00:01"}},{m:{mac:"aa:bb:cc:00:00:02"}}];`);
  // 12.5e9 bytes * 8 / 100e6 = 1000s => ~17 דק'
  const html=run(`roomLinkNote({total_compressed_bytes:12.5e9}, [])`);
  assert.match(html,/קישור איטי בסבב/);
  assert.match(html,/m1/);
  assert.doesNotMatch(html,/m2/);
  assert.match(html,/זמן משוער לגל: ~17 דק'/);
  const noSize=run(`roomLinkNote({total_compressed_bytes:0}, [])`);
  assert.match(noSize,/קישור איטי בסבב/);
  assert.doesNotMatch(noSize,/זמן משוער/,'no size => no invented time');
});

test('round-open warning is absent when nobody selected is slow or unmeasured', () => {
  const {run}=setup();
  run(`MACHINES=[{mac:"a",name:"x",nic_link_mbps:1000},{mac:"b",name:"y",nic_link_mbps:null}];
       roomSelected=()=>[{m:{mac:"a"}},{m:{mac:"b"}}];`);
  assert.equal(run(`roomLinkNote({total_compressed_bytes:1e9}, [])`),'');
});
