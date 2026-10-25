<div dir="rtl">

# מקורות למדריך מנהל ה-IT (`docs/admin-guide-he.md`, ‏#1474)

כל כלל בחומת האש ובטבלאות הרשת שבמדריך — ומאיפה בקוד הוא נלקח. שורות נכונות
לענף `integration/v0743-speed` (‏`cbad065c`, ‏16/10/2026). כשמשנים פורט או זרימה
בקוד — מעדכנים כאן ובמדריך, ומריצים מחדש:
`python tools/docs/md2pdf_he.py docs/admin-guide-he.md --pdf`.

## כללים שנפתחים (פרק 3.1)

| כלל | מה | מקור בקוד |
|---|---|---|
| F1 | קונסולה `8081/tcp` HTTPS, על כרטיס הניהול; השרת אינו בודק מקור | `server/main.py:153` (‏`--console-port` 8081), `server/main.py:167` (‏`--console-host`); `install/nftables-rules.sh:195` (פתוח על כרטיס השרתים, בלי `saddr`); `install/firewall-rules.md:3-7` ("אין בדיקת כתובת מקור בקוד"); `server/health.py:628-631` |
| F2 | ראשי ← משני `8443/tcp` mTLS | `server/main.py:213` (‏`--interserver-port` 8443); `install/nftables-rules.sh:180-183` (במשני: רק `ip saddr` של הראשי); `server/storage_transfer.py:3-7` (דחיפה על הערוץ המאומת, ‏TLS 1.3); `server/storage_client.py:486` (‏TLSv1_3 מינימום); `server/interserver_api.py:1396` (המשני מתחבר ל-5900 של מכונה בשביל הראשי) |
| F3 | משני ← ראשי `8443/tcp` "אתה שם?" | `server/primary_alive.py:30` (‏`/api/interserver/v1/alive`); `install/nftables-rules.sh:184-188`; `docs/server-install.md:239-248` (DNAT באתר הראשי, 8081 לא בין אתרים) |
| F4 | ‏NTP ‏`123/udp` | `installer/lib/finish.sh:86-90` (‏`systemd-timesyncd` מופעל, בלי שרת מוגדר → ברירת המחדל של Debian); `server/health.py:190-197` (בדיקת "סנכרון שעון"); `install/firewall-rules.md` כלל L4 |
| F5 | ‏DNS ‏`53` | `installer/lib/finish.sh:101` (‏`servers_dns` באשף); `server/update.py:55-58` ("חומת אש או DNS") |
| F6 | ‏`github.com:443` לעדכון | `server/update.py:54` (‏`PUBLIC_UPDATE_URL = https://github.com/NadavOked/ImageSrv.git`), `server/update.py:191` (‏`git ls-remote --tags`); `tools/server-upgrade.sh:152` (‏`git fetch --tags`), `tools/server-upgrade.sh:162-164` (אימות חתימה מול `install/release-signers`); `install/firewall-rules.md` כלל L5 |
| F7 | ‏VM ← אינטרנט `80/443` | היום: `server/vmnet.py:12-16,87-101` (יציאה דרך כרטיס הניהול בלבד, לכתובות לא-פרטיות; DNS לשרתי השמות בלבד). ‏v1: הכרעת נדב 16/10, ענף `feat/vm-own-mac` — **לא בריפו עדיין** |
| F8 | ‏VM ← שיתוף רשת `445/tcp` | ‏v1 בלבד (בריף נדב 16/10). היום חסום: `server/vmnet.py:44-46` (‏`DENIED` כולל 10/8, ‏172.16/12, ‏192.168/16). הפורט 445 — הנחה של המדריך (SMB), לא מהקוד |

## כללים שנסגרים (פרק 3.2)

