#!/usr/bin/env bash
# server-upgrade.sh — מסלול השדרוג של #748/#1193. רץ דרך
# `systemd-run --collect`, מנותק מתהליך
# imagectl-server, כי הצעד האחרון כאן הוא restart שלו.
#
# שימוש: server-upgrade.sh <tag> <repo-dir>
#
# מה שהוא עושה, בסדר הזה, ולמה בסדר הזה (‏#1131 ס' 5–6):
#   0. סבב פתוח/רץ = לא משדרגים (גם כשמופעל ידנית, לא רק מהקונסולה).
#   1. יעד החזרה — הקומיט שהעץ עליו לפני הריצה (‏#1546); השם מ-update_previous
#      רק כשהוא מצביע עליו.
#   2. fetch, אימות חתימת התג (#1277), checkout — מחוץ לארגז החול של שרת ה-web.
#   3. יחידות systemd — קודם, כדי ש-daemon-reload יראה כל שינוי בהן.
#   3א. ‏#1411: עדכוני האבטחה של Debian מהמאגר שבגרסה החתומה בלבד
#      (install/security-upgrade.sh) — שלב `packages`; כשל = חזרה לתג הקודם.
#   4. initrd (#1230) — tools/boot-payload-build.sh לפי הדגלים השמורים, מול
#      הקרנל שנגזר עכשיו. בלי דגלים — ברירת המחדל המתועדת (‏#1546), לא דילוג.
#   5. restart, ואז **ראיה חיובית** שהחדש רץ: is-active + `/health/live`
#      שמדווח בדיוק את התג + verify-boot-payload.sh.
#   5ב. ‏#1361: restart למסך השרת (imagectl-dcui) — אחרי האימות; כשל = אזהרה.
#   6. כשל בראיה → checkout לתג הקודם, יחידות, restart, וסטטוס `failed`
#      עם הסיבה ב-DB (דרך update._set_status) — הקונסולה מציגה אותו.
#   ‏#1301: כל מעבר שלב נכתב לסטטוס (stage + חותמת ב-stages), וכשל נושא reason
#      שמכונה קוראת — מסך העדכון בקונסולה מציג לפיהם, לא לפי הטקסט העברי.
#
# ‏#1074: שדרוג אינו מפעיל את חומת האש הראשית. שרת קיים לא ינותק. (‏#1334:
# חבילת nftables מותקנת בצעד 3ב — הטבלה של ה-VM בלבד, לא ה-ruleset הראשי.)
#
# ‏#1334, צעד 3ב: install/vmupdate-host.sh של התג החדש (חבילות + המשתמש
# imagectl-vm). כשל **אינו** מפיל את העדכון — השרת מתעדכן, ו"עדכון ב-VM"
# לא זמין — וגם אינו נבלע: הסיבה נכתבת לסטטוס (`warning`), והקונסולה מציגה
# "אומת — אבל …". המנוע עצמו מסרב בשם (missing_tool/vm_user) עד שזה יתוקן.
#
# משתני סביבה (לבדיקות — בייצור ברירות המחדל): IMAGECTL_DATA_DIR,
# IMAGECTL_UNIT_DIR, IMAGECTL_JOURNALD_DIR, IMAGECTL_INITRD_FLAGS, IMAGECTL_ISO_MANIFEST,
# IMAGECTL_HTTP_ROOT, IMAGECTL_UPGRADE_LOG, IMAGECTL_CONSOLE_URL,
# IMAGECTL_LIVE_WAIT.
set -euo pipefail

