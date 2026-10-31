// ‏#705: שירותים לכל כרטיס — המצבים של המוקאפ המאושר (nicperm.html, נדב 13/10):
// table · drawer · drawer-dist · warn · warn-table · lockout-last · lockout · handoff · pending ·
// reverted · kept · new · new-drawer. אותה רתמה של network_page.test.cjs: הקבצים האמיתיים
// רצים ב-vm מול DOM מזערי ו-fetch מזויף. עיקרון 5: /net/services שלא נקרא = "לא נקרא".
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

const nic=(name,extra={})=>({name,mac:'00:50:56:a1:07:'+name.slice(-2),state:'up',present:true,addresses:[],enabled:false,proxy:false,trunk:false,
  range_start:'',range_end:'',netmask:'255.255.255.0',gateway:'',dns:[],lease:'12h',server_ip:'',description:'',speed_mbps:1000,
  dhcp_live:{state:'off',checked:true,detail:''},dhcp_live_label:'כבוי',dhcp_diverged:false,...extra});
const NICS=[
  nic('ens33',{addresses:['10.10.10.8/24'],description:'רשת השרתים'}),
  nic('ens34',{addresses:['10.44.0.1/24'],description:"כיתות בניין א'",enabled:true,server_ip:'10.44.0.1',dhcp_live:{state:'serving',checked:true,detail:''},dhcp_live_label:'משרת'}),
  nic('ens35',{state:'down',speed_mbps:null}),
];
const cfgRow=(name,extra={})=>({name,mode:'static',mode_he:'כתובת סטטית',address:'',netmask:'255.255.255.0',gateway:'',dns:[],routes:[],present:true,state:'up',live_addresses:[],mismatches:[],...extra});
const NETCFG={interfaces:[cfgRow('ens33',{live_addresses:['10.10.10.8/24']}),cfgRow('ens34',{live_addresses:['10.44.0.1/24']}),cfgRow('ens35',{mode:'manual',state:'down'})],
  live:{checked:true,reason:'',routes:[],nameservers:[]},management_interface:'ens33',sourced:true,resolv_path:'/etc/resolv.conf',
  rollback:{armed:true,armed_detail:'active',unit:'imagesrv-netrollback.timer',window_seconds:60,pending:false,interface:'',seconds_left:0,expired:false,corrupt:false}};
const L=(o={})=>({console:null,monitor:null,multicast:null,netboot:null,interserver:null,...o});
const svcRow=(name,services,extra={})=>({name,present:true,address:null,services,source:'default',configured:services.length>0,unreadable:false,listening:L(),...extra});
const TODAY={services:['console','monitor','multicast','netboot','interserver'],edited:false,window_seconds:60,pending:null,last:null,
  you:{interface:'ens33',address:'10.10.10.8',peer:'10.10.10.51'},
  nics:[svcRow('ens33',['console','monitor','interserver'],{address:'10.10.10.8',listening:L({console:true,monitor:true,interserver:true,netboot:false})}),
        svcRow('ens34',['multicast','netboot'],{address:'10.44.0.1',listening:L({console:false,monitor:false,netboot:true,interserver:false})}),
        svcRow('ens35',[])]};
const OFFICE=JSON.parse(JSON.stringify(TODAY));
OFFICE.nics[2]={...svcRow('ens35',['console','monitor'],{address:'10.20.0.8',source:'saved'})};
const PORTS=[{id:'http_console',name:'HTTP',port:'8081',proto:'tcp',state:'ok',detail:'',enabled:true,bind:'10.10.10.8',toggle:'confirm',off_means:'x'},
             {id:'interserver',name:'mTLS',port:'8443',proto:'tcp',state:'ok',detail:'',enabled:true,bind:'10.10.10.8',toggle:'api',off_means:'x'}];

