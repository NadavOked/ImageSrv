<div dir="rtl">

# שכבה 4: יומן

מה נרשם היום עם הסיבה, מה קורה בלי רישום, ומה אפשר לרשום ולהציג. **נדב: כל החלטה של המוח מופיעה
בקונסולה בשורה בעברית** (R1).

**איפה נשמר:** ‏journal = טבלת היומן בשרת (`journal()` ב-`server/db.py`). ‏DB = טבלה ייעודית. ‏agent log =
היומן של הסוכן, שמגיע לשרת רק כזנב (S37), ועלול להיקטע (L9). קונסולה = מסך.

**איך קוראים את עמודת "איפה":** ‏`קובץ:שורה` · ‏`סימבול`. **הסימבול הוא המצביע, והשורה היא רמז בלבד**
(נכונה ל-`cf58981a`). ‏«טקסט» בסוגריים הוא עוגן: מחרוזת שמופיעה באותו קובץ, ליד הכלל, כשהסימבול
הוא פונקציה גדולה (למשל `_send` ב-`chunksend.py`). בפריט שעוד לא קיים, המיקום הוא המקום שבו הנתון
הגולמי כבר יושב, או שבו המלאי מציע להוסיף אותו.

## L: מה נרשם היום

| # | מה נרשם | איפה נשמר | עם הסיבה? | קוד | המלצה | למה |
|---|---|---|---|---|---|---|
| L1 | החלטת קצב הגל, עם `basis` (start/up/down/held/learned/ceiling…), ‏`by`, ‏`previous` | DB `wave_rates.decision` + journal `wave_rate` + קונסולה | כן | `server/rounds/drive_rates.py:845` · `decide` («"wave_rate"»)<br>`server/rounds/drive_rates.py:892` · `_basis_note` | ✅ קיים, להשאיר | קיים; ה-basis המאשים דיסק יתוקן ב-G8. **הערה:** חסר: בגל חתיכות `basis=learned` מאשים דיסק (קובץ 2, הערה 1) |
| L2 | תוכנית ההשלמה: ‏`round`, כמה ב-multicast ולכמה מחשבים, כמה לכל מוריד | journal `chunk_repair` | חלקי: המספרים כן, למה (THRESHOLD) לא | `server/rounds/chunksend.py:763` · `_send` («"chunk_repair"») | ⏳ אחר כך | מתעד את מסלול D8. ראה הכרעה פנימית. **הערה:** חסר: טקסט חופשי. אין טבלה, ואין את רשימת הטווחים |
| L3 | דיסק שנכשל במנוע החתיכות, עם הסיבה בעברית | journal `chunk_disk_failed` | כן | `server/rounds/chunksend.py:550` · `_note_slow_fetches` («"chunk_disk_failed"») | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה. |
| L4 | סיכום הגל: ‏`repair_rounds`, ‏`multicast_chunks`, ‏`fetched_chunks` | journal `send_done` | — | `server/rounds/chunksend.py:795` · `_send` («"send_done"») | ✅ עבודה חדשה | קיים כטקסט; קטן להעביר לטבלה (עם G9). **הערה:** חסר: טקסט (`{w.stats}`). לא ב-`wave_rates.stream` |
| L5 | תחילת גל: אימג', מחיצות, מנוע | journal `send_start` | — | `server/rounds/chunksend.py:694` · `_send` («"send_start"») | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה. |
| L6 | כשל הגל "אף מחשב לא התייצב… תוך N שניות" | journal `send_failed` + הודעה לסוכן | כן, אבל המספר שגוי | `server/rounds/chunksend.py:709` · `_send` («אף מחשב לא התייצב לשידור»)<br>`server/rounds/sender.py:86` · `later_start_timeout` | ✅ עבודה חדשה | ההודעה אמרה 180 כשההמתנה הייתה 780, והטעתה ב-#3112 (G3). **הערה:** ההודעה מציגה `self.start_timeout` (180) גם במחיצה 2+, שבה ההמתנה היא 780 (G3) |
| L7 | חבר גל אבד, עם מה שהיה פתוח אצלו | journal `client_lost` | כן | `server/rounds/room.py:822` · `_mark_lost` («"client_lost"») | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה. |
| L8 | דיסק שנכשל, עם סיבה וסיווג ata | DB `disk_failures` | כן | `server/disks/disk_failures.py:103` · `record` | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה. |
| L9 | זנב יומן הסוכן (עד 400 שורות או 64KB) | DB `agent_logs` | — | `agent/lib/core/datareport.sh:11` · `agent_log_send`<br>`server/agent/logs.py:150` · `receive` | ✅ עבודה חדשה | קיים, אבל נקטע בגל ארוך (G19). **הערה:** חסר: נקטע בגל ארוך. כל שורות chunkcast הן טקסט בלבד |
| L10 | מוני המקלט של chunkcast לכל מעבר | agent log בלבד | — | `agent/chunkcast.c:1213` · `cc_recv` | ✅ עבודה חדשה | קיים ביומן הסוכן; צריך כשדה (G7). **הערה:** חסר: לא מגיע כשדה (S41) |
| L11 | שורת ההורדה: טווחים, ‏curl rc, ‏apply rc | agent log בלבד | — | `agent/lib/restore/chunkfetch.sh:52` · `_chunk_fetch` | ⏳ אחר כך | המדידה עצמה עוזרת, והיא חלק ממסלול ההשלמה. ראה הכרעה פנימית. **הערה:** חסר: בלי בתים ובלי זמן (S59) |
| L12 | "מגירה N חזרה (serial…)" | agent log בלבד | — | `agent/lib/restore/drawerback.sh:159` · `_db_join` («חזרה (serial») | ✅ עבודה חדשה | השרת והקונסולה לא ידעו שמגירה חזרה (#3112); ייפתר ב-G1. **הערה:** נמצא ב-`drawerback.sh` (`_db_join`), ביומן הסוכן בלבד. המלאי כתב "לא איתרתי". חסר: השרת והקונסולה לא יודעים (S45) |
| L13 | צפצופים | agent log, רק כשהצפצוף נכשל | — | `agent/lib/core/roundbeep.sh:42` · `_rb_unheard` | ✅ עבודה חדשה | צפצופים בלי יומן (#3106); קטן (G5). **הערה:** חסר: צפצוף שהושמע לא נרשם (#3106) |
| L14 | צילום I/O של דיסק השרת | DB `server_io` | — | `server/learn/server_io.py:92` · `begin`<br>`server/learn/server_io.py:137` · `end`<br>`server/app.py:682` · `on_chunk_event` | ✅ עבודה חדשה | server_io לא נסגר בגל חתיכות; קטן (G4). **הערה:** חסר: ‏`ended_at` ריק בכל גל חתיכות |
| L15 | קצב לכל דיסק, ‏drops ו-delivered לכל משכפל | DB `drive_rates`, ‏`wave_rates` | — | `server/rounds/drive_rates.py:998` · `record_wave` | ✅ עבודה חדשה | קיים; drops ריק בגל חתיכות, ייפתר ב-G6. **הערה:** חסר: בגל חתיכות `drops` הוא None (S44) |
| L16 | שינויי סף מלמידה | journal | כן | `server/learn/tuning.py:285` · `_finish` («"learning_done"») | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה. |
| L17 | קפיצת אבדן ברשת | journal | כן | `server/learn/netloss.py:126` · `evaluate_chunk` («"net_loss_jump"») | ✅ קיים, להשאיר | קיים ומשמש היום, בגל חתיכות (#3164), לפי `abandoned` לכל GB. מאז #3173 זה המנוע היחיד. לא נמדד על ברזל; להשאיר בלי עבודה. **הערה:** בגל חתיכות השורה נושאת גם `local=` (מה שהמקלטים עצמם זרקו). |
| L18 | שלבי שחזור לכל יעד, עם חותמות זמן | DB `restore_stages` | — | `server/agent/restore_stages.py:69` · `record` | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה. |
| L19 | שאלות למפעיל ותשובות (משכפל שכבה, המתנה) | journal | כן | `server/rounds/interrupt.py:87` · `UNANSWERED_REASON` («"round_interrupt_answer"»)<br>`server/rounds/prompt_hold.py:52` · `PROMPT_EXPIRED_WHY` | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה. |
| L20 | אירוע SMART בשער | DB `disk_events` + journal `smart_red` | כן | `server/disks/disk_events.py:130` · `ingest` («"smart_red"») | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה. |
| L21 | ‏`fw_warning`, ‏`gui_failed`, ‏`wol_arm_failed` | journal | כן | `server/agent/api.py:510` · `_agent_hello_body` («"fw_warning"»)<br>`server/agent/api.py:557` · `_agent_hello_body` («"gui_failed"»)<br>`server/agent/api.py:564` · `_agent_hello_body` («"wol_arm_failed"»)<br>`server/agent/firmware_image.py:157` · `record` («"fw_changed"») | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה. |
| L22 | תצוגת הסבב במסך: ‏ETA, ‏stalled, קצב לכל מגירה | קונסולה | — | `server/rounds/room.py:170` · `drawer_list` | ✅ עבודה חדשה | חסרים 'משלים' ו'ממתין למשכפל אחר', וזה מה שבלבל ב-#3112 (R17). **הערה:** חסר: אין "משלים", אין "ממתין למשכפל אחר" בשם |

## U: החלטות שקורות היום בלי רישום, או בלי הסיבה

המיקום הוא ההחלטה עצמה, שקורית בלי שורת יומן.

| # | החלטה | קוד | מה לא נרשם | המלצה | למה |
|---|---|---|---|---|---|
| U1 | מגירה מקבלת `skipped` (פיגרה אחרי הזרם) | `agent/chunkcast.c:819` · `cc_verifier` («t->skipped++») | מופיע רק כמונה ביומן הסוכן. השרת רואה רק "חסר" | ✅ עבודה חדשה | ייפתר ב-G7 (S41, R6). |
| U2 | `--write-kbps` שנבחר, ולפי איזה דיסק | `server/rounds/chunksend.py:571` · `_write_kbps` | לא נרשם בכלל. זה כנראה מה שקובע את קצב הזרם (קובץ 2, הערה 1) | ✅ עבודה חדשה | כנראה זה מה שקובע את קצב הזרם; ייפתר ב-G9 (R7). |
| U3 | מי לא התייצב למחיצה, ומתי ויתרו עליו | `server/rounds/chunksend.py:474` · `_wait_marks` | רק הודעת הכשל של כל הגל (L6) | ✅ עבודה חדשה | ייפתר ב-G3 (R10). |
| U4 | למה מחשב קיבל `receive` ולא `fetch` (D10) | `server/rounds/repair.py:139` · `plan` | לא נרשם | ⏳ אחר כך | מתעד את D10. ראה הכרעה פנימית. |
| U5 | מגירה שחזרה: ההחלטה להשלים, והטווחים | `agent/lib/restore/drawerfill.sh:147` · `_db_part` | agent log בלבד (L12) | ✅ עבודה חדשה | ייפתר ב-G1: המשכפל מדווח לשרת שהוא משלים (S45). |
| U6 | ‏serial אחר שהוכנס לחריץ "לא מצטרף" | `agent/lib/restore/drawerback.sh:55` · `_db_strangers` | "said in the log, once". ‏agent log בלבד | ✅ עבודה חדשה | קטן: שורת journal אחת, והמפעיל יודע למה דיסק לא הצטרף. |
| U7 | הגל הבא `unmeasured` כי אין rcvbuf בגל חתיכות | `server/rounds/drive_rates.py:384` · `_next_from` | ה-basis נרשם (L1), אבל לא הסיבה שאין מדידה | ✅ עבודה חדשה | ייפתר ב-G6 (S44). |
| U8 | קצב HTTP לכל משכפל | `server/rounds/chunksend.py:520` · `_note_slow_fetches` | בזיכרון בלבד. נעלם בסוף הגל | ✅ עבודה חדשה | ייפתר ב-G10 (V7). |
| U9 | ‏`SO_RCVBUF` שניתן | `agent/chunkcast.c:1130` · `cc_recv` («SO_RCVBUF») | agent log בלבד | ✅ עבודה חדשה | ייפתר ב-G7 (S42). |
| U10 | הצפצוף שהושמע | `agent/lib/core/roundbeep.sh:94` · `_rb_play` | לא נרשם (#3106) | ✅ עבודה חדשה | ייפתר ב-G5 (#3106). |

## R: מה אפשר לרשום ולהציג

| # | רשומה | מצב | קוד | המלצה | למה |
|---|---|---|---|---|---|
| R1 | שורה לכל החלטה של המוח, בקונסולה ובעברית: "דיסק 7 → שידור חדש: 4 ממתינים" | לא קיים | — | ⏳ אחר כך | חובה לפי נדב, ברגע שיש החלטות מוח להציג. |
| R2 | טבלת החלטות: זמן, אירוע מעורר, צילום הקלטים (`J`, ‏`M_m`, ‏`U`, ‏`r_mc`, ‏`r_http`, ‏`E`, ‏psi), הבחירה, החלופות עם `T_A`/`T_B`/`F`, ה-epoch | לא קיים | — | ⏳ אחר כך | תשתית של המוח. |
| R3 | תחזית מול מה שקרה: ‏ETA שהמוח חזה מול זמן הסיום בפועל | חלקי | `server/brain/linkwarn.py` · `finished` | ✅ עבודה חדשה | #3277: ה-ETA של משכפל על קישור איטי, כתוצאה של החלטת `K3/LINK_SLOW`. |
| R4 | ציר זמן לכל דיסק: הכנסה → קבלה → הצטרפות (CURRENT/ALL) → השלמה → readback → done | קיים | `server/brain/joinwatch.py:443` · `timeline`<br>`server/console/api.py` · `brain_disk_timeline`<br>`server/brain/joinwatch.py` · `_stage`<br>`server/agent/restore_stages.py` · `record` | ⏳ אחר כך | פס ייצור; חלקית קיים (L18). |
| R5 | "משלים מגירה N: 1,393 חתיכות, 40%" במסך ובשרת | קיים | `native-gui/src/screens_cloner.c` · `entry_bay` («מצטרף ומשלים»)<br>`agent/lib/gui/clonergui.sh:118` · `drawer_fill_record`<br>`server/static/console-prodline.js:36` · `plRejoinNote`<br>`server/rounds/room.py:399` · `_rejoin`<br>`server/brain/joiner.py:279` · `completing_text`<br>`server/brain/joinwatch.py` · `_r5` | ⏳ אחר כך | מסביר למה שכנה ממתינה; S45 ו-R17 מכסים את רובו. ראה הכרעה פנימית. |
| R6 | מוני chunkcast לכל משכפל לכל מעבר, כשדות | לא קיים | — | ✅ עבודה חדשה | בריאות הזרם לאורך זמן (G7). |
| R7 | ‏`write_kbps` ו-`kbps` של כל מעבר, ומי מהם האט | לא קיים | — | ✅ עבודה חדשה | קטן, ומכריע את 242m בכל גל בלי מדידה מיוחדת (G9). |
| R8 | ‏`server_io` גם לגל חתיכות + ‏tx של הכרטיס + מעבד chunkcast | מדווח | `server/learn/server_senses.py` · `Senses` («"server": summary»)<br>`server/learn/server_io.py:92` · `begin`<br>`server/brain/memory.py` · `_server_io` | ✅ עבודה חדשה | server_io בגל חתיכות (G4, G18). #3125: `result_json.server` וחלונות `kind = "window"`. |
| R9 | הורדות HTTP: מי, כמה בתים, כמה טווחים, משך, קצב | לא קיים | — | ⏳ אחר כך | המדידה עצמה עוזרת (`r_http`). ראה הכרעה פנימית. |
| R10 | הודעת כשל "לא התייצב" עם המספר הנכון ועם שם המשכפל | לא קיים | — | ✅ עבודה חדשה | יומן שלא מטעה (G3). |
| R11 | צפצופים ביומן הסוכן | לא קיים | — | ✅ עבודה חדשה | צפצופים ביומן (#3106, G5). |
| R12 | ראיה שלא נעשה שידור ממוקד (מונה `multicast_chunks` = 0 לכל גל, בטבלה) | חלקי | `server/rounds/chunksend.py:795` · `_send` («"send_done"») | ⏳ אחר כך | הראיה שהמסלול הממוקד כבוי, אם יכובה. ראה הכרעה פנימית. |
| R13 | זנב יומן הסוכן ארוך יותר, או ללא קיטוע בגל ארוך | חלקי | `server/agent/logs.py:48` · `MAX_TEXT` | ✅ עבודה חדשה | לא מאבדים את תחילת הגל (G19). |
| R14 | קובץ "הקלטה" של גל (כל האירועים לפי סדר) לסימולטור | לא קיים | — | ⏳ אחר כך | שכבת הסימולטור. |
| R15 | מגירה שנכשלת שוב ושוב (לפי חריץ) | חלקי | `server/learn/ports.py:79` · `record_wave`<br>`server/learn/ports.py:121` · `verdict` | ✅ עבודה חדשה | חלקית קיים (port_history); קטן להשלים, עם B33. |
| R16 | הצגת `sata_mode` / הדרייבר כאזהרה כתומה | לא קיים | — | ✅ עבודה חדשה | הוכרע ב-13/11; קטן (G12). |
| R17 | "ממתין לזרם מהשרת" מול "ממתין למשכפל אחר" מול "השרת מחכה לי" | חלקי | `server/rounds/chunksend.py:448` · `_answer` («"others"») | ✅ עבודה חדשה | קטן (השדות כבר מגיעים לסוכן), ומסביר במסך למה מגירה ממתינה. |

**דוגמה לשורת החלטה של המוח** (טיוטת התכנון, סעיף 3ב):

```
2026-11-13T19:42:10Z decide=4812 snap=9f3c1a round=room_714f980d pc=cloner2
  action=HOLD_PARTITION target=p4 class=LIVENESS rule=K6
  reason=pc_repairing_drawer evidence="served 412MB/60s; ledger +1,393 ch"
  lease=07:40/14:00 vetoed=FAIL_WAVE(later_start_timeout=780s) ceiling=30:00
```

ובקונסולה: "משכפל 2 משלים את מגירה 3 (‏1,393 חתיכות, 40%). מחיצה 4 ממתינה לו, לכל היותר עד 14:00 דק'."

</div>
