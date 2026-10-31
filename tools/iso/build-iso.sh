#!/usr/bin/env bash
#
# ImageSrv — בניית ISO ההתקנה (#1139, #1190): ציוד האתחול החתום של דביאן 13
# netinst + ‏pool/ מקומי + הקוד + **המתקין החי שלנו** (קרנל + initramfs עם
# הגואי והמנוע), כ-ISO hybrid (BIOS + UEFI/Secure Boot).
#
#   sudo tools/iso/build-iso.sh --out /root/ic-iso-tmp --live-initrd live.img \
#        [--netinst debian-13.7.0-amd64-netinst.iso] [--pool /root/ic-iso-tmp/repo] \
#        [--ref v0.48.0]
#
# ‏#1632 (הכרעת נדב 24/10): סיסמת root **אינה** חלק מה-ISO. המפעיל בוחר אותה
# במסך ההתקנה, ליד סיסמת ה-admin, והמנוע כותב את הגיבוב שלה ביעד. ‏--root-password
# ו-IMAGESRV_ROOT_PASSWORD מסורבים בשם — כדי שאיש לא יחשוב שקבע בהם משהו.
#
# ‏#1190 (הכרעת נדב 21/09): ה-ISO **אינו** מריץ את מתקין דביאן. הוא עולה
# ישר ל-initramfs שלנו (`build_initramfs.sh --installer --with-gui`) עם
# ‏`imagesrv.mode=installer`; הגואי שואל דיסק/תפקיד/רשת/שם/admin על מסך
# השרת, המנוע (installer/) מתקין מה-pool שעל ה-ISO, ואחרי הריסטרט
# ‏firstboot משלים מהתשובות בלי אשף. הקרנל ל-/live הוא זה שמודולי
# ה-initramfs נבנו מולו — build-iso מאמת את ההתאמה ועוצר בלעדיה.
#
# רץ על דביאן 13 (שרת המעבדה). מה שהוא **אינו** עושה: לא מאתחל שום
# מכונה. הבדיקה שה-ISO עולה היא test-iso.sh (QEMU/OVMF) או VM אמיתי.
#
# למה ה-ISO נבנה מה-netinst ולא מאפס (R25 §1, R57): ‏shim ו-GRUB החתומים
# של דביאן מגיעים ממנו, ו-`xorriso -boot_image any replay` משכפל את ציוד
# האתחול (El Torito ×2, MBR, GPT) בלי לפרק אותו. ‏grub.cfg אינו חתום
# ואינו נאכף — עריכתו אינה שוברת Secure Boot.
#
# החבילות (R63): ה-netinst הוא כבר repo של apt (dists/ + pool/). ה-pool
# שלנו (make-pool.sh) מתמזג לתוכו ו-dists/ מאונדקס מחדש — וההתקנה
# מקבלת אותן דרך apt-cdrom, המסלול של כל התקנה מ-CD. אין file:/cdrom
# ב-chroot ואין מפתח ארעי.
#
# ‏Reproducible (R61): ‏SOURCE_DATE_EPOCH = זמן הקומיט של --ref; ‏git
# archive --mtime; touch על העץ; gzip -n; xorriso -volume_date. שתי
# בניות מאותו ref + אותו netinst + אותו pool אמורות לתת אותו SHA256 —
# **לא נמדד עדיין** (ראה README).
#
# הפלט: ‏$OUT/imagesrv-<tag>-amd64.iso, ‏SHA256SUMS, ‏*.el_torito.txt (הראיה
# ששני קטעי האתחול נשמרו), ו-imagesrv-iso.json (המניפסט, גם בתוך ה-ISO).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT=""
NETINST=""
POOL=""
REF="HEAD"
# הקוד שנכנס ל-ISO מגיע מהריפו **הציבורי** (NadavOked/ImageSrv) — עותק
# הפרסום הנקי, בלי כלי סוכנים, יומנים ותשובות מחקר. ‏`--source DIR` מאפשר
# עץ מקומי (למעבדה), ובכל מקרה השומר למטה מסרב לארוז נתיבים פרטיים.
PUBLIC_URL="${IMAGESRV_PUBLIC_URL:-https://github.com/NadavOked/ImageSrv.git}"
SOURCE=""
LIVE_INITRD=""
LAB=false
VOLID="IMAGESRV_INSTALL"
CDIMAGE_URL="https://cdimage.debian.org/debian-cd/current/amd64/iso-cd"

