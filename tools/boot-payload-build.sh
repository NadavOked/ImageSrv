#!/usr/bin/env bash
#
# ImageSrv — מטען האתחול של התחנות: ה-initrd (אחד או יותר) ו-vmlinuz
# ב-/srv/imagesrv/boot. **מימוש אחד** לשני המקומות שבונים אותו (#1230):
# ‏tools/iso/firstboot.sh בהתקנה, ו-tools/server-upgrade.sh בכל עדכון.
#
# מה נגזר כאן **בכל ריצה** ולכן לעולם אינו נרשם בקובץ הדגלים:
#   * הקרנל — המותקן החדש ביותר שאינו cloud (build_initramfs.sh מסרב
#     ל-cloud, #904). עדכון שרץ אחרי שדרוג קרנל בונה מול הקרנל החדש
#     ומעתיק את ה-vmlinuz שלו; ‏--kernel-version קבוע היה בונה מול הישן.
#   * ‏SOURCE_DATE_EPOCH (#1125) — זמן הקומיט של העץ שנבנה (יש .git), אחרת
#     ‏source_date_epoch ממניפסט ה-ISO (עץ בלי .git, בניית --source).
#     בלי אף אחד מהם — כישלון בשם, לא `date` שקט.
#
# קובץ הדגלים (/etc/imagesrv/initrd.flags) — שורה לכל הרצת
# ‏build_initramfs.sh: רק מה שקבוע (תפקיד ו---output). ‏# ושורה ריקה
# מדולגות. זה הפורמט ש-firstboot כותב (--write-flags) ושהעדכון קורא.
#
# אטומיות: כל initrd נבנה ל-<output>.new; רק כשכולם נבנו ואינם ריקים —
# והקרנל הועתק ל-vmlinuz.new — כולם מוחלפים. בנייה שנכשלה משאירה את
# המטען הקודם שלם, ולכן חזרה לגרסה הקודמת היא גם חזרה ל-initrd שלה.
#
# הודעות ה-runtime ב-ASCII: firstboot מציג את השורה האחרונה על מסך השרת.
#
#   boot-payload-build.sh --http-root DIR (--flags-file FILE | --iso-default)
#                         [--manifest FILE] [--write-flags FILE] [--build-log FILE]
#
# משתני סביבה (לבדיקות): IMAGESRV_MODULES_DIR (/lib/modules),
# ‏IMAGESRV_KERNEL_DIR (/boot), IMAGESRV_BUILD_TMPDIR (/var/tmp).

set -euo pipefail

say() { printf 'boot-payload-build: %s\n' "$*" >&2; }
die() { say "$*"; exit 1; }

APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
MODULES_DIR="${IMAGESRV_MODULES_DIR:-/lib/modules}"
KERNEL_DIR="${IMAGESRV_KERNEL_DIR:-/boot}"
HTTP_ROOT=""
FLAGS_FILE=""
ISO_DEFAULT=0
MANIFEST=/etc/imagesrv/iso-release.json
WRITE_FLAGS=""
BUILD_LOG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --http-root)   HTTP_ROOT="${2:?}"; shift 2 ;;
        --flags-file)  FLAGS_FILE="${2:?}"; shift 2 ;;
        --iso-default) ISO_DEFAULT=1; shift ;;
        --manifest)    MANIFEST="${2:?}"; shift 2 ;;
        --write-flags) WRITE_FLAGS="${2:?}"; shift 2 ;;
        --build-log)   BUILD_LOG="${2:?}"; shift 2 ;;
        *) die "unknown option: $1" ;;
    esac
done
[[ -n "$HTTP_ROOT" ]] || die "--http-root is required"
[[ -n "$FLAGS_FILE" || "$ISO_DEFAULT" == 1 ]] || die "pass --flags-file FILE or --iso-default"
[[ -z "$FLAGS_FILE" || "$ISO_DEFAULT" == 0 ]] || die "--flags-file and --iso-default are exclusive"

