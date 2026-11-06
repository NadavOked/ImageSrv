#!/usr/bin/env bash
#
# ImageSrv — גיבוי השרת (‏#1128, עיצוב R41). רץ כ-root על השרת, מ-cron
# או ביד:
#
#   sudo bash /opt/imagesrv/tools/backup-server.sh --dest /backup/imagesrv
#
# מה נכנס, ולמה כך:
#   db/imagesrv-<ts>.db   עותק עקבי של ה-DB דרך ה-backup API של sqlite
#                         (‏`server.system.backup.consistent_db_copy`) — לא `cp`:
#                         ב-WAL חלק מהמצב יושב ב-imagesrv.db-wal, ו-cp של
#                         הקובץ הראשי בזמן ריצה מאבד טרנזאקציות (R40 #8).
#                         ‏`PRAGMA integrity_check` = ok הוא הראיה שהעותק
#                         קריא, לא רק שנכתב (עיקרון 5). ‏sqlite3 (ה-CLI)
#                         אינו מותקן על השרת — python3 כן, ואותו API.
#   data/                 שאר תיקיית הנתונים: תעודת הקונסולה, netcfg,
#                         known_hosts — בלי שום imagesrv.db* (גם לא עותק
#                         ה-pre-upgrade, ‏#2504), בלי נקודות העיגון
#                         של האחסון (‏storage/, ‏storage-test/), ובלי branding/
#                         (לוגו מוסד מלפני #1302 — שארית, לא הגדרה).
#   system/<ts>/          קובצי המערכת של ImageSrv: dnsmasq, nftables,
#                         interfaces.d, ‏/etc/imagesrv, שורות ImageSrv
#                         ב-fstab, יחידות ה-systemd וה-drop-ins שלהן.
#                         **בלי שני הסודות של /etc/imagesrv** (#2450, #2455):
#                         ‏grub-password — התקנה נקייה יוצרת חדשה; ‏creds/ —
#                         הסוד של מיקום SMB שמור ב-DB (‏params_json) ו"התחבר"
#                         כותב את הקובץ מחדש, ושל יעד הגיבוי מוזן מחדש.
#   images/               rsync של תיקיית האימג'ים (אלא אם --no-images):
#                         בלי `.capture-*`/`.import-*` (באמצע), **בלי
#                         `--delete`** — גיבוי אינו מוחק עותקי עבר בגלל
#                         מחיקה בצד המקור — ואחריו `tools/verify-images.py`
#                         על היעד: ‏rsync שסיים אינו "גיבוי תקין".
#   SHA256SUMS-<ts>       על db/ ו-system/<ts>/ (data/ ו-images/ הם מראה
#                         מתגלגלת; לאימג'ים יש מניפסט משלהם).
#
# ‏umask 077: הגיבוי מכיל סיסמאות מגובבות, סודות MFA ואת מפתח התעודה.
# יציאה 0 רק כשכל שלב הצליח ואומת; כל כשל נאמר בשמו ומפיל את הסקריפט.

set -euo pipefail

DEST=""
DATA_DIR="/var/lib/imagesrv"
IMAGES_DIR="/srv/imagesrv/images"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WITH_IMAGES=1
SYS_ROOT="/"

usage() {
    cat <<'EOF'
ImageSrv — גיבוי השרת: DB (עותק עקבי), תיקיית הנתונים, קובצי מערכת, אימג'ים.

  sudo bash tools/backup-server.sh --dest /backup/imagesrv [אפשרויות]

  --dest DIR           חובה — יעד הגיבוי (דיסק אחר / NAS מעוגן)
  --data-dir DIR       תיקיית הנתונים (ברירת מחדל /var/lib/imagesrv)
  --images DIR         תיקיית האימג'ים (ברירת מחדל /srv/imagesrv/images)
  --app-dir DIR        שורש הקוד (ברירת מחדל: התיקייה שמעל הסקריפט)
  --system-root DIR    מאיפה לקרוא את קובצי המערכת (ברירת מחדל /; לבדיקות)
  --no-images          רק DB/נתונים/מערכת — לגיבוי השעתי; האימג'ים בלילי
  -h, --help           המסך הזה

יציאה 0 = הכול הועתק ואומת. כל דבר אחר = לא, והשלב שנכשל נאמר בשמו.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest) DEST="${2:-}"; shift 2 ;;
        --data-dir) DATA_DIR="${2:-}"; shift 2 ;;
        --images) IMAGES_DIR="${2:-}"; shift 2 ;;
        --app-dir) APP_DIR="${2:-}"; shift 2 ;;
        --system-root) SYS_ROOT="${2:-}"; shift 2 ;;
        --no-images) WITH_IMAGES=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "backup-server: ארגומנט לא מוכר: $1" >&2; usage >&2; exit 2 ;;
    esac
done

