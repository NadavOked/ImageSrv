<div dir="rtl">

# התראות במייל — הגדרה, ו-OAuth למכללה (#1751)

ממשק ה-API מתועד ב-[`interfaces.md` סעיף 42](interfaces.md). המסמך הזה מיועד לנדב ולמי שמגדיר את השרת. הוא מסביר איך בוחרים דרך שליחה, ומה המכללה צריכה להכין כדי ש"התחבר עם Microsoft" יעבוד.

> **מה כאן נבדק ומה לא.** המנגנון נבדק מול שרת SMTP מזויף ומול ספק זהות מזויף (`tests/test_email_alerts_1751.py`). **לא נשלח אף מייל אמיתי, ולא נעשתה אף פנייה אמיתית ל-Microsoft או ל-Google.** כל מה שכתוב כאן על התנהגות הספקים מבוסס על התיעוד שלהם כפי שאני מכיר אותו, ולא על מדידה. לפני הפעלה במכללה צריך לבדוק את זה מול החשבון האמיתי.

## איזו דרך שליחה לבחור

| הספק במסך | מתי מתאים | מה צריך |
|---|---|---|
| **Office 365** | תיבת דואר אמיתית ב-Microsoft 365 שולחת את ההתראות | אפליקציה רשומה ב-Azure ו-"התחבר עם Microsoft" (למטה), **או** סיסמה, אם המכללה עדיין מתירה SMTP AUTH בסיסמה |
| **Office 365 Direct Send** | שליחה רק לנמענים **בתוך** הדומיין של המכללה, בלי תיבה ובלי סיסמה | פורט 25 יוצא מהשרת ל-`<domain>.mail.protection.outlook.com`. בדרך כלל גם כתובת ה-IP הציבורית של המכללה ברשומת ה-SPF |
| **Exchange מקומי / relay פנימי** | יש במכללה שרת דואר פנימי שמעביר מיילים מכתובת השרת | כתובת ה-relay, וש-relay יתיר את כתובת ה-IP של השרת |
| **Gmail** | חשבון Google שולח | ‏App Password (סיסמת אפליקציה, דורשת אימות דו-שלבי). ‏OAuth — ראו "Google" למטה |
| **SMTP מותאם** | כל שרת אחר | מארח, פורט, הצפנה, ואם צריך גם משתמש וסיסמה |

**השאלה הפתוחה מ-#1751 עדיין פתוחה:** איזה שרת דואר יש במכללה. ה-relay הפנימי הוא הדרך הפשוטה ביותר, כי אין בה OAuth ואין בה סודות. Direct Send הוא השני בפשטות.

## "התחבר עם Microsoft" — מה המכללה צריכה לעשות

השרת משתמש ב-**device code**: הוא מבקש קוד מ-Microsoft, והקונסולה מציגה קוד וכתובת (`https://microsoft.com/devicelogin`). המפעיל פותח את הכתובת **בדפדפן שלו**, מקליד את הקוד ומאשר. בסוף השרת שומר refresh token (קובץ 0600) ושולח איתו ב-`AUTH XOAUTH2`. אין צורך בכתובת חזרה (redirect) לשרת, והדפדפן לא צריך להגיע לשרת.

### ‏client id: המכללה רושמת אפליקציה

**אין "client id ציבורי" שמותר לנו להשתמש בו.** קיימים מזהים של אפליקציות אחרות שרשמו את עצמן אצל Microsoft (למשל של לקוחות דואר מוכרים), אבל שימוש בהם הוא התחזות לאפליקציה אחרת. הוא מפר את תנאי השימוש, ו-Microsoft או מנהל ה-tenant יכולים לחסום אותו בכל רגע. לכן **השרת לא כולל מזהה כזה**, ושדה ה-client id ריק עד שמזינים מזהה.

ב-Azure (‏Microsoft Entra admin center), מנהל ה-tenant של המכללה צריך לבצע:
1. **App registrations → New registration.** שם לבחירתכם, למשל "ImageSrv alerts". ‏Supported account types: "Accounts in this organizational directory only".
2. **Authentication → Advanced settings → Allow public client flows: Yes.** בלעדיו device code נדחה.
3. **API permissions → Add a permission → APIs my organization uses → Office 365 Exchange Online → Delegated → `SMTP.Send`**, ואז "Grant admin consent".
4. מעתיקים את **Application (client) ID** ואת **Directory (tenant) ID** לשדות החשבון בקונסולה. ‏tenant ריק = `organizations`.
5. בתיבת הדואר ששולחת חייב להיות **SMTP AUTH מופעל**, גם כשמתחברים ב-OAuth. לפי מה שאני מכיר, זה נעשה ב-Exchange admin center → Mail flow, או ב-PowerShell: `Set-CASMailbox <mailbox> -SmtpClientAuthenticationDisabled $false`.

