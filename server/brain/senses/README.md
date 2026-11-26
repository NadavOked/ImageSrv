<div dir="rtl">

# שכבה 1: חושים

כל מידע שהמשכפל, השרת והרשת יודעים, או יכולים לדעת. **בלי חוש, אין החלטה.** חוש שלא הגיע הוא
"לא ידוע", ולעולם לא ערך ברירת מחדל (עיקרון 5, וטיוטת התכנון סעיף 5א).

**המצב:**
- **מדווח ומשמש:** הסוכן שולח, וקוד בשרת מחליט לפיו.
- **מדווח, לא משמש:** נשלח ונשמר או מוצג, אבל אף החלטה לא נשענת עליו.
- **לא קיים:** לא מדווח, ואפשר להשיג. המיקום הוא איפה הנתון הגולמי כבר יושב.
- **חלקי / לא נבדק:** כמו בעמודת "למה".

**איך קוראים את עמודת "איפה":** ‏`קובץ:שורה` · ‏`סימבול`. **הסימבול הוא המצביע, והשורה היא רמז בלבד**
(נכונה ל-`cf58981a`). ‏«טקסט» בסוגריים הוא עוגן: מחרוזת שמופיעה באותו קובץ, ליד הכלל, כשהסימבול
הוא פונקציה גדולה (למשל `_send` ב-`chunksend.py`). בפריט שעוד לא קיים, המיקום הוא המקום שבו הנתון
הגולמי כבר יושב, או שבו המלאי מציע להוסיף אותו.

**הכי דחוף בשכבה הזו:** ‏**S45** ("המשכפל משלים מגירה", הכשל של PR #3112), ו-**S41/S44** (בריאות
הזרם במנוע החתיכות, שבלעדיה כל גל חתיכות הבא נשאר `unmeasured`).

## המשכפל (S)

