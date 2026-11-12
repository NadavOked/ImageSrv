// stripTags (console.js): הטקסט של HTML שהקונסולה בנתה, ל-title. החליף את ‏/<[^>]*>/g —
// הסרה "חלקית" שמ-"<<b>script>" משאירה "<script>" (CodeQL js/incomplete-multi-character-sanitization,
// שער ה-CodeQL הציבורי 16/10). console.js רץ ב-vm מול DOM מזערי, כמו ב-task_panel.test.cjs.
const {consoleFiles} = require('./console_src.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

function setup() {
  const el=()=>({value:'',textContent:'',innerHTML:'',hidden:false,dataset:{},style:{},classList:{add(){},remove(){},toggle(){},contains:()=>false},
    setAttribute(){},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],closest:()=>null,focus(){}});
  const doc={querySelector:()=>el(),querySelectorAll:()=>[],getElementById:()=>el(),
    createElement:()=>el(),documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},body:{appendChild(){}}};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,encodeURIComponent,decodeURIComponent,
    document:doc,window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  for(const f of ['progress.js',...consoleFiles()]) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  return (s)=>vm.runInContext(s,ctx);
}

test('stripTags: nested/overlapping tags never leave a "<" behind (the regex left "<script>")',()=>{
  const run=setup();
  for(const evil of ['<<b>script>alert(1)<</b>/script>','<scr<b>ipt>x','<<<a>>>b','<!--<b>-->','a<b','<img src=x onerror=alert(1)//']){
    const out=run(`stripTags(${JSON.stringify(evil)})`);
    assert.ok(!out.includes('<'),`${JSON.stringify(evil)} -> ${JSON.stringify(out)}`);
  }
});

test('stripTags: Hebrew text and entities pass through unchanged; only the tags go',()=>{
  const run=setup();
  assert.equal(run(`stripTags('<b>אימג&#39;ים</b> · <span class="x">שרת 2</span>')`),"אימג&#39;ים · שרת 2");
  assert.equal(run(`stripTags('הפצה · פעיל')`),'הפצה · פעיל');
  assert.equal(run(`stripTags('a > b')`),'a > b','a lone ">" outside a tag is text');
  assert.equal(run('stripTags(null)'),'');
  assert.equal(run('stripTags(7)'),'7');
});

test('UI.objHeader: the sub-line title is the plain text, decoded once and re-escaped (#1300) -- no "<" survives',()=>{
  const run=setup();
  const html=run(`UI.objHeader({name:"x",sub:"<b>אימג&#39;ים</b> · 3 מחשבים"})`);
  assert.match(html,/title="אימג&#39;ים · 3 מחשבים"/);
  const evil=run(`UI.objHeader({name:"x",sub:"<<b>script>alert(1)<</b>/script>"})`);
  const title=evil.match(/class="obj-sub" title="([^"]*)"/)[1];
  assert.ok(!title.includes('<'),title);
});
