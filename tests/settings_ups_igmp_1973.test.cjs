// ‏#1972 + #1973 (נדב 22/10): מתג ה-IGMP querier בכרטיס "שיכפול והפצה", ושורת ה-UPS
// בכרטיס "כללי" — המצב השמור והקריאה האחרונה של המנטר. בלי דפדפן: console.js ב-vm.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const js=fs.readFileSync(path.join(root,'console.js'),'utf8');

function setup() {
  const node=()=>({value:'',innerHTML:'',textContent:'',dataset:{},style:{removeProperty(){}},
    classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
    querySelector(){return null;},querySelectorAll(){return [];},setAttribute(){},removeAttribute(){},focus(){}});
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:node,
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false})},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx);
  vm.runInContext(js,ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",capabilities:{}};');
  return run;
}
const MODES={none:'אין UPS',usb:'USB — מחובר לשרת',net:'רשת — שרת NUT'};

test('the IGMP querier is a switch in the deploy card, on when unset', () => {
  const run=setup();
  run('SETTINGS={}');
  const row=run('JSON.stringify(settingRow("igmp_querier"))');
  assert.match(row,/"card":"deploy"/);
  assert.match(row,/"type":"switch"/);
  assert.equal(run('settingCurrent("igmp_querier")'),true);
  run('SETTINGS={igmp_querier:"false"}');
  assert.equal(run('settingCurrent("igmp_querier")'),false);
});

test('UPS row: unread is not "no UPS", and the live state is shown', () => {
  const run=setup();
  run('UPS_STATUS=null; upsError="HTTP 500"');
  assert.match(run('upsRowHtml()'),/HTTP 500/);
  run(`UPS_STATUS=${JSON.stringify({mode:'none',target:'',modes:MODES,state:'not_applicable',detail:'לא הוגדר UPS'})}`);
  const none=run('upsRowHtml()');
  assert.match(none,/אין UPS/);
  assert.doesNotMatch(none,/לא נבדק/);
  run(`UPS_STATUS=${JSON.stringify({mode:'net',target:'rack@10.0.0.5',modes:MODES,state:'warn',detail:'rack@10.0.0.5: על סוללה — החשמל נפל · סוללה 60%'})}`);
  const net=run('upsRowHtml()');
  assert.match(net,/rack@10\.0\.0\.5/);
  assert.match(net,/החשמל נפל/);
  assert.match(net,/upsSheet\(\)/);
  run(`UPS_STATUS=${JSON.stringify({mode:'usb',target:'',modes:MODES,state:null,detail:null})}`);
  assert.match(run('upsRowHtml()'),/לא נבדק/);
});

test('the UPS sheet posts mode and target, never prompt()', () => {
  const start=js.indexOf('function upsSheet()');
  const body=js.slice(start, js.indexOf('\n}\n', start));
  assert.match(body,/sheet\(\{/);
  assert.match(body,/type: "radio"/);
  assert.match(body,/post\("\/ups"/);
  assert.doesNotMatch(body,/prompt\s*\(/);
  assert.match(js,/UPS_STATUS = await api\("\/ups"\)/);
});
