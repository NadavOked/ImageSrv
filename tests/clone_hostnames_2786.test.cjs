// ‏#2786 (נדב 09/11, המוקאפ המאושר ב-PR #2788): "שמות מחשב" בחלון "התחל סבב" ולשונית ההגדרות.
// הסדר: משכפל לפי המספר בשמו, ובתוכו SATA 0, 1, 2. תבנית (קידומת, ‏INS למרצה, ‏01…) או פרטני.
// חסימות: ארוך מ-15, תו אסור, כולו ספרות, שדה ריק. תו אסור לא נכנס לשדה בכלל (נדב 09/11), והקידומת
// מוגבלת מראש ל-15 פחות הסיומת הארוכה ביותר של הסבב (‏-INS, ‏-29, ‏-120). הכלל עצמו מושווה לשרת
// ב-test_clone_hostnames_2786.py (‏cloneNameRefusal מול hostname_rule.computer_name_refusal).
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {consoleJs}=require('./console_src.cjs');   // #2594: the console's files in load order
const root=path.resolve(__dirname,'../server/static');
const GB=256060514304;

const C2='aa:bb:cc:00:00:02', C10='aa:bb:cc:00:00:10';
const drawer=(dev,port,serial)=>({dev,port,serial,model:'870 EVO',size_bytes:GB,fresh:true,state:null,error:null,bytes_written:0,bytes_total:0,smart:'ok'});
const DUAL={id:'img_d',name:'Win+Ubuntu',folder:'',sort:2,os:'windows',min_target_bytes:60e9,partitions:4,
  expand_partitions:[{index:3,role:'windows',size_bytes:1,expandable:true},{index:4,role:'linux',size_bytes:1,expandable:true}]};
const PLAIN={id:'img_1',name:'office365',folder:'',sort:1,os:'windows',min_target_bytes:60e9,partitions:4,
  expand_partitions:[{index:3,role:'windows',size_bytes:1,expandable:true}]};

function setup({cloneNames={pad:2,instructor:'INS',max_len:15}}={}) {
  const nodes=new Map(), requests=[];
  const node=(key)=>{
    if(!nodes.has(key)) {
      const cls=new Set();
      nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{},
        classList:{add:(c)=>cls.add(c),remove:(c)=>cls.delete(c),toggle:(c,on)=>{(on===undefined?!cls.has(c):on)?cls.add(c):cls.delete(c);},contains:(c)=>cls.has(c)},
        setAttribute(){},getAttribute(){return null;},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],focus(){}});
    }
    return nodes.get(key);
  };
  const room={round:null,disk_floor:null,clone_names:cloneNames,machines:[
    // ‏משכפל 10 קודם במערך — הסדר של השמות הוא לפי המספר, לא לפי סדר הדיווח
    {mac:C10,name:'משכפל 10',drawer_count:3,awake:true,drawer_list:[drawer('sda',1,'T1'),drawer('sdb',2,'T2')]},
    {mac:C2,name:'משכפל 2',drawer_count:3,awake:true,drawer_list:[drawer('sda',1,'S1'),drawer('sdb',2,'S2'),drawer('sdc',3,'S3')]}]};
  const machines=room.machines.map((m)=>({mac:m.mac,suffix:m.name,group_id:'grp_CLONERS',drawer_count:3,disks:m.drawer_list,prompt:null}));
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,RegExp,Error,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:()=>node('?'),querySelectorAll:()=>[],getElementById:(id)=>node('#'+id),
      createElement:()=>node('new'),documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},removeEventListener(){},body:{appendChild(){}}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{ requests.push({url,options}); return {status:200,ok:true,headers:{get:()=>null},json:async()=>({ok:true})}; }});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx); vm.runInContext(consoleJs(),ctx);
  const run=(code)=>vm.runInContext(code,ctx);
  run(`ME={username:"admin",role:"admin",server_name:"srv",version:"v0",capabilities:{}}; current="deploy"; renderCurrent=()=>{}; toast=(m)=>{globalThis.toasted=m;}; loadDeploy=async()=>{}; closeModal=()=>{}; openRoomRound=()=>{};`);
  run(`ROOM=${JSON.stringify(room)}; MACHINES=${JSON.stringify(machines)}; IMAGES=${JSON.stringify([PLAIN,DUAL])}; DEPLOY.form.image='img_1';`);
  return {run,node,requests,ctx};
}
const pick=(run,keys)=>run(`${JSON.stringify(keys)}.forEach(k=>{DEPLOY.form.sel[k]=true;}); 0`);
const names=(run)=>JSON.parse(run('JSON.stringify(nmNames(roomMachines()).map(r=>r.name))'));
const order=(run)=>JSON.parse(run('JSON.stringify(nmNames(roomMachines()).map(r=>machineName(r.x.m)+"/SATA "+(r.x.d.port-1)))'));
const ALL=[`${C10}@2`,`${C10}@1`,`${C2}@3`,`${C2}@1`];

