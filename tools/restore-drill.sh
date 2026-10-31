#!/usr/bin/env bash
#
# ImageSrv — תרגיל שחזור (‏#1128, R41 §7): גיבוי שלא שוחזר פעם אינו גיבוי.
#
#   sudo bash /opt/imagesrv/tools/restore-drill.sh                 # מגבה ומשחזר
#   sudo bash /opt/imagesrv/tools/restore-drill.sh --backup /backup/imagesrv
#
# מה קורה: (1) גיבוי טרי ל-`mktemp -d` — או `--backup DIR` קיים מהריצה
# הלילית; (2) שחזור לתיקייה זמנית: ה-DB האחרון מ-`db/`, ‏`data/`, והאימג'ים
# (העתקה מלאה — או `--skip-images` כשאין מקום: אז DB ותצורה בלבד, ונאמר);
# (3) שרת **שני** על העותק, על פורטים זמניים ו-loopback בלבד; (4) הפאס:
# ‏`GET /api/console/health/live` = 200 עם גרסה, מספר ה-manifest.json
# בעותק שווה למקור, ו-`verify-images.py` על העותק ירוק. ‏`is-active` אינו
# ראיה — שרת שעלה ונפל אחרי שנייה גם הוא היה "active" לרגע (עיקרון 5).
#
# השרת השני רץ כמשתמש `nobody` כשאנחנו root, בכוונה: בעלייה השרת מסנכרן
# את dnsmasq ואת known-macs **מה-DB שלו** לתוך /etc — ועותק שרץ כ-root
# היה כותב את הגיבוי (אולי ישן) על תצורת הייצור החיה. כ-nobody הכתיבה
# נכשלת ונרשמת ביומן של **העותק**, והייצור אינו נוגע.

set -euo pipefail

BACKUP=""
DATA_DIR="/var/lib/imagesrv"
IMAGES_DIR="/srv/imagesrv/images"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=18080
KEEP=0
WAIT=60
SKIP_IMAGES=0
SYS_ROOT="/"

usage() {
    cat <<'EOF'
ImageSrv — תרגיל שחזור: גיבוי → שחזור לתיקייה זמנית → שרת שני → ראיה.

  sudo bash tools/restore-drill.sh [אפשרויות]

  --backup DIR         גיבוי קיים (מ-backup-server.sh) במקום גיבוי טרי
  --data-dir DIR       תיקיית הנתונים של הייצור (ברירת מחדל /var/lib/imagesrv)
  --images DIR         תיקיית האימג'ים של הייצור (ברירת מחדל /srv/imagesrv/images)
  --app-dir DIR        שורש הקוד (ברירת מחדל: התיקייה שמעל הסקריפט)
  --port N             פורט הסוכן של השרת השני; הקונסולה על N+1, הקיוסק N+2 (ברירת מחדל 18080)
  --wait SECONDS       כמה להמתין לשרת השני (ברירת מחדל 60)
  --skip-images        לא לשחזר אימג'ים (אין מקום לעותק) — DB ותצורה בלבד
  --system-root DIR    מועבר ל-backup-server.sh (לבדיקות)
  --keep               לא למחוק את תיקיית התרגיל בסיום (לניפוי)
  -h, --help           המסך הזה

יציאה 0 = PASS: השחזור עלה וענה, והאימג'ים בעותק שלמים. כל דבר אחר = FAIL בשמו.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup) BACKUP="${2:-}"; shift 2 ;;
        --data-dir) DATA_DIR="${2:-}"; shift 2 ;;
        --images) IMAGES_DIR="${2:-}"; shift 2 ;;
        --app-dir) APP_DIR="${2:-}"; shift 2 ;;
        --port) PORT="${2:-}"; shift 2 ;;
        --wait) WAIT="${2:-}"; shift 2 ;;
        --skip-images) SKIP_IMAGES=1; shift ;;
        --system-root) SYS_ROOT="${2:-}"; shift 2 ;;
        --keep) KEEP=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "restore-drill: ארגומנט לא מוכר: $1" >&2; usage >&2; exit 2 ;;
    esac
done