| כלל | מקור |
|---|---|
| D1 כיתות ← שרת | `install/firewall-rules.md` כלל L6; `install/nftables-rules.sh:7,10-11` ("הכיתות (v2) אינן נפתחות כאן כלל"); `server/health.py:632-634` (‏4011 רק כשיש כיתות) |
| D2 ‏SSH | `install/nftables-rules.sh:10` ("אין כלל TCP 22 בשום מצב (#1266)"); `install/firewall-rules.md` כלל L8 |
| D3 ההפצה אינה מנותבת | `install/firewall-rules.md` §3 (L1 ופסקת ה-DHCP); `README.md:279` |
| D4 מולטיקאסט/WoL לא לכיתות | `install/firewall-rules.md` כלל L9; `README.md:261-266` |
| D5 קונסולה רק מ-IT | ראו F1 |
| D6 ‏VM לא מגיע לרשתות פנימיות | היום: `server/vmnet.py:17-19,44-46,97-100`. ‏v1: בריף נדב 16/10 |

## בתוך וילן ההפצה (פרק 3.3)

| מה | מקור |
|---|---|
| DHCP ‏`67/68` | `install/nftables-rules.sh:162-163`; `server/health.py:592-612`; כבוי בהתקנה: `docs/server-install.md:313-320` |
| TFTP ‏`69` + conntrack helper | `install/nftables-rules.sh:164-165`; `install/firewall-rules.md` §3 (אזהרת `nf_conntrack_tftp`); `server/health.py:574` |
| HTTP ‏`8080` לתחנות, לא מוצפן | `server/main.py:149`; `install/nftables-rules.sh:166-167`; `README.md:290-296` (סיסמת משתמש ההפצה גלויה) |
| מולטיקאסט `9000-9001/udp`, ‏TTL 1 | `server/sender.py:37,41` (‏`PRODUCTION_PORTBASE = 9000`, ‏portbase ו-portbase+1); `server/sender.py:659-685` (‏`udp-sender` בלי `--ttl`); `install/nftables-rules.sh:35,168-169`; `README.md:248-249`; מחשב הבנייה כמקור: `agent/lib/directsend.sh:36` |
| ‏WoL ‏`9/udp` ‏`255.255.255.255` | `server/wol.py:28-29`, `server/wol.py:140-150` (על הממשק בלבד); `install/nftables-rules.sh:170` |
| מוניטור `5900/tcp` שרת ← תחנה, כבוי כברירת מחדל | `server/monitor.py:21` (‏`MONITOR_PORT`), `server/monitor.py:656` (השרת פותח את החיבור); `install/nftables-rules.sh:135-136` (הדפדפן דרך פורט הקונסולה); `server/health.py:653-666` |
| ‏`4011` לא בגרסה 1 | `server/health.py:632-645` |
| ‏`8082` הוסר | `server/app.py:660`, `server/kiosk.py:3` (‏#1247) |
| חומת האש על השרת: input drop, ‏output accept, ‏forward drop | `install/nftables-rules.sh:209-237` |

## מתגים, DHCP relay ורשת (פרקים 2, 4, 5)

| טענה | מקור |
|---|---|
| הגדרות מתג ההפצה (וילן, port security, ‏IGMP, ‏DHCP snooping, בלי 802.1X, תיוג) | `README.md:268-284` |
| ‏IGMP: פקיעה אחרי 125–260 ש'; ‏1G ≈ 110MB/s; נמדד 106MB/s; ‏Dynamic Memory | `docs/imagectl-spec.md:109-115` |
| ‏10GbE לשרת | `docs/imagectl-spec.md:352` |
| בלי `ip helper-address` לשרת | `install/firewall-rules.md` §3 (פסקת DHCP) |
| אישור נוסף ל-DHCP על כרטיס trunk | `server/console_dhcp.py:415-418` |
| "מי עוד עונה" / מכונה מרשת זרה — אזהרה, לא חסימה | `README.md:286-288`; `server/foreign_vlan.py:1-11` |
| השרת אינו מתייג וילנים | אין יצירת ממשק VLAN ב-`server/netcfg.py`, ‏`server/console_netcfg.py`, ‏`server/deploy_net.py` (חיפוש `vlan` — 0 תוצאות) |
| הניהול יכול להיות רשת הכיתות | הכרעת נדב 16/10 (בריף) |
| ‏PortFast/edge | **לא מהקוד** — המלצה כללית, מסומנת במדריך |

## ‏VM לעדכון Windows (פרק 6)

| טענה | מקור |
|---|---|
| ‏MAC משלו, כרטיס לבחירה, DHCP reservation, ‏ESXi/Hyper-V | בריף נדב 16/10, ענף `feat/vm-own-mac` — **לא בריפו** |
| היום: NAT דרך כרטיס הניהול, בלי כתובות פרטיות | `server/vmnet.py:1-30`; `docs/vm-update.md` §3 |
| ‏`/dev/kvm` חובה, סירוב `no_kvm` | `server/vmupdate.py:104-110,662` |
| השרת על ברזל (13/10) | `docs/vm-update.md:9-10` |
| דומיינים של Windows Update, ‏KMS ‏1688 | **לא מהקוד** — ידע כללי על Microsoft; מסומן "להשלים מול הרשימה הרשמית" |
| וירטואליזציה מקוננת, Promiscuous | **לא מהקוד** — נגזר מדרישת `/dev/kvm`; Promiscuous הוא שאלה פתוחה |

## העברות בין אתרים ועדכונים (פרקים 7, 8)

| טענה | מקור |
|---|---|
| הראשי יוזם, דחיפה ומשיכה | `server/storage_transfer.py:3-7,18-19`; ‏#1070 |
| ניתוק = כישלון גלוי ומתחילים מחדש | `server/storage_transfer.py:12-16` |
| מקום פנוי: היום ‏2× + 256MiB | `server/interserver_api.py:304-306,514` |
| אחרי #1457: ‏1× + 256MiB | `origin/fix/1457-intersite-push` (‏`4e9d3c9f`): ‏`FREE_SPACE_FACTOR = 1` |
| ללא תקרה כברירת מחדל; תקרה בקונסולה | בריף נדב 16/10, ענף `feat/wave-rate-by-machine` — **לא בריפו** |
| עדכון רק בתג חתום, חזרה אוטומטית | `tools/server-upgrade.sh:162-170` (‏`verify-release-tag.py`), ‏`rollback` לאורך הסקריפט |
| בלי מקור apt חי | הכרעת נדב 15/10 ב-#1411 (תגובה ב-Issue) |
| ‏`deb.debian.org` הוסר מ-vmupdate-host | `install/vmupdate-host.sh` (‏#1459); נאכף ב-`tests/test_no_live_apt_source.py` |
| אין SSH בייצור | `install/firewall-rules.md` §1 (‏#1148, ‏#1266) |

## מחשבי השיכפול והבנייה (פרק 9, ‏#1563)

מדידות הברזל כאן הן מ-17/10/2026, ממחשב שיכפול אחד וממחשב בנייה אחד במעבדה.
דוחות המעבדה אינם בריפו. הנתיב שלהם מצוין כאן כמקור, ולא במדריך עצמו.

| טענה | מקור |
|---|---|
| ‏`Remote Wakeup Boot Source = Remote Server`: עם ברירת המחדל (דיסק), WoL ב-06:01:56 UTC — לוגו ואחריו מסך שחור, בלי DHCP (תצפית של נדב). אחרי השינוי: DHCP תוך 12ש', ודיווח לשרת (hello) תוך 43ש' ו-37ש' | ברזל 17/10, ‏HP Compaq 8100 (‏e1000e): ‏`C:\ImageCtl-Lab\evening-1015\metal-1017-poweroff.md`, הרצות 1b, ‏3, ‏4, והצעה ב'. נבדק על דגם אחד בלבד |
| "legacy עם דיסק GPT נתקע" | הסבר המנגנון — מ-#1563 ומהנחיית @מנהל-צוות. מה שנצפה הוא הסימפטום (לוגו, מסך שחור, בלי DHCP), לא המנגנון |
| כל מסלולי הכיבוי (קונסולה, מסך, אוטומטי) עוברים ב-`safe_poweroff`, שדורך WoL וקורא בחזרה לפני `poweroff -f`, ולא מכבה אם הדריכה נכשלה | `agent/lib/common.sh:54` (‏`arm_wol`), ‏`:94-117` (‏`safe_poweroff`); קוראים: `agent/lib/idle.sh:38,53`, ‏`agent/lib/localclone.sh:270`, ‏`agent/lib/lcwait.sh:46`, ‏`agent/lib/common.sh:147`, ‏`agent/monitor.c:602`; "כיבוי כל המשכפלים": `server/static/console.js:4468-4479` |
| כיבוי בכפתור עלול לשבור את ה-WoL הבא | תצפית מעבדה 16–17/10 (‏#1563, ‏#1562). **לא ניסוי מבוקר**, והמנגנון לא ידוע |
| ‏WoL לא מעיר את מחשב הבנייה (‏Realtek RTL8168h, ‏`r8169`) | ‏#1562 (ברזל 17/10). בקרה חיובית באותה רשת: מחשב השיכפול התעורר (‏`metal-1017-poweroff.md` הרצות 3, ‏4; ‏`C:\ImageCtl-Lab\evening-1015\metal-1017-results.md`) |
| ה-watchdog: רק במחשב שיכפול, 600ש' בלי התקדמות → אתחול | `agent/lib/watchdog.sh:35,50,103` |
| ה-watchdog לא מכסה כיבוי שנתקע | קרנל v6.12: ‏`drivers/watchdog/iTCO_wdt.c:592` (‏`watchdog_stop_on_reboot`), ‏`drivers/watchdog/watchdog_core.c:158-176` (‏`watchdog_reboot_notifier` עוצר על `SYS_POWER_OFF` לפני `device_shutdown()`); ‏`metal-1017-poweroff.md` ס' "למה ה-watchdog לא אתחל"; הערה ב-`agent/lib/watchdog.sh:4-6` |
| מסלול הכיבוי 4/4 על ברזל; תקיעה אחת לא שוחזרה | ‏`metal-1017-poweroff.md`, הרצות 1–4 וס' "ניתוח" 1 (הכרעה: ‏`poweroff -f` נשאר, הצעה א') |
| ‏1Gb ≈ ‏110MB/s; נמדד 106MB/s (17/09) | `docs/imagectl-spec.md:113-115` |
| קליטה: ‏74MB/s ל-NVMe מול 54MB/s ל-HDD, אותו מקור, אימג' וקו | `docs/research/R10-server-storage-and-capacity.md:22,38-42` (ברזל, 05/09) |
| סבב למגירה אחת: רשת 75MB/s (תקרה 600m), דיסק 202MB/s, 19.5GB | ‏`metal-1017-results.md` (בדיקה 8, ‏17/10, ‏08:24–08:30 UTC) |
| 202MB/s = ‏97% מ-209MB/s של בדיקת הכתיבה; המעבד 67% עסוק (usr+sys+sirq) ועוד 17% iowait; הדיסק הוא הגבול | ‏`C:\ImageCtl-Lab\evening-1015\cloner-pipeline-perf.md` ס' 0 ו-1.1 (‏@מנהל-ביצועים, ‏17/10; ‏#1571). מעבד i5-650, שתי ליבות פיזיות |
| המעבד כגבול בכמה מגירות | אותו מסמך, ס' 0 סעיף 3 — **הערכה**, לא מדידה |
| קצב הסבב: פתיחה 60% / גג 90% מהכרטיס (600m/900m ב-1Gb), ‏+100m אחרי סבב נקי, ‏×0.75 מעל 0.1% שידורים חוזרים, רצפה רבע מהפתיחה, לפי המכונה האיטית, לא משתנה באמצע | `server/drive_rates.py:1-24` (תיעוד), ‏`:92,98,102,104,106,111,125-126` (קבועים), ‏`:253-254` (ירידה), ‏`:312` (המכונה האיטית), ‏`:400` (רצפה). הסף 0.1% מסומן בקוד "ניחוש מנומק, לא מדידה" (‏`:107-110`) |
| 600m עם 228,044 שידורים חוזרים (~1.7%) → הסבב הבא 450m | יומן שרת המעבדה 17/10: ‏`450m basis=down ... 600m rexmits=228044`, כפי שצוטט ב-`cloner-pipeline-perf.md` ס' 0 |

## המסכים של המוצר (פרק 13, v0.76.0)

שורות נכונות לענף `integration/v0760-rc6` (‏`fdbbca8c`, ‏18/10/2026). כל מה שכאן נקרא בקוד;
**לא הורץ בדפדפן ולא על ברזל** אלא אם נאמר אחרת.

| טענה | מקור |
|---|---|
| קבוצות ושורות ההגדרות, טווחים, מתגים מסוכנים (`confirmOff`), "לא נקרא" | `server/static/console.js:6984` (‏`SETTING_GROUPS`), ‏`:6992` (‏`SETTING_ROWS`), ‏`:7103` (‏`settingsSwitch`), ‏`:7170` (‏`settings`); הודעת הקומיט `cc2ece3f` (#1685) |
| כל שינוי נשמר מיד ונרשם ביומן | `server/static/console.js:7103-7112` (`post("/settings")` ואחריו toast "נשמר — נרשם ביומן") |
| ההפעלה מחדש האוטומטית: כבוי / כל יום / פעם בשבוע | `server/static/console.js:7154` (‏`autoRebootSheet`) |
| לשונית "שרתי משנה": רק admin, ורק בראשי/עצמאי | `server/static/console.js:7167` (‏`settingsSecondariesAllowed`); `server/static/branches.js:486` (‏`settingsSecondariesTab`); הקומיט `99010d76` |
| תגי המצב `מחובר` / `לא ענה` / `בודק חיבור…` / `מושבת` | `server/static/branches.js` (‏`branchPillHtml`) |
| רישום משני בשני שלבים (כתובת → SPKI → קוד) | `server/static/branches.js:556` (‏`openEnrollSheet`) |
| F4 במשני: קוד 8 תווים ל-10 דקות; ‏F2 "Primary-Visible Address" | מסירת 17/10 §1.1 (מ-CHANGELOG v0.75.0); `haifa-pairing-1017.md` "צעדים לנדב בבוקר" ב-`C:\ImageCtl-Lab\evening-1015\`. **הקוד של `dcui/` לא נקרא במסמך הזה** |
| "בטל דיסק זה": הנתיב, ההרשאות, הסירובים, "ביטול אינו כשל" | `server/room.py:1696` (‏`POST /drawer/cancel`); `server/drawer_cancel.py`; הקומיט `88f26d5f` |
| הסוכן מוחק GPT ראשי וגיבוי של הדיסק שנכתב, לפי מספר סידורי | `agent/lib/cancel.sh`, ‏`agent/lib/verdict.sh` (‏`cancel_drawer`); הקומיט `bd7183e9` |
| חלון הביטול בגואי: "המשך לכתוב" (Esc), "בטל את דיסק N" (Del, לא Enter), ‏X במחשב הבנייה | `native-gui/src/canceldlg.c`; הקומיט `6f940f76` |
| כפתור "בטל…" בקונסולה, `sheet()` רגיל בלי הקלדת מילה | `server/static/console.js` (הקומיט `188d3bcf`); #1270 |
| אזור זמן: `timedatectl`, קריאה חוזרת, ‏DB ב-UTC, שדה בהתקנה | `server/timezone.py`; `server/static/console.js:7145` (‏`timezoneSheet`); `installer/lib/zones.txt`; הקומיט `c3b2e8f1` (#1447) |
| הנפקת תעודה: `POST /tls/reissue`, ‏`CN=imagesrv-console`, ‏`console.crt.prev`, התנתקות | `server/console_hostcfg.py:134`; `server/static/console.js:7223` (‏`tlsReissueSheet`), ‏`:7208` (‏`tlsDetailsSheet`) |
| ספירת 20 שניות כשהכול ירוק: `END_POWEROFF_S=20`, "כבה עכשיו" (Enter); יעד עם בעיה — בלי ספירה ובלי תקרה עד "כבה" (#2024, ‏`END_HOLD_S` בוטל) | `agent/lib/idle.sh` (‏`end_countdown`, ‏`end_problem`); `native-gui/src/main.c` (‏`end_modal`); ‏`native-gui/src/screens_ask.c` (‏`draw_end_bar`) |
| מחשב הבנייה שואל "כבה" / "חזרה לתפריט"; שיכפול מקומי שנעצר חוזר לבחירה | `CHANGELOG.md` סקשן `[Unreleased]` (#1560); זיכרון `no-black-screen-gui-always` (הכרעות 17/10 12:45) |
| צבעי הדיסק: אדום SMART תקול ("כתוב בכל זאת", דלג כברירת מחדל), כתום "נכשל קודם", ירוק "עצירה" | `CHANGELOG.md` `[Unreleased]` (#1580, "צבעי הדיסק בשיכפול לפי הכרעת נדב 17/10"); `server/smart_ask.py:22`; `server/disk_events.py:24-26` |
| דיסק איטי = אדום (סף 50MB/s או פחות מחצי מהחציון של האחרים) | `agent/lib/writecheck.sh:22,40,138-156` |
| CRC מעל הסף = כתום; ברירת מחדל `0` (כבוי) | `server/static/console.js:6992+` (שורת `crc_warn_threshold`); #1366 |
| מה שעוצר BIOS של HP על F1 = כתום | `agent/lib/smart.sh:32,121` (#1366) |
| שליפת מגירה = `removed` = ירוק | `CHANGELOG.md` `[Unreleased]` ("מגירה שנשלפה באמצע הכתיבה אינה צובעת את הדיסק כתום") |
| השאלה בלי תשובה נסגרת אחרי חצי שעה; "השידור ממתין לתשובה על דיסק" | `CHANGELOG.md` `[Unreleased]` ("השידור נכשל אחרי 180 שניות…", #1581) |
| שורת הדרייברים: מצבים, צבעים, `BOOT_RISK_HE` | `server/static/console.js:4496-4530` (‏`driversOutcomeText`, ‏`driversTone`, ‏`bootStorageText`) |
| עמוד הדרייברים: לשוניות, ייבוא `tar`, אימות sha256, מחיקה | `server/static/drivers.js:1-8`; `server/static/console.js:7461`; `docs/driver-packages.md` |
| `drivers.linux`: בודק ולא משנה | הקומיט `886a6628` (#1331 T8); `agent/lib/linuxdrivers.sh` |
| `Start=0`/`StartOverride=0` רק כשחבילה הונחה (הכרעת נדב 17/10) | זיכרון `night-1017-autonomous` (הכרעות ~21:00); `agent/lib/bootstorage.sh`; #1711 (ניסוי ה-VM) |
| הספירה במשכפל נראתה על ברזל (rc2, שיכפול מקומי, 17/10); אחרי כשל — כיבוי תוך <80ש', הספירה לא נצפתה | ‏`C:\ImageCtl-Lab\evening-1015\metal-1017-results.md`, ס' "rc2" |

## פרק 4.1–4.20: רשת מנוהלת בשלוש שכבות (22/10/2026)

| מה | מקור |
|---|---|
| פורטי המולטיקאסט: `--portbase` (ברירת מחדל 9000, ו-`portbase+1`), `--interface`, בלי `--mcast-data-address` ובלי `--ttl` | `server/sender.py` (`command_for`, `DEFAULT_PORTBASE`) |
| זרם מולטיקאסט אחד בכל המערכת | `server/sessions.py:54` |
| WoL: UDP 9, broadcast `255.255.255.255`, מוצמד לכרטיס ההפצה | `server/wol.py` (`WOL_PORT`, `broadcast_sender`) |
| אין IGMP querier בשרת (Issues #1543, #1972) | חיפוש `querier` ב-`server/`, `agent/` — אין תוצאות |
| ערוץ הבקרה של udpcast ו-כתובת הקבוצה הנגזרת | דף man של `udp-sender` (Debian); לא נקבע בקוד |
| TCN, תקלות DHCP snooping, EEE, duplex, LACP | `C:\ImageCtl-Lab\evening-1015esearch-new-failures-1022.md` (N01, N02, N05, N07, N09, N28) |
| ~106MB/s, 1G, 10GbE בחדר השיכפולים | `docs/imagectl-spec.md` סעיף 6 ושורה 351 |

</div>