usage() {
    cat <<'EOF'
ImageSrv — בניית ISO ההתקנה.

  sudo tools/iso/build-iso.sh --out DIR [אפשרויות]

  --out DIR            תיקיית העבודה והפלט (נדרשים ~4GB פנויים)
  --netinst FILE       ISO של דביאן 13 netinst; בלעדיו מוריד את הנוכחי מ-cdimage.debian.org
  --pool DIR           פלט של make-pool.sh --out; בלעדיו מריץ אותו (דורש אינטרנט)
  --ref REF            התג של הקוד שייכנס ל-ISO (ברירת מחדל HEAD של המקור)
  --source DIR         ריפו מקומי במקום clone של הציבורי (מעבדה); נתיבים
                       פרטיים (tools/agents, .agents, logs, docs/research…)
                       עוצרים את הבנייה גם אז
  --public-url URL     הריפו הציבורי לשיבוט (ברירת מחדל NadavOked/ImageSrv)
  --live-initrd FILE   ה-initramfs של המתקין החי (build_initramfs.sh --installer --with-gui);
                       הקרנל ל-/live נלקח מ-linux-image של ה-pool ומושווה למודולים שבו
  --lab                אפשר initrd עם גישת מעבדה; שם ה-ISO והמניפסט יסומנו ב--lab
EOF
}

# #1632: סיסמת root נקבעת במסך ההתקנה, לא כאן — סירוב בשם, לפני כל עבודה.
refuse_root_password() {
    printf 'build-iso: %s\n' "סיסמת root אינה נקבעת עוד בבניית ה-ISO (#1632): המפעיל בוחר אותה במסך ההתקנה, ליד סיסמת ה-admin. הסר את --root-password / IMAGESRV_ROOT_PASSWORD." >&2
    exit 2
}
[[ -z "${IMAGESRV_ROOT_PASSWORD:-}" ]] || refuse_root_password

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out)           OUT="${2:?}"; shift 2 ;;
        --netinst)       NETINST="${2:?}"; shift 2 ;;
        --pool)          POOL="${2:?}"; shift 2 ;;
        --ref)           REF="${2:?}"; shift 2 ;;
        --source)        SOURCE="${2:?}"; shift 2 ;;
        --public-url)    PUBLIC_URL="${2:?}"; shift 2 ;;
        --root-password|--root-password=*) refuse_root_password ;;
        --live-initrd)   LIVE_INITRD="${2:?}"; shift 2 ;;
        --lab)           LAB=true; shift ;;
        -h|--help)       usage; exit 0 ;;
        *) printf 'unknown option: %s  (try --help)\n' "$1" >&2; exit 2 ;;
    esac
done

die() { printf 'build-iso: %s\n' "$*" >&2; exit 1; }
say() { printf 'build-iso: %s\n' "$*"; }

[[ -n "$OUT" ]] || { usage >&2; exit 2; }
for tool in xorriso dpkg-scanpackages dpkg-deb git python3 sha256sum gzip tar cpio zstd; do
    command -v "$tool" >/dev/null || die "חסר $tool"
done
[[ -f "$SCRIPT_DIR/packages.txt" ]] || die "אין packages.txt ליד הסקריפט"
[[ -n "$LIVE_INITRD" ]] || die "חסר --live-initrd (‏build_initramfs.sh --installer --with-gui) — ה-ISO עולה למתקין החי, לא למתקין דביאן"
[[ -s "$LIVE_INITRD" ]] || die "‏--live-initrd אינו קובץ: $LIVE_INITRD"
LIVE_INITRD=$(readlink -f "$LIVE_INITRD")

