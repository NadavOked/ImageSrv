// ‏#2528 (שלב 1): מחשב שיכפול כבה או אותחל באמצע הסבב — השאלה בעמוד "סבב הפצה" של הקונסולה.
// השרת אינו כותב בלי תשובה (/room.round.interrupt, server/rounds/interrupt.py); כאן נבדק מה שהמפעיל רואה:
// הנוסח והכפתורים באים מהשרת, שני כפתורים בלבד (אין "המשך מאותה נקודה" בשלב 1), הדיסק מוצג לפי המקום
// שבו הוא יושב עכשיו, והתשובה נשלחת מהקונסולה (שהיא של המנהל, #1073) — לא רק ממי שפתח את הסבב. אותה תבנית vm כמו deploy_room_page.test.cjs, מצומצמת.
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const C1='a0:48:1c:8a:18:40';
const GB256=256060514304;

const ASK={machines:[{mac:C1,name:'מחשב 1',disks:[{serial:'S5Y11',size_bytes:GB256,model:'870 EVO',was_port:2,port:2}]}],
  disks:1,answer:null,text_he:'המשכפל מחשב 1 כבה או אותחל באמצע הסבב. דיסק אחד לא הושלם.',
  buttons_he:{rewrite:'כתוב מחדש את הדיסקים שלא הושלמו',close:'סגור את הסבב'},expires_in_s:1500};
const round=(extra={})=>({id:'room_1',image_id:'img_1',image_name:'office365',state:'active',target_drives:2,written_drives:1,remaining_drives:1,
  wave_number:1,wave_state:'closed',ready_drives:0,opened_by:'nadav',source:{kind:'library'},precheck:[],missing:[],interrupt:null,...extra});
const room=(r)=>({round:r,stream_stalled:false,disk_floor:null,machines:[
  {mac:C1,name:'מחשב 1',drawer_count:2,awake:true,drawers:2,fresh_drawers:1,joined:false,state:null,bytes_written:0,bytes_total:0,error:null,drawer_list:[]}]});

