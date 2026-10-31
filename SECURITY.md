# מדיניות אבטחה

ImageSrv מריץ שרת אתחול רשת: הוא מגיש שרשרת אתחול חתומה, יכול לחלק
כתובות DHCP, וכותב לכוננים של כיתות שלמות. חולשה כאן אינה "באג באתר" —
היא גישה לכתיבה על מחשבים של אנשים אחרים.

שני ריפואים, שני משטחי חשיפה:

| ריפו | נראות | מה Issue שם עושה |
|---|---|---|
| `NadavOked/ImageSrv-dev` | **פרטי** | Issue אינו מפרסם דבר. זה מקום הדיווח. |
| `NadavOked/ImageSrv` | ציבורי | ענף, PR או Issue שם מפרסמים את החולשה לפני שיש תיקון. |

**דיווח על חולשה בארכיון הפרטי הוא Issue, והוא מותר ורצוי.** מה שאסור
הוא לפרסם אותה לציבורי לפני שיש תיקון.

---

## גרסאות נתמכות

הפרויקט מסומן ב-semver בתגים, נמצא לפני 1.0, ונפרס ב-`git checkout` של
תג נקי. אין ענפי תחזוקה ואין backport.

| גרסה | נתמכת | מה זה אומר |
|---|---|---|
| **התג האחרון** (`git describe --tags`; `v0.15.1` נכון ל-2026-08-30) | ✅ | תיקון אבטחה נכנס ל-`main` ומקבל תג חדש |
| כל תג ישן יותר | ❌ | אין תיקון בגרסה הישנה. השדרוג הוא checkout של התג האחרון |

מה שמחזיק את זה סביר: **המסלול קדימה קצר** — התגים יוצאים בקצב גבוה,
והפרש של כמה תגים אינו שדרוג גדול.

לפני 1.0, קפיצת minor עשויה לשנות התנהגות שהמפעיל רואה. הכלל המחייב
לגזירת גרסאות כתוב ב-`CLAUDE.md`.

---

## לאן מדווחים

**אם יש גישה ל-`NadavOked/ImageSrv-dev` (הריפו הפרטי):** Issue שם,
עם תווית `security`. הוא אינו מפרסם דבר. זה המסלול הרגיל.

**אם נראה רק `NadavOked/ImageSrv` (הציבורי):** אין גישה לארכיון. דיווח
דרך GitHub Security Advisories בלבד — לשונית **Security** ← **Report a
vulnerability**, או
<https://github.com/NadavOked/ImageSrv-dev/security/advisories/new>.
Issue או PR בציבורי מפרסם את החולשה לפני שיש תיקון.

**אין כתובת דוא"ל לאבטחה, ואין תוכנית תגמול.** הפרויקט מתוחזק על ידי
אדם אחד; ערוץ שאין מי שיאייש אותו לא היה מוסיף דבר.

**מה שאסור לכולם:** לפרסם לציבורי — ענף, PR, Issue או פוסט — לפני שיש
תיקון. `tools/git/publish-to-public.sh` מסרב ל-Issue/PR עם תווית
`security`; זה השומר האמיתי, לא איסור על Issue בפרטי.

## מתי Advisory בנוסף ל-Issue

Advisory נדרש כשהחולשה נוגעת **למי שאינו נדב**:

- תלמיד שכוננו נכתב.
- מפעיל במכללה אחרת שפורס את המוצר הציבורי.
- כל מי שיכול להגיע לשרת בלי להיות בעל הריפו.

חולשה שחיה רק בכלי פנימי שאינו יוצא לציבורי (סוכנים, CI, סקילים,
מעבדה) — Issue פרטי עם תווית `security` מספיק. אין משטח חשיפה חיצוני,
ואין למי לחשוף בתיאום.

בשני המקרים התווית `security` נשארת על ה-Issue, כדי שהפרסום לציבורי
ייעצר.

---

## מה כדאי שיהיה בדיווח

אותו מבנה שבו נכתבים הבאגים כאן, כי הוא זה שמאפשר לתקן:

- **התג או הקומיט** שנבדק (`git describe --tags`).
- **השורש** — הקובץ והשורה, עם ציטוט הקוד. לא רק "יש path traversal".
- **מה נשבר בפועל** — מה תוקף יכול לעשות, ובאיזו הרשאה הוא מתחיל
  (אנונימי? משתמש `deploy`? גישה לוילן ההפצה?).