install -d "$OUT"
OUT=$(cd "$OUT" && pwd)
WORK="$OUT/work"
ISO_TREE="$WORK/iso"

# --- הקוד: ref → תג, קומיט, epoch -----------------------------------------------
if [[ -n "$SOURCE" ]]; then
    REPO=$(cd "$SOURCE" && pwd) || die "--source לא קיים: $SOURCE"
    say "מקור הקוד: עץ מקומי $REPO (לא הציבורי — רק למעבדה)"
else
    REPO="$OUT/public-src"
    rm -rf "$REPO"
    say "משכפל את הריפו הציבורי $PUBLIC_URL ($REF)"
    git clone -q --branch "$REF" --depth 1 "$PUBLIC_URL" "$REPO" 2>/dev/null \
        || die "clone של $PUBLIC_URL ב-$REF נכשל — התג לא פורסם לציבורי? (publish-to-public.sh)"
    REF=HEAD
fi
COMMIT=$(git -C "$REPO" rev-parse --verify "$REF^{commit}") || die "ref לא מוכר: $REF"
TAG=$(git -C "$REPO" describe --tags --always "$COMMIT")
$LAB && TAG="$TAG-lab"
EPOCH=$(git -C "$REPO" log -1 --format=%ct "$COMMIT")
export SOURCE_DATE_EPOCH="$EPOCH"
say "קוד: $TAG ($COMMIT), SOURCE_DATE_EPOCH=$EPOCH"