die() { echo "server-upgrade: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: server-upgrade.sh <tag> <repo-dir>"
TAG=$1
REPO_DIR=$2
DATA_DIR="${IMAGECTL_DATA_DIR:-/var/lib/imagectl}"
DB="$DATA_DIR/imagectl.db"
UNIT_DIR="${IMAGECTL_UNIT_DIR:-/etc/systemd/system}"
JOURNALD_DIR="${IMAGECTL_JOURNALD_DIR:-/etc/systemd/journald.conf.d}"
FLAGS_FILE="${IMAGECTL_INITRD_FLAGS:-/etc/imagectl/initrd.flags}"
ISO_MANIFEST="${IMAGECTL_ISO_MANIFEST:-/etc/imagectl/iso-release.json}"
HTTP_ROOT="${IMAGECTL_HTTP_ROOT:-/srv/imagectl/boot}"
UPGRADE_STATE="${IMAGECTL_UPGRADE_STATE:-$DATA_DIR/server-upgrade.state}"
PREV=""         # התג הקודם — נקרא בשלב 1; ריק עד אז (set_status קורא אותו)
INITRD_NOTE=""   # נקבע בחזרה — האם המטען הקודם הוחזר
# ‏#1287: עותק המטען הקודם (initrd/vmlinuz) — מוחזר יחד עם הקוד בחזרה.
PAYLOAD_SAVE="$HTTP_ROOT/.pre-upgrade"
PAYLOAD_SAVED=0  # 1 רק אחרי שהעותק של **הריצה הזו** הושלם
# ‏#1957: הטיימרים שהריצה הזו הפעילה — החזרה מכבה את אלה שאין בעץ הישן.
ENABLED_TIMERS=()
FAILED_TIMER=""
TIMER_NOTE=""
LOG="${IMAGECTL_UPGRADE_LOG:-/var/log/imagectl/server-upgrade.log}"
CONSOLE_URL="${IMAGECTL_CONSOLE_URL:-https://127.0.0.1:8081}"
LIVE_WAIT="${IMAGECTL_LIVE_WAIT:-90}"
CERT="$DATA_DIR/console-tls/console.crt"
[[ -f "$CERT" ]] || CERT=""

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
exec >>"$LOG" 2>&1
echo "--- $(date -Is) upgrading to $TAG in $REPO_DIR ---"

cd "$REPO_DIR" || die "no such repo dir: $REPO_DIR"

# ‏#1301: הריצה הזו — מתי התחילה. אם update.py כתב זה עתה `running` לאותו תג
# (עם started_at/by/from), זו אותה ריצה והשעון בקונסולה ממשיך ממנו; אחרת
# (הפעלה ידנית) — עכשיו. כשל הקריאה אינו עוצר שדרוג: השעה מ-date.
RUN_STARTED="$(python3 - "$REPO_DIR" "$DB" "$TAG" <<'PYEOF'
import json, sys
repo, db, tag = sys.argv[1:4]
sys.path.insert(0, repo)
from server.db import connect, get_setting, now_iso
from server import update
try:
    old = json.loads(get_setting(connect(db), update.STATUS_KEY) or "null")
except ValueError:
    old = None
ok = isinstance(old, dict) and old.get("state") == "running" and old.get("tag") == tag
print(old["started_at"] if ok and old.get("started_at") else now_iso())
PYEOF
)" || RUN_STARTED=""
[[ -n "$RUN_STARTED" ]] || RUN_STARTED="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
STAGE=""   # fetch | verify | install | restart | done — נכתב ב-stage()
# ‏#1334: אזהרה של הריצה (‏"עדכון ב-VM לא זמין: …") — נקבעת בצעד 3ב ועוברת בכל
# כתיבה מכאן והלאה, כך ש-stage()/done אינם מוחקים אותה. ריק = אין.
UPGRADE_WARNING=""

# סטטוס לקונסולה — אותו מפתח ואותה פונקציה של update.py, לא עותק.
# ‏#1301: בכל כתיבה — השלב, ‏started_at וחותמת לכל שלב (‏stages), ובכשל `reason`
# (‏round|fetch|signature|install|timeout|rollback) ו-`detail` (סיבת הכשל בלבד, בלי
# תוצאת החזרה). ‏`error` נשאר הטקסט המלא כמו קודם. רק ‏API יציב של server/ (db,
# ‏update.STATUS_KEY/_set_status): אחרי חזרה ה-python הוא של התג הקודם.
set_status() {   # set_status <state> [<error>] [<reason>] [<detail>]
    python3 - "$REPO_DIR" "$DB" "$TAG" "$PREV" "$RUN_STARTED" "$STAGE" "$1" "${2:-}" "${3:-}" "${4:-}" "$UPGRADE_WARNING" <<'PYEOF' || echo "server-upgrade: כתיבת הסטטוס ל-DB נכשלה"
import json, sys
repo, db, tag, prev, started, stage, state, error, reason, detail, warning = sys.argv[1:12]
sys.path.insert(0, repo)
from server.db import connect, get_setting, journal, now_iso
from server import update
conn = connect(db)
try:
    old = json.loads(get_setting(conn, update.STATUS_KEY) or "null")
except ValueError:
    old = None
same = isinstance(old, dict) and old.get("started_at") == started and old.get("tag") == tag
stages = dict(old.get("stages") or {}) if same else {}
if stage and stage not in stages:
    stages[stage] = now_iso()
doc = {"state": state, "tag": tag, "started_at": started, "stages": stages,
       "from": (old.get("from") if same else None) or prev or None,
       "by": old.get("by") if same else None}
if stage:
    doc["stage"] = stage
for key, value in (("error", error), ("reason", reason), ("detail", detail), ("warning", warning)):
    if value:
        doc[key] = value
update._set_status(conn, doc)
if state in ("failed", "done"):
    journal(conn, "update_apply_" + state,
            f"{tag}: {reason or stage}" + (f" — {error}" if error else ""),
            doc["by"] or "server-upgrade")
PYEOF
}
# ‏#1893: סמן עמיד (fsync + rename) של מה שהריצה הזו כבר עשתה — מה שיחידת
# imagectl-upgrade-recover קוראת באתחול שאחרי הפסקת חשמל. ‏db_snap_ver ו-payload_saved
# הם של **הריצה הזו** בלבד: עותק DB או מטען שנשארו מעדכון קודם אינם דרך חזרה ממנה.
write_recovery_marker() {
    python3 - "$UPGRADE_STATE" "$REPO_DIR" "$PREV" "$TAG" "$STAGE" "$HTTP_ROOT" \
        "${DB_SNAP_VER:-}" "$PAYLOAD_SAVED" <<'PYEOF'
import json, os, sys
path, repo, previous, tag, stage, http_root, db_snap_ver, payload_saved = sys.argv[1:]
tmp = path + ".tmp"
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"repo": repo, "previous": previous, "tag": tag, "stage": stage,
               "http_root": http_root, "db_snap_ver": db_snap_ver,
               "payload_saved": payload_saved == "1"}, f)
    f.flush(); os.fsync(f.fileno())