- **שחזור** — הצעדים המדויקים, ומה נצפה מולם. הפרויקט מבחין בין
  "לא הצלחנו לבדוק" ל"בדקנו"; אם לא שוחזר בפועל — לכתוב זאת.
- **מה שאסור בתיקון**, אם ידוע — כיוון תיקון שייצור את הבאג ההפוך.

---

## מה בתחום

- **השרת** (`server/`) — אימות, הרשאות, חתימת עוגיות, העלאה וייבוא
  של אימג'ים, ה-API של הקונסולה.
- **הסוכן ושרשרת האתחול** (`agent/`, `boot/`) — מה שמריץ מכונה שעולה
  ברשת, ומה שנכתב לכונן שלה.
- **המתקין ויחידות ה-systemd** (`deploy/`) — הרשאות, `ReadWritePaths`,
  קבצים שנוצרים על השרת.

## מה לא בתחום

- **הפעלה שגויה בכוונה.** DHCP על רשת המכללה הוא ההגדרה המסוכנת ביותר
  במערכת. הוא כבוי כברירת מחדל ומאחורי כמה שכבות אישור, ותועד ככזה
  ב-`docs/server-install.md`. הדלקה מודעת שלו על הרשת הלא-נכונה היא
  תקלה תפעולית, לא חולשה.
