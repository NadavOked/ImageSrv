<div dir="rtl">

# `deploy/` — התקנה והגדרה של **שרת** ImageSrv על דביאן

מה שרץ **על השרת המותקן**: ‏`setup-boot-server.sh` (מתקין את הקוד, ‏dnsmasq,
‏TFTP/HTTP ויחידות systemd), יחידות `imagesrv-*.service`/`.timer`
(שרת, פרוקסי, אשף, ‏DCUI, שחזור רשת), כללי חומת האש
(`nftables-rules.sh`, ‏`firewall-rules.md`), ‏`verify-boot-payload.sh`
ו-`console-live.sh` (האם הקונסולה עונה אחרי התקנה/שדרוג).

**מה היא לא:**
- לא המתקין החי של ה-ISO — זה `installer/` (רץ מה-initramfs, לפני שיש שרת).
- לא בניית ה-ISO — זה `tools/iso/`.
- לא קוד הסוכן שרץ על תחנות (`agent/`) ולא קוד השרת עצמו (`server/`).

**איך שלוש התיקיות מתחברות:** ‏`tools/iso/build-iso.sh` בונה ISO שמכיל את
`installer/` (המנוע) ואת עץ הקוד, כולל תיקייה זו. המנוע מעתיק את הקוד
ל-`/opt/imagesrv-src` והאתחול הראשון (`tools/iso/firstboot.sh`) מפעיל מכאן את
`setup-boot-server.sh` ואת יחידות השרת. אפשר להריץ אותו גם ידנית על דביאן
קיימת, בלי ISO.

</div>