os.replace(tmp, path)
PYEOF
}
stage() { STAGE=$1; set_status running; write_recovery_marker; }

# 0. השרת עסוק? — ‏#1891: אותה הגדרה בדיוק כמו "החל עדכון" והאתחול המתוזמן
# (‏auto_reboot.busy_reason): סבב, סבב חדר גם בין גלים, קליטה רצה, שיכפול
# מקומי. בלי בדיקת "עדכון רץ" — זה העדכון עצמו; ובלי עדכון VM — המצב שלו
# חי בתהליך השרת, ו"החל עדכון" בדק אותו לפני ששיגר אותנו. DB שלא נקרא =
# לא יודעים = לא משדרגים (עיקרון 5: "לא הצלחנו לבדוק" אינו "לא עסוק").
# מנהל שלחץ "עדכן בכל זאת" (‏`forced` בסטטוס של **הריצה הזו** — running, אותו
# תג) כבר ראה את החסימה ואישר; היא נרשמה ביומן, והסקריפט אינו מסרב שוב.
ROUND="$(python3 - "$REPO_DIR" "$DB" "$TAG" <<'PYEOF'
import sys, types
repo, db, tag = sys.argv[1:4]
sys.path.insert(0, repo)
from server import auto_reboot, update
from server.db import connect
conn = connect(db)
reason = auto_reboot.busy_reason(types.SimpleNamespace(conn=conn), include_update=False)
doc = update.get_status(conn) or {}
forced = doc.get("state") == "running" and doc.get("tag") == tag and doc.get("forced")
if reason and forced:
    print(f"server-upgrade: עסוק ({reason}) — ממשיכים: המנהל אישר מעל החסימה", file=sys.stderr)
print("none" if not reason or forced else reason)
PYEOF
)" || die "לא הצלחתי לבדוק אם השרת עסוק ($DB) — לא משדרגים"
[[ "$ROUND" == "none" ]] || { set_status failed "$ROUND — השדרוג לא התחיל" round "$ROUND — השדרוג לא התחיל"; die "$ROUND — לא משדרגים"; }

# 1. התג הקודם — יעד החזרה. update.py שומר אותו לפני ה-checkout.
PREV="$(python3 - "$REPO_DIR" "$DB" <<'PYEOF'
import sys
repo, db = sys.argv[1:3]
sys.path.insert(0, repo)
from server.db import connect, get_setting
from server import update
print(get_setting(connect(db), update.PREVIOUS_KEY) or "")
PYEOF
)" || die "קריאת התג הקודם מה-DB נכשלה"
# ‏#1546: יעד החזרה הוא מה שהעץ עליו **עכשיו** — לא רק מה שב-DB. בהרצה ידנית
# ‏update_previous ריק (שרת חיפה, 17/10: כשל בלי checkout ובלי restart), ואחרי
# עדכון קודם הוא נשאר על התג שלפני **אותו** עדכון. השם מה-DB נשמר רק כשהוא
# מצביע על אותו קומיט. בלי HEAD קריא — לא משדרגים: אין לאן לחזור.
if ! PRE_HEAD="$(git rev-parse HEAD 2>&1)" || [[ -z "$PRE_HEAD" ]]; then
    why="git rev-parse HEAD נכשל לפני השדרוג (${PRE_HEAD:-בלי פלט}) — אין יעד חזרה, לא משדרגים"
    set_status failed "$why" install "$why"
    die "$why"
fi

if [[ -z "$PREV" ]]; then
    echo "אין תג קודם שמור (update_previous) — יעד החזרה הוא הקומיט הנוכחי $PRE_HEAD"
    PREV="$PRE_HEAD"
# ‏`|| true`: תג שלא נפתר אינו ראיה שזה אותו קומיט — ולכן יעד החזרה הוא HEAD.
elif [[ "$(git rev-parse --verify -q "$PREV^{commit}" || true)" != "$PRE_HEAD" ]]; then
    echo "update_previous ($PREV) אינו מה שהעץ עליו — יעד החזרה הוא הקומיט הנוכחי $PRE_HEAD"
    PREV="$PRE_HEAD"
fi

# 2. רק היחידה המנותקת רשאית לכתוב לעץ. כשל git נכתב לסטטוס כלשונו,
# ולפני שנגענו ביחידות, ב-initrd או בשירות הרץ.
stage fetch
if ! git_error="$(git -C "$REPO_DIR" fetch --tags 2>&1)"; then
    why="git fetch --tags נכשל: $git_error"
    set_status failed "$why" fetch "$why"
    rm -f "$UPGRADE_STATE"
    die "$why"
