// ‏#1751 — לשונית "התראות במייל" בהגדרות, לפי המוקאפ #1762 (נדב 31/10) ושש ההמלצות שאושרו איתו:
// Gmail = כתובת + סיסמת אפליקציה בלבד; Microsoft = מזהה אפליקציה + tenant תחת "מתקדם", ומשפט הסבר ליד
// "התחבר" כשהם ריקים; Relay = "הכי פשוט…"; בלי "המייל כפי שנשלח" ובלי "שליחה עכשיו"; השרת קובע.
// בלי דפדפן: progress.js + console.js + email_alerts.js ב-vm מול DOM מזערי ו-fetch מזויף.
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
// הקוד בלי ההערות — ההערה בראש הקובץ מזכירה בכוונה את מה שהוסר.
const SRC=fs.readFileSync(path.join(root,'email_alerts.js'),'utf8').replace(/\/\*[\s\S]*?\*\//g,'').replace(/^\s*\/\/.*$/gm,'');

const CATS=[{key:'cloning',label:'שיכפול',kinds:['round_failed']},{key:'disks',label:'דיסקים',kinds:['disk_red']},{key:'updates',label:'עדכונים',kinds:[]},{key:'system',label:'מערכת',kinds:[]},{key:'peers',label:'שרתי משנה',kinds:['peer']}];
const PROVIDERS=[{key:'office365',host:'smtp.office365.com',port:587,security:'starttls',auth:['oauth','password'],oauth:'microsoft'},
  {key:'o365_direct',host:null,port:25,security:'starttls',auth:['none'],oauth:null},{key:'relay',host:null,port:25,security:'none',auth:['none'],oauth:null},
  {key:'gmail',host:'smtp.gmail.com',port:587,security:'starttls',auth:['oauth','password'],oauth:'google'},{key:'custom',host:null,port:587,security:'starttls',auth:['none','password'],oauth:null}];
const acc=(o={})=>({id:'acc_1',name:'',provider:'office365',host:'smtp.office365.com',port:587,security:'starttls',auth:'oauth',username:'imagesrv@college.test',from_addr:'imagesrv@college.test',
  oauth_client_id:'',oauth_tenant:'organizations',password_set:false,oauth_connected:false,oauth_client_secret_set:false,secret_error:null,blocked:null,oauth_pending:false,...o});
const rule=(o={})=>({id:'rule_1',name:'ברירת מחדל',enabled:true,account_id:'acc_1',categories:CATS.map(c=>c.key),min_severity:'warn',recipients:[{address:'nadav@college.test',lang:'he'}],...o});
const view=(o={})=>({accounts:[],rules:[],kinds:[],categories:CATS,severities:['info','warn','err'],languages:['he','en'],default_language:'he',providers:PROVIDERS,
  warnings:[],config_error:null,queue:{pending:0,last_sent_at:null,last_failure:null},running:true,...o});

function setup(fixtures={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,disabled:false,dataset:{},style:{},
      classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.inserted=(this.inserted||'')+html;},setAttribute(){},removeAttribute(){},getAttribute(){return null;},focus(){}});
    return nodes.get(key);
  }
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>ctx.CHECKED||[],getElementById:id=>node('#'+id),createElement:()=>node('el'+Math.random()),
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){},body:{appendChild(){}}},
    navigator:{clipboard:{writeText:async()=>{}}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){throw new Error('localStorage written');}},
    sessionStorage:{getItem:()=>null,setItem(){throw new Error('sessionStorage written');}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:(fn)=>{ctx.TIMERS=(ctx.TIMERS||[]);ctx.TIMERS.push(fn);return 1;},clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0], method=options.method||'GET';
      requests.push({url:key,method,body:typeof options.body==='string'?JSON.parse(options.body):null});
      let f=fixtures[method+' '+key]??fixtures[key];
      if(typeof f==='function') f=f(requests[requests.length-1]);
      if(f && f.__status) return {status:f.__status,ok:false,json:async()=>({detail:f.detail})};
      return {status:200,ok:true,json:async()=>f ?? {}};
    }});
  for(const f of ['progress.js',...consoleFiles(),'branches.js','email_alerts.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={timezone:"UTC",username:"admin",role:"admin",version:"v0.78.0",capabilities:{}}; SETTINGS={}; current="settings"; globalThis.sheets=[]; globalThis.toasts=[];'
    +'sheet=o=>{sheets.push(o);}; toast=(m,ms,kind)=>{toasts.push([m,kind]);}; renderCurrent=()=>{globalThis.rendered=(globalThis.rendered||0)+1;};'
    +'globalThis.modal=null; openModalContent=(t,b,ok,fn,danger,sub)=>{modal={t,b,ok,danger,sub};};');
  return {run,requests,ctx,node};
}
const lastSheet=(s)=>s.run('sheets[sheets.length-1]');

