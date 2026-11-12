// #1840 (הכרעת נדב 07/11): אימג' עם כרך BitLocker שנקלט סקטור-אחר-סקטור — `bitlocker` ב-/api/console/images.
// "חובה שזה יהיה גלוי למפעיל": תג כתום ברשימה ובמגירה, שורת "הצפנה" בסקירה, וסיומת + שורה כתומה
// בבחירת האימג' לסבב — אותם רכיבים כמו "מצב שינה" (#2053), בלי לחסום ובלי לשאול.
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
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
  for(const f of ['progress.js',...consoleFiles()]) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=(code)=>vm.runInContext(code,ctx);
  run(`ME={username:"admin",role:"admin",server_name:"srv",version:"v0",capabilities:{}}; current="deploy"; renderCurrent=()=>{};`);
  run(`IMAGES=${JSON.stringify(IMAGES)}`);
  return {run,node};
}

const base={os:'unknown',folder:'Office',total_compressed_bytes:41.8e9,min_target_bytes:238e9,partitions:3,created:'2026-11-07T08:00:00Z',source_disk_bytes:256060514304};
const IMAGES=[
  {...base,id:'img_bl',name:'Win11 מוצפן',bitlocker:[3]},
  {...base,id:'img_plain',name:'Office 2024 — סטנדרט',bitlocker:[]},
  {...base,id:'img_old',name:'אימג׳ ישן'},   // שרת ישן — אין שדה
];
const NOTE='מוצפן ב-BitLocker: יועתק כמו שהוא, בלי כיווץ; במחשב אחר יידרש מפתח שחזור';

test('the list: an orange "BitLocker" pill with the note as its hover text; none without partitions', () => {
  const {run}=setup();
  const cell=(i)=>run(`imageRow(IMAGES[${i}]).cells[1]`);
  assert.ok(cell(0).includes(`<span title="${NOTE}"><span class="pill warn">BitLocker</span></span>`),cell(0));
  for(const i of [1,2]) assert.doesNotMatch(cell(i),/BitLocker/);
});

test('the drawer: the chip, and an "הצפנה" row that names the partition', () => {
  const {run}=setup();
  const html=run(`imageDrawerHtml(IMAGES[0])`);
  assert.ok(html.includes(`<span class="n-chip w" title="${NOTE}">BitLocker</span>`),'chip');
  assert.ok(html.includes(`<span class="k">הצפנה</span><span class="v"><span class="hib-src">מחיצה 3 — ${NOTE}</span></span>`),html);
  assert.doesNotMatch(run(`imageDrawerHtml(IMAGES[1])`),/BitLocker|הצפנה/);
});

test('the round window: " · BitLocker" in the option, the orange line under the choice, and nothing blocked', () => {
  const {run}=setup();
  run(`ROOM={round:null,machines:[],disk_floor:null}; MACHINES=[]; DEPLOY.form.image='img_bl';`);
  const html=run('roomNewWindow(roomMachines())');
  assert.match(html,/<option value="img_bl" selected>Win11 מוצפן · נכנס לדיסק מ-238 GB · BitLocker<\/option>/);
  assert.doesNotMatch(html,/<option value="img_bl"[^>]*disabled/,'the tag does not block');
  assert.ok(html.includes(`<div class="full rn-hib" id="rn-hib" aria-live="polite"><span class="n-chip w">BitLocker</span><span>${NOTE}</span></div>`),html);
});

test('the round window: choosing in the list updates the line in place', () => {
  const {run,node}=setup();
  run(`ROOM={round:null,machines:[],disk_floor:null}; MACHINES=[]; DEPLOY.form.image='img_plain';`);
  const html=run('roomNewWindow(roomMachines())');
  assert.ok(html.includes('<div class="full rn-hib" id="rn-hib" aria-live="polite" hidden></div>'),html);
  const box=node('#rn-hib');
  run(`roomFormSet('image','img_bl')`);
  assert.equal(box.hidden,false); assert.ok(box.innerHTML.includes(NOTE),box.innerHTML);
  run(`roomFormSet('image','img_plain')`);
  assert.equal(box.hidden,true); assert.equal(box.innerHTML,'');
});

test('a restore that skipped the drivers because of the encryption says so, not "not Windows"', () => {
  const {run}=setup();
  assert.equal(run(`driversOutcomeText({state:'skipped',packages:[],reason:'bitlocker'})`),'דרייברים דולגו — הכרך מוצפן ב-BitLocker, לא נגענו בו');
});
