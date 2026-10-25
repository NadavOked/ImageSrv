#!/usr/bin/env bash
#
# ImageCtl — האם תחנה יכולה בכלל לעלות דרך השרת הזה.
#
# ‏boot/grub_menu.py מפנה **כל** תחנה שיש לה משימה אל /boot/vmlinuz
# ו-/boot/initrd.img. בהתקנה נקייה מאפס (#332) שניהם החזירו 404 בזמן
# שהמתקין יצא 0 והדפיס "מוכן." — שרת מוכן שאף מחשב לא עולה דרכו.
#
# הראיה כאן **חיובית ומורכבת**: הקובץ קיים על הדיסק, **וגם** השרת מחזיר
# ‏200, **וגם** הגודל המוצהר אינו יכול להיות גוף שגיאה — גוף ה-404 של
# ‏/boot הוא תשעה בייטים, ולכן "יש תשובה ויש גודל" אינו "יש קובץ".
# ו"השרת לא ענה" אינו 404 ואינו 200: זו בדיקה שלא רצה, וזה כישלון
# (‏CLAUDE.md, עיקרון 5).
#
#   sudo bash install/verify-boot-payload.sh --server-url http://10.44.12.10:8080
#
# מידות הקרנל וה-initrd הטקסטואלי מגיעות מ-`server/health.py`: מסך הבריאות
# והמתקין חייבים לומר עליהם אותו דבר. ה-initrd הגרפי נשאר לא-חוסם במסך
# הבריאות, אבל כלי הטכנאי בודק אותו ברצפה נפרדת כאשר הוא קיים (#1161).

set -euo pipefail

HTTP_ROOT="/srv/imagectl/boot"
SERVER_URL=""
WAIT=30
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    cat <<'EOF'
ImageSrv — בדיקה שהשרת מגיש את הקרנל ואת ה-initrd.

  sudo bash install/verify-boot-payload.sh --server-url http://<כתובת>:8080

  --server-url URL     חובה — הכתובת שהתחנות פונות אליה (זו שב-grub.cfg)
  --http-root PATH     תיקיית האתחול על הדיסק (ברירת מחדל /srv/imagectl/boot)
  --app-dir PATH       שורש הקוד (ברירת מחדל: התיקייה שמעל הסקריפט)
  --wait SECONDS       כמה להמתין לשרת שזה עתה הורם (ברירת מחדל 30)
  -h, --help           המסך הזה

יציאה 0 = תחנה יכולה לעלות. כל דבר אחר = לא, והסיבה נאמרת בשמה.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --http-root)  HTTP_ROOT="${2:?}"; shift 2 ;;
        --server-url) SERVER_URL="${2:?}"; shift 2 ;;
        --app-dir)    APP_DIR="${2:?}"; shift 2 ;;
        --wait)       WAIT="${2:?}"; shift 2 ;;
        -h|--help)    usage; exit 0 ;;
        *) printf 'unknown option: %s  (try --help)\n' "$1" >&2; exit 2 ;;
    esac
done

if [[ -z "$SERVER_URL" ]]; then
    printf '%s\n' "חסר --server-url. בלי הכתובת שהתחנות פונות אליה אין מה לבדוק." >&2
    exit 2
fi

