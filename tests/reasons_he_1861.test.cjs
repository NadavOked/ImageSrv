// #1861 (Nadav 20/10: "are all the errors the machine finds in Hebrew?") -- the console's rendering of the code
// words about a disk: the SMART question's reason ("(cable)" was raw, in an LTR span), a drawer's state ("skipped",
// "replacing" fell through DRAWER_STATE_HE), and a red disk's chip (read-only was "the BIOS will stop at F1").
// The words are read from the producers' code -- smart.sh, ata_cause.py, the agent's target_set -- never typed here,
// so a new one with no Hebrew fails by name. The maps themselves: tests/test_reasons_he_1861.py.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const repo=path.resolve(__dirname,'..');
const root=path.join(repo,'server/static');

function setup() {
  const el=()=>({value:'',textContent:'',innerHTML:'',hidden:false,dataset:{},style:{},classList:{add(){},remove(){},toggle(){},contains:()=>false},
    setAttribute(){},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],closest:()=>null,focus(){}});
  const doc={querySelector:()=>el(),querySelectorAll:()=>[],getElementById:()=>el(),
    createElement:()=>el(),documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},body:{appendChild(){}}};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,RegExp,encodeURIComponent,decodeURIComponent,
    document:doc,window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  for(const f of ['progress.js','console.js','branches.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  return (code)=>vm.runInContext(code,ctx);
}
const run=setup();
const read=(p)=>fs.readFileSync(path.join(repo,p),'utf8');
const visible=(html)=>html.replace(/<[^>]*>/g,' ').replace(/onclick="[^"]*"/g,' ');

/* the producers */
const smartSh=read('agent/lib/smart.sh');
const warnWords=((smartSh.match(/^SMART_WARN_REASONS="([^"]*)"/m)||[])[1]||'').split(/\s+/).filter(Boolean);   // #1859: the orange words
const smartReasons=[...new Set([...[...smartSh.matchAll(/(?:echo "|_smart_store "\$1" |set -- )(?:ok|fail|warn|unchecked) (\w+)/g)].map(m=>m[1]),...warnWords])].filter(r=>r!=='ok');
const causes=[...read('server/ata_cause.py').match(/^CAUSES = \(([^)]*)\)/m)[1].matchAll(/"(\w+)"/g)].map(m=>m[1]);
const states=new Set(['waiting','failed']);
for(const f of fs.readdirSync(path.join(repo,'agent/lib')).filter(f=>f.endsWith('.sh')))
  for(const m of read('agent/lib/'+f).matchAll(/\btarget_set "?\$[\w{}]+"? "?([a-z_]+)"?/g)) states.add(m[1]);

test('the scans see what they were written for', () => {
  for(const r of ['health_failed','uncorrectable','pending_sectors','readonly','open_failed','no_smart','ro_unread']) assert.ok(smartReasons.includes(r),r);
  assert.deepEqual(causes.sort(),['cable','disk','readonly','removed','unclassified','write_error']);   // write_error: #1841
  for(const s of ['skipped','cancelled']) assert.ok(states.has(s),s);   // #2042: no "replacing" -- it came only from "replace" on an orange disk's question
});

test('the SMART question names its reason in Hebrew -- a cause of the previous clone and every smart.sh word', () => {
  const m={mac:'aa:bb:cc:dd:ee:01',name:'מחשב 1'};
  const asks=[...causes.map(r=>({verdict:'failed_last',reason:r})),...smartReasons.map(r=>({verdict:'warn',reason:r}))];
  for(const a of asks) {
    const html=run(`roomAskNote(${JSON.stringify(m)},${JSON.stringify({nonce:'1-2',port:2,answer:null,...a})})`);
    const shown=visible(html);
    assert.doesNotMatch(shown,new RegExp(`\\b${a.reason}\\b`),`${a.verdict}/${a.reason} reached the note raw: ${shown}`);
    assert.doesNotMatch(shown,/סיבה לא מוכרת/,`${a.reason} has no words of its own`);
    assert.doesNotMatch(html,/dir="ltr">/,'the reason is a Hebrew phrase, not an LTR code word');
  }
});

test('every state the agent writes for a drawer is said in Hebrew -- the tooltip and the branches page', () => {
  for(const s of states) {
    const he=run(`DRAWER_STATE_HE[${JSON.stringify(s)}]`);
    assert.ok(he && /[א-ת]/.test(he),`DRAWER_STATE_HE has no words for "${s}"`);
    const cap=run(`branchDrawerHtml({state:${JSON.stringify(s)},port:1},0)`);
    assert.doesNotMatch(visible(cap),new RegExp(`\\b${s}\\b`),cap);
  }
});

test('a red disk chip says why: the F1 reason, or read-only -- never only "the BIOS will stop at F1" for a read-only disk', () => {
  const chip=(reason)=>visible(run(`disksHtml([{disk:'sda',disk_number:1,verdict:'fail',reason:${JSON.stringify(reason)}}])`));
  assert.match(chip('readonly'),/קריאה בלבד/);
  assert.doesNotMatch(chip('readonly'),/F1/);
  assert.match(chip('pending_sectors'),/F1 — SMART: סקטורים ממתינים \(197\)/);
});
