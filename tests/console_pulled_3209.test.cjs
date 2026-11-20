// ‏#3209 (נדב 14/11): מגירה שנשלפה בסבב רגיל — אפורה "נשלף · ממתין להחזרה", לא אדומה "נכשל".
// השרת שולח `drawer_list[].pulled: true` (room._pulled: היעד failed, הסוכן אמר pulled, והמכונה עוד writing);
// ‏`state` נשאר failed. הקונסולה ציירה אדום; עכשיו drawerPulled(d) קודם לכל מקום ששואל `state === "failed"`.
// הצבע והנוסח כמו מסך המשכפל (#3270/#3304): אפור = מצב תהליך. אדום נשאר לכשל אמיתי (pulled חסר או false).
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {consoleJs}=require('./console_src.cjs');
const {ruled}=require('./disk_rule_fixture.cjs');   // #3666: the disk arrives with the server's rule; the console decides nothing
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
const M={mac:'aa:bb:cc:00:00:01',drawer_count:1};
const disk=(extra={})=>({dev:'sda',port:1,serial:'S1',model:'870 EVO',size_bytes:256060514304,smart:'ok',state:'failed',pulled:true,
  bytes_written:38,bytes_total:100,error:null,...extra});
const slot=(d)=>({n:1,disk:ruled(d),live:ruled(d)});
const html=(run,render,d)=>run(`${render}(${JSON.stringify(M)}, ${JSON.stringify(slot(d))}, false)`);
const PULLED='נשלף · ממתין להחזרה';

test('the round card says "נשלף" in grey: no red class, no emphasis frame, an idle (grey) bar at the written %, and the waiting line', () => {
  const run=setup(), h=html(run,'roundDskHtml',disk());
  const cls=h.match(/^<div class="([^"]*)"/)[1];
  assert.ok(cls.split(' ').includes('pulled'),cls);
  assert.ok(!cls.includes('bad') && !cls.includes('bay-emphasis'),cls);
  assert.ok(h.includes('<b>נשלף</b>'),h);
  assert.ok(h.includes(`<span class="dline">${PULLED}</span>`),h);
  assert.ok(h.includes('class="bar idle"') && h.includes('--w:38%'),h);
  assert.ok(!h.includes('נכשל') && !h.includes('class="bar err"'),h);
  assert.ok(!h.includes('role="progressbar"'),'a pulled drawer is not a live meter');
});

test('without the server\'s `pulled: true` the same failed drawer stays red "נכשל" -- the grey needs positive evidence', () => {
  const run=setup();
  for (const d of [disk({pulled:false}), disk({pulled:undefined}), disk({pulled:'true'}), disk({pulled:1})]) {
    const h=html(run,'roundDskHtml',d);
    assert.ok(/^<div class="dsk bad bay-emphasis err"/.test(h),h);
    assert.ok(h.includes('<b>נכשל</b>') && h.includes('class="bar err"') && !h.includes(PULLED),h);
  }
});

test('`pulled` on a drawer that is not failed changes nothing (the server only sets it with failed)', () => {
  const run=setup();
  for (const state of ['writing','done','verifying']) {
    const h=html(run,'roundDskHtml',disk({state}));
    assert.ok(!h.includes(PULLED) && !/^<div class="dsk[^"]*pulled/.test(h),`${state}: ${h}`);
  }
});

test('a pulled drawer whose disk is really red keeps the red (SMART fail is a reason of its own)', () => {
  const run=setup();
  assert.equal(run(`slotClass(${JSON.stringify(slot(disk({smart:'fail'})))})`),'err');
  assert.equal(run(`slotClass(${JSON.stringify(slot(disk()))})`),'','pulled alone: no colour -- not "run", not "ok" (smart ok)');
  assert.equal(run(`slotClass(${JSON.stringify(slot(disk({pulled:false})))})`),'err');
});

test('the machine page slot, bay card, status and hover agree: grey "נשלף"', () => {
  const run=setup(), d=disk();
  const sh=html(run,'slotHtml',d);
  assert.ok(!sh.includes('נכשל') && !sh.includes('bar err') && sh.includes('bar idle'),sh);
  assert.deepEqual(JSON.parse(run(`JSON.stringify(bayChip(${JSON.stringify(slot(d))}))`)),['grey','נשלף']);
  assert.equal(run(`bayStatus(${JSON.stringify(slot(d))})`),'נשלף');
  const lines=JSON.parse(run(`JSON.stringify(bayLines(${JSON.stringify(slot(d))}))`));
  assert.ok(lines.some(([,t])=>t===PULLED) && !lines.some(([c])=>c==='bad'),JSON.stringify(lines));
  assert.ok(run(`diskTitle(${JSON.stringify(M)}, ${JSON.stringify(slot(d))}, ${JSON.stringify(d)})`).includes(PULLED));
  // the failed twin is still red everywhere
  const f=disk({pulled:false});
  assert.deepEqual(JSON.parse(run(`JSON.stringify(bayChip(${JSON.stringify(slot(f))}))`)),['red','נכשל בסבב']);
  assert.equal(run(`bayStatus(${JSON.stringify(slot(f))})`),'נכשל');
});

test('the production-line table (r1) shows the same grey tag and words; a failed twin keeps "נכשל"', () => {
  const run=setup(), pl={disks:[]};
  const row=(d)=>JSON.parse(run(`JSON.stringify(plRow({}, ${JSON.stringify(pl)}, ${JSON.stringify(M)}, ${JSON.stringify(slot(d))}))`));
  const [tag,painted,bar,note]=row(disk());
  assert.ok(tag.includes('t-mut') && tag.includes('נשלף') && !tag.includes('t-bad'),tag);
  assert.equal(painted,false); assert.equal(bar,''); assert.equal(note,PULLED);
  const [ftag]=row(disk({pulled:false,error:'I/O error'}));
  assert.ok(ftag.includes('t-bad') && ftag.includes('נכשל'),ftag);
});

test('the grey is in the stylesheet: .dsk.pulled is muted like .dsk.cancelled, never the danger colour', () => {
  const css=fs.readFileSync(path.join(root,'console.css'),'utf8');
  assert.ok(/\.page \.mach \.dsk\.pulled span,\.page \.mach \.dsk\.pulled b\{color:var\(--clr-muted\)\}/.test(css));
  assert.ok(/\.page \.bar\.idle i\{background:var\(--led-idle\)\}/.test(css));
});
