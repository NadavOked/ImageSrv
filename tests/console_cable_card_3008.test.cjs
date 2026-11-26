// #3008 (נדב 12/11, הוכרע 23/11): בכרטיס המשכפל, דיסק שסיבת הכשל שלו היא כבל/חריץ אינו מסומן "נכשל בשיכפול הקודם" --
// הוא מסומן כבעיית חיבור, באותן מילים כמו הכותרת "חיבור של <שם> לא תקין" (#3859) ושבב הטבלה "חיבור לא תקין". הדיסק לא מואשם.
// המילים מגיעות מהמוח (`rule.source_reason_short_he` / `source_reason_he`, server/brain/diskcolor.py), לא נכתבות בקונסולה.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {consoleJs}=require('./console_src.cjs');
const {ruleOf}=require('./disk_rule_fixture.cjs');
const root=path.resolve(__dirname,'../server/static');

function setup() {
  const node=()=>({value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{},classList:{add(){},remove(){},toggle(){},contains:()=>false},
    setAttribute(){},getAttribute(){return null;},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],focus(){}});
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,RegExp,Error,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:()=>node(),querySelectorAll:()=>[],getElementById:()=>node(),
      createElement:()=>node(),documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},removeEventListener(){},body:{appendChild(){}}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({ok:true})})});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx); vm.runInContext(consoleJs(),ctx);
  return (code)=>vm.runInContext(code,ctx);
}
const J=JSON.stringify;
const DISK_FAILED_LAST='נכשל בשיכפול הקודם';
const CABLE_CHIP='חיבור לא תקין';
// the card of a slot whose open failure record has this cause (the rule is what the server would send for it)
const card=(run,cause,disk={smart:'ok'})=>{
  const fail=cause?{cause,serial:'S1',port:1,at:'2026-11-12T12:52:17Z'}:null;
  const s={n:1,disk:{serial:'S1',...disk,rule:ruleOf(disk,{fail})},...(fail?{fail}:{})};
  return {chip:JSON.parse(run(`JSON.stringify(bayChip(${J(s)}))`)),lines:JSON.parse(run(`JSON.stringify(bayLines(${J(s)}))`)),cls:run(`slotClass(${J(s)})`),s};
};

test('a cable cause marks the connection, not the disk: the chip and the line say "חיבור לא תקין", in the same yellow', () => {
  const run=setup(), c=card(run,'cable');
  assert.deepEqual(c.chip,['yellow',CABLE_CHIP]);
  assert.equal(c.cls,'yel','the colour is unchanged: a yellow card');
  assert.deepEqual(c.lines,[['yel','חיבור לא תקין — לא מעתיקים ממנו; אפשר לכתוב עליו']]);
  assert.ok(!JSON.stringify(c).includes(DISK_FAILED_LAST),'the disk is not blamed anywhere on the card');
});

test('the card chip is the table chip: the same words as the disk-tab chip of the same cause', () => {
  const run=setup(), c=card(run,'cable');
  assert.match(run(`smartPill({smart:'ok'},{cause:'cable'})`),new RegExp(`>${c.chip[1]}<`));
});

test('a failure of the disk itself keeps "נכשל בשיכפול הקודם" (control: only the cable cause changed)', () => {
  const run=setup();
  for (const cause of ['disk','unknown']) {
    const c=card(run,cause);
    assert.deepEqual(c.chip,['yellow',DISK_FAILED_LAST],cause);
    assert.match(c.lines[0][1],/^נכשל בשיכפול הקודם — /,cause);
  }
  assert.deepEqual(card(run,null,{smart:'ok',write_mark:'incomplete'}).chip,['yellow','כתיבה לא הושלמה']);   // the other yellow, untouched
});

test('the console shows the words the brain sent, and falls back to the old ones for a rule without them', () => {
  const run=setup();
  const rule={color:'yellow',source:false,target:true,source_reason:'cable_failed',source_reason_he:'X — Y',source_reason_short_he:'X'};
  assert.deepEqual(JSON.parse(run(`JSON.stringify(bayChip(${J({n:1,disk:{rule}})}))`)),['yellow','X']);
  const bare={color:'yellow',source:false,target:true,source_reason:'failed_last',source_reason_he:'נכשל בשיכפול הקודם — Z'};
  assert.deepEqual(JSON.parse(run(`JSON.stringify(bayChip(${J({n:1,disk:{rule:bare}})}))`)),['yellow',DISK_FAILED_LAST]);
});
