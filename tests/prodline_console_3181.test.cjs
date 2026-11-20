// ‏#3181 (נדב 14/11): פס ייצור בקונסולה — המעבר האוטומטי בחלון "התחל סבב" (בלי מתג), שם לכל דיסק שביקשו
// (`queue`), ועיצוב r1 בזמן ההפצה: שורה ניטרלית עם תג "SMART תקין" לפני הסבב, צבע מלא רק ל"אפשר להוציא"
// ול"הושלם", וכל תג מצב באותו גודל (‏.pl-tag).
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {consoleJs}=require('./console_src.cjs');
const root=path.resolve(__dirname,'../server/static');
const GB=256060514304;

const MACS=['aa:bb:cc:00:00:01','aa:bb:cc:00:00:02','aa:bb:cc:00:00:03'];
const SLOTS=['sata:1','sata:2','sata:3'];
const {ruled}=require('./disk_rule_fixture.cjs');   // #3666: the disk arrives with the server's rule; the console decides nothing
const drawer=(mac,port,extra={})=>ruled({dev:'sd'+'abc'[port-1],port,serial:`S${mac.slice(-1)}${port}`,model:'870 EVO',size_bytes:GB,fresh:true,
  state:null,error:null,bytes_written:0,bytes_total:0,smart:'ok',...extra});

function setup(round=null, drawers=null) {
  const node=(key)=>({value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{},classList:{add(){},remove(){},toggle(){},contains:()=>false},
    setAttribute(){},getAttribute(){return null;},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],focus(){}});
  const machines=MACS.map((mac,i)=>({mac,name:`משכפל ${i+1}`,drawer_count:3,drawer_slots:SLOTS,awake:true,
    drawer_list:(drawers&&drawers[mac])||[1,2,3].map((p)=>drawer(mac,p))}));
  const room={round,disk_floor:null,clone_names:{pad:2,instructor:'INS',max_len:15},machines};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,RegExp,Error,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:()=>node('?'),querySelectorAll:()=>[],getElementById:()=>node('#'),
      createElement:()=>node('new'),documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},removeEventListener(){},body:{appendChild(){}}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({ok:true})})});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx); vm.runInContext(consoleJs(),ctx);
  const run=(code)=>vm.runInContext(code,ctx);
  run(`ME={username:"labtech",role:"deploy",server_name:"srv",version:"v0",capabilities:{}}; current="deploy"; renderCurrent=()=>{}; toast=()=>{}; loadDeploy=async()=>{}; closeModal=()=>{}; openRoomRound=()=>{};`);
  const reg=machines.map((m)=>({mac:m.mac,suffix:m.name,group_id:'grp_CLONERS',drawer_count:3,drawer_slots:SLOTS,disks:m.drawer_list,prompt:null}));
  run(`ROOM=${JSON.stringify(room)}; MACHINES=${JSON.stringify(reg)}; IMAGES=${JSON.stringify([{id:'img_1',name:'office365',folder:'',os:'windows',min_target_bytes:60e9,partitions:4,expand_partitions:[]}])}; DEPLOY.form.image='img_1';`);
  return run;
}
const pickAll=(run)=>run(`${JSON.stringify(MACS.flatMap((m)=>[1,2,3].map((p)=>`${m}@${p}`)))}.forEach(k=>{DEPLOY.form.sel[k]=true;}); 0`);

test('9 drawers and 9 disks is a regular round; 9 and 10 is a production line, with no switch', () => {
  const run=setup(); pickAll(run);
  assert.equal(run('plDrawers(roomMachines())'),9);
  run(`DEPLOY.form.target="9"`); assert.equal(run('plIsLine(roomMachines())'),false);
  run(`DEPLOY.form.target="10"`); assert.equal(run('plIsLine(roomMachines())'),true);
});

test('a production line names every disk asked: the marked ones, then the queue', () => {
  const run=setup(); pickAll(run);
  run(`DEPLOY.form.target="11"; nmSet('on',true); nmForm().prefix='LAB'`);
  assert.equal(run('nmProblem(roomMachines())'),'','"one wave only" is not the reason in a production line');
  const choice=JSON.parse(run('JSON.stringify(nmChoice(roomMachines()))'));
  assert.equal(choice.hostnames.names.length,9);
  assert.deepEqual(choice.hostnames.queue,['LAB-10','LAB-11']);
  const html=run('roomNamesBlock(roomMachines(), false)');
  assert.ok(html.includes('<td><span class="tg m">בתור</span></td>'),'the queue rows are in the preview (a chip since the approved window mockup, #3181)');
});