fi
# ‏#1277: התג חתום במפתח שהעץ **המותקן** סומך עליו — לפני ה-checkout, כלומר
# לפני שקוד מהתג רץ. המאמת וקובץ המפתחות נקראים מהעץ הנוכחי (ה-fetch אינו
# נוגע בעץ העבודה): גרסה שנחתמה במפתח זר אינה מביאה איתה את המפתח שיאשר
# אותה. "לא נבדק" (2) הוא סירוב בדיוק כמו "לא חתום" (1), והצלחה דורשת ראיה
# חיובית — קוד 0 **וגם** שורת `signed:` (עיקרון 5).
SIGNERS="$REPO_DIR/install/release-signers"
stage verify
if sig="$(python3 -I "$REPO_DIR/tools/verify-release-tag.py" "$REPO_DIR" "$TAG" "$SIGNERS" 2>&1)" \
        && [[ "$sig" == "signed: "* ]]; then
    echo "חתימה: $TAG $sig"
else
    why="התג $TAG לא אומת כחתום — העדכון נדחה, העץ לא הוחלף: ${sig:-המאמת לא החזיר דבר}"
    set_status failed "$why" signature "$why"
    rm -f "$UPGRADE_STATE"
    die "$why"
fi
# ‏#1894: עותק עקבי של ה-DB (‏sqlite backup — נכון גם תחת WAL ושרת רץ) לפני
# שקוד חדש נוגע בו. החזרה משחזרת אותו רק אם הגרסה החדשה באמת מיגרה (‏user_version
# עלה) — אחרת אין סיבה לאבד את מה שנרשם בינתיים. בלי עותק — לא משדרגים.
DB_SNAPSHOT="$DB.pre-upgrade"
DB_NOTE=""
if ! DB_SNAP_VER="$(python3 - "$DB" "$DB_SNAPSHOT" <<'PYEOF' 2>&1
import os, sqlite3, sys
src, dst = sys.argv[1:3]
tmp = dst + ".tmp"
if os.path.exists(tmp):
    os.unlink(tmp)
old = os.umask(0o077)          # הקובץ מחזיק סודות (סיסמאות, MFA, console_secret)
try:
    source, copy = sqlite3.connect(src), sqlite3.connect(tmp)
    source.backup(copy)
    version = copy.execute("PRAGMA user_version").fetchone()[0]
    copy.close()
    source.close()
finally:
    os.umask(old)
os.replace(tmp, dst)
print(version)
PYEOF
)" || [[ ! "$DB_SNAP_VER" =~ ^[0-9]+$ ]]; then
    why="העתקת ה-DB לפני העדכון ($DB_SNAPSHOT) נכשלה — לא משדרגים בלי דרך חזרה: $DB_SNAP_VER"
    set_status failed "$why" install "$why"
    rm -f "$UPGRADE_STATE"
    die "$why"
fi
echo "ה-DB הועתק ל-$DB_SNAPSHOT (גרסת סכמה $DB_SNAP_VER)"

stage install
if ! git_error="$(git -C "$REPO_DIR" checkout --detach "$TAG" 2>&1)"; then
    why="git checkout --detach $TAG נכשל: $git_error"
    set_status failed "$why" install "$why"
    rm -f "$UPGRADE_STATE"
    die "$why"
fi
# ‏#1957: יחידות הגרסה — ‏*.service וגם *.timer. גרסה בלי טיימרים היא אפס
# טיימרים (nullglob), לא glob מילולי שנכשל ב-install.
timer_files() {
    local files
    shopt -s nullglob
    files=(install/*.timer)
    shopt -u nullglob
    (( ${#files[@]} == 0 )) || printf '%s\n' "${files[@]}"
}
install_units() {
    local timers
    mapfile -t timers < <(timer_files)
    install -m 0644 install/*.service "$UNIT_DIR/" || return 1
    (( ${#timers[@]} == 0 )) || install -m 0644 "${timers[@]}" "$UNIT_DIR/" || return 1
}
# ‏#1957: טיימר שהגרסה מביאה (#1895: הגיבוי השעתי) מופעל גם בעדכון, לא רק
# בהתקנה נקייה. ‏enable --now על טיימר שכבר פעיל אינו משנה דבר.
enable_timers() {
    local t name
    while IFS= read -r t; do
        name="${t##*/}"
        ENABLED_TIMERS+=("$name")   # לפני הניסיון: גם הפעלה חלקית מכובה בחזרה
        systemctl enable --now "$name" || { FAILED_TIMER="$name"; return 1; }
    done < <(timer_files)
}
# ‏#1957: חזרה לגרסה שאין לה את הטיימר — הוא מכובה ומוסר, אחרת הוא ממשיך
# להריץ יחידה מול מודול שאינו קיים. רץ אחרי ה-checkout לעץ הישן.
drop_new_timers() {
    local name
    for name in "${ENABLED_TIMERS[@]}"; do
        [[ ! -f "install/$name" ]] || continue
        if ! { systemctl disable --now "$name" && rm -f "$UNIT_DIR/$name"; }; then
            TIMER_NOTE="$TIMER_NOTE (כיבוי $name נכשל — הוא עדיין מופעל)"
        fi
    done
}
# ‏#2000: נקרא רק מתוך `if` ב-rollback, ושם `set -e` כבוי — בלי `||` בכל צעד,
# כשל ב-install_units נבלע, ורק ה-restart קבע אם "החזרה הצליחה". הצעד שנכשל
# נשמר בשמו ב-ROLLBACK_STEP, והחזרה מדווחת עליו (עיקרון 5).
ROLLBACK_STEP=""
install_units_and_restart() {
    install_units || { ROLLBACK_STEP="התקנת היחידות של $PREV ל-$UNIT_DIR"; return 1; }
    systemctl daemon-reload || { ROLLBACK_STEP="systemctl daemon-reload"; return 1; }
    systemctl restart imagectl-server || { ROLLBACK_STEP="systemctl restart imagectl-server"; return 1; }
}

