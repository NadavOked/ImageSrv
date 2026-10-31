#!/usr/bin/env bash
# deploy/security-upgrade.sh — עדכוני האבטחה של Debian, רק מתוך הגרסה החתומה (#1411).
#
# הכרעת נדב (15/10): לשרת אין מקור apt חי. כל גרסה של ImageSrv נושאת מאגר apt
# קטן — חבילות trixie-security של מה שהשרת מתקין (tools/iso/make-pool.sh
# --security) — והגיבוב שלו כתוב ב-deploy/security-pool.json **שבעץ התג**.
# חתימת התג (#1277) היא לכן החתימה על המאגר: tar שאינו תואם לגיבוב נדחה.
#
#   deploy/security-upgrade.sh <tag> <repo-dir>
#
# נקרא מ-tools/server-upgrade.sh אחרי אימות החתימה וה-checkout (העץ = התג).
# הסדר: גרסה בלי security-pool.json → אין מה לעשות (0) · המאגר הזה כבר הוחל
# (‏$DATA_DIR/security-pool.json, אותו sha256) → 0 · הורדה, גודל + sha256 מול
# התג · אילו חבילות **מותקנות** מפגרות אחרי המאגר (dpkg) · אם יש: apt-get
# install שלהן בשמן, שרואה **רק** את המאגר הזה (sources/lists/cache משלו), בלי הסרות,
# בתקרת זמן · ראיה חיובית: אף חבילה מותקנת אינה מפגרת אחריו · רישום המצב.
#
# ‏#1803: גם חבילה מ-tools/iso/packages.txt **של התג** שאינה מותקנת בשרת (נוספה לרשימה
# אחרי שהשרת הותקן — ifenslave/#1264) מותקנת מאותו מאגר, באותה קריאת apt. המאגר נושא את
# כל הסגירה של הרשימה (make-pool.sh --security). חבילה חסרה שאינה במאגר = כשל לפני apt;
# ‏"המאגר כבר הוחל" אינו "אין מה לעשות" כל עוד חסרה חבילה. שירות שחבילה כזו מביאה **אינו
# מוגדר כאן** — ההגדרה שייכת לקוד שמשתמש בו (docs/release-signing.md, הסדר מול #1773).
#
# ‏0 = הכול מעודכן ונבדק בחזרה. אחרת השורה האחרונה ב-stdout היא הסיבה:
#   2 security-pool.json או packages.txt לא תקין/לא נקרא · 3 הורדה/גיבוב · 4 apt-get update
#   נכשל או בדיקת הגרסאות/המותקנות לא רצה (לא הותקן דבר) · 5 apt-get install נכשל/פקע או
#   רצה להסיר (ייתכן שחלק הותקן; dpkg --configure -a הורץ) · 6 apt יצא 0 וחבילות עדיין
#   מפגרות או חסרות · 7 חבילה מ-packages.txt חסרה בשרת ואינה במאגר (או שאין מאגר) — לא
#   הותקן דבר · 8 החלפת systemd-timesyncd ב-chrony (‏#1773, deploy/timesync-swap.sh) נכשלה —
#   השורה אומרת אם לא התחילה, אם חזרה ל-timesyncd, או אם גם החזרה נכשלה. החבילות **אינן מוחזרות** לגרסה קודמת בכשל — החזרה היא של הקוד (server-upgrade).
#
# משתני סביבה (בדיקות; בייצור ברירות המחדל): IMAGESRV_DATA_DIR,
# ‏IMAGESRV_SECURITY_POOL_URL (התיקייה שבה ה-tar; ברירת מחדל: ה-Release של התג
# ב-github, לפי origin), ‏IMAGESRV_SECURITY_TIMEOUT (שניות ל-apt, 1200),
# ‏IMAGESRV_SECURITY_DL_TIMEOUT (שניות להורדה, 1800), ‏IMAGESRV_APT, ‏IMAGESRV_DPKG,
# ‏IMAGESRV_DPKG_QUERY, ‏IMAGESRV_CURL, ‏IMAGESRV_PYTHON.
set -uo pipefail