| # | חוש | מצב | איפה (נאסף · נצרך) | כללים | המלצה | למה |
|---|---|---|---|---|---|---|
| S1 | ‏MAC וכל ה-MAC-ים | מדווח ומשמש | `agent/lib/hw/sysinfo.sh:66` · `build_hello`<br>`server/agent/api.py:368` · `_agent_hello_body`<br>`server/agent/hello.py:347` · `build_answer` | — | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (זהות והצטרפות לסבב). |
| S2 | `joining` (המשכפל מבקש להצטרף לגל) | מדווח ומשמש | `agent/lib/hw/sysinfo.sh:81` · `build_hello`<br>`server/agent/hello.py:498` · `build_answer` | K6 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (הצטרפות לגל וזיהוי חזרה באמצע זרם). |
| S3 | `engines` (המשכפל מכיר את מנוע החתיכות) | מדווח ומשמש | `agent/lib/hw/sysinfo.sh:107` · `build_hello`<br>`server/rounds/chunksend.py:300` · `note_agent` | — | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (גל חתיכות לא יוצא למשכפל ישן). |
| S4 | `boot_id` | מדווח ומשמש | `agent/lib/hw/sysinfo.sh:111` · `build_hello`<br>`server/rounds/ledger.py:43` · `note_boot`<br>`server/rounds/interrupt.py:151` · `other_boot` | K1 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (מזהה אתחול באמצע גל). |
| S5 | מהירות כרטיס הרשת של המשכפל | מדווח ומשמש | `agent/lib/hw/probe.sh:145` · `probe_nic`<br>`server/rounds/nic_speed.py:51` · `reported`<br>`server/rounds/drive_rates.py:770` · `choose` | K8 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (המשכפל האיטי קובע תקרה). |
| S6 | זהות כל דיסק: serial, גודל, פורט, wwid, ‏pci | מדווח ומשמש | `agent/lib/hw/diskinfo.sh:105` · `build_disk_entry`<br>`server/rounds/room.py:550` · `_validate_target_slots`<br>`server/rounds/ledger.py:190` · `_same` | K3 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה; כל השאר נשען על זהות הדיסק. |
| S7 | פסק SMART ‏(ok/warn/fail) | מדווח ומשמש | `agent/lib/hw/diskinfo.sh:134` · `build_disk_entry`<br>`server/disks/disk_rule.py:43` · `judge`<br>`server/rounds/room.py:568` · `_validate_target_slots` | K3 K9 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (לא כותבים לדיסק גוסס). |
| S8 | כל תכונות SMART | מדווח ומשמש | `agent/lib/smart/smartattrs.sh:46` · `smart_attrs_json`<br>`server/disks/smart_history.py:160` · `learned_for`<br>`agent/lib/smart/smartgate.sh:86` · `smart_preflight` | K3 K9 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (דיסק שמידרדר נצבע כתום). |
| S9 | מהירות קישור SATA מול המקסימום | מדווח ומשמש | `agent/lib/hw/disklink.sh:16` · `_dl_gbps`<br>`server/rounds/room_precheck.py:46` · `link_state` («unknown») | K3 | ✅ קיים, להשאיר | קיים ומדווח; #3591 (נדב 17/11): המהירות (1.5/3.0/6.0) אינה סיבה לצבע ולא לחסימה -- היא מידע, וקלט למוח (תקרת קצב ליעד, סיבה לאיטיות רק כשהיא הצוואר). כבל או מגירה גרועים נתפסים ב-sata_issue וב-CRC שעולה בכתיבה. |
| S10 | אירועי SATA מ-dmesg (‏link_reset, ‏drawer_gone) | מדווח ומשמש | `agent/lib/smart/hwissues.sh:52` · `hw_refresh`<br>`server/disks/disk_rule.py:77` · `facts_of` («sata_issue») | K3 K9 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (מגירה מקרטעת לא נבחרת). |
| S11 | סימן "כתיבה לא שלמה" על הדיסק | מדווח ומשמש | `agent/lib/restore/writemark.sh:60` · `writemark_json`<br>`server/disks/disk_rule.py:81` · `facts_of` («write_incomplete») | K3 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (דיסק חצי-כתוב לא ישמש מקור). |
| S12 | בדיקת הכתיבה, ‏MB/s לכל דיסק (ערוץ chunk-pass) | מדווח ומשמש | `agent/lib/restore/chunkrecv.sh:57` · `_chunk_body` («write_mbps»)<br>`server/rounds/chunksend.py:367` · `report`<br>`server/rounds/chunksend.py:571` · `_write_kbps` | K8 | ✅ קיים, להשאיר | קיים, אבל נראה שהוא זה שקובע את קצב הזרם ואת 242m; לבדוק ב-M13 לפני שנוגעים (G8). |
| S13 | פסק בדיקת הכתיבה (`write_preflight`) | מדווח ומשמש | `agent/lib/core/progress.sh:176` · `build_progress` («write_preflight»)<br>`server/rounds/room.py:317` · `_write_slow`<br>`server/learn/tuning.py:201` · `_floor_change` | K3 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (דיסק איטי לא מאט גל). |
| S14 | בתים שנכתבו לכל יעד | מדווח ומשמש | `agent/lib/core/progress.sh:92` · `target_bytes`<br>`server/rounds/drive_rates.py:998` · `record_wave` | K8 | ✅ קיים, להשאיר | קיים; בגל חתיכות המספר מטעה (S70), והתיקון הוא G8 ולא החוש עצמו. |
| S15 | מצב, שגיאה ו-ata_log של יעד שנכשל | מדווח ומשמש | `agent/lib/restore/failmark.sh:172` · `failure_json`<br>`server/disks/disk_failures.py:103` · `record` | K4 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (דיסק שנכשל נצבע בסבב הבא). |
| S16 | עצם הגעת דיווח progress (כל ~2 ש') | מדווח ומשמש | `agent/lib/core/progress.sh:237` · `progress_loop`<br>`server/rounds/room.py:768` · `_mark_lost` | K1 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה; זה חוש החיות של כל גל. |
| S17 | טווחי החתיכות העמידות (`durable`) לכל דיסק | מדווח ומשמש | `agent/lib/restore/chunkledger.sh:81` · `ledger_seq_json`<br>`server/rounds/chunkledger.py:134` · `record`<br>`server/rounds/chunksend.py:737` · `_send` («self.ledger.durable»)<br>`server/rounds/repair.py:115` · `plan` | K1 K2 K4 K5 | ⏳ אחר כך | קיים ומשמש להשלמה, ונשאר פעיל (נדב: לא מכבים). לפני פרסום לציבורי: ראה הכרעה פנימית. |
| S18 | מצב המקלט לכל מחיצה (listening/ended/fetching) | מדווח ומשמש | `agent/lib/restore/chunkrecv.sh:52` · `_chunk_body`<br>`server/rounds/chunksend.py:323` · `report` | K5 K6 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה; והוא גם המקום שבו יגיע הדיווח 'משלים מגירה' של G1. |
| S19 | התקדמות הורדה (חתיכות שנעשו עמידות בזמן fetch) | מדווח ומשמש | `agent/lib/restore/chunkfetch.sh:24` · `CHUNK_FETCH_TELL_S`<br>`server/rounds/chunksend.py:489` · `_wait_marks`<br>`server/rounds/chunksend.py:520` · `_note_slow_fetches` | K1 K5 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (הורדה שמתקדמת אינה שתיקה). |
| S21 | ‏throttle של המעבד בגל | מדווח ומשמש | `agent/lib/core/datareport.sh:89` · `_cpu_stat`<br>`server/rounds/drive_rates.py:582` · `_fleet_start` («throttled»)<br>`server/rounds/drive_rates.py:675` · `_from_previous` | K8 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (גל שהמעבד הואט בו לא מלמד קצב). |
| S22 | טמפרטורת הדיסק אחרי הכתיבה | מדווח ומשמש | `agent/lib/core/progress.sh:172` · `build_progress`<br>`server/disks/smart_history.py:98` · `record_temp` | K9 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (חם בכתיבה = כתום בסבב הבא). |
| S23 | המשכפל ממתין לשאלה למפעיל | מדווח ומשמש | `agent/lib/core/attended.sh:30` · `attended_hello`<br>`server/rounds/prompt_hold.py:60` · `_OPEN_SQL` | K1 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (השולח לא נוטש משכפל שמחכה לאדם). |
| S24 | שיכפול מקומי רץ | מדווח ומשמש | `agent/lib/core/attended.sh:36` · `attended_hello`<br>`server/auto_reboot.py:131` · `busy_reason` | — | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (השרת לא מאתחל את עצמו באמצע). |
| S25 | המשכפל "מוחזק" בסוף סבב, עם בעיות | מדווח ומשמש | `agent/lib/core/hold.sh:85` · `held_report`<br>`server/rounds/room.py:948` · `_unseen` | K1 K10 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (השכמה מחדש והכשלת סבב). |
| S26 | אירוע SMART בשער (‏disk-event) | מדווח ומשמש | `agent/lib/smart/smartgate.sh:21` · `smart_event_json`<br>`server/disks/disk_events.py:52` · `ingest` | — | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (בסיס CRC והיסטוריית SMART). |
| S27 | ‏UDP `InErrors` | מדווח, לא משמש | `agent/lib/core/waits.sh:253` · `udp_drops`<br>`server/learn/net_history.py:91` · `wave_drops` | — | ⏳ אחר כך | כבר נאסף ומוצג; להפוך אותו להגנה רק כשיש מוח, ולא ראינו כשל שהוא היה מונע. |
| S28 | מעבד המשכפל: busy, ‏iowait, טמפ' שיא (סוף גל בלבד) | מדווח, לא משמש | `agent/lib/core/datareport.sh:89` · `_cpu_stat`<br>`server/rounds/drive_rates.py:1104` · `_cpu` | — | ⏳ אחר כך | כבר נאסף; ממוצע אחד לגל לא מכריע 'איפה הצוואר', ומחכה לתוצאת M13. |
| S29 | עליית CRC ‏(SMART 199) בסבב, וה-CRC עצמו | מדווח, לא משמש | `agent/lib/core/progress.sh:170` · `build_progress`<br>`agent/lib/hw/disklink.sh:72` · `disk_link_json`<br>`server/rounds/room_precheck.py:54` · `crc_baseline` | K9 | ⏳ אחר כך | האזהרה הקיימת מספיקה היום; 'להעדיף HTTP למגירה כזו' הוא החלטת מוח. |
| S30 | מצב בקר ה-SATA ‏(ahci/ide/raid, לפי מחלקת PCI) | מדווח, לא משמש | `agent/lib/hw/fwimage.sh:14` · `fw_sata_mode`<br>`server/agent/firmware_image.py:65` · `build` | K3 | ✅ עבודה חדשה | קטן, הוכרע ב-13/11 (בלי AHCI אין פס), ועוזר כאזהרה גם בלי המוח (G12). |
| S31 | ‏duplex, ‏rx_crc_errors, ‏rx_dropped, ‏tx_errors של הכרטיס | מדווח, לא משמש | `agent/lib/hw/probe.sh:152` · `probe_nic` | — | ⏳ אחר כך | כבר במטמון ה-probe; לא ראינו כשל שהוא היה מסביר. |
| S32 | בדיקת כבל ‏(ethtool), ‏LLDP (לאיזה פורט מחובר) | מדווח, לא משמש | `agent/lib/hw/netprobe.sh:55` · `_np_cable_run`<br>`server/agent/probe.py:263` · `verdicts` | — | ⏳ אחר כך | תקציב לפי מתג רלוונטי רק כשיש כמה מתגים בדרך; היום אין החלטה שצריכה אותו. |
| S33 | דגם הדיסק | מדווח, לא משמש | `agent/lib/hw/diskinfo.sh:110` · `build_disk_entry`<br>`server/rounds/room.py:268` · `drawer_list` | — | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (אזהרת דגם איטי). |
| S34 | פסק ה-readback ‏(ok/נכשל/סיבה) | מדווח, לא משמש | `agent/lib/core/progress.sh:171` · `build_progress`<br>`server/rounds/room.py:254` · `drawer_list` | K4 | ✅ קיים, להשאיר | קיים; זו הראיה החיובית להצלחה (עיקרון 5), והמוח חייב להישען עליה. |
| S35 | חותמות זמן של שלבי שחזור לכל יעד | מדווח, לא משמש | `agent/lib/core/datareport.sh:73` · `target_extra_json`<br>`server/agent/restore_stages.py:69` · `record` | — | ✅ קיים, להשאיר | כבר נשמר, ויענה על M6 בלי קוד חדש. |
| S37 | זנב יומן הסוכן (עד 400 שורות או 64KB) | מדווח, לא משמש | `agent/lib/core/datareport.sh:11` · `agent_log_send`<br>`server/agent/logs.py:150` · `receive`<br>`server/agent/logs.py:112` · `classify` | — | ✅ קיים, להשאיר | קיים; את הקיטוע בגל ארוך מתקנים ב-G19. |
| S38 | צעדי אתחול ותזמונם | מדווח, לא משמש | `agent/lib/core/common.sh:234` · `trace_step`<br>`server/agent/boottrace.py:80` · `record`<br>`server/learn/wol.py:101` · `answered` | — | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (סוגר WoL). |
| S39 | טמפ' רכיבים (‏thermal) | מדווח, לא משמש | `agent/lib/hw/probe.sh:133` · `probe_thermal` | — | ⏳ אחר כך | לא ראינו התחממות משכפל; רלוונטי רק לפס ארוך. |
| S41 | מוני המקלט של chunkcast לכל מעבר: חבילות, זרות, פגומות, כפולות, חתיכות שננטשו באמצע (`evicted`), ‏`busy`, ‏`skipped` לכל יעד, ‏`bad_chunk` | לא קיים | `agent/chunkcast.c:704` · `cc_report` («stat_rx»)<br>`agent/chunkcast.c:1213` · `cc_recv`<br>`agent/lib/restore/chunkrecv.sh:49` · `_chunk_body` | K7 | ✅ עבודה חדשה | בלעדיו אין שום אות לבריאות הזרם במנוע החתיכות, וזה גם אבחון לשיכפול של היום (G7). |
| S42 | SO_RCVBUF שהקרנל באמת נתן | מדווח, לא משמש | `agent/chunkcast.c:1130` · `cc_recv` («SO_RCVBUF»)<br>`agent/chunkcast.c:132` · `CC_RCVBUF` | — | ✅ עבודה חדשה | קטן, ומסביר את החתיכות שננטשות: ביקשנו 16MB וקיבלנו 416KB (G7). |
| S43 | מילת הפסק של המעבר (ended/complete/stalled/no-stream…) | מדווח, לא משמש | `agent/chunkcast.c:711` · `cc_report`<br>`agent/lib/restore/chunkrecv.sh:131` · `_chunk_word_rc` | — | ✅ עבודה חדשה | קטן; השרת יבחין בין 'נגמר' ל'נתקע' במקום לקבל רק ended (G7). |
| S44 | ‏`RcvbufErrors` בגלי חתיכות | לא קיים | `agent/lib/restore/drawers.sh:79` · `restore_partition_drawers`<br>`agent/lib/core/waits.sh:253` · `udp_drops`<br>`server/rounds/drive_rates.py:350` · `_rcvbuf` | K7 | ✅ עבודה חדשה | קטן, ובלעדיו כל גל חתיכות הבא נשאר unmeasured (G6). |
| S45 | "המשכפל משלים מגירה שחזרה" | לא קיים | `agent/lib/restore/drawerfill.sh:112` · `_db_part` («no pass: the server is not asked, nor told») | K1 K6 | ✅ עבודה חדשה | זה הכשל של PR #3112: השרת לא ידע שהמשכפל משלים, וסגר את הסבב (G1). **הערה:** ‏`_chunk_fetch` נקרא בלי pass: השרת לא נשאל ולא יודע. זה הכשל של PR #3112 |
| S46 | עומק התור לכתיבה לכל מגירה, ופיגור המגירה מהזרם | חלקי | `agent/lib/restore/wavesense.sh:66` · `_ws_window` («inflight_max»)<br>`agent/chunkcast.c:131` · `CC_QCAP`<br>`server/brain/wavesense.py` · `well_formed` | — | ⏳ אחר כך | תנאי ל-HTTP במקביל לזרם (B5), שהוא החלטת מוח. #3041: מדווח עומק התור של ההתקן (in-flight); חסר התור של chunkcast ופיגור המגירה מהזרם. |
| S47 | ‏`/proc/diskstats` לכל יעד: ‏io_ticks, ‏in-flight, זמן כתיבה | מדווח, לא משמש | `agent/lib/restore/wavesense.sh:66` · `_ws_window` («util_max»)<br>`server/brain/wavesense.py` · `DISK_FIELDS` | — | ✅ עבודה חדשה | קטן, ועונה 'האם הדיסק הוא הצוואר', שזו בדיוק השאלה של 242m (G17). |
| S48 | מעבד לכל ליבה לאורך הגל (לא רק ממוצע) | מדווח, לא משמש | `agent/lib/restore/wavesense.sh:66` · `_ws_window` («busy_max»)<br>`server/brain/wavesense.py` · `CPU_FIELDS` | — | ⏳ אחר כך | בינוני; נחוץ רק אם M13 לא יסביר את 36 החתיכות בשנייה. |
| S49 | עומס IRQ ו-softirq ‏(NET_RX) | מדווח, לא משמש | `agent/lib/restore/wavesense.sh:57` · `_ws_snap` («NET_RX:»)<br>`server/brain/wavesense.py` · `CPU_FIELDS` | — | ⏳ אחר כך | אבחון לשאלה ש-M13 אולי סוגרת; לפתוח רק אם לא. |
| S50 | מוני הכרטיס בזמן הגל: ‏rx_missed, ‏rx_no_buffer, גודל ring | מדווח, לא משמש | `agent/lib/restore/wavesense.sh:116` · `_ws_nic_json`<br>`agent/lib/restore/wavesense.sh:46` · `_ws_ring`<br>`server/brain/wavesense.py` · `NIC_FIELDS` | — | ⏳ אחר כך | אבחון לשאלה ש-M13 אולי סוגרת; לפתוח רק אם לא. בלי ethtool: «לא זמין» (`ethtool: false`), לא 0. |
| S51 | ‏`carrier_changes` (נפילות קישור מאז האתחול) | לא קיים | `agent/lib/hw/cablelive.sh:25` · `cable_live_check` | — | ✅ עבודה חדשה | כבר נקרא בסוכן ורק לא נשלח; קטן, ומסביר ניתוק קצר באמצע גל. |
| S52 | מוני Phy Event של SATA ‏(smartctl -x) | לא קיים | `agent/lib/hw/disklink.sh:60` · `disk_link` | — | ⏳ אחר כך | הפלט כבר נשמר מקומית; לא ראינו כשל SATA שאינו CRC. |
| S53 | מוני reset של ata_link בזמן אמת | לא קיים | `agent/lib/smart/hwissues.sh:39` · `hw_link_words` | K9 | ⏳ אחר כך | שייך לשליפה בפס (M8), לא לשיכפול של היום. |
| S54 | טמפ' הדיסק בזמן הכתיבה | לא קיים | `agent/lib/core/progress.sh:172` · `build_progress` | — | ⏳ אחר כך | לא ראינו SSD שמאט מחום; לפתוח אם מדידה תראה. |
| S56 | זמן מהכנסת דיסק עד שהוא מוכן (uevent → SMART → בדיקת כתיבה) | לא קיים | `agent/uevent.c:18` · `disk_event` | — | ⏳ אחר כך | שייך למצטרף מאוחר בפס (M6), ו-S35 כבר נותן את רוב התשובה. |
| S57 | חריץ שהיה תפוס ב-POST | לא קיים | — | K3 | ⏳ אחר כך | פס ייצור (דיסק חדש בחריץ); ממתין ל-#3108. |
| S58 | שם החיבור הפיזי של המגירה (‏ata1 מול התווית "2") | לא קיים | `agent/lib/hw/diskinfo.sh:67` · `port_from_path`<br>`server/disks/slot_names.py:104` · `label` | — | ⏳ אחר כך | בינוני, ו-slot_names.py קיים ולא נקרא; לבדוק קודם מה כבר יש. |
| S59 | נתוני ההורדה: טווחים, בתים, זמן, ‏curl rc | לא קיים | `agent/lib/restore/chunkfetch.sh:52` · `_chunk_fetch` | K5 | ⏳ אחר כך | המדידה עצמה עוזרת (`r_http` אמיתי), אבל היא חלק ממסלול ההשלמה. לפני פרסום לציבורי: ראה הכרעה פנימית. |
| S60 | לחץ זיכרון במשכפל (PSI) | לא קיים | — | — | ⏳ אחר כך | רלוונטי רק כש-HTTP וזרם רצים יחד (B5). |
| S62 | אילו צפצופים הושמעו | לא קיים | `agent/lib/core/roundbeep.sh:42` · `_rb_unheard` | — | ✅ עבודה חדשה | צפצופים בלי יומן (#3106); קטן (G5). |
| S63 | ‏hash לכל חתיכה ב-readback, וההיסט הראשון שאינו תואם | מדווח ומשמש | `agent/lib/restore/rbchunks.sh:30` · `rb_bad_list`<br>`agent/lib/restore/readback.sh:79` · `rb_digest`<br>`server/brain/integrity.py:184` · `target`<br>`agent/fanout.c:635` · `rb_pread` | K4 | ⏳ אחר כך | השלמה ממוקדת אחרי readback במקום כשל. ראה הכרעה פנימית. #3287: נשלח, ו-K4 קורא אותו בצל. |
| S64 | ‏UUID/זהות אימג' בתוך היומן של כל דיסק | לא נבדק | `server/rounds/chunkledger.py:134` · `record` («image_id») | — | ✅ עבודה חדשה | בדיקה קטנה (G21): חתיכה מאימג' אחר = כשל שקט; קודם לבדוק אם זה כבר קיים. **הערה:** שורת `chunk_ledger` נושאת `image_id` (‏`chunkledger.record`). האם חתיכה מאימג' אחר נחסמת בכתיבה — לא נבדק (G21) |
| S65 | שם מנהל ההתקן של בקר הדיסקים (`ahci` מול `ata_piix`) | לא קיים | `agent/lib/hw/fwimage.sh:14` · `fw_sata_mode` | K3 | ✅ עבודה חדשה | קטן, הוכרע ב-13/11, וקובע את ניסוח האזהרה הכתומה (G12). |

## השרת (V)

| # | חוש | מצב | איפה (נאסף · נצרך) | כללים | המלצה | למה |
|---|---|---|---|---|---|---|
| V1 | מהירות הקישור של כרטיס ההפצה | מדווח ומשמש | `server/rounds/drive_rates.py:208` · `read_link`<br>`server/rounds/drive_rates.py:766` · `choose` | K7 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (תקרת הגל לפי הכרטיס). |
| V2 | דיסק הספרייה (diskstats) ו-PSI של I/O, בתחילת הגל ובסופו | מדווח ומשמש | `server/learn/server_io.py:92` · `begin`<br>`server/learn/server_io.py:137` · `end`<br>`server/app.py:644` · `wave_bitrate` («server_io.begin»)<br>`server/app.py:628` · `on_sender_event` («server_io.end»)<br>`server/app.py:682` · `on_chunk_event` («server_io.end»)<br>`server/learn/server_senses.py` · `window` («server_io.measure») | K7 | ✅ עבודה חדשה | נסגר בשני המנועים (#3064, G4), ו-`bound` צורך אותו. #3125: גם בכל חלון של הגל. |
| V4 | מוני השולח chunkcast: חתיכות שנשלחו, בתים, זמן, האטה בגלל `--write-kbps` מול `--kbps` | חלקי | `server/learn/sender_senses.py` · `read`<br>`server/learn/sender_senses.py` · `step`<br>`server/rounds/chunklog.py` · `log_path`<br>`server/rounds/directchunk.py` · `runner` («chunklog.run_process»)<br>`agent/chunkcast.c:1366` · `cc_pace` | K8 | ✅ עבודה חדשה | קטן, ומראה בכל גל מה באמת הגביל את הזרם: מכריע את 242m (G9). #3125: חתיכות, בתים וזמן לכל `chunkcast` חי, ובחלון: חתיכות ו-Mbps, מהלוג שהרצת הייצור כותבת (#3340). **חסר:** מי משני הקצבים האט. הלוג לא אומר, ו-`cc_pace` ישן בלי לספור: שינוי ב-`agent/chunkcast.c`. |
| V5 | המקום בזרם החי (חתיכה k מתוך N, ‏%) | מדווח, לא משמש | `server/learn/sender_senses.py` · `step`<br>`server/learn/sender_senses.py` · `total` | — | ✅ עבודה חדשה | קטן, ונותן ETA מדויק במסך כבר היום (G15). #3125: k מ-`sent` האחרונה, ו-N מהאינדקס (מעבר ראשון) או מרשימת ההשלמה. |
| V6 | קצב ההגשה ב-HTTP לכל משכפל, ומספר ההורדות במקביל | מדווח, לא משמש | `server/learn/server_senses.py` · `_http`<br>`server/brain/served.py` · `snapshot`<br>`server/agent/api.py` · `_CountedFile` («self._served.opened») | K1 K2 K7 | ✅ עבודה חדשה | ב-6 משכפלים HTTP חונק את 1GbE של השרת; בלי מונה אין תקציב (G16). #3125: לכל כתובת בכל חלון. |
| V7 | קצב ההורדה של כל משכפל (מה-ledger) | מדווח, לא משמש | `server/learn/server_senses.py` · `_ledger`<br>`server/learn/server_senses.py` · `durable`<br>`server/rounds/chunksend.py:520` · `_note_slow_fetches` | K5 | ✅ עבודה חדשה | כבר נמדד בזיכרון ונעלם; קטן לשמור (G10). #3125: מ-`chunk_ledger`, לכל משכפל בכל חלון. |
| V8 | מעבד השרת לכל תהליך (chunkcast, ‏uvicorn) | מדווח, לא משמש | `server/learn/server_senses.py` · `processes`<br>`server/learn/server_senses.py` · `_chunkcast_ticks` | — | ✅ עבודה חדשה | קטן, ונדרש כדי לדעת איפה השרת נתקע ב-6 משכפלים (G18). |
| V9 | מוני הכרטיס של השרת: tx bytes, ‏drops | מדווח ומשמש | `server/brain/netsense.py` · `nic_counters`<br>`server/learn/server_senses.py` · `window` («nic_tx_mbps») | K7 | ✅ עבודה חדשה | קטן, וזה המספר שתקציב הרשת E נשען עליו (G18). #3125: נשמר גם לגל. |
| V10 | ‏PSI של מעבד וזיכרון בשרת, ‏loadavg | מדווח, לא משמש | `server/learn/server_senses.py` · `_psi`<br>`server/learn/server_senses.py` · `_loadavg` | K7 | ✅ עבודה חדשה | קטן, באותו קובץ כמו G4/G18; עומס כללי של השרת. |
| V11 | מקום פנוי בספרייה | מדווח ומשמש | `server/learn/server_io.py:46` · `SERVER_DISK_UTIL` | — | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה. |
| V12 | טבלאות ה-DB: ‏chunk_ledger, ‏wave_rates, ‏drive_rates, ‏model_speeds, ‏session_members | מדווח ומשמש | `server/db.py:458` · `chunk_ledger`<br>`server/db.py:735` · `wave_rates`<br>`server/db.py:713` · `drive_rates`<br>`server/db.py:768` · `model_speeds`<br>`server/db.py:93` · `session_members` | — | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (הזיכרון בין גלים). |
| V13 | ה-journal | מדווח, לא משמש | `server/db.py:2176` · `journal` | — | ✅ קיים, להשאיר | קיים; חומר גלם לסימולטור, בלי עבודה. |
| V14 | קפיצת שעון | מדווח ומשמש | `server/system/clockstep.py:149` · `guard`<br>`server/rounds/room.py:792` · `_mark_lost` | K1 | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (לא מכריזים אובדן בטעות). |
| V15 | תור הממתינים: אילו דיסקים מחכים להצטרף, ומתי נכנסו | מדווח, לא משמש | `server/brain/joinwatch.py:88` · `queue`<br>`server/brain/joinwatch.py` · `_late` | — | ⏳ אחר כך | תור ממתינים = פס ייצור. ראה הכרעה פנימית. |

## הרשת (N)

| # | חוש | מצב | איפה (נאסף · נצרך) | כללים | המלצה | למה |
|---|---|---|---|---|---|---|
| N1 | מהירות הקישור של המשכפל ושל השרת | מדווח ומשמש | `server/rounds/nic_speed.py:51` · `reported`<br>`server/rounds/drive_rates.py:208` · `read_link` | — | ✅ קיים, להשאיר | קיים ומשמש היום; להשאיר בלי עבודה (= S5, V1). |
| N2 | קפיצה באבדן מול בסיס כרטיס ההפצה | מדווח ומשמש | `server/learn/netloss.py:79` · `assess` | — | ✅ קיים, להשאיר | קיים ומשמש היום, בגל חתיכות (#3164), לפי `abandoned` לכל GB. מאז #3173 זה המנוע היחיד. לא נמדד על ברזל; להשאיר בלי עבודה. |
| N4 | ‏IGMP snooping ו-querier | מדווח, לא משמש | `server/brain/l2sense.py:117` · `n4_server`<br>`server/brain/l2sense.py:94` · `n4_cloner`<br>`server/net/igmp_querier.py:212` · `sense`<br>`agent/netwatch.c:99` · `igmp_note` | — | ✅ קיים, להשאיר | מאזין פסיבי בשרת ובמשכפל (#3064, 15/11), בלי גישה למתג: נמצא / לא נמצא querier / לא נבדק. מצב צל. |
| N5 | מוני פורט במתג: ניצולת, ‏drops | לא קיים | — | — | ⏳ אחר כך | חסום: אין גישה לניהול המתג. יפתח: SNMP לקריאה בלבד, או ייצוא ידני של מוני הפורטים. ראה `catalog.toml`. |
| N6 | הצפת מולטיקאסט לפורטים שלא ביקשו | מדווח, לא משמש | `server/brain/l2sense.py:141` · `n6_cloner`<br>`agent/netwatch.c:334` · `sniff` | — | ✅ קיים, להשאיר | משכפל ממתין מקשיב ב-promisc וסופר מסגרות של קבוצת המנוע בלי שהצטרף (#3064). מוצף / לא מוצף / לא נמדד / לא נבדק. מצב צל. |
| N8 | תעבורה אחרת ברשת ההפצה | מדווח, לא משמש | `server/brain/l2sense.py:182` · `n8`<br>`server/net/netwatch.py:143` · `Watch`<br>`agent/netwatch.c:127` · `classify` | — | ✅ קיים, להשאיר | השרת והמשכפל סופרים מסגרות שאינן של ImageSrv (#3064). בלי סף: המוח יתאים לבסיס. מצב צל. |

**ראיית חיים** (טיוטת התכנון, סעיף 5א) היא OR של שלושה חושים: דיווח progress (S16), התקדמות
ה-ledger (S17, ‏S19), ובייטים שהשרת עצמו הגיש למשכפל ב-HTTP (V6, עוד לא קיים). ‏**V6 לבדו היה מציל את
13/11**, גם בלי לשנות את הסוכן. הוא לא מחליף את S45: ‏S45 נותן את הסיבה ("משלים מגירה N"), ‏V6 רק "עסוק".

</div>
