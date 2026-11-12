// drawerEnhance מעביר את כפתורי .acts / קופסת border-top לשורת הפעולות של המגירה. מה שאינו כפתור
// (הסבר, "בקרוב", תווית WoL) אסור שייעלם בשקט. בלי jsdom: DOM מזערי בתוך vm, ו-console.js האמיתי.
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

class El {
  constructor(tag,cls='',style='',text=''){this.tag=tag;this.cls=new Set(cls.split(' ').filter(Boolean));this.style=style;this.text=text;this.children=[];this.parent=null;this.hidden=false;this.dataset={};
    const self=this; this.classList={contains:c=>self.cls.has(c),add:c=>self.cls.add(c),remove:c=>self.cls.delete(c)};}
  get firstElementChild(){return this.children[0]||null;}
  getAttribute(k){return k==='style'?this.style:(this.attrs&&this.attrs[k])??null;}
  setAttribute(){} addEventListener(){} removeEventListener(){}
  appendChild(c){if(c.parent)c.remove();c.parent=this;this.children.push(c);return c;}
  remove(){if(this.parent){this.parent.children=this.parent.children.filter(x=>x!==this);this.parent=null;}}
  get textContent(){return this.text+this.children.map(c=>c.textContent).join('');}
  set textContent(v){this.text=String(v);}
  set innerHTML(v){this.children=[];this.text='';}
  descendants(){return this.children.flatMap(c=>[c,...c.descendants()]);}
  querySelectorAll(sel){return this.descendants().filter(e=>e.tag==='button'||(e.tag==='a'&&e.cls.has('btn')));}
  querySelector(){return this.children[0]||null;}
}
const el=(tag,cls,style,text,kids=[])=>{const e=new El(tag,cls,style,text);kids.forEach(k=>e.appendChild(k));return e;};

function load() {
  const nodes={drawerSub:new El('div'),drawerTabs:new El('div'),drawerFoot:new El('div')};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:()=>new El('div'),querySelectorAll:()=>[],getElementById:id=>nodes[id]||new El('div'),createElement:t=>new El(t),
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false})},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx);
  for (const f of consoleFiles()) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  return {nodes,ctx,run:s=>vm.runInContext(s,ctx)};
}

test('drawerEnhance moves buttons to the footer and keeps every non-button element of the acts / border-top boxes in the body',()=>{
  const {nodes,ctx,run}=load();
  const wolNote=el('span','muted','','WoL — לתחנות כיתה ב-v2');
  const soon=el('span','muted','','בקרוב');
  const acts=el('div','acts','',' ',[el('button','btn primary','','פעולה'),wolNote]);
  const tail=el('div','','border-top:1px solid;padding-top:12px','',[el('button','btn danger','','מחיקה'),soon]);
  const rootEl=el('div','page drw','','',[el('div','obj-sub','','תת-כותרת'),el('div','','','גוף'),acts,tail]);
  const body=el('div'); body.appendChild(rootEl);
  ctx.__body=body; run('drawerEnhance(__body)');
  const bodyText=body.textContent;
  assert.match(bodyText,/WoL — לתחנות כיתה ב-v2/,'the non-button note in .acts survived');
  assert.match(bodyText,/בקרוב/,'the non-button label in the border-top box survived');
  assert.ok(nodes.drawerFoot.children.some(b=>b.text==='פעולה'),'the button moved to the footer');
  assert.ok(nodes.drawerFoot.children.some(b=>b.text==='מחיקה'&&b.cls.has('ghost-dng')),'the danger button moved too');
  assert.ok(!body.descendants().some(e=>e.tag==='button'),'no button is left stranded in the body');
});

test('drawerEnhance still removes an acts box that held only buttons (no empty shell left behind)',()=>{
  const {ctx,run}=load();
  const acts=el('div','acts','','',[el('button','btn','','סגור')]);
  const rootEl=el('div','page drw','','',[el('div','','','גוף'),acts]);
  const body=el('div'); body.appendChild(rootEl);
  ctx.__body=body; run('drawerEnhance(__body)');
  assert.equal(rootEl.children.length,1); assert.ok(!rootEl.children.includes(acts));
});

// ‏#1300 (console-full.html: "מחיצות 3", "היסטוריה 4"): ‏data-cnt על כותרת ה-.sec הופך למונה בלשונית; בלי — בלי מונה.
test('drawerEnhance puts a .sec data-cnt into its tab as a small counter',()=>{
  const {nodes,ctx,run}=load();
  const sec=(text,cnt)=>{const h=el('div','sec','',text); if(cnt!=null) h.attrs={'data-cnt':cnt}; return h;};
  const rootEl=el('div','page drw','','',[el('div','','','',[sec('פרטים'),el('div','','','גוף')]),
    el('div','','','',[sec('מחיצות','3'),el('div','','','טבלה')]),el('div','','','',[sec('היסטוריה',''),el('div','','','יומן')])]);
  const body=el('div'); body.appendChild(rootEl);
  ctx.__body=body; run('drawerEnhance(__body)');
  const tabs=nodes.drawerTabs.children;
  assert.deepEqual(tabs.map(t=>t.text),['סקירה','מחיצות','היסטוריה']);
  assert.deepEqual(tabs.map(t=>t.children.map(c=>c.tag+':'+c.text)),[[],['small:3'],[]],'empty data-cnt = no counter');
});
