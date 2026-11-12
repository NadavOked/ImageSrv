// ‏#1049 שלב ב': "בריאות המכונה" במגירת המחשב ובעמוד הבית — מרונדר בפועל
// (console.js + net.js ב-vm מול DOM מזערי ו-fetch מזויף), לא רק מחרוזות.
// עיקרון 5: null = "לא נבדק" (אפור), {error} = "לא הצלחנו לבדוק" (כתום),
// ערך = מוצג; verdicts ריק אינו "הכול תקין".
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const minutesAgo=(n)=>new Date(Date.now()-n*60000).toISOString();

const PROBE_GOOD={
  power:{on_battery:false,supply:'AC'},
  rtc:{hwclock_epoch:1758124800,system_epoch:1758124812,skew_seconds:-12},
  cpu:{model:'Intel Core i5-8500',cores:6,microcode:'0xf4',vulnerabilities:{meltdown:'Mitigation: PTI'}},
  memory:{total_bytes:8589934592,dimms:[{size_bytes:8589934592,speed_mts:2666,manufacturer:'Samsung'}],ecc:null},
  thermal:[{type:'x86_pkg_temp',temp_c:42},{type:'acpitz',temp_c:27}],
  nic:[{name:'eth0',speed_mbps:1000,duplex:'full',stats:{rx_crc_errors:0,rx_dropped:0,tx_errors:0,collisions:0},ip_conflict:{checked:true,duplicate:false}}],
  pci_without_driver:[],
  kernel:{lockdown:'[none] integrity confidentiality',taint:0},
  pstore:{crashed:false,files:[],excerpt:null},
  oem_key:'XXXXX-XXXXX-XXXXX-XXXXX-XXXXX',
  disks:[{name:'sda',nvme_smart:null,smart_errors:{count:0}},{name:'nvme0n1',nvme_smart:{critical_warning:0,percentage_used:5,media_errors:0,unsafe_shutdowns:2},smart_errors:null}],
  encryption:[],
  probe_seconds:1,
  netprobe:{cable:{skipped:'link up'},
            lldp:{switch:'sw-lab-1',chassis:'aa:bb:cc:dd:ee:ff',port:'Gi1/0/12',port_desc:'lab port',ttl:120}}};
const PROBE_BAD={
  power:{on_battery:true,supply:'BAT0'},
  rtc:{hwclock_epoch:1,system_epoch:100000,skew_seconds:-7200},
  cpu:{error:'cpuinfo empty'},
  memory:{total_bytes:8589934592,dimms:null,ecc:{ce_count:4,ue_count:1}},
  thermal:null,
  nic:[{name:'eth0',speed_mbps:100,duplex:'half',stats:{rx_crc_errors:7,rx_dropped:3,tx_errors:0,collisions:0},ip_conflict:{checked:true,duplicate:true}}],
  pci_without_driver:['8086:15bc:020000','10ec:8168:020000'],
  kernel:null,
  pstore:{crashed:true,files:['dmesg-ramoops-0'],excerpt:'panic: boom'},
  oem_key:{error:'MSDM too short'},
  disks:[{name:'nvme0n1',nvme_smart:{critical_warning:1,percentage_used:95,media_errors:2,unsafe_shutdowns:0},smart_errors:null}],
  encryption:[{node:'/dev/sda2',type:'BitLocker'},{node:'/dev/sda3',type:'crypto_LUKS'}],
  probe_seconds:2};
const VERDICTS_BAD=[
  {key:'battery',level:'err',text_he:'המחשב על סוללה — שיכפול/קליטה עלולים להיקטע'},
  {key:'rtc',level:'warn',text_he:'שעון החומרה סוטה ב-2 שעות — סוללת BIOS חשודה'},
  {key:'nvme:nvme0n1',level:'err',text_he:'NVMe nvme0n1: critical_warning=1 · 2 שגיאות מדיה'},
  {key:'pstore',level:'warn',text_he:'המחשב קרס לפני האתחול הזה'},
  {key:'ip_conflict:eth0',level:'err',text_he:'כפילות IP על eth0 — מחשב אחר ברשת עונה על הכתובת'},
  {key:'crc:eth0',level:'warn',text_he:'7 שגיאות CRC על eth0 — כבל או פורט חשודים'}];