TAG=${1:?usage: security-upgrade.sh <tag> <repo-dir>}
REPO=${2:?usage: security-upgrade.sh <tag> <repo-dir>}
DATA_DIR="${IMAGESRV_DATA_DIR:-/var/lib/imagesrv}"
STATE="$DATA_DIR/security-pool.json"
WORK="$DATA_DIR/security-pool"
APT="${IMAGESRV_APT:-apt-get}"
DPKG="${IMAGESRV_DPKG:-dpkg}"
DPKG_QUERY="${IMAGESRV_DPKG_QUERY:-dpkg-query}"
CURL="${IMAGESRV_CURL:-curl}"
PYTHON="${IMAGESRV_PYTHON:-python3}"
APT_TIMEOUT="${IMAGESRV_SECURITY_TIMEOUT:-1200}"
DL_TIMEOUT="${IMAGESRV_SECURITY_DL_TIMEOUT:-1800}"
LOCK="$REPO/deploy/security-pool.json"
PKG_LIST="$REPO/tools/iso/packages.txt"

fail() { echo "$2"; exit "$1"; }
words() { tr '\n' ' ' <<<"$1" | sed 's/ *$//'; }
count() { if [[ -n "$1" ]]; then wc -l <<<"$1"; else echo 0; fi; }

# --- ‏#1803: מה שהתג דורש — אותה רשימה שה-ISO מתקין (installer/lib/bootstrap.sh). רשימה
# שלא נקראה אינה "אין חסרות" (עיקרון 5).
[[ -r "$PKG_LIST" ]] \
    || fail 2 "עדכוני מערכת: tools/iso/packages.txt של $TAG לא נקרא — לא נבדק אילו חבילות חסרות בשרת"
WANTED="$(sed 's/#.*//' "$PKG_LIST" | tr -s ' \t' '\n' | sed '/^$/d' | LC_ALL=C sort -u)" && [[ -n "$WANTED" ]] \
    || fail 2 "עדכוני מערכת: tools/iso/packages.txt של $TAG ריק או לא נקרא"
bad="$(grep -v '^[A-Za-z0-9][A-Za-z0-9+.-]*$' <<<"$WANTED")"; rc=$?
[[ $rc -eq 1 ]] || fail 2 "עדכוני מערכת: tools/iso/packages.txt של $TAG לא תקין (grep $rc): $(words "$bad")"

# אילו מהן אינן מותקנות. ‏dpkg-query: 1 = "לא מוכרת" (תשובה: חסרה); 0 עם סטטוס שאינו
# "install ok installed" (הוסרה, config-files) = חסרה; כל קוד אחר = הבדיקה נשברה (2).
not_installed() {
    local pkg st rc
    while read -r pkg; do
        st="$("$DPKG_QUERY" -W -f='${Status} ${Version}' "$pkg" 2>/dev/null)"; rc=$?
        if [[ $rc -eq 1 ]]; then echo "$pkg"; continue; fi
        [[ $rc -eq 0 ]] || return 2
        [[ "$st" == "install ok installed "* ]] || echo "$pkg"
    done <<<"$WANTED"
}

if [[ ! -e "$LOCK" ]]; then
    missing="$(not_installed)" \
        || fail 4 "עדכוני מערכת: בדיקת החבילות המותקנות מול packages.txt לא רצה — לא הותקן דבר"
    [[ -z "$missing" ]] \
        || fail 7 "עדכוני מערכת: הגרסה $TAG אינה נושאת מאגר, ואין מאיפה להתקין את מה שהיא דורשת: $(words "$missing")"
    echo "עדכוני מערכת: הגרסה $TAG אינה נושאת מאגר עדכוני אבטחה — אין מה להתקין"
    exit 0
fi
lock_out="$("$PYTHON" -I "$REPO/deploy/security-pool-lock.py" "$LOCK" 2>&1)" \
    || fail 2 "עדכוני מערכת: deploy/security-pool.json של $TAG לא תקין: $lock_out"
read -r ASSET SHA SIZE DATE <<<"$lock_out"

applied_sha="$("$PYTHON" -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("sha256",""))' "$STATE" 2>/dev/null)" \
    || applied_sha=""
