<div dir="rtl">

# מדיניות אבטחה

ImageSrv מריץ שרת אתחול רשת: הוא מגיש שרשרת אתחול חתומה, יכול לחלק
כתובות DHCP, וכותב לכוננים של כיתות שלמות. חולשה כאן אינה "באג באתר":
היא גישה לכתיבה על מחשבים של אנשים אחרים.

## איך מדווחים

**חולשת אבטחה אינה Issue ואינה Pull Request.** Issue ציבורי מפרסם את
החולשה לפני שיש תיקון. מדווחים בפרטיות דרך GitHub Security Advisories:
לשונית **Security** ← **Report a vulnerability**, או
<https://github.com/NadavOked/ImageSrv/security/advisories/new>.

אין כתובת דוא"ל לאבטחה, ואין תוכנית תגמול. הפרויקט מתוחזק על ידי אדם
אחד; ערוץ שאין מי שיאייש אותו לא היה מוסיף דבר.

## גרסאות נתמכות

הפרויקט מסומן ב-semver בתגים ונמצא לפני 1.0. **רק התג האחרון** (דף
ה-Releases) נתמך: תיקון אבטחה נכנס ל-`main` ומקבל תג חדש. אין ענפי
תחזוקה ואין backport, והשדרוג הוא checkout של התג האחרון.

## מה כדאי שיהיה בדיווח

- **התג או הקומיט** שנבדק (`git describe --tags`).
- **השורש**: הקובץ והשורה, עם ציטוט הקוד. לא רק "יש path traversal".
- **מה נשבר בפועל**: מה תוקף יכול לעשות, ובאיזו הרשאה הוא מתחיל (אנונימי?
  משתמש `deploy`? גישה לוילן ההפצה?).
- **שחזור**: הצעדים המדויקים, ומה נצפה מולם. אם לא שוחזר בפועל, כותבים זאת.
- **מה שאסור בתיקון**, אם ידוע: כיוון תיקון שייצור את הבאג ההפוך.

## מה בתחום

- **השרת** (`server/`): אימות, הרשאות, חתימת עוגיות, העלאה וייבוא של
  אימג'ים, ה-API של הקונסולה.
- **הסוכן ושרשרת האתחול** (`agent/`, `boot/`): מה שמריץ מכונה שעולה ברשת,
  ומה שנכתב לכונן שלה.
- **המתקין ויחידות ה-systemd** (`deploy/`): הרשאות, `ReadWritePaths`,
  קבצים שנוצרים על השרת.

## מה לא בתחום

- **הפעלה שגויה בכוונה.** DHCP על רשת המכללה הוא ההגדרה המסוכנת ביותר
  במערכת. הוא כבוי כברירת מחדל ומאחורי כמה שכבות אישור, ותועד ככזה
  ב-`docs/guides/server-install.md`. הדלקה מודעת שלו על הרשת הלא-נכונה היא
  תקלה תפעולית, לא חולשה.
- **פעולות הרסניות שמפעיל מורשה ביקש** (מחיקת אימג', עצירת סבב). הן
  מאחורי אישור מפורש בממשק, בכוונה.
- **דיווחי סורק בלי השפעה מוכחת.** פלט של כלי הוא התחלה של דיווח, לא דיווח.

## מה קורה אחרי הדיווח

בעלים אחד, מאמץ סביר, **בלי SLA**. נשתדל לענות לכל דיווח, גם כשהתשובה היא
"זו לא חולשה" עם הסבר. תיקון נכנס ל-`main` ומקבל תג חדש. ה-Advisory מפורסם
ביום הגרסה, עם הגרסאות המושפעות, הגרסה המתוקנת והחומרה, וקרדיט למדווח אלא
אם ביקש אחרת. אם התיקון נוגע בהתנהגות שמפעיל
רואה, הוא נכתב גם ב-`CHANGELOG.md`.

---

## Reporting a vulnerability (English)

ImageSrv is a network boot and disk-imaging server for a college lab.

**Do not report a vulnerability in an issue or a pull request.** A public
issue discloses the weakness before a fix exists. Report it privately through
GitHub Security Advisories (**Security** tab, **Report a vulnerability**):
<https://github.com/NadavOked/ImageSrv/security/advisories/new>

Only the latest tag is supported; fixes land on `main` and get a new tag.
There is no security email address and no bounty: this project has a single
maintainer, and responses are best effort with no SLA. Please include the tag
you tested, the file and line, what an attacker gains and from which
privilege level, and the exact steps you ran. If you did not actually
reproduce it, say so.

In scope: the server (`server/`), the agent and boot chain (`agent/`, `boot/`),
and the installer and systemd units (`deploy/`). Out of scope: deliberate
misconfiguration (for example turning DHCP on in the wrong network),
destructive actions an authorized operator asked for, and scanner output
without demonstrated impact.

</div>
