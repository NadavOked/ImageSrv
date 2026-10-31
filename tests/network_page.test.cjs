// ‏#954 גל 8: רשת כאובייקט — תרשים (network.md) · חיבורים פיזיים (network-nics.md) ·
// רשת הפצה (network-deploy.md). בלי דפדפן: console.js + net.js + netcfg.js + progress.js
// רצים ב-vm מול DOM מזערי ו-fetch מזויף (כמו health_ports_page.test.cjs).
// עיקרון 5: "בפועל" ≠ "מוגדר" ≠ "לא נקרא" — כל מקור שנכשל הוא מצב משלו, לא "אין".
// ‏#1263: כרטיס = חלון "הגדרת כרטיס…" (שלושה אופנים), רשת ההפצה = חלון משלה; בלי הקלדת שם — חוץ מכיבוי DHCP (#53).
// ‏#705: "מה מותר על כל וילן" — רק ממה ש-/ports.bind אומר; בלעדיו "דורש API", לא המצאה.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

const C1='a0:48:1c:8a:18:40', C2='78:ac:c0:9b:11:c2', B1='c8:d9:d2:0b:fe:32', UNREG='de:ad:be:ef:00:01';
const ago=(s)=>new Date(Date.now()-s*1000).toISOString();
const nic=(name,extra={})=>({name,mac:'bc:24:11:00:00:'+name.slice(-2),state:'up',present:true,addresses:[],enabled:false,proxy:false,trunk:false,
  range_start:'',range_end:'',netmask:'255.255.255.0',gateway:'',dns:[],lease:'12h',server_ip:'',description:'',speed_mbps:1000,
  dhcp_live:{state:'off',checked:true,detail:''},dhcp_live_label:'כבוי',dhcp_diverged:false,...extra});
const NICS=[
  nic('ens18',{addresses:['10.44.10.1/24'],description:'ניהול'}),
  nic('ens19',{addresses:['10.44.9.10/24'],description:'הפצה',enabled:true,range_start:'10.44.9.100',range_end:'10.44.9.200',server_ip:'10.44.9.10',dns:['10.44.10.2'],
    dhcp_live:{state:'serving',checked:true,detail:''},dhcp_live_label:'משרת'}),
  nic('ens20',{addresses:['10.44.11.2/24'],description:'כיתות',proxy:true,trunk:true,server_ip:'10.44.11.1',dhcp_live:{state:'serving',checked:true,detail:''},dhcp_live_label:'proxy פעיל'}),
  nic('ens21',{state:'down',speed_mbps:null}),
];
const cfgRow=(name,extra={})=>({name,mode:'static',mode_he:'כתובת סטטית',address:'',netmask:'255.255.255.0',gateway:'',dns:[],routes:[],present:true,state:'up',live_addresses:[],mismatches:[],...extra});
const NETCFG={interfaces:[
    cfgRow('ens18',{address:'10.44.10.1',gateway:'10.44.10.254',dns:['10.44.10.2'],live_addresses:['10.44.10.1/24'],routes:[{destination:'10.44.13.0',netmask:'255.255.255.0',gateway:'10.44.10.254'}]}),
    cfgRow('ens19',{address:'10.44.9.10',live_addresses:['10.44.9.10/24']}),
    cfgRow('ens20',{address:'10.44.11.1',live_addresses:['10.44.11.2/24'],mismatches:['כתובת: מוגדר 10.44.11.1/24, בפועל 10.44.11.2/24']}),
    cfgRow('ens21',{mode:'manual',mode_he:'לא מנוהל',state:'down'}),
  ],
  live:{checked:true,reason:'',routes:['default via 10.44.10.254','10.44.13.0/24 via 10.44.10.254'],nameservers:['10.44.10.2']},
  management_interface:'ens18',
  sourced:true,resolv_path:'/etc/resolv.conf',
  rollback:{armed:true,armed_detail:'active',unit:'imagesrv-netrollback.timer',window_seconds:60,pending:false,interface:'',seconds_left:0,expired:false,corrupt:false}};
const NET=[
  {mac:C1,ip:'10.44.9.118',description:'',first_seen:ago(9000),last_seen:ago(10),registered:true,name:'מחשב 1',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',
   boot:{step:'hello',label:'hello · בסוכן',index:9,total:9,at:ago(10),seconds:10,next_step:null,next_label:'',stalled:false}},
  {mac:B1,ip:'10.44.9.20',description:'',first_seen:ago(90000),last_seen:ago(3*3600),registered:true,name:'בנייה 1',group_id:'grp_BUILD',group_label:'מחשבי בנייה',role:'build',boot:null},
  {mac:UNREG,ip:'10.44.9.199',description:'',first_seen:ago(60),last_seen:ago(60),registered:false,name:null,group_id:null,group_label:null,role:null,
   boot:{step:'menu-local',label:'GRUB → דיסק מקומי',index:3,total:9,at:ago(60),seconds:60,next_step:null,next_label:'',stalled:false}},
];
const port=(id,name,p,proto,state,detail,extra={})=>({id,name,port:p,proto,desc:'desc '+id,target:'target '+id,state,detail,note:'',...extra});
const PORTS=[
  port('tftp','TFTP','69','udp','ok','dnsmasq מגיש',{enabled:true,bind:'0.0.0.0',toggle:'confirm',off_means:'x'}),
  port('http_boot','HTTP','8080','tcp','ok','uvicorn',{enabled:true,bind:'0.0.0.0',toggle:'confirm',off_means:'x'}),
  port('http_console','HTTP','8081','tcp','ok','uvicorn',{enabled:true,bind:'10.44.10.1',toggle:'confirm',off_means:'x'}),
  port('multicast','Multicast','9000–9001','udp','off','אין סבב',{enabled:true,bind:['10.44.9.10'],toggle:'none',off_means:'x'}),
  port('interserver','mTLS','8443','tcp','off','כבוי',{enabled:false,bind:'',toggle:'api',off_means:'x'}),
];
const PORTS_NO_BIND=PORTS.map(({bind,...p})=>p);
// ‏#1039: מי מחובר לקונסולה, וחכירות dnsmasq של כרטיס ההפצה (C1 גם ב-/net; LEASED רק בחכירה)
const LEASED='02:00:00:00:00:77';
const SESSIONS=[{user:'nadav',ip:'10.44.10.50',since:ago(3600),last_seen:ago(30)},{user:'tech',ip:'10.44.10.51',since:ago(600),last_seen:ago(120)}];
const LEASES={interface:'ens19',checked:true,reason:'',path:'/var/lib/misc/dnsmasq.leases',leases:[
  {mac:C1,ip:'10.44.9.118',hostname:'cloner1',expires:'2026-09-24T10:00:00+00:00'},{mac:LEASED,ip:'10.44.9.150',hostname:null,expires:null}]};
const MON=[{mac:C1,name:'מחשב 1',role:'cloner',ip:'10.44.9.118',online:true},{mac:C2,name:'מחשב 2',role:'cloner',ip:'10.44.9.59',online:false},{mac:B1,name:'בנייה 1',role:'build',ip:null,online:false}];
const NODES=[{id:'sn1',label:'סניף חיפה',base_url:'https://10.44.13.5:8443',group_id:null,node_id:'sn_1',enrolled_at:'2026-09-10T00:00:00+00:00',disabled_at:null,group_label:null},
             {id:'sn2',label:'סניף ישן',base_url:'https://10.44.10.77:8443',group_id:null,node_id:'sn_2',enrolled_at:'2026-09-01T00:00:00+00:00',disabled_at:'2026-09-02T00:00:00+00:00',group_label:null}];

function setup(over={},me={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},className:'',style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},hasAttribute(){return false;},getAttribute(){return null;},closest(){return {style:{}};},focus(){},scrollIntoView(){}});
    return nodes.get(key);
  }
  const fixtures={'/net/interfaces':NICS,'/net/proxy-support':{read:true,version:'2.91',verified:false,broken:true,reason:'dnsmasq 2.91 — proxy שבור'},
    '/net/config':NETCFG,'/net':NET,'/ports':PORTS,'/monitor/machines':MON,'/storage-nodes':NODES,
    '/storage-nodes/sn1/machines':{connected:false,error:'timeout',machines:[],node_id:'sn_1'},
    '/net/interfaces/ens19/probe':{interface:'ens19',checked:true,servers:[]},
    '/net/interfaces/ens18/probe':{interface:'ens18',checked:false,servers:[]},
    '/net/interfaces/ens20/probe':{interface:'ens20',checked:true,servers:['10.44.11.254']},
    '/sessions':SESSIONS,'/net/interfaces/ens19/leases':LEASES,
    '/machines':[{mac:C1,suffix:'מחשב 1',group_id:'grp_CLONERS',disks:null}],'/groups':[{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:1}],
    ...over};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url:key,method:options.method||'GET',body:options.body?JSON.parse(options.body):null});
      const f=fixtures[key];
      if(f instanceof Error) return {status:500,ok:false,headers:{get:()=>null},json:async()=>({detail:f.message})};
      if(f===403) return {status:403,ok:false,headers:{get:()=>null},json:async()=>({detail:'admin only'})};
      if(f===409) return {status:409,ok:false,headers:{get:()=>null},json:async()=>({detail:'not standalone'})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>f ?? {ok:true,verified:true,apply_error:null}};
    }});
  for(const f of ['progress.js','console.js','net.js','netcfg.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME='+JSON.stringify({timezone:'UTC',username:'nadav',role:'admin',server_name:'שרת המכללה',version:'v0.38.1',capabilities:{enroll_secondary:true,classrooms:true},...me})
    +'; globalThis.sheets=[]; globalThis.toasts=[]; sheet = o => { sheets.push(o); }; toast=(m)=>{ toasts.push(m); }; renderCurrent=()=>{ globalThis.rendered=(globalThis.rendered||0)+1; };'
    +' globalThis.selected=[]; selectPageById=(id)=>{ selected.push(id); current=id; }; openMachineDetail=(m)=>{ globalThis.openedMac=m; }; openAddMachine=(o)=>{ globalThis.addMachine=o; };');
  run('MACHINES='+JSON.stringify(fixtures['/machines'])+'; GROUPS='+JSON.stringify(fixtures['/groups'])+';');
  return {run,requests,fixtures,ctx,node};
}
function balanced(html) {
  const stack=[]; const voids=new Set(['input','br','hr','img','meta','link','source','wbr','path','rect','circle']);
  for(const m of html.matchAll(/<\/?([a-z][\w-]*)\b[^>]*>/gi)) {
    const tag=m[1].toLowerCase();
    if(m[0].endsWith('/>') || voids.has(tag)) continue;
    if(m[0].startsWith('</')) assert.equal(stack.pop(),tag,'Unbalanced '+m[0]); else stack.push(tag);
  }
  assert.deepEqual(stack,[]);
}
// ‏LRI…PDI (U+2066/2069): רצפי ASCII בתוך טקסט ה-SVG מבודדים (bidi()) — הבדיקות משוות בלעדיהם
const plain=(s)=>String(s).replace(/[\u2066\u2069]/g,'');
const unesc=(t)=>t.replaceAll('&quot;','"').replaceAll('&#39;',"'").replaceAll('&#96;','`').replaceAll('&lt;','<').replaceAll('&gt;','>').replaceAll('&amp;','&');
const menuOf=(tr)=>{const m=tr.match(/class="kebab"[^>]*data-menu="([^"]*)"/); assert.ok(m,'row has a ⋯ menu'); return JSON.parse(unesc(m[1]));};
const row=(html,needle)=>{const i=html.indexOf(needle); assert.ok(i>=0,'missing row: '+needle); const s=html.lastIndexOf('<tr',i); return html.slice(s,html.indexOf('</tr>',i)+5);};
const card=(html,title)=>{const i=html.indexOf('<span>'+title); assert.ok(i>=0,'missing card: '+title); const re=/<div class="c(?:12|8|6|4|3) card">/g; let s=0,e=html.length; for(const m of html.matchAll(re)){ if(m.index<i) s=m.index; else { e=m.index; break; } } return html.slice(s,e);};
async function loaded(over,me) { const t=setup(over,me); t.run('current="network"'); await t.run('loadNetwork()'); return t; }

