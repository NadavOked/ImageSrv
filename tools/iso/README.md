<div dir="rtl">

# `tools/iso/` — ISO ההתקנה של ImageCtl (#1139)

‏ISO אחד שמתקין דביאן 13 + ImageCtl על שרת פיזי או VM, ‏UEFI (Secure Boot
דלוק) וגם BIOS, **בלי אינטרנט** בזמן ההתקנה. בסופו השרת עולה במצב "רשת
ההפצה לא הוגדרה" — האשף (#910) / הקונסולה מקבלים את המנהל. התוכנית:
`docs/research/answers/R25-installer-iso-external.md`; ההכרעות שנקלטו:
‏R57 (Secure Boot), ‏R58 (preseed), ‏R60 (משני), ‏R61 (reproducible),
‏R62 (זיהוי הכרטיס), ‏R63 (ה-repo).

| קובץ | תפקיד |
|---|---|
| `packages.txt` | **האיחוד** של כל רשימות ה-apt: ‏`PKGS` של המתקין, הבסיס/GUI/toolbox של `build_initramfs.sh`, ומה שרק ה-ISO מוסיף. ‏`tests/test_iso_build.py` נופל אם רשימה זזה בלי הקובץ הזה |
| `make-pool.sh` | ‏`apt-get download` של הרשימה **וכל התלויות** (מול status ריק — כלום לא "כבר מותקן") → `pool/{main,contrib}/imagectl/`, ואינדקסי `dists/` (‏`--index-only` מאנדקס עץ ISO). נכשל בלי `trixie-security` במקורות של המארח (‏#1411). ‏`--security`: רק חבילות trixie-security של הסגירה, כ-tar + `security-pool.json` לכפתור העדכון |
| `preseed.cfg` | ‏d-i אוטומטי: mirror כבוי, חבילות מה-ISO (מסלול ה-cdrom של d-i), הדיסק הראשון שאינו USB, root בלבד, `late_command` |
| `late-command.sh` | רץ **בתוך המתקין**: מעתיק את הקוד ל-`/opt/imagectl-src`, מתקין את היחידה, ורושם ל-`/etc/imagectl/` **עובדות** — הכרטיס ש-d-i הגדיר (‏`installer-nic`), התפקיד (‏`installer-role`), המניפסט (‏`iso-release.json`) |
| `firstboot.sh` + `imagectl-firstboot.service` | האתחול הראשון: בונה `initrd.img`/`initrd.img.gui`/`vmlinuz`, ואז מפעיל את אשף ה-HTTPS עם עובדות הכרטיס והתפקיד כברירות מחדל |
| `build-iso.sh` | ‏netinst רשמי (מאומת sha256) + pool + קוד (`git archive` של ה-ref) + preseed + תפריט → ISO hybrid ב-`xorriso -boot_image any replay`; מדפיס `-report_el_torito` |
| `test-iso.sh` | ‏QEMU/OVMF עם Secure Boot, שני שלבים (התקנה → אתחול ראשון) — **טרם הורץ** (אין KVM במעבדה) |

## בנייה (על דביאן 13 עם אינטרנט — Testrunner)

```bash
sudo tools/iso/make-pool.sh --out /srv/iso/repo                        # פעם אחת; ~600 קבצים, ~400MB
sudo apt-get install linux-image-amd64                                  # הקרנל שמודולי ה-initramfs נבנים מולו = זה שב-pool
sudo tools/build_initramfs.sh --installer --with-gui --output /srv/iso/live.img   # המתקין החי (#1190)
sudo tools/iso/build-iso.sh      --out /srv/iso/out --pool /srv/iso/repo --ref v0.53.0 --live-initrd /srv/iso/live.img
```

**סיסמת root אינה חלק מה-ISO** (‏#1632, הכרעת נדב 24/10): המפעיל בוחר אותה
במסך ההתקנה, במסך חשבון הניהול ליד סיסמת ה-admin (אותם כללים, שדה אימות),
והמנוע כותב ל-root את גיבוב ה-`$6$` שלה — בלי `chage -d 0`, כי היא כבר של
המפעיל. ‏`--root-password` ו-`IMAGECTL_ROOT_PASSWORD` **מסורבים** בהודעה
בשם, ו-`imagectl/root-password.hash` אינו נכתב עוד. התקנה בלי סיסמת root
מהמסך נכשלת בשם (`[invalid-root-password]` / `[root-password-missing]`) לפני
שנוגעים בדיסק.

הפלט: `imagectl-<tag>-amd64.iso`, ‏`SHA256SUMS`, ‏`*.el_torito.txt` (הראיה
ששני קטעי האתחול — BIOS ו-UEFI — נשמרו), ‏`imagectl-iso.json` (כולל
‏`live_kernel` ו-`live_initrd_sha256`). ‏build-iso עוצר אם גרסת הקרנל
ב-pool אינה זו של המודולים ב-initramfs, ואם תווית הכרך אינה `IMAGECTL_INSTALL`.

## מה קורה כשמאתחלים ממנו (#1190 — הכרעת נדב 21/09: "כמו ESXi")

1. ‏GRUB/isolinux עולים **ישר** (‏`timeout=0`) ל-`/live/vmlinuz` + `/live/initrd.img`
   עם `imagectl.mode=installer` — אותו initramfs של מחשבי הבנייה, עם
   ‏`installer/` (המנוע) והגואי. אין מתקין דביאן ואין preseed על ה-ISO.
2. ‏`agent/init` (‏`installer_boot.sh`): DHCP זמני על כל כרטיס עם קישור
   (תוצאה לכל כרטיס ב-`/run/imagectl/dhcp/<if>`), עיגון המדיה לפי התווית
   ב-`/cdrom`, ו-exec ל-`imagectl-installer` — שמעלה את הגואי על fb0. בלי
   מסך/מקלדת/מדיה: הודעה באנגלית על הקונסולה, המתנה — לעולם לא reboot.
3. הגואי (מסכי `docs/design/console-redesign/mockups/install-*`): דיסק
   (רשימה + אישור מחיקה) → תפקיד → רשת ניהול → שם → admin → סיכום →
   התקדמות. ‏`installer/gui-bridge.sh` מפעיל את המנוע `imagectl-install`:
   ‏GPT, debootstrap מ-`/cdrom`, ‏`packages.txt` מה-pool, ‏GRUB לשתי הקושחות,
   ‏`/etc/imagectl/answers` (+ `answers.secret` 0600), ‏`iso-release.json`,
   והמאגר המקומי `/var/lib/imagectl/apt-repo` — ‏`pool/` **כולו** ו-`dists/`
   (‏#1371: האינדקס מונה גם את חבילות ה-netinst; ההעתקה נבדקת — לכל `Filename`
   יש קובץ, אחרת ההתקנה נעצרת ב-`apt-repo-check`).
4. "הסר את מדיית ההתקנה והפעל מחדש" → eject + reboot.
5. אתחול ראשון: ‏`imagectl-firstboot` בונה את שני ה-initrd (שלב א'), ואז —
   כשיש `answers` — `firstboot-answers.sh` מריץ את `setup-boot-server.sh`
   מהתשובות (הסיסמה דרך FD 3 מ-`answers.secret`, שנמחק), מאמת את המטען,
   חותם, ומדליק את ה-DCUI על tty1. **בלי אשף.** בלי `answers` (שרת בלי
   מסך שהותקן אחרת) — המסלול הישן: DHCP זמני, DCUI טרום-התקנה, אשף ב-8081.

**מצבי האתחול הראשון** (`/etc/imagectl/firstboot.status`): ‏`wizard-running`
עד שהמפעיל מחיל; אחר כך `done` או מצב הכשל בשם. כרטיס חסר או MAC שלא נמצא
הם `network-nic-undecidable` ביומן ומחייבים בחירה באשף — **לא מנחשים**.
`check-error` הוא כשל בבדיקה עצמה. המתקין אינו רץ עד אישור המפעיל; כשל
בהחלה נשאר במסך 5 עם המצב המדויק ב-`firstboot.status`.

## מה נמדד (19/09, שרת המעבדה, QEMU 10.0 ב-TCG — אין KVM)

* ‏`xorriso -report_el_torito`: שני קטעי אתחול (BIOS `isolinux.bin`, ‏UEFI
  `efi.img`), ‏MBR/GPT/APM שוחזרו מה-netinst.
* ‏BIOS: התפריט עולה, "ImageSrv server install" מסומן, **אין טיימר**. שני
  דברים ב-netinst של דביאן 13 היו מונעים זאת ו-`build-iso.sh` מסיר אותם:
  ‏`gtk.cfg` נושא `menu default` משלו (האחרון מנצח), ו-`spkgtk.cfg` מפעיל
  **התקנה קולית אינטראקטיבית אחרי 30 שניות** בלי מגע (`timeout 300` +
  ‏`ontimeout`).
* ‏UEFI עם Secure Boot דלוק (‏`OVMF_CODE_4M.ms.fd`): ‏shim → GRUB → התפריט
  שלנו; בקרנל `Lockdown:` (= Secure Boot פעיל). ‏grub.cfg הערוך אינו שובר
  את השרשרת (R57).
* ‏`apt-cdrom add` (מה ש-d-i מריץ) על ה-ISO: 3 אינדקסים, ‏contrib נמצא,
  וכל 76 החבילות + ‏`standard` נפתרות מה-ISO **בלבד** (627 newly
  installed). ובתוך d-i אמיתי: הקרנל 6.12.107 מה-pool שלנו הותקן
  ‏`Get:1 cdrom://[...NETINST...]`.
* שתי שאלות שעצרו התקנה אמיתית גם ב-`priority=critical` ונוספו ל-preseed:
  ‏`apt-setup/cdrom/set-double` (41cdset מוצא את אותו דיסק שוב) ו-
  ‏`apt-setup/no_mirror` (netinst בלי mirror). אחריהן ‏pkgsel התקין מה-ISO.
* מלכודת שנתפסה במדידה: ‏`Release` שמונה רק `Packages.gz` — ‏apt מדלג על
  הרכיב **בשקט** ("Unable to locate package" על כל הרשימה). הפרוס נרשם גם.

## מה טרם אומת (19/09)

* **Hyper-V Gen2 + Secure Boot וברזל** — הבנייה במעבדה, ה-boot אצל נדב
  (תבנית "Microsoft UEFI Certificate Authority").
* **שרת משני**: המתקין מחייב `--primary-url`; בלי `imagectl.primary=<url>`
  בשורת האתחול האשף מציג שדה חובה ריק (`secondary-needs-primary`). האם
  המתקין צריך לקבל משני בלי primary (ה-pairing מהקונסולה) — הכרעת מוצר.
* ‏reproducibility — אותו ref + netinst + pool → אותו sha256: לא נמדד.
* ‏`test-iso.sh` — לא הורץ (הרצה ידנית מקבילה נעשתה ב-QEMU/TCG, ראה למעלה).
* ‏ISO שנבנה **אחרי** התיקונים של 19/09 (set-double/no_mirror, apt-repo,
  בלי openssh-server) — טרם הותקן מקצה לקצה; ההתקנה שנמדדה רצה על
  ‏ISO קודם, ושני המפתחות נענו ביד, וה-apt-repo הוזרק לדיסק ביד.

## הקשר לשתי התיקיות האחרות

‏`tools/iso/` **בונה**: היא אורזת את `installer/` (המנוע החי, רץ ב-initramfs
לפני שיש שרת) ואת עץ הקוד, כולל `install/` (הגדרת השרת המותקן —
‏`setup-boot-server.sh` והיחידות). ‏`firstboot.sh` שכאן הוא החוליה
ביניהן: הוא מפעיל את `install/setup-boot-server.sh` באתחול הראשון.

</div>
