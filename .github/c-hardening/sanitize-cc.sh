#!/bin/sh
# sanitize-cc.sh — עוטף gcc/cc לבנייה עם sanitizers, ב-job `sanitizers` בלבד.
#
# למה עוטף ולא דגל במוצר: הטסטים מקמפלים את קוד ה-C בעצמם, בכעשרים
# מקומות (`"gcc"` מילולי, או `shutil.which("gcc")`). עוטף שיושב ראשון
# ב-PATH מגיע לכולם בלי לשנות שורה בטסטים, ובלי לגעת ב-
# tools/build_initramfs.sh — הבנייה של המוצר אינה יודעת שהוא קיים.
#
# הוא מוסיף את הדגלים **רק** להידור שאחד מקובצי המקור שלו הוא קובץ
# C של הסוכן שמקבל קלט מבחוץ (SAN_SOURCES). כל הידור אחר — הגואי,
# דרייברים של טסטים — עובר ל-gcc האמיתי כמו שהוא.
#
# ‏`-static` מוסר: ‏ASan אינו מתקשר סטטית. זה משנה את הבינארי של
# הבדיקה בלבד, ורק תחת ה-job הזה.
#
# כל הידור שעבר דרכו נרשם ב-$IMAGESRV_SAN_LOG (שם המקור ושם הטסט), וה-
# job דורש שכל מקור הופיע שם לפחות פעם אחת: "לא נמצאו שגיאות" בלי
# ראיה שהבינארי המכושר בכלל נבנה הוא עיקרון 5 בתחפושת.
#
# ההגדרות של ASan/UBSan (‏log_path וכו') **נאפות לתוך הבינארי** דרך קובץ
# `__asan_default_options` שה-job כותב (IMAGESRV_SAN_DEFAULTS), ומקושר לכל
# בינארי מכושר. משתנה סביבה לא היה מספיק: חלק מהטסטים מריצים את fanout דרך
# `sudo -n env ...`, ו-sudo מוחק את ASAN_OPTIONS — הדוח היה הולך ל-stderr של
# טסט, ו-`verify_asan_link_order` היה מפיל את ה-LD_PRELOAD של
# test_inline_readback_2023 (נמדד 13/11 על ה-testrunner: 14 כשלים).
#
# משתני סביבה (ארבעתם חובה; חסר = יציאה 2, לא הידור רגיל בשקט):
#   IMAGESRV_SAN_REAL_CC   ה-gcc האמיתי
#   IMAGESRV_SAN_LOG       קובץ הרישום
#   IMAGESRV_SAN_ROOT      שורש הריפו; רק מקור שתחתיו נחשב
#   IMAGESRV_SAN_DEFAULTS  קובץ ה-C של __asan/__ubsan_default_options

SAN_SOURCES="fanout.c lldpsniff.c hivewrite.c monitor.c uevent.c chunkcast.c"
# ‏`-fno-sanitize-recover=undefined`: ב-gcc 14 (testrunner, 13/11) דוח של UBSan בבנייה
# משולבת עם ASan הולך ל-stderr **תמיד**, גם עם log_path, והתוכנית ממשיכה ויוצאת 0.
# טסט שבולע stderr היה עובר ירוק על UB. כך UB הורג את התוכנית, כמו ASan.
# ‏`-fno-sanitize=object-size`: בלעדיו ה-UBSan תופס heap overflow **לפני** ASan (מ-O1),
# כלומר ל-stderr ובלי קובץ דוח. נמדד ב-canary של ה-workflow, 13/11. ‏ASan בודק
# את זה ממילא, ומדויק יותר.
SAN_FLAGS="-fsanitize=address,undefined -fno-sanitize=object-size -fno-sanitize-recover=undefined -fno-omit-frame-pointer -g"

real=${IMAGESRV_SAN_REAL_CC:-}
log=${IMAGESRV_SAN_LOG:-}
root=${IMAGESRV_SAN_ROOT:-}
defaults=${IMAGESRV_SAN_DEFAULTS:-}
if [ -z "$real" ] || [ -z "$log" ] || [ -z "$root" ] || [ ! -f "$defaults" ]; then
    echo "sanitize-cc.sh: IMAGESRV_SAN_REAL_CC, IMAGESRV_SAN_LOG, IMAGESRV_SAN_ROOT and IMAGESRV_SAN_DEFAULTS (a file) are required" >&2
    exit 2
fi

hit=
link=yes
for a in "$@"; do
    case "$a" in
        -c|-E|-S) link= ; continue ;;
        *.c) ;;
        # שלב קישור נפרד (`-c` ואז `-o x x.o`, כמו ב-test_fanout_xxh_on_request_1492):
        # אובייקט מכושר בלי `-fsanitize` בקישור הוא undefined reference ל-__asan_*.
        *.o)
            for s in $SAN_SOURCES; do
                [ "${a##*/}" = "${s%.c}.o" ] && hit="$hit ${s%.c}.o"
            done
            continue ;;
        *) continue ;;
    esac
    case "$a" in
        /*) p=$a ;;
        *) p=$(pwd)/$a ;;
    esac
    case "$p" in
        "$root"/agent/*) ;;
        *) continue ;;
    esac
    for s in $SAN_SOURCES; do
        [ "${p##*/}" = "$s" ] && hit="$hit $s"
    done
done

[ -n "$hit" ] || exec "$real" "$@"

n=$#
for a in "$@"; do
    [ "$a" = "-static" ] || set -- "$@" "$a"
done
shift "$n"
# בקישור בלבד: עם `-c -o x.o` קובץ מקור שני הוא שגיאה של gcc.
[ -z "$link" ] || set -- "$@" "$defaults"

echo "sanitized:$hit test=${PYTEST_CURRENT_TEST:-none}" >> "$log"
# shellcheck disable=SC2086 # SAN_FLAGS is a list of flags, split on purpose
exec "$real" $SAN_FLAGS "$@"
