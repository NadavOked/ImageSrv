<div dir="rtl">

# שכבה 3: ספים

כל מספר שהחלטה נשענת עליו, וממה הוא נקבע. **מספר בלי מקור הוא ציטוט, לא ראיה** (עיקרון 5ב).

**המקור** (`source` ב-`catalog.toml`):
- **מדידה** (`measured`): עם תאריך. **הטסט נופל על `measured` בלי `date`.** מדידה מהמחקר שהתאריך שלה
  לא ידוע מסומנת `research`, ולא `measured`.
- **נגזר** (`derived`): מחושב מקבוע אחר. **הערכה** (`estimate`): כתוב בקוד או בתכנון שזו הצעה.
- **הכרעה** (`decision`): של נדב או של המתאם. **מדיניות** (`policy`). **חיצוני** (`external`): ‏FOG,
  ‏Clonezilla, ‏man page. **לא ידוע** (`unknown`): אין הערה עם מקור, או לא נקרא.

**איך קוראים את עמודת "איפה":** ‏`קובץ:שורה` · ‏`סימבול`. **הסימבול הוא המצביע, והשורה היא רמז בלבד**
(נכונה ל-`cf58981a`). ‏«טקסט» בסוגריים הוא עוגן: מחרוזת שמופיעה באותו קובץ, ליד הכלל, כשהסימבול
הוא פונקציה גדולה (למשל `_send` ב-`chunksend.py`). בפריט שעוד לא קיים, המיקום הוא המקום שבו הנתון
הגולמי כבר יושב, או שבו המלאי מציע להוסיף אותו.

## T: ספים שקיימים היום

