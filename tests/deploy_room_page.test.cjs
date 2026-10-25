// ‏#954 גל 4: סבב הפצה — חדר המשכפלים (#695), לפי docs/design/console-redesign/deploy.md
// §"המבנה — חדר המשכפלים". בלי דפדפן: console.js + net.js + progress.js ב-vm מול DOM מזערי
// ו-fetch מזויף. עיקרון 5: "לא נבדק" ≠ ירוק; ROOM null (לא נקרא) ≠ חדר בלי סבב.
// הכרעת נדב 16/09: כתום = בלי תשובה ממשיך לכתוב, אדום = בלי תשובה מדלג — לעולם לא "דלג בשניהם".
// עיקרון 7: עצירת סבב מאחורי חלון אישור (#1270). "דיסק N · SATA N-1", לעולם לא sd*.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const minutesAgo=(n)=>new Date(Date.now()-n*60000).toISOString();
const GB256=256060514304, GB512=512110190592;

const B1='c8:d9:d2:0b:fe:32', C1='a0:48:1c:8a:18:40', C2='78:ac:c0:9b:11:c2', C3='78:ac:c0:9b:11:c3';
const drawer=(dev,port,serial,extra={})=>({dev,port,serial,model:'870 EVO',size_bytes:GB256,fresh:true,selected:false,state:null,error:null,bytes_written:0,bytes_total:0,stalled_s:null,crc_delta:null,smart:'unchecked',...extra});
const round=(extra={})=>({id:'rnd_1',image_id:'img_1',image_name:'office365',target_drives:12,written_drives:5,remaining_drives:7,wave_number:2,wave_state:'running',ready_drives:5,opened_by:'nadav',source:{kind:'library'},...extra});
const floor={mac:C2,name:'מחשב 2',port:3,dev:'sdc',size_bytes:GB256};
/* ‏#1269: GET /room/driver-models — הדגמים שיש להם חבילה בספרייה */
const MODELS=[{vendor:'HP',model:'HP EliteDesk 800 G6',packages:['g6-all']},{vendor:'LENOVO',model:'ThinkCentre M70q Gen 3',packages:['m70q-nic']}];

function roomIdle() {
  return {round:null,stream_stalled:false,disk_floor:floor,machines:[
    {mac:C1,name:'מחשב 1',drawer_count:2,awake:true,drawers:2,fresh_drawers:2,joined:false,state:null,bytes_written:0,bytes_total:0,error:null,
      drawer_list:[drawer('sda',1,'S5Y10',{smart:'ok'}),drawer('sdb',2,'S5Y11',{smart:'ok'})]},
    {mac:C2,name:'מחשב 2',drawer_count:3,awake:true,drawers:3,fresh_drawers:3,joined:false,state:null,bytes_written:0,bytes_total:0,error:null,
      drawer_list:[drawer('sda',1,'S5Y30',{smart:'ok'}),drawer('sdb',2,'S5Y31',{smart:'warn'}),drawer('sdc',3,'S5Y2NX0R12345',{smart:'ok'})]},
    {mac:C3,name:'מחשב 3',drawer_count:3,awake:false,drawers:0,fresh_drawers:0,drawer_list:[],joined:false,state:null,bytes_written:0,bytes_total:0,error:null}]};
}
/* גל רץ: מחשב 1 כותב על שתי מגירות, מחשב 2 — אחת כותבת, אחת כתומה (CRC), אחת נכשלה; מחשב 3 לא מחובר. */
function roomRunning(wave={}) {
  const r=roomIdle(); r.round=round(wave);
  r.machines[0]={...r.machines[0],joined:true,state:'running',bytes_written:38,bytes_total:100,
    drawer_list:[drawer('sda',1,'S5Y10',{smart:'ok',state:'writing',bytes_written:38,bytes_total:100,stalled_s:2}),drawer('sdb',2,'S5Y11',{smart:'ok',state:'writing',bytes_written:38,bytes_total:100,stalled_s:3})]};
  r.machines[1]={...r.machines[1],joined:true,state:'running',bytes_written:37,bytes_total:100,
    drawer_list:[drawer('sda',1,'S5Y30',{smart:'ok',state:'writing',bytes_written:37,bytes_total:100,stalled_s:2}),
                 drawer('sdb',2,'S5Y31',{smart:'warn',state:'writing',bytes_written:37,bytes_total:100,crc_delta:3,stalled_s:4}),
                 drawer('sdc',3,'S5Y2NX0R12345',{smart:'ok',state:'failed',error:'queue overflow: target lost bytes',bytes_written:4,bytes_total:100})]};
  return r;
}