function setup(over={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},className:'',style:{removeProperty(){},setProperty(){}},
      classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},hasAttribute(){return false;},getAttribute(){return null;},closest(){return {style:{}};},focus(){},scrollIntoView(){}});
    return nodes.get(key);
  }
  const fixtures={'/net/interfaces':NICS,'/net/config':NETCFG,'/net':[],'/ports':PORTS,'/monitor/machines':[],'/storage-nodes':[],'/sessions':[],
    '/net/interfaces/ens34/leases':{interface:'ens34',checked:true,reason:'',leases:[]},'/net/services':TODAY,...over};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    location:{protocol:'https:'},sessionStorage:{getItem:()=>null,setItem(){}},navigator:{},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; const method=options.method||'GET';
      requests.push({url:key,method,body:options.body?JSON.parse(options.body):null});
      const f=fixtures[method+' '+key]!==undefined?fixtures[method+' '+key]:fixtures[key];
      if(f instanceof Error) return {status:f.status||500,ok:false,headers:{get:()=>null},json:async()=>({detail:f.message})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>f ?? {ok:true}};
    }});
  for(const f of ['progress.js','console.js','net.js','netcfg.js','nicsvc.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME='+JSON.stringify({username:'nadav',role:'admin',server_name:'שרת',version:'v0.72.0',tls:{fingerprint_sha256:'aa'},capabilities:{enroll_secondary:false,classrooms:false}})
    +'; globalThis.sheets=[]; globalThis.toasts=[]; sheet = o => { sheets.push(o); }; toast=(m)=>{ toasts.push(m); }; renderCurrent=()=>{ globalThis.rendered=(globalThis.rendered||0)+1; };'
    +' globalThis.drawers=[]; openDrawer=(t,b,o)=>{ drawers.push({t,b,o}); setDrawerBody(b); }; globalThis.modalOpen=0; openModalFrame=()=>{ modalOpen++; };');
  return {run,requests,fixtures,ctx,node};
}
async function loaded(over) { const t=setup(over); t.run('current="network"'); await t.run('loadNetwork()'); return t; }
const row=(html,needle)=>{const i=html.indexOf(needle); assert.ok(i>=0,'missing row: '+needle); const s=html.lastIndexOf('<tr',i); return html.slice(s,html.indexOf('</tr>',i)+5);};
const drawer=(t)=>t.node('#drawerBody').innerHTML;
function balanced(html) {
  const stack=[]; const voids=new Set(['input','br','hr','img','meta','link','source','wbr','path','rect','circle']);
  for(const m of html.matchAll(/<\/?([a-z][\w-]*)\b[^>]*>/gi)) {
    const tag=m[1].toLowerCase();
    if(m[0].endsWith('/>') || voids.has(tag)) continue;
    if(m[0].startsWith('</')) assert.equal(stack.pop(),tag,'Unbalanced '+m[0]); else stack.push(tag);
  }
  assert.deepEqual(stack,[]);
}

test('table: a "שירותים" column with chips in the fixed order; your NIC is marked; an unset NIC says "לא הוגדר · הכול כבוי"',async()=>{
  const t=await loaded();
  const html=t.run('networkPage(1)'); balanced(html);
  assert.match(html,/<th>תפקיד<\/th><th>שירותים <span class="ns-help">· מה השרת נותן דרכו<\/span><\/th><th><span class="sr">פעולות/);
  const r33=row(html,'data-nic="ens33"'), r34=row(html,'data-nic="ens34"'), r35=row(html,'data-nic="ens35"');
  assert.match(r33,/<small><i><\/i>החיבור שלך<\/small>/);
  assert.deepEqual([...r33.matchAll(/class="ns-sc"[^>]*>.*?<\/svg>([^<]+)</g)].map(m=>m[1]),['קונסולה','מוניטור','בין-שרתים']);
  assert.deepEqual([...r34.matchAll(/class="ns-sc"[^>]*>.*?<\/svg>([^<]+)</g)].map(m=>m[1]),['שידור','אתחול מהרשת']);
  assert.match(r35,/<span class="ns-sc unset">לא הוגדר · הכול כבוי<\/span>/);
  assert.match(r33,/onclick="nicsvcRowClick\(event,'ens33'\)"/,'the row opens the drawer');
  const menu=JSON.parse(r33.match(/data-menu="([^"]*)"/)[1].replaceAll('&quot;','"').replaceAll('&#39;',"'"));
  assert.deepEqual(menu[0],['שירותים…',"nicsvcOpen('ens33')"],'"שירותים…" is the first item of ⋯');
});