test('the order: the number in the cloner name first (2 before 10), then SATA 0, 1, 2 inside it', () => {
  const {run}=setup(); pick(run,ALL);
  assert.deepEqual(order(run),['משכפל 2/SATA 0','משכפל 2/SATA 2','משכפל 10/SATA 0','משכפל 10/SATA 1']);
});

test('off is the default: one line that says the name in the image stays, and nothing is sent', async () => {
  const {run,requests}=setup(); pick(run,ALL);
  const html=run('roomNewWindow(roomMachines())');
  assert.match(html,/<div class="full nm" id="nm"><div class="nm-h"><h5>שמות מחשב<\/h5><label class="n-sw nm-sw"><input type="checkbox" role="switch" id="nm-on" aria-label="לכתוב שמות מחשב חדשים"[^>]*><span class="track"><\/span><\/label><span class="sub">כבוי: כל מחשב נשאר עם השם שבאימג'<\/span><\/div><\/div>/);
  await run('openRoomRoundSubmit()');
  const body=JSON.parse(requests.find((r)=>r.url==='/api/console/room'&&r.options.method==='POST').options.body);
  assert.equal('hostnames' in body,false,'the body is the one from before the Issue');
});

test('template with an instructor: 100-INS, then 01 on; the preview, the rule line and the footer say it', () => {
  const {run,node}=setup(); pick(run,ALL);
  run(`nmSet('on',true); nmSet('ins',true); nmPrefixInput(Object.assign(document.getElementById('nm-prefix'),{value:'100'}))`);
  assert.deepEqual(names(run),['100-INS','100-01','100-02','100-03']);
  const html=run('roomNewWindow(roomMachines())');
  assert.ok(html.includes('<tr><td>משכפל 2</td><td><b>0</b> <span class="muted">· דיסק 1</span></td><td><span class="lbl ins">100-INS</span><small class="insn">מרצה</small></td></tr>'),html);
  assert.ok(html.includes('מספור התלמידים: <bdi dir="ltr">01–03</bdi>'));
  assert.ok(html.includes('<small id="nm-count">· 4 דיסקים</small>'));
  assert.ok(html.includes('אם <bdi dir="ltr">100-01</bdi> ייכשל, <bdi dir="ltr">100-02</bdi> נשאר עם שמו'),'the failed disk keeps its number');
  run('roomFooterSync(roomMachines())');
  assert.match(node('#modalHint').textContent,new RegExp(' · שמות: ⁦100-INS⁩ עד ⁦100-03⁩$'));
  assert.equal(node('#modalOk').disabled,false);
});

test('template without an instructor: 7735-30-01 to the last; padding from the settings', () => {
  const {run}=setup(); pick(run,ALL);
  run(`nmSet('on',true); nmForm().prefix='7735-30'`);
  assert.deepEqual(names(run),['7735-30-01','7735-30-02','7735-30-03','7735-30-04']);
  const p3=setup({cloneNames:{pad:3,instructor:'TEACH',max_len:15}}); pick(p3.run,ALL);
  p3.run(`nmSet('on',true); nmSet('ins',true); nmForm().prefix='LAB'`);
  assert.deepEqual(names(p3.run),['LAB-TEACH','LAB-001','LAB-002','LAB-003']);
});

test('the prefix is capped while typing: 15 minus the longest ending of this round, with a counter', () => {
  const {run}=setup(); pick(run,ALL);
  run(`nmSet('on',true); nmSet('ins',true)`);
  // ‏"-INS" (4) ארוך מ-"-03" (3): 11 תווים לקידומת
  assert.match(run('roomNewWindow(roomMachines())'),/id="nm-prefix" dir="ltr" maxlength="11"/);
  run(`nmPrefixInput(Object.assign(document.getElementById('nm-prefix'),{value:'ABCDEFGHIJK'}))`);   // 11 + "-INS" = 15
  assert.equal(run('nmProblem(roomMachines())'),'');
  assert.equal(run('nmLeftText(roomMachines())'),'נשארו 0 תווים');
  run(`nmForm().prefix='ABCDEFGHIJKL'; nmRefresh()`);   // 12 — הודבק לפני ש"מרצה" סומן, למשל
  assert.equal(run('nmProblem(roomMachines())'),'עם ⁦-INS⁩ השם יהיה 16 תווים — עד 15');
  assert.equal(run('nmPrefixWhy(roomMachines())'),'עם ⁦-INS⁩ השם יהיה 16 תווים');
  assert.equal(run('nmForm().prefix'),'ABCDEFGHIJKL','not shortened in silence');
});

test('the longest ending: -29 for 30 disks with an instructor, -100 above 99 students, -INS when longer', () => {
  const {run}=setup();
  const tail=(n,ins,pad=2,suf='INS')=>run(`nmTail(${n},{ins:${ins}},{pad:${pad},suf:${JSON.stringify(suf)}})`);
  assert.equal(tail(30,true),'-INS');
  assert.equal(tail(30,false),'-30');
  assert.equal(tail(101,true),'-100');
  assert.equal(tail(120,false),'-120');
  assert.equal(tail(3,true,2,'TEACHER'),'-TEACHER');
  assert.equal(run('nmPrefixMax(120,{ins:false},{pad:2,suf:"INS"})'),11);
  assert.equal(run('nmPrefixMax(30,{ins:true},{pad:2,suf:"INS"})'),11);
});

test('a character Windows or Linux refuses never enters the field, and the short line says why', () => {
  const {run,node}=setup(); pick(run,ALL);
  run(`nmSet('on',true)`);
  const el=node('#nm-prefix'); el.value='לב 1_0.0é-A';
  run(`nmPrefixInput(document.getElementById('nm-prefix'))`);
  assert.equal(el.value,'100-A'); assert.equal(run('nmForm().prefix'),'100-A');
  assert.equal(node('#nm-chars').innerHTML,'רק אותיות באנגלית, ספרות ומקף');
  assert.equal(node('#nm-chars').classList.contains('bad'),true);
  el.value='100-AB'; run(`nmPrefixInput(document.getElementById('nm-prefix'))`);
  assert.equal(node('#nm-chars').classList.contains('bad'),false,'a clean keystroke clears it');
});

test('per disk: an empty field blocks, an all-digit name blocks, a good one opens', async () => {
  const {run,node,requests}=setup(); pick(run,ALL);
  run(`nmSet('on',true); nmSet('mode','indiv')`);
  const html=run('roomNewWindow(roomMachines())');
  assert.equal((html.match(/<input class="nm-in bad" dir="ltr" maxlength="15" value="" data-key=/g)||[]).length,4,'every row is a field, all marked');
  assert.ok(html.includes('<span class="sub">דיסק בלי שם חוסם את פתיחת הסבב</span>'));
  assert.equal(run('nmProblem(roomMachines())'),'חסר שם ל-4 דיסקים');
  run('roomFooterSync(roomMachines())');
  assert.equal(node('#modalHint').textContent,'חסר שם ל-4 דיסקים. הסבב לא ייפתח');
  assert.equal(node('#modalOk').disabled,true);
  run(`nmForm().indiv=${JSON.stringify({[`${C2}@1`]:'LAB-A-01',[`${C2}@3`]:'LAB-A-02',[`${C10}@1`]:'LAB-B-01',[`${C10}@2`]:'12345'})}`);
  assert.equal(run('nmProblem(roomMachines())'),'השם ⁦12345⁩ כולו ספרות — Windows אינו מקבל שם כזה');
  await run('openRoomRoundSubmit()');
  assert.equal(requests.some((r)=>r.url==='/api/console/room'),false,'blocked: nothing posted');
  run(`nmForm().indiv[${JSON.stringify(`${C10}@2`)}]='LAB-B-02'`);
  await run('openRoomRoundSubmit()');
  const body=JSON.parse(requests.find((r)=>r.url==='/api/console/room').options.body);
  assert.deepEqual(body.hostnames,{names:[
    {mac:C2,port:1,name:'LAB-A-01'},{mac:C2,port:3,name:'LAB-A-02'},{mac:C10,port:1,name:'LAB-B-01'},{mac:C10,port:2,name:'LAB-B-02'}]});
});

test('per disk: typing filters the field and marks it in place', () => {
  const {run,ctx}=setup(); pick(run,ALL);
  run(`nmSet('on',true); nmSet('mode','indiv')`);
  const el={value:'LAB_1 א',classList:{s:new Set(),toggle(c,on){on?this.s.add(c):this.s.delete(c);}},getAttribute:()=>`${C2}@1`,parentNode:null};
  ctx.__el=el; run('nmIndivInput(__el)');
  assert.equal(el.value,'LAB1'); assert.equal(run(`nmForm().indiv[${JSON.stringify(`${C2}@1`)}]`),'LAB1');
  assert.equal(el.classList.s.has('bad'),false);
});

test('more than one wave blocks the names: disks of the next wave would get the same names', () => {
  const {run}=setup(); pick(run,ALL);
  run(`nmSet('on',true); nmForm().prefix='100'; DEPLOY.form.target='8'`);
  assert.match(run('nmProblem(roomMachines())'),/^שמות מחשב רק לסבב של גל אחד/);
});

test('an image with Windows and Linux asks where to write; neither blocks; one is sent', async () => {
  const {run,requests}=setup(); pick(run,ALL);
  run(`DEPLOY.form.image='img_d'; nmSet('on',true); nmForm().prefix='100'`);
  const html=run('roomNewWindow(roomMachines())');
  assert.ok(html.includes(`<div class="nm-os"><b>לכתוב את השם ל:</b><label class="chk"><input type="checkbox" checked onchange="nmSet('windows',this.checked)">Windows</label><label class="chk"><input type="checkbox" checked onchange="nmSet('linux',this.checked)">לינוקס</label>`),html);
  run(`nmSet('windows',false); nmSet('linux',false)`);
  assert.equal(run('nmProblem(roomMachines())'),'סמנו לפחות מערכת הפעלה אחת');
  run(`nmSet('windows',true)`);
  await run('openRoomRoundSubmit()');
  const body=JSON.parse(requests.find((r)=>r.url==='/api/console/room').options.body);
  assert.deepEqual(body.hostnames.os,['windows']);
  const plain=setup(); pick(plain.run,ALL); plain.run(`nmSet('on',true)`);
  assert.doesNotMatch(plain.run('roomNewWindow(roomMachines())'),/nm-os/,'one system: no question');
});

test('a direct round (from a build disk) has no names section', () => {
  const {run}=setup(); pick(run,ALL);
  assert.equal(run('roomNamesBlock(roomMachines(), true)'),'');
});

test('the settings tab: padding 1-4 (2 pressed), the instructor suffix, and the example', () => {
  const {run,node}=setup();
  run(`current="settings"; currentTab=1; SETTINGS={clone_name_pad:"2",clone_name_instructor:"INS"}`);
  const html=run('settings()');
  assert.ok(html.includes('<button type="button" aria-pressed="true" onclick="nmSettingSave(\'clone_name_pad\',\'2\')">2</button>'),html);
  assert.ok(html.includes('id="nm-suf" dir="ltr" maxlength="8" value="INS"'));
  assert.ok(html.includes('<span class="lbl ins">100-INS</span><span class="lbl">100-01</span><span class="lbl">100-02</span>'));
  assert.ok(html.includes('עם ⁦-INS⁩ נשארים לקידומת עד 11 תווים'));
  const el=node('#nm-suf'); el.value='TE-ACH ר';
  run(`nmSufInput(document.getElementById('nm-suf'))`);
  assert.equal(el.value,'TEACH');
  assert.equal(node('#nm-suf-why').textContent,'רק אותיות באנגלית וספרות');
});

test('reopening a failed round sends the same names', async () => {
  const {run,requests}=setup();
  run(`ROOM.round={id:'r1',state:'failed',image_id:'img_1',target_drives:1,target_slots:[{mac:${JSON.stringify(C2)},ports:[1]}],expand_partition:'auto',hostnames:{names:[{mac:${JSON.stringify(C2)},port:1,name:'100-01'}],os:null}}`);
  await run('reopenRoom()');
  const body=JSON.parse(requests.find((r)=>r.url==='/api/console/room').options.body);
  assert.deepEqual(body.hostnames,{names:[{mac:C2,port:1,name:'100-01'}],os:null});
});
