// #3897 (נדב 20/11, מוקאפ m3897-round-open, מצבים 1-3): הצעת השרת בחלון "התחל סבב".
// ההתנהגות נבדקת דרך הפונקציות שהחלון קורא: הקלדת כמות -> GET /room/suggest -> הבחירה מתמלאת, הכרטיס מצויר,
// עריכה ידנית מסומנת, וכשל בקריאה אינו נראה כ"אין משכפלים פנויים".
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {consoleJs}=require('./console_src.cjs');
const root=path.resolve(__dirname,'../server/static');
const GB=256060514304;
const MACS=['aa:bb:cc:00:00:01','aa:bb:cc:00:00:02','aa:bb:cc:00:00:03'];
const {ruled}=require('./disk_rule_fixture.cjs');
const drawer=(mac,port)=>ruled({dev:'sd'+'abc'[port-1],port,serial:`S${mac.slice(-1)}${port}`,model:'870 EVO',size_bytes:GB,fresh:true,
  state:null,error:null,bytes_written:0,bytes_total:0,smart:'ok'});

function setup() {
  const node=()=>({value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{},classList:{add(){},remove(){},toggle(){},contains:()=>false},
    setAttribute(){},getAttribute(){return null;},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],focus(){}});
  const machines=MACS.map((mac,i)=>({mac,name:`cloner-${i+1}`,drawer_count:3,drawer_slots:['sata:1','sata:2','sata:3'],awake:true,drawer_list:[1,2,3].map((p)=>drawer(mac,p))}));
  const room={round:null,disk_floor:null,clone_names:{pad:2,instructor:'INS',max_len:15},machines};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,RegExp,Error,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,activeElement:null,querySelector:()=>node(),querySelectorAll:()=>[],getElementById:()=>node(),
      createElement:()=>node(),documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},removeEventListener(){},body:{appendChild(){}}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({ok:true})})});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx); vm.runInContext(consoleJs(),ctx);
  const run=(code)=>vm.runInContext(code,ctx);
  run(`ME={username:"labtech",role:"deploy",server_name:"srv",version:"v0",capabilities:{}}; current="deploy"; renderCurrent=()=>{}; toast=()=>{}; loadDeploy=async()=>{}; closeModal=()=>{}; openRoomRound=()=>{};`);
  const reg=machines.map((m)=>({mac:m.mac,suffix:m.name,group_id:'grp_CLONERS',drawer_count:3,drawer_slots:['sata:1','sata:2','sata:3'],disks:m.drawer_list,prompt:null}));
  run(`ROOM=${JSON.stringify(room)}; MACHINES=${JSON.stringify(reg)}; IMAGES=${JSON.stringify([{id:'img_1',name:'office365',folder:'',os:'windows',min_target_bytes:60e9,partitions:4,expand_partitions:[]}])}; DEPLOY.form.image='img_1';`);
  return run;
}
const dr=(port,state,extra={})=>({port,drawer:port,serial:`S${port}`,state,reason_he:null,...extra});
const cl=(n,drawers,extra={})=>({mac:MACS[n-1],name:`cloner-${n}`,number:n,display_name:`משכפל-${n}`,awake:true,state:'free',busy_in:null,drawers,...extra});
const all3=(sel=3)=>[1,2,3].map((p)=>dr(p,p<=sel?'selected':'spare'));
// ההצעה כמו שהשרת מחזיר אותה: selected הוא בדיוק הצורה של target_slots
function answer(machines,n) {
  const selected=machines.map((m)=>({mac:m.mac,ports:m.drawers.filter((d)=>d.state==='selected').map((d)=>d.port)})).filter((s)=>s.ports.length);
  const count=selected.reduce((a,s)=>a+s.ports.length,0);
  return {target_drives:n,machines,selected,selected_count:count,shortfall:n-count,line:n>count&&count>0,waves:count?Math.ceil(n/count):0};
}
function serve(run,data) {   // api() מחזיר את התשובה, ושומר מה נשאל
  const body=data instanceof Error?`throw new Error(${JSON.stringify(data.message)})`:`return ${JSON.stringify(data)}`;
  run(`globalThis.ASKED=[]; api=async(p)=>{ASKED.push(p); ${body};}`);
}
const asked=(run)=>JSON.parse(run('JSON.stringify(ASKED)'));
const sel=(run)=>Object.keys(JSON.parse(run('JSON.stringify(DEPLOY.form.sel)'))).sort();

