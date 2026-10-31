#!/usr/bin/env bash
# console-live.sh — האם השרת עונה? (‏#1131)
#
#   bash deploy/console-live.sh <console-url> [<version-expected>] [<seconds>] [<cert.pem>]
#
# שואל `GET <url>/api/console/health/live` (ללא הזדהות, על loopback) עד
# <seconds> (ברירת מחדל 60), ויוצא 0 **רק** על 200 עם `{"ok": true}` — ואם
# ניתנה גרסה, רק כשהשרת מדווח בדיוק אותה (אחרי שדרוג: ראיה חיובית שהקוד
# החדש הוא זה שרץ, לא היחידה הישנה שעדיין עונה). כל דבר אחר — יציאה 1
# עם התשובה/השגיאה האחרונה ב-stderr. מדפיס את הגרסה שדווחה ל-stdout.
#
# ‏<cert.pem> = תעודת הקונסולה (חתומה-עצמית, #703) — כשניתנת, היא ה-CA
# היחיד שמאומת (pinning); בלעדיה — בלי אימות, על loopback בלבד.
# משתמשים בו: המתקין (לפני חומת האש) ו-tools/server-upgrade.sh (אחרי restart).
# python3 ולא curl: curl אינו מובטח על דביאן מינימלי; python3 כן (השרת בו).
set -euo pipefail

[[ $# -ge 1 ]] || { echo "usage: console-live.sh <console-url> [<version>] [<seconds>] [<cert.pem>]" >&2; exit 2; }
URL="${1%/}"
WANT="${2:-}"
WAIT="${3:-60}"
CERT="${4:-}"

python3 - "$URL" "$WANT" "$WAIT" "$CERT" <<'PYEOF'
import json, ssl, sys, time, urllib.request

url, want, wait, cert = (sys.argv[1] + "/api/console/health/live", sys.argv[2],
                         float(sys.argv[3]), sys.argv[4])
def context():
    # ‏#1417: נבנה מחדש בכל ניסיון — השרת עשוי להחליף את התעודה בעלייה
    # הראשונה (#1380), והבודק חייב להצמיד את הקובץ **כפי שהוא עכשיו**.
    ctx = ssl.create_default_context(cafile=cert or None)
    # השם בתעודה הוא כרטיס השרתים/שם המארח, לא 127.0.0.1 — מאמתים את התעודה
    # (pinned) ולא את השם. בלי cert: loopback בלבד, בלי אימות.
    ctx.check_hostname = False
    if not cert:
        ctx.verify_mode = ssl.CERT_NONE
    return ctx
deadline = time.monotonic() + wait
last = "לא נשלחה אף בקשה"
while True:
    try:
        with urllib.request.urlopen(url, timeout=3, context=context()) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            version = body.get("version") or ""
            if resp.status == 200 and body.get("ok") is True and (not want or version == want):
                print(version)
                sys.exit(0)
            last = f"HTTP {resp.status} {body}" + (f" (ציפינו לגרסה {want})" if want else "")
    except Exception as exc:                                                # noqa: BLE001
        last = f"{type(exc).__name__}: {exc}"
    if time.monotonic() >= deadline:
        sys.exit(f"{url}: השרת לא ענה כנדרש תוך {wait:g} ש' — {last}")
    time.sleep(1)
PYEOF
