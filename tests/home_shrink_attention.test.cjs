// ‏#1253 (נמדד על ברזל 10/10, קליטה tsk_749e מ-HP-BUILD): "דיסק מכווץ — לא
// הוחזר לגודלו" הופיע ב"דורש טיפול" בזמן שהקליטה עוד רצה — והדיסק יוחזר
// בסופה. שלושה מצבים, כל אחד בשם:
//   * קליטה פעילה של הרשומה — לא ב"דורש טיפול"; ב"מה קורה עכשיו" ליד הקליטה.
//   * הקליטה הסתיימה/נכשלה והרשומה פתוחה — האזהרה הקיימת, מילה במילה.
//   * רשימת הקליטות לא נקראה — "לא נבדק", לא הסתרה (עיקרון 5).
// בלי דפדפן: console.js רץ ב-vm מול DOM מזערי ו-fetch מזויף, כמו home_overview.
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const today=new Date().toISOString().slice(0,10);
const MAC='cc:cc:cc:cc:cc:cc';

function capture(over={}) {
  return {id:'tsk_749e',type:'capture',mac:MAC,machine:'HP-BUILD',name:'Vm',disk:'sdb',folder:'Win',state:'running',
    bytes_written:50.8e9,bytes_total:null,source_progress:null,error:null,created_at:today+'T13:25:59Z',...over};
}
function record(over={}) {
  return {id:7,mac:MAC,port:2,serial:'S5Y2NX0R',task_id:'tsk_749e',image_name:'Vm',opened_at:today+'T13:26:36Z',...over};
}

function setup(tasks, records) {
  const nodes=new Map();
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const fixtures={
    '/overview':{images:1,machines:1,storage:null,session:null,room:null,pulls:[]},
    '/tasks':tasks,'/net':[],'/ports':[],'/health':[],'/journal':[],'/update':{},
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false})},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url)=>{
      const key=url.replace('/api/console','').split('?')[0];
      if(fixtures[key] instanceof Error) throw fixtures[key];
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] || {}};
    }});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx);
  for (const f of consoleFiles()) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",version:"v0.60.0",capabilities:{}}; current="home"; toast=()=>{};');
  run(`IMAGES=[];FOLDERS=[];DISK_FAILURES=[];MACHINES=${JSON.stringify([{mac:MAC,suffix:'HP-BUILD'}])};SHRINK_RECORDS=${JSON.stringify(records)}`);
  return {run,fixtures};
}
async function home(tasks, records=[record()]) {
  const s=setup(tasks, records);
  await s.run('loadHome()');
  // ‏#1300: "מה קורה עכשיו" עבר מהסיכום ללשונית "משימות" — שתיהן נבדקות
  return {...s, html:s.run('home(0)')+s.run('home(1)')};
}
const attention=html=>{
  const at=html.indexOf('id="home-attn"');
  if(at<0) return html.slice(0,html.indexOf('<div class="kpi '));   // "אין פריטים לטיפול" / "לא נקראו"
  return html.slice(at,html.indexOf('</details>',html.indexOf('</ul>',at)));
};
const nowCard=html=>html.slice(html.indexOf('<span>משימות'));

test('#1253: a capture that is still running is not "needs attention" — its shrunk disk is named next to it in "now"',async()=>{
  for(const state of ['running','pending']) {
    const {html}=await home([capture({state})]);
    assert.doesNotMatch(attention(html),/דיסק מכווץ/,`${state}: the disk will be restored at the end of the capture`);
    assert.match(nowCard(html),/קליטת אימג&#39; — Vm<\/span><span class="sub">[^<]*<bdi dir="ltr">sdb<\/bdi>[^]*?דיסק 2 מכווץ — יוחזר בסוף הקליטה/,state);
  }
});

test('#1253: a capture that finished or failed with the record still open keeps the existing warning, word for word',async()=>{
  for(const state of ['done','failed','cancelled']) {
    const {html}=await home([capture({state})]);
    // ‏#1300: הפעולה — כפתור בעמודה נפרדת, לא קישור בתוך הטקסט
    assert.match(attention(html),/<b>דיסק מכווץ<\/b> — HP-BUILD · דיסק 2 כווץ לקליטה ולא הוחזר לגודלו\.<\/div><button class="btn sm flat" onclick="selectPageById\('machines'\)">למחשבים<\/button>/,state);
    assert.doesNotMatch(html,/יוחזר בסוף הקליטה/,state);
  }
});

test('#1253: a record whose capture is not in /tasks at all is the real warning (hiding needs positive evidence)',async()=>{
  const {html}=await home([capture({id:'tsk_other',state:'done'})]);
  assert.match(attention(html),/<b>דיסק מכווץ<\/b> — HP-BUILD · דיסק 2 כווץ לקליטה ולא הוחזר לגודלו/);
});

test('#1253: an open record from an older capture is not hidden by a new capture on the same machine',async()=>{
  const {html}=await home([capture({id:'tsk_new',state:'running'}),capture({state:'failed'})]);
  assert.match(attention(html),/<b>דיסק מכווץ<\/b> — HP-BUILD · דיסק 2 כווץ לקליטה ולא הוחזר לגודלו/);
  assert.doesNotMatch(html,/יוחזר בסוף הקליטה/);
});

test('#1253: a record without task_id (opened before the field) is matched by an active capture on the same machine',async()=>{
  const legacy=record({task_id:null});
  let {html}=await home([capture({id:'tsk_x',state:'running'})],[legacy]);
  assert.doesNotMatch(attention(html),/דיסק מכווץ/);
  assert.match(nowCard(html),/דיסק 2 מכווץ — יוחזר בסוף הקליטה/);
  ({html}=await home([capture({id:'tsk_x',state:'running',mac:'dd:dd:dd:dd:dd:dd'})],[legacy]));
  assert.match(attention(html),/<b>דיסק מכווץ<\/b> — HP-BUILD · דיסק 2 כווץ לקליטה ולא הוחזר לגודלו/);
});

test('#1253: /tasks that could not be read is "not checked" — never hidden, never the "forgotten" verdict (principle 5)',async()=>{
  const s=setup(new Error('tasks down'),[record()]);
  await s.run('loadHome()');
  const html=s.run('home(0)');
  const card=attention(html);
  assert.match(card,/<b>דיסק מכווץ — לא נבדק<\/b> — HP-BUILD · דיסק 2 כווץ לקליטה\. רשימת הקליטות לא נקראה/);
  assert.doesNotMatch(card,/ולא הוחזר לגודלו/,'"forgotten" is a verdict we could not reach');
  assert.doesNotMatch(html,/יוחזר בסוף הקליטה/);
});

test('#1253: a /tasks read that fails after a good one is "not checked" again, and a good read clears it',async()=>{
  const s=setup([capture()],[record()]);
  await s.run('loadHome()');
  assert.doesNotMatch(attention(s.run('home(0)')),/דיסק מכווץ/);
  s.fixtures['/tasks']=new Error('tasks down');
  await s.run('loadHome()');
  assert.match(attention(s.run('home(0)')),/דיסק מכווץ — לא נבדק/,'stale CAPTURE_TASKS is not evidence the capture still runs');
  s.fixtures['/tasks']=[capture()];
  await s.run('loadHome()');
  const html=s.run('home(0)');
  assert.doesNotMatch(attention(html),/דיסק מכווץ/);
  assert.doesNotMatch(html,/לא נקראו: משימות/,'a good read clears the old error');
});