test('/net/services unread → "לא נקרא" in the column and a note, never "אף שירות"',async()=>{
  const t=await loaded({'/net/services':new Error('db locked')});
  const html=t.run('networkPage(1)');
  assert.match(row(html,'data-nic="ens33"'),/<span class="muted" title="db locked">לא נקרא<\/span>/);
  assert.match(html,/\/net\/services לא נקרא: db locked/);
  assert.doesNotMatch(html,/אף שירות/);
});

test('drawer (ens33) + lockout-last: the console is locked on the last NIC that serves it; trying says why',async()=>{
  const t=await loaded();
  t.run("nicsvcOpen('ens33')");
  assert.equal(t.run('drawers[0].t'),'שירותים — ens33');
  assert.match(t.run('drawers[0].o.sub'),/רשת השרתים · 10\.10\.10\.8 · ניהול/);
  let d=drawer(t); balanced(d);
  assert.match(d,/אתה מחובר דרך הכרטיס הזה<\/b> — מהמחשב <span class="ns-addr">10\.10\.10\.51<\/span> לכתובת <span class="ns-addr">10\.10\.10\.8<\/span>/);
  assert.match(d,/<label class="n-sw" aria-disabled="true">.*?<input id="ns-console" type="checkbox" role="switch" checked aria-disabled="true"/);
  assert.match(d,/זה הכרטיס היחיד עם קונסולה, ולכן היא נעולה כאן/);
  assert.match(d,/<span class="pt">8081\/tcp<\/span>/,'the port comes from /ports');
  assert.match(d,/פעיל — השרת מאזין כאן/,'listening=true from the sockets');
  assert.match(d,/id="nsSave" onclick="nicsvcSave\(\)" disabled>שמירה והחלה</);
  t.run("nicsvcToggle('console',{checked:false,getAttribute:()=> 'true'})");
  d=drawer(t);
  assert.match(d,/class="ns-msg bad" role="alert">.*אי אפשר לכבות כאן/);
  assert.equal(t.run("NICSVC_DRAFT.draft.has('console')"),true,'the switch did not move');
});

test('drawer-dist (ens34) + warn: an odd combination is an orange warning, not a block',async()=>{
  const t=await loaded();
  t.run("nicsvcOpen('ens34')");
  assert.match(drawer(t),/<input id="ns-multicast" type="checkbox" role="switch" checked/);
  assert.match(drawer(t),/פעיל — השרת משדר כאן/);
  t.run("nicsvcOpen('ens33')");
  t.run("nicsvcToggle('netboot',{checked:true,getAttribute:()=>null})");
  const d=drawer(t);
  assert.match(d,/class="ns-msg" role="status">.*צירוף חריג.<\/b> ברשת השרתים כבר יש DHCP של המכללה/);
  assert.match(d,/יופעל בשמירה/); assert.match(d,/ישתנה בשמירה:<\/b><ul><li>אתחול מהרשת — יופעל/);
  assert.match(d,/id="nsSave" onclick="nicsvcSave\(\)" >שמירה והחלה</,'still savable');
});

test('save on a NIC you are not cutting: one PUT, a toast, and a reload',async()=>{
  const t=await loaded({'PUT /net/services/ens35':{ok:true}});
  t.run("nicsvcOpen('ens35')");
  t.run("nicsvcToggle('console',{checked:true,getAttribute:()=>null})");
  await t.run('nicsvcSave()');
  const put=t.requests.find(r=>r.method==='PUT');
  assert.equal(put.url,'/net/services/ens35'); assert.deepEqual(put.body,{services:['console']});
  assert.match(t.run('toasts.at(-1)'),/נשמר — ens35 נותן עכשיו: קונסולה/);
  assert.equal(t.run('sheets.length'),0,'no confirmation when you are not on it');
});