die() { echo "backup-server: $*" >&2; exit 1; }
say() { echo "backup-server: $*"; }

[[ -n "$DEST" ]] || die "--dest חובה"
[[ -d "$DATA_DIR" ]] || die "תיקיית הנתונים אינה קיימת: $DATA_DIR"
[[ -f "$DATA_DIR/imagesrv.db" ]] || die "אין imagesrv.db ב-$DATA_DIR"
[[ -f "$APP_DIR/server/system/backup.py" ]] || die "אין server/system/backup.py תחת $APP_DIR — --app-dir?"
# ‏rsync מגיע עם המתקין מ-v0.53.3; שרת שהותקן קודם (ה-ISO של v0.53.0) עובד
# עם cp — לא מצטבר, אבל לא "אין גיבוי". נאמר בפלט, לא בשקט.
HAVE_RSYNC=1
command -v rsync >/dev/null 2>&1 || { HAVE_RSYNC=0; echo "backup-server: אין rsync — מעתיק ב-cp (apt-get install rsync למצטבר)" >&2; }
# sync_dir SRC DST [שמות-לדילוג ברמה העליונה...] — ‏rsync, או cp -au לכל
# רשומה עליונה שאינה מוחרגת (התוכן של תיקיית אימג' אינו משתנה אחרי המניפסט).
sync_dir() {
    local src="$1" dst="$2"; shift 2
    if [[ "$HAVE_RSYNC" -eq 1 ]]; then
        local ex=()
        for pat in "$@"; do ex+=(--exclude="/$pat"); done
        rsync -a --one-file-system "${ex[@]}" "$src/" "$dst/"
        return
    fi
    mkdir -p "$dst"
    local entry name pat skip
    for entry in "$src"/* "$src"/.[!.]*; do
        [[ -e "$entry" ]] || continue
        name="$(basename "$entry")"; skip=0
        # shellcheck disable=SC2053  # the pattern is a glob on purpose (.capture-*)
        for pat in "$@"; do [[ "$name" == $pat ]] && { skip=1; break; }; done
        [[ "$skip" -eq 1 ]] && continue
        cp -a -u "$entry" "$dst/"
    done
}
command -v python3 >/dev/null 2>&1 || die "python3 אינו מותקן"

umask 077
TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST/db" "$DEST/data" "$DEST/system/$TS"

# --- 1. DB: עותק עקבי + integrity_check -----------------------------------
DB_OUT="$DEST/db/imagesrv-$TS.db"
say "DB → $DB_OUT"
PYTHONPATH="$APP_DIR" python3 - "$DATA_DIR/imagesrv.db" "$DB_OUT" <<'PYEOF'
import sqlite3, sys
from pathlib import Path
from server.system.backup import consistent_db_copy
src, out = sys.argv[1], Path(sys.argv[2])
conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
try:
    data = consistent_db_copy(conn)          # integrity_check == ok, or raises
finally:
    conn.close()
out.write_bytes(data)
check = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
try:
    verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
    tables = check.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
finally:
    check.close()
if verdict != "ok":
    raise SystemExit(f"integrity_check on {out}: {verdict}")
print(f"backup-server: DB ok — {len(data)} bytes, {tables} tables, integrity_check=ok")
PYEOF

# --- 2. שאר תיקיית הנתונים -----------------------------------------------
# ‏#2504: ‏imagesrv.db* — תבנית, לא רשימת שמות. ה-DB נכנס רק דרך db/ (עותק בלי
# ‏console_secret); עותק שלפני עדכון (imagesrv.db.pre-upgrade, ‏#1894) נושא את הסוד
# כמות שהוא. ‏data/ היא מראה מתגלגלת בלי --delete, ולכן עותק שריצה ישנה השאירה
# שם נמחק כאן — ואחר כך ראיה שאין אף אחד (עיקרון 5).
say "data_dir → $DEST/data/"
sync_dir "$DATA_DIR" "$DEST/data" 'imagesrv.db*' 'storage' 'storage-test' 'branding'
for stale in "$DEST/data"/imagesrv.db*; do
    [[ -e "$stale" ]] || continue
    rm -f -- "$stale"
    say "הוסר מ-data/ עותק DB מריצה קודמת (#2504): ${stale##*/}"
done
for leaked in "$DEST/data"/imagesrv.db*; do
    [[ ! -e "$leaked" ]] || die "עותק DB נשאר ב-data/: $leaked"
done

