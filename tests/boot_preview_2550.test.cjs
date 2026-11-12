// ‏#2550: "באתחול הבא" — עמודה בטבלת המחשבים וכרטיס במגירה. בלי דפדפן: console.js + net.js + progress.js
// ב-vm מול DOM מזערי ו-fetch מזויף, והפלט נבדק כטקסט (כמו machines_page.test.cjs).
// ההחלטה היא של השרת (`decide()`); כאן נבדקים המילים, והעיקרון: לא נקרא / לא ידוע ≠ "דיסק מקומי" (עיקרון 5),
// קריאה אחת לכל החדר, וקריאה שנכשלה אינה משאירה תצוגה ישנה שנראית עדכנית.
const {consoleJs, consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const minutesAgo=(n)=>new Date(Date.now()-n*60000).toISOString();

const BUILD='c8:d9:d2:0b:fe:32', CLONER1='a0:48:1c:8a:18:40', CLONER2='78:ac:c0:9b:11:c2', CLONER3='78:ac:c0:9b:11:c3', STATION='b4:2e:99:07:1a:c1';

function setup(preview) {
  const nodes=new Map(), requests=[];
  const node=(key)=>{ if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
    classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},querySelector(){return null;},querySelectorAll(){return [];},
    insertAdjacentHTML(){},setAttribute(){},removeAttribute(){},focus(){}}); return nodes.get(key); };
  const fixtures={
    '/groups':[{id:'grp_BUILD',label:'מחשבי בנייה',role:'build',sort:1},{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:2}],
    '/machines':[
      {mac:BUILD,suffix:'HP-BUILD-1',group_id:'grp_BUILD',disks:null},
      {mac:CLONER1,suffix:'משכפל 1',group_id:'grp_CLONERS',drawer_count:2,disks:null},
      {mac:CLONER2,suffix:'משכפל 2',group_id:'grp_CLONERS',drawer_count:3,disks:null},
      {mac:CLONER3,suffix:'משכפל 3',group_id:'grp_CLONERS',drawer_count:4,disks:null}],
    '/disk-failures':[],'/shrink-records':[],'/net':[],'/monitor/machines':[],'/room':null,'/room/history':[],'/tasks':[],
    '/images':[{id:'img_office',name:'Office 2024 — סטנדרט'}],
    '/machines/boot-preview':preview,
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url)=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push(key);
      if(fixtures[key] instanceof Error) throw fixtures[key];
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] ?? {ok:true}};
    }});
  for(const f of ['progress.js',...consoleFiles(),'net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",version:"v0.31.1",capabilities:{}}; current="machines"; toast=(t)=>{ globalThis.toasted=t; };');
  for(const [name,key] of [['MACHINES','/machines'],['GROUPS','/groups'],['DISK_FAILURES','/disk-failures'],['SHRINK_RECORDS','/shrink-records'],['IMAGES','/images']]) run(name+'='+JSON.stringify(fixtures[key]));
  return {run,fixtures,requests,node,ctx};
}

const SESSION={id:'ses_a',state:'open',image_id:'img_office'};
const PREVIEW={checked_at:new Date().toISOString(),guard_limit:3,machines:{
  [BUILD]:{decided:true,kind:'task',code:'task-assigned',action:'agent',role:'build',guard:null,task:{type:'capture',name:'Windows 11 בסיס'},session:null},
  [CLONER1]:{decided:true,kind:'session',code:'session-joinable',action:'agent',role:'cloner',guard:null,task:null,session:SESSION},
  [CLONER2]:{decided:true,kind:'wait',code:'cloner-wait',action:'agent',role:'cloner',guard:null,task:null,session:null},
  [CLONER3]:{decided:true,kind:'menu',code:'boot-loop-guard',action:'local',role:'cloner',guard:{sent:3,limit:3},task:null,session:SESSION}}};

const row=(html,mac)=>{ const i=html.indexOf(`data-mac="${mac}"`); assert.ok(i>=0,mac); return html.slice(i,html.indexOf('</tr>',i)); };
const cell=(tr)=>{ const m=tr.match(/<span class="boot">(.*?)<\/span><\/span>/); assert.ok(m,tr); return m[1]; };