# ‏#1287: הקבצים שהבנייה מחליפה — ה-vmlinuz וכל --output, באותו פענוח של
# tools/boot-payload-build.sh (שורה לבנייה, ‏# ושורה ריקה מדולגות). כאן ולא
# שם: יעד ישן שאין לו מצב "רשימה" היה חוסם חזרה לגרסה ישנה.
payload_files() {
    echo "$HTTP_ROOT/vmlinuz"
    if [[ "${payload_args[0]}" == --iso-default ]]; then
        printf '%s\n' "$HTTP_ROOT/initrd.img" "$HTTP_ROOT/initrd.img.gui"
        return
    fi
    local line words i
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -n "$line" && "$line" != \#* ]] || continue
        read -r -a words <<<"$line"
        for i in "${!words[@]}"; do
            if [[ "${words[$i]}" == --output ]]; then echo "${words[$((i + 1))]:-}"; fi
        done
    done < "$FLAGS_FILE"   # קובץ שלא נקרא = סטטוס לא-אפס, לא רשימה חלקית
}

# מעתיק כל קובץ קיים ל-$PAYLOAD_SAVE/<n>, ורושם "<n> <נתיב>" ב-list. נקרא
# כ-`save_payload || rollback`, ושם `set -e` כבוי — לכן כל צעד נבדק בשמו.
save_payload() {
    local n=0 path files
    files="$(payload_files)" || return 1
    rm -rf "$PAYLOAD_SAVE" && mkdir -p "$PAYLOAD_SAVE" && : > "$PAYLOAD_SAVE/list" || return 1
    while IFS= read -r path; do
        [[ -n "$path" && -e "$path" ]] || continue
        cp -p "$path" "$PAYLOAD_SAVE/$n" && echo "$n $path" >> "$PAYLOAD_SAVE/list" || return 1
        n=$((n + 1))
    done <<<"$files"
    PAYLOAD_SAVED=1
    echo "המטען הקודם נשמר ב-$PAYLOAD_SAVE ($n קבצים)"
}

# מחזיר כל קובץ במקומו (העתקה ל-.restore ואז mv — מכונה שמורידה לא תקבל
# חצי קובץ) ומאמת ב-cmp. כל כשל = 1, והעותק נשאר לשחזור ידני.
restore_payload() {
    local n path
    while read -r n path; do
        cp -p "$PAYLOAD_SAVE/$n" "$path.restore" && mv -f "$path.restore" "$path" \
            && cmp -s "$PAYLOAD_SAVE/$n" "$path" || return 1
    done < "$PAYLOAD_SAVE/list" || return 1
    rm -rf "$PAYLOAD_SAVE"
}

# ‏#1301: <סוג> = ה-reason כשלא חזרנו — install (ברירת מחדל) או timeout (השרת החדש
# לא ענה בזמן). חזרה שהצליחה היא reason=rollback, כי זה מה שהמפעיל צריך לדעת: השרת
# עובד, על הגרסה הקודמת.
# ‏#1894: הגרסה החדשה עלתה ומיגרה את ה-DB (‏user_version גבוה מזה של העותק) — הקוד
# הישן מסרב לעלות על DB כזה (‏db.SchemaError), ולכן החזרה מחזירה גם אותו. השירות
# נעצר קודם: אין כותב בזמן השחזור. מה שנרשם מאז העדכון אובד — וזה נאמר בסטטוס.
restore_db_if_migrated() {
    local now out
    [[ -f "$DB_SNAPSHOT" && "${DB_SNAP_VER:-}" =~ ^[0-9]+$ ]] || return 0
    if ! now="$(python3 -c 'import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute("PRAGMA user_version").fetchone()[0])' "$DB" 2>&1)" \
            || [[ ! "$now" =~ ^[0-9]+$ ]]; then
        DB_NOTE=" (גרסת הסכמה של ה-DB לא נקראה — ה-DB לא הוחזר; העותק ב-$DB_SNAPSHOT)"
        return 0
    fi
    (( now > DB_SNAP_VER )) || return 0
    if systemctl stop imagectl-server && out="$(python3 - "$DB_SNAPSHOT" "$DB" <<'PYEOF' 2>&1
import sqlite3, sys
snap, live = sys.argv[1:3]
source, target = sqlite3.connect(snap), sqlite3.connect(live)
source.backup(target)
print(target.execute("PRAGMA user_version").fetchone()[0])
PYEOF
)" && [[ "$out" == "$DB_SNAP_VER" ]]; then
        DB_NOTE=" (ה-DB עבר מיגרציה בגרסה החדשה — סכמה $DB_SNAP_VER→$now — והוחזר לעותק שלפני העדכון; מה שנרשם מאז העדכון אבד)"
    else
        DB_NOTE=" (החזרת ה-DB לעותק שלפני העדכון נכשלה: ${out:-systemctl stop נכשל} — העותק ב-$DB_SNAPSHOT, והשרת הישן לא יעלה על ה-DB החדש)"
    fi
    echo "DB:$DB_NOTE"
}