test('the tab: admin only, never in a secondary context, and its own index when there are no secondaries', () => {
  const s=setup();
  assert.equal(s.run('emailAlertsAllowed()'),true);
  // ‏#2786: "שמות מחשב" היא תמיד הלשונית השנייה — הלשוניות האלה זזות אחריה באחת.
  s.run('currentTab=2; EM_VIEW=null; EM_ERR=""');
  assert.match(s.run('settings()'),/התראות במייל/);
  assert.match(s.run('settings()'),/טוען…/,'tab 2 is the email tab when there is no "שרתי משנה" tab');
  s.run('ME.capabilities={enroll_secondary:true}; BRANCH_READY=true; BRANCH_NODES=[]; BRANCH_ERR=""; currentTab=3');
  const html=s.run('settings()');
  assert.ok(html.indexOf('שרתי משנה')<html.indexOf('התראות במייל'),'order: כללי, שמות מחשב, שרתי משנה, התראות במייל');
  assert.match(html,/טוען…/);
  s.run('currentTab=2'); assert.match(s.run('settings()'),/אין עדיין שרת משנה/,'tab 2 stays the secondaries tab');
  s.run('ME.role="deploy"'); assert.equal(s.run('emailAlertsAllowed()'),false);
  s.run('ME.role="admin"; SERVER_CTX="n1"'); assert.equal(s.run('emailAlertsAllowed()'),false);
});

test('not configured: four providers in the mockup order, Relay carries the "simplest" label, Gmail has no Google sign-in', () => {
  const s=setup();
  s.run('EM_VIEW='+JSON.stringify(view()));
  const html=s.run('emailAlertsTab()');
  const order=['Office 365','Gmail / Google Workspace','Relay פנימי / Exchange, בלי משתמש','SMTP מותאם'].map(n=>html.indexOf(`<strong>${n}</strong>`));
  assert.ok(order.every(i=>i>0),order); assert.deepEqual([...order].sort((a,b)=>a-b),order);
  assert.match(html,/Relay פנימי[^]*?<span class="em-tag">הכי פשוט, אם יש במכללה שרת דואר פנימי<\/span>/);
  assert.match(html,/התראות במייל לא מוגדרות/);
  assert.doesNotMatch(SRC,/התחבר עם Google/,'recommendation 2: no Google sign-in');
  s.run('EM.pick="gmail"');
  const g=s.run('emailAlertsTab()');
  assert.match(g,/סיסמת אפליקציה/); assert.match(g,/href="https:\/\/support\.google\.com\/accounts\/answer\/185833" target="_blank" rel="noopener noreferrer"/);
  assert.match(g,/id="em-g-pass" type="password" dir="ltr" autocomplete="new-password">/,'the password field is always empty');
});

test('Microsoft: with no app id the sentence about what the college registers sits next to "התחבר"; the fields live under "מתקדם"', () => {
  const s=setup();
  s.run('EM_VIEW='+JSON.stringify(view())+'; EM.pick="office365"');
  let html=s.run('emailAlertsTab()');
  const btn=html.indexOf('התחבר עם Microsoft'), need=html.indexOf('data-testid="em-ms-needs"');
  assert.ok(btn>0 && need>btn && need-btn<600,'the sentence is right after the button');
  assert.match(html,/לרשום אפליקציה ב-Azure/);
  assert.match(html,/<span>מזהה אפליקציה<\/span>|<dt>מזהה אפליקציה<\/dt>/,'the app-id row is in "פרטי SMTP"');
  s.run('EM_VIEW='+JSON.stringify(view({accounts:[acc({oauth_client_id:'11111111-2222-3333-4444-555555555555'})]})));
  html=s.run('emailAlertsTab()');
  assert.doesNotMatch(html,/data-testid="em-ms-needs"/,'with an app id the sentence is gone');
  assert.match(html,/11111111-2222-3333-4444-555555555555/);
});