test('a regular round with more disks than marked (and not more than drawers) keeps the one-wave refusal', () => {
  const run=setup();
  run(`${JSON.stringify(MACS.flatMap((m)=>[1,2].map((p)=>`${m}@${p}`)))}.forEach(k=>{DEPLOY.form.sel[k]=true;}); 0`);
  run(`DEPLOY.form.target="9"; nmSet('on',true); nmForm().prefix='LAB'`);
  assert.equal(run('plIsLine(roomMachines())'),false);
  assert.match(run('nmProblem(roomMachines())'),/גל אחד/);
  assert.equal(JSON.parse(run('JSON.stringify(nmChoice(roomMachines()))')).hostnames,undefined);
});

function lineRound(over={}) {
  const disks=MACS.flatMap((mac,i)=>[1,2,3].map((p,j)=>({seq:i*3+j,name:`LAB-0${i*3+j+1}`,mac,port:p,serial:`S${mac.slice(-1)}${p}`,state:'writing',changed_at:new Date().toISOString()})));
  disks.push({seq:9,name:'LAB-10',mac:null,port:null,serial:null,state:'queued',changed_at:null});
  return {id:'room_1',state:'active',wave_state:'open',wave_number:1,target_drives:10,written_drives:0,remaining_drives:10,
    target_slots:MACS.map((mac)=>({mac,ports:[1,2,3]})),throughput_bps:null,
    production_line:{drawers:9,target:10,written:0,remaining:10,next_name:'LAB-10',queue_left:1,disks,decisions:[],...over}};
}

test('before the round a healthy disk is a neutral row with a small "SMART תקין" tag', () => {
  const run=setup(lineRound());
  const html=run('plBody(roomMachines(), roomRound())');
  assert.ok(html.includes('<span class="pl-tag t-ok"><i></i>SMART תקין</span>'),html);
  assert.ok(!html.includes('class="rdy"'),'no row is painted before the round');
  assert.ok(html.includes('נכתבו 0 מתוך 10 שמות · נשארו 10'));
});