function setup() {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const fixtures={
    '/groups':[{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:2,machines:3}],
    '/machines':[
      {mac:'a0:48:1c:8a:18:40',suffix:'מחשב 1',group_id:'grp_CLONERS',note:null,drawer_count:2,prompt:null,disks:[],disks_reported_at:minutesAgo(9),inventory:null,inventory_seen_at:null,
        probe:PROBE_GOOD,probe_seen_at:minutesAgo(3),probe_verdicts:[]},
      {mac:'78:ac:c0:9b:11:c2',suffix:'מחשב 2',group_id:'grp_CLONERS',note:null,drawer_count:3,prompt:null,disks:[],disks_reported_at:minutesAgo(9),inventory:null,inventory_seen_at:null,
        probe:PROBE_BAD,probe_seen_at:minutesAgo(1),probe_verdicts:VERDICTS_BAD},
      {mac:'78:ac:c0:9b:11:c3',suffix:'מחשב 3',group_id:'grp_CLONERS',note:null,drawer_count:3,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null,
        probe:null,probe_seen_at:null,probe_verdicts:[]}],
    '/net':[],'/disk-failures':[],'/shrink-records':[],'/monitor/machines':[],'/overview':{images:0,machines:3,storage:null,pulls:[],room:null,session:null},
    '/journal':[],'/health':[],'/ports':[],'/update':{},
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] ?? {ok:true}};
    }});
  for(const f of ['progress.js',...consoleFiles(),'net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",version:"v0.40.0",capabilities:{}}; current="machines"; sheet=()=>{}; confirmSheet=()=>{}; selectPageById=(id)=>{ globalThis.selected=id; current=id; };');
  for(const [name,key] of [['OVERVIEW','/overview'],['MACHINES','/machines'],['GROUPS','/groups'],['DISK_FAILURES','/disk-failures'],['SHRINK_RECORDS','/shrink-records'],['NET','/net'],['MONITOR_ROWS','/monitor/machines'],['HEALTH','/health']]) run(name+'='+JSON.stringify(fixtures[key]));
  return {run,node,requests};
}
function balanced(html) {
  const stack=[]; const voids=new Set(['input','br','hr','img','meta','link','source','wbr','path','rect','circle']);
  for(const m of html.matchAll(/<\/?([a-z][\w-]*)\b[^>]*>/gi)) {
    const tag=m[1].toLowerCase();
    if(m[0].endsWith('/>') || voids.has(tag)) continue;
    if(m[0].startsWith('</')) assert.equal(stack.pop(),tag,'Unbalanced '+m[0]); else stack.push(tag);
  }
  assert.deepEqual(stack,[],'Unclosed tags');
}
// ‏#1300: "בריאות המכונה" היא קבוצה בתוך לשונית "סקירה" (‏.grp), ואחריה לשונית "דיסקים"
const section=(html)=>html.slice(html.indexOf('<div class="grp">בריאות המכונה'),html.indexOf('<div class="sec" data-cnt',html.indexOf('<div class="grp">בריאות המכונה')));