# --- מה בונים: השורות, מהקובץ או ברירת המחדל של ה-ISO -------------------------
lines=()
if [[ "$ISO_DEFAULT" == 1 ]]; then
    # מה שה-ISO מתקין: initrd טקסטואלי, והגרפי למחשבי בנייה ושיכפול (#835).
    # ‏--skip-apt: החבילות כבר מותקנות מה-ISO (tests/test_iso_build.py שומר).
    lines=("--skip-apt --output $HTTP_ROOT/initrd.img"
           "--skip-apt --with-gui --output $HTTP_ROOT/initrd.img.gui")
else
    [[ -r "$FLAGS_FILE" ]] || die "Cannot read $FLAGS_FILE"
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -n "$line" && "$line" != \#* ]] || continue
        lines+=("$line")
    done < "$FLAGS_FILE"
    ((${#lines[@]} > 0)) || die "$FLAGS_FILE has no build lines; nothing would be built"
fi

outputs=()
for line in "${lines[@]}"; do
    read -r -a words <<<"$line"
    out=""
    for i in "${!words[@]}"; do
        case "${words[$i]}" in
            --kernel-version|--source-date-epoch)
                die "$FLAGS_FILE pins ${words[$i]}; it is derived at build time and must not be recorded ($line)" ;;
            --output) out="${words[$((i + 1))]:-}" ;;
        esac
    done
    [[ -n "$out" ]] || die "Build line without --output: $line"
    outputs+=("$out")
done

# --- הקרנל וה-epoch — נגזרים עכשיו ---------------------------------------------
KVER=$(find "$MODULES_DIR" -mindepth 1 -maxdepth 1 -printf '%f\n' \
    | awk '$0 !~ /cloud/' | sort -V | tail -n1) \
    || die "Could not inspect installed kernels in $MODULES_DIR"
[[ -n "$KVER" ]] || die "No non-cloud kernel found in $MODULES_DIR; is linux-image-amd64 installed?"
VMLINUZ="$KERNEL_DIR/vmlinuz-$KVER"
[[ -f "$VMLINUZ" ]] || die "Missing $VMLINUZ"

if [[ -e "$APP_DIR/.git" ]]; then
    EPOCH=$(git -C "$APP_DIR" log -1 --format=%ct) \
        || die "git log failed in $APP_DIR; cannot derive SOURCE_DATE_EPOCH"
else
    EPOCH=$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["source_date_epoch"]))' \
        "$MANIFEST" 2>/dev/null) \
        || die "No .git in $APP_DIR and no source_date_epoch in $MANIFEST"
fi
[[ "$EPOCH" =~ ^[0-9]+$ ]] || die "SOURCE_DATE_EPOCH is not a number: '$EPOCH'"
say "kernel $KVER, SOURCE_DATE_EPOCH=$EPOCH, ${#lines[@]} build(s)"

# --- הבנייה, ל-.new — במקביל (#1465) ---------------------------------------------
# שתי הבניות היו 498 ש' זו אחר זו (שרת המעבדה, 6 vCPU, 16/10); במקביל — הארוכה
# מביניהן. עץ ה-staging (מאות MB לבנייה) הולך ל-/var/tmp שעל הדיסק ולא ל-/tmp,
# שבדביאן 13 הוא tmpfs: שתי בניות במקביל היו שתיים כאלה ב-RAM.
# סדרתי בכל זאת: שורת --with-gui (שתיים כאלה היו מריצות `make -B` לאותו
# native-gui/imagesrv-station-gui) ושורה בלי --skip-apt (נעילת dpkg). כולן בתור אחד.
# הפסק לפי ראיה חיובית בלבד: קובץ rc שאומר 0 ו-.new לא ריק; כל השאר — כשל בשם.
if [[ -n "$BUILD_LOG" ]]; then exec 3>>"$BUILD_LOG"; else exec 3>&1; fi
install -d "$HTTP_ROOT"
BUILD_TMP="${IMAGESRV_BUILD_TMPDIR:-/var/tmp}"
[[ -d "$BUILD_TMP" && -w "$BUILD_TMP" ]] \
    || die "build temp dir $BUILD_TMP is not a writable directory; the previous payload is untouched"