test('warn-table: a saved odd combination is an orange chip and a note with "פתיחת השירותים…"',async()=>{
  const v=JSON.parse(JSON.stringify(TODAY)); v.nics[0].services=['console','monitor','netboot','interserver']; v.nics[0].source='saved'; v.edited=true;
  const t=await loaded({'/net/services':v});
  const html=t.run('networkPage(1)');
  assert.match(row(html,'data-nic="ens33"'),/<span class="ns-sc w" title="ברשת השרתים כבר יש DHCP[^"]*">.*?אתחול מהרשת<span class="sr"> — צירוף חריג<\/span>/);
  assert.match(html,/<b>ens33 נותן אתחול מהרשת ברשת השרתים.<\/b> ברשת השרתים כבר יש DHCP.*onclick="nicsvcOpen\('ens33'\)">פתיחת השירותים…/);
});

test('lockout + handoff: console off on the NIC you are on → red confirm (no typed word) → PUT with handoff → the handoff screen',async()=>{
  const out={ok:true,changed:true,nic:'ens33',services:['monitor','interserver'],pending:{interface:'ens33',seconds_left:60,window_seconds:60},go:['https://10.20.0.8:8081']};
  const t=await loaded({'/net/services':OFFICE,'PUT /net/services/ens33':out});
  t.run("nicsvcOpen('ens33')");
  assert.doesNotMatch(drawer(t),/aria-disabled/,'another NIC serves the console — not locked');
  t.run("nicsvcToggle('console',{checked:false,getAttribute:()=>null})");
  const d=drawer(t);
  assert.match(d,/אתה מחובר דרך הכרטיס הזה.<\/b> אחרי השמירה תתנתק, ותצטרך לאשר תוך 60 שניות מ-<span class="ns-addr">10\.20\.0\.8<\/span>/);
  assert.match(d,/>שמירה והחלה…</);
  await t.run('nicsvcSave()');
  assert.equal(t.requests.filter(r=>r.method==='PUT').length,0,'nothing is sent before the confirmation');
  const s=t.run('sheets[0]');
  assert.equal(s.title,'לכבות את הקונסולה ב-ens33?'); assert.equal(s.submitLabel,'לכבות ולהתנתק'); assert.equal(s.danger,true);
  assert.equal(s.verify,undefined,'#1270: no typed word');
  assert.match(s.note,/תתנתק ברגע ההחלה/); assert.match(s.note,/יש לך 60 שניות<\/b> להיכנס/); assert.match(s.note,/https:\/\/10\.20\.0\.8:8081/);
  assert.match(s.note,/השרת מחזיר לבד את ההגדרה הקודמת, והקונסולה חוזרת ל-ens33/);
  const next=await t.run('sheets[0].onSubmit({})');
  const put=t.requests.find(r=>r.method==='PUT');
  assert.deepEqual(put.body,{services:['monitor','interserver'],handoff:true});
  t.ctx.__next=next; t.run('__next()');
  const hand=t.node('#sheet').innerHTML; balanced(hand);
  assert.match(hand,/השינוי הוחל. החיבור הזה מתנתק./);
  assert.match(hand,/<a class="btn primary" id="nsGo" href="https:\/\/10\.20\.0\.8:8081">/);
  assert.match(hand,/class="ns-ring big"[^>]*role="timer" aria-label="נשארו 60 שניות"/);
  assert.match(hand,/והקונסולה חוזרת ל-ens33, בכתובת <span class="ns-addr">10\.10\.10\.8<\/span>/);
  assert.equal(t.run('modalOpen'),1);
});