test('"התחבר" without an app id saves the address and does not ask Microsoft for a code', async () => {
  const s=setup({'POST /email-alerts/accounts':{account_id:'acc_1',...view({accounts:[acc()]})},'/email-alerts/accounts/acc_1/oauth/start':{user_code:'X'}});
  s.run('EM_VIEW='+JSON.stringify(view())+'; EM.pick="office365"');
  s.node('#em-o365-addr').value='imagesrv@college.test';
  await s.run('emConnect()');
  const post=s.requests.find(r=>r.url==='/email-alerts/accounts');
  assert.deepEqual(post.body,{provider:'office365',auth:'oauth',username:'imagesrv@college.test',from_addr:'imagesrv@college.test'});
  assert.ok(!s.requests.some(r=>/oauth\/start/.test(r.url)),'no device code without an app id');
  assert.match(s.node('#em-o365-err').textContent,/מזהה אפליקציה/);
});

test('"התחבר" with an app id shows the code, polls, and reloads when connected', async () => {
  const a=acc({oauth_client_id:'11111111-2222-3333-4444-555555555555'});
  const s=setup({'/email-alerts/accounts/acc_1/oauth/start':{user_code:'HQ3KZ7W2P',verification_uri:'https://microsoft.com/devicelogin',expires_in:900,interval:5},
    '/email-alerts/accounts/acc_1/oauth/poll':{state:'connected',message:'החשבון מחובר'},'GET /email-alerts':view({accounts:[{...a,oauth_connected:true}],rules:[rule()]})});
  s.run('EM_VIEW='+JSON.stringify(view({accounts:[a],rules:[rule()]})));
  s.node('#em-o365-addr').value='imagesrv@college.test';
  await s.run('emConnect()');
  assert.ok(!s.requests.some(r=>r.method==='PUT'),'nothing changed — no PUT (a PUT would retarget and drop secrets)');
  const html=s.run('emailAlertsTab()');
  assert.match(html,/<b>HQ3KZ7W2P<\/b>/); assert.match(html,/href="https:\/\/microsoft\.com\/devicelogin" target="_blank" rel="noopener noreferrer"/);
  assert.match(html,/מתחבר ל-Microsoft/);
  await s.run('emPoll()');
  assert.equal(s.run('EM.device'),null);
  assert.match(s.run('emailAlertsTab()'),/<span class="pill ok">מחובר<\/span>/);
});