- **פעולות הרסניות שמפעיל מורשה ביקש** (מחיקת אימג', עצירת סבב). הן
  מאחורי הקלדת שם בכוונה.
- **דיווחי סורק בלי השפעה מוכחת.** פלט של כלי הוא התחלה של דיווח, לא
  דיווח.
- **המעבדה של הפרויקט** ותשתיות בדיקה — הן אינן חלק ממה שנפרס.

---

## התראות Dependabot על תלויות מוצמדות

‏`server/requirements.txt` מצמיד `cryptography==43.0.0` ו-`pyOpenSSL==25.0.0`
במכוון: אלה גרסאות Debian 13, והשרת מקבל אותן מ-**apt**
(‏`python3-cryptography`, ‏`python3-openssl`), לא מ-pip. ‏Dependabot קורא את
**מחרוזת הגרסה** ולא את מה שנפרס, ולכן הוא מדווח על ‏`43.0.0` גם על תיקונים
ש-Debian עשה ב-backport (‏`43.0.0-3+deb13u1`). **אין מעלים את ההצמדה כדי
להשתיק התראה** — זה מנתק את ה-CI מהגרסה שנפרסת (#113; ‏PR 754/755
נסגרו מהסיבה הזו ב-13/09).

מצב ההתראות הפתוחות, מול security-tracker.debian.org (‏17/10/2026, נבדק שוב 22/10/2026 — ללא שינוי, #1975):

| התראה | על מה | trixie | ניתן לניצול אצלנו? |
|---|---|---|---|
| #3 CVE-2026-26007 | נקודות על עקומות SECT | **תוקן** (‏`43.0.0-3+deb13u1`) | לא — רק `SECP256R1` |
| #4 CVE-2026-27448 | `set_tlsext_servername_callback` | **תוקן** (‏`25.0.0-1+deb13u1`) | לא — אין callback כזה |
| #5 CVE-2026-27459 | `set_cookie_generate_callback` (DTLS) | **תוקן** (‏`25.0.0-1+deb13u1`) | לא — אין DTLS |
| #6 CVE-2026-34073 | name constraints ב-`x509.verification` | **vulnerable, no-DSA** | לא — אין `x509.verification` (#1639) |
| #9 CVE-2026-69249 | בניית שרשרת ב-`x509.verification` | **vulnerable, no-DSA** | לא — אין `x509.verification` (#1639) |
| — CVE-2026-69248 (אין התראה: GHSA מציין 45.0.0–48.0.0) | wildcard מול name constraints ב-`x509.verification` | **vulnerable, no-DSA** | לא — אין `x509.verification` |
| #1, #2, #7 | OpenSSL **בתוך wheels** של PyPI | לא רלוונטי | לא — חבילת apt מקשרת ל-`libssl3t64` של המערכת (בדיקה: `trixie-security` ‏`openssl 3.5.7-1~deb13u3`); ה-wheel רץ רק ב-CI ובפיתוח |

"אינו ניתן לניצול" אינו טענה שנשענים עליה בלי ראיה:
‏`tests/test_pinned_deps_unreachable_paths.py` סורק את `server/` ונופל אם נכנס
אליו אחד הנתיבים הפגיעים. אם הוא נופל — חוזרים לטבלה הזו ומחליטים, לא
מתקנים את הטסט.

**תיקון של Debian מגיע לשרת מותקן רק בתוך גרסה חתומה של ImageSrv** (הכרעת נדב
15/10, #1411) — לא דרך `apt` חי בשרת. עד שהמנגנון הזה קיים, "תוקן ב-trixie"
פירושו "תוקן במקור", לא "תוקן אצל המכללה".

### חולשות פתוחות ב-Debian 13 בכל מה שהשרת וה-initramfs מתקינים

‏Dependabot רואה רק את `server/requirements.txt`. השאר — חבילות המקור (כ-580 כשנספרו עם Recommends, לפני #1987)
ש-`apt` מתקין מ-`tools/iso/packages.txt` (בלי Recommends, #1987) — נבדק ב-
‏`tools/security-recheck.py` מול security-tracker.debian.org, על תג נתון,
על מכונת Debian 13 (ה-testrunner). **מריצים אותו על התג של כל גרסה לפני
השחרור** ומצרפים את התוצאה להערות השחרור (#1975):

```sh
# על ה-testrunner (Debian 13, ‏apt-get update טרי; בלי GitHub):
python3 -I tools/security-recheck.py --repo . --tag vX.Y.Z \
    [--baseline <הריצה הקודמת>.tsv] > security-vX.Y.Z.tsv
# בתחנת הפיתוח (‏gh מחובר, בלי apt):
python tools/security-recheck.py --dependabot-only
```

הכלי מדפיס **כל** CVE שלא תוקן ב-trixie; רובן בחבילות שאינן נגישות מהרשת
(הקרנל לבדו כ-740, ארגז הכלים, ‏QEMU). ההכרעה "נגיש אצלנו?" אינה בכלי —
היא נכתבת ב-#1975, והנתיבים שנקבע שאינם נגישים נעולים בטסט
(‏`tests/test_pinned_deps_unreachable_paths.py`: ‏python-multipart, ‏h2/hpack,
‏anyio, ‏python3.13, ו-applets של busybox בסוכן). ב-22/10 נמצאו שתיים
עם נתיב בקוד שלנו, שתיהן Low: ‏busybox ‏udhcpc ‏CVE-2026-88833 (‏DoS מוילן
ההפצה, #1980) ו-open-iscsi ‏CVE-2026-44943 (‏discovery מול יעד מתחזה, #1983).

---

## מה קורה אחרי הדיווח

בעלים אחד, מאמץ סביר, **בלי SLA**. אין הבטחה לזמן תגובה, כי הבטחה כזו
לא תיאמת. מה שכן:

- כל דיווח נענה, גם כשהתשובה היא "זו לא חולשה" עם הסבר.
- תיקון נכנס ל-`main` ומקבל **תג חדש**; ה-Advisory מפורסם אחריו, עם
  קרדיט למדווח אלא אם ביקש אחרת.
- אם התיקון נוגע בהתנהגות שמפעיל רואה, הוא נכתב גם ב-`CHANGELOG.md`.

---

## Reporting a vulnerability (English)

ImageSrv is a network boot and disk-imaging server for a college lab.

Two repositories, two disclosure surfaces:

- `NadavOked/ImageSrv-dev` is **private**. An issue there does not
  publish anything. That is the working repo — file the issue there, with
  the `security` label.
- `NadavOked/ImageSrv` is public. A branch, PR, or issue there discloses
  the weakness before a fix exists. Do not file there.

If you can only see the public repo, report through GitHub Security
Advisories:
<https://github.com/NadavOked/ImageSrv-dev/security/advisories/new>

A Security Advisory is **also** required when the weakness affects someone
other than the maintainer (a student whose disk is written, another college
deploying the public product). A weakness only in private-only tooling
(agents, CI, lab) that never ships publicly needs the private issue and
label, not an Advisory.

`publish-to-public.sh` refuses any issue or PR labelled `security`. That is
the real guard — publishing is what discloses, not the private issue.

Only the latest tag is supported; fixes land on `main` and get a new tag.
There is no security email address and no bounty — this project has a single
maintainer, and responses are best effort with no SLA. Please include the tag
you tested, the file and line, what an attacker gains and from which
privilege level, and the exact steps you ran. If you did not actually
reproduce it, say so.
