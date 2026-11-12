// apple-pending-4 · 1: תפריט ⋯ בסוף שורה (UI.rowMenu). בלי דפדפן: console.js ב-vm מול DOM מזערי.
// נבדק: (א) הפעולה ההרסנית אחרונה, מופרדת ואדומה, גם כשהקורא סידר אותה באמצע; (ב) נגישות — aria,
// Enter/↓ פותחים, חצים/Home/End מזיזים, Esc סוגר והפוקוס חוזר לכפתור, Tab סוגר; (ג) הפעולה ההרסנית
// עדיין פותחת sheet אישור (#1270) — התפריט לא עוקף אותה.
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

function setup() {
  const doc={activeElement:null,listeners:{},nodes:new Map(),body:{appendChild(el){doc.nodes.set(el.id,el);}}};
  const mkEl=(id)=>{
    const el={id,className:'',innerHTML:'',textContent:'',value:'',hidden:false,dataset:{},querySelector(){return null;},closest(){return null;},removeAttribute(){},hasAttribute(){return false;},removeEventListener(){},attrs:{},style:{props:{},setProperty(k,v){this.props[k]=v;}},listeners:{},_open:false,
      classList:{add(c){if(c==='open')el._open=true;},remove(c){if(c==='open')el._open=false;},toggle(){},contains:(c)=>c==='open'&&el._open},
      setAttribute(k,v){this.attrs[k]=String(v);},getAttribute(k){return this.attrs[k]??null;},
      addEventListener(t,fn){this.listeners[t]=fn;},getBoundingClientRect:()=>({left:20,right:52,top:100,bottom:132,width:32}),
      offsetWidth:180,offsetHeight:150,isConnected:true,focus(){doc.activeElement=el;},
      querySelectorAll(sel){
        if(sel!=='[role=menuitem]') return [];
        return [...el.innerHTML.matchAll(/<button type="button" role="menuitem"[^>]*onclick="([^"]*)"[^>]*>([^<]*)<\/button>/g)]
          .map((m)=>{ if(!el._items) el._items=new Map(); const k=m[1]+'|'+m[2]; if(!el._items.has(k)) el._items.set(k,{label:m[2],onclick:m[1],focus(){doc.activeElement=this;}}); return el._items.get(k); });
      }};
    return el;
  };
  doc.createElement=()=>mkEl('');
  doc.getElementById=(id)=>doc.nodes.get(id)||null;
  doc.querySelector=()=>mkEl('x'); doc.querySelectorAll=()=>[];
  doc.documentElement={getAttribute:()=>'rtl',setAttribute(){}};
  doc.addEventListener=(t,fn)=>{doc.listeners[t]=fn;};
  const origCreate=doc.createElement; doc.createElement=()=>{const e=origCreate(); return e;};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,encodeURIComponent,decodeURIComponent,
    document:doc,window:{addEventListener(){},matchMedia:()=>({matches:false}),innerWidth:1024,innerHeight:768,open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  for(const f of ['progress.js',...consoleFiles()]) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=(s)=>vm.runInContext(s,ctx);
  return {run,doc,ctx};
}
const keyEvent=(key)=>({key,prevented:false,stopped:false,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}});
const unesc=(t)=>t.replaceAll('&quot;','"').replaceAll('&#39;',"'").replaceAll('&amp;','&');