if [[ "$applied_sha" == "$SHA" ]]; then
    missing="$(not_installed)" \
        || fail 4 "עדכוני מערכת: בדיקת החבילות המותקנות מול packages.txt לא רצה — לא הותקן דבר"
    if [[ -z "$missing" ]]; then
        echo "עדכוני מערכת מתאריך $DATE כבר מותקנים (המאגר $ASSET)"
        exit 0
    fi
    echo "עדכוני מערכת: המאגר $ASSET כבר הוחל, אבל חסרות חבילות מהרשימה: $(words "$missing")"
fi

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
rm -rf "$WORK" && mkdir -p "$WORK/repo" "$WORK/lists/partial" "$WORK/cache/archives/partial" "$WORK/sources.list.d" \
    || fail 3 "עדכוני מערכת: לא נוצרה תיקיית העבודה $WORK"

# --- הורדה: מה-Release של התג, וראיה חיובית — גודל ו-sha256 מול התג החתום ----------
if [[ -n "${IMAGESRV_SECURITY_POOL_URL:-}" ]]; then
    base="${IMAGESRV_SECURITY_POOL_URL%/}"
else
    origin="$(git -C "$REPO" remote get-url origin 2>&1)" \
        || fail 3 "עדכוני מערכת: לא נקראה כתובת המקור של הגרסאות (git remote): $origin"
    origin="${origin%/}"; origin="${origin%.git}"
    [[ "$origin" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
        || fail 3 "עדכוני מערכת: המקור $origin אינו ריפו ב-github — לא ידוע מאיפה להוריד את $ASSET"
    base="$origin/releases/download/$TAG"
fi
echo "עדכוני מערכת: מוריד $ASSET ($SIZE בתים) מ-$base"
if ! dl_out="$("$CURL" -fsSL --proto '=https,file' --proto-redir '=https' --connect-timeout 20 \
        --speed-limit 10240 --speed-time 60 --max-time "$DL_TIMEOUT" \
        -o "$WORK/$ASSET" "$base/$ASSET" 2>&1)"; then
    fail 3 "עדכוני מערכת: הורדת $ASSET נכשלה: ${dl_out:-curl לא מסר סיבה}"
fi
got_size="$(stat -c %s "$WORK/$ASSET" 2>/dev/null)" || got_size=""
got_sha="$(sha256sum "$WORK/$ASSET" 2>/dev/null | cut -d' ' -f1)" || got_sha=""
[[ "$got_size" == "$SIZE" && "$got_sha" == "$SHA" ]] \
    || fail 3 "עדכוני מערכת: $ASSET שהורד אינו תואם לגיבוב שבגרסה החתומה (גודל ${got_size:-?}, sha256 ${got_sha:-?}) — נדחה"
tar -xf "$WORK/$ASSET" -C "$WORK/repo" --no-same-owner \
    || fail 3 "עדכוני מערכת: פתיחת $ASSET נכשלה"
comps=()
for d in "$WORK"/repo/dists/trixie/*/; do [[ -d "$d" ]] && comps+=("$(basename "$d")"); done
[[ ${#comps[@]} -gt 0 ]] || fail 3 "עדכוני מערכת: ב-$ASSET אין dists/trixie — אין מאגר"

# --- אילו חבילות מותקנות מפגרות אחרי המאגר. ‏dpkg-query: 1 = "לא מותקנת" (תשובה), --
# כל קוד אחר = הבדיקה עצמה נשברה (CLAUDE.md, "1 הוא תשובה").
# אינדקס שלא נפתח, או ריק, אינו "הכול מעודכן" — הבדיקה נכשלת (2).
behind() {
    local idx pkg ver st rc
    idx="$(zcat "$WORK"/repo/dists/trixie/*/binary-*/Packages.gz | awk '/^Package: /{p=$2} /^Version: /{print p, $2}')" \
        && [[ -n "$idx" ]] || return 2
    while read -r pkg ver; do
        st="$("$DPKG_QUERY" -W -f='${Status} ${Version}' "$pkg" 2>/dev/null)"; rc=$?
        if [[ $rc -eq 1 ]]; then continue; fi
        [[ $rc -eq 0 ]] || return 2
        [[ "$st" == "install ok installed "* ]] || continue
        "$DPKG" --compare-versions "${st#install ok installed }" ge "$ver" || echo "$pkg"
    done <<<"$idx"
}
before="$(behind)" || fail 4 "עדכוני מערכת: בדיקת הגרסאות המותקנות מול המאגר לא רצה — לא הותקן דבר"

