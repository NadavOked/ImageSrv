<div dir="rtl">

# `deploy/` — הגדרת **שרת** ImageSrv המותקן

מה שרץ **על השרת המותקן**: ‏`setup-boot-server.sh` (מגדיר את הקוד, ‏dnsmasq,
‏TFTP/HTTP ויחידות systemd), יחידות `imagesrv-*.service`/`.timer`
(שרת, פרוקסי, ‏DCUI, שחזור רשת), כללי חומת האש
(`nftables-rules.sh`, ‏`firewall-rules.md`), ‏`verify-boot-payload.sh`
ו-`console-live.sh` (האם הקונסולה עונה אחרי התקנה/שדרוג).

**מה היא לא:**
- לא המתקין החי של ה-ISO — זה `installer/` (רץ מה-initramfs, לפני שיש שרת).
- לא בניית ה-ISO — זה `tools/iso/`.
- לא קוד הסוכן שרץ על תחנות (`agent/`) ולא קוד השרת עצמו (`server/`).

**איך שלוש התיקיות מתחברות:** ‏`tools/iso/build-iso.sh` בונה ISO שמכיל את
`installer/` (המנוע) ואת עץ הקוד, כולל תיקייה זו. המנוע מעתיק את הקוד
ל-`/opt/imagesrv-src` והאתחול הראשון (`tools/iso/firstboot.sh`) מפעיל מכאן את
`setup-boot-server.sh` ואת יחידות השרת. זו אינה דרך התקנה בפני עצמה:
**שרת מותקן רק מה-ISO או מה-USB**, ולא על דביאן קיימת ולא דרך הרשת.

</div>
