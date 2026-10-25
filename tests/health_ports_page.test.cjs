// ‏#954 גל 5: בריאות ושירותים (health.md) + רשת › פורטים (network-ports.md).
// בלי דפדפן: console.js + net.js + progress.js רצים ב-vm מול DOM מזערי ו-fetch מזויף.
// עיקרון 5: "לא נבדק" (unknown) ≠ "כבוי" (off) ≠ "לא מאזין" (bad) — שלושה צבעים;
// כל endpoint שלא נקרא הוא מצב משלו, לא "אין".
// הכרעות נדב 17/09: 06:12 המתגים בפורטים ולא בבריאות; 06:25 מתג בכל שורה, 🔒 = הקלדת שם.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

const MAC1='a0:48:1c:8a:18:40', MAC2='78:ac:c0:9b:11:c2';
const check=(id,label,state,detail)=>({id,label,state,detail});
const HEALTH=[
  check('server','השרת בכתובת ההפצה','ok','http://10.44.9.10:8080 עונה על תפריט האתחול'),
  check('udp_sender','מולטיקאסט (udp-sender)','warn','udp-sender לא נמצא ב-PATH'),
  check('tftp_port','פורט 69 (TFTP)','bad','אף אחד לא מגיש TFTP — מחשבים לא יעלו ב-PXE'),
  check('identity','זהות מכונה','off','שומר הזהות כבוי'),
  check('shim_fresh','עדכניות ה-shim','unknown','לא ניתן לקרוא /usr/lib/shim — לא נבדק'),
  check('agent_loops','מחשבים שנופלים לסוכן','bad','2 מחשבים בעשר הדקות האחרונות'),
  check('agent_loop:'+MAC1,'מחשבי שיכפול מחשב 1','bad','3 פעמים · נראה לאחרונה לפני 2 דק\''),
  check('agent_loop:'+MAC2,MAC2,'bad','2 פעמים · נראה לאחרונה לפני 4 דק\''),
  check('off_vlan','מחשבים שפונים מרשת אחרת','ok','אף פנייה מרשת שאינה 10.44.9.0/24'),
];
const port=(id,name,p,proto,state,detail,extra={})=>({id,name,port:p,proto,desc:'desc '+id,target:'target '+id,state,detail,note:'לפתוח ב-FW: '+p,...extra});
const PORTS_OLD=[
  port('tftp','TFTP','69','udp','ok','dnsmasq מגיש'),
  port('http_boot','HTTP','8080','tcp','ok','עונה על תפריט האתחול'),
  port('http_console','HTTP','8081','tcp','off','אין hook שקורא את ההאזנה על הפורט הזה — לא אומת'),
  port('pxe_proxy','PXE','4011','udp','off','אין hook — לא אומת'),
  port('multicast','Multicast','9000–9001','udp','off','אין udp-sender חי — ואין סבב'),
  port('monitor','Monitor (RFB)','5900','tcp','off','המתג monitor:stations כבוי'),
];
const nic=(name,extra={})=>({name,mac:'00:11:22:33:44:'+name.slice(-2),state:'up',present:true,addresses:['10.0.0.1/24'],enabled:false,proxy:false,trunk:false,
  range_start:'',range_end:'',netmask:'255.255.255.0',gateway:'',dns:[],lease:'12h',server_ip:'',description:'',
  dhcp_live:{state:'off'},dhcp_live_label:'כבוי',dhcp_diverged:false,...extra});
const NICS=[nic('ens18'),
  nic('ens19',{enabled:true,range_start:'10.44.9.100',range_end:'10.44.9.200',server_ip:'10.44.9.10',dhcp_live:{state:'serving'},dhcp_live_label:'משרת'}),
  nic('ens20',{proxy:true,dhcp_live:{state:'serving'},dhcp_live_label:'proxy פעיל'})];

