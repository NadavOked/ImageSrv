// ‏#2001 (נדב 22/10): חלון "מיפוי חיבורים" בקונסולה, לפי המוקאפ המאושר
// (docs/design/console-redesign/mockups/slot-map-2001.html, ‏surface=console). ההכרעות:
// 1. השם מחליף את "דיסק N"; שורה פעילה בלי שם = "דיסק N" לפי הסדר; הסדר = סדר החיבורים.
// 2. "חל על": המחשב הזה / כל מחשבי הדגם — ברירת המחדל: כל מחשבי הדגם.
// 3. בלי מיפוי שמור: חיבור עם דיסק פעיל, ריק כבוי. מה שסומן פעיל נשאר פעיל.
// 4. הוספה ידנית: סוג ומספר בלבד, בלי PCI.
// בלי דפדפן: console.js + net.js + progress.js ב-vm מול DOM מזערי ו-fetch מזויף.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const GB=1e9;
const C1='a0:48:1c:8a:18:40', C2='78:ac:c0:9b:11:c2', HP='HP Compaq 8100 Elite SFF PC';
const disk=(dev,port,serial,extra={})=>({dev,port,serial,model:'Samsung SSD 870 EVO',size_bytes:500*GB,smart:'ok',...extra});

function setup({c2={},admin=true,fail=null,fail409=false}={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const c2disks=[disk('sda',1,'S-1'),disk('sdb',2,'S-2',{model:'WDC WD10EZEX',size_bytes:1000*GB}),disk('sdc',4,'S-4',{model:'KINGSTON SA400S37',size_bytes:240*GB}),
    disk('nvme0n1',null,'NV-1',{model:'SAMSUNG MZVLB512',size_bytes:512*GB,pci:'0000:02:00.0'})];
  const base={group_id:'grp_CLONERS',note:null,prompt:null,disks_reported_at:'2026-10-22T08:00:00Z',inventory:null,inventory_seen_at:null,
    slot_model:HP,slot_map_source:'default',model_slot_map:null,drawer_slots_custom:false};
  const fixtures={
    '/groups':[{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:2,machines:2}],
    '/machines':[
      {...base,mac:C1,suffix:'מחשב 1',drawer_count:2,disks:[disk('sda',1,'X-1')],drawer_slots:['sata:1','sata:2'],slot_names:{}},
      {...base,mac:C2,suffix:'מחשב 2',drawer_count:3,disks:c2disks,optical:[{dev:'sr0',model:'hp DVD-RAM GH60L',port:6}],drawer_slots:['sata:1','sata:2','sata:3'],slot_names:{},...c2}],
    '/net':[], '/disk-failures':[], '/shrink-records':[], '/monitor/machines':[], '/tasks':[], '/room/history':[], '/journal':[],
    '/room':{round:null,stream_stalled:false,machines:[]},
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get textContent(){return text},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      if(options.method==='PUT' && fail409 && !/replace_machine_maps/.test(options.body||'')) return {status:409,ok:false,headers:{get:()=>null},json:async()=>({code:'machine_maps',message_he:'ל-2 מחשבים מהדגם יש מיפוי משלהם — הוא יוחלף',machines:['x','y']})};
      if(options.method==='PUT' && fail) return {status:400,ok:false,headers:{get:()=>null},json:async()=>({detail:fail})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] ?? {ok:true,machines:[C1,C2]}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run(`ME={username:"nadav",role:"${admin?'admin':'deploy'}",server_name:"srv",version:"v0.67.1",capabilities:{}}; current="machines"; toast=(m)=>{ globalThis.toasted=m; }; renderCurrent=()=>{}; openDrawer=(t,h)=>{ globalThis.drawerHtml=h; }; setDrawerBody=(h)=>{ globalThis.drawerHtml=h; }; loadMachines=async()=>{ globalThis.reloaded=(globalThis.reloaded||0)+1; };`);
  for(const [name,key] of [['MACHINES','/machines'],['GROUPS','/groups'],['DISK_FAILURES','/disk-failures'],['SHRINK_RECORDS','/shrink-records'],['NET','/net'],['MONITOR_ROWS','/monitor/machines'],['ROOM','/room'],['ROOM_HISTORY','/room/history']]) run(name+'='+JSON.stringify(fixtures[key]));
  return {run,requests,node};
}
const win=(node)=>node('#smap').innerHTML;
const rows=(html)=>[...html.matchAll(/<tr class="([^"]*)"(?: data-key="([^"]*)")?>([^]*?)<\/tr>/g)].filter(m=>!/addrow/.test(m[1])).map(m=>({cls:m[1],key:m[2],html:m[3]}));
const lastPut=(requests)=>requests.filter(r=>r.options.method==='PUT').pop();

test('no saved map: a row per connection the machine shows, in connection order; a disk = active, empty = off; CD/DVD is never a disk', () => {
  const {run,node}=setup(); const enc=encodeURIComponent(C2);
  run(`openSlotMap('${enc}')`);
  assert.equal(node('#modalTitle').textContent,'מיפוי חיבורים');
  assert.equal(node('#modalSub').textContent.split(' · ').slice(0,3).join('|'),`⁨מחשב 2⁩|⁨${HP}⁩|⁨${C2}⁩`,'each part isolated, in order');
  const html=win(node), r=rows(html);
  assert.deepEqual(r.map(x=>x.html.match(/<b>([^<]*)<\/b>/)[1]),['SATA 0','SATA 1','SATA 2','SATA 3','SATA 4','SATA 5','NVMe 1'],'SATA 0..5 (the CD/DVD sets the last port), then NVMe');
  assert.deepEqual(r.map(x=>/checked/.test(x.html)),[true,true,false,true,false,false,true],'decision 3: a disk now = active, an empty connection = off');
  assert.match(r[0].html,/Samsung SSD 870 EVO<\/span> · <span class="sz"><bdi dir="ltr">500GB<\/bdi>/);
  assert.match(r[0].html,/placeholder="דיסק 1"/); assert.match(r[1].html,/placeholder="דיסק 2"/);
  assert.match(r[3].html,/placeholder="דיסק 4"/,'Nadav 22/10: no saved map → SATA n = דיסק n+1, the gap at SATA 2 does not renumber');
  assert.match(r[6].html,/placeholder="דיסק 7"/,'NVMe after the highest SATA row'); assert.match(r[6].html,/<span class="pci mono">02:00.0<\/span>/);
  assert.match(r[2].html,/<span class="na">לא בשימוש<\/span>/); assert.match(r[2].html,/<span class="vac">ריק<\/span>/);
  assert.match(r[5].html,/כונן CD\/DVD · <bdi>hp DVD-RAM GH60L<\/bdi>[^]*לא דיסק — לא יעד ולא מקור[^]*<span class="n-sw smap-sw na-sw" aria-hidden="true">/); assert.doesNotMatch(r[5].html,/<input type="checkbox"/,'no switch to flip');
  assert.match(html,/<div class="smap-note"><b>אין מיפוי שמור<\/b>/);
  assert.match(node('#modalHint').innerHTML,/4 פעילים · ברירת המחדל/);
});

test('decision 2: "applies to" defaults to all machines of the model, with the count; a machine with its own map opens on "this machine"', () => {
  let {run,node}=setup(); run(`openSlotMap('${encodeURIComponent(C2)}')`);
  let html=win(node);
  assert.match(html,/aria-pressed="false" onclick="smapScope\('machine'\)">המחשב הזה<\/button><button type="button" aria-pressed="true" onclick="smapScope\('model'\)">כל ה-<bdi>HP Compaq 8100 Elite SFF PC<\/bdi> \(2 מחשבים\)<\/button>/);
  ({run,node}=setup({c2:{slot_map_source:'machine',drawer_slots_custom:true,drawer_slots:['sata:2'],slot_names:{'sata:2':'שלי'}}}));
  run(`openSlotMap('${encodeURIComponent(C2)}')`); html=win(node);
  assert.match(html,/aria-pressed="true" onclick="smapScope\('machine'\)">המחשב הזה/);
  assert.deepEqual(rows(html).map(x=>/checked/.test(x.html)),[false,true,false,false,false,false,false],'its own map, not the disks');
  assert.match(rows(html)[1].html,/value="שלי"/);
  assert.doesNotMatch(html,/אין מיפוי שמור/);
  ({run,node}=setup({c2:{slot_model:null}}));
  run(`openSlotMap('${encodeURIComponent(C2)}')`); html=win(node);
  assert.doesNotMatch(html,/smapScope\('model'\)/); assert.match(html,/המחשב לא דיווח דגם — אפשר לשמור רק לו/);
});

test('decision 3: an empty connection switched on stays on; the numbers follow the order; the footer counts unsaved changes', () => {
  const {run,node}=setup(); run(`openSlotMap('${encodeURIComponent(C2)}')`);
  run('smapToggle(2)');   // SATA 2, empty — an edit: from here the map numbers by order (decision 1)
  const r=rows(win(node));
  assert.match(r[2].html,/checked/); assert.match(r[2].html,/placeholder="דיסק 3"/);
  assert.match(r[3].html,/placeholder="דיסק 4"/,'SATA 3 moved to "דיסק 4"');
  run('smapName(0,"מגירה תחתונה")');
  assert.match(node('#modalHint').innerHTML,/5 פעילים<\/span><span class="smap-dirty"> · 2 שינויים לא נשמרו/);
  run('smapToggle(5)'); assert.doesNotMatch(rows(win(node))[5].html,/checked/,'the CD/DVD switch does nothing');
});

test('save: one PUT with the scope, the active connections in connection order and only the given names; the toast says how many machines', async () => {
  const {run,requests}=setup(); const enc=encodeURIComponent(C2);
  run(`openSlotMap('${enc}')`);
  run('smapToggle(2)'); run('smapName(0,"  מגירה תחתונה ")'); run('smapName(6,"דיסק מהיר")');
  await run('smapSave()');
  const put=lastPut(requests);
  assert.equal(put.url,`/api/console/machines/${enc}/slot-map`);
  assert.deepEqual(JSON.parse(put.options.body),{scope:'model',slots:['sata:1','sata:2','sata:3','sata:4','nvme:0000:02:00.0'],names:{'sata:1':'מגירה תחתונה','nvme:0000:02:00.0':'דיסק מהיר'}});
  assert.equal(run('SMAP'),null); assert.equal(run('reloaded'),1); assert.equal(run('toasted'),'המיפוי נשמר — חל על 2 מחשבים');
});

test('two rows with the same name (also against another row\'s "דיסק N") are refused before any request, and the field says where', async () => {
  const {run,requests,node}=setup(); run(`openSlotMap('${encodeURIComponent(C2)}')`);
  run('smapName(1,"מגירה עליונה")'); run('smapName(3,"מגירה עליונה")');
  await run('smapSave()');
  assert.equal(lastPut(requests),undefined,'nothing sent');
  const html=win(node), r=rows(html);
  assert.match(html,/<div class="smap-note err" role="alert"><b>לא נשמר:<\/b> שתי שורות עם אותו שם \("מגירה עליונה"\)/);
  assert.match(r[3].html,/class="n-in smap-in bad"[^>]*aria-invalid="true"[^]*<div class="ferr" id="smap-e3">השם כבר בשימוש ב-SATA 1<\/div>/);
  run('smapName(3,"דיסק 1")'); run('SMAP.err=null'); await run('smapSave()');   // edited → "דיסק N" by order
  assert.equal(lastPut(requests),undefined,'"דיסק 1" is SATA 0\'s automatic name');
  assert.match(rows(win(node))[3].html,/השם כבר בשימוש ב-SATA 0/);
});

test('decision 4: "+ הוסף חיבור" takes a type and a number, no PCI; NVMe n without a drive is nvme#n, marked "not found"', async () => {
  const {run,requests,node}=setup(); run(`openSlotMap('${encodeURIComponent(C2)}')`);
  assert.match(win(node),/<button type="button" class="link" onclick="smapAddOpen\(\)">\+ הוסף חיבור<\/button>/);
  run('smapAddOpen()'); let html=win(node);
  assert.doesNotMatch(html,/PCI<\/label>|כתובת PCI<\/label>/,'no PCI field');
  assert.match(html,/<span class="lbl" id="smap-at">סוג<\/span>[^]*>SATA<\/button>[^]*>NVMe<\/button>[^]*<label class="lbl" for="smap-anum">מספר<\/label>[^]*<label class="lbl" for="smap-aname">שם<\/label>/);
  run('smapAddType("NVMe")'); run('SMAP.add.num="2"'); run('SMAP.add.name="דיסק מהיר"'); run('smapAddCommit()');
  html=win(node); const r=rows(html);
  const added=r.find(x=>x.key==='nvme#2');
  assert.ok(added,'NVMe 2 → nvme#2'); assert.match(added.cls,/new/); assert.match(added.html,/<b>NVMe 2<\/b>/);
  assert.match(added.html,/נוסף ידנית · לא נמצא במחשב/); assert.match(added.html,/value="דיסק מהיר"/);
  assert.equal(r[r.length-1].key,'nvme#2','after the NVMe the machine shows');
  run('smapAddOpen()'); run('SMAP.add.num="1"'); run('smapAddType("NVMe")'); run('SMAP.add.num="1"'); run('smapAddCommit()');
  assert.equal(rows(win(node)).filter(x=>x.key==='nvme:0000:02:00.0').length,1,'NVMe 1 is the controller already shown — no second row');
  run('smapAddOpen()'); run('SMAP.add.num="40"'); run('smapAddCommit()');
  assert.match(win(node),/<p class="ferr" role="alert">מספר SATA בין 0 ל-31<\/p>/);
  run('SMAP.add.num="7"'); run('smapAddCommit()');
  assert.ok(rows(win(node)).some(x=>x.key==='sata:8'),'SATA 7 = sata:8 (ataN)');
  await run('smapSave()');
  const body=JSON.parse(lastPut(requests).options.body);
  assert.deepEqual(body.slots,['sata:1','sata:2','sata:4','sata:8','nvme:0000:02:00.0','nvme#2']);
  assert.equal(body.names['nvme#2'],'דיסק מהיר');
});

test('a refused save keeps the window and the edits, and shows the server reason', async () => {
  const {run,node}=setup({fail:'השם יכול להכיל אותיות, ספרות, רווח ו- - . # ( ) / בלבד'}); run(`openSlotMap('${encodeURIComponent(C2)}')`);
  run('smapName(0,"x;y")'); await run('smapSave()');
  assert.match(win(node),/<div class="smap-note err" role="alert"><b>לא נשמר:<\/b> השם יכול להכיל/);
  assert.ok(run('SMAP')!==null); assert.match(rows(win(node))[0].html,/value="x;y"/);
});

test('reset: only when something is saved in that scope; it PUTs slots:null for the chosen scope', async () => {
  let {run,requests,node}=setup(); run(`openSlotMap('${encodeURIComponent(C2)}')`);
  assert.equal(node('#smap-reset').hidden,true,'nothing saved → no reset button');
  ({run,requests,node}=setup({c2:{slot_map_source:'model',model_slot_map:{slots:['sata:1'],names:{'sata:1':'דגם'}}}}));
  run(`openSlotMap('${encodeURIComponent(C2)}')`);
  assert.equal(node('#smap-reset').hidden,false);
  assert.deepEqual(rows(win(node)).map(x=>/checked/.test(x.html)),[true,false,false,false,false,false,false],'the model map is what opens');
  run('smapScope("machine")'); assert.equal(node('#smap-reset').hidden,true,'the machine has nothing of its own');
  run('smapScope("model")'); await run('smapReset()');
  assert.deepEqual(JSON.parse(lastPut(requests).options.body),{scope:'model',slots:null});
});

test('a non-admin gets no window', () => {
  const {run}=setup({admin:false}); run(`openSlotMap('${encodeURIComponent(C2)}')`);
  assert.equal(run('SMAP'),null);
});

test('decision 1: the name replaces "דיסק N" — NVMe by its connection, a port-only record, the round history', () => {
  const {run}=setup({c2:{slot_map_source:'model',drawer_slots:['sata:1','sata:2','sata:4','nvme:0000:02:00.0'],
    slot_names:{'sata:4':'מגירה עליונה','nvme:0000:02:00.0':'דיסק מהיר'}}});
  const m=`findMachine('${C2}')`;
  assert.equal(run(`diskLabel(${m}, ${m}.disks[3])`),'דיסק מהיר','NVMe named by nvme:<PCI>');
  assert.equal(run(`diskLabel(${m}, ${m}.disks[2])`),'מגירה עליונה');
  assert.equal(run(`diskLabel(${m}, ${m}.disks[1])`),'דיסק 2','no name → "דיסק N"');
  assert.equal(run(`portLabel(${m}, 4)`),'מגירה עליונה','a shrink record / SMART question / floor carries only the port');
  assert.equal(run(`portLabel(${m}, 3)`),'דיסק 3','an unmapped port reads as before');
  assert.match(run(`disksHtml([{disk:'sdc',disk_number:3,slot_name:'מגירה עליונה',verdict:'warn',decision:null}])`),/מגירה עליונה/,'history');
  assert.match(run(`whereList([{m:${m},s:{n:3,disk:${m}.disks[2]}},{m:${m},s:{n:2,disk:${m}.disks[1]}}])`),/מחשב 2 · מגירה עליונה, דיסק 2/);
});

test('Nadav 22/10: saving for the model when machines of the model have their own map asks first (a sheet), sends nothing, and "back" keeps the edits', async () => {
  const {run,requests,node}=setup();
  run(`MACHINES.find(m=>m.mac==='${C1}').slot_map_source='machine'`);
  run('sheet=(o)=>{ globalThis.formOptions=o; }');
  run(`openSlotMap('${encodeURIComponent(C2)}')`); run('smapName(0,"מגירה תחתונה")');
  await run('smapSave()');
  assert.equal(lastPut(requests),undefined,'nothing sent before the answer');
  const o=run('formOptions');
  assert.equal(o.submitLabel,'החלף ושמור'); assert.equal(o.danger,true);
  assert.match(o.note,/ל-מחשב אחד מהדגם יש מיפוי משלהם — הוא יוחלף: מחשב 1/);
  o.extra.onClick();   // "חזרה למיפוי"
  assert.match(rows(win(node))[0].html,/value="מגירה תחתונה"/,'the edit survived the question');
  await o.onSubmit({});
  assert.deepEqual(JSON.parse(lastPut(requests).options.body),{scope:'model',slots:['sata:1','sata:2','sata:4','nvme:0000:02:00.0'],names:{'sata:1':'מגירה תחתונה'},replace_machine_maps:true});
});

test('no own maps in the model, or "this machine" → saved without a question', async () => {
  const {run,requests}=setup();
  run('sheet=(o)=>{ globalThis.formOptions=o; }');
  run(`openSlotMap('${encodeURIComponent(C2)}')`); await run('smapSave()');
  assert.equal(run('globalThis.formOptions'),undefined);
  assert.equal(JSON.parse(lastPut(requests).options.body).replace_machine_maps,undefined);
});

test('a 409 machine_maps from the server (stale data in the browser) also becomes the question', async () => {
  const {run,requests}=setup({fail409:true});
  run('sheet=(o)=>{ globalThis.formOptions=o; }');
  run(`openSlotMap('${encodeURIComponent(C2)}')`); await run('smapSave()');
  assert.equal(requests.filter(r=>r.options.method==='PUT').length,1);
  assert.ok(run('globalThis.formOptions'),'the 409 became the question'); assert.match(run('formOptions').note,/ל-2 מחשבים מהדגם יש מיפוי משלהם — הוא יוחלף/);
  assert.match(run("SMAP ? '' : 'gone'"),/^$/,'the window state is kept for "back"');
});