/* ---------- האובייקט והניווט ---------- */

test('network is one own page with four tabs (ports = the wave-5 page); the old nic/netdeploy/network pages are gone',()=>{
  const {run}=setup();
  assert.equal(run('pages.network.own'),true); assert.deepEqual(JSON.parse(run('JSON.stringify(pages.network.tabs)')),['תרשים','חיבורים פיזיים','רשת הפצה','פורטים']);
  assert.equal(run('pages.network.load===loadNetwork'),true);
  assert.equal(run('pages.nic'),undefined); assert.equal(run('pages.netdeploy'),undefined);
  for(const fn of ['renderNicCard','netBannerHtml','nic','netdeploy','saveNetwork','confirmSaveNetwork','loadNetcfgData','loadNetPages','editDeployNic','nicUnion','selectSidebarNic'])
    assert.equal(run(`typeof ${fn}`),'undefined',fn+' should be gone');
  assert.equal(run('networkPage(0)'),run('pagePlaceholder()'),'placeholder before the first read');
  // לשונית "פורטים" מנווטת לדף הקיים; דף הפורטים מצביע חזרה ללשוניות האובייקט
  assert.equal(run('netTabClick(3)'),"selectPageById('ports')"); assert.equal(run('netTabClick(1)'),'openNetwork(1)');
});

