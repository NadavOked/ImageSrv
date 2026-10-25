// #1859 (Nadav 20/10): the console shows every SMART attribute of a disk in Hebrew, marks the critical ones and
// what rose since the previous round ("להחליף את הדיסק" on a critical one), says "אין עדיין השוואה" with no
// previous round -- never "stable" -- and says why a disk is orange. The view is GET /machines/{mac}/smart
// (server/smart_history.py describe); console.js runs in a vm, as in reasons_he_1861.test.cjs.
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
const attr=(over)=>({id:5,name_he:'סקטורים שהועברו',note_he:'קריטי – סקטורים שהועברו לגיבוי',known:true,vendor:'Reallocated_Sector_Ct',
  value:100,worst:100,thresh:10,raw:2,critical:true,rose_by:null,failing_now:false,...over});
const MAC='aa:bb:cc:dd:ee:01';
const view=(disk)=>{ run(`SMART_ATTRS[${JSON.stringify(MAC)}]=${JSON.stringify({mac:MAC,no_history_he:'אין עדיין השוואה',disks:[disk]})}`);
  return run(`smartAttrsHtml(${JSON.stringify(MAC)},{dev:'sda',serial:'S1'})`); };

test('every attribute in Hebrew, the critical marked, and a critical one that rose says to replace the disk', () => {
  const html=view({serial:'S1',dev:'sda',read:true,compared:true,previous_at:'2026-10-19T08:00:00Z',learned:'critical_rose',
    attributes:[attr({rose_by:2}),attr({id:235,name_he:'POR_Recovery_Count',note_he:'לא מוכר',known:false,vendor:'POR_Recovery_Count',critical:false,raw:87})]});
  const t=text(html);
  assert.match(t,/סקטורים שהועברו/); assert.match(t,/קריטי/);
  assert.match(t,/עלה ב-.*2.* מאז הסבב הקודם — להחליף את הדיסק/);
  assert.match(t,/POR_Recovery_Count \( לא מוכר \)|POR_Recovery_Count.*לא מוכר/,'an id the table does not know: the vendor name, "לא מוכר"');
  assert.match(t,/מאפיין קריטי עלה מאז הסבב הקודם — להחליף את הדיסק/,'the learned word in Hebrew');
  assert.doesNotMatch(t,/critical_rose/);
});

test('no previous round is "no comparison yet", never "stable"', () => {
  const t=text(view({serial:'S1',dev:'sda',read:true,compared:false,learned:null,attributes:[attr({raw:0})]}));
  assert.match(t,/אין עדיין השוואה/); assert.doesNotMatch(t,/יציב/);
});

test('a disk smartctl did not read is "not checked", not an empty table', () => {
  const html=view({serial:'S1',dev:'sda',read:false,compared:false,learned:null,attributes:[]});
  assert.match(text(html),/לא נבדק/); assert.doesNotMatch(html,/<table/);
});

test('#1921: a hardware note is said beside the disk -- the drawer/cable, never the disk', () => {
  const t=text(view({serial:'S1',dev:'sda',read:true,compared:false,learned:null,
    hw:[{word:'crc',he:'שגיאות CRC רבות בקישור — כבל או מגירה חשודים, לא הדיסק'}],
    attributes:[attr({id:199,name_he:'שגיאות CRC',vendor:'CRC_Error_Count',raw:1752,table:'hw',verdict:'hw'})]}));
  assert.match(t,/הערת חומרה \(מגירה\/כבל — לא הדיסק\): שגיאות CRC רבות בקישור/);
  const none=text(view({serial:'S1',dev:'sda',read:true,compared:false,learned:null,attributes:[attr({raw:0})]}));
  assert.doesNotMatch(none,/הערת חומרה/);
});

test('an orange disk says why -- once, without "SMART SMART"', () => {
  const said=run(`smartText({smart:'warn',smart_reason:'ssd_worn'})`);
  assert.equal(said,'SMART אזהרה — הדיסק שחוק — 90% או יותר');
  const box=text(run(`diskBoxesHtml({mac:${JSON.stringify(MAC)},disks:[{dev:'sda',port:1,smart:'warn',smart_reason:'reallocated',size_bytes:500107862016,model:'X'}]})`));
  assert.doesNotMatch(box,/SMART SMART/); assert.match(box,/סקטורים שהועברו \(5\)/);
});
