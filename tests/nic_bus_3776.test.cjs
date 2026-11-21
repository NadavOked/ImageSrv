// #3776: סוג אפיק כרטיס ההפצה (/machines.nic_bus) בפרטי המחשב. עובדה בלבד: PCI רגיל = כתום, PCIe כפי שדווח,
// לא דווח / unknown = "לא נקרא" (עיקרון 5: "לא נקרא" אינו PCIe תקין).
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

const mk=(bus)=>`({mac:"aa:bb:cc:00:00:01",name:"m1",nic_bus:${bus}})`;

test('conventional PCI is orange', () => {
  const {run}=setup();
  const html=run(`nicBusStatus(${mk('{bus:"pci",capable_66mhz:true,bridge_capable_66mhz:false}')})`);
  assert.match(html,/st warn/);
  assert.match(html,/PCI/);
});

test('PCIe shows the link as reported, and the maximum only when it differs', () => {
  const {run}=setup();
  const full=run(`nicBusStatus(${mk('{bus:"pcie",current_link_speed:2.5,current_link_width:1,max_link_speed:2.5,max_link_width:1}')})`);
  assert.match(full,/st ok/); assert.ok(full.includes('PCIe 2.5GT/s ×1')); assert.ok(!full.includes('מקסימום'));
  const less=run(`nicBusStatus(${mk('{bus:"pcie",current_link_speed:2.5,current_link_width:1,max_link_speed:8,max_link_width:4}')})`);
  assert.match(less,/st ok/); assert.ok(less.includes('מקסימום 8GT/s ×4'));
});

test('unknown, missing or junk is "not read", never OK and never PCIe', () => {
  const {run}=setup();
  for(const v of ['null','undefined','{bus:"unknown"}','{bus:"usb"}','"pcie"','{}']) {
    const html=run(`nicBusStatus(${mk(v)})`);
    assert.match(html,/לא נקרא/,v);
    assert.doesNotMatch(html,/st ok|PCIe/,v);
  }
});
