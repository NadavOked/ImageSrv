// #2053 (המוקאפ docs/design/mockups-1104/2053-hibernated.html, אושר בידי נדב 04/11): תג "מצב שינה"
// לאימג' שנקלט ממקור מושהה — נגזר מ-`src_unhib` הקיים במניפסט (#2213). ברשימה ליד השם, במגירה
// (שורת התגים + "המקור בקליטה" בסקירה), ובחלון "התחל סבב" (סיומת ושורה כתומה שאינה חוסמת).
// בלי השדה — אין תג ואין שורה (עיקרון 5: היעדר אינו "לא היה מושהה").
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
  return {run,node};
}

const base={os:'windows',folder:'Office',total_compressed_bytes:41.8e9,min_target_bytes:238e9,partitions:3,created:'2026-11-03T08:00:00Z',source_disk_bytes:256060514304};
const IMAGES=[
  {...base,id:'img_hib',name:'Office 2024 — HP-BUILD-2',src_unhib:'unhibernated'},
  {...base,id:'img_log',name:'Windows 11 בסיס',src_unhib:'unhibernated_logfile'},
  {...base,id:'img_old',name:'Office 2024 — סטנדרט'},   // נקלט לפני #2213 — אין שדה
];
const TIP='המקור היה במצב שינה של Windows (Fast Startup). ההשהיה בוטלה בעותק בזמן הקליטה; המקור לא נכתב.';
const ROUND='האימג\' נקלט ממקור שהיה במצב שינה של Windows (Fast Startup). ההשהיה בוטלה בעותק בזמן הקליטה, והאימג\' אומת.';
const SRC='Windows במקור לא כובה עד הסוף (Fast Startup). בעותק בוטלה ההשהיה (עלייה מלאה)';
const LOG='גם יומן ה-NTFS ($LogFile) נוקה בעותק.';
const esc=(s)=>s.replace(/'/g,'&#39;');

function withImages() {
  const s=setup();
  s.run(`IMAGES=${JSON.stringify(IMAGES)}`);
  return s;
}

test('the list: an orange "מצב שינה" pill beside the name, with the hover text; none without the field', () => {
  const {run}=withImages();
  const cell=(i)=>run(`imageRow(IMAGES[${i}]).cells[1]`);
  for(const i of [0,1]) {
    assert.ok(cell(i).includes(`<span title="${esc(TIP)}"><span class="pill warn">מצב שינה</span></span>`),cell(i));
  }
  assert.doesNotMatch(cell(2),/מצב שינה|pill warn/,'no field → no tag, and no contrary claim');
});

test('the drawer: the tag in the chip row, and "המקור בקליטה" in the overview', () => {
  const {run}=withImages();
  const html=run(`imageDrawerHtml(IMAGES[0])`);
  assert.ok(html.includes(`<span class="n-chip w" title="${esc(TIP)}">מצב שינה</span>`),'chip');
  assert.match(html,/<span class="k">המקור בקליטה<\/span><span class="v"><span class="hib-src">[^<]*<\/span><br><span class="muted">המקור עצמו לא נכתב\.<\/span><\/span>/);
  assert.ok(html.includes(`<span class="hib-src">${SRC}</span>`),'the existing capture sentence, word for word');
  assert.ok(!html.includes(LOG),'unhibernated: no $LogFile sentence');
});

test('the drawer: unhibernated_logfile adds the $LogFile sentence', () => {
  const {run}=withImages();
  const html=run(`imageDrawerHtml(IMAGES[1])`);
  assert.ok(html.includes(`<span class="hib-src">${SRC} ${LOG}</span>`),html);
  assert.match(html,/<span class="n-chip w"[^>]*>מצב שינה<\/span>/,'the same tag for both values');
});

test('the drawer: no field → no chip and no "המקור בקליטה" row', () => {
  const {run}=withImages();
  const html=run(`imageDrawerHtml(IMAGES[2])`);
  assert.doesNotMatch(html,/מצב שינה|המקור בקליטה|hib-src/);
});

test('an unknown src_unhib value is not tagged (only the two values of interfaces.md)', () => {
  const {run}=withImages();
  assert.equal(run(`imageHibernated({src_unhib:'something_else'})`),false);
  assert.equal(run(`imageHibernated({src_unhib:null})`),false);
  assert.equal(run(`imageHibernated(null)`),false);
});

test('the round window: " · מצב שינה" in the option, and the orange line under the choice -- not blocking', () => {
  const {run}=withImages();
  run(`ROOM={round:null,machines:[],disk_floor:null}; MACHINES=[]; DEPLOY.form.image='img_hib';`);
  const html=run('roomNewWindow(roomMachines())');
  assert.match(html,/<option value="img_hib" selected>Office 2024 — HP-BUILD-2 · נכנס לדיסק מ-238 GB · מצב שינה<\/option>/);
  assert.match(html,/<option value="img_old">Office 2024 — סטנדרט · נכנס לדיסק מ-238 GB<\/option>/,'untagged image: no suffix');
  assert.doesNotMatch(html,/<option value="img_hib"[^>]*disabled/,'the tag does not block');
  // נדב 04/11: השדה ברוחב מלא, כדי שהסיומת לא תיחתך כשהרשימה סגורה (כמו במוקאפ); הגרסה אחרי ההסבר
  assert.match(html,/<label class="full">אימג'<select id="rn-image"/);
  assert.ok(html.indexOf('id="rn-hib"')<html.indexOf('id="rn-version"'),'the orange line sits right under the image, before the version');
  assert.ok(html.includes(`<div class="full rn-hib" id="rn-hib" aria-live="polite"><span class="n-chip w">מצב שינה</span><span>${esc(ROUND)}</span></div>`),html);
});

test('the round window: an untagged choice keeps the line hidden and empty; choosing in the list updates it in place', () => {
  const {run,node}=withImages();
  run(`ROOM={round:null,machines:[],disk_floor:null}; MACHINES=[]; DEPLOY.form.image='img_old';`);
  const html=run('roomNewWindow(roomMachines())');
  assert.ok(html.includes('<div class="full rn-hib" id="rn-hib" aria-live="polite" hidden></div>'),html);
  const box=node('#rn-hib');
  run(`roomFormSet('image','img_hib')`);
  assert.equal(box.hidden,false); assert.ok(box.innerHTML.includes(ROUND.replace(/'/g,'&#39;')),box.innerHTML);
  run(`roomFormSet('image','img_old')`);
  assert.equal(box.hidden,true); assert.equal(box.innerHTML,'');
});
