// Local renderer/adapter checks. No browser or server dependencies.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=process.env.CONSOLE_STATIC_ROOT || path.resolve(__dirname,'../server/static');
function setup() {
  const nodes=new Map(), requests=[], intervals=new Map(); let intervalId=0;
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_position,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const fixtures={
    '/overview':{images:1,machines:1,storage:{total_bytes:1024,free_bytes:512},session:{id:'session',group_id:'grp_class',prefix:'LAB',image_name:'Image',state:'open',joined:1,expected_clients:2,roster:['aa','bb'],members:[{mac:'aa',hostname:'LAB-01',state:'running',bytes_written:50,bytes_total:100}]},pulls:[]},
    '/images':[{id:'image',name:'Image',folder:'Folder',description:'Description',total_compressed_bytes:1024,partitions:2}],
    '/folders':[{name:'Folder',description:'Test',images:1}],
    '/groups':[{id:'grp_class',label:'Class',role:'classroom',machines:1},{id:'grp_BUILD',role:'build',label:'Build',machines:1}],
    '/machines':[{mac:'aa',suffix:'01',group_id:'grp_class'},{mac:'cc',suffix:'Builder',group_id:'grp_BUILD'}],
    '/net':[], '/tasks':[], '/journal/events':[{event:'capture',label:'Capture'}], '/journal':[],
    '/settings':{recovery_require_login:'true',session_wait_seconds:'60',console_idle_seconds:'300'},
    '/users':[{username:'admin',role:'admin',created_at:'2026-09-13'}],
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open:(...args)=>requests.push({open:args})},
    localStorage:{getItem:()=>null,setItem(){},removeItem(){}},CSS:{escape:x=>x},
    setInterval:fn=>{intervals.set(++intervalId,fn);return intervalId},clearInterval:id=>intervals.delete(id),setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      if(fixtures[key] instanceof Error) throw fixtures[key];
      return {status:200,ok:true,headers:{get:()=>key==='/journal'?'true':null},json:async()=>fixtures[key] || {}};
    }});
  vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../server/static/progress.js'),'utf8'),ctx);
  vm.runInContext(fs.readFileSync(path.join(root,'console.js'),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={timezone:"UTC",username:"admin",role:"admin",idle_seconds:300,capabilities:{interbranch_transfer:true,classrooms:true}};');
  for(const [name,key] of [['OVERVIEW','/overview'],['IMAGES','/images'],['FOLDERS','/folders'],['MACHINES','/machines'],['GROUPS','/groups']]) run(name+'='+JSON.stringify(fixtures[key]));
  return {ctx,run,node,requests,fixtures,intervals};
}
function balanced(html) {
  const stack=[]; const voids=new Set(['input','br','hr','img','meta','link','source','wbr']);
  for(const m of html.matchAll(/<\/?([a-z][\w-]*)\b[^>]*>/gi)) {
    const tag=m[1].toLowerCase();
    if(m[0].endsWith('/>') || voids.has(tag)) continue;
    if(m[0].startsWith('</')) assert.equal(stack.pop(),tag,'Unbalanced '+m[0]); else stack.push(tag);
  }
  assert.deepEqual(stack,[],'Unclosed tags');
}
test('all populated page renderers produce balanced fragments',()=>{
  const {run}=setup();
  run('USERS=[{username:"admin",role:"admin",created_at:"2026-09-13"}]; SETTINGS={recovery_require_login:"true",session_wait_seconds:"60",console_idle_seconds:"300"}; LOG.rows=[{ts:"2026-09-13T10:00:00",user:"admin",event:"login",label:"L",text:"T"}];');
  for(const name of ['home','images','deploy','machines','settings','logs','permissions']) {
    const html=run(name+'()'); assert.ok(html.length>50,name); balanced(html);
    assert.doesNotMatch(html,/<script|<\/html|id="modal"/,name+' must be a fragment');
  }
});
test('capture sheet submits the existing endpoint and selected folder',async()=>{
  const {run,requests}=setup(); run('sheet = options => { globalThis.formOptions=options; }; IMAGES_FOLDER="Folder";');
  await run('openCapture()');
  assert.equal(run('formOptions.fields.find(f=>f.id==="folder").value'),'Folder');
  await run('formOptions.onSubmit({mac:"cc",disk:"sda",name:"Capture",folder:"Folder"})');
  assert.ok(requests.some(r=>r.url==='/api/console/tasks/capture' && JSON.parse(r.options.body).disk==='sda'));
});
// #1251 (Nadav, 10/10): a new image always goes into a folder -- the capture
// sheet offers no "ללא תיקייה", refuses an empty choice before the POST, and
// with no folders at all it says where to create one instead of opening.
test('capture sheet has no "no folder" option and refuses an empty folder (#1251)',async()=>{
  const {run,requests}=setup(); run('sheet = options => { globalThis.formOptions=options; }; IMAGES_FOLDER=null; FOLDERS=null;');
  await run('openCapture()');
  const opts=run('JSON.stringify(formOptions.fields.find(f=>f.id==="folder").options)');
  assert.doesNotMatch(opts,/ללא תיקייה/);
  assert.deepEqual(JSON.parse(opts).map(o=>o.value),['','Folder'],'placeholder, then the folders read from /folders (FOLDERS was not loaded)');
  assert.equal(run('formOptions.fields.find(f=>f.id==="folder").value'),'');
  await assert.rejects(run('formOptions.onSubmit({mac:"cc",disk:"sda",name:"Capture",folder:""})'),/תיקיית יעד/);
  assert.ok(!requests.some(r=>r.url==='/api/console/tasks/capture'),'no POST without a folder');
});
test('capture with no folders at all says where to create one (#1251)',async()=>{
  const {run,fixtures}=setup(); fixtures['/folders']=[];
  run('globalThis.formOptions=null; sheet = options => { globalThis.formOptions=options; }; toast=(m)=>{ globalThis.toasted=m; };');
  await run('openCapture()');
  assert.equal(run('formOptions'),null,'no capture sheet without a folder to put the image in');
  assert.match(run('toasted'),/אימג'ים/); assert.match(run('toasted'),/תיקייה/);
});
test('folder creation and editing call existing endpoints',async()=>{
  const {run,requests}=setup(); run('sheet = options => { globalThis.formOptions=options; }; loadImages=async()=>{};');
  run('addFolderSheet()'); await run('formOptions.onSubmit({name:"New",description:"Text"})');
  run('editFolderSheet(FOLDERS[0])'); await run('formOptions.onSubmit({name:"Renamed",description:"Text"})');
  assert.ok(requests.some(r=>r.url==='/api/console/folders' && r.options.method==='POST'));
  assert.ok(requests.some(r=>r.url==='/api/console/folders/Folder' && r.options.method==='PUT'));
});
test('journal filters reach server, inclusive end minute, truncated warning (#954 גל 6: LOG state instead of #jf-* fields)',async()=>{
  const {run,requests}=setup(); run('toast=(m)=>{ globalThis.toasted=(globalThis.toasted||"")+m; }; renderCurrent=()=>{};');
  run("LOG.event='capture';LOG.q='needle';LOG.user='admin';LOG.range='custom';LOG.since='2026-09-01T10:00';LOG.until='2026-09-13T12:00';");
  await run('loadJournalData()'); const r=requests.find(r=>r.url.startsWith('/api/console/journal?'));
  assert.ok(r);const q=new URL(r.url,'http://localhost').searchParams;
  assert.equal(q.get('event'),'capture');assert.equal(q.get('user'),'admin');assert.equal(q.get('q'),'needle');assert.equal(q.get('from'),'2026-09-01T10:00:00');assert.equal(q.get('to'),'2026-09-13T12:00:59');assert.equal(q.get('limit'),'200');
  assert.equal(run('LOG.truncated'),true,'the X-Journal-Search-Truncated header is read');
  assert.match(run('logs()'),/חיפוש חלקי/);
});
test('polling refreshes status, marks failed reads, and stops on logout',async()=>{
  const {run,intervals,fixtures,node}=setup(); run('renderCurrent=()=>{};startStatusWatch();');
  assert.equal(intervals.size,1); await run('refreshStatus()'); assert.equal(run('overviewError'),'');
  fixtures['/overview']=new Error('offline'); await run('refreshStatus()');
  assert.match(run('overviewError'),/מצב השרת לא נקרא/);assert.match(node('.task-summary').textContent,/מצב השרת לא נקרא/);
  run('showLogin()');assert.equal(intervals.size,0);assert.equal(run('ME'),null);
});
test('the class tab of the deploy page (#954 גל 4 — no round drawer) includes absent roster members, errors and SMART',async()=>{
  const {run,fixtures}=setup();
  fixtures['/machines']=[{mac:'aa',suffix:'01'},{mac:'bb',suffix:'02'},{mac:'dd',suffix:'03'}];
  run('OVERVIEW.session.members[0].error="disk error";OVERVIEW.session.members[0].disks=[{disk_number:1,verdict:"fail",decision:"replace"}];');
  await run('sessionClassMachines(OVERVIEW.session.group_id)');const html=run('deploy(1)');
  assert.match(html,/LAB-02/);assert.match(html,/disk error/);assert.match(html,/disk-smart fail/);assert.match(html,/הסבב הבא: 03/);balanced(html);
});
test('deploy role cannot reach admin or capability pages',()=>{
  const {run}=setup();run('ME.role="deploy"');
  for(const id of ['machines','health','settings','branches','permissions','logs','network']) assert.equal(run(`pageAllowed("${id}")`),false,id);
  for(const id of ['home','images','deploy'])assert.equal(run(`pageAllowed("${id}")`),true,id);
  assert.doesNotMatch(run('images()'),/onclick="openCapture/);
  run('ME.role="admin";ME.capabilities.interbranch_transfer=false');assert.equal(run('pageAllowed("branches")'),false);
});
test('machine monitor is MAC-specific and restricted to build/cloner',()=>{
  const {run,requests}=setup();run('monitorMachine("aa");monitorMachine("cc")');
  assert.equal(requests.length,1);assert.match(requests[0].open[0],/monitor.html\?mac=cc&name=Builder/);
});
test('#954 wave 3: machines.js is gone — the machines page lives in console.js and owns its header',()=>{
  const {run}=setup();assert.ok(!fs.existsSync(path.join(root,'machines.js')));
  assert.equal(run('typeof machines'),'function');assert.equal(run('pages.machines.own'),true);assert.equal(run('typeof machineAdminPage'),'undefined');
});
test('unknown storage never displays zero',()=>{
  const {run}=setup();assert.notEqual(run('fmtBytes(null)'),run('fmtBytes(0)'));
  run('OVERVIEW.storage.free_bytes=null');assert.doesNotMatch(run('home()'),/100%|0\.0 GB/);
});
test('toast treats endpoint errors as text',()=>{
  const {run,node}=setup();run('toast("<img src=x onerror=alert(1)>")');assert.equal(node('#toast').innerHTML,'');assert.match(node('#toast').textContent,/<img/);
});
test('existing upload posts tar bytes and reports progress',()=>{
  const {run,ctx,node}=setup();let request;
  ctx.XMLHttpRequest=class {constructor(){request=this;this.upload={};}open(method,url){this.method=method;this.url=url;}setRequestHeader(k,v){this.header=[k,v];}send(file){this.file=file;}};
  run('uploadImage({name:"sample.tar"})');
  assert.equal(request.url,'/api/console/images/upload');assert.equal(request.method,'POST');assert.deepEqual(request.header,['Content-Type','application/x-tar']);
  request.upload.onprogress({lengthComputable:true,loaded:50,total:100});assert.match(node('#modalBody').innerHTML,/50%/);
});
test('existing start and stop retain endpoint payloads',async()=>{
  const {run,node,requests}=setup();run('refreshStatus=async()=>{}');
  await run('startRound()');run('sheet = options => { globalThis.formOptions=options; }; OVERVIEW={session:{id:"session",image_name:"Image"}};');run('stopRound()');assert.equal(run('formOptions.verify'),undefined,'#1270: no typed name');assert.equal(run('formOptions.danger'),true);await run('formOptions.onSubmit({})');
  assert.ok(requests.some(r=>r.url==='/api/console/sessions/session/start'));
  assert.ok(requests.some(r=>r.url==='/api/console/sessions/session/close' && JSON.parse(r.options.body).confirm_name==='Image'));
});
test('every enabled script compiles together without global collisions',()=>{
  const {ctx}=setup();
  // codeql[js/incomplete-multi-character-sanitization] false positive: test code -- the string only feeds assert.match on the repo's own files and is never rendered (#291)
  const html=fs.readFileSync(path.join(root,'index.html'),'utf8').replace(/<!--[\s\S]*?-->/g,'');
  for(const m of html.matchAll(/<script src="([^?]+)\?v=/g)) {
    if(['console.js','progress.js'].includes(m[1]))continue;
    const file=path.join(root,m[1]);
    vm.runInContext(fs.readFileSync(fs.existsSync(file)?file:path.resolve(__dirname,'../server/static',m[1]),'utf8'),ctx);
  }
});
test('all static includes carry the same ?v= and color literals stay in tokens',()=>{
  // #826: a fixed version number fails on every justified bump; the invariant is
  // "one cache-bust version for every include", not "the version is X".
  // codeql[js/incomplete-multi-character-sanitization] false positive: test code -- the string only feeds assert.match on the repo's own files and is never rendered (#291)
  const html=fs.readFileSync(path.join(root,'index.html'),'utf8').replace(/<!--[\s\S]*?-->/g,'');
  const versions=[...html.matchAll(/(?:src|href)="[^"?]+\?v=([^"&]+)/g)].map(m=>m[1]);
  assert.ok(versions.length>=5,'expected the static includes to carry ?v=');
  assert.deepEqual([...new Set(versions)],[versions[0]],'includes disagree on ?v=: '+versions.join(','));
  for(const file of ['console.css','console.js','index.html']) {
    // Comments are not colors: "// #822:" is an Issue reference, not #822.
    // codeql[js/incomplete-multi-character-sanitization] false positive: test code -- the string only feeds assert.match on the repo's own files and is never rendered (#291)
    const s=fs.readFileSync(path.join(root,file),'utf8').replace(/\/\*[\s\S]*?\*\//g,'').replace(/(^|\s)\/\/.*$/gm,'$1').replace(/<!--[\s\S]*?-->/g,'');
    const literals=s.match(/(?:[: ,]\s*#[a-f\d]{3,8}\b|rgba?\(\s*\d)/gi) || [];
    assert.deepEqual(literals,[],file+' contains literal colors');
  }
});

/* #1129 (R43): esc() משמש ב-67 attributes, והסריאליזציה של הדפדפן
   (textContent→innerHTML) ממירה רק &<> — ערך עם " סגר את ה-attribute והזריק
   onfocus=. הבדיקה מריצה את esc() המשוגר על ששת התווים, ועל שם שיתוף SMB
   עוין מסריקת רשת (מקור שאינו בשליטת המפעיל). */
test('#1129: esc() escapes all six characters, not only &<>',()=>{
  const {run}=setup();
  for(const [raw,expected] of [['&','&amp;'],['<','&lt;'],['>','&gt;'],['"','&quot;'],["'",'&#39;'],['`','&#96;']]) {
    assert.equal(run('esc('+JSON.stringify(raw)+')'),expected,'esc('+JSON.stringify(raw)+')');
  }
  assert.equal(run('esc(null)'),''); assert.equal(run('esc(undefined)'),''); assert.equal(run('esc(7)'),'7');
  const share='share"><img src=x onerror=alert(1)>';
  const html=run('`<input value="${esc('+JSON.stringify(share)+')}">`');
  assert.doesNotMatch(html,/"><img/,'the quote must not close the attribute');
  assert.equal(html,'<input value="share&quot;&gt;&lt;img src=x onerror=alert(1)&gt;">');
});
test("#1129: server ids inside onclick=\"fn('…')\" go through encodeId, which escapes the quote",()=>{
  const {run}=setup();
  const evil="tsk'); alert(1); ('";
  assert.doesNotMatch(run('encodeId('+JSON.stringify(evil)+')'),/'/,'encodeId must not leave a raw quote');
  assert.equal(run('decodeURIComponent(encodeId('+JSON.stringify(evil)+'))'),evil,'round-trip');
  // The two capture-cancel buttons carry the id encoded, not esc()'d (#1266: the SSH port switch is gone).
  const js=fs.readFileSync(path.join(root,'console.js'),'utf8');
  assert.doesNotMatch(js,/onclick="cancelCapture\('\$\{esc\(/);
  assert.doesNotMatch(js,/onclick="portSwitch\('\$\{esc\(/);
  assert.match(js,/cancelCapture\(decodeURIComponent\('\$\{encodeId\(t\.id\)\}'\)\)/);
  const br=fs.readFileSync(path.join(root,'branches.js'),'utf8');
  assert.doesNotMatch(br,/loadBranchMachines\('\$\{esc\(/);
});
