// ‏#954 גל 2: ספריית האימג'ים שנבנתה מחדש לפי docs/design/console-redesign/images.md
// — עץ תיקיות, טבלת התיקייה, בחירה מרובה, גרירה = העברה/סדר, מגירת אימג',
// לשוניות קליטות/אחסון. בלי דפדפן: console.js רץ ב-vm מול DOM מזערי ו-fetch
// מזויף, והפלט נבדק כטקסט. עיקרון 5: "לא נקרא"/"לא ידוע" אינם "אין"/0.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const today=new Date().toISOString().slice(0,10);

function setup() {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const fixtures={
    '/images':[
      {id:'img_7f3a91',name:'Office 2024 — סטנדרט',description:'Windows 11 + Office',folder:'Office',sort:1,family:256,os:'windows',created:'2026-09-02T09:14:00+03:00',source_disk_bytes:256060514304,min_target_bytes:254908301312,used_bytes:84540833792,total_compressed_bytes:57982058496,partitions:2},
      {id:'img_a1b2c3',name:'Office 2024 — מרצה',description:'',folder:'Office',sort:2,family:256,os:'windows',created:'2026-09-10T11:02:00+03:00',source_disk_bytes:512110190592,min_target_bytes:null,used_bytes:null,total_compressed_bytes:57982058496,partitions:2},
      {id:'img_2c8e04',name:'CAD Heavy',description:'SolidWorks',folder:'',sort:1,family:500,os:'windows',created:'2026-08-20T08:00:00+03:00',source_disk_bytes:256060514304,total_compressed_bytes:57982058496,partitions:2},
      {id:'img_lnx001',name:'Ubuntu 24.04',description:'',folder:'Linux',sort:1,family:256,os:'linux',created:'2026-09-12T10:00:00+03:00',source_disk_bytes:256060514304,min_target_bytes:255000000000,total_compressed_bytes:21474836480,partitions:3}],
    '/folders':[{name:'Office',description:'אימג׳י משרד',images:2},{name:'סייבר',description:'',images:0},{name:'Linux',description:'',images:1}],
    '/overview':{images:4,machines:3,storage:{total_bytes:1000*1024**3,free_bytes:312*1024**3},
      session:{id:'s1',image_id:'img_7f3a91',image_name:'Office 2024 — סטנדרט',group_label:'כיתה 303',state:'open',joined:2,expected_clients:3,members:[]},
      room:{id:7,image_id:'img_lnx001',image_name:'Ubuntu 24.04',wave_number:2,wave_state:'running',target_drives:6,written_drives:2},pulls:[]},
    '/tasks':[{id:'t1',type:'capture',name:'Office 2024 v3',machine:'בנייה 1',mac:'cc',disk:'sda',state:'pending',bytes_written:0,bytes_total:null,image_id:'img_new',created_at:today+'T09:40:00Z',updated_at:today+'T09:40:00Z'},
              {id:'t0',type:'capture',name:'Ubuntu 24.04',machine:'בנייה 1',mac:'cc',disk:'sda',state:'done',error:null,bytes_written:21474836480,bytes_total:21474836480,image_id:'img_lnx001',created_at:'2026-09-12T09:40:00Z',updated_at:'2026-09-12T09:50:00Z'}],
    // ‏#972: המניפסט הציבורי (GET /images/{id}) — מה שהמגירה מציגה כטבלת מחיצות.
    '/images/img_lnx001':{id:'img_lnx001',name:'Ubuntu 24.04',created_by:'nadav',scheme:'gpt',sector_size:512,compression:'zstd-3',partclone_version:'0.3.27',
      boot_ca:['Microsoft Corporation UEFI CA 2011','Microsoft UEFI CA 2023'],boot_ca_error:null,
      partitions:[{index:1,role:'esp',fs:'vfat',size_bytes:104857600,used_bytes:31457280,file:'p1.esp.pcl.zst',sha256:'ab12cd34ef56'+'0'.repeat(52)},
                  {index:2,role:'swap',fs:'swap',size_bytes:8589934592,used_bytes:0,file:null,sha256:null},
                  {index:3,role:'linux',fs:'ext4',size_bytes:64424509440,used_bytes:null,shrunk_from_bytes:255013683200,file:'p3.root.pcl.zst',sha256:'9f'.repeat(32)}]},
    '/images/img_7f3a91':{id:'img_7f3a91',name:'Office 2024 — סטנדרט',scheme:'gpt',sector_size:512,
      partitions:[{index:1,role:'esp',fs:'vfat',size_bytes:104857600,used_bytes:31457280,file:'p1.esp.pcl.zst',sha256:'aa'.repeat(32)},
                  {index:3,role:'windows',fs:'ntfs',size_bytes:254803968000,used_bytes:84509376512,file:'p3.win.pcl.zst',sha256:'bb'.repeat(32)}]},
    '/journal':[{ts:'2026-09-12T10:00:00Z',user:'nadav',event:'capture_done',label:'אימג\' נקלט',text:'"Ubuntu 24.04"',severity:'ok'},
                {ts:today+'T08:00:00Z',user:'nadav',event:'image_edit',label:'אימג\' עודכן',text:'"Ubuntu 24.04" — עודכנו: תיאור',severity:'info'}],
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false})},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      if(fixtures[key] instanceof Error) throw fixtures[key];
      if(fixtures[key]===403) return {status:403,ok:false,json:async()=>({detail:'admin only'})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] || {ok:true}};
    }});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx);
  vm.runInContext(fs.readFileSync(path.join(root,'console.js'),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={timezone:"UTC",username:"admin",role:"admin",server_name:"srv",version:"v0.31.0",capabilities:{classrooms:true}}; current="images"; sheet = o => { globalThis.formOptions=o; }; confirmSheet=(t,s,l,fn)=>{ globalThis.confirmFn=fn; };');
  for(const [name,key] of [['OVERVIEW','/overview'],['IMAGES','/images'],['FOLDERS','/folders'],['CAPTURE_TASKS','/tasks']]) run(name+'='+JSON.stringify(fixtures[key]));
  return {run,node,requests,fixtures,ctx};
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
const dragEvent=(data)=>({preventDefault(){},currentTarget:{classList:{add(){},remove(){}}},dataTransfer:{effectAllowed:'',setData(k,v){data[k]=v;},getData(k){return data[k]||'';}}});

test('the page is the datastore browser: header, three tabs, tree with counters, one datagrid', () => {
  const {run}=setup(); const html=run('images(0)'); balanced(html);
  assert.match(html,/class="page aligned"/,'#1300: sticky header + card shadow'); assert.match(html,/ספריית אימג&#39;ים/);
  assert.match(html,/4 אימג'ים · 3 תיקיות/);
  assert.match(html,/688 GB<\/bdi> בשימוש · <bdi dir="ltr">312 GB<\/bdi> פנוי/);
  for(const t of ['קבצים','קליטות','אחסון']) assert.match(html,new RegExp('role="tab"[^>]*>'+t+'<'));
  assert.match(html,/\+ קליטה ממחשב בנייה/); assert.match(html,/העלאת קובץ tar/); assert.match(html,/\+ תיקייה/);
  assert.match(html,/role="tree"/);
  assert.match(html,/כל האימג&#39;ים<\/span><span class="cnt">4</); assert.match(html,/Office<\/span><span class="cnt">2</);
  assert.match(html,/סייבר<\/span><span class="cnt">0</); assert.match(html,/ללא תיקייה<\/span><span class="cnt">1</);
  assert.equal((html.match(/<table class="dg kebab-col"/g)||[]).length,1);
  // ‏#1300 (console-full.html imgCols): שם · מערכת · גודל · מתאים לדיסק · תיקייה · מצב · נקלט; מערכת, תיקייה ונקלט — משניות
  for(const col of ['שם','גודל','מתאים לדיסק','מצב']) assert.match(html,new RegExp('<th>'+col+'</th>'));
  for(const col of ['מערכת','תיקייה','נקלט']) assert.match(html,new RegExp('<th class="c-opt">'+col+'</th>'),'console-full.html P.images: OS, folder and date are secondary when the table is narrow');
  assert.doesNotMatch(html,/<th[^>]*>(בשימוש|בשרת|אימות|נוצר)<\/th>/,'#1300: the old column set is gone; "in use" is in the image drawer');
  assert.match(html,/<span class="fcrumb"><b>כל האימג'ים<\/b><\/span>/,'#1300: a prominent table title, not a tiny breadcrumb');
  assert.match(html,/placeholder="חיפוש אימג'"/);
  assert.match(html,/<div class="card-h"><span>אימג&#39;ים<\/span><\/div>/,'stage 2: the card has a title');
  assert.match(html,/<span class="dskico"><svg[^>]*><ellipse/,'stage 2: an image row carries the disk icon');
  assert.doesNotMatch(html,/כללי בטיחות|פעולות מהירות|מטא־נתונים|>Up<|>Down<|>Edit</);
});

test('rows: market disk fit includes the decimal requirement, null is a dash not 0, usage comes from /overview only', () => {
  const {run}=setup(); const html=run('images(0)');
  // ‏#1300: "256 GB ומעלה" בשורה אחת (console-full.html fitsDisk)
  assert.match(html,/title="254908301312 bytes"><b><bdi dir="ltr">256 GB<\/bdi><\/b> ומעלה<\/span><span class="sub muted">נדרש לפחות <bdi dir="ltr">255 GB<\/bdi>/);
  assert.match(html,/title="255000000000 bytes"><b><bdi dir="ltr">256 GB<\/bdi><\/b> ומעלה<\/span><span class="sub muted">נדרש לפחות <bdi dir="ltr">255 GB<\/bdi>/);
  assert.doesNotMatch(html,/<span class="sub">Windows 11 \+ Office<\/span>/,'#1300: low rows — no description under the name (it is in the drawer)');
  const teacher=html.slice(html.indexOf('img_a1b2c3'),html.indexOf('img_2c8e04'));
  assert.match(teacher,/class="muted">—<\/span>/); assert.doesNotMatch(teacher,/>0 GB</);
  const cad=html.slice(html.indexOf('img_2c8e04'),html.indexOf('img_lnx001'));
  assert.match(cad,/class="muted">—<\/span>/,'field absent (pre-#953 server) is unknown, not invented');
  assert.doesNotMatch(html,/סבב פתוח · כיתה 303|סבב שיכפול · גל 2/,'#1300: usage is not a table column any more');
  run('openImageDetail("img_7f3a91")');
  assert.match(run('document.getElementById("drawerBody").innerHTML'),/<span class="n-chip n">בשימוש: סבב פתוח · כיתה 303<\/span>/,'usage comes from /overview only — now a chip at the top of the drawer');
  assert.equal((html.match(/st ok">sha256 אומת</g)||[]).length,4,'every image in the library was verified on entry');
  assert.doesNotMatch(html,/st ok">sha256 תקין/,'"תקין now" only after a scrub in this session');
  assert.match(html,/02\/09\/2026/); assert.match(html,/54\.0 GB/);
  const menu=JSON.parse(html.match(/aria-label="פעולות עבור Office 2024 — סטנדרט" data-menu="([^"]*)"/)[1].replaceAll('&quot;','"').replaceAll('&#39;',"'"));
  assert.deepEqual(menu.map(i=>i[0]),['פרטים','הפץ','שינוי שם…','העבר לתיקייה…','מחיקה…'],'stage 2: one ⋯ menu per row, destructive last');
  assert.equal(menu[4][2],'danger'); assert.equal(menu[1][1],"deployImage('img_7f3a91')");
  assert.doesNotMatch(html,/class="btn sm" onclick="deployImage/,'no inline buttons per row any more');
});

test('a running capture is a grey row inside the table, under "all images", with cancel — not a bar above', () => {
  const {run}=setup(); const html=run('images(0)');
  assert.match(html,/<tr class="task">/); assert.match(html,/Office 2024 v3/); assert.match(html,/ממתין שמחשב הבנייה בנייה 1 יעלה ב-PXE/);
  assert.match(html,/cancelCapture\(decodeURIComponent\('t1'\)\)">בטל קליטה/); assert.match(html,/id="capture-bar"><\/div>/,'the bar above the table carries only done+error warnings');
  assert.match(html,/indeterminate|לא ידוע/,'pending capture with no denominator is not 0%');
  run('selectImagesFolder("Office")'); assert.doesNotMatch(run('images(0)'),/<tr class="task">/,'/tasks has no folder (#968) — shown only under all images');

  run('CAPTURE_TASKS=[{id:"t9",name:"office365",state:"done",error:"sda: המקור לא הוחזר לגודלו"}]');
  const warned=run('images(0)'); assert.match(warned,/id="capture-bar"><div class="note warn">.*office365.*המקור לא הוחזר לגודלו/,'#927: a done+error capture stays visible above the table');
});

test('folder selection, breadcrumb, description, edit/delete of the folder, and the text filter', () => {
  const {run,node}=setup();
  run('selectImagesFolder("Office")'); let html=run('images(0)');
  assert.match(html,/onclick="selectImagesFolder\(null\)">כל האימג'ים<\/a><span>\/<\/span><b>Office<\/b>/);
  assert.match(html,/— אימג׳י משרד/); assert.match(html,/עריכת תיקייה/); assert.doesNotMatch(html,/מחק תיקייה/,'a folder with images cannot be deleted');
  assert.equal((html.match(/data-id="img_/g)||[]).length,2);
  run('selectImagesFolder("%D7%A1%D7%99%D7%99%D7%91%D7%A8")'); html=run('images(0)');
  assert.match(html,/התיקייה ריקה/); assert.match(html,/מחק תיקייה/); assert.match(html,/<b class="et">התיקייה ריקה<\/b>גררו אליה אימג/,'stage 2: the empty folder has a title and a hint');
  run('selectImagesFolder("")'); html=run('images(0)');
  assert.match(html,/<b>ללא תיקייה<\/b>/); assert.equal((html.match(/data-id="img_/g)||[]).length,1); assert.match(html,/CAD Heavy/);
  run('selectImagesFolder(null); imagesFilter("ubuntu")');
  assert.equal((node('#img-table').innerHTML.match(/data-id="img_/g)||[]).length,1);
  run('imagesFilter("zzz")'); assert.match(node('#img-table').innerHTML,/אין אימג&#39;ים שתואמים לסינון/);
  run('imagesFilter("")'); assert.equal((node('#img-table').innerHTML.match(/data-id="img_/g)||[]).length,4);
});

test('an empty library says what to do; an unread library is a placeholder, not "no images"', () => {
  const {run}=setup();
  run('IMAGES=[];FOLDERS=[];CAPTURE_TASKS=[]'); const html=run('images(0)');
  assert.match(html,/אין אימג&#39;ים בספרייה — קלוט ממחשב בנייה או העלה קובץ tar/); assert.match(html,/onclick="openCapture/);
  run('IMAGES=null'); assert.match(run('images(0)'),/טוען נתונים/); assert.doesNotMatch(run('images(0)'),/אין אימג'ים/);
});

test('multi-select: bar counts, move-to-folder PUTs every selected image, delete is one confirm sheet listing the names (#1270)', async () => {
  const {run,node,requests}=setup();
  run('toggleImgSel("img_7f3a91",true); toggleImgSel("img_a1b2c3",true)');
  let html=run('images(0)');
  assert.match(html,/נבחרו 2 מתוך 4/); assert.match(html,/bulkMove\(\)">העבר לתיקייה…/); assert.match(html,/bulkDelete\(\)">מחיקה/);
  assert.doesNotMatch(html.slice(html.indexOf('dg-bar foot')),/שינוי שם/,'rename is a single-image action'); assert.equal((html.match(/<tr class="sel"/g)||[]).length,2);
  run('loadImages=async()=>{}; bulkMove()');
  await run('formOptions.onSubmit({folder:"Linux"})');
  const puts=requests.filter(r=>r.options.method==='PUT');
  assert.deepEqual(puts.map(r=>r.url).sort(),['/api/console/images/img_7f3a91','/api/console/images/img_a1b2c3']);
  assert.equal(JSON.parse(puts[0].options.body).folder,'Linux'); assert.equal(run('IMG.sel.size'),0);
  run('toggleImgSel("img_7f3a91",true); toggleImgSel("img_2c8e04",true); bulkDelete()');
  assert.equal(run('formOptions.fields'),undefined,'#1270: no typed fields'); assert.equal(run('formOptions.danger'),true);
  assert.match(run('formOptions.sub'),/Office 2024 — סטנדרט/); assert.match(run('formOptions.sub'),/CAD Heavy/); assert.match(run('formOptions.note'),/class="n-impact"/,'consequences as an impact list');
  assert.ok(!requests.some(r=>r.url.endsWith('/delete')),'nothing deleted before the sheet is confirmed');
  await run('formOptions.onSubmit({})');
  const dels=requests.filter(r=>r.url.endsWith('/delete'));
  assert.equal(dels.length,2); assert.equal(JSON.parse(dels[1].options.body).confirm_name,'CAD Heavy');
  run('toggleImgSel("img_lnx001",true); toggleImgSelAll(true)'); assert.match(run('images(0)'),/נבחרו 4 מתוך 4/);
  run('imgSelClear()'); assert.doesNotMatch(run('images(0)'),/dg-bar foot/);
});

test('drag: image onto a folder = PUT folder (selection travels), folder onto folder = /folders/order, row onto row = sort', async () => {
  const {run,requests,ctx}=setup(); run('loadImages=async()=>{}');
  const html=run('images(0)');
  assert.match(html,/draggable="true" ondragstart="imgDragStart\(event,'img_7f3a91'\)"/);
  assert.match(html,/ondrop="imgDrop\(event,'Linux'\)"/); assert.match(html,/ondrop="imgDrop\(event,''\)"/,'"no folder" is a drop target');
  assert.doesNotMatch(html,/ondrop="imgDrop\(event,null\)"/,'"all images" is not a folder');
  const data={}; ctx.EV=dragEvent(data);
  run('IMG.sel.add("img_7f3a91"); IMG.sel.add("img_a1b2c3"); imgDragStart(EV,"img_7f3a91")');
  assert.deepEqual(JSON.parse(data['application/x-imagesrv-images']),['img_7f3a91','img_a1b2c3'],'dragging a selected row drags the whole selection');
  await run('imgDrop(EV,"Linux")');
  const puts=requests.filter(r=>r.options.method==='PUT').map(r=>[r.url,JSON.parse(r.options.body).folder]);
  assert.deepEqual(puts,[['/api/console/images/img_7f3a91','Linux'],['/api/console/images/img_a1b2c3','Linux']]);
  const fdata={}; ctx.EV=dragEvent(fdata);
  run('folderDragStart(EV,"Linux")'); await run('imgDrop(EV,"Office")');
  const order=requests.find(r=>r.url==='/api/console/folders/order');
  assert.deepEqual(JSON.parse(order.options.body).names,['Linux','Office','סייבר']);
  requests.length=0; const rdata={}; ctx.EV=dragEvent(rdata);
  run('IMG.sel.clear(); imgDragStart(EV,"img_a1b2c3")'); await run('imgDropOnRow(EV,"img_7f3a91")');
  const sorts=requests.filter(r=>r.options.method==='PUT').map(r=>[r.url.split('/').pop(),JSON.parse(r.options.body).sort]);
  assert.deepEqual(sorts,[['img_a1b2c3',1],['img_7f3a91',2]]);
  requests.length=0; await run('imgDropOnRow(EV,"img_lnx001")');
  assert.equal(requests.length,0,'no reorder across folders');
});

test('the image drawer: header line, actions, verification note, properties, partitions loading, history from journal + tasks', async () => {
  const {run,node,requests}=setup();
  run('openImageDetail("img_lnx001")');
  assert.equal(node('#drawerTitle').textContent,'Ubuntu 24.04');
  let html=node('#drawerBody').innerHTML; balanced(html);
  // ‏#1300 (console-full.html W.imgDrawer): משנה קצרה (מערכת · גודל · נקלט מ-) ותגיות sha256 / בשימוש
  assert.match(html,/class="page drw"/); assert.match(html,/<div class="obj-sub">לינוקס · <bdi dir="ltr">[^<]+<\/bdi> · נקלט <bdi dir="ltr">12\/09\/2026<\/bdi> מ-בנייה 1<span class="n-meta"><span class="n-chip ">sha256 אומת<\/span><span class="n-chip n">בשימוש: סבב שיכפול · גל 2<\/span><\/span><\/div>/);
  assert.match(html,/<span class="k">מזהה<\/span><span class="v"><span class="mono">img_lnx001<\/span>/);
  assert.ok(html.indexOf('<div class="sec">פרטים</div>')<html.indexOf('sha256 של כל מחיצה אומת'),'the sha256 note sits under the details (bottom of the overview)');
  assert.match(html,/class="btn primary" onclick="openRoundWithImage\('img_lnx001'\)">פתח סבב עם אימג' זה/,'console-full.html S148: the primary action opens a round with this image'); assert.match(html,/onclick="renameImage\('img_lnx001'\)">שינוי שם/); assert.match(html,/class="btn sm danger" onclick="deleteImage\('img_lnx001'\)">מחיקה…/); assert.match(html,/href="\/api\/console\/images\/img_lnx001\/download">הורדה/);
  assert.match(html,/sha256 של כל מחיצה אומת בכניסה לספרייה/); assert.match(html,/scrubImage\('img_lnx001'\)">הרץ עכשיו/);
  assert.match(html,/<span class="k">תיקייה<\/span><span class="v">Linux <a[^>]*>העבר/);
  assert.match(html,/<div class="sec" data-cnt="3">מחיצות<\/div>/,'#1300: the partition count is the tab counter');
  assert.doesNotMatch(html,/<span class="k">(מחיצות|שימוש|נכנס לדיסק מ-)<\/span>/,'#1300: the overview is ~5 rows + "minimal drive"');
  assert.match(html,/טוען את טבלת המחיצות/,'before the manifest arrives: loading, not an empty table');
  assert.doesNotMatch(html,/דורש API/); assert.doesNotMatch(html,/Secure Boot/);
  assert.match(html,/נקלט ממחשב הבנייה בנייה 1 · sda/,'capture history from /tasks');
  assert.match(html,/deleteImage\([^)]*\)">מחיקה…</); assert.doesNotMatch(html,/הקלדת שם/);
  const j=requests.find(r=>r.url.startsWith('/api/console/journal?'));
  assert.equal(new URL(j.url,'http://x').searchParams.get('q'),'Ubuntu 24.04');
  await new Promise(r=>setImmediate(r)); await new Promise(r=>setImmediate(r));
  html=node('#drawerBody').innerHTML;
  assert.match(html,/אימג&#39; עודכן — &quot;Ubuntu 24.04&quot; — עודכנו: תיאור/); assert.match(html,/12\/09\/2026/);
  assert.ok(html.indexOf('אימג\' עודכן')<html.indexOf('נקלט ממחשב הבנייה'),'newest first');
});

test('a scrub result overrides the verification column and fills the partition table with per-file sha256 states', async () => {
  const {run,node,fixtures}=setup(); run('renderCurrent=()=>{}');
  fixtures['/images/img_7f3a91/scrub']={intact:false,images:[{id:'img_7f3a91',state:'drift',files:[{index:1,file:'p1.esp.pcl.zst',state:'ok'},{index:3,file:'p3.win.pcl.zst',state:'mismatch'}]}]};
  await run('scrubImage("img_7f3a91")');
  const html=run('images(0)');
  assert.match(html,/st err">sha256 לא תואם: p3\.win\.pcl\.zst/);
  const drawer=node('#drawerBody').innerHTML;
  assert.match(drawer,/note err/); assert.match(drawer,/אין להפיץ אותו/);
  assert.match(drawer,/<th>sha256<\/th>/); assert.match(drawer,/st ok">תקין/); assert.match(drawer,/st err">mismatch/);
  assert.match(run('images(2)'),/sha256 לא תואם/,'storage tab lists the session results');
});

test('captures tab lists /tasks with state colours; storage tab reads /overview and never shows unread as empty', () => {
  const {run}=setup();
  let html=run('images(1)'); balanced(html);
  assert.match(html,/<th>מחשב בנייה<\/th>/); assert.match(html,/st warn">ממתין שהמחשב יעלה ב-PXE/); assert.match(html,/st ok">הושלם/);
  assert.match(html,/cancelCapture\(decodeURIComponent\('t1'\)\)">ביטול/); assert.match(html,/openImageDetail\('img_lnx001'\)">פרטים/);
  run('CAPTURE_TASKS=[]'); assert.match(run('images(1)'),/אין קליטות עדיין/);
  html=run('images(2)'); balanced(html);
  assert.match(html,/פנוי בדיסק האימג&#39;ים/); assert.match(html,/312 GB/); assert.match(html,/688 GB<\/bdi> בשימוש מתוך <bdi dir="ltr">1000 GB/); assert.match(html,/aria-valuenow="69"/); assert.match(html,/<span class="pct">69%<\/span>/,'stage 2: the percent sits next to the usage bar');
  assert.match(html,/scrubLibrary\(\)">אמת את כל הספרייה/); assert.doesNotMatch(html,/דורשת API/);
  assert.match(html,/התוצאה השמורה האחרונה לכל אימג/,'#972: the saved result exists now — the empty text points to it');
  run('OVERVIEW=null'); html=run('images(2)'); assert.match(html,/לא נקרא/); assert.doesNotMatch(html,/aria-valuenow/);
});

test('deploy role: browse, deploy, download and details — no capture, upload, folders, move, rename, delete, scrub, drag', () => {
  const {run,node,requests}=setup(); run('ME.role="deploy"');
  const html=run('images(0)');
  for(const bad of ['openCapture','openImageIngest','addFolderSheet','draggable=','ondrop=','bulkMove','bulkDelete','עריכת תיקייה','cancelCapture']) assert.doesNotMatch(html,new RegExp(bad),bad);
  assert.match(html,/deployImage\(/); assert.match(html,/<input type="checkbox"/);
  run('toggleImgSel("img_7f3a91",true)'); const bar=run('images(0)');
  assert.match(bar,/bulkDownload\(\)">הורדה/); assert.doesNotMatch(bar,/מחיקה|העבר לתיקייה|שינוי שם/);
  run('openImageDetail("img_7f3a91")'); const d=node('#drawerBody').innerHTML;
  assert.match(d,/הורדה/); assert.doesNotMatch(d,/scrubImage|renameImage|deleteImage|moveImage|editImageDescription/);
  assert.match(d,/היסטוריית היומן זמינה למנהל בלבד/);
  assert.ok(requests.some(r=>r.url==='/api/console/images/img_7f3a91'),'#972: the manifest is for every logged-in user');
  assert.ok(!requests.some(r=>r.url.startsWith('/api/console/journal')),'the journal stays admin-only');
  assert.doesNotMatch(run('images(2)'),/scrubLibrary/);
});

const tick=async()=>{ for(let i=0;i<3;i++) await new Promise(r=>setImmediate(r)); };

test('#972: the drawer shows the partition table from the manifest — null used is "not measured", swap has no file, shrink and boot_ca shown', async () => {
  const {run,node,requests}=setup();
  run('openImageDetail("img_lnx001")'); await tick();
  const html=node('#drawerBody').innerHTML; balanced(html);
  assert.ok(requests.some(r=>r.url==='/api/console/images/img_lnx001'),'GET /images/{id}');
  for(const col of ['#','תפקיד','מערכת קבצים','גודל','בשימוש','כווץ מ-','sha256']) assert.match(html,new RegExp('<th>'+col+'</th>'));
  const root=html.slice(html.indexOf('<td>3</td>')); assert.ok(root.length>0);
  assert.match(root,/<td>linux<\/td><td>ext4<\/td>/);
  assert.match(root,/60\.0 GB/); assert.match(root,/<span class="muted">לא נמדד<\/span>/,'used_bytes null is not measured, not 0');
  assert.match(root,/title="255013683200 bytes"><bdi dir="ltr">238 GB/,'shrunk_from_bytes');
  assert.match(root,/title="9f9f/); assert.match(root,/>9f9f9f9f9f9f…</);
  assert.match(html,/<td>2<\/td><td>swap<\/td>.*לא נשמר \(swap\)/);
  const esp=html.slice(html.indexOf('<td>1</td>'),html.indexOf('<td>2</td>'));
  assert.match(esp,/30\.0 MB/); assert.match(esp,/<span class="muted">—<\/span>/,'no shrink = dash');
  assert.match(html,/<span class="k">נקלט על ידי<\/span><span class="v">nadav</);
  assert.match(html,/<span class="k">טבלת מחיצות<\/span><span class="v">gpt · סקטור <bdi dir="ltr">512 B/);
  assert.match(html,/<span class="k">רשויות אתחול<\/span><span class="v">Microsoft Corporation UEFI CA 2011 · Microsoft UEFI CA 2023</);
  assert.match(html,/<span class="k">דחיסה<\/span><span class="v">zstd-3 · partclone 0\.3\.27</);
  assert.doesNotMatch(html,/דורש API|טוען את טבלת המחיצות/);
});

test('#972: boot_ca null says why, absent says "not recorded", and a manifest that failed to load is a warning — not an empty table', async () => {
  const {run,node,fixtures}=setup();
  Object.assign(fixtures['/images/img_lnx001'],{boot_ca:null,boot_ca_error:'no ESP mounted'});
  run('openImageDetail("img_lnx001")'); await tick();
  assert.match(node('#drawerBody').innerHTML,/<span class="muted">לא נגזר<\/span> — no ESP mounted/);
  delete fixtures['/images/img_lnx001'].boot_ca; delete fixtures['/images/img_lnx001'].created_by;
  run('openImageDetail("img_lnx001")'); await tick();
  let html=node('#drawerBody').innerHTML;
  assert.match(html,/רשויות אתחול<\/span><span class="v"><span class="muted">לא נרשם/);
  assert.match(html,/נקלט על ידי<\/span><span class="v"><span class="muted">לא נרשם/);
  fixtures['/images/img_7f3a91']=new Error('network down');
  run('openImageDetail("img_7f3a91")'); await tick();
  html=node('#drawerBody').innerHTML;
  assert.match(html,/note warn.*המניפסט לא נקרא: network down/); assert.doesNotMatch(html,/אין מחיצות במניפסט|<th>תפקיד<\/th>/);
});

test('#972: the saved scrub result drives the column and the drawer; a scrub in this session still wins', async () => {
  const {run,node,fixtures}=setup(); run('renderCurrent=()=>{}');
  run('IMAGES[0].last_scrub={ts:"2026-09-20T08:15:00+00:00",state:"intact"}; IMAGES[3].last_scrub={ts:"2026-09-21T07:00:00+00:00",state:"drift"}');
  let html=run('images(0)');
  assert.match(html,/st ok">sha256 תקין · 20\/09\/2026</); assert.match(html,/st err">sha256 לא תואם · 21\/09\/2026</);
  assert.equal((html.match(/st ok">sha256 אומת</g)||[]).length,2,'never re-verified = verified on entry');
  run('openImageDetail("img_lnx001")');
  html=node('#drawerBody').innerHTML;
  assert.match(html,/note err.*האימות החוזר האחרון \(<bdi dir="ltr">21\/09\/2026 07:00<\/bdi>\) מצא sha256 לא תואם/);
  run('openImageDetail("img_7f3a91")');
  assert.match(node('#drawerBody').innerHTML,/note ok.*אימות חוזר אחרון <bdi dir="ltr">20\/09\/2026 08:15/);
  fixtures['/images/img_7f3a91/scrub']={intact:false,images:[{id:'img_7f3a91',state:'drift',files:[{index:1,file:'p1.esp.pcl.zst',state:'ok'},{index:3,file:'p3.win.pcl.zst',state:'mismatch'}]}]};
  await run('scrubImage("img_7f3a91")'); await tick();
  assert.match(run('images(0)'),/st err">sha256 לא תואם: p3\.win\.pcl\.zst/,'this session beats the saved "intact"');
  html=node('#drawerBody').innerHTML;
  assert.match(html,/<td>3<\/td><td>windows<\/td>.*st err">mismatch/,'per-file state lands on its partition row');
});

test('#972: rounds in the last 30 days — a number, "none" for a measured 0, and unknown for null or a server without the field', () => {
  const {run,node}=setup();
  // ‏#1300: הספירה — שורת "סבבים" בלשונית ההיסטוריה; "בשימוש עכשיו" — תגית בראש המגירה
  const usage=()=>node('#drawerBody').innerHTML.match(/<span class="k">סבבים<\/span><span class="v">(.*?)<\/span><\/div>/)[1];
  run('IMAGES[3].rounds_30d=4; openImageDetail("img_lnx001")');
  assert.match(usage(),/^4 סבבים ב-30 הימים האחרונים$/);
  assert.match(node('#drawerBody').innerHTML,/n-chip n">בשימוש: סבב שיכפול · גל 2</);
  run('IMAGES[2].rounds_30d=0; openImageDetail("img_2c8e04")');
  assert.match(usage(),/^אין סבבים ב-30 הימים האחרונים$/);
  assert.match(node('#drawerBody').innerHTML,/n-chip n">לא בסבב פעיל</);
  run('IMAGES[2].rounds_30d=1; openImageDetail("img_2c8e04")'); assert.match(usage(),/סבב אחד ב-30 הימים האחרונים/);
  run('IMAGES[2].rounds_30d=null; openImageDetail("img_2c8e04")'); assert.match(usage(),/לא ידוע/); assert.doesNotMatch(usage(),/אין סבבים|0 סבבים/);
  run('delete IMAGES[1].rounds_30d; openImageDetail("img_a1b2c3")'); assert.match(usage(),/לא ידוע/);
});

test('the capture list re-renders the page only when the tasks actually changed (selection survives polling)', async () => {
  const {run,fixtures}=setup(); let renders=0; run('renderCurrent=()=>{globalThis.n=(globalThis.n||0)+1}; renderActivity=()=>{}');
  await run('loadCaptures()'); await run('loadCaptures()');
  renders=run('globalThis.n'); assert.equal(renders,1);
  fixtures['/tasks'][0].state='running'; await run('loadCaptures()'); assert.equal(run('globalThis.n'),2);
});

test('the pages table owns the new page and the old renderer entry is gone', () => {
  const {run}=setup();
  assert.equal(run('pages.images.own'),true); assert.equal(JSON.stringify(run('pages.images.tabs')),JSON.stringify(['קבצים','קליטות','אחסון']));
  assert.match(run('layout(pages.images,1)'),/class="page aligned"/);
  assert.equal(run('typeof filterImagesFolder'),'undefined'); assert.equal(run('typeof reorderImage'),'undefined');
});

test('#1215: an image on an unavailable location is "not checked", never "sha256 mismatch"; drift still is', async () => {
  const {run,node,fixtures}=setup(); run('renderCurrent=()=>{}');
  fixtures['/images/scrub']={intact:false,images:[
    {id:'img_7f3a91',state:'unavailable',files:[]},
    {id:'img_2c8e04',state:'drift',files:[{index:3,file:'p3.win.pcl.zst',state:'mismatch'}]},
    {id:'img_lnx001',state:'intact',files:[{index:1,file:'p1.esp.pcl.zst',state:'ok'}]}]};
  await run('scrubLibrary()'); await tick();
  let html=run('images(0)');
  assert.match(html,/st warn">המיקום לא זמין · לא נבדק</);
  assert.equal((html.match(/sha256 לא תואם/g)||[]).length,1,'only the drifted image is "mismatch"');
  assert.match(html,/st err">sha256 לא תואם: p3\.win\.pcl\.zst/);
  const storage=run('images(2)');
  assert.match(storage,/st warn">המיקום לא זמין · לא נבדק</);
  assert.equal((storage.match(/sha256 לא תואם/g)||[]).length,1,'storage tab: one mismatch row, the drifted one');
  run('openImageDetail("img_7f3a91")');
  const drawer=node('#drawerBody').innerHTML;
  assert.match(drawer,/note warn.*המיקום לא זמין · לא נבדק/); assert.doesNotMatch(drawer,/לא תואם|אין להפיץ אותו/);
  run('openImageDetail("img_2c8e04")');
  assert.match(node('#drawerBody').innerHTML,/note err.*sha256 לא תואם/);
  assert.match(node('#toast').textContent,/פגום/,'a real drift still says corrupt');
  fixtures['/images/img_7f3a91/scrub']={intact:false,images:[{id:'img_7f3a91',state:'unavailable',files:[]}]};
  await run('scrubImage("img_7f3a91")'); await tick();
  assert.match(node('#toast').textContent,/לא זמין/); assert.doesNotMatch(node('#toast').textContent,/פגום|לא תואם/);
});

test('final mockup: the delete-image window lists what happens; "in use" unknown is never presented as free (principle 5)', () => {
  const {run}=setup();
  const id='encodeId(IMAGES[0].id)';
  run(`OVERVIEW=null; deleteImage(${id})`);
  assert.equal(run('formOptions.title'),"למחוק את האימג'?"); assert.equal(run('formOptions.submitLabel'),'מחיקה'); assert.equal(run('formOptions.danger'),true);
  const unknown=run('formOptions.note');
  assert.match(unknown,/class="n-impact"/); assert.match(unknown,/לא נבדק אם האימג' בשימוש/); assert.doesNotMatch(unknown,/אפשר למחוק בבטחה/);
  run(`OVERVIEW={}; deleteImage(${id})`);
  assert.match(run('formOptions.note'),/אפשר למחוק בבטחה/,'/overview was read and the image is not in use');
  assert.match(run('formOptions.note'),/הפעולה אינה הפיכה/);
});

test('final mockup: "פתח סבב עם אימג\' זה" selects the image in the round form and opens the round window',async()=>{
  const {run}=setup();
  run('roomOperator=()=>true; globalThis.opened=0; selectPageById=(p)=>{ globalThis.went=p; }; closeDrawer=()=>{}; openRoomRound=()=>{ globalThis.opened++; }; api=async()=>({round:null,machines:[]})');
  await run('openRoundWithImage("img_lnx001")');
  assert.equal(run('DEPLOY.form.image'),'img_lnx001'); assert.equal(run('DEPLOY.form.src'),'library'); assert.equal(run('went'),'deploy');
  assert.equal(run('opened'),1,'the round window opens with the image already chosen');
  run('roomOperator=()=>false; DEPLOY.form.image=""; went=""; opened=0');
  await run('openRoundWithImage("img_lnx001")');
  assert.equal(run('DEPLOY.form.image'),''); assert.equal(run('went'),''); assert.equal(run('opened'),0);
});

test('library "הפץ" opens the round with this image; an active round shows a clear message with a link, no new choice',async()=>{
  const {run}=setup();
  run('roomOperator=()=>true; globalThis.opened=0; globalThis.went=""; selectPageById=(p)=>{ globalThis.went=p; }; closeDrawer=()=>{}; openRoomRound=()=>{ globalThis.opened++; }; api=async()=>({round:null,machines:[]}); DEPLOY.form.image=""');
  await run('deployImage("img_7f3a91")');
  assert.equal(run('DEPLOY.form.image'),'img_7f3a91','the image is already in the round form'); assert.equal(run('went'),'deploy'); assert.equal(run('opened'),1);
  run('DEPLOY.form.image=""; went=""; opened=0; globalThis.modal=null; openModalContent=(t,b,ok,fn)=>{ globalThis.modal={t,b,ok,fn}; }; api=async()=>({round:{id:"rnd_1",image_id:"img_lnx001",image_name:"Ubuntu 24.04",wave_number:2,wave_state:"running"},machines:[]})');
  await run('deployImage("img_7f3a91")');
  assert.equal(run('modal.t'),'כבר יש סבב פעיל'); assert.match(run('modal.b'),/כבר יש סבב פעיל — Ubuntu 24\.04/); assert.match(run('modal.b'),/selectPageById\('deploy'\)/);
  assert.equal(run('DEPLOY.form.image'),''); assert.equal(run('opened'),0); assert.equal(run('went'),'');
  run('modal=null; globalThis.said=""; toast=(m)=>{ globalThis.said=m; }; api=async()=>{ throw new Error("boom"); }');
  await run('deployImage("img_7f3a91")');
  assert.match(run('said'),/החדר לא נקרא/,'room not read is an error, not an empty window'); assert.equal(run('opened'),0);
});

test('classroom deploy button is hidden when classrooms are off (v1); when on, an active round gets the same message',()=>{
  const {run}=setup();
  run('ME.capabilities={classrooms:false}');
  assert.equal(run('classroomsOn()'),false);
  assert.doesNotMatch(run('groupKpis({id:"grp_X",role:"classroom",label:"303"},[])'),/deployToGroup/);
  run('ME.capabilities={classrooms:true}; globalThis.n=0; globalThis.modal=null; roomOperator=()=>true; openModalContent=(t,b)=>{ globalThis.modal={t,b}; }; selectPageById=()=>{ globalThis.n++; }; OVERVIEW={session:{id:"s1",image_name:"Office",state:"open"}}; ROOM=null');
  assert.match(run('groupKpis({id:"grp_X",role:"classroom",label:"303"},[])'),/deployToGroup/);
  run('deployToGroup("grp_X")'); assert.equal(run('modal.t'),'כבר יש סבב פעיל'); assert.equal(run('n'),0);
  run('ME.capabilities={classrooms:false}; modal=null; deployToGroup("grp_X")'); assert.equal(run('modal'),null);
});

// ‏#1777: "הפץ" (תפריט השורה) ו"פתח סבב עם אימג' זה" (המגירה) — דרך חלון "התחל סבב" האמיתי, בלי להחליף
// את openRoomRound/openModalContent/selectPageById: הטסטים שלמעלה מחליפים אותם, ולכן לא היו נופלים אילו
// הכפתור חזר "רק לעבור לדף הסבב". כאן נבדק מה שהמפעיל רואה: החלון פתוח, האימג' הזה כבר נבחר בו,
// ושום סבב לא נפתח (אין POST) — פתיחה רק בלחיצה המפורשת על "פתח סבב".
test('#1777: both deploy buttons open the real round window with this image chosen, and start nothing', async () => {
  const opened = {};
  for (const call of ['deployImage("img_7f3a91")', 'openRoundWithImage("img_7f3a91")']) {
    const {run,node,requests,fixtures}=setup();
    fixtures['/room']={round:null,machines:[],disk_floor:null};
    run('DEPLOY.form.image=""; current="images"');
    await run(call);
    const body=node('#modalBody').innerHTML;
    assert.equal(run('current'),'deploy',call+': on the round page');
    assert.equal(node('#modalTitle').textContent,'התחל סבב',call+': the round window is open, not just the page');
    assert.match(body,/<option value="img_7f3a91"[^>]*selected>Office 2024 — סטנדרט/,call+': this image is already chosen');
    assert.match(body,/<option value="Office" selected>Office \(2\)/,call+': its folder too');
    assert.equal(node('#modalOk').textContent,'פתח סבב');
    assert.deepEqual(requests.filter((r)=>(r.options.method||'GET')!=='GET').map((r)=>r.url),[],call+': nothing is started without the operator');
    opened[call]=body;
  }
  assert.equal(opened['deployImage("img_7f3a91")'],opened['openRoundWithImage("img_7f3a91")'],'both buttons do the same thing');
});
