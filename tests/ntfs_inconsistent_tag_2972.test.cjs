// #2972 (נדב 12/11, "כמו Clonezilla"): מחיצת NTFS ש-ntfsresize מצא לא עקבית נקלטה בגודלה המלא, בלי כיווץ —
// `ntfs_inconsistent` ב-/api/console/images. תג כתום ברשימה ובמגירה, שורת "NTFS" בסקירה, וסיומת + שורה
// כתומה בבחירת האימג' לסבב — אותם רכיבים כמו BitLocker (#1840), בלי לחסום ובלי לשאול.
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
  run(`ME={username:"admin",role:"admin",server_name:"srv",version:"v0",capabilities:{}}; current="deploy"; renderCurrent=()=>{};`);
  run(`IMAGES=${JSON.stringify(IMAGES)}`);
  return {run,node};
}

const base={os:'unknown',folder:'Office',total_compressed_bytes:41.8e9,min_target_bytes:238e9,partitions:3,created:'2026-11-12T08:00:00Z',source_disk_bytes:256060514304};
const IMAGES=[
  {...base,id:'img_ni',name:'Win11 בנייה',ntfs_inconsistent:[3],bitlocker:[]},
  {...base,id:'img_plain',name:'Office 2024 — סטנדרט',ntfs_inconsistent:[],bitlocker:[]},
  {...base,id:'img_old',name:'אימג׳ ישן'},   // שרת ישן — אין שדה
];
const TAG='NTFS לא נסגר';
const NOTE='NTFS לא נסגר כראוי (לא עקבי) — לא כווץ; Windows יריץ chkdsk באתחול הראשון';

test('the list: an orange "NTFS לא נסגר" pill with the note as its hover text; none without partitions', () => {
  const {run}=setup();
  const cell=(i)=>run(`imageRow(IMAGES[${i}]).cells[1]`);
  assert.ok(cell(0).includes(`<span title="${NOTE}"><span class="pill warn">${TAG}</span></span>`),cell(0));
  for(const i of [1,2]) assert.ok(!cell(i).includes(TAG),cell(i));
});

test('the drawer: the chip, and an "NTFS" row that names the partition', () => {
  const {run}=setup();
  const html=run(`imageDrawerHtml(IMAGES[0])`);
  assert.ok(html.includes(`<span class="n-chip w" title="${NOTE}">${TAG}</span>`),'chip');
  assert.ok(html.includes(`<span class="k">NTFS</span><span class="v"><span class="hib-src">מחיצה 3 — ${NOTE}</span></span>`),html);
  for(const i of [1,2]) assert.ok(!run(`imageDrawerHtml(IMAGES[${i}])`).includes(NOTE));
});

test('the round window: the tag in the option, the orange line under the choice, and nothing blocked', () => {
  const {run}=setup();
  run(`ROOM={round:null,machines:[],disk_floor:null}; MACHINES=[]; DEPLOY.form.image='img_ni';`);
  const html=run('roomNewWindow(roomMachines())');
  assert.ok(html.includes(`<option value="img_ni" selected>Win11 בנייה · נכנס לדיסק מ-238 GB · ${TAG}</option>`),html);
  assert.doesNotMatch(html,/<option value="img_ni"[^>]*disabled/,'the tag does not block');
  assert.ok(html.includes(`<div class="full rn-hib" id="rn-hib" aria-live="polite"><span class="n-chip w">${TAG}</span><span>${NOTE}</span></div>`),html);
});

test('the console says the same words as the agent writes in the task (captureview.sh)', () => {
  const sh=fs.readFileSync(path.join(path.resolve(__dirname,'..'),'agent/lib/capture/captureview.sh'),'utf8');
  assert.ok(sh.includes(`CAPTURE_NTFS_NOTE="${NOTE}"`));
  const {run}=setup();
  assert.equal(run('NI_NOTE'),NOTE);
});