# --- ‏#1803: החבילות מהרשימה שחסרות בשרת — כולן חייבות להיות במאגר, אחרת לא נוגעים בדבר.
missing="$(not_installed)" \
    || fail 4 "עדכוני מערכת: בדיקת החבילות המותקנות מול packages.txt לא רצה — לא הותקן דבר"
if [[ -n "$missing" ]]; then
    in_pool="$(zcat "$WORK"/repo/dists/trixie/*/binary-*/Packages.gz | awk '/^Package: /{print $2}')" \
        && [[ -n "$in_pool" ]] || fail 4 "עדכוני מערכת: אינדקס המאגר לא נקרא — לא הותקן דבר"
    # ‏grep -v: 0 = יש שורות שאינן במאגר (תשובה), 1 = כולן בו, אחר = הבדיקה נשברה.
    absent="$(grep -vxF -f <(printf '%s\n' "$in_pool") <<<"$missing")"; rc=$?
    [[ $rc -ne 0 ]] \
        || fail 7 "עדכוני מערכת: חבילות שהגרסה דורשת אינן במאגר החתום של $TAG ($ASSET) — לא הותקן דבר: $(words "$absent")"
    [[ $rc -eq 1 ]] || fail 4 "עדכוני מערכת: בדיקת החבילות החסרות מול המאגר לא רצה (grep $rc) — לא הותקן דבר"
    echo "עדכוני מערכת: חבילות חדשות בגרסה: $(words "$missing")"
fi

# --- ‏#1773 (הכרעת נדב 19/10, PR #1834, אפשרות ב'): chrony מחליפה את systemd-timesyncd. זוג
# קבוע, בשלב משלו (deploy/timesync-swap.sh) ואחרי שאר החבילות — לא היתר הסרה כאן:
# ‏`--no-remove` נשאר לכל השאר. chrony כבר במאגר (נבדק למעלה, עם כל החסרות).
SWAP=""
if grep -qx chrony <<<"$missing"; then
    st="$("$DPKG_QUERY" -W -f='${Status} ${Version}' systemd-timesyncd 2>/dev/null)"; rc=$?
    [[ $rc -le 1 ]] || fail 4 "עדכוני מערכת: בדיקת systemd-timesyncd לא רצה (dpkg-query $rc) — לא הותקן דבר"
    if [[ $rc -eq 0 && "$st" == "install ok installed "* ]]; then
        SWAP=1
        missing="$(grep -vx chrony <<<"$missing")"     # ריק (grep 1) = chrony הייתה החסרה היחידה
        echo "עדכוני מערכת: chrony תחליף את systemd-timesyncd בשלב נפרד, אחרי שאר החבילות"
    fi
fi

