#!/usr/bin/env bash
#
# ImageSrv — ה-repo המקומי של ה-ISO (#1139): כל חבילה מ-packages.txt
# **וכל התלויות שלה**, כקבצי .deb, ואינדקסי apt מעליהם.
#
#   sudo tools/iso/make-pool.sh --out /root/ic-iso-tmp/repo --netinst debian-13.7.0-amd64-netinst.iso
#   sudo tools/iso/make-pool.sh --index-only <עץ-ISO>
#   sudo tools/iso/make-pool.sh --security --out /root/ic-security-pool
#
# ‏#1411: המארח הבונה **חייב** לכלול trixie-security במקורות שלו — אחרת ה-pool
# (וכל שרת שיותקן ממנו) קפוא על הגרסאות של יום ההוצאה, והסקריפט נכשל בשמו.
#
# ‏--security (‏#1411, ‏#1803): **כל** אותה סגירה, בגרסת המועמד — של trixie-security
# כשיש — ארוזה ל-tar אחד (‏imagesrv-security-pool-<תאריך>.tar) עם
# ‏security-pool.json לידו — הגיבוב, הגודל והתאריך. כל הסגירה ולא רק עדכוני האבטחה:
# כפתור העדכון מתקין ממנו גם חבילה שנוספה ל-packages.txt אחרי שהשרת הותקן (#1803).
# את ה-json מעתיקים ל-
# ‏deploy/security-pool.json (קומיט, תג), ו-publish-to-public.sh מצמיד את ה-tar
# ל-Release של התג. כפתור העדכון מתקין ממנו בלבד (deploy/security-upgrade.sh).
#
# רץ על דביאן 13 עם אינטרנט (שרת המעבדה). **התקנה מה-ISO רצה בלי
# אינטרנט**, ולכן כל חבילה שחסרה כאן נכשלת רק מול שרת אמיתי — הסקריפט
# בודק לפני ההורדה שלכל שם יש מועמד ב-apt, ואחרי ההורדה שלכל שם יש
# ‏.deb ב-pool (ראיה חיובית, עיקרון 5). "apt לא התלונן" אינו ראיה.
#
# איך מקבלים את **כל** התלויות ולא רק את מה שחסר במכונה הזאת: ‏apt-get
# רץ מול קובץ status **ריק** (‏Dir::State::status), כלומר מנקודת מבטו
# שום חבילה אינה מותקנת — והוא מוריד את הסגירה המלאה, כולל libc6. זה
# יותר ממה שהמותקן צריך (הבסיס מגיע מה-netinst עצמו) ואינו מזיק: אותו
# קובץ באותה גרסה מחליף את עצמו.
#
# ‏הבסיס של debootstrap (#2520): המנוע מתקין קודם את מה ש-debootstrap בוחר מה-ISO —
# ‏Priority required + important — ורק אחר כך את packages.txt. כשה-pool הביא גרסה
# חדשה של חבילה מהבסיס (apt 3.0.4, ‏trixie-security 04/11) ולא את אחותה שתלויה בה
# בדיוק (apt-utils: ‏`apt (= 3.0.3)`, רק ב-netinst), שתיהן הותקנו יחד וההתקנה נעצרה
# ב-45%. לכן גם הבסיס נכנס לסגירה — באותו snapshot כמו השאר — בהורדה **נפרדת**
# (שתי סגירות עקביות, בלי שקונפליקט בין שתי הרשימות יפיל את שתיהן), ו-build-iso.sh
# עוצר על כל תלות-גרסה שאינה מתקיימת באינדקס הממוזג (tools/iso/check-pool.py).
# ‏--netinst (אותו ISO שעובר ל-build-iso.sh): הבסיס לבדו אינו סוגר את הפער. ה-netinst
# נושא חבילות שאינן בבסיס ולא ברשימה, ותלויות בדיוק באחות שהעלינו — vim 2:9.1.1230-2
# ‏(`vim-common (= 2:9.1.1230-2)`) מול vim-common ‏+deb13u1 של הבסיס (בניית המעבדה 06/11).
# לכן אחרי ההורדה מריצים את check-pool.py על ה-netinst + מה שהורד, ומורידים את
# הגרסה החדשה של כל חבילה שנמצאה תלויה בגרסה שאינה עוד — עד שהאינדקס עקבי.
#
# ‏Recommends נכללים ב-pool — אבל **אף התקנה אינה מתקינה אותם** (‏#1987:
# ‏`--no-install-recommends` בכל מקום, נבדק ב-tests/test_no_install_recommends_1987.py).
# ה-pool נשאר על-קבוצה בכוונה: שרת רגיל שהותקן לפני #1987 עם Recommends ממשיך
# לקבל דרכו את עדכוני האבטחה שלהם (‏--security). חבילה ב-pool אינה מותקנת.
#
# הפלט (‏--out DIR):
#   DIR/pool/{main,contrib,...}/imagesrv/*.deb   לפי ה-Section של החבילה
#   DIR/pool-date                                היום (UTC) שבו נלקחו הגרסאות —
#                                                ‏build-iso.sh כותב אותו למניפסט
#   DIR/dists/trixie/<רכיב>/binary-amd64/{Packages.gz,Release}
#   DIR/dists/trixie/Release                     לא חתום — כמו ה-netinst הרשמי
#                                                עצמו; ‏d-i מוסיף את הדיסק דרך
#                                                apt-cdrom ו-TrustCDROM
# ‏--index-only DIR כותב רק את dists/ מעל pool/ קיים — כך build-iso.sh
# מאנדקס מחדש את עץ ה-ISO אחרי שה-pool שלנו מוזג לתוך זה של ה-netinst.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PACKAGES_FILE="$SCRIPT_DIR/packages.txt"
OUT=""
INDEX_ONLY=""
SECURITY=""
NETINST=""
SUITE="trixie"
ARCH="amd64"