rollback() {   # rollback <סיבה> [<סוג>]
    local why="$1" cause="${2:-install}"
    trap - ERR
    echo "--- $(date -Is) upgrade to $TAG FAILED: $why ---"
    echo "מחזיר ל-$PREV"
    ROLLBACK_STEP="git checkout --detach $PREV"
    if git checkout --detach "$PREV"; then
        drop_new_timers
        restore_db_if_migrated
        # המטען חוזר לפני ה-restart: השרת הישן עולה כבר מול ה-initrd שלו.
        if [[ "$PAYLOAD_SAVED" == 1 ]]; then
            if restore_payload; then
                INITRD_NOTE=" (וה-initrd הקודם הוחזר)"
            else
                INITRD_NOTE=" (החזרת ה-initrd הקודם נכשלה — העותק ב-$PAYLOAD_SAVE)"
            fi
            echo "מטען האתחול:$INITRD_NOTE"
        fi
        if install_units_and_restart; then
            set_status failed "$why · הוחזר ל-$PREV$INITRD_NOTE$DB_NOTE$TIMER_NOTE" rollback "$why"
            rm -f "$UPGRADE_STATE"
            die "$why — הוחזר ל-$PREV"
        fi
    fi
    set_status failed "$why · והחזרה ל-$PREV נכשלה גם היא: $ROLLBACK_STEP נכשל$INITRD_NOTE$DB_NOTE$TIMER_NOTE" "$cause" "$why"
    die "$why — והחזרה ל-$PREV נכשלה ($ROLLBACK_STEP)"
}

# ‏#1185: מכאן העץ כבר על התג החדש — והוא מה שהשרת יריץ באתחול הבא. לכן
# כל כשל מכאן והלאה חוזר לתג הקודם, גם כשל שאיש לא כתב לו `|| rollback`:
# בלי ה-trap, ‏`set -e` היה יוצא עם העץ על החדש, השירות/היחידות/ה-initrd
# על הישן, וסטטוס `running` לתמיד ("לא ידוע" שנראה כמו "בתהליך").
trap 'rollback "server-upgrade.sh נכשל בשורה $LINENO (קוד $?) — הפירוט ב-$LOG"' ERR
current=$(git rev-parse HEAD 2>&1) || rollback "git rev-parse HEAD נכשל אחרי checkout: $current"
target=$(git rev-list -n 1 "$TAG" 2>&1) || rollback "git rev-list נכשל עבור $TAG אחרי checkout: $target"
# שתי תשובות ריקות אינן "אותו קומיט" -- ראיה חיובית בלבד (עיקרון 5).
[[ -n "$current" && "$current" == "$target" ]] \
    || rollback "העץ ב-$REPO_DIR אינו על $TAG (HEAD הוא ${current:-ריק}, התג הוא ${target:-ריק})"

# 3. יחידות + 4. initrd
install_units || rollback "התקנת יחידות systemd ל-$UNIT_DIR נכשלה"
systemctl daemon-reload || rollback "systemctl daemon-reload נכשל"
enable_timers || rollback "הפעלת $FAILED_TIMER נכשלה (systemctl enable --now)"
# ‏#1893: יחידת השחזור (בתוך install/*.service שלמעלה) מופעלת כאן — לא לפני ה-fetch: עד שהעץ
# החתום נמשך ואומת לא נוגעים ב-systemd (#1193). היא שומרת על העדכון **הבא**; לעדכון הזה
# היא כבר פעילה מההתקנה (setup-boot-server.sh) או מהעדכון הקודם. כשל = אזהרה גלויה, לא חזרה.
systemctl enable imagectl-upgrade-recover.service \
    || UPGRADE_WARNING="${UPGRADE_WARNING:+$UPGRADE_WARNING · }יחידת השחזור של עדכון שנקטע (imagectl-upgrade-recover) לא הופעלה — הפסקת חשמל בעדכון הבא לא תוחזר באתחול"

# 3א. ‏#1411: עדכוני האבטחה של Debian — רק מהמאגר שהגרסה החתומה נושאת
# (install/security-upgrade.sh; הגיבוב שלו בעץ התג, שאומת למעלה). לפני בניית
# ה-initrd, כדי שקרנל חדש מהמאגר ייכנס כבר לקבצי האתחול של המחשבים. כשל = חזרה
# לתג הקודם, כמו כל כשל התקנה. חבילות שכבר הותקנו אינן מוחזרות (apt אינו מוריד
# גרסה בבטחה) — הסיבה בסטטוס אומרת מה קרה. גרסה בלי הסקריפט = גרסה בלי היכולת.
SEC_UP="install/security-upgrade.sh"
if [[ -f "$SEC_UP" ]]; then
    stage packages
    sec_out="$(bash "$SEC_UP" "$TAG" "$REPO_DIR" 2>&1)" \
        || { echo "$sec_out"; rollback "$(printf '%s\n' "$sec_out" | tail -n 1)"; }
    echo "$sec_out"
    # כל האזהרות, לא רק האחרונה: שעון לא מסונכרן (#1773) וקרנל חדש יכולים לבוא יחד.
    sec_warn="$(sed -n 's/^אזהרה: //p' <<<"$sec_out" | awk 'NR > 1 { printf " · " } { printf "%s", $0 }')"
    if [[ -n "$sec_warn" ]]; then
        UPGRADE_WARNING="${UPGRADE_WARNING:+$UPGRADE_WARNING · }$sec_warn"
        set_status running
    fi