function setup({room=roomIdle(),role='admin',session=null,prompts={}}={}) {
  const nodes=new Map(), requests=[], opened=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const fixtures={
    '/groups':[{id:'grp_BUILD',label:'מחשבי בנייה',role:'build',sort:1,machines:1},{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:2,machines:3},{id:'grp_LAB303',label:'כיתה 303 — סייבר',role:'classroom',sort:3,machines:2}],
    '/machines':[
      {mac:B1,suffix:'בנייה 1',group_id:'grp_BUILD',note:null,drawer_count:null,prompt:null,disks:[{dev:'sda',size_bytes:GB512,model:'870 EVO',serial:'S5YB1',port:1,smart:'ok'}],disks_reported_at:minutesAgo(3),inventory:null,inventory_seen_at:null},
      {mac:C1,suffix:'מחשב 1',group_id:'grp_CLONERS',note:null,drawer_count:2,prompt:prompts[C1]||null,disks:[{dev:'sda',size_bytes:GB256,model:'870 EVO',serial:'S5Y10',port:1,smart:'ok'},{dev:'sdb',size_bytes:GB256,model:'870 EVO',serial:'S5Y11',port:2,smart:'ok'}],disks_reported_at:minutesAgo(1),inventory:null,inventory_seen_at:null},
      {mac:C2,suffix:'מחשב 2',group_id:'grp_CLONERS',note:null,drawer_count:3,prompt:prompts[C2]||null,disks:[{dev:'sda',size_bytes:GB256,model:'870 EVO',serial:'S5Y30',port:1,smart:'ok'},{dev:'sdb',size_bytes:GB256,model:'870 EVO',serial:'S5Y31',port:2,smart:'warn'},{dev:'sdc',size_bytes:GB256,model:'870 EVO',serial:'S5Y2NX0R12345',port:3,smart:'ok'}],disks_reported_at:minutesAgo(1),inventory:null,inventory_seen_at:null},
      {mac:C3,suffix:'מחשב 3',group_id:'grp_CLONERS',note:null,drawer_count:3,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null},
      {mac:'b4:2e:99:07:1a:c1',suffix:'01',group_id:'grp_LAB303',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null},
      {mac:'b4:2e:99:07:1a:c2',suffix:'02',group_id:'grp_LAB303',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null}],
    '/net':[
      {mac:C1,ip:'10.44.12.118',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(1),registered:true,name:'מחשב 1',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',boot:null},
      {mac:C2,ip:'10.44.12.59',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(1),registered:true,name:'מחשב 2',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',boot:null}],
    '/disk-failures':[],
    '/images':[
      {id:'img_1',name:'office365',description:'',folder:'',sort:1,family:'windows',os:'windows',created:'2026-09-12T10:00:00Z',source_disk_bytes:GB256,total_compressed_bytes:20e9,partitions:4,min_target_bytes:53005516800},
      {id:'img_2',name:'win512-full',description:'',folder:'מרצים',sort:2,family:'windows',os:'windows',created:'2026-09-16T10:00:00Z',source_disk_bytes:GB512,total_compressed_bytes:210e9,partitions:4,min_target_bytes:300e9},
      {id:'img_3',name:'kali',description:'',folder:'',sort:3,family:'linux',os:'linux',created:'2026-09-10T10:00:00Z',source_disk_bytes:GB256,total_compressed_bytes:9e9,partitions:2,min_target_bytes:null}],
    '/overview':{images:3,machines:6,storage:null,pulls:[],room:room.round,session},
    '/room':room,
    '/room/driver-models':MODELS,
    '/room/history':[{id:'rr_12',state:'closed',image_id:'img_9',image_name:'win11-base',source_kind:'library',target_drives:6,written_drives:6,opened_by:'nadav',created_at:'2026-09-12T08:00:00Z',closed_at:'2026-09-12T09:30:00Z',failed_reason:null}],
    '/room/wake':{sent:3,failed:0,reasons:[]},
    '/room/start':{ok:true},
    '/room/close':{ok:true},
  };
  if(role==='deploy') for(const k of ['/health','/journal','/update','/monitor/machines','/tasks']) fixtures[k]=403;
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Error,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(...a){opened.push(a);}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      const v=fixtures[key];
      if(v===403) return {status:403,ok:false,json:async()=>({detail:'admin only'})};
      if(v&&v.__status) return {status:v.__status,ok:false,json:async()=>({detail:v.detail})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>v ?? {ok:true}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run(`ME={username:"${role}",role:"${role}",server_name:"srv",version:"v0.32.1",capabilities:{classrooms:true}}; current="deploy"; sheet = o => { globalThis.formOptions=o; }; selectPageById=(id)=>{ globalThis.selected=id; current=id; }; toast=(m)=>{ globalThis.toasted=m; }; renderCurrent=()=>{ globalThis.rendered=(globalThis.rendered||0)+1; }; activateTab=(i)=>{ globalThis.activated=i; }; monitorMachine=(mac)=>{ globalThis.monitored=mac; };`);
  for(const [name,key] of [['OVERVIEW','/overview'],['MACHINES','/machines'],['GROUPS','/groups'],['DISK_FAILURES','/disk-failures'],['NET','/net'],['ROOM','/room'],['IMAGES','/images']]) run(name+'='+JSON.stringify(fixtures[key]));
  run('ROOM_KEY=JSON.stringify([ROOM,MACHINES.map(m=>[m.mac,m.prompt])]);');
  return {run,node,requests,fixtures,ctx,opened};
}
function balanced(html) {
  const stack=[]; const voids=new Set(['input','br','hr','img','meta','link','source','wbr','path','rect','circle']);
  for(const m of html.matchAll(/<\/?([a-z][\w-]*)\b[^>]*>/gi)) {
    const tag=m[1].toLowerCase();
    if(m[0].endsWith('/>') || voids.has(tag)) continue;
    if(m[0].startsWith('</')) assert.equal(stack.pop(),tag,'Unbalanced '+m[0]); else stack.push(tag);
  }
  assert.deepEqual(stack,[],'Unclosed tags');
}
const between=(html,a,b)=>html.slice(html.indexOf(a),b?html.indexOf(b,html.indexOf(a)):undefined);
const mach=(html,mac)=>html.slice(html.indexOf(`data-mac="${mac}"`)).split(/<div class="mach(?: err| off)?" data-mac=/)[0];
const card=(html,mac)=>html.slice(html.indexOf(`data-mac="${mac}"`)).split(/<div class="mcard(?: off)?"/)[0];
const kpi=(html,label)=>between(html,`<div class="l">${label}</div>`,'</div><div class="s">')+between(html,`<div class="l">${label}</div>`).slice(0,600);

test('page registry: deploy is an own page with three tabs, the room first; the old renderer, drawer and "+ action" entries are gone', () => {
  const {run}=setup();
  assert.equal(run('pages.deploy.own'),true); assert.equal(run('pages.deploy.load===loadDeploy'),true);
  assert.equal(JSON.stringify(run('deployTabs()')),JSON.stringify(['חדר המשכפלים','כיתה','היסטוריה']));
  assert.equal(run('typeof emptyDataCard'),'function','still used by the network page');
  assert.equal(run('typeof startSelectedRound'),'undefined'); assert.equal(run('typeof openNewDeployment'),'undefined');
  assert.equal(run('typeof roundDrawerOpen'),'undefined','no round drawer any more');
  run('openRoundDetail()'); assert.equal(run('selected'),'deploy'); assert.equal(run('activated'),1,'"details" opens the class tab, not a drawer');
  for(const r of ['admin','deploy']) { const s=setup({role:r}); assert.equal(s.run("pageAllowed('deploy')"),true,r); }
});

test('no round (mockup console-full.html P.deploy): "סבב הפצה" + "פנוי", "אין סבב פעיל", "התחל סבב" in the header and in the empty state of "חדר המשכפלים"; the room grid lives in the start window', () => {
  const {run}=setup();
  const html=run('deploy(0)'); balanced(html);
  assert.match(html,/obj-name">סבב הפצה<span class="pill ">פנוי<\/span><\/div><div class="obj-sub"[^>]*>אין סבב פעיל</);
  assert.match(html,/<div class="obj-actions"><button class="btn primary" onclick="openRoomRound\(\)"><svg[^]*?<\/svg> התחל סבב<\/button><\/div>/,'the header holds the one action of the mockup');
  assert.doesNotMatch(html,/startWave|stopRoom|toggleRoomBig|wakeRoom|refreshPage/,'no wave to start or stop; the big view, room WoL and refresh are not in the mockup');
  assert.doesNotMatch(html,/class="kpi/,'no tiles without a round');
  assert.doesNotMatch(html,/class="rgrid|class="mgrid|data-mac=/,'mockup: no machine grid without a round');
  const room=between(html,'<div class="c12 card">');
  assert.match(room,/^<div class="c12 card"><div class="card-h"><span>חדר המשכפלים<\/span><div class="acts"><button class="btn sm danger" onclick="poweroffRoom\('grp_CLONERS'\)">כיבוי כל המשכפלים…<\/button><\/div><\/div>/);
  assert.match(room,/<div class="empty"><span class="eic"><svg[^]*?<\/svg><\/span><b class="et">אין סבב פעיל<\/b><p>בחרו אימג' ומחשבי יעד כדי להתחיל\. מחשבים כבויים יידלקו דרך WoL\.<\/p><button class="btn primary" onclick="openRoomRound\(\)">התחל סבב<\/button><\/div>/);
  const d=setup({role:'deploy'}).run('deploy(0)');
  assert.match(d,/openRoomRound\(\)"><svg[^]*?<\/svg> התחל סבב/,'deploy opens rounds'); assert.doesNotMatch(d,/poweroffRoom/,'powering the room off is for an admin (poweroffRoom)');
  const v=setup(); v.run('ME.role="viewer"'); assert.doesNotMatch(v.run('deploy(0)'),/openRoomRound|poweroffRoom/,'no action without the right');
  assert.doesNotMatch(html,/class="rnew"/,'the old inline card is gone');
  const form=run('roomNewWindow(roomMachines())');
  assert.match(form,/aria-pressed="true"[^>]*>מהשרת \(הספרייה\)/); assert.match(form,/aria-pressed="false"[^>]*>מדיסק מחשב בנייה \(הפצה ישירה\)/,'a builder that reported disks → #715 offered');
  assert.match(form,/<select id="rn-image"/); assert.match(form,/<input type="number" id="rn-target" min="1" value="5"/,'default target = the ready drawers, like the GUI (5 awake)');
  assert.doesNotMatch(form,/openRoomRoundSubmit/,'#1485 (S350): the primary action is in the window footer, not at the end of the body');
  assert.match(form,/class="rn-m" data-mac="a0:48:1c:8a:18:40"[^]*class="rn-m off" data-mac="78:ac:c0:9b:11:c3"/,'every cloner and its drawers are in the start window, off ones too');
  run('openRoomRound()');
  assert.equal(run("document.getElementById('modalOk').textContent"),'פתח סבב'); assert.equal(run("document.getElementById('modalOk').disabled"),true,'nothing ticked → the footer action waits');
  assert.equal(run("document.getElementById('modalHint').textContent"),'יש לסמן לפחות דיסק אחד'); assert.match(run("document.getElementById('modalHint').className"),/rn-sum need/);
  assert.match(form,/ברירת המחדל = 5 מגירות מוכנות/); assert.match(form,/id="rn-version"><option>הנוכחית \(ברירת מחדל\)/);   // אפשרות אחת, לא מנוטרל (README §8)
  assert.equal(run('typeof formOptions'),'undefined');
});

test('the mockup-only controls are not in the product: "מצב לדוגמה" (active / no round), "הצג מה השתנה"; the page has no round number the API does not give', () => {
  const pages=[setup().run('deploy(0)'),setup({room:roomRunning()}).run('deploy(0)'),setup({room:roomRunning({wave_state:'open'})}).run('deploy(0)')];
  for (const html of pages) {
    assert.doesNotMatch(html,/מצב לדוגמה|הצג מה השתנה|data-round=|hint-demo|chgSw/);
    assert.doesNotMatch(html,/סבב \d/,'room_rounds.id is "room_xxxx" -- there is no "round 12" to show');
  }
  for (const f of ['console.js','index.html','console.css']) {
    const src=fs.readFileSync(path.join(root,f),'utf8');
    assert.doesNotMatch(src,/מצב לדוגמה|הצג מה השתנה|data-round=|hint-demo/,f);
  }
});

test('#953: every image says "fits a disk from X GB"; one that does not fit the smallest drawer is disabled with the exact server refusal string; unknown floor → note, nothing disabled', () => {
  const {run}=setup();
  run("roomFormSet('folder','-')");   // folder-first: the no-folder images
  let html=run('roomNewWindow(roomMachines())');
  assert.match(html,/<option value="img_1">office365 · נכנס לדיסק מ-54 GB<\/option>/);
  assert.match(html,/<option value="img_3">kali · גודל נדרש לא ידוע<\/option>/,'min_target_bytes null ≠ 0');
  run("roomFormSet('folder','מרצים')"); html=run('roomNewWindow(roomMachines())');
  assert.match(html,/<option value="img_2" disabled>win512-full · נכנס לדיסק מ-300 GB — לא נכנס: דיסק 3 במחשב מחשב 2 הוא 256GB, האימג&#39; צריך 300GB<\/option>/);
  assert.match(html,/המגירה הקטנה ביותר בחדר: 256GB \(דיסק 3 במחשב מחשב 2\)/);
  assert.equal(run("imageFitReason(IMAGES[1],ROOM.disk_floor)"),"דיסק 3 במחשב מחשב 2 הוא 256GB, האימג' צריך 300GB");
  assert.equal(run("imageFitReason(IMAGES[0],ROOM.disk_floor)"),null); assert.equal(run("imageFitReason(IMAGES[1],null)"),null);
  assert.equal(run("imageFitReason(IMAGES[1],{name:'מחשב 2',port:null,dev:'nvme0n1',size_bytes:256e9})"),"דיסק nvme0n1 במחשב מחשב 2 הוא 256GB, האימג' צריך 300GB",'no port → device name, as the server does');
  run('delete ROOM.disk_floor'); html=run('roomNewWindow(roomMachines())');
  assert.match(html,/גודל הדיסקים לא ידוע — יסורב במכונה אם לא ייכנס/); assert.doesNotMatch(html,/<option[^>]*disabled/);
});

test('#1354: when the minimum free space is what makes an image too big, the reason says so -- the exact string of room.fit_refusal', () => {
  const {run}=setup();
  const floor="{name:'מחשב 2',port:3,dev:'sdc',size_bytes:256060514304}";
  assert.equal(run(`imageFitReason({min_target_bytes:258659844096,layout_bytes:242553716736,min_free_gb:20},${floor})`),
    "דיסק 3 במחשב מחשב 2 הוא 256GB, האימג' צריך 259GB (כולל 20GB פנויים במחיצת המערכת)");
  assert.equal(run(`imageFitReason({min_target_bytes:300e9,layout_bytes:300e9,min_free_gb:20},${floor})`),
    "דיסק 3 במחשב מחשב 2 הוא 256GB, האימג' צריך 300GB", 'the layout alone is too big: no suffix');
  assert.equal(run(`imageFitReason({min_target_bytes:null,layout_bytes:242553716736,min_free_gb:20},${floor})`), null,
    'C: not measured is not a SIZE refusal -- imageDeployBlock (#1494) blocks it by the agent\'s name ("not checked")');
});

test('#1494: an image the agent cannot check for free space (C: used_bytes not measured) is disabled with the agent\'s reason -- not offered as "size unknown", with or without a known floor', () => {
  const {run}=setup();
  const why="לא נבדק אם יישארו 20GB פנויים ב-C: — לא נמדד כמה תפוס בה (used_bytes חסר)";
  run(`IMAGES[2].min_free_unchecked=${JSON.stringify(why)}`);
  run("roomFormSet('folder','-')");
  let html=run('roomNewWindow(roomMachines())');
  assert.match(html,/<option value="img_3" disabled>kali · גודל נדרש לא ידוע — לא ניתן לפרוס: לא נבדק אם יישארו 20GB פנויים ב-C: — לא נמדד כמה תפוס בה \(used_bytes חסר\)<\/option>/);
  assert.match(html,/<option value="img_1">office365 · נכנס לדיסק מ-54 GB<\/option>/,'a measured image is untouched');
  run('delete ROOM.disk_floor'); html=run('roomNewWindow(roomMachines())');
  assert.match(html,/<option value="img_3" disabled>/,'no floor known: still disabled -- the refusal does not depend on drawer size');
  assert.equal(run('imageDeployBlock(IMAGES[0])'),null);
  assert.equal(run('imageDeployBlock({min_free_unchecked:null})'),null);
});

test('folder-first image pick (like the GUI): folders with counts first, the image list is disabled until one is chosen and shows only that folder; a chosen image decides the folder; changing folder clears the image', () => {
  const {run}=setup();
  let html=run('roomNewWindow(roomMachines())');
  assert.match(html,/<select id="rn-folder"[^>]*><option value="">— בחר תיקייה —<\/option><option value="-">ללא תיקייה \(2\)<\/option><option value="מרצים">מרצים \(1\)<\/option><\/select>/);
  assert.match(html,/<select id="rn-image"[^>]*disabled><option value="">— בחר קודם תיקייה —<\/option><\/select>/,'no folder → no image list');
  run("roomFormSet('folder','מרצים')"); html=run('roomNewWindow(roomMachines())');
  assert.match(html,/<option value="מרצים" selected>/); assert.doesNotMatch(html,/<option value="img_1"/,'only that folder images');
  assert.doesNotMatch(between(html,'id="rn-image"','</select>'),/disabled><option/);
  run("roomFormSet('image','img_2'); roomFormSet('folder','-')");
  assert.equal(run('DEPLOY.form.image'),'','another folder clears the image');
  run("roomFormSet('image','img_1')"); run("DEPLOY.form.folder=null"); html=run('roomNewWindow(roomMachines())');
  assert.match(html,/<option value="-" selected>ללא תיקייה/,'a chosen image decides the folder');
});

test('new round: POST /room {image_id, target_drives}; a 409 fit refusal is shown in the card verbatim; #715 source sends target_slots grouped by machine from the connected drawers', async () => {
  const {run,requests,fixtures}=setup();
  run("roomFormSet('image','img_1'); roomFormSet('target','6'); roomPickAll()");   // ‏#1275: בלי בחירה אין סבב
  await run('openRoomRoundSubmit()');
  let r=requests.find(x=>x.url==='/api/console/room'&&x.options.method==='POST');
  assert.deepEqual(JSON.parse(r.options.body),{image_id:'img_1',target_drives:6,target_slots:[{mac:C1,ports:[1,2]},{mac:C2,ports:[1,3]}]});
  assert.match(run('toasted'),/הסבב נפתח/); assert.equal(run('DEPLOY.err'),'');
  assert.ok(requests.some(x=>x.url==='/api/console/images')&&requests.some(x=>x.url==='/api/console/overview'),'loadDeploy re-reads after opening');
  fixtures['/room']={__status:409,detail:"דיסק 3 במחשב מחשב 2 הוא 256GB, האימג' צריך 300GB"};
  run("roomFormSet('image','img_1'); roomPickAll()"); await run('openRoomRoundSubmit()');
  assert.equal(run('DEPLOY.err'),"דיסק 3 במחשב מחשב 2 הוא 256GB, האימג' צריך 300GB");
  fixtures['/room']=roomIdle(); const html=run('roomNewWindow(roomMachines())');
  assert.match(html,/<div class="note err"><span aria-hidden="true">●<\/span><span>הסבב לא נפתח: דיסק 3 במחשב מחשב 2 הוא 256GB, האימג&#39; צריך 300GB<\/span><\/div>/);
  run("DEPLOY.err=''; roomFormSet('image','')"); await run('openRoomRoundSubmit()'); assert.equal(run('DEPLOY.err'),"בחר אימג'");
  requests.length=0; run("roomFormSet('src','build_disk'); roomFormSet('disk','sda')");
  assert.match(run('roomNewWindow(roomMachines())'),/היעד: הדיסקים שסומנו בגריד למטה/);
  await run('openRoomRoundSubmit()');
  r=requests.find(x=>x.url==='/api/console/room'&&x.options.method==='POST');
  assert.deepEqual(JSON.parse(r.options.body),{source:{kind:'build_disk',mac:B1,disk:'sda'},target_slots:[{mac:C1,ports:[1,2]},{mac:C2,ports:[1,3]}]});
  const d=setup({role:'deploy'}); d.run("roomFormSet('image','img_1'); roomPickAll()"); await d.run('openRoomRoundSubmit()');
  assert.ok(d.requests.some(x=>x.url==='/api/console/room'&&x.options.method==='POST'),'deploy is a room operator');
});

test('running wave (mockup P.deploy): "סבב הפצה" + "פעיל", "גל N · image · N מחשבים", "עצירת הסבב…"; three tiles — overall progress, stream rate, re-sent blocks — then the room card, then the notes', () => {
  const {run}=setup({room:roomRunning()});
  const html=run('deploy(0)'); balanced(html);
  assert.match(html,/obj-name">סבב הפצה<span class="pill info">פעיל<\/span><\/div><div class="obj-sub"[^>]*>גל 2 · office365 · 3 מחשבים</);
  assert.match(html,/<div class="obj-actions"><button class="btn danger" onclick="stopRoom\(\)">עצירת הסבב…<\/button><\/div>/,'the one header action of the mockup');
  assert.doesNotMatch(html,/startWave|openRoomRound|wakeRoom\(\)">|toggleRoomBig|refreshPage/);
  assert.doesNotMatch(html,/rnew/,'no new-round form while a round is active');
  const order=['<div class="c12 kpis">','<div class="c12 card"><div class="card-h"><span>חדר המשכפלים','<div class="c12 rnotes">'].map((x)=>html.indexOf(x));
  assert.ok(order.every((i,k)=>i>0&&(k===0||i>order[k-1])),'tiles → room card → notes '+order);
  const kpis=between(html,'<div class="c12 kpis">','<div class="c12 card">');
  assert.equal((kpis.match(/<div class="kpi /g)||[]).length,3,'three tiles, like the mockup');
  assert.match(kpis,/<div class="kpi info"><div class="l">התקדמות כוללת<\/div><div class="v"><bdi dir="auto">31%<\/bdi><\/div><\/div>/,'the average of the joined drawers (wavePct)');
  assert.match(kpis,/<div class="kpi info"><div class="l">קצב זרם<\/div><div class="v"><bdi dir="auto">—<\/bdi><\/div><div class="s"><b title="[^"]*throughput_bps[^"]*">לא נמדד<\/b><\/div><\/div>/,'null → "not measured", not 0 (#989)');
  assert.match(kpis,/<div class="kpi info"><div class="l">בלוקים שודרו שוב<\/div><div class="v"><bdi dir="auto">—<\/bdi><\/div><div class="s"><b title="[^"]*loss_blocks[^"]*">לא נמדד<\/b><\/div><\/div>/);
  assert.doesNotMatch(html,/Gb\/s|איבוד 0|מתוך 3 גלים/,'no invented rate, loss or wave total');
  const foot=between(html,'<div class="room-foot">','<div class="c12 rnotes">');
  assert.match(foot,/^<div class="room-foot"><p>גל 2 · נכתבו 5 מתוך 12 · נשארו 7 · [^<]*<\/p><p>הגל משדר — /,'what the mockup has no room for and needs no action is a quiet line at the foot of the room card, not a banner');
  assert.doesNotMatch(between(html,'<div class="c12 rnotes">'),/נכתבו 5 מתוך 12|הגל משדר/,'the wave line and the running line are no longer blue notes');
  const s2=setup({room:roomRunning()}); s2.run("ROOM.machines.forEach(m=>(m.drawer_list||[]).forEach(d=>{d.state=null;d.bytes_total=0;}))");
  assert.match(s2.run('deploy(0)'),/התקדמות כוללת<\/div><div class="v"><bdi dir="auto">—<\/bdi><\/div><div class="s"><b title="עוד אין דיווח כתיבה מהמגירות שהצטרפו">לא נמדד<\/b>/,'no report → "—", not 0%');
});

test('running wave: the stream tiles come from /room.round (udp-sender of the server, #989) — 0 is a measurement, null is not', () => {
  const {run}=setup({room:roomRunning({throughput_bps:117964800,loss_blocks:0})});
  let html=run('deploy(0)');
  assert.match(html,/<div class="kpi info"><div class="l">קצב זרם<\/div><div class="v"><bdi dir="auto">113 MB\/s<\/bdi><\/div><\/div>/);
  assert.match(html,/<div class="kpi info"><div class="l">בלוקים שודרו שוב<\/div><div class="v"><bdi dir="auto">0<\/bdi><\/div><\/div>/,'0 re-sent blocks is a measurement');
  assert.doesNotMatch(between(html,'<div class="c12 kpis">','<div class="c12 card">'),/דורש API|לא נמדד/);
  html=setup({room:roomRunning({throughput_bps:null,loss_blocks:37})}).run('deploy(0)');
  assert.match(html,/קצב זרם<\/div><div class="v"><bdi dir="auto">—<\/bdi><\/div><div class="s"><b title="[^"]*">לא נמדד<\/b>/);
  assert.match(html,/בלוקים שודרו שוב<\/div><div class="v"><bdi dir="auto">37<\/bdi><\/div><\/div>/);
});

test('#447: the wave rate line names the drive that lowered it; without wave_rate nothing is shown', () => {
  const learned={max_bitrate:'108m',ceiling:'200m',basis:'learned',projected:false,unknown_drives:2,drives:5,
    by:{serial:'S5Y31',model:'870 EVO',mac:C2,port:2,rate_bps:15000000}};
  /* the whole page: kpi() keeps 600 characters, and this line comes after the stream line */
  let k1=setup({room:roomRunning({wave_rate:learned})}).run('deploy(0)');
  assert.match(k1,/קצב לגל: <bdi dir="ltr">108m<\/bdi> \(נקבע לפי 870 EVO <span class="mono">S5Y31<\/span> · מחשב 2 דיסק 2\) · 2 כוננים בלי היסטוריה/);
  k1=setup({room:roomRunning({wave_rate:{...learned,basis:'start',max_bitrate:'600m',by:null,unknown_drives:0,projected:true}})}).run('deploy(0)');
  assert.match(k1,/צפוי קצב לגל: <bdi dir="ltr">600m<\/bdi> \(קצב פתיחה — מחשב שיכפול בלי גל קודם\)/);
  assert.doesNotMatch(k1,/בלי היסטוריה/);
  /* the automatic rate (Nadav 11/10): the reason the server moved — no new control, only the reason */
  for (const [basis,rate,why] of [['up','700m','עלה — הגל הקודם נקי'],['down','450m','ירד — שידורים חוזרים'],
    ['held','600m','נשאר — הגל הקודם לא הוכיח שאפשר לעלות'],['unmeasured','600m','נשאר — הגל הקודם לא נמדד'],
    ['ceiling','900m','בתקרה — לא עולים מעליה']]) {
    k1=setup({room:roomRunning({wave_rate:{...learned,basis,max_bitrate:rate,by:null,unknown_drives:0}})}).run('deploy(0)');
    assert.ok(k1.includes(`קצב לגל: <bdi dir="ltr">${rate}</bdi> (${why})`),basis);
  }
  k1=setup({room:roomRunning()}).run('deploy(0)');
  assert.doesNotMatch(k1,/קצב לגל/,'no wave_rate (old server, build-machine source) → no invented line');
});

test('Nadav 15/10: the wave rate line says the deploy card and the ceiling it gives; an unread card is said, never guessed', () => {
  const w={max_bitrate:'6000m',ceiling:'9000m',start:'6000m',basis:'start',by:null,projected:true,unknown_drives:0,drives:4,
    link:{interface:'eth1',speed_mbps:10000,why:null,applied:true}};
  let k1=setup({room:roomRunning({wave_rate:w})}).run('deploy(0)');
  assert.ok(k1.includes('כרטיס ההפצה: <bdi dir="ltr">10Gb/s</bdi> · תקרת שידור: <bdi dir="ltr">1.1GB/s</bdi>'),k1.slice(0,400));
  k1=setup({room:roomRunning({wave_rate:{...w,ceiling:'2250m',link:{...w.link,speed_mbps:2500}}})}).run('deploy(0)');
  assert.ok(k1.includes('כרטיס ההפצה: <bdi dir="ltr">2.5Gb/s</bdi> · תקרת שידור: <bdi dir="ltr">281MB/s</bdi>'));
  k1=setup({room:roomRunning({wave_rate:{...w,ceiling:'900m',max_bitrate:'600m',link:{interface:'ens192',speed_mbps:null,why:'virtual',applied:false}}})}).run('deploy(0)');
  assert.ok(k1.includes('כרטיס ההפצה: <span title="virtual">כרטיס וירטואלי — המהירות אינה של הקו</span> · תקרת שידור: <bdi dir="ltr">113MB/s</bdi>'));
  assert.doesNotMatch(k1,/1Gb\/s/,'an unread card is never shown as 1Gb/s');
  k1=setup({room:roomRunning({wave_rate:{...w,link:undefined}})}).run('deploy(0)');
  assert.doesNotMatch(k1,/כרטיס ההפצה/,'no link (old server, build-machine source) → no invented line');
});

test('Nadav 15/10 (b): the slowest cloner that set the cap is named; an assumed 1Gb/s says it is an assumption', () => {
  const w={max_bitrate:'600m',ceiling:'900m',start:'600m',basis:'start',by:null,projected:true,unknown_drives:0,drives:4,cap_by:'cloner',
    link:{interface:'eth1',speed_mbps:10000,why:null,applied:true},
    cloner:{mac:C2,speed_mbps:1000,source:'reported',why:null,assumed:[]}};
  let k1=setup({room:roomRunning({wave_rate:w})}).run('deploy(0)');
  assert.ok(k1.includes('כרטיס ההפצה: <bdi dir="ltr">10Gb/s</bdi> · תקרה לפי מחשב 2: <bdi dir="ltr">1Gb/s</bdi> · תקרת שידור: <bdi dir="ltr">113MB/s</bdi>'));
  assert.doesNotMatch(k1,/הנחה/);
  k1=setup({room:roomRunning({wave_rate:{...w,cloner:{...w.cloner,source:'assumed',why:'no_probe',assumed:[C2]}}})}).run('deploy(0)');
  assert.ok(k1.includes('תקרה לפי מחשב 2: <bdi dir="ltr">1Gb/s</bdi> (הנחה — המהירות לא ידועה)'));
  k1=setup({room:roomRunning({wave_rate:{...w,cap_by:'server'}})}).run('deploy(0)');
  assert.doesNotMatch(k1,/תקרה לפי/,'the server card set the cap — no cloner is blamed');
});

test('running wave (mockup P.deploy): a card per cloner — name, "מחובר", IP; a row per drawer "דיסק N" · bar · percent; orange CRC; a failed drawer is "נכשל", the card red with "דיסק אדום" and which disk and why; off = dashed "לא הצטרף לסבב"; the notes say what happens without an answer', () => {
  const {run}=setup({room:roomRunning()});
  const html=run('deploy(0)');
  assert.match(html,/<div class="c12 card"><div class="card-h"><span>חדר המשכפלים <small>כשל במחשב אחד לא עוצר את השאר<\/small><\/span><div class="acts"><button class="btn sm danger" onclick="poweroffRoom\('grp_CLONERS'\)">כיבוי כל המשכפלים…<\/button><\/div><\/div>/);
  assert.doesNotMatch(html,/class="mgrid|class="legend|dsk rr|class="rn"|machine-round|machine-fail/,'the old lcbay rows and the idle square grid are gone');
  const grid=between(html,'<div class="rgrid round">','<div class="c12 rnotes">');
  assert.doesNotMatch(grid,/sd[abc]/,'never sd*');
  const c1=mach(grid,C1), c2=mach(grid,C2), c3=mach(grid,C3);
  assert.match(c1,new RegExp(`^data-mac="${C1}" title="${C1}"><div class="mt"><span>מחשב 1</span><span class="pill ok">מחובר</span></div><div class="ms"><bdi dir="ltr">10\\.44\\.12\\.118</bdi></div><div class="dsks">`),'name + "מחובר" + IP; the MAC is the tooltip');
  assert.match(c1,/<div class="dsk" data-slot="1" title="SATA 0 · 870 EVO · S5Y10 · כותב · SMART תקין"><span>דיסק 1<\/span><div class="bar " role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="38"><i style="--w:38%"><\/i><\/div><b>38%<\/b>/,'"דיסק N" · bar · percent, the mockup row; the state word is in the tooltip');
  assert.match(c2,/<div class="dsk bay-emphasis warn" data-slot="2" title="SATA 1 · 870 EVO · S5Y31 · כותב · CRC \+3 · לבדוק כבל · SMART אזהרה"><span>דיסק 2<\/span><div class="bar warn"[^>]*><i style="--w:37%"><\/i><\/div><b>37%<\/b>/);
  assert.match(c2,/<div class="dsk bad bay-emphasis err" data-slot="3" title="[^"]*נכשל בסבב — queue overflow: target lost bytes[^"]*"><span>דיסק 3<\/span><div class="bar err"><i style="--w:100%"><\/i><\/div><b>נכשל<\/b><\/div>/,'a write that failed says "נכשל" (principle 4), never a quiet skip');
  assert.match(grid,new RegExp(`<div class="mach err" data-mac="${C2}"`),'a machine with a failed drawer is red');
  assert.match(c2,/<span>מחשב 2<\/span><span class="pill err">דיסק אדום<\/span><\/div><div class="ms">דיסק 3 אדום — הדיסק לא עמד בקצב — אבדו בייטים<\/div>/,'which disk and why, on the card');
  assert.doesNotMatch(c2.replace(/title="[^"]*"/g,''),/queue overflow/,'no English on the screen; the raw reason stays in the tooltip');
  assert.match(grid,new RegExp(`<div class="mach off" data-mac="${C3}"`)); assert.match(c3,/<span>מחשב 3<\/span><span class="pill ">לא מחובר<\/span><button class="btn sm wx" onclick="wakeMachine\('[^']+'\)" title="WoL למכונה הזו בלבד \(#984\)">WoL<\/button><\/div><div class="ms">לא מחובר — לא הצטרף לסבב<\/div>/);
  assert.doesNotMatch(c1,/WoL/,'connected → no WoL');
  assert.equal(run('drawerErrorText("oops: weird")'),'הכתיבה נכשלה — הפרטים בריחוף על הדיסק'); assert.equal(run('drawerErrorText(null)'),'נכשל בכתיבה'); assert.equal(run('drawerErrorText("הדיסק נותק")'),'הדיסק נותק');
  assert.doesNotMatch(c1,/mach-note err|pill err/,'a healthy machine has no failure line');
  run("ROOM.machines[1].drawer_list[0]={...ROOM.machines[1].drawer_list[0],state:'done',bytes_written:100}");
  const d2=mach(run('deploy(0)'),C2);
  assert.match(d2,/<div class="ms">דיסק 3 אדום — הדיסק לא עמד בקצב — אבדו בייטים; דיסק 1 משוחזר<\/div>/,'what was written is said next to what failed');
  assert.match(d2,/<div class="dsk" data-slot="1" [^>]*><span>דיסק 1<\/span><div class="bar ok"[^>]*aria-valuenow="100"><i style="--w:100%"><\/i><\/div><b>100%<\/b>/);
  const notes=between(html,'<div class="c12 rnotes">');
  assert.match(notes,/<div class="note warn">[^]*?<b>מחשב 2 · דיסק 2<\/b> — SMART אזהרה \/ CRC \/ קישור איטי: <b>הסוכן ממשיך לכתוב<\/b> \(כתום = כותב\)[^]*?דילוג מרחוק — <b[^>]*>דורש API<\/b>/);
  // #1767: drawer 3's write failed in the round (smart ok) -- the red note says that, not "SMART נכשל" (see the test below)
  assert.match(notes,/<div class="note err">[^]*?<b>מחשב 2 · דיסק 3<\/b> — אדום: <b>הכתיבה בסבב נכשלה: הדיסק לא עמד בקצב — אבדו בייטים<\/b>\. /);
  assert.doesNotMatch(notes,/דלג בשניהם|onclick="[^"]*skip/,'never "skip both", never an invented skip endpoint');
});

test('#1767: the red note says the red drawer\'s own reason -- the write failed / SMART failed / read-only -- never "SMART נכשל" for all three', () => {
  const redNote=(html)=>between(html,'<div class="c12 rnotes">').match(/<div class="note err">(?:(?!<div class="note).)*/g)||[];
  // (1) the write failed in the round, SMART ok
  let notes=redNote(setup({room:roomRunning()}).run('deploy(0)'));
  assert.equal(notes.length,1,notes);
  assert.match(notes[0],/<b>מחשב 2 · דיסק 3<\/b> — אדום: <b>הכתיבה בסבב נכשלה: הדיסק לא עמד בקצב — אבדו בייטים<\/b>\. /);
  assert.doesNotMatch(notes[0],/SMART|#1732|לחלץ לשרת/,'not SMART, not "skipped", not "extract to the server"');
  // (2) SMART failed: never written -- the #1732 note, as before
  let r=roomRunning(); r.machines[1].drawer_list[2]={...r.machines[1].drawer_list[2],state:null,error:null,bytes_written:0,bytes_total:0,smart:'fail'};
  notes=redNote(setup({room:r}).run('deploy(0)'));
  assert.equal(notes.length,1,notes);
  assert.match(notes[0],/<b>מחשב 2 · דיסק 3<\/b> — אדום: <b>SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת<\/b> \(#1732: מדולג בגלוי, בלי שאלה\)/);
  // (3) read-only (#1742): its own cause, not SMART
  r=roomRunning(); r.machines[1].drawer_list[2]={...r.machines[1].drawer_list[2],state:null,error:null,bytes_written:0,bytes_total:0,smart:'ok'};
  const k=setup({room:r});
  k.run(`DISK_FAILURES=[{id:9,session_id:'r15',mac:'${C2}',dev:'sdc',serial:'S5Y2NX0R12345',port:3,disk_number:3,ata_port:'ata3',at:'2026-09-15T14:22:00Z',cause:'readonly',error:'read-only',ata_log:[]}]`);
  notes=redNote(k.run('deploy(0)'));
  assert.equal(notes.length,1,notes);
  assert.match(notes[0],/<b>מחשב 2 · דיסק 3<\/b> — אדום: <b>הדיסק במצב קריאה בלבד<\/b>\. /);
  assert.doesNotMatch(notes[0],/SMART/);
});

test('a drawer with no report is "—" (not 0%), an empty slot is "ריק", and a red drawer that was not written in the round is "דולג" with a red track', () => {
  const r=roomRunning();
  r.machines[0].drawer_list[1]={...r.machines[0].drawer_list[1],state:null,bytes_written:0,bytes_total:0};
  r.machines[1].drawer_list[2]={...r.machines[1].drawer_list[2],state:null,error:null,bytes_written:0,bytes_total:0,smart:'fail'};
  const html=setup({room:r}).run('deploy(0)'); balanced(html);
  assert.match(mach(html,C1),/<div class="dsk" data-slot="2" [^>]*><span>דיסק 2<\/span><div class="bar "><i style="--w:0%"><\/i><\/div><b>—<\/b><\/div>/);
  assert.match(mach(html,C2),/<div class="dsk bad bay-emphasis err" data-slot="3" [^>]*><span>דיסק 3<\/span><div class="bar err"><i style="--w:0%"><\/i><\/div><b>דולג<\/b><\/div>/);
  assert.match(mach(html,C2),/<div class="ms">דיסק 3 אדום — SMART נכשל<\/div>/);
  assert.match(mach(html,C3),/<div class="dsk vacant" data-slot="1"><span>דיסק 1<\/span><div class="bar "><i style="--w:0%"><\/i><\/div><b>ריק<\/b><\/div>/);
});

test('the operator question (prompt) is a note in the page: orange machine → "keeps writing", red machine → "skips"; monitor and clear links for admin, answer from console = API note', () => {
  const room=roomRunning();
  const prompts={[C1]:'Disk 2: SMART warn - write anyway? [y/n]',[C2]:'Disk 3: failed the previous clone - skip? [y/n]'};
  const {run}=setup({room,prompts});
  run(`DISK_FAILURES=[{id:7,session_id:'r15',mac:'${C2}',dev:'sdc',serial:'S5Y2NX0R12345',port:3,disk_number:3,ata_port:'ata3',at:'2026-09-15T14:22:00Z',cause:'cable',error:'ATA bus error',ata_log:[]}]; ROOM.machines[0].drawer_list[1].smart='warn';`);
  run(`ROOM.machines[1].drawer_list.find(d=>d.port===3).smart='fail'`);   // #1580: red is SMART fail (a failed-last record alone is orange)
  const html=run('deploy(0)'); balanced(html);
  const notes=between(html,'<div class="c12 rnotes">');
  const n1=between(notes,'<b>מחשב 1</b>','</div>'), n2=between(notes,'<b>מחשב 2</b>','</div>');
  assert.match(notes,/<div class="note warn"><span aria-hidden="true">●<\/span><span><b>מחשב 1<\/b> ממתין למפעיל: <span dir="ltr">Disk 2: SMART warn - write anyway\? \[y\/n\]<\/span>\. <b>כתום — בלי תשובה הסוכן ממשיך לכתוב<\/b> על מחשב 1 · דיסק 2\./);
  assert.match(n1,/selectPageById\('monitor'\)">לענות ליד המסך — דף המוניטור</,'monitor only on the monitor page (Nadav 17/09)'); assert.doesNotMatch(n1,/monitorMachine\(/); assert.match(n1,/תשובה מהקונסולה אינה זמינה לשאלה הזאת — עונים ליד המכונה/,'a prompt without an ask: the console cannot answer, and says so'); assert.match(n1,/שיכפול תמיד עם מישהו ליד המחשבים/);
  assert.doesNotMatch(n1,/מדלג/,'orange never says "skips"');
  assert.match(notes,/<div class="note err"><span aria-hidden="true">●<\/span><span><b>מחשב 2<\/b> ממתין למפעיל: <span dir="ltr">Disk 3: failed the previous clone - skip\? \[y\/n\]<\/span>\. <b>אדום — בלי תשובה הסוכן מדלג<\/b> על מחשב 2 · דיסק 3\./);
  assert.match(n2,/clearDiskFailure\(7\)">נקה כשל קודם — מחשב 2 · דיסק 3</);
  assert.equal((notes.match(/<div class="note (?:warn|err)">/g)||[]).length,2,'a machine with a question gets one note; its drawers are not repeated in the aggregate notes');
  assert.match(mach(html,C1),/<span>מחשב 1<\/span><span class="pill ok">מחובר<\/span>[^]*?<b>38%<\/b>/,'connection pill and progress rows remain separate; the question is in the note');
  const d=setup({room:roomRunning(),role:'deploy',prompts}); const dh=d.run('deploy(0)');
  assert.doesNotMatch(dh,/monitorMachine|clearDiskFailure/,'deploy: no admin links'); assert.match(dh,/ממתין למפעיל/);
});

test('wave states: open → "waiting to join" pill and "start wave (N ready)"; verifying keeps its percent; stalled stream → orange pill and rate tile; closed wave / target done', () => {
  let s=setup({room:roomRunning({wave_state:'open',ready_drives:4,wave_number:1,written_drives:0,remaining_drives:12})});
  s.run("ROOM.machines.forEach(m=>{m.joined=false;m.state=null;(m.drawer_list||[]).forEach(d=>{d.state=null;d.bytes_written=0;d.bytes_total=0;d.crc_delta=null;})})");
  let html=s.run('deploy(0)'); balanced(html);
  assert.match(html,/obj-name">סבב הפצה<span class="pill info">ממתין להצטרפות · 4 מוכנים<\/span><\/div><div class="obj-sub"[^>]*>גל 1 · /);
  assert.match(html,/startWave\(\)">התחל גל \(4 מוכנים\)<\/button><button class="btn danger" onclick="stopRoom\(\)">עצירת הסבב…</);
  assert.match(html,/התקדמות כוללת<\/div><div class="v"><bdi dir="auto">—<\/bdi><\/div><div class="s"><b title="הגל טרם התחיל">לא נמדד<\/b>/,'no report → "—", not 0%');
  s=setup({room:roomRunning()}); s.run("ROOM.machines[0].drawer_list[0].state='verifying'");
  html=s.run('deploy(0)'); assert.match(mach(html,C1),/<div class="dsk" data-slot="1" title="[^"]*מאמת · SMART תקין"><span>דיסק 1<\/span><div class="bar "[^>]*><i style="--w:38%"><\/i><\/div><b>38%<\/b>/);
  s.run('ROOM.stream_stalled=true'); html=s.run('deploy(0)');
  assert.match(html,/<span class="pill warn">הזרם עצר</); assert.match(html,/<div class="kpi warn"><div class="l">קצב זרם<\/div>[^]*?<b>הזרם עצר<\/b>/);
  s=setup({room:roomRunning({wave_state:'closed',written_drives:12,remaining_drives:0})});
  s.run("ROOM.machines.forEach(m=>(m.drawer_list||[]).forEach(d=>{if(d.state==='writing'){d.state='done';d.bytes_written=100;}}))");
  html=s.run('deploy(0)'); assert.match(html,/<span class="pill ">הגל נסגר</);
  assert.match(mach(html,C1),/<div class="dsk" data-slot="1" title="[^"]*נכתב · SMART תקין"><span>דיסק 1<\/span><div class="bar ok"[^>]*><i style="--w:100%"><\/i><\/div><b>100%<\/b>/);
  assert.doesNotMatch(html,/startWave/);
});

test('target default (like the GUI "ready || …"): untouched = the ticked disks, else the ready drawers; typed value wins; the submit uses the same number', async () => {
  const {run}=setup();
  assert.match(run('roomNewWindow(roomMachines())'),/id="rn-target" min="1" value="5"/,'nothing ticked → the 5 ready drawers');
  run("roomPick('"+C1+"@1')"); assert.match(run('roomNewWindow(roomMachines())'),/id="rn-target" min="1" value="1"/,'one ticked → 1');
  run("roomPickAll()"); assert.match(run('roomNewWindow(roomMachines())'),/id="rn-target" min="1" value="4"/,'select-all chooses only green drawers');
  run("roomFormSet('target','9')"); run("roomPick('"+C1+"@1')"); assert.match(run('roomNewWindow(roomMachines())'),/id="rn-target" min="1" value="9"/,'a typed value is never overwritten');
  const s=setup(); s.run("roomFormSet('image','img_1'); roomPick('"+C1+"@1'); roomPick('"+C1+"@2')"); await s.run('openRoomRoundSubmit()');
  const r=s.requests.find(x=>x.url==='/api/console/room'&&x.options.method==='POST');
  assert.equal(JSON.parse(r.options.body).target_drives,2,'untouched target = what is ticked');
});

test('live hint (GUI parity): an open wave says how many are ready and how many more are needed; a running wave says elapsed, rate and ETA — only what was measured', () => {
  let s=setup({room:roomRunning({wave_state:'open',ready_drives:5,remaining_drives:7,wave_number:1})});
  let html=s.run('deploy(0)'); balanced(html);
  assert.match(html,/הגל ממתין: 5 מגירות מוכנות, צריך עוד 2 — או "התחל גל"\. החלפתם מגירות\? הדליקו את המכונות והן יצטרפו\./);
  s=setup({room:roomRunning({wave_state:'open',ready_drives:9,remaining_drives:7})}); assert.match(s.run('deploy(0)'),/הגל ממתין: 9 מגירות מוכנות, צריך עוד 0 — או/,'never negative');
  s=setup({room:roomRunning({elapsed_s:125,rate_bps:98000000,eta_s:3725})}); html=s.run('deploy(0)');
  assert.match(html,/<p>הגל משדר — עבר 2:05 · <bdi dir="ltr">93\.5 MB\/שנ&#39;<\/bdi> · נותרו ~1:02:05\. מכונה שסיימה — מכבים, מחליפים מגירות, מדליקים\.<\/p>/,'a quiet line in the room-card foot');
  s=setup({room:roomRunning({elapsed_s:40,rate_bps:null,eta_s:null})}); html=s.run('deploy(0)');
  assert.match(html,/הגל משדר — עבר 0:40 · סיום לא נמדד\./,'rate null ≠ 0: not printed, ETA "not measured"'); assert.doesNotMatch(html,/MB\/שנ/);
  s=setup({room:roomRunning()}); assert.match(s.run('deploy(0)'),/הגל משדר — זמן וקצב טרם נמדדו\./,'nothing measured → says so');
  assert.doesNotMatch(setup().run('deploy(0)'),/הגל ממתין|הגל משדר/,'no round, no hint');
});

test('#1331 mockup v2: a storage driver that was not made boot-start is red under its disk, reds the machine card, says "may not boot" on the card and in a red note; the wave that ended says so in the summary', () => {
  const room=roomRunning({wave_state:'closed',wave_number:2,written_drives:5,target_drives:5});
  room.machines[0].drawer_list[1].drivers={state:'staged',packages:['hp-800g6-win11'],boot:{state:'failed',service:'stornvme',error:"באימג' אין את השירות stornvme"}};
  room.machines[1].drawer_list[0].drivers={state:'staged',packages:['hp-800g6-win11','realtek-nic'],boot:{state:'enabled',service:'stornvme'}};
  const s=setup({room}); const html=s.run('deploy(0)'); balanced(html);
  const at=(mac)=>html.lastIndexOf('<div class="mach',html.indexOf(`data-mac="${mac}"`));
  const c1=html.slice(at(C1),at(C2)), c2=mach(html,C2);
  assert.match(c1,/^<div class="mach err"/,'the card of a machine with a disk that may not boot is red');
  assert.match(c1,/<span class="dline bad">דרייברים הונחו \(חבילה אחת\): hp-800g6-win11 · דרייבר האחסון stornvme לא הוגדר לעלייה — באימג&#39; אין את השירות stornvme<\/span>/);
  assert.match(c1,/<div class="mach-note err">דיסק 2 — הדיסק עלול לא לעלות \(0x7B\) — לבדוק לפני שמחזירים לכיתה<\/div>/);
  assert.match(c2,/<span class="dline muted">דרייברים הונחו \(2 חבילות\): hp-800g6-win11, realtek-nic · דרייבר האחסון stornvme הוגדר לעלייה<\/span>/);
  assert.doesNotMatch(c2,/עלול לא לעלות/,'enabled is positive evidence, not a warning');
  assert.match(html,/<div class="note err"[^]*<b>מחשב 1 · דיסק 2<\/b> — דרייבר האחסון stornvme לא הוגדר לעלייה — באימג&#39; אין את השירות stornvme\. <b>הדיסק עלול לא לעלות \(0x7B\) — לבדוק לפני שמחזירים לכיתה\.<\/b>/);
  assert.match(html,/גל 2 הסתיים — נכתבו 5 מתוך 5\. דרייברים: 2 מגירות הונחו\./);
  assert.match(c1,/<span class="pill err">דיסק אדום<\/span>/,'a disk that may not boot is a red disk on the card');
});

test('drivers outcome (#1331 mockup v2): a line under the bar of each drawer, one summary, and a note for each drawer that needs a hand — orange = not placed (the restore itself is done); none → nothing', () => {
  const s=setup({room:roomRunning()});
  assert.doesNotMatch(s.run('deploy(0)'),/class="dline|drv-legend|דרייברים: \d|חבילת דרייברים לפי דגם המגירה/,'no outcome → no line, no legend, no summary, nothing in the header');
  s.run("ROOM.machines[0].drawer_list[0].drivers={state:'staged',packages:['g6-all','g6-nic']}; ROOM.machines[0].drawer_list[1].drivers={state:'skipped',reason:'no_model'}; ROOM.machines[1].drawer_list[0].drivers={state:'failed',error:'חבילה חסרה'}");
  const html=s.run('deploy(0)'); balanced(html);
  assert.match(html,/<span class="dline muted">דרייברים הונחו \(2 חבילות\): g6-all, g6-nic<\/span>/);
  assert.match(html,/<span class="dline muted">דרייברים: לא נבחר דגם למגירה — לא הונחו<\/span>/);
  assert.match(html,/<span class="dline warn">דרייברים לא הונחו — חבילה חסרה<\/span>/);
  assert.match(html,/class="drv-legend">מתחת לכל דיסק — מה קרה לדרייברים שלו/);
  assert.match(html,/obj-sub"[^>]*>גל 2 · office365 · חבילת דרייברים לפי דגם המגירה · 3 מחשבים</,'the header says the round places drivers, after the image (mockup v2)');
  assert.match(html,/דרייברים: 1 מגירה הונחה, 1 לא הונחה, 1 בלי דגם\./);
  assert.match(html,/<div class="note warn"[^]*<b>מחשב 2 · דיסק 1<\/b> — דרייברים לא הונחו — חבילה חסרה\. המחשב יעלה בלי הדרייברים של הדגם; השחזור עצמו הושלם\./,'a failure is orange (done without its drivers), not red');
  assert.doesNotMatch(html,/0x7B|class="dline bad"/,'nothing here may fail to boot');
});

/* ‏#989 ס׳ 2 — תשובת SMART מהקונסולה. השאלה מגיעה מ-/room (machines[].ask), לא מ-prompt: רק מכונה
   שהמסך שלה חי מציעה אותה. התשובה הראשונה גוברת; השנייה מקבלת 409 והקונסולה מציגה אותו כפי שהוא. */
const askOpen=(extra={})=>({nonce:'3-2',port:2,verdict:'warn',reason:'crc 4',asked_at:minutesAgo(1),answer:null,answered_by:null,answered_at:null,...extra});

test('SMART question from the console: an open question shows the disk, the verdict and three buttons; a disk that failed last time is orange with "retry" (#1580)', () => {
  const room=roomRunning();
  room.machines[0].ask=askOpen(); room.machines[1].ask=askOpen({nonce:'5-3',port:3,verdict:'failed_last',reason:'cable'});
  const {run}=setup({room,prompts:{[C1]:'Disk 2: SMART warn (crc 4)',[C2]:'Disk 3: failed the previous clone'}});
  const html=run('deploy(0)'); balanced(html);
  const notes=between(html,'<div class="c12 rnotes">'), n1=between(notes,'<b>מחשב 1</b>','</div></div>'), n2=between(notes,'<b>מחשב 2</b>','</div></div>');
  assert.match(n1,/דיסק 2/); assert.match(n1,/SMART אזהרה/);
  assert.match(n1,new RegExp(`answerSmart\\('${C1}','3-2','replace','warn'\\)">החלף דיסק`)); assert.match(n1,new RegExp(`answerSmart\\('${C1}','3-2','rescue','warn'\\)">כתוב בכל זאת`)); assert.match(n1,new RegExp(`answerSmart\\('${C1}','3-2','skip','warn'\\)">דלג על הדיסק`));
  assert.match(n1,/בלי תשובה הסוכן ממשיך לכתוב/,'orange: no answer = keeps writing');
  assert.match(n2,/דיסק 3/); assert.match(n2,/נכשל בשיכפול הקודם/);
  assert.match(n2,new RegExp(`answerSmart\\('${C2}','5-3','rescue','failed_last'\\)">נסה שוב`),'orange: retry writes');
  assert.match(n2,/בלי תשובה הסוכן ממשיך לכתוב/);
  assert.doesNotMatch(notes,/דורש API<\/b>[^]{0,80}תשובה מהקונסולה|תשובה מהקונסולה — <b[^>]*>דורש API/,'the "needs API" placeholder is gone for a machine that can be answered');
  assert.equal((notes.match(/ממתין למפעיל:/g)||[]).length,0,'one note per machine: the question replaces the generic prompt note');
});

test('SMART question: answered → who answered and what, no buttons; a prompt with no ask (text menu / old agent) says to answer at the machine', () => {
  const room=roomRunning(); room.machines[0].ask=askOpen({answer:'skip',answered_by:'המכונה (ליד המסך)',answered_at:minutesAgo(0)});
  const {run}=setup({room,prompts:{[C1]:'Disk 2: SMART warn (crc 4)',[C2]:'Disk 3: failed the previous clone'}});
  const html=run('deploy(0)'), notes=between(html,'<div class="c12 rnotes">');
  const n1=between(notes,'<b>מחשב 1</b>','</div></div>');
  assert.match(n1,/נענתה ע&quot;י המכונה \(ליד המסך\): דלג/); assert.doesNotMatch(n1,/answerSmart/);
  assert.match(notes,/<b>מחשב 2<\/b> ממתין למפעיל[^]*?תשובה מהקונסולה אינה זמינה לשאלה הזאת — עונים ליד המכונה/,'no ask → the console cannot answer, and says so (not a silent missing button)');
  assert.doesNotMatch(notes,/דורש API/);
});

test('SMART answer posts {mac, nonce, answer}; replace powers the machine off and goes through a confirm sheet; a 409 shows the server text and re-reads', async () => {
  const room=roomRunning(); room.machines[0].ask=askOpen();
  const {run,requests,fixtures}=setup({room});
  await run(`answerSmart('${C1}','3-2','skip')`);
  let r=requests.filter(x=>x.url==='/api/console/room/answer').at(-1);
  assert.deepEqual(JSON.parse(r.options.body),{mac:C1,nonce:'3-2',answer:'skip'}); assert.match(run('toasted'),/התשובה נשלחה/);
  await run(`answerSmart('${C1}','3-2','rescue')`); assert.equal(JSON.parse(requests.filter(x=>x.url==='/api/console/room/answer').at(-1).options.body).answer,'rescue');
  const before=requests.filter(x=>x.url==='/api/console/room/answer').length;
  run(`formOptions=undefined; answerSmart('${C1}','3-2','replace')`);
  assert.equal(requests.filter(x=>x.url==='/api/console/room/answer').length,before,'replace: nothing posted before the sheet is confirmed');
  assert.equal(run('formOptions.danger'),true); assert.match(run('formOptions.note'),/יכבה/);
  await run('formOptions.onSubmit({})'); assert.equal(JSON.parse(requests.filter(x=>x.url==='/api/console/room/answer').at(-1).options.body).answer,'replace');
  fixtures['/room/answer']={__status:409,detail:'כבר נענה ע"י המכונה (ליד המסך)'};
  requests.length=0; await run(`answerSmart('${C1}','3-2','skip')`);
  assert.match(run('toasted'),/כבר נענה ע"י המכונה \(ליד המסך\)/); assert.ok(requests.some(x=>x.url==='/api/console/room'),'re-read after the refusal: the screen shows the winner');
  run('ME=null'); requests.length=0; await run(`answerSmart('${C1}','3-2','skip')`); assert.equal(requests.length,0,'not an operator → nothing sent');
});

test('start wave posts /room/start and a 409 is a toast with the reason; stop is behind the typed image name and posts /room/close with confirm_name; WoL posts /room/wake', async () => {
  const {run,requests,fixtures}=setup({room:roomRunning({wave_state:'open'})});
  await run('startWave()'); assert.ok(requests.some(r=>r.url==='/api/console/room/start'&&r.options.method==='POST')); assert.match(run('toasted'),/הגל התחיל/);
  fixtures['/room/start']={__status:409,detail:'אף מכונת שיכפול עוד לא הצטרפה לגל'};
  await run('startWave()'); assert.match(run('toasted'),/הגל לא התחיל: אף מכונת שיכפול עוד לא הצטרפה לגל/);
  run('stopRoom()');
  assert.equal(run('formOptions.danger'),true); assert.equal(run('formOptions.verify'),undefined,'#1270: no typed name'); assert.equal(run('formOptions.submitLabel'),'עצירת הסבב');
  assert.ok(!requests.some(r=>r.url==='/api/console/room/close'),'nothing posted before the sheet is confirmed');
  await run('formOptions.onSubmit({})');
  const close=requests.find(r=>r.url==='/api/console/room/close'); assert.deepEqual(JSON.parse(close.options.body),{confirm_name:'office365'});
  await run('wakeRoom()'); assert.ok(requests.some(r=>r.url==='/api/console/room/wake')); assert.match(run('toasted'),/WoL נשלח ל-3 מחשבים/);
  run("ROOM.round.source={kind:'build_disk',mac:'"+B1+"',disk:'sda',name:'בנייה 1',task_state:'running',manifest_ready:true}; ROOM.round.image_name='בנייה 1:sda'");
  assert.match(run('deploy(0)'),/obj-sub"[^>]*>גל 2 · מקור: מחשב הבנייה \(בנייה 1:sda\) · /); run('stopRoom()'); assert.equal(run('formOptions.verify'),undefined); await run('formOptions.onSubmit({})'); assert.deepEqual(JSON.parse(requests.filter(r=>r.url==='/api/console/room/close').at(-1).options.body),{confirm_name:'בנייה 1:sda'},'#715: the name sent is what GET returns');
  run('ME.role="deploy"'); run('formOptions=undefined; stopRoom()'); assert.equal(run('formOptions.danger'),true,'deploy is a room operator');
  run('ME=null'); run('formOptions=undefined; stopRoom()'); assert.equal(run('typeof formOptions'),'undefined');
});

test('the big view is gone (not in the mockup); ROOM unread ≠ no round; empty room', () => {
  const {run}=setup({room:roomRunning()});
  assert.equal(run('typeof toggleRoomBig'),'undefined'); assert.equal(run('"big" in DEPLOY'),false);
  run("ROOM=null; DEPLOY.roomErr='שגיאה 500'"); let html=run('deploy(0)'); balanced(html);
  assert.match(html,/<span class="pill ">החדר לא נקרא</); assert.match(html,/החדר לא נקרא: שגיאה 500 — הגריד והסבב לא ידועים/); assert.doesNotMatch(html,/rgrid|rnew|אין סבב פעיל|openRoomRound/);
  run("ROOM={round:null,stream_stalled:false,disk_floor:null,machines:[]}"); html=run('deploy(0)'); balanced(html);
  assert.match(html,/אין מחשבי שיכפול רשומים — הוסיפו אותם במחשבים/); assert.match(html,/openClass\('grp_CLONERS'\)">למחשבי השיכפול</); assert.doesNotMatch(html,/<b class="et">אין סבב פעיל/);
});

test('class tab: the existing session code as-is inside cards (row, start/stop, members with SMART/error, absent roster, next round, pulls); history tab = API note', () => {
  const session={id:'s1',image_id:'img_1',image_name:'Office 2024',group_id:'grp_LAB303',group_label:'כיתה 303',prefix:'LAB',expected_clients:2,joined:1,state:'open',starts_in_seconds:252,kind:'multicast',
    members:[{mac:'b4:2e:99:07:1a:c1',hostname:'LAB-01',state:'waiting',bytes_written:0,bytes_total:0,error:'disk error',disks:[{disk_number:1,verdict:'fail',decision:'replace'}]}],roster:['b4:2e:99:07:1a:c1','b4:2e:99:07:1a:c2'],stuck:{}};
  const {run,fixtures}=setup({session});
  run("SESSION_MACHINES={group:'grp_LAB303',list:[{mac:'b4:2e:99:07:1a:c1',suffix:'01'},{mac:'b4:2e:99:07:1a:c2',suffix:'02'},{mac:'b4:2e:99:07:1a:c3',suffix:'03'}]}");
  let html=run('deploy(1)'); balanced(html);
  assert.match(html,/obj-name">סבב כיתה</); assert.match(html,/v2/);
  assert.match(html,/<td><strong>כיתה 303<\/strong><\/td><td>Office 2024<\/td><td>כיתה 303<\/td><td>1 \/ 2<\/td>/); assert.match(html,/פתוח · מתחיל בעוד 252 ש'/);
  assert.match(html,/startRound\(\)">התחל עכשיו</); assert.match(html,/stopRound\(\)">עצור סבב</);
  assert.match(html,/<b>LAB-01<\/b>/); assert.match(html,/disk error/); assert.match(html,/disk-smart fail/); assert.match(html,/<b>LAB-02<\/b><div class="sub">טרם הצטרפה/); assert.match(html,/הסבב הבא: 03/);
  assert.doesNotMatch(html,/mgrid|kpi|rnew/,'no room content on the class tab');
  run('ME.role="deploy"'); assert.match(run('deploy(1)'),/startRound\(\)/,'deploy operates class rounds too'); run('ME.role="admin"');
  run('OVERVIEW.session=null; OVERVIEW.pulls=[{image_name:"kali",kind:"unicast",members:[{mac:"b4:2e:99:07:1a:c3",hostname:"LAB-03",state:"running",bytes_written:30,bytes_total:100}]}]');
  html=run('deploy(1)'); assert.match(html,/אין סבב כיתה פתוח/); assert.match(html,/משיכות יוניקאסט \(1\)/); assert.match(html,/<b>LAB-03<\/b><span class="tag">יוניקאסט<\/span>/);
  run('OVERVIEW.session={id:"w1",image_id:"img_1",image_name:"office365",group_id:"grp_CLONERS",prefix:"ROOM",expected_clients:3,joined:2,state:"running",members:[{mac:"'+C1+'",state:"running"}],roster:null,stuck:{}}; OVERVIEW.room={id:"rnd_1"}');
  html=run('deploy(1)'); assert.match(html,/אין סבב כיתה — הסבב הפעיל הוא גל בחדר המשכפלים/); assert.doesNotMatch(html,/stopRound|<table class="table"/,'the room wave is not a class round — no class stop button on it');
  run('OVERVIEW=null'); assert.match(run('deploy(1)'),/\/overview לא נקרא/);
  html=run('deploy(2)'); balanced(html); assert.match(html,/obj-name">היסטוריית סבבים</); assert.match(html,/דורש API/); assert.match(html,/selectPageById\('logs'\)">ביומן</); assert.doesNotMatch(html,/class="ev"|mgrid/);
  run('ME.role="deploy"'); assert.match(run('deploy(2)'),/ביומן \(מנהל\)/); run('ME.role="admin"');
  // ‏#980 §3: החדר מ-/room/history; הכיתות (v2) נשארות "דורש API".
  assert.match(html,/היסטוריית הסבבים לא נקראה/,'unread history is said');
  run('ROOM_HISTORY='+JSON.stringify(fixtures['/room/history'])); html=run('deploy(2)'); balanced(html);
  assert.match(html,/<th>#<\/th><th>אימג׳<\/th><th>מחשבים<\/th><th>נכתבו<\/th><th>משך<\/th><th[^>]*>מצב<\/th><th[^>]*>תאריך<\/th>/);
  assert.match(html,/win11-base<\/td><td>—<\/td><td>6\/6<\/td><td>90 דק(?:'|&#39;)<\/td><td[^>]*><span class="pill ok">הצליח<\/span>/,'history follows the mockup columns and keeps the result pill');
  assert.match(html,/סבבים קודמים בכיתות — <b[^>]*>דורש API<\/b>/);
});

test('#1081 v1: classrooms flag off drops the class tab; history stays', () => {
  const {run}=setup();
  run('ME.capabilities.classrooms=false; ROOM_HISTORY=[]');
  assert.deepEqual(JSON.parse(run('JSON.stringify(deployTabs())')),['חדר המשכפלים','היסטוריה']);
  const room=run('deploy(0)'); balanced(room);
  assert.match(room,/obj-name">סבב הפצה</);
  assert.doesNotMatch(room,/>כיתה</);
  const hist=run('deploy(1)');
  assert.match(hist,/obj-name">היסטוריית סבבים</);
  assert.doesNotMatch(hist,/ובכיתות|בכיתות|דורש API/); assert.match(hist,/אין סבבים קודמים בחדר/);
});

test('data: loadDeploy reads /overview, /room, /images, /machines, /groups, /disk-failures, /net and the class roster; the 2s poll re-reads /room + /machines on the deploy page and re-renders only on change (prompt included)', async () => {
  const session={id:'s1',image_id:'img_1',image_name:'Office 2024',group_id:'grp_LAB303',expected_clients:2,joined:0,state:'open',members:[],roster:[],stuck:{}};
  const {run,requests,fixtures}=setup({session});
  run('ROOM=null; IMAGES=null; MACHINES=null; renderActivity=()=>{}; markStatusFresh=()=>{}; rendered=0');
  await run('loadDeploy()');
  for(const u of ['/overview','/room','/room/history','/images','/machines','/groups','/disk-failures','/net','/machines?group=grp_LAB303']) assert.ok(requests.some(r=>r.url==='/api/console'+u),u);
  assert.equal(run('ROOM.machines.length'),3); assert.equal(run('ROOM_HISTORY.length'),1); assert.equal(run('IMAGES.length'),3); assert.equal(run('SESSION_MACHINES.group'),'grp_LAB303'); assert.ok(run('rendered')>=1);
  run("current='deploy'; rendered=0"); requests.length=0;
  await run('refreshGroupLive()');
  assert.equal(requests.filter(r=>r.url==='/api/console/room').length,1); assert.equal(requests.filter(r=>r.url==='/api/console/machines').length,1); assert.equal(run('rendered'),0,'same answer → no re-render');
  fixtures['/machines']=fixtures['/machines'].map(m=>m.mac===C2?{...m,prompt:'Disk 2: SMART warn - write anyway?'}:m);
  await run('refreshGroupLive()'); assert.equal(run('rendered'),1,'a new operator question re-renders'); assert.equal(run('MACHINES.find(m=>m.mac==="'+C2+'").prompt'),'Disk 2: SMART warn - write anyway?');
  fixtures['/room']=roomRunning(); await run('refreshGroupLive()'); assert.equal(run('rendered'),2); assert.equal(run('ROOM.round.id'),'rnd_1');
  run("current='images'"); requests.length=0; await run('refreshGroupLive()'); assert.equal(requests.length,0,'not on the deploy page (and no cloners object open) → no poll');
  run("ROOM=null; DEPLOY.roomErr=''"); fixtures['/room']={__status:500,detail:'boom'}; await run('loadDeploy()');
  assert.equal(run('ROOM'),null); assert.equal(run('DEPLOY.roomErr'),'boom','a failed read is "unread", not "no round"');
});

/* ‏#1269 שלב 2: "להוסיף דרייברים" בפתיחת סבב — מתג כבוי כברירת מחדל, ודגם לכל דיסק יעד.
   אין "אוטומטי": החומרה שזוהתה היא של המשכפל, לא של המכונה שתקבל את הדיסק — דגם מפורש או "בלי". */
const newCard=(html)=>html;

test('#1269 drivers: the switch is off by default, disabled with a reason when the list is unread (null) or empty ([]) — two reasons, never "automatic"', () => {
  const {run}=setup();
  run('DRIVER_MODELS='+JSON.stringify(MODELS));
  const html=run('roomNewWindow(roomMachines())'); balanced(html);
  let form=newCard(html);
  assert.match(form,/<input type="checkbox" id="rn-drv" onchange="roomFormSet\('drv',this\.checked\)"> להוסיף דרייברים<\/label>/,'off, enabled');
  assert.match(form,/כבוי — לא יונחו דרייברים/);
  assert.doesNotMatch(form,/rn-drv-all|data-drv=/,'no model choice while off');
  run('DRIVER_MODELS=null'); form=newCard(run('roomNewWindow(roomMachines())'));
  assert.match(form,/id="rn-drv" disabled/); assert.match(form,/רשימת הדגמים לא נקראה מהשרת/);
  run('DRIVER_MODELS=[]'); form=newCard(run('roomNewWindow(roomMachines())'));
  assert.match(form,/id="rn-drv" disabled/); assert.match(form,/אין בספרייה חבילת דרייברים עם דגם מחשב/);
  run('DRIVER_MODELS=[]; DEPLOY.form.drv=true'); assert.doesNotMatch(newCard(run('roomNewWindow(roomMachines())')),/data-drv=/,'on but no models = off');
});

test('#1269 drivers on: a row per connected disk with "בלי" + the library models only; "a model for every disk" fills every row; the summary groups by machine', () => {
  const {run}=setup();
  run('DRIVER_MODELS='+JSON.stringify(MODELS)+"; roomFormSet('drv',true)");
  assert.match(newCard(run('roomNewWindow(roomMachines())')),/סמנו דיסקים בגריד כדי לבחור להם דגם/,'no selection → no rows'); run('roomPickAll()');
  const html=run('roomNewWindow(roomMachines())'); balanced(html);
  let form=newCard(html);
  assert.match(form,/id="rn-drv" checked/); assert.match(form,/החבילות שבשרת לדגם שנבחר יונחו על הדיסק אחרי השחזור/);
  const rows=form.match(/<select data-drv="[^"]+"/g)||[];
  assert.deepEqual(rows.map(r=>r.match(/data-drv="([^"]+)"/)[1]),[`${C1}@1`,`${C1}@2`,`${C2}@1`,`${C2}@3`],'select-all chooses green drawers only');
  const one=between(form,`data-drv="${C1}@1"`,'</select>');
  assert.deepEqual([...one.matchAll(/<option[^>]*>([^<]*)<\/option>/g)].map(m=>m[1]),['בלי','HP HP EliteDesk 800 G6','LENOVO ThinkCentre M70q Gen 3']);
  assert.doesNotMatch(form,/אוטומטי|אוטו|לפי החומרה/);
  assert.match(form,/דרייברים: אף דיסק לא קיבל דגם/,'default per disk = "בלי"');
  run("roomDrvAll('1')"); form=newCard(run('roomNewWindow(roomMachines())'));
  assert.equal((form.match(/<option value="1" selected>LENOVO ThinkCentre M70q Gen 3<\/option>/g)||[]).length,4);
  assert.match(form,/דרייברים: ThinkCentre M70q Gen 3 ← 2 דיסקים ב-מחשב 1 · ThinkCentre M70q Gen 3 ← 2 דיסקים ב-מחשב 2/);
  run(`roomDrvSet('${C2}@3','')`); run(`roomDrvSet('${C1}@2','0')`); form=newCard(run('roomNewWindow(roomMachines())'));
  assert.match(form,/דרייברים: ThinkCentre M70q Gen 3 ← דיסק אחד ב-מחשב 1 · HP EliteDesk 800 G6 ← דיסק אחד ב-מחשב 1 · ThinkCentre M70q Gen 3 ← דיסק אחד ב-מחשב 2 · בלי ← מחשב 2/);
  run(`roomPick('${C2}@3')`); form=newCard(run('roomNewWindow(roomMachines())'));   // ‏#1275: הדרייברים הולכים אחרי אותה בחירה
  assert.deepEqual((form.match(/<select data-drv="[^"]+"/g)||[]).map(r=>r.match(/data-drv="([^"]+)"/)[1]),[`${C1}@1`,`${C1}@2`,`${C2}@1`],'a cleared disk drops out of the drivers rows');
});

test('#1269 drivers: POST /room adds target_slots + driver_models (library) and driver_models (direct); no model on any disk refuses locally; switch off = the old body', async () => {
  const {run,requests}=setup();
  run('DRIVER_MODELS='+JSON.stringify(MODELS)+"; roomFormSet('image','img_1'); roomFormSet('target','6'); roomFormSet('drv',true); roomPickAll()");
  await run('openRoomRoundSubmit()');
  assert.equal(requests.filter(x=>x.url==='/api/console/room'&&x.options.method==='POST').length,0,'nothing posted');
  assert.equal(run('DEPLOY.err'),'לא נבחר דגם לאף דיסק — בחרו דגם, או כבו את "להוסיף דרייברים"');
  run(`roomDrvSet('${C1}@1','1'); roomDrvSet('${C2}@3','0')`);
  await run('openRoomRoundSubmit()');
  let r=requests.find(x=>x.url==='/api/console/room'&&x.options.method==='POST');
  assert.deepEqual(JSON.parse(r.options.body),{image_id:'img_1',target_drives:6,
    target_slots:[{mac:C1,ports:[1,2]},{mac:C2,ports:[1,3]}],
    driver_models:[{mac:C1,port:1,vendor:'LENOVO',model:'ThinkCentre M70q Gen 3'},{mac:C2,port:3,vendor:'HP',model:'HP EliteDesk 800 G6'}]});
  assert.equal(run('DEPLOY.form.drv'),false,'after opening the switch is off again');
  requests.length=0; run("roomFormSet('src','build_disk'); roomFormSet('disk','sda'); roomFormSet('drv',true); roomPickAll()"); run(`roomPick('${C2}@2'); roomDrvSet('${C2}@2','0')`);
  await run('openRoomRoundSubmit()');
  r=requests.find(x=>x.url==='/api/console/room'&&x.options.method==='POST');
  assert.deepEqual(JSON.parse(r.options.body),{source:{kind:'build_disk',mac:B1,disk:'sda'},target_slots:[{mac:C1,ports:[1,2]},{mac:C2,ports:[1,2,3]}],
    driver_models:[{mac:C2,port:2,vendor:'HP',model:'HP EliteDesk 800 G6'}]});
  const s2=setup(); s2.run('DRIVER_MODELS='+JSON.stringify(MODELS)+"; roomFormSet('image','img_1'); roomFormSet('target','6'); roomPick('"+C1+"@2')");
  await s2.run('openRoomRoundSubmit()');
  assert.deepEqual(JSON.parse(s2.requests.find(x=>x.url==='/api/console/room'&&x.options.method==='POST').options.body),{image_id:'img_1',target_drives:6,target_slots:[{mac:C1,ports:[2]}]},'switch off: no driver_models');
});

test('#1269 drivers: loadDeploy reads /room/driver-models; a failed read is "unread" (null), not "no models"; the round shows each drawer\'s outcome in its title', async () => {
  const {run,requests,fixtures}=setup();
  run('DRIVER_MODELS=null; renderActivity=()=>{}; markStatusFresh=()=>{}');
  await run('loadDeploy()');
  assert.ok(requests.some(r=>r.url==='/api/console/room/driver-models'));
  assert.equal(run('DRIVER_MODELS.length'),2);
  fixtures['/room/driver-models']={__status:500,detail:'boom'}; await run('loadDeploy()');
  assert.equal(run('DRIVER_MODELS'),null);
  const room=roomRunning();
  room.machines[0].drawer_list[0].drivers={state:'staged',packages:['m70q-chipset','m70q-nic']};
  room.machines[0].drawer_list[1].drivers={state:'failed',packages:[],error:'the server has no driver package for the chosen model'};
  room.machines[1].drawer_list[0].drivers={state:'skipped',packages:[],reason:'not_windows'};
  const s=setup({room}); const html=s.run('deploy(0)'); const c1=card(html,C1), c2=card(html,C2);
  assert.match(c1,/data-slot="1" title="[^"]*דרייברים הונחו \(2 חבילות\): m70q-chipset, m70q-nic/);
  assert.match(c1,/data-slot="2" title="[^"]*דרייברים לא הונחו — the server has no driver package for the chosen model/);
  assert.match(c2,/data-slot="1" title="[^"]*דרייברים דולגו — האימג&#39; אינו Windows/);
  assert.doesNotMatch(c2,/data-slot="2" title="[^"]*דרייברים/,'drivers null = the step did not run — nothing said');
});

/* ‏#1275: גריד בחירת הדיסקים — checkbox אמיתי; אדום/כבוי/ריק חסומים. */
const pk=(html)=>between(html,'<div class="full rn-pick">','<details class="full rn-drv"');
const pcard=(html,mac)=>(html.split('<section class="rn-m').find(c=>c.includes(`data-mac="${mac}"`))||'').split('<section class="rn-m')[0];

test('#1275 pick grid: one card per cloner and one checkbox label per present disk; nothing selected by default', () => {
  const {run}=setup(); const html=run('roomNewWindow(roomMachines())'); balanced(html);
  const g=pk(html);
  assert.equal((g.match(/<section class="rn-m/g)||[]).length,3,'a card per cloner, the off one included');
  assert.equal((g.match(/<label class="dk st-/g)||[]).length,5,'one label tile per present disk');
  assert.equal((g.match(/type="checkbox"/g)||[]).length,5,'every present drawer uses a real checkbox');
  assert.doesNotMatch(g,/class="dk[^"]* sel/,'no default selection');
  assert.match(g,/דיסק 1<\/span><span class="dk-sz"><bdi[^>]*>256GB<\/bdi>/,'rows use the existing marketed disk size helper');
  assert.match(g,/<span class="n-chip">נכנס<\/span>/,'green: the mockup pill (n-chip — the window is not .page)');
  assert.match(g,/<span class="n-chip w">אזהרה<\/span>/,'warning: orange chip');
  assert.match(g,/5 מגירות ניתנות לבחירה/); assert.match(g,/בחר הכול/); assert.match(g,/נקה הכול/);
  assert.deepEqual(JSON.parse(run('JSON.stringify(roomSubmitState(roomMachines()))')),{n:0,text:'יש לסמן לפחות דיסק אחד'},'the footer summary; the action is disabled until a disk is ticked');
  assert.match(pcard(html,C3),/כבוי · יידלק ב-WoL/);
});

test('#1275 pick grid: selection keeps warning colour, adds blue selection state, and machine/room select-all choose green only', () => {
  const {run}=setup();
  run(`roomFormSet('target','5'); roomPick('${C2}@2')`); let html=run('roomNewWindow(roomMachines())'); balanced(html);
  const c2=pcard(html,C2);
  assert.match(c2,new RegExp(`class="dk st-w sel" data-key="${C2}@2"><input class="dk-in" type="checkbox" checked`),'selected warning keeps orange class and gains selection');
  assert.match(c2,/<b>1<\/b> מתוך 3 נבחרו/); assert.match(c2,/class="dk st-g" data-key=/);
  assert.equal(run('roomSubmitState(roomMachines()).text'),'נבחרו דיסק 1 ב-מחשב 1 · משוער 5 גלים'.replace('דיסק 1','1 דיסק').replace('מחשב 1','1 מחשב'));
  run('openRoomRound()'); assert.equal(run("document.getElementById('modalOk').disabled"),false,'a ticked disk enables the footer action');
  run(`roomPick('${C2}@2')`); assert.doesNotMatch(pcard(run('roomNewWindow(roomMachines())'),C2),/st-w sel/,'second click clears');
  run(`roomPickMachine('${C2}')`); html=run('roomNewWindow(roomMachines())'); assert.match(pcard(html,C2),/<b>2<\/b> מתוך 3 נבחרו/,'machine select-all skips orange');
  run(`roomPickClearMachine('${C2}')`); assert.match(pcard(run('roomNewWindow(roomMachines())'),C2),/<b>0<\/b> מתוך 3 נבחרו/);
  run('roomPickAll()'); assert.match(run('roomSubmitState(roomMachines()).text'),/נבחרו 4 דיסקים ב-2 מחשבים/,'room select-all selects green only');
  run('roomPickClearAll()'); assert.equal(run('roomSubmitState(roomMachines()).n'),0);
});

test('#1275 safety: off, empty, unidentified, and red drawers cannot be ticked; red checkbox is disabled', () => {
  const room=roomIdle();
  room.machines[0].drawer_list=[drawer('sda',1,'S5Y10',{smart:'fail',smart_reason:'pending_sectors'}),drawer('sdb',2,null,{smart:'ok',size_bytes:null})];   // אדום (#1580: SMART fail); בלי serial וגודל
  // #1580 (נדב 17/10): נכשל בשיכפול הקודם = כתום — הסוכן שואל "נסה שוב", ובלי תשובה כותב.
  room.machines[1].drawer_list=[drawer('sda',1,'S5Y30',{smart:'failed_last',smart_reason:'cable'})];                     // חריצים 2,3 ריקים
  const {run}=setup({room});
  run(`roomPick('${C3}@1'); roomPick('${C2}@2'); roomPick('${C2}@3'); roomPick('${C1}@2')`);   // כולם נדחים
  const html=run('roomNewWindow(roomMachines())'); balanced(html); assert.equal(run('roomSubmitState(roomMachines()).n'),0,'nothing selectable was selected');
  const c1=pcard(html,C1);
  assert.match(c1,new RegExp(`class="dk st-d" data-key="${C1}@1"><input class="dk-in" type="checkbox" disabled`),'red is a disabled checkbox');
  assert.match(c1,/<span class="n-chip d">לא ניתן לסימון<\/span>/,'red: the red chip');
  assert.match(c1,new RegExp(`data-key="${C1}@2"><input class="dk-in" type="checkbox" disabled`),'no serial → disabled'); assert.match(c1,/class="dk-sz"><bdi[^>]*>—<\/bdi>/);
  assert.equal((pcard(html,C2).match(/class="dk st-e"/g)||[]).length,2,'two dashed empty slots');
  assert.match(pcard(html,C2),/class="dk st-w"[^>]*>[\s\S]*נכשל בשיכפול הקודם — &quot;נסה שוב&quot; כותב/,'#1580: failed last time is orange, retry');
  run(`roomPick('${C1}@1')`); assert.equal(run('roomSubmitState(roomMachines()).n'),0,'red cannot enter selection state');
});

test('#1366 start-round colours: CRC/F1/link are orange; slow write preflight is red and cannot be ticked', () => {
  const room=roomIdle();
  room.machines[0].drawer_list=[
    drawer('sda',1,'S5Y10',{smart:'ok',crc_suspect:true,crc:17,link_state:'slow',link_gbps:1.5,link_max_gbps:6}),
    drawer('sdb',2,'S5Y11',{smart:'ok',write_preflight:{state:'slow',reason:'איטי — 23 MB/s'}}),
  ];
  const {run}=setup({room}); const html=run('roomNewWindow(roomMachines())'); const c1=pcard(html,C1);
  assert.match(c1,/class="dk st-w"[^>]*>[\s\S]*כבל או מגירה חשודים — 17 שגיאות · קישור 1\.5 מתוך 6\.0 Gb\/s · לבדוק כבל או מגירה/);
  assert.match(c1,new RegExp(`class="dk st-d" data-key="${C1}@2"><input class="dk-in" type="checkbox" disabled[\\s\\S]*איטי — 23 MB/s`));
  run(`roomPick('${C1}@2')`); assert.doesNotMatch(run('roomNewWindow(roomMachines())'),new RegExp(`data-key="${C1}@2"[^>]*>[\\s\\S]*type="checkbox" checked`));
});

test('#1275 pick grid: the round carries the selection as target_slots (library and direct); nothing selected is refused locally with the reason and posts nothing', async () => {
  const {run,requests}=setup();
  run("roomFormSet('image','img_1'); roomFormSet('target','6')");
  await run('openRoomRoundSubmit()');
  assert.equal(requests.filter(x=>x.url==='/api/console/room'&&x.options.method==='POST').length,0); assert.equal(run('DEPLOY.err'),'יש לסמן לפחות דיסק אחד');
  run(`roomPick('${C2}@3'); roomPick('${C1}@1'); roomPick('${C2}@1')`);
  assert.equal(run('DEPLOY.err'),'','picking clears the message');
  await run('openRoomRoundSubmit()');
  let r=requests.find(x=>x.url==='/api/console/room'&&x.options.method==='POST');
  assert.deepEqual(JSON.parse(r.options.body),{image_id:'img_1',target_drives:6,target_slots:[{mac:C1,ports:[1]},{mac:C2,ports:[1,3]}]},'grouped by machine, the GUI bridge format');
  assert.deepEqual(JSON.parse(run('JSON.stringify(DEPLOY.form.sel)')),{},'cleared after opening');
  requests.length=0; run(`roomFormSet('src','build_disk'); roomFormSet('disk','sda'); roomPick('${C2}@2')`);
  await run('openRoomRoundSubmit()');
  r=requests.find(x=>x.url==='/api/console/room'&&x.options.method==='POST');
  assert.deepEqual(JSON.parse(r.options.body),{source:{kind:'build_disk',mac:B1,disk:'sda'},target_slots:[{mac:C2,ports:[2]}]});
  run(`roomFormSet('src','library'); roomFormSet('image','img_1'); roomFormSet('target','1'); roomPickAll()`); requests.length=0;
  await run('openRoomRoundSubmit()'); assert.equal(requests.filter(x=>x.options&&x.options.method==='POST').length,0); assert.match(run('DEPLOY.err'),/היעד \(1\) קטן ממספר הדיסקים שסומנו \(4\)/);
  run("roomFormSet('target','')"); assert.match(newCard(run('roomNewWindow(roomMachines())')),/id="rn-target" min="1" value="4"/,'untyped target defaults to the four green ticked drawers');
});

test('#989 item 3: wave progress line (wave · written of target · left) with a K-waves estimate = ceil(left / ready), or "waiting for new disks" when none are ready', () => {
  let s=setup({room:roomRunning({wave_number:2,written_drives:15,target_drives:30,remaining_drives:15,ready_drives:6})});
  let html=s.run('deploy(0)'); balanced(html);
  assert.match(html,/גל 2 · נכתבו 15 מתוך 30 · נשארו 15 · משוער: עוד 3 גלים/,'ceil(15/6)=3');
  assert.doesNotMatch(html,/הגל הסתיים/,'a running wave has not ended');
  s=setup({room:roomRunning({wave_state:'open',wave_number:2,written_drives:15,target_drives:30,remaining_drives:15,ready_drives:0})}); html=s.run('deploy(0)');
  assert.match(html,/נשארו 15 · ממתין לדיסקים חדשים/); assert.doesNotMatch(html,/משוער: עוד/,'no ready drawers → no estimate, never "0 waves" or Infinity');
  s=setup({room:roomRunning({wave_state:'closed',written_drives:12,remaining_drives:0})}); assert.doesNotMatch(s.run('deploy(0)'),/משוער|ממתין לדיסקים חדשים|נכתבו 12 מתוך/,'nothing left → no progress line');
});

test('#989 item 3: end of a wave with work left says so and offers the existing WoL button (operators only); wave 1 still open has not ended', () => {
  let s=setup({room:roomRunning({wave_state:'open',wave_number:2,written_drives:5,remaining_drives:7,ready_drives:0})});
  let html=s.run('deploy(0)'); balanced(html);
  assert.match(html,/הגל הסתיים — החליפו דיסקים והעירו את מחשבי השיכפול כדי להמשיך <button class="btn" onclick="wakeRoom\(\)">הער את מחשבי השיכפול<\/button>/);
  s=setup({room:roomRunning({wave_state:'open',wave_number:1,written_drives:0,remaining_drives:12,ready_drives:4})}); assert.doesNotMatch(s.run('deploy(0)'),/הגל הסתיים/);
  s=setup({room:roomRunning({wave_state:'open',wave_number:2,written_drives:5,remaining_drives:7,ready_drives:0})});
  s.run('ME.role="viewer"'); html=s.run('deploy(0)'); assert.match(html,/הגל הסתיים/); assert.doesNotMatch(html,/הער את מחשבי השיכפול/,'no button without the right to wake');
});

/* ‏#432: קישור ה-SATA לפני "התחל" — כתום שואל, לא חוסם; ‏#65: מי חסר בגל, וכבה אותו / התחל בכל זאת / בטל. */
const WARN_TEXT='מחשב 2 · דיסק 2: קישור 1.5 מתוך 6.0 Gb/s — לבדוק כבל או מגירה';
function roomOpenWave(extra={}) {
  const r=roomRunning({wave_state:'open',wave_number:1,written_drives:0,remaining_drives:6,ready_drives:4,...extra});
  r.machines[0]={...r.machines[0],drawer_list:[drawer('sda',1,'S5Y10',{smart:'ok',link_gbps:6,link_max_gbps:6,link_state:'ok'}),drawer('sdb',2,'S5Y11',{smart:'ok',link_gbps:null,link_max_gbps:null,link_state:'unknown'})]};
  r.machines[1]={...r.machines[1],drawer_list:[drawer('sda',1,'S5Y30',{smart:'ok',link_gbps:6,link_max_gbps:6,link_state:'ok'}),drawer('sdb',2,'S5Y31',{smart:'ok',link_gbps:1.5,link_max_gbps:6,link_state:'slow',crc_rise:12})]};
  return r;
}

test('#432: a slow drawer is orange with the link and CRC rise in its tooltip; an unread link says "unknown" and is never called fine', () => {
  const {run}=setup({room:roomOpenWave()});
  const html=run('deploy(0)'); balanced(html);
  assert.match(mach(html,C2),/<div class="dsk bay-emphasis warn" data-slot="2" title="[^"]*קישור 1\.5 מתוך 6\.0 Gb\/s · לבדוק כבל או מגירה · CRC \+12 מאז הסבב הקודם · SMART תקין">/);
  assert.match(mach(html,C1),/<div class="dsk" data-slot="1" title="[^"]*קישור 6\.0 מתוך 6\.0 Gb\/s · SMART תקין">/);
  assert.match(mach(html,C1),/<div class="dsk" data-slot="2" title="[^"]*קישור SATA לא ידוע · SMART תקין">/,'unknown is said, not hidden');
  assert.doesNotMatch(mach(html,C1),/לא ידוע · לבדוק כבל/);
  assert.equal(run("linkSlow({link_gbps:3,link_max_gbps:6})"),true,'/machines carries only the numbers');
  assert.equal(run("linkSlow({link_gbps:3,link_max_gbps:null})"),false,'an unknown max is not "slow" either');
});

test('#432: an open wave with a precheck warning shows it in orange; "start" asks first, and only "start anyway" posts', async () => {
  const s=setup({room:roomOpenWave({precheck:[{mac:C2,machine:'מחשב 2',port:2,serial:'S5Y31',slot_name:null,link_gbps:1.5,link_max_gbps:6,crc_rise:null,text_he:WARN_TEXT}]})});
  const html=s.run('deploy(0)'); balanced(html);
  assert.match(html,/<div class="note warn">[^]*?לפני ההתחלה — מגירה אחת בגל עם קישור חשוד[^]*?מחשב 2 · דיסק 2: קישור 1\.5 מתוך 6\.0 Gb\/s[^]*?אזהרה, לא חסימה/);
  s.requests.length=0; await s.run('startWave()');
  assert.equal(s.requests.filter(x=>x.options.method==='POST').length,0,'no POST before the operator chose');
  assert.equal(s.run('formOptions.submitLabel'),'התחל בכל זאת'); assert.match(s.run('formOptions.note'),/קישור 1\.5 מתוך 6\.0/);
  await s.run('formOptions.onSubmit({})');
  assert.ok(s.requests.some(x=>x.url==='/api/console/room/start'&&x.options.method==='POST'));
  const clean=setup({room:roomOpenWave({precheck:[]})});
  assert.doesNotMatch(clean.run('deploy(0)'),/לפני ההתחלה/);
  await clean.run('startWave()'); assert.ok(clean.requests.some(x=>x.url==='/api/console/room/start'),'no warning → straight to start');
  assert.equal(clean.run('typeof formOptions'),'undefined');
});

test('#65: the missing machines are named with the three actions; power-off only for one that can hear, behind a confirm window', async () => {
  const miss=[{mac:C2,name:'מחשב 2',reason:'drawers',ports:[3],awake:true,joined:true,can_poweroff:true,text_he:'מחשב 2: אין דיסק חדש בדיסק 3'},
              {mac:C3,name:'מחשב 3',reason:'off',ports:[1,2],awake:false,joined:false,can_poweroff:false,text_he:'מחשב 3 כבוי או לא מחובר — לא הצטרף לגל (דיסק 1, דיסק 2)'}];
  const s=setup({room:roomOpenWave({missing:miss})});
  const html=s.run('deploy(0)'); balanced(html);
  const note=between(html,'חסרים בגל','</div></span></div>');
  assert.match(note,/חסרים בגל: 2 מחשבים<\/b> — 4 מגירות מוכנות/);
  assert.match(note,/מחשב 2: אין דיסק חדש בדיסק 3 <button type="button" class="btn sm" onclick="poweroffMissing\('78:ac:c0:9b:11:c2'\)">כבה אותו<\/button>/);
  assert.match(note,/מחשב 3 כבוי או לא מחובר[^<]*<\/span>/,'an off machine gets no power-off button');
  assert.equal((note.match(/כבה אותו/g)||[]).length,1);
  assert.match(note,/onclick="startWave\(\)">התחל בכל זאת \(4 מוכנים\)<\/button><button type="button" class="btn danger" onclick="stopRoom\(\)">בטל את הסבב<\/button>/);
  s.run(`poweroffMissing('${C2}')`);
  assert.equal(s.run('formOptions.danger'),true); assert.equal(s.run('formOptions.submitLabel'),'כיבוי');
  s.requests.length=0; await s.run('formOptions.onSubmit({})');
  const r=s.requests.find(x=>x.options.method==='POST');
  assert.equal(r.url,`/api/console/room/missing/${encodeURIComponent(C2)}/poweroff`); assert.deepEqual(JSON.parse(r.options.body),{confirm_name:'מחשב 2'});
  const v=setup({room:roomOpenWave({missing:miss})}); v.run('ME.role="viewer"');
  const vh=v.run('deploy(0)'); assert.match(vh,/חסרים בגל/); assert.doesNotMatch(vh,/כבה אותו|התחל בכל זאת|בטל את הסבב/,'actions only for room operators');
  const run2=setup({room:roomRunning({missing:miss})}); assert.doesNotMatch(run2.run('deploy(0)'),/חסרים בגל/,'a running wave has nothing missing to act on');
});

test('17/10 (metal) + #1376: a live round shows only the cloners and drawers it writes to (round.target_slots {mac, ports}); a round with no choice (null) shows them all', () => {
  const picked=setup({room:roomRunning({target_slots:[{mac:C1,ports:[2]},{mac:C2,ports:[1,3]}]})}).run('deploy(0)');
  const grid=between(picked,'<div class="rgrid round">','<div class="c12 rnotes">');
  assert.match(grid,new RegExp(`data-mac="${C1}"`));
  assert.doesNotMatch(grid,new RegExp(`data-mac="${C3}"`),'a cloner outside the round is not on its screen');
  assert.match(picked,/obj-sub"[^>]*>גל 2 · office365 · 2 מחשבים</,'"N מחשבים" counts the cloners of the round');
  assert.equal((mach(grid,C1).match(/<div class="dsk[ "]/g)||[]).length,1); assert.doesNotMatch(mach(grid,C1),/data-slot="1"/,'a drawer that was not ticked is not a row');
  assert.match(mach(grid,C1),/data-slot="2"/);
  assert.match(mach(grid,C2),/data-slot="1"[^]*data-slot="3"/); assert.doesNotMatch(mach(grid,C2),/data-slot="2"/,'the orange drawer was not ticked: no row, no CRC line');
  const all=between(setup({room:roomRunning({target_slots:null})}).run('deploy(0)'),'<div class="rgrid round">','<div class="c12 rnotes">');
  for (const mac of [C1,C2,C3]) assert.match(all,new RegExp(`data-mac="${mac}"`));
  assert.match(mach(all,C1),/data-slot="1"[^]*data-slot="2"/);
});

test('#1386: "בטל…" only on a writing drawer of the live round, for a round operator (shown on hover/focus over the percent); cancelled = grey, empty bar, who and when; cancelling = "מבטל…"', () => {
  const r=roomRunning();
  r.machines[1].drawer_list[1]={...r.machines[1].drawer_list[1],state:'cancelled',bytes_written:0,
    cancel:{by:'nadav',source:'console',done:true,text_he:'בוטל על ידי nadav · 16:06'}};
  const {run}=setup({room:r});
  const html=run('deploy(0)'); balanced(html);
  const grid=between(html,'<div class="rgrid round">','<div class="c12 rnotes">');
  const c1=mach(grid,C1), c2=mach(grid,C2);
  assert.match(c1,/<b>38%<\/b><button type="button" class="btn sm danger dx" onclick="cancelDrawer\('a0%3A48%3A1c%3A8a%3A18%3A40',1\)" aria-label="בטל את הכתיבה לדיסק 1">בטל…<\/button><\/div>/);
  assert.equal((c1.match(/cancelDrawer\(/g)||[]).length,2,'both writing drawers of machine 1');
  assert.doesNotMatch(c2.match(/data-slot="3"[^]*?<\/b>/)[0],/cancelDrawer/,'a failed drawer has nothing to cancel');
  assert.match(c2,/<div class="dsk cancelled" data-slot="2" [^>]*><span>דיסק 2<\/span><div class="bar "><i style="--w:0%"><\/i><\/div><b>בוטל<\/b><\/div>/,'grey, an empty bar -- not "37%", no button');
  assert.match(c2,/<div class="mach-note">דיסק 2 — <bdi>בוטל על ידי nadav · 16:06<\/bdi> · טבלת המחיצות נמחקה, הדיסק יוצג ריק<\/div>/);
  assert.doesNotMatch(grid,/sd[abc]/,'never sd*, not even in onclick');
  run("ROOM.machines[0].drawer_list[1]={...ROOM.machines[0].drawer_list[1],state:'cancelled',cancel:{by:'nadav',done:true,text_he:'בוטל על ידי nadav'}}");
  assert.match(run('deploy(0)'),new RegExp(`<div class="mach" data-mac="${C1}"`),'a cancel does not make the machine red');
  run("ROOM.machines[1].drawer_list[1]={...ROOM.machines[1].drawer_list[1],state:'writing',cancel:{by:'nadav',done:false,text_he:'בוטל על ידי nadav'}}");
  assert.match(mach(run('deploy(0)'),C2),/<div class="dsk cancelling" data-slot="2" [^>]*><span>דיסק 2<\/span><div class="bar warn"><i style="--w:0%"><\/i><\/div><b>מבטל…<\/b><\/div>/);
  run('ME=null'); assert.doesNotMatch(run('deploy(0)'),/cancelDrawer\(/,'signed out: no button');
  const css=fs.readFileSync(path.join(root,'console.css'),'utf8');
  assert.match(css,/\.page \.rgrid\.round \.dsk:hover \.dx,\.page \.rgrid\.round \.dsk:focus-within \.dx\{opacity:1;pointer-events:auto\}/,'the button appears on hover and on keyboard focus');
  assert.match(css,/@media \(hover:none\)\{\.page \.rgrid\.round \.dsk \.dx\{position:static;[^}]*opacity:1/,'without hover (touch) it is always shown');
});

test('#1386: the window is the console\'s danger sheet -- "המשך לכתוב" (never "ביטול"), the red "בטל את דיסק N", no typed word; it posts {mac, dev}', async () => {
  const {run,requests,fixtures}=setup({room:roomRunning()});
  run(`cancelDrawer(encodeURIComponent('${C2}'),2)`);
  assert.equal(run('formOptions.danger'),true); assert.equal(run('formOptions.verify'),undefined,'#1270');
  assert.equal(run('formOptions.cancelLabel'),'המשך לכתוב'); assert.equal(run('formOptions.submitLabel'),'בטל את דיסק 2');
  assert.equal(run('formOptions.title'),'לבטל את הכתיבה לדיסק 2?');
  assert.match(run('formOptions.sub'),/^מחשב 2 · מגירה 2 · 870 EVO · .+ · עכשיו ב-37%$/);
  const note=run('formOptions.note');
  assert.match(note,/<li class="bad">[^]*הכתיבה לדיסק הזה נעצרת מיד/); assert.match(note,/<li class="bad">[^]*טבלת המחיצות שלו נמחקת/);
  assert.match(note,/<li class="imp-ok">[^]*שאר המגירות ממשיכות/); assert.match(note,/<li class="imp-ok">[^]*לא נחשב כשל/);
  assert.match(run('formOptions.hint'),/בוטל על ידי admin/);
  assert.ok(!requests.some(r=>r.url==='/api/console/room/drawer/cancel'),'nothing posted before the sheet is confirmed');
  await run('formOptions.onSubmit({})');
  const post=requests.find(r=>r.url==='/api/console/room/drawer/cancel');
  assert.equal(post.options.method,'POST'); assert.deepEqual(JSON.parse(post.options.body),{mac:C2,dev:'sdb'});
  fixtures['/room/drawer/cancel']={__status:409,detail:'הדיסק אינו כותב עכשיו — אין מה לבטל'};
  await assert.rejects(run('formOptions.onSubmit({})'),/הדיסק אינו כותב עכשיו/,'the server\'s reason reaches the sheet\'s error line');
  run('ME=null; formOptions=undefined'); run(`cancelDrawer(encodeURIComponent('${C2}'),2)`); assert.equal(run('typeof formOptions'),'undefined');
});

test('#1936: a drawer the operator cancelled in an earlier wave says "בוטל", not "נכתב" 100%', () => {
  // wave 2: S5Y11 (C1 disk 2) was cancelled in wave 1 -- closed in the round, not a target now
  const s=setup({room:roomRunning({wave_number:2})});
  s.run("Object.assign(ROOM.machines[0].drawer_list[1],{state:null,bytes_written:0,bytes_total:0,fresh:false,cancelled_round:true})");
  let html=s.run('deploy(0)'); balanced(html);
  assert.match(mach(html,C1),/<div class="dsk cancelled" data-slot="2"[^>]*><span>דיסק 2<\/span><div class="bar "[^>]*><i style="--w:0%"><\/i><\/div><b>בוטל<\/b>/);
  // the guard: written in an earlier wave (no cancelled_round) is still "נכתב" 100% (#1790)
  s.run("ROOM.machines[0].drawer_list[1].cancelled_round=false");
  html=s.run('deploy(0)');
  assert.match(mach(html,C1),/<div class="dsk" data-slot="2"[^>]*><span>דיסק 2<\/span><div class="bar ok"[^>]*><i style="--w:100%"><\/i><\/div><b>נכתב<\/b>/);
});
