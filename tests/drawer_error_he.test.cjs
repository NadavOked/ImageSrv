// ‏drawerErrorText (console.js, #1376/#1612): סיבת הכשל של מגירה בכרטיס הסבב — נדב 17/10: אין אנגלית על המסך.
// ‏`error` מגיע מהסוכן גולמי. הטסט עובר על **כל** הסיבות שהסוכן יכול לשים בו (הרשימה למטה, עם המקור של כל אחת),
// ובודק שאף אחת לא מוצגת באנגלית. סיבות `fanout.c` נקראות מהקוד עצמו — סיבה חדשה שנוספה שם בלי מיפוי ב-console.js
// מפילה את הטסט הזה, ולא מתגלה מול מכונה. console.js רץ ב-vm מול DOM מזערי, כמו ב-strip_tags.test.cjs.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const agent=path.resolve(__dirname,'../agent');

function setup() {
  const el=()=>({value:'',textContent:'',innerHTML:'',hidden:false,dataset:{},style:{},classList:{add(){},remove(){},toggle(){},contains:()=>false},
    setAttribute(){},removeAttribute(){},addEventListener(){},removeEventListener(){},querySelector:()=>null,querySelectorAll:()=>[],closest:()=>null,focus(){}});
  const doc={querySelector:()=>el(),querySelectorAll:()=>[],getElementById:()=>el(),
    createElement:()=>el(),documentElement:{setAttribute(){},getAttribute:()=>'rtl'},addEventListener(){},body:{appendChild(){}}};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,RegExp,encodeURIComponent,decodeURIComponent,
    document:doc,window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},fetch:async()=>({status:200,ok:true,headers:{get:()=>null},json:async()=>({})})});
  for(const f of ['progress.js','console.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  return (raw)=>vm.runInContext(`drawerErrorText(${JSON.stringify(raw)})`,ctx);
}
const he=setup();

/* האורקל — **מחמיר מהקוד**: שמות כלים מרשימה סגורה בלבד (הרשימה כאן ב-console.js נפרדת בכוונה), מזהה = יש בו
   ספרה / נקודה / קו נטוי / קו תחתון / גיבוב. כל מילה לטינית אחרת (2+ אותיות) = אנגלית. כלי חדש בטקסט עברי של הסוכן
   מפיל את הטסט, והתיקון הוא החלטה: להוסיף לרשימה כאן ו-ב-DRAWER_TECH, או לנסח מחדש. */
const TOOLS=/\b(?:partclone|zstd|sgdisk|fanout|mkswap|mkfifo|ntfsresize|ntfsfix|ntfs|btrfs|xfs|dd|cmp|umount|blockdev|openssl|swap|rc|dirty|gpt|esp|guid|smart|uefi|bios|secure boot|windows|linux|mib|gib|gb|mb|udp-sender|dsend|dfanout|dzstd|dpcl)\b/gi;
const latinWords=(s)=>s.replace(/[A-Za-z0-9_./-]*[0-9_./][A-Za-z0-9_./-]*|\b[0-9a-f]{8,}\b/gi,' ').replace(TOOLS,' ').match(/[A-Za-z]{2,}/g)||[];
const GENERIC='הכתיבה נכשלה — הפרטים בריחוף על הדיסק';

test('the oracle itself: it flags English and lets the agent\'s own Hebrew-with-tool-names through (not vacuous)',()=>{
  assert.deepEqual(latinWords('queue overflow: target lost bytes'),['queue','overflow','target','lost','bytes']);
  assert.deepEqual(latinWords('מחיצה 2: הכתיבה נכשלה (partclone rc=1, ממסר rc=0) — sha256 xxh64 /dev/sdb 3.2 GB'),[]);
});

/* ---- סיבות fanout.c: נקראות מהקוד ---- */
const fanoutC=fs.readFileSync(path.join(agent,'fanout.c'),'utf8');
function fanoutReasons() {
  const out=new Set();
  for(const m of fanoutC.matchAll(/fail_target\s*\([^;]*?\);/gs))
    for(const s of m[0].matchAll(/"([^"%]+)"/g)) out.add(s[1]);          // ‏"%s stream (got …)" נבנית למטה
  for(const m of fanoutC.matchAll(/->reason\s*=\s*"([^"]+)"/g)) out.add(m[1]);
  out.add('unknown');                                                     // ‏printf("%s failed %s", …, t->reason ? t->reason : "unknown")
  assert.match(fanoutC,/t->reason \? t->reason : "unknown"/);
  assert.match(fanoutC,/"%s stream \(got %lld of %lld\)"/);
  out.add('short stream (got 4096 of 1048576)'); out.add('long stream (got 2097152 of 1048576)');
  return [...out];
}

test('fanout.c: every reason it can print is in Hebrew on the card (read from the C source, so a new one cannot slip by)',()=>{
  const reasons=fanoutReasons();
  assert.ok(reasons.length>=11,`expected the 8 fixed reasons + unknown + short/long stream, found ${reasons.length}: ${reasons}`);
  for(const r of reasons) {
    const shown=he(r);
    assert.deepEqual(latinWords(shown),[],`${r} -> ${shown}`);
    assert.notEqual(shown,GENERIC,`${r} has no mapping in DRAWER_ERROR_HE`);
    assert.doesNotMatch(shown,/הפרטים בריחוף/,`${r}: nothing was cut — the whole reason is said`);
  }
});

test('fanout reasons keep their numbers and their wording',()=>{
  assert.equal(he('queue overflow: target lost bytes'),'הדיסק לא עמד בקצב — אבדו בייטים');
  assert.equal(he('write error'),'שגיאת כתיבה לדיסק');
  assert.equal(he('stalled with no write progress'),'הדיסק לא עמד בקצב — הכתיבה נתקעה והוא הוצא מהסבב');
  assert.equal(he('stalled the stream (too slow to drain)'),'הדיסק לא עמד בקצב — הוא עיכב את הזרם והוצא מהסבב');   // #1796
  assert.equal(he('short stream (got 4096 of 1048576)'),'הזרם קצר מהצפוי — התקבלו 4096 מתוך 1048576 בייט');
  assert.equal(he('long stream (got 2097152 of 1048576)'),'הזרם ארוך מהצפוי — התקבלו 2097152 מתוך 1048576 בייט');
});

/* ---- העטיפות: איך הסוכן שם את סיבת fanout ב-error ---- */
test('the wrappers in the agent are the ones the console parses (verdict.sh _write_fail, lccopy.sh lc_judge)',()=>{
  assert.ok(fs.readFileSync(path.join(agent,'lib/verdict.sh'),'utf8').includes('partition $2: הכתיבה לדיסק נכשלה (fanout: ${3:-אין דיווח}; partclone rc=${_prc:-לא נרשם}${_plog:+; $_plog})'));
  assert.ok(fs.readFileSync(path.join(agent,'lib/lccopy.sh'),'utf8').includes('מחיצה $2: שגיאת כתיבה אחרי $_lj_at$LC_RLM — ${_lj_line#* failed }${_lj_log:+ · $_lj_log}'));
});
const PCL='Partclone fail, please check /var/log/partclone.log !';          // השורה האחרונה של partclone.log — גולמית
test('every fanout reason, inside both wrappers, with a partclone log line behind it: Hebrew, the log line is cut',()=>{
  for(const r of fanoutReasons()) {
    for(const raw of [`partition 2: הכתיבה לדיסק נכשלה (fanout: ${r}; partclone rc=1; ${PCL})`,
                      `מחיצה 2: שגיאת כתיבה אחרי 3.2 GB‏ — ${r} · ${PCL}`]) {
      const shown=he(raw);
      assert.deepEqual(latinWords(shown),[],`${raw} -> ${shown}`);
      assert.match(shown,/^מחיצה 2: /,raw);
      assert.match(shown,/הפרטים בריחוף על הדיסק$/,'the cut log line is said to be in the tooltip: '+shown);
      assert.doesNotMatch(shown,/Partclone|please check/,shown);
    }
  }
  assert.equal(he(`partition 2: הכתיבה לדיסק נכשלה (fanout: write error; partclone rc=1; ${PCL})`),
    'מחיצה 2: הכתיבה לדיסק נכשלה (שגיאת כתיבה לדיסק; partclone rc=1) — הפרטים בריחוף על הדיסק');
  assert.equal(he('partition 2: הכתיבה לדיסק נכשלה (fanout: ok; partclone rc=1)'),
    'מחיצה 2: הכתיבה לדיסק נכשלה (ההפצה הסתיימה בהצלחה, אך הכתיבה לדיסק נכשלה; partclone rc=1)');
});

/* ---- כל השאר: הסיבות שהסוכן כותב בעצמו (verdict.sh, drawers.sh, writecheck.sh, ntfsboot.sh, lccopy.sh, lcsnap.sh, …) ---- */
const AGENT_REASONS=[
  // [מקור, הסיבה כפי שהסוכן כותב אותה]
  ['verdict.sh:_read_fanout_report','partition 2: הכתיבה לדיסק נכשלה (fanout: ok (zstd rc לא נרשם); partclone rc=0)'],
  ['verdict.sh:_read_fanout_report','partition 2: הכתיבה לדיסק נכשלה (fanout: אין דיווח; partclone rc=לא נרשם)'],
  ['verdict.sh:_read_fanout_report','מחיצה 2: mkswap נכשל'],                       // ‏drawers.sh:77, ‏lccopy.sh:216
  ['drawers.sh:restore_partition_drawers','מחיצה 2: הפצת הזרם למגירות נכשלה (fanout rc=137)'],
  ['drawers.sh:restore_partition_drawers','מחיצה 2: ה-sha256 לא חושב -- אין ראיה שהזרם הגיע שלם'],
  ['drawers.sh:restore_partition_drawers','מחיצה 2: אי-התאמת sha256 בקובץ part2.zst'],
  ['drawers.sh:restore_partition_drawers','מחיצה 2: דוח fanout לא נקרא -- אין ראיה מי מהמגירות שרדה'],
  ['drawers.sh:restore_partition_drawers','מחיצה 2: הזרם נקטע לפני סופו (מקור rc=1)'],
  ['drawers.sh:restore_partition_drawers','מחיצה 2: פג הזמן -- הזרם לא הגיע'],
  ['drawers.sh:restore_partition_drawers','מחיצה 2: פג הזמן — המגירה לא סיימה לכתוב'],
  ['drawers.sh:run_restore_drawers','נכתבו 1 מתוך 3 מחיצות'],
  ['drawers.sh:run_restore_drawers','פג הזמן — טבלת המחיצות לא נכתבה תוך 300 שניות'],   // #1704
  ['ntfsboot.sh','מחיצה 3: סקטור אתחול הגיבוי של NTFS — הכתיבה לסקטור 1000 נכשלה (dd rc=1)'],
  ['ntfsboot.sh','מחיצה 3: סקטור אתחול הגיבוי של NTFS — המטמון לא רוקן (blockdev --flushbufs) — אין קריאה חוזרת מהדיסק'],
  ['ntfsboot.sh','מחיצה 3: סקטור אתחול הגיבוי של NTFS — ההשוואה לסקטור 0 לא בוצעה (cmp rc=2)'],
  ['ntfsboot.sh','מחיצה 3: סקטור אתחול הגיבוי של NTFS — סקטור 0 אינו סקטור אתחול של NTFS (לא נקרא)'],
  ['restore.sh:PLAN_ERROR','sgdisk דחה את תוכנית השחזור — הדיסק לא שונה'],
  ['manifest.sh:verify_unique_guids','מחיצה 2: ה-GUID הייחודי בדיסק הוא 1234 ולא 5678 (#26)'],
  ['writecheck.sh:_wc_why (busybox dd)',"הדיסק לא מקבל כתיבה — התחלה (0 MiB): dd: error writing '/dev/sdb': Input/output error"],
  ['writecheck.sh:_wc_why (busybox dd)',"הדיסק לא מקבל כתיבה — אמצע (120000 MiB): dd: error writing '/dev/sdb': No space left on device"],
  ['writecheck.sh:_wc_why (busybox dd)',"הדיסק לא מקבל כתיבה — סוף (239000 MiB): dd: can't open '/dev/sdb': No such file or directory"],
  ['writecheck.sh:_wc_why (busybox dd)',"הדיסק לא מקבל כתיבה — התחלה (0 MiB): dd: error writing '/dev/sdb': Read-only file system"],
  ['writecheck.sh:_wc_why (busybox dd)',"הדיסק לא מקבל כתיבה — התחלה (0 MiB): dd: can't open '/dev/sdb': Permission denied"],
  ['writecheck.sh:_wc_why (busybox dd)',"הדיסק לא מקבל כתיבה — התחלה (0 MiB): dd: can't open '/dev/sdb': Device or resource busy"],
  ['writecheck.sh:_wc_why (busybox dd)',"הדיסק לא מקבל כתיבה — התחלה (0 MiB): dd: error writing '/dev/sdb': Invalid argument"],
  ['writecheck.sh:_wc_why (busybox dd)',"הדיסק לא מקבל כתיבה — סוף (239000 MiB): הקריאה החוזרת נכשלה: dd: error reading '/dev/sdb': Input/output error"],
  ['writecheck.sh:_wc_why (fallback)','הדיסק לא מקבל כתיבה — סוף (239000 MiB): הקריאה החוזרת נכשלה: dd'],
  ['writecheck.sh:_wc_why (fallback)','הדיסק לא מקבל כתיבה — התחלה (0 MiB): הכתיבה נכשלה'],
  ['writecheck.sh:write_check','הדיסק לא מקבל כתיבה — לא סיים לכתוב ולקרוא בחזרה תוך 120 שניות'],
  ['writecheck.sh:write_speed_verdict','איטי — 12 MB/s, מתחת לסף 30 MB/s'],
  ['writecheck.sh:write_speed_verdict','איטי — 12 MB/s, פחות מחצי מ-80 MB/s של שאר המגירות'],
  ['diskfit.sh:disk_fits','הדיסק קטן מדי ב-12 MiB (נדרשים 256060514304 בייט, בדיסק 256000000000; חסרים 60514304 בייט)'],
  ['bootcadb.sh:boot_ca_preflight',"המחשב לא יאמין לאימג' — Secure Boot יחסום אתחול"],
  ['lccopy.sh:lc_judge','מחיצה 1: הכתיבה נכשלה אחרי 3.2 GB‏ (partclone rc=1, ממסר rc=0) · '+PCL],
  ['lccopy.sh:lc_judge','מחיצה 1: אין דיווח מהפיצול לאחר 3.2 GB‏ — אין ראיה שנכתב'],
  ['lccopy.sh:lc_judge','מחיצה 1: ה-xxh64 של מה שהיעד קיבל לא חושב — אין ראיה'],
  ['lccopy.sh:lc_judge','מחיצה 1: xxh64 של מה שהיעד קיבל (a1b2c3d4e5f6) שונה מהמקור (0f1e2d3c4b5a)'],
  ['lccopy.sh:lc_stream','מחיצה 1: קריאת המקור נכשלה (partclone rc=1) — המקור לא שונה'],
  ['lccopy.sh:lc_stream','מחיצה 1: mkfifo נכשל'],
  ['grow.sh:finish_grow (#1798)',"הכתיבה הושלמה, אך הסימון 'כתיבה לא הושלמה' לא הוסר מהדיסק"],
  ['lccopy.sh:_lc_gpt_why (שלוש שורות יומן אחרונות)','טבלת המחיצות לא נכתבה: sgdisk: Could not open /dev/sdb Warning! Main partition table overlaps the first partition by 34 blocks!'],
  ['lcplan.sh:lc_target_check','sgdisk לא קרא את הטבלה מהיעד'],
  ['lcplan.sh:lc_target_check','מחיצה 1: sgdisk -i נכשל ביעד'],
  ['lcplan.sh:lc_target_check','מחיצה 1: GUID ייחודי 1234 במקום 5678'],
  ['localclone.sh+lcsnap.sh','המקור לא כווץ, ולכן היעד לא נכתב: הכיווץ של העותק של מחיצה 2 נכשל (ntfsresize rc=1) — המקור לא נכתב; הפרטים ביומן'],
  ['localclone.sh+lcsnap.sh (LC_SNAP_TD)','המקור לא כווץ, ולכן היעד לא נכתב: ; העותק בזיכרון לא שוחרר (dmsetup remove lc-cut-2 failed; lc-src-2 is still listed; dmsetup ls failed -- the views could not be checked; rmmod brd failed; brd is still loaded) — המקור לא נכתב; אתחלו את המחשב לפני שיכפול נוסף'],
  ['lcsnap.sh','דגל ה-dirty לא נוקה בעותק של מחיצה 2 (ntfsfix -d rc=1) — המקור לא נכתב'],
  ['shrink.sh+localclone.sh',"לא הצלחנו לקרוא את כניסת מחיצה 2 בטבלה (sgdisk -i: דגלים '0x8000') — הדיסק לא שונה"],
  ['directsend.sh:_direct_failed','מחיצה 2: השליחה נכשלה (שלב=dsend rc=1)'],
  ['directsend.sh:_direct_failed','מחיצה 2: אף מחשב משכפל לא הצטרף לזרם (udp-sender לא התחיל את ההעברה)'],
  ['directsend.sh:_direct_failed','מחיצה 2: ה-sha256 של מה שנשלח לא חושב — אין ראיה שהזרם תאם למניפסט'],
  // #1805: the read-back after the write (readback.sh), with fanout --readback's own words in it
  ['readback.sh:rb_digest (rc 4)',"מחיצה 3: האימות אחרי הכתיבה נכשל — הדיסק לא שמר את מה שנכתב (הקריאה מהדיסק נכשלה: read error on /dev/sdb3 at byte 1048576: Input/output error)"],
  ['readback.sh:rb_digest (rc 3)','האימות אחרי הכתיבה לא רץ — אין ראיה שהדיסק שמר את מה שנכתב — מחיצה 3: מפת הבלוקים לא נקראה מהדיסק (the bit map failed its CRC)'],
  ['readback.sh:rb_flush','האימות אחרי הכתיבה לא רץ — אין ראיה שהדיסק שמר את מה שנכתב — ריקון המטמון של מחיצה 3 נכשל (blockdev --flushbufs rc=1)'],
  ['readback.sh:_rb_judge (map)',"מחיצה 1: האימות אחרי הכתיבה נכשל — הדיסק לא שמר את מה שנכתב (מפת הבלוקים בדיסק: 8192 בייט, באימג': 40960)"],
  ['readback.sh:_rb_part (a drawn chunk, #1864)','מחיצה 3: האימות אחרי הכתיבה נכשל — הדיסק לא שמר את מה שנכתב (חתיכה 17 של 4MiB מתוך 11000)'],
  ['readback.sh:_rb_part (fanout gave no list)','האימות אחרי הכתיבה לא רץ — אין ראיה שהדיסק שמר את מה שנכתב — מחיצה 3: fanout --readback לא החזיר רשימה'],
  ['writecheck.sh:_wc_alias','הדיסק לא מקבל כתיבה — סמן ב-0 MiB נקרא בחזרה שונה — הדיסק מחזיר תוכן של מקום אחר (קיבולת מזויפת?)'],
];

test(`the agent's own reasons (${AGENT_REASONS.length} sources with English in them): none is shown in English, and each still says something in Hebrew`,()=>{
  for(const [src,raw] of AGENT_REASONS) {
    const shown=he(raw);
    assert.deepEqual(latinWords(shown),[],`${src}: ${raw} -> ${shown}`);
    assert.notEqual(shown,GENERIC,`${src}: ${raw} -> nothing of it survived`);
    assert.match(shown,/[א-ת]{3}/,`${src}: ${shown}`);
  }
});

test('Hebrew with only tool names is left exactly as the agent wrote it (the old rule hid all of these behind a generic line)',()=>{
  for(const raw of ['מחיצה 2: mkswap נכשל','מחיצה 2: ה-sha256 לא חושב -- אין ראיה שהזרם הגיע שלם','איטי — 12 MB/s, מתחת לסף 30 MB/s',
                    'מחיצה 2: הזרם נקטע לפני סופו (מקור rc=1)','הדיסק נותק','הדיסק קטן מדי ב-12 MiB (נדרשים 256060514304 בייט, בדיסק 256000000000; חסרים 60514304 בייט)',
                    'מחיצה 3: האימות אחרי הכתיבה נכשל — הדיסק לא שמר את מה שנכתב'])   // #1805: Nadav's words, whole
    assert.equal(he(raw),raw);
});

test('mapped OS errors: dd keeps only the part that helps, in Hebrew',()=>{
  assert.equal(he("הדיסק לא מקבל כתיבה — התחלה (0 MiB): dd: error writing '/dev/sdb': Input/output error"),
    'הדיסק לא מקבל כתיבה — התחלה (0 MiB): שגיאת קריאה/כתיבה בדיסק — הפרטים בריחוף על הדיסק');
  assert.equal(he("dd: error writing '/dev/sdb': No space left on device"),'אין מספיק מקום בדיסק — הפרטים בריחוף על הדיסק');
});

test('what nobody mapped is cut, never shown: only English -> the generic line; null / empty / Hebrew behave as before',()=>{
  assert.equal(he('oops: weird'),GENERIC);
  assert.equal(he('Partclone fail, please check /var/log/partclone.log !'),GENERIC);
  assert.equal(he('segfault (core dumped)'),GENERIC);
  assert.equal(he('מחיצה 2: no space (core dumped)'),'מחיצה 2 — הפרטים בריחוף על הדיסק');
  assert.equal(he(null),'נכשל בכתיבה'); assert.equal(he(''),'נכשל בכתיבה'); assert.equal(he('הדיסק נותק'),'הדיסק נותק');
  assert.doesNotMatch(he('x'),/tooltip/,'"tooltip" is English too');
});