else
    echo "$SEC_UP אינו בתג $TAG — גרסה בלי עדכוני מערכת"
fi

# 3ב. ‏#1334 — עדכון ב-VM. גרסה בלי הסקריפט = גרסה בלי היכולת (חזרה לתג ישן),
# לא כשל. ה-`if` מנטרל את ה-trap של ERR: הכשל כאן נרשם, לא מחזיר אחורה.
VMU_HOST="install/vmupdate-host.sh"
if [[ -f "$VMU_HOST" ]]; then
    if vmu_out="$(bash "$VMU_HOST" 2>&1)"; then
        echo "$vmu_out"
    else
        vmu_rc=$?
        echo "$vmu_out"
        vmu_why="עדכון ב-VM לא זמין: $(printf '%s\n' "$vmu_out" | tail -n 1) (install/vmupdate-host.sh, קוד $vmu_rc)"
        echo "server-upgrade: $vmu_why"
        UPGRADE_WARNING="${UPGRADE_WARNING:+$UPGRADE_WARNING · }$vmu_why"
        set_status running
    fi
else
    echo "$VMU_HOST אינו בתג $TAG — גרסה בלי עדכון ב-VM"
fi

# 3ג. ‏#1737: תקרה ליומן המערכת — גם לשרת שהותקן לפניה. גרסה בלי הקובץ = בלי
# היכולת. כשל אינו מחזיר את הקוד אחורה (השרת עובד גם בלעדיה), אבל נאמר בסטטוס.
JOURNALD_CONF="install/journald-imagectl.conf"
if [[ -f "$JOURNALD_CONF" ]]; then
    if jd_out="$(install -D -m 0644 "$JOURNALD_CONF" "$JOURNALD_DIR/90-imagectl.conf" 2>&1 \
            && systemctl restart systemd-journald 2>&1)"; then
        echo "תקרת היומן: $JOURNALD_DIR/90-imagectl.conf"
    else
        jd_why="תקרת היומן (journald) לא הוחלה: $(printf '%s\n' "${jd_out:-אין פלט}" | tail -n 1)"
        echo "server-upgrade: $jd_why"
        UPGRADE_WARNING="${UPGRADE_WARNING:+$UPGRADE_WARNING · }$jd_why"
        set_status running
    fi
else
    echo "$JOURNALD_CONF אינו בתג $TAG — גרסה בלי תקרת יומן"
fi

# ‏#1230: "העדכון עבר והמכונות על initrd ישן" אסור — סוכן ישן מול שרת חדש.
# הבנייה היא tools/boot-payload-build.sh, אותו קוד של firstboot: הקרנל וה-
# epoch נגזרים עכשיו (לא מהקובץ), ו-vmlinuz של הקרנל הזה מועתק. שלושה מצבים:
#   * ‏$FLAGS_FILE קיים — הבניות שנרשמו בהתקנה (תפקיד + --output בלבד).
#   * חסר, בשרת שהותקן מה-ISO (יש $ISO_MANIFEST) — firstboot של לפני #1230
#     בנה בדיוק את ברירת המחדל של ה-ISO ולא רשם אותה. בונים אותה ורושמים.
#   * חסר, בשרת שהותקן ב-setup-boot-server.sh (‏#1546, שרת חיפה) — ברירת המחדל
#     **המתועדת** (docs/server-install.md, ‏install/verify-boot-payload.sh):
#     ‏initrd.img + initrd.img.gui ב-$HTTP_ROOT — אותן שורות. זה לא ניחוש
#     דגלים: אלה הקבצים שהשרת מגיש. בנייה שנכשלת חוזרת לתג הקודם, בשם.
if [[ -e "$FLAGS_FILE" ]]; then
    payload_args=(--flags-file "$FLAGS_FILE")
else
    if [[ -e "$ISO_MANIFEST" ]]; then
        echo "$FLAGS_FILE חסר בשרת מה-ISO — בונה את ברירת המחדל של ה-ISO ורושם אותה"
    else
        echo "$FLAGS_FILE חסר ואין $ISO_MANIFEST — בונה את ברירת המחדל המתועדת (initrd.img + initrd.img.gui ב-$HTTP_ROOT) ורושם אותה"
    fi
    payload_args=(--iso-default --write-flags "$FLAGS_FILE")
fi
# ‏#1463: שלב משלו — הבנייה היא ~90% מהעדכון (498 ש' בשרת המעבדה, 16/10), ומסך העדכון
# נותן לו תקרה משלו. בלי זה `install` היה 8:43 מול תקרה של 10 דקות בלי מעבר שלב.
stage payload
save_payload || rollback "שמירת עותק של המטען הקודם ב-$PAYLOAD_SAVE נכשלה — לא בונים בלי דרך חזרה"
write_recovery_marker   # #1893: from here a cut power restores this run's saved payload
bash tools/boot-payload-build.sh --http-root "$HTTP_ROOT" --manifest "$ISO_MANIFEST" "${payload_args[@]}" \
    || rollback "בניית ה-initrd נכשלה (הפירוט ב-$LOG)"