test('rowMenu: the destructive item is last, separated and red — even when the caller put it in the middle',()=>{
  const {run}=setup();
  const html=run(`UI.rowMenu('nadav',[['שנה','a()'],['מחק','del()','danger'],['איפוס','b()']])`);
  const items=JSON.parse(unesc(html.match(/data-menu="([^"]*)"/)[1]));
  assert.deepEqual(items.map((i)=>i[0]),['שנה','איפוס','מחק']);
  assert.equal(items.at(-1)[2],'danger');
  assert.match(html,/aria-haspopup="menu" aria-expanded="false" aria-label="פעולות עבור nadav"/);
  assert.match(html,/onclick="event\.stopPropagation\(\);openRowMenu\(this\)"/,'a row-level onclick (netSelect) must not fire');
  const menu=run(`rowMenuHtml(${JSON.stringify(items)})`);
  assert.match(menu,/<hr role="separator"><button type="button" role="menuitem" tabindex="-1" class="dg" onclick="closeRowMenu\(true\);del\(\)">מחק</);
  assert.equal((menu.match(/<hr/g)||[]).length,1);
  const noDanger=run(`rowMenuHtml([['א','x()'],['ב','y()']])`);
  assert.doesNotMatch(noDanger,/<hr|class="dg"/,'no separator when nothing is destructive');
});

test('rowMenu: aria-expanded, first item focused, arrows/Home/End move, Esc closes and returns focus to the button',()=>{
  const {run,doc,ctx}=setup();
  const btn=doc.querySelector(); btn.attrs['data-menu']=JSON.stringify([['א','a()'],['ב','b()'],['מחק','d()','danger']]); btn.attrs['aria-label']='פעולות עבור x';
  ctx.btn=btn; run('openRowMenu(btn)');
  const menu=doc.getElementById('rowMenu');
  assert.equal(btn.getAttribute('aria-expanded'),'true'); assert.equal(menu._open,true);
  assert.equal(doc.activeElement.label,'א','focus moves to the first item');
  ctx.e=keyEvent('ArrowDown'); run('rowMenuKey(e)'); assert.equal(doc.activeElement.label,'ב'); assert.equal(ctx.e.prevented,true);
  ctx.e=keyEvent('End'); run('rowMenuKey(e)'); assert.equal(doc.activeElement.label,'מחק');
  ctx.e=keyEvent('ArrowDown'); run('rowMenuKey(e)'); assert.equal(doc.activeElement.label,'א','wraps around');
  ctx.e=keyEvent('ArrowUp'); run('rowMenuKey(e)'); assert.equal(doc.activeElement.label,'מחק');
  ctx.e=keyEvent('Home'); run('rowMenuKey(e)'); assert.equal(doc.activeElement.label,'א');
  ctx.e=keyEvent('Escape'); run('rowMenuKey(e)');
  assert.equal(menu._open,false); assert.equal(btn.getAttribute('aria-expanded'),'false'); assert.equal(doc.activeElement,btn,'focus returns to the ⋯ button');
  assert.equal(ctx.e.stopped,true,'Esc must not also close a sheet behind it');
});

test('rowMenu: ArrowDown/ArrowUp on the button open it (last item for ↑); Tab closes it; a second click on the same button closes it',()=>{
  const {run,doc,ctx}=setup();
  const btn=doc.querySelector(); btn.attrs['data-menu']=JSON.stringify([['א','a()'],['ב','b()']]);
  ctx.btn=btn; ctx.e=keyEvent('ArrowUp'); run('rowMenuTriggerKey(e,btn)');
  assert.equal(btn.getAttribute('aria-expanded'),'true'); assert.equal(doc.activeElement.label,'ב');
  ctx.e=keyEvent('Tab'); run('rowMenuKey(e)'); assert.equal(btn.getAttribute('aria-expanded'),'false'); assert.equal(doc.activeElement,btn);
  run('openRowMenu(btn)'); assert.equal(btn.getAttribute('aria-expanded'),'true');
  run('openRowMenu(btn)'); assert.equal(btn.getAttribute('aria-expanded'),'false','toggle');
  ctx.e=keyEvent('a'); run('rowMenuTriggerKey(e,btn)'); assert.equal(btn.getAttribute('aria-expanded'),'false','other keys do not open');
});

test('rowMenu: opening a second row closes the first (one menu at a time); an empty/invalid menu opens nothing',()=>{
  const {run,doc,ctx}=setup();
  const b1=doc.querySelector(), b2=doc.querySelector();
  b1.attrs['data-menu']=JSON.stringify([['א','a()']]); b2.attrs['data-menu']=JSON.stringify([['ב','b()']]);
  ctx.b1=b1; ctx.b2=b2; run('openRowMenu(b1); openRowMenu(b2)');
  assert.equal(b1.getAttribute('aria-expanded'),'false'); assert.equal(b2.getAttribute('aria-expanded'),'true');
  const b3=doc.querySelector(); b3.attrs['data-menu']='not json'; ctx.b3=b3; run('openRowMenu(b3)');
  assert.notEqual(b3.getAttribute('aria-expanded'),'true');
});

test('permissions: deleting a user from the ⋯ menu still opens the typed-name sheet (principle 7)',async()=>{
  const {run,ctx}=setup();
  run('ME={username:"admin",role:"admin",capabilities:{}}; globalThis.sheets=[]; sheet=(o)=>{sheets.push(o);}; USERS=[{username:"admin",role:"admin",disabled:false},{username:"yossi",role:"deploy",disabled:false}];');
  const html=run('userRowHtml(USERS[1]).cells.at(-1)');
  const items=JSON.parse(unesc(html.match(/data-menu="([^"]*)"/)[1]));
  const del=items.at(-1);
  assert.equal(del[2],'danger');
  run(del[1]);   // = מה שה-onclick של הפריט מריץ
  assert.equal(run('sheets.length'),1);
  assert.equal(run('sheets[0].danger'),true);
  assert.equal(run('sheets[0].verify'),undefined,'the destructive action is a confirm sheet, no typed name (#1270)');
});

test('#1262/#1268: the builtin admin row offers only password reset and revoke-sessions, and says why',()=>{
  const {run}=setup();
  // שלושה מנהלים פעילים — שומר "המנהל האחרון" שקט, ולכן הסינון בא מ-is_builtin בלבד.
  run('ME={username:"noc",role:"admin",capabilities:{}}; USERS=[{username:"admin",role:"admin",disabled:false,is_builtin:true},{username:"noc",role:"admin",disabled:false},{username:"tech2",role:"admin",disabled:false}];');
  const labels=(i)=>JSON.parse(unesc(run(`userRowHtml(USERS[${i}]).cells.at(-1)`).match(/data-menu="([^"]*)"/)[1])).map((x)=>x[0]);
  assert.deepEqual(labels(0),['איפוס סיסמה…','נתק את כל ההפעלות…']);
  assert.match(run('userRowHtml(USERS[0]).cells[0]'),/<span class="sub">נוצר בהתקנה · לא ניתן למחיקה<\/span>/,'#1300: the sub-line of the mockup (MFA is its own column)');
  // מנהל רגיל באותו מצב — עדיין עם תפקיד/השבתה/מחיקה (לא סינון-יתר).
  // ‏#1268: למשתמש אחר — "איפוס סיסמה…" במקום "סיסמה…" (הקלדת סיסמה קבועה נשארת רק לעצמך).
  assert.deepEqual(labels(2),['שנה תפקיד…','איפוס סיסמה…','השבת משתמש…','דרוש אימות דו-שלבי…','נתק את כל ההפעלות…','מחק משתמש…']);
  for(const i of [0,2]) assert.doesNotMatch(run(`userRowHtml(USERS[${i}]).cells.at(-1)`),/userPasswordSheet/,'no typed password for another user');
  // ה-builtin כשהוא המחובר: אין פעולות בשורה — אין ניתוק הפעלות של עצמך, השרת מסרב לאפס את
  // עצמך, ו"שנה סיסמה" עבר ל"החשבון שלי" (#1268). בלי ⋯ ריק.
  run('ME={username:"admin",role:"admin",capabilities:{},is_builtin:true};');
  // ‏#1300: גם לשורה של עצמך יש ⋯ (כמו המוקאפ) — אבל רק "שנה סיסמה" שמוביל ל"החשבון שלי"; לא תפקיד/השבתה/איפוס (#1268)
  assert.match(run('userRowHtml(USERS[0]).cells.at(-1)'),/data-menu="\[\[&quot;שנה סיסמה \(החשבון שלי\)&quot;,&quot;openAccountPage\(\)&quot;\]\]"/);
});