usage() {
    cat <<'EOF'
ImageSrv — בניית ה-repo המקומי של ה-ISO.

  sudo tools/iso/make-pool.sh --out DIR [--packages FILE] [--netinst ISO]
  sudo tools/iso/make-pool.sh --index-only DIR
  sudo tools/iso/make-pool.sh --security --out DIR

  --out DIR         לאן להוריד (pool/ + dists/ נכתבים מתחתיו)
  --packages FILE   ברירת מחדל: tools/iso/packages.txt
  --netinst ISO     ה-netinst שה-ISO נבנה ממנו: גם החבילות שלו שתלויות בדיוק בגרסה
                    שהעלינו מורדות בגרסתן החדשה (#2520). בלעדיו build-iso.sh עלול להיעצר
  --index-only DIR  רק dists/ מעל DIR/pool קיים (בלי apt)
  --security        כל הסגירה (עם עדכוני trixie-security), כ-tar + security-pool.json (#1411, #1803)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out)        OUT="${2:?}"; shift 2 ;;
        --packages)   PACKAGES_FILE="${2:?}"; shift 2 ;;
        --index-only) INDEX_ONLY="${2:?}"; shift 2 ;;
        --security)   SECURITY=1; shift ;;
        --netinst)    NETINST="${2:?}"; shift 2 ;;
        -h|--help)    usage; exit 0 ;;
        *) printf 'unknown option: %s  (try --help)\n' "$1" >&2; exit 2 ;;
    esac
done

die() { printf 'make-pool: %s\n' "$*" >&2; exit 1; }