test('loadNetwork reads every source once, sets the shared globals for net.js/netcfg.js/ports, and selects the deploy NIC',async()=>{
  const {run,requests,node}=await loaded();
  const urls=requests.map(r=>r.url);
  for(const u of ['/net/interfaces','/net/config','/net','/ports','/monitor/machines','/storage-nodes','/storage-nodes/sn1/machines','/sessions','/net/interfaces/ens19/leases']) assert.equal(urls.filter(x=>x===u).length,1,u);
  assert.ok(!urls.includes('/net/interfaces/ens18/leases'),'leases of the deploy NIC only (#1039)');
  assert.ok(!urls.includes('/storage-nodes/sn2/machines'),'a disabled secondary is not asked');
  assert.equal(run('NETW.nics.length'),4); assert.equal(run('NETCFG.live.checked'),true); assert.equal(run('NET.length'),3);
  assert.ok(!urls.includes('/ssh'),'#1266: no SSH endpoint'); assert.equal(run('NETW.sel'),'ens19','the DHCP NIC is selected by default');
  assert.equal(run('JSON.stringify(NETW.err)'),'{}');
  assert.equal(run('globalThis.rendered'),1);
  // העץ: כרטיס לכל NIC פותח את האובייקט בלשונית 1 עם הכרטיס נבחר
  const tree=node('#nicTree').innerHTML;
  assert.match(tree,/onclick="openNetwork\(1,'ens18',this\)"/); assert.equal((tree.match(/class="inventory-node/g)||[]).length,4);
});

test('openNetwork: from another page selects the network page then the tab; inside the page a NIC from the tree becomes the selection',async()=>{
  const {run}=await loaded();
  run('globalThis.calls=[]; selectPage=(el,id)=>{ calls.push(["page",id]); current=id; currentTab=0; }; activateTab=(i)=>{ calls.push(["tab",i]); currentTab=i; }; markTreeSelection=()=>{};');
  run('current="home"; openNetwork(2)');
  assert.deepEqual(JSON.parse(run('JSON.stringify(calls)')),[['page','network'],['tab',2]]);
  run('calls=[]; current="network"; currentTab=1; rendered=0; openNetwork(1,"ens18")');
  assert.equal(run('calls.length'),0); assert.equal(run('NETW.sel'),'ens18'); assert.equal(run('rendered'),1,'same tab → re-render, not a page reload');
});

test('deploy role: the network object is not allowed and loadNetwork reads nothing',async()=>{
  const {run,requests}=setup({},{role:'deploy',capabilities:{}});
  assert.equal(run('pageAllowed("network")'),false); assert.equal(run('pageAllowed("ports")'),false);
  await run('loadNetwork()');
  assert.equal(requests.length,0);
});

/* ---------- לשונית 0: התרשים ---------- */

test('diagram model: a lane per NIC, VLAN kind by configuration, "allowed" only from /ports.bind, clients placed by subnet with every line state',async()=>{
  const {run}=await loaded();
  const m=plain(run('JSON.stringify(netDiagramModel())')); const model=JSON.parse(m);
  assert.match(run('JSON.stringify(netDiagramModel())'),/\u2066de:ad:be:ef:00:01\u2069/,'ASCII runs are isolated for RTL');
  assert.equal(model.lanes.length,4);
  const lane=(name)=>model.lanes.find(l=>l.nic.name===name);
  assert.equal(lane('ens19').vlan.kind,'deploy'); assert.equal(lane('ens18').vlan.kind,'mgmt'); assert.equal(lane('ens20').vlan.kind,'proxy'); assert.equal(lane('ens21').vlan.kind,'none');
  assert.deepEqual(lane('ens19').allowed,['TFTP 69','HTTP 8080','Multicast 9000–9001'],'bind 0.0.0.0 + the NIC address; 8081 bound elsewhere');
  assert.deepEqual(lane('ens18').allowed,['TFTP 69','HTTP 8080','HTTP 8081']);
  assert.deepEqual(lane('ens21').allowed,['TFTP 69','HTTP 8080'],'no address → only wildcard binds');
  assert.equal(lane('ens19').led,'ok'); assert.equal(lane('ens21').led,'off');
  const kinds=(l)=>l.clients.map(c=>[c.kind,c.led,!!c.dashed]);
  // הפצה: משכפלים (1 מתוך 2 מחוברים = כתום), בנייה בלי כתובת (מקווקו), לא רשום (כתום)
  assert.deepEqual(kinds(lane('ens19')),[['cloner','warn',false],['build','',true],['unreg','warn',false]]);
  assert.match(lane('ens19').clients[0].title,/משכפלים — מחשב 1, מחשב 2/); assert.match(lane('ens19').clients[0].sub,/10\.44\.9\.118 · 10\.44\.9\.59 · 1 מתוך 2 מחוברים/);
  assert.match(lane('ens19').clients[2].title,/לא רשום — de:ad:be:ef:00:01/); assert.match(lane('ens19').clients[2].sub,/10\.44\.9\.199 · GRUB → דיסק מקומי/);
  assert.equal(lane('ens19').unreg,1);
  // ניהול: הקונסולה (ירוק — הדף הזה נטען), סניף חיפה (אדום — לא ענה; הכתובת מחוץ לכל רשת → נופל לניהול), סניף מושבת (אפור מקווקו, לפי הכתובת)
  assert.deepEqual(kinds(lane('ens18')),[['console','ok',false],['branch','err',false],['branch','',true]],'not answering = red solid line; disabled = dashed');
  assert.equal(plain(lane('ens18').clients[0].sub),'2 מחוברים: nadav (10.44.10.50) · tech (10.44.10.51)','#1039: /sessions, not "דורש API"');
  assert.match(plain(lane('ens18').clients[0].tip),/nadav · 10\.44\.10\.50 · נכנס .* · נראה /);
  assert.match(lane('ens18').clients[1].sub,/https:\/\/10\.44\.13\.5:8443 · לא ענה: timeout/);
  assert.match(lane('ens18').clients[2].sub,/מושבת/);
  // כיתות = תיבה סטטית v2 על ה-proxy, מקווקו, בלי נתונים
  assert.deepEqual(kinds(lane('ens20')),[['class','',true]]); assert.equal(lane('ens20').clients[0].title,'כיתות — v2');
  assert.deepEqual(model.orphans,[]);
});

// ---------- שלב 1: תרשים הרשת לפי המוקאפ המאושר — השרת למעלה, וילן לכל כרטיס מוגדר, לחיצה = מגירה ----------
const mapOf=(html)=>{ const s0=html.indexOf('<svg class="netmap-svg"'); assert.ok(s0>=0,'the map is there'); return html.slice(s0,html.indexOf('</svg>',s0)+6); };
const vlans=(svg)=>[...svg.matchAll(/<g class="nm-vl" role="button" tabindex="0" aria-label="([^"]*)" onclick="netLaneOpen\('([^']*)'\)"[^>]*>(.*?)<\/g>/g)].map(m=>({label:m[1],nic:m[2],inner:m[3]}));
test('network map: the server on top, a VLAN card per configured NIC with its NIC name + IP, explicit width/height (gotcha), nothing invented',async()=>{
  const {run}=await loaded();
  const html=plain(run('networkPage(0)')); balanced(html);
  const svg=mapOf(html);
  assert.match(svg,/^<svg class="netmap-svg" xmlns="http:\/\/www\.w3\.org\/2000\/svg" width="\d+" height="340" viewBox="0 0 \d+ 340" role="img"/);
  const lanes=vlans(svg);
  assert.deepEqual(lanes.map(l=>l.nic),['ens18','ens19','ens20'],'ens21 has no role ("לא מוגדר") — it stays in the physical tab, not on the map');
  assert.match(lanes[0].inner,/ens18 · 10\.44\.10\.1</); assert.match(lanes[1].inner,/ens19 · 10\.44\.9\.10</);
  assert.match(lanes[1].inner,/וילן ההפצה/); assert.match(lanes[1].inner,/10\.44\.9\.0\/24/); assert.match(lanes[0].inner,/שרתים — קונסולה/);
  assert.match(svg,/שרת המכללה/); assert.match(svg,/v0\.38\.1 · מחובר ל-3 וילנים/);
  assert.equal((svg.match(/class="nm-port"/g)||[]).length,3,'one port on the server per VLAN');
  assert.doesNotMatch(svg,/VLAN \d/,'no invented VLAN numbers'); assert.doesNotMatch(svg,/10\.10\.10\.|10\.44\.0\./,'no mockup data');
  assert.doesNotMatch(svg,/class="[^"]*\b(bad|warn)\b/,'every NIC is up here — nothing red or orange');
  assert.match(html,/<span>מפת הרשת <small title="נמדד: כתובות ופער[^"]*">לחיצה על וילן פותחת פרטים<\/small><\/span>/,'#1300: the source note is the title of the card sub-line');
  // ‏#1300 (console-full.html P.network): כותרת אחת לארבע הלשוניות — "רשת", פיל DHCP, משנה = הרשתות
  assert.match(html,/<div class="obj-name">רשת<span class="pill ok">DHCP פעיל<\/span><\/div><div class="obj-sub"[^>]*>ניהול 10\.44\.10\.0\/24 · הפצה 10\.44\.9\.0\/24<\/div>/);
  assert.match(html,/<div class="obj-actions"><button class="btn" onclick="refreshPage\(\)">/,'only refresh in the header — "+ כרטיס" lives on the NIC card');
  assert.doesNotMatch(html,/previewDnsmasq|קבצי dnsmasq/,'#1263: removed');
  assert.match(html,/role="tab" aria-selected="true" tabindex="0" onclick="openNetwork\(0\)">תרשים/); assert.match(html,/onclick="selectPageById\('ports'\)">פורטים/);
});

test('network map: a disconnected NIC is red + dashed + "מנותק" — in the card, the wire and the chip; an unread link is orange, not green',async()=>{
  const down=NICS.map(n=>n.name==='ens19'?{...n,state:'down',speed_mbps:null}:n);
  let t=await loaded({'/net/interfaces':down});
  let svg=mapOf(plain(t.run('networkPage(0)')));
  const lane=vlans(svg).find(l=>l.nic==='ens19');
  assert.match(lane.inner,/class="nm-box bad"/); assert.match(lane.inner,/ens19 · מנותק</); assert.match(lane.inner,/class="nm-chip bad"/); assert.match(lane.inner,/>מנותק<\/text>/);
  assert.equal((svg.match(/class="nm-wire bad"/g)||[]).length,1,'exactly the disconnected NIC has a red wire'); assert.equal((svg.match(/class="nm-port bad"/g)||[]).length,1);
  const unknown=NICS.map(n=>n.name==='ens19'?{...n,state:'unknown'}:n);
  t=await loaded({'/net/interfaces':unknown}); svg=mapOf(plain(t.run('networkPage(0)')));
  assert.match(vlans(svg).find(l=>l.nic==='ens19').inner,/class="nm-box warn"/); assert.match(svg,/ens19 · קישור לא נקרא/);
});

test('network map: the services chip is the visible "bind" only — same-name services fold ("HTTP 8080/8081"), clipped with the full text in the <title>',async()=>{
  const {run}=await loaded();
  const svg=plain(mapOf(run('networkPage(0)')));
  const ens19=vlans(svg).find(l=>l.nic==='ens19').inner;
  assert.match(ens19,/TFTP 69 · HTTP 8080 · Multicast 9000–9001|TFTP 69 · HTTP 8080 · Multicast 9000…|TFTP 69 · HTTP 8080 · Multica…/);
  assert.match(ens19,/<title>[^<]*TFTP 69 · HTTP 8080 · Multicast 9000–9001<\/title>/,'the full text is the tooltip');
  assert.equal(run('netAllowedShort(["HTTPS 8443","HTTP 8082","HTTP 8080","DHCP 67"])'),'HTTPS 8443 · HTTP 8082/8080 · DHCP 67','same-name ports fold into one chip');
  assert.equal(run('netAddrList([])'),'ללא כתובת'); assert.equal(run('netAddrList(["10.0.0.1"])'),'⁦10.0.0.1⁩'); assert.equal(run('netAddrList(["1.1.1.1","2.2.2.2","3.3.3.3"])'),'⁦1.1.1.1⁩ · ⁦2.2.2.2⁩ · ⁦+1⁩','each address isolated on its own — " · " is not ASCII');
});

test('network map: without bind the chip says "דורש API", with bind null "טבלת הסוקטים לא נקראה" — never a guessed policy (#705, #1039)',async()=>{
  let {run}=await loaded({'/ports':PORTS_NO_BIND});
  assert.equal(run('netServicesOn(NETW.nics[1])'),null);
  const html=plain(run('networkPage(0)')); assert.match(mapOf(html),/מה מותר: דורש API/); assert.doesNotMatch(mapOf(html),/TFTP 69/);
  assert.match(run('networkPage(1)'),/<span title="— דורש API"><span class="pill ">לא מוגדר<\/span><\/span>/,'#1300: the services are the title of the role pill');
  const server=[port('tftp','TFTP','69','udp','ok','',{bind:[]}),port('http_console','HTTP','8081','tcp','ok','',{bind:['10.44.10.1/24:8081']}),
    port('interserver','HTTPS (mTLS)','8443','tcp','ok','',{bind:['10.44.10.1/24:8443']}),port('multicast','Multicast','9000–9001','udp','off','',{bind:['[::]:9000']})];
  let t=await loaded({'/ports':server});
  assert.equal(t.run('JSON.stringify(netServicesOn(NETW.nics[0]))'),JSON.stringify(['HTTP 8081','HTTPS (mTLS) 8443','Multicast 9000–9001']));
  assert.equal(t.run('JSON.stringify(netServicesOn(NETW.nics[1]))'),JSON.stringify(['Multicast 9000–9001']),'only the wildcard');
  t=await loaded({'/ports':server.map(p=>({...p,bind:[]}))});
  assert.equal(t.run('JSON.stringify(netServicesOn(NETW.nics[0]))'),'[]','every bind [] = read, and nothing listens here');
  t=await loaded({'/ports':server.map(p=>p.id==='tftp'?{...p,bind:null}:p)});
  assert.equal(t.run('netServicesOn(NETW.nics[0])'),null);
  assert.match(mapOf(t.run('networkPage(0)')),/טבלת הסוקטים לא נקראה/); assert.doesNotMatch(mapOf(t.run('networkPage(0)')),/מה מותר: דורש API/);
  assert.match(t.run('networkPage(1)'),/<span title="— לא נקרא"><span class="pill ">לא מוגדר<\/span><\/span>/);
});

test('network map: the lane drawer lists the machines placed on that NIC by subnet (monitor + unregistered + branches) with their state, from the existing API only',async()=>{
  const {run}=await loaded();
  run('globalThis.opened=null; openDrawer=(t,b)=>{ opened={t,b}; }');
  run("netLaneOpen('ens19')");
  const o=run('opened'); assert.equal(o.t,'וילן ההפצה'); balanced(o.b);
  assert.match(o.b,/<div class="obj-sub">וילן ההפצה · <span class="mono">10\.44\.9\.0\/24<\/span><\/div>/);
  assert.match(o.b,/<div class="sec">כרטיס<\/div>[\s\S]*<div class="sec"[^>]*>כתובת<\/div>[\s\S]*<div class="sec"[^>]*>שירותים<\/div>/,'overview = card, address, services');
  assert.match(o.b,/<div class="sec">מחשבים \(\d+\)<\/div>/,'machines are the second tab');
  const tbl=o.b.slice(o.b.indexOf('מחשבים ('));
  assert.match(tbl,/מחשב 1<\/td>.*10\.44\.9\.118/s); assert.match(tbl,/מחשב 2<\/td>.*10\.44\.9\.59/s); assert.match(tbl,/>לא רשום<\/span>/); assert.match(tbl,/>מחובר</); assert.match(tbl,/>לא מחובר</);
  assert.match(o.b,/<button class="btn primary" onclick="closeDrawer\(\);openNetwork\(1\)">לכרטיסי הרשת<\/button>/,'the primary goes to the physical NIC tab');
  assert.doesNotMatch(o.b,/10\.44\.12\./,'no mockup data');
  run("netLaneOpen('nope')"); assert.equal(run('toasts.at(-1)'),'כרטיס לא נמצא');
});

test('network map: a down NIC drawer says so and lists no machines; classroom (proxy) VLANs are not on the map in v1 (#1081, v1.2)',async()=>{
  const down=NICS.map(n=>n.name==='ens19'?{...n,state:'down',speed_mbps:null}:n);
  const t=await loaded({'/net/interfaces':down});
  t.run('globalThis.opened=null; openDrawer=(x,b)=>{ opened=b; }'); t.run("netLaneOpen('ens19')");
  assert.match(t.run('opened'),/class="note err".*הכרטיס ens19 מנותק\. מחשבים בוילן לא יראו את השרת/);
  const v1=await loaded({}, {capabilities:{enroll_secondary:true,classrooms:false}});
  assert.deepEqual(vlans(mapOf(v1.run('networkPage(0)'))).map(l=>l.nic),['ens18','ens19'],'ens20 is the classroom (proxy) VLAN — hidden');
});

test('stage 2: "1000 Mbps" in the link row (VLAN drawer and NIC table) is an isolated LTR run — RTL otherwise prints "Mbps 1000"',async()=>{
  const t=await loaded();
  t.run('globalThis.opened=null; openDrawer=(x,b)=>{ opened=b; }'); t.run("netLaneOpen('ens18')");
  const d=t.run('opened');
  assert.match(d,/<span class="st ok">מחובר · <bdi dir="ltr">1000 Mbps<\/bdi><\/span>/,'the drawer link row');
  assert.doesNotMatch(d,/מחובר · 1000 Mbps/,'never as plain text inside the Hebrew sentence');
  assert.match(t.run('netLinkStatus({present:true,state:"up",speed_mbps:null,name:"x"})'),/^<span class="st ok">מחובר<\/span>$/,'no speed - no stray separator');
  assert.match(t.run('netLinkStatus({present:true,state:"down"})'),/מנותק/); assert.match(t.run('netLinkStatus({present:false})'),/לא קיים במערכת/);
});

test('empty and unread are messages, not an empty drawing: no NICs → empty state; /net/interfaces failed → red note; other sources failed → their own notes',async()=>{
  let t=await loaded({'/net/interfaces':[]});
  let html=t.run('networkPage(0)'); assert.doesNotMatch(html,/<svg class="netmap-svg"/); assert.match(html,/class="empty">לא נמצאו כרטיסי רשת/);
  t=await loaded({'/net/interfaces':new Error('ip link: לא רץ')});
  assert.equal(t.run('NETW.nics'),null);
  html=t.run('networkPage(0)'); assert.doesNotMatch(html,/<svg class="netmap-svg"/); assert.match(html,/class="note err".*לא הצלחתי לקרוא את כרטיסי הרשת: ip link: לא רץ — אין מה לצייר/); assert.match(html,/class="pill err">לא נקרא</);
  assert.match(t.run('networkPage(1)'),/לא הצלחתי לקרוא את כרטיסי הרשת/); assert.match(t.run('networkPage(2)'),/לא הצלחתי לקרוא את כרטיסי הרשת/);
  t=await loaded({'/net':new Error('db locked'),'/ports':new Error('ss חסר'),'/monitor/machines':403,'/storage-nodes':409});
  html=t.run('networkPage(0)'); balanced(html);
  assert.match(html,/\/net לא נקרא[^<]*<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">db locked<\/code>/,'raw system text is behind "technical details"'); assert.match(html,/\/ports לא נקרא: ss חסר/,'a Hebrew-dominant detail stays inline'); assert.match(html,/\/monitor\/machines לא נקרא/);
  assert.doesNotMatch(html,/class="note[^"]*">[^<]*storage-nodes/,'409 = no secondaries, not an error');
  assert.match(html,/<svg class="netmap-svg"/,'NICs were read — the map stays, without the unread machines');
  assert.match(mapOf(html),/מה מותר: דורש API/,'/ports unread → the chip says it does not know, it never draws a guessed policy');
  t=await loaded({'/ports':new Error('x'),'/net/interfaces':NICS.map(n=>({...n,enabled:false,proxy:false,trunk:false}))});
  assert.match(t.run('networkPage(0)'),/אין כרטיס שמוגדר כוילן/,'no NIC has a role that can be known without bind: the map says so');
});

/* ---------- לשונית 1: חיבורים פיזיים ---------- */

test('NICs table (#1300, console-full.html P.network "physical" 1:1): one card "כרטיסי רשת" with "+ כרטיס"; columns name · description · MAC · link · address · role (pill) · ⋯ — no selected-NIC card and no routes card on the page (#1032; #1266: no SSH)',async()=>{
  const {run}=await loaded();
  const html=run('networkPage(1)'); balanced(html);
  assert.match(html,/<div class="c12 card"><div class="card-h"><span>כרטיסי רשת<\/span><div class="acts"><button class="btn primary sm" onclick="addNic\(\)"><svg[^]*?<\/svg> כרטיס<\/button><\/div><\/div>/);
  assert.match(html,/<table class="dg kebab-col"><thead><tr><th>כרטיס<\/th><th>תיאור<\/th><th class="c-opt">MAC<\/th><th>קישור<\/th><th>כתובת<\/th><th>תפקיד<\/th><th><span class="sr">פעולות<\/span><\/th>/);
  assert.doesNotMatch(html,/<span>נתיבים סטטיים|— הפצה<\/span>|נבחר<\/small>/,'only the NIC card, like the mockup');
  const r18=row(html,'data-nic="ens18"'), r19=row(html,'data-nic="ens19"'), r20=row(html,'data-nic="ens20"'), r21=row(html,'data-nic="ens21"');
  for(const r of [r18,r20,r21]) { const n=r.match(/data-nic="(ens\d\d)"/)[1];   // ‏MENUS.nic: הגדרת כרטיס · תיאור; ‏+ נתיבים סטטיים (שהיו כרטיס בדף)
    assert.deepEqual(menuOf(r),[['הגדרת כרטיס…',`netEditNic('${n}')`],['תיאור…',`netDescribe('${n}')`],['נתיבים סטטיים…',`netRoutesDrawer('${n}')`]]); }
  assert.deepEqual(menuOf(r19).at(-1),['כיבוי DHCP…',"netDhcpOff('ens19')",'danger'],'the deploy NIC: DHCP off, last and red');
  assert.match(r19,/^<tr data-nic="ens19">/,'no painted "selected" row — the mockup has none');
  assert.match(r19,/<td><b>ens19<\/b><\/td><td>הפצה<\/td><td class="c-opt"><span class="mono">bc:24:11:00:00:19<\/span><\/td><td><bdi dir="ltr">1 Gb<\/bdi><\/td>/);
  assert.match(r21,/<td><b>ens21<\/b><\/td><td>—<\/td>/); assert.match(r21,/<td>ללא קישור<\/td>/);
  assert.match(r18,/<td><span title="מוגדר: static 10\.44\.10\.1\/24 · gw 10\.44\.10\.254">10\.44\.10\.1<\/span><\/td>/,'the address as measured, no prefix; the configured text is the title');
  assert.match(r18,/<span title="ניהול · TFTP 69 · HTTP 8080 · HTTP 8081"><span class="pill info">ניהול<\/span><\/span>/,'#1263: the management NIC is named; services in the title');
  assert.match(r19,/<span class="pill ok">הפצה · פעיל<\/span>/);
  assert.match(r20,/10\.44\.11\.2<\/span> <span title="כתובת: מוגדר 10\.44\.11\.1\/24, בפועל 10\.44\.11\.2\/24"><span class="pill err">לא תואם להגדרה<\/span>/,'a gap is a red pill, the detail in the title');
  assert.match(r20,/class="pill warn">רשת המכללה<|class="pill ">PXE proxy</);
  assert.match(r21,/<span class="pill ">לא מוגדר<\/span>/);
  assert.doesNotMatch(html,/SSH|ssh_nic|role="switch"/,'#1266: no SSH switch per NIC');
  // נתיבים סטטיים — מגירה מהתפריט: ראיה חיובית מטבלת הניתוב, מחיקה דרך ה-sheet של netcfg.js
  const routes=run('netRoutesHtml("ens18")');
  assert.match(routes,/class="st ok"><\/span><span class="mono">10\.44\.13\.0\/24<\/span><\/td><td><span class="mono">10\.44\.10\.254<\/span>/);
  assert.match(routes,/onclick="routeDeleteSheet\('ens18',0\)">מחק/);
  run("routeDeleteSheet('ens18',0)"); assert.equal(run('sheets.length'),1); assert.equal(run('sheets[0].verify'),undefined,'#1270: no typed word'); assert.equal(run('sheets[0].danger'),true);
});

test('NICs: live state not read → "לא נקרא" (never an address shown as measured), interfaces.d not sourced → red note, rollback pending → the banner',async()=>{
  const cfg=JSON.parse(JSON.stringify(NETCFG)); cfg.live={checked:false,reason:'ip addr: לא רץ',routes:[],nameservers:[]}; cfg.sourced=false;
  cfg.rollback={...cfg.rollback,pending:true,interface:'ens18',seconds_left:42};
  for(const r of cfg.interfaces) r.mismatches=['המצב בפועל לא נקרא'];
  const {run}=await loaded({'/net/config':cfg});
  const html=run('networkPage(1)'); balanced(html);
  const r18=row(html,'data-nic="ens18"');
  assert.match(r18,/<td><span class="muted" title="מוגדר: static 10\.44\.10\.1\/24 · gw 10\.44\.10\.254">לא נקרא<\/span><\/td>/);
  assert.doesNotMatch(r18,/10\.44\.10\.1<\/span>/);
  assert.match(html,/המצב בפועל לא נקרא[^<]*<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">ip addr: לא רץ<\/code>/);
  assert.match(html,/interfaces\.d — כל מה שנכתב שם לא ייקרא באתחול/);
  assert.match(html,/id="netcfg-pending"/); assert.match(html,/ההגדרה של ens18 ממתינה לאישור/); assert.match(html,/onclick="confirmNetRollback\(\)"/);
  assert.match(run('networkPage(0)'),/id="netcfg-pending"/,'the banner is above the diagram too');
  assert.match(run('netRoutesHtml("ens18")'),/class="st unk"><\/span><span class="mono">10\.44\.13\.0\/24/);
  // ‏/net/config לא נקרא בכלל → אותו דבר, עם השם של המקור
  const t=await loaded({'/net/config':new Error('permission denied')});
  const h2=t.run('networkPage(1)'); assert.match(h2,/\/net\/config לא נקרא[^<]*<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">permission denied/); assert.match(row(h2,'data-nic="ens18"'),/title="מוגדר: לא נקרא">/);
  assert.match(t.run('netRoutesHtml("ens18")'),/\/net\/config לא נקרא: permission denied/);
});

// ‏#1263 (הכרעת נדב 10/10): לכל כרטיס אופן הגדרה אחד — רשת הפצה / DHCP מהרשת / כתובת ידנית — בחלון
// אחד "הגדרת כרטיס…"; הגדרות רשת ההפצה בחלון של לשונית "רשת הפצה". בלי הקלדת שם הכרטיס בשניהם;
// כיבוי DHCP — כן. כרטיס הניהול: DHCP או ידנית בלבד.
// מערכים מתוך ה-vm הם מ-realm אחר — deepEqual קפדני משווה גם prototype; JSON מנרמל.
const sheetAt=(run,i)=>JSON.parse(run(`JSON.stringify(sheets[${i}])`));
test('#1263 the card window: three modes, no typed name; the management NIC has no deploy mode and says the 60-second rollback',async()=>{
  const {run,requests}=await loaded();
  run("netEditNic('ens19')");
  let sh=sheetAt(run,0);
  assert.equal(sh.title,'הגדרת כרטיס — ens19'); assert.equal(sh.verify,undefined,'no typed NIC name'); assert.notEqual(sh.danger,true);
  const mode=(o)=>o.fields.find(f=>f.id==='mode');
  assert.deepEqual(mode(sh).options.map(o=>[o.value,o.label]),[['deploy','רשת הפצה'],['dhcp','DHCP מהרשת'],['static','כתובת ידנית']]);
  assert.equal(mode(sh).value,'deploy','DHCP serving = the deploy mode'); assert.equal(mode(sh).type,'radio');
  assert.deepEqual(sh.fields.map(f=>f.id),['mode','address','netmask','gateway','dns1','dns2'],'static: IP/mask/gateway + two DNS');
  assert.match(sh.note,/כבו קודם את ה-DHCP/,'leaving the deploy mode means DHCP off first');
  run("netEditNic('ens18')"); sh=sheetAt(run,1);
  assert.equal(sh.sub,'כרטיס הניהול · רשת השרתים'); assert.deepEqual(mode(sh).options.map(o=>o.value),['dhcp','static']);
  assert.match(sh.note,/כרטיס הניהול — נבחר בהתקנה/); assert.match(sh.note,/תוך 60 שניות, ההגדרה הקודמת תחזור/);
  assert.equal(mode(sh).value,'static'); assert.equal(sh.fields.find(f=>f.id==='dns1').value,'10.44.10.2');
  run("netEditNic('ens20')"); sh=sheetAt(run,2);
  assert.equal(mode(sh).value,'static'); assert.equal(sh.fields.find(f=>f.id==='address').value,'10.44.11.1');
  run("netEditNic('ens21')"); sh=sheetAt(run,3);
  assert.equal(mode(sh).value,'dhcp','unmanaged with no live address → DHCP'); assert.match(sh.note,/אינו מנוהל היום מהקונסולה/);
  assert.ok(!requests.some(r=>r.method!=='GET'),'nothing is written before a submit');
});

test('#1263 the card window saves static (two DNS) and DHCP through the existing address API, confirm sent by the screen',async()=>{
  const t=await loaded();
  t.fixtures['/net/config/ens20/preview']={problems:[]};
  t.fixtures['/net/config/ens20']={ok:true,verified:true,apply_error:null,mismatches:[],rollback:{pending:false}};
  t.run("netEditNic('ens20')");
  await t.run("sheets[0].onSubmit({mode:'static',address:'10.44.11.5',netmask:'255.255.255.0',gateway:'10.44.11.254',dns1:'10.44.10.2',dns2:'10.44.10.3'})");
  const put=t.requests.find(r=>r.method==='PUT'&&r.url==='/net/config/ens20');
  assert.ok(put,'PUT /net/config/ens20');
  assert.equal(put.body.mode,'static'); assert.equal(put.body.address,'10.44.11.5'); assert.deepEqual(put.body.dns,['10.44.10.2','10.44.10.3']);
  assert.equal(put.body.confirm,'ens20','the screen sends the NIC name — no typing');
  assert.ok(t.requests.some(r=>r.method==='POST'&&r.url==='/net/config/ens20/preview'),'server problems are asked first');
  t.requests.length=0;
  await t.run("sheets[0].onSubmit({mode:'dhcp',address:'',netmask:'',gateway:'',dns1:'',dns2:''})");
  const dhcp=t.requests.find(r=>r.method==='PUT'); assert.equal(dhcp.body.mode,'dhcp'); assert.deepEqual(dhcp.body.dns,[]); assert.equal(dhcp.body.address,'');
  // שדות לא תקינים — שום בקשה
  t.requests.length=0;
  const bad=await t.run("sheets[0].onSubmit({mode:'static',address:'10.44.11.300',netmask:'255.0.255.0',gateway:'',dns1:'x',dns2:''}).then(()=>null,e=>e.message)");
  assert.equal(bad,'כתובת IP אינה תקינה · מסכה אינה תקינה · DNS ראשי אינו כתובת תקינה'); assert.equal(t.requests.filter(r=>r.method!=='GET').length,0);
  // כרטיס הניהול לא נהיה רשת הפצה, וכרטיס ההפצה לא עוזב את האופן בלי לכבות DHCP
  t.run("netEditNic('ens18')");
  assert.match(await t.run("sheets[1].onSubmit({mode:'deploy'}).then(()=>null,e=>e.message)"),/כרטיס הניהול/);
  t.run("netEditNic('ens19')");
  assert.match(await t.run("sheets[2].onSubmit({mode:'static',address:'10.44.9.1',netmask:'255.255.255.0',gateway:'',dns1:'',dns2:''}).then(()=>null,e=>e.message)"),/כבו קודם את ה-DHCP/);
  assert.equal(t.requests.filter(r=>r.method!=='GET').length,0);
});

test('#1263 "+ כרטיס" is the same window with a detected-NIC select; deploy mode continues to the deploy window',async()=>{
  const t=await loaded();
  t.run('addNic()');
  const sh=sheetAt(t.run,0);
  assert.equal(sh.title,'הוספת כרטיס'); assert.deepEqual(sh.fields.slice(0,2).map(f=>f.id),['nic','description']);
  assert.deepEqual(sh.fields[0].options.map(o=>o.value),['ens21'],'present, not serving, not described'); assert.match(sh.fields[0].options[0].label,/^ens21 — מנותק$/);
  t.run('setTimeout=(f)=>{ f(); return 1; }');
  await t.run("sheets[0].onSubmit({nic:'ens21',description:'כיתה 3',mode:'deploy'})");
  assert.deepEqual(t.requests.filter(r=>r.method!=='GET').map(r=>[r.method,r.url,r.body]),[['POST','/net/interfaces',{name:'ens21',description:'כיתה 3'}]]);
  assert.equal(t.run('sheets[1].title'),'רשת הפצה — ens21','the deploy window opens for the chosen NIC');
});

test('#1263 the deploy window: network + mask → the server address is derived; no gateway, no DNS, no proxy, no typed name',async()=>{
  const t=await loaded();
  assert.equal(t.run("firstHost('10.0.20.0','255.255.255.0')"),'10.0.20.1');
  assert.equal(t.run("firstHost('10.0.20.5','255.255.255.0')"),'','not a network address — not rounded');
  assert.equal(t.run("firstHost('10.0.20.0','255.255.255.254')"),'','/31 has no room');
  t.run("netEditDeploy('ens19')");
  const sh=sheetAt(t.run,0);
  assert.equal(sh.title,'רשת הפצה — ens19'); assert.equal(sh.verify,undefined);
  const ids=sh.fields.filter(f=>f.id); assert.deepEqual(ids.map(f=>f.id),['network','netmask','range_start','range_end','lease_n','lease_u']);
  assert.deepEqual(sh.fields.filter(f=>f.type==='section').map(f=>f.label),['רשת ההפצה','DHCP למחשבים'],'the mockup sections, in the stage-1 window frame');
  assert.equal(ids[0].value,'10.44.9.0'); assert.equal(ids[4].value,'12'); assert.equal(ids[5].value,'h');
  assert.doesNotMatch(JSON.stringify(sh),/proxy|שער|DNS/,'closed network: no proxy, gateway or DNS');
  assert.match(sh.note,/השרת יקבל אוטומטית את <b dir="ltr">10\.44\.9\.1<\/b>/);
  const err=(v)=>t.run(`sheets[0].onSubmit(${JSON.stringify(v)}).then(()=>null,e=>e.message)`);
  const good={network:'10.44.30.0',netmask:'255.255.255.0',range_start:'10.44.30.50',range_end:'10.44.30.200',lease_n:'8',lease_u:'h'};
  assert.equal(await err({...good,network:'10.44.30.5'}),'10.44.30.5 אינה כתובת רשת — עם המסכה הזו הרשת היא 10.44.30.0');
  assert.equal(await err({...good,range_start:'10.44.30.1'}),'כתובת השרת (10.44.30.1) נמצאת בתוך הטווח');
  assert.equal(await err({...good,range_end:'10.44.31.9'}),'הטווח חייב להיות בתוך הרשת');
  assert.equal(await err({...good,lease_n:'0'}),'משך חכירה: מספר שלם חיובי');
  assert.equal(t.requests.filter(r=>r.method!=='GET').length,0);
  t.fixtures['/net/config/ens19/deploy']={ok:true,server_ip:'10.44.30.1',address:{ok:true},dhcp:{ok:true},apply_error:null};
  assert.equal(await err(good),null);
  const put=t.requests.find(r=>r.method==='PUT');
  assert.equal(put.url,'/net/config/ens19/deploy','one request — address and DHCP together');
  assert.deepEqual(put.body,{network:'10.44.30.0',netmask:'255.255.255.0',range_start:'10.44.30.50',range_end:'10.44.30.200',lease:'8h',confirm:'ens19',confirm_trunk:false});
  assert.match(t.run('toasts[toasts.length-1]'),/רשת ההפצה נשמרה על ens19 — כתובת השרת 10\.44\.30\.1/);
  // הכתובת לא אומתה — ה-DHCP לא נגע, והחלון נשאר פתוח עם הסיבה
  t.fixtures['/net/config/ens19/deploy']={ok:false,server_ip:'10.44.30.1',address:{ok:false},dhcp:null,apply_error:'הכתובת 10.44.30.1 על ens19 לא אומתה מול המצב בפועל — ה-DHCP לא שונה'};
  assert.match(await err(good),/לא אומתה.*ה-DHCP לא שונה/);
  // כרטיס הניהול — אין חלון בכלל
  t.run("netEditDeploy('ens18')"); assert.equal(t.run('sheets.length'),1); assert.match(t.run('toasts[toasts.length-1]'),/כרטיס הניהול/);
});

// ‏#1263 אחרי שלב 1: כל שדה ב-sheet() יושב ב-`.n-f` והתווית היא אחות, לא אם — ההסתרה היא של ה-`.n-f`.
test('#1263 card-window wiring: static fields hide outside "כתובת ידנית", the button and the footer hint follow validity',async()=>{
  const {run}=await loaded();
  run(`globalThis.W={}; const mk=(v)=>{const wrap={style:{display:''}}; return {value:v,wrap,closest:(sel)=>sel==='.n-f'?wrap:null};};
    W.els={address:mk('10.44.11.5'),netmask:mk('255.255.255.0'),gateway:mk(''),dns1:mk(''),dns2:mk('')};
    W.mode='dhcp'; W.on={}; W.submit={disabled:false}; W.hint={textContent:''};
    W.panes=[{dataset:{nicPane:'deploy'},hidden:false},{dataset:{nicPane:'dhcp'},hidden:true},{dataset:{nicPane:'leave-deploy'},hidden:true}];
    W.body={addEventListener:(t,f)=>{W.on[t]=f;},querySelectorAll:()=>W.panes};
    W.form={querySelector:(sel)=>sel==='.sbody'?W.body:sel==='button[type="submit"]'?W.submit:sel.indexOf('sf-mode')>=0?{value:W.mode}:null};
    document.getElementById=(id)=>id==='sheet'?W.form:id==='sf-hint'?W.hint:(W.els[id.replace('sf-','')]||null);
    W.shown=()=>Object.values(W.els).map(e=>e.wrap.style.display);`);
  // כרטיס הפצה שבחרו בו "DHCP מהרשת": השדות מוסתרים, הכפתור כבוי והסיבה בפס התחתון
  run("wireNicWindow('dhcp','deploy')");
  assert.deepEqual(JSON.parse(run('JSON.stringify(W.shown())')),['none','none','none','none','none']);
  assert.equal(run('W.submit.disabled'),true); assert.equal(run('W.hint.textContent'),'כבו קודם את ה-DHCP של ההפצה');
  assert.equal(run('W.panes[1].hidden'),false); assert.equal(run('W.panes[0].hidden'),true); assert.equal(run('W.panes[2].hidden'),false);
  // כרטיס ידני תקין: השדות גלויים והכפתור דלוק; כתובת שבורה — כבוי, והסיבה נאמרת
  run("W.mode='static'; wireNicWindow('static','static')");
  assert.deepEqual(JSON.parse(run('JSON.stringify(W.shown())')),['','','','','']);
  assert.equal(run('W.submit.disabled'),false); assert.equal(run('W.hint.textContent'),'');
  run("W.els.address.value='10.44.11.300'; W.on.input()");
  assert.equal(run('W.submit.disabled'),true); assert.equal(run('W.hint.textContent'),'כתובת IP אינה תקינה');
});

test('#1263 DHCP off keeps the typed NIC name; the forget/description sheets are unchanged',async()=>{
  const {run,requests}=await loaded();
  run("netDhcpOff('ens19')");
  assert.equal(run('sheets[0].title'),'לכבות את ה-DHCP?'); assert.equal(run('sheets[0].sub'),'ens19'); assert.equal(run('sheets[0].verify'),undefined,'#1270: no typed word'); assert.equal(run('sheets[0].danger'),true);
  run("netForget('ens21')"); assert.match(run('sheets[1].title'),/להסיר את הגדרות הכרטיס/);
  for(const fn of ['editNic','editAddress','showFile','previewDnsmasq','netShowFile','netEditDhcp','netEditAddress']) assert.equal(run(`typeof ${fn}`),'undefined',fn+' should be gone');
  assert.ok(!requests.some(r=>r.method==='PUT'),'nothing is written without the sheet being submitted');
});

/* ---------- לשונית 2: רשת הפצה ---------- */

test('deploy network (#1300, console-full.html P.network "dist" 1:1): "רשת ההפצה" dl.kv (network, server address, DHCP, range, lease + "who else answers" with "בדוק") with edit / DHCP off; "כרטיסים ברשת ההפצה"; "חכירות פעילות" (address · MAC · name · expires)',async()=>{
  const {run}=await loaded();
  const html=run('networkPage(2)'); balanced(html);
  assert.match(html,/class="obj-name">רשת<span class="pill ok">DHCP פעיל</);
  assert.match(html,/<div class="obj-actions"><button class="btn" onclick="refreshPage\(\)">/);
  assert.doesNotMatch(html,/previewDnsmasq|קבצי dnsmasq|<span>כרטיסים אחרים|<span>מי קיבל כתובת/);
  assert.match(html,/<div class="c6 card"><div class="card-h"><span>רשת ההפצה<\/span><div class="acts"><button class="btn sm" onclick="netEditDeploy\('ens19'\)">עריכה…<\/button><button class="btn sm danger" onclick="netDhcpOff\('ens19'\)">כיבוי DHCP…<\/button><\/div>/);
  assert.match(html,/<div class="c6 card"><div class="card-h"><span>כרטיסים ברשת ההפצה/);
  const serves=card(html,'רשת ההפצה');
  assert.match(serves,/<dt>כתובת רשת<\/dt><dd><span class="mono">10\.44\.9\.0 \/ 255\.255\.255\.0<\/span><\/dd>/);
  assert.match(serves,/<dt>כתובת השרת<\/dt><dd><span class="mono">10\.44\.9\.10<\/span> <small class="muted">\(אוטומטית, הראשונה ברשת\)<\/small><\/dd>/);
  assert.match(serves,/<dt>DHCP<\/dt><dd><span title="[^"]*"><span class="pill ok">פעיל — מחלק כתובות<\/span><\/span><\/dd>/);
  assert.match(serves,/<dt>טווח<\/dt><dd><span class="mono">10\.44\.9\.100 – 10\.44\.9\.200<\/span><\/dd>/);
  assert.match(serves,/<dt>חכירה<\/dt><dd>12 שעות<\/dd>/,'"12h" of dnsmasq reads "12 שעות"');
  assert.match(serves,/<dt>מי עוד עונה<\/dt><dd><span class="st ">לא נבדק<\/span> <button class="btn sm flat" type="button" onclick="netProbe\('ens19'\)">בדוק<\/button><\/dd>/);
  const leases=card(html,'חכירות פעילות');
  assert.match(leases,/<thead><tr><th>כתובת<\/th><th>MAC<\/th><th>שם<\/th><th class="c-opt">פוקעת<\/th><\/tr>/);
  assert.match(row(leases,'data-mac="'+C1+'"'),/<bdi dir="ltr">10\.44\.9\.118<\/bdi>[^]*<td>מחשב 1<\/td>/,'a registered machine by its name');
  const rl=row(leases,'data-mac="'+LEASED+'"');
  assert.match(rl,/class="pill warn">לא רשום<\/span> <button class="btn sm flat" type="button" onclick="netRegister\('02%3A00%3A00%3A00%3A00%3A77'\)">רשום<\/button>/);
  assert.match(rl,/<td class="c-opt">—<\/td>/,'no expiry = dash');
  run("netRegister('de%3Aad%3Abe%3Aef%3A00%3A01')"); assert.equal(run('JSON.stringify(globalThis.addMachine)'),JSON.stringify({mac:UNREG}));
});

test('deploy network: stored ≠ live (dhcp_diverged) is orange, live not read is "לא נקרא", no DHCP NIC at all → "הגדר כרשת הפצה" on a non-trunk NIC',async()=>{
  const div=NICS.map(n=>n.name==='ens19'?{...n,dhcp_diverged:true,dhcp_live:{state:'configured_not_running',checked:true,detail:'dnsmasq inactive'},dhcp_live_label:'מוגדר, השירות אינו פועל'}:n);
  let t=await loaded({'/net/interfaces':div}); let html=t.run('networkPage(2)');
  assert.match(html,/class="pill warn">DHCP לא תואם למצב הפעיל</); assert.match(card(html,'רשת ההפצה'),/class="pill warn">מופעל — לא תואם למצב הפעיל</);
  const unk=NICS.map(n=>n.name==='ens19'?{...n,dhcp_live:{state:'unknown',checked:false,detail:'לא ניתן לקרוא את מצב ה-DHCP הפעיל'},dhcp_live_label:'לא ידוע'}:n);
  t=await loaded({'/net/interfaces':unk}); html=t.run('networkPage(2)');
  assert.match(html,/class="pill (warn|)">DHCP: לא ידוע</,'not read is never "DHCP פעיל"'); assert.doesNotMatch(html,/DHCP פעיל/);
  assert.match(card(html,'רשת ההפצה'),/class="pill unk">מופעל — המצב הפעיל לא נקרא</); assert.doesNotMatch(card(html,'רשת ההפצה'),/pill ok/);
  const none=NICS.map(n=>({...n,enabled:false,proxy:false,dhcp_live:{state:'off',checked:true,detail:''},dhcp_live_label:'כבוי'}));
  t=await loaded({'/net/interfaces':none}); html=t.run('networkPage(2)');
  assert.match(html,/class="pill ">DHCP כבוי</); assert.match(html,/הפצה: לא הוגדרה/);
  assert.match(html,/<div class="c12 card"><div class="card-h"><span>רשת ההפצה<\/span><\/div>/,'no deploy NIC → one full-width card');
  assert.match(card(html,'רשת ההפצה'),/class="empty">אין כרטיס מוגדר כרשת הפצה[^]*<button class="btn primary" onclick="netEditDeploy\('ens19'\)">הגדר כרשת הפצה</,'first present non-trunk NIC that is not the management NIC (#1263)');
  assert.doesNotMatch(html,/בדוק<\/button>|<span>חכירות פעילות/);
  assert.match(t.run('networkPage(0)'),/class="pill ">DHCP כבוי</,'the diagram shares the header');
});

test('#1556: management NIC unknown → "הגדר כרשת הפצה" never defaults to the NIC the console came through; both unknown → a picker with nothing chosen',async()=>{
  const none=NICS.map(n=>({...n,enabled:false,proxy:false,dhcp_live:{state:'off',checked:true,detail:''},dhcp_live_label:'כבוי'}));
  // שרת חיפה: אין firstboot.status, הקונסולה מחוברת דרך הכרטיס הראשון
  let t=await loaded({'/net/interfaces':none,'/net/config':{...NETCFG,management_interface:null,console_interface:'ens18'}});
  let html=card(t.run('networkPage(2)'),'רשת ההפצה');
  assert.match(html,/<button class="btn primary" onclick="netEditDeploy\('ens19'\)">הגדר כרשת הפצה</,'skips ens18 — the console is on it');
  // לא ידוע דרך מה הקונסולה מחוברת, ולא ידוע מי הניהול — לא מנחשים: בוחרים
  t=await loaded({'/net/interfaces':none,'/net/config':{...NETCFG,management_interface:null,console_interface:null}});
  html=card(t.run('networkPage(2)'),'רשת ההפצה');
  assert.match(html,/<button class="btn primary" onclick="netPickDeploy\(\)">הגדר כרשת הפצה</);
  t.run('netPickDeploy()');
  const pick=t.run('sheets[sheets.length-1]'), field=pick.fields.find(f=>f.id==='nic');
  assert.equal(field.value,'','nothing is chosen for the operator');
  assert.equal(JSON.stringify(field.options.map(o=>o.value)),JSON.stringify(['','ens18','ens19','ens21']),'present, not trunk, not management');
  await assert.rejects(pick.onSubmit({nic:''}),/בחרו כרטיס/);
  const next=await pick.onSubmit({nic:'ens19'}); next();
  assert.equal(t.run('sheets[sheets.length-1]').title,'רשת הפצה — ens19');
});

/* ‏#1039: חכירות dnsmasq — מכונה שקיבלה כתובת ולא הגיעה ל-hello נראית רק שם; "לא נקרא" בשם, לא טבלה ריקה. */
test('deploy network: leases unread are named, never an empty table (principle 5)',async()=>{
  let t=await loaded({'/net/interfaces/ens19/leases':{interface:'ens19',checked:false,reason:'/var/lib/misc/dnsmasq.leases: Permission denied',path:'/var/lib/misc/dnsmasq.leases',leases:null}});
  let html=t.run('networkPage(2)');
  assert.match(card(html,'חכירות פעילות'),/חכירות dnsmasq לא נקראו<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">\/var\/lib\/misc\/dnsmasq\.leases: Permission denied/); assert.doesNotMatch(card(html,'חכירות פעילות'),/<table/);
  t=await loaded({'/net/interfaces/ens19/leases':new Error('boom')}); html=t.run('networkPage(2)');
  assert.match(card(html,'חכירות פעילות'),/חכירות dnsmasq לא נקראו: boom/);
  t=await loaded({'/net/interfaces/ens19/leases':{...LEASES,leases:[]}}); html=t.run('networkPage(2)');
  assert.match(card(html,'חכירות פעילות'),/class="empty">אין חכירות על ens19/);
});

test('console box: /sessions not read is named on the box and as a note — never "only me"',async()=>{
  const t=await loaded({'/sessions':new Error('db locked')});
  const lane=JSON.parse(plain(t.run('JSON.stringify(netDiagramModel())'))).lanes.find(l=>l.nic.name==='ens18');
  assert.equal(lane.clients[0].sub,'session פעיל · מי מחובר — לא נקרא: db locked');
  assert.match(t.run('networkPage(0)'),/\/sessions לא נקרא — מי מחובר לקונסולה אינו ידוע<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">db locked<\/code>/);
});

test('probe ("מי עוד עונה"): checked=false is "the check did not run", not "nobody"; servers found is red; a failed request is a toast + unknown',async()=>{
  const {run,requests}=await loaded();
  await run("netProbe('ens19')");
  assert.deepEqual(requests.filter(r=>r.url.endsWith('/probe')).map(r=>[r.method,r.url]),[['GET','/net/interfaces/ens19/probe']]);
  assert.match(card(run('networkPage(2)'),'רשת ההפצה'),/<dt>מי עוד עונה<\/dt><dd><span class="st ok">אף שרת אחר לא עונה \(נבדק \d\d:\d\d\)<\/span>/);
  await run("netProbe('ens18')"); await run("netProbe('ens20')");
  assert.equal(run("netProbeStatus('ens18').cls"),'unk'); assert.match(run("netProbeStatus('ens18').text"),/הבדיקה לא רצה \(\d\d:\d\d\) — לא ידוע מי עונה/);
  assert.equal(run("netProbeStatus('ens20').cls"),'err'); assert.match(run("netProbeStatus('ens20').text"),/עונים: 10\.44\.11\.254 \(נבדק \d\d:\d\d\)/);
  assert.equal(run("netProbeStatus('ens21').text"),'לא נבדק');
  const t=await loaded({'/net/interfaces/ens19/probe':new Error('timeout')});
  await t.run("netProbe('ens19')");
  assert.equal(t.run('JSON.stringify(toasts)'),JSON.stringify(['בדיקת DHCP נכשלה: timeout'])); assert.match(t.run('networkPage(2)'),/class="st unk">הבדיקה נכשלה \(\d\d:\d\d\): timeout</);
});

/* 18/09 — מודל האתרים (הכרעת נדב, interfaces.md §18 "חוקי חומת האש בין האתרים"): לכל אתר שלושה וילנים — שרתים / הפצה / כיתות,
   ואין וילן "ניהול" נפרד. שרת המשנה יושב מאחורי FW משלו, הראשי יוזם ורק 8443 פתוח ביניהם — ולכן הוא לא לקוח על הוילן אלא "מעבר ל-FW". */
test('site model: the console/8443 VLAN is "שרתים", and a secondary is "דרך FW · 8443 בלבד" on a dashed line even when its box is red/green',async()=>{
  const {run}=await loaded();
  const model=JSON.parse(plain(run('JSON.stringify(netDiagramModel())')));
  const lane=(name)=>model.lanes.find(l=>l.nic.name===name);
  assert.equal(lane('ens18').vlan.label,'שרתים — קונסולה');
  // כרטיס שנושא רק 8443 (bind של interserver על כתובתו) — עדיין וילן השרתים, לא וילן משלו
  run('NETW.ports=NETW.ports.map(p=>p.id==="interserver"?{...p,bind:"10.44.13.1"}:p)');
  assert.equal(run('JSON.stringify(netVlanOf({enabled:false,proxy:false,trunk:false,addresses:["10.44.13.1/24"],name:"ens22"}))'),JSON.stringify({kind:'inter',label:'שרתים — בין-שרתי'}));
  const [console_,haifa,old]=lane('ens18').clients;
  assert.equal(console_.kind,'console'); assert.ok(!console_.viaFw,'the console is a client on the VLAN, not behind a FW');
  assert.equal(haifa.sub,'דרך FW · 8443 בלבד · https://10.44.13.5:8443 · לא ענה: timeout');
  assert.equal(haifa.tip,'שרת המשנה מאחורי FW משלו; הראשי יוזם, רק 8443 · https://10.44.13.5:8443 · לא ענה: timeout');
  assert.equal(old.sub,'דרך FW · 8443 בלבד · https://10.44.10.77:8443 · מושבת');
  assert.equal(haifa.viaFw,true); assert.equal(old.viaFw,true); assert.equal(haifa.dashed,false,'the box stays solid — connected is measured; the line is what marks the FW');
  const html=plain(run('networkPage(0)')); balanced(html);
  assert.match(mapOf(html),/שרתים — קונסולה/); assert.doesNotMatch(html,/ניהול — קונסולה|>בין-שרתים —/);
  // שרת המשנה אינו קופסה על המפה אלא שורה במגירת הוילן, עם "שרת משנה" וחיבור לפי מה שנמדד
  assert.deepEqual(lane('ens18').rows.filter(r=>r.kind==='branch').map(r=>[r.name,r.ok]),[['סניף חיפה',false],['סניף ישן',false]],'connected is measured; disabled is not "connected"');
  run('globalThis.opened=null; openDrawer=(t,b)=>{ opened=b; }'); run("netLaneOpen('ens18')");
  assert.match(run('opened'),/סניף חיפה<\/td>.*שרת משנה/s);
});

test('#1081 v1: classrooms flag off does not draw the classrooms box and hides proxy DHCP from רשת הפצה', async () => {
  const {run}=await loaded({}, {capabilities:{enroll_secondary:true,classrooms:false}});
  const model=JSON.parse(plain(run('JSON.stringify(netDiagramModel())')));
  const lane=(name)=>model.lanes.find(l=>l.nic.name===name);
  assert.equal((lane('ens20').clients||[]).filter(c=>c.kind==='class').length,0);
  const html=plain(run('networkPage(0)'));
  assert.doesNotMatch(html,/כיתות — v2/);
  assert.doesNotMatch(html,/מחשבי כיתה אינם במהדורה זו/);
  const deploy=run('networkPage(2)');
  assert.doesNotMatch(deploy,/class="name">ens20</,'proxy NIC is the classroom VLAN — hidden in v1');
});

// ‏#1088: רשת ההפצה מוגדרת מהקונסולה — מה הדף אומר לפני, ומה אחרי ההדלקה הראשונה.
test('#1088 deploy network not configured: /net/deploy configured=false → note + tab text; a shape without configured is not "not configured"; the first DHCP enable toasts the restart',async()=>{
  const none=NICS.map(n=>({...n,enabled:false,proxy:false,dhcp_live:{state:'off',checked:true,detail:''},dhcp_live_label:'כבוי'}));
  const hint='רשת ההפצה לא הוגדרה — בחר כרטיס בדף הרשת, קבע לו כתובת סטטית והדלק עליו DHCP';
  let t=await loaded({'/net/interfaces':none,'/net/deploy':{configured:false,source:'none',interface:null,url:null,hint}});
  assert.equal(t.run('NET_DEPLOY.source'),'none');
  let html=t.run('networkPage(0)'); balanced(html);
  assert.match(html,new RegExp(hint+' — <a href="#" onclick="openNetwork\\(2\\);return false;">לשונית רשת הפצה</a>'));
  html=t.run('networkPage(2)');
  assert.match(html,new RegExp(hint+' — <a href="#" onclick="openNetwork\\(2\\);return false;">'),'#1300: the "not configured" hint is the note on every tab (the tab sub-line is shared now)');
  // הטופס של ההדלקה הראשונה אומר שזו הגדרת רשת ההפצה, והשמירה מדווחת על האתחול
  t.run('netEditDeploy("ens19")');
  const sh=t.run('sheets[sheets.length-1]');
  assert.match(sh.note,/רשת ההפצה מוגדרת כאן <b>בפעם הראשונה<\/b>/);
  t.fixtures['/net/interfaces/ens18']={ok:true,apply_error:null,interface:{},deploy:{ok:true,url:'http://10.44.9.10:8080',interface:'ens18',errors:[],restarting:true}};
  await t.run('saveNic("ens18",{enabled:true,confirm:"ens18"})');
  assert.match(t.run('toasts[toasts.length-1]'),/רשת ההפצה הוגדרה על ens18 \(http:\/\/10\.44\.9\.10:8080\) — השרת מתאתחל/);
  assert.equal(t.run('NET_DEPLOY.configured'),true);
  // ‏#1121: השלמה שנכשלה היא 500 — put זורק, שום דבר לא נרשם (NET_DEPLOY לא
  // מתהפך), והשגיאה מגיעה לטופס דרך ה-catch של sheet(); אין טוסט "עודכנה".
  t.run('NET_DEPLOY={configured:false,source:null,interface:null,url:null,hint:null}; toasts.length=0;');
  t.fixtures['/net/interfaces/ens18']=new Error('ההשלמה נכשלה: חומת אש: nft נפל');
  const thrown=await t.run('saveNic("ens18",{enabled:true,confirm:"ens18"}).then(()=>null,e=>e.message)');
  assert.equal(thrown,'ההשלמה נכשלה: חומת אש: nft נפל');
  assert.equal(t.run('NET_DEPLOY.configured'),false);
  assert.equal(t.run('toasts.length'),0);
  // תשובה בלי configured (שרת ישן / fixture גנרי) אינה "לא הוגדרה" — ראיה חיובית בלבד
  t=await loaded({'/net/interfaces':none});
  html=t.run('networkPage(0)'); assert.doesNotMatch(html,/רשת ההפצה לא הוגדרה/);
  html=t.run('networkPage(2)'); assert.match(card(html,'רשת ההפצה'),/class="empty">אין כרטיס מוגדר כרשת הפצה/); assert.doesNotMatch(html,/רשת ההפצה לא הוגדרה/);
  t.run('netEditDeploy("ens19")'); assert.doesNotMatch(t.run('sheets[sheets.length-1]').note,/בפעם הראשונה/);
  // כשההפצה מוגדרת (cli) — אין הערה ואין "בפעם הראשונה"
  t=await loaded({'/net/interfaces':none,'/net/deploy':{configured:true,source:'cli',interface:'ens19',url:'http://10.44.9.10:8080',hint:null}});
  assert.doesNotMatch(t.run('networkPage(0)'),/רשת ההפצה לא הוגדרה/);
  // ‏/net/deploy שלא נקרא — נאמר, לא "מוגדר"
  t=await loaded({'/net/interfaces':none,'/net/deploy':new Error('boom')});
  assert.match(t.run('networkPage(0)'),/\/net\/deploy לא נקרא[^<]*<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">boom<\/code>/);
});

// apple-pending-4 · 4: פיל ה-DHCP עבר מכותרת הסקירה לדף הרשת (התרשים)
test('network diagram header carries the DHCP pill (moved from the overview); the overview header has none',async()=>{
  const {run}=await loaded();
  const html=run('networkPage(0)');
  assert.match(html,/<div class="obj-name">רשת<span class="pill ok">DHCP פעיל<\/span>/,'#1300: the mockup wording; a DHCP that is not serving says its live label');
  assert.doesNotMatch(run('layout(pages.home,0)'),/DHCP: |DHCP חיצוני/);
});

test('stage 2: devices whose address is outside every NIC subnet are named in a warning under the diagram (restored from main)',async()=>{
  const t=await loaded();
  const none=t.run('netMapBody(netDiagramModel())');
  assert.doesNotMatch(none,/מחוץ לרשתות הכרטיסים/,'no orphans - no warning');
  t.run('NETW.nics=[]; NET=[{mac:"de:ad:be:ef:00:09",ip:"192.168.99.9",registered:false,last_seen:null}]');
  const model=JSON.parse(plain(t.run('JSON.stringify(netDiagramModel())')));
  assert.ok(model.orphans.some(o=>/de:ad:be:ef:00:09/.test(o.title)),'no lane at all - the unregistered device has nowhere to be drawn');
  const html=t.run('netMapBody(netDiagramModel())');
  assert.match(html,/class="note warn".*\d+ מחוברים עם כתובת מחוץ לרשתות הכרטיסים: .*de:ad:be:ef:00:09/);
  assert.match(t.run('netOrphansNote({orphans:[{title:"a"},{title:"b"}]})'),/2 מחוברים עם כתובת מחוץ לרשתות הכרטיסים: a · b/);
});

/* ---------- ‏#1264: רשת ההפצה על שני כרטיסים (bond, ‏balance-alb) ---------- */

const DEPLOY_CONSOLE={configured:true,source:'console',interface:'ens19',url:'http://10.44.9.10:8080',hint:null};
// ‏ens22 פנוי (לא מוגדר, בלי כתובת); ‏ens21 מנותק ופנוי; ‏ens18 ניהול; ‏ens20 proxy+trunk
const NICS_FREE=[...NICS,nic('ens22',{})];
const NETCFG_FREE={...NETCFG,interfaces:[...NETCFG.interfaces,cfgRow('ens22',{mode:'manual',mode_he:'לא מנוהל'})]};
const BONDED_NICS=[NICS[0],nic('bond0',{addresses:['10.44.9.10/24'],enabled:true,range_start:'10.44.9.100',range_end:'10.44.9.200',server_ip:'10.44.9.10',
    dhcp_live:{state:'serving',checked:true,detail:''},dhcp_live_label:'משרת'}),nic('ens19',{}),NICS[2],NICS[3],nic('ens22',{speed_mbps:1000})];
const bondView=(state,members)=>({name:'bond0',mode:'balance-alb',checked:state!=='unchecked',reason:state==='unchecked'?'/proc/net/bonding/bond0: No such file':'',state,members});
const BONDED_CFG=(bond)=>({...NETCFG,deploy_bond:bond,interfaces:[NETCFG.interfaces[0],cfgRow('bond0',{address:'10.44.9.10',live_addresses:['10.44.9.10/24'],slaves:['ens19','ens22']}),
    cfgRow('ens19',{mode:'manual',member_of:'bond0'}),NETCFG.interfaces[2],NETCFG.interfaces[3],cfgRow('ens22',{mode:'manual',member_of:'bond0'})]});
const bondedFixtures=(bond)=>({'/net/interfaces':BONDED_NICS,'/net/config':BONDED_CFG(bond),'/net/deploy':{...DEPLOY_CONSOLE,interface:'bond0'},
  '/net/interfaces/bond0/probe':{interface:'bond0',checked:true,servers:[]},'/net/interfaces/bond0/leases':{...LEASES,interface:'bond0'}});

test('#1264 deploy tab: "כרטיסים ברשת ההפצה" — each card, its role, link and state; one card down is "פעיל על כרטיס אחד"',async()=>{
  const {run}=await loaded(bondedFixtures(bondView('degraded',[{name:'ens19',role:1,state:'active'},{name:'ens22',role:2,state:'down'}])));
  const html=run('networkPage(2)'); balanced(html);
  const cards=card(html,'כרטיסים ברשת ההפצה');
  assert.match(cards,/<th>כרטיס<\/th><th>תפקיד<\/th><th>קישור<\/th><th>מצב<\/th>/);
  assert.match(row(cards,'>ens19<'),/כרטיס 1.*class="pill ok">פעיל</);
  assert.match(row(cards,'>ens22<'),/כרטיס 2.*class="pill err">מנותק</);
  assert.match(cards,/class="pill warn">פעיל על כרטיס אחד</);
  assert.match(cards,/שני הכרטיסים עובדים יחד על הכתובת <span class="mono">10\.44\.9\.10<\/span>/);
  assert.match(card(html,'רשת ההפצה'),/netEditDeploy\('bond0'\)/,'the deploy network is edited on the bond');
});

/* ‏#1264, מדידה 17/10 (מעבדת netns על TA, ‏balance-alb מעל veth של 1Gb, 10 סבבים של 1GB): זרם
   המולטיקאסט יוצא בכרטיס אחד בכל רגע (הקרנל מגבב לפי כתובת היעד, ועובר כרטיס רק באיזון של כל 10
   שניות), וניתוק כרטיס באמצע סבב עוצר אותו ל-0.5–3 שניות — והסבב מסתיים, ‏sha זהה בכל המקבלים.
   הטקסט אומר את זה, ולא "בלי הפסקה" או "בלי שירגישו". */
test('#1264 the bond text says what was measured: a round leaves on one card, and a cut card pauses it for seconds',async()=>{
  const {run}=await loaded(bondedFixtures(bondView('ok',[{name:'ens19',role:1,state:'active'},{name:'ens22',role:2,state:'active'}])));
  const cards=card(run('networkPage(2)'),'כרטיסים ברשת ההפצה');
  assert.match(cards,/סבב שיכפול יוצא בכרטיס אחד בכל רגע/);
  assert.match(cards,/סבב שרץ נעצר לשניות ספורות ומסתיים כרגיל/);
  assert.doesNotMatch(cards,/בלי הפסקה/);
  const t=await loaded({'/net/deploy':DEPLOY_CONSOLE,'/net/interfaces':NICS_FREE,'/net/config':NETCFG_FREE});
  t.run("netEditDeploy('ens19')");
  const help=sheetAt(t.run,0).fields.find(f=>f.id==='second').help;
  assert.match(help,/סבב שיכפול יוצא בכרטיס אחד/);
  assert.match(help,/סבב שרץ נעצר לשניות ספורות ומסתיים כרגיל/);
  assert.doesNotMatch(help,/בלי שירגישו/);
});

test('#1264 a bond state that was not read is "לא נבדק" on every card — never "פעיל"',async()=>{
  const {run}=await loaded(bondedFixtures(bondView('unchecked',[{name:'ens19',role:1,state:'unchecked'},{name:'ens22',role:2,state:'unchecked'}])));
  const cards=card(run('networkPage(2)'),'כרטיסים ברשת ההפצה');
  assert.doesNotMatch(cards,/>פעיל</);
  assert.equal((cards.match(/class="pill warn">לא נבדק</g)||[]).length,3,'two cards + the bond');
  assert.match(cards,/\/proc\/net\/bonding\/bond0: No such file/);
});

test('#1264 one deploy card: a single "כרטיס 1" row from its live link, and how to add a second',async()=>{
  const {run}=await loaded({'/net/deploy':DEPLOY_CONSOLE});
  const cards=card(run('networkPage(2)'),'כרטיסים ברשת ההפצה');
  assert.match(row(cards,'>ens19<'),/כרטיס 1.*1 Gb.*class="pill ok">פעיל</);
  assert.equal((cards.match(/<tr>/g)||[]).length,2,'header + one card');
  assert.match(cards,/אפשר להוסיף כרטיס שני/);
});

test('#1264 NIC table: a bond member says it is in the deploy network',async()=>{
  const {run}=await loaded(bondedFixtures(bondView('ok',[{name:'ens19',role:1,state:'active'},{name:'ens22',role:2,state:'active'}])));
  assert.match(run("netRoleLabel(NETW.nics.find(n=>n.name==='ens22'))"),/^הפצה · bond0( · |$)/);
  assert.doesNotMatch(run("netRoleLabel(NETW.nics.find(n=>n.name==='ens21'))"),/bond0/);
});

test('#1264 deploy window: "כרטיסים" — card 1 fixed, card 2 only free cards; changing only card 2 sends only /second',async()=>{
  const t=await loaded({'/net/deploy':DEPLOY_CONSOLE,'/net/interfaces':NICS_FREE,'/net/config':NETCFG_FREE});
  t.run("netEditDeploy('ens19')");
  const sh=sheetAt(t.run,0);
  assert.deepEqual(sh.fields.filter(f=>f.type==='section').map(f=>f.label),['רשת ההפצה','DHCP למחשבים','כרטיסים']);
  const c1=sh.fields.find(f=>f.id==='card1'), c2=sh.fields.find(f=>f.id==='second');
  assert.deepEqual(c1.options.map(o=>o.value),['ens19'],'replacing the first card is a reinstall — it is shown, not chosen');
  assert.deepEqual(c2.options.map(o=>o.value),['','ens21','ens22'],'no management, no proxy/trunk, no card that is configured or carries an address');
  assert.equal(c2.value,''); assert.equal(c2.options[0].label,'בלי כרטיס שני');
  assert.match(c2.help,/שני הכרטיסים עובדים יחד על אותה כתובת/);
  t.fixtures['/net/config/ens19/second']={ok:true,interface:'bond0',server_ip:'10.44.9.10',apply_error:null,deploy:{ok:true,url:'http://10.44.9.10:8080'},bond:{state:'ok'}};
  const v={network:'10.44.9.0',netmask:'255.255.255.0',range_start:'10.44.9.100',range_end:'10.44.9.200',lease_n:'12',lease_u:'h',card1:'ens19',second:'ens22'};
  assert.equal(await t.run(`sheets[0].onSubmit(${JSON.stringify(v)}).then(()=>null,e=>e.message)`),null);
  const writes=t.requests.filter(r=>r.method!=='GET');
  assert.deepEqual(writes.map(r=>[r.url,r.body]),[['/net/config/ens19/second',{second:'ens22'}]],'the network did not change — no deploy PUT, no dnsmasq restart');
  assert.match(t.run('toasts[toasts.length-1]'),/ens22 נוסף לרשת ההפצה — שני הכרטיסים פעילים יחד על 10\.44\.9\.10/);
  // ההעברה לא אומתה (השרת החזיר את הקבצים) — החלון נשאר פתוח עם הסיבה
  t.fixtures['/net/config/ens19/second']={ok:false,interface:'ens19',server_ip:'10.44.9.10',apply_error:'bond0 לא אומת: x. ההגדרה הקודמת על ens19 הוחזרה ואומתה',deploy:null,bond:null};
  assert.match(await t.run(`sheets[0].onSubmit(${JSON.stringify(v)}).then(()=>null,e=>e.message)`),/bond0 לא אומת/);
});

test('#1264 deploy window on the bond: card 2 is selected, and "בלי כרטיס שני" removes it; first-time setup has no cards section',async()=>{
  const t=await loaded(bondedFixtures(bondView('ok',[{name:'ens19',role:1,state:'active'},{name:'ens22',role:2,state:'active'}])));
  t.run("netEditDeploy('bond0')");
  const sh=sheetAt(t.run,0);
  assert.deepEqual(sh.fields.find(f=>f.id==='card1').options.map(o=>o.value),['ens19']);
  assert.equal(sh.fields.find(f=>f.id==='second').value,'ens22');
  t.fixtures['/net/config/bond0/second']={ok:true,interface:'ens19',server_ip:'10.44.9.10',apply_error:null,deploy:{ok:true,url:'http://10.44.9.10:8080'},bond:null};
  const v={network:'10.44.9.0',netmask:'255.255.255.0',range_start:'10.44.9.100',range_end:'10.44.9.200',lease_n:'12',lease_u:'h',card1:'ens19',second:''};
  assert.equal(await t.run(`sheets[0].onSubmit(${JSON.stringify(v)}).then(()=>null,e=>e.message)`),null);
  assert.deepEqual(t.requests.filter(r=>r.method!=='GET').map(r=>[r.url,r.body]),[['/net/config/bond0/second',{second:''}]]);
  assert.match(t.run('toasts[toasts.length-1]'),/הכרטיס השני הוסר — רשת ההפצה על ens19 בלבד/);
  const first=await loaded({'/net/deploy':{configured:false,source:'none',interface:null,url:null,hint:null}});
  first.run("netEditDeploy('ens19')");
  assert.equal(sheetAt(first.run,0).fields.some(f=>f.id==='second'),false,'the second card comes after the deploy network exists');
});

/* ---------- ‏#1274: אחרי כשל השלמה — "השלם הגדרה" באותו ‏/second ---------- */

const completeBtn=(cards)=>{const m=cards.match(/<button[^>]*onclick="([^"]+)"[^>]*>השלם הגדרה<\/button>/); return m ? unesc(m[1]) : null;};

test('#1274 the bond is up but the completion failed (/net/deploy still on card 1): "השלם הגדרה" sends the same /second on bond0, no typed word',async()=>{
  const t=await loaded({...bondedFixtures(bondView('ok',[{name:'ens19',role:1,state:'active'},{name:'ens22',role:2,state:'active'}])),'/net/deploy':DEPLOY_CONSOLE});
  const cards=card(t.run('networkPage(2)'),'כרטיסים ברשת ההפצה'); balanced(cards);
  const click=completeBtn(cards);
  assert.ok(click,'a completion that did not finish has a way to finish it from the console');
  assert.match(cards,/class="note warn"><span aria-hidden="true">●<\/span><span>ההגדרה לא הושלמה/);
  t.run(click);
  const sh=t.run('sheets.at(-1)');
  assert.equal(sh.submitLabel,'השלם הגדרה'); assert.equal((sh.fields||[]).length,0,'a confirm window, not a typed word');
  assert.match(sh.note,/השרת יאותחל/);
  t.fixtures['/net/config/bond0/second']={ok:false,interface:'bond0',server_ip:'10.44.9.10',apply_error:'חומת אש: nft -c נפל',deploy:{ok:false,errors:['חומת אש: nft -c נפל']},bond:{state:'ok'}};
  assert.match(await sh.onSubmit().then(()=>'',e=>e.message),/nft -c נפל/,'a second failure stays in the window, named');
  t.fixtures['/net/config/bond0/second']={ok:true,interface:'bond0',server_ip:'10.44.9.10',apply_error:null,deploy:{ok:true,url:'http://10.44.9.10:8080'},bond:{state:'ok'}};
  assert.equal(await sh.onSubmit().then(()=>'ok',e=>e.message),'ok');
  assert.deepEqual(t.requests.filter(r=>r.method!=='GET').map(r=>[r.url,r.body]),
    [['/net/config/bond0/second',{second:'ens22'}],['/net/config/bond0/second',{second:'ens22'}]],'the same endpoint, the same card — nothing else is sent');
  assert.match(t.run('toasts[toasts.length-1]'),/הושלמה/);
});

test('#1274 removing card 2 whose completion failed (/net/deploy still on bond0): "השלם הגדרה" sends /second with no card on the single card',async()=>{
  const t=await loaded({'/net/deploy':{...DEPLOY_CONSOLE,interface:'bond0'}});
  const click=completeBtn(card(t.run('networkPage(2)'),'כרטיסים ברשת ההפצה'));
  assert.ok(click);
  t.run(click);
  t.fixtures['/net/config/ens19/second']={ok:true,interface:'ens19',server_ip:'10.44.9.10',apply_error:null,deploy:{ok:true,url:'http://10.44.9.10:8080'},bond:null};
  assert.equal(await t.run('sheets.at(-1).onSubmit()').then(()=>'ok',e=>e.message),'ok');
  assert.deepEqual(t.requests.filter(r=>r.method!=='GET').map(r=>[r.url,r.body]),[['/net/config/ens19/second',{second:''}]]);
});

test('#1274 a completed deploy network has no "השלם הגדרה" — on the bond, on one card, and when /net/deploy was not read',async()=>{
  for(const over of [bondedFixtures(bondView('ok',[{name:'ens19',role:1,state:'active'},{name:'ens22',role:2,state:'active'}])),
                     {'/net/deploy':DEPLOY_CONSOLE},
                     {...bondedFixtures(bondView('ok',[{name:'ens19',role:1,state:'active'},{name:'ens22',role:2,state:'active'}])),'/net/deploy':new Error('db')}]) {
    const t=await loaded(over);
    assert.equal(completeBtn(card(t.run('networkPage(2)'),'כרטיסים ברשת ההפצה')),null);
  }
});