test('typing a count asks the server for that many and fills the selection with exactly what it proposed', async () => {
  const run=setup();
  serve(run,answer([cl(1,all3()),cl(2,all3()),cl(3,[dr(1,'selected'),dr(2,'spare'),dr(3,'spare')])],7));
  run(`DEPLOY.form.target="7"`);
  await run('rsFetch()');
  assert.deepEqual(asked(run),['/room/suggest?target_drives=7']);
  assert.deepEqual(sel(run),[...[1,2,3].map((p)=>`${MACS[0]}@${p}`),...[1,2,3].map((p)=>`${MACS[1]}@${p}`),`${MACS[2]}@1`].sort());
  const card=run('rsCard()');
  assert.ok(card.includes('7 דיסקים ב-3 משכפלים'),'the header counts what is selected');
  assert.ok(card.includes('לפי סדר המספרים'));
  assert.ok(!card.includes('נערך ידנית'));
  assert.ok(!card.includes('נכנס חלקית'),'no red drawer, so no "partial" explanation');
});

test('a red drawer is drawn as a non-clickable red tile, and the banner says why the next cloner filled in', async () => {
  const run=setup();
  const m=[cl(1,all3()),cl(2,[dr(1,'selected'),dr(2,'selected'),dr(3,'red',{reason_he:'SMART נכשל'})]),cl(3,[dr(1,'selected'),dr(2,'spare'),dr(3,'spare')])];
  serve(run,answer(m,6));
  run(`DEPLOY.form.target="6"`); await run('rsFetch()');
  const card=run('rsCard()');
  assert.match(card,/<span class="dt red"[^>]*aria-label="מגירה 3 אדומה/,'the red tile is a span, not a button');
  assert.ok(!/<button[^>]*class="dt[^"]*red/.test(card),'a red drawer can never be picked');
  assert.ok(card.includes('<b>משכפל-2 מגירה 3 אדומה</b>'));
  assert.ok(card.includes('משכפל-3 נכנס חלקית'),'the count the operator typed decides, not "all the free ones"');
});

test('clicking a tile marks the proposal as hand-edited, and "back to the proposal" asks again and clears it', async () => {
  const run=setup();
  serve(run,answer([cl(1,all3()),cl(2,all3())],6));
  run(`DEPLOY.form.target="6"`); await run('rsFetch()');
  run(`rsTile(${JSON.stringify(MACS[1]+'@3')})`);
  assert.equal(run('DEPLOY.form.sug.edited'),true);
  assert.ok(!sel(run).includes(`${MACS[1]}@3`));
  const card=run('rsCard()');
  assert.ok(card.includes('<b>נערך ידנית</b>'));
  assert.ok(card.includes('חזרה להצעת השרת'));
  await run('rsReset()');
  assert.equal(run('DEPLOY.form.sug.edited'),false);
  assert.ok(sel(run).includes(`${MACS[1]}@3`),'the proposal is back');
  assert.ok(!run('rsCard()').includes('חזרה להצעת השרת'));
  assert.equal(asked(run).length,2);
});