tmp_plain=""
rel_tmp=""
cleanup() {
    [[ -z "$tmp_plain" ]] || rm -f "$tmp_plain" "$tmp_plain.err"
    [[ -z "$rel_tmp" ]] || rm -f "$rel_tmp"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# אינדקס: Packages.gz לכל רכיב + Release אחד עם גיבוב לכל קובץ ב-dists
# ---------------------------------------------------------------------------
write_index() {
    local root="$1" comp packages_dir
    [[ -d "$root/pool" ]] || die "אין $root/pool — אין מה לאנדקס"
    command -v dpkg-scanpackages >/dev/null || die "חסר dpkg-scanpackages (חבילת dpkg-dev)"
    for comp_dir in "$root"/pool/*/; do
        comp=$(basename "$comp_dir")
        packages_dir="$root/dists/$SUITE/$comp/binary-$ARCH"
        install -d "$packages_dir"
        tmp_plain=$(mktemp)
        # ‏-m: שתי גרסאות של אותה חבילה (זו של ה-netinst וזו של
        # ‏trixie-updates) נשארות שתיהן — apt בוחר סט עקבי.
        ( cd "$root" && dpkg-scanpackages -m --arch "$ARCH" "pool/$comp" /dev/null ) \
            > "$tmp_plain" 2>"$tmp_plain.err" \
            || { cat "$tmp_plain.err" >&2; die "dpkg-scanpackages נכשל על pool/$comp"; }
        gzip -9 -n -c "$tmp_plain" > "$packages_dir/Packages.gz"
        rm -f "$tmp_plain" "$tmp_plain.err"
        tmp_plain=""
        [[ -f "$packages_dir/Release" ]] || cat > "$packages_dir/Release" <<EOF
Archive: stable
Origin: Debian
Label: Debian
Component: $comp
Architecture: $ARCH
EOF
        printf 'index: %s — %s חבילות\n' "$comp" \
            "$(zcat "$packages_dir/Packages.gz" | grep -c '^Package: ')"
    done

    local dists="$root/dists/$SUITE" comps rel
    comps=$(cd "$dists" && find . -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort | tr '\n' ' ')
    rel="$dists/Release"
    # נכתב מחוץ ל-dists: קובץ זמני בתוכו היה נכנס לרשימת הגיבובים של עצמו.
    rel_tmp=$(mktemp)
    {
        printf 'Origin: Debian\nLabel: Debian\nSuite: stable\nCodename: %s\n' "$SUITE"
        printf 'Date: %s\n' "$(date -u -R)"
        printf 'Architectures: %s\nComponents: %s\n' "$ARCH" "${comps% }"
        printf 'Description: Debian 13 + ImageSrv offline installer repository\n'
        # ‏apt מזהה אינדקס לפי השם **הלא-דחוס** (main/binary-amd64/Packages)
        # ורק אז בוחר דחיסה שרשומה. ‏Release שמונה רק את Packages.gz גורם
        # ל-apt לדלג על הרכיב **בשקט** — נמדד 19/09 מול ה-ISO: "Unable to
        # locate package" על כל הרשימה. לכן לכל .gz נרשם גם התוכן הפרוס.
        for algo in md5sum sha256sum; do
            case "$algo" in md5sum) printf 'MD5Sum:\n' ;; *) printf 'SHA256:\n' ;; esac
            while IFS= read -r f; do
                printf ' %s %16d %s\n' "$($algo < "$dists/$f" | cut -d' ' -f1)" "$(stat -c %s "$dists/$f")" "$f"
                case "$f" in
                    *.gz) printf ' %s %16d %s\n' "$(zcat "$dists/$f" | $algo | cut -d' ' -f1)" \
                              "$(zcat "$dists/$f" | wc -c)" "${f%.gz}" ;;
                esac
            done < <(cd "$dists" && find . -type f ! -name Release ! -name 'Release.gpg' ! -name InRelease -printf '%P\n' | LC_ALL=C sort)
        done
    } > "$rel_tmp"
    mv "$rel_tmp" "$rel"
    rel_tmp=""
    # חתימה ישנה מעל אינדקס חדש היא שגיאה, לא "לא חתום" — מסירים אותה.
    rm -f "$dists/Release.gpg" "$dists/InRelease"
    printf 'index: %s נכתב (%s)\n' "$rel" "${comps% }"
}

if [[ -n "$INDEX_ONLY" ]]; then
    write_index "$INDEX_ONLY"
    exit 0
fi

# ---------------------------------------------------------------------------
# הורדה
# ---------------------------------------------------------------------------
[[ -n "$OUT" ]] || { usage >&2; exit 2; }
[[ -f "$PACKAGES_FILE" ]] || die "אין $PACKAGES_FILE"
[[ "$(id -u)" -eq 0 ]] || die "צריך root (apt-get)"
command -v apt-get >/dev/null || die "אין apt-get — זה רץ על דביאן"
if [[ -n "$SECURITY" && -e "$OUT/pool" ]]; then
    die "$OUT/pool כבר קיים — ‏--security בונה מאגר שלם מאפס, לתיקייה ריקה"
fi

mapfile -t PKGS < <(sed 's/#.*//' "$PACKAGES_FILE" | tr -s ' \t' '\n' | grep -v '^$' | LC_ALL=C sort -u)
(( ${#PKGS[@]} )) || die "packages.txt ריק"
printf 'make-pool: %d חבילות מבוקשות\n' "${#PKGS[@]}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || die "apt-get update נכשל — בלי רשימות עדכניות אין מה להוריד"

# ‏#1411: בלי trixie-security במקורות, ה-pool הוא של יום ההוצאה — ושרת שמותקן
# ממנו לא יקבל את התיקונים לעולם (אין לו מקור חי). ראיה חיובית: ‏apt-cache policy
# מונה רק מקורות שיש להם רשימות, כלומר שה-update למעלה באמת הביא.
policy=$(apt-cache policy 2>&1) || die "apt-cache policy נכשל — לא נבדק אם trixie-security במקורות: $policy"
[[ "$policy" == *"n=trixie-security"* ]] \
    || die "trixie-security אינו במקורות של המארח הבונה (apt-cache policy). הוסף ל-sources.list: deb http://security.debian.org/debian-security trixie-security main contrib — ואז apt-get update (#1411)"

# שלב 1 — לכל שם יש מועמד. שם בלי מועמד (typo, חבילה שירדה, contrib לא
# מופעל) הוא הכישלון ש-R25 מזהיר ממנו, והוא נעצר כאן ולא מול שרת.
missing=()
for p in "${PKGS[@]}"; do
    cand=$(apt-cache policy "$p" 2>/dev/null | awk '/Candidate:/ {print $2}')
    [[ -n "$cand" && "$cand" != "(none)" ]] || missing+=("$p")
done
if (( ${#missing[@]} )); then
    printf 'make-pool: אין מועמד ב-apt ל: %s\n' "${missing[*]}" >&2
    printf 'make-pool: fonts-ibm-plex יושבת ב-contrib — ודא ש-sources.list כולל contrib (#120)\n' >&2
    exit 1
fi

# ‏#2520: הבסיס של debootstrap — Priority required + important במקורות של המארח, אותה
# הגדרה ש-debootstrap מפעיל על אינדקס ה-ISO. ראיה חיובית: apt ו-apt-utils בתוכו;
# רשימה ריקה או בלעדיהם = הפענוח נשבר, לא "אין בסיס" (עיקרון 5).
base_packages() {
    apt-cache dumpavail | awk -F': ' '/^Package: /{p=$2} /^Priority: (required|important)$/{print p}' \
        | LC_ALL=C sort -u
}
base_out=$(base_packages) || die "apt-cache dumpavail נכשל — הבסיס של debootstrap לא נקרא"
mapfile -t BASE <<<"$base_out"
grep -qx apt <<<"$base_out" && grep -qx apt-utils <<<"$base_out" \
    || die "הבסיס של debootstrap (Priority required/important) לא כולל את apt ו-apt-utils — הפענוח נשבר (${#BASE[@]} שמות)"
printf 'make-pool: %d חבילות בבסיס של debootstrap (required + important)\n' "${#BASE[@]}"

DL="$OUT/download"
install -d "$DL/partial" "$OUT/pool"
EMPTY_STATUS="$OUT/.empty-status"
: > "$EMPTY_STATUS"
POOL_DATE=$(date -u +%Y-%m-%d)

# מיון לרכיבים לפי ה-Section שבחבילה עצמה (contrib/fonts → contrib).
sort_into_pool() {
    local deb section comp n=0
    for deb in "$DL"/*.deb; do
        [[ -e "$deb" ]] || die "לא הורד אף .deb אל $DL"
        section=$(dpkg-deb -f "$deb" Section)
        case "$section" in
            */*) comp="${section%%/*}" ;;
            *)   comp="main" ;;
        esac
        install -d "$OUT/pool/$comp/imagesrv"
        mv -f "$deb" "$OUT/pool/$comp/imagesrv/"
        n=$((n + 1))
    done
    rm -rf "$DL" "$EMPTY_STATUS"
    printf 'make-pool: %d קבצי .deb ב-%s/pool\n' "$n" "$OUT"
}

# ---------------------------------------------------------------------------
# ‏--security (‏#1411): עדכוני trixie-security של הסגירה בלבד, כ-tar + json
# ---------------------------------------------------------------------------
if [[ -n "$SECURITY" ]]; then
    # אותה סגירה (status ריק, Recommends), בסימולציה: שורת `Inst` נושאת את מקור
    # הגרסה ("Debian-Security:13/stable-security"). נשמר רק מה שמגיע משם.
    sim=$(apt-get -s -o Dir::State::status="$EMPTY_STATUS" -o Debug::NoLocking=1 \
              -o APT::Install-Recommends=true install "${PKGS[@]}" 2>&1) \
        || { printf '%s\n' "$sim" | tail -n 5 >&2; die "apt-get -s על הסגירה המלאה נכשל"; }
    # ‏#2520: גם הבסיס — שרת מותקן מחזיק את apt-utils, ועדכון של apt בלעדיה נכשל ב---no-remove.
    sim_base=$(apt-get -s -o Dir::State::status="$EMPTY_STATUS" -o Debug::NoLocking=1 \
              -o APT::Install-Recommends=true install "${BASE[@]}" 2>&1) \
        || { printf '%s\n' "$sim_base" | tail -n 5 >&2; die "apt-get -s על הבסיס של debootstrap נכשל"; }
    sim+=$'\n'"$sim_base"
    # ‏#1803: **כל** הסגירה, בגרסת המועמד (זו של trixie-security כשיש) — כך כפתור העדכון
    # מתקין גם חבילה שנוספה ל-packages.txt אחרי שהשרת הותקן (ifenslave/#1264).
    mapfile -t ALL < <(sed -nE 's/^Inst ([^ ]+) (\[[^]]*\] )?\(([^ ]+) .*$/\1=\3/p' <<<"$sim" | LC_ALL=C sort -u)
    mapfile -t SEC < <(sed -nE 's/^Inst ([^ ]+) (\[[^]]*\] )?\(([^ ]+) [^)]*-[Ss]ecurity[, ][^)]*\).*$/\1=\3/p' <<<"$sim" | LC_ALL=C sort -u)
    # אפס הוא כמעט בוודאות פענוח שנשבר, לא "אין עדכונים" (עיקרון 5).
    (( ${#SEC[@]} )) || die "אף חבילה בסגירה אינה מ-trixie-security — הפענוח של apt-get -s נשבר, או שהמקור לא נקרא"
    declare -A IN_CLOSURE=()
    for spec in "${ALL[@]}"; do IN_CLOSURE[${spec%%=*}]=1; done
    missing=()       # ראיה חיובית: כל שם ב-packages.txt בסגירה, אחרת השרת לא יוכל להתקין אותו
    for p in "${PKGS[@]}"; do [[ -n "${IN_CLOSURE[$p]:-}" ]] || missing+=("$p"); done
    (( ${#missing[@]} == 0 )) || die "חבילות מ-packages.txt אינן בסגירה של apt-get -s: ${missing[*]}"
    # ‏#1773: בשרת שמתעדכן chrony מחליפה את systemd-timesyncd (deploy/timesync-swap.sh), ודרך
    # החזרה היא systemd-timesyncd מאותו מאגר — בגרסה של systemd שבסגירה, כי היא תלויה בו
    # בגרסה מדויקת. מחוץ לסימולציה: שתיהן מתנגשות, ו-apt-get -s לא היה בוחר את שתיהן.
    if [[ -n "${IN_CLOSURE[chrony]:-}" ]]; then
        sysd=$(printf '%s\n' "${ALL[@]}" | sed -n 's/^systemd=//p')
        [[ -n "$sysd" ]] || die "chrony בסגירה ו-systemd לא — אין גרסה ל-systemd-timesyncd של דרך החזרה (#1773)"
        ALL+=("systemd-timesyncd=$sysd")
    fi
    printf 'make-pool: %d חבילות בסגירה, %d מהן מ-trixie-security\n' "${#ALL[@]}" "${#SEC[@]}"
    ( cd "$DL" && apt-get download "${ALL[@]}" ) || die "apt-get download נכשל"
    for spec in "${ALL[@]}"; do      # ראיה חיובית: קובץ לכל חבילה=גרסה (נקודתיים = %3a)
        p=${spec%%=*}; v=${spec#*=}
        found=$(find "$DL" -maxdepth 1 -name "${p}_${v//:/%3a}_*.deb" -print -quit)
        [[ -n "$found" ]] || die "ההורדה הסתיימה בלי $p $v"
    done
    sort_into_pool
    write_index "$OUT"
    printf '%s\n' "$POOL_DATE" > "$OUT/pool-date"
    ASSET="imagesrv-security-pool-$POOL_DATE.tar"
    tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="$POOL_DATE 00:00Z" \
        -cf "$OUT/$ASSET" -C "$OUT" pool dists pool-date || die "אריזת $ASSET נכשלה"
    sha=$(sha256sum "$OUT/$ASSET" | cut -d' ' -f1) && size=$(stat -c %s "$OUT/$ASSET") \
        || die "הגיבוב של $ASSET לא חושב"
    printf '{\n "asset": "%s",\n "date": "%s",\n "packages": %d,\n "security": %d,\n "sha256": "%s",\n "size": %d,\n "suite": "trixie+trixie-security"\n}\n' \
        "$ASSET" "$POOL_DATE" "${#ALL[@]}" "${#SEC[@]}" "$sha" "$size" > "$OUT/security-pool.json"
    # ראיה חיובית: ה-json נקרא באותו קורא שהשרת והפרסום מריצים.
    lock_out=$(python3 -I "$SCRIPT_DIR/../../deploy/security-pool-lock.py" "$OUT/security-pool.json" 2>&1) \
        || die "security-pool.json שנכתב אינו עובר את deploy/security-pool-lock.py: $lock_out"
    printf 'make-pool: %s (%s בתים, sha256 %s)\n' "$OUT/$ASSET" "$size" "$sha"
    printf 'make-pool: הבא — להעתיק את %s ל-deploy/security-pool.json (קומיט + תג), ולפרסם עם IMAGESRV_SECURITY_POOL=%s\n' \
        "$OUT/security-pool.json" "$OUT/$ASSET"
    exit 0
fi

printf 'make-pool: מוריד את הסגירה המלאה אל %s\n' "$DL"
apt-get -y --download-only \
    -o Dir::State::status="$EMPTY_STATUS" \
    -o Dir::Cache::archives="$DL" \
    -o Debug::NoLocking=1 \
    -o APT::Install-Recommends=true \
    install "${PKGS[@]}" \
    || die "apt-get --download-only נכשל"
# ‏#2520: הבסיס של debootstrap, סגירה משלו לאותה תיקייה (אותו snapshot = אותן גרסאות).
apt-get -y --download-only \
    -o Dir::State::status="$EMPTY_STATUS" \
    -o Dir::Cache::archives="$DL" \
    -o Debug::NoLocking=1 \
    -o APT::Install-Recommends=true \
    install "${BASE[@]}" \
    || die "apt-get --download-only של הבסיס של debootstrap נכשל"

# ‏#2520: חבילות של ה-netinst שתלויות בדיוק בגרסה שהעלינו (vim מול vim-common). מה שנשאר
# אחרי חמישה סבבים, או חבילה **שלנו** בפער, הוא בעיה שהורדה לא פותרת — נעצרים.
if [[ -n "$NETINST" ]]; then
    [[ -f "$NETINST" ]] || die "אין $NETINST"
    NI="$OUT/.netinst-dists"
    rm -rf "$NI"
    xorriso -osirrox on -indev "$NETINST" -extract /dists "$NI" >/dev/null 2>&1 \
        || die "xorriso לא חילץ את /dists מ-$NETINST"
    mapfile -t NI_INDEX < <(find "$NI" -path '*/binary-amd64/Packages.gz' ! -path '*/debian-installer/*' | LC_ALL=C sort)
    [[ " ${NI_INDEX[*]} " == *"/main/binary-amd64/Packages.gz "* ]] \
        || die "אין main/binary-amd64/Packages.gz ב-$NETINST — זה לא netinst של דביאן amd64?"
    for round in 1 2 3 4 5; do
        ( cd "$OUT" && dpkg-scanpackages -m --arch "$ARCH" download /dev/null ) > "$OUT/.download-Packages" 2>/dev/null \
            || die "dpkg-scanpackages נכשל על $DL"
        rc=0
        chk=$(python3 -I "$SCRIPT_DIR/check-pool.py" "${NI_INDEX[@]}" "$OUT/.download-Packages" 2>&1) || rc=$?
        (( rc != 2 )) || die "check-pool.py לא בדק את ה-netinst מול ההורדה: $chk"
        (( rc != 0 )) || break
        mapfile -t LIFT < <(sed -nE 's/^check-pool: skew: ([^ ]+) .*$/\1/p' <<<"$chk" | LC_ALL=C sort -u)
        (( ${#LIFT[@]} )) || die "check-pool.py החזיר פער בלי שורות skew: $chk"
        for p in "${LIFT[@]}"; do
            if compgen -G "$DL/${p}_*.deb" >/dev/null; then
                printf '%s\n' "$chk" >&2; die "$p מההורדה שלנו עצמה בפער — הורדה נוספת לא תפתור"
            fi
        done
        printf 'make-pool: סבב %d — חבילות ה-netinst שתלויות בגרסה שהעלינו: %s\n' "$round" "${LIFT[*]}"
        apt-get -y --download-only \
            -o Dir::State::status="$EMPTY_STATUS" \
            -o Dir::Cache::archives="$DL" \
            -o Debug::NoLocking=1 \
            -o APT::Install-Recommends=true \
            install "${LIFT[@]}" \
            || die "apt-get --download-only של ${LIFT[*]} נכשל"
    done
    (( rc == 0 )) || { printf '%s\n' "$chk" >&2; die "ה-netinst וההורדה עדיין בפער אחרי חמישה סבבים"; }
    printf 'make-pool: מול ה-netinst — %s\n' "${chk#check-pool: }"
    rm -rf "$NI" "$OUT/.download-Packages"
fi

# שלב 2 — מיון לרכיבים.
sort_into_pool

# שלב 3 — ראיה חיובית: לכל שם מבוקש יש קובץ. שם ללא קובץ = כישלון.
missing=()
for p in "${PKGS[@]}"; do
    found=$(find "$OUT/pool" -name "${p}_*.deb" -print -quit)
    [[ -n "$found" ]] || missing+=("$p")
done
if (( ${#missing[@]} )); then
    printf 'make-pool: הורדה הסתיימה אבל אין .deb ל: %s\n' "${missing[*]}" >&2
    exit 1
fi

write_index "$OUT"
printf '%s\n' "$POOL_DATE" > "$OUT/pool-date"
du -sh "$OUT/pool" | awk '{print "make-pool: גודל ה-pool: " $1}'