# --- 3. קובצי המערכת של ImageSrv ------------------------------------------
SYS="$DEST/system/$TS"
say "קובצי מערכת → $SYS/"
SYS_PATHS=(
    /etc/nftables.conf
    '/etc/imagesrv/*'                           # רשומה-רשומה, כדי ש-SYS_SKIP יחול
    '/etc/imagesrv/.[!.]*'
    /srv/imagesrv/boot/grub.cfg
    /etc/systemd/system/imagesrv-server.service
    /etc/systemd/system/imagesrv-server.service.d
    /etc/systemd/system/imagesrv-proxy.service
    /etc/systemd/system/imagesrv-proxy.service.d
    /etc/systemd/system/imagesrv-dcui.service
    /etc/iscsi/initiatorname.iscsi
    '/etc/dnsmasq.d/imagesrv*'                  # מצוטט: הגלוב מתרחב בלולאה, יחסית ל---system-root
    '/etc/network/interfaces.d/imagesrv-*'
)
# ‏#2450/#2455: סודות שאינם נכנסים לגיבוי (יחסית לשורש). מי שקורא את שיתוף
# הגיבוי אינו מקבל את סיסמת ה-GRUB ואת סיסמאות ה-SMB; ‏.grub-password.*.tmp
# הוא write_atomic של השרת באמצע כתיבה.
SYS_SKIP=(
    etc/imagesrv/grub-password
    'etc/imagesrv/.grub-password.*'
    etc/imagesrv/creds
)
copied=0
skipped=()
# ‏--system-root: הבדיקות מצביעות על שורש מזויף ולא על /etc של המכונה
# שמריצה אותן (ב-CI יש `/etc/iscsi/initiatorname.iscsi` שרק root קורא).
# ‏cp שנכשל מפיל את הריצה: כ-root "לא קריא" הוא תקלה, לא משהו שמדלגים עליו.
cd "$SYS_ROOT"
for path in "${SYS_PATHS[@]}"; do          # glob שלא תאם נשאר מילולי — ולכן -e
    # shellcheck disable=SC2086  # ‏${path#/} יחסי לשורש; הגלוב מתרחב כאן בכוונה
    for match in ${path#/}; do
        [[ -e "$match" ]] || continue
        skip=0
        # shellcheck disable=SC2053  # the pattern is a glob on purpose
        for pat in "${SYS_SKIP[@]}"; do [[ "$match" == $pat ]] && { skip=1; break; }; done
        if [[ "$skip" -eq 1 ]]; then skipped+=("/$match"); continue; fi
        cp -a --parents "$match" "$SYS/"
        copied=$((copied + 1))
    done
done
cd - >/dev/null
# ראיה, לא כוונה (עיקרון 5): אף סוד מהרשימה אינו בעותק.
for pat in "${SYS_SKIP[@]}"; do
    # shellcheck disable=SC2086
    for leaked in "$SYS"/$pat; do
        [[ ! -e "$leaked" ]] || die "סוד הגיע לגיבוי: $leaked"
    done
done
[[ ${#skipped[@]} -eq 0 ]] || say "לא בגיבוי (סודות, #2450/#2455): ${skipped[*]} — בשחזור: סיסמת GRUB חדשה, ו-creds/ נכתב מחדש ב'התחבר'"
# שורות ImageSrv ב-fstab: ‏grep 1 = אין שורות (תקין), ‏2 = הבדיקה נשברה.
set +e
grep -i imagesrv "$SYS_ROOT/etc/fstab" > "$SYS/fstab.imagesrv"
rc=$?
set -e
case "$rc" in
    0) copied=$((copied + 1)) ;;
    1) rm -f "$SYS/fstab.imagesrv" ;;
    *) die "grep על /etc/fstab נכשל (rc=$rc)" ;;
esac
say "קובצי מערכת: $copied הועתקו"

# --- 4. אימג'ים: rsync בלי --delete, ואז אימות מול המניפסטים -----------------
if [[ "$WITH_IMAGES" -eq 1 ]]; then
    [[ -d "$IMAGES_DIR" ]] || die "תיקיית האימג'ים אינה קיימת: $IMAGES_DIR"
    say "אימג'ים → $DEST/images/ (בלי --delete)"
    sync_dir "$IMAGES_DIR" "$DEST/images" '.capture-*' '.import-*'
    say "אימות האימג'ים ביעד מול manifest.json"
    python3 "$APP_DIR/tools/verify-images.py" --quiet --allow-empty "$DEST/images"
fi

# --- 5. SHA256SUMS על מה שנוצר בריצה הזו -----------------------------------
(
    cd "$DEST"
    find "db/imagesrv-$TS.db" "system/$TS" -type f -print0 | sort -z | xargs -0 sha256sum
) > "$DEST/SHA256SUMS-$TS"
(cd "$DEST" && sha256sum --quiet -c "SHA256SUMS-$TS") || die "SHA256SUMS לא אומת מיד אחרי הכתיבה"

say "הסתיים: $DEST (‏$TS) — DB אומת, $copied קובצי מערכת, אימג'ים: $([[ "$WITH_IMAGES" -eq 1 ]] && echo אומתו || echo דולגו)"