test('a clean measured probe: the health group shows every field, green only where measured, grey "not checked" for null, no verdicts', () => {
  const {run,node}=setup();
  run("openMachineDetail('a0:48:1c:8a:18:40')");
  const html=node('#drawerBody').innerHTML; balanced(html);
  const h=section(html);
  assert.match(h,/<div class="grp">בריאות המכונה <small>\(נדגם לפני 3 דק&#39;\)<\/small><\/div>/,'sampled-ago in the group header');
  assert.doesNotMatch(h,/class="note (err|warn)"/,'no verdicts on a clean probe');
  assert.match(h,/<span class="k">חשמל<\/span><span class="v"><span class="st ok">חשמל \(AC\)</);
  assert.match(h,/<span class="k">שעון<\/span><span class="v"><span class="st ok">סטייה 12 שנ&#39;</);
  assert.match(h,/<span class="k">מעבד<\/span><span class="v">Intel Core i5-8500 · 6 ליבות · מיקרוקוד <span class="mono">0xf4</);
  assert.match(h,/<span class="k">זיכרון<\/span><span class="v"><bdi dir="ltr">8.0 GB<\/bdi> · 1 DIMMs · <span class="muted">ECC לא נבדק</,'ecc null = grey, not "ECC ok"');
  assert.match(h,/<span class="k">טמפ&#39; מקס&#39;<\/span><span class="v"><span class="st ok">42°C</,'max over zones');
  assert.match(h,/<span class="k">רשת<\/span><span class="v"><span class="mono">eth0<\/span> 1000Mb\/s full · CRC 0 · dropped 0 · IP ללא כפילות</);
  assert.match(h,/<span class="k">מתג ופורט<\/span><span class="v"><span class="mono" dir="ltr" title="lab port · aa:bb:cc:dd:ee:ff">sw-lab-1 · Gi1\/0\/12/);
  assert.match(h,/<span class="k">כבל<\/span><span class="v"><span class="muted">לא נבדק — יש קישור/);
  assert.match(h,/<span class="k">NVMe<\/span><span class="v"><span class="mono">nvme0n1<\/span>: <span class="st ok">5% בלאי · 0 שגיאות מדיה</,'sda (nvme_smart null) is left out; nvme0n1 shown');
  assert.match(h,/<span class="k">קריסה קודמת<\/span><span class="v"><span class="muted">לא</);
  assert.match(h,/<span class="k">מפתח OEM<\/span><span class="v"><span class="mono" dir="ltr">XXXXX-XXXXX-XXXXX-XXXXX-XXXXX</);
  assert.match(h,/<span class="k">PCI בלי דרייבר<\/span><span class="v"><span class="muted">אין</);
  assert.match(h,/<span class="k">הצפנה<\/span><span class="v"><span class="muted">אין</);
});

test('a bad probe: verdicts above the group in red/orange, error fields orange "could not check", null fields grey, PCI without driver links to drivers', () => {
  const {run,node}=setup();
  run("openMachineDetail('78%3Aac%3Ac0%3A9b%3A11%3Ac2')");
  const html=node('#drawerBody').innerHTML; balanced(html);
  const h=section(html);
  assert.equal((h.match(/class="note err"/g)||[]).length,3,'battery + nvme + ip conflict are red');
  assert.equal((h.match(/class="note warn"/g)||[]).length,3,'rtc + pstore + crc are orange');
  assert.match(h,/note err"[^]*המחשב על סוללה — שיכפול\/קליטה עלולים להיקטע/);
  assert.match(h,/note warn"[^]*שעון החומרה סוטה ב-2 שעות — סוללת BIOS חשודה/);
  assert.ok(h.indexOf('class="note') < h.indexOf('<div class="kv">'),'verdicts come before the group');
  assert.match(h,/<span class="k">חשמל<\/span><span class="v"><span class="st err">על סוללה \(BAT0\)</);
  assert.match(h,/<span class="k">שעון<\/span><span class="v"><span class="st warn">סוטה ב-2 שעות — סוללת BIOS חשודה</);
  assert.match(h,/<span class="k">מעבד<\/span><span class="v"><span class="st warn">לא הצלחנו לבדוק: cpuinfo empty</,'error = orange, not grey');
  assert.match(h,/<span class="muted">DIMMs לא נבדקו<\/span> · <span class="st err">ECC: 1 לא-מתוקנות</);
  assert.match(h,/<span class="k">טמפ&#39; מקס&#39;<\/span><span class="v"><span class="muted">לא נבדק</,'thermal null = grey');
  assert.match(h,/eth0<\/span> 100Mb\/s half · <span class="st warn">CRC 7<\/span> · dropped 3 · <span class="st err">כפילות IP</);
  assert.match(h,/nvme0n1<\/span>: <span class="st err">95% בלאי · 2 שגיאות מדיה · critical_warning=1</);
  assert.match(h,/<span class="st warn">קרס לפני האתחול הזה<\/span> <span class="mono">dmesg-ramoops-0<\/span> <details class="inline"><summary>קטע<\/summary><pre class="ata-log">panic: boom</);
  assert.match(h,/<span class="k">מפתח OEM<\/span><span class="v"><span class="st warn">לא הצלחנו לבדוק: MSDM too short</);
  assert.match(h,/<span class="st warn">2 התקנים<\/span> <span class="mono" title="8086:15bc:020000 10ec:8168:020000">8086:15bc:020000 · 10ec:8168:020000<\/span> · <a role="link" tabindex="0" onclick="selectPageById\('drivers'\)">לדף הדרייברים</);
  assert.match(h,/<span class="k">הצפנה<\/span><span class="v">BitLocker <span class="mono">\/dev\/sda2<\/span> · LUKS <span class="mono">\/dev\/sda3</);
});

test('a machine that never reported a probe says so — not an empty green group', () => {
  const {run,node}=setup();
  run("openMachineDetail('78:ac:c0:9b:11:c3')");
  const h=section(node('#drawerBody').innerHTML);
  assert.match(h,/<div class="grp">בריאות המכונה<\/div>/,'no "sampled" suffix without a sample');
  assert.match(h,/המכונה מעולם לא דיווחה בדיקת מכונה \(סוכן ישן\)/);
  assert.doesNotMatch(h,/class="kv"/); assert.doesNotMatch(h,/st ok/);
});

test('#1048 netprobe rows: unheard grey, pending, listen error orange, cable open red', () => {
  const {run}=setup();
  const health=(np)=>run('machineHealthHtml({probe:'+JSON.stringify({...PROBE_GOOD,netprobe:np})+'})');
  assert.match(health({cable:{skipped:'link up'},lldp:{unheard:true,waited_s:35}}),
    /<span class="k">מתג ופורט<\/span><span class="v"><span class="muted">לא נקלט \(35ש'\)/);
  const pending=health({pending:true});
  assert.match(pending,/<span class="k">מתג ופורט<\/span><span class="v"><span class="muted">ממתין/);
  assert.match(pending,/<span class="k">כבל<\/span><span class="v"><span class="muted">ממתין/);
  assert.match(health({cable:null,lldp:{error:'no such interface'}}),/<span class="st warn">לא הצלחנו להאזין/);
  assert.match(health({cable:{status:'open',pairs:[{pair:'B',code:'Open',length_m:12}]},lldp:null}),
    /<span class="k">כבל<\/span><span class="v"><span class="st err">זוג B פתוח ב-12 מ&#39;/);
  const notes=run('machineVerdictsHtml({probe_verdicts:[{key:"cable",level:"err",text_he:"כבל פגום: זוג B פתוח ב-12 מטר"}]})');
  assert.match(notes,/class="note err"[^]*כבל פגום: זוג B פתוח ב-12 מטר/);
});

test('home "needs attention": every verdict is a note with the machine name and a link to its drawer; a clean machine adds nothing', () => {
  const {run}=setup();
  run('HOME.net=[]; HOME.nodes=[]; HOME.err={};');
  const html=run('homeAttention()'); balanced(html);
  assert.equal((html.match(/class="attn-i (err|warn)"/g)||[]).length,5,'five verdicts of מחשב 2, none for מחשב 1/3');
  assert.doesNotMatch(html,/NVMe/,'Nadav 18/10: a disk SMART verdict is in the machine drawer, not on the overview');
  assert.match(html,/attn-i err"[^]*<b>מחשב 2<\/b> — המחשב על סוללה — שיכפול\/קליטה עלולים להיקטע\.<\/div><button class="btn sm flat" onclick="openMachineDetail\('78%3Aac%3Ac0%3A9b%3A11%3Ac2'\)">פרטי המכונה</,'#1300: the action is a button in its own column');
  assert.match(html,/attn-i warn"[^]*<b>מחשב 2<\/b> — 7 שגיאות CRC על eth0/);
  assert.doesNotMatch(html,/מחשב 1|מחשב 3/);
  assert.match(html,/<span class="cnt">5<\/span>/);
});