> לפי מה שידוע לי, Microsoft מוציאה משימוש את **SMTP AUTH בסיסמה** (Basic auth) ב-Exchange Online, בשלבים במהלך 2026. אם זה כבר הושלם ב-tenant של המכללה, ‏"Office 365 עם סיסמה" ייכשל בקוד 535, ו-OAuth הוא הדרך היחידה. צריך לבדוק את הסטטוס בפועל ב-tenant.

### איפה זה ייחסם אם לשרת אין יציאה לאינטרנט

הקונסולה רצה על רשת הניהול. השלבים, לפי הסדר:

| שלב | מי פונה | לאן | מה קורה בלי יציאה |
|---|---|---|---|
| 1. "התחבר" — בקשת קוד | **השרת** | `https://login.microsoftonline.com` (443) | **נכשל כאן, מיד.** הקונסולה מציגה "אין חיבור ל-login.microsoftonline.com", ונרשם ביומן `email_oauth_failed` |
| 2. אישור הקוד | **הדפדפן של המפעיל** | `https://microsoft.com/devicelogin` | לא תלוי בשרת. עובד מכל מחשב שיש לו אינטרנט, גם טלפון |
| 3. המתנה לאישור | **השרת** | `login.microsoftonline.com` (443) | נכשל כמו שלב 1 |
| 4. כל שליחה: חידוש האסימון | **השרת** | `login.microsoftonline.com` (443) | המייל נשאר בתור עם ניסיונות חוזרים, והסיבה גלויה בהיסטוריה |
| 5. כל שליחה: SMTP | **השרת** | `smtp.office365.com:587` | אותו דבר |

**סיכום:** ל-OAuth צריך HTTPS יוצא מהשרת ל-`login.microsoftonline.com` ו-SMTP יוצא ל-`smtp.office365.com:587`, **תמיד**. זה לא חד-פעמי, כי האסימון מתחדש בכל שליחה. חומת האש של השרת עצמו לא חוסמת את זה (`chain output` הוא `policy accept`). חומת האש של המכללה לא נבדקה. כש-relay פנימי זמין, הוא לא צריך אינטרנט בכלל.

## ‏Google (Gmail)

- **App Password** (ספק "Gmail", אימות בסיסמה) עובד היום, בתנאי שבחשבון מופעל אימות דו-שלבי.
- **OAuth עם device code — צפוי להיכשל.** לפי התיעוד של Google כפי שאני מכיר אותו, device code ("TVs and Limited Input devices") מתיר רק רשימה מצומצמת של scopes, ו-`https://mail.google.com/`, שנדרש ל-SMTP, לא נמצא בה. המנגנון בשרת אחיד לשני הספקים, ולכן אם Google תסרב, התשובה שלה (למשל `invalid_scope`) תוצג כלשונה. **לא בדקתי מול Google אמיתי.** כדי לבדוק צריך client id מסוג "TVs and Limited Input devices" ו-client secret. ה-client secret נשמר אצלנו בקובץ 0600.
- אם Google אכן מסרבת, האפשרות הבאה היא זרימת "authorization code" עם הדבקה ידנית של הכתובת שחזרה. היא **לא** בנויה; היא החלטה לנדב.

## מה נשמר איפה

- **ההגדרות** (חשבונות וכללים): בטבלת `settings`, במפתח `email_alerts`. בלי סודות.
- **הסודות**: בתיקייה `<data_dir>/email-accounts/`. הסיסמה בקובץ `<id>.password`, ו-refresh token ו-client secret בקובץ `<id>.oauth`. כולם ב-0600. גיבוי ההגדרות (#1128) כולל אותם, כמו את שאר הסודות שב-`data_dir`.
- **שינוי יעד:** אם משנים ספק, מארח, פורט, הצפנה, אימות, משתמש או אפליקציה **בלי** להזין סיסמה באותה פעולה, הסודות נמחקים. כך הם לא נשלחים לשרת אחר.

</div>