export TMPDIR="$BUILD_TMP"
LOGDIR=$(mktemp -d "$TMPDIR/imagesrv-payload.XXXXXX")
trap 'rm -rf "$LOGDIR"' EXIT
t0=$SECONDS

build_line() {   # build_line <n>: one build_initramfs.sh run; the verdict is $LOGDIR/<n>.rc
    local n=$1 out i rc
    local -a words
    out="${outputs[$n]}"
    read -r -a words <<<"${lines[$n]}"
    for i in "${!words[@]}"; do
        [[ "${words[$i]}" == --output ]] && words[i + 1]="$out.new"
    done
    rm -f "$out.new"
    say "building $out (${lines[$n]})"
    if bash "$APP_DIR/tools/build_initramfs.sh" "${words[@]}" \
        --kernel-version "$KVER" --source-date-epoch "$EPOCH" >"$LOGDIR/$n.log" 2>&1; then
        rc=0
    else
        rc=$?
    fi
    echo "$rc" > "$LOGDIR/$n.rc"
}

serial=()
parallel=()
for n in "${!lines[@]}"; do
    case " ${lines[$n]} " in
        *" --with-gui "*) serial+=("$n") ;;
        *" --skip-apt "*) parallel+=("$n") ;;
        *) serial+=("$n") ;;
    esac
done
pids=()
if ((${#serial[@]})); then
    (for n in "${serial[@]}"; do build_line "$n"; done) &
    pids+=("$!")
fi
for n in "${parallel[@]}"; do
    build_line "$n" &
    pids+=("$!")
done
for pid in "${pids[@]}"; do wait "$pid" || true; done   # the verdict is the .rc files, below

# היומנים לפי סדר השורות, לא משולבים.
for n in "${!lines[@]}"; do
    printf '=== build_initramfs.sh %s ===\n' "${lines[$n]}" >&3
    if [[ -f "$LOGDIR/$n.log" ]]; then cat "$LOGDIR/$n.log" >&3; fi
done

failures=()
for n in "${!lines[@]}"; do
    rc=unknown
    if [[ -f "$LOGDIR/$n.rc" ]]; then rc=$(<"$LOGDIR/$n.rc"); fi
    if [[ "$rc" != 0 ]]; then
        failures+=("Build failed: ${lines[$n]} (exit ${rc:-unknown})")
    elif [[ ! -s "${outputs[$n]}.new" ]]; then
        failures+=("${outputs[$n]} is empty after the build")
    fi
done
if ((${#failures[@]})); then
    for f in "${failures[@]}"; do say "$f"; done
    # השורה האחרונה היא מה ש-firstboot מציג: הכשל הראשון בשמו, ולא רק מספר.
    more=""
    if ((${#failures[@]} > 1)); then more=" (+$((${#failures[@]} - 1)) more)"; fi
    die "${failures[0]}$more; the previous payload is untouched"
fi
say "built ${#lines[@]} initrd(s) in $((SECONDS - t0)) s (${#parallel[@]} parallel, ${#serial[@]} in the serial queue)"
install -m 0644 "$VMLINUZ" "$HTTP_ROOT/vmlinuz.new"

# --- הכול נבנה: מחליפים יחד -----------------------------------------------------
for out in "${outputs[@]}"; do mv -f "$out.new" "$out"; done
mv -f "$HTTP_ROOT/vmlinuz.new" "$HTTP_ROOT/vmlinuz"

if [[ -n "$WRITE_FLAGS" ]]; then
    install -d "$(dirname "$WRITE_FLAGS")"
    {
        printf '# ImageSrv initrd builds (#1230): one build_initramfs.sh run per line.\n'
        printf '# --kernel-version and --source-date-epoch are derived at every build.\n'
        printf '%s\n' "${lines[@]}"
    } > "$WRITE_FLAGS.new"
    mv -f "$WRITE_FLAGS.new" "$WRITE_FLAGS"
    say "recorded ${#lines[@]} build line(s) in $WRITE_FLAGS"
fi
say "Boot payload ready for $KVER: ${outputs[*]} $HTTP_ROOT/vmlinuz"