| # | סף | ערך | איפה | מקור | החלטה | המלצה | למה |
|---|---|---|---|---|---|---|---|
| T1 | `THRESHOLD`: ממספר מחשבים זה, חתיכה חסרה משודרת שוב ב-multicast | 2 | `server/rounds/repair.py:37` · `THRESHOLD` | הערכה: "הצעה של התכנון ולא מדידה" (docstring של repair.py) | D8 | ⏳ אחר כך | סף של D8. לפני פרסום לציבורי: ראה הכרעה פנימית. |
| T2 | `MAX_REPAIRS` | 5 | `server/rounds/repair.py:39` · `MAX_REPAIRS` | הערכה: אותו מקור כמו T1 | D12 | ✅ קיים, להשאיר | קיים (הערכה); לא גרם לכשל. |
| T3 | `MAX_MISSING_PERMILLE` (הוסר ב-#3170; הסף היום הוא T43) | הוסר (היה 500). הסף: `writecheck_min_mbps` (T43) | `server/rounds/repair.py:46` · `slow_reason` | הערכה: אותו מקור כמו T1 | D11 | ✅ קיים, להשאיר | הקבוע הוסר ב-#3170; `slow_reason` שופט לפי המהירות המינימלית לכונן. למצטרף מאוחר זה X18. |
| T4 | `WRITE_MARGIN` | 0.9 | `server/rounds/chunksend.py:60` · `WRITE_MARGIN` | נגזר: מ-`drive_rates.MARGIN` | D7 | ✅ קיים, להשאיר | קיים; חלק מ-D7, נבדק ב-M13. |
| T5 | `ENDED_GRACE_S` | 150 | `server/rounds/chunksend.py:64` · `ENDED_GRACE_S` | קודם `lost + 120` של הסוכן; מ-18/11 לא נגזר | D17 | ✅ קיים, להשאיר | קיים (נגזר). |
| T6 | `FETCH_SLOW_S` | 600 | `server/rounds/chunksend.py:68` · `FETCH_SLOW_S` | הכרעה 2026-11-13: הכרעת המתאם | D14 | ✅ קיים, להשאיר | קיים (הכרעה 13/11). |
| T7 | `POLL_S` | 0.2 | `server/rounds/chunksend.py:58` · `POLL_S` | לא ידוע | — | ✅ קיים, להשאיר | קיים; מקור לא ידוע, אבל לא גרם לכשל. |
| T8 | `CC_SLOTS`: חתיכות בהרכבה בבת אחת | 4 | `agent/chunkcast.c:129` · `CC_SLOTS` | לא ידוע | D23 (חתיכות שננטשות?) | ✅ קיים, להשאיר | קיים; לבחון אחרי M11 אם הוא קשור לחתיכות שננטשות. |
| T9 | `CC_QCAP`: חתיכות מאומתות בתור לכל יעד | 8 | `agent/chunkcast.c:131` · `CC_QCAP` | לא ידוע | D23 | ✅ קיים, להשאיר | קיים; לבחון אחרי M11. |
| T10 | `CC_VQ` | 4 | `agent/chunkcast.c:130` · `CC_VQ` | לא ידוע | — | ✅ קיים, להשאיר | קיים; לא גרם לכשל. |
| T11 | `CC_RCVBUF` מבוקש | 16MB. ניתן בפועל 416KB (מחקר 1ד) | `agent/chunkcast.c:132` · `CC_RCVBUF` | לא ידוע: #2994; ניתן בפועל 416KB (מחקר 1ד) | — | ✅ עבודה חדשה | ביקשנו 16MB וקיבלנו 416KB; לדווח (S42) ולתקן rmem_max לפני בסיס X10. |
| T12 | `CC_WRITE_STALL_MS` | 120,000 | `agent/chunkcast.c:133` · `CC_WRITE_STALL_MS` | נגזר: `STALL_EVICT_MS` של fanout | D24 | ✅ קיים, להשאיר | קיים (נגזר מ-fanout). |
| T13 | `CC_PAYLOAD` | 1,440 | `agent/chunkcast.c:126` · `CC_PAYLOAD` | נגזר: MTU 1500 פחות כותרות | — | ✅ קיים, להשאיר | קיים (נגזר מ-MTU). |
| T14 | `CHUNK_FETCH_TELL_S` | 20 | `agent/lib/restore/chunkfetch.sh:24` · `CHUNK_FETCH_TELL_S` | לא ידוע | — | ✅ קיים, להשאיר | קיים. |
| T15 | ‏`speed-limit` של curl בהשלמה | ‏1 B/s במשך `WAIT_STREAM_STALL_S` | `agent/lib/restore/chunkfetch.sh:38` · `_chunk_fetch` («speed-limit») | נגזר: `WAIT_STREAM_STALL_S` | — | ✅ קיים, להשאיר | קיים (נגזר). |
| T16 | `CHUNK_ASK_POLL_S` | 2 | `agent/lib/restore/chunkrecv.sh:37` · `CHUNK_ASK_POLL_S` | לא ידוע | — | ✅ קיים, להשאיר | קיים. |
| T17 | `MANIFEST_TTL_S` | 15 | `server/rounds/chunkledger.py:239` · `MANIFEST_TTL_S` | לא ידוע | — | ✅ קיים, להשאיר | קיים. |
| T18 | `START_BITRATE` | 600m | `server/rounds/drive_rates.py:128` · `START_BITRATE` | מדידה 2026-09-06: 61.06MB/s לשני מקבלים, בלי כתיבה לדיסק | D2 | ✅ קיים, להשאיר | קיים (מדידה, בלי כתיבה לדיסק). |
| T19 | `TOP_BITRATE` | 900m | `server/rounds/drive_rates.py:132` · `TOP_BITRATE` | מדידה 2026-09-06: שולח המולטיקאסט הישן ב-95.7% | D2 | ✅ קיים, להשאיר | קיים (מדידה). |
| T20 | `MARGIN` | 0.9 | `server/rounds/drive_rates.py:108` · `MARGIN` | הערכה | D4 | ✅ קיים, להשאיר | קיים; חלק מ-D4, נבדק ב-M13. |
| T21 | `KEEP_UP` | 0.8 | `server/rounds/drive_rates.py:112` · `KEEP_UP` | נגזר: מ-95.7% (06/09) | D4 | ✅ קיים, להשאיר | קיים; זה הסף שבגללו הדיסק נראה 'מגביל' (D4), נבדק ב-M13. |
| T22 | `MIN_SAMPLE_BYTES` / `MIN_SAMPLE_S` | ‏1GB / 60 ש' | `server/rounds/drive_rates.py:114` · `MIN_SAMPLE_BYTES`<br>`server/rounds/drive_rates.py:115` · `MIN_SAMPLE_S` | הערכה | D4 | ✅ קיים, להשאיר | קיים (הערכה). |
| T23 | `MIN_HELD_SAMPLES` | 2 | `server/rounds/drive_rates.py:118` · `MIN_HELD_SAMPLES` | הערכה | D4 | ✅ קיים, להשאיר | קיים; 'שני גלים' הוא מה שמפעיל את 242m, נבדק ב-M13. |
| T24 | `FLOOR_RATIO` | 0.25 | `server/rounds/drive_rates.py:122` · `FLOOR_RATIO` | הערכה | D2 | ✅ קיים, להשאיר | קיים (הערכה). |
| T25 | `STEP_UP` | 100m | `server/rounds/drive_rates.py:137` · `STEP_UP` | הערכה | D3 | ✅ קיים, להשאיר | קיים (הערכה). |
| T27 | `DOWN_FACTOR` | 0.75 | `server/rounds/drive_rates.py:148` · `DOWN_FACTOR` | הערכה | D3, ‏D5 | ✅ קיים, להשאיר | קיים (הערכה). |
| T28 | `LOSS_DOWN_RATIO` | 0.001 | `server/rounds/drive_rates.py:153` · `LOSS_DOWN_RATIO` | הערכה: "ניחוש מנומק, לא מדידה" | D3 | ✅ קיים, להשאיר | קיים (ניחוש מנומק). |
| T30 | `GOODPUT_NOISE` / `REFINE_STEP` | ‏0.03 / ‏25m | `server/rounds/drive_rates.py:165` · `GOODPUT_NOISE`<br>`server/rounds/drive_rates.py:170` · `REFINE_STEP` | מדידה 2026-10-18: ברזל | D2 | ✅ קיים, להשאיר | קיים (מדידה 18/10). |
| T33 | `GOODPUT_WINDOW` / `WINDOW` | ‏10 / ‏5 | `server/rounds/drive_rates.py:190` · `GOODPUT_WINDOW`<br>`server/rounds/drive_rates.py:106` · `WINDOW` | הערכה: חישוב בטסט / הערכה | D2 | ✅ קיים, להשאיר | קיים. |
| T34 | `LINK_CEILING/START_PER_MBPS` | ‏90% / ‏60% מהקישור | `server/rounds/drive_rates.py:199` · `LINK_CEILING_PER_MBPS`<br>`server/rounds/drive_rates.py:200` · `LINK_START_PER_MBPS` | הכרעה 2026-10-15: הכרעת נדב | D2 | ✅ קיים, להשאיר | קיים (הכרעת נדב 15/10). |
| T35 | כלל המגירות `0.75^log2(n/n0)` | — | `server/rounds/drive_rates.py:624` · `_from_previous` | מדיניות: #1842 | D5 | ✅ קיים, להשאיר | קיים (מדיניות #1842). |
| T36 | `ASSUMED_MBPS` לכרטיס משכפל שלא נקרא | 1000 | `server/rounds/nic_speed.py:29` · `ASSUMED_MBPS` | הערכה | D6 | ✅ קיים, להשאיר | קיים (הערכה). |
| T37 | `LOST_SECONDS` / `lost_wait_seconds` | ‏180 (מותר רק להגדיל) | `server/rounds/room.py:83` · `LOST_SECONDS`<br>`server/agent/timing.py:34` · `LOST` | הערכה: "אינו מדידת שדה"; נדב 12/11: מותר רק להגדיל | D21 | ✅ קיים, להשאיר | קיים (הכרעת נדב). |
| T38 | `STALL_SECONDS` (כל המגירות עצרו) | 180 | `server/rounds/room.py:1880` · `STALL_SECONDS` | מדידה 2026-09-08: עצירה בריאה של SSD עד 120 ש' | D22 | ✅ קיים, להשאיר | קיים (מדידה 08/09). |
| T42 | `REWAKE_*` | ‏60 × 6, ‏180 | `server/rounds/room.py:90` · `REWAKE_SECONDS`<br>`server/rounds/room.py:91` · `REWAKE_MAX`<br>`server/rounds/room.py:92` · `REWAKE_BOOT_SECONDS` | נגזר: ספירת כיבוי שנעצרה 320 ש' | D35 | ✅ קיים, להשאיר | קיים. |
| T43 | `writecheck_min_mbps` | ‏50 (‏10–500). מעבדה: 74 | `server/disks/writefloor.py:26` · `DEFAULT`<br>`server/disks/writefloor.py:24` · `SETTING`<br>`agent/lib/restore/writecheck.sh:42` · `WRITECHECK_MIN_MBPS` | מדידה 2026-09-17: סבב 17/09: "50 הוא מתחת לחצי"; מעבדה: 74 | D26, ‏D14 | ✅ קיים, להשאיר | קיים (מדידה 17/09). |
| T44 | חצי החציון בבדיקת הכתיבה | ×2 | `agent/lib/restore/writecheck.sh:188` · `write_speed_verdict` | הערכה | D26 | ✅ קיים, להשאיר | קיים. |
| T45 | למידת הסף: ‏`REQUIRED_WAVES` ‏2, ‏`FLOOR_OF_MEDIAN` ‏0.5, ‏`MAX_STEP` ‏1.5, ‏`MODEL_OUTLIER` ‏3, ‏`SATA_MBPS_PER_GBPS` ‏100 |  | `server/learn/tuning.py:62` · `REQUIRED_WAVES`<br>`server/learn/tuning.py:64` · `FLOOR_OF_MEDIAN`<br>`server/learn/tuning.py:67` · `MAX_STEP`<br>`server/learn/tuning.py:70` · `MODEL_OUTLIER`<br>`server/learn/tuning.py:72` · `SATA_MBPS_PER_GBPS` | לא ידוע: המקורות לא נקראו | D27 | ✅ קיים, להשאיר | קיים (לא נקרא). |
| T46 | ‏netloss: ‏`WINDOW` ‏5, ‏`MIN_WAVES` ‏3, ‏`JUMP` ‏3 |  | `server/learn/netloss.py:42` · `WINDOW`<br>`server/learn/netloss.py:45` · `MIN_WAVES`<br>`server/learn/netloss.py:47` · `JUMP` | לא ידוע: לא נקרא | D39 | ✅ קיים, להשאיר | קיים (לא נקרא). |
| T47 | `SERVER_DISK_UTIL` ו-`SERVER_DISK_AWAIT_MS` (#3365) | 0.20 ו-10ms | `server/learn/server_io.py:46` · `SERVER_DISK_UTIL` | לא ידוע | — | ✅ קיים, להשאיר | קיים. |
| T48 | `ATTEMPT_LIMIT` (bootguard) | 3 | `server/agent/bootguard.py:40` · `ATTEMPT_LIMIT` | הערכה: הערכה מנומקת (bootguard.py) | D32 | ✅ קיים, להשאיר | קיים. |
| T49 | שאלת משכפל שכבה | ‏35 דק' (‏5–240) | `server/rounds/interrupt.py:52` · `DEFAULT`<br>`server/rounds/interrupt.py:53` · `MINIMUM`<br>`server/rounds/interrupt.py:54` · `MAXIMUM` | הכרעה: הכרעה | D33 | ✅ קיים, להשאיר | נכון לשיכפול של היום; בפס זה B42. |
| T50 | המתנה לשאלה למפעיל | ‏60 / ‏2,100 ש' | `server/rounds/prompt_hold.py:38` · `ASK_HOLD_ALIVE_SECONDS`<br>`server/rounds/prompt_hold.py:45` · `ASK_HOLD_MAX_SECONDS` | לא ידוע | D34 | ✅ קיים, להשאיר | קיים. |
| T51 | המתנות הסוכן: ‏`WAIT_STREAM_START` ‏600, ‏`_LATER` ‏900, ‏`STALL` ‏300, ‏`DRAWER` ‏300, ‏`WRITECHECK` ‏30, ‏`READBACK` ‏7,200 |  | `agent/lib/core/waits.sh:27` · `WAIT_STREAM_START_S`<br>`agent/lib/core/waits.sh:39` · `WAIT_STREAM_START_LATER_S`<br>`agent/lib/core/waits.sh:46` · `WAIT_STREAM_STALL_S`<br>`agent/lib/core/waits.sh:56` · `WAIT_DRAWER_S`<br>`agent/lib/core/waits.sh:79` · `WAIT_WRITECHECK_S`<br>`agent/lib/core/waits.sh:80` · `WAIT_READBACK_S` | הערכה: חלקן מנומקות (`READBACK`: 500GB ב-70MB/s) | D37 | ✅ קיים, להשאיר | קיים. |
| T52 | `DISK_STALL_S` | 120 | `agent/lib/core/timing.sh:42` · `DISK_STALL_S` | הכרעת נדב 18/11 (ההגדרה `drawer_disk_wait_seconds`; מחליף את `TIMING_STALL_MARGIN_S`) | D17, ‏D37 | ✅ קיים, להשאיר | קיים. |
| T53 | `END_POWEROFF_S` / `IDLE_POWEROFF_S` | ‏20 / ‏300 | `agent/lib/core/idle.sh:116` · `END_POWEROFF_S`<br>`agent/lib/core/idle.sh:14` · `IDLE_POWEROFF_S` | הכרעה: #1560 | D36 | ✅ קיים, להשאיר | נכון לשיכפול של היום; בפס זה B24. |

## X: ספים שהמוח צריך

| # | סף למוח | הצעה היום | מה חסר ומה ימדוד | איפה | המלצה | למה |
|---|---|---|---|---|---|---|
| X1 | זנב מינימלי שעוד שווה להצטרף אליו | התחלה 50 ש' (המחקר, 13/11). נלמד: פי 2 מהחציון של זמן ההצטרפות שנמדד, 10–300 ש', מ-3 מדידות | המדידה: מהדיווח הראשון של rejoin עד שהחוסר ירד לראשונה (קירוב ל-M6), נשמרת בשורת R4 FILLED ושורדת אתחול. M6 עצמה לא נמדדה. | `server/brain/joiner.py:51` · `X1_START_S`<br>`server/brain/joiner.py` · `x1`<br>`server/brain/joinwatch.py` · `_learned` | ⏳ אחר כך | מצטרף מאוחר (פס, M6). ראה הכרעה פנימית. |
| X2 | "זנב קטן": ‏`T_http ≤ 60 ש'` | כ-2,500 חתיכות | חסר: ‏`r_http` במצב לא-שקט, ובכמה משכפלים. מדידה: M2, ‏M3 | — | ⏳ אחר כך | תלוי ב-M2/M3. ראה הכרעה פנימית. |
| X3 | מודל `T_http = 4 + 0.025·טווחים + 0.02·חתיכות` | — | חסר: הורדה מלאה (M2), ונתוני S59 בכל השלמה. מדידה: M2 | — | ⏳ אחר כך | תלוי ב-M2. ראה הכרעה פנימית. |
| X4 | `r_mc` | ‏36 / ‏21 | חסר: למה 36. ההשערה בקובץ 2, הערה 1: `--write-kbps`. מדידה: M13 (חדש) | — | ✅ עבודה חדשה | הקצב 242m: להבין למה 36 חתיכות בשנייה (M13). |
| X5 | `E`: תקציב יציאה של השרת | 70% / 50% | חסר: הכרעת נדב + מתג המכללה. מדידה: M10, ‏V9 | — | ✅ עבודה חדשה | תקציב HTTP ל-6 משכפלים צריך מספר: נקודת פתיחה מנדב, כיול ב-M14. |
| X6 | `HTTP_MAX_CONC` | 1–12 | חסר: איפה השרת נתקע. מדידה: M3 + ‏M14 (חדש): 6 משכפלים | — | ✅ עבודה חדשה | ב-6 משכפלים צריך תקרה; היום 'אין בסיס'. נקבע ב-M14. |
| X7 | `F ≥ 1.5`, ‏`T_A ≤ 0.8·T_B`, ‏`T_B ≤ 1.2·T_A` | — | חסר: ‏`r_http`, ‏`r_mc`, ואם (ב) הוא מעבר מלא (B7) או ממוקד (D8). מדידה: M2, ‏M3 | — | ⏳ אחר כך | סף של המוח. ראה הכרעה פנימית. |
| X8 | היסטרזיס: 5–10 ש', ‏15%, ‏30 ש' | — | חסר: סימולטור + גלים אמיתיים. מדידה: סימולטור | — | ⏳ אחר כך | סף של המוח; מהסימולטור. |
| X9 | ‏Lone handoff | 20 ש' | חסר: —. מדידה: סימולטור | — | ⏳ אחר כך | תלוי ב-carousel. |
| X10 | בסיס "זרם נפגע": חתיכות נטושות במעבר נקי | ‏4–716 | חסר: תיקון rmem_max, ואחריו בסיס. מדידה: M11 (תלוי ב-S41) | — | ⏳ אחר כך | תלוי ב-S41 ובתיקון rmem_max (T11), ואז M11. |
| X11 | ‏rcvbuf > 0 בגל חתיכות | — | חסר: —. מדידה: M11 | `server/rounds/drive_rates.py:350` · `_rcvbuf` | ✅ עבודה חדשה | סף פשוט (גדול מ-0), כמו שהיה בשולח הישן; צריך רק את S44 (G6). |
| X12 | ‏`psi_full` של השרת | 0.2 | חסר: גל חתיכות + HTTP. ‏`server_io` לא נסגר (V2). מדידה: M4 | — | ⏳ אחר כך | צריך קודם את G4, ואז M4. |
| X13 | זמן ריקון תור הכתיבה ל-HTTP במקביל | ‏250ms / ‏1 ש' | חסר: חוש S46. מדידה: M1 | — | ⏳ אחר כך | תלוי בחוש S46 ובמדידה M1. ראה הכרעה פנימית. |
| X14 | תקרה להשלמה פרטית | ‏ASR: ‏32MB | חסר: ‏X2 נותן נקודת פתיחה. מדידה: M2 | — | ⏳ אחר כך | תלוי ב-M2. ראה הכרעה פנימית. |
| X15 | תקציב השלמה לשכנה אחרי שליפה | ‏100MiB/s לכל משכפל; 64 חתיכות | `server/brain/fill.py` · `CEILING_BPS` (M1/M2, ברזל 14/11). חסר: M8 | — | ✅ קיים, להשאיר | K5 (#3181). |
| X16 | ‏HDD במנוע החתיכות | ‏69MB/s בבדיקת כתיבה | חסר: גל והשלמה ל-HDD. מדידה: M5 | — | ⏳ אחר כך | תלוי ב-M5, ובשאלה אם במכללה יש HDD (שאלה 5). |
| X17 | כמה זמן מחכים למשכפל שמשלים מגירה לפני מחיצה הבאה | התחלה כ-49 חתיכות לשנייה (2,071 ב-42 ש', ההערה בקטלוג, 13/11). נלמד: החציון של קצב ההשלמה שנמדד. הגל לא מחכה (#3295 ב') | הנגזרת: כמה זמן ההשלמה תיקח, ומזה B17 מול B18. במנוע החתיכות אין המתנה למשכפל שמשלים (הכרעת נדב 15/11, #3295 ב'); later_start_timeout (780 ש') הוא ההמתנה ל-listening בתחילת מחיצה (D16). M15 לא נמדדה במלואה. | `server/brain/joiner.py:55` · `X17_START_CPS`<br>`server/brain/joiner.py` · `x17`<br>`server/rounds/chunkctx.py` · `later_start_timeout` | ⏳ אחר כך | זה המספר שחסר ל-#3112; M15 נמדדת בכל מקרה. ראה הכרעה פנימית. |
| X18 | ‏`MAX_MISSING_PERMILLE` (הוסר ב-#3170) למצטרף מאוחר | אין סף למצטרף מאוחר: פטור מ-D11 (צל) | נגזר מהכרעת נדב 15/11 (#3295 ב'): דיסק איטי משלים אחר כך ולא מאט את הגל. MAX_MISSING_PERMILLE הוסר ב-#3170. | `server/brain/joiner.py:271` · `late_slow`<br>`server/rounds/repair.py` · `slow_reason` | ⏳ אחר כך | מצטרף מאוחר (פס). |
| X19 | דיוק בדיקת הכתיבה | גרגור 10ms | חסר: בדיקה ארוכה יותר, או שעון מדויק. מדידה: M16 (חדש) | — | ✅ עבודה חדשה | D7 נשען על הערך הזה, ו-M16 זולה; עוזר גם בלי המוח. |
| X20 | מספר משכפלים בכל הספים | 1–2 נמדדו | חסר: משכפלי VM מול LabRoot לצד השרת. מדידה: M14 | — | ✅ עבודה חדשה | כלל: כל סף נבדק ב-6 משכפלים ומעלה (M14). |
| X21 | ‏Loop Suspend: כמה מעברים ריקים עד עצירה | — | חסר: —. מדידה: סימולטור | — | ⏳ אחר כך | תלוי ב-carousel. |

## M: מדידות חדשות (בנוסף ל-M1–M12 במחקר)

| # | מדידה | מה היא מכריעה | המלצה | למה |
|---|---|---|---|---|
| M13 | גל חתיכות אחד ב-700m עם `--write-kbps` גבוה פי 2 (או `writecheck` שמחזיר יותר), מגירה אחת, אותו אימג'. רושמים חתיכות בשנייה | אם עולה מ-36: ההשערה בקובץ 2 (הערה 1) נכונה. אז ‏`r_mc` נקבע בבדיקת הכתיבה, ו-242m הוא לולאה. בדיקת קוד מקדימה: לחשב `write_kbps` מתוך `chunksend` ביומן של ses_d30e6d7b | ✅ עבודה חדשה | מכריעה את 242m, והיא זולה: גל אחד במעבדה, בלי קוד. |
| M14 | ‏6 משכפלי VM מורידים HTTP יחד מ-LabRoot, עם ובלי זרם חי | ‏`HTTP_MAX_CONC` ותקציב גלובלי ב-6+ משכפלים | ✅ עבודה חדשה | בלעדיה אין מספרים לתקציב HTTP ב-6 משכפלים. |
| M15 | מגירה שחזרה משלימה מחיצה מלאה, בזמן שמחיצה אחרת אמורה להתחיל | ‏X17, ומה עדיף מבין B16 ל-B18 | ✅ עבודה חדשה | מכריעה איך לתקן את #3112 (B16 מול B17/B18). |
| M16 | בדיקת כתיבה של 64MB במקום 3×4MiB, על אותם SSD | האם 179/209 הם המהירות האמיתית או גרגור | ✅ עבודה חדשה | זולה, ומכריעה אם 179/209 הם מהירות אמיתית (D7). |

## KT: ערכי ההתחלה של טיוטת התכנון

**שום טיימר כאן לא מפעיל פעולה הרסנית לבד.** טיימר רק מעביר מצב (ALIVE → SUSPECT → LOST). פעולה
הרסנית עוברת דרך הווטו (K2). ‏LOST נשאר 180 ש' (T37) ו-STALLED נשאר 180 ש' (T38), כמו היום.

| # | ערך | מספר | מקור | כללים |
|---|---|---|---|---|
| KT1 | tick של המוח | 10 ש' + כל אירוע | הערכה: research-3064 4ב | — |
| KT2 | SUSPECT: בלי שום ראיה | 30 ש' | הערכה: לא מפעיל פעולה, רק שורת יומן | — |
| KT3 | חוזה השלמה (lease) | `max(300, 3 × נשאר ÷ r_http)` ש', עד KT6 | הערכה: המכפיל 3 והרצפה 300 | K5 |
| KT4 | `r_http` התחלתי לחוזה | 42 חתיכות/ש' | מדידה 2026-11-13: הנמוך שנמדד על ברזל, ses_108caf1d | K5 |
| KT5 | חסד אחרי הפעלה מחדש של השרת | 300 ש' | הערכה: לא אומת | — |
| KT6 | תקרה מוחלטת להחזקה | 30 דק' | הערכה: בערך פי 5 מהשלמת מחיצה מלאה (~6 דק', חישוב). M15 תכריע | K2 |
| KT7 | מקביליות HTTP | 2 בכל השרת, 1 לכל משכפל | הערכה: מעבר ל-2 רק אחרי M14 | K7 |
| KT8 | dwell ורווח מינימלי למעבר | החלטה מוקפאת עד אירוע או סוף מעבר, ולפחות 30 ש'; 15% | הערכה: B13, B14 | — |
| KT9 | קישור איטי של משכפל: אזהרה ו-ETA לפני ההתחלה (`linkwarn.SLOW_MBPS`, #3277) | 100Mb/s ומטה | מדידה 2026-11-11: מכללה, #2841 | K3 |
| KT10 | קצב ה-ETA לכל Mb/s של קישור המשכפל (`linkwarn.BYTES_PER_S_PER_MBPS`) | 7.27MB/s ב-100Mb/s | מדידה 2026-11-11: מכללה, #2841, מדידה אחת -- נרשם מול בפועל (R3) | K3 |

</div>