function setup(over={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},hasAttribute(){return false;},getAttribute(){return null;},closest(){return {style:{}};},focus(){}});
    return nodes.get(key);
  }
  const fixtures={'/health':HEALTH,'/update':{current:'v0.34.0',enabled:true,previous:'v0.33.0',server_name:'srv'},'/update/status':{state:'idle'},
    '/update/check':{at:`${new Date().toISOString().slice(0,10)}T06:00:00+00:00`,current:'v0.34.0',latest:'v0.35.0',available:true,reason:''},   // "היום" = תאריך UTC של היום, לא תאריך קשיח (נפל ב-20/09 על 19/09)
    '/ports':PORTS_OLD,'/monitor/settings':{port:5900,enabled:false},'/net/interfaces':NICS,'/net/proxy-support':{read:true,version:'2.90',verified:false,broken:true,reason:'dnsmasq 2.90 — proxy שבור'},
    '/machines':[{mac:MAC1,suffix:'מחשב 1',group_id:'grp_CLONERS',disks:null}],'/groups':[{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:1}],
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
      // ‏#1000: כותרות תשובה לפי נתיב (fixtures.__headers[key][name]) — X-Health-Checked-At
      const headers={get:(name)=>(fixtures.__headers && fixtures.__headers[key] && fixtures.__headers[key][name]) || null};
      if(f instanceof Error) return {status:500,ok:false,headers,json:async()=>({detail:f.message})};
      if(f===403) return {status:403,ok:false,headers,json:async()=>({detail:'admin only'})};
      return {status:200,ok:true,headers,json:async()=>f ?? {ok:true,verified:true,apply_error:null}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={timezone:"UTC",username:"admin",role:"admin",server_name:"srv",version:"v0.34.0",capabilities:{}}; globalThis.sheets=[]; globalThis.toasts=[]; globalThis.realSheet = sheet; sheet = o => { sheets.push(o); }; toast=(m)=>{ toasts.push(m); }; renderCurrent=()=>{ globalThis.rendered=(globalThis.rendered||0)+1; }; globalThis.selected=[]; selectPageById=(id)=>{ selected.push(id); current=id; }; openMachineDetail=(m)=>{ globalThis.openedMac=m; };');
  run('MACHINES='+JSON.stringify(fixtures['/machines'])+'; GROUPS='+JSON.stringify(fixtures['/groups'])+';');
  return {run,requests,fixtures,ctx};
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
const cell=(html,needle,span=700)=>{const i=html.indexOf(needle); assert.ok(i>=0,'missing: '+needle); return html.slice(i,i+span);};
const row=(html,needle)=>{const i=html.indexOf(needle); assert.ok(i>=0,'missing row: '+needle); const s=html.lastIndexOf('<tr',i); return html.slice(s,html.indexOf('</tr>',i)+5);};

/* ---------- בריאות ושירותים ---------- */

test('health is an own page without tabs; ports is an own page; loaders wired',()=>{
  const {run}=setup();
  assert.equal(run('pages.health.own'),true); assert.equal(run('pages.health.tabs.length'),0);
  assert.equal(run('pages.health.load===loadHealth'),true);
  assert.equal(run('pages.ports.own'),true); assert.equal(run('pages.ports.load===loadPorts'),true);
  assert.equal(run('typeof loadSsh'),'undefined','loadSsh moved into the ports page');
  assert.equal(run('health()'),run('pagePlaceholder()'),'placeholder before the first read');
});

test('the checks table shows all five states with their own colour — "not checked" is never green, "off" is grey',async()=>{
  const {run}=setup(); run('current="health"');
  await run('loadHealth()');
  const html=run('health()'); balanced(html);
  assert.match(html,/<table class="dg">/); assert.match(html,/<th>בדיקה<\/th><th>מצב<\/th><th>פירוט<\/th>/,'#1300: console-full.html P.health');
  assert.match(row(html,'השרת בכתובת ההפצה'),/class="pill ok">תקין</);
  assert.match(row(html,'מולטיקאסט (udp-sender)'),/class="pill warn">אזהרה</);
  assert.match(row(html,'פורט 69 (TFTP)'),/class="pill err">תקלה</);
  const unk=row(html,'עדכניות ה-shim');
  assert.match(unk,/class="pill unk">לא נבדק</); assert.doesNotMatch(unk,/pill ok/);
  assert.equal(run('healthStatusClass("unknown")'),'unk'); assert.equal(run('healthStatusLabel("unknown")'),'לא נבדק');
  // פירוט — ‏#1300: שורה קצרה (עד " — " הראשון); ה-detail של השרת כלשונו נשאר מאחורי "פרטים"
  assert.match(row(html,'פורט 69 (TFTP)'),/<span class="wrap">אף אחד לא מגיש TFTP<\/span><details class="tech"><summary>פרטים<\/summary><div>אף אחד לא מגיש TFTP — מחשבים לא יעלו ב-PXE<\/div><\/details>/);
  // הכותרת: כמה בדיקות ומתי (הספירה לפי מצב — ב-KPI)
  assert.match(html,/<div class="obj-sub"[^>]*>9 בדיקות · זמן המדידה לא נמסר<\/div>/,'#1000: no X-Health-Checked-At header → named, not the browser clock');
  assert.match(html,/class="pill err">תקלה</);
  assert.match(html,/onclick="refreshPage\(\)"><svg[^]*?<\/svg> רענון</,'refreshPage runs pages.health.load (loadHealth) with feedback');
  // בלי מתגים ובלי הלשוניות הישנות
  assert.doesNotMatch(html,/ssh-body|class="switch|role="switch"/);
  assert.doesNotMatch(html,/role="tablist"/);
});

test('stage 3: the update card and backup are no longer on the health page (they live in settings)',async()=>{
  const {run}=setup(); run('current="health"');
  await run('loadHealth()');
  const html=run('health()');
  assert.doesNotMatch(html,/healthUpdateCheck|confirmUpdateApply|confirmSettingsBackup|עדכונים/);
});

test('stage 3: KPI row (תקינים / באזהרה / תקלות) + "לא נבדקו" tile; unchecked is its own number, never folded into ok',async()=>{
  const {run}=setup(); run('current="health"');
  await run('loadHealth()');
  const html=run('health()'); balanced(html);
  const k=cell(html,'class="c12 kpis"',2600);
  assert.match(k,/<div class="kpi ok"><div class="l">תקינים<\/div><div class="v"><bdi dir="auto">2</);
  assert.match(k,/<div class="kpi warn"><div class="l">באזהרה<\/div><div class="v"><bdi dir="auto">1</);
  assert.match(k,/<div class="kpi err"><div class="l">תקלות<\/div><div class="v"><bdi dir="auto">4</);
  assert.match(k,/<div class="kpi warn"><div class="l">לא נבדקו<\/div><div class="v"><bdi dir="auto">1</,'#1300: three KPIs like the mockup — plus "not checked" whenever there is one (principle 5)');
  assert.doesNotMatch(k,/כבויים \/ לא ישימים/,'#1300: off / n/a stay in their rows, not a tile');
  const clean=setup({'/health':[check('server','השרת','ok','עונה'),check('x','ssh','off','כבוי')]}); clean.run('current="health"');
  await clean.run('loadHealth()');
  assert.equal((clean.run('health()').match(/<div class="kpi /g)||[]).length,3,'nothing unchecked → exactly the three KPIs of the mockup');
  // לא נקרא — אין KPI בכלל (לא "0 תקינים")
  const bad=setup({'/health':new Error('boom')}); bad.run('current="health"');
  return bad.run('loadHealth()').then(()=>{ assert.doesNotMatch(bad.run('health()'),/class="c12 kpis"/); });
});

test('stage 3: "תקלות פתוחות" lists bad, warn and unknown (worst first) — empty state only when every check was checked and none failed',async()=>{
  const {run}=setup(); run('current="health"');
  await run('loadHealth()');
  const html=run('health()');
  const card=html.slice(html.indexOf('תקלות פתוחות'),html.indexOf('<span>שירותים'));
  assert.ok(card.indexOf('פורט 69 (TFTP)')<card.indexOf('מולטיקאסט (udp-sender)'),'errors before warnings');
  assert.ok(card.indexOf('מולטיקאסט (udp-sender)')<card.indexOf('עדכניות ה-shim'),'warnings before unchecked');
  assert.match(cell(card,'עדכניות ה-shim',400),/pill unk">לא נבדק</);
  assert.doesNotMatch(card,/class="empty"/);
  assert.doesNotMatch(card,/SSH בתחנות/,'off is not an open fault');
  // הכול תקין → מצב ריק
  const allOk=setup({'/health':[check('server','השרת','ok','עונה'),check('x','ssh','off','כבוי')]}); allOk.run('current="health"');
  return allOk.run('loadHealth()').then(()=>{
    const h=allOk.run('health()'); assert.match(cell(h,'תקלות פתוחות',400),/class="empty">אין תקלות — כל הבדיקות נבדקו/);
    // בדיקה אחת שלא נבדקה — אינה "אין תקלות"
    const unk=setup({'/health':[check('server','השרת','ok','עונה'),check('shim','shim','unknown','לא נבדק')]}); unk.run('current="health"');
    return unk.run('loadHealth()').then(()=>{ const h2=unk.run('health()'); assert.doesNotMatch(cell(h2,'תקלות פתוחות',600),/אין תקלות/); });
  });
});

test('per-machine loop rows are grouped under "לולאות אתחול" right after the summary row, with a details action for a registered machine',async()=>{
  const {run}=setup(); run('current="health"');
  await run('loadHealth()');
  const html=run('health()');
  const order=['מחשבים שנופלים לסוכן','לולאות אתחול · 2','מחשבי שיכפול מחשב 1',MAC2,'מחשבים שפונים מרשת אחרת'].map((s)=>html.indexOf(s));
  assert.ok(order.every((v,i)=>v>=0 && (i===0 || v>order[i-1])),'order: '+order.join(','));
  assert.match(html,/<tr class="group"><td colspan="4">לולאות אתחול · 2<\/td><\/tr>/);
  assert.match(row(html,'מחשבי שיכפול מחשב 1'),/grp-kid/);
  assert.match(row(html,'מחשבי שיכפול מחשב 1'),new RegExp("openMachineDetail\\('"+encodeURIComponent(MAC1)+"'\\)"));
  assert.doesNotMatch(row(html,MAC2),/openMachineDetail/,'unregistered MAC has no drawer');
  assert.match(row(html,'פורט 69 (TFTP)'),/selectPageById\('ports'\)/,'a check points to the page where it is fixed');
});

test('health pill follows the worst state: warn-only, unknown-only, off-only, all ok, empty',async()=>{
  const pill=async(checks)=>{const {run}=setup({'/health':checks}); run('current="health"'); await run('loadHealth()'); return run('health()').match(/class="pill ([a-z]*)">([^<]*)</).slice(1,3);};
  assert.deepEqual(await pill([check('a','a','ok',''),check('b','b','warn','')]),['warn','אזהרה']);
  assert.deepEqual(await pill([check('a','a','ok',''),check('b','b','unknown','')]),['warn','לא נבדק']);
  assert.deepEqual(await pill([check('a','a','ok',''),check('b','b','off','')]),['','חלק כבוי']);
  assert.deepEqual(await pill([check('a','a','ok','')]),['ok','הכל תקין']);
  assert.deepEqual(await pill([]),['','אין בדיקות']);
});

test('a fresh server has one needs-attention item: configure the deployment network',()=>{
  const {run}=setup();
  const fresh=[
    check('deploy_net','הגדר את רשת ההפצה','warn','בחר כרטיס בדף הרשת'),
    check('dhcp_port','פורט 67 (DHCP)','not_applicable','לא ישים עד שתוגדר רשת ההפצה'),
    check('identity','זהות מכונה','not_applicable','לא ישים עד שתוגדר רשת ההפצה'),
  ];
  run('HEALTH='+JSON.stringify(fresh)+'; HOME.net=[]; HOME.nodes=[]; DISK_FAILURES=[]; SHRINK_RECORDS=[]; MACHINES=[]; SECONDARY_STATUS={}');
  const html=run('homeAttention()');
  assert.match(html,/<span class="cnt">1<\/span>/);
  assert.match(html,/<b>הגדר את רשת ההפצה<\/b> — בחר כרטיס בדף הרשת/,'a Hebrew detail stays inline');
  assert.match(html,/openNetwork\(2\)/);
  assert.doesNotMatch(html,/פורט 67|זהות מכונה/);
});

test('/health that cannot be read is its own state — red note, "לא נקרא" pill, not a placeholder and not an empty table',async()=>{
  const {run}=setup({'/health':new Error('DB down')}); run('current="health"');
  await run('loadHealth()');
  const html=run('health()');
  assert.notEqual(html,run('pagePlaceholder()'));
  assert.match(html,/class="pill err">לא נקרא</); assert.match(html,/note err.*DB down/);
  assert.doesNotMatch(html,/אין בדיקות/);
  assert.match(run('toasts.join("|")'),/DB down/);
});

test('stage 3: update card (moved to settings): installed/previous/last-check, check → apply button with a plain confirm (#1192), revert with a red-button confirm (#1778)',async()=>{
  const {run,requests}=setup(); run('current="settings"');
  await run('loadSettingsData()');
  let html=run('settings()');
  const card=cell(html,'<span>עדכונים',2400);
  // סידור ההגדרות (נדב 17/10): "עדכונים ותחזוקה" — שורה לכל דבר: מה מותקן, ומה מצאה הבדיקה האחרונה + "בדוק עכשיו"
  assert.match(card,/<dt[^>]*>גרסה מותקנת<\/dt><dd[^>]*><span class="sv mono" dir="ltr">v0\.34\.0<\/span>/);
  assert.match(card,/<dt>בדיקה אחרונה<\/dt><dd><span class="sv">מעולם לא נבדק בשרת הזה<\/span>/,'#1000: last_check null = never checked on this server');
  assert.match(card,/onclick="healthUpdateCheck\(\)">בדוק עכשיו</);
  assert.doesNotMatch(card,/החל עדכון/,'no apply before a check found something');
  assert.match(card,/confirmUpdateRevert\(UPDATE_INFO\.previous\)">חזור ל-v0\.33\.0</);
  await run('healthUpdateCheck()');
  assert.ok(requests.some((r)=>r.url==='/update/check' && r.method==='POST'));
  html=run('settings()');
  assert.match(cell(html,'<span>עדכונים',3200),/קיימת גרסה חדשה: <b class="mono">v0\.35\.0<\/b> · נבדק היום 06:00/,'#1000: the time is the server `at`, not the browser clock');
  // ‏#1192 (נדב 21/09): עדכון = דיאלוג אישור בלבד, בלי הקלדת שם השרת (הפעולה הפיכה: "חזור ל-").
  assert.match(html,/confirmUpdateApply\(UPDATE_INFO\.latest\)">החל עדכון v0\.35\.0</);
  assert.doesNotMatch(html,/הקלדת שם השרת\)/,'#1192: no typed-name hint on the apply button');
  run("confirmUpdateApply(UPDATE_INFO.latest)");
  const s=run('sheets.at(-1)');
  assert.equal(s.danger,true); assert.equal(s.verify,undefined,'#1192: no text field to type');
  assert.match(s.note,/<ul class="n-impact">[^]*השרת יופעל מחדש/,'consequences are an impact list in the note (#1301: the approved window)');
  await s.onSubmit();
  const apply=requests.find((r)=>r.url==='/update/apply');
  assert.deepEqual(apply.body,{tag:'v0.35.0'},'#1192: no confirm_name');
  // ‏#1778 (הכרעת #1270): "חזור ל-…" — אותו חלון עם כפתור אדום, בלי שדה הקלדה ובלי שם שרת בגוף.
  run("confirmUpdateRevert(UPDATE_INFO.previous)");
  const r=run('sheets.at(-1)');
  assert.equal(r.danger,true); assert.equal(r.verify,undefined,'#1778: no text field to type'); assert.equal(r.fields,undefined);
  assert.equal(r.sub,'v0.33.0');
  await r.onSubmit();
  const revert=requests.find((q)=>q.url==='/update/revert');
  assert.equal(revert.method,'POST'); assert.deepEqual(revert.body,{},'#1778: no confirm_name');
});

test('update card: switch off → note with a link to settings and no buttons; failed check ≠ "no update"; status line; /update unreadable',async()=>{
  let s=setup({'/update':{current:'v0.34.0',enabled:false,previous:null,server_name:'srv'}}); s.run('current="settings"');
  await s.run('loadSettingsData()');
  let card=cell(s.run('settings()'),'<span>עדכונים',2400);
  // ‏#1300: המתג "עדכון גרסה מהקונסולה" יושב בקבוצה הזו עצמה — מתג בסוף השורה (סידור 17/10)
  assert.match(card,/<label class="msw solo" id="srow-update_enabled"><input type="checkbox" role="switch" aria-label="עדכון גרסה מהקונסולה" onchange=/,'off = not checked');
  // #1128: the settings-backup row does not depend on the update switch; no update buttons though
  assert.doesNotMatch(card,/healthUpdateCheck|confirmUpdateApply|confirmUpdateRevert/);
  assert.match(card,/גיבוי הגדרות<\/dt><dd[^>]*><button[^>]*onclick="confirmSettingsBackup\(\)"/,'backup never depends on the update switch');
  assert.doesNotMatch(card,/חזור ל-/,'no previous version → no revert');
  assert.ok(!s.requests.some((r)=>r.url==='/update/status'),'status is not asked while the switch is off');
  s=setup({'/update/check':new Error('no route to github'),'/update/status':{state:'running',tag:'v0.35.0',verified:false}}); s.run('current="settings"');
  await s.run('loadSettingsData()'); await s.run('healthUpdateCheck()');
  card=cell(s.run('settings()'),'<span>עדכונים',3200);
  assert.match(card,/הבדיקה נכשלה: no route to github/); assert.doesNotMatch(card,/החל עדכון|אין חדש/);
  assert.match(card,/class="st run">העדכון ל-v0\.35\.0 הופעל — ממתין לאתחול השרת/);
  s=setup({'/update':new Error('403')}); s.run('current="settings"');
  await s.run('loadSettingsData()');
  assert.match(cell(s.run('settings()'),'<span>עדכונים',900),/גרסה מותקנת<\/dt><dd[^>]*><span class="muted">לא נקרא: 403<\/span>/,'unread is said, not an empty version');
});

test('#1277: the check shows the new tag as signed / unsigned / not checked — an unsigned tag disables apply, "not checked" never reads as signed',async()=>{
  const check=(signature)=>({at:`${new Date().toISOString().slice(0,10)}T06:00:00+00:00`,current:'v0.34.0',latest:'v0.35.0',available:true,reason:null,...(signature?{signature}:{})});
  const card=async(signature)=>{
    const s=setup({'/update/check':check(signature)}); s.run('current="settings"');
    await s.run('loadSettingsData()'); await s.run('healthUpdateCheck()');
    return cell(s.run('settings()'),'<span>עדכונים',2400);
  };
  let c=await card({state:'signed',detail:'imagectl-release SHA256:abc'});
  // ‏#1300: הכרטיס אומר "קיימת גרסה חדשה" (לא "יש עדכון: א → ב"); החתימה חייבת להופיע גם בו.
  assert.match(c,/קיימת גרסה חדשה: <b class="mono">v0\.35\.0<\/b>[^<]* · חתימה: חתום</);
  assert.match(c,/class="muted"[^>]*>imagectl-release SHA256:abc<\/small>/,'the signer fingerprint is the secondary line');
  assert.match(c,/<button class="btn primary" onclick="confirmUpdateApply\(UPDATE_INFO\.latest\)">החל עדכון v0\.35\.0</);
  c=await card({state:'unsigned',detail:'unsigned: חתום במפתח שאינו ב-release-signers (SHA256:zzz)'});
  assert.match(c,/חתימה: לא חתום</); assert.match(c,/SHA256:zzz/);
  assert.match(c,/<button class="btn primary" disabled title="התג v0\.35\.0 אינו חתום[^"]*"[^>]*>החל עדכון v0\.35\.0</,'the server would refuse it — so the button does too');
  c=await card({state:'unchecked',detail:'הורדת התג v0.35.0 נכשלה: timeout'});
  assert.match(c,/חתימה: לא נבדק</); assert.doesNotMatch(c,/חתימה: חתום/);
  assert.doesNotMatch(c,/primary" disabled/,'"not checked" stays clickable — server-upgrade.sh decides');
  c=await card(null);
  assert.match(c,/חתימה: לא נבדק</,'a stored check from before #1277 has no signature — "not checked", never "signed"');
});

test('#1273: while an update is running the apply and revert buttons are disabled; a verified update enables them again',async()=>{
  let s=setup({'/update/status':{state:'running',tag:'v0.35.0',verified:false}}); s.run('current="settings"');
  await s.run('loadSettingsData()'); await s.run('healthUpdateCheck()');
  let card=cell(s.run('settings()'),'<span>עדכונים',3600);
  assert.match(card,/<button class="btn primary" disabled[^>]*>החל עדכון v0\.35\.0</,'apply is disabled while running');
  assert.match(card,/<button class="btn danger" disabled[^>]*>חזור ל-v0\.33\.0</,'revert is disabled while running');
  assert.match(card,/<button class="btn sm flat" type="button" onclick="healthUpdateCheck\(\)">בדוק עכשיו</,'checking stays available');
  s=setup({'/update/status':{state:'done',tag:'v0.34.0',verified:true}}); s.run('current="settings"');
  await s.run('loadSettingsData()'); await s.run('healthUpdateCheck()');
  card=cell(s.run('settings()'),'<span>עדכונים',3600);
  assert.doesNotMatch(card,/disabled/,'a verified update does not block the next one');
});

test('#1273: a double click on "החלת העדכון" sends one /update/apply, and a refusal lets the operator try again',async()=>{
  const {run,requests,fixtures}=setup(); run('current="settings"');
  await run('loadSettingsData()'); await run('healthUpdateCheck()');
  run("confirmUpdateApply(UPDATE_INFO.latest)");
  const s=run('sheets.at(-1)');
  await Promise.all([s.onSubmit(), s.onSubmit()]);
  assert.equal(requests.filter((r)=>r.url==='/update/apply').length,1,'the second click does not send a second apply');
  fixtures['/update/apply']=new Error('עדכון ל-v0.35.0 כבר רץ');
  run("confirmUpdateApply(UPDATE_INFO.latest)");
  const again=run('sheets.at(-1)');
  assert.match(await again.onSubmit().then(()=>'',(e)=>e.message),/כבר רץ/,'the 409 is shown in the window');
  delete fixtures['/update/apply'];
  assert.equal(await again.onSubmit().then(()=>'ok',(e)=>e.message),'ok','after a refusal the same window can send again');
  assert.equal(requests.filter((r)=>r.url==='/update/apply').length,3);
});

test('#1000: "last check" comes from /update.last_check and survives a reload; a stored failure is named, not "no update"',async()=>{
  let s=setup({'/update':{current:'v0.34.0',enabled:true,previous:null,server_name:'srv',last_check:{at:'2026-09-18T06:00:00+00:00',current:'v0.34.0',latest:'v0.34.0',available:false,reason:null}}}); s.run('current="settings"');
  await s.run('loadSettingsData()');
  let card=cell(s.run('settings()'),'<span>עדכונים',2400);
  assert.match(card,/בדיקה אחרונה<\/dt><dd><span class="sv">18\/09\/2026 06:00 — כבר על הגרסה העדכנית \(v0\.34\.0\)/);
  assert.doesNotMatch(card,/לא נבדק בסשן/);
  s=setup({'/update':{current:'v0.34.0',enabled:true,previous:null,server_name:'srv',last_check:{at:'2026-09-18T06:00:00+00:00',current:'v0.34.0',latest:null,available:false,reason:'הבדיקה מול x נכשלה: timeout'}}}); s.run('current="settings"');
  await s.run('loadSettingsData()');
  card=cell(s.run('settings()'),'<span>עדכונים',2400);
  assert.match(card,/18\/09\/2026 06:00 — הבדיקה נכשלה: הבדיקה מול x נכשלה: timeout/); assert.doesNotMatch(card,/אין חדש|כבר על הגרסה/);
  assert.doesNotMatch(card,/החל עדכון/,'a stored "available" from an earlier session is not an apply button — apply needs a fresh check (UPDATE_CHECK)');
});

test('#1281: update network failure shows guidance first and technical detail as secondary text',async()=>{
  const message='השרת לא הצליח להגיע ל-github.com בפורט 443 (חומת אש או DNS). כדי לעדכן, בקשו לפתוח בחומת האש של המכללה: השרת ← github.com:443.';
  const detail="Command ['git', 'ls-remote'] timed out after 20 seconds";
  const s=setup({'/update':{current:'v0.34.0',enabled:true,previous:null,last_check:{at:'2026-09-18T06:00:00+00:00',current:'v0.34.0',latest:null,available:false,reason:'no_network',message,detail}}});
  s.run('current="settings"'); await s.run('loadSettingsData()');
  const card=cell(s.run('settings()'),'<span>עדכונים',2200);
  assert.ok(card.includes(message));
  const escapedDetail="Command [&#39;git&#39;, &#39;ls-remote&#39;] timed out after 20 seconds";
  assert.match(card,/class="muted"[^>]*>Command \[&#39;git&#39;, &#39;ls-remote&#39;\] timed out after 20 seconds<\/small>/);
  assert.ok(card.indexOf(message) < card.indexOf(escapedDetail),'guidance is the main line before technical detail');
  assert.doesNotMatch(card,/החל עדכון/,'a failed check never enables apply');
});

test('#1000: the health header time comes from the X-Health-Checked-At header (server clock), read by a direct fetch',async()=>{
  const s=setup({__headers:{'/health':{'X-Health-Checked-At':'2026-09-19T09:41:00+00:00'}}}); s.run('current="health"');
  await s.run('loadHealth()');
  assert.match(s.run('HEALTH_AT'),/^(היום|19\/09\/2026) 09:41$/);
  const html=s.run('health()');
  assert.match(html,/9 בדיקות · נבדק (היום|19\/09\/2026) 09:41</);
  assert.ok(Array.isArray(s.run('HEALTH')),'the body is still the array');
  assert.equal(s.run('typeof clockNow'),'function','clockNow is still used for the ports page — but not for /health');
});

/* ---------- רשת › פורטים — #1267: מופרדים לפי רשת, חומת אש רק בין רשתות ----------
   נדב 10/10: כרטיס לכל רשת (הפצה / ניהול / בין שרתים). ברשת ההפצה השרת מנהל את
   הפורטים — אין עמודת חומת אש ואין פעולות. בין רשתות: חומת אש בשרת (פתוח=ירוק,
   חסום=אדום, לא נבדק=אפור), "מה לפתוח בחומת האש של המכללה" מה-note, ותפריט
   "בדוק עכשיו" / "הצג פרטים" בלבד. אין "פתח/סגור" בשום מקום, ו-22/SSH לא מוצג (#1266). */
const p1267=(id,name,p,proto,network,state,detail,extra={})=>({id,name,port:p,proto,desc:'desc '+id,target:'target '+id,state,detail,
  note:'לפתוח ב-FW: '+proto.toUpperCase()+' '+p+' הסבר '+id,enabled:null,listening:null,bind:[],toggle:'none',toggle_url:null,confirm_word:null,confirm_when:null,
  off_means:'',network,firewall:null,firewall_detail:null,...extra});
const PORTS_1267=[
  p1267('tftp','TFTP','69','udp','deploy','ok','dnsmasq מגיש',{listening:true,bind:['0.0.0.0:69'],toggle:'confirm',enabled:true}),
  p1267('dhcp','DHCP','67','udp','deploy','ok','dnsmasq מאזין',{listening:true,bind:['0.0.0.0:67'],toggle:'confirm',enabled:true,interfaces:[{name:'ens19',enabled:true,proxy:false}]}),
  p1267('http_boot','HTTP','8080','tcp','deploy','ok','עונה על תפריט האתחול',{listening:true,bind:['0.0.0.0:8080'],toggle:'confirm',enabled:true}),
  p1267('http_console','HTTPS','8081','tcp','mgmt','ok','המתג דלוק ומאזין',{listening:true,bind:['10.44.10.1:8081','127.0.0.1:8081'],toggle:'confirm',enabled:true,
    firewall:'open',firewall_detail:'כלל פתיחה בטבלת imagectl: iifname "ens18" tcp dport 8081 accept'}),
  p1267('pxe_proxy','PXE','4011','udp','deploy','off','כבוי על ידי המפעיל — לא מאזין'),
  p1267('multicast','Multicast','9000–9001','udp','deploy','ok','אין udp-sender חי — ואין סבב'),
  p1267('monitor','Monitor (RFB)','5900','tcp','deploy','off','המתג monitor:stations כבוי'),
  p1267('interserver','HTTPS (mTLS)','8443','tcp','interserver','off','לא מאזין',{listening:false,
    firewall:'blocked',firewall_detail:'אין כלל פתיחה בטבלת imagectl, ו-policy drop חוסם חיבור מבחוץ',off_means:'שרתים משניים לא יוכלו להירשם'}),
  p1267('firewall','חומת אש','—','nft',null,'ok','פעילה, 9 כללים לטבלת imagectl'),
];
const card=(html,title)=>{const i=html.indexOf('<span>'+title); assert.ok(i>=0,'missing card: '+title); const s=html.lastIndexOf('<div class="',i); const n=html.indexOf('<div class="c12 card"',i+1); return html.slice(s,n<0?undefined:n);};
const menuOf=(r)=>JSON.parse(r.match(/data-menu="([^"]*)"/)[1].replaceAll('&quot;','"').replaceAll('&#39;',"'"));

test('#1267 ports page: three cards by network — deploy has no firewall column and no actions; management and inter-server do',async()=>{
  const {run}=setup({'/ports':PORTS_1267}); run('current="ports"');
  assert.equal(run('ports()'),run('pagePlaceholder()'));
  await run('loadPorts()');
  const html=run('ports()'); balanced(html);
  const dep=card(html,'רשת הפצה'), mgmt=card(html,'רשת ניהול'), inter=card(html,'בין שרתים');
  // הפצה: הכרטיס שמשרת DHCP (מה-/ports) + הכתובת שלו (מ-/net/interfaces)
  assert.match(dep,/<span>רשת הפצה · ens19 · 10\.0\.0\.1</);
  for(const p of ['67/udp','69/udp','8080/tcp','4011/udp','9000–9001/udp','5900/tcp']) assert.ok(dep.includes(p),'deploy card is missing '+p);
  assert.match(dep,/<th>פורט<\/th><th>שירות<\/th><th>מי פונה<\/th><th>מאזין<\/th><\/tr>/,'deploy: four columns, no firewall');
  assert.doesNotMatch(dep,/חומת אש<\/th>|מה לפתוח בחומת|class="kebab"/,'deploy: no firewall column and no row menu');
  assert.match(dep,/השרת מנהל את רשת ההפצה ואת הפורטים שלה/);
  // ניהול: 8081, חומת אש פתוח = ירוק, מה לפתוח = ה-note בלי הקידומת
  assert.match(mgmt,/<span>רשת ניהול · 10\.44\.10\.1</,'the management address is the measured bind of 8081, loopback left out');
  assert.match(mgmt,/<th>פורט<\/th><th>שירות<\/th><th>מי פונה<\/th><th>מאזין<\/th><th>חומת אש<\/th><th class="c-opt">מה לפתוח בחומת האש של המכללה<\/th>/);
  const r8081=row(mgmt,'8081/tcp');
  assert.match(r8081,/class="pill ok">פתוח</); assert.match(r8081,/>TCP 8081 הסבר http_console</); assert.doesNotMatch(r8081,/לפתוח ב-FW/);
  assert.match(mgmt,/חומת האש של המכללה לא נראית מהשרת/);
  // בין שרתים: 8443 חסום = אדום
  assert.match(row(inter,'8443/tcp'),/class="pill err">חסום</);
  assert.ok(!dep.includes('8081') && !dep.includes('8443') && !mgmt.includes('8443'),'each port in its own card only');
  // מצב חומת האש של השרת כולה — מה-/ports (שורת firewall), לא כשורת פורט; ‏#1300: הודעה רק כשאינה תקינה (כמו המוקאפ — רק הכרטיסים)
  assert.doesNotMatch(html,/חומת האש של השרת: פעילה/);
  const bad=setup({'/ports':PORTS_1267.map((p)=>p.id==='firewall'?{...p,state:'bad',detail:'לא פעילה'}:p)}); bad.run('current="ports"');
  await bad.run('loadPorts()');
  assert.match(bad.run('ports()'),/class="note err"[^]*חומת האש של השרת: לא פעילה/);
  assert.doesNotMatch(html,/<span class="mono">—\/nft</);
});

test('#1267 ports page: no open/close anywhere, no switches, no typed confirmations, and 22/SSH is not shown',async()=>{
  const {run}=setup({'/ports':PORTS_1267}); run('current="ports"');
  await run('loadPorts()');
  const html=run('ports()');
  assert.doesNotMatch(html,/role="switch"/,'no switch on the ports page');
  assert.doesNotMatch(html,/22\/tcp|SSH|🔒/,'SSH rows are not shown (#1266)');
  const kebabs=[...html.matchAll(/data-menu="([^"]*)"/g)];
  assert.equal(kebabs.length,2,'a row menu only for 8081 and 8443');
  for(const k of kebabs) assert.deepEqual(menuOf(k[0]).map((i)=>i[0]),['בדוק עכשיו','הצג פרטים']);
  assert.doesNotMatch(html,/פתח…|סגור…|הקלד/);
});

test('#1267 firewall not read → "לא נבדק", never green; the header pill says so',async()=>{
  const unread=PORTS_1267.map((p)=>p.network==='mgmt'||p.network==='interserver'?{...p,firewall:null,firewall_detail:'nft לא נקרא — לא נבדק'}:p);
  const {run}=setup({'/ports':unread}); run('current="ports"');
  await run('loadPorts()');
  const html=run('ports()');
  const r=row(card(html,'רשת ניהול'),'8081/tcp');
  assert.match(r,/<span title="nft לא נקרא — לא נבדק"><span class="pill ">לא נבדק<\/span><\/span>/,'the firewall cell: not checked, never green or red');
  assert.doesNotMatch(r,/class="pill err"/);
  // ‏#1300 (console-full.html P.network "ports"): עמודת "מאזין" היא פיל אחד; מה שנמדד — ב-title
  assert.match(r,/<span title="המתג דלוק ומאזין"><span class="pill ok">מאזין<\/span><\/span>/);
});

test('#1267 "בדוק עכשיו" re-reads /ports and reports what was measured; "הצג פרטים" opens a real drawer',async()=>{
  const {run,requests}=setup({'/ports':PORTS_1267}); run('current="ports"');
  await run('loadPorts()'); run('ports()');
  const before=requests.filter((r)=>r.url==='/ports').length;
  await run("portCheckNow('interserver')");
  assert.equal(requests.filter((r)=>r.url==='/ports').length,before+1,'a real re-read, not a canned toast');
  assert.match(run('toasts.at(-1)'),/8443\/tcp: כבוי — לא מאזין · חומת אש בשרת: חסום/);
  run("portDetail('http_console')");
  assert.equal(run('document.getElementById("drawerTitle").textContent'),'8081/tcp · HTTPS');
  const body=run('document.getElementById("drawerBody").innerHTML');
  assert.match(body,/class="page drw"/); assert.match(body,/מצב שנמדד/); assert.match(body,/10\.44\.10\.1:8081/);
  assert.ok(body.includes('iifname &quot;ens18&quot; tcp dport 8081 accept'),'the rule that opened it');
  assert.match(body,/חומת האש של המכללה/); assert.match(body,/TCP 8081 הסבר http_console/);
  assert.match(body,/onclick="portCheckNow\('http_console', true\)">בדוק עכשיו</);
  assert.ok(!requests.some((r)=>r.method!=='GET'),'nothing is written');
  assert.equal(run('sheets.length'),0,'no modal, no typed word');
  // תוצאה שלא נקראה — הבדיקה נכשלה, לא "תקין"
  run('api=async()=>{throw new Error("DB down")}');
  await run("portCheckNow('http_console')");
  assert.match(run('toasts.at(-1)'),/הבדיקה נכשלה: DB down/);
});

test('#1267 /ports that could not be read is a red note, not an empty page',async()=>{
  const {run}=setup({'/ports':new Error('DB down')}); run('current="ports"');
  await run('loadPorts()');
  const html=run('ports()');
  assert.notEqual(html,run('pagePlaceholder()')); assert.match(html,/note err.*לא הצלחתי לקרוא את רשימת הפורטים: DB down/);
  assert.doesNotMatch(html,/<table/); assert.doesNotMatch(html,/port-state|כל השורות נמדדו/,'nothing measured is claimed');
  // ‏#954 גל 8: הלשוניות הן של אובייקט הרשת — הדף הזה הוא לשונית 3; ‏#1300: אותה כותרת "רשת" כמו שאר הלשוניות
  assert.match(html,/<div class="obj-name">רשת</);
  assert.match(html,/role="tab" aria-selected="true" tabindex="0" onclick="selectPageById\('ports'\)">פורטים/);
});

test('deploy role: the pages stay admin-only and no admin endpoint is requested',async()=>{
  const {run,requests}=setup({'/health':403,'/monitor/settings':403,'/update':403});
  run('ME={timezone:"UTC",username:"mafitz",role:"deploy",server_name:"srv",capabilities:{}}; current="ports"');
  assert.equal(run('pageAllowed("health")'),false); assert.equal(run('pageAllowed("ports")'),false);
  await run('loadPorts()');
  for(const u of ['/monitor/settings','/net/interfaces']) assert.ok(!requests.some((r)=>r.url===u),u+' must not be requested by deploy');
});

// --- נדב 17/09: שורת TFTP עם 8 כתובות האזנה מתחה את הטבלה מעבר למסך ---------------
test('a long bind list renders one address per line, IPv6 link-local folded into +N, never one long inline string', () => {
  const {run}=setup();
  const html=run(`portNicHtml(${JSON.stringify(['127.0.0.1:69','10.10.10.8:69','10.44.3.1:69','10.44.0.1:69','[fe80::215:5dff:fe44:3c07]%eth3:69','[fe80::215:5dff:fe10:1]%br0:69','[fe80::215:5dff:fe10:2]%eth1:69','[::1]:69'])})`);
  assert.match(html,/class="mono bindlist" dir="ltr"/);
  assert.match(html,/10\.10\.10\.8:69<br>10\.44\.3\.1:69/,'one per line');
  assert.doesNotMatch(html,/fe80[^<]*<br>/,'link-local is not in the visible list');
  assert.match(html,/title="[^"]*fe80[^"]*">\+4</,'the rest is folded into +N with a tooltip');
  assert.doesNotMatch(html,/, \[fe80/,'no comma-joined inline string');
  assert.match(run(`portNicHtml(['0.0.0.0:8080'])`),/0\.0\.0\.0:8080/);
  assert.match(run(`portNicHtml([])`),/—/);
});

// apple-pending-4 · 4: פיל ה-TLS עבר מכותרת הסקירה לדף הבריאות
// ‏#1300 (הכרעת נדב 12/10, "כמו המוקאפ"): בכותרת הבריאות פיל מצב אחד — בלי פיל TLS. מצב התעודה המלא
// נשאר בהגדרות (כרטיס "תעודת TLS") ובפרטי ה-Session.
test('#1300: the health header has one state pill and no TLS pill; TLS lives in settings',async()=>{
  const {run}=setup(); run('current="health"');
  await run('loadHealth()');
  for(const tls of ['{fingerprint_sha256:"AB:CD"}','null']) {
    run('ME.tls='+tls);
    assert.doesNotMatch(run('health()'),/TLS (כבוי|תעודה)/,tls);
  }
  assert.doesNotMatch(run('layout(pages.home,0)'),/TLS (כבוי|תעודה)/,'the overview header does not show TLS either');
  assert.equal(typeof run('tlsRowHtml'),'function','the TLS row is on the settings page');
});
test('#1300: raw English detail never sits in a health row — a short Hebrew line, the text behind "פרטים"',async()=>{
  const {run}=setup({'/health':[check('boot','קבצי האתחול','bad','missing in /srv/tftp: bootx64.efi, grubx64.efi, grub/grub.cfg'),check('fw','חומת אש','unknown','nft: command not found')]}); run('current="health"');
  await run('loadHealth()');
  const html=run('health()');
  const visible=html.replace(/<details class="tech">[^]*?<\/details>/g,'');
  assert.doesNotMatch(visible,/srv\/tftp|bootx64|command not found/,'no raw text outside "פרטים"');
  assert.match(row(html,'קבצי האתחול'),/<span class="wrap">נמצאה תקלה<\/span><details class="tech"><summary>פרטים<\/summary><code dir="ltr">missing in \/srv\/tftp: bootx64\.efi/);
  assert.match(row(html,'חומת אש'),/<span class="wrap">לא נבדק<\/span><details class="tech">/);
});

/* ---------- #1267 המשך (נדב 11/10): עצירה/הפעלה של שירות עברה מדף הפורטים לדף הבריאות ----------
   אותו API של דף הפורטים (PUT toggle_url = /ports/{id}); עצירה = חלון אישור עם השלכות וכפתור אדום,
   בלי הקלדה; הפעלה = כפתור רגיל; מצב שלא נקרא = "לא נבדק" בלי כפתור. */
const TFTP_SVC_WARN='כיבוי 69 (TFTP) עוצר את ה-PXE: מחשבי בנייה ושיכפול לא יעלו מהשרת.';
const PORTS_SVC=[
  p1267('tftp','TFTP','69','udp','deploy','ok','dnsmasq מגיש',{enabled:true,listening:true,toggle:'confirm',toggle_url:'/api/console/ports/tftp',
    confirm_word:'srv',confirm_when:'off',warning_he:TFTP_SVC_WARN,off_means:'אין shim/GRUB'}),
  p1267('dhcp','DHCP','67','udp','deploy','ok','dnsmasq מאזין',{enabled:true,listening:true,toggle:'confirm',toggle_url:'/api/console/net/interfaces/{name}'}),
  p1267('http_boot','HTTP','8080','tcp','deploy','off','כבוי על ידי המפעיל — לא מאזין',{enabled:false,listening:false,toggle:'confirm',
    toggle_url:'/api/console/ports/http_boot',confirm_word:'srv',confirm_when:'off'}),
  p1267('http_console','HTTPS','8081','tcp','mgmt','unknown','טבלת הסוקטים לא נקראה',{enabled:true,listening:null,bind:null,toggle:'confirm',
    toggle_url:'/api/console/ports/http_console',confirm_word:'srv',confirm_when:'off'}),
  p1267('monitor','Monitor (RFB)','5900','tcp','deploy','off','המתג כבוי',{enabled:false,toggle:'confirm',toggle_url:'/api/console/monitor/settings'}),
  p1267('interserver','HTTPS (mTLS)','8443','tcp','interserver','ok','המתג דלוק ומאזין',{enabled:true,listening:true,toggle:'api',
    toggle_url:'/api/console/ports/interserver',off_means:'שרתים משניים לא יוכלו להירשם או לסנכרן מול השרת הזה'}),
  p1267('firewall','חומת אש','—','nft',null,'ok','פעילה'),
];

test('#1267 health page: a services card with stop/start per service — stop is a red confirm with consequences and no typed word, start is a plain button',async()=>{
  const {run,requests}=setup({'/ports':PORTS_SVC}); run('current="health"');
  await run('loadHealth()');
  const html=run('health()'); balanced(html);
  const svc=card(html,'שירותים');
  for(const p of ['69/udp','8080/tcp','8081/tcp','8443/tcp']) assert.ok(svc.includes(p),'services card is missing '+p);
  assert.ok(!svc.includes('67/udp') && !svc.includes('5900/tcp'),'DHCP/monitor keep their own pages — their switch is not a /ports/{id} service');
  assert.doesNotMatch(svc,/disabled/);
  // TFTP פועל → "עצור…" → sheet אדום עם האזהרה, בלי הקלדה; נשלחת המילה שהשרת דורש, מהשורה
  const tftp=row(svc,'69/udp');
  assert.match(tftp,/class="btn sm danger" onclick="healthSvcStop\('tftp'\)">עצור…</);
  run("healthSvcStop('tftp')");
  let s=run('sheets.at(-1)');
  assert.equal(s.danger,true); assert.equal(s.verify,undefined,'no typed word'); assert.equal(s.submitLabel,'עצירה');
  assert.ok(s.note.includes(TFTP_SVC_WARN),'the consequences are in the dialog');
  const before=requests.filter((r)=>r.url==='/ports').length;
  await s.onSubmit();
  assert.deepEqual(requests.filter((r)=>r.method==='PUT').at(-1),{url:'/ports/tftp',method:'PUT',body:{enabled:false,confirm:'srv'}});
  assert.ok(requests.filter((r)=>r.url==='/ports').length>before,'the state is re-read after the change');
  // 8080 עצור → "הפעל" רגיל, בלי חלון
  const boot=row(svc,'8080/tcp');
  assert.match(boot,/class="btn sm" onclick="healthSvcStart\('http_boot'\)">הפעל</);
  const nSheets=run('sheets.length');
  await run("healthSvcStart('http_boot')");
  assert.equal(run('sheets.length'),nSheets,'start has no dialog');
  assert.deepEqual(requests.filter((r)=>r.method==='PUT').at(-1),{url:'/ports/http_boot',method:'PUT',body:{enabled:true}});
  // 8081: מצב שלא נקרא — "לא נבדק", בלי כפתור
  const con=row(svc,'8081/tcp');
  assert.match(con,/class="st unk">לא נבדק</); assert.doesNotMatch(con,/<button/);
  // 8443 (api): עצירה — אותו חלון אדום, בלי confirm בגוף
  run("healthSvcStop('interserver')");
  s=run('sheets.at(-1)'); assert.equal(s.danger,true); assert.equal(s.verify,undefined); assert.match(s.note,/שרתים משניים לא יוכלו להירשם/);
  await s.onSubmit();
  assert.deepEqual(requests.filter((r)=>r.method==='PUT').at(-1),{url:'/ports/interserver',method:'PUT',body:{enabled:false}});
});

test('#1267 health page: /ports that could not be read → services "לא נבדק", no buttons',async()=>{
  const {run}=setup({'/ports':new Error('DB down')}); run('current="health"');
  await run('loadHealth()');
  const svc=card(run('health()'),'שירותים');
  assert.match(svc,/לא נבדק/); assert.match(svc,/DB down/); assert.doesNotMatch(svc,/<button/);
});