if [[ -n "$before" || -n "$missing" || -n "$SWAP" ]]; then
    [[ -z "$before" ]] || echo "עדכוני מערכת: $(wc -l <<<"$before") חבילות לעדכון: $(words "$before")"
    printf 'deb [trusted=yes] file:%s trixie %s\n' "$WORK/repo" "${comps[*]}" > "$WORK/sources.list"
    opts=(-o "Dir::Etc::SourceList=$WORK/sources.list" -o "Dir::Etc::SourceParts=$WORK/sources.list.d"
          -o "Dir::State::Lists=$WORK/lists" -o "Dir::Cache=$WORK/cache" -o "Acquire::Retries=0"
          -o "DPkg::Lock::Timeout=120")
    upd="$(timeout --kill-after=30 "$APT_TIMEOUT" env DEBIAN_FRONTEND=noninteractive "$APT" "${opts[@]}" update -qq 2>&1)" \
        || fail 4 "עדכוני מערכת: apt-get update מול המאגר נכשל — לא הותקן דבר: $(tail -n 1 <<<"$upd")"
    # ‏install של החבילות המפגרות בשמן, ולא `upgrade`: על TA (‏17/10) ‏`upgrade
    # --with-new-pkgs` השאיר בצד את linux-image-amd64, שתלויה בחבילת קרנל חדשה, ויצא 0.
    # ‏--no-remove: אם apt רוצה להסיר חבילה כלשהי — הוא נכשל, ואנחנו איתו.
    # ‏--no-install-recommends: רק המפגרות והחדשות ומה שהן חייבות, לא תוספות מהמאגר —
    # כמו ה-ISO עצמו (installer/lib/bootstrap.sh).
    mapfile -t names < <(sed '/^$/d' <<<"$before"$'\n'"$missing")
    if (( ${#names[@]} )); then
        up="$(timeout --kill-after=30 "$APT_TIMEOUT" env DEBIAN_FRONTEND=noninteractive "$APT" "${opts[@]}" \
                -y -q --no-remove --no-install-recommends \
                -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
                install "${names[@]}" 2>&1)"; rc=$?
        printf '%s\n' "$up" | tail -n 20
        if [[ $rc -ne 0 ]]; then
            why="apt-get install נכשל (קוד $rc)"; [[ $rc -eq 124 ]] && why="apt-get install לא הסתיים תוך $APT_TIMEOUT ש'"
            timeout --kill-after=30 600 env DEBIAN_FRONTEND=noninteractive "$DPKG" --configure -a \
                || why="$why; גם dpkg --configure -a נכשל"
            fail 5 "עדכוני מערכת: $why: $(grep -v '^\s*$' <<<"$up" | tail -n 1)"
        fi
    fi
    after="$(behind)" || fail 6 "עדכוני מערכת: apt-get יצא 0, אבל בדיקת הגרסאות אחריו לא רצה"
    [[ -z "$after" ]] || fail 6 "עדכוני מערכת: apt-get יצא 0, אבל לא עודכנו: $(tr '\n' ' ' <<<"$after")"
    # ‏#1773: ההחלפה — אחרי ש-systemd עודכן, כי דרך החזרה היא systemd-timesyncd בגרסה שלו.
    # כשל = הסיבה של השלב (חזרנו ל-timesyncd, או לא התחלנו, או שגם החזרה נכשלה) — וחזרה לתג.
    if [[ -n "$SWAP" ]]; then
        swap="$(bash "$REPO/deploy/timesync-swap.sh" "$REPO" "$WORK/repo" "${opts[@]}" 2>&1)"; rc=$?
        printf '%s\n' "$swap"
        [[ $rc -eq 0 ]] || fail 8 "עדכוני מערכת: $(grep -v '^\s*$' <<<"$swap" | tail -n 1)"
        missing="$(sed '/^$/d' <<<"$missing"$'\n'chrony)"
    fi
    # ‏#1803: ראיה חיובית — קריאה חוזרת של dpkg, לא קוד היציאה של apt.
    still="$(not_installed)" || fail 6 "עדכוני מערכת: apt-get יצא 0, אבל בדיקת החבילות המותקנות אחריו לא רצה"
    [[ -z "$still" ]] || fail 6 "עדכוני מערכת: apt-get יצא 0, אבל לא הותקנו: $(words "$still")"
fi

state_tmp="$STATE.tmp"
"$PYTHON" -I -c 'import json,sys
k = ("date", "sha256", "asset", "tag", "upgraded", "installed")
print(json.dumps(dict(zip(k, sys.argv[1:7])), ensure_ascii=False))' \
    "$DATE" "$SHA" "$ASSET" "$TAG" "$(count "$before")" "$(count "$missing")" > "$state_tmp" \
    && mv -f "$state_tmp" "$STATE" \
    || fail 6 "עדכוני מערכת הותקנו, אבל $STATE לא נכתב"
if grep -q '^linux-image-' <<<"$before"$'\n'"$missing"; then
    echo "אזהרה: קרנל חדש הותקן מעדכוני המערכת — בשרת עצמו הוא ייכנס לתוקף באתחול הבא"
fi
echo "עדכוני מערכת מתאריך $DATE מותקנים ($ASSET)"