test('pending (from the other address): a banner on every page + a countdown chip on the row; keep / revert',async()=>{
  const v=JSON.parse(JSON.stringify(OFFICE)); v.you={interface:'ens35',address:'10.20.0.8',peer:'10.20.0.51'}; v.edited=true;
  v.nics[0].services=['monitor','interserver']; v.nics[0].source='saved';
  v.pending={interface:'ens33',previous:null,applied:['monitor','interserver'],seconds_left:47,window_seconds:60,corrupt:false,apply_error:null};
  v.pending.previous=['console','monitor','interserver'];
  const t=await loaded({'/net/services':v,'POST /net/services/confirm':{ok:true,interface:'ens33'}});
  await t.run('nicsvcRefresh()');       // הפס נצבע שוב בכל דגימה — עכשיו עם תיאור הכרטיס
  const banner=t.node('#nicPend').innerHTML; balanced(banner);
  assert.match(banner,/שינוי ברשת ממתין לאישור שלך — חוזר בעוד <span data-ns-cd>47<\/span> שנ׳/);
  assert.match(banner,/קונסולה כובתה ב-ens33 \(רשת השרתים\)/);
  assert.match(banner,/onclick="nicsvcKeep\(\)">השאר את השינוי<\/button><button class="btn" type="button" onclick="nicsvcUndo\(\)">החזר עכשיו/);
  assert.match(banner,/aria-live="polite"/,'announced at 30 and 10 seconds only');
  assert.match(row(t.run('networkPage(1)'),'data-nic="ens33"'),/<span class="ns-sc w">.*קונסולה כובתה · <span data-ns-cd>47<\/span> שנ׳<\/span>/);
  await t.run('nicsvcKeep()');
  assert.ok(t.requests.some(r=>r.method==='POST'&&r.url==='/net/services/confirm'));
  assert.match(t.run('toasts.at(-1)'),/השינוי נשמר — הקונסולה כבויה ב-ens33. נכנסים מ-10\.20\.0\.8/);
});

test('reverted: an info banner that says why, closable; kept: nothing lingers',async()=>{
  const v=JSON.parse(JSON.stringify(TODAY)); v.last={event:'reverted',interface:'ens33',reason:'expired',reason_he:'לא אושר בזמן',errors:[],at:new Date().toISOString()};
  const t=await loaded({'/net/services':v});
  const banner=t.node('#nicPend').innerHTML;
  assert.match(banner,/class="ns-pend info" role="status">.*השינוי ברשת חזר לבד<\/b><p>לא אושר תוך 60 שניות, ולכן השרת החזיר את ההגדרה הקודמת: הקונסולה פעילה שוב ב-ens33, בכתובת <span class="ns-addr">10\.10\.10\.8<\/span>/);
  assert.match(banner,/aria-label="סגירה" onclick="nicsvcDismiss/);
  v.last={...v.last,event:'kept'};
  const k=await loaded({'/net/services':v});
  assert.equal(k.node('#nicPend').innerHTML,'');
  const f=JSON.parse(JSON.stringify(TODAY)); f.last={event:'reverted',interface:'ens33',reason:'apply_failed',errors:['חומת אש: nft -c'],at:new Date().toISOString()};
  const failed=await loaded({'/net/services':f});
  assert.match(failed.node('#nicPend').innerHTML,/class="ns-pend bad".*ההחלה נכשלה, ולכן ההגדרה הקודמת חזרה.*nft -c/,'a failed apply is red and names the error');
});

test('new + new-drawer: a connected NIC that was never set — a note, a tinted row, and a drawer with everything off',async()=>{
  const nics=NICS.map(n=>n.name==='ens35'?{...n,state:'up',speed_mbps:1000}:n);
  const t=await loaded({'/net/interfaces':nics});
  const html=t.run('networkPage(1)');
  assert.match(html,/<b>כרטיס מחובר שלא הוגדר: ens35<\/b> — קישור 1 Gb. השרת לא מאזין עליו לשום דבר.*onclick="nicsvcOpen\('ens35'\)">בחירת שירותים…/);
  assert.match(row(html,'data-nic="ens35"'),/class="ns-new"/);
  t.run("nicsvcOpen('ens35')");
  const d=drawer(t);
  assert.match(d,/כרטיס שלא הוגדר — הכול כבוי.<\/b> זו ברירת המחדל הבטוחה/);
  assert.equal((d.match(/role="switch" checked/g)||[]).length,0,'all off');
  assert.equal((d.match(/כבוי — השרת לא (מאזין|משדר) כאן/g)||[]).length,6);   // + NTP (#1773)
});

test('measured, not assumed: a service on in the setting that is not bound says so',async()=>{
  const v=JSON.parse(JSON.stringify(TODAY)); v.nics[1].listening.netboot=false;
  const t=await loaded({'/net/services':v});
  t.run("nicsvcOpen('ens34')");
  assert.match(drawer(t),/מוגדר פעיל — אבל השרת לא מאזין כאן כרגע/);
});
