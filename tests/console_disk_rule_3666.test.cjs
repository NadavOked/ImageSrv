// #3666 (אפיק #3653, הכרעת נדב 18/11): הקונסולה אינה מחשבת את צבע הדיסק -- היא מציגה את מה שהמוח שלח (`rule`).
// (K3/DISK_COLOR, server/brain/diskcolor.py). לפני: `diskRule` ו-`diskFacts` בקונסולה החליטו, בעותק משלהם של הטבלה.
//
// מה נבדק:
//  1. אין בקונסולה טבלה, ואין חישוב צבע של דיסק (קריאת המקור -- שומר מפני שהחישוב חוזר).
//  2. מה שהמוח אמר הוא מה שמוצג: הצבע, הסיבה והמשפט כמו שהגיעו, לכל חמשת הצבעים.
//  3. אין החלטה = "לא נבדק" (אפור), **לעולם לא ירוק** -- גם כש-`smart` אומר "ok", וגם כש-`smart` אומר "fail" (עיקרון 5).
//  4. מה שהטסטים האחרים בונים כ-`rule` (tests/disk_rule_fixture.cjs) נאמן לטבלה: כל 112 צירופי העובדות נותנים את הפסק שהטבלה נתנה
//     לפני השינוי (tests/fixtures/disk_rule_golden_3666.json) -- אחרת כל הטסטים שנשענים עליו היו בודקים חלום.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {consoleJs}=require('./console_src.cjs');
const {ruleOf,ruled,decorate}=require('./disk_rule_fixture.cjs');
const root=path.resolve(__dirname,'../server/static');
const GOLDEN=JSON.parse(fs.readFileSync(path.resolve(__dirname,'fixtures/disk_rule_golden_3666.json'),'utf8'));

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
const slot=(d)=>({n:1,disk:d});
const J=JSON.stringify;

// --- 1. the console holds no table and works no colour out -------------------------------------------------------

test('console.js holds no copy of the disk rule: no table, no sentences, no diskRule / diskFacts', () => {
  const src=consoleJs();
  for (const gone of ['function diskRule(','function diskFacts(','DISK_RULE_RED','DISK_RULE_YELLOW','DISK_RULE_HE','DISK_RULE_SHORT'])
    assert.ok(!src.includes(gone),`${gone} is back in the console`);
  for (const sentence of ['נפסל כאיטי בבדיקת הכתיבה בסבב הזה','בעיית SATA (הקישור התאפס או המגירה התנתקה)','לא מעתיקים ממנו; אפשר לכתוב עליו'])
    assert.ok(!src.includes(sentence),`the rule's sentence is written in the console: ${sentence}`);
  const fn=(name)=>{ const i=src.indexOf(`function ${name}(`); assert.ok(i>=0,name); return src.slice(i,src.indexOf('\n}\n',i)); };
  for (const name of ['slotRule','slotClass']) assert.doesNotMatch(fn(name),/\.smart\b|write_mark|sata_issue|failed_last/,`${name} reads a fact instead of the verdict`);
});

// --- 2. what the brain said is what is shown ----------------------------------------------------------------------

test('slotClass is the brain\'s colour: red / yellow / orange / green, as the server sent them', () => {
  const run=setup();
  const cls=(d)=>run(`slotClass(${J(slot(d))})`);
  assert.equal(cls({rule:ruleOf({smart:'fail'})}),'err');
  assert.equal(cls({rule:ruleOf({smart:'ok',sata_issues:['drawer_gone']})}),'err');
  assert.equal(cls({rule:ruleOf({smart:'ok'},{fail:{cause:'cable'}})}),'yel');
  assert.equal(cls({rule:ruleOf({smart:'ok',write_mark:'incomplete'})}),'yel');
  assert.equal(cls({rule:ruleOf({smart:'warn'})}),'warn');
  assert.equal(cls({rule:ruleOf({smart:'ok'})}),'ok');
  assert.equal(cls({rule:ruleOf({smart:'unchecked'})}),'');
});