test('fewer free drawers than disks: the warning shows the waves, and "only N" re-asks for N', async () => {
  const run=setup();
  const m=[cl(1,all3()),cl(2,[1,2,3].map((p)=>dr(p,'spare')),{state:'busy',busy_in:'Windows 11 מעבדה'}),cl(3,all3())];
  serve(run,answer(m,18));
  run(`DEPLOY.form.target="18"`); await run('rsFetch()');
  const card=run('rsCard()');
  assert.ok(card.includes('אין מספיק משכפלים פנויים ל-18 דיסקים בבת אחת'));
  assert.ok(card.includes('פס ייצור'));
  assert.ok(card.includes('<div>גל 1 · 6 דיסקים</div><div>גל 2 · 6 דיסקים</div><div>גל 3 · 6 דיסקים</div>'));
  assert.ok(card.includes('פנויים: משכפל-1 ו-משכפל-3 (6 מגירות)'));
  assert.ok(card.includes('rsOnly(6)')&&card.includes('רק 6 דיסקים'));
  assert.ok(card.includes('class="cl busy"')&&card.includes('Windows 11 מעבדה'),'the busy cloner is shown with the round it is in');
  const busy=card.split('class="cl busy"')[1].split('</div></div>')[0];
  assert.ok(!busy.includes('onclick')&&!busy.includes('<button'),'a busy cloner cannot be picked');
  serve(run,answer([cl(1,all3()),cl(3,all3())],6));
  await run('rsOnly(6)');
  assert.deepEqual(asked(run),['/room/suggest?target_drives=6']);
  assert.equal(run('DEPLOY.form.target'),'6');
  assert.ok(!run('rsCard()').includes('אין מספיק משכפלים פנויים'));
});

test('a failed read says the proposal was not read, and leaves the hand selection alone', async () => {
  const run=setup();
  run(`DEPLOY.form.sel[${JSON.stringify(MACS[0]+'@1')}]=true; DEPLOY.form.target="4"`);
  serve(run,new Error('שגיאה 500'));
  await run('rsFetch()');
  const card=run('rsCard()');
  assert.ok(card.includes('ההצעה לא נקראה')&&card.includes('שגיאה 500')&&card.includes('נסה שוב'));
  assert.ok(!card.includes('אין מספיק'),'"could not read" is not "no free cloners"');
  assert.deepEqual(sel(run),[`${MACS[0]}@1`]);
});

test('an older answer that arrives after a newer one is dropped', async () => {
  const run=setup();
  const three=answer([cl(1,all3())],3), six=answer([cl(1,all3()),cl(2,all3())],6);
  run(`globalThis.RESOLVE=[]; api=(p)=>new Promise((res)=>{RESOLVE.push(()=>res(p.endsWith("=3")?${JSON.stringify(three)}:${JSON.stringify(six)}));});`);
  run(`DEPLOY.form.target="3"`); const first=run('rsFetch()');
  run(`DEPLOY.form.target="6"`); const second=run('rsFetch()');
  run('RESOLVE[1]()'); await second;
  run('RESOLVE[0]()'); await first;
  assert.equal(run('DEPLOY.form.sug.n'),6);
  assert.equal(sel(run).length,6);
});

test('a sleeping cloner that is selected is named in the footer: it will wake by WoL and the round waits for it', async () => {
  const run=setup();
  serve(run,answer([cl(1,all3()),cl(2,all3(),{awake:false})],6));
  run(`DEPLOY.form.target="6"`); await run('rsFetch()');
  assert.ok(run('rsCard()').includes('כבוי · WoL'));
  const st=run('roomSubmitState(roomMachines())');
  assert.ok(String(st.sub).includes('משכפל-2 כבוי: יידלק ב-WoL והסבב ימתין לו'),st.sub);
});

test('no count, or a direct round, proposes nothing; the target field has the - and + steppers', () => {
  const run=setup();
  run(`DEPLOY.form.target=""; rsSchedule()`);
  assert.equal(run('DEPLOY.form.sug'),null);
  assert.ok(run('rsCard()').includes('כתבו כמה דיסקים'));
  run(`DEPLOY.form.sug={n:3,data:null,err:"",edited:false}; DEPLOY.form.target="3"; DEPLOY.form.src="build_disk"; rsSchedule()`);
  assert.equal(run('DEPLOY.form.sug'),null,'a direct round has an explicit target; a stale proposal must not survive the switch');
  const step=run('rsStepper("18")');
  assert.ok(step.includes('onclick="rsStep(-1)"')&&step.includes('onclick="rsStep(1)"')&&step.includes('id="rn-target"')&&step.includes('value="18"'));
});