say() { echo "restore-drill: $*"; }
fail() { echo "restore-drill: FAIL — $*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || fail "curl אינו מותקן"
[[ -f "$APP_DIR/server/main.py" ]] || fail "אין server/main.py תחת $APP_DIR — --app-dir?"

umask 077
DRILL="$(mktemp -d /tmp/imagesrv-drill.XXXXXX)"
SERVER_PID=""
cleanup() {
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [[ "$KEEP" -eq 1 ]]; then say "תיקיית התרגיל נשמרה: $DRILL"; else rm -rf "$DRILL"; fi
}
trap cleanup EXIT

# --- 1. גיבוי ---------------------------------------------------------------
if [[ -z "$BACKUP" ]]; then
    BACKUP="$DRILL/backup"
    say "גיבוי טרי → $BACKUP"
    NOIMG=(); [[ "$SKIP_IMAGES" -eq 1 ]] && NOIMG=(--no-images)
    bash "$APP_DIR/tools/backup-server.sh" --dest "$BACKUP" --data-dir "$DATA_DIR" \
        --images "$IMAGES_DIR" --app-dir "$APP_DIR" --system-root "$SYS_ROOT" "${NOIMG[@]}"
fi
[[ -d "$BACKUP/db" && -d "$BACKUP/data" ]] || fail "$BACKUP אינו גיבוי של backup-server.sh (אין db/ ו-data/)"
LATEST_DB="$(find "$BACKUP/db" -maxdepth 1 -name 'imagesrv-*.db' -print0 | sort -z | tail -z -n 1 | tr -d '\0')"
[[ -n "$LATEST_DB" ]] || fail "אין imagesrv-*.db תחת $BACKUP/db"

# --- 2. שחזור לתיקייה זמנית ---------------------------------------------------
RESTORE="$DRILL/restore"
mkdir -p "$RESTORE/data" "$RESTORE/images" "$RESTORE/tftp"
say "שחזור: $LATEST_DB → $RESTORE/data/imagesrv.db"
cp -a "$BACKUP/data/." "$RESTORE/data/"
cp "$LATEST_DB" "$RESTORE/data/imagesrv.db"
WITH_IMAGES=0
if [[ "$SKIP_IMAGES" -eq 0 && -d "$BACKUP/images" ]]; then
    # העתקה מלאה, לא קישורים קשיחים: העותק עובר chown ל-nobody, וקישור
    # קשיח היה משנה את הבעלות גם על קובצי הגיבוי עצמם (אותו inode).
    say "שחזור האימג'ים (העתקה מלאה)"
    cp -a "$BACKUP/images/." "$RESTORE/images/"
    WITH_IMAGES=1
fi
LOG="$DRILL/server.log"
: > "$LOG"
RUN_AS=()
if [[ "$(id -u)" -eq 0 ]]; then
    # ‏nobody צריך להיכנס ל-$DRILL (711 — בלי לקרוא את הרשימה) ולבעול את
    # העותק; ‏$RESTORE עצמו 700 — הסודות המשוחזרים אינם נפתחים למשתמש אחר.
    chmod 711 "$DRILL"
    chown -R nobody "$RESTORE" "$LOG"
    chmod 700 "$RESTORE"
    RUN_AS=(runuser -u nobody --)
fi

# --- 3. שרת שני על העותק ----------------------------------------------------
CONSOLE_PORT=$((PORT + 1))
say "מרים שרת שני על 127.0.0.1:$PORT/$CONSOLE_PORT (לוג: $LOG)"
# הפלט לקובץ ולא ל-PIPE (שרת שאיש לא קורא את הפלט שלו נחסם — CLAUDE.md).
"${RUN_AS[@]}" env PYTHONPATH="$APP_DIR" python3 -m server.main \
    --server-url "http://127.0.0.1:$PORT" --data-dir "$RESTORE/data" --images "$RESTORE/images" \
    --host 127.0.0.1 --port "$PORT" --console-host 127.0.0.1 --console-port "$CONSOLE_PORT" \
    --console-tls off --tftp-root "$RESTORE/tftp" \
    --boot-dir "$RESTORE/boot" --dhcp-leases "$RESTORE/leases" --sender-simulate \
    --repo-dir "$APP_DIR" >> "$LOG" 2>&1 </dev/null &
SERVER_PID=$!

# --- 4. הראיה --------------------------------------------------------------
LIVE=""
for _ in $(seq 1 "$WAIT"); do
    kill -0 "$SERVER_PID" 2>/dev/null || break
    LIVE="$(curl -s -m 3 "http://127.0.0.1:$CONSOLE_PORT/api/console/health/live" || true)"
    [[ "$LIVE" == *'"ok":true'* ]] && break
    sleep 1
done
if [[ "$LIVE" != *'"ok":true'* ]]; then
    tail -n 20 "$LOG" >&2 || true
    fail "השרת המשוחזר לא ענה על /health/live תוך ${WAIT}s"
fi
say "השרת המשוחזר עונה: $LIVE"

count_manifests() { find "$1" -name manifest.json -not -path '*/.capture-*' -not -path '*/.import-*' | wc -l; }
SRC_N="$(count_manifests "$IMAGES_DIR")"
DST_N="$(count_manifests "$RESTORE/images")"
if [[ "$WITH_IMAGES" -eq 1 ]]; then
    [[ "$SRC_N" == "$DST_N" ]] || fail "מספר האימג'ים שונה: מקור $SRC_N, עותק $DST_N"
    python3 "$APP_DIR/tools/verify-images.py" --quiet --allow-empty "$RESTORE/images" \
        || fail "verify-images על העותק נכשל"
    say "אימג'ים: $DST_N = $SRC_N, כל המחיצות תואמות למניפסט"
else
    say "אימג'ים לא שוחזרו (--skip-images / גיבוי בלי אימג'ים) — נבדקו DB ותיקיית הנתונים בלבד"
fi
DB_MACHINES="$(python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('select count(*) from machines').fetchone()[0])" "$RESTORE/data/imagesrv.db")"
say "PASS — ‏$DB_MACHINES מכונות ב-DB המשוחזר; גיבוי: $BACKUP"