test('#2550 table: the new column sits between "נפח" and "מצב", with the answer and the short reason', () => {
  const {run}=setup(PREVIEW);
  run('BOOT_PREVIEW='+JSON.stringify(PREVIEW));
  const html=run('machinesTableHtml()');
  const head=html.slice(html.indexOf('<thead>'),html.indexOf('</thead>')).replace(/<[^>]+>/g,'|');
  assert.match(head,/נפח\|+באתחול הבא\|+מצב/);
  assert.match(cell(row(html,BUILD)),/pill info">משימה פתוחה: קליטה<\/span><span class="why">Windows 11 בסיס · מחכה שהמחשב יעלה/);
  assert.match(cell(row(html,CLONER1)),/pill info">מצטרף לסבב<\/span><span class="why">סבב פתוח: Office 2024 — סטנדרט/);
  assert.match(cell(row(html,CLONER2)),/pill info">ImageSrv, מסך המתנה<\/span><span class="why">אין סבב פתוח: מחכה לסבב/);
});

test('#2550 table: a fired boot-loop guard is orange, never blue, and says why in short', () => {
  const {run}=setup(PREVIEW);
  run('BOOT_PREVIEW='+JSON.stringify(PREVIEW));
  const c=cell(row(run('machinesTableHtml()'),CLONER3));
  assert.match(c,/pill warn">תפריט בחירה<\/span><span class="why">הגנת לולאת אתחול ירתה$/);
});

test('#2550 every kind has words, and an auto-boot says which screen', () => {
  const {run}=setup(PREVIEW);
  const text=(item,mac=CLONER2)=>{ run('BOOT_PREVIEW='+JSON.stringify({checked_at:null,machines:{[mac]:item}})); return run(`bootView(findMachine('${mac}'))`); };
  assert.equal(text({decided:true,kind:'auto',guard:null},BUILD).line,'אין משימה: עולה למסך מחשב הבנייה');
  assert.equal(text({decided:true,kind:'auto',guard:null},CLONER2).line,'אין משימה: עולה למסך התחנה');
  assert.equal(text({decided:true,kind:'menu',guard:null}).line,'דיסק מקומי או ImageSrv');
  assert.equal(text({decided:true,kind:'local',guard:null}).title,'דיסק מקומי');
  // a guarded cloner still WAITS (it has no local disk): orange, and never a "menu"
  const w=text({decided:true,kind:'wait',guard:{sent:3,limit:3},session:SESSION});
  assert.equal(w.cls,'warn'); assert.match(w.line,/הגנת לולאת אתחול ירתה: הסבב לא נמסר לו/); assert.equal(w.title,'ImageSrv, מסך המתנה');
});

test('#2550 principle 5: not read / could not check / unknown kind is "לא ידוע" -- never "דיסק מקומי", never blue', () => {
  const {run}=setup(PREVIEW);
  const view=(preview)=>{ run('BOOT_PREVIEW='+JSON.stringify(preview)); return run(`bootView(findMachine('${CLONER2}'))`); };
  for(const p of [null,{machines:{}},{machines:{[CLONER2]:{decided:false,why:'answer-failed'}}},{machines:{[CLONER2]:{decided:true,kind:'something-new'}}}]) {
    const v=view(p);
    assert.equal(v.title,'לא ידוע',JSON.stringify(p)); assert.equal(v.cls,'',JSON.stringify(p)); assert.doesNotMatch(v.title+v.line,/דיסק מקומי/);
  }
});

test('#2550 drawer card: what, why, the open round and who else joins it, and the re-check button', () => {
  const {run}=setup(PREVIEW);
  run('BOOT_PREVIEW='+JSON.stringify(PREVIEW));
  const card=run(`bootCardHtml(findMachine('${CLONER3}'))`);
  assert.match(card,/class="bootcard warn" id="boot-card"/);
  assert.match(card,/באתחול הבא<span class="x">נבדק עכשיו · לא נספר כאתחול<\/span>/);
  assert.match(card,/<div class="big">תפריט בחירה<\/div>/);
  assert.match(card,/המחשב ימתין לאדם\. הוא לא יעלה לבד, ולא יקבל את הסבב הפתוח\./);
  assert.match(card,/למה: השרת שלח אותו לסוכן 3 פעמים ברצף והוא חזר בלי להתקדם, אז הסבב לא נמסר לו יותר\./);
  assert.match(card,/סבב פתוח כרגע: Office 2024 — סטנדרט\. משכפל 1 מצטרף אליו\./);
  assert.match(card,/שרת כבוי או PXE שנכשל בקושחה: המחשב ינסה את סדר האתחול הרגיל שלו\./);
  assert.match(card,/onclick="refreshBootPreview\('78%3Aac%3Ac0%3A9b%3A11%3Ac3'\)">בדיקה מחדש<\/button>/);
});

test('#2550 the drawer carries the card at the top of the overview, before "מצב עכשיו"', () => {
  const {run}=setup(PREVIEW);
  run('BOOT_PREVIEW='+JSON.stringify(PREVIEW));
  const html=run(`machineDrawerHtml(findMachine('${CLONER3}'))`);
  assert.ok(html.indexOf('id="boot-card"')>0 && html.indexOf('id="boot-card"')<html.indexOf('מצב עכשיו'));
});

test('#2550 everything from the server is escaped', () => {
  const {run}=setup(PREVIEW);
  run('BOOT_PREVIEW='+JSON.stringify({checked_at:null,machines:{[BUILD]:{decided:true,kind:'task',guard:null,task:{type:'capture',name:'<img src=x onerror=alert(1)>'}}}}));
  const html=run('machinesTableHtml()')+run(`bootCardHtml(findMachine('${BUILD}'))`);
  assert.doesNotMatch(html,/<img src=x/); assert.match(html,/&lt;img src=x/);
});

test('#2550 one call for the whole room: loading the machines reads the preview once, and a failure leaves "not read", not the last answer', async () => {
  const {run,requests,fixtures}=setup(PREVIEW);
  await run('loadMachines()');
  assert.equal(requests.filter((k)=>k==='/machines/boot-preview').length,1,'one call, not one per machine');
  assert.equal(run('Object.keys(BOOT_PREVIEW.machines).length'),4);
  fixtures['/machines/boot-preview']=new Error('boom');
  await run('loadMachines()');
  assert.equal(run('BOOT_PREVIEW'),null,'a failed read must not keep showing the old answer as if it were current');
  assert.equal(run(`bootView(findMachine('${CLONER2}'))`).title,'לא ידוע');
});

test('#2550 "בדיקה מחדש" is the same single call, and a failed one says so', async () => {
  const {run,requests,fixtures}=setup(PREVIEW);
  run('BOOT_PREVIEW='+JSON.stringify(PREVIEW));
  await run(`(async () => { await loadBootPreview(); })()`);
  assert.equal(requests.length,1); assert.equal(requests[0],'/machines/boot-preview');
  fixtures['/machines/boot-preview']=new Error('boom');
  await run('loadBootPreview()');
  assert.equal(run('BOOT_PREVIEW'),null);
});

test('#2550 the status poll refreshes the preview only on the machines page and not more often than the interval', () => {
  const src=consoleJs();
  const poll=src.slice(src.indexOf('function startStatusWatch()'),src.indexOf('/* ‏#1255: פאנל'));
  assert.match(poll,/current === "machines" && Date\.now\(\) - BOOT_PREVIEW_AT > BOOT_PREVIEW_EVERY_MS\) loadBootPreview\(\)/);
  assert.match(src,/const BOOT_PREVIEW_EVERY_MS = 10000;/);
});

// #2682: the server's answer is not understood (bad schema) on a distributing card -> the agent's failure screen (#2666).
// The preview must say so -- not "דיסק מקומי" (what decide() said before the flag) and not "לא ידוע".
test('#2682 the failure screen is named: a warning, with the button, and not "local disk" or "unknown"', () => {
  const {run}=setup(PREVIEW);
  const item={decided:true,kind:'failure',code:'answer-failure-screen',action:'agent',role:'build',guard:null,task:null,session:null};
  run('BOOT_PREVIEW='+JSON.stringify({checked_at:new Date().toISOString(),guard_limit:3,machines:{[BUILD]:item}}));
  const v=run(`bootView(findMachine('${BUILD}'))`);
  assert.equal(v.title,'מסך כישלון'); assert.equal(v.cls,'warn');
  assert.doesNotMatch(v.title+v.line+v.lead,/דיסק מקומי|לא ידוע/);
  const card=run(`bootCardHtml(findMachine('${BUILD}'))`);
  assert.match(card,/class="bootcard warn"/); assert.match(card,/אתחל עכשיו/); assert.match(card,/ההגנה מלולאה \(#75\)/);
});
