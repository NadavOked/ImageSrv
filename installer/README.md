<div dir="rtl">

# `installer/` — **מנוע** ההתקנה החי של ה-ISO (#1190)

מה שרץ **בתוך ה-initramfs של ה-ISO**, לפני שקיים שרת: ‏`imagesrv-installer`
(תהליך הקונסולה — מעלה את הגואי הנייטיבי על המסך), ‏`gui-bridge.sh` (הגשר
שדרכו הגואי מפעיל את המנוע ושולף ממנו מצב), ‏`imagesrv-install` (המנוע
עצמו) ו-`lib/` (‏`disks`, ‏`answers`, ‏`bootstrap`, ‏`finish`, ‏`progress`).
POSIX sh בלבד (busybox ash; המנוע עושה `exec` ל-dash כשהוא קיים).

**מה היא לא:**
- לא ההגדרה של השרת אחרי ההתקנה — זה `deploy/`.
- לא בניית ה-ISO והחבילות — זה `tools/iso/`.
- לא הגואי עצמו — הוא נבנה לתוך ה-initramfs (`tools/build_initramfs.sh
  --installer --with-gui`).

**איך שלוש התיקיות מתחברות:** ‏`tools/iso/build-iso.sh` אורז את התיקייה הזו
ל-ISO; בעלייה היא מתקינה דביאן מה-pool שעל המדיה על הדיסק שנבחר, ומעתיקה
אליו את הקוד. מכאן ההגדרה היא של `deploy/setup-boot-server.sh`, שרץ באתחול
הראשון של המערכת המותקנת.

</div>
