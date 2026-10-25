# פלט lsblk אמיתי (#2102)

נאסף ב-24/10/2026 על ה-testrunner (`imagectl-testrunner`): Debian GNU/Linux 13
(trixie), ‏`lsblk from util-linux 2.41.5`, קרנל `6.12.113+deb13-cloud-amd64`.
הפלט נשמר כמו שהוא, בייט-בבייט מ-stdout (קריאה בלבד — לא שונה דבר בדיסקים).

| קובץ | הפקודה |
|---|---|
| `flat-sda-3parts.json` | `lsblk -J -o PATH,TYPE,PKNAME /dev/sda` |
| `flat-sdb-no-parts.json` | `lsblk -J -o PATH,TYPE,PKNAME /dev/sdb` (כונן בלי מחיצות) |
| `tree-sda-3parts.json` | `lsblk -J -o NAME,PATH,TYPE,PKNAME /dev/sda` |
| `flat-sda-no-pkname.json` | `lsblk -J -o PATH,TYPE /dev/sda` (הפקודה שלפני #2102) |

למה: בלי עמודת NAME, ‏lsblk 2.41 מחזיר רשימה **שטוחה**, ועם NAME — עץ עם
`children`. הטסטים של #1988 זייפו רק עץ, ולכן "הוסף כונן" נכשל על כל שרת
שהותקן מה-ISO. אותה צורה שטוחה נצפתה גם על VM מה-ISO (v0.76.0-rc1), ראו
גוף #2102.

**לא נאסף:** פלט מגרסת util-linux ישנה יותר (למשל 2.38 של דביאן 12). העץ
כאן הוא של 2.41 עם NAME.