# 5. restart + ראיה חיובית
stage restart
systemctl restart imagectl-server || rollback "systemctl restart imagectl-server נכשל"
systemctl is-active --quiet imagectl-server || rollback "imagectl-server אינו active אחרי restart"
bash install/console-live.sh "$CONSOLE_URL" "$TAG" "$LIVE_WAIT" "$CERT" \
    || rollback "השרת לא ענה ב-/health/live עם הגרסה $TAG תוך $LIVE_WAIT ש'" timeout

# כתובת הסוכן ל-verify-boot-payload: ‏IMAGECTL_URL מהיחידה (cli — המעבדה),
# אחרת מה שהקונסולה רשמה (deploy:url), אחרת loopback (רשת הפצה שטרם
# הוגדרה, #1088). ‏sed ולא grep -o: "לא נמצא" הוא תשובה, לא כישלון (pipefail).
AGENT_URL="$(systemctl show -p Environment --value imagectl-server \
    | sed -n 's/.*IMAGECTL_URL=\([^ "]*\).*/\1/p' | head -n1)"
if [[ -z "$AGENT_URL" ]]; then
    AGENT_URL="$(python3 - "$DB" <<'PYEOF'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
row = conn.execute("SELECT value FROM settings WHERE key = 'deploy:url'").fetchone()
print(row[0] if row and row[0] else "http://127.0.0.1:8080")
PYEOF
)" || AGENT_URL="http://127.0.0.1:8080"
fi
bash install/verify-boot-payload.sh --app-dir "$REPO_DIR" --http-root "$HTTP_ROOT" --server-url "$AGENT_URL" \
    || rollback "verify-boot-payload.sh נכשל מול $AGENT_URL"

# 5ב. ‏#1361: מסך השרת (imagectl-dcui על tty1) הוא תהליך python נפרד, והוא
# מריץ את הקוד שנטען באתחול — בלי restart המפעיל רואה את המסך הישן עד
# האתחול הבא. אחרי האימות בלבד: כשל כאן אינו כשל שדרוג (השרת החדש רץ
# ואומת), ולכן אינו מחזיר אחורה — אבל גם אינו נבלע: הוא נכתב לאזהרה.
# שלושה מצבים, כל אחד בשם: אין יחידה (שרת שלא הותקן מה-ISO) · עצורה
# בכוונה (inactive) · כל השאר → restart + ראיה חיובית (is-active).
# "לא הצלחנו לבדוק" אינו "אין מסך" (עיקרון 5).
DCUI_UNIT=imagectl-dcui.service
dcui_warn() {
    echo "server-upgrade: $1"
    UPGRADE_WARNING="${UPGRADE_WARNING:+$UPGRADE_WARNING · }$1"
}
dcui_show="$(systemctl show -p LoadState -p ActiveState "$DCUI_UNIT" 2>&1)" \
    || dcui_show="systemctl show נכשל: $dcui_show"
dcui_load="$(sed -n 's/^LoadState=//p' <<<"$dcui_show")"
dcui_active="$(sed -n 's/^ActiveState=//p' <<<"$dcui_show")"
if [[ "$dcui_load" == not-found ]]; then
    echo "$DCUI_UNIT אינו מותקן בשרת הזה — אין מסך שרת להפעיל מחדש"
elif [[ "$dcui_load" != loaded || -z "$dcui_active" ]]; then
    dcui_warn "מסך השרת לא הופעל מחדש — לא נבדק אם הוא קיים: ${dcui_show:-systemctl show לא החזיר דבר}"
elif [[ "$dcui_active" == inactive ]]; then
    echo "$DCUI_UNIT עצור (inactive) — לא מופעל מחדש"
elif systemctl restart "$DCUI_UNIT" && systemctl is-active --quiet "$DCUI_UNIT"; then
    echo "מסך השרת ($DCUI_UNIT) הופעל מחדש על $TAG"
else
    dcui_warn "מסך השרת לא הופעל מחדש: $DCUI_UNIT אינו active אחרי restart — הוא מציג את הגרסה הקודמת עד אתחול"
fi

echo "--- $(date -Is) upgrade to $TAG done and verified ---"
STAGE="done"
# ‏#1893: הסמן יורד **לפני** "done" — חשמל שנופל ביניהם משאיר `running` (נעשה stale
# אחרי STALE_UPDATE_MINUTES), ולא מחזיר באתחול עדכון שכבר אומת.
rm -f "$UPGRADE_STATE"
set_status "done"
# אחרי האימות בלבד. כשל כאן אינו כשל שדרוג (השרת החדש רץ ואומת) — נרשם
# בלוג, והריצה הבאה מוחקת את העותק לפני שהיא שומרת חדש (PAYLOAD_SAVED).
trap - ERR
rm -rf "$PAYLOAD_SAVE" || echo "server-upgrade: לא נמחק $PAYLOAD_SAVE — העותק נשאר על הדיסק"
