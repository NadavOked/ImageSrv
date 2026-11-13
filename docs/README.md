<div dir="rtl">

# התיעוד של ImageSrv

דף כניסה לתיקייה `docs/`. שורה אחת לכל מסמך מרכזי.

## ארכיטקטורה (`architecture/`)

| מסמך | מה יש בו |
|---|---|
| [imagesrv-spec.md](architecture/imagesrv-spec.md) | אפיון המערכת: מה היא עושה, למה, ובאילו אילוצים |
| [interfaces.md](architecture/interfaces.md) | החוזה בין הרכיבים (API, מניפסט, משימות). מקור האמת למה שעובר ביניהם |
| [server.md](architecture/server.md) | השרת והקונסולה, כולל הפעלה כשירות |
| [agent.md](architecture/agent.md) | הסוכן שרץ על המחשבים: החלטות, שחזור, קליטה, חדר שיכפולים |
| [grub-generator.md](architecture/grub-generator.md) | מחולל תפריט האתחול (GRUB) |
| [security-model.md](architecture/security-model.md) | מודל האמון: מה מאומת באתחול ובמשימה, ומה לא |

## מדריכים (`guides/`)

| מסמך | מה יש בו |
|---|---|
| [server-install.md](guides/server-install.md) | התקנת שרת |
| [admin-guide-he.md](guides/admin-guide-he.md) | מדריך למנהל ה-IT: חומת אש, מתגים ומכונה וירטואלית ([PDF](guides/admin-guide-he.pdf)) |
| [admin-guide-sources.md](guides/admin-guide-sources.md) | מאיפה בקוד נלקח כל כלל במדריך המנהל |
| [switch-checklist-cisco.md](guides/switch-checklist-cisco.md) | רשימת בדיקה לסוויץ' Cisco בוילן ההפצה |
| [local-clone.md](guides/local-clone.md) | שיכפול מקומי, דיסק לדיסק, והתקן ה-USB |
| [driver-packages.md](guides/driver-packages.md) | חבילות דרייברים |
| [vm-update.md](guides/vm-update.md) | עדכון אימג' Windows בתוך מכונה וירטואלית על השרת |
| [email-alerts.md](guides/email-alerts.md) | התראות במייל: הגדרת שליחה ו-OAuth |

## מדידות (`measurements/`)

| מסמך | מה יש בו |
|---|---|
| [measurements/README.md](measurements/README.md) | זמני וקצבי הפצה שנמדדו, עם מקור וסביבה לכל מספר |
| [distribution.csv](measurements/distribution.csv) | הנתונים הגולמיים של אותן מדידות, שורה לכל מדידה |

## תרשימים ומיתוג

| קובץ | מה יש בו |
|---|---|
| [architecture-v1.svg](architecture-v1.svg) | תרשים הארכיטקטורה ([גרסה כהה](architecture-v1-dark.svg)) |
| [flow-v1.svg](flow-v1.svg) | תרשים הזרימות ([גרסה כהה](flow-v1-dark.svg)) |
| [usb-clone.svg](usb-clone.svg) | השיכפול מהתקן USB ([גרסה כהה](usb-clone-dark.svg)) |
| [brand/](brand/) | הלוגו, בגרסה בהירה וכהה |

## עיצוב (`design/`)

| מסמך | מה יש בו |
|---|---|
| [native-gui-mockup-2026-09-18.html](design/native-gui-mockup-2026-09-18.html) | המוקאפ המאושר של הממשק הנייטיבי, שהטסטים בודקים מולו את הצבעים |
| [native-gui-mockup-2026-09-13.html](design/native-gui-mockup-2026-09-13.html) | המוקאפ הקודם של אותו ממשק |

דיווח על חולשת אבטחה: [SECURITY.md](../SECURITY.md).

</div>