test('a full row colour only for "אפשר להוציא" and "הושלם", and every state tag is the same .pl-tag', () => {
  const r=lineRound(); r.wave_state='running';
  r.production_line.disks[0].state='removable'; r.production_line.written=1; r.production_line.remaining=9;
  const live={[MACS[0]]:[drawer(MACS[0],1,{state:'done',bytes_written:100,bytes_total:100}),drawer(MACS[0],2,{state:'writing',bytes_written:40,bytes_total:100}),
    drawer(MACS[0],3,{state:'failed',error:'I/O error',error_short:'שגיאת כתיבה'})]};
  const run=setup(r,live);
  const html=run('plBody(roomMachines(), roomRound())');
  const rows=[...html.matchAll(/<tr( class="rdy")?><td>מגירה[\s\S]*?<\/tr>/g)].map((m)=>m[0]);
  const rdy=rows.filter((x)=>x.startsWith('<tr class="rdy">'));
  assert.equal(rdy.length,1,'only the removable row is painted');
  assert.ok(rdy[0].includes('אפשר להוציא'));
  assert.ok(rows.some((x)=>x.includes('<span class="pl-tag t-w"><i></i>כותב</span>')));
  assert.ok(rows.some((x)=>x.includes('<span class="pl-tag t-bad"><i></i>נכשל</span>')));
  const table=html.slice(html.indexOf('<table class="pl-t">'),html.indexOf('</table>'));
  assert.ok(!/class="(pill|lb-chip|chip)\b/.test(table),'no tag of another size in the table');
  assert.ok(html.includes('<span>מוכן להוצאה</span><span class="pl-tag t-ok"><i></i>1</span>'));
});

test('an emptied drawer waits with the next name', () => {
  const r=lineRound(); r.wave_state='running';
  r.production_line.disks[0].state='removed';
  r.production_line.disks[9]={...r.production_line.disks[9],mac:MACS[0],port:1,state:'reserved'};
  const live={[MACS[0]]:[drawer(MACS[0],2,{state:'writing'}),drawer(MACS[0],3,{state:'writing'})]};
  const run=setup(r,live);
  const html=run('plBody(roomMachines(), roomRound())');
  assert.ok(html.includes('<span class="pl-tag t-mut"><i></i>ממתין לדיסק</span>'),html);
  assert.ok(html.includes('הבא: <bdi dir="ltr">LAB-10</bdi>'));
});

test('a shadow decision of the brain says it was recorded, not done', () => {
  const run=setup(lineRound({decisions:[{at:new Date().toISOString(),mode:'shadow',rule:'K5',reason:'חסרות מעט (30 חתיכות): השלמה משכנה אחת'}]}));
  const html=run('plBody(roomMachines(), roomRound())');
  assert.ok(html.includes('חסרות מעט (30 חתיכות): השלמה משכנה אחת'));
  assert.ok(html.includes('צל — נרשם, לא בוצע'));
});

// #3181 (writer E): a cloner whose disks are all finished, with names left, waits for the next disk — and says since when
test('a cloner that waits for the next disk says "ממתין לדיסק" with the time, in its own row and in the size of every tag', () => {
  const r=lineRound({waiting:[{mac:MACS[1],since_s:754}]}); r.wave_state='running';
  const html=setup(r)('plBody(roomMachines(), roomRound())');
  const groups=[...html.matchAll(/<tr class="g">[\s\S]*?<\/tr>/g)].map((m)=>m[0]);
  assert.equal(groups.length,3,html);
  assert.match(groups[1],/<span class="pl-tag t-mut"><i><\/i>ממתין לדיסק · 12 דק[^<]{1,6}<\/span><\/td><\/tr>$/);   // 754 s = 12 minutes; the apostrophe is escaped
  assert.ok(!groups[0].includes('ממתין לדיסק')&&!groups[2].includes('ממתין לדיסק'),'only the cloner that said it waits');
  // the row's own rule for its small grey text ("3 מגירות") does not reach the tag: one size for every state tag
  const css=fs.readFileSync(path.join(root,'console.css'),'utf8');
  assert.ok(css.includes('.pl-t tr.g td span:not(.pl-tag){'),'the group row shrinks its counter, never a tag');
  // a time the server did not say is not shown as zero; and a server that says nothing of waiting draws no tag at all
  const bare=lineRound({waiting:[{mac:MACS[0],since_s:null}]}); bare.wave_state='running';
  assert.ok(setup(bare)('plBody(roomMachines(), roomRound())').includes('<span class="pl-tag t-mut"><i></i>ממתין לדיסק</span></td></tr>'));
  const old=lineRound(); old.wave_state='running';
  assert.ok(!setup(old)('plBody(roomMachines(), roomRound())').includes('ממתין לדיסק'));
});

// #3064 (R5, the approved mockup): a returned drawer that still completes is "מצטרף ומשלים" with how much is missing -- in a tag of the same size
test('a completing drawer says "מצטרף ומשלים" with the count, the share and the source, and only what was said', () => {
  const r=lineRound(); r.wave_state='running';
  const live={[MACS[0]]:[
    drawer(MACS[0],1,{state:'writing',bytes_written:62,bytes_total:100,rejoin:{missing:1393,chunks:3000,part:2,step:'neighbor'}}),
    drawer(MACS[0],2,{state:'writing',bytes_written:40,bytes_total:100,rejoin:{missing:5,chunks:null,part:null,step:null}}),
    drawer(MACS[0],3,{state:'writing',bytes_written:40,bytes_total:100,rejoin:null})]};
  const html=setup(r,live)('plBody(roomMachines(), roomRound())');
  const rows=[...html.matchAll(/<tr( class="rdy")?><td>מגירה[\s\S]*?<\/tr>/g)].map((m)=>m[0]);
  const join=rows.filter((x)=>x.includes('מצטרף ומשלים'));
  assert.equal(join.length,2,'two drawers complete, the third writes as it did');
  assert.ok(join[0].includes('<span class="pl-tag t-w"><i></i>מצטרף ומשלים</span>'),'the tag of every state: .pl-tag');
  assert.ok(join[0].includes('משלים מחיצה')&&join[0].includes('1,393')&&join[0].includes('46% מהמחיצה')&&join[0].includes('ממגירה שכנה'),join[0]);
  const note=join[1].slice(join[1].lastIndexOf('<td'));   // the note cell: the row's own bar says "40%" for the bytes written, and that is not the share of the partition
  assert.ok(note.includes('משלים · חסרות')&&!note.includes('%')&&!note.includes('מחיצה'),'no whole, no share; no part, no partition: '+join[1]);
  assert.ok(rows.some((x)=>x.includes('<span class="pl-tag t-w"><i></i>כותב</span>')&&!x.includes('מצטרף')),'a drawer without rejoin writes');
  // an old server that sends no `rejoin` at all: the row is as it was
  const old=setup(r,{[MACS[0]]:[drawer(MACS[0],1,{state:'writing',bytes_written:62,bytes_total:100})]})('plBody(roomMachines(), roomRound())');
  assert.ok(!old.includes('מצטרף ומשלים'));
});

test('B22: a failed disk keeps its name -- the row says so, not "back in the queue"', () => {
  const r=lineRound(); r.wave_state='running';
  r.production_line.disks[2].state='failed';
  const live={[MACS[0]]:[drawer(MACS[0],1,{state:'writing',bytes_written:40,bytes_total:100}),drawer(MACS[0],2,{state:'writing',bytes_written:40,bytes_total:100}),
    drawer(MACS[0],3,{state:'failed',error:'I/O error',error_short:'שגיאת כתיבה'})]};
  const html=setup(r,live)('plBody(roomMachines(), roomRound())');
  assert.ok(html.includes('השם') && html.includes('LAB-03') && html.includes('נשמר לדיסק'), 'the failed row names the name it keeps');
  assert.ok(!html.includes('חזר לתור'));
});
