// apple-pending-4 · 2: הלשונית היא המקור והעץ עוקב אחריה. בלי דפדפן: console.js ב-vm מול עץ מזויף.
// היום (לפני התיקון): בלשונית "חיבורים פיזיים" העץ אמר "רשת" בלבד. עכשיו: הצומת של הלשונית מסומן
// (active), והאב — "רשת" — מקבל סימון רך (anc). לחיצה על צומת או על לשונית מעדכנת את שניהם.
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

function setup() {
  const mk=(id,attrs={},inNet=false)=>{
    const cls=new Set();
    return {id,attrs:{...attrs},cls,inNet,parentElement:null,
      classList:{add:(...c)=>c.forEach((x)=>cls.add(x)),remove:(...c)=>c.forEach((x)=>cls.delete(x)),contains:(c)=>cls.has(c)},
      setAttribute(k,v){this.attrs[k]=String(v);},removeAttribute(k){delete this.attrs[k];},getAttribute(k){return this.attrs[k]??null;},
      closest(sel){return sel==='#netTA'&&inNet?{}:null;},hasAttribute(){return false;},style:{},dataset:{},addEventListener(){},removeEventListener(){}};
  };
  const nodes={network:mk('network',{'data-page':'network'}),diagram:mk('diagram',{'data-net-tab':'0'},true),nic:mk('nic',{'data-net-tab':'1'},true),dist:mk('dist',{'data-net-tab':'2'},true),
    ports:mk('ports',{'data-page':'ports'},true),home:mk('home',{'data-page':'home'})};
  // ‏#1300: הסימון הרך הולך במעלה העץ האמיתי (box.inventory-children → השורה שלפניו), לא "#netTA" קשיח
  const netTA={classList:{contains:(c)=>c==='inventory-children'},previousElementSibling:nodes.network,parentElement:null,hasAttribute:()=>false,style:{}};
  for(const k of ['diagram','nic','dist','ports']) nodes[k].parentElement=netTA;
  nodes.network.cls.add('inventory-node');
  const all=Object.values(nodes);
  const bySel={'.inventory-node[data-net-tab="0"]':nodes.diagram,'.inventory-node[data-net-tab="1"]':nodes.nic,'.inventory-node[data-net-tab="2"]':nodes.dist,'.inventory-node[data-page="ports"]':nodes.ports,
    '.inventory-node[data-page="network"]':nodes.network,'.inventory-node[data-page="home"]':nodes.home};
  const content={innerHTML:''};
  const generic=()=>({value:'',style:{},dataset:{},classList:{add(){},remove(){},contains:()=>false,toggle(){}},setAttribute(){},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[]});
  const doc={querySelector:(s)=>bySel[s]||generic(),
    querySelectorAll:(s)=>/\.active|\.anc/.test(s)?all.filter((n)=>n.cls.has('active')||n.cls.has('anc')||n.attrs['aria-current']):[],
    getElementById:(id)=>id==='content'?content:{value:'',style:{},classList:{add(){},remove(){},contains:()=>false},setAttribute(){},addEventListener(){},removeEventListener(){}},
    documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},createElement:()=>({}),body:{appendChild(){}}};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,encodeURIComponent,decodeURIComponent,
    document:doc,window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  for(const f of ['progress.js',...consoleFiles(),'net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=(s)=>vm.runInContext(s,ctx);
  run('wireNetPage=()=>{}; wireRestoredPage=()=>{}; layout=()=>"x"; pageAllowed=()=>true; closeSidebar=()=>{};');
  const state=()=>Object.fromEntries(Object.entries(nodes).map(([k,n])=>[k,n.cls.has('active')?'active':n.cls.has('anc')?'anc':'']));
  return {run,nodes,state};
}

test('network tab → its own tree node is active and "רשת" is the soft ancestor; the diagram tab marks its own "תרשים" node (mockup)',()=>{
  const {run,state}=setup();
  run('current="network"; currentTab=0; syncTreeToTab()');
  assert.deepEqual(state(),{network:'anc',diagram:'active',nic:'',dist:'',ports:'',home:''},'diagram = its own node (as in the mockup), "רשת" soft');
  run('activateTab(1)');
  assert.deepEqual(state(),{network:'anc',diagram:'',nic:'active',dist:'',ports:'',home:''},'physical connections');
  run('activateTab(2)');
  assert.deepEqual(state(),{network:'anc',diagram:'',nic:'',dist:'active',ports:'',home:''},'distribution network');
  run('activateTab(3)');
  assert.deepEqual(state(),{network:'anc',diagram:'',nic:'',dist:'',ports:'active',home:''},'ports tab');
  run('activateTab(0)');
  assert.deepEqual(state(),{network:'anc',diagram:'active',nic:'',dist:'',ports:'',home:''},'back to the diagram node');
});

test('exactly one active node and the ancestor is never also active (no double marking)',()=>{
  const {run,nodes}=setup();
  for (const t of [0,1,2,3,1,0]) {
    run(`current="network"; activateTab(${t})`);
    const active=Object.values(nodes).filter((n)=>n.cls.has('active')), anc=Object.values(nodes).filter((n)=>n.cls.has('anc'));
    assert.equal(active.length,1,'tab '+t); assert.ok(anc.length<=1); assert.ok(!anc.some((n)=>n.cls.has('active')));
    assert.equal(active[0].attrs['aria-current'],'page');
  }
});

test('clicking a tree node marks it and its ancestor too (ports page = the ports node, network = soft)',()=>{
  const {run,state}=setup();
  run('selectPageById("ports")');
  assert.deepEqual(state(),{network:'anc',diagram:'',nic:'',dist:'',ports:'active',home:''});
  run('selectPageById("home")');
  assert.deepEqual(state(),{network:'',diagram:'',nic:'',dist:'',ports:'',home:'active'},'leaving the network clears the soft mark');
});

test('a page without per-tab tree nodes is untouched by tab changes',()=>{
  const {run,state}=setup();
  run('selectPageById("home")'); run('current="home"; pages.home.tabs=["a","b"]; activateTab(1)');
  assert.deepEqual(state(),{network:'',diagram:'',nic:'',dist:'',ports:'',home:'active'});
});
