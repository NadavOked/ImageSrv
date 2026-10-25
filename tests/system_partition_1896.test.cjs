// #1896 (Nadav 22/10): an old image whose two windows partitions are the same size is "לא נבדק" in the
// library, with a link that marks C:, until it is marked; the sheet offers exactly the server's candidates.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(path.resolve(__dirname,'..'),'server/static');

function setup() {
  const el=()=>({value:'',textContent:'',innerHTML:'',hidden:false,dataset:{},style:{},classList:{add(){},remove(){},toggle(){},contains:()=>false},
    setAttribute(){},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],closest:()=>null,focus(){}});
  const doc={querySelector:()=>el(),querySelectorAll:()=>[],getElementById:()=>el(),
    createElement:()=>el(),documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},body:{appendChild(){}}};
  const posts=[];
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,RegExp,encodeURIComponent,decodeURIComponent,
    document:doc,window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,opts)=>{posts.push([url,opts&&opts.body]);return {status:200,ok:true,headers:{get:()=>null},json:async()=>({ok:true})};}});
  for(const f of ['progress.js','console.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  return {run:(code)=>vm.runInContext(code,ctx),posts};
}

const IMG={id:'img_189600',name:'Twins Old',os:'windows',folder:'',total_compressed_bytes:1,min_target_bytes:null,
  min_free_unchecked:'לא נבדק אם יישארו 20GB פנויים ב-C:',system_unmarked:true,
  system_candidates:[{index:2,size_bytes:128849018880,used_bytes:42949672960,system:false},{index:3,size_bytes:128849018880,used_bytes:1073741824,system:false}]};

test('an unmarked image says "לא נבדק" with a link that marks C:, not a size', () => {
  const {run}=setup();
  run(`IMAGES=${JSON.stringify([IMG])}`);
  const cells=run(`imageRow(IMAGES[0]).cells.join(' ')`);
  assert.match(cells,/לא נבדק/);
  assert.match(cells,/markSystemPartition\('img_189600'\)/);
  assert.match(cells,/סימון מחיצת המערכת/);   // the row menu too
  const marked=run(`imageRow(Object.assign({}, IMAGES[0], {system_unmarked:false, min_target_bytes:256000000000})).cells.join(' ')`);
  assert.doesNotMatch(marked,/לא נבדק/);
});

test('the sheet offers the server candidates and posts the chosen index', async () => {
  const {run,posts}=setup();
  run(`IMAGES=${JSON.stringify([IMG])}; var _sheet=null; sheet=(o)=>{_sheet=o;}; loadImages=async()=>{}; toast=()=>{};`);
  run(`markSystemPartition('img_189600')`);
  const opts=run(`_sheet.fields[0].options.map(o=>o.value).join(',')`);
  assert.equal(opts,'2,3');
  await run(`_sheet.onSubmit({index:'3'})`);
  const hit=posts.find(([u])=>String(u).includes('/images/img_189600/system-partition'));
  assert.ok(hit, JSON.stringify(posts));
  assert.deepEqual(JSON.parse(hit[1]),{index:3});
});