function setup({r=round({interrupt:ASK}),role='admin',answerStatus=null}={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const fixtures={'/groups':[{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:2,machines:1}],
    '/machines':[{mac:C1,suffix:'מחשב 1',group_id:'grp_CLONERS',note:null,drawer_count:2,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null}],
    '/net':[],'/disk-failures':[],'/images':[],'/overview':{images:0,machines:1,storage:null,pulls:[],room:r,session:null},'/room':room(r),
    '/room/interrupt':answerStatus?{__status:answerStatus,detail:'אין שאלה פתוחה על הסבב הזה'}:{ok:true}};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Error,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      const v=fixtures[key];
      if(v&&v.__status) return {status:v.__status,ok:false,json:async()=>({detail:v.detail})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>v ?? {ok:true}};
    }});
  for(const f of ['progress.js',...consoleFiles(),'net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run(`ME={username:"${role}",role:"${role}",server_name:"srv",version:"v0.93.0",capabilities:{classrooms:false}}; current="deploy"; toast=(m)=>{ globalThis.toasted=m; }; renderCurrent=()=>{};`);
  for(const [name,key] of [['OVERVIEW','/overview'],['MACHINES','/machines'],['GROUPS','/groups'],['DISK_FAILURES','/disk-failures'],['NET','/net'],['ROOM','/room'],['IMAGES','/images']]) run(name+'='+JSON.stringify(fixtures[key]));
  return {run,requests};
}
const note=(html)=>html.slice(html.indexOf('כבה או אותחל באמצע הסבב')-200).split('</div></span></div>')[0];

test('an open question: the server\'s sentence, the disk where it sits now, and exactly the two stage-1 buttons', () => {
  const {run}=setup();
  const html=run('deploy(0)');
  assert.match(html,/<b>המשכפל מחשב 1 כבה או אותחל באמצע הסבב\. דיסק אחד לא הושלם\.<\/b> שום דיסק לא נכתב עד שתהיה תשובה\./);
  const n=note(html);
  assert.match(n,/מחשב 1 · דיסק 2 · <bdi dir="ltr">S5Y11<\/bdi> — לא הושלם/);
  assert.match(n,/<button type="button" class="btn primary" onclick="answerInterrupt\('rewrite'\)">כתוב מחדש את הדיסקים שלא הושלמו<\/button><button type="button" class="btn danger" onclick="answerInterrupt\('close'\)">סגור את הסבב<\/button>/);
  assert.equal((n.match(/<button/g)||[]).length,2,'two buttons, no third');
  assert.doesNotMatch(html,/המשך מאותה נקודה/,'"continue from the same point" is stage 4, not here');
  assert.match(n,/בלי תשובה הסבב ייסגר בעוד ~25 דק', ושום דבר לא ייכתב\./);
  assert.match(html,/<span class="pill warn">ממתין להכרעה<\/span>/,'the header says the round waits for a decision');
});

test('a disk that moved to another drawer is shown where it is now; one that is not connected says so', () => {
  const moved={...ASK,machines:[{...ASK.machines[0],disks:[{serial:'S5Y11',was_port:2,port:1},{serial:'S5Y12',was_port:3,port:null}]}]};
  const html=setup({r:round({interrupt:moved})}).run('deploy(0)');
  assert.match(html,/מחשב 1 · דיסק 1 \(היה בדיסק 2\) · <bdi dir="ltr">S5Y11<\/bdi> — לא הושלם/);
  assert.match(html,/מחשב 1 · אינו מחובר עכשיו · <bdi dir="ltr">S5Y12<\/bdi> — לא הושלם/);
});

test('while the wave still runs for other machines the note says the clock starts when it ends', () => {
  const html=setup({r:round({wave_state:'running',interrupt:{...ASK,wave_running:true,expires_in_s:2100}})}).run('deploy(0)');
  assert.match(html,/הגל עוד רץ במחשבים אחרים, והתשובה תיושם כשהוא יסתיים\. בלי תשובה בתוך ~35 דק' אחרי סוף הגל, הסבב ייסגר ושום דבר לא ייכתב\./);
  assert.match(html,/answerInterrupt\('rewrite'\)/,'it can already be answered');
});

test('no question, no note: a round without an interrupt is drawn as before', () => {
  const html=setup({r:round({interrupt:null,wave_state:'open',wave_number:2})}).run('deploy(0)');
  assert.doesNotMatch(html,/answerInterrupt|כבה או אותחל|ממתין להכרעה/);
});

test('an answer given while the wave still runs: a quiet line, no buttons', () => {
  const html=setup({r:round({wave_state:'running',interrupt:{...ASK,answer:'rewrite',expires_in_s:null}})}).run('deploy(0)');
  assert.match(html,/התשובה התקבלה \("כתוב מחדש את הדיסקים שלא הושלמו"\) — תיושם כשהגל הנוכחי יסתיים\./);
  assert.doesNotMatch(html,/answerInterrupt/);
});

test('a role that does not operate rounds sees the question without the buttons, and cannot answer', async () => {
  const {run,requests}=setup({role:'viewer'});
  const html=run('deploy(0)');
  assert.match(html,/כבה או אותחל באמצע הסבב/); assert.doesNotMatch(html,/answerInterrupt/);
  await run(`answerInterrupt('rewrite')`);
  assert.ok(!requests.some(x=>x.url==='/api/console/room/interrupt'),'nothing is posted');
});

test('the console answers, whoever opened the round: the click posts the answer and reloads the room', async () => {
  for(const [role,choice,said] of [['admin','rewrite',/ייכתבו מחדש/],['admin','close',/הסבב נסגר/]]) {
    const {run,requests}=setup({role});
    await run(`answerInterrupt('${choice}')`);
    const sent=requests.find(x=>x.url==='/api/console/room/interrupt');
    assert.ok(sent,'the answer goes to /room/interrupt'); assert.equal(sent.options.method,'POST');
    assert.deepEqual(JSON.parse(sent.options.body),{answer:choice,machines:[C1]},'the answer names the machines the screen showed');
    assert.match(run('toasted'),said);
    assert.ok(requests.some(x=>x.url.startsWith('/api/console/room')&&x!==sent&&(!x.options.method||x.options.method==='GET')),'the room is read again');
  }
});

test('someone else answered first (409): the operator is told, nothing is claimed', async () => {
  const {run}=setup({answerStatus:409});
  await run(`answerInterrupt('rewrite')`);
  assert.match(run('toasted'),/התשובה לא נרשמה: אין שאלה פתוחה על הסבב הזה/);
});

test('the text is escaped: a machine name or serial cannot inject markup', () => {
  const evil={...ASK,text_he:'<img src=x onerror=1>',machines:[{mac:C1,name:'<b>x</b>',disks:[{serial:'"><script>',was_port:1,port:1}]}]};
  const html=setup({r:round({interrupt:evil})}).run('deploy(0)');
  assert.doesNotMatch(html,/<img src=x|<b>x<\/b>|"><script>/);
});