test('Gmail: address + app password go to the server once; no secret is rendered or stored in the browser', async () => {
  const saved=view({accounts:[acc({provider:'gmail',host:'smtp.gmail.com',auth:'password',username:'a@gmail.com',from_addr:'a@gmail.com',password_set:true})],rules:[rule()]});
  const s=setup({'POST /email-alerts/accounts':{account_id:'acc_1',...saved}});
  s.run('EM_VIEW='+JSON.stringify(view())+'; EM.pick="gmail"');
  s.node('#em-g-addr').value='a@gmail.com'; s.node('#em-g-pass').value='abcd efgh ijkl mnop';
  await s.run('emSaveGmail()');
  assert.deepEqual(s.requests[0].body,{provider:'gmail',auth:'password',username:'a@gmail.com',from_addr:'a@gmail.com',password:'abcd efgh ijkl mnop'});
  const html=s.run('emailAlertsTab()');
  assert.doesNotMatch(html,/abcd efgh/,'the password is never rendered');
  assert.match(html,/<dt>סיסמה<\/dt><dd><span class="sv">מוגדרת<\/span>/);
  assert.doesNotMatch(SRC,/localStorage|sessionStorage|indexedDB|document\.cookie/,'nothing of this tab is kept in the browser');
  assert.doesNotMatch(SRC,/\bprompt\(|\bconfirm\(/,'only sheet()');
});

test('the test email: success only on the server ack; a refusal shows the reason, raw SMTP behind "פרטים טכניים"', async () => {
  const conf=view({accounts:[acc({provider:'relay',host:'mail.college.test',port:25,security:'none',auth:'none',username:''})],rules:[rule({recipients:[{address:'nadav@college.test',lang:'en'}]})]});
  let reply={ok:true,code:250,message:'2.0.0 OK',refused:[]};
  const s=setup({'/email-alerts/accounts/acc_1/test':()=>reply});
  s.run('EM_VIEW='+JSON.stringify(conf));
  assert.match(s.run('emailAlertsTab()'),/כמעט מוכן.<\/b> שלח מייל בדיקה/);
  await s.run(`emTest("${encodeURIComponent('nadav@college.test')}")`);
  assert.deepEqual(s.requests[0].body,{to:'nadav@college.test',lang:'en'},'the recipient language goes with the test');
  let html=s.run('emailAlertsTab()');
  assert.match(html,/שרת הדואר אישר ·/); assert.match(html,/<code dir="ltr">250<\/code>/); assert.match(html,/התראות פעילות/);
  reply={ok:false,code:535,message:'5.7.139 Authentication unsuccessful',refused:[]};
  await s.run(`emTest("${encodeURIComponent('nadav@college.test')}")`);
  html=s.run('emailAlertsTab()');
  assert.match(html,/נכשלה ·/); assert.match(html,/שרת הדואר סירב/);
  assert.match(html,/<details class="tech"><summary>פרטים טכניים<\/summary><code dir="ltr">535 5\.7\.139 Authentication unsuccessful<\/code>/);
  assert.match(html,/מייל הבדיקה נכשל/);
  reply={__status:409,detail:'החשבון אינו מחובר'};
  await s.run(`emTest("${encodeURIComponent('nadav@college.test')}")`);
  assert.match(s.run('emailAlertsTab()'),/לא הגענו לתשובה של שרת הדואר/,'a request that failed is a failure, not "sent"');
});

test('disconnect / remove: a red button without a typed word; OAuth disconnect deletes the token only', async () => {
  const conn=view({accounts:[acc({oauth_client_id:'x',oauth_connected:true})],rules:[rule()]});
  const s=setup({'DELETE /email-alerts/accounts/acc_1/oauth':view({accounts:[acc({oauth_client_id:'x'})],rules:[rule()]})});
  s.run('EM_VIEW='+JSON.stringify(conn));
  s.run('emRemoveSheet()');
  const o=lastSheet(s);
  assert.equal(o.danger,true); assert.equal(o.submitLabel,'התנתקות'); assert.equal((o.fields||[]).length,0,'no typed confirmation (#1270)'); assert.doesNotMatch(o.note||'',/<input/);
  await s.run('sheets[sheets.length-1].onSubmit({})');
  assert.deepEqual(s.requests.map(r=>r.method+' '+r.url),['DELETE /email-alerts/accounts/acc_1/oauth']);
  assert.match(s.run('emailAlertsTab()'),/התחבר עם Microsoft/,'back to the connect step, address kept');
  const relay=view({accounts:[acc({provider:'relay',host:'mail.college.test',auth:'none',username:''})],rules:[rule()]});
  const r=setup({'PUT /email-alerts/accounts/acc_1':view({accounts:[acc({provider:'relay',host:'',auth:'none',username:''})],rules:[rule()]})});
  r.run('EM_VIEW='+JSON.stringify(relay)); r.run('emRemoveSheet()');
  await r.run('sheets[sheets.length-1].onSubmit({})');
  assert.deepEqual(r.requests[0].body,{clear_secrets:true,host:''});
});

test('SMTP details: Office 365 has the app-id and tenant fields; only what changed is sent; a new account is created with the picked provider', async () => {
  const s=setup({'POST /email-alerts/accounts':{account_id:'acc_1',...view({accounts:[acc({oauth_client_id:'11111111-2222-3333-4444-555555555555'})]})}});
  s.run('EM_VIEW='+JSON.stringify(view())+'; EM.pick="office365"');
  s.run('emSmtpSheet()');
  const o=lastSheet(s), ids=o.fields.map(f=>f.id);
  assert.ok(ids.includes('oauth_client_id') && ids.includes('oauth_tenant'),ids);
  assert.ok(!ids.includes('password') && !ids.includes('from_addr'),'OAuth: no password field, sender comes from "התחבר"');
  await s.run('sheets[sheets.length-1].onSubmit({host:"smtp.office365.com",port:"587",security:"starttls",oauth_client_id:"11111111-2222-3333-4444-555555555555",oauth_tenant:""})');
  assert.deepEqual(s.requests[0].body,{host:'smtp.office365.com',port:587,security:'starttls',oauth_client_id:'11111111-2222-3333-4444-555555555555',oauth_tenant:'organizations',provider:'office365'});
  const c=setup({'PUT /email-alerts/accounts/acc_1':view({accounts:[acc({provider:'custom',host:'smtp.x.org',auth:'password',username:'u'})]})});
  c.run('EM_VIEW='+JSON.stringify(view({accounts:[acc({provider:'custom',host:'smtp.x.org',auth:'password',username:'u',password_set:true})]})));
  c.run('emSmtpSheet()');
  assert.ok(lastSheet(c).fields.some(f=>f.id==='password' && f.type==='password' && !f.value),'password field is empty');
  await c.run('sheets[sheets.length-1].onSubmit({host:"smtp.x.org",port:"465",security:"tls",from_addr:"imagesrv@college.test",username:"u",password:""})');
  assert.deepEqual(c.requests[0].body,{port:465,security:'tls'},'unchanged fields are not sent; an empty password is not sent');
});

test('history: a row opens what the server keeps — state, server reply, attempts, the event lines; no "send now", no email body', async () => {
  const hist={queue:[{id:7,kind:'disk_red',label:'דיסק אדום',severity:'err',lang:'he',rule_id:'rule_1',rule_name:'ברירת מחדל',account_id:'acc_1',account_name:'',
    recipients:['nadav@college.test'],state:'pending',attempts:2,max_attempts:8,code:451,error:'4.3.2 Service not available',created_at:'2026-10-31T09:00:00+00:00',
    sent_at:null,next_at:'2026-10-31T09:04:00+00:00',count:6,items:['דיסק 2 במחשב שיכפול 1: SMART אדום']}],journal:[]};
  const s=setup({'/email-alerts/history':hist});
  s.run('EM_VIEW='+JSON.stringify(view({accounts:[acc({oauth_connected:true,oauth_client_id:'x'})],rules:[rule()]}))+'; EM.adv=true');
  await s.run('loadEmailHistory(false)');
  const html=s.run('emailAlertsTab()');
  assert.match(html,/<details class="em-adv" open/); assert.match(html,/ממתין · ניסיון 3/); assert.match(html,/onclick="emHistSheet\(7\)"/);
  s.run('emHistSheet(7)');
  const m=s.run('modal');
  assert.equal(m.t,'דיסק אדום'); assert.match(m.b,/4\.3\.2 Service not available/); assert.match(m.b,/2 מתוך 8/);
  assert.match(m.b,/SMART אדום/); assert.match(m.b,/ועוד 5 שורות/); assert.match(m.b,/הניסיון הבא/);
  assert.doesNotMatch(SRC,/שליחה עכשיו|המייל כפי שנשלח/,'recommendation 5');
});

test('recipients live inside rules: a new one needs a rule; the primary address cannot be deleted from the list', async () => {
  const s=setup({'PUT /email-alerts/rules/rule_1':view({accounts:[acc()],rules:[rule()]})});
  s.run('EM_VIEW='+JSON.stringify(view({accounts:[acc()],rules:[rule(),rule({id:'rule_2',categories:['disks'],min_severity:'err',recipients:[]})]})));
  s.run('emPersonSheet("")');
  await assert.rejects(s.run('sheets[sheets.length-1].onSubmit({addr:"tech@college.test",lang:"en"})'),/נמען נשמר בתוך כלל/);
  s.ctx.CHECKED=[{value:'rule_1'}];
  await s.run('sheets[sheets.length-1].onSubmit({addr:"tech@college.test",lang:"en"})');
  assert.deepEqual(s.requests.map(r=>r.method+' '+r.url),['PUT /email-alerts/rules/rule_1']);
  assert.deepEqual(s.requests[0].body.recipients,[{address:'nadav@college.test',lang:'he'},{address:'tech@college.test',lang:'en'}]);
  s.run(`emPersonDelete("${encodeURIComponent('nadav@college.test')}")`);
  assert.equal(lastSheet(s).title,'אי אפשר למחוק את הכתובת הראשית');
});

test('server-side state is said, not hidden: a stopped sender, a config error and a missing password are on the page', () => {
  const s=setup();
  s.run('EM_VIEW='+JSON.stringify(view({running:false,config_error:'JSON',warnings:['Direct Send מעביר רק לנמענים בדומיין'],
    accounts:[acc({provider:'custom',host:'smtp.x.org',auth:'password',username:'u',password_set:false,blocked:'אין סיסמה שמורה'})],rules:[rule()]})));
  const html=s.run('emailAlertsTab()');
  assert.match(html,/שליחת ההתראות לא רצה בשרת/); assert.match(html,/ההגדרות השמורות לא פוענחו/); assert.match(html,/Direct Send מעביר/);
  assert.match(html,/התראות מושהות/); assert.match(html,/<span class="pill warn">אין סיסמה<\/span>/);
  assert.match(html,/disabled>/,'no test while the password is missing');
  s.run('EM_VIEW=null; EM_ERR="שגיאה 500"');
  assert.match(s.run('emailAlertsTab()'),/לא הצלחתי לקרוא את הגדרות ההתראות במייל: שגיאה 500/,'unread is not "not configured"');
});