# --- ה-netinst ---------------------------------------------------------------
if [[ -z "$NETINST" ]]; then
    say "מוריד את רשימת ה-SHA256 מ-$CDIMAGE_URL"
    sums=$(curl -fsSL "$CDIMAGE_URL/SHA256SUMS") || die "הורדת SHA256SUMS נכשלה"
    line=$(printf '%s\n' "$sums" | grep -E ' debian-13\.[0-9.]+-amd64-netinst\.iso$' | head -n1)
    [[ -n "$line" ]] || die "אין debian-13.x-amd64-netinst.iso ב-SHA256SUMS — דביאן 13 כבר לא current?"
    expected=${line%% *}; name=${line##* }
    NETINST="$OUT/$name"
    if [[ ! -f "$NETINST" ]]; then
        say "מוריד $name"
        curl -fSL -o "$NETINST.part" "$CDIMAGE_URL/$name" || die "הורדת $name נכשלה"
        mv "$NETINST.part" "$NETINST"
    fi
    actual=$(sha256sum "$NETINST" | cut -d' ' -f1)
    [[ "$actual" == "$expected" ]] || die "sha256 של $name אינו תואם: $actual ≠ $expected"
    say "netinst אומת: $name"
fi
[[ -f "$NETINST" ]] || die "אין $NETINST"
NETINST=$(readlink -f "$NETINST")
NETINST_SHA=$(sha256sum "$NETINST" | cut -d' ' -f1)

# --- ה-pool ------------------------------------------------------------------
if [[ -z "$POOL" ]]; then
    POOL="$OUT/repo"
    say "אין --pool — מריץ make-pool.sh --out $POOL"
    bash "$SCRIPT_DIR/make-pool.sh" --out "$POOL" || die "make-pool.sh נכשל"
fi
[[ -d "$POOL/pool" ]] || die "אין $POOL/pool (פלט של make-pool.sh --out)"

# --- חילוץ ה-netinst ----------------------------------------------------------
rm -rf "$WORK"; install -d "$ISO_TREE"
say "מחלץ את ה-netinst"
xorriso -osirrox on -indev "$NETINST" -extract / "$ISO_TREE" >"$WORK/xorriso-extract.log" 2>&1 \
    || { tail -n 20 "$WORK/xorriso-extract.log" >&2; die "xorriso -extract נכשל"; }
chmod -R u+w "$ISO_TREE"
for must in isolinux/isolinux.cfg isolinux/menu.cfg boot/grub/grub.cfg install.amd/vmlinuz install.amd/initrd.gz dists pool .disk/info; do
    [[ -e "$ISO_TREE/$must" ]] || die "ה-netinst אינו מכיל $must — זה לא netinst של דביאן amd64?"
done

# --- מיזוג ה-pool ואינדוקס מחדש ------------------------------------------------
say "ממזג את ה-pool ($(find "$POOL/pool" -name '*.deb' | wc -l) קבצים)"
cp -a "$POOL/pool/." "$ISO_TREE/pool/"

# packages.txt is the installer's offline contract.  Index the merged pool by
# the Package control field once: filenames are not evidence because versions,
# epochs and Debian filename escaping can all change them.
mapfile -t PKGS < <(sed 's/#.*//' "$SCRIPT_DIR/packages.txt" | tr -s ' \t' '\n' | grep -v '^$' | LC_ALL=C sort -u)
(( ${#PKGS[@]} )) || die "packages.txt ריק"
POOL_PACKAGES=$(mktemp "$WORK/pool-packages.XXXXXX")
trap 'rm -f "${POOL_PACKAGES:-}"' EXIT
while IFS= read -r -d '' deb; do
    dpkg-deb -f "$deb" Package >> "$POOL_PACKAGES" \
        || die "לא ניתן לקרוא Package מתוך $deb"
done < <(find "$ISO_TREE/pool" -type f -name '*.deb' -print0)
LC_ALL=C sort -u -o "$POOL_PACKAGES" "$POOL_PACKAGES"
missing=()
for p in "${PKGS[@]}"; do
    grep -Fxq "$p" "$POOL_PACKAGES" || missing+=("$p")
done
rm -f "$POOL_PACKAGES"
POOL_PACKAGES=""
if (( ${#missing[@]} )); then
    die "חבילות מ-packages.txt חסרות מה-pool הממוזג: ${missing[*]}"
fi
say "pool: כל ${#PKGS[@]} החבילות מ-packages.txt קיימות לפי שדה Package"

bash "$SCRIPT_DIR/make-pool.sh" --index-only "$ISO_TREE" || die "אינדוקס dists/ נכשל"
for comp in main contrib; do
    [[ -s "$ISO_TREE/dists/trixie/$comp/binary-amd64/Packages.gz" ]] || die "אין Packages.gz ל-$comp אחרי האינדוקס"
done

# --- הקוד -----------------------------------------------------------------------
say "מכניס את הקוד ($REF) כ-imagesrv-src/"
git -C "$REPO" archive --format=tar --prefix=imagesrv-src/ --mtime="@$EPOCH" "$COMMIT" \
    | tar -x -C "$ISO_TREE" || die "git archive נכשל"
[[ -f "$ISO_TREE/imagesrv-src/server/main.py" && -f "$ISO_TREE/imagesrv-src/deploy/setup-boot-server.sh" ]] \
    || die "imagesrv-src חסר את server/main.py או deploy/setup-boot-server.sh"
# ‏#1277: כפתור העדכון מסרב לכל תג שאינו חתום במפתח שב-deploy/release-signers
# של העץ המותקן. ISO בלי מפתח שם = שרת שלא יתעדכן לעולם — עוצרים כאן ולא שם.
signer_keys=0
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] || signer_keys=$((signer_keys + 1))
done < "$ISO_TREE/imagesrv-src/deploy/release-signers" \
    || die "imagesrv-src/deploy/release-signers לא נקרא — שרת מה-ISO לא יוכל לאמת אף עדכון"
((signer_keys > 0)) || die "אין מפתח ב-deploy/release-signers של $TAG — שרת מה-ISO היה מסרב לכל עדכון (docs/server-install.md, חתימה על גרסאות)"
say "release-signers: $signer_keys מפתחות חתימה — כפתור העדכון יאמת מולם"
# ‏#1185: כפתור העדכון (server/update.py) הוא `git describe` / `git fetch --tags`
# / `git checkout <tag>` על /opt/imagesrv. עץ מ-`git archive` הוא עץ בלי
# ‏.git — השרת הראשון שהותקן מה-ISO (20/09) הציג "לא ידועה (אין תגית git
# על העץ)" ולא יכול היה לעדכן לעולם. לכן ה-.git של ה-clone הרדוד מהציבורי
# נארז לצד העץ: ‏origin = הריפו הציבורי, התג של ה-ISO נמצא בו, ו-fetch של
# תג חדש עובד גם על clone רדוד. **רק מהציבורי**: ה-.git של עץ מקומי
# (--source) הוא ההיסטוריה של הריפו הפרטי, והיא לא עולה על ISO.
if [[ -z "$SOURCE" ]]; then
    origin=$(git -C "$REPO" remote get-url origin 2>/dev/null || true)
    [[ "$origin" == "$PUBLIC_URL" ]] || die "ה-.git שנארז חייב להצביע על הציבורי ($PUBLIC_URL), לא על $origin"
    cp -a "$REPO/.git" "$ISO_TREE/imagesrv-src/.git"
    # ה-mtime של .git (כמו של העץ) מקובע ל-SOURCE_DATE_EPOCH — קבצי ה-pack
    # עצמם אינם reproducible בין שני clone-ים, וזה מחיר ידוע.
    find "$ISO_TREE/imagesrv-src/.git" -exec touch -h -d "@$EPOCH" {} +
    say "imagesrv-src/.git: clone רדוד של $origin ב-$TAG — כפתור העדכון יעבוד"
else
    say "אזהרה: --source — imagesrv-src נארז בלי .git; כפתור העדכון לא יעבוד בשרת הזה (מעבדה בלבד)"
fi
# השומר: מה שאסור לו להיות ב-ISO גם אם המקור הוא עץ פרטי (אותה רשימה
# כמו HARD_DENY ב-publish-to-public.sh, ועוד מה שרק הפרטי מחזיק).
leaked=()
for deny in .claude .agents .otogit tools/agents AGENTS.md logs docs/research; do
    [[ -e "$ISO_TREE/imagesrv-src/$deny" ]] && leaked+=("$deny")
done
if ((${#leaked[@]} > 0)); then
    printf 'build-iso: נתיב פרטי בתוך imagesrv-src: %s\n' "${leaked[@]}" >&2
    die "המקור אינו העץ הציבורי (${#leaked[@]} נתיבים פרטיים) — בנה מהריפו הציבורי, לא מ-ImageSrv-dev"
fi
say "imagesrv-src: אין נתיבים פרטיים ($(find "$ISO_TREE/imagesrv-src" -type f | wc -l) קבצים)"

# The notices are documentation for the distributed ISO itself, not only for
# the source tree embedded in it. Keep a copy at the ISO root where a recipient
# can find it without installing or unpacking anything (#1707).
[[ -f "$ISO_TREE/imagesrv-src/THIRD_PARTY_NOTICES.md" ]] \
    || die "THIRD_PARTY_NOTICES.md חסר ממקור הקוד"
install -m 0644 "$ISO_TREE/imagesrv-src/THIRD_PARTY_NOTICES.md" \
    "$ISO_TREE/THIRD_PARTY_NOTICES.md"

# --- המטען החי: קרנל + initramfs + הקבצים ש-firstboot והמנוע קוראים ----------------
install -d "$ISO_TREE/imagesrv" "$ISO_TREE/live"
install -m 0755 "$SCRIPT_DIR/firstboot.sh" "$ISO_TREE/imagesrv/firstboot.sh"
install -m 0644 "$SCRIPT_DIR/imagesrv-firstboot.service" "$ISO_TREE/imagesrv/imagesrv-firstboot.service"
# אין גיבוב root על ה-ISO (#1632): סיסמת root נבחרת במסך ההתקנה, והמנוע
# (installer/lib/finish.sh) מגבב אותה ביעד.

# הקרנל: מה-linux-image שב-pool (זה שהמותקן יקבל), והמודולים ב-initramfs
# חייבים להיות שלו — מודול של קרנל אחר לא נטען, וזה נראה כמו "אין דיסקים".
# הגבוה לפי גרסה, לא לפי נתיב: ה-netinst מביא קרנל ישן ב-pool/main/l/, ו-pool
# שלנו מביא חדש יותר ב-pool/main/imagesrv/ — מיון נתיבים בחר את הישן (12/10).
kdeb=$(find "$ISO_TREE/pool" -name 'linux-image-6*-amd64_*.deb' -printf '%f\t%p\n' | sort -V | tail -n1 | cut -f2)
[[ -n "$kdeb" ]] || die "אין linux-image-6.*-amd64 ב-pool — packages.txt מבקש linux-image-amd64?"
KTMP="$WORK/kernel"; rm -rf "$KTMP"; install -d "$KTMP"
dpkg-deb -x "$kdeb" "$KTMP" || die "dpkg-deb -x $kdeb נכשל"
kvmlinuz=$(find "$KTMP/boot" -name 'vmlinuz-*' | head -n1)
[[ -s "$kvmlinuz" ]] || die "ב-$kdeb אין boot/vmlinuz-*"
KVER=${kvmlinuz##*/vmlinuz-}
ikver=$( (zstd -dc "$LIVE_INITRD" 2>/dev/null || gzip -dc "$LIVE_INITRD" 2>/dev/null || cat "$LIVE_INITRD") \
    | cpio -t 2>/dev/null | grep -m1 -o '^lib/modules/[^/]*' | cut -d/ -f3 || true)
[[ -n "$ikver" ]] || die "ב-$LIVE_INITRD אין lib/modules/<kver> — זה לא initramfs של build_initramfs.sh?"
lab_flag=(); if $LAB; then lab_flag=(--lab); fi   # מערך ולא $(…) לא מצוטט (SC2046)
lab_overlay=$(bash "$SCRIPT_DIR/initrd-lab-gate.sh" "$LIVE_INITRD" "${lab_flag[@]}") \
    || die "initrd של מעבדה דורש --lab מפורש"
[[ "$ikver" == "$KVER" ]] || die "אי-התאמה: המודולים ב-initramfs הם של $ikver והקרנל ב-pool הוא $KVER — בנה את ה-initramfs מול linux-image-amd64 של ה-pool"
install -m 0644 "$kvmlinuz" "$ISO_TREE/live/vmlinuz"
install -m 0644 "$LIVE_INITRD" "$ISO_TREE/live/initrd.img"
say "live: vmlinuz-$KVER + initrd.img ($(du -h "$LIVE_INITRD" | cut -f1)) — מודולים תואמים"
# מתקין דביאן יורד מהעץ: לא מוצע בתפריט, ולא תופס 100MB.
rm -rf "$ISO_TREE/install.amd" "$ISO_TREE/preseed.cfg"

# --- תפריטי האתחול: ערך ImageSrv אחד ב-BIOS וב-UEFI ---------------------------------
# ‏#1190: שורת הקרנל נושאת רק imagesrv.mode=installer (עיקרון 2 — כמו
# ב-PXE, בלי פרטי משימה); אין תפריט — ה-ISO עולה ישר למתקין החי, ומחיקת
# הדיסק מאושרת בגואי. ‏DI_ARGS הוא השם ההיסטורי של הפרמטרים בתבניות.
DI_ARGS="imagesrv.mode=installer"
[[ -f "$SCRIPT_DIR/imagesrv.cfg.in" ]] || die "חסרה תבנית imagesrv.cfg.in"
[[ -f "$SCRIPT_DIR/isolinux-menu.cfg.in" ]] || die "חסרה תבנית isolinux-menu.cfg.in"
[[ -f "$ISO_TREE/isolinux/menu.cfg" ]] || die "חסר isolinux/menu.cfg ב-netinst"
sed "s|@DI_ARGS@|$DI_ARGS|g" "$SCRIPT_DIR/imagesrv.cfg.in" > "$ISO_TREE/isolinux/imagesrv.cfg"
grep -q '^label imagesrv$' "$ISO_TREE/isolinux/imagesrv.cfg" || die "יצירת isolinux/imagesrv.cfg נכשלה"
if grep -q '@DI_ARGS@' "$ISO_TREE/isolinux/imagesrv.cfg"; then die "הפרמטרים לא הוזרקו ל-isolinux/imagesrv.cfg"; fi
n_isolinux=$(grep -c '^label ' "$ISO_TREE/isolinux/imagesrv.cfg" || true)
[[ "$n_isolinux" -eq 1 ]] || die "isolinux/imagesrv.cfg אינו מכיל בדיוק ערך אחד (נמצאו $n_isolinux)"
install -m 0644 "$SCRIPT_DIR/isolinux-menu.cfg.in" "$ISO_TREE/isolinux/menu.cfg"
[[ "$(cat "$ISO_TREE/isolinux/menu.cfg")" == $'include stdmenu.cfg\ninclude imagesrv.cfg\ntimeout 1' ]] || die "isolinux/menu.cfg מכיל ערכים שאינם של ImageSrv"
n_default=$(grep -cE '^[[:space:]]+menu default$' "$ISO_TREE/isolinux/imagesrv.cfg" || true)
[[ "$n_default" -eq 1 ]] || die "לא בדיוק menu default אחד בתפריט הראשי (נמצאו $n_default)"

[[ -f "$SCRIPT_DIR/grub.cfg.in" ]] || die "חסרה תבנית grub.cfg.in"
[[ -f "$ISO_TREE/boot/grub/grub.cfg" ]] || die "חסר boot/grub/grub.cfg ב-netinst"
python3 - "$SCRIPT_DIR/grub.cfg.in" "$ISO_TREE/boot/grub/grub.cfg" "$TAG" "$DI_ARGS" <<'PY'
import sys
src, dst, tag, di_args = sys.argv[1:5]
text = open(src, encoding="utf-8").read()
text = text.replace("@TAG@", tag).replace("@DI_ARGS@", di_args)
if "@" in text:
    raise SystemExit("placeholder left in grub.cfg")
open(dst, "w", encoding="utf-8", newline="\n").write(text)
PY
n_grub=$(grep -c '^menuentry ' "$ISO_TREE/boot/grub/grub.cfg" || true)
[[ "$n_grub" == 1 ]] || die "GRUB: $n_grub ערכים במקום אחד — רק המתקין החי (#1190)"
grep -q '^set timeout=0$' "$ISO_TREE/boot/grub/grub.cfg" || die "GRUB אינו עולה ישר למתקין (#1190)"
grep -q "ImageSrv $TAG installer" "$ISO_TREE/boot/grub/grub.cfg" || die "כותרת ImageSrv חסרה מ-GRUB"
grep -q '^ *linux */live/vmlinuz imagesrv.mode=installer' "$ISO_TREE/boot/grub/grub.cfg" || die "GRUB אינו מעלה את /live/vmlinuz במצב installer"
if grep -q 'install.amd\|preseed' "$ISO_TREE/boot/grub/grub.cfg" "$ISO_TREE/isolinux/imagesrv.cfg"; then die "שרידי מתקין דביאן בתפריטים"; fi
if grep -qE 'Graphical install|Advanced options|Accessible dark contrast' "$ISO_TREE/boot/grub/grub.cfg"; then
    die "ערכי Debian נשארו ב-boot/grub/grub.cfg"
fi

# --- המניפסט (R61) --------------------------------------------------------------
say "כותב imagesrv-iso.json"
POOL_DATE="$(cat "$POOL/pool-date" 2>/dev/null || true)" KVER="$KVER" LAB_OVERLAY="$lab_overlay" SOURCE_GIT="$([[ -z "$SOURCE" ]] && echo true || echo false)" python3 - "$ISO_TREE" "$OUT/imagesrv-iso.json" "$TAG" "$COMMIT" "$EPOCH" "$(basename "$NETINST")" "$NETINST_SHA" "${PKGS[*]}" <<'PY'
import hashlib, json, os, subprocess, sys
tree, out, tag, commit, epoch, netinst, netinst_sha, pkgs = sys.argv[1:9]
debs = []
for root, _dirs, files in os.walk(os.path.join(tree, "pool")):
    for f in sorted(files):
        if not f.endswith(".deb") or os.path.basename(root) != "imagesrv":
            continue
        p = os.path.join(root, f)
        ctl = subprocess.run(["dpkg-deb", "-f", p, "Package", "Version"], check=True,
                             capture_output=True, text=True).stdout
        fields = dict(l.split(": ", 1) for l in ctl.strip().splitlines())
        debs.append({"package": fields["Package"], "version": fields["Version"],
                     "path": os.path.relpath(p, tree),
                     "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest()})
debs.sort(key=lambda d: d["path"])
manifest = {
    "imagesrv_tag": tag, "git_commit": commit, "source_date_epoch": int(epoch),
    "netinst": netinst, "netinst_sha256": netinst_sha,
    "xorriso": subprocess.run(["xorriso", "-version"], capture_output=True, text=True).stdout.splitlines()[0],
    "requested_packages": pkgs.split(),
    "live_kernel": os.environ.get("KVER", ""),
    # #1411: the day make-pool.sh took the versions (empty = an older pool without the file)
    "pool_date": os.environ.get("POOL_DATE", "").strip(),
    "lab_overlay": os.environ.get("LAB_OVERLAY", "false") == "true",
    # false only for a --source (lab) build: the engine then tolerates the missing .git
    "source_git": os.environ.get("SOURCE_GIT", "true") == "true",
    "live_initrd_sha256": hashlib.sha256(open(os.path.join(tree, "live", "initrd.img"), "rb").read()).hexdigest(),
    "pool_debs": debs,
}
s = json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
for dst in (out, os.path.join(tree, "imagesrv-iso.json")):
    open(dst, "w", encoding="utf-8", newline="\n").write(s)
print(f"build-iso: manifest: {len(debs)} debs")
PY

# --- זמנים אחידים (R61) ---------------------------------------------------------
find "$ISO_TREE" -exec touch -h -d "@$EPOCH" {} +

# --- ה-ISO עצמו --------------------------------------------------------------
ISO="$OUT/imagesrv-$TAG-amd64.iso"
rm -f "$ISO"
say "בונה $ISO (replay של ציוד האתחול מה-netinst)"
xorriso -indev "$NETINST" -outdev "$ISO" \
    -boot_image any replay \
    -volid "$VOLID" \
    -volume_date all_file_dates "=$EPOCH" \
    -update_r "$ISO_TREE" / -- \
    >"$WORK/xorriso-build.log" 2>&1 \
    || { tail -n 30 "$WORK/xorriso-build.log" >&2; die "xorriso נכשל"; }
[[ -s "$ISO" ]] || die "ה-ISO לא נכתב"
xorriso -indev "$ISO" -pvd_info 2>/dev/null | grep -q "Volume Id *: *$VOLID" || die "תווית הכרך אינה $VOLID — init לא ימצא את המדיה"

# --- הראיה: שני קטעי El Torito (BIOS 0x00 + UEFI 0xEF) נשמרו --------------------
REPORT="$ISO.el_torito.txt"
{
    xorriso -indev "$ISO" -report_el_torito plain
    echo
    xorriso -indev "$ISO" -report_el_torito as_mkisofs
} > "$REPORT" 2>/dev/null
n_boot=$(grep -c '^El Torito boot img' "$REPORT" || true)
grep -q 'El Torito boot img.*BIOS' "$REPORT" || die "אין קטע אתחול BIOS ב-$ISO (ראה $REPORT)"
grep -q 'El Torito boot img.*UEFI' "$REPORT" || die "אין קטע אתחול UEFI ב-$ISO (ראה $REPORT)"
say "El Torito: $n_boot קטעי אתחול (BIOS + UEFI) — $REPORT"

( cd "$OUT" && sha256sum "$(basename "$ISO")" > SHA256SUMS )
say "מוכן: $ISO ($(du -h "$ISO" | cut -f1))"
cat "$OUT/SHA256SUMS"