check() {
    python3 - "$APP_DIR" "$HTTP_ROOT" "$SERVER_URL" "$WAIT" <<'PYEOF'
import sys
import time
from pathlib import Path

app_dir, root, base, wait = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
base = base.rstrip("/")

# ‏sudo מגיע לא פעם עם LC_ALL=C, ואז הדפסת עברית זורקת UnicodeEncodeError
# ויציאה שאינה 0 — כשל בהדפסה שנראה בדיוק כמו שער שסירב. הודעה מעוותת
# עדיפה על שער שמדווח את הסיבה הלא נכונה.
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(errors="backslashreplace")

sys.path.insert(0, app_dir)
from server import health  # noqa: E402

_size_of = health.default_hooks()["http_size"]
_seen = {}


def probe(url):
    """מודדים פעם אחת לכל כתובת — הראיה שמודפסת והשיפוט הם אותה מדידה."""
    if url not in _seen:
        _seen[url] = _size_of(url)
    return _seen[url]


# המתנה מוגבלת לשרת שזה עתה הורם. היא **אינה** בולעת את התוצאה: אם הוא
# לא ענה עד הסוף, `boot_asset_problems` אומר "הבדיקה עצמה לא רצה" וזה
# נספר ככישלון — לא כ"בסדר".
deadline = time.monotonic() + float(wait)
while time.monotonic() < deadline:
    if _size_of(f"{base}/boot/{health.BOOT_ASSETS[0]}")[0] is not None:
        break
    time.sleep(1)

problems = []
for name in health.BOOT_ASSETS:
    path = root / name
    disk = path.stat().st_size if path.is_file() else None
    status, served = probe(f"{base}/boot/{name}")
    print(f"  {name}: דיסק={disk if disk is not None else 'אין'}"
          f" · HTTP={status if status is not None else 'לא ענה'}"
          f" · מוגש={served if served is not None else 'לא הוצהר'}")
    if disk is None:
        problems.append(f"{name}: אינו קיים ב-{root}")
    elif disk < health.MIN_ASSET_BYTES:
        problems.append(
            f"{name}: {disk} בייטים בלבד על הדיסק, מתחת ל-"
            f"{health.MIN_ASSET_BYTES} — אינו קובץ אתחול")

problems += health.boot_asset_problems(probe, base)

# The GUI initramfs is optional on a server built without --with-gui, and the
# server deliberately falls back to the text image in that case.  If it is
# present, build/cloner roles use it, so verify it with its own realistic floor.
gui_name = "initrd.img.gui"
GUI_MIN_ASSET_BYTES = 30 << 20
gui_path = root / gui_name
if not gui_path.is_file():
    print(f"  {gui_name}: absent (server built without --with-gui)")
else:
    disk = gui_path.stat().st_size
    status, served = probe(f"{base}/boot/{gui_name}")
    print(f"  {gui_name}: דיסק={disk}"
          f" · HTTP={status if status is not None else 'לא ענה'}"
          f" · מוגש={served if served is not None else 'לא הוצהר'}")
    if disk < GUI_MIN_ASSET_BYTES:
        problems.append(
            f"{gui_name}: {disk} בייטים בלבד על הדיסק, מתחת ל-{GUI_MIN_ASSET_BYTES}"
            " — אינו initramfs גרפי")
    if status is None:
        problems.append(f"{gui_name}: הבדיקה עצמה לא רצה (השרת לא ענה)")
    elif status != 200:
        problems.append(f"{gui_name}: {status} מ-/boot")
    elif served is None:
        problems.append(f"{gui_name}: 200 בלי גודל מוצהר — הבדיקה לא רצה")
    elif served < GUI_MIN_ASSET_BYTES:
        problems.append(
            f"{gui_name}: {served} בייטים בלבד הוגשו, מתחת ל-{GUI_MIN_ASSET_BYTES}"
            " — אינו initramfs גרפי")

for line in problems:
    print(f"  - {line}", file=sys.stderr)
sys.exit(1 if problems else 0)
PYEOF
}

rc=0
check || rc=$?

if (( rc == 0 )); then
    printf '%s\n' "תחנה יכולה לעלות דרך $SERVER_URL."
    exit 0
fi

cat >&2 <<EOF

[x] אף תחנה לא תוכל לעלות דרך השרת הזה.

תפריט ה-GRUB מפנה כל תחנה שיש לה משימה אל /boot/vmlinuz ו-/boot/initrd.img.
המתקין אינו בונה אותם — זהו שלב נפרד, ובלעדיו השרת עולה, הקונסולה עובדת,
ואף מחשב לא מגיע ל-ImageSrv. מסך הבריאות אומר את אותו הדבר, בשורה
"קבצי האתחול".

מה עושים (docs/server-install.md, "שלב שני, חובה: הקרנל וה-initrd"):

  sudo apt-get install -y --no-install-recommends linux-image-amd64
  KVER=\$(ls /lib/modules | grep -v cloud | tail -n1)
  sudo bash tools/build_initramfs.sh --kernel-version "\$KVER" \\
       --output $HTTP_ROOT/initrd.img
  # לשרת שמציע GUI למחשבי בנייה/שיכפול:
  sudo bash tools/build_initramfs.sh --with-gui --kernel-version "\$KVER" \\
       --output $HTTP_ROOT/initrd.img.gui
  sudo cp "/boot/vmlinuz-\$KVER" $HTTP_ROOT/vmlinuz

ואז מריצים את הבדיקה הזו שוב, עד שהיא עוברת:

  sudo bash install/verify-boot-payload.sh \\
       --server-url $SERVER_URL --http-root $HTTP_ROOT
EOF
exit 1
