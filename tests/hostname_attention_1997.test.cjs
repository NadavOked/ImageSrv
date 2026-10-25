// ‏#1997 (נדב, 22/10): שם מחשב או שרת = hostname באנגלית, עד 15 תווים. שם
// קיים שאינו חוקי (שרת המשנה "חיפה", מכונה "מחשב בנייה") לא נשבר ולא משתנה
// לבד — הוא מופיע ב"דורש טיפול" בסקירה, עם כפתור שפותח את שינוי השם. השרת
// מסמן (`name_problem` / `label_problem` / `server_name_problem`) והקונסולה
// מציגה; הכלל עצמו אינו משוכפל ל-JS.
// בלי דפדפן: console.js רץ ב-vm מול DOM מזערי, כמו home_shrink_attention.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const RULE="שם מחשב או שרת באנגלית בלבד: אותיות אנגליות, ספרות ומקף, עד 15 תווים, לא מתחיל ולא נגמר במקף";

function setup() {
  const node=()=>({value:'',innerHTML:'',textContent:'',dataset:{},style:{removeProperty(){}},
    classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
    querySelector(){return null;},querySelectorAll(){return [];},setAttribute(){},removeAttribute(){},focus(){}});
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:node,
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false})},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx);
  vm.runInContext(fs.readFileSync(path.join(root,'console.js'),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('toast=()=>{}; HEALTH=[]; SHRINK_RECORDS=[]; HOME.net=[]; HOME.nodes=[];');
  return run;
}
const me=(over={})=>`ME=${JSON.stringify({username:'admin',role:'admin',server_name:'imagesrv-ta',server_name_problem:null,capabilities:{},...over})}`;

test('a machine whose name is not a hostname is in "needs attention" with a rename button',()=>{
  const run=setup();
  run(me());
  run(`MACHINES=${JSON.stringify([
    {mac:'aa:bb:cc:00:19:97',suffix:'מחשב בנייה',name_problem:`שם המחשב 'מחשב בנייה' אינו חוקי — ${RULE}`},
    {mac:'aa:bb:cc:00:19:98',suffix:'PC3',name_problem:null}])}`);
  const html=run('homeAttention()');
  assert.match(html,/id="home-attn"/);
  assert.match(html,/<b>שם מחשב לא חוקי — מחשב בנייה<\/b>/);
  assert.ok(html.includes(RULE),'the rule is said, not just "invalid"');
  assert.match(html,/<button class="btn sm flat" onclick="renameMachine\('aa%3Abb%3Acc%3A00%3A19%3A97'\)">שינוי שם<\/button>/);
  assert.doesNotMatch(html,/PC3/,'a valid name is not an item');
});

test('a secondary server whose name is not a hostname (חיפה) is in "needs attention" with a rename button',()=>{
  const run=setup();
  run(me()); run('MACHINES=[]');
  run(`HOME.nodes=${JSON.stringify([{id:'n1',label:'חיפה',label_problem:`שם שרת המשנה 'חיפה' אינו חוקי — ${RULE}`},
                                     {id:'n2',label:'imagesrv-ta2',label_problem:null}])}`);
  run('SECONDARY_STATUS={n1:{connected:true},n2:{connected:true}}');
  const html=run('homeAttention()');
  assert.match(html,/<b>שם שרת משנה לא חוקי — חיפה<\/b>/);
  assert.match(html,/onclick="renameBranchNode\('n1'\)">שינוי שם<\/button>/);
  assert.doesNotMatch(html,/imagesrv-ta2/);
});

test('this server\'s own name that is not a hostname is in "needs attention" with the hostname sheet',()=>{
  const run=setup();
  run(me({server_name:'שרת-ראשי',server_name_problem:`שם השרת 'שרת-ראשי' אינו חוקי — ${RULE}`})); run('MACHINES=[]');
  const html=run('homeAttention()');
  assert.match(html,/<b>שם השרת לא חוקי — שרת-ראשי<\/b>/);
  assert.match(html,/onclick="hostnameSheet\(\)">שינוי שם<\/button>/);
});

test('all names valid = no name item (the rule lives on the server; the console only shows its verdict)',()=>{
  const run=setup();
  run(me()); run(`MACHINES=${JSON.stringify([{mac:'aa:bb:cc:00:19:98',suffix:'PC3',name_problem:null}])}`);
  const html=run('homeAttention()');
  assert.doesNotMatch(html,/לא חוקי/);
  assert.match(html,/אין פריטים לטיפול/);
});

test('the add-machine form says the name is an English computer name, not "free"',()=>{
  const src=fs.readFileSync(path.join(root,'console.js'),'utf8');
  assert.doesNotMatch(src,/שם חופשי לבנייה ולשיכפול/);
  assert.match(src,/שם מחשב — באנגלית, עד 15 תווים/);
});