test('the card and the lines say the brain\'s words -- the sentence, the short reason, the colour -- and nothing of their own', () => {
  const run=setup();
  const red=ruled({smart:'fail',smart_reason:'health_failed',model:'870 EVO',size_bytes:256060514304});
  const lines=JSON.parse(run(`JSON.stringify(bayLines(${J(slot(red))}))`)).map(([c,t])=>`${c}:${t}`);
  assert.ok(lines.includes(`bad:אדום: ${red.rule.target_reason_he}`),lines.join(' | '));
  const yel=ruled({smart:'ok',write_mark:'incomplete'});
  assert.deepEqual(JSON.parse(run(`JSON.stringify(bayLines(${J(slot(yel))}))`)),[['yel',yel.rule.source_reason_he]]);
  const slow=ruled({smart:'ok',write_slow:true});
  assert.match(run(`diskTitle(${J(M)},${J(slot(slow))},${J(slow)})`),/נפסל כאיטי בבדיקת הכתיבה בסבב הזה/);
  assert.doesNotMatch(run(`diskTitle(${J(M)},${J(slot(ruled({smart:'ok'})))},${J(ruled({smart:'ok'}))})`),/נפסל כאיטי/);
  // a disk that is red by SMART *and* slow says both (the title lists every reason that held, not only the first)
  const both=ruled({smart:'fail',write_slow:true});
  assert.match(run(`diskTitle(${J(M)},${J(slot(both))},${J(both)})`),/נפסל כאיטי בבדיקת הכתיבה בסבב הזה/);
});

// --- 3. no decision is "not checked", never green ----------------------------------------------------------------

test('a disk with no `rule` is grey "not checked" -- even when its own `smart` says ok (it did not pass through the brain)', () => {
  const run=setup();
  for (const smart of ['ok','warn','fail','failed_last',undefined]) {
    const d={dev:'sda',port:1,serial:'S1',model:'870 EVO',size_bytes:256060514304,smart};
    assert.equal(run(`slotClass(${J(slot(d))})`),'',`smart=${smart}`);
    assert.deepEqual(JSON.parse(run(`JSON.stringify(bayChip(${J(slot(d))}))`)),['grey','SMART לא נבדק'],`smart=${smart}`);
    assert.equal(run(`slotRule(${J(slot(d))}).no_decision`),true);
  }
  assert.equal(run(`slotRule({n:1,disk:null}).color`),'grey');
});

test('a rule that is not an object with a colour is no decision either', () => {
  const run=setup();
  for (const bad of [null,'red',{},{color:7},[]]) assert.equal(run(`slotRule(${J({n:1,disk:{smart:'ok',rule:bad}})}).no_decision`),true,J(bad));
});

// --- 4. the test builder is faithful to the table ----------------------------------------------------------------

const SMARTS=['ok','warn','fail','failed_last','unchecked',null,'something_new'];
const FLAGS=['failed_last','write_incomplete','sata_issue','write_slow'];
const sig=(r)=>[r.color,r.source?1:0,r.target?1:0,r.reason||'',r.source_reason||'',r.target_reason||''].join('|');

test('the fixture builder gives, for all 112 combinations of facts, the verdict the table gave before #3666', () => {
  let n=0;
  for (const smart of SMARTS) for (let bits=0; bits<16; bits++) {   // the same order as the golden file's generator
    const on=FLAGS.map((_,i)=>!!(bits&(8>>i)));
    const disk={}; if (smart!==null) disk.smart=smart;
    if (on[1]) disk.write_mark='incomplete';
    if (on[2]) disk.sata_issues=['link_reset'];
    const got=ruleOf(disk,{fail:on[0]?{cause:'disk'}:null,write_slow:on[3]});
    assert.equal(sig(got),GOLDEN.facts[n],`#${n} ${J({smart,on})}`); n++;
  }
  assert.equal(n,112); assert.equal(GOLDEN.facts.length,112);
  for (const [word,he] of Object.entries(GOLDEN.reason_he)) assert.equal(ruleOf({smart:'ok',...({smart_fail:{smart:'fail'},sata_issue:{sata_issues:['link_reset']},write_slow:{write_slow:true},
    failed_last:{smart:'failed_last'},write_incomplete:{write_mark:'incomplete'}})[word]}).reason_he,he,word);
});

test('decorate() joins a failure record by serial or by port, and leaves a rule that is already there alone', () => {
  const m=[{mac:'aa',disks:[{serial:'S1',port:1,smart:'ok'},{serial:'S2',port:2,smart:'ok'},{serial:'S3',port:3,smart:'ok',rule:{color:'grey'}}]}];
  decorate(m,[{serial:'S1',cause:'disk'},{mac:'aa',port:2,cause:'readonly'}]);
  assert.deepEqual(m[0].disks.map((d)=>d.rule.color),['yellow','red','grey']);
});
